"""Tests for the Summarizer."""

from __future__ import annotations

from unittest.mock import patch

from kodo.summarizer import Summarizer


def _make_summarizer():
    """Create a summarizer with truncation backend (no external deps)."""
    with (
        patch("kodo.summarizer._probe_ollama", return_value=None),
        patch("kodo.summarizer._probe_gemini", return_value=None),
    ):
        return Summarizer()


def test_accumulated_summary_collects_truncation() -> None:
    """With truncation backend, summaries are first non-empty lines."""
    s = _make_summarizer()
    s.summarize("worker", "build X", "Created file X.py with feature X")
    s.summarize("tester", "test X", "ALL CHECKS PASS")
    # get_accumulated_summary drains pending tasks
    acc = s.get_accumulated_summary()
    assert "[worker]" in acc
    assert "[tester]" in acc


def test_get_accumulated_summary_waits_for_pending(tmp_path) -> None:
    """BUG FIX: get_accumulated_summary must drain pending tasks first."""
    import time

    s = _make_summarizer()

    # Patch _summarize_truncate to add a small delay
    original = s._do_summarize

    def slow_summarize(agent_name, task, report):
        time.sleep(0.05)
        original(agent_name, task, report)

    s._do_summarize = slow_summarize
    s.summarize("worker", "task", "result text here")

    # Without the fix, this could return "" because the task is still in-flight
    acc = s.get_accumulated_summary()
    assert "[worker]" in acc


def test_clear_resets_summaries() -> None:
    """BUG FIX: summaries should be clearable between cycles."""
    s = _make_summarizer()
    s.summarize("worker", "task", "did stuff")
    s.get_accumulated_summary()  # drain
    assert s.get_accumulated_summary() != ""  # still there

    s.clear()
    assert s.get_accumulated_summary() == ""


def test_summarize_after_get_accumulated_summary() -> None:
    """get_accumulated_summary restarts the executor so new work is accepted."""
    s = _make_summarizer()
    s.summarize("worker", "task1", "first result")
    s.get_accumulated_summary()  # drains and restarts executor

    s.summarize("tester", "task2", "second result")
    acc = s.get_accumulated_summary()
    assert "[tester]" in acc


