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
