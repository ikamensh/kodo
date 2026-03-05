"""Dynamic team designer — generates agent roles per task.

The orchestrator analyzes the goal and constructs a team with custom
system prompts tailored to the specific knowledge task.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic_ai import Agent as PydanticAgent

from kodo.knowledge.models import (
    AgentRole,
    KnowledgeGoal,
    PatternType,
    QuestionType,
    TeamDesign,
    _EFFORT_DEFAULTS,
)
from kodo.knowledge.prompts import TEAM_DESIGNER_PROMPT

if TYPE_CHECKING:
    pass


def design_team(
    goal: KnowledgeGoal,
    model: str,
) -> TeamDesign:
    """Use an LLM to design the agent team for a knowledge task.

    Args:
        goal: The user's knowledge goal.
        model: Pydantic-ai model string for the designer (should be cheap/fast).

    Returns:
        TeamDesign with roles, pattern, and rationale.
    """
    max_agents = _EFFORT_DEFAULTS[goal.effort]["max_agents"]

    prompt_parts = [f"## Goal\n{goal.goal}"]
    if goal.domain_hints:
        prompt_parts.append(f"## Domain hints\n{', '.join(goal.domain_hints)}")
    if goal.constraints:
        prompt_parts.append(f"## Constraints\n{', '.join(goal.constraints)}")
    if goal.output_format:
        prompt_parts.append(f"## Desired output format\n{goal.output_format}")
    if goal.reference_files:
        from pathlib import Path
        ref_names = [Path(f).name for f in goal.reference_files]
        prompt_parts.append(
            f"## Reference materials provided\n"
            f"The user has provided these reference files that agents can read "
            f"from the workspace (as ref_<stem>): {', '.join(ref_names)}.\n"
            f"Agents that need to work with this material should have "
            f"read_artifact in their tools list."
        )
    prompt_parts.append(f"## Effort level\n{goal.effort}")

    user_prompt = "\n\n".join(prompt_parts)

    agent = PydanticAgent(
        model,
        system_prompt=TEAM_DESIGNER_PROMPT.format(max_agents=max_agents),
    )
    result = agent.run_sync(user_prompt)
    return _parse_team_design(result.output)


def _parse_team_design(raw: str) -> TeamDesign:
    """Parse the LLM's JSON response into a TeamDesign."""
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    data = json.loads(text)

    roles = [
        AgentRole(
            name=r["name"],
            system_prompt=r["system_prompt"],
            model_preference=r.get("model_preference", "best"),
            tools=r.get("tools", []),
        )
        for r in data["roles"]
    ]

    return TeamDesign(
        roles=roles,
        pattern=PatternType(data["pattern"]),
        question_type=QuestionType(data["question_type"]),
        rationale=data.get("rationale", ""),
    )
