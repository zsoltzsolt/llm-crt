# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Ibex CPU coverage model, level L2 (= L1 + instruction sequences & alignment).

L2 adds 157 bins on top of the 313 L1 bins (470 total):

  G1 hazard types (101), riscv-dv cp_gpr_hazard style:
       <instr>_raw_hazard  instr reads a register (not x0) written by the
                           previous instruction; for the 29 L1 instructions
                           that read registers (L0 readers are already
                           covered by the L0 RAW crosses).
       <instr>_waw_hazard  instr writes rd (not x0) also written by the
                           previous instruction; 36 register writers.
       <instr>_war_hazard  instr writes rd (not x0) read by the previous
                           instruction; 36 register writers.
  G2 memory alignment (20):
       <instr>_offset_<k>  lw/sw/lh/lhu/sh with effective address [1:0] == k.
                           Misaligned accesses are split into two bus
                           transactions by the Ibex load/store unit.
  G3 back-to-back multicycle ops (36):
       <prev>-><curr>_back_to_back  over {load, store, mul, div, branch, jump};
                           "branch" means a TAKEN conditional branch.

G1 and G3 only count for instructions retired consecutively within the same
stimulus block (across blocks the harness park loop sits in between).
"""

from typing import Optional
import unittest

from designs.ibex_cpu_l1.instructions import (
    ALL_BINS as L1_BINS,
    BRANCHES,
    I_ALU,
    LOADS,
    MULDIV,
    STORES,
    Decoded,
    Instr,
    bin_level as l1_bin_level,
)

HAZARD_RAW_READERS = I_ALU + LOADS + BRANCHES + (Instr.JALR,) + MULDIV
NO_RD = frozenset(STORES + BRANCHES)
RD_WRITERS = tuple(i for i in Instr if i not in NO_RD)
ALIGN_INSTRS = (Instr.LW, Instr.SW, Instr.LH, Instr.LHU, Instr.SH)

MUL_OPS = (Instr.MUL, Instr.MULH, Instr.MULHSU, Instr.MULHU)
DIV_OPS = (Instr.DIV, Instr.DIVU, Instr.REM, Instr.REMU)
B2B_CATEGORIES = ("load", "store", "mul", "div", "branch", "jump")


def raw_bin(instr: Instr) -> str:
    return f"{instr.value}_raw_hazard"


def waw_bin(instr: Instr) -> str:
    return f"{instr.value}_waw_hazard"


def war_bin(instr: Instr) -> str:
    return f"{instr.value}_war_hazard"


def offset_bin(instr: Instr, offset: int) -> str:
    return f"{instr.value}_offset_{offset}"


def b2b_bin(prev: str, curr: str) -> str:
    return f"{prev}->{curr}_back_to_back"


def _build_l2_bins() -> list[str]:
    bins = [raw_bin(i) for i in HAZARD_RAW_READERS]
    bins += [waw_bin(i) for i in RD_WRITERS]
    bins += [war_bin(i) for i in RD_WRITERS]
    bins += [offset_bin(i, k) for i in ALIGN_INSTRS for k in range(4)]
    bins += [b2b_bin(p, c) for p in B2B_CATEGORIES for c in B2B_CATEGORIES]
    return bins


L2_NEW_BINS = _build_l2_bins()
ALL_BINS = L1_BINS + L2_NEW_BINS
_L2_BIN_SET = frozenset(L2_NEW_BINS)


def bin_level(bin_name: str) -> int:
    """0 = original L0 plan, 1 = added by L1, 2 = added by L2."""
    return 2 if bin_name in _L2_BIN_SET else l1_bin_level(bin_name)


def writes_rd(d: Decoded) -> bool:
    return d.instr not in NO_RD and d.rd != 0


def b2b_category(d: Decoded, taken: Optional[bool]) -> Optional[str]:
    if d.instr in LOADS:
        return "load"
    if d.instr in STORES:
        return "store"
    if d.instr in MUL_OPS:
        return "mul"
    if d.instr in DIV_OPS:
        return "div"
    if d.instr in BRANCHES:
        return "branch" if taken else None
    if d.instr in (Instr.JAL, Instr.JALR):
        return "jump"
    return None


def sample_pair(prev: Optional[Decoded], prev_taken: Optional[bool],
                curr: Decoded, curr_taken: Optional[bool]) -> list[str]:
    """G1 + G3 bins for two instructions retired back-to-back in one block."""
    if prev is None:
        return []
    out = []
    if writes_rd(prev) and curr.instr in HAZARD_RAW_READERS and prev.rd in curr.reads():
        out.append(raw_bin(curr.instr))
    if writes_rd(curr):
        if writes_rd(prev) and prev.rd == curr.rd:
            out.append(waw_bin(curr.instr))
        if curr.rd in prev.reads():
            out.append(war_bin(curr.instr))
    prev_cat = b2b_category(prev, prev_taken)
    curr_cat = b2b_category(curr, curr_taken)
    if prev_cat and curr_cat:
        out.append(b2b_bin(prev_cat, curr_cat))
    return out


def sample_alignment(d: Decoded, rs1_val: int) -> Optional[str]:
    if d.instr not in ALIGN_INSTRS:
        return None
    return offset_bin(d.instr, (rs1_val + d.imm) & 0x3)


class TestL2Model(unittest.TestCase):
    def test_bin_counts(self) -> None:
        self.assertEqual(len(HAZARD_RAW_READERS), 29)
        self.assertEqual(len(RD_WRITERS), 36)
        self.assertEqual(len(L2_NEW_BINS), 157)
        self.assertEqual(len(set(ALL_BINS)), 470)
        self.assertEqual(sum(bin_level(b) == 2 for b in ALL_BINS), 157)

    def test_pairs(self) -> None:
        from designs.ibex_cpu_l1.instructions import decode
        lw_x5 = decode(0x0002A283)      # lw x5, 0(x5)
        addi_x6_x5 = decode(0x00128313)  # addi x6, x5, 1
        addi_x5_x6 = decode(0x00130293)  # addi x5, x6, 1
        mul = decode(0x02B50533)         # mul x10, x10, x11
        div = decode(0x02B54533)         # div x10, x10, x11
        self.assertEqual(sample_pair(lw_x5, None, addi_x6_x5, None), ["addi_raw_hazard"])
        self.assertIn("addi_waw_hazard", sample_pair(lw_x5, None, addi_x5_x6, None))
        self.assertIn("addi_war_hazard", sample_pair(addi_x6_x5, None, addi_x5_x6, None))
        self.assertIn("mul->div_back_to_back", sample_pair(mul, None, div, None))
        self.assertIn("div_waw_hazard", sample_pair(mul, None, div, None))
        bne = decode(0xFE029CE3)
        self.assertIn("branch->mul_back_to_back", sample_pair(bne, True, mul, None))
        self.assertNotIn("branch->mul_back_to_back", sample_pair(bne, False, mul, None))
        x0_write = decode(0x00000013)    # addi x0, x0, 0
        self.assertEqual(sample_pair(x0_write, None, decode(0x00000013), None), [])

    def test_alignment(self) -> None:
        from designs.ibex_cpu_l1.instructions import decode
        lw = decode(0xFFF42503)          # lw x10, -1(x8)
        self.assertEqual(sample_alignment(lw, 0x1000), "lw_offset_3")
        self.assertIsNone(sample_alignment(decode(0x00040503), 0))  # lb


if __name__ == "__main__":
    unittest.main()
