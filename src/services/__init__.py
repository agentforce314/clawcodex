"""Services subsystem — includes the compression pipeline and more."""

from __future__ import annotations

import json
from pathlib import Path

# Preserve backward-compat archive metadata
_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / 'reference_data' / 'subsystems' / 'services.json'
try:
    _SNAPSHOT = json.loads(_SNAPSHOT_PATH.read_text())
    ARCHIVE_NAME = _SNAPSHOT['archive_name']
    MODULE_COUNT = _SNAPSHOT['module_count']
    SAMPLE_FILES = tuple(_SNAPSHOT['sample_files'])
    PORTING_NOTE = f"Python package for '{ARCHIVE_NAME}' with {MODULE_COUNT} archived module references."
except Exception:
    ARCHIVE_NAME = "services"
    MODULE_COUNT = 0
    SAMPLE_FILES = ()
    PORTING_NOTE = "Services package (archive metadata unavailable)."

__all__ = ['ARCHIVE_NAME', 'MODULE_COUNT', 'PORTING_NOTE', 'SAMPLE_FILES', 'mcp']
