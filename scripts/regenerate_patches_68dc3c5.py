#!/usr/bin/env python3
"""
Regenerate upstream patches for commit 68dc3c5.

For every file in src/ that differs from src/upstream/68dc3c5/, generate a
unified-diff patch.  Applying all patches (via quilt / git am / patch -p1)
to src/upstream/68dc3c5/ must produce src/ exactly.

IMPORTANT: upstream files use LF line endings while src/ files may use CRLF.
This script normalises everything to LF before diffing so the generated
patches apply cleanly to the upstream (LF) tree.

Usage:
    python scripts/regenerate_patches_68dc3c5.py
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
UPSTREAM = PROJECT / "src" / "upstream" / "68dc3c5"
SRC = PROJECT / "src"
PATCH_DIR = PROJECT / "patches" / "upstream" / "68dc3c5" / "merged"
SERIES_FILE = PATCH_DIR.parent / "series"
BACKUP_DIR = PROJECT / "patches" / "upstream" / "68dc3c5" / "backup"


def read_normalised(path: Path) -> str:
    """Read a file and normalise line endings to LF (\\n)."""
    raw = path.read_bytes()
    # Replace CRLF with LF
    raw = raw.replace(b"\r\n", b"\n")
    # Replace any stray CR with LF
    raw = raw.replace(b"\r", b"\n")
    return raw.decode("utf-8")


def files_differ_norm(upstream_path: Path, src_path: Path) -> bool:
    """Compare two files after normalising line endings."""
    return read_normalised(upstream_path) != read_normalised(src_path)


def _normalize_patch_path(path: str) -> str:
    """Convert a relative file path to the patch-naming convention.

    Examples:
        bridge/__init__.py  →  bridge__init__.py
        agent/_outlines_adapter.py  →  agent__outlines_adapter_py
        settings/pydantic_adapter.py  →  settings_pydantic_adapter_py
    """
    # Replace directory separators with underscores
    name = path.replace("/", "_")
    # Replace the last dot (before extension) with underscore
    dot_idx = name.rfind(".")
    if dot_idx >= 0:
        name = name[:dot_idx] + "_" + name[dot_idx + 1:]
    return name


def generate_patch_header(relative_path: str, upstream_path: Path, src_path: Path, is_new: bool) -> str:
    """Generate a unified-diff patch header for a single file."""
    from datetime import datetime, timezone

    upstream_mtime = os.path.getmtime(upstream_path) if not is_new else 0
    src_mtime = os.path.getmtime(src_path)

    upstream_ts = datetime.fromtimestamp(upstream_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f %z") if not is_new else "1970-01-01 00:00:00.000000000 +0000"
    src_ts = datetime.fromtimestamp(src_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f %z")

    if is_new:
        return (
            f"diff --git a/{relative_path} b/{relative_path}\n"
            f"new file mode 100644\n"
            f"--- /dev/null\n"
            f"+++ src/upstream/68dc3c5/{relative_path}\n"
        )
    else:
        return (
            f"--- src/upstream/68dc3c5/{relative_path}\t{upstream_ts}\n"
            f"+++ src/{relative_path}\t{src_ts}\n"
        )


def run_diff_raw(upstream_path: Path, src_path: Path) -> str:
    """Run unified diff between upstream and src files AS-IS (no line-ending normalisation).

    diff -u on Linux correctly handles CRLF/LF differences: context lines
    from the upstream file (LF) are shown without \\r, while '+' lines from
    src (CRLF) are shown with \\r.  This means the resulting patch will
    correctly convert LF upstream files into CRLF output.

    NOTE: We MUST use text=False (binary mode) when capturing, because
    text=True / universal_newlines=True strips \\r characters from the output,
    which would destroy the CRLF information in '+' lines.
    """
    result = subprocess.run(
        ["diff", "-u", str(upstream_path), str(src_path)],
        capture_output=True, timeout=30
    )
    return result.stdout.decode("utf-8")


def write_patch_content(path: Path, content: str) -> None:
    """Write patch content to disk.

    The content preserves CRLF in '+' lines (from diff -u) so that 'patch'
    correctly creates/modifies files with the right line endings.
    """
    path.write_bytes(content.encode("utf-8"))


def generate_patch_content(relative_path: str, upstream_path: Path, src_path: Path, is_new: bool) -> str | None:
    """Generate full patch content for a file. Returns None if no meaningful diff."""
    header = generate_patch_header(relative_path, upstream_path, src_path, is_new)

    if is_new:
        # For new files, include the full file content WITHOUT normalising
        # line endings (keep CRLF intact so 'patch' creates files matching src/)
        raw = src_path.read_bytes()
        ends_with_newline = raw.endswith(b"\n")
        has_crlf = b"\r\n" in raw
        if has_crlf:
            content = raw.decode("utf-8")
            lines = content.split("\r\n")
            if content.endswith("\r\n"):
                lines.pop()
            if not lines:
                return None
            line_count = len(lines)
            diff_lines = [f"@@ -0,0 +1,{line_count} @@"]
            for i, line in enumerate(lines):
                if i < len(lines) - 1:
                    diff_lines.append(f"+{line}\r")
                else:
                    # Last line: only include \r if file ends with newline
                    if ends_with_newline:
                        diff_lines.append(f"+{line}\r")
                    else:
                        diff_lines.append(f"+{line}")
            if ends_with_newline:
                body = "\n".join(diff_lines) + "\n"
            else:
                body = "\n".join(diff_lines) + "\n\\ No newline at end of file\n"
            return header + body
        else:
            content = raw.decode("utf-8")
            lines = content.split("\n")
            if content.endswith("\n"):
                lines.pop()
            if not lines:
                return None
            line_count = len(lines)
            diff_lines = [f"@@ -0,0 +1,{line_count} @@"]
            for i, line in enumerate(lines):
                diff_lines.append(f"+{line}")
            if ends_with_newline:
                body = "\n".join(diff_lines) + "\n"
            else:
                body = "\n".join(diff_lines) + "\n\\ No newline at end of file\n"
            return header + body
    else:
        # For modified files, use raw diff (preserves CRLF in + lines)
        diff_output = run_diff_raw(upstream_path, src_path)
        if not diff_output:
            return None
        # Remove the first two lines of diff output (--- and +++ with temp paths)
        # since we already generate our own header
        diff_lines = diff_output.split("\n")
        # diff_lines[0] is "--- /tmp/.../upstream"
        # diff_lines[1] is "+++ /tmp/.../src"
        # Keep the rest
        body = "\n".join(diff_lines[2:])
        # Only return if there's actual content (hunks)
        if not body.strip():
            return None
        return header + body


def generate_quilt_header(relative_path: str, patch_name: str) -> str:
    """Generate a quilt-compatible description header."""
    return f"Patch: {patch_name}\nDescription: Upstream changes for {relative_path}\n\n"


def main():
    # Create backup of existing patches
    if PATCH_DIR.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        for f in PATCH_DIR.glob("*.patch"):
            f.rename(BACKUP_DIR / f.name)
        print(f"Backed up existing patches to {BACKUP_DIR}")

    # Ensure patch directory exists
    PATCH_DIR.mkdir(parents=True, exist_ok=True)

    # Collect all files to patch
    modified_files = []  # Files in both, content differs
    new_files = []       # Files only in src/

    # Walk through src/upstream/68dc3c5/ and src/
    upstream_files = set()
    src_files = set()

    for f in UPSTREAM.rglob("*"):
        if f.is_file() and not f.name.endswith((".pyc", ".pyo")):
            rel = f.relative_to(UPSTREAM)
            upstream_files.add(str(rel))

    for f in SRC.rglob("*"):
        if f.is_file() and not f.name.endswith((".pyc", ".pyo")):
            rel = f.relative_to(SRC)
            src_files.add(str(rel))

    # Skip certain directories
    skip_dirs = {"__pycache__"}
    skip_prefixes = {"upstream/"}  # Don't patch src/upstream/ itself

    # Categorize files
    for rel_path in src_files:
        # Skip files in upstream/ directory itself
        if any(rel_path.startswith(p) for p in skip_prefixes):
            continue

        # Skip files in skip_dirs
        parts = rel_path.split("/")
        if any(p in skip_dirs for p in parts):
            continue

        upstream_file = UPSTREAM / rel_path
        src_file = SRC / rel_path

        if upstream_file.exists():
            # File exists in both - check if different (normalised)
            if files_differ_norm(upstream_file, src_file):
                modified_files.append(rel_path)
        else:
            # File only in src/ - new file
            new_files.append(rel_path)

    # Check for deleted files (in upstream but not in src/)
    deleted_files = []
    for rel_path in upstream_files:
        if any(rel_path.startswith(p) for p in skip_prefixes):
            continue
        parts = rel_path.split("/")
        if any(p in skip_dirs for p in parts):
            continue
        if not (SRC / rel_path).exists():
            deleted_files.append(rel_path)

    if deleted_files:
        print(f"WARNING: {len(deleted_files)} files exist in upstream but not in src/:")
        for f in deleted_files:
            print(f"  - {f}")
        print("These will need delete patches (not yet implemented).\n")

    # Sort for deterministic ordering
    modified_files.sort()
    new_files.sort()

    print(f"Modified files: {len(modified_files)}")
    print(f"New files: {len(new_files)}")
    if deleted_files:
        print(f"Deleted files (WARNING): {len(deleted_files)}")

    # Generate patches for modified files
    patch_entries = []  # (patch_filename, patch_path) in application order

    # Phase 1: Modified files
    success_count = 0
    skip_count = 0
    for i, rel_path in enumerate(modified_files, start=1):
        upstream_file = UPSTREAM / rel_path
        src_file = SRC / rel_path

        # Generate patch content
        content = generate_patch_content(rel_path, upstream_file, src_file, is_new=False)
        if content is None:
            skip_count += 1
            continue

        # Determine patch filename
        norm_name = _normalize_patch_path(rel_path)
        patch_filename = f"{i:04d}.{norm_name}.patch"
        patch_path = PATCH_DIR / patch_filename

        write_patch_content(patch_path, content)
        patch_entries.append((patch_filename, "modified"))
        success_count += 1
        if success_count % 50 == 0:
            print(f"  [{success_count}/{len(modified_files)}] MOD: ... ({skip_count} skipped)")
    print(f"  Modified: {success_count} patches generated, {skip_count} skipped")

    # Phase 2: New files
    new_count = 0
    for j, rel_path in enumerate(new_files, start=len(modified_files) + 1):
        src_file = SRC / rel_path

        content = generate_patch_content(rel_path, UPSTREAM / rel_path, src_file, is_new=True)
        if content is None:
            continue

        norm_name = _normalize_patch_path(rel_path)
        patch_filename = f"{j:04d}.{norm_name}.patch"
        patch_path = PATCH_DIR / patch_filename

        write_patch_content(patch_path, content)
        patch_entries.append((patch_filename, "new"))
        new_count += 1
    print(f"  New files: {new_count} patches generated")

    # Generate series file
    series_lines = [
        f"# Quilt series file — 68dc3c5 (regenerated patches)",
        "#",
        "# Generated by scripts/regenerate_patches_68dc3c5.py",
        f"# Date: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"#",
        f"# Modified files: {len(modified_files)}",
        f"# New files: {len(new_files)}",
        f"# Total: {len(patch_entries)}",
        "",
        "# === Phase 1: Modified files (diffs from upstream) ===",
    ]

    for patch_filename, ptype in patch_entries:
        if ptype == "new" and not any("=== Phase 2" in l for l in series_lines):
            series_lines.extend([
                "",
                "# === Phase 2: New files (not present in upstream) ===",
            ])
        series_lines.append(patch_filename)

    series_lines.append("")  # trailing newline
    SERIES_FILE.write_text("\n".join(series_lines), encoding="utf-8")
    print(f"\nSeries file: {SERIES_FILE} ({len(patch_entries)} patches)")

    # Print summary
    total_diff_size = sum(os.path.getsize(PATCH_DIR / p[0]) for p in patch_entries)
    print(f"\nSummary:")
    print(f"  Total patches: {len(patch_entries)}")
    print(f"  Total size: {total_diff_size:,} bytes ({total_diff_size/1024/1024:.1f} MB)")
    print(f"  Patch directory: {PATCH_DIR}")
    print(f"  Series file: {SERIES_FILE}")


if __name__ == "__main__":
    main()
