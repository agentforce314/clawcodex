"""export — interactive ``/export`` command (port of TS local-jsx).

Port of ``typescript/src/commands/export/`` (``export.tsx`` + ``index.ts``). A
``local-jsx`` command becomes an :class:`InteractiveCommand` (blocked remotely by
type), with two paths mirroring the TS command:

  * **Args path** (``/export <filename>``): render + write the file *headlessly*,
    never touching ``ctx.ui`` — so it works on the SDK / ``NullUIHost`` surface
    where ``select`` would raise (the headless keystone, like /output-style's
    text result).
  * **Wizard path** (no args): drive ``ctx.ui`` — ``select`` the format, then
    ``prompt_text`` the filename. So one consumer exercises *both* bridge
    primitives.

Deliberate divergences from TS (documented for parity review):

  * **Clipboard deferred** (plan §4.5). TS's wizard offers a "method" step
    (clipboard vs file); a faithful clipboard write needs ``osc.ts`` ported,
    which is out of scope this phase. The wizard ships file-only, so the
    one-option "method" select is dropped (dead UI) and returns when clipboard
    lands as its own PR. The command ``description`` keeps TS's "to a file or
    clipboard" verbatim so it doesn't churn when that follow-up lands.
  * **Esc cancels the whole export.** TS ``ExportDialog`` Esc navigates to the
    previous step; the linear ``await select``/``await prompt_text`` shape can't
    express back-navigation, so Esc at any step cancels the export →
    :meth:`InteractiveOutcome.skip` (the /permissions cancel convention). TS
    shows an "Export cancelled" toast at the format step; the skip path is
    silent. Acceptable — the steps are cheap to re-invoke.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from src.utils.export_formats import (
    ExportFormat,
    ensure_export_filename_extension,
    infer_export_format_from_filename,
    parse_export_args,
    resolve_export_filepath,
)
from src.utils.export_renderer import (
    extract_message_content,
    is_text_block,
    render_messages_for_export,
)

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
    UIOption,
)

# Wizard format options — labels/descriptions mirror ExportDialog.tsx:136-148.
_FORMAT_OPTIONS: list[UIOption] = [
    UIOption(
        value="text",
        label="Plain Text (.txt)",
        description="Plain text format",
    ),
    UIOption(
        value="markdown",
        label="Markdown (.md)",
        description="Markdown format for readable archives",
    ),
    UIOption(
        value="json",
        label="JSON (.json)",
        description="Structured JSON for programmatic use",
    ),
]


def format_timestamp(date: datetime) -> str:
    """``YYYY-MM-DD-HHMMSS`` in local time (port of export.tsx:11-19)."""
    return date.strftime("%Y-%m-%d-%H%M%S")


def _message_type(msg: Any) -> Any:
    if isinstance(msg, Mapping):
        return msg.get("type")
    return getattr(msg, "type", None)


def _block_text(block: Any) -> str:
    if isinstance(block, Mapping):
        return block.get("text") or ""
    return getattr(block, "text", "") or ""


def extract_first_prompt(messages: Any) -> str:
    """First user message's first line, truncated to 50 chars (export.tsx:20-42).

    Mirrors TS ``msg.message?.content`` reads via the renderer's
    :func:`extract_message_content` accessor so flat dataclasses and wire-shaped
    dicts both resolve.
    """
    first_user = None
    for msg in messages or []:
        if _message_type(msg) == "user":
            first_user = msg
            break
    if first_user is None:
        return ""

    content = extract_message_content(first_user)
    result = ""
    if isinstance(content, str):
        result = content.strip()
    elif isinstance(content, list):
        # TS uses ``find(b => b.type === 'text')`` and reads ``.text`` off the
        # match; a malformed first text block (no ``text`` key) yields ``''``.
        # ``is_text_block`` additionally requires a string ``text``, so a
        # malformed block is skipped and scanning continues — a negligible
        # divergence (TextBlock.text is typed ``str = ""``, so unreachable in
        # practice) that fails no worse than TS.
        for item in content:
            if is_text_block(item):
                result = _block_text(item).strip()
                break

    result = result.split("\n")[0]
    if len(result) > 50:
        result = result[:49] + "…"
    return result


def sanitize_filename(text: str) -> str:
    """Lowercase + hyphenate into a filesystem-safe slug (export.tsx:43-49)."""
    s = text.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)  # drop special chars
    s = re.sub(r"\s+", "-", s)  # spaces -> hyphens
    s = re.sub(r"-+", "-", s)  # collapse repeated hyphens
    s = re.sub(r"^-|-$", "", s)  # trim leading/trailing hyphens
    return s


def _default_filename(messages: Any) -> str:
    """``<ts>-<slug>.txt`` or ``conversation-<ts>.txt`` (export.tsx:85-91)."""
    first_prompt = extract_first_prompt(messages)
    timestamp = format_timestamp(datetime.now())
    sanitized = sanitize_filename(first_prompt) if first_prompt else ""
    if sanitized:
        return f"{timestamp}-{sanitized}.txt"
    return f"conversation-{timestamp}.txt"


@dataclass(frozen=True)
class ExportCommand(InteractiveCommand):
    """Render the conversation and write it to a file.

    Args path is headless (never reaches ``ctx.ui``); the no-args path drives
    the file-only wizard. Frozen + no new fields (the ``StatuslineCommand``
    pattern); behavior lives entirely in :meth:`run`.
    """

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        parsed = parse_export_args(args)
        if parsed.error:
            return InteractiveOutcome(message=parsed.error, display="system")

        # Resolve the conversation with the existing no-conversation idiom
        # (builtins.py:381). Covers a None conversation too — hasattr(None, …)
        # is False — so SDK/listing callers degrade gracefully instead of
        # raising.
        conversation = context.conversation
        if not hasattr(conversation, "messages"):
            return InteractiveOutcome(
                message="No conversation to export.", display="system"
            )
        messages = conversation.messages

        # Format: --format flag > filename extension > text default
        # (export.tsx:60-63).
        fmt: Optional[ExportFormat] = parsed.format
        if fmt is None and parsed.filename:
            fmt = infer_export_format_from_filename(parsed.filename)
        if fmt is None:
            fmt = "text"

        cwd = str(context.cwd or context.workspace_root)

        # --- Args path: headless render + write, never touches ctx.ui. ---
        if parsed.filename:
            # TS preserves a ``.markdown`` extension only when the user did not
            # pass an explicit --format flag (export.tsx:69-71).
            return self._write_export(
                messages,
                fmt,
                parsed.filename,
                cwd,
                preserve_markdown_extension=parsed.format is None,
            )

        # --- Wizard path: select format, then prompt for filename. ---
        picked = await context.ui.select(
            "Select export format:", _FORMAT_OPTIONS, current=fmt
        )
        if picked is None:
            return InteractiveOutcome.skip()  # Esc -> cancel the whole export.
        chosen: ExportFormat = picked  # type: ignore[assignment]

        # Default filename carries the chosen format's extension (ExportDialog
        # recomputes it when the format changes, ExportDialog.tsx:37-66).
        default_name = ensure_export_filename_extension(
            _default_filename(messages), chosen, preserve_markdown_extension=True
        )
        name = await context.ui.prompt_text("Enter filename:", default=default_name)
        if name is None:
            return InteractiveOutcome.skip()

        # The wizard submit always preserves a ``.markdown`` extension
        # (ExportDialog.tsx:99-101).
        return self._write_export(
            messages, chosen, name, cwd, preserve_markdown_extension=True
        )

    @staticmethod
    def _write_export(
        messages: Any,
        fmt: ExportFormat,
        filename: str,
        cwd: str,
        *,
        preserve_markdown_extension: bool,
    ) -> InteractiveOutcome:
        """Render + write the export; never raises (export.tsx:67-82,
        ExportDialog.tsx:95-121 both wrap the write in a try/catch and report a
        failure message rather than throwing out of the command)."""
        try:
            content = render_messages_for_export(messages, format=fmt)
            final_filename = ensure_export_filename_extension(
                filename,
                fmt,
                preserve_markdown_extension=preserve_markdown_extension,
            )
            filepath = resolve_export_filepath(cwd, final_filename)
            with open(filepath, "w", encoding="utf-8") as fh:
                fh.write(content)
            return InteractiveOutcome(
                message=f"Conversation exported to: {filepath}", display="system"
            )
        except Exception as exc:  # mirror TS catch-all (export.tsx:79)
            return InteractiveOutcome(
                message=f"Failed to export conversation: {exc}", display="system"
            )


EXPORT_COMMAND = ExportCommand(
    name="export",
    # Verbatim from index.ts. Mentions clipboard, which is deferred this phase
    # (plan §4.5); kept as-is so the description doesn't churn when the
    # clipboard follow-up reintroduces that delivery method.
    description="Export the current conversation to a file or clipboard",
    argument_hint="[filename]",
)


__all__ = [
    "EXPORT_COMMAND",
    "ExportCommand",
    "extract_first_prompt",
    "format_timestamp",
    "sanitize_filename",
]
