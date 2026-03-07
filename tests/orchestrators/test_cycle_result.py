"""Tests for CycleResult and RunResult data classes."""

from __future__ import annotations

from kodo.orchestrators.base import CycleResult, RunResult, StageResult


class TestRunResult:
    def test_finished_when_last_cycle_finished(self) -> None:
        rr = RunResult(
            cycles=[
                CycleResult(finished=False, summary="partial"),
                CycleResult(finished=True, summary="done"),
            ]
        )
        assert rr.finished is True
        assert rr.summary == "done"

    def test_not_finished_when_last_cycle_not_finished(self) -> None:
        rr = RunResult(
            cycles=[
                CycleResult(finished=True, summary="first"),
                CycleResult(finished=False, summary="ran out of turns"),
            ]
        )
        assert rr.finished is False

    def test_totals_sum_across_cycles(self) -> None:
        rr = RunResult(
            cycles=[
                CycleResult(exchanges=10, total_cost_usd=1.0),
                CycleResult(exchanges=5, total_cost_usd=0.5),
            ]
        )
        assert rr.total_exchanges == 15
        assert rr.total_cost_usd == 1.5

    def test_finished_uses_max_stage_index_not_last_appended(self) -> None:
        """Parallel stages arrive in non-deterministic order.

        If stage 4 (success) arrives before stage 3 (failure), the
        last-appended entry is stage 3.  ``finished`` should still
        return True because stage 4 has the highest index.
        """
        rr = RunResult(
            cycles=[CycleResult(finished=True)],
            stage_results=[
                StageResult(stage_index=1, stage_name="S1", finished=True),
                # Arrival order: stage 4 first, then stage 3
                StageResult(stage_index=4, stage_name="S4", finished=True),
                StageResult(stage_index=3, stage_name="S3", finished=False),
            ],
        )
        # Stage 3 is [-1] but stage 4 has the highest index → finished
        assert rr.finished is True

    def test_not_finished_when_max_stage_index_failed(self) -> None:
        """When the stage with the highest index failed, finished is False."""
        rr = RunResult(
            cycles=[CycleResult(finished=True)],
            stage_results=[
                StageResult(stage_index=1, stage_name="S1", finished=True),
                StageResult(stage_index=2, stage_name="S2", finished=False),
            ],
        )
        assert rr.finished is False
