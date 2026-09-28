<!--
Copyright 2026 LLM-CRT Contributors.
Copyright lowRISC contributors.
Licensed under the Apache License, Version 2.0, see LICENSE for details.
SPDX-License-Identifier: Apache-2.0
-->

# LLM-Driven Constrained Random Test (CRT) Generation for Hardware Verification

**LLM-CRT** (`llm-crt`) is a high-throughput framework for Large Language Model (LLM) guided **Constrained Random Test (CRT)** generation for hardware design verification.

Instead of prompting an LLM for every single stimulus on every clock cycle (which is slow, fragile, and token-prohibitive), **LLM-CRT** prompts the LLM to synthesize and iteratively refine a **stochastic Python generation policy**. The policy is executed locally at full speed against a Verilator / Cocotb simulation. At the end of each batch (e.g. 100,000 cycles), remaining uncovered functional bins are summarized and fed back to the LLM to refine the generation policy.

---

## Key Features

- **High Throughput**: 100,000+ simulation cycles per policy refinement batch; LLM reasoning occurs between batches, not per clock cycle.
- **AST Safety & Policy Validation**: Automatic AST parsing, scope verification, dunder access prevention, and execution smoke-testing to reject broken LLM policy candidates automatically.
- **Unified Modular Design (`DUTTarget`)**: Adding a new hardware target requires only defining a design folder under `designs/` with RTL, Cocotb testbench, and a `DUTTarget` subclass.
- **Flexible LLM Provider Backend**: Built-in support for local LLMs via **Ollama** (`qwen2.5-coder`, `llama3`), **OpenAI** (`gpt-4o-mini`, `gpt-4o`), **Azure OpenAI**, and **OpenRouter**.
- **Zero-Leak Benchmark Integrity**: Prompting includes only uncovered bin names, rules, and example syntax shapes—ensuring no ground-truth solution encodings or data leakage.

---

## Hardware Target Designs

The repository includes the following hardware target designs in `designs/`:

- **`ibex_decoder`**: Standalone instantiation of the instruction decoder from the lowRISC Ibex RISC-V core (RV32I / RV32C compressed instructions).
- **`ibex_cpu`**: Full lowRISC Ibex 32-bit RISC-V CPU core with memory bus agent, dynamic exception recovery, and pipeline cross-hazard monitoring.
- **`mips_cpu`**: Complete MIPS32 CPU core supporting R-type, I-type, J-type instructions, delay slots, and coprocessor 0 control.

---

## Quickstart

### 1. Prerequisites

- **Python 3.10+**
- **Verilator** (v5.0+ recommended)
- **Cocotb** (v1.8+)

Install Python dependencies:
```bash
pip install -r python-requirements.txt
```

---

### 2. Running an Experiment

#### Step 1: Start the Cocotb Simulation Testbench
In one terminal, navigate to the target design directory, compile, and start the Verilator simulation server:
```bash
cd designs/ibex_decoder
make
```
When prompted by the testbench wrapper, enter a port number (e.g. `5050`).

#### Step 2: Launch the LLM CRT Client
In a second terminal, launch `run_crt.py`:

**Using Local Ollama (`qwen2.5-coder`):**
```bash
python3 run_crt.py --design ibex_decoder --provider ollama --model qwen2.5-coder:14b --server 127.0.0.1:5050
```

**Using OpenAI (`gpt-4o-mini`):**
```bash
export OPENAI_API_KEY="your-api-key"
python3 run_crt.py --design ibex_decoder --provider openai --model gpt-4o-mini --server 127.0.0.1:5050
```

To list all available registered hardware targets:
```bash
python3 run_crt.py --list
```

---

## Project Structure

```
llm-crt/
├── crt/                          # Core Framework Engine
│   ├── __init__.py               # Package init & legacy policy unpickling aliases
│   ├── agent.py                  # EditableCRTAgent policy runner & execution environment
│   ├── base_target.py            # DUTTarget abstract base class & registry
│   ├── llm_client.py             # LLM client supporting Ollama, OpenAI, Azure, & OpenRouter
│   ├── policy_manager.py         # Standardized prompt builder, AST validation, & policy patcher
│   └── runner.py                 # Batch simulation loop, coverage tracking, & trace logger
│
├── designs/                      # Hardware Target Designs (DUTs)
│   ├── ibex_decoder/             # Ibex Instruction Decoder (RTL, TB, policy.py, target.py)
│   ├── ibex_cpu/                 # Ibex RISC-V CPU (RTL, TB, policy.py, target.py)
│   └── mips_cpu/                 # MIPS CPU (RTL, TB, policy.py, target.py)
│
├── docs/                         # Flow Documentation & Architecture
│   └── llm_driven_crt_generation.md
│
├── run_crt.py                    # Unified CLI entrypoint
├── global_shared_types.py        # Shared data structures for testbench IPC
└── python-requirements.txt       # Python package dependencies
```

---

## Adding a New Hardware Target Design

Adding a new target design to the framework is clean and modular:

1. Create a directory `designs/<new_design>/` containing your RTL files, Makefile, and Cocotb testbench.
2. Create a `policy.py` containing the two editable region markers:
   ```python
   # BEGIN LLM HELPER REGION
   # END LLM HELPER REGION

   def generate_instruction(coverage, rng):
       # BEGIN LLM BODY REGION
       return rng.getrandbits(32)
       # END LLM BODY REGION
   ```
3. Create a `target.py` defining a subclass of `DUTTarget` registered with `@register_target("<new_design>")`.
4. Your target design will automatically be recognized by `python3 run_crt.py --list` and ready for experiments!

---

## Documentation

For a detailed deep dive into the CRT refinement loop, validation safety rules, and prompt structure, see [`docs/llm_driven_crt_generation.md`](docs/llm_driven_crt_generation.md).

---

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.
