"""Speech-to-text for the desktop composer's mic button.

The desktop records a clip and POSTs it to ``/api/audio/transcribe`` as a
base64 data URL, expecting ``{ok, transcript, provider?}``. There is no
concrete STT provider in the agent core (``src/services/voice/stt.py`` is an
abstract interface), so this implements transcription directly against an
OpenAI-compatible ``/audio/transcriptions`` endpoint (Whisper) using a
configured provider's key + base URL — the same providers the agent already
talks to. Degrades with an actionable message when none can transcribe.
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Providers whose OpenAI-compatible base URL exposes /audio/transcriptions.
# openai (incl. LiteLLM/Azure gateways) and groq both host Whisper; others
# 404 the route, so we don't guess.
_STT_PROVIDERS = ("openai", "groq")
_STT_MODEL = {"openai": "whisper-1", "groq": "whisper-large-v3"}


@dataclass
class TranscriptionResult:
    ok: bool
    transcript: str = ""
    provider: str | None = None
    error: str | None = None


def _decode_data_url(data_url: str) -> tuple[bytes, str] | None:
    """(bytes, mime) from a data: URL, or None if it isn't one.

    MediaRecorder produces MIME types with parameters, e.g.
    ``data:audio/webm;codecs=opus;base64,<data>`` — the mediatype segment can
    carry its own ``;param=value`` pairs before the ``;base64`` marker. Split
    on the FIRST comma (the mediatype can't contain one) rather than a regex
    that assumes the mediatype is parameter-free.
    """
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        return None
    comma = data_url.find(",")
    if comma == -1:
        return None
    header = data_url[len("data:"):comma]  # e.g. "audio/webm;codecs=opus;base64"
    payload = data_url[comma + 1:]
    is_base64 = header.rstrip().endswith(";base64")
    if is_base64:
        header = header.rstrip()[: -len(";base64")]
    # Base mediatype is everything before the first parameter (";codecs=…").
    mime = header.split(";", 1)[0].strip() or "audio/webm"
    try:
        if is_base64:
            raw = base64.b64decode(payload)
        else:
            from urllib.parse import unquote_to_bytes

            raw = unquote_to_bytes(payload)
    except (binascii.Error, ValueError):
        return None
    return raw, mime


def _ext_for(mime: str) -> str:
    return {
        "audio/webm": "webm",
        "audio/ogg": "ogg",
        "audio/mp4": "mp4",
        "audio/mpeg": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
    }.get(mime.split(";")[0].strip(), "webm")


# Canonical transcription endpoints. Whisper lives here regardless of a
# provider's CHAT base URL — a user's `openai` block may point at a chat-only
# proxy (e.g. LiteLLM) that has no /audio/transcriptions route, so STT defaults
# to the provider's real audio endpoint unless the config overrides it.
_STT_BASE_URL = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
}


def _stt_config() -> dict[str, Any]:
    try:
        from src.config import load_config

        return (load_config() or {}).get("stt") or {}
    except Exception:  # noqa: BLE001
        return {}


def _voice_config() -> dict[str, Any]:
    try:
        from src.config import load_config

        return (load_config() or {}).get("voice") or {}
    except Exception:  # noqa: BLE001
        return {}


def _configured_stt_provider() -> str | None:
    """An explicit STT provider from config (``stt.provider`` /
    ``voice.stt_provider``), if set."""
    return _stt_config().get("provider") or _voice_config().get("stt_provider")


def _stt_model_for(provider: str) -> str:
    """The transcription model id: ``stt.<provider>.model`` → ``stt.model`` →
    ``voice.stt_model`` → the provider's default (whisper-1 / whisper-large-v3)."""
    stt = _stt_config()
    per = stt.get(provider) if isinstance(stt.get(provider), dict) else {}
    override = per.get("model") or stt.get("model") or _voice_config().get("stt_model")
    return str(override) if override else _STT_MODEL.get(provider, "whisper-1")


def _resolve_stt() -> tuple[str, str, str] | None:
    """(provider_id, base_url, api_key) for transcription, or None.

    Resolution, most specific first:
      * ``stt.<provider>`` block — ``base_url`` / ``api_key`` overrides,
      * ``voice.stt_base_url`` / ``voice.stt_api_key`` (flat overrides),
      * the provider's own config block (its chat ``api_key``),
      * the canonical audio endpoint for the provider (``_STT_BASE_URL``).
    The api_key is required; the base URL always has a canonical fallback so
    "use the OpenAI endpoint with an OpenAI key" works with just a key set.
    """
    from src.config import get_provider_config

    configured = _configured_stt_provider()
    candidates = ([configured] if configured else []) + list(_STT_PROVIDERS)
    stt = _stt_config()
    voice = _voice_config()

    for pid in candidates:
        if not pid:
            continue
        per = stt.get(pid) if isinstance(stt.get(pid), dict) else {}
        try:
            prov_cfg = get_provider_config(pid) or {}
        except Exception:  # noqa: BLE001
            prov_cfg = {}
        key = per.get("api_key") or voice.get("stt_api_key") or prov_cfg.get("api_key")
        if not key:
            continue
        base = (
            per.get("base_url")
            or voice.get("stt_base_url")
            or _STT_BASE_URL.get(pid)
            or prov_cfg.get("base_url")
        )
        if not base:
            continue
        return str(pid), str(base).rstrip("/"), str(key)
    return None


async def transcribe_data_url(data_url: str, mime_type: str | None = None) -> TranscriptionResult:
    """Transcribe a recorded clip via an OpenAI-compatible Whisper endpoint."""
    decoded = _decode_data_url(data_url)
    if decoded is None:
        return TranscriptionResult(ok=False, error="invalid audio payload")
    audio, sniffed_mime = decoded
    mime = mime_type or sniffed_mime
    if not audio:
        return TranscriptionResult(ok=False, error="empty audio clip")

    picked = _resolve_stt()
    if picked is None:
        return TranscriptionResult(
            ok=False,
            error="Voice input needs a speech-to-text provider. Add an OpenAI "
            "or Groq API key (they host Whisper), then pick the transcription "
            "model in Settings → Voice.",
        )
    provider, base, key = picked
    filename = f"clip.{_ext_for(mime)}"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{base}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (filename, audio, mime)},
                data={"model": _stt_model_for(provider)},
            )
    except httpx.HTTPError as exc:
        logger.warning("desktop: transcription request failed", exc_info=True)
        return TranscriptionResult(ok=False, provider=provider, error=str(exc))

    if resp.status_code >= 400:
        detail = resp.text[:400]
        lowered = detail.lower()
        # A chat-only OpenAI-compatible gateway (e.g. a LiteLLM proxy with no
        # Whisper route) rejects the transcription model. Say what's wrong and
        # what to do, not the raw upstream JSON.
        if resp.status_code in (400, 404) and (
            "model" in lowered or "not found" in lowered or "transcription" in lowered
        ):
            return TranscriptionResult(
                ok=False, provider=provider,
                error=f"The '{provider}' endpoint rejected the transcription "
                "model. Pick a valid one in Settings → Voice (Transcription "
                "model), or point the provider at a Whisper-capable base URL.",
            )
        return TranscriptionResult(
            ok=False, provider=provider,
            error=f"transcription failed ({resp.status_code}): {detail}",
        )
    try:
        text = str((resp.json() or {}).get("text", "")).strip()
    except ValueError:
        text = resp.text.strip()
    return TranscriptionResult(ok=True, provider=provider, transcript=text)


__all__ = ["TranscriptionResult", "transcribe_data_url"]
