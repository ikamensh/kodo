"""Advisory queue — thread-safe feedback channel for coach and human input.

Both AI coach and human feedback produce Advisory messages that get
injected into the orchestrator's context via agent tool returns and
between-cycle prompts.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from kodo import log


@dataclass
class Advisory:
    """A single piece of feedback from coach or human."""

    id: str
    message: str
    source: Literal["coach", "human"]
    priority: Literal["info", "warning", "correction"]
    timestamp: float = field(default_factory=time.time)
    orchestrator_response: str | None = None

    @property
    def source_label(self) -> str:
        return self.source.upper()

    @property
    def priority_icon(self) -> str:
        return {"info": "ℹ️", "warning": "⚠️", "correction": "🚨"}[self.priority]


class AdvisoryQueue:
    """Thread-safe queue for advisory messages with drain semantics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[Advisory] = []
        self._history: list[Advisory] = []
        self._counter = 0

    def push(
        self,
        message: str,
        source: Literal["coach", "human"],
        priority: Literal["info", "warning", "correction"] = "info",
    ) -> Advisory:
        """Add an advisory to the queue. Returns the created Advisory."""
        with self._lock:
            self._counter += 1
            adv = Advisory(
                id=f"adv_{self._counter:04d}",
                message=message,
                source=source,
                priority=priority,
            )
            self._pending.append(adv)

        icon = adv.priority_icon
        log.tprint(f"{icon} [advisory|{adv.source}] {adv.message[:200]}")
        log.emit(
            "advisory_pushed",
            advisory_id=adv.id,
            source=adv.source,
            priority=adv.priority,
            message=adv.message[:500],
        )
        return adv

    def drain(self) -> list[Advisory]:
        """Remove and return all pending advisories."""
        with self._lock:
            drained = self._pending[:]
            self._pending.clear()
            self._history.extend(drained)
            return drained

    def record_reply(self, advisory_id: str, response: str) -> bool:
        """Record the orchestrator's reply to an advisory. Returns True if found."""
        with self._lock:
            for adv in self._history:
                if adv.id == advisory_id:
                    adv.orchestrator_response = response
                    return True
            for adv in self._pending:
                if adv.id == advisory_id:
                    adv.orchestrator_response = response
                    return True
        return False

    def get_history(self) -> list[Advisory]:
        """Return all advisories (drained + pending) for coach context."""
        with self._lock:
            return self._history[:] + self._pending[:]

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


def format_advisories(advisories: list[Advisory]) -> str:
    """Format drained advisories for injection into a tool return or prompt."""
    if not advisories:
        return ""

    parts = ["\n---"]
    for adv in advisories:
        parts.append(f"[{adv.source}] {adv.message}")
    parts.append("---")
    return "\n".join(parts)


def format_advisories_for_prompt(advisories: list[Advisory]) -> str:
    """Format advisories for injection into between-cycle prompt (strategic level)."""
    if not advisories:
        return ""

    parts = ["# Feedback\n"]
    for adv in advisories:
        parts.append(f"- [{adv.source}] {adv.message}")
    return "\n".join(parts)
