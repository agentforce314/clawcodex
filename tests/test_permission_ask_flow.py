"""C1 end-to-end tests: ask → multi-option reply → apply + persist → reload.

Covers the previously-severed last mile (registry adapter / tool_input /
suggestions) and the read side (rules loaded at startup). The restart
round-trip is the test that would have caught the persist-into-a-void bug
the plan critic flagged.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.permissions.check import has_permissions_to_use_tool
from src.permissions.settings_paths import (
    default_setup_paths,
    settings_path_for_destination,
)
from src.permissions.setup import setup_permissions
from src.permissions.types import (
    PermissionAskDecision,
    PermissionAskReply,
    PermissionPassthroughResult,
    ToolPermissionContext,
)
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolCall, ToolResult
from src.tool_system.registry import ToolRegistry


def _make_ask_tool(name: str = "Bash"):
    return build_tool(
        name=name,
        description="test tool",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        call=lambda tool_input, context: ToolResult(name=name, output={"ok": True}),
        check_permissions=lambda tool_input, context: PermissionPassthroughResult(),
    )


class TestSettingsPaths(unittest.TestCase):
    def test_destination_mapping(self) -> None:
        # Project tier is .clawcodex/ — NOT .claude/, which the real
        # Claude Code harness owns (cross-tool interference otherwise).
        self.assertEqual(
            settings_path_for_destination("localSettings", "/tmp/p"),
            "/tmp/p/.clawcodex/settings.local.json",
        )
        self.assertEqual(
            settings_path_for_destination("projectSettings", "/tmp/p"),
            "/tmp/p/.clawcodex/settings.json",
        )
        # The live user_settings_path() is conftest-isolated in tests
        # (_isolate_user_permission_settings), so lock the canonical user
        # tier via the constant it derives from.
        import os

        from src.permissions import settings_paths as sp_mod

        self.assertEqual(
            sp_mod.USER_SETTINGS_FILENAME,
            os.path.join("~", ".clawcodex", "settings.json"),
        )
        self.assertIsNone(settings_path_for_destination("session"))
        self.assertIsNone(settings_path_for_destination("cliArg"))

    def test_default_setup_paths_keys(self) -> None:
        paths = default_setup_paths("/tmp/p")
        self.assertEqual(
            set(paths),
            {
                "user_settings_path",
                "project_settings_path",
                "local_settings_path",
                "managed_settings_path",
            },
        )
        self.assertEqual(
            paths["local_settings_path"], "/tmp/p/.clawcodex/settings.local.json"
        )


class TestRegistryAskFlow(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.registry = ToolRegistry([_make_ask_tool()])
        self.ctx = ToolContext(
            workspace_root=self.root,
            permission_context=ToolPermissionContext(mode="default"),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _local_settings(self) -> Path:
        return self.root / ".clawcodex" / "settings.local.json"

    def _dispatch(self, command: str):
        return self.registry.dispatch(
            ToolCall(name="Bash", input={"command": command}), self.ctx
        )

    def test_request_carries_tool_input_and_suggestions(self) -> None:
        captured = {}

        def handler(request):
            captured["request"] = request
            return PermissionAskReply(behavior="allow")

        self.ctx.permission_handler = handler
        result = self._dispatch("git diff --stat")
        self.assertFalse(result.is_error)
        request = captured["request"]
        self.assertEqual(request.tool_name, "Bash")
        self.assertEqual(request.tool_input, {"command": "git diff --stat"})
        self.assertTrue(request.suggestions)
        rule = request.suggestions[0].rules[0]
        self.assertEqual(rule.rule_content, "git diff:*")

    def test_allow_once_does_not_persist(self) -> None:
        self.ctx.permission_handler = lambda request: PermissionAskReply(
            behavior="allow"
        )
        result = self._dispatch("git diff --stat")
        self.assertFalse(result.is_error)
        self.assertFalse(self._local_settings().exists())
        # And the in-memory context gained no rules either.
        self.assertEqual(self.ctx.permission_context.always_allow_rules, {})

    def test_allow_always_applies_in_memory_and_persists(self) -> None:
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return PermissionAskReply(
                behavior="allow", chosen_updates=tuple(request.suggestions)
            )

        self.ctx.permission_handler = handler

        first = self._dispatch("git diff --stat")
        self.assertFalse(first.is_error)
        self.assertEqual(calls["n"], 1)

        # Persisted: rule string under permissions.allow in localSettings.
        settings = json.loads(self._local_settings().read_text())
        self.assertIn("Bash(git diff:*)", settings["permissions"]["allow"])

        # In-memory: a second matching call must NOT re-prompt.
        second = self._dispatch("git diff HEAD~1")
        self.assertFalse(second.is_error)
        self.assertEqual(calls["n"], 1, "rule should auto-allow without re-asking")

    def test_deny_feedback_reaches_tool_error(self) -> None:
        self.ctx.permission_handler = lambda request: PermissionAskReply(
            behavior="deny", message="use git log instead"
        )
        result = self._dispatch("git diff --stat")
        self.assertTrue(result.is_error)
        self.assertIn("use git log instead", result.output["error"])

    def test_restart_round_trip_rule_auto_allows(self) -> None:
        """Persist via "always" → rebuild context via the STARTUP loader →
        the rule auto-allows with no handler involved at all."""

        self.ctx.permission_handler = lambda request: PermissionAskReply(
            behavior="allow", chosen_updates=tuple(request.suggestions)
        )
        self.assertFalse(self._dispatch("git diff --stat").is_error)
        self.assertTrue(self._local_settings().exists())

        # "Restart": fresh context built exactly like the entrypoints do.
        setup = setup_permissions(
            cwd=str(self.root),
            mode="default",
            user_settings_path=str(self.root / "nonexistent-user-settings.json"),
            project_settings_path=str(self.root / ".clawcodex" / "settings.json"),
            local_settings_path=str(self._local_settings()),
        )
        tool = _make_ask_tool()
        decision = has_permissions_to_use_tool(
            tool, {"command": "git diff --cached"}, setup.context
        )
        self.assertEqual(decision.behavior, "allow")

        # A non-matching command still asks.
        other = has_permissions_to_use_tool(
            tool, {"command": "rm -r build"}, setup.context
        )
        self.assertEqual(other.behavior, "ask")


class TestHeadlessStartupLoadsPersistedRules(unittest.TestCase):
    """Executes the PRODUCTION headless setup block (not an inline copy):
    a rule persisted in the workspace's local settings must be live in the
    tool_context that run_headless constructs (review-A finding 5b)."""

    def test_headless_setup_block_loads_rules(self) -> None:
        import io
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from src.entrypoints import headless as headless_mod
        from src.entrypoints.headless import HeadlessOptions, run_headless
        from src.providers.base import ChatResponse

        class _FakeProvider:
            def __init__(self, api_key, base_url=None, model=None):
                self.model = model or "fake"

            def chat(self, messages, tools=None, **kw):
                return ChatResponse(
                    content="ok",
                    model="fake",
                    usage={"input_tokens": 1, "output_tokens": 1},
                    finish_reason="end_turn",
                    tool_uses=None,
                )

        class _FakeRegistry:
            def list_tools(self):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            local = root / ".clawcodex" / "settings.local.json"
            local.parent.mkdir(parents=True)
            local.write_text(
                json.dumps({"permissions": {"allow": ["Bash(git diff:*)"]}})
            )

            captured: dict = {}
            original = headless_mod.run_query_as_agent_loop

            async def _capture(*args, **kw):
                captured["tool_context"] = kw["tool_context"]
                return await original(*args, **kw)

            with patch.object(
                headless_mod, "get_provider_class", lambda n: _FakeProvider
            ), patch.object(
                headless_mod,
                "get_provider_config",
                lambda n: {"api_key": "x", "default_model": "fake"},
            ), patch.object(
                headless_mod, "get_default_provider", lambda: "anthropic"
            ), patch.object(
                headless_mod,
                "build_default_registry",
                lambda provider=None: _FakeRegistry(),
            ), patch.object(
                headless_mod, "run_query_as_agent_loop", _capture
            ):
                code = run_headless(
                    HeadlessOptions(
                        prompt="hi",
                        output_format="text",
                        stdout=io.StringIO(),
                        stderr=io.StringIO(),
                        workspace_root=root,
                    )
                )
            self.assertEqual(code, 0)
            ctx = captured["tool_context"]
            self.assertIn(
                "Bash(git diff:*)",
                ctx.permission_context.always_allow_rules.get("localSettings", []),
            )


class TestHeadlessNoHandlerStillDenies(unittest.TestCase):
    def test_ask_without_handler_denies(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            registry = ToolRegistry([_make_ask_tool()])
            ctx = ToolContext(
                workspace_root=Path(tmp.name),
                permission_context=ToolPermissionContext(mode="default"),
            )
            ctx.permission_handler = None
            result = registry.dispatch(
                ToolCall(name="Bash", input={"command": "git diff"}), ctx
            )
            self.assertTrue(result.is_error)
        finally:
            tmp.cleanup()


class TestSafetyAsksCarryNoSuggestions(unittest.TestCase):
    def test_dangerous_command_ask_has_no_suggestions(self) -> None:
        from src.permissions.check import has_permissions_to_use_tool
        from src.tool_system.tools.bash.bash_tool import _bash_check_permissions

        tool = build_tool(
            name="Bash",
            description="bash",
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
            },
            call=lambda tool_input, context: ToolResult(name="Bash", output={"ok": True}),
            check_permissions=_bash_check_permissions,
        )
        tmp = tempfile.TemporaryDirectory()
        try:
            ctx = ToolContext(
                workspace_root=Path(tmp.name),
                permission_context=ToolPermissionContext(mode="default"),
            )
            decision = has_permissions_to_use_tool(
                tool,
                {"command": "rm -rf /tmp/whatever"},
                ctx.permission_context,
                tool_use_context=ctx,
            )
            self.assertEqual(decision.behavior, "ask")
            self.assertFalse(
                decision.suggestions,
                "safety-flagged asks must not suggest saving the command",
            )
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
