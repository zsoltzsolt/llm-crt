"""Base editable CRT policy for Ibex decoder experiments."""


# BEGIN LLM HELPER REGION
# END LLM HELPER REGION


def generate_instruction(coverage, rng):
    # BEGIN LLM BODY REGION
    return rng.getrandbits(32)
    # END LLM BODY REGION
