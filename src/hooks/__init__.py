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
    ShellType,
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
from .shell_invocation import (
    DEFAULT_HOOK_SHELL,
    SHELL_TYPES,
    build_powershell_args,
    find_powershell_path,
)

__all__ = [
    "ALL_HOOK_EVENTS",
    "AGENT_HOOK_TIMEOUT_MS",
    "HTTP_HOOK_TIMEOUT_MS",
    "TOOL_HOOK_EXECUTION_TIMEOUT_MS",
    "AsyncHookRegistry",
    "DEFAULT_HOOK_SHELL",
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
    "SHELL_TYPES",
    "ShellType",
    "StopHookInput",
    "UserPromptSubmitHookInput",
    "build_powershell_args",
    "find_powershell_path",
    "get_global_hook_registry",
    "reset_global_hook_registry",
    "HookConfigManager",
]
