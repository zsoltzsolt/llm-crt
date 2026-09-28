# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Utilities for parsing, validating, updating, and smoke-testing CRT policy files."""

import ast
import importlib.util
import os
import random
import re
import textwrap
from types import ModuleType
from typing import Dict, Tuple

HELPER_BEGIN_MARKER = "# BEGIN LLM HELPER REGION"
HELPER_END_MARKER = "# END LLM HELPER REGION"
BODY_BEGIN_MARKER = "    # BEGIN LLM BODY REGION"
BODY_END_MARKER = "    # END LLM BODY REGION"
LEGACY_BODY_BEGIN_MARKER = "    # BEGIN LLM CONSTRAINT REGION"
LEGACY_BODY_END_MARKER = "    # END LLM CONSTRAINT REGION"

DISALLOWED_NODE_TYPES = (
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
    ast.ClassDef,
    ast.AsyncFunctionDef,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Raise,
    ast.Delete,
)

DISALLOWED_NAMES = {
    "__builtins__",
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}


class MissingCoverage(dict):
    def get(self, key, default=None):
        return 0


def load_policy_module(policy_path: str) -> ModuleType:
    """Dynamically load a Python policy file as a module."""
    module_name = f"llm_crt_policy_{abs(hash(os.path.abspath(policy_path)))}"
    spec = importlib.util.spec_from_file_location(module_name, policy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load policy module from {policy_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "generate_instruction"):
        raise RuntimeError(f"{policy_path} does not define generate_instruction")

    return module


def _validate_instruction_value(instruction) -> None:
    if not isinstance(instruction, int):
        raise ValueError("Policy returned a non-integer instruction")
    if not 0 <= instruction <= 0xFFFFFFFF:
        raise ValueError("Policy returned an instruction outside 32-bit range")


def smoke_test_policy_module(module: ModuleType, allow_instruction_blocks: bool = False) -> None:
    """Run a smoke test against a loaded policy module to verify execution."""
    coverage = MissingCoverage()
    rng = random.Random(0)

    for _ in range(32):
        try:
            result = module.generate_instruction(coverage, rng)
        except Exception as e:
            raise ValueError(
                "Policy failed during smoke test in generate_instruction: "
                f"{type(e).__name__}: {e}"
            ) from e
        if allow_instruction_blocks and isinstance(result, (list, tuple)):
            if not result:
                raise ValueError("Policy returned an empty instruction block")
            for instruction in result:
                _validate_instruction_value(instruction)
            continue
        _validate_instruction_value(result)


def _strip_fenced_code(response: str) -> str:
    fenced = re.search(r"```(?:python)?\s*(.*?)```", response, re.DOTALL | re.I)
    if fenced:
        return fenced.group(1)
    return response


def _extract_marked_region(source: str, begin_marker: str, end_marker: str):
    begin_label = begin_marker.strip()
    end_label = end_marker.strip()
    lines = source.splitlines()
    begin_idx = None

    for i, line in enumerate(lines):
        if line.strip() == begin_label:
            begin_idx = i
            continue
        if begin_idx is not None and line.strip() == end_label:
            return textwrap.dedent("\n".join(lines[begin_idx + 1 : i])).strip()

    return None


def extract_policy_regions(response: str) -> Tuple[str, str]:
    """Extract (helper_region, body_region) from an LLM output string."""
    response = _strip_fenced_code(response)

    helper_region = _extract_marked_region(
        response, HELPER_BEGIN_MARKER, HELPER_END_MARKER
    )
    body_region = _extract_marked_region(
        response, BODY_BEGIN_MARKER, BODY_END_MARKER
    )
    if body_region is None:
        body_region = _extract_marked_region(
            response, LEGACY_BODY_BEGIN_MARKER, LEGACY_BODY_END_MARKER
        )

    if helper_region is not None or body_region is not None:
        if body_region is None:
            function_body = _extract_generate_instruction_body(response)
            if function_body is None:
                raise ValueError("Could not find LLM body region")
            body_region = function_body
        return helper_region or "", body_region

    function_body = _extract_generate_instruction_body(response)
    if function_body is not None:
        return "", function_body

    return "", textwrap.dedent(response).strip()


def _extract_generate_instruction_body(source: str):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "generate_instruction":
            if not node.body:
                return None
            lines = source.splitlines()
            start = node.body[0].lineno - 1
            end = getattr(node.body[-1], "end_lineno", node.body[-1].lineno)
            return textwrap.dedent("\n".join(lines[start:end])).strip()

    return None


def validate_policy_regions(helper_region: str, body_region: str) -> None:
    """Validate policy syntax and safety via Python AST parsing."""
    if not body_region:
        raise ValueError("LLM returned an empty policy body region")

    source = (
        f"{helper_region.strip()}\n\n"
        "def generate_instruction(coverage, rng):\n"
        f"{textwrap.indent(body_region.strip(), '    ')}\n"
    )
    tree = ast.parse(source)
    generate_functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "generate_instruction"
    ]
    if len(generate_functions) != 1:
        raise ValueError("Policy must define exactly one generate_instruction function")

    function = generate_functions[0]
    arg_names = [arg.arg for arg in function.args.args]
    if arg_names != ["coverage", "rng"]:
        raise ValueError("generate_instruction signature must be (coverage, rng)")
    if function.args.vararg or function.args.kwarg or function.args.defaults:
        raise ValueError("generate_instruction must not use varargs or defaults")

    if not any(
        _contains_return_outside_nested_functions(stmt) for stmt in function.body
    ):
        raise ValueError("Policy body region must return instruction(s)")

    for node in ast.walk(tree):
        if node is tree:
            continue
        if isinstance(node, DISALLOWED_NODE_TYPES):
            raise ValueError(f"Disallowed Python construct: {type(node).__name__}")
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            raise ValueError("Decorators are not allowed in helper functions")
        if isinstance(node, ast.Name) and node.id in DISALLOWED_NAMES:
            raise ValueError(f"Disallowed name in policy region: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("Dunder attribute access is not allowed")


def _contains_return_outside_nested_functions(node: ast.AST) -> bool:
    if isinstance(node, ast.FunctionDef):
        return False
    if isinstance(node, ast.Return):
        return True
    return any(
        _contains_return_outside_nested_functions(child)
        for child in ast.iter_child_nodes(node)
    )


def replace_policy_regions(policy_text: str, helper_region: str, body_region: str) -> str:
    """Insert new helper and body regions into policy template text."""
    validate_policy_regions(helper_region, body_region)

    lines = policy_text.splitlines()
    lines = _replace_marked_region(
        lines,
        HELPER_BEGIN_MARKER,
        HELPER_END_MARKER,
        helper_region,
        indent="",
    )
    lines = _replace_marked_region(
        lines,
        BODY_BEGIN_MARKER,
        BODY_END_MARKER,
        body_region,
        indent="    ",
    )
    return "\n".join(lines) + "\n"


def _replace_marked_region(lines, begin_marker, end_marker, region, indent=""):
    begin_label = begin_marker.strip()
    end_label = end_marker.strip()
    begin_idx = None

    for i, line in enumerate(lines):
        if line.strip() == begin_label:
            begin_idx = i
            continue
        if begin_idx is not None and line.strip() == end_label:
            replacement = [line[: len(line) - len(line.lstrip())] + begin_label]
            if region.strip():
                replacement.extend(
                    f"{indent}{region_line}" if region_line else ""
                    for region_line in region.strip().splitlines()
                )
            replacement.append(line[: len(line) - len(line.lstrip())] + end_label)
            return lines[:begin_idx] + replacement + lines[i + 1 :]

    raise ValueError(f"Could not find editable marker pair: {begin_label}")


def format_policy_regions(helper_region: str, body_region: str) -> str:
    return (
        f"{HELPER_BEGIN_MARKER}\n"
        f"{helper_region.strip()}\n"
        f"{HELPER_END_MARKER}\n\n"
        f"{BODY_BEGIN_MARKER.strip()}\n"
        f"{body_region.strip()}\n"
        f"{BODY_END_MARKER.strip()}"
    )


def write_updated_policy(
    current_policy_path: str,
    next_policy_path: str,
    llm_response: str,
    allow_instruction_blocks: bool = False,
) -> str:
    """Extract, validate, replace, write, and smoke-test updated policy file."""
    helper_region, body_region = extract_policy_regions(llm_response)
    with open(current_policy_path, "r", encoding="UTF8") as f:
        policy_text = f.read()

    updated_policy = replace_policy_regions(policy_text, helper_region, body_region)
    with open(next_policy_path, "w", encoding="UTF8") as f:
        f.write(updated_policy)

    try:
        module = load_policy_module(next_policy_path)
    except Exception as e:
        raise RuntimeError(
            "Policy failed while loading generated policy module: "
            f"{type(e).__name__}: {e}"
        ) from e

    smoke_test_policy_module(
        module,
        allow_instruction_blocks=allow_instruction_blocks,
    )
    return format_policy_regions(helper_region, body_region)


def _coverage_group(bin_name: str) -> str:
    if "_x_" in bin_name:
        return bin_name.split("_x_", 1)[0]
    if bin_name.startswith("read_A_reg_"):
        return "read_A_reg"
    if bin_name.startswith("read_B_reg_"):
        return "read_B_reg"
    if bin_name.startswith("write_reg_"):
        return "write_reg"
    return "op_misc"


def summarize_coverage(coverage_plan: Dict[str, int], max_missed_bins: int = 240) -> str:
    hit_bins = [name for name, hits in coverage_plan.items() if hits > 0]
    missed_bins = [name for name, hits in coverage_plan.items() if hits == 0]
    missed_by_group = {}
    for bin_name in missed_bins:
        missed_by_group.setdefault(_coverage_group(bin_name), []).append(bin_name)

    summary = [
        f"Coverage: {len(hit_bins)} / {len(coverage_plan)}",
        f"Uncovered bins: {len(missed_bins)}",
        "",
        "UNREACHED BIN GROUPS:",
    ]

    shown_count = 0
    for group_name in sorted(missed_by_group):
        group_bins = missed_by_group[group_name]
        remaining_budget = max_missed_bins - shown_count
        if remaining_budget <= 0:
            break
        examples = group_bins[: min(8, remaining_budget)]
        shown_count += len(examples)
        summary.append(
            f"- {group_name}: {len(group_bins)} missed; examples: "
            f"{', '.join(examples)}"
        )

    if shown_count < len(missed_bins):
        summary.append(f"- ... {len(missed_bins) - shown_count} more examples omitted")

    return "\n".join(summary)


COMMON_PROMPT_RULES = (
    "Rules:\n"
    "- Return only Python code containing both editable marker pairs.\n"
    "- Do not include markdown or explanations.\n"
    "- Do not include def generate_instruction(...); return only the helper region and the body region contents with their markers.\n"
    "- Define helper functions/constants in the helper region if you need encoders, bin matching, or reusable instruction-selection logic.\n"
    "- Helper-region functions run at module scope. They must not reference coverage or rng unless those values are explicit function arguments.\n"
    "- CRITICAL SCOPE RULE: Do NOT put 'def miss(...)' in the HELPER REGION! 'coverage' is only defined inside generate_instruction in the BODY REGION. Define 'def miss(name): return coverage.get(name, 0) == 0' inside the BEGIN/END LLM BODY REGION.\n"
    "- CRITICAL: To check if a bin is missing, use coverage.get(bin_name, 0) == 0. DO NOT use 'bin_name in coverage'!\n"
    "- CRITICAL STOCHASTIC GATING RULE:\n"
    "  Do NOT write hardcoded deterministic `if miss(x): return static_insn` rules without randomness!\n"
    "  ALWAYS use probability gating e.g. `if miss('bin') and rng.random() < 0.4:` OR use `rng.choice`/`rng.randint` inside targeted blocks so execution falls through to explore other instructions!\n"
    "- Put per-call selection logic and the final return in the body region.\n"
    "- Do not import modules, open files, define classes, or use exec/eval.\n"
    "- Keep some randomness so CRT can still explore."
)


def build_refinement_prompt(
    design_title: str,
    return_rule: str,
    good_response_shape: str,
    isa_helper_reference: str,
    coverage_summary: str,
    previous_helper_region: str,
    previous_body_region: str,
) -> str:
    """Build a standardized CRT prompt combining common framework rules with target-specific specs."""
    return (
        f"You are improving a constrained-random instruction generator for a {design_title}.\n"
        "The simulator/testbench interface is fixed. You may edit only two "
        "marked regions: BEGIN/END LLM HELPER REGION at module scope, and "
        "BEGIN/END LLM BODY REGION inside generate_instruction(coverage, rng).\n\n"
        f"{COMMON_PROMPT_RULES}\n"
        f"- {return_rule}\n\n"
        "Good response shape:\n"
        f"{good_response_shape.strip()}\n\n"
        f"{isa_helper_reference.strip()}\n\n"
        f"{coverage_summary.strip()}\n\n"
        "CURRENT HELPER REGION:\n"
        "```python\n"
        f"{previous_helper_region}\n"
        "```\n\n"
        "CURRENT BODY REGION:\n"
        "```python\n"
        f"{previous_body_region}\n"
        "```\n"
    )

