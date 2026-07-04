"""PLUGINS-1 — the karpathy-guidelines bundled builtin plugin.

Registry was ported 6/6 but INERT (zero registrations/consumers); this
phase ports the one bundled plugin verbatim and inits it at startup.
"""
from __future__ import annotations

import pytest

from src.plugins.builtin_plugins import (
    clear_builtin_plugins,
    get_builtin_plugin_definition,
    get_builtin_plugins,
)
from src.plugins.init_builtin import init_builtin_plugins


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_builtin_plugins()
    yield
    clear_builtin_plugins()


def test_init_registers_karpathy_default_disabled():
    init_builtin_plugins()
    init_builtin_plugins()  # idempotent
    ps = get_builtin_plugins()
    assert [p.name for p in ps["disabled"]] == ["karpathy-guidelines"]
    assert ps["enabled"] == []


def test_prompt_verbatim_pin():
    """The prompt is model-facing eval-tuned prose, mechanically extracted
    from the TS template literal — pin its size and section headers."""
    from src.plugins.karpathy_guidelines import KARPATHY_GUIDELINES_PROMPT as P

    assert len(P) == 2351
    for header in (
        "## 1. Think Before Coding",
        "## 2. Simplicity First",
        "## 3. Surgical Changes",
        "## 4. Goal-Driven Execution",
    ):
        assert header in P


def test_focus_suffix():
    init_builtin_plugins()
    d = get_builtin_plugin_definition("karpathy-guidelines")
    skill = d.skills[0]
    bare = skill["get_prompt_for_command"]("")
    focused = skill["get_prompt_for_command"](" tighten error handling ")
    assert "## User Focus" not in bare[0]["text"]
    assert focused[0]["text"].rstrip().endswith("tighten error handling")
    assert "## User Focus" in focused[0]["text"]
