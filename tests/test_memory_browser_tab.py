"""Tests for tabs/memory_browser_tab.py -- Memory Browser tab mixin."""
import sqlite3
import unittest
from unittest.mock import patch, MagicMock

class TestMemoryBrowserTabMixin(unittest.TestCase):
    def setUp(self):
        self._p = patch("tabs.memory_browser_tab.QTimer", return_value=MagicMock())
        self._p.start()
    def tearDown(self):
        self._p.stop()
    def _mk(self, obj):
        with patch.object(obj, "_refresh_memory_browser"):
            obj._tab = obj.create_memory_browser_tab()

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "episodic_table"))
        self.assertTrue(hasattr(obj, "core_table"))
        self.assertTrue(hasattr(obj, "mem_search_input"))
        self.assertTrue(hasattr(obj, "mem_editor"))
        self.assertTrue(hasattr(obj, "mem_btn_save"))
        self.assertTrue(hasattr(obj, "mem_btn_boost"))
        self.assertTrue(hasattr(obj, "mem_btn_delete"))

    def test_source_tabs_have_2_tabs(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        self.assertEqual(obj.mem_source_tabs.count(), 2)
        self.assertIn("Episodic", obj.mem_source_tabs.tabText(0))
        self.assertIn("Core", obj.mem_source_tabs.tabText(1))

    def test_episodic_table_has_6_columns(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        self.assertEqual(obj.episodic_table.columnCount(), 6)
        labels = [obj.episodic_table.horizontalHeaderItem(i).text()
                  for i in range(6)]
        self.assertEqual(labels, ["ID", "Timestamp", "Summary", "Imp.", "Tags", "Session"])

    def test_core_table_has_5_columns(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        self.assertEqual(obj.core_table.columnCount(), 5)
        labels = [obj.core_table.horizontalHeaderItem(i).text()
                  for i in range(5)]
        self.assertEqual(labels, ["ID", "Timestamp", "Content", "Type", "Imp."])

    def test_buttons_disabled_initially(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        self.assertFalse(obj.mem_btn_save.isEnabled())
        self.assertFalse(obj.mem_btn_boost.isEnabled())
        self.assertFalse(obj.mem_btn_delete.isEnabled())

    def test_boost_spin_defaults(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        self.assertEqual(obj.mem_boost_spin.minimum(), 1)
        self.assertEqual(obj.mem_boost_spin.maximum(), 5)
        self.assertEqual(obj.mem_boost_spin.value(), 1)

    def test_refresh_loads_episodic_entries(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        entries = [
            {"id": 1, "timestamp": "2026-07-09 10:00",
             "summary": "Test summary", "importance_score": 7,
             "tags": '["rag", "test"]', "session_id": "sess-001"},
            {"id": 2, "timestamp": "2026-07-09 11:00",
             "summary": "Another", "importance_score": 3,
             "tags": '["note"]', "session_id": "sess-002"},
        ]
        pv = patch("memory_vault.get_recent_episodic",
                   return_value=entries)
        pc = patch("memory_vault.get_all_core_memories",
                   return_value=[])
        pv.start(); pc.start()
        try:
            obj._refresh_memory_browser()
        finally:
            pv.stop(); pc.stop()
        self.assertEqual(obj.episodic_table.rowCount(), 2)

    def test_refresh_loads_core_memories(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        memories = [
            {"id": 1, "timestamp": "2026-07-09", "content": "Core content",
             "node_type": "fact", "importance_score": 8, "tags": "[]"},
        ]
        pv = patch("memory_vault.get_recent_episodic",
                   return_value=[])
        pc = patch("memory_vault.get_all_core_memories",
                   return_value=memories)
        pv.start(); pc.start()
        try:
            obj._refresh_memory_browser()
        finally:
            pv.stop(); pc.stop()
        self.assertEqual(obj.core_table.rowCount(), 1)
        self.assertEqual(obj.core_table.item(0, 2).text()[:12], "Core content")

    def test_refresh_stats_label(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        pv = patch("memory_vault.get_recent_episodic",
                   return_value=[
            {"id": 1, "timestamp": "", "summary": "", "importance_score": 0,
             "tags": "[]", "session_id": ""},
        ])
        pc = patch("memory_vault.get_all_core_memories",
                   return_value=[
            {"id": 1, "timestamp": "", "content": "", "node_type": "",
             "importance_score": 0, "tags": "[]"},
            {"id": 2, "timestamp": "", "content": "", "node_type": "",
             "importance_score": 0, "tags": "[]"},
        ])
        pv.start(); pc.start()
        try:
            obj._refresh_memory_browser()
        finally:
            pv.stop(); pc.stop()
        self.assertIn("1 episodic", obj.mem_stats_label.text())
        self.assertIn("2 core memories", obj.mem_stats_label.text())

    def test_refresh_handles_errors(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        pv = patch("memory_vault.get_recent_episodic",
                   side_effect=sqlite3.Error("fail"))
        pc = patch("memory_vault.get_all_core_memories",
                   side_effect=sqlite3.Error("fail"))
        pv.start(); pc.start()
        try:
            obj._refresh_memory_browser()
        finally:
            pv.stop(); pc.stop()
        self.assertIn("Error", obj.episodic_table.item(0, 0).text())
        self.assertIn("Error", obj.core_table.item(0, 0).text())

    def test_source_changed_clears_detail(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        obj.mem_editor.setPlainText("Some text")
        obj.mem_meta_label.setText("Some meta")
        obj.mem_btn_save.setEnabled(True)
        obj._on_mem_source_changed(1)
        self.assertEqual(obj.mem_editor.toPlainText(), "")
        self.assertFalse(obj.mem_btn_save.isEnabled())

    def test_selection_changed_episodic_shows_detail(self):
        from PyQt6.QtCore import Qt
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        from PyQt6.QtWidgets import QTableWidgetItem
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        obj.mem_source_tabs.setCurrentIndex(0)
        item0 = QTableWidgetItem("1")
        item0.setData(Qt.ItemDataRole.UserRole, {
            "id": 1, "source": "episodic", "full_content": "Test content",
            "importance": 5, "tags": ["rag"], "session_id": "s1",
            "timestamp": "2026-07-09 10:00",
        })
        obj.episodic_table.setRowCount(1)
        obj.episodic_table.setItem(0, 0, item0)
        obj.episodic_table.selectRow(0)
        obj._on_mem_table_selection_changed()
        self.assertEqual(obj.mem_editor.toPlainText(), "Test content")
        self.assertIn("ID: 1", obj.mem_meta_label.text())
        self.assertTrue(obj.mem_btn_save.isEnabled())

    def test_selection_changed_core_shows_detail(self):
        from PyQt6.QtCore import Qt
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        from PyQt6.QtWidgets import QTableWidgetItem
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        obj.mem_source_tabs.setCurrentIndex(1)
        item0 = QTableWidgetItem("1")
        item0.setData(Qt.ItemDataRole.UserRole, {
            "id": 1, "source": "core", "full_content": "Core content",
            "importance": 8, "node_type": "fact", "tags": [],
            "timestamp": "2026-07-09",
        })
        obj.core_table.setRowCount(1)
        obj.core_table.setItem(0, 0, item0)
        obj.core_table.selectRow(0)
        obj._on_mem_table_selection_changed()
        self.assertEqual(obj.mem_editor.toPlainText(), "Core content")
        self.assertIn("CORE", obj.mem_meta_label.text())

    def test_clear_detail_resets_panel(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        obj.mem_editor.setPlainText("Some text")
        obj.mem_btn_save.setEnabled(True)
        obj._clear_mem_detail()
        self.assertEqual(obj.mem_meta_label.text(), "Select an entry to view and edit")
        self.assertEqual(obj.mem_editor.toPlainText(), "")
        self.assertFalse(obj.mem_btn_save.isEnabled())

    def test_save_changes_calls_vault(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        obj._current_mem_id = 1
        obj._current_mem_source = "episodic"
        obj.mem_editor.setPlainText("Updated content")
        pv = patch("memory_vault.update_entry_content",
                   return_value=True)
        pv.start()
        try:
            with patch.object(obj, "_refresh_memory_browser"):
                obj._mem_save_changes()
        finally:
            pv.stop()
        obj.log_to_audit.assert_called_once()

    def test_save_changes_empty_content_warns(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        obj._current_mem_id = 1
        obj._current_mem_source = "episodic"
        obj.mem_editor.setPlainText("   ")
        with patch("tabs.memory_browser_tab.QMessageBox") as mock_mb:
            obj._mem_save_changes()
        mock_mb.warning.assert_called_once()

    def test_boost_importance_calls_vault(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        obj._current_mem_id = 1
        obj._current_mem_source = "episodic"
        pv = patch("memory_vault.boost_entry_importance",
                   return_value=8)
        pv.start()
        try:
            with patch.object(obj, "_refresh_memory_browser"):
                obj._mem_boost_importance()
        finally:
            pv.stop()
        obj.log_to_audit.assert_called_once()
        self.assertIn("boosted to 8", obj.log_to_audit.call_args[0][0])

    def test_boost_importance_non_episodic_warns(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        obj._current_mem_id = 1
        obj._current_mem_source = "core"
        with patch("tabs.memory_browser_tab.QMessageBox") as mock_mb:
            obj._mem_boost_importance()
        mock_mb.information.assert_called_once()

    def test_delete_entry_confirm_yes_calls_vault(self):
        from PyQt6.QtWidgets import QMessageBox
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        obj._current_mem_id = 1
        obj._current_mem_source = "episodic"
        pv = patch("memory_vault.delete_entry",
                   return_value=True)
        mb = patch("tabs.memory_browser_tab.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes)
        pv.start(); mb.start()
        try:
            with patch.object(obj, "_refresh_memory_browser"):
                obj._mem_delete_entry()
        finally:
            pv.stop(); mb.stop()
        obj.log_to_audit.assert_called_once()

    def test_delete_entry_confirm_no_skips(self):
        from PyQt6.QtWidgets import QMessageBox
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        obj._current_mem_id = 1
        obj._current_mem_source = "episodic"
        pv = patch("memory_vault.delete_entry")
        mb = patch("tabs.memory_browser_tab.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.No)
        mock_delete = pv.start()
        mb.start()
        try:
            obj._mem_delete_entry()
        finally:
            pv.stop()
            mb.stop()
        mock_delete.assert_not_called()

    def test_filter_hides_non_matching_rows(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        from PyQt6.QtWidgets import QTableWidgetItem
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        obj.mem_source_tabs.setCurrentIndex(0)
        obj.episodic_table.setRowCount(2)
        obj.episodic_table.setItem(0, 0, QTableWidgetItem("1"))
        obj.episodic_table.setItem(0, 2, QTableWidgetItem("hello world"))
        obj.episodic_table.setItem(1, 0, QTableWidgetItem("2"))
        obj.episodic_table.setItem(1, 2, QTableWidgetItem("goodbye"))
        obj._filter_memory_table("hello")
        self.assertFalse(obj.episodic_table.isRowHidden(0))
        self.assertTrue(obj.episodic_table.isRowHidden(1))

    def test_filter_clear_shows_all(self):
        from tabs.memory_browser_tab import MemoryBrowserTabMixin
        from PyQt6.QtWidgets import QTableWidgetItem
        obj = MemoryBrowserTabMixin()
        self._mk(obj)
        obj.mem_source_tabs.setCurrentIndex(1)
        obj.core_table.setRowCount(2)
        obj.core_table.setItem(0, 0, QTableWidgetItem("1"))
        obj.core_table.setItem(0, 2, QTableWidgetItem("abc"))
        obj.core_table.setItem(1, 0, QTableWidgetItem("2"))
        obj.core_table.setItem(1, 2, QTableWidgetItem("def"))
        obj._filter_memory_table("xyz")
        self.assertTrue(obj.core_table.isRowHidden(0))
        obj._filter_memory_table("")
        self.assertFalse(obj.core_table.isRowHidden(0))

if __name__ == "__main__":
    unittest.main()
