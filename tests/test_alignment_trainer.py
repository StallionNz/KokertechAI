"""
test_alignment_trainer.py - Unit tests for alignment_trainer.py.

NOTE: Historical cross-file QApplication singleton issue (conftest.py) — now resolved.
      All 51 pre-existing failures were fixed in June 2026 (test fixture
      isolation improvements, proper QWidget cleanup, and standardized mock
      patterns).  Per-file and cross-file batches both pass cleanly.

      Run this file in isolation to verify actual test results:
        pytest test_alignment_trainer.py
"""

import sys
import unittest
from unittest.mock import patch, MagicMock

# QApplication provided by conftest.py (session-scoped qapp fixture)

class TestAlignmentTrainer(unittest.TestCase):
    """Test the AlignmentTrainer QMainWindow methods."""

    def setUp(self):
        from alignment_trainer import AlignmentTrainer
        self.patches = []
        # Patch DB_PATH to prevent touching the real database
        self.patches.append(patch("alignment_trainer.DB_PATH", ":memory:"))
        # Patch QMessageBox to prevent blocking exec() calls during __init__
        # (load_history() fails on empty in-memory DB and calls QMessageBox.critical)
        self.patches.append(patch("alignment_trainer.QMessageBox"))
        for p in self.patches:
            p.start()
        self.window = AlignmentTrainer()

    def tearDown(self):
        try:
            self.window.close()
            self.window.deleteLater()
        except Exception:
            pass
        for p in reversed(self.patches):
            p.stop()
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

    @patch("alignment_trainer.sqlite3.connect")
    def test_load_history_populates_list(self, mock_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (1, "User: hello\nAgent: hi"),
            (2, "User: what is AI?\nAgent: AI is..."),
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn
        mock_connect.return_value.__enter__.return_value = mock_conn
        self.window.load_history()
        self.assertEqual(self.window.interaction_list.count(), 2)
        self.assertIn("ID 1", self.window.interaction_list.item(0).text())

    @patch("alignment_trainer.sqlite3.connect")
    def test_load_history_handles_db_error(self, mock_connect):
        mock_connect.side_effect = Exception("DB locked")
        with patch("alignment_trainer.QMessageBox") as mock_mb:
            self.window.load_history()
            mock_mb.critical.assert_called_once()

    def test_load_interaction_with_selection(self):
        from PyQt6.QtCore import Qt
        self.window.interaction_list.addItem("Test item")
        item = self.window.interaction_list.item(0)
        item.setData(Qt.ItemDataRole.UserRole, "Full content here")
        self.window.interaction_list.setCurrentRow(0)
        self.window.load_interaction()
        self.assertEqual(self.window.audit_display.toPlainText(), "Full content here")

    def test_load_interaction_no_selection(self):
        self.window.interaction_list.clearSelection()
        self.window.load_interaction()
        self.assertEqual(self.window.audit_display.toPlainText(), "")

    def test_submit_correction_empty_input(self):
        self.window.correction_input.clear()
        self.window.submit_correction()
        self.assertEqual(self.window.audit_display.toPlainText(), "")

    @patch("alignment_trainer.sqlite3.connect")
    def test_submit_correction_valid(self, mock_connect):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn
        mock_connect.return_value.__enter__.return_value = mock_conn
        self.window.correction_input.setText("Don't apologize so much")
        self.window.correction_type.setCurrentText("Tone Adjustment")
        with patch("alignment_trainer.QMessageBox") as mock_mb:
            self.window.submit_correction()
        mock_cursor.execute.assert_called_once()
        args = mock_cursor.execute.call_args[0][1]
        # Canonical schema (2026-09): agent_id inserted at index 1
        self.assertEqual(args[1], "alignment_trainer")
        self.assertEqual(args[2], "MANUAL_OVERRIDE: Tone Adjustment")
        self.assertEqual(args[4], "Don't apologize so much")
        mock_mb.information.assert_called_once()

    @patch("alignment_trainer.sqlite3.connect")
    def test_submit_correction_db_error(self, mock_connect):
        mock_connect.side_effect = Exception("Write failed")
        self.window.correction_input.setText("Fix this")
        with patch("alignment_trainer.QMessageBox") as mock_mb:
            self.window.submit_correction()
        mock_mb.critical.assert_called_once()

if __name__ == "__main__":
    unittest.main()
