"""Goal collection, intake interview, and auto-refinement."""

import json
import re
import sys
from pathlib import Path

from kodo import log, make_session
from kodo.factory import has_claude, has_cursor, has_gemini_cli
from kodo.log import RunDir
from kodo.orchestrators.base import GoalPlan, GoalStage

from kodo.cli._ui import (
    _atomic_write,
    _print_agent,
    _print_separator,
    _Spinner,
    _GREEN,
    _BOLD,
    _DIM,
    _RESET,
)
from kodo.cli._params import _select_one


def _close_session(session) -> None:
    """Terminate and close a session, ignoring errors.

    Intake sessions are not wrapped in Agent so they lack automatic cleanup.
    This helper ensures subprocess-backed sessions don't leak child processes.
    """
    try:
        session.terminate()
    except Exception:
        pass
    if hasattr(session, "close"):
        try:
            session.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Intake prompts
# ---------------------------------------------------------------------------

_INTAKE_PREAMBLE = """\
You are refining a software project goal{purpose}.

Ask 2-3 clarifying questions about constraints, tech choices, and scope.
When clear enough, write {output}."""

_INTAKE_STAGES_SUFFIX = """

JSON format:
{{
  "context": "Shared context — tech stack, key files, conventions",
  "stages": [
    {{
      "index": 1,
      "name": "Short label",
      "description": "What this stage accomplishes",
      "acceptance_criteria": "Verifiable definition of done",
      "browser_testing": false
    }}
  ]
}}

Set browser_testing=true only for stages with web UI to verify in a browser.
Break into 2-5 independently verifiable stages, ordered by dependency."""

_PARALLELISM_PASS_PROMPT = """\
Now review the plan you just wrote and identify stages that can run in parallel.

Stages can be parallel when they:
- Touch different files or areas of the codebase
- Don't depend on each other's output
- Could be done by separate developers simultaneously

For each stage, decide:
- parallel_group: assign the same integer to stages that should run concurrently. \
null for sequential stages. Each parallel stage runs in its own git worktree.
- persist_changes: true if a parallel stage produces code changes that should \
be merged back into the main branch. false for read-only/exploration stages \
(testing, auditing, analysis).

Update the JSON file, adding parallel_group and persist_changes to each stage. \
If no stages can be parallelized, leave them all as null/false."""

_AUTO_REFINE_PROMPT = """\
Review this goal before implementation:

{goal}

Concisely answer (2-3 sentences each):
1. **Implicit constraints** — what does this goal imply that isn't stated?
2. **Simplest architecture** — one specific approach, not options.
3. **Common traps** — most likely over-engineering mistake?

Then write a refined goal to {output_path} incorporating the original intent \
plus implicit constraints. Keep it concise — an autonomous agent will read it.
"""

# TODO: The canned questions above are a starting point. Experiment with whether
# letting the LLM ask its own probing questions (rather than canned ones) produces
# better refinement. The hypothesis is that canned "is this the simplest
# architecture" almost never hurts, but LLM-generated questions might catch
# domain-specific traps that canned questions miss.


def _build_intake_prompt(output_path: str, staged: bool) -> str:
    """Build intake prompt with the correct output file path."""
    if staged:
        prompt = _INTAKE_PREAMBLE.format(
            purpose=" into an ordered list of stages",
            output=f"a structured goal plan to {output_path}",
        )
        return prompt + _INTAKE_STAGES_SUFFIX
    else:
        return _INTAKE_PREAMBLE.format(
            purpose="",
            output=f"a refined, detailed goal to {output_path}",
        )


# ---------------------------------------------------------------------------
# Goal input
# ---------------------------------------------------------------------------


def get_goal() -> str:
    """Prompt user for a multiline goal. Empty line finishes input."""
    print("\nWhat's your goal? (Enter an empty line to finish)")
    print("-" * 40)
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        print("No goal provided. Exiting.")
        sys.exit(1)
    return text


# ---------------------------------------------------------------------------
# Goal plan parsing
# ---------------------------------------------------------------------------


def _looks_staged(goal_text: str) -> bool:
    """Heuristic: detect if goal text has numbered steps or bullet lists."""
    numbered = re.findall(r"^\s*\d+[\.\)]\s+", goal_text, re.MULTILINE)
    return len(numbered) >= 2


def _parse_goal_plan(raw: dict) -> GoalPlan:
    """Convert a raw dict (from JSON) into a GoalPlan dataclass.

    Skips stages that are missing required fields rather than crashing.
    """
    context = raw.get("context")
    if not context:
        return GoalPlan(context="", stages=[])  # malformed input, return empty
    stages = []
    for s in raw.get("stages", []):
        if not isinstance(s, dict):
            continue
        index = s.get("index")
        name = s.get("name")
        description = s.get("description")
        acceptance_criteria = s.get("acceptance_criteria")
        if index is None or not name or not description or acceptance_criteria is None:
            continue
        stages.append(
            GoalStage(
                index=index,
                name=name,
                description=description,
                acceptance_criteria=acceptance_criteria,
                browser_testing=bool(s.get("browser_testing", False)),
                parallel_group=s.get("parallel_group"),
                persist_changes=bool(s.get("persist_changes", False)),
            )
        )
    return GoalPlan(context=context, stages=stages)


def _load_goal_plan(run_dir: RunDir) -> GoalPlan | None:
    """Load an existing goal-plan.json from the run directory."""
    plan_path = run_dir.goal_plan_file
    if not plan_path.exists():
        return None
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    plan = _parse_goal_plan(raw)
    return plan if plan.stages else None


# ---------------------------------------------------------------------------
# Interactive intake chat
# ---------------------------------------------------------------------------


def run_intake_chat(
    backend: str,
    run_dir: RunDir,
    goal_text: str,
    staged: bool = False,
) -> GoalPlan | str | None:
    """Interactive intake chat using the Session abstraction.

    Returns GoalPlan if staged + file written, refined goal string if
    single + file written, or None if user bailed.
    """
    run_dir.root.mkdir(parents=True, exist_ok=True)
    log.init(run_dir)

    goal_path = run_dir.goal_file
    _atomic_write(goal_path, goal_text)
    print(f"\nGoal saved to {goal_path}")

    output_file = run_dir.goal_plan_file if staged else run_dir.goal_refined_file
    prompt = _build_intake_prompt(str(output_file), staged)
    _intake_models = {
        "claude": "opus",
        "cursor": "composer-1.5",
        "gemini-cli": "gemini-2.5-flash",
    }
    model = _intake_models.get(backend, "composer-1.5")
    session = make_session(backend, model, system_prompt=prompt)

    try:
        print(
            f"\n  {_DIM}Intake interview — type {_BOLD}/done{_RESET}{_DIM} or empty line to finish{_RESET}"
        )
        _print_separator()

        # First message — agent explores the project and asks clarifying questions
        project_dir = run_dir.project_dir
        initial = f"Here's my project goal:\n\n{goal_text}"
        with _Spinner("Reviewing project"):
            result = session.query(initial, project_dir, max_turns=10)
        log.emit(
            "intake_response",
            text=result.text,
            is_error=result.is_error,
            turns=result.turns,
        )
        _print_agent(result.text)

        # If the agent already wrote the output file on the first turn,
        # skip the conversation loop — no need to make the user type /done.
        if output_file.exists():
            _print_separator()
            return _read_intake_output(
                output_file, staged,
                session=session if staged else None,
                project_dir=project_dir if staged else None,
            )

        # Conversation loop
        while True:
            try:
                user_input = input(f"  {_GREEN}{_BOLD}>{_RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not user_input or user_input == "/done":
                break

            try:
                with _Spinner("Thinking"):
                    result = session.query(user_input, project_dir, max_turns=10)
            except Exception as exc:
                error_msg = f"Session error: {exc}"
                log.emit("intake_response", text=error_msg, is_error=True, turns=0)
                _print_agent(error_msg)
                continue
            log.emit(
                "intake_response",
                text=result.text,
                is_error=result.is_error,
                turns=result.turns,
            )
            _print_agent(result.text)

        _print_separator()

        # Check if output was written during the conversation
        if output_file.exists():
            return _read_intake_output(
                output_file, staged,
                session=session if staged else None,
                project_dir=project_dir if staged else None,
            )

        # User ended the interview — ask Claude to finalize and write the output
        finalize_msg = (
            "The user has ended the interview. Based on everything discussed, "
            "please write the output file now."
        )
        with _Spinner("Finalizing"):
            result = session.query(finalize_msg, project_dir, max_turns=10)
        _print_agent(result.text)

        if output_file.exists():
            return _read_intake_output(
                output_file, staged,
                session=session if staged else None,
                project_dir=project_dir if staged else None,
            )

        print("\nNo output file written; using original goal.")
        return None
    finally:
        _close_session(session)


def _run_parallelism_pass(session, output_file: Path, project_dir: Path) -> None:
    """Second turn: ask the LLM to annotate the sequential plan with parallelism.

    Sends ``_PARALLELISM_PASS_PROMPT`` in the same session so the LLM has full
    context from the planning conversation.  The LLM overwrites the plan file
    with updated ``parallel_group`` / ``persist_changes`` fields.
    """
    try:
        with _Spinner("Identifying parallelism"):
            session.query(_PARALLELISM_PASS_PROMPT, project_dir, max_turns=10)
    except Exception as exc:
        print(f"\n  Parallelism pass failed ({exc}), using sequential plan.")


def _read_intake_output(
    output_file: Path,
    staged: bool,
    *,
    session=None,
    project_dir: Path | None = None,
) -> GoalPlan | str | None:
    """Read the intake output file and return the appropriate type.

    When *session* and *project_dir* are provided for a staged plan, runs a
    parallelism pass (second LLM turn) to annotate stages with
    ``parallel_group`` and ``persist_changes`` before parsing.
    """
    if staged:
        if session is not None and project_dir is not None:
            _run_parallelism_pass(session, output_file, project_dir)
        try:
            raw = json.loads(output_file.read_text(encoding="utf-8"))
            plan = _parse_goal_plan(raw)
            if plan.stages:
                print(f"\nGoal plan read from {output_file}")
                print(f"  {len(plan.stages)} stage(s):")
                for s in plan.stages:
                    pg = f" [parallel group {s.parallel_group}]" if s.parallel_group is not None else ""
                    pc = " [persist]" if s.persist_changes else ""
                    print(f"    {s.index}. {s.name}{pg}{pc}")
                return plan
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"\nWarning: could not read {output_file}: {exc}")
        return None
    else:
        try:
            refined = output_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None
        if refined:
            print(f"\nRefined goal read from {output_file}")
            return refined
        return None


# ---------------------------------------------------------------------------
# Auto-refinement (no human in the loop)
# ---------------------------------------------------------------------------


def run_intake_auto(
    backend: str,
    run_dir: RunDir,
    goal_text: str,
) -> str | None:
    """Automated goal refinement — no human in the loop.

    Uses the same session as interactive intake but sends a single structured
    prompt instead of a conversation. Returns the refined goal string, or None
    if refinement failed.
    """
    run_dir.root.mkdir(parents=True, exist_ok=True)

    goal_path = run_dir.goal_file
    _atomic_write(goal_path, goal_text)

    output_file = run_dir.goal_refined_file
    prompt = _AUTO_REFINE_PROMPT.format(goal=goal_text, output_path=str(output_file))
    _refine_models = {
        "claude": "opus",
        "cursor": "composer-1.5",
        "gemini-cli": "gemini-2.5-flash",
    }
    model = _refine_models.get(backend, "composer-1.5")
    session = make_session(backend, model, system_prompt=prompt)

    try:
        project_dir = run_dir.project_dir
        print("\n--- Auto-refining goal (no human input) ---")

        with _Spinner("Analyzing goal"):
            result = session.query(
                f"Here's the project goal to analyze:\n\n{goal_text}",
                project_dir,
                max_turns=10,
            )

        print(f"\n{result.text}\n")

        if output_file.exists():
            try:
                refined = output_file.read_text(encoding="utf-8").strip()
            except OSError:
                refined = ""
            if refined:
                print(f"Refined goal written to {output_file}")
                return refined

        # LLM didn't write the file — use its response as the refinement
        analysis = (result.text or "").strip()
        if analysis:
            refined = f"{goal_text}\n\n# Pre-implementation analysis\n\n{analysis}"
            _atomic_write(output_file, refined)
            print(f"Refined goal written to {output_file}")
            return refined

        print("Auto-refinement produced no output; using original goal.")
        return None
    finally:
        _close_session(session)


# ---------------------------------------------------------------------------
# Non-interactive intake (single-turn, no conversation)
# ---------------------------------------------------------------------------


def run_single_turn_plan(
    run_dir: RunDir,
    system_prompt: str,
    initial_message: str,
    *,
    spinner_text: str = "Analyzing project and creating plan",
) -> GoalPlan | None:
    """Single-turn plan generation: send prompt, get GoalPlan JSON back.

    Shared by non-interactive intake and --improve discovery.  Picks the best
    available backend, creates a session with *system_prompt*, sends
    *initial_message*, and parses the resulting ``goal-plan.json``.
    """
    run_dir.root.mkdir(parents=True, exist_ok=True)

    if has_claude():
        backend, model = "claude", "opus"
    elif has_cursor():
        backend, model = "cursor", "composer-1.5"
    elif has_gemini_cli():
        backend, model = "gemini-cli", "gemini-2.5-flash"
    else:
        return None

    output_file = run_dir.goal_plan_file
    session = make_session(backend, model, system_prompt=system_prompt)

    try:
        project_dir = run_dir.project_dir
        with _Spinner(spinner_text):
            result = session.query(initial_message, project_dir, max_turns=10)
        print(f"\n{result.text}\n")

        if not output_file.exists():
            with _Spinner("Finalizing plan"):
                result = session.query(
                    "Please write the goal-plan.json file now based on your analysis.",
                    project_dir,
                    max_turns=10,
                )
            print(f"\n{result.text}\n")

        if output_file.exists():
            return _read_intake_output(
                output_file, staged=True,
                session=session, project_dir=project_dir,
            )

        return None
    finally:
        _close_session(session)


def run_intake_noninteractive(
    run_dir: RunDir,
    goal_text: str,
) -> GoalPlan | None:
    """Non-interactive intake: send goal, get staged plan back, no conversation."""
    goal_path = run_dir.goal_file
    _atomic_write(goal_path, goal_text)

    output_file = run_dir.goal_plan_file
    prompt = _build_intake_prompt(str(output_file), staged=True) + (
        "\n\nIMPORTANT: This is a non-interactive session. "
        "Do NOT ask clarifying questions. Analyze the project and goal, "
        "make reasonable assumptions, and write the goal-plan.json file immediately."
    )

    print("Running intake (non-interactive)...")
    plan = run_single_turn_plan(
        run_dir,
        system_prompt=prompt,
        initial_message=f"Here's my project goal:\n\n{goal_text}",
    )
    if plan is None:
        print("Warning: intake did not produce a plan. Proceeding without stages.")
    return plan


# ---------------------------------------------------------------------------
# Offer intake menu (interactive flow)
# ---------------------------------------------------------------------------


def _first_backend() -> str | None:
    """Return the first available backend key, or None."""
    if has_claude():
        return "claude"
    if has_cursor():
        return "cursor"
    if has_gemini_cli():
        return "gemini-cli"
    return None


def _offer_intake(run_dir: RunDir, goal_text: str) -> GoalPlan | str | None:
    """Offer goal refinement before launch. Returns refined goal/plan or None."""
    backend = _first_backend()
    if not backend:
        print("\nSkipping refinement (no backends available).")
        return None

    options = [
        "Quick refine — surfaces implicit constraints, no conversation",
        "Interview — interactive Q&A, optionally break into stages",
        "Skip",
    ]
    choice = _select_one("\nRefine goal before launch?", options)
    if choice.startswith("Skip"):
        return None

    if choice.startswith("Quick"):
        refined = run_intake_auto(backend, run_dir, goal_text)
        return refined

    # Interview — let user pick backend if multiple are available
    backends: list[str] = []
    if has_claude():
        backends.append("Claude")
    if has_cursor():
        backends.append("Cursor")
    if has_gemini_cli():
        backends.append("Gemini CLI")

    if len(backends) > 1:
        _backend_map = {
            "Claude": "claude",
            "Cursor": "cursor",
            "Gemini CLI": "gemini-cli",
        }
        backend = _backend_map[_select_one("Interview backend:", backends)]

    staged = False
    if _looks_staged(goal_text):
        print("This goal looks like it has multiple steps.")
        stage_choice = input("Break into stages? [Y/n] ").strip().lower()
        staged = not stage_choice or stage_choice == "y"
    else:
        stage_choice = input("Break into stages? [y/N] ").strip().lower()
        staged = stage_choice == "y"

    return run_intake_chat(backend, run_dir, goal_text, staged=staged)
