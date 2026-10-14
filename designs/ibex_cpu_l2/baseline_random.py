# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Constrained-random baseline policy for Ibex CPU L2 experiments.

Blocks of 1-8 instructions, each uniform over the ten RV32IM instruction formats with random registers and full-range immediates.

Not used by LLM runs, which start from policy.py (plain random 32-bit words).
Run it with:
    run_crt.py --design ibex_cpu_l2 --initial-policy designs/ibex_cpu_l2/baseline_random.py --max-batches 1
"""


# BEGIN LLM HELPER REGION
def enc_r(funct7, rs2, rs1, funct3, rd):
    return (((funct7 & 0x7f) << 25) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | 0x33)

def enc_i(imm, rs1, funct3, rd, opcode):
    return (((imm & 0xfff) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | opcode)

def enc_s(funct3, rs2, rs1, imm):
    return (((imm & 0xfe0) << 20) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((imm & 0x1f) << 7) | 0x23)

def enc_b(funct3, rs1, rs2, imm):
    return ((((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3f) << 25) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | (((imm >> 1) & 0xf) << 8) | (((imm >> 11) & 1) << 7) | 0x63)

def enc_u(imm20, rd, opcode):
    return (((imm20 & 0xfffff) << 12) | ((rd & 31) << 7) | opcode)

def enc_j(rd, imm):
    return (((imm & 0x100000) << 11) | ((imm & 0x7fe) << 20) | ((imm & 0x800) << 9) | (imm & 0xff000) | ((rd & 31) << 7) | 0x6f)

def random_instruction(rng):
    kind = rng.choice(["r", "m", "i", "shift", "u", "load", "store", "branch", "jal", "jalr"])
    rd = rng.randint(0, 31)
    rs1 = rng.randint(0, 31)
    rs2 = rng.randint(0, 31)
    if kind == "r":
        f7, f3 = rng.choice([(0x00, 0), (0x20, 0), (0x00, 1), (0x00, 2), (0x00, 3), (0x00, 4), (0x00, 5), (0x20, 5), (0x00, 6), (0x00, 7)])
        return enc_r(f7, rs2, rs1, f3, rd)
    if kind == "m":
        return enc_r(0x01, rs2, rs1, rng.randint(0, 7), rd)
    if kind == "i":
        return enc_i(rng.getrandbits(12), rs1, rng.choice([0, 2, 3, 4, 6, 7]), rd, 0x13)
    if kind == "shift":
        f7, f3 = rng.choice([(0x00, 1), (0x00, 5), (0x20, 5)])
        return enc_i((f7 << 5) | rng.randint(0, 31), rs1, f3, rd, 0x13)
    if kind == "u":
        return enc_u(rng.getrandbits(20), rd, rng.choice([0x37, 0x17]))
    if kind == "load":
        return enc_i(rng.getrandbits(12), rs1, rng.choice([0, 1, 2, 4, 5]), rd, 0x03)
    if kind == "store":
        return enc_s(rng.choice([0, 1, 2]), rs2, rs1, rng.getrandbits(12))
    if kind == "branch":
        return enc_b(rng.choice([0, 1, 4, 5, 6, 7]), rs1, rs2, rng.getrandbits(13))
    if kind == "jal":
        return enc_j(rd, rng.getrandbits(21))
    return enc_i(rng.getrandbits(12), rs1, 0, rd, 0x67)
# END LLM HELPER REGION


def generate_instruction(coverage, rng):
    # BEGIN LLM BODY REGION
    return [random_instruction(rng) for _ in range(rng.randint(1, 8))]
    # END LLM BODY REGION
