"""Python package placeholder for the archived `entrypoints` subsystem."""

from __future__ import annotations

import json
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / 'reference_data' / 'subsystems' / 'entrypoints.json'
_SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text())

ARCHIVE_NAME = _SNAPSHOT['archive_name']
MODULE_COUNT = _SNAPSHOT['module_count']
SAMPLE_FILES = tuple(_SNAPSHOT['sample_files'])
PORTING_NOTE = f"Python placeholder package for '{ARCHIVE_NAME}' with {MODULE_COUNT} archived module references."

from .headless import HeadlessOptions, run_headless  # noqa: E402
from .tui import TUIOptions, run_tui, should_use_tui  # noqa: E402

__all__ = [
    'ARCHIVE_NAME',
    'MODULE_COUNT',
    'PORTING_NOTE',
    'SAMPLE_FILES',
    'HeadlessOptions',
    'run_headless',
    'TUIOptions',
    'run_tui',
    'should_use_tui',
]
