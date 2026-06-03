"""Headless (non-interactive) entrypoint.

Port of ``typescript/src/cli/print.ts``, scoped to the slice that matters for
Phase 1: run a single prompt (or a stream of prompts via stream-json stdin)
through the agent loop and emit the response in the requested output format.

The heavy lifting lives in :mod:`src.query.query` (the canonical agent
loop), driven via the sync wrapper
:func:`src.query.agent_loop_compat.run_query_as_agent_loop`. That loop
already understands Anthropic + OpenAI-compatible providers and emits
structured tool events; this module adapts those events to the CLI
protocol in :mod:`src.cli_core`.

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
import signal as _signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, Iterable, Optional

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
from src.tool_system.renderers import AgentLoopResult, ToolEvent
from src.query.agent_loop_compat import (
    build_effective_system_prompt,
    run_query_as_agent_loop,
)
from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.utils.abort_controller import AbortController, AbortError


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
    # ``skip_permissions`` is a backward-compat alias for the boolean form
    # of ``--dangerously-skip-permissions``. ``permission_mode`` and
    # ``is_bypass_permissions_mode_available`` were added in round 5 to
    # mirror the TS reference's resolved state. When ``skip_permissions``
    # is True we treat it as ``permission_mode='bypassPermissions'`` and
    # ``is_bypass_permissions_mode_available=True``.
    skip_permissions: bool = False
    permission_mode: str = "default"
    is_bypass_permissions_mode_available: bool = False
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

    # Optional system prompt body to append (from resolved default agent).
    append_system_prompt: str = ""


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

    # Compute the effective permission context. ``skip_permissions=True`` is
    # the legacy alias and means "user passed --dangerously-skip-permissions";
    # ``permission_mode`` / ``is_bypass_permissions_mode_available`` are the
    # round-5 fields. When skip_permissions wins, force bypass mode + bypass
    # availability so the registry's ``has_permissions_to_use_tool`` check
    # short-circuits to ``allow``.
    from src.permissions.types import ToolPermissionContext

    if options.skip_permissions:
        effective_mode: str = "bypassPermissions"
        bypass_available = True
    else:
        effective_mode = options.permission_mode or "default"
        bypass_available = bool(options.is_bypass_permissions_mode_available)

    # Per-session abort controller. SIGINT trips this so the running
    # tool (Bash supervisor, Agent subagent) unwinds immediately rather
    # than waiting for the next safe interpreter bytecode boundary.
    # Without this wiring, Ctrl-C only fires ``KeyboardInterrupt`` at
    # the next safe boundary — which can be several minutes for a
    # subprocess.wait() or an in-flight subagent.
    abort_controller = AbortController()
    tool_context = ToolContext(
        workspace_root=workspace_root,
        permission_context=ToolPermissionContext(
            mode=effective_mode,  # type: ignore[arg-type]
            is_bypass_permissions_mode_available=bypass_available,
        ),
        abort_controller=abort_controller,
    )
    tool_context.options.is_non_interactive_session = True
    if options.skip_permissions or effective_mode == "bypassPermissions":
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
                permission_mode=effective_mode,
            )
        )

    aggregate_text: list[str] = []
    aggregate_tool_events: list[dict] = []
    num_turns_total = 0
    usage_total: dict[str, int] = {}
    exit_code = 0
    start = time.monotonic()

    # Two-mode SIGINT handler:
    # * Idle (waiting on stdin for the next stream-json input) → raise
    #   ``KeyboardInterrupt`` immediately so the blocking read returns.
    # * In-flight ``run_agent_loop`` → first strike trips the controller
    #   (cooperative unwind), second strike force-quits via
    #   ``KeyboardInterrupt``. Both map to exit 130.
    # See ``_install_sigint_handler`` for the full handler logic; the
    # for-loop's outer ``except (AbortError, KeyboardInterrupt)`` is the
    # single chokepoint that catches whatever the handler raises.
    # ``restore_sigint`` runs in the ``finally`` so we don't leak global
    # signal state to embedders.
    in_agent_loop = _InAgentLoopFlag()
    restore_sigint = _install_sigint_handler(
        abort_controller, in_agent_loop, stderr
    )
    try:
        # Cancellation is caught at the for-loop level (not per-iteration)
        # so that a SIGINT landing on ANY cancellation point unwinds to one
        # place that emits the cancelled ResultEvent: the iterator step
        # (``StreamJsonReader``'s blocking stdin read in idle mode), the
        # agent loop itself, or the post-success accounting between them.
        # The inner per-iteration ``except Exception`` keeps per-turn
        # tool/provider error handling local — it must NOT catch
        # ``AbortError``/``KeyboardInterrupt`` (Python catches them via
        # ``Exception`` only when they inherit from it; ``AbortError`` does
        # but ``KeyboardInterrupt`` does not, so we exclude AbortError
        # explicitly).
        try:
            for user_msg in inputs:
                session.conversation.add_user_message(user_msg.text)

                on_event = _build_event_bridge(writer, aggregate_tool_events)
                on_text_chunk = None
                if writer is not None and options.include_partial_messages:
                    def _emit_partial(chunk: str) -> None:
                        writer.write(PartialTextEvent(text=chunk))

                    on_text_chunk = _emit_partial

                try:
                    in_agent_loop.value = True
                    try:
                        # Ch5/F.2 cutover: route headless through the
                        # canonical query() loop via the F.1 adapter.
                        # Headless is single-shot per prompt and starts
                        # its own event loop, so ``asyncio.run`` is the
                        # right pattern. Pre-build the effective system
                        # prompt (CLAUDE.md + git status + style) so the
                        # cold-start context reaches query() unchanged
                        # — the legacy run_agent_loop did this inside
                        # the loop; the adapter doesn't.
                        import asyncio as _asyncio
                        from src.outputStyles import resolve_output_style
                        _style_prompt = resolve_output_style(
                            getattr(tool_context, "output_style_name", None),
                            getattr(tool_context, "output_style_dir", None),
                        ).prompt
                        effective_system_prompt = (
                            build_effective_system_prompt(_style_prompt, tool_context)
                        )
                        if options.append_system_prompt:
                            effective_system_prompt = (
                                f"{effective_system_prompt}\n\n{options.append_system_prompt}"
                            )

                        def _persist(msg: Any) -> None:
                            # BLOCKING #2 fix: persist FULL message
                            # (including tool_use/tool_result blocks)
                            # so the next turn can pair tool_use IDs to
                            # results. Plain add_assistant_message
                            # loses the structure.
                            # Critic S3: log + re-raise on failure
                            # rather than swallow; a persist error
                            # means the conversation is corrupted and
                            # the next API call will reject it. Better
                            # to surface now than to debug a 400 later.
                            try:
                                session.conversation.add_message(msg.role, msg.content)
                            except Exception:
                                import logging
                                logging.getLogger(__name__).exception(
                                    "Failed to persist message into "
                                    "conversation: role=%s",
                                    getattr(msg, "role", "?"),
                                )
                                raise

                        compat_result = _asyncio.run(run_query_as_agent_loop(
                            initial_messages=list(session.conversation.messages),
                            provider=provider,
                            tool_registry=tool_registry,
                            tool_context=tool_context,
                            system_prompt=effective_system_prompt,
                            max_turns=options.max_turns,
                            on_event=on_event,
                            on_text_chunk=on_text_chunk,
                            on_message=_persist,
                            # Critic C2: pass the OWNING controller so
                            # the provider's chat_stream_response listens
                            # on the same signal the SIGINT handler trips.
                            # Passing only ``cancel_signal=signal`` would
                            # force the adapter to mint a fresh controller
                            # and break the mid-stream tear-down path.
                            abort_controller=abort_controller,
                        ))
                        # Re-wrap into legacy AgentLoopResult shape so
                        # downstream usage/num_turns/response_text code
                        # stays untouched. ``usage if num_turns > 0
                        # else None`` preserves the dict|None contract.
                        result = AgentLoopResult(
                            response_text=compat_result.response_text,
                            usage=(
                                compat_result.usage
                                if compat_result.num_turns > 0
                                else None
                            ),
                            num_turns=compat_result.num_turns,
                        )
                    finally:
                        # Flip BEFORE the outer except block can run so a
                        # SIGINT landing between ``run_agent_loop`` returning
                        # and the next iterator step is correctly classified
                        # as idle. (``AbortError`` is a subclass of
                        # ``Exception`` and would otherwise be re-raised
                        # through this finally too — so we set the flag
                        # back to False regardless of how we leave.)
                        in_agent_loop.value = False
                except AbortError:
                    # Re-raise to the outer ``except`` so the cancelled
                    # ResultEvent is emitted in exactly one place.
                    raise
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
        except (AbortError, KeyboardInterrupt):
            # Cancellation from ANY point in the loop body lands here:
            # * ``AbortError`` from a cooperative unwind inside
            #   ``run_agent_loop`` (first SIGINT, in-flight mode).
            # * ``KeyboardInterrupt`` from the SIGINT handler's idle
            #   branch (raised mid-``inputs.__iter__()`` while blocked on
            #   stdin), or from the in-flight second-strike force-quit.
            # All map to exit 130 for shell parity. ``error`` is left
            # unset — ``subtype: "cancelled"`` already carries the
            # signal, and pairing ``is_error=False`` with a populated
            # ``error`` field would confuse consumers.
            exit_code = 130
            if writer is not None:
                writer.write(
                    ResultEvent(
                        subtype="cancelled",
                        session_id=session.session_id,
                        num_turns=num_turns_total,
                        result="",
                        duration_ms=int((time.monotonic() - start) * 1000),
                        is_error=False,
                    )
                )
    finally:
        restore_sigint()

    duration_ms = int((time.monotonic() - start) * 1000)
    final_text = "\n\n".join(t for t in aggregate_text if t).strip()

    if options.output_format == "text":
        if final_text:
            stdout.write(final_text + "\n")
            stdout.flush()
    elif options.output_format == "json":
        if exit_code == 0:
            json_subtype = "success"
        elif exit_code == 130:
            json_subtype = "cancelled"
        else:
            json_subtype = "error"
        payload = {
            "type": "result",
            "subtype": json_subtype,
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


class _InAgentLoopFlag:
    """Mutable shared flag indicating whether ``run_agent_loop`` is in flight.

    Read by the SIGINT handler to decide between cooperative abort
    (in-flight: trip the controller, let the loop unwind at the next
    safe boundary) and immediate raise (idle, e.g. blocked on
    ``StreamJsonReader``'s stdin read: the only way to make the read
    return is to actually raise ``KeyboardInterrupt`` on the same
    thread — Python 3 auto-retries EINTR'd reads when the handler
    didn't raise, per PEP 475).
    """

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = False


def _install_sigint_handler(
    controller: AbortController,
    in_agent_loop: _InAgentLoopFlag,
    stderr: IO[str],
) -> Callable[[], None]:
    """Install a context-aware SIGINT handler.

    - **Idle** (``in_agent_loop.value`` is False, e.g. blocked on
      stdin reading the next stream-json input): raise
      ``KeyboardInterrupt`` immediately. Python 3 PEP 475 retries
      EINTR'd ``read()`` calls when the signal handler did NOT raise,
      so a cooperative abort here would *hang the stdin read* until
      the user hit Ctrl-C a second time — a UX regression vs. the
      pre-fix behaviour where the first Ctrl-C raised at the next
      bytecode boundary and exited the program. Raising on the first
      strike restores parity with that pre-fix path.

    - **Cooperative** (in-flight ``run_agent_loop``, first strike):
      trip ``controller``. Every abort-aware site — the agent loop's
      ``_check_cancel`` boundaries, the Bash supervisor's poll loop,
      the subagent query loop, the streaming executor's per-tool
      controller, hook gates — sees the signal and unwinds gracefully
      with a partial result that's appended to the conversation. A
      message is printed to stderr so the user knows the request was
      received but unwind may take a moment.

    - **Cooperative** (in-flight, second strike): re-install the
      platform default handler (defense-in-depth against a possible
      third strike landing during unwind) and raise
      ``KeyboardInterrupt`` directly. This is the force-quit escape
      hatch for the rare case where a tool doesn't honour the abort.

    Returns a callable that restores whatever handler was installed
    before us, so embedders that drive ``run_headless`` from inside a
    larger program don't have their global signal state mutated.

    ``signal.signal`` is only callable from the main thread; if we are
    not the main thread (e.g. an SDK harness that runs headless in a
    worker thread), the install is skipped and the returned restore is
    a no-op. Cancellation in that case falls back to the agent loop's
    natural turn-boundary checks via ``KeyboardInterrupt`` propagation
    from whatever signal facility the embedder is using.
    """

    previous = _signal.getsignal(_signal.SIGINT)

    def _handler(signum, frame):
        if not in_agent_loop.value:
            # Idle on input — raise so the blocking stdin read returns.
            # No need to swap to ``default_int_handler``: there's no
            # cooperative-unwind escalation state to escalate from, and
            # ``restore_sigint()`` in the ``finally`` block will revert
            # the user's pre-existing handler shortly after the raise
            # unwinds out of ``run_headless``. A second SIGINT before
            # that finally runs would just re-enter this handler and
            # raise again — fine.
            raise KeyboardInterrupt
        if controller.signal.aborted:
            # Second strike during cooperative unwind: re-arm the
            # platform default handler (so any third strike terminates
            # the process the usual way) and raise the force-quit.
            _signal.signal(_signal.SIGINT, _signal.default_int_handler)
            raise KeyboardInterrupt
        controller.abort("user_interrupt")
        try:
            # Plain ASCII for portability — some legacy Windows code
            # pages can't encode U+2026 and would silently drop the
            # message via the outer except.
            stderr.write("\nCancelling... (Ctrl-C again to force quit)\n")
            stderr.flush()
        except Exception:
            # A broken stderr (closed pipe etc.) must not stop the
            # cancellation from propagating — the controller is already
            # tripped, the agent loop will unwind regardless.
            pass

    try:
        _signal.signal(_signal.SIGINT, _handler)
    except (ValueError, OSError):
        # Not in main thread (ValueError) or SIGINT not supported on
        # this platform (OSError on some Windows configurations).
        # Fall back to the agent loop's natural turn-boundary cancel
        # checks via ``KeyboardInterrupt`` — the pre-fix behaviour.
        return lambda: None

    def _restore() -> None:
        try:
            _signal.signal(_signal.SIGINT, previous)
        except (ValueError, OSError):
            pass

    return _restore


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
