"""Session-scoped env exports written by lifecycle hooks (#281).

Holds the evaluated exports from ``CLAUDE_ENV_FILE`` writes by
SessionStart / Setup / CwdChanged hooks, bucketed per event. The Bash
tool merges :func:`get_session_hook_env` over ``os.environ`` at spawn —
scoping the contract to "subsequent Bash tool commands" (TS parity:
``sessionEnvironment.ts`` injects into bash commands only, never the
host process env).

Bucketing per event gives CwdChanged the TS clearing semantics for
free: each fire replaces that event's bucket, so per-project exports
from the previous directory don't leak into the next one
(TS ``clearCwdEnvFiles``).

Leaf module: importable from the Bash tool without dragging in the hook
executor stack.
"""

from __future__ import annotations

# Merge precedence, lowest first (TS HOOK_ENV_PRIORITY,
# sessionEnvironment.ts:146-151: setup < sessionstart < cwdchanged —
# SessionStart overrides Setup on key conflict).
_ENV_EVENTS = ("Setup", "SessionStart", "CwdChanged")

_buckets: dict[str, dict[str, str]] = {}


def clear_event_bucket(event: str) -> None:
    """Drop an event's exports — called at the start of each fire so a
    re-fire (e.g. a cwd change) replaces rather than accumulates."""
    _buckets.pop(event, None)


def merge_into_bucket(event: str, exports: dict[str, str]) -> None:
    if not exports or event not in _ENV_EVENTS:
        return
    _buckets.setdefault(event, {}).update(exports)


def get_session_hook_env() -> dict[str, str]:
    """The merged hook-export view, later lifecycle events winning."""
    merged: dict[str, str] = {}
    for event in _ENV_EVENTS:
        merged.update(_buckets.get(event, {}))
    return merged


def reset_session_hook_env_for_testing() -> None:
    _buckets.clear()
