"""Unit-test versions of test_live.py assertions.

Same structure and assertions as live tests, but use mocks so they run without
real backends. Kept in sync with test_live.py for CI coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from kodo import log
from kodo.log import RunDir
from kodo.sessions.base import QueryResult
from tests.mocks.claude_sdk import (
    MockAssistantMessage,
    MockClaudeAgentOptions,
    MockClaudeSDKClient,
    MockPermissionResultAllow,
    MockPermissionResultDeny,
    MockResultMessage,
    MockTextBlock,
    MockToolUseBlock,
)
from tests.mocks.codex_process import MockCodexProcess
from tests.mocks.gemini_cli_process import MockGeminiCliProcess

SIMPLE_PROMPT = "Reply with the word 'ok'. Do not use any tools."


def _gemini_popen(cmd, **kwargs):
    return MockGeminiCliProcess(
        cmd,
        result_text="ok",
        input_tokens=10,
        output_tokens=5,
        **kwargs,
    )


def _codex_popen(cmd, **kwargs):
    return MockCodexProcess(
        cmd,
        result_text="ok",
        session_id="t1",
        input_tokens=10,
        output_tokens=5,
        **kwargs,
    )


def _install_claude_mock():
    resp = MockResultMessage(
        result="ok",
        num_turns=1,
        total_cost_usd=0.01,
        usage={"input_tokens": 10, "output_tokens": 5},
    )
    mock_client = MockClaudeSDKClient(responses=[resp])
    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = MockClaudeAgentOptions
    fake_mod.ClaudeSDKClient = lambda options=None: mock_client
    fake_mod.ResultMessage = MockResultMessage
    fake_mod.AssistantMessage = MockAssistantMessage
    fake_types = ModuleType("claude_agent_sdk.types")
    fake_types.PermissionResultAllow = MockPermissionResultAllow
    fake_types.PermissionResultDeny = MockPermissionResultDeny
    fake_types.TextBlock = MockTextBlock
    fake_types.ToolUseBlock = MockToolUseBlock
    return mock_client, {
        "claude_agent_sdk": fake_mod,
        "claude_agent_sdk.types": fake_types,
    }


# ---------------------------------------------------------------------------
# Gemini CLI — mirrors TestGeminiCliSession
# ---------------------------------------------------------------------------


class TestGeminiCliSessionUnit:
    @pytest.fixture
    def result_and_session(self, tmp_path: Path):
        log.init(RunDir.create(tmp_path, "gemini_live_unit"))
        from kodo.sessions.gemini_cli import GeminiCliSession

        session = GeminiCliSession(
            model="gemini-2.5-flash-lite",
            system_prompt="You are a helpful assistant.",
        )
        with patch("kodo.sessions.base.subprocess.Popen", _gemini_popen):
            result = session.query(SIMPLE_PROMPT, tmp_path, max_turns=5)
        return result, session

    def test_returns_nonempty(self, result_and_session) -> None:
        result, session = result_and_session
        assert isinstance(result, QueryResult)
        assert not result.is_error, f"Session returned error: {result.text}"
        assert result.text.strip(), "Response text is empty"
        assert result.elapsed_s > 0
        assert session.stats.queries == 1

    def test_tracks_tokens(self, result_and_session) -> None:
        result, session = result_and_session
        assert session.stats.total_input_tokens > 0, "No input tokens tracked"
        assert session.stats.total_output_tokens > 0, "No output tokens tracked"

    def test_system_prompt_did_not_crash(self, result_and_session) -> None:
        result, _ = result_and_session
        assert not result.is_error


# ---------------------------------------------------------------------------
# Codex — mirrors TestCodexSession
# ---------------------------------------------------------------------------


class TestCodexSessionUnit:
    @pytest.fixture
    def result_and_session(self, tmp_path: Path):
        log.init(RunDir.create(tmp_path, "codex_live_unit"))
        from kodo.sessions.codex import CodexSession

        session = CodexSession(model="gpt-5.2-codex")
        with patch("kodo.sessions.base.subprocess.Popen", _codex_popen):
            result = session.query(SIMPLE_PROMPT, tmp_path, max_turns=5)
        return result, session

    def test_returns_nonempty(self, result_and_session) -> None:
        result, session = result_and_session
        assert isinstance(result, QueryResult)
        assert not result.is_error, f"Session returned error: {result.text}"
        assert result.text.strip(), "Response text is empty"
        assert result.elapsed_s > 0
        assert session.stats.queries == 1

    def test_tracks_tokens(self, result_and_session) -> None:
        _, session = result_and_session
        assert (
            session.stats.total_input_tokens > 0
            or session.stats.total_output_tokens > 0
        ), "No tokens tracked"

    def test_bad_model_returns_error(self, tmp_path: Path) -> None:
        log.init(RunDir.create(tmp_path, "codex_bad_model_unit"))
        from kodo.sessions.codex import CodexSession

        session = CodexSession(model="nonexistent-model-xyz")
        with patch(
            "kodo.sessions.base.subprocess.Popen",
            lambda cmd, **kw: MockCodexProcess(
                cmd, error_message="model does not exist", **kw
            ),
        ):
            result = session.query(SIMPLE_PROMPT, tmp_path, max_turns=5)

        assert result.is_error, "Bad model should return is_error=True"
        assert result.text, "Bad model should return an error message"
        assert "not supported" in result.text or "does not exist" in result.text, (
            f"Error should mention model issue: {result.text!r}"
        )


# ---------------------------------------------------------------------------
# Claude Code — mirrors TestClaudeSession
# ---------------------------------------------------------------------------


class TestClaudeSessionUnit:
    @pytest.fixture
    def result_and_session(self, tmp_path: Path):
        log.init(RunDir.create(tmp_path, "claude_live_unit"))
        mock_client, fake_modules = _install_claude_mock()

        with patch.dict(sys.modules, fake_modules):
            from kodo.sessions.claude import ClaudeSession

            session = ClaudeSession(model="sonnet", use_api_key=True)
            try:
                result = session.query(SIMPLE_PROMPT, tmp_path, max_turns=5)
                return result, session
            finally:
                session.close()

    def test_returns_nonempty(self, result_and_session) -> None:
        result, session = result_and_session
        assert isinstance(result, QueryResult)
        assert not result.is_error, f"Session returned error: {result.text}"
        assert result.text.strip(), "Response text is empty"
        assert result.elapsed_s > 0
        assert session.stats.queries == 1

    def test_tracks_tokens(self, result_and_session) -> None:
        _, session = result_and_session
        assert session.stats.total_cost_usd >= 0
