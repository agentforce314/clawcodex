"""Native agent-server MCP wiring, with only model inference controlled.

The default run uses a scripted HTTP peer. CLAWCODEX_TEST_PARALLEL_LIVE=1 uses
real anonymous Parallel search/fetch through the same loader, SDK transport,
registry, permissions and query loop. This proves wiring, not model quality.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from demos.parallel_search.__main__ import select_url
from src.providers.base import ChatResponse
from src.server.agent_server import AgentServerConfig, make_spawn_agent

SEARCH = "mcp__parallel-search__web_search"
FETCH = "mcp__parallel-search__web_fetch"


def _result_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        message = message if isinstance(message, dict) else message.to_dict()
        content = message.get("content")
        if isinstance(content, list):
            for block in reversed(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    assert not block.get("is_error"), content
                    value = block.get("content", "")
                    if isinstance(value, list):
                        return "\n".join(b.get("text", "") for b in value)
                    return str(value)
    raise AssertionError("Native loop did not feed tool output to the provider")


@pytest.mark.asyncio
@pytest.mark.parametrize("allow", [True, False])
async def test_native_parallel_conversation(monkeypatch, tmp_path, allow) -> None:
    """Discover, ask permission, search, choose a result URL, fetch and answer."""
    live = os.environ.get("CLAWCODEX_TEST_PARALLEL_LIVE") == "1"
    if live and not allow:
        pytest.skip("The offline run covers denial without spending a live request")
    demo = Path(__file__).resolve().parents[2] / "demos" / "parallel_search"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_bytes = (demo / "config.json").read_bytes()
    config = json.loads(config_bytes)
    config["mcpServers"]["invalid-server"] = {"type": "http"}
    (config_dir / "config.json").write_text(json.dumps(config))
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "unapproved-project": config["mcpServers"]["parallel-search"],
                }
            }
        )
    )
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("CLAWCODEX_MANAGED_CONFIG_DIR", str(tmp_path / "managed"))
    for name in list(os.environ):
        if any(part in name for part in ("API_KEY", "AUTH_TOKEN", "ACCESS_TOKEN")):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    from src.bootstrap.state import _STATE

    # Exercise interactive project approval, as on the TUI backend.
    monkeypatch.setattr(_STATE, "is_interactive", True)
    requests: list[dict[str, Any]] = []
    state: dict[str, Any] = {"turns": 0, "permissions": [], "selected_url": None}

    def peer(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(200 if request.method == "DELETE" else 405)
        rpc = json.loads(request.content)
        method = rpc["method"]
        if "id" not in rpc:
            return httpx.Response(202)
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "controlled-http-peer", "version": "1"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": name,
                        "description": name,
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": True,
                        },
                    }
                    for name in ("web_search", "web_fetch")
                ]
            }
        elif method == "tools/call":
            params = rpc["params"]
            if params["name"] == "web_search":
                text = json.dumps(
                    {"results": [{"url": "https://example.org/selected-page"}]}
                )
            else:
                assert params["arguments"]["urls"] == [
                    "https://example.org/selected-page"
                ]
                text = "Fetched fixture: asyncio provides concurrent code using async/await."
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        else:
            raise AssertionError(method)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": result}
        )

    original_send = httpx.AsyncClient.send

    async def observed_send(client, request, **kwargs):
        rpc = json.loads(request.content) if request.method == "POST" else {}
        response = await original_send(client, request, **kwargs)
        requests.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "url": str(request.url),
                "method": request.method,
                "user_agent": request.headers.get("user-agent"),
                "authorization_present": "authorization" in request.headers,
                "rpc_method": rpc.get("method"),
                "params": rpc.get("params"),
                "status": response.status_code,
                "retry_after": response.headers.get("retry-after"),
            }
        )
        assert response.status_code != 429, requests[-1]
        return response

    monkeypatch.setattr(httpx.AsyncClient, "send", observed_send)
    if not live:
        from src.services.mcp.fetch_wrappers import build_mcp_timeout

        def client_factory(*, headers=None):
            # Only the HTTP peer is controlled; the SDK and native caller stay real.
            return httpx.AsyncClient(
                headers=headers,
                timeout=build_mcp_timeout(),
                transport=httpx.MockTransport(peer),
            )

        monkeypatch.setattr(
            "src.services.mcp.transport.build_mcp_http_client", client_factory
        )

    class ResultDependentProvider:
        """Controlled model fixture: each decision depends on native tool output."""

        def __init__(self, api_key=None, base_url=None, model=None):
            self.model = "controlled-wiring-fixture"

        def chat_stream_response(self, *args, **kwargs):
            raise NotImplementedError

        def chat(self, messages, tools=None, **kwargs):
            state["turns"] += 1
            if state["turns"] == 1:
                assert "ToolSearch" in json.dumps(tools)
                name = "ToolSearch"
                args = {"query": "mcp__parallel-search__", "max_results": 2}
            elif state["turns"] == 2:
                assert SEARCH in json.dumps(tools) and FETCH in json.dumps(tools)
                args = {
                    "objective": "Find official Python asyncio documentation",
                    "search_queries": ["Python asyncio official documentation"],
                    "session_id": "native-agent-wiring-test",
                }
                name = SEARCH
            elif not allow:
                return ChatResponse(
                    content="Permission denied; no search executed.",
                    model=self.model,
                    usage={},
                    finish_reason="stop",
                )
            elif state["turns"] == 3:
                state["search_text"] = _result_text(messages)
                state["selected_url"] = select_url(state["search_text"])
                args = {
                    "urls": [state["selected_url"]],
                    "objective": "Explain what asyncio is used for",
                    "session_id": "native-agent-wiring-test",
                }
                name = FETCH
            else:
                state["fetch_text"] = _result_text(messages)
                assert "asyncio" in state["fetch_text"].lower()
                return ChatResponse(
                    content="From fetched content: " + state["fetch_text"][:500],
                    model=self.model,
                    usage={},
                    finish_reason="stop",
                )
            return ChatResponse(
                content="",
                model=self.model,
                usage={},
                finish_reason="tool_use",
                tool_uses=[
                    {"id": f"call{state['turns']}", "name": name, "input": args}
                ],
            )

    # Explicit local-provider selection uses the native no-key credential rule.
    # No production defaults, registry, loader, tool wrappers or loop are patched.
    monkeypatch.setattr(
        "src.providers.get_provider_class", lambda name: ResultDependentProvider
    )
    spawn = make_spawn_agent(
        AgentServerConfig(provider_name="ollama", permission_mode="default")
    )
    handle = await spawn("parallel_native_test", str(tmp_path), None)
    gen = handle.messages_from_agent()
    frames = []
    try:
        for _ in range(30):
            frame = await asyncio.wait_for(gen.__anext__(), 40)
            frames.append(frame)
            if frame.get("subtype") == "init":
                names = {tool["name"] for tool in frame["tools"]}
                assert {SEARCH, FETCH}.issubset(names), frame
                assert not any("unapproved-project" in n for n in names)
                notices = [
                    f.get("message", "") for f in frames if f.get("subtype") == "status"
                ]
                assert any("invalid-server" in n for n in notices), notices
                assert any("unapproved-project" in n for n in notices), notices
                assert frame["permission_mode"] == "default", frame
                break
        else:
            raise AssertionError(frames)
        await handle.send_to_agent(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "Search for Python asyncio, fetch a returned URL, and explain it.",
                },
            }
        )
        for _ in range(80):
            frame = await asyncio.wait_for(gen.__anext__(), 80)
            frames.append(frame)
            if frame.get("type") == "control_request":
                request = frame["request"]
                assert request["subtype"] == "can_use_tool", frame
                state["permissions"].append(request["tool_name"])
                # Prove the call has not run before the explicit approval.
                assert (
                    len([r for r in requests if r["rpc_method"] == "tools/call"])
                    == len(state["permissions"]) - 1
                )
                await handle.send_to_agent(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": frame["request_id"],
                            "response": {"behavior": "allow" if allow else "deny"},
                        },
                    }
                )
            if frame.get("type") == "result":
                assert not frame.get("is_error"), frame
                break
        else:
            raise AssertionError("No final native agent result")
        calls = [r for r in requests if r["rpc_method"] == "tools/call"]
        if allow:
            assert state["permissions"] == [SEARCH, FETCH]
            assert state["turns"] == 4
            assert [r["params"]["name"] for r in calls] == ["web_search", "web_fetch"]
            assert calls[1]["params"]["arguments"]["urls"] == [state["selected_url"]]
            assert "From fetched content:" in json.dumps(frames)
            assert state["fetch_text"][:200] in str(frame["result"])
        else:
            assert state["permissions"] == [SEARCH]
            assert calls == []
        expected_ua = json.loads(config_bytes)["mcpServers"]["parallel-search"][
            "headers"
        ]["User-Agent"]
        assert requests and all(r["user_agent"] == expected_ua for r in requests)
        assert all(r["url"] == "https://search.parallel.ai/mcp" for r in requests)
        assert not any(r["authorization_present"] for r in requests)
        assert not list(config_dir.glob("*token*"))
    finally:
        await handle.shutdown()
        await gen.aclose()
        evidence = os.environ.get("CLAWCODEX_PARALLEL_EVIDENCE")
        if evidence:
            Path(evidence).write_text(
                json.dumps(
                    {
                        "live": live,
                        "configuration": json.loads(config_bytes),
                        "caller": "make_spawn_agent -> McpRuntime -> loader/SDK -> registry/permissions/query loop",
                        "fixture": "ResultDependentProvider; proves wiring, not production model quality",
                        "state": state,
                        "requests": requests,
                        "frames": frames,
                    },
                    indent=2,
                    default=str,
                )
            )


@pytest.mark.asyncio
async def test_native_config_errors_without_tools(monkeypatch, tmp_path) -> None:
    """Surface invalid-only configuration even when MCP startup returns False."""
    from tests.server.test_agent_server_e2e import _TextProvider

    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "mcpServers": {"invalid-only": {"type": "http"}},
            }
        )
    )
    monkeypatch.setenv("CLAWCODEX_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CLAWCODEX_MANAGED_CONFIG_DIR", str(tmp_path / "managed"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.providers.get_provider_class", lambda name: _TextProvider)
    handle = await make_spawn_agent(AgentServerConfig(provider_name="ollama"))(
        "invalid_mcp_test", str(tmp_path), None
    )
    gen = handle.messages_from_agent()
    frames = []
    try:
        for _ in range(30):
            frame = await asyncio.wait_for(gen.__anext__(), 10)
            frames.append(frame)
            if frame.get("subtype") == "init":
                assert not any(t["name"].startswith("mcp__") for t in frame["tools"])
                assert any("invalid-only" in f.get("message", "") for f in frames)
                break
        else:
            raise AssertionError(frames)
    finally:
        await handle.shutdown()
        await gen.aclose()
