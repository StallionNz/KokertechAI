"""Tests for tabs/cognitive_agency_tab.py -- Cognitive Agency tab mixin."""
import sqlite3
import unittest
from unittest.mock import patch, MagicMock


class TestCognitiveAgencyTabMixin(unittest.TestCase):
    """Tests for CognitiveAgencyTabMixin -- SCBE ledger + growth arcs + models."""

    def test_create_agency_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "bias_table"))
        self.assertTrue(hasattr(obj, "growth_table"))
        self.assertTrue(hasattr(obj, "models_table"))
        self.assertTrue(hasattr(obj, "models_status_label"))

    def test_bias_table_has_4_columns(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        self.assertEqual(obj.bias_table.columnCount(), 4)
        labels = [obj.bias_table.horizontalHeaderItem(i).text() for i in range(4)]
        self.assertEqual(labels, ["Timestamp", "Bias Type", "Confidence", "Description"])

    def test_growth_table_has_3_columns(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        self.assertEqual(obj.growth_table.columnCount(), 3)
        labels = [obj.growth_table.horizontalHeaderItem(i).text() for i in range(3)]
        self.assertEqual(labels, ["Timestamp", "Event Description", "Energy Shift"])

    def test_models_table_has_4_columns(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        self.assertEqual(obj.models_table.columnCount(), 4)
        labels = [obj.models_table.horizontalHeaderItem(i).text() for i in range(4)]
        self.assertEqual(labels, ["ID", "Created", "Owned By", "Object"])

    def test_refresh_models_success_status(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        obj.available_models = [{"id": "llama-3", "created": 1700000000, "owned_by": "meta", "object": "model"}]
        obj._discovery_status = "success"
        obj._discovery_diagnostic = ""
        obj._refresh_available_models()
        self.assertEqual(obj.models_table.rowCount(), 1)
        self.assertEqual(obj.models_table.item(0, 0).text(), "llama-3")
        self.assertIn("1 online", obj.models_status_label.text())

    def test_refresh_models_empty_status(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        obj.available_models = []
        obj._discovery_status = "empty"
        obj._discovery_diagnostic = "No models directory"
        obj._refresh_available_models()
        self.assertIn("none found", obj.models_status_label.text())

    def test_refresh_models_error_status(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        obj.available_models = []
        obj._discovery_status = "error"
        obj._discovery_diagnostic = "Connection refused"
        obj._refresh_available_models()
        self.assertIn("probe failed", obj.models_status_label.text())

    def test_refresh_models_skipped_status(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        obj.available_models = []
        obj._discovery_status = "skipped"
        obj._discovery_diagnostic = ""
        obj._refresh_available_models()
        self.assertIn("disabled via CONFIG", obj.models_status_label.text())

    def test_refresh_models_pending_status(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        obj.available_models = []
        obj._discovery_status = "pending"
        obj._discovery_diagnostic = ""
        obj._refresh_available_models()
        self.assertIn("probing", obj.models_status_label.text())

    def test_refresh_models_with_lock(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        import threading
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        obj._lock = threading.Lock()
        obj.available_models = [{"id": "test-model", "created": "", "owned_by": "local", "object": "model"}]
        obj._discovery_status = "success"
        obj._discovery_diagnostic = ""
        obj._refresh_available_models()
        self.assertEqual(obj.models_table.rowCount(), 1)
        self.assertEqual(obj.models_table.item(0, 0).text(), "test-model")

    def test_refresh_agency_data_db_not_exists(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        # Call the real refresh_agency_data with DB path not existing
        p1 = patch("os.path.exists", return_value=False)
        with p1:
            obj.refresh_agency_data()
        # Tables should still be empty (early return when DB missing)
        self.assertEqual(obj.bias_table.rowCount(), 0)
        self.assertEqual(obj.growth_table.rowCount(), 0)

    def test_refresh_agency_data_with_mock_db(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()

        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            [("2026-07-09", "confirmation_bias", 85.0, "Over-relied on first answer")],
            [("2026-07-09", "Gained new skill", 12.5)],
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        p1 = patch("os.path.exists", return_value=True)
        p2 = patch("sqlite3.connect", return_value=mock_conn)
        with p1, p2:
            obj.refresh_agency_data()

        self.assertEqual(obj.bias_table.rowCount(), 1)
        self.assertEqual(obj.bias_table.item(0, 0).text(), "2026-07-09")
        self.assertEqual(obj.bias_table.item(0, 1).text(), "confirmation_bias")
        self.assertEqual(obj.bias_table.item(0, 2).text(), "85.0%")
        self.assertEqual(obj.growth_table.rowCount(), 1)
        self.assertEqual(obj.growth_table.item(0, 0).text(), "2026-07-09")
        self.assertEqual(obj.growth_table.item(0, 1).text(), "Gained new skill")
        self.assertEqual(obj.growth_table.item(0, 2).text(), "+12.5%")
        fg = obj.growth_table.item(0, 2).foreground().color()
        self.assertEqual(fg.name(), "#10b981")
        mock_conn.close.assert_called_once()

    def test_refresh_agency_data_negative_energy_shift(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()

        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [
            [],
            [("2026-07-09", "Lost momentum", -5.0)],
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        p1 = patch("os.path.exists", return_value=True)
        p2 = patch("sqlite3.connect", return_value=mock_conn)
        with p1, p2:
            obj.refresh_agency_data()

        self.assertEqual(obj.growth_table.rowCount(), 1)
        self.assertEqual(obj.growth_table.item(0, 2).text(), "-5.0%")
        fg = obj.growth_table.item(0, 2).foreground().color()
        self.assertEqual(fg.name(), "#ef4444")

    def test_refresh_agency_data_db_exception_handled(self):
        from tabs.cognitive_agency_tab import CognitiveAgencyTabMixin
        obj = CognitiveAgencyTabMixin()
        with patch.object(obj, "refresh_agency_data"):
            obj._tab = obj.create_agency_tab()
        obj.log_to_audit = MagicMock()
        p1 = patch("os.path.exists", return_value=True)
        p2 = patch("sqlite3.connect", side_effect=sqlite3.Error("DB locked"))
        with p1, p2:
            obj.refresh_agency_data()
        obj.log_to_audit.assert_called_once()
        self.assertIn("Failed to refresh", obj.log_to_audit.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
