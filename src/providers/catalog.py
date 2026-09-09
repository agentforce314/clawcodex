"""Provider catalog for the ``/model`` picker's step 1.

``get_settings`` reports only the provider the session is *currently* on, so a
picker built on it can never show more than one row. This module enumerates
:data:`src.providers.PROVIDER_INFO` — the merged registry of hand-written
provider classes plus the data-driven OpenAI-compatible specs — which is
exactly the set ``--provider`` accepts.

Each row carries what the picker renders: the ○/●/* auth mark
(``authenticated`` / ``is_current``), the model list, and the fields that drive
the inline API-key stage (``auth_type`` == ``"api_key"`` plus ``key_env``).

Credential probing is read-only and best-effort: a provider whose key cannot be
resolved is reported unauthenticated rather than raising, so one bad config
block cannot blank the whole picker.
"""

from __future__ import annotations

from typing import Any


def exclusive_env_vars(pid: str) -> tuple[list[str], list[str]]:
    """Split a provider's key env-vars into ``(exclusively-own, shared)``.

    ``provider_env_vars`` is a *resolution preference* list — "any of these
    will authenticate me" — not an ownership list, and several providers
    legitimately name another vendor's variable: ``nvidia-nim`` accepts
    ``DEEPSEEK_API_KEY``, ``siliconflow`` and ``siliconflow-cn`` share
    ``SILICONFLOW_API_KEY``, ``huggingface`` reads ``HF_TOKEN``, ``gemini``
    reads ``GOOGLE_API_KEY``.

    The config ``env`` block is one global secret namespace, so treating that
    preference list as a delete-set destroys credentials the user set for a
    *different* provider — including, in the ``nvidia-nim`` → ``DEEPSEEK``
    case, the provider the session is actively running on, which would route
    straight around the active-provider guard.

    Ownership follows the PRIMARY candidate, not mere membership. A plain
    "does anyone else list this name" test over-corrects into uselessness:
    ``DEEPSEEK_API_KEY`` is DeepSeek's own primary variable but also
    ``nvidia-nim``'s last-resort fallback, so membership alone would make
    DeepSeek — the provider this project is built around — permanently
    undisconnectable. A name is this provider's when it heads its own list and
    no other provider also leads with it, or when nobody else names it at all.
    Genuinely contested names (``siliconflow`` and ``siliconflow-cn`` both lead
    with ``SILICONFLOW_API_KEY``) stay shared and are reported, not deleted.
    """
    from . import PROVIDER_INFO, provider_env_vars

    mine = list(provider_env_vars(pid))
    primary = mine[0] if mine else None
    others_all: set[str] = set()
    others_primary: set[str] = set()
    for other in PROVIDER_INFO:
        if other == pid:
            continue
        candidates = provider_env_vars(other)
        others_all.update(candidates)
        if candidates:
            others_primary.add(candidates[0])

    owned: list[str] = []
    shared: list[str] = []
    for name in mine:
        if (name == primary and name not in others_primary) or name not in others_all:
            owned.append(name)
        else:
            shared.append(name)
    return owned, shared


def provider_catalog(
    current: str | None = None,
    current_models: list[str] | None = None,
    current_ready: bool = False,
) -> list[dict[str, Any]]:
    """Every known provider, ordered for the picker.

    ``current`` is the session's active provider id (its row gets
    ``is_current``). ``current_models`` overrides that provider's static model
    list with the session's live one, so endpoint-discovered catalogues
    (Ollama / vLLM / SGLang) show their real models instead of the static stub.
    ``current_ready`` marks the active provider authenticated because the
    session demonstrably constructed it.

    The session's flat model list includes every enabled Fusion model.
    Group those by their BASE provider here; the vision provider supplies
    only image understanding and does not own the fused model.

    Credential probing deliberately mirrors ``get_provider_config``'s literal
    lookup rather than trying to be cleverer than it, because
    ``_do_set_provider`` resolves the same way: a row reported here as
    authenticated but which ``set_provider`` then refuses would be a picker
    offering a provider you cannot actually select.

    The ACTIVE provider is the one exception. A session launched as
    ``--provider glm`` has a working ``[providers.glm]`` block that a canonical
    ``zai`` lookup cannot see — the packaged defaults seed an empty ``zai``
    block that wins the literal lookup — so probing it would render the user's
    own running provider as needing a key.

    Ordering: the active provider first, then providers that can authenticate,
    then the rest alphabetically — so the useful rows are above the fold and
    the unconfigured long tail sorts predictably.
    """
    from . import (
        PROVIDER_INFO,
        canonical_provider_name,
        provider_env_vars,
        provider_has_credentials,
        provider_requires_api_key,
        resolve_api_key,
    )

    from .fusion_models import load_fusion_models

    current_id = canonical_provider_name(current) if current else None
    fusion_models = load_fusion_models()
    fusion_names = {model.name.lower() for model in fusion_models}

    rows: list[dict[str, Any]] = []
    for pid, info in PROVIDER_INFO.items():
        is_current = pid == current_id
        try:
            api_key = resolve_api_key(pid)
        except Exception:  # noqa: BLE001 — one bad block must not blank the picker
            api_key = ""
        try:
            authenticated = provider_has_credentials(pid, api_key)
        except Exception:  # noqa: BLE001
            authenticated = bool(api_key)
        if is_current and current_ready:
            authenticated = True

        env_vars = provider_env_vars(pid)
        key_env = env_vars[0] if env_vars else ""

        models = [str(m) for m in (info.get("available_models") or [])]
        if is_current and current_models is not None:
            models = [str(m) for m in current_models]
        elif pid == "openai" and authenticated and not api_key:
            # The inactive row must use the same auth-specific catalog as a
            # live OpenAI session. Construction only checks local credentials;
            # it neither creates an SDK client nor refreshes OAuth tokens.
            try:
                from src.config import get_provider_config
                from .openai_provider import OpenAIProvider

                models = OpenAIProvider(
                    api_key=api_key,
                    base_url=get_provider_config(pid).get("base_url"),
                ).get_available_models()
            except Exception:  # noqa: BLE001 — retain the static fallback
                pass

        own_fusion = [
            model.name for model in fusion_models
            if model.enabled and canonical_provider_name(model.base.provider) == pid
        ]
        models = list(dict.fromkeys(
            own_fusion + [m for m in models if m.lower() not in fusion_names]
        ))

        row: dict[str, Any] = {
            "slug": pid,
            "name": info.get("label") or pid,
            "models": models,
            "total_models": len(models),
            "authenticated": authenticated,
            # Only providers that actually take a key get the picker's inline
            # key stage; local servers (Ollama / vLLM / SGLang) need none.
            "auth_type": "api_key" if provider_requires_api_key(pid) else "none",
            "is_current": is_current,
        }
        if key_env:
            row["key_env"] = key_env
        # What ^d would actually delete, so the confirm screen can name it
        # rather than saying a vague "removes saved credentials". Several of
        # these are conventional variables other tooling reads (HF_TOKEN,
        # GOOGLE_API_KEY), so the user deserves to see the list before
        # confirming — this only ever clears clawcodex's own config, never a
        # shell profile, but an informed choice beats a narrower rule.
        owned_env, _shared_env = exclusive_env_vars(pid)
        if current_id:
            # Subtract what the live session authenticates through: the
            # handler declines to delete those, so listing them here would
            # promise a removal the confirm screen then does not perform —
            # in the field this whole disclosure exists to make honest.
            in_use = set(provider_env_vars(current_id))
            owned_env = [e for e in owned_env if e not in in_use]
        if owned_env:
            row["removes_env"] = owned_env
        if not authenticated:
            row["warning"] = (
                f"paste {key_env} to activate"
                if key_env
                else "run `clawcodex login` to configure"
            )
        rows.append(row)

    rows.sort(
        key=lambda r: (not r["is_current"], not r["authenticated"], str(r["name"]).lower())
    )
    return rows
