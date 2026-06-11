"""Session factory for knowledge agents.

Knowledge agents use API sessions (not CLI sessions like Claude Code/Cursor).
They're lightweight wrappers around pydantic-ai that expose the Session protocol.

NOTE: Agent sessions are called from within the orchestrator's run_sync(),
which already holds an asyncio event loop.  To avoid nested-loop conflicts
we run each agent query in a dedicated thread with its own event loop.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai import Tool
from pydantic_ai.exceptions import ModelHTTPError

from kodo.models import make_fresh_model
from kodo.sessions.base import QueryResult, SessionStats
from kodo.utils import run_in_thread

if TYPE_CHECKING:
    from kodo.knowledge.models import AgentRole, Workspace

# Map model_preference to actual pydantic-ai model strings.
# Falls back gracefully: prefers Anthropic when available, Gemini otherwise.
_PREFERENCE_MAP: dict[str, str] = {
    "best": "google-gla:gemini-3.1-pro-preview",
    "fast": "google-gla:gemini-3.5-flash",
    "reasoning": "google-gla:gemini-3.1-pro-preview",
    # "search" and "compute" use "best" model but get extra tools
}


class ApiSession:
    """Session backed by a pydantic-ai Agent for knowledge work.

    Unlike code sessions (subprocess-based), this calls the API directly.
    Each query creates a fresh pydantic-ai run — no conversation continuity
    within the session (the orchestrator manages context via the workspace).
    """

    def __init__(
        self,
        model: str,
        system_prompt: str = "",
        tools: list[Tool] | None = None,
    ):
        self.model = model
        self._system_prompt = system_prompt
        self._tools = tools or []
        self._stats = SessionStats()

    @property
    def stats(self) -> SessionStats:
        return self._stats

    @property
    def cost_bucket(self) -> str:
        return "api"

    @property
    def session_id(self) -> str | None:
        return None

    def query(
        self,
        prompt: str,
        project_dir: Path,
        *,
        max_turns: int = 15,
    ) -> QueryResult:
        """Run a query in a dedicated thread with its own event loop.

        Agent sessions are called from within the orchestrator's run_sync()
        which already holds the main event loop.  We use a fresh thread +
        event loop + httpx client to avoid event-loop binding conflicts
        between the orchestrator's cached connections and agent connections.
        """
        start = time.time()

        model_str = self.model
        system_prompt = self._system_prompt
        tools = self._tools or None

        def _run():
            model = make_fresh_model(model_str)
            agent = PydanticAgent(model, system_prompt=system_prompt, tools=tools)
            return agent.run_sync(prompt)

        try:
            result = run_in_thread(_run, timeout=300)
        except Exception as exc:
            elapsed = time.time() - start
            # UnexpectedModelBehavior means the agent used tools but
            # couldn't produce a clean text output — treat as partial success.
            if "UnexpectedModelBehavior" in type(exc).__name__:
                self._stats.queries += 1
                return QueryResult(
                    text="(Agent completed tool work but output validation failed)",
                    elapsed_s=elapsed,
                )
            if isinstance(exc, ModelHTTPError):
                return QueryResult(
                    text=f"API error: {exc}",
                    elapsed_s=elapsed,
                    is_error=True,
                )
            if isinstance(exc, TimeoutError):
                return QueryResult(
                    text="Agent timed out after 300s",
                    elapsed_s=elapsed,
                    is_error=True,
                )
            return QueryResult(
                text=f"Error: {type(exc).__name__}: {exc}",
                elapsed_s=elapsed,
                is_error=True,
            )

        elapsed = time.time() - start
        usage = result.usage()
        inp = usage.input_tokens or 0
        out = usage.output_tokens or 0

        self._stats.queries += 1
        self._stats.total_input_tokens += inp
        self._stats.total_output_tokens += out

        return QueryResult(
            text=result.output or "",
            elapsed_s=elapsed,
            turns=usage.requests,
            input_tokens=inp,
            output_tokens=out,
        )

    def reset(self) -> None:
        self._stats = SessionStats()

    def terminate(self) -> None:
        pass  # API sessions have no persistent process

    def close(self) -> None:
        pass

    def clone(self) -> "ApiSession":
        return ApiSession(
            model=self.model,
            system_prompt=self._system_prompt,
            tools=list(self._tools),
        )


def make_knowledge_session(
    role: "AgentRole",
    default_model: str = "claude-opus-4-7",
    workspace: "Workspace | None" = None,
) -> ApiSession:
    """Create an API session for a knowledge agent role.

    Args:
        role: The dynamically-designed agent role.
        default_model: Fallback model if preference can't be resolved.
        workspace: Shared workspace for read/write artifact tools.
    """
    from kodo.models import PYDANTIC_MODEL_MAP

    # Resolve model from preference
    pydantic_model = _PREFERENCE_MAP.get(
        role.model_preference,
        PYDANTIC_MODEL_MAP.get(default_model, f"anthropic:{default_model}"),
    )

    # Build agent-level tools based on role.tools
    tools: list[Tool] = []
    if "compute" in role.tools:
        from kodo.knowledge.tools import _make_compute

        tools.append(Tool(_make_compute(), name="compute", takes_ctx=False))

    if workspace is not None:
        if "read_artifact" in role.tools:
            from kodo.knowledge.tools import _make_read_artifact

            tools.append(
                Tool(
                    _make_read_artifact(workspace),
                    name="read_artifact",
                    description="Read a knowledge artifact from the shared workspace.",
                    takes_ctx=False,
                )
            )
        if "write_artifact" in role.tools:
            from kodo.knowledge.tools import _make_write_artifact

            tools.append(
                Tool(
                    _make_write_artifact(workspace),
                    name="write_artifact",
                    description="Write or update a knowledge artifact.",
                    takes_ctx=False,
                )
            )

    return ApiSession(
        model=pydantic_model,
        system_prompt=role.system_prompt,
        tools=tools,
    )
