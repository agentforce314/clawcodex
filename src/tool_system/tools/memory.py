"""The ``Memory`` tool — persistent curated memory (hermes-agent port).

Single entry point over :class:`src.memory.MemoryStore` with two calling
shapes (donor: ``tools/memory_tool.py``):

* single op — ``{action: add|replace|remove, target, content?, old_text?}``
* batch (preferred) — ``{target, operations: [{action, content?, old_text?}, …]}``
  applied atomically against the FINAL char budget in one call.

Design notes kept from the donor:

* no ``read``/``list`` action — memory is ambient (the frozen snapshot in
  the system prompt); error paths return ``current_entries`` when the
  model genuinely needs them (over-budget, missing ``old_text``);
* recoverable failures are returned as structured ``success: false`` JSON
  (NOT ``is_error`` tool failures) so converters don't prepend ``Error:``
  to what is really a consolidate-and-retry protocol response;
* the optional write-approval gate stages the exact payload instead of
  committing when ``memory_write_approval`` is on (``staged: true`` result
  shape — downstream consumers must not treat it as a committed write).
"""

from __future__ import annotations

import json
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult

MEMORY_TOOL_NAME = "Memory"

_VALID_TARGETS = ("memory", "user")
_VALID_ACTIONS = ("add", "replace", "remove")


def _store():
    from src.memory import get_memory_store

    return get_memory_store()


def _missing_old_text_error(store: Any, target: str, action: str) -> dict[str, Any]:
    """Recoverable error for a replace/remove call without ``old_text``.

    A bare "old_text is required" is a dead end — some structured-output
    clients drop optional fields. Return the current inventory plus a
    retry instruction so the model can reissue with ``old_text`` set to a
    unique substring of the entry it means (donor issues #43412/#49466).
    """
    entries = store._entries_for(target)
    current = store._char_count(target)
    limit = store._char_limit(target)
    return {
        "success": False,
        "error": (
            f"'{action}' needs old_text -- a short unique substring of the entry "
            f"to {action}. None was provided. Reissue the {action} with old_text "
            f"set to part of one of the current_entries below."
        ),
        "current_entries": entries,
        "usage": f"{current:,}/{limit:,}",
    }


def _apply_write_gate(payload: dict[str, Any], summary: str) -> dict[str, Any] | None:
    """Consult the write-approval gate. Returns the staged-result dict when
    the write must not proceed, or None to perform the real write. Fails
    open (gate module problems must not block memory writes)."""
    try:
        from src.memory import write_approval as wa

        if not wa.write_approval_enabled():
            return None
        record = wa.stage_write(payload, summary=summary)
        return {
            "success": True,
            "staged": True,
            "pending_id": record["id"],
            "message": (
                "Memory write approval is on: the write was staged for user "
                "review (not committed). The user can apply it via "
                "/memory approve."
            ),
        }
    except Exception:  # noqa: BLE001 — fail open, donor behavior
        return None


def _summarize_single(action: str, target: str, content: str | None, old_text: str | None) -> str:
    label = "user profile" if target == "user" else "memory"
    if action == "add":
        return f"add to {label}: {(content or '')[:120]}"
    if action == "replace":
        return f"replace in {label}: {(old_text or '')[:60]} -> {(content or '')[:60]}"
    return f"remove from {label}: {(old_text or '')[:120]}"


def _summarize_batch(target: str, operations: list[dict[str, Any]]) -> str:
    label = "user profile" if target == "user" else "memory"
    return f"apply {len(operations)} op(s) to {label}"


def _memory_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    action = tool_input.get("action") or ""
    target = tool_input.get("target") or "memory"
    content = tool_input.get("content")
    old_text = tool_input.get("old_text")
    operations = tool_input.get("operations")

    if target not in _VALID_TARGETS:
        raise ToolInputError(f"Invalid target '{target}'. Use 'memory' or 'user'.")

    store = _store()

    # ── batch path ────────────────────────────────────────────────────
    if operations:
        if not isinstance(operations, list):
            raise ToolInputError(
                "operations must be a list of {action, content?, old_text?} objects."
            )
        staged = _apply_write_gate(
            {"action": "batch", "target": target, "operations": operations},
            _summarize_batch(target, operations),
        )
        if staged is not None:
            return ToolResult(name=MEMORY_TOOL_NAME, output=staged)
        return ToolResult(
            name=MEMORY_TOOL_NAME, output=store.apply_batch(target, operations)
        )

    # ── single-op path ────────────────────────────────────────────────
    # Validate required params BEFORE the gate so an invalid write is
    # rejected immediately rather than staged and failing at approve time.
    if action not in _VALID_ACTIONS:
        raise ToolInputError(
            f"Unknown action '{action}'. Use add, replace, or remove — or pass "
            f"an 'operations' batch."
        )
    if action == "add" and not content:
        raise ToolInputError("content is required for 'add'.")
    if action == "replace" and not old_text:
        return ToolResult(
            name=MEMORY_TOOL_NAME,
            output=_missing_old_text_error(store, target, "replace"),
        )
    if action == "replace" and not content:
        raise ToolInputError("content is required for 'replace'.")
    if action == "remove" and not old_text:
        return ToolResult(
            name=MEMORY_TOOL_NAME,
            output=_missing_old_text_error(store, target, "remove"),
        )

    staged = _apply_write_gate(
        {"action": action, "target": target, "content": content, "old_text": old_text},
        _summarize_single(action, target, content, old_text),
    )
    if staged is not None:
        return ToolResult(name=MEMORY_TOOL_NAME, output=staged)

    if action == "add":
        result = store.add(target, content or "")
    elif action == "replace":
        result = store.replace(target, old_text or "", content or "")
    else:
        result = store.remove(target, old_text or "")

    return ToolResult(name=MEMORY_TOOL_NAME, output=result)


def _memory_enabled() -> bool:
    try:
        from src.settings.settings import get_settings

        return bool(getattr(get_settings(), "memory_store_enabled", True))
    except Exception:  # noqa: BLE001 — default on, donor parity
        return True


def _tool_use_summary(tool_input: dict[str, Any] | None) -> str | None:
    if not tool_input:
        return None
    target = tool_input.get("target") or "memory"
    label = "user profile" if target == "user" else "memory"
    ops = tool_input.get("operations")
    if isinstance(ops, list) and ops:
        return f"Update {label} ({len(ops)} ops)"
    action = tool_input.get("action") or "update"
    return f"{str(action).capitalize()} {label} entry"


# Donor MEMORY_SCHEMA description — the schema is a behavior program
# (HOW/WHEN/IF FULL/TARGETS/SKIP policy travels with the API surface).
_MEMORY_PROMPT = (
    "Save durable facts to persistent memory that survive across sessions. Memory is "
    "injected into every future session, so keep entries compact and high-signal.\n\n"
    "HOW: make ALL your changes in ONE call via an 'operations' array (each item: "
    "{action, content?, old_text?}). The batch applies atomically and the char limit is "
    "checked only on the FINAL result — so a single call can remove/replace stale entries "
    "to free room AND add new ones, even when an add alone would overflow. The response "
    "reports current/limit chars and confirms completion; one batch call finishes the "
    "update, so don't repeat it. Use the bare action/content/old_text fields only for a "
    "single lone change.\n\n"
    "WHEN: save proactively when the user states a preference, correction, or personal "
    "detail, or you learn a stable fact about their environment, conventions, or workflow. "
    "Priority: user preferences & corrections > environment facts > procedures. The best "
    "memory stops the user repeating themselves.\n\n"
    "IF FULL: an add is rejected with the current entries shown. Reissue as ONE batch that "
    "removes or shortens enough stale entries and adds the new one together.\n\n"
    "TARGETS: 'user' = who the user is (name, role, preferences, style). 'memory' = your "
    "notes (environment, conventions, tool quirks, lessons).\n\n"
    "Write memories as declarative facts, not instructions to yourself: "
    "'User prefers concise responses' ✓ — 'Always respond concisely' ✗.\n\n"
    "SKIP: trivial/obvious info, easily re-discovered facts, raw data dumps, task progress, "
    "completed-work logs, temporary TODO state. Do not record PR numbers, commit SHAs, or "
    "anything that will be stale in a week."
)


MemoryTool: Tool = build_tool(
    name=MEMORY_TOOL_NAME,
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "The action to perform (single-op shape). Omit when using 'operations'.",
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "Which memory store: 'memory' for personal notes, 'user' for the user profile.",
            },
            "content": {
                "type": "string",
                "description": "The entry content. Required for 'add' and 'replace' (single-op shape).",
            },
            "old_text": {
                "type": "string",
                "description": (
                    "REQUIRED for 'replace' and 'remove' (single-op shape): a short unique "
                    "substring identifying the existing entry to modify. Omit only for 'add'."
                ),
            },
            "operations": {
                "type": "array",
                "description": (
                    "Batch shape: a list of operations applied atomically in one call "
                    "against the final char budget. Preferred when making multiple changes "
                    "or consolidating to make room. Each item is {action, content?, old_text?}."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["add", "replace", "remove"]},
                        "content": {"type": "string", "description": "Entry content for add/replace."},
                        "old_text": {"type": "string", "description": "Substring identifying the entry for replace/remove."},
                    },
                    "required": ["action"],
                },
            },
        },
        "required": ["target"],
    },
    call=_memory_call,
    prompt=_MEMORY_PROMPT,
    description="Save durable facts to persistent cross-session memory (bounded MEMORY.md / USER.md stores).",
    max_result_size_chars=100_000,
    is_enabled=_memory_enabled,
    is_read_only=lambda _input: False,
    # The store serializes every mutation behind a .lock sidecar +
    # reload-under-lock, so parallel dispatch is safe.
    is_concurrency_safe=lambda _input: True,
    get_tool_use_summary=_tool_use_summary,
    to_auto_classifier_input=lambda input_data: json.dumps(
        {
            "action": (input_data or {}).get("action") or "batch",
            "target": (input_data or {}).get("target"),
            "ops": len((input_data or {}).get("operations") or []),
        }
    ),
)
