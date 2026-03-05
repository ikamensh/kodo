"""Claude session using claude-agent-sdk with conversation continuity."""

from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

from kodo import log
from kodo.env import anthropic_env_lock
from kodo.models import CLAUDE_SONNET
from kodo.sessions.base import QueryResult, SessionStats


def _extract_tokens(usage: dict | None) -> tuple[int | None, int | None]:
    """Pull input/output token counts from the raw usage dict."""
    if not usage:
        return None, None
    inp = usage.get("input_tokens") or usage.get("prompt_tokens")
    out = usage.get("output_tokens") or usage.get("completion_tokens")
    return inp, out


class ClaudeSession:
    def __init__(
        self,
        model: str = CLAUDE_SONNET,
        system_prompt: str | None = None,
        chrome: bool = False,
        fallback_model: str | None = None,
        use_api_key: bool = False,
        resume_session_id: str | None = None,
        session_timeout_s: int | None = None,
        effort: str | None = None,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.chrome = chrome
        self.fallback_model = fallback_model
        self.use_api_key = use_api_key
        self.resume_session_id = resume_session_id
        self._session_timeout_s = session_timeout_s
        self.effort = effort  # "low" | "standard" | "high" | "max" | None
        self._client = None
        self._project_dir: Path | None = None
        self._session_id: str | None = None
        self._stats = SessionStats()
        # Plan-mode review state: when a worker calls ExitPlanMode, we capture
        # the plan and interrupt so the orchestrator can review it.
        # _plan_approved is set only when the follow-up prompt signals approval
        # (contains "plan approved" or similar).  This ensures that asking for
        # revisions doesn't accidentally auto-approve the next ExitPlanMode.
        self._pending_plan: str | None = None
        self._plan_approved: bool = False
        # Dedicated thread+loop so we never conflict with a caller's event loop
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._closed = False
        self._close_lock = threading.Lock()

    def __enter__(self) -> "ClaudeSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    async def _can_use_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: Any,
    ) -> Any:
        """Handle tool permission requests.

        For ExitPlanMode we always capture the plan and deny the *first*
        call per query so the orchestrator can review it.  The flag
        ``_plan_approved`` is set only when the orchestrator's next prompt
        explicitly approves (see ``query()``).  This avoids auto-approving
        a revised plan when the orchestrator asked for changes.
        """
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        if tool_name == "ExitPlanMode":
            if self._plan_approved:
                # Orchestrator approved — let the worker proceed.
                self._plan_approved = False
                return PermissionResultAllow()
            # Capture plan text and interrupt so orchestrator can review.
            self._pending_plan = tool_input.get("plan", tool_input.get("content", ""))
            return PermissionResultDeny(
                message=(
                    "Plan submitted for orchestrator review. "
                    "Stop and wait for feedback."
                ),
                interrupt=True,
            )

        return PermissionResultAllow()

    _DEFAULT_QUERY_TIMEOUT: float = 7200  # 2 hours; prevents indefinite hangs

    @property
    def _query_timeout(self) -> float:
        """Effective query timeout: session_timeout_s if set, else default."""
        if self._session_timeout_s is not None:
            return float(self._session_timeout_s)
        return self._DEFAULT_QUERY_TIMEOUT

    def _run(self, coro, *, timeout: float | None = None):
        """Submit a coroutine to our background loop and block until it completes.
        If coro is None (sync client, e.g. mock), return immediately."""
        if coro is None:
            return
        if not self._thread.is_alive():
            raise RuntimeError(
                "Background event-loop thread is dead; cannot execute coroutine"
            )
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    @property
    def stats(self) -> SessionStats:
        return self._stats

    @property
    def cost_bucket(self) -> str:
        return "api" if self.use_api_key else "claude_subscription"

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def _ensure_client(self, project_dir: Path) -> None:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        if self._thread and not self._thread.is_alive():
            raise RuntimeError("Session is closed")
        if self._client is not None and self._project_dir == project_dir:
            return

        self._disconnect()
        self._project_dir = project_dir

        extra_args = {}
        if self.chrome:
            extra_args["--chrome"] = None

        # Hold the env lock only for env mutation and option/client construction.
        # The actual connect() I/O happens outside the lock to avoid blocking
        # other threads that need anthropic_env_lock.
        with anthropic_env_lock:
            # The SDK always starts with os.environ, so we must remove CLAUDECODE
            # from the actual environment to prevent nested-session detection.
            os.environ.pop("CLAUDECODE", None)

            # Unless explicitly opted in, strip ANTHROPIC_API_KEY so the SDK
            # session uses the Claude.ai subscription instead of API billing.
            saved_api_key = None
            if not self.use_api_key and "ANTHROPIC_API_KEY" in os.environ:
                saved_api_key = os.environ.pop("ANTHROPIC_API_KEY")

            resume_id = self.resume_session_id
            if resume_id:
                self.resume_session_id = None  # one-shot: first connect resumes

            options = ClaudeAgentOptions(
                permission_mode="bypassPermissions",
                cwd=project_dir,
                disallowed_tools=["AskUserQuestion"],
                model=self.model,
                fallback_model=self.fallback_model,
                extra_args=extra_args,
                debug_stderr=None,
                stderr=lambda msg: log.emit("claude_stderr", message=msg),
                can_use_tool=self._can_use_tool,
                **({"resume": resume_id} if resume_id else {}),
                **({"system_prompt": self.system_prompt} if self.system_prompt else {}),
                **({"effort": self.effort} if self.effort else {}),
            )
            self._client = ClaudeSDKClient(options=options)

        # connect() spawns the subprocess and does I/O — run outside the lock.
        try:
            self._run(self._client.connect(), timeout=120)
        finally:
            # Restore the key so the orchestrator's own API calls still work,
            # even if connect() failed.
            with anthropic_env_lock:
                if saved_api_key is not None:
                    os.environ["ANTHROPIC_API_KEY"] = saved_api_key

    def _disconnect(self) -> None:
        if self._client is not None:
            try:
                self._run(self._client.disconnect(), timeout=10)
            except (RuntimeError, TimeoutError):
                pass  # cancel scope mismatch or disconnect hung — harmless
            self._client = None

    def clone(self) -> "ClaudeSession":
        """Create a fresh session with the same config but no state."""
        return ClaudeSession(
            model=self.model,
            system_prompt=self.system_prompt,
            chrome=self.chrome,
            fallback_model=self.fallback_model,
            use_api_key=self.use_api_key,
            session_timeout_s=self._session_timeout_s,
            effort=self.effort,
        )

    def _get_subprocess_pid(self) -> int | None:
        """Extract the PID of the SDK subprocess, if available."""
        try:
            transport = self._client._transport  # type: ignore[union-attr]
            if transport and transport._process:
                return transport._process.pid
        except (AttributeError, TypeError):
            pass
        return None

    def close(self) -> None:
        """Stop the event loop and join the background thread. Idempotent."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        # Grab subprocess PID before disconnect so we can force-kill if needed.
        subprocess_pid = self._get_subprocess_pid()

        try:
            self._disconnect()
        except (OSError, RuntimeError):
            pass  # best-effort; ensure stop/join/close always run

        # Cancel all pending asyncio tasks before stopping the loop so that
        # leaked Future objects (holding SDK client, response buffers, sockets)
        # are cleaned up instead of lingering in "pending" state forever.
        try:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                self._loop.call_soon_threadsafe(task.cancel)
            # Give tasks a moment to process cancellation callbacks.
            time.sleep(0.05)
        except RuntimeError:
            pass  # loop already closed/stopped

        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass  # loop already closed/stopped
        self._thread.join(timeout=5)
        if self._thread.is_alive() and subprocess_pid:
            # The SDK subprocess didn't exit in time — force-kill it so the
            # event-loop thread (blocked reading its stdout) can unblock.
            try:
                os.kill(subprocess_pid, signal.SIGTERM)
            except OSError:
                pass
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                try:
                    os.kill(subprocess_pid, signal.SIGKILL)
                except OSError:
                    pass
                self._thread.join(timeout=2)
        if self._thread.is_alive():
            # Fallback when subprocess_pid was None (SDK transport changed,
            # connect failed partway): give the thread 2s more to exit on its own.
            self._thread.join(timeout=2)
        if self._thread.is_alive():
            log.emit(
                "session_close_warning",
                session="claude",
                reason="thread_still_alive_after_kill",
            )
        try:
            self._loop.close()
        except (OSError, RuntimeError):
            pass  # best-effort cleanup

    def terminate(self) -> None:
        """Forcefully disconnect the SDK client.

        Called on timeout to stop a running query.  Safe to call when no
        query is in flight (delegates to ``_disconnect``).
        """
        self._disconnect()

    def reset(self) -> None:
        log.emit(
            "session_reset",
            session="claude",
            model=self.model,
            tokens_before=self._stats.total_tokens,
            queries_before=self._stats.queries,
        )
        self._disconnect()
        self._stats = SessionStats()

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        from claude_agent_sdk import AssistantMessage, ResultMessage
        from claude_agent_sdk.types import TextBlock, ToolUseBlock

        if self._loop.is_closed():
            raise RuntimeError("Session is closed")
        try:
            self._ensure_client(project_dir)
        except Exception as exc:
            # Connection failed (auth error, binary broken, etc.)
            # Clean up the broken client so the next call can retry.
            self._client = None
            self._project_dir = None
            exc_name = type(exc).__name__
            return QueryResult(
                text=(
                    f"Claude session failed to connect: {exc_name}: {exc}\n"
                    "Check that Claude Code is installed, authenticated, "
                    "and your subscription is active."
                ),
                elapsed_s=0.0,
                is_error=True,
            )

        # If a plan was captured in the previous query, check whether the
        # orchestrator is approving it or asking for revisions.  Only approve
        # ExitPlanMode when the prompt contains an approval signal; otherwise
        # the next ExitPlanMode will be captured again for another review.
        if self._pending_plan is not None:
            _APPROVAL_SIGNALS = [
                "plan approved",
                "proceed with",
                "go ahead",
                "i pick",
                "i choose",
                "implement",
                "let's go with",
            ]
            prompt_lower = prompt.lower()
            self._plan_approved = any(s in prompt_lower for s in _APPROVAL_SIGNALS)
            self._pending_plan = None

        log.emit(
            "session_query_start",
            session="claude",
            model=self.model,
            prompt=prompt,
            max_turns=max_turns,
            project_dir=str(project_dir),
        )

        t0 = time.monotonic()

        assert self._client is not None, "_ensure_client must succeed before query"
        try:
            self._run(self._client.query(prompt), timeout=self._query_timeout)

            result = QueryResult(text="", elapsed_s=0.0)
            # Collect text from AssistantMessage blocks as fallback when
            # ResultMessage.result is None (e.g. tool-use-heavy turns).
            assistant_texts: list[str] = []
            tool_uses: list[dict[str, Any]] = []

            async def _collect():
                nonlocal result
                assert self._client is not None
                async for message in self._client.receive_response():
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                assistant_texts.append(block.text)
                            elif isinstance(block, ToolUseBlock):
                                # Log tool name + input (file_path etc), not full content
                                tu_input = dict(block.input) if block.input else {}
                                # Strip large content fields to avoid JSONL bloat
                                for key in ("content", "new_string", "old_string", "new_source"):
                                    if key in tu_input and isinstance(tu_input[key], str) and len(tu_input[key]) > 200:
                                        tu_input[key] = tu_input[key][:200] + "...(truncated)"
                                tool_uses.append({"name": block.name, "input": tu_input})
                    elif isinstance(message, ResultMessage):
                        inp, out = _extract_tokens(message.usage)
                        result = QueryResult(
                            text=message.result or "",
                            elapsed_s=time.monotonic() - t0,
                            turns=message.num_turns,
                            cost_usd=message.total_cost_usd,
                            is_error=message.is_error,
                            input_tokens=inp,
                            output_tokens=out,
                            usage_raw=message.usage,
                        )
                        if hasattr(message, "session_id") and message.session_id:
                            self._session_id = message.session_id
                        self._stats.queries += 1
                        self._stats.total_input_tokens += inp or 0
                        self._stats.total_output_tokens += out or 0
                        self._stats.total_cost_usd += message.total_cost_usd or 0.0

            self._run(_collect(), timeout=self._query_timeout)

            # If ResultMessage had no text, use collected AssistantMessage texts.
            if not result.text and assistant_texts:
                result = QueryResult(
                    text="\n\n".join(assistant_texts),
                    elapsed_s=result.elapsed_s,
                    turns=result.turns,
                    cost_usd=result.cost_usd,
                    is_error=result.is_error,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    usage_raw=result.usage_raw,
                )

            # If a plan was captured during this query, prepend it to the result
            # so the orchestrator can see and review it.
            if self._pending_plan:
                result = QueryResult(
                    text=f"[PROPOSED PLAN]\n{self._pending_plan}\n\n"
                    f"[Agent is in plan mode, awaiting review]\n{result.text}",
                    elapsed_s=result.elapsed_s,
                    turns=result.turns,
                    cost_usd=result.cost_usd,
                    is_error=False,  # Not an error — plan review is expected
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    usage_raw=result.usage_raw,
                )
        except Exception as exc:
            elapsed = time.monotonic() - t0
            exc_name = type(exc).__name__
            log.emit(
                "session_query_error",
                session="claude",
                model=self.model,
                error=f"{exc_name}: {exc}",
            )
            return QueryResult(
                text=f"Claude session error during query: {exc_name}: {exc}",
                elapsed_s=elapsed,
                is_error=True,
            )

        log.emit(
            "session_query_end",
            session="claude",
            model=self.model,
            elapsed_s=result.elapsed_s,
            is_error=result.is_error,
            turns=result.turns,
            cost_usd=result.cost_usd,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            response_text=result.text,
            usage_raw=result.usage_raw,
            session_id=self._session_id,
            tool_uses=tool_uses if tool_uses else None,
        )
        return result
