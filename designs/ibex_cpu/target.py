# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Ibex CPU Target implementation for CRT."""

import json
import os
from typing import Any, Dict, List, Tuple

from crt.base_target import DUTTarget, register_target
from crt.policy_manager import (
    build_refinement_prompt,
    extract_policy_regions,
    load_policy_module,
)
from designs.ibex_cpu.instructions import Encoding
from designs.ibex_cpu.shared_types import IbexStateInfo, Stimulus


def summarize_cpu_coverage(coverage_plan, max_missed_bins=120):
    hit_bins = [name for name, hits in coverage_plan.items() if hits > 0]
    missed_bins = [name for name, hits in coverage_plan.items() if hits == 0]
    missed_by_group = {}

    for bin_name in missed_bins:
        if "->" in bin_name:
            group_name = "raw_hazard"
        else:
            group_name = bin_name.rsplit("_", 1)[0]
        missed_by_group.setdefault(group_name, []).append(bin_name)

    summary = [
        f"Coverage: {len(hit_bins)} / {len(coverage_plan)}",
        f"Uncovered bins: {len(missed_bins)}",
        "",
        "UNREACHED BIN GROUPS:",
    ]

    shown_count = 0
    for group_name in sorted(missed_by_group):
        group_bins = missed_by_group[group_name]
        remaining_budget = max_missed_bins - shown_count
        if remaining_budget <= 0:
            break
        examples = group_bins[:remaining_budget]
        shown_count += len(examples)
        summary.append(
            f"- {group_name}: {len(group_bins)} missed: "
            f"{', '.join(examples)}"
        )

    if shown_count < len(missed_bins):
        summary.append(f"- ... {len(missed_bins) - shown_count} more examples omitted")

    return "\n".join(summary)


def cpu_helper_reference():
    return (
        "Policy interface:\n"
        "- You receive coverage, a dict-like object keyed by coverage-bin name.\n"
        "- CRITICAL: 'coverage' is a dict of ALL bin names mapped to their hit counts.\n"
        "- A bin is UNCOVERED when coverage.get(bin_name, 0) == 0.\n"
        "- DO NOT check 'if bin_name in coverage:' because ALL bin names exist in the coverage dict!\n"
        "- Correct way to check for an uncovered bin:\n"
        "  def miss(name):\n"
        "      return coverage.get(name, 0) == 0\n"
        "- Recommended helper encoder functions to define in HELPER REGION:\n"
        "  def enc_r(funct7, rs2, rs1, funct3, rd):\n"
        "      return (((funct7 & 0x7f) << 25) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | 0x33)\n"
        "  def enc_s(funct3, rs2, rs1, imm):\n"
        "      return (((imm & 0xfe0) << 20) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((imm & 0x1f) << 7) | 0x23)\n"
        "  def enc_j(rd, imm):\n"
        "      return (((imm & 0x100000) << 11) | ((imm & 0x7fe) << 20) | ((imm & 0x800) << 9) | (imm & 0xff000) | ((rd & 31) << 7) | 0x6f)\n"
        "- CRITICAL STOCHASTIC GATING RULE:\n"
        "  Do NOT write hardcoded deterministic `if miss(x): return static_insn` rules without randomness!\n"
        "  If static_insn fails to hit the bin, execution gets TRAPPED on that `if` line forever (100,000 cycles).\n"
        "  ALWAYS use probability gating e.g. `if miss('add_same_src') and rng.random() < 0.4:` OR use `rng.choice`/`rng.randint` inside targeted blocks so execution falls through to explore other instructions!\n"
        "- You receive rng, a random.Random-like object. Use rng.randint, rng.choice, rng.choices, rng.random, and rng.getrandbits as needed.\n"
        "- Return either one 32-bit instruction integer or a short list/tuple of 32-bit instruction integers.\n"
        "- R-type ALU ops use opcode 0x33: add (funct7=0x00, funct3=0), sub (0x20/0), sll (0x00/1), slt (0x00/2), sltu (0x00/3), xor (0x00/4), srl (0x00/5), sra (0x20/5), or (0x00/6), and (0x00/7).\n"
        "- S-type stores use opcode 0x23: sb (funct3=0), sh (1), sw (2).\n"
        "- JAL uses opcode 0x6f.\n"
        "- Coverpoints (bin name = <instr>_<coverpoint>): seen = executed; zero_dst = rd is x0; zero_src = rs1 or rs2 is x0; "
        "same_src = rs1 == rs2; br_backwards / br_forwards = jal offset < 0 / > 0.\n"
        "- <prev>-><curr>_raw_hazard: <curr> reads (as rs1 or rs2) the register that the immediately preceding executed "
        "instruction <prev> wrote as rd. <prev> is an R-type ALU op or jal; <curr> is an R-type ALU op or a store. "
        "To hit one, return a list in which <prev> writes some register r and the next instruction <curr> reads r, "
        "e.g. [enc_r(0x20, 2, 3, 0, 5), enc_r(0, 6, 5, 0, 7)] hits sub->add_raw_hazard (sub writes x5, add reads x5).\n"
        "- ALWAYS include stochastic fallback logic (e.g. rng.choice for random op/funct/registers) at the end of the body region!\n"
    )


def enc_j(rd, imm):
    imm = imm & 0x1FFFFF
    i20 = (imm >> 20) & 1
    i10_1 = (imm >> 1) & 0x3FF
    i11 = (imm >> 11) & 1
    i19_12 = (imm >> 12) & 0xFF
    return (i20 << 31) | (i10_1 << 21) | (i11 << 20) | (i19_12 << 12) | ((rd & 31) << 7) | 0x6F


def _block_trap_vector_writes(insn):
    """Replace CSR writes to mtvec (0x305) and Ibex's cpuctrlsts/secureseed with an
    illegal word: a corrupted mtvec would make every later trap unrecoverable."""
    insn &= 0xFFFFFFFF
    funct3 = (insn >> 12) & 0x7
    src = (insn >> 15) & 0x1F
    is_csr = (insn & 0x7F) == 0x73 and funct3 in (1, 2, 3, 5, 6, 7)
    writes = funct3 in (1, 5) or src != 0
    if is_csr and writes and (insn >> 20) in (0x305, 0x7C0, 0x7C1):
        return 0x00000000
    return insn


def _iter_policy_instructions(result):
    if isinstance(result, (list, tuple)):
        if not result:
            raise ValueError("CPU policy returned an empty instruction block")
        if len(result) > 8:
            raise ValueError(f"CPU policy returned too many instructions: {len(result)}")
        instructions = result
    else:
        instructions = [result]

    for instruction in instructions:
        if not isinstance(instruction, int):
            raise ValueError(f"CPU policy returned a non-integer instruction: {instruction!r}")
        if not 0 <= instruction <= 0xFFFFFFFF:
            raise ValueError(f"CPU policy returned instruction outside 32-bit range: {instruction:#x}")

    return instructions


@register_target("ibex_cpu")
class IbexCpuTarget(DUTTarget):
    """DUTTarget for the Ibex RISC-V CPU."""

    name = "ibex_cpu"

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
            "def enc_s(funct3, rs2, rs1, imm):\n"
            "    return (((imm & 0xfe0) << 20) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((imm & 0x1f) << 7) | 0x23)\n"
            "def enc_j(rd, imm):\n"
            "    return (((imm & 0x100000) << 11) | ((imm & 0x7fe) << 20) | ((imm & 0x800) << 9) | (imm & 0xff000) | ((rd & 31) << 7) | 0x6f)\n"
            "# END LLM HELPER REGION\n"
            "# BEGIN LLM BODY REGION\n"
            "def miss(name):\n"
            "    return coverage.get(name, 0) == 0\n"
            "if miss('add_same_src') and rng.random() < 0.4:\n"
            "    return enc_r(0, 1, 1, 0, rng.randint(0, 31))\n"
            "f3 = rng.choice([0, 1, 2, 3, 4, 5, 6, 7])\n"
            "return enc_r(0, rng.randint(0, 31), rng.randint(0, 31), f3, rng.randint(0, 31))\n"
            "# END LLM BODY REGION"
        )
        return build_refinement_prompt(
            design_title="Ibex CPU coverage test",
            return_rule="The code must return either one valid 32-bit instruction integer, or a short list/tuple of valid instructions (R-type ALU, S-type store, JAL).",
            good_response_shape=good_response_shape,
            isa_helper_reference=cpu_helper_reference(),
            coverage_summary=summarize_cpu_coverage(coverage_plan, max_missed_bins),
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
            import random
            rng = random.Random(0)
            coverage = {}
            seen_instructions = set()

            for _ in range(64):
                result = module.generate_instruction(coverage, rng)
                for instruction in _iter_policy_instructions(result):
                    typed_insn = Encoding(instruction).typed()
                    if typed_insn is None:
                        raise ValueError(f"Instruction {instruction:#010x} outside supported R/S/J subset")
                    try:
                        seen_instructions.add(typed_insn.instruction().value)
                    except Exception:
                        pass

            if len(seen_instructions) < 3:
                raise ValueError(
                    f"Generated policy is non-stochastic or collapsed to too few instructions: {sorted(seen_instructions)}. "
                    "Ensure your policy maintains randomness using rng when targeting uncovered bins."
                )

    def format_stimulus(self, raw_instruction: Any) -> Any:
        if raw_instruction is None:
            return Stimulus(insn_mem_updates=[], finish=True)

        instructions = list(_iter_policy_instructions(raw_instruction))
        updates = []
        base_pc = 0x100098  # Active execution slot in test_prog.bin loop
        for idx, insn in enumerate(instructions):
            updates.append((base_pc + (idx * 4), _block_trap_vector_writes(insn)))

        # Always append loop-back JAL to return to base_pc after the instruction block
        loop_pc = base_pc + (len(instructions) * 4)
        delta = base_pc - loop_pc
        loop_jal = enc_j(0, delta)
        updates.append((loop_pc, loop_jal))

        return Stimulus(insn_mem_updates=updates, finish=False)

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
        instructions = list(_iter_policy_instructions(raw_instruction)) if raw_instruction else []
        hex_insns = [f"0x{insn & 0xFFFFFFFF:08x}" for insn in instructions]
        retired_pc = f"0x{dut_state.last_pc:08x}" if (dut_state and getattr(dut_state, 'last_pc', None) is not None) else ""
        retired_insn = f"0x{dut_state.last_insn:08x}" if (dut_state and getattr(dut_state, 'last_insn', None) is not None) else ""

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
                "new_bins": json.dumps(new_bins),
            }
        )
