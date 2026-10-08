"""
Integration tests for KokertechDashboard.closeEvent() - verifies the
end-to-end cleanup path: timers, workers, threads,
MCP server, notification manager, and wake listener.

NOTE: Historical cross-file QApplication singleton issue (conftest.py) — now resolved.
      All 51 pre-existing failures were fixed in June 2026 (test fixture
      isolation improvements, proper QWidget cleanup, and standardized mock
      patterns).  Per-file and cross-file batches both pass cleanly.

      Run this file in isolation to verify actual test results:
        pytest test_app_core_integration.py
"""

import os
import sys
import threading
import unittest
from unittest.mock import patch, MagicMock, call, ANY

# QApplication provided by conftest.py (session-scoped qapp fixture)

def _create_dashboard():
    """Create a KokertechDashboard with all heavy external deps mocked."""
    import app_core

    # Patch init_ui to prevent HTTP hang in create_settings_tab -> _refresh_model_dropdown
    ui = patch("app_core.KokertechDashboard.init_ui")
    ui.start()

    mem = patch.multiple("memory_vault",
        semantic_search=MagicMock(return_value=[]),
        store_memory=MagicMock(),
        get_recent_bias=MagicMock(return_value=[]),
        get_growth_arc=MagicMock(return_value=[]),
        ensure_tables_exist=MagicMock(),
        consolidate_episodic=MagicMock(return_value=3),
    )
    mem.start()

    # Patch app_core.get_logger (local import at module level), not logging_config.get_logger
    log = patch("app_core.get_logger", return_value=MagicMock())
    log.start()

    tts = patch("app_core.VoiceOutput", return_value=MagicMock())
    tts.start()

    vrw = patch("workers.VoiceRecorderWorker", return_value=MagicMock())
    vrw.start()

    req = patch("requests.post")
    mock_post = req.start()
    mock_post.return_value = MagicMock(status_code=200,
        json=lambda: {"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        raise_for_status=lambda: None)

    # Also mock requests.get — Discovery.probe() uses GET (not POST) to
    # reach the /v1/models endpoint at 127.0.0.1:1337. Without this patch
    # the background discovery thread hangs on a real TCP connection attempt.
    req_get = patch("requests.get")
    mock_get = req_get.start()
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"object": "list", "data": []})

    # Disable background startup tasks that interact with real hardware
    # (model loading via llama.cpp) or real network (discovery probe).
    # These run on daemon threads and do not block construction, but they
    # consume significant CPU/RAM and can cause test timeouts indirectly
    # via system resource pressure or C++-level lock contention.
    # Uses patch.dict so CONFIG is automatically restored on stop().
    cfg = patch.dict("config.CONFIG", {
        "model_prewarm_enabled": False,
        "discovery_probe_on_startup": False,
    }, clear=False)
    cfg.start()

    # Belt-and-suspenders: also mock Discovery.probe directly so that
    # even if the above patches were somehow bypassed, no real HTTP
    # request is ever made.
    disc = patch("Discovery.probe")
    disc.start()

    # Patch timer setup methods to prevent background QTimers from starting.
    # Without these patches, the timers (vram 2s, cleanup 30s, consolidation
    # 5min, daily summary 1hr) keep firing after construction and can
    # interfere with test teardown.
    timer_patches = patch.multiple(
        "app_core.KokertechDashboard",
        setup_vram_monitor=MagicMock(),
        setup_cleanup_timer=MagicMock(),
        setup_consolidation_timer=MagicMock(),
        setup_daily_summary_timer=MagicMock(),
    )
    timer_patches.start()

    # Patch onboarding_wizard.is_first_run to prevent the wizard dialog
    # from appearing during tests (would block on exec() for user input).
    onb = patch("onboarding_wizard.is_first_run", return_value=False)
    onb.start()

    # Patch _prewarm_model to prevent REAL llama.cpp model loads. Without
    # this, every KokertechDashboard() construction spawns the unguarded
    # prewarm daemon thread (app_core._init_async_and_deferred_tasks),
    # which loads the production GGUF into the test process. Observed
    # 2026-09-22: 'llama_context: n_ctx_seq (4096) < n_ctx_train (262144)'
    # printed during chunked-serial full-suite runs; native llama state in
    # the pytest process caused nondeterministic hard-crash deaths in later
    # tests (known-failures operating notes). NOTE: the historical comment below
    # ("model_prewarm_enabled: False" disables model loading) was a PHANTOM
    # — that key is read by no production code; _prewarm_model is unguarded.
    # Mirrors the fix in tests/test_app_core.py::_make() (Group 4, 2026-07-14).
    prewarm = patch("app_core.KokertechDashboard._prewarm_model", MagicMock())
    prewarm.start()

    # ── Tab init patches (prevent hangs during Dashboard creation) ───
    # Patch _init_safety to prevent slow pyautogui → pyscreeze → PIL.ImageFont
    # import chain that hangs under pytest on Windows.
    _p_cu = patch("tabs.computer_use_tab.ComputerUseTabMixin._init_safety", MagicMock())
    _p_cu.start()
    # Patch _update_health to prevent slow nvidia-smi/GPUtil VRAM queries.
    _p_h = patch("tabs.health_tab.HealthTabMixin._update_health", MagicMock())
    _p_h.start()
    # Patch refresh_agency_data to prevent SQLite lock contention.
    _p_ca = patch("tabs.cognitive_agency_tab.CognitiveAgencyTabMixin.refresh_agency_data", MagicMock())
    _p_ca.start()

    mock_registry = MagicMock()
    mock_registry.get_plugin_count.return_value = 0
    mock_registry.get_enabled_count.return_value = 0
    mock_registry.get_system_prompt_addition.return_value = "[]"
    mock_registry.plugins = {}
    mock_registry.metadata = {}
    mock_registry.disabled = set()
    mock_registry.is_enabled.return_value = True
    pl = patch("plugin_registry.registry", mock_registry)
    pl.start()

    # ── closeEvent path mocks ─────────────────────────────────────
    # Mock scan_execution_log (called by _call_with_timeout in closeEvent).
    # consolidate_episodic is already mocked via patch.multiple above.
    _p_log = patch("utils.log_scanner.scan_execution_log")
    _p_log.start()
    # Mock module-level imports that closeEvent triggers
    _p_mcp = patch("mcp_server.stop_http_server")
    _p_mcp.start()
    _p_kb = patch("keyboard.unhook_all")
    _p_kb.start()

    w = app_core.KokertechDashboard()
    # Prevent closeEvent from calling QApp.quit() which sets the internal
    # closingDown flag on the session-scoped QApplication, preventing widget
    # creation in subsequent test files. Set here in _create_dashboard so
    # it applies to ALL windows created through this helper, including
    # those in EdgeCases tests that call closeEvent() directly.
    w._suppress_quit = True

    return w, {"memory_vault": mem, "logger": log, "tts": tts, "vrw": vrw,
        "plugin_registry": pl, "requests": req, "requests_get": req_get,
        "init_ui": ui, "discovery_probe": disc, "config": cfg,
        "timer_patches": timer_patches, "onboarding": onb, "prewarm": prewarm,
        "log_scanner": _p_log, "mcp_server": _p_mcp, "keyboard": _p_kb,
        "computer_use_init_safety": _p_cu, "health_update": _p_h,
        "agency_refresh": _p_ca}

class TestCloseEventBasicCleanup(unittest.TestCase):
    """Tests for the core closeEvent cleanup paths."""

    def setUp(self):
        self.window, self.mocks = _create_dashboard()
        # Prevent closeEvent from calling QApp.quit() which sets the internal
        # closingDown flag, preventing widget creation in subsequent test files.
        self.window._suppress_quit = True
        from PyQt6.QtCore import QTimer
        self.window.vram_timer = QTimer()
        self.window.cleanup_timer = QTimer()

    def _destroy_widget(self):
        """Destroy the C++ widget to prevent pytest hang at session end.

        KokertechDashboard teardown (closeEvent) calls QApplication.instance().quit(),
        putting QApplication into a "closing down" state. Without explicitly destroying
        the C++ widget via deleteLater() + processEvents(), the dangling widget object
        causes Qt to hang when the session-scoped qapp fixture is cleaned up.
        """
        try:
            self.window.deleteLater()
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                for _ in range(5):
                    app.processEvents()
        except Exception:
            pass

    def tearDown(self):
        try:
            self.window._suppress_quit = True
            self.window.close()
        except Exception:
            pass
        self._destroy_widget()
        for m in self.mocks.values():
            try:
                m.stop()
            except Exception:
                pass

    def test_closeEvent_stops_timers(self):
        self.assertIsNotNone(self.window.vram_timer)
        self.assertIsNotNone(self.window.cleanup_timer)
        self.window.vram_timer.stop = MagicMock(wraps=self.window.vram_timer.stop)
        self.window.cleanup_timer.stop = MagicMock(wraps=self.window.cleanup_timer.stop)
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        self.window.vram_timer.stop.assert_called_once()
        self.window.cleanup_timer.stop.assert_called_once()
        self.assertTrue(ev.isAccepted())

    def test_closeEvent_calls_consolidation(self):
        """closeEvent calls consolidate_episodic on shutdown."""
        from memory_vault import consolidate_episodic as ce
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        ce.assert_called_once_with(importance_threshold=5, max_age_days=1)
        self.assertTrue(ev.isAccepted())

    def test_web_server_port_persistence(self):
        """
        Web server port set in CONFIG survives save/load roundtrip.
        Simulates: user sets custom port -> save -> restart -> verify restored.

        Uses a temp file for SETTINGS_PATH (via patch.object) to avoid
        writing to the real app_settings.json.
        """
        import json
        import tempfile
        import config as _cfg
        from config import CONFIG, save_settings, _load_settings

        temp_path = tempfile.mktemp(suffix=".json")
        original_port = CONFIG.get("web_server_port", 5050)
        try:
            with patch.object(_cfg, "SETTINGS_PATH", temp_path):
                # 1. Set a custom port (simulates UI change via _on_port_changed)
                custom_port = 8080
                CONFIG["web_server_port"] = custom_port

                # 2. Persist to disk (as _on_port_changed calls save_settings)
                save_settings()
                self.assertTrue(
                    os.path.isfile(temp_path),
                    f"temp_path {temp_path} should exist after save_settings"
                )

                # 3. Read the JSON file directly to verify disk persistence
                with open(temp_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertEqual(
                    saved.get("web_server_port"), custom_port,
                    f"web_server_port should be {custom_port} in saved JSON, got {saved.get('web_server_port')}"
                )

                # 4. Clear CONFIG and reload from disk (simulates app restart)
                CONFIG.pop("web_server_port", None)
                self.assertNotIn(
                    "web_server_port", CONFIG,
                    "web_server_port should be absent from CONFIG before reload"
                )
                _load_settings()

                # 5. Verify the custom port was restored after reload
                self.assertEqual(
                    CONFIG.get("web_server_port"), custom_port,
                    f"web_server_port should be {custom_port} after restart/reload, got {CONFIG.get('web_server_port')}"
                )

        finally:
            # Restore original port to avoid polluting CONFIG for subsequent tests
            CONFIG["web_server_port"] = original_port
            # Clean up the temp file
            try:
                if os.path.isfile(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass

        # SETTINGS_PATH is auto-restored by patch.object exiting the with block

    def test_closeEvent_saves_window_geometry(self):
        """closeEvent stores base64-encoded geometry and maximized state to CONFIG."""
        import base64
        from PyQt6.QtGui import QCloseEvent
        from config import CONFIG

        # Clear any leftover keys from previous tests
        CONFIG.pop("_window_geometry", None)
        CONFIG.pop("_window_maximized", None)

        ev = QCloseEvent()
        self.window.closeEvent(ev)

        # Geometry should be a non-empty, valid base64 string
        geom_b64 = CONFIG.get("_window_geometry", "")
        self.assertGreater(len(geom_b64), 0,
            "CONFIG['_window_geometry'] should be non-empty after closeEvent")
        decoded = base64.b64decode(geom_b64)
        self.assertGreater(len(decoded), 0,
            "Decoded geometry bytes should be non-empty")

        # Maximized state should be a bool matching the actual window state
        maximized = CONFIG.get("_window_maximized")
        self.assertIsInstance(maximized, bool,
            "CONFIG['_window_maximized'] should be a bool")
        self.assertEqual(maximized, self.window.isMaximized(),
            "Saved maximized state should match window.isMaximized()")

        self.assertTrue(ev.isAccepted())

    def test_closeEvent_logs_shutdown(self):
        self.window.log_to_audit = MagicMock()
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        self.window.log_to_audit.assert_any_call("Shutdown initiated.")
        self.window.log_to_audit.assert_any_call("Shutdown complete.")

    def test_closeEvent_calls_log_session_end(self):
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        self.window.file_logger.log_session_end.assert_called_once()

    def test_closeEvent_stops_voice_output(self):
        self.window.voice_output = MagicMock()
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        self.window.voice_output.stop.assert_called_once()

    def test_closeEvent_calls_stop_wake_listener(self):
        self.window._stop_wake_listener = MagicMock()
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        self.window._stop_wake_listener.assert_called_once()

    def test_closeEvent_shuts_down_notification_manager(self):
        self.window.notification_manager = MagicMock()
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        self.window.notification_manager.shutdown.assert_called_once()
    def test_closeEvent_stops_mcp_server(self):
        # stop_http_server imported inside closeEvent from mcp_server
        with patch("mcp_server.stop_http_server") as mock_stop:
            from PyQt6.QtGui import QCloseEvent
            ev = QCloseEvent()
            self.window.closeEvent(ev)
            mock_stop.assert_called_once()

    def test_closeEvent_unhooks_keyboard(self):
        # keyboard imported inside closeEvent
        with patch("keyboard.unhook_all") as mock_unhook:
            from PyQt6.QtGui import QCloseEvent
            ev = QCloseEvent()
            self.window.closeEvent(ev)
            mock_unhook.assert_called_once()

    def test_closeEvent_git_tracker_stopped(self):
        self.window.git_tracker = MagicMock()
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        self.window.git_tracker.stop_monitoring.assert_called_once()

    def test_closeEvent_health_timer_stopped(self):
        self.window.health_timer = MagicMock()
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        self.window.health_timer.stop.assert_called_once()

    def test_closeEvent_git_refresh_timer_stopped(self):
        self.window.git_refresh_timer = MagicMock()
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        self.window.git_refresh_timer.stop.assert_called_once()

class TestCloseEventWorkersThreads(unittest.TestCase):
    """Tests for worker termination and thread joining."""

    def setUp(self):
        self.window, self.mocks = _create_dashboard()
        self.window._suppress_quit = True
        from PyQt6.QtCore import QTimer
        self.window.vram_timer = QTimer()
        self.window.cleanup_timer = QTimer()

    def _destroy_widget(self):
        """Destroy the C++ widget to prevent pytest hang at session end.

        KokertechDashboard teardown (closeEvent) calls QApplication.instance().quit(),
        putting QApplication into a "closing down" state. Without explicitly destroying
        the C++ widget via deleteLater() + processEvents(), the dangling widget object
        causes Qt to hang when the session-scoped qapp fixture is cleaned up.
        """
        try:
            self.window.deleteLater()
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                for _ in range(5):
                    app.processEvents()
        except Exception:
            pass

    def tearDown(self):
        try:
            self.window._suppress_quit = True
            self.window.close()
        except Exception:
            pass
        self._destroy_widget()
        for m in self.mocks.values():
            try:
                m.stop()
            except Exception:
                pass

    def test_closeEvent_terminates_running_worker(self):
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        self.window.worker = mock_worker
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        mock_worker.terminate.assert_called_once()
        mock_worker.wait.assert_called_once_with(2000)

    def test_closeEvent_skips_none_worker(self):
        self.window.worker = None
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        self.assertTrue(ev.isAccepted())

    def test_closeEvent_skips_not_running_worker(self):
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = False
        self.window.worker = mock_worker
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        mock_worker.terminate.assert_not_called()

    def test_closeEvent_joins_active_thread(self):
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        self.window.active_threads = [mock_thread]
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        mock_thread.join.assert_called_once_with(timeout=2.0)
        self.assertEqual(len(self.window.active_threads), 0)

    def test_closeEvent_skips_dead_thread(self):
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        self.window.active_threads = [mock_thread]
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        mock_thread.join.assert_not_called()
        self.assertEqual(len(self.window.active_threads), 0)

    def test_closeEvent_real_thread_joined(self):
        flag = threading.Event()

        def wait_on_flag():
            flag.wait(timeout=10)

        t = threading.Thread(target=wait_on_flag, daemon=True)
        t.start()
        self.window.active_threads = [t]
        flag.set()
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        self.assertFalse(t.is_alive())
        self.assertTrue(ev.isAccepted())

    def test_closeEvent_terminates_multiple_workers(self):
        w1, w2 = MagicMock(), MagicMock()
        w1.isRunning.return_value = True
        w2.isRunning.return_value = True
        self.window.desire_worker = w1
        self.window.auditor_worker = w2
        from PyQt6.QtGui import QCloseEvent
        ev = QCloseEvent()
        self.window.closeEvent(ev)
        w1.terminate.assert_called_once()
        w2.terminate.assert_called_once()

class TestCloseEventEdgeCases(unittest.TestCase):
    """Edge cases and defensive checks."""

    @staticmethod
    def _cleanup_widget(w, mocks):
        """Close the C++ widget and destroy it to prevent pytest hang at session end."""
        try:
            w._suppress_quit = True
            w.close()
        except Exception:
            pass
        try:
            w.deleteLater()
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                for _ in range(5):
                    app.processEvents()
        except Exception:
            pass
        for m in mocks.values():
            try:
                m.stop()
            except Exception:
                pass

    def test_closeEvent_safe_when_timers_missing(self):
        from PyQt6.QtGui import QCloseEvent
        w, mocks = _create_dashboard()
        for attr in ["vram_timer", "cleanup_timer", "health_timer", "git_refresh_timer"]:
            if hasattr(w, attr):
                delattr(w, attr)
        ev = QCloseEvent()
        w.closeEvent(ev)
        self.assertTrue(ev.isAccepted())
        self._cleanup_widget(w, mocks)

    def test_closeEvent_safe_when_workers_raise(self):
        from PyQt6.QtGui import QCloseEvent
        w, mocks = _create_dashboard()
        bad_worker = MagicMock()
        bad_worker.isRunning.side_effect = RuntimeError("worker deleted")
        w.worker = bad_worker
        ev = QCloseEvent()
        w.closeEvent(ev)
        self.assertTrue(ev.isAccepted())
        self._cleanup_widget(w, mocks)

    def test_closeEvent_safe_when_no_notification_manager(self):
        from PyQt6.QtGui import QCloseEvent
        w, mocks = _create_dashboard()
        if hasattr(w, "notification_manager"):
            delattr(w, "notification_manager")
        ev = QCloseEvent()
        w.closeEvent(ev)
        self.assertTrue(ev.isAccepted())
        self._cleanup_widget(w, mocks)

if __name__ == "__main__":
    unittest.main()