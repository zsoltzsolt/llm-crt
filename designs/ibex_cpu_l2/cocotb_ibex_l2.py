# Copyright lowRISC contributors.
# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Cocotb testbench for the Ibex CPU L2 coverage target.

Uses the L1 execution harness unchanged (park loop, run-once blocks, trap
recovery, SYSTEM/compressed sanitizing); only the coverage monitor differs.
See designs/ibex_cpu_l1/cocotb_ibex_l1.py for the execution model.
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
    MemAgent,
    SimulationController,
    do_reset,
    init_instruction_memory,
)
from designs.ibex_cpu_l2.instruction_monitor import InstructionMonitor


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
