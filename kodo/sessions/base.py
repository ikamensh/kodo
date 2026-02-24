"""Session protocol and shared types."""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class QueryResult:
    text: str
    elapsed_s: float
    turns: int | None = None
    cost_usd: float | None = None
    is_error: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    usage_raw: dict | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.text = self.text.strip()


@dataclass
class SessionStats:
    """Cumulative stats for the current session."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    queries: int = 0

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


class Session(Protocol):
    @property
    def stats(self) -> SessionStats: ...

    @property
    def cost_bucket(self) -> str:
        """Billing bucket: 'api', 'claude_subscription', or 'cursor_subscription'."""
        ...

    @property
    def session_id(self) -> str | None:
        """Backend session ID for resume support. None if not yet established."""
        return None

    def query(
        self, prompt: str, project_dir: Path, *, max_turns: int
    ) -> QueryResult: ...

    def reset(self) -> None: ...

    def terminate(self) -> None:
        """Kill the underlying process/connection.

        Called on timeout to forcefully stop a running query before ``reset()``.
        Implementations should be safe to call even when no query is in flight.
        """
        ...


class SubprocessSession:
    """Base for subprocess-backed sessions (Cursor, Codex, Gemini CLI).

    Provides shared init, stats, system-prompt prepend, subprocess spawn/wait,
    and reset logic.  Subclasses keep their own ``query()`` and override
    ``reset()`` (calling ``super().reset()``) to clear session-specific state.
    """

    _session_label: str  # set by each subclass

    def __init__(self, model: str, system_prompt: str | None = None):
        self.model = model
        self.system_prompt = system_prompt
        self._stats = SessionStats()
        self._system_prompt_sent = False
        self._process: subprocess.Popen | None = None

    @property
    def stats(self) -> SessionStats:
        return self._stats

    def _prepend_system_prompt(self, prompt: str) -> str:
        """Prepend system prompt to the first query, then set flag."""
        if self.system_prompt and not self._system_prompt_sent:
            prompt = f"{self.system_prompt}\n\n{prompt}"
            self._system_prompt_sent = True
        return prompt

    def _spawn(
        self, cmd: list[str], *, cwd: str | None = None
    ) -> tuple[subprocess.Popen, list[str], threading.Thread]:
        """Spawn subprocess with a stderr-drain thread.

        Returns ``(proc, stderr_chunks, thread)``.
        """
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )
        self._process = proc
        stderr_chunks: list[str] = []
        _STDERR_MAX_LINES = 10_000  # cap to avoid unbounded memory

        def _drain() -> None:
            for i, line in enumerate(proc.stderr):
                if i < _STDERR_MAX_LINES:
                    stderr_chunks.append(line)
                elif i == _STDERR_MAX_LINES:
                    stderr_chunks.append("\n[... stderr truncated ...]\n")

        thread = threading.Thread(target=_drain, daemon=True)
        thread.start()
        return proc, stderr_chunks, thread

    def _wait(
        self,
        proc: subprocess.Popen,
        stderr_chunks: list[str],
        thread: threading.Thread,
    ) -> str:
        """Wait for process and join drain thread.  Returns stderr text."""
        proc.wait()
        # Allow up to 30s for drain to finish; process has exited so stderr
        # should close soon. Increase from 5s to avoid truncating long output.
        thread.join(timeout=30)
        return "".join(stderr_chunks)

    def terminate(self) -> None:
        """Kill the running subprocess.

        Safe to call when no process is active (no-op).  Sends SIGTERM
        first; escalates to SIGKILL if the process doesn't exit within 5 s.
        """
        proc = self._process
        if proc is None or proc.poll() is not None:
            return  # nothing running
        try:
            proc.terminate()
        except OSError:
            pass  # already exited
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        self._process = None

    def reset(self) -> None:
        """Reset shared state.  Subclasses should log, clear their own state,
        then call ``super().reset()``."""
        self._stats = SessionStats()
        self._system_prompt_sent = False


# ---------------------------------------------------------------------------
# Shared error classification for subprocess-based sessions
# ---------------------------------------------------------------------------

_AUTH_PATTERNS = re.compile(
    r"unauthori[sz]ed|authentication failed|invalid.{0,20}(api.?key|token|credential)"
    r"|401\b|403\b|forbidden|access denied|not authenticated",
    re.IGNORECASE,
)

_SUBSCRIPTION_PATTERNS = re.compile(
    r"subscription|billing|payment|quota exceeded|rate.?limit"
    r"|usage.?limit|plan.?limit|account.?(suspended|disabled|deactivated)",
    re.IGNORECASE,
)

_BINARY_PATTERNS = re.compile(
    r"command not found|no such file|not found|not installed"
    r"|permission denied|cannot execute|exec format error",
    re.IGNORECASE,
)


def classify_session_error(
    returncode: int,
    stderr: str,
    stdout: str = "",
    backend: str = "",
) -> str | None:
    """Classify a subprocess failure into an actionable hint, or None.

    Returns a short human-readable hint when the error matches a known
    pattern; None if nothing specific was detected.
    """
    combined = f"{stderr}\n{stdout}"

    if _AUTH_PATTERNS.search(combined):
        return (
            f"{backend + ': ' if backend else ''}"
            f"Authentication failed — check your API key or login status."
        )

    if _SUBSCRIPTION_PATTERNS.search(combined):
        return (
            f"{backend + ': ' if backend else ''}"
            f"Subscription/billing issue — check your account status."
        )

    if _BINARY_PATTERNS.search(combined):
        return (
            f"{backend + ': ' if backend else ''}"
            f"Binary not working — reinstall or check PATH."
        )

    if returncode < 0:
        import signal

        try:
            sig = signal.Signals(-returncode).name
        except (ValueError, AttributeError):
            sig = str(-returncode)
        return f"{backend + ': ' if backend else ''}Process killed by signal {sig}."

    return None
