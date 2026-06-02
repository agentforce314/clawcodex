"""Structured report writer for orchestrator runs."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReportResult:
    run_id: str
    workspace_markdown_path: str
    persistent_markdown_path: str
    workspace_json_path: str
    persistent_json_path: str


@dataclass(frozen=True)
class RunReport:
    run_id: str
    tracker: str
    owner: str | None
    repo: str | None
    issue_id: str
    issue_identifier: str | None
    issue_title: str | None
    status: str
    branch_name: str | None
    base_branch: str | None
    commit_sha: str | None
    pr_number: str | None
    pr_url: str | None
    turn_count: int
    tool_count: int
    verification_status: str | None
    verification_output: str | None
    output_excerpt: str
    # F-45: path to ~/.clawcodex/tool-events/{run_id}/events.ndjson
    # (per-tool audit bypass). Added at the end of the dataclass so
    # existing reader code that constructs ``RunReport(**legacy_dict)``
    # keeps working (the field defaults to None).
    tool_events_path: str | None = None


def write(
    *,
    run_id: str,
    workspace_path: Path,
    tracker: str,
    owner: str | None,
    repo: str | None,
    issue: Any,
    status: str,
    branch_name: str | None = None,
    base_branch: str | None = None,
    commit_sha: str | None = None,
    pr_number: str | None = None,
    pr_url: str | None = None,
    turn_count: int = 0,
    tool_count: int = 0,
    verification_status: str | None = None,
    verification_output: str | None = None,
    output_text: str = "",
    tool_events_path: str | None = None,
) -> ReportResult:
    issue_id = str(getattr(issue, "id", None) or "unknown")
    safe_tracker = _safe_segment(tracker or "unknown")
    safe_owner = _safe_segment(owner or "local")
    safe_repo = _safe_segment(repo or "local")
    safe_issue_id = _safe_segment(issue_id)

    report = RunReport(
        run_id=run_id,
        tracker=tracker,
        owner=owner,
        repo=repo,
        issue_id=issue_id,
        issue_identifier=getattr(issue, "identifier", None),
        issue_title=getattr(issue, "title", None),
        status=status,
        branch_name=branch_name,
        base_branch=base_branch,
        commit_sha=commit_sha,
        pr_number=pr_number,
        pr_url=pr_url,
        turn_count=turn_count,
        tool_count=tool_count,
        verification_status=verification_status,
        verification_output=verification_output,
        output_excerpt=_excerpt(output_text),
        tool_events_path=tool_events_path,
    )

    workspace_dir = workspace_path / ".reports"
    persistent_dir = (
        Path.home()
        / ".clawcodex"
        / "reports"
        / safe_tracker
        / safe_owner
        / safe_repo
        / safe_issue_id
    )
    workspace_dir.mkdir(parents=True, exist_ok=True)
    persistent_dir.mkdir(parents=True, exist_ok=True)

    workspace_md = workspace_dir / f"{run_id}.md"
    workspace_json = workspace_dir / f"{run_id}.json"
    persistent_md = persistent_dir / f"{run_id}.md"
    persistent_json = persistent_dir / f"{run_id}.json"

    markdown = _render_markdown(report)
    payload = json.dumps(asdict(report), indent=2, ensure_ascii=False)

    workspace_md.write_text(markdown, encoding="utf-8")
    workspace_json.write_text(payload, encoding="utf-8")
    _copy_with_fallback(workspace_md, persistent_md)
    _copy_with_fallback(workspace_json, persistent_json)

    # F-45 Sub-C: dual-write the per-tool NDJSON into the persistent
    # layer so the audit log survives workspace cleanup. We copy the
    # source file (under ~/.clawcodex/tool-events/) into the persistent
    # reports dir using _copy_with_fallback for atomic semantics.
    if tool_events_path:
        tool_events = Path(tool_events_path)
        if tool_events.exists():
            persistent_events = persistent_dir / f"{run_id}.events.ndjson"
            _copy_with_fallback(tool_events, persistent_events)

    return ReportResult(
        run_id=run_id,
        workspace_markdown_path=str(workspace_md),
        persistent_markdown_path=str(persistent_md),
        workspace_json_path=str(workspace_json),
        persistent_json_path=str(persistent_json),
    )


def _render_markdown(report: RunReport) -> str:
    lines = [
        "# ClawCodex Run Report",
        "",
        f"- Run: `{report.run_id}`",
        f"- Issue: {report.issue_identifier or report.issue_id}",
        f"- Status: `{report.status}`",
        f"- Tracker: `{report.tracker}`",
        f"- Branch: `{report.branch_name or 'n/a'}`",
        f"- Base: `{report.base_branch or 'n/a'}`",
        f"- Commit: `{report.commit_sha or 'n/a'}`",
        f"- Pull request: {report.pr_url or 'n/a'}",
        f"- Turns: {report.turn_count}",
        f"- Tool calls: {report.tool_count}",
        f"- Verification: `{report.verification_status or 'skipped'}`",
    ]
    # F-45: register the per-tool audit log path so the report reader
    # can `cat` it without grepping ~/.clawcodex/ first.
    if report.tool_events_path:
        lines.append(f"- Tool events: `{report.tool_events_path}`")
    lines.extend(
        [
            "",
            "## Verification Output",
            "",
            "```",
            report.verification_output or "",
            "```",
            "",
            "## Agent Output Excerpt",
            "",
            "```",
            report.output_excerpt,
            "```",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _excerpt(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return f"... (truncated from {len(text)} chars)\n{text[-limit:]}"


def _copy_with_fallback(src: Path, dst: Path) -> None:
    try:
        shutil.copy2(src, dst)
    except Exception:
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        tmp.write_bytes(src.read_bytes())
        os.replace(tmp, dst)


def _safe_segment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value).strip("-._") or "unknown"
