"""Pre-API image size validation.

Port of ``typescript/src/utils/imageValidation.ts``. Walks the messages
about to be sent to the API, rejects any base64-encoded image whose
payload exceeds Anthropic's 5 MB hard limit. Catching this client-side
gives a cleaner error and avoids a wasted network round trip.

Mirrors TS's ImageSizeError + validateImagesForAPI exports.
"""
from __future__ import annotations

from typing import Any

from src.utils.image_processor import API_IMAGE_MAX_BASE64_SIZE


class ImageSizeError(Exception):
    """Raised when one or more images exceed Anthropic's 5 MB base64 limit.

    Carries the offending base64 lengths so the caller can build an actionable
    error message (e.g. "image at message[3].content[1] is 8.2 MB, max 5 MB").
    """

    def __init__(self, oversized: list[tuple[int, int]]):
        """``oversized`` is a list of ``(byte_length, max_bytes)`` tuples."""
        self.oversized = oversized
        msg_parts = [
            f"image at index {i} is {sz / (1024 * 1024):.2f} MB (max {mx / (1024 * 1024):.2f} MB)"
            for i, (sz, mx) in enumerate(oversized)
        ]
        super().__init__(
            f"{len(oversized)} image(s) exceed the Anthropic API size limit: "
            + "; ".join(msg_parts)
        )


def _iter_content_blocks(content: Any):
    """Yield each content block from a typed or dict message body."""
    if isinstance(content, list):
        for block in content:
            yield block
    elif isinstance(content, str):
        return
    else:
        # Typed Message with .content attribute
        inner = getattr(content, "content", None)
        if isinstance(inner, list):
            for block in inner:
                yield block


def _get_block_type(block: Any) -> str | None:
    if isinstance(block, dict):
        return block.get("type")
    return getattr(block, "type", None)


def _get_image_source_data(block: Any) -> str | None:
    """Return the base64 ``data`` field of an image block, or None.

    Handles both:
    - ``{"type": "image", "source": {"data": "...", ...}}``  (dict form)
    - ``ImageBlockParam`` typed objects with ``.source.data``
    """
    if isinstance(block, dict):
        source = block.get("source")
        if isinstance(source, dict):
            data = source.get("data")
            return data if isinstance(data, str) else None
        return None
    # Typed object
    source = getattr(block, "source", None)
    if source is None:
        return None
    data = getattr(source, "data", None) if not isinstance(source, dict) else source.get("data")
    return data if isinstance(data, str) else None


# Cap on nested tool_result depth the walker will descend into. The
# Anthropic API doesn't accept arbitrarily deep nesting in production
# (tool_result content is normally one level deep); the limit guards a
# pathologically constructed message from blowing Python's recursion
# limit. Pick a value comfortably above any realistic depth.
_MAX_TOOL_RESULT_DEPTH = 32


def _iter_image_blocks(content: Any, _depth: int = 0):
    """Yield every image block reachable from ``content``.

    Walks top-level blocks AND descends into ``tool_result`` block content
    lists. Required because the Read tool now returns images inside a
    tool_result's ``content`` (post-#154/#155 image-handling parity); a
    walker that only looks at the outer user message would miss them and
    let an oversized base64 reach the API.

    Recursion is bounded by ``_MAX_TOOL_RESULT_DEPTH`` so an adversarial
    or accidentally over-nested message cannot hit Python's default
    recursion limit (typically ~1000). Production tool_result nesting is
    single-digit; the cap is generous defense-in-depth. Depth-first
    yield order matches a reader's mental model of the message: for
    ``[ImgA, ToolResult(ImgB), ImgC]`` the walker yields A, B, C, which
    matches the index numbering used in ``ImageSizeError``.
    """
    if _depth > _MAX_TOOL_RESULT_DEPTH:
        return
    for block in _iter_content_blocks(content):
        btype = _get_block_type(block)
        if btype == "image":
            yield block
        elif btype == "tool_result":
            # tool_result.content is either a string (no images) or a
            # list of blocks; recurse so we descend in document order.
            if isinstance(block, dict):
                inner = block.get("content")
            else:
                inner = getattr(block, "content", None)
            if isinstance(inner, list):
                yield from _iter_image_blocks(inner, _depth + 1)


def validate_images_for_api(messages: list[Any]) -> None:
    """Walk ``messages`` and raise ImageSizeError if any image is too large.

    Each image block's base64 string length is compared to
    ``API_IMAGE_MAX_BASE64_SIZE`` (5 MB). Mirrors TS imageValidation.ts:52-105.

    Recursion covers tool_result-nested images so the Read tool's image
    return path is also guarded; see ``_iter_image_blocks``.
    """
    oversized: list[tuple[int, int]] = []
    for msg in messages:
        # Get the content body whether msg is a dict or a typed Message
        if isinstance(msg, dict):
            content = msg.get("content")
        else:
            content = getattr(msg, "content", None)
        for block in _iter_image_blocks(content):
            data = _get_image_source_data(block)
            if data is None:
                continue
            if len(data) > API_IMAGE_MAX_BASE64_SIZE:
                oversized.append((len(data), API_IMAGE_MAX_BASE64_SIZE))
    if oversized:
        # Emit analytics for the failure (best-effort).
        try:
            from src.services.analytics.events import EventType, log_event
            log_event(
                EventType.IMAGE_PROCESSING,
                subtype="api_validation_failed",
                count=len(oversized),
                max_size=max(sz for sz, _ in oversized),
            )
        except Exception:  # pragma: no cover - telemetry is best-effort
            pass
        raise ImageSizeError(oversized)
