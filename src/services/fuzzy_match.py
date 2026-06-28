"""UI-neutral fuzzy scorer (components C5).

Lives in the service layer so consumers — ``workspace_search`` and any
headless or agent-server surface — get a pure scoring function without a UI
dependency (the dependency-direction rule from the C2/C4 reviews).
"""

from __future__ import annotations


def fuzzy_score(text: str, query: str) -> tuple[bool, int]:
    """Return ``(matched, score)``; higher score = better match.

    Matching rules:
      * Empty query matches everything with score 0.
      * Case-insensitive substring match scores ``1000 - position``.
      * Subsequence match scores ``500 - gap_penalty``.
      * Anything else returns ``(False, 0)``.
    """

    if not query:
        return True, 0
    text_lower = text.lower()
    q_lower = query.lower()
    pos = text_lower.find(q_lower)
    if pos >= 0:
        return True, 1000 - pos
    # Subsequence scan.
    ti = 0
    last_match = -1
    gap = 0
    for qc in q_lower:
        while ti < len(text_lower) and text_lower[ti] != qc:
            ti += 1
        if ti >= len(text_lower):
            return False, 0
        if last_match >= 0:
            gap += ti - last_match - 1
        last_match = ti
        ti += 1
    return True, max(0, 500 - gap)


__all__ = ["fuzzy_score"]
