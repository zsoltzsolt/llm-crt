# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""MIPS CPU Target implementation for CRT."""

import json
import os
from typing import Any, Dict, List, Tuple

from crt.base_target import DUTTarget, register_target
from crt.policy_manager import (
    build_refinement_prompt,
    extract_policy_regions,
    load_policy_module,
)
from designs.mips_cpu.instructions import Encoding
from designs.mips_cpu.shared_types import MipsStateInfo, Stimulus

BOOT_PC = 0xBFC00000
VALIDATION_PC = BOOT_PC + 0x20


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
        examples = group_bins[: min(8, remaining_budget)]
        shown_count += len(examples)
        summary.append(
            f"- {group_name}: {len(group_bins)} missed; examples: "
            f"{', '.join(examples)}"
        )

    if shown_count < len(missed_bins):
        summary.append(f"- ... {len(missed_bins) - shown_count} more examples omitted")

    return "\n".join(summary)


def mips_helper_reference():
    return (
        "Policy interface:\n"
        "- You receive coverage, a dict-like object keyed by coverage-bin name.\n"
        "- CRITICAL: 'coverage' is a dict of ALL bin names mapped to their hit counts.\n"
        "- A bin is UNCOVERED when coverage.get(bin_name, 0) == 0.\n"
        "- DO NOT check 'if bin_name in coverage:' because ALL bin names exist in the coverage dict!\n"
        "- Correct way to check for an uncovered bin:\n"
        "  def miss(name):\n"
        "      return coverage.get(name, 0) == 0\n"
        "- Register field ordering for R-type `rtype(funct, rs, rt, rd)`:\n"
        "  * `rs` (bits 25:21) = source reg 1\n"
        "  * `rt` (bits 20:16) = source reg 2\n"
        "  * `rd` (bits 15:11) = DESTINATION REG (bits 15:11!)\n"
        "  * Coverpoint zero_dst requires `rd == 0`! (e.g. `rtype(0x20, 1, 2, 0)`).\n"
        "  * Coverpoint zero_src requires `rs == 0` or `rt == 0`! (e.g. `rtype(0x20, 0, 1, 2)`).\n"
        "  * Coverpoint same_src requires `rs == rt`! (e.g. `rtype(0x20, 1, 1, 2)`).\n"
        "- CRITICAL STOCHASTIC GATING RULE:\n"
        "  Do NOT write hardcoded deterministic `if miss(x): return static_insn` rules without randomness!\n"
        "  If static_insn fails to hit the bin, execution gets TRAPPED on that `if` line forever (100,000 cycles).\n"
        "  ALWAYS use probability gating e.g. `if miss('add_zero_dst') and rng.random() < 0.4:` OR use `rng.choice`/`rng.randint` inside targeted blocks so execution falls through to explore other instructions!\n"
        "- You receive rng, a random.Random-like object. Use rng.randint, rng.choice, rng.choices, rng.random, and rng.getrandbits as needed.\n"
        "- Return either one 32-bit instruction integer or a short list/tuple of 32-bit instruction integers.\n"
        "- R-type: op=0x00. Funct codes: add 0x20, sub 0x22, sll 0x0f, slt 0x2a, sltu 0x2b, xor 0x26, srl 0x02, sra 0x03, or 0x25, and 0x24.\n"
        "- Stores: op=0x28 (sb), 0x29 (sh), 0x2b (sw).\n"
        "- Jumps: op=0x02 (j) or 0x03 (jal), 26-bit target address in bits 25:0.\n"
        "- ALWAYS include stochastic fallback logic (e.g. rng.choice for random op/funct/registers) at the end of the body region!\n"
    )


def _iter_policy_instructions(result):
    if isinstance(result, (list, tuple)):
        if not result:
            raise ValueError("MIPS policy returned an empty instruction block")
        if len(result) > 8:
            raise ValueError(f"MIPS policy returned too many instructions: {len(result)}")
        instructions = result
    else:
        instructions = [result]

    for instruction in instructions:
        if not isinstance(instruction, int):
            raise ValueError(f"MIPS policy returned a non-integer instruction: {instruction!r}")
        if not 0 <= instruction <= 0xFFFFFFFF:
            raise ValueError(f"MIPS policy returned instruction outside 32-bit range: {instruction:#x}")

    return instructions


@register_target("mips_cpu")
class MipsCpuTarget(DUTTarget):
    """DUTTarget for the MIPS CPU."""

    name = "mips_cpu"

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
            "def rtype(funct, rs, rt, rd):\n"
            "    return (0x00 << 26) | ((rs & 31) << 21) | ((rt & 31) << 16) | ((rd & 31) << 11) | (funct & 63)\n"
            "# END LLM HELPER REGION\n"
            "# BEGIN LLM BODY REGION\n"
            "def miss(name):\n"
            "    return coverage.get(name, 0) == 0\n"
            "if miss('add_same_src') and rng.random() < 0.4:\n"
            "    return rtype(0x20, 1, 1, rng.randint(0, 31))\n"
            "if miss('add_zero_dst') and rng.random() < 0.4:\n"
            "    return rtype(0x20, rng.randint(0, 31), rng.randint(0, 31), 0)\n"
            "f = rng.choice([0x20, 0x22, 0x0f, 0x2a, 0x2b, 0x26, 0x02, 0x03, 0x25, 0x24])\n"
            "return rtype(f, rng.randint(0, 31), rng.randint(0, 31), rng.randint(0, 31))\n"
            "# END LLM BODY REGION"
        )
        return build_refinement_prompt(
            design_title="MIPS32 CPU functional coverage test",
            return_rule="The code must return either one valid 32-bit instruction integer, or a short list/tuple of valid instructions (SPECIAL R-type, store, jump).",
            good_response_shape=good_response_shape,
            isa_helper_reference=mips_helper_reference(),
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
                "MIPS policy refinement must define its own helper function in "
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
                    typed_insn = Encoding(instruction, VALIDATION_PC).typed()
                    if typed_insn is None:
                        raise ValueError(f"MIPS instruction {instruction:#010x} outside supported subset")
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
        return Stimulus(
            insn_mem_updates=[insn & 0xFFFFFFFF for insn in instructions],
            finish=False,
        )

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
