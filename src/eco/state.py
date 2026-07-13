"""Session state + savings stats for /eco.

Process-global session toggle, the same shape as
:mod:`src.workflow.ultracode` (``/effort ultracode``): the ``/eco`` command
and the Bash tool share it without plumbing. This also means eco applies to
subagent Bash calls (they run in the same process and burn the same context
tokens — intended), and on a multi-session transport one process has one
switch (documented ultracode precedent).

Stats are honest the RTK way: only actual compressions are recorded
(passthroughs contribute nothing, so they can't dilute the averages —
RTK tracking.rs ``track_passthrough`` records 0/0 for the same reason).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class EcoStats:
    commands: int = 0
    baseline_tokens: int = 0
    eco_tokens: int = 0
    # filter name -> (uses, tokens saved); insertion-ordered for display.
    by_filter: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def saved_tokens(self) -> int:
        return max(0, self.baseline_tokens - self.eco_tokens)

    @property
    def savings_pct(self) -> float:
        if self.baseline_tokens <= 0:
            return 0.0
        return 100.0 * self.saved_tokens / self.baseline_tokens


_lock = threading.Lock()
_session_on = False
_stats = EcoStats()


def set_eco_session(on: bool) -> None:
    """Turn Bash-output compression on/off for this session."""
    global _session_on
    with _lock:
        _session_on = bool(on)


def is_eco_session() -> bool:
    """Whether /eco compression is currently enabled."""
    with _lock:
        return _session_on


def record_compression(
    filter_name: str, baseline_tokens: int, eco_tokens: int
) -> None:
    """Record one successful compression (called by the engine only)."""
    with _lock:
        _stats.commands += 1
        _stats.baseline_tokens += max(0, int(baseline_tokens))
        _stats.eco_tokens += max(0, int(eco_tokens))
        uses, saved = _stats.by_filter.get(filter_name, (0, 0))
        _stats.by_filter[filter_name] = (
            uses + 1,
            saved + max(0, int(baseline_tokens) - int(eco_tokens)),
        )


def eco_stats() -> EcoStats:
    """A snapshot copy of the session's savings stats."""
    with _lock:
        return EcoStats(
            commands=_stats.commands,
            baseline_tokens=_stats.baseline_tokens,
            eco_tokens=_stats.eco_tokens,
            by_filter=dict(_stats.by_filter),
        )


def reset_eco() -> None:
    """Clear the toggle and stats (test/teardown helper)."""
    global _session_on, _stats
    with _lock:
        _session_on = False
        _stats = EcoStats()
