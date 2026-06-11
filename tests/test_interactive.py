"""Tests for the interactive user input module."""

from __future__ import annotations

import sys
import threading
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from kodo.advisory import AdvisoryQueue
from kodo.cli._interactive import (
    _OutputHold,
    _printable,
    is_interactive,
    run_with_interactive_input,
)


# ---------------------------------------------------------------------------
# is_interactive()
# ---------------------------------------------------------------------------


class TestIsInteractive:
    def test_true_when_tty(self):
        with patch("kodo.cli._interactive.sys", autospec=True) as mock_sys:
            mock_sys.stdin.isatty.return_value = True
            assert is_interactive(json_mode=False) is True

    def test_false_when_not_tty(self):
        with patch("kodo.cli._interactive.sys", autospec=True) as mock_sys:
            mock_sys.stdin.isatty.return_value = False
            assert is_interactive(json_mode=False) is False

    def test_false_when_json_mode(self):
        with patch("kodo.cli._interactive.sys", autospec=True) as mock_sys:
            mock_sys.stdin.isatty.return_value = True
            assert is_interactive(json_mode=True) is False

    def test_false_when_both(self):
        with patch("kodo.cli._interactive.sys", autospec=True) as mock_sys:
            mock_sys.stdin.isatty.return_value = False
            assert is_interactive(json_mode=True) is False


# ---------------------------------------------------------------------------
# Helpers — _printable, _OutputHold
# ---------------------------------------------------------------------------


class TestPrintable:
    def test_plain_text_unchanged(self):
        assert _printable("fix the tests") == "fix the tests"

    def test_control_chars_stripped(self):
        """Enter/tab/control bytes from the raw key chunk must not leak into
        the composer prefill."""
        assert _printable("f\r") == "f"
        assert _printable("\r") == ""
        assert _printable("a\x07b") == "a b"

    def test_pasted_multiline_becomes_one_line(self):
        assert _printable("fix this\nand that\n") == "fix this and that"


class TestInputWithPrefill:
    def test_concat_fallback_preserves_prefill(self):
        """When the readline startup-hook path is unavailable (libedit), the
        prefilled chars and the typed remainder must combine losslessly."""
        from kodo.cli._interactive import _input_with_prefill

        with patch("builtins.input", autospec=True) as mock_input:
            mock_input.return_value = "ocus on tests"
            with patch.dict(sys.modules, {"readline": None}):
                line = _input_with_prefill("  > ", "f")

        assert line == "focus on tests"
        assert mock_input.call_args.args == ("  > f",)


class TestOutputHold:
    def test_buffers_then_releases_in_order(self):
        """Writes during a hold reach the real stream only on release, intact
        and in order — the invariant that protects the composer line."""
        real = sys.stdout
        hold = _OutputHold()
        try:
            print("first")
            print("second")
            assert sys.stdout is hold
        finally:
            hold.release()
        assert sys.stdout is real
        # buffer content was flushed exactly once, in write order
        assert hold._buffer.getvalue() == "first\nsecond\n"

    def test_passthrough_identity(self):
        """fileno/isatty must mirror the real stdout so input() keeps the
        readline path while a hold is installed."""
        hold = _OutputHold()
        try:
            assert hold.isatty() == hold._stdout.isatty()
        finally:
            hold.release()


# ---------------------------------------------------------------------------
# run_with_interactive_input()
# ---------------------------------------------------------------------------


def _make_orchestrator(result=None, error=None):
    """Create a mock orchestrator whose run() blocks on a gate event.

    The gate is released when the console side_effect is exhausted (via
    _keys_then_close), ensuring the bg thread stays alive long enough
    for the watch loop to process inputs.
    """
    orch = MagicMock()
    gate = threading.Event()
    orch._gate = gate
    actual_result = result or MagicMock(name="RunResult")

    def _run(*args, **kwargs):
        gate.wait(timeout=0.05)
        if error:
            raise error
        return actual_result

    orch.run.side_effect = _run
    orch._result = actual_result
    return orch


def _keys_then_close(gate: threading.Event, events: list):
    """side_effect for console.wait_key(): yield events, then close stdin.

    Each event is either a key chunk (str), or an exception to raise.
    When exhausted, releases the orchestrator gate and reports closed stdin.
    """
    idx = 0

    def _side_effect(*args, **kwargs):
        nonlocal idx
        if idx >= len(events):
            gate.set()
            return ""  # stdin closed -> loop breaks
        val = events[idx]
        idx += 1
        if isinstance(val, type) and issubclass(val, BaseException):
            raise val()
        if isinstance(val, BaseException):
            raise val
        return val

    return _side_effect


def _console_mock(MockConsole, gate: threading.Event, keys: list):
    """Configure the autospec'd _Console instance for a test."""
    console = MockConsole.return_value
    console.composing = False  # plain attr, invisible to autospec
    console.wait_key.side_effect = _keys_then_close(gate, keys)
    return console


class TestRunWithInteractiveInput:
    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive._Console", autospec=True)
    def test_user_text_reaches_advisory_queue(self, MockConsole, mock_log):
        """A keypress opens the composer; submitted text is pushed to the
        advisory queue with source='human' and the typed char prefilled."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        console = _console_mock(MockConsole, orch._gate, ["f"])
        console.compose.return_value = "focus on tests"

        run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

        console.compose.assert_called_once_with("f")
        queue.drain()  # flush pending to history
        human_msgs = [a for a in queue.get_history() if a.source == "human"]
        assert any("focus on tests" in a.message for a in human_msgs)

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive._Console", autospec=True)
    def test_empty_input_ignored(self, MockConsole, mock_log):
        """Cancelled / whitespace compositions should not create advisories."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        console = _console_mock(MockConsole, orch._gate, ["\r", "\r"])
        console.compose.side_effect = ["", "  "]

        run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

        queue.drain()
        assert len(queue.get_history()) == 0

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive._Console", autospec=True)
    def test_stop_command_pushes_correction(self, MockConsole, mock_log):
        """/stop pushes a correction-priority advisory."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        console = _console_mock(MockConsole, orch._gate, ["/"])
        console.compose.return_value = "/stop"

        run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

        queue.drain()
        corrections = [a for a in queue.get_history() if a.priority == "correction"]
        assert len(corrections) == 1
        assert "stop" in corrections[0].message.lower()

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive._Console", autospec=True)
    def test_eof_in_composer_stops_listening(self, MockConsole, mock_log):
        """Ctrl+D in the composer ends input handling; the run still completes."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()
        orch._gate.set()  # run can finish immediately

        console = _console_mock(MockConsole, orch._gate, ["x"])
        console.compose.return_value = None  # EOF

        result = run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)
        assert result is orch._result

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive._Console", autospec=True)
    def test_bg_thread_exception_propagates(self, MockConsole, mock_log):
        """Exceptions from orchestrator.run() propagate to the caller."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator(error=RuntimeError("boom"))

        _console_mock(MockConsole, orch._gate, [])

        with pytest.raises(RuntimeError, match="boom"):
            run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive._Console", autospec=True)
    def test_returns_orchestrator_result(self, MockConsole, mock_log):
        """The RunResult from orchestrator.run() is returned."""
        queue = AdvisoryQueue()
        from kodo.orchestrators.types import RunResult

        sentinel = create_autospec(RunResult, instance=True)
        orch = _make_orchestrator(result=sentinel)

        _console_mock(MockConsole, orch._gate, [])

        result = run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)
        assert result is sentinel

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive._Console", autospec=True)
    def test_advisory_queue_passed_to_orchestrator(self, MockConsole, mock_log):
        """The advisory_queue is passed through to orchestrator.run()."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        _console_mock(MockConsole, orch._gate, [])

        run_with_interactive_input(orch, ("goal", "/tmp", {}), {"max_cycles": 5}, queue)

        orch.run.assert_called_once()
        assert orch.run.call_args.kwargs["advisory_queue"] is queue
        assert orch.run.call_args.kwargs["max_cycles"] == 5
        assert orch.run.call_args.args == ("goal", "/tmp", {})

    def test_fallback_without_termios(self):
        """When termios is unavailable, falls back to synchronous run."""
        queue = AdvisoryQueue()
        sentinel = MagicMock(name="RunResult")
        orch = MagicMock()
        orch.run.return_value = sentinel

        with patch("kodo.cli._interactive._HAS_TERMIOS", False):  # noqa: autospec
            result = run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

        assert result is sentinel
        orch.run.assert_called_once()
        assert orch.run.call_args.kwargs["advisory_queue"] is queue

    def test_fallback_when_stdin_not_a_tty(self):
        """When _Console can't take control of stdin (e.g. not a real tty,
        as in this test process), falls back to synchronous run."""
        queue = AdvisoryQueue()
        sentinel = MagicMock(name="RunResult")
        orch = MagicMock()
        orch.run.return_value = sentinel

        result = run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

        assert result is sentinel
        orch.run.assert_called_once()

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive._Console", autospec=True)
    def test_ctrl_c_pushes_stop_advisory(self, MockConsole, mock_log):
        """First Ctrl+C pushes a correction advisory and continues."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        _console_mock(MockConsole, orch._gate, [KeyboardInterrupt])

        run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

        queue.drain()
        corrections = [a for a in queue.get_history() if a.priority == "correction"]
        assert len(corrections) == 1

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive._Console", autospec=True)
    def test_double_ctrl_c_raises(self, MockConsole, mock_log):
        """Second Ctrl+C raises KeyboardInterrupt."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        _console_mock(MockConsole, orch._gate, [KeyboardInterrupt, KeyboardInterrupt])

        with pytest.raises(KeyboardInterrupt):
            run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive._Console", autospec=True)
    def test_terminal_restored_on_exit(self, MockConsole, mock_log):
        """The terminal is always restored to cooked mode, even on errors."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator(error=RuntimeError("boom"))

        console = _console_mock(MockConsole, orch._gate, [])

        with pytest.raises(RuntimeError):
            run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

        console.restore.assert_called()
