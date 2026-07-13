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
    "deepseek-v4-pro": ModelConfig(
        model_id="deepseek-v4-pro",
        display_name="DeepSeek V4 Pro",
        context_window=1_000_000,
        max_output_tokens=8_192,
        supports_cache=True,
    ),
    "deepseek-v4-flash": ModelConfig(
        model_id="deepseek-v4-flash",
        display_name="DeepSeek V4 Flash",
        context_window=1_000_000,
        max_output_tokens=8_192,
        supports_cache=True,
    ),
    # Z.ai GLM Coding Plan. glm-5.2 ships a 1M context window (like DeepSeek V4);
    # glm-5.1 is 202_752 and legacy glm-4.x is 128K. Registered here so the
    # canonical window/threshold path agrees with the context display — both
    # exact keys, so glm-4 never prefix-matches glm-5.2's 1M.
    "glm-5.2": ModelConfig(
        model_id="glm-5.2",
        display_name="GLM-5.2",
        context_window=1_000_000,
        max_output_tokens=8_192,
        supports_cache=True,
    ),
    "glm-5.1": ModelConfig(
        model_id="glm-5.1",
        display_name="GLM-5.1",
        context_window=202_752,
        max_output_tokens=8_192,
        supports_cache=True,
    ),
    "glm-4": ModelConfig(
        model_id="glm-4",
        display_name="GLM-4",
        context_window=128_000,
        max_output_tokens=8_192,
        supports_cache=True,
    ),
    # MiniMax pricing is maintained in services/pricing.py.
    "MiniMax-M2.7": ModelConfig(
        model_id="MiniMax-M2.7",
        display_name="MiniMax M2.7",
        context_window=204_800,
        max_output_tokens=8_192,
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
    "gpt-5.5": ModelConfig(
        model_id="gpt-5.5",
        display_name="GPT-5.5",
        context_window=272_000,
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
    """Get config for a model, or None if unknown."""
    if model_id in MODEL_CONFIGS:
        return MODEL_CONFIGS[model_id]
    # Try prefix match (for date-variant models)
    for key, config in MODEL_CONFIGS.items():
        base = key.rsplit("-", 1)[0]
        if model_id.startswith(base):
            return config
    return None
