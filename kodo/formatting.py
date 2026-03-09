"""Shared formatting constants and helpers.

Zero imports from kodo — safe to use anywhere without circular-import risk.
"""

# ANSI escape sequences
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def plural(n: int, word: str) -> str:
    """Return e.g. '1 cycle' or '3 cycles'. Handles simple English plurals."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
