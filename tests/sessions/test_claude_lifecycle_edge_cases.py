"""Edge-case lifecycle tests for ClaudeSession._ensure_client.

These tests verify that transient connect() failures don't leave a stale
self._client behind, which would cause _ensure_client to skip reconnection
on the next call (the "stale client" bug).
"""

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


def _fake_modules(client_factory):
    """Build fake claude_agent_sdk modules using a custom client factory."""
    fake_mod = ModuleType("claude_agent_sdk")
    fake_mod.ClaudeAgentOptions = MockClaudeAgentOptions
    fake_mod.ClaudeSDKClient = client_factory
    fake_mod.ResultMessage = MockResultMessage
    fake_mod.AssistantMessage = MockAssistantMessage

    fake_types = ModuleType("claude_agent_sdk.types")
    fake_types.PermissionResultAllow = MockPermissionResultAllow
    fake_types.PermissionResultDeny = MockPermissionResultDeny
    fake_types.TextBlock = MockTextBlock
    fake_types.ToolUseBlock = MockToolUseBlock

    return {
        "claude_agent_sdk": fake_mod,
        "claude_agent_sdk.types": fake_types,
    }


# ── Stale-client bug: connect() failure must clear self._client ──────────


class _FailOnceThenSucceedClient(MockClaudeSDKClient):
    """Client whose connect() fails on the first call and succeeds thereafter.

    Because MockClaudeSDKClient.connect() is synchronous (returns None),
    we raise synchronously here as well so _run() sees None on success.
    """

    _attempt = 0

    def connect(self):
        _FailOnceThenSucceedClient._attempt += 1
        if _FailOnceThenSucceedClient._attempt == 1:
            raise ConnectionError("Transient failure — subprocess crashed")
        # Success path: return None (sync mock, same as base class)
        self.connected = True
        return None


def test_ensure_client_clears_state_on_connect_failure(tmp_path: Path):
    """Reproduce the stale-client bug:

    1. Call _ensure_client → ClaudeSDKClient is constructed, connect() raises.
    2. After the failure, self._client and self._project_dir MUST be None
       so that a subsequent _ensure_client call re-creates the client instead
       of hitting the early-return guard.
    3. Call _ensure_client again → it should succeed (not short-circuit).
    """
    log.init(RunDir.create(tmp_path, "stale_client"))

    # Reset the class-level attempt counter
    _FailOnceThenSucceedClient._attempt = 0
    clients_created = []

    def factory(options=None):
        client = _FailOnceThenSucceedClient(options=options)
        clients_created.append(client)
        return client

    modules = _fake_modules(factory)

    with patch.dict(sys.modules, modules):
        session = ClaudeSession(use_api_key=True)
        try:
            # ── First call: connect() raises ──
            with pytest.raises(ConnectionError, match="Transient failure"):
                session._ensure_client(tmp_path)

            # After failure, both must be cleared (the bug left them set):
            assert session._client is None, (
                "_client should be None after connect() failure"
            )
            assert session._project_dir is None, (
                "_project_dir should be None after connect() failure"
            )

            # ── Second call: should construct a *new* client and succeed ──
            session._ensure_client(tmp_path)

            assert session._client is not None, (
                "_client should be set after successful reconnect"
            )
            assert session._client.connected, (
                "Client should be connected after successful reconnect"
            )
            assert session._project_dir == tmp_path

            # Two clients should have been created (not one reused)
            assert len(clients_created) == 2
        finally:
            session.close()


def test_query_recovers_after_connect_failure(tmp_path: Path):
    """End-to-end: query() should return an error on connect failure, then
    succeed on the next call once the transient problem is gone."""
    log.init(RunDir.create(tmp_path, "query_recover"))

    attempt = [0]

    class FailFirstConnectClient(MockClaudeSDKClient):
        def connect(self):
            attempt[0] += 1
            if attempt[0] == 1:
                raise ConnectionError("auth server down")
            self.connected = True
            return None

    resp = MockResultMessage(result="recovered!", is_error=False)

    def factory(options=None):
        return FailFirstConnectClient(options=options, responses=[resp])

    modules = _fake_modules(factory)

    with patch.dict(sys.modules, modules):
        session = ClaudeSession(use_api_key=True)
        try:
            # First query: connect fails → graceful error result
            r1 = session.query("hello", tmp_path, max_turns=5)
            assert r1.is_error is True
            assert "failed to connect" in r1.text.lower()

            # Second query: connect succeeds → normal result
            r2 = session.query("hello again", tmp_path, max_turns=5)
            assert r2.is_error is False
            assert r2.text == "recovered!"
        finally:
            session.close()


def test_ensure_client_clears_state_on_async_connect_failure(tmp_path: Path):
    """Same as the sync test but with an async connect() that raises,
    exercising the _run() → future.result() path."""
    log.init(RunDir.create(tmp_path, "stale_async"))

    attempt = [0]
    clients_created = []

    class AsyncFailOnceClient(MockClaudeSDKClient):
        async def connect(self):
            attempt[0] += 1
            if attempt[0] == 1:
                raise OSError("Subprocess failed to start")
            self.connected = True

    def factory(options=None):
        client = AsyncFailOnceClient(options=options)
        clients_created.append(client)
        return client

    modules = _fake_modules(factory)

    with patch.dict(sys.modules, modules):
        session = ClaudeSession(use_api_key=True)
        try:
            # First call: async connect() raises
            with pytest.raises(OSError, match="Subprocess failed"):
                session._ensure_client(tmp_path)

            assert session._client is None
            assert session._project_dir is None

            # Second call: should succeed
            session._ensure_client(tmp_path)

            assert session._client is not None
            assert session._client.connected
            assert len(clients_created) == 2
        finally:
            session.close()
