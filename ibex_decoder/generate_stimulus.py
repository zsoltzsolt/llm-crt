#!/bin/env python3
# Copyright Zixi Zhang
# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

import csv
import argparse
import json
from datetime import datetime
import zmq
import pickle
from contextlib import closing
import sys
import os
import re
import shutil

directory = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(directory))
# print(sys.path)

from ibex_decoder.shared_types import *
from global_shared_types import *
from agents.agent_llm_crt import (
    EditableCRTAgent,
    append_policy_log,
    append_policy_rejection_log,
    build_policy_refinement_prompt,
    extract_policy_regions,
    write_updated_policy,
)
from agents.agent_random import RandomAgent, ConstrainedRandomAgent
from agents.agents_CLI import *
from agents.agent_LLM import *
from prompt_generators.prompt_generator_fixed_ID import FixedPromptGenerator4ID1
from prompt_generators.prompt_generator_template_ID import *
from models.llm_gpt import ChatGPT
from stimuli_extractor import DumbExtractor
from stimuli_filter import Filter
from loggers.logger_csv import CSVLogger
from loggers.logger_txt import TXTLogger
from agents.agent_ID_dumb import DumbAgent4ID


class StimulusSender:
    def __init__(self, zmq_addr):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect(zmq_addr)

    def send_stimulus(self, stimulus_obj):
        self.socket.send_pyobj(stimulus_obj)
        coverage_obj = self.socket.recv_pyobj()

        if not isinstance(coverage_obj, CoverageDatabase):
            raise RuntimeError("Bad format of coverage response")

        return coverage_obj

    def close(self):
        if self.socket:
            self.socket.close()


def _hex_instruction_or_empty(value):
    return "" if value is None else f"0x{value & 0xFFFFFFFF:08x}"


def _decoder_coverage_rate_or_empty(g_coverage):
    if g_coverage.get() is None:
        return "", ""
    return g_coverage.get_coverage_rate()


def _decoder_coverage_plan_or_empty(g_coverage):
    if g_coverage.get() is None:
        return {}
    return g_coverage.get_coverage_plan()


def write_decoder_crt_trace_row(
    trace_writer,
    batch_idx,
    cycle,
    policy_path,
    instruction,
    coverage_before,
    g_coverage,
):
    coverage_after = _decoder_coverage_plan_or_empty(g_coverage)
    new_bins = sorted(
        name
        for name, hits in coverage_after.items()
        if hits > 0 and coverage_before.get(name, 0) == 0
    )
    cov_hit, cov_total = _decoder_coverage_rate_or_empty(g_coverage)
    trace_writer.writerow(
        {
            "batch": batch_idx + 1,
            "cycle": cycle,
            "policy": policy_path,
            "instruction": _hex_instruction_or_empty(instruction),
            "coverage_hit": cov_hit,
            "coverage_total": cov_total,
            "new_bins": json.dumps(new_bins),
        }
    )


def _strip_wrapping_quotes(value):
    value = value.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return value


def _parse_coverage_rate(value):
    if value is None:
        return None

    value = str(value).strip()
    if not value:
        return None

    try:
        return int(value)
    except ValueError:
        match = re.search(r"-?\d+", value)
        return int(match.group(0)) if match else None


def _extract_instructions_from_response(response):
    response = _strip_wrapping_quotes(str(response))
    instructions = []

    try:
        payload = json.loads(response)
        if isinstance(payload, dict) and isinstance(payload.get("instructions"), list):
            instructions = payload["instructions"]
        elif isinstance(payload, list):
            instructions = payload
    except json.JSONDecodeError:
        pass

    if not instructions:
        instructions = DumbExtractor()(response)

    parsed_instructions = []
    for instruction in instructions:
        if isinstance(instruction, str):
            base = 16 if instruction.lower().startswith("0x") else 10
            instruction = int(instruction, base)
        if not isinstance(instruction, int):
            raise ValueError(f"Instruction {instruction!r} is not an integer")
        if not 0 <= instruction <= 0xFFFFFFFF:
            raise ValueError(f"Instruction {instruction:#x} is outside 32-bit range")
        parsed_instructions.append(instruction)

    return parsed_instructions


def _iter_csv_log_rows(csv_path):
    with open(csv_path, "r", encoding="UTF8", newline="") as f:
        rows = list(csv.reader(f))

    header_index = None
    for i, row in enumerate(rows):
        if "ASSISTANT" in row and "Coverage Rate" in row:
            header_index = i
            break

    if header_index is None:
        raise ValueError(
            f"{csv_path} does not contain a CSVLogger header with ASSISTANT "
            "and Coverage Rate columns"
        )

    header = rows[header_index]
    for row in rows[header_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue
        padded_row = row + [""] * (len(header) - len(row))
        yield dict(zip(header, padded_row))


def extract_instruction_entries(csv_path, only_improving=False, initial_coverage=0):
    """Return assistant instructions from CSV rows, optionally only improving rows."""

    entries = []
    previous_coverage = initial_coverage

    for row in _iter_csv_log_rows(csv_path):
        coverage_rate = _parse_coverage_rate(row.get("Coverage Rate"))
        if coverage_rate is None:
            continue

        improved = coverage_rate > previous_coverage
        if improved or not only_improving:
            instructions = _extract_instructions_from_response(row.get("ASSISTANT", ""))
            if instructions:
                entries.append(
                    {
                        "total_message": row.get("Total Message#", ""),
                        "dialog": row.get("Dialog #", ""),
                        "message": row.get("Message #", ""),
                        "coverage_before": previous_coverage,
                        "coverage_after": coverage_rate,
                        "improved": improved,
                        "instructions": instructions,
                    }
                )

        previous_coverage = max(previous_coverage, coverage_rate)

    return entries


def extract_coverage_improving_instructions(csv_path, initial_coverage=0):
    """Return assistant instructions from rows that increased cumulative coverage."""

    return extract_instruction_entries(
        csv_path,
        only_improving=True,
        initial_coverage=initial_coverage,
    )


def replay_csv_stimuli(csv_path=None, server_ip_port=None, only_improving=False):
    replay_kind = "coverage-improving" if only_improving else "all-row"
    print(f"Running {replay_kind} CSV replay experiment on ID...")

    if csv_path is None:
        csv_path = input(
            "Please enter CSV log path (e.g. ../example_output.csv): "
        ).strip()

    if server_ip_port is None:
        server_ip_port = input(
            "Please enter server's IP and port (e.g. 127.0.0.1:5050, 128.232.65.218:5555): "
        ).strip()

    entries = extract_instruction_entries(csv_path, only_improving=only_improving)
    total_instructions = sum(len(entry["instructions"]) for entry in entries)
    improving_rows = sum(1 for entry in entries if entry["improved"])
    print(
        f"Loaded {total_instructions} instructions from "
        f"{len(entries)} CSV rows ({improving_rows} originally improved coverage)."
    )

    g_coverage = GlobalCoverageDatabase()

    with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
        for entry in entries:
            print(
                f"Replaying row {entry['total_message']} "
                f"({entry['coverage_before']} -> {entry['coverage_after']}), "
                f"{len(entry['instructions'])} instructions"
            )
            for instruction in entry["instructions"]:
                coverage = stimulus_sender.send_stimulus(instruction)
                g_coverage.set(coverage)

        coverage = stimulus_sender.send_stimulus(None)
        g_coverage.set(coverage)

    coverage_plan = {
        k: v for (k, v) in g_coverage.get_coverage_plan().items() if v > 0
    }
    print(
        f"Finished replay with hits: {coverage_plan}\n"
        f"Coverage: {g_coverage.get_coverage_rate()}\n"
    )


def replay_coverage_improving_stimuli(csv_path=None, server_ip_port=None):
    replay_csv_stimuli(csv_path, server_ip_port, only_improving=True)


def random_experiment():
    print("Running random experiment on ID...")

    server_ip_port = input(
        "Please enter server's IP and port (e.g. 127.0.0.1:5050, 128.232.65.218:5555): "
    )

    CYCLES = 5000000
    agent = ConstrainedRandomAgent(total_cycle=CYCLES, seed=datetime.now().timestamp())
    # agent = RandomAgent(total_cycle=CYCLES, seed=datetime.now().timestamp())

    # run test
    g_dut_state = GlobalDUTState()
    g_coverage = GlobalCoverageDatabase()

    with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
        while not agent.end_simulation(g_dut_state, g_coverage):
            stimulus = agent.generate_next_value(g_dut_state, g_coverage)
            coverage = stimulus_sender.send_stimulus(stimulus)
            g_coverage.set(coverage)

        coverage = stimulus_sender.send_stimulus(None)

        g_coverage.set(coverage)
        coverage_plan = {
            k: v for (k, v) in g_coverage.get_coverage_plan().items() if v > 0
        }
        print(
            f"Finished random agent on Ibex with {CYCLES} cycles \n"
            f"Hits: {coverage_plan}, \n"
            f"Coverage rate: {g_coverage.get_coverage_rate()}\n"
            f"Coverage rate: {g_coverage.get_coverage_rate()[0] / g_coverage.get_coverage_rate()[1] * 100}\n"
        )


def build_llm_agent(log_name_suffix=""):
    prompt_generator = TemplatePromptGenerator4ID1(
        bin_descr_path=os.path.join(directory, "../examples_ID/bins_description.txt"),
        sampling_missed_bins_method="IDAdaNew",
    )

    stimulus_generator = ChatGPT(
        system_prompt=prompt_generator.generate_system_prompt(),
        best_iter_buffer_resetting="STABLE",
        compress_msg_algo="best 3",
        prioritise_harder_bins=False,
    )
    extractor = DumbExtractor()
    stimulus_filter = Filter(0x0, 0xFFFFFFFF)

    prefix = "./logs/"
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
        dialog_bound=300,
        rst_plan=rst_plan_High_Tolerance,
    )


def print_coverage_summary(label, g_coverage):
    cov_hit, cov_total = g_coverage.get_coverage_rate()
    cov_percent = (cov_hit / cov_total * 100) if cov_total > 0 else 0
    print(f"{label}: ({cov_hit}/{cov_total}) - {cov_percent:.2f}%")


def hybrid_experiment(random_cycles=10000, server_ip_port=None):
    print("Running hybrid random-then-LLM experiment on ID...")
    print(f"Random warm-up cycles: {random_cycles}\n")

    if random_cycles <= 0:
        raise ValueError("random_cycles must be positive")

    if server_ip_port is None:
        server_ip_port = input(
            "Please enter server's IP and port (e.g. 127.0.0.1:5050, 128.232.65.218:5555): "
        )

    random_agent = ConstrainedRandomAgent(
        total_cycle=random_cycles, seed=datetime.now().timestamp()
    )

    g_dut_state = GlobalDUTState()
    g_coverage = GlobalCoverageDatabase()

    with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
        print("Starting random warm-up...")
        while not random_agent.end_simulation(g_dut_state, g_coverage):
            stimulus = random_agent.generate_next_value(g_dut_state, g_coverage)
            coverage = stimulus_sender.send_stimulus(stimulus)
            g_coverage.set(coverage)

        print_coverage_summary("Coverage after random warm-up", g_coverage)

        cov_hit, cov_total = g_coverage.get_coverage_rate()
        if cov_hit < cov_total:
            print("Switching to LLM-guided stimulus generation...\n")
            llm_agent = build_llm_agent(log_name_suffix="_hybrid")
            llm_agent.start_iterative_from_existing_coverage()

            while not llm_agent.end_simulation(g_dut_state, g_coverage):
                stimulus = llm_agent.generate_next_value(g_dut_state, g_coverage)
                coverage = stimulus_sender.send_stimulus(stimulus)
                g_coverage.set(coverage)
        else:
            print("Random warm-up reached full coverage; skipping LLM phase.\n")

        coverage = stimulus_sender.send_stimulus(None)
        g_coverage.set(coverage)

    coverage_plan = {
        k: v for (k, v) in g_coverage.get_coverage_plan().items() if v > 0
    }
    print(
        f"Finished hybrid experiment with hits: {coverage_plan}\n"
        f"Coverage: {g_coverage.get_coverage_rate()}\n"
    )


def build_policy_refiner(model_name):
    system_prompt = (
        "You are a hardware verification assistant. You update only a small "
        "Python constrained-random policy region for a RISC-V decoder test. "
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
    batch_cycles=1000000,
    max_batches=5,
    server_ip_port=None,
    llm_model="gpt-5.4-mini",
):
    print("Running LLM-guided CRT policy experiment on ID...")
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

    base_policy_path = os.path.join(directory, "../agents/llm_crt_policy.py")
    current_policy_path = os.path.join(log_dir, "policy_iter_0.py")
    policy_log_path = os.path.join(log_dir, "policy_refinement.txt")
    trace_path = os.path.join(log_dir, "stimulus_trace.csv")
    shutil.copyfile(base_policy_path, current_policy_path)

    policy_refiner = None
    g_dut_state = GlobalDUTState()
    g_coverage = GlobalCoverageDatabase()
    trace_file = open(trace_path, "w", newline="", encoding="UTF8")
    trace_writer = csv.DictWriter(
        trace_file,
        fieldnames=[
            "batch",
            "cycle",
            "policy",
            "instruction",
            "coverage_hit",
            "coverage_total",
            "new_bins",
        ],
    )
    trace_writer.writeheader()

    with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
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

            while not agent.end_simulation(g_dut_state, g_coverage):
                stimulus = agent.generate_next_value(g_dut_state, g_coverage)
                coverage_before = _decoder_coverage_plan_or_empty(g_coverage)
                coverage = stimulus_sender.send_stimulus(stimulus)
                g_coverage.set(coverage)
                write_decoder_crt_trace_row(
                    trace_writer,
                    batch_idx,
                    agent.current_cycle,
                    current_policy_path,
                    stimulus,
                    coverage_before,
                    g_coverage,
                )
                trace_file.flush()

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

            prompt = build_policy_refinement_prompt(
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
            region = None
            for retry_idx in range(3):
                response = ""
                try:
                    response, _ = policy_refiner(prompt)
                    region = write_updated_policy(
                        current_policy_path,
                        next_policy_path,
                        response,
                    )
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

            if region is None:
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
                response,
                region,
            )
            current_policy_path = next_policy_path
            print(f"Accepted updated policy: {current_policy_path}")

        coverage = stimulus_sender.send_stimulus(None)
        g_coverage.set(coverage)

    trace_file.close()

    coverage_plan = {
        k: v for (k, v) in g_coverage.get_coverage_plan().items() if v > 0
    }
    print(
        f"\nFinished LLM-guided CRT experiment with hits: {coverage_plan}\n"
        f"Coverage: {g_coverage.get_coverage_rate()}\n"
        f"Policy logs: {policy_log_path}\n"
        f"Stimulus trace: {trace_path}\n"
    )


def main_llama2():
    print("Running main_llama2 experiment on ID...")

    from models.llm_llama2 import Llama2

    server_ip_port = input(
        "Please enter server's IP and port (e.g. 127.0.0.1:5050, 128.232.65.218:5555): "
    )

    # build components
    prompt_generator = TemplatePromptGenerator4ID2(
        bin_descr_path="../examples_ID/bins_description.txt",
        sampling_missed_bins_method="IDNewest",
    )

    stimulus_generator = Llama2(
        system_prompt=prompt_generator.generate_system_prompt(),
        best_iter_buffer_resetting="STABLE",
        compress_msg_algo="best 3",
        prioritise_harder_bins=True,
    )
    print("Llama2 successfully built")

    extractor = DumbExtractor()
    stimulus_filter = Filter(0x0, 0xFFFFFFFF)

    # build loggers
    prefix = "./logs/"
    t = datetime.now()
    t = t.strftime("%Y%m%d_%H%M%S")
    logger_txt = TXTLogger(f"{prefix}{t}.txt")
    logger_csv = CSVLogger(f"{prefix}{t}.csv")

    # create agent
    agent = LLMAgent(
        prompt_generator,
        stimulus_generator,
        extractor,
        stimulus_filter,
        [logger_txt, logger_csv],
        dialog_bound=1000,
        rst_plan=rst_plan_Normal_Tolerance,
    )
    print("Agent successfully built\n")

    # run test
    g_dut_state = GlobalDUTState()
    g_coverage = GlobalCoverageDatabase()

    with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
        while not agent.end_simulation(g_dut_state, g_coverage):
            stimulus = agent.generate_next_value(g_dut_state, g_coverage)
            coverage = stimulus_sender.send_stimulus(stimulus)
            g_coverage.set(coverage)

        coverage = stimulus_sender.send_stimulus(None)

        g_coverage.set(coverage)
        coverage_plan = {
            k: v for (k, v) in g_coverage.get_coverage_plan().items() if v > 0
        }
        print(
            f"Finished with hits: {coverage_plan}\n"
            f"Coverage: {g_coverage.get_coverage_rate()}\n"
        )


def main():
    print("Running main experiment on ID...")

    server_ip_port = input(
        "Please enter server's IP and port (e.g. 127.0.0.1:5050, 128.232.65.218:5555): "
    )

    agent = build_llm_agent()
    print("Agent successfully built\n")

    # agent = RandomAgent(3000000)

    # run test
    g_dut_state = GlobalDUTState()
    g_coverage = GlobalCoverageDatabase()

    with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
        while not agent.end_simulation(g_dut_state, g_coverage):
            stimulus = agent.generate_next_value(g_dut_state, g_coverage)
            coverage = stimulus_sender.send_stimulus(stimulus)
            g_coverage.set(coverage)

        coverage = stimulus_sender.send_stimulus(None)
        # coverage.output_coverage()

        g_coverage.set(coverage)
        # print(f"Full coverage plan: {g_coverage.get_coverage_plan()}\n")
        coverage_plan = {
            k: v for (k, v) in g_coverage.get_coverage_plan().items() if v > 0
        }
        print(
            f"Finished with hits: {coverage_plan}\n"
            f"Coverage: {g_coverage.get_coverage_rate()}\n"
        )
        # print(
        #     f"Finished at dialog #{agent.dialog_index}, message #{agent.msg_index}, \n"
        #     f"with total {agent.total_msg_cnt} messages \n"
        #     f"Hits: {coverage_plan}, \n"
        #     f"Coverage rate: {g_coverage.get_coverage_rate()}\n"
        # )


def budget_experiment():
    print("Running budget experiment on ID...")

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
        prompt_generator = TemplatePromptGenerator4ID2(
            bin_descr_path="../examples_ID/bins_description.txt",
            sampling_missed_bins_method="IDAdaNew",
        )

        # stimulus_generator = Llama2(system_prompt=prompt_generator.generate_system_prompt())
        # print('Llama2 successfully built')
        stimulus_generator = ChatGPT(
            system_prompt=prompt_generator.generate_system_prompt(),
            best_iter_buffer_resetting="STABLE",
            compress_msg_algo="best 3",
            prioritise_harder_bins=True,
        )
        extractor = DumbExtractor()
        stimulus_filter = Filter(0x0, 0xFFFFFFFF)

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
            rst_plan=rst_plan_Coverage_RateBased_Tolerance,
            token_budget=BUDGET,
        )
        print("Agent successfully built\n")

        # run test
        g_dut_state = GlobalDUTState()
        g_coverage = GlobalCoverageDatabase()

        with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
            while not agent.end_simulation(g_dut_state, g_coverage):
                stimulus = agent.generate_next_value(g_dut_state, g_coverage)
                coverage = stimulus_sender.send_stimulus(stimulus)
                g_coverage.set(coverage)

            coverage = stimulus_sender.send_stimulus(None)
            g_coverage.set(coverage)

            data.append(
                [
                    trial_cnt,
                    agent.total_msg_cnt,
                    BUDGET.init_budget - BUDGET.budget,
                    g_coverage.get_coverage_rate()[0],
                    str(g_coverage.get_coverage_plan()),
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
        choices=[
            "random",
            "main",
            "hybrid",
            "llm_crt",
            "budget",
            "replay_csv",
            "replay_csv_all",
        ],
        help="Experiment mode to run",
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        help="CSVLogger output to replay in replay_csv mode",
    )
    parser.add_argument(
        "--server",
        dest="server_ip_port",
        help="Decoder server IP and port, e.g. 127.0.0.1:5050",
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
    elif args.mode == "replay_csv":
        replay_coverage_improving_stimuli(args.csv_path, args.server_ip_port)
    elif args.mode == "replay_csv_all":
        replay_csv_stimuli(args.csv_path, args.server_ip_port, only_improving=False)
