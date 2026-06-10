"""Feature gating for dynamic workflows.

There is no central feature-flag system in clawcodex; following the advisor
precedent (``src/utils/advisor.py``), workflows are gated by an env kill-switch
plus a settings key. ``is_workflows_enabled()`` is the single chokepoint the
Workflow tool, command discovery, and the bundled commands consult.

Disabled when any of:
- ``CLAUDE_CODE_DISABLE_WORKFLOWS`` is truthy (read at call time);
- the ``disable_workflows`` setting is true; or
- the camelCase ``disableWorkflows`` JSON key is set (via ``SettingsSchema.extra``).
"""

from __future__ import annotations

import os

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_DISABLE_ENV = "CLAUDE_CODE_DISABLE_WORKFLOWS"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def is_workflows_enabled() -> bool:
    if _env_truthy(_DISABLE_ENV):
        return False
    try:
        from src.settings.settings import get_settings

        settings = get_settings()
    except Exception:
        # Pre-startup / no settings available: default to enabled (the env
        # kill-switch above still applies).
        return True
    if getattr(settings, "disable_workflows", False):
        return False
    extra = getattr(settings, "extra", None)
    if isinstance(extra, dict) and extra.get("disableWorkflows"):
        return False
    return True
