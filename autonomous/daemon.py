"""Autonomous improvement daemon integrating all Kodo subsystems.

Ties together benchmarking, goal identification, multi-cycle learning,
and execution into a single autonomous loop that can run nightly or
continuously.

Usage::

    daemon = ImprovementDaemon(project_dir=Path("/my/project"))
    report = daemon.run_cycle()
    print(report.summary)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from improvements.benchmark import (
    BenchmarkStore,
    CycleBenchmark,
)
from kodo.goal_identifier import (
    ImprovementGoal,
    PerformanceAnalyzer,
)
from kodo.learning import CycleLearner, CycleRecord


# ---------------------------------------------------------------------------
# Daemon result types
# ---------------------------------------------------------------------------


@dataclass
class DaemonCycleReport:
    """Report from a single daemon improvement cycle."""

    cycle_id: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    goals_identified: int = 0
    goals_attempted: int = 0
    goals_succeeded: int = 0
    goals_failed: int = 0
    metrics_before: dict[str, float] = field(default_factory=dict)
    metrics_after: dict[str, float] = field(default_factory=dict)
    execution_time_s: float = 0.0
    goal_details: list[dict[str, Any]] = field(default_factory=list)
    learning_summary: str = ""

    @property
    def success_rate(self) -> float:
        """Fraction of attempted goals that succeeded."""
        if self.goals_attempted == 0:
            return 0.0
        return self.goals_succeeded / self.goals_attempted

    @property
    def summary(self) -> str:
        """Human-readable one-line summary."""
        return (
            f"Cycle {self.cycle_id}: "
            f"{self.goals_succeeded}/{self.goals_attempted} goals succeeded "
            f"({self.success_rate:.0%}) in {self.execution_time_s:.1f}s"
        )

    def format_report(self) -> str:
        """Full markdown report."""
        parts = [
            f"# Improvement Cycle {self.cycle_id}\n",
            f"**Timestamp:** {self.timestamp}",
            f"**Duration:** {self.execution_time_s:.1f}s",
            f"**Success Rate:** {self.success_rate:.0%}\n",
        ]

        if self.goal_details:
            parts.append("## Goals\n")
            for detail in self.goal_details:
                status = "pass" if detail.get("success") else "fail"
                parts.append(f"- [{status}] {detail.get('title', 'Unknown')}")
            parts.append("")

        if self.metrics_before and self.metrics_after:
            parts.append("## Metrics\n")
            parts.append("| Metric | Before | After | Change |")
            parts.append("|--------|--------|-------|--------|")
            for metric in self.metrics_before:
                before = self.metrics_before[metric]
                after = self.metrics_after.get(metric, before)
                delta = after - before
                sign = "+" if delta > 0 else ""
                parts.append(f"| {metric} | {before:.1f} | {after:.1f} | {sign}{delta:.1f} |")
            parts.append("")

        if self.learning_summary:
            parts.append("## Learning Insights\n")
            parts.append(self.learning_summary)

        return "\n".join(parts)


@dataclass
class DaemonStatus:
    """Current daemon status snapshot."""

    running: bool = False
    total_cycles: int = 0
    total_goals_succeeded: int = 0
    total_goals_attempted: int = 0
    uptime_s: float = 0.0
    last_cycle_report: DaemonCycleReport | None = None

    @property
    def overall_success_rate(self) -> float:
        if self.total_goals_attempted == 0:
            return 0.0
        return self.total_goals_succeeded / self.total_goals_attempted


# ---------------------------------------------------------------------------
# Core daemon
# ---------------------------------------------------------------------------


class ImprovementDaemon:
    """Autonomous daemon that identifies, executes, and learns from improvements.

    Integrates:
    - **PerformanceAnalyzer** (Cycle 8): identifies bottlenecks and proposes goals
    - **CycleLearner** (Cycle 9): learns which improvements/agents work best
    - **BenchmarkStore** (Cycle 7): persists metrics for comparison

    The daemon can be run once (``run_cycle``) or continuously (``run_loop``).
    """

    def __init__(
        self,
        project_dir: Path,
        *,
        metrics_collector: Callable[[], dict[str, float]] | None = None,
        goal_executor: Callable[[ImprovementGoal], bool] | None = None,
        max_goals_per_cycle: int = 3,
        cycle_interval_s: float = 3600.0,  # 1 hour between cycles
    ) -> None:
        self.project_dir = Path(project_dir)
        self._metrics_collector = metrics_collector or self._default_metrics
        self._goal_executor = goal_executor or self._default_executor
        self.max_goals_per_cycle = max_goals_per_cycle
        self.cycle_interval_s = cycle_interval_s

        # Sub-systems
        self.analyzer = PerformanceAnalyzer()
        self.learner = CycleLearner(
            self.project_dir / ".kodo" / "learning_history.json"
        )
        self.benchmark_store = BenchmarkStore(self.project_dir)

        # State
        self._cycle_count = 0
        self._running = False
        self._start_time = 0.0
        self._reports: list[DaemonCycleReport] = []

    # ── Single cycle ─────────────────────────────────────────────────

    def run_cycle(self) -> DaemonCycleReport:
        """Execute one improvement cycle.

        1. Collect current metrics
        2. Identify bottlenecks and propose goals
        3. Re-rank goals using learning history
        4. Execute each goal
        5. Record results and update learning
        6. Return a report
        """
        self._cycle_count += 1
        cycle_id = f"daemon-{self._cycle_count}"
        start = time.time()

        report = DaemonCycleReport(cycle_id=cycle_id)

        # Step 1: Collect metrics
        metrics_before = self._metrics_collector()
        report.metrics_before = dict(metrics_before)

        # Step 2: Identify goals
        goals = self.analyzer.propose_goals(
            metrics_before, max_goals=self.max_goals_per_cycle
        )
        report.goals_identified = len(goals)

        # Step 3: Re-rank with learning history
        if goals:
            goals = self.learner.rank_goals_with_learning(goals)

        # Step 4: Execute goals
        for goal in goals:
            report.goals_attempted += 1
            detail: dict[str, Any] = {"title": goal.title, "priority": goal.priority}

            try:
                success = self._goal_executor(goal)
                detail["success"] = success
                if success:
                    report.goals_succeeded += 1
                else:
                    report.goals_failed += 1
            except Exception as exc:
                detail["success"] = False
                detail["error"] = str(exc)
                report.goals_failed += 1

            report.goal_details.append(detail)

            # Record in learner
            metrics_after = self._metrics_collector()
            self.learner.record_cycle(
                CycleRecord(
                    cycle_id=f"{cycle_id}-{goal.priority}",
                    cycle_name=goal.title,
                    improvement_type=getattr(goal.bottleneck, "metric", "unknown"),
                    agents_used=["daemon"],
                    success=detail["success"],
                    metrics_before=metrics_before,
                    metrics_after=metrics_after,
                    execution_time_s=time.time() - start,
                    rework_cycles=0,
                )
            )

        # Step 5: Final metrics
        report.metrics_after = self._metrics_collector()
        report.execution_time_s = time.time() - start

        # Step 6: Save benchmark
        benchmark = CycleBenchmark(
            cycle_id=cycle_id,
            cycle_name=f"Daemon Cycle {self._cycle_count}",
        )
        for metric, value in report.metrics_after.items():
            benchmark.add_sample(metric, value, "")
        self.benchmark_store.save_cycle(benchmark)

        # Step 7: Learning summary
        report.learning_summary = self.learner.effectiveness_summary()

        self._reports.append(report)
        return report

    # ── Continuous loop ──────────────────────────────────────────────

    def run_loop(
        self,
        *,
        max_cycles: int | None = None,
        on_cycle: Callable[[DaemonCycleReport], None] | None = None,
    ) -> list[DaemonCycleReport]:
        """Run improvement cycles continuously.

        Parameters
        ----------
        max_cycles : int, optional
            Stop after this many cycles (None = run forever).
        on_cycle : callable, optional
            Called after each cycle with the report.

        Returns
        -------
        list[DaemonCycleReport]
            All cycle reports.
        """
        self._running = True
        self._start_time = time.time()
        cycles_run = 0

        while self._running:
            report = self.run_cycle()
            cycles_run += 1

            if on_cycle:
                on_cycle(report)

            if max_cycles is not None and cycles_run >= max_cycles:
                break

            if self._running:
                time.sleep(self.cycle_interval_s)

        self._running = False
        return self._reports

    def stop(self) -> None:
        """Signal the daemon to stop after the current cycle."""
        self._running = False

    # ── Status ───────────────────────────────────────────────────────

    def status(self) -> DaemonStatus:
        """Return current daemon status."""
        return DaemonStatus(
            running=self._running,
            total_cycles=self._cycle_count,
            total_goals_succeeded=sum(r.goals_succeeded for r in self._reports),
            total_goals_attempted=sum(r.goals_attempted for r in self._reports),
            uptime_s=time.time() - self._start_time if self._start_time else 0,
            last_cycle_report=self._reports[-1] if self._reports else None,
        )

    def weekly_summary(self) -> str:
        """Generate a weekly summary of all cycles."""
        if not self._reports:
            return "No cycles executed yet."

        total_succeeded = sum(r.goals_succeeded for r in self._reports)
        total_attempted = sum(r.goals_attempted for r in self._reports)
        total_time = sum(r.execution_time_s for r in self._reports)

        parts = [
            "# Weekly Summary\n",
            f"**Cycles:** {len(self._reports)}",
            f"**Goals Attempted:** {total_attempted}",
            f"**Goals Succeeded:** {total_succeeded}",
            f"**Overall Success Rate:** {total_succeeded / total_attempted:.0%}" if total_attempted else "",
            f"**Total Time:** {total_time:.1f}s\n",
        ]

        # Cycle breakdown
        parts.append("## Cycle Details\n")
        for report in self._reports:
            parts.append(f"- {report.summary}")
        parts.append("")

        # Learning insights
        parts.append(self.learner.effectiveness_summary())

        return "\n".join(parts)

    # ── Defaults ─────────────────────────────────────────────────────

    @staticmethod
    def _default_metrics() -> dict[str, float]:
        """Default metrics collector — returns neutral values."""
        return {
            "tokens_per_task": 1000,
            "execution_time_s": 120,
            "test_coverage": 90,
            "error_rate": 2,
        }

    @staticmethod
    def _default_executor(goal: ImprovementGoal) -> bool:
        """Default executor — always succeeds (no-op)."""
        return True
