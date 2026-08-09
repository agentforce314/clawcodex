"""DeepSeek provider implementation.

DeepSeek exposes an OpenAI-compatible API at https://api.deepseek.com.
Current production models are ``deepseek-v4-pro`` and ``deepseek-v4-flash``;
the legacy aliases ``deepseek-chat`` / ``deepseek-reasoner`` are being
deprecated and resolve to the non-thinking / thinking modes of
``deepseek-v4-flash`` respectively.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

try:
    from openai import OpenAI  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    OpenAI = None

from .base import ChatResponse
from .openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.debug("ignoring invalid %s=%r", name, raw)
        return default


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek provider using the OpenAI SDK against the DeepSeek base URL."""

    DEFAULT_BASE_URL = "https://api.deepseek.com"

    #: Marks this provider as DeepSeek so the query layer relocates the
    #: per-request-volatile (REQUEST-scope) system sections to a trailing tail,
    #: keeping the request prefix byte-stable for DeepSeek's automatic prefix
    #: cache (see ``query._split_system_prompt_blocks``).
    is_deepseek = True

    #: DeepSeek's OpenAI-format thinking vocabulary is ``low | high | max``
    #: (api-docs.deepseek.com/guides/thinking_mode). Thinking is ON by
    #: default at ``high``.
    #:
    #: The API does not VALIDATE this field — probed 2026-08-03 against
    #: ``deepseek-v4-flash``, every one of ``low / medium / high / xhigh /
    #: max / minimal`` returned 200, and so did a value the docs never list.
    #: So an unsupported level is not an error, it is silently discarded and
    #: the default (``high``) applies. Without the mapping below, ``xhigh``
    #: — chosen precisely when a task is hard — quietly delivered LESS
    #: reasoning than ``max``, which DeepSeek does support.
    supported_reasoning_efforts = ("low", "high", "max")

    #: ``medium`` has no DeepSeek equivalent and already behaved as ``high``
    #: (unknown → default), so this makes the existing behaviour explicit
    #: rather than changing it. ``xhigh`` is the real fix: it means "above
    #: high", and ``max`` is the only DeepSeek level that is.
    reasoning_effort_aliases = {"medium": "high", "xhigh": "max", "minimal": "low"}

    #: Default per-request ``max_tokens`` when the caller didn't set one
    #: (``CLAWCODEX_DEEPSEEK_MAX_OUTPUT`` overrides; ``0`` disables the cap).
    #:
    #: Why a cap at all: DeepSeek's thinking models do NOT budget reasoning
    #: to fit ``max_tokens`` — and with no ``max_tokens`` the server default
    #: is 131,072. Measured on terminal-bench 2.1 (tb21-flash-visiontool,
    #: 2026-08-08), ``deepseek-v4-flash`` at ``reasoning_effort=max``
    #: produced single requests of 131,071 output tokens that were 100%
    #: ``reasoning_content`` — at the observed ~90-145 tok/s one such request
    #: consumes an entire 900s task budget before the agent takes a single
    #: action. Per-request output distribution on that run: p95 = 8,076,
    #: p97 = 12,061, p99 = 26,191 — so 16K clears ~98% of real requests and
    #: bounds a runaway to ~2-3 minutes instead of the whole trial.
    DEFAULT_OUTPUT_CAP = 16_384

    #: Total pure-reasoning truncations after which thinking is disabled for
    #: the REST of the session (``CLAWCODEX_DEEPSEEK_FUSE_STICKY`` sets the
    #: threshold; ``0`` — the default — never goes sticky). A model that
    #: burns its whole output budget on reasoning repeatedly on the same
    #: task has demonstrated the pattern is stable; acting without thinking
    #: beats deliberating until the clock runs out. Deliberately opt-in:
    #: only time-budgeted contexts want it, so the headless entrypoint sets
    #: the env (default 3) and interactive sessions — which have no trial
    #: clock and live long enough for 3 unrelated burns to accumulate —
    #: keep thinking available unless the user opts in.
    DEFAULT_FUSE_STICKY_TRIPS = 0

    def __init__(
        self, api_key: str, base_url: Optional[str] = None, model: Optional[str] = None
    ):
        """Initialize DeepSeek provider.

        Args:
            api_key: DeepSeek API key (sk-...)
            base_url: Base URL (optional, defaults to https://api.deepseek.com)
            model: Default model (default: deepseek-v4-pro)
        """
        super().__init__(
            api_key,
            base_url or self.DEFAULT_BASE_URL,
            model or "deepseek-v4-pro",
        )
        # Reasoning-fuse state (see ``_recover_reasoning_burn``). Session
        # lifetime == provider instance lifetime.
        self._fuse_trips = 0
        self._thinking_disabled_sticky = False

    # ------------------------------------------------------------------ #
    # Reasoning-runaway protection ("the fuse")
    # ------------------------------------------------------------------ #
    #
    # Failure shape this guards (terminal-bench 2.1, deepseek-v4-flash,
    # effort=max): a request streams reasoning_content indefinitely, never
    # emits content or a tool call, and is eventually truncated by
    # ``max_tokens`` (``finish_reason="length"``). The stream watchdog can't
    # catch it — reasoning chunks ARE progress — and the httpx read timeout
    # can't either (bytes keep flowing). Uncapped, the truncation point is
    # the server-default 131,072 tokens ≈ 15-25 minutes of wall clock.
    #
    # Three layers, all env-tunable:
    #   1. a default per-request ``max_tokens`` (``_apply_output_cap``) so a
    #      runaway is bounded in the first place;
    #   2. on a truncated response with NOTHING actionable (no content, no
    #      tool calls), one immediate retry of the same request with
    #      ``thinking: {"type": "disabled"}`` — probed 2026-08-09: the same
    #      prompt that burned 4,096 reasoning tokens and returned nothing
    #      answered in 4.7s / 527 tokens with thinking disabled;
    #   3. after ``DEFAULT_FUSE_STICKY_TRIPS`` such burns in one session,
    #      thinking is disabled for every subsequent request.

    @property
    def _output_cap(self) -> int:
        return _env_int("CLAWCODEX_DEEPSEEK_MAX_OUTPUT", self.DEFAULT_OUTPUT_CAP)

    @property
    def _fuse_enabled(self) -> bool:
        return os.environ.get("CLAWCODEX_DEEPSEEK_FUSE", "").lower() not in (
            "0", "false", "no",
        )

    def _apply_output_cap(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Default ``max_tokens`` to the cap; clamp explicit values to 2x cap.

        The clamp bounds explicit escalations — most concretely the query
        loop's 64K max-output-tokens retry (``ESCALATED_MAX_TOKENS``), which
        reaches this wire now that ``finish_reason="length"`` is normalized
        onto the internal ``max_tokens`` stop-reason vocabulary. On this
        wire 64K of output is 7-12 minutes of generation at the observed
        90-145 tok/s — longer than most terminal-bench task budgets have
        left by the time it fires; 2x cap keeps the retry meaningfully
        bigger while staying inside a budget.
        """
        cap = self._output_cap
        if cap <= 0:
            return kwargs
        current = kwargs.get("max_tokens")
        if current is None:
            kwargs = dict(kwargs)
            kwargs["max_tokens"] = cap
        else:
            try:
                current_int = int(current)
            except (TypeError, ValueError):
                current_int = None
            if current_int is not None and current_int > cap * 2:
                kwargs = dict(kwargs)
                kwargs["max_tokens"] = cap * 2
        return kwargs

    @staticmethod
    def _force_thinking_disabled(kwargs: dict[str, Any]) -> dict[str, Any]:
        """Return kwargs with thinking hard-off (OpenAI-format shape).

        ``reasoning_effort`` is dropped rather than kept alongside: the two
        fields configure the same mode and the docs define no precedence
        between a level and an explicit disable.
        """
        kwargs = dict(kwargs)
        extra_body = dict(kwargs.get("extra_body") or {})
        extra_body.pop("reasoning_effort", None)
        extra_body["thinking"] = {"type": "disabled"}
        kwargs["extra_body"] = extra_body
        return kwargs

    @staticmethod
    def _is_reasoning_burn(response: ChatResponse) -> bool:
        """True when a truncated response contains nothing actionable.

        ``finish_reason == "length"`` with no content and no tool calls means
        the entire output budget went to ``reasoning_content`` (or, equally
        useless, to nothing) — the agent loop cannot advance on it.
        """
        if response.finish_reason != "length":
            return False
        if (response.content or "").strip():
            return False
        if response.tool_uses:
            return False
        return True

    @staticmethod
    def _drop_amputated_trailing_tool_call(response: ChatResponse) -> ChatResponse:
        """Refuse a length-truncated trailing tool call that was JSON-repaired.

        When the output cap lands mid-tool-call, the base parser closes the
        dangling JSON and returns a VALID-LOOKING call — a ``Write`` cut
        mid-``content`` becomes a well-formed ``Write`` with silently
        truncated content that would be executed and report success. On
        ``finish_reason="length"``, a repaired LAST call is that amputation
        (earlier calls completed before the cap and keep their repair-only
        semantics). Dropping it converts the hazard into one of two safe
        shapes: earlier complete calls still run, or — if nothing is left —
        the response becomes a reasoning-burn the fuse/loop recovery handles.

        DeepSeek-only today because only this provider caps ``max_tokens``,
        but ``repaired_tool_indices`` is produced base-wide and any
        OpenAI-compat provider can length-truncate at its server default —
        promoting this guard to ``OpenAICompatibleProvider`` is the natural
        follow-up when another provider grows a cap.
        """
        if response.finish_reason != "length":
            return response
        if not response.tool_uses or not response.repaired_tool_indices:
            return response
        last = len(response.tool_uses) - 1
        if last not in response.repaired_tool_indices:
            return response
        dropped = response.tool_uses[last]
        logger.warning(
            "deepseek output cap amputated trailing tool call %s(%s) — "
            "dropping it rather than executing a JSON-repaired truncation",
            dropped.get("name"),
            ", ".join(list(dropped.get("input") or {})[:4]),
        )
        response.tool_uses = response.tool_uses[:last] or None
        response.repaired_tool_indices = [
            i for i in response.repaired_tool_indices if i != last
        ] or None
        return response

    @staticmethod
    def _merge_burn_usage(
        burned: dict[str, Any], final: dict[str, Any]
    ) -> dict[str, Any]:
        """Fold the burned request's OUTPUT-side usage into the response's.

        The query loop records usage from the response it receives; without
        the merge, the burned request's generated tokens (real, billed)
        would vanish from cost accounting. Only output-side counters are
        summed: the input-side fields (``input_tokens``, ``cache_*``) are
        the documented last-wins "live context" measure (see
        agent_loop_compat's usage notes), and summing two requests' prompt
        sides would double the context every fused turn reports. The one
        consequence — the burned attempt's prompt-side billing is
        undercounted — is accepted.
        """
        merged = dict(final)
        for key in ("output_tokens", "total_tokens", "reasoning_tokens"):
            value = burned.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                base = merged.get(key)
                merged[key] = (base if isinstance(base, (int, float)) else 0) + value
        return merged

    def _note_fuse_trip(self) -> None:
        self._fuse_trips += 1
        sticky_at = _env_int(
            "CLAWCODEX_DEEPSEEK_FUSE_STICKY", self.DEFAULT_FUSE_STICKY_TRIPS
        )
        if sticky_at > 0 and self._fuse_trips >= sticky_at and not self._thinking_disabled_sticky:
            self._thinking_disabled_sticky = True
            logger.warning(
                "deepseek reasoning fuse: %d consecutive pure-reasoning "
                "truncations — disabling thinking for all subsequent requests",
                self._fuse_trips,
            )

    def reset_fuse(self) -> None:
        """Forget fuse state (trips + sticky thinking-off).

        Called on conversation resets (``/clear``): a fresh conversation is
        fresh evidence, and sticky state silently overriding a later
        ``/effort`` in a context the user has reset would be a trap.
        Subagents ``copy.copy`` the provider, so they inherit the flags at
        copy time but their trips never propagate back — deliberate: a
        subagent's pathology says little about the parent's next turn.
        """
        self._fuse_trips = 0
        self._thinking_disabled_sticky = False

    def _fused_call(self, issue: Any, kwargs: dict[str, Any]) -> ChatResponse:
        """Shared cap + fuse logic for ``chat`` and ``chat_stream_response``.

        ``issue(kwargs)`` performs one request. The fuse fires at most once
        per call: a length-truncated response with nothing actionable is
        re-issued with thinking disabled. Successful (non-burn) responses
        reset the consecutive-trip counter, so the sticky escalation only
        triggers on an unbroken run of burns — three burns spread across a
        long session are three independent recoveries, not a pathology.
        """
        kwargs = self._apply_output_cap(kwargs)
        if self._thinking_disabled_sticky:
            kwargs = self._force_thinking_disabled(kwargs)
        response = self._drop_amputated_trailing_tool_call(issue(kwargs))
        if not (self._fuse_enabled and self._is_reasoning_burn(response)):
            if self._fuse_trips:
                self._fuse_trips = 0
            return response
        if self._thinking_disabled_sticky:
            # Thinking was already off — a re-issue would be byte-identical.
            # Return the truncation; the loop's max-tokens recovery owns it.
            return response
        self._note_fuse_trip()
        logger.warning(
            "deepseek reasoning fuse tripped (trip %d): request truncated at "
            "max_tokens with 100%% reasoning — retrying with thinking disabled",
            self._fuse_trips,
        )
        retry = self._drop_amputated_trailing_tool_call(
            issue(self._force_thinking_disabled(kwargs))
        )
        retry.usage = self._merge_burn_usage(response.usage or {}, retry.usage or {})
        return retry

    def chat(self, messages, tools=None, **kwargs):
        return self._fused_call(
            lambda kw: super(DeepSeekProvider, self).chat(
                messages, tools=tools, **kw
            ),
            kwargs,
        )

    def chat_stream_response(
        self,
        messages,
        tools=None,
        on_text_chunk=None,
        abort_signal=None,
        on_thinking_chunk=None,
        **kwargs,
    ):
        return self._fused_call(
            lambda kw: super(DeepSeekProvider, self).chat_stream_response(
                messages,
                tools=tools,
                on_text_chunk=on_text_chunk,
                abort_signal=abort_signal,
                on_thinking_chunk=on_thinking_chunk,
                **kw,
            ),
            kwargs,
        )

    def _create_client(self) -> Any:
        """Create OpenAI SDK client pointed at DeepSeek."""
        if OpenAI is None:  # pragma: no cover
            raise ModuleNotFoundError(
                "openai package is not installed. Install optional dependencies to use DeepSeekProvider."
            )
        kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "base_url": self.base_url or self.DEFAULT_BASE_URL,
        }
        import os
        if os.environ.get("CLAWCODEX_SSL_VERIFY", "").lower() in ("0", "false", "no"):
            import httpx
            kwargs["http_client"] = httpx.Client(verify=False)
        return OpenAI(**kwargs)

    def _build_usage_dict(self, usage: Any) -> dict[str, Any]:
        """Add DeepSeek prompt-cache accounting onto the base usage dict.

        DeepSeek reports automatic prefix-cache utilisation in its ``usage``
        object — either as top-level ``prompt_cache_hit_tokens`` /
        ``prompt_cache_miss_tokens`` (DeepSeek's native shape) or, on some
        OpenAI-compatible gateways, nested under
        ``prompt_tokens_details.cached_tokens``. The base
        :meth:`OpenAICompatibleProvider._build_usage_dict` now performs the
        nested-``cached_tokens`` split itself for every OpenAI-compatible
        provider; this override remains for DeepSeek's NATIVE top-level
        fields, which the base does not know about.

        When a cache hit is present we re-map onto the **Anthropic
        convention** that ``cost_tracker.record_api_usage`` /
        ``services.pricing.compute_cost`` already understand:

        * ``input_tokens``            → cache MISS (uncached), priced at input
        * ``cache_read_input_tokens`` → cache HIT, priced at the cheap cache rate
        * ``cache_creation_input_tokens`` → 0 (DeepSeek has no cache-write charge)

        ``total_tokens`` and ``output_tokens`` are left untouched (still the
        full counts), and ``reasoning_tokens`` is surfaced when present. With
        no cache hit, the dict is identical to the base (all prompt tokens are
        uncached input). This override runs only for the DeepSeek provider;
        every other provider keeps the base behaviour.
        """
        result = super()._build_usage_dict(usage)
        if usage is None:
            return result

        def _field(name: str) -> int:
            value = getattr(usage, name, None)
            if value is None:
                extra = getattr(usage, "model_extra", None)
                if isinstance(extra, dict):
                    value = extra.get(name)
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        hit = _field("prompt_cache_hit_tokens")
        miss = _field("prompt_cache_miss_tokens")

        # OpenAI-compatible nested shape: prompt_tokens_details.cached_tokens.
        details = getattr(usage, "prompt_tokens_details", None)
        nested_cached = (
            getattr(details, "cached_tokens", None) if details is not None else None
        )
        if not hit and nested_cached:
            try:
                hit = int(nested_cached)
            except (TypeError, ValueError):
                hit = 0

        # The FULL prompt (hit + miss). The base builder now performs the
        # generic nested-``cached_tokens`` re-map itself, so ``input_tokens``
        # may already be miss-only; adding back whatever it split off
        # recovers the original total. Without this the subtraction below
        # runs twice and drives the miss count to zero, under-reporting cost.
        prompt_tokens = int(result.get("input_tokens", 0) or 0) + int(
            result.get("cache_read_input_tokens", 0) or 0
        )
        if hit > 0:
            # Only an explicit/derivable cache hit triggers the re-map; with no
            # hit, the base dict (input_tokens = full prompt) is already correct.
            if not miss:
                miss = max(prompt_tokens - hit, 0)
            result["input_tokens"] = miss
            result["cache_read_input_tokens"] = hit
            result["cache_creation_input_tokens"] = 0

        cdetails = getattr(usage, "completion_tokens_details", None)
        reasoning = (
            getattr(cdetails, "reasoning_tokens", None) if cdetails is not None else None
        )
        if reasoning:
            try:
                result["reasoning_tokens"] = int(reasoning)
            except (TypeError, ValueError):
                pass
        return result

    def get_available_models(self) -> list[str]:
        """Return DeepSeek's current production models.

        ``deepseek-chat`` and ``deepseek-reasoner`` are kept for backward
        compatibility but DeepSeek has announced they will be deprecated.
        """
        return [
            # V4 series (current)
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            # Legacy aliases (being deprecated; map to v4-flash modes)
            "deepseek-chat",
            "deepseek-reasoner",
        ]
