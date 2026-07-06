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


_IF_CONDITION_EVENTS = frozenset(
    {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"}
)
# The seven tools whose TS analogs implement preparePermissionMatcher
# (each matches its rule pattern with matchWildcardPattern):
#   file-path tools → file_path / notebook_path;
#   pattern tools (Glob/Grep/Monitor) → pattern;
#   Bash → command (with any-subcommand semantics).
_IF_FILE_PATH_TOOLS = frozenset(
    {"Read", "Edit", "MultiEdit", "Write", "NotebookEdit"}
)
_IF_PATTERN_TOOLS = frozenset({"Glob", "Grep"})
# Bash + Monitor match on the COMMAND with prefix-or-wildcard semantics
# (TS MonitorTool.preparePermissionMatcher also uses `command`, Bash-style).
# Monitor is unreachable today (the port has no Monitor tool) but the
# categorization is kept faithful for when it lands.
_IF_COMMAND_TOOLS = frozenset({"Bash", "Monitor"})

_ESCAPED_STAR = "\x00ESC_STAR\x00"
_ESCAPED_BACKSLASH = "\x00ESC_BSL\x00"


def _match_wildcard_pattern(pattern: str, value: str) -> bool:
    """Port of `matchWildcardPattern`
    (utils/permissions/shellRuleMatching.ts:90) — the SAME matcher every
    TS tool's preparePermissionMatcher uses for `if`, so it is faithful for
    both Bash commands and file/pattern values (and, unlike the Bash-only
    permission matcher, carries no command-chaining strictness)."""
    import re

    trimmed = pattern.strip()
    # Escape-sequence handling: \* → literal star, \\ → literal backslash.
    processed_chars: list[str] = []
    i = 0
    while i < len(trimmed):
        ch = trimmed[i]
        if ch == "\\" and i + 1 < len(trimmed):
            nxt = trimmed[i + 1]
            if nxt == "*":
                processed_chars.append(_ESCAPED_STAR)
                i += 2
                continue
            if nxt == "\\":
                processed_chars.append(_ESCAPED_BACKSLASH)
                i += 2
                continue
        processed_chars.append(ch)
        i += 1
    processed = "".join(processed_chars)

    escaped = re.sub(r"""[.+?^${}()|\[\]\\'"]""", lambda m: "\\" + m.group(0), processed)
    with_wildcards = escaped.replace("*", ".*")
    regex_pattern = (
        with_wildcards.replace(_ESCAPED_STAR, "\\*").replace(_ESCAPED_BACKSLASH, "\\\\")
    )
    # Trailing ' *' (the only unescaped wildcard) → optional, so 'git *'
    # matches bare 'git' too (prefix-rule alignment).
    unescaped_star_count = processed.count("*")
    if regex_pattern.endswith(" .*") and unescaped_star_count == 1:
        regex_pattern = regex_pattern[:-3] + "( .*)?"
    return re.match(f"^{regex_pattern}$", value, re.DOTALL) is not None


def _extract_rule_prefix(rule_content: str) -> str | None:
    """Port of `permissionRuleExtractPrefix` (shellRuleMatching.ts:43-48):
    the legacy colon form ``git:*`` → prefix ``git``; anything else → None."""
    import re

    m = re.match(r"^(.+):\*$", rule_content)
    return m.group(1) if m else None


def _strip_env_prefix(command: str) -> str:
    """Strip leading ``VAR=val`` assignments so ``FOO=bar git push`` matches
    ``Bash(git *)`` (TS matches on argv, BashTool preparePermissionMatcher)."""
    import re

    prev = None
    cur = command.strip()
    while cur != prev:
        prev = cur
        cur = re.sub(r"^[A-Za-z_][A-Za-z0-9_]*=\S*\s+", "", cur)
    return cur


def _command_rule_matches(rule_content: str, command: str) -> bool:
    """The Bash/Monitor command matcher (BashTool.preparePermissionMatcher):
    the colon-prefix branch (``git:*`` → exact or prefix+space), else the
    wildcard matcher — applied to the env-stripped command."""
    cmd = _strip_env_prefix(command)
    prefix = _extract_rule_prefix(rule_content)
    if prefix is not None:
        return cmd == prefix or cmd.startswith(prefix + " ")
    return _match_wildcard_pattern(rule_content, cmd)


def _matchable_values_for_tool(
    tool_name: str, tool_input: dict[str, Any]
) -> list[str] | None:
    """The value(s) a tool's `if` rule content matches against, or None
    when the tool has no matcher analog (→ fail-OPEN, run + warn). Bash
    returns the command plus each chained sub-command (any-subcommand
    match, BashTool preparePermissionMatcher), so `if:"Bash(git *)"` fires
    on `git push && npm test`."""
    if tool_name in _IF_COMMAND_TOOLS:
        cmd = tool_input.get("command")
        if not isinstance(cmd, str):
            return None
        cands = [cmd]
        try:
            from src.permissions.bash_suggestions import (
                contains_unquoted_chaining,
                split_chained_command,
            )

            if contains_unquoted_chaining(cmd):
                cands.extend(split_chained_command(cmd) or [])
        except Exception:  # noqa: BLE001
            pass
        return cands
    if tool_name in _IF_FILE_PATH_TOOLS:
        val = tool_input.get("file_path") or tool_input.get("notebook_path")
        return [val] if isinstance(val, str) else None
    if tool_name in _IF_PATTERN_TOOLS:
        val = tool_input.get("pattern")
        return [val] if isinstance(val, str) else None
    return None


def _matches_if_condition(
    if_condition: str | None, event: str, tool_name: str | None,
    tool_input: dict[str, Any] | None,
) -> bool:
    """SCHEMAS-1 — the port of `prepareIfConditionMatcher`
    (utils/hooks.ts:1571-1610): a hook's `if` permission-rule pre-filter.

    Returns True (run the hook) or False (skip). Semantics match TS:
    * no condition → run;
    * present condition on a NON-tool event → SKIP (TS's caller sees an
      undefined matcher and returns false — hooks.ts:2023-2027);
    * rule tool-name ≠ current tool → skip;
    * no rule-content → run;
    * rule-content → matched with matchWildcardPattern against the tool's
      value(s); a tool WITHOUT a matcher analog fails OPEN (run + warn) so
      a configured hook is never silently disabled.
    """
    if not if_condition:
        return True

    from src.permissions.rule_parser import (
        normalize_legacy_tool_name,
        permission_rule_value_from_string,
    )

    if event not in _IF_CONDITION_EVENTS or not tool_name:
        # A tool-syntax `if` cannot be evaluated for a non-tool event → skip
        # (TS parity, hooks.ts:2023-2027). Not "ignore and run".
        logger.debug(
            "hook `if` condition %r cannot be evaluated for non-tool event %s; skipping",
            if_condition, event,
        )
        return False

    current = normalize_legacy_tool_name(tool_name)
    parsed = permission_rule_value_from_string(if_condition)
    if normalize_legacy_tool_name(parsed.tool_name or "") != current:
        return False
    if not parsed.rule_content:
        return True  # tool-name-only condition → run

    values = _matchable_values_for_tool(current, tool_input or {})
    if values is None:
        # No matcher analog for this tool — fail OPEN (run) with a visible
        # warning rather than silently disabling a configured hook.
        logger.warning(
            "hook `if` condition %r on tool %s has no matcher; running the "
            "hook unconditionally (no per-tool matcher for %s)",
            if_condition, current, current,
        )
        return True

    # Command-class tools (Bash/Monitor) use the prefix-or-wildcard matcher
    # (so colon rules like `Bash(rm:*)` work — the canonical rule form);
    # file/pattern tools use the plain wildcard matcher.
    matcher = (
        _command_rule_matches
        if current in _IF_COMMAND_TOOLS
        else _match_wildcard_pattern
    )
    return any(matcher(parsed.rule_content, v) for v in values)


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

    from src.utils.subprocess_env import subprocess_env

    return {
        # subprocess_env() scrubs secret vars when CLAUDE_CODE_SUBPROCESS_ENV_SCRUB
        # is set (anti-exfiltration; parity with TS subprocessEnv at the hook
        # spawn site) — otherwise a pass-through copy of os.environ.
        **subprocess_env(),
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
                env=_build_hook_env(hook, stdin_data, tool_use_context),
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
                if parsed.updatedPermissions:
                    result.updated_permissions = parsed.updatedPermissions
                if parsed.interrupt:
                    result.interrupt = True
                hso = parsed.hookSpecificOutput or {}
                # EVENT-NAME GATE (critic C1-M1/M3): TS validates the emitted
                # hookEventName against the RUNNING event at the TOP of the
                # `if (json.hookSpecificOutput)` block (hooks.ts:757-765,
                # "Hook returned incorrect event name") — BEFORE the switch
                # that maps the envelope/permissionDecision forms — and rejects
                # the WHOLE output on mismatch. Placed here (above the envelope
                # block below) so it covers ALL hso extraction, not just the
                # permissionDecision path: otherwise a hook registered under
                # the wrong event emitting EITHER form leaks a decision into
                # the permission grant (fail-OPEN). Port posture is warn+drop
                # (the WI-1.4 analog of TS's throw); mirroring TS's
                # `if (expectedHookEvent && …)`, the check is SKIPPED when the
                # running event is absent (direct/test calls pass no
                # ``hook_event``). Also closes m1 (additionalContext
                # over-extraction on wrong-event forms).
                _running_event = stdin_data.get("hook_event")
                _hso_event = hso.get("hookEventName") if isinstance(hso, dict) else None
                if _running_event and _hso_event and _hso_event != _running_event:
                    logger.warning(
                        "Hook %r hookSpecificOutput.hookEventName=%r != running "
                        "event %r; dropping the hookSpecificOutput payload.",
                        command, _hso_event, _running_event,
                    )
                    hso = {}
                # TS wire-envelope compat (utils/hooks.ts:833-840): a hook
                # written for the reference CLI emits
                # ``hookSpecificOutput.decision`` — normalize onto the same
                # fields; the flat form (above) wins on conflict.
                hso_decision = hso.get("decision") if isinstance(hso, dict) else None
                if isinstance(hso_decision, dict):
                    behavior = hso_decision.get("behavior")
                    if result.permission_behavior is None and behavior in ("allow", "deny", "ask"):
                        result.permission_behavior = behavior
                        result.hook_permission_decision_reason = (
                            hso_decision.get("message") or result.hook_permission_decision_reason
                        )
                    if result.updated_input is None and isinstance(hso_decision.get("updatedInput"), dict):
                        result.updated_input = hso_decision["updatedInput"]
                    if result.updated_permissions is None and isinstance(hso_decision.get("updatedPermissions"), list):
                        result.updated_permissions = hso_decision["updatedPermissions"]
                    if hso_decision.get("interrupt"):
                        result.interrupt = True
                # PreToolUse structured form (types/hooks.ts:73-78, mapped at
                # utils/hooks.ts:726-800): ``hookSpecificOutput.permissionDecision``
                # is the DOCUMENTED way a PreToolUse hook allows/denies/asks.
                # TS treats it as the MORE SPECIFIC decision — it OVERRIDES the
                # flat ``decision`` (unlike the PermissionRequest envelope
                # above, which only fills when unset). A deny's message rides
                # hook_permission_decision_reason (the port's single-path deny
                # convention — TS also sets a separate blockingError, which
                # here would double-yield a denial). The event-name gate above
                # has already dropped this whole payload on a wrong-event emit.
                if isinstance(hso, dict) and hso.get("hookEventName") == "PreToolUse":
                    pd = hso.get("permissionDecision")
                    if pd is not None:
                        if pd in ("allow", "deny", "ask"):
                            result.permission_behavior = pd
                            if pd == "deny":
                                result.hook_permission_decision_reason = (
                                    hso.get("permissionDecisionReason")
                                    or parsed.reason
                                    or "Blocked by hook"
                                )
                        else:
                            # TS throws "Unknown hook permissionDecision
                            # type"; the port's WI-1.4 posture is warn + drop.
                            logger.warning(
                                "Hook %r emitted unknown permissionDecision %r;"
                                " valid types are: allow, deny, ask. Dropping.",
                                command, pd,
                            )
                    pdr = hso.get("permissionDecisionReason")
                    if isinstance(pdr, str) and pdr:
                        result.hook_permission_decision_reason = pdr
                    if isinstance(hso.get("updatedInput"), dict):
                        result.updated_input = hso["updatedInput"]
                if parsed.preventContinuation:
                    result.prevent_continuation = True
                    result.stop_reason = parsed.stopReason
                if parsed.additionalContexts:
                    result.additional_contexts = parsed.additionalContexts
                # ``hookSpecificOutput.additionalContext`` (singular string —
                # the PreToolUse/PostToolUse/UserPromptSubmit/SessionStart
                # forms all carry it, utils/hooks.ts:793-800): APPEND onto the
                # additional_contexts list (after the flat assignment above,
                # so both survive) → the hook_additional_context attachment.
                if isinstance(hso, dict):
                    _hso_ac = hso.get("additionalContext")
                    if isinstance(_hso_ac, str) and _hso_ac:
                        result.additional_contexts = (
                            list(result.additional_contexts or []) + [_hso_ac]
                        )
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


async def _run_hooks_for_event(
    event: str,
    tool_name: str | None,
    stdin_data: dict[str, Any],
    tool_use_context: Any,
    abort_signal: Any | None = None,
    timeout_ms: int = TOOL_HOOK_EXECUTION_TIMEOUT_MS,
) -> AsyncGenerator[dict[str, Any], None]:
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
        event_hooks = [h for h in event_hooks if h.source.is_policy]

    tool_use_id = stdin_data.get("tool_use_id", str(uuid4()))
    parent_tool_use_id = ""

    tool_input = stdin_data.get("tool_input") if isinstance(stdin_data.get("tool_input"), dict) else None

    for hook in event_hooks:
        if tool_name and not _matches_tool(hook.matcher, tool_name):
            continue
        # SCHEMAS-1 — the `if` permission-rule pre-filter (was inert: the
        # field parsed but never evaluated, so `if` hooks ran unconditionally).
        if not _matches_if_condition(
            getattr(hook, "if_condition", None), event, tool_name, tool_input
        ):
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
                "updated_permissions": result.updated_permissions,
                "interrupt": result.interrupt,
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


async def execute_permission_request_hooks(
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, Any],
    tool_use_context: Any,
    permission_suggestions: list[Any] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run PermissionRequest hooks for a pending permission ask.

    HOOKS-1 (my-docs/get-parity-by-folder/hooks-refactoring-plan.md W1) —
    the port of ``executePermissionRequestHooks`` (utils/hooks.ts:4392-4427):
    fired at the ask seam BEFORE any interactive prompt, matcher-scoped by
    tool name like PreToolUse. A hook may resolve the ask (allow with
    optional updatedInput/updatedPermissions; deny with message + optional
    interrupt) or stay silent (normal prompt flow continues).
    """
    stdin_data: dict[str, Any] = {
        "tool_name": tool_name,
        "tool_use_id": tool_use_id,
        "tool_input": tool_input,
    }
    if permission_suggestions:
        stdin_data["permission_suggestions"] = permission_suggestions

    abort_signal = None
    abort_ctrl = getattr(tool_use_context, "abort_controller", None)
    if abort_ctrl:
        abort_signal = abort_ctrl.signal

    async for result in _run_hooks_for_event(
        "PermissionRequest",
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


async def execute_stop_failure_hooks(
    last_message: Any,
    tool_use_context: Any,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run StopFailure hooks (ch05 round-3 G1).

    Fired when the loop ends a turn on an API-error response — the
    death-spiral guard path where Stop hooks must NOT run (TS
    query.ts:1346-1349 + the PTL/media surfacing exits :1256/:1263).
    TS dispatches fire-and-forget; the port awaits at the (terminal)
    exit paths — latency bounded by the hook timeout.
    """
    stdin_data = {
        "hook_event": "StopFailure",
        "error": _flatten_text(getattr(last_message, "content", "")) or "",
    }
    abort_signal = None
    abort_ctrl = getattr(tool_use_context, "abort_controller", None)
    if abort_ctrl:
        abort_signal = abort_ctrl.signal
    async for result in _run_hooks_for_event(
        "StopFailure",
        None,
        stdin_data,
        tool_use_context,
        abort_signal=abort_signal,
    ):
        yield result


def _flatten_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        return " ".join(parts)
    return str(content or "")

async def execute_teammate_idle_hooks(
    teammate_name: str,
    team_name: str,
    tool_use_context: Any,
    permission_mode: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """QUERY-1 — port of ``executeTeammateIdleHooks`` (utils/hooks.ts:3920):
    fired from the teammate's stop path after the core Stop/SubagentStop
    loop. Matcher-less (no tool scoping); hook stdin carries the teammate
    identity."""
    stdin_data: dict[str, Any] = {
        "teammate_name": teammate_name,
        "team_name": team_name,
    }
    if permission_mode:
        stdin_data["permission_mode"] = permission_mode
    abort_signal = None
    abort_ctrl = getattr(tool_use_context, "abort_controller", None)
    if abort_ctrl:
        abort_signal = abort_ctrl.signal

    async for result in _run_hooks_for_event(
        "TeammateIdle",
        None,
        stdin_data,
        tool_use_context,
        abort_signal,
    ):
        yield result


async def execute_task_completed_hooks(
    task_id: str,
    task_subject: str,
    task_description: str | None,
    teammate_name: str,
    team_name: str,
    tool_use_context: Any,
    permission_mode: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """QUERY-1 — port of ``executeTaskCompletedHooks`` (utils/hooks.ts:4000):
    fired once per in-progress task OWNED by the stopping teammate. Hook
    stdin carries the task fields + teammate identity
    (TaskCompletedHookInput)."""
    stdin_data: dict[str, Any] = {
        "task_id": task_id,
        "task_subject": task_subject,
        "task_description": task_description,
        "teammate_name": teammate_name,
        "team_name": team_name,
    }
    if permission_mode:
        stdin_data["permission_mode"] = permission_mode
    abort_signal = None
    abort_ctrl = getattr(tool_use_context, "abort_controller", None)
    if abort_ctrl:
        abort_signal = abort_ctrl.signal

    async for result in _run_hooks_for_event(
        "TaskCompleted",
        None,
        stdin_data,
        tool_use_context,
        abort_signal,
    ):
        yield result

