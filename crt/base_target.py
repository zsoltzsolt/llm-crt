# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""DUT Target interface and registry for CRT generation."""

import importlib
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_TARGET_REGISTRY: Dict[str, type] = {}

_KNOWN_DESIGNS = (
    "ibex_decoder",
    "ibex_cpu",
    "mips_cpu",
)


def register_target(name: str):
    """Decorator registering a DUTTarget subclass under ``name``."""

    def decorate(cls):
        if name in _TARGET_REGISTRY and _TARGET_REGISTRY[name] is not cls:
            raise ValueError(f"Target {name!r} is already registered")
        cls.name = name
        _TARGET_REGISTRY[name] = cls
        return cls

    return decorate


def _load_target_module(name: str) -> None:
    try:
        importlib.import_module(f"designs.{name}.target")
    except ModuleNotFoundError as e:
        if e.name not in (name, f"designs.{name}.target", f"designs.{name}"):
            raise


def get_target(name: str) -> "DUTTarget":
    """Return a target instance for ``name``, loading module if needed."""
    if name not in _TARGET_REGISTRY:
        _load_target_module(name)
    if name not in _TARGET_REGISTRY:
        raise KeyError(
            f"No DUT target registered under {name!r}. "
            f"Available: {', '.join(list_targets()) or '(none loaded)'}"
        )
    return _TARGET_REGISTRY[name]()


def list_targets(load_all: bool = True) -> List[str]:
    """Names of all registered targets."""
    if load_all:
        for name in _KNOWN_DESIGNS:
            _load_target_module(name)
    return sorted(_TARGET_REGISTRY)


class DUTTarget(ABC):
    """Abstract base class for hardware designs targeted by CRT."""

    name: str = ""

    @property
    @abstractmethod
    def base_policy_path(self) -> str:
        """Path to the initial base policy scaffold file."""

    @abstractmethod
    def build_refinement_prompt(
        self,
        coverage_plan: Dict[str, int],
        previous_helper_region: str,
        previous_body_region: str,
        max_missed_bins: int = 120,
    ) -> str:
        """Build the LLM prompt for policy refinement based on missing coverage."""

    @abstractmethod
    def validate_policy(
        self, policy_path: str, require_generated_helpers: bool = False
    ) -> None:
        """Target-specific validation for a generated policy file.

        Must raise SyntaxError, ValueError, or RuntimeError if invalid.
        """

    @abstractmethod
    def format_stimulus(self, raw_instruction: Any) -> Any:
        """Format raw output from generate_instruction into testbench stimulus."""

    def parse_response(self, response: Any) -> Tuple[Any, Any]:
        """Parse testbench response into (dut_state, coverage)."""
        if isinstance(response, tuple) and len(response) == 2:
            return response
        return None, response

    def merge_coverage(self, g_coverage: Any, new_coverage: Any) -> None:
        """Merge cycle coverage into global coverage database."""
        if new_coverage is not None:
            g_coverage.set(new_coverage)

    @abstractmethod
    def get_trace_fieldnames(self) -> List[str]:
        """Header column names for stimulus_trace.csv."""

    @abstractmethod
    def write_trace_row(
        self,
        trace_writer: Any,
        batch_idx: int,
        cycle: int,
        policy_path: str,
        raw_instruction: Any,
        dut_state: Any,
        cov_before: Dict[str, int],
        cov_after: Dict[str, int],
        cov_hit: int,
        cov_total: int,
    ) -> None:
        """Write a cycle record to stimulus_trace.csv."""
