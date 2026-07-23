"""Core bash tool definition -- execution, permissions, and result mapping."""

from __future__ import annotations

import json
import os as _os_mod
import re
import shlex
import signal as _signal_mod
import subprocess
import sys as _sys_mod
import time as _time_mod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Poll interval for the abort/timeout watcher. 50 ms keeps ESC perceptibly
# instant (well under the ~100 ms threshold where humans notice latency)
# while costing ~20 wakeups/sec for a long-running command — negligible.
_ABORT_POLL_INTERVAL_S = 0.05

# Bound on how long we wait for the kernel to reap a SIGKILL'd process
# before falling through to drain pipes via ``communicate()``. SIGKILL
# itself is uncatchable; a non-trivial wait here only happens when the
# child is stuck in an uninterruptible kernel wait (e.g. an NFS mount
# that lost the server). Mirrors the spirit of TS's ``tree-kill`` reap
# in that we bound the post-kill drain, not the pre-kill grace.
_KILL_REAP_TIMEOUT_S = 2.0


@dataclass
class _BashRunResult:
    returncode: int
    stdout: str
    stderr: str
    interrupted: bool = False
    timed_out: bool = False


def _get_abort_signal(context: ToolContext) -> Any:
    # ``abort_controller`` is non-optional on ``ToolContext``; the
    # ``getattr(..., None)`` indirection used to paper over the
    # historical "field is None" hazard class.
    return context.abort_controller.signal


def _kill_process_group(pid: int, sig: int) -> None:
    try:
        if _sys_mod.platform == "win32":
            # No setpgid on Windows; just kill the process itself.
            _os_mod.kill(pid, sig)
        else:
            _os_mod.killpg(_os_mod.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        # Already gone (race vs. natural exit) or insufficient
        # privileges — fall through to subprocess.wait() which will
        # surface the right state.
        pass


def _run_bash_with_abort(
    argv: list[str],
    *,
    cwd: str,
    timeout_s: int,
    abort_signal: Any | None,
) -> _BashRunResult:
    """Run ``argv`` with abort + timeout supervision.

    Replaces ``subprocess.run(..., timeout=...)``: launches the
    subprocess in its own session/process group, polls for completion
    while watching ``abort_signal.aborted`` and the timeout, and kills
    the whole group (SIGTERM → grace → SIGKILL) when either fires.
    Returning quickly on abort is what makes ESC feel instant — the
    previous ``subprocess.run`` had to wait the entire timeout.
    """

    # ``stdin=DEVNULL`` matches TS ``Shell.ts`` (stdio[0] = 'pipe' with the
    # writable end never written to). Without this the child inherits the
    # parent's stdin -- when clawcodex runs in a terminal, that's a TTY, and
    # scaffolders like ``npm create vite`` see ``isatty(0)`` and try to prompt
    # for confirmation, hanging the command until timeout.
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if _sys_mod.platform == "win32":
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        popen_kwargs["start_new_session"] = True

    if "env" not in popen_kwargs:
        # Scrub secret env vars when CLAUDE_CODE_SUBPROCESS_ENV_SCRUB is set
        # (anti-exfiltration; parity with TS subprocessEnv at the Bash site).
        from src.utils.subprocess_env import subprocess_env

        popen_kwargs["env"] = subprocess_env()

    proc = subprocess.Popen(argv, **popen_kwargs)

    deadline = _time_mod.monotonic() + timeout_s
    interrupted = False
    timed_out = False

    while True:
        if proc.poll() is not None:
            break
        if abort_signal is not None and getattr(abort_signal, "aborted", False):
            interrupted = True
            break
        if _time_mod.monotonic() >= deadline:
            timed_out = True
            break
        _time_mod.sleep(_ABORT_POLL_INTERVAL_S)

    # Mirrors TS ``ShellCommand.ts:337-343`` (``#doKill``): both the
    # abort and timeout paths actually call ``treeKill(pid, 'SIGKILL')``
    # — the SIGTERM passed into ``#doKill(SIGTERM)`` from the timeout
    # handler is a *label* used downstream by ``#handleExit`` to choose
    # the stderr prefix, not the signal actually sent. Send SIGKILL
    # immediately in both cases for byte-for-byte parity and to keep
    # ESC latency under the ~50ms target tracked by PR #130.
    # ``interrupted`` / ``timed_out`` are the source-of-truth
    # discriminator for downstream callers; the exit-code label is
    # rewritten in ``_bash_call``.
    if interrupted or timed_out:
        _kill_process_group(proc.pid, _signal_mod.SIGKILL)
        try:
            proc.wait(timeout=_KILL_REAP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            # SIGKILL is uncatchable, so this only happens when the
            # process is in an uninterruptible kernel wait (e.g. stuck
            # on an NFS mount). Nothing more we can do — fall through
            # to ``communicate()`` to drain whatever pipes are open.
            pass

    # ``communicate()`` after a kill is safe and gathers any pending
    # output that buffered before the signal landed.
    try:
        stdout, stderr = proc.communicate(timeout=_KILL_REAP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        stdout, stderr = "", ""

    return _BashRunResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout or "",
        stderr=stderr or "",
        interrupted=interrupted,
        timed_out=timed_out,
    )

_HARDCODED_DANGEROUS_PATTERNS = [
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\b\s+if=", re.IGNORECASE),
    re.compile(r"\brm\b.*\s+-rf\s+/\s*$", re.IGNORECASE),
    re.compile(r"\brm\b.*\s+-rf\s+/\s+"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.IGNORECASE),
]

from ...build_tool import SearchOrReadResult, Tool, ValidationResult, build_tool
from ...context import ToolContext
from ...errors import ToolInputError, ToolPermissionError
from ...protocol import ToolResult
from src.permissions.bash_security import analyze_bash_command
from src.permissions.types import PermissionPassthroughResult, PermissionResult
from src.utils.format import format_duration

from .background import spawn_background_bash
from .command_semantics import interpret_command_result
from .prompt import get_bash_prompt, get_default_timeout_ms, get_max_timeout_ms
from .read_only_validation import is_command_read_only
from .search_classification import (
    SearchOrReadResult as _SearchOrRead,
    is_search_or_read_command,
    is_silent_command,
)
from .sleep_detection import detect_blocked_sleep_pattern
from .utils import strip_empty_lines, strip_leading_blank_lines, truncate_output

BASH_TOOL_NAME = "Bash"

TOOL_SUMMARY_MAX_LENGTH = 80


def _try_extract_cd(command: str) -> Path | None:
    """Return the target only for a standalone ``cd <path>`` command.

    Compound commands must run in the shell.  Treating
    ``cd /work && make`` as a pure directory change silently discards
    everything after the path.
    """
    stripped = command.strip()
    if not stripped.startswith("cd "):
        return None
    try:
        parts = shlex.split(stripped, posix=True)
    except ValueError:
        return None
    if len(parts) == 2 and parts[0] == "cd":
        return Path(parts[1])
    return None


def _bash_check_permissions(
    tool_input: dict[str, Any],
    context: ToolContext,
) -> PermissionResult:
    """Bash's own permission stage — TS-parity rewrite.

    Mirrors the original's per-command pipeline (bashPermissions.ts): deny/ask
    RULES run upstream in ``has_permissions_to_use_tool_inner`` (compound-aware
    and normalization-hardened) before this is called; here we resolve:

    1. STRUCTURAL refusals only — a command the parser can't statically
       analyze (control flow, unparseable quoting) or one hiding executable
       substitution asks with NO suggestions (TS too-complex/injection asks,
       ``suggestions: []``), except that a raw exact-string allow rule is
       honored first (TS checkEarlyExitDeny exact-allow: the user consciously
       saved that literal command).
    2. acceptEdits mode — filesystem-write commands (mkdir/touch/rm/rmdir/
       mv/cp/sed; rm/rmdir still gated on critical paths) and redirect-free
       read-only commands auto-allow (TS modeValidation.ts).
    3. Read-only auto-allow — a provably read-only, in-roots command runs
       with NO prompt and NO rule in any mode (TS bashPermissions.ts:1136
       "Read-only command is allowed").
    4. Everything else → passthrough → the generic prompt, which now carries
       the "don't ask again" suggestion ladder.

    The old class-based screen (dangerous/destructive/unknown → un-grantable
    safety ask that also preempted allow rules) is gone — the original has no
    such screen; its dialog shows a warning for destructive commands instead
    (forwarded via the ``warning`` field on the can_use_tool request). The
    classifier still consumes ``analyze_bash_command`` via auto mode, and the
    hardcoded dangerous patterns + sandbox hard-gate still refuse at spawn
    (``bash_command_safety_guard``).
    """
    command = (tool_input or {}).get("command", "")
    if not command:
        return PermissionPassthroughResult()

    from src.permissions.bash_mode_validation import check_accept_edits_bash
    from src.permissions.bash_suggestions import (
        contains_executable_substitution,
    )
    from src.permissions.read_only_commands import (
        check_read_only_constraints,
    )
    from src.permissions.types import (
        PermissionAllowDecision,
        PermissionAskDecision,
        SafetyCheckDecisionReason,
    )

    cwd_str = str(context.cwd) if getattr(context, "cwd", None) else _os_mod.getcwd()
    perm_ctx = getattr(context, "permission_context", None)

    # bypassPermissions (and plan+bypass-available) runs everything — the
    # tool's own structural/write asks must not preempt it (they are
    # SafetyCheck asks, which otherwise short-circuit before the bypass branch
    # in has_permissions_to_use_tool_inner). Deny rules still apply upstream.
    _mode = getattr(perm_ctx, "mode", None)
    if _mode == "bypassPermissions" or (
        _mode == "plan"
        and getattr(perm_ctx, "is_bypass_permissions_mode_available", False)
    ):
        return PermissionPassthroughResult()

    allowed_roots: list[str] = []
    try:
        allowed_roots = [str(r) for r in context.allowed_roots()]
    except Exception:
        allowed_roots = []
    if not allowed_roots:
        allowed_roots = [cwd_str]

    # 1. Structural refusals (parser gives up / hidden substitution /
    #    eval-like builtins whose arguments ARE code). Two TS analogs with
    #    DIFFERENT exact-allow handling:
    #    * PARSE refusals (AST too-complex / injection substitution) go through
    #      checkEarlyExitDeny, which honors an exact-string ALLOW rule
    #      (bashPermissions.ts:2124-2131) — the user saved that literal command.
    #    * checkSemantics refusals (EVAL_LIKE_BUILTINS, NAME-eval subscript)
    #      go through checkSemanticsDeny, which only honors DENY rules, NEVER an
    #      allow — so an exact ``Bash(eval "…")`` rule must NOT run eval.
    #    All ask with empty suggestions.
    from src.permissions.read_only_commands import (
        accesses_proc_environ,
        find_eval_like_builtin,
        find_name_eval_subscript_attack,
    )

    analysis = analyze_bash_command(command)
    parse_reason: str | None = None      # exact-allow honored
    semantics_reason: str | None = None  # exact-allow NOT honored
    if analysis.is_complex:
        parse_reason = f"Complex command: {analysis.reason}"
    elif contains_executable_substitution(command):
        parse_reason = "Command contains substitution that executes hidden commands"
    else:
        eval_like = find_eval_like_builtin(command)
        subscript = find_name_eval_subscript_attack(command)
        if eval_like is not None:
            semantics_reason = (
                f"`{eval_like}` executes its arguments as shell code, which "
                "cannot be statically analyzed"
            )
        elif subscript is not None:
            semantics_reason = (
                f"`{subscript}` arithmetically evaluates an array subscript, "
                "which can execute a command substitution even when quoted"
            )
        elif accesses_proc_environ(command):
            semantics_reason = (
                "Accesses /proc/*/environ, which may expose environment "
                "secrets of another process"
            )
    if parse_reason is not None:
        exact_rule = _exact_allow_rule(perm_ctx, command)
        if exact_rule is not None:
            from src.permissions.types import RuleDecisionReason

            return PermissionAllowDecision(
                behavior="allow",
                updated_input=tool_input,
                decision_reason=RuleDecisionReason(rule=exact_rule),
            )
    if parse_reason is not None or semantics_reason is not None:
        return PermissionAskDecision(
            behavior="ask",
            message=(
                "This command requires confirmation: "
                f"{parse_reason or semantics_reason}"
            ),
            decision_reason=SafetyCheckDecisionReason(
                reason=parse_reason or semantics_reason,
                classifier_approvable=True,
            ),
            suggestions=(),  # never suggest saving an unanalyzable command
        )

    # 2. acceptEdits mode: filesystem writes + read-only commands auto-allow.
    if perm_ctx is not None and getattr(perm_ctx, "mode", None) == "acceptEdits":
        accept_edits = check_accept_edits_bash(
            command, cwd=cwd_str, allowed_roots=allowed_roots
        )
        if accept_edits is True:
            from src.permissions.types import ModeDecisionReason

            return PermissionAllowDecision(
                behavior="allow",
                updated_input=tool_input,
                decision_reason=ModeDecisionReason(mode="acceptEdits"),
            )
        if isinstance(accept_edits, PermissionAskDecision):
            return accept_edits  # dangerous rm/rmdir target — always surface

    # 2.5. A filesystem-write command aimed at a DANGEROUS-removal target
    # (`/`, `~`, a direct child of `/`) or OUTSIDE the workspace can never be
    # auto-allowed — not by acceptEdits, not by a Bash(rm:*) rule. Surface a
    # SafetyCheck ask with NO suggestions (mirrors TS checkPathConstraints /
    # checkDangerousRemovalPaths, which "cannot be auto-allowed by permission
    # rules" and never suggests saving the command). Returning it here — as a
    # SafetyCheck ask — makes it preempt the content-rule allow in check.py
    # (the safetyCheck coercion runs before rule matching), so a saved
    # ``Bash(rm:*)`` still can't reach ``rm -rf ~``.
    from src.permissions.bash_mode_validation import (
        rule_allow_path_gate,
        ACCEPT_EDITS_WRITE_COMMANDS,
    )
    from src.permissions.bash_suggestions import (
        contains_unquoted_chaining,
        split_chained_command,
    )

    write_legs = [command]
    if contains_unquoted_chaining(command):
        _subs = split_chained_command(command)
        write_legs = _subs if _subs is not None else [command]
    for _leg in write_legs:
        _tok = _leg.strip().split(None, 1)[0] if _leg.strip() else ""
        _base = _os_mod.path.basename(_tok)
        if _base in ACCEPT_EDITS_WRITE_COMMANDS and not rule_allow_path_gate(
            _leg, cwd=cwd_str, allowed_roots=allowed_roots
        ):
            return PermissionAskDecision(
                behavior="ask",
                message=(
                    f"This command targets a protected or out-of-workspace "
                    f"path and requires confirmation: {_leg.strip()}"
                ),
                decision_reason=SafetyCheckDecisionReason(
                    reason="Write to a dangerous or out-of-workspace path",
                    classifier_approvable=False,
                ),
                suggestions=(),
            )

    # 3. Read-only auto-allow (no rule, no prompt — any mode).
    if check_read_only_constraints(
        command, cwd=cwd_str, allowed_roots=allowed_roots
    ):
        from src.permissions.types import OtherDecisionReason

        return PermissionAllowDecision(
            behavior="allow",
            updated_input=tool_input,
            decision_reason=OtherDecisionReason(
                reason="Read-only command is allowed",
            ),
        )

    # 4. No verdict here — rules and the prompt flow decide.
    return PermissionPassthroughResult()


def _exact_allow_rule(perm_ctx: Any, command: str) -> Any | None:
    """Raw exact-string allow rule for ``command``, or None.

    TS honors an exact-match allow even for commands its analyzers refuse to
    reason about (bashPermissions.ts:2124-2131) — the user saved that literal
    string on purpose. Only full-string equality qualifies.
    """
    if perm_ctx is None:
        return None
    try:
        from src.permissions.rules import get_rule_by_contents_for_tool

        stripped = command.strip()
        for rule_content, rule in get_rule_by_contents_for_tool(
            perm_ctx, "Bash", "allow"
        ).items():
            if stripped and stripped == str(rule_content).strip():
                return rule
    except Exception:
        return None
    return None


def _bash_validate_input(
    tool_input: dict[str, Any],
    context: ToolContext,
) -> ValidationResult:
    command = (tool_input or {}).get("command", "")
    sleep_pattern = detect_blocked_sleep_pattern(command)
    if sleep_pattern is not None:
        return ValidationResult.fail(
            f"Blocked: {sleep_pattern}. Run blocking commands in the background "
            "with run_in_background: true -- you'll get a completion notification "
            "when done. If you genuinely need a short delay (rate limiting, "
            "deliberate pacing), keep it at 5 seconds or less.",
            error_code=10,
        )
    return ValidationResult.ok()


def bash_command_safety_guard(command: str) -> None:
    """Pre-spawn safety for any shell command run through the bash machinery:
    the hardcoded-dangerous-pattern block + the C8 sandbox hard-gate.

    Shared by ``_bash_call`` (foreground + run_in_background) AND the Monitor
    tool, which spawns via ``spawn_background_bash`` directly — so Monitor
    can't be a way around these guards (critic C5-P2). Raises
    ``ToolPermissionError`` to refuse; the sandbox check is best-effort (a
    settings problem must not crash the tool), but a hard-gate refusal always
    propagates."""
    for pat in _HARDCODED_DANGEROUS_PATTERNS:
        if pat.search(command):
            raise ToolPermissionError("refusing to run potentially dangerous command")

    # Sandbox guard (C8): the port has no sandbox ENFORCEMENT, so a
    # ``sandbox.enabled`` setting maps onto TS's documented sandbox-unavailable
    # path (sandboxTypes.ts:96-103). failIfUnavailable → REFUSE (the
    # managed-settings hard gate: never silently run unsandboxed); otherwise
    # warn once and proceed unsandboxed.
    try:
        from src.permissions.sandbox_guard import (
            sandbox_hard_gate_error,
            warn_if_unsandboxed_once,
        )
        from src.settings.settings import get_settings

        _settings = get_settings()
        _gate = sandbox_hard_gate_error(_settings)
        if _gate:
            raise ToolPermissionError(_gate)
        warn_if_unsandboxed_once(_settings)
    except ToolPermissionError:
        raise
    except Exception:  # noqa: BLE001 — the guard must never break the tool
        import logging as _logging
        _logging.getLogger(__name__).debug(
            "[sandbox] guard check failed", exc_info=True
        )


def _bash_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    command = tool_input["command"]
    if not isinstance(command, str) or not command.strip():
        raise ToolInputError("command must be a non-empty string")
    if "\x00" in command:
        raise ToolInputError("command contains NUL byte")

    # Defense-in-depth: block obviously dangerous commands + apply the C8
    # sandbox hard-gate. Shared with the Monitor tool (which spawns via
    # spawn_background_bash directly, bypassing this function) — so the guards
    # can't drift and Monitor can't be a hole around them.
    bash_command_safety_guard(command)

    explicit_cwd = tool_input.get("cwd")
    if explicit_cwd is not None:
        if not isinstance(explicit_cwd, str) or not explicit_cwd.startswith("/"):
            raise ToolInputError("cwd must be an absolute path when provided")
        cwd = context.ensure_allowed_path(explicit_cwd)
    else:
        cwd = context.cwd or context.workspace_root

    # ``run_in_background: true`` detaches the command so the agent can keep
    # coordinating while a long-running job (dev server, build, long test
    # suite, ...) makes progress. Mirrors
    # ``typescript/src/tools/BashTool/BashTool.tsx`` ``spawnBackgroundTask``
    # behaviour: we return immediately with a task id and let the model poll
    # the output via ``TaskOutput``.
    if tool_input.get("run_in_background"):
        bg_output = spawn_background_bash(
            command=command,
            cwd=cwd,
            description=tool_input.get("description"),
            context=context,
        )
        return ToolResult(name=BASH_TOOL_NAME, output=bg_output)

    cd_target = _try_extract_cd(command)
    if (
        cd_target is not None
        and command.strip().startswith("cd ")
        and len(command.strip().splitlines()) == 1
    ):
        if not cd_target.is_absolute():
            next_dir = (cwd / cd_target).expanduser().resolve()
        else:
            next_dir = cd_target.expanduser().resolve()
        next_dir = context.ensure_allowed_path(next_dir)
        if not next_dir.exists() or not next_dir.is_dir():
            return ToolResult(
                name=BASH_TOOL_NAME,
                output={"error": f"directory does not exist: {next_dir}"},
                is_error=True,
            )
        context.cwd = next_dir
        return ToolResult(
            name=BASH_TOOL_NAME,
            output={"cwd": str(context.cwd), "stdout": "", "stderr": ""},
        )

    # Resolve timeout: prefer explicit timeout (ms), fall back to timeout_s (legacy), then default
    timeout_ms = tool_input.get("timeout")
    if timeout_ms is not None:
        max_ms = get_max_timeout_ms()
        if not isinstance(timeout_ms, (int, float)) or timeout_ms < 1000:
            raise ToolInputError("timeout must be at least 1000 ms")
        if timeout_ms > max_ms:
            raise ToolInputError(f"timeout must not exceed {max_ms} ms")
        timeout_s = int(timeout_ms / 1000)
    else:
        timeout_s = tool_input.get("timeout_s")
        if timeout_s is None:
            timeout_s = int(get_default_timeout_ms() / 1000)
        if not isinstance(timeout_s, int) or timeout_s < 1 or timeout_s > 600:
            raise ToolInputError("timeout_s must be an integer between 1 and 600")

    # Persist cwd across invocations (port of ``typescript/src/utils/Shell.ts``,
    # which writes PWD to ``cwdFilePath`` after every command and calls
    # ``setCwdState()``). We wrap the user's command so that a trailing ``pwd``
    # writes the shell's final directory into a tempfile, and read it back to
    # update ``context.cwd``. This way ``cd demos && ls`` (compound) or a
    # ``pushd`` inside a script correctly moves the persistent CWD forward
    # instead of being discarded with the subprocess.
    import os as _os
    import tempfile as _tempfile

    cwd_fd, cwd_path = _tempfile.mkstemp(prefix="clawcodex-bash-cwd-", suffix=".txt")
    _os.close(cwd_fd)
    try:
        wrapped = f"{{ {command}\n}}; __rc=$?; pwd > {shlex.quote(cwd_path)} 2>/dev/null; exit $__rc"
        # Spawn bash in its own session/process group so we can kill the
        # whole subtree (e.g. ``find /`` that itself forks helpers) when
        # ESC fires. Mirrors TS ``ShellCommand`` (typescript/src/utils/
        # ShellCommand.ts:187-192,345) which uses ``tree-kill`` for the
        # same reason. ``start_new_session=True`` is ``setsid()`` on
        # POSIX; on Windows it falls back to a process group via
        # ``CREATE_NEW_PROCESS_GROUP``.
        run_result = _run_bash_with_abort(
            ["bash", "-lc", wrapped],
            cwd=str(cwd),
            timeout_s=timeout_s,
            abort_signal=_get_abort_signal(context),
        )

        if run_result.interrupted:
            # ESC-abort path. Mirrors TS ``BashTool.tsx:610-630`` where
            # ``interrupted=true`` triggers the ``<error>Command was
            # aborted before completion</error>`` appendage and sets
            # ``is_error=true`` on the API block.
            return ToolResult(
                name=BASH_TOOL_NAME,
                output={
                    "cwd": str(cwd),
                    "exit_code": -1,
                    "stdout": truncate_output(run_result.stdout or ""),
                    "stderr": truncate_output(run_result.stderr or ""),
                    "interrupted": True,
                },
                is_error=True,
            )

        if run_result.timed_out:
            # Timeout path. Mirrors TS ``ShellCommand.ts:323-328``:
            # prepend the duration marker to whatever stderr was
            # captured, leave ``interrupted=false``, and let
            # ``_bash_map_result_to_api`` emit ``is_error=false`` — the
            # duration string in stderr is the model-facing signal, the
            # ``<error>`` tag is reserved for user-initiated abort.
            # Without this split the timeout case looked identical to
            # ESC and the model retried timed-out commands on resume.
            #
            # Formatting parity:
            #   * ``format_duration`` is the Python port of TS
            #     ``formatDuration`` (src/utils/format.py mirrors
            #     typescript/src/utils/format.ts:34-95). It produces
            #     ``30s`` / ``2m 0s`` / ``1h 5m 12s`` — exactly the
            #     string TS embeds in the marker. ``timeout_s * 1000``
            #     converts to ms because ``format_duration`` and the
            #     TS reference both take ms.
            #   * ``prepend_stderr`` mirrors TS ``ShellCommand.ts:56-58``
            #     — a SINGLE SPACE separator (not newline), and the
            #     existing stderr is passed through unmodified so a
            #     trailing newline or leading whitespace the model
            #     might use as a delimiter survives the prepend.
            #   * ``exit_code = 143`` mirrors TS ``ShellCommand.ts:50``
            #     (``SIGTERM = 143``) — the label for the timeout path,
            #     decoupled from whatever signal Popen actually reports
            #     (which would be ``-9`` on Unix after our SIGKILL).
            existing_stderr = run_result.stderr or ""
            timeout_marker = (
                f"Command timed out after {format_duration(timeout_s * 1000)}"
            )
            stderr_with_marker = (
                f"{timeout_marker} {existing_stderr}"
                if existing_stderr
                else timeout_marker
            )
            return ToolResult(
                name=BASH_TOOL_NAME,
                output={
                    "cwd": str(cwd),
                    "exit_code": 143,
                    "stdout": truncate_output(run_result.stdout or ""),
                    "stderr": truncate_output(stderr_with_marker),
                    "timed_out": True,
                },
                is_error=False,
            )

        completed_returncode = run_result.returncode
        completed_stdout = run_result.stdout or ""
        completed_stderr = run_result.stderr or ""

        # If the command succeeded in changing directory, promote the new cwd
        # into the shared ToolContext so follow-up Bash invocations start
        # there. Errors (e.g. command exited mid-flight before ``pwd`` ran)
        # fall through quietly — we keep the prior cwd.
        try:
            with open(cwd_path, "r", encoding="utf-8") as handle:
                final_cwd_text = handle.read().strip()
        except OSError:
            final_cwd_text = ""
    finally:
        try:
            _os.unlink(cwd_path)
        except OSError:
            pass

    if final_cwd_text:
        try:
            new_cwd = context.ensure_allowed_path(final_cwd_text)
            if new_cwd.exists() and new_cwd.is_dir():
                context.cwd = new_cwd
                cwd = new_cwd
        except ToolPermissionError:
            # cd'd outside the allowed roots — don't track it but don't fail
            # the call either (matches the TS behavior where the process can
            # roam freely but the UI cwd clamps to the workspace).
            pass

    stdout = truncate_output(completed_stdout)
    stderr = truncate_output(completed_stderr)

    interpretation = interpret_command_result(
        command, completed_returncode, completed_stdout, completed_stderr,
    )

    output: dict[str, Any] = {
        "cwd": str(cwd),
        "exit_code": completed_returncode,
        "stdout": stdout,
        "stderr": stderr,
    }

    # Detect data-URI image output (e.g. matplotlib.savefig printed to stdout).
    # Use the un-truncated stdout to avoid splitting a base64 string mid-stream.
    # Matches TS BashTool isImage flag set in mapToolResultToToolResultBlockParam.
    from .image_output import is_image_output as _is_image_output
    if completed_stdout and _is_image_output(completed_stdout):
        output["isImage"] = True
        # Keep the raw data URI in stdout so the mapper can build the image block.
        output["stdout"] = completed_stdout.strip()

    if interpretation.message:
        output["returnCodeInterpretation"] = interpretation.message
    if is_silent_command(command):
        output["noOutputExpected"] = True

    # /eco: compress the model-bound rendering. The raw stdout/stderr stay in
    # the output dict (and, for lossy filters, in the per-session tee file);
    # note the TUI transcript renders the mapped content, so the user sees
    # the same compact string the model does. Runs on the full pre-truncation
    # output so the tee recovery file is complete.
    _maybe_apply_eco(
        output,
        command=command,
        exit_code=completed_returncode,
        full_stdout=completed_stdout,
        full_stderr=completed_stderr,
        context=context,
    )

    return ToolResult(
        name=BASH_TOOL_NAME,
        output=output,
        is_error=interpretation.is_error,
    )


def _assemble_bash_body(stdout: str, stderr: str) -> str:
    """The stdout+stderr part of the model-bound content (no interrupt
    marker, no returnCodeInterpretation). Factored out so the eco engine's
    never-worse guard compares against exactly what the mapper would emit."""
    processed_stdout = strip_leading_blank_lines(stdout).rstrip() if stdout else ""
    parts: list[str] = []
    if processed_stdout:
        parts.append(processed_stdout)
    if stderr and stderr.strip():
        parts.append(stderr.strip())
    return "\n".join(parts)


def _eco_tee_dir(context: ToolContext) -> Path | None:
    """Per-session directory for eco raw-recovery files, co-located with the
    Step-11 ``tool-results`` persistence dir (``.../<session>/eco/``)."""
    try:
        from src.services.tool_execution.tool_result_persistence import (
            resolve_tool_results_dir,
        )

        return resolve_tool_results_dir(context).parent / "eco"
    except Exception:  # noqa: BLE001 — recovery dir is best-effort
        return None


def _maybe_apply_eco(
    output: dict[str, Any],
    *,
    command: str,
    exit_code: int,
    full_stdout: str,
    full_stderr: str,
    context: ToolContext,
) -> None:
    """When ``/eco`` is on, attach a compressed model-bound rendering.

    Sets ``output["ecoContent"]`` (consumed by ``_bash_map_result_to_api``)
    plus ``ecoFilter``/``ecoSavedTokens`` metadata. Never raises; never
    touches the raw stdout/stderr fields, exit code, or error semantics.
    (The TUI renders the mapped content, so the compact rendering is also
    what the user sees in the transcript.) Skips image output (the data URI
    must reach the image mapper intact). The interrupted/timeout/background/
    cd paths return before this is called.
    """
    try:
        from src.eco import is_eco_session

        if not is_eco_session():
            return
        if output.get("isImage"):
            return
        from src.eco.engine import compress_bash_output

        baseline = _assemble_bash_body(
            output.get("stdout", ""), output.get("stderr", "")
        )
        if not baseline.strip():
            return
        outcome = compress_bash_output(
            command=command,
            exit_code=exit_code,
            full_text=_assemble_bash_body(full_stdout, full_stderr),
            baseline=baseline,
            tee_dir=_eco_tee_dir(context),
        )
        if outcome is not None:
            output["ecoContent"] = outcome.content
            output["ecoFilter"] = outcome.filter_name
            output["ecoSavedTokens"] = outcome.saved_tokens
    except Exception:  # noqa: BLE001 — eco must never break the Bash tool
        import logging as _logging

        _logging.getLogger(__name__).debug("[eco] apply failed", exc_info=True)


def _bash_map_result_to_api(output: Any, tool_use_id: str) -> dict[str, Any]:
    if isinstance(output, dict):
        # ``run_in_background: true`` responses carry a task id + a canned
        # message instead of stdout/stderr -- hand it through verbatim so the
        # model sees something actionable.
        if output.get("backgroundTaskId") and not output.get("stdout"):
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": output.get("message", "")
                or f"Background task started: {output['backgroundTaskId']}",
            }
        # Image data-URI output: surface as an image content block so the
        # model sees the rendered image (matplotlib, mermaid, etc.) instead
        # of base64 noise. Mirrors TS BashTool/utils.ts buildImageToolResult.
        if output.get("isImage"):
            from .image_output import build_image_tool_result as _build_img
            blocks = _build_img(output.get("stdout", ""))
            if blocks is not None:
                return {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": blocks,
                }
        stdout = output.get("stdout", "")
        stderr = output.get("stderr", "")
        interpretation = output.get("returnCodeInterpretation")
        interrupted = output.get("interrupted", False)

        # /eco: a compressed rendering replaces the stdout+stderr assembly on
        # the wire only — the display fields stay raw. Interrupt marker and
        # returnCodeInterpretation append after it either way. (eco is never
        # set on interrupted results, so the joined string is byte-identical
        # to the historical assembly whenever ecoContent is absent.)
        eco_content = output.get("ecoContent")
        if isinstance(eco_content, str) and eco_content.strip():
            body = eco_content
        else:
            body = _assemble_bash_body(stdout, stderr)

        parts: list[str] = []
        if body:
            parts.append(body)
        if interrupted:
            parts.append("<error>Command was aborted before completion</error>")

        if interpretation:
            parts.append(interpretation)

        content = "\n".join(parts) if parts else ""

        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
            "is_error": interrupted,
        }

    if isinstance(output, str):
        content_val: str | list[dict[str, Any]] = output
    else:
        content_val = json.dumps(output) if output else ""

    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content_val,
    }


def _bash_prompt_fn() -> str:
    return get_bash_prompt()


def _bash_search_or_read(input_data: dict) -> SearchOrReadResult:
    cmd = (input_data or {}).get("command", "")
    result = is_search_or_read_command(cmd)
    return SearchOrReadResult(
        is_search=result.is_search,
        is_read=result.is_read,
        is_list=result.is_list,
    )


def _bash_classifier_input(input_data: dict) -> str:
    return (input_data or {}).get("command", "")


def _bash_activity(input_data: dict | None) -> str | None:
    if not input_data:
        return "Running command"
    cmd = input_data.get("command", "")
    desc = input_data.get("description")
    if desc:
        return f"Running {desc}"
    return f"Running {cmd[:60]}" if cmd else "Running command"


def _bash_user_facing_name(input_data: dict | None) -> str:
    if not input_data:
        return "Bash"
    return f"Bash: {(input_data.get('command', '') or '')[:50]}" if input_data else "Bash"


def _bash_tool_use_summary(input_data: dict | None) -> str | None:
    if not input_data:
        return None
    desc = input_data.get("description")
    if desc:
        return desc[:TOOL_SUMMARY_MAX_LENGTH]
    cmd = input_data.get("command", "")
    return cmd[:TOOL_SUMMARY_MAX_LENGTH] if cmd else None


BashTool: Tool = build_tool(
    name=BASH_TOOL_NAME,
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "command": {
                "type": "string",
                "description": "The command to execute",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory (absolute path)",
            },
            "timeout_s": {
                "type": "integer",
                "description": "Timeout in seconds (1-600)",
            },
            "timeout": {
                "type": "integer",
                "description": "Optional timeout in milliseconds",
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command does in active voice."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Set to true to run this command in the background.",
            },
        },
        "required": ["command"],
    },
    call=_bash_call,
    prompt=_bash_prompt_fn,
    description="Execute a shell command.",
    max_result_size_chars=30_000,
    map_result_to_api=_bash_map_result_to_api,
    check_permissions=_bash_check_permissions,
    validate_input=_bash_validate_input,
    is_read_only=lambda _input: is_command_read_only((_input or {}).get("command", "")),
    is_concurrency_safe=lambda _input: is_command_read_only((_input or {}).get("command", "")),
    is_destructive=lambda _input: not is_command_read_only((_input or {}).get("command", "")),
    user_facing_name=_bash_user_facing_name,
    search_hint="shell terminal execute run command",
    to_auto_classifier_input=_bash_classifier_input,
    is_search_or_read_command=_bash_search_or_read,
    get_activity_description=_bash_activity,
    get_tool_use_summary=_bash_tool_use_summary,
)
