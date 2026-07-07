from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .types import (
    OtherDecisionReason,
    PermissionAllowDecision,
    PermissionAskDecision,
    PermissionPassthroughResult,
    PermissionResult,
    SafetyCheckDecisionReason,
    WorkingDirDecisionReason,
)

# TS parity (typescript/src/utils/permissions/filesystem.ts:59-83). The gate
# these lists feed (``check_path_safety_for_auto_edit``) only runs on the
# acceptEdits/auto fast-paths, and only AFTER the working-roots containment
# check — out-of-workspace paths (~/.ssh, ~/.aws, …) never reach it. A
# previous revision padded the lists far beyond the original (lockfiles,
# .env*, Makefile, .npmrc/.netrc/ssh/kube/aws entries, .ssh/.gnupg/.config
# dirs), which meant a user who explicitly opted into "allow all edits during
# this session" still got prompted for everyday in-repo files the original
# auto-accepts. Trimmed back to the original's sets, plus this port's own
# config namespace (``.clawcodex``) in the ``.openclaude`` slot.
DANGEROUS_FILES: tuple[str, ...] = (
    ".gitconfig",
    ".gitmodules",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".ripgreprc",
    ".mcp.json",
    ".claude.json",
    ".clawcodex.json",
)

DANGEROUS_DIRECTORIES: tuple[str, ...] = (
    ".git",
    ".vscode",
    ".idea",
    ".claude",
    ".clawcodex",
)

PROTECTED_LOCKFILES: tuple[str, ...] = (
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Pipfile.lock",
    "poetry.lock",
    "Cargo.lock",
    "Gemfile.lock",
    "composer.lock",
    "go.sum",
    "flake.lock",
)

PROTECTED_GENERATED_DIRS: tuple[str, ...] = (
    "node_modules",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    "vendor",
)

ENV_FILE_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".env.staging",
)


def normalize_case_for_comparison(path: str) -> str:
    return path.lower()


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _get_relative_parts(file_path: str, cwd: str) -> list[str]:
    try:
        rel = os.path.relpath(file_path, cwd)
        return rel.replace("\\", "/").split("/")
    except ValueError:
        return file_path.replace("\\", "/").split("/")


def resolve_path(file_path: str) -> str:
    expanded = os.path.expanduser(file_path)
    abs_path = os.path.abspath(expanded)
    try:
        resolved = str(Path(abs_path).resolve())
    except OSError:
        resolved = abs_path
    return resolved


def is_env_file(basename: str) -> bool:
    lower = basename.lower()
    if lower in [p.lower() for p in ENV_FILE_PATTERNS]:
        return True
    if lower.startswith(".env."):
        return True
    return False


def is_lockfile(basename: str) -> bool:
    return basename in PROTECTED_LOCKFILES


def is_in_generated_dir(file_path: str, cwd: str | None = None) -> bool:
    parts = _get_relative_parts(file_path, cwd or os.getcwd())
    for part in parts:
        if part in PROTECTED_GENERATED_DIRS:
            return True
    return False


# Structural carve-out (TS filesystem.ts:474-486): ``.claude/worktrees/`` (and
# this port's ``.clawcodex/worktrees/``) is where git worktrees live — a
# ``.claude`` segment immediately followed by ``worktrees`` is infrastructure,
# not a user-created dangerous directory. Without the carve-out, EVERY file in
# a worktree session (…/.claude/worktrees/<name>/src/x.py) matched the
# protected-directory gate and acceptEdits was completely defeated there.
_WORKTREE_CARVEOUT_DIRS: frozenset[str] = frozenset({".claude", ".clawcodex"})


def check_path_safety_for_auto_edit(
    file_path: str,
    cwd: str | None = None,
) -> PermissionResult | None:
    abs_path = resolve_path(file_path)
    segments = abs_path.replace("\\", "/").split("/")

    for i, segment in enumerate(segments):
        seg_lower = normalize_case_for_comparison(segment)
        for dangerous_dir in DANGEROUS_DIRECTORIES:
            if seg_lower != normalize_case_for_comparison(dangerous_dir):
                continue
            # (TS scans every segment including the last — a target path
            # ending in ``.claude``/``.git`` is itself flagged.)
            if dangerous_dir in _WORKTREE_CARVEOUT_DIRS:
                nxt = segments[i + 1] if i + 1 < len(segments) else ""
                if normalize_case_for_comparison(nxt) == "worktrees":
                    continue  # structural worktree path — keep scanning
            return PermissionAskDecision(
                behavior="ask",
                message=f"This file is inside a protected directory ({dangerous_dir}/) and requires confirmation.",
                decision_reason=SafetyCheckDecisionReason(
                    reason=f"File is inside protected directory: {dangerous_dir}/",
                    classifier_approvable=True,
                ),
            )

    basename = os.path.basename(abs_path)
    basename_lower = normalize_case_for_comparison(basename)

    for dangerous_file in DANGEROUS_FILES:
        if basename_lower == normalize_case_for_comparison(dangerous_file):
            return PermissionAskDecision(
                behavior="ask",
                message=f"Editing {dangerous_file} requires confirmation as it could affect system behavior.",
                decision_reason=SafetyCheckDecisionReason(
                    reason=f"File is a protected configuration file: {dangerous_file}",
                    classifier_approvable=True,
                ),
            )

    return None


def check_read_permission_for_path(
    file_path: str,
    cwd: str | None = None,
    allowed_directories: list[str] | None = None,
) -> PermissionResult | None:
    abs_path = resolve_path(file_path)

    if abs_path.startswith("\\\\") or abs_path.startswith("//"):
        return PermissionAskDecision(
            behavior="ask",
            message="UNC paths are not allowed for security reasons.",
            decision_reason=SafetyCheckDecisionReason(
                reason="UNC path detected",
                classifier_approvable=False,
            ),
        )

    if allowed_directories:
        resolved = Path(abs_path).resolve()
        for allowed_dir in allowed_directories:
            allowed_resolved = Path(allowed_dir).resolve()
            if _is_within(resolved, allowed_resolved):
                return None
        return PermissionPassthroughResult(
            behavior="passthrough",
            message=f"File is outside allowed working directories: {abs_path}",
        )

    return None


def check_write_permission_for_path(
    file_path: str,
    cwd: str | None = None,
    allowed_directories: list[str] | None = None,
) -> PermissionResult | None:
    safety_result = check_path_safety_for_auto_edit(file_path, cwd)
    if safety_result is not None:
        return safety_result

    return check_read_permission_for_path(file_path, cwd, allowed_directories)


def get_write_scope(
    file_path: str,
    cwd: str | None = None,
    additional_directories: list[str] | None = None,
) -> str:
    abs_path = resolve_path(file_path)
    effective_cwd = cwd or os.getcwd()

    try:
        Path(abs_path).relative_to(Path(effective_cwd).resolve())
        return "inside_cwd"
    except ValueError:
        pass

    if additional_directories:
        for d in additional_directories:
            try:
                Path(abs_path).relative_to(Path(d).resolve())
                return "inside_additional"
            except ValueError:
                continue

    return "outside"


def get_scratchpad_dir() -> str:
    tmp = os.environ.get("TMPDIR", "/tmp")
    scratchpad = os.path.join(tmp, "claude-scratchpad")
    os.makedirs(scratchpad, exist_ok=True)
    return scratchpad


def is_in_scratchpad(file_path: str) -> bool:
    try:
        abs_path = resolve_path(file_path)
        scratchpad = get_scratchpad_dir()
        return _is_within(Path(abs_path), Path(scratchpad))
    except OSError:
        return False


def _scratchpad_dir_path() -> str:
    """Scratchpad path *without* the ``makedirs`` side effect of
    :func:`get_scratchpad_dir` — safe to call from a permission check on every
    read."""
    tmp = os.environ.get("TMPDIR", "/tmp")
    return os.path.join(tmp, "claude-scratchpad")


def _readable_internal_dirs(context: Any) -> list[Path]:
    """Harness-internal directories whose files are readable without a prompt.

    Mirrors TS ``checkReadableInternalPath``
    (``typescript/src/utils/permissions/filesystem.ts:1633``): the runtime
    writes these paths and then points the model back at them (e.g. a large tool
    result spilled to disk and replaced in-message with a reference), so reading
    them must never prompt even though they sit outside ``workspace_root``.

    Each source is best-effort — a missing/unimportable one is skipped, never
    fatal — so a permission check never blows up on an optional subsystem.
    """
    dirs: list[Path] = []

    # Per-session tool-results spill dir. Also folded into
    # ``ToolContext.allowed_roots()``; included here so this predicate is
    # correct when called standalone (and when ``context`` is None).
    try:
        from src.services.tool_execution.tool_result_persistence import (
            resolve_tool_results_dir,
        )

        if context is not None:
            dirs.append(resolve_tool_results_dir(context))
    except Exception:
        pass

    # Tool-result budget spill dir (``/tmp/claw_codex_budget/<pid>``) — the
    # compaction pipeline offloads large results here and the model reads them
    # back. Process-scoped so we never trust another process's spill.
    try:
        from src.services.compact.tool_result_budget import (
            get_tool_result_budget_dir,
        )

        dirs.append(get_tool_result_budget_dir())
    except Exception:
        pass

    # Per-session scratchpad (path only — do not create it here).
    try:
        dirs.append(Path(_scratchpad_dir_path()))
    except Exception:
        pass

    # Auto-memory (memdir): the ``~/.claude/projects/<slug>/memory/`` subtree
    # ONLY. NOT ``get_memory_base_dir()`` — that returns the whole ``~/.claude``
    # config home and would silently expose ``.credentials.json``, settings, and
    # *other* projects' transcripts. Mirrors TS ``isAutoMemPath``
    # (``typescript/src/memdir/paths.ts:274``), scoped to this project's slug.
    try:
        from src.memdir.paths import get_auto_mem_path

        dirs.append(Path(get_auto_mem_path()))
    except Exception:
        pass

    # NOTE: TS ``checkReadableInternalPath`` also allowlists session-plan files,
    # the current project's transcript dir (``isProjectDirPath``), project-temp
    # (``/tmp/claude/<cwd>/``), agent-memory, ``~/.claude/tasks``,
    # ``~/.claude/teams``, and bundled-skills. Those subsystems are not (yet)
    # ported here; omitting them only causes extra prompts (under-allow), never
    # extra access. Extend this set when porting them — keep every entry
    # narrowly scoped (never the ``~/.claude`` root).
    return dirs


def check_readable_internal_path(file_path: str, context: Any) -> bool:
    """True when ``file_path`` resolves inside a harness-internal readable dir.

    Compares fully resolved paths on both sides so the macOS ``/tmp`` →
    ``/private/tmp`` symlink (and any other symlinked prefix) matches.
    """
    if not file_path:
        return False
    try:
        target = Path(resolve_path(file_path))
    except Exception:
        return False
    for d in _readable_internal_dirs(context):
        try:
            if _is_within(target, Path(resolve_path(str(d)))):
                return True
        except Exception:
            continue
    return False


def _path_in_allowed_roots(file_path: str, context: Any) -> bool:
    """True when ``file_path`` resolves inside one of ``context.allowed_roots()``
    (workspace + additional working dirs + session grants + tool-results dir)."""
    if context is None:
        return False
    try:
        roots = list(context.allowed_roots())
    except Exception:
        return False
    try:
        target = Path(resolve_path(file_path))
    except Exception:
        return False
    for root in roots:
        try:
            if _is_within(target, Path(resolve_path(str(root)))):
                return True
        except Exception:
            continue
    return False


def check_read_permission_for_tool(file_path: str, context: Any) -> PermissionResult:
    """Path-based read permission, mirroring TS ``checkReadPermissionForTool``
    (``typescript/src/utils/permissions/filesystem.ts:1048``).

    Tool-level ``Read`` deny/ask rules are resolved upstream in
    :func:`src.permissions.check.has_permissions_to_use_tool_inner` *before*
    this runs, so this only supplies the path-based ``allow`` (working dir +
    internal harness) and otherwise returns ``passthrough`` — which the caller
    renders as the read ``ask`` (with the "allow reading during this session"
    suggestion). Returning ``passthrough`` (not ``ask``) also keeps ``auto`` /
    ``bypassPermissions`` modes allowing everything via the outer flow.
    """
    if not file_path:
        return PermissionPassthroughResult()

    # UNC paths — defense in depth (TS step 1). Check the raw input and the
    # abspath form (which both preserve a ``//`` / ``\\`` prefix); ``resolve_path``
    # collapses ``//`` → ``/`` so it cannot be used for this check.
    expanded_abs = os.path.abspath(os.path.expanduser(file_path))
    if (
        file_path.startswith("\\\\")
        or file_path.startswith("//")
        or expanded_abs.startswith("\\\\")
        or expanded_abs.startswith("//")
    ):
        return PermissionAskDecision(
            behavior="ask",
            message="UNC paths require manual approval for security reasons.",
            decision_reason=OtherDecisionReason(reason="UNC path detected"),
        )

    abs_path = resolve_path(file_path)

    # Reads inside an allowed working root (TS step 6).
    if _path_in_allowed_roots(abs_path, context):
        return PermissionAllowDecision(
            behavior="allow",
            decision_reason=WorkingDirDecisionReason(
                reason="Read within an allowed working directory",
            ),
        )

    # Reads of harness-internal paths (TS step 7 — checkReadableInternalPath).
    if check_readable_internal_path(abs_path, context):
        return PermissionAllowDecision(
            behavior="allow",
            decision_reason=OtherDecisionReason(
                reason="Read of a harness-internal path",
            ),
        )

    # Outside working dirs and not internal — defer to the ask flow.
    return PermissionPassthroughResult()
