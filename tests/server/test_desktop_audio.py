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


@pytest.mark.asyncio
async def test_transcribe_no_provider_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop_audio, "_stt_config", lambda: {})
    monkeypatch.setattr(desktop_audio, "_voice_config", lambda: {})
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
    monkeypatch.setattr(desktop_audio, "_stt_config", lambda: {})
    monkeypatch.setattr(desktop_audio, "_voice_config", lambda: {})

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["has_multipart"] = b"whisper-1" in request.content
        return httpx.Response(200, json={"text": "hello world"})

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: orig_client(*a, **{**k, "transport": transport}))

    result = await transcribe_data_url(_wav_data_url(), "audio/wav")
    assert result.ok is True
    assert result.transcript == "hello world"
    assert result.provider == "openai"
    # openai STT defaults to the CANONICAL OpenAI endpoint (not the provider's
    # chat base_url, which may be a proxy without /audio/transcriptions).
    assert captured["url"] == "https://api.openai.com/v1/audio/transcriptions"
    assert captured["auth"] == "Bearer k"
    assert captured["has_multipart"] is True


@pytest.mark.asyncio
async def test_transcribe_honors_explicit_stt_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """stt.openai.{base_url,api_key,model} override the provider + canonical
    defaults — this is how a user points STT at their own endpoint/model."""
    monkeypatch.setattr("src.config.get_provider_config", lambda pid: {})
    monkeypatch.setattr(desktop_audio, "_stt_config", lambda: {
        "provider": "openai",
        "openai": {"base_url": "https://my.stt/v1", "api_key": "sk-real", "model": "gpt-4o-transcribe"},
    })
    monkeypatch.setattr(desktop_audio, "_voice_config", lambda: {})

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["model_ok"] = b"gpt-4o-transcribe" in request.content
        return httpx.Response(200, json={"text": "hi"})

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: orig_client(*a, **{**k, "transport": transport}))

    result = await transcribe_data_url(_wav_data_url(), "audio/wav")
    assert result.ok is True
    assert captured["url"] == "https://my.stt/v1/audio/transcriptions"
    assert captured["auth"] == "Bearer sk-real"
    assert captured["model_ok"] is True


@pytest.mark.asyncio
async def test_transcribe_model_rejection_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.config.get_provider_config",
        lambda pid: {"api_key": "k"} if pid == "openai" else {},
    )
    monkeypatch.setattr(desktop_audio, "_stt_config", lambda: {})
    monkeypatch.setattr(desktop_audio, "_voice_config", lambda: {})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "Invalid model name passed in model=whisper-1"})

    transport = httpx.MockTransport(handler)
    orig_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: orig_client(*a, **{**k, "transport": transport}))

    result = await transcribe_data_url(_wav_data_url(), "audio/wav")
    assert result.ok is False
    assert "Settings → Voice" in result.error


@pytest.mark.asyncio
async def test_transcribe_empty_clip() -> None:
    result = await transcribe_data_url("data:audio/wav;base64,")
    assert result.ok is False
    assert "empty" in result.error.lower()


def test_stt_model_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    # Per-provider stt.<p>.model wins.
    monkeypatch.setattr("src.config.load_config",
                        lambda: {"stt": {"openai": {"model": "gpt-4o-transcribe"}}})
    assert desktop_audio._stt_model_for("openai") == "gpt-4o-transcribe"
    # Legacy voice.stt_model still honored.
    monkeypatch.setattr("src.config.load_config",
                        lambda: {"voice": {"stt_model": "whisper-large-v3"}})
    assert desktop_audio._stt_model_for("openai") == "whisper-large-v3"
    # Default when nothing set.
    monkeypatch.setattr("src.config.load_config", lambda: {})
    assert desktop_audio._stt_model_for("openai") == "whisper-1"
    assert desktop_audio._stt_model_for("groq") == "whisper-large-v3"
