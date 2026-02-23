#!/usr/bin/env python3
"""Entry point for the buggy calculator."""

from calculator import add, divide, get_stats

if __name__ == "__main__":
    print("2 + 3 =", add(2, 3))
    print("10 / 2 =", divide(10, 2))
    print("Stats:", get_stats([1, 2, 3]))
