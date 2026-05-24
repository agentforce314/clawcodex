"""Teleport utilities package.

Mirrors ``typescript/src/utils/teleport/``. Only the consumer-facing
``api.get_oauth_headers`` is ported in Phase 2 — the rest of the
teleport stack (peer discovery, message relay) is out of scope for the
bridge orchestrator port.
"""

from src.utils.teleport.api import ANTHROPIC_VERSION, get_oauth_headers

__all__ = ['ANTHROPIC_VERSION', 'get_oauth_headers']
