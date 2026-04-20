"""Speech-to-text provider.

Mirrors TypeScript voice/stt.ts — abstract STT interface and configuration.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class STTConfig:
    """Speech-to-text configuration."""
    language: str = "en"
    model: str = "whisper-1"
    sample_rate: int = 16000
    encoding: str = "pcm_s16le"
    interim_results: bool = True


@dataclass
class STTResult:
    """Result of a speech-to-text transcription."""
    text: str
    confidence: float = 1.0
    is_final: bool = True
    language: str = "en"
    duration_ms: float = 0.0


class STTProvider(ABC):
    """Abstract speech-to-text provider."""

    @abstractmethod
    async def transcribe(self, audio_data: bytes, config: STTConfig | None = None) -> STTResult:
        """Transcribe audio data to text."""

    @abstractmethod
    async def start_streaming(self, config: STTConfig | None = None) -> None:
        """Start streaming transcription."""

    @abstractmethod
    async def feed_audio(self, chunk: bytes) -> STTResult | None:
        """Feed an audio chunk. Returns interim result if available."""

    @abstractmethod
    async def stop_streaming(self) -> STTResult:
        """Stop streaming and return final result."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources."""
