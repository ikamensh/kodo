"""Tests for the Summarizer."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

from kodo.summarizer import (
    Summarizer,
    _probe_gemini,
    _probe_ollama,
    _summarize_gemini,
    _summarize_truncate,
)


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


# ── Probe functions ──────────────────────────────────────────────────────


class TestProbeOllama:
    def test_returns_model_when_available(self):
        response = json.dumps({"models": [{"name": "llama3.2:1b"}]}).encode()

        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.read.return_value = response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", autospec=True, return_value=mock_resp):
            result = _probe_ollama()
        assert result == "llama3.2:1b"

    def test_returns_none_on_connection_error(self):
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            autospec=True,
            side_effect=urllib.error.URLError("refused"),
        ):
            assert _probe_ollama() is None

    def test_returns_none_on_empty_models(self):
        response = json.dumps({"models": []}).encode()

        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.read.return_value = response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", autospec=True, return_value=mock_resp):
            assert _probe_ollama() is None

    def test_returns_none_on_timeout(self):
        with patch("urllib.request.urlopen", autospec=True, side_effect=TimeoutError):
            assert _probe_ollama() is None


class TestProbeGemini:
    def test_returns_gemini_key(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=False):
            assert _probe_gemini() == "test-key"

    def test_returns_google_key_fallback(self):
        env = {"GOOGLE_API_KEY": "goog-key"}
        with patch.dict("os.environ", env, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            assert _probe_gemini() == "goog-key"

    def test_returns_none_when_no_key(self):
        with patch.dict("os.environ", {}, clear=True):
            assert _probe_gemini() is None


# ── Backend selection (_ensure_backend) ──────────────────────────────────


class TestEnsureBackend:
    def test_ollama_preferred_over_gemini(self):
        s = Summarizer()
        with (
            patch(
                "kodo.summarizer._probe_ollama", autospec=True, return_value="llama3"
            ),
            patch(
                "kodo.summarizer._probe_gemini",
                autospec=True,
                return_value="gemini-key",
            ),
        ):
            with s._lock:
                s._ensure_backend()
        assert s._backend == "ollama"
        assert s._backend_param == "llama3"

    def test_gemini_when_no_ollama(self):
        s = Summarizer()
        with (
            patch("kodo.summarizer._probe_ollama", autospec=True, return_value=None),
            patch(
                "kodo.summarizer._probe_gemini", autospec=True, return_value="my-key"
            ),
        ):
            with s._lock:
                s._ensure_backend()
        assert s._backend == "gemini"
        assert s._backend_param == "my-key"

    def test_truncate_when_nothing_available(self):
        s = Summarizer()
        with (
            patch("kodo.summarizer._probe_ollama", autospec=True, return_value=None),
            patch("kodo.summarizer._probe_gemini", autospec=True, return_value=None),
        ):
            with s._lock:
                s._ensure_backend()
        assert s._backend == "truncate"

    def test_only_probes_once(self):
        s = Summarizer()
        with (
            patch(
                "kodo.summarizer._probe_ollama", autospec=True, return_value=None
            ) as mock_ollama,
            patch("kodo.summarizer._probe_gemini", autospec=True, return_value=None),
        ):
            with s._lock:
                s._ensure_backend()
                s._ensure_backend()
        assert mock_ollama.call_count == 1


# ── Gemini backend with mocked HTTP ──────────────────────────────────────


class TestSummarizeGemini:
    def test_parses_successful_response(self):
        response = json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "Summary of work done"}]}}]}
        ).encode()

        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.read.return_value = response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", autospec=True, return_value=mock_resp):
            result = _summarize_gemini("fake-key", "build feature", "Created X.py")
        assert result == "Summary of work done"

    def test_empty_candidates_returns_empty(self):
        response = json.dumps({"candidates": []}).encode()

        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.read.return_value = response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", autospec=True, return_value=mock_resp):
            result = _summarize_gemini("fake-key", "task", "report")
        assert result == ""


# ── Truncation helper ────────────────────────────────────────────────────


class TestSummarizeTruncate:
    def test_returns_first_nonempty_line(self):
        assert _summarize_truncate("\n\n  Hello world\nSecond line") == "Hello world"

    def test_truncates_at_120_chars(self):
        long_line = "x" * 200
        assert len(_summarize_truncate(long_line)) == 120

    def test_empty_string(self):
        assert _summarize_truncate("") == ""

    def test_whitespace_only(self):
        assert _summarize_truncate("   \n\n  ") == ""


# ── Shutdown lifecycle ───────────────────────────────────────────────────


class TestShutdown:
    def test_shutdown_drains_and_prevents_new_work(self):
        s = _make_summarizer()
        s.summarize("worker", "task", "result")
        s.shutdown()
        # After shutdown, summarize is a no-op
        s.summarize("worker", "task2", "more work")
        result = s.get_accumulated_summary()
        # Only the first summary should be there
        assert "task2" not in result

    def test_shutdown_idempotent(self):
        s = _make_summarizer()
        s.shutdown()
        s.shutdown()  # no crash

    def test_summarize_after_shutdown_is_noop(self):
        s = _make_summarizer()
        s.shutdown()
        s.summarize("worker", "task", "report")
        result = s.get_accumulated_summary()
        assert result == ""
