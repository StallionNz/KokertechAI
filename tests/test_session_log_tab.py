"""Tests for tabs/session_log_tab.py -- Session Log Statistics tab mixin."""
import unittest
from unittest.mock import patch, MagicMock

class TestSessionLogTabMixin(unittest.TestCase):
    def setUp(self):
        self._p = patch("tabs.session_log_tab.QTimer", return_value=MagicMock())
        self._p.start()
        import tabs.session_log_tab as slt
        slt._SESSION_STATS_LOADED = False
        slt._load_attempted = False
        slt._find_files = None
        slt._parse_yaml = None
        slt._aggregate = None
        slt._generate_badges = None
        slt._count_files = None
    def tearDown(self):
        self._p.stop()
    def _mk(self, obj):
        with patch("tabs.session_log_tab._load_session_stats", return_value=False):
            obj._tab = obj.create_session_log_tab()
            obj.findChild = MagicMock()

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.session_log_tab import SessionLogTabMixin
        obj = SessionLogTabMixin()
        self._mk(obj)
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "slog_table"))
        self.assertTrue(hasattr(obj, "slog_sprint_display"))
        self.assertTrue(hasattr(obj, "slog_tags_display"))
        self.assertTrue(hasattr(obj, "slog_files_display"))
        self.assertTrue(hasattr(obj, "slog_badge_area"))
        self.assertTrue(hasattr(obj, "slog_status_label"))

    def test_table_has_6_columns(self):
        from tabs.session_log_tab import SessionLogTabMixin
        obj = SessionLogTabMixin()
        self._mk(obj)
        self.assertEqual(obj.slog_table.columnCount(), 6)
        labels = [obj.slog_table.horizontalHeaderItem(i).text() for i in range(6)]
        self.assertEqual(labels, ["Session", "Date", "Duration", "Files +", "Files ~", "Focus"])

    def test_sprint_display_readonly(self):
        from tabs.session_log_tab import SessionLogTabMixin
        obj = SessionLogTabMixin()
        self._mk(obj)
        self.assertTrue(obj.slog_sprint_display.isReadOnly())

    def test_auto_badge_unchecked(self):
        from tabs.session_log_tab import SessionLogTabMixin
        obj = SessionLogTabMixin()
        self._mk(obj)
        self.assertFalse(obj.slog_auto_badge_cb.isChecked())

    def test_badge_area_hidden(self):
        from tabs.session_log_tab import SessionLogTabMixin
        obj = SessionLogTabMixin()
        self._mk(obj)
        self.assertFalse(obj.slog_badge_area.isVisible())

    def test_refresh_stats_not_available(self):
        from tabs.session_log_tab import SessionLogTabMixin
        obj = SessionLogTabMixin()
        self._mk(obj)
        with patch("tabs.session_log_tab._load_session_stats", return_value=False):
            obj._slog_refresh()
        self.assertIn("not available", obj.slog_status_label.text())

    def test_refresh_no_files(self):
        from tabs.session_log_tab import SessionLogTabMixin
        import tabs.session_log_tab as slt
        slt._SESSION_STATS_LOADED = True
        slt._find_files = lambda: []
        obj = SessionLogTabMixin()
        self._mk(obj)
        obj._slog_refresh()
        self.assertIn("No session files", obj.slog_status_label.text())

    def test_refresh_with_sessions(self):
        from tabs.session_log_tab import SessionLogTabMixin
        import tabs.session_log_tab as slt
        slt._SESSION_STATS_LOADED = True
        slt._find_files = lambda: ["f1.yaml"]
        slt._parse_yaml = lambda fp: {"session": 1, "date": "2026-07-09", "duration": "2h", "focus": "test"}
        slt._count_files = lambda s: (3, 2, [], [])
        slt._aggregate = lambda sessions: {
            "sessions": 1, "hours": 2, "date_range": ("a", "b"),
            "session_list": [{"session": 1, "date": "2026-07-09", "duration": "2h", "focus": "test"}],
            "sprint_hours": {1: 2.0}, "tag_frequency": {"test": 1},
            "created_files": [], "modified_files": [], "created": 0, "modified": 0, "changed": 0,
        }
        obj = SessionLogTabMixin()
        self._mk(obj)
        obj._slog_refresh()
        self.assertIn("1 sessions", obj.slog_status_label.text())

    def test_refresh_exception(self):
        from tabs.session_log_tab import SessionLogTabMixin
        import tabs.session_log_tab as slt
        slt._SESSION_STATS_LOADED = True
        slt._find_files = lambda: (_ for _ in ()).throw(OSError("crash"))
        obj = SessionLogTabMixin()
        self._mk(obj)
        obj._slog_refresh()
        self.assertIn("Refresh failed", obj.slog_status_label.text())

    def test_update_ui_sets_table(self):
        from tabs.session_log_tab import SessionLogTabMixin
        import tabs.session_log_tab as slt
        slt._count_files = lambda s: (1, 0, [], [])
        obj = SessionLogTabMixin()
        self._mk(obj)
        obj.findChild = MagicMock()
        obj._slog_stats = {
            "sessions": 1, "hours": 2, "created": 0, "modified": 0, "changed": 0,
            "session_list": [{"session": 1, "date": "2026-07-09", "duration": "2h", "focus": "test"}],
            "sprint_hours": {}, "tag_frequency": {},
            "created_files": [], "modified_files": [], "date_range": ("a", "b"),
        }
        obj._slog_update_ui()
        self.assertEqual(obj.slog_table.rowCount(), 1)

    def test_card_creates_widget(self):
        from PyQt6.QtWidgets import QHBoxLayout
        from tabs.session_log_tab import SessionLogTabMixin
        obj = SessionLogTabMixin()
        self._mk(obj)
        layout = QHBoxLayout()
        obj._slog_card("Test", "42", "#ff0000", layout, "test_attr")
        self.assertEqual(layout.count(), 1)

    def test_generate_badges_no_stats(self):
        from tabs.session_log_tab import SessionLogTabMixin
        obj = SessionLogTabMixin()
        self._mk(obj)
        obj._slog_stats = None
        pr = patch.object(obj, "_slog_refresh")
        pr_mock = pr.start()
        try: obj._slog_generate_badges()
        finally: pr.stop()
        pr_mock.assert_called_once()

if __name__ == "__main__":
    unittest.main()
