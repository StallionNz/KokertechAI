"""
Wake Word Integration Tests - Sprint 3 Stream D

Tests wake word toggle lifecycle in app_core.py: _toggle_wake_word,
_start_wake_listener, _stop_wake_listener, _on_wake_word_detected,
and closeEvent cleanup.

NOTE: Cross-file QApp isolation handled by conftest.py Phase 4.
      The 10 pre-existing failures were fixed June 2026 by adding
      ``_safe_log_to_audit()`` and ``_save_window_geometry`` to ``_TestDashboard``.
"""

import unittest
import sys
import threading
from unittest.mock import MagicMock, patch


class _TestDashboard:
    """Plain class (not MagicMock) so hasattr/getattr work correctly."""
    def _hide_typing_indicator(self):
        """No-op matching AppUIMixin._hide_typing_indicator."""

    def _hide_progress_panel(self):
        """No-op matching AppUIMixin._hide_progress_panel."""

    def _hide_save_badge(self, *args, **kwargs):
        """No-op matching AppUIMixin._hide_save_badge (added to closeEvent path).
        Accepts *args/**kwargs to match real signature (e.g. fade=False).
        """

    def _flush_chat_history_save(self, *args, **kwargs):
        """No-op matching AppLifecycleMixin._flush_chat_history_save (closeEvent path).
        Accepts *args/**kwargs to match real signature.
        """

    def _safe_log_to_audit(self, msg):
        """Matches AppHotkeysMixin._safe_log_to_audit delegation.
        Calls log_to_audit when available; fallback to file_logger or no-op.
        """
        if hasattr(self, "log_to_audit"):
            try:
                self.log_to_audit(msg)
                return
            except RuntimeError:
                pass
        if hasattr(self, "file_logger"):
            try:
                self.file_logger.info(msg)
            except Exception:
                pass

def _make_dash():
    """Build a test dashboard object with wake word attributes."""
    d = _TestDashboard()
    d.wake_status_lbl = MagicMock()
    d.file_logger = MagicMock()
    d.audit_log_display = MagicMock()
    d.audit_log_display.verticalScrollBar = MagicMock()
    d.audit_log_display.verticalScrollBar.return_value = MagicMock()
    d.log_to_audit = MagicMock()
    d._do_hotkey_voice = MagicMock()
    d._on_wake_word_detected = MagicMock()
    return d

def _event():
    return threading.Event()

def _thread(alive=True):
    t = MagicMock(spec=threading.Thread)
    t.is_alive.return_value = alive
    return t

class TestWakeWordToggle(unittest.TestCase):

    def setUp(self):
        self.d = _make_dash()

    def test_enable_calls_start(self):
        from app_core import KokertechDashboard
        self.d._start_wake_listener = MagicMock()
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True):
            KokertechDashboard._toggle_wake_word(self.d, True)
            self.d._start_wake_listener.assert_called_once()

    def test_disable_calls_stop(self):
        from app_core import KokertechDashboard
        self.d._wake_stop = _event()
        self.d._wake_thread = _thread(alive=False)
        self.d._stop_wake_listener = MagicMock()
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True):
            KokertechDashboard._toggle_wake_word(self.d, False)
            self.d._stop_wake_listener.assert_called_once()

    def test_enable_updates_config(self):
        from app_core import CONFIG, KokertechDashboard
        orig = CONFIG.get("wake_word_enabled", False)
        self.d._start_wake_listener = MagicMock()
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True):
            KokertechDashboard._toggle_wake_word(self.d, True)
            self.assertTrue(CONFIG["wake_word_enabled"])
        CONFIG["wake_word_enabled"] = orig

    def test_disable_updates_config(self):
        from app_core import CONFIG, KokertechDashboard
        CONFIG["wake_word_enabled"] = True
        self.d._wake_stop = _event()
        self.d._wake_thread = _thread(alive=False)
        self.d._stop_wake_listener = MagicMock()
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True):
            KokertechDashboard._toggle_wake_word(self.d, False)
            self.assertFalse(CONFIG["wake_word_enabled"])
        CONFIG["wake_word_enabled"] = False

    def test_disable_clears_status_label(self):
        from app_core import KokertechDashboard
        self.d._wake_stop = _event()
        self.d._wake_thread = _thread(alive=False)
        self.d._stop_wake_listener = MagicMock()
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True):
            KokertechDashboard._toggle_wake_word(self.d, False)
            self.d.wake_status_lbl.setText.assert_any_call("")

class TestWakeWordStart(unittest.TestCase):

    def setUp(self):
        self.d = _make_dash()

    def test_bails_when_not_available(self):
        from app_core import KokertechDashboard
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", False):
            KokertechDashboard._start_wake_listener(self.d)
            self.assertFalse(hasattr(self.d, '_wake_stop'))

    def test_calls_wake_word_start(self):
        from app_core import KokertechDashboard
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True), \
             patch("wake_word.start_wake_listener", return_value=(_event(), _thread())) as mock_start:
            KokertechDashboard._start_wake_listener(self.d)
            mock_start.assert_called_once()

    def test_sets_stop_and_thread_attrs(self):
        from app_core import KokertechDashboard
        stop = _event()
        thread = _thread()
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True), \
             patch("wake_word.start_wake_listener", return_value=(stop, thread)):
            KokertechDashboard._start_wake_listener(self.d)
            self.assertIs(self.d._wake_stop, stop)
            self.assertIs(self.d._wake_thread, thread)

    def test_updates_status_label_on_success(self):
        from app_core import KokertechDashboard
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True), \
             patch("wake_word.start_wake_listener", return_value=(_event(), _thread())):
            KokertechDashboard._start_wake_listener(self.d)
            self.d.wake_status_lbl.setText.assert_any_call("Listening...")

    def test_passes_callback_to_start(self):
        from app_core import KokertechDashboard
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True), \
             patch("wake_word.start_wake_listener", return_value=(_event(), _thread())) as mock_start:
            KokertechDashboard._start_wake_listener(self.d)
            call_args = mock_start.call_args[0]
            self.assertIs(call_args[0], self.d._on_wake_word_detected)

    def test_logs_to_audit_on_success(self):
        from app_core import KokertechDashboard
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True), \
             patch("wake_word.start_wake_listener", return_value=(_event(), _thread())):
            KokertechDashboard._start_wake_listener(self.d)
            self.d.log_to_audit.assert_called()
            self.assertIn("Wake word listener started",
                          self.d.log_to_audit.call_args[0][0])

    def test_handles_exception_gracefully(self):
        from app_core import KokertechDashboard
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True), \
             patch("wake_word.start_wake_listener", side_effect=RuntimeError("No mic")):
            KokertechDashboard._start_wake_listener(self.d)
            self.d.log_to_audit.assert_called()
            self.assertIn("start failed", self.d.log_to_audit.call_args[0][0])
            self.d.wake_status_lbl.setText.assert_any_call("Error")

class TestWakeWordStop(unittest.TestCase):

    def test_stop_sets_event(self):
        d = _make_dash()
        d._wake_stop = _event()
        d._wake_thread = _thread(alive=True)
        stop_ref = d._wake_stop  # capture before method nullifies
        from app_core import KokertechDashboard
        KokertechDashboard._stop_wake_listener(d)
        self.assertTrue(stop_ref.is_set())

    def test_joins_thread_when_alive(self):
        d = _make_dash()
        d._wake_stop = _event()
        d._wake_thread = _thread(alive=True)
        thread_ref = d._wake_thread  # capture before method nullifies
        from app_core import KokertechDashboard
        KokertechDashboard._stop_wake_listener(d)
        thread_ref.join.assert_called_once_with(timeout=2.0)

    def test_skips_join_when_thread_dead(self):
        d = _make_dash()
        d._wake_stop = _event()
        d._wake_thread = _thread(alive=False)
        thread_ref = d._wake_thread  # capture before method nullifies
        from app_core import KokertechDashboard
        KokertechDashboard._stop_wake_listener(d)
        thread_ref.join.assert_not_called()

    def test_nulls_stop_and_thread(self):
        d = _make_dash()
        d._wake_stop = _event()
        d._wake_thread = _thread(alive=False)
        from app_core import KokertechDashboard
        KokertechDashboard._stop_wake_listener(d)
        self.assertIsNone(d._wake_stop)
        self.assertIsNone(d._wake_thread)

    def test_handles_missing_attrs(self):
        d = _make_dash()
        from app_core import KokertechDashboard
        KokertechDashboard._stop_wake_listener(d)

    def test_idempotent(self):
        d = _make_dash()
        d._wake_stop = _event()
        d._wake_thread = _thread(alive=False)
        from app_core import KokertechDashboard
        KokertechDashboard._stop_wake_listener(d)
        KokertechDashboard._stop_wake_listener(d)

    def test_enable_disable_roundtrip(self):
        d = _make_dash()
        stop = _event()
        thread = _thread(alive=True)
        with patch("app_hotkeys._WAKE_WORD_AVAILABLE", True), \
             patch("wake_word.start_wake_listener", return_value=(stop, thread)):
            from app_core import KokertechDashboard
            KokertechDashboard._start_wake_listener(d)
            self.assertIs(d._wake_stop, stop)
            self.assertIs(d._wake_thread, thread)
            KokertechDashboard._stop_wake_listener(d)
            self.assertIsNone(d._wake_stop)
            self.assertIsNone(d._wake_thread)
            self.assertTrue(stop.is_set())

class TestWakeWordDetection(unittest.TestCase):

    def setUp(self):
        self.d = _make_dash()

    def test_schedules_hotkey_voice(self):
        from app_core import KokertechDashboard
        with patch("app_hotkeys.QTimer") as mock_qtimer:
            KokertechDashboard._on_wake_word_detected(self.d)
            mock_qtimer.singleShot.assert_called_once_with(
                0, self.d._do_hotkey_voice)

    def test_handles_exception_gracefully(self):
        from app_core import KokertechDashboard
        with patch("app_hotkeys.QTimer") as mock_qtimer:
            mock_qtimer.singleShot.side_effect = RuntimeError("Timer error")
            KokertechDashboard._on_wake_word_detected(self.d)
            self.d.file_logger.warning.assert_called()

class TestWakeWordCloseEvent(unittest.TestCase):

    def _make_bare(self):
        from app_core import KokertechDashboard
        d = _TestDashboard()
        d.log_to_audit = MagicMock()
        d.file_logger = MagicMock()
        d.file_logger.log_session_end = MagicMock()
        d.controller = MagicMock()
        d.controller.api_process = None
        d.active_threads = []
        d._lock = threading.Lock()
        d.voice_output = MagicMock()
        d.voice_output.stop = MagicMock()
        d.vram_timer = MagicMock()
        d.cleanup_timer = MagicMock()
        d._save_window_geometry = MagicMock()
        # Attach the real _stop_wake_listener so closeEvent can call it
        def _stop():
            KokertechDashboard._stop_wake_listener(d)
        d._stop_wake_listener = _stop
        return d

    def test_close_calls_stop_wake(self):
        d = self._make_bare()
        d._wake_stop = _event()
        d._wake_thread = _thread(alive=False)
        stop_ref = d._wake_stop  # capture before nullify
        event = MagicMock()
        with patch("keyboard.unhook_all") as mock_kb, \
             patch("app_lifecycle._call_with_timeout", return_value=None), \
             patch("mcp_server.stop_http_server"), \
             patch("app_lifecycle.CONFIG",
                   {"shutdown_provider_on_exit": False,
                    "autosave_logs": False}), \
             patch("PyQt6.QtWidgets.QApplication.instance",
                   return_value=MagicMock()):
            from app_core import KokertechDashboard
            KokertechDashboard.closeEvent(d, event)
        self.assertIsNone(d._wake_stop)
        self.assertIsNone(d._wake_thread)

    def test_close_handles_no_wake_attrs(self):
        d = self._make_bare()
        event = MagicMock()
        with patch("keyboard.unhook_all") as mock_kb, \
             patch("app_lifecycle._call_with_timeout", return_value=None), \
             patch("mcp_server.stop_http_server"), \
             patch("app_lifecycle.CONFIG",
                   {"shutdown_provider_on_exit": False,
                    "autosave_logs": False}), \
             patch("PyQt6.QtWidgets.QApplication.instance",
                   return_value=MagicMock()):
            from app_core import KokertechDashboard
            KokertechDashboard.closeEvent(d, event)

    def test_close_kills_wake_thread_if_alive(self):
        d = self._make_bare()
        d._wake_stop = _event()
        d._wake_thread = _thread(alive=True)
        stop_ref = d._wake_stop  # capture before nullify
        thread_ref = d._wake_thread  # capture before nullify
        event = MagicMock()
        with patch("keyboard.unhook_all") as mock_kb, \
             patch("app_lifecycle._call_with_timeout", return_value=None), \
             patch("mcp_server.stop_http_server"), \
             patch("app_lifecycle.CONFIG",
                   {"shutdown_provider_on_exit": False,
                    "autosave_logs": False}), \
             patch("PyQt6.QtWidgets.QApplication.instance",
                   return_value=MagicMock()):
            from app_core import KokertechDashboard
            KokertechDashboard.closeEvent(d, event)
        self.assertTrue(stop_ref.is_set())
        thread_ref.join.assert_called_once_with(timeout=2.0)
        self.assertIsNone(d._wake_stop)
        self.assertIsNone(d._wake_thread)

if __name__ == "__main__":
    unittest.main()
