"""Which reasoning-effort levels a given (provider, model) pair actually accepts.

The ``/model`` picker's third step offers these, so the list has to be the
levels the model will really take rather than the union ladder ``/effort``
validates against. The knowledge already exists, scattered across the two
paths that send effort on the wire; this module is the one place that reads
them together and answers the question the picker asks.

Two directions of error, and they are not symmetric — the same asymmetry
``_model_supports_xhigh_effort`` documents:

* offering a level the model REJECTS is fatal, because a 400 on the effort
  level is not retried or downgraded anywhere, so every subsequent request
  fails;
* omitting a level the model would have accepted merely hides a choice.

So this errs toward the narrower list wherever the codebase actually knows,
and falls back to the full ladder only where it does not.
"""

from __future__ import annotations

from src.settings.constants import VALID_EFFORT_VALUES

#: The clawcodex ladder, minus the empty "auto" sentinel. Anything offered
#: has to survive ``_do_set_effort``'s validation, which keys off this.
LADDER: tuple[str, ...] = tuple(v for v in VALID_EFFORT_VALUES if v)

#: Offered first on every model that supports effort at all: clears the
#: session override and lets the provider pick (``/effort auto``).
AUTO = "auto"


def effort_options(provider_name: str | None, model: str | None) -> dict[str, object]:
    """The effort levels ``model`` accepts under ``provider_name``.

    Returns ``{"supported": bool, "levels": [...]}``. ``supported`` False
    means the model takes no effort parameter at all — the picker skips its
    third step rather than offering a list that cannot be applied. ``levels``
    never includes ``auto``; the caller prepends it, since "let the provider
    decide" is meaningful exactly when some real level is also on offer.
    """
    canonical = _canonical(provider_name)

    if canonical == "anthropic":
        return _anthropic_options(model)

    if canonical == "openai":
        return _openai_options(model)

    # Every other provider: the codebase carries no per-model effort table,
    # and the OpenAI-compatible paths pass the value through as a body field
    # that unsupported models IGNORE rather than reject (kimi-k3 was probed
    # doing exactly that — silently dropped, not a 400). A quietly-ignored
    # level costs nothing; withholding the whole ladder would remove a
    # working control from every non-first-party provider.
    return {"supported": True, "levels": list(LADDER)}


def _canonical(provider_name: str | None) -> str:
    if not provider_name:
        return ""

    try:
        from src.providers import canonical_provider_name

        return canonical_provider_name(provider_name)
    except Exception:  # noqa: BLE001 — an unknown slug is not an error here
        return provider_name.strip().lower()


def _anthropic_options(model: str | None) -> dict[str, object]:
    """Claude: effort is GA on a short allowlist, and ``xhigh`` is gated
    inside it. Both predicates live in query.py because that is where the
    request is built; importing them keeps one source of truth rather than
    a copy that silently rots when a new model is added there."""
    from src.query.query import _model_supports_effort, _model_supports_xhigh_effort

    if not _model_supports_effort(model):
        # Not a failure — the request still succeeds, the API just uses its
        # own default and a requested level is dropped on the floor. That is
        # precisely the silent no-op the picker should not offer.
        return {"supported": False, "levels": []}

    xhigh_ok = _model_supports_xhigh_effort(model)

    # ``max`` is broadly accepted (the 400 that rejects xhigh on sonnet-4-6
    # names max among the supported levels), so only xhigh needs gating.
    return {
        "supported": True,
        "levels": [lvl for lvl in LADDER if lvl != "xhigh" or xhigh_ok],
    }


def _openai_options(model: str | None) -> dict[str, object]:
    """OpenAI: the Responses API rejects a reasoning block outright on
    non-reasoning models, so those get no third step at all."""
    from src.providers.openai_responses import OPENAI_REASONING_EFFORTS, supports_reasoning

    if not supports_reasoning(model or ""):
        return {"supported": False, "levels": []}

    # Intersect rather than pass through: OPENAI_REASONING_EFFORTS carries
    # ``none``, which ``/effort`` would reject, and omits ``max``, which
    # OpenAI 400s on for the same model OpenRouter tolerates it for.
    return {
        "supported": True,
        "levels": [lvl for lvl in LADDER if lvl in OPENAI_REASONING_EFFORTS],
    }
