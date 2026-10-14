# Ibex CPU — coverage level L1

L1 = the original 196-bin `ibex_cpu` plan (L0) **+ 117 bins** for the remaining
non-system RV32IM instructions, **313 bins** in total. The RTL and the
`cocotb_ibex.sv` wrapper are reused from [`../ibex_cpu`](../ibex_cpu) unchanged;
the L0 target is not modified.

| Group | Instructions | Coverpoints | Bins |
|---|---|---|---|
| L0 (unchanged) | add…and, sb/sh/sw, jal | seen, zero_dst, zero_src, same_src, br_* + 143 RAW hazards | 196 |
| I-type ALU | addi, slti, sltiu, xori, ori, andi, slli, srli, srai | seen, zero_dst, zero_src | 27 |
| Upper immediate | lui, auipc | seen, zero_dst | 4 |
| Loads | lb, lh, lw, lbu, lhu | seen, zero_dst, zero_src | 15 |
| Branches | beq, bne, blt, bge, bltu, bgeu | seen, zero_src, same_src, br_backwards, br_forwards, not_taken | 36 |
| JALR | jalr | seen, zero_dst, zero_src | 3 |
| M extension | mul, mulh, mulhsu, mulhu, div, divu, rem, remu | seen, zero_dst, zero_src, same_src | 32 |

Branch `taken` is computed from the register values the core actually read
(RVFI `rs1_rdata`/`rs2_rdata`). The trace CSV reports `l0_hit` and
`l1_new_hit` separately, so L0 progress can be compared with the L0 target.

## Execution model

Unlike the L0 testbench (one slot re-executed for 8 clocks per step), each
stimulus block of up to 8 instructions executes **exactly once**:

1. The core idles in a park loop (`jal x0, 0` at `0x00100098`).
2. The block is written at `0x00100100`, followed by a jump back to the park
   loop, and the park loop is patched to jump into the block. After the launch
   jump retires, the park loop is restored.
3. Only instructions retired from the block's addresses count for coverage.
4. Unmapped instruction memory reads as `0x00000000` (illegal), so branches
   and JALRs leaving the block trap to `0x00100000`, which jumps back to the
   park loop.
5. SYSTEM-opcode words (CSR, ECALL, EBREAK, MRET, WFI) and compressed words
   are replaced with `0x00000000` before execution; they are out of scope for L1.
6. Blocks that don't return within 512 clocks (e.g. a branch to itself) are
   overwritten with jumps to the park loop (`timeouts` column in the trace).

## Running

```bash
# Terminal 1: build and start the testbench (CRT_PORT skips the port prompt)
cd designs/ibex_cpu_l1
CRT_PORT=5050 make

# Terminal 2
python3 run_crt.py --design ibex_cpu_l1 --provider openai --model gpt-4o-mini --server 127.0.0.1:5050
```

## Policies

- `policy.py` — starting point of LLM runs (`policy_iter_0`): plain
  `rng.getrandbits(32)`, empty helper region. The LLM writes the generator.
- `baseline_random.py` — constrained-random baseline (uniform over the L1
  instruction formats, random registers/immediates). Run it without LLM calls:

```bash
python3 run_crt.py --design ibex_cpu_l1 --initial-policy designs/ibex_cpu_l1/baseline_random.py \
  --max-batches 1 --batch-cycles 20000 --no-dynamic-cycles --stagnation-window 0 --server 127.0.0.1:5050
```

Unit tests for the coverage model:

```bash
python3 -m unittest designs.ibex_cpu_l1.instructions
```
