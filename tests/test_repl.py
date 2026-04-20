"""Tests for REPL functionality."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import tempfile
import json
from rich.markdown import Markdown

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
                        self.assertIn("Claw Codex", rendered)
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

                        repl = ClawcodexREPL(provider_name="glm")
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


if __name__ == '__main__':
    unittest.main()
