"""Tests for verify_done gate logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from kodo.agent import Agent
from kodo.orchestrators.base import (
    CycleConfig,
    DoneSignal,
    QuickCheck,
    VerificationState,
    handle_done,
    verify_done,
)
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
            SUMMARY, True, done_signal, GOAL, team, tmp_project,
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
            SUMMARY, True, done_signal, GOAL, team, tmp_project,
            config=CycleConfig(verification="skip"),
        )
        assert tester.session.stats.queries == 0

    def test_skip_still_rejects_on_failure(self, tmp_project: Path) -> None:
        """verification='skip' still marks unsuccessful when success=False."""
        team = {"tester": make_agent("ALL CHECKS PASS")}
        done_signal = DoneSignal()
        result = handle_done(
            SUMMARY, False, done_signal, GOAL, team, tmp_project,
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
            SUMMARY, True, done_signal, GOAL, team, tmp_project,
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
            SUMMARY, True, done_signal, GOAL, team, tmp_project,
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
            SUMMARY, True, done_signal, GOAL, team, tmp_project,
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
            QuickCheck(path=str(existing), description="File A", error_message="A missing"),
            QuickCheck(path=missing, description="File B", error_message="B missing"),
        ]
        result = handle_done(
            SUMMARY, True, done_signal, GOAL, team, tmp_project,
            config=CycleConfig(verification=checks),
        )
        assert "Quick-check verification failed" in result
        assert "B missing" in result
        assert "A missing" not in result


class TestHandleDoneFullVerification:
    """Verify that verification='full' behaves as before."""

    def test_full_runs_verifiers(self, tmp_project: Path) -> None:
        """verification='full' runs agent-based verification."""
        tester = make_agent("ALL CHECKS PASS")
        team = {"tester": tester}
        done_signal = DoneSignal()
        result = handle_done(
            SUMMARY, True, done_signal, GOAL, team, tmp_project,
            config=CycleConfig(verification="full"),
        )
        assert "Verified and accepted" in result
        assert tester.session.stats.queries == 1

    def test_full_is_default(self, tmp_project: Path) -> None:
        """When config is not specified, full verification runs."""
        tester = make_agent("ALL CHECKS PASS")
        team = {"tester": tester}
        done_signal = DoneSignal()
        handle_done(SUMMARY, True, done_signal, GOAL, team, tmp_project)
        assert tester.session.stats.queries == 1


class TestVerificationStateCycleBoundary:
    """Ensure VerificationState resets between cycles."""

    def test_verification_state_resets_between_cycles(
        self, tmp_project: Path,
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
