"""Tests for tabs/scheduled_actions_tab.py -- Scheduled Actions tab mixin."""
import unittest
from unittest.mock import patch, MagicMock

class TestScheduledActionsTabMixin(unittest.TestCase):
    def setUp(self):
        self._p1 = patch("tabs.scheduled_actions_tab.QTimer", return_value=MagicMock())
        self._p1.start()
        self._p2 = patch("tabs.scheduled_actions_tab.sqlite3")
        self._mock_sqlite = self._p2.start()
        self._mock_sqlite.Row = dict
        import sqlite3 as _real_sqlite3
        self._mock_sqlite.OperationalError = _real_sqlite3.OperationalError
    def tearDown(self):
        self._p1.stop()
        self._p2.stop()
    def _mk(self, obj):
        with patch.object(obj, "_sa_refresh_tasks"), patch.object(obj, "_sa_ensure_table"), patch.object(obj, "_sa_populate_action_combo"):
            obj._tab = obj.create_scheduled_actions_tab()
    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "_sa_table"))
        self.assertTrue(hasattr(obj, "_sa_name_input"))
        self.assertTrue(hasattr(obj, "_sa_action_combo"))
        self.assertTrue(hasattr(obj, "_sa_interval_spin"))
        self.assertTrue(hasattr(obj, "_sa_stats_label"))
    def test_table_has_7_columns(self):
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        self.assertEqual(obj._sa_table.columnCount(), 7)
        labels = [obj._sa_table.horizontalHeaderItem(i).text() for i in range(7)]
        self.assertEqual(labels, ["Name", "Action", "Interval", "Last Run", "Next Run", "Count", "Status"])
    def test_interval_spin_defaults(self):
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        self.assertEqual(obj._sa_interval_spin.minimum(), 10)
        self.assertEqual(obj._sa_interval_spin.maximum(), 86400)
        self.assertEqual(obj._sa_interval_spin.value(), 3600)
    def test_fmt_interval_seconds(self):
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        self.assertEqual(ScheduledActionsTabMixin._sa_fmt_interval(30), "30s")
        self.assertEqual(ScheduledActionsTabMixin._sa_fmt_interval(90), "1m")
        self.assertEqual(ScheduledActionsTabMixin._sa_fmt_interval(3600), "1h")
        self.assertEqual(ScheduledActionsTabMixin._sa_fmt_interval(86400), "1d")
    def test_status_label_shows_active(self):
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        self.assertIn("Active", obj._sa_status_label.text())
        self.assertIn("30s", obj._sa_status_label.text())
    def test_load_tasks_returns_list(self):
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "name": "Test", "action": "search_web", "params": "{}",
             "interval_seconds": 60, "last_run": "2026-07-09 10:00",
             "next_run": "2026-07-09 10:01", "enabled": 1, "run_count": 5, "last_result": "ok"},
        ]
        mock_conn.execute.return_value = mock_cursor
        self._mock_sqlite.connect.return_value = mock_conn
        tasks = obj._sa_load_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["name"], "Test")
        self.assertEqual(tasks[0]["action"], "search_web")
        mock_conn.close.assert_called_once()
    def test_load_tasks_handles_error(self):
        import sqlite3
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        self._mock_sqlite.connect.side_effect = sqlite3.OperationalError("DB locked")
        tasks = obj._sa_load_tasks()
        self.assertEqual(tasks, [])
    def test_refresh_tasks_populates_table(self):
        from PyQt6.QtCore import Qt
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        mock_tasks = [
            {"id": 1, "name": "Daily Search", "action": "search_web",
             "interval_seconds": 3600, "last_run": "2026-07-09 10:00",
             "next_run": "2026-07-09 11:00", "enabled": 1, "run_count": 3, "last_result": ""},
            {"id": 2, "name": "Paused Task", "action": "write_file",
             "interval_seconds": 60, "last_run": "", "next_run": "",
             "enabled": 0, "run_count": 0, "last_result": ""},
        ]
        with patch.object(obj, "_sa_load_tasks", return_value=mock_tasks):
            obj._sa_refresh_tasks()
        self.assertEqual(obj._sa_table.rowCount(), 2)
        self.assertEqual(obj._sa_table.item(0, 0).text(), "Daily Search")
        self.assertEqual(obj._sa_table.item(0, 1).text(), "search_web")
        self.assertEqual(obj._sa_table.item(0, 2).text(), "1h")
        self.assertIn("Total tasks: 2", obj._sa_stats_label.text())
        self.assertIn("Enabled: 1", obj._sa_stats_label.text())
    def test_refresh_tasks_empty_list(self):
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        with patch.object(obj, "_sa_load_tasks", return_value=[]):
            obj._sa_refresh_tasks()
        self.assertEqual(obj._sa_table.rowCount(), 0)
        self.assertIn("Total tasks: 0", obj._sa_stats_label.text())
    def test_add_task_missing_name_shows_warning(self):
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        obj._sa_name_input.setText("")
        with patch("tabs.scheduled_actions_tab.QMessageBox") as mock_mb:
            obj._sa_add_task()
        mock_mb.warning.assert_called_once()
    def test_add_task_with_valid_inputs(self):
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        obj._sa_name_input.setText("My Task")
        obj._sa_action_combo.addItem("Search (search_web)", "search_web")
        obj._sa_action_combo.setCurrentIndex(0)
        obj._sa_interval_spin.setValue(120)
        mock_conn = MagicMock()
        self._mock_sqlite.connect.return_value = mock_conn
        with patch("tabs.scheduled_actions_tab.QMessageBox"), patch.object(obj, "_sa_refresh_tasks"):
            obj._sa_add_task()
        mock_conn.execute.assert_called_once()
        self.assertEqual(obj._sa_name_input.text(), "")
        obj.log_to_audit.assert_called_once()
    def test_check_due_tasks_skips_when_shutting_down(self):
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        obj._shutting_down = True
        with patch.object(obj, "_sa_load_tasks") as mock_load:
            obj._sa_check_due_tasks()
        mock_load.assert_not_called()
    def test_check_due_tasks_no_due_tasks(self):
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        with patch.object(obj, "_sa_load_tasks", return_value=[]):
            obj._sa_check_due_tasks()
    def test_populate_action_combo_with_registry(self):
        from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
        obj = ScheduledActionsTabMixin()
        self._mk(obj)
        mock_pr = MagicMock()
        mock_pr.plugins = {"search_web": {}, "write_file": {}}
        mock_pr.is_enabled.return_value = True
        mock_pr.metadata = {"search_web": {"name": "Search Web"}, "write_file": {"name": "Write File"}}
        with patch("plugin_registry.registry", mock_pr):
            obj._sa_populate_action_combo()
        self.assertEqual(obj._sa_action_combo.count(), 2)
        self.assertIn("Search Web", obj._sa_action_combo.itemText(0))

if __name__ == "__main__":
    unittest.main()
