"""Bundled-skill catalogue + init orchestrator.

Mirrors the TS pattern in ``typescript/src/skills/bundled/index.ts``:
each individual skill module exposes a ``register_*_skill()`` function
that calls ``register_bundled_skill(BundledSkillDefinition(...))``;
``init_bundled_skills()`` calls them in order at startup.

``init_bundled_skills`` is idempotent — calling it twice does not
re-register skills (the second call is a no-op). The bundled-skill
registry consults a sentinel set so a fresh ``clear_bundled_skills()``
forces re-init on the next call.
"""

from __future__ import annotations

import logging

from .debug import register_debug_skill
from .loop import register_loop_skill
from .simplify import register_simplify_skill
from .stuck import register_stuck_skill
from .verify_content import register_verify_content_skill

logger = logging.getLogger(__name__)


# Tracks whether ``init_bundled_skills`` has already populated the
# registry. Reset by ``clear_bundled_skills`` (via the hook below) so
# tests that wipe state can re-init cleanly.
_INITIALIZED: bool = False


def init_bundled_skills() -> None:
    """Register every always-on bundled skill exactly once.

    Calls each ``register_*_skill()`` function in a fixed order. Skills
    with feature gates check ``is_enabled`` lazily at lookup time
    (matches TS) — they're registered unconditionally so they show up
    in the catalogue when the gate flips.

    Idempotent: subsequent calls are no-ops. Use the
    ``clear_bundled_skills()`` hook in ``src.skills.bundled_skills`` to
    reset state in tests.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return
    register_simplify_skill()
    register_debug_skill()
    register_loop_skill()
    register_stuck_skill()
    register_verify_content_skill()
    _INITIALIZED = True
    logger.debug("bundled skills initialized")


def reset_bundled_skills_init_flag() -> None:
    """Drop the idempotency flag so the next ``init_bundled_skills``
    call re-runs. Wired to ``clear_bundled_skills`` so test fixtures
    that reset the registry also reset the flag."""
    global _INITIALIZED
    _INITIALIZED = False


__all__ = [
    "init_bundled_skills",
    "reset_bundled_skills_init_flag",
    "register_simplify_skill",
    "register_debug_skill",
    "register_loop_skill",
    "register_stuck_skill",
    "register_verify_content_skill",
]
