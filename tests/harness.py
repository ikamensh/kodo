"""Interactive terminal test harness for kodo CLI.

Drives kodo as a real user would — spawns a PTY, sends keystrokes,
reads output, handles timeouts. Built on pexpect.

Usage:
    from tests.harness import KodoRunner

    with KodoRunner(project_dir="/tmp/test") as k:
        # Non-interactive command
        result = k.run("--help")
        assert "--goal" in result.output

        # Interactive session
        k.spawn()
        k.expect("What's your goal?")
        k.sendline("Build a hello world script")
        k.sendline("")  # empty line to finish goal input
        k.expect("Reuse this config?")
        k.sendline("y")
        k.expect("Proceed?")
        k.sendline("n")  # abort before launch
        k.close()
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pexpect

if TYPE_CHECKING:
    pass


@dataclass
class RunResult:
    """Result of a non-interactive kodo invocation."""

    output: str
    stderr: str
    exit_code: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class KodoRunner:
    """Test harness for driving kodo CLI interactively and non-interactively.

    Parameters:
        project_dir: Directory to use as --project. Created as tmpdir if None.
        timeout: Default timeout in seconds for expect() calls.
        env: Extra environment variables to set.
        git_init: Initialize a git repo in the project dir.
    """

    def __init__(
        self,
        project_dir: str | Path | None = None,
        timeout: int = 30,
        env: dict[str, str] | None = None,
        git_init: bool = True,
    ):
        self._timeout = timeout
        self._extra_env = env or {}
        self._git_init = git_init
        self._child: pexpect.spawn | None = None
        self._owns_tmpdir = project_dir is None
        self._project_dir = Path(
            project_dir or tempfile.mkdtemp(prefix="kodo_test_")
        )
        self._kodo_cmd = self._find_kodo()

    def _find_kodo(self) -> str:
        """Find the kodo executable via uv."""
        # Use uv run to ensure correct venv
        return "uv"

    def _base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self._extra_env)
        # Ensure consistent terminal for pexpect
        env["TERM"] = "dumb"
        env["COLUMNS"] = "120"
        env["LINES"] = "40"
        # Disable color for cleaner output parsing
        env["NO_COLOR"] = "1"
        env["FORCE_COLOR"] = "0"
        return env

    def __enter__(self) -> "KodoRunner":
        self._project_dir.mkdir(parents=True, exist_ok=True)
        if self._git_init and not (self._project_dir / ".git").exists():
            subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=self._project_dir,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "--allow-empty", "-m", "init"],
                cwd=self._project_dir,
                capture_output=True,
            )
        return self

    def __exit__(self, *exc):
        self.close()
        if self._owns_tmpdir and self._project_dir.exists():
            shutil.rmtree(self._project_dir, ignore_errors=True)

    # ── Non-interactive commands ────────────────────────────────────────

    def run(
        self,
        *args: str,
        timeout: int | None = None,
        input_text: str | None = None,
    ) -> RunResult:
        """Run kodo with args non-interactively. Returns RunResult."""
        cmd = ["uv", "run", "kodo", *args]
        # Add --project if not already specified and not a subcommand
        if "--project" not in args and not any(
            a in args for a in ("runs", "run", "backends", "backend",
                                "teams", "team", "logs", "log", "--help",
                                "--version", "help")
        ):
            cmd.extend(["--project", str(self._project_dir)])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout or self._timeout,
                cwd=self._project_dir,
                env=self._base_env(),
                input=input_text,
            )
            return RunResult(
                output=proc.stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired as e:
            return RunResult(
                output=(e.stdout or b"").decode("utf-8", errors="replace"),
                stderr=(e.stderr or b"").decode("utf-8", errors="replace"),
                exit_code=-1,
                timed_out=True,
            )

    # ── Interactive sessions (pexpect) ──────────────────────────────────

    def spawn(
        self,
        *extra_args: str,
        timeout: int | None = None,
    ) -> pexpect.spawn:
        """Spawn an interactive kodo session. Returns the pexpect child."""
        self.close()  # close any existing session

        args = ["run", "kodo", "--project", str(self._project_dir), *extra_args]
        self._child = pexpect.spawn(
            "uv",
            args,
            timeout=timeout or self._timeout,
            env=self._base_env(),
            cwd=str(self._project_dir),
            encoding="utf-8",
            codec_errors="replace",
            dimensions=(40, 120),
        )
        return self._child

    def expect(
        self,
        pattern: str | list[str],
        timeout: int | None = None,
    ) -> int:
        """Wait for pattern in output. Returns index if list, 0 if string.

        Raises pexpect.TIMEOUT or pexpect.EOF on failure.
        """
        if self._child is None:
            raise RuntimeError("No interactive session. Call spawn() first.")
        return self._child.expect(pattern, timeout=timeout or self._timeout)

    def expect_exact(
        self,
        pattern: str | list[str],
        timeout: int | None = None,
    ) -> int:
        """Wait for exact string match (no regex)."""
        if self._child is None:
            raise RuntimeError("No interactive session. Call spawn() first.")
        return self._child.expect_exact(pattern, timeout=timeout or self._timeout)

    def sendline(self, text: str = "") -> None:
        """Send a line of text (with newline appended)."""
        if self._child is None:
            raise RuntimeError("No interactive session. Call spawn() first.")
        self._child.sendline(text)

    def send(self, text: str) -> None:
        """Send raw text (no newline)."""
        if self._child is None:
            raise RuntimeError("No interactive session. Call spawn() first.")
        self._child.send(text)

    def send_ctrl_c(self) -> None:
        """Send Ctrl+C (SIGINT)."""
        if self._child is None:
            raise RuntimeError("No interactive session. Call spawn() first.")
        self._child.sendintr()

    def send_ctrl_d(self) -> None:
        """Send Ctrl+D (EOF)."""
        if self._child is None:
            raise RuntimeError("No interactive session. Call spawn() first.")
        self._child.sendeof()

    def send_arrow_down(self, count: int = 1) -> None:
        """Send arrow-down key(s) for questionary navigation."""
        if self._child is None:
            raise RuntimeError("No interactive session. Call spawn() first.")
        for _ in range(count):
            self._child.send("\x1b[B")

    def send_arrow_up(self, count: int = 1) -> None:
        """Send arrow-up key(s) for questionary navigation."""
        if self._child is None:
            raise RuntimeError("No interactive session. Call spawn() first.")
        for _ in range(count):
            self._child.send("\x1b[A")

    def send_enter(self) -> None:
        """Send Enter key."""
        self.sendline()

    @property
    def before(self) -> str:
        """Text matched before the last expect()."""
        if self._child is None:
            return ""
        return self._child.before or ""

    @property
    def after(self) -> str:
        """Text matched by the last expect()."""
        if self._child is None:
            return ""
        return self._child.after or ""

    @property
    def buffer(self) -> str:
        """Current unread buffer contents."""
        if self._child is None:
            return ""
        return self._child.buffer or ""

    def read_all_nonblocking(self, timeout: float = 0.5) -> str:
        """Read all available output without blocking long."""
        if self._child is None:
            return ""
        try:
            self._child.expect(pexpect.TIMEOUT, timeout=timeout)
        except pexpect.TIMEOUT:
            pass
        return self._child.before or ""

    def get_full_output(self) -> str:
        """Get all output captured so far (before + buffer)."""
        if self._child is None:
            return ""
        return (self._child.before or "") + (self._child.buffer or "")

    def wait_for_exit(self, timeout: int | None = None) -> int:
        """Wait for the process to exit. Returns exit code."""
        if self._child is None:
            raise RuntimeError("No interactive session.")
        self._child.expect(pexpect.EOF, timeout=timeout or self._timeout)
        self._child.close()
        code = self._child.exitstatus or 0
        self._child = None
        return code

    def close(self) -> None:
        """Close the current interactive session if any."""
        if self._child is not None:
            if self._child.isalive():
                self._child.terminate(force=True)
            self._child.close()
            self._child = None

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    @property
    def is_alive(self) -> bool:
        return self._child is not None and self._child.isalive()
