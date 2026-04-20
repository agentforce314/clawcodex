"""Managed settings path resolution for enterprise deployments.

Matches TypeScript settings/managedPath.ts.
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_managed_settings_path() -> Path | None:
    """Resolve the managed settings file path for enterprise deployments.

    Checks:
    1. CLAUDE_MANAGED_SETTINGS_PATH env var
    2. Platform-specific default paths
    """
    env_path = os.environ.get("CLAUDE_MANAGED_SETTINGS_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    # Platform-specific defaults
    import platform
    system = platform.system()

    if system == "Darwin":
        managed = Path("/Library/Application Support/ClawCodex/managed-settings.json")
        if managed.exists():
            return managed

    if system == "Linux":
        managed = Path("/etc/clawcodex/managed-settings.json")
        if managed.exists():
            return managed

    if system == "Windows":
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")
        managed = Path(program_data) / "ClawCodex" / "managed-settings.json"
        if managed.exists():
            return managed

    return None
