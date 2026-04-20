from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from src.agent.conversation import Conversation
from src.outputStyles import BUILTIN_OUTPUT_STYLES, load_output_styles_dir, resolve_output_style
from src.providers.base import ChatResponse
from src.tool_system.agent_loop import run_agent_loop
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry


class TestOutputStyles(unittest.TestCase):
    def test_load_output_styles_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "custom.md").write_text("Custom prompt", encoding="utf-8")
            styles = load_output_styles_dir(root)
            self.assertIn("default", styles)
            self.assertIn("custom", styles)
            self.assertEqual(styles["custom"].prompt, "Custom prompt")

    def test_resolve_output_style_fallback(self) -> None:
        style = resolve_output_style("missing")
        self.assertEqual(style.name, "default")
        self.assertEqual(style.prompt, BUILTIN_OUTPUT_STYLES["default"].prompt)

    def test_agent_loop_injects_style_prompt_for_non_anthropic(self) -> None:
        registry = build_default_registry(include_user_tools=False)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            style_dir = root / ".claude" / "output-styles"
            style_dir.mkdir(parents=True, exist_ok=True)
            (style_dir / "custom.md").write_text("Be extra terse.", encoding="utf-8")

            ctx = ToolContext(workspace_root=root)
            ctx.output_style_name = "custom"
            ctx.output_style_dir = style_dir

            conversation = Conversation()
            conversation.add_user_message("hello")

            provider = MagicMock()
            provider.chat.return_value = ChatResponse(
                content="ok",
                model="test",
                usage={"input_tokens": 1, "output_tokens": 1},
                finish_reason="stop",
                tool_uses=None,
            )

            out = run_agent_loop(conversation, provider, registry, ctx, verbose=False)
            self.assertEqual(out.response_text, "ok")
            messages = provider.chat.call_args.args[0]
            self.assertEqual(messages[0]["role"], "system")
            self.assertIn("Be extra terse.", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()

