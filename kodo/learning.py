"""Multi-cycle learning for Kodo self-improvement.

Tracks what types of improvements work best, which agent combinations
are most effective, and uses this history to optimize future cycles.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


@dataclass
class CycleRecord:
    """Metadata about a completed improvement cycle."""

    cycle_id: str
    cycle_name: str
    improvement_type: str  # "feature", "refactor", "bugfix", "optimization", "testing"
    agents_used: list[str]  # e.g. ["architect", "worker_smart"]
    success: bool
    metrics_before: dict[str, float] = field(default_factory=dict)
    metrics_after: dict[str, float] = field(default_factory=dict)
    execution_time_s: float = 0.0
    rework_cycles: int = 0  # rejections before acceptance
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def metric_delta(self, metric: str) -> float | None:
        """Return the change in a metric (after - before), or None if missing."""
        if metric in self.metrics_before and metric in self.metrics_after:
            return self.metrics_after[metric] - self.metrics_before[metric]
        return None

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for JSON."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CycleRecord:
        """Deserialize from a dict (e.g. loaded from JSON)."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CycleLearner:
    """Learns from cycle history to improve future agent execution.

    Persists cycle records as a JSON array and provides analytics
    for success rates, agent effectiveness, and metric trends.
    """

    def __init__(self, history_path: Path) -> None:
        self.history_path = Path(history_path)
        self._history: list[CycleRecord] = []
        if self.history_path.exists():
            self._load()

    # ── Persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load history from disk."""
        if not self.history_path.exists():
            self._history = []
            return
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
            self._history = [CycleRecord.from_dict(rec) for rec in data]
        except (json.JSONDecodeError, TypeError, KeyError):
            self._history = []

    def save_history(self) -> Path:
        """Persist current history to disk. Returns the file path."""
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path.write_text(
            json.dumps([asdict(r) for r in self._history], indent=2),
            encoding="utf-8",
        )
        return self.history_path

    def record_cycle(self, record: CycleRecord) -> None:
        """Append a cycle record and persist."""
        self._history.append(record)
        self.save_history()

    def load_history(self) -> list[CycleRecord]:
        """Load all past cycle records from disk and return them.

        Also refreshes the in-memory cache.
        """
        self._load()
        return list(self._history)

    # ── Success rate analytics ───────────────────────────────────────

    def success_rate_by_type(self) -> dict[str, float]:
        """Success rate (0.0-1.0) per improvement_type."""
        counts: dict[str, list[bool]] = {}
        for rec in self._history:
            counts.setdefault(rec.improvement_type, []).append(rec.success)
        return {
            t: sum(vals) / len(vals) for t, vals in counts.items() if vals
        }

    def success_rate_by_agent(self) -> dict[str, float]:
        """Success rate (0.0-1.0) per individual agent."""
        counts: dict[str, list[bool]] = {}
        for rec in self._history:
            for agent in rec.agents_used:
                counts.setdefault(agent, []).append(rec.success)
        return {
            a: sum(vals) / len(vals) for a, vals in counts.items() if vals
        }

    def best_agent_for_type(self, improvement_type: str) -> str | None:
        """Which agent has the highest success rate for the given type.

        Only considers agents that participated in cycles of that type.
        Returns None if no history for this type.
        """
        agent_successes: dict[str, list[bool]] = {}
        for rec in self._history:
            if rec.improvement_type != improvement_type:
                continue
            for agent in rec.agents_used:
                agent_successes.setdefault(agent, []).append(rec.success)

        if not agent_successes:
            return None

        return max(
            agent_successes,
            key=lambda a: sum(agent_successes[a]) / len(agent_successes[a]),
        )

    def avg_rework_by_type(self) -> dict[str, float]:
        """Average rework cycles per improvement_type."""
        groups: dict[str, list[int]] = {}
        for rec in self._history:
            groups.setdefault(rec.improvement_type, []).append(rec.rework_cycles)
        return {
            t: sum(vals) / len(vals) for t, vals in groups.items() if vals
        }

    # ── Metric trends ────────────────────────────────────────────────

    def metric_trends(self) -> dict[str, list[tuple[str, float]]]:
        """For each metric, list of (cycle_id, value) pairs from metrics_after.

        Shows the progression of each metric across cycles.
        """
        trends: dict[str, list[tuple[str, float]]] = {}
        for rec in self._history:
            for metric, value in rec.metrics_after.items():
                trends.setdefault(metric, []).append((rec.cycle_id, value))
        return trends

    # ── Recommendations ──────────────────────────────────────────────

    def recommend_team(self, improvement_type: str) -> list[str]:
        """Recommend agent team composition based on historical success.

        Returns a list of agents sorted by effectiveness for this type.
        Falls back to a sensible default if no history exists.
        """
        agent_rates: dict[str, float] = {}
        agent_counts: dict[str, int] = {}

        for rec in self._history:
            if rec.improvement_type != improvement_type:
                continue
            for agent in rec.agents_used:
                agent_rates.setdefault(agent, 0.0)
                agent_counts.setdefault(agent, 0)
                if rec.success:
                    agent_rates[agent] += 1
                agent_counts[agent] += 1

        if not agent_rates:
            # No history: return sensible default
            return ["architect", "worker_smart"]

        # Compute rates and sort by effectiveness
        sorted_agents = sorted(
            agent_rates.keys(),
            key=lambda a: agent_rates[a] / agent_counts[a] if agent_counts[a] else 0,
            reverse=True,
        )
        return sorted_agents

    def rank_goals_with_learning(
        self,
        goals: Sequence[Any],
    ) -> list[Any]:
        """Re-rank improvement goals based on historical success rates.

        Goals whose improvement_type has higher historical success are
        ranked higher (more likely to succeed → do those first).
        Goals without matching history keep their original relative order.

        Each goal is expected to have a ``bottleneck`` attribute with a
        ``metric`` field used to match against improvement types.
        """
        type_rates = self.success_rate_by_type()
        type_rework = self.avg_rework_by_type()

        def score(goal: Any) -> float:
            """Score a goal: higher = better to work on next."""
            # Base score from severity
            base = getattr(goal.bottleneck, "severity", 0.5) if hasattr(goal, "bottleneck") else 0.5

            # Boost from historical success rate
            metric = getattr(goal.bottleneck, "metric", "") if hasattr(goal, "bottleneck") else ""
            hist_rate = type_rates.get(metric, 0.5)
            rework_penalty = type_rework.get(metric, 0.0) * 0.05  # penalize high-rework types

            return base * 0.6 + hist_rate * 0.4 - rework_penalty

        ranked = sorted(goals, key=score, reverse=True)

        # Re-assign priorities
        for i, goal in enumerate(ranked):
            if hasattr(goal, "priority"):
                goal.priority = i + 1

        return ranked

    # ── Summary ──────────────────────────────────────────────────────

    def effectiveness_summary(self) -> str:
        """Human-readable markdown summary of learning insights."""
        history = self._history
        if not history:
            return "# Learning Summary\n\nNo cycle history available yet."

        total = len(history)
        successes = sum(1 for r in history if r.success)
        overall_rate = successes / total if total else 0

        parts = [
            "# Learning Summary\n",
            f"**Total Cycles:** {total}",
            f"**Overall Success Rate:** {overall_rate:.0%}",
            f"**Total Execution Time:** {sum(r.execution_time_s for r in history):.1f}s\n",
        ]

        # Success by type
        type_rates = self.success_rate_by_type()
        if type_rates:
            parts.append("## Success Rate by Improvement Type\n")
            for t, rate in sorted(type_rates.items(), key=lambda x: x[1], reverse=True):
                parts.append(f"- **{t}:** {rate:.0%}")
            parts.append("")

        # Success by agent
        agent_rates = self.success_rate_by_agent()
        if agent_rates:
            parts.append("## Success Rate by Agent\n")
            for a, rate in sorted(agent_rates.items(), key=lambda x: x[1], reverse=True):
                parts.append(f"- **{a}:** {rate:.0%}")
            parts.append("")

        # Rework analysis
        rework = self.avg_rework_by_type()
        if rework:
            parts.append("## Average Rework Cycles by Type\n")
            for t, avg in sorted(rework.items(), key=lambda x: x[1]):
                parts.append(f"- **{t}:** {avg:.1f} rework cycles")
            parts.append("")

        # Metric trends
        trends = self.metric_trends()
        if trends:
            parts.append("## Metric Trends\n")
            for metric, values in trends.items():
                if len(values) >= 2:
                    first_val = values[0][1]
                    last_val = values[-1][1]
                    direction = "improved" if last_val != first_val else "stable"
                    parts.append(
                        f"- **{metric}:** {first_val:.1f} -> {last_val:.1f} ({direction})"
                    )
            parts.append("")

        # Recommendations
        if type_rates:
            best_type = max(type_rates, key=type_rates.get)  # type: ignore[arg-type]
            worst_type = min(type_rates, key=type_rates.get)  # type: ignore[arg-type]
            parts.append("## Recommendations\n")
            parts.append(
                f"- **Most effective:** {best_type} ({type_rates[best_type]:.0%} success)"
            )
            if type_rates[worst_type] < 0.5:
                parts.append(
                    f"- **Needs improvement:** {worst_type} ({type_rates[worst_type]:.0%} success)"
                )

        return "\n".join(parts)
