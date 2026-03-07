"""Tests for ApiOrchestrator context summarization wiring and processor behavior.

Verifies that:
- cycle() creates a SummarizationProcessor with the correct trigger/keep params
- The processor is omitted when max_context_tokens=None
- The processor triggers correctly at 100k tokens (chars//4 heuristic)
- Preserved messages come from the tail of the original list
- Tool call/response pairs are not split during summarization
- No real LLM calls are made
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai_summarization import (
    SummarizationProcessor,
    count_tokens_approximately,
)

from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator
from tests.conftest import FakeRunResult


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_fake_team():
    """Create a minimal fake TeamConfig for orchestrator tests."""
    from kodo.agent import Agent
    from tests.conftest import FakeSession

    session = FakeSession(response_text="ok")
    agent = Agent(session, "test agent", max_turns=5)
    return {"worker": agent}


def _make_messages(total_chars: int, num_messages: int):
    """Create alternating request/response messages totaling ``total_chars``.

    Uses the knowledge that ``count_tokens_approximately()`` = total_chars // 4,
    so to produce N tokens pass ``total_chars = N * 4``.
    """
    chars_per_msg = total_chars // num_messages
    messages = []
    for i in range(num_messages):
        text = "x" * chars_per_msg
        if i % 2 == 0:
            messages.append(
                ModelRequest(parts=[UserPromptPart(content=text)])
            )
        else:
            messages.append(
                ModelResponse(
                    parts=[TextPart(content=text)],
                    model_name="test",
                    timestamp="2024-01-01T00:00:00Z",
                )
            )
    return messages


def _run_async(coro):
    """Run an async coroutine synchronously for testing."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── TestProcessorWiring ──────────────────────────────────────────────────


class TestProcessorWiring:
    """Verify that ApiOrchestrator.cycle() wires create_summarization_processor
    with the correct parameters and passes the result to pydantic-ai Agent."""

    def test_processor_created_with_correct_params(self, tmp_path: Path):
        """Default 100k limit → trigger=100k, keep=50k."""
        log.init(RunDir.create(tmp_path, "ctx_params"))

        captured_hp = {}
        mock_processor = MagicMock()

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            captured_hp.update(kwargs)
            self.run_sync = lambda prompt, **kw: FakeRunResult()

        team = _make_fake_team()

        with (
            patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
            patch(
                "kodo.orchestrators.api.create_summarization_processor",
                return_value=mock_processor,
            ) as mock_create,
            patch.object(ApiOrchestrator, "_summarize", return_value="ok"),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6", max_context_tokens=100_000)
            orch.cycle("test", tmp_path, team, max_exchanges=5)

        mock_create.assert_called_once_with(
            trigger=("tokens", 100_000),
            keep=("tokens", 50_000),
            model=orch._pydantic_model,
        )
        assert captured_hp["history_processors"] == [mock_processor]

    def test_processor_custom_token_limit(self, tmp_path: Path):
        """Custom 200k limit → trigger=200k, keep=100k."""
        log.init(RunDir.create(tmp_path, "ctx_custom"))

        mock_processor = MagicMock()

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            self.run_sync = lambda prompt, **kw: FakeRunResult()

        team = _make_fake_team()

        with (
            patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
            patch(
                "kodo.orchestrators.api.create_summarization_processor",
                return_value=mock_processor,
            ) as mock_create,
            patch.object(ApiOrchestrator, "_summarize", return_value="ok"),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6", max_context_tokens=200_000)
            orch.cycle("test", tmp_path, team, max_exchanges=5)

        mock_create.assert_called_once_with(
            trigger=("tokens", 200_000),
            keep=("tokens", 100_000),
            model=orch._pydantic_model,
        )

    def test_processor_disabled_when_none(self, tmp_path: Path):
        """max_context_tokens=None → no processor, history_processors=None."""
        log.init(RunDir.create(tmp_path, "ctx_none"))

        captured_hp = {}

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            captured_hp.update(kwargs)
            self.run_sync = lambda prompt, **kw: FakeRunResult()

        team = _make_fake_team()

        with (
            patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
            patch(
                "kodo.orchestrators.api.create_summarization_processor",
            ) as mock_create,
            patch.object(ApiOrchestrator, "_summarize", return_value="ok"),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6", max_context_tokens=None)
            orch.cycle("test", tmp_path, team, max_exchanges=5)

        mock_create.assert_not_called()
        assert captured_hp.get("history_processors") is None


# ── TestTokenCountingHeuristic ───────────────────────────────────────────


class TestTokenCountingHeuristic:
    """Unit tests for count_tokens_approximately with real pydantic-ai types."""

    def test_user_prompt_tokens(self):
        """UserPromptPart character count ÷ 4 = token estimate."""
        msg = ModelRequest(parts=[UserPromptPart(content="a" * 400)])
        assert count_tokens_approximately([msg]) == 100

    def test_text_response_tokens(self):
        """TextPart character count ÷ 4 = token estimate."""
        msg = ModelResponse(
            parts=[TextPart(content="b" * 800)],
            model_name="test",
            timestamp="2024-01-01T00:00:00Z",
        )
        assert count_tokens_approximately([msg]) == 200

    def test_system_prompt_counted(self):
        """SystemPromptPart characters contribute to the count."""
        msg = ModelRequest(parts=[SystemPromptPart(content="s" * 1200)])
        assert count_tokens_approximately([msg]) == 300

    def test_mixed_messages(self):
        """Combined user + assistant messages → total_chars // 4."""
        msgs = [
            ModelRequest(parts=[UserPromptPart(content="u" * 200)]),
            ModelResponse(
                parts=[TextPart(content="a" * 600)],
                model_name="test",
                timestamp="2024-01-01T00:00:00Z",
            ),
        ]
        assert count_tokens_approximately(msgs) == (200 + 600) // 4


# ── TestProcessorTriggerBehavior ─────────────────────────────────────────


class TestProcessorTriggerBehavior:
    """Integration tests with real SummarizationProcessor, mocked _create_summary."""

    def _make_processor(self):
        return SummarizationProcessor(
            model="test-model",
            trigger=("tokens", 100_000),
            keep=("tokens", 50_000),
        )

    def test_below_threshold_no_op(self):
        """~50k tokens (well below 100k trigger) → output identical to input."""
        processor = self._make_processor()
        # 50k tokens = 200k chars
        messages = _make_messages(total_chars=200_000, num_messages=40)

        result = _run_async(processor(messages))

        assert len(result) == len(messages)
        # Identity — no copy made
        assert result is messages

    def test_at_threshold_triggers(self):
        """Exactly 100k tokens (≥ trigger) → summarization fires."""
        processor = self._make_processor()
        # 100k tokens = 400k chars
        messages = _make_messages(total_chars=400_000, num_messages=80)

        async def fake_summary(msgs):
            return "Summarized old context."

        processor._create_summary = fake_summary

        result = _run_async(processor(messages))

        assert len(result) < len(messages)
        first = result[0]
        assert isinstance(first, ModelRequest)
        assert isinstance(first.parts[0], SystemPromptPart)
        assert "Summary of previous conversation" in first.parts[0].content

    def test_above_threshold_summarizes(self):
        """~120k tokens → fewer messages, summary injected as SystemPromptPart."""
        processor = self._make_processor()
        # 120k tokens = 480k chars
        messages = _make_messages(total_chars=480_000, num_messages=80)

        async def fake_summary(msgs):
            return "Previously: the agent worked on feature X."

        processor._create_summary = fake_summary

        result = _run_async(processor(messages))

        assert len(result) < len(messages)
        first = result[0]
        assert isinstance(first, ModelRequest)
        assert isinstance(first.parts[0], SystemPromptPart)
        assert "feature X" in first.parts[0].content

    def test_preserved_messages_from_tail(self):
        """After summarization, result[1:] are a contiguous tail of originals."""
        processor = self._make_processor()
        messages = _make_messages(total_chars=480_000, num_messages=80)

        async def fake_summary(msgs):
            return "summary"

        processor._create_summary = fake_summary

        result = _run_async(processor(messages))

        preserved = result[1:]  # skip summary message
        assert len(preserved) > 0

        # Find the start index by identity (not equality — content repeats)
        start_idx = next(i for i, m in enumerate(messages) if m is preserved[0])

        # Preserved messages should be a contiguous slice of originals (by identity)
        expected_tail = messages[start_idx:]
        assert len(preserved) == len(expected_tail)
        for p, o in zip(preserved, expected_tail):
            assert p is o, "Preserved message is not the same object from original"

    def test_preserved_token_count_within_keep(self):
        """Preserved messages should be roughly ≤ 50k tokens (the keep value)."""
        processor = self._make_processor()
        messages = _make_messages(total_chars=480_000, num_messages=80)

        async def fake_summary(msgs):
            return "summary"

        processor._create_summary = fake_summary

        result = _run_async(processor(messages))

        preserved = result[1:]
        preserved_tokens = count_tokens_approximately(preserved)
        # Allow some tolerance for safe-cutoff rounding
        assert preserved_tokens <= 55_000
        # Should retain a meaningful amount
        assert preserved_tokens > 20_000

    def test_empty_messages_no_crash(self):
        """Empty message list → empty list returned."""
        processor = self._make_processor()
        result = _run_async(processor([]))
        assert result == []

    def test_single_message_below_threshold(self):
        """One short message passes through unchanged."""
        processor = self._make_processor()
        messages = [ModelRequest(parts=[UserPromptPart(content="hello")])]

        result = _run_async(processor(messages))

        assert result is messages

    def test_one_token_below_threshold_no_trigger(self):
        """99,999 tokens (just below 100k) → no summarization."""
        processor = self._make_processor()
        # 99,999 tokens = 399,996 chars
        messages = _make_messages(total_chars=399_996, num_messages=80)

        # Verify we're just below
        assert count_tokens_approximately(messages) < 100_000

        result = _run_async(processor(messages))

        assert result is messages

    def test_summary_with_error_fallback(self):
        """When _create_summary returns an error string, it's still injected."""
        processor = self._make_processor()
        messages = _make_messages(total_chars=480_000, num_messages=80)

        async def error_summary(msgs):
            return "Error generating summary: LLM unavailable"

        processor._create_summary = error_summary

        result = _run_async(processor(messages))

        assert len(result) < len(messages)
        first = result[0]
        assert isinstance(first, ModelRequest)
        assert "Error generating summary" in first.parts[0].content


# ── TestSafeCutoff ───────────────────────────────────────────────────────


class TestSafeCutoff:
    """Verify that tool call/response pairs are never split by summarization."""

    def test_tool_pairs_not_split(self):
        """ToolCallPart + ToolReturnPart with matching IDs stay on same side."""
        processor = SummarizationProcessor(
            model="test-model",
            trigger=("tokens", 100_000),
            keep=("tokens", 50_000),
        )

        # Build messages: ~60k tokens before pair, pair, ~60k tokens after
        # Total ~120k tokens, trigger fires, keep=50k means cutoff near middle
        prefix = _make_messages(total_chars=240_000, num_messages=40)
        call_id = "call_test_tool_pair"
        tool_call = ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="ask_worker",
                    args={"task": "do work"},
                    tool_call_id=call_id,
                )
            ],
            model_name="test",
            timestamp="2024-01-01T00:00:00Z",
        )
        tool_return = ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="ask_worker",
                    content="work done",
                    tool_call_id=call_id,
                )
            ]
        )
        suffix = _make_messages(total_chars=240_000, num_messages=40)
        messages = [*prefix, tool_call, tool_return, *suffix]

        async def fake_summary(msgs):
            return "summary"

        processor._create_summary = fake_summary

        result = _run_async(processor(messages))

        preserved = result[1:]  # skip summary

        has_call = any(
            isinstance(msg, ModelResponse)
            and any(isinstance(p, ToolCallPart) and p.tool_call_id == call_id for p in msg.parts)
            for msg in preserved
        )
        has_return = any(
            isinstance(msg, ModelRequest)
            and any(isinstance(p, ToolReturnPart) and p.tool_call_id == call_id for p in msg.parts)
            for msg in preserved
        )

        # If either is present, both must be (pair not split)
        if has_call or has_return:
            assert has_call and has_return, (
                "Tool call/return pair was split across summarization boundary"
            )
