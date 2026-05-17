"""Tests for REPL functionality."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import tempfile
import json
import threading
import time
from rich.markdown import Markdown

import src.config as config_module
from src.repl import ClawcodexREPL
from src.agent import Session, Conversation
from src.providers.base import ChatMessage, ChatResponse


class TestREPL(unittest.TestCase):
    """Test REPL functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Create a temporary config directory
        self.temp_dir = tempfile.mkdtemp()
        self.config_dir = Path(self.temp_dir) / ".clawcodex"
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Create a test config
        test_config = {
            "default_provider": "glm",
            "providers": {
                "glm": {
                    "api_key": "test_api_key_12345678",
                    "base_url": "https://open.bigmodel.cn/api/paas/v4",
                    "default_model": "glm-4.5"
                }
            }
        }

        config_file = self.config_dir / "config.json"
        with open(config_file, 'w') as f:
            json.dump(test_config, f)

        # Redirect ConfigManager to the test config and drop any cached
        # singleton state. Patching ``get_config_path`` alone is a no-op
        # because the manager reads ``GLOBAL_CONFIG_FILE`` directly.
        self._global_config_patcher = patch.object(
            config_module, "GLOBAL_CONFIG_FILE", config_file
        )
        self._global_config_patcher.start()
        config_module._default_manager = None

    def tearDown(self):
        self._global_config_patcher.stop()
        config_module._default_manager = None

    def test_repl_initialization(self):
        """Test REPL initialization."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create') as mock_session:
                mock_session.return_value = Mock()

                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    repl = ClawcodexREPL(provider_name="glm")
                    self.assertIsNotNone(repl)
                    self.assertEqual(repl.provider_name, "glm")
                    self.assertFalse(repl.stream)

    def test_repl_initialization_with_stream_enabled(self):
        """Test REPL can start with stream mode enabled."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create') as mock_session:
                mock_session.return_value = Mock()

                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    repl = ClawcodexREPL(provider_name="glm", stream=True)
                    self.assertTrue(repl.stream)

    def test_startup_header_contains_logo_and_metadata(self):
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create'):
                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = Mock(return_value=mock_provider)

                    repl = ClawcodexREPL(provider_name="glm")

                    with patch('src.repl.core.Path.cwd', return_value=Path(self.temp_dir)):
                        # Capture stdout to verify fallback path output
                        import io
                        from contextlib import redirect_stdout

                        f = io.StringIO()
                        with redirect_stdout(f):
                            repl._print_startup_header()

                        rendered = f.getvalue()
                        self.assertIn("ClawCodex", rendered)
                        self.assertIn("glm-4.5", rendered)
                        self.assertIn("GLM Provider", rendered)
                        # Path may be truncated, just check start and end parts
                        self.assertTrue(
                            self.temp_dir[:20] in rendered or self.temp_dir[-20:] in rendered
                        )

    def test_handle_command_exit(self):
        """Test /exit command."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create'):
                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    repl = ClawcodexREPL(provider_name="glm")

                    with self.assertRaises(SystemExit):
                        repl.handle_command("/exit")

    def test_handle_command_clear(self):
        """Test /clear command."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create') as mock_session:
                mock_session_instance = Mock()
                mock_session_instance.conversation = Mock()
                mock_session.return_value = mock_session_instance

                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    repl = ClawcodexREPL(provider_name="glm")
                    repl.handle_command("/clear")

                    mock_session_instance.conversation.clear.assert_called_once()

    def test_handle_command_stream_toggle(self):
        """Test /stream command toggles stream mode safely."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create'):
                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    repl = ClawcodexREPL(provider_name="glm")
                    self.assertFalse(repl.stream)

                    repl.handle_command("/stream on")
                    self.assertTrue(repl.stream)

                    repl.handle_command("/stream off")
                    self.assertFalse(repl.stream)

    def test_handle_command_render_last_renders_markdown(self):
        """Test /render-last re-renders the last assistant response."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create') as mock_session_factory:
                mock_session = Mock()
                mock_session.conversation = Conversation()
                mock_session.conversation.add_assistant_message("## Hello\n\n- item")
                mock_session_factory.return_value = mock_session

                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    repl = ClawcodexREPL(provider_name="glm")
                    repl.console.print = Mock()
                    repl.handle_command("/render-last")

                    self.assertTrue(any(
                        args and isinstance(args[0], Markdown)
                        for args, _kwargs in repl.console.print.call_args_list
                    ))

    def test_handle_command_render_last_without_message(self):
        """Test /render-last handles empty history gracefully."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create') as mock_session_factory:
                mock_session = Mock()
                mock_session.conversation = Conversation()
                mock_session_factory.return_value = mock_session

                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    repl = ClawcodexREPL(provider_name="glm")
                    repl.console.print = Mock()
                    repl.handle_command("/render-last")

                    self.assertTrue(any(
                        args and "No assistant response available to render." in str(args[0])
                        for args, _kwargs in repl.console.print.call_args_list
                    ))

    def test_handle_command_tools_lists_registered_tools(self):
        """/tools must call ToolRegistry.list_tools() and print each name.

        Regression: the handler previously called the non-existent
        ``list_specs()`` and crashed with AttributeError on every invocation.
        """
        from types import SimpleNamespace
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create'):
                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    repl = ClawcodexREPL(provider_name="glm")
                    repl.tool_registry = SimpleNamespace(
                        list_tools=lambda: [
                            SimpleNamespace(name="Bash"),
                            SimpleNamespace(name="Read"),
                        ]
                    )
                    repl.console.print = Mock()
                    repl.handle_command("/tools")

                    printed = " ".join(
                        str(args[0]) for args, _ in repl.console.print.call_args_list if args
                    )
                    self.assertIn("Available tools:", printed)
                    self.assertIn("Bash", printed)
                    self.assertIn("Read", printed)

    def test_handle_command_context_resolves_tool_descriptions_to_strings(self):
        """/context must populate tool_schemas with string descriptions.

        Regression: Tool.description is a Callable[[dict], str], not a string.
        The handler previously stuffed the callable straight into the
        ``tool_schemas`` payload; downstream consumers expecting a string
        would receive a function reference.
        """
        from types import SimpleNamespace
        from src.tool_system.build_tool import build_tool
        from src.tool_system.protocol import ToolResult

        def _noop(_input, _ctx):
            return ToolResult(name="X", output={}, is_error=False)

        real_tool = build_tool(
            name="Bash",
            input_schema={"type": "object"},
            call=_noop,
            description=lambda _i: "Run a shell command",
        )

        sentinel = "REGRESSION_SENTINEL_3F2A_style_prompt_was_plumbed_through"
        style_stub = SimpleNamespace(prompt=sentinel)

        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create'):
                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    repl = ClawcodexREPL(provider_name="glm")
                    repl.tool_registry = SimpleNamespace(list_tools=lambda: [real_tool])
                    repl._try_execute_new_command = Mock(return_value=(False, None))
                    repl.console.print = Mock()

                    with patch('src.repl.core.resolve_output_style', return_value=style_stub):
                        repl.handle_command("/context")

                    schemas = repl.command_context.config["tool_schemas"]
                    self.assertEqual(len(schemas), 1)
                    self.assertEqual(schemas[0]["name"], "Bash")
                    self.assertIsInstance(schemas[0]["description"], str)
                    self.assertEqual(schemas[0]["description"], "Run a shell command")
                    self.assertEqual(schemas[0]["input_schema"], {"type": "object"})

                    # Regression: system_prompt must reflect the real prompt,
                    # not be hard-coded to "" (which made the analyzer's
                    # token estimate silently miss the entire system prompt).
                    # The sentinel proves the style_prompt was actually
                    # threaded through build_full_system_prompt.
                    system_prompt = repl.command_context.config["system_prompt"]
                    self.assertIsInstance(system_prompt, str)
                    self.assertIn(sentinel, system_prompt)

    def test_chat_uses_true_api_stream_for_simple_prompt(self):
        """Simple prompts should use provider.chat_stream when stream mode is enabled."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create') as mock_session_factory:
                mock_session = Mock()
                mock_session.conversation = Conversation()
                mock_session_factory.return_value = mock_session

                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider.chat_stream.return_value = iter(["你", "好"])
                    mock_provider_class.return_value = Mock(return_value=mock_provider)

                    repl = ClawcodexREPL(provider_name="glm", stream=True)
                    repl.console.print = Mock()

                    with patch('src.repl.core.run_agent_loop') as mock_agent_loop:
                        repl.chat("你是谁")

                    mock_provider.chat_stream.assert_called_once()
                    mock_agent_loop.assert_not_called()
                    self.assertFalse(any(
                        args and isinstance(args[0], Markdown)
                        for args, _kwargs in repl.console.print.call_args_list
                    ))
                    self.assertEqual(len(mock_session.conversation.messages), 2)
                    self.assertEqual(mock_session.conversation.messages[1].role, "assistant")
                    last_content = mock_session.conversation.messages[1].content
                    if isinstance(last_content, list):
                        self.assertEqual(last_content[0].text, "你好")
                    else:
                        self.assertEqual(last_content, "你好")

    def test_chat_uses_query_engine_for_code_task(self):
        """Code-like prompts use the new QueryEngine path."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create') as mock_session_factory:
                mock_session = Mock()
                mock_session.conversation = Conversation()
                mock_session_factory.return_value = mock_session

                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider.chat_stream_response.side_effect = NotImplementedError()
                    mock_provider.chat.return_value = ChatResponse(
                        content="Done reading README.",
                        model="test",
                        usage={"input_tokens": 10, "output_tokens": 5},
                        finish_reason="end_turn",
                        tool_uses=None,
                    )
                    mock_provider_class.return_value = Mock(return_value=mock_provider)

                    repl = ClawcodexREPL(provider_name="glm", stream=True)
                    repl.console.print = Mock()
                    repl.chat("请读取 README.md 并总结")

                    mock_provider.chat.assert_called()

    def test_chat_uses_query_engine_on_stream_init_failure(self):
        """If real streaming fails, fall back to QueryEngine."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create') as mock_session_factory:
                mock_session = Mock()
                mock_session.conversation = Conversation()
                mock_session_factory.return_value = mock_session

                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider.chat_stream.side_effect = RuntimeError("stream unavailable")
                    mock_provider.chat_stream_response.side_effect = NotImplementedError()
                    mock_provider.chat.return_value = ChatResponse(
                        content="fallback response",
                        model="test",
                        usage={"input_tokens": 10, "output_tokens": 5},
                        finish_reason="end_turn",
                        tool_uses=None,
                    )
                    mock_provider_class.return_value = Mock(return_value=mock_provider)

                    repl = ClawcodexREPL(provider_name="glm", stream=True)
                    repl.console.print = Mock()
                    repl.chat("你好呀")

                    mock_provider.chat_stream.assert_called_once()
                    mock_provider.chat.assert_called()

    def test_handle_command_slash_shows_commands_and_skills(self):
        skills_dir = Path(self.temp_dir) / "skills"
        (skills_dir / "hello").mkdir(parents=True, exist_ok=True)
        (skills_dir / "hello" / "SKILL.md").write_text(
            "---\n"
            "description: say hello\n"
            "---\n"
            "Hello\n",
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
                with patch('src.repl.core.Session.create'):
                    with patch('src.providers.get_provider_class') as mock_provider_class:
                        mock_provider = Mock()
                        mock_provider.model = "glm-4.5"
                        mock_provider_class.return_value = mock_provider

                        repl = ClawcodexREPL(provider_name="glm")
                        repl.console.print = Mock()
                        repl.handle_command("/")
                        rendered = "\n".join(
                            str(args[0]) for args, _kwargs in repl.console.print.call_args_list if args
                        )
                        self.assertIn("Available commands and skills", rendered)
                        self.assertIn("/hello", rendered)

    def test_handle_command_slash_prefix_filters(self):
        skills_dir = Path(self.temp_dir) / "skills"
        (skills_dir / "hello").mkdir(parents=True, exist_ok=True)
        (skills_dir / "hello" / "SKILL.md").write_text(
            "---\n"
            "description: say hello\n"
            "---\n"
            "Hello\n",
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
                with patch('src.repl.core.Session.create'):
                    with patch('src.providers.get_provider_class') as mock_provider_class:
                        mock_provider = Mock()
                        mock_provider.model = "glm-4.5"
                        mock_provider_class.return_value = mock_provider

                        repl = ClawcodexREPL(provider_name="glm")
                        repl.console.print = Mock()
                        repl.handle_command("/he")
                        rendered = "\n".join(
                            str(args[0]) for args, _kwargs in repl.console.print.call_args_list if args
                        )
                        self.assertIn("/help", rendered)
                        self.assertIn("/hello", rendered)

    def test_handle_command_skill_invokes_skill_tool_and_chats_with_prompt(self):
        skills_dir = Path(self.temp_dir) / "skills"
        (skills_dir / "hello").mkdir(parents=True, exist_ok=True)
        (skills_dir / "hello" / "SKILL.md").write_text(
            "---\n"
            "description: say hello\n"
            "arguments: [name]\n"
            "---\n"
            "Hello $name\n",
            encoding="utf-8",
        )
        with patch.dict("os.environ", {"CLAWCODEX_SKILLS_DIR": str(skills_dir)}):
            with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
                with patch('src.repl.core.Session.create'):
                    with patch('src.providers.get_provider_class') as mock_provider_class:
                        mock_provider = Mock()
                        mock_provider.model = "glm-4.5"
                        mock_provider_class.return_value = mock_provider

                        repl = ClawcodexREPL(
                            provider_name="glm",
                            permission_mode="bypassPermissions",
                            is_bypass_permissions_mode_available=True,
                        )
                        repl.chat = Mock()
                        repl.handle_command("/hello bob")
                        args, _kwargs = repl.chat.call_args
                        self.assertIn("Hello bob", args[0])

    def test_save_session(self):
        """Test session saving."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create') as mock_session:
                mock_session_instance = Mock()
                mock_session_instance.session_id = "test_session_123"
                mock_session.return_value = mock_session_instance

                with patch('src.providers.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    repl = ClawcodexREPL(provider_name="glm")
                    repl.save_session()

                    mock_session_instance.save.assert_called_once()

    def test_load_session(self):
        """Test session loading."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create') as mock_session:
                mock_session_instance = Mock()
                mock_session_instance.session_id = "current_session"
                mock_session.return_value = mock_session_instance

                with patch('src.providers.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    with patch('src.repl.core.Session.load') as mock_load:
                        loaded_session = Mock()
                        loaded_session.session_id = "loaded_session_123"
                        loaded_session.provider = "glm"
                        loaded_session.model = "glm-4.5"
                        loaded_session.conversation = Mock()
                        loaded_session.conversation.messages = []
                        mock_load.return_value = loaded_session

                        repl = ClawcodexREPL(provider_name="glm")
                        repl.load_session("loaded_session_123")

                        self.assertEqual(repl.session.session_id, "loaded_session_123")

    def test_load_nonexistent_session(self):
        """Test loading a session that doesn't exist."""
        with patch('src.config.get_config_path', return_value=self.config_dir / "config.json"):
            with patch('src.repl.core.Session.create') as mock_session:
                mock_session_instance = Mock()
                mock_session_instance.session_id = "current_session"
                mock_session.return_value = mock_session_instance

                with patch('src.providers.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider

                    with patch('src.repl.core.Session.load', return_value=None):
                        repl = ClawcodexREPL(provider_name="glm")
                        original_session = repl.session

                        repl.load_session("nonexistent")

                        # Session should not change
                        self.assertEqual(repl.session, original_session)

    def test_permission_prompt_is_serialized(self):
        """Concurrent permission checks should not open overlapping prompts."""
        with patch('src.repl.core.get_provider_config', return_value={
            "api_key": "test_api_key_12345678",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "default_model": "glm-4.5",
        }), patch('src.repl.core.PromptSession') as mock_prompt_session:
            mock_prompt_session.return_value = Mock(prompt=Mock(return_value=""))
            with patch('src.repl.core.Session.create'):
                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider
                    repl = ClawcodexREPL(provider_name="glm")
                    repl.console.print = Mock()

                    in_prompt = 0
                    max_in_prompt = 0
                    counter_lock = threading.Lock()

                    def fake_input(_prompt: str) -> str:
                        nonlocal in_prompt, max_in_prompt
                        with counter_lock:
                            in_prompt += 1
                            if in_prompt > max_in_prompt:
                                max_in_prompt = in_prompt
                        time.sleep(0.03)
                        with counter_lock:
                            in_prompt -= 1
                        return "1"

                    repl._safe_input = fake_input  # type: ignore[assignment]

                    t1 = threading.Thread(
                        target=repl._handle_permission_request,
                        args=("Grep", "Claude wants to use Grep. Allow?", None),
                    )
                    t2 = threading.Thread(
                        target=repl._handle_permission_request,
                        args=("Read", "Claude wants to use Read. Allow?", None),
                    )
                    t1.start()
                    t2.start()
                    t1.join()
                    t2.join()

                    self.assertEqual(max_in_prompt, 1)

    def test_permission_prompt_cached_per_tool(self):
        """After first decision, same tool should not prompt again."""
        with patch('src.repl.core.get_provider_config', return_value={
            "api_key": "test_api_key_12345678",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "default_model": "glm-4.5",
        }), patch('src.repl.core.PromptSession') as mock_prompt_session:
            mock_prompt_session.return_value = Mock(prompt=Mock(return_value=""))
            with patch('src.repl.core.Session.create'):
                with patch('src.repl.core.get_provider_class') as mock_provider_class:
                    mock_provider = Mock()
                    mock_provider.model = "glm-4.5"
                    mock_provider_class.return_value = mock_provider
                    repl = ClawcodexREPL(provider_name="glm")
                    repl.console.print = Mock()

                    prompt_calls = 0

                    def fake_input(_prompt: str) -> str:
                        nonlocal prompt_calls
                        prompt_calls += 1
                        return "1"

                    repl._safe_input = fake_input  # type: ignore[assignment]

                    first = repl._handle_permission_request(
                        "Grep",
                        "Claude wants to use Grep. Allow?",
                        None,
                    )
                    second = repl._handle_permission_request(
                        "Grep",
                        "Claude wants to use Grep. Allow?",
                        None,
                    )

                    self.assertEqual(first, (True, False))
                    self.assertEqual(second, (True, False))
                    self.assertEqual(prompt_calls, 1)


class TestConversation(unittest.TestCase):
    """Test conversation management."""

    def test_add_message(self):
        """Test adding messages to conversation."""
        conv = Conversation()
        conv.add_message("user", "Hello")
        conv.add_message("assistant", "Hi there!")

        self.assertEqual(len(conv.messages), 2)
        self.assertEqual(conv.messages[0].role, "user")
        self.assertEqual(conv.messages[0].content, "Hello")
        self.assertEqual(conv.messages[1].role, "assistant")

    def test_max_history(self):
        """Test max history limit."""
        conv = Conversation(max_history=3)

        # Add 5 messages
        for i in range(5):
            conv.add_message("user", f"Message {i}")

        # Should only keep last 3
        self.assertEqual(len(conv.messages), 3)
        self.assertEqual(conv.messages[0].content, "Message 2")
        self.assertEqual(conv.messages[2].content, "Message 4")

    def test_get_messages(self):
        """Test getting messages in API format."""
        conv = Conversation()
        conv.add_message("user", "Test")
        conv.add_message("assistant", "Response")

        messages = conv.get_messages()

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0], {"role": "user", "content": "Test"})
        self.assertEqual(messages[1]["role"], "assistant")
        content = messages[1]["content"]
        if isinstance(content, list):
            self.assertEqual(content[0]["text"], "Response")
        else:
            self.assertEqual(content, "Response")

    def test_clear(self):
        """Test clearing conversation."""
        conv = Conversation()
        conv.add_message("user", "Test")
        conv.clear()

        self.assertEqual(len(conv.messages), 0)

    def test_serialization(self):
        """Test conversation serialization."""
        conv = Conversation()
        conv.add_message("user", "Test")
        conv.add_message("assistant", "Response")

        # Serialize
        data = conv.to_dict()
        self.assertIn("messages", data)
        self.assertEqual(len(data["messages"]), 2)

        # Deserialize
        conv2 = Conversation.from_dict(data)
        self.assertEqual(len(conv2.messages), 2)
        self.assertEqual(conv2.messages[0].content, "Test")


class TestSession(unittest.TestCase):
    """Test session management."""

    def test_create_session(self):
        """Test session creation."""
        session = Session.create("glm", "glm-4.5")

        self.assertIsNotNone(session.session_id)
        self.assertEqual(session.provider, "glm")
        self.assertEqual(session.model, "glm-4.5")
        self.assertEqual(len(session.conversation.messages), 0)

    def test_session_save_load(self):
        """Test session save and load."""
        with tempfile.TemporaryDirectory() as temp_dir:
            session_dir = Path(temp_dir) / ".clawcodex" / "sessions"

            with patch('src.agent.session.Path.home', return_value=Path(temp_dir)):
                # Create and save
                session = Session.create("glm", "glm-4.5")
                session.conversation.add_message("user", "Test message")
                session.save()

                # Load
                loaded = Session.load(session.session_id)
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.session_id, session.session_id)
                self.assertEqual(len(loaded.conversation.messages), 1)
                self.assertEqual(loaded.conversation.messages[0].content, "Test message")


class TestREPLConversationSanitization(unittest.TestCase):
    """Pins the REPL-side mirror of the engine's image-strip recovery.

    The bug: an image-bearing UserMessage that triggered an
    `image_unsupported` API error stays in `session.conversation` after
    the engine strips its own `_mutable_messages`. The direct-stream
    path (_build_direct_stream_payload) reads
    `session.conversation.messages` directly — so without this mirror,
    a short text-only follow-up routed through `_stream_direct_response`
    would re-trigger the same 404 against Anthropic/Minimax providers.

    Tests `_sanitize_conversation_for_api_error` directly (extracted
    from the engine-loop handler for testability) so a regression
    that removes the strip call or breaks the tag check is caught.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_dir = Path(self.temp_dir) / ".clawcodex"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "config.json"
        test_config = {
            "default_provider": "openrouter",
            "providers": {
                "openrouter": {
                    "api_key": "sk-or-test-key-12345678",
                    "base_url": "https://openrouter.ai/api/v1",
                    "default_model": "deepseek/deepseek-v4-pro",
                }
            }
        }
        with open(self.config_file, 'w') as f:
            json.dump(test_config, f)
        self._global_config_patcher = patch.object(
            config_module, "GLOBAL_CONFIG_FILE", self.config_file
        )
        self._global_config_patcher.start()
        config_module._default_manager = None

    def tearDown(self):
        self._global_config_patcher.stop()
        config_module._default_manager = None

    def _make_repl(self):
        # Build the smallest valid REPL fixture for unit-level testing
        # of the sanitization helper. We need a real `session.conversation`
        # (so the helper has something to mutate); everything else is mocked.
        with patch('src.repl.core.Session.create') as mock_session_create:
            session = Mock()
            session.conversation = Conversation()
            session.session_id = "test-session"
            session.provider = "openrouter"
            session.model = "deepseek/deepseek-v4-pro"
            mock_session_create.return_value = session

            with patch('src.repl.core.get_provider_class') as mock_provider_class:
                mock_provider = Mock()
                mock_provider.model = "deepseek/deepseek-v4-pro"
                mock_provider_class.return_value = mock_provider

                repl = ClawcodexREPL(provider_name="openrouter")
                repl.session = session
                return repl

    def test_image_unsupported_strips_images_from_conversation(self):
        """When an image_unsupported AssistantMessage is passed to the
        sanitizer, image blocks must be removed from
        session.conversation.messages — direct-stream path correctness
        depends on this."""
        from src.types.content_blocks import ImageBlock, TextBlock
        from src.types.messages import AssistantMessage

        repl = self._make_repl()

        repl.session.conversation.add_user_message([
            TextBlock(text="describe this image"),
            ImageBlock(source={
                "type": "base64",
                "media_type": "image/png",
                "data": "AAAA",
            }),
        ])

        err_msg = AssistantMessage(
            content="image not supported text",
            isApiErrorMessage=True,
        )
        err_msg._api_error = "image_unsupported"  # type: ignore[attr-defined]

        repl._sanitize_conversation_for_api_error(err_msg)

        # User's text intent must survive; the image bytes get the
        # "[image]" placeholder (matches strip_images_from_typed_messages
        # contract).
        user_msgs = [m for m in repl.session.conversation.messages
                     if m.role == "user"]
        self.assertEqual(len(user_msgs), 1)
        content = user_msgs[0].content
        self.assertIsInstance(content, list)
        texts = [b.text for b in content if isinstance(b, TextBlock)]
        self.assertIn("describe this image", texts)
        self.assertIn("[image]", texts)
        for block in content:
            self.assertNotIsInstance(block, ImageBlock)

    def test_no_strip_when_error_tag_absent(self):
        """A regular assistant message (or one with a different
        _api_error tag) must NOT strip images — the strip is gated on
        the specific image_unsupported tag, so adjacent errors
        (prompt_too_long, etc.) keep their own recovery semantics."""
        from src.types.content_blocks import ImageBlock, TextBlock
        from src.types.messages import AssistantMessage

        repl = self._make_repl()
        repl.session.conversation.add_user_message([
            TextBlock(text="describe"),
            ImageBlock(source={
                "type": "base64",
                "media_type": "image/png",
                "data": "AAAA",
            }),
        ])

        # Non-error success message: no-op.
        ok_msg = AssistantMessage(content="here is the description")
        repl._sanitize_conversation_for_api_error(ok_msg)
        user_content = repl.session.conversation.messages[0].content
        self.assertTrue(
            any(isinstance(b, ImageBlock) for b in user_content),
            "image must survive when no _api_error tag is set",
        )

        # Different error tag: no-op too (prompt_too_long has its own
        # recovery via reactive_compact and must not trip image strip).
        ptl_msg = AssistantMessage(
            content="prompt too long",
            isApiErrorMessage=True,
        )
        ptl_msg._api_error = "prompt_too_long"  # type: ignore[attr-defined]
        repl._sanitize_conversation_for_api_error(ptl_msg)
        user_content = repl.session.conversation.messages[0].content
        self.assertTrue(
            any(isinstance(b, ImageBlock) for b in user_content),
            "image must survive when a non-image_unsupported tag is set",
        )

    def test_chat_invokes_sanitization_for_image_unsupported(self):
        """Wiring test: the engine-loop handler in REPL.chat MUST call
        ``_sanitize_conversation_for_api_error`` when the engine yields
        an image_unsupported AssistantMessage. Without this test, the
        helper could exist + be unit-tested while the call site is
        silently removed — and the bug would re-appear only at runtime
        on the direct-stream path."""
        from src.types.content_blocks import ImageBlock, TextBlock
        from src.types.messages import AssistantMessage

        repl = self._make_repl()

        # Seed the conversation with an image-bearing user message so
        # if the strip is invoked, we have something to strip.
        repl.session.conversation.add_user_message([
            TextBlock(text="describe"),
            ImageBlock(source={
                "type": "base64",
                "media_type": "image/png",
                "data": "AAAA",
            }),
        ])

        # Mock the engine's submit_message to yield exactly one
        # AssistantMessage tagged image_unsupported. This is the
        # message-shape the production query() function yields for the
        # OpenRouter 404.
        async def fake_submit_message(content):
            err_msg = AssistantMessage(
                content="image not supported text",
                isApiErrorMessage=True,
            )
            err_msg._api_error = "image_unsupported"  # type: ignore[attr-defined]
            yield err_msg

        sanitize_spy = Mock(wraps=repl._sanitize_conversation_for_api_error)
        repl._sanitize_conversation_for_api_error = sanitize_spy  # type: ignore[method-assign]

        # Patch QueryEngine.__init__ -> .submit_message to use our fake.
        # ``chat()`` constructs a QueryEngine inline, so we patch the
        # class to return a mock with our fake_submit_message.
        with patch('src.repl.core.QueryEngine') as mock_engine_class:
            mock_engine = Mock()
            mock_engine.submit_message = fake_submit_message
            mock_engine.reset_abort_controller = Mock()
            mock_engine.get_messages = Mock(return_value=[])
            mock_engine_class.return_value = mock_engine

            # Suppress console output so the test runs silently.
            repl.console.print = Mock()
            # Ensure the test's prompt routes through the QueryEngine
            # path (the direct-stream path doesn't go through the
            # handler we're testing). A long-enough prompt with a
            # code keyword forces _should_try_direct_stream to return
            # False; see core.py:2199-2238.
            repl.chat("please read README.md and summarize it for me carefully")

        sanitize_spy.assert_called()
        # And it must have been called with the tagged AssistantMessage,
        # not some other message — pinning the call-site code path.
        call_args_list = sanitize_spy.call_args_list
        self.assertTrue(
            any(
                len(call.args) >= 1
                and isinstance(call.args[0], AssistantMessage)
                and getattr(call.args[0], "_api_error", None) == "image_unsupported"
                for call in call_args_list
            ),
            "sanitization must be called with the image_unsupported AssistantMessage; "
            f"got call_args_list={call_args_list!r}",
        )


if __name__ == '__main__':
    unittest.main()
