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


def _parse_hook_config(raw: dict[str, Any]) -> HookConfig:
    hook_type = raw.get("type", "command")
    return HookConfig(
        type=hook_type,
        command=raw.get("command", ""),
        timeout=raw.get("timeout"),
        matcher=raw.get("matcher"),
        url=raw.get("url"),
        prompt_text=raw.get("promptText") or raw.get("prompt_text"),
        agent_instructions=raw.get("agentInstructions") or raw.get("agent_instructions"),
        source=HookSource.SETTINGS,
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
        configs: list[HookConfig] = []
        for hook_raw in hook_list:
            if isinstance(hook_raw, dict):
                configs.append(_parse_hook_config(hook_raw))
        if configs:
            hooks[event_name] = configs

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

        await self._registry.clear_source(HookSource.SETTINGS)

        for event_name, hook_configs in snapshot.hooks.items():
            for config in hook_configs:
                if event_name in ALL_HOOK_EVENTS:
                    await self._registry.register(
                        event_name,  # type: ignore[arg-type]
                        config,
                        HookSource.SETTINGS,
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
