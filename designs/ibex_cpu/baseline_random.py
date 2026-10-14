# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Constrained-random baseline policy for Ibex CPU (L0) experiments.

Random R-type ALU and S-type store instructions (the original L0 base policy).

Not used by LLM runs, which start from policy.py (plain random 32-bit words).
Run it with:
    run_crt.py --design ibex_cpu --initial-policy designs/ibex_cpu/baseline_random.py --max-batches 1
"""


# BEGIN LLM HELPER REGION
def enc_r(funct7, rs2, rs1, funct3, rd):
    return (((funct7 & 0x7f) << 25) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((rd & 31) << 7) | 0x33)

def enc_s(funct3, rs2, rs1, imm):
    return (((imm & 0xfe0) << 20) | ((rs2 & 31) << 20) | ((rs1 & 31) << 15) | ((funct3 & 7) << 12) | ((imm & 0x1f) << 7) | 0x23)

def enc_j(rd, imm):
    return (((imm & 0x100000) << 11) | ((imm & 0x7fe) << 20) | ((imm & 0x800) << 9) | (imm & 0xff000) | ((rd & 31) << 7) | 0x6f)
# END LLM HELPER REGION


def generate_instruction(coverage, rng):
    # BEGIN LLM BODY REGION
    op = rng.choice(["r", "s"])
    if op == "r":
        f7 = rng.choice([0x00, 0x20])
        f3 = rng.randint(0, 7)
        return enc_r(f7, rng.randint(0, 31), rng.randint(0, 31), f3, rng.randint(0, 31))
    else:
        f3 = rng.choice([0, 1, 2])
        return enc_s(f3, rng.randint(0, 31), rng.randint(0, 31), rng.randint(0, 2047))
    # END LLM BODY REGION
