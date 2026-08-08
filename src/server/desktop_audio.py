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
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Providers whose OpenAI-compatible base URL exposes /audio/transcriptions.
# openai (incl. LiteLLM/Azure gateways) and groq both host Whisper; others
# 404 the route, so we don't guess.
_STT_PROVIDERS = ("openai", "groq")
_STT_MODEL = {"openai": "whisper-1", "groq": "whisper-large-v3"}
_DATA_URL_RE = re.compile(r"^data:([^;,]*)(;base64)?,(.*)$", re.DOTALL)


@dataclass
class TranscriptionResult:
    ok: bool
    transcript: str = ""
    provider: str | None = None
    error: str | None = None


def _decode_data_url(data_url: str) -> tuple[bytes, str] | None:
    """(bytes, mime) from a data: URL, or None if it isn't one."""
    match = _DATA_URL_RE.match(data_url or "")
    if not match:
        return None
    mime = match.group(1) or "audio/webm"
    payload = match.group(3)
    try:
        raw = base64.b64decode(payload) if match.group(2) else payload.encode("utf-8")
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


def _configured_stt_provider() -> str | None:
    """An explicit ``voice.stt_provider`` from config, if set."""
    try:
        from src.config import load_config

        voice = (load_config() or {}).get("voice") or {}
        stt = (load_config() or {}).get("stt") or {}
        return voice.get("stt_provider") or stt.get("provider")
    except Exception:  # noqa: BLE001
        return None


def _stt_model_for(provider: str) -> str:
    """The transcription model id: config override, else the provider default."""
    try:
        from src.config import load_config

        cfg = load_config() or {}
        override = (cfg.get("voice") or {}).get("stt_model") or (cfg.get("stt") or {}).get("model")
        if override:
            return str(override)
    except Exception:  # noqa: BLE001
        pass
    return _STT_MODEL.get(provider, "whisper-1")


def _pick_provider() -> tuple[str, str, str] | None:
    """(provider_id, base_url, api_key) of the STT-capable provider to use.

    An explicit ``voice.stt_provider`` wins; otherwise the first configured
    provider known to host Whisper.
    """
    from src.config import get_provider_config

    configured = _configured_stt_provider()
    candidates = ([configured] if configured else []) + list(_STT_PROVIDERS)
    for pid in candidates:
        if not pid:
            continue
        try:
            cfg = get_provider_config(pid) or {}
        except Exception:  # noqa: BLE001
            continue
        key = cfg.get("api_key")
        base = cfg.get("base_url")
        if key and base:
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

    picked = _pick_provider()
    if picked is None:
        return TranscriptionResult(
            ok=False,
            error="Voice input needs a speech-to-text provider. Configure an "
            "OpenAI or Groq API key (they host Whisper) in ~/.clawcodex/config.json.",
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
                error=f"The '{provider}' endpoint doesn't offer a speech-to-text "
                "model. Point an OpenAI or Groq provider at a Whisper-capable "
                "base URL, or set voice.stt_model in ~/.clawcodex/config.json.",
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
