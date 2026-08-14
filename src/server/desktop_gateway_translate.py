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

import re
from typing import Any

# ── usage mapping ────────────────────────────────────────────────────────────
# Backend usage counters are token-based ({input_tokens, output_tokens, …});
# the renderer merges {calls, input, output, total} into its session stats.


def usage_payload(usage: dict[str, Any] | None) -> dict[str, int] | None:
    """Backend token counters → the renderer's usage shape.

    The first four keys are the contract the desktop renderer merges into its
    session stats; the rest are additive and carry what that shape flattens
    away — the reasoning/content split and the cache counters, which are the
    difference between "20k input tokens" and "20k input tokens, 87% served
    from cache". Readers that only know the original four are unaffected.
    """
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    payload = {
        "calls": 1,
        "input": input_tokens,
        "output": output_tokens,
        "total": input_tokens + output_tokens,
    }
    for source, key in (
        ("reasoning_tokens", "reasoning"),
        ("cache_read_input_tokens", "cache_read"),
        ("cache_creation_input_tokens", "cache_write"),
    ):
        value = usage.get(source)
        if value:
            payload[key] = int(value)
    return payload


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


# ── tool vocabulary adapter ──────────────────────────────────────────────────
# clawcodex speaks the Claude Code tool vocabulary (Read/Bash/Glob/…). The
# desktop's tool renderer keys every per-tool formatter — icon, title, diff
# view, split stdout/stderr, count chip — off its OWN fixed set of names
# (ui-desktop/.../tool/fallback-model/index.ts ``TOOL_META`` plus its
# ``toolName === …`` branches), and it reads a tool's output out of *named
# result fields* rather than raw text: a plain-string ``result`` is dropped
# outright by the renderer's ``parseMaybeJsonObject``. Unadapted, every row
# fell through to the generic path — no title, no icon, and no output at all.
#
# Summaries deliberately mirror the TUI's (ui-tui/src/gatewayClient.ts
# ``toolContext`` / ``formatToolResult``) so both surfaces describe the same
# tool run the same way.

_RENDER_TOOL_NAMES: dict[str, str] = {
    "read": "read_file",
    "write": "write_file",
    "edit": "edit_file",
    "multiedit": "edit_file",
    "notebookedit": "edit_file",
    "bash": "terminal",
    "bashoutput": "terminal",
    "killshell": "terminal",
    "killbash": "terminal",
    "glob": "list_files",
    "ls": "list_files",
    "grep": "search_files",
    "websearch": "web_search",
    "webfetch": "web_extract",
    "todowrite": "todo",
    "askuserquestion": "clarify",
}


def render_tool_name(name: str) -> str:
    """clawcodex tool name → the name the desktop renderer formats for.

    Unknown tools (Task, custom MCP tools, …) pass through unchanged and get
    the renderer's generic treatment, which is the right fallback.
    """
    return _RENDER_TOOL_NAMES.get(name.strip().lower(), name)


def tool_context(tool_input: Any) -> str:
    """The single argument worth showing beside the tool name.

    Priority order is the TUI's ``toolContext``, so a Bash row reads
    ``ls src/`` on both surfaces and a Grep row shows its pattern rather than
    its search path. The renderer picks this up as ``context`` — the key its
    generic title/preview path is built around.
    """
    if not isinstance(tool_input, dict):
        return ""
    pattern = tool_input.get("pattern")
    if pattern is not None:
        return str(pattern)
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if value is not None:
            return str(value)
    for key in ("command", "url", "query", "description", "prompt"):
        value = tool_input.get(key)
        if value is not None:
            return str(value)
    return ""


def render_tool_args(tool_input: Any) -> dict[str, Any]:
    """Tool input plus the aliases the renderer reads.

    It looks for ``path``/``file``/``filepath`` when resolving a file target;
    clawcodex spells it ``file_path``/``notebook_path``. Aliased rather than
    renamed so the raw arguments stay intact for the args view.
    """
    if not isinstance(tool_input, dict):
        return {}
    args = dict(tool_input)
    if not isinstance(args.get("path"), str):
        for key in ("file_path", "notebook_path"):
            if isinstance(args.get(key), str):
                args["path"] = args[key]
                break
    return args


_SANDBOX_BLOCK_RE = re.compile(r"<sandbox_violations>.*?</sandbox_violations>", re.DOTALL)
_ERROR_TAG_RE = re.compile(r"</?(?:tool_use_error|error)>")
_PREFIXED_ERROR_RE = re.compile(r"^(?:Error|Cancelled):\s")
_NUMBERED_LINE_RE = re.compile(r"^\s*\d+\t")


def clean_tool_error(text: str) -> str:
    """Tool failure text → the sentence a row should show (TUI parity).

    Strips the model-facing markup (``<tool_use_error>`` wrappers, sandbox
    violation blocks) that is bookkeeping for the agent, not for a reader.
    """
    cleaned = _ERROR_TAG_RE.sub("", _SANDBOX_BLOCK_RE.sub("", text)).strip()
    if not cleaned:
        return "Tool execution failed"
    if "InputValidationError: " in cleaned:
        return "Invalid tool parameters"
    if _PREFIXED_ERROR_RE.match(cleaned):
        return cleaned
    return f"Error: {cleaned}"


def _format_file_size(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{value:.1f}".removesuffix(".0") + unit
    return f"{size} bytes"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def summarize_tool_result(name: str, text: str, display: Any) -> str:
    """One-line description of what a tool produced, matching the TUI.

    Returned as the row's ``context``. The renderer prefers ``context`` over
    everything else on its generic detail path, so this is set ONLY where the
    summary is the whole story — never where it would hide real output the
    expanded row should show (a Grep's matches get a count chip instead).
    Empty means "the raw output speaks for itself".
    """
    if name == "Read":
        if isinstance(display, dict) and display.get("type") == "image":
            size = display.get("originalSize")
            if isinstance(size, int) and not isinstance(size, bool):
                return f"Read image ({_format_file_size(size)})"
            return "Read image"
        lines = [line for line in text.split("\n") if line]
        if lines and _NUMBERED_LINE_RE.match(lines[0]):
            return f"Read {_plural(len(lines), 'line')}"
        return ""
    if name == "WebSearch" and isinstance(display, dict):
        count = display.get("searchCount")
        if isinstance(count, int) and not isinstance(count, bool):
            summary = f"Did {count} search{'' if count == 1 else 'es'}"
            seconds = display.get("durationSeconds")
            if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
                summary += f" in {round(seconds)}s" if seconds >= 1 else f" in {round(seconds * 1000)}ms"
            return summary
    return ""


def inline_diff_from_display(display: Any) -> str:
    """``structuredPatch`` display envelope → a unified diff string.

    The renderer paints ``inline_diff`` as a real diff and counts its +/-
    lines for the "Added N, removed M" summary, mirroring how the TUI treats
    an edit's diff AS its result rather than printing result text.
    """
    if not isinstance(display, dict):
        return ""
    path = display.get("filePath")
    path_label = path if isinstance(path, str) else "file"
    patch = display.get("structuredPatch")
    if not isinstance(patch, list) or not patch:
        content = display.get("content")
        if display.get("type") == "create" and isinstance(content, str) and content:
            body = "\n".join(f"+{line}" for line in content.split("\n"))
            return f"--- /dev/null\n+++ {path_label}\n{body}"
        return ""
    lines = [f"--- {path_label}", f"+++ {path_label}"]
    for hunk in patch:
        if not isinstance(hunk, dict):
            continue
        body = hunk.get("lines")
        if not isinstance(body, list):
            continue
        lines.append(
            "@@ -{},{} +{},{} @@".format(
                hunk.get("oldStart", 0), hunk.get("oldLines", 0),
                hunk.get("newStart", 0), hunk.get("newLines", 0),
            )
        )
        lines.extend(str(line) for line in body)
    return "\n".join(lines) if len(lines) > 2 else ""


def render_tool_result(name: str, text: str, display: Any) -> dict[str, Any]:
    """Tool output → the result object the renderer knows how to read.

    Each renderer tool family reads its output from a different field
    (``content`` for a file read, ``output`` for a shell run, ``inline_diff``
    for an edit); a bare string reaches none of them.
    """
    render = render_tool_name(name)
    result: dict[str, Any] = {}

    if render in ("read_file", "web_extract"):
        if text:
            result["content"] = text
    elif render == "terminal":
        # The renderer paints this as an ANSI terminal body.
        result["output"] = text
    elif render in ("edit_file", "write_file"):
        diff = inline_diff_from_display(display)
        if diff:
            result["inline_diff"] = diff
        elif text:
            result["message"] = text
        if isinstance(display, dict) and isinstance(display.get("filePath"), str):
            result["path"] = display["filePath"]
    elif text:
        # No dedicated renderer branch, so the body comes from the generic
        # path — which prefers the *argument* context ("*.py") over anything
        # in the result, and would print the glob back at the user instead of
        # the files it matched. Repeating the output under `context` is what
        # outranks it; `output` stays for the copy and subtitle paths, which
        # read that key instead. A real summary below replaces the `context`
        # copy where one exists.
        result["output"] = text
        result["context"] = text

    if render == "list_files" and text:
        result["file_count"] = len([line for line in text.split("\n") if line.strip()])
    elif render == "search_files" and text:
        result["match_count"] = len([line for line in text.split("\n") if line.strip()])
    elif render == "web_search" and isinstance(display, dict):
        count = display.get("searchCount")
        if isinstance(count, int) and not isinstance(count, bool):
            result["result_count"] = count
        seconds = display.get("durationSeconds")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
            result["duration_s"] = seconds

    summary = summarize_tool_result(name, text, display)
    if summary:
        result["context"] = summary
    return result


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
    ``name`` (falling back to the literal "tool") and matches a completion to
    its running row by name + id — so completing without it produced the
    bare, contentless "Tool" rows.

    Names, arguments and results all go through the vocabulary adapter above,
    which is what engages the renderer's per-tool formatting instead of its
    unlabelled generic fallback.
    """
    kind = frame.get("type")
    message = frame.get("message") or {}
    content = message.get("content")
    events: list[tuple[str, Any]] = []

    if kind == "assistant":
        # One assistant envelope is one completed model request — a STEP. It
        # is emitted before the tool rows it opens, so a reader can close the
        # step's books (tokens, model, why it stopped) and then watch the
        # tools it asked for. ``result`` only ever reports the whole turn.
        step = step_complete_payload(frame)
        if step is not None:
            events.append(("step.complete", step))

        for block in _iter_blocks(content):
            if block.get("type") != "tool_use":
                continue
            tool_id = str(block.get("id") or "")
            name = str(block.get("name") or "")
            if tool_names is not None and tool_id:
                tool_names[tool_id] = name
            tool_input = block.get("input") or {}
            payload: dict[str, Any] = {
                "tool_id": tool_id,
                "name": render_tool_name(name),
                "args": render_tool_args(tool_input),
            }
            context = tool_context(tool_input)
            if context:
                payload["context"] = context
            events.append(("tool.start", payload))
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
        name = (tool_names or {}).pop(tool_id, "") if tool_names else ""
        text = _tool_output_text(block.get("content"))
        payload = {"tool_id": tool_id, "name": render_tool_name(name)}
        if block.get("is_error"):
            # A failed row shows the reason, not the output — and no diff:
            # nothing was written. The renderer flags failure off `error`.
            payload["error"] = clean_tool_error(text)
            payload["result"] = {}
        else:
            payload["result"] = render_tool_result(name, text, display)
        events.append(("tool.complete", payload))
    return events


def step_complete_payload(frame: dict[str, Any]) -> dict[str, Any] | None:
    """Assistant envelope → the per-step facts, or None when it carries none.

    ``usage`` is the only field worth an event on its own; ``model`` and
    ``stop_reason`` ride along because a step that switched models (a fallback)
    or stopped early (``max_tokens``) is exactly the step a reader is looking
    for, and neither is recoverable from the turn's totals.
    """
    usage = usage_payload(frame.get("usage"))
    model = frame.get("model")
    stop_reason = frame.get("stop_reason")
    if usage is None and not model and not stop_reason:
        return None
    payload: dict[str, Any] = {}
    if usage is not None:
        payload["usage"] = usage
    if model:
        payload["model"] = str(model)
    if stop_reason:
        payload["stop_reason"] = str(stop_reason)
    return payload


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
    "clean_tool_error",
    "inline_diff_from_display",
    "render_tool_args",
    "render_tool_name",
    "render_tool_result",
    "step_complete_payload",
    "summarize_tool_result",
    "tool_context",
    "translate_frame",
    "translate_result",
    "translate_sdk_envelope",
    "translate_stream_event",
    "usage_payload",
]
