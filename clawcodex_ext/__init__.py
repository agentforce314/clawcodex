"""Downstream ClawCodex extension layer."""

# Eagerly register downstream extensions that must be in place before any
# src/ code runs.  These registrations are idempotent.
from clawcodex_ext.permissions import install_permission_extensions  # noqa: F401
from clawcodex_ext.memory.scope_aware_prompt import install_memory_extension  # noqa: F401
from clawcodex_ext.providers import (  # noqa: F401 — registers model discovery hooks
    _codex_api_discovery,
)

install_permission_extensions()
install_memory_extension()
