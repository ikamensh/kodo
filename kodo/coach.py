"""Coach — persistent background agent that monitors orchestrator behavior.

Runs parallel to the orchestrator, reads log events and agent summaries,
detects drift/circles/strategic issues, and pushes advisories to the
AdvisoryQueue. Also polls for human feedback from a file channel.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from kodo import log
from kodo.advisory import AdvisoryQueue


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_DEFAULT_TENETS = """\
Ensure:
1. Building towards appropriately ambitious target, not strawman
2. Architecture is sound, no contradictions
3. Whatever is built gets tested is the closest way to how it will get used

Avoid:
1. Micromanagement — doing agents' work through a communication bottleneck
2. Drift — wandering from the original goal
3. Over-decomposition — pieces so small agents lack context
"""

_TEST_TENETS = """\
Mode: TEST — find real bugs by using the software like a user would.

Make system come as close as possible to real usage scenarios, its allowed and encouraged
to build tools to enable that.
"""

_IMPROVE_TENETS = """\
Mode: IMPROVE — improve abstractions and detail of code.

Avoid:
Fixes for unrealistic usecases, extra try/catch with no benefit.
"""

_MODE_TENETS: dict[str, str] = {
    "test": _TEST_TENETS,
    "improve": _IMPROVE_TENETS,
}

COACH_SYSTEM_PROMPT = """\
You coach an AI orchestrator that delegates work to coding agents. \
You see each agent dispatch and result. Stay silent unless the orchestrator \
is strategically off track. Only say things the orchestrator doesn't already \
know and can act on.

How the orchestrator works:
- It runs in stages (e.g. discovery → implementation → verification)
- It delegates tasks to worker/tester agents who have full codebase access
- Intermediate results don't need to be perfect — the orchestrator iterates
- The orchestrator handles reporting and completion on its own

Your role is STRATEGIC, not tactical:
- Flag patterns across multiple dispatches (drift, circles, wrong approach)
- Don't micromanage individual agent outputs
- Don't remind about report files, formatting, or bookkeeping
- Don't repeat yourself — if you said it once, the orchestrator heard it

{tenets}
"""

_COACH_DEFAULT_MODEL = "google-gla:gemini-3.1-pro-preview"


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Coach base
# ---------------------------------------------------------------------------


class Coach:
    """Background thread that watches orchestrator activity and pushes feedback.

    The coach:
    1. Reads the JSONL log file to track orchestrator dispatches and results
    2. Periodically assesses whether the orchestrator is drifting
    3. Polls a human feedback file for manual interventions
    4. Pushes all feedback into the shared AdvisoryQueue
    """

    def __init__(
        self,
        queue: AdvisoryQueue,
        goal: str,
        project_dir: Path,
        *,
        assess_every_n: int = 1,
        model: str | None = None,
        tenets: str | None = None,
        mode: str | None = None,
        poll_interval: float = 3.0,
    ) -> None:
        self._queue = queue
        self._goal = goal
        self._project_dir = project_dir
        self._assess_every_n = assess_every_n
        self._poll_interval = poll_interval
        self._model = model
        # Priority: explicit tenets file > mode-specific defaults > generic defaults
        self._tenets = tenets or _MODE_TENETS.get(mode or "", _DEFAULT_TENETS)

        # State tracking
        self._dispatches: list[dict] = []
        self._results: list[dict] = []
        self._errors: list[dict] = []
        self._last_assess_count = 0

        # pydantic-ai conversation history
        self._message_history: list = []
        self._assess_lock = threading.Lock()
        self._agent = None  # created lazily on first assess

        # Human feedback file
        self._human_feedback_file: Path | None = None
        self._human_feedback_pos = 0

        # Thread control
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Log file tracking
        self._log_file: Path | None = None
        self._log_pos = 0

    def start(self) -> None:
        """Start the coach background thread."""
        self._log_file = log.get_log_file()

        # Set up human feedback file
        if self._log_file:
            self._human_feedback_file = (
                self._log_file.parent / "human_feedback.txt"
            )

        self._thread = threading.Thread(
            target=self._run_loop,
            name="coach",
            daemon=True,
        )
        self._thread.start()
        log.tprint("🏋️ [coach] started")
        log.emit("coach_started", goal=self._goal[:200])

    def stop(self) -> None:
        """Signal the coach to stop and wait for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        log.emit("coach_stopped")

    def record_dispatch(self, agent_name: str, task: str) -> None:
        """Called by handle_agent_call to record a dispatch (thread-safe)."""
        self._dispatches.append(
            {"agent": agent_name, "task": task[:500], "t": time.time()}
        )
        self._maybe_assess()

    def record_result(
        self, agent_name: str, task: str, is_error: bool, *, report: str = "",
    ) -> None:
        """Called by handle_agent_call to record a result with the agent's report."""
        entry = {
            "agent": agent_name,
            "task": task[:500],
            "is_error": is_error,
            "report": report[:2000],
            "t": time.time(),
        }
        self._results.append(entry)
        if is_error:
            self._errors.append(entry)

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Main loop: poll for human feedback."""
        while not self._stop.wait(timeout=self._poll_interval):
            try:
                self._poll_human_feedback()
            except Exception as exc:
                log.emit("coach_poll_error", error=str(exc))

    def _poll_human_feedback(self) -> None:
        """Check human feedback file for new lines."""
        if self._human_feedback_file is None:
            return
        if not self._human_feedback_file.exists():
            return

        try:
            with open(self._human_feedback_file) as f:
                f.seek(self._human_feedback_pos)
                new_lines = f.readlines()
                self._human_feedback_pos = f.tell()
        except OSError:
            return

        for line in new_lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Parse optional priority prefix: "warning: message" or "correction: message"
            priority = "info"
            for p in ("correction", "warning", "info"):
                if line.lower().startswith(f"{p}:"):
                    priority = p
                    line = line[len(p) + 1 :].strip()
                    break

            self._queue.push(line, source="human", priority=priority)

    # ------------------------------------------------------------------
    # AI assessment
    # ------------------------------------------------------------------

    def _maybe_assess(self) -> None:
        """Trigger AI assessment if enough dispatches have accumulated."""
        dispatch_count = len(self._dispatches)
        if dispatch_count - self._last_assess_count < self._assess_every_n:
            return
        self._last_assess_count = dispatch_count

        # Run assessment in a separate thread to not block the tool return
        threading.Thread(
            target=self._assess,
            name="coach-assess",
            daemon=True,
        ).start()

    def _assess(self) -> None:
        """Run AI assessment of recent orchestrator activity."""
        if not self._assess_lock.acquire(blocking=False):
            return  # another assessment is already running

        try:
            self._do_assess()
        finally:
            self._assess_lock.release()

    def _get_agent(self):
        """Lazily create and cache the pydantic-ai agent."""
        if self._agent is None:
            from pydantic_ai import Agent as PydanticAgent, Tool

            from kodo.models import make_fresh_model, resolve_model

            model_str = resolve_model(self._model) if self._model else _COACH_DEFAULT_MODEL
            queue = self._queue

            def send_feedback(message: str, priority: str = "warning") -> str:
                """Send feedback to the orchestrator. Only use when something is clearly wrong."""
                if priority not in ("info", "warning", "correction"):
                    priority = "warning"
                queue.push(message, source="coach", priority=priority)
                return "Delivered."

            self._agent = PydanticAgent(
                make_fresh_model(model_str),
                system_prompt=COACH_SYSTEM_PROMPT.format(tenets=self._tenets),
                tools=[Tool(send_feedback, takes_ctx=False)],
            )
        return self._agent

    def _do_assess(self) -> None:
        event_msg = self._build_event_message()
        if not event_msg:
            return

        if not self._message_history:
            event_msg = (
                f"# Goal\n{self._goal}\n\n"
                f"I'll send you each agent dispatch and result as they happen.\n\n"
                f"{event_msg}"
            )

        pending_before = self._queue.pending_count

        try:
            agent = self._get_agent()
            result = agent.run_sync(
                event_msg,
                message_history=self._message_history or None,
            )
            self._message_history = list(result.all_messages())

            if self._queue.pending_count == pending_before:
                log.emit("coach_assess_ok", dispatches=len(self._dispatches))
        except BaseException as exc:
            log.emit("coach_assess_error", error=f"{type(exc).__name__}: {exc}")

    def _build_event_message(self) -> str:
        """Build a message for the latest dispatch+result pair."""
        r = self._results[-1] if self._results else None
        if r is None:
            return ""

        n = len(self._results)
        status = "ERROR" if r["is_error"] else "ok"
        report = r.get("report", "")

        parts = [f"[dispatch #{n}] ask_{r['agent']}({r['task'][:300]})"]
        parts.append(f"Status: {status}")
        if report:
            parts.append(report[:1500])

        # Add pattern summary on first assessment or every 5 dispatches
        if not self._message_history or n % 5 == 0 or n == 1:
            task_counts: dict[str, int] = {}
            for d in self._dispatches:
                key = d["task"][:80]
                task_counts[key] = task_counts.get(key, 0) + 1
            repeated = {k: v for k, v in task_counts.items() if v >= 2}
            if repeated:
                parts.append("\nRepeated tasks so far:")
                for task, count in sorted(repeated.items(), key=lambda x: -x[1])[:3]:
                    parts.append(f"  {count}x: {task}")
            parts.append(f"\n({n} dispatches total, {len(self._errors)} errors)")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Alternative: structured-output coach (for comparison)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Filtered coach — single coach with a gating filter
# ---------------------------------------------------------------------------

_FILTER_PROMPT = """\
Gate this coach→orchestrator message.

Event: {event}
Proposed: {message}

BLOCK if:
- About truncated output, formatting, or prompt length issues
- Just praises, summarizes, or restates what already happened
- Orchestrator can already see this from the agent output

PASS if it suggests a different DIRECTION or flags a strategic gap. \
Mentioning tools or approaches by name is fine — that's direction, not micromanagement.
"""

_FILTER_DEFAULT_MODEL = "anthropic:claude-haiku-4-5-20251001"


class FilterVerdict(BaseModel):
    """Filter's decision on proposed coach feedback."""

    decision: Literal["pass", "block"]
    reason: str = ""


class FilteredCoach(Coach):
    """Coach with a filter agent that gates feedback quality.

    When the coach calls send_feedback, a filter agent reviews the message.
    If blocked, the reason is returned to the coach as the tool result,
    teaching it what not to say in future assessments within the same session.
    """

    def __init__(self, *args, filter_model: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._filter_model = filter_model
        self._filter_agent = None
        self._current_event = ""

    def _get_filter_agent(self):
        if self._filter_agent is None:
            from pydantic_ai import Agent as PydanticAgent

            from kodo.models import make_fresh_model

            model_str = self._filter_model or _FILTER_DEFAULT_MODEL
            self._filter_agent = PydanticAgent(
                make_fresh_model(model_str),
                output_type=FilterVerdict,
            )
        return self._filter_agent

    def _filter_message(self, message: str) -> str | None:
        """Filter a proposed message. Returns None if accepted, reason string if blocked."""
        try:
            agent = self._get_filter_agent()
            result = agent.run_sync(
                _FILTER_PROMPT.format(
                    event=self._current_event[:2000],
                    message=message,
                ),
            )
            if result.output.decision == "pass":
                return None
            return result.output.reason or "Not relevant enough."
        except Exception:
            return None  # on error, let it through

    def _get_agent(self):
        """Create coach agent where send_feedback goes through the filter."""
        if self._agent is None:
            from pydantic_ai import Agent as PydanticAgent, Tool

            from kodo.models import make_fresh_model, resolve_model

            model_str = resolve_model(self._model) if self._model else _COACH_DEFAULT_MODEL
            queue = self._queue
            coach_self = self

            def send_feedback(message: str, priority: str = "warning") -> str:
                """Send feedback to the orchestrator. Only use when something is clearly wrong."""
                if priority not in ("info", "warning", "correction"):
                    priority = "warning"
                block_reason = coach_self._filter_message(message)
                if block_reason:
                    log.emit("coach_filtered", reason=block_reason[:200])
                    return f"Not delivered. Reason: {block_reason}"
                queue.push(message, source="coach", priority=priority)
                return "Delivered."

            self._agent = PydanticAgent(
                make_fresh_model(model_str),
                system_prompt=COACH_SYSTEM_PROMPT.format(tenets=coach_self._tenets),
                tools=[Tool(send_feedback, takes_ctx=False)],
            )
        return self._agent

    def _do_assess(self) -> None:
        # Store current event for filter access before parent runs assessment
        self._current_event = self._build_event_message()
        super()._do_assess()
