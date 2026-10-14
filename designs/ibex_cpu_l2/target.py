# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Ibex CPU L2 Target implementation for CRT (L1 + sequences & alignment)."""

import os
from typing import Dict

from crt.base_target import register_target
from crt.policy_manager import build_refinement_prompt
from designs.ibex_cpu_l1.target import IbexCpuL1Target, cpu_l1_helper_reference
from designs.ibex_cpu_l2.coverage import bin_level

# Imported so ZMQ replies (pickled L2 coverage databases) can be unpickled.
import designs.ibex_cpu_l2.shared_types  # noqa: F401


def summarize_l2_coverage(coverage_plan, max_cross_examples=150):
    """Missed per-instruction bins grouped by instruction; cross bins by kind."""
    missed = [name for name, hits in coverage_plan.items() if hits == 0]
    hit_count = len(coverage_plan) - len(missed)

    missed_by_instr = {}
    missed_cross = {}
    for name in missed:
        pair_or_instr, cov = name.split("_", 1)
        if "->" in pair_or_instr:
            missed_cross.setdefault(cov, []).append(name)
        else:
            missed_by_instr.setdefault(pair_or_instr, []).append(cov)

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

    for kind, label in (("raw_hazard", "L0 RAW-HAZARD CROSS"), ("back_to_back", "BACK-TO-BACK")):
        names = missed_cross.get(kind, [])
        summary.append("")
        summary.append(f"UNCOVERED {label} BINS (<prev>-><curr>_{kind}): {len(names)}")
        if names:
            examples = names if kind == "back_to_back" else names[:max_cross_examples]
            summary.append(f"- {'all' if len(examples) == len(names) else 'examples'}: {', '.join(examples)}")
            if len(names) > len(examples):
                summary.append(f"- ... {len(names) - len(examples)} more omitted")

    return "\n".join(summary)


def cpu_l2_coverage_reference():
    return (
        "Additional L2 coverage plan (sequences inside one block and memory alignment):\n"
        "- IMPORTANT: <instr>_raw_hazard / _waw_hazard / _war_hazard and <prev>-><curr>_back_to_back bins only count when "
        "the two instructions execute consecutively INSIDE THE SAME returned block. Return lists of instructions to hit them; "
        "a single instruction per call can never hit them.\n"
        "- <instr>_raw_hazard: <instr> reads a register (not x0) written by the immediately preceding instruction in the block.\n"
        "- <instr>_waw_hazard: <instr> writes rd (not x0) that the preceding instruction in the block also wrote.\n"
        "- <instr>_war_hazard: <instr> writes rd (not x0) that the preceding instruction in the block read as a source.\n"
        "- <instr>_offset_<k> (lw, sw, lh, lhu, sh): effective address (rs1 value + imm) has address bits [1:0] == k. "
        "Register values persist, so e.g. set a base register with lui/addi earlier in the block, then use the immediate to pick the offset.\n"
        "- <prev>-><curr>_back_to_back over categories: load (lb/lh/lw/lbu/lhu), store (sb/sh/sw), mul (mul/mulh/mulhsu/mulhu), "
        "div (div/divu/rem/remu), branch (a conditional branch that is TAKEN), jump (jal/jalr). <curr> must execute immediately after <prev>. "
        "If <prev> is a taken branch or jump, its target must be the next instruction inside the block (e.g. offset +4) "
        "for the block to continue; a target outside the block ends the block.\n"
        "- The L0 <prev>-><curr>_raw_hazard crosses keep their L1 meaning (they may also span two consecutive blocks).\n"
    )


@register_target("ibex_cpu_l2")
class IbexCpuL2Target(IbexCpuL1Target):
    """DUTTarget for the Ibex RISC-V CPU, coverage level L2 (470 bins)."""

    name = "ibex_cpu_l2"
    level_columns = ("l0_hit", "l1_new_hit", "l2_new_hit")

    @staticmethod
    def bin_level(bin_name: str) -> int:
        return bin_level(bin_name)

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
            "if miss('addi_raw_hazard') and rng.random() < 0.4:\n"
            "    r = rng.randint(1, 31)\n"
            "    return [enc_r(0, rng.randint(0, 31), rng.randint(0, 31), 0, r), enc_i(rng.getrandbits(12), r, 0, rng.randint(1, 31), 0x13)]\n"
            "f3 = rng.choice([0, 1, 2, 3, 4, 5, 6, 7])\n"
            "return [enc_r(0, rng.randint(0, 31), rng.randint(0, 31), f3, rng.randint(0, 31)) for _ in range(rng.randint(1, 8))]\n"
            "# END LLM BODY REGION"
        )
        return build_refinement_prompt(
            design_title="Ibex CPU coverage test (RV32IM, instruction sequences)",
            return_rule="The code must return either one valid 32-bit RV32IM instruction integer, or a list/tuple of up to 8 valid RV32IM instructions.",
            good_response_shape=good_response_shape,
            isa_helper_reference=cpu_l1_helper_reference() + "\n" + cpu_l2_coverage_reference(),
            coverage_summary=summarize_l2_coverage(coverage_plan),
            previous_helper_region=previous_helper_region,
            previous_body_region=previous_body_region,
        )
