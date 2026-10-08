"""
wake_word.py — Voice wake word detection for KokertechAI.

Sprint 3 Stream D: listens for a configurable wake word (default: "hey koker")
using OpenAI Whisper for speech-to-text and PyAudio for microphone capture.

Architecture:
    start_wake_listener() -> daemon thread running listen_for_wake_word()
    listen_for_wake_word() -> blocking loop: capture -> transcribe -> match -> callback
    _get_whisper() -> lazy-load Whisper model (thread-safe)
    _compute_rms() -> audio energy for voice activity detection
    _transcribe_audio() -> Whisper speech-to-text
    _match_wake_word() -> case-insensitive keyword detection

Dependencies (both in requirements.txt):
  - openai-whisper (MIT license)
  - PyAudio (MIT license, requires portaudio)
"""

import threading
import time

import numpy as np

from config import CONFIG
from logging_config import get_logger


logger = get_logger(name="WakeWord")

_DEFAULT_WAKE_WORD = "hey koker"
_WHISPER_MODEL = None
_WHISPER_LOCK = threading.Lock()
_RMS_THRESHOLD = 100       # Minimum RMS energy to consider as speech
_SAMPLE_RATE = 16000       # Whisper expects 16 kHz mono
_CHUNK = 1024              # Frames per PyAudio buffer
_CHANNELS = 1              # Mono input
_RECORD_SECONDS = 3        # Max recording window per detection cycle


# ---------------------------------------------------------------------------
# Lazy Whisper model loader (thread-safe singleton)
# ---------------------------------------------------------------------------


def _get_whisper():
    """Lazy-load the Whisper model.

    Uses the tiny model by default (low VRAM).  Thread-safe via
    ``_WHISPER_LOCK``.

    Returns:
        The ``whisper.Whisper`` model instance, or ``None`` on failure.
    """
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    with _WHISPER_LOCK:
        if _WHISPER_MODEL is not None:
            return _WHISPER_MODEL
        try:
            import whisper
            model_name = CONFIG.get("whisper_model", "tiny")
            _WHISPER_MODEL = whisper.load_model(model_name)
            logger.info(
                f"Whisper model '{model_name}' loaded for wake word detection"
            )
        except Exception as exc:
            logger.warning(f"Failed to load Whisper model: {exc}")
            _WHISPER_MODEL = None
    return _WHISPER_MODEL


# ---------------------------------------------------------------------------
# Public availability check
# ---------------------------------------------------------------------------


def is_available():
    """Check whether the ``whisper`` package can be imported.

    This is a **fast** check — it does NOT load the model (that only
    happens inside ``_get_whisper()`` when the listener actually starts).
    ``app_hotkeys.py`` calls this at module-import time, so keeping
    it lightweight avoids a 5-10 s hang on startup.

    Returns:
        ``True`` if ``whisper`` is installed, ``False`` otherwise.
    """
    try:
        import whisper  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def _compute_rms(audio_data):
    """Compute RMS (root mean square) energy of 16-bit PCM audio data.

    Args:
        audio_data: Raw 16-bit PCM audio bytes.

    Returns:
        Float RMS energy level (``0.0`` if data is empty or too short).
    """
    if not audio_data or len(audio_data) < 2:
        return 0.0
    samples = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
    if len(samples) == 0:
        return 0.0
    mean_sq = float(np.mean(samples ** 2))
    return float(np.sqrt(mean_sq))


def _transcribe_audio(audio, audio_data):
    """Transcribe raw PCM audio to text using Whisper.

    Args:
        audio: Raw 16-bit PCM audio bytes captured from the microphone.
        audio_data: Metadata dict (currently unused; sample rate is
            hardcoded to 16 kHz as the module constant).

    Returns:
        Lowercased transcribed text string, or empty string on failure.
    """
    try:
        model = _get_whisper()
        if model is None:
            logger.debug("Whisper model not available — skipping transcription")
            return ""

        # Normalize int16 PCM to float32 in [-1.0, 1.0]
        samples = (
            np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        )
        result = model.transcribe(
            samples, language="en", fp16=False, temperature=0.0,
        )
        text = result.get("text", "").strip()
        return text.lower()
    except Exception as exc:
        logger.debug(f"Transcription error: {exc}")
        return ""


def _match_wake_word(text):
    """Check whether *text* contains the configured wake word.

    The wake word is read from ``CONFIG["wake_word"]`` with a fallback of
    ``"hey koker"``.  Comparison is case-insensitive and substring-based.

    Args:
        text: Lowercased transcribed text.

    Returns:
        ``True`` if the wake word appears anywhere in *text*.
    """
    if not text:
        return False
    wake_word = CONFIG.get("wake_word", _DEFAULT_WAKE_WORD).lower().strip()
    return wake_word in text


# ---------------------------------------------------------------------------
# Main listen loop (runs in a daemon thread)
# ---------------------------------------------------------------------------


def listen_for_wake_word(callback, stop_event):
    """Blocking loop: capture microphone, transcribe, and detect wake word.

    Runs in a background daemon thread.  Stops when *stop_event* is set.

    Voice activity detection uses an RMS energy threshold to avoid
    transcribing silence.  Once speech is detected, the audio is handed
    to Whisper for transcription and the result is checked for the
    configured wake word.

    Args:
        callback: Zero-argument callable invoked when the wake word is
            detected.
        stop_event: ``threading.Event`` — the loop exits when this flag
            is set.
    """
    try:
        import pyaudio
    except ImportError:
        logger.error(
            "PyAudio not installed — wake word detection unavailable. "
            "Run: pip install PyAudio"
        )
        return

    pa = pyaudio.PyAudio()
    stream = None
    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=_CHANNELS,
            rate=_SAMPLE_RATE,
            input=True,
            frames_per_buffer=_CHUNK,
        )
        logger.info("Microphone opened for wake word detection")
    except Exception as exc:
        logger.error(f"Could not open microphone: {exc}")
        pa.terminate()
        return

    # Pre-load Whisper model here so the first detection is snappier
    _get_whisper()

    try:
        frames_per_record = int(_SAMPLE_RATE / _CHUNK * _RECORD_SECONDS)

        while not stop_event.is_set():
            frames = []
            try:
                for _ in range(frames_per_record):
                    if stop_event.is_set():
                        break
                    data = stream.read(_CHUNK, exception_on_overflow=False)
                    frames.append(data)

                if not frames:
                    continue

                audio_bytes = b"".join(frames)

                # Voice activity detection
                rms = _compute_rms(audio_bytes)
                if rms < _RMS_THRESHOLD:
                    continue  # Too quiet — likely silence/noise

                text = _transcribe_audio(audio_bytes, {"sample_rate": _SAMPLE_RATE})
                if not text:
                    continue

                logger.debug(f"Transcribed: {text[:80]}")

                if _match_wake_word(text):
                    logger.info(f"Wake word detected in: {text}")
                    callback()
                    # Brief cooldown to prevent re-trigger spam
                    time.sleep(1.0)

            except OSError as exc:
                if not stop_event.is_set():
                    logger.warning(f"Audio capture error: {exc}")
                    time.sleep(0.5)
            except Exception as exc:
                if not stop_event.is_set():
                    logger.warning(f"Wake word listener error: {exc}")
                    time.sleep(0.5)
    finally:
        if stream is not None:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        try:
            pa.terminate()
        except Exception:
            pass
        logger.info("Wake word listener stopped")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def start_wake_listener(callback):
    """Start wake word detection in a daemon thread.

    Args:
        callback: Zero-argument callable invoked when the wake word is
            detected.

    Returns:
        Tuple of ``(stop_event, thread)`` for controlling the listener.
        The caller should store these references so the listener can be
        stopped later via ``stop_event.set()`` and ``thread.join()``.
    """
    stop_event = threading.Event()
    thread = threading.Thread(
        target=listen_for_wake_word,
        args=(callback, stop_event),
        daemon=True,
        name="wake-word-listener",
    )
    thread.start()
    return stop_event, thread
