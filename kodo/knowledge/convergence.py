"""Convergence assessment — replaces code-kodo's verification for knowledge work."""

from __future__ import annotations

import json

from pydantic_ai import Agent

from kodo.knowledge.prompts import CONVERGENCE_ASSESSOR_PROMPT
from kodo.models import make_fresh_model
from kodo.utils import run_in_thread, strip_markdown_fences


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

    def _run():
        fresh_model = make_fresh_model(model)
        agent = Agent(fresh_model, system_prompt="You are a convergence assessor. Respond only with valid JSON.")
        return agent.run_sync(prompt)

    try:
        result = run_in_thread(_run, timeout=120)
    except Exception as exc:
        return _fallback(f"Assessment error: {exc}")

    return _parse_assessment(result.output)


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
    text = strip_markdown_fences(raw)

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
