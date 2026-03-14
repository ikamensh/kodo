"""Tests for ClaudeCodeOrchestrator.cycle() — nudge loop, cost tracking, error handling."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@contextlib.contextmanager
def _base_patches(fake_client_cls, done_signal):
    """Context manager that stubs out SDK + MCP + logging."""
    mock_mcp = MagicMock(_mcp_server=MagicMock())
    env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with (
        patch.dict(os.environ, env_without_key, clear=True),
        patch("claude_agent_sdk.ClaudeSDKClient", fake_client_cls),
        patch(
            "kodo.orchestrators.claude_code.build_mcp_server", autospec=True, return_value=mock_mcp
        ),
        patch("kodo.orchestrators.claude_code.build_cycle_prompt", autospec=True, return_value="go"),
        patch("kodo.orchestrators.claude_code.DoneSignal", autospec=True, return_value=done_signal),
        patch("kodo.orchestrators.claude_code.VerificationState", autospec=True),
        patch("kodo.orchestrators.claude_code.log", autospec=True),
    ):
        yield


def _make_client(messages, disconnect_exc=None):
    """Build a FakeClient class yielding *messages* from receive_response."""
    class FakeClient:
        def __init__(self, options=None):
            self._queries = 0

        async def connect(self):
            pass

        async def query(self, prompt):
            self._queries += 1

        async def receive_response(self):
            for msg in messages:
                yield msg

        async def disconnect(self):
            if disconnect_exc:
                raise disconnect_exc

    return FakeClient


def _result_msg(*, is_error=False, num_turns=1, cost=0.01, result="done"):
    from claude_agent_sdk import ResultMessage
    return ResultMessage(
        subtype="result",
        duration_ms=100,
        duration_api_ms=50,
        is_error=is_error,
        num_turns=num_turns,
        session_id="fake-session",
        total_cost_usd=cost,
        result=result,
    )


def _make_orch():
    from kodo.orchestrators.claude_code import ClaudeCodeOrchestrator
    return ClaudeCodeOrchestrator(model="sonnet")


# ── Cost & exchange tracking ────────────────────────────────────────────


class TestCycleTracking:
    def test_accumulates_exchanges_and_cost(self, tmp_path: Path):
        """ResultMessage.num_turns and total_cost_usd are summed into CycleResult."""
        done = MagicMock(called=True, success=True, summary="all done")
        msg = _result_msg(num_turns=3, cost=0.05)
        FakeClient = _make_client([msg])

        with _base_patches(FakeClient, done):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(spec=dict), max_exchanges=5,
            )

        assert result.exchanges >= 3
        assert result.total_cost_usd >= 0.05

    def test_done_signal_applies_summary(self, tmp_path: Path):
        """When done_signal.called is True, apply_done_signal sets the result."""
        done = MagicMock(called=True, success=True, summary="task completed")
        FakeClient = _make_client([_result_msg()])

        with _base_patches(FakeClient, done):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(spec=dict), max_exchanges=5,
            )

        assert result.finished is True
        assert "task completed" in result.summary


# ── Error result handling ───────────────────────────────────────────────


class TestCycleErrors:
    def test_error_result_prefixes_summary(self, tmp_path: Path):
        """When ResultMessage.is_error=True, summary gets error prefix and loop breaks."""
        done = MagicMock(called=False)
        msg = _result_msg(is_error=True, result="rate limited")
        FakeClient = _make_client([msg])

        with _base_patches(FakeClient, done):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(spec=dict), max_exchanges=5,
            )

        assert "[Claude Code error]" in result.summary
        assert "rate limited" in result.summary

    def test_no_messages_ends_cycle(self, tmp_path: Path):
        """Empty receive_response iterator ends the cycle gracefully."""
        done = MagicMock(called=False)
        FakeClient = _make_client([])  # no messages

        with _base_patches(FakeClient, done):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(spec=dict), max_exchanges=5,
            )

        # Should not crash; result should exist
        assert result is not None
        assert result.finished is False


# ── Nudge loop ──────────────────────────────────────────────────────────


class TestNudgeLoop:
    def test_nudge_limit_breaks_loop(self, tmp_path: Path):
        """After _MAX_NUDGES nudges without done, the cycle ends."""
        done = MagicMock(called=False)  # never becomes True
        msg = _result_msg()
        FakeClient = _make_client([msg])
        query_count = 0

        OrigClient = FakeClient

        class CountingClient(OrigClient):
            async def query(self, prompt):
                nonlocal query_count
                query_count += 1
                await super().query(prompt)

        with _base_patches(CountingClient, done):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(spec=dict), max_exchanges=5,
            )

        # Initial query + up to _MAX_NUDGES (3) nudge queries = 4
        assert query_count <= 5
        assert result.finished is False


# ── Disconnect error handling ───────────────────────────────────────────


class TestDisconnect:
    def test_cancel_error_swallowed(self, tmp_path: Path):
        """RuntimeError with 'cancel' in message is silently caught."""
        done = MagicMock(called=True, success=True, summary="done")
        FakeClient = _make_client(
            [_result_msg()],
            disconnect_exc=RuntimeError("anyio cancel scope mismatch"),
        )

        with _base_patches(FakeClient, done):
            # Should not raise
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(spec=dict), max_exchanges=5,
            )
        assert result is not None

    def test_non_cancel_error_propagates(self, tmp_path: Path):
        """RuntimeError without 'cancel' in message is re-raised."""
        done = MagicMock(called=True, success=True, summary="done")
        FakeClient = _make_client(
            [_result_msg()],
            disconnect_exc=RuntimeError("connection refused"),
        )

        with _base_patches(FakeClient, done):
            with pytest.raises(RuntimeError, match="connection refused"):
                _make_orch().cycle(
                    goal="test",
                    project_dir=tmp_path,
                    team=MagicMock(spec=dict),
                    max_exchanges=5,
                )
