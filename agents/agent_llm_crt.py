# Copyright Zixi Zhang
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

import ast
import importlib.util
import os
import random
import re
import textwrap
from types import ModuleType
from typing import Dict, List

from agents.agent_base import BaseAgent
from global_shared_types import GlobalCoverageDatabase, GlobalDUTState


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


class EditableCRTAgent(BaseAgent):
    def __init__(self, policy_path, total_cycle=300000, seed=0):
        super().__init__()
        import random

        self.policy_path = policy_path
        self.total_cycle = total_cycle
        self.current_cycle = 0
        self.rng = random.Random(seed)
        self.policy_module = load_policy_module(policy_path)

    def end_simulation(
        self, dut_state: GlobalDUTState, coverage_database: GlobalCoverageDatabase
    ):
        return not self.current_cycle < self.total_cycle

    def reset(self):
        self.current_cycle = 0

    def generate_next_value(
        self, dut_state: GlobalDUTState, coverage_database: GlobalCoverageDatabase
    ):
        if self.current_cycle % 10000 == 0:
            print(f"Generated {self.current_cycle} CRT stimuli\n")

        self.current_cycle += 1
        coverage = (
            {}
            if coverage_database.get() is None
            else coverage_database.get_coverage_plan()
        )
        return self.policy_module.generate_instruction(coverage, self.rng)


def load_policy_module(policy_path) -> ModuleType:
    module_name = f"llm_crt_policy_{abs(hash(os.path.abspath(policy_path)))}"
    spec = importlib.util.spec_from_file_location(module_name, policy_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load policy module from {policy_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "generate_instruction"):
        raise RuntimeError(f"{policy_path} does not define generate_instruction")

    return module


class MissingCoverage(dict):
    def get(self, key, default=None):
        return 0


def _validate_instruction_value(instruction) -> None:
    if not isinstance(instruction, int):
        raise ValueError("Policy returned a non-integer instruction")
    if not 0 <= instruction <= 0xFFFFFFFF:
        raise ValueError("Policy returned an instruction outside 32-bit range")


def smoke_test_policy_module(module: ModuleType, allow_instruction_blocks=False) -> None:
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


def extract_policy_regions(response: str):
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


def extract_python_region(response: str) -> str:
    _, body_region = extract_policy_regions(response)
    return body_region


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
        raise ValueError("Policy body region must return one instruction")

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


def validate_policy_region(region: str) -> None:
    validate_policy_regions("", region)


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


def replace_policy_region(policy_text: str, region: str) -> str:
    return replace_policy_regions(policy_text, "", region)


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
    current_policy_path,
    next_policy_path,
    llm_response,
    allow_instruction_blocks=False,
) -> str:
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


def summarize_coverage(coverage_plan: Dict[str, int], max_missed_bins=240) -> str:
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


def helper_reference() -> str:
    return (
        "Policy interface:\n"
        "- You receive coverage, a dict-like object keyed by coverage-bin name.\n"
        "- A bin is uncovered when coverage.get(bin_name, 0) == 0.\n"
        "- You receive rng, a random.Random-like object. Use rng.randint, "
        "rng.choice, rng.choices, rng.random, and rng.getrandbits as needed.\n"
        "- There are no predeclared instruction helpers in the policy file. "
        "Define any helper functions/constants you need in the helper region.\n"
        "- R-type ALU ops use opcode 0x33. Encodings are: "
        "ADD funct7=0x00 funct3=0, SUB 0x20/0, SLL 0x00/1, "
        "SLT 0x00/2, SLTU 0x00/3, XOR 0x00/4, SRL 0x00/5, "
        "SRA 0x20/5, OR 0x00/6, AND 0x00/7.\n"
        "- I-type ALU ops use opcode 0x13. Non-shift funct3 values are: "
        "ADDI 0, SLTI 2, SLTUI 3, XORI 4, ORI 6, ANDI 7. "
        "Shift immediates are SLLI funct7=0x00 funct3=1, "
        "SRLI 0x00/5, SRAI 0x20/5.\n"
        "- Loads use opcode 0x03: LB funct3=0, LH=1, LW=2. "
        "Stores use opcode 0x23: SB funct3=0, SH=1, SW=2.\n"
        "- Top-level op bins may include ADD, SUB, SLL, SLT, SLTU, XOR, SRL, "
        "SRA, OR, AND, immediate ALU bins such as ADDI or SUBI if present in "
        "coverage, load/store bins such as LB/LH/LW/SB/SH/SW, and "
        "illegal_instruction. Target the exact coverage keys shown below.\n"
        "- Cross-bin names follow OP_x_read_A_reg_N, OP_x_read_B_reg_N, "
        "and OP_x_write_reg_N where applicable, for example "
        "SUB_x_read_A_reg_7, XOR_x_read_B_reg_3, SRAI_x_write_reg_2, "
        "LH_x_read_A_reg_4, SH_x_read_B_reg_9.\n"
    )


def build_policy_refinement_prompt(
    policy_text: str,
    coverage_plan: Dict[str, int],
    previous_helper_region: str,
    previous_body_region: str,
    max_missed_bins=120,
) -> str:
    return (
        "You are improving a constrained-random instruction generator for a "
        "RISC-V decoder coverage test.\n"
        "The simulator/testbench interface is fixed. You may edit only two "
        "marked regions: BEGIN/END LLM HELPER REGION at module scope, and "
        "BEGIN/END LLM BODY REGION inside generate_instruction(coverage, rng).\n\n"
        "Rules:\n"
        "- Return only Python code containing both editable marker pairs.\n"
        "- Do not include markdown or explanations.\n"
        "- Do not include def generate_instruction(...); return only the helper "
        "region and the body region contents with their markers.\n"
        "- The code must return exactly one 32-bit instruction integer.\n"
        "- Define helper functions/constants in the helper region if you need "
        "encoders, bin matching, or reusable instruction-selection logic.\n"
        "- Helper-region functions run at module scope. They must not reference "
        "coverage or rng unless those values are explicit function arguments. "
        "A simple miss(name) helper that closes over coverage belongs in the "
        "body region, not the helper region.\n"
        "- Put per-call selection logic and the final return in the body region.\n"
        "- Use the unreached bin groups below to spread generation across all "
        "missing op families and register cross bins; do not keep targeting only "
        "ADD/ADDI/LW/SW if other families are missing.\n"
        "- Do not rely on helper functions from the policy file; the scaffold "
        "intentionally provides none.\n"
        "- Do not import modules, open files, define classes, or use exec/eval.\n"
        "- Keep some randomness so CRT can still explore.\n\n"
        "Good response shape:\n"
        "# BEGIN LLM HELPER REGION\n"
        "def enc_r(funct7, rs2, rs1, funct3, rd):\n"
        "    return (((funct7 & 0x7f) << 25) | ((rs2 & 31) << 20) | "
        "((rs1 & 31) << 15) | ((funct3 & 7) << 12) | "
        "((rd & 31) << 7) | 0x33)\n"
        "# END LLM HELPER REGION\n"
        "# BEGIN LLM BODY REGION\n"
        "def miss(name):\n"
        "    return coverage.get(name, 0) == 0\n"
        "# choose a target bin, synthesize one instruction, and return it\n"
        "# END LLM BODY REGION\n\n"
        f"{helper_reference()}\n"
        f"{summarize_coverage(coverage_plan, max_missed_bins)}\n\n"
        "CURRENT HELPER REGION:\n"
        "```python\n"
        f"{previous_helper_region}\n"
        "```\n\n"
        "CURRENT BODY REGION:\n"
        "```python\n"
        f"{previous_body_region}\n"
        "```\n"
    )


def append_policy_log(log_path, iteration, coverage_rate, prompt, response, region):
    with open(log_path, "a", encoding="UTF8") as f:
        f.write(f"\n===== ITERATION {iteration} =====\n")
        f.write(f"Coverage before refinement: {coverage_rate}\n\n")
        f.write("----- PROMPT -----\n")
        f.write(prompt)
        f.write("\n\n----- RESPONSE -----\n")
        f.write(response)
        f.write("\n\n----- ACCEPTED REGION -----\n")
        f.write(region)
        f.write("\n")


def append_policy_rejection_log(
    log_path,
    iteration,
    coverage_rate,
    prompt,
    rejected_attempts,
    current_policy_path,
):
    with open(log_path, "a", encoding="UTF8") as f:
        f.write(f"\n===== ITERATION {iteration} REJECTED =====\n")
        f.write(f"Coverage before refinement: {coverage_rate}\n")
        f.write(f"Continuing with existing policy: {current_policy_path}\n\n")
        f.write("----- FINAL PROMPT -----\n")
        f.write(prompt)
        for attempt_idx, response, error in rejected_attempts:
            f.write(f"\n\n----- REJECTED RESPONSE {attempt_idx} -----\n")
            f.write(response)
            f.write("\n\n----- VALIDATION ERROR -----\n")
            f.write(f"{type(error).__name__}: {error}\n")
