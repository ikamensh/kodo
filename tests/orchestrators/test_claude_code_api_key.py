"""Regression test: ClaudeCodeOrchestrator must NOT leak ANTHROPIC_API_KEY.

The Claude Code SDK subprocess inherits os.environ.  If ANTHROPIC_API_KEY is
present, the CLI uses API billing instead of the subscription — silently
charging users real money while the stats table says "subscription".

Agent sessions (ClaudeSession) already strip the key.  The orchestrator must
do the same.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_fake_client_class(captured_env: dict):
    """Return a FakeClient class that snapshots os.environ at __init__ time."""
    from claude_agent_sdk import ResultMessage

    class FakeClient:
        def __init__(self, options=None):
            captured_env.update(os.environ)

        async def connect(self):
            pass

        async def query(self, prompt):
            pass

        async def receive_response(self):
            msg = ResultMessage(
                subtype="result",
                duration_ms=100,
                duration_api_ms=50,
                is_error=False,
                num_turns=1,
                session_id="fake-session",
                total_cost_usd=0.01,
                result="done",
            )
            yield msg

        async def disconnect(self):
            pass

    return FakeClient


class TestOrchestratorApiKeyStripped:
    """ANTHROPIC_API_KEY must not be in os.environ when ClaudeSDKClient is created."""

    def test_api_key_not_in_env_during_client_creation(self, tmp_path: Path):
        """The orchestrator must strip ANTHROPIC_API_KEY before creating the
        SDK client so the subprocess uses subscription billing."""
        from kodo.orchestrators.claude_code import ClaudeCodeOrchestrator

        captured_env: dict[str, str] = {}
        FakeClient = _make_fake_client_class(captured_env)

        fake_done = MagicMock()
        fake_done.called = True
        fake_done.success = True
        fake_done.summary = "test done"

        fake_team = MagicMock(spec=dict)

        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key-12345"}),
            patch("claude_agent_sdk.ClaudeSDKClient", FakeClient),
            patch(
                "kodo.orchestrators.claude_code.build_mcp_server", autospec=True
            ) as mock_mcp,
            patch(
                "kodo.orchestrators.claude_code.build_cycle_prompt",
                autospec=True,
                return_value="go",
            ),
            patch(
                "kodo.orchestrators.claude_code.DoneSignal",
                autospec=True,
                return_value=fake_done,
            ),
            patch("kodo.orchestrators.claude_code.VerificationState", autospec=True),
            patch("kodo.orchestrators.claude_code.log", autospec=True),
        ):
            mock_mcp.return_value = MagicMock(_mcp_server=MagicMock())

            orch = ClaudeCodeOrchestrator(model="sonnet")
            orch.cycle(
                goal="test",
                project_dir=tmp_path,
                team=fake_team,
                max_exchanges=5,
            )

        assert "ANTHROPIC_API_KEY" not in captured_env, (
            "ANTHROPIC_API_KEY was present in os.environ when ClaudeSDKClient "
            "was created — the subprocess will use API billing instead of "
            "subscription. This is a billing bug."
        )

    def test_api_key_restored_after_cycle(self, tmp_path: Path):
        """After the cycle completes, ANTHROPIC_API_KEY must be restored
        so the orchestrator's own API calls (e.g. summarizer) still work."""
        from kodo.orchestrators.claude_code import ClaudeCodeOrchestrator

        captured_env: dict[str, str] = {}
        FakeClient = _make_fake_client_class(captured_env)

        fake_done = MagicMock()
        fake_done.called = True
        fake_done.success = True
        fake_done.summary = "test done"

        fake_team = MagicMock(spec=dict)
        original_key = "sk-test-key-12345"

        with (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": original_key}),
            patch("claude_agent_sdk.ClaudeSDKClient", FakeClient),
            patch(
                "kodo.orchestrators.claude_code.build_mcp_server", autospec=True
            ) as mock_mcp,
            patch(
                "kodo.orchestrators.claude_code.build_cycle_prompt",
                autospec=True,
                return_value="go",
            ),
            patch(
                "kodo.orchestrators.claude_code.DoneSignal",
                autospec=True,
                return_value=fake_done,
            ),
            patch("kodo.orchestrators.claude_code.VerificationState", autospec=True),
            patch("kodo.orchestrators.claude_code.log", autospec=True),
        ):
            mock_mcp.return_value = MagicMock(_mcp_server=MagicMock())

            orch = ClaudeCodeOrchestrator(model="sonnet")
            orch.cycle(
                goal="test",
                project_dir=tmp_path,
                team=fake_team,
                max_exchanges=5,
            )

            # Check inside the patch.dict context so we see the restored value,
            # not the real env restored by patch.dict cleanup.
            assert os.environ.get("ANTHROPIC_API_KEY") == original_key, (
                "ANTHROPIC_API_KEY was not restored after the cycle — "
                "this would break the orchestrator's own API calls."
            )

    def test_works_without_api_key(self, tmp_path: Path):
        """When ANTHROPIC_API_KEY is not set, the cycle should work fine."""
        from kodo.orchestrators.claude_code import ClaudeCodeOrchestrator

        captured_env: dict[str, str] = {}
        FakeClient = _make_fake_client_class(captured_env)

        fake_done = MagicMock()
        fake_done.called = True
        fake_done.success = True
        fake_done.summary = "test done"

        fake_team = MagicMock(spec=dict)

        env_without_key = {
            k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"
        }
        with (
            patch.dict(os.environ, env_without_key, clear=True),
            patch("claude_agent_sdk.ClaudeSDKClient", FakeClient),
            patch(
                "kodo.orchestrators.claude_code.build_mcp_server", autospec=True
            ) as mock_mcp,
            patch(
                "kodo.orchestrators.claude_code.build_cycle_prompt",
                autospec=True,
                return_value="go",
            ),
            patch(
                "kodo.orchestrators.claude_code.DoneSignal",
                autospec=True,
                return_value=fake_done,
            ),
            patch("kodo.orchestrators.claude_code.VerificationState", autospec=True),
            patch("kodo.orchestrators.claude_code.log", autospec=True),
        ):
            mock_mcp.return_value = MagicMock(_mcp_server=MagicMock())

            orch = ClaudeCodeOrchestrator(model="sonnet")
            orch.cycle(
                goal="test",
                project_dir=tmp_path,
                team=fake_team,
                max_exchanges=5,
            )

        assert "ANTHROPIC_API_KEY" not in captured_env
