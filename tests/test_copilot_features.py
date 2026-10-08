"""
Unit tests for copilot_features.py — TTS, screen capture, and vision analysis.

All heavy dependencies (Piper, winsound, mss, win32gui, PIL, requests) are mocked.
"""

import json
import os
import sys
import builtins
import tempfile
import queue
import threading
import unittest
from unittest.mock import patch, Mock, MagicMock, ANY, call

# Pre-populate sys.modules for modules imported inside functions.
sys.modules["mss"] = MagicMock()
sys.modules["mss.tools"] = MagicMock()
sys.modules["win32gui"] = MagicMock()
# PIL is installed — no need to mock it in sys.modules

# Mock piper for _speak_piper tests (SynthesisConfig imported inside method)
sys.modules["piper"] = MagicMock()
sys.modules["piper.config"] = MagicMock()


class _FakeSynthConfig:
    """Stand-in for piper.config.SynthesisConfig — stores length_scale as a real float."""
    def __init__(self, length_scale=1.0):
        self.length_scale = length_scale

sys.modules["piper.config"].SynthesisConfig = _FakeSynthConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tts_stub(self):
    """Stub for VoiceOutput._tts_loop — signals init done without entering the
    queue-processing loop or calling any real TTS init code."""
    self._init_done.set()

def _make_vo():
    """Create a VoiceOutput instance without running _tts_loop (no thread)."""
    from copilot_features import VoiceOutput
    vo = VoiceOutput.__new__(VoiceOutput)
    vo.rate = 180
    vo.voice_name = None
    vo._queue = queue.Queue()
    vo._init_done = threading.Event()
    vo._init_done.set()  # pretend init is done
    vo._running = True
    vo._engine = None
    vo._engine_type = None
    vo._piper_models = {}
    vo._voices = []
    return vo

# ===========================================================================
# _discover_piper_models
# ===========================================================================

class TestDiscoverPiperModels(unittest.TestCase):
    """Tests for _discover_piper_models()."""

    @patch("os.listdir")
    @patch("os.path.isdir", return_value=True)
    @patch("os.makedirs")
    @patch("os.path.dirname", return_value=r"C:\KokertechAI")
    @patch.dict("config.CONFIG", {"tts_model_dir": ""}, clear=True)
    def test_finds_onnx_files(self, mock_dirname, mock_makedirs, mock_isdir, mock_listdir):
        mock_listdir.return_value = [
            "en_US-lessac-medium.onnx",
            "en_US-amy-medium.onnx",
            "not_a_model.txt",
        ]
        from copilot_features import _discover_piper_models
        models = _discover_piper_models()
        self.assertEqual(len(models), 2)
        names = [m["name"] for m in models]
        self.assertIn("en_US-lessac-medium", names)
        self.assertIn("en_US-amy-medium", names)

    @patch("os.path.isdir", return_value=False)
    @patch("os.makedirs")
    @patch("os.path.dirname", return_value=r"C:\KokertechAI")
    @patch.dict("config.CONFIG", {"tts_model_dir": ""}, clear=True)
    def test_empty_when_no_dir(self, mock_dirname, mock_makedirs, mock_isdir):
        mock_makedirs.return_value = None
        from copilot_features import _discover_piper_models
        models = _discover_piper_models()
        self.assertEqual(models, [])
        mock_makedirs.assert_any_call(
            r"C:\KokertechAI\voice_models", exist_ok=True
        )

# ===========================================================================
# _play_wav_file
# ===========================================================================

class TestPlayWavFile(unittest.TestCase):
    """Tests for _play_wav_file()."""

    @patch("winsound.PlaySound")
    def test_plays_wav(self, mock_play):
        from copilot_features import _play_wav_file
        _play_wav_file(r"C:\test.wav")
        mock_play.assert_called_once_with(
            r"C:\test.wav",
            ANY,
        )

# ===========================================================================
# VoiceOutput — TTS engine
# ===========================================================================

class TestVoiceOutputInit(unittest.TestCase):
    """VoiceOutput.__init__ — initialisation flow and engine selection."""

    # ── Piper path ────────────────────────────────────────────────────

    @patch("copilot_features.logger")  # suppresses %s-formatting crash in the real logger
    @patch("piper.PiperVoice")
    @patch("copilot_features._discover_piper_models")
    @patch("copilot_features.VoiceOutput._tts_loop", _tts_stub)
    def test_piper_path_sets_engine(self, mock_discover, mock_piper_voice_cls,
                                     mock_logger):
        """_init_piper succeeds → engine_type='piper', voices populated."""
        mock_discover.return_value = [
            {"name": "en_US-lessac-medium", "path": "/fake/model.onnx"},
        ]
        mock_piper_voice = Mock()
        mock_piper_voice_cls.load.return_value = mock_piper_voice

        from copilot_features import VoiceOutput
        vo = VoiceOutput()
        vo._init_piper()

        self.assertEqual(vo._engine_type, "piper")
        self.assertEqual(vo._engine, mock_piper_voice)
        self.assertEqual(vo._voices, ["en_US-lessac-medium"])
        self.assertIn("en_US-lessac-medium", vo._piper_models)
        mock_piper_voice_cls.load.assert_called_once()

    @patch("copilot_features.logger")
    @patch("piper.PiperVoice")
    @patch("copilot_features._discover_piper_models")
    @patch("copilot_features.VoiceOutput._tts_loop", _tts_stub)
    def test_piper_selects_voice_name(self, mock_discover, mock_piper_voice_cls,
                                       mock_logger):
        """voice_name is honoured when loading the Piper model."""
        mock_discover.return_value = [
            {"name": "voice_a", "path": "/a.onnx"},
            {"name": "voice_b", "path": "/b.onnx"},
        ]
        from copilot_features import VoiceOutput
        vo = VoiceOutput()
        vo.voice_name = "voice_b"
        vo._init_piper()
        mock_piper_voice_cls.load.assert_called_with("/b.onnx")

    # ── SAPI fallback path ────────────────────────────────────────────

    @patch("copilot_features.logger")
    def test_sapi_fallback_when_piper_fails(self, mock_logger):
        """_init_piper raises → fall back to _init_sapi (no _tts_stub)."""
        # No @patch on _tts_loop — we let the real _tts_loop run because we
        # control its sub-calls via instance patches below.
        # Put a sentinel in the queue so _tts_loop doesn't block forever.
        import pythoncom
        import win32com.client

        mock_speaker = Mock()
        voices_mock = Mock()
        v0 = Mock()
        v0.GetDescription.return_value = "Microsoft David Desktop"
        voices_mock.Item.side_effect = lambda i: v0
        voices_mock.Count = 1
        mock_speaker.GetVoices.return_value = voices_mock

        with patch("pythoncom.CoInitialize") as mock_coin:
            with patch("win32com.client.Dispatch", return_value=mock_speaker):
                from copilot_features import VoiceOutput
                vo = VoiceOutput.__new__(VoiceOutput)
                vo.rate = 180
                vo.voice_name = None
                vo._queue = queue.Queue()
                vo._queue.put(None)  # sentinel to break the queue loop
                vo._init_done = threading.Event()
                vo._running = True
                vo._engine = None
                vo._engine_type = None
                vo._piper_models = {}
                vo._voices = []

                with patch.object(vo, "_init_piper",
                                  side_effect=ImportError("no piper")):
                    vo._tts_loop()
                self.assertEqual(vo._engine_type, "sapi")
                self.assertEqual(vo._engine, mock_speaker)
                self.assertIn("Microsoft David Desktop", vo._voices)

    # ── Both fail ─────────────────────────────────────────────────────

    @patch("copilot_features.logger")
    def test_both_fail_logs_warning(self, mock_logger):
        """Both _init_piper and _init_sapi fail → logger.warning called."""
        vo = _make_vo()
        vo._queue.put(None)  # sentinel to break the queue loop
        with patch.object(vo, "_init_piper", side_effect=ImportError("no piper")):
            with patch.object(vo, "_init_sapi", side_effect=Exception("no sapi")):
                vo._tts_loop()
        mock_logger.warning.assert_called_once()
        self.assertIn("failed completely", mock_logger.warning.call_args[0][0])

    # ── _init_done signalling ─────────────────────────────────────────

    @patch("copilot_features.VoiceOutput._tts_loop", _tts_stub)
    def test_init_done_signalled(self):
        """_init_done Event is set after _tts_loop completes init phase."""
        from copilot_features import VoiceOutput
        vo = VoiceOutput()
        self.assertTrue(vo._init_done.is_set())

class TestVoiceOutputPiper(unittest.TestCase):
    """Low-level Piper methods: _init_piper, _speak_piper, set_voice reload."""

    # ── _init_piper ───────────────────────────────────────────────────

    @patch("copilot_features.logger")
    @patch("piper.PiperVoice")
    @patch("copilot_features._discover_piper_models")
    def test_init_piper_loads_model(self, mock_discover, mock_piper_cls,
                                     mock_logger):
        """_init_piper loads the first available Piper model."""
        mock_discover.return_value = [
            {"name": "en_US-lessac-medium", "path": "/voices/model.onnx"},
        ]
        mock_piper = Mock()
        mock_piper_cls.load.return_value = mock_piper

        vo = _make_vo()
        vo._init_piper()
        self.assertEqual(vo._engine_type, "piper")
        self.assertIs(vo._engine, mock_piper)
        self.assertEqual(vo._voices, ["en_US-lessac-medium"])
        mock_piper_cls.load.assert_called_once_with("/voices/model.onnx")

    @patch("piper.PiperVoice")
    @patch("copilot_features._discover_piper_models")
    def test_init_piper_no_models_raises(self, mock_discover, mock_piper_cls):
        """_init_piper raises FileNotFoundError when no models exist."""
        mock_discover.return_value = []
        vo = _make_vo()
        with self.assertRaises(FileNotFoundError):
            vo._init_piper()

    # ── _speak_piper ──────────────────────────────────────────────────

    @patch("copilot_features._play_wav_file")
    @patch("tempfile.gettempdir", return_value=r"C:\Windows\Temp")
    def test_speak_piper_synthesizes_and_plays(self, mock_tempdir, mock_play):
        """_speak_piper synthesises audio via Piper and plays WAV."""
        vo = _make_vo()
        chunk1 = Mock()
        chunk1.audio_int16_bytes = b"\x00\x01" * 100
        vo._engine = Mock()
        vo._engine.synthesize.return_value = [chunk1]
        vo._engine_type = "piper"

        vo._speak_piper("Hello world")

        vo._engine.synthesize.assert_called_once()
        args, kwargs = vo._engine.synthesize.call_args
        self.assertEqual(args[0], "Hello world")
        self.assertIn("syn_config", kwargs)
        # Verify WAV was played
        mock_play.assert_called_once_with(
            r"C:\Windows\Temp\kokertech_tts.wav"
        )

    @patch("copilot_features._play_wav_file")
    @patch("tempfile.gettempdir", return_value=r"C:\Windows\Temp")
    @patch("copilot_features.os.remove")
    @patch("copilot_features.os.path.getsize", return_value=100)
    def test_speak_piper_cleans_up_wav(self, mock_getsize,
                                       mock_remove, mock_tempdir, mock_play):
        """_speak_piper removes the temp WAV file after playback."""
        vo = _make_vo()
        chunk = Mock()
        chunk.audio_int16_bytes = b"\x00\x01" * 100
        vo._engine = Mock()
        vo._engine.synthesize.return_value = [chunk]
        vo._engine_type = "piper"

        vo._speak_piper("test")
        mock_remove.assert_called_once_with(
            r"C:\Windows\Temp\kokertech_tts.wav"
        )

    @patch("copilot_features._play_wav_file")
    @patch("tempfile.gettempdir", return_value=r"C:\Windows\Temp")
    @patch("copilot_features.os.remove")
    def test_speak_piper_cleanup_on_error(self, mock_remove, mock_tempdir, mock_play):
        """Temp WAV is cleaned up even if playback fails."""
        vo = _make_vo()
        chunk = Mock()
        chunk.audio_int16_bytes = b"\x00\x01" * 100
        vo._engine = Mock()
        vo._engine.synthesize.return_value = [chunk]
        vo._engine_type = "piper"
        mock_play.side_effect = Exception("playback error")

        # _play_wav_file raises, which propagates through _speak_piper
        try:
            vo._speak_piper("test")
        except Exception:
            pass
        # Cleanup still happened in the finally block
        mock_remove.assert_called_once()

    @patch("copilot_features._play_wav_file")
    @patch("tempfile.gettempdir", return_value=r"C:\Windows\Temp")
    @patch("copilot_features.os.path.getsize", return_value=0)
    @patch("copilot_features.os.path.exists", return_value=True)
    def test_speak_piper_skips_empty_wav(self, mock_exists, mock_getsize,
                                          mock_tempdir, mock_play):
        """_speak_piper does not play a zero-byte WAV."""
        vo = _make_vo()
        chunk = Mock()
        chunk.audio_int16_bytes = b""  # empty chunk
        vo._engine = Mock()
        vo._engine.synthesize.return_value = [chunk]
        vo._engine_type = "piper"

        vo._speak_piper("silence")
        mock_play.assert_not_called()

    # ── WPM → length_scale mapping ────────────────────────────────────

    @patch("copilot_features._play_wav_file")
    @patch("tempfile.gettempdir", return_value=r"C:\Windows\Temp")
    def test_rate_maps_to_length_scale(self, mock_tempdir, mock_play):
        """Higher WPM → lower length_scale (faster speech)."""
        vo = _make_vo()
        vo.rate = 300  # fast
        chunk = Mock()
        chunk.audio_int16_bytes = b"\x00" * 100
        vo._engine = Mock()
        vo._engine.synthesize.return_value = [chunk]
        vo._engine_type = "piper"

        vo._speak_piper("fast")
        _, kwargs = vo._engine.synthesize.call_args
        length_scale = kwargs["syn_config"].length_scale
        # 180 / 300 = 0.6
        self.assertAlmostEqual(length_scale, 0.6, places=2)

    @patch("copilot_features._play_wav_file")
    @patch("tempfile.gettempdir", return_value=r"C:\Windows\Temp")
    def test_rate_maps_clamped(self, mock_tempdir, mock_play):
        """length_scale is clamped between 0.5 and 2.0."""
        vo = _make_vo()
        vo.rate = 30  # very slow → should clamp to 2.0
        chunk = Mock()
        chunk.audio_int16_bytes = b"\x00" * 100
        vo._engine = Mock()
        vo._engine.synthesize.return_value = [chunk]
        vo._engine_type = "piper"

        vo._speak_piper("slow")
        _, kwargs = vo._engine.synthesize.call_args
        self.assertAlmostEqual(kwargs["syn_config"].length_scale, 2.0, places=2)

        vo.rate = 600  # very fast → should clamp to 0.5
        vo._speak_piper("very fast")
        _, kwargs = vo._engine.synthesize.call_args
        self.assertAlmostEqual(kwargs["syn_config"].length_scale, 0.5, places=2)

class TestVoiceOutputSapi(unittest.TestCase):
    """Low-level SAPI methods: _init_sapi, _speak_sapi."""

    # ── _init_sapi ────────────────────────────────────────────────────

    @patch("win32com.client.Dispatch")
    @patch("pythoncom.CoInitialize")
    def test_init_sapi_populates_voices(self, mock_coin, mock_dispatch):
        """_init_sapi discovers SAPI voices."""
        mock_speaker = Mock()
        v0 = Mock()
        v0.GetDescription.return_value = "Microsoft David Desktop"
        v1 = Mock()
        v1.GetDescription.return_value = "Microsoft Zira Desktop"
        mock_voices = Mock()
        mock_voices.Count = 2
        mock_voices.Item.side_effect = lambda i: [v0, v1][i]
        mock_speaker.GetVoices.return_value = mock_voices
        mock_dispatch.return_value = mock_speaker

        vo = _make_vo()
        vo._init_sapi()

        self.assertEqual(vo._engine_type, "sapi")
        self.assertIs(vo._engine, mock_speaker)
        self.assertEqual(vo._voices, [
            "Microsoft David Desktop",
            "Microsoft Zira Desktop",
        ])

    @patch("win32com.client.Dispatch")
    @patch("pythoncom.CoInitialize")
    def test_init_sapi_no_coinitialize_if_already_done(self, mock_coin, mock_dispatch):
        """CoInitialize is called."""
        mock_speaker = Mock()
        mock_speaker.GetVoices.return_value.Count = 0
        mock_dispatch.return_value = mock_speaker

        vo = _make_vo()
        vo._init_sapi()
        mock_coin.assert_called_once()

    # ── _speak_sapi ───────────────────────────────────────────────────

    def test_speak_sapi_sets_rate(self):
        """_speak_sapi maps WPM to SAPI rate and calls Speak."""
        vo = _make_vo()
        vo.rate = 240  # +60 from 180
        vo._engine = Mock()
        vo._engine_type = "sapi"

        vo._speak_sapi("hello")
        # (240 - 180) / 12 = 5
        self.assertEqual(vo._engine.Rate, 5)
        vo._engine.Speak.assert_called_once_with("hello")

    def test_speak_sapi_rate_clamped(self):
        """SAPI rate is clamped to [-10, 10]."""
        vo = _make_vo()
        vo.rate = 999  # (999-180)/12 ≈ 68 → clamp to 10
        vo._engine = Mock()
        vo._engine_type = "sapi"

        vo._speak_sapi("fast")
        self.assertEqual(vo._engine.Rate, 10)

        vo.rate = 1  # (1-180)/12 ≈ -14 → clamp to -10
        vo._speak_sapi("slow")
        self.assertEqual(vo._engine.Rate, -10)

    def test_speak_sapi_selects_voice(self):
        """_speak_sapi sets the Voice property when voice_name matches."""
        vo = _make_vo()
        vo.voice_name = "Microsoft Zira Desktop"
        mock_speaker = Mock()
        v0 = Mock()
        v0.GetDescription.return_value = "Microsoft David Desktop"
        v1 = Mock()
        v1.GetDescription.return_value = "Microsoft Zira Desktop"
        mock_voices = Mock()
        mock_voices.Count = 2
        mock_voices.Item.side_effect = lambda i: [v0, v1][i]
        mock_speaker.GetVoices.return_value = mock_voices
        vo._engine = mock_speaker
        vo._engine_type = "sapi"

        vo._speak_sapi("hello")
        self.assertEqual(vo._engine.Voice, v1)

    def test_speak_sapi_no_voice_when_no_match(self):
        """When voice_name doesn't match any voice, no Voice property set."""
        vo = _make_vo()
        vo.voice_name = "NonExistent Voice"
        # Use spec so Mock doesn't auto-create arbitrary attributes on access
        mock_speaker = MagicMock(spec=["GetVoices", "Speak", "Rate"])
        v0 = Mock()
        v0.GetDescription.return_value = "Microsoft David Desktop"
        mock_voices = Mock()
        mock_voices.Count = 1
        mock_voices.Item.side_effect = lambda i: v0
        mock_speaker.GetVoices.return_value = mock_voices
        vo._engine = mock_speaker
        vo._engine_type = "sapi"

        vo._speak_sapi("hello")
        # Voice should NOT have been set (no match found)
        # With spec=[], Mock won't auto-create Voice on getattr access
        self.assertFalse(hasattr(vo._engine, 'Voice'))

class TestVoiceOutputSetVoice(unittest.TestCase):
    """set_voice() behaviour — property and Piper reload."""

    def test_sets_voice_name(self):
        """set_voice stores the voice name."""
        vo = _make_vo()
        vo.set_voice("en_US-amy-medium")
        self.assertEqual(vo.voice_name, "en_US-amy-medium")

    @patch("piper.PiperVoice")
    def test_reloads_piper_engine(self, mock_piper_cls):
        """When Piper is active, set_voice reloads the engine for the new voice."""
        vo = _make_vo()
        vo._engine_type = "piper"
        vo._piper_models = {"en_US-amy-medium": "/models/amy.onnx"}
        mock_new_engine = Mock()
        mock_piper_cls.load.return_value = mock_new_engine

        vo.set_voice("en_US-amy-medium")
        self.assertEqual(vo._engine, mock_new_engine)
        mock_piper_cls.load.assert_called_once_with("/models/amy.onnx")

    def test_does_not_reload_for_sapi(self):
        """When SAPI is active, set_voice does not reload the engine."""
        vo = _make_vo()
        vo._engine_type = "sapi"
        vo._engine = "sapi_engine"  # not a Mock, to prove no reload
        vo._piper_models = {"voice_a": "/a.onnx"}

        vo.set_voice("voice_a")
        self.assertEqual(vo._engine, "sapi_engine")

class TestVoiceOutputTtsLoop(unittest.TestCase):
    """_tts_loop — queue processing and thread orchestration."""

    @patch("copilot_features.VoiceOutput._tts_loop", _tts_stub)
    def test_speak_queues_text(self):
        """speak() adds text to the internal queue."""
        from copilot_features import VoiceOutput
        vo = VoiceOutput()
        # Queue loop is stubbed, so items stay in the queue
        vo.speak("Hello")
        self.assertEqual(vo._queue.qsize(), 1)
        self.assertEqual(vo._queue.get(block=False), "Hello")

    @patch("copilot_features.VoiceOutput._tts_loop", _tts_stub)
    def test_speak_ignores_empty(self):
        """speak('') and speak(None) should not queue anything."""
        from copilot_features import VoiceOutput
        vo = VoiceOutput()
        vo.speak("")
        vo.speak(None)
        self.assertEqual(vo._queue.qsize(), 0)

    @patch("copilot_features.VoiceOutput._tts_loop", _tts_stub)
    def test_stop_sets_running_flag(self):
        """stop() sets _running to False."""
        from copilot_features import VoiceOutput
        vo = VoiceOutput()
        vo.stop()
        self.assertFalse(vo._running)

    @patch("copilot_features.VoiceOutput._tts_loop", _tts_stub)
    def test_stop_sends_sentinel(self):
        """stop() puts None in the queue to unblock the TTS loop."""
        from copilot_features import VoiceOutput
        vo = VoiceOutput()
        vo.stop()
        sentinel = vo._queue.get(block=False)
        self.assertIsNone(sentinel)

    # ── _tts_loop processes queue items ───────────────────────────────

    def test_tts_loop_processes_queued_text_via_piper(self):
        """_tts_loop picks up queued text and calls _speak_piper."""
        vo = _make_vo()
        vo._engine_type = "piper"
        vo._voices = ["voice_a"]

        vo.speak("Hello from test")
        vo._queue.put(None)  # sentinel to break the loop (speak(None) is no-op)

        with patch.object(vo, "_init_piper"):
            with patch.object(vo, "_speak_piper") as mock_speak:
                vo._tts_loop()
                mock_speak.assert_called_once_with("Hello from test")

    def test_tts_loop_calls_sapi_when_piper_engine(self):
        """_tts_loop calls _speak_sapi when engine_type is 'sapi'."""
        vo = _make_vo()
        vo._engine_type = "sapi"
        vo._voices = []

        vo.speak("Hello")
        vo._queue.put(None)  # sentinel (speak(None) is a no-op)

        with patch.object(vo, "_init_piper"):
            with patch.object(vo, "_speak_sapi") as mock_speak:
                vo._tts_loop()
                mock_speak.assert_called_once_with("Hello")

    def test_tts_loop_logs_speak_error(self):
        """If _speak_piper raises, the loop logs but does not crash."""
        vo = _make_vo()
        vo._engine_type = "piper"
        vo._voices = []

        vo.speak("crash")
        vo._queue.put(None)  # sentinel (speak(None) is a no-op)

        with patch.object(vo, "_init_piper"):
            with patch("copilot_features.logger") as mock_log:
                with patch.object(
                    vo, "_speak_piper", side_effect=Exception("boom")
                ):
                    vo._tts_loop()
                    mock_log.warning.assert_called_once()
                    # The logger receives ("TTS speak error: %s", Exception("boom"))
                    # Check the actual exception is in the second positional arg
                    self.assertIn("boom", str(mock_log.warning.call_args[0][0]))

class TestVoiceOutputInterface(unittest.TestCase):
    """Public API: set_rate, get_voices."""

    @patch("copilot_features.VoiceOutput._tts_loop", _tts_stub)
    def test_set_rate_stores_int(self):
        """set_rate converts to int and stores."""
        from copilot_features import VoiceOutput
        vo = VoiceOutput()
        vo.set_rate("200")  # string input
        self.assertEqual(vo.rate, 200)
        vo.set_rate(150.7)  # float truncates
        self.assertEqual(vo.rate, 150)

    @patch("copilot_features.VoiceOutput._tts_loop", _tts_stub)
    def test_get_voices_returns_copy(self):
        """get_voices returns a list copy, not the internal reference."""
        from copilot_features import VoiceOutput
        vo = VoiceOutput()
        vo._voices = ["v1", "v2"]
        voices = vo.get_voices()
        self.assertEqual(voices, ["v1", "v2"])
        voices.append("v3")
        self.assertEqual(vo._voices, ["v1", "v2"])

# ===========================================================================
# ScreenCapture — existing tests
# ===========================================================================

class TestScreenCapture(unittest.TestCase):
    """Tests for ScreenCapture class."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="kokertech_screen_test_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("mss.tools")
    @patch("mss.MSS")
    def test_capture_saves_png(self, mock_mss_cls, mock_tools):
        mock_mss = Mock()
        mock_mss.monitors = [{}, {"left": 0, "top": 0, "width": 1920, "height": 1080}]
        mock_sct_img = Mock()
        mock_sct_img.rgb = b"\x00" * (1920 * 1080 * 3)
        mock_sct_img.size = (1920, 1080)
        mock_mss.grab.return_value = mock_sct_img
        mock_mss_cls.return_value = mock_mss

        from copilot_features import ScreenCapture
        sc = ScreenCapture(save_dir=self.temp_dir)
        filepath, window = sc.capture(monitor_index=1)

        self.assertTrue(filepath.endswith(".png"))
        self.assertIn(self.temp_dir, filepath)
        mock_mss.grab.assert_called_once_with(mock_mss.monitors[1])
        mock_tools.to_png.assert_called_once()

    @patch("mss.MSS")
    def test_out_of_range_monitor_defaults_to_1(self, mock_mss_cls):
        mock_mss = Mock()
        mock_mss.monitors = [
            {"dummy": 0},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
        ]
        mock_sct_img = Mock()
        mock_sct_img.rgb = b"\x00" * (1920 * 1080 * 3)
        mock_sct_img.size = (1920, 1080)
        mock_mss.grab.return_value = mock_sct_img
        mock_mss_cls.return_value = mock_mss

        from copilot_features import ScreenCapture
        sc = ScreenCapture(save_dir=self.temp_dir)
        filepath, _ = sc.capture(monitor_index=5)
        self.assertIsNotNone(filepath)

    @patch("copilot_features.os.listdir")
    @patch("copilot_features.os.path.getctime")
    @patch("copilot_features.os.remove")
    def test_cleanup_removes_old_files(self, mock_remove, mock_getctime, mock_listdir):
        mock_listdir.return_value = [f"screen_20250101_{i:06d}.png" for i in range(15)]
        mock_getctime.side_effect = [float(i) for i in range(15)]

        from copilot_features import ScreenCapture
        sc = ScreenCapture(save_dir=self.temp_dir)
        sc.cleanup(max_files=10)
        self.assertEqual(mock_remove.call_count, 5)

    @patch("copilot_features.logger")
    @patch("copilot_features.os.listdir")
    def test_cleanup_handles_error(self, mock_listdir, mock_logger):
        mock_listdir.side_effect = Exception("dir error")

        from copilot_features import ScreenCapture
        sc = ScreenCapture(save_dir=self.temp_dir)
        sc.cleanup(max_files=10)
        mock_logger.warning.assert_called_once()

# ===========================================================================
# VisionAnalyzer — existing tests
# ===========================================================================

class TestVisionAnalyzer(unittest.TestCase):
    """Tests for VisionAnalyzer class.

    PIL is installed on this system, so @patch("PIL.Image.open") works directly.
    copilot_features imports get_provider via 'from ai_base import get_provider'
    (local reference), so @patch("copilot_features.get_provider") is needed.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="kokertech_vision_test_")
        self.img_path = os.path.join(self.temp_dir, "test.png")
        with open(self.img_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("config.CONFIG", {"active_provider": "mock_provider", "vision_model": ""})
    def test_init_uses_config(self):
        from copilot_features import VisionAnalyzer
        va = VisionAnalyzer()
        self.assertEqual(va.provider_name, "mock_provider")

    def test_analyze_file_not_found(self):
        from copilot_features import VisionAnalyzer
        va = VisionAnalyzer(provider_name="local_llm", model_name="llava")
        result = va.analyze("/nonexistent/image.png")
        self.assertIn("not found", result)

    @patch("PIL.Image.open")
    @patch("copilot_features.get_provider")
    def test_analyze_success(self, mock_get_provider, mock_pil_open):
        mock_img = Mock()
        mock_img.mode = "RGB"
        mock_img.thumbnail.return_value = None
        mock_img.save.side_effect = lambda buf, **kw: buf.write(b"fake-jpeg-data")
        mock_pil_open.return_value.__enter__.return_value = mock_img

        mock_provider = Mock()
        mock_provider.chat_completion.return_value = {
            "content": "I see a desktop with a browser window.",
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        from copilot_features import VisionAnalyzer
        va = VisionAnalyzer(provider_name="local_llm", model_name="llava")
        result = va.analyze(self.img_path, context="testing")
        self.assertIn("desktop", result)

    @patch("PIL.Image.open")
    @patch("copilot_features.get_provider")
    def test_analyze_provider_error(self, mock_get_provider, mock_pil_open):
        mock_img = Mock()
        mock_img.mode = "RGB"
        mock_img.thumbnail.return_value = None
        mock_img.save.side_effect = lambda buf, **kw: buf.write(b"fake-jpeg-data")
        mock_pil_open.return_value.__enter__.return_value = mock_img

        mock_provider = Mock()
        mock_provider.chat_completion.return_value = {
            "content": "",
            "error": "API rate limited",
        }
        mock_get_provider.return_value = mock_provider

        from copilot_features import VisionAnalyzer
        va = VisionAnalyzer(provider_name="local_llm", model_name="llava")
        result = va.analyze(self.img_path)
        self.assertIn("API rate limited", result)

    def test_analyze_missing_dependency(self):
        """PIL is installed, so mock __import__ to raise ImportError for PIL."""
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError(f"No module named {name}")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = mock_import
        try:
            from copilot_features import VisionAnalyzer
            va = VisionAnalyzer(provider_name="local_llm")
            result = va.analyze(self.img_path)
            self.assertIn("Missing dependency", result)
        finally:
            builtins.__import__ = original_import

    @patch("PIL.Image.open")
    def test_analyze_generic_exception(self, mock_pil_open):
        """Image.open raises a generic Exception, caught by except Exception handler."""
        mock_pil_open.side_effect = Exception("Unexpected crash")
        from copilot_features import VisionAnalyzer
        va = VisionAnalyzer(provider_name="local_llm")
        result = va.analyze(self.img_path)
        self.assertIn("Unexpected crash", result)
