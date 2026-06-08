"""F-49 Phase 3: resume a paused or completed orchestrator session.

Given an issue identifier (or run_id), loads the SessionStorage
transcript written by the headless agent, calls :func:`Session.resume`
to update bootstrap state + restore cost counters, then renders a
short summary so the operator can confirm the LLM context is intact
before either re-attaching via ``issue attach`` or starting a fresh
REPL.

This is the orchestrator-side counterpart to
``clawcodex --resume <run_id>``. Both flows share the underlying
``SessionStorage`` (the orchestrator's ``session_id`` IS the
``run_id``) and the typed ``resume_session()`` reader, so a resume
here gives the operator the same reconstructed Conversation that
``--resume`` would on a top-level session.

Reads only; no orchestrator coupling beyond :class:`IssueRegistry`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _resolve_run_id(
    registry_path: Path | None,
    issue_id: str | None,
    run_id: str | None,
) -> tuple[str, str] | None:
    """Return ``(run_id, label)`` for the targeted session, or ``None``.

    ``label`` is the human-friendly identifier shown in the summary
    (the issue's :attr:`IssueRecord.issue_identifier` if available,
    else the issue_id, else ``"run:<run_id>"``).
    """
    from extensions.orchestrator.issue_registry import IssueRegistry

    if not issue_id and not run_id:
        return None

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
        label = record.issue_identifier or record.issue_id or f"run:{record.run_id}"
        return record.run_id, label

    # --run mode: caller knows the run_id but not the issue.
    assert run_id is not None  # already gated above
    return run_id, f"run:{run_id}"


def _render_summary(label: str, result) -> str:
    """Format a :class:`ResumeResult` for terminal output.

    Shows the metadata, message count, last 3 turns (user / assistant
    snippets), and any warnings. Truncates long content blocks to
    keep the summary readable in pipelines.
    """
    lines: list[str] = []
    lines.append(f"Resumed session for {label}")
    if result.metadata is not None:
        md = result.metadata
        lines.append(
            f"  model={md.model} cwd={md.cwd} "
            f"title={md.title!r} messages={md.message_count}"
        )
    else:
        lines.append("  (no metadata.json — using empty conversation)")

    lines.append(f"  rehydrated messages: {result.message_count}")
    if result.has_warnings:
        lines.append("  warnings:")
        for w in result.warnings:
            lines.append(f"    - {w}")

    # Last 3 turns (6 messages: user + assistant pairs).
    tail = result.messages[-6:] if result.messages else []
    if tail:
        lines.append("  last turns:")
        for msg in tail:
            role = getattr(msg, "role", "?")
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                snippet_parts: list[str] = []
                for block in content:
                    btype = getattr(block, "type", None)
                    if btype == "text":
                        snippet_parts.append(
                            (getattr(block, "text", "") or "").strip()
                        )
                    elif btype == "tool_use":
                        snippet_parts.append(
                            f"[tool:{getattr(block, 'name', '?')}]"
                        )
                    elif btype == "tool_result":
                        snippet_parts.append("[tool_result]")
                snippet = " ".join(p for p in snippet_parts if p)
            else:
                snippet = (str(content) if content else "").strip()
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
            lines.append(f"    [{role}] {snippet}")

    return "\n".join(lines)


def _run_resume_session(
    registry_path: Path | None,
    args: argparse.Namespace,
) -> int:
    """CLI handler for ``clawcodex orchestrator issue resume-session``.

    Resolves the target run, calls :meth:`Session.resume` to update
    bootstrap state, reads the JSONL transcript via
    :func:`resume_session`, and prints a summary. Exits 0 on success,
    1 on lookup failure, 2 on usage error.
    """
    issue_id = getattr(args, "id", None)
    run_id = getattr(args, "run", None)

    if not issue_id and not run_id:
        print(
            "error: --id <issue_id> or --run <run_id> is required",
            file=sys.stderr,
        )
        return 2

    resolved = _resolve_run_id(registry_path, issue_id, run_id)
    if resolved is None:
        if issue_id:
            print(
                f"error: no completed run found for issue {issue_id!r}. "
                f"Nothing to resume.",
                file=sys.stderr,
            )
        else:
            print(
                f"error: could not resolve run {run_id!r}",
                file=sys.stderr,
            )
        return 1

    target_run_id, label = resolved

    # Update bootstrap state (session_id singleton + cost counters).
    # We deliberately do NOT mutate bootstrap state when the load
    # fails — the resume_session() reader below will produce a
    # well-defined empty result.
    from src.agent.session import Session

    session = Session.resume(target_run_id)

    # Read the JSONL transcript (the canonical LLM context).
    from src.services.session_resume import resume_session

    result = resume_session(target_run_id)

    if not result.success and not result.messages and not result.metadata:
        if session is None:
            print(
                f"error: no session found for run_id {target_run_id!r} "
                f"and no transcript.jsonl is readable",
                file=sys.stderr,
            )
            return 1
        # Session was found but the JSONL is missing; surface that.
        print(
            f"warning: Session.resume() loaded run_id={target_run_id} "
            f"but no JSONL transcript was found at "
            f"~/.clawcodex/sessions/{target_run_id}/transcript.jsonl. "
            f"The conversation shown is empty.",
            file=sys.stderr,
        )

    print(_render_summary(label, result))
    return 0
