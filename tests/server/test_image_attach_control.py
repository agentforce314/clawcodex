"""agent-server image attachment: attach_image / clipboard_image / detect_file_drop.

The client holds no image bytes — it only renders the "📎 Attached image" notice
— so the server's pending list is the single source of truth, and the drain onto
the next prompt is the behaviour that matters.
"""

from __future__ import annotations

import asyncio
import io
import unittest
from pathlib import Path
from unittest import mock

from src.utils.image_paste import PastedImage
from src.utils.image_processor import ImageDimensions


def _png(width: int = 40, height: int = 20) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _session(cwd: str = "/tmp"):
    from src.server.agent_server import AgentServerConfig, _AgentSession

    emitted: list = []
    sess = _AgentSession(
        session_id="s1",
        cwd=cwd,
        config=AgentServerConfig(single_session=True),
        loop=mock.MagicMock(),
        out_queue=mock.MagicMock(),
    )
    sess._emit = lambda env: emitted.append(env)
    return sess, emitted


def _reply_of(emitted: list) -> dict:
    return emitted[-1]["response"]["response"]


def _fake_image(*, resized: bool = False, source: str | None = None) -> PastedImage:
    dims = (
        ImageDimensions(
            original_width=2400, original_height=1400,
            display_width=1568, display_height=914,
        )
        if resized
        else ImageDimensions(
            original_width=40, original_height=20, display_width=40, display_height=20
        )
    )
    return PastedImage(
        base64="aGVsbG8=", media_type="image/png", dimensions=dims, source_path=source
    )


class TestDrainPendingImages(unittest.TestCase):
    def test_no_pending_leaves_a_plain_string_alone(self) -> None:
        """The text-only path must not be turned into a block list."""
        sess, _ = _session()
        self.assertEqual(sess._drain_pending_images("hello"), "hello")

    def test_image_precedes_the_text(self) -> None:
        sess, _ = _session()
        sess._pending_images.append(_fake_image())
        out = sess._drain_pending_images("what is this?")
        self.assertIsInstance(out, list)
        self.assertEqual(out[0]["type"], "image")
        self.assertEqual(out[0]["source"]["media_type"], "image/png")
        self.assertEqual(out[0]["source"]["data"], "aGVsbG8=")
        self.assertEqual(out[-1], {"type": "text", "text": "what is this?"})

    def test_resized_image_carries_scale_metadata(self) -> None:
        """A downsampled screenshot needs the scale factor or coordinates lie."""
        sess, _ = _session()
        sess._pending_images.append(_fake_image(resized=True))
        out = sess._drain_pending_images("click the button")
        texts = [b["text"] for b in out if b["type"] == "text"]
        self.assertTrue(
            any("Multiply coordinates by" in t for t in texts),
            f"expected coordinate-mapping metadata, got {texts}",
        )

    def test_unresized_clipboard_image_has_no_metadata_noise(self) -> None:
        sess, _ = _session()
        sess._pending_images.append(_fake_image(resized=False))
        out = sess._drain_pending_images("hi")
        self.assertEqual([b["type"] for b in out], ["image", "text"])

    def test_drain_is_destructive(self) -> None:
        """A resend must not duplicate the image."""
        sess, _ = _session()
        sess._pending_images.append(_fake_image())
        first = sess._drain_pending_images("a")
        self.assertIsInstance(first, list)
        self.assertEqual(sess._pending_images, [])
        self.assertEqual(sess._drain_pending_images("b"), "b")

    def test_empty_text_yields_no_empty_text_block(self) -> None:
        sess, _ = _session()
        sess._pending_images.append(_fake_image())
        out = sess._drain_pending_images("")
        self.assertEqual([b["type"] for b in out], ["image"])

    def test_existing_blocks_are_preserved(self) -> None:
        sess, _ = _session()
        sess._pending_images.append(_fake_image())
        existing = [{"type": "text", "text": "hi"}]
        out = sess._drain_pending_images(existing)
        self.assertEqual(out[0]["type"], "image")
        self.assertIn({"type": "text", "text": "hi"}, out)

    def test_multiple_images_all_ride_along(self) -> None:
        sess, _ = _session()
        sess._pending_images.extend([_fake_image(), _fake_image()])
        out = sess._drain_pending_images("two")
        self.assertEqual(sum(1 for b in out if b["type"] == "image"), 2)


class TestAttachImageControl(unittest.TestCase):
    def _tmpimage(self, name: str = "a.png") -> Path:
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        p = d / name
        p.write_bytes(_png())
        return p

    def test_attach_by_path_queues_and_replies(self) -> None:
        sess, emitted = _session()
        path = self._tmpimage()
        asyncio.run(sess._do_attach_image("r1", str(path)))
        reply = _reply_of(emitted)
        self.assertEqual(reply["name"], "a.png")
        self.assertEqual(reply["width"], 40)
        self.assertEqual(reply["height"], 20)
        self.assertGreater(reply["token_estimate"], 0)
        self.assertEqual(len(sess._pending_images), 1)

    def test_reply_never_carries_base64(self) -> None:
        """Bytes stay server-side; the client has no use for them."""
        sess, emitted = _session()
        asyncio.run(sess._do_attach_image("r1", str(self._tmpimage())))
        self.assertNotIn("base64", repr(_reply_of(emitted)))

    def test_non_image_path_declines_without_error(self) -> None:
        """Caller must be free to fall through to generic file-drop handling."""
        sess, emitted = _session()
        asyncio.run(sess._do_attach_image("r1", "/tmp/notes.txt"))
        self.assertEqual(_reply_of(emitted), {})
        self.assertEqual(sess._pending_images, [])

    def test_missing_image_reports_an_error(self) -> None:
        sess, emitted = _session()
        asyncio.run(sess._do_attach_image("r1", "/nonexistent/nope.png"))
        self.assertIn("error", _reply_of(emitted))
        self.assertEqual(sess._pending_images, [])

    def test_empty_path_reports_an_error(self) -> None:
        sess, emitted = _session()
        asyncio.run(sess._do_attach_image("r1", ""))
        self.assertIn("error", _reply_of(emitted))

    def test_reader_exception_does_not_escape(self) -> None:
        sess, emitted = _session()
        with mock.patch(
            "src.utils.image_paste.try_read_image_from_path",
            side_effect=RuntimeError("boom"),
        ):
            asyncio.run(sess._do_attach_image("r1", "/tmp/a.png"))
        self.assertIn("error", _reply_of(emitted))


class TestClipboardImageControl(unittest.TestCase):
    def test_attaches_a_clipboard_image(self) -> None:
        sess, emitted = _session()
        with mock.patch(
            "src.utils.image_paste.get_image_from_clipboard", return_value=_fake_image()
        ), mock.patch(
            "src.utils.image_paste.clipboard_tooling_available", return_value=True
        ):
            asyncio.run(sess._do_clipboard_image("r1"))
        reply = _reply_of(emitted)
        self.assertEqual(reply["name"], "clipboard image")
        self.assertEqual(len(sess._pending_images), 1)

    def test_empty_clipboard_replies_empty(self) -> None:
        """Distinct from unavailable: the client falls back to a text paste."""
        sess, emitted = _session()
        with mock.patch(
            "src.utils.image_paste.get_image_from_clipboard", return_value=None
        ), mock.patch(
            "src.utils.image_paste.clipboard_tooling_available", return_value=True
        ):
            asyncio.run(sess._do_clipboard_image("r1"))
        self.assertEqual(_reply_of(emitted), {})
        self.assertEqual(sess._pending_images, [])

    def test_missing_tooling_is_distinguishable(self) -> None:
        sess, emitted = _session()
        with mock.patch(
            "src.utils.image_paste.clipboard_tooling_available", return_value=False
        ):
            asyncio.run(sess._do_clipboard_image("r1"))
        self.assertTrue(_reply_of(emitted).get("unavailable"))

    def test_reader_exception_does_not_escape(self) -> None:
        sess, emitted = _session()
        with mock.patch(
            "src.utils.image_paste.get_image_from_clipboard",
            side_effect=RuntimeError("boom"),
        ), mock.patch(
            "src.utils.image_paste.clipboard_tooling_available", return_value=True
        ):
            asyncio.run(sess._do_clipboard_image("r1"))
        self.assertIn("error", _reply_of(emitted))


class TestDetectFileDrop(unittest.TestCase):
    def _tmpfile(self, name: str, data: bytes) -> Path:
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        p = d / name
        p.write_bytes(data)
        return p

    def test_dropped_image_is_attached(self) -> None:
        sess, emitted = _session()
        p = self._tmpfile("shot.png", _png())
        asyncio.run(sess._do_detect_file_drop("r1", str(p)))
        reply = _reply_of(emitted)
        self.assertTrue(reply["matched"])
        self.assertTrue(reply["is_image"])
        self.assertEqual(reply["name"], "shot.png")
        self.assertEqual(reply["text"], "", "an attached image inserts no text")
        self.assertEqual(len(sess._pending_images), 1)

    def test_dropped_non_image_becomes_an_at_reference(self) -> None:
        sess, emitted = _session()
        p = self._tmpfile("data.csv", b"a,b\n1,2\n")
        asyncio.run(sess._do_detect_file_drop("r1", str(p)))
        reply = _reply_of(emitted)
        self.assertTrue(reply["matched"])
        self.assertFalse(reply["is_image"])
        self.assertEqual(reply["text"], f"@{p}")
        self.assertEqual(sess._pending_images, [])

    def test_prose_does_not_match(self) -> None:
        sess, emitted = _session()
        asyncio.run(sess._do_detect_file_drop("r1", "please look at\nthis thing"))
        self.assertEqual(_reply_of(emitted), {"matched": False})

    def test_nonexistent_path_does_not_match(self) -> None:
        sess, emitted = _session()
        asyncio.run(sess._do_detect_file_drop("r1", "/nonexistent/whatever.csv"))
        self.assertEqual(_reply_of(emitted), {"matched": False})

    def test_unreadable_image_falls_through_to_file_handling(self) -> None:
        """A .png that isn't decodable is still a real file on disk."""
        sess, emitted = _session()
        p = self._tmpfile("liar.png", b"not an image")
        asyncio.run(sess._do_detect_file_drop("r1", str(p)))
        reply = _reply_of(emitted)
        self.assertTrue(reply["matched"])
        self.assertFalse(reply["is_image"])
        self.assertEqual(sess._pending_images, [])


class TestPendingImagesLifecycle(unittest.TestCase):
    def test_user_message_drains_the_queue(self) -> None:
        """The whole point: an attached image reaches the model with the prompt."""

        sess, _ = _session()
        sess._pending_images.append(_fake_image())
        put: list = []
        sess._inbox = mock.Mock(put=put.append)

        asyncio.run(
            sess.send_to_agent({"type": "user", "message": {"role": "user", "content": "hi"}})
        )

        self.assertEqual(len(put), 1)
        content = put[0]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(sess._pending_images, [])

    def test_text_only_turn_still_puts_a_string(self) -> None:

        sess, _ = _session()
        put: list = []
        sess._inbox = mock.Mock(put=put.append)

        asyncio.run(
            sess.send_to_agent({"type": "user", "message": {"role": "user", "content": "hi"}})
        )

        self.assertEqual(put, ["hi"])

    def test_ephemeral_turn_does_not_consume_the_image(self) -> None:
        """A "btw" message must leave the queue alone.

        ``_run_turn(btw=True)`` restores a pre-turn snapshot, so draining into an
        ephemeral message would consume the image and then discard it. The
        earlier version of this test asserted the opposite and pinned the bug.
        """
        sess, _ = _session()
        sess._pending_images.append(_fake_image())
        put: list = []
        sess._inbox = mock.Mock(put=put.append)

        asyncio.run(
            sess.send_to_agent({
                "type": "user",
                "ephemeral": True,
                "message": {"role": "user", "content": "btw"},
            })
        )

        self.assertTrue(put[0]["__btw__"])
        self.assertEqual(put[0]["content"], "btw", "no image blocks on an ephemeral turn")
        self.assertEqual(len(sess._pending_images), 1, "image must survive for the real turn")

    def test_ephemeral_envelope_shape_preserved(self) -> None:
        """The __btw__ wrapper still applies with nothing queued."""
        sess, _ = _session()
        put: list = []
        sess._inbox = mock.Mock(put=put.append)

        asyncio.run(
            sess.send_to_agent({
                "type": "user",
                "ephemeral": True,
                "message": {"role": "user", "content": "btw"},
            })
        )

        self.assertEqual(put[0], {"__btw__": True, "content": "btw"})



class TestClearDropsPendingImages(unittest.TestCase):
    """An unsent image belongs to the conversation the user just discarded.

    Carrying it across /clear would silently attach it to an unrelated prompt in
    a fresh context.
    """

    def test_clear_empties_the_queue(self) -> None:

        sess, emitted = _session()
        sess._pending_images.append(_fake_image())

        asyncio.run(
            sess._handle_control_request({"request_id": "c1", "request": {"subtype": "clear"}})
        )

        self.assertEqual(sess._pending_images, [])

    def test_clear_refused_mid_turn_keeps_the_queue(self) -> None:
        """A refused clear must not have side effects."""

        sess, emitted = _session()
        sess._pending_images.append(_fake_image())
        sess._current_abort = object()  # an active turn

        asyncio.run(
            sess._handle_control_request({"request_id": "c1", "request": {"subtype": "clear"}})
        )

        self.assertFalse(_reply_of(emitted).get("ok"))
        self.assertEqual(len(sess._pending_images), 1)


class TestBlockOrderConsumers(unittest.TestCase):
    """The user's text must be the FIRST text block.

    Three consumers flatten content by concatenating text blocks with no
    separator and then read the front of the result, so leading metadata broke
    all three: ``_parse_turn_budget`` (``^``-anchored, so ``+500k`` no-ops),
    ``_first_prompt_preview`` (/resume + branch names), and UserPromptSubmit
    hooks (``^``-anchored matchers).
    """

    def test_metadata_trails_the_user_text(self) -> None:
        sess, _ = _session()
        sess._pending_images.append(_fake_image(resized=True, source="/tmp/shot.png"))
        out = sess._drain_pending_images("+500k refactor this")
        texts = [b["text"] for b in out if b["type"] == "text"]
        self.assertEqual(texts[0], "+500k refactor this")
        self.assertTrue(any("Multiply coordinates by" in t for t in texts[1:]))
        self.assertEqual(out[0]["type"], "image", "images still lead")

    def test_flattened_text_starts_with_the_prompt(self) -> None:
        from src.server.agent_server import _extract_prompt_text

        sess, _ = _session()
        sess._pending_images.append(_fake_image(resized=True, source="/tmp/shot.png"))
        out = sess._drain_pending_images("+500k do the thing")
        flat = _extract_prompt_text({"message": {"role": "user", "content": out}})
        self.assertTrue(
            flat.startswith("+500k do the thing"),
            f"a ^-anchored consumer would miss: {flat[:60]!r}",
        )

    def test_first_prompt_preview_shows_the_prompt(self) -> None:
        from src.server.agent_server import _first_prompt_preview
        from src.types.messages import create_user_message

        sess, _ = _session()
        sess._pending_images.append(_fake_image(resized=True, source="/tmp/shot.png"))
        out = sess._drain_pending_images("what is in this screenshot?")
        preview = _first_prompt_preview([create_user_message(out)])
        self.assertIsNotNone(preview)
        self.assertNotIn("[Image", preview)
        self.assertIn("screenshot", preview)


class TestPendingImageCap(unittest.TestCase):
    """Unbounded queueing plus a destructive drain loses every image at once."""

    def test_cap_refuses_the_overflow_attach(self) -> None:
        sess, emitted = _session()
        for _ in range(sess.MAX_PENDING_IMAGES):
            self.assertTrue(sess._queue_image(_fake_image()))
        self.assertFalse(sess._queue_image(_fake_image()))
        self.assertEqual(len(sess._pending_images), sess.MAX_PENDING_IMAGES)

    def test_overflow_reports_an_actionable_error(self) -> None:
        sess, emitted = _session()
        for _ in range(sess.MAX_PENDING_IMAGES):
            sess._queue_image(_fake_image())
        sess._attach_image("r1", _fake_image())
        error = _reply_of(emitted).get("error", "")
        self.assertIn("/clear", error)
        self.assertNotIn("name", _reply_of(emitted))

    def test_dropped_path_route_is_also_capped(self) -> None:
        """Every producer goes through _queue_image, not an inline append."""
        import shutil
        import tempfile

        sess, emitted = _session()
        for _ in range(sess.MAX_PENDING_IMAGES):
            sess._queue_image(_fake_image())
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        p = d / "extra.png"
        p.write_bytes(_png())
        asyncio.run(sess._do_detect_file_drop("r1", str(p)))
        self.assertEqual(len(sess._pending_images), sess.MAX_PENDING_IMAGES)
        self.assertIn("error", _reply_of(emitted))


if __name__ == "__main__":
    unittest.main()
