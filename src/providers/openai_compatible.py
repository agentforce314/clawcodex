"""OpenAI-compatible provider base class.

This base class consolidates shared logic for providers that use the
OpenAI-style /chat/completions API (OpenAI, GLM, Minimax, etc.).
"""

from __future__ import annotations

import json
import logging
from abc import abstractmethod
from typing import Any, Generator, Optional

from .base import BaseProvider, ChatResponse, MessageInput, TextChunkCallback

logger = logging.getLogger(__name__)

# Cap on the ESC-cancel worker-thread queue in ``chat_stream_response``.
# See the ``chunk_queue`` construction below for why this is bounded.
_CHUNK_QUEUE_MAXSIZE = 64


def _apply_client_timeout(client: Any) -> Any:
    """Bound an OpenAI-SDK client's read timeout + retries (env-tunable).

    Without this, a streaming request to an endpoint that accepts the connection
    but stalls mid-read blocks the *synchronous* SDK read for the SDK's 600s
    default — and that read runs on the asyncio event loop the agent loop drives,
    so one stalled stream freezes every concurrent workflow agent. ``read`` is
    the max gap BETWEEN bytes, so legitimate long streams keep working as long as
    data keeps flowing. Applied centrally (base ``client`` property) so every
    subclass is covered. Tunable via CLAWCODEX_LLM_READ_TIMEOUT /
    CLAWCODEX_LLM_CONNECT_TIMEOUT / CLAWCODEX_LLM_MAX_RETRIES.
    """
    try:
        import os

        import httpx

        read = float(os.environ.get("CLAWCODEX_LLM_READ_TIMEOUT", "120"))
        connect = float(os.environ.get("CLAWCODEX_LLM_CONNECT_TIMEOUT", "15"))
        max_retries = int(os.environ.get("CLAWCODEX_LLM_MAX_RETRIES", "1"))
        timeout = httpx.Timeout(connect=connect, read=read, write=30.0, pool=15.0)
        return client.with_options(timeout=timeout, max_retries=max_retries)
    except Exception:  # noqa: BLE001 — never break client creation over timeout cfg
        return client


def _anthropic_image_block_to_openai(block: dict[str, Any]) -> dict[str, Any] | None:
    """Translate an Anthropic image content block to OpenAI's
    ``image_url`` shape.

    Anthropic: ``{"type": "image", "source": {"type": "base64",
    "media_type": "image/png", "data": "..."}}``.
    OpenAI:    ``{"type": "image_url", "image_url": {"url":
    "data:image/png;base64,..."}}``.

    Returns ``None`` when the block isn't a recognisable Anthropic image
    block (caller should keep the original).
    """
    if not isinstance(block, dict) or block.get("type") != "image":
        return None
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    if source.get("type") != "base64":
        return None
    media_type = source.get("media_type") or "image/png"
    data = source.get("data")
    if not data or not isinstance(data, str):
        # Producer-bug guard: an empty/missing data field would generate
        # ``data:image/png;base64,`` which OpenAI rejects with a confusing
        # error. Return ``None`` so the caller keeps the original block --
        # the upstream serializer will surface the malformed shape instead
        # of silently producing a request the server will fail.
        return None
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{data}"},
    }


def _anthropic_document_block_to_openai(block: dict[str, Any]) -> dict[str, Any] | None:
    """Translate an Anthropic document content block (PDF) to OpenAI's
    ``file`` content shape.

    Anthropic: ``{"type": "document", "source": {"type": "base64",
    "media_type": "application/pdf", "data": "..."}}``.
    OpenAI:    ``{"type": "file", "file": {"filename": "document.pdf",
    "file_data": "data:application/pdf;base64,..."}}``.

    No production path currently produces ``DocumentBlock`` for an
    OpenAI-compatible provider (PDFs flow through Read tool's
    ``_read_map_result_to_api`` as text). This translator exists so that
    if ``DocumentBlock`` ever shows up — e.g. a future @-mention path or
    a third-party tool returning a PDF — it's converted to the closest
    OpenAI shape instead of silently passing through as an unrecognised
    Anthropic-shape block (which OpenAI-compat providers either reject
    or drop). Mirrors the ``image`` translator's defensive contract.

    Returns ``None`` when the block isn't a recognisable Anthropic
    document block (caller should keep the original).
    """
    if not isinstance(block, dict) or block.get("type") != "document":
        return None
    source = block.get("source")
    if not isinstance(source, dict):
        return None
    if source.get("type") != "base64":
        return None
    media_type = source.get("media_type") or "application/pdf"
    data = source.get("data")
    if not data or not isinstance(data, str):
        # Same producer-bug guard as the image translator: an empty
        # ``data`` field would produce ``data:application/pdf;base64,``
        # which the server rejects. Return ``None`` so the caller keeps
        # the original block and the upstream serializer surfaces the
        # malformed shape instead of producing a request the server will
        # fail confusingly.
        return None
    # OpenAI's ``file`` block accepts an optional ``filename``; many
    # provider impls require it. Use a stable default since Anthropic's
    # document source carries no filename hint.
    return {
        "type": "file",
        "file": {
            "filename": "document.pdf",
            "file_data": f"data:{media_type};base64,{data}",
        },
    }


def _translate_anthropic_multimodal_block(block: Any) -> dict[str, Any] | None:
    """Try every Anthropic-shape multimodal translator on ``block``.

    Returns the OpenAI-shape replacement, or ``None`` when the block isn't
    a translatable multimodal type (text / unknown blocks pass through
    untouched at the call site). Centralised so the converter doesn't
    grow a long chain of ``if isinstance/elif`` branches as new
    multimodal shapes appear.
    """
    if not isinstance(block, dict):
        return None
    translated = _anthropic_image_block_to_openai(block)
    if translated is not None:
        return translated
    return _anthropic_document_block_to_openai(block)


def _convert_anthropic_messages_to_openai(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert Anthropic-format messages to OpenAI chat-completion format.

    Handles four transformations:
    1. Assistant messages with tool_use content blocks → assistant + tool_calls
    2. User messages with tool_result content blocks → role=tool messages
    3. Anthropic image content blocks (in user messages or tool_result
       payloads) → OpenAI ``image_url`` data-URI blocks. Required because
       both @image.png @-mentions and Read-tool image returns ship
       Anthropic-shape blocks; without translation, OpenAI-compatible
       providers either reject the request or silently drop the image.
    4. Anthropic document content blocks → OpenAI ``file`` blocks.
       Defensive translation: no production path currently produces
       ``DocumentBlock`` for an OpenAI-compatible provider, but if one
       ever appears it lands in the closest OpenAI shape rather than
       passing through as an unrecognised Anthropic block.
    """
    # ChatGPT-subscription passthrough items (encrypted reasoning et al.)
    # have no Chat Completions representation — they exist only for the
    # OpenAI Responses replay path. Strip them BEFORE conversion so a
    # mid-session model switch never leaks them to a server that rejects
    # unknown block types. See openai_responses.RESPONSES_ITEM_BLOCK_TYPE.
    from .openai_responses import strip_responses_item_blocks
    messages = strip_responses_item_blocks(messages)

    result: list[dict[str, Any]] = []

    # Pre-scan every assistant message for the tool_use ids it declares.
    # An OpenAI ``role=tool`` message is only valid as a response to a
    # ``tool_call`` that was actually emitted; a tool_result whose
    # tool_use_id never appears in any assistant ``tool_use`` is an ORPHAN
    # and the API rejects it ("messages with role 'tool' must be a
    # response to a preceding message with 'tool_calls'"). The tool_result
    # branch below drops such orphans. ``ensure_tool_result_pairing``
    # (src/types/messages.py) is the upstream backstop but its reverse-
    # strip only fires when the previous message is NOT an assistant, so
    # an orphan trailing a text-only assistant turn (a stale id after
    # compaction/resume) slips through to here. Mirrors TS
    # openaiShim.ts:498/548 ``knownToolCallIds``. (The symmetric assistant-
    # side guard — dropping a tool_use that has no result — is NOT ported;
    # ``ensure_tool_result_pairing`` backfills synthetic results for every
    # tool_use upstream, so no orphan tool_call reaches the converter.)
    #
    # Intentionally ``tool_use``-only — NOT ``server_tool_use`` /
    # ``mcp_tool_use`` (which ``ensure_tool_result_pairing`` does recognize,
    # messages.py). Those server-side shapes never reach THIS converter:
    # the Anthropic server-advisor path strips them before non-Anthropic
    # providers (query.py strip_advisor_blocks), and client-side MCP
    # surfaces as a plain ``tool_use``. Matches TS, which builds
    # knownToolCallIds only from converted tool_use (openaiShim.ts:638).
    # Do not widen this to server/mcp shapes without re-checking that path,
    # or you risk un-dropping genuine orphans.
    known_tool_call_ids: set[str] = set()
    for scan_msg in messages:
        if scan_msg.get("role") != "assistant":
            continue
        scan_content = scan_msg.get("content")
        if not isinstance(scan_content, list):
            continue
        for scan_block in scan_content:
            if isinstance(scan_block, dict) and scan_block.get("type") == "tool_use":
                known_tool_call_ids.add(scan_block.get("id", ""))

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Pass through simple string content and system messages
        if isinstance(content, str) or role == "system":
            result.append(msg)
            continue

        if not isinstance(content, list):
            result.append(msg)
            continue

        # --- assistant with tool_use blocks ---
        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            other_blocks: list[dict[str, Any]] = []

            for block in content:
                if not isinstance(block, dict):
                    other_blocks.append(block)
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    inp = block.get("input", {})
                    tool_calls.append({
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(inp) if isinstance(inp, dict) else str(inp),
                        },
                    })
                else:
                    other_blocks.append(block)

            if tool_calls:
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "tool_calls": tool_calls,
                }
                # DeepSeek thinking mode requires reasoning_content to be
                # replayed together with assistant tool call messages.
                reasoning_content = msg.get("reasoning_content")
                if isinstance(reasoning_content, str) and reasoning_content:
                    assistant_msg["reasoning_content"] = reasoning_content
                text_content = "\n".join(text_parts) if text_parts else None
                if text_content:
                    assistant_msg["content"] = text_content
                else:
                    assistant_msg["content"] = None
                result.append(assistant_msg)
            else:
                # No tool calls, keep original
                result.append(msg)
            continue

        # --- user with tool_result blocks ---
        if role == "user":
            tool_results: list[dict[str, Any]] = []
            non_tool_blocks: list[dict[str, Any]] = []

            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_results.append(block)
                else:
                    # Translate Anthropic multimodal blocks (image / document)
                    # to their OpenAI shapes; pass everything else through
                    # unchanged.
                    translated = _translate_anthropic_multimodal_block(block)
                    non_tool_blocks.append(translated if translated is not None else block)

            # Emit tool_result blocks as ``role=tool`` messages FIRST,
            # BEFORE any non-tool user content. OpenAI-compatible APIs
            # require an assistant message carrying ``tool_calls`` to be
            # followed IMMEDIATELY by the matching ``role=tool`` messages
            # (one per ``tool_call_id``). A single Anthropic user message
            # can carry BOTH tool_result blocks AND plain text — that is
            # exactly what ``normalize_messages_for_api`` produces when it
            # merges a rejected/interrupted tool turn with the user's next
            # prompt (reject 4 Reads, then type "please continue"). Emitting
            # the text first would slip a ``role=user`` message between the
            # ``tool_calls`` and the tool responses, and the API rejects the
            # request with "An assistant message with 'tool_calls' must be
            # followed by tool messages responding to each 'tool_call_id'
            # (insufficient tool messages following tool_calls message)".
            # So tool messages go first; remaining user content is appended
            # AFTER the loop. Mirrors TS openaiShim.ts:546-567.
            #
            # Multimodal tool_results additionally split into a ``role=tool``
            # text message PLUS a synthetic ``role=user`` carrying the image/
            # file blocks (OpenAI tool messages can't hold multimodal
            # content). Those synthetic user messages are COLLECTED here and
            # emitted only AFTER the whole loop — never inline — so a second
            # tool_result in the same batch (parallel tool calls, e.g. one
            # Read returns an image and another returns text) can't drop a
            # ``role=user`` BETWEEN two ``role=tool`` messages and detach the
            # later tool_call from its response.
            deferred_multimodal_user_messages: list[dict[str, Any]] = []
            # Emit each tool_result as a separate role=tool message
            for tr in tool_results:
                tool_use_id = tr.get("tool_use_id", "")
                # Orphan guard: drop a tool_result whose id was never
                # emitted as a tool_call (see the pre-scan note above).
                # With no preceding ``tool_calls`` entry the API rejects
                # the ``role=tool`` message. Mirrors TS openaiShim.ts:548.
                if tool_use_id not in known_tool_call_ids:
                    logger.debug(
                        "Dropping orphan tool_result for tool_use_id=%r "
                        "(no matching tool_call in history)",
                        tool_use_id,
                    )
                    continue
                raw_content = tr.get("content", "")
                # Collect any image blocks separately. OpenAI's ``role=tool``
                # message only accepts a text ``content`` string -- it
                # rejects multimodal content. So we split: emit the text
                # body as the tool message, then immediately follow with a
                # synthetic ``role=user`` message that carries the image
                # blocks via ``image_url``. The model sees the image
                # alongside the tool result, which is the closest semantic
                # match to Anthropic's native multimodal tool_result.
                multimodal_blocks_from_tool: list[dict[str, Any]] = []
                if isinstance(raw_content, list):
                    # Flatten nested content blocks to text
                    parts = []
                    for item in raw_content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(item.get("text", ""))
                        elif isinstance(item, dict) and item.get("type") in ("image", "document"):
                            translated_multimodal = _translate_anthropic_multimodal_block(item)
                            if translated_multimodal is not None:
                                multimodal_blocks_from_tool.append(translated_multimodal)
                        elif isinstance(item, str):
                            parts.append(item)
                        else:
                            parts.append(json.dumps(item) if isinstance(item, dict) else str(item))
                    flat_content = "\n".join(parts) if parts else ""
                elif isinstance(raw_content, str):
                    flat_content = raw_content
                else:
                    flat_content = str(raw_content)

                # KNOWN OPENAI-API LIMITATION — tool→user split:
                # ----------------------------------------------------
                # OpenAI's wire format requires ``content`` on a tool
                # message to be a non-empty string AND does not allow
                # image_url / file blocks in a tool message. So when
                # an Anthropic tool_result carries multimodal content,
                # we must split it: emit ``role=tool`` with the text
                # body (or a placeholder if none), then a synthetic
                # ``role=user`` carrying the translated multimodal
                # blocks. The synthetic user message CANNOT be linked
                # back to its parent tool_call_id — OpenAI provides no
                # wire-level mechanism for that. The model sees
                # ``tool(text)`` followed by ``user(multimodal)`` and
                # could briefly treat the trailing user message as a
                # new prompt rather than the tool's payload.
                #
                # Mitigations applied here:
                #   1. Tool message carries an explicit "see following
                #      message" suffix (image-only) or a "see also next
                #      message" line (text+image) referencing the
                #      tool_use_id. This is symmetric across both the
                #      image-only and text+image branches so the
                #      correlation hint is always present when a split
                #      happened.
                #   2. Synthetic user message starts with a tiny text
                #      block that names the tool_use_id. The image /
                #      file blocks follow. The model now has the link
                #      visible from BOTH directions even though
                #      ``tool_call_id`` only exists on the tool message.
                # On Anthropic the equivalent stays a single
                # multimodal tool_result with no split; there is no
                # equivalent Anthropic-side limitation.
                if multimodal_blocks_from_tool:
                    correlation = (
                        f"[multimodal content for tool_use_id={tool_use_id} "
                        "delivered in the following message]"
                    )
                    if flat_content:
                        # Text + multimodal: append a one-line pointer so
                        # the tool message carries the same correlation
                        # marker the image-only branch produces.
                        flat_content = f"{flat_content}\n\n{correlation}"
                    else:
                        # Image-only tool_result: the pointer is the
                        # whole tool-message body. OpenAI rejects empty
                        # tool-message content, so this also doubles as
                        # the non-empty guard.
                        flat_content = correlation
                if not flat_content:
                    # Defensive: if there were no multimodal blocks AND
                    # no text body (e.g. ``content: []`` or ``content: ""``
                    # — the converter's fallthrough JSON-dumps unknown
                    # block types into ``flat_content`` so those don't
                    # land here), emit a literal sentinel so OpenAI's
                    # "tool message content must be non-empty"
                    # requirement is honoured.
                    flat_content = "[empty tool result]"

                # OpenAI's ``role=tool`` message has no ``is_error`` field,
                # so error-ness must ride the text: prefix ``Error: `` like
                # TS convertToolResultContent (openaiShim.ts:309/:349/:356
                # — every text-shaped emission applies it). Load-bearing
                # since thrown tool errors are no longer wrapped in
                # ``<tool_use_error>`` tags (_format_error parity port):
                # without the prefix an OpenAI-wire model would see a bare
                # message with no failure signal at all.
                if tr.get("is_error"):
                    flat_content = f"Error: {flat_content}"

                result.append({
                    "role": "tool",
                    "tool_call_id": tool_use_id,
                    "content": flat_content,
                })
                if multimodal_blocks_from_tool:
                    # Lead with a tiny text block naming the parent
                    # tool_use_id so the model can correlate this
                    # synthetic user message back to the prior
                    # ``role=tool`` message even though OpenAI's wire
                    # format gives no tool_call_id on user messages.
                    correlation_text = {
                        "type": "text",
                        "text": (
                            f"[content for tool_use_id={tool_use_id}]"
                        ),
                    }
                    # DEFER (do not append inline): collected and emitted
                    # after the loop so it never splits two tool messages.
                    deferred_multimodal_user_messages.append({
                        "role": "user",
                        "content": [correlation_text, *multimodal_blocks_from_tool],
                    })

            # All ``role=tool`` messages have now been emitted contiguously
            # right after the assistant ``tool_calls``. NOW emit the deferred
            # multimodal user payloads, then any remaining non-tool user
            # content — both must follow every tool message so neither splits
            # a tool_calls/tool-response run.
            result.extend(deferred_multimodal_user_messages)
            if non_tool_blocks:
                result.append({"role": "user", "content": non_tool_blocks})
            continue

        # Fallback
        result.append(msg)
    return result


def _convert_to_openai_tool_schema(anthropic_tool: dict[str, Any]) -> dict[str, Any] | None:
    """Convert Anthropic tool schema to OpenAI/GLM/Minimax function format.

    Returns None if the schema is invalid (missing type, type is None, or other issues).
    """
    input_schema = anthropic_tool.get("input_schema")
    if not input_schema or not isinstance(input_schema, dict):
        return None
    schema_type = input_schema.get("type")
    if schema_type is None or schema_type == "None":
        return None
    # Some providers (Azure) require type=object to have properties
    if schema_type == "object" and "properties" not in input_schema and "anyOf" not in input_schema and "oneOf" not in input_schema:
        # Try to add an empty properties dict if none provided
        input_schema = {**input_schema, "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": anthropic_tool["name"],
            "description": anthropic_tool.get("description", ""),
            "parameters": input_schema,
        },
    }


def _close_truncated_json(s: str) -> str:
    """Best-effort complete a JSON document cut off mid-stream.

    Tool-call arguments stream as string deltas; an interrupted or late-
    truncated stream can leave invalid JSON (an unterminated string, open
    braces/brackets, a dangling comma/colon). This closes those so the partial
    value is still recoverable, returning ``"{}"`` if the result is still not
    valid JSON.
    """
    stack: list[str] = []  # expected closing chars, innermost last
    in_str = False
    esc = False
    for c in s:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            stack.append("}")
        elif c == "[":
            stack.append("]")
        elif c in ("}", "]"):
            if stack:
                stack.pop()
    out = s
    if esc:  # a trailing backslash with nothing escaped — drop it
        out = out[:-1]
    if in_str:  # close the dangling string
        out += '"'
    trimmed = out.rstrip(" \t\r\n")
    if trimmed.endswith(","):  # dangling comma before a closer
        out = trimmed[:-1]
    elif trimmed.endswith(":"):  # key with no value yet
        out = trimmed + "null"
    for closer in reversed(stack):
        out += closer
    return out if _is_valid_json(out) else "{}"


def _is_valid_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except Exception:
        return False


def _parse_tool_call_arguments(raw: str | None) -> dict[str, Any]:
    """Parse streamed tool-call argument JSON, recovering truncation.

    Empty/absent → ``{}``. Valid JSON object → parsed as-is. Invalid JSON →
    best-effort close the truncation (see :func:`_close_truncated_json`) so a
    resumed/replayed turn keeps the partial arguments instead of discarding
    them; still falls back to ``{}`` when unrecoverable. Activates only on
    already-invalid input, so the happy path is untouched for every provider.

    Always returns a ``dict``: tool-call arguments are JSON objects, so a
    top-level array/scalar (exotic or malformed) coerces to ``{}`` rather than
    flowing downstream where ``input`` is consumed as a mapping.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        try:
            parsed = json.loads(_close_truncated_json(raw))
        except Exception:
            return {}
    return parsed if isinstance(parsed, dict) else {}


class OpenAICompatibleProvider(BaseProvider):
    """Base class for providers using OpenAI-style chat completions API.

    Subclasses must implement:
    - _create_client(): Create and return the provider-specific SDK client
    - get_available_models(): Return list of available model names

    The client is created lazily on first use.
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """Initialize OpenAI-compatible provider.

        Args:
            api_key: API key for authentication
            base_url: Base URL for API endpoint
            model: Default model to use
        """
        super().__init__(api_key, base_url, model)
        self._client: Optional[Any] = None

    @abstractmethod
    def _create_client(self) -> Any:
        """Create the provider-specific SDK client.

        Returns:
            An instance of the provider's SDK client.
        """
        pass

    @property
    def client(self) -> Any:
        """Get or create the SDK client (lazy initialization).

        A bounded read timeout is applied centrally here so EVERY
        openai-compatible provider (openai, openrouter, deepseek, glm, …) is
        protected from a stalled streaming read blocking the asyncio event loop
        — not just OpenAIProvider. The SDK default is read=600s, which freezes
        concurrent workflow agents for up to 10 minutes on a stalled stream.
        """
        if self._client is None:
            self._client = _apply_client_timeout(self._create_client())
        return self._client

    def _prepare_messages(self, messages: list[MessageInput]) -> list[dict[str, Any]]:
        """Convert messages to OpenAI format, translating Anthropic tool blocks."""
        base = super()._prepare_messages(messages)
        return _convert_anthropic_messages_to_openai(base)

    def _build_usage_dict(self, usage: Any) -> dict[str, Any]:
        if usage is None:
            return {}
        return {
            "input_tokens": getattr(usage, "prompt_tokens", 0),
            "output_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
        }

    @staticmethod
    def _with_system_prompt(
        provider_messages: list[dict[str, Any]],
        kwargs: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Fold an Anthropic-style ``system`` kwarg into the message list.

        OpenAI-compatible chat completions have no top-level ``system``
        parameter — the system prompt is a ``role="system"`` message. Several
        callers in this port pass ``system=`` as a kwarg, mirroring the
        Anthropic provider: compaction's ``COMPACT_SYSTEM_PROMPT``
        (``services/compact/compact.py``), agent hooks, and the memdir
        selector. The normal query loop already prepends a system message for
        these providers (``query.py`` non-Anthropic branch), so the kwarg only
        reaches here on those side paths. Without this translation it is
        splatted into ``completions.create()``, which raises
        ``TypeError: Completions.create() got an unexpected keyword argument
        'system'`` — the failure that drops compaction to its text-extraction
        fallback on DeepSeek/GLM/OpenAI/OpenRouter.

        Pops ``system`` from ``kwargs`` (so it is NOT forwarded to the SDK) and
        returns ``provider_messages`` with a leading system message when the
        kwarg was a non-empty string or Anthropic-style list of text blocks;
        otherwise returns ``provider_messages`` unchanged.
        """
        system = kwargs.pop("system", None)
        if not system:
            return provider_messages
        if isinstance(system, str):
            text = system
        elif isinstance(system, list):
            parts: list[str] = []
            for block in system:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    parts.append(str(block.get("text", "")))
            text = "\n".join(p for p in parts if p)
        else:
            text = str(system)
        text = text.strip()
        if not text:
            return provider_messages
        return [{"role": "system", "content": text}, *provider_messages]

    def chat(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ) -> ChatResponse:
        """Synchronous chat completion.

        Args:
            messages: List of chat messages
            tools: Optional list of tool schemas (Anthropic format)
            **kwargs: Additional parameters

        Returns:
            Chat response
        """
        model = self._get_model(**kwargs)

        # Convert messages
        provider_messages = self._prepare_messages(messages)

        # Convert tools to OpenAI format
        extra_kwargs: dict[str, Any] = {}
        if tools:
            converted = [_convert_to_openai_tool_schema(t) for t in tools]
            extra_kwargs["tools"] = [t for t in converted if t is not None]

        # Fold an Anthropic-style ``system`` kwarg into a leading system
        # message (and pop it from kwargs so it isn't forwarded to the SDK).
        provider_messages = self._with_system_prompt(provider_messages, kwargs)

        # Make API call
        response = self.client.chat.completions.create(
            model=model,
            messages=provider_messages,
            **extra_kwargs,
            **{k: v for k, v in kwargs.items() if k not in ["model", "tools"]},
        )

        # Extract content
        choice = response.choices[0]

        # Handle reasoning content (GLM specific, but harmless for others)
        reasoning_content: Optional[str] = None
        if (
            hasattr(choice.message, "reasoning_content")
            and choice.message.reasoning_content
        ):
            reasoning_content = choice.message.reasoning_content

        # Extract tool calls (OpenAI format -> Anthropic format)
        tool_uses: Optional[list[dict[str, Any]]] = None
        if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:
            tool_uses = []
            for tc in choice.message.tool_calls:
                args = _parse_tool_call_arguments(tc.function.arguments)
                tool_uses.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                })

        return ChatResponse(
            content=choice.message.content or "",
            model=response.model,
            usage=self._build_usage_dict(getattr(response, "usage", None)),
            finish_reason=choice.finish_reason,
            reasoning_content=reasoning_content,
            tool_uses=tool_uses,
        )

    def chat_stream(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        **kwargs
    ) -> Generator[str, None, None]:
        """Streaming chat completion.

        Args:
            messages: List of chat messages
            tools: Optional list of tool schemas (Anthropic format)
            **kwargs: Additional parameters

        Yields:
            Chunks of response content
        """
        model = self._get_model(**kwargs)

        # Convert messages
        provider_messages = self._prepare_messages(messages)

        # Convert tools to OpenAI format
        extra_kwargs: dict[str, Any] = {}
        if tools:
            converted = [_convert_to_openai_tool_schema(t) for t in tools]
            extra_kwargs["tools"] = [t for t in converted if t is not None]

        # Fold an Anthropic-style ``system`` kwarg into a leading system
        # message (and pop it from kwargs so it isn't forwarded to the SDK).
        provider_messages = self._with_system_prompt(provider_messages, kwargs)

        # Stream API call
        stream = self.client.chat.completions.create(
            model=model,
            messages=provider_messages,
            stream=True,
            **extra_kwargs,
            **{k: v for k, v in kwargs.items() if k not in ["model", "tools"]},
        )

        for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content

    def chat_stream_response(
        self,
        messages: list[MessageInput],
        tools: Optional[list[dict[str, Any]]] = None,
        on_text_chunk: TextChunkCallback | None = None,
        abort_signal: Any = None,
        on_thinking_chunk: TextChunkCallback | None = None,
        **kwargs
    ) -> ChatResponse:
        """Stream OpenAI-compatible chunks while rebuilding the final response.

        ESC-cancellation runs the SDK iteration on a daemon worker
        thread that pushes chunks into a ``queue.Queue``. The main
        thread polls the queue with a 100 ms timeout and re-checks
        ``guard.aborted`` between ticks. On abort the main thread
        raises ``AbortError`` immediately and orphans the worker —
        the worker dies when the underlying connection eventually
        closes.

        Why the worker indirection (vs. the simpler in-loop check
        used in earlier revisions): the OpenAI Python SDK uses sync
        ``httpx`` for streaming, and ``response.close()`` from
        another thread is purely advisory. For LiteLLM-proxied
        connections (and certain other httpx + chunked-transfer
        configurations) the SDK's blocking socket read doesn't
        actually return when the response is "closed" — it keeps
        consuming bytes. Unlike JavaScript's native ``fetch +
        AbortSignal`` integration (which the TypeScript reference at
        ``typescript/src/services/api/openaiShim.ts`` uses), Python
        has no portable way to make a sync blocking read honor an
        abort from another thread, so the worker exists to keep the
        main thread's response time independent of the SDK's
        cooperation.
        """
        from ._stream_abort import StreamAbortGuard

        guard = StreamAbortGuard(abort_signal)
        guard.raise_if_pre_aborted()

        model = self._get_model(**kwargs)
        provider_messages = self._prepare_messages(messages)

        extra_kwargs: dict[str, Any] = {}
        if tools:
            converted = [_convert_to_openai_tool_schema(t) for t in tools]
            extra_kwargs["tools"] = [t for t in converted if t is not None]

        # ``stream_options.include_usage`` opts the OpenAI streaming API
        # into emitting a final ``usage`` chunk; without it, ``chunk.usage``
        # is always ``None`` and the rebuilt ChatResponse has empty token
        # counts. The spinner row + ``/stats`` rely on this — see
        # ``_build_usage_dict`` below and the consumer in
        # ``src/query/query.py``.
        # Fold an Anthropic-style ``system`` kwarg into a leading system
        # message before the kwargs splat below (and pop it from kwargs so it
        # isn't forwarded to the SDK).
        provider_messages = self._with_system_prompt(provider_messages, kwargs)

        stream_kwargs = {k: v for k, v in kwargs.items() if k not in ["model", "tools"]}
        existing_stream_options = stream_kwargs.pop("stream_options", None) or {}
        stream_kwargs["stream_options"] = {
            **existing_stream_options,
            "include_usage": True,
        }
        stream = self.client.chat.completions.create(
            model=model,
            messages=provider_messages,
            stream=True,
            **extra_kwargs,
            **stream_kwargs,
        )

        content_parts: list[str] = []
        response_model = model
        finish_reason = "stop"
        reasoning_parts: list[str] = []
        usage_obj: Any = None
        tool_calls_by_index: dict[int, dict[str, str]] = {}

        # Worker-thread iteration. The OpenAI Python SDK uses sync
        # ``httpx`` for streaming, and ``response.close()`` from another
        # thread is best-effort — for LiteLLM-proxied connections (and
        # some other httpx configurations) the SDK's blocking socket
        # read doesn't actually return when the response is closed.
        # Unlike JavaScript's native ``fetch + AbortSignal`` integration
        # (which the TypeScript reference uses), Python has no portable
        # way to make a sync blocking read honor an abort from another
        # thread.
        #
        # Workaround: hoist the iteration onto a daemon worker thread
        # that pushes chunks into a queue. The main thread polls the
        # queue with a short timeout and re-checks ``guard.aborted``
        # each tick. On abort we raise ``AbortError`` immediately and
        # orphan the worker — it'll die when the underlying connection
        # eventually closes (server-side, idle timeout, or the SDK's
        # natural exhaustion). The cost is some wasted bandwidth on
        # the orphaned read; the benefit is that the user's prompt
        # comes back in ~100 ms regardless of LiteLLM/httpx behavior.
        import queue as _queue
        import threading as _threading

        _DONE = object()
        # Bounded so an orphaned worker (abort fired but the SDK
        # iterator never honors ``response.close()`` — the LiteLLM
        # proxy case documented above) can't accumulate unbounded
        # chunks in memory. Once the queue is full, ``put()`` blocks
        # the worker instead of growing, capping retained memory at
        # ``_CHUNK_QUEUE_MAXSIZE`` chunks regardless of how long the
        # underlying connection stays open after abort.
        chunk_queue: _queue.Queue = _queue.Queue(maxsize=_CHUNK_QUEUE_MAXSIZE)

        def _drain_stream() -> None:
            try:
                for c in stream:
                    chunk_queue.put(c)
            except BaseException as exc:  # noqa: BLE001 — surface to consumer
                chunk_queue.put(exc)
            finally:
                chunk_queue.put(_DONE)

        worker = _threading.Thread(
            target=_drain_stream,
            daemon=True,
            name=f"openai-stream-{id(stream)}",
        )

        with guard.attach(stream):
            worker.start()
            while True:
                try:
                    item = chunk_queue.get(timeout=0.1)
                except _queue.Empty:
                    # No chunk available right now — check abort and
                    # loop. The 100 ms tick bounds how long the user
                    # waits between pressing ESC and the prompt
                    # returning, regardless of how slow / blocked the
                    # underlying SDK iteration is.
                    if guard.aborted:
                        # Use ``raise_if_post_aborted`` so the abort
                        # reason from the controller is preserved
                        # (rather than hardcoding ``"user_interrupt"``,
                        # which would silently downgrade a non-default
                        # reason like a future ``"rate_limit_backoff"``).
                        guard.raise_if_post_aborted()
                    continue

                if item is _DONE:
                    break
                if isinstance(item, BaseException):
                    if isinstance(item, Exception):
                        guard.reraise_if_aborted(item)
                        raise item
                    # KeyboardInterrupt/SystemExit from the worker
                    # path — re-raise as-is so the outer signal-
                    # handling story stays intact.
                    raise item

                chunk = item
                response_model = getattr(chunk, "model", response_model)
                usage_candidate = getattr(chunk, "usage", None)
                if usage_candidate is not None:
                    usage_obj = usage_candidate

                choices = getattr(chunk, "choices", None) or []
                if choices:
                    choice = choices[0]
                    if getattr(choice, "finish_reason", None):
                        finish_reason = choice.finish_reason

                    delta = getattr(choice, "delta", None)
                    if delta is not None:
                        content_piece = getattr(delta, "content", None)
                        if content_piece:
                            piece = str(content_piece)
                            content_parts.append(piece)
                            if on_text_chunk is not None:
                                on_text_chunk(piece)

                        reasoning_piece = getattr(delta, "reasoning_content", None)
                        if reasoning_piece:
                            reasoning_parts.append(str(reasoning_piece))
                            if on_thinking_chunk is not None:
                                on_thinking_chunk(str(reasoning_piece))

                        tool_call_deltas = getattr(delta, "tool_calls", None) or []
                        for tc in tool_call_deltas:
                            idx = getattr(tc, "index", 0)
                            entry = tool_calls_by_index.setdefault(idx, {"id": "", "name": "", "arguments": ""})

                            tc_id = getattr(tc, "id", None)
                            if tc_id:
                                entry["id"] = str(tc_id)

                            function = getattr(tc, "function", None)
                            if function is not None:
                                fn_name = getattr(function, "name", None)
                                if fn_name:
                                    entry["name"] += str(fn_name)
                                fn_args = getattr(function, "arguments", None)
                                if fn_args:
                                    entry["arguments"] += str(fn_args)

                # Check abort AFTER processing this chunk so any
                # already-delivered content is preserved (matches the
                # in-loop-check semantics from the old implementation:
                # the chunk-list test pins that the chunk we received
                # before the abort gets processed; we just don't take
                # the next one).
                if guard.aborted:
                    guard.raise_if_post_aborted()

        # Stream completed naturally OR the in-loop check broke out.
        # In the latter case the signal is already tripped; raise so
        # the caller bails at the same place every other path does.
        guard.raise_if_post_aborted()

        tool_uses: list[dict[str, Any]] = []
        for idx in sorted(tool_calls_by_index.keys()):
            item = tool_calls_by_index[idx]
            if not item["name"]:
                continue
            parsed_args = _parse_tool_call_arguments(item["arguments"])
            tool_uses.append({
                "id": item["id"] or f"tool_call_{idx}",
                "name": item["name"],
                "input": parsed_args,
            })

        reasoning_content = "".join(reasoning_parts) if reasoning_parts else None
        return ChatResponse(
            content="".join(content_parts),
            model=response_model,
            usage=self._build_usage_dict(usage_obj),
            finish_reason=finish_reason,
            reasoning_content=reasoning_content,
            tool_uses=tool_uses or None,
        )
