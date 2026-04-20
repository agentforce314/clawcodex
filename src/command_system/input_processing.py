"""User input processing matching TypeScript utils/input.ts and commands/parseInput.ts.

Handles:
- Command detection and routing
- File path detection and @-mention expansion
- URL detection and attachment
- Multi-line input handling
- Input history management
- Special escape sequences
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .registry import CommandRegistry
from .types import Command

# Regex patterns
_COMMAND_RE = re.compile(r"^/([a-zA-Z][a-zA-Z0-9_-]*)(?:\s+(.*))?$", re.DOTALL)
# Match any ``@path`` token (including ``@relative/path`` without ``./`` prefix)
# to line up with ``typescript/src/utils/attachments.ts`` (regex
# ``/(^|\s)@([^\s]+)\b/g``). We keep trailing punctuation characters out of the
# capture so ``"see @foo/bar."`` extracts ``foo/bar``. A leading ``@scope``
# (e.g. ``@anthropic-ai/sdk``) is only treated as a path mention when it contains
# a path separator — otherwise it's just a username-style mention and ignored.
_FILE_MENTION_RE = re.compile(
    r"(?:^|(?<=\s))@(?!\")([^\s,;:\"'`\[\](){}]+)"
)

# Match ``@agent-<type>`` and ``@"<type> (agent)"`` mentions, mirroring
# ``extractAgentMentions`` in ``typescript/src/utils/attachments.ts``. Both
# quoted and unquoted variants are supported; the captured group is the
# agent-type string (minus the ``agent-`` prefix for the unquoted form).
_AGENT_MENTION_UNQUOTED_RE = re.compile(
    r"(?:^|(?<=\s))@(agent-[\w:.@\-]+)"
)
_AGENT_MENTION_QUOTED_RE = re.compile(
    r"(?:^|(?<=\s))@\"([\w:.@\-]+) \(agent\)\""
)
_URL_RE = re.compile(
    r"https?://[^\s<>\"'`\[\](){}]+",
    re.IGNORECASE,
)
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
_ESCAPE_RE = re.compile(r"^\\(/)")  # Escaped slash


@dataclass
class ParsedInput:
    """Result of parsing user input."""
    raw: str
    input_type: str  # "command" | "text" | "empty" | "multiline"
    command_name: str = ""
    command_args: str = ""
    text: str = ""
    file_mentions: list[str] = field(default_factory=list)
    url_mentions: list[str] = field(default_factory=list)
    image_paths: list[str] = field(default_factory=list)
    is_escaped_command: bool = False


def parse_user_input(text: str, *, cwd: str | None = None) -> ParsedInput:
    """Parse user input into structured form.

    Detects:
    - Commands (starting with /)
    - Escaped commands (starting with \\/)
    - File mentions (@path)
    - URL mentions
    - Image paths
    - Empty input
    """
    if not text or not text.strip():
        return ParsedInput(raw=text, input_type="empty")

    stripped = text.strip()

    # Escaped command (\\/ → treat as text)
    if stripped.startswith("\\/"):
        unescaped = stripped[1:]  # Remove the backslash
        return ParsedInput(
            raw=text,
            input_type="text",
            text=unescaped,
            is_escaped_command=True,
            file_mentions=_extract_file_mentions(unescaped, cwd),
            url_mentions=_extract_urls(unescaped),
            image_paths=_extract_image_paths(unescaped, cwd),
        )

    # Command detection
    match = _COMMAND_RE.match(stripped)
    if match:
        cmd_name = match.group(1)
        cmd_args = (match.group(2) or "").strip()
        return ParsedInput(
            raw=text,
            input_type="command",
            command_name=cmd_name,
            command_args=cmd_args,
            text=stripped,
        )

    # Regular text
    return ParsedInput(
        raw=text,
        input_type="text",
        text=stripped,
        file_mentions=_extract_file_mentions(stripped, cwd),
        url_mentions=_extract_urls(stripped),
        image_paths=_extract_image_paths(stripped, cwd),
    )


def _extract_file_mentions(text: str, cwd: str | None = None) -> list[str]:
    """Extract ``@path`` mentions from text.

    Mirrors the behaviour of
    ``extractAtMentionedFiles`` / ``processAtMentionedFiles`` in
    ``typescript/src/utils/attachments.ts``: paths without a leading
    ``/``/``./``/``../``/``~`` are resolved relative to ``cwd`` (so
    ``@demos/minecraft_v2`` picks up the workspace folder of the same
    name). Bare ``@foo`` words are ignored when they don't look like a path
    — we require either an absolute marker or a path separator to avoid
    accidentally expanding e.g. ``@claude`` or ``@alice``.
    """
    mentions: list[str] = []
    for match in _FILE_MENTION_RE.finditer(text):
        path_str = match.group(1).rstrip(".,!?:;\"'`)]}")
        if not path_str:
            continue
        looks_like_path = (
            path_str.startswith(("/", "~", "./", "../"))
            or "/" in path_str
            or "." in path_str
        )
        if not looks_like_path:
            continue
        expanded = os.path.expanduser(path_str)
        if not os.path.isabs(expanded) and cwd:
            expanded = os.path.join(cwd, expanded)
        mentions.append(expanded)
    return mentions


def _extract_urls(text: str) -> list[str]:
    """Extract URLs from text."""
    return _URL_RE.findall(text)


# ---------------------------------------------------------------------------
# @path expansion to context attachments
# ---------------------------------------------------------------------------


_MAX_DIR_ENTRIES = 1000


def expand_at_mentions(
    text: str,
    *,
    cwd: str | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Resolve ``@path`` mentions and build context attachments.

    Mirrors ``processAtMentionedFiles`` in
    ``typescript/src/utils/attachments.ts``: if a mention resolves to a
    directory we build a ``Listed directory`` attachment containing its
    entries (up to 1000); if it resolves to a readable file we attach the
    file's contents verbatim. The returned ``text`` is left unchanged — the
    caller prepends / appends the attachments before sending to the model.
    """
    cwd = cwd or os.getcwd()
    seen: set[str] = set()
    attachments: list[dict[str, str]] = []

    for match in _FILE_MENTION_RE.finditer(text):
        raw = match.group(1).rstrip(".,!?:;\"'`)]}")
        if not raw:
            continue
        # Skip bare @word mentions (no path separator / dot / home marker).
        if not (
            raw.startswith(("/", "~", "./", "../"))
            or "/" in raw
            or "." in raw
        ):
            continue

        expanded = os.path.expanduser(raw)
        if not os.path.isabs(expanded):
            expanded = os.path.abspath(os.path.join(cwd, expanded))
        if expanded in seen:
            continue
        seen.add(expanded)

        try:
            if os.path.isdir(expanded):
                entries = sorted(os.listdir(expanded))
                truncated = len(entries) > _MAX_DIR_ENTRIES
                shown = entries[:_MAX_DIR_ENTRIES]
                if truncated:
                    shown.append(
                        f"\u2026 and {len(entries) - _MAX_DIR_ENTRIES} more entries"
                    )
                display_path = os.path.relpath(expanded, cwd)
                attachments.append(
                    {
                        "kind": "directory",
                        "path": expanded,
                        "display_path": display_path,
                        "content": "\n".join(shown),
                    }
                )
            elif os.path.isfile(expanded):
                try:
                    with open(expanded, "r", encoding="utf-8", errors="replace") as fh:
                        data = fh.read()
                except OSError:
                    continue
                display_path = os.path.relpath(expanded, cwd)
                attachments.append(
                    {
                        "kind": "file",
                        "path": expanded,
                        "display_path": display_path,
                        "content": data,
                    }
                )
        except OSError:
            continue

    return text, attachments


def format_at_mention_attachments(attachments: list[dict[str, str]]) -> str:
    """Render attachments produced by :func:`expand_at_mentions` and
    :func:`expand_agent_mentions` as a single string ready to be prepended to
    the user message. Empty input returns ``""``.
    """
    if not attachments:
        return ""
    blocks: list[str] = []
    for att in attachments:
        kind = att.get("kind")
        if kind == "directory":
            blocks.append(
                f"<system-reminder>\n"
                f"Listed directory {att['display_path']}/:\n"
                f"{att['content']}\n"
                f"</system-reminder>"
            )
        elif kind == "file":
            blocks.append(
                f"<system-reminder>\n"
                f"Contents of {att['display_path']}:\n"
                f"```\n{att['content']}\n```\n"
                f"</system-reminder>"
            )
        elif kind == "agent_mention":
            # Mirrors ``typescript/src/utils/messages.ts`` ``agent_mention``
            # case: the reminder nudges the model to delegate to the named
            # agent via the Agent tool rather than replying inline.
            blocks.append(
                f"<system-reminder>\n"
                f"The user has expressed a desire to invoke the agent "
                f"\"{att['agent_type']}\". Please invoke the agent "
                f"appropriately using the Agent tool, passing in the required "
                f"context to it.\n"
                f"</system-reminder>"
            )
    return "\n\n".join(blocks)


def expand_agent_mentions(
    text: str,
    agents: list[Any] | None,
) -> list[dict[str, str]]:
    """Find ``@agent-<type>`` mentions and build ``agent_mention`` attachments.

    Mirrors ``processAgentMentions`` in
    ``typescript/src/utils/attachments.ts``: each mention that resolves to a
    known agent type produces a single attachment; unknown agents are
    silently dropped so stray ``@agent-foo`` text in prompts doesn't pollute
    the model's context with misleading reminders.
    """
    if not text or not agents:
        return []

    known_types: set[str] = set()
    for agent in agents:
        agent_type = getattr(agent, "agent_type", None) or (
            agent.get("agent_type") if isinstance(agent, dict) else None
        )
        if isinstance(agent_type, str) and agent_type:
            known_types.add(agent_type)

    if not known_types:
        return []

    seen: set[str] = set()
    attachments: list[dict[str, str]] = []

    for match in _AGENT_MENTION_UNQUOTED_RE.finditer(text):
        raw = match.group(1)
        agent_type = raw[len("agent-"):] if raw.startswith("agent-") else raw
        if agent_type in seen or agent_type not in known_types:
            continue
        seen.add(agent_type)
        attachments.append({"kind": "agent_mention", "agent_type": agent_type})

    for match in _AGENT_MENTION_QUOTED_RE.finditer(text):
        agent_type = match.group(1)
        if agent_type in seen or agent_type not in known_types:
            continue
        seen.add(agent_type)
        attachments.append({"kind": "agent_mention", "agent_type": agent_type})

    return attachments


def _extract_image_paths(text: str, cwd: str | None = None) -> list[str]:
    """Extract image file paths from mentions."""
    images: list[str] = []
    for mention in _extract_file_mentions(text, cwd):
        ext = os.path.splitext(mention)[1].lower()
        if ext in _IMAGE_EXTENSIONS:
            images.append(mention)
    return images


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def validate_input(text: str, *, max_length: int = 1_000_000) -> tuple[bool, str]:
    """Validate user input.

    Returns (is_valid, error_message).
    """
    if not text:
        return True, ""  # Empty is valid (will be handled as empty type)

    if len(text) > max_length:
        return False, f"Input too long: {len(text)} chars (max {max_length})"

    return True, ""


# ---------------------------------------------------------------------------
# Input history
# ---------------------------------------------------------------------------


class InputHistory:
    """Track user input history for navigation and recall."""

    def __init__(self, max_entries: int = 1000) -> None:
        self._entries: list[str] = []
        self._max_entries = max_entries
        self._cursor: int = -1

    def add(self, text: str) -> None:
        """Add an entry to history."""
        if not text.strip():
            return
        # Don't add duplicates of the last entry
        if self._entries and self._entries[-1] == text:
            return
        self._entries.append(text)
        if len(self._entries) > self._max_entries:
            self._entries.pop(0)
        self._cursor = len(self._entries)

    def previous(self) -> str | None:
        """Get previous history entry (up arrow)."""
        if not self._entries:
            return None
        self._cursor = max(0, self._cursor - 1)
        return self._entries[self._cursor]

    def next(self) -> str | None:
        """Get next history entry (down arrow)."""
        if not self._entries:
            return None
        self._cursor = min(len(self._entries), self._cursor + 1)
        if self._cursor >= len(self._entries):
            return ""  # Past the end = empty
        return self._entries[self._cursor]

    def search(self, prefix: str) -> list[str]:
        """Search history for entries starting with prefix."""
        prefix_lower = prefix.lower()
        return [e for e in reversed(self._entries) if e.lower().startswith(prefix_lower)]

    def clear(self) -> None:
        """Clear all history."""
        self._entries.clear()
        self._cursor = -1

    @property
    def entries(self) -> list[str]:
        return list(self._entries)

    @property
    def size(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Multi-line input handling
# ---------------------------------------------------------------------------


def is_multiline_trigger(text: str) -> bool:
    """Check if input starts a multi-line block.

    Triggers: triple backtick, heredoc-style <<, or trailing backslash.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        return True
    if stripped.startswith("<<"):
        return True
    if stripped.endswith("\\"):
        return True
    return False


def is_multiline_complete(accumulated: str) -> bool:
    """Check if multi-line input is complete.

    Complete when closing ``` is found on its own line, or heredoc delimiter
    matches.
    """
    lines = accumulated.split("\n")
    if len(lines) < 2:
        return False

    # Check for closing ```
    if lines[0].strip().startswith("```"):
        for line in lines[1:]:
            if line.strip() == "```":
                return True
        return False

    # Check for heredoc
    if lines[0].strip().startswith("<<"):
        delimiter = lines[0].strip()[2:].strip()
        if delimiter:
            return any(line.strip() == delimiter for line in lines[1:])

    # Trailing backslash continuation
    return not lines[-1].strip().endswith("\\")


# ---------------------------------------------------------------------------
# Command suggestion / auto-complete
# ---------------------------------------------------------------------------


def suggest_commands(
    partial: str,
    registry: CommandRegistry,
    *,
    limit: int = 10,
) -> list[str]:
    """Suggest command completions for partial input.

    Args:
        partial: Partial command input (e.g., "/hel")
        registry: Command registry to search
        limit: Max suggestions

    Returns:
        List of suggested command names (with / prefix)
    """
    if not partial.startswith("/"):
        return []

    query = partial[1:]  # Remove /
    if not query:
        # Show all commands
        commands = registry.list_commands()
        return [f"/{cmd.name}" for cmd in commands[:limit]]

    matches = registry.find_commands(query, limit=limit)
    return [f"/{cmd.name}" for cmd in matches]
