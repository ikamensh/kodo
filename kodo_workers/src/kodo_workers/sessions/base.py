"""Session protocol and shared types."""

from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol


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
    last_activity: float = 0.0  # monotonic timestamp of last output from session

    def __post_init__(self) -> None:
        if self.last_activity == 0.0:
            self.last_activity = time.monotonic()

    def touch(self) -> None:
        """Mark that the session produced output just now."""
        self.last_activity = time.monotonic()

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


class Session(Protocol):
    model: str

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
        self,
        prompt: str,
        project_dir: Path,
        *,
        max_turns: int,
    ) -> QueryResult: ...

    def reset(self) -> None: ...

    def terminate(self) -> None:
        """Kill the underlying process/connection.

        Called on timeout to forcefully stop a running query before ``reset()``.
        Implementations should be safe to call even when no query is in flight.
        """
        ...

    def close(self) -> None:
        """Release resources (event loops, file handles, threads).

        Called after ``terminate()`` for final cleanup.  The default is a
        no-op; sessions that own background threads or event loops (e.g.
        ``ClaudeSession``) should override this.
        """
        ...

    def clone(self) -> Session:
        """Return a fresh session with the same config but no state."""
        ...


class SubprocessSession:
    """Base for subprocess-backed sessions (Cursor, Codex, Gemini CLI).

    Provides shared init, stats, system-prompt prepend, subprocess spawn/wait,
    and reset logic.  Subclasses keep their own ``query()`` and override
    ``reset()`` (calling ``super().reset()``) to clear session-specific state.
    """

    _session_label: str  # set by each subclass
    _WAIT_TIMEOUT = 7200  # 2h max; prevents indefinite block if process hangs

    def __init__(
        self,
        model: str,
        system_prompt: str | None = None,
        timeout_s: int = 7200,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self._stats = SessionStats()
        self._system_prompt_sent = False
        self._process: subprocess.Popen | None = None
        self._timeout_s = timeout_s
        self._did_timeout = False

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
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
    ) -> tuple[subprocess.Popen, list[str], threading.Thread]:
        """Spawn subprocess with a stderr-drain thread.

        Returns ``(proc, stderr_chunks, thread)``.
        """
        self._did_timeout = False
        # Strip ANTHROPIC_API_KEY from worker subprocesses to prevent
        # accidental API billing when workers should use subscription.
        import os

        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            env=env,
        )
        self._process = proc
        assert proc.stdout is not None, "stdout must be PIPE"
        assert proc.stderr is not None, "stderr must be PIPE"
        stderr_chunks: list[str] = []
        _STDERR_MAX_LINES = 10_000  # cap to avoid unbounded memory
        _STDERR_MAX_LINE = 65536  # 64KB per line to avoid unbounded single-line buffer

        def _drain() -> None:
            buf_parts: list[str] = []
            line_count = 0
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    buf = "".join(buf_parts)
                    if buf and line_count < _STDERR_MAX_LINES:
                        stderr_chunks.append(
                            buf[:_STDERR_MAX_LINE]
                            + (
                                "\n[... truncated ...]\n"
                                if len(buf) > _STDERR_MAX_LINE
                                else "\n"
                            ),
                        )
                    break
                buf_parts.append(chunk)
                # Join once per read to scan for newlines; remainder
                # stays as a single string so no quadratic growth.
                buf = "".join(buf_parts)
                buf_parts.clear()
                while "\n" in buf or len(buf) >= _STDERR_MAX_LINE:
                    if "\n" in buf:
                        line, _, buf = buf.partition("\n")
                        line = line + "\n"
                    else:
                        line = buf[:_STDERR_MAX_LINE] + "\n[... truncated ...]\n"
                        buf = buf[_STDERR_MAX_LINE:]
                    if line_count < _STDERR_MAX_LINES:
                        stderr_chunks.append(line)
                        line_count += 1
                    elif line_count == _STDERR_MAX_LINES:
                        stderr_chunks.append("\n[... stderr truncated ...]\n")
                        line_count += 1
                if buf:
                    buf_parts.append(buf)

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
        from kodo_workers import log

        try:
            proc.wait(timeout=self._timeout_s)
        except subprocess.TimeoutExpired:
            self._did_timeout = True
            log.emit(
                "session_timeout",
                session=getattr(self, "_session_label", "subprocess"),
                timeout_s=self._timeout_s,
            )
            try:
                proc.terminate()
            except OSError:
                pass  # process already exited
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass  # process already exited
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    log.emit(
                        "zombie_process",
                        session=getattr(self, "_session_label", "subprocess"),
                        pid=proc.pid,
                    )
                    log.tprint(
                        f"⚠️  Process {proc.pid} became zombie (will clean up on exit)",
                    )
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
        if proc is None:
            return  # nothing running
        if proc.poll() is not None:
            self._process = None
            return  # already exited
        try:
            proc.terminate()
        except OSError:
            pass  # already exited
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=2)  # reap zombie after kill
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.wait(0)  # reap if process already exited
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._process = None

    def close(self) -> None:
        """Release resources.  No-op for subprocess sessions (cleanup is in
        ``terminate()`` / ``_wait()``).  Override if the session owns
        long-lived resources like event loops or background threads."""

    def reset(self) -> None:
        """Reset shared state.  Subclasses should log, clear their own state,
        then call ``super().reset()``."""
        self._stats = SessionStats()
        self._system_prompt_sent = False

    def _query_template(
        self,
        cmd: list[str],
        *,
        cwd: str | None = None,
        parse_stdout: Callable[[subprocess.Popen], tuple[str, int, int]],
    ) -> "QueryResult | _SpawnedResult":
        """Shared spawn → parse → wait → stats logic for subprocess queries.

        *parse_stdout* receives the running ``Popen`` and must read from
        ``proc.stdout``.  It returns ``(result_text, input_tokens,
        output_tokens)``.

        Returns a ``QueryResult`` early on spawn failure, or a
        ``_SpawnedResult`` carrying process output for the subclass to
        do session-specific error classification, logging, and final
        ``QueryResult`` construction.
        """
        t0 = time.monotonic()

        try:
            proc, stderr_chunks, stderr_thread = self._spawn(cmd, cwd=cwd)
        except (FileNotFoundError, PermissionError, OSError) as exc:
            elapsed = time.monotonic() - t0
            error_msg = (
                classify_session_error(
                    -1,
                    str(exc),
                    backend=self._session_label,
                )
                or f"Failed to spawn {self._session_label}: {exc}"
            )
            return QueryResult(text=error_msg, elapsed_s=elapsed, is_error=True)

        try:
            result_text, input_tokens, output_tokens = parse_stdout(proc)
        finally:
            stderr_text = self._wait(proc, stderr_chunks, stderr_thread)
        elapsed = time.monotonic() - t0

        self._stats.queries += 1
        self._stats.total_input_tokens += input_tokens
        self._stats.total_output_tokens += output_tokens

        return _SpawnedResult(
            elapsed_s=elapsed,
            returncode=proc.returncode,
            stderr_text=stderr_text,
            result_text=result_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


@dataclass
class _SpawnedResult:
    """Internal result from ``_query_template`` for subclass post-processing.

    Carries process output so the subclass can do session-specific error
    classification, logging, and final ``QueryResult`` construction.
    """

    elapsed_s: float
    returncode: int
    stderr_text: str
    result_text: str
    input_tokens: int
    output_tokens: int


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
    r"|usage.?limit|plan.?limit|account.?(suspended|disabled|deactivated)"
    r"|429\b|too many requests|connection refused|503\b|service unavailable",
    re.IGNORECASE,
)

_BINARY_PATTERNS = re.compile(
    r"command not found|no such file|not found|not installed"
    r"|permission denied|cannot execute|exec format error",
    re.IGNORECASE,
)

_FALLBACK_SIGNAL_NAMES = {
    2: "SIGINT",
    9: "SIGKILL",
    15: "SIGTERM",
}


def _signal_name(signum: int) -> str:
    """Return a stable signal name across platforms."""
    import signal

    try:
        return signal.Signals(signum).name
    except (ValueError, AttributeError):
        return _FALLBACK_SIGNAL_NAMES.get(signum, str(signum))


def classify_session_error(
    returncode: int,
    stderr: str,
    stdout: str = "",
    backend: str = "",
    *,
    did_timeout: bool = False,
    timeout_s: int = 0,
) -> str | None:
    """Classify a subprocess failure into an actionable hint, or None.

    Returns a short human-readable hint when the error matches a known
    pattern; None if nothing specific was detected.
    """
    if did_timeout:
        prefix = f"{backend}: " if backend else ""
        return (
            f"{prefix}Process timed out after {timeout_s}s. "
            "Hint: increase session_timeout_s in TeamConfig."
        )

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
        sig = _signal_name(-returncode)
        return f"{backend + ': ' if backend else ''}Process killed by signal {sig}."

    return None
