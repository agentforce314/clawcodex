"""Transport-level failures must classify as retryable.

Regression for a live incident (claude-opus-5, interactive TUI): a turn died
with the Anthropic SDK's raw ``APITimeoutError`` text
("Request timed out or interrupted...") and every follow-up prompt did the same
until the network settled, because nothing retried it.

Two layers were responsible:

1. SDK auto-retry -- disabled on foreground turns (``sdk_max_retries=0``,
   ``query.py``) so the manual lane can own retry accounting.
2. ``categorize_retryable_api_error`` -- tested ``isinstance`` against the
   *builtin* ``ConnectionError``/``TimeoutError``/``OSError``, which no SDK
   transport error subclasses, so everything fell through to
   ``retryable=False, error_type="unknown"``. THIS was the defect.

Net effect: a foreground turn had strictly FEWER attempts than a stock SDK
client would give it.

A third layer, ``is_transient_stream_drop``, also declines timeouts -- and that
is CORRECT, not part of the bug. It answers the narrower question "did a stream
die mid-response?", and it deliberately owns neither the contract nor the budget
for timeouts; see the lane-ownership note in ``src/providers/stream_retry.py``
and ``TestStreamRetryLaneOwnership`` below before changing it. Admitting
timeouts there multiplies the two retry budgets (3 x 11 = 33 wire attempts).
"""

from __future__ import annotations

import unittest

import httpx
from anthropic import (
    APIConnectionError as SDKAPIConnectionError,
    APITimeoutError as SDKAPITimeoutError,
    BadRequestError,
)

from src.providers.stream_retry import is_transient_stream_drop
from src.services.api.errors import (
    categorize_retryable_api_error,
    is_transport_error,
)


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class TestSDKTransportErrorsAreRetryable(unittest.TestCase):
    """The exact exception the user saw, plus its siblings."""

    def test_sdk_api_timeout_error_is_retryable(self) -> None:
        err = SDKAPITimeoutError(request=_request())
        # Pin the real SDK message so a reworded SDK release is visible here.
        self.assertIn("Request timed out or interrupted", str(err))
        result = categorize_retryable_api_error(err)
        self.assertTrue(result.retryable)
        self.assertEqual(result.error_type, "connection_error")

    def test_sdk_api_connection_error_is_retryable(self) -> None:
        result = categorize_retryable_api_error(
            SDKAPIConnectionError(request=_request())
        )
        self.assertTrue(result.retryable)
        self.assertEqual(result.error_type, "connection_error")

    def test_httpx_timeout_family_is_retryable(self) -> None:
        for exc in (
            httpx.ConnectTimeout("timed out"),
            httpx.ReadTimeout("timed out"),
            httpx.WriteTimeout("timed out"),
            httpx.PoolTimeout("timed out"),
            httpx.ConnectError("refused"),
            httpx.ReadError("reset"),
            httpx.RemoteProtocolError("peer closed connection"),
        ):
            with self.subTest(exc=type(exc).__name__):
                result = categorize_retryable_api_error(exc)
                self.assertTrue(result.retryable)
                self.assertEqual(result.error_type, "connection_error")

    def test_builtin_transport_errors_still_retryable(self) -> None:
        """The original isinstance branch's coverage must not regress."""
        for exc in (
            ConnectionError("reset"),
            TimeoutError("timed out"),
            OSError("network unreachable"),
        ):
            with self.subTest(exc=type(exc).__name__):
                self.assertTrue(categorize_retryable_api_error(exc).retryable)


class TestStatusBearingErrorsStayUnretried(unittest.TestCase):
    """A server verdict is not a transport failure."""

    def test_bad_request_is_not_transport(self) -> None:
        err = BadRequestError(
            "invalid",
            response=httpx.Response(400, request=_request()),
            body=None,
        )
        self.assertFalse(is_transport_error(err))
        self.assertFalse(categorize_retryable_api_error(err).retryable)

    def test_status_attribute_wins_over_type_name(self) -> None:
        """A status-bearing error must not be laundered into a retry.

        Guards the widened MRO walk: an exception that merely *shares* a listed
        class name but carries an HTTP status is a server verdict.
        """

        class APITimeoutError(Exception):  # noqa: N818 — deliberate name clash
            status_code = 400

        self.assertFalse(is_transport_error(APITimeoutError("nope")))

    def test_client_side_protocol_errors_stay_unretried(self) -> None:
        """Deterministic client-side faults must never earn a retry.

        ``httpx.LocalProtocolError`` means *this client* emitted something
        illegal (malformed header, bad method/body combination). Retrying
        reproduces it. It subclasses ``httpx.ProtocolError``, so listing that
        base name in the transport set would launder it into 10 pointless
        attempts -- which is why only ``RemoteProtocolError`` is listed.
        """
        for exc in (
            httpx.LocalProtocolError("illegal header"),
            httpx.ProxyError("bad proxy"),
            httpx.DecodingError("bad gzip"),
            httpx.UnsupportedProtocol("gopher://"),
            httpx.TooManyRedirects("loop"),
            httpx.InvalidURL("nope"),
        ):
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(
                    is_transport_error(exc),
                    f"{type(exc).__name__} must not be classified as transport",
                )
                self.assertFalse(categorize_retryable_api_error(exc).retryable)

    def test_unknown_error_still_not_retryable(self) -> None:
        result = categorize_retryable_api_error(ValueError("something else"))
        self.assertFalse(result.retryable)
        self.assertEqual(result.error_type, "unknown")

    def test_prompt_too_long_precedence_over_transport(self) -> None:
        """Classification order matters: a PTL never becomes a retry."""
        err = SDKAPITimeoutError(request=_request())
        err.args = ("prompt is too long: 300000 tokens > 200000",)
        self.assertFalse(categorize_retryable_api_error(err).retryable)


class TestStreamRetryLaneOwnership(unittest.TestCase):
    """Layer 2 deliberately does NOT own timeouts; the query lane does.

    Admitting them here would multiply the two budgets
    (``stream_idle_max_attempts`` x ``DEFAULT_MAX_RETRIES`` = 33 wire attempts),
    turning a dead network into ~13 minutes of silent waiting instead of one
    bounded, user-visible retry series. It would also break this predicate's own
    contract: a connect timeout never established a stream, so it is not a
    "mid-stream drop".
    """

    def test_timeouts_are_not_treated_as_mid_stream_drops(self) -> None:
        for exc in (
            SDKAPITimeoutError(request=_request()),
            httpx.ConnectTimeout("timed out"),
            httpx.ReadTimeout("timed out"),
        ):
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(is_transient_stream_drop(exc))
                # ...but the query lane must still retry it.
                self.assertTrue(categorize_retryable_api_error(exc).retryable)

    def test_api_connection_error_still_matches(self) -> None:
        self.assertTrue(
            is_transient_stream_drop(SDKAPIConnectionError(request=_request()))
        )

    def test_status_bearing_error_still_rejected(self) -> None:
        err = BadRequestError(
            "invalid",
            response=httpx.Response(400, request=_request()),
            body=None,
        )
        self.assertFalse(is_transient_stream_drop(err))


class TestAnthropicClientTimeout(unittest.TestCase):
    """The streaming lane previously inherited the SDK's connect=5.0."""

    def test_connect_phase_is_widened(self) -> None:
        from src.providers.anthropic_provider import (
            DEFAULT_API_CONNECT_TIMEOUT_S,
            _client_timeout,
        )

        timeout = _client_timeout()
        self.assertIsNotNone(timeout)
        self.assertEqual(timeout.connect, DEFAULT_API_CONNECT_TIMEOUT_S)
        self.assertGreater(timeout.connect, 5.0)
        self.assertEqual(timeout.read, 600.0)

    def test_env_overrides(self) -> None:
        import os
        from unittest import mock

        from src.providers.anthropic_provider import _client_timeout

        with mock.patch.dict(
            os.environ,
            {"API_CONNECT_TIMEOUT_MS": "25000", "API_TIMEOUT_MS": "120000"},
        ):
            timeout = _client_timeout()
        self.assertEqual(timeout.connect, 25.0)
        self.assertEqual(timeout.read, 120.0)

    def test_client_is_constructed_with_the_timeout(self) -> None:
        from unittest import mock

        from src.providers.anthropic_provider import (
            DEFAULT_API_CONNECT_TIMEOUT_S,
            AnthropicProvider,
        )

        provider = AnthropicProvider(api_key="sk-test")
        with mock.patch(
            "src.providers.anthropic_provider.anthropic.Anthropic"
        ) as ctor:
            provider._ensure_client()
        passed = ctor.call_args.kwargs
        self.assertIn("timeout", passed)
        self.assertEqual(passed["timeout"].connect, DEFAULT_API_CONNECT_TIMEOUT_S)

    def test_minimax_shares_the_timeout(self) -> None:
        """Minimax speaks the same wire through the same SDK and lane."""
        from unittest import mock

        from src.providers.anthropic_provider import DEFAULT_API_CONNECT_TIMEOUT_S
        from src.providers.minimax_provider import MinimaxProvider

        provider = MinimaxProvider(api_key="mm-test")
        with mock.patch("src.providers.minimax_provider.anthropic.Anthropic") as ctor:
            provider._ensure_client()
        self.assertEqual(
            ctor.call_args.kwargs["timeout"].connect, DEFAULT_API_CONNECT_TIMEOUT_S
        )

    def test_non_streaming_path_does_not_collapse_connect(self) -> None:
        """The effective per-request timeout, which is where the bug lived.

        ``chat()`` sets a per-request timeout so the SDK will accept a
        non-streaming call at opus-class ``max_tokens``. Passing a bare float
        there made httpx expand it across all four phases, silently resetting
        ``connect`` from 15s back to 600s -- so a black-holed SYN on a
        compaction summarize hung for ten minutes.
        """
        from src.providers.anthropic_provider import (
            DEFAULT_API_CONNECT_TIMEOUT_S,
            _api_timeout_seconds,
            _client_timeout,
        )

        # A bare float — what the old code passed — flattens every phase, so
        # connect inherits the full 600s read budget.
        self.assertEqual(httpx.Timeout(_api_timeout_seconds()).connect, 600.0)
        # The phase-split object keeps connect distinct from read.
        split = _client_timeout()
        self.assertEqual(split.connect, DEFAULT_API_CONNECT_TIMEOUT_S)
        self.assertEqual(split.read, 600.0)
        self.assertNotEqual(split.connect, split.read)

    def test_chat_passes_the_phase_split_timeout(self) -> None:
        """``chat()`` must hand the SDK an httpx.Timeout, never a float."""
        from unittest import mock

        from src.providers.anthropic_provider import (
            DEFAULT_API_CONNECT_TIMEOUT_S,
            AnthropicProvider,
        )

        provider = AnthropicProvider(api_key="sk-test")
        fake_client = mock.MagicMock()
        fake_client.messages.create.return_value = mock.MagicMock(
            content=[], usage=None, stop_reason="end_turn"
        )
        with mock.patch.object(provider, "_client_for_request", return_value=fake_client):
            provider.chat([{"role": "user", "content": "hi"}])
        timeout = fake_client.messages.create.call_args.kwargs["timeout"]
        self.assertIsInstance(timeout, httpx.Timeout)
        self.assertEqual(timeout.connect, DEFAULT_API_CONNECT_TIMEOUT_S)
        self.assertEqual(timeout.read, 600.0)


if __name__ == "__main__":
    unittest.main()
