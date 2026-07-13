"""Never-worse guard + token estimator (port of RTK guard.rs / tracking.rs).

``estimate_tokens`` is the ~4-chars-per-token heuristic RTK uses for its
savings accounting and guard decisions — deliberately fast and deterministic
(no tokenizer import). It exists alongside :mod:`src.token_estimation`
(tiktoken-backed) on purpose: the guard runs inline on every Bash result and
must not pay encoder costs or vary by installed extras.
"""

from __future__ import annotations

import math


def estimate_tokens(text: str) -> int:
    """Estimate tokens as ``ceil(len/4)`` (RTK tracking.rs:1284)."""
    if not text:
        return 0
    return math.ceil(len(text) / 4.0)


def never_worse(raw: str, filtered: str) -> str:
    """Return ``filtered`` unless it would emit MORE tokens than ``raw``.

    The outermost safety wrapper on every compressed emission (RTK guard.rs):
    a "compact" rendering that estimates larger than the original loses
    automatically, bounding the worst case at zero savings. Ties keep
    ``filtered``.
    """
    if estimate_tokens(filtered) > estimate_tokens(raw):
        return raw
    return filtered
