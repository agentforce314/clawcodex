"""Hook execution engine — mirrors TypeScript utils/hooks.ts.

Core hook execution with shell command protocol, JSON stdin/stdout,
exit code semantics, timeout handling, and settings-driven configuration.

Exit code semantics:
- 0: success (stdout parsed as JSON for decisions)
- 2: blocking error (stderr or stdout used as error message)
- other non-zero: non-blocking error (logged, execution continues)

Security invariant: hooks cannot lower security level.
Hook 'allow' does not bypass settings deny/ask rules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import warnings
from typing import Any, AsyncGenerator
from uuid import uuid4

from src.hooks.hook_types import (
    TOOL_HOOK_EXECUTION_TIMEOUT_MS,
    HookConfig,
    HookEvent,
    HookResult,
)
from src.hooks.trust_gate import should_skip_hook_due_to_trust
from src.types.messages import (
    create_attachment_message,
    create_progress_message,
)

logger = logging.getLogger(__name__)


def _get_hooks_from_snapshot(tool_use_context: Any) -> dict[str, list[HookConfig]]:
    """Read hooks from the frozen snapshot held on the active HookConfigManager.

    Mirrors typescript/src/utils/hooks/hooksConfigSnapshot.ts:119-124
    (``getHooksConfigFromSnapshot``). settings.json is never re-read implicitly;
    snapshot updates flow only through ``HookConfigManager.load()`` (startup) or
    ``HookConfigManager.reload_if_changed()`` (explicit /hooks command).

    **Back-compat:** if ``hook_config_manager`` is not set on the context but
    ``options.hooks`` is, fall back to the legacy read path with a
    ``DeprecationWarning``. This keeps existing callers / tests working during
    one CHANGELOG cycle. After the deprecation cycle, the fallback is removed.
    """
    manager = getattr(tool_use_context, "hook_config_manager", None)
    if manager is not None:
        snapshot = getattr(manager, "snapshot", None)
        if snapshot is None:
            # Manager exists but hasn't been loaded — treat as empty.
            return {}
        # Defensive copy: callers MUST NOT mutate the returned dict.
        return {ev: list(hooks) for ev, hooks in snapshot.hooks.items()}

    # Legacy fallback — emit DeprecationWarning if options.hooks carries data.
    return _get_hooks_from_options_legacy(tool_use_context)


def _get_hooks_from_options_legacy(tool_use_context: Any) -> dict[str, list[HookConfig]]:
    """Legacy read path: ``tool_use_context.options.hooks``.

    Bypasses the snapshot freezing semantic introduced in WI-0.1. Preserved for
    one CHANGELOG cycle so existing callers / tests do not break in lockstep
    with the rewire. Emits a ``DeprecationWarning`` when it actually returns
    data (silent no-op when options.hooks is empty/None).
    """
    try:
        options = getattr(tool_use_context, "options", None)
        if options is None:
            return {}
        hooks_config = getattr(options, "hooks", None)
        if hooks_config is None or not hooks_config:
            return {}
        if not isinstance(hooks_config, dict):
            return {}
        result: dict[str, list[HookConfig]] = {}
        for event_name, hook_list in hooks_config.items():
            if isinstance(hook_list, list):
                configs = []
                for h in hook_list:
                    if isinstance(h, dict):
                        configs.append(HookConfig(
                            type=h.get("type", "command"),
                            command=h.get("command", ""),
                            timeout=h.get("timeout"),
                            matcher=h.get("matcher"),
                        ))
                    elif isinstance(h, HookConfig):
                        configs.append(h)
                result[event_name] = configs
        if result:
            warnings.warn(
                "Reading hooks from tool_use_context.options.hooks is deprecated; "
                "wire a HookConfigManager onto tool_use_context.hook_config_manager "
                "and call .load() at bootstrap. The legacy path bypasses the snapshot "
                "security model (chapter §'The Snapshot Security Model'). Will be "
                "removed two CHANGELOG entries after the rename.",
                DeprecationWarning,
                stacklevel=3,
            )
        return result
    except Exception:
        return {}


# Back-compat alias — preserved for any external test fixtures still importing
# ``_get_hooks_from_settings`` directly. New code uses ``_get_hooks_from_snapshot``.
def _get_hooks_from_settings(tool_use_context: Any) -> dict[str, list[HookConfig]]:
    return _get_hooks_from_snapshot(tool_use_context)


def has_hook_for_event(event: str, tool_use_context: Any) -> bool:
    hooks = _get_hooks_from_snapshot(tool_use_context)
    return bool(hooks.get(event))


def _matches_tool(matcher: str | None, tool_name: str) -> bool:
    if matcher is None:
        return True
    if matcher == tool_name:
        return True
    if matcher.endswith("*"):
        prefix = matcher[:-1]
        return tool_name.startswith(prefix)
    if matcher.startswith("*"):
        suffix = matcher[1:]
        return tool_name.endswith(suffix)
    return matcher == tool_name


def _build_hook_env(
    hook: HookConfig,
    stdin_data: dict[str, Any],
    tool_use_context: Any | None,
) -> dict[str, str]:
    """Compute the env-var dict passed to a command-hook subprocess.

    Phase-1 / WI-1.5 — adds three vars on top of inherited ``os.environ``:

      * ``CLAUDE_HOOK_EVENT`` — the canonical event name (existing).
      * ``CLAUDE_PROJECT_DIR`` — workspace root from the active context.
        Empty string if the context doesn't carry a workspace_root.
      * ``CLAUDE_PLUGIN_ROOT`` — set from ``hook.skill_root`` (populated only
        for skill-declared hooks; empty for everything else).
      * ``CLAUDE_ENV_FILE`` — per-fire ephemeral env file path. Set ONLY for
        the three lifecycle events that benefit from env propagation
        (``SessionStart``, ``Setup``, ``CwdChanged``). For other events:
        empty string. Per N4: this WI sets the path; the
        sourcing-and-applying loop (read the file back and apply exports to
        subsequent shells in the session) is a separate follow-up ticket.
        TODO(ch12-followup): ticket #<TBD> covers the env-file source/apply
        cycle.
    """
    event_name = stdin_data.get("hook_event", "")
    workspace_root = ""
    if tool_use_context is not None:
        wr = getattr(tool_use_context, "workspace_root", None)
        if wr is not None:
            workspace_root = str(wr)

    env_file = _env_file_for_event(event_name)

    return {
        **os.environ,
        "CLAUDE_HOOK_EVENT": event_name,
        "CLAUDE_PROJECT_DIR": workspace_root,
        "CLAUDE_PLUGIN_ROOT": hook.skill_root or "",
        "CLAUDE_ENV_FILE": env_file,
    }


def _env_file_for_event(event_name: str) -> str:
    """Return a writable path for the hook to write env exports to.

    Only set for events whose hooks may legitimately propagate env to
    subsequent shells in the session (TS pattern). For other events, return
    empty string — the hook MAY still observe ``CLAUDE_ENV_FILE`` and treat
    "empty" as "no env propagation requested."

    The file is per-fire ephemeral: a unique path under
    ``~/.clawcodex/hook-env/<event>.<pid>.<nanos>``. This WI does NOT
    create the file or read it back; it only computes the path. Sourcing is
    a follow-up.
    """
    if event_name not in ("SessionStart", "Setup", "CwdChanged"):
        return ""
    home = os.path.expanduser("~")
    return os.path.join(
        home, ".clawcodex", "hook-env",
        f"{event_name}.{os.getpid()}.{time.time_ns()}",
    )


async def _execute_command_hook(
    hook: HookConfig,
    stdin_data: dict[str, Any],
    abort_signal: Any | None = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
    tool_use_context: Any | None = None,
) -> HookResult:
    command = hook.command
    if not command:
        return HookResult()

    effective_timeout = (hook.timeout or timeout_ms) / 1000.0
    start_time = time.monotonic()

    try:
        stdin_json = json.dumps(stdin_data, default=str)

        process = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_build_hook_env(hook, stdin_data, tool_use_context),
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(stdin_json.encode()),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except Exception:
                pass
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return HookResult(
                blocking_error=f"Hook timed out after {duration_ms}ms",
                exit_code=-1,
                duration_ms=duration_ms,
                command=command,
            )

        duration_ms = int((time.monotonic() - start_time) * 1000)
        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        exit_code = process.returncode or 0

        if abort_signal and abort_signal.aborted:
            return HookResult(
                exit_code=exit_code,
                duration_ms=duration_ms,
                command=command,
            )

        if exit_code == 2:
            error_msg = stderr or stdout or "Hook exited with code 2"
            return HookResult(
                blocking_error=error_msg,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                command=command,
            )

        if exit_code != 0:
            return HookResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                command=command,
            )

        result = HookResult(
            exit_code=0,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            command=command,
        )

        # Phase-1 / WI-1.4 — schema-validated output parsing. Replaces the
        # prior ad-hoc ``dict.get`` block: malformed output (capital-D
        # ``Deny``, unknown fields, non-string ``reason``, etc.) used to no-op
        # silently; now it logs a WARNING and the decision payload is
        # dropped (exit code is still honored).
        if stdout:
            from src.hooks.output_schema import parse_hook_output  # local import: pydantic
            parsed, err = parse_hook_output(stdout)
            if err is not None:
                logger.warning(
                    "Hook %r emitted output that failed schema validation; "
                    "dropping decision payload. error=%s stdout=%r",
                    command, err, stdout[:200],
                )
            elif parsed is not None:
                if parsed.decision is not None:
                    result.permission_behavior = parsed.decision
                    result.hook_permission_decision_reason = parsed.reason
                if parsed.updatedInput:
                    result.updated_input = parsed.updatedInput
                if parsed.preventContinuation:
                    result.prevent_continuation = True
                    result.stop_reason = parsed.stopReason
                if parsed.additionalContexts:
                    result.additional_contexts = parsed.additionalContexts
                if parsed.updatedMCPToolOutput is not None:
                    result.updated_mcp_tool_output = parsed.updatedMCPToolOutput

        return result

    except Exception as e:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return HookResult(
            blocking_error=f"Hook execution error: {e}",
            exit_code=-1,
            duration_ms=duration_ms,
            command=command,
        )


async def _collect_hooks_for_event(
    event: str,
    tool_name: str | None,
    tool_use_context: Any,
    tool_input: dict[str, Any] | None = None,
) -> list[Any]:
    """Phase-3 / WI-3.1 (I2 contract split — Phase 3 owns "collect"):
    merge snapshot hooks with session-registered hooks for ``event``,
    apply the trust gate and matcher filter, return the ordered list of
    ``SessionHookEntry``-shaped items to fire.

    Phase-4 / WI-4.2 extension: ``tool_input`` is now threaded through so
    the ``if`` condition matcher (``matches_hook_condition``) can evaluate
    rule-content patterns against the tool's input fields (e.g.,
    ``Bash(git commit*)`` against ``tool_input["command"]``).

    Each returned item carries:
      * ``config`` — the ``HookConfig`` to execute
      * ``event``  — the event name (post-Stop→SubagentStop conversion)
      * ``matcher`` — copied from ``config.matcher`` (or empty string)
      * ``on_success`` — optional callback fired by the executor after a
                          successful firing; populated for ``once: true``
                          session-scoped hooks.

    Snapshot hooks always have ``on_success=None``: ``once: true`` is a
    session-hook-only contract per the chapter. (A snapshot ``once: true``
    would be ambiguous — what would "once" mean for a config-driven hook
    that's reloaded fresh on every startup?)
    """
    from .condition_matcher import matches_hook_condition
    from .session_hooks import SessionHookEntry

    trust_skip = should_skip_hook_due_to_trust(tool_use_context)

    snapshot_hooks = _get_hooks_from_snapshot(tool_use_context).get(event, [])
    if trust_skip:
        snapshot_hooks = [h for h in snapshot_hooks if h.source.is_policy]

    snapshot_entries: list[SessionHookEntry] = [
        SessionHookEntry(
            config=h,
            event=event,  # type: ignore[arg-type]
            matcher=h.matcher or "",
            on_success=None,
        )
        for h in snapshot_hooks
    ]

    session_entries: list[SessionHookEntry] = []
    registry = getattr(tool_use_context, "session_hook_registry", None)
    session_id = getattr(tool_use_context, "session_id", None)
    if registry is not None and session_id is not None:
        raw_session_entries = await registry.get_for_event(session_id, event)
        if trust_skip:
            # Session-source hooks are NEVER policy-tier (only POLICY_SETTINGS
            # is); under trust gate, all session-scoped hooks are dropped.
            raw_session_entries = []
        session_entries = list(raw_session_entries)

    merged = snapshot_entries + session_entries

    if tool_name is not None:
        # Phase-4 / WI-4.2 — combined matcher + ``if_condition`` filter.
        # ``matches_hook_condition`` AND-s the simple matcher with the
        # permission-rule grammar. Pre-Phase-4 only ``matcher`` was
        # consulted; the ``if_condition`` field on HookConfig was parsed
        # but never used. Now both contribute.
        ti = tool_input or {}
        merged = [
            entry for entry in merged
            if matches_hook_condition(entry.config, tool_name, ti)
        ]

    return merged


async def _dispatch_hook_by_type(
    hook: HookConfig,
    stdin_data: dict[str, Any],
    *,
    abort_signal: Any | None = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
    tool_use_context: Any = None,
) -> HookResult:
    """Dispatch a hook to its type-specific executor.

    Phase-7 follow-up D5. Mirrors the dispatch pattern in TS
    ``hookEvents.ts`` / ``hooks.ts`` where each hook type routes to its
    own executor. Pre-D5 the Python executor always called
    ``_execute_command_hook`` regardless of ``hook.type`` — agent /
    prompt / http hooks were silently broken for non-lifecycle events.

    Provider / model for LLM-driven hook types (agent, prompt) come
    from ``tool_use_context.provider`` and ``tool_use_context.model``.
    Bootstrap wires these onto the context at session start (analogous
    to D3's ``forked_skill_runner`` wiring); sub-agent contexts inherit
    from their parent.
    """
    hook_type = hook.type
    if hook_type == "command":
        return await _execute_command_hook(
            hook, stdin_data,
            abort_signal=abort_signal,
            timeout_ms=timeout_ms,
            tool_use_context=tool_use_context,
        )
    if hook_type == "http":
        from .exec_http_hook import execute_http_hook
        return await execute_http_hook(hook, stdin_data, timeout_ms=timeout_ms)
    if hook_type == "prompt":
        from .exec_prompt_hook import execute_prompt_hook
        provider = getattr(tool_use_context, "provider", None) if tool_use_context else None
        model = getattr(tool_use_context, "model", None) if tool_use_context else None
        return await execute_prompt_hook(
            hook, stdin_data, provider=provider, model=model,
        )
    if hook_type == "agent":
        from .exec_agent_hook import execute_agent_hook
        provider = getattr(tool_use_context, "provider", None) if tool_use_context else None
        model = getattr(tool_use_context, "model", None) if tool_use_context else None
        return await execute_agent_hook(
            hook, stdin_data, provider=provider, model=model,
        )
    # Unknown hook type → log and return a no-op result. The validator
    # at config-load time should have caught this; if it slipped
    # through, we don't crash the executor.
    logger.warning("Unknown hook type %r; treating as no-op", hook_type)
    return HookResult(exit_code=0)


async def _run_hooks_for_event(
    event: str,
    tool_name: str | None,
    stdin_data: dict[str, Any],
    tool_use_context: Any,
    abort_signal: Any | None = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
) -> AsyncGenerator[dict[str, Any], None]:
    # WI-3.1 — Phase 3 owns "collect": merge snapshot + session-registry
    # hooks. WI-4.2 — also feeds tool_input through so the ``if``-condition
    # matcher can evaluate rule-content patterns against tool fields.
    entries = await _collect_hooks_for_event(
        event,
        tool_name,
        tool_use_context,
        tool_input=stdin_data.get("tool_input"),
    )

    tool_use_id = stdin_data.get("tool_use_id", str(uuid4()))
    parent_tool_use_id = ""

    # WI-4.1 — collect per-hook results in a list rather than yielding
    # decisions per-hook; aggregate at the end via ``aggregate_hook_results``.
    # Per-hook progress and attachment messages still yield live (those are
    # for UI feedback, not decision-routing).
    collected_results: list[HookResult] = []

    # Phase-6 / WI-6.1 — emission stream subscribers see one
    # ``hook_started`` per hook before execution, one ``hook_response``
    # after, and one ``hook_aggregated`` at the end. Local import
    # because ``src.hooks.events`` shouldn't be a hard dep of executors
    # that don't have subscribers (zero-subscriber path is one
    # ``_dispatch`` short-circuit on the global enable flag).
    from src.hooks.events import emit_hook_started, emit_hook_response

    for hook_index, entry in enumerate(entries):
        hook = entry.config
        # Hook id ties ``hook_started`` to the matching ``hook_response``.
        # Composition (event, sequence, tool_use_id) ensures uniqueness
        # across concurrent _run_hooks_for_event calls (each with its own
        # tool_use_id).
        hook_event_id = f"{event}:{hook_index}:{tool_use_id}"

        emit_hook_started(
            hook_id=hook_event_id,
            event=event,
            hook_type=hook.type,
            command=hook.command,
            source=hook.source,
            tool_use_id=tool_use_id,
        )

        yield {
            "message": create_progress_message(
                toolUseID=tool_use_id,
                parentToolUseID=parent_tool_use_id,
                data={"command": hook.command, "prompt_text": None},
            ),
        }

        # Phase-7 follow-up D5 — dispatch by hook type. Pre-D5 the
        # executor always called ``_execute_command_hook`` regardless
        # of ``hook.type``; agent / prompt / http hooks for non-
        # lifecycle events (PreToolUse, etc.) silently degraded to
        # spawning empty commands. D5 routes each type to its proper
        # executor and threads ``provider``/``model`` from the active
        # ToolContext for the LLM-driven hook types.
        result = await _dispatch_hook_by_type(
            hook,
            {**stdin_data, "hook_event": event},
            abort_signal=abort_signal,
            timeout_ms=timeout_ms,
            tool_use_context=tool_use_context,
        )

        collected_results.append(result)

        emit_hook_response(
            hook_id=hook_event_id,
            event=event,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            blocking_error=result.blocking_error,
            permission_behavior=result.permission_behavior,
            command=hook.command,
        )

        # WI-3.1 — ``once: true`` removal. Fire the entry's on_success
        # callback after a *successful* firing (exit 0, no blocking_error,
        # no permission deny). The callback fire-and-forgets removal via
        # ``asyncio.create_task`` so the executor doesn't await the lock
        # acquisition.
        if (
            entry.on_success is not None
            and result.exit_code == 0
            and result.blocking_error is None
            and result.permission_behavior != "deny"
        ):
            try:
                entry.on_success()
            except Exception:
                logger.exception("once: true on_success callback raised")

        # Per-hook attachment messages (UI feedback) — live yields.
        # Decision messages move to the aggregation pass below.
        if result.exit_code is not None and result.exit_code != 0 and result.exit_code != 2:
            yield {
                "message": create_attachment_message({
                    "type": "hook_non_blocking_error",
                    "hook_event": event,
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_ms": result.duration_ms,
                    "command": result.command,
                }),
            }
        elif result.exit_code == 0:
            yield {
                "message": create_attachment_message({
                    "type": "hook_success",
                    "hook_event": event,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "duration_ms": result.duration_ms,
                    "command": result.command,
                }),
            }

    # WI-4.1 — aggregate decisions across all hooks with deny>ask>allow
    # precedence. Pre-Phase-4 each hook yielded its own decision payload;
    # downstream consumers reading only the first yield could get the
    # wrong answer when multiple hooks contributed conflicting decisions.
    # Now the executor yields ONE aggregated decision payload (matching
    # the per-hook yield shape so consumers don't need to restructure)
    # plus an ``aggregated_hook_decision`` key carrying the full
    # AggregatedHookResult for telemetry/UI.
    if collected_results:
        from .aggregation import aggregate_hook_results
        from src.hooks.events import emit_hook_aggregated
        agg = aggregate_hook_results(collected_results)

        # Phase-6 / WI-6.1 — fire the final ``hook_aggregated`` event so
        # subscribers see the post-aggregation decision + full
        # ``contributing_reasons`` attribution without re-deriving from
        # individual ``hook_response`` events.
        emit_hook_aggregated(event=event, aggregated=agg)

        if agg.blocking_error:
            yield {
                "blocking_error": {
                    "blocking_error": agg.blocking_error,
                    # ``command`` carries the first contributing-result's
                    # command for log/diagnostic purposes; the full
                    # attribution is in ``contributing_reasons``.
                    "command": next(
                        (r.command for r in collected_results if r.blocking_error),
                        None,
                    ),
                },
            }

        if agg.permission_behavior is not None:
            yield {
                "permission_behavior": agg.permission_behavior,
                "hook_permission_decision_reason": agg.hook_permission_decision_reason,
                "updated_input": agg.updated_input,
            }

        if agg.prevent_continuation:
            yield {
                "prevent_continuation": True,
                "stop_reason": agg.stop_reason,
            }

        if agg.additional_contexts:
            yield {"additional_contexts": agg.additional_contexts}

        if agg.updated_mcp_tool_output is not None:
            yield {"updated_mcp_tool_output": agg.updated_mcp_tool_output}

        if agg.updated_input and agg.permission_behavior is None:
            yield {"updated_input": agg.updated_input}

        # New: full attribution payload for telemetry/UI subscribers that
        # want to render "denied by hook A (because X), also denied by
        # hook B (because Y)" without re-deriving from per-result yields.
        if agg.contributing_reasons:
            yield {"aggregated_hook_decision": agg}


async def execute_pre_tool_hooks(
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, Any],
    tool_use_context: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    stdin_data = {
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "tool_input": tool_input,
    }

    abort_signal = None
    abort_ctrl = getattr(tool_use_context, "abort_controller", None)
    if abort_ctrl:
        abort_signal = abort_ctrl.signal

    async for result in _run_hooks_for_event(
        "PreToolUse",
        tool_name,
        stdin_data,
        tool_use_context,
        abort_signal=abort_signal,
    ):
        yield result


async def execute_post_tool_hooks(
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, Any],
    tool_response: Any,
    tool_use_context: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    stdin_data = {
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "tool_input": tool_input,
        "tool_response": str(tool_response) if not isinstance(tool_response, (str, dict, list)) else tool_response,
    }

    abort_signal = None
    abort_ctrl = getattr(tool_use_context, "abort_controller", None)
    if abort_ctrl:
        abort_signal = abort_ctrl.signal

    async for result in _run_hooks_for_event(
        "PostToolUse",
        tool_name,
        stdin_data,
        tool_use_context,
        abort_signal=abort_signal,
    ):
        yield result


async def execute_post_tool_failure_hooks(
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, Any],
    error: str,
    tool_use_context: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    stdin_data = {
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "tool_input": tool_input,
        "error": error,
    }

    abort_signal = None
    abort_ctrl = getattr(tool_use_context, "abort_controller", None)
    if abort_ctrl:
        abort_signal = abort_ctrl.signal

    async for result in _run_hooks_for_event(
        "PostToolUseFailure",
        tool_name,
        stdin_data,
        tool_use_context,
        abort_signal=abort_signal,
    ):
        yield result


async def execute_stop_hooks(
    *,
    permission_mode: str | None = None,
    abort_signal: Any | None = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
    stop_hook_active: bool = False,
    subagent_id: str | None = None,
    tool_use_context: Any = None,
    messages: list[Any] | None = None,
    agent_type: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    event = "SubagentStop" if subagent_id else "Stop"

    stdin_data: dict[str, Any] = {
        "permission_mode": permission_mode,
        "stop_hook_active": stop_hook_active,
    }
    if subagent_id:
        stdin_data["subagent_id"] = subagent_id
    if agent_type:
        stdin_data["agent_type"] = agent_type

    async for result in _run_hooks_for_event(
        event,
        None,
        stdin_data,
        tool_use_context,
        abort_signal=abort_signal,
        timeout_ms=timeout_ms,
    ):
        yield result
