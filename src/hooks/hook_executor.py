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
        (``SessionStart``, ``Setup``, ``CwdChanged``) and only for
        POSIX-shell hooks (TS parity: PowerShell hooks are skipped — they
        would write ``$env:FOO = ...`` syntax a POSIX source can't
        consume). After the hook completes successfully the executor
        evaluates the file and applies the exports to subsequent Bash
        tool commands (#281, ``_apply_env_file``).
    """
    event_name = stdin_data.get("hook_event", "")
    workspace_root = ""
    if tool_use_context is not None:
        wr = getattr(tool_use_context, "workspace_root", None)
        if wr is not None:
            workspace_root = str(wr)

    # PowerShell hooks never get the env file (TS parity, hooks.ts
    # ``!isPowerShell``): they'd write ``$env:FOO = ...`` syntax that a
    # POSIX source can't consume.
    if hook.shell == "powershell":
        env_file = ""
    else:
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
    ``~/.clawcodex/hook-env/<event>.<pid>.<nanos>``. The parent directory
    is created here so a hook's ``echo ... > "$CLAUDE_ENV_FILE"`` redirect
    can succeed; the executor reads the file back and applies the exports
    after the hook completes (#281, ``_apply_env_file``).
    """
    # TS also includes FileChanged (hooks.ts:1097); add it here when the
    # file-watcher event is ported.
    if event_name not in ("SessionStart", "Setup", "CwdChanged"):
        return ""
    home = os.path.expanduser("~")
    env_dir = os.path.join(home, ".clawcodex", "hook-env")
    try:
        os.makedirs(env_dir, exist_ok=True)
    except OSError:
        return ""  # unwritable home — skip env propagation for this fire
    return os.path.join(
        env_dir, f"{event_name}.{os.getpid()}.{time.time_ns()}",
    )


_MAX_ENV_FILE_BYTES = 256 * 1024

# Noise the sourcing shell itself sets — never part of the hook's intent.
_SHELL_NOISE_KEYS = frozenset({"_", "SHLVL", "PWD", "OLDPWD"})


def _shell_eval_env_exports(env_file: str) -> dict[str, str]:
    """Evaluate the env file with a real POSIX shell and return the env
    delta it produced (#281).

    Sourcing — rather than parsing ``KEY=VAL`` lines — is load-bearing:
    the canonical use case is ``export PATH="$HOME/bin:$PATH"`` (venv /
    conda activation), and a literal parse would store the unexpanded
    ``$HOME/bin:$PATH`` string, corrupting PATH for every later spawn.
    The shell sees (and the diff baseline includes) the current session
    view, so a later hook can prepend to a PATH an earlier hook already
    extended. Same trust boundary as the hook itself, which already ran
    arbitrary shell.

    Known limits: ``unset FOO`` is ignored (the diff only observes
    additions/changes); the eval is a deliberately synchronous
    ``subprocess.run`` inside the async hook path — ~10ms, once per fire
    of three rare lifecycle events.
    """
    import subprocess

    if os.name == "nt":
        return {}  # POSIX-shell contract; PowerShell hooks don't get the var
    from .session_env import get_session_hook_env

    base = {**os.environ, **get_session_hook_env()}
    try:
        # ``set -a``: auto-export plain ``KEY=VAL`` assignments too — the
        # documented contract accepts both with and without ``export``.
        proc = subprocess.run(
            [
                "/bin/sh",
                "-c",
                'set -a; . "$1" >/dev/null 2>&1 || exit 9; env -0',
                "sh",
                env_file,
            ],
            capture_output=True,
            env=base,
            timeout=10,
        )
    except Exception:
        logger.debug("failed to evaluate %s", env_file, exc_info=True)
        return {}
    if proc.returncode != 0:
        logger.warning(
            "CLAUDE_ENV_FILE %s failed to source (exit %s); ignoring",
            env_file,
            proc.returncode,
        )
        return {}
    exports: dict[str, str] = {}
    for entry in proc.stdout.split(b"\0"):
        if not entry:
            continue
        raw_key, sep, raw_val = entry.partition(b"=")
        if not sep:
            continue
        key = raw_key.decode("utf-8", errors="replace")
        if key in _SHELL_NOISE_KEYS:
            continue
        value = raw_val.decode("utf-8", errors="replace")
        if base.get(key) != value:
            exports[key] = value
    return exports


def _discard_env_file(env_file: str) -> None:
    """Remove the per-fire env file without applying it (fail/timeout/abort
    paths — partial writes from an unsuccessful hook are untrusted)."""
    if not env_file:
        return
    try:
        os.unlink(env_file)
    except OSError:
        pass


def _apply_env_file(env_file: str, event: str) -> None:
    """Source-and-apply cycle for ``CLAUDE_ENV_FILE`` (#281).

    Evaluates the per-fire env file a SessionStart/Setup/CwdChanged hook
    may have written, merges the resulting exports into the session hook
    env (consumed by the Bash tool at spawn — NOT the host process env;
    TS parity: ``sessionEnvironment.ts`` injects into bash commands
    only), and removes the ephemeral file. Fail-soft throughout — env
    propagation is a convenience, never a reason to fail the hook
    pipeline.

    Deliberate divergence from TS: TS sources whatever the file holds
    regardless of how the hook exited; here only a hook that completed
    successfully gets its exports applied (the caller gates on
    ``exit_code == 0``) — partial writes from a failed/timed-out hook
    are discarded.
    """
    if not env_file:
        return
    try:
        if not os.path.isfile(env_file):
            return
        if os.path.getsize(env_file) > _MAX_ENV_FILE_BYTES:
            logger.warning(
                "CLAUDE_ENV_FILE %s exceeds %d bytes; ignoring",
                env_file,
                _MAX_ENV_FILE_BYTES,
            )
            return
        exports = _shell_eval_env_exports(env_file)
        if exports:
            from .session_env import merge_into_bucket

            merge_into_bucket(event, exports)
            logger.debug(
                "applied %d env export(s) from %s hook env file: %s",
                len(exports),
                event,
                sorted(exports.keys()),
            )
    except Exception:
        logger.debug("failed to apply %s", env_file, exc_info=True)
    finally:
        try:
            os.unlink(env_file)
        except OSError:
            pass


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

    env_file = ""
    try:
        stdin_json = json.dumps(stdin_data, default=str)

        # Built once per fire: the env dict carries the unique
        # CLAUDE_ENV_FILE path that the source-and-apply step below must
        # read back (#281) — calling _build_hook_env twice would mint two
        # different paths.
        hook_env = _build_hook_env(hook, stdin_data, tool_use_context)
        env_file = hook_env.get("CLAUDE_ENV_FILE", "")

        # Round-2 / Ch12 — per-hook shell selection. ``shell="powershell"``
        # spawns ``pwsh`` with explicit argv and skips the bash-shell path.
        # ``None`` / ``"bash"`` keeps the historical ``create_subprocess_shell``
        # invocation. Mirrors the TS branch at
        # ``typescript/src/utils/hooks.ts:1098-1125``.
        if hook.shell == "powershell":
            from .shell_invocation import build_powershell_args, find_powershell_path

            pwsh_path = find_powershell_path()
            if pwsh_path is None:
                duration_ms = int((time.monotonic() - start_time) * 1000)
                # Error string mirrors TS at typescript/src/utils/hooks.ts:1102-1106
                # verbatim (single quotes around 'powershell' as in the TS source)
                # so log scrapers / regression tests written against TS messages
                # transfer unchanged.
                return HookResult(
                    blocking_error=(
                        f"Hook \"{command}\" has shell: 'powershell' but no "
                        "PowerShell executable (pwsh or powershell) was found "
                        "on PATH. Install PowerShell, or remove "
                        "\"shell\": \"powershell\" to use bash."
                    ),
                    exit_code=-1,
                    duration_ms=duration_ms,
                    command=command,
                )
            process = await asyncio.create_subprocess_exec(
                pwsh_path,
                *build_powershell_args(command),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=hook_env,
            )
        else:
            # Default (bash on POSIX via /bin/sh, the historical path).
            # An explicit ``shell="bash"`` lands here too — it's a no-op
            # alias for ``None`` per the chapter's "defaults to bash"
            # contract.
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=hook_env,
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

        # Source-and-apply (#281): only a hook that completed successfully
        # gets its env exports applied — a timed-out, aborted, or failed
        # hook's partial writes are discarded by the finally below
        # (deliberate divergence from TS, which sources unconditionally).
        _apply_env_file(env_file, stdin_data.get("hook_event", ""))

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
    finally:
        # Ephemeral-file cleanup on every exit path. A no-op after a
        # successful apply (which already unlinked); on timeout / abort /
        # non-zero exit it discards unapplied partial writes.
        _discard_env_file(env_file)


async def _run_hooks_for_event(
    event: str,
    tool_name: str | None,
    stdin_data: dict[str, Any],
    tool_use_context: Any,
    abort_signal: Any | None = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
) -> AsyncGenerator[dict[str, Any], None]:
    # #281: each fire of an env-propagating event REPLACES that event's
    # session exports — for CwdChanged this is the TS clearing semantics
    # (clearCwdEnvFiles): the previous directory's per-project env never
    # leaks into the next one, even when the new cwd defines no hooks.
    if event in ("SessionStart", "Setup", "CwdChanged"):
        from .session_env import clear_event_bucket

        clear_event_bucket(event)

    # WI-0.2 — workspace-trust gate. Skip non-policy hooks while the workspace
    # is untrusted. The per-hook policy check happens below since policy-source
    # identification is per-HookConfig.
    trust_skip = should_skip_hook_due_to_trust(tool_use_context)

    # WI-0.1 — read from the frozen snapshot, not from options.hooks. The
    # snapshot is built once at startup by HookConfigManager.load() and is
    # immune to settings.json mutation between trust acceptance and tool calls.
    hooks = _get_hooks_from_snapshot(tool_use_context)
    event_hooks = hooks.get(event, [])

    if trust_skip:
        # Drop everything that isn't a policy-source hook. ``HookConfig.source``
        # is a ``HookSource`` enum; any non-policy value is gated. Imported
        # locally to avoid pulling the enum into module-init paths that don't
        # need it. Phase-1 / WI-1.2 renamed ``POLICY`` → ``POLICY_SETTINGS``;
        # the ``is_policy`` predicate is the canonical way to ask the
        # question and shields callers from future renames.
        gated = [h for h in event_hooks if not h.source.is_policy]
        event_hooks = [h for h in event_hooks if h.source.is_policy]
        if gated:
            # Breadcrumb for "why don't my hooks run": configured hooks
            # silently not firing in an untrusted workspace is
            # undiagnosable otherwise (#275).
            logger.info(
                "skipping %d non-policy %s hook(s): workspace is untrusted",
                len(gated),
                event,
            )

    tool_use_id = stdin_data.get("tool_use_id", str(uuid4()))
    parent_tool_use_id = ""

    for hook in event_hooks:
        if tool_name and not _matches_tool(hook.matcher, tool_name):
            continue

        yield {
            "message": create_progress_message(
                toolUseID=tool_use_id,
                parentToolUseID=parent_tool_use_id,
                data={"command": hook.command, "prompt_text": None},
            ),
        }

        result = await _execute_command_hook(
            hook,
            {**stdin_data, "hook_event": event},
            abort_signal=abort_signal,
            timeout_ms=timeout_ms,
            tool_use_context=tool_use_context,
        )

        if result.blocking_error:
            yield {"blocking_error": {"blocking_error": result.blocking_error, "command": result.command}}

        if result.permission_behavior is not None:
            yield {
                "permission_behavior": result.permission_behavior,
                "hook_permission_decision_reason": result.hook_permission_decision_reason,
                "updated_input": result.updated_input,
            }

        if result.prevent_continuation:
            yield {
                "prevent_continuation": True,
                "stop_reason": result.stop_reason,
            }

        if result.additional_contexts:
            yield {"additional_contexts": result.additional_contexts}

        if result.updated_mcp_tool_output is not None:
            yield {"updated_mcp_tool_output": result.updated_mcp_tool_output}

        if result.updated_input and result.permission_behavior is None:
            yield {"updated_input": result.updated_input}

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
