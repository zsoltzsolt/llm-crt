# Ibex CPU — coverage level L2

L2 = L1 (313 bins, see [`../ibex_cpu_l1`](../ibex_cpu_l1)) **+ 157 bins** for
instruction sequences and memory alignment, **470 bins** in total. It uses the
L1 execution harness and the L0 RTL unchanged; only the coverage monitor differs.

| Group | Bins | Definition |
|---|---|---|
| G1 hazard types | 101 | `<instr>_raw_hazard` (29 L1 readers), `<instr>_waw_hazard`, `<instr>_war_hazard` (36 register writers); registers other than x0, previous instruction in the same block |
| G2 memory alignment | 20 | `<instr>_offset_<k>` for lw/sw/lh/lhu/sh, effective address [1:0] == k (misaligned accesses are split into two bus transactions) |
| G3 back-to-back | 36 | `<prev>-><curr>_back_to_back` over {load, store, mul, div, branch (taken), jump} |

G1 and G3 only count for instructions retired consecutively **within one
block**; across blocks the harness park loop sits in between.

LLM runs start from `policy.py` (plain `rng.getrandbits(32)`). The
constrained-random baseline `baseline_random.py` returns random blocks of 1–8
instructions, so it is fair on sequence bins; run it with
`--initial-policy designs/ibex_cpu_l2/baseline_random.py --max-batches 1`.

Constrained-random baseline (50k steps): 408/470; L2 new bins 149/157. The bins it misses
are taken branch/jump → X back-to-back pairs, which need the target to be the
next instruction in the block.

```bash
cd designs/ibex_cpu_l2 && CRT_PORT=5050 make
python3 run_crt.py --design ibex_cpu_l2 --provider ollama --model qwen2.5-coder:14b --server 127.0.0.1:5050
python3 -m unittest designs.ibex_cpu_l2.coverage
```
