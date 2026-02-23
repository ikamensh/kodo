"""Simple calculator with intentional bugs for testing kodo --improve."""


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def divide(a, b):
    # Bug: no check for zero divisor
    return a / b


def get_stats(numbers):
    """Return count, sum, and average. Bug: crashes on empty list (NameError)."""
    if len(numbers) == 0:
        return {"count": count, "sum": 0, "avg": 0}  # NameError: count undefined
    count = len(numbers)
    total = sum(numbers)
    return {"count": count, "sum": total, "avg": total / count}
