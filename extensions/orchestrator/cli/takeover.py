"""F-49 takeover: socket-aligned stop-agent-and-spawn-REPL flow.

Replaces the legacy control-file path with the Phase 1 Unix
control socket. The agent is paused (not killed) at the next
turn boundary; the operator's REPL then resumes the same
``run_id`` so inputs flow into the existing
``transcript.jsonl``.

    clawcodex orchestrator issue takeover --id ISSUE-1
        │
        ├─ IssueRegistry.get_by_issue_ref(issue_id)
        │   → (run_id, workspace_path)             # authoritative
        │
        ├─ compute socket: {workspace}/.run_control/{run_id}.sock
        │
        ├─ if socket exists:
        │   open socket → send "pause" + "takeover" → close
        │   wait 1.5s for the runner to reach the next turn
        │     boundary and call _flush_turn_transcript() + flush()
        │
        └─ subprocess: python3 -m src.cli --resume <run_id> --workspace <ws>
              │
              └─ ClawCodexExtREPL(resume_session_id=run_id)
                    → Session.resume(run_id)            # directory format
                    → REPL inputs write to the same transcript.jsonl
                      via Session._save_to_session_storage()
                      (session_id == run_id)

Reads only; no orchestrator coupling beyond :class:`IssueRegistry`.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class _TakeoverTarget:
    """Resolved target for the takeover flow."""

    run_id: str
    workspace_path: Path
    issue_id: str  # for header / log lines


def _resolve_target(
    registry_path: Path | None,
    workspace_root: Path | None,
    issue_id: str | None,
    run_id: str | None,
) -> _TakeoverTarget | None:
    """Look up ``(run_id, workspace_path)`` via :class:`IssueRegistry`
    or accept ``--run`` + ``--workspace`` directly.

    Lookup priority (mirrors
    :func:`extensions.orchestrator.cli.attach._resolve_attach_target`):
      1. ``--id <issue_id>`` via
         :meth:`IssueRegistry.get_by_issue_ref`. Returns the
         record's ``run_id`` and ``workspace_path``.
      2. ``--run <run_id>`` + ``--workspace <path>`` (or resolved
         ``workspace_root``). The caller is responsible for the
         workspace because there is no inverse index from
         ``run_id`` → ``workspace_path`` in the registry.
      3. Otherwise ``None`` — caller emits a usage / lookup error.
    """
    from extensions.orchestrator.issue_registry import IssueRegistry

    if issue_id:
        if registry_path is None or not registry_path.exists():
            return None
        try:
            registry = IssueRegistry(registry_path)
        except Exception:
            return None
        record = registry.get_by_issue_ref(issue_id)
        if record is None or record.run_id is None:
            return None
        if record.workspace_path is None:
            return None
        return _TakeoverTarget(
            run_id=record.run_id,
            workspace_path=Path(record.workspace_path),
            issue_id=record.issue_identifier or record.issue_id,
        )

    if run_id and workspace_root is not None:
        # ``--run`` mode: workspace comes from ``--workspace`` or
        # the resolved ``workspace_root``. We don't have the issue
        # identifier here, so use the run_id as a label.
        return _TakeoverTarget(
            run_id=run_id,
            workspace_path=Path(workspace_root),
            issue_id=f"run:{run_id}",
        )

    return None


def _run_takeover(
    registry_path: Path | None,
    workspace_root: Path | None,
    args: argparse.Namespace,
) -> int:
    """CLI handler for ``clawcodex orchestrator issue takeover``.

    Resolves the target via :class:`IssueRegistry`, sends
    ``pause`` + ``takeover`` over the control socket (if alive),
    waits for a quiet period, then spawns the REPL with
    ``--resume <run_id>`` so the operator's takeover writes to
    the same ``transcript.jsonl`` the headless agent used.

    Exits 0 on success, 1 on lookup / socket / spawn error, 2 on
    usage error.

    Step 1 stub: this version validates args + resolves the
    target and prints a "TODO" message. The full socket + REPL
    flow lands in Step 3 of the F-49 takeover plan.
    """
    issue_id = getattr(args, "id", None) or getattr(args, "issue_id", None)
    run_id = getattr(args, "run", None) or getattr(args, "run_id", None)
    workspace_arg = getattr(args, "workspace", None)

    if not issue_id and not run_id:
        print(
            "error: --id <issue_id> or --run <run_id> is required",
            file=sys.stderr,
        )
        return 2

    if run_id and not workspace_root and not workspace_arg:
        print(
            "error: --run requires --workspace "
            "(or a resolved workspace root)",
            file=sys.stderr,
        )
        return 2

    effective_workspace = (
        Path(workspace_arg) if workspace_arg else workspace_root
    )

    target = _resolve_target(
        registry_path, effective_workspace, issue_id, run_id,
    )
    if target is None:
        if issue_id:
            print(
                f"error: no active run found for issue {issue_id!r}. "
                f"Nothing to take over.",
                file=sys.stderr,
            )
        else:
            print(
                f"error: could not resolve target for run {run_id!r}",
                file=sys.stderr,
            )
        return 1

    print(
        f"[takeover stub] would pause+takeover agent for "
        f"{target.issue_id} (run {target.run_id}) in "
        f"{target.workspace_path} and spawn --resume REPL. "
        f"Socket path: "
        f"{target.workspace_path / '.run_control' / (target.run_id + '.sock')}",
        file=sys.stderr,
    )
    return 0
