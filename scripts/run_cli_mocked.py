"""Run kodo CLI with mocked sessions for a successful end-to-end test.

Usage:
    uv run python scripts/run_cli_mocked.py

All agents and orchestrator are mocked. No API keys or real backends required.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch

from kodo.agent import Agent
from kodo.cli import _main_inner
from kodo.orchestrators.base import CycleResult, RunResult
from tests.conftest import FakeSession


def _make_fake_session(*, response_text: str = "Task completed successfully."):
    return FakeSession(response_text=response_text)


def _fake_make_session(backend: str, model: str, **kwargs) -> FakeSession:
    return _make_fake_session()


def _fake_build_team():
    """Build a minimal team with FakeSession agents."""
    session = _make_fake_session()
    agent = Agent(session, "Test agent", max_turns=5)
    return {"worker_fast": agent, "worker_smart": agent}


def _fake_build_orchestrator(*args, **kwargs):
    """Return an orchestrator whose run() returns a successful RunResult."""
    mock = MagicMock()
    mock.model = "mock"
    mock.run.return_value = RunResult(
        cycles=[CycleResult(exchanges=1, finished=True, success=True, summary="Done.")]
    )
    return mock


def main():
    project_dir = Path(__file__).resolve().parent.parent / "tmp_mock_run"
    project_dir.mkdir(exist_ok=True)

    # Redirect runs to a temp location
    runs_tmp = project_dir / "runs"
    runs_tmp.mkdir(exist_ok=True)

    with (
        patch("kodo.cli.make_session", side_effect=_fake_make_session),
        patch("kodo.factory.make_session", side_effect=_fake_make_session),
        patch("kodo.factory.has_claude", return_value=True),
        patch("kodo.factory.has_cursor", return_value=True),
        patch("kodo.factory.has_codex", return_value=False),
        patch("kodo.factory.has_gemini_cli", return_value=False),
        patch("kodo.cli.has_claude", return_value=True),
        patch("kodo.cli.has_cursor", return_value=True),
        patch("kodo.cli.check_api_key", return_value=None),
        patch("kodo.factory._build_team_mission", _fake_build_team),
        patch("kodo.factory._build_team_saga", _fake_build_team),
        patch("kodo.cli.build_orchestrator", side_effect=_fake_build_orchestrator),
        patch("kodo.log._runs_root", return_value=runs_tmp),
    ):
        sys.argv = [
            "kodo",
            "--goal",
            "Echo hello world",
            "--yes",
            "--mode",
            "quick",
            "--skip-intake",
            "--orchestrator",
            "api",
            "--orchestrator-model",
            "opus",
            str(project_dir),
        ]
        _main_inner()


if __name__ == "__main__":
    main()
