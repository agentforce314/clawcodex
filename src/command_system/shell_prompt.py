"""Shell-exec-at-prompt-build for slash commands.

Python port of typescript/src/utils/promptShellExecution.ts (executeShellCommandsInPrompt)
plus the BashTool-backed executor pattern from src/tool_system/tools/skill.py
(_make_shell_executor). Reuses the public shell-block primitives already ported in
src/skills/runtime_substitution.py (find_shell_blocks / format_shell_output /
format_shell_error) rather than re-deriving the regexes.

Two divergences from TS, both deliberate and matching the already-shipped skills path:
  * No permission gate. TS runs ``hasPermissionsToUseTool`` and BLOCKS any command not
    matched by ``alwaysAllowRules.command`` (throws MalformedCommandError, block never
    runs). Python's ``BashTool.call()`` bypasses the registry gate and runs every
    detected block unconditionally under the ToolContext's permission mode
    (bypassPermissions by default). The parsed ``allowed_tools`` list is accepted for
    parity but not enforced yet.
  * No abort on failure. TS throws and aborts the whole prompt build; Python embeds the
    formatted error inline (``[Error: …]``) and continues, returning a complete prompt
    with visible error text (DEV-2: surface as visible errors, not silent drops, without
    crashing). See src/skills/runtime_substitution.py:140-157.
"""
from __future__ import annotations

import logging
from typing import Callable

from src.skills.runtime_substitution import (
    find_shell_blocks,
    format_shell_error,
    format_shell_output,
)

logger = logging.getLogger(__name__)

# (command, inline) -> rendered text to splice in. Identical contract to
# runtime_substitution.ShellExecutor.
ShellExecutor = Callable[[str, bool], str]


def execute_shell_commands_in_prompt(
    text: str,
    *,
    shell_executor: ShellExecutor,
    slash_command_name: str = "",
) -> str:
    """Replace embedded shell blocks in ``text`` with their executed output.

    Port of executeShellCommandsInPrompt (promptShellExecution.ts:73). Scans for
    ``` ```! ... ``` ``` (fenced) and ``!`...``` (inline) forms via
    ``find_shell_blocks``, runs each through ``shell_executor`` (which formats its own
    success/error text), and splices the result back in.

    Execution is sequential in ``find_shell_blocks`` order (TS uses ``Promise.all``);
    this is collision-safe because ``str.replace(full_match, replacement, 1)`` targets
    the first occurrence and the detected ``full_match`` strings do not nest. Unlike TS
    — which raises MalformedCommandError on permission/exec failure and aborts the build
    — a crashing executor is caught here and rendered inline so prompt-build never dies.
    """
    blocks = find_shell_blocks(text)
    if not blocks:
        return text
    result = text
    for full_match, command, inline in blocks:
        try:
            replacement = shell_executor(command, inline)
        except Exception as exc:  # noqa: BLE001 — surface anything, never crash prompt build
            logger.exception(
                "shell executor crashed for %s command %r", slash_command_name, command
            )
            replacement = format_shell_error(exc, full_match, inline=inline)
        result = result.replace(full_match, replacement, 1)
    return result


def make_bash_shell_executor(
    tool_context,  # src.tool_system.context.ToolContext
    allowed_tools: list[str] | None,
    *,
    slash_command_name: str,
) -> ShellExecutor:
    """Return a BashTool-backed ShellExecutor. Mirrors skill.py:_make_shell_executor.

    ``allowed_tools`` is accepted for TS parity (TS injects it as
    ``alwaysAllowRules.command`` for the call) but is NOT enforced — the Python
    ``BashTool.call()`` path bypasses the registry permission gate and runs under the
    ToolContext's permission mode (bypassPermissions by default). Precise injection is
    deferred, identical to the documented skill limitation. ``BashTool`` is imported
    lazily so importing this module never pulls ``tool_system`` onto the
    ``command_system`` import chain.
    """
    from src.tool_system.tools.bash import BashTool  # lazy: keep tool_system off import chain

    _ = allowed_tools  # acknowledged; precise injection deferred

    def _exec(command: str, inline: bool) -> str:
        try:
            tr = BashTool.call({"command": command}, tool_context)
        except Exception as exc:  # noqa: BLE001 — surface every failure inline
            return format_shell_error(exc, command, inline=inline)

        output = tr.output if isinstance(tr.output, dict) else {}
        stdout = str(output.get("stdout", ""))
        stderr = str(output.get("stderr", ""))
        exit_code = output.get("exit_code")

        # Non-zero exit: embed the failure text inline (matches TS' ShellError) but keep
        # going so the rest of the prompt still renders.
        if isinstance(exit_code, int) and exit_code != 0:
            err_text = format_shell_output(stdout, stderr, inline=inline)
            err_text = err_text or f"command failed (exit {exit_code})"
            return format_shell_error(err_text, command, inline=inline)

        if tr.is_error:
            err_text = (
                format_shell_output(stdout, stderr, inline=inline)
                or output.get("error")
                or "command failed"
            )
            return format_shell_error(str(err_text), command, inline=inline)

        return format_shell_output(stdout, stderr, inline=inline)

    return _exec
