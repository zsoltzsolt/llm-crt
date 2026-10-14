# Copyright lowRISC contributors.
# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

from typing import Optional

from designs.ibex_cpu_l1.instructions import (
    BRANCHES,
    branch_taken,
    decode,
    insn_bin,
    sample_coverage,
    sample_hazard,
)
from designs.ibex_cpu_l1.shared_types import CoverageDatabase


class InstructionMonitor:
    """Samples RVFI retirements and records L1 coverage.

    Only instructions retired from the current stimulus block's addresses are
    counted, so harness code (park loop, trap handler, recovery jumps) never
    contributes coverage.
    """

    coverage_db_cls = CoverageDatabase

    def __init__(self, dut):
        top = dut.u_top
        self.rvfi_valid = top.rvfi_valid
        self.rvfi_order = top.rvfi_order
        self.rvfi_trap = top.rvfi_trap
        self.rvfi_pc = top.rvfi_pc_rdata
        self.rvfi_insn = top.rvfi_insn
        self.rvfi_rs1_rdata = top.rvfi_rs1_rdata
        self.rvfi_rs2_rdata = top.rvfi_rs2_rdata

        self.coverage_db = self.coverage_db_cls()
        self.stimulus_pcs: frozenset = frozenset()
        self.last_order: Optional[int] = None
        self.last_pc: Optional[int] = None
        self.last_insn: Optional[int] = None
        # Previous retired stimulus instruction, carried across blocks for
        # RAW-hazard sampling (as in L0).
        self.prev_decoded = None

    def set_stimulus_pcs(self, pcs) -> None:
        self.stimulus_pcs = frozenset(pcs)

    def sample(self) -> Optional[tuple[int, int]]:
        """Call once per clock. Returns (pc, insn) of a new retirement, else None."""
        try:
            if not int(self.rvfi_valid.value):
                return None
            order = int(self.rvfi_order.value)
            if order == self.last_order:
                return None
            self.last_order = order
            pc = int(self.rvfi_pc.value)
            insn = int(self.rvfi_insn.value)
            trapped = bool(int(self.rvfi_trap.value))
        except ValueError:
            # X/Z values around reset
            return None

        self._observe(pc, insn, trapped)
        if pc not in self.stimulus_pcs:
            self._on_uncounted_retirement()
        elif trapped:
            self._record_trapped(pc, insn)
        else:
            self._record(pc, insn)
        return pc, insn

    def _record(self, pc: int, insn: int) -> None:
        d = decode(insn)
        if d is None:
            self._on_uncounted_retirement()
            return

        taken = None
        if d.instr in BRANCHES:
            taken = branch_taken(
                d.instr,
                int(self.rvfi_rs1_rdata.value),
                int(self.rvfi_rs2_rdata.value),
            )

        bins = self.coverage_db.bins
        for cov in sample_coverage(d, taken):
            bins[insn_bin(d.instr, cov)] += 1
        hazard = sample_hazard(self.prev_decoded, d)
        if hazard is not None:
            bins[hazard] += 1
        self._record_extra(d, taken)

        self.prev_decoded = d
        self.last_pc = pc
        self.last_insn = insn

    # Extension hooks for higher coverage levels.
    def _observe(self, pc: int, insn: int, trapped: bool) -> None:
        """Called for every retirement, stimulus or not."""

    def _record_trapped(self, pc: int, insn: int) -> None:
        """A stimulus instruction that raised an exception (not counted in L1/L2)."""
        self._on_uncounted_retirement()

    def _record_extra(self, d, taken) -> None:
        pass

    def _on_uncounted_retirement(self) -> None:
        pass
