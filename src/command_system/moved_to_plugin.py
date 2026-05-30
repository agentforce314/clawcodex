"""create_moved_to_plugin_command — Python port of TS commands/createMovedToPluginCommand.ts.

Builds a builtin 'prompt' command whose prompt depends on USER_TYPE: ant users get a
static "this command moved to a plugin" message; everyone else dispatches to a private
prompt builder (the command's real implementation while the marketplace is private).
"""
from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Union

from .types import CommandContext, PromptCommand

# (args, context) -> prompt blocks. May be sync or async (TS returns a Promise).
PrivatePromptBuilder = Callable[
    [str, CommandContext],
    Union[list[dict[str, Any]], Awaitable[list[dict[str, Any]]]],
]


def _moved_to_plugin_text(plugin_name: str, plugin_command: str) -> str:
    """Verbatim from createMovedToPluginCommand.ts:48-57."""
    return (
        "This command has been moved to a plugin. Tell the user:\n\n"
        "1. To install the plugin, run:\n"
        f"   openclaude plugin install {plugin_name}@claude-code-marketplace\n\n"
        f"2. After installation, use /{plugin_name}:{plugin_command} to run this command\n\n"
        "3. For more information, see: "
        f"https://github.com/anthropics/claude-code-marketplace/blob/main/{plugin_name}/README.md\n\n"
        "Do not attempt to run the command. Simply inform the user about the plugin "
        "installation."
    )


@dataclass(frozen=True)
class MovedToPluginCommand(PromptCommand):
    """A builtin prompt command gated on USER_TYPE (see createMovedToPluginCommand.ts).

    Frozen like its base; the custom prompt builder is stored as a non-comparing,
    non-repr field (mirrors LocalCommand._call_impl) so the frozen dataclass stays
    hashable/comparable by its data fields.
    """

    plugin_name: str = ""
    plugin_command: str = ""
    _private_builder: Optional[PrivatePromptBuilder] = field(
        default=None, repr=False, compare=False
    )

    async def get_prompt_for_command(
        self, args: str, context: CommandContext
    ) -> list[dict[str, Any]]:
        # TS gates on process.env.USER_TYPE === 'ant' (createMovedToPluginCommand.ts:44).
        if os.environ.get("USER_TYPE") == "ant":
            return [
                {
                    "type": "text",
                    "text": _moved_to_plugin_text(self.plugin_name, self.plugin_command),
                }
            ]
        if self._private_builder is None:
            raise ValueError(f"{self.name}: no private prompt builder configured")
        result = self._private_builder(args, context)
        if inspect.isawaitable(result):  # support async builders (TS returns a Promise)
            result = await result
        return result


def create_moved_to_plugin_command(
    *,
    name: str,
    description: str,
    progress_message: str,
    plugin_name: str,
    plugin_command: str,
    get_prompt_while_marketplace_is_private: PrivatePromptBuilder,
) -> MovedToPluginCommand:
    """Port of createMovedToPluginCommand({...}) — returns a builtin prompt command."""
    return MovedToPluginCommand(
        name=name,
        description=description,
        progress_message=progress_message,
        content_length=0,  # dynamic content (TS contentLength: 0)
        source="builtin",  # TS source: 'builtin'
        plugin_name=plugin_name,
        plugin_command=plugin_command,
        _private_builder=get_prompt_while_marketplace_is_private,
    )
