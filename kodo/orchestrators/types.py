"""Shared dataclasses, type aliases, and lightweight classes for orchestrators."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from kodo.agent import Agent


# Team is just a named dict of agents
TeamConfig = dict[str, Agent]


class FatalAgentError(Exception):
    """Raised when all workers have hit unrecoverable errors."""


@dataclass
class QuickCheck:
    """Lightweight scripted check that replaces agent-based verification.

    Used for stages where a simple file-existence check is sufficient
    (e.g. analytical stages that write a findings file).
    """

    path: str  # file that must exist (can use {run_dir} placeholder)
    description: str  # shown to orchestrator as what we're verifying
    error_message: str  # fed back to agent if check fails


@dataclass
class GoalStage:
    """One stage in a multi-stage goal plan."""

    index: int  # 1-based
    name: str  # short label
    description: str  # full prose for orchestrator
    acceptance_criteria: str  # verifiable "done" definition
    browser_testing: bool = False  # whether this stage needs browser verification
    parallel_group: int | None = None  # stages with same group run concurrently
    persist_changes: bool = False  # merge worktree changes back after completion
    verification: Literal["full", "skip"] | list[QuickCheck] = "full"


@dataclass
class CycleConfig:
    """Pass-through configuration for a single cycle.

    Bundles stage-level settings (browser_testing, verification) and
    run-level settings (verifiers, auto_commit) so they don't have to be
    threaded as individual keyword arguments through every layer.
    """

    browser_testing: bool = False
    verifiers: dict | None = None
    auto_commit: bool = False
    verification: Literal["full", "skip"] | list[QuickCheck] = "full"
    done_mode: Literal["legacy", "new"] = "new"
    acceptance_criteria: str | None = None
    effort: str = "standard"  # "low" | "standard" | "high" | "max"


@dataclass
class GoalPlan:
    """Ordered list of stages with shared architectural context."""

    context: str  # shared architectural context
    stages: list[GoalStage]


@dataclass
class StageResult:
    """Groups cycles and outcome for a single stage."""

    stage_index: int
    stage_name: str
    cycles: list["CycleResult"] = field(default_factory=list)
    finished: bool = False
    summary: str = ""


@dataclass
class CycleResult:
    """Result of a single orchestration cycle (one 'day of work')."""

    exchanges: int = 0
    total_cost_usd: float = 0.0
    finished: bool = False
    success: bool = False
    summary: str = ""
    stage_index: int | None = None


@dataclass
class RunResult:
    """Result of a full multi-cycle run."""

    cycles: list[CycleResult] = field(default_factory=list)
    stage_results: list[StageResult] = field(default_factory=list)

    @property
    def total_exchanges(self) -> int:
        return sum(c.exchanges for c in self.cycles)

    @property
    def total_cost_usd(self) -> float:
        return sum(c.total_cost_usd for c in self.cycles)

    @property
    def finished(self) -> bool:
        # In staged runs, a crashed stage may have 0 cycles — check
        # stage_results to avoid reporting "finished" when the last
        # stage failed.
        #
        # Use the stage with the highest index rather than the last
        # appended entry — parallel stages arrive in non-deterministic
        # order so [-1] could be an earlier, failed stage while the
        # logically-last stage actually succeeded.
        if self.stage_results:
            last_stage = max(self.stage_results, key=lambda s: s.stage_index)
            return last_stage.finished
        return bool(self.cycles) and self.cycles[-1].finished

    @property
    def summary(self) -> str:
        return self.cycles[-1].summary if self.cycles else ""


@dataclass
class ResumeState:
    """State for resuming a previously interrupted run."""

    completed_cycles: int
    prior_summary: str
    agent_session_ids: dict[str, str]
    completed_stages: list[int]
    stage_summaries: list[str]
    current_stage_cycles: int
    pending_exchanges: list[dict] = field(default_factory=list)


class DoneSignal:
    """Shared mutable to communicate between the ``done`` tool and the cycle loop."""

    def __init__(self) -> None:
        self.called = False
        self.summary = ""
        self.success = False
        self.terminal: Literal["goal_done", "end_cycle", "raise_issue", "legacy"] | None = None
