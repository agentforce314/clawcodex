"""CCR bridge subsystem (Bridge v1 + Bridge v2 + shared primitives).

Real implementations land here per ``my-docs/ch16-remote-refactoring-plan.md``.
The ``ARCHIVE_NAME``/``MODULE_COUNT``/``SAMPLE_FILES``/``PORTING_NOTE``
re-exports are preserved for backwards compat with
``tests/test_porting_workspace.py:73-79`` (``from src import bridge`` then
``bridge.MODULE_COUNT > 0``). New code uses full module paths instead, e.g.
``from src.bridge.bounded_uuid_set import BoundedUUIDSet``.
"""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / 'reference_data' / 'subsystems' / 'bridge.json'
_SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text())

ARCHIVE_NAME = _SNAPSHOT['archive_name']
MODULE_COUNT = _SNAPSHOT['module_count']
SAMPLE_FILES = tuple(_SNAPSHOT['sample_files'])
PORTING_NOTE = f"Python placeholder package for '{ARCHIVE_NAME}' with {MODULE_COUNT} archived module references."

__all__ = ['ARCHIVE_NAME', 'MODULE_COUNT', 'PORTING_NOTE', 'SAMPLE_FILES']
