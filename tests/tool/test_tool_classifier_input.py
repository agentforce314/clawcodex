"""Round-2 parity test for ``Tool.to_auto_classifier_input``.

See ``my-docs/ch06-tools-round2-gap-analysis.md``. TS book ch06 lists
``toAutoClassifierInput`` as one of the five "core" Tool interface
methods (`book/ch06-tools.md` lines 11-19) and as a fail-closed
default in "Apply This" -- a tool that forgets to override it returns
``""`` and the classifier silently skips it.

This test:

1. Verifies each Python tool that has a TS counterpart with a
   ``toAutoClassifierInput`` override produces a non-empty
   classifier-input string for representative input.
2. Verifies the default (``Tool`` with no override) still returns
   ``""`` -- the fail-closed contract is preserved.
3. Verifies the TS shapes for the cases TS pins down (e.g.
   ``TodoWrite`` returns ``"N items"``, ``Config`` returns
   ``"setting = value"``).

Note on tasks_v2: TaskCreate / TaskGet / TaskList / TaskUpdate /
TaskOutput are gated by the ``is_todo_v2_enabled`` feature flag. The
test imports them directly (bypassing registry filtering) so the
classifier-input assertion runs regardless of flag state.
"""
from __future__ import annotations

import unittest
from typing import Any

from src.tool_system.build_tool import Tool, build_tool
from src.tool_system.tools import (
    AskUserQuestionTool,
    BriefTool,
    ClipboardReadTool,
    ClipboardWriteTool,
    ConfigTool,
    CronCreateTool,
    CronDeleteTool,
    CronListTool,
    EnterPlanModeTool,
    EnterWorktreeTool,
    ExitPlanModeTool,
    ExitWorktreeTool,
    LSPTool,
    ListMcpResourcesTool,
    MCPTool,
    ReadMcpResourceTool,
    ReadTool,
    SendMessageTool,
    SendUserMessageTool,
    SleepTool,
    StatusTool,
    StructuredOutputTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskOutputTool,
    TaskStopTool,
    TaskUpdateTool,
    TeamCreateTool,
    TeamDeleteTool,
    TodoWriteTool,
    WebFetchTool,
    WebSearchTool,
)


# (tool, sample_input, predicate(actual_value: str) -> bool, description)
# Predicates instead of exact strings so the test tolerates safe
# concatenation differences while still locking in the substring the
# classifier must see.
TOOLS_TABLE: list[tuple[Tool, dict[str, Any], object, str]] = [
    # File / read-side
    (ReadTool, {"file_path": "/tmp/example.py"},
     lambda v: v == "/tmp/example.py",
     "Read should return file_path verbatim"),

    # Web
    (WebFetchTool, {"url": "https://example.com", "prompt": "summarize page"},
     lambda v: "https://example.com" in v and "summarize page" in v,
     "WebFetch should include both url and prompt when prompt is set"),
    (WebFetchTool, {"url": "https://example.com"},
     lambda v: v == "https://example.com",
     "WebFetch should fall back to url-only when prompt is absent"),
    (WebSearchTool, {"query": "anthropic api docs"},
     lambda v: v == "anthropic api docs",
     "WebSearch should return the query verbatim"),

    # Interactive
    (AskUserQuestionTool, {"questions": [{"question": "Are you sure?"}, {"question": "Why?"}]},
     lambda v: "Are you sure?" in v and "Why?" in v,
     "AskUserQuestion should join question text"),
    (AskUserQuestionTool, {"questions": ["just a string"]},
     lambda v: "just a string" in v,
     "AskUserQuestion should tolerate plain string entries"),

    # Todo / Task
    (TodoWriteTool, {"todos": [{"content": "a"}, {"content": "b"}]},
     lambda v: v == "2 items",
     "TodoWrite should report count, not contents"),
    (TaskCreateTool, {"subject": "Fix auth bug", "description": "..."},
     lambda v: v == "Fix auth bug",
     "TaskCreate should return subject"),
    (TaskGetTool, {"taskId": "T123"},
     lambda v: v == "T123",
     "TaskGet should return taskId"),
    (TaskUpdateTool, {"taskId": "T1", "status": "in_progress", "subject": "Updated"},
     lambda v: "T1" in v and "in_progress" in v and "Updated" in v,
     "TaskUpdate should join taskId + status + subject"),
    (TaskOutputTool, {"task_id": "T123"},
     lambda v: v == "T123",
     "TaskOutput should return task_id"),
    (TaskStopTool, {"task_id": "T123"},
     lambda v: v == "T123",
     "TaskStop should prefer task_id"),
    (TaskStopTool, {"shell_id": "S456"},
     lambda v: v == "S456",
     "TaskStop should fall back to shell_id (KillShell compat)"),
    (TaskListTool, {},
     lambda v: v == "",
     "TaskList has no input; default empty string is correct"),

    # Worktree / Plan
    (EnterWorktreeTool, {"name": "wt-feature"},
     lambda v: v == "wt-feature",
     "EnterWorktree should return name"),
    (ExitWorktreeTool, {},
     lambda v: v == "",
     "ExitWorktree default when action absent"),
    (ExitPlanModeTool, {"plan": "step 1\nstep 2"},
     lambda v: v.startswith("step 1"),
     "ExitPlanMode should surface plan prefix"),
    (EnterPlanModeTool, {},
     lambda v: v == "",
     "EnterPlanMode default (no input)"),

    # Cron
    (CronCreateTool, {"cron": "0 * * * *", "prompt": "summarize logs"},
     lambda v: "0 * * * *" in v and "summarize logs" in v,
     "CronCreate should include schedule and prompt"),
    (CronDeleteTool, {"id": "cron-abc"},
     lambda v: v == "cron-abc",
     "CronDelete should return id"),
    (CronListTool, {},
     lambda v: v == "",
     "CronList has no input; default empty string is correct"),

    # MCP
    (ListMcpResourcesTool, {"server": "fileserver"},
     lambda v: v == "fileserver",
     "ListMcpResources should return server"),
    (ReadMcpResourceTool, {"server": "fs", "uri": "file:///tmp/a"},
     lambda v: "fs" in v and "file:///tmp/a" in v,
     "ReadMcpResource should include server and uri"),
    (MCPTool, {"server": "s1", "tool": "echo"},
     lambda v: "s1" in v and "echo" in v,
     "MCP should include server and tool"),

    # Config / Team / Brief
    (ConfigTool, {"setting": "default_provider"},
     lambda v: v == "default_provider",
     "Config read should return setting key alone"),
    (ConfigTool, {"setting": "default_provider", "value": "anthropic"},
     lambda v: v == "default_provider = anthropic",
     "Config write should return setting = value"),
    (BriefTool, {"text": "deploy is done"},
     lambda v: v == "deploy is done",
     "Brief should return message body"),
    (TeamCreateTool, {"team_name": "platform"},
     lambda v: v == "platform",
     "TeamCreate should return team_name"),
    (TeamDeleteTool, {},
     lambda v: v == "",
     "TeamDelete has no input; default empty string is correct"),

    # Messaging
    (SendMessageTool, {"to": "alice", "message": "hello"},
     lambda v: v == "to alice: hello",
     "SendMessage plain text → 'to <to>: <message>'"),
    (SendMessageTool, {"to": "bob", "message": {"type": "shutdown_request"}},
     lambda v: v == "to bob: <shutdown_request>",
     "SendMessage structured envelope → 'to <to>: <type>'"),
    (SendUserMessageTool, {"message": "deployment done", "status": "normal"},
     lambda v: v == "deployment done",
     "SendUserMessage should return message body"),

    # Misc
    (LSPTool, {"method": "textDocument/hover"},
     lambda v: v == "textDocument/hover",
     "LSP should return method"),
    (SleepTool, {"seconds": 5},
     lambda v: "5" in v,
     "Sleep should surface the duration"),
    (StructuredOutputTool, {"foo": "bar"},
     lambda v: v == "",
     "StructuredOutput has no security-relevant input shape; default empty is correct"),
    (StatusTool, {},
     lambda v: v == "",
     "Status takes no input; default empty is correct"),
    (ClipboardReadTool, {},
     lambda v: v == "",
     "ClipboardRead takes no input; default empty is correct"),
    (ClipboardWriteTool, {"content": "hello"},
     lambda v: v == "",
     "ClipboardWrite default (kept until a TS counterpart appears)"),
]


def _noop_call(tool_input: dict[str, Any], context: Any) -> Any:  # pragma: no cover
    from src.tool_system.protocol import ToolResult
    return ToolResult(name="Noop", output={})


class TestToAutoClassifierInputParity(unittest.TestCase):
    """Walk the table; assert each tool emits the expected shape."""

    def test_each_tool_emits_expected_classifier_input(self) -> None:
        for tool, sample, predicate, description in TOOLS_TABLE:
            with self.subTest(tool=tool.name, description=description):
                value = tool.to_auto_classifier_input(sample)
                self.assertIsInstance(value, str, f"{tool.name}: result must be str (got {type(value).__name__})")
                self.assertTrue(
                    predicate(value),
                    f"{tool.name} ({description}): unexpected classifier "
                    f"input. Got: {value!r}",
                )

    def test_default_is_empty_string(self) -> None:
        """Fail-closed default: a tool with no override returns ``""``."""
        t = build_tool(
            name="DefaultTool",
            input_schema={"type": "object", "properties": {}},
            call=_noop_call,
        )
        self.assertEqual(t.to_auto_classifier_input({}), "")
        self.assertEqual(t.to_auto_classifier_input({"anything": 1}), "")


class TestToolsWithTSCounterpartHaveOverride(unittest.TestCase):
    """Smoke test: every tool whose TS counterpart has a
    ``toAutoClassifierInput`` override should have one in Python too.

    This is a regression guard: if a future contributor adds a new
    Python tool that mirrors a TS-with-classifier-input tool but
    forgets the override, this test points at it. We list the tools
    by their Python registry name explicitly so removing one fails
    loudly.
    """

    EXPECTED_OVERRIDES = {
        "Read", "WebFetch", "WebSearch", "AskUserQuestion",
        "TodoWrite", "TaskCreate", "TaskGet", "TaskUpdate",
        "TaskOutput", "TaskStop", "EnterWorktree", "ExitWorktree",
        "ExitPlanMode", "CronCreate", "CronDelete",
        "ListMcpResourcesTool", "ReadMcpResourceTool", "MCP",
        "Config", "Brief", "TeamCreate", "SendMessage",
        "SendUserMessage", "LSP", "Sleep", "ToolSearch",
        "Edit", "Write", "Grep", "Glob", "NotebookEdit", "Bash",
        "Skill", "Agent",
    }

    def test_each_expected_tool_overrides_classifier_input(self) -> None:
        """For every expected tool, calling
        ``to_auto_classifier_input`` with a representative input
        should produce a non-empty string."""
        from src.tool_system.tools import ALL_STATIC_TOOLS

        # Build a name → tool map (also include the make_*_tool
        # factories so we test ToolSearch and Agent).
        by_name = {t.name: t for t in ALL_STATIC_TOOLS}

        # ToolSearch and Agent are constructed via factory; pull them
        # out separately so the test does not depend on registry
        # construction.
        from src.tool_system.registry import ToolRegistry
        from src.tool_system.tools.tool_search import make_tool_search_tool
        from src.tool_system.tools.agent import make_agent_tool
        registry = ToolRegistry()
        for t in ALL_STATIC_TOOLS:
            registry.register(t)
        by_name["ToolSearch"] = make_tool_search_tool(registry)
        by_name["Agent"] = make_agent_tool(registry)

        # Representative inputs for the override check. Each input
        # MUST produce a non-empty string. We do not match shape here
        # (the TOOLS_TABLE test above does that); we just verify the
        # tool didn't silently fall through to the empty-string
        # default.
        non_empty_samples: dict[str, dict[str, Any]] = {
            "Read": {"file_path": "/x"},
            "WebFetch": {"url": "https://x"},
            "WebSearch": {"query": "x"},
            "AskUserQuestion": {"questions": [{"question": "?"}]},
            "TodoWrite": {"todos": [{"content": "a"}]},
            "TaskCreate": {"subject": "x"},
            "TaskGet": {"taskId": "x"},
            "TaskUpdate": {"taskId": "x"},
            "TaskOutput": {"task_id": "x"},
            "TaskStop": {"task_id": "x"},
            "EnterWorktree": {"name": "x"},
            "ExitWorktree": {"action": "remove"},
            "ExitPlanMode": {"plan": "x"},
            "CronCreate": {"cron": "* * * * *", "prompt": "x"},
            "CronDelete": {"id": "x"},
            "ListMcpResourcesTool": {"server": "x"},
            "ReadMcpResourceTool": {"server": "x", "uri": "y"},
            "MCP": {"server": "x", "tool": "y"},
            "Config": {"setting": "x"},
            "Brief": {"text": "x"},
            "TeamCreate": {"team_name": "x"},
            "SendMessage": {"to": "u", "message": "m"},
            "SendUserMessage": {"message": "x", "status": "normal"},
            "LSP": {"method": "x"},
            "Sleep": {"seconds": 1},
            "ToolSearch": {"query": "x"},
            "Edit": {"file_path": "/x", "old_string": "a", "new_string": "b"},
            "Write": {"file_path": "/x", "content": "y"},
            "Grep": {"pattern": "x"},
            "Glob": {"pattern": "x"},
            "NotebookEdit": {"notebook_path": "/x", "new_source": "y"},
            "Bash": {"command": "ls"},
            "Skill": {"skill": "x"},
            "Agent": {"prompt": "do x"},
        }

        missing: list[str] = []
        empty_outputs: list[tuple[str, Any]] = []
        for name in self.EXPECTED_OVERRIDES:
            tool = by_name.get(name)
            if tool is None:
                missing.append(name)
                continue
            sample = non_empty_samples.get(name, {})
            value = tool.to_auto_classifier_input(sample)
            if not isinstance(value, str) or value == "":
                empty_outputs.append((name, value))

        self.assertEqual(
            missing, [],
            f"Expected tools not in registry: {missing}",
        )
        self.assertEqual(
            empty_outputs, [],
            "These tools should override to_auto_classifier_input but "
            f"returned empty/falsy: {empty_outputs}",
        )


if __name__ == "__main__":
    unittest.main()
