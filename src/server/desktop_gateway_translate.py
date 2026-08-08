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
        text = delta.get("text")
        return [("message.delta", {"text": text})] if text else []
    if kind == "thinking_delta":
        thinking = delta.get("thinking")
        return [("reasoning.delta", {"text": thinking})] if thinking else []
    return []


def translate_sdk_envelope(frame: dict[str, Any]) -> list[tuple[str, Any]]:
    """Tool lifecycle out of SDK message envelopes.

    An assistant envelope's ``tool_use`` blocks start tools; a user envelope's
    ``tool_result`` blocks complete them. Streaming text itself already arrived
    via ``stream_event`` deltas, so text blocks are NOT re-emitted here (the
    renderer would double-print).
    """
    message = frame.get("message") or {}
    content = message.get("content")
    events: list[tuple[str, Any]] = []
    if frame.get("type") == "assistant":
        for block in _iter_blocks(content):
            if block.get("type") == "tool_use":
                events.append(
                    (
                        "tool.start",
                        {
                            "tool_id": block.get("id") or "",
                            "name": block.get("name") or "",
                            "args": block.get("input") or {},
                        },
                    )
                )
    elif frame.get("type") == "user":
        for block in _iter_blocks(content):
            if block.get("type") == "tool_result":
                events.append(
                    (
                        "tool.complete",
                        {
                            "tool_id": block.get("tool_use_id") or "",
                            # The renderer keys rows by tool_id and falls back
                            # to name; the id is authoritative here.
                            "name": "",
                            "output": _tool_output_text(block.get("content")),
                            "is_error": bool(block.get("is_error")),
                        },
                    )
                )
    return events


def translate_result(frame: dict[str, Any]) -> list[tuple[str, Any]]:
    is_error = bool(frame.get("is_error"))
    payload: dict[str, Any] = {
        "text": frame.get("result") or "",
        "status": "error" if is_error else "ok",
    }
    if is_error:
        error = frame.get("error") or frame.get("result") or "agent error"
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


def translate_frame(frame: dict[str, Any]) -> list[tuple[str, Any]]:
    """Translate one agent frame into desktop gateway events.

    Server-initiated ``control_request`` frames (permissions) are NOT handled
    here — they need connection state (pending map) and are handled by the
    session pump directly.
    """
    kind = frame.get("type")
    if kind == "stream_event":
        return translate_stream_event(frame)
    if kind in ("assistant", "user"):
        return translate_sdk_envelope(frame)
    if kind == "result":
        return translate_result(frame)
    if kind == "text":
        # Final rendered text also arrives via `result`; interim standalone
        # text frames seal an interim bubble so the final complete doesn't
        # wipe streamed commentary.
        text = frame.get("text") or ""
        return [("message.interim", {"text": text})] if text else []
    return []


__all__ = [
    "approval_request_payload",
    "translate_frame",
    "translate_result",
    "translate_sdk_envelope",
    "translate_stream_event",
    "usage_payload",
]
