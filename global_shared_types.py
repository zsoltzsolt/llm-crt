# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

import re
from typing import *

# This module is deliberately DUT-agnostic: it imports no DUT package.
#
# A DUT plugs in by satisfying two duck-typed contracts:
#   coverage database -- get_coverage_dict() -> {bin_name: hit_count}
#                        is_hard_bin(name) -> bool          (optional)
#   DUT state         -- last_pc / last_insn attributes     (optional,
#                        processor-like DUTs only)
#                        RESET_PC class attribute           (optional)
#
# Adding a DUT therefore requires no edit to this file.

# Reset vector reported for DUTs that do not declare RESET_PC (and for
# non-processor DUTs, which still have to report some PC to the prompts).
DEFAULT_RESET_PC = 0x00100080

# Weight applied to bins a DUT declares "hard" when scoring coverage.
HARD_BIN_WEIGHT = 2.5

# Cross-coverage bin names produced by the existing DUTs. Used only as a
# fallback when a coverage database does not declare is_hard_bin() -- e.g. when
# coverage arrives as a plain {bin_name: count} dict.
_CROSS_BIN_MARKERS = ("_x_", "raw_hazard", "->")


def _is_coverage_database(coverage) -> bool:
    return isinstance(coverage, dict) or hasattr(coverage, "get_coverage_dict")


def _default_is_hard_bin(bin_name: str) -> bool:
    if any(marker in bin_name for marker in _CROSS_BIN_MARKERS):
        return True
    return re.fullmatch(r"double_-?\d+_-?\d+", bin_name) is not None


class GlobalCoverageDatabase:
    def __init__(self, coverage=None):
        self._coverage_database = None
        self.set(coverage)

    def get(self):
        return self._coverage_database

    def set(self, coverage):
        if self._coverage_database is not None:
            assert isinstance(
                coverage, type(self._coverage_database)
            ), "New coverage is of different type of self._coverage_database."

        # DUT-agnostic contract: a coverage database is anything that can
        # render itself as {bin_name: hit_count}, or a plain dict already in
        # that form. Adding a DUT requires no change here.
        if coverage is not None and not _is_coverage_database(coverage):
            raise TypeError(
                f"Coverage of type {type(coverage)} is not supported: expected a "
                "dict of bin-name -> hit-count, or an object providing "
                "get_coverage_dict()."
            )

        self._coverage_database = coverage

    def get_coverage_plan(self) -> Dict[str, int]:
        cov = self._coverage_database
        if cov is None:
            return {}
        if isinstance(cov, dict):
            return cov
        if hasattr(cov, "get_coverage_dict"):
            return cov.get_coverage_dict()
        raise TypeError(
            f"coverage_database of type {type(cov)} not supported: expected a "
            "dict of bin-name -> hit-count, or an object providing "
            "get_coverage_dict()."
        )

    def get_coverage_rate(self) -> Tuple[int, int]:
        coverage = self.get_coverage_plan()
        coverage_hit = {k: v for (k, v) in coverage.items() if v > 0}
        return len(coverage_hit), len(coverage)

    def get_coverage_score(self, prioritise_harder_bins=True) -> float:
        coverage = [k for (k, v) in self.get_coverage_plan().items() if v > 0]
        if not prioritise_harder_bins:
            return len(coverage)
        # Which bins count as "hard" is a property of the DUT's coverage plan,
        # so each coverage database declares it via is_hard_bin().
        is_hard = getattr(self._coverage_database, "is_hard_bin", _default_is_hard_bin)
        return sum(HARD_BIN_WEIGHT if is_hard(k) else 1 for k in coverage)


class GlobalDUTState:
    def __init__(self):
        self._dut_state = None
        self.prev_valid_pc = None
        self.prev_valid_instr = None

    def get(self):
        return self._dut_state

    def set(self, dut_state):
        if self._dut_state is not None:
            assert isinstance(
                dut_state, type(self._dut_state)
            ), "New dut_state is of different type of self._dut_state."

        # DUT states are plain data carriers. Processor-like DUTs expose
        # last_pc/last_insn (and optionally RESET_PC); others expose whatever
        # their own agent needs. No per-DUT registration is required here.
        self._dut_state = dut_state

    def _reset_pc(self):
        # A processor DUT declares its own reset vector; Ibex's is the default
        # for historical reasons (DUTs without a PC still report one).
        return getattr(self._dut_state, "RESET_PC", DEFAULT_RESET_PC)

    def _reports_retirement(self) -> bool:
        return hasattr(self._dut_state, "last_pc") and hasattr(
            self._dut_state, "last_insn"
        )

    def get_pc(self):
        if self._reports_retirement():
            if self._dut_state.last_pc is None:
                # return "\"invalid\" since the CPU has tried to execute invalid instruction(s)."
                if self.prev_valid_pc is None:
                    self.prev_valid_pc = self._reset_pc()
                return hex(self.prev_valid_pc)
            self.prev_valid_pc = self._dut_state.last_pc
            return hex(self._dut_state.last_pc)
        else:
            self.prev_valid_pc = self._reset_pc()
            return hex(self.prev_valid_pc)

    def get_last_instr(self):
        if self._reports_retirement():
            if self._dut_state.last_insn is None:
                return (
                    None
                    if self.prev_valid_instr is None
                    else hex(self.prev_valid_instr)
                )
            self.prev_valid_instr = self._dut_state.last_insn
            return hex(self._dut_state.last_insn)
        else:
            return None


class Budget:
    budget: int
    init_budget: int
    total_budget: int

    def __init__(self, budget_per_trial, total_budget):
        self.init_budget = budget_per_trial
        self.total_budget = total_budget

    def no_budget(self):
        return self.budget <= 0
