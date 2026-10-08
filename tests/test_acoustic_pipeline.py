"""
tests/test_acoustic_pipeline.py — Unit tests for acoustic_pipeline.py.

Tests cover:
  1. VAD RMS calculation, silence detection, and speech boundary detection.
  2. SentenceChunker streaming token accumulation, abbreviation protection, and flushing.
  3. Shared Whisper model loader & in-memory transcription.
  4. Piper voice discovery, synthesis, and fallback mechanisms.
  5. AcousticPipeline facade queueing and playback lifecycle.
  6. VoiceLoopWorker execution, VAD cycle, streaming TTS integration, and cancellation.
"""
from __future__ import annotations

import struct
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from acoustic_pipeline import (
    AcousticPipeline,
    SentenceChunker,
    VoiceLoopWorker,
    compute_audio_rms,
    discover_available_voices,
    get_acoustic_pipeline,
    is_speech_active,
    synthesize_text_to_wav,
    transcribe_pcm_audio,
)


class TestAudioRMSAndVAD(unittest.TestCase):
    """Verify audio RMS energy computation and VAD thresholding."""

    def test_empty_audio_returns_zero(self):
        assert compute_audio_rms(b"") == 0.0
        assert compute_audio_rms(b"\x00") == 0.0

    def test_silence_returns_zero(self):
        silence = b"\x00\x00" * 512
        assert compute_audio_rms(silence) == 0.0
        assert not is_speech_active(silence)

    def test_synthetic_sine_wave_energy(self):
        # Generate 16000 Hz sine wave at 440 Hz
        sample_rate = 16000
        duration = 0.1  # seconds
        freq = 440.0
        amplitude = 10000.0  # Well above 120 threshold

        t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        sine = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.int16)
        raw_pcm = sine.tobytes()

        rms = compute_audio_rms(raw_pcm)
        assert rms > 5000.0
        assert is_speech_active(raw_pcm, threshold=120.0)

    def test_below_threshold_is_not_speech(self):
        low_amplitude = np.full(512, 10, dtype=np.int16).tobytes()
        rms = compute_audio_rms(low_amplitude)
        assert rms == 10.0
        assert not is_speech_active(low_amplitude, threshold=120.0)


class TestSentenceChunker(unittest.TestCase):
    """Verify streaming token sentence chunking and abbreviation protection."""

    def test_single_complete_sentence(self):
        chunker = SentenceChunker()
        assert chunker.add_token("Hello ") == []
        assert chunker.add_token("world! ") == ["Hello world!"]
        assert chunker.flush() == []

    def test_multiple_sentences_in_stream(self):
        chunker = SentenceChunker()
        s1 = chunker.add_token("First sentence. Second ")
        assert s1 == ["First sentence."]

        s2 = chunker.add_token("sentence? Third ")
        assert s2 == ["Second sentence?"]

        s3 = chunker.add_token("sentence! ")
        assert s3 == ["Third sentence!"]

        assert chunker.flush() == []

    def test_abbreviation_protection(self):
        chunker = SentenceChunker()
        # "Dr. Smith arrived." should not split after "Dr."
        s1 = chunker.add_token("Dr. ")
        assert s1 == []

        s2 = chunker.add_token("Smith arrived. ")
        assert s2 == ["Dr. Smith arrived."]

    def test_flush_remaining_unpunctuated_text(self):
        chunker = SentenceChunker()
        chunker.add_token("This is a sentence without ending punctuation")
        flushed = chunker.flush()
        assert flushed == ["This is a sentence without ending punctuation"]


class TestWhisperTranscription(unittest.TestCase):
    """Verify in-memory Whisper STT without temporary files."""

    def test_empty_pcm_returns_empty_string(self):
        assert transcribe_pcm_audio(b"") == ""

    @patch("acoustic_pipeline.get_shared_whisper")
    def test_transcribe_pcm_audio_success(self, mock_get_whisper):
        mock_model = MagicMock()
        mock_model.transcribe.return_value = {"text": "   offline voice transcription works   "}
        mock_get_whisper.return_value = mock_model

        # 16-bit PCM buffer of 100 samples
        pcm_bytes = struct.pack("<100h", *([1000] * 100))
        result = transcribe_pcm_audio(pcm_bytes)

        assert result == "offline voice transcription works"
        mock_model.transcribe.assert_called_once()
        # Verify passed input is float32 numpy array
        args, kwargs = mock_model.transcribe.call_args
        assert isinstance(args[0], np.ndarray)
        assert args[0].dtype == np.float32

    @patch("acoustic_pipeline.get_shared_whisper", return_value=None)
    def test_transcribe_pcm_audio_when_model_missing(self, mock_get_whisper):
        pcm_bytes = struct.pack("<50h", *([100] * 50))
        result = transcribe_pcm_audio(pcm_bytes)
        assert "unavailable" in result


class TestPiperTTSBackend(unittest.TestCase):
    """Verify Piper voice discovery and synthesis."""

    def test_discover_available_voices_structure(self):
        voices = discover_available_voices()
        assert isinstance(voices, dict)
        # In this workspace, voice_models/ contains .onnx files
        if voices:
            for name, path in voices.items():
                assert path.endswith(".onnx")
                assert name in path

    def test_synthesize_empty_text_returns_none(self):
        assert synthesize_text_to_wav("") is None
        assert synthesize_text_to_wav("   ") is None

    @patch("acoustic_pipeline.get_piper_voice")
    def test_synthesize_text_to_wav_success(self, mock_get_voice):
        mock_voice = MagicMock()
        mock_chunk = MagicMock()
        mock_chunk.audio_int16_bytes = b"\x00\x01" * 100
        mock_voice.synthesize.return_value = [mock_chunk]
        mock_get_voice.return_value = mock_voice

        wav_bytes = synthesize_text_to_wav("Hello from Jarvis.")
        assert wav_bytes is not None
        assert wav_bytes[:4] == b"RIFF"
        assert b"WAVE" in wav_bytes[:16]


class TestAcousticPipelineFacade(unittest.TestCase):
    """Verify singleton lifecycle and queue operations."""

    def test_singleton_getter(self):
        p1 = get_acoustic_pipeline()
        p2 = get_acoustic_pipeline()
        assert p1 is p2
        assert isinstance(p1, AcousticPipeline)

    def test_speak_queues_and_stop_playback_clears(self):
        pipeline = get_acoustic_pipeline()
        with patch("acoustic_pipeline.synthesize_text_to_wav", return_value=b"RIFF_FAKE_WAV"):
            pipeline.speak("Test sentence.")
            pipeline.stop_playback()
            assert pipeline._playback_queue.empty()


class TestVoiceLoopWorker(unittest.TestCase):
    """Verify full-duplex VoiceLoopWorker lifecycle."""

    def test_worker_initialization(self):
        worker = VoiceLoopWorker(controller=None, auto_speak=True)
        assert worker.auto_speak is True
        assert not worker.cancel_event.is_set()

    def test_worker_stop_sets_event(self):
        worker = VoiceLoopWorker(controller=None)
        worker.stop()
        assert worker.cancel_event.is_set()


class TestDuplexBargeIn(unittest.TestCase):
    """REGRESSION GUARD for DuplexBargeInController and streaming audio interruption."""

    def test_duplex_threshold_multiplier(self):
        from acoustic_pipeline import DuplexBargeInController
        ctrl = DuplexBargeInController(base_threshold=100.0, threshold_multiplier=2.5)
        self.assertEqual(ctrl.get_effective_threshold(is_playing=False), 100.0)
        self.assertEqual(ctrl.get_effective_threshold(is_playing=True), 250.0)

    def test_duplex_barge_in_speech_detection(self):
        from acoustic_pipeline import DuplexBargeInController
        ctrl = DuplexBargeInController(
            base_threshold=120.0,
            threshold_multiplier=2.0,
            min_speech_duration=0.1,
            chunk_duration=0.05,  # Requires 2 consecutive chunks
        )

        loud_chunk = np.full(512, 1000, dtype=np.int16).tobytes()  # High energy
        quiet_chunk = np.full(512, 10, dtype=np.int16).tobytes()   # Low energy

        # Chunk 1 of loud speech while playing (not yet triggered)
        self.assertFalse(ctrl.process_chunk(loud_chunk, is_playing=True))
        # Chunk 2 of loud speech while playing (triggers barge-in!)
        self.assertTrue(ctrl.process_chunk(loud_chunk, is_playing=True))

        # Quiet chunk resets consecutive counter
        self.assertFalse(ctrl.process_chunk(quiet_chunk, is_playing=True))
        # Need 2 more chunks to trigger again
        self.assertFalse(ctrl.process_chunk(loud_chunk, is_playing=True))
        self.assertTrue(ctrl.process_chunk(loud_chunk, is_playing=True))

    def test_pipeline_is_playing_state(self):
        pipeline = get_acoustic_pipeline()
        pipeline.stop_playback()
        self.assertFalse(pipeline.is_playing())

    def test_worker_barge_in_signal_defined(self):
        worker = VoiceLoopWorker(controller=None)
        self.assertTrue(hasattr(worker, "barge_in_signal"))

    def test_two_stage_ducking_state(self):
        """REGRESSION GUARD: Verify soft ducking triggers before hard barge-in."""
        from acoustic_pipeline import DuplexBargeInController
        ctrl = DuplexBargeInController(
            base_threshold=100.0,
            threshold_multiplier=1.0,
            duck_duration=0.05,        # 1 chunk for ducking
            min_speech_duration=0.15,   # 3 chunks for interruption
            chunk_duration=0.05,
        )

        loud_chunk = np.full(512, 1000, dtype=np.int16).tobytes()
        quiet_chunk = np.full(512, 10, dtype=np.int16).tobytes()

        # Chunk 1: Stage 1 ducking active, no hard interruption yet
        interrupted = ctrl.process_chunk(loud_chunk, is_playing=True)
        self.assertFalse(interrupted)
        self.assertTrue(ctrl.is_ducked)

        # Chunk 2: Still ducked, not yet interrupted
        interrupted = ctrl.process_chunk(loud_chunk, is_playing=True)
        self.assertFalse(interrupted)
        self.assertTrue(ctrl.is_ducked)

        # Chunk 3: Stage 2 hard interruption triggered!
        interrupted = ctrl.process_chunk(loud_chunk, is_playing=True)
        self.assertTrue(interrupted)

        # Reset on quiet
        ctrl.process_chunk(quiet_chunk, is_playing=True)
        self.assertFalse(ctrl.is_ducked)

    def test_attenuate_wav_bytes(self):
        """REGRESSION GUARD: Audio attenuation scales int16 PCM samples."""
        from acoustic_pipeline import attenuate_wav_bytes
        import io, wave

        # Generate a small 16-bit WAV with known sample values
        samples = np.full(100, 10000, dtype=np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(samples.tobytes())
        raw_wav = buf.getvalue()

        attenuated = attenuate_wav_bytes(raw_wav, volume_scale=0.25)
        self.assertIsNotNone(attenuated)
        self.assertNotEqual(len(attenuated), 0)

        with wave.open(io.BytesIO(attenuated), "rb") as rf:
            out_frames = rf.readframes(rf.getnframes())
            out_samples = np.frombuffer(out_frames, dtype=np.int16)
            # Scaled 10000 * 0.25 = 2500
            self.assertTrue(np.allclose(out_samples, 2500, atol=2))

    def test_pipeline_ducking_lifecycle(self):
        """REGRESSION GUARD: Pipeline set_ducking state changes."""
        pipeline = get_acoustic_pipeline()
        pipeline.set_ducking(True, 0.25)
        self.assertTrue(pipeline.is_ducked())
        pipeline.set_ducking(False)
        self.assertFalse(pipeline.is_ducked())

