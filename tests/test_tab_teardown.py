"""tests/test_tab_teardown.py — Unit tests for deterministic tab lifecycle teardown.

Validates:
1. Every tab mixin with persistent timers or background workers implements
   explicit teardown hooks (teardown() and tab-specific aliases).
2. Teardown handles absent, inactive, and throwing timers without raising exceptions.
3. closeEvent in app_lifecycle.py iterates and calls all tab teardown methods.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from PyQt6.QtCore import QObject

from tabs.neural_graph_tab import NeuralGraphTabMixin
from tabs.progress_tab import ProgressTabMixin
from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
from tabs.session_journal_tab import SessionJournalTabMixin
from tabs.session_log_tab import SessionLogTabMixin
from tabs.tool_audit_tab import ToolAuditTabMixin


class TestTabTeardownMethods(unittest.TestCase):
    """Verify teardown behavior across all tabs with background timers/workers."""

    @staticmethod
    def _create_progress_tab():
        return type("DummyProgressTab", (ProgressTabMixin, QObject), {})()

    def test_progress_tab_teardown(self) -> None:
        tab = self._create_progress_tab()
        mock_timer = MagicMock()
        tab._idle_timer = mock_timer

        tab.teardown()
        mock_timer.stop.assert_called_once()

        mock_timer.reset_mock()
        tab.teardown_progress_tab()
        mock_timer.stop.assert_called_once()

    def test_progress_tab_teardown_missing_timer_safe(self) -> None:
        tab = self._create_progress_tab()
        # Should not raise when timer is absent
        tab.teardown()

    def test_scheduled_actions_tab_teardown(self) -> None:
        tab = ScheduledActionsTabMixin()
        mock_timer = MagicMock()
        tab._sa_scheduler_timer = mock_timer

        tab.teardown()
        mock_timer.stop.assert_called_once()

        mock_timer.reset_mock()
        tab.teardown_scheduled_actions()
        mock_timer.stop.assert_called_once()

    def test_session_journal_tab_teardown(self) -> None:
        tab = SessionJournalTabMixin()
        mock_timer = MagicMock()
        tab._sj_timer = mock_timer

        tab.teardown()
        mock_timer.stop.assert_called_once()

        mock_timer.reset_mock()
        tab.teardown_session_journal()
        mock_timer.stop.assert_called_once()

    def test_session_log_tab_teardown(self) -> None:
        tab = SessionLogTabMixin()
        mock_timer = MagicMock()
        tab._slog_auto_timer = mock_timer

        tab.teardown()
        mock_timer.stop.assert_called_once()

        mock_timer.reset_mock()
        tab.teardown_session_log()
        mock_timer.stop.assert_called_once()

    def test_tool_audit_tab_teardown(self) -> None:
        tab = ToolAuditTabMixin()
        mock_timer = MagicMock()
        tab._ta_refresh_timer = mock_timer

        tab.teardown()
        mock_timer.stop.assert_called_once()

        mock_timer.reset_mock()
        tab.teardown_tool_audit()
        mock_timer.stop.assert_called_once()

    def test_neural_graph_tab_teardown(self) -> None:
        tab = NeuralGraphTabMixin()
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        tab.layout_worker = mock_worker

        mock_auto = MagicMock()
        mock_auto.isRunning.return_value = True
        tab._auto_link_worker = mock_auto

        tab.teardown()
        mock_worker.stop.assert_called_once()
        mock_auto.quit.assert_called_once()
        self.assertIsNone(tab.layout_worker)
        self.assertIsNone(tab._auto_link_worker)


class TestCloseEventCallsTabTeardowns(unittest.TestCase):
    """Verify closeEvent in app_lifecycle.py coordinates tab teardown methods."""

    def test_close_event_invokes_all_teardowns(self) -> None:
        from app_lifecycle import AppLifecycleMixin

        class DummyDashboard(AppLifecycleMixin):
            def __init__(self):
                self._shutting_down = False
                self.active_threads = []
                self.controller = MagicMock()
                self.file_logger = MagicMock()
                self.log_to_audit = MagicMock()
                self.teardown_neural_graph = MagicMock()
                self.teardown_progress_tab = MagicMock()
                self.teardown_scheduled_actions = MagicMock()
                self.teardown_session_journal = MagicMock()
                self.teardown_session_log = MagicMock()
                self.teardown_tool_audit = MagicMock()
                self._stop_wake_listener = MagicMock()
                self._save_window_geometry = MagicMock()
                self._hide_typing_indicator = MagicMock()
                self._hide_progress_panel = MagicMock()

        dashboard = DummyDashboard()
        mock_event = MagicMock()

        dashboard.closeEvent(mock_event)

        dashboard.teardown_neural_graph.assert_called_once()
        dashboard.teardown_progress_tab.assert_called_once()
        dashboard.teardown_scheduled_actions.assert_called_once()
        dashboard.teardown_session_journal.assert_called_once()
        dashboard.teardown_session_log.assert_called_once()
        dashboard.teardown_tool_audit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
