"""Per-invocation transforms applied to a skill's markdown body.

These mirror the four transforms TS performs inside
``createSkillCommand.getPromptForCommand`` (loadSkillsDir.ts:344-399):

    1. Prepend ``Base directory for this skill: <root>\\n\\n`` if the
       skill carries a ``skill_root``/``base_dir``.
    2. Apply ``substitute_arguments`` (named placeholders, ``$N``,
       ``$ARGUMENTS`` shorthand, optional append-on-no-placeholder).
    3. Replace ``${CLAUDE_SKILL_DIR}`` with the resolved skill directory.
       Backslashes get normalized to forward slashes (Windows compat with
       embedded shell injections).
    4. Replace ``${CLAUDE_SESSION_ID}`` with the active session id.
    5. Execute embedded ``!`...`` and `````! ... ````` shell blocks
       via the supplied ``shell_executor``. Skipped entirely for skills
       loaded from MCP (security boundary mirroring TS' ``loadedFrom !==
       'mcp'`` guard).

The transforms live in this file as pure functions (one per step) plus a
single orchestrator, ``render_skill_prompt``, that wires them together.
The orchestrator takes a pluggable ``shell_executor`` callable so the
SkillTool can pass a BashTool-backed implementation while unit tests can
inject a fake (or pass ``None`` to skip shell execution).
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional, Sequence

from .argument_substitution import substitute_arguments

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Shell-block patterns (mirroring promptShellExecution.ts)
# ----------------------------------------------------------------------

# `````!\\n<command>\\n````` — multi-line fenced form. Capture is the inner
# command with surrounding whitespace stripped at use-site.
_BLOCK_PATTERN = re.compile(r"```!\s*\n?([\s\S]*?)\n?```")

# ``!`<command>``` — inline form. Requires whitespace or start-of-line
# before the ``!`` to avoid false positives inside other inline-code spans
# (e.g. ```foo`!`bar```) and shell variable references like ``$!``.
_INLINE_PATTERN = re.compile(r"(?:^|(?<=\s))!`([^`]+)`", re.MULTILINE)


# ----------------------------------------------------------------------
# Pure transforms (each independently testable)
# ----------------------------------------------------------------------


def prepend_base_dir_header(content: str, base_dir: Optional[str]) -> str:
    """Prepend the canonical ``Base directory ...`` header.

    No-op when ``base_dir`` is empty/None — matches TS behavior where
    bundled skills without a shipped ``files`` directory get no header.
    """
    if not base_dir:
        return content
    return f"Base directory for this skill: {base_dir}\n\n{content}"


def substitute_skill_dir(content: str, base_dir: Optional[str]) -> str:
    """Replace every ``${CLAUDE_SKILL_DIR}`` with the skill root.

    Backslashes are flipped to forward slashes so embedded shell commands
    (``!`cat ${CLAUDE_SKILL_DIR}/script.sh```) don't treat them as escape
    sequences on Windows. Mirrors the TS ``process.platform === 'win32'``
    branch but applies unconditionally — flipping ``\\`` to ``/`` is safe
    on POSIX paths since absolute POSIX paths never contain ``\\``.
    """
    if not base_dir:
        return content
    skill_dir = base_dir.replace("\\", "/") if "\\" in base_dir else base_dir
    return content.replace("${CLAUDE_SKILL_DIR}", skill_dir)


def substitute_session_id(content: str, session_id: Optional[str]) -> str:
    """Replace ``${CLAUDE_SESSION_ID}`` with the active session id.

    Unknown / unset session id → empty string substitution (matches TS
    behavior when ``getSessionId()`` returns falsy).
    """
    return content.replace("${CLAUDE_SESSION_ID}", session_id or "")


# ----------------------------------------------------------------------
# Shell-block detection + execution helpers
# ----------------------------------------------------------------------


def find_shell_blocks(content: str) -> list[tuple[str, str, bool]]:
    """Return ``[(full_match, command, inline)]`` for both block forms.

    ``inline`` is ``True`` for ``!`...```, ``False`` for fenced `````! ... `````.
    Fenced blocks are scanned unconditionally; the inline scan is gated on
    the cheap ``"!`" in content`` check (mirrors the TS micro-opt: 93% of
    skills have no inline form, and the inline regex with lookbehind is
    significantly slower than the fenced one).
    """
    out: list[tuple[str, str, bool]] = []
    for m in _BLOCK_PATTERN.finditer(content):
        cmd = (m.group(1) or "").strip()
        if cmd:
            out.append((m.group(0), cmd, False))
    if "!`" in content:
        for m in _INLINE_PATTERN.finditer(content):
            cmd = (m.group(1) or "").strip()
            if cmd:
                out.append((m.group(0), cmd, True))
    return out


def has_shell_blocks(content: str) -> bool:
    """Cheap check for whether ``content`` contains any shell-exec form."""
    if "```!" in content:
        return True
    if "!`" in content and _INLINE_PATTERN.search(content) is not None:
        return True
    return False


def format_shell_output(stdout: str, stderr: str, *, inline: bool) -> str:
    """Format BashTool output the way TS' ``formatBashOutput`` does."""
    parts: list[str] = []
    if stdout and stdout.strip():
        parts.append(stdout.strip())
    if stderr and stderr.strip():
        if inline:
            parts.append(f"[stderr: {stderr.strip()}]")
        else:
            parts.append(f"[stderr]\n{stderr.strip()}")
    return (" " if inline else "\n").join(parts)


def format_shell_error(
    error: BaseException | str,
    pattern: str,
    *,
    inline: bool,
) -> str:
    """Format an exception/timeout the way TS' ``formatBashError`` does.

    Unlike TS (which raises ``MalformedCommandError`` and lets the caller
    re-throw), the Python port embeds the formatted error text inline in
    the rendered prompt. This satisfies the DEV-2 requirement that shell
    failures "surface as visible errors in the rendered prompt, not
    silent drops" without crashing the SkillTool call.
    """
    msg = str(error) if not isinstance(error, str) else error
    if inline:
        return f"[Error: {msg}]"
    return f"[Error]\n{msg}"


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------


# Signature: (command:str, inline:bool) -> rendered text to splice in.
# Returning a string lets the executor format errors uniformly without
# the renderer needing to know about the underlying tool.
ShellExecutor = Callable[[str, bool], str]


def render_skill_prompt(
    *,
    body: str,
    args: str | None,
    base_dir: Optional[str],
    argument_names: Sequence[str] = (),
    session_id: Optional[str] = None,
    loaded_from: Optional[str] = None,
    slash_command_name: str = "",
    shell_executor: ShellExecutor | None = None,
) -> str:
    """Apply every per-invocation transform in TS order.

    Order is fixed (and matches TS):
        1. base-dir prepend
        2. argument substitution
        3. ``${CLAUDE_SKILL_DIR}``
        4. ``${CLAUDE_SESSION_ID}``
        5. embedded shell execution (skipped for ``loaded_from == 'mcp'``)

    ``shell_executor`` is the only side-effecting hook. When ``None`` (or
    when the skill is MCP-loaded), step 5 is a no-op and any embedded
    shell blocks survive verbatim in the output. The SkillTool wires a
    BashTool-backed executor; tests can inject fakes.
    """
    content = body or ""

    # 1. Base-dir header
    content = prepend_base_dir_header(content, base_dir)

    # 2. Arguments. ``append_if_no_placeholder=True`` matches TS and the
    # existing Python behavior — non-placeholder skills still receive the
    # raw args appended as ``ARGUMENTS: <args>``.
    content = substitute_arguments(
        content,
        args,
        append_if_no_placeholder=True,
        argument_names=list(argument_names),
    )

    # 3. ${CLAUDE_SKILL_DIR}
    content = substitute_skill_dir(content, base_dir)

    # 4. ${CLAUDE_SESSION_ID}
    content = substitute_session_id(content, session_id)

    # 5. Shell exec — guarded on MCP source (security boundary). MCP
    # skills come from remote untrusted servers; their markdown bodies
    # must never trigger local shell execution.
    if loaded_from == "mcp":
        if has_shell_blocks(content):
            logger.debug(
                "skill %r is MCP-loaded; skipping inline shell execution "
                "for %d blocks",
                slash_command_name,
                len(find_shell_blocks(content)),
            )
        return content

    if shell_executor is None:
        if has_shell_blocks(content):
            logger.debug(
                "skill %r contains inline shell blocks but no executor "
                "was provided; leaving them as-is",
                slash_command_name,
            )
        return content

    blocks = find_shell_blocks(content)
    if not blocks:
        return content

    for full_match, command, inline in blocks:
        try:
            replacement = shell_executor(command, inline)
        except Exception as exc:  # noqa: BLE001 — we want to surface anything
            logger.exception(
                "shell executor crashed for skill %r command %r",
                slash_command_name,
                command,
            )
            replacement = format_shell_error(exc, full_match, inline=inline)
        # Use a lambda for the replacement to bypass re-style $-expansion
        # in str.replace? Python's ``str.replace`` doesn't interpret
        # backreferences, so a plain string is safe — but we limit to one
        # replacement per call so multiple identical blocks can each get
        # their own (potentially different) executor result.
        content = content.replace(full_match, replacement, 1)

    return content
