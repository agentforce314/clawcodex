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
) -> HookConfigSnapshot:
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
            hooks.setdefault(target_event, []).append(_parse_hook_config(hook_raw))

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
    ) -> None:
        self._registry = registry
        self._settings_path = Path(settings_path) if settings_path else _get_settings_path()
        self._snapshot: HookConfigSnapshot | None = None
        self._last_mtime: float = 0.0

    @property
    def snapshot(self) -> HookConfigSnapshot | None:
        return self._snapshot

    async def load(self) -> HookConfigSnapshot:
        snapshot = load_hooks_from_settings(self._settings_path)
        self._snapshot = snapshot

        await self._registry.clear_source(HookSource.USER_SETTINGS)

        for event_name, hook_configs in snapshot.hooks.items():
            for config in hook_configs:
                if event_name in ALL_HOOK_EVENTS:
                    await self._registry.register(
                        event_name,  # type: ignore[arg-type]
                        config,
                        HookSource.USER_SETTINGS,
                    )

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
