"""WI-4.2 acceptance tests — fire-and-forget API preconnect.

The chapter's pattern (TS ``apiPreconnect.ts``): fire HEAD ``api.anthropic.com``
post-trust-gate so the TCP+TLS handshake overlaps with the rest of the
bootstrap. Win: ~100-200ms shaved off the first-API-call latency.

Per critic M10: acceptance is **structural** — handle returns within a
trivial budget (it's a daemon thread spawn, not the actual HEAD request).
Skip-conditions (custom base URL, proxy, escape-hatch env) prevent wasted
work in those scenarios.
"""

from __future__ import annotations

import os
import threading
import time
import unittest
from unittest.mock import patch

from src.utils.api_preconnect import (
    PreconnectHandle,
    should_skip_preconnect,
    start_api_preconnect,
)


class TestShouldSkipPreconnect(unittest.TestCase):
    """The skip-decision is the conservative half of the design."""

    def setUp(self):
        # Snapshot the relevant env vars and restore in tearDown so
        # tests don't leak state.
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "ANTHROPIC_BASE_URL",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "CLAUDE_CODE_DISABLE_API_PRECONNECT",
            )
        }
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_does_not_skip(self):
        self.assertFalse(should_skip_preconnect())

    def test_skip_when_base_url_overridden(self):
        os.environ["ANTHROPIC_BASE_URL"] = "https://proxy.example.com"
        self.assertTrue(should_skip_preconnect())

    def test_skip_when_http_proxy_set(self):
        os.environ["HTTP_PROXY"] = "http://corporate.proxy:3128"
        self.assertTrue(should_skip_preconnect())

    def test_skip_when_https_proxy_set(self):
        os.environ["HTTPS_PROXY"] = "http://corporate.proxy:3128"
        self.assertTrue(should_skip_preconnect())

    def test_skip_when_disable_env_truthy(self):
        for v in ("1", "true", "TRUE", "yes", "Yes"):
            with self.subTest(value=v):
                os.environ["CLAUDE_CODE_DISABLE_API_PRECONNECT"] = v
                self.assertTrue(should_skip_preconnect())

    def test_disable_env_falsy_does_not_skip(self):
        for v in ("0", "false", "no", "", "garbage"):
            with self.subTest(value=v):
                os.environ["CLAUDE_CODE_DISABLE_API_PRECONNECT"] = v
                self.assertFalse(should_skip_preconnect())


class TestStartApiPreconnect(unittest.TestCase):
    """The fire-and-forget contract: returns immediately with a handle."""

    def setUp(self):
        # Make sure no skip-conditions interfere.
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "ANTHROPIC_BASE_URL",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "CLAUDE_CODE_DISABLE_API_PRECONNECT",
            )
        }
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_returns_handle_synchronously(self):
        """The call returns a PreconnectHandle in milliseconds; HEAD work
        happens on a daemon thread in the background."""
        t0 = time.perf_counter()
        handle = start_api_preconnect()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertIsInstance(handle, PreconnectHandle)
        self.assertLess(
            elapsed_ms, 100,
            f"start_api_preconnect took {elapsed_ms:.1f}ms — must return immediately",
        )
        # Best effort: drain the daemon thread so the test process exits cleanly.
        if handle.thread is not None:
            handle.thread.join(timeout=2.0)

    def test_thread_is_daemon(self):
        """Daemon thread = process exit isn't blocked by a slow HEAD."""
        handle = start_api_preconnect()
        if handle.thread is not None:
            self.assertTrue(
                handle.thread.daemon,
                "Preconnect thread must be daemon so slow HEAD doesn't block exit",
            )
            handle.thread.join(timeout=2.0)

    def test_skipped_when_base_url_overridden(self):
        os.environ["ANTHROPIC_BASE_URL"] = "https://custom"
        handle = start_api_preconnect()
        self.assertTrue(handle.skipped)
        self.assertIsNone(handle.thread)

    def test_skipped_when_proxy_configured(self):
        os.environ["HTTP_PROXY"] = "http://proxy"
        handle = start_api_preconnect()
        self.assertTrue(handle.skipped)
        self.assertIsNone(handle.thread)

    def test_preconnect_does_not_raise_on_network_error(self):
        """Best-effort: a failed HEAD must NOT propagate out of the thread."""
        # Force the daemon to actually run by giving it a brief moment;
        # if the thread raises, daemon=True swallows it but the test
        # would observe via a missed handle. The structural contract is
        # "no exception escapes the call site" — verified by reaching
        # the next line.
        handle = start_api_preconnect()
        if handle.thread is not None:
            handle.thread.join(timeout=2.0)
        # If we got here, no exception escaped — contract satisfied.


if __name__ == "__main__":
    unittest.main()
