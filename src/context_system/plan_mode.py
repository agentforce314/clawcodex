"""Plan-mode conversation attachments.

Ports the plan-mode attachment pipeline:

* ``getPlanModeAttachments`` + ``getPlanModeExitAttachment``
  (``typescript/src/utils/attachments.ts:1132-1274``) — cadence (first turn
  always, then every ``TURNS_BETWEEN_ATTACHMENTS`` human turns; full text on
  the 1st/6th/11th… attachment since the last exit, sparse otherwise;
  one-time re-entry and exit attachments).
* The attachment texts (``typescript/src/utils/messages.ts:3233-3423`` and
  ``:3838-3871``) — the 5-phase workflow (non-interview arm,
  ``PLAN_PHASE4_CONTROL``), sparse reminder, re-entry, sub-agent variant and
  exit texts, verbatim with the port's tool/agent names (all identical to the
  reference names).
* Agent-count knobs (``typescript/src/utils/planModeV2.ts:5-43``) — env
  overrides honored; the port uses the default arm (1 plan agent, 3 explore
  agents; no subscription tiers).

NOT ported (my-docs/plan-mode/plan-mode-port-design.md §3.8): the
interview-phase arm and the pewter-ledger Phase-4 experiment arms (GrowthBook
experiments; control arm only).

Persistence contract: TS attachment messages live in the transcript, so the
cadence scan runs over message history. The port persists these as plain
``<system-reminder>``-wrapped meta user messages (via the caller's
``on_attachment``), and the scan discriminates by content markers:

* plan_mode attachment  → text starts with ``<system-reminder>`` and contains
  ``Plan mode is active`` (full) or ``Plan mode still active`` (sparse);
* re-entry attachment   → contains ``## Re-entering Plan Mode``;
* exit attachment       → contains ``## Exited Plan Mode``;
* human turn            → user message with NO tool_result blocks whose text
  does NOT start with ``<system-reminder>`` (the analog of TS ``!isMeta &&
  !hasToolResultContent``, attachments.ts:1144-1151).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from src.bootstrap.state import (
    has_exited_plan_mode_in_session,
    needs_plan_mode_exit_attachment,
    set_has_exited_plan_mode,
    set_needs_plan_mode_exit_attachment,
)
from src.utils.plans import get_plan, get_plan_file_path

logger = logging.getLogger(__name__)

# typescript/src/utils/attachments.ts:260-263
TURNS_BETWEEN_ATTACHMENTS = 5
FULL_REMINDER_EVERY_N_ATTACHMENTS = 5

_SYSTEM_REMINDER_PREFIX = "<system-reminder>"
_FULL_MARKER = "Plan mode is active"
_SPARSE_MARKER = "Plan mode still active"
_REENTRY_MARKER = "## Re-entering Plan Mode"
_EXIT_MARKER = "## Exited Plan Mode"


# ---------------------------------------------------------------------------
# Agent-count knobs (typescript/src/utils/planModeV2.ts:5-43)
# ---------------------------------------------------------------------------


def get_plan_mode_agent_count() -> int:
    """Plan-agent parallelism (``getPlanModeV2AgentCount``). Default arm = 1."""
    raw = os.environ.get("CLAUDE_CODE_PLAN_V2_AGENT_COUNT")
    if raw:
        try:
            count = int(raw)
            if 0 < count <= 10:
                return count
        except ValueError:
            pass
    return 1


def get_plan_mode_explore_agent_count() -> int:
    """Explore-agent parallelism (``getPlanModeV2ExploreAgentCount``) = 3."""
    raw = os.environ.get("CLAUDE_CODE_PLAN_V2_EXPLORE_AGENT_COUNT")
    if raw:
        try:
            count = int(raw)
            if 0 < count <= 10:
                return count
        except ValueError:
            pass
    return 3


# ---------------------------------------------------------------------------
# Attachment texts (typescript/src/utils/messages.ts)
# ---------------------------------------------------------------------------

# messages.ts:3162-3169 — PLAN_PHASE4_CONTROL (the control experiment arm).
PLAN_PHASE4_CONTROL = """### Phase 4: Final Plan
Goal: Write your final plan to the plan file (the only file you can edit).
- Begin with a **Context** section: explain why this change is being made — the problem or need it addresses, what prompted it, and the intended outcome
- Include only your recommended approach, not all alternatives
- Ensure that the plan file is concise enough to scan quickly, but detailed enough to execute effectively
- Include the paths of critical files to be modified
- Reference existing functions and utilities you found that should be reused, with their file paths
- Include a verification section describing how to test the changes end-to-end (run the code, use MCP tools, run tests)"""


def _plan_file_info(plan_file_path: str, plan_exists: bool) -> str:
    # messages.ts:3229-3231 (Edit/Write are the port's FileEdit/FileWrite names)
    if plan_exists:
        return (
            f"A plan file already exists at {plan_file_path}. You can read it "
            "and make incremental edits using the Edit tool."
        )
    return (
        f"No plan file exists yet. You should create your plan at "
        f"{plan_file_path} using the Write tool."
    )


def build_plan_mode_full_text(plan_file_path: str, plan_exists: bool) -> str:
    """The full 5-phase plan-mode instructions (messages.ts:3233-3298)."""
    agent_count = get_plan_mode_agent_count()
    explore_agent_count = get_plan_mode_explore_agent_count()

    multi_agent_section = (
        f"""- **Multiple agents**: Use up to {agent_count} agents for complex tasks that benefit from different perspectives

Examples of when to use multiple agents:
- The task touches multiple parts of the codebase
- It's a large refactor or architectural change
- There are many edge cases to consider
- You'd benefit from exploring different approaches

Example perspectives by task type:
- New feature: simplicity vs performance vs maintainability
- Bug fix: root cause vs workaround vs prevention
- Refactoring: minimal change vs clean architecture
"""
        if agent_count > 1
        else ""
    )

    return f"""Plan mode is active. The user indicated that they do not want you to execute yet -- you MUST NOT make any edits (with the exception of the plan file mentioned below), run any non-readonly tools (including changing configs or making commits), or otherwise make any changes to the system. This supercedes any other instructions you have received.

## Plan File Info:
{_plan_file_info(plan_file_path, plan_exists)}
You should build your plan incrementally by writing to or editing this file. NOTE that this is the only file you are allowed to edit - other than this you are only allowed to take READ-ONLY actions.

## Plan Workflow

### Phase 1: Initial Understanding
Goal: Gain a comprehensive understanding of the user's request by reading through code and asking them questions. Critical: In this phase you should only use the Explore subagent type.

1. Focus on understanding the user's request and the code associated with their request. Actively search for existing functions, utilities, and patterns that can be reused — avoid proposing new code when suitable implementations already exist.

2. **Launch up to {explore_agent_count} Explore agents IN PARALLEL** (single message, multiple tool calls) to efficiently explore the codebase.
   - Use 1 agent when the task is isolated to known files, the user provided specific file paths, or you're making a small targeted change.
   - Use multiple agents when: the scope is uncertain, multiple areas of the codebase are involved, or you need to understand existing patterns before planning.
   - Quality over quantity - {explore_agent_count} agents maximum, but you should try to use the minimum number of agents necessary (usually just 1)
   - If using multiple agents: Provide each agent with a specific search focus or area to explore. Example: One agent searches for existing implementations, another explores related components, a third investigating testing patterns

### Phase 2: Design
Goal: Design an implementation approach.

Launch Plan agent(s) to design the implementation based on the user's intent and your exploration results from Phase 1.

You can launch up to {agent_count} agent(s) in parallel.

**Guidelines:**
- **Default**: Launch at least 1 Plan agent for most tasks - it helps validate your understanding and consider alternatives
- **Skip agents**: Only for truly trivial tasks (typo fixes, single-line changes, simple renames)
{multi_agent_section}
In the agent prompt:
- Provide comprehensive background context from Phase 1 exploration including filenames and code path traces
- Describe requirements and constraints
- Request a detailed implementation plan

### Phase 3: Review
Goal: Review the plan(s) from Phase 2 and ensure alignment with the user's intentions.
1. Read the critical files identified by agents to deepen your understanding
2. Ensure that the plans align with the user's original request
3. Use AskUserQuestion to clarify any remaining questions with the user

{PLAN_PHASE4_CONTROL}

### Phase 5: Call ExitPlanMode
At the very end of your turn, once you have asked the user questions and are happy with your final plan file - you should always call ExitPlanMode to indicate to the user that you are done planning.
This is critical - your turn should only end with either using the AskUserQuestion tool OR calling ExitPlanMode. Do not stop unless it's for these 2 reasons

**Important:** Use AskUserQuestion ONLY to clarify requirements or choose between approaches. Use ExitPlanMode to request plan approval. Do NOT ask about plan approval in any other way - no text questions, no AskUserQuestion. Phrases like "Is this plan okay?", "Should I proceed?", "How does this plan look?", "Any changes before we start?", or similar MUST use ExitPlanMode.

NOTE: At any point in time through this workflow you should feel free to ask the user questions or clarifications using the AskUserQuestion tool. Don't make large assumptions about user intent. The goal is to present a well researched plan to the user, and tie any loose ends before implementation begins."""


def build_plan_mode_sparse_text(plan_file_path: str) -> str:
    """The sparse reminder (messages.ts:3391-3403, non-interview arm)."""
    return (
        "Plan mode still active (see full instructions earlier in "
        f"conversation). Read-only except plan file ({plan_file_path}). "
        "Follow 5-phase workflow. End turns with AskUserQuestion (for "
        "clarifications) or ExitPlanMode (for plan approval). Never ask "
        "about plan approval via text or AskUserQuestion."
    )


def build_plan_mode_reentry_text(plan_file_path: str) -> str:
    """One-time re-entry guidance (messages.ts:3841-3859)."""
    return f"""## Re-entering Plan Mode

You are returning to plan mode after having previously exited it. A plan file exists at {plan_file_path} from your previous planning session.

**Before proceeding with any new planning, you should:**
1. Read the existing plan file to understand what was previously planned
2. Evaluate the user's current request against that plan
3. Decide how to proceed:
   - **Different task**: If the user's request is for a different task—even if it's similar or related—start fresh by overwriting the existing plan
   - **Same task, continuing**: If this is explicitly a continuation or refinement of the exact same task, modify the existing plan while cleaning up outdated or irrelevant sections
4. Continue on with the plan process and most importantly you should always edit the plan file one way or the other before calling ExitPlanMode

Treat this as a fresh planning session. Do not assume the existing plan is relevant without evaluating it first."""


def build_plan_mode_subagent_text(plan_file_path: str, plan_exists: bool) -> str:
    """Sub-agent variant (messages.ts:3405-3423)."""
    if plan_exists:
        info = (
            f"A plan file already exists at {plan_file_path}. You can read it "
            "and make incremental edits using the Edit tool if you need to."
        )
    else:
        info = (
            f"No plan file exists yet. You should create your plan at "
            f"{plan_file_path} using the Write tool if you need to."
        )
    return f"""Plan mode is active. The user indicated that they do not want you to execute yet -- you MUST NOT make any edits, run any non-readonly tools (including changing configs or making commits), or otherwise make any changes to the system. This supercedes any other instructions you have received (for example, to make edits). Instead, you should:

## Plan File Info:
{info}
You should build your plan incrementally by writing to or editing this file. NOTE that this is the only file you are allowed to edit - other than this you are only allowed to take READ-ONLY actions.
Answer the user's query comprehensively, using the AskUserQuestion tool if you need to ask the user clarifying questions. If you do use the AskUserQuestion, make sure to ask all clarifying questions you need to fully understand the user's intent before proceeding."""


def build_plan_mode_exit_text(plan_file_path: str, plan_exists: bool) -> str:
    """One-time exited-plan-mode notice (messages.ts:3860-3871)."""
    plan_reference = (
        f" The plan file is located at {plan_file_path} if you need to "
        "reference it."
        if plan_exists
        else ""
    )
    return f"""## Exited Plan Mode

You have exited plan mode. You can now make edits, run tools, and take actions.{plan_reference}"""


def wrap_in_system_reminder(text: str) -> str:
    """``wrapMessagesInSystemReminder`` analog for a plain text body."""
    return f"<system-reminder>\n{text}\n</system-reminder>"


# ---------------------------------------------------------------------------
# Message-scan helpers (content-based discriminators; see module docstring)
# ---------------------------------------------------------------------------


def _message_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            else:
                if getattr(block, "type", None) == "text":
                    parts.append(str(getattr(block, "text", "")))
        return "".join(parts)
    return ""


def _has_tool_result(message: Any) -> bool:
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if btype == "tool_result":
            return True
    return False


def _role(message: Any) -> str:
    role = getattr(message, "role", None)
    if role is None and isinstance(message, dict):
        role = message.get("role")
    return str(role or "")


def _is_human_turn(message: Any) -> bool:
    """Non-meta, non-tool-result user message (attachments.ts:1144-1151)."""
    if _role(message) != "user":
        return False
    if _has_tool_result(message):
        return False
    return not _message_text(message).lstrip().startswith(_SYSTEM_REMINDER_PREFIX)


def _is_plan_mode_attachment(message: Any) -> bool:
    if _role(message) != "user":
        return False
    text = _message_text(message)
    if not text.lstrip().startswith(_SYSTEM_REMINDER_PREFIX):
        return False
    return _FULL_MARKER in text or _SPARSE_MARKER in text or _REENTRY_MARKER in text


def _is_full_plan_mode_attachment(message: Any) -> bool:
    """Only the plan_mode attachment proper (not reentry) — mirrors TS
    countPlanModeAttachmentsSinceLastExit counting only type 'plan_mode'."""
    if _role(message) != "user":
        return False
    text = _message_text(message)
    if not text.lstrip().startswith(_SYSTEM_REMINDER_PREFIX):
        return False
    return _FULL_MARKER in text or _SPARSE_MARKER in text


def _is_exit_attachment(message: Any) -> bool:
    if _role(message) != "user":
        return False
    text = _message_text(message)
    return text.lstrip().startswith(_SYSTEM_REMINDER_PREFIX) and _EXIT_MARKER in text


def _turns_since_last_attachment(messages: list[Any]) -> tuple[int, bool]:
    """(human turns since the last plan_mode/reentry attachment, found_any).

    Mirrors ``getPlanModeAttachmentTurnCount`` (attachments.ts:1132-1164).
    """
    turns = 0
    for message in reversed(messages):
        if _is_plan_mode_attachment(message):
            return turns, True
        if _is_human_turn(message):
            turns += 1
    return turns, False


def _attachments_since_last_exit(messages: list[Any]) -> int:
    """Mirrors ``countPlanModeAttachmentsSinceLastExit`` (:1170-1185)."""
    count = 0
    for message in reversed(messages):
        if _is_exit_attachment(message):
            break
        if _is_full_plan_mode_attachment(message):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Attachment builders (the getPlanModeAttachments / exit analogs)
# ---------------------------------------------------------------------------


def build_plan_mode_attachments(
    messages: list[Any],
    permission_mode: str,
    agent_id: str | None = None,
) -> list[str]:
    """Return the plan-mode attachment TEXTS due this turn (possibly empty).

    Mirrors ``getPlanModeAttachments`` (attachments.ts:1187-1243): empty
    unless the mode is ``plan``; throttled to every
    :data:`TURNS_BETWEEN_ATTACHMENTS` human turns once one was sent; a
    one-time re-entry text precedes the plan_mode text when the session
    previously exited plan mode and the plan file exists; full text on the
    1st/6th/11th… attachment since the last exit, sparse otherwise.

    Returns raw texts — the caller wraps each via
    :func:`wrap_in_system_reminder` into persisted meta user messages.
    """
    if permission_mode != "plan":
        return []

    if messages:
        turns, found = _turns_since_last_attachment(messages)
        if found and turns < TURNS_BETWEEN_ATTACHMENTS:
            return []

    plan_file_path = str(get_plan_file_path(agent_id))
    existing_plan = get_plan(agent_id)

    attachments: list[str] = []

    if has_exited_plan_mode_in_session() and existing_plan is not None:
        attachments.append(build_plan_mode_reentry_text(plan_file_path))
        set_has_exited_plan_mode(False)  # one-time guidance

    if agent_id:
        attachments.append(
            build_plan_mode_subagent_text(plan_file_path, existing_plan is not None)
        )
        return attachments

    attachment_count = _attachments_since_last_exit(messages or []) + 1
    reminder_full = attachment_count % FULL_REMINDER_EVERY_N_ATTACHMENTS == 1

    if reminder_full:
        attachments.append(
            build_plan_mode_full_text(plan_file_path, existing_plan is not None)
        )
    else:
        attachments.append(build_plan_mode_sparse_text(plan_file_path))

    return attachments


def build_plan_mode_exit_attachment(
    permission_mode: str,
    agent_id: str | None = None,
) -> list[str]:
    """One-time plan_mode_exit text (``getPlanModeExitAttachment``, :1249-1274)."""
    if not needs_plan_mode_exit_attachment():
        return []

    if permission_mode == "plan":
        set_needs_plan_mode_exit_attachment(False)
        return []

    set_needs_plan_mode_exit_attachment(False)

    plan_file_path = str(get_plan_file_path(agent_id))
    plan_exists = get_plan(agent_id) is not None
    return [build_plan_mode_exit_text(plan_file_path, plan_exists)]
