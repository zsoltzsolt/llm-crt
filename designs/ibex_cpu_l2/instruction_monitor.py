# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

from designs.ibex_cpu_l1.instruction_monitor import InstructionMonitor as L1InstructionMonitor
from designs.ibex_cpu_l2.coverage import sample_alignment, sample_pair
from designs.ibex_cpu_l2.shared_types import CoverageDatabase


class InstructionMonitor(L1InstructionMonitor):
    """L1 monitor plus L2 sequence (G1/G3) and alignment (G2) bins.

    Sequence bins pair only instructions retired consecutively within the
    current block: any uncounted retirement (harness code, traps, unknown
    encodings) or the start of a new block breaks the chain.
    """

    coverage_db_cls = CoverageDatabase

    def __init__(self, dut):
        super().__init__(dut)
        self.block_prev = None
        self.block_prev_taken = None

    def set_stimulus_pcs(self, pcs) -> None:
        super().set_stimulus_pcs(pcs)
        self._on_uncounted_retirement()

    def _on_uncounted_retirement(self) -> None:
        self.block_prev = None
        self.block_prev_taken = None

    def _record_extra(self, d, taken) -> None:
        bins = self.coverage_db.bins
        for name in sample_pair(self.block_prev, self.block_prev_taken, d, taken):
            bins[name] += 1
        align = sample_alignment(d, int(self.rvfi_rs1_rdata.value))
        if align is not None:
            bins[align] += 1
        self.block_prev = d
        self.block_prev_taken = taken
