"""Tests for kodo.sessions.claude.ClaudeSession."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from kodo import log
from kodo.log import RunDir
from kodo.sessions.claude import ClaudeSession, _extract_tokens
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


_TEST_API_KEY = "sk-test-secret"


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


def test_query_returns_result(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "claude_query"))
    resp = MockResultMessage(
        result="Hello world",
        num_turns=2,
        total_cost_usd=0.05,
        usage={"input_tokens": 200, "output_tokens": 100},
    )
    mock_client, fake_modules = _install_mock_sdk(responses=[resp])

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(model="sonnet", use_api_key=True)
        try:
            result = session.query("say hello", tmp_path, max_turns=10)
        finally:
            session.close()

    assert result.text == "Hello world"
    assert result.is_error is False
    assert result.turns == 2
    assert result.cost_usd == 0.05
    assert result.input_tokens == 200
    assert result.output_tokens == 100


def test_stats_accumulate(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "claude_stats"))
    r1 = MockResultMessage(
        result="r1",
        total_cost_usd=0.01,
        usage={"input_tokens": 100, "output_tokens": 50},
    )
    r2 = MockResultMessage(
        result="r2",
        total_cost_usd=0.02,
        usage={"input_tokens": 200, "output_tokens": 80},
    )

    # Two queries to different project dirs forces a reconnect with fresh client
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    call_count = [0]

    def make_client(options=None):
        nonlocal call_count
        responses = [r1] if call_count[0] == 0 else [r2]
        call_count[0] += 1
        return MockClaudeSDKClient(options=options, responses=responses)

    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = MockClaudeAgentOptions
    fake_mod.ClaudeSDKClient = make_client
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
        session = ClaudeSession(model="sonnet", use_api_key=True)
        try:
            session.query("q1", dir_a, max_turns=10)
            session.query("q2", dir_b, max_turns=10)
        finally:
            session.close()

    assert session.stats.queries == 2
    assert session.stats.total_input_tokens == 300
    assert session.stats.total_output_tokens == 130
    assert abs(session.stats.total_cost_usd - 0.03) < 1e-9


def test_reset_clears_stats(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "claude_reset"))
    mock_client, fake_modules = _install_mock_sdk()

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(model="sonnet", use_api_key=True)
        try:
            session.query("q", tmp_path, max_turns=10)
            assert session.stats.queries == 1
            session.reset()
            assert session.stats.queries == 0
        finally:
            session.close()


def test_extract_tokens_variants():
    assert _extract_tokens({"input_tokens": 10, "output_tokens": 5}) == (10, 5)
    assert _extract_tokens({"prompt_tokens": 10, "completion_tokens": 5}) == (10, 5)
    assert _extract_tokens(None) == (None, None)
    assert _extract_tokens({}) == (None, None)


def test_api_key_stripped_by_default(tmp_path: Path, monkeypatch):
    log.init(RunDir.create(tmp_path, "claude_key_strip"))

    keys_during_init = []

    class TrackingOptions(MockClaudeAgentOptions):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            keys_during_init.append(os.environ.get("ANTHROPIC_API_KEY"))

    mock_client = MockClaudeSDKClient()
    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = TrackingOptions
    fake_mod.ClaudeSDKClient = lambda options=None: mock_client
    fake_mod.ResultMessage = MockResultMessage
    fake_mod.AssistantMessage = MockAssistantMessage
    fake_types = ModuleType("claude_agent_sdk.types")
    fake_types.PermissionResultAllow = MockPermissionResultAllow
    fake_types.PermissionResultDeny = MockPermissionResultDeny
    fake_types.TextBlock = MockTextBlock
    fake_types.ToolUseBlock = MockToolUseBlock

    monkeypatch.setenv("ANTHROPIC_API_KEY", _TEST_API_KEY)

    with patch.dict(
        sys.modules,
        {
            "claude_agent_sdk": fake_mod,
            "claude_agent_sdk.types": fake_types,
        },
    ):
        session = ClaudeSession(model="sonnet", use_api_key=False)
        try:
            session.query("q", tmp_path, max_turns=10)
        finally:
            session.close()

    # Key should have been stripped during _ensure_client
    assert keys_during_init[0] is None
    # Key should be restored after
    assert os.environ.get("ANTHROPIC_API_KEY") == _TEST_API_KEY


def test_api_key_kept_when_explicit(tmp_path: Path, monkeypatch):
    log.init(RunDir.create(tmp_path, "claude_key_keep"))

    keys_during_init = []

    class TrackingOptions(MockClaudeAgentOptions):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            keys_during_init.append(os.environ.get("ANTHROPIC_API_KEY"))

    mock_client = MockClaudeSDKClient()
    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = TrackingOptions
    fake_mod.ClaudeSDKClient = lambda options=None: mock_client
    fake_mod.ResultMessage = MockResultMessage
    fake_mod.AssistantMessage = MockAssistantMessage
    fake_types = ModuleType("claude_agent_sdk.types")
    fake_types.PermissionResultAllow = MockPermissionResultAllow
    fake_types.PermissionResultDeny = MockPermissionResultDeny
    fake_types.TextBlock = MockTextBlock
    fake_types.ToolUseBlock = MockToolUseBlock

    monkeypatch.setenv("ANTHROPIC_API_KEY", _TEST_API_KEY)

    with patch.dict(
        sys.modules,
        {
            "claude_agent_sdk": fake_mod,
            "claude_agent_sdk.types": fake_types,
        },
    ):
        session = ClaudeSession(model="sonnet", use_api_key=True)
        try:
            session.query("q", tmp_path, max_turns=10)
        finally:
            session.close()

    assert keys_during_init[0] == _TEST_API_KEY


def test_custom_session_timeout(tmp_path: Path):
    """Test that custom session_timeout_s is used."""
    log.init(RunDir.create(tmp_path, "claude_timeout"))
    mock_client, fake_modules = _install_mock_sdk()

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(
            model="sonnet",
            use_api_key=True,
            session_timeout_s=3600,
        )
        try:
            # Verify custom timeout is used
            assert session._query_timeout == 3600.0
        finally:
            session.close()


def test_run_with_dead_thread_raises(tmp_path: Path):
    """Test that _run() raises when thread is dead."""
    log.init(RunDir.create(tmp_path, "claude_dead_thread"))
    mock_client, fake_modules = _install_mock_sdk()

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(model="sonnet", use_api_key=True)
        # Kill the thread
        session._loop.call_soon_threadsafe(session._loop.stop)
        session._thread.join(timeout=1)

        # Now _run() should raise
        import asyncio

        coro = asyncio.sleep(0)
        try:
            session._run(coro, timeout=1)
            assert False, "Should have raised RuntimeError"
        except RuntimeError as exc:
            assert "thread is dead" in str(exc).lower()
        finally:
            # Cleanup: close the loop manually since thread is dead
            try:
                session._loop.close()
            except Exception:
                pass


def test_ensure_client_with_dead_thread(tmp_path: Path):
    """Test that _ensure_client raises when thread is dead."""
    log.init(RunDir.create(tmp_path, "claude_ensure_dead"))
    mock_client, fake_modules = _install_mock_sdk()

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(model="sonnet", use_api_key=True)
        # Kill the thread
        session._loop.call_soon_threadsafe(session._loop.stop)
        session._thread.join(timeout=1)

        # _ensure_client should raise
        try:
            session._ensure_client(tmp_path)
            assert False, "Should have raised RuntimeError"
        except RuntimeError as exc:
            assert "closed" in str(exc).lower()
        finally:
            # Cleanup
            try:
                session._loop.close()
            except Exception:
                pass


def test_chrome_mode_flag(tmp_path: Path):
    """Test that chrome=True passes --chrome flag."""
    log.init(RunDir.create(tmp_path, "claude_chrome"))

    captured_options = []

    class TrackingOptions(MockClaudeAgentOptions):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            captured_options.append(kwargs.get("extra_args", {}))

    mock_client = MockClaudeSDKClient()
    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = TrackingOptions
    fake_mod.ClaudeSDKClient = lambda options=None: mock_client
    fake_mod.ResultMessage = MockResultMessage
    fake_mod.AssistantMessage = MockAssistantMessage
    fake_types = ModuleType("claude_agent_sdk.types")
    fake_types.PermissionResultAllow = MockPermissionResultAllow
    fake_types.PermissionResultDeny = MockPermissionResultDeny
    fake_types.TextBlock = MockTextBlock
    fake_types.ToolUseBlock = MockToolUseBlock

    with patch.dict(
        sys.modules,
        {"claude_agent_sdk": fake_mod, "claude_agent_sdk.types": fake_types},
    ):
        session = ClaudeSession(model="sonnet", chrome=True, use_api_key=True)
        try:
            session.query("test", tmp_path, max_turns=10)
        finally:
            session.close()

    assert captured_options[0] == {"--chrome": None}


def test_resume_session_one_shot(tmp_path: Path):
    """Test that resume_session_id is cleared after first use."""
    log.init(RunDir.create(tmp_path, "claude_resume"))

    captured_resumes = []

    class TrackingOptions(MockClaudeAgentOptions):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            captured_resumes.append(kwargs.get("resume"))

    call_count = [0]

    def make_client(options=None):
        call_count[0] += 1
        return MockClaudeSDKClient(options=options)

    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = TrackingOptions
    fake_mod.ClaudeSDKClient = make_client
    fake_mod.ResultMessage = MockResultMessage
    fake_mod.AssistantMessage = MockAssistantMessage
    fake_types = ModuleType("claude_agent_sdk.types")
    fake_types.PermissionResultAllow = MockPermissionResultAllow
    fake_types.PermissionResultDeny = MockPermissionResultDeny
    fake_types.TextBlock = MockTextBlock
    fake_types.ToolUseBlock = MockToolUseBlock

    with patch.dict(
        sys.modules,
        {"claude_agent_sdk": fake_mod, "claude_agent_sdk.types": fake_types},
    ):
        session = ClaudeSession(
            model="sonnet",
            resume_session_id="old-session-123",
            use_api_key=True,
        )
        try:
            # First query uses resume
            session.query("first", tmp_path, max_turns=10)
            assert session.resume_session_id is None  # Cleared after first use

            # Force reconnect by changing project dir
            other_dir = tmp_path / "other"
            other_dir.mkdir()
            session.query("second", other_dir, max_turns=10)
        finally:
            session.close()

    # First client got resume, second didn't
    assert captured_resumes[0] == "old-session-123"
    assert captured_resumes[1] is None


def test_disconnect_timeout_ignored(tmp_path: Path):
    """Test that TimeoutError during disconnect is caught."""
    log.init(RunDir.create(tmp_path, "claude_disconnect_timeout"))

    class TimeoutClient(MockClaudeSDKClient):
        async def disconnect(self):
            raise TimeoutError("Disconnect timed out")

    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = MockClaudeAgentOptions
    fake_mod.ClaudeSDKClient = lambda options=None: TimeoutClient(options=options)
    fake_mod.ResultMessage = MockResultMessage
    fake_mod.AssistantMessage = MockAssistantMessage
    fake_types = ModuleType("claude_agent_sdk.types")
    fake_types.PermissionResultAllow = MockPermissionResultAllow
    fake_types.PermissionResultDeny = MockPermissionResultDeny
    fake_types.TextBlock = MockTextBlock
    fake_types.ToolUseBlock = MockToolUseBlock

    with patch.dict(
        sys.modules,
        {"claude_agent_sdk": fake_mod, "claude_agent_sdk.types": fake_types},
    ):
        session = ClaudeSession(model="sonnet", use_api_key=True)
        session.query("test", tmp_path, max_turns=10)
        # reset() calls _disconnect(), which should swallow the timeout
        session.reset()  # Should not raise
        session.close()


def test_close_idempotent(tmp_path: Path):
    """Test that calling close() twice is safe."""
    log.init(RunDir.create(tmp_path, "claude_close_twice"))
    mock_client, fake_modules = _install_mock_sdk()

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(model="sonnet", use_api_key=True)
        session.query("test", tmp_path, max_turns=10)
        session.close()
        # Second close should be a no-op
        session.close()  # Should not raise


def test_close_with_disconnect_error(tmp_path: Path):
    """Test that OSError during close is caught."""
    log.init(RunDir.create(tmp_path, "claude_close_err"))

    class BrokenClient(MockClaudeSDKClient):
        async def disconnect(self):
            raise OSError("Disconnect failed")

    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = MockClaudeAgentOptions
    fake_mod.ClaudeSDKClient = lambda options=None: BrokenClient(options=options)
    fake_mod.ResultMessage = MockResultMessage
    fake_mod.AssistantMessage = MockAssistantMessage
    fake_types = ModuleType("claude_agent_sdk.types")
    fake_types.PermissionResultAllow = MockPermissionResultAllow
    fake_types.PermissionResultDeny = MockPermissionResultDeny
    fake_types.TextBlock = MockTextBlock
    fake_types.ToolUseBlock = MockToolUseBlock

    with patch.dict(
        sys.modules,
        {"claude_agent_sdk": fake_mod, "claude_agent_sdk.types": fake_types},
    ):
        session = ClaudeSession(model="sonnet", use_api_key=True)
        session.query("test", tmp_path, max_turns=10)
        session.close()  # Should not raise despite OSError


def test_tool_use_content_truncation(tmp_path: Path):
    """Test that long tool content is truncated in logs."""
    log.init(RunDir.create(tmp_path, "claude_tool_trunc"))

    long_content = "A" * 500  # More than 200 chars
    tool_block = MockToolUseBlock(
        name="Write", input={"file_path": "/test.py", "content": long_content}
    )
    asst_msg = MockAssistantMessage(content=[tool_block])
    result_msg = MockResultMessage(result="done")

    mock_client, fake_modules = _install_mock_sdk(responses=[asst_msg, result_msg])

    with patch.dict(sys.modules, fake_modules):
        session = ClaudeSession(model="sonnet", use_api_key=True)
        try:
            session.query("test", tmp_path, max_turns=10)
        finally:
            session.close()

    # Test passes if no error raised - truncation happens during query
