"""Backward-compatible shim for the renamed Z.ai (GLM) provider.

The legacy ``glm`` provider — which spoke to Zhipu's ``open.bigmodel.cn``
endpoint through the ``zhipuai`` SDK with ``zai/``-prefixed model names — has
been replaced by :class:`~src.providers.zai_provider.ZaiProvider`, which
targets Z.ai's OpenAI-compatible GLM Coding Plan endpoint. The ``glm``
provider id still resolves to the new provider (see
``src.providers.get_provider_class``); this module only preserves the old
``from src.providers.glm_provider import GLMProvider`` import path.
"""

from __future__ import annotations

from .zai_provider import ZaiProvider

# Legacy alias: ``GLMProvider`` now IS ``ZaiProvider``.
GLMProvider = ZaiProvider

__all__ = ["GLMProvider", "ZaiProvider"]
