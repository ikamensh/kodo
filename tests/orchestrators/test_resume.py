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


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
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

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
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


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
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

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        orch.run("goal", tmp_project, team, max_cycles=3, resume=resume)

    assert orch._cycle_calls[0]["prior_summary"] == "here is what happened before"


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_normal_run_unchanged(mock_viewer, tmp_project):
    """Without resume, run starts at cycle 1 with empty prior_summary."""
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run("goal", tmp_project, team, max_cycles=5)

    assert len(orch._cycle_calls) == 1
    assert orch._cycle_calls[0]["prior_summary"] == ""
    assert result.finished is True


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_keyboard_interrupt_emits_run_end(mock_viewer, tmp_project):
    """KeyboardInterrupt during cycle loop still emits run_end via try/finally."""

    class InterruptOrchestrator(FakeOrchestrator):
        def cycle(self, *args, **kwargs):
            raise KeyboardInterrupt()

    orch = InterruptOrchestrator()
    team = {"worker": make_agent()}

    with pytest.raises(KeyboardInterrupt):
        with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
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


# ── inject_resume_sessions() direct tests ────────────────────────────────


class TestInjectResumeSessions:
    """Verify inject_resume_sessions dispatches correctly per session type."""

    def _make_team(self, session, role: str = "worker"):
        from kodo.agent import Agent

        return {role: Agent(session, "test agent")}

    def test_noop_when_resume_is_none(self):
        from kodo.orchestrators.resume import inject_resume_sessions

        team = self._make_team(MagicMock())
        inject_resume_sessions(team, None)
        # No exception, no attribute set — just a no-op

    @patch("kodo.sessions.claude.ClaudeSession.__init__", autospec=True, side_effect=lambda self, **kw: None)
    def test_claude_session_sets_resume_session_id(self, _mock_init):
        from kodo.orchestrators.resume import inject_resume_sessions
        from kodo.sessions.claude import ClaudeSession

        sess = ClaudeSession.__new__(ClaudeSession)
        sess.resume_session_id = None
        team = self._make_team(sess)

        resume = ResumeState(
            completed_cycles=1,
            prior_summary="",
            agent_session_ids={"worker": "claude-sid-abc"},
            completed_stages=[],
            stage_summaries=[],
            current_stage_cycles=0,
        )
        inject_resume_sessions(team, resume)
        assert sess.resume_session_id == "claude-sid-abc"

    def test_cursor_session_sets_chat_id(self):
        from kodo.orchestrators.resume import inject_resume_sessions
        from kodo.sessions.cursor import CursorSession

        sess = CursorSession.__new__(CursorSession)
        sess._chat_id = None
        team = self._make_team(sess)

        resume = ResumeState(
            completed_cycles=1,
            prior_summary="",
            agent_session_ids={"worker": "cursor-chat-123"},
            completed_stages=[],
            stage_summaries=[],
            current_stage_cycles=0,
        )
        inject_resume_sessions(team, resume)
        assert sess._chat_id == "cursor-chat-123"

    def test_codex_session_sets_session_id(self):
        from kodo.orchestrators.resume import inject_resume_sessions
        from kodo.sessions.codex import CodexSession

        sess = CodexSession.__new__(CodexSession)
        sess._session_id = None
        team = self._make_team(sess)

        resume = ResumeState(
            completed_cycles=1,
            prior_summary="",
            agent_session_ids={"worker": "codex-sid-456"},
            completed_stages=[],
            stage_summaries=[],
            current_stage_cycles=0,
        )
        inject_resume_sessions(team, resume)
        assert sess._session_id == "codex-sid-456"

    def test_gemini_session_sets_resume_next(self):
        from kodo.orchestrators.resume import inject_resume_sessions
        from kodo.sessions.gemini_cli import GeminiCliSession

        sess = GeminiCliSession.__new__(GeminiCliSession)
        sess._resume_next = False
        team = self._make_team(sess)

        resume = ResumeState(
            completed_cycles=1,
            prior_summary="",
            agent_session_ids={"worker": "gemini-sid-789"},
            completed_stages=[],
            stage_summaries=[],
            current_stage_cycles=0,
        )
        inject_resume_sessions(team, resume)
        assert sess._resume_next is True

    def test_unknown_agent_name_skipped(self):
        from kodo.orchestrators.resume import inject_resume_sessions
        from tests.conftest import FakeSession

        sess = FakeSession()
        team = self._make_team(sess)

        resume = ResumeState(
            completed_cycles=1,
            prior_summary="",
            agent_session_ids={"nonexistent_role": "sid-xxx"},
            completed_stages=[],
            stage_summaries=[],
            current_stage_cycles=0,
        )
        # Should not raise — just skips the unknown agent
        inject_resume_sessions(team, resume)

    def test_unrecognized_session_type_ignored(self):
        from kodo.orchestrators.resume import inject_resume_sessions
        from tests.conftest import FakeSession

        sess = FakeSession()
        team = self._make_team(sess)

        resume = ResumeState(
            completed_cycles=1,
            prior_summary="",
            agent_session_ids={"worker": "sid-yyy"},
            completed_stages=[],
            stage_summaries=[],
            current_stage_cycles=0,
        )
        # FakeSession doesn't match any isinstance branch — should be a no-op
        inject_resume_sessions(team, resume)
        assert not hasattr(sess, "_chat_id")
        assert not hasattr(sess, "_session_id")

    def test_multiple_agents_different_session_types(self):
        from kodo.agent import Agent
        from kodo.orchestrators.resume import inject_resume_sessions
        from kodo.sessions.claude import ClaudeSession
        from kodo.sessions.cursor import CursorSession

        claude_sess = ClaudeSession.__new__(ClaudeSession)
        claude_sess.resume_session_id = None
        cursor_sess = CursorSession.__new__(CursorSession)
        cursor_sess._chat_id = None

        team = {
            "worker_smart": Agent(claude_sess, "smart"),
            "worker_fast": Agent(cursor_sess, "fast"),
        }

        resume = ResumeState(
            completed_cycles=2,
            prior_summary="",
            agent_session_ids={
                "worker_smart": "claude-id",
                "worker_fast": "cursor-id",
            },
            completed_stages=[],
            stage_summaries=[],
            current_stage_cycles=0,
        )
        inject_resume_sessions(team, resume)
        assert claude_sess.resume_session_id == "claude-id"
        assert cursor_sess._chat_id == "cursor-id"
