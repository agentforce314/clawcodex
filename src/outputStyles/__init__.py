"""Python package placeholder for the archived `outputStyles` subsystem."""

from __future__ import annotations

import json
from pathlib import Path

from .loader import load_output_styles_dir, resolve_output_style
from .styles import BUILTIN_OUTPUT_STYLES, OutputStyle

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / 'reference_data' / 'subsystems' / 'outputStyles.json'
_SNAPSHOT = json.loads(SNAPSHOT_PATH.read_text())

ARCHIVE_NAME = _SNAPSHOT['archive_name']
MODULE_COUNT = _SNAPSHOT['module_count']
SAMPLE_FILES = tuple(_SNAPSHOT['sample_files'])
PORTING_NOTE = f"Python placeholder package for '{ARCHIVE_NAME}' with {MODULE_COUNT} archived module references."

__all__ = [
    'ARCHIVE_NAME',
    'BUILTIN_OUTPUT_STYLES',
    'MODULE_COUNT',
    'OutputStyle',
    'PORTING_NOTE',
    'SAMPLE_FILES',
    'load_output_styles_dir',
    'resolve_output_style',
]
