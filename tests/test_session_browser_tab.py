"""Tests for tabs/session_browser_tab.py -- Session Browser tab mixin."""
import sqlite3
import unittest
from unittest.mock import patch, MagicMock

class TestSessionBrowserTabMixin(unittest.TestCase):
    def setUp(self):
        self._p = patch("tabs.session_browser_tab.QTimer", return_value=MagicMock())
        self._p.start()
    def tearDown(self):
        self._p.stop()
    def _mk(self, obj):
        with patch.object(obj, "_session_browser_refresh"):
            obj._tab = obj.create_session_browser_tab()

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "session_table"))
        self.assertTrue(hasattr(obj, "session_search_input"))
        self.assertTrue(hasattr(obj, "session_entries_list"))
        self.assertTrue(hasattr(obj, "session_entry_preview"))
        self.assertTrue(hasattr(obj, "entry_importance_btn"))
        self.assertTrue(hasattr(obj, "entry_delete_btn"))
        self.assertTrue(hasattr(obj, "session_delete_btn"))

    def test_session_table_has_5_columns(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        self.assertEqual(obj.session_table.columnCount(), 5)
        labels = [obj.session_table.horizontalHeaderItem(i).text() for i in range(5)]
        self.assertEqual(labels, ["Session ID", "Start Time", "End Time", "Entries", "Summary"])

    def test_entry_preview_is_readonly(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        self.assertTrue(obj.session_entry_preview.isReadOnly())

    def test_buttons_disabled_initially(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        self.assertFalse(obj.entry_importance_btn.isEnabled())
        self.assertFalse(obj.entry_delete_btn.isEnabled())
        self.assertFalse(obj.session_delete_btn.isEnabled())

    def test_refresh_loads_sessions(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        s = dict(session_id="sess-001", start_time="2026-07-01 10:00", end_time="2026-07-01 12:00", entry_count=5, summary="Work")
        s2 = dict(session_id="sess-002", start_time="2026-07-02 09:00", end_time="2026-07-02 11:00", entry_count=3, summary="Bugs")
        pv = patch("memory_vault.list_recent_sessions", return_value=[s, s2])
        pv.start()
        try: obj._session_browser_refresh()
        finally: pv.stop()
        self.assertEqual(obj.session_table.rowCount(), 2)
        self.assertIn(obj.session_table.item(0, 0).text(), ["sess-001", "sess-002"])

    def test_refresh_handles_error(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        pv = patch("memory_vault.list_recent_sessions", side_effect=sqlite3.Error("DB error"))
        pv.start()
        try: obj._session_browser_refresh()
        finally: pv.stop()
        obj.log_to_audit.assert_called_once()
        self.assertIn("failed", obj.log_to_audit.call_args[0][0])

    def test_new_session_calls_vault(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        pv = patch("memory_vault.start_new_session", return_value="new-sess-id")
        pr = patch.object(obj, "_session_browser_refresh")
        pv.start(); pr_mock = pr.start()
        try: obj._session_browser_new_session()
        finally: pv.stop(); pr.stop()
        obj.log_to_audit.assert_called_once()
        self.assertIn("new-sess-id", obj.log_to_audit.call_args[0][0])
        pr_mock.assert_called_once()

    def test_filter_by_search_text(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        s1 = dict(session_id="abc-def", start_time="", end_time="", entry_count=1, summary="Alpha work")
        s2 = dict(session_id="xyz-123", start_time="", end_time="", entry_count=1, summary="Beta testing")
        obj._session_sessions_cache = [s1, s2]
        obj.session_search_input.setText("alpha")
        obj._session_browser_filter()
        self.assertEqual(obj.session_table.rowCount(), 1)
        self.assertEqual(obj.session_table.item(0, 0).text(), "abc-def")

    def test_filter_empty_shows_all(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        s = dict(session_id="s1", start_time="", end_time="", entry_count=1, summary="")
        obj._session_sessions_cache = [s, s]
        obj.session_search_input.setText("")
        obj._session_browser_filter()
        self.assertEqual(obj.session_table.rowCount(), 2)

    def test_render_table_sets_count(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        s = dict(session_id="s1", start_time="now", end_time="later", entry_count=2, summary="Test")
        obj._session_render_table([s])
        self.assertIn("1 session", obj.session_count_label.text())

    def test_session_selected_loads_entries(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        from PyQt6.QtWidgets import QTableWidgetItem
        obj = SessionBrowserTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        item0 = QTableWidgetItem("sess-test")
        obj.session_table.setRowCount(1)
        obj.session_table.setItem(0, 0, item0)
        obj.session_table.selectRow(0)
        e = dict(id=1, timestamp="10:00", importance_score=5, summary="Entry one", tags="[]")
        pv = patch("memory_vault.get_recent_episodic", return_value=[e])
        pv.start()
        try: obj._session_browser_on_session_selected()
        finally: pv.stop()
        self.assertTrue(obj.session_delete_btn.isEnabled())
        self.assertEqual(obj.session_entries_list.count(), 1)
    def test_session_selected_empty_clears(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        obj._session_browser_on_session_selected()
        self.assertFalse(obj.session_delete_btn.isEnabled())
        self.assertEqual(obj.session_entries_list.count(), 0)

    def test_render_entries_populates(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        obj._session_selected_session_id = chr(34)+chr(115)+chr(101)+chr(115)+chr(115)+chr(45)+chr(49)+chr(34)
        e1 = dict(id=1, timestamp=chr(34)+chr(49)+chr(48)+chr(58)+chr(48)+chr(48)+chr(34), importance_score=5, summary=chr(34)+chr(69)+chr(110)+chr(116)+chr(114)+chr(121)+chr(32)+chr(111)+chr(110)+chr(101)+chr(34), tags=chr(34)+chr(91)+chr(93)+chr(34))
        e2 = dict(id=2, timestamp=chr(34)+chr(49)+chr(48)+chr(58)+chr(48)+chr(53)+chr(34), importance_score=3, summary=chr(34)+chr(69)+chr(110)+chr(116)+chr(114)+chr(121)+chr(32)+chr(116)+chr(119)+chr(111)+chr(34), tags=chr(34)+chr(91)+chr(92)+chr(34)+chr(114)+chr(97)+chr(103)+chr(92)+chr(34)+chr(93)+chr(34))
        obj._session_render_entries([e1, e2])
        self.assertEqual(obj.session_entries_list.count(), 2)
    def test_render_entries_populates(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        obj._session_selected_session_id = "sess-1"
        e1 = {"id": 1, "timestamp": "10:00", "importance_score": 5, "summary": "Entry one", "tags": "[]"}
        e2 = {"id": 2, "timestamp": "10:05", "importance_score": 3, "summary": "Entry two", "tags": '["rag"]'}
        obj._session_render_entries([e1, e2])
        self.assertEqual(obj.session_entries_list.count(), 2)
        self.assertIn("sess-1", obj.session_entries_label.text())
        self.assertIn("2 entr", obj.entry_count_label.text())

    def test_render_entries_empty(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        obj._session_selected_session_id = "sess-2"
        obj._session_render_entries([])
        self.assertEqual(obj.session_entries_list.count(), 0)
        self.assertIn("0 entries", obj.entry_count_label.text())

    def test_entry_selected_shows_preview(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        obj._session_entries_cache = [{"id": 1, "timestamp": "10:00", "importance_score": 5, "summary": "Full entry text", "tags": "[]"}]
        obj._session_render_entries(obj._session_entries_cache)
        obj.session_entries_list.setCurrentRow(0)
        preview = obj.session_entry_preview.toPlainText()
        self.assertIn("Full entry text", preview)
        self.assertIn("Importance: 5", preview)
        self.assertTrue(obj.entry_importance_btn.isEnabled())
        self.assertTrue(obj.entry_delete_btn.isEnabled())

    def test_boost_importance(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        obj._session_selected_entry_id = 1
        pv = patch("memory_vault.boost_entry_importance", return_value=6)
        pr = patch.object(obj, "_session_browser_on_session_selected")
        pv.start(); pr.start()
        try: obj._session_browser_boost_importance()
        finally: pv.stop(); pr.stop()
        obj.log_to_audit.assert_called_once()
        self.assertIn("Boosted", obj.log_to_audit.call_args[0][0])

    def test_delete_entry_yes(self):
        from PyQt6.QtWidgets import QMessageBox
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        obj._session_selected_entry_id = 1
        pv = patch("memory_vault.delete_entry", return_value=True)
        mb = patch("tabs.session_browser_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes)
        pr = patch.object(obj, "_session_browser_on_session_selected")
        pv.start(); mb.start(); pr.start()
        try: obj._session_browser_delete_entry()
        finally: pv.stop(); mb.stop(); pr.stop()
        obj.log_to_audit.assert_called_once()

    def test_delete_entry_no(self):
        from PyQt6.QtWidgets import QMessageBox
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        obj._session_selected_entry_id = 1
        md = patch("memory_vault.delete_entry")
        mb = patch("tabs.session_browser_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.No)
        pv = md.start(); mb.start()
        try: obj._session_browser_delete_entry()
        finally: pv.stop(); mb.stop()
        pv.assert_not_called()

    def test_delete_session_yes(self):
        from PyQt6.QtWidgets import QMessageBox
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        obj._session_selected_session_id = "sess-del"
        pv = patch("memory_vault.delete_session", return_value=True)
        mb = patch("tabs.session_browser_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes)
        pr = patch.object(obj, "_session_browser_refresh")
        pv.start(); mb.start(); pr.start()
        try: obj._session_browser_delete_session()
        finally: pv.stop(); mb.stop(); pr.stop()
        obj.log_to_audit.assert_called_once()

    def test_delete_session_no_selection(self):
        from tabs.session_browser_tab import SessionBrowserTabMixin
        obj = SessionBrowserTabMixin()
        self._mk(obj)
        obj._session_selected_session_id = None
        md = patch("memory_vault.delete_session")
        pv = md.start()
        try: obj._session_browser_delete_session()
        finally: pv.stop()
        pv.assert_not_called()

if __name__ == "__main__":
    unittest.main()
