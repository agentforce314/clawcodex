"""ENTRY-1 — ``clawcodex mcp serve`` engine tests.

Port of ``typescript/src/entrypoints/mcp.ts`` (``startMCPServer``); plan:
my-docs/get-parity-by-folder/entrypoints-refactoring-plan.md §ENTRY-1.

The security-relevant pin (plan §W1/P1): the serve ToolContext is built with
``ToolPermissionContext()`` — mode "default", empty rules, bypass
UNAVAILABLE — NOT the ``ToolContext`` default factory (which is
``bypassPermissions`` and would run the server with all gating off). With no
``permission_handler``, ask-requiring tools fail CLOSED.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.entrypoints.mcp_serve import (
    _build_serve_context,
    _error_result,
    _result_to_content,
    _tool_description,
    build_server,
    get_combined_tools,
)


class _StubTool:
    def __init__(self, name: str) -> None:
        self.name = name


# ---------------------------------------------------------------------------
# Unit — helpers
# ---------------------------------------------------------------------------


def test_serve_context_is_enforcing_not_bypass(tmp_path: Path) -> None:
    """Regression pin for critic P1: Python's ToolContext DEFAULT is
    bypassPermissions (context.py) — the serve context must not inherit it."""
    ctx = _build_serve_context(tmp_path)
    assert ctx.permission_context.mode == "default"
    assert ctx.permission_context.is_bypass_permissions_mode_available is False
    assert ctx.permission_handler is None


def test_get_combined_tools_mcp_wins_on_collision() -> None:
    """mcp.ts:46-54 — builtins shadowed by an MCP tool name are dropped;
    MCP tools come first."""
    builtins = [_StubTool("Read"), _StubTool("Grep")]
    mcp_tools = [_StubTool("Read"), _StubTool("mcp__x__y")]
    combined = get_combined_tools(builtins, mcp_tools)
    assert [t.name for t in combined] == ["Read", "mcp__x__y", "Grep"]
    assert combined[0] is mcp_tools[0]


def test_result_to_content_mappings() -> None:
    """mcp.ts:199-227 — str / text-block / image-block / unknown / other."""
    from src.tool_system.protocol import ToolResult

    r = ToolResult(name="T", output="plain")
    [c] = _result_to_content(r)
    assert c.type == "text" and c.text == "plain"

    blocks = ToolResult(name="T", output=[
        {"type": "text", "text": "hello"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
        {"type": "mystery", "value": 1},
    ])
    out = _result_to_content(blocks)
    assert out[0].type == "text" and out[0].text == "hello"
    assert out[1].type == "image" and out[1].data == "QUJD" and out[1].mimeType == "image/png"
    assert out[2].type == "text" and json.loads(out[2].text)["type"] == "mystery"

    other = ToolResult(name="T", output={"k": "v"})
    [c2] = _result_to_content(other)
    assert json.loads(c2.text) == {"k": "v"}


def test_error_result_shape() -> None:
    r = _error_result("boom")
    assert r.isError is True and r.content[0].text == "boom"
    assert _error_result("").content[0].text == "Error"


# ---------------------------------------------------------------------------
# In-process round-trips over the SDK memory transport (real build_server)
# ---------------------------------------------------------------------------


def _session(tmp_path: Path):
    from mcp.shared.memory import create_connected_server_and_client_session

    async def _factory():
        server = await build_server(tmp_path, load_configured_mcp=False)
        return create_connected_server_and_client_session(server)

    return _factory


def test_list_tools_shape(tmp_path: Path) -> None:
    async def run() -> None:
        cm = await _session(tmp_path)()
        async with cm as s:
            listing = await s.list_tools()
            by_name = {t.name: t for t in listing.tools}
            assert {"Read", "Glob", "Grep", "Bash", "Write"} <= set(by_name)
            # Descriptions come from tool.prompt() (a non-empty string).
            assert by_name["Glob"].description
            # Input schemas are the registry's JSON-schema dicts.
            assert by_name["Glob"].inputSchema.get("type") == "object"
            # No output schemas — this port's tools declare none.
            assert all(t.outputSchema is None for t in listing.tools)

    asyncio.run(run())


def test_call_tool_read_only_executes(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("hi", encoding="utf-8")

    async def run() -> None:
        cm = await _session(tmp_path)()
        async with cm as s:
            r = await s.call_tool("Glob", {"pattern": "*.txt", "path": str(tmp_path)})
            assert r.isError is False
            assert "hello.txt" in r.content[0].text

    asyncio.run(run())


def test_call_tool_ask_requiring_denied_fail_closed(tmp_path: Path) -> None:
    """The serve posture has no permission handler → ask escalations DENY
    and the tool must not execute (mirrors TS's non-interactive empty
    permission context)."""
    async def run() -> None:
        cm = await _session(tmp_path)()
        async with cm as s:
            target = tmp_path / "never.txt"
            r = await s.call_tool("Write", {"file_path": str(target), "content": "x"})
            assert r.isError is True
            assert not target.exists(), "denied tool must not have executed"

    asyncio.run(run())


def test_call_tool_validation_error_formatted(tmp_path: Path) -> None:
    async def run() -> None:
        cm = await _session(tmp_path)()
        async with cm as s:
            r = await s.call_tool("Glob", {"pattern": 123})
            assert r.isError is True
            text = r.content[0].text
            assert "Glob" in text and "pattern" in text

    asyncio.run(run())


def test_call_tool_unknown_tool(tmp_path: Path) -> None:
    async def run() -> None:
        cm = await _session(tmp_path)()
        async with cm as s:
            r = await s.call_tool("NoSuchTool", {})
            assert r.isError is True
            assert "not found" in r.content[0].text

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Verb + lean contract + betas defaults
# ---------------------------------------------------------------------------


def test_mcp_usage_lists_serve(capsys: pytest.CaptureFixture[str]) -> None:
    from src.entrypoints.mcp import run_mcp_subcommand

    assert run_mcp_subcommand(["--help"]) == 0
    out = capsys.readouterr().out
    assert "serve" in out and "list" in out


def test_mcp_list_stays_lean() -> None:
    """The list fast path must not load the serve engine, the agent-server,
    or the TUI launcher (WI-4.3 contract) — serve's engine import lives
    inside its own verb branch. (Pre-existing transitive imports of the mcp
    PACKAGE's own server modules and the partial tool_system pull via the
    MCP config layer are NOT this contract — both measured before this PR.)"""
    code = (
        "import sys; from src.entrypoints.mcp import run_mcp_subcommand; "
        "run_mcp_subcommand(['list']); "
        "heavy = [m for m in sys.modules if m.startswith(("
        "'src.entrypoints.mcp_serve', 'src.server.agent_server', "
        "'src.entrypoints.tui_launcher'))]; "
        "print('HEAVY:' + ','.join(heavy))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert proc.returncode == 0, proc.stderr
    heavy_line = [l for l in proc.stdout.splitlines() if l.startswith("HEAVY:")][-1]
    assert heavy_line == "HEAVY:", f"lean contract broken: {heavy_line}"


def test_betas_default_at_cli_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """cli.main() sets the OpenClaude experimental-betas-off default
    (cli.tsx:44) without clobbering an explicit user value."""
    import src.cli as cli

    monkeypatch.delenv("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", raising=False)
    monkeypatch.setattr(sys, "argv", ["clawcodex", "--version"])
    cli.main()
    assert os.environ["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "true"

    monkeypatch.setenv("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "false")
    cli.main()
    assert os.environ["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "false"


def test_betas_default_at_agent_server_entry() -> None:
    """agent_server_cli is its own process entry (standalone backend) — the
    default is set at MODULE scope (mirroring cli.tsx:44's placement, before
    the heavy imports can capture env), so it must hold on a bare import
    with the var absent, and an explicit user value must survive."""
    repo_root = str(Path(__file__).resolve().parents[2])
    code = (
        "import os; import src.entrypoints.agent_server_cli; "
        "print(os.environ.get('CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS'))"
    )
    base_env = {k: v for k, v in os.environ.items()
                if k != "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"}

    p1 = subprocess.run([sys.executable, "-c", code], capture_output=True,
                        text=True, timeout=120, cwd=repo_root, env=base_env)
    assert p1.returncode == 0, p1.stderr
    assert p1.stdout.strip() == "true"

    p2 = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        timeout=120, cwd=repo_root,
        env={**base_env, "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "false"},
    )
    assert p2.returncode == 0, p2.stderr
    assert p2.stdout.strip() == "false"


def test_call_tool_out_of_workspace_read_denied(tmp_path: Path) -> None:
    """The serve posture composes with the ported read-permission semantics
    (check_read_permission_for_tool): in-workspace reads are silent, an
    out-of-workspace read raises the external-read ask — which fails closed
    with no permission handler. Found live by the stdio smoke."""
    import tempfile

    outside = Path(tempfile.mkdtemp())
    (outside / "secret.txt").write_text("x", encoding="utf-8")

    async def run() -> None:
        cm = await _session(tmp_path)()
        async with cm as s:
            r = await s.call_tool("Glob", {"pattern": "*.txt", "path": str(outside)})
            assert r.isError is True

    asyncio.run(run())


def test_reexposed_mcp_tools_integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full re-exposure chain (critic MAJOR-2): loader → registry
    registration incl. the evict-and-replace MCP-wins collision recovery →
    list_tools surfacing → dispatch. Re-exposed tools carry a content-less
    session allow rule (the user's configured servers are already their
    grant; the C7 approval gate filtered unapproved ones) — builtins stay
    fail-closed."""
    import src.entrypoints.mcp_serve as sm
    from src.tool_system.build_tool import build_tool
    from src.tool_system.protocol import ToolResult

    echo = build_tool(
        name="mcp__fake__echo",
        input_schema={
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
            "additionalProperties": False,
        },
        call=lambda i, c: ToolResult(name="mcp__fake__echo", output=f"echo:{i['msg']}"),
        prompt="Echo from the fake MCP server.",
        description="Echo tool.",
    )
    colliding = build_tool(
        name="Read",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        call=lambda i, c: ToolResult(name="Read", output="MCP-READ"),
        prompt="MCP Read replacement.",
        description="Shadowing Read.",
    )

    async def fake_loader():
        return [], [echo, colliding]

    monkeypatch.setattr(sm, "load_reexposed_mcp_tools", fake_loader)

    async def run() -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        server = await sm.build_server(tmp_path)  # default: uses the (fake) loader
        async with create_connected_server_and_client_session(server) as s:
            listing = await s.list_tools()
            names = [t.name for t in listing.tools]
            by_name = {t.name: t for t in listing.tools}
            # Surfaced, MCP-first ordering.
            assert "mcp__fake__echo" in names
            assert names.index("mcp__fake__echo") < names.index("Glob")
            # Evict-and-replace happened in the REAL registry: the builtin
            # Read's slot now serves the MCP tool's description and body.
            assert by_name["Read"].description.startswith("MCP Read replacement")

            r = await s.call_tool("mcp__fake__echo", {"msg": "hi"})
            assert r.isError is False and r.content[0].text == "echo:hi"

            r2 = await s.call_tool("Read", {"anything": 1})
            assert r2.isError is False and r2.content[0].text == "MCP-READ"

            # The grant covers ONLY re-exposed names — builtin asks still
            # fail closed.
            r3 = await s.call_tool("Write", {"file_path": str(tmp_path / "n"), "content": "x"})
            assert r3.isError is True
            assert not (tmp_path / "n").exists()

    asyncio.run(run())


def test_settings_deny_rule_beats_reexposure_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user who approved a server (C7) but denied one of its tools in
    settings must have the deny honored — deny rules precede allow rules,
    so the settings deny beats the re-exposure grant (critic follow-up)."""
    import src.entrypoints.mcp_serve as sm
    from src.tool_system.build_tool import build_tool
    from src.tool_system.protocol import ToolResult

    dangerous = build_tool(
        name="mcp__fake__dangerous",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        call=lambda i, c: ToolResult(name="mcp__fake__dangerous", output="RAN"),
        prompt="Should never run.",
        description="d",
    )
    benign = build_tool(
        name="mcp__fake__benign",
        input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        call=lambda i, c: ToolResult(name="mcp__fake__benign", output="OK"),
        prompt="Fine to run.",
        description="b",
    )

    async def fake_loader():
        return [], [dangerous, benign]

    monkeypatch.setattr(sm, "load_reexposed_mcp_tools", fake_loader)

    local_settings = tmp_path / "settings.local.json"
    local_settings.write_text(
        json.dumps({"permissions": {"deny": ["mcp__fake__dangerous"]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.permissions.settings_paths.default_setup_paths",
        lambda cwd=None: {
            "user_settings_path": None,
            "project_settings_path": None,
            "local_settings_path": str(local_settings),
            "managed_settings_path": None,
        },
    )

    async def run() -> None:
        from mcp.shared.memory import create_connected_server_and_client_session

        server = await sm.build_server(tmp_path)
        async with create_connected_server_and_client_session(server) as s:
            r_deny = await s.call_tool("mcp__fake__dangerous", {})
            assert r_deny.isError is True
            assert "RAN" not in (r_deny.content[0].text if r_deny.content else "")
            r_ok = await s.call_tool("mcp__fake__benign", {})
            assert r_ok.isError is False and r_ok.content[0].text == "OK"

    asyncio.run(run())
