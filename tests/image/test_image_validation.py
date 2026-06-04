"""Tests for src.utils.image_validation — port of TS imageValidation.ts."""
from __future__ import annotations

import unittest
from typing import Any

from src.utils.image_processor import API_IMAGE_MAX_BASE64_SIZE
from src.utils.image_validation import ImageSizeError, validate_images_for_api


def _img_block(b64_len: int) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "A" * b64_len},
    }


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def _msg(content) -> dict:
    return {"role": "user", "content": content}


class TestValidateImagesForAPI(unittest.TestCase):
    def test_no_images_passes(self) -> None:
        validate_images_for_api([_msg([_text_block("hi")])])
        validate_images_for_api([_msg("just a string")])
        validate_images_for_api([])

    def test_image_within_limit_passes(self) -> None:
        validate_images_for_api([_msg([_img_block(1000)])])

    def test_image_at_limit_passes(self) -> None:
        validate_images_for_api([_msg([_img_block(API_IMAGE_MAX_BASE64_SIZE)])])

    def test_oversize_image_raises(self) -> None:
        with self.assertRaises(ImageSizeError) as cm:
            validate_images_for_api([_msg([_img_block(API_IMAGE_MAX_BASE64_SIZE + 1)])])
        # Error reports the oversized image
        self.assertEqual(len(cm.exception.oversized), 1)
        self.assertIn("exceed the Anthropic API size limit", str(cm.exception))

    def test_multiple_oversize_reported_together(self) -> None:
        msgs = [
            _msg([_img_block(API_IMAGE_MAX_BASE64_SIZE + 100)]),
            _msg([_img_block(API_IMAGE_MAX_BASE64_SIZE + 200)]),
        ]
        with self.assertRaises(ImageSizeError) as cm:
            validate_images_for_api(msgs)
        self.assertEqual(len(cm.exception.oversized), 2)

    def test_mixed_image_and_text_within_limit_passes(self) -> None:
        validate_images_for_api([
            _msg([_text_block("hi"), _img_block(1000), _text_block("bye")]),
        ])

    def test_handles_nested_tool_result_with_image(self) -> None:
        """Image blocks inside content lists are detected regardless of where
        they sit (e.g. inside an arbitrary block list)."""
        msgs = [_msg([_img_block(API_IMAGE_MAX_BASE64_SIZE + 1)])]
        with self.assertRaises(ImageSizeError):
            validate_images_for_api(msgs)

    def test_oversize_image_inside_tool_result_content_raises(self) -> None:
        """Read tool returns image blocks inside ``tool_result.content``
        (post-#154/#155). Validator must recurse so it sees them, otherwise
        an oversized base64 reaches the API and surfaces as an opaque error.
        """
        nested = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_1",
                    "content": [
                        {"type": "text", "text": "image content:"},
                        _img_block(API_IMAGE_MAX_BASE64_SIZE + 1),
                    ],
                }
            ],
        }
        with self.assertRaises(ImageSizeError) as cm:
            validate_images_for_api([nested])
        self.assertEqual(len(cm.exception.oversized), 1)

    def test_in_limit_image_inside_tool_result_passes(self) -> None:
        nested = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu_1",
                    "content": [_img_block(1024)],
                }
            ],
        }
        validate_images_for_api([nested])  # should not raise

    def test_tool_result_with_string_content_does_not_crash(self) -> None:
        """Defensive: a tool_result whose ``content`` is a plain string
        (no images) must not trip the new recursion."""
        msgs = [{
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}],
        }]
        validate_images_for_api(msgs)  # should not raise

    def test_deeply_nested_tool_results_hit_depth_limit_not_recursion_error(self) -> None:
        """Pathological nesting must NOT raise ``RecursionError``. The
        walker has a soft depth cap (``_MAX_TOOL_RESULT_DEPTH``) and
        stops descending past it. Beyond-the-cap images are skipped
        silently (a wider validator could log a warning, but raising
        would convert a defensive guard into a crash for any
        adversarial input).
        """
        # Build a chain 5000-deep — well above Python's default
        # recursion limit (~1000), guaranteeing the unbounded form
        # would crash.
        innermost = _img_block(API_IMAGE_MAX_BASE64_SIZE + 1)
        node: Any = [innermost]
        for _ in range(5000):
            node = [{"type": "tool_result", "tool_use_id": "x", "content": node}]
        msgs = [{"role": "user", "content": node}]
        # Should NOT raise RecursionError. Deep image is past the
        # depth cap so it's silently skipped. Important: this means
        # validate_images_for_api returns without finding an oversize
        # image — the depth cap is a safety net, not a strict
        # invariant. Real-world tool_result nesting is single-digit.
        validate_images_for_api(msgs)

    def test_within_depth_limit_image_still_caught(self) -> None:
        """Sanity check the depth cap doesn't reject normal-depth tool_results."""
        # Realistic shape: single tool_result containing an image.
        msgs = [{
            "role": "user",
            "content": [{
                "type": "tool_result", "tool_use_id": "x",
                "content": [_img_block(API_IMAGE_MAX_BASE64_SIZE + 1)],
            }],
        }]
        with self.assertRaises(ImageSizeError):
            validate_images_for_api(msgs)

    def test_skips_image_block_with_no_data(self) -> None:
        """Defensive: malformed blocks without source.data don't crash."""
        bad = {"type": "image", "source": {"type": "url", "url": "https://x"}}
        validate_images_for_api([_msg([bad])])  # should not raise

    def test_skips_unknown_block_types(self) -> None:
        unknown = {"type": "weird", "payload": "x"}
        validate_images_for_api([_msg([unknown])])  # should not raise


if __name__ == "__main__":
    unittest.main()
