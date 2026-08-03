#!/bin/env python3
# Copyright Zixi Zhang
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""LLM-guided CRT policy experiment for the MIPS CPU.

This is the MIPS counterpart of the CRT driver in ibex_cpu/generate_stimulus.py.
It is deliberately a separate module from mips_cpu/generate_stimulus.py so that
CRT runs do not import models.llm_azure (which requires Azure credentials at
module load time).

Two things differ from the Ibex driver:

1. mips_cpu.instructions.Encoding takes (encoding, pc), not (encoding).
2. The MIPS testbench owns instruction addressing. cocotb_mips.py derives the
   write address from the live PC and consumes Stimulus.insn_mem_updates as a
   flat list of instruction words, so the client never computes a PC. The
   Ibex helpers instruction_memory_updates_for_pc / next_cpu_stimulus_pc have
   no MIPS equivalent and are intentionally absent.
"""

import argparse
import copy
import csv
import json
import os
import shutil
import sys
import time
from contextlib import closing
from datetime import datetime

import zmq

directory = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.dirname(directory)
sys.path.insert(0, repo_root)

from agents.agent_llm_crt import (  # noqa: E402
    EditableCRTAgent,
    append_policy_log,
    append_policy_rejection_log,
    extract_policy_regions,
    load_policy_module,
    write_updated_policy,
)
from global_shared_types import GlobalCoverageDatabase, GlobalDUTState  # noqa: E402
from models.llm_gpt import ChatGPT  # noqa: E402
from mips_cpu.instructions import Encoding  # noqa: E402
from mips_cpu.shared_types import MipsStateInfo, Stimulus  # noqa: E402

# MIPS reset vector. Used as the reference PC when decoding candidate
# instructions during policy validation, and before the DUT reports state.
BOOT_PC = 0xBFC00000
# The testbench writes stimulus a little ahead of the fetch PC
# (cocotb_mips.py:137 uses pc + 0x18), so validation decodes against that.
VALIDATION_PC = BOOT_PC + 0x20


class StimulusSender:
    def __init__(self, zmq_addr):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect(zmq_addr)

    def send_stimulus(self, stimulus_obj):
        self.socket.send_pyobj(stimulus_obj)
        state_coverage_obj = self.socket.recv_pyobj()

        if not isinstance(state_coverage_obj, tuple):
            raise RuntimeError("Bad format of coverage response")
        if not isinstance(state_coverage_obj[0], MipsStateInfo):
            raise RuntimeError("Bad format of coverage response element 0")

        return state_coverage_obj

    def close(self):
        if self.socket:
            self.socket.close()


# ---------------------------------------------------------------------------
# MIPS encoding reference
#
# These tables mirror mips_cpu/instructions.py exactly. Note that SLL is funct
# 0x0f there, not the architectural 0x00 -- keep the two in sync if that
# upstream choice is ever revised.
# ---------------------------------------------------------------------------

R_FUNCT = {
    "add": 0x20,
    "sub": 0x22,
    "sll": 0x0F,
    "slt": 0x2A,
    "sltu": 0x2B,
    "xor": 0x26,
    "srl": 0x02,
    "sra": 0x03,
    "or": 0x25,
    "and": 0x24,
}
S_OPCODE = {"sb": 0x28, "sh": 0x29, "sw": 0x2B}
J_OPCODES = {0x02, 0x03}


def print_coverage_summary(label, g_coverage):
    cov_hit, cov_total = g_coverage.get_coverage_rate()
    cov_percent = (cov_hit / cov_total * 100) if cov_total > 0 else 0
    print(f"{label}: ({cov_hit}/{cov_total}) - {cov_percent:.2f}%")


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
        "- A bin is uncovered when coverage.get(bin_name, 0) == 0.\n"
        "- You receive rng, a random.Random-like object. Use rng.randint, "
        "rng.choice, rng.choices, rng.random, and rng.getrandbits as needed.\n"
        "- There are no predeclared instruction helper functions in the policy "
        "file. Define any helper functions/constants you need in the helper "
        "region.\n"
        "- Return either one 32-bit instruction integer or a short list/tuple "
        "of 32-bit instruction integers. The testbench writes a returned block "
        "into consecutive instruction memory words in one transaction.\n"
        "- This is MIPS32, big-endian field layout in a 32-bit word. The "
        "coverage model accepts only SPECIAL R-type ALU ops, stores, and "
        "jumps. Do not generate loads, branches, I-type ALU ops, COP0/COP1, "
        "or arbitrary illegal words.\n"
        "- R-type: op field (bits 31:26) must be 0b000000. rs = bits 25:21, "
        "rt = bits 20:16, rd = bits 15:11, shamt = bits 10:6, funct = bits "
        "5:0. Funct codes: add 0x20, sub 0x22, sll 0x0f, slt 0x2a, sltu 0x2b, "
        "xor 0x26, srl 0x02, sra 0x03, or 0x25, and 0x24.\n"
        "- Stores: op = 0x28 for sb, 0x29 for sh, 0x2b for sw. base = bits "
        "25:21, rt = bits 20:16, signed offset = bits 15:0. Prefer small "
        "aligned offsets: any offset for sb, even for sh, multiples of four "
        "for sw.\n"
        "- Jumps: op = 0x02 (j) or 0x03 (jal), 26-bit absolute target in bits "
        "25:0. The target is an absolute word address, NOT a relative offset. "
        "The field is only 26 bits, so any target >= 0x4000000 wraps to zero. "
        "Keep targets within 0x0000000..0x3ffffff.\n"
        "- Jump direction is judged by comparing (pc & 0x0fffffff) against "
        "(target << 2). Lower target means jal_br_backwards, higher means "
        "jal_br_forwards.\n"
        f"- From the reset vector {BOOT_PC:#010x}, pc & 0x0fffffff is "
        f"{VALIDATION_PC & 0x0FFFFFFF:#010x}, so a first jump only reads as "
        "forwards when target > 0x3f00008. That is a narrow window near the "
        "top of the range; use something like 0x3f80000 for forwards and any "
        "small value such as 0x0100000 for backwards.\n"
        "- After a jump is taken the PC becomes (pc & 0xf0000000) | "
        "(target << 2), so pc & 0x0fffffff equals the previous target << 2. "
        "Successive jumps therefore compare directly against each other: "
        "alternating a low target and a high target covers both directions "
        "reliably.\n"
        "- Per-instruction bins are named like add_seen, add_zero_dst, "
        "add_zero_src, add_same_src, sw_seen, jal_br_backwards, and "
        "jal_br_forwards.\n"
        "- zero_dst means rd == 0. zero_src means rs == 0 or rt == 0. "
        "same_src means rs == rt.\n"
        "- RAW hazard cross bins are named like add->sub_raw_hazard. A RAW "
        "hazard is covered when the previous R-type or jump writes a register "
        "and the next R-type or store reads that same register as rs or rt.\n"
        "- IMPORTANT: for a jump, the coverage model reads its 'destination "
        "register' from bits 15:11 of the instruction word, which are part of "
        "the 26-bit target field. To hit a jal->X_raw_hazard bin, choose a "
        "jump target whose bits 15:11 equal the rs or rt of the next "
        "instruction.\n"
        "- If you target RAW hazards, return the producer and consumer in the "
        "same list/tuple so they are written into instruction memory "
        "together.\n"
    )


MIPS_POLICY_REFINEMENT_PREAMBLE = """\
You are improving a constrained-random instruction generator for a MIPS32 CPU \
functional coverage test.

The simulator and testbench interface are fixed. You may modify only the two \
editable regions:
- BEGIN/END LLM HELPER REGION (module scope)
- BEGIN/END LLM BODY REGION (inside generate_instruction(coverage, rng))

Requirements:
- Output only valid Python code.
- Return only the two editable regions, including their BEGIN/END markers.
- Do not include markdown, comments outside the editable regions, or any \
explanations.
- Do not redefine generate_instruction(...); only provide the helper region \
and the body region.

Implementation requirements:
- The helper region must contain reusable helper code, including at least one \
helper function for instruction encoding or targeted instruction generation.
- Do not place all encoding logic directly inside the body region.
- Do not rely on helper functions defined elsewhere; assume the scaffold \
provides none.
- Place all per-call decision logic and the final return statement inside the \
body region.

Instruction generation:
- Return either:
  - a single valid MIPS32 test instruction encoded as a 32-bit integer, or
  - a short list or tuple of valid 32-bit instruction encodings.
- Generate only SPECIAL R-type ALU instructions, store instructions, or jump \
instructions."""


def build_mips_policy_refinement_prompt(
    coverage_plan,
    previous_helper_region,
    previous_body_region,
    max_missed_bins=120,
):
    return (
        f"{MIPS_POLICY_REFINEMENT_PREAMBLE}\n\n"
        f"{mips_helper_reference()}\n"
        f"{summarize_cpu_coverage(coverage_plan, max_missed_bins)}\n\n"
        "CURRENT HELPER REGION:\n"
        "```python\n"
        f"{previous_helper_region}\n"
        "```\n\n"
        "CURRENT BODY REGION:\n"
        "```python\n"
        f"{previous_body_region}\n"
        "```\n"
    )


def _iter_policy_instructions(result):
    if isinstance(result, (list, tuple)):
        if not result:
            raise ValueError("MIPS policy returned an empty instruction block")
        if len(result) > 8:
            raise ValueError(
                f"MIPS policy returned an excessively large instruction block: {len(result)}"
            )
        instructions = result
    else:
        instructions = [result]

    for instruction in instructions:
        if not isinstance(instruction, int):
            raise ValueError(
                "MIPS policy returned a non-integer instruction value: "
                f"{instruction!r}"
            )
        if not 0 <= instruction <= 0xFFFFFFFF:
            raise ValueError(
                "MIPS policy returned an instruction outside 32-bit range: "
                f"{instruction:#x}"
            )

    return instructions


class _DefaultCoverage(dict):
    def __init__(self, default):
        super().__init__()
        self.default = default

    def get(self, key, default=None):
        return self.default


class _OnlyMissingCoverage(dict):
    def __init__(self, missing_key):
        super().__init__()
        self.missing_key = missing_key

    def get(self, key, default=None):
        return 0 if key == self.missing_key else 1


def _cpu_policy_validation_coverages():
    # The MIPS coverage model reuses the Ibex bin names verbatim, so this
    # forced-target list is identical to the one in ibex_cpu.
    forced_targets = [
        "add_seen",
        "sub_seen",
        "sll_seen",
        "slt_seen",
        "sltu_seen",
        "xor_seen",
        "srl_seen",
        "sra_seen",
        "or_seen",
        "and_seen",
        "sb_seen",
        "sh_seen",
        "sw_seen",
        "jal_seen",
        "jal_br_backwards",
        "jal_br_forwards",
        "add_same_src",
        "sub_same_src",
        "sb_same_src",
        "sh_same_src",
        "sw_same_src",
        "sra->sub_raw_hazard",
    ]
    return [
        _DefaultCoverage(0),
        _DefaultCoverage(1),
        *[_OnlyMissingCoverage(target) for target in forced_targets],
    ]


def describe_mips_instruction_rejection(instruction):
    op = (instruction >> 26) & 0x3F

    if op == 0x00:
        funct = instruction & 0x3F
        known = ", ".join(f"{name} {code:#04x}" for name, code in R_FUNCT.items())
        return (
            f"{instruction:#010x} is a SPECIAL (op 0x00) instruction with funct "
            f"{funct:#04x}, which is not one of the supported ALU ops ({known})"
        )

    if op in S_OPCODE.values():
        return (
            f"{instruction:#010x} uses store opcode {op:#04x} but was not "
            "accepted by the decoder"
        )

    if op in J_OPCODES:
        return (
            f"{instruction:#010x} uses jump opcode {op:#04x} but was not "
            "accepted by the decoder"
        )

    unsupported = {
        0x08: "ADDI",
        0x09: "ADDIU",
        0x0A: "SLTI",
        0x0B: "SLTIU",
        0x0C: "ANDI",
        0x0D: "ORI",
        0x0E: "XORI",
        0x0F: "LUI",
        0x04: "BEQ",
        0x05: "BNE",
        0x06: "BLEZ",
        0x07: "BGTZ",
        0x10: "COP0",
        0x11: "COP1",
        0x20: "LB",
        0x21: "LH",
        0x23: "LW",
        0x24: "LBU",
        0x25: "LHU",
    }
    family = unsupported.get(op, "unknown or reserved")
    return (
        f"{instruction:#010x} has opcode {op:#04x} ({family}), but this "
        "coverage subset accepts only SPECIAL R-type opcode 0x00, store "
        "opcodes 0x28/0x29/0x2b, and jump opcodes 0x02/0x03"
    )


def validate_mips_policy_outputs(
    policy_path, samples=256, require_generated_helpers=False
):
    if require_generated_helpers:
        with open(policy_path, "r", encoding="UTF8") as f:
            helper_region, _ = extract_policy_regions(f.read())
        if "def " not in helper_region:
            raise ValueError(
                "MIPS policy refinement must define its own helper function in "
                "the LLM helper region"
            )

    module = load_policy_module(policy_path)

    import random

    rng = random.Random(0)
    coverages = _cpu_policy_validation_coverages()
    for coverage in coverages:
        for _ in range(samples):
            result = module.generate_instruction(coverage, rng)
            for instruction in _iter_policy_instructions(result):
                typed_instruction = Encoding(instruction, VALIDATION_PC).typed()
                if typed_instruction is None:
                    raise ValueError(
                        "MIPS policy returned an instruction outside the "
                        "supported R/store/jump coverage subset: "
                        f"{describe_mips_instruction_rejection(instruction)}"
                    )
                try:
                    typed_instruction.instruction()
                except AssertionError as e:
                    raise ValueError(
                        "MIPS policy returned an invalid instruction encoding "
                        "for the supported R/store/jump subset: "
                        f"{describe_mips_instruction_rejection(instruction)}"
                    ) from e


def validate_mips_policy_is_productive(policy_path, samples=512):
    module = load_policy_module(policy_path)

    import random

    seen_instruction_names = set()
    rng = random.Random(1)
    coverage = _DefaultCoverage(1)

    for _ in range(samples):
        result = module.generate_instruction(coverage, rng)
        for instruction in _iter_policy_instructions(result):
            typed_instruction = Encoding(instruction, VALIDATION_PC).typed()
            if typed_instruction is None:
                raise ValueError(
                    "MIPS policy returned an instruction outside the supported "
                    "R/store/jump coverage subset: "
                    f"{describe_mips_instruction_rejection(instruction)}"
                )
            seen_instruction_names.add(typed_instruction.instruction().value)

    if len(seen_instruction_names) < 4:
        raise ValueError(
            "MIPS policy is too narrow under covered feedback; it generated only "
            f"{sorted(seen_instruction_names)}"
        )


def merge_cpu_coverage(g_coverage, coverage):
    if coverage is None:
        return

    cumulative = g_coverage.get()
    if cumulative is None:
        g_coverage.set(copy.deepcopy(coverage))
        return

    if not isinstance(coverage, type(cumulative)):
        raise TypeError(
            "New coverage is of different type of self._coverage_database."
        )

    for instr, covs in coverage.instructions.items():
        for cov, count in covs.items():
            cumulative.instructions[instr][cov] = max(
                cumulative.instructions[instr][cov],
                count,
            )

    for instr, x_covs in coverage.cross_coverage.items():
        for x_cov, count in x_covs.items():
            cumulative.cross_coverage[instr][x_cov] = max(
                cumulative.cross_coverage[instr][x_cov],
                count,
            )


def _hex_or_empty(value):
    return "" if value is None else f"0x{value:08x}"


def _hex_list(values):
    return [f"0x{value & 0xFFFFFFFF:08x}" for value in values]


def _coverage_rate_from_plan(coverage_plan):
    """Derive the coverage rate from an already-built coverage plan.

    get_coverage_rate() rebuilds the whole plan internally. Taking the rate
    from a plan the caller already has keeps the inner loop at one plan
    rebuild per cycle instead of three.
    """
    if not coverage_plan:
        return "", ""
    return sum(1 for hits in coverage_plan.values() if hits > 0), len(coverage_plan)


def _coverage_plan_or_empty(g_coverage):
    if g_coverage.get() is None:
        return {}
    return g_coverage.get_coverage_plan()


def write_mips_crt_trace_row(
    trace_writer,
    batch_idx,
    cycle,
    policy_path,
    instructions,
    mips_state,
    coverage_before,
    coverage_after,
    cov_hit,
    cov_total,
):
    new_bins = sorted(
        name
        for name, hits in coverage_after.items()
        if hits > 0 and coverage_before.get(name, 0) == 0
    )
    trace_writer.writerow(
        {
            "batch": batch_idx + 1,
            "cycle": cycle,
            "policy": policy_path,
            "instructions": json.dumps(_hex_list(instructions)),
            "retired_pc": _hex_or_empty(mips_state.last_pc),
            "retired_insn": _hex_or_empty(mips_state.last_insn),
            "coverage_hit": cov_hit,
            "coverage_total": cov_total,
            "new_bins": json.dumps(new_bins),
        }
    )


def build_policy_refiner(model_name):
    system_prompt = (
        "You are a hardware verification assistant. You update only a small "
        "Python constrained-random policy region for a MIPS32 CPU test. "
        "Return code only."
    )
    return ChatGPT(
        system_prompt=system_prompt,
        model_name=model_name,
        temperature=0.2,
        max_gen_tokens=3000,
        best_iter_buffer_resetting="STABLE",
        compress_msg_algo="recent 3",
        prioritise_harder_bins=False,
    )


def llm_crt_experiment(
    batch_cycles=100000,
    max_batches=5,
    server_ip_port=None,
    llm_model="gpt-5.4-mini",
):
    print("Running LLM-guided CRT policy experiment on MIPS...")
    print(f"CRT batch cycles: {batch_cycles}")
    print(f"Max batches: {max_batches}")
    print(f"Policy refiner model: {llm_model}\n")

    if batch_cycles <= 0:
        raise ValueError("batch_cycles must be positive")
    if max_batches <= 0:
        raise ValueError("max_batches must be positive")

    if server_ip_port is None:
        server_ip_port = input(
            "Please enter server's IP and port (e.g. 127.0.0.1:5050, 128.232.65.218:5555): "
        )

    t = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join("./logs", f"{t}_llm_crt")
    os.makedirs(log_dir, exist_ok=True)

    base_policy_path = os.path.join(repo_root, "agents", "llm_crt_policy_mips.py")
    current_policy_path = os.path.join(log_dir, "policy_iter_0.py")
    policy_log_path = os.path.join(log_dir, "policy_refinement.txt")
    trace_path = os.path.join(log_dir, "stimulus_trace.csv")
    shutil.copyfile(base_policy_path, current_policy_path)

    policy_refiner = None
    g_coverage = GlobalCoverageDatabase()

    with open(trace_path, "w", newline="", encoding="UTF8") as trace_file:
        trace_writer = csv.DictWriter(
            trace_file,
            fieldnames=[
                "batch",
                "cycle",
                "policy",
                "instructions",
                "retired_pc",
                "retired_insn",
                "coverage_hit",
                "coverage_total",
                "new_bins",
            ],
        )
        trace_writer.writeheader()

        for batch_idx in range(max_batches):
            print(
                f"\n===== CRT batch {batch_idx + 1}/{max_batches} "
                f"using {current_policy_path} ====="
            )
            agent = EditableCRTAgent(
                current_policy_path,
                total_cycle=batch_cycles,
                seed=datetime.now().timestamp() + batch_idx,
            )
            g_dut_state = GlobalDUTState()
            stimulus = Stimulus(insn_mem_updates=[], finish=False)
            last_printed_state = None
            # Cumulative plan carried across cycles: this cycle's "after" is the
            # next cycle's "before", so one rebuild per cycle suffices.
            coverage_before = _coverage_plan_or_empty(g_coverage)

            with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
                while not agent.end_simulation(g_dut_state, g_coverage):
                    instructions = agent.generate_next_value(g_dut_state, g_coverage)
                    instructions = list(_iter_policy_instructions(instructions))
                    # The MIPS testbench owns addressing: send bare words.
                    stimulus.insn_mem_updates = [
                        insn & 0xFFFFFFFF for insn in instructions
                    ]
                    mips_state, coverage = stimulus_sender.send_stimulus(stimulus)
                    g_dut_state.set(mips_state)
                    merge_cpu_coverage(g_coverage, coverage)
                    coverage_after = _coverage_plan_or_empty(g_coverage)
                    cov_hit, cov_total = _coverage_rate_from_plan(coverage_after)
                    write_mips_crt_trace_row(
                        trace_writer,
                        batch_idx,
                        agent.current_cycle,
                        current_policy_path,
                        instructions,
                        mips_state,
                        coverage_before,
                        coverage_after,
                        cov_hit,
                        cov_total,
                    )
                    trace_file.flush()
                    coverage_before = coverage_after

                    current_state = (mips_state.last_pc, mips_state.last_insn)
                    if (
                        mips_state.last_pc is not None
                        and current_state != last_printed_state
                    ):
                        print(
                            f"DUT state: {mips_state.last_pc:08x} "
                            f"{mips_state.last_insn:08x}\n"
                        )
                        last_printed_state = current_state

                    # Stop as soon as the plan is full. The between-batch check
                    # alone would keep driving stimulus for the rest of the
                    # batch after there is nothing left to cover.
                    if cov_total and cov_hit == cov_total:
                        print(
                            f"Full coverage reached at cycle "
                            f"{agent.current_cycle}; ending batch early."
                        )
                        break

                finish_stimulus = Stimulus(insn_mem_updates=[], finish=True)
                _, final_coverage = stimulus_sender.send_stimulus(finish_stimulus)
                merge_cpu_coverage(g_coverage, final_coverage)

            time.sleep(0.05)

            coverage_rate = g_coverage.get_coverage_rate()
            cov_hit, cov_total = coverage_rate
            cov_percent = (cov_hit / cov_total * 100) if cov_total > 0 else 0
            print(
                f"Coverage after batch {batch_idx + 1}: "
                f"({cov_hit}/{cov_total}) - {cov_percent:.2f}%"
            )

            if cov_hit == cov_total:
                print("Full coverage reached; stopping refinement.")
                break
            if batch_idx == max_batches - 1:
                print("Reached max CRT batches; stopping refinement.")
                break

            if policy_refiner is None:
                policy_refiner = build_policy_refiner(llm_model)

            with open(current_policy_path, "r", encoding="UTF8") as f:
                current_policy_text = f.read()
            previous_helper_region, previous_body_region = extract_policy_regions(
                current_policy_text
            )

            prompt = build_mips_policy_refinement_prompt(
                g_coverage.get_coverage_plan(),
                previous_helper_region,
                previous_body_region,
            )

            next_policy_path = os.path.join(log_dir, f"policy_iter_{batch_idx + 1}.py")
            last_error = None
            rejected_attempts = []
            accepted_region = None
            accepted_response = None
            for retry_idx in range(3):
                response = ""
                try:
                    response, _ = policy_refiner(prompt)
                    candidate_region = write_updated_policy(
                        current_policy_path,
                        next_policy_path,
                        response,
                        allow_instruction_blocks=True,
                    )
                    validate_mips_policy_outputs(
                        next_policy_path,
                        require_generated_helpers=True,
                    )
                    validate_mips_policy_is_productive(next_policy_path)
                    accepted_region = candidate_region
                    accepted_response = response
                    break
                except (SyntaxError, ValueError, RuntimeError) as e:
                    last_error = e
                    rejected_attempts.append((retry_idx + 1, response, e))
                    prompt += (
                        "\n\nYour previous policy region was rejected by validation:\n"
                        f"{type(e).__name__}: {e}\n"
                        "Return only corrected Python code with both editable "
                        "marker pairs. Do not return a full function or "
                        "explanatory text."
                    )
                    print(f"Rejected LLM policy update attempt {retry_idx + 1}/3: {e}")
                except Exception as e:
                    if e.__class__.__name__ != "RateLimitError":
                        raise
                    last_error = e
                    rejected_attempts.append((retry_idx + 1, response, e))
                    print(
                        "LLM policy update hit rate limit; continuing "
                        f"with existing policy. Error: {e}"
                    )
                    break

            if accepted_region is None:
                # write_updated_policy writes before validating, so a fully
                # rejected iteration leaves its last candidate at
                # next_policy_path. Move it aside: policy_iter_*.py must mean
                # "this policy actually drove a batch".
                if os.path.exists(next_policy_path):
                    os.replace(
                        next_policy_path,
                        os.path.join(log_dir, f"rejected_iter_{batch_idx + 1}.py"),
                    )
                append_policy_rejection_log(
                    policy_log_path,
                    batch_idx + 1,
                    coverage_rate,
                    prompt,
                    rejected_attempts,
                    current_policy_path,
                )
                print(
                    "Could not get a valid LLM policy update; continuing with "
                    f"existing policy. Last error: {last_error}"
                )
                continue

            append_policy_log(
                policy_log_path,
                batch_idx + 1,
                coverage_rate,
                prompt,
                accepted_response,
                accepted_region,
            )
            current_policy_path = next_policy_path
            print(f"Accepted updated policy: {current_policy_path}")

    coverage_plan = {k: v for (k, v) in g_coverage.get_coverage_plan().items() if v > 0}
    print(
        f"\nFinished LLM-guided CRT experiment with hits: {coverage_plan}\n"
        f"Coverage: {g_coverage.get_coverage_rate()}\n"
        f"Policy logs: {policy_log_path}\n"
        f"Stimulus trace: {trace_path}\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument("--batch_cycles", type=int, default=100000)
    parser.add_argument("--max_batches", type=int, default=5)
    parser.add_argument("--server", type=str, default=None)
    parser.add_argument("--llm_model", type=str, default="gpt-5.4-mini")
    args = parser.parse_args()
    llm_crt_experiment(
        batch_cycles=args.batch_cycles,
        max_batches=args.max_batches,
        server_ip_port=args.server,
        llm_model=args.llm_model,
    )
