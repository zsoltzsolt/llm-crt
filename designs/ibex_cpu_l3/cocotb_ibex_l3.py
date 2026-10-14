# Copyright lowRISC contributors.
# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Cocotb testbench for the Ibex CPU L3 coverage target.

Builds on the L1 execution harness (park loop, run-once blocks, trap
recovery; see designs/ibex_cpu_l1/cocotb_ibex_l1.py) with a relaxed sandbox:

  * SYSTEM instructions (CSR*, ECALL, EBREAK, MRET, WFI) are executed. Only
    CSR writes to mtvec and Ibex's security CSRs (cpuctrlsts, secureseed) are
    replaced by 0x00000000, since a broken mtvec would make traps unrecoverable.
    Compressed words are still replaced.
  * Data accesses at or above ERROR_REGION_BASE get a bus error.
  * If the core sleeps (WFI), an NMI is raised. The NMI vector
    (mtvec + 0x7C) holds an MRET, so execution resumes after the WFI.
  * The privilege mode persists across blocks, like register state.
"""

import os
import sys
from contextlib import closing

curr_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.abspath(os.path.join(curr_dir, "..", ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from designs.ibex_cpu_l1.cocotb_ibex_l1 import (
    ILLEGAL,
    TRAP_VECTOR,
    MemAgent,
    SimulationController,
    do_reset,
    init_instruction_memory,
)
from designs.ibex_cpu_l3.coverage import PROTECTED_CSRS, decode_system, is_error_addr
from designs.ibex_cpu_l3.instruction_monitor import InstructionMonitor

NMI_VECTOR = TRAP_VECTOR + 0x7C
MRET = 0x30200073
SLEEP_CYCLES_BEFORE_NMI = 4


class L3SimulationController(SimulationController):
    def __init__(self, dut, instruction_monitor, imem_agent, zmq_addr):
        super().__init__(dut, instruction_monitor, imem_agent, zmq_addr)
        self.nmi_mode = dut.u_top.u_ibex_top.u_ibex_core.nmi_mode
        self.nmi_active = False
        self.sleep_cycles = 0
        self.dut.irq_nm_i.value = 0

    def sanitize(self, word):
        word &= 0xFFFFFFFF
        if (word & 0x3) != 0x3:
            return ILLEGAL
        s = decode_system(word)
        if s is not None and s.kind == "csr" and s.csr in PROTECTED_CSRS and s.writes_csr:
            return ILLEGAL
        return word

    async def _tick(self):
        retired = await super()._tick()
        # WFI wake-up: raise NMI until the core has entered NMI mode.
        if self.nmi_active:
            if int(self.nmi_mode.value):
                self.dut.irq_nm_i.value = 0
                self.nmi_active = False
        elif int(self.dut.core_sleep_o.value):
            self.sleep_cycles += 1
            if self.sleep_cycles >= SLEEP_CYCLES_BEFORE_NMI:
                self.dut.irq_nm_i.value = 1
                self.nmi_active = True
                self.sleep_cycles = 0
        else:
            self.sleep_cycles = 0
        return retired


@cocotb.test()
async def basic_test(dut):
    server_port = os.environ.get("CRT_PORT") or input(
        "Please enter server's port (e.g. 5050, 5555): "
    )

    while True:
        dut.data_gnt_i.value = 0
        dut.data_rvalid_i.value = 0
        dut.irq_nm_i.value = 0

        imem_agent = MemAgent(dut, "instr", read_default=lambda addr: ILLEGAL, handle_writes=False)
        dmem_agent = MemAgent(
            dut, "data", read_default=lambda addr: addr, handle_writes=True, error_fn=is_error_addr
        )
        ins_mon = InstructionMonitor(dut)
        init_instruction_memory(imem_agent)
        imem_agent.write_mem(NMI_VECTOR, MRET)

        cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
        await do_reset(dut)
        cocotb.start_soon(imem_agent.run_mem())
        cocotb.start_soon(dmem_agent.run_mem())

        await ClockCycles(dut.clk_i, 1)

        sim_ctrl = L3SimulationController(
            dut, ins_mon, imem_agent, f"tcp://*:{server_port}"
        )
        with closing(sim_ctrl) as simulation_controller:
            cocotb.start_soon(simulation_controller.controller_loop())

            await simulation_controller.end_simulation_event.wait()
            await ClockCycles(dut.clk_i, 1)
