"""resume — ``/resume`` session picker (port of TS local-jsx, components C2).

TS ``/resume`` (``commands/resume/index.ts``: description "Resume a previous
conversation", argumentHint "[conversation id or search term]") mounts the
``LogSelector`` picker and swaps the live session. Python's interactive swap
lives in the TUI (``tui/commands.py`` → ``open_dialog="resume"`` →
``ResumeConversation`` → ``AgentBridge.resume_session``), because only the
TUI owns a live conversation it can replace.

This registry command serves the NON-TUI surfaces (REPL/SDK/help/aggregator)
in the **output-style precedent**: ``run()`` returns text without touching
``ctx.ui`` — a degraded-but-honest LIST of resumable sessions plus the
pointer to the TUI for the actual swap. Filtering matches the TUI picker:
metadata-only sessions (``message_count == 0``) are hidden and counted
(gap-doc §5 Q2 decision — headless ``/rename`` can mint such entries).

Coexistence: **inversion** (the ``/theme`` pattern) — the TUI intercept
stays authoritative; this command never runs there.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import (
    CommandContext,
    InteractiveCommand,
    InteractiveOutcome,
)


def _list_resumable(term: str = "") -> tuple[list[str], int]:
    """``(lines, hidden_count)`` for the degraded session list.

    UI-neutral by construction: imports only ``services`` modules (no
    Textual — the dependency-direction rule from the C1/C2 reviews).
    """

    from src.bootstrap.state import get_session_id
    from src.services.session_listing import build_resume_entries, filter_entries

    try:
        from src.services.session_storage import SessionStorage

        metas = SessionStorage.list_sessions()
    except Exception:
        metas = []
    entries, hidden = build_resume_entries(
        metas, exclude_session_id=str(get_session_id())
    )
    if term:
        entries = filter_entries(entries, term)
    return [f"• {entry.label()}  [{entry.session_id}]" for entry in entries], hidden


@dataclass(frozen=True)
class ResumeCommand(InteractiveCommand):
    """List resumable sessions; the interactive swap is TUI-only."""

    async def run(self, args: str, context: CommandContext) -> InteractiveOutcome:
        term = (args or "").strip()
        lines, hidden = _list_resumable(term)
        if not lines:
            message = (
                f"No resumable conversations match {term!r}."
                if term
                else "No resumable conversations yet."
            )
            if hidden:
                message += (
                    f" ({hidden} metadata-only session(s) hidden — "
                    "no stored messages.)"
                )
            return InteractiveOutcome(message=message, display="system")
        header = (
            f"Resumable conversations matching {term!r}:"
            if term
            else "Resumable conversations:"
        )
        parts = [header]
        parts.extend(lines)
        if hidden:
            parts.append(
                f"({hidden} unresumable metadata-only session(s) hidden)"
            )
        parts.append(
            "Resuming replaces the live conversation — run /resume inside "
            "the TUI to pick and load one."
        )
        return InteractiveOutcome(message="\n".join(parts), display="system")


RESUME_COMMAND = ResumeCommand(
    name="resume",
    description="Resume a previous conversation",  # verbatim TS index.ts:6
    argument_hint="[conversation id or search term]",  # verbatim TS index.ts:8
    aliases=["continue"],  # verbatim TS index.ts:7
)


__all__ = ["RESUME_COMMAND", "ResumeCommand"]
