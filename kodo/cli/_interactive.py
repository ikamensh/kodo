"""Interactive user input during orchestrator runs.

Architecture: there is deliberately *no* persistent input line or status bar.
An earlier version used prompt_toolkit (``bottom_toolbar`` + ``patch_stdout``)
to keep an always-visible prompt while orchestrator output scrolled above.
Any such design needs a renderer that erases and redraws its UI around every
output write, tracking the terminal cursor remotely via CPR escape
round-trips.  Under tmux those round-trips race with output bursts, the
renderer's cursor model desyncs, and the scrollback floods with duplicated
prompts and blank lines — https://github.com/ikamensh/kodo/issues/52.

Instead, output scrolls plainly and input is on demand:

- A raw-mode (cbreak) watcher waits for a keypress on stdin.
- The first typed character opens a composer: a plain ``input()`` line with
  readline editing, prefilled with that character.
- While composing, orchestrator output is buffered and flushed after submit,
  so the line being typed is never disturbed.
- Submitted text is pushed into the AdvisoryQueue and reaches the
  orchestrator via existing injection points (tool returns and between-cycle
  prompts).

No escape-sequence rendering happens outside the composer, so there is
nothing to corrupt — in tmux, over ssh, or anywhere else.
"""

from __future__ import annotations

import io
import os
import select
import sys
import threading
from typing import TYPE_CHECKING, Any

from kodo import log
from kodo.formatting import DIM, RESET

try:
    import termios
    import tty

    _HAS_TERMIOS = True
except ImportError:  # non-POSIX
    _HAS_TERMIOS = False

if TYPE_CHECKING:
    from kodo.advisory import AdvisoryQueue
    from kodo.orchestrators.base import RunResult


def is_interactive(json_mode: bool = False) -> bool:
    """Return True when interactive input should be enabled."""
    return sys.stdin.isatty() and not json_mode


def _build_status() -> str:
    """One-line run status shown when the composer opens."""
    progress = log.get_run_progress()
    cycle, max_cycles, stage_label, active_agent = progress.snapshot()

    parts: list[str] = []

    elapsed = log.get_elapsed_s()
    if elapsed is not None:
        parts.append(log._fmt_time(elapsed))
    if max_cycles:
        parts.append(f"cycle {cycle}/{max_cycles}")
    if stage_label:
        parts.append(log._trunc(stage_label, 40))
    if active_agent:
        parts.append(f"{active_agent} working")

    stats = log.get_run_stats()
    agents, orch_cost, _bucket = stats.snapshot()
    if agents:
        total_cost = sum(s.cost_usd for s in agents.values()) + orch_cost
        total_calls = sum(s.calls for s in agents.values())
        parts.append(f"{total_calls} calls")
        if total_cost >= 0.005:
            parts.append(f"${total_cost:.2f}")

    return " | ".join(parts) if parts else "kodo"


def _printable(chunk: str) -> str:
    """Reduce a raw key chunk to printable text (control chars become spaces)."""
    return "".join(c if c.isprintable() else " " for c in chunk).strip()


class _OutputHold:
    """Buffers writes to sys.stdout/sys.stderr until released.

    Exposes ``fileno``/``isatty`` of the real stdout so that ``input()``
    still takes the readline path (it probes sys.stdin/sys.stdout for a tty).
    """

    def __init__(self) -> None:
        self._buffer = io.StringIO()
        self._lock = threading.Lock()
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = self  # type: ignore[assignment]
        sys.stderr = self  # type: ignore[assignment]

    def write(self, data: str) -> int:
        with self._lock:
            return self._buffer.write(data)

    def flush(self) -> None:
        pass

    def fileno(self) -> int:
        return self._stdout.fileno()

    def isatty(self) -> bool:
        return self._stdout.isatty()

    @property
    def encoding(self) -> str:
        return self._stdout.encoding

    @property
    def errors(self) -> str:
        return self._stdout.errors

    def release(self) -> None:
        sys.stdout = self._stdout
        sys.stderr = self._stderr
        text = self._buffer.getvalue()
        if text:
            self._stdout.write(text)
            self._stdout.flush()


def _input_with_prefill(prompt: str, prefill: str) -> str:
    """input() with *prefill* already inserted into the line editor.

    GNU readline supports injecting text via a startup hook; libedit (macOS)
    does not, so there the prefill is embedded into the prompt instead (it
    just cannot be backspaced over).
    """
    if not prefill:
        return input(prompt)
    try:
        import readline
    except ImportError:
        readline = None
    if readline is not None and getattr(readline, "backend", "readline") == "readline":
        readline.set_startup_hook(lambda: readline.insert_text(prefill))
        try:
            return input(prompt)
        finally:
            readline.set_startup_hook(None)
    return prefill + input(prompt + prefill)


class _Console:
    """Raw-keypress watcher and on-demand line composer for the controlling tty."""

    def __init__(self) -> None:
        self._fd = sys.stdin.fileno()
        self._saved = termios.tcgetattr(self._fd)
        self.composing = False
        tty.setcbreak(self._fd)

    def restore(self) -> None:
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def wait_key(self, timeout: float) -> str | None:
        """Return typed text, '' when stdin closed, None on timeout/ignorable input."""
        r, _, _ = select.select([self._fd], [], [], timeout)
        if not r:
            return None
        data = os.read(self._fd, 1024)
        if not data:
            return ""
        text = data.decode("utf-8", errors="ignore")
        if text.startswith("\x1b"):
            return None  # terminal-generated sequence (arrow keys, focus events, ...)
        return text

    def compose(self, prefill: str) -> str | None:
        """Cooked-mode line input; orchestrator output is held until done.

        Returns the submitted line, or None on EOF (Ctrl+D).
        """
        self.composing = True
        self.restore()
        print(
            f"  {DIM}─ {_build_status()} · steering — Enter sends, empty cancels{RESET}"
        )
        hold = _OutputHold()
        try:
            try:
                line = _input_with_prefill("  > ", prefill)
            except EOFError:
                return None
            # Erase the echoed input line (the advisory print replaces it);
            # on cancel also erase the status line above it.
            erase = b"\x1b[1A\x1b[2K\r"
            os.write(hold.fileno(), erase if line.strip() else erase * 2)
            return line
        finally:
            hold.release()
            tty.setcbreak(self._fd)
            self.composing = False


_STOP_ADVISORY = (
    "User requested stop. Finish current work and "
    "call goal_done or end_cycle immediately."
)


def run_with_interactive_input(
    orchestrator: Any,
    run_args: tuple,
    run_kwargs: dict,
    advisory_queue: "AdvisoryQueue",
) -> "RunResult":
    """Run the orchestrator in a background thread with on-demand steering input.

    *run_args* are the positional arguments to ``orchestrator.run()``
    (goal, project_dir, team).  *run_kwargs* are the keyword arguments.

    The main thread watches stdin for keypresses and opens a composer line
    on demand; submitted text is pushed into the advisory queue.  Falls back
    to a plain synchronous run when raw terminal control is unavailable.
    """
    if not _HAS_TERMIOS:
        return orchestrator.run(*run_args, **run_kwargs, advisory_queue=advisory_queue)

    try:
        console = _Console()
    except (OSError, termios.error):
        return orchestrator.run(*run_args, **run_kwargs, advisory_queue=advisory_queue)

    result_holder: list[RunResult | None] = [None]
    error_holder: list[BaseException | None] = [None]
    done_event = threading.Event()
    stdout_fd = sys.stdout.fileno()

    def _bg_run() -> None:
        try:
            result_holder[0] = orchestrator.run(
                *run_args, **run_kwargs, advisory_queue=advisory_queue
            )
        except BaseException as exc:
            error_holder[0] = exc
        finally:
            done_event.set()
            if console.composing:
                # Composer blocks on input(); write past any output hold so
                # the user knows to press Enter.
                os.write(stdout_fd, "\n  run finished — press Enter\n".encode())

    thread = threading.Thread(target=_bg_run, name="orchestrator", daemon=True)
    thread.start()

    stopping = False
    try:
        while not done_event.is_set():
            try:
                chunk = console.wait_key(timeout=0.2)
                if chunk is None:
                    continue
                if chunk == "":
                    break  # stdin closed
                text = console.compose(_printable(chunk))
            except KeyboardInterrupt:
                if stopping:
                    # Second Ctrl+C: force stop
                    raise
                stopping = True
                log.tprint(
                    "Stopping after current exchange... (Ctrl+C again to force)"
                )
                advisory_queue.push(
                    _STOP_ADVISORY, source="human", priority="correction"
                )
                continue

            if text is None:
                break  # EOF (Ctrl+D)
            text = text.strip()
            if not text:
                continue
            if text == "/stop":
                stopping = True
                advisory_queue.push(
                    _STOP_ADVISORY, source="human", priority="correction"
                )
                continue

            advisory_queue.push(text, source="human")
            log.emit("human_input", message=text[:500])
    except KeyboardInterrupt:
        # Propagate to the caller's KeyboardInterrupt handler
        thread.join(timeout=10)
        raise
    finally:
        console.restore()

    thread.join(timeout=30)

    if error_holder[0] is not None:
        raise error_holder[0]

    return result_holder[0]  # type: ignore[return-value]
