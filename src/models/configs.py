"""Per-model configuration matching TypeScript model/configs.ts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for a specific model."""
    model_id: str
    display_name: str
    context_window: int
    max_output_tokens: int
    supports_thinking: bool = True
    supports_tools: bool = True
    supports_vision: bool = True
    supports_computer_use: bool = False
    supports_cache: bool = True
    is_deprecated: bool = False
    deprecation_message: str = ""
    cost_input_per_mtok: float = 3.0
    cost_output_per_mtok: float = 15.0
    cost_cache_create_per_mtok: float = 3.75
    cost_cache_read_per_mtok: float = 0.30


MODEL_CONFIGS: dict[str, ModelConfig] = {
    # Claude 4 series
    "claude-sonnet-4-20250514": ModelConfig(
        model_id="claude-sonnet-4-20250514",
        display_name="Claude Sonnet 4",
        context_window=200_000,
        max_output_tokens=16_384,
        supports_thinking=True,
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
        cost_cache_create_per_mtok=3.75,
        cost_cache_read_per_mtok=0.30,
    ),
    "claude-opus-4-20250514": ModelConfig(
        model_id="claude-opus-4-20250514",
        display_name="Claude Opus 4",
        context_window=200_000,
        max_output_tokens=32_768,
        supports_thinking=True,
        supports_computer_use=True,
        cost_input_per_mtok=15.0,
        cost_output_per_mtok=75.0,
        cost_cache_create_per_mtok=18.75,
        cost_cache_read_per_mtok=1.50,
    ),

    # Claude Opus 4.8 / Fable 5 (current frontier — 1M context window,
    # 128K true output cap). Placed AFTER the legacy claude-opus-4-20250514
    # row on purpose: ``get_model_config``'s prefix fallback iterates in
    # insertion order and both opus keys share the ``claude-opus-4`` base,
    # so bare 4.x ids without an exact entry (opus-4-1/4-5/4-6/4-7 and
    # dated snapshots) keep resolving to the legacy 200K row — under-
    # estimating the window compacts early, the safe direction (see the
    # GPT-5 note below). Register future dated 4-8 snapshots explicitly.
    #
    # max_output_tokens is the FIRST-ATTEMPT wire ``max_tokens`` for
    # Anthropic providers (``resolve_max_output_tokens`` step 3), not the
    # model's capability ceiling: 32_000 keeps the query loop's 64K
    # truncation-escalation (``ESCALATED_MAX_TOKENS``) meaningful.
    "claude-opus-4-8": ModelConfig(
        model_id="claude-opus-4-8",
        display_name="Claude Opus 4.8",
        context_window=1_000_000,
        max_output_tokens=32_000,
        supports_thinking=True,
        supports_computer_use=True,
        cost_input_per_mtok=5.0,
        cost_output_per_mtok=25.0,
        cost_cache_create_per_mtok=6.25,
        cost_cache_read_per_mtok=0.50,
    ),
    "claude-fable-5": ModelConfig(
        model_id="claude-fable-5",
        display_name="Claude Fable 5",
        context_window=1_000_000,
        max_output_tokens=32_000,
        supports_thinking=True,
        supports_computer_use=True,
        cost_input_per_mtok=10.0,
        cost_output_per_mtok=50.0,
        cost_cache_create_per_mtok=12.50,
        cost_cache_read_per_mtok=1.00,
    ),
    # Claude Opus 5 — the current Opus tier, at Opus 4.8's price (5/25 per
    # MTok) and shape: 1M context (default AND maximum), 128K true output
    # cap, same 32_000 first-attempt wire ``max_tokens`` convention as the
    # rows above. Registered LAST among the opus rows because its prefix
    # base is the family-wide ``claude-opus`` (``key.rsplit("-", 1)[0]``).
    # Every 4.x id still matches the earlier ``claude-opus-4`` base first,
    # so their 200K/legacy resolution is unchanged, but two other strings
    # now land here: an unregistered future ``claude-opus-<n>``, AND the
    # literal ``claude-opus`` — a live MODEL_ALIASES key (aliases.py:15)
    # that resolves to claude-opus-4-20250514, so ``display_name()`` on the
    # UNRESOLVED alias now reads "Claude Opus 5". Callers that canonicalize
    # first (the normal path) are unaffected; test_model_system pins both.
    # This inverts the "under-estimate is the safe direction" note above
    # for unknown opus ids, and over-estimating is the worse failure —
    # auto-compact never fires and the request eventually exceeds the real
    # window. Accepted because every Opus since 4.6 ships 1M, and it
    # matches ``claude-fable-5``, whose base is likewise family-wide.
    # (``src/services/pricing.py`` deliberately went the other way: its
    # bare ``claude-opus-4`` prefix was dropped so unknown ids price as
    # None rather than wrong. A wrong cost is silent; a wrong window is
    # not — but the directions genuinely differ, so don't "harmonize"
    # them without re-reading both rationales.)
    "claude-opus-5": ModelConfig(
        model_id="claude-opus-5",
        display_name="Claude Opus 5",
        context_window=1_000_000,
        max_output_tokens=32_000,
        supports_thinking=True,
        supports_computer_use=True,
        cost_input_per_mtok=5.0,
        cost_output_per_mtok=25.0,
        cost_cache_create_per_mtok=6.25,
        cost_cache_read_per_mtok=0.50,
    ),

    # Claude 3.7 series
    "claude-3-7-sonnet-20250219": ModelConfig(
        model_id="claude-3-7-sonnet-20250219",
        display_name="Claude 3.7 Sonnet",
        context_window=200_000,
        max_output_tokens=16_384,
        supports_thinking=True,
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
    ),

    # Claude 3.5 series
    "claude-3-5-sonnet-20241022": ModelConfig(
        model_id="claude-3-5-sonnet-20241022",
        display_name="Claude 3.5 Sonnet (Oct 2024)",
        context_window=200_000,
        max_output_tokens=8_192,
        supports_thinking=False,
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
    ),
    "claude-3-5-sonnet-20240620": ModelConfig(
        model_id="claude-3-5-sonnet-20240620",
        display_name="Claude 3.5 Sonnet (Jun 2024)",
        context_window=200_000,
        max_output_tokens=8_192,
        supports_thinking=False,
        is_deprecated=True,
        deprecation_message="Use claude-sonnet-4-20250514 instead",
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
    ),
    "claude-3-5-haiku-20241022": ModelConfig(
        model_id="claude-3-5-haiku-20241022",
        display_name="Claude 3.5 Haiku",
        context_window=200_000,
        max_output_tokens=8_192,
        supports_thinking=False,
        cost_input_per_mtok=1.0,
        cost_output_per_mtok=5.0,
        cost_cache_create_per_mtok=1.25,
        cost_cache_read_per_mtok=0.10,
    ),

    # Claude 3 series
    "claude-3-opus-20240229": ModelConfig(
        model_id="claude-3-opus-20240229",
        display_name="Claude 3 Opus",
        context_window=200_000,
        max_output_tokens=4_096,
        supports_thinking=False,
        is_deprecated=True,
        deprecation_message="Use claude-opus-4-20250514 instead",
        cost_input_per_mtok=15.0,
        cost_output_per_mtok=75.0,
    ),
    "claude-3-sonnet-20240229": ModelConfig(
        model_id="claude-3-sonnet-20240229",
        display_name="Claude 3 Sonnet",
        context_window=200_000,
        max_output_tokens=4_096,
        supports_thinking=False,
        is_deprecated=True,
        deprecation_message="Use claude-sonnet-4-20250514 instead",
        cost_input_per_mtok=3.0,
        cost_output_per_mtok=15.0,
    ),
    "claude-3-haiku-20240307": ModelConfig(
        model_id="claude-3-haiku-20240307",
        display_name="Claude 3 Haiku",
        context_window=200_000,
        max_output_tokens=4_096,
        supports_thinking=False,
        cost_input_per_mtok=0.25,
        cost_output_per_mtok=1.25,
        cost_cache_create_per_mtok=0.30,
        cost_cache_read_per_mtok=0.03,
    ),

    # DeepSeek V4 series (OpenAI-compatible; api.deepseek.com). Registered so
    # context-window-aware logic (compaction triggers, token warnings) uses
    # DeepSeek's real ~1M window instead of the 200K default. Keys are the
    # bare model ids used ONLY by the ``deepseek`` provider; OpenRouter's
    # ``deepseek/…`` ids do not prefix-match ``deepseek-v4``, so OpenRouter is
    # intentionally unaffected. Legacy ``deepseek-chat`` / ``deepseek-reasoner``
    # are deliberately NOT registered: their prefix-match base would be the
    # broad ``deepseek`` and could capture other ids.
    #
    # NOTE: ``get_model_config``'s prefix fallback bases these on
    # ``deepseek-v4`` and ``pro`` precedes ``flash``, so a FUTURE
    # dated/suffixed variant (e.g. ``deepseek-v4-flash-0701``) would fall back
    # to ``pro``'s row — register such variants explicitly.
    #
    # Cost/pricing is intentionally NOT set here: DeepSeek's USD rates live in
    # ``services/pricing.py`` (the single source the cost path reads). The
    # ``ModelConfig.cost_*`` defaults are unread for these models — duplicating
    # the rates here only invites 10× decimal drift between the two tables.
    # ``max_output_tokens`` is DeepSeek's documented 384K ceiling, not the
    # 8_192 first registered here — that was a placeholder sitting next to a
    # 1M context window, and it under-reported the model by 47x in the
    # token warning.
    #
    # It is ADVISORY on this wire, so correcting it changes little at
    # runtime: OpenAICompatibleProvider sends no ``max_tokens`` field at all
    # (unlike AnthropicProvider's ``_default_max_tokens`` or Gemini's
    # ``config_kwargs["max_output_tokens"]``), so DeepSeek applies its own
    # server-side default either way. The one live consumer is the
    # auto-compact reservation, and it clamps to MAX_OUTPUT_TOKENS_FOR_SUMMARY
    # (20_000), so the effective input window moves 991_808 -> 980_000 — 1.2%,
    # in the safer direction.
    #
    # Recorded because the inertness is easy to mistake for a bug: this value
    # was briefly suspected of truncating long ``effort=max`` responses on
    # terminal-bench 2.1, and it cannot, because it never reaches the wire.
    #
    # ``supports_vision=False``: the DeepSeek API rejects any non-text
    # content block outright —
    #   400  unknown variant `image_url`, expected `text`
    # — so a pasted screenshot, an ``@image.png`` mention, or a ``Read`` of
    # an image kills the turn. Probed against api.deepseek.com 2026-07-30.
    # A fusion model (``/fusion``, ``providers/fusion_models.py``) is the
    # way to use images with these: it borrows vision from a second model
    # and hands the base model a text description.
    "deepseek-v4-pro": ModelConfig(
        model_id="deepseek-v4-pro",
        display_name="DeepSeek V4 Pro",
        context_window=1_000_000,
        max_output_tokens=384_000,
        supports_cache=True,
        supports_vision=False,
    ),
    "deepseek-v4-flash": ModelConfig(
        model_id="deepseek-v4-flash",
        display_name="DeepSeek V4 Flash",
        context_window=1_000_000,
        max_output_tokens=384_000,
        supports_cache=True,
        supports_vision=False,
    ),
    # Z.ai GLM Coding Plan. glm-5.2 ships a 1M context window (like DeepSeek V4);
    # glm-5.1 is 202_752 and legacy glm-4.x is 128K. Registered here so the
    # canonical window/threshold path agrees with the context display — both
    # exact keys, so glm-4 never prefix-matches glm-5.2's 1M.
    #
    # ``supports_vision=False`` on the glm-5.x text line. Probed 2026-07-30
    # against BOTH Z.ai endpoints (``api/coding/paas/v4`` and the general
    # ``api/paas/v4``); each rejects an image part with
    #   400 code 1210  messages.content.type is invalid, allowed values: ['text']
    # Z.ai's vision models are the separate ``*v`` family (glm-4.5v,
    # glm-4.6v, glm-5v-turbo — the "GLM-5V-Turbo" of claude-code-router's
    # own fusion example), which are NOT part of the GLM Coding Plan. Worth
    # stating explicitly because "GLM-5.2 is multimodal" is an easy and
    # costly assumption: it makes glm-5.2 look like a valid *vision* half
    # for a fusion model, where it would fail on every image.
    # OpenAI's open-weight models, served by several hosts in this registry
    # (cerebras as ``gpt-oss-120b``, groq/baseten as ``openai/gpt-oss-120b``).
    #
    # These rows exist to STOP a prefix match, not merely to describe a model.
    # ``get_model_config`` falls back to ``key.rsplit("-", 1)[0]``, under which
    # "gpt-oss-120b" reduces to "gpt" and collided with the gpt-5.x family —
    # so a bare gpt-oss id silently inherited gpt-5.5's 272k window, its 128k
    # output cap and its $3/$15 pricing. That window sizes auto-compaction, so
    # the session would run past the real limit and die on a context-length
    # 400 rather than compacting. The namespaced ``openai/gpt-oss-120b`` did
    # not start with "gpt" and so got the safe generic defaults instead: one
    # model behaving two ways depending on which host served it.
    #
    # 131,072 both ways per OpenAI's model docs (2026-08-02). Hosts may cap
    # output lower; the context window is the load-bearing number here.
    "gpt-oss-120b": ModelConfig(
        model_id="gpt-oss-120b",
        display_name="GPT-OSS 120B",
        context_window=131_072,
        max_output_tokens=131_072,
        supports_cache=False,
        supports_vision=False,
    ),
    "gpt-oss-20b": ModelConfig(
        model_id="gpt-oss-20b",
        display_name="GPT-OSS 20B",
        context_window=131_072,
        max_output_tokens=131_072,
        supports_cache=False,
        supports_vision=False,
    ),
    # The namespaced forms groq and baseten actually serve. Explicit rows are
    # the documented remedy for a vendor-qualified id needing a real window
    # (see ``get_model_config``'s docstring and ``openai/gpt-5.6-luna``) —
    # without them these fall to the generic 200k default, which is LARGER
    # than the true 131k, so compaction is sized past the real limit.
    "openai/gpt-oss-120b": ModelConfig(
        model_id="openai/gpt-oss-120b",
        display_name="GPT-OSS 120B",
        context_window=131_072,
        max_output_tokens=131_072,
        supports_cache=False,
        supports_vision=False,
    ),
    "openai/gpt-oss-20b": ModelConfig(
        model_id="openai/gpt-oss-20b",
        display_name="GPT-OSS 20B",
        context_window=131_072,
        max_output_tokens=131_072,
        supports_cache=False,
        supports_vision=False,
    ),
    "glm-5.2": ModelConfig(
        model_id="glm-5.2",
        display_name="GLM-5.2",
        context_window=1_000_000,
        max_output_tokens=8_192,
        supports_cache=True,
        supports_vision=False,
    ),
    "glm-5.1": ModelConfig(
        model_id="glm-5.1",
        display_name="GLM-5.1",
        context_window=202_752,
        max_output_tokens=8_192,
        supports_cache=True,
        supports_vision=False,
    ),
    "glm-4": ModelConfig(
        model_id="glm-4",
        display_name="GLM-4",
        context_window=128_000,
        max_output_tokens=8_192,
        supports_cache=True,
    ),
    # Z.ai's VISION family (the ``*v`` models). Registered explicitly, and
    # with ``supports_vision=True`` stated rather than defaulted, because
    # these are the models the fusion-model validator's error message tells
    # users to pick — so their capability must be a positive fact in the
    # table, not an assumption about an absent id. Probed 2026-07-30: all
    # three accept an image part on ``api.z.ai/api/paas/v4`` (they answer
    # 429 "insufficient balance" on a Coding-Plan-only key, which is an
    # entitlement result, not a rejection of the shape). NOT part of the
    # GLM Coding Plan.
    "glm-5v-turbo": ModelConfig(
        model_id="glm-5v-turbo",
        display_name="GLM-5V-Turbo",
        context_window=128_000,
        max_output_tokens=8_192,
        supports_vision=True,
    ),
    "glm-4.6v": ModelConfig(
        model_id="glm-4.6v",
        display_name="GLM-4.6V",
        context_window=128_000,
        max_output_tokens=8_192,
        supports_vision=True,
    ),
    "glm-4.5v": ModelConfig(
        model_id="glm-4.5v",
        display_name="GLM-4.5V",
        context_window=128_000,
        max_output_tokens=8_192,
        supports_vision=True,
    ),
    # MiniMax pricing is maintained in services/pricing.py.
    "MiniMax-M2.7": ModelConfig(
        model_id="MiniMax-M2.7",
        display_name="MiniMax M2.7",
        context_window=204_800,
        max_output_tokens=8_192,
        supports_vision=False,
        supports_cache=True,
    ),
    "MiniMax-M3": ModelConfig(
        model_id="MiniMax-M3",
        display_name="MiniMax M3",
        context_window=1_000_000,
        max_output_tokens=8_192,
        supports_cache=True,
    ),
    # Meta Muse Spark 1.1 (api.meta.ai, OpenAI-compatible). Muse Spark is a
    # server-side reasoning model (usage reports ``reasoning_tokens``); like
    # DeepSeek/GLM it exposes no Anthropic-style thinking blocks (the
    # ``thinking=`` kwarg is gated on ``is_anthropic`` in query.py), so the
    # capability flags keep their defaults. Pricing lives in
    # ``services/pricing.py`` (single source) — the cost_* fields are unset.
    # A future ``muse-spark-2.x`` would prefix-match this row via
    # ``get_model_config`` (base ``muse-spark``); register such variants
    # explicitly, as the DeepSeek/GLM rows above note.
    #
    # context_window=1_048_576 (2^20): Meta's documented window — the
    # api.meta.ai overview page states 1,048,576 tokens. Same 1M-class tier as
    # the DeepSeek-V4 / GLM-5.2 rows above.
    #
    # max_output_tokens=16_384 is NOT sent as the wire ``max_tokens``:
    # query.py forwards ``resolve_max_output_tokens()`` only for
    # Anthropic/Minimax providers; OpenAI-compatible providers send no cap and
    # rely on the server default (verified — a ~2.3K-token answer returns
    # ``finish_reason="stop"``, not truncated). The value's only live effect is
    # the auto-compact output reservation (``token_warning`` -> ``autocompact``,
    # clamped at 20_000); 16_384 reserves more output headroom than DeepSeek/
    # GLM's 8_192, which suits a model that spends part of its budget on
    # reasoning tokens.
    "muse-spark-1.1": ModelConfig(
        model_id="muse-spark-1.1",
        display_name="Muse Spark 1.1",
        context_window=1_048_576,
        max_output_tokens=16_384,
        supports_cache=True,
    ),

    # OpenAI GPT-5 family. 272K input / 128K output is the GPT-5 window
    # (400K total); it matches what the ChatGPT-subscription backend reports
    # for gpt-5.5 / gpt-5.4 / gpt-5.4-mini (Codex CLI models cache; OpenCode
    # pins the same numbers for gpt-5.5, plugin/openai/codex.ts:387). Until
    # now every gpt model silently fell back to DEFAULT_CONTEXT_WINDOW
    # (200K), making auto-compact fire ~70K tokens early on these. NOTE on
    # the prefix fallback in ``get_model_config``: a gpt id with no exact
    # entry resolves to the FIRST gpt entry below (base "gpt"), i.e. 272K —
    # the safe direction for compaction (under-estimating compacts early;
    # over-estimating overflows); the smaller legacy windows get exact
    # entries so they never take that path. As with the Meta entry above,
    # max_output_tokens is not sent on the wire for OpenAI providers — its
    # live effect is the auto-compact output reservation (clamped at 20K).
    # GPT-5.6 (Sol / Terra / Luna — three durable capability tiers on one
    # generation, 1.05M context each). These keys sit BEFORE "gpt-5.5"
    # deliberately: ``get_model_config``'s prefix fallback walks in insertion
    # order and each of these has base "gpt-5.6" (rsplit on the last "-"), so
    # putting them first is what lets an unlisted variant like
    # ``gpt-5.6-sol-pro`` resolve to 1.05M instead of falling through to the
    # 272K catch-all. The bare ``gpt-5.6`` alias is deliberately NOT here —
    # see below.
    "gpt-5.6-sol": ModelConfig(
        model_id="gpt-5.6-sol",
        display_name="GPT-5.6 Sol",
        context_window=1_048_576,
        max_output_tokens=128_000,
    ),
    "gpt-5.6-terra": ModelConfig(
        model_id="gpt-5.6-terra",
        display_name="GPT-5.6 Terra",
        context_window=1_048_576,
        max_output_tokens=128_000,
    ),
    "gpt-5.6-luna": ModelConfig(
        model_id="gpt-5.6-luna",
        display_name="GPT-5.6 Luna",
        context_window=1_048_576,
        max_output_tokens=128_000,
    ),
    # The same model as the row above, under its OpenRouter id. This table is
    # keyed by BARE name and ``get_model_config`` deliberately does not strip
    # a ``<vendor>/`` prefix (see its docstring), so without this row the id
    # that actually reaches the provider on the OpenRouter path matches
    # nothing and silently falls back to DEFAULT_CONTEXT_WINDOW (200K) — a 5x
    # under-read that makes auto-compact fire at a fifth of the real window.
    # Added for the terminal-bench harness, which drives this model as
    # ``--model openrouter/openai/gpt-5.6-luna``; Harbor splits on the FIRST
    # slash, so clawcodex receives ``--model openai/gpt-5.6-luna``.
    #
    # Only Luna is duplicated, not the whole Sol/Terra family: a qualified row
    # is the narrow, per-model answer to a general gap, and adding rows
    # nobody routes yet is speculative duplication. The general fix (teaching
    # the resolver the vendor prefix, as ``get_pricing`` already does) is
    # "decision #1" and stays out of scope — see the docstring.
    #
    # Base for the prefix fallback is "openai/gpt-5.6", which nothing else
    # claims, so ``openai/gpt-5.6-luna-pro`` resolves here too — matching how
    # the bare rows above let ``gpt-5.6-sol-pro`` through.
    "openai/gpt-5.6-luna": ModelConfig(
        model_id="openai/gpt-5.6-luna",
        display_name="GPT-5.6 Luna",
        context_window=1_048_576,
        max_output_tokens=128_000,
    ),
    "gpt-5.5": ModelConfig(
        model_id="gpt-5.5",
        display_name="GPT-5.5",
        context_window=272_000,
        max_output_tokens=128_000,
    ),
    # ``gpt-5.6`` is OpenAI's alias for Sol. Its base is "gpt" (not
    # "gpt-5.6"), so it would become the catch-all for EVERY unknown gpt id if
    # it preceded "gpt-5.5" — handing them a 1.05M window. Over-estimating
    # overflows the context; under-estimating only compacts early, so the
    # catch-all must stay on the 272K entry. Exact lookups are unaffected by
    # position: ``get_model_config`` checks for an exact key first.
    "gpt-5.6": ModelConfig(
        model_id="gpt-5.6",
        display_name="GPT-5.6 (Sol)",
        context_window=1_048_576,
        max_output_tokens=128_000,
    ),
    "gpt-5.4": ModelConfig(
        model_id="gpt-5.4",
        display_name="GPT-5.4",
        context_window=272_000,
        max_output_tokens=128_000,
    ),
    "gpt-5.4-mini": ModelConfig(
        model_id="gpt-5.4-mini",
        display_name="GPT-5.4 Mini",
        context_window=272_000,
        max_output_tokens=128_000,
    ),
    "gpt-5.3-codex-spark": ModelConfig(
        model_id="gpt-5.3-codex-spark",
        display_name="GPT-5.3 Codex Spark",
        context_window=128_000,
        max_output_tokens=64_000,
    ),
    "gpt-4o": ModelConfig(
        model_id="gpt-4o",
        display_name="GPT-4o",
        context_window=128_000,
        max_output_tokens=16_384,
    ),
    "gpt-4o-mini": ModelConfig(
        model_id="gpt-4o-mini",
        display_name="GPT-4o Mini",
        context_window=128_000,
        max_output_tokens=16_384,
    ),
    "gpt-4-turbo": ModelConfig(
        model_id="gpt-4-turbo",
        display_name="GPT-4 Turbo",
        context_window=128_000,
        max_output_tokens=4_096,
    ),
    "gpt-4": ModelConfig(
        model_id="gpt-4",
        display_name="GPT-4",
        context_window=8_192,
        max_output_tokens=8_192,
    ),
    "gpt-3.5-turbo": ModelConfig(
        model_id="gpt-3.5-turbo",
        display_name="GPT-3.5 Turbo",
        context_window=16_385,
        max_output_tokens=4_096,
    ),
}


def get_model_config(model_id: str) -> ModelConfig | None:
    """Get config for a model, or None if unknown.

    Exact match, then a prefix fallback for date-variant ids (a row's claimed
    prefix is its key minus the last ``-``-segment).

    NOT attempted: stripping a leading ``<vendor>/`` segment so OpenRouter ids
    resolve to their bare row. ``get_pricing`` (services/pricing.py) does
    exactly that, and mirroring it here is tempting — but it is deliberately
    out of scope, the same call ``tests/test_deepseek_prefix_cache.py`` pins
    as "decision #1" (``deepseek/deepseek-v4-pro`` keeps the 200K default).
    Two reasons it is not a free win: it would silently outrank a user's own
    ``modelLimits`` override, which ``get_context_window_for_model`` consults
    only when this returns ``None``; and it would resolve ids whose bare name
    prefix-matches an unrelated row, in the window-WIDENING direction, which
    overflows the request instead of merely compacting early. Reversing that
    decision is its own change with its own test sweep. A vendor-qualified
    model that needs a real window gets an explicit row instead — see
    ``openai/gpt-5.6-luna``.
    """
    if model_id in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_id]
    # Try prefix match (for date-variant models)
    for key, config in MODEL_CONFIGS.items():
        base = key.rsplit("-", 1)[0]
        if model_id.startswith(base):
            return config
    return None
