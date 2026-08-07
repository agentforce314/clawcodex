"""``vision_analyze`` — borrow a vision model's eyes, on demand, with a question.

Ported from hermes-agent (``tools/vision_tools.py``), whose two-parameter
``(image_url, question)`` schema this keeps so the two stay recognisable.

Why this exists alongside fusion
--------------------------------
A fusion model (``src/providers/fusion_models.py``) substitutes every image
with a description at the provider layer. That is the only thing that can
rescue an image which is ALREADY in flight — a pasted screenshot, a ``Read``
result — because the request 400s before the model gets a turn. But it runs
ONE fixed "describe everything" pass and then discards the bytes, so a base
model that needs the VAT line off an invoice the generic pass skimmed has no
recourse. ``fusion_models.py`` states the gap itself: *"there is no per-call
tool invocation."*

This tool is that invocation. The model supplies its own ``question``, which
is concatenated into the vision prompt, so the second look is targeted rather
than generic. It is also what makes vision reachable with NO fusion model
configured at all.

Local paths only, deliberately
------------------------------
``image_url`` accepts a filesystem path. Remote ``http(s)`` is refused with a
clear message rather than fetched: doing it safely needs URL validation, a
redirect guard and a size cap (hermes carries all three), and that is its own
change. Paths run through ``ToolContext.ensure_readable_path``, so the
existing workspace containment rules apply for free — this tool cannot be
used to read an image outside the allowed roots.

Cost
----
Unlike the fusion vision leg — whose tokens are logged but, by that module's
own admission, "appear in no clawcodex total" — this call self-records via
``record_api_usage``, the same way the client-side advisor does.
"""

from __future__ import annotations

import base64
import threading
from pathlib import Path
from typing import Any

from ..build_tool import Tool, build_tool
from ..context import ToolContext
from ..protocol import ToolResult

#: Cap on the returned description, mirroring the fusion path's
#: ``_MAX_DESCRIPTION_CHARS`` so neither route can blow up a turn. Kept
#: below ``max_result_size_chars`` deliberately: this bound is about what a
#: model should have to read, the tool-result bound is a transport backstop.
_MAX_ANSWER_CHARS = 8_000

_BASE_PROMPT = (
    "You are describing an image for a software engineer who cannot see it. "
    "Transcribe every piece of visible text verbatim — code, terminal output, "
    "error messages, labels and numbers — and describe layout and any UI "
    "elements. Do not speculate about intent."
)


def _err(message: str) -> ToolResult:
    return ToolResult(name="vision_analyze", output=message, is_error=True)


def _load_image(raw_path: str, context: ToolContext) -> tuple[dict[str, Any], str] | ToolResult:
    """Resolve+read the image, returning an Anthropic ``source`` dict.

    Returns a ``ToolResult`` instead when the input is unusable — the caller
    surfaces it as a tool error rather than raising, so a bad path costs the
    model one tool call rather than the whole turn.
    """
    candidate = raw_path.strip()
    if candidate.lower().startswith(("http://", "https://")):
        return _err(
            "vision_analyze takes a local file path, not a URL. Download the "
            "image first (e.g. with Bash) and pass the resulting path."
        )
    if candidate.startswith("data:"):
        return _err(
            "vision_analyze takes a local file path, not a data: URL. Write "
            "the bytes to a file first and pass that path."
        )

    try:
        # Containment: the same guard every other file-reading tool uses.
        path = Path(context.ensure_readable_path(candidate))
    except Exception as exc:  # noqa: BLE001 — surface as a tool error
        return _err(f"Cannot read {candidate!r}: {exc}")

    # Existence before extension: a typo'd path should say "no such file",
    # not "not a supported image", which sends the model looking for a
    # converter it does not need.
    if not path.is_file():
        return _err(f"No such image file: {path}")

    from .read import IMAGE_EXTENSIONS

    ext = path.suffix.lstrip(".").lower()
    if ext not in IMAGE_EXTENSIONS:
        return _err(
            f"Not a supported image: {path} (expected one of "
            f"{', '.join(sorted(IMAGE_EXTENSIONS))})."
        )

    # Reuse Read's pipeline wholesale rather than reimplementing it. Two
    # things it gets right that a naive read_bytes()+extension does not:
    #
    #  * MAGIC BYTES decide media_type, not the filename. A JPEG saved as
    #    .png would otherwise ship a mismatched media_type and come back as
    #    a confusing provider 400 that reads like an outage.
    #  * DOWNSCALING. The API ceiling is API_IMAGE_MAX_BASE64_SIZE (5 MB),
    #    and a retina screenshot — the common case here — routinely exceeds
    #    the 3.75 MB raw budget. Without this, exactly the images this
    #    feature exists for would be rejected by the vendor.
    from src.utils.image_processor import (
        IMAGE_READ_SAFETY_CAP,
        ImageProcessingError,
        detect_image_format_from_buffer,
        maybe_resize_image,
        read_file_bytes,
    )

    try:
        # Bounded: guards against a symlinked /dev/zero or a TOCTOU-grown
        # file, same reasoning as read.py's bounded read.
        raw = read_file_bytes(path, IMAGE_READ_SAFETY_CAP)
    except OSError as exc:
        return _err(f"Cannot read {path}: {exc}")
    if not raw:
        return _err(f"Image file is empty: {path}")

    try:
        sniffed = detect_image_format_from_buffer(raw)
        result = maybe_resize_image(raw, len(raw), format_hint=sniffed)
        data, media_type = result.data, result.media_type
    except ImageProcessingError as exc:
        # DIVERGENCE from read.py, which falls back to raw bytes here "so the
        # model at least sees something". That is right for Read, whose
        # consumer may still be a vision model; it is wrong here, where the
        # bytes go straight to a vendor that would answer with an opaque 400.
        # A clear tool error is the better failure.
        return _err(f"Cannot process image {path}: {exc}")

    # No token-budget compression step (read.py's compress_image_to_token_budget):
    # that bounds what an image costs in the MAIN model's context, and this
    # image never enters it — only the vision model's reply does, itself
    # capped by _MAX_ANSWER_CHARS. The size ceiling above is the one that
    # matters here.

    source = {
        "type": "base64",
        "media_type": media_type,
        "data": base64.b64encode(data).decode("ascii"),
    }
    return source, str(path)


def _vision_call(tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
    # Local imports: this tool sits in the static registry and we don't want
    # every boot path to wake providers/config. Same reasoning as advisor.py.
    from src.providers.vision_config import load_vision_config

    cfg = load_vision_config()
    if cfg is None:
        # Defence in depth: ``is_enabled`` should have kept this tool out of
        # the schema, but registry dispatch looks tools up by NAME and
        # bypasses that gate entirely (see AdvisorTool's note).
        return _err(
            "Vision unavailable: no vision model configured. Run "
            "/vision <provider>:<model> to set one."
        )

    raw_path = tool_input.get("image_url") or tool_input.get("image_path") or ""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return _err("vision_analyze requires 'image_url' (a local file path).")
    question = tool_input.get("question")
    question = question.strip() if isinstance(question, str) else ""

    loaded = _load_image(raw_path, context)
    if isinstance(loaded, ToolResult):
        return loaded
    source, resolved_path = loaded

    prompt = _BASE_PROMPT
    if cfg.prompt:
        prompt = f"{prompt}\n\nAdditional instructions: {cfg.prompt}"
    if question:
        # The whole point of the tool: a targeted question rather than
        # fusion's fixed pass. Mirrors hermes's prompt composition.
        prompt = f"{prompt}\n\nThen answer this specific question:\n{question}"

    try:
        text, usage = _ask_vision_model(cfg, source, prompt)
    except Exception as exc:  # noqa: BLE001 — a failed look must not kill the turn
        return _err(f"Vision call to {cfg.selector} failed: {exc}")

    if not text:
        return _err(f"Vision model {cfg.selector} returned no text.")
    if len(text) > _MAX_ANSWER_CHARS:
        text = text[:_MAX_ANSWER_CHARS] + "… [truncated]"

    _record_usage(cfg, usage)
    return ToolResult(
        name="vision_analyze",
        output=f"[{resolved_path} — described by {cfg.selector}]\n{text}",
        content_type="text",
    )


def _ask_vision_model(cfg: Any, source: dict[str, Any], prompt: str) -> tuple[str, dict]:
    """One vision call. Raises on transport failure; caller degrades."""
    from src.config import get_provider_config
    from src.providers import get_provider_class, resolve_api_key

    provider_cls = get_provider_class(cfg.provider)
    raw_cfg = dict(get_provider_config(cfg.provider) or {})
    # Translate config keys to constructor kwargs EXPLICITLY. Never
    # ``**raw_cfg`` — ``default_model`` and any future config field would be
    # forwarded as kwargs and crash the constructor. And ``resolve_api_key``
    # rather than ``raw_cfg["api_key"]``, so a provider whose key lives in an
    # env var works instead of dying on "Missing credentials".
    provider = _cached_provider(cfg, provider_cls, raw_cfg)

    # The image is handed over in ANTHROPIC block shape and the target
    # provider translates it: OpenAI-compatible providers turn it into an
    # ``image_url`` data URI in ``_prepare_messages``, Anthropic-wire
    # providers take it as-is. One call shape covers every vision provider —
    # the same trick FusionProvider._describe_once relies on.
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
        model=cfg.model,
        max_tokens=2_000,
        timeout=_timeout_for(provider, cfg),
    )
    text = (getattr(response, "content", "") or "").strip()
    usage = getattr(response, "usage", None)
    return text, usage if isinstance(usage, dict) else _usage_to_dict(usage)


#: One provider instance per (provider, model, base_url), built lazily.
#: A fresh SDK client per call leaks connection pools; FusionProvider caches
#: its vision provider under a lock for the same reason.
_provider_cache: dict[tuple, Any] = {}
_provider_lock = threading.Lock()


def _cached_provider(cfg: Any, provider_cls: Any, raw_cfg: dict) -> Any:
    from src.providers import resolve_api_key

    # The resolved KEY is part of the cache key, not just provider/model/url.
    # Without it a rotated credential or a fresh ``/login`` would keep reusing
    # a client built with the dead one for the life of the process — this
    # cache is module-global, unlike FusionProvider's per-instance one, so
    # nothing rebuilds it on a provider switch either.
    api_key = resolve_api_key(cfg.provider, raw_cfg)
    key = (cfg.provider, cfg.model, raw_cfg.get("base_url"), api_key)
    cached = _provider_cache.get(key)
    if cached is not None:
        return cached
    with _provider_lock:
        cached = _provider_cache.get(key)
        if cached is not None:
            return cached
        cached = provider_cls(
            api_key=api_key,
            base_url=raw_cfg.get("base_url"),
            model=cfg.model,
        )
        _provider_cache[key] = cached
        return cached


def _timeout_for(provider: Any, cfg: Any) -> Any:
    """A phase-split timeout, never a bare float.

    ``httpx`` expands a bare float across ALL FOUR phases, so a 60s budget
    becomes a 60s CONNECT timeout — a dead host hangs the turn for a minute
    instead of failing fast. FusionProvider documents this same hazard.
    """
    seconds = max(1.0, cfg.timeout_ms / 1000.0)
    try:
        import httpx

        return httpx.Timeout(seconds, connect=min(15.0, seconds))
    except Exception:  # noqa: BLE001 — fall back rather than fail the call
        return seconds


def _usage_to_dict(usage: Any) -> dict:
    if usage is None:
        return {}
    out = {}
    for key in ("input_tokens", "output_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            out[key] = value
    return out


def _record_usage(cfg: Any, usage: dict) -> None:
    """Fold the vision call into the session cost totals.

    This is the deliberate difference from the fusion vision leg, which only
    logs its usage and therefore bills real money that shows up in no
    clawcodex total. A tool call has no excuse not to be accounted.
    """
    if not usage:
        return
    try:
        from src.cost_tracker import record_api_usage

        # ``record_api_usage(model, usage)`` — a usage MAPPING, not kwargs.
        record_api_usage(cfg.model, usage)
    except Exception:  # noqa: BLE001 — accounting must never break the result
        pass


def _vision_enabled() -> bool:
    """Gate for the advertised tool schema. Defaults OFF.

    Off by default because this spends money at a SECOND vendor — the same
    reasoning that makes ``advisor_enabled`` opt-in. ``is_enabled`` takes no
    arguments, so it cannot consult the active provider; a settings/config
    read is the only thing available here, and it is the right gate anyway
    (a configured vision model is useful to a vision-capable main model too).
    """
    try:
        from src.providers.vision_config import vision_is_configured

        return vision_is_configured()
    except Exception:  # noqa: BLE001 — a broken config hides the tool
        return False


VisionAnalyzeTool: Tool = build_tool(
    name="vision_analyze",
    input_schema={
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "Local file path to the image (png, jpg, gif, webp).",
            },
            "question": {
                "type": "string",
                "description": (
                    "Your specific question about the image. Be concrete — this "
                    "is what makes the answer better than a generic description."
                ),
            },
        },
        "required": ["image_url", "question"],
        # Tolerant on dispatch, like AdvisorTool: registry validation runs
        # this against whatever the model actually emitted, and some workers
        # decorate calls with extra fields. Failing a whole consultation over
        # an ignored key trades a working tool for schema pedantry.
        "additionalProperties": True,
    },
    call=_vision_call,
    prompt=(
        "Ask a vision-capable model about a local image and get a text answer.\n\n"
        "Use this when you need to see an image but your own model cannot, or "
        "when an image was described to you and you need a closer look at "
        "something specific. Always pass a concrete question — 'what is the "
        "total amount and VAT on this invoice?' beats 'describe this'."
    ),
    description=lambda _input: "Ask a vision model about an image",
    is_enabled=_vision_enabled,
    # NOT is_read_only, despite only reading a file locally: this tool
    # SHIPS those bytes to a third-party vendor and spends money doing it.
    # ``is_read_only`` is the flag auto-approval logic keys off, and a
    # screenshot routinely contains credentials — marking this read-only
    # would make it a no-prompt exfiltration path for anything under
    # ``allowed_roots``. NOT concurrency-safe for the same reason plus one
    # more: nothing here bounds fan-out, so a parallel batch is N paid calls.
    max_result_size_chars=50_000,
)
