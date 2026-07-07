"""Per-tool result persistence — Step 11 of the 14-step execution pipeline.

Mirrors ``typescript/src/utils/toolResultStorage.ts``. The TS file is huge
(~1000 LOC) because it also tracks an aggregate per-conversation budget via
``ContentReplacementState``. This module ports just the per-tool layer that
runs once per ``tool_use``:

1. Empty content → replaced with ``"(<tool> completed with no output)"``.
2. Image content → returned as-is (must reach the model intact).
3. Content above ``get_persistence_threshold(tool.name, tool.max_result_size_chars)``
   → persisted to ``{tool_results_dir}/{tool_use_id}.{txt,json}`` and replaced
   with a ``<persisted-output>`` wrapper that includes a preview.

The aggregate ``ContentReplacementState`` layer (cache-stable replacement
decisions across turns) is intentionally not ported here — it ties into
compaction/resume which is a separate chapter.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from src.tool_system.build_tool import Tool
    from src.tool_system.context import ToolContext

log = logging.getLogger(__name__)

# Mirrors typescript/src/constants/toolLimits.ts.
DEFAULT_MAX_RESULT_SIZE_CHARS: int = 50_000

# WI-5.1: per-message tool-result aggregate budget. TS constant at
# ``typescript/src/constants/toolLimits.ts:49``. Prevents the
# ``[Read, Read, Read, Read, Read]`` parallel-tool fan-out from blowing
# the context budget in a single turn — five 40K results each fit under
# the per-tool 50K threshold but sum to 200K, which is the per-message
# aggregate cap. The Nth result that would push the running aggregate
# past this cap is persisted to disk regardless of its individual size.
MAX_TOOL_RESULTS_PER_MESSAGE_CHARS: int = 200_000

# Preview size for the wrapper message — TS toolResultStorage.ts:109.
PREVIEW_SIZE_BYTES: int = 2_000

# XML wrapper that the model recognizes as "this is a persisted reference".
PERSISTED_OUTPUT_TAG: str = "<persisted-output>"
PERSISTED_OUTPUT_CLOSING_TAG: str = "</persisted-output>"

# Tool-results subdir name within the session storage directory.
TOOL_RESULTS_SUBDIR: str = "tool-results"


# ---------------------------------------------------------------------------
# Threshold resolution
# ---------------------------------------------------------------------------


def get_persistence_threshold(tool_name: str, declared_max: float) -> float:
    """Resolve the effective persistence threshold for a tool.

    Mirrors ``getPersistenceThreshold`` in
    ``typescript/src/utils/toolResultStorage.ts:55-78``.

    - ``declared_max == math.inf`` is a hard opt-out (FileReadTool — its
      output must never be persisted because the model would Read the
      persisted file in a circular loop). The infinity passes through
      regardless of the global default.
    - Otherwise the effective threshold is ``min(declared, DEFAULT)``.

    The TS reference also consults a GrowthBook override
    (``tengu_satin_quoll``); Python has no equivalent so the override hook
    is omitted.
    """
    # math.inf passes through (Python ``float`` — `int(inf)` raises).
    if declared_max == float("inf"):
        return declared_max
    return min(int(declared_max), DEFAULT_MAX_RESULT_SIZE_CHARS)


# ---------------------------------------------------------------------------
# Empty / image / size predicates
# ---------------------------------------------------------------------------


def is_tool_result_content_empty(content: Any) -> bool:
    """True when a tool_result's content is empty or effectively empty.

    Mirrors ``isToolResultContentEmpty`` in
    ``typescript/src/utils/toolResultStorage.ts:250-265``. Covers:

    - ``None`` / ``""``
    - whitespace-only strings
    - empty lists
    - lists where every block is a text block with empty/whitespace text

    Non-text blocks (images, tool_reference) are treated as non-empty.
    """
    if content is None:
        return True
    if isinstance(content, str):
        return content.strip() == ""
    if not isinstance(content, list):
        return False
    if len(content) == 0:
        return True
    for block in content:
        # Dict-form text block.
        if isinstance(block, dict):
            if block.get("type") != "text":
                return False
            text = block.get("text")
            if not isinstance(text, str) or text.strip() != "":
                return False
            continue
        # Object-form text block (e.g. dataclass with .type and .text).
        block_type = getattr(block, "type", None)
        if block_type != "text":
            return False
        text = getattr(block, "text", None)
        if not isinstance(text, str) or text.strip() != "":
            return False
    return True


def _has_image_block(content: Any) -> bool:
    """True when content contains an image (or document) content block.

    Image blocks must reach the model intact — never persist them to disk.
    """
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, dict):
            if block.get("type") in ("image", "document"):
                return True
        else:
            if getattr(block, "type", None) in ("image", "document"):
                return True
    return False


def _content_size(content: Any) -> int:
    """Approximate character size of a tool_result's content."""
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    total += len(block.get("text") or "")
                elif block.get("type") in ("image", "document"):
                    # Image bytes are counted in characters of their base64
                    # payload — we don't try to estimate; non-text blocks
                    # are not persistence candidates anyway.
                    total += 0
            else:
                t = getattr(block, "type", None)
                if t == "text":
                    total += len(getattr(block, "text", "") or "")
        return total
    return len(str(content))


# ---------------------------------------------------------------------------
# Preview generation
# ---------------------------------------------------------------------------


def generate_preview(content: str, max_bytes: int) -> tuple[str, bool]:
    """Generate a preview, truncating at a newline boundary when reasonable.

    Mirrors ``generatePreview`` in
    ``typescript/src/utils/toolResultStorage.ts:339-356``.

    Returns ``(preview, has_more)``.
    """
    if len(content) <= max_bytes:
        return content, False

    truncated = content[:max_bytes]
    last_newline = truncated.rfind("\n")
    cut_point = last_newline if last_newline > max_bytes * 0.5 else max_bytes
    return content[:cut_point], True


def _format_file_size(size: int) -> str:
    """Human-friendly size string. Mirrors TS ``formatFileSize`` roughly."""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / (1024 * 1024):.1f}MB"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PersistedToolResult:
    filepath: str
    original_size: int
    is_json: bool
    preview: str
    has_more: bool


@dataclass(frozen=True)
class PersistToolResultError:
    error: str


PersistResult = Union[PersistedToolResult, PersistToolResultError]


def is_persist_error(result: PersistResult) -> bool:
    return isinstance(result, PersistToolResultError)


def _content_to_string(content: str | list[Any]) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str, indent=2)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def persist_tool_result(
    content: str | list[Any],
    tool_use_id: str,
    *,
    tool_results_dir: Path,
) -> PersistResult:
    """Persist a tool result to disk; return the persisted-result info.

    Mirrors ``persistToolResult`` in
    ``typescript/src/utils/toolResultStorage.ts:137-184``.

    Uses ``"x"`` mode (write-and-fail-if-exists) so re-running the same
    ``tool_use_id`` (e.g. after micro-compact replays the message) is a no-op
    rather than a re-write.
    """
    is_json = isinstance(content, list)

    if is_json:
        # We cannot persist content that contains non-text blocks — the
        # caller should have already short-circuited on image blocks.
        for block in content:  # type: ignore[union-attr]
            block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if block_type not in ("text",):
                return PersistToolResultError(
                    error="Cannot persist tool results containing non-text content"
                )

    try:
        _ensure_dir(tool_results_dir)
    except OSError as exc:
        return PersistToolResultError(error=f"Failed to create tool-results dir: {exc}")

    ext = "json" if is_json else "txt"
    filepath = tool_results_dir / f"{tool_use_id}.{ext}"
    content_str = _content_to_string(content)

    try:
        # ``x`` mode: write only if the file doesn't already exist.
        with open(filepath, "x", encoding="utf-8") as f:
            f.write(content_str)
    except FileExistsError:
        # Already persisted on a prior turn — fall through and use existing
        # file path (parity with TS EEXIST handling).
        pass
    except OSError as exc:
        return PersistToolResultError(error=f"Failed to write tool result: {exc}")

    preview, has_more = generate_preview(content_str, PREVIEW_SIZE_BYTES)

    return PersistedToolResult(
        filepath=str(filepath),
        original_size=len(content_str),
        is_json=is_json,
        preview=preview,
        has_more=has_more,
    )


def build_large_tool_result_message(result: PersistedToolResult) -> str:
    """Build the ``<persisted-output>`` wrapper string the model sees.

    Mirrors ``buildLargeToolResultMessage`` in
    ``typescript/src/utils/toolResultStorage.ts:189-199``.
    """
    parts = [PERSISTED_OUTPUT_TAG]
    parts.append(
        f"Output too large ({_format_file_size(result.original_size)}). "
        f"Full output saved to: {result.filepath}"
    )
    parts.append("")  # blank line separator
    parts.append(f"Preview (first {_format_file_size(PREVIEW_SIZE_BYTES)}):")
    parts.append(result.preview)
    if result.has_more:
        parts.append("...")
    parts.append(PERSISTED_OUTPUT_CLOSING_TAG)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def maybe_persist_large_tool_result(
    tool_result_block: dict[str, Any],
    tool_name: str,
    *,
    threshold: float,
    tool_results_dir: Path,
    aggregate_chars_so_far: int = 0,
    aggregate_cap: int = MAX_TOOL_RESULTS_PER_MESSAGE_CHARS,
) -> dict[str, Any]:
    """Apply per-tool persistence to a tool_result block.

    Mirrors ``maybePersistLargeToolResult`` in
    ``typescript/src/utils/toolResultStorage.ts:272-334``.

    Returns the (possibly modified) block. Callers tracking the per-
    message aggregate budget (WI-5.1) should use
    ``compute_block_chars`` on the returned block + their running
    counter; ``maybe_persist_large_tool_result`` itself uses the
    ``aggregate_chars_so_far`` argument only to DECIDE whether to
    persist a block that would otherwise pass the per-tool threshold
    but push the running total past ``aggregate_cap``.

    Returns the original block unchanged when:
    - content is non-empty AND below ``threshold``
      AND the running aggregate WOULD NOT exceed ``aggregate_cap``
    - content contains image blocks (those reach the model intact)
    - persistence fails (we surface the original rather than lose data)

    Returns a modified block (new ``content``) when:
    - content is empty → ``"(<tool_name> completed with no output)"``
    - content is large → wrapper message with size + path + preview
    - the running aggregate WOULD exceed ``aggregate_cap`` after adding
      this block (WI-5.1: persist-to-disk to keep total within budget)
    """
    content = tool_result_block.get("content")

    # Empty-content guard. inc-4586 in TS: empty tool_result at the prompt
    # tail makes some models emit the stop sequence and end their turn with
    # no output. Inject a marker so the model always has something to react
    # to. Parity with toolResultStorage.ts:287-295.
    if is_tool_result_content_empty(content):
        new_block = dict(tool_result_block)
        new_block["content"] = f"({tool_name} completed with no output)"
        return new_block

    # Image content must not be persisted — the model needs the bytes.
    if _has_image_block(content):
        return tool_result_block

    size = _content_size(content)

    # ``threshold == inf`` is a hard opt-out — the tool author has
    # declared this output must never be persisted (circular-Read
    # case for FileReadTool, see ch06-tools.md). Skip both the
    # per-tool threshold check AND the aggregate gate; trust the
    # tool to self-bound its output. Without this guard, a large
    # Read still gets persisted whenever the running aggregate is
    # close to the cap.
    if threshold == float("inf"):
        return tool_result_block

    # WI-5.1: per-message aggregate gate. Even when this block alone is
    # under ``threshold``, if adding it would push the running total
    # over ``aggregate_cap`` we persist it to disk to keep the message
    # within budget. DEVIATION: the TS reference enforces the aggregate
    # ONLY at the wire (enforceToolResultBudget, with non-finite tools
    # excluded from replacement AND counting via skipToolNames); the
    # port decides at result-creation time instead — see
    # my-docs/ch06-tools-round3-gap-analysis.md §3. Same 200K constant
    # (toolLimits.ts:49); callers skip the counter for non-finite tools.
    aggregate_would_exceed = (aggregate_chars_so_far + size) > aggregate_cap
    if size <= threshold and not aggregate_would_exceed:
        return tool_result_block

    tool_use_id = tool_result_block.get("tool_use_id") or _hash_id(content)

    persist_result = persist_tool_result(
        content,  # type: ignore[arg-type]
        tool_use_id,
        tool_results_dir=tool_results_dir,
    )
    if is_persist_error(persist_result):
        log.warning(
            "Tool result persistence failed for %s: %s",
            tool_name,
            persist_result.error,  # type: ignore[union-attr]
        )
        return tool_result_block

    assert isinstance(persist_result, PersistedToolResult)
    message = build_large_tool_result_message(persist_result)
    new_block = dict(tool_result_block)
    new_block["content"] = message
    return new_block


def compute_block_chars(tool_result_block: dict[str, Any]) -> int:
    """Return the char-size of a tool_result block's content.

    Helper for WI-5.1 aggregate tracking. Use after
    ``process_tool_result_block`` / ``maybe_persist_large_tool_result``
    to update the running per-message aggregate counter.
    """
    return _content_size(tool_result_block.get("content"))


def process_tool_result_block(
    tool: "Tool",
    tool_use_result: Any,
    tool_use_id: str,
    *,
    tool_results_dir: Path,
    aggregate_chars_so_far: int = 0,
) -> dict[str, Any]:
    """Map a tool result to its API form and apply per-tool persistence.

    Mirrors ``processToolResultBlock`` in
    ``typescript/src/utils/toolResultStorage.ts:205-226``. The single entry
    point used by the execution pipeline (Step 11).

    Backward-compat: returns just the block (not a tuple). The caller
    threads the running aggregate counter via ``aggregate_chars_so_far``
    so this function can decide whether to force-persist a block that
    would otherwise pass the per-tool threshold but push the running
    total past ``MAX_TOOL_RESULTS_PER_MESSAGE_CHARS``. Updating the
    counter post-call is the caller's responsibility — use
    ``compute_block_chars(returned_block)`` to measure.
    """
    tool_result_block = tool.map_result_to_api(tool_use_result, tool_use_id)
    threshold = get_persistence_threshold(tool.name, tool.max_result_size_chars)
    return maybe_persist_large_tool_result(
        tool_result_block,
        tool.name,
        threshold=threshold,
        tool_results_dir=tool_results_dir,
        aggregate_chars_so_far=aggregate_chars_so_far,
    )


def _hash_id(content: Any) -> str:
    """Fallback ID for a tool_result block missing ``tool_use_id``.

    Used only when an upstream caller forgot to set the id; the deterministic
    hash makes re-runs idempotent (same content → same path).
    """
    if isinstance(content, str):
        material = content
    else:
        material = json.dumps(content, default=str, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_tool_results_dir(context: "ToolContext") -> Path:
    """Resolve the per-session tool-results dir.

    Preference order:
    1. ``~/.clawcodex/<workspace_basename>/<session_id>/tool-results/`` when the
       context has a session id.
    2. ``/tmp/clawcodex_tool_results/<pid>/tool-results/`` as a fallback.

    The TS reference uses a more elaborate path layout (project-dir hashing
    in ``utils/sessionStorage.ts``); we keep it simple and Python-native.
    Tests can monkeypatch this function or pass an explicit dir.
    """
    session_id = getattr(context, "session_id", None)
    workspace_root = getattr(context, "workspace_root", None)
    if session_id and workspace_root is not None:
        workspace_basename = Path(workspace_root).name or "workspace"
        return (
            Path.home()
            / ".clawcodex"
            / workspace_basename
            / str(session_id)
            / TOOL_RESULTS_SUBDIR
        )
    return Path("/tmp") / "clawcodex_tool_results" / str(os.getpid()) / TOOL_RESULTS_SUBDIR
