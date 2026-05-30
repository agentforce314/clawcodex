"""Tests for the statusline builtin (Class-A completion chapter).

Port of ``commands/statusline.tsx`` (type: 'prompt'). statusline is a *dynamic*
prompt command: it overrides ``get_prompt_for_command`` instead of using the base
``$ARGUMENTS`` substitution, because TS's ``args.trim() || <default phrase>`` fallback
and the f-string template around ``AGENT_TOOL_NAME`` are not expressible that way.

The produced text is asserted byte-exactly: ``AGENT_TOOL_NAME == "Agent"`` on both
sides, so the output is identical to TS, not merely internally consistent.
"""
from __future__ import annotations

from pathlib import Path

from src.agent.constants import AGENT_TOOL_NAME
from src.command_system import (
    REMOTE_SAFE_COMMANDS,
    STATUSLINE_COMMAND,
    StatuslineCommand,
    create_command_context,
    get_builtin_commands,
    get_commands,
)
from src.command_system.types import CommandType

# The exact literal TS emits for empty args (note capital `L` in `statusLine` and the
# trailing closing quote). Hardcoded — not built from AGENT_TOOL_NAME — so a change to
# that constant trips test #6's cross-check rather than silently passing here.
EXPECTED_DEFAULT = (
    'Create an Agent with subagent_type "statusline-setup" '
    'and the prompt "Configure my statusLine from my shell PS1 configuration"'
)


async def test_default_empty_args_exact_byte_match(tmp_path):
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    result = await STATUSLINE_COMMAND.get_prompt_for_command("", ctx)

    assert len(result) == 1
    block = result[0]
    assert block["type"] == "text"
    text = block["text"]
    assert text == EXPECTED_DEFAULT
    # Boundary locks: no stray leading/trailing whitespace, prompt's closing quote
    # is the final character (guards the f-string template edges).
    assert text == text.strip()
    assert text.endswith('"')


async def test_whitespace_only_args_uses_default(tmp_path):
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    result = await STATUSLINE_COMMAND.get_prompt_for_command("   ", ctx)
    # strip() or default -> the whitespace-only input collapses to the default phrase.
    assert result[0]["text"] == EXPECTED_DEFAULT


async def test_custom_args_embedded_verbatim(tmp_path):
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    result = await STATUSLINE_COMMAND.get_prompt_for_command("use my zsh theme", ctx)

    text = result[0]["text"]
    assert text == (
        'Create an Agent with subagent_type "statusline-setup" '
        'and the prompt "use my zsh theme"'
    )
    # The default phrase must NOT appear when the user supplied args.
    assert "Configure my statusLine from my shell PS1 configuration" not in text


async def test_args_trimmed_outer_internal_spacing_preserved(tmp_path):
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    result = await STATUSLINE_COMMAND.get_prompt_for_command("  a  b  ", ctx)
    # args.strip() == JS args.trim(): outer whitespace removed, internal run preserved.
    assert result[0]["text"] == (
        'Create an Agent with subagent_type "statusline-setup" and the prompt "a  b"'
    )


async def test_no_argument_substitution_and_no_quote_escaping(tmp_path):
    # Faithfulness: statusline does NOT route through substitute_arguments, so a literal
    # `$ARGUMENTS` (or `$@`) in the args is embedded verbatim, never expanded; and the
    # nested double quotes are NOT escaped (TS embeds raw).
    ctx = create_command_context(workspace_root=tmp_path, cwd=tmp_path)
    result = await STATUSLINE_COMMAND.get_prompt_for_command('x "$ARGUMENTS" y', ctx)

    text = result[0]["text"]
    assert text == (
        'Create an Agent with subagent_type "statusline-setup" '
        'and the prompt "x "$ARGUMENTS" y"'
    )
    assert "\\" not in text  # no backslash escaping introduced


def test_metadata_matches_ts_definition():
    cmd = STATUSLINE_COMMAND
    assert isinstance(cmd, StatuslineCommand)
    assert cmd.name == "statusline"
    assert cmd.description == "Set up OpenClaude's status line UI"
    assert cmd.progress_message == "setting up statusLine"
    assert cmd.command_type == CommandType.PROMPT
    assert cmd.source == "builtin"
    assert cmd.content_length == 0
    assert cmd.disable_non_interactive is True
    assert cmd.allowed_tools == [
        AGENT_TOOL_NAME,
        "Read(~/**)",
        "Edit(~/.claude/settings.json)",
    ]
    # Cross-check: the hardcoded literal above is only correct because of this.
    assert AGENT_TOOL_NAME == "Agent"


def test_registered_in_builtins_and_aggregator():
    builtin_names = [c.name for c in get_builtin_commands()]
    assert "statusline" in builtin_names

    all_names = [c.name for c in get_commands(cwd=str(Path.cwd()))]
    assert "statusline" in all_names


def test_remote_safe_name_now_resolves():
    # safe_commands.py listed "statusline" as a forward-looking REMOTE_SAFE policy name;
    # this chapter makes the name resolve to a real registered command.
    assert "statusline" in REMOTE_SAFE_COMMANDS
    assert "statusline" in {c.name for c in get_builtin_commands()}
