"""Minimal structured logging hooks for worker sessions.

Sessions call ``log.emit(event, **fields)`` / ``log.tprint(msg)`` /
``log.save_conversation(agent, idx, messages)``.  This module provides
standalone default implementations plus a sink-injection point so a host
application (e.g. kodo) can redirect events into its own run log.

Rationale: keeping a tiny surface here means kodo_workers has zero
dependency on kodo's RunDir / RunStats / progress-table machinery, while
still letting kodo capture every session event when the two are used
together.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


class _Sink(Protocol):
    def emit(self, event: str, **data: Any) -> None: ...
    def tprint(self, msg: str) -> None: ...
    def save_conversation(
        self, agent_name: str, query_index: int, messages: list[dict]
    ) -> str | None: ...


class _DefaultSink:
    """Standalone sink: JSONL to a file if set, otherwise silent.

    ``tprint`` always prints to stdout.  ``save_conversation`` writes a
    gzip file next to the log file when one is configured.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._log_file: Path | None = None
        self._start_time: float = time.monotonic()

    def set_log_file(self, path: Path | None) -> None:
        with self._lock:
            self._log_file = path
            self._start_time = time.monotonic()

    def emit(self, event: str, **data: Any) -> None:
        with self._lock:
            path = self._log_file
            start = self._start_time
        if path is None:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "t": round(time.monotonic() - start, 3),
            "event": event,
            **data,
        }
        try:
            with open(path, "a") as f:
                f.write(json.dumps(record, default=_serialize) + "\n")
        except OSError:
            pass  # best-effort

    def tprint(self, msg: str) -> None:
        with self._lock:
            start = self._start_time
        elapsed = time.monotonic() - start
        print(f"  [{elapsed:7.1f}s] {msg}", flush=True)

    def save_conversation(
        self, agent_name: str, query_index: int, messages: list[dict]
    ) -> str | None:
        import gzip

        with self._lock:
            path = self._log_file
        if path is None:
            return None
        try:
            conv_dir = path.parent / "conversations"
            conv_dir.mkdir(exist_ok=True)
            fname = f"{agent_name}_{query_index:03d}.jsonl.gz"
            data = "\n".join(json.dumps(m, default=_serialize) for m in messages)
            (conv_dir / fname).write_bytes(gzip.compress(data.encode()))
            return f"conversations/{fname}"
        except Exception:
            return None  # best-effort


_default_sink = _DefaultSink()
_sink: _Sink = _default_sink


def set_sink(sink: _Sink | None) -> None:
    """Install (or clear) a host-provided sink.  Passing None restores the default."""
    global _sink
    _sink = sink if sink is not None else _default_sink


def set_log_file(path: Path | None) -> None:
    """Point the default sink at *path* (a JSONL file).  Convenience for standalone use."""
    _default_sink.set_log_file(path)


def emit(event: str, **data: Any) -> None:
    """Write a structured event to the active sink."""
    _sink.emit(event, **data)


def tprint(msg: str) -> None:
    """Print *msg* with an elapsed-time prefix via the active sink."""
    _sink.tprint(msg)


def save_conversation(
    agent_name: str, query_index: int, messages: list[dict]
) -> str | None:
    """Persist a full conversation.  Returns the stored path, or None if unsupported."""
    return _sink.save_conversation(agent_name, query_index, messages)


def _serialize(obj: Any) -> Any:
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(obj)
    try:
        return repr(obj)
    except Exception:
        return f"<{type(obj).__name__} unserializable>"
