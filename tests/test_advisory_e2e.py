"""End-to-end tests for the advisory/coach system.

1. Human feedback injection — full lifecycle from file write to orchestrator seeing it
2. AI coach integration — real LLM call detecting tenet violations
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from kodo import log
from kodo.advisory import AdvisoryQueue, format_advisories
from kodo.log import RunDir
from kodo.coach import Coach
from kodo.orchestrators.agent_tools import handle_agent_call
from kodo.orchestrators.cycle_utils import build_cycle_prompt
from kodo.orchestrators.types import CycleResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_agent(report_text: str = "Task completed.", is_error: bool = False):
    """Create a mock agent that returns a fixed report."""
    agent = MagicMock()
    result = MagicMock()
    result.format_report.return_value = report_text
    result.is_error = is_error
    result.elapsed_s = 2.0
    result.context_reset = False
    result.session_tokens = 500
    result.text = report_text
    agent.run.return_value = result
    agent.session.cost_bucket = "api"
    return agent


# ---------------------------------------------------------------------------
# Unit tests: Human feedback full lifecycle
# ---------------------------------------------------------------------------


class TestHumanFeedbackLifecycle:
    """Test the complete flow: human writes file → coach reads it →
    advisory injected into tool return → orchestrator can reply."""

    def test_human_writes_feedback_appears_in_next_tool_return(self, tmp_path):
        """Simulate: human writes feedback file, coach polls it,
        next agent tool call includes the advisory."""
        log.init(RunDir.create(tmp_path))

        queue = AdvisoryQueue()
        obs = Coach(queue, "implement auth", tmp_path, assess_every_n=100)

        # Human writes feedback
        feedback_file = tmp_path / "human_feedback.txt"
        feedback_file.write_text("warning: the auth endpoint uses OAuth2, not API keys\n")
        obs._human_feedback_file = feedback_file
        obs._poll_human_feedback()

        # Now simulate an agent tool return that picks up the advisory
        agent = _make_fake_agent("Fixed the login handler.")
        summarizer = MagicMock()

        report = handle_agent_call(
            "worker",
            agent,
            "fix auth endpoint",
            tmp_path,
            summarizer,
            advisory_queue=queue,
            coach=obs,
        )

        assert "Fixed the login handler." in report
        assert "[human]" in report
        assert "OAuth2" in report
        assert queue.pending_count == 0

    def test_human_feedback_with_reply_cycle(self, tmp_path):
        """Full cycle: human advises → appears in tool return →
        orchestrator replies → reply stored on advisory."""
        log.init(RunDir.create(tmp_path))

        queue = AdvisoryQueue()
        obs = Coach(queue, "fix bug", tmp_path, assess_every_n=100)

        # Human writes
        feedback_file = tmp_path / "human_feedback.txt"
        feedback_file.write_text("correction: the database is PostgreSQL, not MySQL\n")
        obs._human_feedback_file = feedback_file
        obs._poll_human_feedback()

        # Get the advisory ID from what was pushed
        advisories = queue.drain()
        assert len(advisories) == 1
        adv = advisories[0]
        assert adv.source == "human"
        assert adv.priority == "correction"
        assert "PostgreSQL" in adv.message

        # Orchestrator replies
        from kodo.orchestrators.tools import _make_reply_to_advisor

        reply_fn = _make_reply_to_advisor(queue)
        result = reply_fn(adv.id, "Understood, switching to PostgreSQL driver")

        assert adv.id in result
        assert adv.orchestrator_response == "Understood, switching to PostgreSQL driver"

    def test_human_feedback_multiple_messages_interleaved_with_agent_calls(
        self, tmp_path
    ):
        """Multiple human messages arrive between agent calls, each gets
        drained at the right time."""
        log.init(RunDir.create(tmp_path))

        queue = AdvisoryQueue()
        obs = Coach(queue, "build API", tmp_path, assess_every_n=100)
        feedback_file = tmp_path / "human_feedback.txt"
        obs._human_feedback_file = feedback_file

        agent = _make_fake_agent("Done with task.")
        summarizer = MagicMock()

        # First human message before first agent call
        feedback_file.write_text("use REST not GraphQL\n")
        obs._poll_human_feedback()

        report1 = handle_agent_call(
            "worker", agent, "task1", tmp_path, summarizer,
            advisory_queue=queue, coach=obs,
        )
        assert "REST not GraphQL" in report1
        assert queue.pending_count == 0

        # Second human message before second agent call
        with open(feedback_file, "a") as f:
            f.write("warning: don't forget rate limiting\n")
        obs._poll_human_feedback()

        report2 = handle_agent_call(
            "worker", agent, "task2", tmp_path, summarizer,
            advisory_queue=queue, coach=obs,
        )
        assert "rate limiting" in report2
        assert "REST not GraphQL" not in report2  # already drained

    def test_human_feedback_empty_lines_and_comments_skipped(self, tmp_path):
        """Comments and empty lines in feedback file are ignored."""
        log.init(RunDir.create(tmp_path))

        queue = AdvisoryQueue()
        obs = Coach(queue, "goal", tmp_path, assess_every_n=100)
        feedback_file = tmp_path / "human_feedback.txt"
        feedback_file.write_text(
            "# This is a comment\n"
            "\n"
            "   \n"
            "# Another comment\n"
            "actual feedback here\n"
        )
        obs._human_feedback_file = feedback_file
        obs._poll_human_feedback()

        drained = queue.drain()
        assert len(drained) == 1
        assert drained[0].message == "actual feedback here"

    def test_between_cycle_injection_of_human_feedback(self, tmp_path):
        """Human feedback injected into between-cycle prompt."""
        log.init(RunDir.create(tmp_path))

        queue = AdvisoryQueue()
        queue.push("you're overcomplicating this", source="human", priority="warning")

        with patch(
            "kodo.orchestrators.run_status.read_run_status",
            autospec=True,
            return_value="",
        ):
            prompt = build_cycle_prompt(
                "build auth", tmp_path, "prior cycle work", advisory_queue=queue
            )

        assert "# Goal" in prompt
        assert "# Previous progress" in prompt
        assert "# Feedback" in prompt
        assert "overcomplicating" in prompt
        assert queue.pending_count == 0

    def test_no_feedback_file_doesnt_crash(self, tmp_path):
        """Coach handles missing feedback file gracefully."""
        queue = AdvisoryQueue()
        obs = Coach(queue, "goal", tmp_path, assess_every_n=100)
        obs._human_feedback_file = tmp_path / "nonexistent.txt"
        obs._poll_human_feedback()  # should not raise
        assert queue.pending_count == 0

    def test_coach_background_thread_polls_feedback(self, tmp_path):
        """Coach's background thread picks up feedback written to file."""
        log.init(RunDir.create(tmp_path))

        queue = AdvisoryQueue()
        obs = Coach(queue, "goal", tmp_path, assess_every_n=100, poll_interval=0.1)

        # Write feedback file before starting coach
        feedback_file = tmp_path / "human_feedback.txt"

        # Set up the coach's feedback file path manually (normally done by start())
        obs._human_feedback_file = feedback_file

        # Start coach thread
        obs._stop = threading.Event()
        obs._thread = threading.Thread(target=obs._run_loop, name="test-coach", daemon=True)
        obs._thread.start()

        try:
            # Write feedback after coach started
            feedback_file.write_text("background thread test message\n")

            deadline = time.time() + 5
            while queue.pending_count == 0 and time.time() < deadline:
                time.sleep(0.05)

            assert queue.pending_count >= 1
            drained = queue.drain()
            assert any("background thread test message" in a.message for a in drained)
        finally:
            obs.stop()


# ---------------------------------------------------------------------------
# Integration test: AI coach detects tenet violations
# ---------------------------------------------------------------------------


def _real_gemini_key() -> str | None:
    """Return the real Gemini API key, ignoring fake test placeholders."""
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(var, "")
        if val and not val.startswith("fake"):
            return val
    return None


@pytest.mark.live
class TestAICoachTenetViolation:
    """Integration test: use real LLM (Gemini Flash) to verify the coach
    detects orchestration tenet violations from activity patterns.

    These tests make real API calls to Gemini Flash and are skipped when
    no real API key is available (conftest.py injects fake keys that
    are filtered out).
    """

    @pytest.fixture(autouse=True)
    def _ensure_real_key(self):
        """Ensure the real API key is in the environment, not a fake.

        The conftest.py session fixture injects fake keys for both
        GEMINI_API_KEY and GOOGLE_API_KEY. Since _call_llm checks
        GEMINI_API_KEY first, we must remove the fake and set
        GOOGLE_API_KEY to the real value.
        """
        key = _real_gemini_key()
        if not key:
            pytest.skip("No real Gemini API key available")
        saved = {}
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            saved[var] = os.environ.get(var)
        # Remove fake GEMINI_API_KEY so _call_llm falls through to GOOGLE_API_KEY
        if os.environ.get("GEMINI_API_KEY", "").startswith("fake"):
            del os.environ["GEMINI_API_KEY"]
        os.environ["GOOGLE_API_KEY"] = key
        yield
        for var, val in saved.items():
            if val is not None:
                os.environ[var] = val
            else:
                os.environ.pop(var, None)

    def test_coach_detects_circles(self, tmp_path):
        """Coach should flag when the same task is dispatched repeatedly
        (tenet violation: circles/drift)."""
        log.init(RunDir.create(tmp_path))

        queue = AdvisoryQueue()
        obs = Coach(
            queue,
            "Fix the login bug in auth.py",
            tmp_path,
            assess_every_n=999,  # disable auto-assess, we call _assess manually
        )

        # Simulate orchestrator going in circles — same task dispatched 6 times
        for i in range(6):
            obs.record_dispatch("worker", "Fix the failing test in test_auth.py")
            obs.record_result("worker", "Fix the failing test in test_auth.py", is_error=True)

        # Trigger assessment synchronously
        obs._assess()

        # Coach should have pushed feedback about circles/repetition
        drained = queue.drain()

        # We expect at least one advisory about the repetitive pattern
        assert len(drained) >= 1, (
            "Coach should have detected the circular pattern of 6 identical "
            "failing dispatches"
        )
        # Check that the feedback is relevant
        combined = " ".join(a.message.lower() for a in drained)
        has_relevant_feedback = any(
            keyword in combined
            for keyword in [
                "repeat", "circle", "loop", "same", "again",
                "fail", "stuck", "pattern", "drift",
                "different", "approach", "root cause", "investigate",
            ]
        )
        assert has_relevant_feedback, (
            f"Coach feedback should mention repetition/circles. Got: {combined}"
        )

    def test_coach_stays_silent_on_healthy_pattern(self, tmp_path):
        """Coach should NOT generate feedback when orchestrator is
        working productively with varied, successful tasks."""
        log.init(RunDir.create(tmp_path))

        queue = AdvisoryQueue()
        obs = Coach(
            queue,
            "Build a REST API for user management",
            tmp_path,
            assess_every_n=999,
        )

        # Simulate healthy orchestration — varied tasks, all succeeding
        healthy_tasks = [
            "Implement user registration endpoint with validation",
            "Add authentication middleware with JWT tokens",
            "Write integration tests for the registration flow",
            "Implement user profile update endpoint",
        ]
        for task in healthy_tasks:
            obs.record_dispatch("worker", task)
            obs.record_result("worker", task, is_error=False)

        obs._assess()

        drained = queue.drain()
        # Coach might stay silent (ideal) or give low-priority info
        critical = [a for a in drained if a.priority in ("warning", "correction")]
        assert len(critical) == 0, (
            f"Coach should not raise warnings for healthy orchestration. "
            f"Got: {[a.message for a in critical]}"
        )

    def test_coach_detects_ignored_failures(self, tmp_path):
        """Coach should flag when workers keep failing but orchestrator
        doesn't change approach (tenet: ignored signals)."""
        log.init(RunDir.create(tmp_path))

        queue = AdvisoryQueue()
        obs = Coach(
            queue,
            "Deploy the application to production",
            tmp_path,
            assess_every_n=999,
        )

        # Worker keeps failing on different but related tasks — orchestrator
        # never investigates why
        for i in range(5):
            obs.record_dispatch("worker", f"Run deployment script (attempt {i+1})")
            obs.record_result("worker", f"Run deployment script (attempt {i+1})", is_error=True)

        obs._assess()

        drained = queue.drain()
        assert len(drained) >= 1, (
            "Coach should flag repeated deployment failures"
        )
        combined = " ".join(a.message.lower() for a in drained)
        has_relevant_feedback = any(
            keyword in combined
            for keyword in [
                "fail", "error", "investig", "why", "root",
                "repeat", "attempt", "same", "approach",
                "diagnos", "cause",
            ]
        )
        assert has_relevant_feedback, (
            f"Coach should mention repeated failures. Got: {combined}"
        )


# ---------------------------------------------------------------------------
# Integration: coach context includes prior replies
# ---------------------------------------------------------------------------


class TestCoachEventMessage:
    """Verify the coach's event message includes dispatch/result info."""

    def test_event_message_includes_result_details(self, tmp_path):
        queue = AdvisoryQueue()
        obs = Coach(queue, "fix auth", tmp_path, assess_every_n=100)

        obs.record_dispatch("worker", "refactor auth module")
        obs.record_result("worker", "refactor auth module", False, report="refactored successfully")

        msg = obs._build_event_message()
        assert "refactor auth module" in msg
        assert "refactored successfully" in msg
        assert "ok" in msg
