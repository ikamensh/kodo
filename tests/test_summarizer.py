"""Tests for the Summarizer."""

from __future__ import annotations

from kodo.summarizer import Summarizer


def _make_summarizer():
    """Create a summarizer with truncation backend (no external deps)."""
    s = Summarizer()
    s._backend = "truncate"
    return s


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


# ── Resilience tests (relocated from test_error_paths.py) ────────────────

import threading


def test_concurrent_get_accumulated_summary() -> None:
    """Multiple threads draining concurrently must not crash."""
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

    assert not errors
    got_content = any("[w1]" in r or "[w2]" in r for r in results)
    assert got_content


def test_do_summarize_swallows_backend_exceptions() -> None:
    """Ollama backend failure is swallowed silently."""
    s = _make_summarizer()
    s._backend = "ollama"
    s._backend_param = "fake-model"

    s.summarize("worker", "task", "report")
    result = s.get_accumulated_summary()
    assert "[worker]" not in result


def test_empty_report_produces_empty_summary() -> None:
    """Whitespace-only report is not appended to summaries."""
    s = _make_summarizer()
    s.summarize("worker", "task", "   \n\n   ")
    result = s.get_accumulated_summary()
    assert result == ""


