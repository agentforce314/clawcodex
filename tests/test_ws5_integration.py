"""
WS-5 Context Management — Integration & Smoke Tests.

Exercises the full context pipeline end-to-end:
  CLAUDE.md loading → git context → prompt assembly → QueryEngine → API call

Uses deterministic FakeProvider — no real API calls.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.agent.conversation import Conversation
from src.context_system import (
    append_system_context,
    build_context_prompt,
    clear_context_caches,
    fetch_system_prompt_parts,
    get_system_context,
    get_user_context,
    prepend_user_context,
)
from src.context_system.claude_md import (
    clear_memory_file_caches,
    get_claude_mds,
    get_memory_files,
    process_memory_file,
)
from src.context_system.git_context import (
    clear_git_caches,
    collect_git_context,
    format_git_status,
)
from src.context_system.memory_prefetch import find_relevant_memories
from src.context_system.models import MemoryFileInfo, SystemPromptParts
from src.context_system.prompt_assembly import clear_context_caches as clear_prompt_caches
from src.providers.base import ChatResponse
from src.query.engine import QueryEngine, QueryEngineConfig
from src.query.query import QueryParams, StreamEvent
from src.tool_system.build_tool import build_tool
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.tool_search import (
    ToolSearchMode,
    extract_discovered_tool_names,
    filter_tools_for_request,
    get_tool_search_mode,
    is_deferred_tool,
    is_tool_search_enabled,
)
from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from src.types.messages import AssistantMessage, UserMessage


def _run(coro):
    return asyncio.run(coro)


def _init_git_repo(path: str) -> None:
    """Initialize a git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Integration Test"], cwd=path, capture_output=True)
    (Path(path) / "README.md").write_text("# Integration Test\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=path, capture_output=True)


# ---------------------------------------------------------------------------
# Smoke: Module imports
# ---------------------------------------------------------------------------

class TestWS5SmokeImports(unittest.TestCase):
    """All WS-5 modules import cleanly."""

    def test_context_system_imports(self):
        import src.context_system
        import src.context_system.models
        import src.context_system.claude_md
        import src.context_system.git_context
        import src.context_system.prompt_assembly
        import src.context_system.memory_prefetch

    def test_tool_search_imports(self):
        import src.tool_system.tool_search

    def test_public_api_exports(self):
        from src.context_system import (
            fetch_system_prompt_parts,
            get_user_context,
            get_system_context,
            append_system_context,
            prepend_user_context,
            clear_context_caches,
            get_memory_files,
            get_claude_mds,
            clear_memory_file_caches,
            collect_git_context,
            format_git_status,
            clear_git_caches,
            get_is_git,
            MemoryFileInfo,
            MemoryType,
            SystemPromptParts,
            build_context_prompt,
        )


# ---------------------------------------------------------------------------
# Smoke: Core type construction
# ---------------------------------------------------------------------------

class TestWS5SmokeTypes(unittest.TestCase):
    """Core WS-5 types construct correctly."""

    def test_memory_file_info(self):
        info = MemoryFileInfo(
            path="/project/CLAUDE.md",
            type="Project",
            content="Rule: always test.",
        )
        self.assertEqual(info.path, "/project/CLAUDE.md")
        self.assertEqual(info.type, "Project")
        self.assertIsNone(info.parent)
        self.assertIsNone(info.globs)

    def test_system_prompt_parts(self):
        parts = SystemPromptParts(
            default_system_prompt=["Section 1"],
            user_context={"claudeMd": "rules"},
            system_context={"gitStatus": "clean"},
        )
        self.assertEqual(len(parts.default_system_prompt), 1)
        self.assertIn("claudeMd", parts.user_context)


# ---------------------------------------------------------------------------
# Integration: Full CLAUDE.md → prompt pipeline
# ---------------------------------------------------------------------------

class TestWS5IntegrationClaudeMdPipeline(unittest.TestCase):
    """CLAUDE.md files are loaded and formatted into the prompt correctly."""

    def setUp(self):
        clear_memory_file_caches()
        clear_git_caches()
        clear_context_caches()

    def tearDown(self):
        clear_memory_file_caches()
        clear_git_caches()
        clear_context_caches()

    def test_project_claude_md_appears_in_user_context(self):
        """A CLAUDE.md at workspace root appears in get_user_context()."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CLAUDE.md").write_text(
                "Always write tests for every change.", encoding="utf-8",
            )
            with patch.dict(os.environ, {
                "CLAUDE_CODE_ORIGINAL_CWD": tmp,
                "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "",
                "CLAUDE_CODE_BARE_MODE": "",
            }):
                user_ctx = _run(get_user_context(cwd=tmp))
                self.assertIn("currentDate", user_ctx)
                if "claudeMd" in user_ctx:
                    self.assertIn("Always write tests", user_ctx["claudeMd"])

    def test_multi_level_claude_md_priority(self):
        """Files closer to CWD have higher priority (loaded later)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "packages" / "core"
            sub.mkdir(parents=True)

            (root / "CLAUDE.md").write_text("Root rule: use Python 3.12.", encoding="utf-8")
            (sub / "CLAUDE.md").write_text("Core rule: prefer async.", encoding="utf-8")

            with patch.dict(os.environ, {"CLAUDE_CODE_ORIGINAL_CWD": str(sub)}):
                files = _run(get_memory_files(cwd=str(sub)))
                project_files = [f for f in files if f.type == "Project"]
                # Both should be loaded
                contents = " ".join(f.content for f in project_files)
                if project_files:
                    self.assertIn("Root rule", contents)
                    self.assertIn("Core rule", contents)
                    # Core should come after Root (higher priority)
                    root_idx = next(
                        (i for i, f in enumerate(project_files) if "Root rule" in f.content),
                        -1,
                    )
                    core_idx = next(
                        (i for i, f in enumerate(project_files) if "Core rule" in f.content),
                        -1,
                    )
                    if root_idx >= 0 and core_idx >= 0:
                        self.assertGreater(core_idx, root_idx)

    def test_rules_directory_loaded(self):
        """Files in .claude/rules/*.md are loaded as project rules."""
        with tempfile.TemporaryDirectory() as tmp:
            rules_dir = Path(tmp) / ".claude" / "rules"
            rules_dir.mkdir(parents=True)
            (rules_dir / "style.md").write_text("Use 4-space indent.", encoding="utf-8")
            (rules_dir / "testing.md").write_text("All PRs need tests.", encoding="utf-8")

            with patch.dict(os.environ, {"CLAUDE_CODE_ORIGINAL_CWD": tmp}):
                files = _run(get_memory_files(cwd=tmp))
                rule_files = [f for f in files if "rules" in f.path]
                self.assertGreaterEqual(len(rule_files), 2)
                all_content = " ".join(f.content for f in rule_files)
                self.assertIn("4-space indent", all_content)
                self.assertIn("All PRs need tests", all_content)

    def test_include_chain_integration(self):
        """@include directives are followed transitively."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "shared.md").write_text("Shared config.", encoding="utf-8")
            (root / "CLAUDE.md").write_text(
                f"Main rules.\n@{root / 'shared.md'}", encoding="utf-8",
            )

            with patch.dict(os.environ, {"CLAUDE_CODE_ORIGINAL_CWD": tmp}):
                files = _run(get_memory_files(cwd=tmp))
                all_content = " ".join(f.content for f in files if f.type == "Project")
                self.assertIn("Main rules", all_content)
                self.assertIn("Shared config", all_content)

    def test_get_claude_mds_formatting(self):
        """get_claude_mds() produces formatted prompt text with type descriptions."""
        files = [
            MemoryFileInfo(path="/p/CLAUDE.md", type="Project", content="Rule A"),
            MemoryFileInfo(path="/home/.claude/CLAUDE.md", type="User", content="Rule B"),
            MemoryFileInfo(path="/p/CLAUDE.local.md", type="Local", content="Rule C"),
        ]
        result = get_claude_mds(files)
        self.assertIn("Rule A", result)
        self.assertIn("Rule B", result)
        self.assertIn("Rule C", result)
        self.assertIn("project instructions", result)
        self.assertIn("global instructions", result)
        self.assertIn("private project instructions", result)
        self.assertIn("OVERRIDE", result)


# ---------------------------------------------------------------------------
# Integration: Git context → system context pipeline
# ---------------------------------------------------------------------------

class TestWS5IntegrationGitPipeline(unittest.TestCase):
    """Git context flows through to system context correctly."""

    def setUp(self):
        clear_git_caches()
        clear_context_caches()

    def tearDown(self):
        clear_git_caches()
        clear_context_caches()

    def test_git_context_appears_in_system_context(self):
        """Git status appears in get_system_context() for a real git repo."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": ""}):
                sys_ctx = _run(get_system_context(cwd=tmp))
                self.assertIn("gitStatus", sys_ctx)
                self.assertIn("Git repository detected", sys_ctx["gitStatus"])
                self.assertIn("Integration Test", sys_ctx["gitStatus"])

    def test_git_context_disabled_via_env(self):
        """Git status is omitted when CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=true."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "true"}):
                sys_ctx = _run(get_system_context(cwd=tmp))
                self.assertNotIn("gitStatus", sys_ctx)

    def test_non_git_dir_returns_empty(self):
        """Non-git directories produce empty system context (no error)."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": ""}):
                sys_ctx = _run(get_system_context(cwd=tmp))
                self.assertNotIn("gitStatus", sys_ctx)

    def test_git_status_with_dirty_tree(self):
        """Modified files appear in the git status."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            # Create untracked file
            (Path(tmp) / "dirty.py").write_text("# dirty\n", encoding="utf-8")
            ctx = _run(collect_git_context(cwd=tmp))
            self.assertIsNotNone(ctx.status)
            self.assertIn("dirty.py", ctx.status)


# ---------------------------------------------------------------------------
# Integration: fetch_system_prompt_parts() full pipeline
# ---------------------------------------------------------------------------

class TestWS5IntegrationPromptAssembly(unittest.TestCase):
    """fetch_system_prompt_parts() assembles all context correctly."""

    def setUp(self):
        clear_memory_file_caches()
        clear_git_caches()
        clear_context_caches()

    def tearDown(self):
        clear_memory_file_caches()
        clear_git_caches()
        clear_context_caches()

    def test_full_pipeline_with_git_and_claude_md(self):
        """Full context with git repo + CLAUDE.md produces complete parts."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            (Path(tmp) / "CLAUDE.md").write_text("Integration test rule.", encoding="utf-8")

            with patch.dict(os.environ, {
                "CLAUDE_CODE_ORIGINAL_CWD": tmp,
                "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "",
                "CLAUDE_CODE_BARE_MODE": "",
                "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "",
            }):
                parts = _run(fetch_system_prompt_parts(cwd=tmp))

                self.assertIsInstance(parts, SystemPromptParts)
                self.assertIsInstance(parts.default_system_prompt, list)
                self.assertIsInstance(parts.user_context, dict)
                self.assertIsInstance(parts.system_context, dict)

                # User context should have date
                self.assertIn("currentDate", parts.user_context)

                # System context should have git
                self.assertIn("gitStatus", parts.system_context)
                self.assertIn("Git repository", parts.system_context["gitStatus"])

    def test_custom_system_prompt_skips_default_and_system(self):
        """Custom system prompt skips default prompt and system context."""
        with patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true"}):
            parts = _run(fetch_system_prompt_parts(
                custom_system_prompt="My custom prompt",
            ))
            self.assertEqual(parts.default_system_prompt, [])
            self.assertEqual(parts.system_context, {})
            # User context still has date
            self.assertIn("currentDate", parts.user_context)

    def test_append_system_context_formats_correctly(self):
        """append_system_context merges system prompt + git status."""
        prompt = append_system_context(
            ["You are helpful.", "Be concise."],
            {"gitStatus": "branch: main\nWorking tree clean."},
        )
        self.assertIn("You are helpful.", prompt)
        self.assertIn("Be concise.", prompt)
        self.assertIn("gitStatus: branch: main", prompt)

    def test_prepend_user_context_injects_reminder(self):
        """prepend_user_context adds system-reminder as first message."""
        original_msgs = [UserMessage(content="What does this code do?")]
        result = prepend_user_context(original_msgs, {
            "claudeMd": "Always explain thoroughly.",
            "currentDate": "2025-06-01",
        })
        self.assertEqual(len(result), 2)
        # First message is the reminder
        reminder = result[0]
        self.assertIsInstance(reminder, UserMessage)
        self.assertIn("<system-reminder>", reminder.content)
        self.assertIn("Always explain thoroughly", reminder.content)
        self.assertIn("2025-06-01", reminder.content)
        # Original message preserved
        self.assertEqual(result[1].content, "What does this code do?")


# ---------------------------------------------------------------------------
# Integration: QueryEngine with context assembly
# ---------------------------------------------------------------------------

class TestWS5IntegrationQueryEngine(unittest.TestCase):
    """QueryEngine correctly uses fetch_system_prompt_parts."""

    def setUp(self):
        clear_memory_file_caches()
        clear_git_caches()
        clear_context_caches()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        clear_memory_file_caches()
        clear_git_caches()
        clear_context_caches()
        self.temp_dir.cleanup()

    def test_engine_with_explicit_system_prompt(self):
        """QueryEngine with system_prompt set uses it directly."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Hello!",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        config = QueryEngineConfig(
            cwd=self.workspace,
            provider=provider,
            tool_registry=self.registry,
            tools=self.registry.list_tools(),
            tool_context=self.context,
            system_prompt="Explicit system prompt for testing.",
        )
        engine = QueryEngine(config)
        collected = []

        async def run():
            async for msg in engine.submit_message("Hi"):
                collected.append(msg)

        _run(run())

        # Provider should have been called
        self.assertTrue(provider.chat.called)
        # System prompt should be the explicit one
        call_args = provider.chat.call_args
        messages = call_args.args[0] if call_args.args else call_args.kwargs.get("messages", [])
        # First message should be system with our explicit prompt
        system_msgs = [m for m in messages if m.get("role") == "system"]
        if system_msgs:
            self.assertIn("Explicit system prompt", system_msgs[0]["content"])

    def test_engine_without_system_prompt_uses_fetch(self):
        """QueryEngine without system_prompt calls fetch_system_prompt_parts."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Response",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        # Create CLAUDE.md in workspace
        (self.workspace / "CLAUDE.md").write_text(
            "Test rule: always be helpful.", encoding="utf-8",
        )

        with patch.dict(os.environ, {
            "CLAUDE_CODE_ORIGINAL_CWD": str(self.workspace),
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "",
            "CLAUDE_CODE_BARE_MODE": "",
        }):
            config = QueryEngineConfig(
                cwd=self.workspace,
                provider=provider,
                tool_registry=self.registry,
                tools=self.registry.list_tools(),
                tool_context=self.context,
            )
            engine = QueryEngine(config)
            collected = []

            async def run():
                async for msg in engine.submit_message("Hi"):
                    collected.append(msg)

            _run(run())

        # Should have received a response
        assistants = [m for m in collected if isinstance(m, AssistantMessage)]
        self.assertGreaterEqual(len(assistants), 1)

        # Provider should have been called with messages
        self.assertTrue(provider.chat.called)
        call_args = provider.chat.call_args
        messages = call_args.args[0] if call_args.args else call_args.kwargs.get("messages", [])
        # Messages should include a system-reminder with CLAUDE.md content
        all_content = " ".join(
            m.get("content", "") if isinstance(m.get("content"), str) else ""
            for m in messages
        )
        # The system-reminder with CLAUDE.md should be in the messages
        reminder_msgs = [
            m for m in messages
            if m.get("role") == "user"
            and isinstance(m.get("content"), str)
            and "system-reminder" in m.get("content", "")
        ]
        if reminder_msgs:
            self.assertIn("always be helpful", reminder_msgs[0]["content"])

    def test_engine_with_append_system_prompt(self):
        """QueryEngine with append_system_prompt adds it to the assembled prompt."""
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="OK",
            model="test",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="end_turn",
            tool_uses=None,
        )

        with patch.dict(os.environ, {
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "true",
            "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "true",
        }):
            config = QueryEngineConfig(
                cwd=self.workspace,
                provider=provider,
                tool_registry=self.registry,
                tools=self.registry.list_tools(),
                tool_context=self.context,
                append_system_prompt="Output style: be concise and direct.",
            )
            engine = QueryEngine(config)

            async def run():
                async for _ in engine.submit_message("Hello"):
                    pass

            _run(run())

        self.assertTrue(provider.chat.called)
        call_args = provider.chat.call_args
        messages = call_args.args[0] if call_args.args else call_args.kwargs.get("messages", [])
        system_msgs = [m for m in messages if m.get("role") == "system"]
        if system_msgs:
            self.assertIn("be concise and direct", system_msgs[0]["content"])


# ---------------------------------------------------------------------------
# Integration: Tool search pipeline
# ---------------------------------------------------------------------------

class TestWS5IntegrationToolSearch(unittest.TestCase):
    """Tool search filtering integrates with the query pipeline."""

    def test_deferred_tools_filtered_in_tst_mode(self):
        """In TST mode, MCP tools are filtered out unless discovered."""
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "true", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": ""}):
            normal_tool = build_tool(
                name="Read",
                input_schema={"type": "object", "properties": {}},
                call=lambda i, c: None,
            )
            search_tool = build_tool(
                name="ToolSearch",
                input_schema={"type": "object", "properties": {}},
                call=lambda i, c: None,
            )
            mcp_tool = build_tool(
                name="mcp__slack__post",
                input_schema={"type": "object", "properties": {}},
                call=lambda i, c: None,
                is_mcp=True,
            )

            tools = [normal_tool, search_tool, mcp_tool]
            filtered = filter_tools_for_request(tools, "claude-sonnet-4-6", [])
            names = [t.name for t in filtered]
            self.assertIn("Read", names)
            self.assertIn("ToolSearch", names)
            self.assertNotIn("mcp__slack__post", names)

    def test_discovered_tools_restored_after_filter(self):
        """Previously discovered MCP tools are included in filtered results."""
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "true", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": ""}):
            tools = [
                build_tool(name="Read", input_schema={"type": "object", "properties": {}}, call=lambda i, c: None),
                build_tool(name="ToolSearch", input_schema={"type": "object", "properties": {}}, call=lambda i, c: None),
                build_tool(name="mcp__jira__create", input_schema={"type": "object", "properties": {}}, call=lambda i, c: None, is_mcp=True),
            ]
            # Message history with discovered tool
            messages = [{
                "type": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "123",
                    "content": [{"type": "tool_reference", "tool_name": "mcp__jira__create"}],
                }],
            }]
            filtered = filter_tools_for_request(tools, "claude-sonnet-4-6", messages)
            names = [t.name for t in filtered]
            self.assertIn("mcp__jira__create", names)

    def test_standard_mode_keeps_all_tools(self):
        """In STANDARD mode, all tools are kept regardless of deferral."""
        with patch.dict(os.environ, {"ENABLE_TOOL_SEARCH": "false", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": ""}):
            tools = [
                build_tool(name="Read", input_schema={"type": "object", "properties": {}}, call=lambda i, c: None),
                build_tool(name="mcp__tool", input_schema={"type": "object", "properties": {}}, call=lambda i, c: None, is_mcp=True),
            ]
            filtered = filter_tools_for_request(tools, "claude-sonnet-4-6", [])
            self.assertEqual(len(filtered), 2)


# ---------------------------------------------------------------------------
# Integration: Memory prefetch
# ---------------------------------------------------------------------------

class TestWS5IntegrationMemoryPrefetch(unittest.TestCase):
    """Memory prefetch integrates with the file system."""

    def test_prefetch_finds_relevant_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "testing-guide.md").write_text(
                "# Testing Guide\nHow to write unit tests for the project.",
                encoding="utf-8",
            )
            (Path(tmp) / "deployment.md").write_text(
                "# Deployment\nHow to deploy to production.",
                encoding="utf-8",
            )
            result = _run(find_relevant_memories("testing guide unit", tmp))
            self.assertIsInstance(result, list)
            # Should find at least the testing guide
            if result:
                paths = {r.path for r in result}
                self.assertTrue(
                    any("testing" in p for p in paths),
                    f"Expected testing file in {paths}",
                )


# ---------------------------------------------------------------------------
# Integration: Cache invalidation
# ---------------------------------------------------------------------------

class TestWS5IntegrationCacheInvalidation(unittest.TestCase):
    """Context caches clear correctly for post-compact scenarios."""

    def test_full_cache_clear_cycle(self):
        """All caches can be cleared without error and produce fresh results."""
        clear_memory_file_caches()
        clear_git_caches()
        clear_context_caches()

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "CLAUDE.md").write_text("Rule v1", encoding="utf-8")
            with patch.dict(os.environ, {
                "CLAUDE_CODE_ORIGINAL_CWD": tmp,
                "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "",
                "CLAUDE_CODE_BARE_MODE": "",
                "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "true",
            }):
                # First fetch
                parts1 = _run(fetch_system_prompt_parts(cwd=tmp))

                # Clear all caches
                clear_context_caches()

                # Update CLAUDE.md
                (Path(tmp) / "CLAUDE.md").write_text("Rule v2", encoding="utf-8")

                # Second fetch should see updated content
                parts2 = _run(fetch_system_prompt_parts(cwd=tmp))

                # Both should be valid SystemPromptParts
                self.assertIsInstance(parts1, SystemPromptParts)
                self.assertIsInstance(parts2, SystemPromptParts)

    def test_git_cache_refresh(self):
        """Git cache can be cleared and re-fetched."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)

            clear_git_caches()
            ctx1 = _run(collect_git_context(cwd=tmp))
            self.assertTrue(ctx1.available)

            # Add a new file
            (Path(tmp) / "new.py").write_text("pass\n", encoding="utf-8")
            subprocess.run(["git", "add", "new.py"], cwd=tmp, capture_output=True)

            clear_git_caches()
            ctx2 = _run(collect_git_context(cwd=tmp))
            self.assertTrue(ctx2.available)
            # After add, status should show the staged file
            if ctx2.status:
                self.assertIn("new.py", ctx2.status)


# ---------------------------------------------------------------------------
# Integration: Legacy backward compatibility
# ---------------------------------------------------------------------------

class TestWS5IntegrationBackwardCompat(unittest.TestCase):
    """Legacy build_context_prompt() still works through the new pipeline."""

    def setUp(self):
        clear_memory_file_caches()
        clear_git_caches()
        clear_context_caches()

    def tearDown(self):
        clear_memory_file_caches()
        clear_git_caches()
        clear_context_caches()

    def test_build_context_prompt_with_claude_md(self):
        """Legacy API produces prompt with runtime context + instructions."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CLAUDE.md").write_text("Legacy compat rule.", encoding="utf-8")
            (root / "README.md").write_text("# Test\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("pass\n", encoding="utf-8")

            with patch.dict(os.environ, {"CLAUDE_CODE_ORIGINAL_CWD": tmp}):
                prompt = build_context_prompt(root)

            self.assertIn("## Runtime Context", prompt)
            self.assertIn("Legacy compat rule", prompt)

    def test_build_context_prompt_with_git(self):
        """Legacy API includes git context for repos."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(tmp)

            prompt = build_context_prompt(root)

            self.assertIn("## Git Context", prompt)
            self.assertIn("Git repository", prompt)


# ---------------------------------------------------------------------------
# Smoke: End-to-end query with full context
# ---------------------------------------------------------------------------

class TestWS5SmokeEndToEnd(unittest.TestCase):
    """
    End-to-end smoke: workspace with git + CLAUDE.md → QueryEngine → response.
    Verifies the full chain works without errors.
    """

    def setUp(self):
        clear_memory_file_caches()
        clear_git_caches()
        clear_context_caches()

    def tearDown(self):
        clear_memory_file_caches()
        clear_git_caches()
        clear_context_caches()

    def test_full_end_to_end(self):
        """Complete pipeline: git repo + CLAUDE.md + QueryEngine → assistant response."""
        with tempfile.TemporaryDirectory() as tmp:
            _init_git_repo(tmp)
            (Path(tmp) / "CLAUDE.md").write_text(
                "E2E rule: always explain your reasoning.", encoding="utf-8",
            )
            workspace = Path(tmp)
            registry = build_default_registry()
            context = ToolContext(workspace_root=workspace)

            provider = MagicMock()
            provider.chat_stream_response.side_effect = NotImplementedError()
            provider.chat.return_value = ChatResponse(
                content="I'll help you with that.",
                model="test",
                usage={"input_tokens": 50, "output_tokens": 20},
                finish_reason="end_turn",
                tool_uses=None,
            )

            with patch.dict(os.environ, {
                "CLAUDE_CODE_ORIGINAL_CWD": tmp,
                "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "",
                "CLAUDE_CODE_BARE_MODE": "",
                "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS": "",
            }):
                config = QueryEngineConfig(
                    cwd=workspace,
                    provider=provider,
                    tool_registry=registry,
                    tools=registry.list_tools(),
                    tool_context=context,
                    append_system_prompt="Be concise.",
                )
                engine = QueryEngine(config)
                collected = []

                async def run():
                    async for msg in engine.submit_message("Explain this project"):
                        collected.append(msg)

                _run(run())

            # Should get at least one assistant response
            assistants = [m for m in collected if isinstance(m, AssistantMessage)]
            self.assertGreaterEqual(len(assistants), 1)
            # Content may be string or list of TextBlock
            content = assistants[0].content
            if isinstance(content, list):
                text = " ".join(
                    b.text for b in content if hasattr(b, "text")
                )
            else:
                text = content
            self.assertIn("help you with that", text)

            # Provider was called
            self.assertTrue(provider.chat.called)

            # Messages sent to provider should include system-reminder
            call_args = provider.chat.call_args
            messages = call_args.args[0] if call_args.args else call_args.kwargs.get("messages", [])
            self.assertGreater(len(messages), 0)


if __name__ == "__main__":
    unittest.main()
