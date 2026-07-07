"""Canonical clawcodex config-directory resolution.

Single source of truth for the three config roots. clawcodex is its own
product, so its files live under clawcodex-branded locations — NOT the
real Claude Code harness's ``~/.claude`` / ``<project>/.claude``, which
this codebase must never read or write (a machine commonly has both
tools installed; sharing those directories means inheriting and mutating
the other tool's live skills/agents/settings — see the review-A C1
finding that first split ``permissions/settings_paths.py``).

Roots:
  * User:    ``$CLAWCODEX_CONFIG_DIR`` or ``~/.clawcodex``
  * Project: ``<dir>/.clawcodex`` (walked per-subsystem)
  * Managed: ``$CLAWCODEX_MANAGED_CONFIG_DIR`` or ``/etc/clawcodex``

The legacy ``~/.claude`` locations are consulted exactly once, by
``src/utils/legacy_migration.py``, as a copy-only migration SOURCE.

This module is a stdlib-only leaf (no ``src`` imports) so every
subsystem — including early-import ones like ``src/config.py`` — can
depend on it without cycles.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Env override for the user config dir. Deliberately NOT
#: ``CLAUDE_CONFIG_DIR``: honoring the real Claude Code harness's
#: override would re-couple the two tools' state on machines that set
#: it (the exact contamination this module exists to prevent).
CONFIG_DIR_ENV = "CLAWCODEX_CONFIG_DIR"

#: Env override for the managed/policy config dir.
MANAGED_CONFIG_DIR_ENV = "CLAWCODEX_MANAGED_CONFIG_DIR"

#: Name of the per-project config directory.
PROJECT_DIR_NAME = ".clawcodex"

#: Default managed/policy dir (enterprise deployments).
DEFAULT_MANAGED_CONFIG_DIR = "/etc/clawcodex"

#: Legacy locations — migration SOURCE only. Never read at runtime.
LEGACY_USER_DIR_NAME = ".claude"
LEGACY_PROJECT_DIR_NAME = ".claude"


def get_user_config_dir() -> Path:
    """User config home: ``$CLAWCODEX_CONFIG_DIR`` or ``~/.clawcodex``.

    Returns an expanduser'd (not resolved) path; callers that need
    symlink-resolved identity apply ``.resolve()`` themselves — several
    consumers (e.g. memdir) deliberately keep the unresolved spelling.
    """
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".clawcodex"


def get_managed_config_dir() -> Path:
    """Managed/policy dir: ``$CLAWCODEX_MANAGED_CONFIG_DIR`` or ``/etc/clawcodex``."""
    override = os.environ.get(MANAGED_CONFIG_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path(DEFAULT_MANAGED_CONFIG_DIR)


def get_legacy_user_config_dir() -> Path:
    """The pre-rebrand user dir (``~/.claude``) — migration source only."""
    return Path.home() / LEGACY_USER_DIR_NAME


__all__ = [
    "CONFIG_DIR_ENV",
    "MANAGED_CONFIG_DIR_ENV",
    "PROJECT_DIR_NAME",
    "DEFAULT_MANAGED_CONFIG_DIR",
    "LEGACY_USER_DIR_NAME",
    "LEGACY_PROJECT_DIR_NAME",
    "get_user_config_dir",
    "get_managed_config_dir",
    "get_legacy_user_config_dir",
]
