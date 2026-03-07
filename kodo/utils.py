"""Shared utilities used across kodo modules."""

from __future__ import annotations

import asyncio
import threading
from typing import Callable, TypeVar

T = TypeVar("T")


def run_in_thread(
    fn: Callable[[], T],
    *,
    timeout: float = 300,
) -> T:
    """Run *fn* in a dedicated thread with its own asyncio event loop.

    This is needed when calling pydantic-ai's ``agent.run()`` (async) from
    within a context that already holds an event loop (e.g. inside
    ``run_sync()``).  Each thread gets its own loop so httpx clients
    don't share asyncio primitives across loops.

    *fn* is called with no arguments; it should capture what it needs
    via closure.  Exceptions from *fn* are re-raised in the caller.
    If the thread doesn't finish within *timeout* seconds, a
    ``TimeoutError`` is raised.
    """
    result_holder: list[T] = []
    error_holder: list[BaseException] = []

    def _target() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result_holder.append(fn())
        except BaseException as exc:
            error_holder.append(exc)
        finally:
            loop.close()

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if error_holder:
        raise error_holder[0]
    if not result_holder:
        raise TimeoutError(f"Thread timed out after {timeout}s")
    return result_holder[0]


def strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences (```…```) wrapping a string.

    Commonly needed when parsing JSON from LLM output that arrives
    wrapped in ```json … ``` blocks.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines)
    return text
