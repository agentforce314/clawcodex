"""Image processing utilities for the Read tool.

Port of ``typescript/src/utils/imageResizer.ts``. Pillow replaces ``sharp``.

The TS reference implements:
- Magic-byte format detection (PNG/JPEG/GIF/WebP)
- Resize-to-fit (3.75 MB / 1568×1568 envelope)
- Multi-strategy compression (PNG palette → JPEG q={80,60,40,20})
- Token-budget compression (per-pixel budget → byte budget → resize+encode)
- Image dimensions metadata for coordinate-mapping prompts

This module is the Python equivalent. Pillow is imported lazily inside the
functions that need it so importing this module is cheap and so callers
that only need magic-byte detection don't pay the Pillow cost.

See also: ``my-docs/image-handling-gap-analysis.md`` and
``my-docs/image-handling-refactoring-plan.md``.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants (mirrors typescript/src/constants/apiLimits.ts)
# ---------------------------------------------------------------------------

# Hard API limit on a base64-encoded image (5 MB of base64 string).
API_IMAGE_MAX_BASE64_SIZE = 5 * 1024 * 1024

# Target raw-byte size before base64 encoding: base64 inflates by 4/3, so
# 3.75 MB raw → 5 MB base64. Anything bigger gets compressed/resized.
IMAGE_TARGET_RAW_SIZE = (API_IMAGE_MAX_BASE64_SIZE * 3) // 4

# Client-side max dimensions. The API internally resizes >1568, so going
# higher just wastes bandwidth.
IMAGE_MAX_WIDTH = 1568
IMAGE_MAX_HEIGHT = 1568

# Generous safety cap on the bounded image read: well above any realistic
# image (high-DPI scans top out ~30-40 MB) but below where a symlinked
# /dev/zero would OOM the process. Used by the Read tool to bound disk I/O;
# pillow then resizes the in-memory buffer down to IMAGE_TARGET_RAW_SIZE.
# TS readImageWithTokenBudget passes no cap (undefined maxBytes); we add
# one as defense-in-depth.
IMAGE_READ_SAFETY_CAP = 50 * 1024 * 1024

# Token-per-base64-character ratio. TS uses 0.125 (~8 tokens per char) as a
# fixed estimate at FileReadTool.ts:1140. We mirror it.
TOKEN_PER_BASE64_CHAR = 0.125


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ImageDimensions:
    """Original + display dimensions for coordinate-mapping prompts."""
    original_width: int | None = None
    original_height: int | None = None
    display_width: int | None = None
    display_height: int | None = None


@dataclass(frozen=True)
class ResizeResult:
    """Output of resize/compress operations."""
    data: bytes
    media_type: str  # e.g. "image/png"
    dimensions: ImageDimensions | None


class ImageProcessingError(Exception):
    """Raised when an image cannot be decoded, resized, or compressed."""


# ---------------------------------------------------------------------------
# Magic-byte format detection (no Pillow needed)
# ---------------------------------------------------------------------------

def detect_image_format_from_buffer(buf: bytes) -> str:
    """Return the media type of an image buffer based on magic bytes.

    Mirrors TS ``detectImageFormatFromBuffer`` at imageResizer.ts:769-812.
    Falls back to ``image/png`` for unknown formats — matches TS.
    """
    if len(buf) >= 4 and buf[0] == 0x89 and buf[1:4] == b"PNG":
        return "image/png"
    if len(buf) >= 3 and buf[0] == 0xFF and buf[1] == 0xD8 and buf[2] == 0xFF:
        return "image/jpeg"
    if len(buf) >= 3 and buf[0:3] == b"GIF":
        return "image/gif"
    if (
        len(buf) >= 12
        and buf[0:4] == b"RIFF"
        and buf[8:12] == b"WEBP"
    ):
        return "image/webp"
    return "image/png"


def detect_image_format_from_base64(b64: str) -> str:
    """Decode the leading bytes of a base64 string and sniff the format."""
    import base64
    # Decode just enough to inspect magic bytes. 16 b64 chars → 12 raw bytes,
    # enough for WebP's RIFF...WEBP signature at offset 8.
    head = b64[:24]
    try:
        head_bytes = base64.b64decode(head, validate=False)
    except Exception:  # pragma: no cover - defensive
        return "image/png"
    return detect_image_format_from_buffer(head_bytes)


# ---------------------------------------------------------------------------
# Bounded file read (port of fsOperations.ts:578-602 readFileBytes)
# ---------------------------------------------------------------------------

def read_file_bytes(path: Path, max_bytes: int | None = None) -> bytes:
    """Read up to ``max_bytes`` from ``path``.

    Caps disk I/O so a symlink to /dev/zero or a corrupted stat'd size can't
    OOM the process. When ``max_bytes`` is None, behaves like ``read_bytes``.
    """
    if max_bytes is None:
        return path.read_bytes()
    size = path.stat().st_size
    read_size = min(size, max_bytes)
    with path.open("rb") as fh:
        return fh.read(read_size)


# ---------------------------------------------------------------------------
# Pillow integration (lazy import)
# ---------------------------------------------------------------------------

def _pil():
    """Lazy import for Pillow so module import stays cheap."""
    try:
        from PIL import Image, UnidentifiedImageError  # noqa: F401
    except ImportError as e:  # pragma: no cover - dependency guaranteed by pyproject.toml
        raise ImageProcessingError(
            "Pillow is not installed. Add 'Pillow>=10.0' to dependencies."
        ) from e
    return Image, UnidentifiedImageError


def _log_image_event(subtype: str, **fields: Any) -> None:
    """Emit an IMAGE_PROCESSING analytics event, swallowing any errors.

    Telemetry is best-effort — a missing or misconfigured sink must never
    break the image pipeline.
    """
    try:
        from src.services.analytics.events import EventType, log_event
        log_event(EventType.IMAGE_PROCESSING, subtype=subtype, **fields)
    except Exception:  # pragma: no cover - telemetry is best-effort
        pass


def _pil_format_to_media_type(pil_format: str | None, fallback: str) -> str:
    """Map a Pillow format string ('PNG'/'JPEG'/'GIF'/'WEBP') to media type."""
    if not pil_format:
        return fallback
    fmt = pil_format.upper()
    if fmt == "PNG":
        return "image/png"
    if fmt in ("JPEG", "JPG"):
        return "image/jpeg"
    if fmt == "GIF":
        return "image/gif"
    if fmt == "WEBP":
        return "image/webp"
    return fallback


_SUPPORTED_ENCODE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


def _encode_image(img: Any, media_type: str, quality: int | None = None) -> bytes:
    """Encode a PIL Image to bytes in the given media type.

    Supports only the four Anthropic API image types. Raises
    ``ImageProcessingError`` for any other media_type so the caller can't
    silently mislabel bytes (e.g. asking for image/tiff and getting PNG
    bytes back tagged as image/tiff — which the API would reject).
    """
    if media_type not in _SUPPORTED_ENCODE_TYPES:
        raise ImageProcessingError(
            f"Unsupported image encoding type: {media_type}. "
            f"Supported: {sorted(_SUPPORTED_ENCODE_TYPES)}"
        )
    buf = io.BytesIO()
    if media_type == "image/png":
        # Plain optimized PNG. Palette quantization happens in
        # ``compress_image_to_byte_budget`` step 2 (where it's gated on
        # the source being PNG); keeping it out of the default encode
        # path preserves alpha for opaque-vs-transparent inputs alike.
        save_kwargs: dict[str, Any] = {"format": "PNG", "optimize": True, "compress_level": 9}
        img.save(buf, **save_kwargs)
    elif media_type == "image/jpeg":
        # JPEG requires RGB; convert if image has alpha/palette.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        save_kwargs = {"format": "JPEG", "quality": quality or 80, "optimize": True}
        img.save(buf, **save_kwargs)
    elif media_type == "image/webp":
        save_kwargs = {"format": "WEBP", "quality": quality or 80}
        img.save(buf, **save_kwargs)
    elif media_type == "image/gif":
        # GIF re-encode preserves palette but loses transparency in some
        # frames; acceptable per TS behavior.
        img.save(buf, format="GIF")
    return buf.getvalue()


def _resize_to_envelope(img: Any, max_w: int, max_h: int) -> tuple[Any, int, int]:
    """Resize ``img`` to fit within ``max_w × max_h`` preserving aspect ratio.

    Returns ``(resized_img, new_w, new_h)``. No-op when image already fits.
    Matches sharp's ``fit: 'inside', withoutEnlargement: true``.
    """
    Image, _ = _pil()
    w, h = img.size
    if w <= max_w and h <= max_h:
        return img, w, h
    scale = min(max_w / w, max_h / h)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    return resized, new_w, new_h


# ---------------------------------------------------------------------------
# Main resize pipeline (port of maybeResizeAndDownsampleImageBuffer)
# ---------------------------------------------------------------------------

def maybe_resize_image(
    buf: bytes,
    original_size: int,
    format_hint: str | None = None,
) -> ResizeResult:
    """Resize/compress ``buf`` so it fits within IMAGE_TARGET_RAW_SIZE and
    IMAGE_MAX_WIDTH/HEIGHT.

    Returns the (possibly unchanged) buffer with media type and dimensions.
    On a hard Pillow failure (corrupt image), raises ``ImageProcessingError``
    so the caller can decide whether to fall back to the raw bytes.

    Mirrors TS imageResizer.ts:169-433.
    """
    Image, UnidentifiedImageError = _pil()

    try:
        img = Image.open(io.BytesIO(buf))
        img.load()  # force decode now so we surface errors here
    except UnidentifiedImageError as e:
        _log_image_event("resize_failed", reason="unidentified", original_size=original_size)
        raise ImageProcessingError(f"Could not decode image: {e}") from e
    except Exception as e:
        _log_image_event("resize_failed", reason="open_error", error=str(e), original_size=original_size)
        raise ImageProcessingError(f"Could not open image: {e}") from e

    orig_w, orig_h = img.size
    media_type = _pil_format_to_media_type(img.format, format_hint or "image/png")

    # Fast path: already within envelope, no work needed.
    if (
        original_size <= IMAGE_TARGET_RAW_SIZE
        and orig_w <= IMAGE_MAX_WIDTH
        and orig_h <= IMAGE_MAX_HEIGHT
    ):
        return ResizeResult(
            data=buf,
            media_type=media_type,
            dimensions=ImageDimensions(
                original_width=orig_w,
                original_height=orig_h,
                display_width=orig_w,
                display_height=orig_h,
            ),
        )

    # Resize to fit IMAGE_MAX_WIDTH × IMAGE_MAX_HEIGHT, preserving aspect.
    resized_img, new_w, new_h = _resize_to_envelope(img, IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT)

    # First encoding attempt at the original media type.
    try:
        encoded = _encode_image(resized_img, media_type)
    except Exception as e:
        _log_image_event("resize_failed", reason="encode_error", error=str(e))
        raise ImageProcessingError(f"Could not encode image: {e}") from e

    # If the resized+encoded version fits, ship it.
    if len(encoded) <= IMAGE_TARGET_RAW_SIZE:
        return ResizeResult(
            data=encoded,
            media_type=media_type,
            dimensions=ImageDimensions(
                original_width=orig_w,
                original_height=orig_h,
                display_width=new_w,
                display_height=new_h,
            ),
        )

    # Still too big — try JPEG at progressive qualities. Matches TS line 290-330.
    last_jpeg_attempt: bytes | None = None
    for quality in (80, 60, 40, 20):
        try:
            attempt = _encode_image(resized_img, "image/jpeg", quality=quality)
        except Exception:
            continue
        last_jpeg_attempt = attempt
        if len(attempt) <= IMAGE_TARGET_RAW_SIZE:
            return ResizeResult(
                data=attempt,
                media_type="image/jpeg",
                dimensions=ImageDimensions(
                    original_width=orig_w,
                    original_height=orig_h,
                    display_width=new_w,
                    display_height=new_h,
                ),
            )

    # Even q=20 didn't fit. Emit a fallback event and return the smallest
    # attempt so the caller can decide; matches TS "let oversize through with
    # a warning" at lines 387-432.
    final = last_jpeg_attempt if last_jpeg_attempt is not None else encoded
    final_media = "image/jpeg" if last_jpeg_attempt is not None else media_type
    _log_image_event(
        "resize_fallback",
        original_size=original_size,
        final_size=len(final),
    )
    return ResizeResult(
        data=final,
        media_type=final_media,
        dimensions=ImageDimensions(
            original_width=orig_w,
            original_height=orig_h,
            display_width=new_w,
            display_height=new_h,
        ),
    )


# ---------------------------------------------------------------------------
# Byte / token budget compression (port of compressImageBuffer family)
# ---------------------------------------------------------------------------

def compress_image_to_byte_budget(
    buf: bytes,
    max_bytes: int,
    media_type: str | None = None,
) -> ResizeResult:
    """Aggressively compress ``buf`` to fit under ``max_bytes``.

    Multi-strategy fallback identical to TS compressImageBuffer:
    0. Fast path: if already under budget, return unchanged.
    1. Progressive resize at [1.0, 0.75, 0.5, 0.25] with JPEG q=80→q=50
    2. PNG palette quantization for PNG sources
    3. Ultra-fallback: 400×400 / JPEG q=20

    The ``media_type`` parameter is advisory — the actual source format is
    re-sniffed from magic bytes inside the function so a mislabeled input
    still routes through the right palette/JPEG path. Raises
    ``ImageProcessingError`` if even step 3 fails.

    Note: the fast-path return (already under budget) returns
    ``dimensions=None`` because Pillow is never opened. Callers that need
    dims for already-under-budget buffers must call ``maybe_resize_image``
    instead, which always populates them.
    """
    # Fast path mirrors TS imageResizer.ts:521-524: skip Pillow entirely
    # when the buffer already fits, preserving the original format/bytes.
    if len(buf) <= max_bytes:
        sniffed = detect_image_format_from_buffer(buf)
        return ResizeResult(data=buf, media_type=sniffed, dimensions=None)

    Image, _ = _pil()

    try:
        img = Image.open(io.BytesIO(buf))
        img.load()
    except Exception as e:
        _log_image_event("compress_failed", reason="open_error", error=str(e))
        raise ImageProcessingError(f"Could not decode image for compression: {e}") from e

    orig_w, orig_h = img.size
    # Re-sniff input format so a caller-supplied media_type that disagrees
    # with the actual bytes doesn't route through the wrong branch.
    sniffed_media = detect_image_format_from_buffer(buf)

    # Step 1: progressive resize × quality grid.
    last_attempt: bytes | None = None
    last_attempt_dims: tuple[int, int] | None = None
    for scale in (1.0, 0.75, 0.5, 0.25):
        target_w = max(1, int(orig_w * scale))
        target_h = max(1, int(orig_h * scale))
        scaled = img if scale == 1.0 else img.resize((target_w, target_h), Image.LANCZOS)
        for quality in (80, 50):
            try:
                attempt = _encode_image(scaled, "image/jpeg", quality=quality)
            except Exception:
                continue
            last_attempt = attempt
            last_attempt_dims = (target_w, target_h)
            if len(attempt) <= max_bytes:
                return ResizeResult(
                    data=attempt,
                    media_type="image/jpeg",
                    dimensions=ImageDimensions(
                        original_width=orig_w,
                        original_height=orig_h,
                        display_width=target_w,
                        display_height=target_h,
                    ),
                )

    # Step 2: PNG palette reduction (only meaningful for PNG sources).
    # Resize to 800x800 first so a high-res palette PNG fits where the
    # full-resolution palette wouldn't. Mirrors TS imageResizer.ts:702-723.
    if sniffed_media == "image/png":
        try:
            palette_source, pw, ph = _resize_to_envelope(img, 800, 800)
            palette_img = palette_source.convert("P", palette=Image.ADAPTIVE, colors=64)
            attempt = _encode_image(palette_img, "image/png")
            if len(attempt) <= max_bytes:
                return ResizeResult(
                    data=attempt,
                    media_type="image/png",
                    dimensions=ImageDimensions(
                        original_width=orig_w,
                        original_height=orig_h,
                        display_width=pw,
                        display_height=ph,
                    ),
                )
        except Exception:
            pass

    # Step 3: ultra-fallback (400×400, JPEG q=20).
    try:
        fallback_img, fw, fh = _resize_to_envelope(img, 400, 400)
        attempt = _encode_image(fallback_img, "image/jpeg", quality=20)
        # Return whatever we got, even if still over budget — better than
        # rejecting outright.
        if len(attempt) > max_bytes:
            _log_image_event(
                "compress_fallback",
                requested_bytes=max_bytes,
                final_bytes=len(attempt),
            )
        return ResizeResult(
            data=attempt,
            media_type="image/jpeg",
            dimensions=ImageDimensions(
                original_width=orig_w,
                original_height=orig_h,
                display_width=fw,
                display_height=fh,
            ),
        )
    except Exception as e:
        _log_image_event("compress_failed", reason="ultra_failed", error=str(e))
        raise ImageProcessingError(f"Could not compress image: {e}") from e


def compress_image_to_token_budget(
    buf: bytes,
    max_tokens: int,
    media_type: str | None = None,
) -> ResizeResult:
    """Convert a token budget to a byte budget and compress.

    Mirrors TS compressImageBufferWithTokenLimit. The 0.125 ratio
    (TOKEN_PER_BASE64_CHAR) is TS's fixed estimate for tokens per base64
    character; equivalently, 8 base64 chars per token. The ``media_type``
    parameter is advisory; the actual format is re-sniffed from the buffer.
    """
    max_base64_chars = max_tokens * 8  # int form of max_tokens / 0.125
    max_bytes = (max_base64_chars * 3) // 4
    return compress_image_to_byte_budget(buf, max_bytes, media_type)


# ---------------------------------------------------------------------------
# Dimensions metadata (port of createImageMetadataText)
# ---------------------------------------------------------------------------

def create_image_metadata_text(
    dimensions: ImageDimensions | None,
    source_path: str | None,
) -> str | None:
    """Build the human-readable metadata string sent as an isMeta user message.

    Matches TS imageResizer.ts:835-880 verbatim:
    - No dims AND no source -> None.
    - No dims AND source     -> ``[Image source: <path>]``
    - Unresized AND source   -> ``[Image: source: <path>]``
    - Unresized AND no source -> None
    - Resized (no source)     -> ``[Image: original WxH, displayed at wxh. Multiply coordinates by X to map to original image.]``
    - Resized (with source)   -> ``[Image: source: <path>, original WxH, displayed at wxh. Multiply coordinates by X to map to original image.]``

    The scale factor is ``original_width / display_width`` (model sees the
    displayed image and wants to point at coordinates in the original).
    """
    if dimensions is None:
        if source_path:
            return f"[Image source: {source_path}]"
        return None
    ow, oh = dimensions.original_width, dimensions.original_height
    dw, dh = dimensions.display_width, dimensions.display_height
    # Invalid or missing dims -> source-only fallback (matches TS).
    if not ow or not oh or not dw or not dh or dw <= 0 or dh <= 0:
        if source_path:
            return f"[Image source: {source_path}]"
        return None
    was_resized = (ow != dw) or (oh != dh)
    if not was_resized and not source_path:
        return None
    parts: list[str] = []
    if source_path:
        parts.append(f"source: {source_path}")
    if was_resized:
        scale_factor = ow / dw
        parts.append(
            f"original {ow}x{oh}, displayed at {dw}x{dh}. "
            f"Multiply coordinates by {scale_factor:.2f} to map to original image."
        )
    return f"[Image: {', '.join(parts)}]"


# ---------------------------------------------------------------------------
# Token estimation helper (mirrors TS inline at FileReadTool.ts:1140)
# ---------------------------------------------------------------------------

def estimate_image_tokens_from_base64_length(base64_len: int) -> int:
    """Return the rough token count for a base64-encoded image payload."""
    return math.ceil(base64_len * TOKEN_PER_BASE64_CHAR)
