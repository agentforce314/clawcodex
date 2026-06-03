"""Downstream provider extensions — model discovery hooks and provider overrides."""
from __future__ import annotations

# Eagerly register model discovery hooks so they're active before any
# ModelRegistry instance is created.  Registration is idempotent.
from clawcodex_ext.providers.hooks import _codex_api_discovery
from clawcodex_ext.cli.model_cmd.registry import register_discovery_hook

register_discovery_hook("openai-codex", _codex_api_discovery)
