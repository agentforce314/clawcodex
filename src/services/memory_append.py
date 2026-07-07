"""``#`` memory-note append (components C9) — UI-neutral file layer.

Port note (honesty over invention): the vendored TS snapshot ships the
RENDERER for saved notes (``UserMemoryInputMessage.tsx`` — the
``<user-memory-input>`` row plus a random "Got it. / Good to know. /
Noted." acknowledgement) and the target selector
(``MemoryFileSelector.tsx``, already ported as ``/memory``), but NOT
the trigger glue: no ``#`` detection or append function exists in the
snapshot. This module supplies that glue with a documented discipline:

* ensure the file exists (ensure-create via append-mode open, plus the
  same ``~/.clawcodex`` mkdir rule as the ``/memory`` port);
* separate from existing content with a single newline (adding one to
  a file that doesn't end in ``\\n`` first);
* write the note as a ``- `` bullet line unless the user already
  supplied list/heading punctuation — matching the bulleted style the
  product writes to CLAUDE.md memory files.
"""

from __future__ import annotations

import random
from pathlib import Path

SAVING_MESSAGES = ("Got it.", "Good to know.", "Noted.")


def pick_saving_message() -> str:
    """TS UserMemoryInputMessage.tsx:9 — lodash ``sample`` of the trio."""

    return random.choice(SAVING_MESSAGES)


def format_note_line(note: str) -> str:
    stripped = note.strip()
    if stripped.startswith(("-", "*", "#")):
        return stripped
    return f"- {stripped}"


def append_memory_note(path: str, note: str) -> bool:
    """Append *note* to the memory file at *path*. Returns success."""

    stripped = note.strip()
    if not stripped:
        return False
    try:
        target = Path(path)
        config_home = Path.home() / ".clawcodex"
        try:
            if target.resolve().is_relative_to(config_home.resolve()):
                config_home.mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError):
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if target.exists():
            existing = target.read_text(encoding="utf-8")
        prefix = ""
        if existing and not existing.endswith("\n"):
            prefix = "\n"
        with open(target, "a", encoding="utf-8") as f:
            f.write(f"{prefix}{format_note_line(stripped)}\n")
        return True
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError from a non-UTF8 file —
        # the bool contract must hold without caller help.
        return False


__all__ = [
    "SAVING_MESSAGES",
    "append_memory_note",
    "format_note_line",
    "pick_saving_message",
]
