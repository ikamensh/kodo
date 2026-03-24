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
    ) -> None:
        self._queue = queue
        self._goal = goal
        self._project_dir = project_dir
        self._assess_every_n = assess_every_n
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
        while not self._stop.wait(timeout=3.0):
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

        # Add pattern summary every 5 dispatches for context
        if n % 5 == 0 or n == 1:
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


class CoachFeedback(BaseModel):
    """Coach response — empty feedback means all good."""

    feedback: str = ""
    priority: Literal["info", "warning", "correction"] = "warning"


_REVIEW_PROMPT = """\
Review this proposed coach feedback. The orchestrator sees all agent outputs directly.

Context: {event}
Proposed: {feedback}

Decide: accept (strategic insight orchestrator would miss), reject (noise/known), \
or revise (right direction, needs reframing — explain how).
"""

_REVISION_PROMPT = """\
Rewrite this feedback per reviewer guidance. Return ONLY the revised text.

Original: {feedback}
Guidance: {revision_guidance}
"""

_FINAL_REVIEW_PROMPT = """\
The author revised their feedback based on your guidance. Accept or reject.

Revised: {revised}
"""

_DUAL_COACH_SECONDARY_MODEL = "anthropic:claude-haiku-4-5-20251001"


class ReviewVerdict(BaseModel):
    """Reviewer's decision on proposed coach feedback."""

    decision: Literal["accept", "reject", "revise"]
    reason: str = ""


class DualCoach(Coach):
    """Coach with independent assessment + review protocol.

    Two models from different providers assess each event in parallel.
    Each proposal is reviewed by the other model, which can accept, reject,
    or ask for revisions. Revised feedback gets a final accept/reject.
    Both proposals can survive — up to two messages sent per event.
    """

    def __init__(self, *args, secondary_model: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._secondary_model = secondary_model
        self._secondary_history: list = []
        self._secondary_agent = None
        self._review_agents: dict[str, object] = {}  # model_str → cached agent
        self._revision_agents: dict[str, object] = {}

    def _make_assess_agent(self, model_str: str):
        """Create a coach agent with a non-pushing send_feedback tool (captures only)."""
        from pydantic_ai import Agent as PydanticAgent, Tool

        from kodo.models import make_fresh_model

        def send_feedback(message: str, priority: str = "warning") -> str:
            """Send feedback to the orchestrator. Only use when something is clearly wrong."""
            return "Noted."

        return PydanticAgent(
            make_fresh_model(model_str),
            system_prompt=COACH_SYSTEM_PROMPT.format(tenets=self._tenets),
            tools=[Tool(send_feedback, takes_ctx=False)],
        )

    def _get_secondary_agent(self):
        if self._secondary_agent is None:
            model_str = self._secondary_model or _DUAL_COACH_SECONDARY_MODEL
            self._secondary_agent = self._make_assess_agent(model_str)
        return self._secondary_agent

    def _get_review_agent(self, model_str: str):
        if model_str not in self._review_agents:
            from pydantic_ai import Agent as PydanticAgent

            from kodo.models import make_fresh_model

            self._review_agents[model_str] = PydanticAgent(
                make_fresh_model(model_str),
                output_type=ReviewVerdict,
            )
        return self._review_agents[model_str]

    def _get_revision_agent(self, model_str: str):
        if model_str not in self._revision_agents:
            from pydantic_ai import Agent as PydanticAgent

            from kodo.models import make_fresh_model

            self._revision_agents[model_str] = PydanticAgent(
                make_fresh_model(model_str),
                output_type=str,
            )
        return self._revision_agents[model_str]

    @staticmethod
    def _extract_feedback(result) -> str:
        """Extract the feedback message from tool calls in an agent result."""
        from pydantic_ai.messages import ModelResponse, ToolCallPart

        for msg in result.new_messages():
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if isinstance(part, ToolCallPart) and part.tool_name == "send_feedback":
                        args = part.args
                        if isinstance(args, dict):
                            return args.get("message", "")
        return ""

    def _do_assess(self) -> None:
        import concurrent.futures

        from kodo.models import resolve_model

        event_msg = self._build_event_message()
        if not event_msg:
            return

        if not self._message_history:
            full_event = (
                f"# Goal\n{self._goal}\n\n"
                f"I'll send you each agent dispatch and result as they happen.\n\n"
                f"{event_msg}"
            )
        else:
            full_event = event_msg

        primary_model_str = resolve_model(self._model) if self._model else _COACH_DEFAULT_MODEL
        secondary_model_str = self._secondary_model or _DUAL_COACH_SECONDARY_MODEL

        # Override primary agent to use non-pushing version for dual mode
        if self._agent is None:
            self._agent = self._make_assess_agent(primary_model_str)

        def _run(agent, event, history):
            result = agent.run_sync(event, message_history=history or None)
            return self._extract_feedback(result), list(result.all_messages())

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                fut_a = pool.submit(_run, self._agent, full_event, self._message_history)
                fut_b = pool.submit(_run, self._get_secondary_agent(), full_event, self._secondary_history)

                feedback_a, history_a = fut_a.result(timeout=120)
                feedback_b, history_b = fut_b.result(timeout=120)

            self._message_history = history_a
            self._secondary_history = history_b

            # Build proposals: (source_label, feedback, author_model, reviewer_model)
            proposals = []
            if feedback_a:
                proposals.append(("primary", feedback_a, primary_model_str, secondary_model_str))
            if feedback_b:
                proposals.append(("secondary", feedback_b, secondary_model_str, primary_model_str))

            if not proposals:
                log.emit("coach_assess_ok", dispatches=len(self._dispatches))
                return

            # Review phase: each proposal reviewed by the other model
            sent = 0
            for source, feedback, author_model, reviewer_model in proposals:
                accepted_feedback = self._review_proposal(
                    reviewer_model, author_model, event_msg, feedback,
                )
                if accepted_feedback:
                    self._queue.push(accepted_feedback, source="coach", priority="warning")
                    log.emit(
                        "coach_dual_sent",
                        source=source,
                        dispatches=len(self._dispatches),
                    )
                    sent += 1

            if not sent:
                log.emit(
                    "coach_dual_vetoed",
                    dispatches=len(self._dispatches),
                    proposals=len(proposals),
                )

        except BaseException as exc:
            log.emit("coach_assess_error", error=f"{type(exc).__name__}: {exc}")

    def _review_proposal(
        self,
        reviewer_model: str,
        author_model: str,
        event: str,
        feedback: str,
    ) -> str | None:
        """Run the review protocol. Returns accepted feedback text or None."""
        try:
            # Step 1: reviewer evaluates
            review_agent = self._get_review_agent(reviewer_model)
            result = review_agent.run_sync(
                _REVIEW_PROMPT.format(event=event[:2000], feedback=feedback),
            )
            verdict = result.output

            if verdict.decision == "accept":
                return feedback

            if verdict.decision == "reject":
                return None

            # Step 2: "revise" — author rewrites with reviewer's guidance
            revision_agent = self._get_revision_agent(author_model)
            revision_result = revision_agent.run_sync(
                _REVISION_PROMPT.format(
                    feedback=feedback,
                    revision_guidance=verdict.reason,
                ),
            )
            revised = revision_result.output.strip()

            if not revised:
                return None

            # Step 3: same reviewer does final accept/reject
            # Pass message_history from step 1 so it remembers its own guidance
            final_result = review_agent.run_sync(
                _FINAL_REVIEW_PROMPT.format(revised=revised),
                message_history=list(result.all_messages()),
            )
            if final_result.output.decision == "accept":
                return revised
            return None

        except Exception as exc:
            log.emit("coach_review_error", error=f"{type(exc).__name__}: {exc}")
            return None
