"""Adversarial tests for ClaudeSession — based on expected interface behavior."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from kodo import log
from kodo.log import RunDir
from kodo.sessions.claude import ClaudeSession
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


def _fake_modules(responses=None, client_factory=None):
    """Build fake claude_agent_sdk modules. Returns (client_or_factory, modules_dict)."""
    if client_factory is None:
        mock_client = MockClaudeSDKClient(responses=responses)

        def client_factory_fn(options=None):
            return mock_client
    else:
        mock_client = None
        client_factory_fn = client_factory

    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = MockClaudeAgentOptions
    fake_mod.ClaudeSDKClient = client_factory_fn
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


def _run_session(
    tmp_path, run_id, responses=None, client_factory=None, **session_kwargs
):
    """Helper: create session, run one query, clean up. Returns (session, result)."""
    log.init(RunDir.create(tmp_path, run_id))
    session_kwargs.setdefault("use_api_key", True)
    _, modules = _fake_modules(responses=responses, client_factory=client_factory)

    with patch.dict(sys.modules, modules):
        session = ClaudeSession(**session_kwargs)
        try:
            result = session.query("test prompt", tmp_path, max_turns=10)
        finally:
            session.close()

    return session, result


def test_none_cost_treated_as_zero(tmp_path: Path):
    """If total_cost_usd is None, stats should accumulate 0, not crash."""
    resp = MockResultMessage(
        result="ok", total_cost_usd=None, usage={"input_tokens": 10, "output_tokens": 5}
    )
    session, result = _run_session(tmp_path, "none_cost", responses=[resp])

    assert session.stats.total_cost_usd == 0.0
    assert result.cost_usd is None


def test_none_usage_tokens_treated_as_zero(tmp_path: Path):
    """If usage is None, token stats should stay at 0."""
    resp = MockResultMessage(result="ok", total_cost_usd=0.0, usage=None)
    session, result = _run_session(tmp_path, "none_usage", responses=[resp])

    assert session.stats.total_input_tokens == 0
    assert session.stats.total_output_tokens == 0
    assert result.input_tokens is None
    assert result.output_tokens is None


def test_empty_result_string(tmp_path: Path):
    """ResultMessage with empty result string should not crash."""
    resp = MockResultMessage(result="", is_error=False)
    session, result = _run_session(tmp_path, "empty_result", responses=[resp])
    assert result.text == ""
    assert result.is_error is False


def test_error_result_propagated(tmp_path: Path):
    """If is_error=True, the QueryResult should reflect that."""
    resp = MockResultMessage(result="something went wrong", is_error=True)
    session, result = _run_session(tmp_path, "error_result", responses=[resp])
    assert result.is_error is True
    assert "something went wrong" in result.text


def test_no_messages_from_receive_response(tmp_path: Path):
    """If receive_response yields nothing, result should be the default empty QueryResult."""
    session, result = _run_session(tmp_path, "no_messages", responses=[])
    assert result.text == ""


def test_same_project_dir_reuses_client(tmp_path: Path):
    """Querying the same project_dir twice should not create a second client."""
    log.init(RunDir.create(tmp_path, "reuse_client"))
    client_count = [0]

    def counting_factory(options=None):
        client_count[0] += 1
        return MockClaudeSDKClient(options=options)

    _, modules = _fake_modules(client_factory=counting_factory)

    with patch.dict(sys.modules, modules):
        session = ClaudeSession(use_api_key=True)
        try:
            session.query("q1", tmp_path, max_turns=10)
            session.query("q2", tmp_path, max_turns=10)
        finally:
            session.close()

    assert client_count[0] == 1  # Only one client created


def test_different_project_dir_creates_new_client(tmp_path: Path):
    """Querying a different project_dir should create a new client."""
    log.init(RunDir.create(tmp_path, "diff_dir"))
    client_count = [0]
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    def counting_factory(options=None):
        client_count[0] += 1
        return MockClaudeSDKClient(options=options)

    _, modules = _fake_modules(client_factory=counting_factory)

    with patch.dict(sys.modules, modules):
        session = ClaudeSession(use_api_key=True)
        try:
            session.query("q1", dir_a, max_turns=10)
            session.query("q2", dir_b, max_turns=10)
        finally:
            session.close()

    assert client_count[0] == 2


def test_plan_mode_review_does_not_crash(tmp_path: Path):
    """When a plan is pending and the next prompt is non-approval, session doesn't crash."""
    log.init(RunDir.create(tmp_path, "plan_mode"))
    resp = MockResultMessage(result="waiting for review", is_error=False)
    _, modules = _fake_modules(responses=[resp])

    with patch.dict(sys.modules, modules):
        session = ClaudeSession(use_api_key=True)
        try:
            # Simulate what happens when ExitPlanMode is denied:
            # the session captures the plan via _can_use_tool
            session._pending_plan = "Step 1: do X\nStep 2: do Y"

            # Non-approval prompt — session should handle gracefully
            result = session.query("review my plan", tmp_path, max_turns=10)
            assert not result.is_error
        finally:
            session.close()


def test_plan_approval_requires_signal(tmp_path: Path):
    """Plan is only approved when the prompt contains an approval signal."""
    log.init(RunDir.create(tmp_path, "plan_approval"))
    resp = MockResultMessage(result="ok", is_error=False)
    _, modules = _fake_modules(responses=[resp, resp, resp])

    with patch.dict(sys.modules, modules):
        session = ClaudeSession(use_api_key=True)
        try:
            # Simulate captured plan (as _can_use_tool would set it)
            session._pending_plan = "Option A vs Option B"

            # Non-approval prompt — plan_approved should stay False
            session.query("I don't like these, try again", tmp_path, max_turns=5)
            assert session._plan_approved is False

            # Simulate another captured plan after revision
            session._pending_plan = "Option C vs Option D"

            # Approval signal in prompt
            session.query("Proceed with option C", tmp_path, max_turns=5)
            assert session._plan_approved is True

            # Another plan cycle with different approval wording
            session._plan_approved = False
            session._pending_plan = "Option E"
            session.query("I choose option E, let's go", tmp_path, max_turns=5)
            assert session._plan_approved is True
        finally:
            session.close()


def test_query_after_close_raises(tmp_path: Path):
    """After close(), attempting to query should fail (loop is stopped)."""
    log.init(RunDir.create(tmp_path, "after_close"))
    _, modules = _fake_modules()

    with patch.dict(sys.modules, modules):
        session = ClaudeSession(use_api_key=True)
        session.close()

        with pytest.raises(Exception):
            session.query("q", tmp_path, max_turns=10)
