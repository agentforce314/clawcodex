"""Tests for the fork-subagent helper module.

Mirrors typescript/src/tools/AgentTool/forkSubagent.ts behavior.
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent.constants import (
    FORK_BOILERPLATE_TAG,
    FORK_DIRECTIVE_PREFIX,
    FORK_SUBAGENT_TYPE,
)
from src.agent.fork_subagent import (
    FORK_AGENT,
    FORK_PLACEHOLDER_RESULT,
    build_child_message,
    build_forked_messages,
    build_worktree_notice,
    is_fork_subagent_enabled,
    is_in_fork_child,
)
from src.tool_system.context import ToolContext, ToolUseOptions
from src.types.content_blocks import (
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from src.types.messages import (
    AssistantMessage,
    UserMessage,
    create_user_message,
)


class TestForkConstants(unittest.TestCase):
    """Constants match the TypeScript reference."""

    def test_fork_subagent_type(self) -> None:
        self.assertEqual(FORK_SUBAGENT_TYPE, "fork")

    def test_fork_boilerplate_tag(self) -> None:
        self.assertEqual(FORK_BOILERPLATE_TAG, "fork-boilerplate")

    def test_fork_directive_prefix_matches_ts(self) -> None:
        # TS: typescript/src/constants/xml.ts:66
        self.assertEqual(FORK_DIRECTIVE_PREFIX, "Your directive: ")

    def test_fork_placeholder_result(self) -> None:
        # TS: forkSubagent.ts:93
        self.assertEqual(
            FORK_PLACEHOLDER_RESULT, "Fork started — processing in background"
        )

    def test_fork_agent_type(self) -> None:
        self.assertEqual(FORK_AGENT.agent_type, "fork")

    def test_fork_agent_uses_inherit_model(self) -> None:
        self.assertEqual(FORK_AGENT.model, "inherit")


class TestBuildChildMessage(unittest.TestCase):
    """Boilerplate-wrapped directive matches TS structure."""

    def test_contains_open_and_close_tags(self) -> None:
        out = build_child_message("do thing")
        self.assertIn(f"<{FORK_BOILERPLATE_TAG}>", out)
        self.assertIn(f"</{FORK_BOILERPLATE_TAG}>", out)

    def test_uses_your_directive_prefix(self) -> None:
        out = build_child_message("refactor login flow")
        self.assertIn("Your directive: refactor login flow", out)
        self.assertNotIn("DIRECTIVE: refactor", out)

    def test_contains_rule_about_default_to_forking(self) -> None:
        out = build_child_message("x")
        # The boilerplate calls out the parent's "default to forking"
        # instruction so the child knows to ignore it. Match the substring
        # rather than the surrounding quote/period punctuation, which is
        # part of an English sentence and may be reformatted in the future.
        self.assertIn("default to forking", out)

    def test_contains_scope_format_marker(self) -> None:
        out = build_child_message("x")
        # The TS template requires the response to begin with "Scope:".
        self.assertIn("Scope:", out)
        self.assertIn("Result:", out)
        self.assertIn("Key files:", out)
        self.assertIn("Files changed:", out)
        self.assertIn("Issues:", out)


class TestBuildForkedMessages(unittest.TestCase):
    """Forked message pair construction matches TS algorithm."""

    def test_no_parent_returns_single_user_message(self) -> None:
        out = build_forked_messages("do thing", None)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], UserMessage)

    def test_parent_with_no_tool_use_returns_single_user_message(self) -> None:
        parent = AssistantMessage(content=[TextBlock(text="hello")])
        out = build_forked_messages("do thing", parent)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], UserMessage)

    def test_parent_with_tool_use_clones_assistant_and_emits_placeholders(self) -> None:
        parent = AssistantMessage(
            content=[
                TextBlock(text="planning"),
                ToolUseBlock(id="tu_1", name="Read", input={"path": "/a"}),
                ToolUseBlock(id="tu_2", name="Write", input={"path": "/b", "content": "x"}),
            ]
        )
        out = build_forked_messages("update foo", parent)
        self.assertEqual(len(out), 2)

        # The first message is a cloned assistant — distinct uuid, same blocks.
        cloned = out[0]
        self.assertIsInstance(cloned, AssistantMessage)
        self.assertNotEqual(cloned.uuid, parent.uuid)
        # All original blocks must survive.
        self.assertEqual(len(cloned.content), 3)
        self.assertEqual(cloned.content[1].id, "tu_1")
        self.assertEqual(cloned.content[2].id, "tu_2")

        # Second message: tool_results for every tool_use + the directive text.
        user_msg = out[1]
        self.assertIsInstance(user_msg, UserMessage)
        blocks = list(user_msg.content)
        # 2 placeholders + 1 directive text
        self.assertEqual(len(blocks), 3)

        result_blocks = [b for b in blocks if isinstance(b, ToolResultBlock)]
        self.assertEqual({b.tool_use_id for b in result_blocks}, {"tu_1", "tu_2"})
        for b in result_blocks:
            self.assertEqual(b.content, FORK_PLACEHOLDER_RESULT)

        text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
        self.assertEqual(len(text_blocks), 1)
        self.assertIn(f"<{FORK_BOILERPLATE_TAG}>", text_blocks[0].text)
        self.assertIn("Your directive: update foo", text_blocks[0].text)

    def test_clone_does_not_mutate_parent(self) -> None:
        original_blocks = [
            TextBlock(text="planning"),
            ToolUseBlock(id="tu_1", name="Read", input={"path": "/a"}),
        ]
        parent = AssistantMessage(content=original_blocks)
        original_count = len(parent.content)
        original_uuid = parent.uuid
        _ = build_forked_messages("x", parent)
        self.assertEqual(len(parent.content), original_count)
        self.assertEqual(parent.uuid, original_uuid)


class TestIsInForkChild(unittest.TestCase):
    """Recursion-guard fallback scans message history for the boilerplate tag."""

    def test_empty_messages_returns_false(self) -> None:
        self.assertFalse(is_in_fork_child([]))
        self.assertFalse(is_in_fork_child(None))

    def test_user_message_with_tag_returns_true(self) -> None:
        msg = create_user_message(
            content=[TextBlock(text=f"<{FORK_BOILERPLATE_TAG}> rules ...")]
        )
        self.assertTrue(is_in_fork_child([msg]))

    def test_assistant_message_with_tag_returns_false(self) -> None:
        # Only user messages are scanned, mirroring TS guard.
        msg = AssistantMessage(
            content=[TextBlock(text=f"<{FORK_BOILERPLATE_TAG}>")]
        )
        self.assertFalse(is_in_fork_child([msg]))

    def test_user_message_text_block_without_tag(self) -> None:
        msg = create_user_message(content=[TextBlock(text="just a directive")])
        self.assertFalse(is_in_fork_child([msg]))

    def test_dict_shaped_messages_supported(self) -> None:
        msg = {
            "role": "user",
            "type": "user",
            "content": [{"type": "text", "text": f"...<{FORK_BOILERPLATE_TAG}>..."}],
        }
        self.assertTrue(is_in_fork_child([msg]))


class TestIsForkSubagentEnabled(unittest.TestCase):
    """Feature gate honors env flag and interactivity."""

    def setUp(self) -> None:
        self._original_env = os.environ.get("CLAUDE_FORK_SUBAGENT")
        os.environ.pop("CLAUDE_FORK_SUBAGENT", None)

    def tearDown(self) -> None:
        if self._original_env is None:
            os.environ.pop("CLAUDE_FORK_SUBAGENT", None)
        else:
            os.environ["CLAUDE_FORK_SUBAGENT"] = self._original_env

    def _make_context(self, *, non_interactive: bool) -> ToolContext:
        ctx = ToolContext(workspace_root=Path("/tmp"))
        ctx.options = ToolUseOptions(is_non_interactive_session=non_interactive)
        return ctx

    def test_disabled_when_env_unset(self) -> None:
        ctx = self._make_context(non_interactive=False)
        self.assertFalse(is_fork_subagent_enabled(ctx))

    def test_disabled_when_env_falsey(self) -> None:
        os.environ["CLAUDE_FORK_SUBAGENT"] = "0"
        ctx = self._make_context(non_interactive=False)
        self.assertFalse(is_fork_subagent_enabled(ctx))

    def test_enabled_when_env_truthy_and_interactive(self) -> None:
        os.environ["CLAUDE_FORK_SUBAGENT"] = "1"
        ctx = self._make_context(non_interactive=False)
        self.assertTrue(is_fork_subagent_enabled(ctx))

    def test_disabled_in_non_interactive_session(self) -> None:
        os.environ["CLAUDE_FORK_SUBAGENT"] = "1"
        ctx = self._make_context(non_interactive=True)
        self.assertFalse(is_fork_subagent_enabled(ctx))

    def test_uses_global_state_when_no_context(self) -> None:
        os.environ["CLAUDE_FORK_SUBAGENT"] = "1"
        with patch(
            "src.agent.fork_subagent.get_is_non_interactive_session",
            return_value=False,
        ):
            self.assertTrue(is_fork_subagent_enabled())
        with patch(
            "src.agent.fork_subagent.get_is_non_interactive_session",
            return_value=True,
        ):
            self.assertFalse(is_fork_subagent_enabled())


class TestBuildWorktreeNotice(unittest.TestCase):
    """Worktree notice mentions both paths and isolation."""

    def test_mentions_both_paths(self) -> None:
        out = build_worktree_notice("/repo", "/tmp/wt-1234")
        self.assertIn("/repo", out)
        self.assertIn("/tmp/wt-1234", out)

    def test_mentions_isolation(self) -> None:
        out = build_worktree_notice("/repo", "/tmp/wt-1234")
        self.assertIn("isolated", out.lower())


if __name__ == "__main__":
    unittest.main()
