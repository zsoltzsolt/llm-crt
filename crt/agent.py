# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Agent for executing editable Python CRT policies."""

import random
from typing import Any
from crt.policy_manager import load_policy_module


class EditableCRTAgent:
    """Agent that drives simulation using a local Python policy file."""

    def __init__(self, policy_path: str, total_cycle: int = 300000, seed: float = 0):
        self.policy_path = policy_path
        self.total_cycle = total_cycle
        self.current_cycle = 0
        self.rng = random.Random(seed)
        self.policy_module = load_policy_module(policy_path)

    def end_simulation(self, dut_state: Any, coverage_database: Any) -> bool:
        return not (self.current_cycle < self.total_cycle)

    def reset(self) -> None:
        self.current_cycle = 0

    def generate_next_value(self, dut_state: Any, coverage_database: Any) -> Any:
        if self.current_cycle % 10000 == 0 and self.current_cycle > 0:
            print(f"Generated {self.current_cycle} CRT stimuli\n")

        self.current_cycle += 1

        if coverage_database is None:
            coverage = {}
        elif hasattr(coverage_database, "get_coverage_plan"):
            plan = coverage_database.get_coverage_plan()
            coverage = {} if plan is None else plan
        elif hasattr(coverage_database, "get_coverage_dict"):
            coverage = coverage_database.get_coverage_dict()
        elif isinstance(coverage_database, dict):
            coverage = coverage_database
        else:
            coverage = {}

        return self.policy_module.generate_instruction(coverage, self.rng)
