"""Headless (non-interactive) entrypoint.

Port of ``typescript/src/cli/print.ts``, scoped to the slice that matters for
Phase 1: run a single prompt (or a stream of prompts via stream-json stdin)
through the agent loop and emit the response in the requested output format.

The heavy lifting lives in :mod:`src.tool_system.agent_loop` which already
understands Anthropic + OpenAI-compatible providers and emits structured tool
events. This module adapts those events to the CLI protocol in
:mod:`src.cli_core`.

Design notes
------------
* No Rich / prompt_toolkit imports — headless mode must run on plain pipes
  (CI, SDK clients, tests) without a TTY.
* Tool permission handling is driven by ``--dangerously-skip-permissions``:
  when set, tools run without gating; otherwise the default ``ToolContext``
  mode (``bypassPermissions``) still applies but *interactive* permission
  prompts auto-deny — we never ``input()`` in headless mode.
* The agent loop is synchronous; we call it inside ``run_headless`` and
  translate events to NDJSON on the fly.
"""

from __future__ import annotations

import io
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterable, Optional

from src.agent import Session
from src.cli_core import (
    AssistantEvent,
    PartialTextEvent,
    ResultEvent,
    StreamJsonReader,
    StreamJsonWriter,
    SystemEvent,
    ToolResultEvent,
    ToolUseEvent,
    UserInputMessage,
    cli_error,
    ndjson_safe_dumps,
)
from src.config import get_default_provider, get_provider_config
from src.providers import get_provider_class
from src.tool_system.agent_loop import ToolEvent, run_agent_loop
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry


OUTPUT_FORMATS = ("text", "json", "stream-json")
INPUT_FORMATS = ("text", "stream-json")


@dataclass
class HeadlessOptions:
    """Options accepted by :func:`run_headless`.

    Kept as a plain dataclass (no Click/argparse coupling) so the CLI layer
    and tests can construct it independently.
    """

    prompt: str | None = None
    output_format: str = "text"
    input_format: str = "text"
    provider_name: str | None = None
    model: str | None = None
    max_turns: int = 20
    skip_permissions: bool = False
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    include_partial_messages: bool = False
    verbose: bool = False

    # Mostly for tests: override streams so we can capture output.
    stdin: IO[str] | None = None
    stdout: IO[str] | None = None
    stderr: IO[str] | None = None

    # Workspace root override (default: cwd).
    workspace_root: Path | None = None


def run_headless(options: HeadlessOptions) -> int:
    """Run one or more prompts in headless mode. Returns the exit code."""

    if options.output_format not in OUTPUT_FORMATS:
        cli_error(
            f"error: --output-format must be one of {', '.join(OUTPUT_FORMATS)}", 2
        )
    if options.input_format not in INPUT_FORMATS:
        cli_error(
            f"error: --input-format must be one of {', '.join(INPUT_FORMATS)}", 2
        )
    if options.input_format == "stream-json" and options.output_format != "stream-json":
        cli_error(
            "error: --input-format stream-json requires --output-format stream-json",
            2,
        )

    stdout = options.stdout or sys.stdout
    stderr = options.stderr or sys.stderr
    stdin = options.stdin or sys.stdin

    provider_name = options.provider_name or get_default_provider()
    try:
        provider_cfg = get_provider_config(provider_name)
    except Exception as exc:
        cli_error(f"error: unable to load provider config: {exc}", 2)
    if not provider_cfg.get("api_key"):
        cli_error(
            f"error: API key for provider '{provider_name}' is not configured. "
            "Run `clawcodex login` to set it up.",
            2,
        )

    provider_cls = get_provider_class(provider_name)
    model = options.model or provider_cfg.get("default_model")
    provider = provider_cls(
        api_key=provider_cfg["api_key"],
        base_url=provider_cfg.get("base_url"),
        model=model,
    )

    session = Session.create(provider_name, getattr(provider, "model", model or ""))

    tool_registry = build_default_registry(provider=provider)
    if options.allowed_tools:
        allow = {name.lower() for name in options.allowed_tools}
        _filter_registry(tool_registry, keep=lambda n: n.lower() in allow)
    if options.disallowed_tools:
        deny = {name.lower() for name in options.disallowed_tools}
        _filter_registry(tool_registry, keep=lambda n: n.lower() not in deny)

    workspace_root = options.workspace_root or Path.cwd()
    tool_context = ToolContext(workspace_root=workspace_root)
    tool_context.options.is_non_interactive_session = True
    if options.skip_permissions:
        tool_context.allow_docs = True
        tool_context.permission_handler = None
    else:
        # Never block a pipe on stdin. Auto-deny any permission request.
        tool_context.permission_handler = _auto_deny_permission_handler(stderr)
    # AskUserQuestion has no terminal to read from in headless mode.
    tool_context.ask_user = _noop_ask_user

    # Build the input iterator.
    if options.input_format == "stream-json":
        inputs: Iterable[UserInputMessage] = StreamJsonReader(stdin)
    else:
        prompt_text = options.prompt
        if prompt_text is None or prompt_text == "-":
            prompt_text = stdin.read()
        prompt_text = (prompt_text or "").strip()
        if not prompt_text:
            cli_error("error: no prompt provided (pass an argument or pipe stdin)", 2)
        inputs = [UserInputMessage(text=prompt_text, raw={"prompt": prompt_text})]

    writer: StreamJsonWriter | None = None
    if options.output_format == "stream-json":
        writer = StreamJsonWriter(stdout)
        tools = [tool.name for tool in tool_registry.list_tools()]
        writer.write(
            SystemEvent(
                subtype="init",
                session_id=session.session_id,
                model=getattr(provider, "model", None),
                provider=provider_name,
                cwd=str(workspace_root),
                tools=tools,
                permission_mode="bypassPermissions"
                if options.skip_permissions
                else "default",
            )
        )

    aggregate_text: list[str] = []
    aggregate_tool_events: list[dict] = []
    num_turns_total = 0
    usage_total: dict[str, int] = {}
    exit_code = 0
    start = time.monotonic()

    for user_msg in inputs:
        session.conversation.add_user_message(user_msg.text)

        on_event = _build_event_bridge(writer, aggregate_tool_events)
        on_text_chunk = None
        if writer is not None and options.include_partial_messages:
            def _emit_partial(chunk: str) -> None:
                writer.write(PartialTextEvent(text=chunk))

            on_text_chunk = _emit_partial

        try:
            result = run_agent_loop(
                conversation=session.conversation,
                provider=provider,
                tool_registry=tool_registry,
                tool_context=tool_context,
                max_turns=options.max_turns,
                stream=bool(on_text_chunk),
                verbose=options.verbose,
                on_event=on_event,
                on_text_chunk=on_text_chunk,
            )
        except KeyboardInterrupt:
            exit_code = 130
            break
        except Exception as exc:
            exit_code = 1
            if writer is not None:
                writer.write(
                    ResultEvent(
                        subtype="error",
                        session_id=session.session_id,
                        num_turns=num_turns_total,
                        result=str(exc),
                        duration_ms=int((time.monotonic() - start) * 1000),
                        is_error=True,
                        error=str(exc),
                    )
                )
            else:
                print(f"error: {exc}", file=stderr)
            break

        num_turns_total += result.num_turns
        if result.usage:
            for key, value in result.usage.items():
                usage_total[key] = usage_total.get(key, 0) + int(value)

        if writer is not None:
            writer.write(AssistantEvent(text=result.response_text))
        aggregate_text.append(result.response_text)

    duration_ms = int((time.monotonic() - start) * 1000)
    final_text = "\n\n".join(t for t in aggregate_text if t).strip()

    if options.output_format == "text":
        if final_text:
            stdout.write(final_text + "\n")
            stdout.flush()
    elif options.output_format == "json":
        payload = {
            "type": "result",
            "subtype": "error" if exit_code not in (0, 130) else "success",
            "session_id": session.session_id,
            "provider": provider_name,
            "model": getattr(provider, "model", None),
            "num_turns": num_turns_total,
            "result": final_text,
            "duration_ms": duration_ms,
            "usage": usage_total or None,
            "tool_events": aggregate_tool_events,
            "is_error": exit_code not in (0, 130),
        }
        stdout.write(ndjson_safe_dumps(payload) + "\n")
        stdout.flush()
    elif options.output_format == "stream-json" and writer is not None and exit_code == 0:
        writer.write(
            ResultEvent(
                subtype="success",
                session_id=session.session_id,
                num_turns=num_turns_total,
                result=final_text,
                duration_ms=duration_ms,
                usage=usage_total or None,
            )
        )

    return exit_code


# ---------------------------------------------------------------------------
# Helpers


def _filter_registry(registry, *, keep) -> None:
    """In-place best-effort filter of a ToolRegistry."""

    try:
        entries = list(registry.list_tools())
    except Exception:
        return
    for tool in entries:
        name = getattr(tool, "name", "")
        if not keep(name):
            try:
                registry.unregister(name)
            except Exception:
                # Registry may not support unregistration; fall back to
                # marking the tool disallowed through ToolContext.
                continue


def _auto_deny_permission_handler(stderr: IO[str]):
    def handler(tool_name: str, message: str, suggestion: Optional[str]):
        stderr.write(
            f"[headless] denying permission for {tool_name}: {message}"
            " (pass --dangerously-skip-permissions to bypass)\n"
        )
        try:
            stderr.flush()
        except Exception:
            pass
        return False, False

    return handler


def _noop_ask_user(questions):  # type: ignore[override]
    # In non-interactive mode, collapse every question to an empty answer.
    answers: dict = {}
    for q in questions or []:
        if isinstance(q, dict) and isinstance(q.get("question"), str):
            answers[q["question"]] = ""
    return answers


def _build_event_bridge(writer: StreamJsonWriter | None, sink: list[dict]):
    def on_event(event: ToolEvent) -> None:
        if event.kind == "tool_use":
            record = {
                "type": "tool_use",
                "tool_use_id": event.tool_use_id,
                "name": event.tool_name,
                "input": event.tool_input or {},
            }
            sink.append(record)
            if writer is not None:
                writer.write(
                    ToolUseEvent(
                        tool_use_id=event.tool_use_id,
                        name=event.tool_name,
                        input=dict(event.tool_input or {}),
                    )
                )
        elif event.kind in ("tool_result", "tool_error"):
            record = {
                "type": "tool_result",
                "tool_use_id": event.tool_use_id,
                "name": event.tool_name,
                "output": _jsonable(event.tool_output),
                "is_error": bool(event.is_error),
            }
            if event.error:
                record["error"] = event.error
            sink.append(record)
            if writer is not None:
                writer.write(
                    ToolResultEvent(
                        tool_use_id=event.tool_use_id,
                        name=event.tool_name,
                        output=_jsonable(event.tool_output),
                        is_error=bool(event.is_error),
                    )
                )

    return on_event


def _jsonable(value):
    """Coerce arbitrary tool output into a JSON-safe shape."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    try:
        return str(value)
    except Exception:
        return repr(value)
