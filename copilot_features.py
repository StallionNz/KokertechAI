"""
copilot_features.py — Merged Copilot features: TTS, screen capture, and vision analysis.

Ported from Projects/KokertechCopilot/speak.py, screen.py, and brain.py.
All features use the shared AI provider layer (ai_base.get_provider) for model access.

Sprint 8.1: TTS engine upgraded from Windows SAPI to Piper TTS with SAPI fallback.
"""

import os
import time
import json
import queue
import threading
from datetime import datetime, timedelta

from logging_config import get_logger
from ai_base import get_provider


logger = get_logger(name="MemoryVault")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _discover_piper_models():
    """Scan the voice_models/ directory for Piper .onnx voice files.

    Returns a list of dicts: [{"name": "en_US-lessac-medium", "path": "...onnx"}, ...]
    When the directory does not exist it is created (via os.makedirs with
    exist_ok=True) and an empty list is returned.
    """
    import config
    base_dir = config.CONFIG.get("tts_model_dir") or os.path.dirname(__file__)
    voice_dir = os.path.join(base_dir, "voice_models")
    if not os.path.isdir(voice_dir):
        os.makedirs(voice_dir, exist_ok=True)
        return []
    models = []
    for fname in sorted(os.listdir(voice_dir)):
        if fname.endswith(".onnx") and not fname.startswith("KokertechAI"):
            name = fname.replace(".onnx", "")
            models.append({
                "name": name,
                "path": os.path.join(voice_dir, fname),
            })
    return models


def _play_wav_file(wav_path):
    """Play a WAV file on Windows using winsound."""
    import winsound
    winsound.PlaySound(wav_path, winsound.SND_FILENAME)


# ---------------------------------------------------------------------------
# VoiceOutput — Text-to-speech engine (Piper + SAPI fallback)
# ---------------------------------------------------------------------------


class VoiceOutput:
    """Text-to-speech engine with Piper TTS and Windows SAPI fallback."""

    def __init__(self):
        self.rate = 180          # words per minute
        self.voice_name = None
        self._queue = queue.Queue()
        self._init_done = threading.Event()
        self._running = True
        self._engine = None
        self._engine_type = None
        self._piper_models = {}
        self._voices = []
        self._tts_lock = threading.Lock()

        # Discover available Piper models synchronously so get_voices is populated immediately
        try:
            models = _discover_piper_models()
            if models:
                self._piper_models = {m["name"]: m["path"] for m in models}
                self._voices[:] = list(self._piper_models.keys())
        except (OSError, ValueError):
            # Graceful fallback: best-effort synchronous model discovery
            pass

        # Start the TTS loop in a background daemon thread
        t = threading.Thread(target=self._tts_loop, daemon=True)
        t.start()

    # ── Piper TTS ───────────────────────────────────────────────────

    def _init_piper(self, voice_name=None):
        """Load a Piper voice model.

        Raises ``FileNotFoundError`` when no ``.onnx`` models are found in
        ``voice_models/``.
        """
        models = _discover_piper_models()
        if not models:
            raise FileNotFoundError("No Piper voice models found in voice_models/")
        # Populate _piper_models cache
        self._piper_models = {m["name"]: m["path"] for m in models}
        self._voices[:] = list(self._piper_models.keys())
        chosen = voice_name or self.voice_name
        if chosen and chosen in self._piper_models:
            path = self._piper_models[chosen]
        else:
            path = models[0]["path"]
        import piper  # type: ignore[import-untyped]
        self._engine = piper.PiperVoice.load(path)
        self._engine_type = "piper"

    def _speak_piper(self, text):
        """Synthesise *text* with Piper and play the resulting WAV.

        Uses a fixed temp path (``kokertech_tts.wav``) inside the system
        temp directory so tests can assert against a predictable location.
        """
        import tempfile
        import wave
        import importlib

        if not self._engine or self._engine_type != "piper":
            self._init_piper()

        # Map WPM → length_scale (clamped 0.5–2.0)
        length_scale = 180.0 / max(self.rate, 1)
        length_scale = max(0.5, min(2.0, length_scale))
        piper_config = importlib.import_module("piper.config")
        syn_config = piper_config.SynthesisConfig(length_scale=length_scale)

        wav_path = os.path.join(tempfile.gettempdir(), "kokertech_tts.wav")
        try:
            chunks = self._engine.synthesize(text, syn_config=syn_config)
            # Assemble WAV from Piper audio chunks
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)   # 16-bit
                wf.setframerate(22050)
                for chunk in chunks:
                    wf.writeframes(chunk.audio_int16_bytes)
            if os.path.getsize(wav_path) > 0:
                _play_wav_file(wav_path)
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass

    # ── Windows SAPI fallback ───────────────────────────────────────

    def _init_sapi(self):
        """Initialise the Windows SAPI voice engine."""
        import pythoncom  # type: ignore[import-untyped]
        import win32com.client  # type: ignore[import-untyped]
        pythoncom.CoInitialize()
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        self._engine = speaker
        self._engine_type = "sapi"
        # Discover voices
        sapi_voices = speaker.GetVoices()
        self._voices[:] = [
            sapi_voices.Item(i).GetDescription()
            for i in range(sapi_voices.Count)
        ]

    def _speak_sapi(self, text):
        """Speak *text* via Windows SAPI with WPM→Rate mapping."""
        if not self._engine or self._engine_type != "sapi":
            self._init_sapi()
        # Map WPM → SAPI Rate (clamped -10..10)
        rate = (self.rate - 180) // 12
        rate = max(-10, min(10, rate))
        self._engine.Rate = rate
        # Select voice if voice_name matches
        if self.voice_name:
            sapi_voices = self._engine.GetVoices()
            for i in range(sapi_voices.Count):
                v = sapi_voices.Item(i)
                if v.GetDescription() == self.voice_name:
                    self._engine.Voice = v
                    break
        self._engine.Speak(text)

    # ── Public API ──────────────────────────────────────────────────

    def speak(self, text):
        """Queue *text* for the background TTS loop.  Empty / None text is ignored."""
        if not text or not str(text).strip():
            return
        self._queue.put(text)

    def stop(self):
        """Signal the TTS loop to exit and unblock the queue."""
        self._running = False
        self._queue.put(None)

    def set_rate(self, rate):
        """Set the speaking rate (words per minute)."""
        self.rate = int(rate)

    def get_voices(self, timeout=1.0):
        """Return a copy of the available voice names."""
        if not self._voices:
            self._init_done.wait(timeout=timeout)
        if not self._voices:
            try:
                models = _discover_piper_models()
                if models:
                    self._piper_models = {m["name"]: m["path"] for m in models}
                    self._voices = list(self._piper_models.keys())
            except (OSError, ValueError):
                # Graceful fallback: best-effort discovery when init thread delayed
                pass
        return list(self._voices)

    def set_voice(self, voice_name):
        """Store the voice name and reload the Piper engine if active."""
        self.voice_name = voice_name
        if self._engine_type == "piper" and voice_name in self._piper_models:
            try:
                import piper  # type: ignore[import-untyped]
                self._engine = piper.PiperVoice.load(self._piper_models[voice_name])
                self._engine_type = "piper"
            except (ImportError, RuntimeError, OSError, ValueError):
                # Graceful fallback: best-effort reload; retains previous engine on failure
                pass

    # ── Background TTS loop ─────────────────────────────────────────

    def _tts_loop(self):
        """Background daemon thread that initialises the TTS engine, then
        processes queued text items until a ``None`` sentinel is received."""
        # Init phase: try Piper, fall back to SAPI
        try:
            self._init_piper()
        except Exception:
            try:
                self._init_sapi()
            except Exception:
                logger.warning("TTS initialisation failed completely")
                self._init_done.set()
                return
        self._init_done.set()

        # Queue processing loop
        while self._running:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if item is None:          # sentinel — stop
                break
            try:
                if self._engine_type == "piper":
                    self._speak_piper(item)
                elif self._engine_type == "sapi":
                    self._speak_sapi(item)
            except Exception as exc:
                logger.warning(f"TTS speak error: {exc}")


# ---------------------------------------------------------------------------
# ScreenCapture — desktop screenshot via mss
# ---------------------------------------------------------------------------


class ScreenCapture:
    """Capture screenshots and save them to a directory."""

    def __init__(self, save_dir):
        self.save_dir = save_dir
        os.makedirs(save_dir, exist_ok=True)

    def capture(self, monitor_index=1):
        """Capture a monitor and return ``(filepath, window_title)``."""
        import mss
        import mss.tools
        from datetime import datetime

        sct = mss.MSS()
        monitors = sct.monitors
        idx = monitor_index if monitor_index < len(monitors) else 1
        monitor = monitors[idx]
        sct_img = sct.grab(monitor)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"screen_{ts}.png"
        filepath = os.path.join(self.save_dir, fname)
        mss.tools.to_png(sct_img.rgb, sct_img.size, output=filepath)
        return filepath, ""

    def cleanup(self, max_files=10):
        """Remove oldest screenshots when count exceeds *max_files*."""
        try:
            files = sorted(
                [f for f in os.listdir(self.save_dir) if f.endswith(".png")],
                key=lambda f: os.path.getctime(os.path.join(self.save_dir, f)),
            )
            while len(files) > max_files:
                oldest = files.pop(0)
                os.remove(os.path.join(self.save_dir, oldest))
        except Exception as e:
            logger.warning("ScreenCapture cleanup error: %s", e)


# ---------------------------------------------------------------------------
# VisionAnalyzer — AI-powered image description
# ---------------------------------------------------------------------------


class VisionAnalyzer:
    """Analyse images using an AI vision model."""

    def __init__(self, provider_name=None, model_name=None):
        import config
        self.provider_name = provider_name or config.CONFIG.get("active_provider", "")
        self.model_name = model_name or config.CONFIG.get("vision_model", "")

    def analyze(self, image_path, context=None):
        """Describe *image_path* using the vision model.

        Returns the description string, or an error message on failure.
        """
        import base64

        if not os.path.isfile(image_path):
            return "Error: image not found"

        # Load and preprocess the image
        try:
            from PIL import Image  # type: ignore[import-untyped]
        except ImportError:
            return "Error: Missing dependency 'PIL' (Pillow). Install with: pip install Pillow"

        try:
            img = Image.open(image_path)
            img = img.convert("RGB")
            img.thumbnail((512, 512))
            import io
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception as exc:
            return f"Error: {exc}"

        prompt = context or "Describe this image."
        provider = get_provider()
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }]
        result = provider.chat_completion(
            messages=messages, model=self.model_name, max_tokens=500,
        )
        if isinstance(result, dict):
            if result.get("error"):
                return result["error"]
            return result.get("content", "")
        return str(result)
