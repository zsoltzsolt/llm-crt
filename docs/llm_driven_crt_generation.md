# LLM-Driven Constrained Random Test (CRT) Generation

This document provides a detailed architectural overview of the LLM-driven constrained-random test generation flow implemented in this repository.

The framework is design-agnostic: the core policy-refinement loop in `crt/` is shared by all hardware target designs in `designs/` (e.g. `ibex_decoder`, `ibex_cpu`, `mips_cpu`), while design-specific instruction encoders, coverage plans, and Cocotb testbenches remain modularly isolated.

---

## Architecture Overview

```
                      +-----------------------------+
                      |   LLM Refiner Backend       |
                      | (Ollama / OpenAI / Azure)   |
                      +--------------+--------------+
                                     ^
                                     |  Refinement Prompt (Uncovered Bins + Code)
                                     v
                      +-----------------------------+
                      |    crt/policy_manager.py    |
                      |   AST Safety & Policy Patcher|
                      +--------------+--------------+
                                     |
                                     v  Validated Python Policy
                      +-----------------------------+
                      |      crt/agent.py           |
                      |     EditableCRTAgent        |
                      +--------------+--------------+
                                     |
                                     |  Stimulus Batch (100k cycles)
                                     v
+-------------------------------------------------------------------------+
|                      Cocotb / Verilator Testbench                       |
|   (RTL DUT: ibex_decoder / ibex_cpu / mips_cpu)                         |
+-------------------------------------------------------------------------+
                                     |
                                     v  Coverage Updates
                      +-----------------------------+
                      |       crt/runner.py         |
                      |   Batch Coverage Evaluator  |
                      +-----------------------------+
```

---

## Core Principles

1. **Policy-Level Generation vs. Per-Stimulus Prompting**:
   Prompting an LLM on every clock cycle is prohibitively slow and token-expensive. Instead, the LLM is called between batches to write and iteratively refine a small, stochastic Python generation policy (`policy.py`). The policy executes locally at native Python speed against the simulation.

2. **Editable Region Isolation**:
   The LLM is only permitted to modify code inside two marked region pairs:
   ```python
   # BEGIN LLM HELPER REGION
   # (Helper functions, lookup tables, encoders)
   # END LLM HELPER REGION

   def generate_instruction(coverage, rng):
       # BEGIN LLM BODY REGION
       # (Per-stimulus targeting logic and instruction return)
       # END LLM BODY REGION
   ```

3. **Stochastic Exploration Protection**:
   Policies are required to use probability gating (`rng.random() < P` or `rng.choice`) rather than deterministic `if miss(bin): return insn` logic. This ensures generation does not collapse into a fixed replay script and continues exploring cross-coverage combinations.

4. **AST Parsing and Safety Validation**:
   Before an LLM-synthesized policy is executed against the Cocotb simulator, `crt/policy_manager.py` performs static AST checks:
   - Validates correct placement of `BEGIN/END` markers.
   - Rejects illegal syntax, module scope errors, missing function signatures, imports, file IO, and dunder attribute access.
   - Conducts a multi-iteration smoke test to verify execution output formats.

---

## Hardware Target Modules (`designs/`)

Target designs implement the abstract `DUTTarget` class defined in `crt/base_target.py` and register themselves with `@register_target("<design_name>")`:

- **`ibex_decoder`** (`designs/ibex_decoder/`):
  Tests the instruction decoder of the Ibex RISC-V core. Validates full opcode decoding, register indexing, and RV32C compressed instruction cross-bins.
- **`ibex_cpu`** (`designs/ibex_cpu/`):
  Tests the complete Ibex RISC-V processor core. Includes a memory model agent (`MemAgent`) with dynamic `JAL` recovery for exception traps and pipeline RAW hazard cross-coverage sampling.
- **`mips_cpu`** (`designs/mips_cpu/`):
  Tests a 5-stage MIPS32 CPU core including branch delay slots, CP0 control registers, and hazard detection.

---

## Experiment Output Artifacts

Each experiment run creates a timestamped output directory under `logs/`:

- **`policy_iter_0.py`**, **`policy_iter_1.py`**, etc.: Accepted policy iterations.
- **`policy_refinement.txt`**: Complete audit log of prompts sent to the LLM, raw LLM responses, AST validation results, and acceptance/rejection decisions.
- **`stimulus_trace.csv`**: Per-cycle log tracking stimulus values, policy branches taken, coverage growth, and newly covered bins.
