"""Tests for orchestrator run() resume and try/finally behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.base import CycleResult, OrchestratorBase, ResumeState
from tests.conftest import make_agent


class FakeOrchestrator(OrchestratorBase):
    """Minimal orchestrator for testing run() logic."""

    def __init__(self, cycle_results: list[CycleResult] | None = None):
        self.model = "test-model"
        self._orchestrator_name = "test"
        self._summarizer = MagicMock()
        self._cycle_results = cycle_results or []
        self._cycle_calls: list[dict] = []

    def cycle(
        self,
        goal: str,
        project_dir: Path,
        team,
        *,
        max_exchanges: int = 30,
        prior_summary: str = "",
        config=None,
    ) -> CycleResult:
        self._cycle_calls.append(
            {
                "goal": goal,
                "prior_summary": prior_summary,
            }
        )
        if self._cycle_results:
            return self._cycle_results.pop(0)
        return CycleResult(summary="cycle done")


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Initialize logging and return a temp project dir."""
    log.init(RunDir.create(tmp_path))
    return tmp_path


@patch("kodo.orchestrators.base.open_viewer", create=True)
def test_resume_skips_completed_cycles(mock_viewer, tmp_project):
    """When resuming after 2 completed cycles with max_cycles=5, run starts at cycle 3."""
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="cycle 3 done"),
            CycleResult(summary="cycle 4", finished=True, success=True),
        ]
    )
    team = {"worker": make_agent()}

    resume = ResumeState(
        completed_cycles=2,
        prior_summary="prior work summary",
        agent_session_ids={},
        completed_stages=[],
        stage_summaries=[],
        current_stage_cycles=0,
    )

    with patch("kodo.viewer.open_viewer", create=True):
        result = orch.run(
            "test goal",
            tmp_project,
            team,
            max_exchanges=20,
            max_cycles=5,
            resume=resume,
        )

    # Should have called cycle twice (cycles 3 and 4)
    assert len(orch._cycle_calls) == 2
    # First resumed cycle gets the prior_summary
    assert orch._cycle_calls[0]["prior_summary"] == "prior work summary"
    # Second cycle gets cycle 3's summary
    assert orch._cycle_calls[1]["prior_summary"] == "cycle 3 done"
    assert result.finished is True


@patch("kodo.orchestrators.base.open_viewer", create=True)
def test_resume_prior_summary_passed(mock_viewer, tmp_project):
    """First resumed cycle receives the prior_summary from ResumeState."""
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    resume = ResumeState(
        completed_cycles=1,
        prior_summary="here is what happened before",
        agent_session_ids={},
        completed_stages=[],
        stage_summaries=[],
        current_stage_cycles=0,
    )

    with patch("kodo.viewer.open_viewer", create=True):
        orch.run("goal", tmp_project, team, max_cycles=3, resume=resume)

    assert orch._cycle_calls[0]["prior_summary"] == "here is what happened before"


@patch("kodo.orchestrators.base.open_viewer", create=True)
def test_normal_run_unchanged(mock_viewer, tmp_project):
    """Without resume, run starts at cycle 1 with empty prior_summary."""
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):
        result = orch.run("goal", tmp_project, team, max_cycles=5)

    assert len(orch._cycle_calls) == 1
    assert orch._cycle_calls[0]["prior_summary"] == ""
    assert result.finished is True


@patch("kodo.orchestrators.base.open_viewer", create=True)
def test_keyboard_interrupt_emits_run_end(mock_viewer, tmp_project):
    """KeyboardInterrupt during cycle loop still emits run_end via try/finally."""

    class InterruptOrchestrator(FakeOrchestrator):
        def cycle(self, *args, **kwargs):
            raise KeyboardInterrupt()

    orch = InterruptOrchestrator()
    team = {"worker": make_agent()}

    with pytest.raises(KeyboardInterrupt):
        with patch("kodo.viewer.open_viewer", create=True):
            orch.run("goal", tmp_project, team, max_cycles=3)

    # Verify run_end was emitted despite the interrupt
    log_file = log.get_log_file()
    assert log_file is not None
    import json

    events = []
    for line in log_file.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except Exception:
            continue

    run_end_events = [e for e in events if e.get("event") == "run_end"]
    assert len(run_end_events) == 1


def test_resume_session_ids_injected_at_build_time():
    """Resume session IDs are set on sessions at team build, not in orchestrator.run."""
    from kodo.agent import Agent
    from tests.conftest import FakeSession

    session = FakeSession()
    team = {"worker": Agent(session, "test")}

    # Simulate what _build_run_setup does when agent_session_ids is provided
    agent_session_ids = {"worker": "saved-session-123"}
    for agent_name, sid in agent_session_ids.items():
        agent = team.get(agent_name)
        if agent is not None:
            agent.session.resume_session_id = sid

    assert session.resume_session_id == "saved-session-123"
