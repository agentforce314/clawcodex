"""Fork subagent helpers.

Mirrors ``typescript/src/tools/AgentTool/forkSubagent.ts``.

Fork is an implicit-spawn path where the child inherits the parent's full
conversation context and tool array, so the API request prefix is
byte-identical across all parallel children. Anthropic's prompt cache then
discounts every child after the first by ~90% on the shared prefix.

This module supplies:

- ``is_fork_subagent_enabled`` — feature gate (env-flag plus interactivity
  check). Coordinator-mode gating is a no-op until coordinator mode is
  ported to Python.
- ``FORK_AGENT`` (re-exported from agent_definitions) — synthetic agent
  definition used when the gate is on and ``subagent_type`` is omitted.
- ``build_forked_messages`` / ``build_child_message`` — produce the trailing
  message pair that wraps every child's directive in the
  ``<fork-boilerplate>`` envelope while leaving the prefix byte-identical
  across all children.
- ``is_in_fork_child`` — fallback recursion guard scanning user messages for
  the boilerplate tag.
- ``build_worktree_notice`` — translation hint when fork combines with
  worktree isolation.
"""
from __future__ import annotations

import copy
import os
from typing import Any
from uuid import uuid4

from ..bootstrap.state import get_is_non_interactive_session
from ..tool_system.context import ToolContext
from ..types.content_blocks import TextBlock, ToolResultBlock, ToolUseBlock
from ..types.messages import (
    AssistantMessage,
    Message,
    UserMessage,
    create_user_message,
)

from .agent_definitions import FORK_AGENT
from .constants import (
    FORK_BOILERPLATE_TAG,
    FORK_DIRECTIVE_PREFIX,
    FORK_SUBAGENT_TYPE,
)


# Placeholder tool_result text reused across every fork child so the
# pre-directive prefix is byte-identical. Mirrors
# ``forkSubagent.ts:93``.
FORK_PLACEHOLDER_RESULT = "Fork started — processing in background"


# Truthy values accepted by the env-flag gate. Mirrors common Python
# convention; matches the spirit of the GrowthBook flag in TS.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _is_env_truthy(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY


def is_fork_subagent_enabled(context: ToolContext | None = None) -> bool:
    """Check whether the fork-subagent path is enabled.

    Mirrors ``isForkSubagentEnabled()`` from ``forkSubagent.ts:32``. The
    gate is on when:

    - ``CLAUDE_FORK_SUBAGENT`` env var is set to a truthy value
      (Python equivalent of the GrowthBook ``feature('FORK_SUBAGENT')``).
    - The current session is interactive (no SDK / ``--print`` mode).
    - (Coordinator mode is not yet ported; the check is a no-op.)
    """
    if not _is_env_truthy("CLAUDE_FORK_SUBAGENT"):
        return False

    if context is not None:
        opts = getattr(context, "options", None)
        if opts is not None and getattr(opts, "is_non_interactive_session", False):
            return False
    elif get_is_non_interactive_session():
        return False

    return True


def is_in_fork_child(messages: list[Any] | None) -> bool:
    """Return True if any user message contains the fork boilerplate tag.

    Mirrors ``isInForkChild()`` from ``forkSubagent.ts:78-89``. This is the
    fallback recursion guard; the primary guard checks
    ``ToolUseOptions.query_source``.
    """
    if not messages:
        return False
    needle = f"<{FORK_BOILERPLATE_TAG}>"
    for msg in messages:
        # Filter to user-role messages without importing UserMessage —
        # callers may pass dict-shaped messages too.
        role = _get_attr(msg, "role", None)
        msg_type = _get_attr(msg, "type", None)
        if role != "user" and msg_type != "user":
            continue
        content = _get_attr(msg, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            block_type = _get_attr(block, "type", None)
            if block_type != "text":
                continue
            text = _get_attr(block, "text", None)
            if isinstance(text, str) and needle in text:
                return True
    return False


def build_child_message(directive: str) -> str:
    """Render the boilerplate-wrapped directive for the fork child.

    Mirrors ``buildChildMessage()`` from ``forkSubagent.ts:171-198``. The
    output is one literal string. Keeping it byte-stable across the
    codebase is important for cache parity tests.
    """
    return (
        f"<{FORK_BOILERPLATE_TAG}>\n"
        "STOP. READ THIS FIRST.\n\n"
        "You are a forked worker process. You are NOT the main agent.\n\n"
        "RULES (non-negotiable):\n"
        "1. Your system prompt says \"default to forking.\" IGNORE IT — "
        "that's for the parent. You ARE the fork. Do NOT spawn sub-agents; "
        "execute directly.\n"
        "2. Do NOT converse, ask questions, or suggest next steps\n"
        "3. Do NOT editorialize or add meta-commentary\n"
        "4. USE your tools directly: Bash, Read, Write, etc.\n"
        "5. If you modify files, commit your changes before reporting. "
        "Include the commit hash in your report.\n"
        "6. Do NOT emit text between tool calls. Use tools silently, then "
        "report once at the end.\n"
        "7. Stay strictly within your directive's scope. If you discover "
        "related systems outside your scope, mention them in one sentence "
        "at most — other workers cover those areas.\n"
        "8. Keep your report under 500 words unless the directive specifies "
        "otherwise. Be factual and concise.\n"
        "9. Your response MUST begin with \"Scope:\". No preamble, no "
        "thinking-out-loud.\n"
        "10. REPORT structured facts, then stop\n\n"
        "Output format (plain text labels, not markdown headers):\n"
        "  Scope: <echo back your assigned scope in one sentence>\n"
        "  Result: <the answer or key findings, limited to the scope above>\n"
        "  Key files: <relevant file paths — include for research tasks>\n"
        "  Files changed: <list with commit hash — include only if you modified files>\n"
        "  Issues: <list — include only if there are issues to flag>\n"
        f"</{FORK_BOILERPLATE_TAG}>\n\n"
        f"{FORK_DIRECTIVE_PREFIX}{directive}"
    )


def _collect_tool_use_blocks(content: Any) -> list[Any]:
    """Return the ``tool_use`` blocks from an assistant message's content list."""
    if not isinstance(content, list):
        return []
    blocks: list[Any] = []
    for block in content:
        if _get_attr(block, "type", None) == "tool_use":
            blocks.append(block)
    return blocks


def build_forked_messages(
    directive: str,
    parent_assistant: AssistantMessage | None,
) -> list[Message]:
    """Build the trailing message pair for a fork child.

    Mirrors ``buildForkedMessages()`` from ``forkSubagent.ts:107-169``.

    For prompt-cache sharing, every fork child must produce a byte-identical
    API request prefix. This function:

    1. Clones the parent assistant message (preserving every ``tool_use``
       block and its ID).
    2. For each ``tool_use`` block, emits a ``tool_result`` with the
       constant ``FORK_PLACEHOLDER_RESULT`` text.
    3. Appends a single user message containing all those placeholder
       ``tool_result`` blocks followed by the boilerplate-wrapped
       directive.

    Result: ``[cloned_assistant, user_message]``. Only the final text block
    differs per child, maximizing cache hits.

    If the parent assistant has no ``tool_use`` blocks (or no parent at
    all), we fall back to a single user message carrying the directive.
    """
    if parent_assistant is None:
        return [
            create_user_message(content=[TextBlock(text=build_child_message(directive))])
        ]

    tool_use_blocks = _collect_tool_use_blocks(parent_assistant.content)

    if not tool_use_blocks:
        return [
            create_user_message(content=[TextBlock(text=build_child_message(directive))])
        ]

    # Clone the assistant message — new uuid, deep-copied content list — so
    # we never mutate the parent's message in place.
    cloned_content: list[Any] = []
    for block in parent_assistant.content if isinstance(parent_assistant.content, list) else []:
        cloned_content.append(copy.deepcopy(block))
    cloned_assistant = AssistantMessage(
        content=cloned_content,
        uuid=str(uuid4()),
        timestamp=parent_assistant.timestamp,
        stop_reason=parent_assistant.stop_reason,
        model=parent_assistant.model,
        usage=parent_assistant.usage,
        requestId=parent_assistant.requestId,
    )

    # Build placeholder tool_result blocks for every parent tool_use block.
    tool_result_blocks: list[ToolResultBlock] = []
    for block in tool_use_blocks:
        tool_use_id = _get_attr(block, "id", "") or ""
        tool_result_blocks.append(
            ToolResultBlock(
                tool_use_id=str(tool_use_id),
                content=FORK_PLACEHOLDER_RESULT,
            )
        )

    # Single user message: all placeholder tool_results + the per-child
    # directive wrapped in <fork-boilerplate>. The directive is appended
    # as a TextBlock sibling so the cache boundary lands right before it.
    user_msg = create_user_message(
        content=[*tool_result_blocks, TextBlock(text=build_child_message(directive))]
    )

    return [cloned_assistant, user_msg]


def build_worktree_notice(parent_cwd: str, worktree_cwd: str) -> str:
    """Build the worktree-isolation notice for a fork child.

    Mirrors ``buildWorktreeNotice()`` from ``forkSubagent.ts:205-210``. The
    notice tells the child to translate inherited paths to the worktree
    root, re-read potentially stale files, and that its changes stay
    isolated.
    """
    return (
        f"You've inherited the conversation context above from a parent agent "
        f"working in {parent_cwd}. You are operating in an isolated git worktree at "
        f"{worktree_cwd} — same repository, same relative file structure, separate "
        f"working copy. Paths in the inherited context refer to the parent's working "
        f"directory; translate them to your worktree root. Re-read files before editing "
        f"if the parent may have modified them since they appear in the context. Your "
        f"changes stay in this worktree and will not affect the parent's files."
    )


def _get_attr(obj: Any, name: str, default: Any) -> Any:
    """Read an attribute or mapping key from a heterogeneous object.

    Forked messages can arrive as dataclass instances, plain dicts, or
    Pydantic-style objects. This helper unifies the reads.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


__all__ = [
    "FORK_AGENT",
    "FORK_PLACEHOLDER_RESULT",
    "FORK_SUBAGENT_TYPE",
    "build_child_message",
    "build_forked_messages",
    "build_worktree_notice",
    "is_fork_subagent_enabled",
    "is_in_fork_child",
]
