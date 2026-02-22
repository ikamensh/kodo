"""Self-improvement goal identification.

Analyzes benchmark data, log history, and system state to automatically
identify the top performance bottlenecks and propose improvement goals
for the next cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class BottleneckAnalysis:
    """A detected performance bottleneck."""

    metric: str
    current_value: float
    unit: str
    target_value: float
    severity: float  # 0.0 (minor) to 1.0 (critical)
    description: str
    suggested_action: str

    @property
    def gap_pct(self) -> float:
        """Percentage gap between current and target."""
        if self.target_value == 0:
            return 0.0
        return abs(self.current_value - self.target_value) / abs(self.target_value) * 100


@dataclass
class ImprovementGoal:
    """A proposed improvement goal for the next cycle."""

    title: str
    description: str
    priority: int  # 1 = highest priority
    estimated_impact: str
    bottleneck: BottleneckAnalysis
    acceptance_criteria: list[str] = field(default_factory=list)

    def format_proposal(self) -> str:
        """Format this goal as a readable proposal."""
        criteria = "\n".join(f"  - {c}" for c in self.acceptance_criteria)
        return (
            f"## Priority {self.priority}: {self.title}\n"
            f"{self.description}\n\n"
            f"**Estimated Impact:** {self.estimated_impact}\n"
            f"**Current:** {self.bottleneck.current_value:.2f} {self.bottleneck.unit}\n"
            f"**Target:** {self.bottleneck.target_value:.2f} {self.bottleneck.unit}\n"
            f"**Gap:** {self.bottleneck.gap_pct:.1f}%\n\n"
            f"**Acceptance Criteria:**\n{criteria}"
        )


# ---------------------------------------------------------------------------
# Standard performance targets
# ---------------------------------------------------------------------------

# Target values for common metrics — used to identify gaps
DEFAULT_TARGETS: dict[str, tuple[float, str, bool]] = {
    # metric: (target, unit, lower_is_better)
    "tokens_per_task": (1000, "tokens", True),
    "execution_time_s": (120, "seconds", True),
    "test_coverage": (90, "%", False),
    "error_rate": (2, "%", True),
    "bug_escape_rate": (5, "%", True),
    "rework_rate": (5, "%", True),
    "first_try_success_rate": (95, "%", False),
}

# Mapping from metric to improvement action templates
_IMPROVEMENT_ACTIONS: dict[str, tuple[str, str]] = {
    "tokens_per_task": (
        "Reduce token usage per task",
        "Optimize prompts, compress agent descriptions, reduce context window bloat. "
        "Use PromptOptimizer to identify and remove redundant instructions.",
    ),
    "execution_time_s": (
        "Reduce execution time",
        "Parallelize independent tasks, optimize agent routing, reduce retry delays. "
        "Use ParallelDispatcher for concurrent agent execution.",
    ),
    "test_coverage": (
        "Increase test coverage",
        "Add missing unit tests for untested code paths. Focus on core modules: "
        "sessions, agents, orchestrators.",
    ),
    "error_rate": (
        "Reduce error rate",
        "Improve retry logic, add better error handling, fix root causes of "
        "transient failures.",
    ),
    "bug_escape_rate": (
        "Improve verification thoroughness",
        "Enhance architect checklist, add more verification categories, improve "
        "report parsing to catch more issues.",
    ),
    "rework_rate": (
        "Reduce task rework",
        "Improve task routing accuracy, provide better context to agents, "
        "use complexity scoring to match tasks to appropriate workers.",
    ),
    "first_try_success_rate": (
        "Improve first-try success",
        "Better task routing, clearer directives, more context for agents. "
        "Route complex tasks to worker_smart, simple tasks to worker_fast.",
    ),
}


class PerformanceAnalyzer:
    """Analyzes performance data to identify bottlenecks and propose goals.

    Uses benchmark data, configurable targets, and heuristic ranking
    to produce a prioritized list of improvement goals.
    """

    def __init__(
        self,
        targets: dict[str, tuple[float, str, bool]] | None = None,
    ):
        self.targets = targets or DEFAULT_TARGETS

    def analyze(
        self,
        current_metrics: dict[str, float],
    ) -> list[BottleneckAnalysis]:
        """Identify bottlenecks by comparing current metrics to targets.

        Returns bottlenecks sorted by severity (most critical first).
        """
        bottlenecks: list[BottleneckAnalysis] = []

        for metric, (target, unit, lower_is_better) in self.targets.items():
            if metric not in current_metrics:
                continue

            current = current_metrics[metric]

            # Compute severity: how far from target
            if target == 0:
                severity = 0.0
            elif lower_is_better:
                # e.g., tokens: lower is better → severity = excess / target
                severity = max(0.0, (current - target) / target)
            else:
                # e.g., coverage: higher is better → severity = deficit / target
                severity = max(0.0, (target - current) / target)

            severity = min(1.0, severity)  # cap at 1.0

            if severity < 0.05:
                continue  # within acceptable range

            title, action = _IMPROVEMENT_ACTIONS.get(
                metric,
                (f"Improve {metric}", f"Optimize {metric} toward target {target}"),
            )

            bottlenecks.append(
                BottleneckAnalysis(
                    metric=metric,
                    current_value=current,
                    unit=unit,
                    target_value=target,
                    severity=severity,
                    description=f"{title}: currently {current:.1f}{unit}, "
                    f"target {target:.1f}{unit}",
                    suggested_action=action,
                )
            )

        # Sort by severity (highest first)
        bottlenecks.sort(key=lambda b: b.severity, reverse=True)
        return bottlenecks

    def propose_goals(
        self,
        current_metrics: dict[str, float],
        *,
        max_goals: int = 3,
        min_severity: float = 0.15,
    ) -> list[ImprovementGoal]:
        """Generate prioritized improvement goals from current metrics.

        Returns up to *max_goals* goals, ranked by impact (severity).
        Only bottlenecks with severity >= *min_severity* become goals.
        """
        bottlenecks = [
            b for b in self.analyze(current_metrics)
            if b.severity >= min_severity
        ]
        goals: list[ImprovementGoal] = []

        for i, bottleneck in enumerate(bottlenecks[:max_goals]):
            title, description = _IMPROVEMENT_ACTIONS.get(
                bottleneck.metric,
                (f"Improve {bottleneck.metric}", bottleneck.suggested_action),
            )

            goal = ImprovementGoal(
                title=title,
                description=description,
                priority=i + 1,
                estimated_impact=(
                    f"Reduce {bottleneck.metric} by {bottleneck.gap_pct:.0f}%"
                    if bottleneck.severity > 0.5
                    else f"Improve {bottleneck.metric} toward target"
                ),
                bottleneck=bottleneck,
                acceptance_criteria=[
                    f"{bottleneck.metric} reaches {bottleneck.target_value:.0f} {bottleneck.unit}",
                    "No regression in other metrics",
                    "All tests pass",
                    "Architect approves changes",
                ],
            )
            goals.append(goal)

        return goals

    def format_proposal(
        self,
        goals: Sequence[ImprovementGoal],
    ) -> str:
        """Format goals as a complete proposal document."""
        if not goals:
            return "# No Improvements Needed\n\nAll metrics are within target ranges."

        parts = ["# Proposed Improvement Goals\n"]
        parts.append(
            f"Identified **{len(goals)}** high-impact improvement(s):\n"
        )

        for goal in goals:
            parts.append(goal.format_proposal())
            parts.append("")

        return "\n".join(parts)
