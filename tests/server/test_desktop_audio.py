"""Tests for desktop voice transcription (QA round 2)."""

from __future__ import annotations

import base64

import httpx
import pytest

from src.server import desktop_audio
from src.server.desktop_audio import _decode_data_url, transcribe_data_url


def _wav_data_url() -> str:
    raw = b"RIFFxxxxWAVEfmt " + b"\x00" * 32
    return "data:audio/wav;base64," + base64.b64encode(raw).decode()


def test_decode_data_url_roundtrip() -> None:
    decoded = _decode_data_url(_wav_data_url())
    assert decoded is not None
    audio, mime = decoded
    assert mime == "audio/wav"
    assert audio.startswith(b"RIFF")


def test_decode_rejects_non_data_url() -> None:
    assert _decode_data_url("not a data url") is None


def test_decode_mediarecorder_mime_with_codec_param() -> None:
    """Regression: MediaRecorder emits ``audio/webm;codecs=opus`` — the mediatype
    carries a ``;codecs=`` param before ``;base64``. The old regex failed this
    and the mic showed 'invalid audio payload'."""
    raw = b"\x1aE\xdf\xa3webm-bytes"
    for header in ("audio/webm;codecs=opus", "audio/ogg; codecs=opus", "audio/mp4"):
        url = f"data:{header};base64," + base64.b64encode(raw).decode()
        decoded = _decode_data_url(url)
        assert decoded is not None, header
        audio, mime = decoded
        assert audio == raw
        # Base mediatype only — params stripped.
        assert mime == header.split(";", 1)[0].strip()


@pytest.mark.asyncio
async def test_transcribe_no_provider_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop_audio, "_configured_stt_provider", lambda: None)
    monkeypatch.setattr("src.config.get_provider_config", lambda pid: {})
    result = await transcribe_data_url(_wav_data_url())
    assert result.ok is False
    assert "speech-to-text" in result.error.lower()


@pytest.mark.asyncio
async def test_transcribe_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.config.get_provider_config",
        lambda pid: {"api_key": "k", "base_url": "https://stt.example/v1"} if pid == "openai" else {},
    )
    monkeypatch.setattr(desktop_audio, "_configured_stt_provider", lambda: None)
    monkeypatch.setattr(desktop_audio, "_stt_model_for", lambda p: "whisper-1")

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["has_multipart"] = b"whisper-1" in request.content
        return httpx.Response(200, json={"text": "hello world"})

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient

    def client_factory(*a, **k):
        k["transport"] = transport
        return orig_client(*a, **k)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    result = await transcribe_data_url(_wav_data_url(), "audio/wav")
    assert result.ok is True
    assert result.transcript == "hello world"
    assert result.provider == "openai"
    assert captured["url"] == "https://stt.example/v1/audio/transcriptions"
    assert captured["auth"] == "Bearer k"
    assert captured["has_multipart"] is True


@pytest.mark.asyncio
async def test_transcribe_model_rejection_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.config.get_provider_config",
        lambda pid: {"api_key": "k", "base_url": "https://chat.example/v1"} if pid == "openai" else {},
    )
    monkeypatch.setattr(desktop_audio, "_configured_stt_provider", lambda: None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "Invalid model name passed in model=whisper-1"})

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: orig_client(*a, **{**k, "transport": transport}))

    result = await transcribe_data_url(_wav_data_url(), "audio/wav")
    assert result.ok is False
    assert "doesn't offer a speech-to-text model" in result.error


@pytest.mark.asyncio
async def test_transcribe_empty_clip() -> None:
    result = await transcribe_data_url("data:audio/wav;base64,")
    assert result.ok is False
    assert "empty" in result.error.lower()


def test_stt_model_config_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.config.load_config", lambda: {"voice": {"stt_model": "whisper-large-v3"}})
    assert desktop_audio._stt_model_for("openai") == "whisper-large-v3"
