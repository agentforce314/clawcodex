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
    'The user\'s message contains the keyword "ultracode": WRITE a reusable '
    'multi-agent workflow script (a "pipeline") for the task in this message and '
    "SAVE it as a slash command — do NOT do the task turn by turn, and do NOT run "
    "the workflow now.\n\n"
    "This is a writing task you do YOURSELF with your Write tool. There is NO "
    '"ultracode" tool, skill, or command to invoke or look up — do NOT use '
    "ToolSearch, do NOT search for a skill, and do NOT call any \"Workflow\" tool. "
    "You are authoring a `.py` file and stopping.\n\n"
    "Steps:\n"
    "1. Read `src/workflow/bundled/deep_research.py` for a real example. A workflow "
    "is sandboxed async Python shaped like:\n\n"
    "    meta = {\n"
    "        \"name\": \"<kebab-name>\",\n"
    "        \"description\": \"<one-line summary>\",\n"
    "        \"phases\": [{\"title\": \"Search\"}, {\"title\": \"Write\"}],\n"
    "    }\n"
    "    phase(\"Search\")\n"
    "    items = await agent(\"<prompt>\", schema={...})\n"
    "    researched = await parallel([agent(f\"<prompt {x}>\") for x in items])\n"
    "    phase(\"Write\")\n"
    "    return await agent(\"<synthesize from the results above>\")\n\n"
    "Use ONLY the injected primitives — `await agent(prompt, schema=...)`, "
    "`parallel`, `pipeline`, `phase`, `log`, `budget` — and end with `return "
    "<result>`. No `import`, no `open`, no clock/random.\n"
    "2. Design the pipeline: decompose into phases, fan out subagents for the "
    "independent parts, verify, then synthesize. Give `meta.description` a clear "
    "one-line summary.\n"
    "3. Pick a short kebab-case name and Write the script to "
    "`.clawcodex/workflows/<name>.py`. The filename stem becomes the command name.\n"
    "4. Then STOP. Reply in two or three lines: confirm the saved file and tell the "
    "user to run it with `/<name>` (it runs in the background like /deep-research).\n\n"
    "Write the file and stop — do NOT run it. This reminder is injected by the "
    "harness, not typed by the user.\n"
    "</system-reminder>"
)

_SESSION_REMINDER = (
    "<system-reminder>\n"
    "Ultracode is on for this session: when a task would benefit from a reusable "
    "multi-agent pipeline, AUTHOR a workflow for it and SAVE it as a `/<name>` "
    "command rather than doing it turn by turn. Write a sandboxed-Python workflow "
    "script (see `src/workflow/bundled/deep_research.py` for the format: a `meta` "
    "dict + phases, then a body using `await agent()`/`parallel`/`pipeline`/"
    "`phase`/`log`) to `.clawcodex/workflows/<name>.py` with your Write tool, then "
    "tell the user to run it with `/<name>`. Do NOT invoke any \"ultracode\" or "
    "\"Workflow\" tool and do NOT run it inline. Solo only on conversational turns "
    "or trivial edits. Reset with /effort high.\n"
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
