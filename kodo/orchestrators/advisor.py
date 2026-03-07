"""Adaptive planning advisor — re-assesses plan between stages."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent as PydanticAgent

from kodo import log
from kodo.orchestrators.base import GoalPlan, GoalStage

DEFAULT_MAX_STAGES = 20

ADVISOR_SYSTEM_PROMPT = """\
You are a planning advisor for a software engineering team. Your job is to decide
what the team should work on NEXT, based on progress so far.

You receive:
1. The original user goal
2. Project context (tech stack, conventions)
3. Summaries of completed stages
4. The original plan (if any) as a reference — you are NOT bound to follow it

Your options:
- "next_stage": Define the next concrete, independently-verifiable stage of work.
  Keep stages small and focused (1-2 cycles of work each).
  Each stage should produce a testable, verifiable outcome.
- "done": The original goal has been fully achieved. Provide a summary of what
  was accomplished.

Guidelines:
- Re-assess after EVERY stage. Don't blindly follow the original plan.
- If a stage produced unexpected results or revealed new requirements, adapt.
- If the goal is simpler than expected, finish early — don't add busywork.
- If new work was discovered during a stage, create a stage for it.
- Keep stage descriptions focused on WHAT to achieve, not HOW to code it.
- Acceptance criteria must be concretely verifiable (tests pass, behavior works),
  not subjective ("code is clean").
- Prefer completing the most impactful work first.
"""


class AdvisorDecision(BaseModel):
    """Structured output from the advisor."""

    action: Literal["next_stage", "done"]
    # For "next_stage":
    stage_name: str | None = None
    stage_description: str | None = None
    acceptance_criteria: str | None = None
    browser_testing: bool = False
    # For "done":
    summary: str | None = None
    # Reasoning visible in logs
    reasoning: str = ""


class Advisor:
    """Adaptive planning advisor using pydantic-ai.

    Called between stages to decide what the team should work on next.
    Each call gets the full state via the prompt (goal + completed summaries),
    so no conversation history is needed.
    """

    def __init__(
        self,
        model: str,
        max_stages: int = DEFAULT_MAX_STAGES,
    ):
        self.model = model
        self.max_stages = max_stages
        self._agent = PydanticAgent(
            model,
            system_prompt=ADVISOR_SYSTEM_PROMPT,
            output_type=AdvisorDecision,
        )

    def assess(
        self,
        goal: str,
        plan: GoalPlan,
        completed_summaries: list[str],
        completed_count: int,
    ) -> AdvisorDecision:
        """Ask the advisor what to do next. Returns a structured decision."""
        prompt = _build_assess_prompt(
            goal, plan, completed_summaries, completed_count, self.max_stages,
        )

        log.emit(
            "advisor_assess_start",
            completed_stages=completed_count,
            max_stages=self.max_stages,
        )

        result = self._agent.run_sync(prompt)

        decision = result.output

        log.emit(
            "advisor_assess_end",
            action=decision.action,
            stage_name=decision.stage_name,
            reasoning=decision.reasoning[:500],
        )

        return decision

    @staticmethod
    def make_stage(decision: AdvisorDecision, next_index: int) -> GoalStage:
        """Convert an AdvisorDecision into a GoalStage."""
        return GoalStage(
            index=next_index,
            name=decision.stage_name or f"Stage {next_index}",
            description=decision.stage_description or "",
            acceptance_criteria=decision.acceptance_criteria or "",
            browser_testing=decision.browser_testing,
        )

    def close(self) -> None:
        """Release resources. No-op for pydantic-ai advisor."""


class SessionAdvisor(Advisor):
    """Advisor backed by an existing intake session.

    The intake session already has full planning context from the conversation,
    so assessment prompts are lightweight — just feed stage results and ask
    what's next.
    """

    def __init__(self, session, project_dir: Path, *, max_stages: int = DEFAULT_MAX_STAGES):
        # Skip Advisor.__init__ — we don't need a PydanticAgent
        self.max_stages = max_stages
        self.model = "session"
        self._session = session
        self._project_dir = project_dir
        self._started = False

    def assess(
        self,
        goal: str,
        plan: GoalPlan,
        completed_summaries: list[str],
        completed_count: int,
    ) -> AdvisorDecision:
        prompt = _build_session_assess_prompt(
            completed_summaries, completed_count, self._started,
        )
        self._started = True

        log.emit(
            "advisor_assess_start",
            completed_stages=completed_count,
            max_stages=self.max_stages,
            advisor_type="session",
        )

        result = self._session.query(prompt, self._project_dir, max_turns=3)

        if result.is_error:
            log.emit("advisor_assess_end", action="done", advisor_type="session",
                     error=result.text[:500])
            return AdvisorDecision(
                action="done",
                summary=f"Advisor session error: {result.text[:200]}",
            )

        decision = _parse_advisor_json(result.text)

        log.emit(
            "advisor_assess_end",
            action=decision.action,
            stage_name=decision.stage_name,
            reasoning=decision.reasoning[:500],
            advisor_type="session",
        )

        return decision

    def close(self) -> None:
        """Terminate and close the backing session."""
        try:
            self._session.terminate()
        except (OSError, RuntimeError) as e:
            log.emit("session_cleanup_warning", error=str(e))
        try:
            self._session.close()
        except (OSError, RuntimeError) as e:
            log.emit("session_cleanup_warning", error=str(e))


_SESSION_JSON_SCHEMA = """\
{
  "action": "next_stage" or "done",
  "stage_name": "short name (for next_stage)",
  "stage_description": "what to achieve (for next_stage)",
  "acceptance_criteria": "how to verify (for next_stage)",
  "browser_testing": false,
  "summary": "what was accomplished (for done)",
  "reasoning": "your reasoning"
}"""

_SESSION_TRANSITION_PROMPT = f"""\
Planning is complete. You are now the advisor for execution.

Between stages, I'll report what was accomplished and you'll decide what the \
team should work on next. Respond with ONLY a JSON object (no markdown, no \
explanation outside the JSON).

JSON schema:
{_SESSION_JSON_SCHEMA}

Review the plan we discussed and tell me what the team should work on first."""

_SESSION_SUBSEQUENT_TEMPLATE = """\
Stage {n} completed.
Summary: {summary}

What should the team do next? Respond with ONLY a JSON object.

JSON schema:
{schema}"""


def _build_session_assess_prompt(
    completed_summaries: list[str],
    completed_count: int,
    started: bool,
) -> str:
    """Build a lightweight prompt for the session advisor."""
    if not started:
        return _SESSION_TRANSITION_PROMPT

    summary = completed_summaries[-1] if completed_summaries else "(no summary)"
    # Truncate to avoid bloating the session context
    if len(summary) > 2000:
        summary = summary[:2000] + "…"
    return _SESSION_SUBSEQUENT_TEMPLATE.format(
        n=completed_count,
        summary=summary,
        schema=_SESSION_JSON_SCHEMA,
    )


def _parse_advisor_json(text: str) -> AdvisorDecision:
    """Extract an AdvisorDecision from session response text.

    Tries, in order:
    1. Parse the whole text as JSON
    2. Extract from ```json ... ``` code fence
    3. Find outermost { ... } containing "action"
    4. Fallback: return "done"
    """
    if not text or not text.strip():
        return AdvisorDecision(action="done", summary="Empty advisor response")

    # 1. Try whole text
    try:
        return AdvisorDecision.model_validate_json(text.strip())
    except Exception:
        pass

    # 2. Try code fence
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        try:
            return AdvisorDecision.model_validate_json(fence_match.group(1).strip())
        except Exception:
            pass

    # 3. Find outermost { ... } with "action"
    brace_match = re.search(r"\{[^{}]*\"action\"[^{}]*\}", text, re.DOTALL)
    if brace_match:
        try:
            return AdvisorDecision.model_validate_json(brace_match.group(0))
        except Exception:
            pass

    # 4. Fallback
    return AdvisorDecision(action="done", summary="Could not parse advisor response")


def _build_assess_prompt(
    goal: str,
    plan: GoalPlan,
    completed_summaries: list[str],
    completed_count: int,
    max_stages: int,
) -> str:
    """Build the assessment prompt for the advisor."""
    parts = [f"# Original Goal\n{goal}"]

    if plan.context:
        parts.append(f"# Project Context\n{plan.context}")

    if completed_summaries:
        parts.append("# Completed Stages")
        for i, summary in enumerate(completed_summaries, 1):
            parts.append(f"## Stage {i}\n{summary}")

    # Show remaining stages from original plan as reference
    remaining_original = [s for s in plan.stages if s.index > completed_count]
    if remaining_original:
        parts.append(
            "# Remaining Stages (original plan — for reference, not mandatory)"
        )
        for s in remaining_original:
            parts.append(f"- {s.name}: {s.description[:300]}")

    parts.append(
        f"\n{completed_count} stage(s) completed so far. "
        f"Safety limit: {max_stages} total stages. "
        f"What should the team do next?"
    )

    return "\n\n".join(parts)
