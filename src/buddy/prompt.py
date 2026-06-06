"""Companion-intro attachment + system-prompt text.

Two functions:

* :func:`companion_intro_text` — the system-prompt block.
* :func:`build_companion_intro_attachment` — guards + dedup + attachment dict.

Plus :func:`format_companion_intro_attachments` — renders companion-intro
into a ``<system-reminder>`` block. Kept separate from at-mention formatting
so neither's scope creeps into the other.
"""
from __future__ import annotations

from typing import Any, Iterable

from src.buddy.companion import get_companion
from src.buddy.feature import is_buddy_enabled
from src.config import load_config

try:
    from src.types.messages import AttachmentMessage
except ImportError:
    AttachmentMessage = object  # type: ignore[misc, assignment]


def companion_intro_text(name: str, species: str) -> str:
    """System-prompt block describing the companion to the model."""
    return (
        f"# Companion\n\n"
        f"A small {species} named {name} sits beside the user's input box "
        f"and occasionally comments in a speech bubble. You're not {name} — "
        f"it's a separate watcher.\n\n"
        f"When the user addresses {name} directly (by name), its bubble "
        f"will answer. Your job in that moment is to stay out of the way: "
        f"respond in ONE line or less, or just answer any part of the "
        f"message meant for you. Don't explain that you're not {name} — "
        f"they know. Don't narrate what {name} might say — the bubble "
        f"handles that."
    )


def build_companion_intro_attachment(
    messages: Iterable[Any] | None,
) -> list[dict[str, Any]]:
    """Build the companion-intro attachment, or ``[]``.

    Returns ``[]`` when ANY of:
    * buddy is disabled,
    * no companion hatched,
    * companion is muted,
    * this companion has already been announced in this conversation (dedup).
    """
    if not is_buddy_enabled():
        return []
    companion = get_companion()
    if companion is None:
        return []
    if load_config().get('companion_muted', False):
        return []

    for msg in messages or []:
        try:
            if not isinstance(msg, AttachmentMessage):
                continue
            for att in (msg.attachments or []):
                if not isinstance(att, dict):
                    continue
                if att.get('kind') == 'companion_intro' and att.get('name') == companion.name:
                    return []
        except Exception:
            continue

    return [{
        'kind': 'companion_intro',
        'name': companion.name,
        'species': companion.species,
    }]


def format_companion_intro_attachments(
    attachments: list[dict[str, Any]],
) -> str:
    """Render companion-intro attachments as concatenated <system-reminder>s."""
    blocks: list[str] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        if att.get('kind') != 'companion_intro':
            continue
        text = companion_intro_text(
            att.get('name', ''),
            att.get('species', ''),
        )
        blocks.append(f"<system-reminder>\n{text}\n</system-reminder>")
    return '\n\n'.join(blocks)


__all__ = [
    'build_companion_intro_attachment',
    'companion_intro_text',
    'format_companion_intro_attachments',
]
