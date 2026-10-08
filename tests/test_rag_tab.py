"""Tests for tabs/rag_tab.py -- Agentic RAG Dashboard tab mixin."""
import unittest
from unittest.mock import patch, MagicMock

class TestRagTabMixin(unittest.TestCase):
    def setUp(self):
        self._p = patch("tabs.rag_tab.QTimer", return_value=MagicMock())
        self._p.start()
    def tearDown(self):
        self._p.stop()
    def _mk(self, obj):
        with patch.object(obj, "_refresh_rag_history"):
            obj._tab = obj.create_rag_tab()

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "rag_total_queries"))
        self.assertTrue(hasattr(obj, "rag_avg_hops"))
        self.assertTrue(hasattr(obj, "rag_total_sources"))
        self.assertTrue(hasattr(obj, "rag_history_tree"))
        self.assertTrue(hasattr(obj, "rag_query_input"))
        self.assertTrue(hasattr(obj, "rag_preview"))

    def test_history_tree_has_4_columns(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        tree = obj.rag_history_tree
        self.assertEqual(tree.columnCount(), 4)
        labels = [tree.headerItem().text(i) for i in range(4)]
        self.assertEqual(labels, ["Research Query / Detail", "Hops", "Sources", "Time"])

    def test_preview_is_readonly(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        self.assertTrue(obj.rag_preview.isReadOnly())

    def test_refresh_no_entries_shows_placeholder(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        pv = patch("memory_vault.get_recent_episodic", return_value=[])
        pv.start()
        try:
            obj._refresh_rag_history()
        finally:
            pv.stop()
        self.assertEqual(obj.rag_history_tree.topLevelItemCount(), 1)
        self.assertIn("no research queries", obj.rag_history_tree.topLevelItem(0).text(0))

    def test_refresh_error_shows_red_item(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        pv = patch("memory_vault.get_recent_episodic", side_effect=RuntimeError("DB error"))
        pv.start()
        try:
            obj._refresh_rag_history()
        finally:
            pv.stop()
        self.assertEqual(obj.rag_history_tree.topLevelItemCount(), 1)
        self.assertIn("Could not load", obj.rag_history_tree.topLevelItem(0).text(0))

    def test_refresh_rag_entries_populate_tree(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        entries = [
            {"tags": '["rag", "research"]', "summary": "[RAG] Q: What is AI?\nA: AI is...",
             "importance_score": 7, "timestamp": "2026-07-09 10:00:00",
             "metadata": '{"hops": 3, "context_count": 12, "sub_questions": ["What is ML?", "What is DL?"]}'},
            {"tags": '["rag"]', "summary": "[RAG] Q: Test?\nA: Answer",
             "importance_score": 3, "timestamp": "2026-07-09 11:00:00",
             "metadata": '{"hops": 1, "context_count": 3, "sub_questions": []}'},
        ]
        pv = patch("memory_vault.get_recent_episodic", return_value=entries)
        pv.start()
        try:
            obj._refresh_rag_history()
        finally:
            pv.stop()
        self.assertEqual(obj.rag_history_tree.topLevelItemCount(), 2)
        self.assertIn("What is AI?", obj.rag_history_tree.topLevelItem(0).text(0))

    def test_refresh_sub_questions_as_children(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        entries = [
            {"tags": '["rag"]', "summary": "[RAG] Q: Test?\nA: Answer",
             "importance_score": 5, "timestamp": "2026-07-09 10:00:00",
             "metadata": '{"hops": 2, "context_count": 5, "sub_questions": ["SQ1", "SQ2"]}'},
        ]
        pv = patch("memory_vault.get_recent_episodic", return_value=entries)
        pv.start()
        try:
            obj._refresh_rag_history()
        finally:
            pv.stop()
        parent = obj.rag_history_tree.topLevelItem(0)
        self.assertEqual(parent.childCount(), 2)
        self.assertIn("Sub-Q1", parent.child(0).text(0))
        self.assertIn("Sub-Q2", parent.child(1).text(0))

    def test_refresh_skips_non_rag_entries(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        entries = [
            {"tags": '["other"]', "summary": "Not RAG", "importance_score": 0,
             "timestamp": "2026-07-09 10:00:00", "metadata": "{}"},
        ]
        pv = patch("memory_vault.get_recent_episodic", return_value=entries)
        pv.start()
        try:
            obj._refresh_rag_history()
        finally:
            pv.stop()
        self.assertEqual(obj.rag_history_tree.topLevelItemCount(), 1)
        self.assertIn("no research queries", obj.rag_history_tree.topLevelItem(0).text(0))

    def test_refresh_updates_stats_labels(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        entries = [
            {"tags": '["rag"]', "summary": "[RAG] Q: Q1?\nA: A1",
             "importance_score": 5, "timestamp": "2026-07-09 10:00:00",
             "metadata": '{"hops": 3, "context_count": 10}'},
            {"tags": '["rag"]', "summary": "[RAG] Q: Q2?\nA: A2",
             "importance_score": 6, "timestamp": "2026-07-09 11:00:00",
             "metadata": '{"hops": 1, "context_count": 4}'},
        ]
        pv = patch("memory_vault.get_recent_episodic", return_value=entries)
        pv.start()
        try:
            obj._refresh_rag_history()
        finally:
            pv.stop()
        self.assertIn("Total Research Queries: 2", obj.rag_total_queries.text())
        self.assertIn("Avg Hops: 2.0", obj.rag_avg_hops.text())
        self.assertIn("Total Sources Retrieved: 14", obj.rag_total_sources.text())

    def test_color_coding_by_hop_count(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        entries = [
            {"tags": '["rag"]', "summary": "[RAG] Q: Q1?\nA: A1",
             "importance_score": 5, "timestamp": "2026-07-09 10:00:00",
             "metadata": '{"hops": 3, "context_count": 10}'},
            {"tags": '["rag"]', "summary": "[RAG] Q: Q2?\nA: A2",
             "importance_score": 5, "timestamp": "2026-07-09 11:00:00",
             "metadata": '{"hops": 2, "context_count": 5}'},
            {"tags": '["rag"]', "summary": "[RAG] Q: Q3?\nA: A3",
             "importance_score": 5, "timestamp": "2026-07-09 12:00:00",
             "metadata": '{"hops": 1, "context_count": 2}'},
        ]
        pv = patch("memory_vault.get_recent_episodic", return_value=entries)
        pv.start()
        try:
            obj._refresh_rag_history()
        finally:
            pv.stop()
        c0 = obj.rag_history_tree.topLevelItem(0).foreground(1).color()
        c1 = obj.rag_history_tree.topLevelItem(1).foreground(1).color()
        c2 = obj.rag_history_tree.topLevelItem(2).foreground(1).color()
        self.assertEqual(c0.name(), "#10b981")
        self.assertEqual(c1.name(), "#f59e0b")
        self.assertEqual(c2.name(), "#6b7280")

    def test_item_click_main_entry_shows_preview(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTreeWidgetItem
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        item = QTreeWidgetItem(["Test query", "3", "12 chunk(s)", "10:00:00"])
        item.setData(0, Qt.ItemDataRole.UserRole, {
            "query": "Test query", "hops": 3, "context_count": 12,
            "sub_questions": ["SQ1"], "full_summary": "[RAG] Q: Test query\nA: The answer",
            "timestamp": "2026-07-09 10:00:00",
        })
        obj._on_rag_item_clicked(item, 0)
        preview = obj.rag_preview.toPlainText()
        self.assertIn("Test query", preview)
        self.assertIn("The answer", preview)
        self.assertIn("Retrieval Hops: 3", preview)
        self.assertIn("Total Sources: 12", preview)
        self.assertIn("SQ1", preview)

    def test_item_click_sub_question_item(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTreeWidgetItem
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        item = QTreeWidgetItem(["Sub item"])
        item.setData(0, Qt.ItemDataRole.UserRole, {"type": "sub_question", "text": "What is ML?"})
        obj._on_rag_item_clicked(item, 0)
        self.assertIn("[SUB-QUESTION]", obj.rag_preview.toPlainText())
        self.assertIn("What is ML?", obj.rag_preview.toPlainText())

    def test_copy_preview_to_clipboard(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        obj.rag_preview.setPlainText("Sample text")
        mock_clipboard = MagicMock()
        with patch("tabs.rag_tab.QApplication.clipboard", return_value=mock_clipboard):
            obj._copy_rag_preview()
        mock_clipboard.setText.assert_called_once_with("Sample text")

    def test_repeat_query_pastes_to_chat_input(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTreeWidgetItem
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        obj.txt_input = MagicMock()
        item = QTreeWidgetItem(["Query text"])
        item.setData(0, Qt.ItemDataRole.UserRole, {"query": "Test research query"})
        obj.rag_history_tree.addTopLevelItem(item)
        obj.rag_history_tree.setCurrentItem(item)
        obj._repeat_rag_query()
        obj.txt_input.setPlainText.assert_called_once()
        self.assertIn("<<RAG:Test research query>>", obj.txt_input.setPlainText.call_args[0][0])

    def test_run_query_with_txt_input(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        obj.txt_input = MagicMock()
        obj.rag_query_input.setText("What is AI?")
        obj._run_rag_query()
        obj.txt_input.setPlainText.assert_called_once_with("<<RAG:What is AI?>>")
        self.assertEqual(obj.rag_query_input.text(), "")

    def test_run_query_without_txt_input(self):
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        obj.rag_query_input.setText("Query no input")
        obj._run_rag_query()
        self.assertIn("Cannot run query", obj.rag_preview.toPlainText())
        self.assertEqual(obj.rag_query_input.text(), "")

    def test_repeat_query_via_context_bridge(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTreeWidgetItem
        from tabs.context import DashboardContext
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        restored = []
        obj.context = DashboardContext(restore_chat_input=lambda text: restored.append(text))
        item = QTreeWidgetItem(["Query text"])
        item.setData(0, Qt.ItemDataRole.UserRole, {"query": "Test research query"})
        obj.rag_history_tree.addTopLevelItem(item)
        obj.rag_history_tree.setCurrentItem(item)
        obj._repeat_rag_query()
        self.assertEqual(restored, ["<<RAG:Test research query>>"])

    def test_run_query_via_context_bridge(self):
        from tabs.context import DashboardContext
        from tabs.rag_tab import RagTabMixin
        obj = RagTabMixin()
        self._mk(obj)
        restored = []
        obj.context = DashboardContext(restore_chat_input=lambda text: restored.append(text))
        obj.rag_query_input.setText("Context RAG query")
        obj._run_rag_query()
        self.assertEqual(restored, ["<<RAG:Context RAG query>>"])
        self.assertEqual(obj.rag_query_input.text(), "")

if __name__ == "__main__":
    unittest.main()
