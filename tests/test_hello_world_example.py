"""Executable check: examples/hello_world.py prints exactly one hello line.

Run: uv run pytest tests/test_hello_world_example.py -q
Or:  uv run python examples/hello_world.py
"""

import contextlib
import io
import runpy
from pathlib import Path


def test_hello_world_prints_exact_message() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "examples" / "hello_world.py"
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        runpy.run_path(str(script), run_name="__main__")
    assert buf.getvalue() == "Hello, world!\n"
