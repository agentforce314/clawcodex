from __future__ import annotations

import os
from pathlib import Path

from .types import (
    PermissionAskDecision,
    PermissionPassthroughResult,
    PermissionResult,
    SafetyCheckDecisionReason,
)

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
    ".env",
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    ".pypirc",
    ".netrc",
    ".docker/config.json",
    ".kube/config",
    ".aws/credentials",
    ".ssh/config",
    ".ssh/known_hosts",
    ".ssh/authorized_keys",
    "Makefile",
)

DANGEROUS_DIRECTORIES: tuple[str, ...] = (
    ".git",
    ".vscode",
    ".idea",
    ".claude",
    ".ssh",
    ".gnupg",
    ".config",
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


def check_path_safety_for_auto_edit(
    file_path: str,
    cwd: str | None = None,
) -> PermissionResult | None:
    abs_path = resolve_path(file_path)
    normalized = normalize_case_for_comparison(abs_path)

    for dangerous_dir in DANGEROUS_DIRECTORIES:
        dir_lower = normalize_case_for_comparison(dangerous_dir)
        if f"/{dir_lower}/" in normalized or normalized.endswith(f"/{dir_lower}"):
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

    if is_env_file(basename):
        return PermissionAskDecision(
            behavior="ask",
            message=f"Editing {basename} requires confirmation as it may contain secrets.",
            decision_reason=SafetyCheckDecisionReason(
                reason=f"File is an environment file: {basename}",
                classifier_approvable=True,
            ),
        )

    if is_lockfile(basename):
        return PermissionAskDecision(
            behavior="ask",
            message=f"Editing lockfile {basename} requires confirmation.",
            decision_reason=SafetyCheckDecisionReason(
                reason=f"File is a lockfile: {basename}",
                classifier_approvable=False,
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
