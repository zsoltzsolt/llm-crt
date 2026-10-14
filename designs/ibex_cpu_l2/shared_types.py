# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

from dataclasses import dataclass, field
from typing import Dict

from designs.ibex_cpu_l2.coverage import ALL_BINS, bin_level

# Stimulus and DUT state are shared with L1 (same harness).
from designs.ibex_cpu_l1.shared_types import IbexStateInfo, Stimulus  # noqa: F401


@dataclass
class CoverageDatabase:
    bins: Dict[str, int] = field(default_factory=lambda: dict.fromkeys(ALL_BINS, 0))

    def get_coverage_dict(self) -> Dict[str, int]:
        return dict(self.bins)

    @staticmethod
    def is_hard_bin(bin_name: str) -> bool:
        """Cross/sequence bins are the hard ones; they score 2.5x."""
        return "hazard" in bin_name or "back_to_back" in bin_name

    def level_hits(self) -> Dict[int, int]:
        hits = {0: 0, 1: 0, 2: 0}
        for name, count in self.bins.items():
            if count > 0:
                hits[bin_level(name)] += 1
        return hits
