"""Smoke tests for CLI wrapper infrastructure."""

import re

import pytest

from tests.integration.cli_wrapper import run_kodo

pytestmark = pytest.mark.integration


def test_kodo_version() -> None:
    result = run_kodo("--version")
    assert result.success()
    assert re.search(r"\d+\.\d+\.\d+", result.stdout)
    assert result.duration > 0


def test_kodo_help() -> None:
    result = run_kodo("--help")
    assert result.success()
    assert "usage" in result.stdout.lower() or "kodo" in result.stdout.lower()


def test_kodo_invalid_subcommand() -> None:
    result = run_kodo("--nonexistent-flag-xyz")
    assert result.exit_code != 0
