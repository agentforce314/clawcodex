"""Secret / API-key storage for Claw Codex.

Keys live in the single config file (``~/.clawcodex/config.json``) under a
top-level ``"env"`` object — one place for all configuration, no scattered
``.env`` files::

    {
      "default_provider": "...",
      "providers": { ... },
      "env": {
        "TAVILY_API_KEY": "tvly-xxxxxxxx"
      }
    }

Because the config hierarchy is global < project < local (deep-merged), an
``env`` block may appear at any level; later levels override earlier ones.

Resolution order for a single key (highest precedence first):

  1. the real process environment (an explicit ``export NAME=...``)
  2. the merged config ``env`` block

So an exported shell variable always wins over the stored value (handy for
temporary overrides and CI), while the config file is the durable store.

At startup :func:`apply_config_env_to_environ` copies the config ``env`` block
into ``os.environ`` (without clobbering already-set variables) so every existing
``os.environ[...]`` reader — tools, subprocesses, MCP servers — transparently
sees the stored keys. :func:`get_secret` additionally falls back to reading the
config directly, so it resolves correctly even on code paths that never ran
startup ``init()`` (standalone scripts, tests).

Values are never logged; the global config file is written ``0600`` by
``src.config._atomic_write_json``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Top-level config key holding the NAME -> value secret map.
CONFIG_ENV_KEY = "env"


def _coerce_env_map(section: Any) -> dict[str, str]:
    """Return a clean ``{NAME: str}`` map from a config ``env`` section.

    Non-dict sections, empty/non-string names, and ``bool``/``None``/container
    values are skipped (``bool`` is excluded deliberately — ``True`` is not a
    credential). Numbers are coerced to their string form.
    """
    if not isinstance(section, dict):
        return {}
    out: dict[str, str] = {}
    for name, value in section.items():
        if not isinstance(name, str) or not name:
            continue
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (str, int, float)):
            out[name] = str(value)
    return out


def _config_env() -> dict[str, str]:
    """The merged config ``env`` block as a ``{NAME: value}`` map (may be empty)."""
    try:
        from src.config import load_config

        return _coerce_env_map(load_config().get(CONFIG_ENV_KEY))
    except Exception as exc:  # config unreadable -> behave as if unset
        logger.debug("secret_store: could not read config env: %s", exc)
        return {}


def get_secret(name: str, default: str | None = None) -> str | None:
    """Resolve a single secret / API key.

    Order: real ``os.environ`` (non-empty) -> config ``env`` block -> *default*.
    Empty / whitespace-only values are treated as unset.
    """
    env_val = os.environ.get(name)
    if env_val and env_val.strip():
        return env_val
    cfg_val = _config_env().get(name)
    if cfg_val and cfg_val.strip():
        return cfg_val
    return default


def apply_config_env_to_environ(*, override: bool = False) -> list[str]:
    """Copy the config ``env`` block into ``os.environ``.

    Called once at startup (from :func:`src.init.init`). With ``override=False``
    (the default) an already-set, non-empty environment variable is left
    untouched, so an explicit ``export`` wins over the stored value. Returns the
    list of variable NAMES applied (never values) for diagnostics.
    """
    applied: list[str] = []
    for name, value in _config_env().items():
        if not override and (os.environ.get(name) or "").strip():
            continue
        os.environ[name] = value
        applied.append(name)
    if applied:
        logger.debug(
            "secret_store: applied %d config env var(s): %s",
            len(applied),
            ", ".join(sorted(applied)),
        )
    return applied


def list_secret_names() -> list[str]:
    """Sorted NAMES of keys stored in the config ``env`` block (no values)."""
    return sorted(_config_env().keys())


def set_secret(name: str, value: str) -> None:
    """Store a secret / API key in the **global** config ``env`` block.

    Writes ``~/.clawcodex/config.json`` atomically (``0600``) and refreshes the
    live process environment so the new value is visible immediately in-session.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("secret name must be a non-empty string")
    from src.config import _get_default_manager

    mgr = _get_default_manager()
    cfg = mgr.load_global()
    section = cfg.get(CONFIG_ENV_KEY)
    if not isinstance(section, dict):
        section = {}
    section[name] = value
    cfg[CONFIG_ENV_KEY] = section
    mgr.save_global(cfg)
    # Reflect immediately for the current process (explicit set overrides live).
    os.environ[name] = value


def delete_secret(name: str) -> bool:
    """Remove a secret from the global config ``env`` block.

    Returns ``True`` if a stored key was removed, ``False`` if it was absent.
    Also clears the live ``os.environ`` mirror.
    """
    from src.config import _get_default_manager

    mgr = _get_default_manager()
    cfg = mgr.load_global()
    section = cfg.get(CONFIG_ENV_KEY)
    if not isinstance(section, dict) or name not in section:
        return False
    del section[name]
    cfg[CONFIG_ENV_KEY] = section
    mgr.save_global(cfg)
    os.environ.pop(name, None)
    return True
