"""Buddy / companion subsystem — ports TypeScript ``buddy/*`` modules."""

from __future__ import annotations

from src.buddy.feature import is_buddy_enabled
from src.buddy.prompt import (
    build_companion_intro_attachment,
    companion_intro_text,
    format_companion_intro_attachments,
)

__all__ = [
    'is_buddy_enabled',
    'build_companion_intro_attachment',
    'companion_intro_text',
    'format_companion_intro_attachments',
]
