"""Unit tests for the benchmark package — pure logic, mocked I/O, ~2s target."""

from __future__ import annotations

import importlib
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── _util ──────────────────────────────────────────────────────────────────

from benchmark._util import (
    _docker_is_ready,
    _start_docker_macos,
    detect_backends,
    docker_safe,
    ensure_docker_running,
    iter_jsonl,
    load_json,
    load_jsonl,
    setup_logging,
)


class TestDockerSafe:
    @pytest.mark.parametrize("input_,expected", [
        ("claude", "claude"),
        ("kodo:solo", "kodo_solo"),
        ("a/b@c", "a_b_c"),
        ("a.b-c_d", "a.b-c_d"),
    ])
    def test_sanitizes_names(self, input_, expected):
        """Alphanumeric, dots, dashes, underscores pass through; everything else becomes _."""
        assert docker_safe(input_) == expected


class TestLoadJson:
    @pytest.mark.parametrize("content,expected,label", [
        (None, {}, "missing file"),
        ('{"a": 1}', {"a": 1}, "valid json"),
        ("{not json", {}, "corrupt json"),
    ])
    def test_returns_dict_or_empty(self, tmp_path, content, expected, label):
        """Missing/corrupt files return {}; valid files parse correctly."""
        f = tmp_path / "data.json"
        if content is not None:
            f.write_text(content)
        assert load_json(f) == expected


class TestLoadJsonl:
    def test_valid_lines(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"a":1}\n{"b":2}\n')
        assert load_jsonl(f) == [{"a": 1}, {"b": 2}]

    @pytest.mark.parametrize("content,label", [
        (None, "missing file"),
        ('{"a":1}\n\n{"b":2}\n', "blank lines"),
        ('{"a":1}\nnot json\n{"b":2}\n', "malformed lines"),
    ])
    def test_edge_cases(self, tmp_path, content, label):
        """Missing file returns []; blank/malformed lines are skipped."""
        f = tmp_path / "data.jsonl"
        if content is not None:
            f.write_text(content)
        result = load_jsonl(f)
        if content is None:
            assert result == []
        else:
            assert result == [{"a": 1}, {"b": 2}]


class TestIterJsonl:
    @pytest.mark.parametrize("content,expected,label", [
        (None, [], "missing file"),
        ('{"x":1}\n{"x":2}\n', [{"x": 1}, {"x": 2}], "valid lines"),
        ('{"x":1}\nbad\n{"x":3}\n', [{"x": 1}, {"x": 3}], "skips bad lines"),
    ])
    def test_iter_jsonl(self, tmp_path, content, expected, label):
        """Missing files yield []; bad lines are skipped; good lines parse."""
        f = tmp_path / "data.jsonl"
        if content is not None:
            f.write_text(content)
        assert list(iter_jsonl(f)) == expected


class TestSetupLogging:
    def test_does_not_raise(self):
        setup_logging(verbose=False)
        setup_logging(verbose=True)


class TestDetectBackends:
    def test_nothing_on_path_still_includes_kodo(self):
        with patch("benchmark._util.shutil.which", autospec=True, return_value=None):
            assert detect_backends() == ["kodo"]

    def test_detects_all_when_everything_on_path(self):
        with patch("benchmark._util.shutil.which", autospec=True, return_value="/usr/bin/x"):
            assert set(detect_backends()) == {"kodo", "claude", "cursor", "codex", "gemini"}


class TestDockerIsReady:
    @pytest.mark.parametrize("side_effect,returncode,expected", [
        (None, 0, True),
        (None, 1, False),
        (FileNotFoundError, None, False),
        (subprocess.TimeoutExpired("docker", 10), None, False),
    ])
    def test_ready_iff_returncode_zero(self, side_effect, returncode, expected):
        """Ready only when docker info returns 0; errors/timeouts return False."""
        if side_effect:
            with patch("subprocess.run", autospec=True, side_effect=side_effect):
                assert _docker_is_ready() is expected
        else:
            with patch("subprocess.run", autospec=True, return_value=MagicMock(returncode=returncode)):
                assert _docker_is_ready() is expected


class TestStartDockerMacos:
    def test_orbstack_preferred_then_docker_desktop_fallback(self):
        """Orbstack is tried first; Docker Desktop is fallback; both failing returns False."""
        with (
            patch("benchmark._util.shutil.which", autospec=True, return_value="/opt/homebrew/bin/orbctl"),
            patch("subprocess.run", autospec=True) as mock_run,
        ):
            assert _start_docker_macos() is True
            assert mock_run.call_args[0][0] == ["orbctl", "start"]
        with (
            patch("benchmark._util.shutil.which", autospec=True, return_value=None),
            patch("subprocess.run", autospec=True) as mock_run,
        ):
            assert _start_docker_macos() is True
            assert mock_run.call_args[0][0] == ["open", "-a", "Docker"]
        with (
            patch("benchmark._util.shutil.which", autospec=True, return_value=None),
            patch("subprocess.run", autospec=True, side_effect=FileNotFoundError),
        ):
            assert _start_docker_macos() is False


class TestEnsureDockerRunning:
    def test_already_running(self):
        with patch(
            "benchmark._util._docker_is_ready", autospec=True, return_value=True
        ):
            assert ensure_docker_running() is True

    def test_starts_and_becomes_ready(self):
        calls = {"n": 0}

        def ready_after_one():
            calls["n"] += 1
            return calls["n"] > 1

        with (
            patch(
                "benchmark._util._docker_is_ready",
                autospec=True,
                side_effect=ready_after_one,
            ),
            patch(
                "benchmark._util._start_docker_macos", autospec=True, return_value=True
            ),
            patch(
                "benchmark._util.platform.system", autospec=True, return_value="Darwin"
            ),
            patch("benchmark._util.time.sleep", autospec=True),
        ):
            assert ensure_docker_running(timeout=10) is True

    def test_returns_false_on_linux(self):
        with (
            patch(
                "benchmark._util._docker_is_ready", autospec=True, return_value=False
            ),
            patch(
                "benchmark._util.platform.system", autospec=True, return_value="Linux"
            ),
        ):
            assert ensure_docker_running() is False


# ── tasks ──────────────────────────────────────────────────────────────────

from benchmark.tasks import SWETask, _parse_list_field, _row_to_task


class TestParseListField:
    @pytest.mark.parametrize("input_,expected", [
        (["a", "b"], ["a", "b"]),
        ('["a", "b"]', ["a", "b"]),
        ("['a', 'b']", ["a", "b"]),
    ])
    def test_parses_lists(self, input_, expected):
        """Lists, JSON strings, and Python repr strings all parse correctly."""
        assert _parse_list_field(input_) == expected

    @pytest.mark.parametrize("input_,expected", [
        ("[]", []),
        ("not a list", []),
    ])
    def test_edge_cases(self, input_, expected):
        """Empty list and garbage input return []."""
        assert _parse_list_field(input_) == expected


class TestRowToTask:
    def test_parses_all_fields_including_uppercase(self):
        """Parses lists from JSON strings, handles uppercase field names."""
        row = {
            "instance_id": "repo__name-123", "repo": "owner/repo",
            "base_commit": "abc123", "problem_statement": "Fix the bug",
            "FAIL_TO_PASS": '["test_foo"]', "PASS_TO_PASS": '["test_bar"]',
            "version": "1.0",
        }
        task = _row_to_task(row)
        assert task.instance_id == "repo__name-123"
        assert task.fail_to_pass == ["test_foo"]
        assert task.pass_to_pass == ["test_bar"]

    def test_missing_optional_fields_default_empty(self):
        row = {"instance_id": "id1", "repo": "o/r", "base_commit": "abc", "problem_statement": "desc"}
        task = _row_to_task(row)
        assert task.version == ""
        assert task.fail_to_pass == []


# ── runner ─────────────────────────────────────────────────────────────────

from benchmark.runner import (
    TaskResult,
    _build_prompt,
    _load_completed,
    _parse_json_output,
    _timeout_for_arm,
    _append_result,
    _append_prediction,
    _safe_run,
    parse_arm,
)


class TestParseArm:
    @pytest.mark.parametrize("arm,expected", [
        ("claude", ("claude", None)),
        ("kodo:solo", ("kodo", "solo")),
        ("kodo:solo+opus", ("kodo", "solo+opus")),
    ])
    def test_splits_backend_and_team(self, arm, expected):
        assert parse_arm(arm) == expected


class TestTimeoutForArm:
    @pytest.mark.parametrize("arm,expected", [
        ("kodo", 999), ("kodo:solo", 999),  # kodo uses kodo_timeout
        ("claude", 100), ("cursor", 100),   # others use default
    ])
    def test_kodo_gets_kodo_timeout(self, arm, expected):
        assert _timeout_for_arm(arm, 100, 999) == expected


class TestBuildPrompt:
    def test_contains_instance_id(self):
        task = SWETask(
            instance_id="repo__name-123",
            repo="owner/repo",
            base_commit="abc",
            problem_statement="Something is broken",
            fail_to_pass=[],
            pass_to_pass=[],
        )
        prompt = _build_prompt(task)
        assert "repo__name-123" in prompt
        assert "Something is broken" in prompt
        assert "Do not add or modify tests" in prompt


class TestParseJsonOutput:
    @pytest.mark.parametrize("text,expected", [
        ('{"a": 1}', {"a": 1}),
        ('some log\n{"a": 1}', {"a": 1}),
        ('log1\n{"a": 1}\nlog2', {"a": 1}),
    ])
    def test_extracts_json(self, text, expected):
        """Finds JSON whether it's the whole output, last line, or buried."""
        assert _parse_json_output(text) == expected

    @pytest.mark.parametrize("text", ["", "just text\nno json here"])
    def test_returns_empty_when_no_json(self, text):
        assert _parse_json_output(text) == {}

    def test_prefers_full_parse(self):
        """If full stdout is valid JSON, use that over line-by-line scan."""
        data = json.dumps({"full": True})
        assert _parse_json_output(data) == {"full": True}


class TestAppendResult:
    def test_writes_and_appends_jsonl_with_seed(self, tmp_path):
        """Writes JSONL, appends multiple entries, stores seed (default 0)."""
        _append_result(tmp_path, TaskResult("id1", "claude", "patch", 10.0, "ok"))
        _append_result(tmp_path, TaskResult("id2", "b", "", 2.0, "error", error="fail"), seed=2)
        lines = (tmp_path / "results.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        e1, e2 = json.loads(lines[0]), json.loads(lines[1])
        assert (e1["instance_id"], e1["seed"]) == ("id1", 0)
        assert (e2["instance_id"], e2["seed"]) == ("id2", 2)

    def test_calls_fsync(self, tmp_path):
        """Crash safety: writes are flushed and fsynced."""
        with patch("benchmark.runner.os.fsync", autospec=True) as mock_fsync:
            _append_result(tmp_path, TaskResult("id1", "claude", "patch", 10.0, "ok"))
            assert mock_fsync.called


class TestAppendPrediction:
    def test_writes_prediction_with_sanitized_filename_but_original_arm(self, tmp_path):
        """Filename uses sanitized arm; entry stores original arm for lossless round-trips."""
        result = TaskResult("id1", "kodo:solo_opus", "diff --git ...", 10.0, "ok")
        _append_prediction(tmp_path, result)
        files = list(tmp_path.glob("predictions-*.jsonl"))
        assert len(files) == 1
        assert ":" not in files[0].name  # sanitized filename
        entry = json.loads(files[0].read_text().strip())
        assert entry["model_patch"] == "diff --git ..."
        assert entry["arm"] == "kodo:solo_opus"  # original arm preserved
        assert entry["model_name_or_path"] == "kodo_solo_opus"  # sanitized


class TestLoadCompleted:
    def test_loads_pairs_and_skips_bad_lines(self, tmp_path):
        """Empty dir returns empty set; loads pairs; skips bad JSON lines."""
        assert _load_completed(tmp_path) == set()
        (tmp_path / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude"}\nbad json\n'
            '{"instance_id":"id2","arm":"kodo"}\n'
        )
        assert _load_completed(tmp_path) == {("id1", "claude"), ("id2", "kodo")}


class TestSafeRun:
    def test_catches_exceptions(self):
        task = SWETask("id1", "owner/repo", "abc", "problem", [], [])
        with patch(
            "benchmark.runner._run_single_task",
            autospec=True,
            side_effect=RuntimeError("boom"),
        ):
            result = _safe_run(task, "claude", Path("/tmp"), 60)
        assert result.status == "error"
        assert "boom" in result.error
        assert result.patch == ""


# ── report ─────────────────────────────────────────────────────────────────

from benchmark.report import (
    _dataset_short,
    _eval_key,
    _median,
    _percentile,
    generate_report,
    print_status,
)


class TestMedian:
    @pytest.mark.parametrize("vals,expected", [
        ([3, 1, 2], 2), ([1, 2, 3, 4], 2.5), ([5], 5),
    ])
    def test_median(self, vals, expected):
        assert _median(vals) == expected


class TestPercentile:
    @pytest.mark.parametrize("vals,pct,expected", [
        (list(range(1, 101)), 90, 91),
        ([1, 2, 3, 4, 5], 50, 3),
    ])
    def test_percentile(self, vals, pct, expected):
        assert _percentile(vals, pct) == expected


class TestDatasetShort:
    @pytest.mark.parametrize("full,short", [
        ("princeton-nlp/SWE-bench_Verified", "Verified"),
        ("ScaleAI/SWE-bench_Pro", "Pro"),
        ("princeton-nlp/SWE-bench_Lite", "Lite"),
        ("org/Custom_Dataset", "Custom_Dataset"),
    ])
    def test_extracts_short_name(self, full, short):
        assert _dataset_short(full) == short


class TestEvalKey:
    @pytest.mark.parametrize("input_,expected", [
        ("claude", "claude"),
        ("kodo:solo", "kodo_solo"),
    ])
    def test_sanitizes(self, input_, expected):
        assert _eval_key(input_) == expected


class TestGenerateReport:
    def test_basic_report(self, tmp_path):
        run_dir = tmp_path / "runs" / "test_run"
        run_dir.mkdir(parents=True)

        meta = {
            "dataset": "princeton-nlp/SWE-bench_Lite",
            "task_count": 2,
            "arms": ["claude", "kodo"],
        }
        (run_dir / "meta.json").write_text(json.dumps(meta))

        results = [
            {"instance_id": "id1", "arm": "claude", "status": "ok", "elapsed_s": 100},
            {"instance_id": "id1", "arm": "kodo", "status": "ok", "elapsed_s": 200},
        ]
        (run_dir / "results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in results) + "\n"
        )
        (run_dir / "eval-summary.json").write_text("{}")

        ret = generate_report(tmp_path, "test_run")
        assert ret == 0
        report = (run_dir / "report.md").read_text()
        assert "SWE-bench_Lite" in report
        assert "claude" in report
        assert "Timing" in report

    def test_report_with_eval(self, tmp_path):
        run_dir = tmp_path / "runs" / "test_run"
        run_dir.mkdir(parents=True)

        meta = {"dataset": "d", "task_count": 2, "arms": ["claude", "kodo"]}
        (run_dir / "meta.json").write_text(json.dumps(meta))
        (run_dir / "results.jsonl").write_text("")

        eval_summary = {
            "claude": {
                "resolved": ["id1"],
                "failed": ["id2"],
                "error": [],
                "resolve_rate": 0.5,
            },
            "kodo": {
                "resolved": ["id1", "id2"],
                "failed": [],
                "error": [],
                "resolve_rate": 1.0,
            },
        }
        (run_dir / "eval-summary.json").write_text(json.dumps(eval_summary))

        ret = generate_report(tmp_path, "test_run")
        assert ret == 0
        report = (run_dir / "report.md").read_text()
        assert "Resolution Rates" in report
        assert "Head-to-Head" in report
        assert "kodo only" in report


class TestPrintStatus:
    def test_returns_zero_regardless_of_state(self, tmp_path):
        """No runs, empty runs, or populated runs all return 0."""
        assert print_status(tmp_path) == 0  # no runs dir
        (tmp_path / "runs").mkdir()
        assert print_status(tmp_path) == 0  # empty runs dir
        run_dir = tmp_path / "runs" / "run1"
        run_dir.mkdir()
        meta = {"dataset": "princeton-nlp/SWE-bench_Verified", "task_count": 5, "arms": ["claude"]}
        (run_dir / "meta.json").write_text(json.dumps(meta))
        (run_dir / "results.jsonl").write_text('{"instance_id":"id1","arm":"claude","status":"ok"}\n')
        assert print_status(tmp_path) == 0  # with data


# ── runner (continued) ─────────────────────────────────────────────────────

from benchmark.runner import (
    _clean_env,
    _load_global_completed,
    _run_subprocess,
    _save_run_meta,
    run_benchmark,
)


class TestCleanEnv:
    def test_strips_claudecode_and_api_key(self):
        """CLAUDECODE always removed; API key removed unless keep_api_key=True."""
        with patch.dict("os.environ", {"CLAUDECODE": "1", "ANTHROPIC_API_KEY": "sk-xxx", "PATH": "/usr/bin"}, clear=True):
            env = _clean_env()
            assert "CLAUDECODE" not in env
            assert "ANTHROPIC_API_KEY" not in env
            assert "PATH" in env
            env2 = _clean_env(keep_api_key=True)
            assert env2["ANTHROPIC_API_KEY"] == "sk-xxx"


class TestRunSubprocess:
    def test_success(self):
        output, status, error, stdout, stderr = _run_subprocess(
            ["echo", '{"ok": true}'],
            cwd=None,
            timeout=5,
        )
        assert status == "ok"
        assert output == {"ok": True}
        assert error == ""

    @pytest.mark.parametrize("cmd,timeout,expected_status,error_substr", [
        (["sleep", "10"], 0.05, "timeout", "Timed out"),
        (["false"], 5, "error", ""),
        ([sys.executable, "-c", "import sys; sys.exit(2)"], 5, "partial", ""),
    ])
    def test_failure_modes(self, cmd, timeout, expected_status, error_substr):
        """Timeout, nonzero exit, and exit-code-2 (partial) are classified correctly."""
        output, status, error, stdout, stderr = _run_subprocess(
            cmd, cwd=None, timeout=timeout,
        )
        assert status == expected_status
        if error_substr:
            assert error_substr in error


class TestSaveRunMeta:
    def test_creates_meta_and_does_not_overwrite(self, tmp_path):
        tasks = [SWETask("id1", "o/r", "abc", "desc", [], [])]
        _save_run_meta(tmp_path, tasks, ["claude"], 7200, dataset="test")
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["task_count"] == 1 and meta["arms"] == ["claude"]
        # Does not overwrite existing
        _save_run_meta(tmp_path, [], [], 100)
        assert json.loads((tmp_path / "meta.json").read_text()) == meta


class TestLoadGlobalCompleted:
    def test_empty_and_basic_loading(self, tmp_path):
        """No runs dir returns empty; single and multiple runs accumulate."""
        assert _load_global_completed(tmp_path) == set()
        for name, iid in [("r1", "id1"), ("r2", "id2")]:
            d = tmp_path / "runs" / name
            d.mkdir(parents=True)
            (d / "results.jsonl").write_text(
                f'{{"instance_id":"{iid}","arm":"claude","status":"ok"}}\n'
            )
        assert _load_global_completed(tmp_path) == {("id1", "claude"), ("id2", "claude")}

    @pytest.mark.parametrize("status,expected_in_set", [
        ("ok", True), ("partial", True),  # partial has valid patch
        ("error", False), ("timeout", False),
    ])
    def test_status_filtering(self, tmp_path, status, expected_in_set):
        """ok/partial count as completed; error/timeout are skipped."""
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            f'{{"instance_id":"id1","arm":"claude","status":"{status}"}}\n'
        )
        result = _load_global_completed(tmp_path)
        assert (("id1", "claude") in result) == expected_in_set

    def test_exclude_run_dir_and_no_data_duplication(self, tmp_path):
        """Excluded run dirs are ignored; results are NOT copied into current run."""
        r1 = tmp_path / "runs" / "r1"
        r2 = tmp_path / "runs" / "r2"
        for d in (r1, r2):
            d.mkdir(parents=True)
        (r1 / "results.jsonl").write_text('{"instance_id":"id1","arm":"claude","status":"ok"}\n')
        (r2 / "results.jsonl").write_text('{"instance_id":"id2","arm":"claude","status":"ok"}\n')
        completed = _load_global_completed(tmp_path, exclude_run_dir=r2)
        assert completed == {("id1", "claude")}
        assert not (r2 / "results_copied.jsonl").exists()  # no duplication

    def test_skips_bad_lines(self, tmp_path):
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"ok"}\nbad json\n'
            '{"instance_id":"id2","arm":"kodo","status":"ok"}\n'
        )
        assert _load_global_completed(tmp_path) == {("id1", "claude"), ("id2", "kodo")}

    def test_seed_filtering_and_legacy_default(self, tmp_path):
        """Seed filtering: each seed sees only matching results; no-seed defaults to 0."""
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"ok","seed":0}\n'
            '{"instance_id":"id2","arm":"claude","status":"ok","seed":1}\n'
            '{"instance_id":"id3","arm":"claude","status":"ok"}\n'  # legacy, no seed field
        )
        assert _load_global_completed(tmp_path, seed=0) == {("id1", "claude"), ("id3", "claude")}
        assert _load_global_completed(tmp_path, seed=1) == {("id2", "claude")}
        assert _load_global_completed(tmp_path, seed=2) == set()


class TestRunBenchmark:
    def test_sequential_with_mock(self, tmp_path):
        task = SWETask("id1", "o/r", "abc", "problem", [], [])
        fake_result = TaskResult("id1", "claude", "patch", 5.0, "ok")

        with (
            patch(
                "benchmark.runner._safe_run", autospec=True, return_value=fake_result
            ),
            patch("benchmark.runner._save_run_meta", autospec=True),
        ):
            run_benchmark(
                tasks=[task],
                arms=["claude"],
                workspace=tmp_path,
                run_id="test",
                timeout=100,
            )

        # Result should be written
        results_file = tmp_path / "runs" / "test" / "results.jsonl"
        assert results_file.exists()

    def test_skips_globally_completed(self, tmp_path):
        """Tasks completed in prior runs are not re-executed."""
        task = SWETask("id1", "o/r", "abc", "problem", [], [])

        # Create a prior run with this task completed.
        # Use the normalized arm name (bare "claude" expands to "claude:opus").
        prior = tmp_path / "runs" / "prior"
        prior.mkdir(parents=True)
        (prior / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude:opus","status":"ok"}\n'
        )

        with (
            patch("benchmark.runner._safe_run", autospec=True) as mock_run,
            patch("benchmark.runner._save_run_meta", autospec=True),
        ):
            run_benchmark(
                tasks=[task],
                arms=["claude"],
                workspace=tmp_path,
                run_id="test",
                timeout=100,
            )

        # _safe_run should NOT have been called — task already done
        mock_run.assert_not_called()


# ── evaluate ───────────────────────────────────────────────────────────────

from benchmark.evaluate import (
    _EvalHeartbeat,
    _count_pro_progress,
    _docker_safe as eval_docker_safe,
    _emit_eval_diagnostics,
    _make_stall_monitor,
    _parse_pro_results,
    _parse_standard_results,
    _run_eval_subprocess,
    _collect_eval_results,
    evaluate_predictions,
)


class TestEvalDockerSafe:
    def test_basic(self):
        assert eval_docker_safe("kodo:solo") == "kodo_solo"


class TestRunEvalSubprocess:
    @pytest.mark.skipif(os.name == "nt", reason="os.killpg is POSIX-only")
    def test_timeout_kills_process_group(self):
        """Timeout should reap the evaluator group so later arms can continue."""
        proc = MagicMock()
        proc.pid = 321
        proc.poll.return_value = None
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(["python"], 10),
            None,
        ]

        with (
            patch(
                "benchmark.evaluate.subprocess.Popen", autospec=True, return_value=proc
            ),
            patch("benchmark.evaluate.os.killpg", autospec=True) as mock_killpg,
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                _run_eval_subprocess(["python"], timeout=10, heartbeat=_EvalHeartbeat())

        mock_killpg.assert_called_once_with(321, signal.SIGKILL)

    def test_nonzero_exit_raises(self):
        """Non-zero exit should still surface as a CalledProcessError."""
        proc = MagicMock()
        proc.wait.return_value = 7

        with patch(
            "benchmark.evaluate.subprocess.Popen", autospec=True, return_value=proc
        ):
            with pytest.raises(subprocess.CalledProcessError):
                _run_eval_subprocess(["python"], timeout=10)

    @pytest.mark.skipif(os.name == "nt", reason="os.killpg is POSIX-only")
    def test_timeout_writes_diagnostics(self, tmp_path):
        """Timeouts emit a stall snapshot so hangs leave evidence behind."""
        proc = MagicMock()
        proc.pid = 321
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired(["python"], 10), None]
        proc.stdout = iter(())

        with (
            patch(
                "benchmark.evaluate.subprocess.Popen", autospec=True, return_value=proc
            ),
            patch("benchmark.evaluate.os.killpg", autospec=True),
            patch(
                "benchmark.evaluate._capture_command",
                autospec=True,
                return_value={"stdout": []},
            ),
        ):
            with pytest.raises(subprocess.TimeoutExpired):
                _run_eval_subprocess(
                    ["python"],
                    timeout=10,
                    heartbeat=_EvalHeartbeat(),
                    context="test eval",
                    diagnostic_dir=tmp_path,
                )

        snapshot = json.loads((tmp_path / "stall-diagnostics.json").read_text())
        assert snapshot["context"] == "test eval timeout"
        assert snapshot["pid"] == 321


class TestCountProProgress:
    def test_counts_finished_instance_outputs(self, tmp_path):
        """An instance is complete once the evaluator writes its *_output.json file."""
        inst_done = tmp_path / "instance_done"
        inst_done.mkdir()
        (inst_done / "kodo_output.json").write_text("{}")
        inst_running = tmp_path / "instance_running"
        inst_running.mkdir()

        completed, in_progress = _count_pro_progress(tmp_path)

        assert completed == 1
        assert in_progress == ["instance_running"]


class TestEvalDiagnostics:
    def test_emit_eval_diagnostics_writes_snapshot(self, tmp_path, caplog):
        """Diagnostic snapshots include the stalled state and persist to disk."""
        heartbeat = _EvalHeartbeat()
        heartbeat.note_progress(completed=2, in_progress=["instance_a", "instance_b"])
        proc = MagicMock()
        proc.pid = 777
        proc.poll.return_value = None
        (tmp_path / "trace.log").write_text("hello")

        with patch(
            "benchmark.evaluate._capture_command",
            autospec=True,
            return_value={"stdout": []},
        ):
            with caplog.at_level(logging.WARNING):
                path = _emit_eval_diagnostics(
                    proc,
                    heartbeat,
                    context="pro eval for cursor",
                    diagnostic_dir=tmp_path,
                )

        assert path == tmp_path / "stall-diagnostics.json"
        snapshot = json.loads(path.read_text())
        assert snapshot["context"] == "pro eval for cursor"
        assert snapshot["completed"] == 2
        assert snapshot["in_progress"] == ["instance_a", "instance_b"]
        assert any("appears stalled" in message for message in caplog.messages)

    def test_stall_monitor_emits_diagnostics_after_idle_threshold(self, tmp_path):
        """A quiet evaluator should trigger diagnostics once it is idle long enough."""
        proc = MagicMock()
        proc.poll.side_effect = [None, 0]
        heartbeat = _EvalHeartbeat()
        stale = time.monotonic() - 10_000
        heartbeat.started_at = stale
        heartbeat.last_output_at = stale
        heartbeat.last_progress_at = stale
        heartbeat.last_filesystem_at = stale
        heartbeat.last_diagnostic_at = stale

        stop_event = threading.Event()
        with (
            patch("benchmark.evaluate._stall_seconds", autospec=True, return_value=1),
            patch(
                "benchmark.evaluate._stall_repeat_seconds",
                autospec=True,
                return_value=3600,
            ),
            patch(
                "benchmark.evaluate._stall_check_seconds", autospec=True, return_value=0
            ),
            patch(
                "benchmark.evaluate._emit_eval_diagnostics", autospec=True
            ) as mock_emit,
            patch.object(stop_event, "wait", side_effect=[False, True]),
        ):
            monitor = _make_stall_monitor(
                stop_event,
                proc,
                heartbeat,
                context="pro eval for cursor",
                diagnostic_dir=tmp_path,
            )
            monitor()

        mock_emit.assert_called_once()


class TestParseProResults:
    def test_missing_or_empty_file(self, tmp_path):
        assert _parse_pro_results(tmp_path)["resolve_rate"] == 0.0
        (tmp_path / "eval_results.json").write_text("{}")
        assert _parse_pro_results(tmp_path)["resolve_rate"] == 0.0

    def test_classifies_resolved_failed_and_infra_error(self, tmp_path):
        """Resolved=True, resolved=False with report=failed, no report=error."""
        data = {"id1": True, "id2": False, "id3": False}
        (tmp_path / "eval_results.json").write_text(json.dumps(data))
        # id2 has report.json -> genuine failure; id3 has no report -> infra error
        inst_dir = tmp_path / "id2"
        inst_dir.mkdir()
        (inst_dir / "report.json").write_text(json.dumps({"id2": {"resolved": False}}))
        result = _parse_pro_results(tmp_path)
        assert sorted(result["resolved"]) == ["id1"]
        assert result["failed"] == ["id2"]
        assert result["error"] == ["id3"]
        assert result["resolve_rate"] == pytest.approx(1 / 3)


class TestParseStandardResults:
    def test_classifies_resolved_failed_missing_and_corrupt(self, tmp_path):
        """Resolved, failed, missing report (error), and corrupt report (error)."""
        eval_dir = tmp_path / "eval_results"
        eval_dir.mkdir()
        for name, data in [
            ("id1", {"id1": {"resolved": True}}),
            ("id2", {"id2": {"resolved": False}}),
        ]:
            d = eval_dir / name
            d.mkdir()
            (d / "report.json").write_text(json.dumps(data))
        (eval_dir / "id3").mkdir()  # no report -> error
        d4 = eval_dir / "id4"
        d4.mkdir()
        (d4 / "report.json").write_text("not json")  # corrupt -> error
        result = _parse_standard_results(eval_dir)
        assert result["resolved"] == ["id1"]
        assert result["failed"] == ["id2"]
        assert sorted(result["error"]) == ["id3", "id4"]
        assert result["resolve_rate"] == pytest.approx(1 / 4)


class TestCollectEvalResults:
    def test_collects_pro_results_and_creates_eval_dir_if_missing(self, tmp_path):
        """Handles missing eval dir gracefully; collects pro-mode results."""
        _collect_eval_results(tmp_path, is_pro=True)
        assert (tmp_path / "eval").is_dir()
        assert json.loads((tmp_path / "eval-summary.json").read_text()) == {}
        # Now with data
        tmp2 = tmp_path / "with_data"
        tmp2.mkdir()
        eval_dir = tmp2 / "eval" / "claude"
        eval_dir.mkdir(parents=True)
        (eval_dir / "eval_results.json").write_text(json.dumps({"id1": True, "id2": False}))
        _collect_eval_results(tmp2, is_pro=True)
        summary = json.loads((tmp2 / "eval-summary.json").read_text())
        assert summary["claude"]["resolved"] == ["id1"]


class TestEvaluatePredictions:
    def test_writes_summary_when_docker_available(self, tmp_path):
        """No predictions + docker available = empty eval-summary.json created."""
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "meta.json").write_text(json.dumps({"dataset": "princeton-nlp/SWE-bench_Lite"}))
        with patch("benchmark._util.ensure_docker_running", autospec=True, return_value=True):
            evaluate_predictions(tmp_path, "r1")
        assert (run_dir / "eval-summary.json").exists()

    def test_skips_when_docker_unavailable(self, tmp_path):
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        with patch("benchmark._util.ensure_docker_running", autospec=True, return_value=False):
            evaluate_predictions(tmp_path, "r1")
        assert not (run_dir / "eval-summary.json").exists()


# ── upload tracker ────────────────────────────────────────────────────────

from benchmark.online.upload_tracker import (
    flush_pending_uploads,
    load_uploaded,
    mark_uploaded,
)
from benchmark.online.validation import suspicious_upload_reason


class TestMarkUploaded:
    def test_creates_and_appends(self, tmp_path):
        mark_uploaded(tmp_path, "id1", "claude", "run1")
        assert (tmp_path / "uploaded.jsonl").exists()
        mark_uploaded(tmp_path, "id2", "kodo:solo", "run1")
        assert len((tmp_path / "uploaded.jsonl").read_text().strip().split("\n")) == 2


class TestLoadUploaded:
    def test_loads_triples_with_seeds_and_skips_bad_lines(self, tmp_path):
        """Loads (id, arm, seed) triples; missing file returns empty; bad lines skipped."""
        assert load_uploaded(tmp_path) == set()
        mark_uploaded(tmp_path, "id1", "claude", "run1", seed=0)
        mark_uploaded(tmp_path, "id1", "claude", "run2", seed=1)
        mark_uploaded(tmp_path, "id2", "kodo:solo", "run3")
        # Inject bad line
        with open(tmp_path / "uploaded.jsonl", "a") as f:
            f.write("bad json\n")
        uploaded = load_uploaded(tmp_path)
        assert uploaded == {("id1", "claude", 0), ("id1", "claude", 1), ("id2", "kodo:solo", 0)}


class TestOnlineValidation:
    @pytest.mark.parametrize("patch_len,agent_output,expected_reason", [
        (100, {}, None),  # real patch, empty output is fine (e.g. cursor)
        (0, {}, "empty_agent_output"),  # no patch AND no output
        (0, {"status": "ok", "finished": True}, "no_patch"),  # output but no patch
    ])
    def test_patch_and_output_validation(self, patch_len, agent_output, expected_reason):
        reason = suspicious_upload_reason(
            status="ok", elapsed_s=42.0, patch_len=patch_len, agent_output=agent_output,
        )
        assert reason == expected_reason

    def test_flags_kodo_worker_broken(self):
        """Detects broken kodo workers from agent_output or error log."""
        for kwargs in [
            dict(arm="kodo:solo", status="error", elapsed_s=627.6, patch_len=4624,
                 error="worker trace", agent_output={"status": "error", "error": "unknown error"}),
            dict(arm="kodo:solo", status="error", elapsed_s=1566.5, patch_len=1551,
                 error="[worker] error: unknown error", agent_output="transcript"),
        ]:
            assert suspicious_upload_reason(**kwargs) == "kodo_worker_broken"

    @pytest.mark.parametrize("arm,expected_reason", [
        ("cursor", "bare_arm_missing_model"),
        ("claude", "bare_arm_missing_model"),
        ("kodo", "bare_arm_missing_model"),
        ("cursor:composer-2", None),
        ("claude:opus", None),
        ("kodo:solo", None),
        ("gemini", None),
    ])
    def test_bare_arm_rejection(self, arm, expected_reason):
        """Arms without model suffix are rejected (except gemini)."""
        reason = suspicious_upload_reason(
            arm=arm, status="ok", elapsed_s=42.0, patch_len=100,
            agent_output={"status": "ok"},
        )
        assert reason == expected_reason


class TestMultiRunAggregation:
    def test_unpack_legacy_and_new_format(self):
        """Legacy (flat dict) wraps in {"0": data}; new format (with "runs") passes through."""
        from benchmark.online.db import _unpack_arm_runs
        legacy = {"status": "ok", "elapsed_s": 42, "eval_status": True, "resolved": True}
        assert _unpack_arm_runs(legacy) == {"0": legacy}
        new = {"runs": {"0": {"status": "ok", "resolved": True, "eval_status": True},
                        "1": {"status": "ok", "resolved": False, "eval_status": True}}}
        runs = _unpack_arm_runs(new)
        assert len(runs) == 2 and runs["0"]["resolved"] is True

    @pytest.mark.parametrize("runs,n_runs,n_eval,rate,resolved", [
        ({"0": {"status": "ok", "eval_status": True, "resolved": True}}, 1, 1, 1.0, True),
        ({"0": {"status": "ok", "eval_status": True, "resolved": True},
          "1": {"status": "ok", "eval_status": True, "resolved": False},
          "2": {"status": "ok", "eval_status": True, "resolved": True}}, 3, 3, 2/3, True),
        ({"0": {"status": "ok", "eval_status": True, "resolved": False},
          "1": {"status": "ok"}}, 2, 1, 0.0, False),
    ])
    def test_aggregate_arm(self, runs, n_runs, n_eval, rate, resolved):
        from benchmark.online.db import _aggregate_arm
        agg = _aggregate_arm(runs)
        assert (agg["n_runs"], agg["n_evaluated"]) == (n_runs, n_eval)
        assert agg["resolve_rate"] == pytest.approx(rate)
        assert agg["resolved"] is resolved


class TestFlushPendingUploads:
    def _setup_run(self, workspace, run_id, results, predictions=None):
        """Helper: create a run dir with results and optional predictions."""
        run_dir = workspace / "runs" / run_id
        run_dir.mkdir(parents=True)
        meta = {"dataset": "princeton-nlp/SWE-bench_Lite", "arms": ["claude"]}
        (run_dir / "meta.json").write_text(json.dumps(meta))
        (run_dir / "results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in results) + "\n"
        )
        if predictions:
            for fname, preds in predictions.items():
                (run_dir / fname).write_text(
                    "\n".join(json.dumps(p) for p in preds) + "\n"
                )
        return run_dir

    def test_no_config_returns_error_no_runs_returns_ok(self, tmp_path):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=False):
            assert flush_pending_uploads(tmp_path) == 1
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True):
            assert flush_pending_uploads(tmp_path) == 0

    def test_uploads_pending(self, tmp_path):
        self._setup_run(
            tmp_path,
            "r1",
            [{"instance_id": "id1", "arm": "claude", "status": "ok", "elapsed_s": 10}],
            {
                "predictions-claude.jsonl": [
                    {
                        "instance_id": "id1",
                        "model_name_or_path": "claude",
                        "arm": "claude",
                        "model_patch": "diff",
                    }
                ]
            },
        )

        with (
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.upload_task_result", autospec=True
            ) as mock_upload,
        ):
            ret = flush_pending_uploads(tmp_path)

        assert ret == 0
        mock_upload.assert_called_once()
        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs["instance_id"] == "id1"
        assert call_kwargs["patch"] == "diff"
        # Should be marked as uploaded (with default seed=0)
        assert ("id1", "claude", 0) in load_uploaded(tmp_path)

    def test_skips_already_uploaded(self, tmp_path):
        self._setup_run(
            tmp_path,
            "r1",
            [{"instance_id": "id1", "arm": "claude", "status": "ok"}],
        )
        # Pre-mark as uploaded
        mark_uploaded(tmp_path, "id1", "claude", "r1")

        with (
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.upload_task_result", autospec=True
            ) as mock_upload,
        ):
            ret = flush_pending_uploads(tmp_path)

        assert ret == 0
        mock_upload.assert_not_called()

    def test_handles_upload_failure(self, tmp_path):
        self._setup_run(
            tmp_path,
            "r1",
            [{"instance_id": "id1", "arm": "claude", "status": "ok"}],
        )

        with (
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.upload_task_result",
                autospec=True,
                side_effect=Exception("server down"),
            ),
        ):
            ret = flush_pending_uploads(tmp_path)

        assert ret == 1  # failure
        # Should NOT be marked as uploaded
        assert load_uploaded(tmp_path) == set()

    def test_skips_suspicious_rows_without_hitting_server(self, tmp_path):
        self._setup_run(
            tmp_path,
            "r1",
            [
                {
                    "instance_id": "id1",
                    "arm": "codex",
                    "status": "ok",
                    "elapsed_s": 1.0,
                    "patch_len": 0,
                    "agent_output": {
                        "msg": {
                            "type": "error",
                            "message": "You've hit your usage limit.",
                        }
                    },
                }
            ],
        )

        with (
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch("benchmark.online.client._post", autospec=True) as mock_post,
        ):
            ret = flush_pending_uploads(tmp_path)

        assert ret == 0
        mock_post.assert_not_called()
        assert ("id1", "codex", 0) in load_uploaded(tmp_path)


# ── upload (publish) ──────────────────────────────────────────────────────

from benchmark.online.publish import _dataset_key, _DatasetBuild


class TestDatasetKey:
    @pytest.mark.parametrize("full,key", [
        ("princeton-nlp/SWE-bench_Verified", "verified"),
        ("ScaleAI/SWE-bench_Pro", "pro"),
        ("princeton-nlp/SWE-bench_Lite", "lite"),
        ("something/else", ""),
    ])
    def test_extracts_key(self, full, key):
        assert _dataset_key(full) == key


class TestDatasetBuild:
    def test_init(self):
        ds = _DatasetBuild()
        assert ds.tasks == {}
        assert ds.arms == set()
        assert ds.results == {}
        assert ds.patches == {}


# ── __main__ ───────────────────────────────────────────────────────────────

from benchmark.__main__ import main


class TestUploadRunOnlineWarning:
    def test_warns_once_when_unconfigured(self):
        """_upload_run_online logs a warning when auth is missing."""
        import benchmark.runner as runner_mod

        runner_mod._upload_warned = False  # reset state

        with (
            patch("benchmark.runner.log", autospec=True) as mock_log,
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=False,
            ),
        ):
            from benchmark.runner import _upload_run_online

            _upload_run_online("run1", [], [], 100, "d")
            _upload_run_online("run2", [], [], 100, "d")  # second call

        # Warning should fire exactly once
        warning_calls = [
            c
            for c in mock_log.warning.call_args_list
            if "Online uploads disabled" in str(c)
        ]
        assert len(warning_calls) == 1
        runner_mod._upload_warned = False  # clean up


class TestMainCLI:
    def test_status_and_report_only_flags(self, tmp_path):
        with patch("sys.argv", ["benchmark", "--status", "--workspace", str(tmp_path)]):
            assert main() == 0
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "meta.json").write_text(json.dumps({"dataset": "d", "task_count": 0, "arms": []}))
        (run_dir / "results.jsonl").write_text("")
        (run_dir / "eval-summary.json").write_text("{}")
        with patch("sys.argv", ["benchmark", "--report-only", "--run-id", "r1", "--workspace", str(tmp_path)]):
            assert main() == 0

    def test_report_only_skips_distributed_mode_when_configured(self, tmp_path):
        """Regression: report-only should not contact the online assignment server."""
        with (
            patch(
                "sys.argv",
                [
                    "benchmark",
                    "--report-only",
                    "--run-id",
                    "r1",
                    "--workspace",
                    str(tmp_path),
                ],
            ),
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch("benchmark.online.client.whoami", autospec=True) as mock_whoami,
            patch(
                "benchmark.__main__._run_distributed", autospec=True
            ) as mock_distributed,
            patch(
                "benchmark.report.generate_report", autospec=True, return_value=0
            ) as mock_report,
        ):
            ret = main()

        assert ret == 0
        mock_report.assert_called_once_with(tmp_path, "r1")
        mock_whoami.assert_not_called()
        mock_distributed.assert_not_called()

    def test_evaluate_only_skips_distributed_mode_when_configured(self, tmp_path):
        """Regression: evaluate-only should stay on local artifacts too."""
        with (
            patch(
                "sys.argv",
                [
                    "benchmark",
                    "--evaluate-only",
                    "--run-id",
                    "r1",
                    "--workspace",
                    str(tmp_path),
                ],
            ),
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch("benchmark.online.client.whoami", autospec=True) as mock_whoami,
            patch(
                "benchmark.__main__._run_distributed", autospec=True
            ) as mock_distributed,
            patch(
                "benchmark.evaluate.evaluate_predictions", autospec=True
            ) as mock_evaluate,
            patch(
                "benchmark.report.generate_report", autospec=True, return_value=0
            ) as mock_report,
        ):
            ret = main()

        assert ret == 0
        mock_evaluate.assert_called_once_with(tmp_path, "r1")
        mock_report.assert_called_once_with(tmp_path, "r1")
        mock_whoami.assert_not_called()
        mock_distributed.assert_not_called()

    def test_upload_pending_no_auth(self, tmp_path):
        with (
            patch(
                "sys.argv",
                ["benchmark", "--upload-pending", "--workspace", str(tmp_path)],
            ),
            patch("benchmark.online.config._CLIENT_CREDENTIALS", None),  # noqa: autospec
            patch.dict("os.environ", {}, clear=True),
        ):
            ret = main()
        assert ret == 1


# ── distribute ─────────────────────────────────────────────────────────────

from benchmark.online.distribute import prioritize_assignments


class TestPrioritizeAssignments:
    @pytest.mark.parametrize("ids,results,backends,claims,expected_len,label", [
        ([], {}, ["claude"], set(), 0, "empty dataset"),
        (["id1"], {}, [], set(), 0, "no backends"),
        (["id1"], {"id1": {"claude": {"status": "ok"}}}, ["claude"], set(), 0, "all evaluated"),
    ])
    def test_returns_empty(self, ids, results, backends, claims, expected_len, label):
        """Empty dataset, no backends, or fully evaluated all return []."""
        assert prioritize_assignments(
            all_instance_ids=ids, results=results, backends=backends,
            active_claims=claims,
        ) == []

    def test_assigns_missing_backend_and_skips_covered(self):
        """Assigns only uncovered backends; covered ones are excluded."""
        result = prioritize_assignments(
            all_instance_ids=["id1"],
            results={"id1": {"claude": {}}},
            backends=["claude", "kodo:solo"],
            active_claims=set(),
        )
        assert result == [{"instance_id": "id1", "arm": "kodo:solo"}]

    def test_excludes_active_claims_and_respects_limit(self):
        result = prioritize_assignments(
            all_instance_ids=["id1", "id2", "id3"],
            results={}, backends=["claude"],
            active_claims={("id1", "claude")}, limit=1,
        )
        assert len(result) == 1
        assert result[0]["instance_id"] != "id1"

    def test_deterministic_and_multiple_backends(self):
        """Same input always produces same output; multiple backends all assigned."""
        kwargs = dict(all_instance_ids=["id2", "id1"], results={},
                      backends=["claude", "kodo"], active_claims=set())
        r1 = prioritize_assignments(**kwargs)
        r2 = prioritize_assignments(**kwargs)
        assert r1 == r2
        assert {a["arm"] for a in r1} == {"claude", "kodo"}

    def test_prefers_higher_coverage_tasks(self):
        """Tasks evaluated by more other backends are prioritized for comparison value."""
        result = prioritize_assignments(
            all_instance_ids=["id1", "id2", "id3"],
            results={"id1": {"claude": {}, "cursor": {}}, "id2": {"claude": {}}, "id3": {}},
            backends=["kodo:solo"], active_claims=set(),
        )
        assert [r["instance_id"] for r in result] == ["id1", "id2", "id3"]

    def test_arm_pressure_steers_away_from_busy_arms(self):
        """Lower pressure arms come first; missing arms treated as 0; empty dict is no-op."""
        result = prioritize_assignments(
            all_instance_ids=["id1"], results={},
            backends=["claude", "cursor"], active_claims=set(),
            arm_pressure={"cursor": 3},  # claude not mentioned = 0
        )
        assert result[0]["arm"] == "claude"
        assert result[1]["arm"] == "cursor"
        # Empty dict equivalent to no pressure
        no_p = prioritize_assignments(all_instance_ids=["id1"], results={},
                                       backends=["claude"], active_claims=set())
        with_empty = prioritize_assignments(all_instance_ids=["id1"], results={},
                                             backends=["claude"], active_claims=set(), arm_pressure={})
        assert no_p == with_empty

    def test_arm_pressure_secondary_to_coverage(self):
        """Coverage takes priority over arm pressure."""
        result = prioritize_assignments(
            all_instance_ids=["id1", "id2"],
            results={"id1": {"codex": {}}, "id2": {}},
            backends=["claude"], active_claims=set(),
            arm_pressure={"claude": 5},
        )
        assert result[0]["instance_id"] == "id1"  # higher coverage wins despite pressure


# ── client fetch_assignments ───────────────────────────────────────────────

import benchmark.online.config as online_config
from benchmark.online.client import _request, fetch_assignments, is_configured
import benchmark.online.db as online_db


class TestOnlineClientConfig:
    def test_env_vars_parsed_correctly(self):
        """ALLOWED_DATASETS parsed from CSV; SNAPSHOT_PREFIX strips slashes."""
        with patch.dict("os.environ", {"KODO_BENCH_ALLOWED_DATASETS": "verified, pro"}, clear=False):
            reloaded = importlib.reload(online_config)
            assert reloaded.ALLOWED_DATASETS == frozenset({"verified", "pro"})
        importlib.reload(online_config)
        with patch.dict("os.environ", {"KODO_BENCH_SNAPSHOT_PREFIX": "/frozen/h2h-verified-2026-03-16/"}, clear=False):
            reloaded = importlib.reload(online_config)
            assert reloaded.SNAPSHOT_PREFIX == "frozen/h2h-verified-2026-03-16"
        importlib.reload(online_config)

    def test_unconfigured_probe_does_not_freeze_empty_state(self):
        """Late-loaded env should still be observed after an early empty probe."""
        with patch("benchmark.online.config._CLIENT_CREDENTIALS", None):  # noqa: autospec
            with patch.dict("os.environ", {}, clear=True):
                assert is_configured() is False

            with patch.dict(
                "os.environ",
                {
                    "KODO_BENCH_URL": "https://bench.example",
                    "KODO_BENCH_TOKEN": "token-1",
                },
                clear=True,
            ):
                assert is_configured() is True

    def test_first_complete_credentials_are_cached(self):
        """After configuration is first observed, later env changes are ignored."""
        response = MagicMock()
        response.read.return_value = b"{}"

        with patch("benchmark.online.config._CLIENT_CREDENTIALS", None):  # noqa: autospec
            with patch.dict(
                "os.environ",
                {
                    "KODO_BENCH_URL": "https://bench-one.example",
                    "KODO_BENCH_TOKEN": "token-1",
                },
                clear=True,
            ):
                assert is_configured() is True

            with (
                patch.dict(
                    "os.environ",
                    {
                        "KODO_BENCH_URL": "https://bench-two.example",
                        "KODO_BENCH_TOKEN": "token-2",
                    },
                    clear=True,
                ),
                patch(
                    "urllib.request.urlopen", autospec=True, return_value=response
                ) as mock_urlopen,
            ):
                _request("GET", "/api/whoami")
                assert online_config._CLIENT_CREDENTIALS == (
                    "https://bench-one.example",
                    "token-1",
                )

        request = mock_urlopen.call_args.args[0]
        assert request.full_url == "https://bench-one.example/api/whoami"
        assert request.get_header("Authorization") == "Bearer token-1"


class TestFetchAssignments:
    def test_returns_none_when_unconfigured_raises_on_error(self):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=False):
            assert fetch_assignments(["claude"]) is None
        with (
            patch("benchmark.online.client.is_configured", autospec=True, return_value=True),
            patch("benchmark.online.client._post_json", autospec=True, side_effect=Exception("timeout")),
        ):
            with pytest.raises(Exception, match="timeout"):
                fetch_assignments(["claude"])

    def test_returns_assignments_or_empty(self):
        assignments = [{"instance_id": "id1", "arm": "claude", "dataset": "pro"}]
        with (
            patch("benchmark.online.client.is_configured", autospec=True, return_value=True),
            patch("benchmark.online.client._post_json", autospec=True, return_value={"assignments": assignments}),
        ):
            assert fetch_assignments(["claude"], datasets={"pro": ["id1"]}) == assignments

    def test_passes_datasets_to_server(self):
        with (
            patch("benchmark.online.client.is_configured", autospec=True, return_value=True),
            patch("benchmark.online.client._post_json", autospec=True, return_value={"assignments": []}) as mock_post,
        ):
            fetch_assignments(["claude"], datasets={"pro": ["id1"], "verified": ["id2"]})
        assert mock_post.call_args[0][1]["datasets"] == {"pro": ["id1"], "verified": ["id2"]}


class TestOnlineDb:
    def test_delete_patch_skips_when_storage_dep_missing(self):
        with (
            patch(
                "benchmark.online.db._bucket", autospec=True, side_effect=ImportError
            ),
            patch("benchmark.online.db.log", autospec=True) as mock_log,
        ):
            online_db.delete_patch("pro", "id1", "codex")

        mock_log.warning.assert_called_once()

    def test_head_to_head_index_keeps_only_overlap_and_two_arms(self):
        """Head-to-head data should expose only tasks both Kodo and Cursor evaluated."""
        index = {
            "tasks": [
                {"instance_id": "repo__1"},
                {"instance_id": "repo__2"},
                {"instance_id": "repo__3"},
            ],
            "arms": ["kodo", "cursor", "claude"],
            "results": {
                "repo__1": {
                    "kodo": {"eval_status": True, "resolved": True},
                    "cursor": {"eval_status": True, "resolved": False},
                    "claude": {"eval_status": True, "resolved": True},
                },
                "repo__2": {
                    "kodo": {"eval_status": True, "resolved": False},
                    "cursor": {"status": "ok"},
                },
                "repo__3": {
                    "cursor": {"eval_status": True, "resolved": True},
                    "claude": {"eval_status": True, "resolved": False},
                },
            },
            "meta": {
                "dataset": "verified",
                "last_updated": "2026-03-16T00:00:00+00:00",
            },
        }

        filtered = online_db.head_to_head_index(index)

        assert filtered["arms"] == ["kodo", "cursor"]
        assert [task["instance_id"] for task in filtered["tasks"]] == ["repo__1"]
        assert set(filtered["results"]["repo__1"]) == {"kodo", "cursor"}
        assert filtered["meta"]["view_mode"] == "head_to_head"
        assert filtered["meta"]["comparison"] == {
            "primary_arm": "kodo",
            "secondary_arm": "cursor",
        }

    def test_head_to_head_index_picks_kodo_arm_with_overlap(self):
        """When plain kodo has no overlap, choose the Kodo arm that does."""
        index = {
            "tasks": [
                {"instance_id": "repo__1"},
                {"instance_id": "repo__2"},
            ],
            "arms": ["kodo", "kodo:solo", "cursor"],
            "results": {
                "repo__1": {
                    "kodo:solo": {"eval_status": True, "resolved": False},
                    "cursor": {"eval_status": True, "resolved": True},
                },
                "repo__2": {
                    "kodo": {"eval_status": True, "resolved": True},
                },
            },
            "meta": {"dataset": "verified"},
        }

        filtered = online_db.head_to_head_index(index)

        assert filtered["arms"] == ["kodo:solo", "cursor"]
        assert [task["instance_id"] for task in filtered["tasks"]] == ["repo__1"]

    def test_snapshot_read_write_blob_paths(self):
        """Snapshot operations use correct blob paths under snapshots/ prefix."""
        snap = "h2h-verified-2026-03-16"
        expected_index_path = f"snapshots/{snap}/data/verified/index.json"
        expected_patch_path = f"snapshots/{snap}/patches/verified/django__django-11400/cursor.diff"

        # Read index
        blob = MagicMock()
        blob.exists.return_value = True
        blob.download_as_bytes.return_value = b'{"meta":{}}'
        bucket = MagicMock()
        bucket.blob.return_value = blob
        with patch("benchmark.online.db._bucket", autospec=True, return_value=bucket):
            online_db.get_snapshot_index_json("verified", snap)
        bucket.blob.assert_called_once_with(expected_index_path)

        # Read patch
        blob2 = MagicMock()
        blob2.exists.return_value = True
        blob2.download_as_text.return_value = "diff"
        bucket2 = MagicMock()
        bucket2.blob.return_value = blob2
        with patch("benchmark.online.db._bucket", autospec=True, return_value=bucket2):
            online_db.get_snapshot_patch("verified", "django__django-11400", "cursor", snap)
        bucket2.blob.assert_called_once_with(expected_patch_path)

        # Write index
        blob3 = MagicMock()
        bucket3 = MagicMock()
        bucket3.blob.return_value = blob3
        with patch("benchmark.online.db._bucket", autospec=True, return_value=bucket3):
            online_db.save_snapshot_index(snap, "verified", {"meta": {}, "tasks": []})
        bucket3.blob.assert_called_once_with(expected_index_path)


# ── runner with assignments ────────────────────────────────────────────────


class TestRunBenchmarkDistributed:
    def test_runs_only_assigned_pairs(self, tmp_path):
        """In distributed mode, only assigned (instance_id, arm) pairs run."""
        t1 = SWETask("id1", "o/r", "abc", "problem", [], [])
        t2 = SWETask("id2", "o/r", "abc", "problem", [], [])

        call_log = []

        def fake_run(task, arm, workspace, timeout, run_dir=None, **kwargs):
            call_log.append((task.instance_id, arm))
            return TaskResult(task.instance_id, arm, "patch", 1.0, "ok")

        with (
            patch("benchmark.runner._safe_run", autospec=True, side_effect=fake_run),
            patch("benchmark.runner._save_run_meta", autospec=True),
        ):
            run_benchmark(
                tasks=[t1, t2],
                arms=["claude", "kodo:solo"],
                workspace=tmp_path,
                run_id="test",
                timeout=100,
                assignments=[
                    {"instance_id": "id1", "arm": "claude"},
                    {"instance_id": "id2", "arm": "kodo:solo"},
                ],
            )

        # Should have run exactly the assigned pairs, not all 4 combinations.
        # Bare "claude" is normalized to "claude:opus" by run_benchmark.
        assert set(call_log) == {("id1", "claude:opus"), ("id2", "kodo:solo")}

    def test_none_assignments_runs_everything(self, tmp_path):
        """assignments=None means normal mode (all tasks x arms)."""
        t1 = SWETask("id1", "o/r", "abc", "problem", [], [])

        call_log = []

        def fake_run(task, arm, workspace, timeout, run_dir=None, **kwargs):
            call_log.append((task.instance_id, arm))
            return TaskResult(task.instance_id, arm, "patch", 1.0, "ok")

        with (
            patch("benchmark.runner._safe_run", autospec=True, side_effect=fake_run),
            patch("benchmark.runner._save_run_meta", autospec=True),
        ):
            run_benchmark(
                tasks=[t1],
                arms=["claude", "kodo:solo"],
                workspace=tmp_path,
                run_id="test",
                timeout=100,
                assignments=None,
            )

        assert set(call_log) == {("id1", "claude:opus"), ("id1", "kodo:solo")}

    def test_crash_recovery_in_distributed_mode(self, tmp_path):
        """Locally completed tasks within distributed mode are skipped."""
        t1 = SWETask("id1", "o/r", "abc", "problem", [], [])

        # Pre-populate local results (simulating crash recovery).
        # Use normalized arm name since run_benchmark normalizes arms.
        run_dir = tmp_path / "runs" / "test"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude:opus","status":"ok"}\n'
        )

        call_log = []

        def fake_run(task, arm, workspace, timeout, run_dir=None, **kwargs):
            call_log.append((task.instance_id, arm))
            return TaskResult(task.instance_id, arm, "patch", 1.0, "ok")

        with (
            patch("benchmark.runner._safe_run", autospec=True, side_effect=fake_run),
            patch("benchmark.runner._save_run_meta", autospec=True),
        ):
            run_benchmark(
                tasks=[t1],
                arms=["claude", "kodo:solo"],
                workspace=tmp_path,
                run_id="test",
                timeout=100,
                assignments=[
                    {"instance_id": "id1", "arm": "claude"},
                    {"instance_id": "id1", "arm": "kodo:solo"},
                ],
            )

        # claude:opus should be skipped (already in local results), kodo:solo should run
        assert call_log == [("id1", "kodo:solo")]


# ── CLI distribute ─────────────────────────────────────────────────────────


class TestMainDistribute:
    """Tests for distributed mode (default when server is configured)."""

    def _mock_load_tasks(self):
        """Mock load_tasks to return different tasks per dataset."""

        def _load(*, dataset, **kw):
            if "Pro" in dataset:
                return [SWETask("pro1", "o/r", "abc", "p", [], [])]
            elif "Verified" in dataset:
                return [SWETask("ver1", "o/r", "abc", "p", [], [])]
            return []

        return patch("benchmark.tasks.load_tasks", autospec=True, side_effect=_load)

    def test_distribute_no_assignments(self, tmp_path):
        """Returns 0 when server says all tasks are covered."""
        with (
            patch("sys.argv", ["benchmark", "--workspace", str(tmp_path)]),
            self._mock_load_tasks(),
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.fetch_assignments",
                autospec=True,
                return_value=[],
            ),
        ):
            ret = main()
        assert ret == 0

    def test_falls_back_to_local_when_not_configured(self, tmp_path):
        """Falls through to local mode when env vars are missing."""
        with (
            patch("sys.argv", ["benchmark", "--workspace", str(tmp_path)]),
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=False,
            ),
            patch(
                "benchmark.tasks.load_tasks",
                autospec=True,
                return_value=[SWETask("t1", "o/r", "abc", "p", [], [])],
            ),
            patch("benchmark.runner.run_benchmark", autospec=True),
            patch("benchmark.evaluate.evaluate_predictions", autospec=True),
            patch("benchmark.report.generate_report", autospec=True, return_value=0),
        ):
            ret = main()
        assert ret == 0

    def test_distribute_server_error_fails(self, tmp_path):
        """Fails hard when server returns an error."""
        with (
            patch("sys.argv", ["benchmark", "--workspace", str(tmp_path)]),
            self._mock_load_tasks(),
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.fetch_assignments",
                autospec=True,
                side_effect=Exception("HTTP Error 502: Bad Gateway"),
            ),
        ):
            ret = main()
        assert ret == 1

    def test_distribute_sends_only_requested_dataset(self, tmp_path):
        """Sends only the --dataset dataset to server (default: pro)."""
        with (
            patch("sys.argv", ["benchmark", "--workspace", str(tmp_path)]),
            self._mock_load_tasks(),
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.fetch_assignments",
                autospec=True,
                return_value=[],
            ) as mock_fetch,
            patch("benchmark._util.shutil.which", autospec=True, return_value=None),
        ):
            main()
        call_kwargs = mock_fetch.call_args[1]
        assert list(call_kwargs["datasets"].keys()) == ["pro"]
        assert "pro1" in call_kwargs["datasets"]["pro"]

    def test_distribute_respects_dataset_verified(self, tmp_path):
        """--dataset verified sends only verified tasks to server."""
        with (
            patch(
                "sys.argv",
                ["benchmark", "--workspace", str(tmp_path), "--dataset", "verified"],
            ),
            self._mock_load_tasks(),
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.fetch_assignments",
                autospec=True,
                return_value=[],
            ) as mock_fetch,
            patch("benchmark._util.shutil.which", autospec=True, return_value=None),
        ):
            main()
        call_kwargs = mock_fetch.call_args[1]
        assert list(call_kwargs["datasets"].keys()) == ["verified"]
        assert "ver1" in call_kwargs["datasets"]["verified"]

    def test_distribute_auto_detects_backends(self, tmp_path):
        """Without --arm auto-detects available backends."""
        with (
            patch("sys.argv", ["benchmark", "--workspace", str(tmp_path)]),
            self._mock_load_tasks(),
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.fetch_assignments",
                autospec=True,
                return_value=[],
            ) as mock_fetch,
            patch("benchmark._util.shutil.which", autospec=True, return_value=None),
        ):
            main()
        call_kwargs = mock_fetch.call_args[1]
        assert "kodo" in call_kwargs["backends"]

    def test_distribute_explicit_backends_override(self, tmp_path):
        """--backends overrides auto-detection."""
        with (
            patch(
                "sys.argv",
                [
                    "benchmark",
                    "--backends",
                    "claude,cursor",
                    "--workspace",
                    str(tmp_path),
                ],
            ),
            self._mock_load_tasks(),
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.fetch_assignments",
                autospec=True,
                return_value=[],
            ) as mock_fetch,
        ):
            main()
        call_kwargs = mock_fetch.call_args[1]
        assert set(call_kwargs["backends"]) == {"claude", "cursor"}

    def test_distribute_polls_until_empty(self, tmp_path):
        """Keeps polling until server returns no assignments."""
        batch1 = [{"instance_id": "pro1", "arm": "claude", "dataset": "pro"}]

        with (
            patch("sys.argv", ["benchmark", "--workspace", str(tmp_path)]),
            self._mock_load_tasks(),
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.fetch_assignments",
                autospec=True,
                side_effect=[batch1, []],
            ) as mock_fetch,
            patch("benchmark.runner.run_benchmark", autospec=True),
        ):
            ret = main()
        assert ret == 0
        assert mock_fetch.call_count == 2

    def test_distribute_server_error_after_batch_returns_ok(self, tmp_path):
        """If server errors after completing a batch, return 0 (work was done)."""
        batch1 = [{"instance_id": "pro1", "arm": "claude", "dataset": "pro"}]

        with (
            patch("sys.argv", ["benchmark", "--workspace", str(tmp_path)]),
            self._mock_load_tasks(),
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.fetch_assignments",
                autospec=True,
                side_effect=[batch1, Exception("connection reset")],
            ),
            patch("benchmark.runner.run_benchmark", autospec=True),
        ):
            ret = main()
        assert ret == 0


# ── progress view ──────────────────────────────────────────────────────────


# ── fetch_unevaluated ─────────────────────────────────────────────────────

from benchmark.online.client import fetch_unevaluated
from benchmark.online.cleanup_dummy_results import candidate_rows
from benchmark.online.mirror import (
    fetch_patch,
    flatten_index_rows,
    load_rows,
    main as mirror_main,
    mirror_dataset,
    public_base_url,
)


class TestFetchUnevaluated:
    def test_returns_none_when_unconfigured_or_error(self):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=False):
            assert fetch_unevaluated("pro") is None
        with (
            patch("benchmark.online.client.is_configured", autospec=True, return_value=True),
            patch("benchmark.online.client._get_json", autospec=True, side_effect=Exception("timeout")),
        ):
            assert fetch_unevaluated("pro") is None

    def test_returns_predictions_and_maps_dataset_key(self):
        preds = [{"instance_id": "id1", "arm": "claude", "patch": "diff"}]
        with (
            patch("benchmark.online.client.is_configured", autospec=True, return_value=True),
            patch("benchmark.online.client._get_json", autospec=True, return_value={"predictions": preds}) as mock_get,
        ):
            assert fetch_unevaluated("ScaleAI/SWE-bench_Pro") == preds
        assert mock_get.call_args[0][0] == "/api/unevaluated/pro"


class TestOnlineMirror:
    def test_public_base_url_prefers_explicit_then_env_then_default(self):
        with patch.dict(
            "os.environ", {"KODO_BENCH_URL": "https://env.example"}, clear=True
        ):
            assert public_base_url("https://arg.example") == "https://arg.example"
            assert public_base_url() == "https://env.example"

        with patch.dict("os.environ", {}, clear=True):
            assert public_base_url().startswith("https://kodo-bench-")

    def test_flatten_index_rows_keeps_one_row_per_instance_arm(self):
        index = {
            "meta": {"dataset": "verified"},
            "results": {
                "id2": {"cursor": {"status": "partial"}},
                "id1": {
                    "claude": {
                        "status": "ok",
                        "resolved": True,
                        "elapsed_s": 12.5,
                        "provenance": {"user": "alice", "host": "mbp"},
                    }
                },
            },
        }

        rows = flatten_index_rows(index)

        assert rows == [
            {
                "dataset": "verified",
                "instance_id": "id1",
                "arm": "claude",
                "status": "ok",
                "resolved": True,
                "eval_status": None,
                "elapsed_s": 12.5,
                "patch_len": None,
                "error": None,
                "run_id": None,
                "provenance_user": "alice",
                "provenance_host": "mbp",
            },
            {
                "dataset": "verified",
                "instance_id": "id2",
                "arm": "cursor",
                "status": "partial",
                "resolved": None,
                "eval_status": None,
                "elapsed_s": None,
                "patch_len": None,
                "error": None,
                "run_id": None,
            },
        ]

    def test_mirror_dataset_writes_index_rows_and_optional_patches(self, tmp_path):
        index = {
            "meta": {"dataset": "verified"},
            "results": {"id1": {"claude": {"status": "ok"}}},
        }

        with (
            patch(
                "benchmark.online.mirror.fetch_dataset_index",
                autospec=True,
                return_value=index,
            ),
            patch(
                "benchmark.online.mirror.fetch_dataset_patches",
                autospec=True,
                return_value={"id1/claude": "diff --git"},
            ),
        ):
            out = mirror_dataset("verified", out_dir=tmp_path, include_patches=True)

        assert out == tmp_path / "verified"
        assert json.loads((out / "index.json").read_text()) == index
        assert json.loads((out / "rows.json").read_text()) == [
            {
                "dataset": "verified",
                "instance_id": "id1",
                "arm": "claude",
                "status": "ok",
                "resolved": None,
                "eval_status": None,
                "elapsed_s": None,
                "patch_len": None,
                "error": None,
                "run_id": None,
            }
        ]
        assert json.loads((out / "patches.json").read_text()) == {
            "id1/claude": "diff --git"
        }

    def test_load_rows_accepts_dir_file_and_tilde(self, tmp_path):
        """Accepts directory, file path, and ~ expansion."""
        dataset_dir = tmp_path / "home" / ".kodo" / "benchmark" / "mirror" / "verified"
        dataset_dir.mkdir(parents=True)
        rows = [{"instance_id": "id1", "arm": "claude"}]
        (dataset_dir / "rows.json").write_text(json.dumps(rows))
        assert load_rows(dataset_dir) == rows
        assert load_rows(dataset_dir / "rows.json") == rows
        with patch.dict("os.environ", {"HOME": str(tmp_path / "home"), "USERPROFILE": str(tmp_path / "home")}, clear=False):
            assert load_rows("~/.kodo/benchmark/mirror/verified") == rows

    def test_fetch_patch_quotes_instance_id_and_arm(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"patch body"

        with patch(
            "urllib.request.urlopen", autospec=True, return_value=response
        ) as mock_urlopen:
            patch_text = fetch_patch("verified", "repo__name-1", "kodo:solo+opus")

        assert patch_text == "patch body"
        assert mock_urlopen.call_args.args[0].endswith(
            "/api/patch/verified/repo__name-1/kodo%3Asolo%2Bopus"
        )

    def test_mirror_main_uses_requested_output_dir(self, tmp_path):
        with patch(
            "benchmark.online.mirror.mirror_dataset", autospec=True
        ) as mock_mirror:
            ret = mirror_main(
                ["--dataset", "verified", "--out", str(tmp_path), "--patches"]
            )

        assert ret == 0
        mock_mirror.assert_called_once_with(
            "verified",
            out_dir=tmp_path,
            include_patches=True,
            base_url=None,
        )


class TestCleanupDummyResults:
    def test_candidate_rows_flags_suspicious_and_skips_clean(self):
        """candidate_rows flags empty_agent_output and kodo_worker_broken; skips clean rows."""
        rows = [
            {"instance_id": "id1", "arm": "codex:gpt-5.4", "status": "ok",
             "elapsed_s": 1.0, "patch_len": 0, "run_id": "run-a", "agent_output": {}},
            {"instance_id": "id2", "arm": "claude:opus", "status": "ok",
             "elapsed_s": 120.0, "patch_len": 42, "run_id": "run-a",
             "agent_output": {"status": "ok"}},  # clean - should be skipped
            {"instance_id": "id3", "arm": "kodo:solo", "status": "error",
             "elapsed_s": 627.6, "patch_len": 4624, "run_id": "run-a",
             "error": "[worker] error: unknown error",
             "agent_output": {"status": "error", "error": "bound to a different event loop"}},
        ]
        with patch("benchmark.online.cleanup_dummy_results.db.iter_task_results",
                    autospec=True, return_value=rows):
            result = candidate_rows("pro", run_id="run-a")
        assert len(result) == 2
        assert result[0]["reason"] == "empty_agent_output"
        assert result[1]["reason"] == "kodo_worker_broken"

    def test_main_filters_by_reason_and_elapsed(self, capsys):
        rows = [
            {
                "instance_id": "id1",
                "arm": "codex",
                "status": "error",
                "elapsed_s": 1.0,
                "patch_len": 0,
                "run_id": "run-a",
                "reason": "no_patch",
            },
            {
                "instance_id": "id2",
                "arm": "gemini",
                "status": "error",
                "elapsed_s": 25.0,
                "patch_len": 0,
                "run_id": "run-a",
                "reason": "no_patch",
            },
        ]

        with patch(
            "benchmark.online.cleanup_dummy_results.candidate_rows",
            autospec=True,
            return_value=rows,
        ):
            from benchmark.online.cleanup_dummy_results import main as cleanup_main

            ret = cleanup_main(
                [
                    "--dataset",
                    "pro",
                    "--reason",
                    "no_patch",
                    "--max-elapsed",
                    "10",
                ]
            )

        out = capsys.readouterr().out
        assert ret == 0
        assert "suspicious uploads: 1 rows" in out
        assert "no_patch: 1" in out

    def test_main_uses_batch_delete_for_zero_patch_rows(self):
        rows = [
            {
                "instance_id": "id1",
                "arm": "codex",
                "status": "error",
                "elapsed_s": 1.0,
                "patch_len": 0,
                "run_id": "run-a",
                "reason": "no_patch",
            }
        ]

        with (
            patch(
                "benchmark.online.cleanup_dummy_results.candidate_rows",
                autospec=True,
                return_value=rows,
            ),
            patch(
                "benchmark.online.cleanup_dummy_results.db.delete_task_results_batch",
                autospec=True,
            ) as mock_batch,
            patch(
                "benchmark.online.cleanup_dummy_results.db.delete_empty_result_docs",
                autospec=True,
                return_value=1,
            ) as mock_empty,
        ):
            from benchmark.online.cleanup_dummy_results import main as cleanup_main

            ret = cleanup_main(
                [
                    "--dataset",
                    "pro",
                    "--reason",
                    "no_patch",
                    "--apply",
                ]
            )

        assert ret == 0
        mock_batch.assert_called_once_with("pro", [("id1", "codex")])
        mock_empty.assert_called_once_with("pro")


# ── evaluate_pending ──────────────────────────────────────────────────────


from benchmark.evaluate_pending import evaluate_pending


class TestEvaluatePending:
    @pytest.mark.parametrize("configured,docker_ok,fetch_return,expected", [
        (False, True, [], 1),    # unconfigured
        (True, False, [], 1),    # docker unavailable
        (True, True, [], 0),     # nothing pending
        (True, True, None, 1),   # fetch failure
    ])
    def test_precondition_failures(self, tmp_path, configured, docker_ok, fetch_return, expected):
        """Returns 1 if unconfigured, docker down, or fetch fails; 0 if nothing pending."""
        with (
            patch("benchmark.online.client.is_configured", autospec=True, return_value=configured),
            patch("benchmark._util.ensure_docker_running", autospec=True, return_value=docker_ok),
            patch("benchmark.online.client.fetch_unevaluated", autospec=True, return_value=fetch_return),
        ):
            assert evaluate_pending(tmp_path, dataset_arg="verified") == expected

    def test_writes_predictions_and_runs_eval(self, tmp_path):
        """Full flow: fetch → write → combined eval → upload per arm."""
        predictions = [
            {"instance_id": "django__django-12345", "arm": "claude", "patch": "diff1"},
            {"instance_id": "django__django-67890", "arm": "claude", "patch": "diff2"},
            {
                "instance_id": "django__django-12345",
                "arm": "kodo:solo",
                "patch": "diff3",
            },
        ]

        eval_results = {
            "claude": {
                "resolved": ["django__django-12345"],
                "failed": ["django__django-67890"],
                "error": [],
                "resolve_rate": 0.5,
            },
            "kodo:solo": {
                "resolved": ["django__django-12345"],
                "failed": [],
                "error": [],
                "resolve_rate": 1.0,
            },
        }

        def fake_eval_combined(run_dir, arm_names, run_id, dataset, on_instance=None):
            """Simulate combined eval: call on_instance for each (instance, arm)."""
            results = {}
            for arm in arm_names:
                result = eval_results.get(
                    arm,
                    {"resolved": [], "failed": [], "error": [], "resolve_rate": 0.0},
                )
                if on_instance:
                    for iid in result.get("resolved", []):
                        on_instance(iid, arm, True)
                    for iid in result.get("failed", []):
                        on_instance(iid, arm, False)
                results[arm] = result
            return results

        upload_calls = []

        def fake_upload(
            dataset, arm, *, resolved=None, failed=None, error=None, seed=0
        ):
            upload_calls.append(
                {
                    "dataset": dataset,
                    "arm": arm,
                    "resolved": resolved,
                    "failed": failed,
                    "seed": seed,
                }
            )

        with (
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark._util.ensure_docker_running",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.fetch_unevaluated",
                autospec=True,
                return_value=predictions,
            ),
            patch(
                "benchmark.evaluate.evaluate_arms_combined",
                autospec=True,
                side_effect=fake_eval_combined,
            ) as mock_eval,
            patch(
                "benchmark.online.client.upload_eval_results",
                autospec=True,
                side_effect=fake_upload,
            ),
        ):
            result = evaluate_pending(tmp_path, dataset_arg="verified")

        assert result == 0

        # Check predictions files were written
        run_dirs = list((tmp_path / "runs").iterdir())
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]
        assert run_dir.name.startswith("eval_")

        # Check meta.json
        meta = json.loads((run_dir / "meta.json").read_text())
        assert "Verified" in meta["dataset"]
        assert meta["task_count"] == 3

        # Check prediction files exist with correct content
        claude_preds = (run_dir / "predictions-claude.jsonl").read_text().splitlines()
        assert len(claude_preds) == 2
        kodo_preds = (run_dir / "predictions-kodo_solo.jsonl").read_text().splitlines()
        assert len(kodo_preds) == 1

        # Combined eval called once with all arms
        assert mock_eval.call_count == 1

        # Check uploads: 3 per-instance streaming + 2 bulk (one per arm)
        assert len(upload_calls) == 5
        # Verify all instances were uploaded (via streaming)
        streamed = {
            (c["arm"], c["resolved"][0] if c["resolved"] else c["failed"][0])
            for c in upload_calls
            if len(c.get("resolved", []) + c.get("failed", [])) == 1
        }
        assert ("claude", "django__django-12345") in streamed
        assert ("claude", "django__django-67890") in streamed
        assert ("kodo:solo", "django__django-12345") in streamed
        # Verify bulk uploads happened (one per arm with full results)
        bulk = [
            c
            for c in upload_calls
            if len(c.get("resolved", []) + c.get("failed", [])) > 1
        ]
        assert (
            len(bulk) == 1
        )  # only claude has 2 instances; kodo:solo has 1 (same as streaming)

    def test_returns_0_even_when_eval_returns_empty(self, tmp_path):
        """Returns 0 when eval produces no results (empty arm)."""
        predictions = [{"instance_id": "id1", "arm": "claude", "patch": "diff"}]

        def fake_eval_combined(run_dir, arm_names, run_id, dataset, on_instance=None):
            return {
                arm: {"resolved": [], "failed": [], "error": [], "resolve_rate": 0.0}
                for arm in arm_names
            }

        with (
            patch(
                "benchmark.online.client.is_configured",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark._util.ensure_docker_running",
                autospec=True,
                return_value=True,
            ),
            patch(
                "benchmark.online.client.fetch_unevaluated",
                autospec=True,
                return_value=predictions,
            ),
            patch(
                "benchmark.evaluate.evaluate_arms_combined",
                autospec=True,
                side_effect=fake_eval_combined,
            ),
        ):
            result = evaluate_pending(tmp_path, dataset_arg="verified")

        assert result == 0


