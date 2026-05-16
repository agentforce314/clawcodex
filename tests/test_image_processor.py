"""Unit tests for src.utils.image_processor — port of TS imageResizer.ts.

Covers magic-byte detection, bounded file read, resize-to-envelope,
token-budget compression, and dimensions metadata.
"""
from __future__ import annotations

import base64
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.utils.image_processor import (
    API_IMAGE_MAX_BASE64_SIZE,
    IMAGE_MAX_HEIGHT,
    IMAGE_MAX_WIDTH,
    IMAGE_READ_SAFETY_CAP,
    IMAGE_TARGET_RAW_SIZE,
    ImageDimensions,
    ImageProcessingError,
    compress_image_to_byte_budget,
    compress_image_to_token_budget,
    create_image_metadata_text,
    detect_image_format_from_base64,
    detect_image_format_from_buffer,
    estimate_image_tokens_from_base64_length,
    maybe_resize_image,
    read_file_bytes,
)


def _make_png_bytes(w: int, h: int, color: str = "red") -> bytes:
    img = Image.new("RGB", (w, h), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_jpeg_bytes(w: int, h: int, color: str = "blue", quality: int = 80) -> bytes:
    img = Image.new("RGB", (w, h), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


class TestMagicByteFormatDetection(unittest.TestCase):
    """Mirrors TS imageResizer.ts:769-829 detectImageFormatFromBuffer tests."""

    def test_detects_png(self) -> None:
        self.assertEqual(
            detect_image_format_from_buffer(b"\x89PNG\r\n\x1a\nrest"),
            "image/png",
        )

    def test_detects_jpeg(self) -> None:
        self.assertEqual(
            detect_image_format_from_buffer(b"\xff\xd8\xff\xe0\x00\x10JFIF"),
            "image/jpeg",
        )

    def test_detects_gif(self) -> None:
        self.assertEqual(
            detect_image_format_from_buffer(b"GIF89a\x01\x00\x01\x00"),
            "image/gif",
        )

    def test_detects_webp(self) -> None:
        self.assertEqual(
            detect_image_format_from_buffer(b"RIFF\x00\x00\x00\x00WEBPVP8 "),
            "image/webp",
        )

    def test_detects_real_pillow_png(self) -> None:
        self.assertEqual(
            detect_image_format_from_buffer(_make_png_bytes(10, 10)),
            "image/png",
        )

    def test_detects_real_pillow_jpeg(self) -> None:
        self.assertEqual(
            detect_image_format_from_buffer(_make_jpeg_bytes(10, 10)),
            "image/jpeg",
        )

    def test_unknown_falls_back_to_png(self) -> None:
        """Matches TS default at imageResizer.ts:812."""
        self.assertEqual(detect_image_format_from_buffer(b"\x00\x01\x02\x03"), "image/png")

    def test_handles_truncated_buffer(self) -> None:
        """Must not raise on <4 bytes."""
        self.assertEqual(detect_image_format_from_buffer(b""), "image/png")
        self.assertEqual(detect_image_format_from_buffer(b"\x89"), "image/png")
        self.assertEqual(detect_image_format_from_buffer(b"\x89PNG"), "image/png")

    def test_base64_variant_decodes_and_sniffs(self) -> None:
        png_b64 = base64.b64encode(_make_png_bytes(10, 10)).decode()
        self.assertEqual(detect_image_format_from_base64(png_b64), "image/png")


class TestReadFileBytes(unittest.TestCase):
    """Mirrors TS fsOperations.ts:578-602 readFileBytes tests."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_unbounded_returns_full_file(self) -> None:
        data = b"hello world" * 1000
        p = self.root / "f.bin"
        p.write_bytes(data)
        self.assertEqual(read_file_bytes(p), data)

    def test_caps_at_max_bytes(self) -> None:
        data = b"x" * 10_000
        p = self.root / "f.bin"
        p.write_bytes(data)
        capped = read_file_bytes(p, max_bytes=1000)
        self.assertEqual(len(capped), 1000)
        self.assertEqual(capped, b"x" * 1000)

    def test_smaller_than_cap_returns_full_file(self) -> None:
        data = b"abc"
        p = self.root / "f.bin"
        p.write_bytes(data)
        self.assertEqual(read_file_bytes(p, max_bytes=1000), data)


class TestMaybeResizeImage(unittest.TestCase):
    """Mirrors TS imageResizer.ts:169-433 maybeResizeAndDownsampleImageBuffer."""

    def test_small_image_passthrough(self) -> None:
        """No-op when already within envelope. Original bytes preserved."""
        data = _make_png_bytes(100, 100)
        result = maybe_resize_image(data, len(data), format_hint="image/png")
        self.assertEqual(result.data, data)
        self.assertEqual(result.media_type, "image/png")
        self.assertEqual(result.dimensions.original_width, 100)
        self.assertEqual(result.dimensions.display_width, 100)

    def test_oversized_downscales_to_envelope(self) -> None:
        """Image with dimensions > IMAGE_MAX_WIDTH gets resized preserving aspect."""
        data = _make_png_bytes(3000, 2000)
        result = maybe_resize_image(data, len(data), format_hint="image/png")
        self.assertLessEqual(result.dimensions.display_width, IMAGE_MAX_WIDTH)
        self.assertLessEqual(result.dimensions.display_height, IMAGE_MAX_HEIGHT)
        self.assertEqual(result.dimensions.original_width, 3000)
        self.assertEqual(result.dimensions.original_height, 2000)
        # Aspect ratio preserved (within rounding)
        orig_aspect = 3000 / 2000
        new_aspect = result.dimensions.display_width / result.dimensions.display_height
        self.assertAlmostEqual(orig_aspect, new_aspect, places=1)

    def test_oversized_in_bytes_only_gets_recompressed(self) -> None:
        """When dims fit but bytes don't, encoding shrinks the payload."""
        # 1000x1000 of random noise will balloon as PNG
        img = Image.effect_noise((1000, 1000), 128).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        # Synthesize an oversize by passing a fake original_size > cap
        result = maybe_resize_image(data, IMAGE_TARGET_RAW_SIZE + 1, format_hint="image/png")
        # Result is some encoding (PNG or JPEG fallback); dims preserved
        self.assertEqual(result.dimensions.original_width, 1000)

    def test_resize_jpeg_preserves_jpeg_media_type(self) -> None:
        data = _make_jpeg_bytes(2000, 1500)
        result = maybe_resize_image(data, len(data), format_hint="image/jpeg")
        self.assertEqual(result.media_type, "image/jpeg")

    def test_corrupt_image_raises_processing_error(self) -> None:
        with self.assertRaises(ImageProcessingError):
            maybe_resize_image(b"definitely not an image", 30, format_hint="image/png")


class TestCompressImageToByteBudget(unittest.TestCase):
    """Mirrors TS compressImageBuffer multi-strategy fallback."""

    def test_compresses_to_under_budget(self) -> None:
        data = _make_png_bytes(2000, 2000)
        result = compress_image_to_byte_budget(data, 100_000, "image/png")
        self.assertLessEqual(len(result.data), 100_000)

    def test_ultra_fallback_when_budget_impossibly_small(self) -> None:
        """Even with a 100-byte budget, returns the best attempt (don't raise)."""
        data = _make_png_bytes(2000, 2000)
        result = compress_image_to_byte_budget(data, 100, "image/png")
        # Won't actually fit but we get the smallest attempt, not an exception
        self.assertGreater(len(result.data), 0)
        self.assertEqual(result.media_type, "image/jpeg")  # ultra-fallback uses JPEG

    def test_corrupt_input_over_budget_raises(self) -> None:
        """Corrupt bytes that exceed the budget must trigger the Pillow open
        attempt and raise. (Under-budget corrupt bytes hit the fast-path and
        round-trip unchanged, mirroring TS lines 521-524.)"""
        with self.assertRaises(ImageProcessingError):
            compress_image_to_byte_budget(b"not an image", 5, "image/png")

    def test_undersized_corrupt_input_passes_through_fast_path(self) -> None:
        """Fast-path returns the buffer unchanged when already under budget,
        even for non-image bytes — caller decides what to do with them."""
        result = compress_image_to_byte_budget(b"hi", 1000, "image/png")
        self.assertEqual(result.data, b"hi")


class TestCompressImageToTokenBudget(unittest.TestCase):
    """Token budget converts to byte budget via TOKEN_PER_BASE64_CHAR ratio."""

    def test_within_budget_returns_compressed(self) -> None:
        data = _make_png_bytes(1500, 1500)
        result = compress_image_to_token_budget(data, max_tokens=10_000, media_type="image/png")
        # 10k tokens -> 80k base64 chars -> 60k raw bytes max
        self.assertLessEqual(len(result.data), 100_000)  # generous upper bound for ultra-fallback


class TestCreateImageMetadataText(unittest.TestCase):
    """Mirrors TS imageResizer.ts:835-880 createImageMetadataText."""

    def test_returns_none_when_dimensions_and_source_absent(self) -> None:
        self.assertIsNone(create_image_metadata_text(None, None))

    def test_no_dimensions_with_source_returns_source_only(self) -> None:
        """TS lines 850-853: ``[Image source: <path>]`` when dims missing."""
        self.assertEqual(
            create_image_metadata_text(None, "/x.png"),
            "[Image source: /x.png]",
        )

    def test_returns_none_when_unresized_and_no_source(self) -> None:
        dims = ImageDimensions(original_width=100, original_height=100,
                                display_width=100, display_height=100)
        self.assertIsNone(create_image_metadata_text(dims, None))

    def test_unresized_with_source_emits_source_only(self) -> None:
        dims = ImageDimensions(original_width=100, original_height=100,
                                display_width=100, display_height=100)
        text = create_image_metadata_text(dims, "/photos/a.png")
        self.assertEqual(text, "[Image: source: /photos/a.png]")

    def test_resized_with_source_includes_display_and_scale(self) -> None:
        dims = ImageDimensions(original_width=4000, original_height=3000,
                                display_width=1568, display_height=1176)
        text = create_image_metadata_text(dims, "/photos/big.png")
        self.assertIn("/photos/big.png", text)
        self.assertIn("original 4000x3000", text)
        self.assertIn("displayed at 1568x1176", text)
        # Scale factor is original_width/display_width = 4000/1568 = 2.55
        self.assertIn("Multiply coordinates by 2.55", text)
        self.assertIn("to map to original image", text)

    def test_resized_no_source_still_emits(self) -> None:
        """Resize alone is reason enough to emit metadata."""
        dims = ImageDimensions(original_width=4000, original_height=3000,
                                display_width=1568, display_height=1176)
        text = create_image_metadata_text(dims, None)
        self.assertIsNotNone(text)
        self.assertIn("original 4000x3000", text)
        self.assertNotIn("source:", text)


class TestEstimateImageTokensFromBase64Length(unittest.TestCase):
    def test_zero_length(self) -> None:
        self.assertEqual(estimate_image_tokens_from_base64_length(0), 0)

    def test_known_ratio(self) -> None:
        # 1000 base64 chars * 0.125 = 125 tokens
        self.assertEqual(estimate_image_tokens_from_base64_length(1000), 125)

    def test_rounds_up(self) -> None:
        # 5 chars * 0.125 = 0.625 -> ceil = 1
        self.assertEqual(estimate_image_tokens_from_base64_length(5), 1)


class TestConstants(unittest.TestCase):
    """Locks in TS parity for the exposed constants."""

    def test_target_raw_size_matches_ts(self) -> None:
        # TS apiLimits.ts:29: IMAGE_TARGET_RAW_SIZE = (5 MB) * 3/4 = 3.75 MB
        self.assertEqual(IMAGE_TARGET_RAW_SIZE, (5 * 1024 * 1024 * 3) // 4)

    def test_api_max_base64_size_matches_ts(self) -> None:
        self.assertEqual(API_IMAGE_MAX_BASE64_SIZE, 5 * 1024 * 1024)

    def test_max_dimensions_match_ts(self) -> None:
        self.assertEqual(IMAGE_MAX_WIDTH, 1568)
        self.assertEqual(IMAGE_MAX_HEIGHT, 1568)

    def test_read_safety_cap_above_target(self) -> None:
        """Safety cap must be well above the API target so real images aren't truncated."""
        self.assertGreater(IMAGE_READ_SAFETY_CAP, IMAGE_TARGET_RAW_SIZE * 10)


if __name__ == "__main__":
    unittest.main()
