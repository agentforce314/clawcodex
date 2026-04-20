from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping

from .context import ToolContext
from .protocol import ToolResult
from src.permissions.types import (
    PermissionPassthroughResult,
    PermissionResult,
)


@dataclass(frozen=True)
class ValidationResult:
    result: bool
    message: str = ""
    error_code: int = 0

    @staticmethod
    def ok() -> ValidationResult:
        return ValidationResult(result=True)

    @staticmethod
    def fail(message: str, error_code: int = 0) -> ValidationResult:
        return ValidationResult(result=False, message=message, error_code=error_code)


@dataclass(frozen=True)
class SearchOrReadResult:
    is_search: bool = False
    is_read: bool = False
    is_list: bool = False


@dataclass(frozen=True)
class McpInfo:
    server_name: str
    tool_name: str


@dataclass
class Tool:
    name: str
    input_schema: Mapping[str, Any]
    call: Callable[[dict[str, Any], ToolContext], ToolResult]
    prompt: Callable[..., str]
    description: Callable[[dict[str, Any]], str]
    map_result_to_api: Callable[[Any, str], dict[str, Any]]
    check_permissions: Callable[[dict[str, Any], ToolContext], PermissionResult]
    is_enabled: Callable[[], bool]
    is_concurrency_safe: Callable[[dict[str, Any]], bool]
    is_read_only: Callable[[dict[str, Any]], bool]
    is_destructive: Callable[[dict[str, Any]], bool]
    user_facing_name: Callable[[dict[str, Any] | None], str]
    to_auto_classifier_input: Callable[[dict[str, Any]], Any]

    aliases: tuple[str, ...] = ()
    search_hint: str | None = None
    max_result_size_chars: int = 20_000
    strict: bool = False
    should_defer: bool = False
    always_load: bool = False
    is_mcp: bool = False
    is_lsp: bool = False

    validate_input: Callable[[dict[str, Any], ToolContext], ValidationResult] | None = None
    get_path: Callable[[dict[str, Any]], str] | None = None
    input_json_schema: Mapping[str, Any] | None = None
    mcp_info: McpInfo | None = None

    interrupt_behavior: Callable[[], Literal["cancel", "block"]] | None = None
    is_search_or_read_command: Callable[[dict[str, Any]], SearchOrReadResult] | None = None
    is_open_world: Callable[[dict[str, Any]], bool] | None = None
    requires_user_interaction: Callable[[], bool] | None = None
    inputs_equivalent: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None
    backfill_observable_input: Callable[[dict[str, Any]], None] | None = None
    prepare_permission_matcher: Callable[[dict[str, Any]], Callable[[str], bool]] | None = None
    get_tool_use_summary: Callable[[dict[str, Any] | None], str | None] | None = None
    get_activity_description: Callable[[dict[str, Any] | None], str | None] | None = None

    def matches_name(self, name: str) -> bool:
        return self.name == name or name in self.aliases


Tools = list[Tool]


TOOL_DEFAULTS: dict[str, Any] = {
    "is_enabled": lambda: True,
    "is_concurrency_safe": lambda _input: False,
    "is_read_only": lambda _input: False,
    "is_destructive": lambda _input: False,
    "check_permissions": lambda _input, _ctx: PermissionPassthroughResult(),
    "to_auto_classifier_input": lambda _input: "",
    "user_facing_name": None,
}


def _default_map_result_to_api(name: str) -> Callable[[Any, str], dict[str, Any]]:
    def _map(output: Any, tool_use_id: str) -> dict[str, Any]:
        if isinstance(output, str):
            content: str | list[dict[str, Any]] = output
        elif isinstance(output, dict):
            content = json.dumps(output)
        else:
            content = str(output)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
    return _map


def build_tool(
    *,
    name: str,
    input_schema: Mapping[str, Any],
    call: Callable[[dict[str, Any], ToolContext], ToolResult],
    prompt: Callable[..., str] | str | None = None,
    description: Callable[[dict[str, Any]], str] | str | None = None,
    map_result_to_api: Callable[[Any, str], dict[str, Any]] | None = None,
    aliases: tuple[str, ...] | list[str] = (),
    search_hint: str | None = None,
    max_result_size_chars: int = 20_000,
    strict: bool = False,
    should_defer: bool = False,
    always_load: bool = False,
    is_mcp: bool = False,
    is_lsp: bool = False,
    is_enabled: Callable[[], bool] | None = None,
    is_concurrency_safe: Callable[[dict[str, Any]], bool] | None = None,
    is_read_only: Callable[[dict[str, Any]], bool] | None = None,
    is_destructive: Callable[[dict[str, Any]], bool] | None = None,
    check_permissions: Callable[[dict[str, Any], ToolContext], PermissionResult] | None = None,
    validate_input: Callable[[dict[str, Any], ToolContext], ValidationResult] | None = None,
    user_facing_name: Callable[[dict[str, Any] | None], str] | None = None,
    get_path: Callable[[dict[str, Any]], str] | None = None,
    to_auto_classifier_input: Callable[[dict[str, Any]], Any] | None = None,
    input_json_schema: Mapping[str, Any] | None = None,
    mcp_info: McpInfo | None = None,
    interrupt_behavior: Callable[[], Literal["cancel", "block"]] | None = None,
    is_search_or_read_command: Callable[[dict[str, Any]], SearchOrReadResult] | None = None,
    is_open_world: Callable[[dict[str, Any]], bool] | None = None,
    requires_user_interaction: Callable[[], bool] | None = None,
    inputs_equivalent: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
    backfill_observable_input: Callable[[dict[str, Any]], None] | None = None,
    prepare_permission_matcher: Callable[[dict[str, Any]], Callable[[str], bool]] | None = None,
    get_tool_use_summary: Callable[[dict[str, Any] | None], str | None] | None = None,
    get_activity_description: Callable[[dict[str, Any] | None], str | None] | None = None,
) -> Tool:
    if isinstance(prompt, str):
        _p = prompt
        prompt_fn: Callable[..., str] = lambda: _p
    elif prompt is None:
        prompt_fn = lambda: ""
    else:
        prompt_fn = prompt

    if isinstance(description, str):
        _d = description
        desc_fn: Callable[[dict[str, Any]], str] = lambda _input: _d
    elif description is None:
        desc_fn = lambda _input: name
    else:
        desc_fn = description

    return Tool(
        name=name,
        input_schema=input_schema,
        call=call,
        prompt=prompt_fn,
        description=desc_fn,
        map_result_to_api=map_result_to_api or _default_map_result_to_api(name),
        aliases=tuple(aliases),
        search_hint=search_hint,
        max_result_size_chars=max_result_size_chars,
        strict=strict,
        should_defer=should_defer,
        always_load=always_load,
        is_mcp=is_mcp,
        is_lsp=is_lsp,
        is_enabled=is_enabled or TOOL_DEFAULTS["is_enabled"],
        is_concurrency_safe=is_concurrency_safe or TOOL_DEFAULTS["is_concurrency_safe"],
        is_read_only=is_read_only or TOOL_DEFAULTS["is_read_only"],
        is_destructive=is_destructive or TOOL_DEFAULTS["is_destructive"],
        check_permissions=check_permissions or TOOL_DEFAULTS["check_permissions"],
        to_auto_classifier_input=to_auto_classifier_input or TOOL_DEFAULTS["to_auto_classifier_input"],
        validate_input=validate_input,
        user_facing_name=user_facing_name or (lambda _input: name),
        get_path=get_path,
        input_json_schema=input_json_schema,
        mcp_info=mcp_info,
        interrupt_behavior=interrupt_behavior,
        is_search_or_read_command=is_search_or_read_command,
        is_open_world=is_open_world,
        requires_user_interaction=requires_user_interaction,
        inputs_equivalent=inputs_equivalent,
        backfill_observable_input=backfill_observable_input,
        prepare_permission_matcher=prepare_permission_matcher,
        get_tool_use_summary=get_tool_use_summary,
        get_activity_description=get_activity_description,
    )


def tool_matches_name(tool: Tool, name: str) -> bool:
    return tool.name == name or name in tool.aliases


def find_tool_by_name(tools: Tools, name: str) -> Tool | None:
    for t in tools:
        if tool_matches_name(t, name):
            return t
    return None
