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

Call this from your application's session-start entry point (the
equivalent of TS's API-client init path). Today the recommended call
site is the REPL/TUI bootstrap, after settings have been loaded.
"""

from __future__ import annotations

import os

from src.state.cache_state import (
    evaluate_prompt_cache_1h_eligibility,
    get_beta_header_latches,
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


def reset_eligibility_for_tests() -> None:
    """Test-only: clear the latch so a fresh evaluation can happen."""
    latches = get_beta_header_latches()
    latches.prompt_cache_1h_eligible = None


__all__ = [
    "initialize_prompt_cache_eligibility",
    "reset_eligibility_for_tests",
]
