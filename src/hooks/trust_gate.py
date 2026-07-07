"""Workspace-trust gate for hook execution.

Mirrors the TypeScript ``shouldSkipHookDueToTrust`` predicate (referenced from
``typescript/src/utils/hooks/hooksConfigManager.ts``). The chapter
(``ch12-extensibility.md`` §"The Snapshot Security Model") describes this as a
centralized gate at the top of ``executeHooks()`` — introduced after two
vulnerabilities where hooks fired in lifecycle states the user had not consented
to.

Failure mode without this gate: a project's ``.clawcodex/settings.json`` defines a
hook that fires before every tool call. The user opens the project, declines the
workspace-trust dialog, and the hook still fires once because ``SessionStart``
runs before the trust check. The gate closes that window.

Policy hooks (``HookSource.POLICY_SETTINGS``) are NOT subject to this gate per
chapter §"The Snapshot Security Model" final paragraph: "the policy layer
always wins."
"""

from __future__ import annotations

from typing import Any


def should_skip_hook_due_to_trust(tool_use_context: Any) -> bool:
    """Return True if non-policy hooks must be skipped because the workspace
    isn't trusted.

    Reads ``tool_use_context.workspace_trusted`` (a bool added on ``ToolContext``
    in WI-0.2). Defaults to ``False`` if the attribute is missing — fail-safe:
    unknown trust state is treated as untrusted.

    Returns ``True`` (skip) iff workspace_trusted is False. Callers must still
    let policy-source hooks through; the per-hook policy check happens at the
    caller, not here, because policy-source identification is per-``HookConfig``.
    """
    return not getattr(tool_use_context, "workspace_trusted", False)
