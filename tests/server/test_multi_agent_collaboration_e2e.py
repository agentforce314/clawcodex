"""Leader and teammates collaborate through the real WebSocket server and tools."""

from __future__ import annotations

import html
import json
import re
from types import SimpleNamespace

import pytest

from src.providers.base import ChatResponse
from src.server.direct_connect_manager import (
    DirectConnectCallbacks,
    DirectConnectSessionManager,
)
from src.server.direct_connect_session import create_direct_connect_session
from src.tool_system.defaults import build_default_registry
from tests.server.test_agent_server_e2e import (
    _assistant_text,
    _running_server,
    _wait_for,
)

pytestmark = pytest.mark.integration


class CollaborationProvider:
    model = "collaboration-test"

    def __init__(self, output):
        self.output = output
        self.root_stage = 0
        self.child_stage = 0
        self.requests = []
        self.deleted = False

    def chat_stream_response(self, *args, **kwargs):
        raise NotImplementedError

    def chat(self, messages, **kwargs):
        serialized = json.dumps(messages)
        teammate = bool(
            re.search(r"You are builder, a persistent teammate", serialized)
        )
        latest = next(m for m in reversed(messages) if m.get("role") == "user")
        content = latest["content"]
        text = html.unescape(
            content
            if isinstance(content, str)
            else "\n".join(
                block.get("text", "")
                for block in content
                if block.get("type") == "text"
            )
        )
        self.requests.append((teammate, text, serialized))
        tool = None
        answer = "Ready"
        if teammate:
            if '"type": "shutdown_request"' in text:
                request, _ = json.JSONDecoder().raw_decode(text[text.index("{") :])
                tool = (
                    "SendMessage",
                    {
                        "to": "team-lead",
                        "message": {
                            "type": "shutdown_response",
                            "request_id": request["request_id"],
                            "approve": True,
                        },
                    },
                )
            elif "WRITE_ARTIFACT" in text:
                self.child_stage = 1
                tool = (
                    "Write",
                    {
                        "file_path": str(self.output),
                        "content": "verified teammate output",
                    },
                )
            elif self.child_stage == 1:
                self.child_stage = 2
                tool = (
                    "SendMessage",
                    {
                        "to": "team-lead",
                        "message": "artifact ready",
                        "summary": "Completed artifact",
                    },
                )
            else:
                answer = "PRIVATE teammate prose"
        elif self.root_stage == 0:
            self.root_stage = 1
            tool = ("TeamCreate", {"team_name": "transport"})
        elif self.root_stage == 1:
            self.root_stage = 2
            tool = (
                "Agent",
                {
                    "name": "builder",
                    "description": "Build artifact",
                    "prompt": "WRITE_ARTIFACT",
                },
            )
        elif "RESTART_BUILDER" in text:
            tool = (
                "SendMessage",
                {
                    "to": "builder",
                    "message": "WRITE_ARTIFACT again",
                    "summary": "Retry assignment",
                },
            )
        elif "SHUTDOWN_TEAM" in text:
            tool = (
                "SendMessage",
                {
                    "to": "builder",
                    "message": {"type": "shutdown_request", "reason": "Finished"},
                },
            )
        elif "Teammate exited" in text and not self.deleted:
            self.deleted = True
            tool = ("TeamDelete", {})
        elif "artifact ready" in text:
            answer = "Verified team artifact delivered"
        else:
            answer = "Team is available"
        calls = (
            [{"id": f"call-{len(self.requests)}", "name": tool[0], "input": tool[1]}]
            if tool
            else None
        )
        return ChatResponse(
            content=answer,
            model=self.model,
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="tool_use" if calls else "stop",
            tool_uses=calls,
        )


@pytest.mark.parametrize("interrupt_first", [False, True])
async def test_team_permission_messages_interrupt_and_shutdown_over_websocket(
    tmp_path, monkeypatch, interrupt_first
):
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path / "config"))
    provider = CollaborationProvider(tmp_path / "artifact.txt")
    registry = build_default_registry(provider=provider)
    received, permissions = [], []
    allow = not interrupt_first
    async with _running_server(tmp_path, lambda **kwargs: provider, registry) as config:
        cfg, _ = await create_direct_connect_session(
            server_url=f"http://127.0.0.1:{config.port}", cwd=str(tmp_path)
        )

        async def on_permission(request, request_id):
            permissions.append(request)
            if allow:
                await client.respond_to_permission_request(
                    request_id,
                    SimpleNamespace(behavior="allow", updated_input={}, message=""),
                )

        client = DirectConnectSessionManager(
            cfg,
            DirectConnectCallbacks(
                on_message=received.append,
                on_permission_request=on_permission,
            ),
        )
        await client.connect()
        try:
            assert await _wait_for(
                lambda: any(message.get("subtype") == "init" for message in received)
            )
            await client.send_message("START_TEAM")
            assert await _wait_for(lambda: bool(permissions)), provider.requests
            assert permissions[0]["tool_name"] == "Write"
            builder_id = permissions[0]["agent_id"]
            assert builder_id
            if interrupt_first:
                # Cancel the pending teammate ask, leaving the teammate alive.
                await client._ws.send(
                    json.dumps(
                        {
                            "type": "control_request",
                            "request_id": "stop-assignment",
                            "request": {
                                "subtype": "subagent_interrupt",
                                "subagent_id": builder_id,
                            },
                        }
                    )
                )
                assert await _wait_for(
                    lambda: any(
                        message.get("type") == "agent_progress"
                        and message.get("agent_id") == builder_id
                        and "Idle" in message.get("activity", "")
                        for message in received
                    )
                )
                assert not provider.output.exists()
                allow = True
                await client.send_message("RESTART_BUILDER")
                assert await _wait_for(lambda: len(permissions) == 2)
                assert permissions[1]["agent_id"] == builder_id
            assert await _wait_for(
                lambda: provider.output.exists()
                and provider.output.read_text() == "verified teammate output"
            )
            assert await _wait_for(
                lambda: any(
                    "Verified team artifact delivered" in _assistant_text(message)
                    for message in received
                    if message.get("type") == "assistant"
                )
            ), provider.requests
            assert any(
                message.get("type") == "agent_progress"
                and message.get("agent_id") == builder_id
                for message in received
            )
            assert all(
                "PRIVATE teammate prose" not in _assistant_text(message)
                for message in received
                if message.get("type") == "assistant"
            )
            # The leader is still the main conversation: its tool display data survives TeamCreate.
            assert any(
                (message.get("tool_use_result") or {}).get("agent_id") == builder_id
                for message in received
            )
            await client.send_message("SHUTDOWN_TEAM")
            assert await _wait_for(
                lambda: provider.deleted
                and not (tmp_path / ".clawcodex" / "team.json").exists()
            ), provider.requests
            assert await _wait_for(
                lambda: any(
                    message.get("type") == "agent_progress"
                    and message.get("agent_id") == builder_id
                    and message.get("status") == "completed"
                    for message in received
                )
            )
        finally:
            await client.disconnect()
