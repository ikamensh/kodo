"""Unit tests for the benchmark package — pure logic, mocked I/O, ~2s target."""

from __future__ import annotations

import json
import signal
import subprocess
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
    def test_clean(self):
        assert docker_safe("claude") == "claude"

    def test_colon(self):
        assert docker_safe("kodo:solo") == "kodo_solo"

    def test_special_chars(self):
        assert docker_safe("a/b@c") == "a_b_c"

    def test_dots_underscores_dashes_preserved(self):
        assert docker_safe("a.b-c_d") == "a.b-c_d"


class TestLoadJson:
    def test_missing_file(self, tmp_path):
        assert load_json(tmp_path / "nope.json") == {}

    def test_valid(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"a": 1}')
        assert load_json(f) == {"a": 1}

    def test_corrupt(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{not json")
        assert load_json(f) == {}


class TestLoadJsonl:
    def test_missing_file(self, tmp_path):
        assert load_jsonl(tmp_path / "nope.jsonl") == []

    def test_valid_lines(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"a":1}\n{"b":2}\n')
        assert load_jsonl(f) == [{"a": 1}, {"b": 2}]

    def test_blank_lines_skipped(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"a":1}\n\n{"b":2}\n')
        assert load_jsonl(f) == [{"a": 1}, {"b": 2}]

    def test_bad_lines_skipped(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"a":1}\nnot json\n{"b":2}\n')
        result = load_jsonl(f)
        assert result == [{"a": 1}, {"b": 2}]


class TestIterJsonl:
    def test_missing_file(self, tmp_path):
        assert list(iter_jsonl(tmp_path / "nope.jsonl")) == []

    def test_streams_lines(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"x":1}\n{"x":2}\n')
        assert list(iter_jsonl(f)) == [{"x": 1}, {"x": 2}]

    def test_skips_bad(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"x":1}\nbad\n{"x":3}\n')
        assert list(iter_jsonl(f)) == [{"x": 1}, {"x": 3}]


class TestSetupLogging:
    def test_does_not_raise(self):
        setup_logging(verbose=False)
        setup_logging(verbose=True)


class TestDetectBackends:
    def test_always_includes_kodo(self):
        with patch("benchmark._util.shutil.which", autospec=True, return_value=None):
            backends = detect_backends()
        assert "kodo" in backends

    def test_detects_claude(self):
        def fake_which(name):
            return "/usr/bin/claude" if name == "claude" else None
        with patch("benchmark._util.shutil.which", autospec=True, side_effect=fake_which):
            backends = detect_backends()
        assert "kodo" in backends
        assert "claude" in backends
        assert "cursor" not in backends

    def test_detects_all(self):
        with patch("benchmark._util.shutil.which", autospec=True, return_value="/usr/bin/x"):
            backends = detect_backends()
        assert set(backends) == {"kodo", "claude", "cursor", "codex", "gemini"}

    def test_nothing_on_path(self):
        with patch("benchmark._util.shutil.which", autospec=True, return_value=None):
            backends = detect_backends()
        assert backends == ["kodo"]


class TestDockerIsReady:
    def test_ready_when_returncode_zero(self):
        with patch("subprocess.run", autospec=True) as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert _docker_is_ready() is True

    def test_not_ready_when_returncode_nonzero(self):
        with patch("subprocess.run", autospec=True) as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert _docker_is_ready() is False

    def test_not_ready_when_not_installed(self):
        with patch("subprocess.run", autospec=True, side_effect=FileNotFoundError):
            assert _docker_is_ready() is False

    def test_not_ready_on_timeout(self):
        with patch("subprocess.run", autospec=True, side_effect=subprocess.TimeoutExpired("docker", 10)):
            assert _docker_is_ready() is False


class TestStartDockerMacos:
    def test_orbstack_preferred(self):
        with patch("benchmark._util.shutil.which", autospec=True, return_value="/opt/homebrew/bin/orbctl"), \
             patch("subprocess.run", autospec=True) as mock_run:
            assert _start_docker_macos() is True
            mock_run.assert_called_once()
            assert mock_run.call_args[0][0] == ["orbctl", "start"]

    def test_docker_desktop_fallback(self):
        with patch("benchmark._util.shutil.which", autospec=True, return_value=None), \
             patch("subprocess.run", autospec=True) as mock_run:
            assert _start_docker_macos() is True
            mock_run.assert_called_once()
            assert mock_run.call_args[0][0] == ["open", "-a", "Docker"]

    def test_nothing_works(self):
        with patch("benchmark._util.shutil.which", autospec=True, return_value=None), \
             patch("subprocess.run", autospec=True, side_effect=FileNotFoundError):
            assert _start_docker_macos() is False


class TestEnsureDockerRunning:
    def test_already_running(self):
        with patch("benchmark._util._docker_is_ready", autospec=True, return_value=True):
            assert ensure_docker_running() is True

    def test_starts_and_becomes_ready(self):
        calls = {"n": 0}
        def ready_after_one():
            calls["n"] += 1
            return calls["n"] > 1

        with patch("benchmark._util._docker_is_ready", autospec=True, side_effect=ready_after_one), \
             patch("benchmark._util._start_docker_macos", autospec=True, return_value=True), \
             patch("benchmark._util.platform.system", autospec=True, return_value="Darwin"), \
             patch("benchmark._util.time.sleep", autospec=True):
            assert ensure_docker_running(timeout=10) is True

    def test_returns_false_on_linux(self):
        with patch("benchmark._util._docker_is_ready", autospec=True, return_value=False), \
             patch("benchmark._util.platform.system", autospec=True, return_value="Linux"):
            assert ensure_docker_running() is False


# ── tasks ──────────────────────────────────────────────────────────────────

from benchmark.tasks import SWETask, _parse_list_field, _row_to_task


class TestParseListField:
    def test_list_passthrough(self):
        assert _parse_list_field(["a", "b"]) == ["a", "b"]

    def test_json_string(self):
        assert _parse_list_field('["a", "b"]') == ["a", "b"]

    def test_python_repr(self):
        assert _parse_list_field("['a', 'b']") == ["a", "b"]

    def test_empty_string(self):
        assert _parse_list_field("[]") == []

    def test_garbage(self):
        assert _parse_list_field("not a list") == []


class TestRowToTask:
    def test_basic(self):
        row = {
            "instance_id": "repo__name-123",
            "repo": "owner/repo",
            "base_commit": "abc123",
            "problem_statement": "Fix the bug",
            "fail_to_pass": '["test_foo"]',
            "pass_to_pass": '["test_bar"]',
            "version": "1.0",
            "repo_language": "python",
        }
        task = _row_to_task(row)
        assert task.instance_id == "repo__name-123"
        assert task.repo == "owner/repo"
        assert task.fail_to_pass == ["test_foo"]
        assert task.pass_to_pass == ["test_bar"]

    def test_uppercase_fields(self):
        row = {
            "instance_id": "id1",
            "repo": "o/r",
            "base_commit": "abc",
            "problem_statement": "desc",
            "FAIL_TO_PASS": '["t1"]',
            "PASS_TO_PASS": '["t2"]',
        }
        task = _row_to_task(row)
        assert task.fail_to_pass == ["t1"]

    def test_missing_optional_fields(self):
        row = {
            "instance_id": "id1",
            "repo": "o/r",
            "base_commit": "abc",
            "problem_statement": "desc",
        }
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
    def test_simple(self):
        assert parse_arm("claude") == ("claude", None)

    def test_with_team(self):
        assert parse_arm("kodo:solo") == ("kodo", "solo")

    def test_with_complex_team(self):
        assert parse_arm("kodo:solo+opus") == ("kodo", "solo+opus")




class TestTimeoutForArm:
    def test_kodo(self):
        assert _timeout_for_arm("kodo", 100, 999) == 999

    def test_kodo_team(self):
        assert _timeout_for_arm("kodo:solo", 100, 999) == 999

    def test_claude(self):
        assert _timeout_for_arm("claude", 100, 999) == 100

    def test_cursor(self):
        assert _timeout_for_arm("cursor", 100, 999) == 100


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
    def test_valid_json(self):
        assert _parse_json_output('{"a": 1}') == {"a": 1}

    def test_json_on_last_line(self):
        assert _parse_json_output('some log\n{"a": 1}') == {"a": 1}

    def test_json_buried_in_output(self):
        assert _parse_json_output('log1\n{"a": 1}\nlog2') == {"a": 1}

    def test_empty(self):
        assert _parse_json_output("") == {}

    def test_no_json(self):
        assert _parse_json_output("just text\nno json here") == {}

    def test_prefers_full_parse(self):
        # If full stdout is valid JSON, use that
        data = json.dumps({"full": True})
        assert _parse_json_output(data) == {"full": True}


class TestAppendResult:
    def test_writes_jsonl(self, tmp_path):
        result = TaskResult("id1", "claude", "patch", 10.0, "ok")
        _append_result(tmp_path, result)
        lines = (tmp_path / "results.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["instance_id"] == "id1"
        assert entry["status"] == "ok"

    def test_appends(self, tmp_path):
        _append_result(tmp_path, TaskResult("id1", "a", "", 1.0, "ok"))
        _append_result(tmp_path, TaskResult("id2", "b", "", 2.0, "error", error="fail"))
        lines = (tmp_path / "results.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2

    def test_stores_seed(self, tmp_path):
        result = TaskResult("id1", "claude", "patch", 10.0, "ok")
        _append_result(tmp_path, result, seed=2)
        entry = json.loads((tmp_path / "results.jsonl").read_text().strip())
        assert entry["seed"] == 2

    def test_default_seed_is_zero(self, tmp_path):
        result = TaskResult("id1", "claude", "patch", 10.0, "ok")
        _append_result(tmp_path, result)
        entry = json.loads((tmp_path / "results.jsonl").read_text().strip())
        assert entry["seed"] == 0

    def test_calls_fsync(self, tmp_path):
        """Crash safety: writes are flushed and fsynced."""
        result = TaskResult("id1", "claude", "patch", 10.0, "ok")
        with patch("benchmark.runner.os.fsync", autospec=True) as mock_fsync:
            _append_result(tmp_path, result)
            assert mock_fsync.called


class TestAppendPrediction:
    def test_writes_prediction(self, tmp_path):
        result = TaskResult("id1", "claude", "diff --git ...", 10.0, "ok")
        _append_prediction(tmp_path, result)
        pred_file = tmp_path / "predictions-claude.jsonl"
        assert pred_file.exists()
        entry = json.loads(pred_file.read_text().strip())
        assert entry["model_patch"] == "diff --git ..."

    def test_arm_sanitized_in_filename(self, tmp_path):
        result = TaskResult("id1", "kodo:solo", "patch", 10.0, "ok")
        _append_prediction(tmp_path, result)
        # Should use sanitized arm name in filename
        files = list(tmp_path.glob("predictions-*.jsonl"))
        assert len(files) == 1
        assert ":" not in files[0].name

    def test_preserves_original_arm(self, tmp_path):
        """Regression: predictions must store original arm for lossless round-trips."""
        result = TaskResult("id1", "kodo:solo_opus", "patch", 10.0, "ok")
        _append_prediction(tmp_path, result)
        files = list(tmp_path.glob("predictions-*.jsonl"))
        entry = json.loads(files[0].read_text().strip())
        assert entry["arm"] == "kodo:solo_opus"
        # model_name_or_path is sanitized (lossy), arm is not
        assert entry["model_name_or_path"] == "kodo_solo_opus"


class TestLoadCompleted:
    def test_empty_dir(self, tmp_path):
        assert _load_completed(tmp_path) == set()

    def test_loads_pairs(self, tmp_path):
        results = tmp_path / "results.jsonl"
        results.write_text(
            '{"instance_id":"id1","arm":"claude"}\n'
            '{"instance_id":"id2","arm":"kodo"}\n'
        )
        assert _load_completed(tmp_path) == {("id1", "claude"), ("id2", "kodo")}

    def test_skips_bad_lines(self, tmp_path):
        results = tmp_path / "results.jsonl"
        results.write_text(
            '{"instance_id":"id1","arm":"claude"}\n'
            'bad json\n'
            '{"instance_id":"id2","arm":"kodo"}\n'
        )
        assert _load_completed(tmp_path) == {("id1", "claude"), ("id2", "kodo")}


class TestSafeRun:
    def test_catches_exceptions(self):
        task = SWETask("id1", "owner/repo", "abc", "problem", [], [])
        with patch("benchmark.runner._run_single_task", autospec=True, side_effect=RuntimeError("boom")):
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
    def test_odd(self):
        assert _median([3, 1, 2]) == 2

    def test_even(self):
        assert _median([1, 2, 3, 4]) == 2.5

    def test_single(self):
        assert _median([5]) == 5


class TestPercentile:
    def test_p90(self):
        vals = list(range(1, 101))  # 1..100
        assert _percentile(vals, 90) == 91

    def test_p50(self):
        assert _percentile([1, 2, 3, 4, 5], 50) == 3


class TestDatasetShort:
    def test_verified(self):
        assert _dataset_short("princeton-nlp/SWE-bench_Verified") == "Verified"

    def test_pro(self):
        assert _dataset_short("ScaleAI/SWE-bench_Pro") == "Pro"

    def test_lite(self):
        assert _dataset_short("princeton-nlp/SWE-bench_Lite") == "Lite"

    def test_unknown(self):
        assert _dataset_short("org/Custom_Dataset") == "Custom_Dataset"


class TestEvalKey:
    def test_clean(self):
        assert _eval_key("claude") == "claude"

    def test_colon(self):
        assert _eval_key("kodo:solo") == "kodo_solo"


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
    def test_no_runs_dir(self, tmp_path):
        assert print_status(tmp_path) == 0

    def test_empty_runs_dir(self, tmp_path):
        (tmp_path / "runs").mkdir()
        assert print_status(tmp_path) == 0

    def test_single_run(self, tmp_path):
        run_dir = tmp_path / "runs" / "run1"
        run_dir.mkdir(parents=True)
        meta = {"dataset": "princeton-nlp/SWE-bench_Verified", "task_count": 5, "arms": ["claude"]}
        (run_dir / "meta.json").write_text(json.dumps(meta))
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"ok"}\n'
        )
        assert print_status(tmp_path) == 0


# ── runner (continued) ─────────────────────────────────────────────────────

from benchmark.runner import (
    _clean_env,
    _load_global_completed,
    _run_subprocess,
    _save_run_meta,
    run_benchmark,
)


class TestCleanEnv:
    def test_removes_claudecode(self):
        with patch.dict("os.environ", {"CLAUDECODE": "1", "PATH": "/usr/bin"}, clear=True):
            env = _clean_env()
            assert "CLAUDECODE" not in env
            assert "PATH" in env

    def test_removes_api_key_by_default(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-xxx"}, clear=True):
            env = _clean_env()
            assert "ANTHROPIC_API_KEY" not in env

    def test_keeps_api_key_when_requested(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-xxx"}, clear=True):
            env = _clean_env(keep_api_key=True)
            assert env["ANTHROPIC_API_KEY"] == "sk-xxx"


class TestRunSubprocess:
    def test_success(self):
        output, status, error, stdout, stderr = _run_subprocess(
            ["echo", '{"ok": true}'], cwd=None, timeout=5,
        )
        assert status == "ok"
        assert output == {"ok": True}
        assert error == ""

    def test_timeout(self):
        output, status, error, stdout, stderr = _run_subprocess(
            ["sleep", "10"], cwd=None, timeout=0.05,
        )
        assert status == "timeout"
        assert "Timed out" in error

    def test_nonzero_exit(self):
        output, status, error, stdout, stderr = _run_subprocess(
            ["false"], cwd=None, timeout=5,
        )
        assert status == "error"

    def test_exit_code_2_is_partial(self):
        # exit 2 = partial success (kodo verification unsatisfied)
        output, status, error, stdout, stderr = _run_subprocess(
            ["sh", "-c", "exit 2"], cwd=None, timeout=5,
        )
        assert status == "partial"


class TestSaveRunMeta:
    def test_creates_meta_file(self, tmp_path):
        tasks = [SWETask("id1", "o/r", "abc", "desc", [], [])]
        _save_run_meta(tmp_path, tasks, ["claude"], 7200, dataset="test")
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["task_count"] == 1
        assert meta["arms"] == ["claude"]
        assert meta["instance_ids"] == ["id1"]

    def test_does_not_overwrite(self, tmp_path):
        (tmp_path / "meta.json").write_text('{"existing": true}')
        _save_run_meta(tmp_path, [], [], 100)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta == {"existing": True}


class TestLoadGlobalCompleted:
    def test_no_runs_dir(self, tmp_path):
        assert _load_global_completed(tmp_path) == set()

    def test_single_run(self, tmp_path):
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"ok"}\n'
        )
        assert _load_global_completed(tmp_path) == {("id1", "claude")}

    def test_multiple_runs(self, tmp_path):
        for name, iid in [("r1", "id1"), ("r2", "id2")]:
            d = tmp_path / "runs" / name
            d.mkdir(parents=True)
            (d / "results.jsonl").write_text(
                f'{{"instance_id":"{iid}","arm":"claude","status":"ok"}}\n'
            )
        completed = _load_global_completed(tmp_path)
        assert completed == {("id1", "claude"), ("id2", "claude")}

    def test_skips_errors(self, tmp_path):
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"error"}\n'
        )
        assert _load_global_completed(tmp_path) == set()

    def test_skips_timeouts(self, tmp_path):
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"timeout"}\n'
        )
        assert _load_global_completed(tmp_path) == set()

    def test_keeps_partial(self, tmp_path):
        """partial (kodo exit 2) has a valid patch — should count as completed."""
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"kodo:solo","status":"partial"}\n'
        )
        assert _load_global_completed(tmp_path) == {("id1", "kodo:solo")}

    def test_exclude_run_dir(self, tmp_path):
        r1 = tmp_path / "runs" / "r1"
        r2 = tmp_path / "runs" / "r2"
        r1.mkdir(parents=True)
        r2.mkdir(parents=True)
        (r1 / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"ok"}\n'
        )
        (r2 / "results.jsonl").write_text(
            '{"instance_id":"id2","arm":"claude","status":"ok"}\n'
        )
        # Exclude r2 — should only see r1
        completed = _load_global_completed(tmp_path, exclude_run_dir=r2)
        assert completed == {("id1", "claude")}

    def test_skips_bad_lines(self, tmp_path):
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"ok"}\n'
            'bad json\n'
            '{"instance_id":"id2","arm":"kodo","status":"ok"}\n'
        )
        assert _load_global_completed(tmp_path) == {("id1", "claude"), ("id2", "kodo")}

    def test_no_data_duplication(self, tmp_path):
        """Key behavioral test: prior run results are NOT copied into current run."""
        prior = tmp_path / "runs" / "prior"
        prior.mkdir(parents=True)
        (prior / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"ok"}\n'
        )

        cur = tmp_path / "runs" / "current"
        cur.mkdir(parents=True)

        completed = _load_global_completed(tmp_path, exclude_run_dir=cur)
        assert ("id1", "claude") in completed
        # Current run dir should NOT have any copied files
        assert not (cur / "results.jsonl").exists()

    def test_seed_filtering(self, tmp_path):
        """Different seeds allow the same task to be re-run."""
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"ok","seed":0}\n'
            '{"instance_id":"id2","arm":"claude","status":"ok","seed":1}\n'
        )
        # seed=0 should only see id1
        assert _load_global_completed(tmp_path, seed=0) == {("id1", "claude")}
        # seed=1 should only see id2
        assert _load_global_completed(tmp_path, seed=1) == {("id2", "claude")}
        # seed=2 should see nothing
        assert _load_global_completed(tmp_path, seed=2) == set()

    def test_legacy_results_default_to_seed_zero(self, tmp_path):
        """Results without a seed field are treated as seed=0."""
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"ok"}\n'
        )
        assert _load_global_completed(tmp_path, seed=0) == {("id1", "claude")}
        assert _load_global_completed(tmp_path, seed=1) == set()


class TestRunBenchmark:
    def test_sequential_with_mock(self, tmp_path):
        task = SWETask("id1", "o/r", "abc", "problem", [], [])
        fake_result = TaskResult("id1", "claude", "patch", 5.0, "ok")

        with patch("benchmark.runner._safe_run", autospec=True, return_value=fake_result), \
             patch("benchmark.runner._save_run_meta", autospec=True):
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

        # Create a prior run with this task completed
        prior = tmp_path / "runs" / "prior"
        prior.mkdir(parents=True)
        (prior / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"ok"}\n'
        )

        with patch("benchmark.runner._safe_run", autospec=True) as mock_run, \
             patch("benchmark.runner._save_run_meta", autospec=True):
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
    _count_pro_progress,
    _docker_safe as eval_docker_safe,
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
    def test_timeout_kills_process_group(self):
        """Timeout should reap the evaluator group so later arms can continue."""
        proc = MagicMock()
        proc.pid = 321
        proc.poll.return_value = None
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(["python"], 10),
            None,
        ]

        with patch("benchmark.evaluate.subprocess.Popen", autospec=True, return_value=proc), \
             patch("benchmark.evaluate.os.killpg", autospec=True) as mock_killpg:
            with pytest.raises(subprocess.TimeoutExpired):
                _run_eval_subprocess(["python"], timeout=10)

        mock_killpg.assert_called_once_with(321, signal.SIGKILL)

    def test_nonzero_exit_raises(self):
        """Non-zero exit should still surface as a CalledProcessError."""
        proc = MagicMock()
        proc.wait.return_value = 7

        with patch("benchmark.evaluate.subprocess.Popen", autospec=True, return_value=proc):
            with pytest.raises(subprocess.CalledProcessError):
                _run_eval_subprocess(["python"], timeout=10)


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


class TestParseProResults:
    def test_missing_file(self, tmp_path):
        result = _parse_pro_results(tmp_path)
        assert result["resolved"] == []
        assert result["resolve_rate"] == 0.0

    def test_with_results(self, tmp_path):
        data = {"id1": True, "id2": False, "id3": True}
        (tmp_path / "eval_results.json").write_text(json.dumps(data))
        result = _parse_pro_results(tmp_path)
        assert sorted(result["resolved"]) == ["id1", "id3"]
        assert result["failed"] == ["id2"]
        assert result["resolve_rate"] == pytest.approx(2 / 3)

    def test_empty_results(self, tmp_path):
        (tmp_path / "eval_results.json").write_text("{}")
        result = _parse_pro_results(tmp_path)
        assert result["resolve_rate"] == 0.0


class TestParseStandardResults:
    def _eval_dir(self, tmp_path):
        """Create a clean eval subdirectory to avoid pytest artifacts."""
        d = tmp_path / "eval_results"
        d.mkdir()
        return d

    def test_resolved(self, tmp_path):
        eval_dir = self._eval_dir(tmp_path)
        inst_dir = eval_dir / "id1"
        inst_dir.mkdir()
        report = {"id1": {"resolved": True}}
        (inst_dir / "report.json").write_text(json.dumps(report))

        result = _parse_standard_results(eval_dir)
        assert result["resolved"] == ["id1"]
        assert result["resolve_rate"] == 1.0

    def test_failed(self, tmp_path):
        eval_dir = self._eval_dir(tmp_path)
        inst_dir = eval_dir / "id1"
        inst_dir.mkdir()
        report = {"id1": {"resolved": False}}
        (inst_dir / "report.json").write_text(json.dumps(report))

        result = _parse_standard_results(eval_dir)
        assert result["failed"] == ["id1"]

    def test_missing_report(self, tmp_path):
        eval_dir = self._eval_dir(tmp_path)
        (eval_dir / "id1").mkdir()
        result = _parse_standard_results(eval_dir)
        assert result["error"] == ["id1"]

    def test_corrupt_report(self, tmp_path):
        eval_dir = self._eval_dir(tmp_path)
        inst_dir = eval_dir / "id1"
        inst_dir.mkdir()
        (inst_dir / "report.json").write_text("not json")
        result = _parse_standard_results(eval_dir)
        assert result["error"] == ["id1"]

    def test_mixed(self, tmp_path):
        eval_dir = self._eval_dir(tmp_path)
        for name, data in [("id1", {"id1": {"resolved": True}}),
                           ("id2", {"id2": {"resolved": False}})]:
            d = eval_dir / name
            d.mkdir()
            (d / "report.json").write_text(json.dumps(data))
        (eval_dir / "id3").mkdir()  # no report

        result = _parse_standard_results(eval_dir)
        assert result["resolved"] == ["id1"]
        assert result["failed"] == ["id2"]
        assert result["error"] == ["id3"]
        assert result["resolve_rate"] == pytest.approx(1 / 3)


class TestCollectEvalResults:
    def test_pro_mode(self, tmp_path):
        eval_dir = tmp_path / "eval" / "claude"
        eval_dir.mkdir(parents=True)
        data = {"id1": True, "id2": False}
        (eval_dir / "eval_results.json").write_text(json.dumps(data))

        _collect_eval_results(tmp_path, is_pro=True)

        summary = json.loads((tmp_path / "eval-summary.json").read_text())
        assert "claude" in summary
        assert summary["claude"]["resolved"] == ["id1"]

    def test_creates_eval_dir_if_missing(self, tmp_path):
        _collect_eval_results(tmp_path, is_pro=True)
        assert (tmp_path / "eval").is_dir()
        summary = json.loads((tmp_path / "eval-summary.json").read_text())
        assert summary == {}


class TestEvaluatePredictions:
    def test_no_predictions(self, tmp_path):
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        # No meta, no predictions — should not crash
        with patch("benchmark._util.ensure_docker_running", autospec=True, return_value=True):
            evaluate_predictions(tmp_path, "r1")
        assert (run_dir / "eval-summary.json").exists()

    def test_reads_dataset_from_meta(self, tmp_path):
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        meta = {"dataset": "princeton-nlp/SWE-bench_Lite"}
        (run_dir / "meta.json").write_text(json.dumps(meta))

        with patch("benchmark._util.ensure_docker_running", autospec=True, return_value=True):
            evaluate_predictions(tmp_path, "r1")
        # Should complete without error (no predictions to evaluate)
        assert (run_dir / "eval-summary.json").exists()

    def test_skips_when_docker_unavailable(self, tmp_path):
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        with patch("benchmark._util.ensure_docker_running", autospec=True, return_value=False):
            evaluate_predictions(tmp_path, "r1")
        # Should return early — no eval-summary written
        assert not (run_dir / "eval-summary.json").exists()


# ── upload tracker ────────────────────────────────────────────────────────

from benchmark.online.upload_tracker import (
    flush_pending_uploads,
    load_uploaded,
    mark_uploaded,
)
from benchmark.online.validation import suspicious_upload_reason


class TestMarkUploaded:
    def test_creates_file(self, tmp_path):
        mark_uploaded(tmp_path, "id1", "claude", "run1")
        assert (tmp_path / "uploaded.jsonl").exists()

    def test_appends(self, tmp_path):
        mark_uploaded(tmp_path, "id1", "claude", "run1")
        mark_uploaded(tmp_path, "id2", "kodo:solo", "run1")
        lines = (tmp_path / "uploaded.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2


class TestLoadUploaded:
    def test_no_file(self, tmp_path):
        assert load_uploaded(tmp_path) == set()

    def test_loads_pairs(self, tmp_path):
        mark_uploaded(tmp_path, "id1", "claude", "run1")
        mark_uploaded(tmp_path, "id2", "kodo:solo", "run2")
        uploaded = load_uploaded(tmp_path)
        assert uploaded == {("id1", "claude"), ("id2", "kodo:solo")}

    def test_skips_bad_lines(self, tmp_path):
        f = tmp_path / "uploaded.jsonl"
        f.write_text(
            '{"instance_id":"id1","arm":"claude","run_id":"r1"}\n'
            'bad json\n'
            '{"instance_id":"id2","arm":"kodo","run_id":"r2"}\n'
        )
        assert load_uploaded(tmp_path) == {("id1", "claude"), ("id2", "kodo")}


class TestOnlineValidation:
    def test_flags_any_zero_patch_upload(self):
        reason = suspicious_upload_reason(
            status="ok",
            elapsed_s=1.0,
            patch_len=0,
            agent_output={
                "msg": {
                    "type": "error",
                    "message": "You've hit your usage limit. Upgrade to Pro.",
                }
            },
        )

        assert reason == "no_patch"


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

    def test_no_config_returns_error(self, tmp_path):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=False):
            ret = flush_pending_uploads(tmp_path)
        assert ret == 1

    def test_no_runs(self, tmp_path):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True):
            ret = flush_pending_uploads(tmp_path)
        assert ret == 0

    def test_uploads_pending(self, tmp_path):
        self._setup_run(
            tmp_path, "r1",
            [{"instance_id": "id1", "arm": "claude", "status": "ok", "elapsed_s": 10}],
            {"predictions-claude.jsonl": [
                {"instance_id": "id1", "model_name_or_path": "claude",
                 "arm": "claude", "model_patch": "diff"}
            ]},
        )

        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.upload_task_result", autospec=True) as mock_upload:
            ret = flush_pending_uploads(tmp_path)

        assert ret == 0
        mock_upload.assert_called_once()
        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs["instance_id"] == "id1"
        assert call_kwargs["patch"] == "diff"
        # Should be marked as uploaded
        assert ("id1", "claude") in load_uploaded(tmp_path)

    def test_skips_already_uploaded(self, tmp_path):
        self._setup_run(
            tmp_path, "r1",
            [{"instance_id": "id1", "arm": "claude", "status": "ok"}],
        )
        # Pre-mark as uploaded
        mark_uploaded(tmp_path, "id1", "claude", "r1")

        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.upload_task_result", autospec=True) as mock_upload:
            ret = flush_pending_uploads(tmp_path)

        assert ret == 0
        mock_upload.assert_not_called()

    def test_handles_upload_failure(self, tmp_path):
        self._setup_run(
            tmp_path, "r1",
            [{"instance_id": "id1", "arm": "claude", "status": "ok"}],
        )

        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.upload_task_result", autospec=True,
                   side_effect=Exception("server down")):
            ret = flush_pending_uploads(tmp_path)

        assert ret == 1  # failure
        # Should NOT be marked as uploaded
        assert load_uploaded(tmp_path) == set()

    def test_skips_suspicious_rows_without_hitting_server(self, tmp_path):
        self._setup_run(
            tmp_path, "r1",
            [{
                "instance_id": "id1",
                "arm": "codex",
                "status": "ok",
                "elapsed_s": 1.0,
                "patch_len": 0,
                "agent_output": {
                    "msg": {"type": "error", "message": "You've hit your usage limit."}
                },
            }],
        )

        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client._post", autospec=True) as mock_post:
            ret = flush_pending_uploads(tmp_path)

        assert ret == 0
        mock_post.assert_not_called()
        assert ("id1", "codex") in load_uploaded(tmp_path)


# ── upload (publish) ──────────────────────────────────────────────────────

from benchmark.online.publish import _dataset_key, _DatasetBuild


class TestDatasetKey:
    def test_verified(self):
        assert _dataset_key("princeton-nlp/SWE-bench_Verified") == "verified"

    def test_pro(self):
        assert _dataset_key("ScaleAI/SWE-bench_Pro") == "pro"

    def test_lite(self):
        assert _dataset_key("princeton-nlp/SWE-bench_Lite") == "lite"

    def test_unknown(self):
        assert _dataset_key("something/else") == ""



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

        with patch("benchmark.runner.log", autospec=True) as mock_log, \
             patch("benchmark.online.client.is_configured", autospec=True, return_value=False):
            from benchmark.runner import _upload_run_online
            _upload_run_online("run1", [], [], 100, "d")
            _upload_run_online("run2", [], [], 100, "d")  # second call

        # Warning should fire exactly once
        warning_calls = [c for c in mock_log.warning.call_args_list
                         if "Online uploads disabled" in str(c)]
        assert len(warning_calls) == 1
        runner_mod._upload_warned = False  # clean up


class TestMainCLI:
    def test_status_flag(self, tmp_path):
        with patch("sys.argv", ["benchmark", "--status", "--workspace", str(tmp_path)]):
            ret = main()
        assert ret == 0

    def test_report_only(self, tmp_path):
        run_dir = tmp_path / "runs" / "r1"
        run_dir.mkdir(parents=True)
        meta = {"dataset": "d", "task_count": 0, "arms": []}
        (run_dir / "meta.json").write_text(json.dumps(meta))
        (run_dir / "results.jsonl").write_text("")
        (run_dir / "eval-summary.json").write_text("{}")

        with patch("sys.argv", ["benchmark", "--report-only", "--run-id", "r1",
                                "--workspace", str(tmp_path)]):
            ret = main()
        assert ret == 0

    def test_report_only_skips_distributed_mode_when_configured(self, tmp_path):
        """Regression: report-only should not contact the online assignment server."""
        with patch("sys.argv", ["benchmark", "--report-only", "--run-id", "r1",
                                "--workspace", str(tmp_path)]), \
             patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.whoami", autospec=True) as mock_whoami, \
             patch("benchmark.__main__._run_distributed", autospec=True) as mock_distributed, \
             patch("benchmark.report.generate_report", autospec=True, return_value=0) as mock_report:
            ret = main()

        assert ret == 0
        mock_report.assert_called_once_with(tmp_path, "r1")
        mock_whoami.assert_not_called()
        mock_distributed.assert_not_called()

    def test_evaluate_only_skips_distributed_mode_when_configured(self, tmp_path):
        """Regression: evaluate-only should stay on local artifacts too."""
        with patch("sys.argv", ["benchmark", "--evaluate-only", "--run-id", "r1",
                                "--workspace", str(tmp_path)]), \
             patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.whoami", autospec=True) as mock_whoami, \
             patch("benchmark.__main__._run_distributed", autospec=True) as mock_distributed, \
             patch("benchmark.evaluate.evaluate_predictions", autospec=True) as mock_evaluate, \
             patch("benchmark.report.generate_report", autospec=True, return_value=0) as mock_report:
            ret = main()

        assert ret == 0
        mock_evaluate.assert_called_once_with(tmp_path, "r1")
        mock_report.assert_called_once_with(tmp_path, "r1")
        mock_whoami.assert_not_called()
        mock_distributed.assert_not_called()

    def test_upload_pending_no_auth(self, tmp_path):
        with patch("sys.argv", ["benchmark", "--upload-pending",
                                "--workspace", str(tmp_path)]), \
             patch("benchmark.online.config._CLIENT_CREDENTIALS", None), \
             patch.dict("os.environ", {}, clear=True):
            ret = main()
        assert ret == 1


# ── distribute ─────────────────────────────────────────────────────────────

from benchmark.online.distribute import prioritize_assignments


class TestPrioritizeAssignments:
    def test_empty_dataset(self):
        result = prioritize_assignments(
            all_instance_ids=[],
            results={},
            backends=["claude"],
            active_claims=set(),
        )
        assert result == []

    def test_no_backends(self):
        result = prioritize_assignments(
            all_instance_ids=["id1"],
            results={},
            backends=[],
            active_claims=set(),
        )
        assert result == []

    def test_all_already_evaluated(self):
        result = prioritize_assignments(
            all_instance_ids=["id1"],
            results={"id1": {"claude": {"status": "ok"}}},
            backends=["claude"],
            active_claims=set(),
        )
        assert result == []

    def test_assigns_missing_backend(self):
        result = prioritize_assignments(
            all_instance_ids=["id1"],
            results={"id1": {"claude": {"status": "ok"}}},
            backends=["kodo:solo"],
            active_claims=set(),
        )
        assert result == [{"instance_id": "id1", "arm": "kodo:solo"}]

    def test_prefers_tasks_with_other_coverage(self):
        """Tasks evaluated by other backends should be prioritized."""
        result = prioritize_assignments(
            all_instance_ids=["id1", "id2"],
            results={
                "id1": {"claude": {"status": "ok"}},  # has 1 other backend
                "id2": {},  # no coverage
            },
            backends=["kodo:solo"],
            active_claims=set(),
        )
        assert len(result) == 2
        assert result[0]["instance_id"] == "id1"  # higher comparison value
        assert result[1]["instance_id"] == "id2"

    def test_excludes_active_claims(self):
        result = prioritize_assignments(
            all_instance_ids=["id1", "id2"],
            results={},
            backends=["claude"],
            active_claims={("id1", "claude")},
        )
        assert result == [{"instance_id": "id2", "arm": "claude"}]

    def test_limit(self):
        result = prioritize_assignments(
            all_instance_ids=["id1", "id2", "id3"],
            results={},
            backends=["claude"],
            active_claims=set(),
            limit=2,
        )
        assert len(result) == 2

    def test_multiple_backends(self):
        result = prioritize_assignments(
            all_instance_ids=["id1"],
            results={},
            backends=["claude", "kodo:solo"],
            active_claims=set(),
        )
        assert len(result) == 2
        arms = {a["arm"] for a in result}
        assert arms == {"claude", "kodo:solo"}

    def test_deterministic_ordering(self):
        """Same input always produces same output."""
        kwargs = dict(
            all_instance_ids=["id2", "id1", "id3"],
            results={},
            backends=["claude", "kodo"],
            active_claims=set(),
        )
        r1 = prioritize_assignments(**kwargs)
        r2 = prioritize_assignments(**kwargs)
        assert r1 == r2

    def test_mixed_coverage(self):
        """Complex scenario: mix of coverage levels."""
        result = prioritize_assignments(
            all_instance_ids=["id1", "id2", "id3"],
            results={
                "id1": {"claude": {}, "cursor": {}},  # 2 other backends
                "id2": {"claude": {}},  # 1 other backend
                "id3": {},  # none
            },
            backends=["kodo:solo"],
            active_claims=set(),
        )
        # Ordered by coverage: id1 (2), id2 (1), id3 (0)
        assert result[0]["instance_id"] == "id1"
        assert result[1]["instance_id"] == "id2"
        assert result[2]["instance_id"] == "id3"

    def test_skips_already_covered_backends_only(self):
        """If backend A is done but B isn't, assign B only."""
        result = prioritize_assignments(
            all_instance_ids=["id1"],
            results={"id1": {"claude": {}}},
            backends=["claude", "kodo:solo"],
            active_claims=set(),
        )
        assert len(result) == 1
        assert result[0]["arm"] == "kodo:solo"

    def test_arm_pressure_steers_away_from_busy_arms(self):
        """Arms with more active contributors are deprioritized."""
        result = prioritize_assignments(
            all_instance_ids=["id1"],
            results={},
            backends=["claude", "cursor"],
            active_claims=set(),
            arm_pressure={"cursor": 3, "claude": 0},
        )
        assert len(result) == 2
        # claude (pressure=0) should come before cursor (pressure=3)
        assert result[0]["arm"] == "claude"
        assert result[1]["arm"] == "cursor"

    def test_arm_pressure_secondary_to_coverage(self):
        """Coverage still takes priority over arm pressure."""
        result = prioritize_assignments(
            all_instance_ids=["id1", "id2"],
            results={
                "id1": {"codex": {}},  # has 1 other backend
                "id2": {},  # no coverage
            },
            backends=["claude"],
            active_claims=set(),
            arm_pressure={"claude": 5},  # high pressure, but coverage wins
        )
        assert result[0]["instance_id"] == "id1"  # higher coverage wins

    def test_arm_pressure_breaks_coverage_ties(self):
        """When coverage is equal, pressure breaks the tie."""
        result = prioritize_assignments(
            all_instance_ids=["id1", "id2"],
            results={},  # both have 0 coverage
            backends=["claude", "cursor"],
            active_claims=set(),
            arm_pressure={"cursor": 2, "claude": 0},
        )
        # Same instance_id, claude (0 pressure) before cursor (2 pressure)
        claude_results = [r for r in result if r["arm"] == "claude"]
        cursor_results = [r for r in result if r["arm"] == "cursor"]
        assert len(claude_results) == 2
        assert len(cursor_results) == 2
        # For same instance, claude should come first
        id1_arms = [r["arm"] for r in result if r["instance_id"] == "id1"]
        assert id1_arms[0] == "claude"

    def test_arm_pressure_none_treated_as_zero(self):
        """Missing arms in pressure dict treated as 0 (most attractive)."""
        result = prioritize_assignments(
            all_instance_ids=["id1"],
            results={},
            backends=["claude", "cursor"],
            active_claims=set(),
            arm_pressure={"cursor": 1},  # claude not mentioned = 0
        )
        assert result[0]["arm"] == "claude"
        assert result[1]["arm"] == "cursor"

    def test_arm_pressure_empty_dict_no_effect(self):
        """Empty pressure dict is equivalent to no pressure."""
        no_pressure = prioritize_assignments(
            all_instance_ids=["id1", "id2"],
            results={},
            backends=["claude"],
            active_claims=set(),
        )
        with_empty = prioritize_assignments(
            all_instance_ids=["id1", "id2"],
            results={},
            backends=["claude"],
            active_claims=set(),
            arm_pressure={},
        )
        assert no_pressure == with_empty


# ── client fetch_assignments ───────────────────────────────────────────────

import benchmark.online.config as online_config
from benchmark.online.client import _request, fetch_assignments, is_configured
import benchmark.online.db as online_db


class TestOnlineClientConfig:
    def test_unconfigured_probe_does_not_freeze_empty_state(self):
        """Late-loaded env should still be observed after an early empty probe."""
        with patch("benchmark.online.config._CLIENT_CREDENTIALS", None):
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

        with patch("benchmark.online.config._CLIENT_CREDENTIALS", None):
            with patch.dict(
                "os.environ",
                {
                    "KODO_BENCH_URL": "https://bench-one.example",
                    "KODO_BENCH_TOKEN": "token-1",
                },
                clear=True,
            ):
                assert is_configured() is True

            with patch.dict(
                "os.environ",
                {
                    "KODO_BENCH_URL": "https://bench-two.example",
                    "KODO_BENCH_TOKEN": "token-2",
                },
                clear=True,
            ), patch("urllib.request.urlopen", autospec=True, return_value=response) as mock_urlopen:
                _request("GET", "/api/whoami")
                assert online_config._CLIENT_CREDENTIALS == (
                    "https://bench-one.example",
                    "token-1",
                )

        request = mock_urlopen.call_args.args[0]
        assert request.full_url == "https://bench-one.example/api/whoami"
        assert request.get_header("Authorization") == "Bearer token-1"


class TestFetchAssignments:
    def test_returns_none_when_unconfigured(self):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=False):
            result = fetch_assignments(["claude"])
        assert result is None

    def test_raises_on_error(self):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client._post_json", autospec=True, side_effect=Exception("timeout")):
            with pytest.raises(Exception, match="timeout"):
                fetch_assignments(["claude"])

    def test_returns_assignments(self):
        assignments = [{"instance_id": "id1", "arm": "claude", "dataset": "pro"}]
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client._post_json", autospec=True,
                   return_value={"assignments": assignments}):
            result = fetch_assignments(["claude"], datasets={"pro": ["id1"]})
        assert result == assignments

    def test_passes_datasets(self):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client._post_json", autospec=True,
                   return_value={"assignments": []}) as mock_post:
            fetch_assignments(["claude"], datasets={"pro": ["id1"], "verified": ["id2"]})
        body = mock_post.call_args[0][1]
        assert body["datasets"] == {"pro": ["id1"], "verified": ["id2"]}

    def test_empty_response(self):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client._post_json", autospec=True,
                   return_value={"assignments": []}):
            result = fetch_assignments(["claude"], datasets={"pro": ["id1"]})
        assert result == []


class TestOnlineDb:
    def test_delete_patch_skips_when_storage_dep_missing(self):
        with patch("benchmark.online.db._bucket", autospec=True, side_effect=ImportError), \
             patch("benchmark.online.db.log", autospec=True) as mock_log:
            online_db.delete_patch("pro", "id1", "codex")

        mock_log.warning.assert_called_once()


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

        with patch("benchmark.runner._safe_run", autospec=True, side_effect=fake_run), \
             patch("benchmark.runner._save_run_meta", autospec=True):
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

        # Should have run exactly the assigned pairs, not all 4 combinations
        assert set(call_log) == {("id1", "claude"), ("id2", "kodo:solo")}

    def test_none_assignments_runs_everything(self, tmp_path):
        """assignments=None means normal mode (all tasks x arms)."""
        t1 = SWETask("id1", "o/r", "abc", "problem", [], [])

        call_log = []

        def fake_run(task, arm, workspace, timeout, run_dir=None, **kwargs):
            call_log.append((task.instance_id, arm))
            return TaskResult(task.instance_id, arm, "patch", 1.0, "ok")

        with patch("benchmark.runner._safe_run", autospec=True, side_effect=fake_run), \
             patch("benchmark.runner._save_run_meta", autospec=True):
            run_benchmark(
                tasks=[t1],
                arms=["claude", "kodo:solo"],
                workspace=tmp_path,
                run_id="test",
                timeout=100,
                assignments=None,
            )

        assert set(call_log) == {("id1", "claude"), ("id1", "kodo:solo")}

    def test_crash_recovery_in_distributed_mode(self, tmp_path):
        """Locally completed tasks within distributed mode are skipped."""
        t1 = SWETask("id1", "o/r", "abc", "problem", [], [])

        # Pre-populate local results (simulating crash recovery)
        run_dir = tmp_path / "runs" / "test"
        run_dir.mkdir(parents=True)
        (run_dir / "results.jsonl").write_text(
            '{"instance_id":"id1","arm":"claude","status":"ok"}\n'
        )

        call_log = []

        def fake_run(task, arm, workspace, timeout, run_dir=None, **kwargs):
            call_log.append((task.instance_id, arm))
            return TaskResult(task.instance_id, arm, "patch", 1.0, "ok")

        with patch("benchmark.runner._safe_run", autospec=True, side_effect=fake_run), \
             patch("benchmark.runner._save_run_meta", autospec=True):
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

        # claude should be skipped (already in local results), kodo:solo should run
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
        with patch("sys.argv", ["benchmark",
                                "--workspace", str(tmp_path)]), \
             self._mock_load_tasks(), \
             patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.fetch_assignments", autospec=True, return_value=[]):
            ret = main()
        assert ret == 0

    def test_falls_back_to_local_when_not_configured(self, tmp_path):
        """Falls through to local mode when env vars are missing."""
        with patch("sys.argv", ["benchmark",
                                "--workspace", str(tmp_path)]), \
             patch("benchmark.online.client.is_configured", autospec=True, return_value=False), \
             patch("benchmark.tasks.load_tasks", autospec=True, return_value=[
                 SWETask("t1", "o/r", "abc", "p", [], [])
             ]), \
             patch("benchmark.runner.run_benchmark", autospec=True), \
             patch("benchmark.evaluate.evaluate_predictions", autospec=True), \
             patch("benchmark.report.generate_report", autospec=True, return_value=0):
            ret = main()
        assert ret == 0

    def test_distribute_server_error_fails(self, tmp_path):
        """Fails hard when server returns an error."""
        with patch("sys.argv", ["benchmark",
                                "--workspace", str(tmp_path)]), \
             self._mock_load_tasks(), \
             patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.fetch_assignments", autospec=True,
                   side_effect=Exception("HTTP Error 502: Bad Gateway")):
            ret = main()
        assert ret == 1

    def test_distribute_sends_both_datasets(self, tmp_path):
        """Sends both pro and verified instance_ids to server."""
        with patch("sys.argv", ["benchmark",
                                "--workspace", str(tmp_path)]), \
             self._mock_load_tasks(), \
             patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.fetch_assignments", autospec=True,
                   return_value=[]) as mock_fetch, \
             patch("benchmark._util.shutil.which", autospec=True, return_value=None):
            main()
        call_kwargs = mock_fetch.call_args[1]
        assert "pro" in call_kwargs["datasets"]
        assert "verified" in call_kwargs["datasets"]
        assert "pro1" in call_kwargs["datasets"]["pro"]
        assert "ver1" in call_kwargs["datasets"]["verified"]

    def test_distribute_auto_detects_backends(self, tmp_path):
        """Without --arm auto-detects available backends."""
        with patch("sys.argv", ["benchmark",
                                "--workspace", str(tmp_path)]), \
             self._mock_load_tasks(), \
             patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.fetch_assignments", autospec=True,
                   return_value=[]) as mock_fetch, \
             patch("benchmark._util.shutil.which", autospec=True, return_value=None):
            main()
        call_kwargs = mock_fetch.call_args[1]
        assert "kodo" in call_kwargs["backends"]

    def test_distribute_explicit_backends_override(self, tmp_path):
        """--backends overrides auto-detection."""
        with patch("sys.argv", ["benchmark",
                                "--backends", "claude,cursor",
                                "--workspace", str(tmp_path)]), \
             self._mock_load_tasks(), \
             patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.fetch_assignments", autospec=True,
                   return_value=[]) as mock_fetch:
            main()
        call_kwargs = mock_fetch.call_args[1]
        assert set(call_kwargs["backends"]) == {"claude", "cursor"}

    def test_distribute_polls_until_empty(self, tmp_path):
        """Keeps polling until server returns no assignments."""
        batch1 = [{"instance_id": "pro1", "arm": "claude", "dataset": "pro"}]

        with patch("sys.argv", ["benchmark",
                                "--workspace", str(tmp_path)]), \
             self._mock_load_tasks(), \
             patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.fetch_assignments", autospec=True,
                   side_effect=[batch1, []]) as mock_fetch, \
             patch("benchmark.runner.run_benchmark", autospec=True):
            ret = main()
        assert ret == 0
        assert mock_fetch.call_count == 2

    def test_distribute_server_error_after_batch_returns_ok(self, tmp_path):
        """If server errors after completing a batch, return 0 (work was done)."""
        batch1 = [{"instance_id": "pro1", "arm": "claude", "dataset": "pro"}]

        with patch("sys.argv", ["benchmark",
                                "--workspace", str(tmp_path)]), \
             self._mock_load_tasks(), \
             patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client.fetch_assignments", autospec=True,
                   side_effect=[batch1, Exception("connection reset")]), \
             patch("benchmark.runner.run_benchmark", autospec=True):
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
    def test_returns_none_when_unconfigured(self):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=False):
            result = fetch_unevaluated("pro")
        assert result is None

    def test_returns_none_on_error(self):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client._get_json", autospec=True, side_effect=Exception("timeout")):
            result = fetch_unevaluated("pro")
        assert result is None

    def test_returns_predictions(self):
        preds = [{"instance_id": "id1", "arm": "claude", "patch": "diff"}]
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client._get_json", autospec=True,
                   return_value={"predictions": preds}):
            result = fetch_unevaluated("pro")
        assert result == preds

    def test_empty_response(self):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client._get_json", autospec=True,
                   return_value={"predictions": []}):
            result = fetch_unevaluated("pro")
        assert result == []

    def test_maps_dataset_key(self):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark.online.client._get_json", autospec=True,
                   return_value={"predictions": []}) as mock_get:
            fetch_unevaluated("ScaleAI/SWE-bench_Pro")
        assert mock_get.call_args[0][0] == "/api/unevaluated/pro"


class TestOnlineMirror:
    def test_public_base_url_prefers_explicit_then_env_then_default(self):
        with patch.dict("os.environ", {"KODO_BENCH_URL": "https://env.example"}, clear=True):
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

        with patch("benchmark.online.mirror.fetch_dataset_index", autospec=True, return_value=index), \
             patch("benchmark.online.mirror.fetch_dataset_patches", autospec=True,
                   return_value={"id1/claude": "diff --git"}):
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
        assert json.loads((out / "patches.json").read_text()) == {"id1/claude": "diff --git"}

    def test_load_rows_accepts_directory_or_file(self, tmp_path):
        dataset_dir = tmp_path / "verified"
        dataset_dir.mkdir()
        rows = [{"instance_id": "id1", "arm": "claude"}]
        (dataset_dir / "rows.json").write_text(json.dumps(rows))

        assert load_rows(dataset_dir) == rows
        assert load_rows(dataset_dir / "rows.json") == rows

    def test_load_rows_expands_user_home(self, tmp_path):
        home = tmp_path / "home"
        dataset_dir = home / ".kodo" / "benchmark" / "mirror" / "verified"
        dataset_dir.mkdir(parents=True)
        rows = [{"instance_id": "id1", "arm": "claude"}]
        (dataset_dir / "rows.json").write_text(json.dumps(rows))

        with patch.dict("os.environ", {"HOME": str(home)}, clear=False):
            assert load_rows("~/.kodo/benchmark/mirror/verified") == rows

    def test_fetch_patch_quotes_instance_id_and_arm(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"patch body"

        with patch("urllib.request.urlopen", autospec=True, return_value=response) as mock_urlopen:
            patch_text = fetch_patch("verified", "repo__name-1", "kodo:solo+opus")

        assert patch_text == "patch body"
        assert mock_urlopen.call_args.args[0].endswith(
            "/api/patch/verified/repo__name-1/kodo%3Asolo%2Bopus"
        )

    def test_mirror_main_uses_requested_output_dir(self, tmp_path):
        with patch("benchmark.online.mirror.mirror_dataset", autospec=True) as mock_mirror:
            ret = mirror_main(["--dataset", "verified", "--out", str(tmp_path), "--patches"])

        assert ret == 0
        mock_mirror.assert_called_once_with(
            "verified",
            out_dir=tmp_path,
            include_patches=True,
            base_url=None,
        )


class TestCleanupDummyResults:
    def test_candidate_rows_reuses_upload_validation(self):
        rows = [
            {
                "instance_id": "id1",
                "arm": "codex",
                "status": "ok",
                "elapsed_s": 1.0,
                "patch_len": 0,
                "run_id": "run-a",
            },
            {
                "instance_id": "id2",
                "arm": "claude",
                "status": "ok",
                "elapsed_s": 120.0,
                "patch_len": 42,
                "run_id": "run-a",
            },
        ]

        with patch("benchmark.online.cleanup_dummy_results.db.iter_task_results", autospec=True, return_value=rows):
            result = candidate_rows("pro", run_id="run-a")

        assert result == [
            {
                "instance_id": "id1",
                "arm": "codex",
                "status": "ok",
                "elapsed_s": 1.0,
                "patch_len": 0,
                "run_id": "run-a",
                "reason": "no_patch",
            }
        ]

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

            ret = cleanup_main([
                "--dataset", "pro",
                "--reason", "no_patch",
                "--max-elapsed", "10",
            ])

        out = capsys.readouterr().out
        assert ret == 0
        assert "candidates=1" in out
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

        with patch(
            "benchmark.online.cleanup_dummy_results.candidate_rows",
            autospec=True,
            return_value=rows,
        ), patch(
            "benchmark.online.cleanup_dummy_results.db.delete_task_results_batch",
            autospec=True,
        ) as mock_batch, patch(
            "benchmark.online.cleanup_dummy_results.db.delete_empty_result_docs",
            autospec=True,
            return_value=1,
        ) as mock_empty:
            from benchmark.online.cleanup_dummy_results import main as cleanup_main

            ret = cleanup_main([
                "--dataset", "pro",
                "--reason", "no_patch",
                "--apply",
            ])

        assert ret == 0
        mock_batch.assert_called_once_with("pro", [("id1", "codex")])
        mock_empty.assert_called_once_with("pro")


# ── evaluate_pending ──────────────────────────────────────────────────────


from benchmark.evaluate_pending import evaluate_pending


class TestEvaluatePending:
    def test_fails_when_unconfigured(self, tmp_path):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=False):
            result = evaluate_pending(tmp_path, dataset_arg="verified")
        assert result == 1

    def test_fails_when_docker_unavailable(self, tmp_path):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark._util.ensure_docker_running", autospec=True, return_value=False):
            result = evaluate_pending(tmp_path, dataset_arg="verified")
        assert result == 1

    def test_returns_0_when_nothing_pending(self, tmp_path):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark._util.ensure_docker_running", autospec=True, return_value=True), \
             patch("benchmark.online.client.fetch_unevaluated", autospec=True, return_value=[]):
            result = evaluate_pending(tmp_path, dataset_arg="verified")
        assert result == 0

    def test_returns_1_on_fetch_failure(self, tmp_path):
        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark._util.ensure_docker_running", autospec=True, return_value=True), \
             patch("benchmark.online.client.fetch_unevaluated", autospec=True, return_value=None):
            result = evaluate_pending(tmp_path, dataset_arg="verified")
        assert result == 1

    def test_writes_predictions_and_runs_eval(self, tmp_path):
        """Full flow: fetch → write → eval per arm → upload per arm."""
        predictions = [
            {"instance_id": "django__django-12345", "arm": "claude", "patch": "diff1"},
            {"instance_id": "django__django-67890", "arm": "claude", "patch": "diff2"},
            {"instance_id": "django__django-12345", "arm": "kodo:solo", "patch": "diff3"},
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

        def fake_eval_arm(run_dir, arm, run_id, dataset, on_instance=None, **kwargs):
            """Simulate eval: call on_instance for each task, then return summary."""
            result = eval_results.get(arm, {})
            if on_instance:
                for iid in result.get("resolved", []):
                    on_instance(iid, True)
                for iid in result.get("failed", []):
                    on_instance(iid, False)
            return result

        upload_calls = []

        def fake_upload(dataset, arm, *, resolved=None, failed=None, error=None):
            upload_calls.append({"dataset": dataset, "arm": arm,
                                  "resolved": resolved, "failed": failed})

        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark._util.ensure_docker_running", autospec=True, return_value=True), \
             patch("benchmark.online.client.fetch_unevaluated", autospec=True, return_value=predictions), \
             patch("benchmark.evaluate.evaluate_arm", autospec=True, side_effect=fake_eval_arm) as mock_eval, \
             patch("benchmark.online.client.upload_eval_results", autospec=True, side_effect=fake_upload):
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

        # Check eval was called per arm
        assert mock_eval.call_count == 2

        # Check uploads: 3 per-instance streaming + 2 bulk (one per arm)
        assert len(upload_calls) == 5
        # Verify all instances were uploaded (via streaming)
        streamed = {(c["arm"], c["resolved"][0] if c["resolved"] else c["failed"][0])
                    for c in upload_calls if len(c.get("resolved", []) + c.get("failed", [])) == 1}
        assert ("claude", "django__django-12345") in streamed
        assert ("claude", "django__django-67890") in streamed
        assert ("kodo:solo", "django__django-12345") in streamed
        # Verify bulk uploads happened (one per arm with full results)
        bulk = [c for c in upload_calls if len(c.get("resolved", []) + c.get("failed", [])) > 1]
        assert len(bulk) == 1  # only claude has 2 instances; kodo:solo has 1 (same as streaming)

    def test_returns_0_even_when_eval_returns_empty(self, tmp_path):
        """Returns 0 when eval produces no results (empty arm)."""
        predictions = [{"instance_id": "id1", "arm": "claude", "patch": "diff"}]

        def fake_eval(run_dir, arm, run_id, dataset, **kwargs):
            return {"resolved": [], "failed": [], "error": [], "resolve_rate": 0.0}

        with patch("benchmark.online.client.is_configured", autospec=True, return_value=True), \
             patch("benchmark._util.ensure_docker_running", autospec=True, return_value=True), \
             patch("benchmark.online.client.fetch_unevaluated", autospec=True, return_value=predictions), \
             patch("benchmark.evaluate.evaluate_arm", autospec=True, side_effect=fake_eval):
            result = evaluate_pending(tmp_path, dataset_arg="verified")

        assert result == 0


class TestProgressView:
    def test_progress_html_exists(self):
        """progress.html exists in the static directory."""
        static = Path(__file__).parent.parent / "benchmark" / "online" / "static"
        assert (static / "progress.html").is_file()

    def test_progress_html_is_valid(self):
        """Basic sanity: contains expected elements."""
        static = Path(__file__).parent.parent / "benchmark" / "online" / "static"
        html = (static / "progress.html").read_text()
        assert "<!DOCTYPE html>" in html
        assert "kodo bench" in html
        assert "progress" in html.lower()
        assert "data/verified/index.json" in html
        assert "data/pro/index.json" in html

    def test_main_viewer_links_to_progress(self):
        """Main viewer has a link to the progress page."""
        static = Path(__file__).parent.parent / "benchmark" / "online" / "static"
        html = (static / "index.html").read_text()
        assert "progress.html" in html

    def test_main_viewer_pending_excludes_error_rows(self):
        """Pending-eval UI should only count ok/partial runs, not error rows."""
        static = Path(__file__).parent.parent / "benchmark" / "online" / "static"
        html = (static / "index.html").read_text()
        assert "r.status === 'ok' || r.status === 'partial'" in html
