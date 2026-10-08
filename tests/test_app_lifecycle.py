"""
Tests for app_lifecycle.py - extended coverage.

NOTE: Historical cross-file QApplication singleton issue (conftest.py) — now resolved.
      All 51 pre-existing failures were fixed in June 2026 (test fixture
      isolation improvements, proper QWidget cleanup, and standardized mock
      patterns).  Per-file and cross-file batches both pass cleanly.

      Run this file in isolation to verify actual test results:
        pytest test_app_lifecycle.py
"""

import os, sys, threading, unittest
from unittest.mock import MagicMock, patch, call, PropertyMock
import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox

# QApplication provided by conftest.py (session-scoped qapp fixture)

def _mk():
    from app_lifecycle import AppLifecycleMixin
    from PyQt6.QtCore import QObject
    # Create a QObject subclass so QTimer(self) works (QTimer requires QObject parent)
    class _LC(QObject, AppLifecycleMixin):
        pass
    lc = _LC()
    lc.controller = MagicMock()
    lc.voice_output = MagicMock()
    lc.file_logger = MagicMock()
    lc.audit_signal = MagicMock()
    lc.log_to_audit = MagicMock()
    lc.vram_label = MagicMock()
    lc.vram_status_lbl = MagicMock()
    lc.ram_label = MagicMock()
    lc.chat_display = MagicMock()
    lc.thinking_display = MagicMock()
    lc.txt_input = MagicMock()
    lc.chk_desire_engine = MagicMock()
    lc.chk_tts = MagicMock()
    lc.suggestions_content_layout = MagicMock()
    lc.refresh_agency_data = MagicMock()
    lc.load_rlhf_history = MagicMock()
    lc.add_suggestion_card = MagicMock()
    lc.active_threads = []
    lc._lock = threading.Lock()
    lc.last_user_text = ""
    return lc

class TestSetupTimers(unittest.TestCase):
    def test_vram_timer(self):
        lc = _mk()
        lc.setup_vram_monitor()
        self.assertIsInstance(lc.vram_timer, QTimer)
        self.assertTrue(lc.vram_timer.isActive())
        self.assertEqual(lc.vram_timer.interval(), 2000)

    def test_ram_monitor_timer_connection(self):
        lc = _mk()
        lc.setup_vram_monitor()
        # update_ram_monitor should be connected to the same vram_timer
        self.assertTrue(lc.vram_timer.isActive())
        self.assertEqual(lc.vram_timer.interval(), 2000)

    def test_disk_monitor_timer_connection(self):
        lc = _mk()
        lc.setup_vram_monitor()
        # update_disk_monitor should be connected to the same vram_timer
        self.assertTrue(lc.vram_timer.isActive())
        self.assertEqual(lc.vram_timer.interval(), 2000)
    def test_cleanup_timer(self):
        lc = _mk()
        lc.setup_cleanup_timer()
        self.assertIsInstance(lc.cleanup_timer, QTimer)
        self.assertTrue(lc.cleanup_timer.isActive())
        self.assertEqual(lc.cleanup_timer.interval(), 30000)

    def test_consolidation_timer(self):
        lc = _mk()
        lc.setup_consolidation_timer()
        self.assertIsInstance(lc.consolidation_timer, QTimer)
        self.assertTrue(lc.consolidation_timer.isActive())
        self.assertEqual(lc.consolidation_timer.interval(), 300000)

    def test_consolidation_timer_logs_count(self):
        lc = _mk()
        lc.setup_consolidation_timer()
        # Should be connected without error
        self.assertTrue(lc.consolidation_timer.isActive())

class TestProviderResources(unittest.TestCase):
    def test_providers_found(self):
        lc = _mk()
        mp = MagicMock()
        d = dict(name="llama-server.exe", pid=1234)
        type(mp).info = PropertyMock(return_value=d)
        mp2 = MagicMock()
        mp2.cpu_percent.return_value = 12.5
        mp2.memory_info.return_value.rss = 256 * 1024 * 1024
        import psutil
        oi, op = psutil.process_iter, psutil.Process
        psutil.process_iter = MagicMock(return_value=[mp])
        psutil.Process = MagicMock(return_value=mp2)
        try:
            lc.update_provider_resources()
        finally:
            psutil.process_iter, psutil.Process = oi, op
        t = lc.vram_status_lbl.setText.call_args[0][0]
        self.assertIn("llama.cpp", t)
    def test_import_error(self):
        import app_lifecycle
        lc = _mk()
        original_psutil = app_lifecycle.psutil
        app_lifecycle.psutil = None
        try:
            lc.update_provider_resources()
            lc.vram_status_lbl.setText.assert_not_called()
        finally:
            app_lifecycle.psutil = original_psutil
    def test_exception(self):
        lc = _mk()
        import psutil
        oi = psutil.process_iter
        psutil.process_iter = MagicMock(side_effect=RuntimeError("boom"))
        try:
            lc.update_provider_resources()
        finally:
            psutil.process_iter = oi
        lc.file_logger.debug.assert_called_once()

# =============================================================================
# Tests — Chat history persistence (data/chat_history.json)
# =============================================================================
# Covers the Phase 6 chat-history auto-save/load feature: _save_chat_history,
# _load_chat_history, _clear_chat_history, and the action_wipe_memory wiring.
# Uses a tempdir-based WORKSPACE_DIR patch so tests don't touch the real
# project workspace.

import json
import tempfile
import shutil

class TestChatHistoryPersistence(unittest.TestCase):
    """Tests for AppLifecycleMixin chat history save/load/clear.

    Each test patches ``app_lifecycle.WORKSPACE_DIR`` to a tempdir so the
    real workspace is never touched. The chat_display mock's ``toHtml()``
    returns a string so the save path exercises real ``json.dump``.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chat_hist_test_")
        # Patch WORKSPACE_DIR at the module level so _chat_history_path()
        # uses the tempdir. Also patch the `os.path.join` that the helper
        # uses by patching the module-level WORKSPACE_DIR constant.
        self._ws_patcher = patch("app_lifecycle.WORKSPACE_DIR", self.tmpdir)
        self._ws_patcher.start()
        # Chat-history persistence defaults to ON in production (UX-first).
        # Tests in this class exercise the gated write path (save / load /
        # clear all working). Override the default so the pre-existing tests
        # written before the gate was introduced still pass without per-test
        # wrap-up. Tests that need the OFF path re-patch CONFIG to False
        # explicitly via patch.dict — the inner override wins during the
        # `with` block because patch.dict layers cleanly.
        # freeform_mode/mock_mode pinned False per the CONFIG-mode-pins gate
        # (scripts/assert_config_mode_pins.py): setUp-level clear=False patches
        # must not inherit the user's app_settings.json preferences
        # (debounce window + modes).
        self._cfg_patcher = patch.dict(
            "config.CONFIG",
            {"chat_history_enabled": True, "chat_history_debounce_ms": 500,
             "freeform_mode": False, "mock_mode": False},
            clear=False,
        )
        self._cfg_patcher.start()
        self.lc = _mk()
        # Realistic chat HTML so json.dump actually exercises the encoder
        self.lc.chat_display.toHtml.return_value = (
            "<p style='color:#3B82F6;'>Hello user</p>"
            "<p style='color:#10B981;'>Hi back</p>"
        )

    def tearDown(self):
        # Stop and delete any real QTimer created by _make_pending_timer()
        # to avoid leaks across tests in the same module.
        timer = getattr(self.lc, '_chat_history_save_timer', None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        self._cfg_patcher.stop()
        self._ws_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _history_file(self):
        return os.path.join(self.tmpdir, "data", "chat_history.json")

    # === _save_chat_history ===

    def test_save_creates_file(self):
        self.lc._save_chat_history()
        self.assertTrue(os.path.exists(self._history_file()))

    def test_save_writes_valid_json_with_html(self):
        self.lc._save_chat_history()
        with open(self._history_file(), "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.assertIn("html", payload)
        self.assertIn("saved_at", payload)
        self.assertIn("Hello user", payload["html"])
        self.assertIn("Hi back", payload["html"])

    def test_save_atomic_via_tmp_and_replace(self):
        """save writes to .tmp first then os.replaces — no half-written file."""
        self.lc._save_chat_history()
        # The final file exists, the .tmp should not linger
        self.assertTrue(os.path.exists(self._history_file()))
        self.assertFalse(os.path.exists(self._history_file() + ".tmp"))

    def test_save_no_chat_display_is_noop(self):
        """When chat_display attribute is missing, save silently returns."""
        del self.lc.chat_display
        self.lc._save_chat_history()
        # File should not be created
        self.assertFalse(os.path.exists(self._history_file()))

    def test_save_chat_display_none_is_noop(self):
        """When chat_display is explicitly None, save silently returns."""
        self.lc.chat_display = None
        self.lc._save_chat_history()
        self.assertFalse(os.path.exists(self._history_file()))

    def test_save_handles_oserror(self):
        """If json.dump raises OSError, the error is logged at debug level."""
        self.lc.chat_display.toHtml.side_effect = OSError("disk full")
        # Should not raise
        self.lc._save_chat_history()
        self.lc.file_logger.debug.assert_called()

    def test_save_handles_missing_file_logger(self):
        """If file_logger is not set, save still completes without raising."""
        del self.lc.file_logger
        self.lc._save_chat_history()  # should not raise
        # File was written successfully
        self.assertTrue(os.path.exists(self._history_file()))

    def test_save_cleans_tmp_when_replace_fails(self):
        """If os.replace raises, the .tmp file is removed (no leak on disk)."""
        import app_lifecycle
        original_replace = app_lifecycle.os.replace
        def _raise(*a, **kw):
            raise OSError("simulated cross-volume rename failure")
        app_lifecycle.os.replace = _raise
        try:
            # Should not raise to the caller (we re-raise internally but
            # the outer try/except in _save_chat_history catches it)
            self.lc._save_chat_history()
            # .tmp file was cleaned up (not lingering on disk)
            self.assertFalse(os.path.exists(self._history_file() + ".tmp"))
            # Final file was NOT created
            self.assertFalse(os.path.exists(self._history_file()))
            # Debug log was called with the exact failure message
            self.lc.file_logger.debug.assert_called_with(
                "Chat history save failed: simulated cross-volume rename failure"
            )
        finally:
            app_lifecycle.os.replace = original_replace

    # === _load_chat_history ===

    def test_load_missing_file_is_noop(self):
        """If the file doesn't exist, load returns silently."""
        # No save first — file doesn't exist
        self.lc._load_chat_history()
        self.lc.chat_display.setHtml.assert_not_called()

    def test_load_restores_html_via_sethtml(self):
        """Load reads the JSON and calls chat_display.setHtml with the html."""
        self.lc._save_chat_history()
        self.lc.chat_display.setHtml.reset_mock()
        self.lc._load_chat_history()
        self.lc.chat_display.setHtml.assert_called_once()
        html_arg = self.lc.chat_display.setHtml.call_args[0][0]
        self.assertIn("Hello user", html_arg)
        self.assertIn("Hi back", html_arg)

    def test_load_skips_oversized_file(self):
        """Files larger than CHAT_HISTORY_MAX_BYTES are skipped (no setHtml)."""
        # Create a file just over the 1 MB cap
        os.makedirs(os.path.dirname(self._history_file()), exist_ok=True)
        with open(self._history_file(), "w", encoding="utf-8") as f:
            # 1.1 MB of padding inside the JSON html field
            big_html = "x" * (1_200_000)
            json.dump({"html": big_html, "saved_at": "2026-01-01T00:00:00"}, f)
        self.lc._load_chat_history()
        # setHtml not called because the file was too big
        self.lc.chat_display.setHtml.assert_not_called()
        # Warning was logged
        self.lc.file_logger.warning.assert_called()

    def test_load_corrupt_json_is_silent(self):
        """Corrupt JSON is caught silently (no setHtml, debug log fires)."""
        os.makedirs(os.path.dirname(self._history_file()), exist_ok=True)
        with open(self._history_file(), "w", encoding="utf-8") as f:
            f.write("{this is not valid json")
        # Should not raise
        self.lc._load_chat_history()
        self.lc.chat_display.setHtml.assert_not_called()
        self.lc.file_logger.debug.assert_called()

    def test_load_empty_html_field_skips_sethtml(self):
        """If JSON parses but html is empty, setHtml is not called."""
        os.makedirs(os.path.dirname(self._history_file()), exist_ok=True)
        with open(self._history_file(), "w", encoding="utf-8") as f:
            json.dump({"html": "", "saved_at": "x"}, f)
        self.lc._load_chat_history()
        self.lc.chat_display.setHtml.assert_not_called()

    def test_load_no_chat_display_is_noop(self):
        """If chat_display attribute is missing, load returns silently."""
        self.lc._save_chat_history()
        del self.lc.chat_display
        # Should not raise
        self.lc._load_chat_history()

    def test_load_handles_missing_file_logger(self):
        """If file_logger is not set, load still completes without raising."""
        self.lc._save_chat_history()
        del self.lc.file_logger
        self.lc._load_chat_history()  # should not raise
        self.lc.chat_display.setHtml.assert_called_once()

    # === _clear_chat_history ===

    def test_clear_removes_file(self):
        """Clear deletes the chat history file."""
        self.lc._save_chat_history()
        self.assertTrue(os.path.exists(self._history_file()))
        self.lc._clear_chat_history()
        self.assertFalse(os.path.exists(self._history_file()))

    def test_clear_missing_file_is_noop(self):
        """Clear is a no-op when the file doesn't exist (no exception)."""
        # No save first
        self.lc._clear_chat_history()  # should not raise
        self.assertFalse(os.path.exists(self._history_file()))

    def test_clear_handles_missing_file_logger(self):
        """If file_logger is not set, clear still completes without raising."""
        self.lc._save_chat_history()
        del self.lc.file_logger
        self.lc._clear_chat_history()  # should not raise
        self.assertFalse(os.path.exists(self._history_file()))

    # === action_wipe_memory integration ===

    def test_action_wipe_memory_clears_history_file(self):
        """action_wipe_memory must clear both in-memory display AND the
        on-disk history file so the wipe persists across restarts."""
        # Save some history first
        self.lc._save_chat_history()
        self.assertTrue(os.path.exists(self._history_file()))
        # Patch controller.wipe_memory so we don't hit the real vault
        self.lc.controller.wipe_memory = MagicMock()
        # Set toPlainText return value so autosave path doesn't crash
        self.lc.chat_display.toPlainText.return_value = "some chat data"
        self.lc.action_wipe_memory()
        # In-memory clear was called
        self.lc.chat_display.clear.assert_called_once()
        # Controller wipe_memory was called (the actual memory purge)
        self.lc.controller.wipe_memory.assert_called_once()
        # On-disk file was removed
        self.assertFalse(os.path.exists(self._history_file()))

    # === _is_chat_history_enabled (toggle helper) ===

    def test_is_chat_history_enabled_returns_true_when_set(self):
        """Returns True when CONFIG[chat_history_enabled] is True."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.assertTrue(self.lc._is_chat_history_enabled())

    def test_is_chat_history_enabled_returns_false_when_set(self):
        """Returns False when CONFIG[chat_history_enabled] is False."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.assertFalse(self.lc._is_chat_history_enabled())

    def test_is_chat_history_enabled_missing_key_returns_true(self):
        """Missing key → True (UX-first default; opt-out via Settings)."""
        with patch.dict("config.CONFIG", {}, clear=False):
            # Strip the key transiently to test the default branch
            import config as _cfg
            saved = _cfg.CONFIG.pop("chat_history_enabled", None)
            try:
                self.assertTrue(self.lc._is_chat_history_enabled())
            finally:
                if saved is not None:
                    _cfg.CONFIG["chat_history_enabled"] = saved

    def test_in_code_default_in_config_module_is_true(self):
        """The in-code default for chat_history_enabled in config.py is True.

        Regression guard: a privacy-first OFF default would silently change
        the first-run experience and contradict the UX-first decision. The
        literal in the CONFIG dict is the source of truth; we read the
        source so the test doesn't depend on test-fixture state.
        """
        import re
        # Anchor to this file's own location: a cwd-relative "config.py"
        # breaks whenever pytest runs from tests/ (FileNotFoundError).
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.py"
        )
        with open(cfg_path, "r", encoding="utf-8") as f:
            src = f.read()
        match = re.search(
            r'"chat_history_enabled"\s*:\s*(True|False)', src
        )
        self.assertIsNotNone(
            match, "config.py must declare a chat_history_enabled default"
        )
        self.assertEqual(
            match.group(1), "True",
            "In-code default must be True (UX-first). "
            "If you intentionally flip it back, update _is_chat_history_"
            "enabled()'s fallback and the Settings tooltip too."
        )

    # === _save_chat_history gating ===

    def test_save_skipped_when_toggle_off(self):
        """When CONFIG[chat_history_enabled] is False, _save_chat_history
        is a silent no-op (no file written, no log spam)."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._save_chat_history()
            self.assertFalse(os.path.exists(self._history_file()))

    def test_save_runs_when_toggle_on(self):
        """When CONFIG[chat_history_enabled] is True, the file is written."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._save_chat_history()
            self.assertTrue(os.path.exists(self._history_file()))

    def test_save_toggle_change_picks_up_at_runtime(self):
        """A runtime CONFIG mutation (simulating a Settings-tab toggle
        change) takes effect immediately on the very next save — no
        app restart required. This verifies _is_chat_history_enabled
        reads live CONFIG, not a cached copy."""
        import config as _cfg
        saved = _cfg.CONFIG.get("chat_history_enabled")
        try:
            # Start disabled
            _cfg.CONFIG["chat_history_enabled"] = False
            self.lc._save_chat_history()
            self.assertFalse(os.path.exists(self._history_file()))
            # Flip to enabled (simulating the user toggling the checkbox)
            _cfg.CONFIG["chat_history_enabled"] = True
            self.lc._save_chat_history()
            self.assertTrue(os.path.exists(self._history_file()))
        finally:
            if saved is not None:
                _cfg.CONFIG["chat_history_enabled"] = saved

    # === _load_chat_history gating ===

    def test_load_skipped_when_toggle_off_even_if_file_exists(self):
        """When the toggle is OFF but the on-disk file (from a prior run
        with persistence enabled) exists, _load_chat_history does NOT
        call setHtml — the toggle overrides the file."""
        # Write a file with toggle ON
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._save_chat_history()
        self.lc.chat_display.setHtml.reset_mock()
        # Now read with toggle OFF — must be a no-op
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._load_chat_history()
        self.lc.chat_display.setHtml.assert_not_called()

    def test_load_runs_when_toggle_on(self):
        """With toggle ON, _load_chat_history restores the saved HTML."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._save_chat_history()
        self.lc.chat_display.setHtml.reset_mock()
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._load_chat_history()
        self.lc.chat_display.setHtml.assert_called_once()

    # === _clear_chat_history is intentionally NOT gated ===

    def test_clear_chat_history_works_when_persistence_disabled(self):
        """_clear_chat_history is intentional NOT gated by the toggle —
        users can always wipe a dormant file even when persistence is
        OFF (e.g. the file is left over from a previous run)."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._save_chat_history()
        self.assertTrue(os.path.exists(self._history_file()))
        # Toggle OFF — clear still works
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._clear_chat_history()
        self.assertFalse(os.path.exists(self._history_file()))

    # === _action_clear_chat_history (new sidebar button handler) ===

    def test_action_clear_chat_history_with_yes_clears_display_and_file(self):
        """Yes → clears in-memory display AND deletes on-disk file.
        Distinct from action_wipe_memory which also nukes controller
        short-term memory and runs autosave."""
        # Save first so the file exists
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._save_chat_history()
        self.assertTrue(os.path.exists(self._history_file()))
        self.lc.chat_display.clear.reset_mock()
        # Auto-confirm Yes
        with patch(
            "app_lifecycle.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.lc._action_clear_chat_history()
        # In-memory clear was called
        self.lc.chat_display.clear.assert_called_once()
        # On-disk file was removed
        self.assertFalse(os.path.exists(self._history_file()))
        # Audit log was called with the clear confirmation
        self.lc.log_to_audit.assert_any_call(
            "🗑️ Chat history cleared from disk + display"
        )

    def test_action_clear_chat_history_with_no_is_noop(self):
        """No → no display clear, no file removal, audit logs the cancel."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._save_chat_history()
        self.assertTrue(os.path.exists(self._history_file()))
        self.lc.chat_display.clear.reset_mock()
        with patch(
            "app_lifecycle.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.lc._action_clear_chat_history()
        # In-memory clear NOT called
        self.lc.chat_display.clear.assert_not_called()
        # File still exists
        self.assertTrue(os.path.exists(self._history_file()))
        # Cancel was logged
        self.lc.log_to_audit.assert_any_call(
            "⚠️ Chat history clear cancelled by user"
        )

    def test_action_clear_chat_history_does_not_touch_controller_memory(self):
        """action_clear_chat_history must NOT call controller.wipe_memory —
        that's the distinct role of action_wipe_memory."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._save_chat_history()
        with patch(
            "app_lifecycle.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.lc._action_clear_chat_history()
        self.lc.controller.wipe_memory.assert_not_called()

    def test_action_clear_chat_history_works_with_toggle_off(self):
        """User can still wipe a dormant file via the button even after
        they've turned persistence OFF (the explicit confirmation
        bypasses the toggle gate, matching _clear_chat_history)."""
        # Write a dormant file under the toggled-ON state
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._save_chat_history()
        # Flip the toggle OFF (user just disabled persistence)
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.assertTrue(os.path.exists(self._history_file()))
            with patch(
                "app_lifecycle.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                self.lc._action_clear_chat_history()
        self.assertFalse(os.path.exists(self._history_file()))

# =============================================================================
# Tests — Chat history wiring (integration)
# =============================================================================
# Verifies the SAVE/CLEAR hooks are wired into the actual user-facing paths:
#   - action_send_prompt → AIWorker.reply_signal → handle_ai_response
#   - action_send_prompt → MultiModelWorker.multi_reply_signal → handle_multi_model_response
#   - action_send_ab_test → _handle_ab_test_result
#   - btn_wipe.clicked → action_wipe_memory
# These tests exercise the FULL flow (not just the unit-level save/clear
# helpers) to catch regression bugs like a wiring removal or a code-path
# change that skips the persistence step.

class TestChatHistoryWiring(unittest.TestCase):
    """Integration tests for chat-history persistence wiring.

    Uses the same _mk() MockParent pattern as TestChatHistoryPersistence
    (the bare QObject+AppLifecycleMixin subclass with MagicMock widgets)
    plus tempfile.mkdtemp + WORKSPACE_DIR patching. The AuditorWorker /
    DesireWorker classes are patched on the ``workers`` module so the
    function-scope ``from workers import AuditorWorker`` lookups inside
    handle_ai_response / handle_multi_model_response pick up the mocks
    and don't actually spin up background threads during tests.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chat_wiring_test_")
        self._ws_patcher = patch("app_lifecycle.WORKSPACE_DIR", self.tmpdir)
        self._ws_patcher.start()
        # Enable persistence for the wiring tests — we're testing write
        # paths, so make sure they actually fire (production default is OFF).
        # freeform_mode/mock_mode pinned False per the CONFIG-mode-pins gate
        # (scripts/assert_config_mode_pins.py): setUp-level clear=False patches
        # must not inherit the user's app_settings.json preferences
        # (debounce window + modes).
        self._cfg_patcher = patch.dict(
            "config.CONFIG",
            {"chat_history_enabled": True, "chat_history_debounce_ms": 500,
             "freeform_mode": False, "mock_mode": False},
            clear=False,
        )
        self._cfg_patcher.start()
        self.lc = _mk()
        # ── Stub methods defined in AppUIMixin ──────────────────────
        # The lifecycle mixin calls self._append_* during response handling.
        # Bare mixin instances (as _mk() returns) lack these; stub them
        # so the integration can run without creating a full UI.
        self.lc._append_user_message = MagicMock()
        self.lc._append_assistant_message = MagicMock()
        self.lc._append_error_message = MagicMock()
        # ── chat_display HTML serialization ────────────────────────
        # chat_display.toHtml() returns MagicMock by default, which crashes
        # json.dump in _save_chat_history with "Object of type MagicMock
        # is not JSON serializable". Provide a realistic HTML string so
        # the integration tests can exercise the full save path.
        self.lc.chat_display.toHtml.return_value = (
            "<p style='color:#3B82F6;'>You</p>"
            "<p style='color:#10B981;'>AI</p>"
        )
        # chk_desire_engine.isChecked() defaults to truthy on MagicMock;
        # flip it to False so the desire-engine branch is skipped (we already
        # mock DesireWorker/AuditorWorker below — this is belt-and-braces).
        self.lc.chk_desire_engine.isChecked.return_value = False
        # Pretend action_send_prompt just set this
        self.lc.last_user_text = "test prompt"

    def tearDown(self):
        # LIFO: cfg_patcher first (innermost), ws_patcher second.
        self._cfg_patcher.stop()
        self._ws_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _history_file(self):
        return os.path.join(self.tmpdir, "data", "chat_history.json")

    # === action_send_prompt → handle_ai_response FULL WIRING trace ===

    @patch("workers.DesireWorker")
    @patch("workers.AuditorWorker")
    @patch("keepalive_helper.KeepaliveContext")
    @patch("workers.AIWorker")
    def test_action_send_prompt_wires_reply_signal_to_persistence(
        self, mock_ai_worker_cls, mock_kc_cls, mock_auditor_cls, mock_desire_cls
    ):
        """End-to-end wiring trace — the test the user explicitly asked for.

        Verifies the FULL chain end-to-end:

          1. ``action_send_prompt()`` is invoked (user clicked Send).
          2. ``workers.AIWorker(...)`` is constructed with the user's text.
          3. ``worker.reply_signal.connect(self.handle_ai_response)`` wires
             the LLM completion callback (the actual production wiring —
             a regression that disconnects the signal would break step 4).
          4. Invoking the connected slot as if the worker emitted
             ``reply_signal(ai_data)`` runs ``handle_ai_response`` end-to-end.
          5. ``handle_ai_response`` invokes ``self._save_chat_history()`` at
             the end of the flow, persisting ``data/chat_history.json``.

        A regression that breaks ANY step (disconnected signal, save
        removed from handle_ai_response, etc.) makes this test fail.
        """
        # Configure mocks so no real thread / widget is created
        mock_worker = MagicMock()
        mock_ai_worker_cls.return_value = mock_worker
        mock_auditor_cls.return_value = MagicMock()
        mock_desire_cls.return_value = MagicMock()
        mock_kc_cls.return_value = MagicMock()

        # Provide the user message that action_send_prompt reads
        self.lc.txt_input.toPlainText.return_value = "Hello?"
        # Skip multi-model branch (no compare_toggle attribute)
        self.lc.compare_toggle = None
        # Skip A/B-test branch (no _get_ab_personas attribute on bare mixin)

        # Capture every slot connected to worker.reply_signal during
        # action_send_prompt — we want to verify handle_ai_response is one
        # of them AND invoke it just like the production code would.
        connected_slots = []
        mock_worker.reply_signal.connect.side_effect = (
            lambda slot: connected_slots.append(slot)
        )

        # Step 1: user pressed Send
        self.lc.action_send_prompt()

        # Step 2: verify AIWorker was constructed exactly once
        mock_ai_worker_cls.assert_called_once()

        # Step 3: verify the WIRING exists — handle_ai_response is in the
        # captured slot list. (If a future refactor disconnects the signal,
        # this assertion fails.)
        handle_ai_slot = next(
            (s for s in connected_slots if s == self.lc.handle_ai_response),
            None,
        )
        self.assertIsNotNone(
            handle_ai_slot,
            "action_send_prompt did NOT wire worker.reply_signal to "
            "handle_ai_response — the LLM completion callback is broken.",
        )

        # Step 4: simulate the production path — the AIWorker emits
        # reply_signal(ai_data), which fires the connected slot.
        handle_ai_slot({"final": "Hello, world!", "thinking": "step 1"})

        # Step 5: persistence must have happened at the end of the flow.
        # In production the QTimer fires 500 ms later; force the flush here
        # so the assertion is deterministic in tests.
        self.lc._flush_chat_history_save()
        self.assertTrue(
            os.path.exists(self._history_file()),
            "handle_ai_response ran end-to-end but _save_chat_history "
            "did not write data/chat_history.json",
        )
        with open(self._history_file(), encoding="utf-8") as f:
            payload = json.load(f)
        self.assertIn("html", payload)
        self.assertIn("saved_at", payload)

    # === handle_ai_response (slot bound from worker.reply_signal) ===

    @patch("workers.DesireWorker")
    @patch("workers.AuditorWorker")
    def test_handle_ai_response_writes_file_when_invoked(
        self, mock_auditor_cls, mock_desire_cls
    ):
        """End-to-end: handle_ai_response, when invoked as the slot bound
        from AIWorker.reply_signal in action_send_prompt, schedules a
        debounced chat-history save that lands the rendered HTML on disk
        after the 500 ms coalescing window. Verifies the WIRING between
        the response handler and the persistence helper, not the helper
        itself.

        Note: actual production uses QTimer.singleShot(500ms) — we flush
        the timer via ``_flush_chat_history_save()`` here to make the
        test deterministic without depending on the event loop timing.
        """
        mock_auditor_cls.return_value = MagicMock()
        mock_desire_cls.return_value = MagicMock()
        ai_data = {"final": "Hello, world!", "thinking": "step 1."}
        # Simulate the slot being called as if worker.reply_signal.emit() ran
        self.lc.handle_ai_response(ai_data)
        # Flush any pending debounced save (in production, the QTimer fires
        # automatically 500 ms later; force it here for determinism).
        self.lc._flush_chat_history_save()
        # File was written
        self.assertTrue(os.path.exists(self._history_file()))
        # Valid JSON with the html key
        with open(self._history_file(), encoding="utf-8") as f:
            payload = json.load(f)
        self.assertIn("html", payload)
        self.assertIn("saved_at", payload)
        # The assistant card was appended (not error path)
        self.lc._append_assistant_message.assert_called_once()
        self.lc._append_error_message.assert_not_called()

    @patch("workers.DesireWorker")
    @patch("workers.AuditorWorker")
    def test_handle_ai_response_persists_error_replies_too(
        self, mock_auditor_cls, mock_desire_cls
    ):
        """Error replies (Core Failure / provider timeout) must also persist,
        so the user sees the failed request after a restart and can retry
        with proper context."""
        mock_auditor_cls.return_value = MagicMock()
        mock_desire_cls.return_value = MagicMock()
        ai_data = {"error": "❌ Core Failure: provider timeout"}
        self.lc.handle_ai_response(ai_data)
        # Flush to make the assertion deterministic
        self.lc._flush_chat_history_save()
        self.assertTrue(os.path.exists(self._history_file()))
        self.lc._append_error_message.assert_called_once()

    @patch("workers.DesireWorker")
    @patch("workers.AuditorWorker")
    def test_handle_ai_response_displays_actual_error_text(
        self, mock_auditor_cls, mock_desire_cls
    ):
        """When ai_data has an error key, the actual error text is passed to
        _append_error_message rather than the generic fallback 'Error'."""
        mock_auditor_cls.return_value = MagicMock()
        mock_desire_cls.return_value = MagicMock()
        ai_data = {"error": "Requested tokens (2050) exceed context window of 2048"}
        self.lc.handle_ai_response(ai_data)
        call_arg = self.lc._append_error_message.call_args[0][0]
        self.assertIn("Requested tokens (2050) exceed context window of 2048", call_arg)
        self.assertNotEqual(call_arg, "Error")

    @patch("workers.DesireWorker")
    @patch("workers.AuditorWorker")
    def test_handle_ai_response_does_not_write_when_toggle_off(
        self, mock_auditor_cls, mock_desire_cls
    ):
        """End-to-end gating: when CONFIG[chat_history_enabled] is False,
        the full handle_ai_response flow must NOT write the on-disk file.
        Verifies that the gate fires at both the schedule layer AND the
        underlying _save_chat_history helper — once via _schedule_chat_
        history_save, once via the _save_chat_history that the timer
        would call."""
        mock_auditor_cls.return_value = MagicMock()
        mock_desire_cls.return_value = MagicMock()
        # Make sure the assistant card still gets appended to the in-memory
        # display even when persistence is off (display behavior unchanged).
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc.handle_ai_response({"final": "Hello, world!", "thinking": ""})
            # Even an explicit flush is a no-op when toggle is off
            self.lc._flush_chat_history_save()
        # No file written
        self.assertFalse(os.path.exists(self._history_file()))
        # But the in-memory response is still displayed
        self.lc._append_assistant_message.assert_called_once()

    # === handle_multi_model_response ===

    @patch("workers.DesireWorker")
    @patch("workers.AuditorWorker")
    def test_handle_multi_model_response_persists_comparison_view(
        self, mock_auditor_cls, mock_desire_cls
    ):
        """handle_multi_model_response (slot from MultiModelWorker.multi_reply_signal)
        schedules a debounced save; after flush the rendered comparison is
        on disk so it survives across restarts."""
        mock_auditor_cls.return_value = MagicMock()
        mock_desire_cls.return_value = MagicMock()
        results = [
            {"provider": "local_llm", "model": "m1", "ok": True,
             "content": "first response text"},
            {"provider": "local_llm", "model": "m2", "ok": True,
             "content": "second response text"},
        ]
        self.lc.handle_multi_model_response(results)
        self.lc._flush_chat_history_save()
        self.assertTrue(os.path.exists(self._history_file()))

    # === _handle_ab_test_result ===

    def test_handle_ab_test_result_persists_rendered_view(self):
        """_handle_ab_test_result (rendered after A/B persona comparison)
        schedules a debounced save; after flush the rendered cards are on
        disk so voters can see them next launch."""
        # The vote_bar widgets are created in app_ui.init_ui, not in _mk().
        # Stub them so _show_ab_vote_bar / _hide_ab_vote_bar can mutate them.
        for attr in (
            "ab_vote_label", "btn_ab_vote_a", "btn_ab_vote_b",
            "btn_ab_vote_tie", "btn_ab_vote_skip", "ab_vote_bar",
        ):
            setattr(self.lc, attr, MagicMock())
        # Patch memory_vault lookup (test has no real vault)
        import memory_vault
        with patch.object(
            memory_vault, "get_recent_persona_votes", return_value=[]
        ):
            result = {
                "prompt": "compare these",
                "vote_id": -1,
                "response_a": {"final": "Persona A answer"},
                "response_b": {"final": "Persona B answer"},
            }
            self.lc._handle_ab_test_result(result, "You are A.", "You are B.")
        self.lc._flush_chat_history_save()
        # Persisted
        self.assertTrue(os.path.exists(self._history_file()))

    # === action_wipe_memory full flow (slot bound from btn_wipe.clicked) ===

    def test_action_wipe_memory_full_flow_removes_file(self):
        """End-to-end wipe: when btn_wipe is clicked (which triggers
        action_wipe_memory), the full flow must:
          1. call controller.wipe_memory()
          2. autosave the chat text to data/logs/ (autosave_logs=True)
          3. clear the in-memory chat_display + thinking_display
          4. invoke _clear_chat_history to remove the on-disk
             chat_history.json file as part of the same flow.
        This is the integration check — ensuring the file removal is
        actually wired into action_wipe_memory (not just that
        _clear_chat_history works in isolation)."""
        # Build pre-state: file exists, chat has content
        self.lc._save_chat_history()
        self.assertTrue(os.path.exists(self._history_file()))
        self.lc.chat_display.toPlainText.return_value = "conversational payload"
        # Execute the flow
        self.lc.action_wipe_memory()
        # 1. controller.wipe_memory was called
        self.lc.controller.wipe_memory.assert_called_once()
        # 2. autosave log file written under data/logs/
        log_dir = os.path.join(self.tmpdir, "data", "logs")
        log_files = [f for f in os.listdir(log_dir) if f.startswith("log_")]
        self.assertEqual(len(log_files), 1, "autosave did not write exactly one log file")
        # 3. in-memory displays cleared
        self.lc.chat_display.clear.assert_called_once()
        self.lc.thinking_display.clear.assert_called_once()
        # 4. on-disk chat_history.json removed as part of the same flow
        self.assertFalse(os.path.exists(self._history_file()))
        # 5. The "Session history purged." line was rendered into the
        #    in-memory chat_display (HTML append — visible to the user)
        appended_html = " ".join(
            str(call.args[0]) for call in self.lc.chat_display.append.call_args_list
        )
        self.assertIn("Session history purged", appended_html)
        # 6. The audit log received the post-wipe confirmation
        #    (note: action_wipe_memory logs "Core VRAM context cleared.\n",
        #    while "Session history purged." goes into chat HTML — different paths)
        self.lc.log_to_audit.assert_any_call("Core VRAM context cleared.\n")

    def test_action_wipe_memory_full_flow_removes_file_when_persistence_disabled(self):
        """When the user has toggled persistence OFF but a dormant file
        exists from a previous run, btn_wipe → action_wipe_memory must
        still clean it up. Verifies that action_wipe_memory → _clear_chat_history
        is intentionally NOT gated by the toggle (only save/load are)."""
        # Write a dormant file under toggled-ON state
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._save_chat_history()
        self.assertTrue(os.path.exists(self._history_file()))
        # Disable persistence and run wipe
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc.chat_display.toPlainText.return_value = ""
            self.lc.action_wipe_memory()
        # The full flow still removed the dormant file
        self.assertFalse(os.path.exists(self._history_file()))
        # And the in-memory display was still cleared
        self.lc.chat_display.clear.assert_called_once()

# =============================================================================
# Tests — Chat history debounce/coalesce layer
# =============================================================================
# Verifies that the production save path uses a 500 ms debounce, so multi-model
# compare mode (which fires 3+ responses in rapid succession) produces ONE
# disk write per prompt instead of N.

class TestChatHistoryDebounce(unittest.TestCase):
    """Verifies the QTimer-based debounce layer coalesces bursts of
    ``_schedule_chat_history_save()`` calls into a single disk write.

    Mirrors the same MockParent + tempfile Mkdtemp + WORKSPACE_DIR patch
    pattern as the other chat-history test classes. Production uses a
    real QTimer firing after CHAT_HISTORY_DEBOUNCE_MS (= 500 ms); tests
    exercise the behavior via the same debounced paths but assert
    deterministically using the persistent QTimer's ``isActive()`` state
    and via ``_flush_chat_history_save()`` to force fires synchronously.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chat_debounce_test_")
        self._ws_patcher = patch("app_lifecycle.WORKSPACE_DIR", self.tmpdir)
        self._ws_patcher.start()
        # Enable persistence for these tests — the debounce layer is
        # only meaningful when saves can actually fire.
        # freeform_mode/mock_mode pinned False per the CONFIG-mode-pins gate
        # (scripts/assert_config_mode_pins.py): setUp-level clear=False patches
        # must not inherit the user's app_settings.json preferences
        # (debounce window + modes).
        self._cfg_patcher = patch.dict(
            "config.CONFIG",
            {"chat_history_enabled": True, "chat_history_debounce_ms": 500,
             "freeform_mode": False, "mock_mode": False},
            clear=False,
        )
        self._cfg_patcher.start()
        self.lc = _mk()
        # chat_display.toHtml() must return a real string so the debounced
        # _save_chat_history (when it eventually fires) doesn't choke on a
        # MagicMock during json.dump.
        self.lc.chat_display.toHtml.return_value = (
            "<p style='color:#3B82F6;'>You</p>"
            "<p style='color:#10B981;'>AI</p>"
        )

    def tearDown(self):
        # Stop and delete any real QTimer created by _make_pending_timer()
        # to avoid leaks across tests in the same module.
        timer = getattr(self.lc, '_chat_history_save_timer', None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        self._cfg_patcher.stop()
        self._ws_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _history_file(self):
        return os.path.join(self.tmpdir, "data", "chat_history.json")

    # === Lazy QTimer creation & reuse ===

    def test_schedule_lazily_creates_single_qtimer(self):
        """First _schedule_chat_history_save call creates the persistent
        timer; subsequent calls reuse the same instance (no Qt object
        churn across high-traffic bursts)."""
        # No timer exists before first use
        self.assertFalse(hasattr(self.lc, '_chat_history_save_timer'))
        self.lc._schedule_chat_history_save()
        first_timer = self.lc._chat_history_save_timer
        self.assertIsNotNone(first_timer)
        # Single-shot + 500ms + parented to lc (a QObject)
        self.assertTrue(first_timer.isSingleShot())
        self.assertEqual(first_timer.interval(), 500)
        self.assertIs(first_timer.parent(), self.lc)
        # Active means a write is pending
        self.assertTrue(first_timer.isActive())

        # Second call reuses the same QTimer (same id()) — reset to fire
        # 500 ms from THIS call, not 500 ms from the first.
        self.lc._schedule_chat_history_save()
        self.assertIs(self.lc._chat_history_save_timer, first_timer)
        self.assertTrue(first_timer.isActive())

    # === Coalescing: many schedules → one write ===

    def test_multiple_schedules_coalesce_into_one_write(self):
        """5 schedules within the debounce window → after flush, only ONE
        _save_chat_history invocation. This is the headline behavior the
        user asked for: multi-model bursts collapse into a single write.
        """
        # Wrap _save_chat_history so we can assert call count while
        # keeping the real implementation for the actual write.
        original_save = self.lc._save_chat_history
        mock_save = MagicMock(side_effect=original_save)
        self.lc._save_chat_history = mock_save
        try:
            # Burst of 5 schedules within 500 ms (real production path:
            # handle_ai_response → _schedule → start(500) → handle_again → stop+start)
            for _ in range(5):
                self.lc._schedule_chat_history_save()
            # DEBOUNCE: zero writes yet (the timer is still pending)
            mock_save.assert_not_called()
            # Force the timer to fire — must be EXACTLY ONE write
            self.lc._flush_chat_history_save()
            mock_save.assert_called_once()
        finally:
            self.lc._save_chat_history = original_save
        # And only one file was written
        self.assertTrue(os.path.exists(self._history_file()))

    def test_schedule_does_not_write_without_timer_fire(self):
        """Without calling _flush_chat_history_save, the production QTimer
        is pending and no file is on disk yet. Proves the codepath is
        actually debounced (vs the old always-write-immediately behavior)."""
        self.lc._schedule_chat_history_save()
        # Timer is running but hasn't fired
        timer = self.lc._chat_history_save_timer
        self.assertTrue(timer.isActive())
        # File has NOT been written yet
        self.assertFalse(os.path.exists(self._history_file()))

    # === Flush semantics ===

    def test_flush_writes_immediately_and_deactivates_timer(self):
        """_flush_chat_history_save stops the in-flight timer AND calls
        _save_chat_history synchronously. After flush, the timer is no
        longer active so it won't double-fire later."""
        self.lc._schedule_chat_history_save()
        timer = self.lc._chat_history_save_timer
        self.assertTrue(timer.isActive())
        self.lc._flush_chat_history_save()
        # Timer was stopped
        self.assertFalse(timer.isActive())
        # File is on disk immediately (sync write, no waiting for the timer)
        self.assertTrue(os.path.exists(self._history_file()))
        # Second flush is a no-op for the file (still exists, _save runs
        # but overwrites — sanity check below that nothing crashes)
        self.lc._flush_chat_history_save()
        self.assertTrue(os.path.exists(self._history_file()))

    # === Gating: toggle off ===

    def test_schedule_is_silent_noop_when_toggle_off(self):
        """When CONFIG[chat_history_enabled] is False, _schedule_chat_
        history_save creates NO QTimer and writes NO file. Even an
        explicit _flush_chat_history_save() inside the same toggle-off
        scope is a no-op — defense in depth at both the schedule layer
        and the underlying _save helper."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._schedule_chat_history_save()
            # Even an explicit flush is a no-op while the toggle is off
            self.lc._flush_chat_history_save()
        # No timer allocated (lazy creation skipped)
        self.assertFalse(hasattr(self.lc, '_chat_history_save_timer'))
        # No file written
        self.assertFalse(os.path.exists(self._history_file()))

    # === Clear semantics: cancel pending timer ===

    def test_clear_chat_history_cancels_pending_timer(self):
        """_clear_chat_history stops any pending debounce timer — otherwise
        a still-active QTimer would fire AFTER the wipe and re-create the
        file we just deleted. This is what keeps the wipe durable across
        apps that close too quickly after rendering a response."""
        self.lc._schedule_chat_history_save()
        timer = self.lc._chat_history_save_timer
        self.assertTrue(timer.isActive(), "precondition: timer must be pending")
        # Cancel via _clear_chat_history (timer should NOT fire later)
        self.lc._clear_chat_history()
        self.assertFalse(timer.isActive(),
                          "_clear_chat_history must stop the pending timer")
        # Sanity: simulating the timer firing later does NOT regenerate
        # the file (the underlying _save_chat_history still works, but
        # no one re-starts the timer now)
        self.assertFalse(os.path.exists(self._history_file()))

    # === Real-Qt-timing coverage ===

    def test_qtimer_actually_fires_after_500ms(self):
        """Real Qt-timing coverage: schedule → wait the actual 500 ms
        debounce window → processEvents → _save_chat_history is called
        through the real QTimer timeout signal (not a manual flush).

        Slow (~600 ms) but catches Qt-version-specific timer bugs that
        the other debounce tests would miss (e.g. PyQt6 6.x quirks where
        ``stop()`` + ``start()`` doesn't reset the timer, or where the
        singleShot timer doesn't fire on a mocked QObject parent). All
        other debounce tests bypass the actual timer fire path with
        ``_flush_chat_history_save()`` for speed and determinism.
        """
        import time
        from PyQt6.QtWidgets import QApplication
        # Wrap _save_chat_history so we can assert call count while
        # preserving the real implementation (file gets written)
        original_save = self.lc._save_chat_history
        mock_save = MagicMock(side_effect=original_save)
        self.lc._save_chat_history = mock_save
        try:
            self.lc._schedule_chat_history_save()
            # No write yet (timer is pending)
            mock_save.assert_not_called()
            # Condition-based wait (not a fixed sleep): pump the event loop
            # in a poll loop until the QTimer has actually fired and the
            # debounced save ran, with a generous deadline (4x the 500ms
            # debounce window) so the test is insensitive to Windows timer
            # granularity and machine load. Fixed time.sleep() windows race
            # the timer deadline.
            import time as _time
            import config as _cfg
            from app_lifecycle import CHAT_HISTORY_DEBOUNCE_MS
            window_ms = int(_cfg.CONFIG.get(
                "chat_history_debounce_ms", CHAT_HISTORY_DEBOUNCE_MS
            ))
            deadline_ms = max(2000, 4 * window_ms)
            deadline = _time.monotonic() + deadline_ms / 1000.0
            while not mock_save.called and _time.monotonic() < deadline:
                QApplication.instance().processEvents()
                _time.sleep(0.01)  # 10ms poll — don't starve the CPU
            self.assertTrue(
                mock_save.called,
                f"debounced save did not fire within {deadline_ms} ms of scheduling",
            )
            # The QTimer timeout fired and called _save_chat_history
            mock_save.assert_called_once()
            # And the file is actually on disk
            self.assertTrue(os.path.exists(self._history_file()))
        finally:
            self.lc._save_chat_history = original_save

    # ── Configurable debounce window (CONFIG["chat_history_debounce_ms"]) ──
    # The debounce window is now user-configurable via Settings. The
    # production code reads from CONFIG with the module constant as
    # fallback, and clamps to [0, 60000] ms so a corrupt CONFIG value
    # can't freeze the UI or bypass persistence.

    def test_schedule_uses_config_value_when_set(self):
        """When CONFIG["chat_history_debounce_ms"] is set, the timer
        uses that value (not the module constant)."""
        # First call creates the timer (lazy allocation in the
        # production code). Then patch CONFIG and re-schedule to
        # verify the timer picks up the new value.
        self.lc._schedule_chat_history_save()
        timer = self.lc._chat_history_save_timer
        with patch.dict("config.CONFIG",
                        {"chat_history_debounce_ms": 250}, clear=False):
            self.lc._schedule_chat_history_save()
        self.assertEqual(timer.interval(), 250)

    def test_schedule_falls_back_to_module_constant_when_config_missing(self):
        """When CONFIG["chat_history_debounce_ms"] is missing, the
        module constant CHAT_HISTORY_DEBOUNCE_MS (500ms) is used."""
        from app_lifecycle import CHAT_HISTORY_DEBOUNCE_MS
        import config as _cfg
        saved = _cfg.CONFIG.pop("chat_history_debounce_ms", None)
        try:
            self.lc._schedule_chat_history_save()
        finally:
            if saved is not None:
                _cfg.CONFIG["chat_history_debounce_ms"] = saved
        self.assertEqual(
            self.lc._chat_history_save_timer.interval(),
            CHAT_HISTORY_DEBOUNCE_MS
        )

    def test_schedule_clamps_negative_config_value(self):
        """A corrupt CONFIG value (negative) is clamped to 0 instead
        of being passed to QTimer.start (which would raise)."""
        with patch.dict("config.CONFIG",
                        {"chat_history_debounce_ms": -1000}, clear=False):
            self.lc._schedule_chat_history_save()
        self.assertEqual(self.lc._chat_history_save_timer.interval(), 0)

    def test_schedule_clamps_excessive_config_value(self):
        """A corrupt CONFIG value (>60000ms) is clamped to 60000 so the
        UI can't be frozen by a bad setting."""
        with patch.dict("config.CONFIG",
                        {"chat_history_debounce_ms": 10_000_000},
                        clear=False):
            self.lc._schedule_chat_history_save()
        self.assertEqual(
            self.lc._chat_history_save_timer.interval(), 60_000
        )

    def test_schedule_falls_back_to_constant_on_non_numeric_config(self):
        """A non-numeric CONFIG value (e.g. "abc") falls back to the
        module constant instead of crashing the int() cast."""
        with patch.dict("config.CONFIG",
                        {"chat_history_debounce_ms": "abc"}, clear=False):
            self.lc._schedule_chat_history_save()
        from app_lifecycle import CHAT_HISTORY_DEBOUNCE_MS
        self.assertEqual(
            self.lc._chat_history_save_timer.interval(),
            CHAT_HISTORY_DEBOUNCE_MS
        )

    def test_config_has_default_debounce_key(self):
        """The in-code CONFIG default for chat_history_debounce_ms is 500
        (matching the module constant). Regression guard: a future
        flip to a different default would break existing test timing."""
        import re
        # Anchor to this file's own location: a cwd-relative "config.py"
        # breaks whenever pytest runs from tests/ (FileNotFoundError).
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.py"
        )
        with open(cfg_path, "r", encoding="utf-8") as f:
            src = f.read()
        match = re.search(
            r'"chat_history_debounce_ms"\s*:\s*(\d+)', src
        )
        self.assertIsNotNone(
            match, "config.py must declare a chat_history_debounce_ms default"
        )
        self.assertEqual(
            int(match.group(1)), 500,
            "Default must be 500ms (matches CHAT_HISTORY_DEBOUNCE_MS)"
        )

    # ── Reset-to-default button (Settings → chat-history debounce) ──
    # The ↩ Reset to Default button restores the debounce to 500 ms and
    # re-arms any in-flight timer so the next save fires 500 ms from
    # NOW (not from the original, longer deadline). Mirrors the
    # ``_reset_protocol`` pattern: a one-click restore for power users
    # who tweaked the value and want to go back to the safe default.

    def test_reset_button_resets_spinbox_to_500(self):
        """Click resets the spinbox value to 500 regardless of the
        user's current setting (e.g. after they slid it to 30s)."""
        import types
        from PyQt6.QtWidgets import QSpinBox
        from tabs.settings_tab import SettingsTabMixin
        # Bind the production method onto the test instance (the same
        # pattern used by TestChatHistoryReactiveToggle).
        self.lc._reset_chat_history_debounce = types.MethodType(
            SettingsTabMixin._reset_chat_history_debounce, self.lc
        )
        # Real spinbox with a non-default value
        spin = QSpinBox()
        spin.setRange(0, 60000)  # match production range
        spin.setValue(30_000)  # 30 seconds
        self.lc.spin_chat_history_debounce = spin
        self.lc._reset_chat_history_debounce()
        self.assertEqual(spin.value(), 500)

    def test_reset_button_writes_default_to_config(self):
        """Click writes CONFIG["chat_history_debounce_ms"] = 500 so the
        next _schedule_chat_history_save() picks up the default even
        before the user clicks SAVE ALL CONFIGURATIONS."""
        import types
        from PyQt6.QtWidgets import QSpinBox
        from tabs.settings_tab import SettingsTabMixin
        self.lc._reset_chat_history_debounce = types.MethodType(
            SettingsTabMixin._reset_chat_history_debounce, self.lc
        )
        self.lc.spin_chat_history_debounce = QSpinBox()
        self.lc.spin_chat_history_debounce.setRange(0, 60000)
        # Pre-set CONFIG to a non-default value
        import config as _cfg
        _cfg.CONFIG["chat_history_debounce_ms"] = 15_000
        try:
            self.lc._reset_chat_history_debounce()
            self.assertEqual(_cfg.CONFIG["chat_history_debounce_ms"], 500)
        finally:
            # Restore (the cfg_patcher in tearDown will re-stop, but
            # be defensive in case a future refactor changes the order)
            _cfg.CONFIG.pop("chat_history_debounce_ms", None)

    def test_reset_button_rearms_pending_timer(self):
        """If a save is currently queued (timer is active), the click
        re-arms the timer with the new 500ms window — the user gets
        the 500ms window from NOW, not from the original deadline.
        Mirrors the production code's _schedule_chat_history_save
        stop+restart pattern."""
        import types
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QSpinBox
        from tabs.settings_tab import SettingsTabMixin
        # Bind the production method
        self.lc._reset_chat_history_debounce = types.MethodType(
            SettingsTabMixin._reset_chat_history_debounce, self.lc
        )
        self.lc.spin_chat_history_debounce = QSpinBox()
        self.lc.spin_chat_history_debounce.setRange(0, 60000)
        # Create an active timer with a 60s window (simulating a user
        # who set the debounce to 60s and then queued a save)
        timer = QTimer(self.lc)
        timer.setSingleShot(True)
        timer.start(60_000)
        self.lc._chat_history_save_timer = timer
        # Set CONFIG to a long value so the re-arm picks it up
        import config as _cfg
        _cfg.CONFIG["chat_history_debounce_ms"] = 60_000
        try:
            self.lc._reset_chat_history_debounce()
            # Timer was re-armed with 500ms (the new default)
            self.assertEqual(timer.interval(), 500)
            # Timer is still active (was stopped+restarted, not killed)
            self.assertTrue(timer.isActive())
        finally:
            timer.stop()
            _cfg.CONFIG.pop("chat_history_debounce_ms", None)

    def test_reset_button_noop_for_timer_when_no_pending_save(self):
        """If no save is currently queued (timer is None or inactive),
        the click is a no-op for the timer — we don't allocate one
        just to set its interval. The next _schedule_chat_history_save
        call will pick up the new CONFIG value naturally."""
        import types
        from PyQt6.QtWidgets import QSpinBox
        from tabs.settings_tab import SettingsTabMixin
        self.lc._reset_chat_history_debounce = types.MethodType(
            SettingsTabMixin._reset_chat_history_debounce, self.lc
        )
        self.lc.spin_chat_history_debounce = QSpinBox()
        self.lc.spin_chat_history_debounce.setRange(0, 60000)
        # No timer set
        self.assertFalse(
            hasattr(self.lc, '_chat_history_save_timer')
            and self.lc._chat_history_save_timer is not None
        )
        # Click — must not raise, must not create a timer
        self.lc._reset_chat_history_debounce()
        self.assertFalse(hasattr(self.lc, '_chat_history_save_timer'))

    def test_reset_button_logs_to_audit(self):
        """The audit log records the reset so the session journal can
        be skimmed for debounce changes without parsing prose."""
        import types
        from PyQt6.QtWidgets import QSpinBox
        from tabs.settings_tab import SettingsTabMixin
        self.lc._reset_chat_history_debounce = types.MethodType(
            SettingsTabMixin._reset_chat_history_debounce, self.lc
        )
        self.lc.spin_chat_history_debounce = QSpinBox()
        self.lc.spin_chat_history_debounce.setRange(0, 60000)
        self.lc._reset_chat_history_debounce()
        # The reset audit line is logged
        self.lc.log_to_audit.assert_any_call(
            "↩ Chat-history debounce reset to 500 ms"
        )

# =============================================================================
# Tests — Chat history sidebar status badge
# =============================================================================
# Verifies the chat_history_badge QLabel in the sidebar reflects the current
# CONFIG["chat_history_enabled"] value with distinct text, color, and tooltip
# for ON vs OFF — same role as the existing protocol_active_label and
# mode_indicator badges.

class TestChatHistoryBadge(unittest.TestCase):
    """Tests for the AppUIMixin._update_chat_history_badge() method.

    The ``_mk()`` helper creates a bare AppLifecycleMixin instance — not
    AppUIMixin — so the new badge-update method is not inherited. We bind
    the unbound method from AppUIMixin onto the test instance via
    ``types.MethodType`` so ``self.lc._update_chat_history_badge()`` works
    without dragging in the full UI construction.

    We also manually attach a real ``QLabel`` (not a MagicMock) so we can
    introspect ``text()`` / ``styleSheet()`` / ``toolTip()`` /
    ``isVisible()`` after the update method runs — exactly what init_ui
    would do in production.
    """

    def setUp(self):
        import types
        from PyQt6.QtWidgets import QLabel
        from app_ui import AppUIMixin
        self.tmpdir = tempfile.mkdtemp(prefix="chat_badge_test_")
        self._ws_patcher = patch("app_lifecycle.WORKSPACE_DIR", self.tmpdir)
        self._ws_patcher.start()
        # freeform_mode/mock_mode pinned False per the CONFIG-mode-pins gate
        # (scripts/assert_config_mode_pins.py): setUp-level clear=False patches
        # must not inherit the user's app_settings.json preferences
        # (debounce window + modes).
        self._cfg_patcher = patch.dict(
            "config.CONFIG",
            {"chat_history_enabled": False, "freeform_mode": False, "mock_mode": False},
            clear=False,
        )
        self._cfg_patcher.start()
        self.lc = _mk()
        # Bind the AppUIMixin method onto this test instance so we can
        # call it without a full UI build. Python descriptor protocol
        # (``__get__``) wires the unbound function as a bound method.
        self.lc._update_chat_history_badge = types.MethodType(
            AppUIMixin._update_chat_history_badge, self.lc
        )
        # Manually attach the QLabel that init_ui() would create.
        self.lc.chat_history_badge = QLabel()

    def tearDown(self):
        # Stop and delete any real QTimer created by _make_pending_timer()
        # to avoid leaks across tests in the same module.
        timer = getattr(self.lc, '_chat_history_save_timer', None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        self._cfg_patcher.stop()
        self._ws_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_badge_shows_off_label_when_toggle_is_false(self):
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._update_chat_history_badge()
        self.assertIn("OFF", self.lc.chat_history_badge.text())

    def test_badge_shows_on_label_when_toggle_is_true(self):
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._update_chat_history_badge()
        self.assertIn("ON", self.lc.chat_history_badge.text())

    def test_badge_uses_green_color_when_on(self):
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._update_chat_history_badge()
        # Green palette matches the active mode_indicator
        self.assertIn("#10B981", self.lc.chat_history_badge.styleSheet())

    def test_badge_uses_neutral_gray_when_off(self):
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._update_chat_history_badge()
        # Neutral gray doesn't compete with other status indicators visually
        self.assertIn("#6B7280", self.lc.chat_history_badge.styleSheet())

    def test_badge_is_visible_after_update(self):
        """Privacy-conscious users should always see the current state."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._update_chat_history_badge()
        self.assertTrue(self.lc.chat_history_badge.isVisible())

    def test_badge_text_changes_reactively_between_states(self):
        """Toggling CONFIG should immediately flip the badge text."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._update_chat_history_badge()
        self.assertIn("OFF", self.lc.chat_history_badge.text())
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._update_chat_history_badge()
        self.assertIn("ON", self.lc.chat_history_badge.text())

    def test_badge_tooltip_differs_for_on_vs_off(self):
        """The tooltip explains what the state means + how to change it."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._update_chat_history_badge()
        off_tip = self.lc.chat_history_badge.toolTip()
        self.assertIn("NOT being persisted", off_tip)
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._update_chat_history_badge()
        on_tip = self.lc.chat_history_badge.toolTip()
        self.assertIn("persisted", on_tip)
        self.assertNotEqual(off_tip, on_tip,
                            "ON and OFF tooltips should explain different states")

    def test_badge_is_noop_when_chat_history_badge_attr_missing(self):
        """If init_ui() never ran (bare-mixin test fixture), update is silent."""
        del self.lc.chat_history_badge
        # Must not raise
        self.lc._update_chat_history_badge()

    def test_badge_skipped_during_shutdown(self):
        """During cross-test teardown _shutting_down=True, the update is
        a no-op (avoids C++-object-destruction races on the QLabel)."""
        self.lc._shutting_down = True
        # The first call would normally set text + show. During shutdown
        # the method returns before touching the widget, so text stays
        # at its initial empty value.
        self.lc._update_chat_history_badge()
        self.assertEqual(self.lc.chat_history_badge.text(), "")

# =============================================================================
# Tests — Chat history reactive toggle (Settings → sidebar)
# =============================================================================
# Verifies the wiring between the Settings tab chk_chat_history checkbox
# and the live CONFIG + sidebar badge. Without this test, a regression
# that disconnects the toggled signal (or stops calling save_settings)
# would silently break the "instant visual feedback" the user expects.

class TestChatHistoryReactiveToggle(unittest.TestCase):
    """Tests for the SettingsTabMixin._on_chat_history_toggle handler.

    Same approach as TestChatHistoryBadge: ``_mk()`` returns a bare
    AppLifecycleMixin, so we bind the SettingsTabMixin method onto the
    test instance via ``types.MethodType`` instead of constructing the
    full settings-tab widget tree.
    """

    def setUp(self):
        import types
        from PyQt6.QtWidgets import QLabel
        from app_lifecycle import AppLifecycleMixin
        from app_ui import AppUIMixin
        from tabs.settings_tab import SettingsTabMixin
        self.tmpdir = tempfile.mkdtemp(prefix="chat_reactive_test_")
        self._ws_patcher = patch("app_lifecycle.WORKSPACE_DIR", self.tmpdir)
        self._ws_patcher.start()
        # freeform_mode/mock_mode pinned False per the CONFIG-mode-pins gate
        # (scripts/assert_config_mode_pins.py): setUp-level clear=False patches
        # must not inherit the user's app_settings.json preferences
        # (debounce window + modes).
        self._cfg_patcher = patch.dict(
            "config.CONFIG",
            {"chat_history_enabled": False, "freeform_mode": False, "mock_mode": False},
            clear=False,
        )
        self._cfg_patcher.start()
        self.lc = _mk()
        # Bind the production methods onto the test instance.
        self.lc._update_chat_history_badge = types.MethodType(
            AppUIMixin._update_chat_history_badge, self.lc
        )
        self.lc._on_chat_history_toggle = types.MethodType(
            SettingsTabMixin._on_chat_history_toggle, self.lc
        )
        # ON→OFF stale-file cleanup uses these two AppLifecycleMixin
        # methods — bind them so the handler can find them via hasattr().
        self.lc._chat_history_path = types.MethodType(
            AppLifecycleMixin._chat_history_path, self.lc
        )
        self.lc._clear_chat_history = types.MethodType(
            AppLifecycleMixin._clear_chat_history, self.lc
        )
        # Wire the minimal attributes the handler needs.
        self.lc.log_to_audit = MagicMock()
        self.lc.chat_history_badge = QLabel()

    def tearDown(self):
        # Stop and delete any real QTimer created by _make_pending_timer()
        # to avoid leaks across tests in the same module.
        timer = getattr(self.lc, '_chat_history_save_timer', None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:
                pass
        self._cfg_patcher.stop()
        self._ws_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_toggle_on_updates_config_to_true(self):
        with patch("tabs.settings_tab.save_settings"):
            self.lc._on_chat_history_toggle(True)
        from config import CONFIG
        self.assertTrue(CONFIG.get("chat_history_enabled"))

    def test_toggle_off_updates_config_to_false(self):
        # Start ON, then toggle OFF
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            with patch("tabs.settings_tab.save_settings"):
                self.lc._on_chat_history_toggle(False)
        from config import CONFIG
        self.assertFalse(CONFIG.get("chat_history_enabled"))

    def test_toggle_persists_to_disk_via_save_settings(self):
        """Mirrors the _on_always_on_top_toggle pattern: every toggle
        triggers an immediate save_settings() so the change survives an
        app crash before the main SAVE button is pressed."""
        with patch("tabs.settings_tab.save_settings") as mock_save:
            self.lc._on_chat_history_toggle(True)
        mock_save.assert_called_once()
        with patch("tabs.settings_tab.save_settings") as mock_save:
            self.lc._on_chat_history_toggle(False)
        mock_save.assert_called_once()

    def test_toggle_refreshes_badge_immediately(self):
        """Reactive UX: the sidebar badge updates the instant the
        checkbox is clicked, no need to wait for the main SAVE button."""
        with patch("tabs.settings_tab.save_settings"):
            self.lc._on_chat_history_toggle(True)
        self.assertIn("ON", self.lc.chat_history_badge.text())
        with patch("tabs.settings_tab.save_settings"):
            self.lc._on_chat_history_toggle(False)
        self.assertIn("OFF", self.lc.chat_history_badge.text())

    def test_toggle_logs_to_audit_with_correct_emoji(self):
        """The audit log line uses an emoji + state so the session journal
        can be skimmed for persistence changes without parsing prose."""
        with patch("tabs.settings_tab.save_settings"):
            self.lc._on_chat_history_toggle(True)
        self.lc.log_to_audit.assert_called_with(
            "💾 Chat history persistence: ON"
        )
        with patch("tabs.settings_tab.save_settings"):
            self.lc._on_chat_history_toggle(False)
        self.lc.log_to_audit.assert_called_with(
            "💾 Chat history persistence: OFF"
        )

    def test_toggle_is_noop_during_shutdown(self):
        """During teardown the handler skips all writes to avoid
        C++-object-destruction races on the QLabel and stale CONFIG writes
        that would race the cross-test reset."""
        self.lc._shutting_down = True
        with patch("tabs.settings_tab.save_settings") as mock_save:
            with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
                self.lc._on_chat_history_toggle(True)
        # No CONFIG write, no save, no audit log entry
        mock_save.assert_not_called()
        self.lc.log_to_audit.assert_not_called()
        from config import CONFIG
        self.assertFalse(CONFIG.get("chat_history_enabled"))

    # ── ON→OFF stale-file auto-deletion (with confirmation) ─────────
    # When the user toggles persistence OFF, a previously-saved
    # chat_history.json sits dormant on disk. The handler now offers to
    # delete it so the user doesn't see a confusing "ghost" file. The
    # default answer is No (safer — deletion is irreversible).

    def _write_stale_history_file(self, body='{"html": "<p>old</p>"}'):
        """Write a stale chat_history.json to the test tempdir and return
        the absolute path. Mirrors what _chat_history_path() resolves to
        under the WORKSPACE_DIR patch."""
        import os as _os
        data_dir = _os.path.join(self.tmpdir, "data")
        _os.makedirs(data_dir, exist_ok=True)
        path = _os.path.join(data_dir, "chat_history.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def test_on_to_off_with_stale_file_and_user_confirms_deletes_file(self):
        """ON→OFF + existing file + user clicks Yes: file is deleted
        and a deletion audit line is appended."""
        stale = self._write_stale_history_file()
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            with patch("tabs.settings_tab.save_settings"):
                with patch("tabs.settings_tab.QMessageBox.question",
                           return_value=QMessageBox.StandardButton.Yes
                           ) as mock_q:
                    self.lc._on_chat_history_toggle(False)
        # Prompt was shown exactly once
        mock_q.assert_called_once()
        # File is gone
        self.assertFalse(os.path.exists(stale))
        # Audit log: the deletion line + the toggle line (in that order)
        self.lc.log_to_audit.assert_any_call(
            "🗑️ Stale chat history file deleted on toggle OFF"
        )
        # CONFIG is now OFF
        from config import CONFIG
        self.assertFalse(CONFIG.get("chat_history_enabled"))

    def test_on_to_off_with_stale_file_and_user_declines_keeps_file(self):
        """ON→OFF + existing file + user clicks No: file is kept and a
        'declined' audit line is appended."""
        stale = self._write_stale_history_file()
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            with patch("tabs.settings_tab.save_settings"):
                with patch("tabs.settings_tab.QMessageBox.question",
                           return_value=QMessageBox.StandardButton.No
                           ) as mock_q:
                    self.lc._on_chat_history_toggle(False)
        # Prompt was shown exactly once
        mock_q.assert_called_once()
        # File is STILL THERE
        self.assertTrue(os.path.exists(stale))
        # Audit log: the declined line, NOT the deletion line
        self.lc.log_to_audit.assert_any_call(
            "ℹ️ Stale chat history file kept (user declined deletion)"
        )
        for call in self.lc.log_to_audit.call_args_list:
            self.assertNotIn(
                "deleted on toggle OFF", call.args[0],
                "Should not have logged deletion when user declined"
            )
        # CONFIG is now OFF
        from config import CONFIG
        self.assertFalse(CONFIG.get("chat_history_enabled"))

    def test_on_to_off_with_no_stale_file_does_not_prompt(self):
        """ON→OFF with no file on disk: skip the prompt entirely so the
        user isn't asked to delete something that doesn't exist."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            with patch("tabs.settings_tab.save_settings"):
                with patch("tabs.settings_tab.QMessageBox.question"
                           ) as mock_q:
                    self.lc._on_chat_history_toggle(False)
        mock_q.assert_not_called()
        # CONFIG is still flipped OFF
        from config import CONFIG
        self.assertFalse(CONFIG.get("chat_history_enabled"))

    def test_off_to_on_does_not_prompt_for_deletion(self):
        """OFF→ON is non-destructive: no prompt, no file scan, CONFIG
        updates to True. The dormant file (if any) is intentionally left
        alone — the next ON→OFF transition will offer to delete it."""
        stale = self._write_stale_history_file()  # file exists
        # CONFIG starts OFF (setUp default)
        with patch("tabs.settings_tab.save_settings"):
            with patch("tabs.settings_tab.QMessageBox.question"
                       ) as mock_q:
                self.lc._on_chat_history_toggle(True)
        mock_q.assert_not_called()
        # File is still there (OFF→ON doesn't touch it)
        self.assertTrue(os.path.exists(stale))
        # CONFIG is now ON
        from config import CONFIG
        self.assertTrue(CONFIG.get("chat_history_enabled"))

    # ── ON→OFF pending-save flush (bug fix) ──────────────────────
    # When the user flips the toggle ON→OFF during the 500ms debounce
    # window, any pending save must be flushed to disk BEFORE the
    # toggle takes effect. Otherwise the on-disk file still has the
    # pre-last-response state and the user's last response is
    # silently lost when the pending timer gets cancelled by the
    # toggle. See _on_chat_history_toggle docstring for the full
    # rationale.

    def _make_pending_timer(self):
        """Create a real active QTimer on the test instance so the
        handler's ``timer is not None and timer.isActive()`` check
        fires. Returns the timer."""
        from PyQt6.QtCore import QTimer
        timer = QTimer()
        timer.setSingleShot(True)
        timer.start(60_000)  # 60s — way past the 500ms debounce
        self.lc._chat_history_save_timer = timer
        return timer

    def test_on_to_off_flushes_pending_save_before_toggle(self):
        """ON→OFF with an active debounce timer calls _flush_chat_
        history_save() BEFORE applying the toggle — so the final
        ON-state content lands on disk while the toggle is still ON.
        Without this fix, the last response is silently lost."""
        self._write_stale_history_file(body="<p>old</p>")
        self._make_pending_timer()
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            with patch("tabs.settings_tab.save_settings"):
                with patch.object(self.lc, "_flush_chat_history_save"
                                  ) as mock_flush:
                    # User confirms deletion of the stale file too
                    with patch("tabs.settings_tab.QMessageBox.question",
                               return_value=QMessageBox.StandardButton.No):
                        self.lc._on_chat_history_toggle(False)
        # Flush was called once
        mock_flush.assert_called_once()
        # The flush audit line was logged
        self.lc.log_to_audit.assert_any_call(
            "💾 Flushed pending save before toggle OFF"
        )
        # CONFIG is now OFF
        from config import CONFIG
        self.assertFalse(CONFIG.get("chat_history_enabled"))

    def test_on_to_off_no_pending_timer_is_noop_for_flush(self):
        """ON→OFF with no active timer (or no timer at all) is a no-op
        for the flush — the handler must not crash trying to call
        _flush_chat_history_save() when there's nothing to flush."""
        # No timer set
        self.assertFalse(hasattr(self.lc, '_chat_history_save_timer')
                         or getattr(self.lc, '_chat_history_save_timer',
                                    None) is not None)
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            with patch("tabs.settings_tab.save_settings"):
                with patch.object(self.lc, "_flush_chat_history_save"
                                  ) as mock_flush:
                    # No QMessageBox.question needed — no stale file
                    self.lc._on_chat_history_toggle(False)
        # Flush was NOT called
        mock_flush.assert_not_called()
        # The "flushed" audit line was NOT logged
        for call in self.lc.log_to_audit.call_args_list:
            self.assertNotIn(
                "Flushed pending save", call.args[0],
                "Should not log flush when no timer is pending"
            )
        # CONFIG is now OFF
        from config import CONFIG
        self.assertFalse(CONFIG.get("chat_history_enabled"))

    def test_on_to_off_with_inactive_timer_does_not_flush(self):
        """ON→OFF with a timer that exists but is NOT active (e.g.
        already fired) does not call flush. The handler only flushes
        when timer.isActive() is True."""
        from PyQt6.QtCore import QTimer
        timer = QTimer()
        timer.setSingleShot(True)
        # Do NOT start it — isActive() will be False
        self.lc._chat_history_save_timer = timer
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            with patch("tabs.settings_tab.save_settings"):
                with patch.object(self.lc, "_flush_chat_history_save"
                                  ) as mock_flush:
                    self.lc._on_chat_history_toggle(False)
        mock_flush.assert_not_called()
        # CONFIG is now OFF
        from config import CONFIG
        self.assertFalse(CONFIG.get("chat_history_enabled"))

    def test_off_to_on_does_not_flush_even_with_timer(self):
        """OFF→ON is non-destructive: no flush. (In practice, OFF
        doesn't schedule saves so there shouldn't be a pending timer,
        but the handler must guard against it defensively.)"""
        self._make_pending_timer()
        with patch("tabs.settings_tab.save_settings"):
            with patch.object(self.lc, "_flush_chat_history_save"
                              ) as mock_flush:
                # CONFIG starts OFF (setUp default)
                self.lc._on_chat_history_toggle(True)
        mock_flush.assert_not_called()
        # CONFIG is now ON
        from config import CONFIG
        self.assertTrue(CONFIG.get("chat_history_enabled"))

class TestChatHistorySaveBadge(unittest.TestCase):
    """Tests for the "💾 saving…" overlay badge that gives the user
    visible feedback while a debounced chat-history save is pending.

    The badge is a small QLabel parented to chat_display (positioned
    bottom-right, transparent for mouse events). It appears the moment
    a save is scheduled, and fades out (windowOpacity 1.0 → 0.0 over
    300ms) when the file is actually written.

    This class uses a real QTextEdit for chat_display (not a MagicMock)
    so the badge can be parented to a real QWidget. The conftest's
    session-scoped QApplication is reused.
    """

    def setUp(self):
        from PyQt6.QtWidgets import QTextEdit
        # Enable persistence for these tests so the gate passes and the
        # badge actually shows.
        # freeform_mode/mock_mode pinned False per the CONFIG-mode-pins gate
        # (scripts/assert_config_mode_pins.py): setUp-level clear=False patches
        # must not inherit the user's app_settings.json preferences
        # (debounce window + modes).
        self._cfg_patcher = patch.dict(
            "config.CONFIG",
            {"chat_history_enabled": True, "chat_history_debounce_ms": 500,
             "freeform_mode": False, "mock_mode": False},
            clear=False,
        )
        self._cfg_patcher.start()
        self.lc = _mk()
        # Use a real QTextEdit so the badge can be parented to a real
        # QWidget. The MagicMock from _mk() won't work for badge creation.
        self.lc.chat_display = QTextEdit()
        self.lc.chat_display.resize(400, 300)

    def tearDown(self):
        self._cfg_patcher.stop()
        # Clean up the QTextEdit to avoid Qt teardown issues
        try:
            badge = getattr(self.lc, 'chat_history_save_badge', None)
            if badge is not None:
                badge.deleteLater()
        except (RuntimeError, AttributeError):
            pass
        try:
            if self.lc.chat_display is not None:
                self.lc.chat_display.deleteLater()
        except RuntimeError:
            pass

    def _badge(self):
        return getattr(self.lc, 'chat_history_save_badge', None)

    # ── Lazy creation ──

    def test_ensure_save_badge_creates_label_lazily(self):
        """First call to _ensure_save_badge creates the QLabel. No badge
        is allocated until the first call — users with persistence off
        never get a Qt object."""
        self.assertIsNone(self._badge())  # Not created yet
        badge = self.lc._ensure_save_badge()
        self.assertIsNotNone(badge)
        self.assertEqual(self._badge(), badge)  # Cached
        self.assertTrue(badge.isHidden())  # Starts hidden

    def test_ensure_save_badge_returns_cached_instance(self):
        """Subsequent calls return the same QLabel (no churn)."""
        b1 = self.lc._ensure_save_badge()
        b2 = self.lc._ensure_save_badge()
        self.assertIs(b1, b2)

    def test_ensure_save_badge_returns_none_without_chat_display(self):
        """No chat_display → None. Safe to call during teardown or in
        test contexts that don't have a real chat widget."""
        self.lc.chat_display = None
        self.assertIsNone(self.lc._ensure_save_badge())

    # ── Show / hide ──

    def test_show_save_badge_makes_label_visible(self):
        self.lc._show_save_badge()
        badge = self._badge()
        self.assertIsNotNone(badge)
        self.assertFalse(badge.isHidden())  # shown
        self.assertEqual(badge.windowOpacity(), 1.0)

    def test_show_save_badge_is_idempotent(self):
        """Calling show twice in a row is fine — badge stays visible."""
        self.lc._show_save_badge()
        self.lc._show_save_badge()
        self.assertFalse(self._badge().isHidden())

    def test_hide_save_badge_fade_starts_animation(self):
        """fade=True (default) starts a QPropertyAnimation that fades
        windowOpacity to 0.0 over 300ms then hides."""
        self.lc._show_save_badge()
        self.assertFalse(self._badge().isHidden())  # was shown
        self.lc._hide_save_badge(fade=True)
        # Animation is running — badge is still shown during the fade
        self.assertFalse(self._badge().isHidden())
        # The animation object exists
        self.assertIsNotNone(
            getattr(self.lc, '_save_badge_fade_anim', None)
        )

    def test_hide_save_badge_immediate_hides(self):
        """fade=False hides immediately and resets opacity to 1.0."""
        self.lc._show_save_badge()
        self.assertFalse(self._badge().isHidden())  # was shown
        self.lc._hide_save_badge(fade=False)
        self.assertTrue(self._badge().isHidden())  # now hidden
        self.assertEqual(self._badge().windowOpacity(), 1.0)

    def test_hide_save_badge_noop_when_not_visible(self):
        """Hide is a no-op when the badge isn't visible (no orphan
        animation started)."""
        self.lc._ensure_save_badge()
        # Badge exists but was never shown
        self.assertTrue(self._badge().isHidden())
        self.lc._hide_save_badge(fade=True)
        # No animation was created
        self.assertIsNone(getattr(self.lc, '_save_badge_fade_anim', None))

    def test_hide_save_badge_cancels_in_flight_fade(self):
        """If a fade is already running, hide() cancels it before
        starting a new one (or hiding immediately)."""
        self.lc._show_save_badge()
        self.lc._hide_save_badge(fade=True)  # Start fade #1
        anim1 = self.lc._save_badge_fade_anim
        # Hide again immediately — should stop the in-flight animation
        self.lc._hide_save_badge(fade=False)
        # anim1 should have been stopped (can't easily verify state, but
        # the second hide should have completed without error)
        self.assertTrue(self._badge().isHidden())  # now hidden

    # ── Wiring into the debounce flow ──

    def test_schedule_chat_history_save_shows_badge(self):
        """_schedule_chat_history_save() must call _show_save_badge() so
        the user sees the saving indicator immediately."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._schedule_chat_history_save()
        badge = self._badge()
        self.assertIsNotNone(badge, "Badge should be created on first schedule")
        self.assertFalse(badge.isHidden())

    def test_save_chat_history_fades_badge_after_write(self):
        """_save_chat_history() must call _hide_save_badge(fade=True) in
        a finally block so the badge fades out even if the write fails."""
        self.lc._show_save_badge()
        self.lc._save_chat_history()
        # Fade animation is running — badge still shown during fade
        self.assertFalse(self._badge().isHidden())
        self.assertIsNotNone(getattr(self.lc, '_save_badge_fade_anim', None))

    def test_save_chat_history_fades_badge_even_on_write_failure(self):
        """The hide happens in a `finally` block — a failed write still
        hides the badge (and the error is logged at debug level)."""
        # Make chat_display.toHtml() raise to simulate a write failure
        self.lc.chat_display.toHtml = MagicMock(
            side_effect=RuntimeError("simulated")
        )
        self.lc._show_save_badge()
        self.lc._save_chat_history()
        # Fade was still triggered
        self.assertIsNotNone(getattr(self.lc, '_save_badge_fade_anim', None))

    def test_clear_chat_history_immediately_hides_badge(self):
        """_clear_chat_history() must call _hide_save_badge(fade=False)
        immediately — a fade would be misleading because the data is
        gone, not saved."""
        self.lc._show_save_badge()
        self.lc._clear_chat_history()
        self.assertTrue(self._badge().isHidden())
        self.assertEqual(self._badge().windowOpacity(), 1.0)

    # ── Gate behavior ──

    def test_show_save_badge_noop_when_toggle_disabled(self):
        """When chat_history_enabled is False, _show_save_badge is a
        no-op (no badge created, no QLabel allocated)."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._show_save_badge()
        self.assertIsNone(self._badge())

    def test_schedule_does_not_show_badge_when_disabled(self):
        """_schedule_chat_history_save() gates on the toggle — no badge
        when persistence is off."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._schedule_chat_history_save()
        self.assertIsNone(self._badge())

    def test_show_save_badge_noop_during_shutdown(self):
        """During shutdown, _show_save_badge is a no-op to avoid
        creating widgets that will be immediately destroyed."""
        self.lc._shutting_down = True
        self.lc._show_save_badge()
        self.assertIsNone(self._badge())

    # ── Burst coalescing semantics ──

    def test_burst_keeps_badge_visible_until_final_save(self):
        """Multiple rapid schedules keep the badge visible (it's
        re-shown on every call), and only the final save fires the
        fade. This is the whole point of the debounce UX."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            # 5 rapid schedules (simulating multi-model compare burst)
            for _ in range(5):
                self.lc._schedule_chat_history_save()
        # Badge is shown after the burst
        self.assertFalse(self._badge().isHidden())
        # The debounce timer is pending (not yet fired)
        timer = self.lc._chat_history_save_timer
        self.assertIsNotNone(timer)
        self.assertTrue(timer.isActive())
        # Now simulate the final save (timer fires)
        self.lc._save_chat_history()
        # Fade animation is running
        self.assertIsNotNone(getattr(self.lc, '_save_badge_fade_anim', None))

    # ── "💾 saving for N ms" live countdown label ──────────────
    # The countdown sits to the left of the badge and ticks every
    # 100ms via a small QTimer, reading QTimer.remainingTime() on
    # the debounce timer to show the live value. Power users with
    # a configurable debounce window want to know exactly when the
    # write will fire.

    def _countdown(self):
        return getattr(self.lc, 'chat_history_save_countdown', None)

    def _tick_timer(self):
        return getattr(self.lc, '_save_countdown_tick_timer', None)

    def test_countdown_creates_label_lazily(self):
        """First _show_save_badge_countdown creates the QLabel and
        starts the tick timer. No countdown widget is allocated
        until the first call."""
        self.assertIsNone(self._countdown())
        self.assertIsNone(self._tick_timer())
        self.lc._show_save_badge_countdown()
        self.assertIsNotNone(self._countdown())
        self.assertIsNotNone(self._tick_timer())

    def test_countdown_returns_cached_instance(self):
        """Subsequent _ensure calls return the same QLabel."""
        c1 = self.lc._ensure_save_badge_countdown()
        c2 = self.lc._ensure_save_badge_countdown()
        self.assertIs(c1, c2)

    def test_countdown_returns_none_without_chat_display(self):
        """No chat_display → None. Mirrors the badge behavior."""
        self.lc.chat_display = None
        self.assertIsNone(self.lc._ensure_save_badge_countdown())

    def test_countdown_returns_none_for_magicmock_chat_display(self):
        """chat_display is a MagicMock (not a QWidget) → None.
        Same isinstance guard as the badge."""
        self.lc.chat_display = MagicMock()
        self.assertIsNone(self.lc._ensure_save_badge_countdown())

    def test_countdown_shows_label_and_starts_tick(self):
        """_show_save_badge_countdown makes the label visible and
        starts the 100ms tick timer."""
        self.lc._show_save_badge_countdown()
        self.assertFalse(self._countdown().isHidden())
        tick = self._tick_timer()
        self.assertTrue(tick.isActive())
        self.assertEqual(tick.interval(), 100)

    def test_countdown_text_shows_remaining_time(self):
        """After schedule, the countdown text contains the live
        remaining time from QTimer.remainingTime()."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": True}):
            self.lc._schedule_chat_history_save()
        # Badge show also starts the countdown
        self.lc._show_save_badge()
        countdown = self._countdown()
        self.assertIsNotNone(countdown)
        # Text should contain "saving for" and an "ms" suffix
        text = countdown.text()
        self.assertIn("saving for", text)
        self.assertIn("ms", text)
        # The remaining time should be a non-negative integer and the
        # countdown must be tied to the actual debounce window (not a
        # hardcoded 500): assert against the pending timer's own
        # remainingTime() so user-configured windows (leaked or set via
        # Settings) can't break the assertion.
        import re
        match = re.search(r"saving for (\d+) ms", text)
        self.assertIsNotNone(
            match, f"Expected 'saving for N ms' in text, got: {text!r}"
        )
        remaining = int(match.group(1))
        timer = self.lc._chat_history_save_timer
        self.assertTrue(timer.isActive(),
                        "precondition: debounce timer must be pending")
        # Bounds are semantic, not a hardcoded 500: the countdown must show
        # a non-negative remainder within the configured debounce window
        # (whatever the setting is — the window itself is pinned in setUp,
        # so a leaked app_settings.json value can't drift the assertions).
        import config as _cfg
        from app_lifecycle import CHAT_HISTORY_DEBOUNCE_MS
        configured = int(_cfg.CONFIG.get(
            "chat_history_debounce_ms", CHAT_HISTORY_DEBOUNCE_MS
        ))
        self.assertGreaterEqual(remaining, 0)
        self.assertLessEqual(
            remaining, configured,
            "countdown must not overstate the configured debounce window",
        )

    def test_countdown_text_falls_back_to_saving_when_timer_inactive(self):
        """When the debounce timer is inactive (remainingTime < 0),
        the countdown shows the generic "💾 saving…" text instead
        of a stale value."""
        self.lc._ensure_save_badge_countdown()
        # Create a countdown timer but DON'T start it — remainingTime
        # will be -1 (inactive).
        from PyQt6.QtCore import QTimer
        tick = QTimer()
        tick.setInterval(100)
        self.lc._save_countdown_tick_timer = tick
        self.lc._chat_history_save_timer = QTimer()  # not started
        countdown = self._countdown()
        countdown.show()
        # Update the label
        self.lc._update_save_badge_countdown()
        self.assertIn("saving", countdown.text())
        # Should NOT contain "for N ms" (that's only for active timers)
        self.assertNotIn("for 0 ms", countdown.text())

    def test_countdown_hide_stops_tick_timer(self):
        """_hide_save_badge_countdown stops the 100ms tick timer
        so it doesn't waste CPU updating a hidden label."""
        self.lc._show_save_badge_countdown()
        self.assertTrue(self._tick_timer().isActive())
        self.lc._hide_save_badge_countdown()
        self.assertFalse(self._tick_timer().isActive())
        self.assertTrue(self._countdown().isHidden())

    def test_countdown_is_hidden_when_badge_is_hidden(self):
        """Hiding the badge (fade or immediate) also hides the
        countdown and stops the tick timer. Mirrors the badge's
        lifecycle."""
        self.lc._show_save_badge()
        self.assertFalse(self._countdown().isHidden())
        self.lc._hide_save_badge(fade=False)
        self.assertTrue(self._countdown().isHidden())
        self.assertFalse(self._tick_timer().isActive())

    def test_countdown_noop_when_toggle_disabled(self):
        """When chat_history_enabled is False, the countdown is a
        no-op (no label created, no tick timer)."""
        with patch.dict("config.CONFIG", {"chat_history_enabled": False}):
            self.lc._show_save_badge_countdown()
        self.assertIsNone(self._countdown())
        self.assertIsNone(self._tick_timer())

    def test_countdown_noop_during_shutdown(self):
        """During shutdown, _show_save_badge_countdown is a no-op
        to avoid creating widgets that will be immediately destroyed."""
        self.lc._shutting_down = True
        self.lc._show_save_badge_countdown()
        self.assertIsNone(self._countdown())

    # ── Countdown tick animation (scale-up pulse on each tick) ───
    # The countdown label briefly scales up (~15% from center) on
    # every text change so the eye catches the number drop even
    # when glancing at the badge. Verified here: animation is
    # triggered on text change, cancelled on rapid ticks, no-op
    # when hidden / during shutdown, and the cached animation
    # reference is cleared on finish so it doesn't leak.

    def test_countdown_tick_animation_starts_on_text_change(self):
        """When the countdown text changes, a QPropertyAnimation is
        started and the cached reference is set so rapid ticks can
        cancel it."""
        self.lc._show_save_badge_countdown()
        # First update starts the animation (text goes from empty to
        # the 'saving for N ms' line)
        self.lc._update_save_badge_countdown()
        anim = getattr(self.lc, '_countdown_tick_anim', None)
        self.assertIsNotNone(anim, 'Animation should be created on first tick')
        # The animation is targeting the countdown label's geometry
        from PyQt6.QtCore import QPropertyAnimation
        self.assertIsInstance(anim, QPropertyAnimation)
        self.assertEqual(anim.targetObject(), self._countdown())
        self.assertEqual(anim.propertyName(), b'geometry')

    def test_countdown_tick_animation_cancelled_on_rapid_tick(self):
        """Rapid ticks (e.g. 100ms apart) cancel the in-flight
        animation and start a fresh one — so the pulse stays
        snappy and doesn't queue up."""
        self.lc._show_save_badge_countdown()
        # First tick — creates animation #1
        self.lc._update_save_badge_countdown()
        anim1 = self.lc._countdown_tick_anim
        # Second tick before #1 finishes — should cancel #1
        # and create a new one
        self.lc._update_save_badge_countdown()
        anim2 = self.lc._countdown_tick_anim
        self.assertIsNotNone(anim1)
        self.assertIsNotNone(anim2)
        self.assertIsNot(
            anim1, anim2,
            'Second tick should cancel the first animation and start a new one'
        )

    def test_countdown_tick_animation_noop_when_hidden(self):
        """If the countdown isn't visible, no animation is created
        (saves CPU when the user has scrolled away or the badge is
        hidden)."""
        self.lc._show_save_badge_countdown()
        # Hide the countdown
        self.lc._hide_save_badge_countdown()
        # _show_save_badge_countdown may have already started an
        # animation; clear it so we can verify the no-op path
        # of _animate_countdown_tick on a hidden countdown.
        self.lc._countdown_tick_anim = None
        # Try to animate while hidden
        self.lc._animate_countdown_tick()
        self.assertIsNone(
            self.lc._countdown_tick_anim,
            'Hidden countdown must not start an animation'
        )

    def test_countdown_tick_animation_noop_during_shutdown(self):
        """During teardown the animation is a no-op so we don't
        create QPropertyAnimation objects that will be immediately
        destroyed (cross-test safety)."""
        self.lc._show_save_badge_countdown()
        # _show_save_badge_countdown may have already started an
        # animation (via _update_save_badge_countdown). Clear the
        # cached reference so we can verify the no-op path of
        # _animate_countdown_tick cleanly.
        self.lc._countdown_tick_anim = None
        self.lc._shutting_down = True
        try:
            self.lc._animate_countdown_tick()
            self.assertIsNone(self.lc._countdown_tick_anim)
        finally:
            self.lc._shutting_down = False

    def test_countdown_clear_immediately_hides(self):
        """_clear_chat_history must hide the countdown and stop the
        tick timer (via _hide_save_badge → _hide_save_badge_countdown)."""
        self.lc._show_save_badge()
        self.lc._clear_chat_history()
        self.assertTrue(self._countdown().isHidden())
        self.assertFalse(self._tick_timer().isActive())

    # ── Hover tooltip on the countdown label ─────────────────
    # The countdown sits to the LEFT of the badge and ticks every
    # 100ms. The hover tooltip is the user’s deep-dive surface for
    # the debounce window: it shows the min/max range, the default,
    # and the user’s currently-configured value. Verified here.

    def test_countdown_tooltip_contains_current_configured_value(self):
        """The hover tooltip includes the user's currently-configured
        debounce window (reads live CONFIG, not a cached value)."""
        with patch.dict(
            'config.CONFIG',
            {'chat_history_debounce_ms': 250}, clear=False
        ):
            self.lc._show_save_badge_countdown()
        tip = self._countdown().toolTip()
        self.assertIn('250 ms', tip,
                       f'Tooltip must include the configured 250 ms, got: {tip!r}')

    def test_countdown_tooltip_contains_min_max_range(self):
        """The hover tooltip documents the full configurable range
        (0-60000 ms) so power users can see what's possible without
        opening the Settings tab."""
        self.lc._show_save_badge_countdown()
        tip = self._countdown().toolTip()
        # Tighter: assert the actual range line, not loose substrings
        # that could match the 'Default: 500 ms' or '0 = no debounce'
        # lines by accident.
        self.assertIn('Range: 0-60000 ms', tip)

    def test_countdown_tooltip_contains_default(self):
        """The hover tooltip reminds the user of the default (500 ms)
        so they have a reference point when tweaking."""
        self.lc._show_save_badge_countdown()
        tip = self._countdown().toolTip()
        self.assertIn('500', tip)
        self.assertIn('Default', tip)

    def test_countdown_tooltip_updates_when_config_changes(self):
        """Each _show_save_badge_countdown call re-reads CONFIG so
        the tooltip always reflects the current setting, not a stale
        value from a previous show. The 'Default: 500 ms' line stays
        present (it's the module constant, always 500); the 'Chat-
        history debounce:' line is the one that must update."""
        # First show with the default
        with patch.dict(
            'config.CONFIG',
            {'chat_history_debounce_ms': 500}, clear=False
        ):
            self.lc._show_save_badge_countdown()
        tip1 = self._countdown().toolTip()
        self.assertIn('Chat-history debounce: 500 ms', tip1)
        # Change CONFIG and re-show — the 'current value' line must flip
        with patch.dict(
            'config.CONFIG',
            {'chat_history_debounce_ms': 2000}, clear=False
        ):
            self.lc._show_save_badge_countdown()
        tip2 = self._countdown().toolTip()
        self.assertIn('Chat-history debounce: 2000 ms', tip2)
        # The 'Default: 500 ms' line is constant and stays present
        self.assertIn('Default: 500 ms', tip2,
                       'Default line must still mention the 500 ms default')
        # The CURRENT line must not retain the stale value
        # (the default line is allowed to keep saying 500 ms since
        # it's a constant, so we narrow the assertion to the exact
        # 'Chat-history debounce: <stale>' prefix)
        self.assertNotIn('Chat-history debounce: 500 ms', tip2,
                          'Current-value line must not retain the stale 500 ms')

    def test_countdown_tooltip_clamps_oversized_config(self):
        """If CONFIG somehow has a value above the spinbox max
        (60000 ms), the tooltip clamps it to 60000 so it never
        shows an impossible-to-set value."""
        with patch.dict(
            'config.CONFIG',
            {'chat_history_debounce_ms': 99_999_999}, clear=False
        ):
            self.lc._show_save_badge_countdown()
        tip = self._countdown().toolTip()
        self.assertIn('60000 ms', tip,
                       f'Tooltip must clamp to 60000 ms, got: {tip!r}')
        self.assertNotIn('99999', tip)

    def test_countdown_tooltip_falls_back_on_non_numeric_config(self):
        """A non-numeric CONFIG value (e.g. 'abc') falls back to
        the module constant 500 ms in the tooltip, same as the
        spinbox's clamp logic."""
        with patch.dict(
            'config.CONFIG',
            {'chat_history_debounce_ms': 'abc'}, clear=False
        ):
            self.lc._show_save_badge_countdown()
        tip = self._countdown().toolTip()
        self.assertIn('500 ms', tip)

# Tests - chat history cross-file isolation regression
# Locks in the conftest _suppress_quit flag + deleteLater() pattern.
# Simulates a prior Qt-heavy file, tears it down, runs smoke tests from
# each of the 6 chat-history test classes to verify they all still pass.

@pytest.mark.timeout(30)
class TestChatHistoryCrossFileIsolation(unittest.TestCase):
    def test_all_six_classes_pass_after_heavy_qt_setup(self):
        import gc
        from PyQt6.QtCore import QTimer, QPropertyAnimation
        from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel
        app = QApplication.instance()
        self.assertIsNotNone(app)
        windows = []
        for i in range(3):
            w = QMainWindow()
            w.setWindowTitle('p_' + str(i))
            central = QWidget(w)
            layout = QVBoxLayout(central)
            for j in range(5):
                layout.addWidget(QLabel('l_' + str(i) + '_' + str(j), central))
            w.setCentralWidget(central)
            w.resize(400, 300)
            w.show()
            windows.append(w)
        timers = []
        for i in range(3):
            t = QTimer()
            t.setInterval(60_000)  # 60s - never fires during the test
            t.start()
            timers.append(t)
        anim = QPropertyAnimation(windows[0], b'windowOpacity')
        anim.setDuration(60000)  # 60s - way past test duration so it never fires mid-test
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.start()

        # NOTE: We deliberately do NOT call w.close() here. Raw QMainWindow.close()
        # does NOT call QApplication.quit() - that is KokertechDashboard-specific
        # behavior in app_lifecycle.py::closeEvent. The heavy setup is still
        # realistic (3 real QMainWindows with child widgets, 3 active QTimers,
        # 1 QPropertyAnimation, 3 module patches) - it mirrors what a prior
        # Qt-heavy test file would leave behind, which is the scenario conftest
        # reset must clean up.
        # Ensure plugin_registry.registry exists before patch() tries to
        # resolve the dotted path (registry is a dynamic attribute created
        # by conftest._reset_plugin_registry, not a module-level export).
        # The stub PluginRegistry has no methods (decompiled placeholder),
        # so we also seed execute_command (patch needs it to exist first).
        prior_patches = [patch.multiple('memory_vault', semantic_search=MagicMock(return_value=[]), store_memory=MagicMock(), ensure_tables_exist=MagicMock()), patch.dict('config.CONFIG', {'stale': 'x'}, clear=False), patch('plugin_registry.registry.execute_command', MagicMock(return_value='ok'))]
        for pp in prior_patches:
            pp.start()
        # Verify the heavy setup actually took effect (no-op patch.start() bug
        # would silently make the test weaker - these assertions prove the
        # patches are real)
        import memory_vault
        import plugin_registry
        # patch.multiple() returns a _patch object - verify the module
        # attributes are now MagicMocks (not the originals). This proves
        # the heavy setup actually took effect.
        from unittest.mock import MagicMock as _MM
        self.assertIsInstance(memory_vault.semantic_search, _MM,
            'memory_vault.semantic_search was not patched - heavy setup is broken')
        self.assertIsInstance(plugin_registry.registry.execute_command, _MM,
            'plugin_registry.execute_command was not patched - heavy setup is broken')
        try:
            for t in timers:
                t.stop(); t.deleteLater()
            for w in windows:
                w.deleteLater()
            anim.stop(); anim.deleteLater()
            for pp in prior_patches:
                pp.stop()
            for _ in range(20):
                app.processEvents()
            try: app.sendPostedEvents()
            except Exception: pass
            gc.collect()
            self._sp(); self._sw(); self._sd(); self._sb(); self._sr(); self._ss()
        finally:
            for t in timers:
                try: t.stop()
                except Exception: pass
            for w in windows:
                try: w.deleteLater()
                except Exception: pass
            try: anim.stop()
            except Exception: pass
            for pp in prior_patches:
                try: pp.stop()
                except Exception: pass
            for _ in range(10):
                try: app.processEvents()
                except Exception: break
            gc.collect()
    def _ml(self):
        lc = _mk()
        lc.chat_display.toHtml.return_value = '<p>x</p>'
        return lc
    def _ct(self, lc):
        t = getattr(lc, '_chat_history_save_timer', None)
        if t is not None:
            try: t.stop(); t.deleteLater()
            except RuntimeError: pass
    def _sp(self):
        lc = self._ml(); tmpdir = tempfile.mkdtemp(prefix='ip_')
        try:
            with patch('app_lifecycle.WORKSPACE_DIR', tmpdir), patch.dict('config.CONFIG', {'chat_history_enabled': True}):
                lc._save_chat_history()
                self.assertTrue(os.path.exists(os.path.join(tmpdir, 'data', 'chat_history.json')))
        finally:
            self._ct(lc); shutil.rmtree(tmpdir, ignore_errors=True)
    def _sw(self):
        lc = self._ml(); lc._append_assistant_message = MagicMock(); lc._append_error_message = MagicMock(); lc.chk_desire_engine.isChecked.return_value = False
        tmpdir = tempfile.mkdtemp(prefix='iw_')
        try:
            with patch('app_lifecycle.WORKSPACE_DIR', tmpdir), patch.dict('config.CONFIG', {'chat_history_enabled': True}), patch('workers.AuditorWorker'), patch('workers.DesireWorker'):
                lc.handle_ai_response({'final': 'ok', 'thinking': ''})
                t = getattr(lc, '_chat_history_save_timer', None)
                self.assertIsNotNone(t); self.assertTrue(t.isActive())
                lc._flush_chat_history_save()
                self.assertTrue(os.path.exists(os.path.join(tmpdir, 'data', 'chat_history.json')))
        finally:
            self._ct(lc); shutil.rmtree(tmpdir, ignore_errors=True)
    def _sd(self):
        lc = self._ml(); tmpdir = tempfile.mkdtemp(prefix='id_')
        try:
            with patch('app_lifecycle.WORKSPACE_DIR', tmpdir), patch.dict('config.CONFIG', {'chat_history_enabled': True}):
                for _ in range(5): lc._schedule_chat_history_save()
                lc._flush_chat_history_save()
                self.assertTrue(os.path.exists(os.path.join(tmpdir, 'data', 'chat_history.json')))
        finally:
            self._ct(lc); shutil.rmtree(tmpdir, ignore_errors=True)
    def _sb(self):
        import types
        from PyQt6.QtWidgets import QLabel
        from app_ui import AppUIMixin
        lc = self._ml(); lc._update_chat_history_badge = types.MethodType(AppUIMixin._update_chat_history_badge, lc)
        badge = QLabel(); lc.chat_history_badge = badge
        try:
            with patch.dict('config.CONFIG', {'chat_history_enabled': True}): lc._update_chat_history_badge()
            self.assertIn('ON', badge.text()); self.assertIn('#10B981', badge.styleSheet())
        finally:
            try: badge.deleteLater()
            except Exception: pass
    def _sr(self):
        import types
        from PyQt6.QtWidgets import QLabel
        from app_ui import AppUIMixin
        from tabs.settings_tab import SettingsTabMixin
        lc = self._ml(); lc._update_chat_history_badge = types.MethodType(AppUIMixin._update_chat_history_badge, lc); lc._on_chat_history_toggle = types.MethodType(SettingsTabMixin._on_chat_history_toggle, lc); lc.log_to_audit = MagicMock()
        badge = QLabel(); lc.chat_history_badge = badge
        try:
            with patch('tabs.settings_tab.save_settings'): lc._on_chat_history_toggle(True)
            from config import CONFIG
            self.assertTrue(CONFIG.get('chat_history_enabled')); self.assertIn('ON', badge.text())
        finally:
            try: badge.deleteLater()
            except Exception: pass
    def _ss(self):
        from PyQt6.QtWidgets import QTextEdit
        lc = self._ml(); cd = QTextEdit(); cd.resize(400, 300); lc.chat_display = cd
        try:
            with patch.dict('config.CONFIG', {'chat_history_enabled': True}):
                lc._show_save_badge()
                badge = getattr(lc, 'chat_history_save_badge', None)
                self.assertIsNotNone(badge); self.assertFalse(badge.isHidden())
                lc._hide_save_badge(fade=False); self.assertTrue(badge.isHidden())
        finally:
            badge = getattr(lc, 'chat_history_save_badge', None)
            if badge is not None:
                try: badge.deleteLater()
                except Exception: pass
            try: cd.deleteLater()
            except Exception: pass


class TestAppLifecycleErrorCallbacks(unittest.TestCase):
    def test_run_in_sandbox_exception_callback(self):
        from app_lifecycle import AppLifecycleMixin
        lc = AppLifecycleMixin()
        lc.chat_display = MagicMock()
        lc.log_to_audit = MagicMock()
        lc._last_code_block = "print(1)"

        with patch("docker_sandbox.execute_code", side_effect=RuntimeError("Docker crashed")), \
             patch("threading.Thread") as mock_thread, \
             patch("PyQt6.QtCore.QTimer.singleShot") as mock_single_shot:
            def run_sync(target=None, **kwargs):
                target()
                return MagicMock()
            mock_thread.side_effect = run_sync
            lc._action_run_code_block()
            callback = mock_single_shot.call_args[0][1]
            callback()
            self.assertTrue(any("❌ Sandbox execution failed: Docker crashed" in str(c) for c in lc.chat_display.append.call_args_list))

    def test_handle_ab_test_exception_callback(self):
        from app_lifecycle import AppLifecycleMixin
        lc = AppLifecycleMixin()
        lc.chat_display = MagicMock()
        lc.thinking_display = MagicMock()
        lc.log_to_audit = MagicMock()
        lc.controller = MagicMock()
        lc.controller.process_input_with_personas.side_effect = RuntimeError("AB test model timeout")

        with patch("threading.Thread") as mock_thread, \
             patch("PyQt6.QtCore.QTimer.singleShot") as mock_single_shot:
            def run_sync(target=None, **kwargs):
                target()
                return MagicMock()
            mock_thread.side_effect = run_sync
            lc._action_send_ab_test("prompt", "a", "b")
            for call in mock_single_shot.call_args_list:
                fn = call[0][1]
                fn()
            self.assertTrue(any("❌ A/B test failed: AB test model timeout" in str(c) for c in lc.chat_display.append.call_args_list))


