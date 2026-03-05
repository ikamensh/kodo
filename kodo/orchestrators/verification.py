"""Verification logic extracted from base.py."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kodo.agent import Agent
from kodo.formatting import DIM as _DIM, RESET as _RESET, plural as _plural
from kodo.prompts.roles import (
    CRITERIA_VERIFICATION_INSTRUCTIONS,
    VERIFICATION_INSTRUCTIONS,
    get_verification_effort_supplement,
)

if TYPE_CHECKING:
    from kodo.orchestrators.base import (
        CycleConfig,
        DoneSignal,
        QuickCheck,
        TeamConfig,
    )


@dataclass
class VerificationState:
    """Tracks verification attempts within a single cycle.

    Created fresh each ``cycle()`` call. First ``done()`` resets verifier
    sessions for a clean baseline; subsequent calls reuse the session so
    verifiers have persistent context from prior reviews.
    """

    done_attempt: int = 0


_SIGNAL = r"(?:ALL CHECKS PASS|MINOR ISSUES FIXED)"
_RE_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
_RE_INLINE_CODE = re.compile(r"`[^`]+`")
_RE_SINGLE_QUOTED = re.compile(r"'[^']*" + _SIGNAL + r"[^']*'")
_RE_DOUBLE_QUOTED = re.compile(r'"[^"]*' + _SIGNAL + r'[^"]*"')
_RE_SIGNAL = re.compile(_SIGNAL)
_MD_FMT = r"(?:[*_]{1,3})?"
_RE_SIGNAL_AUTHORITATIVE = re.compile(
    r"(?:^|(?<=\.)|(?<=!)|(?<=\?)|(?<=\u3002))\s*"
    + _MD_FMT
    + _SIGNAL
    + r"(?::|\b)",
    re.MULTILINE,
)


def _check_passed(report: str) -> bool:
    """Return True if a verifier report signals acceptance.

    Rejects reports containing "NOT ALL CHECKS PASS" or "NOT MINOR ISSUES FIXED".
    The signal phrase must appear as the verifier's own assertion — at the start
    of a line/sentence — not inside a quotation, attribution, or code block like
    ``The agent said 'ALL CHECKS PASS' but tests fail`` or inside triple backticks.
    """
    upper = report.upper()
    if "NOT ALL CHECKS PASS" in upper or "NOT MINOR ISSUES FIXED" in upper:
        return False

    # Strip fenced code blocks (```...```) — signals inside code are quotations,
    # not the verifier's own assertion.
    stripped = _RE_FENCED_CODE.sub("", upper)
    # Strip inline code (`...`)
    stripped = _RE_INLINE_CODE.sub("", stripped)
    # Strip single-quoted and double-quoted strings containing the signal phrase
    stripped = _RE_SINGLE_QUOTED.sub("", stripped)
    stripped = _RE_DOUBLE_QUOTED.sub("", stripped)

    # After stripping, reject if no signal phrase remains
    if not _RE_SIGNAL.search(stripped):
        return False

    # Accept the signal at: start of text, start of line, or after sentence-ending
    # punctuation (.!?。).  Reject mid-sentence mentions that follow attribution
    # words (said, say, cannot, etc.).
    return bool(_RE_SIGNAL_AUTHORITATIVE.search(stripped))


def _run_quick_checks(
    checks: list[QuickCheck],
) -> str | None:
    """Run lightweight file-existence checks.

    Returns None if all pass, or a combined error string if any fail.
    Substitutes ``{run_dir}`` in ``check.path`` with the current run directory.
    """
    from kodo import log as _log

    # Resolve {run_dir} placeholder using the active run directory
    log_file = _log.get_log_file()
    run_dir_str = str(log_file.parent) if log_file else ""

    failures: list[str] = []
    for check in checks:
        resolved_path = check.path.replace("{run_dir}", run_dir_str)
        if not Path(resolved_path).exists():
            failures.append(f"- {check.description}: {check.error_message}")
    if failures:
        return (
            "Quick-check verification failed:\n"
            + "\n".join(failures)
            + "\n\nFix these issues and try calling done again."
        )
    return None


def _build_verification_prompt(
    goal: str,
    summary: str,
    acceptance_criteria: str | None = None,
    effort: str = "standard",
) -> str:
    """Build the verification prompt, optionally with criteria checklist."""
    parts = [
        f"The orchestrator claims the following goal is complete:\n\n"
        f"# Goal\n{goal}\n\n"
        f"# Orchestrator's summary\n{summary}\n\n",
    ]

    if acceptance_criteria:
        parts.append(CRITERIA_VERIFICATION_INSTRUCTIONS)
        parts.append(f"## Acceptance Criteria\n{acceptance_criteria}\n")
    else:
        parts.append(VERIFICATION_INSTRUCTIONS)

    effort_supplement = get_verification_effort_supplement(effort)
    if effort_supplement:
        parts.append(effort_supplement)

    return "".join(parts)


def verify_done(
    goal: str,
    summary: str,
    team: TeamConfig,
    project_dir: Path,
    *,
    state: VerificationState | None = None,
    browser_testing: bool = False,
    verifiers: dict | None = None,
    acceptance_criteria: str | None = None,
    effort: str = "standard",
) -> str | None:
    """Run tester + architect to verify the goal is met.

    Returns None if all checks pass, or a rejection message with issues found.
    *browser_testing*: when False, browser testers are skipped even if configured.
    *verifiers*: optional dict with ``testers``, ``browser_testers``, ``reviewers``
    lists referencing agent keys in *team*.  When ``None``, falls back to legacy
    hardcoded key lookups (``"tester"``, ``"tester_browser"``, ``"architect"``).
    *acceptance_criteria*: when provided, the verification prompt enumerates
    criteria and requires per-criterion PASS/FAIL verdicts instead of the
    generic "honest assessment" instructions.
    *effort*: effort level ("standard", "high", "max") — higher levels add
    skepticism and thoroughness to the verification prompt.
    """
    from kodo import log

    if state is None:
        state = VerificationState()

    state.done_attempt += 1
    attempt = state.done_attempt
    reset_session = attempt == 1

    issues = []
    verification_prompt = _build_verification_prompt(
        goal, summary, acceptance_criteria, effort=effort,
    )

    # Resolve which agents to use for each verifier role
    if verifiers is not None:
        tester_keys = verifiers.get("testers", [])
        browser_tester_keys = verifiers.get("browser_testers", [])
        reviewer_keys = verifiers.get("reviewers", [])
    else:
        # Legacy fallback — same behavior as before
        tester_keys = ["tester"] if "tester" in team else []
        browser_tester_keys = ["tester_browser"] if "tester_browser" in team else []
        reviewer_keys = ["architect"] if "architect" in team else []

    # Collect tester agents — include browser testers only when requested
    tester_agents: list[tuple[str, Agent]] = []
    for key in tester_keys:
        if key in team:
            tester_agents.append((key, team[key]))
    if browser_testing:
        for key in browser_tester_keys:
            if key in team:
                tester_agents.append((key, team[key]))

    for tester_name, tester_agent in tester_agents:
        try:
            log.tprint(
                f"[done] running {tester_name} verification (attempt {attempt})...",
            )
            tester_result = tester_agent.run(
                verification_prompt,
                project_dir,
                new_conversation=reset_session,
                agent_name=f"{tester_name}_verification",
            )
            tester_report = tester_result.text or ""
            log.emit(
                "done_verification", agent=tester_name, report=tester_report[:5000],
            )
            if not _check_passed(tester_report):
                issues.append(
                    f"**{tester_name} found issues:**\n{tester_report[:3000]}",
                )
        except Exception as exc:
            log.emit("done_verification_error", agent=tester_name, error=str(exc))
            issues.append(f"**{tester_name} crashed:** {exc}")

    # Run reviewer agents
    for reviewer_key in reviewer_keys:
        reviewer_agent = team.get(reviewer_key)
        if not reviewer_agent:
            continue
        try:
            log.tprint(
                f"[done] running {reviewer_key} verification (attempt {attempt})...",
            )
            reviewer_result = reviewer_agent.run(
                verification_prompt,
                project_dir,
                new_conversation=reset_session,
                agent_name=f"{reviewer_key}_verification",
            )
            reviewer_report = reviewer_result.text or ""
            log.emit(
                "done_verification", agent=reviewer_key, report=reviewer_report[:5000],
            )
            if not _check_passed(reviewer_report):
                label = reviewer_key.replace("_", " ").title()
                issues.append(f"**{label} found issues:**\n{reviewer_report[:3000]}")
        except Exception as exc:
            log.emit("done_verification_error", agent=reviewer_key, error=str(exc))
            label = reviewer_key.replace("_", " ").title()
            issues.append(f"**{label} crashed:** {exc}")

    # Fallback: if no dedicated verifiers exist, use a worker in a fresh session
    has_dedicated_verifiers = (
        bool(tester_agents)
        or bool(
            browser_tester_keys,
        )  # exist even if skipped due to browser_testing=False
        or bool(reviewer_keys)
    )
    if not has_dedicated_verifiers:
        # Prefer worker_smart, fall back to any worker
        verifier = (
            team.get("worker_smart")
            or team.get("worker")
            or next((a for a in team.values()), None)
        )
        if verifier:
            verifier_name = next(
                (n for n, a in team.items() if a is verifier), "worker",
            )
            try:
                log.tprint(
                    f"[done] running {verifier_name} as verifier (fresh session)...",
                )
                verify_result = verifier.run(
                    verification_prompt,
                    project_dir,
                    new_conversation=True,
                    agent_name=f"{verifier_name}_verification",
                )
                verify_report = verify_result.text or ""
                log.emit(
                    "done_verification",
                    agent=verifier_name,
                    report=verify_report[:5000],
                )
                if not _check_passed(verify_report):
                    issues.append(
                        f"**{verifier_name} (verifier) found issues:**\n{verify_report[:3000]}",
                    )
            except Exception as exc:
                log.emit("done_verification_error", agent=verifier_name, error=str(exc))
                issues.append(f"**{verifier_name} (verifier) crashed:** {exc}")

    if issues:
        return (
            f"DONE REJECTED (attempt {attempt}) — verification found issues that must be fixed:\n\n"
            + "\n\n".join(issues)
            + "\n\nFix these issues and try calling done again."
        )
    return None


def handle_done(
    summary: str,
    success: bool,
    done_signal: DoneSignal,
    goal: str,
    team: TeamConfig,
    project_dir: Path,
    *,
    verification_state: VerificationState | None = None,
    orchestrator_tag: str | None = None,
    config: CycleConfig | None = None,
) -> str:
    """Shared done-handler logic for both orchestrators.

    Returns the string result to pass back to the orchestrator model.
    """
    from kodo import log
    from kodo.orchestrators.base import CycleConfig as _CycleConfig
    from kodo.orchestrators.base import _auto_commit

    if config is None:
        config = _CycleConfig()

    tag = {"orchestrator": orchestrator_tag} if orchestrator_tag else {}

    log.emit("orchestrator_done_attempt", **tag, summary=summary, success=success)
    log.tprint(f"🏁 [orchestrator] DONE requested (success={success}): {summary}")

    if not success:
        done_signal.called = True
        done_signal.summary = summary
        done_signal.success = False
        done_signal.terminal = "legacy"
        return "Acknowledged (marked as unsuccessful)."

    # Branch on verification mode
    rejection: str | None = None
    verification = config.verification
    if verification == "skip":
        log.tprint("⏩ [done] verification=skip — accepting immediately")
    elif isinstance(verification, list):
        log.tprint(
            f"⚡ [done] running {_plural(len(verification), 'quick check')}...",
        )
        rejection = _run_quick_checks(verification)
    else:
        # "full" — run verify_done with regex gate
        log.tprint("🔍 [done] running full verification...")
        rejection = verify_done(
            goal,
            summary,
            team,
            project_dir,
            state=verification_state,
            browser_testing=config.browser_testing,
            verifiers=config.verifiers,
            acceptance_criteria=config.acceptance_criteria,
            effort=config.effort,
        )

    if rejection:
        log.emit("orchestrator_done_rejected", **tag, rejection=rejection[:5000])
        log.tprint(f"❌ [done] {_DIM}REJECTED — verification found issues{_RESET}")
        return rejection

    # Auto-commit after successful verification
    if config.auto_commit:
        _auto_commit(team, project_dir, summary)

    done_signal.called = True
    done_signal.summary = summary
    done_signal.success = True
    done_signal.terminal = "legacy"
    log.emit("orchestrator_done_accepted", **tag, summary=summary)

    log.tprint("🎉 [done] ACCEPTED — all checks pass")
    return "Verified and accepted. All checks pass."
