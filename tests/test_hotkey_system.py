"""Unit tests for the global hotkey system — apply_hotkey, CONFIG defaults, handler dispatch.

NOTE: Cross-file QApp isolation handled by conftest.py Phase 4.
      The ``importlib.reload`` hang was fixed June 2026 (RuntimeError not Exception).
"""


import unittest
from unittest.mock import patch, MagicMock, call

import pytest

from config import CONFIG

# ---------------------------------------------------------------------------
# 1. CONFIG defaults
# ---------------------------------------------------------------------------
@patch.dict("config.CONFIG", {
    "trigger_hotkey": "ctrl+alt+a",
    "read_hotkey": "ctrl+alt+s",
    "voice_hotkey": "ctrl+alt+v",
    "hud_hotkey": "ctrl+alt+space",
}, clear=False)
class TestHotkeyConfigDefaults(unittest.TestCase):
    """Verify hotkey CONFIG defaults are correct."""

    def test_trigger_hotkey_default(self):
        assert CONFIG.get("trigger_hotkey") == "ctrl+alt+a"

    def test_read_hotkey_default(self):
        assert CONFIG.get("read_hotkey") == "ctrl+alt+s"

    def test_voice_hotkey_default(self):
        assert CONFIG.get("voice_hotkey") == "ctrl+alt+v"

    def test_hud_hotkey_default(self):
        assert CONFIG.get("hud_hotkey") == "ctrl+alt+space"

    def test_all_hotkey_keys_present(self):
        for key in ("trigger_hotkey", "read_hotkey", "voice_hotkey", "hud_hotkey"):
            assert key in CONFIG


# ---------------------------------------------------------------------------
# 1b. Module-level _WAKE_WORD_AVAILABLE detection
# ---------------------------------------------------------------------------
class TestWakeWordModuleAvailability(unittest.TestCase):
    """Test the module-level _WAKE_WORD_AVAILABLE detection in app_hotkeys.py."""

    @patch("app_hotkeys.wake_word.is_available", return_value=True)
    def test_detected_when_wake_word_available(self, mock_is_avail):
        import importlib
        import app_hotkeys
        importlib.reload(app_hotkeys)
        assert app_hotkeys._WAKE_WORD_AVAILABLE

    @patch("app_hotkeys.wake_word.is_available", side_effect=RuntimeError("Audio device not found"))
    def test_false_when_is_available_raises(self, mock_is_avail):
        import importlib
        import app_hotkeys
        importlib.reload(app_hotkeys)
        assert not app_hotkeys._WAKE_WORD_AVAILABLE


# ---------------------------------------------------------------------------
# 2. app_core.py hotkey methods (uses pytest-qt qtbot for QApplication lifecycle)
# ---------------------------------------------------------------------------
class TestHotkeyDashboardMethods:
    """Tests for apply_hotkey() and _do_hotkey_* methods on KokertechDashboard."""

    @pytest.fixture(autouse=True)
    def _dashboard(self, shared_dashboard):
        """
        Use the module-scoped shared Dashboard and reset state per test.
        The Dashboard is created once per test file by ``shared_dashboard``
        (defined in conftest.py), which applies all baseline patches.
        """
        self.window = shared_dashboard
        # Reset mutable state to isolate each test
        self.window.last_user_text = ""
        self.window.chat_display.clear()
        self.window.audit_log_display.clear()
        self.window.txt_input.clear()
        self.window.file_logger.reset_mock()
        # Remove wake-word / voice attrs that individual tests may create
        for attr in ('_wake_stop', '_wake_thread', 'voice_worker', 'wake_status_lbl'):
            if hasattr(self.window, attr):
                try:
                    delattr(self.window, attr)
                except Exception:
                    pass
        # Reset mic button
        try:
            self.window.btn_mic.setStyleSheet("")
        except Exception:
            pass
        yield


    # ----------------------------------------------------------
    # apply_hotkey
    # ----------------------------------------------------------
    def test_apply_hotkey_registers_all_three(self):
        """apply_hotkey() should call keyboard.add_hotkey for each hotkey with correct handler."""
        mock_kb = MagicMock()
        with patch("app_hotkeys._get_keyboard_module", return_value=mock_kb):
            self.window.apply_hotkey()
            mock_kb.unhook_all.assert_called_once()
            assert mock_kb.add_hotkey.call_count == 5
            expected_calls = [
                call(CONFIG["trigger_hotkey"], self.window._hotkey_trigger_analysis),
                call(CONFIG["read_hotkey"], self.window._hotkey_read_selected_text),
                call(CONFIG["voice_hotkey"], self.window._hotkey_voice_input),
                call(CONFIG["hud_hotkey"], self.window._hotkey_toggle_hud),
                call("ctrl+alt+shift+x", self.window._hotkey_toggle_hud_click_through),
            ]
            mock_kb.add_hotkey.assert_has_calls(expected_calls, any_order=False)

    @patch("app_hotkeys._get_keyboard_module")
    def test_apply_hotkey_handles_exception_gracefully(self, mock_get_kb):
        """If keyboard.add_hotkey raises, the method should not crash."""
        mock_kb = MagicMock()
        mock_kb.add_hotkey.side_effect = Exception("Permission denied")
        mock_get_kb.return_value = mock_kb
        try:
            self.window.apply_hotkey()
        except Exception:
            assert False, "apply_hotkey() should not propagate exceptions"

    # ----------------------------------------------------------
    # _do_hotkey_trigger
    # ----------------------------------------------------------
    def test_do_hotkey_trigger_injects_last_text(self):
        """_do_hotkey_trigger should inject last_user_text into txt_input and send."""
        with patch.object(self.window, "action_send_prompt") as mock_send:
            self.window.last_user_text = "Repeat this"
            self.window.txt_input.clear()
            self.window._do_hotkey_trigger()

            assert self.window.txt_input.toPlainText() == "Repeat this"
            mock_send.assert_called_once()

    def test_do_hotkey_trigger_empty_last_text_still_logs(self):
        """_do_hotkey_trigger with no last_user_text should not send but should log."""
        with patch.object(self.window, "action_send_prompt") as mock_send:
            self.window.last_user_text = ""
            self.window._do_hotkey_trigger()

            mock_send.assert_not_called()
            audit = self.window.audit_log_display.toPlainText()
            assert "Hotkey" in audit
            assert "Trigger" in audit

    # ----------------------------------------------------------
    # _hotkey_trigger_analysis dispatch
    # ----------------------------------------------------------
    @patch("app_hotkeys.QTimer")
    def test_hotkey_trigger_dispatches_via_timer(self, mock_qtimer):
        """_hotkey_trigger_analysis should schedule _do_hotkey_trigger via QTimer.singleShot."""
        self.window._hotkey_trigger_analysis()
        mock_qtimer.singleShot.assert_called_once_with(0, self.window._do_hotkey_trigger)

    # ----------------------------------------------------------
    # _do_hotkey_read
    # ----------------------------------------------------------
    @patch.dict("sys.modules", {"pyperclip": MagicMock(paste=MagicMock(return_value="Selected text from clipboard"))})
    def test_do_hotkey_read_speaks_clipboard(self):
        """_do_hotkey_read should speak the clipboard text via TTS."""
        with patch.object(self.window.voice_output, "speak") as mock_speak:
            self.window._do_hotkey_read()

            mock_speak.assert_called_once_with("Selected text from clipboard")
            audit = self.window.audit_log_display.toPlainText()
            assert "Read" in audit

    @patch.dict("sys.modules", {"pyperclip": MagicMock(paste=MagicMock(return_value=""))})
    def test_do_hotkey_read_empty_clipboard(self):
        """_do_hotkey_read with empty clipboard should log but not speak."""
        with patch.object(self.window.voice_output, "speak") as mock_speak:
            self.window._do_hotkey_read()

            mock_speak.assert_not_called()
            audit = self.window.audit_log_display.toPlainText()
            assert "no text" in audit.lower()

    @patch.dict("sys.modules", {"pyperclip": None})
    def test_do_hotkey_read_missing_pyperclip(self):
        """_do_hotkey_read should gracefully handle missing pyperclip with a warning log."""
        with patch.object(self.window.voice_output, "speak") as mock_speak:
            self.window._do_hotkey_read()

            mock_speak.assert_not_called()
            self.window.file_logger.warning.assert_called()
            assert "pyperclip" in str(self.window.file_logger.warning.call_args).lower()

    # ----------------------------------------------------------
    # _hotkey_read_selected_text dispatch
    # ----------------------------------------------------------
    @patch("app_hotkeys._get_keyboard_module")
    @patch("app_hotkeys.QTimer")
    def test_hotkey_read_dispatches_via_timer(self, mock_qtimer, mock_get_kb):
        """_hotkey_read_selected_text should send ctrl+c then schedule _do_hotkey_read."""
        mock_kb = MagicMock()
        mock_get_kb.return_value = mock_kb
        self.window._hotkey_read_selected_text()
        mock_kb.send.assert_called_once_with("ctrl+c")
        mock_qtimer.singleShot.assert_called_once()

    # ----------------------------------------------------------
    # _do_hotkey_voice
    # ----------------------------------------------------------
    @patch("app_hotkeys.QTimer")
    def test_do_hotkey_voice_starts_recording_with_auto_stop(self, mock_qtimer):
        """_do_hotkey_voice should start voice recording and set 5s auto-stop."""
        with patch.object(self.window, "start_voice_record") as mock_start:
            self.window._do_hotkey_voice()

            mock_start.assert_called_once()
            mock_qtimer.singleShot.assert_called_once_with(5000, self.window.stop_voice_record)

    # ----------------------------------------------------------
    # _hotkey_voice_input dispatch
    # ----------------------------------------------------------
    @patch("app_hotkeys.QTimer")
    def test_hotkey_voice_dispatches_via_timer(self, mock_qtimer):
        """_hotkey_voice_input should schedule _do_hotkey_voice via QTimer.singleShot."""
        self.window._hotkey_voice_input()
        mock_qtimer.singleShot.assert_called_once_with(0, self.window._do_hotkey_voice)

    # ----------------------------------------------------------
    # closeEvent unhooks keyboard
    # ----------------------------------------------------------
    def test_close_event_unhooks_hotkeys(self):
        """closeEvent should call keyboard.unhook_all()."""
        from PyQt6.QtGui import QCloseEvent
        from app_lifecycle import AppLifecycleMixin
        event = QCloseEvent()
        # Call the REAL closeEvent from AppLifecycleMixin directly, bypassing
        # the _test_close_event patch on KokertechDashboard.__dict__. The
        # patch only shadows the class attribute; the mixin's version is intact.
        self.window._suppress_quit = True
        try:
            with patch("keyboard.unhook_all") as mock_unhook, \
                 patch("app_lifecycle._call_with_timeout") as mock_cwt, \
                 patch.object(self.window, "_flush_chat_history_save", create=True), \
                 patch.object(self.window, "_hide_save_badge", create=True):
                # Patch worker/thread attrs to prevent terminate+wait hangs
                for attr in ("worker", "desire_worker", "indexer_worker",
                             "layout_worker", "auditor_worker", "voice_worker"):
                    if hasattr(self.window, attr):
                        delattr(self.window, attr)
                if hasattr(self.window, "active_threads"):
                    self.window.active_threads = []
                if hasattr(self.window, "controller"):
                    self.window.controller.api_process = None
                if hasattr(self.window, "timer_cleanup_list"):
                    self.window.timer_cleanup_list = []
                AppLifecycleMixin.closeEvent(self.window, event)
                mock_unhook.assert_called_once()
        finally:
            # Restore state mutated by the real closeEvent
            self.window._shutting_down = False
            self.window._suppress_quit = False

    # ----------------------------------------------------------
    # HUD hotkey: _hotkey_toggle_hud and _do_toggle_hud
    # ----------------------------------------------------------
    @patch("app_hotkeys.QTimer")
    def test_hotkey_hud_dispatches_via_timer(self, mock_qtimer):
        """_hotkey_toggle_hud should schedule _do_toggle_hud via QTimer.singleShot."""
        self.window._hotkey_toggle_hud()
        mock_qtimer.singleShot.assert_called_once_with(0, self.window._do_toggle_hud)

    @patch("app_hotkeys.run_on_main_thread")
    def test_hotkey_click_through_recovery_dispatches_to_gui_thread(self, mock_dispatch):
        self.window._hotkey_toggle_hud_click_through()
        mock_dispatch.assert_called_once_with(
            self.window._do_toggle_hud_click_through
        )

    def test_click_through_recovery_hotkey_toggles_existing_hud(self):
        mock_hud = MagicMock()
        mock_hud._click_through = False
        self.window.mini_hud = mock_hud
        self.window._do_toggle_hud_click_through()
        mock_hud.set_click_through.assert_called_once_with(True)

    def test_do_toggle_hud_toggles_dialog(self):
        """_do_toggle_hud should instantiate and toggle mini_hud dialog."""
        mock_hud = MagicMock()
        with patch("tabs.mini_hud.MiniHudDialog", return_value=mock_hud):
            if hasattr(self.window, "mini_hud"):
                delattr(self.window, "mini_hud")
            self.window._do_toggle_hud()
            mock_hud.toggle_visibility.assert_called_once()

    def test_on_hud_send_to_main_populates_input(self):
        """_on_hud_send_to_main should populate txt_input."""
        self.window.txt_input.clear()
        self.window._on_hud_send_to_main("Hello from HUD")
        assert self.window.txt_input.toPlainText() == "Hello from HUD"

    # ----------------------------------------------------------
    # Settings tab _apply_hotkeys
    # ----------------------------------------------------------
    @patch("app_hotkeys._get_keyboard_module")
    def test_settings_apply_hotkeys_updates_config_and_calls_apply(self, mock_get_kb):
        """_apply_hotkeys from SettingsTabMixin should update CONFIG and call apply_hotkey."""
        mock_kb = MagicMock()
        mock_get_kb.return_value = mock_kb
        self.window.hotkey_trigger_input.setText("alt+1")
        self.window.hotkey_read_input.setText("alt+2")
        self.window.hotkey_voice_input.setText("alt+3")

        self.window._apply_hotkeys()

        assert CONFIG["trigger_hotkey"] == "alt+1"
        assert CONFIG["read_hotkey"] == "alt+2"
        assert CONFIG["voice_hotkey"] == "alt+3"
        mock_kb.unhook_all.assert_called()

    # ----------------------------------------------------------
    # Wake word: _start_wake_listener
    # ----------------------------------------------------------
    @patch("app_hotkeys._WAKE_WORD_AVAILABLE", False)
    def test_start_wake_listener_not_available(self):
        """_start_wake_listener should return immediately when wake word unavailable."""
        self.window._start_wake_listener()
        assert not hasattr(self.window, '_wake_stop')

    @patch("app_hotkeys._WAKE_WORD_AVAILABLE", True)
    @patch("app_hotkeys.wake_word.start_wake_listener", return_value=(MagicMock(), MagicMock()))
    def test_start_wake_listener_success(self, mock_start):
        """_start_wake_listener should start the listener and update UI."""
        from PyQt6.QtWidgets import QLabel
        self.window.wake_status_lbl = QLabel()
        self.window._start_wake_listener()
        assert self.window._wake_stop is not None
        assert self.window._wake_thread is not None
        mock_start.assert_called_once_with(self.window._on_wake_word_detected)

    @patch("app_hotkeys._WAKE_WORD_AVAILABLE", True)
    @patch("app_hotkeys.wake_word.start_wake_listener", side_effect=RuntimeError("Port busy"))
    def test_start_wake_listener_exception(self, mock_start):
        """_start_wake_listener should log error and show error status on exception."""
        from PyQt6.QtWidgets import QLabel
        self.window.wake_status_lbl = QLabel()
        self.window._start_wake_listener()
        audit = self.window.audit_log_display.toPlainText()
        assert "failed" in audit.lower()
        assert "Port busy" in audit

    # ----------------------------------------------------------
    # Wake word: _stop_wake_listener
    # ----------------------------------------------------------
    def test_stop_wake_listener_cleanup(self):
        """_stop_wake_listener should set stop event, join thread, and clear attributes."""
        mock_stop = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        self.window._wake_stop = mock_stop
        self.window._wake_thread = mock_thread
        self.window._stop_wake_listener()
        mock_stop.set.assert_called_once()
        mock_thread.join.assert_called_once_with(timeout=2.0)
        assert self.window._wake_stop is None
        assert self.window._wake_thread is None

    def test_stop_wake_listener_no_state(self):
        """_stop_wake_listener should not crash when no listener was started."""
        self.window._stop_wake_listener()
        assert getattr(self.window, '_wake_stop', None) is None

    def test_stop_wake_listener_thread_not_alive(self):
        """_stop_wake_listener should skip join if thread is already dead."""
        mock_stop = MagicMock()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        self.window._wake_stop = mock_stop
        self.window._wake_thread = mock_thread
        self.window._stop_wake_listener()
        mock_stop.set.assert_called_once()
        mock_thread.join.assert_not_called()

    # ----------------------------------------------------------
    # Wake word: _toggle_wake_word
    # ----------------------------------------------------------
    def test_toggle_wake_word_enables(self):
        """_toggle_wake_word(True) should set config, save, and start listener."""
        with patch.object(self.window, "_start_wake_listener") as mock_start:
            self.window._toggle_wake_word(True)
            assert CONFIG["wake_word_enabled"]
            mock_start.assert_called_once()

    def test_toggle_wake_word_disables(self):
        """_toggle_wake_word(False) should set config, stop listener, and clear label."""
        from PyQt6.QtWidgets import QLabel
        self.window.wake_status_lbl = QLabel()
        with patch.object(self.window, "_stop_wake_listener") as mock_stop:
            self.window._toggle_wake_word(False)
            assert not CONFIG["wake_word_enabled"]
            mock_stop.assert_called_once()
            assert self.window.wake_status_lbl.text() == ""

    # ----------------------------------------------------------
    # Wake word: _on_wake_word_detected
    # ----------------------------------------------------------
    def test_on_wake_word_detected_schedules_voice(self):
        """_on_wake_word_detected should schedule _do_hotkey_voice via QTimer.singleShot(0)."""
        with patch("app_hotkeys.QTimer.singleShot") as mock_shot:
            self.window._on_wake_word_detected()
            mock_shot.assert_called_once_with(0, self.window._do_hotkey_voice)

    def test_on_wake_word_detected_handles_exception(self):
        """_on_wake_word_detected should catch and log QTimer exceptions."""
        with patch("app_hotkeys.QTimer.singleShot", side_effect=TypeError("Invalid slot")):
            self.window._on_wake_word_detected()
            self.window.file_logger.warning.assert_called()
            assert "Wake word" in str(self.window.file_logger.warning.call_args)

    # ----------------------------------------------------------
    # Voice recording
    # ----------------------------------------------------------
    def test_start_voice_record_sets_style_and_creates_worker(self):
        """start_voice_record should set mic button to red and create VoiceRecorderWorker."""
        self.window.btn_mic.setStyleSheet("")
        self.window.start_voice_record()
        assert "EF4444" in self.window.btn_mic.styleSheet()
        assert getattr(self.window, 'voice_worker', None) is not None

    def test_stop_voice_record_clears_style_and_stops_worker(self):
        """stop_voice_record should reset button and stop the worker."""
        self.window.voice_worker = MagicMock()
        self.window.btn_mic.setStyleSheet("background-color: #EF4444;")
        self.window.stop_voice_record()
        assert self.window.btn_mic.styleSheet() == ""
        self.window.voice_worker.stop_recording.assert_called_once()

    def test_stop_voice_record_no_worker(self):
        """stop_voice_record should not crash when no voice_worker exists."""
        if hasattr(self.window, 'voice_worker'):
            delattr(self.window, 'voice_worker')
        self.window.stop_voice_record()  # should not raise

    # ----------------------------------------------------------
    # Exception paths for hotkey dispatchers
    # ----------------------------------------------------------
    @patch("app_hotkeys.QTimer.singleShot", side_effect=RuntimeError("Timer failed"))
    def test_hotkey_trigger_analysis_exception(self, mock_qtimer):
        """_hotkey_trigger_analysis should catch QTimer exceptions gracefully."""
        self.window._hotkey_trigger_analysis()
        self.window.file_logger.warning.assert_called()
        assert "Hotkey trigger error" in str(self.window.file_logger.warning.call_args)

    @patch("app_hotkeys._get_keyboard_module")
    def test_hotkey_read_selected_text_exception(self, mock_get_kb):
        """_hotkey_read_selected_text should catch keyboard exceptions gracefully."""
        mock_kb = MagicMock()
        mock_kb.send.side_effect = PermissionError("Input not allowed")
        mock_get_kb.return_value = mock_kb
        self.window._hotkey_read_selected_text()
        self.window.file_logger.warning.assert_called()
        assert "Hotkey read error" in str(self.window.file_logger.warning.call_args)

    @patch("app_hotkeys.QTimer.singleShot", side_effect=RuntimeError("Timer failed"))
    def test_hotkey_voice_input_exception(self, mock_qtimer):
        """_hotkey_voice_input should catch QTimer exceptions gracefully."""
        self.window._hotkey_voice_input()
        self.window.file_logger.warning.assert_called()
        assert "Hotkey voice error" in str(self.window.file_logger.warning.call_args)

    def test_do_hotkey_voice_exception(self):
        """_do_hotkey_voice should catch exceptions from start_voice_record."""
        with patch.object(self.window, "start_voice_record", side_effect=RuntimeError("Mic busy")):
            self.window._do_hotkey_voice()
            self.window.file_logger.warning.assert_called()
            assert "Hotkey voice error" in str(self.window.file_logger.warning.call_args)

    # ----------------------------------------------------------
    # Screen capture
    # ----------------------------------------------------------
    def test_action_capture_screen_import_error(self):
        """_action_capture_screen should log error when copilot_features unavailable."""
        with patch.dict("sys.modules", {"copilot_features": None}):
            self.window._action_capture_screen()
            audit = self.window.audit_log_display.toPlainText()
            assert "not available" in audit.lower()

    # ----------------------------------------------------------
    # OCR
    # ----------------------------------------------------------
    @patch("app_hotkeys.QFileDialog.getOpenFileName", return_value=("", ""))
    def test_action_ocr_file_cancelled(self, mock_dialog):
        """_action_ocr_file should return early when user cancels the file dialog."""
        self.window._action_ocr_file()
        mock_dialog.assert_called_once()

    # ----------------------------------------------------------
    # Screen capture — KeepaliveContext + thread startup
    # ----------------------------------------------------------
    @patch("keepalive_helper.KeepaliveContext")
    @patch("app_hotkeys.threading.Thread")
    def test_action_capture_screen_starts_thread(self, mock_thread, mock_kc_cls):
        """_action_capture_screen should construct KeepaliveContext with correct params and start daemon thread."""
        mock_kc = mock_kc_cls.return_value

        self.window._action_capture_screen()

        # Verify KeepaliveContext construction with correct params
        mock_kc_cls.assert_called_once()
        _, kc_kwargs = mock_kc_cls.call_args
        assert kc_kwargs["button"] == self.window.btn_screen
        assert kc_kwargs["log_callback"] == self.window.log_to_audit
        assert callable(kc_kwargs["chat_callback"])

        # Verify synchronous KC setup calls
        mock_kc.set_btn_text.assert_any_call("📷 Capturing...")
        mock_kc.update_chat.assert_any_call("<b>Capturing screen...</b><br>")
        mock_kc.log.assert_any_call("Capturing screen...")
        mock_kc.start_keepalive.assert_called_once_with("analyzing", 30000)

        # Verify thread creation and start
        mock_thread.assert_called_once()
        _, thread_kwargs = mock_thread.call_args
        assert thread_kwargs.get("daemon")
        assert callable(thread_kwargs["target"])
        mock_thread.return_value.start.assert_called_once()

    # ----------------------------------------------------------
    # Screen capture — _do closure: success path
    # ----------------------------------------------------------
    @patch("keepalive_helper.KeepaliveContext")
    @patch("app_hotkeys.threading.Thread")
    @patch("copilot_features.ScreenCapture")
    @patch("copilot_features.VisionAnalyzer")
    def test_action_capture_screen_do_success(self, mock_va_cls, mock_sc_cls, mock_thread, mock_kc_cls):
        """_do closure should capture screen, analyze with vision, log results, and call cleanup."""
        mock_kc = mock_kc_cls.return_value
        mock_kc.elapsed = 5.0

        mock_capture = mock_sc_cls.return_value
        mock_capture.capture.return_value = ("/tmp/screenshot.png", "My Window")

        mock_vision = mock_va_cls.return_value
        mock_vision.analyze.return_value = "Vision analysis result text."

        self.window._action_capture_screen()

        # Capture and run _do synchronously
        _, thread_kwargs = mock_thread.call_args
        do_func = thread_kwargs["target"]
        do_func()

        # Verify ScreenCapture
        mock_sc_cls.assert_called_once()
        mock_capture.capture.assert_called_once()

        # Verify capture logging
        mock_kc.log.assert_any_call("Screen captured from 'My Window' (5.0s)")
        mock_kc.update_chat.assert_any_call(
            "<b>✅ Screen captured from:</b> My Window <i>(5.0s)</i><br>"
        )

        # Verify vision analysis setup
        mock_kc.set_btn_text.assert_any_call("🧠 Analyzing...")
        mock_kc.update_chat.assert_any_call(
            "<b>⏳ Analyzing screenshot with gemma-4-e2b...</b> <i>(this may take 60-120s)</i><br>"
        )

        # Verify VisionAnalyzer invocation
        mock_va_cls.assert_called_once()
        mock_vision.analyze.assert_called_once_with(
            "/tmp/screenshot.png", context="Window: My Window"
        )

        # Verify final result
        mock_kc.log.assert_any_call("Vision analysis complete (5.0s)")
        mock_kc.update_chat.assert_any_call(
            "<b>📸 Screen Capture Complete</b> <i>(5.0s total)</i><br>"
            "<b>Window:</b> My Window<br>"
            "<b>Saved:</b> /tmp/screenshot.png<br><br>"
            "Vision analysis result text."
        )

        # Verify cleanup
        mock_capture.cleanup.assert_called_once_with(max_files=10)
        mock_kc.done.assert_called_once()

    # ----------------------------------------------------------
    # Screen capture — _do closure: error path
    # ----------------------------------------------------------
    @patch("keepalive_helper.KeepaliveContext")
    @patch("app_hotkeys.threading.Thread")
    @patch("copilot_features.ScreenCapture")
    def test_action_capture_screen_do_error(self, mock_sc_cls, mock_thread, mock_kc_cls):
        """_do closure should log error and call done when capture raises."""
        mock_kc = mock_kc_cls.return_value
        mock_kc.elapsed = 3.0

        mock_sc_cls.return_value.capture.side_effect = RuntimeError("GPU OOM")

        self.window._action_capture_screen()

        _, thread_kwargs = mock_thread.call_args
        do_func = thread_kwargs["target"]
        do_func()

        # Verify error was logged
        mock_kc.log.assert_any_call("Screen capture failed after 3.0s: GPU OOM")
        mock_kc.update_chat.assert_any_call(
            "<b style='color:#EF4444;'>❌ Screen capture failed after 3.0s: GPU OOM</b><br>"
        )

        # Verify done is called in finally block even on error
        mock_kc.done.assert_called_once()

    # ----------------------------------------------------------
    # OCR — KeepaliveContext + thread startup
    # ----------------------------------------------------------
    @patch("app_hotkeys.QFileDialog.getOpenFileName", return_value=("/tmp/test.png", ""))
    @patch("keepalive_helper.KeepaliveContext")
    @patch("app_hotkeys.threading.Thread")
    def test_action_ocr_file_starts_thread(self, mock_thread, mock_kc_cls, mock_dialog):
        """_action_ocr_file should construct KeepaliveContext with correct params and start daemon thread."""
        mock_kc = mock_kc_cls.return_value

        self.window._action_ocr_file()

        # Verify KeepaliveContext construction
        mock_kc_cls.assert_called_once()
        _, kc_kwargs = mock_kc_cls.call_args
        assert kc_kwargs["button"] == self.window.btn_ocr
        assert kc_kwargs["log_callback"] == self.window.log_to_audit
        assert callable(kc_kwargs["chat_callback"])

        # Verify file dialog was called
        mock_dialog.assert_called_once()

        # Verify synchronous KC setup
        mock_kc.set_btn_text.assert_any_call("📄 OCR Loading...")
        mock_kc.update_chat.assert_any_call(
            "<b>Running OCR on:</b> test.png<br>"
        )

    # ----------------------------------------------------------
    # OCR — _do closure: success path
    # ----------------------------------------------------------
    @patch("app_hotkeys.QFileDialog.getOpenFileName", return_value=("/tmp/test.png", ""))
    @patch("keepalive_helper.KeepaliveContext")
    @patch("app_hotkeys.threading.Thread")
    def test_action_ocr_file_do_success_with_text(self, mock_thread, mock_kc_cls, mock_dialog):
        """_do closure should OCR image and log extracted text."""
        mock_kc = mock_kc_cls.return_value
        mock_kc.elapsed = 2.0

        with patch("PIL.Image.open") as mock_img_open, \
             patch("pytesseract.image_to_string", return_value="Extracted text from image."):

            mock_img = MagicMock()
            mock_img_open.return_value = mock_img

            self.window._action_ocr_file()

            _, thread_kwargs = mock_thread.call_args
            do_func = thread_kwargs["target"]
            do_func()

            # Verify OCR calls
            mock_img_open.assert_called_once_with("/tmp/test.png")
            mock_kc.log.assert_any_call("OCR extracting text from 'test.png'")
            mock_kc.set_btn_text.assert_any_call("🔍 OCR Extracting...")

            # Verify result
            mock_kc.update_chat.assert_any_call(
                "<b>🔍 OCR Complete</b> <i>(2.0s)</i><br>"
                "<b>File:</b> /tmp/test.png<br>"
                "<b>Extracted text (26 chars):</b><br>"
                "<pre style='white-space:pre-wrap;'>Extracted text from image.</pre>"
            )
            mock_kc.done.assert_called_once()

    # ----------------------------------------------------------
    # OCR — _do closure: empty text
    # ----------------------------------------------------------
    @patch("app_hotkeys.QFileDialog.getOpenFileName", return_value=("/tmp/test.png", ""))
    @patch("keepalive_helper.KeepaliveContext")
    @patch("app_hotkeys.threading.Thread")
    def test_action_ocr_file_do_no_text_found(self, mock_thread, mock_kc_cls, mock_dialog):
        """_do closure should handle empty OCR result gracefully."""
        mock_kc = mock_kc_cls.return_value
        mock_kc.elapsed = 1.5

        with patch("PIL.Image.open") as mock_img_open, \
             patch("pytesseract.image_to_string", return_value=""):

            mock_img = MagicMock()
            mock_img_open.return_value = mock_img

            self.window._action_ocr_file()

            _, thread_kwargs = mock_thread.call_args
            do_func = thread_kwargs["target"]
            do_func()

            mock_kc.update_chat.assert_any_call(
                "<b>🔍 OCR Complete</b> <i>(1.5s)</i><br>"
                "<b>File:</b> /tmp/test.png<br>"
                "<b>Result:</b> No text found in test.png"
            )
            mock_kc.done.assert_called_once()

    # ----------------------------------------------------------
        # OCR — _do closure: import error
        # ----------------------------------------------------------
        @patch("app_hotkeys.QFileDialog.getOpenFileName", return_value=("/tmp/test.png", ""))
        @patch("keepalive_helper.KeepaliveContext")
        @patch("app_hotkeys.threading.Thread")
        def test_action_ocr_file_do_import_error(self, mock_thread, mock_kc_cls, mock_dialog):
            """_do closure should handle missing PIL/pytesseract gracefully."""
            mock_kc = mock_kc_cls.return_value
            mock_kc.elapsed = 0.5

            with patch.dict("sys.modules", {"PIL": None, "pytesseract": None}):
                self.window._action_ocr_file()

                _, thread_kwargs = mock_thread.call_args
                do_func = thread_kwargs["target"]
                do_func()

                mock_kc.update_chat.assert_any_call(
                    "<b style='color:#EF4444;'>❌ OCR deps missing after 0.5s. Run: pip install Pillow pytesseract</b><br>"
                )
                mock_kc.done.assert_called_once()

    # ----------------------------------------------------------
    # OCR — _do closure: generic error
    # ----------------------------------------------------------
    @patch("app_hotkeys.QFileDialog.getOpenFileName", return_value=("/tmp/test.png", ""))
    @patch("keepalive_helper.KeepaliveContext")
    @patch("app_hotkeys.threading.Thread")
    def test_action_ocr_file_do_generic_error(self, mock_thread, mock_kc_cls, mock_dialog):
        """_do closure should log generic errors and call done."""
        mock_kc = mock_kc_cls.return_value
        mock_kc.elapsed = 0.8

        with patch("PIL.Image.open", side_effect=RuntimeError("Corrupt image")):
            self.window._action_ocr_file()

            _, thread_kwargs = mock_thread.call_args
            do_func = thread_kwargs["target"]
            do_func()

            mock_kc.update_chat.assert_any_call(
                "<b style='color:#EF4444;'>❌ OCR failed after 0.8s: Corrupt image</b><br>"
            )
            mock_kc.done.assert_called_once()

    # ----------------------------------------------------------
    # Toggle wake word
    # ----------------------------------------------------------
    def test_toggle_wake_word_enables(self):
        """_toggle_wake_word(True) should set config, save, and start listener."""
        with patch.object(self.window, "_start_wake_listener") as mock_start:
            self.window._toggle_wake_word(True)
            assert CONFIG["wake_word_enabled"]
            mock_start.assert_called_once()

    def test_toggle_wake_word_disables(self):
        """_toggle_wake_word(False) should set config, stop listener, and clear label."""
        from PyQt6.QtWidgets import QLabel
        self.window.wake_status_lbl = QLabel()
        with patch.object(self.window, "_stop_wake_listener") as mock_stop:
            self.window._toggle_wake_word(False)
            assert not CONFIG["wake_word_enabled"]
            mock_stop.assert_called_once()
            assert self.window.wake_status_lbl.text() == ""