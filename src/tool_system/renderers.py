"""Renderer/event types extracted from ``agent_loop.py`` (F.4 of the
ch05 consolidation, post-cutover).

These symbols are pure data + display helpers — no dependency on the
agent loop itself. They live here so the cutover paths (TUI bridge,
headless writer, REPL display, transcript widgets) can import them
without dragging in the legacy ``agent_loop`` module.

The neutral ``tool_system`` location (vs. ``tui/``) was deliberate:
importing from ``src.tui.tool_summary_renderers`` triggers
``src/tui/__init__.py``'s app loader, which created a circular import
via ``agent_bridge → agent_loop_compat → tui``. ``src.tool_system``
has no such cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolEvent:
    """Discrete event observed by the agent loop while executing tools.

    ``kind`` is one of ``tool_use`` (model emitted a tool_use block),
    ``tool_result`` (tool returned successfully), or ``tool_error`` /
    ``tool_result`` with ``is_error=True`` (tool failed). The bridge
    layer maps these to UI rendering or stream-json events.
    """
    kind: str
    tool_name: str
    tool_input: dict[str, Any] | None = None
    tool_output: Any | None = None
    tool_use_id: str | None = None
    is_error: bool = False
    error: str | None = None


@dataclass(frozen=True)
class AgentLoopResult:
    """Result shape returned by the headless/TUI agent loop.

    Preserved verbatim from the legacy ``agent_loop.run_agent_loop``
    return type so callers (CLI accounting, stream-json writer,
    AgentRunFinished UI event) keep working after the cutover to
    ``run_query_as_agent_loop``. The adapter re-wraps its own
    ``AgentLoopRunResult`` into this shape; callers see no diff.
    """
    response_text: str
    usage: dict[str, Any] | None = None  # {"input_tokens": int, "output_tokens": int}
    num_turns: int = 0


ToolEventHandler = Callable[[ToolEvent], None]
TextChunkHandler = Callable[[str], None]


def _safe_call_handler(handler: ToolEventHandler | None, event: ToolEvent) -> None:
    """Call ``handler(event)`` swallowing exceptions.

    The agent loop fires events at safe boundaries between tool calls;
    a buggy handler must not abort the loop. Errors are swallowed
    silently because the handler's contract is fire-and-forget UI
    rendering.
    """
    if handler is None:
        return
    try:
        handler(event)
    except Exception:
        return


def _emit_text_chunks(
    handler: TextChunkHandler | None,
    text: str,
    *,
    chunk_size: int = 12,
) -> None:
    """Emit ``text`` to ``handler`` in small chunks for visible streaming.

    Used by callers that want to simulate streaming UI on a
    non-streaming provider response. Real streaming providers fire
    chunks live via ``provider.chat_stream_response(on_text_chunk=...)``;
    this helper exists for the fallback path.
    """
    if handler is None or not text:
        return
    if chunk_size <= 0:
        chunk_size = len(text)
    for idx in range(0, len(text), chunk_size):
        try:
            handler(text[idx: idx + chunk_size])
        except Exception:
            return


def summarize_tool_use(name: str, tool_input: dict[str, Any]) -> str:
    """Single-line summary of a tool_use block for transcript display.

    Body verbatim from the legacy ``agent_loop.summarize_tool_use`` —
    each tool gets a specific shape (path + line range for Read,
    pattern + dir for Grep, ``@<subagent_type>`` + description for
    Agent, etc.). Unknown tools return empty string.
    """
    lowered = name.lower()
    if lowered == "bash":
        cmd = tool_input.get("command")
        if isinstance(cmd, str):
            s = cmd.strip().replace("\n", " ")
            return s if len(s) <= 80 else s[:77] + "..."
        return ""
    if lowered in {"read", "write", "edit"}:
        p = tool_input.get("file_path") or tool_input.get("filePath") or tool_input.get("path")
        if isinstance(p, str):
            extra = ""
            if lowered == "read":
                off = tool_input.get("offset")
                lim = tool_input.get("limit")
                if isinstance(off, int) or isinstance(lim, int):
                    start = off if isinstance(off, int) else 1
                    if isinstance(lim, int):
                        extra = f" · lines {start}-{start + lim - 1}"
            return f"{p}{extra}"
        return ""
    if lowered == "glob":
        pat = tool_input.get("pattern")
        base = tool_input.get("path")
        if isinstance(pat, str) and isinstance(base, str):
            return f"{pat} · {base}"
        if isinstance(pat, str):
            return pat
        return ""
    if lowered == "grep":
        pat = tool_input.get("pattern")
        base = tool_input.get("path")
        if isinstance(pat, str) and isinstance(base, str):
            return f"{pat} · {base}"
        if isinstance(pat, str):
            return pat
        return ""
    if lowered == "webfetch":
        url = tool_input.get("url")
        return url if isinstance(url, str) else ""
    if lowered == "websearch":
        q = tool_input.get("query")
        return q if isinstance(q, str) else ""
    if lowered == "toolsearch":
        q = tool_input.get("query")
        return q if isinstance(q, str) else ""
    if lowered == "askuserquestion":
        qs = tool_input.get("questions")
        if isinstance(qs, list):
            return f"{len(qs)} question(s)"
        return ""
    if lowered == "sendusermessage":
        status = tool_input.get("status")
        return status if isinstance(status, str) else ""
    if lowered in ("agent", "task"):
        # Surface ``@<subagent_type>`` + the user-supplied ``description``
        # so a wall of ``Agent(...)`` calls in a single turn reads as
        # discrete, scannable activity instead of identical placeholders.
        sub = tool_input.get("subagent_type")
        desc = tool_input.get("description")
        parts: list[str] = []
        if isinstance(sub, str) and sub.strip():
            parts.append(f"@{sub.strip()}")
        if isinstance(desc, str) and desc.strip():
            s = desc.strip().replace("\n", " ")
            parts.append(s if len(s) <= 60 else s[:57] + "...")
        return " · ".join(parts)
    return ""


def summarize_tool_result(name: str, output: Any) -> str:
    """Single-line summary of a tool result for transcript display.

    Mirror of ``summarize_tool_use`` for the result side: per-tool shape
    (file path + operation for Write, exit code for Bash, etc.) so the
    transcript shows outcome at a glance.
    """
    if not isinstance(output, dict):
        return str(output)
    if name.lower() == "write":
        path = output.get("filePath") or output.get("file_path")
        op = output.get("type")
        return f"{name} · {path} · {op}"
    if name.lower() == "edit":
        path = output.get("filePath") or output.get("file_path")
        replace_all = output.get("replaceAll")
        return f"{name} · {path} · replaceAll={replace_all}"
    if name.lower() == "read":
        if isinstance(output, str):
            if "unchanged" in output.lower():
                return f"{name} · unchanged"
            return f"{name}"
        if output.get("type") == "text" and isinstance(output.get("file"), dict):
            f = output["file"]
            path = f.get("filePath")
            num = f.get("numLines")
            total = f.get("totalLines")
            start = f.get("startLine")
            return f"{name} · {path} · lines={start}-{(start or 1) + (num or 0) - 1}/{total}"
        if output.get("type") == "file_unchanged" and isinstance(output.get("file"), dict):
            return f"{name} · {output['file'].get('filePath')} · unchanged"
        if output.get("type") in {"image", "pdf", "notebook"} and isinstance(output.get("file"), dict):
            return f"{name} · {output['file'].get('filePath')} · {output.get('type')}"
        return f"{name}"
    if name.lower() == "glob":
        n = output.get("numFiles")
        return f"{name} · matches={n}"
    if name.lower() == "grep":
        n = output.get("numFiles")
        mode = output.get("mode")
        return f"{name} · mode={mode} · files={n}"
    if name.lower() == "bash":
        code = output.get("exit_code")
        return f"{name} · exit={code}"
    if name.lower() == "webfetch":
        url = output.get("url")
        ct = output.get("content_type")
        return f"{name} · {url} · {ct}"
    if name.lower() == "websearch":
        q = output.get("query")
        results = output.get("results")
        n = len(results) if isinstance(results, list) else None
        return f"{name} · \"{q}\" · results={n}"
    if name.lower() == "config":
        op = output.get("operation")
        setting = output.get("setting")
        return f"{name} · {op} · {setting}"
    if name.lower() == "taskstop":
        tid = output.get("task_id")
        stopped = output.get("stopped")
        return f"{name} · {tid} · stopped={stopped}"
    if name.lower() == "sendusermessage":
        n = 0
        atts = output.get("attachments")
        if isinstance(atts, list):
            n = len(atts)
        return f"{name} · attachments={n}"
    # default: truncate dict keys for brevity
    keys = ", ".join(list(output.keys())[:3])
    return f"{name} · {keys}"


__all__ = [
    "AgentLoopResult",
    "TextChunkHandler",
    "ToolEvent",
    "ToolEventHandler",
    "_emit_text_chunks",
    "_safe_call_handler",
    "summarize_tool_result",
    "summarize_tool_use",
]
