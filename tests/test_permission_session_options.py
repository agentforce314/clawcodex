"""Tests for the per-tool "allow for the whole session" permission option.

Covers the parity work that upgrades the middle permission choice from a
binary "Allow always" (Bash-only "don't ask again") to the original
per-tool option set:

* :func:`src.permissions.updates.default_session_suggestions` — the
  ``PermissionUpdate`` list each tool category contributes (Bash prefix
  rule, file-edit ``setMode:acceptEdits`` + out-of-roots ``addDirectories``,
  read content-less rule, other-tool content-less rule).
* :func:`src.permissions.updates.session_option_label` — the human label.
* The ``acceptEdits``-mode auto-allow added to
  :func:`src.permissions.check.has_permissions_to_use_tool` — the half that
  makes the file-edit session option actually suppress later prompts.
* :meth:`src.tool_system.context.ToolContext.allowed_roots` folding in the
  session-granted directories.
* End-to-end (registry-driven): ask → "always" → apply → a later matching
  call is not re-prompted.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.permissions.check import has_permissions_to_use_tool
from src.permissions.types import (
    AdditionalWorkingDirectory,
    ModeDecisionReason,
    PermissionAskDecision,
    PermissionAskReply,
    PermissionPassthroughResult,
    PermissionResult,
    PermissionRuleValue,
    PermissionUpdateAddDirectories,
    PermissionUpdateAddRules,
    PermissionUpdateSetMode,
    SafetyCheckDecisionReason,
    ToolPermissionContext,
)
from src.permissions.updates import (
    default_session_suggestions,
    session_option_label,
)
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolCall, ToolResult
from src.tool_system.registry import ToolRegistry


# --------------------------------------------------------------------------
# Lightweight test doubles
# --------------------------------------------------------------------------
class _MockTool:
    def __init__(
        self, name: str = "Write", perm_result: PermissionResult | None = None
    ) -> None:
        self._name = name
        self._perm_result = perm_result

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_mcp(self) -> bool:
        return False

    def check_permissions(
        self, tool_input: dict[str, Any], context: Any
    ) -> PermissionResult:
        if self._perm_result is not None:
            return self._perm_result
        return PermissionPassthroughResult()


class _FakeToolUseContext:
    """Stands in for ToolContext where only ``allowed_roots()`` is exercised."""

    def __init__(self, roots: tuple[str, ...]) -> None:
        self._roots = tuple(Path(r) for r in roots)

    def allowed_roots(self) -> tuple[Path, ...]:
        return self._roots


# --------------------------------------------------------------------------
# default_session_suggestions
# --------------------------------------------------------------------------
class TestDefaultSessionSuggestions(unittest.TestCase):
    def test_bash_yields_command_prefix_rule(self) -> None:
        updates = default_session_suggestions("Bash", {"command": "git diff --stat"})
        self.assertTrue(updates)
        first = updates[0]
        self.assertIsInstance(first, PermissionUpdateAddRules)
        self.assertEqual(first.destination, "localSettings")
        self.assertEqual(first.behavior, "allow")
        self.assertEqual(first.rules[0].tool_name, "Bash")
        self.assertEqual(first.rules[0].rule_content, "git diff:*")

    def test_bash_empty_command_yields_nothing(self) -> None:
        self.assertEqual(default_session_suggestions("Bash", {"command": "   "}), [])
        self.assertEqual(default_session_suggestions("Bash", {"command": ""}), [])
        self.assertEqual(default_session_suggestions("Bash", {}), [])

    def test_file_edit_within_roots_sets_accept_edits_only(self) -> None:
        updates = default_session_suggestions(
            "Write",
            {"file_path": "/ws/a.py"},
            ToolPermissionContext(mode="default"),
            allowed_roots=("/ws",),
        )
        self.assertEqual(len(updates), 1)
        self.assertIsInstance(updates[0], PermissionUpdateSetMode)
        self.assertEqual(updates[0].mode, "acceptEdits")
        self.assertEqual(updates[0].destination, "session")

    def test_file_edit_outside_roots_grants_parent_directory(self) -> None:
        updates = default_session_suggestions(
            "Edit",
            {"file_path": "/other/place/a.py"},
            ToolPermissionContext(mode="default"),
            allowed_roots=("/ws",),
        )
        self.assertEqual(len(updates), 2)
        self.assertIsInstance(updates[0], PermissionUpdateSetMode)
        self.assertIsInstance(updates[1], PermissionUpdateAddDirectories)
        # File tools grant the file's *parent* directory.
        self.assertEqual(updates[1].directories, ("/other/place",))
        self.assertEqual(updates[1].destination, "session")

    def test_file_edit_plan_mode_still_sets_accept_edits(self) -> None:
        updates = default_session_suggestions(
            "Write",
            {"file_path": "/ws/a.py"},
            ToolPermissionContext(mode="plan"),
            allowed_roots=("/ws",),
        )
        self.assertEqual(len(updates), 1)
        self.assertIsInstance(updates[0], PermissionUpdateSetMode)

    def test_file_edit_already_accept_edits_within_roots_yields_nothing(self) -> None:
        # Already in acceptEdits + inside roots: the matcher auto-allows, so
        # there is no session option to offer.
        updates = default_session_suggestions(
            "Write",
            {"file_path": "/ws/a.py"},
            ToolPermissionContext(mode="acceptEdits"),
            allowed_roots=("/ws",),
        )
        self.assertEqual(updates, [])

    def test_file_edit_already_accept_edits_outside_roots_grants_dir_only(self) -> None:
        updates = default_session_suggestions(
            "Write",
            {"file_path": "/other/a.py"},
            ToolPermissionContext(mode="acceptEdits"),
            allowed_roots=("/ws",),
        )
        self.assertEqual(len(updates), 1)
        self.assertIsInstance(updates[0], PermissionUpdateAddDirectories)
        self.assertEqual(updates[0].directories, ("/other",))

    def test_read_within_roots_yields_content_less_session_rule(self) -> None:
        updates = default_session_suggestions(
            "Read", {"file_path": "/ws/a.py"}, allowed_roots=("/ws",)
        )
        self.assertEqual(len(updates), 1)
        self.assertIsInstance(updates[0], PermissionUpdateAddRules)
        self.assertEqual(updates[0].destination, "session")
        self.assertEqual(updates[0].behavior, "allow")
        self.assertEqual(updates[0].rules[0].tool_name, "Read")
        self.assertIsNone(updates[0].rules[0].rule_content)

    def test_read_outside_roots_grants_parent_directory(self) -> None:
        updates = default_session_suggestions(
            "Read", {"file_path": "/other/a.py"}, allowed_roots=("/ws",)
        )
        self.assertEqual(len(updates), 2)
        self.assertIsInstance(updates[0], PermissionUpdateAddRules)
        self.assertIsInstance(updates[1], PermissionUpdateAddDirectories)
        self.assertEqual(updates[1].directories, ("/other",))

    def test_grep_outside_roots_grants_the_path_itself(self) -> None:
        # Search tools target a directory, so the grant is the path verbatim
        # (not its parent).
        updates = default_session_suggestions(
            "Grep", {"path": "/other/sub"}, allowed_roots=("/ws",)
        )
        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0].rules[0].tool_name, "Grep")
        self.assertEqual(updates[1].directories, ("/other/sub",))

    def test_other_tool_yields_persisted_content_less_rule(self) -> None:
        updates = default_session_suggestions("WebFetch", {"url": "https://x"})
        self.assertEqual(len(updates), 1)
        self.assertIsInstance(updates[0], PermissionUpdateAddRules)
        self.assertEqual(updates[0].destination, "localSettings")
        self.assertEqual(updates[0].rules[0].tool_name, "WebFetch")
        self.assertIsNone(updates[0].rules[0].rule_content)

    def test_interaction_tools_have_no_session_option(self) -> None:
        for name in ("AskUserQuestion", "EnterPlanMode", "ExitPlanMode"):
            self.assertEqual(default_session_suggestions(name, {}), [], name)

    def test_empty_tool_name_yields_nothing(self) -> None:
        self.assertEqual(default_session_suggestions("", {}), [])

    def test_filesystem_root_edit_does_not_grant_root(self) -> None:
        # A file at "/" would make the grant directory "/" — never register the
        # whole filesystem as a session working root.
        updates = default_session_suggestions(
            "Write",
            {"file_path": "/a.py"},
            ToolPermissionContext(mode="default"),
            allowed_roots=("/ws",),
        )
        self.assertEqual(len(updates), 1)
        self.assertIsInstance(updates[0], PermissionUpdateSetMode)
        self.assertFalse(
            any(isinstance(u, PermissionUpdateAddDirectories) for u in updates)
        )

    def test_unknown_roots_assumed_inside_so_no_dir_grant(self) -> None:
        # allowed_roots=None means "unknown" — don't mint a directory grant.
        updates = default_session_suggestions(
            "Write",
            {"file_path": "/anywhere/a.py"},
            ToolPermissionContext(mode="default"),
            allowed_roots=None,
        )
        self.assertEqual(len(updates), 1)
        self.assertIsInstance(updates[0], PermissionUpdateSetMode)


# --------------------------------------------------------------------------
# session_option_label
# --------------------------------------------------------------------------
class TestSessionOptionLabel(unittest.TestCase):
    def test_empty_suggestions_returns_none(self) -> None:
        self.assertIsNone(session_option_label(()))
        self.assertIsNone(session_option_label((), "Write", {}))

    def test_file_edit_within_roots_label(self) -> None:
        label = session_option_label(
            (PermissionUpdateSetMode(destination="session", mode="acceptEdits"),),
            "Write",
        )
        # No "(shift+tab)" hint: that key is not wired to mode-cycling in this
        # port (mode changes go through /permissions).
        self.assertEqual(label, "allow all edits during this session")
        # Rendered form the surfaces show:
        self.assertEqual(
            f"Yes, {label}", "Yes, allow all edits during this session"
        )

    def test_file_edit_outside_roots_names_directory(self) -> None:
        label = session_option_label(
            (
                PermissionUpdateSetMode(destination="session", mode="acceptEdits"),
                PermissionUpdateAddDirectories(
                    destination="session", directories=("/foo/bar",)
                ),
            ),
            "Write",
        )
        self.assertEqual(label, "allow all edits in bar/ during this session")

    def test_file_edit_detected_via_accept_edits_without_tool_name(self) -> None:
        # Even when the caller cannot supply tool_name, the setMode update
        # identifies the ask as a file edit.
        label = session_option_label(
            (PermissionUpdateSetMode(destination="session", mode="acceptEdits"),),
            None,
        )
        self.assertEqual(label, "allow all edits during this session")

    def test_read_within_roots_label_is_a_complete_phrase(self) -> None:
        label = session_option_label(
            (
                PermissionUpdateAddRules(
                    destination="session",
                    behavior="allow",
                    rules=(PermissionRuleValue(tool_name="Read"),),
                ),
            ),
            "Read",
        )
        self.assertEqual(label, "allow reading during this session")
        # Must read as a sentence once prefixed (regression: bare
        # "during this session" rendered "Yes, during this session").
        self.assertEqual(f"Yes, {label}", "Yes, allow reading during this session")

    def test_read_outside_roots_names_directory(self) -> None:
        label = session_option_label(
            (
                PermissionUpdateAddRules(
                    destination="session",
                    behavior="allow",
                    rules=(PermissionRuleValue(tool_name="Read"),),
                ),
                PermissionUpdateAddDirectories(
                    destination="session", directories=("/foo/proj",)
                ),
            ),
            "Read",
        )
        self.assertEqual(label, "allow reading from proj/ during this session")

    def test_bash_label_uses_dont_ask_again(self) -> None:
        suggestions = default_session_suggestions("Bash", {"command": "git diff"})
        label = session_option_label(tuple(suggestions), "Bash")
        self.assertIsNotNone(label)
        assert label is not None
        self.assertTrue(label.startswith("and don't ask again for"))
        self.assertIn("Bash(git diff", label)

    def test_other_tool_label_uses_dont_ask_again(self) -> None:
        suggestions = default_session_suggestions("WebFetch", {"url": "https://x"})
        label = session_option_label(tuple(suggestions), "WebFetch")
        self.assertEqual(label, "and don't ask again for WebFetch")


# --------------------------------------------------------------------------
# acceptEdits-mode auto-allow (check.py)
# --------------------------------------------------------------------------
class TestAcceptEditsAutoAllow(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.ctx = ToolPermissionContext(mode="acceptEdits")
        self.tuc = _FakeToolUseContext((self.root,))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _check(self, tool, tool_input):
        return has_permissions_to_use_tool(
            tool, tool_input, self.ctx, tool_use_context=self.tuc
        )

    def test_edit_inside_roots_is_auto_allowed(self) -> None:
        decision = self._check(
            _MockTool(name="Write"),
            {"file_path": os.path.join(self.root, "a.py"), "content": "x"},
        )
        self.assertEqual(decision.behavior, "allow")
        self.assertIsInstance(decision.decision_reason, ModeDecisionReason)
        self.assertEqual(decision.decision_reason.mode, "acceptEdits")

    def test_notebook_edit_path_key_is_honored(self) -> None:
        decision = self._check(
            _MockTool(name="NotebookEdit"),
            {"notebook_path": os.path.join(self.root, "nb.ipynb")},
        )
        self.assertEqual(decision.behavior, "allow")

    def test_dangerous_file_inside_roots_still_asks(self) -> None:
        decision = self._check(
            _MockTool(name="Write"),
            {"file_path": os.path.join(self.root, ".env"), "content": "x"},
        )
        self.assertEqual(decision.behavior, "ask")

    def test_edit_outside_roots_still_asks(self) -> None:
        decision = self._check(
            _MockTool(name="Write"),
            {"file_path": "/somewhere/else/a.py", "content": "x"},
        )
        self.assertEqual(decision.behavior, "ask")

    def test_non_edit_tool_not_auto_allowed_in_accept_edits(self) -> None:
        decision = self._check(
            _MockTool(name="Read"),
            {"file_path": os.path.join(self.root, "a.py")},
        )
        self.assertEqual(decision.behavior, "ask")

    def test_explicit_tool_ask_is_not_swallowed_by_accept_edits(self) -> None:
        # A tool that explicitly asks (behavior != passthrough) must keep
        # asking even for an in-roots edit in acceptEdits mode.
        explicit = PermissionAskDecision(
            behavior="ask", message="the docs gate wants confirmation"
        )
        decision = self._check(
            _MockTool(name="Write", perm_result=explicit),
            {"file_path": os.path.join(self.root, "a.py"), "content": "x"},
        )
        self.assertEqual(decision.behavior, "ask")

    def test_default_mode_does_not_auto_allow(self) -> None:
        self.ctx = ToolPermissionContext(mode="default")
        decision = self._check(
            _MockTool(name="Write"),
            {"file_path": os.path.join(self.root, "a.py"), "content": "x"},
        )
        self.assertEqual(decision.behavior, "ask")


# --------------------------------------------------------------------------
# The session option is offered only for passthrough-derived asks
# --------------------------------------------------------------------------
class TestSessionOptionOnlyForPassthroughAsks(unittest.TestCase):
    """An ask a tool raised *explicitly* (e.g. the docs gate) must not be given
    a ``setMode:acceptEdits`` suggestion: flipping mode would not unblock it
    (the auto-allow is passthrough-gated) and would silently widen session
    scope. Only matcher-manufactured (passthrough) asks get the option."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.ctx = ToolPermissionContext(mode="default")
        self.tuc = _FakeToolUseContext((self.root,))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _check(self, tool, tool_input):
        return has_permissions_to_use_tool(
            tool, tool_input, self.ctx, tool_use_context=self.tuc
        )

    def test_passthrough_edit_ask_offers_session_option(self) -> None:
        decision = self._check(
            _MockTool(name="Write"),
            {"file_path": os.path.join(self.root, "a.py"), "content": "x"},
        )
        self.assertEqual(decision.behavior, "ask")
        self.assertTrue(decision.suggestions)
        self.assertTrue(
            any(isinstance(u, PermissionUpdateSetMode) for u in decision.suggestions)
        )

    def test_explicit_docs_gate_ask_offers_no_session_option(self) -> None:
        # Mirrors write.py/edit.py: an explicit ask, no decision_reason.
        explicit = PermissionAskDecision(
            behavior="ask",
            message="Writing documentation files is blocked unless allow_docs is enabled",
        )
        decision = self._check(
            _MockTool(name="Write", perm_result=explicit),
            {"file_path": os.path.join(self.root, "README.md"), "content": "x"},
        )
        self.assertEqual(decision.behavior, "ask")
        self.assertFalse(decision.suggestions)


# --------------------------------------------------------------------------
# ToolContext.allowed_roots folds in session-granted directories
# --------------------------------------------------------------------------
class TestAllowedRootsFolding(unittest.TestCase):
    def test_session_granted_directory_reaches_allowed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as extra:
            ws_r, extra_r = os.path.realpath(ws), os.path.realpath(extra)
            ctx = ToolContext(
                workspace_root=Path(ws_r),
                permission_context=ToolPermissionContext(
                    additional_working_directories={
                        extra_r: AdditionalWorkingDirectory(
                            path=extra_r, source="session"
                        )
                    }
                ),
            )
            roots = {str(r) for r in ctx.allowed_roots()}
            self.assertIn(str(Path(ws_r).resolve()), roots)
            self.assertIn(str(Path(extra_r).resolve()), roots)


# --------------------------------------------------------------------------
# End-to-end: ask → "always" → apply → no re-prompt
# --------------------------------------------------------------------------
def _passthrough_tool(name: str, schema_props: dict[str, Any]):
    return build_tool(
        name=name,
        description="test tool",
        input_schema={"type": "object", "properties": schema_props},
        call=lambda tool_input, context: ToolResult(name=name, output={"ok": True}),
        check_permissions=lambda tool_input, context: PermissionPassthroughResult(),
    )


class TestSessionOptionEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.registry = ToolRegistry(
            [
                _passthrough_tool(
                    "Write", {"file_path": {"type": "string"}, "content": {"type": "string"}}
                ),
                _passthrough_tool("Read", {"file_path": {"type": "string"}}),
            ]
        )
        self.ctx = ToolContext(
            workspace_root=self.root,
            permission_context=ToolPermissionContext(mode="default"),
        )
        self.calls = {"n": 0}

        def always_handler(request):
            self.calls["n"] += 1
            return PermissionAskReply(
                behavior="allow", chosen_updates=tuple(request.suggestions)
            )

        self.ctx.permission_handler = always_handler

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, path: str):
        return self.registry.dispatch(
            ToolCall(name="Write", input={"file_path": path, "content": "x"}), self.ctx
        )

    def _read(self, path: str):
        return self.registry.dispatch(
            ToolCall(name="Read", input={"file_path": path}), self.ctx
        )

    def test_allow_all_edits_session_stops_reprompting(self) -> None:
        first = self._write(str(self.root / "a.py"))
        self.assertFalse(first.is_error)
        self.assertEqual(self.calls["n"], 1)
        # The accepted setMode flipped the live context into acceptEdits.
        self.assertEqual(self.ctx.permission_context.mode, "acceptEdits")
        # A second edit to a *different* in-roots file is not re-prompted.
        second = self._write(str(self.root / "b.py"))
        self.assertFalse(second.is_error)
        self.assertEqual(self.calls["n"], 1, "second edit should auto-allow")

    def test_allow_reading_session_stops_reprompting(self) -> None:
        first = self._read(str(self.root / "a.py"))
        self.assertFalse(first.is_error)
        self.assertEqual(self.calls["n"], 1)
        self.assertIn("Read", self.ctx.permission_context.always_allow_rules["session"])
        second = self._read(str(self.root / "b.py"))
        self.assertFalse(second.is_error)
        self.assertEqual(self.calls["n"], 1, "second read should auto-allow")

    def test_out_of_roots_edit_grants_directory_for_session(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            outside_r = os.path.realpath(outside)
            first = self._write(os.path.join(outside_r, "a.py"))
            self.assertFalse(first.is_error)
            self.assertEqual(self.calls["n"], 1)
            self.assertEqual(self.ctx.permission_context.mode, "acceptEdits")
            self.assertIn(
                outside_r, self.ctx.permission_context.additional_working_directories
            )
            # A later edit elsewhere in the granted directory is auto-allowed.
            second = self._write(os.path.join(outside_r, "b.py"))
            self.assertFalse(second.is_error)
            self.assertEqual(self.calls["n"], 1, "granted dir should auto-allow")


class TestDocsGateEndToEnd(unittest.TestCase):
    """End-to-end: a docs-gated ``.md`` write (explicit tool ask) offers no
    session option, so accepting it cannot silently flip the session into
    acceptEdits, and the same file re-prompts (the gate, not a dead-end mode)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))

        def check(tool_input, context):
            p = tool_input.get("file_path", "")
            if Path(p).suffix.lower() in {".md", ".markdown"}:
                return PermissionAskDecision(
                    behavior="ask", message="docs blocked unless allow_docs"
                )
            return PermissionPassthroughResult()

        write_tool = build_tool(
            name="Write",
            description="docs-gated write",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
            call=lambda tool_input, context: ToolResult(name="Write", output={"ok": True}),
            check_permissions=check,
        )
        self.registry = ToolRegistry([write_tool])
        self.ctx = ToolContext(
            workspace_root=self.root,
            permission_context=ToolPermissionContext(mode="default"),
        )
        self.captured: list[Any] = []

        def always_handler(request):
            self.captured.append(request)
            return PermissionAskReply(
                behavior="allow", chosen_updates=tuple(request.suggestions)
            )

        self.ctx.permission_handler = always_handler

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_md(self):
        return self.registry.dispatch(
            ToolCall(
                name="Write",
                input={"file_path": str(self.root / "README.md"), "content": "x"},
            ),
            self.ctx,
        )

    def test_docs_gate_offers_no_session_option_and_does_not_escalate(self) -> None:
        first = self._write_md()
        self.assertFalse(first.is_error)
        self.assertEqual(len(self.captured), 1)
        # No middle option offered for the explicit docs-gate ask.
        self.assertFalse(self.captured[0].suggestions)
        # The session was NOT flipped into acceptEdits.
        self.assertEqual(self.ctx.permission_context.mode, "default")
        # The same file re-prompts (the gate stands; not a dead-end mode flip).
        self._write_md()
        self.assertEqual(len(self.captured), 2)


if __name__ == "__main__":
    unittest.main()
