"""Backend choices stay usable from discovery through team construction."""

from unittest.mock import patch

import pytest

from kodo import make_session
from kodo.factory import (
    available_backends,
    build_orchestrator,
    clear_backend_cache,
    get_team,
    preferred_orchestrator,
)
from kodo.team_config import build_team_from_json, team_to_json


@pytest.mark.parametrize("backend", ["unknown", "claud"])
def test_unknown_worker_backend_fails_clearly(backend):
    """A typo must not silently run a different provider."""
    with pytest.raises(ValueError, match="Unknown worker backend"):
        make_session(backend, model="test-model")


@pytest.mark.parametrize("orchestrator", ["unknown", "kiro-cli", "opencode"])
def test_unknown_orchestrator_fails_clearly(orchestrator):
    """Worker-only backends and typos cannot fall through to Claude orchestration."""
    with pytest.raises(ValueError, match="Unknown orchestrator"):
        build_orchestrator(orchestrator, model="test-model")


@pytest.mark.parametrize("worker_binary", ["opencode", "kiro-cli"])
@pytest.mark.parametrize("with_gemini", [False, True])
def test_worker_only_backend_does_not_select_unavailable_orchestrator(
    worker_binary, with_gemini
):
    """Worker-only CLIs must leave orchestration to a supported CLI or the API."""
    installed = {worker_binary}
    if with_gemini:
        installed.add("gemini")
    with patch(
        "shutil.which",
        autospec=True,
        side_effect=lambda name: f"/bin/{name}" if name in installed else None,
    ):
        clear_backend_cache()
        try:
            name = preferred_orchestrator()
            assert name == ("gemini-cli" if with_gemini else "api")
            orchestrator = build_orchestrator(name, model="test-model")
            assert orchestrator._orchestrator_name == name
        finally:
            clear_backend_cache()


@pytest.mark.parametrize(
    "backend,binary",
    [
        ("claude", "claude"),
        ("cursor", "cursor-agent"),
        ("codex", "codex"),
        ("gemini-cli", "gemini"),
        ("kimi", "kimi"),
        ("kiro", "kiro-cli"),
        ("opencode", "opencode"),
    ],
)
def test_discovered_backend_builds_and_reloads_a_team(backend, binary):
    """Every supported single backend can build workers and reload their snapshot."""
    teams = []
    with patch(
        "shutil.which",
        autospec=True,
        side_effect=lambda name: f"/bin/{name}" if name == binary else None,
    ):
        clear_backend_cache()
        try:
            assert available_backends()[backend]
            team = get_team("quick").build_team()
            teams.append(team)
            assert set(team) == {"worker_fast", "worker_smart"}
            snapshot = team_to_json(team)
            assert all(
                agent["backend"] == backend for agent in snapshot["agents"].values()
            )
            restored = build_team_from_json(snapshot)
            teams.append(restored)
            assert set(restored) == set(team)
            for role, agent in restored.items():
                assert type(agent.session) is type(team[role].session)
                assert agent.session.model == team[role].session.model
        finally:
            for team in teams:
                for agent in team.values():
                    agent.session.close()
            clear_backend_cache()
