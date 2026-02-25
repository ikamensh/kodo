"""Tests for kodo.factory module."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kodo.factory import (
    available_backends,
    build_orchestrator,
    clear_backend_cache,
    get_team,
)


def test_clear_backend_cache_invalidates():
    """clear_backend_cache() causes available_backends() to re-detect."""
    with patch("kodo.factory.shutil.which", return_value=None):
        clear_backend_cache()
        b1 = available_backends()
    assert all(not v for v in b1.values())

    with patch("kodo.factory.shutil.which", return_value="/usr/bin/fake"):
        clear_backend_cache()
        b2 = available_backends()
    assert all(v for v in b2.values())


def test_get_team_invalid():
    with pytest.raises(KeyError):
        get_team("nonexistent_mode")


def test_build_orchestrator_api():
    with patch("kodo.orchestrators.api.Summarizer"):
        orch = build_orchestrator("api", model="opus")
    assert type(orch).__name__ == "ApiOrchestrator"
    assert orch.model == "claude-opus-4-6"
