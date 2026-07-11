"""Tests for centralized run listing and project-scoped discovery."""

from __future__ import annotations

import json
from pathlib import Path

from kodo import log

_RUN_START = {
    "event": "run_start",
    "orchestrator": "api",
    "model": "m",
    "max_exchanges": 30,
    "max_cycles": 5,
    "team": [],
}
_CLI_ARGS = {"event": "cli_args", "team": "full"}


def _make_run(run_id: str, project_dir: str, goal: str, events: list[dict]) -> None:
    """Create a synthetic run under the central runs directory."""
    d = log._runs_root() / run_id
    d.mkdir(parents=True)
    base = [
        {**_RUN_START, "goal": goal, "project_dir": project_dir},
        _CLI_ARGS,
    ]
    all_events = base + events
    lines = [
        json.dumps({"ts": "2025-01-01T00:00:00Z", "t": 0, **e}) for e in all_events
    ]
    (d / "log.jsonl").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------


class TestListRuns:
    """Listing runs across projects from the central store."""

    def test_empty_store_returns_nothing(self):
        assert log.list_runs() == []

    def test_all_runs_returned_when_no_filter(self, tmp_path: Path):
        proj_a = str(tmp_path / "alpha")
        proj_b = str(tmp_path / "beta")
        _make_run(
            "run_a", proj_a, "goal alpha", [{"event": "cycle_end", "summary": "ok"}]
        )
        _make_run(
            "run_b", proj_b, "goal beta", [{"event": "cycle_end", "summary": "ok"}]
        )

        runs = log.list_runs()
        goals = {r.goal for r in runs}
        assert "goal alpha" in goals
        assert "goal beta" in goals

    def test_filtered_to_one_project(self, tmp_path: Path):
        proj_a = tmp_path / "alpha"
        proj_a.mkdir()
        proj_b = tmp_path / "beta"
        proj_b.mkdir()
        _make_run(
            "run_a",
            str(proj_a),
            "alpha goal",
            [{"event": "cycle_end", "summary": "ok"}],
        )
        _make_run(
            "run_b", str(proj_b), "beta goal", [{"event": "cycle_end", "summary": "ok"}]
        )

        runs = log.list_runs(proj_a)
        assert len(runs) == 1
        assert runs[0].goal == "alpha goal"

    def test_newest_first_ordering(self, tmp_path: Path):
        proj = str(tmp_path)
        _make_run(
            "20250101_100000", proj, "older", [{"event": "cycle_end", "summary": "ok"}]
        )
        _make_run(
            "20250102_100000", proj, "newer", [{"event": "cycle_end", "summary": "ok"}]
        )

        runs = log.list_runs()
        assert runs[0].goal == "newer"
        assert runs[1].goal == "older"

    def test_includes_both_finished_and_incomplete(self, tmp_path: Path):
        proj = str(tmp_path)
        _make_run(
            "run_done",
            proj,
            "finished run",
            [
                {"event": "cycle_end", "summary": "done"},
                {"event": "run_end"},
            ],
        )
        _make_run(
            "run_wip",
            proj,
            "in progress",
            [
                {"event": "cycle_end", "summary": "partial"},
            ],
        )

        runs = log.list_runs()
        assert len(runs) == 2
        statuses = {r.goal: r.finished for r in runs}
        assert statuses["finished run"] is True
        assert statuses["in progress"] is False

    def test_unparseable_runs_skipped(self, tmp_path: Path):
        """A corrupt log file should not break listing other runs."""
        proj = str(tmp_path)
        _make_run("good_run", proj, "works", [{"event": "cycle_end", "summary": "ok"}])

        # Write garbage into another run
        bad = log._runs_root() / "bad_run"
        bad.mkdir()
        (bad / "log.jsonl").write_text("not json at all\n")

        runs = log.list_runs()
        assert len(runs) == 1
        assert runs[0].run_id == "good_run"

    def test_legacy_run_jsonl_found(self, tmp_path: Path):
        """Runs with run.jsonl (legacy name) are still discovered."""
        proj = str(tmp_path)
        d = log._runs_root() / "legacy_run"
        d.mkdir(parents=True)
        base = [
            {**_RUN_START, "goal": "legacy goal", "project_dir": proj},
            _CLI_ARGS,
            {"event": "cycle_end", "summary": "ok"},
        ]
        lines = [
            json.dumps({"ts": "2025-01-01T00:00:00Z", "t": i, **e})
            for i, e in enumerate(base)
        ]
        (d / "run.jsonl").write_text("\n".join(lines) + "\n")
        runs = log.list_runs()
        assert any(r.run_id == "legacy_run" for r in runs)
        legacy = next(r for r in runs if r.run_id == "legacy_run")
        assert legacy.goal == "legacy goal"

    def test_project_filter_ignores_projectless_cli_args(self, tmp_path: Path):
        """Value-less resume can find logs whose cli_args predate project_dir."""
        project = tmp_path / "project"
        project.mkdir()

        def write_run(run_id: str, metadata_events: list[dict]) -> None:
            d = log._runs_root() / run_id
            d.mkdir(parents=True)
            events = metadata_events + [
                {
                    **_RUN_START,
                    "goal": "Build a todo API",
                    "project_dir": str(project),
                },
                {"event": "cycle_end", "summary": "partial", "finished": False},
            ]
            lines = [
                json.dumps({"ts": "2025-01-01T00:00:00Z", "t": i, **e})
                for i, e in enumerate(events)
            ]
            (d / "log.jsonl").write_text("\n".join(lines) + "\n")

        projectless_cli_args = {
            "event": "cli_args",
            "team": "saga",
            "goal_text": "Build a todo API",
            "has_plan": False,
            "max_exchanges": 30,
            "max_cycles": 5,
            "orchestrator": "api",
            "orchestrator_model": "gemini-flash",
        }
        write_run(
            "project_from_run_init",
            [
                {
                    "event": "run_init",
                    "project_dir": str(project),
                },
                projectless_cli_args,
            ],
        )
        write_run("project_from_run_start", [projectless_cli_args])

        runs = log.find_incomplete_runs(project)

        assert {r.run_id for r in runs} == {
            "project_from_run_init",
            "project_from_run_start",
        }


# ---------------------------------------------------------------------------
# find_incomplete_runs — project isolation
# ---------------------------------------------------------------------------


class TestCrossProjectIsolation:
    """Runs from other projects must not leak into project-scoped queries."""

    def test_other_projects_runs_invisible(self, tmp_path: Path):
        mine = tmp_path / "mine"
        mine.mkdir()
        theirs = tmp_path / "theirs"
        theirs.mkdir()

        _make_run(
            "their_run",
            str(theirs),
            "their goal",
            [
                {"event": "cycle_end", "summary": "ok"},
            ],
        )
        _make_run(
            "my_run",
            str(mine),
            "my goal",
            [
                {"event": "cycle_end", "summary": "ok"},
            ],
        )

        incomplete = log.find_incomplete_runs(mine)
        assert len(incomplete) == 1
        assert incomplete[0].goal == "my goal"

    def test_resume_finds_nothing_for_wrong_project(self, tmp_path: Path):
        wrong = tmp_path / "wrong"
        wrong.mkdir()
        right = tmp_path / "right"
        right.mkdir()

        _make_run(
            "some_run",
            str(wrong),
            "wrong project",
            [
                {"event": "cycle_end", "summary": "in progress"},
            ],
        )

        assert log.find_incomplete_runs(right) == []


# ---------------------------------------------------------------------------
# Stage tracking in parse_run
# ---------------------------------------------------------------------------


class TestStageParsing:
    """A staged run's log should reconstruct which stages completed."""

    def _staged_run_start(self, project_dir: str, goal: str) -> dict:
        return {
            **_RUN_START,
            "goal": goal,
            "project_dir": project_dir,
            "has_stages": True,
            "num_stages": 3,
        }

    def test_completed_stages_tracked(self, tmp_path: Path):
        """Two stages complete, third never starts — we should know exactly which finished."""
        d = log._runs_root() / "staged_run"
        d.mkdir(parents=True)
        events = [
            self._staged_run_start(str(tmp_path), "staged goal"),
            _CLI_ARGS,
            {"event": "stage_start", "stage_index": 1},
            {"event": "cycle_end", "summary": "stage 1 done"},
            {
                "event": "stage_end",
                "stage_index": 1,
                "finished": True,
                "summary": "s1 summary",
            },
            {"event": "stage_start", "stage_index": 2},
            {"event": "cycle_end", "summary": "stage 2 done"},
            {
                "event": "stage_end",
                "stage_index": 2,
                "finished": True,
                "summary": "s2 summary",
            },
        ]
        lines = [json.dumps({"ts": "t", "t": 0, **e}) for e in events]
        (d / "log.jsonl").write_text("\n".join(lines) + "\n")

        state = log.parse_run(d / "log.jsonl")
        assert state is not None
        assert state.has_stages is True
        assert state.completed_stages == [1, 2]
        assert state.stage_summaries == ["s1 summary", "s2 summary"]
        assert state.completed_cycles == 2

    def test_stage_that_exhausted_budget_not_marked_complete(self, tmp_path: Path):
        """If a stage ends with finished=False, it should NOT appear in completed_stages."""
        d = log._runs_root() / "budget_run"
        d.mkdir(parents=True)
        events = [
            self._staged_run_start(str(tmp_path), "budget goal"),
            _CLI_ARGS,
            {"event": "stage_start", "stage_index": 1},
            {"event": "cycle_end", "summary": "tried hard"},
            {"event": "cycle_end", "summary": "still trying"},
            {
                "event": "stage_end",
                "stage_index": 1,
                "finished": False,
                "summary": "ran out",
            },
        ]
        lines = [json.dumps({"ts": "t", "t": 0, **e}) for e in events]
        (d / "log.jsonl").write_text("\n".join(lines) + "\n")

        state = log.parse_run(d / "log.jsonl")
        assert state is not None
        assert state.completed_stages == []
        assert state.stage_summaries == []
        assert state.completed_cycles == 2

    def test_cycles_within_stage_tracked_for_resume(self, tmp_path: Path):
        """current_stage_cycles should reflect cycles in the LAST stage only."""
        d = log._runs_root() / "midstage_run"
        d.mkdir(parents=True)
        events = [
            self._staged_run_start(str(tmp_path), "mid-stage"),
            _CLI_ARGS,
            # Stage 1 — completes in 1 cycle
            {"event": "stage_start", "stage_index": 1},
            {"event": "cycle_end", "summary": "s1 done"},
            {"event": "stage_end", "stage_index": 1, "finished": True, "summary": "s1"},
            # Stage 2 — 2 cycles, then crash (no stage_end)
            {"event": "stage_start", "stage_index": 2},
            {"event": "cycle_end", "summary": "s2 cycle 1"},
            {"event": "cycle_end", "summary": "s2 cycle 2"},
        ]
        lines = [json.dumps({"ts": "t", "t": 0, **e}) for e in events]
        (d / "log.jsonl").write_text("\n".join(lines) + "\n")

        state = log.parse_run(d / "log.jsonl")
        assert state is not None
        # current_stage_cycles = cycles within the unfinished stage 2
        assert state.current_stage_cycles == 2
        # total cycles = 1 (stage 1) + 2 (stage 2)
        assert state.completed_cycles == 3
        assert state.completed_stages == [1]

    def test_non_staged_run_has_no_stage_data(self, tmp_path: Path):
        d = log._runs_root() / "plain_run"
        d.mkdir(parents=True)
        events = [
            {**_RUN_START, "goal": "simple", "project_dir": str(tmp_path)},
            _CLI_ARGS,
            {"event": "cycle_end", "summary": "done"},
            {"event": "run_end"},
        ]
        lines = [json.dumps({"ts": "t", "t": 0, **e}) for e in events]
        (d / "log.jsonl").write_text("\n".join(lines) + "\n")

        state = log.parse_run(d / "log.jsonl")
        assert state is not None
        assert state.has_stages is False
        assert state.completed_stages == []
        assert state.current_stage_cycles == 0
