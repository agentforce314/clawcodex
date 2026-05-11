"""Placeholder for parity with ``typescript/src/keybindings/``.

The real Python implementation of TUI keybindings lives at
``src/tui/keybindings.py`` (chord-tracker dispatcher). The configurable
loader / schema / resolver layer (mirroring chapter 13's ``loadUserBindings``,
``parser``, ``resolver``, ``schema``, ``validate``) is planned in phase 2 of
``my-docs/ch13-terminal-ui-refactoring-plan.md``. This module exists so the
``reference_data`` subsystem snapshots resolve and so the namespace is
reserved; do not add behavior here.
"""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / 'reference_data' / 'subsystems' / 'keybindings.json'
_SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text())

ARCHIVE_NAME = _SNAPSHOT['archive_name']
MODULE_COUNT = _SNAPSHOT['module_count']
SAMPLE_FILES = tuple(_SNAPSHOT['sample_files'])
PORTING_NOTE = f"Python placeholder package for '{ARCHIVE_NAME}' with {MODULE_COUNT} archived module references."

__all__ = ['ARCHIVE_NAME', 'MODULE_COUNT', 'PORTING_NOTE', 'SAMPLE_FILES']
