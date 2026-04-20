from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class QueryConfig:
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 16384
    max_turns: int = 50
    effort: str = "high"
    temperature: float | None = None
    stop_sequences: list[str] | None = None
    streaming_tool_execution: bool = True
    reactive_compact_enabled: bool = True
    context_collapse_enabled: bool = False
    token_budget_enabled: bool = True
    thinking_enabled: bool = False
    thinking_budget: int = 10000
    tool_search_enabled: bool = False
    memory_prefetch_enabled: bool = True
    stop_hooks_enabled: bool = True
    tool_use_summary_enabled: bool = True
    emit_tool_use_summaries: bool = True
    fast_mode_enabled: bool = False
    structured_output: dict[str, Any] | None = None
    extra_headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None
    fallback_model: str | None = None
    query_source: str = "repl_main_thread"
    session_id: str = ""


@dataclass(frozen=True)
class FrozenQueryConfig:
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 16384
    max_turns: int = 50
    effort: str = "high"
    temperature: float | None = None
    stop_sequences: tuple[str, ...] | None = None
    streaming_tool_execution: bool = True
    reactive_compact_enabled: bool = True
    context_collapse_enabled: bool = False
    token_budget_enabled: bool = True
    thinking_enabled: bool = False
    thinking_budget: int = 10000
    tool_search_enabled: bool = False
    memory_prefetch_enabled: bool = True
    stop_hooks_enabled: bool = True
    tool_use_summary_enabled: bool = True
    emit_tool_use_summaries: bool = True
    fast_mode_enabled: bool = False
    fallback_model: str | None = None
    query_source: str = "repl_main_thread"
    session_id: str = ""


def build_query_config(
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    thinking_enabled: bool | None = None,
    thinking_budget: int | None = None,
    tool_search_enabled: bool | None = None,
    reactive_compact_enabled: bool | None = None,
    context_collapse_enabled: bool | None = None,
    token_budget_enabled: bool | None = None,
    streaming_tool_execution: bool | None = None,
    memory_prefetch_enabled: bool | None = None,
    stop_hooks_enabled: bool | None = None,
    emit_tool_use_summaries: bool | None = None,
    fast_mode_enabled: bool | None = None,
    fallback_model: str | None = None,
    query_source: str | None = None,
    **kwargs: Any,
) -> FrozenQueryConfig:
    overrides: dict[str, Any] = {}

    if model is not None:
        overrides["model"] = model
    if max_tokens is not None:
        overrides["max_tokens"] = max_tokens
    if max_turns is not None:
        overrides["max_turns"] = max_turns
    if effort is not None:
        overrides["effort"] = effort
    if thinking_enabled is not None:
        overrides["thinking_enabled"] = thinking_enabled
    if thinking_budget is not None:
        overrides["thinking_budget"] = thinking_budget
    if tool_search_enabled is not None:
        overrides["tool_search_enabled"] = tool_search_enabled
    if reactive_compact_enabled is not None:
        overrides["reactive_compact_enabled"] = reactive_compact_enabled
    if context_collapse_enabled is not None:
        overrides["context_collapse_enabled"] = context_collapse_enabled
    if token_budget_enabled is not None:
        overrides["token_budget_enabled"] = token_budget_enabled
    if streaming_tool_execution is not None:
        overrides["streaming_tool_execution"] = streaming_tool_execution
    if memory_prefetch_enabled is not None:
        overrides["memory_prefetch_enabled"] = memory_prefetch_enabled
    if stop_hooks_enabled is not None:
        overrides["stop_hooks_enabled"] = stop_hooks_enabled
    if emit_tool_use_summaries is not None:
        overrides["emit_tool_use_summaries"] = emit_tool_use_summaries
    if fast_mode_enabled is not None:
        overrides["fast_mode_enabled"] = fast_mode_enabled
    if fallback_model is not None:
        overrides["fallback_model"] = fallback_model
    if query_source is not None:
        overrides["query_source"] = query_source

    overrides.setdefault("session_id", str(uuid4()))

    return FrozenQueryConfig(**overrides)
