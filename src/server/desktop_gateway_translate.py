"""Frame translation: agent-server protocol → desktop gateway events.

Left side: the NDJSON frames the in-process agent emits
(``src/server/agent_server.py`` — ``stream_event``/SDK envelopes/``result``/
``system``/server-initiated ``control_request``).
Right side: the event vocabulary the desktop renderer consumes
(``ui-desktop/src/app/session/hooks/use-message-stream/gateway-event.ts``).

Each translator returns a list of ``(event_type, payload)`` tuples to push
(session_id is added by the caller). Kept pure for unit testing.
"""

from __future__ import annotations

from typing import Any

# ── usage mapping ────────────────────────────────────────────────────────────
# Backend usage counters are token-based ({input_tokens, output_tokens, …});
# the renderer merges {calls, input, output, total} into its session stats.


def usage_payload(usage: dict[str, Any] | None) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return {
        "calls": 1,
        "input": input_tokens,
        "output": output_tokens,
        "total": input_tokens + output_tokens,
    }


# ── content-block helpers ────────────────────────────────────────────────────


def _iter_blocks(content: Any):
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                yield block


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = [b.get("text", "") for b in _iter_blocks(content) if b.get("type") == "text"]
    return "".join(p for p in parts if isinstance(p, str))


def as_text(value: Any) -> str:
    """Anything the agent hands us where the UI expects prose → a string.

    The renderer's coerceGatewayText JSON-stringifies a bare object it can't
    read, which is how a raw ``{"type":"tool_use",…}`` block ended up printed
    inside an assistant bubble. Flatten content blocks here and keep only the
    text ones, so a tool block can never reach the transcript as prose.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "".join(as_text(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") in ("tool_use", "tool_result", "thinking"):
            return ""
        for key in ("text", "output_text", "content"):
            if key in value:
                return as_text(value[key])
        return ""
    return str(value)


def _tool_output_text(content: Any) -> str:
    """Best-effort plain text of a tool_result block's content."""
    if isinstance(content, str):
        return content
    return _text_of(content)


# ── frame translators ────────────────────────────────────────────────────────


def translate_stream_event(frame: dict[str, Any]) -> list[tuple[str, Any]]:
    event = frame.get("event") or {}
    if event.get("type") != "content_block_delta":
        return []
    delta = event.get("delta") or {}
    kind = delta.get("type")
    if kind == "text_delta":
        text = as_text(delta.get("text"))
        return [("message.delta", {"text": text})] if text else []
    if kind == "thinking_delta":
        thinking = as_text(delta.get("thinking"))
        return [("reasoning.delta", {"text": thinking})] if thinking else []
    return []


def translate_sdk_envelope(
    frame: dict[str, Any], tool_names: dict[str, str] | None = None
) -> list[tuple[str, Any]]:
    """Tool lifecycle out of SDK message envelopes.

    An assistant envelope's ``tool_use`` blocks start tools; a user envelope's
    ``tool_result`` blocks complete them. Streaming text itself already arrived
    via ``stream_event`` deltas, so text blocks are NOT re-emitted here (the
    renderer would double-print).

    ``tool_names`` carries tool_use_id → tool name across the two frames. A
    ``tool_result`` names only the id, but the renderer labels each row from
    ``name`` (falling back to the literal "tool") and matches rows by name +
    id — so completing without it produced the bare, contentless "Tool" rows.

    Field names follow what the renderer actually reads (``toolResult`` /
    ``toolArgs`` in lib/chat-messages.ts): ``result`` (not "output"),
    ``error`` (not "is_error"), plus ``inline_diff``/``summary``/``duration_s``
    when the agent supplies its richer display envelope.
    """
    kind = frame.get("type")
    message = frame.get("message") or {}
    content = message.get("content")
    events: list[tuple[str, Any]] = []

    if kind == "assistant":
        for block in _iter_blocks(content):
            if block.get("type") != "tool_use":
                continue
            tool_id = str(block.get("id") or "")
            name = str(block.get("name") or "")
            if tool_names is not None and tool_id:
                tool_names[tool_id] = name
            events.append(
                ("tool.start", {"tool_id": tool_id, "name": name,
                                "args": block.get("input") or {}}),
            )
        return events

    if kind != "user":
        return events

    # The agent's trimmed display envelope for the tool that just finished
    # (Edit/Write structuredPatch, Read image size, WebSearch counts …). It
    # rides the same frame as the tool_result block.
    display = frame.get("tool_use_result")
    for block in _iter_blocks(content):
        if block.get("type") != "tool_result":
            continue
        tool_id = str(block.get("tool_use_id") or "")
        payload: dict[str, Any] = {
            "tool_id": tool_id,
            "name": (tool_names or {}).pop(tool_id, "") if tool_names else "",
            "result": _tool_output_text(block.get("content")),
        }
        if block.get("is_error"):
            # The renderer flags a failed row from `error` being truthy; keep
            # the message so the row can show WHY it failed.
            payload["error"] = payload["result"] or "tool failed"
        if isinstance(display, dict):
            for key in ("inline_diff", "summary", "preview", "duration_s",
                        "structuredPatch", "filePath", "type", "originalSize",
                        "numLines", "totalLines"):
                if display.get(key) is not None:
                    payload.setdefault(key, display[key])
        events.append(("tool.complete", payload))
    return events


def translate_result(frame: dict[str, Any]) -> list[tuple[str, Any]]:
    is_error = bool(frame.get("is_error"))
    payload: dict[str, Any] = {
        "text": as_text(frame.get("result")),
        "status": "error" if is_error else "ok",
    }
    if is_error:
        error = as_text(frame.get("error")) or as_text(frame.get("result")) or "agent error"
        payload["error"] = error
        # The streamed text (if any) is real output worth keeping.
        payload["partial"] = bool(frame.get("result"))
    usage = usage_payload(frame.get("usage"))
    if usage:
        payload["usage"] = usage
    return [("message.complete", payload)]


def approval_request_payload(request: dict[str, Any]) -> dict[str, Any]:
    """``can_use_tool`` control_request → ``approval.request`` payload.

    The renderer shows ``command`` (monospace) when present and
    ``description`` otherwise; a Bash-like tool's command string is the most
    useful thing to surface.
    """
    tool_name = request.get("tool_name") or ""
    tool_input = request.get("input") or {}
    command = ""
    if isinstance(tool_input, dict):
        raw = tool_input.get("command")
        if isinstance(raw, str):
            command = raw
    description = request.get("session_label") or f"Use {tool_name}" if tool_name else "Tool approval"
    payload: dict[str, Any] = {
        "command": command,
        "description": description,
        "tool_name": tool_name,
        "input": tool_input,
    }
    warning = request.get("warning")
    if warning:
        payload["warning"] = warning
    suggestions = request.get("suggestions")
    if suggestions:
        payload["suggestions"] = suggestions
    return payload


def translate_frame(
    frame: dict[str, Any], tool_names: dict[str, str] | None = None
) -> list[tuple[str, Any]]:
    """Translate one agent frame into desktop gateway events.

    ``tool_names`` is the session's tool_use_id → name map, threaded so a
    ``tool_result`` can label its row (see :func:`translate_sdk_envelope`).

    Server-initiated ``control_request`` frames (permissions) are NOT handled
    here — they need connection state (pending map) and are handled by the
    session pump directly.
    """
    kind = frame.get("type")
    if kind == "stream_event":
        return translate_stream_event(frame)
    if kind in ("assistant", "user"):
        return translate_sdk_envelope(frame, tool_names)
    if kind == "result":
        return translate_result(frame)
    if kind == "text":
        # Final rendered text also arrives via `result`; interim standalone
        # text frames seal an interim bubble so the final complete doesn't
        # wipe streamed commentary.
        text = as_text(frame.get("text"))
        return [("message.interim", {"text": text})] if text else []
    return []


__all__ = [
    "approval_request_payload",
    "as_text",
    "translate_frame",
    "translate_result",
    "translate_sdk_envelope",
    "translate_stream_event",
    "usage_payload",
]
