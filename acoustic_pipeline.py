"""
acoustic_pipeline.py — Unified Multi-Modal Offline Acoustic Pipeline for KokertechAI.

Provides:
  - VAD (Voice Activity Detection) energy computation & trailing silence detection
  - Thread-safe lazy Whisper speech-to-text with in-memory array transcription
  - Piper ONNX Text-To-Speech engine with Windows SAPI fallback
  - Streaming sentence chunker for near-zero-latency TTS during LLM token streaming
  - Full-duplex VoiceLoopWorker combining VAD capture, Whisper STT, AI streaming,
    and real-time sentence-by-sentence acoustic playback
  - Graceful degradation across all audio hardware and Python environments.
"""
from __future__ import annotations

import io
import os
import queue
import re
import threading
import time
import wave
from typing import Any, Dict, List, Optional

import numpy as np

from config import CONFIG
from logging_config import get_logger

logger = get_logger(name="AcousticPipeline")

# Default acoustic constants
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHUNK_SIZE = 1024
DEFAULT_ENERGY_THRESHOLD = 120.0
DEFAULT_SILENCE_SECONDS = 1.2
DEFAULT_MAX_RECORD_SECONDS = 15.0

_WHISPER_MODEL = None
_WHISPER_LOCK = threading.Lock()
_PIPER_VOICE_CACHE: Dict[str, Any] = {}
_PIPER_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 1. Voice Activity Detection (VAD) & Energy Computation
# ---------------------------------------------------------------------------


def compute_audio_rms(audio_bytes: bytes) -> float:
    """Calculate the Root Mean Square (RMS) energy level of 16-bit PCM audio.

    Args:
        audio_bytes: Raw 16-bit signed PCM audio bytes.

    Returns:
        Float RMS energy level, or 0.0 if empty or invalid.
    """
    if not audio_bytes or len(audio_bytes) < 2:
        return 0.0
    try:
        samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return 0.0
        mean_sq = float(np.mean(samples ** 2))
        return float(np.sqrt(mean_sq))
    except (ValueError, TypeError):
        return 0.0


def is_speech_active(audio_bytes: bytes, threshold: float = DEFAULT_ENERGY_THRESHOLD) -> bool:
    """Return True if the RMS energy exceeds the speech detection threshold."""
    return compute_audio_rms(audio_bytes) >= threshold


# ---------------------------------------------------------------------------
# 2. Sentence-Level Streaming Chunker for Low-Latency TTS
# ---------------------------------------------------------------------------


class SentenceChunker:
    """Accumulates streamed LLM tokens and yields complete spoken sentences.

    Enables Piper TTS playback to start on the very first completed sentence
    while the model is still generating the rest of the response.
    """

    # Non-abbreviation sentence delimiters followed by whitespace or EOF
    _SPLIT_REGEX = re.compile(r"([.!?]+(?:\s+|\n+|$))")
    # Common abbreviations to prevent premature splitting
    _ABBREVIATIONS = (
        "e.g.", "i.e.", "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.",
        "vs.", "etc.", "approx.", "dept.", "est.", "fig.", "inc.", "ltd."
    )

    def __init__(self):
        self._buffer: str = ""

    def add_token(self, token: str) -> List[str]:
        """Add a token to the buffer and return any completed sentences."""
        self._buffer += token
        return self._extract_completed()

    def _extract_completed(self) -> List[str]:
        parts = self._SPLIT_REGEX.split(self._buffer)
        if len(parts) <= 1:
            return []

        completed: List[str] = []
        # Reassemble pairs (text + punctuation delimiter)
        idx = 0
        while idx < len(parts) - 1:
            chunk = parts[idx] + parts[idx + 1]
            idx += 2

            # Check abbreviation false positives
            lower_chunk = chunk.strip().lower()
            if any(lower_chunk.endswith(abbr) for abbr in self._ABBREVIATIONS):
                if completed:
                    completed[-1] += chunk
                else:
                    parts[idx] = chunk + parts[idx]
                continue

            cleaned = chunk.strip()
            if cleaned:
                completed.append(cleaned)

        # Remaining unfinalized segment stays in buffer
        self._buffer = parts[-1] if idx < len(parts) else ""
        return completed

    def flush(self) -> List[str]:
        """Flush and return any remaining text in the buffer."""
        remaining = self._buffer.strip()
        self._buffer = ""
        return [remaining] if remaining else []

    def is_empty(self) -> bool:
        """Return True if the internal buffer contains no unfinalized text."""
        return not bool(self._buffer.strip())


# ---------------------------------------------------------------------------
# 2.5. Duplex Barge-In Interruption Controller
# ---------------------------------------------------------------------------


class DuplexBargeInController:
    """Monitors live microphone VAD during AI speech playback to enable barge-in interruption.

    Uses a dynamic threshold multiplier (default 2.0x) during active TTS playback
    to avoid false positives caused by acoustic bleed from device speakers.
    """

    def __init__(
        self,
        base_threshold: float = DEFAULT_ENERGY_THRESHOLD,
        threshold_multiplier: float = 2.0,
        min_speech_duration: float = 0.2,
        chunk_duration: float = 0.064,  # 1024 samples @ 16kHz ~= 64ms
        duck_duration: float = 0.08,
        duck_volume: float = 0.25,
    ):
        self.base_threshold = float(base_threshold)
        self.threshold_multiplier = max(1.0, float(threshold_multiplier))
        self.min_speech_duration = max(0.05, float(min_speech_duration))
        self.chunk_duration = max(0.01, float(chunk_duration))
        self.duck_duration = max(0.02, float(duck_duration))
        self.duck_volume = max(0.05, min(1.0, float(duck_volume)))
        self._consecutive_speech_chunks = 0
        self._duck_chunks = max(1, int(round(self.duck_duration / self.chunk_duration)))
        self._required_chunks = max(self._duck_chunks, int(round(self.min_speech_duration / self.chunk_duration)))
        self.is_ducked: bool = False

    def get_effective_threshold(self, is_playing: bool) -> float:
        """Calculate the active VAD energy threshold based on playback state."""
        if is_playing:
            return self.base_threshold * self.threshold_multiplier
        return self.base_threshold

    def process_chunk(self, audio_chunk: bytes, is_playing: bool = False) -> bool:
        """Process an audio chunk and return True if user barge-in speech is detected.

        Two-stage processing:
        - Stage 1: Soft ducking when speech >= duck_duration.
        - Stage 2: Hard barge-in when speech >= min_speech_duration.
        """
        if not audio_chunk or len(audio_chunk) < 2:
            self._consecutive_speech_chunks = 0
            self.is_ducked = False
            return False

        rms = compute_audio_rms(audio_chunk)
        effective_threshold = self.get_effective_threshold(is_playing)

        if rms >= effective_threshold:
            self._consecutive_speech_chunks += 1
            if self._consecutive_speech_chunks >= self._duck_chunks:
                self.is_ducked = True
            if self._consecutive_speech_chunks >= self._required_chunks:
                return True
        else:
            self._consecutive_speech_chunks = 0
            self.is_ducked = False

        return False

    def reset(self) -> None:
        """Reset consecutive speech counters and ducking state."""
        self._consecutive_speech_chunks = 0
        self.is_ducked = False


# ---------------------------------------------------------------------------
# 3. Whisper Speech-to-Text Engine
# ---------------------------------------------------------------------------


def get_shared_whisper(model_name: Optional[str] = None):
    """Retrieve or lazily load the shared Whisper model singleton."""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL

    with _WHISPER_LOCK:
        if _WHISPER_MODEL is not None:
            return _WHISPER_MODEL

        target_model = model_name or CONFIG.get("whisper_model", "tiny.en")
        try:
            import whisper
            _WHISPER_MODEL = whisper.load_model(target_model)
            logger.info(f"Loaded shared Whisper model: '{target_model}'")
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            logger.warning(f"Whisper initialization failed ({target_model}): {exc}")
            _WHISPER_MODEL = None

        # Sync with wake_word module singleton if imported
        try:
            import wake_word
            wake_word._WHISPER_MODEL = _WHISPER_MODEL
        except (ImportError, AttributeError):
            pass

    return _WHISPER_MODEL


def transcribe_pcm_audio(
    pcm_bytes: bytes,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    language: str = "en",
) -> str:
    """Transcribe 16-bit mono PCM audio in memory using Whisper.

    Args:
        pcm_bytes: Raw 16-bit PCM byte data at 16 kHz mono.
        sample_rate: Audio sampling frequency (Hz).
        language: ISO-639-1 language code (default 'en').

    Returns:
        Transcribed text string or error description.
    """
    if not pcm_bytes:
        return ""

    model = get_shared_whisper()
    if model is None:
        return "Transcription unavailable: Whisper model not loaded."

    try:
        # Normalize 16-bit signed PCM to float32 in [-1.0, 1.0]
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        result = model.transcribe(
            samples,
            language=language,
            fp16=False,
            temperature=0.0,
        )
        return result.get("text", "").strip()
    except (RuntimeError, ValueError, OSError) as exc:
        logger.error(f"In-memory Whisper transcription error: {exc}")
        return f"Transcription error: {exc}"


# ---------------------------------------------------------------------------
# 4. Offline Piper / SAPI Text-to-Speech Engine
# ---------------------------------------------------------------------------


def discover_available_voices() -> Dict[str, str]:
    """Scan voice_models/ for Piper ONNX voices and map name -> file path."""
    voices: Dict[str, str] = {}
    base_dir = CONFIG.get("tts_model_dir") or os.path.dirname(__file__)
    voice_dir = os.path.join(base_dir, "voice_models")
    if os.path.isdir(voice_dir):
        for fname in sorted(os.listdir(voice_dir)):
            if fname.endswith(".onnx"):
                name = fname[:-5]
                voices[name] = os.path.join(voice_dir, fname)
    return voices


def get_piper_voice(voice_name: Optional[str] = None):
    """Retrieve or load a PiperVoice model instance from voice_models/."""
    voices = discover_available_voices()
    if not voices:
        return None

    chosen = voice_name or CONFIG.get("tts_voice")
    if not chosen or chosen not in voices:
        chosen = next(iter(voices.keys()))

    path = voices[chosen]
    with _PIPER_LOCK:
        if chosen in _PIPER_VOICE_CACHE:
            return _PIPER_VOICE_CACHE[chosen]
        try:
            import piper
            voice = piper.PiperVoice.load(path)
            _PIPER_VOICE_CACHE[chosen] = voice
            logger.info(f"Loaded Piper voice: '{chosen}' from {path}")
            return voice
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            logger.warning(f"Failed to load Piper voice ({chosen}): {exc}")
            return None


def synthesize_text_to_wav(
    text: str,
    voice_name: Optional[str] = None,
    rate_wpm: int = 180,
) -> Optional[bytes]:
    """Synthesize text into WAV byte data using Piper TTS.

    Args:
        text: Text sentence to synthesize.
        voice_name: Optional Piper voice name.
        rate_wpm: Speaking rate in words per minute.

    Returns:
        WAV byte payload or None on failure.
    """
    if not text.strip():
        return None

    voice = get_piper_voice(voice_name)
    if voice is None:
        return None

    try:
        import piper.config
        length_scale = 180.0 / max(rate_wpm, 1)
        length_scale = max(0.5, min(2.0, length_scale))
        syn_config = piper.config.SynthesisConfig(length_scale=length_scale)

        chunks = voice.synthesize(text, syn_config=syn_config)
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)      # 16-bit
            wf.setframerate(22050)
            for chunk in chunks:
                wf.writeframes(chunk.audio_int16_bytes)
        return wav_buf.getvalue()
    except (RuntimeError, ValueError, OSError, TypeError) as exc:
        logger.warning(f"Piper synthesis error: {exc}")
        return None


def attenuate_wav_bytes(wav_bytes: bytes, volume_scale: float = 0.25) -> bytes:
    """Attenuate PCM 16-bit audio in-memory for soft ducking."""
    if not wav_bytes or volume_scale >= 1.0 or volume_scale <= 0.0:
        return wav_bytes
    try:
        import wave
        with wave.open(io.BytesIO(wav_bytes), "rb") as r:
            params = r.getparams()
            frames = r.readframes(r.getnframes())
            if params.sampwidth == 2:
                import numpy as np
                samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
                samples = (samples * volume_scale).clip(-32768, 32767).astype(np.int16)
                out_buf = io.BytesIO()
                with wave.open(out_buf, "wb") as w:
                    w.setparams(params)
                    w.writeframes(samples.tobytes())
                return out_buf.getvalue()
    except (ImportError, OSError, ValueError, RuntimeError, wave.Error):
        # Graceful fallback: return unattenuated audio on processing failure
        return wav_bytes
    return wav_bytes


def play_wav_bytes(wav_bytes: bytes):
    """Play WAV audio bytes directly through Windows sound subsystem."""
    if not wav_bytes:
        return
    try:
        import winsound
        winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
    except (RuntimeError, OSError) as exc:
        logger.warning(f"Audio playback error: {exc}")


# ---------------------------------------------------------------------------
# 5. Acoustic Pipeline Facade
# ---------------------------------------------------------------------------


class AcousticPipeline:
    """Unified offline acoustic pipeline for STT, TTS, and VAD."""

    def __init__(self):
        self._lock = threading.Lock()
        self._playback_queue: queue.Queue = queue.Queue()
        self._playback_running: bool = True
        self._playback_active: bool = False
        self._is_ducked: bool = False
        self._duck_volume: float = 0.25
        self._playback_thread = threading.Thread(
            target=self._playback_worker, daemon=True
        )
        self._playback_thread.start()

    def set_ducking(self, ducked: bool, duck_volume: float = 0.25):
        """Enable or disable audio volume ducking."""
        with self._lock:
            self._is_ducked = ducked
            self._duck_volume = max(0.05, min(1.0, float(duck_volume)))

    def is_ducked(self) -> bool:
        """Return True if playback volume is currently ducked."""
        return getattr(self, "_is_ducked", False)

    def is_playing(self) -> bool:
        """Return True if audio playback is actively playing or queued in the buffer."""
        return self._playback_active or not self._playback_queue.empty()

    def _playback_worker(self):
        while self._playback_running:
            try:
                item = self._playback_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:
                break
            self._playback_active = True
            try:
                play_item = item
                if self.is_ducked():
                    play_item = attenuate_wav_bytes(item, getattr(self, "_duck_volume", 0.25))
                play_wav_bytes(play_item)
            finally:
                self._playback_active = False
                self._playback_queue.task_done()

    def speak(self, text: str, voice_name: Optional[str] = None, rate: int = 180):
        """Synthesize text and queue for non-blocking playback."""
        if not text.strip():
            return

        # Try Piper first
        wav_data = synthesize_text_to_wav(text, voice_name=voice_name, rate_wpm=rate)
        if wav_data:
            self._playback_queue.put(wav_data)
            return

        # Fall back to Windows SAPI
        try:
            import win32com.client
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            sapi_rate = max(-10, min(10, (rate - 180) // 12))
            speaker.Rate = sapi_rate
            speaker.Speak(text)
        except (RuntimeError, OSError, ValueError) as exc:
            logger.warning(f"SAPI TTS fallback error: {exc}")

    def stop_playback(self):
        """Clear queued audio playback items and immediately silence active playback."""
        with self._lock:
            while not self._playback_queue.empty():
                try:
                    self._playback_queue.get_nowait()
                    self._playback_queue.task_done()
                except queue.Empty:
                    break
            self._playback_active = False
            try:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
            except (RuntimeError, OSError, ValueError, AttributeError):
                pass

    def transcribe(self, pcm_bytes: bytes, language: str = "en") -> str:
        """Transcribe speech audio bytes to text."""
        return transcribe_pcm_audio(pcm_bytes, language=language)

    def is_mic_available(self) -> bool:
        """Return True if PyAudio and an active input device are found."""
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            count = pa.get_device_count()
            pa.terminate()
            return count > 0
        except (ImportError, OSError):
            return False

    def is_piper_available(self) -> bool:
        """Return True if Piper and voice models are available."""
        return len(discover_available_voices()) > 0

    def is_whisper_available(self) -> bool:
        """Return True if Whisper can be imported."""
        try:
            import whisper  # noqa: F401
            return True
        except (ImportError, ValueError):
            return False


_PIPELINE_INSTANCE: Optional[AcousticPipeline] = None


def get_acoustic_pipeline() -> AcousticPipeline:
    """Return the global AcousticPipeline singleton."""
    global _PIPELINE_INSTANCE
    if _PIPELINE_INSTANCE is None:
        _PIPELINE_INSTANCE = AcousticPipeline()
    return _PIPELINE_INSTANCE


# ---------------------------------------------------------------------------
# 6. Full-Duplex Voice Loop Worker
# ---------------------------------------------------------------------------

from PyQt6.QtCore import QThread, pyqtSignal


class VoiceLoopWorker(QThread):
    """Full-duplex voice interaction worker.

    Executes:
      1. VAD audio capture (hands-free: stops on natural trailing silence)
      2. Whisper STT transcription
      3. Controller AI prompt submission
      4. Sentence-by-sentence streaming Piper TTS playback
    """

    status_signal = pyqtSignal(str)
    transcription_signal = pyqtSignal(str)
    response_stream_signal = pyqtSignal(str)
    sentence_spoken_signal = pyqtSignal(str)
    barge_in_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        controller: Optional[Any] = None,
        silence_timeout: float = DEFAULT_SILENCE_SECONDS,
        energy_threshold: float = DEFAULT_ENERGY_THRESHOLD,
        max_duration: float = DEFAULT_MAX_RECORD_SECONDS,
        auto_speak: bool = True,
    ):
        super().__init__()
        self.controller = controller
        self.silence_timeout = silence_timeout
        self.energy_threshold = energy_threshold
        self.max_duration = max_duration
        self.auto_speak = auto_speak
        self.cancel_event = threading.Event()
        self.pipeline = get_acoustic_pipeline()

    def stop(self):
        """Request immediate cooperative cancellation."""
        self.cancel_event.set()
        self.requestInterruption()
        self.pipeline.stop_playback()

    def run(self):
        """Execute the full hands-free voice loop."""
        try:
            import pyaudio
        except ImportError:
            self.error_signal.emit("PyAudio not installed (pip install pyaudio).")
            return

        pa = pyaudio.PyAudio()
        stream = None

        try:
            self.status_signal.emit("🎙️ Listening... (Speak now)")
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=DEFAULT_SAMPLE_RATE,
                input=True,
                frames_per_buffer=DEFAULT_CHUNK_SIZE,
            )

            frames: List[bytes] = []
            speech_started = False
            silence_start: Optional[float] = None
            start_time = time.time()

            # Phase 1: VAD Recording Loop
            while not self.cancel_event.is_set() and not self.isInterruptionRequested():
                now = time.time()
                if now - start_time > self.max_duration:
                    break

                try:
                    chunk = stream.read(DEFAULT_CHUNK_SIZE, exception_on_overflow=False)
                except OSError:
                    break

                frames.append(chunk)
                rms = compute_audio_rms(chunk)

                if rms >= self.energy_threshold:
                    if not speech_started:
                        speech_started = True
                        self.status_signal.emit("🔴 Speech detected...")
                    silence_start = None
                else:
                    if speech_started:
                        if silence_start is None:
                            silence_start = now
                        elif now - silence_start >= self.silence_timeout:
                            # User finished speaking naturally
                            break

            if self.cancel_event.is_set() or self.isInterruptionRequested() or not frames:
                self.status_signal.emit("Voice loop stopped.")
                return

            # Phase 2: Whisper Transcription
            self.status_signal.emit("⏳ Transcribing audio with Whisper...")
            pcm_bytes = b"".join(frames)
            text = self.pipeline.transcribe(pcm_bytes)
            if not text or text.startswith("Transcription error"):
                self.status_signal.emit("No clear speech detected.")
                self.finished_signal.emit({"transcription": "", "reply": ""})
                return

            self.transcription_signal.emit(text)
            self.status_signal.emit(f"🗣️ Heard: \"{text}\"")

            if self.controller is None:
                self.finished_signal.emit({"transcription": text, "reply": ""})
                return

            # Phase 3: AI Inference & Sentence Streaming TTS with Duplex Barge-in
            self.status_signal.emit("🧠 Jarvis thinking...")
            chunker = SentenceChunker()
            duplex_enabled = CONFIG.get("duplex_barge_in_enabled", True)
            multiplier = float(CONFIG.get("duplex_energy_multiplier", 2.0))
            min_speech = float(CONFIG.get("duplex_min_speech_duration", 0.2))
            duck_dur = float(CONFIG.get("duplex_duck_duration", 0.08))
            duck_vol = float(CONFIG.get("duplex_duck_volume", 0.25))

            barge_in_controller = DuplexBargeInController(
                base_threshold=self.energy_threshold,
                threshold_multiplier=multiplier,
                min_speech_duration=min_speech,
                duck_duration=duck_dur,
                duck_volume=duck_vol,
            )

            barge_in_stop_event = threading.Event()
            barge_in_detected = False

            def _monitor_barge_in():
                nonlocal barge_in_detected
                while not self.cancel_event.is_set() and not barge_in_stop_event.is_set():
                    if stream is None:
                        break
                    try:
                        chunk = stream.read(DEFAULT_CHUNK_SIZE, exception_on_overflow=False)
                    except (OSError, RuntimeError):
                        break

                    is_playing = self.pipeline.is_playing()
                    was_ducked = barge_in_controller.is_ducked
                    interrupted = barge_in_controller.process_chunk(chunk, is_playing=is_playing)
                    if interrupted:
                        logger.info("⚡ Duplex barge-in: User speech interrupted AI playback.")
                        barge_in_detected = True
                        self.pipeline.set_ducking(False)
                        self.cancel_event.set()
                        self.pipeline.stop_playback()
                        self.barge_in_signal.emit("⚡ Interrupted by user — Listening...")
                        self.status_signal.emit("⚡ Interrupted by user! Stopping speech...")
                        break
                    elif barge_in_controller.is_ducked and not was_ducked:
                        self.pipeline.set_ducking(True, barge_in_controller.duck_volume)
                        self.barge_in_signal.emit("🔉 Audio ducked — User speaking...")
                    elif not barge_in_controller.is_ducked and was_ducked:
                        self.pipeline.set_ducking(False)

            barge_in_thread = None
            if duplex_enabled and stream is not None:
                barge_in_thread = threading.Thread(
                    target=_monitor_barge_in, daemon=True, name="duplex-barge-in-monitor"
                )
                barge_in_thread.start()

            try:
                def _stream_cb(token: str):
                    if self.cancel_event.is_set():
                        return
                    self.response_stream_signal.emit(token)
                    if self.auto_speak and not self.cancel_event.is_set():
                        completed_sentences = chunker.add_token(token)
                        for sent in completed_sentences:
                            if self.cancel_event.is_set():
                                break
                            self.sentence_spoken_signal.emit(sent)
                            self.pipeline.speak(sent)

                reply_dict = self.controller.process_input(
                    text,
                    stream_callback=_stream_cb,
                    cancel_event=self.cancel_event,
                )

                # Flush remaining sentence for TTS if not cancelled
                if self.auto_speak and not self.cancel_event.is_set():
                    for remaining_sent in chunker.flush():
                        if self.cancel_event.is_set():
                            break
                        self.sentence_spoken_signal.emit(remaining_sent)
                        self.pipeline.speak(remaining_sent)
            finally:
                barge_in_stop_event.set()
                if barge_in_thread and barge_in_thread.is_alive():
                    barge_in_thread.join(timeout=0.5)

            if barge_in_detected:
                self.status_signal.emit("⚡ Voice interaction interrupted.")
                self.finished_signal.emit({
                    "transcription": text,
                    "reply": "⏹ Interrupted by user.",
                    "interrupted": True,
                })
                return

            self.status_signal.emit("✅ Voice interaction complete.")
            self.finished_signal.emit({
                "transcription": text,
                "reply": reply_dict.get("final", "") if isinstance(reply_dict, dict) else str(reply_dict),
                "interrupted": False,
            })

        except (RuntimeError, ValueError, OSError) as exc:
            self.error_signal.emit(f"Voice loop error: {exc}")
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except (RuntimeError, OSError):
                    pass
            try:
                pa.terminate()
            except (RuntimeError, OSError):
                pass
