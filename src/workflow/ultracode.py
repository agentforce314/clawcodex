"""The ``ultracode`` workflow-authoring trigger (workflow-engine §4.1).

Two entry points nudge the model to **author a workflow** (via the Workflow
tool) instead of working turn by turn:

* the keyword ``ultracode`` in a prompt — this turn only, and
* ``/effort ultracode`` — a session-long auto-orchestration mode.

Both are gated by :func:`is_workflows_enabled` (§4.8: the keyword no-ops and the
``/effort`` option disappears when workflows are off). Session state is
process-global — the same shape as :mod:`src.utils.message_queue_manager` — so
the REPL chat seam and the ``/effort`` command share it without plumbing.

This module is pure (no console, no model call); the REPL appends
:func:`ultracode_reminder_for` to the user turn, and the model decides whether to
launch a workflow. Python's effort pipeline is inert, so ``/effort ultracode``
contributes the **orchestration mode**, not a reasoning level.
"""

from __future__ import annotations

import re

from src.workflow.gating import is_workflows_enabled

# Standalone, case-insensitive keyword (won't fire on substrings like "ultracoder").
_KEYWORD_RE = re.compile(r"\bultracode\b", re.IGNORECASE)

# Process-global session toggle (set by ``/effort ultracode``).
_session_on = False


def prompt_requests_ultracode(text: str) -> bool:
    """Whether ``text`` contains the standalone ``ultracode`` keyword. Always
    ``False`` when workflows are disabled (§4.8)."""
    if not text or not is_workflows_enabled():
        return False
    return bool(_KEYWORD_RE.search(text))


def set_ultracode_session(on: bool) -> None:
    """Turn the session-long auto-orchestration mode on/off."""
    global _session_on
    _session_on = bool(on)


def is_ultracode_session() -> bool:
    """Whether session mode is on **and** workflows are enabled."""
    return _session_on and is_workflows_enabled()


def reset_ultracode() -> None:
    """Clear the session flag (test/teardown helper)."""
    global _session_on
    _session_on = False


_KEYWORD_REMINDER = (
    "<system-reminder>\n"
    'The user included the keyword "ultracode" in their message — they are '
    "explicitly opting into multi-agent workflow orchestration for THIS task. "
    "Instead of working turn by turn, design and launch a workflow via the "
    "Workflow tool: decompose the task, fan out subagents for the independent "
    "parts, and adversarially verify findings before synthesizing. If the task "
    "is genuinely trivial or purely conversational, you may handle it directly "
    "and briefly say why. This reminder is injected by the harness, not typed by "
    "the user.\n"
    "</system-reminder>"
)

_SESSION_REMINDER = (
    "<system-reminder>\n"
    "Ultracode is on for this session: author and run a workflow (via the "
    "Workflow tool) for every substantive task by default — decompose, fan out "
    "subagents for independent work, and adversarially verify before "
    "synthesizing. For multi-phase work, run several workflows in sequence so "
    "you stay in the loop between phases. Solo only on conversational turns or "
    "trivial mechanical edits. Reset with /effort high.\n"
    "</system-reminder>"
)


def ultracode_reminder_for(text: str) -> str | None:
    """The ultracode ``<system-reminder>`` to append to a user turn, or ``None``.

    Precedence: the keyword in THIS message wins (one-shot keyword reminder);
    otherwise, if session mode is on, the standing session reminder; otherwise
    ``None``. Always ``None`` when workflows are disabled.
    """
    if not is_workflows_enabled():
        return None
    if prompt_requests_ultracode(text):
        return _KEYWORD_REMINDER
    if is_ultracode_session():
        return _SESSION_REMINDER
    return None


__all__ = [
    "prompt_requests_ultracode",
    "set_ultracode_session",
    "is_ultracode_session",
    "reset_ultracode",
    "ultracode_reminder_for",
]
