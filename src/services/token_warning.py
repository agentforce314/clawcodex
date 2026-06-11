"""Context-low warning adapter for UI surfaces (components C3a).

THIN adapter over the canonical ``autoCompact.ts`` port at
``src.services.compact.autocompact.calculate_token_warning_state`` (which
the query engine itself uses) — it resolves the model's context window +
max output tokens and converts the result to a small dataclass. The first
C3a draft forked the math with wrong thresholds; the review (F3) replaced
it with this delegation so the TUI warning and the engine's auto-compact
can never disagree about where the cliff is.

The canonical usage measure is the LAST API response's prompt-side token
count (input + cache read + cache creation), NOT cumulative session
totals — see the warning in TS utils/tokens.ts:407-420.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.services.compact.autocompact import (
    is_auto_compact_enabled,
)
from src.services.compact.autocompact import (
    calculate_token_warning_state as _canonical_state,
)


@dataclass(frozen=True)
class TokenWarningState:
    token_usage: int
    context_window: int
    percent_left: int
    is_above_warning: bool
    is_above_error: bool
    is_above_auto_compact: bool


def calculate_token_warning_state(
    token_usage: int, model_id: str
) -> TokenWarningState:
    from src.models import (
        get_context_window_for_model,
        get_model_max_output_tokens,
    )

    window = int(get_context_window_for_model(model_id) or 0)
    if window <= 0:
        return TokenWarningState(
            token_usage=token_usage,
            context_window=0,
            percent_left=100,
            is_above_warning=False,
            is_above_error=False,
            is_above_auto_compact=False,
        )
    try:
        max_out = int(get_model_max_output_tokens(model_id) or 0) or None
    except Exception:
        max_out = None
    state = _canonical_state(token_usage, window, max_out)
    return TokenWarningState(
        token_usage=token_usage,
        context_window=window,
        percent_left=int(state["percent_left"]),
        is_above_warning=bool(state["is_above_warning_threshold"]),
        is_above_error=bool(state["is_above_error_threshold"]),
        is_above_auto_compact=bool(state["is_above_auto_compact_threshold"]),
    )


def context_low_message(state: TokenWarningState) -> str:
    """TS TokenWarning.tsx label, branched like TS on auto-compact:

    * auto-compact ENABLED (default): the dim advance notice
      "{percent}% until auto-compact";
    * DISABLED: "Context low ({percent}% remaining) · Run /compact to
      compact & continue".
    """

    if is_auto_compact_enabled():
        return f"{state.percent_left}% until auto-compact"
    return (
        f"Context low ({state.percent_left}% remaining) · "
        "Run /compact to compact & continue"
    )


__all__ = [
    "TokenWarningState",
    "calculate_token_warning_state",
    "context_low_message",
]
