# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Unified batch runner for LLM-driven CRT experiments."""

import csv
import json
import os
import shutil
import time
from contextlib import closing
from datetime import datetime
from typing import Optional

import zmq

from crt.agent import EditableCRTAgent
from crt.base_target import DUTTarget
from crt.llm_client import LLMClient
from crt.policy_manager import (
    extract_policy_regions,
    write_updated_policy,
)
from global_shared_types import GlobalCoverageDatabase, GlobalDUTState


class StimulusSender:
    """ZMQ REQ/REP channel to cocotb testbench."""

    def __init__(self, zmq_addr: str, timeout_ms: int = 15000):
        self.zmq_addr = zmq_addr
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.setsockopt(zmq.RCVTIMEO, timeout_ms)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.connect(zmq_addr)

    def send_stimulus(self, stimulus_obj):
        try:
            self.socket.send_pyobj(stimulus_obj)
            return self.socket.recv_pyobj()
        except zmq.Again:
            raise RuntimeError(
                f"Timeout waiting for response from testbench server at {self.zmq_addr}.\n"
                "Please make sure the testbench server is running (e.g. run 'make' inside the design folder) "
                "and entered the correct port number."
            )

    def close(self):
        if self.socket:
            self.socket.close()


def append_policy_log(log_path, iteration, coverage_rate, prompt, response, region):
    with open(log_path, "a", encoding="UTF8") as f:
        f.write(f"\n===== ITERATION {iteration} =====\n")
        f.write(f"Coverage before refinement: {coverage_rate}\n\n")
        f.write("----- PROMPT -----\n")
        f.write(prompt)
        f.write("\n\n----- RESPONSE -----\n")
        f.write(response)
        f.write("\n\n----- ACCEPTED REGION -----\n")
        f.write(region)
        f.write("\n")


def append_policy_rejection_log(
    log_path,
    iteration,
    coverage_rate,
    prompt,
    rejected_attempts,
    current_policy_path,
):
    with open(log_path, "a", encoding="UTF8") as f:
        f.write(f"\n===== ITERATION {iteration} REJECTED =====\n")
        f.write(f"Coverage before refinement: {coverage_rate}\n")
        f.write(f"Continuing with existing policy: {current_policy_path}\n\n")
        f.write("----- FINAL PROMPT -----\n")
        f.write(prompt)
        for attempt_idx, response, error in rejected_attempts:
            f.write(f"\n\n----- REJECTED RESPONSE {attempt_idx} -----\n")
            f.write(response)
            f.write("\n\n----- VALIDATION ERROR -----\n")
            f.write(f"{type(error).__name__}: {error}\n")


def _get_coverage_plan_dict(g_coverage):
    if g_coverage.get() is None:
        return {}
    return g_coverage.get_coverage_plan()


def run_crt_experiment(
    target: DUTTarget,
    batch_cycles: int = 100000,
    max_batches: int = 5,
    stagnation_window: int = 10000,
    min_batch_cycles: int = 10000,
    dynamic_cycles: bool = True,
    max_batch_cycles: int = 500000,
    server_ip_port: Optional[str] = None,
    provider: str = "openai",
    llm_model: str = "gpt-4o-mini",
    output_dir: Optional[str] = None,
):
    """Run an LLM-driven CRT experiment against a DUTTarget with dynamic continuation & early stopping."""
    print(f"Running LLM-guided CRT experiment on {target.name}...")
    print(f"CRT initial target cycles: {batch_cycles}")
    if dynamic_cycles:
        print(f"Dynamic cycles enabled: continues execution as long as coverage improves (stagnation window = {stagnation_window} cycles, max cap = {max_batch_cycles} cycles)")
    else:
        print(f"Fixed batch cycles: {batch_cycles} (early stopping window = {stagnation_window} cycles)")
    print(f"Max batches: {max_batches}")
    print(f"LLM refiner: provider={provider}, model={llm_model}\n")

    if batch_cycles <= 0:
        raise ValueError("batch_cycles must be positive")
    if max_batches <= 0:
        raise ValueError("max_batches must be positive")

    if server_ip_port is None:
        server_ip_port = input(
            "Please enter server's IP and port (e.g. 127.0.0.1:5050): "
        ).strip()

    start_time = time.time()
    t = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = output_dir or os.path.join("./logs", f"{t}_{target.name}_crt")
    os.makedirs(log_dir, exist_ok=True)

    base_policy_path = target.base_policy_path
    current_policy_path = os.path.join(log_dir, "policy_iter_0.py")
    policy_log_path = os.path.join(log_dir, "policy_refinement.txt")
    trace_path = os.path.join(log_dir, "stimulus_trace.csv")
    summary_path = os.path.join(log_dir, "batch_summary.json")

    shutil.copyfile(base_policy_path, current_policy_path)

    policy_refiner = None
    g_dut_state = GlobalDUTState()
    g_coverage = GlobalCoverageDatabase()

    total_cycles_executed = 0
    max_possible_cycles = max_batches * (max_batch_cycles if dynamic_cycles else batch_cycles)
    batch_stats = []

    with open(trace_path, "w", newline="", encoding="UTF8") as trace_file:
        trace_writer = csv.DictWriter(
            trace_file, fieldnames=target.get_trace_fieldnames()
        )
        trace_writer.writeheader()

        with closing(StimulusSender(f"tcp://{server_ip_port}")) as stimulus_sender:
            for batch_idx in range(max_batches):
                print(
                    f"\n===== CRT batch {batch_idx + 1}/{max_batches} "
                    f"using {current_policy_path} ====="
                )
                agent_cap = max_batch_cycles if dynamic_cycles else batch_cycles
                agent = EditableCRTAgent(
                    current_policy_path,
                    total_cycle=agent_cap,
                    seed=datetime.now().timestamp() + batch_idx,
                )

                cov_before = _get_coverage_plan_dict(g_coverage)
                prev_hit, _ = g_coverage.get_coverage_rate()
                cycles_since_last_hit = 0
                early_stopped = False
                batch_start_cycles = total_cycles_executed
                extended_past_target = False

                while not agent.end_simulation(g_dut_state, g_coverage):
                    raw_instruction = agent.generate_next_value(g_dut_state, g_coverage)
                    stimulus = target.format_stimulus(raw_instruction)
                    tb_reply = stimulus_sender.send_stimulus(stimulus)
                    dut_state, new_coverage = target.parse_response(tb_reply)

                    if dut_state is not None:
                        g_dut_state.set(dut_state)
                    target.merge_coverage(g_coverage, new_coverage)

                    cov_after = _get_coverage_plan_dict(g_coverage)
                    cov_hit, cov_total = g_coverage.get_coverage_rate()
                    total_cycles_executed += 1

                    if cov_hit > prev_hit:
                        cycles_since_last_hit = 0
                        prev_hit = cov_hit
                        if dynamic_cycles and agent.current_cycle > batch_cycles and not extended_past_target:
                            print(f"  [Dynamic Extension] Policy is making improvements! Cycle {agent.current_cycle} > target {batch_cycles}. Continuing simulation...")
                            extended_past_target = True
                    else:
                        cycles_since_last_hit += 1

                    if agent.current_cycle % 5000 == 0 or agent.current_cycle == 1:
                        pct = (cov_hit / cov_total * 100) if cov_total > 0 else 0.0
                        print(f"  Batch {batch_idx + 1} | Cycle {agent.current_cycle} | Coverage: {cov_hit}/{cov_total} ({pct:.1f}%)")

                    target.write_trace_row(
                        trace_writer,
                        batch_idx,
                        agent.current_cycle,
                        current_policy_path,
                        raw_instruction,
                        dut_state,
                        cov_before,
                        cov_after,
                        cov_hit,
                        cov_total,
                    )
                    trace_file.flush()
                    cov_before = cov_after

                    if cov_total > 0 and cov_hit == cov_total:
                        print(
                            f"Full coverage reached at cycle {agent.current_cycle}; ending batch."
                        )
                        early_stopped = True
                        break

                    if (
                        stagnation_window > 0
                        and agent.current_cycle >= min_batch_cycles
                        and cycles_since_last_hit >= stagnation_window
                    ):
                        print(
                            f"  [Dynamic Stagnation Stop] Coverage stagnated (no new hits for {stagnation_window} cycles). "
                            f"Ending batch {batch_idx + 1} at cycle {agent.current_cycle}."
                        )
                        early_stopped = True
                        break

                cov_hit, cov_total = g_coverage.get_coverage_rate()
                cov_percent = (cov_hit / cov_total * 100) if cov_total > 0 else 0
                cycles_in_batch = total_cycles_executed - batch_start_cycles
                batch_stats.append({
                    "batch": batch_idx + 1,
                    "cycles_run": cycles_in_batch,
                    "max_cycles_budget": batch_cycles,
                    "early_stopped": early_stopped,
                    "cov_hit": cov_hit,
                    "cov_total": cov_total,
                    "cov_percent": cov_percent,
                    "policy": current_policy_path,
                })

                print(
                    f"Coverage after batch {batch_idx + 1}: "
                    f"({cov_hit}/{cov_total}) - {cov_percent:.2f}% "
                    f"(ran {cycles_in_batch}/{batch_cycles} cycles)"
                )

                if cov_hit == cov_total:
                    print("Full coverage reached; stopping refinement.")
                    break
                if batch_idx == max_batches - 1:
                    print("Reached max CRT batches; stopping refinement.")
                    break

                if policy_refiner is None:
                    policy_refiner = LLMClient(
                        provider=provider,
                        model_name=llm_model,
                    )

                with open(current_policy_path, "r", encoding="UTF8") as f:
                    current_policy_text = f.read()
                prev_helper, prev_body = extract_policy_regions(current_policy_text)

                prompt = target.build_refinement_prompt(
                    g_coverage.get_coverage_plan(),
                    prev_helper,
                    prev_body,
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
                        target.validate_policy(next_policy_path, require_generated_helpers=True)
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
                        last_error = e
                        rejected_attempts.append((retry_idx + 1, response, e))
                        print(f"LLM query error ({e}); continuing with existing policy.")
                        break

                if accepted_region is None:
                    if os.path.exists(next_policy_path):
                        os.replace(
                            next_policy_path,
                            os.path.join(log_dir, f"rejected_iter_{batch_idx + 1}.py"),
                        )
                    append_policy_rejection_log(
                        policy_log_path,
                        batch_idx + 1,
                        (cov_hit, cov_total),
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
                    (cov_hit, cov_total),
                    prompt,
                    accepted_response,
                    accepted_region,
                )
                current_policy_path = next_policy_path
                print(f"Accepted updated policy: {current_policy_path}")

            finish_stimulus = target.format_stimulus(None)
            finish_reply = stimulus_sender.send_stimulus(finish_stimulus)
            _, final_cov = target.parse_response(finish_reply)
            target.merge_coverage(g_coverage, final_cov)

    total_wall_time = time.time() - start_time
    cov_hit, cov_total = g_coverage.get_coverage_rate()
    cycles_saved = max_possible_cycles - total_cycles_executed
    percent_saved = (cycles_saved / max_possible_cycles * 100) if max_possible_cycles > 0 else 0.0

    summary_data = {
        "design": target.name,
        "provider": provider,
        "model": llm_model,
        "max_batches": max_batches,
        "configured_batch_cycles": batch_cycles,
        "stagnation_window": stagnation_window,
        "min_batch_cycles": min_batch_cycles,
        "total_cycles_executed": total_cycles_executed,
        "max_possible_cycles": max_possible_cycles,
        "cycles_saved": cycles_saved,
        "percent_cycles_saved": round(percent_saved, 2),
        "wall_time_seconds": round(total_wall_time, 2),
        "final_coverage_hit": cov_hit,
        "final_coverage_total": cov_total,
        "final_coverage_percent": round((cov_hit / cov_total * 100) if cov_total > 0 else 0.0, 2),
        "batches": batch_stats,
    }

    with open(summary_path, "w", encoding="UTF8") as sf:
        json.dump(summary_data, sf, indent=2)

    print(
        f"\nFinished CRT experiment on {target.name}.\n"
        f"Final Coverage: {g_coverage.get_coverage_rate()} ({summary_data['final_coverage_percent']}%)\n"
        f"Total Cycles Executed: {total_cycles_executed}/{max_possible_cycles} (Saved {cycles_saved} cycles, {percent_saved:.1f}%)\n"
        f"Wall-clock Time: {total_wall_time:.2f}s\n"
        f"Summary JSON: {summary_path}\n"
        f"Policy log: {policy_log_path}\n"
        f"Stimulus trace: {trace_path}\n"
    )
    return g_coverage
