"""Tool-failure-loop guard — port of TS query/toolFailureLoopGuard.ts.

Trips the query loop with ``Terminal(reason="tool_failure_loop")`` when
consecutive tool batches contain only failures and the same failure
signature, error category, or file path keeps recurring. Any successful
tool result resets all counters (toolFailureLoopGuard.ts:91-94).

Divergence vs TS (documented, intentional): two extra error-category
patterns recognize this runtime's native error strings —
``unknown tool:`` (src/tool_system/registry.py:108) maps to the same
``NoSuchTool`` bucket TS assigns its "No such tool available" text, and
``No such file or directory`` (Python ``OSError`` phrasing, no ENOENT
literal) maps to ``NotFound``. All other patterns are verbatim TS.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from ..types.messages import UserMessage

DEFAULT_TOOL_FAILURE_LOOP_THRESHOLD = 3
MAX_FALLBACK_CATEGORY_LENGTH = 120

_ENV_VAR = "CLAUDE_CODE_TOOL_FAILURE_LOOP_THRESHOLD"


@dataclass
class ToolFailureLoopGuardState:
    signature_counts: dict[str, int] = field(default_factory=dict)
    category_counts: dict[str, int] = field(default_factory=dict)
    path_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolFailureLoopGuardDecision:
    tripped: bool
    message: str | None = None
    threshold: int | None = None
    kind: Literal["signature", "category", "path"] | None = None
    tool_name: str | None = None
    error_category: str | None = None
    path: str | None = None


_NOT_TRIPPED = ToolFailureLoopGuardDecision(tripped=False)


def create_tool_failure_loop_guard_state() -> ToolFailureLoopGuardState:
    return ToolFailureLoopGuardState()


def get_tool_failure_loop_threshold(value: str | None = None) -> int:
    if value is None:
        value = os.environ.get(_ENV_VAR)
    if value is None:
        return DEFAULT_TOOL_FAILURE_LOOP_THRESHOLD

    trimmed = value.strip()
    # [0-9] not \d: Python \d matches Unicode digits; TS /^\d+$/ is ASCII.
    if not re.fullmatch(r"[0-9]+", trimmed):
        return DEFAULT_TOOL_FAILURE_LOOP_THRESHOLD

    parsed = int(trimmed)
    # TS Number.isSafeInteger bound (toolFailureLoopGuard.ts:47-49).
    if parsed > 2**53 - 1:
        return DEFAULT_TOOL_FAILURE_LOOP_THRESHOLD
    return parsed


def update_tool_failure_loop_guard(
    *,
    state: ToolFailureLoopGuardState,
    tool_use_blocks: list[Any],
    tool_results: list[Any],
    threshold: int | None = None,
) -> ToolFailureLoopGuardDecision:
    resolved_threshold = _normalize_threshold(threshold)
    if resolved_threshold == 0:
        return _NOT_TRIPPED

    tool_use_by_id = {
        str(getattr(block, "id", "")): block for block in tool_use_blocks
    }
    failures: list[tuple[str, str, str | None]] = []
    has_success = False

    for block in _get_tool_result_blocks(tool_results):
        content = _tool_result_content_to_string(getattr(block, "content", None))

        if getattr(block, "is_error", None) is not True:
            has_success = True
            continue

        if _is_ignored_synthetic_tool_result(content):
            continue

        tool_use = tool_use_by_id.get(str(getattr(block, "tool_use_id", "") or ""))
        tool_name = getattr(tool_use, "name", None) or "unknown"
        error_category = _normalize_error_category(content)
        failures.append((
            tool_name,
            error_category,
            _extract_normalized_path(getattr(tool_use, "input", None)),
        ))

    if has_success:
        _reset(state)
        return _NOT_TRIPPED

    for tool_name, error_category, path in failures:
        signature_count = _increment_counter(
            state.signature_counts, f"{tool_name}\0{error_category}"
        )
        category_count = _increment_counter(state.category_counts, error_category)
        path_count = (
            _increment_counter(state.path_counts, path) if path else 0
        )

        if path and path_count >= resolved_threshold:
            return ToolFailureLoopGuardDecision(
                tripped=True,
                kind="path",
                threshold=resolved_threshold,
                path=path,
                message=_create_trip_message(
                    kind="path", threshold=resolved_threshold, path=path,
                ),
            )

        if signature_count >= resolved_threshold:
            return ToolFailureLoopGuardDecision(
                tripped=True,
                kind="signature",
                threshold=resolved_threshold,
                tool_name=tool_name,
                error_category=error_category,
                message=_create_trip_message(
                    kind="signature",
                    threshold=resolved_threshold,
                    tool_name=tool_name,
                    error_category=error_category,
                ),
            )

        if category_count >= resolved_threshold:
            return ToolFailureLoopGuardDecision(
                tripped=True,
                kind="category",
                threshold=resolved_threshold,
                error_category=error_category,
                message=_create_trip_message(
                    kind="category",
                    threshold=resolved_threshold,
                    error_category=error_category,
                ),
            )

    return _NOT_TRIPPED


def _normalize_threshold(threshold: int | None) -> int:
    if threshold is None:
        return get_tool_failure_loop_threshold()
    if (
        not isinstance(threshold, int)
        or isinstance(threshold, bool)
        or threshold < 0
        or threshold > 2**53 - 1
    ):
        return DEFAULT_TOOL_FAILURE_LOOP_THRESHOLD
    return threshold


def _reset(state: ToolFailureLoopGuardState) -> None:
    state.signature_counts.clear()
    state.category_counts.clear()
    state.path_counts.clear()


def _get_tool_result_blocks(messages: list[Any]) -> list[Any]:
    # Harvest from user messages only (toolFailureLoopGuard.ts:192) —
    # attachment messages in the batch are deliberately skipped.
    blocks: list[Any] = []
    for message in messages:
        if not isinstance(message, UserMessage):
            continue
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            # The loop's tool_results are always real UserMessages with
            # ToolResultBlock content (query.py:990-999) — no dict
            # fallback needed.
            if getattr(block, "type", None) == "tool_result":
                blocks.append(block)
    return blocks


def _tool_result_content_to_string(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(_tool_result_content_to_string(item) for item in content)
    if content is None:
        return ""
    text = getattr(content, "text", None)
    if text is None and isinstance(content, dict):
        text = content.get("text")
    if isinstance(text, str):
        return text
    return str(content)


def _is_ignored_synthetic_tool_result(content: str) -> bool:
    normalized = _normalize_tool_result_text(content).lower()
    unbracketed = re.sub(r"^\[(.*)\]$", r"\1", normalized).strip()
    without_error_prefix = re.sub(r"^error:\s*", "", unbracketed).strip()

    return (
        without_error_prefix == "interrupted by user"
        or without_error_prefix.startswith("request interrupted by user")
        or without_error_prefix == "user rejected tool use"
        or without_error_prefix.startswith(
            "the user doesn't want to proceed with this tool use"
        )
        or without_error_prefix.startswith(
            "the user doesn't want to take this action right now"
        )
        or without_error_prefix == "streaming fallback - tool execution discarded"
        or without_error_prefix.startswith("cancelled: parallel tool call")
    )


def _normalize_error_category(content: str) -> str:
    normalized = _normalize_tool_result_text(content)

    if re.search(r"\bInputValidationError\b", normalized, re.IGNORECASE):
        return "InputValidationError"
    if re.search(r"Invalid tool parameters", normalized, re.IGNORECASE):
        return "InputValidationError"
    if re.search(r"No such tool available", normalized, re.IGNORECASE):
        return "NoSuchTool"
    if re.search(r"unknown tool:", normalized, re.IGNORECASE):
        # Python registry phrasing (registry.py:108) for TS "No such tool".
        return "NoSuchTool"
    if re.search(r"\b(EACCES|EPERM)\b", normalized, re.IGNORECASE):
        return "PermissionError"
    if re.search(r"permission denied", normalized, re.IGNORECASE):
        return "PermissionError"
    if re.search(r"\bENOENT\b", normalized, re.IGNORECASE) or re.search(
        r"not found", normalized, re.IGNORECASE
    ):
        return "NotFound"
    if re.search(r"No such file or directory", normalized, re.IGNORECASE):
        # Python OSError phrasing (no ENOENT literal in str(OSError)).
        return "NotFound"
    if re.search(r"Error writing file", normalized, re.IGNORECASE):
        return "FileWriteError"

    return (
        normalized.lower()[:MAX_FALLBACK_CATEGORY_LENGTH] or "unknown error"
    )


def _normalize_tool_result_text(content: str) -> str:
    stripped = re.sub(r"</?tool_use_error[^>]*>", " ", content, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", stripped).strip()


def _extract_normalized_path(input_value: Any) -> str | None:
    if not isinstance(input_value, dict):
        return None

    for field_name in ("file_path", "path", "notebook_path"):
        value = input_value.get(field_name)
        if not isinstance(value, str):
            continue
        normalized = _normalize_path(value)
        if normalized:
            return normalized

    return None


def _normalize_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    normalized = re.sub(r"/{2,}", "/", normalized)
    normalized = re.sub(r"/+$", "", normalized)

    if normalized == "" and path.strip().startswith("/"):
        return "/"
    return normalized


def _increment_counter(counts: dict[str, int], key: str) -> int:
    counts[key] = counts.get(key, 0) + 1
    return counts[key]


def _create_trip_message(
    *,
    kind: str,
    threshold: int,
    path: str | None = None,
    tool_name: str | None = None,
    error_category: str | None = None,
) -> str:
    if kind == "path":
        reason = f"The path `{path}` failed {threshold} times."
    elif kind == "signature":
        reason = (
            f"`{tool_name}` failed {threshold} times with `{error_category}`."
        )
    else:
        reason = f"Tool calls failed {threshold} times with `{error_category}`."

    return "\n".join([
        "Stopped: repeated tool failures detected.",
        "",
        f"{reason} Please inspect permissions, path, or tool schema before retrying.",
    ])
