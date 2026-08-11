"""Query 恢复判定的纯函数与上下文上限解析。"""

from __future__ import annotations

import logging

from ..providers.base import BaseProvider
from ..types.messages import AssistantMessage, Message

logger = logging.getLogger(__name__)


def get_context_window(provider: BaseProvider) -> int:
    """解析 provider/model context window，未知时使用 200K。"""

    context_window = getattr(provider, "context_window", None)
    if isinstance(context_window, int) and context_window > 0:
        return context_window
    model = getattr(provider, "model", None)
    if isinstance(model, str) and model:
        try:
            from src.models.context import get_context_window_for_model

            return get_context_window_for_model(
                model,
                base_url=getattr(provider, "base_url", None),
            )
        except Exception:  # noqa: BLE001 — 恢复上限解析失败时使用安全默认值
            logger.debug("model context-window resolution failed", exc_info=True)
    return 200_000


def is_withheld_error(message: Message | None, reason: str) -> bool:
    return (
        isinstance(message, AssistantMessage)
        and getattr(message, "_api_error", None) == reason
    )


def is_withheld_max_output_tokens(message: Message | None) -> bool:
    return is_withheld_error(message, "max_output_tokens")


def is_withheld_prompt_too_long(message: Message | None) -> bool:
    return is_withheld_error(message, "prompt_too_long")


def is_withheld_media_size(message: Message | None) -> bool:
    return is_withheld_error(message, "media_size")
