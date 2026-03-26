"""Tests for verify_done gate logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from kodo.agent import Agent
from kodo.orchestrators.base import (
    CycleConfig,
    DoneSignal,
    QuickCheck,
)
from kodo.orchestrators.verification import (
    VerificationState,
    _build_verification_prompt,
    _check_passed,
    handle_done,
    verify_done,
)
from kodo.prompts.roles import build_orchestrator_prompt, ORCHESTRATOR_SYSTEM_PROMPT
from tests.conftest import FakeSession, make_agent

GOAL = "Build a hello-world web server."
SUMMARY = "Implemented hello-world server on port 8000."


def test_all_pass(tmp_project: Path) -> None:
    """When both agents say ALL CHECKS PASS, verify_done returns None."""
    team = {
        "tester": make_agent("ALL CHECKS PASS"),
        "architect": make_agent("ALL CHECKS PASS"),
    }
    assert verify_done(GOAL, SUMMARY, team, tmp_project) is None


@pytest.mark.parametrize(
    "role,other_role,issue_label",
    [
        ("tester", "architect", "tester found issues"),
        ("architect", "tester", "Architect found issues"),
    ],
)
def test_single_role_fails(tmp_project: Path, role, other_role, issue_label) -> None:
    """When one verifier finds issues, verify_done returns rejection."""
    team = {
        role: make_agent("Critical bug: SQL injection in query handler"),
        other_role: make_agent("ALL CHECKS PASS"),
    }
    result = verify_done(GOAL, SUMMARY, team, tmp_project)
    assert result is not None
    assert "DONE REJECTED" in result
    assert issue_label in result
    assert "SQL injection" in result


def test_both_fail(tmp_project: Path) -> None:
    """When both agents find issues, both are included in rejection."""
    team = {
        "tester": make_agent("Server crashes on startup"),
        "architect": make_agent("Missing error handling in routes"),
    }
    result = verify_done(GOAL, SUMMARY, team, tmp_project)
    assert result is not None
    assert "tester found issues" in result
    assert "Architect found issues" in result
    assert "Server crashes" in result
    assert "Missing error handling" in result


def test_case_insensitive_pass(tmp_project: Path) -> None:
    """ALL CHECKS PASS matching is case-insensitive."""
    team = {
        "tester": make_agent("all checks pass - looks good"),
        "architect": make_agent("All Checks Pass"),
    }
    assert verify_done(GOAL, SUMMARY, team, tmp_project) is None


def test_not_all_checks_pass_rejected(tmp_project: Path) -> None:
    """NOT ALL CHECKS PASS must not be a false positive for acceptance."""
    team = {
        "tester": make_agent("NOT ALL CHECKS PASS - server returns 500"),
        "architect": make_agent("ALL CHECKS PASS"),
    }
    result = verify_done(GOAL, SUMMARY, team, tmp_project)
    assert result is not None
    assert "tester found issues" in result


@pytest.mark.parametrize("present_role", ["tester", "architect"])
def test_single_verifier_in_team(tmp_project: Path, present_role) -> None:
    """If only one verifier exists, verification still works."""
    team = {present_role: make_agent("ALL CHECKS PASS")}
    assert verify_done(GOAL, SUMMARY, team, tmp_project) is None


def test_tester_browser_used_when_no_tester(tmp_project: Path) -> None:
    """tester_browser runs when tester is absent and browser_testing=True."""
    team = {
        "tester_browser": make_agent("ALL CHECKS PASS"),
        "architect": make_agent("ALL CHECKS PASS"),
    }
    assert verify_done(GOAL, SUMMARY, team, tmp_project, browser_testing=True) is None


def test_tester_browser_fails(tmp_project: Path) -> None:
    """tester_browser rejection works the same as tester."""
    team = {
        "tester_browser": make_agent("Page returns 404"),
        "architect": make_agent("ALL CHECKS PASS"),
    }
    result = verify_done(GOAL, SUMMARY, team, tmp_project, browser_testing=True)
    assert result is not None
    assert "Page returns 404" in result


def test_both_testers_run_when_both_exist(tmp_project: Path) -> None:
    """BUG FIX: when both tester and tester_browser exist, both should run."""
    team = {
        "tester": make_agent("ALL CHECKS PASS"),
        "tester_browser": make_agent("Button click does nothing"),
        "architect": make_agent("ALL CHECKS PASS"),
    }
    result = verify_done(GOAL, SUMMARY, team, tmp_project, browser_testing=True)
    assert result is not None
    assert "tester_browser found issues" in result
    assert "Button click" in result


def test_both_testers_pass(tmp_project: Path) -> None:
    """When both testers exist and both pass, verify_done returns None."""
    team = {
        "tester": make_agent("ALL CHECKS PASS"),
        "tester_browser": make_agent("ALL CHECKS PASS"),
        "architect": make_agent("ALL CHECKS PASS"),
    }
    assert verify_done(GOAL, SUMMARY, team, tmp_project, browser_testing=True) is None


def test_empty_team(tmp_project: Path) -> None:
    """With no dedicated verifiers, worker is used as fallback verifier."""
    team = {"worker": make_agent("done")}
    result = verify_done(GOAL, SUMMARY, team, tmp_project)
    assert result is not None
    assert "verifier" in result.lower()


def test_verification_starts_fresh_session(tmp_project: Path) -> None:
    """Verification agents start with a fresh session (stats cleared before run)."""
    tester = make_agent("ALL CHECKS PASS")
    architect = make_agent("ALL CHECKS PASS")
    team = {"tester": tester, "architect": architect}

    # Simulate prior work — dirty the stats
    tester.run("prior task", tmp_project, agent_name="tester")
    architect.run("prior task", tmp_project, agent_name="architect")
    assert tester.session.stats.queries == 1

    verify_done(GOAL, SUMMARY, team, tmp_project)

    # Stats should reflect only the verification query, not prior work
    assert tester.session.stats.queries == 1
    assert architect.session.stats.queries == 1


def test_goal_and_summary_in_prompt(tmp_project: Path) -> None:
    """Verification prompt includes the original goal and summary."""
    tester = make_agent("ALL CHECKS PASS")
    team = {"tester": tester}

    verify_done(GOAL, SUMMARY, team, tmp_project)

    # FakeSession records all prompts it receives
    assert len(tester.session.prompts) == 1
    prompt = tester.session.prompts[0]
    assert GOAL in prompt
    assert SUMMARY in prompt


def test_report_truncated_at_3000(tmp_project: Path) -> None:
    """Long agent reports are truncated in the rejection message."""
    long_report = "x" * 5000
    team = {"tester": make_agent(long_report)}
    result = verify_done(GOAL, SUMMARY, team, tmp_project)
    assert result is not None
    # The full rejection should be shorter than the raw report — truncation happened
    assert len(result) < len(long_report)
    # But it still contains the truncated portion
    assert "xxx" in result


# --- Exception handling tests ---


class _CrashingSession(FakeSession):
    def query(self, prompt, project_dir, *, max_turns):
        raise RuntimeError("SDK connection lost")


@pytest.mark.parametrize(
    "role,label",
    [
        ("tester", "tester crashed"),
        ("architect", "Architect crashed"),
    ],
)
def test_exception_becomes_rejection(tmp_project: Path, role, label) -> None:
    """BUG FIX: agent crash should be a rejection, not an unhandled exception."""
    other = "architect" if role == "tester" else "tester"
    crashing_agent = Agent(_CrashingSession(), role.title(), max_turns=10)
    team = {
        role: crashing_agent,
        other: make_agent("ALL CHECKS PASS"),
    }
    result = verify_done(GOAL, SUMMARY, team, tmp_project)
    assert result is not None
    assert "DONE REJECTED" in result
    assert label in result
    assert "SDK connection lost" in result


def test_both_crash(tmp_project: Path) -> None:
    """Both agents crashing produces two rejection items."""
    team = {
        "tester": Agent(_CrashingSession(), "T", max_turns=10),
        "architect": Agent(_CrashingSession(), "A", max_turns=10),
    }
    result = verify_done(GOAL, SUMMARY, team, tmp_project)
    assert result is not None
    assert result.count("crashed") == 2


# --- VerificationState tests ---


def test_minor_issues_fixed_accepted(tmp_project: Path) -> None:
    """When verifiers say MINOR ISSUES FIXED, verify_done accepts (returns None)."""
    team = {
        "tester": make_agent("I fixed some formatting. MINOR ISSUES FIXED"),
        "architect": make_agent("Renamed a variable. MINOR ISSUES FIXED"),
    }
    assert verify_done(GOAL, SUMMARY, team, tmp_project) is None


def test_minor_issues_fixed_case_insensitive(tmp_project: Path) -> None:
    """MINOR ISSUES FIXED matching is case-insensitive."""
    team = {
        "tester": make_agent("minor issues fixed"),
        "architect": make_agent("Minor Issues Fixed"),
    }
    assert verify_done(GOAL, SUMMARY, team, tmp_project) is None


def test_second_attempt_accumulates_queries(tmp_project: Path) -> None:
    """Second done() call reuses verifier sessions (queries accumulate)."""
    tester = make_agent("ALL CHECKS PASS")
    architect = make_agent("ALL CHECKS PASS")
    team = {"tester": tester, "architect": architect}
    state = VerificationState()

    # First call — resets then queries
    verify_done(GOAL, SUMMARY, team, tmp_project, state=state)
    assert tester.session.stats.queries == 1

    # Second call — should NOT reset (persistent context), so queries accumulate
    verify_done(GOAL, SUMMARY, team, tmp_project, state=state)
    assert tester.session.stats.queries == 2
    assert architect.session.stats.queries == 2


def test_attempt_count_in_rejection(tmp_project: Path) -> None:
    """Rejection message includes the attempt number."""
    team = {"tester": make_agent("Something is broken")}
    state = VerificationState()

    result1 = verify_done(GOAL, SUMMARY, team, tmp_project, state=state)
    assert result1 is not None
    assert "attempt 1" in result1

    result2 = verify_done(GOAL, SUMMARY, team, tmp_project, state=state)
    assert result2 is not None
    assert "attempt 2" in result2


# --- Conditional browser testing tests ---


def test_browser_skipped_by_default(tmp_project: Path) -> None:
    """browser_testing defaults to False, so tester_browser is skipped."""
    tester_browser = make_agent("ALL CHECKS PASS")
    team = {
        "tester": make_agent("ALL CHECKS PASS"),
        "tester_browser": tester_browser,
        "architect": make_agent("ALL CHECKS PASS"),
    }
    result = verify_done(GOAL, SUMMARY, team, tmp_project)
    assert result is None
    assert tester_browser.session.stats.queries == 0


def test_browser_runs_when_flag_true(tmp_project: Path) -> None:
    """When browser_testing=True, tester_browser runs."""
    tester_browser = make_agent("ALL CHECKS PASS")
    team = {
        "tester": make_agent("ALL CHECKS PASS"),
        "tester_browser": tester_browser,
        "architect": make_agent("ALL CHECKS PASS"),
    }
    result = verify_done(GOAL, SUMMARY, team, tmp_project, browser_testing=True)
    assert result is None
    assert tester_browser.session.stats.queries == 1


def test_browser_flag_false_skips_even_with_agent(tmp_project: Path) -> None:
    """browser_testing=False skips tester_browser even when the agent exists."""
    tester_browser = make_agent("ALL CHECKS PASS")
    team = {
        "tester": make_agent("ALL CHECKS PASS"),
        "tester_browser": tester_browser,
    }
    result = verify_done(GOAL, SUMMARY, team, tmp_project, browser_testing=False)
    assert result is None
    assert tester_browser.session.stats.queries == 0


# --- handle_done verification modes ---


class TestHandleDoneVerificationSkip:
    """Tests for verification='skip' mode in handle_done."""

    def test_skip_accepts_immediately(self, tmp_project: Path) -> None:
        """verification='skip' accepts without running any verifiers."""
        team = {"tester": make_agent("THIS SHOULD NOT RUN")}
        done_signal = DoneSignal()
        result = handle_done(
            SUMMARY,
            True,
            done_signal,
            GOAL,
            team,
            tmp_project,
            config=CycleConfig(verification="skip"),
        )
        assert "Verified and accepted" in result
        assert done_signal.called
        assert done_signal.success

    def test_skip_does_not_run_verifiers(self, tmp_project: Path) -> None:
        """verification='skip' never queries the tester."""
        tester = make_agent("ALL CHECKS PASS")
        team = {"tester": tester}
        done_signal = DoneSignal()
        handle_done(
            SUMMARY,
            True,
            done_signal,
            GOAL,
            team,
            tmp_project,
            config=CycleConfig(verification="skip"),
        )
        assert tester.session.stats.queries == 0

    def test_skip_still_rejects_on_failure(self, tmp_project: Path) -> None:
        """verification='skip' still marks unsuccessful when success=False."""
        team = {"tester": make_agent("ALL CHECKS PASS")}
        done_signal = DoneSignal()
        result = handle_done(
            SUMMARY,
            False,
            done_signal,
            GOAL,
            team,
            tmp_project,
            config=CycleConfig(verification="skip"),
        )
        assert "unsuccessful" in result.lower()
        assert done_signal.called
        assert not done_signal.success


class TestHandleDoneQuickCheck:
    """Tests for verification=[QuickCheck(...)] mode in handle_done."""

    def test_quick_check_passes_when_file_exists(self, tmp_project: Path) -> None:
        """Quick check accepts when the expected file exists."""
        check_file = tmp_project / "findings.md"
        check_file.write_text("some findings")
        team = {"tester": make_agent("THIS SHOULD NOT RUN")}
        done_signal = DoneSignal()
        checks = [
            QuickCheck(
                path=str(check_file),
                description="Findings file",
                error_message="Missing findings",
            )
        ]
        result = handle_done(
            SUMMARY,
            True,
            done_signal,
            GOAL,
            team,
            tmp_project,
            config=CycleConfig(verification=checks),
        )
        assert "Verified and accepted" in result
        assert done_signal.called
        assert done_signal.success

    def test_quick_check_rejects_when_file_missing(self, tmp_project: Path) -> None:
        """Quick check rejects when the expected file does not exist."""
        missing = str(tmp_project / "nonexistent.md")
        team = {"tester": make_agent("ALL CHECKS PASS")}
        done_signal = DoneSignal()
        checks = [
            QuickCheck(
                path=missing,
                description="Findings file",
                error_message="Missing findings",
            )
        ]
        result = handle_done(
            SUMMARY,
            True,
            done_signal,
            GOAL,
            team,
            tmp_project,
            config=CycleConfig(verification=checks),
        )
        assert "Quick-check verification failed" in result
        assert "Missing findings" in result
        assert not done_signal.called

    def test_quick_check_does_not_run_verifiers(self, tmp_project: Path) -> None:
        """Quick check mode never queries agent verifiers."""
        check_file = tmp_project / "findings.md"
        check_file.write_text("data")
        tester = make_agent("ALL CHECKS PASS")
        team = {"tester": tester}
        done_signal = DoneSignal()
        checks = [
            QuickCheck(
                path=str(check_file),
                description="Findings file",
                error_message="Missing",
            )
        ]
        handle_done(
            SUMMARY,
            True,
            done_signal,
            GOAL,
            team,
            tmp_project,
            config=CycleConfig(verification=checks),
        )
        assert tester.session.stats.queries == 0

    def test_multiple_quick_checks_all_must_pass(self, tmp_project: Path) -> None:
        """All quick checks must pass; one missing file rejects."""
        existing = tmp_project / "a.md"
        existing.write_text("ok")
        missing = str(tmp_project / "b.md")
        team = {}
        done_signal = DoneSignal()
        checks = [
            QuickCheck(
                path=str(existing), description="File A", error_message="A missing"
            ),
            QuickCheck(path=missing, description="File B", error_message="B missing"),
        ]
        result = handle_done(
            SUMMARY,
            True,
            done_signal,
            GOAL,
            team,
            tmp_project,
            config=CycleConfig(verification=checks),
        )
        assert "Quick-check verification failed" in result
        assert "B missing" in result
        assert "A missing" not in result


class TestHandleDoneFullVerification:
    """Verify that verification='full' runs the regex-based verify_done gate."""

    def test_full_runs_verifiers_and_accepts(self, tmp_project: Path) -> None:
        """verification='full' runs tester/architect and accepts when they pass."""
        tester = make_agent("ALL CHECKS PASS")
        team = {"tester": tester}
        done_signal = DoneSignal()
        result = handle_done(
            SUMMARY,
            True,
            done_signal,
            GOAL,
            team,
            tmp_project,
            config=CycleConfig(verification="full"),
        )
        assert "Verified and accepted" in result
        assert done_signal.called
        assert done_signal.success
        assert tester.session.stats.queries == 1

    def test_full_rejects_on_tester_failure(self, tmp_project: Path) -> None:
        """verification='full' rejects when tester reports issues."""
        tester = make_agent("Tests are failing: ImportError")
        team = {"tester": tester}
        done_signal = DoneSignal()
        result = handle_done(
            SUMMARY,
            True,
            done_signal,
            GOAL,
            team,
            tmp_project,
            config=CycleConfig(verification="full"),
        )
        assert "REJECTED" in result
        assert done_signal.called is False


class TestVerificationStateCycleBoundary:
    """Ensure VerificationState resets between cycles."""

    def test_verification_state_resets_between_cycles(
        self,
        tmp_project: Path,
    ) -> None:
        """Each cycle creates a fresh VerificationState, resetting done_attempt.

        Simulates two cycles: cycle 1 calls verify_done() twice (attempt
        reaches 2), then cycle 2 creates a fresh state (attempt restarts
        at 1).
        """
        team = {"tester": make_agent("ALL CHECKS PASS")}

        # ── Cycle 1 ──
        state1 = VerificationState()
        verify_done(GOAL, SUMMARY, team, tmp_project, state=state1)
        assert state1.done_attempt == 1

        verify_done(GOAL, SUMMARY, team, tmp_project, state=state1)
        assert state1.done_attempt == 2

        # ── Cycle 2 — fresh state, counter must restart ──
        state2 = VerificationState()
        verify_done(GOAL, SUMMARY, team, tmp_project, state=state2)
        assert state2.done_attempt == 1  # NOT 3

    def test_default_state_starts_fresh(self) -> None:
        """A newly-created VerificationState always starts at attempt 0."""
        state = VerificationState()
        assert state.done_attempt == 0


# --- Criteria-aware verification tests ---


SAMPLE_CRITERIA = (
    "1) Panel.on_draw() draws border rects when border_width > 0.\n"
    "2) Default Theme colors are updated to dark-blue palette.\n"
    "3) A PNG rendering shows readable text with no clipping."
)


class TestBuildVerificationPrompt:
    """Unit tests for _build_verification_prompt."""

    def test_without_criteria_uses_generic_instructions(self) -> None:
        """No criteria → old-style generic verification instructions."""
        prompt = _build_verification_prompt(GOAL, SUMMARY)
        assert GOAL in prompt
        assert SUMMARY in prompt
        assert "honest assessment" in prompt
        assert "Acceptance Criteria" not in prompt

    def test_with_criteria_includes_checklist(self) -> None:
        """Criteria → structured checklist with per-criterion evaluation."""
        prompt = _build_verification_prompt(GOAL, SUMMARY, SAMPLE_CRITERIA)
        assert GOAL in prompt
        assert SUMMARY in prompt
        assert "Acceptance Criteria" in prompt
        assert "Panel.on_draw()" in prompt
        assert "PASS" in prompt
        assert "FAIL" in prompt
        # Should NOT contain the generic instruction
        assert "honest assessment" not in prompt

    def test_with_criteria_instructs_visual_verification(self) -> None:
        """Criteria prompt tells verifiers to render and READ files."""
        prompt = _build_verification_prompt(GOAL, SUMMARY, SAMPLE_CRITERIA)
        assert "render" in prompt.lower()
        assert "READ the file" in prompt

    def test_empty_string_criteria_treated_as_no_criteria(self) -> None:
        """Empty string acceptance_criteria falls back to generic."""
        prompt = _build_verification_prompt(GOAL, SUMMARY, "")
        assert "honest assessment" in prompt
        assert "Acceptance Criteria" not in prompt


class TestVerifyDoneWithCriteria:
    """Integration tests: acceptance_criteria flows into verification prompt."""

    def test_criteria_in_verifier_prompt(self, tmp_project: Path) -> None:
        """When criteria are provided, verifiers see the checklist."""
        tester = make_agent("ALL CHECKS PASS")
        team = {"tester": tester}
        verify_done(
            GOAL,
            SUMMARY,
            team,
            tmp_project,
            acceptance_criteria=SAMPLE_CRITERIA,
        )
        prompt = tester.session.prompts[0]
        assert "Acceptance Criteria" in prompt
        assert "Panel.on_draw()" in prompt
        assert "PASS" in prompt and "FAIL" in prompt

    def test_no_criteria_verifier_gets_generic(self, tmp_project: Path) -> None:
        """Without criteria, verifiers get the old-style generic instructions."""
        tester = make_agent("ALL CHECKS PASS")
        team = {"tester": tester}
        verify_done(GOAL, SUMMARY, team, tmp_project)
        prompt = tester.session.prompts[0]
        assert "honest assessment" in prompt
        assert "Acceptance Criteria" not in prompt

    def test_criteria_through_handle_done(self, tmp_project: Path) -> None:
        """Criteria threaded from CycleConfig through handle_done to verify_done."""
        tester = make_agent("ALL CHECKS PASS")
        team = {"tester": tester}
        done_signal = DoneSignal()
        config = CycleConfig(
            verification="full",
            acceptance_criteria=SAMPLE_CRITERIA,
        )
        handle_done(
            SUMMARY,
            True,
            done_signal,
            GOAL,
            team,
            tmp_project,
            config=config,
        )
        prompt = tester.session.prompts[0]
        assert "Acceptance Criteria" in prompt
        assert "PNG rendering" in prompt

    def test_criteria_still_gates_on_signal(self, tmp_project: Path) -> None:
        """Even with criteria, _check_passed still requires ALL CHECKS PASS."""
        tester = make_agent("Criterion 1: PASS\nCriterion 2: FAIL — no borders")
        team = {"tester": tester}
        result = verify_done(
            GOAL,
            SUMMARY,
            team,
            tmp_project,
            acceptance_criteria=SAMPLE_CRITERIA,
        )
        assert result is not None
        assert "DONE REJECTED" in result


# --- Effort-level tests ---


class TestEffortLevel:
    """Tests for effort-level prompt supplements."""

    def test_standard_effort_no_supplement(self) -> None:
        """Standard effort adds nothing to orchestrator prompt."""
        prompt = build_orchestrator_prompt(effort="standard")
        assert prompt == ORCHESTRATOR_SYSTEM_PROMPT

    def test_high_effort_adds_supplement(self) -> None:
        """High effort appends quality standards to orchestrator prompt."""
        prompt = build_orchestrator_prompt(effort="high")
        assert prompt.startswith(ORCHESTRATOR_SYSTEM_PROMPT)
        assert "Effort Level: HIGH" in prompt
        assert "NOT sufficient" in prompt

    def test_max_effort_adds_supplement(self) -> None:
        """Max effort appends aggressive standards to orchestrator prompt."""
        prompt = build_orchestrator_prompt(effort="max")
        assert "Effort Level: MAX" in prompt
        assert "comfort zone" in prompt

    def test_high_effort_in_verification_prompt(self) -> None:
        """High effort adds skepticism to verification prompt."""
        prompt = _build_verification_prompt(GOAL, SUMMARY, effort="high")
        assert "HIGH" in prompt
        assert "actually good" in prompt

    def test_max_effort_in_verification_prompt(self) -> None:
        """Max effort adds demanding language to verification prompt."""
        prompt = _build_verification_prompt(GOAL, SUMMARY, effort="max")
        assert "MAX" in prompt
        assert "skeptical" in prompt

    def test_effort_combined_with_criteria(self) -> None:
        """Effort supplement stacks with criteria-aware prompt."""
        prompt = _build_verification_prompt(
            GOAL,
            SUMMARY,
            SAMPLE_CRITERIA,
            effort="max",
        )
        assert "Acceptance Criteria" in prompt
        assert "MAX" in prompt
        assert "Panel.on_draw()" in prompt

    def test_effort_threaded_through_handle_done(self, tmp_project: Path) -> None:
        """Effort flows from CycleConfig through handle_done to verifier prompt."""
        tester = make_agent("ALL CHECKS PASS")
        team = {"tester": tester}
        done_signal = DoneSignal()
        config = CycleConfig(verification="full", effort="max")
        handle_done(
            SUMMARY,
            True,
            done_signal,
            GOAL,
            team,
            tmp_project,
            config=config,
        )
        prompt = tester.session.prompts[0]
        assert "MAX" in prompt

    def test_standard_effort_no_verification_supplement(self) -> None:
        """Standard effort adds no supplement to verification prompt."""
        prompt_standard = _build_verification_prompt(GOAL, SUMMARY, effort="standard")
        prompt_none = _build_verification_prompt(GOAL, SUMMARY)
        assert prompt_standard == prompt_none

    def test_low_effort_adds_supplement(self) -> None:
        """Low effort appends simplicity guidance to orchestrator prompt."""
        prompt = build_orchestrator_prompt(effort="low")
        assert "Effort Level: LOW" in prompt
        assert "simple" in prompt.lower()

    def test_low_effort_no_verification_supplement(self) -> None:
        """Low effort adds no extra verification scrutiny."""
        prompt_low = _build_verification_prompt(GOAL, SUMMARY, effort="low")
        prompt_standard = _build_verification_prompt(GOAL, SUMMARY, effort="standard")
        assert prompt_low == prompt_standard


# --- _check_passed edge cases ---
#
# Consolidated from TestCheckPassedEdgeCases (test_verify_done.py),
# TestCheckPassedQuoting (test_regression.py), and
# TestCheckPassedStress (test_legacy_done_stress.py).

# fmt: off
_CHECK_PASSED_CASES = [
    # (id, report, expected)
    # --- direct signal ---
    ("direct_pass",            "ALL CHECKS PASS",                                          True),
    ("direct_fail",            "Tests are failing, 3 errors found",                         False),
    # --- NOT prefix ---
    ("not_all_checks",         "NOT ALL CHECKS PASS — 2 failures",                         False),
    ("not_minor_issues",       "NOT MINOR ISSUES FIXED — review needed",                   False),
    # --- quoted signal (reject) ---
    ("single_quoted",          "Worker said 'ALL CHECKS PASS' but I found bugs.",           False),
    ("double_quoted",          'Agent output "ALL CHECKS PASS" but 3 tests are broken.',    False),
    ("inline_code",            "The output contained `ALL CHECKS PASS` but tests fail.",    False),
    ("fenced_code",            "Output:\n```\nALL CHECKS PASS\n```\nBut tests fail.",       False),
    # --- sentence boundary (accept) ---
    ("after_period",           "Tests completed. ALL CHECKS PASS.",                         True),
    ("after_exclamation",      "Great! ALL CHECKS PASS!",                                   True),
    ("after_question",         "Ready? ALL CHECKS PASS.",                                   True),
    ("start_of_line",          "Tests ran successfully.\n\nALL CHECKS PASS",                True),
    # --- mid-sentence (reject) ---
    ("mid_sentence",           "Tests ALL CHECKS PASS here but bugs remain",                False),
    # --- MINOR ISSUES FIXED ---
    ("minor_issues_accepted",  "Fixed formatting issues. MINOR ISSUES FIXED.",              True),
    # --- markdown formatting ---
    ("markdown_bold",          "**ALL CHECKS PASS**",                                       True),
    # --- unicode sentence ender ---
    ("unicode_period",         "テスト完了。ALL CHECKS PASS",                                True),
    # --- empty / whitespace ---
    ("empty_report",           "",                                                          False),
    # --- signal split across lines ---
    ("split_across_lines",     "ALL CHECKS\nPASS",                                          False),
]
# fmt: on


class TestCheckPassed:
    """Verify _check_passed regex against representative edge cases.

    Each case covers a distinct category: direct signal, quoting, sentence
    boundaries, markdown, unicode, empty input, and split-line rejection.
    """

    @pytest.mark.parametrize(
        "report, expected",
        [(report, expected) for _, report, expected in _CHECK_PASSED_CASES],
        ids=[id_ for id_, _, _ in _CHECK_PASSED_CASES],
    )
    def test_check_passed(self, report: str, expected: bool) -> None:
        assert _check_passed(report) is expected


# --- _run_quick_checks tests ---


class TestRunQuickChecks:
    """Test the _run_quick_checks function directly."""

    def test_all_checks_pass_returns_none(self, tmp_project: Path) -> None:
        """When all quick checks pass, return None."""
        from kodo.orchestrators.base import QuickCheck
        from kodo.orchestrators.verification import _run_quick_checks

        # Create the files
        file1 = tmp_project / "output.txt"
        file2 = tmp_project / "results.json"
        file1.write_text("data")
        file2.write_text("{}")

        checks = [
            QuickCheck(
                path=str(file1),
                description="Output file",
                error_message="Missing output",
            ),
            QuickCheck(
                path=str(file2),
                description="Results file",
                error_message="Missing results",
            ),
        ]

        result = _run_quick_checks(checks)
        assert result is None

    def test_missing_file_returns_error(self, tmp_project: Path) -> None:
        """When a file is missing, return error message."""
        from kodo.orchestrators.base import QuickCheck
        from kodo.orchestrators.verification import _run_quick_checks

        missing = str(tmp_project / "nonexistent.txt")
        checks = [
            QuickCheck(
                path=missing,
                description="Output file",
                error_message="Missing output.txt",
            ),
        ]

        result = _run_quick_checks(checks)
        assert result is not None
        assert "Quick-check verification failed" in result
        assert "Missing output.txt" in result

    def test_run_dir_placeholder_resolved(self, tmp_project: Path) -> None:
        """The {run_dir} placeholder should be resolved."""
        from kodo import log
        from kodo.orchestrators.base import QuickCheck
        from kodo.orchestrators.verification import _run_quick_checks

        # Get the current run's log file and its directory
        log_file = log.get_log_file()
        if log_file:
            run_dir = log_file.parent
            check_file = run_dir / "findings.md"
            check_file.write_text("findings")

            checks = [
                QuickCheck(
                    path="{run_dir}/findings.md",
                    description="Findings",
                    error_message="Missing findings",
                ),
            ]

            result = _run_quick_checks(checks)
            assert result is None  # Should pass
        else:
            # If no log file, the placeholder won't resolve - test the fallback
            checks = [
                QuickCheck(
                    path="{run_dir}/findings.md",
                    description="Findings",
                    error_message="Missing findings",
                ),
            ]
            result = _run_quick_checks(checks)
            # Should fail because {run_dir} resolves to empty string
            assert result is not None


# --- handle_done edge cases ---


class TestHandleDoneEdgeCases:
    """Test edge cases in handle_done function."""

    def test_handle_done_with_custom_orchestrator_tag(self, tmp_project: Path) -> None:
        """orchestrator_tag should be included in log emissions."""
        from unittest.mock import patch

        team = {"tester": make_agent("ALL CHECKS PASS")}
        done_signal = DoneSignal()

        with patch("kodo.log.emit", autospec=True) as mock_emit:
            result = handle_done(
                SUMMARY,
                True,
                done_signal,
                GOAL,
                team,
                tmp_project,
                orchestrator_tag="custom_orch",
                config=CycleConfig(verification="full"),
            )

        # Verify the done signal was set (verification passed)
        assert done_signal.called
        assert "Verified and accepted" in result

        # Check that emit was called
        assert mock_emit.call_count > 0

    def test_handle_done_auto_commit_when_enabled(self, tmp_project: Path) -> None:
        """auto_commit=True should trigger _auto_commit."""
        from unittest.mock import patch

        team = {"tester": make_agent("ALL CHECKS PASS")}
        done_signal = DoneSignal()

        with patch(
            "kodo.orchestrators.base._auto_commit", autospec=True
        ) as mock_commit:
            handle_done(
                SUMMARY,
                True,
                done_signal,
                GOAL,
                team,
                tmp_project,
                config=CycleConfig(verification="skip", auto_commit=True),
            )

        mock_commit.assert_called_once_with(team, tmp_project, SUMMARY)

    def test_handle_done_no_auto_commit_when_disabled(self, tmp_project: Path) -> None:
        """auto_commit=False should not trigger _auto_commit."""
        from unittest.mock import patch

        team = {"tester": make_agent("ALL CHECKS PASS")}
        done_signal = DoneSignal()

        with patch(
            "kodo.orchestrators.base._auto_commit", autospec=True
        ) as mock_commit:
            handle_done(
                SUMMARY,
                True,
                done_signal,
                GOAL,
                team,
                tmp_project,
                config=CycleConfig(verification="skip", auto_commit=False),
            )

        mock_commit.assert_not_called()


# --- verify_done with verifiers dict ---


class TestVerifyDoneWithVerifiersDict:
    """Test verify_done with custom verifiers dict (not legacy keys)."""

    def test_custom_tester_keys(self, tmp_project: Path) -> None:
        """Custom tester keys from verifiers dict should be used."""
        team = {
            "test_runner": make_agent("ALL CHECKS PASS"),
            "code_reviewer": make_agent("ALL CHECKS PASS"),
        }
        verifiers = {
            "testers": ["test_runner"],
            "reviewers": ["code_reviewer"],
        }
        result = verify_done(
            GOAL,
            SUMMARY,
            team,
            tmp_project,
            verifiers=verifiers,
        )
        assert result is None

    def test_custom_browser_tester_keys(self, tmp_project: Path) -> None:
        """Custom browser_tester keys should work when browser_testing=True."""
        team = {
            "e2e_tester": make_agent("ALL CHECKS PASS"),
            "reviewer": make_agent("ALL CHECKS PASS"),
        }
        verifiers = {
            "browser_testers": ["e2e_tester"],
            "reviewers": ["reviewer"],
        }
        result = verify_done(
            GOAL,
            SUMMARY,
            team,
            tmp_project,
            browser_testing=True,
            verifiers=verifiers,
        )
        assert result is None

    def test_custom_verifier_failure(self, tmp_project: Path) -> None:
        """Custom verifier finding issues should reject."""
        team = {
            "custom_checker": make_agent("Found critical bugs"),
        }
        verifiers = {
            "testers": ["custom_checker"],
        }
        result = verify_done(
            GOAL,
            SUMMARY,
            team,
            tmp_project,
            verifiers=verifiers,
        )
        assert result is not None
        assert "custom_checker found issues" in result

    def test_empty_verifiers_dict_uses_fallback(self, tmp_project: Path) -> None:
        """Empty verifiers dict should trigger fallback to worker."""
        team = {"worker": make_agent("done")}
        verifiers = {
            "testers": [],
            "browser_testers": [],
            "reviewers": [],
        }
        result = verify_done(
            GOAL,
            SUMMARY,
            team,
            tmp_project,
            verifiers=verifiers,
        )
        # Fallback worker should run (and fail the check since response is "done")
        assert result is not None
        assert "worker" in result.lower()

    def test_missing_verifier_key_skipped(self, tmp_project: Path) -> None:
        """Verifier keys that don't exist in team should be skipped."""
        team = {
            "tester": make_agent("ALL CHECKS PASS"),
        }
        verifiers = {
            "testers": ["tester", "nonexistent_tester"],
            "reviewers": [],
        }
        result = verify_done(
            GOAL,
            SUMMARY,
            team,
            tmp_project,
            verifiers=verifiers,
        )
        # Should pass because tester passed (nonexistent is skipped)
        assert result is None


# --- Fallback verifier tests ---


class TestFallbackVerifier:
    """Test the fallback verifier logic when no dedicated verifiers exist."""

    def test_worker_smart_preferred_as_fallback(self, tmp_project: Path) -> None:
        """worker_smart should be preferred over worker as fallback."""
        worker_smart = make_agent("ALL CHECKS PASS")
        worker = make_agent("THIS SHOULD NOT RUN")
        team = {
            "worker": worker,
            "worker_smart": worker_smart,
        }
        result = verify_done(GOAL, SUMMARY, team, tmp_project)
        assert result is None
        # worker_smart should have been used
        assert worker_smart.session.stats.queries == 1
        assert worker.session.stats.queries == 0

    def test_worker_used_if_no_worker_smart(self, tmp_project: Path) -> None:
        """worker should be used if worker_smart doesn't exist."""
        worker = make_agent("done")  # Will fail verification
        team = {"worker": worker}
        result = verify_done(GOAL, SUMMARY, team, tmp_project)
        assert result is not None
        assert "worker" in result.lower()

    def test_any_agent_used_if_no_worker_keys(self, tmp_project: Path) -> None:
        """Any agent from team should be used if neither worker key exists."""
        some_agent = make_agent("ALL CHECKS PASS")
        team = {"some_agent": some_agent}
        result = verify_done(GOAL, SUMMARY, team, tmp_project)
        assert result is None
        assert some_agent.session.stats.queries == 1

    def test_fallback_uses_fresh_session(self, tmp_project: Path) -> None:
        """Fallback verifier should use new_conversation=True."""
        worker = make_agent("ALL CHECKS PASS")
        team = {"worker": worker}

        # Dirty the session
        worker.run("prior work", tmp_project, agent_name="worker")
        assert worker.session.stats.queries == 1

        verify_done(GOAL, SUMMARY, team, tmp_project)

        # Should still be 1 (fresh session for verification)
        assert worker.session.stats.queries == 1
