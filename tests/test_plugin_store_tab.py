"""Tests for tabs/plugin_store_tab.py -- Plugin Store tab mixin."""
import unittest
from unittest.mock import patch, MagicMock


class TestPluginStoreTabMixin(unittest.TestCase):
    """Tests for PluginStoreTabMixin -- plugin browser + management."""

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "_ps_table"))
        self.assertTrue(hasattr(obj, "_ps_search_input"))
        self.assertTrue(hasattr(obj, "_ps_stats_label"))
        self.assertTrue(hasattr(obj, "_ps_detail_text"))
        self.assertTrue(hasattr(obj, "_ps_toggle_btn"))

    def test_create_tab_table_has_7_columns(self):
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        self.assertEqual(obj._ps_table.columnCount(), 7)
        labels = [obj._ps_table.horizontalHeaderItem(i).text() for i in range(7)]
        self.assertEqual(labels, ["Plugin", "Command", "Version", "Tags", "Status", "Errors", "Health"])

    def test_create_tab_detail_text_is_readonly(self):
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        self.assertTrue(obj._ps_detail_text.isReadOnly())

    def test_create_tab_toggle_btn_disabled_initially(self):
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        self.assertFalse(obj._ps_toggle_btn.isEnabled())
        self.assertFalse(obj._ps_clear_errors_btn.isEnabled())

    def test_gather_plugins_returns_empty_when_no_registry(self):
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        with patch.object(obj, "_ps_get_registry", return_value=None):
            result = obj._ps_gather_plugins()
        self.assertEqual(result, [])

    def test_gather_plugins_with_mock_registry(self):
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        mock_pr = MagicMock()
        mock_pr.plugins = {"search_web": {}, "write_file": {}}
        mock_pr.metadata = {
            "search_web": {"name": "Search Web", "description": "Search the web", "version": "1.0", "tags": ["web", "search"]},
            "write_file": {"name": "Write File", "description": "Write a file", "version": "2.0", "tags": ["file"]},
        }
        mock_pr.get_execution_errors.return_value = {"search_web": {"count": 3, "last_error": "timeout", "last_time": "2026-07-09"}}
        mock_pr.get_plugin_health_scores.return_value = {}
        mock_pr.is_enabled.side_effect = lambda cmd: cmd == "search_web"
        with patch.object(obj, "_ps_get_registry", return_value=mock_pr):
            plugins = obj._ps_gather_plugins()
        self.assertEqual(len(plugins), 2)
        # Sorted by command name: search_web, write_file
        self.assertEqual(plugins[0]["command"], "search_web")
        self.assertEqual(plugins[0]["name"], "Search Web")
        self.assertTrue(plugins[0]["enabled"])
        self.assertEqual(plugins[0]["error_count"], 3)
        self.assertEqual(plugins[1]["command"], "write_file")
        self.assertFalse(plugins[1]["enabled"])
        self.assertEqual(plugins[1]["error_count"], 0)

    def test_refresh_table_populates_rows(self):
        from PyQt6.QtCore import Qt
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        mock_pr = MagicMock()
        mock_pr.plugins = {"search_web": {}}
        mock_pr.metadata = {"search_web": {"name": "Search Web", "description": "Search", "version": "1.0", "tags": ["web"]}}
        mock_pr.get_execution_errors.return_value = {}
        mock_pr.get_plugin_health_scores.return_value = {}
        mock_pr.is_enabled.return_value = True
        with patch.object(obj, "_ps_get_registry", return_value=mock_pr):
            obj._ps_refresh_table()
        self.assertEqual(obj._ps_table.rowCount(), 1)
        self.assertEqual(obj._ps_table.item(0, 0).text(), "Search Web")
        self.assertEqual(obj._ps_table.item(0, 1).text(), "search_web")
        self.assertIn("Total: 1", obj._ps_stats_label.text())
        self.assertIn("Enabled: 1", obj._ps_stats_label.text())

    def test_refresh_table_with_search_filter(self):
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        mock_pr = MagicMock()
        mock_pr.plugins = {"search_web": {}, "write_file": {}}
        mock_pr.metadata = {
            "search_web": {"name": "Search Web", "description": "Search", "version": "1.0", "tags": ["web"]},
            "write_file": {"name": "Write File", "description": "Write", "version": "2.0", "tags": ["file"]},
        }
        mock_pr.get_execution_errors.return_value = {}
        mock_pr.get_plugin_health_scores.return_value = {}
        mock_pr.is_enabled.return_value = True
        obj._ps_search_input.setText("write")
        with patch.object(obj, "_ps_get_registry", return_value=mock_pr):
            obj._ps_refresh_table()
        self.assertEqual(obj._ps_table.rowCount(), 1)
        self.assertEqual(obj._ps_table.item(0, 0).text(), "Write File")

    def test_refresh_table_no_registry_shows_error(self):
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        with patch.object(obj, "_ps_get_registry", return_value=None):
            obj._ps_refresh_table()
        self.assertIn("unavailable", obj._ps_stats_label.text())

    def test_selection_changed_shows_details(self):
        from PyQt6.QtCore import Qt
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        mock_pr = MagicMock()
        mock_pr.plugins = {"search_web": {}}
        mock_pr.metadata = {"search_web": {"name": "Search Web", "description": "Search the web", "version": "1.0", "tags": ["web", "search"]}}
        mock_pr.get_execution_errors.return_value = {"search_web": {"count": 2, "last_error": "timeout error", "last_time": "2026-07-09"}}
        mock_pr.get_plugin_health_scores.return_value = {}
        mock_pr.is_enabled.return_value = True
        with patch.object(obj, "_ps_get_registry", return_value=mock_pr):
            obj._ps_refresh_table()
        # Simulate selecting row 0
        obj._ps_on_selection_changed(0, 0)
        detail = obj._ps_detail_text.toPlainText()
        self.assertIn("Search Web", detail)
        self.assertIn("search_web", detail)
        self.assertIn("Enabled", detail)
        self.assertIn("timeout error", detail)
        self.assertTrue(obj._ps_toggle_btn.isEnabled())
        self.assertTrue(obj._ps_clear_errors_btn.isEnabled())
        self.assertIn("Disable", obj._ps_toggle_btn.text())

    def test_selection_changed_negative_row_resets(self):
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        obj._ps_on_selection_changed(-1, 0)
        self.assertIn("Select a plugin", obj._ps_detail_text.toPlainText())
        self.assertFalse(obj._ps_toggle_btn.isEnabled())
        self.assertFalse(obj._ps_clear_errors_btn.isEnabled())

    def test_toggle_selected_calls_registry_toggle(self):
        from PyQt6.QtCore import Qt
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        obj.log_to_audit = MagicMock()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        mock_pr = MagicMock()
        mock_pr.plugins = {"search_web": {}}
        mock_pr.metadata = {"search_web": {"name": "Search Web", "description": "Search", "version": "1.0", "tags": []}}
        mock_pr.get_execution_errors.return_value = {}
        mock_pr.get_plugin_health_scores.return_value = {}
        mock_pr.is_enabled.return_value = True
        mock_pr.toggle.return_value = False
        with patch.object(obj, "_ps_get_registry", return_value=mock_pr):
            obj._ps_refresh_table()
            obj._ps_table.setCurrentCell(0, 0)
            obj._ps_toggle_selected()
        mock_pr.toggle.assert_called_once_with("search_web")
        obj.log_to_audit.assert_called()

    def test_enable_all_calls_registry_enable(self):
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        obj.log_to_audit = MagicMock()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        mock_pr = MagicMock()
        mock_pr.plugins = {"cmd1": {}, "cmd2": {}}
        mock_pr.metadata = {}
        mock_pr.get_execution_errors.return_value = {}
        mock_pr.get_plugin_health_scores.return_value = {}
        mock_pr.is_enabled.return_value = False
        with patch.object(obj, "_ps_get_registry", return_value=mock_pr):
            obj._ps_enable_all()
        self.assertEqual(mock_pr.enable.call_count, 2)
        obj.log_to_audit.assert_called()

    def test_disable_all_calls_registry_disable(self):
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        obj.log_to_audit = MagicMock()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        mock_pr = MagicMock()
        mock_pr.plugins = {"cmd1": {}, "cmd2": {}}
        mock_pr.metadata = {}
        mock_pr.get_execution_errors.return_value = {}
        mock_pr.get_plugin_health_scores.return_value = {}
        mock_pr.is_enabled.return_value = True
        with patch.object(obj, "_ps_get_registry", return_value=mock_pr):
            obj._ps_disable_all()
        self.assertEqual(mock_pr.disable.call_count, 2)
        obj.log_to_audit.assert_called()

    def test_clear_all_errors_calls_registry(self):
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        obj.log_to_audit = MagicMock()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        mock_pr = MagicMock()
        mock_pr.plugins = {}
        mock_pr.metadata = {}
        mock_pr.get_execution_errors.return_value = {}
        mock_pr.get_plugin_health_scores.return_value = {}
        mock_pr.is_enabled.return_value = True
        with patch.object(obj, "_ps_get_registry", return_value=mock_pr):
            obj._ps_clear_all_errors()
        mock_pr.clear_execution_errors.assert_called_once_with()
        obj.log_to_audit.assert_called()

    def test_clear_selected_errors_calls_registry(self):
        from PyQt6.QtCore import Qt
        from tabs.plugin_store_tab import PluginStoreTabMixin
        obj = PluginStoreTabMixin()
        obj.log_to_audit = MagicMock()
        with patch.object(obj, "_ps_refresh_table"):
            obj._tab = obj.create_plugin_store_tab()
        mock_pr = MagicMock()
        mock_pr.plugins = {"search_web": {}}
        mock_pr.metadata = {"search_web": {"name": "Search Web", "description": "Search", "version": "1.0", "tags": []}}
        mock_pr.get_execution_errors.return_value = {"search_web": {"count": 5, "last_error": "err", "last_time": "t"}}
        mock_pr.get_plugin_health_scores.return_value = {}
        mock_pr.is_enabled.return_value = True
        with patch.object(obj, "_ps_get_registry", return_value=mock_pr):
            obj._ps_refresh_table()
            obj._ps_table.setCurrentCell(0, 0)
            obj._ps_clear_selected_errors()
        mock_pr.clear_execution_errors.assert_called_once_with("search_web")
        obj.log_to_audit.assert_called()


if __name__ == "__main__":
    unittest.main()
