"""orchestrator dashboard — standalone LiveView UI.

Usage:
  clawcodex orchestrator dashboard --port 8080 [--workspace PATH]

Launches a standalone HTTP server that streams real-time orchestrator events
to a web-based dashboard. Agents push events to a local event log, and the
dashboard server reads these logs to render a web UI.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Issue status taxonomy
# ---------------------------------------------------------------------------
# These constants MUST stay in sync with extensions.orchestrator.issue_registry
# .IssueStatus. We re-declare them here so the dashboard server can be loaded
# even when the orchestrator is not installed in the same interpreter, and so
# the strings are stable for the frontend.

ISSUE_STATUSES: tuple[str, ...] = (
    "queued",
    "pending",
    "running",
    "synced",
    "pending_review",
    "completed",
    "failed",
    "abandoned",
    "verification_failed",
)

STATUS_META: dict[str, dict[str, str]] = {
    "queued":              {"label": "Queued",             "color": "#6e7681", "icon": "◷", "group": "active"},
    "pending":             {"label": "Pending",            "color": "#d29922", "icon": "○", "group": "active"},
    "running":             {"label": "Running",            "color": "#58a6ff", "icon": "◉", "group": "active"},
    "synced":              {"label": "Synced",             "color": "#a371f7", "icon": "⇄", "group": "active"},
    "pending_review":      {"label": "Review",             "color": "#79c0ff", "icon": "◎", "group": "active"},
    "completed":           {"label": "Completed",          "color": "#3fb950", "icon": "✓", "group": "terminal"},
    "failed":              {"label": "Failed",             "color": "#f85149", "icon": "✗", "group": "terminal"},
    "abandoned":           {"label": "Abandoned",          "color": "#8b949e", "icon": "⊘", "group": "terminal"},
    "verification_failed": {"label": "Verify Failed",      "color": "#db6d28", "icon": "⚠", "group": "terminal"},
}

ACTIVE_STATUSES = {s for s, m in STATUS_META.items() if m["group"] == "active"}
TERMINAL_STATUSES = {s for s, m in STATUS_META.items() if m["group"] == "terminal"}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def add_dashboard_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "dashboard",
        help="Launch standalone LiveView dashboard UI",
        description="Start an HTTP server with a web dashboard for real-time "
                    "orchestrator monitoring. Streams running sessions, tool calls, "
                    "and LLM responses.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to listen on (default: 8080)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        metavar="PATH",
        help="Workspace root to read registry/event logs from. "
             "If omitted, uses $CLAWCODEX_WORKSPACE_ROOT, falls back to the "
             "latest metadata under ~/.clawcodex/orchestrator/*/metadata.json, "
             "and finally ~/.clawcodex/workspace.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not auto-open the dashboard URL in a browser.",
    )


# ---------------------------------------------------------------------------
# Workspace resolution
# ---------------------------------------------------------------------------

def _resolve_workspace_root(explicit: str | None = None) -> Path:
    """Resolve the workspace root in priority order.

    1. Explicit --workspace argument.
    2. $CLAWCODEX_WORKSPACE_ROOT environment variable.
    3. Latest metadata.json under ~/.clawcodex/orchestrator/*/metadata.json.
    4. ~/.clawcodex/workspace (last-resort default).
    """
    if explicit:
        return Path(explicit).expanduser().resolve()

    env_ws = os.environ.get("CLAWCODEX_WORKSPACE_ROOT")
    if env_ws:
        return Path(env_ws).expanduser().resolve()

    metadata_dir = Path.home() / ".clawcodex" / "orchestrator"
    if metadata_dir.exists():
        candidates = []
        for md_dir in metadata_dir.iterdir():
            mf = md_dir / "metadata.json"
            if mf.exists():
                try:
                    data = json.loads(mf.read_text(encoding="utf-8"))
                    ws = data.get("workspace_root")
                    started = data.get("started_at") or 0
                    if ws:
                        candidates.append((started, Path(ws)))
                except Exception:
                    continue
        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            return candidates[0][1]

    return Path.home() / ".clawcodex" / "workspace"


# ---------------------------------------------------------------------------
# State aggregation
# ---------------------------------------------------------------------------

def _safe_read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _classify_status(value: Any) -> str:
    """Normalize a status value to one of ISSUE_STATUSES, defaulting to 'pending'."""
    if not isinstance(value, str):
        return "pending"
    v = value.strip().lower()
    return v if v in ISSUE_STATUSES else "pending"


def _gather_issue_metadata(workspace: Path) -> dict[str, Any]:
    """Read the IssueRegistry JSON and produce a structured per-issue view.

    Returns a dict shaped as:
      {
        "issues": [ {issue_id, identifier, status, ...}, ... ],
        "by_status": { "pending": n, "running": n, ... },
        "totals": { "total": n, "active": n, "terminal": n, "prs": n }
      }
    """
    registry_path = workspace / ".clawcodex_issue_registry.json"
    raw = _safe_read_json(registry_path) or {}

    issues: list[dict[str, Any]] = []
    by_status: dict[str, int] = {s: 0 for s in ISSUE_STATUSES}

    now = time.time()
    for issue_id, record in raw.items():
        if not isinstance(record, dict):
            continue
        status = _classify_status(record.get("status"))
        by_status[status] += 1

        created_at = float(record.get("created_at") or 0)
        updated_at = float(record.get("updated_at") or 0)
        age_seconds = max(0, int(now - created_at)) if created_at else 0
        idle_seconds = max(0, int(now - updated_at)) if updated_at else 0

        workspace_path = record.get("workspace_path") or ""
        workspace_short = ""
        if workspace_path:
            try:
                workspace_short = "/" + str(Path(workspace_path).relative_to(workspace)) if workspace in Path(workspace_path).parents else workspace_path
            except Exception:
                workspace_short = workspace_path

        issues.append({
            "issue_id": issue_id,
            "identifier": record.get("issue_identifier") or issue_id,
            "status": status,
            "branch_name": record.get("branch_name"),
            "commit_sha": record.get("commit_sha"),
            "pr_number": record.get("pr_number"),
            "pr_url": record.get("pr_url"),
            "base_branch": record.get("base_branch") or "main",
            "workspace_path": workspace_path,
            "workspace_short": workspace_short,
            "workspace_strategy": record.get("workspace_strategy"),
            "attempt_count": int(record.get("attempt_count") or 0),
            "retry_count": int(record.get("retry_count") or 0),
            "sequence_index": record.get("sequence_index"),
            "intent": record.get("intent") or "none",
            "report_path": record.get("report_path"),
            "verification_status": record.get("verification_status"),
            "clarification_status": record.get("clarification_status"),
            "created_at": created_at,
            "updated_at": updated_at,
            "age_seconds": age_seconds,
            "idle_seconds": idle_seconds,
        })

    # Sort: active statuses first, then by most recent activity.
    issues.sort(key=lambda i: (
        0 if i["status"] in ACTIVE_STATUSES else 1,
        -int(i["updated_at"] or 0),
    ))

    return {
        "issues": issues,
        "by_status": by_status,
        "totals": {
            "total": len(issues),
            "active": sum(1 for i in issues if i["status"] in ACTIVE_STATUSES),
            "terminal": sum(1 for i in issues if i["status"] in TERMINAL_STATUSES),
            "prs": sum(1 for i in issues if i.get("pr_number")),
        },
    }


def _gather_metadata(workspace: Path) -> dict[str, Any]:
    """Read the orchestrator daemon metadata.json (PID, started_at, project)."""
    metadata_dir = Path.home() / ".clawcodex" / "orchestrator"
    if not metadata_dir.exists():
        return {"found": False}

    best: tuple[float, dict[str, Any], Path] | None = None
    for md_dir in metadata_dir.iterdir():
        mf = md_dir / "metadata.json"
        if not mf.exists():
            continue
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("workspace_root") and str(data.get("workspace_root")) != str(workspace):
            continue
        started = float(data.get("started_at") or 0)
        if best is None or started > best[0]:
            best = (started, data, mf)

    if best is None:
        return {"found": False}

    _, data, mf = best
    pid = data.get("pid")
    alive = False
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
            alive = True
        except (OSError, ProcessLookupError):
            alive = False
    started_at = float(data.get("started_at") or 0)
    return {
        "found": True,
        "pid": pid,
        "alive": alive,
        "started_at": started_at,
        "uptime_seconds": max(0, int(time.time() - started_at)) if started_at else 0,
        "project_slug": data.get("project_slug") or "",
        "workflow_path": data.get("workflow_path") or "",
        "metadata_path": str(mf),
    }


def build_state_snapshot(workspace: Path) -> dict[str, Any]:
    """Assemble a complete snapshot for the dashboard."""
    issues = _gather_issue_metadata(workspace)
    meta = _gather_metadata(workspace)
    return {
        "type": "snapshot",
        "ts": time.time(),
        "workspace": str(workspace),
        "workspace_exists": workspace.exists(),
        "metadata": meta,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class DashboardState:
    """Per-process shared state for the dashboard HTTP server."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.snapshot: dict[str, Any] = build_state_snapshot(workspace)
        self.last_snapshot_at: float = time.time()
        self.snapshot_interval: float = 1.0
        self._lock = threading.Lock()

    def refresh_snapshot(self, force: bool = False) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            if force or (now - self.last_snapshot_at) >= self.snapshot_interval:
                self.snapshot = build_state_snapshot(self.workspace)
                self.last_snapshot_at = now
            return self.snapshot


# JavaScript payload of the dashboard HTML. Kept in a module-level constant so
# Python syntax-checks the surrounding code independently of the JS body.
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ClawCodex Orchestrator LiveView</title>
  <style>
    :root {
      --bg-0: #0b0f17;
      --bg-1: #11161f;
      --bg-2: #161c26;
      --bg-3: #1d2532;
      --line: #232c3a;
      --line-soft: #1a212c;
      --fg-0: #e6edf3;
      --fg-1: #c9d1d9;
      --fg-2: #8b949e;
      --fg-3: #6e7681;
      --accent: #58a6ff;
      --accent-2: #79c0ff;
      --good: #3fb950;
      --warn: #d29922;
      --bad: #f85149;
      --vermillion: #db6d28;
      --purple: #a371f7;
      --cyan: #79c0ff;
      --gray: #8b949e;
      --shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0; padding: 0;
      background: var(--bg-0);
      color: var(--fg-1);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue",
                   "PingFang SC", "Microsoft YaHei", Arial, sans-serif;
      font-size: 13px;
      line-height: 1.45;
      min-height: 100vh;
    }
    body {
      background:
        radial-gradient(1200px 600px at 80% -10%, rgba(88,166,255,0.06), transparent 70%),
        radial-gradient(1000px 500px at -10% 110%, rgba(163,113,247,0.05), transparent 70%),
        var(--bg-0);
    }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    code, .mono { font-family: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace; }
    button { font-family: inherit; }

    /* Layout */
    .app {
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      grid-template-columns: 1fr;
      min-height: 100vh;
      padding: 18px 22px 12px;
      gap: 14px;
    }

    /* Header */
    .header {
      display: flex; align-items: center; gap: 18px;
      padding: 14px 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.02), transparent), var(--bg-1);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
    }
    .header .brand {
      display: flex; align-items: center; gap: 10px;
      font-weight: 600; font-size: 15px; color: var(--fg-0);
    }
    .header .brand .logo {
      width: 26px; height: 26px; border-radius: 7px;
      background: conic-gradient(from 200deg, #58a6ff, #a371f7, #3fb950, #58a6ff);
      box-shadow: 0 0 0 1px rgba(255,255,255,0.06) inset;
    }
    .header .meta { display: flex; gap: 18px; flex: 1; flex-wrap: wrap; }
    .header .meta .kv { color: var(--fg-2); font-size: 12px; }
    .header .meta .kv b { color: var(--fg-0); font-weight: 500; margin-left: 6px; }
    .conn {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 6px 10px; border-radius: 999px;
      background: var(--bg-2); border: 1px solid var(--line);
      font-size: 12px;
    }
    .conn .dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--gray);
      box-shadow: 0 0 0 0 rgba(63,185,80,0.5);
      transition: background 0.2s;
    }
    .conn.ok .dot { background: var(--good); animation: pulse 1.6s infinite; }
    .conn.bad .dot { background: var(--bad); }
    @keyframes pulse {
      0%   { box-shadow: 0 0 0 0 rgba(63,185,80,0.5); }
      70%  { box-shadow: 0 0 0 8px rgba(63,185,80,0); }
      100% { box-shadow: 0 0 0 0 rgba(63,185,80,0); }
    }

    /* Status grid */
    .status-grid {
      display: grid;
      grid-template-columns: repeat(8, minmax(0, 1fr));
      gap: 10px;
    }
    @media (max-width: 1280px) { .status-grid { grid-template-columns: repeat(4, 1fr); } }
    @media (max-width: 720px)  { .status-grid { grid-template-columns: repeat(2, 1fr); } }
    .stat {
      position: relative;
      padding: 12px 14px;
      background: var(--bg-1);
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
      transition: transform 0.15s, border-color 0.2s;
    }
    .stat:hover { transform: translateY(-1px); border-color: var(--line); }
    .stat .label {
      display: flex; align-items: center; gap: 6px;
      font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase;
      color: var(--fg-2);
    }
    .stat .label .ico {
      display: inline-flex; align-items: center; justify-content: center;
      width: 16px; height: 16px; border-radius: 4px;
      background: rgba(255,255,255,0.04);
      font-size: 11px;
    }
    .stat .value {
      font-size: 26px; font-weight: 600; color: var(--fg-0);
      margin-top: 6px; font-variant-numeric: tabular-nums;
    }
    .stat .sub { font-size: 11px; color: var(--fg-3); margin-top: 2px; }
    .stat.zero .value { color: var(--fg-3); }
    .stat::before {
      content: ""; position: absolute; left: 0; top: 0; bottom: 0;
      width: 3px; background: var(--c, var(--accent));
    }

    /* Main panels */
    .main {
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(360px, 1fr);
      gap: 14px;
      min-height: 0;
    }
    @media (max-width: 1100px) { .main { grid-template-columns: 1fr; } }
    .panel {
      background: var(--bg-1);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
      display: flex; flex-direction: column;
      min-height: 0;
    }
    .panel-header {
      display: flex; align-items: center; gap: 10px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--line);
    }
    .panel-header h3 {
      margin: 0; font-size: 13px; font-weight: 600; color: var(--fg-0);
      letter-spacing: 0.02em;
    }
    .panel-header .actions { margin-left: auto; display: flex; gap: 6px; }
    .panel-header input[type="search"], .panel-header select {
      background: var(--bg-2);
      border: 1px solid var(--line);
      color: var(--fg-1);
      border-radius: 6px;
      padding: 5px 8px;
      font-size: 12px;
      outline: none;
    }
    .panel-header input[type="search"] { width: 180px; }
    .panel-header input[type="search"]:focus, .panel-header select:focus { border-color: var(--accent); }
    .panel-body { flex: 1; overflow: auto; min-height: 0; }

    /* Issue table */
    table.issues { width: 100%; border-collapse: collapse; font-size: 12.5px; }
    table.issues th, table.issues td {
      padding: 9px 12px; text-align: left; border-bottom: 1px solid var(--line-soft);
      white-space: nowrap;
    }
    table.issues th {
      position: sticky; top: 0; z-index: 1;
      background: var(--bg-1); color: var(--fg-2); font-weight: 500;
      font-size: 11px; letter-spacing: 0.05em; text-transform: uppercase;
    }
    table.issues tr { cursor: pointer; transition: background 0.1s; }
    table.issues tr:hover { background: rgba(88,166,255,0.05); }
    table.issues tr.selected { background: rgba(88,166,255,0.10); }
    table.issues td.identifier { color: var(--fg-0); font-weight: 500; }
    table.issues td.branch, table.issues td.workspace { color: var(--fg-2); }
    table.issues td.workspace { max-width: 220px; overflow: hidden; text-overflow: ellipsis; }
    table.issues td.num { text-align: right; font-variant-numeric: tabular-nums; }
    .pill {
      display: inline-flex; align-items: center; gap: 6px;
      padding: 2px 8px; border-radius: 999px;
      font-size: 11px; font-weight: 500;
      background: color-mix(in srgb, var(--c) 14%, transparent);
      color: var(--c);
      border: 1px solid color-mix(in srgb, var(--c) 35%, transparent);
    }
    .pill .ico { font-size: 10px; }

    /* Detail panel */
    .detail { padding: 14px 16px; }
    .detail h2 {
      margin: 0 0 4px; font-size: 18px; color: var(--fg-0);
      display: flex; align-items: center; gap: 10px;
    }
    .detail .sub { color: var(--fg-2); font-size: 12px; }
    .detail .grid {
      display: grid; grid-template-columns: 110px 1fr; gap: 6px 14px;
      margin-top: 14px; font-size: 12.5px;
    }
    .detail .grid .k { color: var(--fg-2); }
    .detail .grid .v { color: var(--fg-1); word-break: break-all; }
    .detail .section-title {
      margin: 18px 0 8px; font-size: 11px; text-transform: uppercase;
      letter-spacing: 0.08em; color: var(--fg-3);
    }
    .events-mini {
      max-height: 240px; overflow: auto;
      border: 1px solid var(--line); border-radius: 8px;
      background: var(--bg-0);
    }
    .events-mini .row {
      display: grid; grid-template-columns: 72px 64px 1fr;
      gap: 10px; padding: 6px 10px; font-size: 12px;
      border-bottom: 1px solid var(--line-soft);
    }
    .events-mini .row:last-child { border-bottom: 0; }
    .events-mini .ts { color: var(--fg-3); font-variant-numeric: tabular-nums; }
    .events-mini .type {
      font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.06em;
      color: var(--c, var(--fg-2));
    }
    .empty {
      padding: 28px 16px; text-align: center; color: var(--fg-3); font-size: 12px;
    }

    /* Live event feed */
    .feed {
      background: var(--bg-1);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
      max-height: 260px;
      display: flex; flex-direction: column;
    }
    .feed .panel-body { font-family: "JetBrains Mono", "SF Mono", Menlo, monospace; font-size: 12px; }
    .feed .row {
      display: grid;
      grid-template-columns: 78px 60px 80px 1fr;
      gap: 10px; padding: 5px 14px;
      border-bottom: 1px solid var(--line-soft);
      align-items: baseline;
    }
    .feed .row:hover { background: rgba(88,166,255,0.05); }
    .feed .row .ts { color: var(--fg-3); font-variant-numeric: tabular-nums; }
    .feed .row .type { text-transform: uppercase; font-size: 10.5px; letter-spacing: 0.06em; color: var(--c, var(--fg-2)); }
    .feed .row .ident { color: var(--accent-2); }
    .feed .row .msg { color: var(--fg-1); white-space: pre-wrap; word-break: break-word; }

    /* Token + status distribution */
    .charts { display: grid; grid-template-columns: 2fr 1fr; gap: 14px; }
    @media (max-width: 1100px) { .charts { grid-template-columns: 1fr; } }
    .chart-card {
      background: var(--bg-1);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 16px;
      box-shadow: var(--shadow);
    }
    .chart-card h3 {
      margin: 0 0 8px; font-size: 12px; text-transform: uppercase;
      letter-spacing: 0.06em; color: var(--fg-2); font-weight: 500;
    }
    .dist-bar { display: flex; height: 10px; border-radius: 6px; overflow: hidden; background: var(--bg-2); }
    .dist-bar .seg { transition: flex 0.4s; }
    .dist-legend {
      display: grid; grid-template-columns: repeat(2, 1fr); gap: 4px 12px;
      margin-top: 10px; font-size: 11.5px; color: var(--fg-2);
    }
    .dist-legend .it { display: flex; align-items: center; gap: 6px; }
    .dist-legend .sw { width: 10px; height: 10px; border-radius: 3px; background: var(--c, var(--accent)); }
    .dist-legend .n { color: var(--fg-0); font-variant-numeric: tabular-nums; margin-left: auto; }

    /* Scrollbars */
    ::-webkit-scrollbar { width: 10px; height: 10px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--bg-3); border-radius: 5px; }
    ::-webkit-scrollbar-thumb:hover { background: #2a3445; }

    /* Utility */
    .small { font-size: 11.5px; color: var(--fg-2); }
    .muted { color: var(--fg-3); }
    .nowrap { white-space: nowrap; }
    .right { text-align: right; }
    .hidden { display: none !important; }
  </style>
</head>
<body>
  <div class="app">
    <div class="header">
      <div class="brand">
        <div class="logo"></div>
        <div>ClawCodex <span class="muted">·</span> Orchestrator LiveView</div>
      </div>
      <div class="meta">
        <div class="kv">Workspace <b id="hdr-workspace" class="mono">…</b></div>
        <div class="kv">Daemon <b id="hdr-daemon">…</b></div>
        <div class="kv">Uptime <b id="hdr-uptime">—</b></div>
        <div class="kv">Pull Requests <b id="hdr-prs">0</b></div>
        <div class="kv">Event Count <b id="hdr-events">0</b></div>
      </div>
      <div id="conn" class="conn"><span class="dot"></span><span id="conn-text">connecting…</span></div>
    </div>

    <div class="status-grid" id="status-grid"></div>

    <div class="main">
      <div class="panel">
        <div class="panel-header">
          <h3>Issues</h3>
          <span class="small muted" id="issue-count">0</span>
          <div class="actions">
            <input id="filter" type="search" placeholder="Filter by id / branch…">
            <select id="status-filter">
              <option value="">All statuses</option>
            </select>
          </div>
        </div>
        <div class="panel-body">
          <table class="issues">
            <thead>
              <tr>
                <th>Status</th>
                <th>Identifier</th>
                <th>Branch</th>
                <th>PR</th>
                <th>Workspace</th>
                <th class="right">Attempts</th>
                <th class="right">Idle</th>
              </tr>
            </thead>
            <tbody id="issues-body">
              <tr><td colspan="7" class="empty">Loading…</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <h3>Detail</h3>
          <div class="actions">
            <span class="small muted" id="detail-sub">Select an issue to inspect</span>
          </div>
        </div>
        <div class="panel-body">
          <div id="detail" class="detail">
            <div class="empty">Click an issue on the left to see details, branch, commit, PR, and recent events.</div>
          </div>
        </div>
      </div>
    </div>

    <div class="charts">
      <div class="chart-card">
        <h3>Event Type Distribution</h3>
        <div class="dist-bar" id="dist-bar"></div>
        <div class="dist-legend" id="dist-legend"></div>
      </div>
      <div class="chart-card">
        <h3>Token Activity</h3>
        <div class="small" id="tok-summary" style="color:var(--fg-1)">
          <div>Active sessions: <b id="tok-active">0</b></div>
          <div>Aggregate events: <b id="tok-events">0</b></div>
          <div class="muted" style="margin-top:6px">Per-session counters stream from orchestrator state.</div>
        </div>
      </div>
    </div>

    <div class="feed">
      <div class="panel-header">
        <h3>Live Event Feed</h3>
        <span class="small muted" id="feed-count">0 events</span>
        <div class="actions">
          <select id="feed-type">
            <option value="">All event types</option>
            <option value="tool_call">tool_call</option>
            <option value="tool_result">tool_result</option>
            <option value="text_delta">text_delta</option>
            <option value="phase_complete">phase_complete</option>
          </select>
          <button id="feed-clear" style="background:var(--bg-2);border:1px solid var(--line);color:var(--fg-1);border-radius:6px;padding:4px 8px;cursor:pointer;">Clear</button>
        </div>
      </div>
      <div class="panel-body" id="feed-body">
        <div class="empty">Waiting for events…</div>
      </div>
    </div>
  </div>

  <script>
    "use strict";

    // ---- Constants ---------------------------------------------------------
    const STATUS_META = __STATUS_META__;
    const STATUSES = Object.keys(STATUS_META);
    const ACTIVE = new Set(STATUSES.filter(s => STATUS_META[s].group === "active"));
    const TERMINAL = new Set(STATUSES.filter(s => STATUS_META[s].group === "terminal"));

    const EVENT_TYPE_META = {
      tool_call:      { color: "#a371f7", label: "Tool Call" },
      tool_result:    { color: "#3fb950", label: "Tool Result" },
      text_delta:     { color: "#79c0ff", label: "Text Delta" },
      phase_complete: { color: "#d29922", label: "Phase Complete" },
    };
    const MAX_FEED_ROWS = 500;

    // ---- State -------------------------------------------------------------
    const state = {
      snapshot: null,
      selectedIssueId: null,
      filter: "",
      statusFilter: "",
      feedTypeFilter: "",
      feed: [],            // newest first
      offsets: {},         // last seen file offsets (per issue_id)
    };

    // ---- DOM refs ----------------------------------------------------------
    const $ = (id) => document.getElementById(id);
    const el = {
      conn: $("conn"), connText: $("conn-text"),
      hdrWorkspace: $("hdr-workspace"), hdrDaemon: $("hdr-daemon"),
      hdrUptime: $("hdr-uptime"), hdrPrs: $("hdr-prs"), hdrEvents: $("hdr-events"),
      statusGrid: $("status-grid"),
      issueCount: $("issue-count"),
      issuesBody: $("issues-body"),
      filter: $("filter"),
      statusFilter: $("status-filter"),
      feedBody: $("feed-body"), feedCount: $("feed-count"),
      feedType: $("feed-type"), feedClear: $("feed-clear"),
      detail: $("detail"), detailSub: $("detail-sub"),
      distBar: $("dist-bar"), distLegend: $("dist-legend"),
      tokActive: $("tok-active"), tokEvents: $("tok-events"),
    };

    // ---- Utilities ---------------------------------------------------------
    function fmtAge(seconds) {
      if (seconds == null) return "—";
      if (seconds < 60) return seconds + "s";
      if (seconds < 3600) return Math.floor(seconds/60) + "m " + (seconds%60) + "s";
      const h = Math.floor(seconds/3600);
      const m = Math.floor((seconds%3600)/60);
      return h + "h " + m + "m";
    }
    function fmtUptime(seconds) {
      if (!seconds) return "—";
      if (seconds < 60) return seconds + "s";
      if (seconds < 3600) return Math.floor(seconds/60) + "m";
      const h = Math.floor(seconds/3600);
      const m = Math.floor((seconds%3600)/60);
      return h + "h " + m + "m";
    }
    function fmtNumber(n) {
      if (n == null) return "0";
      if (n < 1000) return String(n);
      if (n < 1_000_000) return (n/1000).toFixed(n < 10000 ? 1 : 0) + "k";
      return (n/1_000_000).toFixed(1) + "M";
    }
    function shortSha(sha) { return sha ? sha.slice(0, 7) : ""; }
    function escapeHtml(s) {
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }
    function truncate(s, n) {
      s = s == null ? "" : String(s);
      return s.length > n ? s.slice(0, n - 1) + "…" : s;
    }

    function pillFor(status) {
      const m = STATUS_META[status] || STATUS_META.pending;
      return `<span class="pill" style="--c:${m.color}"><span class="ico">${m.icon}</span>${m.label}</span>`;
    }

    // ---- Rendering ---------------------------------------------------------
    function renderStatusGrid(byStatus) {
      const html = STATUSES.map(s => {
        const m = STATUS_META[s];
        const n = byStatus[s] || 0;
        const isZero = n === 0 ? " zero" : "";
        const groupLabel = m.group === "active" ? "active" : "terminal";
        return `
          <div class="stat${isZero}" style="--c:${m.color}">
            <div class="label"><span class="ico" style="color:${m.color}">${m.icon}</span>${m.label}<span class="muted" style="margin-left:auto">${groupLabel}</span></div>
            <div class="value">${n}</div>
            <div class="sub">${STATUSES.indexOf(s) >= 0 ? "issues in state" : ""}</div>
          </div>`;
      }).join("");
      el.statusGrid.innerHTML = html;
    }

    function renderIssues(issues) {
      const f = state.filter.trim().toLowerCase();
      const sf = state.statusFilter;
      const filtered = issues.filter(i => {
        if (sf && i.status !== sf) return false;
        if (f) {
          const hay = [i.identifier, i.branch_name, i.workspace_path].filter(Boolean).join(" ").toLowerCase();
          if (!hay.includes(f)) return false;
        }
        return true;
      });
      el.issueCount.textContent = filtered.length + " / " + issues.length;
      if (filtered.length === 0) {
        el.issuesBody.innerHTML = `<tr><td colspan="7" class="empty">No issues match the current filter.</td></tr>`;
        return;
      }
      const rows = filtered.map(i => {
        const pr = i.pr_number
          ? `<a href="${escapeHtml(i.pr_url || "#")}" target="_blank" rel="noopener">#${escapeHtml(i.pr_number)}</a>`
          : `<span class="muted">—</span>`;
        const branch = i.branch_name
          ? `<span class="mono">${escapeHtml(truncate(i.branch_name, 28))}</span>`
          : `<span class="muted">—</span>`;
        const ws = i.workspace_short
          ? `<span class="mono" title="${escapeHtml(i.workspace_path || "")}">${escapeHtml(truncate(i.workspace_short, 30))}</span>`
          : `<span class="muted">—</span>`;
        const sel = i.issue_id === state.selectedIssueId ? " selected" : "";
        return `
          <tr class="issue-row${sel}" data-id="${escapeHtml(i.issue_id)}">
            <td>${pillFor(i.status)}</td>
            <td class="identifier">${escapeHtml(i.identifier)}</td>
            <td class="branch">${branch}</td>
            <td>${pr}</td>
            <td class="workspace">${ws}</td>
            <td class="num">${i.attempt_count || 0}</td>
            <td class="num">${fmtAge(i.idle_seconds)}</td>
          </tr>`;
      }).join("");
      el.issuesBody.innerHTML = rows;
      el.issuesBody.querySelectorAll(".issue-row").forEach(tr => {
        tr.addEventListener("click", () => selectIssue(tr.getAttribute("data-id")));
      });
    }

    function renderDistribution(byType) {
      const entries = Object.entries(byType || {});
      const total = entries.reduce((a, [, n]) => a + n, 0);
      if (total === 0) {
        el.distBar.innerHTML = `<div class="seg" style="flex:1;background:var(--bg-3)"></div>`;
        el.distLegend.innerHTML = `<div class="muted small">No events yet</div>`;
        return;
      }
      el.distBar.innerHTML = entries.map(([t, n]) => {
        const m = EVENT_TYPE_META[t] || { color: "#8b949e", label: t };
        return `<div class="seg" style="flex:${n};background:${m.color}" title="${m.label}: ${n}"></div>`;
      }).join("");
      el.distLegend.innerHTML = entries.map(([t, n]) => {
        const m = EVENT_TYPE_META[t] || { color: "#8b949e", label: t };
        const pct = ((n / total) * 100).toFixed(1);
        return `<div class="it"><span class="sw" style="--c:${m.color};background:${m.color}"></span>${m.label}<span class="n">${fmtNumber(n)} (${pct}%)</span></div>`;
      }).join("");
    }

    function renderHeader(snap) {
      el.hdrWorkspace.textContent = snap.workspace || "—";
      el.hdrWorkspace.title = snap.workspace || "";
      const m = snap.metadata || {};
      if (m.found) {
        el.hdrDaemon.innerHTML = m.alive
          ? `<span style="color:var(--good)">●</span> running (PID ${m.pid || "?"})`
          : `<span style="color:var(--bad)">●</span> stopped`;
        el.hdrDaemon.title = m.metadata_path || "";
        el.hdrUptime.textContent = m.alive ? fmtUptime(m.uptime_seconds) : "—";
      } else {
        el.hdrDaemon.innerHTML = `<span class="muted">not detected</span>`;
        el.hdrUptime.textContent = "—";
      }
      el.hdrPrs.textContent = (snap.issues && snap.issues.totals && snap.issues.totals.prs) || 0;
      el.hdrEvents.textContent = (snap.events && snap.events.total) || 0;
      el.tokActive.textContent = (snap.issues && snap.issues.totals && snap.issues.totals.active) || 0;
      el.tokEvents.textContent = (snap.events && snap.events.total) || 0;
    }

    function renderDetail(issueId) {
      if (!state.snapshot) return;
      const issues = state.snapshot.issues.issues || [];
      const issue = issues.find(i => i.issue_id === issueId);
      if (!issue) {
        el.detail.innerHTML = `<div class="empty">Issue not found.</div>`;
        el.detailSub.textContent = "—";
        return;
      }
      const m = STATUS_META[issue.status] || STATUS_META.pending;
      const events = (state.snapshot.events && state.snapshot.events.recent || [])
        .filter(e => e.issue_id === issueId)
        .slice(0, 20);
      el.detailSub.innerHTML = `${pillFor(issue.status)} <span class="muted small">· ${issue.workspace_strategy || "—"} strategy</span>`;
      const rows = [
        ["Identifier",   `<b>${escapeHtml(issue.identifier)}</b>`],
        ["Issue ID",     `<span class="mono">${escapeHtml(issue.issue_id)}</span>`],
        ["Status",       pillFor(issue.status)],
        ["Branch",       issue.branch_name ? `<span class="mono">${escapeHtml(issue.branch_name)}</span>` : `<span class="muted">—</span>`],
        ["Base branch",  `<span class="mono">${escapeHtml(issue.base_branch || "main")}</span>`],
        ["Commit",       issue.commit_sha ? `<span class="mono">${escapeHtml(shortSha(issue.commit_sha))}</span> <span class="muted">${escapeHtml(issue.commit_sha)}</span>` : `<span class="muted">—</span>`],
        ["Pull request", issue.pr_number ? `<a href="${escapeHtml(issue.pr_url || "#")}" target="_blank" rel="noopener">#${escapeHtml(issue.pr_number)}</a>` : `<span class="muted">—</span>`],
        ["Workspace",    issue.workspace_path ? `<span class="mono" title="${escapeHtml(issue.workspace_path)}">${escapeHtml(issue.workspace_path)}</span>` : `<span class="muted">—</span>`],
        ["Sequence",     issue.sequence_index != null ? `#${issue.sequence_index}` : `<span class="muted">—</span>`],
        ["Intent",       `<span class="mono">${escapeHtml(issue.intent || "none")}</span>`],
        ["Attempts",     String(issue.attempt_count || 0)],
        ["Retries",      String(issue.retry_count || 0)],
        ["Verification", issue.verification_status ? escapeHtml(issue.verification_status) : `<span class="muted">—</span>`],
        ["Clarification",issue.clarification_status ? escapeHtml(issue.clarification_status) : `<span class="muted">—</span>`],
        ["Created",      issue.created_at ? new Date(issue.created_at * 1000).toLocaleString() : `<span class="muted">—</span>`],
        ["Updated",      issue.updated_at ? new Date(issue.updated_at * 1000).toLocaleString() : `<span class="muted">—</span>`],
        ["Idle",         fmtAge(issue.idle_seconds)],
        ["Age",          fmtAge(issue.age_seconds)],
      ];
      const eventsHtml = events.length === 0
        ? `<div class="empty">No recent events for this issue.</div>`
        : events.map(e => {
            const ev = e.event || {};
            const t = ev.type || "?";
            const m2 = EVENT_TYPE_META[t] || { color: "#8b949e", label: t.toUpperCase() };
            let body = "";
            if (t === "tool_call") {
              const params = ev.params ? JSON.stringify(ev.params) : "";
              body = `<span class="mono">${escapeHtml(truncate(params, 120))}</span>`;
            } else if (t === "tool_result") {
              body = `<span class="mono">${escapeHtml(ev.tool_name || "")}</span>${ev.is_error ? ' <span style="color:var(--bad)">[error]</span>' : ""}`;
            } else if (t === "text_delta") {
              body = `<span>${escapeHtml(truncate(ev.content || "", 120))}</span>`;
            } else if (t === "phase_complete") {
              body = `phase ${ev.phase || "?"} · turn ${ev.turn_count || "?"}`;
            } else {
              body = `<span class="mono">${escapeHtml(truncate(JSON.stringify(ev), 120))}</span>`;
            }
            return `<div class="row" style="--c:${m2.color}"><span class="ts">${escapeHtml(e.timestamp || "")}</span><span class="type" style="color:${m2.color}">${m2.label}</span><span>${body}</span></div>`;
          }).join("");

      el.detail.innerHTML = `
        <h2>${escapeHtml(issue.identifier)} ${pillFor(issue.status)}</h2>
        <div class="sub mono">${escapeHtml(issue.issue_id)}</div>
        <div class="grid">
          ${rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join("")}
        </div>
        <div class="section-title">Recent Events (${events.length})</div>
        <div class="events-mini">${eventsHtml}</div>
      `;
    }

    function renderFeed() {
      const f = state.feedTypeFilter;
      const rows = state.feed.filter(e => !f || (e.event && e.event.type === f));
      el.feedCount.textContent = rows.length + " events";
      if (rows.length === 0) {
        el.feedBody.innerHTML = `<div class="empty">No events match the current filter.</div>`;
        return;
      }
      el.feedBody.innerHTML = rows.slice(0, MAX_FEED_ROWS).map(e => {
        const ev = e.event || {};
        const t = ev.type || "?";
        const m = EVENT_TYPE_META[t] || { color: "#8b949e", label: t.toUpperCase() };
        let body = "";
        if (t === "tool_call") {
          const params = ev.params ? JSON.stringify(ev.params) : "";
          body = `<b>${escapeHtml(ev.tool_name || "?")}</b> <span class="muted">${escapeHtml(truncate(params, 100))}</span>`;
        } else if (t === "tool_result") {
          body = `<b>${escapeHtml(ev.tool_name || "?")}</b>${ev.is_error ? ' <span style="color:var(--bad)">[error]</span>' : ""}`;
        } else if (t === "text_delta") {
          body = `<span>${escapeHtml(truncate(ev.content || "", 160))}</span>`;
        } else if (t === "phase_complete") {
          body = `<span>phase ${escapeHtml(String(ev.phase || "?"))} · turn ${escapeHtml(String(ev.turn_count || "?"))}</span>`;
        } else {
          body = `<span class="mono">${escapeHtml(truncate(JSON.stringify(ev), 140))}</span>`;
        }
        return `<div class="row" style="--c:${m.color}"><span class="ts">${escapeHtml(e.timestamp || "")}</span><span class="type" style="color:${m.color}">${m.label}</span><span class="ident">${escapeHtml(identifierFor(e.issue_id))}</span><span class="msg">${body}</span></div>`;
      }).join("");
    }

    function identifierFor(issueId) {
      if (!state.snapshot) return issueId;
      const issue = (state.snapshot.issues.issues || []).find(i => i.issue_id === issueId);
      return issue ? issue.identifier : issueId;
    }

    function renderStatusFilter() {
      if (!state.snapshot) return;
      const counts = state.snapshot.issues.by_status || {};
      const current = state.statusFilter;
      const opts = [`<option value="">All statuses</option>`].concat(
        STATUSES.map(s => `<option value="${s}" ${s === current ? "selected" : ""}>${STATUS_META[s].label} (${counts[s] || 0})</option>`)
      );
      el.statusFilter.innerHTML = opts.join("");
    }

    function renderAll() {
      if (!state.snapshot) return;
      renderHeader(state.snapshot);
      renderStatusGrid(state.snapshot.issues.by_status || {});
      renderStatusFilter();
      renderIssues(state.snapshot.issues.issues || []);
      renderDistribution(state.snapshot.events.by_type || {});
      if (state.selectedIssueId) renderDetail(state.selectedIssueId);
      renderFeed();
    }

    function applySnapshot(snap) {
      state.snapshot = snap;
      renderAll();
    }

    function selectIssue(issueId) {
      state.selectedIssueId = state.selectedIssueId === issueId ? null : issueId;
      renderIssues(state.snapshot.issues.issues || []);
      if (state.selectedIssueId) renderDetail(state.selectedIssueId);
      else {
        el.detail.innerHTML = `<div class="empty">Click an issue on the left to see details, branch, commit, PR, and recent events.</div>`;
        el.detailSub.textContent = "Select an issue to inspect";
      }
    }

    // ---- Event handling ----------------------------------------------------
    function pushEvent(evt) {
      state.feed.unshift(evt);
      if (state.feed.length > MAX_FEED_ROWS * 2) state.feed.length = MAX_FEED_ROWS * 2;
      // Increment by_type counter and update distribution live.
      if (state.snapshot && evt.event && evt.event.type) {
        const bt = state.snapshot.events.by_type || (state.snapshot.events.by_type = {});
        bt[evt.event.type] = (bt[evt.event.type] || 0) + 1;
        state.snapshot.events.total = (state.snapshot.events.total || 0) + 1;
        renderDistribution(bt);
        el.hdrEvents.textContent = state.snapshot.events.total;
      }
      renderFeed();
    }

    // ---- SSE ---------------------------------------------------------------
    let es = null;
    function connect() {
      if (es) { try { es.close(); } catch (e) {} }
      es = new EventSource("/events");
      es.onopen = () => {
        el.conn.classList.remove("bad"); el.conn.classList.add("ok");
        el.connText.textContent = "connected";
      };
      es.onerror = () => {
        el.conn.classList.remove("ok"); el.conn.classList.add("bad");
        el.connText.textContent = "disconnected — retrying…";
      };
      es.onmessage = (msg) => {
        let data;
        try { data = JSON.parse(msg.data); } catch (e) { return; }
        if (data.type === "snapshot") {
          applySnapshot(data);
        } else if (data.type === "event") {
          pushEvent(data);
        }
      };
    }

    // ---- Wire up -----------------------------------------------------------
    el.filter.addEventListener("input", (e) => {
      state.filter = e.target.value;
      if (state.snapshot) renderIssues(state.snapshot.issues.issues || []);
    });
    el.statusFilter.addEventListener("change", (e) => {
      state.statusFilter = e.target.value;
      if (state.snapshot) renderIssues(state.snapshot.issues.issues || []);
    });
    el.feedType.addEventListener("change", (e) => {
      state.feedTypeFilter = e.target.value;
      renderFeed();
    });
    el.feedClear.addEventListener("click", () => {
      state.feed = [];
      renderFeed();
    });

    connect();
    // Periodic refresh to keep header idle/uptime counters ticking even when
    // the server is idle.
    setInterval(() => {
      if (state.snapshot) renderHeader(state.snapshot);
    }, 1000);
  </script>
</body>
</html>"""


def _build_dashboard_html() -> str:
    """Inject the STATUS_META JSON into the HTML template."""
    return DASHBOARD_HTML.replace(
        "__STATUS_META__",
        json.dumps(STATUS_META, ensure_ascii=False),
    )


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler serving the dashboard UI, JSON snapshots, and SSE events."""

    server_version = "ClawCodexDashboard/1.0"
    state: DashboardState  # set on the class by run()

    # Quieter logs — one line per request is too noisy for a polling UI.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, content_type: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path or "/"

        if path == "/":
            self._send_text(_build_dashboard_html(), "text/html; charset=utf-8")
            return

        if path == "/api/state":
            snap = self.state.refresh_snapshot(force=True)
            self._send_json(snap)
            return

        if path.startswith("/api/issue/"):
            issue_id = path[len("/api/issue/"):]
            snap = self.state.refresh_snapshot(force=True)
            for issue in snap["issues"]["issues"]:
                if issue["issue_id"] == issue_id:
                    self._send_json({"issue": issue})
                    return
            self._send_json({"error": "not found", "issue_id": issue_id}, status=404)
            return

        if path == "/api/health":
            self._send_json({
                "ok": True,
                "workspace": str(self.state.workspace),
                "ts": time.time(),
            })
            return

        if path == "/events":
            self._stream_events()
            return

        self.send_error(404, "Not Found")

    # ----- SSE streaming ---------------------------------------------------

    def _stream_events(self) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
        except Exception:
            return

        snapshot_interval = self.state.snapshot_interval
        last_snapshot_at = 0.0

        try:
            # F-49 unified storage: live event stream is sourced from
            # the session transcript JSONL.  We only push periodic
            # snapshot updates here — per-event streaming is handled
            # out-of-band by callers (e.g. ``issue attach``) since the
            # transcript is a regular append-only file readable by
            # ``tail -f`` / TailFollower.
            snap = self.state.refresh_snapshot(force=True)
            last_snapshot_at = time.time()
            self._write_sse({"type": "snapshot", **snap})

            while True:
                # Throttle snapshot refreshes to once per snapshot_interval.
                now = time.time()
                if now - last_snapshot_at >= snapshot_interval:
                    snap = self.state.refresh_snapshot(force=True)
                    last_snapshot_at = now
                    self._write_sse({"type": "snapshot", **snap})

                # Heartbeat / keep-alive.
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()

                time.sleep(snapshot_interval)
        except Exception:
            # Client disconnected or stream error — exit cleanly.
            return

    def _write_sse(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str)
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Execute the orchestrator dashboard command."""
    port: int = args.port
    host: str = args.host
    no_browser: bool = getattr(args, "no_browser", False)

    workspace = _resolve_workspace_root(getattr(args, "workspace", None))
    state = DashboardState(workspace)

    # Bind the per-process state to the handler class.
    DashboardHandler.state = state

    print(f"[dashboard] Workspace : {workspace}")
    print(f"[dashboard] Starting LiveView dashboard on http://{host}:{port}")
    if not workspace.exists():
        print(f"[dashboard] Note: workspace does not exist yet — UI will render empty until it is created.")

    try:
        server = ThreadingHTTPServer((host, port), DashboardHandler)
        threading.Thread(target=server.serve_forever, name="DashboardHTTP", daemon=True).start()

        if not no_browser:
            try:
                import webbrowser
                webbrowser.open(f"http://{host}:{port}")
            except Exception:
                pass

        print(f"[dashboard] Serving at http://{host}:{port}", file=__import__("sys").stderr)
        print("[dashboard] Press Ctrl+C to stop", file=__import__("sys").stderr)

        # Park the main thread.
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[dashboard] stopped")
    except OSError as exc:
        print(f"[dashboard] error: {exc}", file=__import__("sys").stderr)
        return 1

    return 0
