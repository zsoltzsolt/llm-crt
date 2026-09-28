# Copyright 2026 LLM-CRT Contributors.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

"""Base editable CRT policy for MIPS CPU experiments."""


# BEGIN LLM HELPER REGION
# END LLM HELPER REGION


def generate_instruction(coverage, rng):
    # BEGIN LLM BODY REGION
    return rng.getrandbits(32)
    # END LLM BODY REGION
