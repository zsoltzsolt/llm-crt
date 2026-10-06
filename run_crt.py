#!/usr/bin/env python3
# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Main CLI entrypoint for LLM-driven CRT test generation experiments.

Examples:
    # Run against Ibex Decoder using OpenAI
    python3 run_crt.py --design ibex_decoder --provider openai --model gpt-4o-mini --server 127.0.0.1:5050

    # Run against MIPS CPU using local Ollama model
    python3 run_crt.py --design mips_cpu --provider ollama --model qwen2.5-coder:7b --server 127.0.0.1:5050

    # List all registered hardware target designs
    python3 run_crt.py --list
"""

import argparse
import os
import sys

# Ensure repository root is on sys.path
repo_root = os.path.dirname(os.path.abspath(__file__))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from crt import get_target, list_targets, run_crt_experiment


def main(argv=None):
    targets = list_targets()
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="LLM-Driven CRT Coverage Refinement Test Generation",
    )
    parser.add_argument(
        "--design",
        "-d",
        choices=targets,
        help="Hardware design under test (required unless --list is specified)",
    )
    parser.add_argument(
        "--provider",
        default="openai",
        choices=["openai", "ollama", "openrouter", "azure"],
        help="LLM provider backend (use 'ollama' for local LLM models)",
    )
    parser.add_argument(
        "--model",
        "-m",
        default="gpt-4o-mini",
        help="LLM model name for policy refinement (e.g. 'gpt-4o-mini', 'qwen2.5-coder:7b')",
    )
    parser.add_argument(
        "--batch-cycles",
        type=int,
        default=100000,
        help="Initial target simulation cycles per batch before LLM refinement",
    )
    parser.add_argument(
        "--max-batch-cycles",
        type=int,
        default=500000,
        help="Hard safety cap on maximum cycles for a single batch when dynamic cycles is enabled",
    )
    parser.add_argument(
        "--dynamic-cycles",
        action="store_true",
        default=True,
        help="Dynamically continue batch execution as long as new coverage hits are being made",
    )
    parser.add_argument(
        "--no-dynamic-cycles",
        dest="dynamic_cycles",
        action="store_false",
        help="Disable dynamic continuation and enforce fixed --batch-cycles limit",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=5,
        help="Maximum policy refinement batches to run",
    )
    parser.add_argument(
        "--stagnation-window",
        type=int,
        default=10000,
        help="Consecutive cycles without new coverage hits before ending batch due to stagnation",
    )
    parser.add_argument(
        "--min-batch-cycles",
        type=int,
        default=10000,
        help="Minimum simulation cycles to execute before checking stagnation stopping",
    )
    parser.add_argument(
        "--server",
        "-s",
        default=None,
        help="IP and port of the cocotb testbench server (e.g. 127.0.0.1:5050)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write run logs and stimulus trace artifacts",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available target designs and exit",
    )

    args = parser.parse_args(argv)

    if args.list:
        print("Available target designs:")
        for name in targets:
            target_obj = get_target(name)
            print(f"  - {name:15s} (Base policy: {target_obj.base_policy_path})")
        return 0

    if not args.design:
        parser.error("--design is required (or pass --list to view available designs)")

    target = get_target(args.design)
    run_crt_experiment(
        target=target,
        batch_cycles=args.batch_cycles,
        max_batches=args.max_batches,
        stagnation_window=args.stagnation_window,
        min_batch_cycles=args.min_batch_cycles,
        dynamic_cycles=args.dynamic_cycles,
        max_batch_cycles=args.max_batch_cycles,
        server_ip_port=args.server,
        provider=args.provider,
        llm_model=args.model,
        output_dir=args.output_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
