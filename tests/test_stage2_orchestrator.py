"""Deeper integration tests for ApiOrchestrator — orchestrator.run() with fake agents."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator
from tests.conftest import FakeRunResult, make_agent


def _make_fake_team(*, worker_fast: bool = True, worker_smart: bool = False):
    """Create team with kodo.agent.Agent instances wrapping FakeSession."""
    team = {}
    if worker_fast:
        team["worker_fast"] = make_agent(response_text="Task completed.")
    if worker_smart:
        team["worker_smart"] = make_agent(response_text="Analysis done.")
    if not team:
        team["worker"] = make_agent(response_text="Done.")
    return team


# ---------------------------------------------------------------------------
# Test 1: Single-cycle run completes successfully
# ---------------------------------------------------------------------------


def test_single_cycle_run_completes_successfully(tmp_path: Path):
    """ApiOrchestrator.run() with fake team, agent calls done → finished=True."""
    run_dir = RunDir.create(tmp_path, "orch_single")
    log.init(run_dir)

    agent_tools = []

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []

        def fake_run_sync(prompt, *, usage_limits=None):
            for t in agent_tools:
                if t.name == "done":
                    t.function(summary="all done", success=True)
                    break
            return FakeRunResult()

        self.run_sync = fake_run_sync

    team = _make_fake_team()

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch("kodo.orchestrators.base.verify_done", return_value=None),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.run(
            "Build a simple feature",
            tmp_path,
            team,
            max_exchanges=10,
            max_cycles=3,
        )

    assert result.finished is True
    assert len(result.cycles) == 1
    assert result.cycles[0].summary == "all done"


# ---------------------------------------------------------------------------
# Test 2: Multi-cycle when first cycle doesn't finish
# ---------------------------------------------------------------------------


def test_multi_cycle_when_first_doesnt_finish(tmp_path: Path):
    """First cycle no done, second cycle calls done → 2 cycles, 2 CycleResults."""
    run_dir = RunDir.create(tmp_path, "orch_multi")
    log.init(run_dir)

    cycle_count = [0]
    agent_tools = []

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []

        def fake_run_sync(prompt, *, usage_limits=None):
            cycle_count[0] += 1
            if cycle_count[0] == 1:
                return FakeRunResult(output="partial work")
            for t in agent_tools:
                if t.name == "done":
                    t.function(summary="done on cycle 2", success=True)
                    break
            return FakeRunResult()

        self.run_sync = fake_run_sync

    team = _make_fake_team()

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch("kodo.orchestrators.base.verify_done", return_value=None),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.run(
            "Multi-step task",
            tmp_path,
            team,
            max_exchanges=10,
            max_cycles=3,
        )

    assert result.finished is True
    assert len(result.cycles) == 2
    assert result.cycles[0].finished is False
    assert result.cycles[1].finished is True


# ---------------------------------------------------------------------------
# Test 3: max_cycles limit respected
# ---------------------------------------------------------------------------


def test_orchestrator_respects_max_cycles_limit(tmp_path: Path):
    """max_cycles=1, agent never calls done → stops after 1 cycle, finished=False."""
    run_dir = RunDir.create(tmp_path, "orch_limit")
    log.init(run_dir)

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        def fake_run_sync(prompt, *, usage_limits=None):
            return FakeRunResult(output="no done called")

        self.run_sync = fake_run_sync

    team = _make_fake_team()

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch.object(ApiOrchestrator, "_summarize", return_value="work in progress"),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.run(
            "Never finishes",
            tmp_path,
            team,
            max_exchanges=10,
            max_cycles=1,
        )

    assert result.finished is False
    assert len(result.cycles) == 1


# ---------------------------------------------------------------------------
# Test 4: Agent delegation via ask_worker_fast
# ---------------------------------------------------------------------------


def test_agent_delegation_via_ask_worker_fast(tmp_path: Path):
    """Mock agent calls ask_worker_fast tool → dispatches to worker_fast, task received."""
    run_dir = RunDir.create(tmp_path, "orch_delegate")
    log.init(run_dir)

    agent_tools = []
    task_sent = []

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []

        def fake_run_sync(prompt, *, usage_limits=None):
            ask_fast = next(
                (t for t in agent_tools if t.name == "ask_worker_fast"), None
            )
            if ask_fast:
                result = ask_fast.function(task="Implement the login form")
                task_sent.append(result)
            return FakeRunResult()

        self.run_sync = fake_run_sync

    team = _make_fake_team(worker_fast=True)
    worker_fast = team["worker_fast"]

    with patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        orch.cycle(
            "Build login UI",
            tmp_path,
            team,
            max_exchanges=10,
        )

    assert len(task_sent) == 1
    assert (
        "Implement the login form" in task_sent[0] or "Task completed" in task_sent[0]
    )
    assert len(worker_fast.session.prompts) == 1
    assert "Implement the login form" in worker_fast.session.prompts[0]
