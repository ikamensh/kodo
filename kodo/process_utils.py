"""Shared subprocess cleanup helpers."""

from __future__ import annotations

import subprocess


def graceful_kill(proc: subprocess.Popen, timeout: float = 5) -> None:
    """Terminate a subprocess gracefully, escalating to SIGKILL if needed.

    1. Send SIGTERM (via ``proc.terminate()``).
    2. Wait up to *timeout* seconds for the process to exit.
    3. If still alive, send SIGKILL and wait another 2 seconds.

    Handles already-exited processes without raising.
    """
    if proc.poll() is not None:
        return  # already exited

    try:
        proc.terminate()
    except OSError:
        return  # already dead

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass  # already exited between terminate and kill
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass  # will be reaped when parent exits
