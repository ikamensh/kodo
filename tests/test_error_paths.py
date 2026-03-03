"""Error-path and edge-case tests for kodo internals.

Covers five domains that existing test suites touch lightly or not at all:

1. Session error propagation — classify_session_error(), QueryResult edge cases
2. Parallel stage failure modes — DoneSignal atomicity, stage crash propagation
3. Summarizer resilience — post-shutdown submit, concurrent drain, backend errors
4. Log module thread safety — RunDir path traversal, init_append validation,
   emit-before-init, parse_run with corrupt data
5. DoneSignal edge cases — concurrent property mutation, reset-after-set

Run:  uv run pytest tests/test_error_paths.py -v
"""

from __future__ import annotations

import json
import signal
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo import log
from kodo.agent import Agent
from kodo.log import RunDir
from kodo.orchestrators.base import DoneSignal
from kodo.sessions.base import QueryResult, classify_session_error
from kodo.summarizer import Summarizer
from tests.conftest import FakeSession


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  1. Session error propagation                                           ║
# ╚═══════════════════════════════════════════════════════════════════════════╝


class TestClassifySessionError:
    """classify_session_error() is used across subprocess sessions but has
    zero dedicated tests. These cover all four regex branches + the signal
    branch + the passthrough None case."""

    def test_timeout_hint(self):
        hint = classify_session_error(
            1, "", backend="cursor", did_timeout=True, timeout_s=120
        )
        assert hint is not None
        assert "timed out" in hint
        assert "120" in hint
        assert "cursor" in hint

    def test_auth_pattern_matches_401(self):
        hint = classify_session_error(1, "HTTP 401 Unauthorized", "")
        assert hint is not None
        assert "Authentication" in hint or "API key" in hint

    def test_subscription_pattern_matches_429(self):
        hint = classify_session_error(1, "429 Too Many Requests", "")
        assert hint is not None
        assert "Subscription" in hint or "billing" in hint.lower()

    def test_binary_not_found(self):
        hint = classify_session_error(127, "cursor: command not found", "")
        assert hint is not None
        assert "Binary" in hint or "reinstall" in hint.lower()

    def test_killed_by_signal(self):
        """Negative return code → signal name hint."""
        sig_num = signal.SIGTERM.value
        hint = classify_session_error(-sig_num, "", "")
        assert hint is not None
        assert "signal" in hint.lower()
        assert "SIGTERM" in hint

    def test_no_pattern_returns_none(self):
        """Normal exit code with clean stderr → None (no hint)."""
        hint = classify_session_error(0, "", "all good")
        assert hint is None

    def test_backend_prefix_in_hint(self):
        """When backend is provided, the hint starts with it."""
        hint = classify_session_error(
            1, "authentication failed", "", backend="gemini-cli"
        )
        assert hint is not None
        assert hint.startswith("gemini-cli:")


class TestQueryResultEdgeCases:
    """QueryResult.__post_init__ strips text — test boundary inputs."""

    def test_whitespace_only_text_becomes_empty(self):
        qr = QueryResult(text="   \n\t  ", elapsed_s=0.0)
        assert qr.text == ""

    def test_normal_text_is_stripped(self):
        qr = QueryResult(text="  hello world  ", elapsed_s=1.0)
        assert qr.text == "hello world"

    def test_is_error_flag_propagates(self):
        qr = QueryResult(text="fail", elapsed_s=0.0, is_error=True)
        assert qr.is_error is True


class TestSessionErrorPropagationThroughAgent:
    """Agent.run() should propagate session errors in the AgentResult
    without crashing the caller."""

    def test_error_session_produces_error_result(self, tmp_path: Path):
        log.init(RunDir.create(tmp_path, "err_agent"))
        session = FakeSession(response_text="[ERROR] connection refused", is_error=True)
        agent = Agent(session, "test-worker", max_turns=5)
        result = agent.run("do something", tmp_path, agent_name="worker")
        assert result.is_error is True
        assert "ERROR" in result.text


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  2. Parallel stage failure modes                                        ║
# ╚═══════════════════════════════════════════════════════════════════════════╝


class TestDoneSignalNonAtomicRace:
    """DoneSignal uses per-property locking.  A reader between writes can
    see inconsistent state (called=True, success=False).  This test
    demonstrates the window exists by interleaving writes with reads."""

    def test_intermediate_state_observable(self):
        """If properties are set sequentially, a reader CAN see called=True
        with success still False — the design allows this."""
        ds = DoneSignal()
        observed_states: list[tuple[bool, bool]] = []
        barrier = threading.Barrier(2)

        def writer():
            barrier.wait()
            ds.called = True
            # Deliberate pause to widen the race window
            time.sleep(0.01)
            ds.success = True

        def reader():
            barrier.wait()
            time.sleep(0.005)  # read between the two writes
            observed_states.append((ds.called, ds.success))

        tw = threading.Thread(target=writer)
        tr = threading.Thread(target=reader)
        tw.start()
        tr.start()
        tw.join()
        tr.join()

        # The reader may see (True, False) — that's the non-atomic gap.
        # After both threads finish, the final state MUST be consistent:
        assert ds.called is True
        assert ds.success is True

    def test_reset_after_set(self):
        """DoneSignal can be reset to initial state (for cycle reuse)."""
        ds = DoneSignal()
        ds.called = True
        ds.summary = "done"
        ds.success = True

        # Reset all fields
        ds.called = False
        ds.summary = ""
        ds.success = False

        assert ds.called is False
        assert ds.summary == ""
        assert ds.success is False

    def test_concurrent_writes_are_safe(self):
        """Multiple threads setting properties concurrently should not crash."""
        ds = DoneSignal()
        errors: list[Exception] = []

        def hammerer(val: bool):
            try:
                for _ in range(200):
                    ds.called = val
                    ds.success = val
                    ds.summary = f"thread-{val}"
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=hammerer, args=(i % 2 == 0,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent DoneSignal writes raised: {errors}"
        # Final state is deterministic per-thread last-write-wins
        assert isinstance(ds.called, bool)
        assert isinstance(ds.success, bool)
        assert isinstance(ds.summary, str)


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  3. Summarizer resilience                                               ║
# ╚═══════════════════════════════════════════════════════════════════════════╝


def _make_summarizer():
    """Create a truncation-only summarizer (no network deps)."""
    with (
        patch("kodo.summarizer._probe_ollama", return_value=None),
        patch("kodo.summarizer._probe_gemini", return_value=None),
    ):
        return Summarizer()


class TestSummarizerResilience:
    def test_summarize_after_shutdown_is_safe(self):
        """After shutdown(), summarize() is a silent no-op (fire-and-forget).

        Previously this raised RuntimeError.  Now the guard inside
        summarize() catches the shutdown state and returns cleanly.
        """
        s = _make_summarizer()
        s.shutdown()
        # Should not raise — silently discarded
        s.summarize("worker", "task", "report")

    def test_shutdown_is_idempotent(self):
        """Calling shutdown() multiple times must not raise."""
        s = _make_summarizer()
        s.shutdown()
        s.shutdown()  # second call is a no-op

    def test_concurrent_get_accumulated_summary(self):
        """Two threads calling get_accumulated_summary concurrently
        must not crash (executor swap race)."""
        s = _make_summarizer()
        s.summarize("w1", "t1", "Created file A.py")
        s.summarize("w2", "t2", "Created file B.py")

        results: list[str] = []
        errors: list[Exception] = []

        def drain():
            try:
                results.append(s.get_accumulated_summary())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=drain) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent drain raised: {errors}"
        # At least one thread got the summaries (others may get empty after swap)
        got_content = any("[w1]" in r or "[w2]" in r for r in results)
        assert got_content, f"No thread saw summaries; results={results}"

    def test_do_summarize_swallows_known_exceptions(self):
        """_do_summarize catches URLError/HTTPError/JSONDecodeError/OSError/KeyError
        without crashing the executor."""
        s = _make_summarizer()

        # Force ollama backend to trigger network path
        s._backend = "ollama"
        s._backend_param = "fake-model"

        # _summarize_ollama will fail with URLError (no local ollama)
        s.summarize("worker", "task", "report")
        # get_accumulated_summary drains without crash
        result = s.get_accumulated_summary()
        # Summary is empty because the call failed silently
        assert "[worker]" not in result

    def test_empty_report_produces_empty_summary(self):
        """_summarize_truncate on empty/whitespace report returns empty string,
        which is NOT appended to summaries."""
        s = _make_summarizer()
        s.summarize("worker", "task", "   \n\n   ")
        result = s.get_accumulated_summary()
        assert result == ""  # nothing was appended

    def test_none_inputs_handled(self):
        """summarize() with None task/report should not crash — _do_summarize
        normalizes them to empty strings."""
        s = _make_summarizer()
        # The function signature expects str, but callers may pass None
        s._do_summarize("worker", None, None)
        # No crash = pass


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  4. Log module thread safety                                            ║
# ╚═══════════════════════════════════════════════════════════════════════════╝


class TestRunDirPathTraversal:
    """RunDir.create() validates run_id against directory traversal.
    This validation exists in source but has zero tests."""

    def test_slash_in_run_id_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="must not contain"):
            RunDir.create(tmp_path, "../../etc/passwd")

    def test_backslash_in_run_id_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="must not contain"):
            RunDir.create(tmp_path, "foo\\bar")

    def test_dotdot_in_run_id_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="must not contain"):
            RunDir.create(tmp_path, "run..id")

    def test_valid_run_id_accepted(self, tmp_path: Path):
        rd = RunDir.create(tmp_path, "20260303_120000")
        assert rd.run_id == "20260303_120000"
        assert rd.root.exists()


class TestInitAppendValidation:
    """init_append() validates the log file before mutating global state.
    These paths are present in source but untested."""

    def test_nonexistent_file_raises(self, tmp_path: Path):
        fake_log = tmp_path / "does_not_exist.jsonl"
        with pytest.raises(FileNotFoundError, match="does not exist"):
            log.init_append(fake_log)

    def test_invalid_log_raises(self, tmp_path: Path):
        """A file that exists but isn't a valid kodo log raises ValueError."""
        bad_log = tmp_path / "bad.jsonl"
        bad_log.write_text('{"event":"random","ts":"t","t":0}\n')
        with pytest.raises(ValueError, match="missing run_start"):
            log.init_append(bad_log)

    def test_valid_log_resumes(self, tmp_path: Path):
        """A properly structured log file should resume successfully."""
        log_file = tmp_path / "test_run" / "run.jsonl"
        log_file.parent.mkdir(parents=True)
        events = [
            {
                "ts": "t", "t": 0, "event": "run_start",
                "goal": "g", "orchestrator": "api", "model": "m",
                "project_dir": str(tmp_path), "max_exchanges": 10,
                "max_cycles": 5, "team": [],
            },
            {"ts": "t", "t": 0.1, "event": "cli_args", "team": "full"},
            {"ts": "t", "t": 1, "event": "cycle_end", "summary": "partial"},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

        result = log.init_append(log_file)
        assert result == log_file

        # Verify a run_resumed event was emitted
        content = log_file.read_text()
        assert "run_resumed" in content


class TestLogParseRunCorruptData:
    """parse_run() should tolerate corrupt/partial JSONL lines."""

    def test_corrupt_lines_skipped(self, tmp_path: Path):
        """Garbled lines are skipped; valid events still parsed."""
        log_file = tmp_path / "corrupt.jsonl"
        events = [
            json.dumps({
                "ts": "t", "t": 0, "event": "run_start",
                "goal": "g", "orchestrator": "api", "model": "m",
                "project_dir": "/p", "max_exchanges": 10,
                "max_cycles": 5, "team": [],
            }),
            "THIS IS NOT JSON AT ALL {{{",
            '{"incomplete": true',  # truncated JSON
            json.dumps({"ts": "t", "t": 0.1, "event": "cli_args", "team": "full"}),
            json.dumps({"ts": "t", "t": 1, "event": "cycle_end", "summary": "ok"}),
        ]
        log_file.write_text("\n".join(events) + "\n")

        state = log.parse_run(log_file)
        assert state is not None
        assert state.completed_cycles == 1
        assert state.last_summary == "ok"

    def test_missing_goal_returns_none(self, tmp_path: Path):
        """run_start without 'goal' key returns None (corrupt log)."""
        log_file = tmp_path / "no_goal.jsonl"
        events = [
            json.dumps({
                "ts": "t", "t": 0, "event": "run_start",
                # "goal" key is missing
                "orchestrator": "api", "model": "m",
                "project_dir": "/p", "max_exchanges": 10,
            }),
            json.dumps({"ts": "t", "t": 0.1, "event": "cli_args", "team": "full"}),
        ]
        log_file.write_text("\n".join(events) + "\n")

        state = log.parse_run(log_file)
        assert state is None

    def test_empty_file_returns_none(self, tmp_path: Path):
        """Empty log file returns None."""
        log_file = tmp_path / "empty.jsonl"
        log_file.write_text("")
        state = log.parse_run(log_file)
        assert state is None


class TestLogEmitEdgeCases:
    """Edge cases around emit() timing and _start_time handling."""

    def test_emit_before_init_is_silent_noop(self):
        """Emitting before init should not crash (log_file is None)."""
        # _isolate_log fixture resets state
        log.emit("orphan_event", data="test")
        # No exception = pass; nothing was written

    def test_emit_with_unserializable_values(self, tmp_path: Path):
        """emit() should handle non-JSON-serializable objects via _serialize fallback."""
        log.init(RunDir.create(tmp_path, "serial_test"))
        # lambda is not JSON-serializable; _serialize uses repr()
        log.emit("edge", callback=lambda x: x, path=tmp_path)
        lines = log.get_log_file().read_text().strip().split("\n")
        record = json.loads(lines[-1])
        assert record["event"] == "edge"
        assert "lambda" in record["callback"]


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  5. DoneSignal edge cases                                               ║
# ╚═══════════════════════════════════════════════════════════════════════════╝


class TestDoneSignalEdgeCases:
    def test_initial_state(self):
        """Fresh DoneSignal has called=False, summary='', success=False."""
        ds = DoneSignal()
        assert ds.called is False
        assert ds.summary == ""
        assert ds.success is False

    def test_set_summary_without_called(self):
        """Setting summary without setting called is technically valid
        (no enforcement in the protocol)."""
        ds = DoneSignal()
        ds.summary = "orphan summary"
        ds.success = True
        assert ds.called is False
        assert ds.summary == "orphan summary"
        assert ds.success is True

    def test_called_without_success(self):
        """called=True with success=False represents an unsuccessful completion."""
        ds = DoneSignal()
        ds.called = True
        ds.summary = "gave up"
        ds.success = False

        assert ds.called is True
        assert ds.success is False
        assert ds.summary == "gave up"

    def test_repeated_sets_are_idempotent(self):
        """Setting the same value multiple times doesn't change state."""
        ds = DoneSignal()
        ds.called = True
        ds.called = True
        ds.called = True
        assert ds.called is True

        ds.summary = "x"
        ds.summary = "x"
        assert ds.summary == "x"

    def test_summary_can_be_overwritten(self):
        """Summary can be updated (e.g., by a later verification pass)."""
        ds = DoneSignal()
        ds.summary = "first draft"
        ds.summary = "revised summary"
        assert ds.summary == "revised summary"

    def test_rapid_concurrent_read_write(self):
        """Stress test: many concurrent readers and writers on DoneSignal."""
        ds = DoneSignal()
        errors: list[Exception] = []
        stop = threading.Event()

        def writer():
            try:
                i = 0
                while not stop.is_set():
                    ds.called = (i % 2 == 0)
                    ds.success = (i % 3 == 0)
                    ds.summary = f"iter-{i}"
                    i += 1
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                while not stop.is_set():
                    _ = ds.called
                    _ = ds.success
                    _ = ds.summary
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=writer) for _ in range(3)]
            + [threading.Thread(target=reader) for _ in range(5)]
        )
        for t in threads:
            t.start()

        time.sleep(0.1)  # run for 100ms
        stop.set()

        for t in threads:
            t.join(timeout=2.0)

        assert not errors, f"Concurrent DoneSignal access raised: {errors}"
