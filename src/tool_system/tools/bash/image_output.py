"""Bash-tool data-URI image output handling.

Port of ``typescript/src/tools/BashTool/utils.ts`` functions ``isImageOutput``,
``parseDataUri``, ``buildImageToolResult``, plus ``resizeShellImageOutput``
which keeps the image within Anthropic's 5 MB API limit before sending. When
a shell command (e.g. ``matplotlib.savefig`` printed to stdout) emits a
``data:image/...;base64,...`` string, the Bash tool surfaces it as an image
content block to the model instead of garbage text.
"""
from __future__ import annotations

import base64
import re
from typing import Any

# Match the entire stdout. TS regex at BashTool/utils.ts:49.
_IS_IMAGE_OUTPUT_RE = re.compile(r"^data:image/[a-z0-9.+_-]+;base64,", re.IGNORECASE)

# Parse out (mediaType, base64). TS DATA_URI_RE at utils.ts:53.
_PARSE_DATA_URI_RE = re.compile(r"^data:([^;]+);base64,(.+)$")

# Hard ceiling on the input data URI string length before decode, to prevent
# OOM from a hostile shell that emits gigabytes of base64. 25 MB of base64
# decodes to ~18.75 MB raw, well above any legitimate image. Mirrors TS
# MAX_IMAGE_FILE_SIZE at BashTool/utils.ts:96 (20 MB).
_MAX_DATA_URI_BYTES = 25 * 1024 * 1024


def is_image_output(content: str) -> bool:
    """True when ``content`` starts with a base64 image data URI.

    Matches TS isImageOutput at BashTool/utils.ts:49.
    """
    if not isinstance(content, str):
        return False
    return bool(_IS_IMAGE_OUTPUT_RE.match(content.strip()))


def parse_data_uri(s: str) -> tuple[str, str] | None:
    """Return ``(media_type, base64_data)`` or ``None`` if parse fails.

    Matches TS parseDataUri at BashTool/utils.ts:59-65.
    """
    if not isinstance(s, str):
        return None
    m = _PARSE_DATA_URI_RE.match(s.strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def build_image_tool_result(stdout: str) -> list[dict[str, Any]] | None:
    """Build the ``content`` list for a tool_result block containing an image.

    Decodes the data URI and runs it through the image processor so the
    result fits within Anthropic's 5 MB base64 image limit and any
    coordinate-mapping metadata is captured. A hostile shell that emits
    >25 MB of base64 is rejected before decode to avoid OOM.

    Returns ``None`` if ``stdout`` does not parse as a data URI or if the
    image cannot be processed; caller falls through to plain-text handling.
    Mirrors TS buildImageToolResult + resizeShellImageOutput at
    BashTool/utils.ts:71-131.
    """
    if not isinstance(stdout, str) or len(stdout) > _MAX_DATA_URI_BYTES:
        return None
    parsed = parse_data_uri(stdout)
    if parsed is None:
        return None
    media_type, b64 = parsed
    try:
        raw_bytes = base64.b64decode(b64, validate=False)
    except Exception:
        return None
    if not raw_bytes:
        return None
    # Run through the image processor: resize to fit IMAGE_TARGET_RAW_SIZE
    # (3.75 MB raw -> 5 MB base64). Falls back to the raw bytes if Pillow
    # can't decode (e.g. SVG; data URI tells us it's image/svg+xml but
    # Pillow doesn't natively render SVG).
    try:
        from src.utils.image_processor import (
            ImageProcessingError,
            ResizeResult,
            detect_image_format_from_buffer,
            maybe_resize_image,
        )
        detected = detect_image_format_from_buffer(raw_bytes)
        try:
            result = maybe_resize_image(raw_bytes, len(raw_bytes), format_hint=detected)
        except ImageProcessingError:
            # Unsupported format (SVG, ICO, etc.) — pass through unchanged so
            # the model still gets an image block.
            result = ResizeResult(data=raw_bytes, media_type=media_type, dimensions=None)
        final_b64 = base64.b64encode(result.data).decode("ascii")
        final_media = result.media_type or media_type
    except Exception:
        # If anything in the resize path fails unexpectedly, fall back to the
        # raw input so the user at least sees the image.
        final_b64 = b64
        final_media = media_type
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": final_media,
                "data": final_b64,
            },
        }
    ]
