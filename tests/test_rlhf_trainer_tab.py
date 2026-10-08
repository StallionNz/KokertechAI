"""Tests for tabs/rlhf_trainer_tab.py -- RLHF Alignment Trainer tab mixin."""
import sqlite3
import unittest
from unittest.mock import patch, MagicMock

class TestMiniBarChart(unittest.TestCase):
    def test_set_bars_stores_data(self):
        from tabs.rlhf_trainer_tab import MiniBarChart
        chart = MiniBarChart()
        bars = [("A", 0.8, "#EF4444"), ("B", 0.5, "#10B981")]
        chart.set_bars(bars)
        self.assertEqual(chart._bars, bars)
    def test_set_empty_bars(self):
        from tabs.rlhf_trainer_tab import MiniBarChart
        chart = MiniBarChart()
        chart.set_bars([])
        self.assertEqual(chart._bars, [])
    def test_min_max_height(self):
        from tabs.rlhf_trainer_tab import MiniBarChart
        chart = MiniBarChart()
        self.assertEqual(chart.minimumHeight(), 80)
        self.assertEqual(chart.maximumHeight(), 160)

class TestRLHFTrainerTabMixin(unittest.TestCase):
    def setUp(self):
        self._p = patch("tabs.rlhf_trainer_tab.QTimer", return_value=MagicMock())
        self._p.start()
    def tearDown(self):
        self._p.stop()
    def _mk(self, obj):
        with patch.object(obj, "load_rlhf_history"):
            obj._tab = obj.create_alignment_tab()
    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.rlhf_trainer_tab import RLHFTrainerTabMixin
        obj = RLHFTrainerTabMixin()
        self._mk(obj)
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "interaction_list"))
        self.assertTrue(hasattr(obj, "audit_display_rlhf"))
        self.assertTrue(hasattr(obj, "_rlhf_bias_table"))
        self.assertTrue(hasattr(obj, "correction_type"))
    def test_bias_table_4_cols(self):
        from tabs.rlhf_trainer_tab import RLHFTrainerTabMixin
        obj = RLHFTrainerTabMixin()
        self._mk(obj)
        self.assertEqual(obj._rlhf_bias_table.columnCount(), 4)
        labels = [obj._rlhf_bias_table.horizontalHeaderItem(i).text() for i in range(4)]
        self.assertEqual(labels, ["Time", "Type", "Confidence", "Description"])
    def test_correction_type_has_6_items(self):
        from tabs.rlhf_trainer_tab import RLHFTrainerTabMixin
        obj = RLHFTrainerTabMixin()
        self._mk(obj)
        self.assertEqual(obj.correction_type.count(), 6)
        self.assertEqual(obj.correction_type.itemText(0), "Logic/Format Error")
    def test_audit_display_readonly(self):
        from tabs.rlhf_trainer_tab import RLHFTrainerTabMixin
        obj = RLHFTrainerTabMixin()
        self._mk(obj)
        self.assertTrue(obj.audit_display_rlhf.isReadOnly())
    def test_get_bias_data_returns_rows_and_counts(self):
        from tabs.rlhf_trainer_tab import RLHFTrainerTabMixin
        obj = RLHFTrainerTabMixin()
        self._mk(obj)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "timestamp": "2026-07-09 10:00", "bias_type": "logic", "confidence_score": 85, "description": "Bad logic"},
            {"id": 2, "timestamp": "2026-07-09 11:00", "bias_type": "logic", "confidence_score": 60, "description": "Minor logic"},
            {"id": 3, "timestamp": "2026-07-09 12:00", "bias_type": "tone", "confidence_score": 30, "description": "Tone issue"},
        ]
        mock_conn.execute.return_value = mock_cursor
        p = patch("tabs.rlhf_trainer_tab.sqlite3")
        ms = p.start()
        ms.connect.return_value = mock_conn
        ms.connect.return_value.__enter__.return_value = mock_conn
        ms.connect.return_value.__exit__.return_value = None
        ms.Row = dict
        ms.OperationalError = sqlite3.OperationalError
        try:
            rows, counts = obj._rlhf_get_bias_data()
        finally:
            p.stop()
        self.assertEqual(len(rows), 3)
        self.assertEqual(counts["logic"], 2)
        self.assertEqual(counts["tone"], 1)
    def test_get_bias_data_handles_error(self):
        from tabs.rlhf_trainer_tab import RLHFTrainerTabMixin
        obj = RLHFTrainerTabMixin()
        self._mk(obj)
        p = patch("tabs.rlhf_trainer_tab.sqlite3")
        ms = p.start()
        ms.connect.side_effect = sqlite3.OperationalError("DB locked")
        ms.Row = dict
        ms.OperationalError = sqlite3.OperationalError
        try:
            rows, counts = obj._rlhf_get_bias_data()
        finally:
            p.stop()
        self.assertEqual(rows, [])
        self.assertEqual(counts, {})
    def test_load_history_populates_lists(self):
        from tabs.rlhf_trainer_tab import RLHFTrainerTabMixin
        obj = RLHFTrainerTabMixin()
        obj.log_to_audit = MagicMock()
        self._mk(obj)
        mock_conn = MagicMock()
        # load_rlhf_history calls conn.execute() twice:
        # 1st: SELECT id, content FROM core_memories (expects tuples)
        # 2nd: via _rlhf_get_bias_data -> dict(r) (expects dict-like rows)
        interaction_cursor = MagicMock()
        interaction_cursor.fetchall.return_value = [(1, "Hello world interaction")]
        bias_cursor = MagicMock()
        bias_cursor.fetchall.return_value = []  # no bias entries
        mock_conn.execute.side_effect = [interaction_cursor, bias_cursor]
        p = patch("tabs.rlhf_trainer_tab.sqlite3")
        ms = p.start()
        ms.connect.return_value = mock_conn
        ms.connect.return_value.__enter__.return_value = mock_conn
        ms.connect.return_value.__exit__.return_value = None
        ms.Row = dict
        ms.OperationalError = sqlite3.OperationalError
        try:
            obj.load_rlhf_history()
        finally:
            p.stop()
        self.assertEqual(obj.interaction_list.count(), 1)
        self.assertIn("Interactions: 1", obj._rlhf_stats_label.text())
        self.assertIn("Biases: 0", obj._rlhf_stats_label.text())
    def test_load_interaction_shows_content(self):
        from PyQt6.QtCore import Qt
        from tabs.rlhf_trainer_tab import RLHFTrainerTabMixin
        from PyQt6.QtWidgets import QListWidgetItem
        obj = RLHFTrainerTabMixin()
        self._mk(obj)
        item = QListWidgetItem("Test")
        item.setData(Qt.ItemDataRole.UserRole, "Full audit content")
        obj.interaction_list.addItem(item)
        obj.interaction_list.setCurrentRow(0)
        obj.load_rlhf_interaction()
        self.assertEqual(obj.audit_display_rlhf.toPlainText(), "Full audit content")
    def test_submit_correction_empty_is_noop(self):
        from tabs.rlhf_trainer_tab import RLHFTrainerTabMixin
        obj = RLHFTrainerTabMixin()
        self._mk(obj)
        obj.correction_input.setText("")
        obj.submit_rlhf_correction()
    def test_submit_correction_calls_db(self):
        from tabs.rlhf_trainer_tab import RLHFTrainerTabMixin
        obj = RLHFTrainerTabMixin()
        self._mk(obj)
        obj.correction_input.setText("Fix the tone")
        mock_conn = MagicMock()
        p = patch("tabs.rlhf_trainer_tab.sqlite3")
        ms = p.start()
        ms.connect.return_value = mock_conn
        ms.OperationalError = sqlite3.OperationalError
        try:
            p2 = patch("tabs.rlhf_trainer_tab.QMessageBox")
            p3 = patch.object(obj, "load_rlhf_history")
            p2.start(); p3.start()
            obj.submit_rlhf_correction()
            p2.stop(); p3.stop()
        finally:
            p.stop()
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

if __name__ == "__main__":
    unittest.main()
