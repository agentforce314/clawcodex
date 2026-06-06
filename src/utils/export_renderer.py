"""Structured, surface-agnostic conversation export renderers.

Port of the *pure* renderers in ``typescript/src/utils/exportRenderer.tsx``:
``renderMessagesToMarkdown``, ``renderMessagesToJSON``, a from-scratch
plain-text transcriber, and the ``render_messages_for_export`` dispatcher.

Deliberate divergences from the TS source (documented for parity review):

* **No Ink/terminal renderer.** TS's ``text`` format streams the React/Ink UI
  through ``renderToAnsiString``; that is surface-coupled and cannot be
  line-ported. Python's ``text`` format is a standalone plain-text transcript
  walking the same structured content as the markdown renderer (minus markdown
  syntax). The async ``streamRenderedMessages``/``renderMessagesToPlainText``
  Ink path is intentionally NOT ported.
* **Terminal-output envelope parser scoped out.** Python ``src/`` never emits
  ``<bash-stdout>``/``<local-command-stdout>``/``<bash-input>`` (grep-confirmed
  zero occurrences), so ``getTerminalOutputs``/``parseTerminalOutputs`` would
  always return ``None``. Those branches are dead and omitted.
* **Internal-text tags.** Of TS's nine ``INTERNAL_TEXT_TAGS`` only
  ``<task-notification>`` is live in Python (``src/constants/xml.py``; emitted
  by the coordinator/task-notification path as user-role content). So
  ``strip_top_level_internal_text`` strips ``<system-reminder>`` +
  ``<task-notification>`` blocks only.
* **Synthetic-content markers.** Mirror TS ``isSyntheticContent``'s five
  bracketed *display literals* verbatim (see ``_SYNTHETIC_FIRST_TEXTS``), so the
  synthetic-message filter stays byte-identical to TS.

Messages may be flat dataclasses (``src/types/messages.py`` — ``.content``
directly) or plain dicts/wire objects (TS-style ``{'message': {'content': …}}``
or flat ``{'content': …}``). Content blocks may be block dataclasses or dicts.
Block reads use camel/snake fallbacks so both shapes serialize identically; the
JSON output is always camelCase (``toolUseId``/``isError``), wire-compatible
with TS exports.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import is_dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

from src.constants.xml import TASK_NOTIFICATION_TAG
from src.types.content_blocks import content_block_to_dict
from src.types.messages import (
    INTERRUPT_MESSAGE,
    INTERRUPT_MESSAGE_FOR_TOOL_USE,
    SYNTHETIC_TOOL_RESULT_PLACEHOLDER,
)
from src.utils.export_formats import ExportFormat

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

SKIP_MESSAGE_TYPES = frozenset({"progress", "attachment"})
SKIP_SYSTEM_SUBTYPES = frozenset({"api_metrics"})

# Only <task-notification> of TS's INTERNAL_TEXT_TAGS is live in Python.
INTERNAL_TEXT_TAGS = [TASK_NOTIFICATION_TAG]

# First-text-block markers identifying a synthetic (interrupt/cancel/reject/
# no-response) message that should be excluded from export. These mirror the
# five *bracketed display literals* hard-coded in TS ``isSyntheticContent``
# (exportRenderer.tsx:584-588) verbatim — NOT the semantic message constants.
# Two equal Python constants (``INTERRUPT_MESSAGE`` /
# ``INTERRUPT_MESSAGE_FOR_TOOL_USE``); the other three are pure display strings
# with no Python equivalent (Python's ``CANCEL_MESSAGE`` / ``REJECT_MESSAGE`` /
# ``NO_RESPONSE_REQUESTED`` are full sentences, not these brackets), so they are
# ported as literals to keep the synthetic-message filter byte-identical to TS.
_SYNTHETIC_FIRST_TEXTS = frozenset(
    {
        INTERRUPT_MESSAGE,  # "[Request interrupted by user]"
        INTERRUPT_MESSAGE_FOR_TOOL_USE,  # "[Request interrupted by user for tool use]"
        "[Request cancelled]",
        "[Tool use rejected]",
        "[No response requested]",
    }
)

_SYSTEM_REMINDER_RE = re.compile(
    r"<system-reminder\b[^>]*>.*?</system-reminder>", re.DOTALL
)


def _internal_tag_regex(tag: str) -> "re.Pattern[str]":
    escaped = re.escape(tag)
    return re.compile(rf"<{escaped}\b[^>]*>.*?</{escaped}>", re.DOTALL)


_INTERNAL_TEXT_TAG_REGEXES = [_internal_tag_regex(tag) for tag in INTERNAL_TEXT_TAGS]


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    """JS ``new Date().toISOString()`` shape: ``YYYY-MM-DDTHH:MM:SS.mmmZ``."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def strip_system_reminder_blocks(text: str) -> str:
    return _SYSTEM_REMINDER_RE.sub("", text).strip()


def strip_top_level_internal_text(text: str) -> str:
    stripped = strip_system_reminder_blocks(text)
    for regex in _INTERNAL_TEXT_TAG_REGEXES:
        stripped = regex.sub("", stripped)
    return stripped.strip()


def is_internal_text(text: str) -> bool:
    return len(strip_top_level_internal_text(text)) == 0


def looks_like_json(s: str) -> bool:
    trimmed = s.strip()
    return (trimmed.startswith("{") and trimmed.endswith("}")) or (
        trimmed.startswith("[") and trimmed.endswith("]")
    )


def markdown_fence_for(content: str) -> str:
    longest = 0
    for match in re.finditer(r"`+", content):
        longest = max(longest, len(match.group(0)))
    return "`" * max(3, longest + 1)


def safe_json_value(value: Any, seen: Optional[set] = None) -> Any:
    """Circular-safe JSON-able projection. Strings get system-reminder blocks
    stripped (NOT the broader internal-text tags — matches TS ``safeJsonValue``).
    """
    if seen is None:
        seen = set()

    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str):
            return strip_system_reminder_blocks(value)
        return value

    if isinstance(value, (list, tuple)):
        if id(value) in seen:
            return "[Circular]"
        seen.add(id(value))
        try:
            return [safe_json_value(item, seen) for item in value]
        except Exception:
            return "[Unserializable]"
        finally:
            seen.discard(id(value))

    if isinstance(value, Mapping):
        if id(value) in seen:
            return "[Circular]"
        seen.add(id(value))
        try:
            result: dict[str, Any] = {}
            try:
                keys = list(value.keys())
            except Exception:
                return "[Unserializable]"
            for key in keys:
                try:
                    result[key] = safe_json_value(value[key], seen)
                except Exception:
                    result[key] = "[Unserializable]"
            return result
        finally:
            seen.discard(id(value))

    if is_dataclass(value) and not isinstance(value, type):
        return safe_json_value(content_block_to_dict(value), seen)

    return str(value)


def safe_stringify(value: Any, indent: Optional[int] = None) -> str:
    try:
        projected = safe_json_value(value)
        if indent is None:
            return json.dumps(projected, separators=(",", ":"), ensure_ascii=False)
        return json.dumps(projected, indent=indent, ensure_ascii=False)
    except Exception:
        return str(value)


# --------------------------------------------------------------------------- #
# Message / block accessors (dataclass + dict dual-mode)
# --------------------------------------------------------------------------- #


def _field(msg: Any, name: str, default: Any = None) -> Any:
    if isinstance(msg, Mapping):
        return msg.get(name, default)
    return getattr(msg, name, default)


def _is_msg_obj(msg: Any) -> bool:
    return msg is not None and not isinstance(msg, (str, bytes, bool, int, float))


def _is_obj(block: Any) -> bool:
    """Mirror of JS ``!!block && typeof block === 'object'`` (arrays included)."""
    return block is not None and not isinstance(block, (str, bytes, bool, int, float))


def _btype(block: Any) -> Any:
    if isinstance(block, Mapping):
        return block.get("type")
    return getattr(block, "type", None)


def _btext(block: Any) -> Any:
    if isinstance(block, Mapping):
        return block.get("text")
    return getattr(block, "text", None)


def _bcontent(block: Any) -> Any:
    if isinstance(block, Mapping):
        return block.get("content")
    return getattr(block, "content", None)


def _normalize_block(block: Any) -> dict:
    return dict(block) if isinstance(block, Mapping) else content_block_to_dict(block)


def extract_message_content(msg: Any) -> Any:
    if isinstance(msg, Mapping):
        nested = msg.get("message")
        if isinstance(nested, Mapping):
            return nested.get("content")
        if "content" in msg:
            return msg["content"]
        return None
    nested = getattr(msg, "message", None)
    if isinstance(nested, Mapping):
        return nested.get("content")
    return getattr(msg, "content", None)


# --------------------------------------------------------------------------- #
# Block predicates
# --------------------------------------------------------------------------- #


def is_text_block(block: Any) -> bool:
    return _is_obj(block) and _btype(block) == "text" and isinstance(_btext(block), str)


def is_tool_result_block(block: Any) -> bool:
    return _is_obj(block) and _btype(block) == "tool_result"


def is_internal_text_block(block: Any) -> bool:
    return is_text_block(block) and is_internal_text(_btext(block))


def is_tool_result_message_content(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    if not any(is_tool_result_block(b) for b in content):
        return False
    return all(is_tool_result_block(b) or is_internal_text_block(b) for b in content)


def is_synthetic_tool_result_block(block: Any) -> bool:
    if not is_tool_result_block(block):
        return False
    content = _bcontent(block)
    if content == SYNTHETIC_TOOL_RESULT_PLACEHOLDER:
        return True
    if isinstance(content, list):
        return all(
            is_text_block(item) and _btext(item) == SYNTHETIC_TOOL_RESULT_PLACEHOLDER
            for item in content
        )
    return False


def is_synthetic_content(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    first = content[0] if content else None
    has_synthetic_tool_result = any(is_synthetic_tool_result_block(b) for b in content)
    if is_text_block(first) and _btext(first) in _SYNTHETIC_FIRST_TEXTS:
        return True
    return has_synthetic_tool_result and all(
        is_synthetic_tool_result_block(b) or is_internal_text_block(b) for b in content
    )


def is_known_message_type(message_type: str) -> bool:
    return message_type in ("user", "assistant", "system", "tool")


def has_exportable_structured_content(content: Any) -> bool:
    if content is None:
        return False
    if isinstance(content, str):
        return len(content.strip()) > 0 and not is_internal_text(content)
    if not isinstance(content, list):
        return True
    for block in content:
        if is_text_block(block):
            text = _btext(block)
            if len(text.strip()) > 0 and not is_internal_text(text):
                return True
            continue
        if not _is_obj(block):
            if block is not None:
                return True
            continue
        block_type = _btype(block)
        if block_type in ("thinking", "redacted_thinking"):
            continue
        return True
    return False


def should_export_structured_message(msg: Any, msg_type: str) -> bool:
    if msg_type in SKIP_MESSAGE_TYPES:
        return False
    if msg_type == "system":
        subtype = _field(msg, "subtype", None)
        subtype_str = "" if subtype is None else str(subtype)
        if subtype_str in SKIP_SYSTEM_SUBTYPES:
            return False
    if _field(msg, "isMeta") is True or _field(msg, "isCompactSummary") is True:
        return False
    content = extract_message_content(msg)
    if is_synthetic_content(content):
        return False
    return has_exportable_structured_content(content) or not is_known_message_type(
        msg_type
    )


# --------------------------------------------------------------------------- #
# Type / role mapping
# --------------------------------------------------------------------------- #


def message_heading(message_type: str) -> str:
    if message_type == "user":
        return "User"
    if message_type == "assistant":
        return "Assistant"
    if message_type == "system":
        return "System"
    if message_type == "tool":
        return "Tool Result"
    return (message_type[:1].upper() + message_type[1:]) if message_type else message_type


def to_role(message_type: str) -> str:
    return message_type if message_type in ("user", "assistant", "system", "tool") else "unknown"


def to_exported_message_type(msg_type: str, content: Any) -> str:
    if is_tool_result_message_content(content):
        return "tool"
    if msg_type in ("user", "assistant", "system", "tool"):
        return msg_type
    if msg_type != "unknown":
        return "unknown"
    return msg_type


def to_role_for_content(msg_type: str, content: Any) -> str:
    if is_tool_result_message_content(content):
        return "tool"
    return to_role(msg_type)


# --------------------------------------------------------------------------- #
# Markdown renderer
# --------------------------------------------------------------------------- #


def render_text_markdown(text: str, lines: List[str]) -> None:
    if not text:
        return
    stripped = strip_top_level_internal_text(text)
    if not stripped:
        return
    lines.append(stripped)
    lines.append("")


def render_unknown_content_markdown(content: Any, lines: List[str]) -> None:
    lines.append("*[unknown content]*")
    lines.append("")
    serialized = safe_stringify(content, 2)
    marker = markdown_fence_for(serialized)
    lines.append(f"{marker}json")
    lines.append(serialized)
    lines.append(marker)
    lines.append("")


def render_content_block_markdown(
    block: dict, lines: List[str], skip_subheading: bool = False
) -> None:
    block_type = block.get("type")

    if block_type == "text":
        text = block.get("text")
        render_text_markdown(text if isinstance(text, str) else "", lines)
        return

    if block_type == "tool_use":
        name = block.get("name")
        name = name if isinstance(name, str) else "unknown"
        lines.append(f"### Tool Use: {name}")
        lines.append("")
        tool_input = block.get("input")
        if tool_input is not None:
            input_json = safe_stringify(tool_input, 2)
            marker = markdown_fence_for(input_json)
            lines.append(f"{marker}json")
            lines.append(input_json)
            lines.append(marker)
            lines.append("")
        return

    if block_type == "tool_result":
        if not skip_subheading:
            lines.append("### Tool Result")
            lines.append("")
        result_content = block.get("content")
        if result_content is not None:
            as_string = (
                strip_system_reminder_blocks(result_content)
                if isinstance(result_content, str)
                else safe_stringify(result_content, 2)
            )
            fence = "json" if looks_like_json(as_string) else "text"
            marker = markdown_fence_for(as_string)
            lines.append(f"{marker}{fence}")
            lines.append(as_string)
            lines.append(marker)
            lines.append("")
        if block.get("isError") or block.get("is_error"):
            lines.append("*(Error)*")
            lines.append("")
        return

    if block_type == "image":
        lines.append("[Image attachment]")
        lines.append("")
        return

    if block_type == "thinking":
        return

    if block_type == "redacted_thinking":
        return

    label = block_type if block_type is not None else "unknown"
    lines.append(f"*[{label} content block]*")
    lines.append("")
    serialized = safe_stringify(block, 2)
    marker = markdown_fence_for(serialized)
    lines.append(f"{marker}json")
    lines.append(serialized)
    lines.append(marker)
    lines.append("")


def render_messages_to_markdown(messages: Any) -> str:
    lines: List[str] = []
    lines.append("# Conversation Export")
    lines.append("")
    lines.append(f"Exported: {_now_iso()}")
    lines.append("Format: Markdown")
    lines.append("")

    for msg in messages:
        if not _is_msg_obj(msg):
            continue
        raw_type = _field(msg, "type", None)
        msg_type = "unknown" if raw_type is None else str(raw_type)
        if not should_export_structured_message(msg, msg_type):
            continue

        content = extract_message_content(msg)
        if content is None:
            continue

        is_tool_result_message = msg_type in ("user", "tool") and (
            is_tool_result_message_content(content)
        )
        heading = "Tool Result" if is_tool_result_message else message_heading(msg_type)

        content_lines: List[str] = []
        if isinstance(content, str):
            render_text_markdown(content, content_lines)
        elif isinstance(content, list):
            for block in content:
                if not _is_obj(block):
                    render_unknown_content_markdown(block, content_lines)
                    continue
                render_content_block_markdown(
                    _normalize_block(block), content_lines, is_tool_result_message
                )
            if len(content_lines) > 0:
                content_lines.append("")
        else:
            render_unknown_content_markdown(content, content_lines)

        if all(line == "" for line in content_lines):
            continue

        lines.append(f"## {heading}")
        lines.append("")
        lines.extend(content_lines)

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# JSON renderer
# --------------------------------------------------------------------------- #


def serialize_content_block(block: Any) -> Any:
    if not _is_obj(block):
        return {"type": "unknown", "value": safe_json_value(block)}

    b = _normalize_block(block)
    raw_type = b.get("type")
    block_type = raw_type if isinstance(raw_type, str) else "unknown"

    if block_type == "text":
        text = b.get("text")
        if not isinstance(text, str):
            return {"type": "text", "text": ""}
        return {"type": "text", "text": strip_top_level_internal_text(text)}

    if block_type == "tool_use":
        result: dict[str, Any] = {"type": "tool_use"}
        if b.get("id") is not None:
            result["id"] = safe_json_value(b.get("id"))
        if b.get("name") is not None:
            result["name"] = str(b.get("name"))
        if b.get("input") is not None:
            result["input"] = safe_json_value(b.get("input"))
        return result

    if block_type == "tool_result":
        tool_use_id = b.get("tool_use_id")
        if tool_use_id is None:
            tool_use_id = b.get("toolUseId")
        is_error = b.get("is_error")
        if is_error is None:
            is_error = b.get("isError")
        result = {"type": "tool_result"}
        if tool_use_id is not None:
            result["toolUseId"] = str(tool_use_id)
        if b.get("content") is not None:
            result["content"] = safe_json_value(b.get("content"))
        if is_error is not None:
            result["isError"] = bool(is_error)
        return result

    if block_type == "image":
        result = {"type": "image"}
        if b.get("source") is not None:
            result["source"] = safe_json_value(b.get("source"))
        return result

    if block_type in ("thinking", "redacted_thinking"):
        return []

    return {"type": block_type, "value": safe_json_value(b)}


def serialize_content_blocks(content: Any) -> List[Any]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": strip_top_level_internal_text(content)}]
    if not isinstance(content, list):
        return [{"type": "unknown", "value": safe_json_value(content)}]

    out: List[Any] = []
    for block in content:
        if is_text_block(block) and is_internal_text(_btext(block)):
            continue
        serialized = serialize_content_block(block)
        if isinstance(serialized, list):
            out.extend(serialized)
        else:
            out.append(serialized)
    return out


def render_messages_to_json(messages: Any) -> str:
    filtered: List[tuple] = []
    for source_index, msg in enumerate(messages):
        if not _is_msg_obj(msg):
            continue
        raw_type = _field(msg, "type", None)
        msg_type_filter = "" if raw_type is None else str(raw_type)
        if should_export_structured_message(msg, msg_type_filter):
            filtered.append((msg, source_index))

    exported: List[dict] = []
    for index, (msg, source_index) in enumerate(filtered):
        raw_type = _field(msg, "type", None)
        msg_type = "unknown" if raw_type is None else str(raw_type)
        content = extract_message_content(msg)
        exported_type = to_exported_message_type(msg_type, content)
        role = to_role_for_content(msg_type, content)

        result: dict[str, Any] = {
            "index": index,
            "sourceIndex": source_index,
            "type": exported_type,
            "role": role,
            "content": serialize_content_blocks(content),
        }
        if exported_type == "unknown" and msg_type and msg_type != "unknown":
            result["rawType"] = msg_type
        subtype = _field(msg, "subtype", None)
        if isinstance(subtype, str):
            result["subtype"] = subtype
        timestamp = _field(msg, "timestamp", None)
        if isinstance(timestamp, str):
            result["timestamp"] = timestamp
        exported.append(result)

    output = {
        "version": 1,
        "format": "json",
        "exportedAt": _now_iso(),
        "messageCount": len(exported),
        "messages": exported,
    }
    return safe_stringify(output, 2)


# --------------------------------------------------------------------------- #
# Plain-text renderer (from-scratch transcript; see module docstring)
# --------------------------------------------------------------------------- #


def _plain_block_lines(block: dict, lines: List[str], skip_subheading: bool) -> None:
    block_type = block.get("type")

    if block_type == "text":
        text = block.get("text")
        stripped = strip_top_level_internal_text(text) if isinstance(text, str) else ""
        if stripped:
            lines.append(stripped)
            lines.append("")
        return

    if block_type == "tool_use":
        name = block.get("name")
        name = name if isinstance(name, str) else "unknown"
        lines.append(f"Tool Use: {name}")
        tool_input = block.get("input")
        if tool_input is not None:
            lines.append(safe_stringify(tool_input, 2))
        lines.append("")
        return

    if block_type == "tool_result":
        if not skip_subheading:
            lines.append("Tool Result")
        result_content = block.get("content")
        if result_content is not None:
            as_string = (
                strip_system_reminder_blocks(result_content)
                if isinstance(result_content, str)
                else safe_stringify(result_content, 2)
            )
            if as_string:
                lines.append(as_string)
        if block.get("isError") or block.get("is_error"):
            lines.append("(Error)")
        lines.append("")
        return

    if block_type == "image":
        lines.append("[Image attachment]")
        lines.append("")
        return

    if block_type in ("thinking", "redacted_thinking"):
        return

    label = block_type if block_type is not None else "unknown"
    lines.append(f"[{label} content block]")
    lines.append(safe_stringify(block, 2))
    lines.append("")


def render_messages_to_plain_text(messages: Any) -> str:
    lines: List[str] = []
    lines.append("Conversation Export")
    lines.append("")
    lines.append(f"Exported: {_now_iso()}")
    lines.append("Format: Text")
    lines.append("")

    for msg in messages:
        if not _is_msg_obj(msg):
            continue
        raw_type = _field(msg, "type", None)
        msg_type = "unknown" if raw_type is None else str(raw_type)
        if not should_export_structured_message(msg, msg_type):
            continue

        content = extract_message_content(msg)
        if content is None:
            continue

        is_tool_result_message = msg_type in ("user", "tool") and (
            is_tool_result_message_content(content)
        )
        heading = "Tool Result" if is_tool_result_message else message_heading(msg_type)

        content_lines: List[str] = []
        if isinstance(content, str):
            stripped = strip_top_level_internal_text(content)
            if stripped:
                content_lines.append(stripped)
                content_lines.append("")
        elif isinstance(content, list):
            for block in content:
                if not _is_obj(block):
                    content_lines.append(str(block))
                    content_lines.append("")
                    continue
                _plain_block_lines(
                    _normalize_block(block), content_lines, is_tool_result_message
                )
        else:
            content_lines.append(str(content))
            content_lines.append("")

        if all(line == "" for line in content_lines):
            continue

        lines.append(heading)
        lines.append("")
        lines.extend(content_lines)

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #


def render_messages_for_export(messages: Any, *, format: ExportFormat) -> str:
    if format == "text":
        return render_messages_to_plain_text(messages)
    if format == "markdown":
        return render_messages_to_markdown(messages)
    if format == "json":
        return render_messages_to_json(messages)
    raise ValueError(f"Unknown export format: {format!r}")


__all__ = [
    "SKIP_MESSAGE_TYPES",
    "SKIP_SYSTEM_SUBTYPES",
    "INTERNAL_TEXT_TAGS",
    "extract_message_content",
    "has_exportable_structured_content",
    "is_internal_text",
    "is_known_message_type",
    "is_synthetic_content",
    "is_tool_result_message_content",
    "looks_like_json",
    "markdown_fence_for",
    "message_heading",
    "render_content_block_markdown",
    "render_messages_for_export",
    "render_messages_to_json",
    "render_messages_to_markdown",
    "render_messages_to_plain_text",
    "safe_json_value",
    "safe_stringify",
    "serialize_content_block",
    "serialize_content_blocks",
    "should_export_structured_message",
    "strip_system_reminder_blocks",
    "strip_top_level_internal_text",
    "to_exported_message_type",
    "to_role",
    "to_role_for_content",
]
