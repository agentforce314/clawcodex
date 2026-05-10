"""Placeholder for parity with ``typescript/src/components/``.

The real Python implementation of the terminal-UI component tree lives at
``src/tui/widgets/`` (Textual widgets) and ``src/tui/screens/`` (Textual
screens). This module exists so the ``reference_data`` subsystem snapshots
resolve and so the namespace is reserved; do not add behavior here. See
``my-docs/ch13-terminal-ui-gap-analysis.md`` gap #11 for the rationale.
"""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / 'reference_data' / 'subsystems' / 'components.json'
_SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text())

ARCHIVE_NAME = _SNAPSHOT['archive_name']
MODULE_COUNT = _SNAPSHOT['module_count']
SAMPLE_FILES = tuple(_SNAPSHOT['sample_files'])
PORTING_NOTE = f"Python placeholder package for '{ARCHIVE_NAME}' with {MODULE_COUNT} archived module references."

__all__ = ['ARCHIVE_NAME', 'MODULE_COUNT', 'PORTING_NOTE', 'SAMPLE_FILES']
