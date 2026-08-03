#!/bin/env python3
# Copyright Zixi Zhang
# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

import csv
import argparse
import copy
import json
import os
import sys
import shutil
import time
from datetime import datetime

import zmq
import pickle
from contextlib import closing

directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(directory))
# print(sys.path)

from agents.agent_LLM import *
from agents.agent_llm_crt import (
    EditableCRTAgent,
    append_policy_log,
    append_policy_rejection_log,
    extract_policy_regions,
    load_policy_module,
    write_updated_policy,
)
from agents.agent_IC_dumb import *
from agents.agent_random import *
from loggers.logger_csv import CSVLogger
from loggers.logger_txt import TXTLogger
from models.llm_gpt import ChatGPT
from ibex_cpu.instructions import Encoding
from ibex_cpu.shared_types import *
from stimuli_extractor import *
from stimuli_filter import *
from prompt_generators.prompt_generator_template_IC import *

directory = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.dirname(directory)


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
        if not isinstance(state_coverage_obj[0], IbexStateInfo):
            raise RuntimeError("Bad format of coverage response element 0")
        if not isinstance(state_coverage_obj[1], CoverageDatabase):
            raise RuntimeError("Bad format of coverage response element 1")

        return state_coverage_obj

    def close(self):
        if self.socket:
            self.socket.close()


def random_experiment():
    print("Running random experiment on IC...")

    server_ip_port = input(
        "Please enter server's IP and port (e.g. 127.0.0.1:5050, 128.232.65.218:5555): "
    )

    CYCLES = 1000000

    agent = ConstrainedRandomAgent4IC(total_cycle=CYCLES, seed=datetime.now().timestamp())
    #agent = RandomAgent4IC(total_cycle=CYCLES, seed=datetime.now().timestamp())

    stimulus = Stimulus(insn_mem_updates=[], finish=False)
    g_dut_state = GlobalDUTState()
    g_coverage = GlobalCoverageDatabase()

    start_time = datetime.now()

    with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
        while not agent.end_simulation(g_dut_state, g_coverage):
            stimulus.insn_mem_updates = agent.generate_next_value(
                g_dut_state, g_coverage
            )
            # print(
            #     f"Generated updates[:4]: "
            #     f"{list(map(lambda p: (hex(p[0]), hex(p[1])), stimulus.insn_mem_updates))[:4]}\n"
            # )
            ibex_state, coverage = stimulus_sender.send_stimulus(stimulus)
            g_dut_state.set(ibex_state)
            g_coverage.set(coverage)

            if ibex_state.last_pc is not None:
                print(
                    f"DUT state: {ibex_state.last_pc:08x} {ibex_state.last_insn:08x}\n"
                )

        stimulus.finish = True
        _, final_coverage = stimulus_sender.send_stimulus(stimulus)
        final_coverage.output()

        g_coverage.set(final_coverage)
        cov_hit, cov_total = g_coverage.get_coverage_rate()
        cov_percent = (cov_hit / cov_total * 100) if cov_total > 0 else 0
        print(f"Final coverage rate: ({cov_hit}/{cov_total}) - {cov_percent:.2f}%")

        end_time = datetime.now()
        sim_time = (end_time - start_time).total_seconds()

        csv_dir = "./logs"
        if not os.path.exists(csv_dir):
            os.makedirs(csv_dir)

        csv_file = f"{csv_dir}/CRT_experiments.csv"

        run_id = 1
        write_header = not os.path.exists(csv_file)
        if not write_header:
            with open(csv_file, "r", encoding="UTF8") as f:
                reader = csv.reader(f)
                rows = list(reader)
                if len(rows) > 1:
                    try:
                        run_id = int(rows[-1][0]) + 1
                    except ValueError:
                        run_id = len(rows)

        with open(csv_file, "a+", encoding="UTF8", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(["ID", "Simulation Time (s)", "Number of Cycles", "Covered Bins", "Total Bins",
                                 "Coverage Percentage"])

            writer.writerow([
                run_id,
                sim_time,
                agent.current_cycle,
                cov_hit,
                cov_total,
                f"{cov_percent:.2f}%"
            ])


def build_llm_agent(log_name_suffix=""):
    prompt_generator = TemplatePromptGenerator4IC2(
        bin_descr_path=os.path.join(directory, "../examples_IC/bins_description.txt"),
        sampling_missed_bins_method="ICNEWEST",
    )

    stimulus_generator = ChatGPT(
        max_gen_tokens=1000,
        system_prompt=prompt_generator.generate_system_prompt(),
        best_iter_buffer_resetting="STABLE",
        compress_msg_algo="best 2 recent 1",
        prioritise_harder_bins=False,
    )
    extractor = ICExtractor()
    stimulus_filter = ICFilter(0x0, 0xFFFFFFFF)

    prefix = f"./logs/"
    t = datetime.now()
    t = t.strftime("%Y%m%d_%H%M%S")
    logger_txt = TXTLogger(f"{prefix}{t}{log_name_suffix}.txt")
    logger_csv = CSVLogger(f"{prefix}{t}{log_name_suffix}.csv")

    return LLMAgent(
        prompt_generator,
        stimulus_generator,
        extractor,
        stimulus_filter,
        [logger_txt, logger_csv],
        dialog_bound=700,
        rst_plan=rst_plan_Low_Tolerance,
    )


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


def cpu_helper_reference():
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
        "of 32-bit instruction integers. The driver writes a returned block "
        "to consecutive instruction addresses in one simulator transaction and "
        "keeps later writes from overwriting the block.\n"
        "- The CPU coverage model accepts only R-type ALU instructions, "
        "S-type stores, and JAL. Do not generate loads, branches, I-type ALU, "
        "JALR, LUI, AUIPC, compressed instructions, or arbitrary illegal words.\n"
        "- R-type ALU ops use opcode 0x33. Encodings are: "
        "add funct7=0x00 funct3=0, sub 0x20/0, sll 0x00/1, "
        "slt 0x00/2, sltu 0x00/3, xor 0x00/4, srl 0x00/5, "
        "sra 0x20/5, or 0x00/6, and 0x00/7.\n"
        "- S-type stores must use opcode 0x23, imm[11:5] in bits 31:25, rs2 "
        "in 24:20, rs1 in 19:15, funct3 in 14:12, imm[4:0] in 11:7. "
        "sb uses funct3=0, sh=1, sw=2. Prefer small aligned immediates: any "
        "byte offset for sb, even offsets for sh, multiples of four for sw.\n"
        "- JAL must use opcode 0x6f, rd in bits 11:7, imm[20] in bit 31, "
        "imm[10:1] in bits 30:21, imm[11] in bit 20, imm[19:12] in bits "
        "19:12. Use even, nonzero offsets. Negative offsets cover "
        "br_backwards; positive offsets cover br_forwards.\n"
        "- Never emit opcode 0x13, 0x03, 0x63, 0x67, 0x37, 0x17, 0x73, or "
        "compressed halfword encodings. They are outside this coverage subset.\n"
        "- Per-instruction bins are named like add_seen, add_zero_dst, "
        "add_zero_src, add_same_src, sw_seen, jal_br_backwards, and "
        "jal_br_forwards.\n"
        "- RAW hazard cross bins are named like add->sub_raw_hazard or "
        "jal->sw_raw_hazard. A RAW hazard is covered when the previous R-type "
        "or JAL writes rd and the next R-type/store reads that same register.\n"
        "- If you target RAW hazards, return the producer and consumer in the "
        "same list/tuple so they are written into instruction memory together.\n"
    )


def build_cpu_policy_refinement_prompt(
    policy_text,
    coverage_plan,
    previous_helper_region,
    previous_body_region,
    max_missed_bins=120,
):
    return (
        "You are improving a constrained-random instruction generator for an "
        "Ibex CPU coverage test.\n"
        "The simulator/testbench interface is fixed. You may edit only two "
        "marked regions: BEGIN/END LLM HELPER REGION at module scope, and "
        "BEGIN/END LLM BODY REGION inside generate_instruction(coverage, rng).\n\n"
        "Rules:\n"
        "- Return only Python code containing both editable marker pairs.\n"
        "- Do not include markdown or explanations.\n"
        "- Do not include def generate_instruction(...); return only the helper "
        "region and the body region contents with their markers.\n"
        "- The code must return either one valid Ibex CPU test instruction as "
        "a 32-bit integer, or a short list/tuple of valid instructions. Use "
        "R-type ALU, S-type store, or JAL only.\n"
        "- Define helper functions/constants in the helper region.\n"
        "- Include at least one helper function definition in the helper region "
        "for instruction encoding or targeted generation. Do not leave all "
        "encoding logic inline in the body region.\n"
        "- Do not rely on helper functions from the policy file; the scaffold "
        "intentionally provides none.\n"
        "- Put per-call selection logic and the final return in the body region.\n"
        "- Every path through the body must return an instruction integer or "
        "instruction list. Include a final fallback return at the end of the "
        "body; never let Python implicitly return None.\n"
        "- Use the unreached bin groups below to spread generation across "
        "instruction families, simple coverpoints, and RAW hazard pairs.\n"
        "- Do not put one RAW hazard as the first unconditional miss target "
        "when many simple bins are still uncovered. A sticky hard bin must not "
        "block simpler add/sub/store/jal exploration.\n"
        "- For RAW hazard targets, return the producer and consumer as a "
        "two-instruction list in the same response, for example "
        "[enc_r('sra', rd, a, b), enc_r('sub', out, rd, c)]. Do not rely on "
        "a pending instruction across calls for RAW hazards.\n"
        "- Do not import modules, open files, define classes, or use exec/eval.\n"
        "- Keep some randomness so CRT can still explore.\n\n"
        f"{cpu_helper_reference()}\n"
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
            raise ValueError("CPU policy returned an empty instruction block")
        if len(result) > 8:
            raise ValueError(
                f"CPU policy returned an excessively large instruction block: {len(result)}"
            )
        instructions = result
    else:
        instructions = [result]

    for instruction in instructions:
        if not isinstance(instruction, int):
            raise ValueError(
                "CPU policy returned a non-integer instruction value: "
                f"{instruction!r}"
            )
        if not 0 <= instruction <= 0xFFFFFFFF:
            raise ValueError(
                "CPU policy returned an instruction outside 32-bit range: "
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


def describe_cpu_instruction_rejection(instruction):
    opcode = instruction & 0x7F
    unsupported = {
        0x03: "load",
        0x13: "I-type ALU",
        0x17: "AUIPC",
        0x37: "LUI",
        0x63: "branch",
        0x67: "JALR",
        0x73: "system",
    }
    if opcode in {0x33, 0x23, 0x6F}:
        return (
            f"{instruction:#010x} uses allowed opcode {opcode:#04x}, but its "
            "funct/immediate fields do not match a supported add/sub/sll/slt/"
            "sltu/xor/srl/sra/or/and, sb/sh/sw, or jal encoding"
        )
    family = unsupported.get(opcode, "unknown or compressed/illegal")
    return (
        f"{instruction:#010x} has opcode {opcode:#04x} ({family}), but this "
        "coverage subset accepts only R-type opcode 0x33, S-type store opcode "
        "0x23, and JAL opcode 0x6f"
    )


def validate_cpu_policy_outputs(policy_path, samples=256, require_generated_helpers=False):
    if require_generated_helpers:
        with open(policy_path, "r", encoding="UTF8") as f:
            helper_region, _ = extract_policy_regions(f.read())
        if "def " not in helper_region:
            raise ValueError(
                "CPU policy refinement must define its own helper function in "
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
                typed_instruction = Encoding(instruction).typed()
                if typed_instruction is None:
                    raise ValueError(
                        "CPU policy returned an instruction outside the supported "
                        "R/S/J coverage subset: "
                        f"{describe_cpu_instruction_rejection(instruction)}"
                    )
                try:
                    typed_instruction.instruction()
                except AssertionError as e:
                    raise ValueError(
                        "CPU policy returned an invalid instruction encoding for "
                        "the supported R/S/J subset: "
                        f"{describe_cpu_instruction_rejection(instruction)}"
                    ) from e


def validate_cpu_policy_is_productive(policy_path, samples=512):
    module = load_policy_module(policy_path)

    import random

    seen_instruction_names = set()
    rng = random.Random(1)
    coverage = _DefaultCoverage(1)

    for _ in range(samples):
        result = module.generate_instruction(coverage, rng)
        for instruction in _iter_policy_instructions(result):
            typed_instruction = Encoding(instruction).typed()
            if typed_instruction is None:
                raise ValueError(
                    "CPU policy returned an instruction outside the supported "
                    "R/S/J coverage subset: "
                    f"{describe_cpu_instruction_rejection(instruction)}"
                )
            seen_instruction_names.add(typed_instruction.instruction().value)

    if len(seen_instruction_names) < 4:
        raise ValueError(
            "CPU policy is too narrow under covered feedback; it generated only "
            f"{sorted(seen_instruction_names)}"
        )


def instruction_memory_updates_for_pc(pc, instruction):
    instruction &= 0xFFFFFFFF

    if pc & 0x1:
        raise ValueError(f"Instruction PC is not half-word aligned: {pc:#x}")

    if pc & 0x2:
        word_addr = pc & ~0x3
        lower_word = ((instruction & 0xFFFF) << 16) | 0x0001
        upper_word = ((instruction >> 16) & 0xFFFF) | (0x0001 << 16)
        return [(word_addr, lower_word), (word_addr + 4, upper_word)]

    return [(pc, instruction)]


def instruction_block_memory_updates_for_pc(pc, instructions):
    instructions = list(_iter_policy_instructions(instructions))

    if pc & 0x1:
        raise ValueError(f"Instruction PC is not half-word aligned: {pc:#x}")

    if not (pc & 0x2):
        updates = []
        for idx, instruction in enumerate(instructions):
            updates.extend(instruction_memory_updates_for_pc(pc + (idx * 4), instruction))
        return updates

    halfwords = [0x0001]
    for instruction in instructions:
        instruction &= 0xFFFFFFFF
        halfwords.append(instruction & 0xFFFF)
        halfwords.append((instruction >> 16) & 0xFFFF)
    halfwords.append(0x0001)

    updates = []
    word_addr = pc & ~0x3
    for idx in range(0, len(halfwords), 2):
        low = halfwords[idx]
        high = halfwords[idx + 1] if idx + 1 < len(halfwords) else 0x0001
        updates.append((word_addr + (idx // 2) * 4, low | (high << 16)))

    return updates


def next_cpu_stimulus_pc(g_dut_state):
    ibex_state = g_dut_state.get()
    if not isinstance(ibex_state, IbexStateInfo) or ibex_state.last_pc is None:
        return int(g_dut_state.get_pc(), 16)

    if ibex_state.last_insn is not None:
        typed_instruction = Encoding(ibex_state.last_insn).typed()
        if typed_instruction is not None:
            try:
                if typed_instruction.instruction().value == "jal":
                    return ibex_state.last_pc + typed_instruction.offset()
            except AssertionError:
                pass

    return ibex_state.last_pc + 4


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


def _hex_mem_updates(updates):
    return [
        [f"0x{addr:08x}", f"0x{word & 0xFFFFFFFF:08x}"]
        for addr, word in updates
    ]


def _coverage_rate_or_empty(g_coverage):
    if g_coverage.get() is None:
        return "", ""
    return g_coverage.get_coverage_rate()


def _coverage_plan_or_empty(g_coverage):
    if g_coverage.get() is None:
        return {}
    return g_coverage.get_coverage_plan()


def write_cpu_crt_trace_row(
    trace_writer,
    batch_idx,
    cycle,
    policy_path,
    stimulus_pc,
    instructions,
    mem_updates,
    ibex_state,
    coverage_before,
    g_coverage,
):
    coverage_after = _coverage_plan_or_empty(g_coverage)
    new_bins = sorted(
        name
        for name, hits in coverage_after.items()
        if hits > 0 and coverage_before.get(name, 0) == 0
    )
    cov_hit, cov_total = _coverage_rate_or_empty(g_coverage)
    trace_writer.writerow(
        {
            "batch": batch_idx + 1,
            "cycle": cycle,
            "policy": policy_path,
            "stimulus_pc": _hex_or_empty(stimulus_pc),
            "instructions": json.dumps(_hex_list(instructions)),
            "mem_updates": json.dumps(_hex_mem_updates(mem_updates)),
            "retired_pc": _hex_or_empty(ibex_state.last_pc),
            "retired_insn": _hex_or_empty(ibex_state.last_insn),
            "coverage_hit": cov_hit,
            "coverage_total": cov_total,
            "new_bins": json.dumps(new_bins),
        }
    )


def hybrid_experiment(random_cycles=10000, server_ip_port=None):
    print("Running hybrid random-then-LLM experiment on IC...")
    print(f"Random warm-up cycles: {random_cycles}\n")

    if random_cycles <= 0:
        raise ValueError("random_cycles must be positive")

    if server_ip_port is None:
        server_ip_port = input(
            "Please enter server's IP and port (e.g. 127.0.0.1:5050, 128.232.65.218:5555): "
        )

    random_agent = ConstrainedRandomAgent4IC(
        total_cycle=random_cycles, seed=datetime.now().timestamp()
    )

    stimulus = Stimulus(insn_mem_updates=[], finish=False)
    g_dut_state = GlobalDUTState()
    g_coverage = GlobalCoverageDatabase()

    with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
        print("Starting random warm-up...")
        while not random_agent.end_simulation(g_dut_state, g_coverage):
            stimulus.insn_mem_updates = random_agent.generate_next_value(
                g_dut_state, g_coverage
            )
            ibex_state, coverage = stimulus_sender.send_stimulus(stimulus)
            g_dut_state.set(ibex_state)
            g_coverage.set(coverage)

            if ibex_state.last_pc is not None:
                print(
                    f"DUT state: {ibex_state.last_pc:08x} {ibex_state.last_insn:08x}\n"
                )

        print_coverage_summary("Coverage after random warm-up", g_coverage)

        cov_hit, cov_total = g_coverage.get_coverage_rate()
        if cov_hit < cov_total:
            print("Switching to LLM-guided stimulus generation...\n")
            llm_agent = build_llm_agent(log_name_suffix="_hybrid")
            llm_agent.start_iterative_from_existing_coverage()

            while not llm_agent.end_simulation(g_dut_state, g_coverage):
                stimulus.insn_mem_updates = llm_agent.generate_next_value(
                    g_dut_state, g_coverage, is_ic=True
                )
                print(
                    f"Generated updates[:10]: "
                    f"{list(map(lambda p: (hex(p[0]), hex(p[1])), stimulus.insn_mem_updates))[:10]}\n"
                )
                ibex_state, coverage = stimulus_sender.send_stimulus(stimulus)
                g_dut_state.set(ibex_state)
                g_coverage.set(coverage)

                if ibex_state.last_pc is not None:
                    print(
                        f"DUT state: {ibex_state.last_pc:08x} {ibex_state.last_insn:08x}\n"
                    )
        else:
            print("Random warm-up reached full coverage; skipping LLM phase.\n")

        finish_stimulus = Stimulus(insn_mem_updates=[], finish=True)
        _, final_coverage = stimulus_sender.send_stimulus(finish_stimulus)
        g_coverage.set(final_coverage)

    g_coverage.get().output()
    print_coverage_summary("Final coverage", g_coverage)


def build_policy_refiner(model_name):
    system_prompt = (
        "You are a hardware verification assistant. You update only a small "
        "Python constrained-random policy region for an Ibex CPU test. "
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
    print("Running LLM-guided CRT policy experiment on IC...")
    print(f"CRT batch cycles: {batch_cycles}")
    print(f"Max batches: {max_batches}\n")
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

    base_policy_path = os.path.join(repo_root, "agents", "llm_crt_policy_cpu.py")
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
                "stimulus_pc",
                "instructions",
                "mem_updates",
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
            next_write_pc = int(g_dut_state.get_pc(), 16)

            with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
                while not agent.end_simulation(g_dut_state, g_coverage):
                    instructions = agent.generate_next_value(g_dut_state, g_coverage)
                    instructions = list(_iter_policy_instructions(instructions))
                    pc = max(next_write_pc, next_cpu_stimulus_pc(g_dut_state))
                    stimulus.insn_mem_updates = instruction_block_memory_updates_for_pc(
                        pc, instructions
                    )
                    next_write_pc = pc + (4 * len(instructions))
                    coverage_before = _coverage_plan_or_empty(g_coverage)
                    ibex_state, coverage = stimulus_sender.send_stimulus(stimulus)
                    g_dut_state.set(ibex_state)
                    merge_cpu_coverage(g_coverage, coverage)
                    write_cpu_crt_trace_row(
                        trace_writer,
                        batch_idx,
                        agent.current_cycle,
                        current_policy_path,
                        pc,
                        instructions,
                        stimulus.insn_mem_updates,
                        ibex_state,
                        coverage_before,
                        g_coverage,
                    )
                    trace_file.flush()

                    current_state = (ibex_state.last_pc, ibex_state.last_insn)
                    if ibex_state.last_pc is not None and current_state != last_printed_state:
                        print(
                            f"DUT state: {ibex_state.last_pc:08x} {ibex_state.last_insn:08x}\n"
                        )
                        last_printed_state = current_state

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

            prompt = build_cpu_policy_refinement_prompt(
                current_policy_text,
                g_coverage.get_coverage_plan(),
                previous_helper_region,
                previous_body_region,
            )

            next_policy_path = os.path.join(
                log_dir, f"policy_iter_{batch_idx + 1}.py"
            )
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
                    validate_cpu_policy_outputs(
                        next_policy_path,
                        require_generated_helpers=True,
                    )
                    validate_cpu_policy_is_productive(next_policy_path)
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
                    print(
                        f"Rejected LLM policy update attempt {retry_idx + 1}/3: {e}"
                    )
                except Exception as e:
                    if e.__class__.__name__ != "RateLimitError":
                        raise
                    last_error = e
                    rejected_attempts.append((retry_idx + 1, response, e))
                    print(
                        "LLM policy update hit OpenAI rate limit; continuing "
                        f"with existing policy. Error: {e}"
                    )
                    break

            if accepted_region is None:
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

    coverage_plan = {
        k: v for (k, v) in g_coverage.get_coverage_plan().items() if v > 0
    }
    print(
        f"\nFinished LLM-guided CRT experiment with hits: {coverage_plan}\n"
        f"Coverage: {g_coverage.get_coverage_rate()}\n"
        f"Policy logs: {policy_log_path}\n"
        f"Stimulus trace: {trace_path}\n"
    )


def main():
    print("Running main experiment on IC...\n")

    server_ip_port = input(
        "Please enter server's IP and port (e.g. 127.0.0.1:5050, 128.232.65.218:5555): "
    )

    agent = build_llm_agent()
    print("Agent successfully built\n")

    # agent = DumbAgent4IC()

    stimulus = Stimulus(insn_mem_updates=[], finish=False)
    g_dut_state = GlobalDUTState()
    g_coverage = GlobalCoverageDatabase()

    with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
        while not agent.end_simulation(g_dut_state, g_coverage):
            stimulus.insn_mem_updates = agent.generate_next_value(
                g_dut_state, g_coverage, is_ic=True
            )
            print(
                f"Generated updates[:10]: "
                f"{list(map(lambda p: (hex(p[0]), hex(p[1])), stimulus.insn_mem_updates))[:10]}\n"
            )
            ibex_state, coverage = stimulus_sender.send_stimulus(stimulus)
            g_dut_state.set(ibex_state)
            g_coverage.set(coverage)

            if ibex_state.last_pc is not None:
                print(
                    f"DUT state: {ibex_state.last_pc:08x} {ibex_state.last_insn:08x}\n"
                )

        stimulus.finish = True
        _, final_coverage = stimulus_sender.send_stimulus(stimulus)
        final_coverage.output()

        g_coverage.set(final_coverage)
        print(f"Final coverage rate: {g_coverage.get_coverage_rate()}")


def budget_experiment():
    print("Running budget experiment on IC...")

    INIT_BUDGET = 10000000
    print(f"Start with BUDGET={INIT_BUDGET}\n")

    BUDGET = Budget(budget_per_trial=INIT_BUDGET, total_budget=INIT_BUDGET)

    server_ip_port = input(
        "Please enter server's IP and port (e.g. 127.0.0.1:5050, 128.232.65.218:5555): "
    )

    t = datetime.now()
    t = t.strftime("%Y%m%d_%H%M%S")
    prefix = f"./logs/{t}_budget/"
    if not os.path.exists(prefix):
        os.makedirs(prefix)

    header = ["Trial #", "Message cnt", "Token cnt", "Coverage rate", "Coverage plan"]
    data = []
    trial_cnt = 0

    with open(f"{prefix}{t}_summary.csv", "a+", encoding="UTF8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)

    while BUDGET.total_budget > 0:
        trial_cnt += 1
        BUDGET.init_budget = min(BUDGET.init_budget, BUDGET.total_budget)
        BUDGET.budget = BUDGET.init_budget

        # build components
        prompt_generator = TemplatePromptGenerator4IC2(
            bin_descr_path="../examples_IC/bins_description.txt",
            sampling_missed_bins_method="ICNEWEST",
        )

        stimulus_generator = ChatGPT(
            max_gen_tokens=1000,
            system_prompt=prompt_generator.generate_system_prompt(),
            best_iter_buffer_resetting="STABLE",
            compress_msg_algo="best 3",
            prioritise_harder_bins=False,
        )
        extractor = ICExtractor()
        stimulus_filter = ICFilter(0x0, 0xFFFFFFFF)

        # build loggers
        logger_txt = TXTLogger(f"{prefix}{t}_trial_{trial_cnt}.txt")
        logger_csv = CSVLogger(f"{prefix}{t}_trial_{trial_cnt}.csv")

        # create agent
        agent = LLMAgent(
            prompt_generator,
            stimulus_generator,
            extractor,
            stimulus_filter,
            [logger_txt, logger_csv],
            dialog_bound=1000,
            rst_plan=rst_plan_Low_Tolerance,
            token_budget=BUDGET,
        )
        print("Agent successfully built\n")

        stimulus = Stimulus(insn_mem_updates=[], finish=False)
        g_dut_state = GlobalDUTState()
        g_coverage = GlobalCoverageDatabase()

        with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
            while not agent.end_simulation(g_dut_state, g_coverage):
                stimulus.insn_mem_updates = agent.generate_next_value(
                    g_dut_state, g_coverage, is_ic=True
                )
                print(
                    f"Generated updates[:4]: "
                    f"{list(map(lambda p: (hex(p[0]), hex(p[1])), stimulus.insn_mem_updates))[:4]}\n"
                )
                ibex_state, coverage = stimulus_sender.send_stimulus(stimulus)
                g_dut_state.set(ibex_state)
                g_coverage.set(coverage)

                if ibex_state.last_pc is not None:
                    print(
                        f"DUT state: {ibex_state.last_pc:08x} {ibex_state.last_insn:08x}\n"
                    )

            stimulus.finish = True
            _, final_coverage = stimulus_sender.send_stimulus(stimulus)
            # final_coverage.output()
            g_coverage.set(final_coverage)

            data.append(
                [
                    trial_cnt,
                    agent.total_msg_cnt,
                    BUDGET.init_budget - BUDGET.budget,
                    g_coverage.get_coverage_rate()[0],
                    str(
                        dict(
                            filter(
                                lambda p: p[1] != 0,
                                g_coverage.get_coverage_plan().items(),
                            )
                        )
                    ),
                ]
            )
            with open(
                    f"{prefix}{t}_summary.csv", "a+", encoding="UTF8", newline=""
            ) as f:
                writer = csv.writer(f)
                writer.writerow(data[-1])

            BUDGET.total_budget -= BUDGET.init_budget - BUDGET.budget
            print(
                f">>>> Finished trial #{trial_cnt} at dialog #{agent.dialog_index}, message #{agent.msg_index}, \n"
                f"with total {agent.total_msg_cnt} messages \n"
                f"and total {BUDGET.init_budget - BUDGET.budget} tokens \n"
                f"Coverage rate: {g_coverage.get_coverage_rate()}\n"
                f"BUDGET left: {BUDGET.total_budget} tokens\n"
            )

    print("\n******** FINAL RESULT ********\n")
    for entry in data:
        print(
            f"Trial #{entry[0]}, Msg cnt: {entry[1]}, Token cnt: {entry[2]}, Coverage: {entry[3]}"
        )
    min_hit_id = np.argmin([entry[3] for entry in data])
    max_hit_id = np.argmax([entry[3] for entry in data])
    print(
        f"\n"
        f"Total trial cnt: {trial_cnt}\n"
        f"Total token cnt: {INIT_BUDGET - BUDGET.total_budget}\n"
        f"Min coverage: {data[min_hit_id][3]} by trial #{data[min_hit_id][0]}\n"
        f"Max coverage: {data[max_hit_id][3]} by trial #{data[max_hit_id][0]}\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        nargs="?",
        default="random",
        choices=["random", "main", "hybrid", "llm_crt", "budget"],
        help="Experiment mode to run",
    )
    parser.add_argument(
        "--server",
        dest="server_ip_port",
        help="CPU server IP and port, e.g. 127.0.0.1:5050",
    )
    parser.add_argument(
        "--random-cycles",
        type=int,
        default=10000,
        help="Number of constrained-random stimuli before switching to LLM in hybrid mode",
    )
    parser.add_argument(
        "--batch-cycles",
        type=int,
        default=100000,
        help="Number of CRT stimuli per batch in llm_crt mode",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=5,
        help="Maximum number of CRT/refinement batches in llm_crt mode",
    )
    parser.add_argument(
        "--llm-model",
        default="gpt-5.4-mini",
        help="OpenAI model name for policy refinement in llm_crt mode",
    )
    args = parser.parse_args()

    if args.mode == "random":
        random_experiment()
    elif args.mode == "main":
        main()
    elif args.mode == "hybrid":
        hybrid_experiment(args.random_cycles, args.server_ip_port)
    elif args.mode == "llm_crt":
        llm_crt_experiment(
            args.batch_cycles,
            args.max_batches,
            args.server_ip_port,
            args.llm_model,
        )
    elif args.mode == "budget":
        budget_experiment()
