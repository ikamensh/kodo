from __future__ import annotations

import importlib.resources
import tomllib
from pathlib import Path

import kodo


def test_runtime_version_matches_package_metadata():
    """The CLI version and wheel metadata should name the same release."""
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert kodo.__version__ == pyproject["project"]["version"]


def test_runtime_assets_are_available_from_packages():
    """Bundled defaults and web assets should be present after installation."""
    kodo_files = importlib.resources.files("kodo")
    benchmark_files = importlib.resources.files("benchmark")

    expected = [
        kodo_files / "viewer.html",
        kodo_files / "defaults" / "team-full.json",
        kodo_files / "defaults" / "team-quick.json",
        kodo_files / "dashboard" / "dashboard.html",
        kodo_files / "dashboard" / "dashboard.css",
        kodo_files / "dashboard" / "dashboard.js",
        benchmark_files / "online" / "static" / "index.html",
        benchmark_files / "online" / "static" / "methodology.md",
    ]

    assert all(path.is_file() for path in expected)
