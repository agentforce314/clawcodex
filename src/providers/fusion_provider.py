"""FusionProvider — runs a vision model in front of a text-only base model.

The runtime half of the fusion-model feature; the record and its lifecycle
live in :mod:`src.providers.fusion_models`.

Mechanism
---------
A delegating wrapper around the base provider (the ``_EffortProvider``
pattern from ``server/agent_server.py``, including its ``_inner``
recursion guard). On every outbound call it rewrites the message list:
each Anthropic ``image`` block is sent to the configured vision model and
**replaced** with a text block carrying that model's description. The base
provider — and therefore the wire — never sees an image.

Why substitution rather than CCR's tool
--------------------------------------
claude-code-router exposes vision as an MCP tool
(``vision_understand``) the base model may choose to call, because CCR is
a proxy and cannot touch the agent loop. That cannot fix the case this
feature exists for: when the user pastes a screenshot, the image block is
already in the request, so ``deepseek-v4-pro`` returns

    HTTP 400  unknown variant `image_url`, expected `text`

before the model gets a turn to call any tool. clawcodex owns the loop, so
it implements what CCR's docs describe — a vision model "in front of" the
base model, whose result is passed into context — directly. One
consequence worth the trade: images work through *every* entry point at
once (paste, ``@file.png``, ``Read`` on an image, ``Bash`` image output),
with no reliance on the model electing to call a tool.

Invariants
----------
1. **No image block survives.** Every image is replaced, including when the
   vision call fails, times out, or the per-request cap is hit — a
   leftover image block is precisely the 400 this feature prevents, so a
   failure degrades to a text note rather than a dead turn.
2. **The caller's messages are never mutated.** The session owns the
   conversation; rewriting it in place would destroy the original images
   and break a later switch back to a vision-capable model. Rewriting is
   copy-on-write: untouched messages are passed through by reference.
3. **Each distinct image is described once.** Conversation history is
   re-sent on every turn, so without a cache an N-turn conversation would
   pay for the same screenshot N times. Keyed by content hash plus the
   vision configuration in a process-wide bounded cache, so it also
   survives the provider rebuild that a ``/model`` switch performs.
   Failures are cached too — see ``_substitute``.

Scope
-----
``image`` blocks only. Anthropic ``document`` blocks (a PDF handed straight
to the API) are NOT substituted and would still be rejected by a text-only
base model. Not a gap in practice: ``tool_system/tools/read.py`` rasterizes
PDF pages into ``image`` blocks, so PDFs read through the agent are covered;
a caller who hand-builds a ``document`` block is not.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

from .fusion_models import FusionModel

logger = logging.getLogger(__name__)

#: Max descriptions retained process-wide. Each entry is a short string
#: plus a 64-char key, so a few hundred costs well under a megabyte while
#: comfortably covering a long session's screenshots.
_CACHE_MAX_ENTRIES = 256

#: Longest description accepted from the vision model. A runaway response
#: would otherwise be pasted verbatim into every subsequent turn's context.
_MAX_DESCRIPTION_CHARS = 4_000

_DEFAULT_VISION_PROMPT = (
    "Describe this image in full, objective detail for a software engineer who "
    "cannot see it. Transcribe every piece of visible text verbatim, including "
    "code, terminal output, error messages, labels, and numbers. Describe the "
    "layout, any UI elements, and anything that looks wrong or noteworthy. Do "
    "not speculate about intent and do not offer advice — report only what is "
    "visible."
)

#: Ceiling on total time the rewrite may spend on vision calls for ONE
#: request, independent of the per-image timeout. A degraded provider that
#: answers slowly (rather than failing fast) would otherwise multiply
#: ``timeout_ms`` by the image count on the critical path of the user's turn.
_MAX_REQUEST_VISION_SECONDS = 180.0


#: How long a cached FAILURE suppresses retries. Successes are cached for
#: the process lifetime (the description of an image does not change), but a
#: failure is usually transient — a rate limit, a timeout, a network blip —
#: so caching it forever would permanently degrade that image for the rest
#: of the session, and the user's obvious next move ("look at it again")
#: would keep returning the stale note. Long enough to collapse the
#: re-described history of one outage into a single attempt, short enough
#: that a recovered provider is picked up within a turn or two.
_FAILURE_TTL_SECONDS = 90.0


class _Failure:
    """A cached vision failure, with an expiry.

    Distinguishable from a description by type, so a replayed history shows
    the note again instead of re-attempting a call that is still failing —
    until :attr:`expires_at`, after which the entry is treated as a miss and
    the image is retried.
    """

    __slots__ = ("expires_at", "reason")

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.expires_at = time.monotonic() + _FAILURE_TTL_SECONDS

    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class _EmptyVisionResponse(RuntimeError):
    """The vision model answered 200 with no usable text.

    Distinguished from every other vision failure because it is the one that
    is worth a retry: the provider is reachable and fast, it just produced
    nothing this turn. See :meth:`FusionProvider._describe`.
    """


class _Budget:
    """Per-request limits on the rewrite: call count, wall clock, and abort.

    ``calls`` bounds network fan-out. Cache hits never touch it — the cap
    exists to bound *calls*, and charging a cached description would make
    the same conversation degrade as it grows.
    """

    __slots__ = ("abort_signal", "calls", "deadline")

    def __init__(self, calls: int, abort_signal: Any = None) -> None:
        self.calls = calls
        self.deadline = time.monotonic() + _MAX_REQUEST_VISION_SECONDS
        self.abort_signal = abort_signal

    def exhausted(self) -> str | None:
        """Why no further vision call may run, or ``None`` to proceed."""
        if _signal_aborted(self.abort_signal):
            # ESC during a fused turn: stop describing immediately rather
            # than working through a long history the user already cancelled.
            return "the request was interrupted"
        if self.calls <= 0:
            return "this request reached its image limit"
        if time.monotonic() >= self.deadline:
            return "this request reached its vision time limit"
        return None


def _signal_aborted(signal: Any) -> bool:
    """Whether ``abort_signal`` has fired. Tolerant of any signal shape."""
    if signal is None:
        return False
    try:
        aborted = getattr(signal, "aborted", None)
        return bool(aborted() if callable(aborted) else aborted)
    except Exception:  # noqa: BLE001 — an unreadable signal is not an abort
        return False


_cache: "OrderedDict[str, str | _Failure]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key: str) -> "str | _Failure | None":
    with _cache_lock:
        value = _cache.get(key)
        if isinstance(value, _Failure) and value.expired():
            # Expired failure ⇒ a miss, so the image is retried once the
            # vision provider has had time to recover.
            del _cache[key]
            return None
        if value is not None:
            _cache.move_to_end(key)  # LRU
        return value


def _cache_put(key: str, value: "str | _Failure") -> None:
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_vision_cache() -> None:
    """Drop every cached description and cached failure.

    Test-only: the cache key already covers everything that determines a
    description (vision selector + effective prompt, see
    :meth:`FusionProvider._scope`), so no production path needs to
    invalidate it — a reconfigured fusion model simply keys elsewhere. Tests
    need it because the cache is process-wide and would otherwise leak call
    counts between them.
    """
    with _cache_lock:
        _cache.clear()


def _image_source(block: Any) -> dict[str, Any] | None:
    """The ``source`` dict of an Anthropic image block, or ``None``.

    Defensive about shape: every in-repo producer emits
    ``{"type": "image", "source": {...}}`` (``tool_system/tools/read.py``,
    ``tool_system/tools/bash/image_output.py``, the agent-server paste
    path), but a resumed session or an SDK caller could hand over
    something else, and this walker runs on the critical path of every
    request.
    """
    if not isinstance(block, dict) or block.get("type") != "image":
        return None
    source = block.get("source")
    return source if isinstance(source, dict) else {}


def _image_key(scope: str, source: dict[str, Any]) -> str | None:
    """Cache key for an image under a given vision configuration, or ``None``
    if the block carries no usable payload.

    ``scope`` covers everything that determines the description — the vision
    selector AND the effective prompt (see :meth:`FusionProvider._scope`).
    Both matter: re-pointing a fusion model at a different vision model, or
    changing its ``prompt``, must not serve descriptions produced under the
    old configuration. Keying on the selector alone made
    :attr:`FusionModel.prompt` silently inert whenever any fusion model had
    already described that image.
    """
    data = source.get("data")
    if isinstance(data, str) and data:
        digest = hashlib.sha256(data.encode("utf-8", "ignore")).hexdigest()
        return f"{scope}|b64:{digest}"
    url = source.get("url")
    if isinstance(url, str) and url:
        digest = hashlib.sha256(url.encode("utf-8", "ignore")).hexdigest()
        return f"{scope}|url:{digest}"
    return None


def _describe_size(source: dict[str, Any]) -> str:
    """Human-readable size/type prefix for the substituted text block."""
    media_type = source.get("media_type")
    parts = [str(media_type)] if isinstance(media_type, str) and media_type else []
    data = source.get("data")
    if isinstance(data, str) and data:
        # base64 inflates by 4/3; good enough for a context label.
        parts.append(f"{len(data) * 3 // 4 // 1024} KB")
    return ", ".join(parts)


class FusionProvider:
    """Wraps ``inner`` so images are described by ``fusion.vision`` first.

    Not a :class:`~src.providers.base.BaseProvider` subclass, deliberately
    — attribute delegation via :meth:`__getattr__` must reach the inner
    provider's real values (``is_deepseek``, ``base_url``,
    ``has_custom_endpoint``, …), and inheriting would shadow them with
    ``BaseProvider``'s class-level defaults. Same choice as
    ``_EffortProvider``.
    """

    def __init__(
        self,
        inner: Any,
        fusion: FusionModel,
        *,
        vision_provider: Any | None = None,
    ) -> None:
        self._inner = inner
        self._fusion = fusion
        # Injected by tests; production builds it lazily on first image so a
        # fusion session that never sees one makes no vision provider at all.
        self._vision_provider = vision_provider
        self._vision_lock = threading.Lock()

    # ── identity ─────────────────────────────────────────────────────────
    #
    # ``model`` reports the BASE model id, not the fusion name. Everything
    # downstream keys off this string — context window
    # (``models/configs.py``), cost (``cost_tracker``), capability probes,
    # and the request's own ``model`` field — and the base model is the
    # correct answer for all four: it is what actually serves the turn. The
    # fusion name is surfaced separately via :attr:`fusion_name` so the UI
    # can show it without corrupting those lookups.

    @property
    def model(self) -> Any:
        return getattr(self._inner, "model", None)

    @model.setter
    def model(self, value: Any) -> None:
        # An explicit property (not ``__getattr__``, which never sees
        # assignment) so ``provider.model = x`` reaches the inner provider
        # instead of silently shadowing it on the wrapper. Repointing the
        # base model keeps fusion active; swapping to a different fusion
        # model, or off fusion entirely, replaces the whole provider (see
        # the agent-server's ``_do_set_model``).
        self._inner.model = value

    @property
    def fusion(self) -> FusionModel:
        """The record backing this wrapper."""
        return self._fusion

    @property
    def fusion_name(self) -> str:
        """The user-facing fusion model name (for display, never the wire)."""
        return self._fusion.name

    @property
    def inner(self) -> Any:
        """The wrapped base provider."""
        return self._inner

    def __getattr__(self, name: str) -> Any:
        # Guard the delegate itself. Without this, an instance built WITHOUT
        # __init__ (copy.copy / copy.deepcopy create one that way, then probe
        # for __setstate__ / __deepcopy__) recurses until RecursionError:
        # __getattr__ looks up self._inner, which is missing, which calls
        # __getattr__… Two live sites copy the session provider
        # (``agent/run_agent.py``'s per-subagent model override and
        # ``permissions/yolo_classifier.py``) and both swallow Exception —
        # which RecursionError is — so the failure would be silent. Carried
        # over verbatim from ``_EffortProvider``, which learned it the hard
        # way.
        if name in ("_inner", "_fusion", "_vision_provider", "_vision_lock"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    # ``copy``/``deepcopy`` are implemented explicitly because
    # ``_vision_lock`` is a ``threading.Lock``, which cannot be pickled —
    # and ``copy.deepcopy`` falls back to ``__reduce_ex__`` for objects it
    # does not know, so the default would raise
    # ``TypeError: cannot pickle '_thread.lock' object``.
    #
    # That would not be a loud failure. Two live sites copy the session
    # provider — ``agent/run_agent.py``'s per-subagent model override and
    # ``permissions/yolo_classifier.py`` — and BOTH swallow ``Exception``,
    # so on a fusion session subagent model overrides and yolo
    # classification would have quietly stopped working. Caught by
    # ``test_deepcopy_does_not_recurse_forever``.

    def __copy__(self) -> "FusionProvider":
        import copy as _copy

        # The INNER provider is shallow-copied, not shared. Both live call
        # sites copy specifically to get an isolated ``.model``:
        # ``agent/run_agent.py`` does ``turn_provider = copy.copy(provider);
        # turn_provider.model = resolved_model`` under a comment reading
        # "NEVER mutate the shared session provider … N parallel subagents
        # share params.provider — mutating provider.model would race across
        # them", and ``permissions/yolo_classifier.py`` does the same for
        # its classifier model.
        #
        # Sharing ``_inner`` would defeat that: this class defines ``model``
        # as a property whose setter writes THROUGH to the inner provider,
        # so a subagent's override would repoint the main loop's base model
        # (e.g. leaving the session firing a Haiku id at api.deepseek.com)
        # and N subagents would race. Both sites swallow Exception, so it
        # would have been silent. Shallow-copying the inner reproduces plain
        # provider semantics exactly — own ``.model``, shared HTTP client.
        return FusionProvider(
            _copy.copy(self._inner), self._fusion, vision_provider=self._vision_provider
        )

    def __deepcopy__(self, memo: dict) -> "FusionProvider":
        import copy as _copy

        # The vision provider is deliberately NOT copied: it is rebuilt
        # lazily on first image, and the description cache is process-wide,
        # so the clone loses nothing but an idle SDK client. Copying it
        # would drag another live HTTP client (and its own locks) along.
        #
        # ``memo`` is seeded BEFORE recursing so a cycle back to this object
        # resolves to the clone instead of building a second one. No provider
        # holds such a back-reference today; the guard is free.
        clone = FusionProvider.__new__(FusionProvider)
        memo[id(self)] = clone
        clone.__init__(_copy.deepcopy(self._inner, memo), self._fusion)
        return clone

    # ── vision ───────────────────────────────────────────────────────────

    def _vision(self) -> Any:
        """The vision provider, built once on first use.

        Double-checked under a lock: ``chat``/``chat_stream_response`` can
        be entered from more than one thread (the async offload in
        ``BaseProvider.chat_async``, subagents sharing a session provider),
        and building two SDK clients would be wasteful rather than wrong.
        """
        if self._vision_provider is not None:
            return self._vision_provider
        with self._vision_lock:
            if self._vision_provider is not None:
                return self._vision_provider
            from src.config import get_provider_config
            from src.providers import get_provider_class, resolve_api_key

            ref = self._fusion.vision
            provider_cls = get_provider_class(ref.provider)
            cfg = dict(get_provider_config(ref.provider) or {})
            # Translate config keys to constructor kwargs explicitly —
            # ``default_model`` and any future config field must not be
            # forwarded as kwargs. Same reasoning as ``utils/advisor.py``.
            self._vision_provider = provider_cls(
                api_key=resolve_api_key(ref.provider, cfg),
                base_url=cfg.get("base_url"),
                model=ref.model,
            )
            return self._vision_provider

    def _prompt(self) -> str:
        """The effective instruction sent to the vision model."""
        if self._fusion.prompt:
            return (
                f"{_DEFAULT_VISION_PROMPT}\n\nAdditional instructions: "
                f"{self._fusion.prompt}"
            )
        return _DEFAULT_VISION_PROMPT

    def _scope(self) -> str:
        """Cache scope: everything that determines a description's content.

        The vision selector plus a digest of the effective prompt, so two
        fusion models sharing a vision model but differing in ``prompt`` do
        not serve each other's descriptions.
        """
        digest = hashlib.sha256(self._prompt().encode("utf-8", "ignore")).hexdigest()[:16]
        return f"{self._fusion.vision.selector}|p:{digest}"

    def _vision_timeout(self, provider: Any) -> Any:
        """The per-call timeout for the vision request.

        A phase-split ``httpx.Timeout`` when httpx is available, never a bare
        float: httpx expands a float across ALL four phases, so a bare value
        would silently lift ``connect`` from the client-level 15s to the full
        vision timeout — the exact hazard ``anthropic_provider.chat``
        documents ("Pass the phase-split httpx.Timeout, never a bare float"),
        where a black-holed SYN then hangs for the whole window. Keeps
        ``connect`` short while allowing a slow image analysis to finish.
        """
        seconds = max(1.0, self._fusion.timeout_ms / 1000.0)
        try:
            import httpx

            return httpx.Timeout(seconds, connect=min(15.0, seconds))
        except Exception:  # noqa: BLE001 — the float still satisfies the SDK
            return seconds

    def _describe(self, source: dict[str, Any], budget: "_Budget | None" = None) -> str:
        """Ask the vision model about one image. Returns its description.

        Raises on failure; :meth:`_substitute` converts that into a text note
        so invariant 1 (no image block survives) always holds.

        An EMPTY 200 gets one retry; nothing else does. The distinction is
        what keeps the negative cache honest. A transport failure means the
        vision provider is unreachable, and ``_substitute`` caches that so an
        outage costs one attempt per image rather than being re-tried on every
        turn of a replayed history — the arithmetic in :meth:`_substitute`'s
        failure branch (8 images x 60 s x 2 attempts per turn) is why. An empty
        completion is the opposite situation: the provider is up, answered
        fast, and just produced nothing that turn.

        What caching an empty 200 actually costs — stated precisely, because
        an earlier draft of this docstring got it wrong and contradicted
        :data:`_FAILURE_TTL_SECONDS` directly. Failures are NOT cached
        forever: the entry carries a 90 s expiry and ``_cache_get`` drops it.
        So the real cost is that the image is degraded to a "could not be
        described" note for up to 90 s — every turn that falls inside that
        window, which in a fast agent loop is several. That was enough to
        matter on terminal-bench gcode-to-text (2026-08-02), where
        ``openai:gpt-5.6-luna`` returned no text for image 2 of 5 and the task
        scored 0 against a baseline that solved it. One retry inside the same
        request beats waiting out the TTL.

        Bounded at one extra round trip per distinct image, and only while the
        request's own call/time budget still allows it, so the outage
        arithmetic above is unchanged.
        """
        try:
            return self._describe_once(source)
        except _EmptyVisionResponse:
            blocked = budget.exhausted() if budget is not None else None
            if blocked is not None:
                logger.warning(
                    "[fusion] vision returned no text and %s; not retrying", blocked
                )
                raise
            if budget is not None:
                # A retry is a real network call, so charge it. Otherwise a
                # provider stuck returning empty 200s would double the
                # fan-out this cap exists to bound.
                budget.calls -= 1
            logger.info("[fusion] vision returned no text; retrying once")
            return self._describe_once(source)

    def _describe_once(self, source: dict[str, Any]) -> str:
        """One vision call. Raises :class:`_EmptyVisionResponse` for an empty
        200 and lets every other failure propagate as-is."""
        prompt = self._prompt()
        provider = self._vision()
        # The image is handed over in ANTHROPIC block shape and the target
        # provider translates it: OpenAI-compatible providers turn it into
        # an ``image_url`` data URI in ``_prepare_messages``, and
        # Anthropic-wire providers take it as-is. So one call shape covers
        # every vision provider. Same for ``system``, which
        # ``OpenAICompatibleProvider.chat`` folds into a leading system
        # message and the Anthropic wire takes as a kwarg.
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image", "source": dict(source)},
                ],
            }
        ]
        response = provider.chat(
            messages,
            model=self._fusion.vision.model,
            max_tokens=2_000,
            timeout=self._vision_timeout(provider),
        )
        # Record the vision leg's spend. The session cost tracker follows the
        # BASE model (``self.model`` reports the base id, which is what
        # cost/context-window lookups must key off), so these tokens are
        # billed by the provider but appear in no clawcodex total. Logging
        # them keeps the cost at least discoverable; wiring a second model's
        # usage into the tracker's single-model accounting is a larger change
        # than this feature should make.
        usage = getattr(response, "usage", None)
        if usage:
            logger.info(
                "[fusion] vision call billed to %s: %s",
                self._fusion.vision.selector, usage,
            )
        text = (getattr(response, "content", "") or "").strip()
        if not text:
            # Observed live: ``openrouter:z-ai/glm-4.5v`` answers 200 with an
            # empty ``content`` (its text lands in a reasoning field the
            # response shape drops). Treat it as a failure so the caller
            # emits an explicit note rather than an empty text block that
            # reads to the base model as "the image was blank".
            #
            # Typed, not a bare RuntimeError: ``_describe`` retries THIS and
            # only this, and a string match would be a fragile way to say so.
            raise _EmptyVisionResponse(
                f"vision model {self._fusion.vision.selector} returned no text"
            )
        if len(text) > _MAX_DESCRIPTION_CHARS:
            text = text[:_MAX_DESCRIPTION_CHARS] + "… [description truncated]"
        return text

    def _substitute(self, block: Any, budget: "_Budget") -> Any:
        """Return the replacement for one image block (or the block itself)."""
        source = _image_source(block)
        if source is None:
            return block

        label = _describe_size(source)
        prefix = f"[Image ({label})" if label else "[Image"
        vision = self._fusion.vision.selector

        key = _image_key(self._scope(), source)
        if key is None:
            logger.warning("[fusion] image block has no data or url; substituting note")
            return {
                "type": "text",
                "text": f"{prefix}: not described — the image block carried no data.]",
            }

        cached = _cache_get(key)
        if cached is not None:
            # A cached FAILURE replays as its note instead of retrying. See
            # ``_cache_failure`` for why that matters.
            if isinstance(cached, _Failure):
                return {
                    "type": "text",
                    "text": f"{prefix}: could not be described ({vision}): {cached.reason}]",
                }
            return {"type": "text", "text": f"{prefix}, described by {vision}]\n{cached}"}

        exhausted = budget.exhausted()
        if exhausted:
            logger.info("[fusion] %s; leaving image undescribed", exhausted)
            return {
                "type": "text",
                "text": (
                    f"{prefix}: not described — {exhausted}. Ask about this image "
                    "on its own to have it described.]"
                ),
            }

        budget.calls -= 1
        try:
            description = self._describe(source, budget)
        except Exception as exc:  # noqa: BLE001 — invariant 1: never re-raise
            # Degrading to a note keeps the turn alive. Re-raising, or
            # leaving the image in place, reproduces the exact 400 this
            # feature exists to prevent.
            logger.warning("[fusion] vision call failed: %s", exc)
            # Cache the failure. Without this, a vision provider that is
            # down or unreachable is retried for EVERY image on EVERY turn
            # for the life of the session: history is replayed each turn, so
            # 8 images × a 60s timeout × 2 SDK attempts adds ~16 minutes to
            # every request, indefinitely, and none of it is interruptible.
            # Cached negatively, the outage costs one attempt per image and
            # the session stays usable (degraded, and the note says why).
            _cache_put(key, _Failure(str(exc)))
            return {
                "type": "text",
                "text": (
                    f"{prefix}: could not be described — the vision model "
                    f"({vision}) failed: {exc}]"
                ),
            }
        _cache_put(key, description)
        return {"type": "text", "text": f"{prefix}, described by {vision}]\n{description}"}

    def _fuse_content(self, content: Any, budget: "_Budget") -> tuple[Any, bool]:
        """Rewrite a content list. Returns ``(content, changed)``.

        Recurses one level into ``tool_result`` blocks, which is where a
        ``Read``-returned or ``Bash``-captured image lives. Copy-on-write:
        ``changed=False`` means the caller keeps its original object.

        A bare dict ``content`` is normalized to a one-element list first.
        Every in-repo producer emits list-shaped content and the API accepts
        only ``str | list``, so this is defence for a resumed session or an
        SDK caller — the same population ``_image_source`` hardens the block
        shape for. Without it, ``content = <image dict>`` would walk past an
        image and put it on the wire, breaking invariant 1.
        """
        if isinstance(content, dict):
            fused, changed = self._fuse_content([content], budget)
            return (fused, True) if changed else (content, False)
        if not isinstance(content, list):
            return content, False

        out: list[Any] = []
        changed = False
        for block in content:
            if _image_source(block) is not None:
                out.append(self._substitute(block, budget))
                changed = True
                continue
            if isinstance(block, dict) and block.get("type") == "tool_result":
                inner, inner_changed = self._fuse_content(block.get("content"), budget)
                if inner_changed:
                    out.append({**block, "content": inner})
                    changed = True
                    continue
            out.append(block)
        return (out, True) if changed else (content, False)

    def _fuse(self, messages: Any, abort_signal: Any = None) -> Any:
        """Describe-and-substitute every image in ``messages``.

        Returns the original list untouched when there are no images, which
        is the overwhelmingly common case — a fusion session pays nothing
        for turns without one.

        ``abort_signal`` (the caller's, when it passed one) is consulted
        between images so ESC takes effect during the rewrite rather than
        only after it: the rewrite runs BEFORE delegation, so a long history
        of images against a slow vision provider would otherwise be
        uninterruptible and look like a hang.
        """
        if not isinstance(messages, list) or not messages:
            return messages

        budget = _Budget(max(1, self._fusion.max_images), abort_signal)
        started = time.monotonic()
        out: list[Any] = []
        changed = False
        for message in messages:
            # Typed ChatMessage carries ``content: str`` and so can never
            # hold an image block; only dict messages are walked.
            if not isinstance(message, dict):
                out.append(message)
                continue
            content, message_changed = self._fuse_content(message.get("content"), budget)
            if message_changed:
                out.append({**message, "content": content})
                changed = True
            else:
                out.append(message)
        if not changed:
            return messages
        logger.info(
            "[fusion] %s: substituted image blocks via %s (%d vision attempt(s), %.1fs)",
            self._fusion.name,
            self._fusion.vision.selector,
            max(1, self._fusion.max_images) - budget.calls,
            time.monotonic() - started,
        )
        return out

    def _fused_args(
        self, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        """Rewrite whichever of ``args``/``kwargs`` carries ``messages``.

        Callers pass messages positionally (``query._call_model_sync``) or
        by keyword; handling both keeps the wrapper transparent either way.

        The caller's ``abort_signal`` (present on
        ``chat_stream_response``) is forwarded to the rewrite so ESC can
        interrupt it, and left in ``kwargs`` for the inner provider.
        """
        signal = kwargs.get("abort_signal")
        if args:
            return (self._fuse(args[0], signal), *args[1:]), kwargs
        if "messages" in kwargs:
            return args, {**kwargs, "messages": self._fuse(kwargs["messages"], signal)}
        return args, kwargs

    # ── delegated chat surface ───────────────────────────────────────────

    def chat_stream_response(self, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = self._fused_args(args, kwargs)
        return self._inner.chat_stream_response(*args, **kwargs)

    def chat(self, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = self._fused_args(args, kwargs)
        return self._inner.chat(*args, **kwargs)

    def chat_stream(self, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = self._fused_args(args, kwargs)
        return self._inner.chat_stream(*args, **kwargs)

    async def chat_async(self, *args: Any, **kwargs: Any) -> Any:
        # The vision call is blocking HTTP, so the rewrite is offloaded
        # rather than run on the event loop — the same reasoning as
        # ``BaseProvider.chat_async``'s own ``to_thread`` offload.
        args, kwargs = await asyncio.to_thread(self._fused_args, args, kwargs)
        return await self._inner.chat_async(*args, **kwargs)


def build_fusion_provider(fusion: FusionModel, **overrides: Any) -> FusionProvider:
    """Construct a :class:`FusionProvider` for ``fusion`` from config.

    Builds the base provider from ``fusion.base`` the same way the
    entrypoints do (provider class + resolved key + configured base_url),
    then wraps it. ``overrides`` is forwarded to :class:`FusionProvider`
    (tests inject ``vision_provider``).
    """
    from src.config import get_provider_config
    from src.providers import get_provider_class, resolve_api_key

    ref = fusion.base
    cfg = dict(get_provider_config(ref.provider) or {})
    provider_cls = get_provider_class(ref.provider)
    inner = provider_cls(
        api_key=resolve_api_key(ref.provider, cfg),
        base_url=cfg.get("base_url"),
        model=ref.model,
    )
    return FusionProvider(inner, fusion, **overrides)


__all__ = ["FusionProvider", "build_fusion_provider", "clear_vision_cache"]
