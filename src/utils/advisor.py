"""Advisor tool integration.

There are TWO execution modes:

**Server-side** (Anthropic 1P only — Python port of TS ``advisor.ts``):
The model emits a ``server_tool_use(name=advisor)`` block; the Anthropic
API runs a stronger reviewer model on the conversation so far and inlines
an ``advisor_tool_result`` block into the same response. The client only:

1. opts the request into the ``advisor-tool-2026-03-01`` beta,
2. declares the advisor schema in ``tools[]`` (cache-preserving append),
3. injects ``ADVISOR_TOOL_INSTRUCTIONS`` into the system prompt,
4. preserves the resulting blocks in conversation history,
5. strips them on requests that won't carry the beta header.

**Client-side** (any provider — no TS equivalent, Python extension):
The model emits a regular ``tool_use(name="advisor")`` block; the agent
intercepts it, makes a *separate* API call to whatever advisor model the
user configured (could be Anthropic Opus, Gemini, GLM, etc.), and feeds
the response back as a ``tool_result`` block. Two roundtrips per advisor
call but works with any tool-calling main-loop model and any advisor
provider.

The client picks server-side automatically when the main provider is 1P
Anthropic AND the chosen advisor model is a valid server-side target;
otherwise falls back to client-side. Users can also force client-side
even on 1P via the ``advisor_client_mode`` setting (useful for non-
Anthropic advisors on Anthropic main loops, or for transparency).
"""

from __future__ import annotations

import logging
import os
import time
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

    Originally a first-party-only gate (so the slash command wouldn't
    silently no-op on 3P providers). Now that client-side mode lets
    /advisor work on any provider, this only enforces the env-disable
    kill switch. The provider argument is retained for API stability —
    callers (slash-command visibility, /advisor command itself) used
    to pass it. Once an entirely 3P-disabled environment is wanted,
    this is still the chokepoint to add a check.
    """
    return not _env_truthy(_DISABLE_ENV)


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


# ---------------------------------------------------------------------------
# Client-side advisor mode (Python extension — no TS equivalent)
# ---------------------------------------------------------------------------

# Activation mode for a given turn — picked by ``decide_advisor_mode``.
ADVISOR_MODE_INACTIVE = "inactive"
ADVISOR_MODE_SERVER_SIDE = "server_side"
ADVISOR_MODE_CLIENT_SIDE = "client_side"


# The client-side advisor's *own* system prompt. Sent to the advisor
# provider as the system message. Kept short — the conversation we
# forward is the substantive context, and the prompt's role is just to
# orient the advisor model on what kind of feedback to produce.
CLIENT_ADVISOR_SYSTEM_PROMPT = """You are a senior reviewer being consulted by a junior worker model that has paused mid-task to get your judgment. The conversation below is everything the worker has seen and done so far: the user's task, every tool call the worker made, every result.

# CRITICAL — read these before responding

1. **DO NOT restate, summarize, or echo back the worker's plan.** They already know what they're doing. Restating is worse than useless — it wastes their context window and your turn. If you find yourself writing "your plan is to ..." STOP and delete that paragraph.

2. **DO NOT respond in the worker's voice.** Never write "I will...", "My plan is...", "Let me...". You are NOT the worker. You are the reviewer talking AT the worker. Use "you" / "your" / "the plan" — second-person, never first-person.

3. **Your only value is the gap.** Tell the worker what they CAN'T see — what they missed, what's risky, what better approach exists. Anything the worker already wrote in their own message is something they already know — never repeat it.

# Output shape

Reply in this exact format. No preamble. No sign-off.

**Gaps:** 1-3 short bullets on what's missing, wrong, or unclear in the plan. If nothing material → write "Nothing material missing." (one bullet, no more).

**Risks:** 1-3 short bullets on what could break, surprise, or bite later. Concrete failure modes only — not generic disclaimers.

**Do next:** ONE sentence. The single most-important next action.

If the worker's whole approach is fundamentally wrong, skip the format and write a short "Stop — rethink: ..." paragraph instead, then the one-sentence next action.

# Style

Terse. Concrete. Write directly. No hedging ("you might want to consider"), no flattery ("good plan, but..."), no disclaimers ("as an AI..."), no "I think". Cut every sentence that isn't load-bearing."""


# Tool-use shape that the main-loop model sees in client-side mode.
# Regular ``tool_use``-style entry (NOT ``server_tool_use``) with empty
# parameters — the advisor takes the full conversation implicitly, the
# model just invokes the tool with no args. The dispatcher (see
# ``src/tool_system/tools/advisor.py``) maps the call to
# ``execute_client_advisor``.
CLIENT_ADVISOR_TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def build_client_advisor_tool_schema() -> dict[str, Any]:
    """``tools[]`` entry that exposes the client-side advisor to the model.

    Regular tool-use shape (NOT ``server_tool_use``) so any provider that
    supports tool calling can route the invocation. Description doubles
    as a one-line "what does this do" for the model — the full policy
    lives in ``ADVISOR_TOOL_INSTRUCTIONS`` (system prompt).
    """
    return {
        "name": ADVISOR_TOOL_NAME,
        "description": (
            "Consult a stronger reviewer model. Takes no parameters; the "
            "current conversation is forwarded automatically. Returns the "
            "reviewer's advice as text."
        ),
        "input_schema": dict(CLIENT_ADVISOR_TOOL_INPUT_SCHEMA),
    }


def decide_advisor_mode(
    provider: "BaseProvider | None",
    main_loop_model: str | None,
    advisor_model: str | None,
    *,
    force_client_mode: bool = False,
    advisor_provider: str | None = None,
    advisor_enabled: bool = True,
) -> str:
    """Pick activation mode for the upcoming turn.

    Returns one of:
    * ``ADVISOR_MODE_INACTIVE`` — no advisor on this request.
    * ``ADVISOR_MODE_SERVER_SIDE`` — Anthropic 1P beta path.
    * ``ADVISOR_MODE_CLIENT_SIDE`` — separate provider call from the
      tool dispatcher.

    Decision tree:

    1. ``advisor_model`` empty / env-disabled → INACTIVE.
    2. ``advisor_provider`` empty → INACTIVE (the multi-provider
       rewrite requires explicit provider; name-based inference was
       removed because the same model name can sit behind multiple
       providers).
    3. ``force_client_mode`` set → CLIENT_SIDE iff the advisor
       provider is a configured key; else INACTIVE.
    4. 1P + main_loop_model supports server advisor + advisor_model is
       a valid server target + advisor_provider == "anthropic" →
       SERVER_SIDE (the optimized path; one roundtrip, prompt-cache
       friendly). Server-side only makes sense when the advisor call
       lands on the same Anthropic API as the main loop.
    5. Otherwise, if the advisor provider is configured → CLIENT_SIDE.
    6. Else INACTIVE — the configured advisor can't be reached.

    ``advisor_enabled`` is the master switch (settings ``advisor_enabled``,
    default False in production): when False the advisor is INACTIVE regardless
    of model/provider. The parameter defaults True so direct callers (the
    activation truth-table tests) keep their behavior; production call sites pass
    ``get_settings().advisor_enabled``.
    """
    if not advisor_enabled:
        return ADVISOR_MODE_INACTIVE
    if _env_truthy(_DISABLE_ENV):
        return ADVISOR_MODE_INACTIVE
    if not advisor_model:
        return ADVISOR_MODE_INACTIVE
    if not advisor_provider:
        return ADVISOR_MODE_INACTIVE

    # Provider must be configured in ~/.clawcodex/config.json. Use the
    # provider class registry as the lightweight check (a key with no
    # class registered can't be instantiated anyway).
    advisor_routes = False
    try:
        from src.providers import get_provider_class
        get_provider_class(advisor_provider)
        advisor_routes = True
    except Exception:
        advisor_routes = False

    if force_client_mode:
        return ADVISOR_MODE_CLIENT_SIDE if advisor_routes else ADVISOR_MODE_INACTIVE

    if (
        provider is not None
        and is_advisor_enabled(provider)
        and model_supports_advisor(main_loop_model)
        and is_valid_advisor_model(advisor_model)
        and advisor_provider == "anthropic"
    ):
        return ADVISOR_MODE_SERVER_SIDE

    return ADVISOR_MODE_CLIENT_SIDE if advisor_routes else ADVISOR_MODE_INACTIVE


# Human-readable mode labels for status displays.
_ADVISOR_MODE_LABELS: dict[str, str] = {
    ADVISOR_MODE_SERVER_SIDE: "server",
    ADVISOR_MODE_CLIENT_SIDE: "client",
    ADVISOR_MODE_INACTIVE: "inactive",
}


def format_advisor_status(
    provider: "BaseProvider | None",
    main_loop_model: str | None,
) -> str | None:
    """Render a compact one-segment status string for the bottom toolbar.

    Returns e.g. ``"advisor: opus-4-7 (client)"`` when an advisor is
    configured, or ``None`` when it isn't (caller omits the segment
    entirely). The mode label comes from :func:`decide_advisor_mode`
    so the display reflects what the next request will actually do —
    a stale configuration under an unsupported main loop shows
    ``"(inactive)"`` rather than silently lying about the state.

    Formats the advisor status segment uniformly for whatever status
    surface renders it.

    Any unexpected failure (settings cache contention, future provider
    that throws on inspection) returns ``None`` — the status row must
    never be the thing that breaks the input prompt.
    """
    try:
        from src.settings.settings import get_settings
        from src.models.model import canonical_model_name
    except Exception:
        return None
    try:
        settings = get_settings()
        # Master switch off → no advisor segment at all (it isn't running).
        if not bool(getattr(settings, "advisor_enabled", False)):
            return None
        advisor_model = (getattr(settings, "advisor_model", "") or "").strip()
        advisor_provider = (getattr(settings, "advisor_provider", "") or "").strip()
        if not advisor_model:
            return None
        canonical = canonical_model_name(advisor_model)
        force_client = bool(getattr(settings, "advisor_client_mode", False))
        mode = decide_advisor_mode(
            provider,
            main_loop_model,
            canonical,
            force_client_mode=force_client,
            advisor_provider=advisor_provider,
            advisor_enabled=True,  # already checked above
        )
    except Exception:
        return None
    label = _ADVISOR_MODE_LABELS.get(mode, mode)
    # Strip the ``claude-`` family prefix for compactness; everyone
    # reading the toolbar already knows what brand is in play. Other
    # provider prefixes (``gemini-``, ``zai/``, etc.) keep their full
    # name because the brand IS the disambiguator there.
    display = canonical
    if display.lower().startswith("claude-"):
        display = display[len("claude-") :]
    # Qualify with the provider so the user can spot a misroute
    # (e.g. accidentally hitting api.anthropic.com instead of litellm).
    # Falls back to "?" when provider is missing — partial config,
    # already covered by the INACTIVE mode label.
    # Critic S1: colon-separated to match the /advisor slash command
    # input syntax. Lets the user copy the bar value into /advisor
    # verbatim. Splits unambiguously on the first colon even when the
    # model name itself contains slashes (openrouter convention).
    qualified = f"{advisor_provider or '?'}:{display}"
    return f"advisor: {qualified} ({label})"


CLIENT_ADVISOR_PROMPT_SUFFIX = (
    "Now produce advice in the format your system prompt specified "
    "(Gaps / Risks / Do next). DO NOT restate or paraphrase the plan "
    "above — the worker already wrote it. Tell them only what they "
    "can't see: what's missing, what's risky, what to do next. If "
    "their plan is already solid, say 'Nothing material missing.' "
    "and recommend the single next action."
)


def _tool_use_to_text(block: dict[str, Any]) -> str:
    """Render a ``tool_use`` / ``server_tool_use`` / ``mcp_tool_use`` block
    as a single-line text summary the advisor can read without needing
    the underlying tool schemas."""
    import json as _json
    name = block.get("name", "?")
    raw_input = block.get("input", {})
    try:
        rendered = _json.dumps(raw_input, ensure_ascii=False, default=str)
    except Exception:
        rendered = str(raw_input)
    if len(rendered) > 240:
        rendered = rendered[:237] + "..."
    return f"[Tool call: {name}({rendered})]"


def _tool_result_to_text(block: dict[str, Any]) -> str:
    """Render a ``tool_result`` block as a single-line text summary."""
    content = block.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for sub in content:
            if isinstance(sub, dict):
                if isinstance(sub.get("text"), str):
                    parts.append(sub["text"])
                elif sub.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(f"[{sub.get('type', '?')}]")
        text = "\n".join(parts)
    elif content is None:
        text = ""
    else:
        text = str(content)
    if len(text) > 1200:
        text = text[:1197] + "..."
    is_error = block.get("is_error")
    label = "Tool error" if is_error else "Tool result"
    return f"[{label}: {text}]"


def _flatten_content_for_advisor(content: Any) -> str:
    """Reduce a message's content to plain text suitable for the advisor.

    The forwarded conversation must be tool-schema-free (the advisor is
    called with ``tools=[]`` — proxies reject ``tool_use``/``tool_result``
    blocks without a matching ``tools=`` array). Replace them with text
    summaries that preserve the information ("the worker ran Bash with
    ls", "the result was these files") without the typed structure.

    Drops ``thinking`` / ``redacted_thinking`` blocks — the advisor
    doesn't need the worker's chain-of-thought as separate signal.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content is not None else ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        bt = block.get("type")
        if bt == "text":
            t = block.get("text")
            if isinstance(t, str) and t.strip():
                parts.append(t)
        elif bt in ("tool_use", "server_tool_use", "mcp_tool_use"):
            # Drop the worker's OWN advisor call — the marker would
            # invite the reviewer to "answer the call" in the worker's
            # voice rather than give fresh advice. The reviewer
            # already knows it IS the advisor.
            if block.get("name") == ADVISOR_TOOL_NAME:
                continue
            parts.append(_tool_use_to_text(block))
        elif bt == "tool_result":
            parts.append(_tool_result_to_text(block))
        elif bt in ("thinking", "redacted_thinking"):
            continue
        elif bt == "image":
            parts.append("[image attachment]")
        else:
            # Unknown block — preserve the type signal but no payload.
            parts.append(f"[{bt}]")
    return "\n".join(parts).strip()


_ADVISOR_PAIRING_CRUFT = (
    "[Tool result missing due to internal error]",
    "[Tool use interrupted]",
)


def _is_advisor_pairing_cruft(text: str) -> bool:
    """True if the message is just orphan-pairing-pass injected cruft.

    ``normalize_messages_for_api`` runs ``ensure_tool_result_pairing``
    which, on the in-flight worker advisor tool_use, injects a
    synthetic tool_result UserMessage with a "[Tool result missing
    due to internal error]" placeholder. That cruft is meaningful to
    the API (keeps tool_use/tool_result pairing valid) but
    counterproductive to the advisor (looks like a real tool failure
    the advisor should react to). Strip it from the forwarded view.
    """
    t = text.strip()
    return any(cruft in t for cruft in _ADVISOR_PAIRING_CRUFT)


def build_advisor_forwarded_messages(
    messages: list[Any],
) -> list[dict[str, Any]]:
    """Normalize + strip + flatten messages before forwarding to the
    client-side advisor.

    Three transforms:

    1. **Strip prior advisor consultations** — the reviewer shouldn't
       see its own past advice as part of the worker's history; that
       would let the advisor build on its own (potentially wrong)
       earlier output.
    2. **Flatten tool_use/tool_result blocks to text** — the advisor is
       called with ``tools=[]``, but proxies (Vertex-fronted Anthropic
       in particular) reject ``tool_use``/``tool_result`` blocks when
       no ``tools=`` array is sent. Plain text summaries preserve the
       "what happened" information while satisfying the API contract.
    3. **Ensure ends-with-user** — the advisor is invoked from inside
       an assistant ``tool_use``, so the natural tail is assistant.
       Most LLM APIs reject assistant-prefill; append a synthetic user
       turn asking for advice (doubles as a clear prompt aligned with
       ``CLIENT_ADVISOR_SYSTEM_PROMPT``).

    Returns a plain list of dicts safe to send to any provider.
    """
    # Local imports — same cycle-avoidance reason as elsewhere.
    from src.types.messages import normalize_messages_for_api

    api_messages = normalize_messages_for_api(messages)
    api_messages = strip_advisor_blocks(api_messages)

    flattened: list[dict[str, Any]] = []
    for msg in api_messages:
        if not isinstance(msg, Mapping):
            continue
        role = msg.get("role")
        text = _flatten_content_for_advisor(msg.get("content"))
        if not text:
            continue
        # Drop the orphan-pairing-pass artifact: a synthetic user
        # message containing only "[Tool result missing due to
        # internal error]" wraps the in-flight worker advisor call.
        # It's required for downstream API tool_use/tool_result
        # pairing but tells the advisor "your worker just failed",
        # confusing the response. The worker's own tool_use was
        # already dropped from the flattened content above; the
        # synthetic result has no surviving partner anyway.
        if _is_advisor_pairing_cruft(text):
            continue
        flattened.append({"role": role, "content": text})

    # TWO requirements, and conflating them was a real defect:
    #
    #   (a) the forwarded conversation must END WITH A USER TURN — Vertex-
    #       fronted Anthropic and most proxies reject assistant-prefill; and
    #   (b) that final turn must actually ASK FOR ADVICE, or the reviewer is
    #       handed a bare transcript with no request in it.
    #
    # This used to append the request ONLY when the tail was not already a
    # user turn, which silently made (b) conditional on (a). The worker
    # usually calls the advisor mid-tool-round, so after flattening the tail
    # IS a user turn (a ``[Tool result: ...]`` line) — no request was added,
    # and the reviewer did the natural thing with an unterminated transcript:
    # it CONTINUED it, replying in the worker's own voice with narration and
    # further tool calls instead of a review.
    #
    # Measured on an 89-task terminal-bench run: 54 of 326 answered
    # consultations (16.6%), spread over 45 of 89 tasks, came back as echoed
    # transcript. Each cost a full reviewer call and returned the worker its
    # own words as advice — worse than no advisor at all, because the model
    # is told to weight advice heavily.
    #
    # Appended to the EXISTING user turn rather than added as a second one:
    # consecutive same-role messages are rejected on some wires, and the
    # request belongs with the transcript it refers to.
    if not flattened or flattened[-1].get("role") != "user":
        flattened.append({"role": "user", "content": CLIENT_ADVISOR_PROMPT_SUFFIX})
    elif CLIENT_ADVISOR_PROMPT_SUFFIX not in str(flattened[-1].get("content") or ""):
        tail = dict(flattened[-1])
        existing = str(tail.get("content") or "").rstrip()
        tail["content"] = (
            f"{existing}\n\n{CLIENT_ADVISOR_PROMPT_SUFFIX}" if existing
            else CLIENT_ADVISOR_PROMPT_SUFFIX
        )
        flattened[-1] = tail
    return flattened


# Historical flat output budget for an advisor call, now a FLOOR rather
# than the value (see the budget note in ``execute_client_advisor``).
_ADVISOR_MIN_MAX_TOKENS = 4096

# Ceiling for the OpenAI-compatible wire, where the per-model table is an
# advisory compaction figure rather than a legal request cap. See the budget
# note in ``execute_client_advisor`` for why the two wires differ.
#
# The number is a RULE, not a taste: 32768 is the largest
# ``max_output_tokens`` in the entire Anthropic family, so the ceiling says
# "the OpenAI wire never gets a larger budget than the most generous
# Anthropic model gets". Update it if that family maximum moves, not
# otherwise. It sits far above any advisor critique (~1-2K plus reasoning)
# and far below the outliers it exists to stop (deepseek 384000).
#
# Note it also caps a CLAUDE_CODE_MAX_OUTPUT_TOKENS override on this wire
# (resolve_max_output_tokens consults the env before the table), while the
# Anthropic wire still honours such an override whole. That asymmetry is
# intended: the override is a main-loop budget knob, and the reason to bound
# this wire — untested numbers reaching completions.create() — applies to an
# env-supplied value just as much as to a table one.
_ADVISOR_MAX_OPENAI_WIRE_TOKENS = 32_768

# Transient-failure budget for one consultation. Deliberately far below the
# main loop's DEFAULT_MAX_RETRIES (10): the advisor is an auxiliary call the
# worker is blocked on, so a rate-limited reviewer must degrade to "no advice"
# in seconds rather than stall the turn for minutes. Before this existed a
# single 429 — routine on a subscription bucket shared with an interactive
# session — ended the consultation outright.
_ADVISOR_MAX_ATTEMPTS = 3
_ADVISOR_RETRY_BASE_DELAY = 2.0
_ADVISOR_RETRY_MAX_DELAY = 30.0


def _resolve_advisor_effort() -> str | None:
    """Reasoning-effort level for the advisor's own API call.

    ``advisor_effort`` when the user set one (so the reviewer can be dialled
    independently of the worker), else the session-wide ``effort``. Returns
    ``None`` when neither is set, which leaves the parameter off the wire and
    lets the API apply its own default — same omit-don't-guess contract as
    :func:`~src.query.query.resolve_thinking_effort`.
    """
    try:
        from src.settings.settings import get_settings

        settings = get_settings()
    except Exception:  # noqa: BLE001 — settings must never break the advisor
        return None
    for attr in ("advisor_effort", "effort"):
        value = (getattr(settings, attr, "") or "").strip().lower()
        if value:
            return value
    return None


def _advisor_error_is_retryable(exc: Exception) -> bool:
    """Whether one failed advisor attempt is worth re-issuing.

    Reuses the main loop's classifier so the two agree on what "transient"
    means. The trailing checks are a BACKSTOP, not the primary path:
    ``categorize_retryable_api_error`` already has an explicit overloaded
    lane, so the ``status_code == 529`` arm is unreachable in practice. The
    prose arm still earns its place — it catches an overloaded error raised
    as a bare exception carrying no ``status_code`` at all, which the
    classifier cannot categorise.
    """
    try:
        from src.services.api.errors import (
            categorize_retryable_api_error,
            is_quota_exhausted,
        )

        if is_quota_exhausted(exc):
            return False
        if categorize_retryable_api_error(exc).retryable:
            return True
    except Exception:  # noqa: BLE001 — classifier unavailable: fall through
        pass
    status = getattr(exc, "status_code", None)
    if status == 529:
        return True
    text = str(exc).lower()
    return "overloaded" in text


def _advisor_sleep(delay: float, abort_signal: Any) -> bool:
    """Abort-aware backoff. Returns False if the wait was cut short by an
    abort, so the caller stops retrying instead of sleeping through an ESC."""
    deadline = time.monotonic() + delay
    while time.monotonic() < deadline:
        if abort_signal is not None and getattr(abort_signal, "aborted", False):
            return False
        # ``max(0.0, …)``: the loop condition and this expression read the
        # clock separately, so the remainder can go negative in between —
        # and ``time.sleep`` raises ValueError on a negative argument.
        time.sleep(max(0.0, min(0.25, deadline - time.monotonic())))
    return True


def execute_client_advisor(
    advisor_model: str,
    forwarded_messages: list[dict[str, Any]],
    *,
    advisor_provider: str = "",
    abort_signal: Any = None,
    main_provider: Any = None,
) -> tuple[bool, str, dict[str, int]]:
    """Run one client-side advisor consultation.

    Returns ``(ok, text, usage)``: when ``ok`` is True, ``text`` is the
    advisor's advice; when False, ``text`` is a short error message
    suitable for surfacing as a tool_result with ``is_error=True``.
    ``usage`` is a dict with ``input_tokens`` / ``output_tokens`` keys
    (zero-filled on failure paths). The caller accumulates these into
    a session-level counter so the status bar can show advisor token
    spend separately from the worker's.

    Provider routing (post the multi-provider rewrite): use the
    explicit ``advisor_provider`` key as a lookup into
    ``~/.clawcodex/config.json``'s ``providers`` map and instantiate
    the matching provider class with that entry's api_key + base_url,
    overriding the model. The ``/advisor`` command writes the
    provider key alongside the model so this function never has to
    infer.

    ``main_provider`` is no longer consulted for routing — clawcodex
    is multi-provider, every advisor call says exactly which provider
    it wants. The argument is preserved on the signature for callers
    that pass it for backwards compatibility; it's ignored.

    Network failures, model errors, and missing-config conditions are
    all caught and surfaced as ``(False, "...", {0,0})`` rather than
    raised — a tool that throws inside dispatch kills the turn, but
    a failed advisor consultation should just leave the worker model
    uninformed and let it continue.
    """
    _zero_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    if not advisor_provider:
        return (
            False,
            "Advisor unavailable: advisor_provider is not set. Run "
            "/advisor <provider>:<model> to configure.",
            _zero_usage,
        )
    try:
        from src.config import get_provider_config
        from src.providers import get_provider_class

        try:
            provider_cls = get_provider_class(advisor_provider)
        except Exception:
            return (
                False,
                f"Advisor unavailable: provider {advisor_provider!r} has "
                "no registered Provider class. Check the provider key "
                "in ~/.clawcodex/config.json.",
                _zero_usage,
            )
        try:
            cfg_raw = dict(get_provider_config(advisor_provider))
        except Exception:
            return (
                False,
                f"Advisor unavailable: provider {advisor_provider!r} is "
                "not configured in ~/.clawcodex/config.json.",
                _zero_usage,
            )

        # ``get_provider_config`` returns the raw config dict shape
        # (api_key, base_url, default_model) which doesn't match the
        # Provider ``__init__`` keyword args (api_key, base_url, model).
        # Translate explicitly so unknown keys (default_model, plus any
        # future config fields like extra_headers) don't get forwarded
        # as kwargs and crash the constructor.
        # Resolve the key the way every other call site does: configured
        # ``providers.<name>.api_key`` first, then the provider's known env
        # vars via the secret store. Reading cfg_raw["api_key"] directly
        # meant the advisor was the ONE path that ignored the environment,
        # so an advisor provider whose key lives in ``ZAI_API_KEY`` (how
        # eval containers and plenty of shells supply credentials) got
        # ``api_key=""`` and died on "Missing credentials" — while the same
        # provider worked fine as the main loop.
        #
        # Empty is still a legitimate outcome and must stay non-fatal here:
        # the Anthropic subscription path REQUIRES an empty key to fall
        # through to OAuth (a key would silently outrank it).
        from src.providers import resolve_api_key

        provider = provider_cls(
            api_key=resolve_api_key(advisor_provider, cfg_raw),
            base_url=cfg_raw.get("base_url"),
            model=advisor_model,
        )
    except Exception as e:  # noqa: BLE001 — surface as advisor failure
        return (False, f"Advisor unavailable: failed to construct {advisor_provider!r} provider for {advisor_model!r}: {e}", _zero_usage)

    # System-prompt delivery is provider-specific:
    #   * Anthropic-shaped providers (AnthropicProvider / MinimaxProvider)
    #     expect ``system`` as a top-level kwarg; system-role messages
    #     in the messages array would be rejected by the API.
    #   * OpenAI-compatible providers (and Gemini-via-openai-shim) read
    #     a leading ``{"role": "system", "content": ...}`` message and
    #     ignore the ``system=`` kwarg silently.
    # Detect the provider type to send the right shape — sending both
    # forms blindly would either be ignored (best case) or fail
    # validation (worst case, on Anthropic).
    from src.providers import is_anthropic_wire

    is_anthropic_shape = is_anthropic_wire(provider)

    # Output budget. The advisor used to send a flat 4096, which was fine
    # while it sent no thinking — but thinking tokens are drawn from the
    # SAME max_tokens budget as the reply, so a high-effort reviewer can
    # spend the entire allowance reasoning and come back with
    # ``stop_reason=max_tokens`` and no text at all (surfacing here as the
    # useless "Advisor returned no text content"). Floored at the historical
    # 4096 so this can only ever widen the budget. It's a cap, not a target:
    # an advisor that answers in 300 tokens still costs 300 tokens.
    #
    # The ceiling is WIRE-DEPENDENT, and the two families are not symmetric:
    #
    #   * Anthropic — ``max_output_tokens`` IS the request's max_tokens by
    #     design (resolve_max_output_tokens is exactly what the main loop
    #     sends), so take the table value whole.
    #   * OpenAI-compatible — the main loop deliberately sends NO max_tokens
    #     here, and the table value is ADVISORY on this wire: it is tuned as
    #     an auto-compact reservation, not as a legal request cap (deepseek's
    #     row is 384000, luna's 128000). ``openai_compatible`` DOES forward
    #     max_tokens to completions.create(), so the raw table value really
    #     does reach the wire.
    #
    #     Honest scope: DeepSeek accepts 384000 (probed 2026-08-02, 200 OK),
    #     so this is not a live outage — it is an untested number per
    #     provider across a registry of ~30, where a rejection would be a
    #     400: non-retryable, so the consultation dies on attempt 1 and
    #     degrades SILENTLY (the worker continues and the task can still
    #     score). Clamp to a value large enough that reasoning tokens can't
    #     starve the reply, small enough to stay unremarkable on any wire.
    max_tokens = _ADVISOR_MIN_MAX_TOKENS
    try:
        from src.models.context import resolve_max_output_tokens

        table = int(
            resolve_max_output_tokens(
                None, advisor_model, base_url=cfg_raw.get("base_url")
            )
            or 0
        )
        if not is_anthropic_shape:
            table = min(table, _ADVISOR_MAX_OPENAI_WIRE_TOKENS)
        max_tokens = max(max_tokens, table)
    except Exception:  # noqa: BLE001 — unknown model / bad env falls back
        pass

    call_kwargs: dict[str, Any] = {
        "tools": [],
        "max_tokens": max_tokens,
    }

    # Reasoning effort for the advisor's own call. Without this the
    # reviewer — chosen precisely because it reasons harder than the
    # worker — ran with thinking off and the API's default effort, on
    # both wires. ``advisor_effort`` wins when set so the advisor can be
    # dialled independently of the main loop; otherwise the session-wide
    # ``effort`` applies, matching what the worker is running at.
    effort = _resolve_advisor_effort()

    if is_anthropic_shape:
        # System goes as a BLOCK LIST, not a bare string. This is not a
        # style choice — it is load-bearing on the Claude subscription
        # (OAuth) path, and getting it wrong broke the advisor outright for
        # premium models.
        #
        # ``_prepare_subscription_request`` prepends the "You are Claude
        # Code…" preamble that the subscription endpoint requires. With a
        # STRING it concatenates, producing one blob of
        # ``preamble + "\n\n" + advisor prompt``; with a LIST it inserts the
        # preamble as its own block at index 0. The endpoint only accepts
        # the latter for premium models: wire-probed 2026-08-02 against
        # claude-opus-5 over subscription OAuth, 3/3 per cell —
        #
        #   system=None (bare preamble string) -> 200
        #   system=<string>  (preamble + text) -> 429
        #   system=[<block>] (preamble block + text block) -> 200
        #
        # and the rejection arrives MISLABELLED as
        # ``{"type": "rate_limit_error", "message": "Error"}``, so it reads
        # as capacity and invites a pointless backoff hunt. Haiku accepts
        # the string form, which is why a cheap smoke test misses this.
        # The main loop has always sent blocks (that is what carries the
        # cache_control markers), which is why it works on subscription
        # while this path did not.
        call_kwargs["system"] = [
            {"type": "text", "text": CLIENT_ADVISOR_SYSTEM_PROMPT}
        ]
        request_messages = list(forwarded_messages)
        # Shared with the main loop so the model gates (adaptive-vs-budget
        # thinking, the effort allowlist, the xhigh clamp) can't drift.
        try:
            from src.query.query import build_anthropic_thinking_kwargs

            call_kwargs.update(
                build_anthropic_thinking_kwargs(
                    advisor_model,
                    explicit_effort=effort,
                    max_tokens=max_tokens,
                )
            )
        except Exception:  # noqa: BLE001 — never let this break the call
            logging.getLogger(__name__).debug(
                "advisor thinking kwargs failed", exc_info=True
            )
    else:
        # Prepend the system message; OpenAI-compat will honor it
        # naturally as the first message in the conversation.
        request_messages = [
            {"role": "system", "content": CLIENT_ADVISOR_SYSTEM_PROMPT},
            *forwarded_messages,
        ]
        # NON-Anthropic wire: effort is a top-level ``reasoning_effort``
        # body field, not ``output_config``. ``clamp_xhigh=False`` because
        # the xhigh allowlist is a list of Anthropic model NAMES and matches
        # nothing here — clamping on it silently downgraded every xhigh.
        # Mirrors the equivalent branch in query.py::_call_model_sync,
        # INCLUDING the provider-vocabulary translation: without it a
        # DeepSeek advisor received ``xhigh`` where the main loop sends
        # ``max``, and DeepSeek drops a level it doesn't know and applies
        # its default — silently downgraded, which is the exact bug the
        # normalize hook exists to prevent.
        if effort:
            try:
                from src.query.query import (
                    normalize_effort_for_provider,
                    resolve_thinking_effort,
                )

                resolved = normalize_effort_for_provider(
                    provider,
                    resolve_thinking_effort(
                        effort, advisor_model, clamp_xhigh=False
                    ),
                )
                if resolved is not None:
                    extra_body = dict(call_kwargs.get("extra_body") or {})
                    extra_body["reasoning_effort"] = resolved
                    call_kwargs["extra_body"] = extra_body
            except Exception:  # noqa: BLE001 — never let this break the call
                logging.getLogger(__name__).debug(
                    "advisor reasoning_effort failed", exc_info=True
                )

    # ``chat_stream_response`` is the cross-provider call that accepts
    # ``abort_signal`` uniformly (per BaseProvider) and returns a fully
    # accumulated ChatResponse. The plain ``chat()`` path doesn't accept
    # ``abort_signal`` consistently across providers — passing it as a
    # kwarg would forward an unknown param to the underlying SDK for
    # Anthropic (line 239 of anthropic_provider.py forwards unknown
    # kwargs straight to ``messages.create``). Streaming under the hood
    # but no ``on_text_chunk`` callback — we only need the final text.
    #
    # Bounded retry on transient failures. A consultation used to die on the
    # first 429/5xx/connection blip; on a subscription bucket shared with an
    # interactive session those are routine, so the reviewer was effectively
    # unavailable under exactly the load an eval produces. Non-retryable
    # errors (bad key, quota exhausted, 400) still fail on attempt 1.
    _t0 = time.monotonic()
    response = None
    for attempt in range(1, _ADVISOR_MAX_ATTEMPTS + 1):
        try:
            try:
                response = provider.chat_stream_response(
                    request_messages,
                    on_text_chunk=None,
                    abort_signal=abort_signal,
                    **call_kwargs,
                )
            except (NotImplementedError, AttributeError):
                # Older or stub providers may not implement streaming.
                # Fall back to plain chat() — drop abort_signal there since
                # we can't pass it portably.
                response = provider.chat(request_messages, **call_kwargs)
            break
        except Exception as e:  # noqa: BLE001 — surface as advisor failure
            if attempt >= _ADVISOR_MAX_ATTEMPTS or not _advisor_error_is_retryable(e):
                # INFO, not DEBUG: a consultation that gives up is invisible
                # otherwise. The worker carries on and the task can still
                # score, so a run that quietly lost its advisor looks
                # identical to one where the advisor worked — the failure
                # shows up only as a token-count difference.
                logging.getLogger(__name__).info(
                    "advisor consultation failed after %d attempt(s) "
                    "(%s: %s); continuing without advice",
                    attempt, type(e).__name__, str(e)[:200],
                )
                return (
                    False,
                    f"Advisor unavailable: {type(e).__name__}: {e}",
                    _zero_usage,
                )
            delay = min(
                _ADVISOR_RETRY_BASE_DELAY * (2 ** (attempt - 1)),
                _ADVISOR_RETRY_MAX_DELAY,
            )
            # A rate limiter that tells us when to come back beats guessing:
            # exponential backoff from a 2s base clears a burst but not a
            # per-minute subscription window. Reuses the main loop's header
            # reader so both lanes honour Retry-After identically; the
            # helper's own clamp keeps a hostile header from parking the
            # worker indefinitely.
            try:
                from src.query.query import _retry_after_seconds

                delay = min(
                    _retry_after_seconds(e, delay), _ADVISOR_RETRY_MAX_DELAY
                )
            except Exception:  # noqa: BLE001 — header unavailable: keep backoff
                pass
            logging.getLogger(__name__).debug(
                "advisor attempt %d/%d failed (%s); retrying in %.1fs",
                attempt, _ADVISOR_MAX_ATTEMPTS, type(e).__name__, delay,
            )
            if not _advisor_sleep(delay, abort_signal):
                return (
                    False,
                    f"Advisor unavailable: {type(e).__name__}: {e}",
                    _zero_usage,
                )
    if response is None:  # pragma: no cover — loop returns or breaks
        return (False, "Advisor unavailable: no response", _zero_usage)

    # Pull token counts off the ChatResponse for the session
    # accumulator. Defaults to zero when the provider didn't return
    # a usage dict (some mocks / older providers).
    raw_usage = getattr(response, "usage", None) or {}
    usage: dict[str, int] = {
        "input_tokens": int(raw_usage.get("input_tokens", 0) or 0),
        "output_tokens": int(raw_usage.get("output_tokens", 0) or 0),
    }

    # ch04 round-3 G1: the client-side advisor is its own API call -- it
    # must self-record into the bootstrap cost totals (the query loop's
    # head only sees main-loop responses). Duration rides along so /cost's
    # "Total duration (API)" covers the same calls its cost total does.
    try:
        from src.bootstrap.state import add_to_total_duration_state
        from src.cost_tracker import record_api_usage

        # ``call_kwargs`` never carries a ``model`` key — the model rides on
        # the provider instance, which is constructed per consultation with
        # ``model=advisor_model``. Reading it from call_kwargs was dead and
        # made the attribution look configurable when it wasn't.
        record_api_usage(
            getattr(response, "model", None)
            or getattr(provider, "model", None)
            or advisor_model
            or "unknown",
            raw_usage,
        )
        _api_ms = int((time.monotonic() - _t0) * 1000)
        add_to_total_duration_state(_api_ms, _api_ms)
    except Exception:
        # NOTE: no function-local ``import logging`` here — a local import
        # binds the name for the WHOLE function scope, which shadowed the
        # module-level import and made every earlier logging call in this
        # function an UnboundLocalError.
        logging.getLogger(__name__).debug(
            "advisor cost recording failed", exc_info=True
        )

    text = getattr(response, "content", None) or ""
    if not isinstance(text, str) or not text.strip():
        # An empty reply is usually the reviewer DECLINING, not a glitch.
        # Measured across an 89-task terminal-bench run: every empty response
        # came from one of three security-flavoured tasks
        # (break-filter-js-from-html, crack-7z-hash, vulnerable-secret), the
        # model emitted 2-3 tokens, and 100% of consultations on those tasks
        # failed. The old text — "Advisor returned no text content." — told
        # the worker nothing about why, so it simply called again and got the
        # same non-answer, spending a turn and a full cache-write each time.
        #
        # Carry the stop reason (ChatResponse.finish_reason is the provider's
        # ``stop_reason``) and say plainly that retrying is unlikely to help,
        # so the worker proceeds instead of looping. NOT routed through the
        # retry lane: this is a considered response, not a transient error,
        # and re-issuing it costs a fresh consultation to reach the same
        # place.
        reason = str(getattr(response, "finish_reason", "") or "").strip()
        detail = f" (stop reason: {reason})" if reason else ""
        logging.getLogger(__name__).info(
            "advisor returned no text%s; continuing without advice", detail
        )
        return (
            False,
            f"Advisor returned no advice{detail} — the reviewer model "
            "declined or produced no text for this conversation. Calling it "
            "again on this task is unlikely to help; proceed on your own "
            "judgment.",
            usage,
        )
    return (True, text, usage)


__all__ = [
    "ADVISOR_BETA_HEADER",
    "ADVISOR_MODE_CLIENT_SIDE",
    "ADVISOR_MODE_INACTIVE",
    "ADVISOR_MODE_SERVER_SIDE",
    "ADVISOR_TOOL_INSTRUCTIONS",
    "ADVISOR_TOOL_NAME",
    "ADVISOR_TOOL_TYPE",
    "CLIENT_ADVISOR_SYSTEM_PROMPT",
    "CLIENT_ADVISOR_TOOL_INPUT_SCHEMA",
    "build_advisor_forwarded_messages",
    "build_advisor_tool_schema",
    "build_client_advisor_tool_schema",
    "can_user_configure_advisor",
    "decide_advisor_mode",
    "execute_client_advisor",
    "extract_advisor_error_code",
    "extract_advisor_result_text",
    "format_advisor_status",
    "is_advisor_block",
    "is_advisor_enabled",
    "is_valid_advisor_model",
    "model_supports_advisor",
    "strip_advisor_blocks",
]
