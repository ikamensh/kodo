"""Tests for kodo.sessions.kimi.KimiSession."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from kodo import log
from kodo.log import RunDir


# ---------------------------------------------------------------------------
# Mock kimi_agent_sdk module — injected before importing KimiSession
# ---------------------------------------------------------------------------


class _MockTextPart:
    def __init__(self, text: str = ""):
        self.text = text


class _MockTokenUsage:
    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _MockTurnEnd:
    pass


class _MockApprovalRequest:
    def resolve(self, action: str) -> None:
        pass


class _MockKimiSession:
    """Mimics kimi_agent_sdk.Session."""

    _responses: list = []
    _session_id: str = "kimi-test-123"

    def __init__(self, responses=None, session_id="kimi-test-123"):
        self._responses = responses or [_MockTextPart(text="done")]
        self.id = session_id
        self.prompts: list[str] = []
        self._closed = False

    @classmethod
    async def create(cls, work_dir=None, model=None, yolo=True, **kwargs):
        instance = cls()
        return instance

    @classmethod
    async def resume(cls, work_dir=None, session_id=None, **kwargs):
        instance = cls(session_id=session_id or "resumed-123")
        return instance

    async def prompt(self, text):
        self.prompts.append(text)
        for msg in self._responses:
            yield msg

    async def close(self):
        self._closed = True

    def cancel(self):
        pass


def _install_mock_sdk(responses=None, session_id="kimi-test-123"):
    """Install a fake kimi_agent_sdk module into sys.modules."""
    mod = types.ModuleType("kimi_agent_sdk")
    mod.TextPart = _MockTextPart
    mod.TokenUsage = _MockTokenUsage
    mod.TurnEnd = _MockTurnEnd
    mod.ApprovalRequest = _MockApprovalRequest

    # Create a Session class that returns instances with the given responses
    class ConfiguredSession(_MockKimiSession):
        _responses = responses or [_MockTextPart(text="done")]
        _session_id = session_id

        @classmethod
        async def create(cls, work_dir=None, model=None, yolo=True, **kwargs):
            return cls(responses=cls._responses, session_id=cls._session_id)

        @classmethod
        async def resume(cls, work_dir=None, session_id=None, **kwargs):
            return cls(
                responses=cls._responses,
                session_id=session_id or cls._session_id,
            )

    mod.Session = ConfiguredSession
    sys.modules["kimi_agent_sdk"] = mod
    return mod


@pytest.fixture(autouse=True)
def _mock_kimi_sdk():
    """Install mock SDK before each test, remove after."""
    _install_mock_sdk()
    yield
    sys.modules.pop("kimi_agent_sdk", None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_query_returns_result(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "kimi_test"))
    _install_mock_sdk(responses=[_MockTextPart(text="All done!")])

    from kodo.sessions.kimi import KimiSession

    session = KimiSession(model="kimi-k2.5-thinking")
    try:
        result = session.query("do stuff", tmp_path, max_turns=10)
        assert result.text == "All done!"
        assert result.is_error is False
        assert session.stats.queries == 1
    finally:
        session.close()


def test_stats_accumulate(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "kimi_stats"))
    _install_mock_sdk(
        responses=[
            _MockTextPart(text="ok"),
            _MockTokenUsage(prompt_tokens=100, completion_tokens=50),
            _MockTurnEnd(),
        ],
    )

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    try:
        result = session.query("task", tmp_path, max_turns=10)
        assert result.text == "ok"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert session.stats.total_input_tokens == 100
        assert session.stats.total_output_tokens == 50
        assert session.stats.queries == 1
    finally:
        session.close()


def test_reset_clears_stats(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "kimi_reset"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    try:
        session.query("task", tmp_path, max_turns=10)
        assert session.stats.queries == 1

        session.reset()
        assert session.stats.queries == 0
    finally:
        session.close()


def test_system_prompt_prepended_once(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "kimi_sysprompt"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession

    session = KimiSession(system_prompt="Be helpful.")
    try:
        session.query("task1", tmp_path, max_turns=10)
        session.query("task2", tmp_path, max_turns=10)

        # Access the mock session's recorded prompts
        sdk_session = session._session
        assert "Be helpful." in sdk_session.prompts[0]
        assert "Be helpful." not in sdk_session.prompts[1]
    finally:
        session.close()


def test_cost_bucket(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "kimi_bucket"))

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    assert session.cost_bucket == "kimi_api"
    session.close()


def test_clone_creates_fresh_session(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "kimi_clone"))

    from kodo.sessions.kimi import KimiSession

    original = KimiSession(model="kimi-k2", system_prompt="Be smart.")
    cloned = original.clone()
    try:
        assert cloned.model == "kimi-k2"
        assert cloned.system_prompt == "Be smart."
        assert cloned._session is None  # fresh, no connection
        assert cloned is not original
    finally:
        original.close()
        cloned.close()


def test_close_idempotent(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "kimi_close"))

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    session.close()
    session.close()  # should not raise


def test_session_id_from_sdk(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "kimi_sid"))
    _install_mock_sdk(session_id="my-session-42")

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    try:
        assert session.session_id is None  # before first query
        session.query("task", tmp_path, max_turns=10)
        assert session.session_id == "my-session-42"
    finally:
        session.close()


# ---------------------------------------------------------------------------
# New tests for improved coverage
# ---------------------------------------------------------------------------


def test_context_manager_with_statement(tmp_path: Path):
    """Test that KimiSession works with context manager."""
    log.init(RunDir.create(tmp_path, "kimi_ctx"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession

    with KimiSession() as session:
        assert session is not None
        result = session.query("test", tmp_path, max_turns=10)
        assert result.text == "done"
    # Session should be closed after exiting context


def test_context_manager_exception_handling(tmp_path: Path):
    """Test that context manager closes session even on exception."""
    log.init(RunDir.create(tmp_path, "kimi_ctx_err"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession

    session = None
    try:
        with KimiSession() as s:
            session = s
            raise ValueError("Test error")
    except ValueError:
        pass

    # Session should still be closed
    assert session._closed


def test_query_with_custom_timeout(tmp_path: Path):
    """Test that custom session_timeout_s is used."""
    log.init(RunDir.create(tmp_path, "kimi_timeout"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession

    session = KimiSession(session_timeout_s=30)
    try:
        assert session._query_timeout == 30.0
    finally:
        session.close()


def test_run_with_none_coro(tmp_path: Path):
    """Test that _run handles None coro gracefully."""
    log.init(RunDir.create(tmp_path, "kimi_none_coro"))

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    try:
        result = session._run(None)
        assert result is None  # should return immediately
    finally:
        session.close()


def test_session_resume_success(tmp_path: Path):
    """Test that resume_session_id successfully resumes session."""
    log.init(RunDir.create(tmp_path, "kimi_resume"))
    _install_mock_sdk(session_id="resumed-456")

    from kodo.sessions.kimi import KimiSession

    session = KimiSession(resume_session_id="old-session-123")
    try:
        result = session.query("test", tmp_path, max_turns=10)

        # resume_session_id should be cleared after first use (one-shot)
        assert session.resume_session_id is None
        assert result.is_error is False
    finally:
        session.close()


def test_terminate_with_session(tmp_path: Path):
    """Test that terminate calls cancel on active session."""
    log.init(RunDir.create(tmp_path, "kimi_terminate"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession
    from unittest.mock import MagicMock

    session = KimiSession()
    try:
        session.query("test", tmp_path, max_turns=10)

        # Mock cancel to track calls
        session._session.cancel = MagicMock()

        session.terminate()
        session._session.cancel.assert_called_once()
    finally:
        session.close()


def test_query_with_closed_loop_raises(tmp_path: Path):
    """Test that query raises RuntimeError if loop is closed."""
    log.init(RunDir.create(tmp_path, "kimi_closed_loop"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    session.close()  # Closes the loop

    with pytest.raises(RuntimeError, match="Session is closed"):
        session.query("test", tmp_path, max_turns=10)


def test_query_handles_ensure_session_error(tmp_path: Path):
    """Test that query handles errors during session creation."""
    log.init(RunDir.create(tmp_path, "kimi_ensure_err"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    try:
        # Mock _ensure_session to raise
        def mock_ensure(*args):
            raise ConnectionError("Network error")

        original_ensure = session._ensure_session
        session._ensure_session = mock_ensure

        result = session.query("test", tmp_path, max_turns=10)

        assert result.is_error
        assert "Kimi session failed to connect" in result.text
        assert "ConnectionError" in result.text
        assert session._session is None

        session._ensure_session = original_ensure
    finally:
        session.close()


def test_approval_request_handling(tmp_path: Path):
    """Test that ApprovalRequest messages are handled."""
    log.init(RunDir.create(tmp_path, "kimi_approval"))

    # Create responses with ApprovalRequest
    responses = [
        _MockTextPart(text="Starting task"),
        _MockApprovalRequest(),
        _MockTextPart(text="Task complete"),
    ]
    _install_mock_sdk(responses=responses)

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    try:
        result = session.query("task", tmp_path, max_turns=10)
        assert "Starting task" in result.text
        assert "Task complete" in result.text
    finally:
        session.close()


def test_run_with_dead_thread(tmp_path: Path):
    """Test that _run raises RuntimeError when thread is dead."""
    log.init(RunDir.create(tmp_path, "kimi_dead_thread"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession
    from unittest.mock import MagicMock
    import asyncio

    session = KimiSession()
    original_thread = session._thread
    try:
        # Mock thread to appear dead
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        session._thread = mock_thread

        # Use a real coroutine that can be checked
        coro = asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="thread is dead"):
            session._run(coro)
    finally:
        # Restore original thread and clean up
        session._thread = original_thread
        session.close()


def test_build_config_with_existing_file(tmp_path, monkeypatch):
    """Test _build_config returns None when config file exists with models."""
    log.init(RunDir.create(tmp_path, "kimi_config_exists"))

    from kodo.sessions.kimi import KimiSession

    # Create fake config file
    kimi_dir = tmp_path / ".kimi"
    kimi_dir.mkdir()
    config_file = kimi_dir / "config.toml"
    config_file.write_text("[models.kimi-k2.5]\nname = 'test'")

    # Mock Path.home() to return tmp_path
    import pathlib
    original_home = pathlib.Path.home
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)

    try:
        config = KimiSession._build_config()
        assert config is None  # Should use existing config
    finally:
        monkeypatch.setattr(pathlib.Path, "home", original_home)


def test_build_config_without_api_key(monkeypatch):
    """Test _build_config returns None when KIMI_API_KEY is missing."""
    from kodo.sessions.kimi import KimiSession
    import pathlib

    # Mock config file doesn't exist
    def mock_exists(self):
        return False

    original_exists = pathlib.Path.exists
    monkeypatch.setattr(pathlib.Path, "exists", mock_exists)
    monkeypatch.delenv("KIMI_API_KEY", raising=False)

    try:
        config = KimiSession._build_config()
        assert config is None
    finally:
        monkeypatch.setattr(pathlib.Path, "exists", original_exists)


def test_build_config_from_env_key(tmp_path, monkeypatch):
    """Test _build_config creates Config from KIMI_API_KEY."""
    from kodo.sessions.kimi import KimiSession
    import pathlib
    import sys

    # Mock config file doesn't exist
    def mock_exists(self):
        return False

    monkeypatch.setattr(pathlib.Path, "exists", mock_exists)
    monkeypatch.setenv("KIMI_API_KEY", "test-key-123")

    # Add Config class to mock SDK
    _install_mock_sdk()

    class MockConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    sys.modules["kimi_agent_sdk"].Config = MockConfig

    try:
        config = KimiSession._build_config()
        # Config should be created (not None)
        assert config is not None
        assert isinstance(config, MockConfig)
    finally:
        pass


def test_session_resume_fallback(tmp_path):
    """Test failed resume falls back to creating new session."""
    log.init(RunDir.create(tmp_path, "kimi_resume_fallback"))

    # Install mock SDK with resume returning None
    _install_mock_sdk()
    import sys

    # Override resume to return None
    async def mock_resume(*args, **kwargs):
        return None

    sys.modules["kimi_agent_sdk"].Session.resume = classmethod(mock_resume)

    from kodo.sessions.kimi import KimiSession

    session = KimiSession(resume_session_id="old-123")
    try:
        # Should log warning and fall back to create
        result = session.query("test", tmp_path, max_turns=10)
        assert not result.is_error
        # Session should be created (not None)
        assert session._session is not None
    finally:
        session.close()


def test_close_session_handles_runtime_error(tmp_path):
    """Test _close_session handles RuntimeError during close."""
    log.init(RunDir.create(tmp_path, "kimi_close_err"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    try:
        # Create a session first
        session.query("test", tmp_path, max_turns=10)

        # Mock _run to raise RuntimeError
        original_run = session._run

        def mock_run(*args, **kwargs):
            raise RuntimeError("Thread error")

        session._run = mock_run

        # Should not raise, just pass
        session._close_session()
        assert session._session is None

        session._run = original_run
    finally:
        session._run = original_run
        session.close()


def test_terminate_handles_exception(tmp_path):
    """Test that terminate handles exceptions in cancel."""
    log.init(RunDir.create(tmp_path, "kimi_terminate_err"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    try:
        session.query("test", tmp_path, max_turns=10)

        # Mock cancel to raise exception
        def mock_cancel():
            raise ValueError("Cancel failed")

        session._session.cancel = mock_cancel

        # Should not raise
        session.terminate()
    finally:
        session.close()


def test_query_exception_handling(tmp_path):
    """Test that query handles exceptions during execution."""
    log.init(RunDir.create(tmp_path, "kimi_query_exc"))
    _install_mock_sdk()

    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    try:
        # Create session first
        session.query("test", tmp_path, max_turns=10)

        # Mock _run to raise exception during query
        def mock_run_exc(*args, **kwargs):
            if args and hasattr(args[0], "__name__"):
                # This is _do_query
                raise ValueError("Query execution failed")
            return None

        original_run = session._run
        session._run = mock_run_exc

        result = session.query("test2", tmp_path, max_turns=10)

        assert result.is_error
        assert "Kimi session error during query" in result.text
        assert "ValueError" in result.text

        session._run = original_run
    finally:
        session._run = original_run
        session.close()
