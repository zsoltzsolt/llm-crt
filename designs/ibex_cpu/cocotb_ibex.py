# Copyright lowRISC contributors.
# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

import struct
import zmq
import os
import sys

curr_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(curr_dir, "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
# print(sys.path)

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Timer, ClockCycles, ReadWrite, Event
try:
    from designs.ibex_cpu.instruction_monitor import InstructionMonitor
    from designs.ibex_cpu.shared_types import Stimulus, IbexStateInfo
except ModuleNotFoundError:
    from instruction_monitor import InstructionMonitor
    from shared_types import Stimulus, IbexStateInfo

from contextlib import closing


async def do_reset(dut):
    dut.rst_ni.value = 1
    await Timer(15, unit="ns")

    dut.rst_ni.value = 0
    await ClockCycles(dut.clk_i, 3)
    await Timer(5, unit="ns")

    dut.rst_ni.value = 1


prog = [0x00000293, 0x01400313, 0x006282B3, 0xFFDFF06F]


def enc_j(rd, imm):
    imm = imm & 0x1FFFFF
    i20 = (imm >> 20) & 1
    i10_1 = (imm >> 1) & 0x3FF
    i11 = (imm >> 11) & 1
    i19_12 = (imm >> 12) & 0xFF
    return (i20 << 31) | (i10_1 << 21) | (i11 << 20) | (i19_12 << 12) | ((rd & 31) << 7) | 0x6F


class MemAgent:
    def __init__(self, dut, mem_name, default_load_val=0x0980006F, handle_writes=True, jump_to_slot_default=True):
        self.mem_dict = {}

        self.clk = dut.clk_i
        self.gnt = getattr(dut, mem_name + "_gnt_i")
        self.req = getattr(dut, mem_name + "_req_o")
        self.addr = getattr(dut, mem_name + "_addr_o")
        self.rvalid = getattr(dut, mem_name + "_rvalid_i")
        self.rdata = getattr(dut, mem_name + "_rdata_i")

        self.default_load_val = default_load_val
        # Unmapped reads near the slot return a JAL back to it. Must be off for
        # instruction memory: entering such a word at its upper halfword decodes
        # as a compressed jump-to-self and the core spins forever.
        self.jump_to_slot_default = jump_to_slot_default

        self.handle_writes = handle_writes

        if handle_writes:
            self.we = getattr(dut, mem_name + "_we_o")
            self.wdata = getattr(dut, mem_name + "_wdata_o")
            self.be = getattr(dut, mem_name + "_be_o")

    @staticmethod
    def _u32(value):
        return int(value) & 0xFFFFFFFF

    def load_bin(self, bin_filename, start_addr):
        with open(bin_filename, "rb") as bin_file:
            cur_addr = start_addr

            for (word,) in struct.iter_unpack("<I", bin_file.read()):
                self.mem_dict[cur_addr] = self._u32(word)
                cur_addr += 4

    def write_mem(self, addr, word):
        self.mem_dict[int(addr)] = self._u32(word)

    async def run_mem(self):
        self.gnt.value = 0
        self.rvalid.value = 0

        while True:
            await ClockCycles(self.clk, 1)
            await ReadWrite()
            self.rvalid.value = 0

            if self.req.value:
                self.gnt.value = 1
                access_addr = self.addr.value

                if self.handle_writes and self.we.value:
                    write_data = self.wdata.value
                else:
                    write_data = None

                await ClockCycles(self.clk, 1)
                await ReadWrite()
                self.gnt.value = 0
                self.rvalid.value = 1

                if self.handle_writes and write_data:
                    self.rdata.value = 0xDEADBAAD
                    self.mem_dict[int(access_addr)] = self._u32(write_data)
                else:
                    addr_int = int(access_addr)
                    if addr_int in self.mem_dict:
                        self.rdata.value = self._u32(self.mem_dict[addr_int])
                    else:
                        delta = 0x100098 - addr_int
                        if self.jump_to_slot_default and -1048576 <= delta <= 1048574 and (delta % 2 == 0):
                            self.rdata.value = self._u32(enc_j(0, delta))
                        else:
                            self.rdata.value = self._u32(self.default_load_val)


async def update_magic_loc(dut, dmem_agent):
    dmem_agent.mem_dict[0x80000000] = 1

    while True:
        await ClockCycles(dut.clk_i, 1)
        await ReadWrite()

        dmem_agent.mem_dict[0x80000000] = (
            dmem_agent.mem_dict[0x80000000] + 1
        ) & 0xFFFFFFFF


class SimulationController:
    def __init__(self, dut, instruction_monitor, imem_agent, zmq_addr):
        self.dut = dut
        self.instruction_monitor = instruction_monitor
        self.end_simulation_event = Event()
        self.zmq_context = zmq.Context()
        self.zmq_addr = zmq_addr
        self.imem_agent = imem_agent

    async def controller_loop(self):
        with self.zmq_context.socket(zmq.REP) as socket:
            socket.bind(self.zmq_addr)

            await ClockCycles(self.dut.clk_i, 1)
            await ReadWrite()

            while True:
                stimulus_obj = socket.recv_pyobj()

                if not isinstance(stimulus_obj, Stimulus):
                    assert False, "Saw bad stimulus message"

                for addr, data in stimulus_obj.insn_mem_updates:
                    self.imem_agent.write_mem(addr, data)

                for _ in range(8):
                    await ClockCycles(self.dut.clk_i, 1)
                    await ReadWrite()
                    self.instruction_monitor.sample_insn_coverage()

                ibex_state_info = IbexStateInfo(
                    last_pc=self.instruction_monitor.last_pc,
                    last_insn=self.instruction_monitor.last_insn,
                )

                socket.send_pyobj(
                    (ibex_state_info, self.instruction_monitor.coverage_db)
                )

                if stimulus_obj.finish:
                    self.end_simulation_event.set()
                    break

    def close(self):
        self.zmq_context.term()


@cocotb.test()
async def basic_test(dut):
    from global_shared_types import GlobalCoverageDatabase

    server_port = os.environ.get("CRT_PORT") or input("Please enter server's port (e.g. 5050, 5555): ")

    while True:
        dut.data_gnt_i.value = 0
        dut.data_rvalid_i.value = 0

        # Unmapped instruction memory reads as an illegal instruction (both halfwords
        # illegal), so any stray jump traps to 0x00100000, which jumps back to the slot.
        imem_agent = MemAgent(
            dut, "instr", default_load_val=0x00000000, handle_writes=False, jump_to_slot_default=False
        )
        dmem_agent = MemAgent(dut, "data", handle_writes=True)
        ins_mon = InstructionMonitor(dut)
        # Boot straight into the stimulus slot. The original test_prog.bin countdown
        # loop is not loaded: a stray jump into it spins up to 2^32 times.
        imem_agent.write_mem(0x100080, enc_j(0, 0x100098 - 0x100080))
        imem_agent.write_mem(0x10009C, 0xFFDFF06F)  # JAL x0, -4 (jump back to 0x100098)
        imem_agent.write_mem(0x00100000, 0x0980006F)  # JAL x0, 152 (jump from trap handler back to 0x100098)

        cocotb.start_soon(Clock(dut.clk_i, 10, units="ns").start())
        await do_reset(dut)
        cocotb.start_soon(imem_agent.run_mem())
        cocotb.start_soon(dmem_agent.run_mem())
        cocotb.start_soon(update_magic_loc(dut, dmem_agent))

        await ClockCycles(dut.clk_i, 1)

        sim_ctrl = SimulationController(
            dut, ins_mon, imem_agent, f"tcp://*:{server_port}"
        )
        with closing(sim_ctrl) as simulation_controller:
            cocotb.start_soon(simulation_controller.controller_loop())

            await simulation_controller.end_simulation_event.wait()
            await ClockCycles(dut.clk_i, 1)
