"""Regression tests for ``--allowedTools`` / ``--disallowedTools`` filtering.

Both the headless (``--print``) and agent-server paths expose a
``_filter_registry`` helper that backs the ``--allowedTools`` /
``--disallowedTools`` CLI flags. It historically called a non-existent
``registry.unregister`` inside a bare ``try/except``, so the filtering was a
silent no-op: the flags removed nothing from the pool the model saw (tool
schemas are emitted from ``registry.list_tools()``). These tests lock in that
the helper now calls the real ``ToolRegistry.remove_tool`` so the flags take
effect.
"""

from __future__ import annotations

from src.entrypoints import headless as headless_mod
from src.server import agent_server as server_mod
from src.tool_system.defaults import build_default_registry


def _names(registry) -> set[str]:
    return {t.name for t in registry.list_tools()}


def test_registry_lacks_unregister_but_has_remove_tool():
    """The bug was calling ``unregister`` (absent) instead of ``remove_tool``.

    Guards against a future rename silently reintroducing the dead path.
    """
    registry = build_default_registry(provider="anthropic")
    assert not hasattr(registry, "unregister")
    assert hasattr(registry, "remove_tool")


def test_headless_filter_denylist_removes_from_pool():
    registry = build_default_registry(provider="anthropic")
    assert "AskUserQuestion" in _names(registry)

    deny = {"askuserquestion"}
    headless_mod._filter_registry(
        registry, keep=lambda n: n.lower() not in deny
    )

    remaining = _names(registry)
    assert "AskUserQuestion" not in remaining, (
        "disallowed tool must be dropped from list_tools() (the schema source), "
        "not left in the pool the model sees"
    )
    # Untargeted tools survive.
    assert "Bash" in remaining
    assert "Read" in remaining


def test_headless_filter_allowlist_keeps_only_allowed():
    registry = build_default_registry(provider="anthropic")
    keep_set = {"bash", "read", "write", "edit"}
    headless_mod._filter_registry(registry, keep=lambda n: n.lower() in keep_set)

    remaining = {n.lower() for n in _names(registry)}
    assert remaining == keep_set, remaining


def test_agent_server_filter_matches_headless_behavior():
    """The agent-server (TUI/interactive) path shares the same contract."""
    registry = build_default_registry(provider="anthropic")
    deny = {"workflow", "croncreate"}
    server_mod._filter_registry(registry, keep=lambda n: n.lower() not in deny)

    remaining = {n.lower() for n in _names(registry)}
    assert "workflow" not in remaining
    assert "croncreate" not in remaining
    assert "bash" in remaining


def test_filter_is_idempotent_and_survives_unknown_names():
    registry = build_default_registry(provider="anthropic")
    before = _names(registry)
    # Denying a name that isn't registered must not raise or change anything.
    headless_mod._filter_registry(registry, keep=lambda n: n.lower() != "not_a_tool")
    assert _names(registry) == before
