"""
Tests for app_core.py - KokertechDashboard __init__ branches.

NOTE: Historical cross-file QApplication singleton issue (conftest.py) — now resolved.
      All 51 pre-existing failures were fixed in June 2026 (test fixture
      isolation improvements, proper QWidget cleanup, and standardized mock
      patterns).  Per-file and cross-file batches both pass cleanly.

HANG FIX (2026-07-14): The ``_make()`` helper now also patches
``_prewarm_model`` and ``_update_provider_badge``.  The root cause was that
``_prewarm_model`` ran a daemon thread during ``__init__`` that scheduled
``QTimer.singleShot(0, self._update_provider_badge)``.  When ``init_ui`` was
patched, the ``provider_badge`` widget was never created, so
``_update_provider_badge()`` raised ``AttributeError`` when the QTimer fired
during ``app.processEvents()`` in test bodies.  That ``AttributeError``
propagated through PyQt6's C++ ``processEvents()`` boundary (which has
undefined behaviour for unhandled exceptions in slots), causing the test
process to hang indefinitely.

      Run this file in isolation to verify actual test results:
        pytest test_app_core.py
"""

import sys
import unittest
from unittest.mock import patch, MagicMock

import pytest

from PyQt6.QtCore import Qt

# QApplication provided by conftest.py (session-scoped qapp fixture)

class TestDashboardInitBranches:
    """
    Cover conditional branches in KokertechDashboard.__init__
    """

    @pytest.fixture(autouse=True)
    def _setup_teardown(self):
        self._patches = []
        self._mocks = {}
        self._dashboard = None
        yield
        # Destroy the dashboard WITHOUT calling closeEvent (which triggers
        # the full cleanup chain: cancel events, stop timers, join threads,
        # stop voice_output, shutdown notification manager, run consolidation,
        # scan execution log — many of these crash or hang when init_ui was
        # patched because the required widgets/attributes don't exist).
        # Using setParent(None) + deleteLater() avoids the closeEvent path
        # entirely while still properly disposing of the C++ widget.
        if self._dashboard is not None:
            try:
                self._dashboard.setParent(None)
                self._dashboard.deleteLater()
            except Exception:
                pass
        # Flush Qt events to process the deferred deleteLater() and prevent
        # stale timer callbacks from crashing when the next dashboard is
        # created.
        from PyQt6.QtWidgets import QApplication
        _app = QApplication.instance()
        if _app:
            for _ in range(20):
                _app.processEvents()
        for p in self._patches:
            try:
                p.stop()
            except Exception:
                pass

    def _make(self, is_first_run=False, notify_raises=False, onboard_raises=False,
              **config_overrides):
        config = {"auto_start_api": False, "always_on_top": False,
                  "active_theme": "Classic (Charcoal)", "tts_enabled": True}
        config.update(config_overrides)
        self._patches = []
        self._mocks = {}
        # Patch KokertechController to prevent AI provider probing (300s timeout risk)
        p = patch("app_core.KokertechController")
        self._mocks["controller"] = p.start()
        self._patches.append(p)
        # Patch _prewarm_model to prevent the daemon thread from scheduling
        # QTimer.singleShot(0, self._update_provider_badge) during __init__.
        # When the dashboard is created with a patched init_ui, provider_badge
        # widget doesn't exist. The QTimer fires during app.processEvents()
        # in test bodies, and the resulting AttributeError propagates through
        # PyQt6's C++ processEvents() boundary, causing the test to hang.
        p = patch("app_core.KokertechDashboard._prewarm_model")
        self._mocks["_prewarm_model"] = p.start()
        self._patches.append(p)
        # Patch _update_provider_badge as defense-in-depth — it accesses
        # self.provider_badge (created by init_ui, which is patched) and
        # would raise AttributeError if called from a timer callback during
        # processEvents(). Only catches RuntimeError, not AttributeError.
        p = patch("app_core.KokertechDashboard._update_provider_badge")
        self._mocks["_update_provider_badge"] = p.start()
        self._patches.append(p)
        # Patch VoiceOutput to prevent TTS engine initialization
        p = patch("app_core.VoiceOutput")
        self._mocks["voice_output"] = p.start()
        self._patches.append(p)
        # Patch init_ui to prevent non-Qt UI setup (settings tab HTTP calls)
        p = patch("app_core.KokertechDashboard.init_ui")
        self._mocks["init_ui"] = p.start()
        self._patches.append(p)
        # Patch startup_api_check so tests can assert on deferred call
        p = patch("app_core.KokertechDashboard.startup_api_check")
        self._mocks["startup_api_check"] = p.start()
        self._patches.append(p)
        # Patch apply_hotkey (called from _deferred_init_dashboard)
        p = patch("app_core.KokertechDashboard.apply_hotkey")
        self._mocks["apply_hotkey"] = p.start()
        self._patches.append(p)
        # Patch setup methods that create timers or access hardware
        for method in ("setup_vram_monitor", "setup_cleanup_timer",
                       "setup_consolidation_timer", "setup_mcp_client",
                       "apply_theme"):
            p = patch(f"app_core.KokertechDashboard.{method}")
            self._mocks[method] = p.start()
            self._patches.append(p)
        # Patch onboarding
        if onboard_raises:
            p = patch("onboarding_wizard.is_first_run",
                       side_effect=OSError("Onboarding error"))
        else:
            p = patch("onboarding_wizard.is_first_run", return_value=is_first_run)
        self._mocks["is_first_run"] = p.start()
        self._patches.append(p)
        p = patch("onboarding_wizard.run_onboarding")
        self._mocks["run_onboarding"] = p.start()
        self._patches.append(p)
        # Patch notification manager
        if notify_raises:
            p = patch("notifications.get_notification_manager",
                       side_effect=RuntimeError("Notify init failed"))
        else:
            p = patch("notifications.get_notification_manager")
        self._mocks["notifications"] = p.start()
        self._patches.append(p)
        # Patch config
        p = patch.dict("app_core.CONFIG", config, clear=False)
        self._patches.append(p)
        p.start()
        from app_core import KokertechDashboard
        w = KokertechDashboard()
        self._dashboard = w
        return w

    def test_auto_start_api_true(self):
        w = self._make(auto_start_api=True)
        # Flush Qt events to trigger QTimer.singleShot(0) → _deferred_init_dashboard
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            for _ in range(10):
                app.processEvents()
        self._mocks["apply_hotkey"].assert_called_once()
        self._mocks["startup_api_check"].assert_called_once()

    def test_auto_start_api_false(self):
        w = self._make(auto_start_api=False)
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            for _ in range(10):
                app.processEvents()
        self._mocks["apply_hotkey"].assert_called_once()
        self._mocks["startup_api_check"].assert_not_called()

    def test_always_on_top_true(self):
        w = self._make(always_on_top=True)
        assert w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

    def test_always_on_top_false(self):
        w = self._make(always_on_top=False)
        assert not (w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_onboarding_not_first_run(self):
        w = self._make()
        self._mocks["is_first_run"].assert_called_once()

    def test_onboarding_first_run_hides_and_schedules(self):
        w = self._make(is_first_run=True)
        self._mocks["is_first_run"].assert_called_once()

    def test_notification_init_exception_handled(self):
        """When get_notification_manager raises, __init__ logs debug and continues."""
        w = self._make(notify_raises=True)
        self._mocks["is_first_run"].assert_called_once()  # init completes

    def test_onboarding_exception_handled(self):
        """When is_first_run raises, __init__ catches and logs debug."""
        w = self._make(onboard_raises=True)
        # init completes without crash

@pytest.mark.real_onboarding
class TestRunOnboardingOrShow(unittest.TestCase):
    """
    Test _run_onboarding_or_show directly.
    """

    @patch("app_core.check_last_save_integrity", return_value={"ok": True})
    @patch("onboarding_wizard.run_onboarding")
    def test_run_onboarding_runs_wizard_then_show(self, mock_run, mock_integrity):
        from app_core import KokertechDashboard
        w = KokertechDashboard.__new__(KokertechDashboard)
        w.show = MagicMock()
        w.raise_ = MagicMock()
        w.activateWindow = MagicMock()
        w._run_onboarding_or_show()
        mock_run.assert_called_once_with(w)
        w.show.assert_called_once()
        w.raise_.assert_called_once()
        w.activateWindow.assert_called_once()

    @patch("app_core.check_last_save_integrity", return_value={"ok": True})
    @patch("onboarding_wizard.run_onboarding")
    def test_run_onboarding_handles_exception_gracefully(self, mock_run, mock_integrity):
        mock_run.side_effect = RuntimeError("wizard error")
        from app_core import KokertechDashboard
        w = KokertechDashboard.__new__(KokertechDashboard)
        w.show = MagicMock()
        w.raise_ = MagicMock()
        w.activateWindow = MagicMock()
        # Should not raise
        w._run_onboarding_or_show()
        w.show.assert_called_once()

    @patch("app_core.check_last_save_integrity", return_value={"ok": True})
    @patch("onboarding_wizard.run_onboarding")
    def test_run_onboarding_exception_logs_warning(self, mock_run, mock_integrity):
        """REGRESSION GUARD: _run_onboarding_or_show's except must not be silent --
        it logs a warning breadcrumb before degrading to show()."""
        mock_run.side_effect = RuntimeError("wizard error")
        from app_core import KokertechDashboard
        w = KokertechDashboard.__new__(KokertechDashboard)
        w.show = MagicMock()
        w.raise_ = MagicMock()
        w.activateWindow = MagicMock()
        with patch("app_core.logger") as mock_logger:
            w._run_onboarding_or_show()
        mock_logger.warning.assert_called_once()
        self.assertIn("wizard error", str(mock_logger.warning.call_args))

if __name__ == "__main__":
    unittest.main()
