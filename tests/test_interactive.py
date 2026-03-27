"""Tests for the interactive user input module."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from kodo.advisory import AdvisoryQueue
from kodo.cli._interactive import is_interactive, run_with_interactive_input


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
# run_with_interactive_input()
# ---------------------------------------------------------------------------


def _make_orchestrator(result=None, error=None):
    """Create a mock orchestrator whose run() blocks on a gate event.

    The gate is released when the prompt side_effect raises EOFError (via
    _prompt_then_release), ensuring the bg thread stays alive long enough
    for the prompt loop to process inputs.
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


def _prompt_then_release(gate: threading.Event, inputs: list):
    """Build a side_effect for session.prompt() that releases the gate on EOFError."""
    idx = 0

    def _side_effect(*args, **kwargs):
        nonlocal idx
        if idx >= len(inputs):
            gate.set()
            raise EOFError()
        val = inputs[idx]
        idx += 1
        if isinstance(val, type) and issubclass(val, BaseException):
            raise val()
        if isinstance(val, BaseException):
            raise val
        return val

    return _side_effect


class TestRunWithInteractiveInput:
    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive.patch_stdout", autospec=True)
    @patch("kodo.cli._interactive.PromptSession")  # noqa: autospec
    def test_user_text_reaches_advisory_queue(
        self, MockSession, mock_patch_stdout, mock_log
    ):
        """User input is pushed to the advisory queue with source='human'."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        session = MockSession.return_value
        session.prompt.side_effect = _prompt_then_release(
            orch._gate, ["focus on tests"]
        )

        run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

        queue.drain()  # flush pending to history
        all_advisories = queue.get_history()
        human_msgs = [a for a in all_advisories if a.source == "human"]
        assert any("focus on tests" in a.message for a in human_msgs)

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive.patch_stdout", autospec=True)
    @patch("kodo.cli._interactive.PromptSession")  # noqa: autospec
    def test_empty_input_ignored(self, MockSession, mock_patch_stdout, mock_log):
        """Empty lines should not create advisories."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        session = MockSession.return_value
        session.prompt.side_effect = _prompt_then_release(
            orch._gate, ["", "  "]
        )

        run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

        queue.drain()  # flush pending to history
        all_advisories = queue.get_history()
        assert len(all_advisories) == 0

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive.patch_stdout", autospec=True)
    @patch("kodo.cli._interactive.PromptSession")  # noqa: autospec
    def test_stop_command_pushes_correction(
        self, MockSession, mock_patch_stdout, mock_log
    ):
        """/stop pushes a correction-priority advisory."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        session = MockSession.return_value
        session.prompt.side_effect = _prompt_then_release(
            orch._gate, ["/stop"]
        )

        run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

        queue.drain()  # flush pending to history
        all_advisories = queue.get_history()
        corrections = [a for a in all_advisories if a.priority == "correction"]
        assert len(corrections) == 1
        assert "stop" in corrections[0].message.lower()

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive.patch_stdout", autospec=True)
    @patch("kodo.cli._interactive.PromptSession")  # noqa: autospec
    def test_bg_thread_exception_propagates(
        self, MockSession, mock_patch_stdout, mock_log
    ):
        """Exceptions from orchestrator.run() propagate to the caller."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator(error=RuntimeError("boom"))

        session = MockSession.return_value
        session.prompt.side_effect = _prompt_then_release(orch._gate, [])

        with pytest.raises(RuntimeError, match="boom"):
            run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive.patch_stdout", autospec=True)
    @patch("kodo.cli._interactive.PromptSession")  # noqa: autospec
    def test_returns_orchestrator_result(
        self, MockSession, mock_patch_stdout, mock_log
    ):
        """The RunResult from orchestrator.run() is returned."""
        queue = AdvisoryQueue()
        from kodo.orchestrators.types import RunResult

        sentinel = create_autospec(RunResult, instance=True)
        orch = _make_orchestrator(result=sentinel)

        session = MockSession.return_value
        session.prompt.side_effect = _prompt_then_release(orch._gate, [])

        result = run_with_interactive_input(
            orch, ("goal", "/tmp", {}), {}, queue
        )
        assert result is sentinel

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive.patch_stdout", autospec=True)
    @patch("kodo.cli._interactive.PromptSession")  # noqa: autospec
    def test_advisory_queue_passed_to_orchestrator(
        self, MockSession, mock_patch_stdout, mock_log
    ):
        """The advisory_queue is passed through to orchestrator.run()."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        session = MockSession.return_value
        session.prompt.side_effect = _prompt_then_release(orch._gate, [])

        run_with_interactive_input(
            orch, ("goal", "/tmp", {}), {"max_cycles": 5}, queue
        )

        orch.run.assert_called_once()
        assert orch.run.call_args.kwargs["advisory_queue"] is queue
        assert orch.run.call_args.kwargs["max_cycles"] == 5
        assert orch.run.call_args.args == ("goal", "/tmp", {})

    def test_fallback_without_prompt_toolkit(self):
        """When _HAS_PROMPT_TOOLKIT is False, falls back to synchronous run."""
        queue = AdvisoryQueue()
        sentinel = MagicMock(name="RunResult")
        orch = MagicMock()
        orch.run.return_value = sentinel

        with patch(  # noqa: autospec
            "kodo.cli._interactive._HAS_PROMPT_TOOLKIT", False
        ):
            result = run_with_interactive_input(
                orch, ("goal", "/tmp", {}), {}, queue
            )

        assert result is sentinel
        orch.run.assert_called_once()
        assert orch.run.call_args.kwargs["advisory_queue"] is queue

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive.patch_stdout", autospec=True)
    @patch("kodo.cli._interactive.PromptSession")  # noqa: autospec
    def test_ctrl_c_pushes_stop_advisory(
        self, MockSession, mock_patch_stdout, mock_log
    ):
        """First Ctrl+C pushes a correction advisory and continues."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        session = MockSession.return_value
        session.prompt.side_effect = _prompt_then_release(
            orch._gate, [KeyboardInterrupt]
        )

        run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)

        queue.drain()  # flush pending to history
        all_advisories = queue.get_history()
        corrections = [a for a in all_advisories if a.priority == "correction"]
        assert len(corrections) == 1

    @patch("kodo.cli._interactive.log", autospec=True)
    @patch("kodo.cli._interactive.patch_stdout", autospec=True)
    @patch("kodo.cli._interactive.PromptSession")  # noqa: autospec
    def test_double_ctrl_c_raises(
        self, MockSession, mock_patch_stdout, mock_log
    ):
        """Second Ctrl+C raises KeyboardInterrupt."""
        queue = AdvisoryQueue()
        orch = _make_orchestrator()

        session = MockSession.return_value
        session.prompt.side_effect = _prompt_then_release(
            orch._gate, [KeyboardInterrupt, KeyboardInterrupt]
        )

        with pytest.raises(KeyboardInterrupt):
            run_with_interactive_input(orch, ("goal", "/tmp", {}), {}, queue)
