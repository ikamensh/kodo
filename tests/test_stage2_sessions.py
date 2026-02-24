"""Tests for session layer and CLI orchestrators — finding real bugs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


from kodo.sessions.base import QueryResult, SubprocessSession
from kodo.sessions.claude import ClaudeSession
from kodo.sessions.cursor import CursorSession


# ---------------------------------------------------------------------------
# ClaudeSession tests
# ---------------------------------------------------------------------------


class TestClaudeSession:
    def test_init_stores_expected_kwargs(self):
        """ClaudeSession.__init__ accepts and stores expected kwargs."""
        session = ClaudeSession(
            model="opus",
            system_prompt="You are helpful.",
            chrome=True,
            fallback_model="sonnet",
            use_api_key=True,
            resume_session_id="sess_123",
        )
        assert session.model == "opus"
        assert session.system_prompt == "You are helpful."
        assert session.chrome is True
        assert session.fallback_model == "sonnet"
        assert session.use_api_key is True
        assert session.resume_session_id == "sess_123"
        assert session._client is None
        assert session._stats.queries == 0

    def test_terminate_when_no_process_is_safe_noop(self):
        """ClaudeSession.terminate() when no client — safe no-op, no crash."""
        session = ClaudeSession(model="sonnet")
        session.terminate()
        session.terminate()  # double-call also safe

    def test_reset_clears_stats_and_state(self):
        """ClaudeSession.reset() clears stats and disconnects."""
        session = ClaudeSession(model="sonnet")
        session._stats.queries = 5
        session._stats.total_input_tokens = 100
        session.reset()
        assert session._stats.queries == 0
        assert session._stats.total_tokens == 0
        assert session._client is None


# ---------------------------------------------------------------------------
# SubprocessSession tests (via concrete subclass)
# ---------------------------------------------------------------------------


class _TestSubprocessSession(SubprocessSession):
    """Minimal subclass to test _spawn/_wait stderr cap."""

    _session_label = "test"

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        # Spawn process that writes 15k lines to stderr
        cmd = [
            sys.executable,
            "-c",
            "import sys; [sys.stderr.write(f'{i}\\n') for i in range(15000)]",
        ]
        proc, stderr_chunks, thread = self._spawn(cmd)
        stderr_text = self._wait(proc, stderr_chunks, thread)
        # Cap is 10k lines + 1 truncation line
        assert len(stderr_chunks) <= 10_001, "stderr should be capped at 10k lines"
        assert "[... stderr truncated ...]" in stderr_text
        return QueryResult(text="ok", elapsed_s=0.0)


def test_stderr_buffering_cap(tmp_path: Path):
    """SubprocessSession stderr cap: >10k lines does not grow unbounded."""
    session = _TestSubprocessSession(model="test")
    result = session.query("ignored", tmp_path, max_turns=1)
    assert result.text == "ok"


def test_terminate_on_dead_process_no_crash(tmp_path: Path):
    """SubprocessSession.terminate() on already-dead process — no crash."""
    session = _TestSubprocessSession(model="test")
    proc, stderr_chunks, thread = session._spawn(
        [sys.executable, "-c", "pass"]  # exits immediately
    )
    proc.wait()
    assert proc.poll() is not None
    session.terminate()
    session.terminate()


# ---------------------------------------------------------------------------
# CursorSession tests
# ---------------------------------------------------------------------------


class TestCursorSession:
    def test_init_and_config_structure(self):
        """CursorSession initialization and config structure."""
        session = CursorSession(
            model="composer-1.5",
            system_prompt="Helper",
            resume_chat_id="chat_abc",
        )
        assert session.model == "composer-1.5"
        assert session.system_prompt == "Helper"
        assert session._chat_id == "chat_abc"
        assert session.cost_bucket == "cursor_subscription"
        assert session.session_id == "chat_abc"
        assert session._stats.queries == 0


# ---------------------------------------------------------------------------
# ClaudeCodeOrchestrator cycle result parsing
# ---------------------------------------------------------------------------


class TestClaudeCodeOrchestrator:
    def test_cycle_constructs_cycle_result_from_mock_sdk(self, tmp_path: Path):
        """ClaudeCodeOrchestrator.cycle() — mock SDK, verify CycleResult."""
        from kodo.orchestrators.claude_code import ClaudeCodeOrchestrator
        from kodo.orchestrators.base import CycleResult
        from tests.conftest import make_agent

        # Fake ResultMessage — patch so isinstance() passes in cycle()
        class FakeResultMessage:
            num_turns = 3
            total_cost_usd = 0.05
            result = "Task completed."
            is_error = False

        result_msg = FakeResultMessage()

        async def fake_receive():
            yield result_msg

        async def fake_connect():
            pass

        async def fake_query(prompt):
            pass

        async def fake_disconnect():
            pass

        mock_client = MagicMock()
        mock_client.connect = fake_connect
        mock_client.query = fake_query
        mock_client.disconnect = fake_disconnect
        mock_client.receive_response = fake_receive

        def fake_sdk_client(options):
            return mock_client

        team = {"worker": make_agent()}

        with (
            patch("claude_agent_sdk.ClaudeSDKClient", fake_sdk_client),
            patch("claude_agent_sdk.ResultMessage", FakeResultMessage),
            patch("kodo.log.init"),
        ):
            orch = ClaudeCodeOrchestrator(model="opus")
            result = orch.cycle("Build X", tmp_path, team, max_exchanges=10)

        assert isinstance(result, CycleResult)
        assert result.exchanges >= 3
        assert result.total_cost_usd >= 0.05
        assert result.summary == "Task completed."
        assert result.finished is False  # done not called


# ---------------------------------------------------------------------------
# GeminiCliOrchestrator cycle result parsing
# ---------------------------------------------------------------------------


class TestGeminiCliOrchestrator:
    def test_cycle_parses_json_output_correctly(self, tmp_path: Path):
        """GeminiCliOrchestrator.cycle() — mock subprocess, verify CycleResult."""
        from kodo.orchestrators.gemini_cli import GeminiCliOrchestrator
        from kodo.orchestrators.base import CycleResult
        from tests.conftest import make_agent

        team = {"worker": make_agent()}

        gemini_response = {
            "response": "I built the feature.",
            "stats": {
                "models": {
                    "gemini-2.5-flash": {
                        "tokens": {"prompt": 1000, "candidates": 500},
                    }
                }
            },
        }

        call_count = [0]

        def fake_run(cmd, **kwargs):
            call_count[0] += 1
            if "mcp" in cmd and "add" in cmd:
                return MagicMock(returncode=0)
            if "mcp" in cmd and "remove" in cmd:
                return MagicMock(returncode=0)
            return MagicMock(
                returncode=0,
                stdout=json.dumps(gemini_response),
                stderr="",
            )

        ctx_obj = MagicMock()
        ctx_obj.sse_url = "http://localhost:0"

        with (
            patch("kodo.orchestrators.gemini_cli.subprocess.run", side_effect=fake_run),
            patch("kodo.orchestrators.gemini_cli.McpServerContext") as mock_ctx,
            patch("kodo.log.init"),
        ):
            mock_ctx.return_value.__enter__.return_value = ctx_obj

            orch = GeminiCliOrchestrator(model="gemini-2.5-flash")
            result = orch.cycle("Build X", tmp_path, team, max_exchanges=10)

        assert isinstance(result, CycleResult)
        assert result.summary == "I built the feature."
        assert result.exchanges >= 1
        assert result.finished is False

    def test_cycle_handles_garbled_json_output(self, tmp_path: Path):
        """Gemini CLI returns invalid JSON — fallback to raw stdout."""
        from kodo.orchestrators.gemini_cli import GeminiCliOrchestrator
        from tests.conftest import make_agent

        team = {"worker": make_agent()}

        def fake_run(cmd, **kwargs):
            if "mcp" in cmd and "add" in cmd:
                return MagicMock(returncode=0)
            if "mcp" in cmd and "remove" in cmd:
                return MagicMock(returncode=0)
            return MagicMock(
                returncode=0,
                stdout="not valid json {{{",
                stderr="",
            )

        ctx_obj = MagicMock()
        ctx_obj.sse_url = "http://localhost:0"

        with (
            patch("kodo.orchestrators.gemini_cli.subprocess.run", side_effect=fake_run),
            patch("kodo.orchestrators.gemini_cli.McpServerContext") as mock_ctx,
            patch("kodo.log.init"),
        ):
            mock_ctx.return_value.__enter__.return_value = ctx_obj

            orch = GeminiCliOrchestrator(model="gemini-2.5-flash")
            result = orch.cycle("Build X", tmp_path, team, max_exchanges=10)

        assert result.summary == "not valid json {{{"

    def test_cycle_handles_timeout(self, tmp_path: Path):
        """Gemini CLI times out — CycleResult has finished=False, summary set."""
        from kodo.orchestrators.gemini_cli import GeminiCliOrchestrator
        from tests.conftest import make_agent

        team = {"worker": make_agent()}

        gemini_called = [False]

        def fake_run(cmd, **kwargs):
            if "mcp" in cmd and "add" in cmd:
                return MagicMock(returncode=0)
            if "mcp" in cmd and "remove" in cmd:
                return MagicMock(returncode=0)
            if "-p" in cmd:
                gemini_called[0] = True
                raise subprocess.TimeoutExpired(cmd="gemini", timeout=120)
            return MagicMock(returncode=0)

        ctx_obj = MagicMock()
        ctx_obj.sse_url = "http://localhost:0"

        with (
            patch("kodo.orchestrators.gemini_cli.subprocess.run", side_effect=fake_run),
            patch("kodo.orchestrators.gemini_cli.McpServerContext") as mock_ctx,
            patch("kodo.log.init"),
        ):
            mock_ctx.return_value.__enter__.return_value = ctx_obj

            orch = GeminiCliOrchestrator(model="gemini-2.5-flash")
            result = orch.cycle("Build X", tmp_path, team, max_exchanges=10)

        assert result.finished is False
        assert "timed out" in result.summary.lower()
