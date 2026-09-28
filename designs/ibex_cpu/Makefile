# Copyright lowRISC contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
# Makefile

# defaults
SIM ?= verilator
TOPLEVEL_LANG ?= verilog

VERILOG_SOURCES += $(PWD)/src/lowrisc_lint_common_0.1/tools/verilator/common.vlt \
	$(PWD)/src/lowrisc_prim_assert_0.1/lint/prim_assert.vlt \
	$(PWD)/src/lowrisc_prim_generic_clock_gating_0/lint/prim_generic_clock_gating.vlt \
	$(PWD)/src/lowrisc_prim_xilinx_clock_gating_0/lint/prim_xilinx_clock_gating.vlt \
	$(PWD)/src/lowrisc_prim_xilinx_clock_mux2_0/lint/prim_xilinx_clock_mux2.vlt \
	$(PWD)/src/lowrisc_prim_cipher_0/lint/prim_cipher.vlt \
	$(PWD)/src/lowrisc_prim_generic_clock_mux2_0/lint/prim_generic_clock_mux2.vlt \
	$(PWD)/src/lowrisc_prim_generic_ram_1p_0/lint/prim_generic_ram_1p.vlt \
	$(PWD)/src/lowrisc_prim_onehot_check_0/lint/prim_onehot_check.vlt \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/lint/verilator_waiver.vlt \
	$(PWD)/src/lowrisc_prim_ram_1p_scr_0.1/lint/prim_ram_1p_scr.vlt \
	$(PWD)/src/lowrisc_ibex_ibex_top_0.1/lint/verilator_waiver.vlt \
	$(PWD)/src/lowrisc_ibex_ibex_pkg_0.1/rtl/ibex_pkg.sv \
	$(PWD)/src/lowrisc_prim_abstract_prim_pkg_0.1/prim_pkg.sv \
	$(PWD)/src/lowrisc_prim_ram_1p_pkg_0/rtl/prim_ram_1p_pkg.sv \
	$(PWD)/src/lowrisc_prim_cipher_pkg_0.1/rtl/prim_cipher_pkg.sv \
	$(PWD)/src/lowrisc_prim_generic_buf_0/rtl/prim_generic_buf.sv \
	$(PWD)/src/lowrisc_prim_generic_clock_gating_0/rtl/prim_generic_clock_gating.sv \
	$(PWD)/src/lowrisc_prim_generic_flop_0/rtl/prim_generic_flop.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_pkg.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_22_16_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_22_16_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_28_22_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_28_22_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_39_32_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_39_32_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_64_57_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_64_57_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_72_64_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_72_64_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_hamming_22_16_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_hamming_22_16_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_hamming_39_32_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_hamming_39_32_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_hamming_72_64_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_hamming_72_64_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_hamming_76_68_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_hamming_76_68_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_22_16_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_22_16_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_28_22_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_28_22_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_39_32_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_39_32_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_64_57_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_64_57_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_72_64_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_72_64_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_hamming_22_16_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_hamming_22_16_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_hamming_39_32_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_hamming_39_32_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_hamming_72_64_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_hamming_72_64_enc.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_hamming_76_68_dec.sv \
	$(PWD)/src/lowrisc_prim_secded_0.1/rtl/prim_secded_inv_hamming_76_68_enc.sv \
	$(PWD)/src/lowrisc_prim_xilinx_buf_0/rtl/prim_xilinx_buf.sv \
	$(PWD)/src/lowrisc_prim_xilinx_clock_gating_0/rtl/prim_xilinx_clock_gating.sv \
	$(PWD)/src/lowrisc_prim_xilinx_clock_mux2_0/rtl/prim_xilinx_clock_mux2.sv \
	$(PWD)/src/lowrisc_prim_xilinx_flop_0/rtl/prim_xilinx_flop.sv \
	$(PWD)/src/lowrisc_ibex_ibex_icache_0.1/rtl/ibex_icache.sv \
  $(PWD)/src/lowrisc_ibex_ibex_tracer_0.1/rtl/ibex_tracer_pkg.sv \
  $(PWD)/src/lowrisc_ibex_ibex_tracer_0.1/rtl/ibex_tracer.sv \
	$(PWD)/src/lowrisc_prim_abstract_buf_0/prim_buf.sv \
	$(PWD)/src/lowrisc_prim_abstract_clock_gating_0/prim_clock_gating.sv \
	$(PWD)/src/lowrisc_prim_abstract_flop_0/prim_flop.sv \
	$(PWD)/src/lowrisc_prim_cipher_0/rtl/prim_subst_perm.sv \
	$(PWD)/src/lowrisc_prim_cipher_0/rtl/prim_present.sv \
	$(PWD)/src/lowrisc_prim_cipher_0/rtl/prim_prince.sv \
	$(PWD)/src/lowrisc_prim_generic_clock_mux2_0/rtl/prim_generic_clock_mux2.sv \
	$(PWD)/src/lowrisc_prim_generic_ram_1p_0/rtl/prim_generic_ram_1p.sv \
	$(PWD)/src/lowrisc_prim_lfsr_0.1/rtl/prim_lfsr.sv \
	$(PWD)/src/lowrisc_prim_util_0.1/rtl/prim_util_pkg.sv \
	$(PWD)/src/lowrisc_prim_abstract_clock_mux2_0/prim_clock_mux2.sv \
	$(PWD)/src/lowrisc_prim_badbit_ram_1p_0/prim_badbit_ram_1p.sv \
	$(PWD)/src/lowrisc_prim_onehot_check_0/rtl/prim_onehot_check.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_alu.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_branch_predict.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_compressed_decoder.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_controller.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_cs_registers.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_csr.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_counter.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_decoder.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_ex_block.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_fetch_fifo.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_id_stage.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_if_stage.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_load_store_unit.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_multdiv_fast.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_multdiv_slow.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_prefetch_buffer.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_pmp.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_wb_stage.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_dummy_instr.sv \
	$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl/ibex_core.sv \
	$(PWD)/src/lowrisc_prim_abstract_ram_1p_0/prim_ram_1p.sv \
	$(PWD)/src/lowrisc_prim_ram_1p_adv_0.1/rtl/prim_ram_1p_adv.sv \
	$(PWD)/src/lowrisc_prim_ram_1p_scr_0.1/rtl/prim_ram_1p_scr.sv \
	$(PWD)/src/lowrisc_ibex_ibex_top_0.1/rtl/ibex_register_file_ff.sv \
	$(PWD)/src/lowrisc_ibex_ibex_top_0.1/rtl/ibex_register_file_fpga.sv \
	$(PWD)/src/lowrisc_ibex_ibex_top_0.1/rtl/ibex_register_file_latch.sv \
	$(PWD)/src/lowrisc_ibex_ibex_top_0.1/rtl/ibex_lockstep.sv \
	$(PWD)/src/lowrisc_ibex_ibex_top_0.1/rtl/ibex_top.sv \
  $(PWD)/src/lowrisc_ibex_ibex_top_tracing_0.1/rtl/ibex_top_tracing.sv \
	$(PWD)/cocotb_ibex.sv
# use VHDL_SOURCES for VHDL files

# TOPLEVEL is the name of the toplevel module in your Verilog or VHDL file
TOPLEVEL = cocotb_ibex

# MODULE is the basename of the Python test file
MODULE = cocotb_ibex

# Verilator structural coverage output.
COVERAGE_DIR ?= $(PWD)/logs/coverage
COVERAGE_DAT ?= $(COVERAGE_DIR)/ibex_cpu_coverage.dat
COVERAGE_INFO ?= $(COVERAGE_DIR)/ibex_cpu_coverage.info

.DEFAULT_GOAL := sim

CUSTOM_SIM_DEPS += $(COVERAGE_DIR)

#--trace-fst --trace-structs \

EXTRA_ARGS += \
	-Wno-ENUMVALUE \
	-Wno-fatal \
	-GRV32B=3 \
	+incdir+$(PWD)/src/lowrisc_dv_crypto_prince_ref_0.1 \
	-CFLAGS -I$(PWD)/src/lowrisc_dv_crypto_prince_ref_0.1 \
	+incdir+$(PWD)/src/lowrisc_dv_dv_fcov_macros_0 \
	-CFLAGS -I$(PWD)/src/lowrisc_dv_dv_fcov_macros_0 \
	+incdir+$(PWD)/src/lowrisc_dv_secded_enc_0 \
	-CFLAGS -I$(PWD)/src/lowrisc_dv_secded_enc_0 \
	+incdir+$(PWD)/src/lowrisc_prim_util_get_scramble_params_0/rtl \
	-CFLAGS -I$(PWD)/src/lowrisc_prim_util_get_scramble_params_0/rtl \
	+incdir+$(PWD)/src/lowrisc_prim_util_memload_0/rtl \
	-CFLAGS -I$(PWD)/src/lowrisc_prim_util_memload_0/rtl \
	+incdir+$(PWD)/src/lowrisc_dv_scramble_model_0 \
	-CFLAGS -I$(PWD)/src/lowrisc_dv_scramble_model_0 \
	+incdir+$(PWD)/src/lowrisc_dv_verilator_memutil_dpi_0/cpp \
	-CFLAGS -I$(PWD)/$(PWD)/src/lowrisc_dv_verilator_memutil_dpi_0/cpp \
	+incdir+$(PWD)/src/lowrisc_dv_verilator_memutil_dpi_scrambled_0/cpp \
	-CFLAGS -I$(PWD)/src/lowrisc_dv_verilator_memutil_dpi_scrambled_0/cpp \
	+incdir+$(PWD)/src/lowrisc_prim_assert_0.1/rtl \
	-CFLAGS -I$(PWD)/src/lowrisc_prim_assert_0.1/rtl \
	+incdir+$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl \
	-CFLAGS -I$(PWD)/src/lowrisc_ibex_ibex_core_0.1/rtl \
	-DSYNTHESIS=1 \
	-DRVFI=1 \
	--trace-fst \
	--trace-structs \
	--coverage \
	-CFLAGS "-std=c++14 -Wall -O3"

SIM_ARGS += +verilator+coverage+file+$(COVERAGE_DAT)

.PHONY: coverage-report coverage-summary statement-summary block-summary expr-summary toggle-summary toggle-modules coverage-table all-coverage coverage-clean

$(COVERAGE_DIR):
	@mkdir -p $@

coverage-report:
	@test -f "$(COVERAGE_DAT)" || { \
		printf "Missing coverage data: %s\n" "$(COVERAGE_DAT)"; \
		printf "Run the simulation first with 'make' from %s\n" "$(PWD)"; \
		exit 1; \
	}
	verilator_coverage --write-info $(COVERAGE_INFO) $(COVERAGE_DAT)
	@printf "Wrote Verilator coverage data to %s\n" "$(COVERAGE_DAT)"
	@printf "Wrote lcov report to %s\n" "$(COVERAGE_INFO)"

coverage-summary:
	@test -f "$(COVERAGE_INFO)" || { \
		printf "Missing coverage report: %s\n" "$(COVERAGE_INFO)"; \
		printf "Run 'make coverage-report' from %s first\n" "$(PWD)"; \
		exit 1; \
	}
	@awk -F: '\
	/^LF:/  { lf  += $$2 } \
	/^LH:/  { lh  += $$2 } \
	/^BRF:/ { brf += $$2 } \
	/^BRH:/ { brh += $$2 } \
	END { \
		if (lf > 0)  printf "Line coverage:   %.2f%% (%d/%d)\n", 100 * lh / lf, lh, lf; \
		if (brf > 0) printf "Branch coverage: %.2f%% (%d/%d)\n", 100 * brh / brf, brh, brf; \
	}' "$(COVERAGE_INFO)"

statement-summary:
	@test -f "$(COVERAGE_DAT)" || { \
		printf "Missing coverage data: %s\n" "$(COVERAGE_DAT)"; \
		printf "Run 'make' from %s first\n" "$(PWD)"; \
		exit 1; \
	}
	@awk '\
	index($$0, "t\002line") { \
		total += 1; \
		hits = $$NF + 0; \
		if (hits > 0) covered += 1; \
	} \
	END { \
		if (total > 0) printf "Statement coverage: %.2f%% (%d/%d)\n", 100 * covered / total, covered, total; \
	}' "$(COVERAGE_DAT)"

block-summary:
	@test -f "$(COVERAGE_DAT)" || { \
		printf "Missing coverage data: %s\n" "$(COVERAGE_DAT)"; \
		printf "Run 'make' from %s first\n" "$(PWD)"; \
		exit 1; \
	}
	@awk '\
	index($$0, "t\002line") && index($$0, "o\002block") { \
		total += 1; \
		hits = $$NF + 0; \
		if (hits > 0) covered += 1; \
	} \
	END { \
		if (total > 0) printf "Block coverage: %.2f%% (%d/%d)\n", 100 * covered / total, covered, total; \
	}' "$(COVERAGE_DAT)"

expr-summary:
	@test -f "$(COVERAGE_DAT)" || { \
		printf "Missing coverage data: %s\n" "$(COVERAGE_DAT)"; \
		printf "Run 'make' from %s first\n" "$(PWD)"; \
		exit 1; \
	}
	@awk '\
	index($$0, "t\002expr") { \
		total += 1; \
		hits = $$NF + 0; \
		if (hits > 0) covered += 1; \
	} \
	END { \
		if (total > 0) printf "Expression coverage: %.2f%% (%d/%d)\n", 100 * covered / total, covered, total; \
	}' "$(COVERAGE_DAT)"

toggle-summary:
	@test -f "$(COVERAGE_DAT)" || { \
		printf "Missing coverage data: %s\n" "$(COVERAGE_DAT)"; \
		printf "Run 'make' from %s first\n" "$(PWD)"; \
		exit 1; \
	}
	@awk '\
	index($$0, "t\002toggle") { \
		total += 1; \
		hits = $$NF + 0; \
		if (hits > 0) covered += 1; \
	} \
	END { \
		if (total > 0) printf "Toggle coverage: %.2f%% (%d/%d)\n", 100 * covered / total, covered, total; \
	}' "$(COVERAGE_DAT)"

toggle-modules:
	@test -f "$(COVERAGE_DAT)" || { \
		printf "Missing coverage data: %s\n" "$(COVERAGE_DAT)"; \
		printf "Run 'make' from %s first\n" "$(PWD)"; \
		exit 1; \
	}
	@awk '\
	function basename(path,  n, a) { n = split(path, a, "/"); return a[n]; } \
	function rate(c, t) { return (t > 0) ? (100.0 * c / t) : 0.0; } \
	index($$0, "t\002toggle") { \
		file = "unknown"; \
		if (match($$0, /f\002[^\001]*/)) file = substr($$0, RSTART + 2, RLENGTH - 2); \
		file = basename(file); \
		total[file] += 1; \
		hits = $$NF + 0; \
		if (hits > 0) covered[file] += 1; \
	} \
	END { \
		for (file in total) { \
			printf "%7d %7d %6.2f%% %s\n", covered[file], total[file], rate(covered[file], total[file]), file; \
		} \
	}' "$(COVERAGE_DAT)" | sort -k4,4

coverage-table:
	@test -f "$(COVERAGE_DAT)" || { \
		printf "Missing coverage data: %s\n" "$(COVERAGE_DAT)"; \
		printf "Run 'make' from %s first\n" "$(PWD)"; \
		exit 1; \
	}
	@printf "%-12s %-14s %-12s %-14s %-14s %-12s %-8s %-10s\n" \
		"Functional" "Block" "Branch" "Statement" "Expression" "Toggle" "FSM" "Assertion"
	@awk -F: '\
	/^BRF:/ { brf += $$2 } \
	/^BRH:/ { brh += $$2 } \
	END { \
		if (brf > 0) printf "%.2f%% (%d/%d)", 100 * brh / brf, brh, brf; \
		else printf "n/a"; \
	}' "$(COVERAGE_INFO)" > /tmp/ibex_branch_cov.txt
	@awk '\
	index($$0, "t\002line") && index($$0, "o\002block") { total += 1; if (($$NF + 0) > 0) covered += 1; } \
	END { if (total > 0) printf "%.2f%% (%d/%d)", 100 * covered / total, covered, total; else printf "n/a"; } \
	' "$(COVERAGE_DAT)" > /tmp/ibex_block_cov.txt
	@awk '\
	index($$0, "t\002line") { total += 1; if (($$NF + 0) > 0) covered += 1; } \
	END { if (total > 0) printf "%.2f%% (%d/%d)", 100 * covered / total, covered, total; else printf "n/a"; } \
	' "$(COVERAGE_DAT)" > /tmp/ibex_stmt_cov.txt
	@awk '\
	index($$0, "t\002expr") { total += 1; if (($$NF + 0) > 0) covered += 1; } \
	END { if (total > 0) printf "%.2f%% (%d/%d)", 100 * covered / total, covered, total; else printf "n/a"; } \
	' "$(COVERAGE_DAT)" > /tmp/ibex_expr_cov.txt
	@awk '\
	index($$0, "t\002toggle") { total += 1; if (($$NF + 0) > 0) covered += 1; } \
	END { if (total > 0) printf "%.2f%% (%d/%d)", 100 * covered / total, covered, total; else printf "n/a"; } \
	' "$(COVERAGE_DAT)" > /tmp/ibex_toggle_cov.txt
	@printf "%-12s %-14s %-12s %-14s %-14s %-12s %-8s %-10s\n" \
		"see run log" "$$(cat /tmp/ibex_block_cov.txt)" "$$(cat /tmp/ibex_branch_cov.txt)" \
		"$$(cat /tmp/ibex_stmt_cov.txt)" "$$(cat /tmp/ibex_expr_cov.txt)" "$$(cat /tmp/ibex_toggle_cov.txt)" \
		"n/a" "n/a"

all-coverage: coverage-report coverage-summary statement-summary block-summary expr-summary toggle-summary

coverage-clean:
	$(RM) -r $(COVERAGE_DIR)

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim
