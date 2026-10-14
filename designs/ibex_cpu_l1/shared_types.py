# Copyright lowRISC contributors.
# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
from typing import Dict, Optional

from designs.ibex_cpu_l1.instructions import ALL_BINS, bin_level


@dataclass
class CoverageDatabase:
    bins: Dict[str, int] = field(default_factory=lambda: dict.fromkeys(ALL_BINS, 0))

    def get_coverage_dict(self) -> Dict[str, int]:
        return dict(self.bins)

    @staticmethod
    def is_hard_bin(bin_name: str) -> bool:
        """RAW-hazard cross bins are the hard ones; they score 2.5x."""
        return "raw_hazard" in bin_name

    def level_hits(self) -> Dict[int, int]:
        hits = {0: 0, 1: 0}
        for name, count in self.bins.items():
            if count > 0:
                hits[bin_level(name)] += 1
        return hits


@dataclass
class Stimulus:
    # Up to 8 32-bit instruction words, executed once as a block.
    instructions: list[int]
    finish: bool


@dataclass
class IbexStateInfo:
    # Ibex simple-system reset vector.
    RESET_PC = 0x00100080

    last_pc: Optional[int]
    last_insn: Optional[int]
    # Total clock cycles simulated so far and blocks that hit the step timeout.
    sim_cycles: int = 0
    timeouts: int = 0
