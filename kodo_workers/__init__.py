"""Source-tree shim for the vendored kodo_workers package.

The PyPI wheel maps ``kodo_workers`` to ``kodo_workers/src/kodo_workers`` via
``pyproject.toml``.  When running directly from a checkout, Python sees this
outer directory first, so this shim executes the real package initializer and
points submodule imports at the nested source tree.
"""

from __future__ import annotations

from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent / "src" / "kodo_workers"
__path__ = [str(_PKG_DIR)]

_init = _PKG_DIR / "__init__.py"
exec(compile(_init.read_text(), str(_init), "exec"), globals())
