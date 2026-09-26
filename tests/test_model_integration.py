"""Exercise current provider defaults through real SDKs with HTTP intercepted."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic_ai import Agent

from kodo.models import CODEX_DEFAULT, get_model_info, make_fresh_model, resolve_model


@pytest.mark.real_models
@pytest.mark.parametrize(
    "alias",
    [
        CODEX_DEFAULT,
        "gpt-6-astra",
        "opus",
        "sonnet",
        "gemini-flash",
        "deepseek",
        "deepseek-reasoner",
    ],
)
def test_default_models_complete_tool_roundtrip(alias, monkeypatch):
    """A selected model can call a tool and replay signed thinking with its result.

    This tests SDK serialization, endpoint selection, and response parsing without
    substituting the SDK or consuming provider tokens.
    """
    info = get_model_info(alias)
    assert info is not None
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        first = len(requests) == 1
        if info.pydantic_id.startswith("openai:"):
            assert request.url.path == "/v1/responses"
            assert body["model"] == info.full_model_id
            output = (
                [
                    {
                        "type": "reasoning",
                        "id": "rs_1",
                        "summary": [],
                        "encrypted_content": "signed-thinking",
                    },
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "read_evidence",
                        "arguments": "{}",
                    },
                ]
                if first
                else [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Verified.",
                                "annotations": [],
                            }
                        ],
                    },
                ]
            )
            payload = {
                "id": f"resp_{len(requests)}",
                "object": "response",
                "created_at": 1,
                "model": info.full_model_id,
                "status": "completed",
                "output": output,
                "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            }
        elif info.pydantic_id.startswith("anthropic:"):
            assert request.url.path == "/v1/messages"
            assert body["model"] == info.full_model_id
            content = (
                [
                    {
                        "type": "thinking",
                        "thinking": "",
                        "signature": "signed-thinking",
                    },
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "read_evidence",
                        "input": {},
                    },
                ]
                if first
                else [{"type": "text", "text": "Verified."}]
            )
            payload = {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": info.full_model_id,
                "content": content,
                "stop_reason": "tool_use" if first else "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 10},
            }
        elif info.pydantic_id.startswith("deepseek:"):
            assert request.url.path == "/chat/completions"
            assert body["model"] == info.full_model_id
            message = {"role": "assistant", "content": None if first else "Verified."}
            if first:
                message.update(
                    reasoning_content="signed-thinking",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "read_evidence", "arguments": "{}"},
                        }
                    ],
                )
            payload = {
                "id": "chat_1",
                "object": "chat.completion",
                "created": 1,
                "model": info.full_model_id,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if first else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "total_tokens": 20,
                },
            }
        else:
            assert request.url.path.endswith(f"/{info.full_model_id}:generateContent")
            parts = (
                [
                    {
                        "functionCall": {"name": "read_evidence", "args": {}},
                        "thoughtSignature": "c2lnbmVk",
                    }
                ]
                if first
                else [{"text": "Verified."}]
            )
            payload = {
                "candidates": [
                    {
                        "content": {"role": "model", "parts": parts},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 10,
                    "totalTokenCount": 20,
                },
            }
        return httpx.Response(200, json=payload)

    clients = []

    class InterceptedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, transport=httpx.MockTransport(respond))
            clients.append(self)

    monkeypatch.setattr(httpx, "AsyncClient", InterceptedClient)
    calls = []

    def read_evidence() -> str:
        """Read the verification result."""
        calls.append("read")
        return "The integration check passed."

    async def run():
        try:
            model = make_fresh_model(resolve_model(alias))
            assert not isinstance(model, str), "Construct an isolated provider client"
            return await Agent(model, tools=[read_evidence]).run("Verify the evidence.")
        finally:
            for client in clients:
                await client.aclose()

    import asyncio

    result = asyncio.run(run())
    assert result.output == "Verified."
    assert calls == ["read"]
    assert len(requests) == 2
    replay = json.dumps(requests[1])
    assert "The integration check passed." in replay
    assert ("c2lnbmVk" if alias == "gemini-flash" else "signed-thinking") in replay
