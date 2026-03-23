"""Scripted input helper for testing human feedback injection during orchestrator runs.

Provides ``ScriptedInput`` — a timer-based tool that pushes messages into an
``AdvisoryQueue`` at scheduled delays, simulating a human typing feedback
mid-run without needing a real terminal.

Usage::

    queue = AdvisoryQueue()
    scripted = ScriptedInput(queue)
    scripted.after(2.0, "stop immediately", priority="correction")
    scripted.start()
    result = orchestrator.run(..., advisory_queue=queue)
    scripted.cancel()
"""

from __future__ import annotations

import threading

from kodo.advisory import AdvisoryQueue


class ScriptedInput:
    """Schedule timed pushes to an AdvisoryQueue during an orchestrator run.

    Chain ``.after()`` calls to build a script, then call ``.start()``
    before launching the orchestrator.  Call ``.cancel()`` in a finally
    block to clean up timers.

    Example::

        scripted = (
            ScriptedInput(queue)
            .after(1.0, "focus on tests first")
            .after(5.0, "stop and call goal_done", priority="correction")
        )
        scripted.start()
        try:
            result = orch.run(..., advisory_queue=queue)
        finally:
            scripted.cancel()
    """

    def __init__(self, advisory_queue: AdvisoryQueue) -> None:
        self.queue = advisory_queue
        self._timers: list[threading.Timer] = []

    def after(
        self,
        delay_s: float,
        message: str,
        *,
        priority: str = "info",
    ) -> "ScriptedInput":
        """Schedule a message push after *delay_s* seconds."""
        timer = threading.Timer(
            delay_s, self._push, args=(message, priority)
        )
        timer.daemon = True
        self._timers.append(timer)
        return self

    def _push(self, message: str, priority: str) -> None:
        self.queue.push(message, source="human", priority=priority)

    def start(self) -> None:
        """Start all scheduled timers."""
        for t in self._timers:
            t.start()

    def cancel(self) -> None:
        """Cancel all pending timers."""
        for t in self._timers:
            t.cancel()
