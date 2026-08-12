"""Per-subagent model resolution.

Port of TS ``getAgentModel`` (``utils/model/agent.ts``, called at
``runAgent.ts:340``): resolve the model a subagent should run on from the
tool ``model`` param, the agent definition's ``model:`` frontmatter, and
the session model, with a ``CLAUDE_CODE_SUBAGENT_MODEL`` env override.

Per-provider defaults (2026-08): each provider's row in
``src.providers.PROVIDER_INFO`` may declare

* ``subagent_tier_models`` — the resolution targets for bare tier aliases
  (anthropic haiku → ``claude-haiku-4-5``; deepseek haiku →
  ``deepseek-v4-flash``). This is the port of the TS reference's
  per-provider ``getDefault{Opus,Sonnet,Haiku}Model()`` functions.
* ``subagent_model`` — the model subagents run on when neither the Agent
  tool call nor the agent definition names one (anthropic:
  ``claude-haiku-4-5``, the cheapest current-gen tier; deepseek:
  ``deepseek-v4-flash``), overridable per provider via
  ``providers.<id>.subagent_model`` in config.json. NOTE this is a
  DELIBERATE DIVERGENCE from both references, by explicit user directive
  (cheap fan-outs): TS ``getDefaultSubagentModel()`` returns ``'inherit'``
  and opencode's task tool inherits the parent model (its per-provider
  ``getSmallModel`` serves title/name side calls — the config knob here
  borrows that ``small_model`` spelling, not its call sites). Escape
  hatches: ``model: inherit`` in an agent definition or tool call,
  ``providers.<id>.subagent_model = "inherit"`` in config, or
  ``CLAUDE_CODE_SUBAGENT_MODEL=inherit``; the coordinator path pins
  ``inherit`` at the tool layer since workers cannot pass a model param.

Before this table existed the aliases resolved through the static global
``MODEL_ALIASES`` map, whose targets had been retired from the live API —
every Explore spawn on an Anthropic session died with a 404
``not_found_error`` (claude-3-5-haiku-20241022), and DeepSeek sessions
silently ran every subagent on the expensive session model.

Multi-provider guard: providers WITHOUT a subagent table keep the reference
behavior — an alias/id the session provider doesn't recognize falls back to
the session model rather than 400-ing the request, and an unspecified model
inherits. A custom Anthropic-compatible endpoint (proxy / self-hosted) also
inherits rather than trusting the first-party table, mirroring TS
``checkIsClaudeNativeProvider``.

Deliberate asymmetry: a KNOWN alias whose canonical target is retired
degrades to inherit through the availability gate (``h35``,
``claude-3.5-haiku``), but an explicitly-pinned FULL id — even a retired
one — is trusted verbatim and will 404. An explicit full id means the user
is naming a deployment the static catalog can't know about (proxies,
Bedrock shims), and second-guessing it would break those; the alias
spellings carry no such claim.

Concurrency (ch07): Agent is now concurrency-safe, so N parallel
subagents share the session ``provider`` instance. This module only
COMPUTES the model string; the caller (``run_agent``) applies it to a
per-subagent provider CLONE and never mutates the shared provider.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_INHERIT = "inherit"
# Bare family aliases (TS agent.ts + clawcodex's ``fable`` tier). A request
# for one of these that matches the parent's TIER keeps the parent's EXACT
# model rather than downgrading to the alias's canonical (older) target.
_FAMILY_ALIASES = ("opus", "sonnet", "haiku", "fable")

# Operator env pins for the tier aliases, honored only on the ANTHROPIC
# provider. They bypass availability gating (an operator who sets one is
# naming a Bedrock/proxy deployment the static catalog can't know about),
# which is exactly why they must not leak across providers: an
# ANTHROPIC_DEFAULT_HAIKU_MODEL exported for a Bedrock setup would
# otherwise ship an Anthropic id to a DeepSeek session on every Explore
# spawn — a hard 400. Deliberate divergence from TS, which consults these
# env vars before its provider dispatch (the vars are ANTHROPIC_-named;
# honoring them on other vendors' wires trades a certain 400 for parity
# nobody wants).
_TIER_ENV_OVERRIDES = {
    "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
}


def _unwrap(provider: Any) -> Any:
    """The provider that owns the wire (FusionProvider delegates via .inner)."""
    try:
        from src.providers import unwrap_provider

        return unwrap_provider(provider)
    except Exception:  # noqa: BLE001 — identity is best-effort
        return provider


def _provider_id(session_provider: Any) -> str:
    """The unwrapped provider's canonical id ("" when unregistered)."""
    return getattr(_unwrap(session_provider), "provider_id", "") or ""


def _is_custom_anthropic(session_provider: Any) -> bool:
    """An anthropic provider pointed at a non-first-party endpoint.

    TS ``checkIsClaudeNativeProvider``: only the first-party endpoint has a
    guaranteed catalog — a proxy / self-hosted shim may serve none of the
    first-party ids, so both the subagent table and the haiku/sonnet alias
    resolution are disabled for it (subagents inherit instead). NOTE
    ``has_custom_endpoint`` is a METHOD on AnthropicProvider (not a
    property) — it must be called, or the truthy bound method would flag
    every anthropic session as custom.
    """
    if _provider_id(session_provider) != "anthropic":
        return False
    try:
        return bool(_unwrap(session_provider).has_custom_endpoint())
    except Exception:  # noqa: BLE001 — endpoint check is best-effort
        return False


def _provider_info_row(session_provider: Any) -> dict[str, Any]:
    """The session provider's PROVIDER_INFO row, or ``{}``.

    Empty when the provider carries no ``provider_id`` (unregistered) or is
    an anthropic provider on a custom endpoint — both mean "no subagent
    table", so every lookup falls through to the reference (inherit)
    behavior.
    """
    provider_id = _provider_id(session_provider)
    if not provider_id:
        return {}
    if _is_custom_anthropic(session_provider):
        return {}
    try:
        from src.providers import PROVIDER_INFO

        return dict(PROVIDER_INFO.get(provider_id) or {})
    except Exception:  # noqa: BLE001 — registry lookup is best-effort
        return {}


def _subagent_tier_models(session_provider: Any) -> dict[str, str]:
    """The provider's ``subagent_tier_models`` map (bare alias → model)."""
    tiers = _provider_info_row(session_provider).get("subagent_tier_models")
    out: dict[str, str] = {}
    if isinstance(tiers, dict):
        for tier, model in tiers.items():
            if isinstance(tier, str) and isinstance(model, str) and model.strip():
                out[tier.strip().lower()] = model.strip()
    return out


def _registry_subagent_default(session_provider: Any) -> str:
    """The provider's registry ``subagent_model`` default (or ``""``)."""
    default = _provider_info_row(session_provider).get("subagent_model")
    return default.strip() if isinstance(default, str) else ""


def _config_subagent_model(session_provider: Any) -> str:
    """A user-configured ``providers.<id>.subagent_model`` (or ``""``).

    The per-provider knob opencode spells ``small_model``: a user override
    for what unspecified-model subagents run on. The RAW configured string —
    the caller resolves it like any other user-specified model (so
    ``inherit`` and the bare tier aliases work, and a full id is trusted
    literally rather than availability-gated).

    Trust note: ``providers`` is one of config.py's
    ``_UNTRUSTED_TIER_BLOCKED_KEYS``, so a committable per-repo config
    cannot set this knob while the session is untrusted — load-bearing,
    since the value is trusted onto the wire. Don't relocate the knob to a
    flat settings key without re-establishing that guarantee.
    """
    provider_id = _provider_id(session_provider)
    if not provider_id:
        return ""
    try:
        from src.config import get_provider_config

        value = (get_provider_config(provider_id) or {}).get("subagent_model")
    except Exception:  # noqa: BLE001 — config read is best-effort
        return ""
    return value.strip() if isinstance(value, str) else ""


def _provider_serves(model: str, session_provider: Any) -> bool:
    """Whether the session provider's catalog lists ``model``."""
    try:
        available = session_provider.get_available_models() or []
    except Exception:  # noqa: BLE001 — provider can't enumerate → not servable
        return False
    return model in [str(m) for m in available]


def _resolve_against_provider(
    value: str, session_provider: Any, *,
    trust_literal: bool = False, quiet: bool = False,
) -> str:
    """Resolve an alias/id to the model the subagent should run on; inherit
    the session model on a miss. Never raises.

    - ``'inherit'``/empty → the session model.
    - A bare family alias whose tier == the parent's tier → the parent's
      EXACT model (critic M2 — TS ``aliasMatchesParentTier``; avoids the
      surprising same-tier downgrade, e.g. sonnet-4-6 → an older sonnet).
    - A bare family alias with an ``ANTHROPIC_DEFAULT_<TIER>_MODEL`` env pin
      → that pin, verbatim (TS getDefault*Model consults env first).
    - A bare family alias the session provider maps in its
      ``subagent_tier_models`` table → that model (availability-gated, so a
      stale table row degrades to inherit instead of a 404).
    - A full (non-alias) model id → trusted literally when ``trust_literal``
      (the env override / an explicit id — critic M3, TS
      ``parseUserSpecifiedModel``); otherwise gated by availability.
    - An alias mapped by the global table to a model the provider serves →
      that canonical id.
    - Anything the provider doesn't serve → the session model.
    """
    session_model = getattr(session_provider, "model", "") or ""
    normalized = (value or "").strip().lower()
    if not normalized or normalized == _INHERIT:
        return session_model

    is_bare_alias = normalized in _FAMILY_ALIASES

    # M2 — same-tier alias keeps the parent's exact model.
    if is_bare_alias and normalized in session_model.lower():
        return session_model

    if is_bare_alias:
        # Env pins apply only on the anthropic provider (incl. custom
        # endpoints — pinning a proxy/Bedrock deployment name is their
        # whole use case); see _TIER_ENV_OVERRIDES.
        if _provider_id(session_provider) == "anthropic":
            env_var = _TIER_ENV_OVERRIDES.get(normalized, "")
            env_pin = os.environ.get(env_var, "") if env_var else ""
            if env_pin.strip():
                return env_pin.strip()
        # TS agent.ts:105-115 — haiku/sonnet on a non-Claude-native endpoint
        # inherit the parent model outright (the global alias targets are
        # first-party ids a proxy has no obligation to serve). 'opus' (and
        # 'fable') deliberately fall through, matching the TS asymmetry.
        # Deliberate divergence from TS: the guard applies to the tool-param
        # path too (TS returns from its toolSpecifiedModel branch before the
        # guard) — on a proxy an explicit tool 'haiku' inherits here, which
        # is the safe direction. Reached also by an alias-valued
        # providers.<id>.subagent_model knob on a proxy session, hence the
        # trace: naming a full id is the working spelling there.
        if normalized in ("haiku", "sonnet") and _is_custom_anthropic(
            session_provider
        ):
            logger.debug(
                "alias %r on a custom Anthropic endpoint inherits the "
                "session model %r (first-party tier ids are not assumed "
                "served there; pin a full model id to override)",
                value, session_model,
            )
            return session_model
        tier_model = _subagent_tier_models(session_provider).get(normalized, "")
        if tier_model:
            if tier_model == session_model or _provider_serves(
                tier_model, session_provider
            ):
                return tier_model
            logger.log(
                logging.DEBUG if quiet else logging.WARNING,
                "provider tier model %r for alias %r is not in the session "
                "provider's catalog; inheriting the session model %r",
                tier_model, value, session_model,
            )
            return session_model
        # No tier table for this provider — fall through to the global
        # alias path below, which availability-gates and inherits on a
        # miss (e.g. 'haiku' on a provider with no haiku-class model).

    cleaned = (value or "").strip()
    try:
        from src.models.model import canonical_model_name

        canonical = canonical_model_name(cleaned)
    except Exception:  # noqa: BLE001 — resolution failure → inherit
        return session_model

    # M3 — an id NO alias table knows (canonical == the input) from the env
    # override or an explicit pin is trusted literally, so it survives
    # static-list staleness / proxy deployments with custom names. A known
    # alias spelling (canonical != input: 'claude-haiku', 'claude-4-sonnet',
    # 'h35', …) is never trusted raw — the alias string itself is not a
    # servable model id, so it resolves to its canonical target and takes
    # the availability gate below like any other alias.
    if trust_literal and canonical == cleaned:
        return cleaned

    if _provider_serves(canonical, session_provider):
        return canonical
    # Not served by this provider (e.g. 'haiku' on a provider with no
    # tier table and no such model). Inherit rather than 400. Elevated to
    # WARNING (M3) so an ignored explicit pin is observable, not silently
    # dropped at debug — except under ``quiet`` (the Agent tool's
    # reporting-only re-resolution), which would double every warning.
    logger.log(
        logging.DEBUG if quiet else logging.WARNING,
        "agent model %r (→ %r) is not available on the session provider; "
        "inheriting the session model %r",
        value, canonical, session_model,
    )
    return session_model


def get_default_subagent_model(
    session_provider: Any, *, quiet: bool = False,
) -> str:
    """The model an unspecified-model subagent runs on.

    Precedence: the user's ``providers.<id>.subagent_model`` config knob
    (resolved like any user-specified model, so ``inherit``, bare tier
    aliases, and full ids all behave — a full id is trusted literally) >
    the provider's ``subagent_model`` registry default (availability-gated)
    > the session model (inherit — the TS ``getDefaultSubagentModel()``
    behavior, and the behavior of every provider that designates no
    default).
    """
    session_model = getattr(session_provider, "model", "") or ""

    configured = _config_subagent_model(session_provider)
    if configured:
        # Through the same resolver as an explicit pin: 'inherit' → session
        # model, 'sonnet'/'haiku'/… → the provider tier table, a full id →
        # trusted verbatim. Returning the raw string here shipped alias
        # spellings (and the literal 'inherit') onto the wire as model ids.
        return _resolve_against_provider(
            configured, session_provider, trust_literal=True, quiet=quiet,
        )

    default = _registry_subagent_default(session_provider)
    if default and default != session_model:
        if _provider_serves(default, session_provider):
            return default
        logger.log(
            logging.DEBUG if quiet else logging.WARNING,
            "provider default subagent model %r is not in the session "
            "provider's catalog; inheriting the session model %r",
            default, session_model,
        )
    return session_model


def get_agent_model(
    tool_model: str | None,
    agent_def_model: str | None,
    session_provider: Any,
    *,
    quiet: bool = False,
) -> str:
    """Resolve the subagent's model. Precedence (TS getAgentModel):
    ``CLAUDE_CODE_SUBAGENT_MODEL`` env > tool ``model`` param > agent-def
    ``model:`` > the provider's default subagent model (falling back to
    ``'inherit'`` = the session model). Always returns a non-empty model
    when the session provider has one; never raises.

    ``quiet`` demotes the fallback warnings to debug — for callers that
    re-resolve purely for REPORTING (the Agent tool surfaces the routed
    model in its result), so each spawn warns once, from the wire-authority
    resolution in run_agent."""
    env_override = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL")
    if env_override:
        # M3 — the env override is honored more literally (a full id it
        # names is trusted; TS agent.ts:43-45 bypasses provider gating).
        return _resolve_against_provider(
            env_override, session_provider, trust_literal=True, quiet=quiet,
        )

    # Trim per layer (TS trims toolSpecifiedModel before testing it), so a
    # whitespace-only tool param falls through to the agent-def model
    # rather than swallowing it.
    chosen = (tool_model or "").strip() or (agent_def_model or "").strip()
    if not chosen:
        return get_default_subagent_model(session_provider, quiet=quiet)
    # A tool param / frontmatter that names a full model id is trusted
    # literally; bare aliases still go through availability/tier logic.
    return _resolve_against_provider(
        chosen, session_provider, trust_literal=True, quiet=quiet,
    )
