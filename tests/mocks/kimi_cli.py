"""Deterministic ACP executable used by worker and orchestrator integration tests."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path


def emit(message):
    print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)


def record(message):
    if path := os.environ.get("KODO_FAKE_KIMI_REQUESTS"):
        with Path(path).open("a") as stream:
            stream.write(json.dumps(message) + "\n")


async def finish_via_mcp(servers):
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with sse_client(servers[0]["url"]) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            result = await client.call_tool(
                "goal_done", {"summary": "Completed through Kimi MCP"}
            )
            assert not result.isError


def main():
    assert sys.argv[1:] == ["acp"]
    mode = os.environ.get("KODO_FAKE_KIMI_MODE", "normal")
    servers = []
    session_id = ""
    prompts = 0
    for raw in sys.stdin:
        message = json.loads(raw)
        record(message)
        method = message.get("method")
        params = message.get("params", {})
        result = {}
        if method == "initialize":
            result = {
                "protocolVersion": 1,
                "agentCapabilities": {
                    "loadSession": True,
                    "mcpCapabilities": {"http": True, "sse": True},
                },
                "agentInfo": {"name": "Fake Kimi", "version": "0.27.0"},
                "authMethods": [],
            }
        elif method in ("session/new", "session/load"):
            if mode == "auth":
                emit(
                    {
                        "id": message["id"],
                        "error": {
                            "code": -32000,
                            "message": "Authentication required. Run kimi login.",
                        },
                    }
                )
                continue
            session_id = params.get("sessionId") or str(uuid.uuid4())
            servers = params.get("mcpServers", [])
            if method == "session/load":
                prompts = 1
            result = {
                "sessionId": session_id,
                "models": {"currentModelId": "mock-kimi", "availableModels": []},
                "configOptions": [],
            }
        elif method == "session/prompt":
            prompts += 1
            if mode == "inherited_stdout":
                child = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    stdin=subprocess.DEVNULL,
                )
                Path(os.environ["KODO_FAKE_KIMI_CHILD_PID"]).write_text(str(child.pid))
                time.sleep(60)
            if mode == "hang":
                time.sleep(60)
            if mode == "error":
                emit(
                    {
                        "id": message["id"],
                        "error": {
                            "code": -32001,
                            "message": "Provider unavailable",
                        },
                    }
                )
                continue
            if mode == "permission":
                emit(
                    {
                        "id": "permission-1",
                        "method": "session/request_permission",
                        "params": {
                            "sessionId": session_id,
                            "options": [
                                {
                                    "optionId": "deny",
                                    "kind": "reject_once",
                                    "name": "Reject",
                                },
                                {
                                    "optionId": "allow",
                                    "kind": "allow_once",
                                    "name": "Allow",
                                },
                            ],
                        },
                    }
                )
                record(json.loads(sys.stdin.readline()))
            tool_count = int(
                os.environ.get("KODO_FAKE_KIMI_TOOLS", "3" if mode == "limit" else "0")
            )
            for number in range(tool_count):
                emit(
                    {
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "tool_call",
                                "toolCallId": str(number),
                                "title": "Inspect files",
                                "status": "completed",
                            },
                        },
                    }
                )
            if mode == "limit":
                cancel = json.loads(sys.stdin.readline())
                record(cancel)
                assert cancel["method"] == "session/cancel"
                emit({"id": message["id"], "result": {"stopReason": "cancelled"}})
                continue
            text = os.environ.get("KODO_FAKE_KIMI_TEXT", "Hello world")
            for chunk in (text[:5], text[5:]):
                emit(
                    {
                        "method": "session/update",
                        "params": {
                            "sessionId": session_id,
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": chunk},
                            },
                        },
                    }
                )
            if mode == "mcp" or (mode == "nudge" and prompts > 1):
                asyncio.run(finish_via_mcp(servers))
            result = {"stopReason": "end_turn"}
        if "id" in message:
            emit({"id": message["id"], "result": result})


if __name__ == "__main__":
    main()
