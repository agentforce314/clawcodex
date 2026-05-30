"""statusline — Python port of TS commands/statusline.tsx (type: 'prompt').

A dynamic ``prompt`` command: with no args it defaults the inner prompt to a
fixed phrase; otherwise it embeds the user's trimmed args. The single text block
instructs the model to spawn a ``statusline-setup`` subagent.

Note: that subagent is not yet registered in Python (``src/agent/agent_definitions.py``
ships only general-purpose/Explore/Plan/fork), so the emitted prompt is runtime-inert
today — faithfully matching TS, where the command also only emits naming text and
relies on a separately-registered agent. Porting ``tools/AgentTool/built-in/
statuslineSetup.ts`` is a deferred follow-up.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.agent.constants import AGENT_TOOL_NAME

from .types import CommandContext, PromptCommand

# TS: args.trim() || 'Configure my statusLine from my shell PS1 configuration'
_DEFAULT_STATUSLINE_INSTRUCTION = (
    "Configure my statusLine from my shell PS1 configuration"
)


@dataclass(frozen=True)
class StatuslineCommand(PromptCommand):
    """Overrides get_prompt_for_command for the default-when-empty + f-string
    template TS uses, which the base $ARGUMENTS substitution cannot express.

    Frozen like its base (mirrors MovedToPluginCommand); adds no data fields, so
    it stays comparable by the inherited fields (equality-by-data). Not hashable —
    the base carries mutable list fields (allowed_tools/aliases) — but nothing
    hashes commands; registration/dedupe is by .name.
    """

    async def get_prompt_for_command(
        self, args: str, context: CommandContext
    ) -> list[dict[str, Any]]:
        prompt = args.strip() or _DEFAULT_STATUSLINE_INSTRUCTION
        text = (
            f'Create an {AGENT_TOOL_NAME} with subagent_type "statusline-setup" '
            f'and the prompt "{prompt}"'
        )
        return [{"type": "text", "text": text}]


STATUSLINE_COMMAND = StatuslineCommand(
    name="statusline",
    description="Set up OpenClaude's status line UI",
    progress_message="setting up statusLine",
    content_length=0,  # TS contentLength: 0 (dynamic content)
    source="builtin",  # TS source: 'builtin'
    allowed_tools=[AGENT_TOOL_NAME, "Read(~/**)", "Edit(~/.claude/settings.json)"],
    disable_non_interactive=True,  # TS disableNonInteractive: true
)
