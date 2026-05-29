"""Tests for the Class A prompt commands ported in Phase 1: /auto-fix and /review.

Ports typescript/src/commands/auto-fix.ts and the /review half of review.ts.
Verifies prompt-text fidelity, $ARGUMENTS interpolation, and command metadata.
Async tests need no decorator — pytest is configured with asyncio_mode="auto".
"""

from __future__ import annotations

import tempfile

from src.command_system.builtins import AUTO_FIX_COMMAND, REVIEW_COMMAND
from src.command_system.engine import create_command_context
from src.command_system.types import CommandType


def _ctx():
    tmp = tempfile.gettempdir()
    return create_command_context(workspace_root=tmp, cwd=tmp)


async def test_auto_fix_prompt_content():
    blocks = await AUTO_FIX_COMMAND.get_prompt_for_command("", _ctx())
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    text = blocks[0]["text"]
    assert "autoFix" in text
    assert ".claude/settings.json" in text


async def test_review_prompt_with_args_interpolates_arguments():
    blocks = await REVIEW_COMMAND.get_prompt_for_command("1234", _ctx())
    text = blocks[0]["text"]
    # Substring (not .endswith): the prompt template has a trailing newline.
    assert "PR number: 1234" in text
    assert "$ARGUMENTS" not in text


async def test_review_prompt_no_args():
    blocks = await REVIEW_COMMAND.get_prompt_for_command("", _ctx())
    text = blocks[0]["text"]
    assert "gh pr list" in text
    assert "PR number:" in text  # placeholder collapses to empty, label remains
    assert "$ARGUMENTS" not in text


def test_class_a_command_metadata():
    for cmd in (AUTO_FIX_COMMAND, REVIEW_COMMAND):
        assert cmd.command_type == CommandType.PROMPT
        assert cmd.source == "builtin"
        assert cmd.disable_model_invocation is False
    assert AUTO_FIX_COMMAND.name == "auto-fix"
    assert REVIEW_COMMAND.name == "review"
