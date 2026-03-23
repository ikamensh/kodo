"""Tests for the advisory queue and coach feedback system."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from kodo.advisory import (
    Advisory,
    AdvisoryQueue,
    format_advisories,
    format_advisories_for_prompt,
)


# ---------------------------------------------------------------------------
# Advisory dataclass
# ---------------------------------------------------------------------------


class TestAdvisory:
    def test_source_label(self):
        a = Advisory(id="adv_0001", message="test", source="human", priority="info")
        assert a.source_label == "HUMAN"

        a2 = Advisory(id="adv_0002", message="test", source="coach", priority="info")
        assert a2.source_label == "COACH"

    def test_priority_icon(self):
        for priority, expected in [("info", "ℹ️"), ("warning", "⚠️"), ("correction", "🚨")]:
            a = Advisory(id="x", message="m", source="human", priority=priority)
            assert a.priority_icon == expected

    def test_orchestrator_response_default_none(self):
        a = Advisory(id="x", message="m", source="human", priority="info")
        assert a.orchestrator_response is None


# ---------------------------------------------------------------------------
# AdvisoryQueue
# ---------------------------------------------------------------------------


class TestAdvisoryQueue:
    def test_push_and_drain(self):
        q = AdvisoryQueue()
        q.push("msg1", source="human", priority="warning")
        q.push("msg2", source="coach")
        assert q.pending_count == 2

        drained = q.drain()
        assert len(drained) == 2
        assert drained[0].message == "msg1"
        assert drained[0].source == "human"
        assert drained[0].priority == "warning"
        assert drained[1].message == "msg2"
        assert drained[1].source == "coach"
        assert q.pending_count == 0

    def test_drain_empty(self):
        q = AdvisoryQueue()
        assert q.drain() == []

    def test_drain_moves_to_history(self):
        q = AdvisoryQueue()
        q.push("msg", source="human")
        q.drain()
        history = q.get_history()
        assert len(history) == 1
        assert history[0].message == "msg"

    def test_record_reply_in_history(self):
        q = AdvisoryQueue()
        adv = q.push("advice", source="coach")
        q.drain()  # move to history
        assert q.record_reply(adv.id, "understood")
        history = q.get_history()
        assert history[0].orchestrator_response == "understood"

    def test_record_reply_in_pending(self):
        q = AdvisoryQueue()
        adv = q.push("advice", source="human")
        assert q.record_reply(adv.id, "will do")
        drained = q.drain()
        assert drained[0].orchestrator_response == "will do"

    def test_record_reply_not_found(self):
        q = AdvisoryQueue()
        assert not q.record_reply("nonexistent", "reply")

    def test_unique_ids(self):
        q = AdvisoryQueue()
        a1 = q.push("m1", source="human")
        a2 = q.push("m2", source="coach")
        assert a1.id != a2.id

    def test_thread_safety(self):
        """Multiple threads pushing concurrently should not lose messages."""
        q = AdvisoryQueue()
        n_threads = 10
        n_per_thread = 50

        def push_many(thread_id: int):
            for i in range(n_per_thread):
                q.push(f"t{thread_id}-{i}", source="coach")

        threads = [threading.Thread(target=push_many, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        drained = q.drain()
        assert len(drained) == n_threads * n_per_thread


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


class TestFormatAdvisories:
    def test_empty(self):
        assert format_advisories([]) == ""

    def test_single_advisory(self):
        advs = [Advisory(id="adv_0001", message="drift detected", source="coach", priority="warning")]
        result = format_advisories(advs)
        assert "[coach]" in result
        assert "drift detected" in result

    def test_sources_labeled(self):
        advs = [
            Advisory(id="adv_0001", message="msg1", source="coach", priority="info"),
            Advisory(id="adv_0002", message="msg2", source="human", priority="correction"),
        ]
        result = format_advisories(advs)
        assert "coach" in result
        assert "human" in result


class TestFormatAdvisoriesForPrompt:
    def test_empty(self):
        assert format_advisories_for_prompt([]) == ""

    def test_produces_prompt_section(self):
        advs = [Advisory(id="x", message="focus on tests", source="human", priority="warning")]
        result = format_advisories_for_prompt(advs)
        assert "# Feedback" in result
        assert "[human]" in result
        assert "focus on tests" in result


# ---------------------------------------------------------------------------
# Integration: advisory injection in handle_agent_call
# ---------------------------------------------------------------------------


class TestAdvisoryInjectionInAgentCall:
    @patch("kodo.log.print_stats_table")  # noqa: autospec
    @patch("kodo.log.tprint")  # noqa: autospec
    @patch("kodo.log.emit")  # noqa: autospec
    def test_advisories_appended_to_report(self, mock_emit, mock_tprint, mock_stats):
        from kodo.advisory import AdvisoryQueue
        from kodo.orchestrators.agent_tools import handle_agent_call

        queue = AdvisoryQueue()
        queue.push("you're going in circles", source="coach", priority="warning")

        # Create a fake agent
        agent = MagicMock()
        result = MagicMock()
        result.format_report.return_value = "Agent completed task."
        result.is_error = False
        result.elapsed_s = 1.0
        result.context_reset = False
        result.session_tokens = 100
        result.text = "done"
        agent.run.return_value = result
        agent.session.cost_bucket = "api"

        summarizer = MagicMock()

        report = handle_agent_call(
            "worker",
            agent,
            "fix the bug",
            Path("/tmp/test"),
            summarizer,
            advisory_queue=queue,
        )

        assert "Agent completed task." in report
        assert "coach" in report
        assert "you're going in circles" in report
        assert queue.pending_count == 0  # drained

    @patch("kodo.log.print_stats_table")  # noqa: autospec
    @patch("kodo.log.tprint")  # noqa: autospec
    @patch("kodo.log.emit")  # noqa: autospec
    def test_no_advisories_means_clean_report(self, mock_emit, mock_tprint, mock_stats):
        from kodo.advisory import AdvisoryQueue
        from kodo.orchestrators.agent_tools import handle_agent_call

        queue = AdvisoryQueue()  # empty

        agent = MagicMock()
        result = MagicMock()
        result.format_report.return_value = "Agent completed task."
        result.is_error = False
        result.elapsed_s = 1.0
        result.context_reset = False
        result.session_tokens = 100
        result.text = "done"
        agent.run.return_value = result
        agent.session.cost_bucket = "api"

        summarizer = MagicMock()

        report = handle_agent_call(
            "worker",
            agent,
            "fix the bug",
            Path("/tmp/test"),
            summarizer,
            advisory_queue=queue,
        )

        assert report == "Agent completed task."
        assert "ADVISOR" not in report


# ---------------------------------------------------------------------------
# Integration: reply_to_advisor tool
# ---------------------------------------------------------------------------


class TestReplyToAdvisorTool:
    def test_reply_recorded(self):
        from kodo.orchestrators.tools import _make_reply_to_advisor

        queue = AdvisoryQueue()
        adv = queue.push("bad approach", source="coach")
        queue.drain()

        reply_fn = _make_reply_to_advisor(queue)
        result = reply_fn(adv.id, "I disagree because X")

        assert adv.id in result
        history = queue.get_history()
        assert history[0].orchestrator_response == "I disagree because X"

    def test_reply_not_found(self):
        from kodo.orchestrators.tools import _make_reply_to_advisor

        queue = AdvisoryQueue()
        reply_fn = _make_reply_to_advisor(queue)
        result = reply_fn("adv_9999", "reply")
        assert "not found" in result


# ---------------------------------------------------------------------------
# Coach
# ---------------------------------------------------------------------------


class TestCoach:
    def test_record_dispatch_accumulates(self):
        from kodo.coach import Coach

        queue = AdvisoryQueue()
        obs = Coach(queue, "fix bug", Path("/tmp"), assess_every_n=100)
        obs.record_dispatch("worker", "fix tests")
        obs.record_dispatch("worker", "fix linting")
        assert len(obs._dispatches) == 2

    def test_record_result_tracks_errors(self):
        from kodo.coach import Coach

        queue = AdvisoryQueue()
        obs = Coach(queue, "fix bug", Path("/tmp"), assess_every_n=100)
        obs.record_result("worker", "task1", is_error=False)
        obs.record_result("worker", "task2", is_error=True)
        assert len(obs._results) == 2
        assert len(obs._errors) == 1

    def test_human_feedback_file_parsing(self, tmp_path):
        from kodo.coach import Coach

        queue = AdvisoryQueue()
        obs = Coach(queue, "fix bug", tmp_path, assess_every_n=100)

        feedback_file = tmp_path / "human_feedback.txt"
        feedback_file.write_text(
            "# comment\n"
            "stop refactoring\n"
            "warning: tests are broken\n"
            "correction: use OAuth2 not API keys\n"
        )
        obs._human_feedback_file = feedback_file
        obs._poll_human_feedback()

        drained = queue.drain()
        assert len(drained) == 3
        assert drained[0].message == "stop refactoring"
        assert drained[0].priority == "info"
        assert drained[1].message == "tests are broken"
        assert drained[1].priority == "warning"
        assert drained[2].message == "use OAuth2 not API keys"
        assert drained[2].priority == "correction"

    def test_human_feedback_incremental_read(self, tmp_path):
        """Second poll should only read new lines."""
        from kodo.coach import Coach

        queue = AdvisoryQueue()
        obs = Coach(queue, "goal", tmp_path, assess_every_n=100)

        feedback_file = tmp_path / "human_feedback.txt"
        feedback_file.write_text("first message\n")
        obs._human_feedback_file = feedback_file
        obs._poll_human_feedback()
        assert queue.pending_count == 1
        queue.drain()

        # Append more
        with open(feedback_file, "a") as f:
            f.write("second message\n")
        obs._poll_human_feedback()
        assert queue.pending_count == 1
        drained = queue.drain()
        assert drained[0].message == "second message"

    def test_build_assess_prompt(self):
        from kodo.coach import Coach

        queue = AdvisoryQueue()
        obs = Coach(queue, "implement auth", Path("/tmp"), assess_every_n=100)
        obs.record_dispatch("worker", "add login endpoint")
        obs.record_dispatch("worker", "add login endpoint")
        obs.record_dispatch("worker", "fix test")

        prompt = obs._build_assess_prompt()
        assert "implement auth" in prompt
        assert "add login endpoint" in prompt
        assert "REPEATED TASKS" in prompt
        assert "2x" in prompt

    def test_start_stop(self):
        from kodo.coach import Coach

        queue = AdvisoryQueue()
        obs = Coach(queue, "goal", Path("/tmp"), assess_every_n=100)
        obs._log_file = None  # skip log file setup
        obs._thread = threading.Thread(target=lambda: None, daemon=True)
        obs._thread.start()
        obs._thread.join(timeout=1)
        obs.stop()
        # Should not raise


# ---------------------------------------------------------------------------
# Integration: build_cycle_prompt with advisories
# ---------------------------------------------------------------------------


class TestCyclePromptAdvisoryInjection:
    def test_advisories_injected_between_cycles(self):
        from kodo.orchestrators.cycle_utils import build_cycle_prompt

        queue = AdvisoryQueue()
        queue.push("focus on core functionality", source="coach", priority="warning")

        # Need to patch read_run_status
        with patch("kodo.orchestrators.run_status.read_run_status", autospec=True, return_value=""):
            prompt = build_cycle_prompt(
                "fix auth", Path("/tmp"), "prior work", advisory_queue=queue
            )

        assert "# Feedback" in prompt
        assert "focus on core functionality" in prompt
        assert queue.pending_count == 0

    def test_no_advisories_clean_prompt(self):
        from kodo.orchestrators.cycle_utils import build_cycle_prompt

        queue = AdvisoryQueue()

        with patch("kodo.orchestrators.run_status.read_run_status", autospec=True, return_value=""):
            prompt = build_cycle_prompt(
                "fix auth", Path("/tmp"), "prior work", advisory_queue=queue
            )

        assert "Advisor" not in prompt
