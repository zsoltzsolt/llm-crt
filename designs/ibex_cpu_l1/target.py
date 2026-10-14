# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Ibex CPU L1 Target implementation for CRT (L0 plan + RV32IM ISA breadth)."""

import json
import os
import random
from typing import Any, Dict, List, Tuple

from crt.base_target import DUTTarget, register_target
from crt.policy_manager import (
    build_refinement_prompt,
    extract_policy_regions,
    load_policy_module,
)
from designs.ibex_cpu_l1.instructions import bin_level, decode
from designs.ibex_cpu_l1.shared_types import IbexStateInfo, Stimulus

MAX_BLOCK = 8


def summarize_l1_coverage(coverage_plan, max_hazard_examples=150):
    """List every missed non-hazard bin grouped by instruction, plus hazard examples."""
    missed = [name for name, hits in coverage_plan.items() if hits == 0]
    hit_count = len(coverage_plan) - len(missed)

    missed_by_instr = {}
    missed_hazards = []
    for name in missed:
        if "->" in name:
            missed_hazards.append(name)
        else:
            instr, cov = name.split("_", 1)
            missed_by_instr.setdefault(instr, []).append(cov)

    summary = [
        f"Coverage: {hit_count} / {len(coverage_plan)}",
        f"Uncovered bins: {len(missed)}",
        "",
        "UNCOVERED INSTRUCTION BINS (bin name = <instr>_<coverpoint>):",
    ]
    for instr, covs in missed_by_instr.items():
        summary.append(f"- {instr}: {', '.join(covs)}")
    if not missed_by_instr:
        summary.append("- (none)")

    summary.append("")
    summary.append(f"UNCOVERED RAW-HAZARD BINS: {len(missed_hazards)}")
    if missed_hazards:
        examples = missed_hazards[:max_hazard_examples]
        summary.append(f"- examples: {', '.join(examples)}")
        if len(missed_hazards) > len(examples):
            summary.append(f"- ... {len(missed_hazards) - len(examples)} more omitted")

    return "\n".join(summary)


L1_SANDBOX_RULE = "- SYSTEM instructions (CSR*, ECALL, EBREAK, MRET, WFI) and compressed instructions are blocked and end the block.\n"


def cpu_l1_helper_reference(sandbox_rule=L1_SANDBOX_RULE):
    return (
        "Policy interface:\n"
        "- You receive coverage, a dict-like object keyed by coverage-bin name.\n"
        "- CRITICAL: 'coverage' is a dict of ALL bin names mapped to their hit counts.\n"
        "- A bin is UNCOVERED when coverage.get(bin_name, 0) == 0.\n"
        "- DO NOT check 'if bin_name in coverage:' because ALL bin names exist in the coverage dict!\n"
        "- You receive rng, a random.Random-like object. Use rng.randint, rng.choice, rng.choices, rng.random, and rng.getrandbits as needed.\n"
        "- Return either one 32-bit instruction integer or a list/tuple of up to 8 instruction integers.\n"
        "\n"
        "Execution model:\n"
        "- Each returned block is executed exactly once, from a fixed instruction-memory address, then the core returns to an idle loop.\n"
        "- Register values persist between blocks. Data memory is readable/writable at any address.\n"
        "- A taken branch or jump that leaves the block ends the block (the remaining instructions are skipped).\n"
        + sandbox_rule +
        "\n"
        "Coverage plan (bin name = <instr>_<coverpoint>):\n"
        "- seen: instruction executed.\n"
        "- zero_dst: destination register rd is x0.\n"
        "- zero_src: a source register (rs1 or rs2) is x0.\n"
        "- same_src: rs1 == rs2.\n"
        "- br_forwards / br_backwards: JAL offset > 0 / < 0; for conditional branches, branch TAKEN with offset > 0 / < 0.\n"
        "- not_taken: conditional branch evaluated as not taken (depends on the register values at execution time).\n"
        "- <prev>-><curr>_raw_hazard: <curr> reads a register written by the immediately preceding executed instruction <prev> "
        "(prev in R-type ALU ops or jal; curr in R-type ALU ops or stores). The preceding instruction may be the last one of the previous block.\n"
        "\n"
        "Encodings (RV32IM):\n"
        "- R-type, opcode 0x33: add (funct7=0x00, funct3=0), sub (0x20/0), sll (0x00/1), slt (0x00/2), sltu (0x00/3), xor (0x00/4), "
        "srl (0x00/5), sra (0x20/5), or (0x00/6), and (0x00/7).\n"
        "- M extension, opcode 0x33, funct7=0x01: mul (funct3=0), mulh (1), mulhsu (2), mulhu (3), div (4), divu (5), rem (6), remu (7).\n"
        "- I-type ALU, opcode 0x13: addi (funct3=0), slti (2), sltiu (3), xori (4), ori (6), andi (7). "
        "Shifts: slli (funct3=1, imm[11:5]=0x00), srli (5, 0x00), srai (5, 0x20); shamt in imm[4:0].\n"
        "- U-type: lui (opcode 0x37), auipc (opcode 0x17).\n"
        "- Loads, opcode 0x03 (I-type): lb (funct3=0), lh (1), lw (2), lbu (4), lhu (5).\n"
        "- Stores, opcode 0x23 (S-type): sb (funct3=0), sh (1), sw (2).\n"
        "- Branches, opcode 0x63 (B-type, 13-bit even offset): beq (funct3=0), bne (1), blt (4), bge (5), bltu (6), bgeu (7).\n"
        "- jal: opcode 0x6f (J-type). jalr: opcode 0x67, funct3=0 (I-type).\n"
        "- Recommended helper encoders to define in HELPER REGION:\n"
        "  def enc_r(funct7, rs2, rs1, funct3, rd):\n"
        "      return (((funct7 & 0x7f) << 25) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | 0x33)\n"
        "  def enc_i(imm, rs1, funct3, rd, opcode):\n"
        "      return (((imm & 0xfff) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | opcode)\n"
        "  def enc_s(funct3, rs2, rs1, imm):\n"
        "      return (((imm & 0xfe0) << 20) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((imm & 0x1f) << 7) | 0x23)\n"
        "  def enc_b(funct3, rs1, rs2, imm):\n"
        "      return ((((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3f) << 25) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | (((imm >> 1) & 0xf) << 8) | (((imm >> 11) & 1) << 7) | 0x63)\n"
        "  def enc_u(imm20, rd, opcode):\n"
        "      return (((imm20 & 0xfffff) << 12) | ((rd & 31) << 7) | opcode)\n"
        "  def enc_j(rd, imm):\n"
        "      return (((imm & 0x100000) << 11) | ((imm & 0x7fe) << 20) | ((imm & 0x800) << 9) | (imm & 0xff000) | ((rd & 31) << 7) | 0x6f)\n"
        "- ALWAYS include stochastic fallback logic (e.g. rng.choice over instruction kinds with random registers) at the end of the body region!\n"
    )


def _iter_policy_instructions(result):
    if isinstance(result, (list, tuple)):
        if not result:
            raise ValueError("CPU policy returned an empty instruction block")
        if len(result) > MAX_BLOCK:
            raise ValueError(f"CPU policy returned too many instructions: {len(result)} (max {MAX_BLOCK})")
        instructions = result
    else:
        instructions = [result]

    for instruction in instructions:
        if not isinstance(instruction, int):
            raise ValueError(f"CPU policy returned a non-integer instruction: {instruction!r}")
        if not 0 <= instruction <= 0xFFFFFFFF:
            raise ValueError(f"CPU policy returned instruction outside 32-bit range: {instruction:#x}")

    return instructions


@register_target("ibex_cpu_l1")
class IbexCpuL1Target(DUTTarget):
    """DUTTarget for the Ibex RISC-V CPU, coverage level L1 (313 bins)."""

    name = "ibex_cpu_l1"
    # Trace columns with hits per coverage level, indexed by bin_level().
    level_columns = ("l0_hit", "l1_new_hit")

    # Fraction of smoke-tested policy words allowed to be outside decode_policy_word().
    max_undecodable_fraction = 0.0

    @staticmethod
    def bin_level(bin_name: str) -> int:
        return bin_level(bin_name)

    @staticmethod
    def decode_policy_word(instruction: int):
        """Instruction name for a supported encoding, else None."""
        decoded = decode(instruction)
        return decoded.instr.value if decoded is not None else None

    @property
    def base_policy_path(self) -> str:
        curr_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(curr_dir, "policy.py")

    def build_refinement_prompt(
        self,
        coverage_plan: Dict[str, int],
        previous_helper_region: str,
        previous_body_region: str,
        max_missed_bins: int = 120,
    ) -> str:
        good_response_shape = (
            "# BEGIN LLM HELPER REGION\n"
            "def enc_r(funct7, rs2, rs1, funct3, rd):\n"
            "    return (((funct7 & 0x7f) << 25) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | 0x33)\n"
            "def enc_i(imm, rs1, funct3, rd, opcode):\n"
            "    return (((imm & 0xfff) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | opcode)\n"
            "# END LLM HELPER REGION\n"
            "# BEGIN LLM BODY REGION\n"
            "def miss(name):\n"
            "    return coverage.get(name, 0) == 0\n"
            "if miss('addi_zero_src') and rng.random() < 0.4:\n"
            "    return enc_i(rng.getrandbits(12), 0, 0, rng.randint(1, 31), 0x13)\n"
            "f3 = rng.choice([0, 1, 2, 3, 4, 5, 6, 7])\n"
            "return enc_r(0, rng.randint(0, 31), rng.randint(0, 31), f3, rng.randint(0, 31))\n"
            "# END LLM BODY REGION"
        )
        return build_refinement_prompt(
            design_title="Ibex CPU coverage test (RV32IM)",
            return_rule="The code must return either one valid 32-bit RV32IM instruction integer, or a list/tuple of up to 8 valid RV32IM instructions.",
            good_response_shape=good_response_shape,
            isa_helper_reference=cpu_l1_helper_reference(),
            coverage_summary=summarize_l1_coverage(coverage_plan),
            previous_helper_region=previous_helper_region,
            previous_body_region=previous_body_region,
        )

    def validate_policy(self, policy_path: str, require_generated_helpers: bool = False) -> None:
        with open(policy_path, "r", encoding="UTF8") as f:
            helper_region, body_region = extract_policy_regions(f.read())

        if "coverage" in helper_region:
            raise ValueError(
                "Do not reference 'coverage' in the LLM HELPER REGION! 'coverage' is a local parameter inside generate_instruction(coverage, rng) in the BODY REGION. Move 'def miss(name): return coverage.get(name, 0) == 0' into the BEGIN/END LLM BODY REGION."
            )

        if "in coverage" in body_region and "coverage.get" not in body_region:
            raise ValueError(
                "Policy uses 'in coverage' check which is invalid because all bins exist in the coverage dictionary. "
                "Use coverage.get(bin_name, 0) == 0 to check if a bin is uncovered."
            )

        if require_generated_helpers and "def " not in helper_region:
            raise ValueError(
                "CPU policy refinement must define its own helper function in "
                "the LLM helper region"
            )

        if require_generated_helpers:
            module = load_policy_module(policy_path)
            rng = random.Random(0)
            coverage = {}
            seen_instructions = set()
            total_words = 0
            undecodable = []

            for _ in range(64):
                result = module.generate_instruction(coverage, rng)
                for instruction in _iter_policy_instructions(result):
                    total_words += 1
                    name = self.decode_policy_word(instruction)
                    if name is None:
                        undecodable.append(instruction)
                    else:
                        seen_instructions.add(name)

            if len(undecodable) > self.max_undecodable_fraction * total_words:
                raise ValueError(
                    f"{len(undecodable)}/{total_words} generated words are not supported instructions "
                    f"(e.g. {undecodable[0]:#010x}). Check the instruction encoders."
                )

            if len(seen_instructions) < 3:
                raise ValueError(
                    f"Generated policy is non-stochastic or collapsed to too few instructions: {sorted(seen_instructions)}. "
                    "Ensure your policy maintains randomness using rng when targeting uncovered bins."
                )

    def format_stimulus(self, raw_instruction: Any) -> Any:
        if raw_instruction is None:
            return Stimulus(instructions=[], finish=True)
        instructions = [insn & 0xFFFFFFFF for insn in _iter_policy_instructions(raw_instruction)]
        return Stimulus(instructions=instructions, finish=False)

    def parse_response(self, response: Any) -> Tuple[Any, Any]:
        if isinstance(response, tuple) and len(response) == 2:
            return response[0], response[1]
        return None, response

    def get_trace_fieldnames(self) -> List[str]:
        return [
            "batch",
            "cycle",
            "policy",
            "instructions",
            "retired_pc",
            "retired_insn",
            "coverage_hit",
            "coverage_total",
            *self.level_columns,
            "sim_cycles",
            "timeouts",
            "new_bins",
        ]

    def write_trace_row(
        self,
        trace_writer: Any,
        batch_idx: int,
        cycle: int,
        policy_path: str,
        raw_instruction: Any,
        dut_state: Any,
        cov_before: Dict[str, int],
        cov_after: Dict[str, int],
        cov_hit: int,
        cov_total: int,
    ) -> None:
        new_bins = sorted(
            name
            for name, hits in cov_after.items()
            if hits > 0 and cov_before.get(name, 0) == 0
        )
        level_hits = [0] * len(self.level_columns)
        for name, hits in cov_after.items():
            if hits > 0:
                level_hits[self.bin_level(name)] += 1
        instructions = list(_iter_policy_instructions(raw_instruction)) if raw_instruction else []
        hex_insns = [f"0x{insn & 0xFFFFFFFF:08x}" for insn in instructions]
        state = dut_state if isinstance(dut_state, IbexStateInfo) else None
        retired_pc = f"0x{state.last_pc:08x}" if state and state.last_pc is not None else ""
        retired_insn = f"0x{state.last_insn:08x}" if state and state.last_insn is not None else ""

        trace_writer.writerow(
            {
                "batch": batch_idx + 1,
                "cycle": cycle,
                "policy": policy_path,
                "instructions": json.dumps(hex_insns),
                "retired_pc": retired_pc,
                "retired_insn": retired_insn,
                "coverage_hit": cov_hit,
                "coverage_total": cov_total,
                **dict(zip(self.level_columns, level_hits)),
                "sim_cycles": state.sim_cycles if state else "",
                "timeouts": state.timeouts if state else "",
                "new_bins": json.dumps(new_bins),
            }
        )
