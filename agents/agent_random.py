# Copyright Zixi Zhang
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

from agents.agent_base import *
import random


class RandomAgent(BaseAgent):
    def __init__(self, total_cycle=1000000, seed=0):
        super().__init__()
        self.seed = seed
        random.seed(self.seed)
        self.total_cycle = total_cycle
        self.current_cycle = 0

    def end_simulation(
        self, dut_state: GlobalDUTState, coverage_database: GlobalCoverageDatabase
    ):
        return not self.current_cycle < self.total_cycle

    def reset(self):
        self.current_cycle = 0

    def generate_next_value(
        self, dut_state: GlobalDUTState, coverage_database: GlobalCoverageDatabase
    ):
        if self.current_cycle % 10000 == 0:
            print(f"Generated {self.current_cycle} stimuli\n")
        self.current_cycle += 1
        return random.getrandbits(32)

class ConstrainedRandomAgent(RandomAgent):
    R_ALU_OPS = {
        "add": (0b0000000, 0b000),
        "sub": (0b0100000, 0b000),
        "sll": (0b0000000, 0b001),
        "slt": (0b0000000, 0b010),
        "sltu": (0b0000000, 0b011),
        "xor": (0b0000000, 0b100),
        "srl": (0b0000000, 0b101),
        "sra": (0b0100000, 0b101),
        "or": (0b0000000, 0b110),
        "and": (0b0000000, 0b111),
    }

    I_ALU_OPS = {
        "add": ("arith", 0b000),
        "slt": ("arith", 0b010),
        "sltu": ("arith", 0b011),
        "xor": ("arith", 0b100),
        "or": ("arith", 0b110),
        "and": ("arith", 0b111),
        "sll": ("shift", 0b0000000, 0b001),
        "srl": ("shift", 0b0000000, 0b101),
        "sra": ("shift", 0b0100000, 0b101),
    }

    LOAD_FUNCT3 = {"byte": 0b000, "half-word": 0b001, "word": 0b010}
    STORE_FUNCT3 = {"byte": 0b000, "half-word": 0b001, "word": 0b010}
    REACHABLE_I_ALU_OPS = ["add", "slt", "sltu", "xor", "or", "and", "sll", "srl", "sra"]

    def __init__(self, total_cycle=1000000, seed=0):
        super().__init__(total_cycle=total_cycle, seed=seed)
        self.rng = random.Random(seed)

    def _rand_reg(self, allow_x0=True):
        if allow_x0:
            return self.rng.randint(0, 31)
        return self.rng.randint(1, 31)

    def _rand_imm(self, bits, align=1, signed=True):
        if signed:
            lo = -(1 << (bits - 1))
            hi = (1 << (bits - 1)) - 1
        else:
            lo = 0
            hi = (1 << bits) - 1

        vals = list(range(lo, hi + 1, align))
        return self.rng.choice(vals)

    def _encode_r_type(self, funct7, rs2, rs1, funct3, rd, opcode=0b0110011):
        return (
            ((funct7 & 0x7F) << 25)
            | ((rs2 & 0x1F) << 20)
            | ((rs1 & 0x1F) << 15)
            | ((funct3 & 0x7) << 12)
            | ((rd & 0x1F) << 7)
            | (opcode & 0x7F)
        )

    def _encode_i_type(self, imm, rs1, funct3, rd, opcode):
        imm &= 0xFFF
        return (
            (imm << 20)
            | ((rs1 & 0x1F) << 15)
            | ((funct3 & 0x7) << 12)
            | ((rd & 0x1F) << 7)
            | (opcode & 0x7F)
        )

    def _encode_s_type(self, imm, rs2, rs1, funct3, opcode=0b0100011):
        imm &= 0xFFF
        imm_11_5 = (imm >> 5) & 0x7F
        imm_4_0 = imm & 0x1F
        return (
            (imm_11_5 << 25)
            | ((rs2 & 0x1F) << 20)
            | ((rs1 & 0x1F) << 15)
            | ((funct3 & 0x7) << 12)
            | (imm_4_0 << 7)
            | (opcode & 0x7F)
        )

    def _encode_b_type(self, imm, rs2, rs1, funct3, opcode=0b1100011):
        imm &= 0x1FFF
        bit12 = (imm >> 12) & 0x1
        bit11 = (imm >> 11) & 0x1
        bits10_5 = (imm >> 5) & 0x3F
        bits4_1 = (imm >> 1) & 0xF

        return (
            (bit12 << 31)
            | (bits10_5 << 25)
            | ((rs2 & 0x1F) << 20)
            | ((rs1 & 0x1F) << 15)
            | ((funct3 & 0x7) << 12)
            | (bits4_1 << 8)
            | (bit11 << 7)
            | (opcode & 0x7F)
        )

    def _encode_u_type(self, imm20, rd, opcode):
        return (
            ((imm20 & 0xFFFFF) << 12)
            | ((rd & 0x1F) << 7)
            | (opcode & 0x7F)
        )

    def _encode_j_type(self, imm, rd, opcode=0b1101111):
        imm &= 0x1FFFFF
        bit20 = (imm >> 20) & 0x1
        bits10_1 = (imm >> 1) & 0x3FF
        bit11 = (imm >> 11) & 0x1
        bits19_12 = (imm >> 12) & 0xFF

        return (
            (bit20 << 31)
            | (bits19_12 << 12)
            | (bit11 << 20)
            | (bits10_1 << 21)
            | ((rd & 0x1F) << 7)
            | (opcode & 0x7F)
        )

    def _gen_r_type_alu(self, op_name=None, rd=None, rs1=None, rs2=None):
        if op_name is None:
            op_name = self.rng.choice(list(self.R_ALU_OPS.keys()))
        funct7, funct3 = self.R_ALU_OPS[op_name]
        rd = self._rand_reg(True) if rd is None else rd
        rs1 = self._rand_reg(True) if rs1 is None else rs1
        rs2 = self._rand_reg(True) if rs2 is None else rs2
        return self._encode_r_type(funct7, rs2, rs1, funct3, rd)

    def _gen_i_type_alu(self, op_name=None, rd=None, rs1=None):
        if op_name is None:
            op_name = self.rng.choice(
                ["add", "slt", "sltu", "xor", "or", "and"]
            )
        _, funct3 = self.I_ALU_OPS[op_name]
        rd = self._rand_reg(True) if rd is None else rd
        rs1 = self._rand_reg(True) if rs1 is None else rs1
        imm = self._rand_imm(12, signed=True)
        return self._encode_i_type(imm, rs1, funct3, rd, opcode=0b0010011)

    def _gen_shift_i_type(self, op_name=None, rd=None, rs1=None):
        if op_name is None:
            op_name = self.rng.choice(["sll", "srl", "sra"])
        _, funct7, funct3 = self.I_ALU_OPS[op_name]
        rd = self._rand_reg(True) if rd is None else rd
        rs1 = self._rand_reg(True) if rs1 is None else rs1
        shamt = self.rng.randint(0, 31)
        imm = ((funct7 & 0x7F) << 5) | (shamt & 0x1F)
        return self._encode_i_type(imm, rs1, funct3, rd, opcode=0b0010011)

    def _gen_load(self, size=None, rd=None, rs1=None):
        if size is None:
            size = self.rng.choice(list(self.LOAD_FUNCT3.keys()))
        funct3 = self.LOAD_FUNCT3[size]
        rd = self._rand_reg(True) if rd is None else rd
        rs1 = self._rand_reg(True) if rs1 is None else rs1

        if funct3 == 0b000:  # byte
            imm = self.rng.choice(range(-32, 33, 1))
        elif funct3 == 0b001:  # half-word
            imm = self.rng.choice(range(-32, 33, 2))
        else:  # word
            imm = self.rng.choice(range(-32, 33, 4))

        return self._encode_i_type(imm, rs1, funct3, rd, opcode=0b0000011)

    def _gen_store(self, size=None, rs1=None, rs2=None):
        if size is None:
            size = self.rng.choice(list(self.STORE_FUNCT3.keys()))
        funct3 = self.STORE_FUNCT3[size]
        rs1 = self._rand_reg(True) if rs1 is None else rs1
        rs2 = self._rand_reg(True) if rs2 is None else rs2

        if funct3 == 0b000:
            imm = self.rng.choice(range(-32, 33, 1))
        elif funct3 == 0b001:
            imm = self.rng.choice(range(-32, 33, 2))
        else:
            imm = self.rng.choice(range(-32, 33, 4))

        return self._encode_s_type(imm, rs2, rs1, funct3)

    def _gen_branch(self):
        ops = [
            ("beq", 0b000),
            ("bne", 0b001),
            ("blt", 0b100),
            ("bge", 0b101),
            ("bltu", 0b110),
            ("bgeu", 0b111),
        ]
        _, funct3 = self.rng.choice(ops)
        rs1 = self._rand_reg(True)
        rs2 = self._rand_reg(True)
        imm = self.rng.choice(range(-256, 257, 2))
        if imm == 0:
            imm = 2
        return self._encode_b_type(imm, rs2, rs1, funct3)

    def _gen_jal(self):
        rd = self._rand_reg(True)
        imm = self.rng.choice(range(-1024, 1025, 2))
        if imm == 0:
            imm = 2
        return self._encode_j_type(imm, rd, opcode=0b1101111)

    def _gen_jalr(self):
        rd = self._rand_reg(True)
        rs1 = self._rand_reg(True)
        imm = self._rand_imm(12, signed=True)
        return self._encode_i_type(imm, rs1, 0b000, rd, opcode=0b1100111)

    def _gen_lui(self):
        rd = self._rand_reg(True)
        imm20 = self.rng.getrandbits(20)
        return self._encode_u_type(imm20, rd, opcode=0b0110111)

    def _gen_auipc(self):
        rd = self._rand_reg(True)
        imm20 = self.rng.getrandbits(20)
        return self._encode_u_type(imm20, rd, opcode=0b0010111)

    def _gen_illegal(self):
        while True:
            instr = self.rng.getrandbits(32)
            opcode = instr & 0x7F
            if opcode not in {
                0b0110011,  # R-type ALU
                0b0010011,  # I-type ALU
                0b0000011,  # loads
                0b0100011,  # stores
                0b1100011,  # branches
                0b1101111,  # jal
                0b1100111,  # jalr
                0b0110111,  # lui
                0b0010111,  # auipc
            }:
                return instr

    def _pick_missing_index(self, bins):
        missed = [i for i, v in enumerate(bins) if v == 0]
        if missed:
            return self.rng.choice(missed)
        return None

    def _has_decoder_coverage_shape(self, coverage):
        return all(
            hasattr(coverage, attr)
            for attr in [
                "misc",
                "alu_ops_x_read_reg_a",
                "alu_ops_x_read_reg_b",
                "alu_ops_x_write_reg",
                "alu_imm_ops_x_read_reg_a",
                "alu_imm_ops_x_write_reg",
                "load_ops_x_read_reg_a",
                "load_ops_x_write_reg",
                "store_ops_x_read_reg_a",
                "store_ops_x_read_reg_b",
            ]
        )

    def _bootstrap_decoder_instr(self):
        r_ops = list(self.R_ALU_OPS.keys())
        i_ops = self.REACHABLE_I_ALU_OPS
        load_sizes = list(self.LOAD_FUNCT3.keys())
        store_sizes = list(self.STORE_FUNCT3.keys())

        sections = [
            ("illegal", 1),
            ("r_alu", len(r_ops) * 32),
            ("i_alu", len(i_ops) * 32),
            ("load", len(load_sizes) * 32),
            ("store", len(store_sizes) * 32),
        ]

        idx = self.current_cycle % sum(length for _, length in sections)

        if idx == 0:
            return self._gen_illegal()
        idx -= 1

        section_len = len(r_ops) * 32
        if idx < section_len:
            op = r_ops[idx // 32]
            reg = idx % 32
            return self._gen_r_type_alu(op_name=op, rd=reg, rs1=reg, rs2=reg)
        idx -= section_len

        section_len = len(i_ops) * 32
        if idx < section_len:
            op = i_ops[idx // 32]
            reg = idx % 32
            if op in {"sll", "srl", "sra"}:
                return self._gen_shift_i_type(op_name=op, rd=reg, rs1=reg)
            return self._gen_i_type_alu(op_name=op, rd=reg, rs1=reg)
        idx -= section_len

        section_len = len(load_sizes) * 32
        if idx < section_len:
            size = load_sizes[idx // 32]
            reg = idx % 32
            return self._gen_load(size=size, rd=reg, rs1=reg)
        idx -= section_len

        size = store_sizes[idx // 32]
        reg = idx % 32
        return self._gen_store(size=size, rs1=reg, rs2=reg)

    def _target_decoder_bin(self, coverage):
        if coverage.misc["illegal_insn"] == 0:
            return self._gen_illegal()

        families = []

        for op in self.R_ALU_OPS:
            miss_a = self._pick_missing_index(coverage.alu_ops_x_read_reg_a[op])
            miss_b = self._pick_missing_index(coverage.alu_ops_x_read_reg_b[op])
            miss_d = self._pick_missing_index(coverage.alu_ops_x_write_reg[op])
            if any(x is not None for x in (miss_a, miss_b, miss_d)):
                families.append(("r_alu", op, miss_d, miss_a, miss_b))

        for op in self.I_ALU_OPS:
            if op == "sub":
                continue
            miss_a = self._pick_missing_index(coverage.alu_imm_ops_x_read_reg_a[op])
            miss_d = self._pick_missing_index(coverage.alu_imm_ops_x_write_reg[op])
            if miss_a is not None or miss_d is not None:
                families.append(("i_alu", op, miss_d, miss_a))

        for size in self.LOAD_FUNCT3:
            miss_a = self._pick_missing_index(coverage.load_ops_x_read_reg_a[size])
            miss_d = self._pick_missing_index(coverage.load_ops_x_write_reg[size])
            if miss_a is not None or miss_d is not None:
                families.append(("load", size, miss_d, miss_a))

        for size in self.STORE_FUNCT3:
            miss_a = self._pick_missing_index(coverage.store_ops_x_read_reg_a[size])
            miss_b = self._pick_missing_index(coverage.store_ops_x_read_reg_b[size])
            if miss_a is not None or miss_b is not None:
                families.append(("store", size, miss_a, miss_b))

        if not families:
            return None

        target = self.rng.choice(families)

        if target[0] == "r_alu":
            _, op, rd, rs1, rs2 = target
            return self._gen_r_type_alu(
                op_name=op,
                rd=self._rand_reg(True) if rd is None else rd,
                rs1=self._rand_reg(True) if rs1 is None else rs1,
                rs2=self._rand_reg(True) if rs2 is None else rs2,
            )

        if target[0] == "i_alu":
            _, op, rd, rs1 = target
            if op in {"sll", "srl", "sra"}:
                return self._gen_shift_i_type(
                    op_name=op,
                    rd=self._rand_reg(True) if rd is None else rd,
                    rs1=self._rand_reg(True) if rs1 is None else rs1,
                )
            return self._gen_i_type_alu(
                op_name=op,
                rd=self._rand_reg(True) if rd is None else rd,
                rs1=self._rand_reg(True) if rs1 is None else rs1,
            )

        if target[0] == "load":
            _, size, rd, rs1 = target
            return self._gen_load(
                size=size,
                rd=self._rand_reg(True) if rd is None else rd,
                rs1=self._rand_reg(True) if rs1 is None else rs1,
            )

        _, size, rs1, rs2 = target
        return self._gen_store(
            size=size,
            rs1=self._rand_reg(True) if rs1 is None else rs1,
            rs2=self._rand_reg(True) if rs2 is None else rs2,
        )

    def _generate_valid_decoder_instr(self):
        kind = self.rng.choices(
            population=[
                "r_alu",
                "i_alu",
                "shift_i",
                "load",
                "store",
                "branch",
                "jal",
                "jalr",
                "lui",
                "auipc",
                "illegal",
            ],
            weights=[30, 24, 10, 18, 16, 0, 0, 0, 0, 0, 2],
            k=1,
        )[0]

        if kind == "r_alu":
            return self._gen_r_type_alu()
        elif kind == "i_alu":
            return self._gen_i_type_alu()
        elif kind == "shift_i":
            return self._gen_shift_i_type()
        elif kind == "load":
            return self._gen_load()
        elif kind == "store":
            return self._gen_store()
        elif kind == "branch":
            return self._gen_branch()
        elif kind == "jal":
            return self._gen_jal()
        elif kind == "jalr":
            return self._gen_jalr()
        elif kind == "lui":
            return self._gen_lui()
        elif kind == "auipc":
            return self._gen_auipc()
        else:
            return self._gen_illegal()

    def generate_next_value(
        self, dut_state: GlobalDUTState, coverage_database: GlobalCoverageDatabase
    ):
        if self.current_cycle % 10000 == 0:
            print(f"Generated {self.current_cycle} stimuli\n")

        self.current_cycle += 1
        coverage = coverage_database.get()
        if coverage is not None and self._has_decoder_coverage_shape(coverage):
            targeted = self._target_decoder_bin(coverage)
            if targeted is not None:
                return targeted

        return self._bootstrap_decoder_instr()


class RandomAgent4IC(RandomAgent):
    def __init__(self, total_cycle=1000000, seed=0):
        super().__init__(total_cycle=1000000, seed=0)

    def generate_next_value(
        self, dut_state: GlobalDUTState, coverage_database: GlobalCoverageDatabase
    ):
        addr = int(dut_state.get_pc(), 16)
        instr = random.getrandbits(32)

        if self.current_cycle % 10000 == 0:
            print(f"Generated {self.current_cycle} stimuli\n")
        self.current_cycle += 1

        return [(addr, instr)]

class ConstrainedRandomAgent4IC(BaseAgent):
    R_ALU_OPS = {
        "add": (0b0000000, 0b000),
        "sub": (0b0100000, 0b000),
        "sll": (0b0000000, 0b001),
        "slt": (0b0000000, 0b010),
        "sltu": (0b0000000, 0b011),
        "xor": (0b0000000, 0b100),
        "srl": (0b0000000, 0b101),
        "sra": (0b0100000, 0b101),
        "or": (0b0000000, 0b110),
        "and": (0b0000000, 0b111),
    }

    STORE_OPS = {
        "sb": 0b000,
        "sh": 0b001,
        "sw": 0b010,
    }

    R_ALU_NAMES = list(R_ALU_OPS.keys())
    STORE_NAMES = list(STORE_OPS.keys())
    IC_INSTR_NAMES = R_ALU_NAMES + STORE_NAMES + ["jal"]

    def __init__(self, total_cycle=1000000, seed=0):
        super().__init__()
        self.seed = seed
        self.total_cycle = total_cycle
        self.current_cycle = 0
        self.rng = random.Random(seed)
        self.pending_instr = None

    def end_simulation(
        self, dut_state: GlobalDUTState, coverage_database: GlobalCoverageDatabase
    ):
        return not self.current_cycle < self.total_cycle

    def reset(self):
        self.current_cycle = 0
        self.pending_instr = None

    def _rand_reg(self, allow_x0=True):
        if allow_x0:
            return self.rng.randint(0, 31)
        return self.rng.randint(1, 31)

    def _pick_reg_not(self, *forbidden, allow_x0=True):
        lo = 0 if allow_x0 else 1
        choices = [reg for reg in range(lo, 32) if reg not in forbidden]
        return self.rng.choice(choices)

    def _biased_rd(self):
        if self.rng.random() < 0.08:
            return 0
        return self._rand_reg(False)

    def _biased_src_pair(self):
        mode = self.rng.random()
        if mode < 0.12:
            reg = self._rand_reg(True)
            return reg, reg
        if mode < 0.24:
            if self.rng.choice([True, False]):
                return 0, self._rand_reg(True)
            return self._rand_reg(True), 0
        return self._rand_reg(True), self._rand_reg(True)

    def _encode_r_type(self, funct7, rs2, rs1, funct3, rd, opcode=0b0110011):
        return (
            ((funct7 & 0x7F) << 25)
            | ((rs2 & 0x1F) << 20)
            | ((rs1 & 0x1F) << 15)
            | ((funct3 & 0x7) << 12)
            | ((rd & 0x1F) << 7)
            | (opcode & 0x7F)
        )

    def _encode_s_type(self, imm, rs2, rs1, funct3, opcode=0b0100011):
        imm &= 0xFFF
        imm_11_5 = (imm >> 5) & 0x7F
        imm_4_0 = imm & 0x1F
        return (
            (imm_11_5 << 25)
            | ((rs2 & 0x1F) << 20)
            | ((rs1 & 0x1F) << 15)
            | ((funct3 & 0x7) << 12)
            | (imm_4_0 << 7)
            | (opcode & 0x7F)
        )

    def _encode_jal(self, rd, imm, opcode=0b1101111):
        imm &= 0x1FFFFF
        bit20 = (imm >> 20) & 0x1
        bits10_1 = (imm >> 1) & 0x3FF
        bit11 = (imm >> 11) & 0x1
        bits19_12 = (imm >> 12) & 0xFF

        return (
            (bit20 << 31)
            | (bits19_12 << 12)
            | (bit11 << 20)
            | (bits10_1 << 21)
            | ((rd & 0x1F) << 7)
            | (opcode & 0x7F)
        )

    def _gen_alu_instr(self, op_name=None, rd=None, rs1=None, rs2=None):
        if op_name is None:
            op_name = self.rng.choice(self.R_ALU_NAMES)
        funct7, funct3 = self.R_ALU_OPS[op_name]
        if rd is None:
            rd = self._biased_rd()
        if rs1 is None or rs2 is None:
            gen_rs1, gen_rs2 = self._biased_src_pair()
            rs1 = gen_rs1 if rs1 is None else rs1
            rs2 = gen_rs2 if rs2 is None else rs2
        return self._encode_r_type(funct7, rs2, rs1, funct3, rd)

    def _gen_store_instr(self, op_name=None, rs1=None, rs2=None):
        if op_name is None:
            op_name = self.rng.choice(self.STORE_NAMES)
        funct3 = self.STORE_OPS[op_name]
        if rs1 is None or rs2 is None:
            gen_rs1, gen_rs2 = self._biased_src_pair()
            rs1 = gen_rs1 if rs1 is None else rs1
            rs2 = gen_rs2 if rs2 is None else rs2

        if funct3 == 0b000:
            imm = self.rng.choice(range(-16, 17, 1))
        elif funct3 == 0b001:
            imm = self.rng.choice(range(-16, 17, 2))
        else:
            imm = self.rng.choice(range(-16, 17, 4))

        return self._encode_s_type(imm, rs2, rs1, funct3)

    def _gen_jal_instr(self, rd=None, backward=None):
        if rd is None:
            rd = 0 if self.rng.random() < 0.08 else self._rand_reg(False)
        if backward is None:
            backward = self.rng.choice([True, False])
        if backward:
            imm = self.rng.choice(range(-256, 0, 2))
        else:
            imm = self.rng.choice(range(2, 258, 2))
        return self._encode_jal(rd, imm)

    def _generate_valid_ibex_instr(self):
        kind = self.rng.choices(
            population=["alu", "store", "jal"],
            weights=[62, 26, 12],
            k=1,
        )[0]

        if kind == "alu":
            return self._gen_alu_instr()
        elif kind == "store":
            return self._gen_store_instr()
        else:
            return self._gen_jal_instr()

    def _make_hazard_consumer(self, instr_name, hazard_reg):
        if instr_name in self.R_ALU_OPS:
            rd = self._biased_rd()
            if self.rng.choice([True, False]):
                rs1, rs2 = hazard_reg, self._pick_reg_not(hazard_reg, allow_x0=True)
            else:
                rs1, rs2 = self._pick_reg_not(hazard_reg, allow_x0=True), hazard_reg
            return self._gen_alu_instr(instr_name, rd=rd, rs1=rs1, rs2=rs2)

        if instr_name in self.STORE_OPS:
            if self.rng.choice([True, False]):
                rs1, rs2 = hazard_reg, self._pick_reg_not(hazard_reg, allow_x0=True)
            else:
                rs1, rs2 = self._pick_reg_not(hazard_reg, allow_x0=True), hazard_reg
            return self._gen_store_instr(instr_name, rs1=rs1, rs2=rs2)

        return self._generate_valid_ibex_instr()

    def _schedule_hazard(self, prev_instr_name, instr_name):
        hazard_reg = self._rand_reg(False)
        self.pending_instr = self._make_hazard_consumer(instr_name, hazard_reg)

        if prev_instr_name in self.R_ALU_OPS:
            return self._gen_alu_instr(prev_instr_name, rd=hazard_reg)

        if prev_instr_name == "jal":
            return self._gen_jal_instr(rd=hazard_reg, backward=self.rng.choice([True, False]))

        self.pending_instr = None
        return None

    def _generate_hazard_pair_start(self):
        prev_instr = self.rng.choice(self.R_ALU_NAMES + ["jal"])
        curr_instr = self.rng.choice(self.IC_INSTR_NAMES[:-1])
        return self._schedule_hazard(prev_instr, curr_instr)

    def generate_next_value(
        self, dut_state: GlobalDUTState, coverage_database: GlobalCoverageDatabase
    ):
        if self.current_cycle % 10000 == 0:
            print(f"Generated {self.current_cycle} stimuli\n")

        self.current_cycle += 1

        if dut_state is None:
            return 0

        pc = int(dut_state.get_pc(), 16)
        instr = None

        if self.pending_instr is not None:
            instr = self.pending_instr
            self.pending_instr = None
        elif self.rng.random() < 0.32:
            instr = self._generate_hazard_pair_start()

        if instr is None:
            instr = self._generate_valid_ibex_instr()

        return [(pc, instr)]
