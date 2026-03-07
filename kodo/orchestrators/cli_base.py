"""Shared base for CLI-subprocess orchestrators (Cursor, Codex, Gemini CLI)."""

from __future__ import annotations

from pathlib import Path

from kodo import log
from kodo.prompts.roles import ORCHESTRATOR_SYSTEM_PROMPT
from kodo.orchestrators.base import (
    CycleConfig,
    CycleResult,
    DoneSignal,
    OrchestratorBase,
    TeamConfig,
    apply_done_signal,
    build_cycle_prompt,
)
from kodo.orchestrators.mcp_server import McpServerContext, build_mcp_server
from kodo.orchestrators.verification import VerificationState
from kodo.summarizer import Summarizer


class CliOrchestratorBase(OrchestratorBase):
    """Shared init, cycle preamble, and epilogue for CLI orchestrators.

    Subclasses implement ``_run_subprocess`` which receives the MCP
    context, prompt, and CycleResult to fill in — everything else
    (config defaulting, logging, MCP server lifecycle, done-signal
    handling, cycle epilogue) is handled here.

    Subclass contract
    -----------------
    - Set ``_orchestrator_name``, ``_cost_bucket``, and a default model
      constant as class-level or ``__init__`` attributes.
    - Implement ``_run_subprocess(ctx, full_prompt, project_dir,
      max_exchanges, result, done_signal)`` to spawn the CLI process,
      parse output, and populate ``result``.
    """

    _cost_bucket: str  # e.g. "cursor_subscription", "codex_subscription"

    def __init__(
        self,
        model: str,
        system_prompt: str | None = None,
    ):
        self.model = model
        self._system_prompt = system_prompt or ORCHESTRATOR_SYSTEM_PROMPT
        self._summarizer = Summarizer()

    def cycle(
        self,
        goal: str,
        project_dir: Path,
        team: TeamConfig,
        *,
        max_exchanges: int = 30,
        prior_summary: str = "",
        config: CycleConfig | None = None,
    ) -> CycleResult:
        if config is None:
            config = CycleConfig()

        log.emit(
            "cycle_start",
            orchestrator=self._orchestrator_name,
            model=self.model,
            goal=goal,
            project_dir=str(project_dir),
            max_exchanges=max_exchanges,
            has_prior_summary=bool(prior_summary),
            prior_summary=prior_summary or None,
        )

        done_signal = DoneSignal()
        verification_state = VerificationState()
        mcp = build_mcp_server(
            team,
            project_dir,
            self._summarizer,
            done_signal,
            goal,
            orchestrator_tag=self._orchestrator_name,
            verification_state=verification_state,
            config=config,
        )

        result = CycleResult()
        prompt = build_cycle_prompt(goal, project_dir, prior_summary)
        full_prompt = f"{self._system_prompt}\n\n{prompt}"

        with McpServerContext(mcp) as ctx:
            self._run_subprocess(
                ctx, full_prompt, project_dir, max_exchanges, result, done_signal,
            )

        return self._cycle_epilogue(
            result,
            cost_bucket=self._cost_bucket,
            context=f"{self._orchestrator_name} finished.",
        )

    def _run_subprocess(
        self,
        ctx: McpServerContext,
        full_prompt: str,
        project_dir: Path,
        max_exchanges: int,
        result: CycleResult,
        done_signal: DoneSignal,
    ) -> None:
        """Spawn the CLI process, parse output, populate *result*.

        Must be implemented by each subclass.
        """
        raise NotImplementedError

    def _apply_result(
        self,
        result: CycleResult,
        done_signal: DoneSignal,
        response_text: str,
        is_error: bool,
    ) -> None:
        """Shared post-subprocess logic: apply done signal, log, tprint.

        Call this at the end of ``_run_subprocess`` after parsing output.
        """
        apply_done_signal(result, done_signal)
        if not done_signal.called:
            result.summary = response_text

        log.emit(
            "orchestrator_response",
            orchestrator=self._orchestrator_name,
            is_error=is_error,
            result_text=response_text[:2000],
            done_called=done_signal.called,
        )

        if done_signal.called:
            log.tprint(
                f"✅ [orchestrator] cycle done (done tool called): "
                f"{done_signal.summary[:200]}",
            )
        elif is_error:
            log.tprint(
                f"⚠️  [orchestrator] {self._orchestrator_name} error: "
                f"{response_text[:200]}",
            )
        else:
            log.tprint("⏱️  [orchestrator] cycle ended without calling done")
