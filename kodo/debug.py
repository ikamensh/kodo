"""Debug mode: fully mocked backends for testing orchestration flow.

Each mock session gets a latin letter (A, B, C...) and responds
deterministically with Letter+Counter (e.g. A1, A2, A3).  It tracks
all Letter+Number tokens seen in its input.  On reset, state is cleared.

The orchestrator uses the real ApiOrchestrator with a pydantic-ai
FunctionModel that returns deterministic tool calls instead of LLM
responses.  This means the full orchestrator cycle() code runs
unmodified — system prompt, tool wiring, message history, retry logic
— just with a fake model making the decisions.

Usage::

    uv run python -m kodo --debug --goal "test goal"
"""

from __future__ import annotations

import json
import random
import re
import string
import threading
from pathlib import Path

from kodo.agent import Agent
from kodo.orchestrators.base import TeamConfig
from kodo.sessions.base import QueryResult, SessionStats


# ---------------------------------------------------------------------------
# Letter allocator
# ---------------------------------------------------------------------------


class LetterAllocator:
    """Assigns sequential latin letters (A, B, C...) to mock sessions."""

    def __init__(self) -> None:
        self._index = 0
        self._lock = threading.Lock()
        self._assignments: list[tuple[str, str]] = []  # (letter, role)

    def next(self, role: str = "") -> str:
        with self._lock:
            if self._index >= len(string.ascii_uppercase):
                raise RuntimeError("Exhausted all 26 letters")
            letter = string.ascii_uppercase[self._index]
            self._index += 1
            self._assignments.append((letter, role))
            return letter

    def reset(self) -> None:
        with self._lock:
            self._index = 0
            self._assignments.clear()

    @property
    def assignments(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._assignments)


_TOKEN_RE = re.compile(r"[A-Z]\d+")

# Module-level allocator, reset at the start of each debug run.
_allocator = LetterAllocator()


# ---------------------------------------------------------------------------
# Mock session (replaces agent LLM/CLI backends)
# ---------------------------------------------------------------------------


class MockSession:
    """Mock session that responds with Letter+Counter and tracks seen tokens."""

    def __init__(self, letter: str) -> None:
        self.letter = letter
        self._counter = 0
        self.seen_tokens: list[str] = []
        self._stats = SessionStats()
        self.model = f"mock-{letter}"

    @property
    def stats(self) -> SessionStats:
        return self._stats

    @property
    def cost_bucket(self) -> str:
        return "mock"

    @property
    def session_id(self) -> str | None:
        return f"mock-{self.letter}"

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        # Track all Letter+Number tokens in input
        tokens = _TOKEN_RE.findall(prompt)
        self.seen_tokens.extend(tokens)

        # Generate response
        self._counter += 1
        response = f"{self.letter}{self._counter}"

        self._stats.queries += 1

        return QueryResult(
            text=response,
            elapsed_s=0.01,
            turns=1,
            cost_usd=0.0,
        )

    def reset(self) -> None:
        self._counter = 0
        self.seen_tokens.clear()
        self._stats = SessionStats()

    def terminate(self) -> None:
        pass

    def close(self) -> None:
        pass

    def clone(self) -> "MockSession":
        return MockSession(self.letter)

    @property
    def generated_tokens(self) -> list[str]:
        """All tokens this session has generated so far."""
        return [f"{self.letter}{i}" for i in range(1, self._counter + 1)]


# ---------------------------------------------------------------------------
# Mock pydantic-ai model (replaces orchestrator LLM)
# ---------------------------------------------------------------------------


def build_mock_model(letter: str, agent_tool_names: list[str]):
    """Build a pydantic-ai FunctionModel that returns deterministic tool calls.

    The model function receives the exact same message history that a real
    LLM would see (system prompt, user prompt, tool results) and returns
    a tool call to a random agent.  After seeing enough exchanges, it
    calls done().

    The orchestrator's MockSession tracks tokens seen/generated, just like
    agent MockSessions do.
    """
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    session = MockSession(letter)

    def mock_model_fn(messages: list, _info: AgentInfo) -> ModelResponse:

        # Flatten the full message context into text so the mock session
        # can scan for Letter+Number tokens (tracking what the orchestrator
        # "sees" in its context).
        context_text = _flatten_messages(messages)
        session.query(context_text, Path("."), max_turns=1)
        token = f"{letter}{session._counter}"

        # Count only agent delegation calls (ask_X), not done tools.
        # Done tools (goal_done, end_cycle, raise_issue) must not be counted,
        # otherwise after goal_done we'd return goal_done again in a loop.
        prior_agent_calls = sum(
            1
            for msg in messages
            if isinstance(msg, ModelResponse)
            for part in msg.parts
            if isinstance(part, ToolCallPart) and part.tool_name in agent_tool_names
        )
        already_called_goal_done = any(
            isinstance(part, ToolCallPart) and part.tool_name == "goal_done"
            for msg in messages
            if isinstance(msg, ModelResponse)
            for part in msg.parts
        )

        # Reserve the last request for calling goal_done() (new done mode)
        request_budget = len(agent_tool_names) + 2  # sensible default
        if already_called_goal_done:
            return ModelResponse(parts=[TextPart(content="Goal accepted.")])
        if prior_agent_calls >= request_budget - 1:
            summary = f"Mock cycle complete after {prior_agent_calls} agent calls. Token: {token}"
            args_json = json.dumps({"summary": summary})
            return ModelResponse(
                parts=[ToolCallPart(tool_name="goal_done", args=args_json)],
            )

        # Pick a random agent tool
        tool_name = random.choice(agent_tool_names)
        task = f"Task from {token}: do the work"
        args_json = json.dumps({"task": task, "new_conversation": False})

        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool_name, args=args_json)],
        )

    model = FunctionModel(mock_model_fn, model_name=f"mock-{letter}")
    return model, session


def _flatten_messages(messages: list) -> str:
    """Flatten pydantic-ai message history to text for token scanning."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )

    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if hasattr(part, "content") and isinstance(part.content, str):
                    parts.append(part.content[:2000])
                elif isinstance(part, ToolReturnPart):
                    parts.append(str(part.content)[:2000])
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    parts.append(part.content[:2000])
                elif isinstance(part, ToolCallPart):
                    args = part.args if isinstance(part.args, str) else json.dumps(part.args)
                    parts.append(f"{part.tool_name}({args[:500]})")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Mock summarizer
# ---------------------------------------------------------------------------


class MockSummarizer:
    """No-op summarizer for debug mode."""

    def summarize(self, agent_name: str, task: str, report: str) -> None:
        pass

    def get_accumulated_summary(self) -> str:
        return ""

    def clear(self) -> None:
        pass

    def shutdown(self, wait: bool = True) -> None:
        pass


# ---------------------------------------------------------------------------
# Build mock orchestrator (real ApiOrchestrator + FunctionModel)
# ---------------------------------------------------------------------------


def build_mock_orchestrator(
    letter: str,
    team: TeamConfig,
    system_prompt: str | None = None,
):
    """Build a real ApiOrchestrator backed by a mock FunctionModel.

    The full ApiOrchestrator.cycle() runs unmodified — system prompt,
    build_cycle_prompt(), tool wiring, message history, retry logic.
    Only the model is fake.

    Returns (orchestrator, orchestrator_mock_session).
    """
    from kodo.orchestrators.api import ApiOrchestrator

    # Build the tool names that the orchestrator will expose
    agent_tool_names = [f"ask_{name}" for name in team]

    model, orch_session = build_mock_model(letter, agent_tool_names)

    # Create a real ApiOrchestrator but swap its model
    orchestrator = ApiOrchestrator(
        model=f"mock-{letter}",
        system_prompt=system_prompt,
        max_context_tokens=None,  # no summarization in debug
    )
    # Replace the pydantic-ai model string with our FunctionModel
    orchestrator._pydantic_model = model
    orchestrator._summarizer = MockSummarizer()

    return orchestrator, orch_session


# ---------------------------------------------------------------------------
# Team builder
# ---------------------------------------------------------------------------


_TEAM_ROLES: dict[str, dict[str, str]] = {
    "full": {
        "worker_fast": "Fast coding agent (mock)",
        "worker_smart": "Smart reasoning agent (mock)",
        "tester": "Verification agent (mock)",
        "architect": "Code review agent (mock)",
    },
    "quick": {
        "worker_fast": "Fast coding agent (mock)",
        "worker_smart": "Smart reasoning agent (mock)",
    },
}


def build_debug_team(
    team_name: str = "full",
) -> tuple[TeamConfig, dict[str, MockSession]]:
    """Build a mock team matching the given preset. Returns (team, session_map)."""
    roles = _TEAM_ROLES.get(team_name, _TEAM_ROLES["full"])

    team: TeamConfig = {}
    session_map: dict[str, MockSession] = {}

    for role, description in roles.items():
        letter = _allocator.next(role)
        session = MockSession(letter)
        session_map[role] = session
        team[role] = Agent(session, description, max_turns=5, timeout_s=30)

    return team, session_map
