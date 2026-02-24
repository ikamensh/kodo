"""Async LLM summarizer for agent task results.

Fire-and-forget summaries using a background thread. Tries ollama first,
then Gemini, then falls back to simple truncation.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

from kodo import log

_PROMPT_TEMPLATE = (
    "Summarize in 1 sentence what was accomplished. "
    "Be specific (mention file names, features, decisions). No preamble.\n\n"
    "Task: {task}\n"
    "Result: {report}"
)


def _probe_ollama() -> str | None:
    """Return the first available ollama model name, or None."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
        models = data.get("models", [])
        if models:
            return models[0].get("name") or models[0].get("model")
    except Exception:
        pass
    return None


def _probe_gemini() -> str | None:
    """Return the Gemini API key if set, else None."""
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None


def _summarize_ollama(model: str, task: str, report: str) -> str:
    prompt = _PROMPT_TEMPLATE.format(task=task[:200], report=report[:2000])
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
        }
    ).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return (data.get("response") or "").strip()


def _summarize_gemini(api_key: str, task: str, report: str) -> str:
    prompt = _PROMPT_TEMPLATE.format(task=task[:200], report=report[:2000])
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash-lite:generateContent?key={api_key}"
    )
    payload = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    candidates = data.get("candidates", [])
    if candidates:
        content = candidates[0].get("content") or {}
        parts = content.get("parts", [])
        if parts:
            return (parts[0].get("text") or "").strip()
    return ""


def _summarize_truncate(report: str) -> str:
    for line in report.splitlines():
        line = line.strip()
        if line:
            return line[:120]
    return ""


class Summarizer:
    """Fire-and-forget task summarizer using a background thread."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._backend: str = "truncate"
        self._backend_param: str | None = None
        self._summaries: list[str] = []
        self._lock = threading.Lock()

        # Probe backends once at init
        ollama_model = _probe_ollama()
        if ollama_model:
            self._backend = "ollama"
            self._backend_param = ollama_model
            log.tprint(f"🗜️  [summarizer] using ollama ({ollama_model})")
        else:
            gemini_key = _probe_gemini()
            if gemini_key:
                self._backend = "gemini"
                self._backend_param = gemini_key
                log.tprint("🗜️  [summarizer] using gemini")
            else:
                log.tprint(
                    "🗜️  [summarizer] using truncation (no LLM backend available)"
                )

    def summarize(self, agent_name: str, task: str, report: str) -> None:
        """Submit a summary job (fire-and-forget)."""
        self._executor.submit(self._do_summarize, agent_name, task, report)

    def _do_summarize(self, agent_name: str, task: str, report: str) -> None:
        task = task or ""
        report = report or ""
        try:
            if self._backend == "ollama":
                text = _summarize_ollama(self._backend_param, task, report)
            elif self._backend == "gemini":
                text = _summarize_gemini(self._backend_param, task, report)
            else:
                text = _summarize_truncate(report)

            if text:
                with self._lock:
                    self._summaries.append(f"[{agent_name}] {text}")
                log.tprint(f"[{agent_name}] summary: {text}")
        except Exception:
            # Never crash — summaries are best-effort
            pass

    def get_accumulated_summary(self) -> str:
        """Drain pending work and return all summaries collected."""
        # Wait for in-flight tasks to finish before reading
        self._executor.shutdown(wait=True)
        # Restart executor for future use
        self._executor = ThreadPoolExecutor(max_workers=1)
        with self._lock:
            return "\n".join(self._summaries)

    def clear(self) -> None:
        """Clear accumulated summaries (call between cycles)."""
        with self._lock:
            self._summaries.clear()

    def shutdown(self, wait: bool = True) -> None:
        """Drain pending work."""
        self._executor.shutdown(wait=wait)
