"""Test whether the summarizer crashes when the LLM returns null in the response.

Mocks the API (ollama/gemini) to return JSON with null in response/text fields.
Verifies _summarize_ollama and _summarize_gemini handle it without crashing.

Usage:
    uv run python scripts/test_bug_summarizer_none.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kodo.summarizer import _summarize_gemini, _summarize_ollama


def _make_response(body: bytes):
    """Mock HTTP response with read() returning body."""
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def main() -> int:
    errors = []

    # Test ollama: response = null
    with patch("kodo.summarizer.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _make_response(
            json.dumps({"response": None}).encode()
        )
        try:
            result = _summarize_ollama("test-model", "task", "report")
            assert result == "", f"Expected empty string, got {result!r}"
        except Exception as e:
            errors.append(f"ollama: {type(e).__name__}: {e}")

    # Test gemini: text = null
    with patch("kodo.summarizer.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _make_response(
            json.dumps(
                {"candidates": [{"content": {"parts": [{"text": None}]}}]}
            ).encode()
        )
        try:
            result = _summarize_gemini("fake-key", "task", "report")
            assert result == "", f"Expected empty string, got {result!r}"
        except Exception as e:
            errors.append(f"gemini: {type(e).__name__}: {e}")

    # Test gemini: content = null (edge case)
    with patch("kodo.summarizer.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _make_response(
            json.dumps({"candidates": [{"content": None}]}).encode()
        )
        try:
            result = _summarize_gemini("fake-key", "task", "report")
            assert result == "", f"Expected empty string, got {result!r}"
        except Exception as e:
            errors.append(f"gemini content=null: {type(e).__name__}: {e}")

    if errors:
        print("BUG: Summarizer crashed on null response:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    print("PASS: Summarizer handles null response without crashing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
