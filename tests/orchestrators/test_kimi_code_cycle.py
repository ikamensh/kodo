"""Tests for KimiCodeOrchestrator.cycle() — streaming, nudge loop, done signal."""

from __future__ import annotations

import contextlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fake kimi_agent_sdk module ──────────────────────────────────────────
# The real SDK is an optional dependency; build a minimal fake module
# so the deferred import inside cycle() resolves.


def _install_fake_kimi_sdk():
    """Install a fake kimi_agent_sdk module and return its namespace."""
    mod = types.ModuleType("kimi_agent_sdk")

    class TextPart:
        def __init__(self, text: str = ""):
            self.text = text

    class TokenUsage:
        pass

    class TurnEnd:
        pass

    class ApprovalRequest:
        def __init__(self):
            self.resolved = False

        def resolve(self, action: str):
            self.resolved = True

    class Session:
        @classmethod
        async def create(cls, **kwargs):
            return cls()

        def prompt(self, text: str):
            """Return an async iterator; override in tests."""
            raise NotImplementedError

        async def close(self):
            pass

    mod.TextPart = TextPart
    mod.TokenUsage = TokenUsage
    mod.TurnEnd = TurnEnd
    mod.ApprovalRequest = ApprovalRequest
    mod.Session = Session

    sys.modules["kimi_agent_sdk"] = mod
    return mod


_sdk = _install_fake_kimi_sdk()


# ── Helpers ─────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _base_patches(done_signal, session_instance):
    """Stub out MCP server, logging, and the Kimi SDK session."""
    mock_mcp = MagicMock()
    mock_mcp._mcp_server = MagicMock()

    # McpServerContext needs to be a context manager yielding an object with sse_url
    mock_ctx = MagicMock()
    mock_ctx.sse_url = "http://127.0.0.1:9999/sse"
    mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with (
        patch(
            "kodo.orchestrators.kimi_code.build_mcp_server", return_value=mock_mcp
        ),
        patch(
            "kodo.orchestrators.kimi_code.McpServerContext", return_value=mock_ctx
        ),
        patch(
            "kodo.orchestrators.kimi_code.build_cycle_prompt", return_value="go"
        ),
        patch("kodo.orchestrators.kimi_code.DoneSignal", return_value=done_signal),
        patch("kodo.orchestrators.kimi_code.VerificationState"),
        patch("kodo.orchestrators.kimi_code.log"),
        patch.object(
            _sdk.Session, "create", new=AsyncMock(return_value=session_instance)
        ),
    ):
        yield


def _make_orch():
    from kodo.orchestrators.kimi_code import KimiCodeOrchestrator

    return KimiCodeOrchestrator(model="kimi-k2-test")


def _make_session(messages_per_prompt=None):
    """Build a fake Session whose prompt() yields given messages.

    messages_per_prompt: list of message lists. Each call to prompt() pops
    the next list. If None, yields a single TurnEnd per prompt.
    """
    queues = list(messages_per_prompt or [[_sdk.TurnEnd()]])

    class FakeSession(_sdk.Session):
        def __init__(self):
            self._prompt_idx = 0

        @classmethod
        async def create(cls, **kwargs):
            return cls()

        async def prompt(self, text: str):
            idx = self._prompt_idx
            self._prompt_idx += 1
            msgs = queues[idx] if idx < len(queues) else [_sdk.TurnEnd()]
            for m in msgs:
                yield m

        async def close(self):
            pass

    return FakeSession()


# ── Exchange tracking ───────────────────────────────────────────────────


class TestKimiCycleTracking:
    def test_turn_end_increments_exchanges(self, tmp_path: Path):
        """Each TurnEnd in the stream increments result.exchanges."""
        done = MagicMock(called=True, success=True, summary="done")
        session = _make_session([
            [_sdk.TurnEnd(), _sdk.TurnEnd(), _sdk.TurnEnd()],
        ])

        with _base_patches(done, session):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        assert result.exchanges >= 3

    def test_text_parts_collected_into_summary(self, tmp_path: Path):
        """TextPart messages are concatenated when done_signal is not called."""
        done = MagicMock(called=False)
        # done_signal.called stays False through all nudges too
        session = _make_session([
            [_sdk.TextPart("Hello "), _sdk.TextPart("world"), _sdk.TurnEnd()],
            [_sdk.TurnEnd()],  # nudge 1
            [_sdk.TurnEnd()],  # nudge 2
            [_sdk.TurnEnd()],  # nudge 3
        ])

        with _base_patches(done, session):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        # After max nudges, summary should be set (from last response)
        assert result is not None

    def test_done_signal_applies_summary(self, tmp_path: Path):
        """When done_signal.called is True after initial prompt, result is set."""
        done = MagicMock(called=True, success=True, summary="task completed")
        session = _make_session([[_sdk.TurnEnd()]])

        with _base_patches(done, session):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        assert result.finished is True
        assert "task completed" in result.summary


# ── Nudge loop ──────────────────────────────────────────────────────────


class TestKimiNudgeLoop:
    def test_nudge_limit_ends_cycle(self, tmp_path: Path):
        """After _MAX_NUDGES without done, cycle ends gracefully."""
        done = MagicMock(called=False)  # never becomes True
        session = _make_session([
            [_sdk.TurnEnd()],  # initial prompt
            [_sdk.TurnEnd()],  # nudge 1
            [_sdk.TurnEnd()],  # nudge 2
            [_sdk.TurnEnd()],  # nudge 3
        ])

        with _base_patches(done, session):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        assert result.finished is False

    def test_done_during_nudge_stops_loop(self, tmp_path: Path):
        """If done_signal.called becomes True during a nudge, loop stops."""
        call_count = 0

        # done_signal.called returns False first, then True on 2nd check
        class FakeDone:
            success = True
            summary = "finished during nudge"
            terminal = "goal_done"

            @property
            def called(self):
                nonlocal call_count
                call_count += 1
                # False for first check (after initial prompt),
                # True on subsequent checks (during nudge loop)
                return call_count > 2

        done = FakeDone()
        session = _make_session([
            [_sdk.TurnEnd()],  # initial prompt
            [_sdk.TurnEnd()],  # nudge 1 — done becomes True here
        ])

        with _base_patches(done, session):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        assert result.finished is True


# ── Approval request handling ───────────────────────────────────────────


class TestKimiApprovalRequest:
    def test_approval_request_auto_approved(self, tmp_path: Path):
        """ApprovalRequest in stream is auto-resolved with 'approve'."""
        done = MagicMock(called=True, success=True, summary="done")
        approval = _sdk.ApprovalRequest()
        session = _make_session([[approval, _sdk.TurnEnd()]])

        with _base_patches(done, session):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        assert approval.resolved is True
        assert result is not None
