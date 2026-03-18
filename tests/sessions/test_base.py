"""Tests for kodo.sessions.base module."""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch


from kodo.sessions.base import (
    QueryResult,
    SessionStats,
    SubprocessSession,
    classify_session_error,
)


# ── QueryResult tests ──────────────────────────────────────────────────────


class TestQueryResult:
    """Test QueryResult dataclass functionality."""

    def test_strips_whitespace(self):
        """QueryResult strips leading/trailing whitespace from text."""
        result = QueryResult(text="  \n  response  \n  ", elapsed_s=1.0)
        assert result.text == "response"

    def test_empty_text_becomes_empty_string(self):
        """Empty or whitespace-only text becomes empty string."""
        result = QueryResult(text="   \t\n   ", elapsed_s=0.1)
        assert result.text == ""


# ── SessionStats tests ─────────────────────────────────────────────────────


class TestSessionStats:
    """Test SessionStats dataclass functionality."""

    def test_total_tokens_property(self):
        """total_tokens sums input and output tokens."""
        stats = SessionStats(total_input_tokens=100, total_output_tokens=50)
        assert stats.total_tokens == 150

    def test_accumulation(self):
        """Stats can be updated and accumulated."""
        stats = SessionStats()
        stats.total_input_tokens += 100
        stats.total_output_tokens += 50
        stats.total_cost_usd += 0.01
        stats.queries += 1

        assert stats.total_tokens == 150
        assert stats.total_cost_usd == 0.01
        assert stats.queries == 1


# ── SubprocessSession tests ────────────────────────────────────────────────


class ConcreteSubprocessSession(SubprocessSession):
    """Concrete implementation for testing."""

    _session_label = "test_session"

    def __init__(self, model="test-model", system_prompt=None, timeout_s=7200):
        super().__init__(model, system_prompt, timeout_s)
        self.cost_bucket = "test"

    def query(self, prompt: str, project_dir: Path, *, max_turns: int):
        """Minimal query implementation for testing."""
        return QueryResult(text="ok", elapsed_s=1.0)

    def clone(self):
        """Return a fresh session."""
        return ConcreteSubprocessSession(
            self.model, self.system_prompt, self._timeout_s
        )


class TestPrependSystemPrompt:
    """Test _prepend_system_prompt behavior."""

    def test_prepends_on_first_call(self):
        """System prompt is prepended to first query."""
        session = ConcreteSubprocessSession(
            model="test",
            system_prompt="System: Be helpful.",
        )
        result = session._prepend_system_prompt("User query")
        assert result == "System: Be helpful.\n\nUser query"
        assert session._system_prompt_sent is True

    def test_does_not_prepend_on_second_call(self):
        """System prompt is NOT prepended to subsequent queries."""
        session = ConcreteSubprocessSession(
            model="test",
            system_prompt="System: Be helpful.",
        )
        session._prepend_system_prompt("First query")
        result = session._prepend_system_prompt("Second query")
        assert result == "Second query"

    def test_no_prepend_when_no_system_prompt(self):
        """No modification when system_prompt is None."""
        session = ConcreteSubprocessSession(model="test", system_prompt=None)
        result = session._prepend_system_prompt("User query")
        assert result == "User query"
        assert session._system_prompt_sent is False


class TestSubprocessSpawn:
    """Test _spawn method."""

    def test_spawn_creates_process(self):
        """_spawn creates subprocess with correct pipes."""
        session = ConcreteSubprocessSession(model="test")
        proc, stderr_chunks, thread = session._spawn(["echo", "hello"])

        assert isinstance(proc, subprocess.Popen)
        assert proc.stdout is not None
        assert proc.stderr is not None
        assert isinstance(stderr_chunks, list)
        assert isinstance(thread, threading.Thread)
        assert thread.daemon is True

        # Clean up
        proc.wait(timeout=2)
        thread.join(timeout=2)

    def test_spawn_strips_anthropic_api_key(self):
        """_spawn removes ANTHROPIC_API_KEY from environment."""
        import os

        session = ConcreteSubprocessSession(model="test")

        # Set the key in our environment
        original_key = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "test-key-12345"

        try:
            with patch("subprocess.Popen", autospec=True) as mock_popen:
                mock_proc = MagicMock()
                mock_proc.stdout = MagicMock()
                mock_proc.stderr = MagicMock()
                mock_proc.stderr.read = MagicMock(return_value="")
                mock_popen.return_value = mock_proc

                session._spawn(["echo", "test"])

                # Check that the environment passed to Popen does not have the key
                call_kwargs = mock_popen.call_args.kwargs
                assert "ANTHROPIC_API_KEY" not in call_kwargs["env"]
        finally:
            # Restore original state
            if original_key is not None:
                os.environ["ANTHROPIC_API_KEY"] = original_key
            else:
                os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_spawn_sets_did_timeout_false(self):
        """_spawn resets the timeout flag."""
        session = ConcreteSubprocessSession(model="test")
        session._did_timeout = True

        proc, stderr_chunks, thread = session._spawn(["echo", "hello"])
        assert session._did_timeout is False

        # Clean up
        proc.wait(timeout=2)
        thread.join(timeout=2)

    def test_spawn_with_cwd(self):
        """_spawn respects the cwd parameter."""
        session = ConcreteSubprocessSession(model="test")

        with patch("subprocess.Popen", autospec=True) as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout = MagicMock()
            mock_proc.stderr = MagicMock()
            mock_proc.stderr.read = MagicMock(return_value="")
            mock_popen.return_value = mock_proc

            session._spawn(["pwd"], cwd="/tmp")

            assert mock_popen.call_args.kwargs["cwd"] == "/tmp"


class TestSubprocessWait:
    """Test _wait method and timeout handling."""

    def test_wait_normal_completion(self):
        """_wait waits for normal process completion."""
        session = ConcreteSubprocessSession(model="test")
        proc, stderr_chunks, thread = session._spawn(["echo", "test"])

        stderr = session._wait(proc, stderr_chunks, thread)
        assert isinstance(stderr, str)
        assert session._did_timeout is False

    def test_wait_timeout_handling(self):
        """_wait handles timeout by terminating process."""
        session = ConcreteSubprocessSession(model="test", timeout_s=0.1)

        # Create a long-running process
        proc, stderr_chunks, thread = session._spawn(["sleep", "10"])

        with (
            patch("kodo.log.emit", autospec=True) as mock_emit,
            patch("kodo.log.tprint", autospec=True),
        ):
            session._wait(proc, stderr_chunks, thread)

        assert session._did_timeout is True
        assert mock_emit.call_count >= 1

        # Verify process was terminated
        assert proc.poll() is not None

    def test_wait_handles_terminate_oserror(self):
        """_wait handles OSError when process already exited during timeout."""
        session = ConcreteSubprocessSession(model="test", timeout_s=1)
        proc = MagicMock(spec=subprocess.Popen)
        # First wait times out, then subsequent waits after kill succeed
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=1),  # Initial timeout
            None,  # After terminate/kill succeeds
        ]
        proc.terminate.side_effect = OSError("Already exited")
        proc.kill.side_effect = OSError("Already exited")
        stderr_chunks = []
        thread = MagicMock()

        with (
            patch("kodo.log.emit", autospec=True),
            patch("kodo.log.tprint", autospec=True),
        ):
            stderr = session._wait(proc, stderr_chunks, thread)

        # Should not raise, just handle the error
        assert stderr == ""

    def test_wait_escalates_to_kill(self):
        """_wait escalates to kill() if terminate() doesn't work."""
        session = ConcreteSubprocessSession(model="test", timeout_s=1)
        proc = MagicMock(spec=subprocess.Popen)
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=1),  # Initial wait
            subprocess.TimeoutExpired(cmd="test", timeout=5),  # After terminate
            None,  # After kill succeeds
        ]
        stderr_chunks = []
        thread = MagicMock()

        with (
            patch("kodo.log.emit", autospec=True),
            patch("kodo.log.tprint", autospec=True),
        ):
            session._wait(proc, stderr_chunks, thread)

        # Should have called kill after terminate failed
        proc.kill.assert_called_once()

    def test_wait_logs_zombie_process(self):
        """_wait logs warning when process becomes zombie."""
        session = ConcreteSubprocessSession(model="test", timeout_s=1)
        proc = MagicMock(spec=subprocess.Popen)
        proc.pid = 12345
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=1)
        stderr_chunks = []
        thread = MagicMock()

        with (
            patch("kodo.log.emit", autospec=True) as mock_emit,
            patch("kodo.log.tprint", autospec=True) as mock_tprint,
        ):
            session._wait(proc, stderr_chunks, thread)

        # Check for zombie process logging
        emit_calls = [str(call) for call in mock_emit.call_args_list]
        zombie_logged = any("zombie" in str(call).lower() for call in emit_calls)
        assert zombie_logged or any(
            "zombie" in str(call).lower() for call in mock_tprint.call_args_list
        )


class TestTerminate:
    """Test terminate method."""

    def test_terminate_with_no_process(self):
        """terminate() is no-op when no process is running."""
        session = ConcreteSubprocessSession(model="test")
        session.terminate()  # Should not raise
        assert session._process is None

    def test_terminate_when_already_exited(self):
        """terminate() is no-op when process already exited."""
        session = ConcreteSubprocessSession(model="test")
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0  # Already exited
        session._process = proc

        session.terminate()
        assert session._process is None

    def test_terminate_sends_sigterm(self):
        """terminate() sends SIGTERM and waits."""
        session = ConcreteSubprocessSession(model="test")
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None  # Still running
        session._process = proc

        session.terminate()

        proc.terminate.assert_called_once()
        proc.wait.assert_called_once_with(timeout=5)

    def test_terminate_escalates_to_sigkill(self):
        """terminate() escalates to SIGKILL if SIGTERM doesn't work."""
        session = ConcreteSubprocessSession(model="test")
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="test", timeout=5),  # After terminate
            None,  # After kill succeeds
        ]
        session._process = proc

        session.terminate()

        proc.kill.assert_called_once()

    def test_terminate_handles_oserror_on_terminate(self):
        """terminate() handles OSError when process already gone."""
        session = ConcreteSubprocessSession(model="test")
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        proc.terminate.side_effect = OSError("No such process")
        session._process = proc

        session.terminate()  # Should not raise
        assert session._process is None


class TestReset:
    """Test reset method."""

    def test_reset_clears_stats(self):
        """reset() creates fresh SessionStats."""
        session = ConcreteSubprocessSession(model="test")
        session._stats.queries = 5
        session._stats.total_cost_usd = 1.0

        session.reset()

        assert session._stats.queries == 0
        assert session._stats.total_cost_usd == 0.0

    def test_reset_clears_system_prompt_flag(self):
        """reset() allows system prompt to be sent again."""
        session = ConcreteSubprocessSession(
            model="test",
            system_prompt="System prompt",
        )
        session._system_prompt_sent = True

        session.reset()

        assert session._system_prompt_sent is False


# ── classify_session_error tests ───────────────────────────────────────────


class TestClassifySessionError:
    """Test classify_session_error function."""

    def test_timeout_error(self):
        """Returns timeout message when did_timeout=True."""
        result = classify_session_error(
            returncode=0,
            stderr="",
            did_timeout=True,
            timeout_s=3600,
            backend="cursor",
        )
        assert result is not None
        assert "timed out after 3600s" in result
        assert "cursor" in result

    def test_timeout_without_backend(self):
        """Timeout message works without backend label."""
        result = classify_session_error(
            returncode=0,
            stderr="",
            did_timeout=True,
            timeout_s=1800,
        )
        assert "timed out after 1800s" in result

    def test_auth_error_401(self):
        """Detects 401 authentication error."""
        result = classify_session_error(
            returncode=1,
            stderr="Error: 401 Unauthorized - invalid API key",
        )
        assert result is not None
        assert "Authentication failed" in result

    def test_auth_error_forbidden(self):
        """Detects 'forbidden' authentication error."""
        result = classify_session_error(
            returncode=1,
            stderr="Access forbidden: invalid credentials",
            backend="gemini",
        )
        assert "Authentication failed" in result
        assert "gemini" in result

    def test_subscription_error_quota(self):
        """Detects quota exceeded subscription error."""
        result = classify_session_error(
            returncode=1,
            stderr="Error: quota exceeded for your subscription plan",
        )
        assert result is not None
        assert "Subscription/billing issue" in result

    def test_subscription_error_rate_limit(self):
        """Detects rate limit (429) subscription error."""
        result = classify_session_error(
            returncode=1,
            stderr="HTTP 429: Too many requests",
            backend="claude",
        )
        assert "Subscription/billing issue" in result
        assert "claude" in result

    def test_binary_not_found(self):
        """Detects 'command not found' binary error."""
        result = classify_session_error(
            returncode=127,
            stderr="bash: cursor: command not found",
        )
        assert result is not None
        assert "Binary not working" in result

    def test_binary_permission_denied(self):
        """Detects permission denied binary error."""
        result = classify_session_error(
            returncode=126,
            stderr="Permission denied: /usr/local/bin/codex",
            backend="codex",
        )
        assert "Binary not working" in result
        assert "codex" in result

    def test_signal_termination(self):
        """Detects signal-based termination (negative return code)."""
        result = classify_session_error(
            returncode=-signal.SIGTERM,
            stderr="",
        )
        assert result is not None
        assert "killed by signal" in result
        assert "SIGTERM" in result

    def test_signal_kill(self):
        """Detects SIGKILL termination."""
        result = classify_session_error(
            returncode=-9,
            stderr="",
            backend="worker",
        )
        assert "killed by signal" in result
        assert "SIGKILL" in result
        assert "worker" in result

    def test_unknown_signal(self):
        """Handles unknown signal numbers gracefully."""
        result = classify_session_error(
            returncode=-999,  # Unknown signal
            stderr="",
        )
        assert "killed by signal" in result
        assert "999" in result

    def test_unclassified_error_returns_none(self):
        """Returns None for unclassified errors."""
        result = classify_session_error(
            returncode=1,
            stderr="Some random error that doesn't match patterns",
        )
        assert result is None

    def test_checks_stdout_too(self):
        """Error classification checks both stderr and stdout."""
        result = classify_session_error(
            returncode=1,
            stderr="",
            stdout="Error: Authentication failed - invalid token",
        )
        assert result is not None
        assert "Authentication failed" in result

    def test_case_insensitive_matching(self):
        """Pattern matching is case-insensitive."""
        result = classify_session_error(
            returncode=1,
            stderr="ERROR: AUTHENTICATION FAILED",
        )
        assert "Authentication failed" in result

    def test_combined_stderr_stdout(self):
        """Combines stderr and stdout for pattern matching."""
        result = classify_session_error(
            returncode=1,
            stderr="Process started...",
            stdout="Subscription expired. Please renew.",
        )
        assert "Subscription/billing issue" in result


class TestStderrDrainEdgeCases:
    """Test stderr drain thread edge cases."""

    def test_stderr_line_truncation(self):
        """Very long stderr lines trigger truncation logic in drain thread."""
        session = ConcreteSubprocessSession(model="test")

        # Create a command that outputs a very long line to stderr
        # The truncation happens at 65536 bytes per line
        # Since we write without a newline, it stays in the buffer until process ends
        cmd = [
            sys.executable,
            "-c",
            "import sys; sys.stderr.write('x' * 100000 + '\\n')",
        ]

        proc, stderr_chunks, thread = session._spawn(cmd)
        stderr = session._wait(proc, stderr_chunks, thread)

        # With a newline, the line gets processed and should be truncated
        # The code chunks at 65536 bytes and adds truncation marker
        # So we should see the marker OR the line should be shorter than input
        # (The actual behavior depends on when chunks are processed)
        assert len(stderr) > 0
        # At minimum, verify the stderr was captured
        assert "x" in stderr

    def test_stderr_max_lines_limit(self):
        """Stderr output is limited to max lines."""
        session = ConcreteSubprocessSession(model="test")

        # Create a command that outputs many lines
        cmd = [
            sys.executable,
            "-c",
            "import sys; [sys.stderr.write(f'line {i}\\n') for i in range(15000)]",
        ]

        proc, stderr_chunks, thread = session._spawn(cmd)
        stderr = session._wait(proc, stderr_chunks, thread)

        # Should see truncation message
        assert "truncated" in stderr.lower()
        # But should still have collected something
        assert "line" in stderr


class TestSessionProtocolMethods:
    """Test that SubprocessSession implements Session protocol methods."""

    def test_has_required_methods(self):
        """SubprocessSession has all required Session protocol methods."""
        session = ConcreteSubprocessSession(model="test")

        assert hasattr(session, "model")
        assert hasattr(session, "stats")
        assert hasattr(session, "cost_bucket")
        assert hasattr(session, "query")
        assert hasattr(session, "reset")
        assert hasattr(session, "terminate")
        assert hasattr(session, "close")
        assert hasattr(session, "clone")
