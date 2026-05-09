"""Per-source hook loaders.

Phase-2 / WI-2.1. Each module in this package loads hooks from one of the
chapter's six sources (``ch12-extensibility.md`` §"Six Hook Sources"). The
``HookConfigManager`` composes them at startup, applies the policy cascade
(``src/hooks/policy.py``), and builds the immutable snapshot.

Source modules:
    user_settings     — ~/.claude/settings.json (highest priority)
    project_settings  — walks up from workspace_root looking for .claude/settings.json
    local_settings    — <workspace>/.claude/settings.local.json (gitignored)
    policy_settings   — enterprise-managed; cannot be overridden by user
    plugin_hook       — plugins/<plugin>/hooks.json (priority 999, lowest)

The session_hook source is in-memory only (Phase 3 / WI-3.1) and not loaded
from disk; it has no module here.
"""
