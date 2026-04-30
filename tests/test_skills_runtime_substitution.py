"""Tests for the per-invocation skill prompt-rendering pipeline.

Covers DEV-2: each transform in isolation, the orchestrator combining
them in TS order, the MCP shell-exec security guard, and shell-error
formatting (failures embed inline rather than crash the SkillTool).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.skills.bundled_skills import (
    BundledSkillDefinition,
    clear_bundled_skills,
    register_bundled_skill,
)
from src.skills.create import create_skill
from src.skills.loader import (
    clear_dynamic_skills,
    clear_skill_caches,
    clear_skill_registry,
)
from src.skills.runtime_substitution import (
    find_shell_blocks,
    format_shell_error,
    format_shell_output,
    has_shell_blocks,
    prepend_base_dir_header,
    render_skill_prompt,
    substitute_session_id,
    substitute_skill_dir,
)
from src.tool_system.context import ToolContext
from src.tool_system.tools import SkillTool


# ----------------------------------------------------------------------
# Pure transforms in isolation
# ----------------------------------------------------------------------


class TestPrependBaseDirHeader(unittest.TestCase):
    def test_prepends_when_base_dir_set(self) -> None:
        out = prepend_base_dir_header("body", "/skills/foo")
        self.assertEqual(out, "Base directory for this skill: /skills/foo\n\nbody")

    def test_no_op_when_base_dir_none(self) -> None:
        self.assertEqual(prepend_base_dir_header("body", None), "body")

    def test_no_op_when_base_dir_empty(self) -> None:
        self.assertEqual(prepend_base_dir_header("body", ""), "body")


class TestSubstituteSkillDir(unittest.TestCase):
    def test_replaces_placeholder(self) -> None:
        out = substitute_skill_dir(
            "cd ${CLAUDE_SKILL_DIR}/scripts", "/abs/skill"
        )
        self.assertEqual(out, "cd /abs/skill/scripts")

    def test_replaces_multiple(self) -> None:
        out = substitute_skill_dir(
            "${CLAUDE_SKILL_DIR}/a ${CLAUDE_SKILL_DIR}/b", "/x"
        )
        self.assertEqual(out, "/x/a /x/b")

    def test_normalizes_backslashes(self) -> None:
        out = substitute_skill_dir(
            "${CLAUDE_SKILL_DIR}/script.ps1", r"C:\users\me\skill"
        )
        self.assertEqual(out, "C:/users/me/skill/script.ps1")

    def test_no_op_when_base_dir_missing(self) -> None:
        self.assertEqual(
            substitute_skill_dir("${CLAUDE_SKILL_DIR}", None),
            "${CLAUDE_SKILL_DIR}",
        )


class TestSubstituteSessionId(unittest.TestCase):
    def test_replaces_placeholder(self) -> None:
        out = substitute_session_id("session=${CLAUDE_SESSION_ID}", "abc-123")
        self.assertEqual(out, "session=abc-123")

    def test_unset_id_substitutes_empty(self) -> None:
        # Matches TS' getSessionId() -> '' fallback.
        out = substitute_session_id("session=${CLAUDE_SESSION_ID}!", None)
        self.assertEqual(out, "session=!")


class TestFindShellBlocks(unittest.TestCase):
    def test_finds_fenced_block(self) -> None:
        text = "before\n```!\nls -la\n```\nafter"
        blocks = find_shell_blocks(text)
        self.assertEqual(len(blocks), 1)
        full, cmd, inline = blocks[0]
        self.assertIn("```!", full)
        self.assertEqual(cmd, "ls -la")
        self.assertFalse(inline)

    def test_finds_inline_block(self) -> None:
        blocks = find_shell_blocks("status: !`git status -sb`")
        self.assertEqual(len(blocks), 1)
        _, cmd, inline = blocks[0]
        self.assertEqual(cmd, "git status -sb")
        self.assertTrue(inline)

    def test_inline_requires_whitespace_before_bang(self) -> None:
        # TS' lookbehind blocks `foo!`bar` where ! has no preceding ws.
        # We replicate by anchoring the inline pattern at start-or-ws.
        blocks = find_shell_blocks("foo!`nope`")
        self.assertEqual(blocks, [])

    def test_inline_at_line_start(self) -> None:
        blocks = find_shell_blocks("!`pwd`")
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0][1], "pwd")

    def test_no_blocks(self) -> None:
        self.assertEqual(find_shell_blocks("plain text"), [])

    def test_has_shell_blocks_cheap_check(self) -> None:
        self.assertTrue(has_shell_blocks("```!\nls\n```"))
        self.assertTrue(has_shell_blocks(" !`ls`"))
        self.assertFalse(has_shell_blocks("nothing here"))
        self.assertFalse(has_shell_blocks("foo!`bar`"))


class TestFormatShellOutput(unittest.TestCase):
    def test_stdout_only(self) -> None:
        self.assertEqual(format_shell_output("hello\n", "", inline=False), "hello")

    def test_stderr_block_form(self) -> None:
        out = format_shell_output("ok", "warn!", inline=False)
        self.assertEqual(out, "ok\n[stderr]\nwarn!")

    def test_stderr_inline_form(self) -> None:
        out = format_shell_output("ok", "warn!", inline=True)
        self.assertEqual(out, "ok [stderr: warn!]")

    def test_empty(self) -> None:
        self.assertEqual(format_shell_output("", "", inline=False), "")


class TestFormatShellError(unittest.TestCase):
    def test_block_form(self) -> None:
        self.assertEqual(
            format_shell_error("oops", "```!cmd```", inline=False),
            "[Error]\noops",
        )

    def test_inline_form(self) -> None:
        self.assertEqual(
            format_shell_error("oops", "!`cmd`", inline=True),
            "[Error: oops]",
        )

    def test_accepts_exception(self) -> None:
        out = format_shell_error(RuntimeError("boom"), "x", inline=True)
        self.assertEqual(out, "[Error: boom]")


# ----------------------------------------------------------------------
# Renderer orchestration
# ----------------------------------------------------------------------


class TestRenderSkillPrompt(unittest.TestCase):
    def test_combined_substitutions(self) -> None:
        out = render_skill_prompt(
            body="Base: ${CLAUDE_SKILL_DIR}\nSession: ${CLAUDE_SESSION_ID}",
            args="",
            base_dir="/skills/demo",
            argument_names=[],
            session_id="sess-xyz",
            loaded_from="user",
        )
        # Header prepended
        self.assertTrue(out.startswith("Base directory for this skill: /skills/demo"))
        # ${CLAUDE_SKILL_DIR} substituted
        self.assertIn("Base: /skills/demo", out)
        # ${CLAUDE_SESSION_ID} substituted
        self.assertIn("Session: sess-xyz", out)

    def test_argument_substitution_runs_after_header(self) -> None:
        out = render_skill_prompt(
            body="Hello $name from $1",
            args="alice somewhere",
            base_dir="/s/d",
            argument_names=["name"],
            session_id=None,
            loaded_from="user",
        )
        self.assertIn("Hello alice from somewhere", out)
        # Header is still first
        self.assertTrue(out.startswith("Base directory for this skill: /s/d"))

    def test_arguments_appended_when_no_placeholder(self) -> None:
        out = render_skill_prompt(
            body="Static body",
            args="extra",
            base_dir=None,
            argument_names=[],
            session_id=None,
            loaded_from="user",
        )
        self.assertIn("Static body", out)
        self.assertIn("ARGUMENTS: extra", out)

    def test_no_base_dir_no_header(self) -> None:
        out = render_skill_prompt(
            body="bare body",
            args="",
            base_dir=None,
            argument_names=[],
            session_id=None,
            loaded_from="bundled",
        )
        self.assertNotIn("Base directory for this skill", out)
        self.assertEqual(out, "bare body")

    def test_session_id_unset_substitutes_empty(self) -> None:
        out = render_skill_prompt(
            body="ID=${CLAUDE_SESSION_ID}",
            args="",
            base_dir=None,
            argument_names=[],
            session_id=None,
            loaded_from="user",
        )
        self.assertEqual(out, "ID=")


class TestRenderSkillPromptShellExecution(unittest.TestCase):
    def test_shell_executor_called_for_inline(self) -> None:
        seen: list[tuple[str, bool]] = []

        def fake_exec(cmd: str, inline: bool) -> str:
            seen.append((cmd, inline))
            return "fake-output"

        out = render_skill_prompt(
            body="status: !`git status`",
            args="",
            base_dir=None,
            argument_names=[],
            session_id=None,
            loaded_from="user",
            shell_executor=fake_exec,
        )
        self.assertEqual(seen, [("git status", True)])
        self.assertIn("fake-output", out)
        self.assertNotIn("!`git status`", out)

    def test_shell_executor_called_for_block(self) -> None:
        seen: list[tuple[str, bool]] = []

        def fake_exec(cmd: str, inline: bool) -> str:
            seen.append((cmd, inline))
            return f"<<{cmd}>>"

        out = render_skill_prompt(
            body="```!\nls -la\n```",
            args="",
            base_dir=None,
            argument_names=[],
            session_id=None,
            loaded_from="user",
            shell_executor=fake_exec,
        )
        self.assertEqual(seen, [("ls -la", False)])
        self.assertIn("<<ls -la>>", out)

    def test_mcp_skill_skips_shell_execution(self) -> None:
        # Security boundary — MCP skills come from remote untrusted servers
        # and must never trigger local shell execution.
        called: list[str] = []

        def fake_exec(cmd: str, inline: bool) -> str:
            called.append(cmd)
            return "WOULD-RUN"

        out = render_skill_prompt(
            body="hi !`evil` bye",
            args="",
            base_dir=None,
            argument_names=[],
            session_id=None,
            loaded_from="mcp",
            shell_executor=fake_exec,
        )
        self.assertEqual(called, [])
        self.assertIn("!`evil`", out)  # left intact

    def test_no_executor_leaves_blocks_intact(self) -> None:
        out = render_skill_prompt(
            body="run !`whoami` here",
            args="",
            base_dir=None,
            argument_names=[],
            session_id=None,
            loaded_from="user",
            shell_executor=None,
        )
        self.assertIn("!`whoami`", out)

    def test_executor_exception_renders_inline_error(self) -> None:
        def boom(cmd: str, inline: bool) -> str:
            raise RuntimeError("oops")

        out = render_skill_prompt(
            body="x !`bad` y",
            args="",
            base_dir=None,
            argument_names=[],
            session_id=None,
            loaded_from="user",
            shell_executor=boom,
        )
        # Failures must SURFACE in the rendered prompt, not be silently
        # dropped. They should also not crash the renderer.
        self.assertIn("[Error: oops]", out)
        self.assertNotIn("!`bad`", out)

    def test_transforms_run_in_correct_order(self) -> None:
        # Shell command should see the post-substitution dir, since ${CLAUDE_SKILL_DIR}
        # is replaced before shell exec.
        captured: list[str] = []

        def fake_exec(cmd: str, inline: bool) -> str:
            captured.append(cmd)
            return "ran"

        render_skill_prompt(
            body="check !`stat ${CLAUDE_SKILL_DIR}/x`",
            args="",
            base_dir="/abs/skill",
            argument_names=[],
            session_id=None,
            loaded_from="user",
            shell_executor=fake_exec,
        )
        self.assertEqual(captured, ["stat /abs/skill/x"])


# ----------------------------------------------------------------------
# Acceptance criteria — end-to-end via SkillTool
# ----------------------------------------------------------------------


class TestSkillToolRuntimeIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()

    def tearDown(self) -> None:
        clear_skill_caches()
        clear_dynamic_skills()
        clear_skill_registry()
        clear_bundled_skills()
        self.tmp.cleanup()

    def test_disk_skill_gets_base_dir_header(self) -> None:
        """AC#2: disk-loaded skill prepends the canonical header."""
        skills_dir = self.root / ".claude" / "skills"
        create_skill(
            directory=skills_dir, name="hello", description="say hi", body="Hi!",
        )
        ctx = ToolContext(workspace_root=self.root)
        out = SkillTool.call({"skill": "hello"}, ctx).output
        self.assertTrue(out["success"])
        self.assertTrue(
            out["prompt"].startswith("Base directory for this skill:"),
            f"prompt does not start with header: {out['prompt']!r}",
        )
        self.assertIn(str(skills_dir / "hello"), out["prompt"])
        self.assertIn("Hi!", out["prompt"])

    def test_skill_dir_and_session_substitution(self) -> None:
        """AC#1: ${CLAUDE_SKILL_DIR} and ${CLAUDE_SESSION_ID} both
        substitute in a real disk-loaded skill via SkillTool."""
        skills_dir = self.root / ".claude" / "skills"
        create_skill(
            directory=skills_dir, name="echo",
            description="echo placeholders",
            body="DIR=${CLAUDE_SKILL_DIR}\nSID=${CLAUDE_SESSION_ID}",
        )
        ctx = ToolContext(workspace_root=self.root, session_id="my-session-42")
        out = SkillTool.call({"skill": "echo"}, ctx).output
        self.assertTrue(out["success"])
        self.assertIn(f"DIR={skills_dir / 'echo'}", out["prompt"])
        self.assertIn("SID=my-session-42", out["prompt"])

    def test_bundled_skill_no_header(self) -> None:
        """AC#3: bundled skill without skill_root should NOT get header."""
        register_bundled_skill(
            BundledSkillDefinition(
                name="b1",
                description="bundled",
                get_prompt_for_command=lambda a: f"BUNDLED-PROMPT:{a}",
            )
        )
        ctx = ToolContext(workspace_root=self.root)
        out = SkillTool.call({"skill": "b1", "args": "x"}, ctx).output
        self.assertTrue(out["success"])
        self.assertNotIn("Base directory for this skill", out["prompt"])
        self.assertIn("BUNDLED-PROMPT:x", out["prompt"])

    def test_argument_substitution_with_header(self) -> None:
        """AC#4: arg substitution still works after header prepend."""
        skills_dir = self.root / ".claude" / "skills"
        create_skill(
            directory=skills_dir, name="greet", description="greet",
            arguments=["name"], body="Hello $name from $1",
        )
        ctx = ToolContext(workspace_root=self.root)
        out = SkillTool.call({"skill": "greet", "args": "alice town"}, ctx).output
        self.assertTrue(out["success"])
        self.assertTrue(out["prompt"].startswith("Base directory for this skill"))
        self.assertIn("Hello alice from town", out["prompt"])

    def test_shell_executor_logs_when_no_executor_for_mcp(self) -> None:
        """AC#5: MCP-loaded skills never trigger inline shell execution."""
        # Simulate by directly calling the renderer with loaded_from="mcp"
        # and a tracker executor. The MCP guard runs before any executor
        # call.
        called: list[str] = []
        def trk(cmd: str, inline: bool) -> str:
            called.append(cmd)
            return "x"

        out = render_skill_prompt(
            body="!`whoami`",
            args="",
            base_dir=None,
            argument_names=[],
            session_id=None,
            loaded_from="mcp",
            shell_executor=trk,
        )
        self.assertEqual(called, [])
        self.assertIn("!`whoami`", out)


if __name__ == "__main__":
    unittest.main()
