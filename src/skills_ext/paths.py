from __future__ import annotations

"""
clawcodex-specific Skill Path Resolution

Handles path resolution for clawcodex-specific skill directories:
    - ~/.clawcodex/skills
    - CLAWCODEX_SKILLS_DIR
    - CLAWCODEX_MANAGED_SKILLS_DIR

Mirrors the path resolution pattern from tool_system_ext/paths.py.
"""

import os
from pathlib import Path
from typing import Optional


def _get_global_config_dir() -> Path:
    """Get the global config directory."""
    env_override = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return Path.home() / ".claude"


def _get_managed_file_path() -> Path:
    """Get the managed config directory."""
    env_override = os.environ.get("CLAUDE_MANAGED_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve()
    return Path("/etc/claude")


def get_clawcodex_skills_dir() -> Path:
    """Get the default clawcodex skills directory (~/.clawcodex/skills)."""
    return Path.home() / ".clawcodex" / "skills"


def get_clawcodex_managed_skills_dir() -> Path | None:
    """Get the clawcodex managed skills directory from environment."""
    env_val = os.environ.get("CLAWCODEX_MANAGED_SKILLS_DIR")
    if env_val:
        return Path(env_val).expanduser().resolve()
    return None


def get_clawcodex_user_skills_dirs() -> list[Path]:
    """
    Get list of clawcodex-specific user skill directories.

    Returns:
        List of paths in priority order:
        1. CLAWCODEX_SKILLS_DIR (if set)
        2. ~/.clawcodex/skills
    """
    dirs: list[Path] = []

    env_primary = os.environ.get("CLAWCODEX_SKILLS_DIR")
    if env_primary:
        dirs.append(Path(env_primary).expanduser().resolve())

    clawcodex_dir = get_clawcodex_skills_dir()
    if clawcodex_dir not in dirs:
        dirs.append(clawcodex_dir)

    return dirs


def get_clawcodex_project_skills_dir(project_root: str | Path | None) -> Path | None:
    """
    Get the clawcodex project skills directory.

    Args:
        project_root: Project root path, defaults to cwd

    Returns:
        Path to .clawcodex/skills in project root, or None if project_root not given
    """
    if project_root is None:
        return None
    pr = Path(project_root).expanduser().resolve()
    return pr / ".clawcodex" / "skills"


def resolve_skills_paths(
    project_root: str | Path | None = None,
    user_skills_dir: str | Path | None = None,
) -> dict[str, list[str]]:
    """
    Resolve all clawcodex-specific skill paths.

    Returns a dict with keys:
        - user: List of user skill directory paths
        - project: List of project skill directory paths
        - managed: List of managed skill directory paths (may be empty)
    """
    result: dict[str, list[str]] = {
        "user": [],
        "project": [],
        "managed": [],
    }

    # User directories
    if user_skills_dir is not None:
        result["user"].append(str(Path(user_skills_dir).expanduser().resolve()))
    else:
        for d in get_clawcodex_user_skills_dirs():
            result["user"].append(str(d))

    # Project directory
    project_dir = get_clawcodex_project_skills_dir(project_root)
    if project_dir is not None:
        result["project"].append(str(project_dir))

    # Managed directory
    managed_dir = get_clawcodex_managed_skills_dir()
    if managed_dir is not None:
        result["managed"].append(str(managed_dir))

    return result