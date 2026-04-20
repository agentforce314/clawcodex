"""Voice subsystem.

Provides speech-to-text and voice activity detection.
Mirrors TypeScript voice/ directory.
"""
from __future__ import annotations

from .stt import STTProvider, STTResult, STTConfig
from .detection import VoiceActivityDetector, VoiceActivityState

__all__ = [
    "STTConfig",
    "STTProvider",
    "STTResult",
    "VoiceActivityDetector",
    "VoiceActivityState",
]
