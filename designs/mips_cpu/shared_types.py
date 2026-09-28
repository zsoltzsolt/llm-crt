import os
import sys
from dataclasses import dataclass
from itertools import chain
from collections import OrderedDict
from typing import Optional, Dict

directory = os.path.dirname(os.path.abspath("__file__"))
sys.path.insert(0, os.path.dirname(directory))
# print(sys.path)

try:
    from designs.mips_cpu.instructions import Instr, Cov
except ModuleNotFoundError:
    from instructions import Instr, Cov


@dataclass
class CoverageDatabase:
    instructions: list[Instr]
    cross_coverage: dict[Instr, dict[tuple[Instr, Cov], int]]

    def get_coverage_dict(self) -> Dict[str, int]:
        cov_generator = (
            (f"{instr.value}_{cov.value}", num)
            for (instr, covs) in self.instructions.items()
            for (cov, num) in covs.items()
        )
        xcov_generator = (
            (f"{prev_instr.value}->{instr.value}_{cov.value}", num)
            for (instr, x_covs) in self.cross_coverage.items()
            for ((prev_instr, cov), num) in x_covs.items()
        )
        generator = chain(cov_generator, xcov_generator)
        return dict(generator)

    @staticmethod
    def is_hard_bin(bin_name: str) -> bool:
        """RAW-hazard cross bins are the hard ones; they score 2.5x."""
        return "raw_hazard" in bin_name

    def get_coverage_vector(self) -> list[int]:
        return list(self.get_coverage_dict().values())

    def get_coverage_bool_vector(self):
        return list(map(lambda x: 1 if x > 0 else 0, self.get_coverage_vector()))

    def output(self):
        for (instr, covs) in self.instructions.items():
            print(
                f"{instr.value}: (",
                ", ".join(f"{cov.value} {num}" for (cov, num) in covs.items()),
                ")",
            )

        raw_hazards = (
            f"{prev_instr.value}->{instr.value} {num}"
            for (instr, x_covs) in self.cross_coverage.items()
            for ((prev_instr, cov), num) in x_covs.items()
            if cov == Cov.RAW_HAZARD
        )
        WIDTH = 5
        print("Raw Hazards: (\n  ", end="")
        for (idx, item) in enumerate(raw_hazards):
            trailing = ",\n  " if not (idx + 1) % WIDTH else ",\t"
            print(item, end=trailing)
        print("\n)")


@dataclass
class Stimulus:
    insn_mem_updates: list[tuple[int, int]]
    finish: bool


@dataclass
class MipsStateInfo:
    # MIPS architectural reset vector.
    RESET_PC = 0xBFC00000

    last_pc: Optional[int]
    last_insn: Optional[int]
