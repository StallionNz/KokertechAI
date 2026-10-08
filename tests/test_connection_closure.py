"""REGRESSION GUARD tests for deterministic SQLite connection closure (Zero-Trust Invariant 2).

Phase P1 Concurrency & Database Sanitization:
Verifies that all 8 converted sites + memory_vault.delete_memory_node:
- Pass timeout=15.0 to sqlite3.connect
- Enclose the connection in try ... finally: conn.close()
- Ensure conn.close() is invoked even if queries succeed or fail with exceptions.
"""

import os
import unittest
from unittest.mock import MagicMock, patch
import sqlite3

import cognitive_auditor
from services.context_service import ContextService
from tabs.rlhf_trainer_tab import RLHFTrainerTabMixin
from tabs.scheduled_actions_tab import ScheduledActionsTabMixin
from scripts.alignment_trainer import AlignmentTrainer
import memory_vault


class TestConnectionClosure(unittest.TestCase):
    """Verify deterministic connection closure and timeout=15.0 across Phase P1 targets."""

    def test_cognitive_auditor_ensure_agency_tables_closes_conn(self):
        """Verify ensure_agency_tables closes its connection with timeout=15.0."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        with patch("sqlite3.connect", return_value=mock_conn) as mock_connect:
            cognitive_auditor.ensure_agency_tables()
            mock_connect.assert_called_once_with(cognitive_auditor.DB_PATH, timeout=15.0)
            mock_conn.close.assert_called_once()

    def test_cognitive_auditor_ensure_agency_tables_closes_on_error(self):
        """Verify ensure_agency_tables closes its connection even on OperationalError."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.execute.side_effect = sqlite3.OperationalError("disk I/O error")
        with patch("sqlite3.connect", return_value=mock_conn):
            cognitive_auditor.ensure_agency_tables()
            mock_conn.close.assert_called_once()

    def test_cognitive_auditor_process_audit_result_closes_conn(self):
        """Verify _process_audit_result closes its connection with timeout=15.0."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        with patch("sqlite3.connect", return_value=mock_conn) as mock_connect:
            content = (
                '{"biases": [{"bias_type": "sycophancy", "description": "flattery", "confidence": 0.8}],'
                ' "growths": [{"event_description": "refusal upheld", "energy_shift": 0.1}]}'
            )
            cognitive_auditor._process_audit_result(content=content, log_callback=MagicMock())
            mock_connect.assert_called_once_with(cognitive_auditor.DB_PATH, timeout=15.0)
            mock_conn.close.assert_called_once()

    def test_context_service_get_recent_biases_inline_closes_conn(self):
        """Verify ContextService.get_recent_biases_inline closes connection with timeout=15.0."""
        cs = ContextService(workspace=".")
        cs._bias_cache = {"data": "", "ts": 0.0}
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("sycophancy", "avoid praising blindly")]
        mock_conn.cursor.return_value = mock_cursor

        with patch("os.path.exists", return_value=True), \
             patch("sqlite3.connect", return_value=mock_conn) as mock_connect:
            result = cs.get_recent_biases_inline()
            self.assertIn("sycophancy", result)
            mock_connect.assert_called_once_with(os.path.join(".", "kokertech_vault.db"), timeout=15.0)
            mock_conn.close.assert_called_once()

    def test_rlhf_trainer_tab_bias_data_closes_conn(self):
        """Verify RLHFTrainerTabMixin._rlhf_get_bias_data closes connection with timeout=15.0."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.execute.return_value = mock_cursor

        mock_self = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn) as mock_connect:
            rows, counts = RLHFTrainerTabMixin._rlhf_get_bias_data(mock_self)
            self.assertEqual(rows, [])
            mock_connect.assert_called_once()
            self.assertEqual(mock_connect.call_args[1].get("timeout"), 15.0)
            mock_conn.close.assert_called_once()

    def test_rlhf_trainer_tab_load_history_closes_conn(self):
        """Verify RLHFTrainerTabMixin.load_rlhf_history closes connection with timeout=15.0."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.execute.return_value = mock_cursor

        mock_self = MagicMock()
        mock_self._rlhf_get_bias_data.return_value = ([], {})
        with patch("sqlite3.connect", return_value=mock_conn) as mock_connect:
            RLHFTrainerTabMixin.load_rlhf_history(mock_self)
            mock_connect.assert_called_once()
            self.assertEqual(mock_connect.call_args[1].get("timeout"), 15.0)
            mock_conn.close.assert_called_once()

    def test_scheduled_actions_tab_ensure_table_closes_conn(self):
        """Verify ScheduledActionsTabMixin._sa_ensure_table closes connection with timeout=15.0."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_self = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn) as mock_connect:
            ScheduledActionsTabMixin._sa_ensure_table(mock_self)
            mock_connect.assert_called_once()
            self.assertEqual(mock_connect.call_args[1].get("timeout"), 15.0)
            mock_conn.close.assert_called_once()

    def test_alignment_trainer_load_history_closes_conn(self):
        """Verify AlignmentTrainer.load_history closes connection with timeout=15.0."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor

        mock_self = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn) as mock_connect:
            AlignmentTrainer.load_history(mock_self)
            mock_connect.assert_called_once()
            self.assertEqual(mock_connect.call_args[1].get("timeout"), 15.0)
            mock_conn.close.assert_called_once()

    def test_alignment_trainer_submit_correction_closes_conn(self):
        """Verify AlignmentTrainer.submit_correction closes connection with timeout=15.0."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        mock_self = MagicMock()
        mock_self.correction_input.text.return_value = "Always be objective"
        mock_self.correction_type.currentText.return_value = "Neutrality"

        with patch("sqlite3.connect", return_value=mock_conn) as mock_connect, \
             patch("scripts.alignment_trainer.QMessageBox.information"):
            AlignmentTrainer.submit_correction(mock_self)
            mock_connect.assert_called_once()
            self.assertEqual(mock_connect.call_args[1].get("timeout"), 15.0)
            mock_conn.close.assert_called_once()

    def test_memory_vault_delete_memory_node_closes_conn(self):
        """Verify memory_vault.delete_memory_node closes connection with timeout=15.0."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        with patch("memory_vault.ensure_tables_exist"), \
             patch("sqlite3.connect", return_value=mock_conn) as mock_connect, \
             patch("memory_vault._fts_sync_core_delete"), \
             patch("memory_vault._invalidate_vector_cache"), \
             patch("memory_vault.auto_checkpoint_wal"):
            res = memory_vault.delete_memory_node(42)
            self.assertTrue(res)
            mock_connect.assert_called_once_with(memory_vault.DB_PATH, timeout=15.0)
            mock_conn.close.assert_called_once()

    def test_cognitive_auditor_process_audit_result_closes_on_error(self):
        """Verify _process_audit_result closes connection even on error."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.commit.side_effect = sqlite3.OperationalError("disk error")
        with patch("sqlite3.connect", return_value=mock_conn):
            content = '{"biases": [{"bias_type": "bias", "description": "desc", "confidence": 0.9}]}'
            cognitive_auditor._process_audit_result(content=content, log_callback=MagicMock())
            mock_conn.close.assert_called_once()

    def test_context_service_get_recent_biases_inline_closes_on_error(self):
        """Verify ContextService.get_recent_biases_inline closes connection on query error."""
        cs = ContextService(workspace=".")
        cs._bias_cache = {"data": "", "ts": 0.0}
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.cursor.side_effect = sqlite3.OperationalError("query failed")
        with patch("os.path.exists", return_value=True), \
             patch("sqlite3.connect", return_value=mock_conn):
            result = cs.get_recent_biases_inline()
            self.assertEqual(result, "")
            mock_conn.close.assert_called_once()

    def test_rlhf_trainer_tab_bias_data_closes_on_error(self):
        """Verify RLHFTrainerTabMixin._rlhf_get_bias_data closes connection on query error."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.execute.side_effect = sqlite3.OperationalError("table locked")
        mock_self = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            rows, counts = RLHFTrainerTabMixin._rlhf_get_bias_data(mock_self)
            self.assertEqual(rows, [])
            mock_conn.close.assert_called_once()

    def test_rlhf_trainer_tab_load_history_closes_on_error(self):
        """Verify RLHFTrainerTabMixin.load_rlhf_history closes connection on query error."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.execute.side_effect = sqlite3.OperationalError("query failed")
        mock_self = MagicMock()
        mock_self._rlhf_get_bias_data.return_value = ([], {})
        with patch("sqlite3.connect", return_value=mock_conn):
            RLHFTrainerTabMixin.load_rlhf_history(mock_self)
            mock_conn.close.assert_called_once()

    def test_scheduled_actions_tab_ensure_table_closes_on_error(self):
        """Verify ScheduledActionsTabMixin._sa_ensure_table closes connection on DDL error."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.execute.side_effect = sqlite3.OperationalError("corrupted schema")
        mock_self = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn):
            ScheduledActionsTabMixin._sa_ensure_table(mock_self)
            mock_conn.close.assert_called_once()

    def test_alignment_trainer_load_history_closes_on_error(self):
        """Verify AlignmentTrainer.load_history closes connection on cursor error."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.cursor.side_effect = sqlite3.OperationalError("db locked")
        mock_self = MagicMock()
        with patch("sqlite3.connect", return_value=mock_conn), \
             patch("scripts.alignment_trainer.QMessageBox.critical"):
            AlignmentTrainer.load_history(mock_self)
            mock_conn.close.assert_called_once()

    def test_alignment_trainer_submit_correction_closes_on_error(self):
        """Verify AlignmentTrainer.submit_correction closes connection on insert error."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.cursor.side_effect = sqlite3.OperationalError("insert failed")
        mock_self = MagicMock()
        mock_self.correction_input.text.return_value = "Always be objective"
        mock_self.correction_type.currentText.return_value = "Neutrality"
        with patch("sqlite3.connect", return_value=mock_conn), \
             patch("scripts.alignment_trainer.QMessageBox.critical"):
            AlignmentTrainer.submit_correction(mock_self)
            mock_conn.close.assert_called_once()

    def test_memory_vault_delete_memory_node_closes_on_error(self):
        """Verify memory_vault.delete_memory_node closes connection when execute raises."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        mock_conn.execute.side_effect = sqlite3.OperationalError("disk I/O error")
        with patch("memory_vault.ensure_tables_exist"), \
             patch("sqlite3.connect", return_value=mock_conn):
            with self.assertRaises(sqlite3.OperationalError):
                memory_vault.delete_memory_node(42)
            mock_conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
