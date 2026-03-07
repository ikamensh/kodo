"""Comprehensive coverage tests for kodo.sessions.claude.ClaudeSession."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch


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


def _install_mock_sdk(responses=None):
    """Install a fake claude_agent_sdk module and return the mock client that will be created."""
    mock_client = MockClaudeSDKClient(responses=responses)

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


# ── Tier 1: Basic Functionality ──────────────────────────────────────────


def test_context_manager_enter_exit(tmp_path: Path):
    """ClaudeSession should work as a context manager with __enter__ and __exit__."""
    log.init(RunDir.create(tmp_path, "ctx_mgr"))
    mock_client, fake_modules = _install_mock_sdk()

    with patch.dict(sys.modules, fake_modules):
        with ClaudeSession(model="sonnet", use_api_key=True) as session:
            assert isinstance(session, ClaudeSession)
            result = session.query("test", tmp_path, max_turns=5)
            assert result.text == ""

        # After __exit__, session should be closed
        assert session._closed


def test_cost_bucket_api_vs_subscription():
    """cost_bucket should return 'api' when use_api_key=True, 'claude_subscription' otherwise."""
    session_api = ClaudeSession(use_api_key=True)
    session_sub = ClaudeSession(use_api_key=False)

    try:
        assert session_api.cost_bucket == "api"
        assert session_sub.cost_bucket == "claude_subscription"
    finally:
        session_api.close()
        session_sub.close()


def test_session_id_property(tmp_path: Path):
    """session_id property should reflect _session_id from ResultMessage."""
    log.init(RunDir.create(tmp_path, "session_id"))

    # Mock ResultMessage with session_id attribute
    class ResultWithSessionId(MockResultMessage):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.session_id = "test-session-123"

    resp = ResultWithSessionId(result="ok")
    mock_client, fake_modules = _install_mock_sdk(responses=[resp])

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(use_api_key=True)
        try:
            assert session.session_id is None  # Before query
            session.query("test", tmp_path, max_turns=5)
            assert session.session_id == "test-session-123"  # After query
        finally:
            session.close()


# ── Tier 2: _can_use_tool ─────────────────────────────────────────────────


def test_can_use_tool_exit_plan_mode_denied_captures_plan(tmp_path: Path):
    """_can_use_tool should deny ExitPlanMode on first call and capture plan."""
    log.init(RunDir.create(tmp_path, "plan_denied"))

    session = ClaudeSession(use_api_key=True)
    try:
        # Simulate ExitPlanMode tool call
        import asyncio

        result = asyncio.run(
            session._can_use_tool(
                "ExitPlanMode",
                {"plan": "Step 1: Do X\nStep 2: Do Y"},
                context=None,
            )
        )

        # Should deny with interrupt (check class name since it's from real SDK)
        assert "PermissionResultDeny" in type(result).__name__
        assert result.interrupt is True
        assert "review" in result.message.lower()

        # Should capture the plan
        assert session._pending_plan == "Step 1: Do X\nStep 2: Do Y"
    finally:
        session.close()


def test_can_use_tool_exit_plan_mode_approved(tmp_path: Path):
    """_can_use_tool should allow ExitPlanMode when _plan_approved is True."""
    log.init(RunDir.create(tmp_path, "plan_approved"))

    session = ClaudeSession(use_api_key=True)
    try:
        # Set plan_approved flag
        session._plan_approved = True

        import asyncio

        result = asyncio.run(
            session._can_use_tool(
                "ExitPlanMode",
                {"plan": "Revised plan"},
                context=None,
            )
        )

        # Should allow (check class name since it's from real SDK)
        assert "PermissionResultAllow" in type(result).__name__

        # Should reset the flag
        assert session._plan_approved is False
    finally:
        session.close()


def test_can_use_tool_non_plan_tool_allowed(tmp_path: Path):
    """_can_use_tool should allow non-ExitPlanMode tools."""
    log.init(RunDir.create(tmp_path, "other_tool"))

    session = ClaudeSession(use_api_key=True)
    try:
        import asyncio

        result = asyncio.run(
            session._can_use_tool(
                "Read",
                {"file_path": "/some/file.txt"},
                context=None,
            )
        )

        # Should allow (check class name since it's from real SDK)
        assert "PermissionResultAllow" in type(result).__name__
    finally:
        session.close()


# ── Tier 3: query() Edge Cases ────────────────────────────────────────────


def test_query_connect_failure_returns_error_result(tmp_path: Path):
    """query should return error result when _ensure_client raises."""
    log.init(RunDir.create(tmp_path, "connect_fail"))

    # Make ClaudeSDKClient raise on instantiation
    class FailingClient:
        def __init__(self, options=None):
            raise RuntimeError("Authentication failed")

    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = MockClaudeAgentOptions
    fake_mod.ClaudeSDKClient = FailingClient
    fake_mod.ResultMessage = MockResultMessage
    fake_mod.AssistantMessage = MockAssistantMessage

    fake_types = ModuleType("claude_agent_sdk.types")
    fake_types.PermissionResultAllow = MockPermissionResultAllow
    fake_types.PermissionResultDeny = MockPermissionResultDeny
    fake_types.TextBlock = MockTextBlock
    fake_types.ToolUseBlock = MockToolUseBlock

    with patch.dict(
        sys.modules,
        {
            "claude_agent_sdk": fake_mod,
            "claude_agent_sdk.types": fake_types,
        },
    ):
        session = ClaudeSession(use_api_key=True)
        try:
            result = session.query("test", tmp_path, max_turns=5)

            # Should return error result
            assert result.is_error is True
            assert "failed to connect" in result.text.lower()
            assert "RuntimeError" in result.text
        finally:
            session.close()


def test_query_runtime_exception_during_collect(tmp_path: Path):
    """query should return error result when exception occurs during _collect."""
    log.init(RunDir.create(tmp_path, "collect_fail"))

    # Make receive_response raise (must be async generator)
    class FailingClient(MockClaudeSDKClient):
        async def receive_response(self):
            # Must yield to be an async generator
            if False:
                yield
            raise ValueError("Network error")

    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = MockClaudeAgentOptions
    fake_mod.ClaudeSDKClient = lambda options=None: FailingClient(options)
    fake_mod.ResultMessage = MockResultMessage
    fake_mod.AssistantMessage = MockAssistantMessage

    fake_types = ModuleType("claude_agent_sdk.types")
    fake_types.PermissionResultAllow = MockPermissionResultAllow
    fake_types.PermissionResultDeny = MockPermissionResultDeny
    fake_types.TextBlock = MockTextBlock
    fake_types.ToolUseBlock = MockToolUseBlock

    with patch.dict(
        sys.modules,
        {
            "claude_agent_sdk": fake_mod,
            "claude_agent_sdk.types": fake_types,
        },
    ):
        session = ClaudeSession(use_api_key=True)
        try:
            result = session.query("test", tmp_path, max_turns=5)

            # Should return error result
            assert result.is_error is True
            assert "error during query" in result.text.lower()
            assert "ValueError" in result.text
        finally:
            session.close()


def test_query_assistant_message_text_and_tool_blocks(tmp_path: Path):
    """query should collect both TextBlocks and ToolUseBlocks from AssistantMessage."""
    log.init(RunDir.create(tmp_path, "asst_blocks"))

    # Create AssistantMessage with text and tool use
    asst = MockAssistantMessage(
        content=[
            MockTextBlock(text="I'll read the file"),
            MockToolUseBlock(
                id="tool1",
                name="Read",
                input={"file_path": "/test.py"},
            ),
            MockTextBlock(text="Done reading"),
        ]
    )
    result_msg = MockResultMessage(result="Final result")

    mock_client, fake_modules = _install_mock_sdk(responses=[asst, result_msg])

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(use_api_key=True)
        try:
            result = session.query("read test.py", tmp_path, max_turns=5)

            # Should return the ResultMessage text
            assert result.text == "Final result"
        finally:
            session.close()


def test_query_fallback_to_assistant_texts_when_result_empty(tmp_path: Path):
    """query should use AssistantMessage texts when ResultMessage.result is empty."""
    log.init(RunDir.create(tmp_path, "fallback_text"))

    # ResultMessage with empty result
    asst1 = MockAssistantMessage(content=[MockTextBlock(text="First response")])
    asst2 = MockAssistantMessage(content=[MockTextBlock(text="Second response")])
    result_msg = MockResultMessage(result="", num_turns=2)  # Empty result

    mock_client, fake_modules = _install_mock_sdk(responses=[asst1, asst2, result_msg])

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(use_api_key=True)
        try:
            result = session.query("test", tmp_path, max_turns=5)

            # Should fallback to assistant texts joined with \n\n
            assert result.text == "First response\n\nSecond response"
            assert result.turns == 2
        finally:
            session.close()


def test_query_captures_session_id(tmp_path: Path):
    """query should capture session_id from ResultMessage when present."""
    log.init(RunDir.create(tmp_path, "capture_id"))

    # ResultMessage with session_id attribute
    class ResultWithId(MockResultMessage):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.session_id = "session-abc-123"

    resp = ResultWithId(result="ok")
    mock_client, fake_modules = _install_mock_sdk(responses=[resp])

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(use_api_key=True)
        try:
            session.query("test", tmp_path, max_turns=5)

            # Should have captured the session_id
            assert session._session_id == "session-abc-123"
        finally:
            session.close()


def test_query_pending_plan_prepended_to_result(tmp_path: Path):
    """query should prepend pending plan to result text when present."""
    log.init(RunDir.create(tmp_path, "pending_plan"))

    resp = MockResultMessage(result="Agent awaiting instructions")

    # Create a custom client that sets _pending_plan during query execution
    class PlanCapturingClient(MockClaudeSDKClient):
        def __init__(self, session, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.session = session

        async def receive_response(self):
            # Simulate plan being captured during query
            self.session._pending_plan = "1. Setup environment\n2. Run tests"
            for msg in self._responses:
                yield msg

    session = ClaudeSession(use_api_key=True)
    client_instance = PlanCapturingClient(session, responses=[resp])

    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = MockClaudeAgentOptions
    fake_mod.ClaudeSDKClient = lambda options=None: client_instance
    fake_mod.ResultMessage = MockResultMessage
    fake_mod.AssistantMessage = MockAssistantMessage

    fake_types = ModuleType("claude_agent_sdk.types")
    fake_types.PermissionResultAllow = MockPermissionResultAllow
    fake_types.PermissionResultDeny = MockPermissionResultDeny
    fake_types.TextBlock = MockTextBlock
    fake_types.ToolUseBlock = MockToolUseBlock

    with patch.dict(
        sys.modules,
        {
            "claude_agent_sdk": fake_mod,
            "claude_agent_sdk.types": fake_types,
        },
    ):
        try:
            result = session.query("test", tmp_path, max_turns=5)

            # Should prepend the plan
            assert "[PROPOSED PLAN]" in result.text
            assert "1. Setup environment" in result.text
            assert "2. Run tests" in result.text
            assert "[Agent is in plan mode, awaiting review]" in result.text
            assert "Agent awaiting instructions" in result.text
            assert result.is_error is False  # Not an error
        finally:
            session.close()


# ── Tier 4: clone() and terminate() ───────────────────────────────────────


def test_clone_returns_independent_session():
    """clone should return a new session with same config but independent state."""
    original = ClaudeSession(
        model="opus",
        system_prompt="You are a test assistant",
        chrome=True,
        fallback_model="sonnet",
        use_api_key=True,
        session_timeout_s=300,
        effort="high",
    )

    try:
        # Set some state on original
        original._session_id = "original-session-id"
        original._stats.queries = 5

        # Clone it
        cloned = original.clone()

        try:
            # Should have same config
            assert cloned.model == "opus"
            assert cloned.system_prompt == "You are a test assistant"
            assert cloned.chrome is True
            assert cloned.fallback_model == "sonnet"
            assert cloned.use_api_key is True
            assert cloned._session_timeout_s == 300
            assert cloned.effort == "high"

            # Should have independent state
            assert cloned._session_id is None
            assert cloned._stats.queries == 0

            # Should be a different object
            assert cloned is not original
        finally:
            cloned.close()
    finally:
        original.close()


def test_clone_does_not_copy_resume_session_id():
    """clone() should not copy resume_session_id (runtime state, not config)."""
    # resume_session_id is runtime state that's consumed on first use.
    # Clones should start fresh, not inherit resume capability.
    original = ClaudeSession(
        model="sonnet",
        resume_session_id="session-to-resume",
        use_api_key=True,
    )

    try:
        # Clone before resume is consumed
        cloned = original.clone()

        try:
            # Config is copied
            assert cloned.model == "sonnet"
            assert cloned.use_api_key is True

            # But resume_session_id is NOT copied (runtime state)
            assert cloned.resume_session_id is None

            # Original still has it (until first connection)
            assert original.resume_session_id == "session-to-resume"
        finally:
            cloned.close()
    finally:
        original.close()


def test_terminate_delegates_to_disconnect(tmp_path: Path):
    """terminate should call _disconnect to stop running query."""
    log.init(RunDir.create(tmp_path, "terminate"))

    mock_client, fake_modules = _install_mock_sdk()

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(use_api_key=True)
        try:
            # Connect the session
            session.query("test", tmp_path, max_turns=5)

            # Client should be connected
            assert session._client is not None
            assert session._client.connected

            # Keep reference to client before terminate clears it
            client_ref = session._client

            # Call terminate
            session.terminate()

            # Should have called disconnect on the client
            assert client_ref.disconnected

            # Client should be cleared
            assert session._client is None
        finally:
            session.close()
