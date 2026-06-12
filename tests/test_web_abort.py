"""#276 — WebFetch / WebSearch honor abort_controller (ESC-cancel).

Covers the ``src.utils.abortable_net`` primitives and their wiring into
the web tools: an abort mid-connect or mid-read must unblock the caller
in ~poll-interval time (not the 15-20s socket timeout) and surface as
``AbortError`` so the dispatch layer renders the user-cancel message.
"""
from __future__ import annotations

import http.server
import threading
import time
import urllib.request

import pytest

from src.utils.abort_controller import AbortController, AbortError
from src.utils.abortable_net import abortable_read, call_with_abort


def _abort_after(controller: AbortController, delay_s: float) -> threading.Thread:
    t = threading.Timer(delay_s, lambda: controller.abort("user_interrupt"))
    t.daemon = True
    t.start()
    return t


class TestCallWithAbort:
    def test_returns_result_without_signal(self):
        assert call_with_abort(lambda: 42, None) == 42

    def test_returns_result_with_untripped_signal(self):
        assert call_with_abort(lambda: "ok", AbortController().signal) == "ok"

    def test_pre_aborted_raises_immediately_without_calling_fn(self):
        controller = AbortController()
        controller.abort("user_interrupt")
        called = []
        with pytest.raises(AbortError):
            call_with_abort(lambda: called.append(1), controller.signal)
        assert called == []

    def test_abort_mid_call_unblocks_fast(self):
        controller = AbortController()
        release = threading.Event()

        def _slow():
            release.wait(10)
            return "late"

        _abort_after(controller, 0.1)
        start = time.monotonic()
        with pytest.raises(AbortError):
            call_with_abort(_slow, controller.signal)
        elapsed = time.monotonic() - start
        release.set()
        assert elapsed < 2.0, f"abort took {elapsed:.2f}s — should be ~0.1s"

    def test_worker_exception_propagates(self):
        with pytest.raises(ValueError, match="boom"):
            call_with_abort(
                lambda: (_ for _ in ()).throw(ValueError("boom")),
                AbortController().signal,
            )

    def test_late_result_after_abort_is_closed(self):
        controller = AbortController()
        closed = threading.Event()

        class _Resource:
            def close(self):
                closed.set()

        release = threading.Event()

        def _slow():
            release.wait(5)
            return _Resource()

        _abort_after(controller, 0.05)
        with pytest.raises(AbortError):
            call_with_abort(_slow, controller.signal)
        release.set()
        assert closed.wait(2.0), "late-arriving resource was not closed"


class _FakeResponse:
    """Chunked reader that blocks until closed after the first chunk."""

    def __init__(self):
        self._sent_first = False
        self._closed = threading.Event()

    def read(self, n: int) -> bytes:
        if not self._sent_first:
            self._sent_first = True
            return b"x" * min(n, 10)
        # Block like a stalled socket until close() unblocks us.
        self._closed.wait(10)
        raise OSError("read on closed connection")

    def close(self):
        self._closed.set()


class TestAbortableRead:
    def test_reads_fully_without_signal(self):
        class _R:
            def read(self, n):
                return b"abc"

        assert abortable_read(_R(), 3, None) == b"abc"

    def test_reads_fully_with_untripped_signal(self):
        chunks = [b"aa", b"bb", b""]

        class _R:
            def read(self, n):
                return chunks.pop(0)

        out = abortable_read(_R(), 1000, AbortController().signal)
        assert out == b"aabb"

    def test_abort_mid_read_closes_resp_and_raises_fast(self):
        controller = AbortController()
        resp = _FakeResponse()
        _abort_after(controller, 0.1)
        start = time.monotonic()
        with pytest.raises(AbortError):
            abortable_read(resp, 1_000_000, controller.signal)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"abort took {elapsed:.2f}s"
        assert resp._closed.is_set()

    def test_listener_removed_on_success(self):
        controller = AbortController()

        class _R:
            def read(self, n):
                return b""

        abortable_read(_R(), 100, controller.signal)
        assert controller.signal._listeners == []


class _StallingHandler(http.server.BaseHTTPRequestHandler):
    """Sends headers then stalls the body — a hung server.

    The stall must exceed every elapsed-time assertion below: a passing
    test proves the abort unblocked the client while the server was
    still stalling (teardown waits out the remainder, keep it short)."""

    stall_s = 3.0

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "1000")
        self.end_headers()
        self.wfile.write(b"partial")
        self.wfile.flush()
        time.sleep(self.stall_s)

    def do_POST(self):  # noqa: N802 — Tavily search POSTs
        self.do_GET()

    def log_message(self, *args):  # silence test output
        pass


@pytest.fixture
def stalling_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _StallingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()


class TestWebFetchAbortIntegration:
    def test_abort_mid_body_unblocks_fast(self, stalling_server):
        from src.tool_system.tools.web_fetch import _fetch_with_redirect_handling

        controller = AbortController()
        _abort_after(controller, 0.2)
        start = time.monotonic()
        with pytest.raises(AbortError):
            _fetch_with_redirect_handling(
                stalling_server, timeout=8, abort_signal=controller.signal
            )
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"abort took {elapsed:.2f}s — stalled to timeout"

    def test_pre_aborted_signal_raises_before_any_io(self):
        from src.tool_system.tools.web_fetch import _fetch_with_redirect_handling

        controller = AbortController()
        controller.abort("user_interrupt")
        with pytest.raises(AbortError):
            # Unroutable TEST-NET address: if this ever attempts I/O the
            # test hangs instead of failing fast — the raise must come first.
            _fetch_with_redirect_handling(
                "http://192.0.2.1/", timeout=1, abort_signal=controller.signal
            )


class TestWebSearchAbortIntegration:
    def test_abort_unblocks_tavily_request(self, stalling_server, monkeypatch):
        from src.tool_system.tools import web_search as ws

        monkeypatch.setattr(ws, "_TAVILY_URL", stalling_server)
        monkeypatch.setattr(ws, "_tavily_api_key", lambda: "tvly-test")

        controller = AbortController()
        _abort_after(controller, 0.2)
        start = time.monotonic()
        with pytest.raises(AbortError):
            ws._tavily_search("query", abort_signal=controller.signal)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"abort took {elapsed:.2f}s"
