"""Tests for team JSON configuration loading and building."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo.orchestrators.verification import verify_done
from kodo.team_config import build_team_from_json, load_team_config
from tests.conftest import FakeSession, make_agent

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    return tmp_path


MINIMAL_CONFIG = {
    "name": "test-team",
    "agents": {
        "worker": {
            "backend": "claude",
            "model": "opus",
            "description": "A test worker",
        }
    },
}


def _write_team_json(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))


# ---------------------------------------------------------------------------
# load_team_config — file lookup priority
# ---------------------------------------------------------------------------


class TestLoadTeamConfig:
    def test_project_level_takes_priority(self, tmp_project: Path):
        project_team = tmp_project / ".kodo" / "team.json"
        _write_team_json(project_team, {**MINIMAL_CONFIG, "name": "project"})

        with patch("kodo.team_config.Path.home", return_value=tmp_project / "fakehome"):
            user_team = tmp_project / "fakehome" / ".kodo" / "teams" / "full.json"
            _write_team_json(user_team, {**MINIMAL_CONFIG, "name": "user"})

            result = load_team_config("full", tmp_project)
            assert result is not None
            assert result["name"] == "project"

    def test_user_level_fallback(self, tmp_project: Path):
        home = tmp_project / "fakehome"
        user_team = home / ".kodo" / "teams" / "full.json"
        _write_team_json(user_team, {**MINIMAL_CONFIG, "name": "user-full"})

        with patch("kodo.team_config.Path.home", return_value=home):
            result = load_team_config("full", tmp_project)
            assert result is not None
            assert result["name"] == "user-full"

    def test_returns_none_when_no_config(self, tmp_project: Path):
        with patch("kodo.team_config.Path.home", return_value=tmp_project / "fakehome"):
            result = load_team_config("full", tmp_project)
            assert result is None

    def test_invalid_json_raises(self, tmp_project: Path):
        bad_file = tmp_project / ".kodo" / "team.json"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text("not json {{{")

        with pytest.raises(ValueError, match="Invalid JSON"):
            load_team_config("full", tmp_project)

    def test_missing_agents_key_raises(self, tmp_project: Path):
        bad_config = tmp_project / ".kodo" / "team.json"
        _write_team_json(bad_config, {"name": "bad"})

        with pytest.raises(ValueError, match="agents"):
            load_team_config("full", tmp_project)


# ---------------------------------------------------------------------------
# build_team_from_json
# ---------------------------------------------------------------------------


class TestBuildTeamFromJson:
    @patch(
        "kodo.team_config.available_backends",
        return_value={
            "claude": True,
            "cursor": True,
            "codex": False,
            "gemini-cli": False,
        },
    )
    @patch("kodo.team_config.make_session")
    def test_builds_team_with_available_backends(
        self, mock_make_session, mock_backends
    ):
        mock_make_session.return_value = FakeSession()

        config = {
            "agents": {
                "worker": {
                    "backend": "claude",
                    "model": "opus",
                    "description": "A worker",
                    "max_turns": 25,
                    "timeout_s": 900,
                },
                "tester": {
                    "backend": "cursor",
                    "model": "composer-1.5",
                    "description": "A tester",
                    "system_prompt": "You are a tester",
                },
            }
        }

        team = build_team_from_json(config)
        assert "worker" in team
        assert "tester" in team
        assert team["worker"].max_turns == 25
        assert team["worker"].timeout_s == 900
        assert team["tester"].max_turns == 15  # default

    @patch(
        "kodo.team_config.available_backends",
        return_value={
            "claude": True,
            "cursor": False,
            "codex": False,
            "gemini-cli": False,
        },
    )
    @patch("kodo.team_config.make_session")
    def test_skips_unavailable_backend(self, mock_make_session, mock_backends):
        mock_make_session.return_value = FakeSession()

        config = {
            "agents": {
                "worker": {
                    "backend": "claude",
                    "model": "opus",
                },
                "fast_worker": {
                    "backend": "cursor",
                    "model": "composer-1.5",
                },
            }
        }

        team = build_team_from_json(config)
        assert "worker" in team
        assert "fast_worker" not in team

    @patch(
        "kodo.team_config.available_backends",
        return_value={
            "claude": False,
            "cursor": False,
            "codex": False,
            "gemini-cli": False,
        },
    )
    def test_all_backends_missing_raises(self, mock_backends):
        config = {
            "agents": {
                "worker": {"backend": "claude", "model": "opus"},
            }
        }

        with pytest.raises(RuntimeError, match="No agents available"):
            build_team_from_json(config)

    def test_missing_backend_field_raises(self):
        config = {
            "agents": {
                "worker": {"model": "opus"},
            }
        }

        with pytest.raises(ValueError, match="backend"):
            build_team_from_json(config)

    def test_unknown_backend_raises(self):
        config = {
            "agents": {
                "worker": {"backend": "unknown", "model": "x"},
            }
        }

        with pytest.raises(ValueError, match="unknown backend"):
            build_team_from_json(config)

    @patch(
        "kodo.team_config.available_backends",
        return_value={
            "claude": True,
            "cursor": False,
            "codex": False,
            "gemini-cli": False,
        },
    )
    @patch("kodo.team_config.make_session")
    def test_chrome_and_fallback_model_passed(self, mock_make_session, mock_backends):
        mock_make_session.return_value = FakeSession()

        config = {
            "agents": {
                "worker": {
                    "backend": "claude",
                    "model": "opus",
                    "chrome": True,
                    "fallback_model": "sonnet",
                },
            }
        }

        build_team_from_json(config)
        mock_make_session.assert_called_once_with(
            "claude",
            "opus",
            system_prompt=None,
            chrome=True,
            fallback_model="sonnet",
            session_timeout_s=7200,
        )


# ---------------------------------------------------------------------------
# verify_done with verifiers parameter
# ---------------------------------------------------------------------------


class TestVerifyDoneWithVerifiers:
    def _make_team(self):
        return {
            "my_worker": make_agent("ALL CHECKS PASS"),
            "my_tester": make_agent("ALL CHECKS PASS"),
            "my_reviewer": make_agent("ALL CHECKS PASS"),
            "my_browser_tester": make_agent("ALL CHECKS PASS"),
        }

    def test_custom_verifiers_used(self, tmp_project: Path):
        team = self._make_team()
        verifiers = {
            "testers": ["my_tester"],
            "browser_testers": ["my_browser_tester"],
            "reviewers": ["my_reviewer"],
        }

        result = verify_done(
            "test goal",
            "all done",
            team,
            tmp_project,
            verifiers=verifiers,
        )
        assert result is None  # all pass

    def test_custom_verifier_rejects(self, tmp_project: Path):
        team = {
            "worker": make_agent("ALL CHECKS PASS"),
            "strict_reviewer": make_agent("Found a bug in line 42"),
        }
        verifiers = {
            "testers": [],
            "browser_testers": [],
            "reviewers": ["strict_reviewer"],
        }

        result = verify_done(
            "test goal",
            "done",
            team,
            tmp_project,
            verifiers=verifiers,
        )
        assert result is not None
        assert "Strict Reviewer" in result
        assert "bug" in result

    def test_empty_verifiers_skips_verification(self, tmp_project: Path):
        team = {
            "worker": make_agent("ALL CHECKS PASS"),
        }
        verifiers = {
            "testers": [],
            "browser_testers": [],
            "reviewers": [],
        }

        # With empty verifiers, no dedicated verifiers exist,
        # so fallback to worker verification
        result = verify_done(
            "test goal",
            "done",
            team,
            tmp_project,
            verifiers=verifiers,
        )
        # Fallback uses worker — which returns ALL CHECKS PASS
        assert result is None

    def test_browser_testers_only_run_when_browser_testing(self, tmp_project: Path):
        team = {
            "worker": make_agent("ALL CHECKS PASS"),
            "bt": make_agent("BROWSER BROKEN"),
        }
        verifiers = {
            "testers": [],
            "browser_testers": ["bt"],
            "reviewers": [],
        }

        # browser_testing=False → browser tester skipped, but has_dedicated_verifiers
        # is True because browser_tester_keys is non-empty
        result = verify_done(
            "test goal",
            "done",
            team,
            tmp_project,
            verifiers=verifiers,
            browser_testing=False,
        )
        assert result is None  # bt not run

        # browser_testing=True → browser tester runs and rejects
        result = verify_done(
            "test goal",
            "done",
            team,
            tmp_project,
            verifiers=verifiers,
            browser_testing=True,
        )
        assert result is not None
        assert "BROWSER BROKEN" in result

    def test_legacy_fallback_when_verifiers_none(self, tmp_project: Path):
        """When verifiers=None, legacy key matching is used."""
        team = {
            "worker": make_agent("ALL CHECKS PASS"),
            "tester": make_agent("ALL CHECKS PASS"),
            "architect": make_agent("ALL CHECKS PASS"),
        }

        result = verify_done(
            "test goal",
            "done",
            team,
            tmp_project,
            verifiers=None,
        )
        assert result is None

    def test_multiple_testers(self, tmp_project: Path):
        team = {
            "tester_api": make_agent("ALL CHECKS PASS"),
            "tester_e2e": make_agent("E2E test failed"),
        }
        verifiers = {
            "testers": ["tester_api", "tester_e2e"],
            "browser_testers": [],
            "reviewers": [],
        }

        result = verify_done(
            "test goal",
            "done",
            team,
            tmp_project,
            verifiers=verifiers,
        )
        assert result is not None
        assert "tester_e2e" in result
