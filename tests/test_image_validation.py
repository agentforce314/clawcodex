"""Tests for src.utils.image_validation — port of TS imageValidation.ts."""
from __future__ import annotations

import unittest

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

    def test_skips_image_block_with_no_data(self) -> None:
        """Defensive: malformed blocks without source.data don't crash."""
        bad = {"type": "image", "source": {"type": "url", "url": "https://x"}}
        validate_images_for_api([_msg([bad])])  # should not raise

    def test_skips_unknown_block_types(self) -> None:
        unknown = {"type": "weird", "payload": "x"}
        validate_images_for_api([_msg([unknown])])  # should not raise


if __name__ == "__main__":
    unittest.main()
