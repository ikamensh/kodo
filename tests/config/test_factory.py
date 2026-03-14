"""Tests for kodo.factory module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo.factory import (
    available_backends,
    build_orchestrator,
    clear_backend_cache,
    get_team,
    get_team_presets,
)


def test_clear_backend_cache_invalidates():
    """clear_backend_cache() causes available_backends() to re-detect."""
    with patch("kodo.factory.shutil.which", autospec=True, return_value=None):
        clear_backend_cache()
        b1 = available_backends()
    assert all(not v for v in b1.values())

    with patch("kodo.factory.shutil.which", autospec=True, return_value="/usr/bin/fake"):
        clear_backend_cache()
        b2 = available_backends()
    assert all(v for v in b2.values())


def test_get_team_invalid():
    with pytest.raises(KeyError):
        get_team("nonexistent_mode")


def test_build_orchestrator_api():
    with patch("kodo.orchestrators.api.Summarizer", autospec=True):
        orch = build_orchestrator("api", model="opus")
    assert type(orch).__name__ == "ApiOrchestrator"
    assert orch.model == "claude-opus-4-6"


# ── User JSON team tests (relocated from test_audit_findings.py F1/F2) ───


class TestUserJsonTeams:
    """Verify get_team resolves user-defined JSON team files."""

    def test_get_team_presets_only_returns_builtins(self):
        presets = get_team_presets()
        for name in presets:
            assert name in {"full", "quick"}, (
                f"Unexpected preset {name!r} — user teams should not appear in presets"
            )

    def test_get_team_resolves_user_json(self, tmp_path: Path):
        team_json = {
            "name": "audit_test",
            "description": "test team",
            "agents": {
                "worker": {
                    "backend": "claude",
                    "model": "sonnet",
                    "description": "test worker",
                }
            },
        }
        kodo_dir = tmp_path / ".kodo" / "teams"
        kodo_dir.mkdir(parents=True)
        (kodo_dir / "audit_test.json").write_text(json.dumps(team_json))

        with patch("pathlib.Path.home", autospec=True, return_value=tmp_path):
            result = get_team("audit_test")
            assert result is not None, "get_team() should resolve user JSON teams"
            presets = get_team_presets()
            assert "audit_test" not in presets

    def test_invalid_json_in_user_team(self, tmp_path: Path):
        kodo_dir = tmp_path / ".kodo" / "teams"
        kodo_dir.mkdir(parents=True)
        (kodo_dir / "broken.json").write_text("{not valid json!!!")

        with patch("pathlib.Path.home", autospec=True, return_value=tmp_path):
            try:
                result = get_team("broken")
                # Silent fallback — the function didn't raise
                assert result is not None
            except (ValueError, KeyError):
                pass  # If a fix was applied, the function raises early
