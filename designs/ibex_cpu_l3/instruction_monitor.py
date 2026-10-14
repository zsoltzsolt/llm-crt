# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

from designs.ibex_cpu_l2.instruction_monitor import InstructionMonitor as L2InstructionMonitor
from designs.ibex_cpu_l3.coverage import (
    PRIV_U,
    decode_system,
    sample_operands,
    sample_system,
    sample_trap,
)
from designs.ibex_cpu_l3.shared_types import CoverageDatabase


class InstructionMonitor(L2InstructionMonitor):
    """L2 monitor plus L3 bins: SYSTEM/FENCE instructions, exceptions raised by
    stimulus instructions, privilege transitions, and operand corner cases."""

    coverage_db_cls = CoverageDatabase

    def __init__(self, dut):
        super().__init__(dut)
        top = dut.u_top
        self.rvfi_mode = top.rvfi_mode
        self.rvfi_rd_wdata = top.rvfi_rd_wdata
        # Set when a stimulus MRET retires; the next retirement's privilege
        # mode tells where it returned to.
        self.mret_pending = False

    def _observe(self, pc, insn, trapped) -> None:
        if self.mret_pending:
            self.mret_pending = False
            mode = int(self.rvfi_mode.value)
            self.coverage_db.bins["mret_to_u" if mode == PRIV_U else "mret_to_m"] += 1

    def _record(self, pc, insn) -> None:
        s = decode_system(insn)
        if s is None:
            super()._record(pc, insn)
            return
        bins = self.coverage_db.bins
        for name in sample_system(s):
            bins[name] += 1
        if s.kind == "mret":
            self.mret_pending = True
        # SYSTEM/FENCE instructions break both the L0 hazard and L2 block chains.
        self.prev_decoded = None
        self._on_uncounted_retirement()
        self.last_pc = pc
        self.last_insn = insn

    def _record_trapped(self, pc, insn) -> None:
        bins = self.coverage_db.bins
        mode = int(self.rvfi_mode.value)
        for name in sample_trap(insn, mode, int(self.rvfi_rs1_rdata.value)):
            bins[name] += 1
        self.last_pc = pc
        self.last_insn = insn
        super()._record_trapped(pc, insn)

    def _record_extra(self, d, taken) -> None:
        super()._record_extra(d, taken)
        bins = self.coverage_db.bins
        for name in sample_operands(
            d,
            int(self.rvfi_rs1_rdata.value),
            int(self.rvfi_rs2_rdata.value),
            int(self.rvfi_rd_wdata.value),
        ):
            bins[name] += 1
