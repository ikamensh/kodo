"""Regression test: dashboard static assets must be packaged with kodo.

Without these files, `python -m kodo.dashboard` binds successfully but every
HTTP request crashes with FileNotFoundError on dashboard.html. Strict build
backends (uv, poetry-core) only include files explicitly declared in
[tool.setuptools.package-data], unlike pip+setuptools which auto-includes
tracked files. So the source tree carrying these files is necessary but not
sufficient — the package-data declaration must list them too.
"""

from __future__ import annotations

from pathlib import Path

import kodo.dashboard


def test_dashboard_static_files_present_in_source_tree() -> None:
    dashboard_dir = Path(kodo.dashboard.__file__).parent
    expected = ["dashboard.html", "dashboard.css", "dashboard.js"]
    missing = [name for name in expected if not (dashboard_dir / name).is_file()]
    assert not missing, f"dashboard static files missing from package: {missing}"


def test_dashboard_assets_declared_in_package_data() -> None:
    """Pin the explicit declaration so strict build backends (uv) include the files."""
    repo_root = Path(__file__).resolve().parent.parent
    pyproject = (repo_root / "pyproject.toml").read_text()
    for pattern in ("dashboard/*.html", "dashboard/*.css", "dashboard/*.js"):
        assert pattern in pyproject, (
            f"missing package-data pattern {pattern!r} in pyproject.toml — "
            f"strict build backends will exclude dashboard assets from the wheel"
        )
