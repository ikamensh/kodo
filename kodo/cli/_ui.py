"""UI helpers: ANSI formatting, spinner, banner, atomic file writes."""

import os
import tempfile
import threading
import time
from pathlib import Path

from kodo import __version__

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_RESET = "\033[0m"

_BACKEND_LABELS = {
    "ClaudeSession": "claude code",
    "CursorSession": "cursor",
    "CodexSession": "codex",
    "GeminiCliSession": "gemini cli",
    "MockSession": "mock",
}


def _atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write content to path atomically (temp + rename) to avoid corruption on crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    tmp_path = Path(tmp)
    try:
        os.write(fd, content.encode(encoding))
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
        tmp_path = None
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def _backend_label(agent) -> str:
    return _BACKEND_LABELS.get(type(agent.session).__name__, "?")


def _print_banner() -> None:
    print(f"\n  🦉 kodo v{__version__} — autonomous multi-agent coding")
    print("  https://github.com/ikamen/kodo\n")


def _print_agent(text: str) -> None:
    """Print an agent response with a visible left-border."""
    if not text.strip():
        print(f"\n  {_DIM}(no text response){_RESET}\n")
        return
    lines = text.rstrip().splitlines()
    print()
    for line in lines:
        print(f"  {_DIM}{_CYAN}│{_RESET} {line}")
    print()


def _print_separator() -> None:
    print(f"  {_DIM}{'─' * 60}{_RESET}")


def _terminal_columns() -> int:
    """Terminal width in columns, or 80 if not a TTY."""
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


class _Spinner:
    """Simple elapsed-time spinner for long-running operations."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "Thinking"):
        self._message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join()
        # Clear the spinner line (use terminal width so wrapped lines are covered)
        cols = _terminal_columns()
        print(f"\r{' ' * cols}\r", end="", flush=True)

    def _run(self):
        start = time.monotonic()
        i = 0
        while not self._stop.wait(0.1):
            elapsed = int(time.monotonic() - start)
            frame = self.FRAMES[i % len(self.FRAMES)]
            line = f"\r  {frame} {self._message}... {elapsed}s"
            # Truncate to terminal width to prevent wrapping/reprint artifacts
            cols = _terminal_columns()
            if len(line) > cols:
                line = line[: cols - 1]
            print(line, end="", flush=True)
            i += 1
