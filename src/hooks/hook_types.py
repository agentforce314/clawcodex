from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

HookEvent = Literal[
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SubagentStop",
    "TaskCompleted",
    "TeammateIdle",
    "Notification",
    "UserPromptSubmit",
    "PostSampling",
]

ALL_HOOK_EVENTS: list[HookEvent] = [
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "SubagentStop",
    "TaskCompleted",
    "TeammateIdle",
    "Notification",
    "UserPromptSubmit",
    "PostSampling",
]

HookType = Literal["command", "agent", "http", "prompt"]


class HookSource(str, Enum):
    POLICY = "policy"
    SETTINGS = "settings"
    FRONTMATTER = "frontmatter"
    SKILLS = "skills"
    PLUGINS = "plugins"

    @property
    def priority(self) -> int:
        return {
            HookSource.POLICY: 0,
            HookSource.SETTINGS: 1,
            HookSource.FRONTMATTER: 2,
            HookSource.SKILLS: 3,
            HookSource.PLUGINS: 4,
        }[self]


@dataclass
class HookConfig:
    type: HookType = "command"
    command: str = ""
    timeout: int | None = None
    matcher: str | None = None
    url: str | None = None
    prompt_text: str | None = None
    agent_instructions: str | None = None
    source: HookSource = HookSource.SETTINGS


@dataclass
class HookResult:
    message: Any | None = None
    blocking_error: str | None = None
    permission_behavior: str | None = None
    hook_permission_decision_reason: str | None = None
    hook_source: str | None = None
    updated_input: dict[str, Any] | None = None
    prevent_continuation: bool = False
    stop_reason: str | None = None
    additional_contexts: list[str] | None = None
    updated_mcp_tool_output: Any | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    command: str | None = None


@dataclass
class HookProgress:
    command: str = ""
    prompt_text: str | None = None
    tool_use_id: str = ""
    parent_tool_use_id: str = ""


@dataclass
class PreToolUseHookInput:
    tool_name: str = ""
    tool_use_id: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    permission_mode: str | None = None
    request_prompt: str | None = None
    tool_use_summary: str | None = None


@dataclass
class PostToolUseHookInput:
    tool_name: str = ""
    tool_use_id: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_response: Any = None
    permission_mode: str | None = None


@dataclass
class StopHookInput:
    permission_mode: str | None = None
    stop_hook_active: bool = False
    messages: list[Any] = field(default_factory=list)


@dataclass
class NotificationHookInput:
    notification_type: str = ""
    message: str = ""
    tool_name: str | None = None
    tool_use_id: str | None = None


@dataclass
class UserPromptSubmitHookInput:
    user_message: str = ""
    session_id: str | None = None


@dataclass
class PostSamplingHookInput:
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    response_content: Any = None


TOOL_HOOK_EXECUTION_TIMEOUT_MS = 60_000
HTTP_HOOK_TIMEOUT_MS = 30_000
AGENT_HOOK_TIMEOUT_MS = 120_000
