"""Knowledge orchestrator — the main entry point for knowledge work runs.

Builds on the existing ApiOrchestrator's pydantic-ai infrastructure but
with knowledge-specific team design, tools, and convergence logic.
"""

from __future__ import annotations

import time

import httpx
from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.exceptions import (
    ModelHTTPError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.usage import UsageLimits

from kodo import log
from kodo.agent import Agent
from kodo.knowledge.models import (
    ConvergenceState,
    KnowledgeGoal,
    KnowledgeResult,
    TeamDesign,
    Workspace,
    _EFFORT_DEFAULTS,
)
from kodo.knowledge.prompts import PATTERN_PROMPTS
from kodo.knowledge.team_designer import design_team
from kodo.knowledge.tools import build_knowledge_tools
from kodo.orchestrators.base import DoneSignal, apply_done_signal, CycleResult
from kodo.summarizer import Summarizer

from kodo.models import MODEL_PRICING, PYDANTIC_MODEL_MAP


class KnowledgeOrchestrator:
    """Orchestrates knowledge work using dynamically-designed agent teams."""

    def __init__(
        self,
        model: str = "claude-opus-4-7",
        designer_model: str | None = None,
        agent_model: str | None = None,
        max_context_tokens: int = 100_000,
    ):
        self.model = model
        self._pydantic_model = PYDANTIC_MODEL_MAP.get(model, model)
        # Designer can be cheap — it just picks team structure
        self._designer_model = PYDANTIC_MODEL_MAP.get(
            designer_model or model,
            designer_model or self._pydantic_model,
        )
        # Agent model: what the worker agents use
        self._agent_model = agent_model or model
        self._max_context_tokens = max_context_tokens
        self._summarizer = Summarizer()

    def run(self, goal: KnowledgeGoal) -> KnowledgeResult:
        """Execute a complete knowledge work run.

        1. Design the team (LLM call)
        2. Build agent sessions
        3. Run the orchestration loop with convergence checking
        4. Return the final result
        """
        log.emit(
            "knowledge_run_start",
            goal=goal.goal,
            effort=goal.effort,
            domain_hints=goal.domain_hints,
        )
        log.tprint(f"\n[knowledge] Goal: {goal.goal[:200]}")
        log.tprint(f"[knowledge] Effort: {goal.effort}")

        # Step 1: Design the team
        log.tprint("[knowledge] Designing team...")
        team_design = design_team(goal, self._designer_model)
        log.tprint(
            f"[knowledge] Pattern: {team_design.pattern.value} "
            f"({len(team_design.roles)} agents)"
        )
        log.tprint(f"[knowledge] Rationale: {team_design.rationale[:200]}")
        for role in team_design.roles:
            log.tprint(f"  - {role.name} ({role.model_preference})")
        log.emit(
            "knowledge_team_designed",
            pattern=team_design.pattern.value,
            question_type=team_design.question_type.value,
            roles=[r.name for r in team_design.roles],
            rationale=team_design.rationale,
        )

        # Step 2: Create workspace and seed references
        workspace = Workspace()
        self._seed_references(goal, workspace)

        # Step 3: Build agents (with workspace access for tools)
        team = self._build_team(team_design, workspace)

        # Step 4: Run the loop
        try:
            result = self._run_loop(goal, team_design, team, workspace)
        finally:
            self._summarizer.shutdown()
            for agent in team.values():
                agent.close()

        log.emit(
            "knowledge_run_end",
            verdict=result.verdict_type,
            confidence=result.confidence,
            rounds=result.rounds_used,
            cost_usd=result.total_cost_usd,
        )
        return result

    @staticmethod
    def _seed_references(goal: KnowledgeGoal, workspace: Workspace) -> None:
        """Load reference files into workspace as ref_* artifacts."""
        from pathlib import Path

        for path_str in goal.reference_files:
            path = Path(path_str).expanduser()
            if not path.is_file():
                log.tprint(f"[knowledge] Warning: reference file not found: {path}")
                continue
            try:
                content = path.read_text(errors="replace")
                artifact_name = f"ref_{path.stem}"
                workspace.write(artifact_name, content)
                log.tprint(
                    f"[knowledge] Loaded reference: {artifact_name} ({len(content)} chars)"
                )
            except Exception as exc:
                log.tprint(f"[knowledge] Warning: failed to read {path}: {exc}")

    def _build_team(
        self,
        design: TeamDesign,
        workspace: Workspace,
    ) -> dict[str, Agent]:
        """Construct Agent instances from the team design."""
        from kodo.knowledge.sessions import make_knowledge_session

        team: dict[str, Agent] = {}
        for role in design.roles:
            session = make_knowledge_session(
                role=role,
                default_model=self._agent_model,
                workspace=workspace,
            )
            team[role.name] = Agent(
                session=session,
                description=role.system_prompt[:200],
                max_turns=15,
                timeout_s=300,
            )
        return team

    def _run_loop(
        self,
        goal: KnowledgeGoal,
        design: TeamDesign,
        team: dict[str, Agent],
        workspace: Workspace,
    ) -> KnowledgeResult:
        """Main orchestration loop with convergence detection."""
        effort_cfg = _EFFORT_DEFAULTS[goal.effort]
        max_rounds = effort_cfg["max_rounds"]
        max_exchanges = 30  # per round

        convergence = ConvergenceState()
        total_cost = 0.0

        for round_num in range(1, max_rounds + 1):
            convergence.round_number = round_num
            log.tprint(f"\n{'=' * 50}")
            log.tprint(f"[knowledge] Round {round_num}/{max_rounds}")

            cycle_result = self._run_round(
                goal=goal,
                design=design,
                team=team,
                workspace=workspace,
                convergence=convergence,
                max_exchanges=max_exchanges,
            )
            total_cost += cycle_result.total_cost_usd

            # Auto-assess convergence after each round (don't rely on LLM)
            self._auto_assess_convergence(goal, workspace, convergence)

            if cycle_result.finished:
                log.tprint(f"[knowledge] Finished in round {round_num}")
                break

            if convergence.converged:
                log.tprint(
                    f"[knowledge] Converged (confidence={convergence.confidence:.2f}, "
                    f"stability={convergence.stability:.2f})"
                )
                break

            if round_num < max_rounds:
                log.tprint(
                    f"[knowledge] Not converged yet "
                    f"(confidence={convergence.confidence:.2f}). Continuing..."
                )

        # Build final result
        answer = (
            workspace.read("answer") or cycle_result.summary or "(no answer produced)"
        )
        reasoning = workspace.read("reasoning_trace") or ""
        open_questions = workspace.read("open_questions") or ""

        return KnowledgeResult(
            answer=answer,
            verdict_type=convergence.verdict_type,
            confidence=convergence.confidence,
            reasoning_trace=reasoning,
            open_questions=open_questions,
            rounds_used=convergence.round_number,
            total_cost_usd=total_cost,
            workspace=workspace,
        )

    def _auto_assess_convergence(
        self,
        goal: KnowledgeGoal,
        workspace: Workspace,
        convergence: ConvergenceState,
    ) -> None:
        """Assess convergence automatically after each round."""
        from kodo.knowledge.convergence import assess

        current = workspace.read("answer") or "(no answer yet)"
        previous = ""
        if convergence.history:
            previous = convergence.history[-1].get("answer_snapshot", "")

        result = assess(
            goal=goal.goal,
            current_answer=current,
            previous_answer=previous,
            round_number=convergence.round_number,
            model=self._pydantic_model,
        )

        convergence.confidence = result["confidence"]
        convergence.stability = result["stability"]
        convergence.agreement = result["agreement"]
        convergence.completeness = result["completeness"]

        convergence.history.append(
            {
                "round": convergence.round_number,
                "confidence": result["confidence"],
                "stability": result["stability"],
                "agreement": result["agreement"],
                "completeness": result["completeness"],
                "answer_snapshot": current[:2000],
            }
        )

        log.tprint(
            f"[knowledge] Assessment: confidence={result['confidence']:.2f}, "
            f"stability={result['stability']:.2f}, "
            f"completeness={result['completeness']:.2f}"
        )

    def _run_round(
        self,
        goal: KnowledgeGoal,
        design: TeamDesign,
        team: dict[str, Agent],
        workspace: Workspace,
        convergence: ConvergenceState,
        max_exchanges: int,
    ) -> CycleResult:
        """Run one round of the knowledge orchestration loop."""
        done_signal = DoneSignal()

        # Build tools
        tools = build_knowledge_tools(
            team=team,
            workspace=workspace,
            convergence=convergence,
            summarizer=self._summarizer,
            done_signal=done_signal,
        )

        # Build the orchestrator prompt
        team_desc = "\n".join(
            f"- **{r.name}**: {r.system_prompt[:150]}..." for r in design.roles
        )
        pattern_prompt = PATTERN_PROMPTS.get(
            design.pattern.value,
            PATTERN_PROMPTS["deepening"],  # fallback
        )
        system_prompt = pattern_prompt.format(team_description=team_desc)

        # Build user prompt
        user_parts = [f"## Goal\n{goal.goal}"]
        if goal.constraints:
            user_parts.append(
                "## Constraints\n" + "\n".join(f"- {c}" for c in goal.constraints)
            )
        if goal.output_format:
            user_parts.append(
                f"## Output format\n{goal.output_format}\n"
                f"The final 'answer' artifact MUST be in this format."
            )

        # List reference materials available
        ref_names = [n for n in workspace.list_artifacts() if n.startswith("ref_")]
        if ref_names:
            user_parts.append(
                "## Reference materials\n"
                "The following reference documents are available in the workspace. "
                "Have your agents read them with read_artifact before writing:\n"
                + "\n".join(f"- {n}" for n in ref_names)
            )

        workspace_state = workspace.snapshot(max_chars_per_artifact=300)
        if workspace_state != "(no artifacts yet)":
            user_parts.append(f"## Current workspace\n{workspace_state}")

        if convergence.round_number > 1 and convergence.history:
            last = convergence.history[-1]
            user_parts.append(
                f"## Previous round assessment\n"
                f"Confidence: {last.get('confidence', 0):.2f}, "
                f"Stability: {last.get('stability', 0):.2f}\n"
                f"Continue improving the answer."
            )

        user_prompt = "\n\n".join(user_parts)

        # Configure history processors for context management
        history_processors = []
        if self._max_context_tokens:
            from pydantic_ai_summarization import create_summarization_processor

            history_processors.append(
                create_summarization_processor(
                    trigger=("tokens", self._max_context_tokens),
                    keep=("tokens", self._max_context_tokens // 2),
                    model=self._pydantic_model,
                ),
            )

        # Create and run the pydantic-ai orchestrator agent
        agent = PydanticAgent(
            self._pydantic_model,
            system_prompt=system_prompt,
            tools=tools,
            history_processors=history_processors or None,
        )

        result = CycleResult()
        max_retries = 3
        run_result = None

        for attempt in range(max_retries):
            try:
                run_result = agent.run_sync(
                    user_prompt,
                    usage_limits=UsageLimits(request_limit=max_exchanges),
                )
                break
            except UsageLimitExceeded:
                log.tprint(f"[knowledge] Exchange limit reached ({max_exchanges})")
                break
            except UnexpectedModelBehavior as exc:
                log.tprint(f"[knowledge] Model output validation issue: {exc}")
                break
            except ModelHTTPError as exc:
                status = exc.status_code
                if status in (401, 403):
                    raise
                if (
                    status in (408, 429, 500, 502, 503, 504, 529)
                    and attempt < max_retries - 1
                ):
                    wait = 30 * (attempt + 1)
                    log.tprint(f"[knowledge] HTTP {status}, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise
            except (httpx.TimeoutException, httpx.ConnectError):
                if attempt < max_retries - 1:
                    wait = 30 * (attempt + 1)
                    log.tprint(f"[knowledge] Network error, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    raise

        # Extract cost
        if run_result is not None:
            usage = run_result.usage()
            price_in, price_out = MODEL_PRICING.get(self.model, (0, 0))
            result.total_cost_usd = (
                usage.input_tokens * price_in + usage.output_tokens * price_out
            ) / 1_000_000
            result.exchanges = usage.requests

        apply_done_signal(result, done_signal)
        self._summarizer.clear()

        return result
