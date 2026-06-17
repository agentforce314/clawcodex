"""Per-tool permission parity with TS: don't over-prompt for safe tools.

The Python port only defined ``check_permissions`` for the mutating/network
tools; every other tool fell through to the build_tool default
(``PermissionPassthroughResult``) which the flow turns into ``ask`` — so ~20
safe/interactive/bookkeeping tools prompted in ``default`` mode (and were
wrongly denied as "unknown tool" in ``auto`` mode). TS gives each such tool an
``allow`` (or, for filesystem readers, a path-based read check). These tests pin
the ported behavior: safe tools never prompt, genuinely-gated tools still do,
and configured rules still win.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.permissions.check import NO_PERMISSION_TOOLS, has_permissions_to_use_tool
from src.permissions.types import ToolPermissionContext
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry

# Tools that MUST keep prompting (necessarily gated).
_GATED = {
    "Edit", "Write", "MultiEdit", "NotebookEdit",  # file mutation
    "Bash",                                          # code execution
    "WebFetch",                                      # arbitrary network egress
    "Skill",                                         # runs embedded shell / skill cmds
    "MCP", "ListMcpResourcesTool", "ReadMcpResourceTool",  # MCP boundary
    "EnterPlanMode", "ExitPlanMode",                 # plan-mode meta
}


def _ctx(mode: str, ws: Path) -> ToolContext:
    return ToolContext(
        workspace_root=ws, permission_context=ToolPermissionContext(mode=mode)
    )


def _behavior(reg, name, tool_input, ctx) -> str:
    tool = reg.get(name)
    return has_permissions_to_use_tool(
        tool, tool_input, ctx.permission_context, tool_use_context=ctx
    ).behavior


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.ws = Path(tempfile.mkdtemp()).resolve()
        (self.ws / "a.txt").write_text("hi\n")
        self.reg = build_default_registry(include_user_tools=False)


class TestSafeToolsAllow(_Base):
    def test_no_permission_tools_allow_in_default(self) -> None:
        ctx = _ctx("default", self.ws)
        for name in sorted(NO_PERMISSION_TOOLS):
            if self.reg.get(name) is None:
                continue  # tool not registered in this build
            self.assertEqual(
                _behavior(self.reg, name, {}, ctx), "allow",
                f"{name} should auto-allow in default mode",
            )

    def test_no_permission_tools_allow_in_auto(self) -> None:
        # auto mode previously denied these as "unknown tool"; now allow.
        ctx = _ctx("auto", self.ws)
        for name in sorted(NO_PERMISSION_TOOLS):
            if self.reg.get(name) is None:
                continue
            self.assertEqual(
                _behavior(self.reg, name, {}, ctx), "allow",
                f"{name} should auto-allow in auto mode",
            )

    def test_ask_user_question_does_not_prompt(self) -> None:
        ctx = _ctx("default", self.ws)
        self.assertEqual(
            _behavior(self.reg, "AskUserQuestion", {"questions": [{"question": "q?"}]}, ctx),
            "allow",
        )


class TestGatedToolsStillAsk(_Base):
    def test_gated_tools_ask_in_default(self) -> None:
        ctx = _ctx("default", self.ws)
        inputs = {
            "Edit": {"file_path": str(self.ws / "a.txt"), "old_string": "hi", "new_string": "yo"},
            "Write": {"file_path": str(self.ws / "b.txt"), "content": "x"},
            "NotebookEdit": {"notebook_path": str(self.ws / "n.ipynb"), "new_source": "x"},
            "Bash": {"command": "echo hi"},
            "WebFetch": {"url": "https://example.com", "prompt": "x"},
            "Skill": {"skill": "some-skill"},
            "MCP": {"server": "s", "tool": "t"},
            "ListMcpResourcesTool": {},
            "ReadMcpResourceTool": {"server": "s", "uri": "u"},
            "EnterPlanMode": {},
            "ExitPlanMode": {},
        }
        for name, inp in inputs.items():
            if self.reg.get(name) is None:
                continue
            self.assertEqual(
                _behavior(self.reg, name, inp, ctx), "ask",
                f"{name} must remain gated (ask) in default mode",
            )

    def test_gated_tools_not_in_allow_set(self) -> None:
        # Guard against accidentally adding a gated tool to the allow-set.
        for name in _GATED:
            self.assertNotIn(name, NO_PERMISSION_TOOLS)


class TestSearchToolsPathBased(_Base):
    def test_glob_grep_cwd_allow(self) -> None:
        ctx = _ctx("default", self.ws)
        self.assertEqual(_behavior(self.reg, "Glob", {"pattern": "*.py"}, ctx), "allow")
        self.assertEqual(_behavior(self.reg, "Grep", {"pattern": "x"}, ctx), "allow")

    def test_glob_grep_in_workspace_path_allow(self) -> None:
        ctx = _ctx("default", self.ws)
        self.assertEqual(
            _behavior(self.reg, "Grep", {"pattern": "x", "path": str(self.ws)}, ctx), "allow"
        )

    def test_glob_grep_outside_workspace_ask(self) -> None:
        ctx = _ctx("default", self.ws)
        self.assertEqual(
            _behavior(self.reg, "Glob", {"pattern": "*", "path": "/etc"}, ctx), "ask"
        )
        self.assertEqual(
            _behavior(self.reg, "Grep", {"pattern": "x", "path": "/etc"}, ctx), "ask"
        )

    def test_relative_path_resolved_against_context_cwd(self) -> None:
        # A relative path must resolve against context.cwd (the executor's base),
        # not the process cwd — so an in-workspace relative dir still allows.
        (self.ws / "sub").mkdir()
        ctx = _ctx("default", self.ws)
        self.assertEqual(_behavior(self.reg, "Grep", {"pattern": "x", "path": "."}, ctx), "allow")
        self.assertEqual(_behavior(self.reg, "Glob", {"pattern": "*", "path": "sub"}, ctx), "allow")


class TestSendMessageGate(_Base):
    def test_local_recipient_allows(self) -> None:
        ctx = _ctx("default", self.ws)
        self.assertEqual(
            _behavior(self.reg, "SendMessage", {"to": "teammate", "message": "hi"}, ctx),
            "allow",
        )
        self.assertEqual(
            _behavior(self.reg, "SendMessage", {"to": "*", "message": "hi"}, ctx), "allow"
        )

    def test_cross_machine_recipient_asks(self) -> None:
        # bridge:/uds: recipients are a cross-trust boundary → must prompt,
        # even though those transports are stubs today.
        ctx = _ctx("default", self.ws)
        self.assertEqual(
            _behavior(self.reg, "SendMessage", {"to": "bridge:abc", "message": "hi"}, ctx),
            "ask",
        )
        self.assertEqual(
            _behavior(self.reg, "SendMessage", {"to": "uds:/tmp/s.sock", "message": "hi"}, ctx),
            "ask",
        )

    def test_cross_machine_ask_is_bypass_immune(self) -> None:
        # The safetyCheck ask is non-classifier-approvable, so even auto mode
        # surfaces it rather than auto-allowing.
        ctx = _ctx("auto", self.ws)
        self.assertEqual(
            _behavior(self.reg, "SendMessage", {"to": "bridge:abc", "message": "hi"}, ctx),
            "ask",
        )

    def test_skill_is_gated(self) -> None:
        # Skill runs embedded shell / declared tools — the invocation stays gated.
        ctx = _ctx("default", self.ws)
        self.assertEqual(_behavior(self.reg, "Skill", {"skill": "some-skill"}, ctx), "ask")


class TestConfigInputDependent(_Base):
    def test_config_read_allows(self) -> None:
        ctx = _ctx("default", self.ws)
        self.assertEqual(_behavior(self.reg, "Config", {"setting": "theme"}, ctx), "allow")

    def test_config_write_asks(self) -> None:
        ctx = _ctx("default", self.ws)
        self.assertEqual(
            _behavior(self.reg, "Config", {"setting": "theme", "value": "dark"}, ctx), "ask"
        )


class TestRulesStillWin(_Base):
    def test_deny_rule_beats_central_allow(self) -> None:
        # A configured deny rule for an otherwise-allowed tool must still deny —
        # the central allow is gated on passthrough and runs after rule checks.
        pc = ToolPermissionContext.from_iterables(deny_names=["TodoWrite"])
        ctx = ToolContext(workspace_root=self.ws, permission_context=pc)
        self.assertEqual(_behavior(self.reg, "TodoWrite", {}, ctx), "deny")


if __name__ == "__main__":
    unittest.main()
