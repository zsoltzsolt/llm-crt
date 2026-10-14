# Copyright lowRISC contributors.
# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Cocotb testbench for the Ibex CPU L1 coverage target.

Execution model (differs from the L0 testbench on purpose):

  * The core idles in a park loop (``jal x0, 0`` at PARK_PC).
  * For each stimulus the block is written at BLOCK_BASE followed by a
    ``jal`` back to PARK_PC, then PARK_PC is patched to jump into the block.
    Once the launch jump retires, PARK_PC is restored, so the block executes
    exactly once and the step ends when the core is parked again.
  * Unmapped instruction memory reads as 0x00000000 (illegal), so any branch
    or JALR leaving the block traps to mtvec (0x00100000), whose handler
    jumps back to PARK_PC.
  * SYSTEM-opcode words (CSR, ECALL, EBREAK, MRET, WFI) and compressed words
    are replaced by 0x00000000 before execution so they cannot break the
    harness; they are out of scope for L1.
  * Blocks that don't return within STEP_TIMEOUT cycles (e.g. a branch to
    itself) are overwritten with jumps to PARK_PC.
"""

import os
import sys
from contextlib import closing

import zmq

curr_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(curr_dir, "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Event, ReadWrite, Timer

from designs.ibex_cpu_l1.instruction_monitor import InstructionMonitor
from designs.ibex_cpu_l1.shared_types import IbexStateInfo, Stimulus

TRAP_VECTOR = 0x00100000
RESET_PC = 0x00100080
PARK_PC = 0x00100098
BLOCK_BASE = 0x00100100
MAX_BLOCK = 8
ILLEGAL = 0x00000000
STEP_TIMEOUT = 512
RECOVERY_TIMEOUT = 256


def enc_j(rd, imm):
    imm = imm & 0x1FFFFF
    i20 = (imm >> 20) & 1
    i10_1 = (imm >> 1) & 0x3FF
    i11 = (imm >> 11) & 1
    i19_12 = (imm >> 12) & 0xFF
    return (i20 << 31) | (i10_1 << 21) | (i11 << 20) | (i19_12 << 12) | ((rd & 31) << 7) | 0x6F


def jump_to_park(addr):
    return enc_j(0, PARK_PC - addr)


PARK_LOOP = enc_j(0, 0)
LAUNCH = enc_j(0, BLOCK_BASE - PARK_PC)


def sanitize(word):
    word &= 0xFFFFFFFF
    if (word & 0x3) != 0x3 or (word & 0x7F) == 0x73:
        return ILLEGAL
    return word


async def do_reset(dut):
    dut.rst_ni.value = 1
    await Timer(15, unit="ns")

    dut.rst_ni.value = 0
    await ClockCycles(dut.clk_i, 3)
    await Timer(5, unit="ns")

    dut.rst_ni.value = 1


class MemAgent:
    def __init__(self, dut, mem_name, read_default, handle_writes=True, error_fn=None):
        self.mem_dict = {}

        self.clk = dut.clk_i
        self.gnt = getattr(dut, mem_name + "_gnt_i")
        self.req = getattr(dut, mem_name + "_req_o")
        self.addr = getattr(dut, mem_name + "_addr_o")
        self.rvalid = getattr(dut, mem_name + "_rvalid_i")
        self.rdata = getattr(dut, mem_name + "_rdata_i")

        # Callable addr -> word used for addresses never written.
        self.read_default = read_default

        # Optional callable addr -> bool; True answers the access with a bus error.
        self.error_fn = error_fn
        if error_fn is not None:
            self.err = getattr(dut, mem_name + "_err_i")
            self.err.value = 0

        self.handle_writes = handle_writes
        if handle_writes:
            self.we = getattr(dut, mem_name + "_we_o")
            self.wdata = getattr(dut, mem_name + "_wdata_o")
            self.be = getattr(dut, mem_name + "_be_o")

    def write_mem(self, addr, word):
        self.mem_dict[int(addr)] = int(word) & 0xFFFFFFFF

    def read_mem(self, addr):
        if addr in self.mem_dict:
            return self.mem_dict[addr]
        return self.read_default(addr) & 0xFFFFFFFF

    async def run_mem(self):
        self.gnt.value = 0
        self.rvalid.value = 0

        while True:
            await ClockCycles(self.clk, 1)
            await ReadWrite()
            self.rvalid.value = 0
            if self.error_fn is not None:
                self.err.value = 0

            if self.req.value:
                self.gnt.value = 1
                access_addr = int(self.addr.value)
                is_write = self.handle_writes and bool(self.we.value)
                if is_write:
                    write_data = int(self.wdata.value)
                    byte_en = int(self.be.value)

                await ClockCycles(self.clk, 1)
                await ReadWrite()
                self.gnt.value = 0
                self.rvalid.value = 1

                if self.error_fn is not None and self.error_fn(access_addr):
                    self.err.value = 1
                    self.rdata.value = 0
                elif is_write:
                    old = self.read_mem(access_addr)
                    mask = 0
                    for b in range(4):
                        if byte_en & (1 << b):
                            mask |= 0xFF << (8 * b)
                    self.mem_dict[access_addr] = (old & ~mask) | (write_data & mask)
                    self.rdata.value = 0
                else:
                    self.rdata.value = self.read_mem(access_addr)


class SimulationController:
    def __init__(self, dut, instruction_monitor, imem_agent, zmq_addr):
        self.dut = dut
        self.monitor = instruction_monitor
        self.imem = imem_agent
        self.zmq_context = zmq.Context()
        self.zmq_addr = zmq_addr
        self.end_simulation_event = Event()
        self.sim_cycles = 0
        self.timeouts = 0

    async def _tick(self):
        await ClockCycles(self.dut.clk_i, 1)
        await ReadWrite()
        self.sim_cycles += 1
        return self.monitor.sample()

    def sanitize(self, word):
        return sanitize(word)

    def _fill_block_with_park_jumps(self):
        for i in range(MAX_BLOCK + 1):
            addr = BLOCK_BASE + 4 * i
            self.imem.write_mem(addr, jump_to_park(addr))

    async def run_block(self, instructions):
        raw = [w & 0xFFFFFFFF for w in instructions[:MAX_BLOCK]]
        words = [self.sanitize(w) for w in raw]
        self._fill_block_with_park_jumps()
        for i, word in enumerate(words):
            self.imem.write_mem(BLOCK_BASE + 4 * i, word)
        # Sanitized (replaced) words are harness code, not stimulus.
        self.monitor.set_stimulus_pcs(
            BLOCK_BASE + 4 * i for i, (r, w) in enumerate(zip(raw, words)) if r == w
        )
        self.imem.write_mem(PARK_PC, LAUNCH)

        launched = False
        for _ in range(STEP_TIMEOUT):
            retired = await self._tick()
            if retired is None:
                continue
            pc, insn = retired
            if not launched:
                if pc == PARK_PC and insn == LAUNCH:
                    launched = True
                    self.imem.write_mem(PARK_PC, PARK_LOOP)
            elif pc == PARK_PC:
                # Back home: normal return, early exit, or trap recovery.
                return

        # Stuck inside the block: redirect everything to the park loop.
        self.timeouts += 1
        self.monitor.set_stimulus_pcs(())
        self._fill_block_with_park_jumps()
        self.imem.write_mem(PARK_PC, PARK_LOOP)
        for _ in range(RECOVERY_TIMEOUT):
            retired = await self._tick()
            if retired is not None and retired[0] == PARK_PC:
                return
        raise RuntimeError("Ibex did not return to the park loop after recovery")

    async def controller_loop(self):
        with self.zmq_context.socket(zmq.REP) as socket:
            socket.bind(self.zmq_addr)

            while True:
                stimulus_obj = socket.recv_pyobj()

                if not isinstance(stimulus_obj, Stimulus):
                    assert False, "Saw bad stimulus message"

                if stimulus_obj.instructions:
                    await self.run_block(stimulus_obj.instructions)

                ibex_state_info = IbexStateInfo(
                    last_pc=self.monitor.last_pc,
                    last_insn=self.monitor.last_insn,
                    sim_cycles=self.sim_cycles,
                    timeouts=self.timeouts,
                )
                socket.send_pyobj((ibex_state_info, self.monitor.coverage_db))

                if stimulus_obj.finish:
                    self.end_simulation_event.set()
                    break

    def close(self):
        self.zmq_context.term()


def init_instruction_memory(imem_agent):
    imem_agent.write_mem(TRAP_VECTOR, jump_to_park(TRAP_VECTOR))
    imem_agent.write_mem(RESET_PC, jump_to_park(RESET_PC))
    imem_agent.write_mem(PARK_PC, PARK_LOOP)


@cocotb.test()
async def basic_test(dut):
    server_port = os.environ.get("CRT_PORT") or input(
        "Please enter server's port (e.g. 5050, 5555): "
    )

    while True:
        dut.data_gnt_i.value = 0
        dut.data_rvalid_i.value = 0

        imem_agent = MemAgent(dut, "instr", read_default=lambda addr: ILLEGAL, handle_writes=False)
        dmem_agent = MemAgent(dut, "data", read_default=lambda addr: addr, handle_writes=True)
        ins_mon = InstructionMonitor(dut)
        init_instruction_memory(imem_agent)

        cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
        await do_reset(dut)
        cocotb.start_soon(imem_agent.run_mem())
        cocotb.start_soon(dmem_agent.run_mem())

        await ClockCycles(dut.clk_i, 1)

        sim_ctrl = SimulationController(
            dut, ins_mon, imem_agent, f"tcp://*:{server_port}"
        )
        with closing(sim_ctrl) as simulation_controller:
            cocotb.start_soon(simulation_controller.controller_loop())

            await simulation_controller.end_simulation_event.wait()
            await ClockCycles(dut.clk_i, 1)
