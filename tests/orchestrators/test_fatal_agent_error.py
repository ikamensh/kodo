"""Verify FatalAgentError terminates the cycle only when ALL workers are dead.

Run: uv run pytest tests/orchestrators/test_fatal_agent_error.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kodo import log
from kodo.agent import Agent
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator
from kodo.orchestrators.base import FatalAgentError, handle_agent_call
from kodo.summarizer import Summarizer
from tests.conftest import FakeRunResult, FakeSession


def _noop_summarizer():
    """Summarizer that does nothing."""
    with (
        patch("kodo.summarizer._probe_ollama", return_value=None),
        patch("kodo.summarizer._probe_gemini", return_value=None),
    ):
        return Summarizer()


# ── Unit tests for handle_agent_call fatal tracking ─────────────────────


def test_single_worker_fatal_error_raises(tmp_path: Path):
    """With one worker, a single fatal error raises FatalAgentError."""
    log.init(RunDir.create(tmp_path, "fatal_single"))

    session = FakeSession(
        response_text="cursor: Subscription/billing issue — check your account status.",
        is_error=True,
    )
    agent = Agent(session, "test worker", max_turns=5)
    dead_workers: set[str] = set()

    with pytest.raises(FatalAgentError, match="All workers failed"):
        handle_agent_call(
            "worker",
            agent,
            "do something",
            tmp_path,
            _noop_summarizer(),
            dead_workers=dead_workers,
            total_workers=1,
        )


def test_two_workers_one_fatal_continues(tmp_path: Path):
    """With two workers, one fatal error does NOT raise."""
    log.init(RunDir.create(tmp_path, "fatal_partial"))

    session = FakeSession(
        response_text="cursor: Subscription/billing issue — check your account status.",
        is_error=True,
    )
    agent = Agent(session, "cursor worker", max_turns=5)
    dead_workers: set[str] = set()

    # First worker fails fatally — should NOT raise (2 workers total)
    result = handle_agent_call(
        "worker_fast",
        agent,
        "do something",
        tmp_path,
        _noop_summarizer(),
        dead_workers=dead_workers,
        total_workers=2,
    )
    assert "Subscription/billing" in result
    assert dead_workers == {"worker_fast"}


def test_two_workers_both_fatal_raises(tmp_path: Path):
    """With two workers, both failing fatally raises FatalAgentError."""
    log.init(RunDir.create(tmp_path, "fatal_both"))

    dead_workers: set[str] = set()
    summarizer = _noop_summarizer()

    # First worker fails
    session1 = FakeSession(
        response_text="cursor: Subscription/billing issue — check your account status.",
        is_error=True,
    )
    agent1 = Agent(session1, "cursor worker", max_turns=5)
    handle_agent_call(
        "worker_fast",
        agent1,
        "task 1",
        tmp_path,
        summarizer,
        dead_workers=dead_workers,
        total_workers=2,
    )

    # Second worker fails — now all are dead
    session2 = FakeSession(
        response_text="claude: Authentication failed — check your API key or login status.",
        is_error=True,
    )
    agent2 = Agent(session2, "claude worker", max_turns=5)
    with pytest.raises(FatalAgentError, match="All workers failed"):
        handle_agent_call(
            "worker_smart",
            agent2,
            "task 2",
            tmp_path,
            summarizer,
            dead_workers=dead_workers,
            total_workers=2,
        )


def test_non_fatal_error_does_not_mark_worker_dead(tmp_path: Path):
    """A non-fatal error (e.g. timeout) should not add the worker to dead set."""
    log.init(RunDir.create(tmp_path, "fatal_nonfatal"))

    session = FakeSession(response_text="some transient error", is_error=True)
    agent = Agent(session, "test worker", max_turns=5)
    dead_workers: set[str] = set()

    handle_agent_call(
        "worker",
        agent,
        "do something",
        tmp_path,
        _noop_summarizer(),
        dead_workers=dead_workers,
        total_workers=1,
    )
    assert dead_workers == set()


def test_success_does_not_affect_dead_workers(tmp_path: Path):
    """A successful call doesn't remove previously dead workers."""
    log.init(RunDir.create(tmp_path, "fatal_success"))

    dead_workers: set[str] = {"worker_fast"}
    session = FakeSession(response_text="all good")
    agent = Agent(session, "test worker", max_turns=5)

    handle_agent_call(
        "worker_smart",
        agent,
        "do something",
        tmp_path,
        _noop_summarizer(),
        dead_workers=dead_workers,
        total_workers=2,
    )
    # worker_fast stays dead, worker_smart is fine
    assert dead_workers == {"worker_fast"}


# ── Integration test: cycle aborts when all workers fail ────────────────


def test_cycle_aborts_when_all_workers_fatal(tmp_path: Path):
    """ApiOrchestrator.cycle() returns finished=True, success=False when
    all workers hit fatal errors."""
    log.init(RunDir.create(tmp_path, "fatal_cycle"))

    session = FakeSession(
        response_text="cursor: Subscription/billing issue — check your account status.",
        is_error=True,
    )
    team = {"worker": Agent(session, "test worker", max_turns=5)}

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        tool_list = tools or []

        def fake_run_sync(prompt, *, usage_limits=None):
            for t in tool_list:
                if t.name == "ask_worker":
                    t.function("do task")  # will raise FatalAgentError
            return FakeRunResult(output="should not reach")

        self.run_sync = fake_run_sync

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch.object(ApiOrchestrator, "_summarize", return_value="summary"),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle("build feature", tmp_path, team, max_exchanges=10)

    assert result.finished is True
    assert result.success is False
    assert "Aborted" in result.summary
    assert "All workers failed" in result.summary
