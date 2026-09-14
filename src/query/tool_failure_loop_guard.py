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

Second divergence (intentional): counters increment once per BATCH per
distinct key. TS iterates its ``failures`` array and increments once per
failing BLOCK (toolFailureLoopGuard.ts:137,173,201), so a single assistant
turn that issues ``threshold`` parallel calls which all fail the same way
trips the guard immediately -- on the model's FIRST mistake, with no chance
to correct. Measured on terminal-bench 2.1: that ended two runs after 3 and
4 turns (31s / 38s), each on a plain ``python3: command not found`` the
model would have recovered from, while a serially-calling model on the same
task images hit the same missing interpreter and was never stopped. The
penalty landed purely on HOW a model batches its calls, not on whether it
was actually looping, so the guard was measuring the wrong thing. Per-batch
counting matches this module's own stated contract ("consecutive tool
batches").

The change is strictly permissive: every counter is pointwise <= its old
value at every step (all reset paths are untouched) and every trip predicate
is monotone in the counters, so it can only DELAY a trip, never cause one
that would not have happened. How much later, measured by differential fuzz
against the old behavior at threshold 3: a homogeneous loop trips at most
``threshold - 1`` batches later; once failures vary or successes interleave
-- which clear ``signature_counts`` / ``category_counts`` -- the delay grows
(observed up to ~20 batches at a 25% interleaved success rate). The trip is
always eventually reached, never suppressed, and ``max_turns`` bounds the
worst case. Deliberate: a model succeeding a quarter of the time is making
progress, not looping.

Third divergence (intentional): the generic fallback category (no named
error pattern matched) is keyed off the TAIL of the tool result, not the
head. TS and the original Python port both took ``text[:120]``. For Bash
specifically, tool_result content is stdout + stderr + an exit-code
sentence in that order, so the head is dominated by the command's own
stdout (a startup banner, progress output) while the actual differentiator
-- a traceback's exception line, a compiler's final error -- sits at the
tail. Observed in practice: three genuinely different bugs in a script
that prints an identical banner before crashing each time were categorized
as ONE recurring signature and tripped the guard after attempt 3, even
though each attempt fixed the prior bug and hit a new one. Taking the tail
(and preferring a matched ``Traceback (most recent call last):`` block's
tail when present, since that isolates the exception line from any stderr
preamble) fixes this without weakening detection of an actually-recurring
error, since a truly identical failure has an identical tail too.
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
    persistent_signature_counts: dict[str, int] = field(default_factory=dict)
    signature_counts: dict[str, int] = field(default_factory=dict)
    category_counts: dict[str, int] = field(default_factory=dict)
    path_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolFailureLoopGuardDecision:
    tripped: bool
    advisories: tuple[str, ...] = ()
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
    successful_tool_names: set[str] = set()
    successful_mutation_paths: set[str] = set()

    for block in _get_tool_result_blocks(tool_results):
        content = _tool_result_content_to_string(getattr(block, "content", None))
        tool_use = tool_use_by_id.get(str(getattr(block, "tool_use_id", "") or ""))

        if getattr(block, "is_error", None) is not True:
            has_success = True
            if tool_use is not None:
                tool_name = getattr(tool_use, "name", None)
                if tool_name:
                    successful_tool_names.add(tool_name)
                if tool_name in {"Edit", "MultiEdit", "Write", "NotebookEdit"}:
                    path = _extract_normalized_path(getattr(tool_use, "input", None))
                    if path:
                        successful_mutation_paths.add(path)
            continue

        if _is_ignored_synthetic_tool_result(content):
            continue

        tool_name = getattr(tool_use, "name", None) or "unknown"
        error_category = _normalize_error_category(content)
        failures.append((
            tool_name,
            error_category,
            _extract_normalized_path(getattr(tool_use, "input", None)),
        ))

    for tool_name in successful_tool_names:
        prefix = f"{tool_name}\0"
        for key in tuple(state.persistent_signature_counts):
            if key.startswith(prefix):
                del state.persistent_signature_counts[key]

    # One increment per BATCH per distinct key, not one per failing block.
    # See the "Divergence vs TS" note in the module docstring: an assistant
    # turn that issues N parallel calls which all fail the same way is ONE
    # unsuccessful batch, not N consecutive ones.
    bumped_this_batch: set[tuple[str, str]] = set()

    def _bump(counts: dict[str, int], key: str, kind: str) -> tuple[int, bool]:
        """Increment ``counts[key]`` at most once per batch.

        Returns ``(count, first_in_batch)``. For a repeat the CURRENT count
        comes back, so a duplicate can never push a counter over the
        threshold, while a first occurrence that legitimately reaches it
        still trips on this batch. ``first_in_batch`` lets the caller emit
        one advisory per key rather than one per failing block.
        """
        if (kind, key) in bumped_this_batch:
            return counts.get(key, 0), False
        bumped_this_batch.add((kind, key))
        return _increment_counter(counts, key), True

    advisories: list[str] = []
    for tool_name, error_category, _path in failures:
        count, first = _bump(
            state.persistent_signature_counts,
            f"{tool_name}\0{error_category}",
            "persistent",
        )
        if count >= resolved_threshold:
            return ToolFailureLoopGuardDecision(
                tripped=True,
                kind="signature",
                threshold=resolved_threshold,
                tool_name=tool_name,
                error_category=error_category,
                message=_create_trip_message(
                    kind="signature", threshold=resolved_threshold,
                    tool_name=tool_name, error_category=error_category,
                ),
            )
        if first and resolved_threshold > 1 and count == resolved_threshold - 1:
            advisories.append(_create_advisory_message(
                threshold=resolved_threshold,
                tool_name=tool_name,
                error_category=error_category,
            ))

    for tool_name, error_category, path in failures:
        if not path or path in successful_mutation_paths:
            continue
        path_count, _ = _bump(state.path_counts, path, "path")
        if path_count >= resolved_threshold:
            return ToolFailureLoopGuardDecision(
                tripped=True, kind="path", threshold=resolved_threshold,
                path=path,
                message=_create_trip_message(
                    kind="path", threshold=resolved_threshold, path=path,
                ),
            )

    if has_success:
        state.signature_counts.clear()
        state.category_counts.clear()
        for path in successful_mutation_paths:
            state.path_counts.pop(path, None)
        return ToolFailureLoopGuardDecision(
            tripped=False, advisories=tuple(advisories)
        )

    for tool_name, error_category, path in failures:
        signature_count, _ = _bump(
            state.signature_counts, f"{tool_name}\0{error_category}", "signature"
        )
        category_count, _ = _bump(
            state.category_counts, error_category, "category"
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

    return ToolFailureLoopGuardDecision(
        tripped=False, advisories=tuple(advisories)
    )


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
    state.persistent_signature_counts.clear()
    state.signature_counts.clear()
    state.category_counts.clear()
    state.path_counts.clear()


def _create_advisory_message(
    *, threshold: int, tool_name: str, error_category: str,
) -> str:
    safe_tool = tool_name if re.fullmatch(r"[A-Za-z0-9_.:-]+", tool_name) else "unknown tool"
    known_categories = {
        "InputValidationError", "NoSuchTool", "PermissionError",
        "NotFound", "FileWriteError",
    }
    safe_category = error_category if error_category in known_categories else "unknown error"
    return (
        "Warning: repeated tool failures are close to stopping this query.\n\n"
        f"`{safe_tool}` failed {threshold - 1}/{threshold} times with "
        f"`{safe_category}`. One more matching failure will stop the query. "
        "Try a different tool, or verify the path, permissions, and tool "
        "inputs before retrying."
    )


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

    # Generic fallback. Bash results are stdout + stderr + an exit-code
    # sentence, in that order (bash_tool.py:_assemble_bash_body /
    # _bash_map_result_to_api), so a long-running command's own stdout
    # (banner/progress text, often near-identical across genuinely
    # different failures) sits at the head while the actual differentiator
    # -- a traceback's exception line, or a compiler's final error -- sits
    # at the tail, just before the exit-code sentence. Strip that sentence,
    # then prefer the tail over the head so distinct failures don't get
    # collapsed into one signature by a shared stdout prefix.
    without_exit_code = re.sub(
        r"\s*Command failed with exit code \d+\s*$", "", normalized, flags=re.IGNORECASE
    )
    traceback_match = re.search(
        r"Traceback \(most recent call last\):.*$", without_exit_code, re.IGNORECASE
    )
    signal = traceback_match.group(0) if traceback_match else without_exit_code

    return (
        signal.lower()[-MAX_FALLBACK_CATEGORY_LENGTH:] or "unknown error"
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
