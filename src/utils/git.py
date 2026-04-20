from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _run_git(
    args: list[str],
    cwd: str | None = None,
    timeout: float = 30.0,
) -> tuple[str, str, int]:
    effective_cwd = cwd or os.getcwd()
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=effective_cwd,
            timeout=timeout,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", -1
    except FileNotFoundError:
        return "", "git not found", -1
    except Exception as e:
        return "", str(e), -1


def _run_git_ok(args: list[str], cwd: str | None = None) -> str:
    stdout, _, rc = _run_git(args, cwd)
    return stdout if rc == 0 else ""


@dataclass
class FileStatus:
    path: str
    status: str
    original_path: str | None = None

    @property
    def is_modified(self) -> bool:
        return self.status in ("M", "MM")

    @property
    def is_added(self) -> bool:
        return self.status in ("A", "?", "??")

    @property
    def is_deleted(self) -> bool:
        return self.status in ("D",)

    @property
    def is_renamed(self) -> bool:
        return self.status.startswith("R")


@dataclass
class DiffResult:
    diff_text: str
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0


@dataclass
class CommitAttribution:
    path: str
    modified_by_claude: bool = False
    modified_by_user: bool = False
    lines_added: int = 0
    lines_removed: int = 0


@dataclass
class Worktree:
    path: str
    branch: str | None = None
    commit: str | None = None
    is_bare: bool = False
    is_main: bool = False


def get_repo_root(cwd: str | None = None) -> str | None:
    result = _run_git_ok(["rev-parse", "--show-toplevel"], cwd)
    return result or None


def get_current_branch(cwd: str | None = None) -> str | None:
    result = _run_git_ok(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return result or None


def get_default_branch(cwd: str | None = None) -> str:
    head_ref = _run_git_ok(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd)
    if head_ref:
        parts = head_ref.rsplit("/", 1)
        if len(parts) == 2:
            return parts[1]

    for candidate in ("main", "master", "develop"):
        check = _run_git_ok(["rev-parse", "--verify", f"refs/heads/{candidate}"], cwd)
        if check:
            return candidate

    return "main"


def get_file_status(cwd: str | None = None) -> list[FileStatus]:
    effective_cwd = cwd or os.getcwd()
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-z"],
            capture_output=True,
            text=True,
            cwd=effective_cwd,
            timeout=30.0,
        )
        if result.returncode != 0:
            return []
        raw = result.stdout
    except Exception:
        return []

    if not raw:
        return []

    entries: list[FileStatus] = []
    parts = raw.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) < 3:
            i += 1
            continue

        status = entry[:2].strip()
        filepath = entry[3:]

        if not status or not filepath:
            i += 1
            continue

        original = None
        if status.startswith("R") or status.startswith("C"):
            if i + 1 < len(parts):
                original = parts[i + 1]
                i += 1

        entries.append(FileStatus(
            path=filepath,
            status=status,
            original_path=original,
        ))
        i += 1

    return entries


def get_session_diff(
    cwd: str | None = None,
    *,
    staged: bool = False,
    paths: list[str] | None = None,
) -> DiffResult:
    args = ["diff"]
    if staged:
        args.append("--cached")
    args.append("--stat")
    if paths:
        args.append("--")
        args.extend(paths)

    stat_output = _run_git_ok(args, cwd)

    diff_args = ["diff"]
    if staged:
        diff_args.append("--cached")
    if paths:
        diff_args.append("--")
        diff_args.extend(paths)

    diff_output = _run_git_ok(diff_args, cwd)

    files_changed = 0
    insertions = 0
    deletions = 0
    if stat_output:
        lines = stat_output.strip().splitlines()
        if lines:
            summary = lines[-1]
            import re
            files_match = re.search(r"(\d+)\s+file", summary)
            ins_match = re.search(r"(\d+)\s+insertion", summary)
            del_match = re.search(r"(\d+)\s+deletion", summary)
            if files_match:
                files_changed = int(files_match.group(1))
            if ins_match:
                insertions = int(ins_match.group(1))
            if del_match:
                deletions = int(del_match.group(1))

    return DiffResult(
        diff_text=diff_output,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
    )


def get_diff_against_branch(
    branch: str,
    cwd: str | None = None,
) -> DiffResult:
    diff_output = _run_git_ok(["diff", f"{branch}...HEAD"], cwd)
    stat_output = _run_git_ok(["diff", "--stat", f"{branch}...HEAD"], cwd)

    files_changed = 0
    insertions = 0
    deletions = 0
    if stat_output:
        lines = stat_output.strip().splitlines()
        if lines:
            import re
            summary = lines[-1]
            files_match = re.search(r"(\d+)\s+file", summary)
            ins_match = re.search(r"(\d+)\s+insertion", summary)
            del_match = re.search(r"(\d+)\s+deletion", summary)
            if files_match:
                files_changed = int(files_match.group(1))
            if ins_match:
                insertions = int(ins_match.group(1))
            if del_match:
                deletions = int(del_match.group(1))

    return DiffResult(
        diff_text=diff_output,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
    )


def create_branch(
    branch_name: str,
    cwd: str | None = None,
    *,
    checkout: bool = True,
    start_point: str | None = None,
) -> bool:
    args = ["checkout", "-b", branch_name]
    if start_point:
        args.append(start_point)

    if checkout:
        _, _, rc = _run_git(args, cwd)
    else:
        create_args = ["branch", branch_name]
        if start_point:
            create_args.append(start_point)
        _, _, rc = _run_git(create_args, cwd)
    return rc == 0


def get_commit_attribution(
    session_files: set[str],
    cwd: str | None = None,
) -> list[CommitAttribution]:
    status = get_file_status(cwd)
    results: list[CommitAttribution] = []

    for file_status in status:
        abs_path = os.path.abspath(os.path.join(cwd or os.getcwd(), file_status.path))
        is_claude = abs_path in session_files

        lines_added = 0
        lines_removed = 0
        diff_output = _run_git_ok(["diff", "--", file_status.path], cwd)
        if diff_output:
            for line in diff_output.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    lines_added += 1
                elif line.startswith("-") and not line.startswith("---"):
                    lines_removed += 1

        results.append(CommitAttribution(
            path=file_status.path,
            modified_by_claude=is_claude,
            modified_by_user=not is_claude,
            lines_added=lines_added,
            lines_removed=lines_removed,
        ))

    return results


def list_worktrees(cwd: str | None = None) -> list[Worktree]:
    stdout = _run_git_ok(["worktree", "list", "--porcelain"], cwd)
    if not stdout:
        return []

    worktrees: list[Worktree] = []
    current: dict[str, str] = {}

    for line in stdout.splitlines():
        if not line.strip():
            if current:
                worktrees.append(Worktree(
                    path=current.get("worktree", ""),
                    branch=current.get("branch", "").replace("refs/heads/", ""),
                    commit=current.get("HEAD"),
                    is_bare="bare" in current,
                ))
                current = {}
            continue

        if line.startswith("worktree "):
            current["worktree"] = line[9:]
        elif line.startswith("HEAD "):
            current["HEAD"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:]
        elif line == "bare":
            current["bare"] = "true"

    if current:
        worktrees.append(Worktree(
            path=current.get("worktree", ""),
            branch=current.get("branch", "").replace("refs/heads/", ""),
            commit=current.get("HEAD"),
            is_bare="bare" in current,
        ))

    if worktrees:
        worktrees[0].is_main = True

    return worktrees


def create_worktree(
    path: str,
    branch: str | None = None,
    cwd: str | None = None,
    *,
    new_branch: bool = False,
) -> bool:
    args = ["worktree", "add"]
    if new_branch and branch:
        args.extend(["-b", branch, path])
    elif branch:
        args.extend([path, branch])
    else:
        args.append(path)

    _, _, rc = _run_git(args, cwd)
    return rc == 0


def remove_worktree(
    path: str,
    cwd: str | None = None,
    *,
    force: bool = False,
) -> bool:
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(path)
    _, _, rc = _run_git(args, cwd)
    return rc == 0


async def get_session_diff_async(
    cwd: str | None = None,
    **kwargs: Any,
) -> DiffResult:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: get_session_diff(cwd, **kwargs))
