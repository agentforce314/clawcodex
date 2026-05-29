#!/usr/bin/env python3
"""Regenerate downstream overlay patches for an extracted upstream commit.

The generated queue models this invariant:

    src/upstream/{commit} + patches/upstream/{commit}/series == src

Line endings are normalized only for comparison. Patch payloads preserve the
current src/ bytes so applying the queue recreates the downstream tree exactly.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = PROJECT / "src"
DEFAULT_UPSTREAM_ROOT = PROJECT / "src" / "upstream"
DEFAULT_PATCH_ROOT = PROJECT / "patches" / "upstream"
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIXES = (".pyc", ".pyo")
SKIP_PREFIXES = ("upstream/",)


def read_normalised(path: Path) -> bytes:
    raw = path.read_bytes()
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def files_differ_norm(upstream_path: Path, src_path: Path) -> bool:
    return read_normalised(upstream_path) != read_normalised(src_path)


def _normalize_patch_path(path: str) -> str:
    name = path.replace("/", "_")
    dot_idx = name.rfind(".")
    if dot_idx >= 0:
        name = name[:dot_idx] + "_" + name[dot_idx + 1 :]
    return name


def _is_skipped(relative_path: str) -> bool:
    if relative_path.startswith(SKIP_PREFIXES):
        return True
    if relative_path.endswith(SKIP_SUFFIXES):
        return True
    return any(part in SKIP_DIRS for part in relative_path.split("/"))


def collect_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative_path = str(path.relative_to(root))
        if not _is_skipped(relative_path):
            files.add(relative_path)
    return files


def _timestamp(path: Path | None) -> str:
    if path is None:
        return "1970-01-01 00:00:00.000000000 +0000"
    return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S.%f %z"
    )


def run_diff_raw(upstream_path: Path, src_path: Path) -> str:
    result = subprocess.run(
        ["diff", "-u", str(upstream_path), str(src_path)],
        capture_output=True,
        timeout=30,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout.decode("utf-8", errors="replace")


def _added_lines_from_raw(raw: bytes) -> str | None:
    ends_with_newline = raw.endswith(b"\n")
    has_crlf = b"\r\n" in raw
    content = raw.decode("utf-8")

    if has_crlf:
        lines = content.split("\r\n")
        if content.endswith("\r\n"):
            lines.pop()
        newline_marker = "\r"
    else:
        lines = content.split("\n")
        if content.endswith("\n"):
            lines.pop()
        newline_marker = ""

    if not lines:
        return None

    diff_lines = [f"@@ -0,0 +1,{len(lines)} @@"]
    for index, line in enumerate(lines):
        if has_crlf and (index < len(lines) - 1 or ends_with_newline):
            diff_lines.append(f"+{line}{newline_marker}")
        else:
            diff_lines.append(f"+{line}")

    body = "\n".join(diff_lines) + "\n"
    if not ends_with_newline:
        body += "\\ No newline at end of file\n"
    return body


def _deleted_lines_from_raw(raw: bytes) -> str | None:
    content = raw.decode("utf-8")
    ends_with_newline = raw.endswith(b"\n")
    lines = content.splitlines()
    if not lines:
        return None

    diff_lines = [f"@@ -1,{len(lines)} +0,0 @@"]
    diff_lines.extend(f"-{line}" for line in lines)
    body = "\n".join(diff_lines) + "\n"
    if not ends_with_newline:
        body += "\\ No newline at end of file\n"
    return body


def generate_modified_patch(relative_path: str, upstream_path: Path, src_path: Path) -> str | None:
    diff_output = run_diff_raw(upstream_path, src_path)
    if not diff_output:
        return None

    diff_lines = diff_output.split("\n")
    body = "\n".join(diff_lines[2:])
    if not body.strip():
        return None

    return (
        f"diff --git a/{relative_path} b/{relative_path}\n"
        f"--- a/{relative_path}\t{_timestamp(upstream_path)}\n"
        f"+++ b/{relative_path}\t{_timestamp(src_path)}\n"
        f"{body}"
    )


def generate_new_patch(relative_path: str, src_path: Path) -> str | None:
    body = _added_lines_from_raw(src_path.read_bytes())
    if body is None:
        return None

    return (
        f"diff --git a/{relative_path} b/{relative_path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{relative_path}\t{_timestamp(src_path)}\n"
        f"{body}"
    )


def generate_delete_patch(relative_path: str, upstream_path: Path) -> str | None:
    body = _deleted_lines_from_raw(upstream_path.read_bytes())
    if body is None:
        return None

    return (
        f"diff --git a/{relative_path} b/{relative_path}\n"
        "deleted file mode 100644\n"
        f"--- a/{relative_path}\t{_timestamp(upstream_path)}\n"
        "+++ /dev/null\n"
        f"{body}"
    )


def write_patch(path: Path, content: str) -> None:
    path.write_bytes(content.encode("utf-8"))


def backup_existing(patch_dir: Path, backup_dir: Path) -> None:
    if not patch_dir.exists():
        return

    existing = list(patch_dir.glob("*.patch"))
    if not existing:
        return

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / timestamp
    target.mkdir(parents=True, exist_ok=True)
    for patch in existing:
        shutil.move(str(patch), target / patch.name)
    print(f"Backed up {len(existing)} existing patches to {target}")


def write_series(
    series_file: Path,
    compatibility_series_file: Path,
    commit: str,
    patch_entries: list[tuple[str, str]],
    modified_count: int,
    new_count: int,
    deleted_count: int,
) -> None:
    lines = [
        f"# Quilt series file — {commit} (regenerated downstream overlay patches)",
        "#",
        "# Generated by scripts/regenerate_patches_upstream.py",
        f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "#",
        f"# Modified files: {modified_count}",
        f"# New files: {new_count}",
        f"# Deleted files: {deleted_count}",
        f"# Total: {len(patch_entries)}",
        "",
        "# === Phase 1: Modified files (diffs from upstream) ===",
    ]

    phase = "modified"
    for patch_filename, patch_type in patch_entries:
        if patch_type != phase:
            phase = patch_type
            if phase == "new":
                lines.extend(["", "# === Phase 2: New files (not present in upstream) ==="])
            elif phase == "deleted":
                lines.extend(["", "# === Phase 3: Deleted files (removed from upstream base) ==="])
        lines.append(patch_filename)

    lines.append("")
    series_file.write_text("\n".join(lines), encoding="utf-8")

    compatibility_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            compatibility_lines.append(f"merged/{stripped}")
        else:
            compatibility_lines.append(line)
    compatibility_series_file.write_text("\n".join(compatibility_lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="Upstream snapshot under src/upstream/{commit}")
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC, help="Downstream source tree")
    parser.add_argument("--upstream-root", type=Path, default=DEFAULT_UPSTREAM_ROOT)
    parser.add_argument("--patch-root", type=Path, default=DEFAULT_PATCH_ROOT)
    parser.add_argument(
        "--allow-deletes",
        action="store_true",
        help="Generate delete patches for files present upstream but absent downstream",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    src = args.src.resolve()
    upstream = (args.upstream_root / args.commit).resolve()
    patch_base = (args.patch_root / args.commit).resolve()
    patch_dir = patch_base / "merged"
    backup_dir = patch_base / "backup"
    series_file = patch_base / "series"
    compatibility_series_file = patch_base / f"{args.commit}_series"

    if not src.exists():
        print(f"Source tree does not exist: {src}", file=sys.stderr)
        return 1
    if not upstream.exists():
        print(f"Upstream snapshot does not exist: {upstream}", file=sys.stderr)
        return 1

    upstream_files = collect_files(upstream)
    src_files = collect_files(src)

    modified_files = sorted(
        relative_path
        for relative_path in src_files & upstream_files
        if files_differ_norm(upstream / relative_path, src / relative_path)
    )
    new_files = sorted(src_files - upstream_files)
    deleted_files = sorted(upstream_files - src_files)

    if deleted_files and not args.allow_deletes:
        print(f"{len(deleted_files)} upstream files are absent from {src}:")
        for relative_path in deleted_files:
            print(f"  - {relative_path}")
        print("Re-run with --allow-deletes to generate delete patches after review.")
        return 2

    backup_existing(patch_dir, backup_dir)
    patch_dir.mkdir(parents=True, exist_ok=True)
    patch_base.mkdir(parents=True, exist_ok=True)

    patch_entries: list[tuple[str, str]] = []
    index = 1

    for relative_path in modified_files:
        content = generate_modified_patch(relative_path, upstream / relative_path, src / relative_path)
        if content is None:
            continue
        patch_filename = f"{index:04d}.{_normalize_patch_path(relative_path)}.patch"
        write_patch(patch_dir / patch_filename, content)
        patch_entries.append((patch_filename, "modified"))
        index += 1

    for relative_path in new_files:
        content = generate_new_patch(relative_path, src / relative_path)
        if content is None:
            continue
        patch_filename = f"{index:04d}.{_normalize_patch_path(relative_path)}.patch"
        write_patch(patch_dir / patch_filename, content)
        patch_entries.append((patch_filename, "new"))
        index += 1

    if args.allow_deletes:
        for relative_path in deleted_files:
            content = generate_delete_patch(relative_path, upstream / relative_path)
            if content is None:
                continue
            patch_filename = f"{index:04d}.{_normalize_patch_path(relative_path)}.delete.patch"
            write_patch(patch_dir / patch_filename, content)
            patch_entries.append((patch_filename, "deleted"))
            index += 1

    write_series(
        series_file,
        compatibility_series_file,
        args.commit,
        patch_entries,
        len(modified_files),
        len(new_files),
        len(deleted_files) if args.allow_deletes else 0,
    )

    total_size = sum((patch_dir / patch_filename).stat().st_size for patch_filename, _ in patch_entries)
    print(f"Modified files: {len(modified_files)}")
    print(f"New files: {len(new_files)}")
    print(f"Deleted files: {len(deleted_files) if args.allow_deletes else 0}")
    print(f"Total patches: {len(patch_entries)}")
    print(f"Total size: {total_size:,} bytes ({total_size / 1024 / 1024:.1f} MB)")
    print(f"Patch directory: {patch_dir}")
    print(f"Series file: {series_file}")
    print(f"Compatibility series file: {compatibility_series_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
