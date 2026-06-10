"""Bundled workflow scripts shipped with clawcodex (e.g. /deep-research)."""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parent


def bundled_workflow_path(name: str) -> Path:
    """Absolute path to a bundled workflow script (``<name>.py``)."""
    return _DIR / f"{name}.py"
