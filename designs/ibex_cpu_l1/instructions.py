# Copyright lowRISC contributors.
# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Ibex CPU coverage model, level L1 (= L0 + RV32IM ISA breadth).

L0 is the original 196-bin plan from designs/ibex_cpu: 14 instructions with
seen/zero_dst/zero_src/same_src/br_* coverpoints plus 143 RAW-hazard crosses.
Its bin names and semantics are reproduced here unchanged.

L1 adds the remaining 31 non-system RV32IM instructions (117 bins):

    I-type ALU (9)   seen, zero_dst, zero_src
    LUI/AUIPC (2)    seen, zero_dst
    Loads (5)        seen, zero_dst, zero_src
    Branches (6)     seen, zero_src, same_src, br_backwards, br_forwards, not_taken
    JALR (1)         seen, zero_dst, zero_src
    M extension (8)  seen, zero_dst, zero_src, same_src

RAW-hazard crosses are kept to the L0 set (L0 writers x L0 readers); hazards
involving the new instructions are left for L2.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import unittest


class Cov(Enum):
    """Coverage Types

    Instruction Coverpoints:
      seen: Has been observed.
      zero_dst: The destination register has been `x0`.
      zero_src: A source register has been `x0`.
      same_src: Two source registers have been the same.
      br_backwards: Has caused a branch backwards.
      br_forwards: Has caused a branch forwards.
      not_taken: Conditional branch evaluated as not taken.

    Cross Instruction Coverpoints:
      raw_hazard: Reads from a register the previous instruction wrote to.
    """

    SEEN = "seen"
    ZERO_DST = "zero_dst"
    ZERO_SRC = "zero_src"
    SAME_SRC = "same_src"
    BR_BACKWARDS = "br_backwards"
    BR_FORWARDS = "br_forwards"
    NOT_TAKEN = "not_taken"
    RAW_HAZARD = "raw_hazard"


class Instr(Enum):
    # L0 instructions (order matches designs/ibex_cpu/instructions.py)
    ADD = "add"
    SUB = "sub"
    SLL = "sll"
    SLT = "slt"
    SLTU = "sltu"
    XOR = "xor"
    SRL = "srl"
    SRA = "sra"
    OR = "or"
    AND = "and"
    SB = "sb"
    SH = "sh"
    SW = "sw"
    JAL = "jal"
    # L1 additions
    ADDI = "addi"
    SLTI = "slti"
    SLTIU = "sltiu"
    XORI = "xori"
    ORI = "ori"
    ANDI = "andi"
    SLLI = "slli"
    SRLI = "srli"
    SRAI = "srai"
    LUI = "lui"
    AUIPC = "auipc"
    LB = "lb"
    LH = "lh"
    LW = "lw"
    LBU = "lbu"
    LHU = "lhu"
    BEQ = "beq"
    BNE = "bne"
    BLT = "blt"
    BGE = "bge"
    BLTU = "bltu"
    BGEU = "bgeu"
    JALR = "jalr"
    MUL = "mul"
    MULH = "mulh"
    MULHSU = "mulhsu"
    MULHU = "mulhu"
    DIV = "div"
    DIVU = "divu"
    REM = "rem"
    REMU = "remu"


R_ALU = (Instr.ADD, Instr.SUB, Instr.SLL, Instr.SLT, Instr.SLTU,
         Instr.XOR, Instr.SRL, Instr.SRA, Instr.OR, Instr.AND)
STORES = (Instr.SB, Instr.SH, Instr.SW)
I_ALU = (Instr.ADDI, Instr.SLTI, Instr.SLTIU, Instr.XORI, Instr.ORI,
         Instr.ANDI, Instr.SLLI, Instr.SRLI, Instr.SRAI)
UPPER = (Instr.LUI, Instr.AUIPC)
LOADS = (Instr.LB, Instr.LH, Instr.LW, Instr.LBU, Instr.LHU)
BRANCHES = (Instr.BEQ, Instr.BNE, Instr.BLT, Instr.BGE, Instr.BLTU, Instr.BGEU)
MULDIV = (Instr.MUL, Instr.MULH, Instr.MULHSU, Instr.MULHU,
          Instr.DIV, Instr.DIVU, Instr.REM, Instr.REMU)

L0_INSTRS = R_ALU + STORES + (Instr.JAL,)
L1_INSTRS = I_ALU + UPPER + LOADS + BRANCHES + (Instr.JALR,) + MULDIV

COVERPOINTS: dict[Instr, tuple[Cov, ...]] = {}
for _i in R_ALU + MULDIV:
    COVERPOINTS[_i] = (Cov.SEEN, Cov.ZERO_DST, Cov.ZERO_SRC, Cov.SAME_SRC)
for _i in STORES:
    COVERPOINTS[_i] = (Cov.SEEN, Cov.ZERO_SRC, Cov.SAME_SRC)
COVERPOINTS[Instr.JAL] = (Cov.SEEN, Cov.ZERO_DST, Cov.BR_BACKWARDS, Cov.BR_FORWARDS)
for _i in I_ALU + LOADS + (Instr.JALR,):
    COVERPOINTS[_i] = (Cov.SEEN, Cov.ZERO_DST, Cov.ZERO_SRC)
for _i in UPPER:
    COVERPOINTS[_i] = (Cov.SEEN, Cov.ZERO_DST)
for _i in BRANCHES:
    COVERPOINTS[_i] = (Cov.SEEN, Cov.ZERO_SRC, Cov.SAME_SRC,
                       Cov.BR_BACKWARDS, Cov.BR_FORWARDS, Cov.NOT_TAKEN)

# L0 RAW-hazard cross: previous R-type/JAL writes a register that the current
# R-type/store reads (rd == x0 included, as in L0).
HAZARD_WRITERS = R_ALU + (Instr.JAL,)
HAZARD_READERS = R_ALU + STORES


def insn_bin(instr: Instr, cov: Cov) -> str:
    return f"{instr.value}_{cov.value}"


def hazard_bin(prev: Instr, curr: Instr) -> str:
    return f"{prev.value}->{curr.value}_{Cov.RAW_HAZARD.value}"


def _build_bins() -> tuple[list[str], list[str]]:
    l0 = [insn_bin(i, c) for i in L0_INSTRS for c in COVERPOINTS[i]]
    l0 += [hazard_bin(p, c) for c in HAZARD_READERS for p in HAZARD_WRITERS]
    l1 = [insn_bin(i, c) for i in L1_INSTRS for c in COVERPOINTS[i]]
    return l0, l1


L0_BINS, L1_NEW_BINS = _build_bins()
ALL_BINS = L0_BINS + L1_NEW_BINS
_L0_BIN_SET = frozenset(L0_BINS)


def bin_level(bin_name: str) -> int:
    """0 for bins of the original L0 plan, 1 for bins added by L1."""
    return 0 if bin_name in _L0_BIN_SET else 1


def _sext(value: int, bits: int) -> int:
    return value - (1 << bits) if value >> (bits - 1) else value


@dataclass(frozen=True)
class Decoded:
    instr: Instr
    rd: int
    rs1: int
    rs2: int
    imm: int

    def reads(self) -> tuple[int, ...]:
        if self.instr in R_ALU + MULDIV + STORES + BRANCHES:
            return (self.rs1, self.rs2)
        if self.instr in I_ALU + LOADS + (Instr.JALR,):
            return (self.rs1,)
        return ()


_R_FUNCTS = {
    (0x00, 0): Instr.ADD, (0x20, 0): Instr.SUB, (0x00, 1): Instr.SLL,
    (0x00, 2): Instr.SLT, (0x00, 3): Instr.SLTU, (0x00, 4): Instr.XOR,
    (0x00, 5): Instr.SRL, (0x20, 5): Instr.SRA, (0x00, 6): Instr.OR,
    (0x00, 7): Instr.AND,
    (0x01, 0): Instr.MUL, (0x01, 1): Instr.MULH, (0x01, 2): Instr.MULHSU,
    (0x01, 3): Instr.MULHU, (0x01, 4): Instr.DIV, (0x01, 5): Instr.DIVU,
    (0x01, 6): Instr.REM, (0x01, 7): Instr.REMU,
}
_I_FUNCTS = {0: Instr.ADDI, 2: Instr.SLTI, 3: Instr.SLTIU,
             4: Instr.XORI, 6: Instr.ORI, 7: Instr.ANDI}
_SHIFT_FUNCTS = {(0x00, 1): Instr.SLLI, (0x00, 5): Instr.SRLI, (0x20, 5): Instr.SRAI}
_LOAD_FUNCTS = {0: Instr.LB, 1: Instr.LH, 2: Instr.LW, 4: Instr.LBU, 5: Instr.LHU}
_STORE_FUNCTS = {0: Instr.SB, 1: Instr.SH, 2: Instr.SW}
_BRANCH_FUNCTS = {0: Instr.BEQ, 1: Instr.BNE, 4: Instr.BLT,
                  5: Instr.BGE, 6: Instr.BLTU, 7: Instr.BGEU}


def decode(encoding: int) -> Optional[Decoded]:
    """Decode a 32-bit word into one of the 45 L1 instructions, else None."""
    e = encoding & 0xFFFFFFFF
    opcode = e & 0x7F
    rd = (e >> 7) & 0x1F
    funct3 = (e >> 12) & 0x7
    rs1 = (e >> 15) & 0x1F
    rs2 = (e >> 20) & 0x1F
    funct7 = (e >> 25) & 0x7F
    imm_i = _sext(e >> 20, 12)

    instr = None
    imm = 0
    if opcode == 0x33:
        instr = _R_FUNCTS.get((funct7, funct3))
    elif opcode == 0x13:
        if funct3 in (1, 5):
            instr = _SHIFT_FUNCTS.get((funct7, funct3))
            imm = rs2  # shamt
        else:
            instr = _I_FUNCTS.get(funct3)
            imm = imm_i
    elif opcode in (0x37, 0x17):
        instr = Instr.LUI if opcode == 0x37 else Instr.AUIPC
        imm = e & 0xFFFFF000
    elif opcode == 0x03:
        instr = _LOAD_FUNCTS.get(funct3)
        imm = imm_i
    elif opcode == 0x23:
        instr = _STORE_FUNCTS.get(funct3)
        imm = _sext(((e >> 25) << 5) | ((e >> 7) & 0x1F), 12)
    elif opcode == 0x63:
        instr = _BRANCH_FUNCTS.get(funct3)
        imm = _sext(
            (((e >> 31) & 1) << 12) | (((e >> 7) & 1) << 11)
            | (((e >> 25) & 0x3F) << 5) | (((e >> 8) & 0xF) << 1),
            13,
        )
    elif opcode == 0x6F:
        instr = Instr.JAL
        imm = _sext(
            (((e >> 31) & 1) << 20) | (((e >> 12) & 0xFF) << 12)
            | (((e >> 20) & 1) << 11) | (((e >> 21) & 0x3FF) << 1),
            21,
        )
    elif opcode == 0x67 and funct3 == 0:
        instr = Instr.JALR
        imm = imm_i

    if instr is None:
        return None
    return Decoded(instr, rd, rs1, rs2, imm)


def branch_taken(instr: Instr, rs1_val: int, rs2_val: int) -> bool:
    a, b = rs1_val & 0xFFFFFFFF, rs2_val & 0xFFFFFFFF
    sa, sb = _sext(a, 32), _sext(b, 32)
    return {
        Instr.BEQ: a == b,
        Instr.BNE: a != b,
        Instr.BLT: sa < sb,
        Instr.BGE: sa >= sb,
        Instr.BLTU: a < b,
        Instr.BGEU: a >= b,
    }[instr]


def sample_coverage(d: Decoded, taken: Optional[bool] = None) -> list[Cov]:
    """Coverpoints hit by one retired instruction.

    ``taken`` is required for conditional branches (computed from the
    register values the core actually read).
    """
    points = COVERPOINTS[d.instr]
    out = [Cov.SEEN]
    if Cov.ZERO_DST in points and d.rd == 0:
        out.append(Cov.ZERO_DST)
    reads = d.reads()
    if Cov.SAME_SRC in points and len(reads) == 2 and reads[0] == reads[1]:
        out.append(Cov.SAME_SRC)
    if Cov.ZERO_SRC in points and 0 in reads:
        out.append(Cov.ZERO_SRC)

    if d.instr is Instr.JAL:
        if d.imm < 0:
            out.append(Cov.BR_BACKWARDS)
        if d.imm > 0:
            out.append(Cov.BR_FORWARDS)
    elif d.instr in BRANCHES:
        if not taken:
            out.append(Cov.NOT_TAKEN)
        elif d.imm < 0:
            out.append(Cov.BR_BACKWARDS)
        elif d.imm > 0:
            out.append(Cov.BR_FORWARDS)
    return out


def sample_hazard(prev: Optional[Decoded], curr: Decoded) -> Optional[str]:
    if prev is None:
        return None
    if prev.instr in HAZARD_WRITERS and curr.instr in HAZARD_READERS:
        if prev.rd in (curr.rs1, curr.rs2):
            return hazard_bin(prev.instr, curr.instr)
    return None


class TestL1Model(unittest.TestCase):
    def test_bin_counts(self) -> None:
        self.assertEqual(len(L0_BINS), 196)
        self.assertEqual(len(L1_NEW_BINS), 117)
        self.assertEqual(len(set(ALL_BINS)), 313)

    def test_l0_bins_match_original_plan(self) -> None:
        from designs.ibex_cpu.instructions import Instr as L0Instr
        from designs.ibex_cpu.shared_types import CoverageDatabase as L0Db

        db = L0Db(instructions={}, cross_coverage={})
        for i in L0Instr:
            db.instructions[i] = {c: 0 for c in i.type().coverpoints()}
            db.cross_coverage[i] = {x: 0 for x in i.type().cross_coverpoints()}
        self.assertEqual(set(db.get_coverage_dict()), set(L0_BINS))

    def test_decode(self) -> None:
        cases = [
            (0x00000033, Instr.ADD, 0, 0, 0, 0),
            (0x40678633, Instr.SUB, 12, 15, 6, 0),
            (0x02C5C533, Instr.DIV, 10, 11, 12, 0),
            (0xFFF28293, Instr.ADDI, 5, 5, 31, -1),
            (0x4052D293, Instr.SRAI, 5, 5, 5, 5),
            (0x123452B7, Instr.LUI, 5, 8, 3, 0x12345000),
            (0xFFC42503, Instr.LW, 10, 8, 28, -4),
            (0xFC532F23, Instr.SW, 30, 6, 5, -34),
            (0xFE029CE3, Instr.BNE, 25, 5, 0, -8),
            (0xC1CFA2EF, Instr.JAL, 5, 31, 28, -23524),
            (0x000080E7, Instr.JALR, 1, 1, 0, 0),
        ]
        for enc, instr, rd, rs1, rs2, imm in cases:
            d = decode(enc)
            assert d is not None, hex(enc)
            self.assertEqual((d.instr, d.rd, d.rs1, d.rs2, d.imm),
                             (instr, rd, rs1, rs2, imm), hex(enc))
        for enc in (0x00000000, 0x00000073, 0x40004033, 0x0000100F, 0x6000D293):
            self.assertIsNone(decode(enc), hex(enc))

    def test_branch_coverage(self) -> None:
        bne_back = decode(0xFE029CE3)
        self.assertIn(Cov.BR_BACKWARDS, sample_coverage(bne_back, taken=True))
        self.assertIn(Cov.NOT_TAKEN, sample_coverage(bne_back, taken=False))
        self.assertTrue(branch_taken(Instr.BLT, 0xFFFFFFFF, 0))
        self.assertFalse(branch_taken(Instr.BLTU, 0xFFFFFFFF, 0))


if __name__ == "__main__":
    unittest.main()
