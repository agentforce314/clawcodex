"""Tests for the desktop model catalog + slash-command dispatch (QA round 1)."""

from __future__ import annotations

import pytest

from src.server.desktop_commands import build_catalog, complete
from src.server.desktop_slash import dispatch_slash


# ─── command catalog ─────────────────────────────────────────────────────────


def test_catalog_has_builtin_commands() -> None:
    catalog = build_catalog()
    names = {name for name, _ in catalog["pairs"]}
    assert "/help" in names
    assert "/model" in names
    assert "/context" in names
    assert "/cost" in names
    assert catalog["hints"]["/effort"].startswith("[low")


def test_catalog_merges_skills_and_workflows() -> None:
    catalog = build_catalog(
        skills=[{"name": "my-skill", "description": "does a thing", "provenance": "agent", "usage": 5}],
        workflows=[{"name": "deep-research", "description": "research", "argument_hint": "<q>"}],
    )
    names = {name for name, _ in catalog["pairs"]}
    assert "/my-skill" in names
    assert "/deep-research" in names
    assert catalog["hints"]["/deep-research"] == "<q>"
    assert catalog["skills"]["/my-skill"] == {"origin": "local", "usage": 5}
    assert catalog["skill_count"] == 1


def test_complete_prefix_filters() -> None:
    catalog = build_catalog()
    res = complete("/co", catalog)
    names = {item["text"] for item in res["items"]}
    assert "/context" in names
    assert "/cost" in names
    assert "/compact" in names
    assert "/model" not in names
    assert res["replace_from"] == 1


# ─── slash dispatch ──────────────────────────────────────────────────────────


def _control_stub(replies: dict[str, object]):
    calls: list[tuple[str, dict]] = []

    async def control(subtype: str, params: dict) -> object:
        calls.append((subtype, params))
        return replies.get(subtype)

    control.calls = calls  # type: ignore[attr-defined]
    return control


@pytest.mark.asyncio
async def test_dispatch_context() -> None:
    control = _control_stub({"get_context_usage": {"total_tokens": 100, "max_tokens": 1000, "percentage": 10}})
    res = await dispatch_slash(control, "/context", None)
    assert res == {"output": "Context: 100/1000 tokens (10%).", "type": "exec"}


@pytest.mark.asyncio
async def test_dispatch_cost() -> None:
    control = _control_stub({"cost": {"total_cost_usd": 1.2345, "num_turns": 3}})
    res = await dispatch_slash(control, "cost", None)
    assert res["output"] == "Total cost: $1.2345 · 3 turns."


@pytest.mark.asyncio
async def test_dispatch_version_needs_no_control() -> None:
    control = _control_stub({})
    res = await dispatch_slash(control, "/version", None)
    assert res["output"].startswith("ClawCodex ")
    assert control.calls == []  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dispatch_effort_passes_arg() -> None:
    control = _control_stub({"set_effort": {"effort": "high"}})
    res = await dispatch_slash(control, "effort", "high")
    assert res["output"] == "Reasoning effort: high."
    assert control.calls == [("set_effort", {"effort": "high"})]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dispatch_unknown_falls_back_to_skill() -> None:
    control = _control_stub({"skill_command": {"ok": True, "prompt": "expanded skill body"}})
    res = await dispatch_slash(control, "/my-skill", "arg1 arg2")
    assert res == {"message": "expanded skill body", "name": "my-skill", "type": "skill"}
    assert control.calls == [("skill_command", {"name": "my-skill", "args": "arg1 arg2"})]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dispatch_unknown_without_skill_is_graceful() -> None:
    control = _control_stub({"skill_command": {"ok": False}})
    res = await dispatch_slash(control, "/nope", None)
    assert res == {"output": "/nope: not available", "type": "exec"}


@pytest.mark.asyncio
async def test_dispatch_never_raises() -> None:
    async def boom(subtype: str, params: dict) -> object:
        raise RuntimeError("backend exploded")

    res = await dispatch_slash(boom, "/context", None)
    assert res["type"] == "exec"
    assert "backend exploded" in res["output"]


# ─── model catalog: configured providers only ────────────────────────────────


def test_configured_providers_only_filters_the_registry() -> None:
    from src.server.desktop_gateway_methods import configured_providers_only

    catalog = [
        {"slug": "deepseek", "authenticated": True, "auth_type": "api_key"},
        {"slug": "openai", "authenticated": True, "auth_type": "api_key"},
        # Configured key-provider that the user hasn't set up → dropped.
        {"slug": "groq", "authenticated": False, "auth_type": "api_key"},
        # Local no-key server → dropped (not user-configured) unless active.
        {"slug": "ollama", "authenticated": True, "auth_type": "none"},
        # The running provider is always kept, even if the probe missed its key.
        {"slug": "vllm", "authenticated": False, "auth_type": "none", "is_current": True},
    ]
    kept = {p["slug"] for p in configured_providers_only(catalog)}
    assert kept == {"deepseek", "openai", "vllm"}


def test_catalog_from_config_shows_only_configured() -> None:
    from src.server.desktop_gateway_methods import _catalog_from_config

    result = _catalog_from_config()
    # Every returned provider is either configured (key + authenticated) or the
    # current one — never the unconfigured long tail.
    for p in result["providers"]:
        assert p.get("is_current") or (
            p.get("authenticated") and p.get("auth_type") == "api_key"
        ), p["slug"]
    # The active provider shows its FULL model list, not just default_model.
    current = next((p for p in result["providers"] if p.get("is_current")), None)
    if current is not None:
        assert len(current["models"]) >= 1

