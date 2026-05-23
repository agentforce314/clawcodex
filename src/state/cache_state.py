"""Sticky-on header/parameter latches for prompt-cache stability.

Mirrors TS ``bootstrap/state.ts:225,229,233,237,242`` (the five latch field
declarations) and ``bootstrap/state.ts:1738-1787`` (their getters/setters/
reset). Five toggle latches plus a 1h-eligibility evaluation primitive plus
a query-source allowlist control whether each request emits
``cache_control: {ttl: '5m' | '1h'}``.

Why latches at all (per chapter §"Sticky Latch Fields"): each of these five
fields, if flipped mid-session, would bust ~50-70K tokens of cached prompt.
Sacrificing mid-session toggleability buys cache stability worth far more in
dollars per turn.

Per the Phase 2 audit (M9-resolved):
  * ``prompt_cache_1h_eligible`` — wired here; consumed by WI-2.2's
    ``should_1h_cache_ttl`` selector.
  * ``fast_mode_header_latched`` — wired by ``src/utils/fast_mode.py`` on
    first true result of ``is_fast_mode_enabled()``.
  * ``afk_mode_header_latched`` — DEAD STORE today (no AFK toggle in
    Python TUI yet). Future TUI WI must add the trigger.
  * ``cache_editing_header_latched`` — DEAD STORE today (cache-editing is
    a TS GrowthBook treatment with no Python equivalent yet).
  * ``thinking_clear_latched`` — DEAD STORE today (thinking-mode-flip event
    is not exposed by ``src/utils/effort.py`` in the form needed for this
    latch). Future WI must surface the event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.providers.base import BaseProvider

__all__ = [
    "BetaHeaderLatches",
    "clear_beta_header_latches",
    "evaluate_prompt_cache_1h_eligibility",
    "get_beta_header_latches",
    "get_prompt_cache_1h_allowlist",
    "get_prompt_cache_1h_eligible",
    "is_first_party_provider",
    "reset_for_test_only",
    "should_1h_cache_ttl",
    "should_use_global_cache_scope",
]


@dataclass
class BetaHeaderLatches:
    """Six sticky-on fields. Once latched, never reset within a session.

    Mirrors TS ``bootstrap/state.ts:225,229,233,237,242`` (five fields)
    plus ``:221`` (``promptCache1hAllowlist`` — list of query sources
    eligible for 1h caching).

    The ``prompt_cache_1h_eligible`` field uses ``bool | None`` to encode
    "not yet evaluated" (None) vs "decision made" (True/False). First read
    triggers ``evaluate_prompt_cache_1h_eligibility``; subsequent reads
    return the latched truth value. Mirrors TS pattern at
    ``services/api/claude.ts:420-425``::

        let userEligible = getPromptCache1hEligible()
        if (userEligible === null) {
            userEligible = process.env.USER_TYPE === 'ant' ||
                (isClaudeAISubscriber() && !currentLimits.isUsingOverage)
            setPromptCache1hEligible(userEligible)
        }
    """

    # 1h cache TTL: set ONCE on first read if currently None; never re-evaluated.
    # Truth: ``is_ant_user OR (is_subscriber AND NOT using_overage)``.
    # The latch is FALSE for users on overage; TRUE for ant + non-overage subscribers.
    prompt_cache_1h_eligible: bool | None = None

    # Allowlist of query sources eligible for 1h caching. Even when
    # ``prompt_cache_1h_eligible`` is True, the per-call decision still
    # requires the ``query_source`` to appear in this list — mirrors TS
    # GrowthBook config at ``services/api/claude.ts:430-438``.
    # Default empty list = no source emits 1h. Population of this list is
    # left to a future WI that ports the GrowthBook integration; for now
    # the allowlist remains empty and 1h caching is dormant (5m caching
    # still works because Phase 1 already engaged it).
    prompt_cache_1h_allowlist: list[str] = field(default_factory=list)

    # Toggle latches. Set on first toggle event; never reset.
    # WIRING TODO (audit per M9): only ``fast_mode_header_latched`` has a
    # real Python integration today (wired in ``src/utils/fast_mode.py``).
    # The other three are dead-stores until their respective WIs land.
    fast_mode_header_latched: bool = False
    # WIRING TODO: set True in src/tui/<afk-toggle-component>.py when the
    # AFK toggle is implemented. Search for "afk_mode_header_latched" to
    # find this comment.
    afk_mode_header_latched: bool = False
    # WIRING TODO: set True when cache-editing config is first read with
    # a true value. Today there is no Python equivalent of TS's GrowthBook
    # cache-editing treatment.
    cache_editing_header_latched: bool = False
    # WIRING TODO: set True when thinking mode flips after a confirmed
    # cache miss. Today src/utils/effort.py does not expose this event in
    # the shape this latch needs.
    thinking_clear_latched: bool = False


# Module-level singleton. Accessed via ``get_beta_header_latches()``;
# tests call ``reset_for_test_only()`` to wipe state between cases.
_LATCHES = BetaHeaderLatches()


def get_beta_header_latches() -> BetaHeaderLatches:
    """Return the session-level singleton instance."""
    return _LATCHES


def get_prompt_cache_1h_eligible() -> bool | None:
    """Read the latched 1h-cache eligibility.

    Returns:
      * ``True`` / ``False`` after ``evaluate_prompt_cache_1h_eligibility``
        has latched a decision
      * ``None`` if the latch has not yet been evaluated

    Plain getter for parity with TS ``getPromptCache1hEligible``
    (``bootstrap/state.ts:1587``). **No setter is exposed** — writes go
    through ``evaluate_prompt_cache_1h_eligibility``, which preserves the
    sticky-on invariant. See
    ``my-docs/get-parity-by-folder/bootstrap-gap-analysis.md §1.4``.
    """
    return _LATCHES.prompt_cache_1h_eligible


def get_prompt_cache_1h_allowlist() -> list[str]:
    """Read the 1h-cache query-source allowlist.

    Returns a copy to discourage caller mutation. The allowlist is
    populated by future GrowthBook-port work (currently always empty in
    the open build). Plain getter for parity with TS
    ``getPromptCache1hAllowlist`` (``bootstrap/state.ts:1579``). **No
    setter is exposed**.
    """
    return list(_LATCHES.prompt_cache_1h_allowlist)


def evaluate_prompt_cache_1h_eligibility(
    *,
    is_ant_user: bool,
    is_subscriber: bool,
    is_using_overage: bool,
) -> bool:
    """Compute eligibility on first call; subsequent calls return the latched value.

    Mirrors TS ``services/api/claude.ts:420-425``. The ``None`` sentinel on
    ``prompt_cache_1h_eligible`` distinguishes "not yet evaluated" from
    "evaluated to False" — only the former triggers a fresh evaluation.

    Inputs:
      - ``is_ant_user``: TS ``process.env.USER_TYPE === 'ant'``. Python
        equivalent does not exist yet; callers default to ``False`` until
        a porting WI lands. (Per critic R3.)
      - ``is_subscriber``: TS ``isClaudeAISubscriber()``. Same.
      - ``is_using_overage``: TS ``currentLimits.isUsingOverage``. Same.

    Default-False inputs yield ``latch=False`` — the safe default that
    keeps 1h caching dormant. When the porting WI for these inputs lands,
    1h caching activates without requiring code changes here.

    **Status (Phase 2):** this primitive is implemented but has NO
    production caller today. ``grep -rn "evaluate_prompt_cache_1h_eligibility"
    src/`` returns only the definition. The 1h cache path is therefore
    end-to-end dormant: ``prompt_cache_1h_eligible`` stays at ``None``,
    ``should_1h_cache_ttl`` always returns False, every cache_control
    emits ``ttl: '5m'``. Activating 1h requires (a) porting the user-type
    /subscription/overage signals, (b) calling this function with real
    inputs at session start, AND (c) populating
    ``prompt_cache_1h_allowlist`` from a Python-equivalent of the TS
    GrowthBook config. All three are deferred to a future WI.
    """
    latches = get_beta_header_latches()
    if latches.prompt_cache_1h_eligible is None:
        latches.prompt_cache_1h_eligible = is_ant_user or (
            is_subscriber and not is_using_overage
        )
    return latches.prompt_cache_1h_eligible


def should_1h_cache_ttl(query_source: str) -> bool:
    """Decide whether to emit ``ttl: '1h'`` for a given query source.

    Returns True iff BOTH:
      1. ``prompt_cache_1h_eligible`` is latched True (the user is eligible).
      2. ``query_source`` is in the allowlist (this specific call is eligible).

    The allowlist is empty by default — until a future WI populates it
    from configuration, every call defaults to ``ttl: '5m'``. This is the
    safe-default behavior: 5m caching is already engaged from Phase 1; 1h
    is an opt-in extension for sessions that cross 5-minute idle gaps.
    """
    latches = get_beta_header_latches()
    if latches.prompt_cache_1h_eligible is not True:
        return False
    return query_source in latches.prompt_cache_1h_allowlist


def should_use_global_cache_scope(
    *,
    provider: BaseProvider,
    has_mcp_tools: bool,
) -> bool:
    """Decide whether GLOBAL-tier sections may emit ``scope: 'global'``.

    WI-2.3 — mirrors TS ``betas.ts:227 shouldUseGlobalCacheScope()`` plus
    the chapter's MCP-aware policy at ch17 §"The Prompt Cache Architecture"
    line 91 (*"global scope is disabled when MCP tools are present, since
    MCP schemas are per-user"*).

    Returns True iff ALL:
      * Provider is first-party Anthropic (``is_first_party_provider``).
      * No MCP tools are loaded (``has_mcp_tools=False``).
      * The opt-in env var ``CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE`` is set
        to a truthy value. Defaults to OFF.

    **Why env-gated**: the spike test confirmed the Python Anthropic SDK
    passes through the ``scope`` field on cache_control verbatim (TypedDict
    permissive-extras + pydantic), but **the API-side acceptance of
    ``scope: 'global'`` from a non-Anthropic-internal client is unverified**.
    Production rollout should validate the API response on staging first.
    Once verified, flip this default to True (or remove the gate).

    The conservative default (False, requiring explicit opt-in) keeps
    Phase 1's 5m/1h caching working without risking a 400 from the
    server on every cold-start request.
    """
    import os
    enabled = os.environ.get(
        "CLAUDE_CODE_ENABLE_GLOBAL_CACHE_SCOPE", ""
    ).strip().lower() in {"1", "true", "yes"}
    if not enabled:
        return False
    if has_mcp_tools:
        return False
    return is_first_party_provider(provider)


def is_first_party_provider(provider: BaseProvider) -> bool:
    """Mirror TS ``getAPIProvider() === 'firstParty'``.

    True only when using AnthropicProvider with no custom base_url override
    (i.e., the user is hitting Anthropic's first-party endpoint, not a
    third-party proxy / self-hosted variant).

    Used by WI-2.3's global-scope decision: even when a section is GLOBAL-
    tier, ``scope: 'global'`` should only be emitted on first-party
    requests (the cross-user prefix sharing only makes sense there).
    """
    # Local import to avoid a circular dependency: providers package may
    # import from state in the future, and we don't want to lock that.
    from src.providers.anthropic_provider import AnthropicProvider
    if not isinstance(provider, AnthropicProvider):
        return False
    # Public ``has_custom_endpoint`` method on AnthropicProvider so we
    # don't read the ``_client_kwargs`` private attribute (per Phase 2
    # critic m2). A non-empty base_url indicates a custom endpoint
    # (proxy, self-hosted, Bedrock shim, etc.) — first-party = no override.
    return not provider.has_custom_endpoint()


def reset_for_test_only() -> None:
    """Wipe the latch singleton. Test-only escape hatch.

    Production code never calls this — latches are sticky-on by design.
    Tests need to reset between cases to avoid cross-test pollution.
    """
    global _LATCHES
    _LATCHES = BetaHeaderLatches()


def clear_beta_header_latches() -> None:
    """Reset the sticky beta-header latches.

    Mirrors TS ``clearBetaHeaderLatches()`` from
    ``bootstrap/state.ts``. Called on ``/clear`` and ``/compact`` so a fresh
    conversation re-evaluates AFK / fast-mode / cache-editing / thinking-
    clear / 1h-eligibility from scratch (per chapter §"Sticky Latch
    Fields").

    The 1h-eligibility latch is included because the conversation rewind
    invalidates the request-source history that informed the original
    evaluation.
    """
    global _LATCHES
    _LATCHES = BetaHeaderLatches()
