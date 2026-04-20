from __future__ import annotations

import io
import json
import os
import socket
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import patch

from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall
from src.tool_system.registry import ToolRegistry
from src.tool_system.tools import (
    AskUserQuestionTool,
    BashTool,
    BriefTool,
    ConfigTool,
    CronCreateTool,
    CronDeleteTool,
    CronListTool,
    EditTool,
    ReadTool,
    WriteTool,
    GlobTool,
    GrepTool,
    LSPTool,
    MCPTool,
    ListMcpResourcesTool,
    ReadMcpResourceTool,
    SkillTool,
    SleepTool,
    TodoWriteTool,
    StructuredOutputTool,
    TaskStopTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskOutputTool,
    TaskUpdateTool,
    WebFetchTool,
    WebSearchTool,
    TeamCreateTool,
    TeamDeleteTool,
    EnterWorktreeTool,
    ExitWorktreeTool,
    EnterPlanModeTool,
    ExitPlanModeTool,
    make_tool_search_tool,
)


class ToolSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.ctx = ToolContext(workspace_root=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()


class TestReadTool(ToolSystemTests):
    def test_read_returns_cat_n_format(self) -> None:
        p = self.root / "a.txt"
        p.write_text("line1\nline2\nline3\n", encoding="utf-8")
        out = ReadTool.call({"file_path": str(p), "offset": 2, "limit": 2}, self.ctx).output
        self.assertEqual(out["type"], "text")
        self.assertEqual(out["file"]["content"], "2\tline2\n3\tline3")

    def test_read_allows_relative_path_under_workspace(self) -> None:
        p = self.root / "a.txt"
        p.write_text("x\n", encoding="utf-8")
        out = ReadTool.call({"file_path": "a.txt", "limit": 10}, self.ctx).output
        self.assertEqual(out["type"], "text")
        self.assertIn("1\tx", out["file"]["content"])

    def test_read_returns_file_unchanged_stub(self) -> None:
        p = self.root / "same.txt"
        p.write_text("line\n", encoding="utf-8")
        first = ReadTool.call({"file_path": str(p), "limit": 10}, self.ctx).output
        self.assertEqual(first["type"], "text")
        second = ReadTool.call({"file_path": str(p)}, self.ctx).output
        self.assertEqual(second["type"], "file_unchanged")
        self.assertEqual(second["file"]["filePath"], str(p))

    def test_read_notebook(self) -> None:
        p = self.root / "nb.ipynb"
        p.write_text('{"cells":[{"cell_type":"markdown","source":["hi"]}]}', encoding="utf-8")
        out = ReadTool.call({"file_path": str(p)}, self.ctx).output
        self.assertEqual(out["type"], "notebook")
        self.assertEqual(len(out["file"]["cells"]), 1)

    def test_read_pdf(self) -> None:
        p = self.root / "x.pdf"
        p.write_bytes(b"%PDF-1.4\n1 0 obj\n")
        out = ReadTool.call({"file_path": str(p)}, self.ctx).output
        self.assertEqual(out["type"], "pdf")

    def test_read_blocks_device_paths(self) -> None:
        with self.assertRaises(Exception):
            ReadTool.call({"file_path": "/dev/zero"}, self.ctx)


class TestWriteTool(ToolSystemTests):
    def test_write_creates_file(self) -> None:
        p = self.root / "b.txt"
        out = WriteTool.call({"file_path": str(p), "content": "hello"}, self.ctx).output
        self.assertTrue(p.exists())
        self.assertEqual(out["type"], "create")
        self.assertEqual(out["filePath"], str(p))

    def test_write_requires_read_before_overwrite(self) -> None:
        p = self.root / "c.txt"
        p.write_text("old", encoding="utf-8")
        with self.assertRaises(Exception):
            WriteTool.call({"file_path": str(p), "content": "new"}, self.ctx)

        ReadTool.call({"file_path": str(p), "limit": 10}, self.ctx)
        WriteTool.call({"file_path": str(p), "content": "new"}, self.ctx)
        self.assertEqual(p.read_text(encoding="utf-8"), "new")

    def test_write_blocks_docs_by_default(self) -> None:
        p = self.root / "README.md"
        result = WriteTool.check_permissions({"file_path": str(p), "content": "x"}, self.ctx)
        self.assertEqual(result.behavior, "ask")


class TestEditTool(ToolSystemTests):
    def test_edit_requires_read(self) -> None:
        p = self.root / "d.txt"
        p.write_text("hello world", encoding="utf-8")
        with self.assertRaises(Exception):
            EditTool.call({"file_path": str(p), "old_string": "world", "new_string": "you"}, self.ctx)

    def test_edit_replaces_unique(self) -> None:
        p = self.root / "e.txt"
        p.write_text("hello world", encoding="utf-8")
        ReadTool.call({"file_path": str(p), "limit": 10}, self.ctx)
        out = EditTool.call({"file_path": str(p), "old_string": "world", "new_string": "you"}, self.ctx).output
        self.assertEqual(out["filePath"], str(p))
        self.assertEqual(p.read_text(encoding="utf-8"), "hello you")

    def test_edit_requires_replace_all_for_non_unique(self) -> None:
        p = self.root / "f.txt"
        p.write_text("a a a", encoding="utf-8")
        ReadTool.call({"file_path": str(p), "limit": 10}, self.ctx)
        with self.assertRaises(Exception):
            EditTool.call({"file_path": str(p), "old_string": "a", "new_string": "b"}, self.ctx)
        EditTool.call({"file_path": str(p), "old_string": "a", "new_string": "b", "replace_all": True}, self.ctx)
        self.assertEqual(p.read_text(encoding="utf-8"), "b b b")


class TestGlobTool(ToolSystemTests):
    def test_glob_sorts_by_mtime(self) -> None:
        a = self.root / "x1.py"
        b = self.root / "x2.py"
        a.write_text("a", encoding="utf-8")
        time.sleep(0.01)
        b.write_text("b", encoding="utf-8")
        out = GlobTool.call({"pattern": "*.py", "path": str(self.root), "limit": 10}, self.ctx).output
        self.assertTrue(out["filenames"][0].endswith("x2.py"))
        self.assertTrue(out["filenames"][1].endswith("x1.py"))


class TestGrepTool(ToolSystemTests):
    def test_grep_files_with_matches(self) -> None:
        (self.root / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
        (self.root / "b.txt").write_text("nope\n", encoding="utf-8")
        out = GrepTool.call({"pattern": "hello", "path": str(self.root)}, self.ctx).output
        self.assertEqual(out["mode"], "files_with_matches")
        self.assertEqual(out["numFiles"], 1)
        self.assertIn("a.txt", out["filenames"][0])

    def test_grep_content_mode_with_line_numbers(self) -> None:
        (self.root / "a.txt").write_text("hello\nhello\n", encoding="utf-8")
        out = GrepTool.call({"pattern": "hello", "path": str(self.root), "output_mode": "content", "-n": True}, self.ctx).output
        self.assertIn(":1:", out["content"])


class TestBashTool(ToolSystemTests):
    def test_bash_echo(self) -> None:
        out = BashTool.call({"command": "echo hello"}, self.ctx).output
        self.assertEqual(out["exit_code"], 0)
        self.assertIn("hello", out["stdout"])

    def test_bash_blocks_sudo(self) -> None:
        with self.assertRaises(Exception):
            BashTool.call({"command": "sudo echo nope"}, self.ctx)


class TestWebFetchTool(ToolSystemTests):
    def test_web_fetch_blocks_file_scheme(self) -> None:
        with self.assertRaises(Exception):
            WebFetchTool.call({"url": "file:///etc/passwd"}, self.ctx)

    def test_web_fetch_extracts_text(self) -> None:
        html_doc = "<html><body><h1>Title</h1><p>Hello <b>world</b></p></body></html>"

        class _Resp(io.BytesIO):
            headers = {"Content-Type": "text/html"}
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
            with patch("urllib.request.OpenerDirector.open", return_value=_Resp(html_doc.encode("utf-8"))):
                out = WebFetchTool.call({"url": "https://example.com/", "prompt": "extract text"}, self.ctx).output
                self.assertIn("Title", out["result"])
                self.assertIn("Hello", out["result"])


class TestWebSearchTool(ToolSystemTests):
    """Tests for the refactored WebSearchTool.

    All tests that exercise DuckDuckGo search patch both the package-based
    search (``_ddg_package_search``) and ``urllib.request.urlopen`` to ensure
    the HTML-scraping fallback path is taken with controlled data, regardless
    of whether ``duckduckgo-search`` is installed in the test environment.
    """

    _DDG_PKG_PATCH = "src.tool_system.tools.web_search._ddg_package_search"

    def test_web_search_parses_results(self) -> None:
        html_doc = """
        <a class="result__a" href="https://example.com/">Example</a>
        <a class="result__snippet">Snippet</a>
        """

        class _Resp(io.BytesIO):
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch(self._DDG_PKG_PATCH, return_value=None), \
             patch.object(urllib.request, "urlopen", return_value=_Resp(html_doc.encode("utf-8"))):
            out = WebSearchTool.call({"query": "example"}, self.ctx).output
            self.assertEqual(out["query"], "example")
            self.assertIn("duration_seconds", out)
            # Structured output: snippet text + links object
            self.assertEqual(len(out["results"]), 2)
            # First result is text snippet
            self.assertIsInstance(out["results"][0], str)
            self.assertIn("Example", out["results"][0])
            self.assertIn("https://example.com/", out["results"][0])
            # Second result is structured links
            links = out["results"][1]
            self.assertEqual(links["content"][0]["url"], "https://example.com/")
            self.assertEqual(links["content"][0]["title"], "Example")

    def test_web_search_domain_filtering_blocked(self) -> None:
        """blocked_domains filters out matching results."""
        html_doc = """
        <a class="result__a" href="https://example.com/">Example</a>
        <a class="result__snippet">Snippet one</a>
        <a class="result__a" href="https://other.org/">Other</a>
        <a class="result__snippet">Snippet two</a>
        """

        class _Resp(io.BytesIO):
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch(self._DDG_PKG_PATCH, return_value=None), \
             patch.object(urllib.request, "urlopen", return_value=_Resp(html_doc.encode("utf-8"))):
            out = WebSearchTool.call(
                {"query": "test", "blocked_domains": ["example.com"]}, self.ctx
            ).output
            links = out["results"][-1]
            # Only the non-blocked result should remain
            self.assertEqual(len(links["content"]), 1)
            self.assertEqual(links["content"][0]["url"], "https://other.org/")

    def test_web_search_domain_filtering_allowed(self) -> None:
        """allowed_domains keeps only matching results."""
        html_doc = """
        <a class="result__a" href="https://example.com/">Example</a>
        <a class="result__snippet">Snippet one</a>
        <a class="result__a" href="https://other.org/">Other</a>
        <a class="result__snippet">Snippet two</a>
        """

        class _Resp(io.BytesIO):
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch(self._DDG_PKG_PATCH, return_value=None), \
             patch.object(urllib.request, "urlopen", return_value=_Resp(html_doc.encode("utf-8"))):
            out = WebSearchTool.call(
                {"query": "test", "allowed_domains": ["example.com"]}, self.ctx
            ).output
            links = out["results"][-1]
            self.assertEqual(len(links["content"]), 1)
            self.assertEqual(links["content"][0]["url"], "https://example.com/")

    def test_web_search_subdomain_matching(self) -> None:
        """Subdomain matching: sub.example.com matches example.com."""
        html_doc = """
        <a class="result__a" href="https://sub.example.com/page">Sub Example</a>
        <a class="result__snippet">Sub snippet</a>
        <a class="result__a" href="https://badexample.com/">Bad Example</a>
        <a class="result__snippet">Bad snippet</a>
        """

        class _Resp(io.BytesIO):
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch(self._DDG_PKG_PATCH, return_value=None), \
             patch.object(urllib.request, "urlopen", return_value=_Resp(html_doc.encode("utf-8"))):
            out = WebSearchTool.call(
                {"query": "test", "allowed_domains": ["example.com"]}, self.ctx
            ).output
            links = out["results"][-1]
            # sub.example.com matches, badexample.com does NOT
            self.assertEqual(len(links["content"]), 1)
            self.assertEqual(links["content"][0]["url"], "https://sub.example.com/page")

    def test_web_search_validate_mutual_exclusion(self) -> None:
        """Cannot specify both allowed_domains and blocked_domains."""
        result = WebSearchTool.validate_input(
            {"query": "test", "allowed_domains": ["a.com"], "blocked_domains": ["b.com"]},
            self.ctx,
        )
        self.assertFalse(result.result)
        self.assertIn("Cannot specify both", result.message)

    def test_web_search_prompt_includes_date(self) -> None:
        """Prompt includes current month/year and sources requirement."""
        prompt = WebSearchTool.prompt()
        self.assertIn("Sources:", prompt)
        self.assertIn("CRITICAL REQUIREMENT", prompt)
        self.assertIn("current month is", prompt)

    def test_web_search_map_result_to_api(self) -> None:
        """map_result_to_api formats output with source reminder."""
        output = {
            "query": "python docs",
            "results": [
                "**Python** -- The official docs (https://python.org)",
                {"tool_use_id": "ddg-search", "content": [{"title": "Python", "url": "https://python.org"}]},
            ],
            "duration_seconds": 0.5,
        }
        api_result = WebSearchTool.map_result_to_api(output, "test-id")
        self.assertEqual(api_result["tool_use_id"], "test-id")
        self.assertIn("python docs", api_result["content"])
        self.assertIn("REMINDER", api_result["content"])
        self.assertIn("sources", api_result["content"].lower())

    def test_web_search_ddg_package_path(self) -> None:
        """When duckduckgo-search package is available, use it instead of HTML scraping."""
        mock_results = [
            {"title": "Pkg Result", "url": "https://pkg.example.com/", "snippet": "From package"},
        ]
        with patch(self._DDG_PKG_PATCH, return_value=mock_results):
            out = WebSearchTool.call({"query": "package test"}, self.ctx).output
            self.assertEqual(out["query"], "package test")
            links = out["results"][-1]
            self.assertEqual(links["content"][0]["url"], "https://pkg.example.com/")


class TestSleepTool(ToolSystemTests):
    def test_sleep_short(self) -> None:
        start = time.time()
        SleepTool.call({"seconds": 0.01}, self.ctx)
        self.assertGreaterEqual(time.time() - start, 0.0)


class TestTaskStopTool(ToolSystemTests):
    def test_task_stop(self) -> None:
        def target(stop_event):
            while not stop_event.is_set():
                time.sleep(0.01)

        task = self.ctx.task_manager.start(name="loop", target=target)
        out = TaskStopTool.call({"task_id": task.task_id}, self.ctx).output
        self.assertTrue(out["stopped"])


class TestConfigTool(ToolSystemTests):
    def test_config_get_set_roundtrip(self) -> None:
        from src import config as config_mod

        cfg_path = self.root / "config.json"
        cfg_path.write_text(json.dumps(config_mod.get_default_config()), encoding="utf-8")
        with patch("src.config.get_config_path", return_value=cfg_path):
            get_out = ConfigTool.call({"setting": "default_provider"}, self.ctx).output
            self.assertEqual(get_out["operation"], "get")
            set_out = ConfigTool.call({"setting": "default_provider", "value": "openai"}, self.ctx).output
            self.assertEqual(set_out["operation"], "set")
            self.assertEqual(ConfigTool.call({"setting": "default_provider"}, self.ctx).output["value"], "openai")


class TestMCPTool(ToolSystemTests):
    def test_mcp_calls_client(self) -> None:
        class Client:
            def call_tool(self, tool_name: str, args: dict) -> Any:
                return {"tool": tool_name, "args": args}

            def list_tools(self) -> list[str]:
                return ["x"]

        self.ctx.mcp_clients["srv"] = Client()
        out = MCPTool.call({"server": "srv", "tool": "x", "input": {"a": 1}}, self.ctx).output
        self.assertEqual(out["output"]["args"]["a"], 1)


class TestLSPTool(ToolSystemTests):
    def test_lsp_requires_client(self) -> None:
        out = LSPTool.call({"method": "initialize", "params": {}}, self.ctx)
        self.assertTrue(out.is_error)

    def test_lsp_calls_client(self) -> None:
        class Client:
            def request(self, method: str, params=None) -> Any:
                return {"method": method, "params": params}

        self.ctx.lsp_client = Client()
        out = LSPTool.call({"method": "hover", "params": {"x": 1}}, self.ctx).output
        self.assertEqual(out["response"]["params"]["x"], 1)


class TestSkillTool(ToolSystemTests):
    def test_skill_runs_markdown_skill(self) -> None:
        from src.skills.create import create_skill

        skills_dir = self.root / "skills"
        create_skill(
            directory=skills_dir,
            name="hello",
            description="say hello",
            body="Hello $ARGUMENTS[0]!",
            arguments=["name"],
        )
        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            out = SkillTool.call({"skill": "hello", "args": "bob"}, self.ctx).output
            self.assertTrue(out["success"])
            self.assertIn("Hello bob!", out["prompt"])
            self.assertEqual(out["loadedFrom"], "user")

    def test_skill_runs_legacy_python_skill(self) -> None:
        skills_dir = self.root / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "legacy.py").write_text(
            "def run(input, context):\n    return 'hi ' + input.get('name','world')\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            out = SkillTool.call({"name": "legacy", "input": {"name": "bob"}}, self.ctx).output
            self.assertEqual(out["output"], "hi bob")


class TestNewParityTools(ToolSystemTests):
    def test_ask_user_question_uses_handler(self) -> None:
        self.ctx.ask_user = lambda questions: {questions[0]["question"]: "Option A"}
        out = AskUserQuestionTool.call(
            {
                "questions": [
                    {
                        "question": "Choose?",
                        "header": "Choice",
                        "options": [
                            {"label": "Option A", "description": "A"},
                            {"label": "Option B", "description": "B"},
                        ],
                    }
                ]
            },
            self.ctx,
        ).output
        self.assertEqual(out["answers"]["Choose?"], "Option A")

    def test_todo_write(self) -> None:
        out = TodoWriteTool.call(
            {"todos": [{"content": "x", "status": "pending", "activeForm": "Doing x"}]},
            self.ctx,
        ).output
        self.assertEqual(out["newTodos"][0]["content"], "x")

    def test_task_tools_roundtrip(self) -> None:
        created = TaskCreateTool.call({"subject": "T1", "description": "D1"}, self.ctx).output
        task_id = created["task"]["id"]
        listed = TaskListTool.call({}, self.ctx).output
        self.assertEqual(len(listed["tasks"]), 1)
        TaskUpdateTool.call({"taskId": task_id, "status": "completed"}, self.ctx)
        got = TaskGetTool.call({"taskId": task_id}, self.ctx).output
        self.assertEqual(got["task"]["status"], "completed")
        task_out = TaskOutputTool.call({"task_id": task_id}, self.ctx).output
        self.assertEqual(task_out["task"]["task_id"], task_id)

    def test_task_cascade_delete_removes_blockers(self) -> None:
        """Deleting a task removes its ID from blocks/blockedBy of all other tasks."""
        t1 = TaskCreateTool.call({"subject": "T1", "description": "D1"}, self.ctx).output["task"]["id"]
        t2 = TaskCreateTool.call({"subject": "T2", "description": "D2"}, self.ctx).output["task"]["id"]
        t3 = TaskCreateTool.call({"subject": "T3", "description": "D3"}, self.ctx).output["task"]["id"]

        # T2 is blocked by T1; T3 blocks T1
        TaskUpdateTool.call({"taskId": t2, "addBlockedBy": [t1]}, self.ctx)
        TaskUpdateTool.call({"taskId": t3, "addBlocks": [t1]}, self.ctx)

        # Verify dependencies are set
        self.assertIn(t1, self.ctx.tasks[t2]["blockedBy"])
        self.assertIn(t1, self.ctx.tasks[t3]["blocks"])

        # Delete T1
        TaskUpdateTool.call({"taskId": t1, "status": "deleted"}, self.ctx)

        # T1 should be gone
        self.assertNotIn(t1, self.ctx.tasks)
        # T2's blockedBy should no longer contain T1
        self.assertNotIn(t1, self.ctx.tasks[t2].get("blockedBy", []))
        # T3's blocks should no longer contain T1
        self.assertNotIn(t1, self.ctx.tasks[t3].get("blocks", []))

    def test_task_list_filters_internal_tasks(self) -> None:
        """Tasks with metadata._internal=True should be hidden from TaskList."""
        t1 = TaskCreateTool.call({"subject": "Visible", "description": "D1"}, self.ctx).output["task"]["id"]
        t2 = TaskCreateTool.call(
            {"subject": "Internal", "description": "D2", "metadata": {"_internal": True}},
            self.ctx,
        ).output["task"]["id"]

        listed = TaskListTool.call({}, self.ctx).output
        listed_ids = [t["id"] for t in listed["tasks"]]
        self.assertIn(t1, listed_ids)
        self.assertNotIn(t2, listed_ids)

    def test_task_list_filters_resolved_blockers(self) -> None:
        """Completed tasks should be removed from blockedBy in TaskList output."""
        t1 = TaskCreateTool.call({"subject": "Blocker", "description": "D1"}, self.ctx).output["task"]["id"]
        t2 = TaskCreateTool.call({"subject": "Blocked", "description": "D2"}, self.ctx).output["task"]["id"]

        TaskUpdateTool.call({"taskId": t2, "addBlockedBy": [t1]}, self.ctx)

        # Before completion, blocker should appear
        listed = TaskListTool.call({}, self.ctx).output
        t2_entry = [t for t in listed["tasks"] if t["id"] == t2][0]
        self.assertIn(t1, t2_entry["blockedBy"])

        # Complete the blocker
        TaskUpdateTool.call({"taskId": t1, "status": "completed"}, self.ctx)

        # After completion, resolved blocker should be filtered out
        listed = TaskListTool.call({}, self.ctx).output
        t2_entry = [t for t in listed["tasks"] if t["id"] == t2][0]
        self.assertNotIn(t1, t2_entry["blockedBy"])


class TestTaskFormatting(ToolSystemTests):
    """Tests for human-readable result formatting helpers."""

    def test_format_task_created(self) -> None:
        from src.tool_system.tools.tasks_v2 import _format_task_created
        result = _format_task_created("abc123", "Fix auth bug")
        self.assertEqual(result, "Task #abc123 created successfully: Fix auth bug")

    def test_format_task_detail_with_deps(self) -> None:
        from src.tool_system.tools.tasks_v2 import _format_task_detail
        task = {
            "id": "1",
            "subject": "Fix bug",
            "status": "in_progress",
            "description": "Fix the auth bug",
            "blockedBy": ["2", "3"],
            "blocks": ["4"],
        }
        result = _format_task_detail(task)
        self.assertIn("Task #1: Fix bug", result)
        self.assertIn("Status: in_progress", result)
        self.assertIn("Description: Fix the auth bug", result)
        self.assertIn("Blocked by: #2, #3", result)
        self.assertIn("Blocks: #4", result)

    def test_format_task_detail_none(self) -> None:
        from src.tool_system.tools.tasks_v2 import _format_task_detail
        self.assertEqual(_format_task_detail(None), "Task not found")

    def test_format_task_detail_no_deps(self) -> None:
        from src.tool_system.tools.tasks_v2 import _format_task_detail
        task = {
            "id": "1",
            "subject": "Simple task",
            "status": "pending",
            "description": "Do it",
            "blockedBy": [],
            "blocks": [],
        }
        result = _format_task_detail(task)
        self.assertNotIn("Blocked by", result)
        self.assertNotIn("Blocks", result)

    def test_format_task_list_empty(self) -> None:
        from src.tool_system.tools.tasks_v2 import _format_task_list
        self.assertEqual(_format_task_list([]), "No tasks found")

    def test_format_task_list_with_tasks(self) -> None:
        from src.tool_system.tools.tasks_v2 import _format_task_list
        tasks = [
            {"id": "1", "subject": "T1", "status": "pending", "blockedBy": []},
            {"id": "2", "subject": "T2", "status": "in_progress", "owner": "agent-1", "blockedBy": ["1"]},
        ]
        result = _format_task_list(tasks)
        self.assertIn("#1 [pending] T1", result)
        self.assertIn("#2 [in_progress] T2 (agent-1) [blocked by #1]", result)

    def test_format_task_updated_success(self) -> None:
        from src.tool_system.tools.tasks_v2 import _format_task_updated
        result = _format_task_updated(True, "1", ["status", "subject"])
        self.assertEqual(result, "Updated task #1 status, subject")

    def test_format_task_updated_failure(self) -> None:
        from src.tool_system.tools.tasks_v2 import _format_task_updated
        result = _format_task_updated(False, "1", [], error="Task not found")
        self.assertEqual(result, "Task not found")

    def test_tool_search(self) -> None:
        reg = build_default_registry(include_user_tools=False)
        tool_search = make_tool_search_tool(reg)
        out = tool_search.call({"query": "read"}, self.ctx).output
        self.assertIn("Read", out["matches"])

    def test_cron_tools_roundtrip(self) -> None:
        created = CronCreateTool.call({"cron": "*/5 * * * *", "prompt": "ping"}, self.ctx).output
        cron_id = created["id"]
        listed = CronListTool.call({}, self.ctx).output
        self.assertEqual(len(listed["jobs"]), 1)
        deleted = CronDeleteTool.call({"id": cron_id}, self.ctx).output
        self.assertTrue(deleted["success"])

    def test_structured_output(self) -> None:
        out = StructuredOutputTool.call({"ok": True}, self.ctx).output
        self.assertTrue(out["structured_output"]["ok"])

    def test_mcp_resource_tools(self) -> None:
        class Client:
            def list_resources(self):
                return [{"uri": "x://1", "name": "r1", "mimeType": "text/plain"}]

            def read_resource(self, uri: str):
                return {"contents": [{"uri": uri, "text": "hello"}]}

        self.ctx.mcp_clients["srv"] = Client()
        listed = ListMcpResourcesTool.call({"server": "srv"}, self.ctx).output
        self.assertEqual(listed[0]["uri"], "x://1")
        read = ReadMcpResourceTool.call({"server": "srv", "uri": "x://1"}, self.ctx).output
        self.assertEqual(read["contents"][0]["text"], "hello")


class TestRegistryAndHelloWorldTool(ToolSystemTests):
    def test_can_load_user_tool_hello_world(self) -> None:
        user_dir = self.root / "tools"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "hello.py").write_text(
            "tool_spec = {\n"
            "  'name': 'HelloWorld',\n"
            "  'description': 'hello world tool',\n"
            "  'input_schema': { 'type': 'object', 'properties': { 'name': { 'type': 'string' } } },\n"
            "}\n"
            "def run(tool_input, context):\n"
            "  return { 'message': 'hello ' + tool_input.get('name','world') }\n",
            encoding="utf-8",
        )

        from src.tool_system.loader import load_tools_from_dir

        tools = load_tools_from_dir(user_dir)
        self.assertEqual(len(tools), 1)
        reg = ToolRegistry(tools=tools)
        result = reg.dispatch(ToolCall(name="HelloWorld", input={"name": "alice"}), self.ctx)
        self.assertEqual(result.output["message"], "hello alice")


class TestBriefAndAgentTools(ToolSystemTests):
    def test_brief_tool(self) -> None:
        out = BriefTool.call({"text": "abc", "max_chars": 2}, self.ctx).output
        self.assertEqual(out["preview"], "ab…")

    def test_agent_tool_registered(self) -> None:
        reg = build_default_registry(include_user_tools=False)
        agent_tool = reg.get("Agent")
        self.assertIsNotNone(agent_tool)
        self.assertEqual(agent_tool.name, "Agent")

    def test_agent_tool_requires_prompt(self) -> None:
        from src.tool_system.errors import ToolInputError
        reg = build_default_registry(include_user_tools=False)
        agent_tool = reg.get("Agent")
        ctx = ToolContext(workspace_root=self.root)
        with self.assertRaises(ToolInputError):
            agent_tool.call({"prompt": ""}, ctx)

    def test_agent_tool_no_provider_returns_error(self) -> None:
        reg = build_default_registry(include_user_tools=False)
        agent_tool = reg.get("Agent")
        ctx = ToolContext(workspace_root=self.root)
        result = agent_tool.call({"prompt": "search for bugs"}, ctx)
        self.assertTrue(result.is_error)
        self.assertEqual(result.output["status"], "error")


class TestTeamTools(ToolSystemTests):
    def test_team_create_roundtrip(self) -> None:
        create_out = TeamCreateTool.call(
            {"team_name": "test-team", "description": "A test team"},
            self.ctx,
        ).output
        self.assertEqual(create_out["team_name"], "test-team")
        self.assertIsNotNone(create_out["lead_agent_id"])
        self.assertEqual(self.ctx.team["team_name"], "test-team")

        team_file = self.root / ".clawcodex" / "team.json"
        self.assertTrue(team_file.exists())

        delete_out = TeamDeleteTool.call({}, self.ctx).output
        self.assertTrue(delete_out["success"])
        self.assertEqual(delete_out["team_name"], "test-team")
        self.assertIsNone(self.ctx.team)

        self.assertFalse(team_file.exists())

    def test_team_delete_no_team(self) -> None:
        out = TeamDeleteTool.call({}, self.ctx).output
        self.assertFalse(out["success"])
        self.assertEqual(out["message"], "No active team")

    def test_team_create_requires_name(self) -> None:
        from src.tool_system.errors import ToolInputError

        with self.assertRaises(ToolInputError):
            TeamCreateTool.call({"team_name": ""}, self.ctx)


class TestWorktreeTools(ToolSystemTests):
    def test_worktree_roundtrip(self) -> None:
        enter_out = EnterWorktreeTool.call({"name": "test-tree"}, self.ctx).output
        self.assertIn("test-tree", enter_out["worktreePath"])
        self.assertIsNotNone(self.ctx.worktree_root)
        self.assertEqual(self.ctx.cwd, self.ctx.worktree_root)

        worktree_dir = self.root / ".clawcodex" / "worktrees" / "test-tree"
        self.assertTrue(worktree_dir.exists())

        exit_out = ExitWorktreeTool.call({}, self.ctx).output
        self.assertIn("Exited worktree", exit_out["message"])
        self.assertIsNone(self.ctx.worktree_root)
        self.assertEqual(self.ctx.cwd, self.root)

    def test_worktree_enter_already_in(self) -> None:
        from src.tool_system.errors import ToolPermissionError

        EnterWorktreeTool.call({"name": "first"}, self.ctx)
        with self.assertRaises(ToolPermissionError):
            EnterWorktreeTool.call({"name": "second"}, self.ctx)

    def test_worktree_exit_not_in(self) -> None:
        from src.tool_system.errors import ToolPermissionError

        with self.assertRaises(ToolPermissionError):
            ExitWorktreeTool.call({}, self.ctx)

    def test_worktree_name_validation(self) -> None:
        from src.tool_system.errors import ToolInputError

        with self.assertRaises(ToolInputError):
            EnterWorktreeTool.call({"name": ""}, self.ctx)

        with self.assertRaises(ToolInputError):
            EnterWorktreeTool.call({"name": "invalid name!"}, self.ctx)

        with self.assertRaises(ToolInputError):
            EnterWorktreeTool.call({"name": "a" * 65}, self.ctx)


class TestPlanModeTools(ToolSystemTests):
    def test_plan_mode_roundtrip(self) -> None:
        enter_out = EnterPlanModeTool.call({}, self.ctx).output
        self.assertTrue(self.ctx.plan_mode)
        self.assertIn("Entered plan mode", enter_out["message"])

        exit_out = ExitPlanModeTool.call({}, self.ctx).output
        self.assertFalse(self.ctx.plan_mode)
        self.assertFalse(exit_out["isAgent"])
        self.assertTrue(exit_out["hasTaskTool"])

    def test_plan_mode_exit_with_plan(self) -> None:
        EnterPlanModeTool.call({}, self.ctx)

        plan_content = "# My Plan\n\n- Do something\n- Do something else"
        exit_out = ExitPlanModeTool.call({"plan": plan_content}, self.ctx).output

        self.assertEqual(exit_out["plan"], plan_content)
        self.assertIsNotNone(exit_out["filePath"])

        plan_file = self.root / ".clawcodex" / "plan.md"
        self.assertTrue(plan_file.exists())
        self.assertEqual(plan_file.read_text(encoding="utf-8"), plan_content)

    def test_plan_mode_exit_with_custom_path(self) -> None:
        EnterPlanModeTool.call({}, self.ctx)

        custom_path = self.root / "my-plan.md"
        plan_content = "# Custom Plan"
        exit_out = ExitPlanModeTool.call(
            {"plan": plan_content, "planFilePath": str(custom_path)},
            self.ctx,
        ).output

        self.assertEqual(exit_out["filePath"], str(custom_path))
        self.assertTrue(custom_path.exists())

    def test_plan_mode_exit_not_in_mode(self) -> None:
        from src.tool_system.errors import ToolPermissionError

        with self.assertRaises(ToolPermissionError):
            ExitPlanModeTool.call({}, self.ctx)

    def test_plan_mode_plan_validation(self) -> None:
        from src.tool_system.errors import ToolInputError

        EnterPlanModeTool.call({}, self.ctx)

        with self.assertRaises(ToolInputError):
            ExitPlanModeTool.call({"plan": 123}, self.ctx)

        with self.assertRaises(ToolInputError):
            ExitPlanModeTool.call({"plan": "x", "planFilePath": 123}, self.ctx)


if __name__ == "__main__":
    unittest.main()
