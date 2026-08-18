"""
Core compaction — LLM summarization of conversation history.

Port of ``typescript/src/services/compact/compact.ts``.

Provides ``compact_conversation()`` for full compaction and
``partial_compact_conversation()`` for partial (prefix/suffix) compaction.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ...types.content_blocks import TextBlock, ToolUseBlock
from ...types.messages import (
    Message,
    UserMessage,
    AssistantMessage,
    normalize_messages_for_api,
)
from ...compact_service.messages import (
    annotate_boundary_with_preserved_segment,
    create_compact_boundary_message,
    create_compact_summary_message,
    get_messages_after_boundary,
    is_compact_boundary_message,
)
from ...context_system.microcompact import (
    strip_images_from_messages,
    strip_images_from_typed_messages,
    microcompact_messages,
)
from ...token_estimation import (
    count_messages_tokens,
    count_tokens,
    rough_token_count_estimation_for_messages,
)

from .grouping import group_messages_by_api_round
from .prompt import (
    get_compact_prompt,
    get_partial_compact_prompt,
    format_compact_summary,
    get_compact_user_summary_message,
)
from .compact_warning import suppress_compact_warning, clear_compact_warning_suppression
from .post_compact_cleanup import PostCompactContext, run_post_compact_cleanup
from .post_compact_attachments import (
    create_post_compact_file_attachments,
    create_plan_attachment_if_needed,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Compaction Telemetry (PR 3)
# =============================================================================

@dataclass
class CompactionTelemetry:
    """Telemetry data for a compaction event."""
    trigger: str
    tokens_shed: int
    pre_compact_token_count: int
    post_compact_token_count: int
    compaction_cost_usd: float
    cache_hit_rate_before: float | None = None
    cache_hit_rate_after: float | None = None
    estimated_cost_delta_usd: float | None = None
    cost_increased: bool = False


def _get_recent_cache_hit_rate(model: str | None = None) -> float | None:
    """
    Calculate the cache hit rate from recent API usage.

    Returns cache_read / (input + cache_creation + cache_read) as a percentage,
    or None if no usage data is available.
    """
    try:
        from src.bootstrap.state import get_model_usage

        model_usage = get_model_usage()
        if not model_usage:
            return None

        # If model specified, use that; otherwise sum across all models
        if model and model in model_usage:
            usages = [model_usage[model]]
        else:
            usages = list(model_usage.values())

        total_input = sum(u.input_tokens for u in usages)
        total_cache_creation = sum(u.cache_creation_input_tokens for u in usages)
        total_cache_read = sum(u.cache_read_input_tokens for u in usages)

        prompt_total = total_input + total_cache_creation + total_cache_read
        if prompt_total == 0:
            return None

        return (total_cache_read / prompt_total) * 100.0
    except Exception:
        return None


def _estimate_compaction_cost_delta(
    pre_compact_tokens: int,
    post_compact_tokens: int,
    cache_hit_rate_before: float | None,
    cache_hit_rate_after: float | None,
    model: str,
) -> float | None:
    """
    Estimate the cost delta from compaction.

    Positive = compaction increased cost (cache-hostile).
    Negative = compaction decreased cost (cache-friendly).
    """
    try:
        from src.services.pricing import get_pricing, compute_cost

        pricing = get_pricing(model)
        if not pricing:
            return None

        input_rate = pricing.get("input", 0)
        cache_read_rate = pricing.get("cache_read", 0)

        # If cache_read rate is not explicitly set, it's often a fraction of input rate
        if cache_read_rate == 0:
            cache_read_rate = input_rate * 0.1  # typical 90% discount

        tokens_shed = pre_compact_tokens - post_compact_tokens
        if tokens_shed <= 0:
            return None

        # Estimate: tokens shed would have been a mix of cached and uncached
        # based on the cache hit rate before compaction
        hit_rate_before = (cache_hit_rate_before or 0) / 100.0

        # Cost of shed tokens at old hit rate
        uncached_shed = tokens_shed * (1 - hit_rate_before)
        cached_shed = tokens_shed * hit_rate_before
        cost_shed = (uncached_shed * input_rate + cached_shed * cache_read_rate) / 1_000_000

        # Cost of compaction summary call (already tracked in compaction_usage)
        # This is a separate API call that we already pay for

        # The delta is the cost of the compaction call minus the ongoing
        # savings from not sending those tokens in future turns
        # For now, just return the cost of shed tokens as a baseline
        return cost_shed
    except Exception:
        return None


def _log_compaction_telemetry(telemetry: CompactionTelemetry) -> None:
    """Log compaction telemetry with warning if cost increased."""
    logger.info(
        "compaction_telemetry: trigger=%s tokens_shed=%d pre=%d post=%d "
        "compaction_cost=$%.6f hit_before=%.1f%% hit_after=%s delta=$%.6f increased=%s",
        telemetry.trigger,
        telemetry.tokens_shed,
        telemetry.pre_compact_token_count,
        telemetry.post_compact_token_count,
        telemetry.compaction_cost_usd,
        telemetry.cache_hit_rate_before if telemetry.cache_hit_rate_before is not None else -1,
        f"{telemetry.cache_hit_rate_after:.1f}%" if telemetry.cache_hit_rate_after is not None else "pending",
        telemetry.estimated_cost_delta_usd if telemetry.estimated_cost_delta_usd is not None else -1,
        telemetry.cost_increased,
    )

    if telemetry.cost_increased:
        logger.warning(
            "CACHE-HOSTILE COMPACTION: trigger=%s shed %d tokens but increased "
            "effective cost (delta=$%.6f). Pre-hit=%.1f%%, Post-hit=%.1f%%. "
            "Consider: smaller compaction window, different model, or disable auto-compact.",
            telemetry.trigger,
            telemetry.tokens_shed,
            telemetry.estimated_cost_delta_usd or 0,
            telemetry.cache_hit_rate_before or 0,
            telemetry.cache_hit_rate_after or 0,
        )


def _calculate_cache_hit_rate_from_usage(usage: dict[str, Any]) -> float | None:
    """
    Calculate cache hit rate from a single API response's usage dict.

    Handles both Anthropic-native (cache_read_input_tokens) and
    OpenAI-compatible (prompt_tokens_details.cached_tokens) formats.
    """
    try:
        # Anthropic-native format
        if "cache_read_input_tokens" in usage:
            input_tokens = int(usage.get("input_tokens", 0) or 0)
            cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
            cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
            prompt_total = input_tokens + cache_creation + cache_read
            if prompt_total == 0:
                return None
            return (cache_read / prompt_total) * 100.0

        # OpenAI-compatible format
        if "prompt_tokens_details" in usage:
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            cached = usage.get("prompt_tokens_details", {})
            cache_read = int(cached.get("cached_tokens", 0) if isinstance(cached, dict) else 0)
            # OpenAI doesn't have cache_creation, so prompt_total = prompt_tokens
            if prompt_tokens == 0:
                return None
            return (cache_read / prompt_tokens) * 100.0

        return None
    except Exception:
        return None


def log_post_compaction_telemetry(
    trigger: str,
    tokens_shed: int,
    pre_compact_token_count: int,
    post_compact_token_count: int,
    compaction_cost_usd: float,
    cache_hit_rate_before: float | None,
    response_usage: dict[str, Any],
    model: str,
) -> None:
    """
    Log telemetry for the first turn after compaction.

    Called when consume_post_compaction() returns True. Measures the
    cache hit rate from the first post-compaction API response and
    logs updated telemetry.
    """
    cache_hit_rate_after = _calculate_cache_hit_rate_from_usage(response_usage)

    # Recalculate cost delta with actual post-compaction hit rate
    estimated_delta = _estimate_compaction_cost_delta(
        pre_compact_token_count,
        post_compact_token_count,
        cache_hit_rate_before,
        cache_hit_rate_after,
        model,
    )

    cost_increased = (
        estimated_delta is not None
        and estimated_delta > compaction_cost_usd
    )

    logger.info(
        "compaction_telemetry_post: trigger=%s tokens_shed=%d pre=%d post=%d "
        "compaction_cost=$%.6f hit_before=%.1f%% hit_after=%.1f%% delta=$%.6f increased=%s",
        trigger,
        tokens_shed,
        pre_compact_token_count,
        post_compact_token_count,
        compaction_cost_usd,
        cache_hit_rate_before if cache_hit_rate_before is not None else -1,
        cache_hit_rate_after if cache_hit_rate_after is not None else -1,
        estimated_delta if estimated_delta is not None else -1,
        cost_increased,
    )

    if cost_increased:
        logger.warning(
            "CACHE-HOSTILE COMPACTION (confirmed): trigger=%s shed %d tokens "
            "but increased effective cost (delta=$%.6f). Pre-hit=%.1f%%, "
            "Post-hit=%.1f%%. Consider: smaller compaction window, different "
            "model, or disable auto-compact.",
            trigger,
            tokens_shed,
            estimated_delta or 0,
            cache_hit_rate_before or 0,
            cache_hit_rate_after or 0,
        )
    elif cache_hit_rate_after is not None and cache_hit_rate_before is not None:
        hit_rate_delta = cache_hit_rate_after - cache_hit_rate_before
        if hit_rate_delta < -5:  # significant drop
            logger.warning(
                "COMPACTION CACHE HIT RATE DROP: trigger=%s hit rate fell "
                "%.1f%% -> %.1f%% (delta=%.1f%%). Cache prefix may have been "
                "disturbed by compaction.",
                trigger,
                cache_hit_rate_before,
                cache_hit_rate_after,
                hit_rate_delta,
            )

# Maximum output tokens for the summary model
COMPACT_MAX_OUTPUT_TOKENS = 8_192

# Maximum retries on PROMPT_TOO_LONG
MAX_PTL_RETRIES = 3

# Marker prepended after truncation to maintain valid message structure
PTL_RETRY_MARKER = "[earlier conversation truncated for compaction retry]"

# System prompt for the summarization model call. Matches TS reference
# (compact.ts:1305) — keeps the summarizer focused on summarizing rather
# than continuing the parent agent task.
COMPACT_SYSTEM_PROMPT = (
    "You are a helpful AI assistant tasked with summarizing conversations."
)

# Error messages
ERROR_MESSAGE_PROMPT_TOO_LONG = (
    "Your conversation is too long. Please use /compact to reduce context size, "
    "or start a new conversation."
)
ERROR_MESSAGE_NOT_ENOUGH_MESSAGES = "Not enough messages to compact."
ERROR_MESSAGE_INCOMPLETE_RESPONSE = "Failed to generate conversation summary."

_PTL_TOKEN_GAP_REGEX = re.compile(
    r"prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)",
    re.IGNORECASE,
)


def parse_prompt_too_long_token_gap(error_str: str) -> int | None:
    """
    Parse the token gap from a prompt-too-long error message.

    Port of ``parsePromptTooLongTokenCounts`` + ``getPromptTooLongTokenGap``
    from ``typescript/src/services/api/errors.ts``.

    Returns ``actualTokens - limitTokens`` when the message matches the
    standard Anthropic format (e.g., ``prompt is too long: 137500 tokens > 135000 maximum``),
    or ``None`` when the message is unparseable (some Vertex/Bedrock variants).
    """
    if not error_str:
        return None
    match = _PTL_TOKEN_GAP_REGEX.search(error_str)
    if not match:
        return None
    try:
        actual = int(match.group(1))
        limit = int(match.group(2))
    except (ValueError, IndexError):
        return None
    gap = actual - limit
    return gap if gap > 0 else None


def _collect_discovered_tool_names(messages: list[Message]) -> list[str]:
    """
    Collect unique tool names from ``tool_use`` blocks in the given messages.

    Used to populate ``CompactBoundaryMetadata.pre_compact_discovered_tools``
    so a session-resume loader knows which tools were active before compaction.
    """
    seen: set[str] = set()
    for msg in messages:
        if getattr(msg, "role", None) != "assistant":
            continue
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            name = ""
            if isinstance(block, ToolUseBlock):
                name = block.name
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                name = str(block.get("name", ""))
            if name:
                seen.add(name)
    return sorted(seen)


@dataclass
class CompactionResult:
    """Result of a compaction operation."""
    boundary_marker: Message
    summary_messages: list[UserMessage]
    messages_to_keep: list[Message] = field(default_factory=list)
    attachments: list[Message] = field(default_factory=list)
    pre_compact_token_count: int | None = None
    post_compact_token_count: int | None = None
    compaction_usage: dict[str, int] | None = None
    user_display_message: str | None = None
    trigger: str = "manual"
    tokens_saved: int = 0


@dataclass
class CompactContext:
    """Context for a compaction operation."""
    provider: Any  # BaseProvider
    model: str
    messages: list[Message]
    custom_instructions: str | None = None
    trigger: str = "manual"
    post_compact_ctx: PostCompactContext | None = None
    on_compact_progress: Any | None = None  # callback
    read_file_state: dict[str, Any] | None = None
    plan_file_path: str | None = None
    memory_paths: set[str] | None = None


def _is_prompt_too_long_error(error_str: str) -> bool:
    """Check if an error string indicates a prompt-too-long condition."""
    lower = error_str.lower()
    return (
        "prompt_too_long" in lower
        or "prompt is too long" in lower
        or "prompt too long" in lower
        or "context_length_exceeded" in lower
    )


def _mark_post_compaction_state() -> None:
    """Set the bootstrap pending_post_compaction flag (ch05 round-3 G4).

    Consume side is OTel cache-miss telemetry (TS consumePostCompaction,
    services/api/logging.ts) — deferred; the mark keeps the state real.
    Never raises."""
    try:
        from src.bootstrap.state import mark_post_compaction

        mark_post_compaction()
    except Exception:
        logger.debug("mark_post_compaction failed", exc_info=True)


def _record_compaction_usage(context: "CompactContext", usage: Any) -> None:
    """Record a summarize call's usage into the bootstrap cost totals
    (ch04 round-3 G1 — TS counts every API call via addToTotalSessionCost
    in the API layer; the port's direct provider calls must self-record).
    Records under the summarize model (``context.model``), falling back
    to the provider's. Never raises.

    Known gap: unlike the main loop and the advisor, summarize calls feed
    no ``add_to_total_duration_state`` (this helper runs after the call,
    with no start time in scope), so /cost's "Total duration (API)"
    excludes compaction while its cost total includes it."""
    try:
        from src.cost_tracker import record_api_usage

        model = (
            getattr(context, "model", None)
            or getattr(getattr(context, "provider", None), "model", None)
            or "unknown"
        )
        record_api_usage(model, usage)
    except Exception:
        logger.debug("compaction cost recording failed", exc_info=True)


def _fallback_summary(messages: list[Message]) -> str:
    """Generate a simple text fallback summary when the LLM call fails."""
    user_msgs: list[str] = []
    assistant_msgs: list[str] = []
    tool_uses: list[str] = []

    for msg in messages:
        role = msg.role if hasattr(msg, "role") else "user"
        content = msg.content if hasattr(msg, "content") else ""

        if role == "user":
            if isinstance(content, str):
                user_msgs.append(content[:200])
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, TextBlock):
                        user_msgs.append(block.text[:200])
                    elif isinstance(block, dict) and block.get("type") == "text":
                        user_msgs.append(block.get("text", "")[:200])
        elif role == "assistant":
            if isinstance(content, str):
                assistant_msgs.append(content[:200])
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, TextBlock):
                        assistant_msgs.append(block.text[:200])
                    elif isinstance(block, dict) and block.get("type") == "text":
                        assistant_msgs.append(block.get("text", "")[:200])
                    elif isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_uses.append(block.get("name", ""))

    summary_parts = [f"Conversation had {len(messages)} messages."]
    if tool_uses:
        summary_parts.append(f"Tools used: {', '.join(tool_uses[:10])}")
    if user_msgs:
        summary_parts.append(f"Last user message: {user_msgs[-1][:150]}")
    if assistant_msgs:
        summary_parts.append(f"Last assistant message: {assistant_msgs[-1][:150]}")

    return "\n".join(summary_parts)


def truncate_head_for_ptl_retry(
    messages: list[Message],
    token_gap: int | None = None,
) -> list[Message] | None:
    """
    Drop the oldest API-round groups from messages until ``token_gap`` is covered.
    Falls back to dropping 20% of groups when the gap is unknown.
    Returns None when nothing can be dropped without leaving an empty summarize set.

    Port of ``truncateHeadForPTLRetry`` in compact.ts.
    """
    # Strip our own synthetic marker from a previous retry before grouping.
    input_messages = messages
    if (
        messages
        and hasattr(messages[0], "role")
        and messages[0].role == "user"
        and hasattr(messages[0], "isMeta")
        and messages[0].isMeta
    ):
        content = messages[0].content
        if isinstance(content, str) and content == PTL_RETRY_MARKER:
            input_messages = messages[1:]
        elif isinstance(content, list) and len(content) == 1:
            block = content[0]
            text = ""
            if isinstance(block, TextBlock):
                text = block.text
            elif isinstance(block, dict):
                text = block.get("text", "")
            if text == PTL_RETRY_MARKER:
                input_messages = messages[1:]

    rounds = group_messages_by_api_round(input_messages)
    if len(rounds) < 2:
        return None

    if token_gap is not None and token_gap > 0:
        acc = 0
        drop_count = 0
        for r in rounds:
            acc += rough_token_count_estimation_for_messages(r.messages)
            drop_count += 1
            if acc >= token_gap:
                break
    else:
        drop_count = max(1, len(rounds) // 5)

    # Keep at least one group so there's something to summarize
    drop_count = min(drop_count, len(rounds) - 1)
    if drop_count < 1:
        return None

    remaining_rounds = rounds[drop_count:]
    sliced: list[Message] = []
    for r in remaining_rounds:
        sliced.extend(r.messages)

    # If the result starts with an assistant message, prepend a synthetic
    # user marker so the API accepts the sequence.
    if sliced and hasattr(sliced[0], "role") and sliced[0].role == "assistant":
        marker = UserMessage(
            content=[TextBlock(text=PTL_RETRY_MARKER)],
            isMeta=True,
        )
        return [marker] + sliced

    return sliced


async def compact_conversation(
    context: CompactContext,
) -> CompactionResult:
    """
    Compact a conversation by summarizing older messages via LLM.

    This is the Python equivalent of the TypeScript ``compactConversation()``.

    Args:
        context: Compaction context with provider, model, messages, etc.

    Returns:
        ``CompactionResult`` with boundary, summary, and metadata.

    Raises:
        ValueError: If there are fewer than 2 messages to compact.
    """
    clear_compact_warning_suppression()

    messages = context.messages
    # Get messages after the last boundary (skip already-summarized)
    messages_to_compact = get_messages_after_boundary(messages)

    if len(messages_to_compact) < 2:
        raise ValueError(ERROR_MESSAGE_NOT_ENOUGH_MESSAGES)

    # Count pre-compact tokens
    api_messages = normalize_messages_for_api(messages_to_compact)
    pre_compact_tokens = count_messages_tokens(api_messages)

    # Pre-process: strip images, microcompact
    api_messages_stripped = strip_images_from_messages(api_messages)
    compacted_api, _mc_saved = microcompact_messages(api_messages_stripped)

    # Build summary prompt
    prompt = get_compact_prompt(context.custom_instructions)

    # Build messages for the summary API call
    messages_for_summary = list(compacted_api)

    # Call LLM to generate summary with PTL retry loop
    summary_text = ""
    compaction_usage: dict[str, int] | None = None

    for attempt in range(1, MAX_PTL_RETRIES + 1):
        # Append prompt as the last user message
        summary_request_messages: list[dict[str, Any]] = list(messages_for_summary)
        summary_request_messages.append({"role": "user", "content": prompt})

        try:
            response = await context.provider.chat_async(
                messages=summary_request_messages,
                tools=None,
                model=context.model,
                max_tokens=COMPACT_MAX_OUTPUT_TOKENS,
                system=COMPACT_SYSTEM_PROMPT,
            )
            summary_text = response.content.strip() if response.content else ""
            if response.usage:
                compaction_usage = dict(response.usage)
            # ch04 round-3 G1: compaction's usage was extracted but never
            # recorded — a dead cost head of the same class as the main
            # loop's (critic-found). Distinct API call from any
            # query_source="compact" loop turn, so no double count.
            _record_compaction_usage(context, response.usage)
            # ch05 round-3 G4: mark the post-compaction cache state at the
            # SUCCESS site (TS compact.ts:73 / autoCompact.ts:350) — never
            # in run_post_compact_cleanup, which /clear-adjacent paths may
            # share; /clear must not register as a compaction.
            _mark_post_compaction_state()
            break
        except Exception as e:
            error_str = str(e)
            if _is_prompt_too_long_error(error_str):
                if attempt < MAX_PTL_RETRIES:
                    # Use API-round-aware truncation; parse the real token
                    # gap from the error so we drop the right number of
                    # rounds in one retry instead of guessing.
                    token_gap = parse_prompt_too_long_token_gap(error_str)
                    truncated = truncate_head_for_ptl_retry(
                        messages_to_compact, token_gap=token_gap
                    )
                    if truncated is not None:
                        truncated_api = normalize_messages_for_api(truncated)
                        truncated_stripped = strip_images_from_messages(truncated_api)
                        messages_for_summary, _ = microcompact_messages(truncated_stripped)
                        logger.info(
                            "PTL retry %d: dropped %d messages, %d remaining (gap=%s)",
                            attempt,
                            len(messages_to_compact) - len(truncated),
                            len(truncated),
                            token_gap,
                        )
                        messages_to_compact = truncated
                        continue
                    # Fallback: simple halving
                    half = max(2, len(messages_for_summary) // 2)
                    messages_for_summary = messages_for_summary[-half:]
                    continue
                raise ValueError(ERROR_MESSAGE_PROMPT_TOO_LONG) from e

            # Try sync fallback for non-PTL errors
            try:
                response = context.provider.chat(
                    summary_request_messages,
                    tools=None,
                    model=context.model,
                    max_tokens=COMPACT_MAX_OUTPUT_TOKENS,
                    system=COMPACT_SYSTEM_PROMPT,
                )
                summary_text = response.content.strip() if response.content else ""
                if response.usage:
                    compaction_usage = dict(response.usage)
                _record_compaction_usage(context, response.usage)
                _mark_post_compaction_state()
                break
            except Exception as e2:
                logger.warning(
                    "Compact LLM call failed: %s, sync fallback: %s, using text extraction",
                    e, e2,
                )
                summary_text = _fallback_summary(messages_to_compact)
                break

    if not summary_text:
        summary_text = _fallback_summary(messages_to_compact)

    summary_text = format_compact_summary(summary_text)

    # Nano: append pi's cumulative <read-files>/<modified-files> ledger so
    # the model keeps its working set across any number of compactions
    # (src/nano/compact_ledger.py). Path lists, not contents — a few dozen
    # tokens of insurance. No-op (and no import) outside nano mode.
    from src.nano.state import is_nano_mode

    if is_nano_mode():
        try:
            from src.nano.compact_ledger import append_file_ops_ledger

            summary_text = append_file_ops_ledger(
                summary_text, messages, messages_to_compact
            )
        except Exception:
            logger.debug("nano file-op ledger failed", exc_info=True)

    # Create boundary marker
    last_msg_uuid = None
    if messages_to_compact:
        last_msg_uuid = getattr(messages_to_compact[-1], "uuid", None)

    discovered_tools = _collect_discovered_tool_names(messages_to_compact)

    boundary_msg = create_compact_boundary_message(
        trigger=context.trigger,
        pre_compact_token_count=pre_compact_tokens,
        last_message_uuid=last_msg_uuid,
        messages_summarized=len(messages_to_compact),
        discovered_tools=discovered_tools,
    )

    # Create summary message
    suppress_follow_up = context.trigger == "auto"
    formatted_summary = get_compact_user_summary_message(
        summary_text,
        suppress_follow_up=suppress_follow_up,
    )
    summary_msg = create_compact_summary_message(formatted_summary)

    # Create post-compact attachments
    attachments: list[Message] = []
    if context.read_file_state:
        attachments.extend(
            create_post_compact_file_attachments(
                context.read_file_state,
                plan_file_path=context.plan_file_path,
                memory_paths=context.memory_paths,
            )
        )
    plan_attachment = create_plan_attachment_if_needed(context.plan_file_path)
    if plan_attachment is not None:
        attachments.append(plan_attachment)

    # Post-compact cleanup
    if context.post_compact_ctx:
        run_post_compact_cleanup(context.post_compact_ctx)

    suppress_compact_warning()

    # Calculate tokens saved
    post_api_msgs = [{"role": "user", "content": formatted_summary}]
    post_compact_tokens = count_messages_tokens(post_api_msgs)
    tokens_saved = max(0, pre_compact_tokens - post_compact_tokens)

    user_display = (
        f"Compacted conversation (~{tokens_saved:,} tokens saved). "
        f"Pre-compact: {pre_compact_tokens:,} tokens."
    )

    # --- PR 3: Compaction Telemetry ---
    compaction_cost_usd = 0.0
    if compaction_usage:
        try:
            from src.services.pricing import compute_cost
            compaction_cost_usd = compute_cost(context.model, compaction_usage)
        except Exception:
            pass

    cache_hit_rate_before = _get_recent_cache_hit_rate(context.model)

    estimated_delta = _estimate_compaction_cost_delta(
        pre_compact_tokens,
        post_compact_tokens,
        cache_hit_rate_before,
        None,  # post-compaction hit rate measured on next turn
        context.model,
    )

    telemetry = CompactionTelemetry(
        trigger=context.trigger,
        tokens_shed=tokens_saved,
        pre_compact_token_count=pre_compact_tokens,
        post_compact_token_count=post_compact_tokens,
        compaction_cost_usd=compaction_cost_usd,
        cache_hit_rate_before=cache_hit_rate_before,
        estimated_cost_delta_usd=estimated_delta,
        cost_increased=(estimated_delta is not None and estimated_delta > compaction_cost_usd),
    )

    _log_compaction_telemetry(telemetry)

    # Store telemetry for post-compaction measurement
    from src.bootstrap.state import set_compaction_telemetry_data, CompactionTelemetryData
    set_compaction_telemetry_data(CompactionTelemetryData(
        trigger=telemetry.trigger,
        tokens_shed=telemetry.tokens_shed,
        pre_compact_token_count=telemetry.pre_compact_token_count,
        post_compact_token_count=telemetry.post_compact_token_count,
        compaction_cost_usd=telemetry.compaction_cost_usd,
        cache_hit_rate_before=telemetry.cache_hit_rate_before,
        model=context.model,
    ))
    # --- End PR 3 Telemetry ---

    return CompactionResult(
        boundary_marker=boundary_msg,
        summary_messages=[summary_msg],
        messages_to_keep=[],
        attachments=attachments,
        pre_compact_token_count=pre_compact_tokens,
        post_compact_token_count=post_compact_tokens,
        compaction_usage=compaction_usage,
        user_display_message=user_display,
        trigger=context.trigger,
        tokens_saved=tokens_saved,
    )


async def partial_compact_conversation(
    context: CompactContext,
    pivot_index: int,
    direction: str = "earlier",
) -> CompactionResult:
    """
    Compact a prefix or suffix of the conversation.

    Args:
        context: Compaction context.
        pivot_index: Index that splits summarize / keep.
        direction: ``"earlier"`` or ``"up_to"`` summarizes [0, pivot), keeps [pivot, end].
                   ``"later"`` or ``"from"`` keeps [0, pivot), summarizes [pivot, end].

    Returns:
        ``CompactionResult``
    """
    messages = context.messages

    if direction in ("earlier", "up_to"):
        messages_to_summarize = messages[:pivot_index]
        messages_to_keep = [
            m for m in messages[pivot_index:]
            if not is_compact_boundary_message(m)
        ]
    else:
        messages_to_keep = [
            m for m in messages[:pivot_index]
            if m.role != "progress"
        ]
        messages_to_summarize = messages[pivot_index:]

    if len(messages_to_summarize) < 1:
        raise ValueError(ERROR_MESSAGE_NOT_ENOUGH_MESSAGES)

    # Count pre-compact tokens
    api_messages = normalize_messages_for_api(messages_to_summarize)
    pre_compact_tokens = count_messages_tokens(api_messages)

    # Build prompt
    prompt = get_partial_compact_prompt(direction, context.custom_instructions)

    # Pre-process
    api_messages_stripped = strip_images_from_messages(api_messages)
    compacted_api, _ = microcompact_messages(api_messages_stripped)

    # Build messages for the summary API call with PTL retry
    messages_for_summary: list[dict[str, Any]] = list(compacted_api)
    summary_text = ""
    compaction_usage: dict[str, int] | None = None

    for attempt in range(1, MAX_PTL_RETRIES + 1):
        summary_request_messages: list[dict[str, Any]] = list(messages_for_summary)
        summary_request_messages.append({"role": "user", "content": prompt})

        try:
            response = await context.provider.chat_async(
                messages=summary_request_messages,
                tools=None,
                model=context.model,
                max_tokens=COMPACT_MAX_OUTPUT_TOKENS,
                system=COMPACT_SYSTEM_PROMPT,
            )
            summary_text = response.content.strip() if response.content else ""
            if response.usage:
                compaction_usage = dict(response.usage)
            # ch04 round-3 G1: compaction's usage was extracted but never
            # recorded — a dead cost head of the same class as the main
            # loop's (critic-found). Distinct API call from any
            # query_source="compact" loop turn, so no double count.
            _record_compaction_usage(context, response.usage)
            # ch05 round-3 G4: mark the post-compaction cache state at the
            # SUCCESS site (TS compact.ts:73 / autoCompact.ts:350) — never
            # in run_post_compact_cleanup, which /clear-adjacent paths may
            # share; /clear must not register as a compaction.
            _mark_post_compaction_state()
            break
        except Exception as e:
            error_str = str(e)
            if _is_prompt_too_long_error(error_str) and attempt < MAX_PTL_RETRIES:
                token_gap = parse_prompt_too_long_token_gap(error_str)
                truncated = truncate_head_for_ptl_retry(
                    messages_to_summarize, token_gap=token_gap
                )
                if truncated is not None:
                    truncated_api = normalize_messages_for_api(truncated)
                    truncated_stripped = strip_images_from_messages(truncated_api)
                    messages_for_summary, _ = microcompact_messages(truncated_stripped)
                    messages_to_summarize = truncated
                    continue
                # Fallback: simple halving
                half = max(2, len(messages_for_summary) // 2)
                messages_for_summary = messages_for_summary[-half:]
                continue

            logger.warning("Partial compact LLM call failed: %s", e)
            summary_text = _fallback_summary(messages_to_summarize)
            break

    if not summary_text:
        summary_text = _fallback_summary(messages_to_summarize)

    summary_text = format_compact_summary(summary_text)

    # Create boundary + summary
    last_msg_uuid = None
    if messages_to_summarize:
        last_msg_uuid = getattr(messages_to_summarize[-1], "uuid", None)

    discovered_tools = _collect_discovered_tool_names(messages_to_summarize)

    boundary_msg = create_compact_boundary_message(
        trigger=context.trigger,
        pre_compact_token_count=pre_compact_tokens,
        last_message_uuid=last_msg_uuid,
        messages_summarized=len(messages_to_summarize),
        discovered_tools=discovered_tools,
    )

    formatted_summary = get_compact_user_summary_message(summary_text, suppress_follow_up=False)
    summary_msg = create_compact_summary_message(formatted_summary)

    # Annotate the boundary with preserved-segment metadata so the
    # message-loader can relink kept messages into the post-compact chain.
    # Anchor selection mirrors compact.ts:1080-1083:
    #   'up_to' (suffix kept) → anchor = last summary message UUID
    #   'from'/'later' (prefix kept) → anchor = boundary UUID
    if direction in ("up_to", "earlier"):
        anchor_uuid = (
            getattr(summary_msg, "uuid", None)
            or getattr(boundary_msg, "uuid", None)
            or ""
        )
    else:
        anchor_uuid = getattr(boundary_msg, "uuid", None) or ""

    if anchor_uuid:
        boundary_msg = annotate_boundary_with_preserved_segment(
            boundary_msg, anchor_uuid, list(messages_to_keep)
        )

    # Create post-compact attachments
    attachments: list[Message] = []
    if context.read_file_state:
        attachments.extend(
            create_post_compact_file_attachments(
                context.read_file_state,
                preserved_messages=list(messages_to_keep),
                plan_file_path=context.plan_file_path,
                memory_paths=context.memory_paths,
            )
        )
    plan_attachment = create_plan_attachment_if_needed(context.plan_file_path)
    if plan_attachment is not None:
        attachments.append(plan_attachment)

    suppress_compact_warning()

    # --- PR 3: Compaction Telemetry ---
    compaction_cost_usd = 0.0
    if compaction_usage:
        try:
            from src.services.pricing import compute_cost
            compaction_cost_usd = compute_cost(context.model, compaction_usage)
        except Exception:
            pass

    cache_hit_rate_before = _get_recent_cache_hit_rate(context.model)

    # For partial compaction, tokens_saved = pre_compact_tokens (entire summarized portion)
    # post_compact_tokens is just the summary message
    post_api_msgs = [{"role": "user", "content": formatted_summary}]
    post_compact_tokens = count_messages_tokens(post_api_msgs)
    tokens_saved = max(0, pre_compact_tokens - post_compact_tokens)

    estimated_delta = _estimate_compaction_cost_delta(
        pre_compact_tokens,
        post_compact_tokens,
        cache_hit_rate_before,
        None,
        context.model,
    )

    telemetry = CompactionTelemetry(
        trigger=f"{context.trigger}:{direction}",
        tokens_shed=tokens_saved,
        pre_compact_token_count=pre_compact_tokens,
        post_compact_token_count=post_compact_tokens,
        compaction_cost_usd=compaction_cost_usd,
        cache_hit_rate_before=cache_hit_rate_before,
        estimated_cost_delta_usd=estimated_delta,
        cost_increased=(estimated_delta is not None and estimated_delta > compaction_cost_usd),
    )

    _log_compaction_telemetry(telemetry)
    # --- End PR 3 Telemetry ---

    return CompactionResult(
        boundary_marker=boundary_msg,
        summary_messages=[summary_msg],
        messages_to_keep=list(messages_to_keep),
        attachments=attachments,
        pre_compact_token_count=pre_compact_tokens,
        post_compact_token_count=post_compact_tokens,
        compaction_usage=compaction_usage,
        trigger=context.trigger,
        tokens_saved=tokens_saved,
    )
