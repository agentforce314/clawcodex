"""Advisor tool integration — Python port of typescript/src/utils/advisor.ts.

The advisor is a server-side tool: when the model emits an advisor
``server_tool_use`` block, the Anthropic API runs a stronger reviewer model
on the conversation so far and returns an ``advisor_tool_result`` block.
The client never executes anything — its only responsibilities are:

1. opt the request into the ``advisor-tool-2026-03-01`` beta,
2. declare the advisor tool schema in ``tools[]`` (cache-preserving append),
3. inject ``ADVISOR_TOOL_INSTRUCTIONS`` into the system prompt,
4. preserve the resulting blocks in conversation history,
5. strip them on requests that won't carry the beta header (the API would
   400 otherwise).

Provider gating is strict: any non-first-party Anthropic provider
(Bedrock/Vertex shims, OpenAI-compat, Gemini, etc.) MUST NOT see the header
or the schema — 3P endpoints 400 on the unknown tool type.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from src.providers.base import BaseProvider


# Wire-format constants — these strings are load-bearing for API parity with
# the TypeScript reference. Do NOT edit without coordinating a matching change
# in typescript/src/constants/betas.ts and typescript/src/utils/advisor.ts.
ADVISOR_BETA_HEADER = "advisor-tool-2026-03-01"
ADVISOR_TOOL_TYPE = "advisor_20260301"
ADVISOR_TOOL_NAME = "advisor"


# Byte-for-byte copy of typescript/src/utils/advisor.ts:130-145. The prompt
# IS the "when to invoke" policy — drift here changes model behavior.
ADVISOR_TOOL_INSTRUCTIONS = """# Advisor Tool

You have access to an `advisor` tool backed by a stronger reviewer model. It takes NO parameters -- when you call it, your entire conversation history is automatically forwarded. The advisor sees the task, every tool call you've made, every result you've seen.

Call advisor BEFORE substantive work -- before writing code, before committing to an interpretation, before building on an assumption. If the task requires orientation first (finding files, reading code, seeing what's there), do that, then call advisor. Orientation is not substantive work. Writing, editing, and declaring an answer are.

Also call advisor:
- When you believe the task is complete. BEFORE this call, make your deliverable durable: write the file, stage the change, save the result. The advisor call takes time; if the session ends during it, a durable result persists and an unwritten one doesn't.
- When stuck -- errors recurring, approach not converging, results that don't fit.
- When considering a change of approach.

On tasks longer than a few steps, call advisor at least once before committing to an approach and once before declaring done. On short reactive tasks where the next action is dictated by tool output you just read, you don't need to keep calling -- the advisor adds most of its value on the first call, before the approach crystallizes.

Give the advice serious weight. If you follow a step and it fails empirically, or you have primary-source evidence that contradicts a specific claim (the file says X, the code does Y), adapt. A passing self-test is not evidence the advice is wrong -- it's evidence your test doesn't check what the advice is checking.

If you've already retrieved data pointing one way and the advisor points another: don't silently switch. Surface the conflict in one more advisor call -- \"I found X, you suggest Y, which constraint breaks the tie?\" The advisor saw your evidence but may have underweighted it; a reconcile call is cheaper than committing to the wrong branch."""


_DISABLE_ENV = "CLAUDE_CODE_DISABLE_ADVISOR_TOOL"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_ADVISOR_PLACEHOLDER_TEXT = "[Advisor response]"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def model_supports_advisor(model: str | None) -> bool:
    """Whether the main-loop model can call the advisor tool.

    Mirror of TS ``modelSupportsAdvisor`` at typescript/src/utils/advisor.ts:89.
    The USER_TYPE=ant escape hatch matches the TS behavior so internal users
    can dogfood advisor on unreleased model strings.
    """
    m = (model or "").lower()
    return (
        "opus-4-6" in m
        or "sonnet-4-6" in m
        or os.environ.get("USER_TYPE") == "ant"
    )


def is_valid_advisor_model(model: str | None) -> bool:
    """Whether a model string is allowed in the ``model`` field of the
    advisor tool schema. Identical predicate to ``model_supports_advisor``
    in the TS reference (typescript/src/utils/advisor.ts:99).
    """
    m = (model or "").lower()
    return (
        "opus-4-6" in m
        or "sonnet-4-6" in m
        or os.environ.get("USER_TYPE") == "ant"
    )


def is_advisor_enabled(provider: "BaseProvider | None") -> bool:
    """Whether the current process+provider may carry the advisor beta header.

    Env-disable shortcut beats provider check. Without a provider (e.g. a
    pre-startup query about command availability) we cannot know what
    endpoint we'll talk to, so we conservatively return False.
    """
    if _env_truthy(_DISABLE_ENV):
        return False
    if provider is None:
        return False
    # Local import to avoid a top-level cycle: cache_state may import from
    # providers in the future and we don't want to lock that.
    from src.state.cache_state import is_first_party_provider
    return is_first_party_provider(provider)


def can_user_configure_advisor(provider: "BaseProvider | None" = None) -> bool:
    """Whether the user is allowed to configure /advisor in this process.

    The slash command needs to render in command lists even before a
    provider is built (e.g. ``--help``), so when ``provider`` is None we
    fall back to the env-disable check only. Once a provider exists, we
    additionally require it to be first-party Anthropic; otherwise
    /advisor would silently no-op every request and the UI would lie.
    """
    if _env_truthy(_DISABLE_ENV):
        return False
    if provider is None:
        return True
    return is_advisor_enabled(provider)


def _block_field(block: Any, key: str) -> Any:
    """Read ``block[key]`` whether ``block`` is a dict-like or attr-style object."""
    if isinstance(block, Mapping):
        return block.get(key)
    return getattr(block, key, None)


def is_advisor_block(block: Any) -> bool:
    """Detect an advisor server-tool-use or its result block.

    Mirror of TS ``isAdvisorBlock`` at typescript/src/utils/advisor.ts:36.
    Accepts both API-shape dicts and typed SDK objects.
    """
    if block is None:
        return False
    bt = _block_field(block, "type")
    if bt == "advisor_tool_result":
        return True
    if bt == "server_tool_use":
        return _block_field(block, "name") == ADVISOR_TOOL_NAME
    return False


def build_advisor_tool_schema(model: str) -> dict[str, Any]:
    """Return the ``tools[]`` entry that opts a request into the advisor.

    The shape mirrors typescript/src/services/api/claude.ts:1417 — the API
    expects a server tool with the dated type discriminator, a literal
    name of ``advisor``, and the chosen advisor model in ``model``.
    """
    return {
        "type": ADVISOR_TOOL_TYPE,
        "name": ADVISOR_TOOL_NAME,
        "model": model,
    }


def _content_is_only_placeholders(content: list[Any]) -> bool:
    """True iff every block is non-substantive (would yield no UI text).

    Matches TS stripAdvisorBlocks's "empty / thinking-only / blank-text"
    fallback condition at typescript/src/utils/messages.ts:5489-5495.
    """
    for block in content:
        bt = _block_field(block, "type")
        if bt in ("thinking", "redacted_thinking"):
            continue
        if bt == "text":
            text = _block_field(block, "text") or ""
            if not text or not str(text).strip():
                continue
            return False
        return False
    return True


def extract_advisor_result_text(content: Any) -> str | None:
    """Pull the human-readable advice text from an advisor_tool_result.

    The advisor's ``content`` field is a tagged union::

        {type: 'advisor_result',         text: '...'}
        {type: 'advisor_redacted_result', encrypted_content: '...'}
        {type: 'advisor_tool_result_error', error_code: '...'}

    Returns the text for ``advisor_result``, ``None`` for the other
    shapes (use ``extract_advisor_error_code`` for the error branch;
    redacted is intentionally opaque to the client).
    """
    if not isinstance(content, Mapping):
        return None
    if content.get("type") == "advisor_result":
        text = content.get("text")
        if isinstance(text, str) and text:
            return text
    return None


def extract_advisor_error_code(content: Any) -> str | None:
    """Pull the error_code string from an advisor_tool_result_error content."""
    if not isinstance(content, Mapping):
        return None
    if content.get("type") == "advisor_tool_result_error":
        code = content.get("error_code")
        if isinstance(code, str):
            return code
    return None


def strip_advisor_blocks(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop advisor blocks from assistant content for API replay.

    Mirror of TS ``stripAdvisorBlocks`` at typescript/src/utils/messages.ts:5478.
    Used on requests that will NOT carry the advisor beta header — the API
    400s on advisor blocks in history when the header is absent.

    When stripping empties an assistant message (or leaves only
    thinking/blank text), inserts a ``[Advisor response]`` placeholder so
    the API doesn't reject empty assistant content.

    The input list is not mutated; messages whose content changed are
    shallow-cloned, others are passed by reference.
    """
    changed = False
    result: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, Mapping) or msg.get("role") != "assistant":
            result.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue
        filtered = [b for b in content if not is_advisor_block(b)]
        if len(filtered) == len(content):
            result.append(msg)
            continue
        changed = True
        if not filtered or _content_is_only_placeholders(filtered):
            filtered = list(filtered) + [
                {"type": "text", "text": _ADVISOR_PLACEHOLDER_TEXT}
            ]
        new_msg = dict(msg)
        new_msg["content"] = filtered
        result.append(new_msg)
    return result if changed else messages


__all__ = [
    "ADVISOR_BETA_HEADER",
    "ADVISOR_TOOL_INSTRUCTIONS",
    "ADVISOR_TOOL_NAME",
    "ADVISOR_TOOL_TYPE",
    "build_advisor_tool_schema",
    "can_user_configure_advisor",
    "extract_advisor_error_code",
    "extract_advisor_result_text",
    "is_advisor_block",
    "is_advisor_enabled",
    "is_valid_advisor_model",
    "model_supports_advisor",
    "strip_advisor_blocks",
]
