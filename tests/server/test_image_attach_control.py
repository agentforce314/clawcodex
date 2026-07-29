"""agent-server image attachment: attach_image / clipboard_image / detect_file_drop.

The client holds no image bytes — it renders an ``[Image #N]`` chip — so the
server's pending list is the single source of truth. Two behaviours matter: the
drain onto the next prompt, and the chip being AUTHORITATIVE (deleting it
un-attaches the image).
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


def _queue(sess, *images, placeholder: bool = False) -> list[int]:
    """Queue via the real entry point so ids and the cap always apply."""
    return [sess._queue_image(i, expects_placeholder=placeholder) for i in images]


def _reply_of(emitted: list) -> dict:
    return emitted[-1]["response"]["response"]


def _fake_image(
    *, resized: bool = False, source: str | None = None, data: str = "aGVsbG8="
) -> PastedImage:
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
        base64=data, media_type="image/png", dimensions=dims, source_path=source
    )


class TestDrainPendingImages(unittest.TestCase):
    def test_no_pending_leaves_a_plain_string_alone(self) -> None:
        """The text-only path must not be turned into a block list."""
        sess, _ = _session()
        self.assertEqual(sess._drain_pending_images("hello"), "hello")

    def test_image_precedes_the_text(self) -> None:
        sess, _ = _session()
        _queue(sess, _fake_image())
        out = sess._drain_pending_images("what is this?")
        self.assertIsInstance(out, list)
        self.assertEqual(out[0]["type"], "image")
        self.assertEqual(out[0]["source"]["media_type"], "image/png")
        self.assertEqual(out[0]["source"]["data"], "aGVsbG8=")
        self.assertEqual(out[-1], {"type": "text", "text": "what is this?"})

    def test_resized_image_carries_scale_metadata(self) -> None:
        """A downsampled screenshot needs the scale factor or coordinates lie."""
        sess, _ = _session()
        _queue(sess, _fake_image(resized=True))
        out = sess._drain_pending_images("click the button")
        texts = [b["text"] for b in out if b["type"] == "text"]
        self.assertTrue(
            any("Multiply coordinates by" in t for t in texts),
            f"expected coordinate-mapping metadata, got {texts}",
        )

    def test_unresized_clipboard_image_has_no_metadata_noise(self) -> None:
        sess, _ = _session()
        _queue(sess, _fake_image(resized=False))
        out = sess._drain_pending_images("hi")
        self.assertEqual([b["type"] for b in out], ["image", "text"])

    def test_drain_is_destructive(self) -> None:
        """A resend must not duplicate the image."""
        sess, _ = _session()
        _queue(sess, _fake_image())
        first = sess._drain_pending_images("a")
        self.assertIsInstance(first, list)
        self.assertEqual(sess._pending_images, [])
        self.assertEqual(sess._drain_pending_images("b"), "b")

    def test_empty_text_yields_no_empty_text_block(self) -> None:
        sess, _ = _session()
        _queue(sess, _fake_image())
        out = sess._drain_pending_images("")
        self.assertEqual([b["type"] for b in out], ["image"])

    def test_existing_blocks_are_preserved(self) -> None:
        sess, _ = _session()
        _queue(sess, _fake_image())
        existing = [{"type": "text", "text": "hi"}]
        out = sess._drain_pending_images(existing)
        self.assertEqual(out[0]["type"], "image")
        self.assertIn({"type": "text", "text": "hi"}, out)

    def test_multiple_images_all_ride_along(self) -> None:
        sess, _ = _session()
        _queue(sess, _fake_image(), _fake_image())
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
        _queue(sess, _fake_image())
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
        _queue(sess, _fake_image())
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
        _queue(sess, _fake_image())

        asyncio.run(
            sess._handle_control_request({"request_id": "c1", "request": {"subtype": "clear"}})
        )

        self.assertEqual(sess._pending_images, [])

    def test_clear_refused_mid_turn_keeps_the_queue(self) -> None:
        """A refused clear must not have side effects."""

        sess, emitted = _session()
        _queue(sess, _fake_image())
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
        _queue(sess, _fake_image(resized=True, source="/tmp/shot.png"))
        out = sess._drain_pending_images("+500k refactor this")
        texts = [b["text"] for b in out if b["type"] == "text"]
        self.assertEqual(texts[0], "+500k refactor this")
        self.assertTrue(any("Multiply coordinates by" in t for t in texts[1:]))
        self.assertEqual(out[0]["type"], "image", "images still lead")

    def test_flattened_text_starts_with_the_prompt(self) -> None:
        from src.server.agent_server import _extract_prompt_text

        sess, _ = _session()
        _queue(sess, _fake_image(resized=True, source="/tmp/shot.png"))
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
        _queue(sess, _fake_image(resized=True, source="/tmp/shot.png"))
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


class TestImageRefParsing(unittest.TestCase):
    def test_finds_every_chip(self) -> None:
        from src.server.agent_server import _parse_image_refs

        self.assertEqual(
            _parse_image_refs("a [Image #1] b [Image #12] c"), {1, 12}
        )

    def test_id_zero_is_never_real(self) -> None:
        from src.server.agent_server import _parse_image_refs

        self.assertEqual(_parse_image_refs("[Image #0]"), set())

    def test_no_chips_is_empty(self) -> None:
        from src.server.agent_server import _parse_image_refs

        self.assertEqual(_parse_image_refs("just a prompt"), set())
        self.assertEqual(_parse_image_refs("[Image] [Image #] [Pasted text #1]"), set())

    def test_scans_text_blocks_of_a_content_list(self) -> None:
        from src.server.agent_server import _content_text

        blocks = [
            {"type": "image", "source": {"data": "x"}},
            {"type": "text", "text": "see [Image #4]"},
        ]
        self.assertIn("[Image #4]", _content_text(blocks))


class TestChipIsAuthoritative(unittest.TestCase):
    """The `[Image #N]` chip doubles as un-attach.

    Deleting it from the composer must drop the image, mirroring the reference
    ("Images are only sent if their [Image #N] placeholder is still in the
    text", handlePromptSubmit.ts:225).
    """

    def test_ids_increment_across_the_session(self) -> None:
        sess, _ = _session()
        self.assertEqual(_queue(sess, _fake_image(), _fake_image(), _fake_image()), [1, 2, 3])

    def test_kept_chip_sends_its_image(self) -> None:
        sess, _ = _session()
        _queue(sess, _fake_image(), placeholder=True)
        out = sess._drain_pending_images("what is this? [Image #1]")
        self.assertEqual(sum(1 for b in out if b["type"] == "image"), 1)

    def test_deleted_chip_drops_its_image(self) -> None:
        sess, _ = _session()
        _queue(sess, _fake_image(), placeholder=True)
        self.assertEqual(
            sess._drain_pending_images("changed my mind"), "changed my mind"
        )

    def test_only_the_deleted_one_is_dropped(self) -> None:
        sess, _ = _session()
        # Distinguishable bytes, so this can show WHICH image survived.
        _queue(
            sess,
            _fake_image(data="Zmlyc3Q="),   # "first"
            _fake_image(data="c2Vjb25k"),   # "second"
            placeholder=True,
        )
        out = sess._drain_pending_images("keeping [Image #2] only")
        images = [b for b in out if b["type"] == "image"]
        self.assertEqual(len(images), 1)
        self.assertEqual(
            images[0]["source"]["data"], "c2Vjb25k", "kept the wrong image"
        )

    def test_chip_text_stays_in_the_prompt(self) -> None:
        """The reference leaves image refs inline (history.ts:79)."""
        sess, _ = _session()
        _queue(sess, _fake_image(), placeholder=True)
        out = sess._drain_pending_images("look at [Image #1]")
        texts = [b["text"] for b in out if b["type"] == "text"]
        self.assertIn("look at [Image #1]", texts)

    def test_queue_is_drained_even_when_everything_was_unattached(self) -> None:
        """A dropped image must not linger and land on the NEXT prompt."""
        sess, _ = _session()
        _queue(sess, _fake_image(), placeholder=True)
        sess._drain_pending_images("no chip")
        self.assertEqual(sess._pending_images, [])

    def test_client_without_chips_is_unaffected(self) -> None:
        """headless -p and the VS Code bridge render no chip, so never filter."""
        sess, _ = _session()
        _queue(sess, _fake_image(), placeholder=False)
        out = sess._drain_pending_images("no chip anywhere")
        self.assertEqual(sum(1 for b in out if b["type"] == "image"), 1)

    def test_reply_carries_the_id_under_both_names(self) -> None:
        """ImageAttachResponse readers want `id`; ClipboardPasteResponse `count`."""
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        p = d / "a.png"
        p.write_bytes(_png())
        sess, emitted = _session()
        asyncio.run(sess._do_attach_image("r1", str(p), expects_placeholder=True))
        reply = _reply_of(emitted)
        self.assertEqual(reply["id"], 1)
        self.assertEqual(reply["count"], 1)
        self.assertTrue(reply["attached"])


class TestClipboardRouteHonorsPlaceholder(unittest.TestCase):
    """The clipboard route is Ctrl+V *and* Cmd+V — the whole point of the chip.

    ``_do_clipboard_image`` accepted ``expects_placeholder`` and dropped it on the
    floor, so the chip was decoration there: deleting it did not un-attach. 47
    green tests missed it because none drove this handler with the flag set.
    """

    def _clipboard(self, sess, *, flag: bool) -> None:
        with mock.patch(
            "src.utils.image_paste.get_image_from_clipboard", return_value=_fake_image()
        ), mock.patch(
            "src.utils.image_paste.clipboard_tooling_available", return_value=True
        ):
            asyncio.run(sess._do_clipboard_image("r1", expects_placeholder=flag))

    def test_flag_reaches_the_queue(self) -> None:
        sess, _ = _session()
        self._clipboard(sess, flag=True)
        self.assertEqual([p for _, _, p in sess._pending_images], [True])

    def test_deleting_the_chip_un_attaches(self) -> None:
        sess, _ = _session()
        self._clipboard(sess, flag=True)
        self.assertEqual(sess._drain_pending_images("never mind"), "never mind")

    def test_keeping_the_chip_sends_it(self) -> None:
        sess, _ = _session()
        self._clipboard(sess, flag=True)
        out = sess._drain_pending_images("what is [Image #1]")
        self.assertEqual(sum(1 for b in out if b["type"] == "image"), 1)

    def test_unflagged_caller_always_sends(self) -> None:
        """A client that renders no chip must not have its image filtered."""
        sess, _ = _session()
        self._clipboard(sess, flag=False)
        out = sess._drain_pending_images("no chip anywhere")
        self.assertEqual(sum(1 for b in out if b["type"] == "image"), 1)

    def test_dropped_path_route_honors_the_flag_too(self) -> None:
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        p = d / "shot.png"
        p.write_bytes(_png())
        sess, _ = _session()
        asyncio.run(sess._do_detect_file_drop("r1", str(p), expects_placeholder=True))
        self.assertEqual([q for _, _, q in sess._pending_images], [True])
        self.assertEqual(sess._drain_pending_images("nope"), "nope")


if __name__ == "__main__":
    unittest.main()
