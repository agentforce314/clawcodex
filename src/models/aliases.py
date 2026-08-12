"""Model alias table matching TypeScript model/aliases.ts.

The bare family aliases (``sonnet`` / ``opus`` / ``haiku`` / ``fable``) track
the CURRENT first-party model of each family — the TS reference resolves
them through ``getDefaultSonnetModel()``-style functions that are updated at
every model launch (the ``@[MODEL LAUNCH]`` markers in model.ts). Targets
below were verified against the live ``GET /v1/models`` catalog 2026-08-12:
the API no longer serves ``claude-sonnet-4-20250514`` or
``claude-3-5-haiku-20241022`` (404 ``not_found_error``), so pointing an
alias at them turns every ``--model sonnet`` / Explore-agent spawn into a
hard API error.

Note on ``haiku``: the live catalog LISTS only the dated
``claude-haiku-4-5-20251001``, but the bare ``claude-haiku-4-5`` resolves
server-side to it (probed live 2026-08-12 — the API's max_tokens 400 names
the dated id), so the alias uses the bare form like every other current
target.

Per-provider SUBAGENT tier resolution (e.g. ``haiku`` on a DeepSeek
session) does not use this table — see ``subagent_model`` /
``subagent_tier_models`` in ``src/providers/__init__.py`` and
``src/agent/agent_model.py``.
"""

from __future__ import annotations

MODEL_ALIASES: dict[str, str] = {
    # Short names → canonical (current family heads)
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5",
    "haiku": "claude-haiku-4-5",
    "fable": "claude-fable-5",

    # Version aliases. The explicitly-versioned ``claude-4-*`` spellings
    # point at the newest LIVE 4.x snapshot of their family (the dated
    # 2025-05-14 ids they used to target were retired from the API).
    "claude-4-sonnet": "claude-sonnet-4-6",
    "claude-4-opus": "claude-opus-4-8",
    "claude-sonnet": "claude-sonnet-5",
    "claude-opus": "claude-opus-5",
    "claude-haiku": "claude-haiku-4-5",
    "claude-fable": "claude-fable-5",

    # Legacy aliases — explicit historical pins, kept verbatim. These name a
    # specific retired generation on purpose; the API answers 404 for them,
    # which is more honest than silently substituting a different model.
    "claude-3.5-sonnet": "claude-3-5-sonnet-20241022",
    "claude-3.5-haiku": "claude-3-5-haiku-20241022",
    "claude-3-sonnet": "claude-3-sonnet-20240229",
    "claude-3-opus": "claude-3-opus-20240229",
    "claude-3-haiku": "claude-3-haiku-20240307",
    "claude-3.7-sonnet": "claude-3-7-sonnet-20250219",

    # Common typos / shortcuts. ``s4``/``o4`` mean "sonnet 4"/"opus 4" —
    # same intent as claude-4-sonnet/claude-4-opus above, so they track the
    # same newest LIVE 4.x snapshots (their old dated targets were retired
    # and 404). ``h35`` names a specific retired generation like the legacy
    # block, and keeps its historical pin.
    "s4": "claude-sonnet-4-6",
    "o4": "claude-opus-4-8",
    "h35": "claude-3-5-haiku-20241022",
}


def resolve_alias(name: str) -> str:
    """Resolve a model alias to its canonical name.

    Returns the input unchanged if not an alias.
    """
    return MODEL_ALIASES.get(name.lower(), name)
