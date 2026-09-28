# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Ibex Decoder Target implementation for CRT."""

import json
import os
from typing import Any, Dict, List, Tuple

from crt.base_target import DUTTarget, register_target
from crt.policy_manager import (
    build_refinement_prompt,
    extract_policy_regions,
    load_policy_module,
    smoke_test_policy_module,
    summarize_coverage,
)


def decoder_helper_reference() -> str:
    return (
        "Policy interface:\n"
        "- You receive coverage, a dict-like object keyed by coverage-bin name.\n"
        "- A bin is UNCOVERED when coverage.get(bin_name, 0) == 0.\n"
        "- You receive rng, a random.Random-like object. Use rng.randint, "
        "rng.choice, rng.choices, rng.random, and rng.getrandbits as needed.\n"
        "- There are no predeclared instruction helpers in the policy file. "
        "Define any helper functions/constants you need in the helper region.\n"
        "- R-type ALU ops use opcode 0x33. Encodings are: "
        "ADD funct7=0x00 funct3=0, SUB 0x20/0, SLL 0x00/1, "
        "SLT 0x00/2, SLTU 0x00/3, XOR 0x00/4, SRL 0x00/5, "
        "SRA 0x20/5, OR 0x00/6, AND 0x00/7.\n"
        "- I-type ALU ops use opcode 0x13. Non-shift funct3 values are: "
        "ADDI 0, SLTI 2, SLTUI 3, XORI 4, ORI 6, ANDI 7. "
        "Shift immediates are SLLI funct7=0x00 funct3=1, "
        "SRLI 0x00/5, SRAI 0x20/5.\n"
        "- Loads use opcode 0x03: LB funct3=0, LH=1, LW=2. "
        "Stores use opcode 0x23: SB funct3=0, SH=1, SW=2.\n"
        "- Top-level op bins may include ADD, SUB, SLL, SLT, SLTU, XOR, SRL, "
        "SRA, OR, AND, immediate ALU bins such as ADDI or SUBI if present in "
        "coverage, load/store bins such as LB/LH/LW/SB/SH/SW, and "
        "illegal_instruction. Target the exact coverage keys shown below.\n"
        "- Cross-bin names follow OP_x_read_A_reg_N, OP_x_read_B_reg_N, "
        "and OP_x_write_reg_N where applicable, for example "
        "SUB_x_read_A_reg_7, XOR_x_read_B_reg_3, SRAI_x_write_reg_2, "
        "LH_x_read_A_reg_4, SH_x_read_B_reg_9.\n"
        "- ALWAYS include stochastic fallback logic (e.g. rng.getrandbits(32)) at the end of the body region!"
    )


@register_target("ibex_decoder")
class IbexDecoderTarget(DUTTarget):
    """DUTTarget for the Ibex Instruction Decoder."""

    name = "ibex_decoder"

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
            "    return (((funct7 & 0x7f) << 25) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | 0x33) & 0xFFFFFFFF\n"
            "def enc_i(funct3, rs1, imm, rd):\n"
            "    return (((imm & 0xfff) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | 0x13) & 0xFFFFFFFF\n"
            "# END LLM HELPER REGION\n"
            "# BEGIN LLM BODY REGION\n"
            "def miss(name):\n"
            "    return coverage.get(name, 0) == 0\n"
            "for r in range(32):\n"
            "    if miss(f'ADD_x_read_A_reg_{r}') and rng.random() < 0.2:\n"
            "        return enc_r(0x00, rng.randint(0, 31), r, 0, rng.randint(0, 31))\n"
            "    if miss(f'ADD_x_read_B_reg_{r}') and rng.random() < 0.2:\n"
            "        return enc_r(0x00, r, rng.randint(0, 31), 0, rng.randint(0, 31))\n"
            "    if miss(f'ADD_x_write_reg_{r}') and rng.random() < 0.2:\n"
            "        return enc_r(0x00, rng.randint(0, 31), rng.randint(0, 31), 0, r)\n"
            "return rng.getrandbits(32)\n"
            "# END LLM BODY REGION"
        )
        return build_refinement_prompt(
            design_title="RISC-V decoder coverage test",
            return_rule="The code must return exactly one 32-bit instruction integer.",
            good_response_shape=good_response_shape,
            isa_helper_reference=decoder_helper_reference(),
            coverage_summary=summarize_coverage(coverage_plan, max_missed_bins),
            previous_helper_region=previous_helper_region,
            previous_body_region=previous_body_region,
        )

    def validate_policy(
        self, policy_path: str, require_generated_helpers: bool = False
    ) -> None:
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

        module = load_policy_module(policy_path)
        smoke_test_policy_module(module, allow_instruction_blocks=False)

    def format_stimulus(self, raw_instruction: Any) -> Any:
        return raw_instruction

    def parse_response(self, response: Any) -> Tuple[Any, Any]:
        return None, response

    def get_trace_fieldnames(self) -> List[str]:
        return [
            "batch",
            "cycle",
            "policy",
            "instruction",
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
        hex_insn = "" if raw_instruction is None else f"0x{raw_instruction & 0xFFFFFFFF:08x}"
        trace_writer.writerow(
            {
                "batch": batch_idx + 1,
                "cycle": cycle,
                "policy": policy_path,
                "instruction": hex_insn,
                "coverage_hit": cov_hit,
                "coverage_total": cov_total,
                "new_bins": json.dumps(new_bins),
            }
        )
