# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Ibex CPU coverage model, level L3 (= L2 + corner cases & traps).

L3 adds 106 bins on top of the 470 L2 bins (576 total):

  G1 exceptions (8): exc_illegal_instr, exc_ebreak, exc_ecall_m, exc_ecall_u,
       exc_load_fault, exc_store_fault (access in the bus-error region), and
       exc_load_fault_2nd_half / exc_store_fault_2nd_half (misaligned access
       whose second bus transaction hits the error region).
  G2 CSR accesses (52):
       csr_<name>_{read,write,set,clear} for 9 writable M-mode CSRs (36)
       csr_<name>_read for 7 read-only / write-protected CSRs (7)
       csr_write_readonly, csr_nonexistent, csr_from_umode (3)
       csrrw/csrrs/csrrc/csrrwi/csrrsi/csrrci _seen (6)
  G3 privilege & system (6): mret_to_m, mret_to_u, trap_from_umode, wfi_seen,
       fence_seen, fencei_seen
  G4 operand corner cases (40): divide by zero, signed division overflow,
       MULH* operand signs, load sign/zero extension, signed vs unsigned
       comparison disagreement, shift by 0/31, signed add/sub overflow,
       arithmetic right shift of negative values.

CSR "read" = csrrs/csrrc (or the immediate forms) with rs1/uimm == 0;
"write" = csrrw/csrrwi; "set"/"clear" = csrrs(i)/csrrc(i) with a non-zero
source. Only accesses that do not trap count for G2 per-CSR bins.
"""

from dataclasses import dataclass
from typing import Optional
import unittest

from designs.ibex_cpu_l1.instructions import Decoded, Instr, LOADS, STORES, decode
from designs.ibex_cpu_l2.coverage import ALL_BINS as L2_BINS, bin_level as l2_bin_level

# Data accesses at or above this address get a bus error from the testbench.
ERROR_REGION_BASE = 0xF0000000

PRIV_U = 0
PRIV_M = 3

EXC_BINS = [
    "exc_illegal_instr", "exc_ebreak", "exc_ecall_m", "exc_ecall_u",
    "exc_load_fault", "exc_store_fault",
    "exc_load_fault_2nd_half", "exc_store_fault_2nd_half",
]

WRITABLE_CSRS = {
    0x300: "mstatus", 0x304: "mie", 0x340: "mscratch", 0x341: "mepc",
    0x342: "mcause", 0x343: "mtval", 0x320: "mcountinhibit",
    0xB00: "mcycle", 0xB02: "minstret",
}
READ_CSRS = {
    0x301: "misa", 0x344: "mip", 0xF14: "mhartid", 0xF11: "mvendorid",
    0xF12: "marchid", 0xF13: "mimpid", 0x305: "mtvec",
}
CSR_OPS = ("read", "write", "set", "clear")
# Writes to these are blocked by the testbench (they would break trap recovery).
PROTECTED_CSRS = frozenset({0x305, 0x7C0, 0x7C1})

CSR_INSTRS = {1: "csrrw", 2: "csrrs", 3: "csrrc", 5: "csrrwi", 6: "csrrsi", 7: "csrrci"}

CSR_BINS = (
    [f"csr_{name}_{op}" for name in WRITABLE_CSRS.values() for op in CSR_OPS]
    + [f"csr_{name}_read" for name in READ_CSRS.values()]
    + ["csr_write_readonly", "csr_nonexistent", "csr_from_umode"]
    + [f"{name}_seen" for name in CSR_INSTRS.values()]
)

SYSTEM_BINS = ["mret_to_m", "mret_to_u", "trap_from_umode", "wfi_seen", "fence_seen", "fencei_seen"]

SHIFTS = (Instr.SLL, Instr.SRL, Instr.SRA, Instr.SLLI, Instr.SRLI, Instr.SRAI)
SIGN_COMPARES = (Instr.SLT, Instr.SLTU, Instr.SLTI, Instr.SLTIU,
                 Instr.BLT, Instr.BGE, Instr.BLTU, Instr.BGEU)

OPERAND_BINS = (
    [f"{i}_zero_divisor" for i in ("div", "divu", "rem", "remu")]
    + ["div_overflow", "rem_overflow"]
    + ["mulh_neg_neg", "mulh_neg_pos", "mulh_pos_neg", "mulhsu_neg_rs1", "mulhu_both_msb"]
    + ["lb_negative", "lh_negative", "lbu_msb_set", "lhu_msb_set"]
    + [f"{i.value}_sign_disagree" for i in SIGN_COMPARES]
    + [f"{i.value}_shamt_{n}" for i in SHIFTS for n in (0, 31)]
    + ["add_overflow", "addi_overflow", "sub_overflow"]
    + ["sra_negative", "srai_negative"]
)

L3_NEW_BINS = EXC_BINS + CSR_BINS + SYSTEM_BINS + OPERAND_BINS
ALL_BINS = L2_BINS + L3_NEW_BINS
_L3_BIN_SET = frozenset(L3_NEW_BINS)


def bin_level(bin_name: str) -> int:
    """0..2 as in L2, 3 = added by L3."""
    return 3 if bin_name in _L3_BIN_SET else l2_bin_level(bin_name)


def is_error_addr(addr: int) -> bool:
    return (addr & 0xFFFFFFFF) >= ERROR_REGION_BASE


@dataclass(frozen=True)
class SystemInsn:
    kind: str           # csr, ecall, ebreak, mret, wfi, fence, fencei
    csr: int = 0
    instr: str = ""     # csrrw ... csrrci
    op: str = ""        # read, write, set, clear
    rd: int = 0

    @property
    def writes_csr(self) -> bool:
        return self.op in ("write", "set", "clear")


_EXACT_SYSTEM = {
    0x00000073: "ecall",
    0x00100073: "ebreak",
    0x30200073: "mret",
    0x10500073: "wfi",
}


def decode_system(encoding: int) -> Optional[SystemInsn]:
    """Decode SYSTEM (CSR*, ECALL, EBREAK, MRET, WFI) and FENCE/FENCE.I words."""
    e = encoding & 0xFFFFFFFF
    opcode = e & 0x7F
    funct3 = (e >> 12) & 0x7
    if opcode == 0x0F:
        if funct3 == 0:
            return SystemInsn("fence")
        if funct3 == 1:
            return SystemInsn("fencei")
        return None
    if opcode != 0x73:
        return None
    if e in _EXACT_SYSTEM:
        return SystemInsn(_EXACT_SYSTEM[e])
    if funct3 not in CSR_INSTRS:
        return None
    src = (e >> 15) & 0x1F  # rs1, or uimm for the immediate forms
    base = funct3 & 0x3
    if base == 1:
        op = "write"
    elif src == 0:
        op = "read"
    else:
        op = "set" if base == 2 else "clear"
    return SystemInsn("csr", csr=e >> 20, instr=CSR_INSTRS[funct3], op=op, rd=(e >> 7) & 0x1F)


def sample_system(s: SystemInsn) -> list[str]:
    """Bins for a SYSTEM/FENCE instruction that retired without trapping."""
    if s.kind == "csr":
        out = [f"{s.instr}_seen"]
        if s.csr in WRITABLE_CSRS:
            out.append(f"csr_{WRITABLE_CSRS[s.csr]}_{s.op}")
        elif s.csr in READ_CSRS and s.op == "read":
            out.append(f"csr_{READ_CSRS[s.csr]}_read")
        return out
    if s.kind == "wfi":
        return ["wfi_seen"]
    if s.kind in ("fence", "fencei"):
        return [f"{s.kind}_seen"]
    return []  # mret is binned by the monitor once the target mode is known


_ACCESS_SIZE = {Instr.LB: 1, Instr.LBU: 1, Instr.SB: 1, Instr.LH: 2, Instr.LHU: 2,
                Instr.SH: 2, Instr.LW: 4, Instr.SW: 4}


def sample_trap(encoding: int, mode: int, rs1_val: int) -> list[str]:
    """Bins for a stimulus instruction that raised a synchronous exception."""
    out = ["trap_from_umode"] if mode == PRIV_U else []
    s = decode_system(encoding)
    if s is not None and s.kind == "ecall":
        return out + ["exc_ecall_u" if mode == PRIV_U else "exc_ecall_m"]
    if s is not None and s.kind == "ebreak":
        return out + ["exc_ebreak"]

    d = decode(encoding)
    if d is not None and d.instr in _ACCESS_SIZE:
        kind = "load" if d.instr in LOADS else "store"
        addr = (rs1_val + d.imm) & 0xFFFFFFFF
        last = (addr + _ACCESS_SIZE[d.instr] - 1) & 0xFFFFFFFF
        if is_error_addr(addr & ~0x3):
            return out + [f"exc_{kind}_fault"]
        if (last & ~0x3) != (addr & ~0x3) and is_error_addr(last & ~0x3):
            return out + [f"exc_{kind}_fault_2nd_half"]
        return out

    out.append("exc_illegal_instr")
    if s is not None and s.kind == "csr":
        if mode == PRIV_U:
            out.append("csr_from_umode")
        elif s.writes_csr and (s.csr >> 10) == 0x3:
            out.append("csr_write_readonly")
        else:
            out.append("csr_nonexistent")
    return out


def _s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >> 31 else v


def sample_operands(d: Decoded, rs1_val: int, rs2_val: int, rd_wdata: int) -> list[str]:
    """G4 bins from the operand/result values the core actually used."""
    i = d.instr
    a, b = rs1_val & 0xFFFFFFFF, rs2_val & 0xFFFFFFFF
    sa, sb = _s32(a), _s32(b)
    out = []

    if i in (Instr.DIV, Instr.DIVU, Instr.REM, Instr.REMU):
        if b == 0:
            out.append(f"{i.value}_zero_divisor")
        if i in (Instr.DIV, Instr.REM) and a == 0x80000000 and b == 0xFFFFFFFF:
            out.append(f"{i.value}_overflow")
    elif i is Instr.MULH:
        if sa < 0 and sb < 0:
            out.append("mulh_neg_neg")
        elif sa < 0 <= sb:
            out.append("mulh_neg_pos")
        elif sb < 0 <= sa:
            out.append("mulh_pos_neg")
    elif i is Instr.MULHSU and sa < 0:
        out.append("mulhsu_neg_rs1")
    elif i is Instr.MULHU and a >> 31 and b >> 31:
        out.append("mulhu_both_msb")
    elif i in LOADS and d.rd != 0:
        w = rd_wdata & 0xFFFFFFFF
        if i in (Instr.LB, Instr.LH) and w >> 31:
            out.append(f"{i.value}_negative")
        elif i is Instr.LBU and w & 0x80:
            out.append("lbu_msb_set")
        elif i is Instr.LHU and w & 0x8000:
            out.append("lhu_msb_set")

    if i in SIGN_COMPARES:
        if i in (Instr.SLTI, Instr.SLTIU):
            b = d.imm & 0xFFFFFFFF
            sb = _s32(b)
        if (sa < sb) != (a < b):
            out.append(f"{i.value}_sign_disagree")

    if i in SHIFTS:
        shamt = d.imm if i in (Instr.SLLI, Instr.SRLI, Instr.SRAI) else b & 0x1F
        if shamt in (0, 31):
            out.append(f"{i.value}_shamt_{shamt}")
        if i in (Instr.SRA, Instr.SRAI) and sa < 0:
            out.append(f"{i.value}_negative")

    if i in (Instr.ADD, Instr.ADDI, Instr.SUB):
        operand = d.imm if i is Instr.ADDI else (-sb if i is Instr.SUB else sb)
        if not -(1 << 31) <= sa + operand < (1 << 31):
            out.append(f"{i.value}_overflow")

    return out


class TestL3Model(unittest.TestCase):
    def test_bin_counts(self) -> None:
        self.assertEqual((len(EXC_BINS), len(CSR_BINS), len(SYSTEM_BINS), len(OPERAND_BINS)), (8, 52, 6, 40))
        self.assertEqual(len(L3_NEW_BINS), 106)
        self.assertEqual(len(set(ALL_BINS)), 576)

    def test_decode_system(self) -> None:
        self.assertEqual(decode_system(0x34029073), SystemInsn("csr", 0x340, "csrrw", "write", 0))  # csrw mscratch, t0
        self.assertEqual(decode_system(0x30002573).op, "read")   # csrr a0, mstatus
        self.assertEqual(decode_system(0x30046073).op, "set")    # csrsi mstatus, 8
        self.assertEqual(decode_system(0x0000100F).kind, "fencei")
        self.assertEqual(decode_system(0x30200073).kind, "mret")
        self.assertIsNone(decode_system(0x00000033))
        self.assertIsNone(decode_system(0x00004073))  # funct3=4 is not a CSR op

    def test_traps(self) -> None:
        self.assertEqual(sample_trap(0x00000073, PRIV_U, 0), ["trap_from_umode", "exc_ecall_u"])
        self.assertEqual(sample_trap(0x00000000, PRIV_M, 0), ["exc_illegal_instr"])
        self.assertEqual(sample_trap(0xF1401073, PRIV_M, 0), ["exc_illegal_instr", "csr_write_readonly"])
        lw = 0x0002A283  # lw x5, 0(x5)
        self.assertEqual(sample_trap(lw, PRIV_M, 0xF0000010), ["exc_load_fault"])
        self.assertEqual(sample_trap(lw, PRIV_M, 0xEFFFFFFE), ["exc_load_fault_2nd_half"])

    def test_operands(self) -> None:
        div = decode(0x02B54533)  # div x10, x10, x11
        self.assertEqual(sample_operands(div, 0x80000000, 0xFFFFFFFF, 0), ["div_overflow"])
        self.assertEqual(sample_operands(div, 5, 0, 0), ["div_zero_divisor"])
        slt = decode(0x00B52533)  # slt x10, x10, x11
        self.assertEqual(sample_operands(slt, 0xFFFFFFFF, 1, 0), ["slt_sign_disagree"])
        add = decode(0x00B50533)  # add x10, x10, x11
        self.assertEqual(sample_operands(add, 0x7FFFFFFF, 1, 0), ["add_overflow"])
        srai = decode(0x41F55513)  # srai x10, x10, 31
        self.assertEqual(sample_operands(srai, 0x80000000, 0, 0), ["srai_shamt_31", "srai_negative"])


if __name__ == "__main__":
    unittest.main()
