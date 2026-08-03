"""Reasoning effort must reach the wire in the PROVIDER's own vocabulary.

clawcodex exposes ``low | medium | high | xhigh | max`` — Anthropic's ladder.
DeepSeek's OpenAI-format thinking mode accepts only ``low | high | max``
(api-docs.deepseek.com/guides/thinking_mode), and it does NOT validate the
field: probed 2026-08-03 against ``deepseek-v4-flash``, every one of
low/medium/high/xhigh/max/minimal returned 200. An unsupported level is
therefore silently discarded and the provider's default (``high``) applies —
the request looks fine and the user gets a level they did not ask for.

The damaging direction is downward: ``xhigh`` means "more than high", and
dropping it delivers ``high`` when ``max`` was available — on the setting
people reach for precisely when a task is hard.

Also covers the wrapper case, because that is how the fusion models are
built: a ``FusionProvider`` must consult its BASE provider's vocabulary and
its base's wire family, not the wrapper's own (empty) defaults.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from src.providers import is_anthropic_wire, unwrap_provider
from src.providers.anthropic_provider import AnthropicProvider
from src.providers.deepseek_provider import DeepSeekProvider
from src.providers.fusion_models import FusionModel, ModelRef
from src.providers.fusion_provider import FusionProvider
from src.providers.openrouter_provider import OpenRouterProvider


def _fusion(base):
    return FusionProvider(
        base,
        FusionModel(
            name="deepseek-v4-flash-luna",
            base=ModelRef("deepseek", "deepseek-v4-flash"),
            vision=ModelRef("openai", "gpt-5.6-luna"),
        ),
    )


def _deepseek():
    return DeepSeekProvider(api_key="k", model="deepseek-v4-flash")


# --- vocabulary translation ------------------------------------------------


@pytest.mark.parametrize(
    "requested,on_the_wire",
    [
        ("low", "low"),
        ("high", "high"),
        ("max", "max"),
        # No DeepSeek equivalent. Already behaved as `high` (unknown ->
        # default), so this makes the existing behaviour explicit.
        ("medium", "high"),
        # The real fix: "above high" must not silently become "high".
        ("xhigh", "max"),
    ],
)
def test_deepseek_translates_onto_its_own_ladder(requested, on_the_wire):
    assert _deepseek().normalize_reasoning_effort(requested) == on_the_wire


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_providers_taking_the_full_ladder_are_untouched(effort):
    """The default must be identity. OpenRouter accepts all five, and
    rewriting them there would be inventing a restriction that does not
    exist — the failure this fix is meant to prevent, inverted."""
    p = OpenRouterProvider(api_key="k", model="openai/gpt-5.6-luna")
    assert p.normalize_reasoning_effort(effort) == effort


def test_a_provider_with_no_declared_vocabulary_passes_through():
    """Pre-existing behaviour for anything that has not opted in."""

    class _Bare:
        supported_reasoning_efforts = None
        reasoning_effort_aliases: dict[str, str] = {}
        normalize_reasoning_effort = (
            DeepSeekProvider.normalize_reasoning_effort  # unbound, shared impl
        )

    assert _Bare().normalize_reasoning_effort("xhigh") == "xhigh"
    assert _Bare().normalize_reasoning_effort(None) is None


# --- wrapper transparency --------------------------------------------------


def test_unwrap_reaches_the_base_provider():
    base = _deepseek()
    assert unwrap_provider(_fusion(base)) is base
    assert unwrap_provider(base) is base


def test_wire_family_follows_the_fusion_BASE():
    """`is_anthropic_wire` used an isinstance test, and FusionProvider's MRO
    is (FusionProvider, object) — so EVERY fusion model reported
    OpenAI-compatible, including one whose base is Anthropic. That sends a
    top-level `reasoning_effort` to the Anthropic API, which rejects it with
    `400 ... Extra inputs are not permitted`, and prepends the system prompt
    as a message instead of passing the `system` kwarg.
    """
    assert is_anthropic_wire(_fusion(_deepseek())) is False
    anthropic_base = AnthropicProvider(api_key="k", model="claude-opus-5")
    assert is_anthropic_wire(_fusion(anthropic_base)) is True
    assert is_anthropic_wire(anthropic_base) is True


def test_fusion_delegates_the_vocabulary_lookup_to_its_base():
    """The wire boundary looks `normalize_reasoning_effort` up on whatever
    provider it was handed, with no explicit unwrap — that is only correct
    because `FusionProvider.__getattr__` delegates to the base. Pinned here
    so the delegation is a tested property rather than an incidental one: if
    the wrapper ever grows its own attribute (or the `__getattr__` guard list
    changes), a fusion model would silently start sending the untranslated
    clawcodex level.
    """
    fused = _fusion(_deepseek())
    assert fused.normalize_reasoning_effort("xhigh") == "max"
    assert fused.supported_reasoning_efforts == ("low", "high", "max")


def test_unwrap_terminates_on_a_self_referential_wrapper():
    """A cycle must not hang the wire check."""

    class _Loop:
        pass

    a = _Loop()
    a.inner = a
    assert unwrap_provider(a) is a

    b, c = _Loop(), _Loop()
    b.inner, c.inner = c, b
    assert unwrap_provider(b) in (b, c)  # terminates; which end is arbitrary


# --- the whole chain, at the wire boundary ---------------------------------


def _wire_kwargs(provider, effort):
    """Drive the real `_call_model_sync` and capture what it would send."""
    import importlib
    import sys

    importlib.import_module("src.query.query")
    Q = sys.modules["src.query.query"]

    captured: dict = {}

    def _fake_stream(*args, **kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            content="ok", model="m", usage={}, finish_reason="stop",
            tool_uses=None, reasoning_content=None, raw_content_blocks=None,
        )

    target = unwrap_provider(provider)
    target.chat_stream_response = _fake_stream  # type: ignore[method-assign]
    try:
        asyncio.run(
            Q._call_model_sync(
                provider=provider,
                messages=[{"role": "user", "content": "hi"}],
                system_prompt="s",
                tools=[],
                thinking_effort=effort,
            )
        )
    except Exception:  # noqa: BLE001 — only the captured kwargs matter
        pass
    return captured


@pytest.mark.parametrize(
    "requested,on_the_wire",
    [("low", "low"), ("medium", "high"), ("high", "high"),
     ("xhigh", "max"), ("max", "max")],
)
def test_effort_reaches_the_deepseek_wire_translated(requested, on_the_wire):
    kwargs = _wire_kwargs(_deepseek(), requested)
    assert (kwargs.get("extra_body") or {}).get("reasoning_effort") == on_the_wire


@pytest.mark.parametrize(
    "requested,on_the_wire",
    [("medium", "high"), ("xhigh", "max"), ("max", "max")],
)
def test_a_fusion_model_uses_its_BASE_providers_vocabulary(requested, on_the_wire):
    """The fusion wrapper declares no vocabulary of its own; without
    unwrapping, every fusion model would send the raw clawcodex level."""
    kwargs = _wire_kwargs(_fusion(_deepseek()), requested)
    assert (kwargs.get("extra_body") or {}).get("reasoning_effort") == on_the_wire


def test_a_bogus_normalize_hook_cannot_corrupt_the_wire():
    """The wire boundary duck-types this hook via `getattr`, and not every
    provider-shaped object is a real BaseProvider — mocks, gateway shims and
    third-party wrappers all reach it. A MagicMock answers EVERY attribute
    with a callable returning another Mock, which without validation writes a
    `<MagicMock ...>` repr into the request body as the effort level.
    """
    from unittest.mock import MagicMock

    provider = MagicMock()
    provider.model = "some-model"
    # A bare MagicMock also answers `.inner` with a Mock, which would send
    # `unwrap_provider` walking a chain that never ends in a real object.
    # Pin it so this test exercises the normalize guard, not the unwrap loop.
    provider.inner = None
    kwargs = _wire_kwargs(provider, "xhigh")
    assert (kwargs.get("extra_body") or {}).get("reasoning_effort") == "xhigh"


def test_a_raising_normalize_hook_falls_back_instead_of_failing_the_turn():
    base = _deepseek()

    def _boom(_effort):
        raise RuntimeError("provider bug")

    base.normalize_reasoning_effort = _boom  # type: ignore[method-assign]
    kwargs = _wire_kwargs(base, "max")
    assert (kwargs.get("extra_body") or {}).get("reasoning_effort") == "max"
