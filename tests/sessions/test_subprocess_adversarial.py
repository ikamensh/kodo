"""Adversarial tests for subprocess-backed sessions and Agent timeout behavior.

Boundary conditions around hanging sessions, thread cleanup, and prompt return.

Boundary Condition 2 (test_agent_run_returns_on_hanging_session): Agent returns
promptly on timeout, calls session.terminate(), waits for worker to finish, and
shuts down the executor without leaking threads.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from kodo import log
from kodo.agent import Agent
from kodo.log import RunDir
from kodo.sessions.base import QueryResult
from tests.conftest import FakeSession


def _count_threads() -> int:
    """Return the number of non-main threads currently alive."""
    return sum(1 for t in threading.enumerate() if t is not threading.main_thread())


def test_agent_run_returns_on_hanging_session(tmp_path: Path):
    """Boundary Condition 2: Agent.run() must return promptly when a session's
    query blocks indefinitely.

    Uses a session whose query() blocks forever (simulates a stuck subprocess
    or unresponsive backend). With timeout_s set, Agent.run() should return
    within ~timeout_s + small margin, not hang.

    Also checks for thread/process leaks: if Agent returns promptly but leaves
    a background worker thread alive (blocked in the hung session.query()),
    that is documented.
    """
    log.init(RunDir.create(tmp_path, "hanging"))

    class HangingSession(FakeSession):
        """Session whose query() blocks indefinitely until explicitly unblocked."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._unblock = threading.Event()

        def query(self, prompt, project_dir, *, max_turns):
            self._unblock.wait()  # blocks forever until set
            return QueryResult(
                text="unblocked",
                elapsed_s=0.1,
                is_error=False,
            )

        def terminate(self) -> None:
            # Unblock the worker so it can exit (simulates real sessions where
            # terminate() kills the subprocess and the worker returns).
            self._unblock.set()

    session = HangingSession(response_text="never seen")
    timeout_s = 0.1
    agent = Agent(session, "hanging agent", max_turns=5, timeout_s=timeout_s)

    threads_before = _count_threads()
    start = time.monotonic()
    result = agent.run("do something", tmp_path, agent_name="test")
    elapsed = time.monotonic() - start
    threads_after = _count_threads()

    # Unblock the worker thread so it can exit (cleanup for process teardown)
    session._unblock.set()
    time.sleep(0.05)  # allow worker to finish

    # --- Prompt return ---
    if elapsed > 2.0:
        pytest.fail(
            f"Boundary Condition 2 HANG: Agent.run() took {elapsed:.1f}s — "
            f"expected return within ~{timeout_s}s + margin. "
            "Agent appears to block indefinitely when session.query() hangs."
        )

    # --- Correct timeout behavior ---
    assert result.is_error is True
    assert "timed out" in result.text.lower()

    # --- Thread leak check ---
    threads_created = threads_after - threads_before
    assert threads_created == 0, (
        f"Boundary Condition 2 LEAK: Agent.run() returned promptly ({elapsed:.2f}s) "
        f"but left {threads_created} extra thread(s) alive. "
        "Worker thread must be daemon so it does not block process exit."
    )
