"""Tests for Voice subsystem."""
from __future__ import annotations

import unittest

from src.services.voice.detection import (
    VoiceActivityConfig,
    VoiceActivityDetector,
    VoiceActivityState,
)
from src.services.voice.stt import STTConfig, STTResult


class TestSTTTypes(unittest.TestCase):
    def test_stt_config_defaults(self) -> None:
        config = STTConfig()
        self.assertEqual(config.language, "en")
        self.assertEqual(config.sample_rate, 16000)
        self.assertTrue(config.interim_results)

    def test_stt_result(self) -> None:
        result = STTResult(text="hello world", confidence=0.95, is_final=True)
        self.assertEqual(result.text, "hello world")
        self.assertEqual(result.confidence, 0.95)
        self.assertTrue(result.is_final)


class TestVoiceActivityDetector(unittest.TestCase):
    def test_initial_state(self) -> None:
        vad = VoiceActivityDetector()
        self.assertEqual(vad.state, VoiceActivityState.IDLE)
        self.assertFalse(vad.is_speaking)

    def test_start_stop(self) -> None:
        vad = VoiceActivityDetector()
        vad.start()
        self.assertEqual(vad.state, VoiceActivityState.LISTENING)
        vad.stop()
        self.assertEqual(vad.state, VoiceActivityState.IDLE)

    def test_speech_detection(self) -> None:
        config = VoiceActivityConfig(
            min_speech_duration_ms=0,  # Instant detection for testing
            speech_threshold_db=-25.0,
        )
        vad = VoiceActivityDetector(config=config)
        vad.start()

        # Loud audio → speaking
        state = vad.process_audio_level(-10.0)
        self.assertEqual(state, VoiceActivityState.SPEAKING)
        self.assertTrue(vad.is_speaking)

    def test_silence_after_speech(self) -> None:
        config = VoiceActivityConfig(
            min_speech_duration_ms=0,
            max_silence_duration_ms=0,  # Instant silence detection
        )
        vad = VoiceActivityDetector(config=config)
        vad.start()

        # Speech then silence
        vad.process_audio_level(-10.0)
        self.assertTrue(vad.is_speaking)

        state = vad.process_audio_level(-50.0)
        self.assertEqual(state, VoiceActivityState.PROCESSING)

    def test_idle_ignores_audio(self) -> None:
        vad = VoiceActivityDetector()
        state = vad.process_audio_level(-10.0)
        self.assertEqual(state, VoiceActivityState.IDLE)

    def test_state_change_listener(self) -> None:
        vad = VoiceActivityDetector()
        states: list[VoiceActivityState] = []
        unsub = vad.on_state_change(lambda s: states.append(s))

        vad.start()
        vad.stop()
        self.assertEqual(states, [VoiceActivityState.LISTENING, VoiceActivityState.IDLE])

        unsub()
        vad.start()
        self.assertEqual(len(states), 2)  # Listener removed


class TestVoiceActivityConfig(unittest.TestCase):
    def test_defaults(self) -> None:
        config = VoiceActivityConfig()
        self.assertEqual(config.silence_threshold_db, -40.0)
        self.assertEqual(config.speech_threshold_db, -25.0)
        self.assertEqual(config.min_speech_duration_ms, 200.0)
        self.assertEqual(config.max_silence_duration_ms, 1500.0)

    def test_custom(self) -> None:
        config = VoiceActivityConfig(speech_threshold_db=-30.0, sample_rate=8000)
        self.assertEqual(config.speech_threshold_db, -30.0)
        self.assertEqual(config.sample_rate, 8000)


if __name__ == "__main__":
    unittest.main()
