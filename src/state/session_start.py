"""Session-start wiring helpers.

Phase 3.2 of the ch03 state refactor: wires the latching
``evaluate_prompt_cache_1h_eligibility`` writer from a session-start
entry point.

This is a thin shim because the real inputs (is_ant_user, is_subscriber,
is_using_overage) come from the auth/subscription subsystem, which is its
own porting WI. Until that lands, the inputs default to False — yielding
``latch=False`` and keeping 1h caching dormant. The advantage of wiring
the call now (rather than later) is that the latch transitions from
``None`` to ``False`` at session start: subsequent calls to
``should_1h_cache_ttl`` see a settled value and don't race the writer.

When the auth subsystem lands, replace ``_read_auth_signals()`` with a
real reader (or pass the signals in as args from the entry point that
has them). The latch is one-shot, so the wiring is wrong only if the
auth signals aren't available *before* the first API call — at which
point the writer would need to be invoked earlier.

Wired (#285): ``initialize_prompt_cache_state`` runs from
``init.pre_action`` for every CLI invocation, and lazily from
``should_1h_cache_ttl`` for SDK paths that skip pre_action (or after a
/clear reset the latches).
"""

from __future__ import annotations

import os

from src.state.cache_state import (
    evaluate_prompt_cache_1h_eligibility,
    get_beta_header_latches,
    populate_prompt_cache_1h_allowlist,
)


def _read_auth_signals() -> tuple[bool, bool, bool]:
    """Read auth/subscription signals from the environment.

    Returns ``(is_ant_user, is_subscriber, is_using_overage)``.

    Today this defaults to ``(False, False, False)`` unless overridden by
    env vars — which keeps 1h caching dormant. Once an auth subsystem
    lands, this function should read from there instead. The env-var
    layer is a safe fallback for users (e.g. ant employees, testers) who
    want to opt into 1h caching manually.
    """
    is_ant_user = os.environ.get("CLAUDE_CODE_USER_TYPE", "").strip().lower() == "ant"
    is_subscriber = os.environ.get(
        "CLAUDE_CODE_IS_CLAUDE_AI_SUBSCRIBER", ""
    ).strip().lower() in {"1", "true", "yes"}
    is_using_overage = os.environ.get(
        "CLAUDE_CODE_IS_USING_OVERAGE", ""
    ).strip().lower() in {"1", "true", "yes"}
    return is_ant_user, is_subscriber, is_using_overage


def initialize_prompt_cache_eligibility(
    *,
    is_ant_user: bool | None = None,
    is_subscriber: bool | None = None,
    is_using_overage: bool | None = None,
) -> bool:
    """Run the latching ``evaluate_prompt_cache_1h_eligibility`` writer.

    Call once per session, at session start. Returns the latched
    eligibility value.

    Each argument may be passed explicitly (when the auth subsystem is
    available) or left as None (which reads from env vars). Mixed
    usage is supported: callers that know one signal can pass it and
    let the env-var defaults fill in the rest.

    Idempotent: once latched, subsequent calls return the same value
    regardless of new inputs (matches the latch's sticky-on semantics).
    """
    env_ant, env_subscriber, env_overage = _read_auth_signals()
    return evaluate_prompt_cache_1h_eligibility(
        is_ant_user=is_ant_user if is_ant_user is not None else env_ant,
        is_subscriber=is_subscriber if is_subscriber is not None else env_subscriber,
        is_using_overage=is_using_overage if is_using_overage is not None else env_overage,
    )


def _read_configured_1h_sources() -> list[str]:
    """The configured 1h-cache query sources (#285).

    Resolution order:

    1. ``CLAWCODEX_PROMPT_CACHE_1H_SOURCES`` — comma-separated query
       sources (e.g. ``repl_main_thread``). The env var wins absolutely
       when SET: ``CLAWCODEX_PROMPT_CACHE_1H_SOURCES=`` (set but empty)
       is a kill switch that disables 1h even when settings configure
       sources.
    2. ``settings.prompt_cache_1h_sources`` — a list in the settings
       schema (consulted only when the env var is unset).

    Nothing configured means 1h caching stays dormant (the TS default
    when the GrowthBook config returns nothing).
    """
    raw_env = os.environ.get("CLAWCODEX_PROMPT_CACHE_1H_SOURCES")
    if raw_env is not None:
        return [part.strip() for part in raw_env.split(",") if part.strip()]
    try:
        from src.settings.settings import get_settings

        configured = get_settings().prompt_cache_1h_sources
        if isinstance(configured, list):
            return [s for s in configured if isinstance(s, str)]
    except Exception:
        pass  # settings unavailable — dormant default
    return []


def initialize_prompt_cache_state() -> None:
    """Session-start wiring for the 1h prompt-cache path (#285).

    Latches the eligibility decision (env-signal backed until an auth
    subsystem lands) and installs the configured query-source allowlist.
    Without this call, ``prompt_cache_1h_eligible`` stays ``None`` and
    ``should_1h_cache_ttl`` always answers 5m — the pre-#285 dormant
    state. Idempotent; fail-soft (cache TTL selection must never block
    startup).
    """
    try:
        initialize_prompt_cache_eligibility()
        sources = _read_configured_1h_sources()
        if sources:
            populate_prompt_cache_1h_allowlist(sources)
    except Exception:
        import logging

        logging.getLogger(__name__).debug(
            "prompt-cache state initialization failed", exc_info=True
        )


def reset_eligibility_for_tests() -> None:
    """Test-only: clear the latch so a fresh evaluation can happen."""
    latches = get_beta_header_latches()
    latches.prompt_cache_1h_eligible = None


__all__ = [
    "initialize_prompt_cache_eligibility",
    "initialize_prompt_cache_state",
    "reset_eligibility_for_tests",
]
