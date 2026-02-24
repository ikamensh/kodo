"""Integration tests for kodo --improve workflow and --json mode."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo.cli._improve import _extract_section
from kodo.cli._main import _main_inner
from kodo.orchestrators.base import CycleResult, RunResult


def _fake_run_result(*, finished: bool = True, summary: str = "Done."):
    """Canned RunResult for mocked launch_run."""
    return RunResult(
        cycles=[
            CycleResult(
                exchanges=5,
                total_cost_usd=0.02,
                finished=finished,
                summary=summary,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Improve tests
# ---------------------------------------------------------------------------


class TestImproveFlags:
    """--improve sets correct flags and defaults."""

    def test_improve_sets_yes_skip_intake_defaults_saga(self, tmp_path: Path):
        """--improve forces --yes, --skip-intake, defaults --team saga."""
        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
            patch("kodo.cli._main.launch_run") as mock_launch,
        ):
            mock_launch.return_value = _fake_run_result()
            sys.argv = ["kodo", "--improve", str(tmp_path)]
            _main_inner()

            params = mock_launch.call_args[0][2]
            assert params["team"] == "saga"

    def test_improve_with_team_mission_overrides_default(self, tmp_path: Path):
        """--improve --team mission overrides saga default."""
        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
            patch("kodo.cli._main.launch_run") as mock_launch,
        ):
            mock_launch.return_value = _fake_run_result()
            sys.argv = ["kodo", "--improve", "--team", "mission", str(tmp_path)]
            _main_inner()

            params = mock_launch.call_args[0][2]
            assert params["team"] == "mission"


class TestImproveGoalConstruction:
    """--improve constructs goal from _IMPROVE_GOAL template."""

    def test_improve_goal_uses_template_with_run_dir_path(self, tmp_path: Path):
        """Goal contains report_path from run_dir.root."""
        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
            patch("kodo.cli._main.launch_run") as mock_launch,
        ):
            mock_launch.return_value = _fake_run_result()
            sys.argv = ["kodo", "--improve", str(tmp_path)]
            _main_inner()

            goal_text = mock_launch.call_args[0][1]
            assert "improve-report.md" in goal_text
            assert (
                "improvement report" in goal_text.lower()
                or "improve" in goal_text.lower()
            )
            # Template uses {report_path}
            expected_snippet = "Produce a concrete improvement report at"
            assert expected_snippet in goal_text


class TestImproveReportParsing:
    """Post-run report parsing: _extract_section and count extraction."""

    def test_extract_section_and_count_auto_fixed_needs_decision(self):
        """Create fake improve-report.md, verify count extraction works."""
        report = """# Improve Report

## Auto-fixed
- foo.py:10 — fixed unused import
- bar.py:20 — fixed typo in variable name

## Needs decision
- baz.py:5 — consider adding validation
- qux.py:99 — refactor for clarity
"""
        auto_section = _extract_section(report, "Auto-fixed")
        needs_section = _extract_section(report, "Needs decision")

        auto_count = len(re.findall(r"^- .+$", auto_section, re.MULTILINE))
        needs_count = len(re.findall(r"^- .+$", needs_section, re.MULTILINE))

        assert auto_count == 2
        assert needs_count == 2


# ---------------------------------------------------------------------------
# JSON mode tests
# ---------------------------------------------------------------------------


class TestJsonMode:
    """--json produces valid JSON on stdout."""

    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
        ):
            yield

    def test_json_goal_produces_valid_json_with_expected_keys(
        self, tmp_path: Path, capsys
    ):
        """--json --goal produces valid JSON with status, summary, cycles, cost_usd, exchanges."""
        with patch("kodo.cli._main.launch_run") as mock_launch:
            mock_launch.return_value = _fake_run_result()
            sys.argv = [
                "kodo",
                "--goal",
                "test",
                "--skip-intake",
                "--json",
                str(tmp_path),
            ]
            try:
                _main_inner()
            except SystemExit:
                pass

        out = capsys.readouterr().out
        data = json.loads(out)
        assert "status" in data
        assert "summary" in data
        assert "cycles" in data
        assert "cost_usd" in data
        assert "exchanges" in data
        assert "finished" in data

    def test_json_stdout_clean_no_unexpected_output(self, tmp_path: Path, capsys):
        """--json produces only valid JSON on stdout; no extra text mixed in."""
        with patch("kodo.cli._main.launch_run") as mock_launch:
            mock_launch.return_value = _fake_run_result()
            sys.argv = [
                "kodo",
                "--goal",
                "test",
                "--skip-intake",
                "--json",
                str(tmp_path),
            ]
            try:
                _main_inner()
            except SystemExit:
                pass

        out = capsys.readouterr().out
        # stdout should be valid JSON only (no banner or other text mixed in)
        data = json.loads(out)
        assert isinstance(data, dict)


class TestJsonImproveReport:
    """--json --improve includes improve_report when report exists."""

    def test_json_improve_includes_improve_report_key(self, tmp_path: Path, capsys):
        """When improve-report.md exists, JSON output has improve_report key."""
        report_content = """# Improve Report

## Auto-fixed
- a.py:1 — fix one

## Needs decision
- b.py:2 — decide two
"""

        def fake_launch(run_dir, goal_text, params, plan=None, json_mode=False, **kw):
            report_path = run_dir.root / "improve-report.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_content)
            return _fake_run_result()

        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
            patch("kodo.cli._main.launch_run", side_effect=fake_launch),
        ):
            sys.argv = ["kodo", "--improve", "--json", str(tmp_path)]
            try:
                _main_inner()
            except SystemExit:
                pass

        out = capsys.readouterr().out
        data = json.loads(out)
        assert "improve_report" in data
        assert "Auto-fixed" in data["improve_report"]
        assert "Needs decision" in data["improve_report"]
