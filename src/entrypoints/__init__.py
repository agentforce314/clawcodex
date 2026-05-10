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

# WI-4.3: lazy-load the heavy entrypoints (headless + tui). Eagerly
# importing them at package init pulls in Textual, prompt_toolkit, the
# full tool registry, and ~150 transitive modules — defeating the
# fast-path-dispatch acceptance contract for ``clawcodex mcp/doctor/daemon``.
# PEP 562 ``__getattr__`` exposes the public names lazily so callers like
# ``from src.entrypoints import run_tui`` keep working but pay the import
# cost only when actually invoked.
_LAZY_NAMES = {
    'HeadlessOptions': ('headless', 'HeadlessOptions'),
    'run_headless': ('headless', 'run_headless'),
    'TUIOptions': ('tui', 'TUIOptions'),
    'run_tui': ('tui', 'run_tui'),
    'should_use_tui': ('tui', 'should_use_tui'),
}


def __getattr__(name: str):
    if name in _LAZY_NAMES:
        module_name, attr_name = _LAZY_NAMES[name]
        from importlib import import_module
        module = import_module(f'.{module_name}', __name__)
        value = getattr(module, attr_name)
        # Cache so subsequent accesses bypass __getattr__.
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
