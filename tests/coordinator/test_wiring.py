"""COORD-1 — coordinator-mode live-path wiring tests.

The `coordinator/` parity phase (my-docs/get-parity-by-folder/
coordinator-refactoring-plan.md) wired the already-ported
``src/coordinator/`` module into the live paths. This file covers the
wiring seams; the module-level behavior (gates, filters, user-context
shape) stays in ``test_mode.py`` / ``test_prompt.py``.

* W3/W4 — ``build_effective_system_prompt`` coordinator branch: the
  orchestration prompt REPLACES the base blocks (``utils/systemPrompt.ts:
  63-75``), style still appends, the trailing context block survives and
  carries ``# workerToolsContext`` (``QueryEngine.ts:300-306``).
* W2 — ``get_built_in_agents()`` swaps to the coordinator agent list
  (``builtInAgents.ts:35-43``) so ``subagent_type: "worker"`` resolves.
* W5 — ``coordinator_main_loop_registry``: non-mutating main-loop view
  (``toolPool.ts:35-41`` / ``main.tsx:1871-1879``); the full registry the
  Agent tool captured is untouched (worker-pool invariant,
  ``AgentTool.tsx:568-575``).
* W6 — Agent tool: model param ignored (``AgentTool.tsx:252``), spawns
  forced async (``AgentTool.tsx:562``), slim tool prompt
  (``prompt.ts:206-211``).
* W7 — agent-server ``_save_session`` stamps ``mode``; ``_do_resume``
  syncs via ``match_session_mode``, rebuilds the cached system prompt,
  re-emits ``system/init``, and returns the banner.

Env hygiene: ``is_coordinator_mode`` reads the env var live, and
``match_session_mode`` writes ``os.environ`` directly (outside
monkeypatch's tracking), so an autouse save/restore fixture guards both
vars — same pattern as ``test_mode.py``.
"""
from __future__ import annotations

import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.coordinator.mode import coordinator_main_loop_registry


@pytest.fixture(autouse=True)
def _coordinator_env_hygiene():
    saved = {}
    for var in ("CLAUDE_CODE_COORDINATOR_MODE", "CLAUDE_CODE_SIMPLE"):
        saved[var] = os.environ.pop(var, None)
    try:
        yield
    finally:
        for var, prev in saved.items():
            os.environ.pop(var, None)
            if prev is not None:
                os.environ[var] = prev


class _Client:
    """MCP-client stub — the builder only reads ``.name``."""

    def __init__(self, name: str) -> None:
        self.name = name


def _tool_context(tmp_path: Path, mcp_clients: list | None = None):
    """Duck-typed ToolContext: the builder reads cwd / workspace_root /
    (getattr) mcp_clients only."""
    return types.SimpleNamespace(
        cwd=str(tmp_path),
        workspace_root=str(tmp_path),
        mcp_clients=mcp_clients,
    )


# ---------------------------------------------------------------------------
# W3/W4 — build_effective_system_prompt coordinator branch
# ---------------------------------------------------------------------------


def test_prompt_branch_replaces_base_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    from src.query.agent_loop_compat import build_effective_system_prompt

    blocks = build_effective_system_prompt("", _tool_context(tmp_path))
    all_text = "\n".join(b["text"] for b in blocks)

    assert "You are a **coordinator**" in blocks[0]["text"]
    # The entire base prompt is REPLACED — none of its section headers leak.
    for marker in ("# Doing tasks", "# Executing actions", "# Tone"):
        assert marker not in all_text
    # Exactly ONE cache marker, on the last stable block (the coordinator
    # block here — style is empty), mirroring the scope-group convention.
    marked = [i for i, b in enumerate(blocks) if "cache_control" in b]
    assert marked == [0]
    assert blocks[0]["cache_control"]["type"] == "ephemeral"
    # Trailing context block survives, REQUEST-scoped (DeepSeek splitter).
    assert blocks[-1]["_cache_scope"] == "request"


def test_prompt_branch_style_appends_and_carries_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TS preserves appendSystemPrompt next to the coordinator prompt
    (``systemPrompt.ts:72-74``); style_prompt is this builder's
    append-channel. The single cache marker rides the LAST stable block."""
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    from src.query.agent_loop_compat import build_effective_system_prompt

    blocks = build_effective_system_prompt(
        "STYLE-SENTINEL", _tool_context(tmp_path)
    )
    assert blocks[1]["text"] == "STYLE-SENTINEL"
    marked = [i for i, b in enumerate(blocks) if "cache_control" in b]
    assert marked == [1]


def test_prompt_branch_worker_tools_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    from src.query.agent_loop_compat import build_effective_system_prompt

    blocks = build_effective_system_prompt(
        "", _tool_context(tmp_path, [_Client("github"), _Client("sentry")])
    )
    tail = blocks[-1]["text"]
    # Rendered with the prepend_user_context entry idiom (# key\nvalue).
    assert "# workerToolsContext" in tail
    assert "Workers spawned via the Agent tool have access to these tools:" in tail
    # MCP server-name line present iff clients were supplied.
    assert "MCP servers: github, sentry" in tail
    # Scratchpad line surfaces whenever the dir resolves (no Statsig port).
    assert "Scratchpad directory:" in tail


def test_prompt_branch_no_mcp_line_without_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    from src.query.agent_loop_compat import build_effective_system_prompt

    blocks = build_effective_system_prompt("", _tool_context(tmp_path, None))
    assert "MCP servers:" not in blocks[-1]["text"]


def test_prompt_branch_off_is_the_normal_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env off → the branch is inert: normal base sections, no coordinator
    artifacts."""
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    from src.query.agent_loop_compat import build_effective_system_prompt

    tc = _tool_context(tmp_path)
    blocks = build_effective_system_prompt("", tc)
    all_text = "\n".join(b["text"] for b in blocks)
    assert "You are a **coordinator**" not in all_text
    assert "workerToolsContext" not in all_text
    assert "# Doing tasks" in all_text


def test_prompt_branch_off_matches_reference_composition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env off → the builder is deep-equal to the pre-change composition
    (base blocks + trailing REQUEST-scope context block), pinning the
    now-shared trailing-block code against regressions. The env section
    embeds hh:mm:ss (the known second-boundary flake), so timestamps are
    normalized before comparing."""
    import re

    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    from src.command_system import get_skill_tool_commands
    from src.context_system import build_context_prompt
    from src.context_system.prompt_assembly import build_full_system_prompt_blocks
    from src.context_system.system_prompt_cache import CacheScope
    from src.query.agent_loop_compat import build_effective_system_prompt

    tc = _tool_context(tmp_path)
    cwd = str(tmp_path)
    try:
        skills = get_skill_tool_commands(cwd)
    except Exception:
        skills = None
    expected = build_full_system_prompt_blocks(
        cwd=cwd,
        output_style="default",
        append_system_prompt="STYLE-REF",
        query_source="main",
        provider=None,
        mcp_servers=None,
        skills=skills,
    )
    ctx = build_context_prompt(tc.workspace_root, cwd=tc.cwd)
    if ctx.strip():
        expected = expected + [{
            "type": "text",
            "text": ctx,
            "_cache_scope": CacheScope.REQUEST.value,
        }]

    actual = build_effective_system_prompt("STYLE-REF", tc)

    def _norm(blocks: list) -> list:
        return [
            {**b, "text": re.sub(r"\d{2}:\d{2}:\d{2}", "HH:MM:SS", b["text"])}
            for b in blocks
        ]

    assert _norm(actual) == _norm(expected)


# ---------------------------------------------------------------------------
# W2 — builtin-agents swap
# ---------------------------------------------------------------------------


def test_builtin_agents_swap_in_coordinator_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.agent.agent_definitions import get_built_in_agents

    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    types_on = [a.agent_type for a in get_built_in_agents()]
    assert types_on == ["worker", "general-purpose", "Explore", "Plan"]

    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE")
    types_off = [a.agent_type for a in get_built_in_agents()]
    assert "worker" not in types_off
    assert types_off[0] == "general-purpose"


def test_worker_resolves_through_overrides_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spawn path resolves defs via get_agent_definitions_with_overrides
    (agent.py) — the swap must be visible through that layering."""
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    from src.agent.agent_definitions import find_agent_by_type
    from src.agent.load_agents_dir import get_agent_definitions_with_overrides

    agents = get_agent_definitions_with_overrides(str(tmp_path))
    worker = find_agent_by_type(agents, "worker")
    assert worker is not None
    assert worker.agent_type == "worker"


# ---------------------------------------------------------------------------
# W5 — non-mutating main-loop registry view
# ---------------------------------------------------------------------------


class _StubTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.aliases: list[str] = []


def _registry_with(*names: str):
    from src.tool_system.registry import ToolRegistry

    reg = ToolRegistry()
    for n in names:
        reg.register(_StubTool(n))
    return reg


def test_view_is_identity_when_mode_off() -> None:
    reg = _registry_with("Agent", "Read")
    assert coordinator_main_loop_registry(reg) is reg


def test_view_filters_without_mutating_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    reg = _registry_with(
        "Agent", "SendMessage", "TaskStop", "StructuredOutput",
        "Read", "Edit", "Bash",
        "mcp__gh__subscribe_pr_activity", "mcp__gh__create_pr",
    )
    view = coordinator_main_loop_registry(reg)
    assert sorted(t.name for t in view.list_tools()) == [
        "Agent", "SendMessage", "StructuredOutput", "TaskStop",
        "mcp__gh__subscribe_pr_activity",
    ]
    # Worker-pool invariant: the source registry (what the Agent tool
    # captured) still has everything.
    assert len(reg.list_tools()) == 9
    assert reg.get("Read") is not None
    # Shared tool objects, not copies.
    assert view.get("Agent") is reg.get("Agent")


def test_view_bakes_in_disabled_mcp_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The view builds from list_tools(), so disabled-MCP-server hiding is
    inherited by construction."""
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    reg = _registry_with("Agent", "mcp__gh__subscribe_pr_activity")
    reg.disabled_servers.add("gh")
    view = coordinator_main_loop_registry(reg)
    assert [t.name for t in view.list_tools()] == ["Agent"]


# ---------------------------------------------------------------------------
# W6 — Agent tool coordinator behaviors
# ---------------------------------------------------------------------------


def test_agent_tool_prompt_slim_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coordinator gets the shared header only (``prompt.ts:206-211``) —
    the coordinator system prompt already carries usage guidance. Exercised
    through the live tool's prompt() so the _agent_prompt wiring is covered."""
    from src.tool_system.defaults import build_default_registry

    registry = build_default_registry(provider=object())
    agent_tool = registry.get("Agent")
    assert agent_tool is not None

    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    slim = agent_tool.prompt()
    assert "Available agent types" in slim
    for dropped in ("When NOT to use", "Usage notes:", "Example usage:",
                    "## Writing the prompt"):
        assert dropped not in slim

    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE")
    full = agent_tool.prompt()
    assert "When NOT to use" in full
    assert "Usage notes:" in full


def test_coordinator_forces_async_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All coordinator spawns run async (``AgentTool.tsx:562``) so results
    arrive as <task-notification> messages — run_in_background omitted."""
    from src.tool_system.defaults import build_default_registry
    from src.tool_system.protocol import ToolCall
    from src.tool_system.context import ToolContext
    from src.types.content_blocks import TextBlock
    from src.types.messages import AssistantMessage

    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    registry = build_default_registry(provider=object())
    context = ToolContext(workspace_root=tmp_path)

    async def _fake_run_agent(_params):
        yield AssistantMessage(content=[TextBlock(text="done")])

    with patch("src.tool_system.tools.agent.run_agent", _fake_run_agent):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={"description": "t", "prompt": "do work"},
            ),
            context,
        )
    assert result.is_error is False
    assert result.output.get("status") == "async_launched"


def test_coordinator_suppresses_model_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The coordinator's model param is discarded (``AgentTool.tsx:252``) —
    workers need the default model for substantive tasks."""
    from src.tool_system.defaults import build_default_registry
    from src.tool_system.protocol import ToolCall
    from src.tool_system.context import ToolContext
    from src.types.content_blocks import TextBlock
    from src.types.messages import AssistantMessage

    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    registry = build_default_registry(provider=object())
    context = ToolContext(workspace_root=tmp_path)
    seen_models: list = []

    async def _capturing_run_agent(params):
        seen_models.append(getattr(params, "model", "<missing>"))
        yield AssistantMessage(content=[TextBlock(text="done")])

    import time as _time

    with patch("src.tool_system.tools.agent.run_agent", _capturing_run_agent):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "t",
                    "prompt": "do work",
                    "model": "haiku",
                },
            ),
            context,
        )
        assert result.output.get("status") == "async_launched"
        # The async worker thread invokes run_agent shortly after launch.
        deadline = _time.time() + 5.0
        while not seen_models and _time.time() < deadline:
            _time.sleep(0.02)
    assert seen_models, "async worker never invoked run_agent"
    assert seen_models[0] is None


def test_model_param_honored_when_mode_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.tool_system.defaults import build_default_registry
    from src.tool_system.protocol import ToolCall
    from src.tool_system.context import ToolContext
    from src.types.content_blocks import TextBlock
    from src.types.messages import AssistantMessage

    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    registry = build_default_registry(provider=object())
    context = ToolContext(workspace_root=tmp_path)
    seen_models: list = []

    async def _capturing_run_agent(params):
        seen_models.append(getattr(params, "model", "<missing>"))
        yield AssistantMessage(content=[TextBlock(text="done")])

    with patch("src.tool_system.tools.agent.run_agent", _capturing_run_agent):
        result = registry.dispatch(
            ToolCall(
                name="Agent",
                input={
                    "description": "t",
                    "prompt": "do work",
                    "model": "haiku",
                },
            ),
            context,
        )
    # Sync path (no run_in_background, mode off) → run_agent already ran.
    assert result.output.get("status") != "async_launched"
    assert seen_models and seen_models[0] == "haiku"


# ---------------------------------------------------------------------------
# W7 + W5 consumption sites — agent-server harness
# ---------------------------------------------------------------------------


class _ServerHarness(unittest.TestCase):
    """Real ``_AgentSession`` against the keyless ``ollama`` provider with
    the global config redirected to a temp dir — the
    ``tests/test_ch03_state_round4.py`` pattern."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.ws = root / "ws"
        self.ws.mkdir()
        self.config_dir = root / "config-home"
        self.config_dir.mkdir()
        self.global_path = self.config_dir / "config.json"
        self.global_path.write_text(json.dumps({}), encoding="utf-8")
        self.sessions_dir = root / "sessions"
        self.sessions_dir.mkdir()
        self._patches = [
            patch("src.config.get_global_config_path", return_value=self.global_path),
            patch("src.config.GLOBAL_CONFIG_DIR", str(self.config_dir)),
        ]
        for p in self._patches:
            p.start()
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        from src.settings.settings import invalidate_settings_cache

        invalidate_settings_cache()
        self._tmp.cleanup()

    def _build(self, *, single_session: bool = True):
        from src.server.agent_server import (
            AgentServerConfig,
            _AgentSession,
            _build_runtime,
        )

        sess = _AgentSession(
            session_id="s-coord",
            cwd=str(self.ws),
            config=AgentServerConfig(
                provider_name="ollama",
                model=None,
                single_session=single_session,
            ),
            loop=MagicMock(),
            out_queue=MagicMock(),
        )
        _build_runtime(sess, None)
        self.assertIsNone(sess.init_error, f"runtime build failed: {sess.init_error}")
        return sess

    def _emitted(self, sess) -> list[dict]:
        """Messages pushed by _emit. With a MagicMock loop, _emit's
        ``loop.call_soon_threadsafe(out_queue.put_nowait, msg)`` never runs
        the callback — the message is the second positional arg captured on
        the mock."""
        calls = []
        for c in sess.loop.call_soon_threadsafe.call_args_list:
            if len(c.args) >= 2:
                calls.append(c.args[1])
        return [m for m in calls if isinstance(m, dict)]

    def _seed_conversation(self, sess) -> None:
        from src.agent.conversation import Conversation

        sess.session.conversation = Conversation.from_dict(
            {"messages": [{"role": "user", "content": "hi"}]},
        )


class TestServerModePersistResume(_ServerHarness):
    def test_save_session_stamps_normal_mode(self) -> None:
        sess = self._build()
        self._seed_conversation(sess)
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir):
            sess._save_session()
        payload = json.loads((self.sessions_dir / "s-coord.json").read_text(encoding="utf-8"))
        self.assertEqual(payload.get("mode"), "normal")

    def test_save_session_stamps_coordinator_mode(self) -> None:
        sess = self._build()
        self._seed_conversation(sess)
        os.environ["CLAUDE_CODE_COORDINATOR_MODE"] = "1"
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir):
            sess._save_session()
        payload = json.loads((self.sessions_dir / "s-coord.json").read_text(encoding="utf-8"))
        self.assertEqual(payload.get("mode"), "coordinator")

    def _write_session_file(self, mode: object) -> None:
        payload: dict = {
            "session_id": "saved-1",
            "model": "",
            "provider": "ollama",
            "cwd": str(self.ws),
            "message_count": 1,
            "preview": "hi",
            "conversation": {"messages": [{"role": "user", "content": "hi"}]},
        }
        if mode is not None:
            payload["mode"] = mode
        (self.sessions_dir / "saved-1.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def _resume_reply(self, sess) -> dict:
        for m in self._emitted(sess):
            if m.get("type") == "control_response":
                resp = m.get("response", {})
                if resp.get("request_id") == "rq-1":
                    return resp.get("response", {})
        self.fail("no control_response for rq-1 captured")

    def test_resume_enters_coordinator_and_refreshes(self) -> None:
        sess = self._build()
        self._write_session_file("coordinator")
        self.assertNotIn("CLAUDE_CODE_COORDINATOR_MODE", os.environ)
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir):
            sess._do_resume("rq-1", "saved-1")

        # Env flipped (matchSessionMode) + banner surfaced in the reply.
        from src.coordinator.mode import is_coordinator_mode

        self.assertTrue(is_coordinator_mode())
        reply = self._resume_reply(sess)
        self.assertTrue(reply.get("ok"))
        self.assertEqual(
            reply.get("mode_banner"),
            "Entered coordinator mode to match resumed session.",
        )
        # Cached system prompt rebuilt to the coordinator prompt.
        base = sess._base_system_prompt
        first_text = base[0]["text"] if isinstance(base, list) else str(base)
        self.assertIn("You are a **coordinator**", first_text)
        # system/init re-emitted with the narrowed tool list.
        inits = [
            m for m in self._emitted(sess)
            if m.get("type") == "system" and m.get("subtype") == "init"
        ]
        self.assertTrue(inits, "init was not re-emitted after the mode flip")
        names = {t["name"] for t in inits[-1]["tools"]}
        self.assertEqual(
            names, {"Agent", "SendMessage", "TaskStop"}
        )

    def test_resume_without_mode_field_is_noop(self) -> None:
        sess = self._build()
        self._write_session_file(None)
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir):
            sess._do_resume("rq-1", "saved-1")
        from src.coordinator.mode import is_coordinator_mode

        self.assertFalse(is_coordinator_mode())
        reply = self._resume_reply(sess)
        self.assertTrue(reply.get("ok"))
        self.assertNotIn("mode_banner", reply)

    def test_resume_with_junk_mode_is_noop(self) -> None:
        sess = self._build()
        self._write_session_file("bogus-value")
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir):
            sess._do_resume("rq-1", "saved-1")
        from src.coordinator.mode import is_coordinator_mode

        self.assertFalse(is_coordinator_mode())
        reply = self._resume_reply(sess)
        self.assertNotIn("mode_banner", reply)

    def test_resume_never_flips_mode_on_multi_session_server(self) -> None:
        """The env flip is process-global; on a multi-session (--http)
        server one session's resume must not flip the mode — and thereby
        the prompt + tool set — of every sibling session. Same
        single_session discipline as cost-restore."""
        sess = self._build(single_session=False)
        self._write_session_file("coordinator")
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir):
            sess._do_resume("rq-1", "saved-1")
        from src.coordinator.mode import is_coordinator_mode

        self.assertFalse(is_coordinator_mode())
        reply = self._resume_reply(sess)
        self.assertTrue(reply.get("ok"))  # the resume itself still works
        self.assertNotIn("mode_banner", reply)

    def test_resume_exits_coordinator_for_normal_session(self) -> None:
        sess = self._build()
        self._write_session_file("normal")
        os.environ["CLAUDE_CODE_COORDINATOR_MODE"] = "1"
        with patch("src.server.agent_server._sessions_dir", return_value=self.sessions_dir):
            sess._do_resume("rq-1", "saved-1")
        from src.coordinator.mode import is_coordinator_mode

        self.assertFalse(is_coordinator_mode())
        reply = self._resume_reply(sess)
        self.assertEqual(
            reply.get("mode_banner"),
            "Exited coordinator mode to match resumed session.",
        )


class TestServerMainLoopToolFilter(_ServerHarness):
    """W5 consumption-site coverage: the MCP-ordering risk lives at the
    sites, not the helper — fake MCP tools are registered on the FULL
    registry (as the runtime does) and must be filtered in the views."""

    def _register_fake_mcp_tools(self, sess) -> None:
        sess.tool_registry.register(_StubTool("mcp__x__subscribe_pr_activity"))
        sess.tool_registry.register(_StubTool("mcp__x__other"))

    def test_emit_init_lists_filtered_tools_in_coordinator_mode(self) -> None:
        sess = self._build()
        self._register_fake_mcp_tools(sess)
        os.environ["CLAUDE_CODE_COORDINATOR_MODE"] = "1"
        sess.emit_init()
        inits = [
            m for m in self._emitted(sess)
            if m.get("type") == "system" and m.get("subtype") == "init"
        ]
        names = {t["name"] for t in inits[-1]["tools"]}
        self.assertEqual(
            names,
            {
                "Agent", "SendMessage", "TaskStop",
                "mcp__x__subscribe_pr_activity",
            },
        )
        # The full registry (what the Agent tool captured) is untouched.
        full = {t.name for t in sess.tool_registry.list_tools()}
        self.assertIn("Read", full)
        self.assertIn("mcp__x__other", full)

    def test_emit_init_lists_full_tools_when_mode_off(self) -> None:
        sess = self._build()
        self._register_fake_mcp_tools(sess)
        sess.emit_init()
        inits = [
            m for m in self._emitted(sess)
            if m.get("type") == "system" and m.get("subtype") == "init"
        ]
        names = {t["name"] for t in inits[-1]["tools"]}
        self.assertIn("Read", names)
        self.assertIn("mcp__x__other", names)


# ---------------------------------------------------------------------------
# Headless init-event filtering (the :287 analog of server emit_init)
# ---------------------------------------------------------------------------


def test_coordinator_turn_rejects_filtered_out_tool_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The advertised-list narrowing is enforced at execution: the main
    loop dispatches through the filtered view, so a tool_use of a
    filtered-out tool errors — while the full registry (the worker-side
    pool) still dispatches it."""
    from src.tool_system.context import ToolContext
    from src.tool_system.defaults import build_default_registry
    from src.tool_system.protocol import ToolCall

    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    reg = build_default_registry(provider=object())
    view = coordinator_main_loop_registry(reg)

    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    call = ToolCall(name="Read", input={"file_path": str(target)})

    rejected = view.dispatch(call, ToolContext(workspace_root=tmp_path))
    assert rejected.is_error is True
    assert "unknown tool" in str(rejected.output)

    allowed = reg.dispatch(call, ToolContext(workspace_root=tmp_path))
    assert allowed.is_error is not True


def test_headless_init_event_names_are_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stream-json init event lists what the main loop actually gets —
    exercised at the same seam headless uses (view over the built registry)."""
    from src.tool_system.defaults import build_default_registry

    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")
    registry = build_default_registry(provider=object())
    names = {
        t.name for t in coordinator_main_loop_registry(registry).list_tools()
    }
    # C6: StructuredOutput is no longer a static tool (TS specialTools), so the
    # coordinator keep-if-in-allowset filter now yields the 3 real tools.
    assert names == {"Agent", "SendMessage", "TaskStop"}
