"""Policy cascade: ``disableAllHooks`` and ``allowManagedHooksOnly``.

Mirrors TS ``hooksConfigSnapshot.ts:18-88``. Two enterprise-managed flags
that override the default merge behavior, applied AFTER all sources have
been merged but BEFORE the snapshot is built.

Per the chapter (``ch12-extensibility.md`` §"The Snapshot Security Model"):

    > The policy enforcement cascade: ``disableAllHooks`` in policy settings
    > clears everything. ``allowManagedHooksOnly`` excludes user and project
    > hooks. A user can disable their own hooks by setting ``disableAllHooks``,
    > but they cannot disable enterprise-managed hooks. The policy layer
    > always wins.

Per §19 #8 (critic-confirmed): ``disableAllHooks: true`` clears policy hooks
*too* — the chapter's stated semantic. This is counterintuitive but matches
TS. The reason: an enterprise admin who sets ``disableAllHooks: true``
explicitly wants no hooks at all (e.g., during incident response, when even
audit hooks should be disabled).

The cascade does NOT enforce priority precedence within a source — that's
``HookSource.priority``'s job. The cascade is strictly about which sources
survive.
"""

from __future__ import annotations

import logging
from typing import Any

from .hook_types import HookConfig, HookSource

logger = logging.getLogger(__name__)


def should_disable_all_hooks(policy_config: dict[str, Any]) -> bool:
    """True iff policy says ``disableAllHooks: true``.

    Mirrors TS ``hooksConfigSnapshot.ts:21-48``. When this returns True, ALL
    hooks are skipped — including policy-source hooks themselves.
    """
    return bool(policy_config.get("disableAllHooks", False))


def should_allow_managed_hooks_only(policy_config: dict[str, Any]) -> bool:
    """True iff policy says ``allowManagedHooksOnly: true``.

    Mirrors TS ``hooksConfigSnapshot.ts:62-76``. When this returns True, only
    POLICY_SETTINGS-source hooks survive the cascade; user/project/local/
    plugin/session hooks are all dropped.
    """
    return bool(policy_config.get("allowManagedHooksOnly", False))


def apply_policy_cascade(
    hooks_by_event: dict[str, list[HookConfig]],
    policy_config: dict[str, Any],
) -> dict[str, list[HookConfig]]:
    """Apply ``disableAllHooks`` / ``allowManagedHooksOnly`` to a merged
    hooks-by-event dict.

    Returns a NEW dict (does not mutate input).

    Order of checks:
        1. ``disableAllHooks`` — short-circuit; everything cleared, including
           policy-source hooks. The chapter is explicit: "clears everything."
        2. ``allowManagedHooksOnly`` — keep only hooks tagged with
           ``HookSource.POLICY_SETTINGS``.
        3. Otherwise — return unchanged.

    Mirrors TS ``hooksConfigManager.ts:executeHooks`` precedence. Called from
    ``HookConfigManager.load()`` after merging across sources.
    """
    if should_disable_all_hooks(policy_config):
        logger.info("Policy disableAllHooks=true — clearing all hooks (including policy)")
        return {}

    if should_allow_managed_hooks_only(policy_config):
        logger.info(
            "Policy allowManagedHooksOnly=true — keeping policy-source hooks only"
        )
        return {
            event: [h for h in hooks if h.source.is_policy]
            for event, hooks in hooks_by_event.items()
            # Drop empty event keys after filtering for tidier snapshot.
            if any(h.source.is_policy for h in hooks)
        }

    return dict(hooks_by_event)
