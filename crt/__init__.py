# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Core framework for LLM-driven Constrained Random Test (CRT) generation."""

import sys

# Register module aliases in sys.modules so ZMQ pickle.loads can unpickle objects
# serialized with legacy top-level module names (e.g., ibex_cpu.shared_types).
try:
    import designs.ibex_cpu as _ibex_cpu
    import designs.ibex_cpu.instructions as _ibex_cpu_instr
    import designs.ibex_cpu.shared_types as _ibex_cpu_shared
    sys.modules.setdefault("ibex_cpu", _ibex_cpu)
    sys.modules.setdefault("ibex_cpu.shared_types", _ibex_cpu_shared)
    sys.modules.setdefault("ibex_cpu.instructions", _ibex_cpu_instr)
except Exception:
    pass

try:
    import designs.mips_cpu as _mips_cpu
    import designs.mips_cpu.instructions as _mips_cpu_instr
    import designs.mips_cpu.shared_types as _mips_cpu_shared
    sys.modules.setdefault("mips_cpu", _mips_cpu)
    sys.modules.setdefault("mips_cpu.shared_types", _mips_cpu_shared)
    sys.modules.setdefault("mips_cpu.instructions", _mips_cpu_instr)
except Exception:
    pass

try:
    import designs.ibex_decoder as _ibex_dec
    import designs.ibex_decoder.shared_types as _ibex_dec_shared
    sys.modules.setdefault("ibex_decoder", _ibex_dec)
    sys.modules.setdefault("ibex_decoder.shared_types", _ibex_dec_shared)
except Exception:
    pass

from crt.agent import EditableCRTAgent
from crt.base_target import DUTTarget, get_target, list_targets, register_target
from crt.llm_client import LLMClient
from crt.runner import run_crt_experiment

__all__ = [
    "EditableCRTAgent",
    "DUTTarget",
    "register_target",
    "get_target",
    "list_targets",
    "LLMClient",
    "run_crt_experiment",
]
