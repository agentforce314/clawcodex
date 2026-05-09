"""Phase-9 / WI-9.2 — lazy JSON serialization tests.

The hook event emission stream wraps each event in a
``LazyJsonPayload`` (subclass of dict) with a memoized ``json``
property. Two performance properties:

  1. Zero-subscriber and dict-only-consumer scenarios pay zero
     serialization cost.
  2. When N subscribers each access ``payload.json``, serialization
     happens exactly once (memoized).

Tests inspect the serialization side-channel by patching
``json.dumps`` to count calls — concrete proof the contract holds.
"""

from __future__ import annotations

import json as stdlib_json
from unittest.mock import patch

import pytest

from src.hooks.events import (
    LazyJsonPayload,
    clear_hook_event_state,
    emit_hook_started,
    register_hook_event_handler,
)


@pytest.fixture(autouse=True)
def _isolate_event_stream():
    clear_hook_event_state()
    yield
    clear_hook_event_state()


class TestLazyJsonPayloadDirect:
    def test_acts_as_dict_for_field_access(self):
        # Back-compat: subscribers that read fields directly continue
        # to work — LazyJsonPayload IS a dict.
        payload = LazyJsonPayload({"type": "hook_started", "event": "PreToolUse"})
        assert payload["type"] == "hook_started"
        assert payload.get("event") == "PreToolUse"
        assert "type" in payload

    def test_json_property_returns_serialized_string(self):
        payload = LazyJsonPayload({"a": 1, "b": "two"})
        result = payload.json
        # Re-parse to ensure it's valid JSON with the same content.
        assert stdlib_json.loads(result) == {"a": 1, "b": "two"}

    def test_zero_serialization_when_json_never_accessed(self):
        # The headline property: dict-only consumers pay zero cost.
        payload = LazyJsonPayload({"type": "x"})
        with patch("src.hooks.events.json.dumps") as mock_dumps:
            # Read field directly (the dict path).
            _ = payload["type"]
            _ = payload.get("missing", "default")
            for k in payload:
                pass
        # json.dumps was never called.
        assert mock_dumps.call_count == 0

    def test_json_property_memoizes_across_n_accesses(self):
        # Multiple accesses → one serialization. (Ten accesses arbitrary
        # — point is "many"; one serialization regardless.)
        payload = LazyJsonPayload({"a": 1})
        with patch(
            "src.hooks.events.json.dumps", wraps=stdlib_json.dumps,
        ) as mock_dumps:
            for _ in range(10):
                _ = payload.json
        assert mock_dumps.call_count == 1

    def test_concurrent_json_access_serializes_once(self):
        # Thread-safety: two threads both reading payload.json see
        # exactly one serialization between them.
        import threading

        payload = LazyJsonPayload({"a": 1})

        results: list[str] = []
        barrier = threading.Barrier(5)

        def reader():
            barrier.wait()  # all threads start together
            results.append(payload.json)

        with patch(
            "src.hooks.events.json.dumps", wraps=stdlib_json.dumps,
        ) as mock_dumps:
            threads = [threading.Thread(target=reader) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # Exactly one serialization across all 5 readers.
        assert mock_dumps.call_count == 1
        # All 5 got the same string.
        assert len(set(results)) == 1


class TestLazyJsonInDispatchedEvents:
    def test_dispatched_event_is_lazy_payload(self):
        # When emit_* fires, subscribers receive LazyJsonPayload (NOT
        # a plain dict). Subscribers can opt into JSON via .json or
        # treat it as a dict for free.
        captured: list = []
        register_hook_event_handler(captured.append)

        emit_hook_started(hook_id="x", event="PreToolUse", command="echo")

        assert len(captured) == 1
        assert isinstance(captured[0], LazyJsonPayload)
        # Field access works.
        assert captured[0]["type"] == "hook_started"

    def test_subscribers_share_one_serialization(self):
        # 3 subscribers, all of which call payload.json. Total
        # serialization count: 1.
        sub_results: list[str] = []

        def sub(payload):
            sub_results.append(payload.json)

        for _ in range(3):
            register_hook_event_handler(sub)

        with patch(
            "src.hooks.events.json.dumps", wraps=stdlib_json.dumps,
        ) as mock_dumps:
            emit_hook_started(hook_id="x", event="PreToolUse")

        # One emit → one event payload → one serialization.
        assert mock_dumps.call_count == 1
        # All 3 subscribers got the same string.
        assert len(sub_results) == 3
        assert len(set(sub_results)) == 1

    def test_zero_subscribers_zero_serialization(self):
        # No subscribers registered → emit fires (no-op) → no
        # serialization, ever.
        with patch("src.hooks.events.json.dumps") as mock_dumps:
            emit_hook_started(hook_id="x", event="PreToolUse")
        assert mock_dumps.call_count == 0

    def test_dict_only_subscriber_zero_serialization(self):
        # Subscriber that only reads dict fields (the common case)
        # pays zero serialization cost even when registered.
        captured: list = []

        def dict_only_sub(payload):
            # Only dict access; never .json.
            captured.append(payload["type"])

        register_hook_event_handler(dict_only_sub)
        with patch("src.hooks.events.json.dumps") as mock_dumps:
            emit_hook_started(hook_id="x", event="PreToolUse")
        assert mock_dumps.call_count == 0
        assert captured == ["hook_started"]
