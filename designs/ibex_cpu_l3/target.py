# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Ibex CPU L3 Target implementation for CRT (L2 + corner cases & traps)."""

import os
from typing import Dict

from crt.base_target import register_target
from crt.policy_manager import build_refinement_prompt
from designs.ibex_cpu_l1.instructions import decode
from designs.ibex_cpu_l1.target import cpu_l1_helper_reference
from designs.ibex_cpu_l2.target import IbexCpuL2Target, cpu_l2_coverage_reference, summarize_l2_coverage
from designs.ibex_cpu_l3.coverage import (
    ERROR_REGION_BASE,
    READ_CSRS,
    WRITABLE_CSRS,
    bin_level,
    decode_system,
)

# Imported so ZMQ replies (pickled L3 coverage databases) can be unpickled.
import designs.ibex_cpu_l3.shared_types  # noqa: F401

L3_SANDBOX_RULE = (
    "- SYSTEM instructions (CSR*, ECALL, EBREAK, MRET, WFI) ARE executed. An instruction that raises an exception "
    "ends the block (the trap handler returns to the idle loop). CSR writes to mtvec (0x305) are blocked "
    "(replaced by an illegal instruction that is not counted). Compressed instructions are blocked.\n"
)


def _csr_table(csrs):
    return ", ".join(f"{name} 0x{addr:03x}" for addr, name in csrs.items())


def cpu_l3_coverage_reference():
    return (
        "Additional L3 coverage plan (corner cases, CSRs, traps):\n"
        "SYSTEM encodings (opcode 0x73): ecall 0x00000073, ebreak 0x00100073, mret 0x30200073, wfi 0x10500073. "
        "fence 0x0ff0000f, fence.i 0x0000100f (opcode 0x0f).\n"
        "- CSR instructions: (csr << 20) | (src << 15) | (funct3 << 12) | (rd << 7) | 0x73 with funct3 csrrw=1, csrrs=2, csrrc=3, "
        "csrrwi=5, csrrsi=6, csrrci=7 (src is rs1, or a 5-bit unsigned immediate for the *i forms).\n"
        "  def enc_csr(csr, src, funct3, rd):\n"
        "      return (((csr & 0xfff) << 20) | ((src & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | 0x73)\n"
        f"- Writable M-mode CSRs: {_csr_table(WRITABLE_CSRS)}.\n"
        f"- Read-only / write-protected CSRs: {_csr_table(READ_CSRS)}.\n"
        "- csr_<name>_read: csrrs/csrrc (or csrrsi/csrrci) with src == 0. csr_<name>_write: csrrw/csrrwi. "
        "csr_<name>_set / _clear: csrrs(i) / csrrc(i) with src != 0. Only accesses that do not trap count.\n"
        "- csr_write_readonly: write to a read-only CSR (address bits [11:10] == 0b11, e.g. mhartid) in M-mode. "
        "csr_nonexistent: access to an unimplemented CSR address in M-mode. csr_from_umode: any M-mode CSR access from U-mode. "
        "These trap as illegal instructions.\n"
        "- <csr instr>_seen: csrrw/csrrs/csrrc/csrrwi/csrrsi/csrrci executed without trapping.\n"
        "- Exceptions (exc_*), counted for the instruction that raises them: exc_illegal_instr (any illegal instruction, "
        "e.g. the word 0x00000000), exc_ebreak, exc_ecall_m / exc_ecall_u (ecall in M-mode / U-mode), "
        f"exc_load_fault / exc_store_fault (data access to an address >= 0x{ERROR_REGION_BASE:08x} gets a bus error), "
        "exc_load_fault_2nd_half / exc_store_fault_2nd_half (misaligned lw/lh/lhu/sw/sh whose first word is below that "
        "address but whose second word is at or above it).\n"
        "- Privilege: the core starts in M-mode. mret returns to the address in mepc with privilege mstatus.MPP "
        "(bits [12:11]; 0b11 = M, 0b00 = U). mret_to_m / mret_to_u count by the privilege mode after the mret. "
        "The privilege mode persists across blocks; any trap returns to M-mode. "
        "trap_from_umode: an instruction raises an exception while in U-mode. "
        "Tip: auipc gives the current PC, so mepc can be pointed at a later instruction of the same block.\n"
        "- wfi_seen: wfi executed (the testbench wakes the core with an NMI and execution continues after the wfi). "
        "fence_seen, fencei_seen: fence / fence.i executed.\n"
        "- Operand corner cases, from the register values at execution time: "
        "<div|divu|rem|remu>_zero_divisor (rs2 == 0); div_overflow / rem_overflow (rs1 == 0x80000000 and rs2 == 0xffffffff); "
        "mulh_neg_neg / mulh_neg_pos / mulh_pos_neg (signs of rs1, rs2); mulhsu_neg_rs1 (rs1 < 0); mulhu_both_msb (bit 31 set in both); "
        "lb_negative / lh_negative (loaded value negative, rd != x0); lbu_msb_set / lhu_msb_set (bit 7 / bit 15 of the loaded value set, rd != x0); "
        "<slt|sltu|slti|sltiu|blt|bge|bltu|bgeu>_sign_disagree (signed and unsigned comparison of the operands give different results); "
        "<sll|srl|sra|slli|srli|srai>_shamt_0 / _shamt_31 (shift amount 0 / 31); add_overflow / addi_overflow / sub_overflow "
        "(signed 32-bit overflow); sra_negative / srai_negative (rs1 < 0).\n"
    )


@register_target("ibex_cpu_l3")
class IbexCpuL3Target(IbexCpuL2Target):
    """DUTTarget for the Ibex RISC-V CPU, coverage level L3 (576 bins)."""

    name = "ibex_cpu_l3"
    level_columns = ("l0_hit", "l1_new_hit", "l2_new_hit", "l3_new_hit")
    # Policies may emit deliberate illegal words to hit exc_illegal_instr.
    max_undecodable_fraction = 0.25

    @staticmethod
    def bin_level(bin_name: str) -> int:
        return bin_level(bin_name)

    @staticmethod
    def decode_policy_word(instruction: int):
        decoded = decode(instruction)
        if decoded is not None:
            return decoded.instr.value
        s = decode_system(instruction)
        if s is not None:
            return s.instr or s.kind
        return None

    @property
    def base_policy_path(self) -> str:
        curr_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(curr_dir, "policy.py")

    def build_refinement_prompt(
        self,
        coverage_plan: Dict[str, int],
        previous_helper_region: str,
        previous_body_region: str,
        max_missed_bins: int = 120,
    ) -> str:
        good_response_shape = (
            "# BEGIN LLM HELPER REGION\n"
            "def enc_i(imm, rs1, funct3, rd, opcode):\n"
            "    return (((imm & 0xfff) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | opcode)\n"
            "def enc_csr(csr, src, funct3, rd):\n"
            "    return (((csr & 0xfff) << 20) | ((src & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | 0x73)\n"
            "# END LLM HELPER REGION\n"
            "# BEGIN LLM BODY REGION\n"
            "def miss(name):\n"
            "    return coverage.get(name, 0) == 0\n"
            "if miss('csr_mscratch_write') and rng.random() < 0.4:\n"
            "    r = rng.randint(1, 31)\n"
            "    return [enc_i(rng.getrandbits(12), 0, 0, r, 0x13), enc_csr(0x340, r, 1, 0)]\n"
            "return [enc_i(rng.getrandbits(12), rng.randint(0, 31), rng.choice([0, 2, 3, 4, 6, 7]), rng.randint(0, 31), 0x13) for _ in range(rng.randint(1, 8))]\n"
            "# END LLM BODY REGION"
        )
        return build_refinement_prompt(
            design_title="Ibex CPU coverage test (RV32IM + Zicsr, M/U privilege, traps)",
            return_rule="The code must return either one 32-bit instruction integer, or a list/tuple of up to 8 instructions (RV32IM, CSR, SYSTEM, FENCE; illegal words are allowed on purpose for exception bins).",
            good_response_shape=good_response_shape,
            isa_helper_reference=(
                cpu_l1_helper_reference(L3_SANDBOX_RULE)
                + "\n" + cpu_l2_coverage_reference()
                + "\n" + cpu_l3_coverage_reference()
            ),
            coverage_summary=summarize_l2_coverage(coverage_plan),
            previous_helper_region=previous_helper_region,
            previous_body_region=previous_body_region,
        )
