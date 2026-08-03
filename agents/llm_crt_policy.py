# Copyright Zixi Zhang
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Minimal editable CRT policy for Ibex decoder experiments.

The driver copies this file into a log directory and lets the LLM replace the
two marked regions. The scaffold intentionally does not provide instruction
helpers; the LLM must define any helpers it wants to use in the helper region.
"""


# BEGIN LLM HELPER REGION
# END LLM HELPER REGION


def generate_instruction(coverage, rng):
    # BEGIN LLM BODY REGION
    return rng.getrandbits(32)
    # END LLM BODY REGION
