#!/usr/bin/env python3
# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Publication-Quality Results Plotting & Summary Generator for LLM-CRT Experiments.

Parses experiment log directories (stimulus_trace.csv & batch_summary.json)
and generates high-resolution PNG & SVG comparison figures.

Usage:
    python3 scripts/plot_results.py
"""

import csv
import glob
import json
import os
import sys

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


def parse_summary_json(log_dir):
    summary_path = os.path.join(log_dir, "batch_summary.json")
    if os.path.exists(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def parse_stimulus_trace(log_dir):
    trace_path = os.path.join(log_dir, "stimulus_trace.csv")
    if not os.path.exists(trace_path):
        return []
    
    data_points = []
    with open(trace_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            # Sample every 200 rows for smooth rendering
            if i % 200 == 0 or row.get("coverage_hit") == row.get("coverage_total"):
                try:
                    hit = float(row["coverage_hit"])
                    total = float(row["coverage_total"])
                    pct = (hit / total * 100.0) if total > 0 else 0.0
                    data_points.append((i + 1, hit, total, pct))
                except (ValueError, KeyError):
                    continue
    return data_points


def plot_comparison_figures(runs_data, output_dir="docs/charts"):
    os.makedirs(output_dir, exist_ok=True)
    
    # Apply publication style
    plt.style.use('seaborn-v0_8-paper' if 'seaborn-v0_8-paper' in plt.style.available else 'default')
    plt.rcParams['font.sans-serif'] = 'Helvetica'
    plt.rcParams['axes.edgecolor'] = '#333333'
    plt.rcParams['axes.linewidth'] = 1.2

    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]

    # -------------------------------------------------------------
    # Figure 1: Coverage (%) vs Simulation Cycles
    # -------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(8.5, 4.8), dpi=300)
    
    for idx, (label, summary, trace_pts) in enumerate(runs_data):
        if not trace_pts:
            continue
        
        # Ensure points start at (0, 0) for fair baseline alignment
        xs = [0] + [p[0] for p in trace_pts]
        ys = [0.0] + [p[3] for p in trace_pts]
        
        color = colors[idx % len(colors)]
        ax.plot(xs, ys, label=label, color=color, linewidth=2.2)

    ax.set_title("Ibex Decoder: Coverage Growth vs. Simulation Cycles", fontsize=13, fontweight='bold', pad=12)
    ax.set_xlabel("Simulation Cycles", fontsize=11, labelpad=8)
    ax.set_ylabel("Functional Coverage (%)", fontsize=11, labelpad=8)
    ax.set_ylim(0, 105)
    ax.set_xlim(0, 520000)
    ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f"{int(x):,}"))
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(frameon=True, facecolor='#ffffff', edgecolor='#cccccc', fontsize=9, loc="lower right")
    plt.tight_layout()

    fig1_path = os.path.join(output_dir, "fig1_coverage_vs_cycles.png")
    fig.savefig(fig1_path, dpi=300)
    plt.close(fig)
    print(f"Saved Figure 1: {fig1_path}")

    # -------------------------------------------------------------
    # Figure 2: Cycles Executed Per Batch (Bar Chart)
    # -------------------------------------------------------------
    dynamic_run = next((item for item in runs_data if item[1] and item[1].get("batches")), None)
    if dynamic_run:
        label, summary, _ = dynamic_run
        batches = summary["batches"]
        b_nums = [f"Batch {b['batch']}" for b in batches]
        b_cycles = [b["cycles_run"] for b in batches]

        fig, ax = plt.subplots(figsize=(7, 4), dpi=300)
        bars = ax.bar(b_nums, b_cycles, color="#2b5c8f", width=0.55, edgecolor="#1a365d", linewidth=1.2)

        # Draw target budget reference line
        target_budget = summary.get("configured_batch_cycles", 100000)
        ax.axhline(target_budget, color="#d9534f", linestyle="--", linewidth=1.5, label=f"Configured Budget ({target_budget:,} cycles)")

        for bar in bars:
            height = bar.get_height()
            ax.annotate(f"{height:,}",
                        xy=(bar.get_x() + bar.get_width() / 2, height),
                        xytext=(0, 4),  # 4 points vertical offset
                        textcoords="offset points",
                        ha='center', va='bottom', fontweight='bold', fontsize=9)

        ax.set_title("Dynamic Cycles Executed Per Batch (Early Stopping & Extension)", fontsize=13, fontweight='bold', pad=12)
        ax.set_ylabel("Cycles Executed", fontsize=11)
        ax.set_ylim(0, max(b_cycles) * 1.18)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f"{int(x):,}"))
        ax.grid(axis='y', linestyle="--", alpha=0.5)
        ax.legend(frameon=True, facecolor='#ffffff', edgecolor='#cccccc', fontsize=9)
        plt.tight_layout()

        fig2_path = os.path.join(output_dir, "fig2_cycles_per_batch.png")
        fig.savefig(fig2_path, dpi=300)
        plt.close(fig)
        print(f"Saved Figure 2: {fig2_path}")


def main():
    recent_dirs = [
        ("logs/20261003_183523_ibex_decoder_crt", "LLM-CRT With Dynamic Cycles (Extension & Early Stopping)"),
        ("logs/20260927_105646_ibex_decoder_crt", "LLM-CRT Without Dynamic Cycles (Fixed 100k/batch)"),
    ]

    valid_runs = []
    print("\n" + "=" * 90)
    print(f"{'Run Directory / Label':<48} {'Cycles Run':<18} {'Coverage %'}")
    print("=" * 90)

    for log_dir, custom_label in recent_dirs:
        if not os.path.exists(log_dir):
            continue
        summary = parse_summary_json(log_dir)
        basename = os.path.basename(log_dir)
        trace_points = parse_stimulus_trace(log_dir)
        
        if trace_points:
            last_pt = trace_points[-1]
            total_cycles = last_pt[0]
            cov_pct = last_pt[3]
            cycles_str = f"{total_cycles:,} cycles"
        else:
            cycles_str = "N/A"
            cov_pct = 0.0

        print(f"{custom_label:<48} {cycles_str:<18} {cov_pct:.1f}%")
        if trace_points:
            valid_runs.append((custom_label, summary, trace_points))

    print("=" * 90 + "\n")

    plot_comparison_figures(valid_runs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
