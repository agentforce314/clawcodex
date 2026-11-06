from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

BLOCK_EXIT = 2
TIMEOUT_SECONDS = 10.0

def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Do not let a later JSON member override an earlier Guard stop."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key in Guard verdict")
        result[key] = value
    return result



def _command_context(payload: Any) -> tuple[str, str | None] | None:
    if not isinstance(payload, dict):
        return None
    if payload.get("hook_event") != "PreToolUse":
        return None
    if payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    cwd = tool_input.get("cwd")
    # A relative Bash cwd can resolve differently for the hook and the tool.
    # Reject it instead of classifying a command in the wrong directory.
    if cwd is not None and (
        not isinstance(cwd, str) or not cwd.strip() or not os.path.isabs(cwd)
    ):
        return None
    return command, cwd


def _worktree_root(cwd: str) -> str:
    """Find the outer Git worktree root without executing project code."""
    start = os.path.realpath(cwd)
    current = start
    root = start
    while True:
        # A nested directory can contain a project-controlled fake .git file.
        # Use the outermost repository marker, not the first one found.
        # Linked worktrees use a .git file rather than a directory.
        if os.path.lexists(os.path.join(current, ".git")):
            root = current
        parent = os.path.dirname(current)
        if parent == current:
            return root
        current = parent


def _guard_allows(command: str, cwd: str | None = None) -> tuple[bool, str]:
    # When Bash supplies a different working directory, resolve the Guard
    # executable first. Relative PATH entries must not select a project-local
    # binary after subprocess switches into that directory.
    executable = "hol-guard"
    # When Bash has no cwd override, the hook resolves a bare executable
    # using PATH. Relative (including empty) entries can select a
    # project-controlled hol-guard shim that forges an allow verdict.
    # With an explicit cwd, the branch below freezes the executable path.
    if cwd is None and any(
        not os.path.isabs(entry)
        for entry in os.environ.get("PATH", os.defpath).split(os.pathsep)
    ):
        return False, "guard_untrusted_executable"
    # Bind the CLI executable to a verified absolute path even when Bash has
    # no explicit cwd. An absolute project-owned PATH directory can otherwise
    # forge Guard's allow verdict in the current working directory.
    resolved = shutil.which(executable)
    if not resolved:
        return False, "guard_unavailable"
    selected_executable = os.path.abspath(resolved)
    executable = os.path.realpath(selected_executable)
    # Check both the hook launch checkout and Bash's eventual checkout.
    # An explicit Bash cwd can point elsewhere while PATH still selects a
    # repository-controlled Guard shim from the original launch checkout.
    # Check the lookup alias as well as its resolved symlink target.
    worktrees = {_worktree_root(os.getcwd()), _worktree_root(cwd or os.getcwd())}
    try:
        if any(
            os.path.commonpath((candidate, worktree)) == worktree
            for worktree in worktrees
            for candidate in (selected_executable, executable)
        ):
            return False, "guard_untrusted_executable"
    except ValueError:
        return False, "guard_untrusted_executable"
    try:
        result = subprocess.run(
            [executable, "command", "test", "--json", "--", command],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            cwd=cwd,
        )
    except FileNotFoundError:
        return False, "guard_unavailable"
    except subprocess.TimeoutExpired:
        return False, "guard_timeout"
    except UnicodeError:
        return False, "guard_invalid_output"
    except (OSError, ValueError):
        # Invalid argv (for example, a NUL in a command) must not fail open.
        return False, "guard_error"

    if result.returncode != 0:
        return False, "guard_error"

    try:
        payload = json.loads(result.stdout, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return False, "guard_invalid_output"

    if not isinstance(payload, dict):
        return False, "guard_invalid_output"
    classification = payload.get("classification")
    if not isinstance(classification, dict):
        return False, "guard_invalid_output"

    # Command test reports a classification, not Guard's final policy.
    # Do not allow an explicitly stopped response to be overridden by it.
    decision = payload.get("decision")
    if ("continue" in payload and payload["continue"] is not True) or (
        "decision" in payload
        and (not isinstance(decision, str) or decision.strip().lower() != "allow")
    ):
        return False, "guard_block"
    if "policy_action" in payload and payload["policy_action"] not in ("allow", "not_run"):
        return False, "guard_block"
    hook_output = payload.get("hookSpecificOutput")
    if "hookSpecificOutput" in payload and (
        not isinstance(hook_output, dict)
        or hook_output.get("permissionDecision") != "allow"
        or hook_output.get("hookEventName") != "PreToolUse"
    ):
        return False, "guard_block"

    if classification.get("explicitly_benign") is True and payload.get("minimum_action") == "allow":
        return True, "guard_allow"
    return False, "guard_block"


def evaluate(payload: Any) -> tuple[bool, str]:
    context = _command_context(payload)
    if context is None:
        return False, "guard_invalid_input"
    command, cwd = context
    return _guard_allows(command, cwd)


def main() -> int:
    try:
        payload = json.load(sys.stdin, object_pairs_hook=_reject_duplicate_keys)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError, UnicodeError, OSError):
        print("guard_invalid_input", file=sys.stderr)
        return BLOCK_EXIT
    except Exception:
        # Unknown parser failures must also block: hook runner exit 1 is nonblocking.
        print("guard_invalid_input", file=sys.stderr)
        return BLOCK_EXIT

    try:
        allowed, reason = evaluate(payload)
    except Exception:
        # The hook runner treats exit status 1 as nonblocking. Fail closed if
        # an unexpected evaluation error escapes normal Guard error handling.
        print("guard_error", file=sys.stderr)
        return BLOCK_EXIT
    if allowed:
        return 0
    print(reason, file=sys.stderr)
    return BLOCK_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
