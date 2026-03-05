"""Convergence assessment — replaces code-kodo's verification for knowledge work."""

from __future__ import annotations

import asyncio
import json
import threading

from pydantic_ai import Agent

from kodo.knowledge.prompts import CONVERGENCE_ASSESSOR_PROMPT


def assess(
    goal: str,
    current_answer: str,
    previous_answer: str,
    round_number: int,
    model: str,
) -> dict:
    """Ask an LLM to evaluate convergence between rounds.

    Returns dict with confidence, stability, agreement, completeness,
    should_continue, and reasoning.

    Uses a dedicated thread with a fresh model to avoid event loop conflicts
    when called from the orchestrator's tool handlers.
    """
    if round_number <= 1 or not previous_answer:
        # First round — can't assess stability yet
        return {
            "confidence": 0.3,
            "stability": 0.0,
            "agreement": 0.5,
            "completeness": 0.3,
            "should_continue": True,
            "reasoning": "First round — need at least one more iteration to assess convergence.",
        }

    prompt = CONVERGENCE_ASSESSOR_PROMPT.format(
        goal=goal,
        current_answer=current_answer[:5000],
        previous_answer=previous_answer[:5000],
        round_number=round_number,
    )

    result_holder: list = []
    error_holder: list = []

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            from kodo.knowledge.sessions import _make_fresh_model
            fresh_model = _make_fresh_model(model)
            agent = Agent(fresh_model, system_prompt="You are a convergence assessor. Respond only with valid JSON.")
            result = loop.run_until_complete(agent.run(prompt))
            result_holder.append(result)
        except BaseException as exc:
            error_holder.append(exc)
        finally:
            loop.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=120)

    if error_holder:
        return _fallback(f"Assessment error: {error_holder[0]}")
    if not result_holder:
        return _fallback("Assessment timed out")

    return _parse_assessment(result_holder[0].output)


def _fallback(reason: str) -> dict:
    return {
        "confidence": 0.5,
        "stability": 0.5,
        "agreement": 0.5,
        "completeness": 0.5,
        "should_continue": True,
        "reasoning": reason,
    }


def _parse_assessment(raw: str) -> dict:
    """Parse the LLM's convergence assessment."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Fallback: continue iterating
        return {
            "confidence": 0.5,
            "stability": 0.5,
            "agreement": 0.5,
            "completeness": 0.5,
            "should_continue": True,
            "reasoning": "Could not parse assessment — continuing by default.",
        }

    return {
        "confidence": float(data.get("confidence", 0.5)),
        "stability": float(data.get("stability", 0.5)),
        "agreement": float(data.get("agreement", 0.5)),
        "completeness": float(data.get("completeness", 0.5)),
        "should_continue": bool(data.get("should_continue", True)),
        "reasoning": str(data.get("reasoning", "")),
    }
