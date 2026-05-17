#!/usr/bin/env python3
"""Compare two SWE-bench harness summary reports side-by-side.

Reads two ``<model>.<run_id>.json`` files produced by
``swebench.harness.run_evaluation`` (see SWE-bench-dev/swebench/harness/reporting.py)
and emits:

* a top-line table (resolved / unresolved / empty-patch / error counts)
* per-instance disagreement lists (only-A-solved, only-B-solved, both, neither)
* a markdown report suitable for committing alongside a run

Usage:
    python eval/compare_results.py \
        --left  path/to/clawcodex.<run_id>.json  --left-label  clawcodex \
        --right path/to/openclaude.<run_id>.json --right-label openclaude \
        --out   eval/runs/<timestamp>/comparison.md
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Summary:
    """Subset of the harness summary JSON we actually need."""

    label: str
    path: Path
    total: int
    submitted: int
    completed: int
    resolved: int
    unresolved: int
    empty_patch: int
    error: int
    resolved_ids: frozenset[str]
    unresolved_ids: frozenset[str]
    empty_patch_ids: frozenset[str]
    error_ids: frozenset[str]
    submitted_ids: frozenset[str]


def load_summary(path: Path, label: str) -> Summary:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Summary(
        label=label,
        path=path,
        total=int(raw.get("total_instances", 0)),
        submitted=int(raw.get("submitted_instances", 0)),
        completed=int(raw.get("completed_instances", 0)),
        resolved=int(raw.get("resolved_instances", 0)),
        unresolved=int(raw.get("unresolved_instances", 0)),
        empty_patch=int(raw.get("empty_patch_instances", 0)),
        error=int(raw.get("error_instances", 0)),
        resolved_ids=frozenset(raw.get("resolved_ids", [])),
        unresolved_ids=frozenset(raw.get("unresolved_ids", [])),
        empty_patch_ids=frozenset(raw.get("empty_patch_ids", [])),
        error_ids=frozenset(raw.get("error_ids", [])),
        submitted_ids=frozenset(raw.get("submitted_ids", [])),
    )


def _pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.1f}%" if d > 0 else "n/a"


def _format_id_list(ids: list[str], cap: int = 50) -> str:
    if not ids:
        return "_(none)_"
    head = ids[:cap]
    body = "\n".join(f"- `{i}`" for i in head)
    if len(ids) > cap:
        body += f"\n- _… {len(ids) - cap} more_"
    return body


def render_markdown(left: Summary, right: Summary) -> str:
    """Return a markdown report for ``left`` vs ``right``."""
    only_left = sorted(left.resolved_ids - right.resolved_ids)
    only_right = sorted(right.resolved_ids - left.resolved_ids)
    both = sorted(left.resolved_ids & right.resolved_ids)
    neither = sorted(
        (left.submitted_ids | right.submitted_ids)
        - left.resolved_ids
        - right.resolved_ids
    )

    denom = max(left.total, right.total, 1)
    delta = left.resolved - right.resolved

    lines: list[str] = []
    lines.append(f"# SWE-bench comparison: `{left.label}` vs `{right.label}`")
    lines.append("")
    lines.append(f"- **{left.label}** report: `{left.path}`")
    lines.append(f"- **{right.label}** report: `{right.path}`")
    lines.append("")
    lines.append("## Top line")
    lines.append("")
    lines.append(
        f"| metric | {left.label} | {right.label} | Δ ({left.label} − {right.label}) |"
    )
    lines.append("|---|---:|---:|---:|")

    def row(metric: str, l: int, r: int) -> str:
        return f"| {metric} | {l} ({_pct(l, denom)}) | {r} ({_pct(r, denom)}) | {l - r:+d} |"

    lines.append(row("resolved", left.resolved, right.resolved))
    lines.append(row("unresolved", left.unresolved, right.unresolved))
    lines.append(row("empty patch", left.empty_patch, right.empty_patch))
    lines.append(row("error", left.error, right.error))
    lines.append(row("submitted", left.submitted, right.submitted))
    lines.append(row("completed", left.completed, right.completed))
    lines.append("")
    lines.append(f"_Total instances in dataset_: **{denom}**")
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    if delta == 0:
        lines.append(f"`{left.label}` and `{right.label}` resolved the same number of instances ({left.resolved}).")
    elif delta > 0:
        lines.append(
            f"`{left.label}` resolved **{delta} more** instance(s) than `{right.label}` "
            f"({left.resolved} vs {right.resolved})."
        )
    else:
        lines.append(
            f"`{right.label}` resolved **{-delta} more** instance(s) than `{left.label}` "
            f"({right.resolved} vs {left.resolved})."
        )
    lines.append("")
    lines.append(
        f"- Both solved: **{len(both)}**\n"
        f"- Only `{left.label}` solved: **{len(only_left)}**\n"
        f"- Only `{right.label}` solved: **{len(only_right)}**\n"
        f"- Neither solved: **{len(neither)}**"
    )
    lines.append("")
    lines.append(f"## Only `{left.label}` solved ({len(only_left)})")
    lines.append("")
    lines.append(_format_id_list(only_left))
    lines.append("")
    lines.append(f"## Only `{right.label}` solved ({len(only_right)})")
    lines.append("")
    lines.append(_format_id_list(only_right))
    lines.append("")
    lines.append(f"## Both solved ({len(both)})")
    lines.append("")
    lines.append(_format_id_list(both, cap=20))
    lines.append("")
    lines.append(f"## Neither solved ({len(neither)})")
    lines.append("")
    lines.append(_format_id_list(neither, cap=20))
    lines.append("")
    return "\n".join(lines)


def render_text(left: Summary, right: Summary) -> str:
    """Compact stdout summary."""
    only_left = len(left.resolved_ids - right.resolved_ids)
    only_right = len(right.resolved_ids - left.resolved_ids)
    both = len(left.resolved_ids & right.resolved_ids)
    denom = max(left.total, right.total, 1)
    width = max(len(left.label), len(right.label), 8)
    lines = [
        f"SWE-bench comparison ({left.label} vs {right.label}) — {denom} instances",
        f"  {'agent'.ljust(width)}  resolved  unresolved  empty   error",
        f"  {left.label.ljust(width)}  "
        f"{left.resolved:>8}  {left.unresolved:>10}  {left.empty_patch:>5}  {left.error:>5}",
        f"  {right.label.ljust(width)}  "
        f"{right.resolved:>8}  {right.unresolved:>10}  {right.empty_patch:>5}  {right.error:>5}",
        "",
        f"  both solved: {both}  |  only {left.label}: {only_left}  |  only {right.label}: {only_right}",
    ]
    return "\n".join(lines)


def write_disagreement_lists(out_dir: Path, left: Summary, right: Summary) -> dict[str, Path]:
    """Write per-instance disagreement files for triage. Returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    only_left = sorted(left.resolved_ids - right.resolved_ids)
    only_right = sorted(right.resolved_ids - left.resolved_ids)
    both = sorted(left.resolved_ids & right.resolved_ids)
    paths: dict[str, Path] = {}

    def _write(name: str, ids: list[str]) -> Path:
        p = out_dir / name
        p.write_text("\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")
        return p

    paths[f"only_{left.label}"] = _write(f"only_{left.label}.txt", only_left)
    paths[f"only_{right.label}"] = _write(f"only_{right.label}.txt", only_right)
    paths["both"] = _write("both_solved.txt", both)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", required=True, type=Path, help="Path to first summary JSON.")
    parser.add_argument("--right", required=True, type=Path, help="Path to second summary JSON.")
    parser.add_argument("--left-label", default="left", help="Label for the first agent.")
    parser.add_argument("--right-label", default="right", help="Label for the second agent.")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to write the markdown report. Disagreement lists are written next to it.",
    )
    args = parser.parse_args(argv)

    left = load_summary(args.left, args.left_label)
    right = load_summary(args.right, args.right_label)

    print(render_text(left, right))

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render_markdown(left, right), encoding="utf-8")
        write_disagreement_lists(args.out.parent, left, right)
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
