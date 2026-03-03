"""Tests for security and robustness fixes F3, F10, F12, F13, F14, F15."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.base import (
    DoneSignal,
    QuickCheck,
    _run_quick_checks,
    _git,
    _resolve_executable,
)


# ---------------------------------------------------------------------------
# F14: RunDir.create() rejects directory-traversal run_ids
# ---------------------------------------------------------------------------


class TestRunDirTraversalValidation:
    def test_rejects_slash(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must not contain"):
            RunDir.create(tmp_path, "../../etc/passwd")

    def test_rejects_backslash(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must not contain"):
            RunDir.create(tmp_path, "..\\..\\etc")

    def test_rejects_dotdot(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="must not contain"):
            RunDir.create(tmp_path, "foo..bar")

    def test_accepts_normal_run_id(self, tmp_path: Path) -> None:
        rd = RunDir.create(tmp_path, "20260101_120000")
        assert rd.run_id == "20260101_120000"
        assert rd.root.exists()

    def test_accepts_none_run_id(self, tmp_path: Path) -> None:
        rd = RunDir.create(tmp_path)
        assert rd.run_id  # auto-generated timestamp
        assert rd.root.exists()


# ---------------------------------------------------------------------------
# F10: DoneSignal thread-safety
# ---------------------------------------------------------------------------


class TestDoneSignalThreadSafety:
    def test_basic_get_set(self) -> None:
        sig = DoneSignal()
        assert sig.called is False
        assert sig.summary == ""
        assert sig.success is False

        sig.called = True
        sig.summary = "All done"
        sig.success = True

        assert sig.called is True
        assert sig.summary == "All done"
        assert sig.success is True

    def test_concurrent_access(self) -> None:
        """Multiple threads can safely read/write DoneSignal attributes."""
        sig = DoneSignal()
        errors: list[Exception] = []

        def writer():
            try:
                for i in range(200):
                    sig.called = True
                    sig.summary = f"summary-{i}"
                    sig.success = i % 2 == 0
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(200):
                    _ = sig.called
                    _ = sig.summary
                    _ = sig.success
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent access errors: {errors}"

    def test_has_lock_attribute(self) -> None:
        """DoneSignal should have an internal threading.Lock."""
        sig = DoneSignal()
        assert hasattr(sig, "_lock")
        assert isinstance(sig._lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# F12: _run_quick_checks substitutes {run_dir}
# ---------------------------------------------------------------------------


class TestQuickCheckRunDirSubstitution:
    def test_run_dir_placeholder_substituted(self, tmp_path: Path) -> None:
        """When check.path uses {run_dir}, it should be expanded."""
        run_dir = RunDir.create(tmp_path, "qc_test")
        log.init(run_dir)

        # Create the expected file inside the run directory
        findings = run_dir.root / "findings.md"
        findings.write_text("some findings")

        checks = [
            QuickCheck(
                path="{run_dir}/findings.md",
                description="Findings file",
                error_message="Missing findings",
            )
        ]
        result = _run_quick_checks(checks)
        assert result is None  # all checks pass

    def test_run_dir_placeholder_fails_when_missing(self, tmp_path: Path) -> None:
        """When the expanded {run_dir}/file doesn't exist, check should fail."""
        run_dir = RunDir.create(tmp_path, "qc_test2")
        log.init(run_dir)

        checks = [
            QuickCheck(
                path="{run_dir}/nonexistent.md",
                description="Missing file",
                error_message="File not found",
            )
        ]
        result = _run_quick_checks(checks)
        assert result is not None
        assert "File not found" in result


# ---------------------------------------------------------------------------
# F15: init_append() validates log file before setting global state
# ---------------------------------------------------------------------------


class TestInitAppendValidation:
    def test_rejects_nonexistent_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.jsonl"
        with pytest.raises(FileNotFoundError):
            log.init_append(missing)

    def test_rejects_invalid_log(self, tmp_path: Path) -> None:
        """A file without run_start should be rejected."""
        bad_log = tmp_path / "runs" / "bad_run" / "run.jsonl"
        bad_log.parent.mkdir(parents=True)
        bad_log.write_text('{"event": "random", "ts": "2024-01-01"}\n')

        with pytest.raises(ValueError, match="Not a valid kodo log"):
            log.init_append(bad_log)

    def test_accepts_valid_log(self, tmp_path: Path) -> None:
        """A properly initialized log file should be accepted."""
        run_dir = RunDir.create(tmp_path, "valid_run")
        log.init(run_dir)
        # Write a run_start + cli_args to make parse_run happy
        log.emit("run_start", goal="test", project_dir=str(tmp_path))
        log.emit("cli_args", team="full")

        # Reset state then try init_append
        log._log_file = None
        log._run_id = None

        result = log.init_append(run_dir.log_file)
        assert result == run_dir.log_file
        assert log.get_log_file() == run_dir.log_file


# ---------------------------------------------------------------------------
# F3: _resolve_executable and _git use shutil.which
# ---------------------------------------------------------------------------


class TestResolveExecutable:
    def test_git_returns_string(self) -> None:
        """_git() should return a non-empty string."""
        result = _git()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_resolve_unknown_returns_name(self) -> None:
        """An unknown executable falls back to returning the name unchanged."""
        result = _resolve_executable("definitely_not_a_real_executable_xyz")
        assert result == "definitely_not_a_real_executable_xyz"

    def test_resolve_git_returns_absolute_path(self) -> None:
        """shutil.which('git') should find git and return an absolute path."""
        import shutil

        if shutil.which("git") is None:
            pytest.skip("git not installed")
        result = _resolve_executable("git")
        assert Path(result).is_absolute()


# ---------------------------------------------------------------------------
# F13: ClaudeSession._run checks thread alive and uses timeout
# ---------------------------------------------------------------------------


class TestClaudeSessionTimeout:
    def test_run_has_query_timeout_constant(self) -> None:
        """ClaudeSession should have a _DEFAULT_QUERY_TIMEOUT class attribute."""
        from kodo.sessions.claude import ClaudeSession

        assert hasattr(ClaudeSession, "_DEFAULT_QUERY_TIMEOUT")
        assert ClaudeSession._DEFAULT_QUERY_TIMEOUT == 7200

    def test_session_timeout_s_overrides_default(self) -> None:
        """session_timeout_s should override the default query timeout."""
        from kodo.sessions.claude import ClaudeSession

        session = ClaudeSession(model="test", use_api_key=True, session_timeout_s=3600)
        try:
            assert session._query_timeout == 3600.0
        finally:
            session.close()

    def test_default_query_timeout_when_no_override(self) -> None:
        """Without session_timeout_s, the default timeout should be used."""
        from kodo.sessions.claude import ClaudeSession

        session = ClaudeSession(model="test", use_api_key=True)
        try:
            assert session._query_timeout == 7200.0
        finally:
            session.close()

    def test_run_raises_on_dead_thread(self) -> None:
        """_run should raise RuntimeError if the background thread is dead."""
        from kodo.sessions.claude import ClaudeSession

        session = ClaudeSession(model="test", use_api_key=True)
        # Kill the event loop and join the thread
        session._loop.call_soon_threadsafe(session._loop.stop)
        session._thread.join(timeout=5)
        assert not session._thread.is_alive()

        async def dummy():
            return 42

        coro = dummy()
        try:
            with pytest.raises(RuntimeError, match="dead"):
                session._run(coro)
        finally:
            coro.close()  # prevent "coroutine was never awaited" warning

        # Cleanup (mark as closed so close() is idempotent)
        session._closed = True
        try:
            session._loop.close()
        except Exception:
            pass
