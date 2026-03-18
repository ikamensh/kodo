"""Tests for auto-commit after successful verification."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from tests.conftest import _GIT_ENV

import pytest

from kodo import log
from kodo.agent import Agent
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator
from kodo.orchestrators.base import (
    CycleConfig,
    DoneSignal,
    QuickCheck,
    _auto_commit,
)
from kodo.orchestrators.verification import handle_done
from kodo.sessions.base import QueryResult
from tests.conftest import FakeRunResult, FakeSession, make_agent

GOAL = "Build a hello-world web server."
SUMMARY = "Implemented hello-world server on port 8000."


# ---------------------------------------------------------------------------
# GitCommitSession — a session that actually runs git add + commit
# ---------------------------------------------------------------------------


class GitCommitSession(FakeSession):
    """Session that executes real git add/commit when it receives a directive.

    Simulates what a real worker (Claude Code, Cursor) would do: parse the
    prompt, stage files, and commit.
    """

    def query(self, prompt: str, project_dir: Path, *, max_turns: int = 10):
        self.prompts.append(prompt)
        self._stats.queries += 1

        # Simulate the agent running git commands
        subprocess.run(
            ["git", "add", "-A"],
            cwd=project_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "kodo: auto-commit completed work"],
            cwd=project_dir,
            capture_output=True,
            check=True,
        )

        return QueryResult(
            text="Committed all changes.",
            elapsed_s=0.1,
            is_error=False,
        )


@pytest.fixture
def git_project(git_project: Path) -> Path:
    """Extend the shared git_project with uncommitted changes for auto-commit tests."""
    project = git_project

    # Initial tracked file
    (project / "README.md").write_text("# Hello")
    subprocess.run(["git", "add", "-A"], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=project,
        capture_output=True,
        check=True,
        env=_GIT_ENV,
    )

    # Now create uncommitted work (simulates what the worker_fast produced)
    (project / "app.py").write_text("print('hello world')\n")
    (project / "README.md").write_text("# Hello World Server\n")

    return project


def test_auto_commit_dispatches_worker_fast(tmp_project: Path) -> None:
    """_auto_commit sends a commit directive to worker_fast."""
    worker = make_agent("Committed: abc123")
    team = {"worker_fast": worker, "worker_smart": make_agent("ok")}

    _auto_commit(team, tmp_project, SUMMARY)

    session = worker.session
    assert len(session.prompts) == 1
    assert (
        "git diff" in session.prompts[0].lower() or "git" in session.prompts[0].lower()
    )
    assert "commit" in session.prompts[0].lower()
    assert SUMMARY in session.prompts[0]


def test_auto_commit_falls_back_to_worker_smart(tmp_project: Path) -> None:
    """When worker_fast is absent, _auto_commit uses worker_smart."""
    worker = make_agent("Committed: def456")
    team = {"worker_smart": worker}

    _auto_commit(team, tmp_project, SUMMARY)

    assert len(worker.session.prompts) == 1
    assert "commit" in worker.session.prompts[0].lower()


def test_handle_done_calls_auto_commit_on_success(tmp_project: Path) -> None:
    """handle_done triggers _auto_commit when auto_commit=True and verification passes."""
    team = {
        "tester": make_agent("ALL CHECKS PASS"),
        "architect": make_agent("ALL CHECKS PASS"),
        "worker_fast": make_agent("Committed"),
    }
    done_signal = DoneSignal()

    result = handle_done(
        SUMMARY,
        True,
        done_signal,
        GOAL,
        team,
        tmp_project,
        config=CycleConfig(auto_commit=True),
    )

    assert "Verified and accepted" in result
    assert done_signal.called
    assert done_signal.success
    # worker_fast should have been called with commit directive
    worker_session = team["worker_fast"].session
    assert len(worker_session.prompts) == 1
    assert "commit" in worker_session.prompts[0].lower()


def test_handle_done_skips_auto_commit_when_disabled(tmp_project: Path) -> None:
    """handle_done does NOT call _auto_commit when auto_commit=False."""
    team = {
        "tester": make_agent("ALL CHECKS PASS"),
        "architect": make_agent("ALL CHECKS PASS"),
        "worker_fast": make_agent("Should not be called"),
    }
    done_signal = DoneSignal()

    result = handle_done(
        SUMMARY,
        True,
        done_signal,
        GOAL,
        team,
        tmp_project,
        config=CycleConfig(auto_commit=False),
    )

    assert "Verified and accepted" in result
    # worker_fast should NOT have been called
    worker_session = team["worker_fast"].session
    assert len(worker_session.prompts) == 0


def test_handle_done_skips_auto_commit_on_quick_check_rejection(
    tmp_project: Path,
) -> None:
    """handle_done does NOT call _auto_commit when quick-check rejects."""
    from kodo.orchestrators.base import QuickCheck

    team = {"worker_fast": make_agent("Should not be called")}
    done_signal = DoneSignal()
    checks = [
        QuickCheck(
            path=str(tmp_project / "nonexistent.md"),
            description="Required file",
            error_message="Missing file",
        ),
    ]

    result = handle_done(
        SUMMARY,
        True,
        done_signal,
        GOAL,
        team,
        tmp_project,
        config=CycleConfig(auto_commit=True, verification=checks),
    )

    assert "Quick-check verification failed" in result
    # worker_fast should NOT have been called for commit
    worker_session = team["worker_fast"].session
    assert len(worker_session.prompts) == 0


def test_handle_done_skips_auto_commit_on_failure(tmp_project: Path) -> None:
    """handle_done does NOT call _auto_commit when success=False."""
    team = {"worker_fast": make_agent("Should not be called")}
    done_signal = DoneSignal()

    result = handle_done(
        SUMMARY,
        False,
        done_signal,
        GOAL,
        team,
        tmp_project,
        config=CycleConfig(auto_commit=True),
    )

    assert "unsuccessful" in result.lower()
    worker_session = team["worker_fast"].session
    assert len(worker_session.prompts) == 0


# ---------------------------------------------------------------------------
# Integration: auto-commit fires through a full ApiOrchestrator.cycle()
# ---------------------------------------------------------------------------


def _make_team_with_tracked_worker():
    """Create a team with worker_fast whose FakeSession records prompts."""
    worker_fast = make_agent("Committed abc123")
    tester = make_agent("ALL CHECKS PASS")
    architect = make_agent("ALL CHECKS PASS")
    return {
        "worker_fast": worker_fast,
        "tester": tester,
        "architect": architect,
    }


def test_cycle_auto_commit_fires_on_done(tmp_path: Path) -> None:
    """Full cycle: orchestrator calls done → verification passes → worker_fast
    receives a commit directive. Pydantic-ai Agent is mocked, team agents are
    real FakeSessions."""
    log.init(RunDir.create(tmp_path, "auto_commit_cycle"))

    team = _make_team_with_tracked_worker()
    agent_tools = []

    def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
        # Simulate orchestrator calling the done tool
        done_tool = next(t for t in agent_tools if t.name == "done")
        done_tool.function(summary="feature complete", success=True)
        return FakeRunResult()

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    with (
        patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ),
        patch(
            "kodo.orchestrators.verification.verify_done",
            autospec=True,
            return_value=None,
        ),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle(
            GOAL,
            tmp_path,
            team,
            max_exchanges=10,
            config=CycleConfig(auto_commit=True, done_mode="legacy"),
        )

    assert result.finished is True

    # worker_fast should have been dispatched with a commit directive
    wf_session = team["worker_fast"].session
    assert len(wf_session.prompts) == 1
    assert "commit" in wf_session.prompts[0].lower()
    assert "feature complete" in wf_session.prompts[0]


def test_cycle_no_auto_commit_when_disabled(tmp_path: Path) -> None:
    """Full cycle with auto_commit=False: worker_fast is never asked to commit."""
    log.init(RunDir.create(tmp_path, "no_auto_commit_cycle"))

    team = _make_team_with_tracked_worker()
    agent_tools = []

    def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
        done_tool = next(t for t in agent_tools if t.name == "done")
        done_tool.function(summary="feature complete", success=True)
        return FakeRunResult()

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    with (
        patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ),
        patch(
            "kodo.orchestrators.verification.verify_done",
            autospec=True,
            return_value=None,
        ),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle(
            GOAL,
            tmp_path,
            team,
            max_exchanges=10,
            config=CycleConfig(auto_commit=False, done_mode="legacy"),
        )

    assert result.finished is True
    # worker_fast should NOT have been called
    wf_session = team["worker_fast"].session
    assert len(wf_session.prompts) == 0


def test_cycle_auto_commit_skipped_on_rejection(tmp_path: Path) -> None:
    """Full cycle where verification rejects: no commit dispatch."""
    log.init(RunDir.create(tmp_path, "auto_commit_rejected"))

    team = _make_team_with_tracked_worker()
    agent_tools = []

    def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
        # Only call done if we have tools (orchestrator agent, not summarizer)
        done_tool = next((t for t in agent_tools if t.name == "done"), None)
        if done_tool:
            done_tool.function(summary="feature complete", success=True)
        return FakeRunResult()

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    # Use quick-check with a missing file to trigger rejection
    checks = [
        QuickCheck(
            path=str(tmp_path / "nonexistent.md"),
            description="Required file",
            error_message="Missing file",
        )
    ]

    with patch(
        "kodo.orchestrators.api.Agent.__init__",
        autospec=True,
        side_effect=fake_agent_init,
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle(
            GOAL,
            tmp_path,
            team,
            max_exchanges=10,
            config=CycleConfig(
                auto_commit=True, verification=checks, done_mode="legacy"
            ),
        )

    # Orchestrator didn't finish (done was rejected by quick-check, model stopped)
    assert result.finished is False
    # No commit should have been dispatched
    wf_session = team["worker_fast"].session
    assert len(wf_session.prompts) == 0


# ---------------------------------------------------------------------------
# Real git integration: verify actual commits land in a temp repo
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_auto_commit_creates_real_git_commit(git_project: Path) -> None:
    """_auto_commit with GitCommitSession creates a real commit in the repo."""
    # Verify there are uncommitted changes before auto-commit
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip(), "Expected uncommitted changes"

    session = GitCommitSession(response_text="Committed")
    worker = Agent(session, "commit worker", max_turns=5)
    team = {"worker_fast": worker}

    _auto_commit(team, git_project, SUMMARY)

    # Verify the commit exists
    git_log = subprocess.run(
        ["git", "log", "--oneline", "-2"],
        cwd=git_project,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "auto-commit" in git_log.stdout, (
        f"Expected auto-commit in log:\n{git_log.stdout}"
    )

    # Verify working tree is clean
    status_after = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert not status_after.stdout.strip(), (
        f"Working tree should be clean after commit:\n{status_after.stdout}"
    )

    # Verify the committed files
    show = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
        cwd=git_project,
        capture_output=True,
        text=True,
        check=True,
    )
    committed_files = show.stdout.strip().splitlines()
    assert "app.py" in committed_files
    assert "README.md" in committed_files


@pytest.mark.slow
def test_full_cycle_creates_real_commit(tmp_path: Path, git_project: Path) -> None:
    """End-to-end: orchestrator cycle → done → verify → auto-commit → real git commit.

    Orchestrator is mocked (pydantic-ai Agent), verification is mocked,
    but worker_fast uses GitCommitSession that runs real git in git_project.
    """
    log.init(RunDir.create(tmp_path, "real_commit_cycle"))

    session = GitCommitSession(response_text="Committed")
    worker_fast = Agent(session, "commit worker", max_turns=5)
    team = {
        "worker_fast": worker_fast,
        "tester": make_agent("ALL CHECKS PASS"),
        "architect": make_agent("ALL CHECKS PASS"),
    }

    agent_tools = []

    def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
        done_tool = next(t for t in agent_tools if t.name == "done")
        done_tool.function(summary="implemented hello world server", success=True)
        return FakeRunResult()

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    with (
        patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ),
        patch(
            "kodo.orchestrators.verification.verify_done",
            autospec=True,
            return_value=None,
        ),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle(
            GOAL,
            git_project,
            team,
            max_exchanges=10,
            config=CycleConfig(auto_commit=True, done_mode="legacy"),
        )

    assert result.finished is True

    # Verify a real commit was created
    git_log = subprocess.run(
        ["git", "log", "--oneline", "-2"],
        cwd=git_project,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "auto-commit" in git_log.stdout

    # Verify working tree is clean
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert not status.stdout.strip()


@pytest.mark.slow
def test_no_commit_when_auto_commit_disabled_real_git(
    tmp_path: Path,
    git_project: Path,
) -> None:
    """With auto_commit=False, git_project keeps its uncommitted changes."""
    log.init(RunDir.create(tmp_path, "no_commit_real"))

    session = GitCommitSession(response_text="Committed")
    worker_fast = Agent(session, "commit worker", max_turns=5)
    team = {
        "worker_fast": worker_fast,
        "tester": make_agent("ALL CHECKS PASS"),
    }

    agent_tools = []

    def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
        done_tool = next(t for t in agent_tools if t.name == "done")
        done_tool.function(summary="done", success=True)
        return FakeRunResult()

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    with (
        patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ),
        patch(
            "kodo.orchestrators.verification.verify_done",
            autospec=True,
            return_value=None,
        ),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle(
            GOAL,
            git_project,
            team,
            max_exchanges=10,
            config=CycleConfig(auto_commit=False, done_mode="legacy"),
        )

    assert result.finished is True

    # Uncommitted changes should still be there
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip(), "Changes should remain uncommitted"
