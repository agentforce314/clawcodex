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

    # Ensure the conversation ends with a user message. Vertex-fronted
    # Anthropic (and most proxies) reject assistant-prefill.
    if not flattened or flattened[-1].get("role") != "user":
        flattened.append({"role": "user", "content": CLIENT_ADVISOR_PROMPT_SUFFIX})
    return flattened


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
        provider = provider_cls(
            api_key=cfg_raw.get("api_key", ""),
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
    from src.providers.anthropic_provider import AnthropicProvider
    from src.providers.minimax_provider import MinimaxProvider

    is_anthropic_shape = isinstance(provider, (AnthropicProvider, MinimaxProvider))

    call_kwargs: dict[str, Any] = {
        "tools": [],
        "max_tokens": 4096,
    }
    if is_anthropic_shape:
        call_kwargs["system"] = CLIENT_ADVISOR_SYSTEM_PROMPT
        request_messages = list(forwarded_messages)
    else:
        # Prepend the system message; OpenAI-compat will honor it
        # naturally as the first message in the conversation.
        request_messages = [
            {"role": "system", "content": CLIENT_ADVISOR_SYSTEM_PROMPT},
            *forwarded_messages,
        ]

    # ``chat_stream_response`` is the cross-provider call that accepts
    # ``abort_signal`` uniformly (per BaseProvider) and returns a fully
    # accumulated ChatResponse. The plain ``chat()`` path doesn't accept
    # ``abort_signal`` consistently across providers — passing it as a
    # kwarg would forward an unknown param to the underlying SDK for
    # Anthropic (line 239 of anthropic_provider.py forwards unknown
    # kwargs straight to ``messages.create``). Streaming under the hood
    # but no ``on_text_chunk`` callback — we only need the final text.
    _t0 = time.monotonic()
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
    except Exception as e:  # noqa: BLE001 — surface as advisor failure
        return (False, f"Advisor unavailable: {type(e).__name__}: {e}", _zero_usage)

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

        record_api_usage(
            call_kwargs.get("model")
            or getattr(response, "model", None)
            or getattr(provider, "model", "unknown"),
            raw_usage,
        )
        _api_ms = int((time.monotonic() - _t0) * 1000)
        add_to_total_duration_state(_api_ms, _api_ms)
    except Exception:
        import logging

        logging.getLogger(__name__).debug(
            "advisor cost recording failed", exc_info=True
        )

    text = getattr(response, "content", None) or ""
    if not isinstance(text, str) or not text.strip():
        return (False, "Advisor returned no text content.", usage)
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
