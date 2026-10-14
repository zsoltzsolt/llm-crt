# Ibex CPU — coverage level L3

L3 = L2 (470 bins, see [`../ibex_cpu_l2`](../ibex_cpu_l2)) **+ 106 bins** for
corner cases, CSRs and traps, **576 bins** in total. The core RTL and
configuration are the L0 ones; L3 has its own wrapper
[`cocotb_ibex_l3.sv`](cocotb_ibex_l3.sv) that exposes `data_err_i`, `irq_nm_i`
and `core_sleep_o` to cocotb.

| Group | Bins | Definition |
|---|---|---|
| G1 exceptions | 8 | illegal instruction, ebreak, ecall from M / U, load / store access fault, load / store fault on the second half of a misaligned access |
| G2 CSR accesses | 52 | read/write/set/clear × 9 writable M-mode CSRs (36), reads of 7 read-only CSRs (7), write to read-only / nonexistent / from U-mode (3), the 6 CSR instructions seen (6) |
| G3 privilege & system | 6 | mret to M, mret to U, trap from U-mode, wfi, fence, fence.i |
| G4 operand corners | 40 | divide by zero, INT_MIN/-1, MULH* signs, load sign/zero extension, signed≠unsigned comparison, shift by 0/31, signed add/sub overflow, sra of negatives |

Full definitions are in [`coverage.py`](coverage.py).

## Harness differences from L1/L2

- SYSTEM instructions run. Only CSR writes to `mtvec` and Ibex's security CSRs
  (`0x7C0`, `0x7C1`) are replaced by `0x00000000` (not counted), so traps
  always stay recoverable. Compressed words are still replaced.
- Instructions that raise an exception **are** counted (exception bins).
- Data accesses at or above `0xF0000000` get a bus error.
- If the core sleeps after WFI, the testbench raises an NMI; the NMI vector
  (`0x0010007C`) holds an `mret`, so execution continues after the WFI.
- The privilege mode persists across blocks, like register state; any trap
  returns to M-mode.

LLM runs start from `policy.py` (plain `rng.getrandbits(32)`). The
constrained-random baseline is `baseline_random.py`; run it with
`--initial-policy designs/ibex_cpu_l3/baseline_random.py --max-batches 1`.

Constrained-random baseline (50k steps): 456/576; L3 new bins 72/106. It misses 30 of the
52 CSR bins (random 12-bit CSR addresses rarely hit a specific CSR), the
second-half bus faults and the INT_MIN/-1 division overflows.

```bash
cd designs/ibex_cpu_l3 && CRT_PORT=5050 make
python3 run_crt.py --design ibex_cpu_l3 --provider ollama --model qwen2.5-coder:14b --server 127.0.0.1:5050
python3 -m unittest designs.ibex_cpu_l3.coverage
```
