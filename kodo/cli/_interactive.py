"""Interactive user input during orchestrator runs.

Uses prompt_toolkit to provide a persistent input line at the bottom of the
terminal while orchestrator output scrolls above.  User text is pushed into
the AdvisoryQueue and reaches the orchestrator via existing injection points
(tool returns and between-cycle prompts).
"""

from __future__ import annotations

import sys
import threading
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape as xml_escape

from kodo import log

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.patch_stdout import patch_stdout

    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False

if TYPE_CHECKING:
    from kodo.advisory import AdvisoryQueue
    from kodo.orchestrators.base import RunResult


def is_interactive(json_mode: bool = False) -> bool:
    """Return True when interactive input should be enabled."""
    return sys.stdin.isatty() and not json_mode


def _build_toolbar() -> HTML:
    """Build a live status bar from RunProgress and RunStats."""
    progress = log.get_run_progress()
    cycle, max_cycles, stage_label, active_agent = progress.snapshot()

    parts: list[str] = []

    # Elapsed time
    elapsed = log.get_elapsed_s()
    if elapsed is not None:
        parts.append(log._fmt_time(elapsed))

    # Cycle info
    if max_cycles:
        parts.append(f"cycle {cycle}/{max_cycles}")

    # Stage
    if stage_label:
        parts.append(xml_escape(stage_label))

    # Active agent
    if active_agent:
        parts.append(f"<b>{xml_escape(active_agent)}</b> working")

    # Cost summary from RunStats
    stats = log.get_run_stats()
    agents, orch_cost, _bucket = stats.snapshot()
    if agents:
        total_cost = sum(s.cost_usd for s in agents.values()) + orch_cost
        total_calls = sum(s.calls for s in agents.values())
        parts.append(f"{total_calls} calls")
        if total_cost >= 0.005:
            parts.append(f"${total_cost:.2f}")

    if not parts:
        return HTML('<style bg="#333333" fg="#888888"> kodo </style>')

    sep = ' <style fg="#555555">|</style> '
    inner = sep.join(parts)
    return HTML(f'<style bg="#333333" fg="#cccccc"> {inner} </style>')


def run_with_interactive_input(
    orchestrator: Any,
    run_args: tuple,
    run_kwargs: dict,
    advisory_queue: "AdvisoryQueue",
) -> "RunResult":
    """Run the orchestrator in a background thread with interactive input.

    *run_args* are the positional arguments to ``orchestrator.run()``
    (goal, project_dir, team).  *run_kwargs* are the keyword arguments.

    The main thread reads user input via prompt_toolkit and pushes it into
    the advisory queue.  All print() calls from the background thread are
    rendered cleanly above the input line via ``patch_stdout()``.

    Falls back to a plain synchronous run if prompt_toolkit is unavailable.
    """
    if not _HAS_PROMPT_TOOLKIT:
        return orchestrator.run(
            *run_args, **run_kwargs, advisory_queue=advisory_queue
        )

    result_holder: list[RunResult | None] = [None]
    error_holder: list[BaseException | None] = [None]
    done_event = threading.Event()
    session: PromptSession = PromptSession(
        bottom_toolbar=_build_toolbar,
        refresh_interval=0.5,
    )

    def _bg_run() -> None:
        try:
            result_holder[0] = orchestrator.run(
                *run_args, **run_kwargs, advisory_queue=advisory_queue
            )
        except BaseException as exc:
            error_holder[0] = exc
        finally:
            done_event.set()
            # Break the blocking prompt() call in the main thread.
            try:
                if session.app and session.app.is_running:
                    session.app.exit()
            except Exception:
                pass

    thread = threading.Thread(target=_bg_run, name="orchestrator", daemon=True)
    thread.start()

    stopping = False
    prompt_message = HTML("<style fg='#888888'>  &gt; </style>")
    placeholder = HTML("<style fg='#555555'>type to steer agent</style>")

    try:
        with patch_stdout(raw=True):
            while not done_event.is_set():
                try:
                    text = session.prompt(prompt_message, placeholder=placeholder)
                except KeyboardInterrupt:
                    if stopping:
                        # Second Ctrl+C: force stop
                        raise
                    stopping = True
                    log.tprint(
                        "Stopping after current exchange... "
                        "(Ctrl+C again to force)"
                    )
                    advisory_queue.push(
                        "User requested stop. Finish current work and "
                        "call goal_done or end_cycle immediately.",
                        source="human",
                        priority="correction",
                    )
                    continue
                except EOFError:
                    break

                if text is None:
                    # session.app.exit() was called from bg thread
                    break
                text = text.strip()
                if not text:
                    continue

                # Erase the echoed prompt line — the advisory push
                # prints a formatted version that replaces it.
                sys.stdout.write("\x1b[1A\x1b[2K\r")
                sys.stdout.flush()

                if text == "/stop":
                    stopping = True
                    advisory_queue.push(
                        "User requested stop. Finish current work and "
                        "call goal_done or end_cycle immediately.",
                        source="human",
                        priority="correction",
                    )
                    continue

                # Push user feedback into the advisory queue
                advisory_queue.push(text, source="human")
                log.emit("human_input", message=text[:500])
    except KeyboardInterrupt:
        # Propagate to the caller's KeyboardInterrupt handler
        thread.join(timeout=10)
        raise

    thread.join(timeout=30)

    if error_holder[0] is not None:
        raise error_holder[0]

    return result_holder[0]  # type: ignore[return-value]
