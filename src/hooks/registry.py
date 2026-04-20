from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from .hook_types import (
    HookConfig,
    HookEvent,
    HookResult,
    HookSource,
    ALL_HOOK_EVENTS,
)

logger = logging.getLogger(__name__)


@dataclass
class RegisteredHook:
    event: HookEvent
    config: HookConfig
    source: HookSource
    registration_order: int = 0

    @property
    def dedup_key(self) -> str:
        if self.config.type == "command":
            return f"{self.event}:{self.config.type}:{self.config.command}:{self.config.matcher or ''}"
        elif self.config.type == "http":
            return f"{self.event}:{self.config.type}:{self.config.url or ''}:{self.config.matcher or ''}"
        elif self.config.type == "prompt":
            return f"{self.event}:{self.config.type}:{(self.config.prompt_text or '')[:50]}:{self.config.matcher or ''}"
        elif self.config.type == "agent":
            return f"{self.event}:{self.config.type}:{(self.config.agent_instructions or '')[:50]}:{self.config.matcher or ''}"
        return f"{self.event}:{self.config.type}:{self.config.command}:{self.config.matcher or ''}"


class AsyncHookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[RegisteredHook]] = {
            event: [] for event in ALL_HOOK_EVENTS
        }
        self._lock = asyncio.Lock()
        self._counter = 0

    async def register(
        self,
        event: HookEvent,
        config: HookConfig,
        source: HookSource | None = None,
    ) -> RegisteredHook:
        async with self._lock:
            effective_source = source or config.source
            self._counter += 1
            hook = RegisteredHook(
                event=event,
                config=config,
                source=effective_source,
                registration_order=self._counter,
            )

            existing = self._hooks.get(event, [])
            dedup_key = hook.dedup_key
            for existing_hook in existing:
                if existing_hook.dedup_key == dedup_key:
                    return existing_hook

            existing.append(hook)
            existing.sort(key=lambda h: (h.source.priority, h.registration_order))
            self._hooks[event] = existing
            return hook

    async def deregister(
        self,
        event: HookEvent,
        config: HookConfig,
    ) -> bool:
        async with self._lock:
            existing = self._hooks.get(event, [])
            temp_hook = RegisteredHook(event=event, config=config, source=config.source)
            dedup_key = temp_hook.dedup_key
            new_list = [h for h in existing if h.dedup_key != dedup_key]
            removed = len(existing) != len(new_list)
            self._hooks[event] = new_list
            return removed

    async def get_hooks_for_event(
        self,
        event: HookEvent,
        tool_name: str | None = None,
    ) -> list[RegisteredHook]:
        async with self._lock:
            hooks = list(self._hooks.get(event, []))

        if tool_name is not None:
            hooks = [
                h for h in hooks
                if _matches_tool(h.config.matcher, tool_name)
            ]

        return hooks

    async def has_hooks_for_event(
        self,
        event: HookEvent,
        tool_name: str | None = None,
    ) -> bool:
        hooks = await self.get_hooks_for_event(event, tool_name)
        return len(hooks) > 0

    async def clear(self) -> None:
        async with self._lock:
            for event in ALL_HOOK_EVENTS:
                self._hooks[event] = []
            self._counter = 0

    async def clear_source(self, source: HookSource) -> int:
        async with self._lock:
            removed = 0
            for event in ALL_HOOK_EVENTS:
                before = len(self._hooks[event])
                self._hooks[event] = [
                    h for h in self._hooks[event] if h.source != source
                ]
                removed += before - len(self._hooks[event])
            return removed

    async def register_batch(
        self,
        hooks: list[tuple[HookEvent, HookConfig]],
        source: HookSource | None = None,
    ) -> list[RegisteredHook]:
        results: list[RegisteredHook] = []
        for event, config in hooks:
            hook = await self.register(event, config, source)
            results.append(hook)
        return results

    @property
    def hook_count(self) -> int:
        return sum(len(hooks) for hooks in self._hooks.values())

    def get_all_hooks(self) -> dict[HookEvent, list[RegisteredHook]]:
        return {event: list(hooks) for event, hooks in self._hooks.items()}


def _matches_tool(matcher: str | None, tool_name: str) -> bool:
    if matcher is None:
        return True
    if matcher == tool_name:
        return True
    if matcher.endswith("*"):
        prefix = matcher[:-1]
        return tool_name.startswith(prefix)
    if matcher.startswith("*"):
        suffix = matcher[1:]
        return tool_name.endswith(suffix)
    return matcher == tool_name


_global_registry: AsyncHookRegistry | None = None


def get_global_hook_registry() -> AsyncHookRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = AsyncHookRegistry()
    return _global_registry


def reset_global_hook_registry() -> None:
    global _global_registry
    _global_registry = None
