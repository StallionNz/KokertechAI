"""Tests for app_plugins.py - AppPluginMixin plugin management UI."""

import unittest
from unittest.mock import MagicMock, patch, call

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QListWidgetItem, QListWidget, QLabel

import pytest


class TestAppPluginMixinLogic:
    """Tests for AppPluginMixin methods that can be tested with mocks."""

    @pytest.fixture(autouse=True)
    def _setup(self, shared_dashboard):
        self.window = shared_dashboard
        self.mock_registry = MagicMock()
        # Safe defaults to prevent MagicMock truthiness/int comparison issues
        self.mock_registry.get_enabled_count.return_value = 1
        self.mock_registry.get_plugin_count.return_value = 1
        self.mock_registry.get_execution_errors.return_value = {}
        # Ensure plugin_error_summary exists
        if not hasattr(self.window, 'plugin_error_summary'):
            from PyQt6.QtWidgets import QLabel
            self.window.plugin_error_summary = QLabel()
        yield

    def test_update_status_high_ratio_green(self):
        self.mock_registry.get_enabled_count.return_value = 20
        self.mock_registry.get_plugin_count.return_value = 22
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._update_plugin_status()
        assert "20/22 enabled" in self.window.plugin_status_label.text()
        assert "#10B981" in self.window.plugin_status_label.styleSheet()

    def test_update_status_medium_ratio_yellow(self):
        self.mock_registry.get_enabled_count.return_value = 10
        self.mock_registry.get_plugin_count.return_value = 22
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._update_plugin_status()
        assert "10/22 enabled" in self.window.plugin_status_label.text()
        assert "#FBBF24" in self.window.plugin_status_label.styleSheet()

    def test_update_status_low_ratio_red(self):
        self.mock_registry.get_enabled_count.return_value = 5
        self.mock_registry.get_plugin_count.return_value = 22
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._update_plugin_status()
        assert "5/22 enabled" in self.window.plugin_status_label.text()
        assert "#EF4444" in self.window.plugin_status_label.styleSheet()

    def test_update_status_zero_plugins(self):
        self.mock_registry.get_enabled_count.return_value = 0
        self.mock_registry.get_plugin_count.return_value = 0
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._update_plugin_status()
        assert "0/0 enabled" in self.window.plugin_status_label.text()

    def test_update_status_all_enabled(self):
        self.mock_registry.get_enabled_count.return_value = 24
        self.mock_registry.get_plugin_count.return_value = 24
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._update_plugin_status()
        assert "24/24 enabled" in self.window.plugin_status_label.text()
        assert "#10B981" in self.window.plugin_status_label.styleSheet()

    def test_on_toggle_enable(self):
        item = QListWidgetItem("Test")
        item.setData(Qt.ItemDataRole.UserRole, "TEST_CMD")
        item.setCheckState(Qt.CheckState.Checked)
        self.mock_registry.is_enabled.return_value = True
        self.mock_registry.get_enabled_count.return_value = 10
        self.mock_registry.get_plugin_count.return_value = 10
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._on_plugin_toggle(item)
        self.mock_registry.enable.assert_called_once_with("TEST_CMD")

    def test_on_toggle_disable(self):
        item = QListWidgetItem("Test")
        item.setData(Qt.ItemDataRole.UserRole, "TEST_CMD")
        item.setCheckState(Qt.CheckState.Unchecked)
        self.mock_registry.is_enabled.return_value = False
        self.mock_registry.get_enabled_count.return_value = 0
        self.mock_registry.get_plugin_count.return_value = 10
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._on_plugin_toggle(item)
        self.mock_registry.disable.assert_called_once_with("TEST_CMD")

    def test_disable_all_plugins(self):
        self.mock_registry.plugins = {"P1": MagicMock(), "P2": MagicMock()}
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            with patch.object(self.window, "_refresh_plugin_list") as mock_refresh:
                self.window._disable_all_plugins()
        assert self.mock_registry.disable.call_count == 2
        self.mock_registry.disable.assert_has_calls([call("P1"), call("P2")])
        assert mock_refresh.call_count == 1

    def test_enable_all_plugins(self):
        self.mock_registry.plugins = {"P1": MagicMock(), "P2": MagicMock()}
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            with patch.object(self.window, "_refresh_plugin_list") as mock_refresh:
                self.window._enable_all_plugins()
        assert self.mock_registry.enable.call_count == 2
        assert mock_refresh.call_count == 1

    def test_clear_plugin_errors(self):
        self.mock_registry.get_execution_errors.return_value = {}
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            with patch.object(self.window, "_update_plugin_list_errors") as mock_update:
                self.window._clear_plugin_errors()
        self.mock_registry.clear_execution_errors.assert_called_once()
        mock_update.assert_called_once()

    def test_clear_plugin_errors_no_selection(self):
        self.mock_registry.get_execution_errors.return_value = {}
        self.window.plugin_list.clear()
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            with patch.object(self.window, "_update_plugin_list_errors"):
                self.window._clear_plugin_errors()

    def test_on_selected_no_current(self):
        self.window._on_plugin_selected(None, None)
        assert "Select a plugin" in self.window.plugin_detail.text()

    def test_on_selected_disabled(self):
        item = QListWidgetItem("Test")
        item.setData(Qt.ItemDataRole.UserRole, "TEST_CMD")
        self.mock_registry.metadata = {"TEST_CMD": {"name": "Test Plugin", "version": "1.0.0", "author": "T"}}
        self.mock_registry.disabled = {"TEST_CMD"}
        self.mock_registry.get_execution_errors.return_value = None
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._on_plugin_selected(item, None)
        assert "Test Plugin" in self.window.plugin_detail.text()
        assert "Disabled" in self.window.plugin_detail.text()

    def test_on_selected_enabled(self):
        item = QListWidgetItem("Test")
        item.setData(Qt.ItemDataRole.UserRole, "TEST_CMD")
        self.mock_registry.metadata = {"TEST_CMD": {"name": "Active", "version": "2.0.0", "author": "Dev"}}
        self.mock_registry.disabled = set()
        self.mock_registry.get_execution_errors.return_value = None
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._on_plugin_selected(item, None)
        assert "Active" in self.window.plugin_detail.text()
        assert "Enabled" in self.window.plugin_detail.text()

    def test_on_selected_with_errors(self):
        """Error path: execution errors should show count, last error, and red stylesheet."""
        item = QListWidgetItem("Test")
        item.setData(Qt.ItemDataRole.UserRole, "ERR_CMD")
        self.mock_registry.metadata = {"ERR_CMD": {"name": "Faulty", "version": "1.0.0", "author": "T"}}
        self.mock_registry.disabled = set()
        self.mock_registry.get_execution_errors.return_value = {
            "count": 3, "last_error": "Something went wrong"
        }
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._on_plugin_selected(item, None)
        text = self.window.plugin_detail.text()
        assert "Faulty" in text
        assert "Errors: 3" in text
        assert "Something went wrong" in text
        assert "#EF4444" in self.window.plugin_detail.styleSheet()
        assert "Enabled" in text

    def test_update_list_errors_red(self):
        self.mock_registry.get_execution_errors.side_effect = lambda cmd=None: (
            {} if cmd is None else {"count": 1, "last_error": "fail"}
        )
        self.window.plugin_list.clear()
        item = QListWidgetItem("Test")
        item.setData(Qt.ItemDataRole.UserRole, "ERR_CMD")
        self.window.plugin_list.addItem(item)
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            with patch.object(self.window, "_update_plugin_tab_and_error_summary"):
                self.window._update_plugin_list_errors()
        assert item.foreground().color().name() == "#ef4444"

    def test_error_summary_with_errors(self):
        self.mock_registry.get_execution_errors.return_value = {"P1": {"count": 2, "last_error": "err"}}
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._update_plugin_tab_and_error_summary()
        text = self.window.plugin_error_summary.text()
        assert "1 plugin(s)" in text
        assert "2 total" in text

    def test_error_summary_no_errors(self):
        self.mock_registry.get_execution_errors.return_value = {}
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            self.window._update_plugin_tab_and_error_summary()
        assert self.window.plugin_error_summary.text() == ""

    def test_refresh_plugin_list_populates(self):
        self.mock_registry.plugins = {"P1": MagicMock()}
        self.mock_registry.metadata = {"P1": {"name": "Plugin One", "version": "1.0.0", "tags": []}}
        self.mock_registry.is_enabled.return_value = True
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            with patch.object(self.window, "_update_plugin_status"):
                self.window._refresh_plugin_list()
        assert self.window.plugin_list.count() == 1
        item0 = self.window.plugin_list.item(0)
        assert "Plugin One" in item0.text()
        assert item0.checkState() == Qt.CheckState.Checked

    def test_refresh_plugin_list_empty(self):
        self.mock_registry.plugins = {}
        with patch("app_plugins.plugin_registry.registry", self.mock_registry):
            with patch.object(self.window, "_update_plugin_status"):
                self.window._refresh_plugin_list()
        assert self.window.plugin_list.count() == 0

    def test_vault_cleaner_run(self):
        import vault_cleaner as real_vc
        with patch.object(real_vc, "run") as mock_run:
            with patch.dict("sys.modules", {"vault_cleaner": real_vc}):
                with patch.object(self.window, "render_knowledge_graph"):
                    self.window.run_vault_cleaner()
        mock_run.assert_called_once()

    def test_vault_cleaner_catches_exception(self):
        import vault_cleaner as real_vc
        with patch.object(real_vc, "run", side_effect=RuntimeError("DB locked")):
            with patch.dict("sys.modules", {"vault_cleaner": real_vc}):
                with patch.object(self.window, "log_to_audit") as mock_log:
                    self.window.run_vault_cleaner()
        # The except block should call log_to_audit with "Vault Cleaner error: ..."
        found = [str(c) for c in mock_log.call_args_list]
        assert len(found) >= 2, f"Expected at least 2 log calls, got {len(found)}: {found}"
        assert any("error" in str(c).lower() for c in mock_log.call_args_list), found


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
