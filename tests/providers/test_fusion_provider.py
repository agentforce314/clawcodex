"""FusionProvider — vision substitution, caching, and transparent delegation.

The three invariants from the module docstring are what these lock down:

1. no image block survives the rewrite (a leftover one is the 400 the
   feature exists to prevent),
2. the caller's message list is never mutated, and
3. each distinct image is described exactly once.
"""

from __future__ import annotations

import copy

import pytest

from src.providers.fusion_models import FusionModel, ModelRef
from src.providers.fusion_provider import (
    FusionProvider,
    clear_vision_cache,
)


@pytest.fixture(autouse=True)
def _clean_cache():
    # The description cache is process-wide (so it survives the provider
    # rebuild a /model switch performs), which means it must be reset
    # between tests or call-count assertions leak across them.
    clear_vision_cache()
    yield
    clear_vision_cache()


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content
        self.usage = {"input_tokens": 1, "output_tokens": 1}


class FakeVision:
    """Records every call and returns a canned description."""

    def __init__(self, text: str = "A red square beside a blue one.") -> None:
        self.text = text
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return FakeResponse(self.text)


class ExplodingVision:
    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc or RuntimeError("vision is down")
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        raise self.exc


class FakeInner:
    """Stands in for the base provider; captures what reached the wire."""

    model = "deepseek-v4-pro"
    is_deepseek = True
    base_url = "https://api.deepseek.com"

    def __init__(self) -> None:
        self.seen: list | None = None
        self.seen_kwargs: dict | None = None

    def chat(self, messages, tools=None, **kwargs):
        self.seen, self.seen_kwargs = messages, kwargs
        return FakeResponse("base answered")

    def chat_stream_response(self, messages, tools=None, **kwargs):
        self.seen, self.seen_kwargs = messages, kwargs
        return FakeResponse("base streamed")

    def chat_stream(self, messages, tools=None, **kwargs):
        self.seen = messages
        return iter(["chunk"])

    async def chat_async(self, messages, tools=None, **kwargs):
        self.seen, self.seen_kwargs = messages, kwargs
        return FakeResponse("base async")

    def get_available_models(self):
        return ["deepseek-v4-pro", "deepseek-v4-flash"]


def make_fusion(**kw) -> FusionModel:
    defaults = dict(
        name="deepseek-v4-pro-V",
        base=ModelRef("deepseek", "deepseek-v4-pro"),
        vision=ModelRef("openrouter", "google/gemini-2.5-flash"),
    )
    defaults.update(kw)
    return FusionModel(**defaults)


def image(data: str = "AAAA", media_type: str = "image/png") -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def count_images(node) -> int:
    """Every remaining image block, at any nesting depth."""
    if isinstance(node, dict):
        n = 1 if node.get("type") == "image" else 0
        return n + sum(count_images(v) for v in node.values())
    if isinstance(node, list):
        return sum(count_images(v) for v in node)
    return 0


def build(vision=None, **fusion_kw):
    inner = FakeInner()
    vision = vision if vision is not None else FakeVision()
    provider = FusionProvider(inner, make_fusion(**fusion_kw), vision_provider=vision)
    return provider, inner, vision


# ── invariant 1: no image survives ───────────────────────────────────────


def test_image_in_user_message_is_replaced_with_description():
    provider, inner, vision = build()
    provider.chat([{"role": "user", "content": [{"type": "text", "text": "what?"}, image()]}])

    assert count_images(inner.seen) == 0
    replaced = inner.seen[0]["content"][1]
    assert replaced["type"] == "text"
    assert "A red square beside a blue one." in replaced["text"]
    # The substitution announces itself, so the base model reads it as a
    # description rather than as its own observation.
    assert "openrouter:google/gemini-2.5-flash" in replaced["text"]


def test_image_nested_in_tool_result_is_replaced():
    # Where a Read-returned or Bash-captured image actually lives.
    provider, inner, _ = build()
    provider.chat([{
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": [image(), {"type": "text", "text": "read ok"}],
        }],
    }])

    assert count_images(inner.seen) == 0
    block = inner.seen[0]["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "t1"          # siblings preserved
    assert block["content"][0]["type"] == "text"
    assert block["content"][1] == {"type": "text", "text": "read ok"}


def test_vision_failure_still_removes_the_image():
    # The whole point: a failed vision call must NOT leave an image block,
    # or the base provider 400s and the turn dies.
    provider, inner, vision = build(vision=ExplodingVision())
    provider.chat([{"role": "user", "content": [image()]}])

    assert count_images(inner.seen) == 0
    text = inner.seen[0]["content"][0]["text"]
    assert "could not be described" in text
    assert "vision is down" in text


def test_empty_vision_response_is_treated_as_failure():
    # Observed live from openrouter:z-ai/glm-4.5v — HTTP 200 with empty
    # content. An empty text block would read to the base model as "the
    # image was blank", which is worse than an explicit note.
    provider, inner, _ = build(vision=FakeVision(text="   "))
    provider.chat([{"role": "user", "content": [image()]}])

    assert count_images(inner.seen) == 0
    assert "could not be described" in inner.seen[0]["content"][0]["text"]


def test_image_block_without_payload_is_replaced():
    provider, inner, vision = build()
    provider.chat([{"role": "user", "content": [{"type": "image", "source": {}}]}])

    assert count_images(inner.seen) == 0
    assert "no data" in inner.seen[0]["content"][0]["text"]
    assert vision.calls == []  # nothing to send


def test_over_cap_images_are_replaced_with_a_note_not_left_in_place():
    provider, inner, vision = build(max_images=2)
    provider.chat([{
        "role": "user",
        "content": [image("A"), image("B"), image("C"), image("D")],
    }])

    assert count_images(inner.seen) == 0           # invariant 1 holds at the cap
    assert len(vision.calls) == 2                  # cap respected
    texts = [b["text"] for b in inner.seen[0]["content"]]
    assert "reached its image limit" in texts[2]
    assert "reached its image limit" in texts[3]


# ── invariant 2: no mutation of the caller's messages ────────────────────


def test_caller_messages_are_not_mutated():
    provider, inner, _ = build()
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}, image()]},
        {"role": "assistant", "content": "sure"},
    ]
    snapshot = copy.deepcopy(messages)
    provider.chat(messages)

    # The session owns the conversation: rewriting in place would destroy
    # the originals and break a later switch to a vision-capable model.
    assert messages == snapshot
    assert count_images(messages) == 1


def test_untouched_messages_pass_through_by_reference():
    # Copy-on-write: only rewritten messages are rebuilt.
    provider, inner, _ = build()
    plain = {"role": "assistant", "content": "no images here"}
    provider.chat([{"role": "user", "content": [image()]}, plain])
    assert inner.seen[1] is plain


def test_no_images_returns_the_original_list_object():
    provider, inner, vision = build()
    messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    provider.chat(messages)
    # A fusion session pays nothing on turns without an image.
    assert inner.seen is messages
    assert vision.calls == []


# ── invariant 3: each distinct image described once ──────────────────────


def test_identical_images_share_one_vision_call():
    provider, inner, vision = build()
    same = image("SAME")
    provider.chat([
        {"role": "user", "content": [same]},
        {"role": "user", "content": [same]},
        {"role": "user", "content": [same]},
    ])
    assert len(vision.calls) == 1
    assert count_images(inner.seen) == 0


def test_cache_spans_calls_so_replayed_history_is_not_re_described():
    # The load-bearing case: history is re-sent every turn, so without this
    # an N-turn conversation pays N times for one screenshot.
    provider, inner, vision = build()
    history = [{"role": "user", "content": [image("SCREENSHOT")]}]
    for _ in range(5):
        provider.chat(list(history))
    assert len(vision.calls) == 1


def test_distinct_images_get_distinct_calls():
    provider, inner, vision = build()
    provider.chat([{"role": "user", "content": [image("ONE"), image("TWO")]}])
    assert len(vision.calls) == 2


def test_cache_is_keyed_by_vision_model():
    # Re-pointing a fusion model at a different vision model must not serve
    # descriptions written by the old one.
    v1 = FakeVision(text="described by one")
    p1, inner1, _ = build(vision=v1)
    p1.chat([{"role": "user", "content": [image("X")]}])

    v2 = FakeVision(text="described by two")
    p2 = FusionProvider(
        FakeInner(),
        make_fusion(vision=ModelRef("openrouter", "anthropic/claude-sonnet-4.5")),
        vision_provider=v2,
    )
    p2.chat([{"role": "user", "content": [image("X")]}])
    assert len(v2.calls) == 1


def test_cached_hits_do_not_consume_the_cap():
    # The cap bounds NETWORK fan-out; charging cached descriptions would
    # make the same conversation degrade as it grows.
    provider, inner, vision = build(max_images=1)
    same = image("SAME")
    provider.chat([{"role": "user", "content": [same, same, same, same]}])
    assert len(vision.calls) == 1
    assert count_images(inner.seen) == 0
    # All four became real descriptions, none the over-cap note.
    assert all("image limit" not in b["text"] for b in inner.seen[0]["content"])


# ── delegation ───────────────────────────────────────────────────────────


def test_model_reports_the_base_model_not_the_fusion_name():
    # Everything downstream keys off .model — context window, cost,
    # capability probes, the wire's own model field — and the base model is
    # the right answer for all of them.
    provider, _, _ = build()
    assert provider.model == "deepseek-v4-pro"
    assert provider.fusion_name == "deepseek-v4-pro-V"


def test_model_assignment_reaches_the_inner_provider():
    # Without an explicit property, ``provider.model = x`` would shadow the
    # attribute on the wrapper and the wire would keep the old model.
    provider, inner, _ = build()
    provider.model = "deepseek-v4-flash"
    assert inner.model == "deepseek-v4-flash"
    assert provider.model == "deepseek-v4-flash"


def test_arbitrary_attributes_delegate():
    provider, inner, _ = build()
    assert provider.is_deepseek is True          # gates DeepSeek prefix caching
    assert provider.base_url == "https://api.deepseek.com"
    assert provider.get_available_models() == ["deepseek-v4-pro", "deepseek-v4-flash"]
    assert provider.inner is inner


def test_deepcopy_does_not_recurse_forever():
    # copy.deepcopy builds an instance WITHOUT __init__ then probes for
    # __deepcopy__/__setstate__; without the _inner guard in __getattr__
    # that recurses to RecursionError — and two live call sites deepcopy
    # the session provider while swallowing Exception, so it would be
    # silent.
    provider, _, _ = build()
    assert copy.deepcopy(provider) is not None
    assert copy.copy(provider) is not None


@pytest.mark.parametrize("copier", [copy.copy, copy.deepcopy])
def test_copies_get_an_isolated_model(copier):
    # THE reason both live sites copy at all. agent/run_agent.py does
    #   turn_provider = copy.copy(provider); turn_provider.model = resolved
    # under a comment reading "NEVER mutate the shared session provider …
    # N parallel subagents share params.provider"; yolo_classifier.py does
    # the same. Because ``model`` is a property whose setter writes THROUGH
    # to the inner provider, a copy that SHARES the inner would repoint the
    # main loop's base model — leaving the session firing a foreign model id
    # at the base provider. Both sites swallow Exception, so it was silent.
    provider, inner, _ = build()
    clone = copier(provider)
    clone.model = "SUBAGENT-OVERRIDE"
    assert clone.model == "SUBAGENT-OVERRIDE"
    assert provider.model == "deepseek-v4-pro"
    assert inner.model == "deepseek-v4-pro"


def test_copies_still_substitute():
    # A subagent's copied provider must keep the fusion behaviour.
    provider, _, vision = build()
    clone = copy.copy(provider)
    clone.chat([{"role": "user", "content": [image()]}])
    assert count_images(clone.inner.seen) == 0
    assert len(vision.calls) == 1


def test_missing_delegate_raises_attribute_error_not_recursion():
    provider, _, _ = build()
    with pytest.raises(AttributeError):
        provider.definitely_not_a_real_attribute


@pytest.mark.parametrize("method", ["chat", "chat_stream_response"])
def test_sync_entry_points_substitute(method):
    provider, inner, vision = build()
    getattr(provider, method)([{"role": "user", "content": [image()]}])
    assert count_images(inner.seen) == 0
    assert len(vision.calls) == 1


def test_chat_stream_substitutes():
    provider, inner, vision = build()
    list(provider.chat_stream([{"role": "user", "content": [image()]}]))
    assert count_images(inner.seen) == 0


@pytest.mark.asyncio
async def test_chat_async_substitutes():
    provider, inner, vision = build()
    await provider.chat_async([{"role": "user", "content": [image()]}])
    assert count_images(inner.seen) == 0
    assert len(vision.calls) == 1


def test_messages_passed_by_keyword_are_substituted():
    provider, inner, _ = build()
    provider.chat(messages=[{"role": "user", "content": [image()]}])
    assert count_images(inner.seen) == 0


def test_other_kwargs_are_forwarded_untouched():
    provider, inner, _ = build()
    provider.chat([{"role": "user", "content": "hi"}], max_tokens=99, system="sys")
    assert inner.seen_kwargs["max_tokens"] == 99
    assert inner.seen_kwargs["system"] == "sys"


# ── the vision sub-call's own shape ──────────────────────────────────────


def test_vision_call_sends_the_image_in_anthropic_shape():
    # Handing the image over in Anthropic shape lets each provider's own
    # _prepare_messages translate it, so one call shape covers every
    # vision provider (OpenAI-compatible and Anthropic-wire alike).
    provider, _, vision = build()
    provider.chat([{"role": "user", "content": [image("PAYLOAD")]}])

    sent = vision.calls[0]["messages"][0]["content"]
    assert sent[0]["type"] == "text"
    assert sent[1]["type"] == "image"
    assert sent[1]["source"]["data"] == "PAYLOAD"
    assert vision.calls[0]["kwargs"]["model"] == "google/gemini-2.5-flash"


def test_timeout_is_phase_split_not_a_bare_float():
    # httpx expands a bare float across all four phases, which would lift
    # ``connect`` from 15s to the whole vision window — the hazard
    # anthropic_provider.chat documents ("never a bare float").
    provider, _, vision = build(timeout_ms=45_000)
    provider.chat([{"role": "user", "content": [image()]}])
    timeout = vision.calls[0]["kwargs"]["timeout"]
    assert timeout.read == pytest.approx(45.0)
    assert timeout.connect == pytest.approx(15.0)


def test_custom_prompt_is_appended_to_the_default():
    provider, _, vision = build(prompt="Transcribe tables as CSV.")
    provider.chat([{"role": "user", "content": [image()]}])
    prompt = vision.calls[0]["messages"][0]["content"][0]["text"]
    assert "Transcribe tables as CSV." in prompt
    assert "cannot see it" in prompt   # the default instruction survives


def test_long_description_is_truncated():
    provider, inner, _ = build(vision=FakeVision(text="x" * 10_000))
    provider.chat([{"role": "user", "content": [image()]}])
    text = inner.seen[0]["content"][0]["text"]
    assert "[description truncated]" in text
    assert len(text) < 6_000


def test_typed_non_dict_messages_pass_through():
    # ChatMessage carries ``content: str`` and cannot hold an image block.
    from src.providers.base import ChatMessage

    provider, inner, _ = build()
    msg = ChatMessage(role="user", content="plain text")
    provider.chat([msg])
    assert inner.seen[0] is msg


@pytest.mark.parametrize("messages", [[], None, "not a list"])
def test_degenerate_message_inputs_pass_through(messages):
    provider, inner, vision = build()
    provider.chat(messages)
    assert inner.seen == messages
    assert vision.calls == []


# ── cache scope: prompt is description-determining ────────────────────────


def test_cache_is_keyed_by_prompt_too():
    # Two fusion models sharing a vision model but differing in ``prompt``
    # must not serve each other's descriptions — otherwise FusionModel.prompt
    # is silently inert whenever any record already described that image.
    v1 = FakeVision(text="generic description")
    p1, _, _ = build(vision=v1)
    p1.chat([{"role": "user", "content": [image("SHARED")]}])

    v2 = FakeVision(text="CSV transcription")
    p2 = FusionProvider(FakeInner(), make_fusion(prompt="Transcribe tables as CSV."),
                        vision_provider=v2)
    p2.chat([{"role": "user", "content": [image("SHARED")]}])
    assert len(v2.calls) == 1, "prompt change must not reuse the old description"
    assert "CSV transcription" in p2.inner.seen[0]["content"][0]["text"]


# ── failure caching + abort + wall clock (M2) ────────────────────────────


def test_cached_failure_expires_so_a_recovered_provider_is_retried():
    # A failure is usually transient (rate limit, timeout, blip). Caching it
    # for the process lifetime would permanently degrade that image for the
    # session, and the user's obvious next move — ask again — would keep
    # returning the stale note.
    import src.providers.fusion_provider as mod

    provider, inner, vision = build(vision=ExplodingVision())
    provider.chat([{"role": "user", "content": [image("X")]}])
    assert vision.calls == 1

    saved = mod._FAILURE_TTL_SECONDS
    try:
        mod._FAILURE_TTL_SECONDS = -1.0   # every new failure is already stale
        provider.chat([{"role": "user", "content": [image("Y")]}])
        provider.chat([{"role": "user", "content": [image("Y")]}])
    finally:
        mod._FAILURE_TTL_SECONDS = saved
    assert vision.calls == 3, "an expired failure must be retried"


def test_failures_are_cached_so_an_outage_is_not_retried_every_turn():
    # History is replayed each turn. Uncached, a down vision provider costs
    # (images × timeout × SDK retries) on EVERY request, forever — minutes
    # added per turn, none of it interruptible.
    provider, inner, vision = build(vision=ExplodingVision())
    history = [{"role": "user", "content": [image("A"), image("B")]}]
    for _ in range(5):
        provider.chat(list(history))
    assert vision.calls == 2, f"expected one attempt per image, got {vision.calls}"
    # Still no image survives, and the note explains why.
    assert count_images(inner.seen) == 0
    assert "could not be described" in inner.seen[0]["content"][0]["text"]


class _FiredSignal:
    def aborted(self):
        return True


class _LiveSignal:
    aborted = False


def test_abort_signal_stops_further_vision_calls():
    # _fuse runs BEFORE delegation, so without this an ESC during a fused
    # turn would still work through the whole history first.
    provider, inner, vision = build()
    provider.chat_stream_response(
        [{"role": "user", "content": [image("A"), image("B")]}],
        abort_signal=_FiredSignal(),
    )
    assert vision.calls == []
    assert count_images(inner.seen) == 0        # invariant 1 still holds
    assert "interrupted" in inner.seen[0]["content"][0]["text"]


def test_already_described_images_still_render_after_abort():
    # The abort check gates only NEW vision calls. A cached description is
    # free, so history the session already paid for keeps rendering — only
    # the un-described images degrade to a note.
    provider, inner, vision = build()
    provider.chat([{"role": "user", "content": [image("CACHED")]}])
    assert len(vision.calls) == 1

    provider.chat_stream_response(
        [{"role": "user", "content": [image("CACHED"), image("NEW")]}],
        abort_signal=_FiredSignal(),
    )
    blocks = inner.seen[0]["content"]
    assert "A red square beside a blue one." in blocks[0]["text"]
    assert "interrupted" in blocks[1]["text"]
    assert len(vision.calls) == 1        # no new call despite the new image
    assert count_images(inner.seen) == 0


def test_unfired_abort_signal_does_not_block_substitution():
    provider, inner, vision = build()
    provider.chat_stream_response(
        [{"role": "user", "content": [image()]}], abort_signal=_LiveSignal()
    )
    assert len(vision.calls) == 1


def test_abort_signal_is_still_forwarded_to_the_inner_provider():
    signal = _LiveSignal()
    provider, inner, _ = build()
    provider.chat_stream_response([{"role": "user", "content": "hi"}], abort_signal=signal)
    assert inner.seen_kwargs["abort_signal"] is signal


def test_request_wall_clock_bound_stops_describing():
    import src.providers.fusion_provider as mod

    provider, inner, vision = build(max_images=8)
    # Deadline already passed: a slow-but-not-failing vision provider must
    # not multiply timeout_ms by the image count on the user's turn.
    monkey = mod._MAX_REQUEST_VISION_SECONDS
    try:
        mod._MAX_REQUEST_VISION_SECONDS = -1.0
        provider.chat([{"role": "user", "content": [image("A"), image("B")]}])
    finally:
        mod._MAX_REQUEST_VISION_SECONDS = monkey
    assert vision.calls == []
    assert count_images(inner.seen) == 0
    assert "time limit" in inner.seen[0]["content"][0]["text"]


# ── container-shape hardening ─────────────────────────────────────────────


def test_bare_dict_content_is_walked():
    # Not produced in-repo (every producer emits a list), but a resumed
    # session or SDK caller could; unwalked it would put an image on the
    # wire and break invariant 1.
    provider, inner, _ = build()
    provider.chat([{"role": "user", "content": image()}])
    assert count_images(inner.seen) == 0


def test_bare_dict_tool_result_content_is_walked():
    provider, inner, _ = build()
    provider.chat([{
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t", "content": image()}],
    }])
    assert count_images(inner.seen) == 0


# ── concurrency ──────────────────────────────────────────────────────────


def test_concurrent_fuse_from_multiple_threads():
    # Subagents share the session provider, and chat_async offloads to a
    # thread, so _fuse can be entered concurrently.
    import threading

    provider, _, vision = build()
    errors: list[BaseException] = []

    def worker(n: int):
        try:
            provider.chat([{"role": "user", "content": [image(f"IMG{n % 3}")]}])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    # 3 distinct images; the cache may race so allow one extra call each.
    assert 3 <= len(vision.calls) <= 12


# --- empty-200 retry --------------------------------------------------------
#
# A vision model that answers 200 with no text is up, fast, and just produced
# nothing that turn. Caching that permanently loses the image for the rest of
# the session over a transient quirk -- terminal-bench gcode-to-text
# (2026-08-02), where the vision leg returned no text for image 2 of 5 and the
# task scored 0 against a baseline that solved it. Transport failures keep the
# old single-attempt + negative-cache behaviour, which is what keeps a real
# outage from costing 2x the calls on every turn forever.


class FlakyVision:
    """Empty text for the first ``empty_first`` calls, then a description."""

    def __init__(self, empty_first: int = 1, text: str = "A gcode preview.") -> None:
        self.empty_first = empty_first
        self.text = text
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls <= self.empty_first:
            return FakeResponse("")
        return FakeResponse(self.text)


def test_empty_vision_response_is_retried_once_and_succeeds():
    provider, inner, vision = build(vision=FlakyVision())
    provider.chat([{"role": "user", "content": [image()]}])

    assert vision.calls == 2, "the empty 200 should have been re-issued once"
    assert count_images(inner.seen) == 0
    text = inner.seen[0]["content"][0]["text"]
    assert "A gcode preview." in text
    assert "could not be described" not in text


def test_retry_is_bounded_at_one_extra_call():
    provider, inner, vision = build(vision=FlakyVision(empty_first=99))
    provider.chat([{"role": "user", "content": [image()]}])

    assert vision.calls == 2, "exactly one retry, not a loop"
    assert "could not be described" in inner.seen[0]["content"][0]["text"]


def test_transport_failure_is_not_retried():
    """The load-bearing asymmetry: an outage must still cost ONE call.

    Retrying here would restore the 8 images x 60s x 2 attempts every turn
    that the negative cache exists to prevent.
    """
    provider, inner, vision = build(vision=ExplodingVision())
    provider.chat([{"role": "user", "content": [image()]}])

    assert vision.calls == 1
    assert "vision is down" in inner.seen[0]["content"][0]["text"]


def test_retry_charges_the_call_budget():
    """Two images, a cap of 2, and both need a retry: the cap must count the
    retries, so the second image is refused rather than silently doubling
    the fan-out the cap exists to bound."""
    provider, inner, vision = build(vision=FlakyVision(), max_images=2)
    provider.chat([{"role": "user", "content": [image("A"), image("B")]}])

    assert vision.calls == 2, "image A's two calls should exhaust a cap of 2"
    blocks = inner.seen[0]["content"]
    assert "A gcode preview." in blocks[0]["text"]
    assert "image limit" in blocks[1]["text"]
    assert count_images(inner.seen) == 0


def test_a_retried_failure_is_still_cached_negatively():
    """After the retry also comes back empty, the failure caches as before —
    a replayed history must not re-pay for it every turn."""
    provider, inner, vision = build(vision=FlakyVision(empty_first=99))
    msg = [{"role": "user", "content": [image()]}]
    provider.chat(copy.deepcopy(msg))
    provider.chat(copy.deepcopy(msg))

    assert vision.calls == 2, "the second turn must replay the cached failure"
