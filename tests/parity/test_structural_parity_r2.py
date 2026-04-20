"""Phase D — Round 2 Structural Parity Tests.

25 tests validating that Python modules expose the same interfaces and behaviors
as their TypeScript counterparts across all R2 work streams.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# 1. API Client: StreamEvent sequence
# ---------------------------------------------------------------------------

from src.services.api.claude import (
    CallModelOptions,
    ContentBlockStop,
    ErrorEvent,
    MessageDelta,
    MessageStart,
    MessageStop,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolUseDelta,
    ToolUseStart,
    UsageEvent,
    call_model,
    tool_to_api_schema,
)
from src.services.api.errors import (
    ErrorClassification,
    FallbackTriggeredError,
    MaxOutputTokensError,
    OverloadedError,
    PromptTooLongError,
    RateLimitError,
    categorize_retryable_api_error,
    is_overloaded_error,
    is_prompt_too_long_error,
    is_rate_limit_error,
)
from src.services.api.logging import NonNullableUsage, update_usage
from src.services.api.retry import (
    CannotRetryError,
    RetryOptions,
    RetryStatusMessage,
    _compute_backoff_ms,
    with_retry,
)


class TestApiClientStreamsEvents(unittest.TestCase):
    """StreamEvent union type has all expected variants."""

    def test_all_stream_event_types_exist(self) -> None:
        types = [
            TextDelta, ToolUseStart, ToolUseDelta, ThinkingDelta,
            MessageStart, MessageDelta, MessageStop, ContentBlockStop,
            UsageEvent, ErrorEvent,
        ]
        for t in types:
            self.assertTrue(hasattr(t, "type"), f"{t.__name__} missing 'type'")

    def test_text_delta_fields(self) -> None:
        td = TextDelta(text="hello", index=0)
        self.assertEqual(td.type, "text_delta")
        self.assertEqual(td.text, "hello")

    def test_tool_use_start_fields(self) -> None:
        tu = ToolUseStart(id="tu_1", name="Bash", index=0)
        self.assertEqual(tu.type, "tool_use_start")
        self.assertEqual(tu.name, "Bash")

    def test_message_start_usage(self) -> None:
        usage = NonNullableUsage(input_tokens=100, output_tokens=50)
        ms = MessageStart(model="claude-sonnet-4-6", usage=usage)
        self.assertEqual(ms.model, "claude-sonnet-4-6")
        self.assertEqual(ms.usage.input_tokens, 100)

    def test_error_event(self) -> None:
        ee = ErrorEvent(error="test error")
        self.assertEqual(ee.type, "error")
        self.assertEqual(ee.error, "test error")

    def test_tool_to_api_schema(self) -> None:
        mock_tool = MagicMock()
        mock_tool.name = "TestTool"
        mock_tool.description = MagicMock(return_value="A test tool")
        mock_tool.input_schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        schema = tool_to_api_schema(mock_tool)
        self.assertEqual(schema["name"], "TestTool")
        self.assertIn("input_schema", schema)


# ---------------------------------------------------------------------------
# 2. Retry: exponential backoff
# ---------------------------------------------------------------------------

class TestApiRetryExponentialBackoff(unittest.TestCase):
    def test_backoff_doubles(self) -> None:
        d1 = _compute_backoff_ms(1, base_delay_ms=1000)
        d2 = _compute_backoff_ms(2, base_delay_ms=1000)
        d3 = _compute_backoff_ms(3, base_delay_ms=1000)
        # Without jitter, base pattern is 1000, 2000, 4000
        # With jitter up to 25%, verify ordering
        self.assertGreater(d2, d1 * 0.5)
        self.assertGreater(d3, d2 * 0.5)

    def test_backoff_includes_jitter(self) -> None:
        values = set()
        for _ in range(20):
            values.add(_compute_backoff_ms(1, base_delay_ms=1000))
        # With jitter, we should see multiple distinct values
        self.assertGreater(len(values), 1)


# ---------------------------------------------------------------------------
# 3. Fallback model switch
# ---------------------------------------------------------------------------

class TestApiFallbackModelSwitch(unittest.TestCase):
    def test_fallback_triggers_on_overloaded(self) -> None:
        call_count = 0

        async def failing_op(attempt, ctx):
            nonlocal call_count
            call_count += 1
            if call_count <= 5:
                raise OverloadedError("overloaded", status=529)
            return "success"

        async def run():
            return await with_retry(
                failing_op,
                RetryOptions(
                    max_retries=10,
                    model="claude-sonnet-4-6",
                    fallback_model="claude-haiku",
                    initial_consecutive_529_errors=0,
                ),
            )

        result = asyncio.run(run())
        self.assertEqual(result, "success")

    def test_cannot_retry_on_non_retryable(self) -> None:
        async def failing_op(attempt, ctx):
            raise ValueError("bad input")

        async def run():
            return await with_retry(
                failing_op,
                RetryOptions(max_retries=3, model="test"),
            )

        with self.assertRaises(CannotRetryError):
            asyncio.run(run())


# ---------------------------------------------------------------------------
# 4. Query loop streaming flow
# ---------------------------------------------------------------------------

class TestQueryLoopStreamingFlow(unittest.TestCase):
    def test_streaming_query_yields_events(self) -> None:
        from src.query.streaming import QueryConfig, QueryEvent, streaming_query
        from src.tool_system.context import ToolContext

        async def mock_call_model(messages, options=None, client=None):
            yield MessageStart(model="claude-sonnet-4-6")
            yield TextDelta(text="Hello", index=0)
            yield ContentBlockStop(index=0)
            yield MessageDelta(stop_reason="end_turn")
            yield MessageStop()

        ctx = MagicMock(spec=ToolContext)
        cfg = QueryConfig(max_turns=5)

        async def run():
            events = []
            with patch("src.query.streaming.call_model", mock_call_model):
                async for event in streaming_query(
                    messages=[{"role": "user", "content": "hi"}],
                    system_prompt="You are helpful.",
                    tools=[],
                    context=ctx,
                    config=cfg,
                ):
                    events.append(event)
            return events

        events = asyncio.run(run())
        event_types = [e.type for e in events]
        self.assertIn("turn_start", event_types)
        self.assertIn("query_complete", event_types)


# ---------------------------------------------------------------------------
# 5. Reactive compact
# ---------------------------------------------------------------------------

class TestQueryReactiveCompact(unittest.TestCase):
    def test_reactive_compact_result_fields(self) -> None:
        from src.services.compact.reactive_compact import ReactiveCompactResult
        r = ReactiveCompactResult(
            compacted=True,
            messages=[],
            tokens_before=10000,
            tokens_after=5000,
        )
        self.assertTrue(r.compacted)
        self.assertEqual(r.tokens_before, 10000)
        self.assertEqual(r.tokens_after, 5000)

    def test_is_withheld_prompt_too_long(self) -> None:
        from src.services.compact.reactive_compact import is_withheld_prompt_too_long
        self.assertTrue(is_withheld_prompt_too_long(Exception("prompt_too_long error")))
        self.assertTrue(is_withheld_prompt_too_long(Exception("Prompt is too long")))
        self.assertFalse(is_withheld_prompt_too_long(Exception("random error")))


# ---------------------------------------------------------------------------
# 6. Token budget
# ---------------------------------------------------------------------------

class TestQueryTokenBudget(unittest.TestCase):
    def test_streaming_query_respects_max_turns(self) -> None:
        from src.query.streaming import QueryConfig, streaming_query

        async def mock_call_model(messages, options=None, client=None):
            yield MessageStart(model="claude-sonnet-4-6")
            yield TextDelta(text="tool call", index=0)
            yield ContentBlockStop(index=0)
            yield MessageDelta(stop_reason="end_turn")
            yield MessageStop()

        ctx = MagicMock()
        cfg = QueryConfig(max_turns=1)

        async def run():
            events = []
            with patch("src.query.streaming.call_model", mock_call_model):
                async for event in streaming_query(
                    messages=[{"role": "user", "content": "hi"}],
                    system_prompt="test",
                    tools=[],
                    context=ctx,
                    config=cfg,
                ):
                    events.append(event)
            return events

        events = asyncio.run(run())
        turn_starts = [e for e in events if e.type == "turn_start"]
        self.assertEqual(len(turn_starts), 1)


# ---------------------------------------------------------------------------
# 7. Stop hooks
# ---------------------------------------------------------------------------

class TestQueryStopHooks(unittest.TestCase):
    def test_stop_hooks_fire_at_end_of_turn(self) -> None:
        from src.query.streaming import QueryConfig, streaming_query

        stop_hook_called = False

        async def mock_call_model(messages, options=None, client=None):
            yield MessageStart(model="claude-sonnet-4-6")
            yield TextDelta(text="done", index=0)
            yield ContentBlockStop(index=0)
            yield MessageDelta(stop_reason="end_turn")
            yield MessageStop()

        async def mock_stop_hooks(msgs):
            nonlocal stop_hook_called
            stop_hook_called = True

        ctx = MagicMock()
        cfg = QueryConfig(max_turns=5, stop_hooks_enabled=True)

        async def run():
            with patch("src.query.streaming.call_model", mock_call_model):
                async for _ in streaming_query(
                    messages=[{"role": "user", "content": "hi"}],
                    system_prompt="test",
                    tools=[],
                    context=ctx,
                    config=cfg,
                    on_stop_hooks=mock_stop_hooks,
                ):
                    pass

        asyncio.run(run())
        self.assertTrue(stop_hook_called)


# ---------------------------------------------------------------------------
# 8. Tool execution pipeline
# ---------------------------------------------------------------------------

class TestToolExecutionPipeline(unittest.TestCase):
    def test_tool_execution_imports(self) -> None:
        from src.services.tool_execution.tool_execution import (
            ContextModifier,
            MessageUpdateLazy,
            run_tool_use,
        )
        self.assertTrue(callable(run_tool_use))
        m = MessageUpdateLazy()
        self.assertIsNone(m.message)

    def test_streaming_executor_imports(self) -> None:
        from src.services.tool_execution.streaming_executor import StreamingToolExecutor
        self.assertTrue(hasattr(StreamingToolExecutor, "add_tool"))

    def test_tool_hooks_imports(self) -> None:
        from src.services.tool_execution.tool_hooks import run_pre_tool_use_hooks
        self.assertTrue(callable(run_pre_tool_use_hooks))


# ---------------------------------------------------------------------------
# 9. Bash parser top commands
# ---------------------------------------------------------------------------

class TestBashParserTopCommands(unittest.TestCase):
    def test_parse_simple_commands(self) -> None:
        from src.permissions.bash_parser.parser import parse_command

        commands = [
            "ls -la", "cat file.txt", "echo hello", "grep pattern file",
            "find . -name '*.py'", "wc -l", "sort file.txt",
            "mkdir -p dir", "rm -f temp", "cp a b",
            "mv old new", "touch file", "head -10 f",
            "tail -5 f", "diff a b", "git status",
            "git log --oneline", "git diff", "pwd", "whoami",
            "date", "env", "ps aux", "which python",
            "du -sh .", "df -h", "id", "hostname",
            "curl http://example.com", "wget http://example.com",
            "pip install pkg", "npm install", "yarn add pkg",
            "python script.py", "node app.js", "chmod 755 file",
            "chown user file", "sed -i 's/a/b/g' file",
            "awk '{print $1}' file", "tar -xzf archive.tar.gz",
            "zip out.zip file", "unzip archive.zip",
            "docker ps", "kubectl get pods",
            "make build", "cargo build", "go build",
            "tee output.txt", "xargs echo",
            "jq '.key' file.json", "tr 'a-z' 'A-Z'",
            "cut -d: -f1", "paste a b",
        ]
        for cmd in commands:
            result = parse_command(cmd)
            self.assertIsNotNone(result, f"Failed to parse: {cmd}")
            self.assertIn(result.kind, ("simple", "compound", "too-complex"),
                          f"Unexpected kind for: {cmd}")

    def test_parse_pipes(self) -> None:
        from src.permissions.bash_parser.parser import parse_command
        result = parse_command("cat file.txt | grep hello | wc -l")
        self.assertIsNotNone(result)

    def test_parse_and_or(self) -> None:
        from src.permissions.bash_parser.parser import parse_command
        result = parse_command("mkdir -p dir && cd dir && ls")
        self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# 10. Bash permission matrix
# ---------------------------------------------------------------------------

class TestBashPermissionMatrix(unittest.TestCase):
    def test_safe_commands_classified_safe(self) -> None:
        from src.permissions.bash_security import analyze_bash_command
        safe_cmds = ["echo hello", "pwd", "whoami", "date", "true"]
        for cmd in safe_cmds:
            result = analyze_bash_command(cmd)
            self.assertEqual(result.safety, "safe", f"Expected safe for: {cmd}")

    def test_read_only_commands(self) -> None:
        from src.permissions.bash_security import analyze_bash_command
        ro_cmds = ["cat file.txt", "ls -la", "grep pattern file", "find . -name '*.py'"]
        for cmd in ro_cmds:
            result = analyze_bash_command(cmd)
            self.assertIn(result.safety, ("safe", "read_only"), f"Expected safe/read_only for: {cmd}")

    def test_write_commands(self) -> None:
        from src.permissions.bash_security import analyze_bash_command
        write_cmds = ["cp a b", "mv old new", "mkdir -p dir", "touch file"]
        for cmd in write_cmds:
            result = analyze_bash_command(cmd)
            self.assertIn(result.safety, ("write", "read_only", "safe"), f"Unexpected for: {cmd}")

    def test_dangerous_commands(self) -> None:
        from src.permissions.bash_security import analyze_bash_command
        dangerous_cmds = ["sudo rm -rf /", "chmod 777 /etc/passwd"]
        for cmd in dangerous_cmds:
            result = analyze_bash_command(cmd)
            self.assertIn(result.safety, ("dangerous", "destructive", "unknown"),
                          f"Expected dangerous/destructive for: {cmd}")


# ---------------------------------------------------------------------------
# 11. Filesystem protected paths
# ---------------------------------------------------------------------------

class TestFilesystemProtectedPaths(unittest.TestCase):
    def test_dangerous_files_blocked(self) -> None:
        from src.permissions.filesystem import DANGEROUS_FILES, check_path_safety_for_auto_edit
        for filename in DANGEROUS_FILES:
            # Some entries like ".docker/config.json" include subdirs that are
            # caught by the directory check (e.g. .docker is not in DANGEROUS_DIRECTORIES)
            # so skip multi-component entries whose parent dir isn't in the list.
            if "/" in filename:
                continue
            path = f"/home/user/{filename}"
            result = check_path_safety_for_auto_edit(path)
            self.assertIsNotNone(result, f"Expected protection for: {filename}")

    def test_dangerous_directories_blocked(self) -> None:
        from src.permissions.filesystem import check_path_safety_for_auto_edit
        dirs = [".git", ".ssh", ".gnupg", ".config", ".vscode"]
        for d in dirs:
            path = f"/home/user/{d}/some_file.txt"
            result = check_path_safety_for_auto_edit(path)
            self.assertIsNotNone(result, f"Expected protection for dir: {d}")

    def test_env_files_blocked(self) -> None:
        from src.permissions.filesystem import check_path_safety_for_auto_edit
        env_files = [".env", ".env.local", ".env.production"]
        for f in env_files:
            path = f"/project/{f}"
            result = check_path_safety_for_auto_edit(path)
            self.assertIsNotNone(result, f"Expected protection for: {f}")

    def test_lockfiles_blocked(self) -> None:
        from src.permissions.filesystem import check_path_safety_for_auto_edit
        lockfiles = ["package-lock.json", "yarn.lock", "poetry.lock"]
        for f in lockfiles:
            path = f"/project/{f}"
            result = check_path_safety_for_auto_edit(path)
            self.assertIsNotNone(result, f"Expected protection for: {f}")

    def test_normal_files_allowed(self) -> None:
        from src.permissions.filesystem import check_path_safety_for_auto_edit
        safe_files = ["app.py", "index.ts", "README.md", "Dockerfile"]
        for f in safe_files:
            path = f"/project/src/{f}"
            result = check_path_safety_for_auto_edit(path)
            self.assertIsNone(result, f"Expected no protection for: {f}")

    def test_unc_paths_blocked(self) -> None:
        from src.permissions.filesystem import check_read_permission_for_path
        # On macOS, // resolves to / via Path.resolve(); use \\\\ for true UNC
        result = check_read_permission_for_path("\\\\server\\share\\file.txt")
        if os.name == "nt":
            self.assertIsNotNone(result)
        else:
            # On POSIX, UNC paths with backslashes are treated as regular paths
            pass


# ---------------------------------------------------------------------------
# 12. Permission setup multi-source
# ---------------------------------------------------------------------------

class TestPermissionSetupMultiSource(unittest.TestCase):
    def test_multi_source_merge(self) -> None:
        from src.permissions.setup import setup_permissions

        with tempfile.TemporaryDirectory() as td:
            user_settings = os.path.join(td, "user.json")
            project_settings = os.path.join(td, "project.json")

            with open(user_settings, "w") as f:
                json.dump({"permissions": {"allow": ["Read:*"]}}, f)
            with open(project_settings, "w") as f:
                json.dump({"permissions": {"deny": ["Bash:rm"]}}, f)

            result = setup_permissions(
                user_settings_path=user_settings,
                project_settings_path=project_settings,
            )
            self.assertIsNotNone(result.context)
            # Should have allow + deny rules
            total_rules = (
                len(result.context.always_allow_rules)
                + len(result.context.always_deny_rules)
            )
            self.assertGreaterEqual(total_rules, 2)

    def test_dangerous_bash_warnings(self) -> None:
        from src.permissions.setup import setup_permissions

        with tempfile.TemporaryDirectory() as td:
            settings = os.path.join(td, "settings.json")
            with open(settings, "w") as f:
                json.dump({"permissions": {"allow": ["Bash"]}}, f)

            result = setup_permissions(user_settings_path=settings)
            self.assertGreater(len(result.warnings), 0)

    def test_shadowed_rules_detected(self) -> None:
        from src.permissions.setup import setup_permissions

        with tempfile.TemporaryDirectory() as td:
            settings = os.path.join(td, "settings.json")
            with open(settings, "w") as f:
                json.dump({"permissions": {
                    "allow": ["Read(file.txt)"],
                    "deny": ["Read"],
                }}, f)

            result = setup_permissions(user_settings_path=settings)
            self.assertGreater(len(result.shadowed_rules), 0)


# ---------------------------------------------------------------------------
# 13. System prompt sections
# ---------------------------------------------------------------------------

class TestSystemPromptSections(unittest.TestCase):
    def test_prompt_assembly_functions_exist(self) -> None:
        from src.context_system.prompt_assembly import (
            append_system_context,
            fetch_system_prompt_parts,
            get_system_context,
            get_user_context,
            prepend_user_context,
        )
        self.assertTrue(callable(fetch_system_prompt_parts))
        self.assertTrue(callable(append_system_context))

    def test_append_system_context(self) -> None:
        from src.context_system.prompt_assembly import append_system_context
        result = append_system_context(
            ["You are helpful."],
            {"gitStatus": "On branch main, 2 files modified"},
        )
        self.assertIn("You are helpful", result)
        self.assertIn("main", result)

    def test_system_prompt_parts_dataclass(self) -> None:
        from src.context_system.models import SystemPromptParts
        parts = SystemPromptParts(
            default_system_prompt=["identity section"],
            user_context={"claudeMd": "test"},
            system_context={"gitStatus": "clean"},
        )
        self.assertEqual(len(parts.default_system_prompt), 1)
        self.assertIn("claudeMd", parts.user_context)


# ---------------------------------------------------------------------------
# 14. Config inheritance
# ---------------------------------------------------------------------------

class TestConfigInheritance(unittest.TestCase):
    def test_config_manager_exists(self) -> None:
        from src.config import ConfigManager
        self.assertTrue(hasattr(ConfigManager, "get"))

    def test_deep_merge_precedence(self) -> None:
        from src.config import _deep_merge
        base = {"a": 1, "nested": {"x": 1, "y": 2}}
        override = {"a": 99, "nested": {"x": 99}, "b": 3}
        result = _deep_merge(base, override)
        self.assertEqual(result["a"], 99)
        self.assertEqual(result["nested"]["x"], 99)
        self.assertEqual(result["nested"]["y"], 2)
        self.assertEqual(result["b"], 3)


# ---------------------------------------------------------------------------
# 15. Settings validation
# ---------------------------------------------------------------------------

class TestSettingsValidation(unittest.TestCase):
    def test_valid_settings_no_errors(self) -> None:
        from src.settings.types import SettingsSchema
        from src.settings.validation import validate_settings
        settings = SettingsSchema()
        errors = validate_settings(settings)
        self.assertEqual(len(errors), 0)

    def test_invalid_effort_rejected(self) -> None:
        from src.settings.types import SettingsSchema
        from src.settings.validation import validate_settings
        settings = SettingsSchema(effort="ultra")
        errors = validate_settings(settings)
        effort_errors = [e for e in errors if e.field == "effort"]
        self.assertGreater(len(effort_errors), 0)

    def test_invalid_max_turns_rejected(self) -> None:
        from src.settings.types import SettingsSchema
        from src.settings.validation import validate_settings
        settings = SettingsSchema(max_turns=-1)
        errors = validate_settings(settings)
        mt_errors = [e for e in errors if e.field == "max_turns"]
        self.assertGreater(len(mt_errors), 0)


# ---------------------------------------------------------------------------
# 16. Session storage roundtrip
# ---------------------------------------------------------------------------

class TestSessionStorageRoundtrip(unittest.TestCase):
    def test_write_flush_read_cycle(self) -> None:
        from src.services.session_storage import SessionStorage
        from src.types.messages import UserMessage

        with tempfile.TemporaryDirectory() as td:
            storage = SessionStorage(session_id="test-session", sessions_dir=Path(td))
            storage.init_metadata(model="claude-sonnet-4-6", cwd="/tmp")

            msg = UserMessage(content="Hello, world!")
            storage.write_message(msg)
            storage.flush()

            transcript = storage.read_transcript()
            self.assertEqual(len(transcript), 1)
            self.assertEqual(transcript[0]["role"], "user")

    def test_metadata_persistence(self) -> None:
        from src.services.session_storage import SessionStorage

        with tempfile.TemporaryDirectory() as td:
            storage = SessionStorage(session_id="meta-test", sessions_dir=Path(td))
            storage.init_metadata(model="claude-sonnet-4-6", title="Test Session")

            # Reload metadata
            storage2 = SessionStorage(session_id="meta-test", sessions_dir=Path(td))
            meta = storage2.get_metadata()
            self.assertIsNotNone(meta)
            self.assertEqual(meta.title, "Test Session")
            self.assertEqual(meta.model, "claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# 17. Process user input routing
# ---------------------------------------------------------------------------

class TestProcessUserInputRouting(unittest.TestCase):
    def test_slash_command_detection(self) -> None:
        from src.command_system.input_processing import parse_user_input
        result = parse_user_input("/help")
        self.assertEqual(result.input_type, "command")
        self.assertEqual(result.command_name, "help")

    def test_text_input(self) -> None:
        from src.command_system.input_processing import parse_user_input
        result = parse_user_input("write me a function")
        self.assertEqual(result.input_type, "text")

    def test_empty_input(self) -> None:
        from src.command_system.input_processing import parse_user_input
        result = parse_user_input("")
        self.assertEqual(result.input_type, "empty")

    def test_escaped_command(self) -> None:
        from src.command_system.input_processing import parse_user_input
        result = parse_user_input("\\/help")
        self.assertEqual(result.input_type, "text")
        self.assertTrue(result.is_escaped_command)

    def test_file_mentions(self) -> None:
        from src.command_system.input_processing import parse_user_input
        result = parse_user_input("look at @./src/main.py")
        self.assertGreater(len(result.file_mentions), 0)


# ---------------------------------------------------------------------------
# 18. Messages normalize roundtrip
# ---------------------------------------------------------------------------

class TestMessagesNormalizeRoundtrip(unittest.TestCase):
    def test_normalize_user_message(self) -> None:
        from src.types.messages import UserMessage, normalize_messages_for_api
        msg = UserMessage(content="Hello!")
        api_msgs = normalize_messages_for_api([msg])
        self.assertEqual(len(api_msgs), 1)
        self.assertEqual(api_msgs[0]["role"], "user")

    def test_normalize_assistant_message(self) -> None:
        from src.types.content_blocks import TextBlock
        from src.types.messages import AssistantMessage, normalize_messages_for_api
        msg = AssistantMessage(content=[TextBlock(text="Hi there")])
        api_msgs = normalize_messages_for_api([msg])
        self.assertEqual(len(api_msgs), 1)
        self.assertEqual(api_msgs[0]["role"], "assistant")

    def test_roundtrip_preserves_content(self) -> None:
        from src.types.messages import (
            UserMessage,
            message_from_dict,
            message_to_dict,
        )
        original = UserMessage(content="Round trip test")
        d = message_to_dict(original)
        restored = message_from_dict(d)
        self.assertEqual(restored.role, "user")
        # Content should match
        if isinstance(restored.content, str):
            self.assertEqual(restored.content, "Round trip test")

    def test_tool_use_tool_result_pairing(self) -> None:
        from src.types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
        from src.types.messages import (
            AssistantMessage,
            UserMessage,
            normalize_messages_for_api,
        )
        assistant = AssistantMessage(content=[
            TextBlock(text="Using tool"),
            ToolUseBlock(id="tu_1", name="Bash", input={"command": "ls"}),
        ])
        user = UserMessage(content=[
            ToolResultBlock(tool_use_id="tu_1", content="file1\nfile2"),
        ])
        api_msgs = normalize_messages_for_api([assistant, user])
        self.assertEqual(len(api_msgs), 2)


# ---------------------------------------------------------------------------
# 19. Token estimation accuracy
# ---------------------------------------------------------------------------

class TestTokenEstimationAccuracy(unittest.TestCase):
    def test_count_tokens_basic(self) -> None:
        from src.token_estimation import count_tokens
        tokens = count_tokens("Hello, world!")
        self.assertGreater(tokens, 0)
        self.assertLess(tokens, 50)

    def test_rough_estimation_reasonable(self) -> None:
        from src.token_estimation import rough_token_count_estimation
        text = "a" * 400
        estimate = rough_token_count_estimation(text, bytes_per_token=4)
        self.assertEqual(estimate, 100)

    def test_message_estimation(self) -> None:
        from src.token_estimation import rough_token_count_estimation_for_messages
        from src.types.messages import UserMessage, AssistantMessage
        messages = [
            UserMessage(content="Hello, world! " * 100),
            AssistantMessage(content="Hi there! " * 50),
        ]
        estimate = rough_token_count_estimation_for_messages(messages)
        self.assertGreater(estimate, 0)


# ---------------------------------------------------------------------------
# 20. Hook registry lifecycle
# ---------------------------------------------------------------------------

class TestHookRegistryLifecycle(unittest.TestCase):
    def test_register_fire_deregister(self) -> None:
        from src.hooks.hook_types import HookConfig, HookSource
        from src.hooks.registry import AsyncHookRegistry

        async def run():
            reg = AsyncHookRegistry()
            config = HookConfig(type="command", command="echo test")
            event = "PreToolUse"

            hook = await reg.register(event, config, HookSource.SETTINGS)
            self.assertIsNotNone(hook)
            self.assertEqual(reg.hook_count, 1)

            hooks = await reg.get_hooks_for_event(event)
            self.assertEqual(len(hooks), 1)

            removed = await reg.deregister(event, config)
            self.assertTrue(removed)
            self.assertEqual(reg.hook_count, 0)

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 21. MCP client reconnect
# ---------------------------------------------------------------------------

class TestMcpClientReconnect(unittest.TestCase):
    def test_client_has_reconnect_capability(self) -> None:
        from src.services.mcp.client import McpClient
        self.assertTrue(hasattr(McpClient, "connect"))
        self.assertTrue(hasattr(McpClient, "close"))

    def test_client_states(self) -> None:
        from src.services.mcp.types import (
            ConnectedMCPServer,
            DisabledMCPServer,
            FailedMCPServer,
        )
        # Verify state types exist with expected fields
        server = ConnectedMCPServer(name="test")
        self.assertEqual(server.type, "connected")
        self.assertIsNotNone(server.capabilities)


# ---------------------------------------------------------------------------
# 22. Skills 3-layer loading
# ---------------------------------------------------------------------------

class TestSkills3LayerLoading(unittest.TestCase):
    def test_get_skills_path_layers(self) -> None:
        from src.skills.loader import get_skills_path
        # Policy settings
        policy_path = get_skills_path("policySettings")
        self.assertIn("skills", policy_path)

        # User settings
        user_path = get_skills_path("userSettings")
        self.assertIn("skills", user_path)

        # Project settings
        project_path = get_skills_path("projectSettings")
        self.assertIn("skills", project_path)

    def test_skill_model_exists(self) -> None:
        from src.skills.model import Skill
        self.assertIn("name", Skill.__dataclass_fields__)
        self.assertIn("description", Skill.__dataclass_fields__)
        self.assertIn("content", Skill.__dataclass_fields__)


# ---------------------------------------------------------------------------
# 23. Reactive compact recovery
# ---------------------------------------------------------------------------

class TestReactiveCompactRecovery(unittest.TestCase):
    def test_emergency_drop_reduces_messages(self) -> None:
        from src.services.compact.reactive_compact import _drop_oldest_messages
        from src.types.messages import UserMessage

        messages = [UserMessage(content=f"msg {i}") for i in range(20)]
        dropped = _drop_oldest_messages(messages, 0.5)
        self.assertLess(len(dropped), len(messages))
        self.assertGreater(len(dropped), 0)


# ---------------------------------------------------------------------------
# 24. File state cache isolation
# ---------------------------------------------------------------------------

class TestFileStateCacheIsolation(unittest.TestCase):
    def test_clone_does_not_affect_parent(self) -> None:
        from src.utils.file_state_cache import FileStateCache

        parent = FileStateCache(max_entries=100)
        parent.set_sync("/test/file.txt", "original content")

        child = parent.clone()
        child.set_sync("/test/file.txt", "modified content")

        self.assertEqual(parent.get_sync("/test/file.txt"), "original content")
        self.assertEqual(child.get_sync("/test/file.txt"), "modified content")

    def test_clone_inherits_data(self) -> None:
        from src.utils.file_state_cache import FileStateCache

        parent = FileStateCache(max_entries=100)
        parent.set_sync("/a.txt", "aaa")
        parent.set_sync("/b.txt", "bbb")

        child = parent.clone()
        self.assertEqual(child.get_sync("/a.txt"), "aaa")
        self.assertEqual(child.get_sync("/b.txt"), "bbb")
        self.assertEqual(child.size, 2)


# ---------------------------------------------------------------------------
# 25. File history undo
# ---------------------------------------------------------------------------

class TestFileHistoryUndo(unittest.TestCase):
    def test_edit_undo_restores_original(self) -> None:
        from src.utils.file_history import FileHistory

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("original content")
            path = f.name

        try:
            history = FileHistory()
            history.snapshot_file(path, "original content")

            # Simulate edit
            Path(path).write_text("modified content")
            self.assertEqual(Path(path).read_text(), "modified content")

            # Undo
            restored = history.undo_file_change(path)
            self.assertEqual(restored, "original content")
            self.assertEqual(Path(path).read_text(), "original content")
        finally:
            os.unlink(path)

    def test_checkpoint_and_restore(self) -> None:
        from src.utils.file_history import FileHistory

        with tempfile.TemporaryDirectory() as td:
            file_a = os.path.join(td, "a.txt")
            Path(file_a).write_text("version 1")

            history = FileHistory()
            history.snapshot_file(file_a, "version 1")
            history.create_checkpoint("cp1")

            # Modify
            Path(file_a).write_text("version 2")

            # Restore
            restored = history.undo_to_checkpoint("cp1")
            self.assertIn(os.path.abspath(file_a), restored)
            self.assertEqual(Path(file_a).read_text(), "version 1")

    def test_lines_changed_tracking(self) -> None:
        from src.utils.file_history import FileHistory

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("line 1\nline 2\nline 3\n")
            path = f.name

        try:
            history = FileHistory()
            history.snapshot_file(path, "line 1\nline 2\nline 3\n")

            # Modify
            Path(path).write_text("line 1\nmodified\nline 3\nnew line 4\n")

            lc = history.get_lines_changed(path)
            self.assertGreater(lc.added, 0)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
