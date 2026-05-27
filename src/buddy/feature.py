"""Buddy feature gate. Port of ``typescript/src/buddy/feature.ts``.

Always returns ``True``. Symbol exists so call sites are auditable
(grep-able) and a future env-var or config gate can be wired here
without touching every consumer.

See ``my-docs/get-parity-by-folder/buddy-gap-analysis.md`` §4.7 and
``buddy-refactoring-plan.md`` §2.1 for the pinned always-True decision.
"""
from __future__ import annotations


def is_buddy_enabled() -> bool:
    """Whether the companion / buddy subsystem is active in this build."""
    return True


__all__ = ['is_buddy_enabled']
