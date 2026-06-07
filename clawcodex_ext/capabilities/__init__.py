"""Capabilities bridge — re-exports from extensions.capabilities.

This module exists so adapter modules in ``clawcodex_ext/`` can import
from a stable path without depending directly on ``extensions/``
internal structure.  All definitions live in
``extensions/capabilities/adapter_protocol.py``.
"""

from extensions.capabilities.adapter_protocol import (  # noqa: F401
    AdapterInfo,
    AdapterProtocol,
    AdapterRegistry,
    dependency_available,
    env_switch,
    is_provider_adapter,
)
