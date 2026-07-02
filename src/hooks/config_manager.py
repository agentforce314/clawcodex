from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hook_types import HookConfig, HookEvent, HookSource, ALL_HOOK_EVENTS
from .registry import AsyncHookRegistry
from .shell_invocation import SHELL_TYPES, ShellType

logger = logging.getLogger(__name__)


@dataclass
class HookConfigSnapshot:
    hooks: dict[str, list[HookConfig]] = field(default_factory=dict)
    timestamp: float = 0.0
    source_path: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any(self.hooks.values())


@dataclass
class HookValidationError:
    event: str
    index: int
    field: str
    message: str
    severity: str = "error"


def _get_settings_path() -> Path:
    env_override = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser().resolve() / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def _parse_hook_config(raw: dict[str, Any], source: HookSource | None = None) -> HookConfig:
    hook_type = raw.get("type", "command")

    # Round-2 / Ch12 — per-hook shell selection. Only meaningful for
    # ``type == "command"`` (matches TS schema where ``shell`` only appears on
    # BashCommandHookSchema). Unknown values are dropped here and recorded as
    # validator errors by ``validate_hook_configs``; the parser stays
    # permissive so a single bad entry doesn't black-hole the whole snapshot.
    shell_raw = raw.get("shell")
    shell: ShellType | None = (
        shell_raw if isinstance(shell_raw, str) and shell_raw in SHELL_TYPES else None  # type: ignore[assignment]
    )

    return HookConfig(
        type=hook_type,
        command=raw.get("command", ""),
        timeout=raw.get("timeout"),
        matcher=raw.get("matcher"),
        url=raw.get("url"),
        prompt_text=raw.get("promptText") or raw.get("prompt_text"),
        agent_instructions=raw.get("agentInstructions") or raw.get("agent_instructions"),
        # Phase-1 / WI-1.3 — new fields. Settings.json is permissive on key
        # casing: accept both ``if_condition`` (snake_case, Python-native) and
        # ``if`` (TS-native, matches schemas/hooks.ts). ``once`` stays as a
        # bool. ``skill_root`` is NOT parsed from settings — only set at
        # skill-hook registration time (Phase 3).
        if_condition=raw.get("if_condition") or raw.get("if"),
        once=bool(raw.get("once", False)),
        shell=shell,
        source=source if source is not None else HookSource.USER_SETTINGS,
    )


def validate_hook_configs(
    hooks_config: dict[str, Any],
) -> list[HookValidationError]:
    errors: list[HookValidationError] = []

    for event_name, hook_list in hooks_config.items():
        if event_name not in ALL_HOOK_EVENTS:
            errors.append(HookValidationError(
                event=event_name,
                index=-1,
                field="event",
                message=f"Unknown hook event: {event_name}",
                severity="warning",
            ))
            continue

        # Per-hook field validation for the WI-1.3 additions:
        # ``if`` / ``if_condition`` must be a string when present;
        # ``once`` must be a bool.
        if isinstance(hook_list, list):
            for i, hook_raw in enumerate(hook_list):
                if not isinstance(hook_raw, dict):
                    continue
                if_value = hook_raw.get("if_condition", hook_raw.get("if"))
                if if_value is not None and not isinstance(if_value, str):
                    errors.append(HookValidationError(
                        event=event_name,
                        index=i,
                        field="if",
                        message="`if` must be a string (permission-rule grammar)",
                    ))
                if "once" in hook_raw and not isinstance(hook_raw["once"], bool):
                    errors.append(HookValidationError(
                        event=event_name,
                        index=i,
                        field="once",
                        message="`once` must be a boolean",
                    ))

        if not isinstance(hook_list, list):
            errors.append(HookValidationError(
                event=event_name,
                index=-1,
                field="hooks",
                message=f"Hook list for {event_name} must be an array",
            ))
            continue

        for i, hook_raw in enumerate(hook_list):
            if not isinstance(hook_raw, dict):
                errors.append(HookValidationError(
                    event=event_name,
                    index=i,
                    field="hook",
                    message=f"Hook at index {i} must be an object",
                ))
                continue

            hook_type = hook_raw.get("type", "command")
            if hook_type == "command":
                if not hook_raw.get("command"):
                    errors.append(HookValidationError(
                        event=event_name,
                        index=i,
                        field="command",
                        message="Command hook must have a 'command' field",
                    ))
                # Round-2 / Ch12 — validate ``shell`` for command hooks only.
                # TS ``BashCommandHookSchema.shell`` enforces ``z.enum(SHELL_TYPES)``;
                # we surface unknown values here so settings authors see them.
                # Mirrors TS where ``parseSettingsFile`` would reject the entry
                # whole; we keep the parser permissive (drop the bad value)
                # but flag the error so the failure is visible in the
                # ``/hooks`` UI / logs.
                shell_raw = hook_raw.get("shell")
                if shell_raw is not None and shell_raw not in SHELL_TYPES:
                    errors.append(HookValidationError(
                        event=event_name,
                        index=i,
                        field="shell",
                        message=(
                            f"Unknown shell type: {shell_raw!r}. "
                            f"Must be one of {SHELL_TYPES}."
                        ),
                    ))
            elif hook_type == "http":
                if not hook_raw.get("url"):
                    errors.append(HookValidationError(
                        event=event_name,
                        index=i,
                        field="url",
                        message="HTTP hook must have a 'url' field",
                    ))
            elif hook_type == "agent":
                if not hook_raw.get("agentInstructions") and not hook_raw.get("agent_instructions"):
                    errors.append(HookValidationError(
                        event=event_name,
                        index=i,
                        field="agentInstructions",
                        message="Agent hook must have 'agentInstructions' field",
                    ))
            elif hook_type == "prompt":
                if not hook_raw.get("promptText") and not hook_raw.get("prompt_text"):
                    errors.append(HookValidationError(
                        event=event_name,
                        index=i,
                        field="promptText",
                        message="Prompt hook must have 'promptText' field",
                    ))
            else:
                errors.append(HookValidationError(
                    event=event_name,
                    index=i,
                    field="type",
                    message=f"Unknown hook type: {hook_type}",
                ))

            matcher = hook_raw.get("matcher")
            if matcher is not None:
                try:
                    if "*" not in matcher:
                        re.compile(matcher)
                except re.error as e:
                    errors.append(HookValidationError(
                        event=event_name,
                        index=i,
                        field="matcher",
                        message=f"Invalid matcher pattern: {e}",
                        severity="warning",
                    ))

    return errors


# ---------------------------------------------------------------------------
# Phase-1 / WI-1.1 — legacy back-compat reader for lifecycle events.
# ---------------------------------------------------------------------------

# Pre-Phase-1, ``SessionStart`` / ``SessionEnd`` / compaction events were
# routed through the ``Notification`` event with a magic matcher string.
# Phase 1 promotes them to first-class events; this map preserves the legacy
# form for one CHANGELOG cycle. The matcher can be either ``onSessionStart``
# (legacy code path in ``session_hooks.py`` pre-Phase-1) or the bare event
# name (used in the regression test). Both translate to the same first-class
# event with a DeprecationWarning.
_LEGACY_NOTIFICATION_MATCHER_TO_EVENT: dict[str, HookEvent] = {
    "onSessionStart": "SessionStart",
    "onSessionEnd": "SessionEnd",
    "onCompact": "PreCompact",
    "SessionStart": "SessionStart",
    "SessionEnd": "SessionEnd",
    "PreCompact": "PreCompact",
    "PostCompact": "PostCompact",
}


def _translate_legacy_notification_entry(
    hook_raw: dict[str, Any],
) -> HookEvent | None:
    """If ``hook_raw`` carries a legacy lifecycle matcher under
    ``Notification``, return the canonical first-class event name; else None.

    Emits a DeprecationWarning at translation time so settings authors see
    one warning per offending entry.
    """
    matcher = hook_raw.get("matcher")
    if not isinstance(matcher, str):
        return None
    target = _LEGACY_NOTIFICATION_MATCHER_TO_EVENT.get(matcher)
    if target is None:
        return None
    import warnings as _warnings
    _warnings.warn(
        f"Hook registered under 'Notification' with matcher={matcher!r} is "
        f"deprecated; use the first-class event {target!r} directly. "
        "Will be removed two CHANGELOG entries after the rename.",
        DeprecationWarning,
        stacklevel=3,
    )
    return target


def load_hooks_from_settings(
    settings_path: str | Path | None = None,
    *,
    source: HookSource = HookSource.USER_SETTINGS,
) -> HookConfigSnapshot:
    # ch12 round-4 WI-2 — ``source`` tags every parsed HookConfig with the
    # scope it came from (user/project/local/policy). The trust gate keys
    # on ``HookConfig.source.is_policy`` (only policy hooks survive an
    # untrusted workspace), so multi-scope loading MUST carry the source.
    path = Path(settings_path) if settings_path else _get_settings_path()

    if not path.exists():
        return HookConfigSnapshot(timestamp=time.time(), source_path=str(path))

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read hooks settings from %s: %s", path, e)
        return HookConfigSnapshot(timestamp=time.time(), source_path=str(path))

    hooks_raw = data.get("hooks", {})
    if not isinstance(hooks_raw, dict):
        return HookConfigSnapshot(timestamp=time.time(), source_path=str(path))

    hooks: dict[str, list[HookConfig]] = {}
    for event_name, hook_list in hooks_raw.items():
        if not isinstance(hook_list, list):
            continue
        for hook_raw in hook_list:
            if not isinstance(hook_raw, dict):
                continue
            # Phase-1 / WI-1.1 — legacy ``Notification + matcher`` form
            # translates to first-class lifecycle events.
            target_event: str = event_name
            if event_name == "Notification":
                translated = _translate_legacy_notification_entry(hook_raw)
                if translated is not None:
                    target_event = translated
            # ch01 round-4 WI-1 — canonical Claude Code settings nest hook
            # definitions in matcher groups: {"matcher": ..., "hooks": [...]}
            # (TS schemas/hooks.ts HookMatcherSchema). Expand each group,
            # propagating the group matcher onto inner entries that don't
            # set their own. The flat form (a bare hook dict) stays
            # supported. Without the expansion a real-world settings.json
            # parsed into empty-command junk hooks.
            inner = hook_raw.get("hooks")
            if isinstance(inner, list):
                group_matcher = hook_raw.get("matcher")
                for inner_raw in inner:
                    if not isinstance(inner_raw, dict):
                        continue
                    if group_matcher is not None and "matcher" not in inner_raw:
                        inner_raw = {**inner_raw, "matcher": group_matcher}
                    config = _parse_hook_config(inner_raw)
                    if config.type == "command" and not config.command:
                        continue  # malformed entry — never execute ""
                    config.source = source
                    hooks.setdefault(target_event, []).append(config)
                continue
            config = _parse_hook_config(hook_raw)
            if config.type == "command" and not config.command:
                continue  # malformed entry — never execute ""
            config.source = source
            hooks.setdefault(target_event, []).append(config)

    return HookConfigSnapshot(
        hooks=hooks,
        timestamp=time.time(),
        source_path=str(path),
    )


class HookConfigManager:
    def __init__(
        self,
        registry: AsyncHookRegistry,
        settings_path: str | Path | None = None,
        *,
        cwd: str | Path | None = None,
    ) -> None:
        self._registry = registry
        self._settings_path = Path(settings_path) if settings_path else _get_settings_path()
        # ch12 round-4 WI-2 — cwd scopes the project/local settings files.
        # When an explicit settings_path is given (tests / SDK), stay in
        # single-scope mode for backward compatibility.
        self._cwd = str(cwd) if cwd is not None else None
        self._single_scope = settings_path is not None
        self._snapshot: HookConfigSnapshot | None = None
        self._last_mtime: float = 0.0

    @property
    def snapshot(self) -> HookConfigSnapshot | None:
        return self._snapshot

    def _scope_files(self) -> list[tuple[Path, HookSource]]:
        """ch12 round-4 WI-2 — the per-scope hook settings files, low→high
        precedence (user < project < local < policy). Single-scope mode
        (explicit settings_path) returns just that file as user-scope."""
        if self._single_scope:
            return [(self._settings_path, HookSource.USER_SETTINGS)]
        from src.permissions.settings_paths import (
            local_settings_path,
            project_settings_path,
            user_settings_path,
        )
        from src.settings.managed_path import resolve_managed_settings_path

        files: list[tuple[Path, HookSource]] = [
            (Path(user_settings_path()), HookSource.USER_SETTINGS),
            (Path(project_settings_path(self._cwd)), HookSource.PROJECT_SETTINGS),
            (Path(local_settings_path(self._cwd)), HookSource.LOCAL_SETTINGS),
        ]
        managed = resolve_managed_settings_path()
        if managed is not None:
            files.append((Path(managed), HookSource.POLICY_SETTINGS))
        return files

    async def load(self) -> HookConfigSnapshot:
        # ch12 round-4 WI-2 — merge hooks from ALL scopes (was USER only, so
        # a repo's .claude/settings.json hooks + enterprise policy hooks
        # never fired). Each scope's configs are tagged with their source
        # (for the trust gate's policy-survives-distrust rule) and both read
        # lanes — the merged snapshot (context lane) + the registry (router
        # lane) — are populated per source.
        merged: dict[str, list[HookConfig]] = {}
        scope_files = self._scope_files()
        # ch12 round-4 (critic M1) — the process-GLOBAL registry holds only
        # USER-scope hooks (cwd-INDEPENDENT). Project/local/policy hooks are
        # cwd-specific, so registering them globally would let a second
        # session's load() (different cwd) clear_source + overwrite this
        # session's project hooks — cross-session contamination on the
        # registry-backed lanes (PostSampling, session routers). They live
        # in the per-context SNAPSHOT instead (per-session-safe), and the
        # snapshot-first session routers + the snapshot-based tool lane read
        # them there. Only USER is cleared+registered on the global registry.
        await self._registry.clear_source(HookSource.USER_SETTINGS)

        for path, src in scope_files:
            snap = load_hooks_from_settings(path, source=src)
            for event_name, hook_configs in snap.hooks.items():
                for config in hook_configs:
                    merged.setdefault(event_name, []).append(config)
                    if (
                        src is HookSource.USER_SETTINGS
                        and event_name in ALL_HOOK_EVENTS
                    ):
                        await self._registry.register(
                            event_name,  # type: ignore[arg-type]
                            config,
                            src,
                        )

        snapshot = HookConfigSnapshot(
            hooks=merged, timestamp=time.time(),
            source_path=str(self._settings_path),
        )
        self._snapshot = snapshot

        try:
            self._last_mtime = self._settings_path.stat().st_mtime
        except OSError:
            self._last_mtime = 0.0

        return snapshot

    async def reload_if_changed(self) -> bool:
        try:
            current_mtime = self._settings_path.stat().st_mtime
        except OSError:
            return False

        if current_mtime > self._last_mtime:
            await self.load()
            return True

        return False

    async def validate(self) -> list[HookValidationError]:
        if not self._settings_path.exists():
            return []

        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return [HookValidationError(
                event="",
                index=-1,
                field="file",
                message=f"Cannot read settings file: {self._settings_path}",
            )]

        hooks_raw = data.get("hooks", {})
        if not isinstance(hooks_raw, dict):
            return [HookValidationError(
                event="",
                index=-1,
                field="hooks",
                message="'hooks' field must be an object",
            )]

        return validate_hook_configs(hooks_raw)


def bootstrap_hook_config_manager(
    *,
    cwd: str | Path | None = None,
    settings_path: str | Path | None = None,
) -> HookConfigManager | None:
    """Build + load the manager that makes settings hooks live.

    ch01 round-4 WI-1. This is the missing root of the Hooks abstraction:
    the executors all read hook configs through
    ``tool_use_context.hook_config_manager.snapshot``
    (``hook_executor._get_hooks_from_snapshot``), and the router lane
    (post-sampling / session hooks) reads the global ``AsyncHookRegistry``
    — but nothing constructed or loaded a manager in production, so
    configured hooks never fired. Both production ``ToolContext``
    construction sites (agent-server ``_build_runtime``, headless
    ``run_headless``) call this and attach the result.

    ``load()`` populates BOTH read paths at once: the frozen snapshot
    (context lane) and the global registry (router lane), because every
    parsed config is registered into the registry the manager was
    constructed with.

    Sync-only by contract: both call sites are plain sync functions running
    on threads with no live event loop (the agent-server builds runtimes in
    ``run_in_executor``; headless bootstraps before its own
    ``asyncio.run``). If a running loop is detected, log and return None
    rather than deadlock — an async call site needs an async variant, not
    this one.

    Never raises: hooks must not be able to break startup. Returns None
    when ``settings.hooks.enabled`` is False (the framework off-switch;
    unreadable settings fail open to the default of enabled).

    ``cwd`` scopes both the ``hooks.enabled`` knob lookup AND (ch12
    round-4 WI-2) the project/local hook-entry files: ``load()`` now merges
    hooks from user + project (``<cwd>/.claude/settings.json``) + local
    (``<cwd>/.claude/settings.local.json``) + policy (enterprise managed)
    scopes, each tagged with its ``HookSource`` for the trust gate. An
    explicit ``settings_path`` (tests / SDK) keeps single-scope behavior.
    """
    try:
        from ..settings.settings import load_settings

        if not load_settings(cwd=cwd).hooks.enabled:
            logger.info("hooks disabled via settings.hooks.enabled")
            return None
    except Exception:  # noqa: BLE001 — the knob is an off-switch, not a gate
        logger.debug("could not read settings.hooks.enabled; assuming enabled",
                     exc_info=True)

    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # no running loop — the expected state at both call sites
    else:
        # Documented sync-only contract: asyncio.run() below would raise, and
        # probing first avoids creating a never-awaited load() coroutine.
        logger.warning(
            "bootstrap_hook_config_manager called from a running event loop; "
            "hooks not loaded (use an async variant at this call site)",
        )
        return None

    try:
        from .registry import get_global_hook_registry

        manager = HookConfigManager(
            get_global_hook_registry(), settings_path=settings_path,
            cwd=cwd,
        )
        asyncio.run(manager.load())
        snapshot = manager.snapshot
        if snapshot is not None and not snapshot.is_empty:
            count = sum(len(v) for v in snapshot.hooks.values())
            logger.info(
                "loaded %d hook config(s) from %s",
                count, snapshot.source_path,
            )
        return manager
    except Exception:  # noqa: BLE001 — hooks must never break startup
        logger.warning("hook config bootstrap failed; continuing without hooks",
                       exc_info=True)
        return None
