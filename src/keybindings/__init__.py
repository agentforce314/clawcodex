"""Placeholder for parity with ``typescript/src/keybindings/``.

Current truth (get-parity-by-folder keybindings close, 2026-07): the
DEFAULT chord surface lives in the kept TS client
(``ui-tui/src/app/useInputHandlers.ts`` — the sole interactive UI since the
UI-consolidation, PR #566, deleted the Python TUIs this docstring used to
point at). The configurable loader/schema/resolver layer is OFF in open
builds (``loadUserBindings.ts:259-267`` gates on a key absent from the
GrowthBook stub's ``_openBuildDefaults``), so there is nothing to port
unless upstream un-gates it — and the port target would then be the
CLIENT, not this package. This module exists so the ``reference_data``
subsystem snapshots resolve and so the namespace is reserved; do not add
behavior here.
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
