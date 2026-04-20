"""Hook system — PreToolUse, PostToolUse, Stop, Notification, PostSampling hook execution runtime.

Mirrors TypeScript utils/hooks.ts and hooks/ directory.
"""

from __future__ import annotations

from .hook_types import (
    ALL_HOOK_EVENTS,
    AGENT_HOOK_TIMEOUT_MS,
    HTTP_HOOK_TIMEOUT_MS,
    TOOL_HOOK_EXECUTION_TIMEOUT_MS,
    HookConfig,
    HookEvent,
    HookProgress,
    HookResult,
    HookSource,
    HookType,
    NotificationHookInput,
    PostSamplingHookInput,
    PostToolUseHookInput,
    PreToolUseHookInput,
    StopHookInput,
    UserPromptSubmitHookInput,
)
from .config_manager import HookConfigManager
from .registry import (
    AsyncHookRegistry,
    RegisteredHook,
    get_global_hook_registry,
    reset_global_hook_registry,
)

__all__ = [
    "ALL_HOOK_EVENTS",
    "AGENT_HOOK_TIMEOUT_MS",
    "HTTP_HOOK_TIMEOUT_MS",
    "TOOL_HOOK_EXECUTION_TIMEOUT_MS",
    "AsyncHookRegistry",
    "HookConfig",
    "HookEvent",
    "HookProgress",
    "HookResult",
    "HookSource",
    "HookType",
    "NotificationHookInput",
    "PostSamplingHookInput",
    "PostToolUseHookInput",
    "PreToolUseHookInput",
    "RegisteredHook",
    "StopHookInput",
    "UserPromptSubmitHookInput",
    "get_global_hook_registry",
    "reset_global_hook_registry",
    "HookConfigManager",
]
