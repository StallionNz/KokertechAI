"""Tests for tabs/mcp_client_tab.py -- MCP Client Management tab mixin."""
import unittest
from unittest.mock import patch, MagicMock

class TestMCPClientTabMixin(unittest.TestCase):
    def setUp(self):
        self._p = patch("tabs.mcp_client_tab.QTimer", return_value=MagicMock())
        self._p.start()
    def tearDown(self):
        self._p.stop()
    def _mk(self, obj):
        with patch.object(obj, "_mcp_refresh"):
            obj._tab = obj.create_mcp_client_tab()

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "_mcp_table"))
        self.assertTrue(hasattr(obj, "_mcp_name_input"))
        self.assertTrue(hasattr(obj, "_mcp_url_input"))
        self.assertTrue(hasattr(obj, "_mcp_detail_text"))
        self.assertTrue(hasattr(obj, "_mcp_stats_label"))

    def test_table_has_5_columns(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        self.assertEqual(obj._mcp_table.columnCount(), 5)
        labels = [obj._mcp_table.horizontalHeaderItem(i).text() for i in range(5)]
        self.assertEqual(labels, ["Name", "URL", "Tools", "Status", "Enabled"])

    def test_detail_text_is_readonly(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        self.assertTrue(obj._mcp_detail_text.isReadOnly())

    def test_timeout_spin_defaults(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        self.assertEqual(obj._mcp_timeout_spin.minimum(), 1)
        self.assertEqual(obj._mcp_timeout_spin.maximum(), 120)
        self.assertEqual(obj._mcp_timeout_spin.value(), 15)

    def test_get_mcp_client_returns_singleton(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        mock_client = MagicMock()
        with patch("mcp_client.get_mcp_client", return_value=mock_client):
            result = obj._get_mcp_client()
        self.assertEqual(result, mock_client)

    def test_get_mcp_client_import_error_returns_none(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        with patch("mcp_client.get_mcp_client", side_effect=ImportError):
            result = obj._get_mcp_client()
        self.assertIsNone(result)

    def test_refresh_no_client_shows_unavailable(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        with patch.object(obj, "_get_mcp_client", return_value=None):
            obj._mcp_refresh()
        self.assertIn("not available", obj._mcp_status_label.text())

    def test_refresh_populates_table(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        mock_conn1 = MagicMock()
        mock_conn1.name = "Server1"
        mock_conn1.url = "http://localhost:9000"
        mock_conn1.tools = [{"name": "tool_a"}, {"name": "tool_b"}]
        mock_conn1.enabled = True
        mock_conn1.error = None
        mock_conn2 = MagicMock()
        mock_conn2.name = "Server2"
        mock_conn2.url = "http://localhost:9001"
        mock_conn2.tools = []
        mock_conn2.enabled = False
        mock_conn2.error = "timeout"
        mock_client = MagicMock()
        mock_client.servers = {"s1": mock_conn1, "s2": mock_conn2}
        mock_client._synced_commands = {}
        with patch.object(obj, "_get_mcp_client", return_value=mock_client):
            obj._mcp_refresh()
        self.assertEqual(obj._mcp_table.rowCount(), 2)
        self.assertEqual(obj._mcp_table.item(0, 0).text(), "Server1")
        self.assertEqual(obj._mcp_table.item(0, 1).text(), "http://localhost:9000")
        self.assertEqual(obj._mcp_table.item(0, 2).text(), "2")
        self.assertEqual(obj._mcp_table.item(0, 3).text(), "Connected")
        self.assertEqual(obj._mcp_table.item(0, 4).text(), "Yes")
        self.assertEqual(obj._mcp_table.item(1, 3).text(), "Disabled")
        self.assertEqual(obj._mcp_table.item(1, 4).text(), "No")

    def test_refresh_stats_label(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        mock_conn = MagicMock()
        mock_conn.name = "Srv"
        mock_conn.url = "http://x"
        mock_conn.tools = [{"name": "t1"}, {"name": "t2"}, {"name": "t3"}]
        mock_conn.enabled = True
        mock_conn.error = None
        mock_client = MagicMock()
        mock_client.servers = {"s": mock_conn}
        mock_client._synced_commands = {"MCP_SRV_t1": {}, "MCP_SRV_t2": {}}
        with patch.object(obj, "_get_mcp_client", return_value=mock_client):
            obj._mcp_refresh()
        self.assertIn("Servers: 1", obj._mcp_stats_label.text())
        self.assertIn("Total tools: 3", obj._mcp_stats_label.text())
        self.assertIn("Synced: 2", obj._mcp_stats_label.text())

    def test_selection_changed_shows_details(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        mock_conn = MagicMock()
        mock_conn.name = "TestServer"
        mock_conn.url = "http://localhost:9000"
        mock_conn.enabled = True
        mock_conn.timeout = 30
        mock_conn.tools = [{"name": "echo", "description": "Echo tool",
                             "inputSchema": {"properties": {}}}]
        mock_conn.server_info = {"version": "1.0"}
        mock_conn.error = None
        mock_client = MagicMock()
        mock_client.servers = {"TestServer": mock_conn}
        with patch.object(obj, "_get_mcp_client", return_value=mock_client):
            obj._mcp_refresh()
            obj._mcp_on_selection_changed(0, 0)
        text = obj._mcp_detail_text.toPlainText()
        self.assertIn("TestServer", text)
        self.assertIn("http://localhost:9000", text)
        self.assertIn("echo", text)

    def test_selection_changed_negative_row_clears(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        obj._mcp_detail_text.setPlainText("Old text")
        obj._mcp_on_selection_changed(-1, 0)
        self.assertIn("Select a server", obj._mcp_detail_text.toPlainText())

    def test_add_server_missing_name_warns(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        obj._mcp_name_input.setText("")
        obj._mcp_url_input.setText("http://localhost")
        with patch("tabs.mcp_client_tab.QMessageBox") as mock_mb:
            obj._mcp_add_server()
        mock_mb.warning.assert_called_once()

    def test_add_server_missing_url_warns(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        obj._mcp_name_input.setText("Test")
        obj._mcp_url_input.setText("")
        with patch("tabs.mcp_client_tab.QMessageBox") as mock_mb:
            obj._mcp_add_server()
        mock_mb.warning.assert_called_once()

    def test_remove_selected_no_selection_shows_info(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        with patch("tabs.mcp_client_tab.QMessageBox") as mock_mb:
            obj._mcp_remove_selected()
        mock_mb.information.assert_called_once()

    def test_test_selected_no_selection_shows_info(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        with patch("tabs.mcp_client_tab.QMessageBox") as mock_mb:
            obj._mcp_test_selected()
        mock_mb.information.assert_called_once()

    def test_call_tool_missing_name_warns(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTableWidgetItem
        obj = MCPClientTabMixin()
        self._mk(obj)
        # Set up a row so currentRow() returns 0, bypassing the no-selection guard
        item0 = QTableWidgetItem("Srv")
        item0.setData(Qt.ItemDataRole.UserRole, "Srv")
        obj._mcp_table.setRowCount(1)
        obj._mcp_table.setItem(0, 0, item0)
        obj._mcp_table.setCurrentCell(0, 0)
        # Also mock _get_mcp_client so the code doesn't crash after the name check
        mock_conn = MagicMock()
        mock_conn.name = "Srv"
        mock_client = MagicMock()
        mock_client.servers = {"Srv": mock_conn}
        with patch.object(obj, "_get_mcp_client", return_value=mock_client):
            obj._mcp_call_tool_input.setText("")
            with patch("tabs.mcp_client_tab.QMessageBox") as mock_mb:
                obj._mcp_call_tool()
        mock_mb.warning.assert_called_once()

    def test_call_tool_invalid_json_warns(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTableWidgetItem
        obj = MCPClientTabMixin()
        self._mk(obj)
        # Set up a row so currentRow() returns 0, bypassing the no-selection guard
        item0 = QTableWidgetItem("Srv")
        item0.setData(Qt.ItemDataRole.UserRole, "Srv")
        obj._mcp_table.setRowCount(1)
        obj._mcp_table.setItem(0, 0, item0)
        obj._mcp_table.setCurrentCell(0, 0)
        mock_conn = MagicMock()
        mock_conn.name = "Srv"
        mock_client = MagicMock()
        mock_client.servers = {"Srv": mock_conn}
        with patch.object(obj, "_get_mcp_client", return_value=mock_client):
            obj._mcp_call_tool_input.setText("echo")
            obj._mcp_call_args_input.setText("not valid json")
            with patch("tabs.mcp_client_tab.QMessageBox") as mock_mb:
                obj._mcp_call_tool()
        mock_mb.warning.assert_called_once()

    def test_call_tool_no_selection_warns(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        obj = MCPClientTabMixin()
        self._mk(obj)
        obj._mcp_call_tool_input.setText("echo")
        obj._mcp_call_args_input.setText('{"x": 1}')
        with patch("tabs.mcp_client_tab.QMessageBox") as mock_mb:
            obj._mcp_call_tool()
        mock_mb.information.assert_called_once()


class TestMCPErrorCallbacks(unittest.TestCase):
    def setUp(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        self.obj = MCPClientTabMixin()
        TestMCPClientTabMixin._mk(TestMCPClientTabMixin(), self.obj)
        self.obj.log_to_audit = MagicMock()

    def test_mcp_test_selected_exception_callback(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTableWidgetItem
        item0 = QTableWidgetItem("Srv")
        item0.setData(Qt.ItemDataRole.UserRole, "Srv")
        self.obj._mcp_table.setRowCount(1)
        self.obj._mcp_table.setItem(0, 0, item0)
        self.obj._mcp_table.setCurrentCell(0, 0)
        mock_conn = MagicMock()
        mock_conn.name = "Srv"
        mock_conn.check_health.side_effect = RuntimeError("Health timeout")
        mock_client = MagicMock()
        mock_client.servers = {"Srv": mock_conn}

        with patch.object(self.obj, "_get_mcp_client", return_value=mock_client), \
             patch("threading.Thread") as mock_thread, \
             patch("tabs.mcp_client_tab.QTimer.singleShot") as mock_single_shot:
            def run_sync(target=None, **kwargs):
                target()
                return MagicMock()
            mock_thread.side_effect = run_sync
            self.obj._mcp_test_selected()
            callback = mock_single_shot.call_args[0][1]
            callback()
            self.assertIn("Test failed: Health timeout", self.obj._mcp_detail_text.toPlainText())

    def test_mcp_call_tool_exception_callback(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTableWidgetItem
        item0 = QTableWidgetItem("Srv")
        item0.setData(Qt.ItemDataRole.UserRole, "Srv")
        self.obj._mcp_table.setRowCount(1)
        self.obj._mcp_table.setItem(0, 0, item0)
        self.obj._mcp_table.setCurrentCell(0, 0)
        mock_conn = MagicMock()
        mock_conn.name = "Srv"
        mock_conn.call_tool.side_effect = RuntimeError("Tool execution failed")
        mock_client = MagicMock()
        mock_client.servers = {"Srv": mock_conn}

        self.obj._mcp_call_tool_input.setText("my_tool")
        self.obj._mcp_call_args_input.setText("{}")

        with patch.object(self.obj, "_get_mcp_client", return_value=mock_client), \
             patch("threading.Thread") as mock_thread, \
             patch("tabs.mcp_client_tab.QTimer.singleShot") as mock_single_shot:
            def run_sync(target=None, **kwargs):
                target()
                return MagicMock()
            mock_thread.side_effect = run_sync
            self.obj._mcp_call_tool()
            callback = mock_single_shot.call_args[0][1]
            callback()
            self.assertIn("Call failed: Tool execution failed", self.obj._mcp_detail_text.toPlainText())


class TestMCPClientDashboardContext(unittest.TestCase):
    """Test DashboardContext composition and routing on MCPClientTabMixin."""

    def setUp(self):
        from tabs.mcp_client_tab import MCPClientTabMixin
        self.obj = MCPClientTabMixin()
        TestMCPClientTabMixin._mk(TestMCPClientTabMixin(), self.obj)

    def test_default_context_fallback(self):
        ctx = self.obj.context
        self.assertIsNotNone(ctx)

    def test_context_setter_and_override(self):
        from tabs.context import DashboardContext
        custom_ctx = DashboardContext()
        self.obj.context = custom_ctx
        self.assertIs(self.obj.context, custom_ctx)
        self.assertIs(self.obj.ctx, custom_ctx)

    def test_log_routes_to_context_log(self):
        from tabs.context import DashboardContext
        logged = []
        self.obj.context = DashboardContext(log_to_audit=lambda msg: logged.append(msg))
        self.obj.context.log("MCP test event")
        self.assertEqual(logged, ["MCP test event"])

    def test_remove_selected_routes_audit_to_context_log(self):
        from tabs.context import DashboardContext
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTableWidgetItem
        logged = []
        self.obj.context = DashboardContext(log_to_audit=lambda msg: logged.append(msg))
        item0 = QTableWidgetItem("TestSrv")
        item0.setData(Qt.ItemDataRole.UserRole, "TestSrv")
        self.obj._mcp_table.setRowCount(1)
        self.obj._mcp_table.setItem(0, 0, item0)
        self.obj._mcp_table.setCurrentCell(0, 0)
        self.obj._mcp_refresh = MagicMock()
        self.obj._mcp_detail_text = MagicMock()

        mock_client = MagicMock()
        mock_client.servers = {"TestSrv": MagicMock()}

        with patch.object(self.obj, "_get_mcp_client", return_value=mock_client), \
             patch("tabs.mcp_client_tab.QMessageBox.question", return_value=16384), \
             patch("config.save_settings"):
            self.obj._mcp_remove_selected()

        self.assertEqual(logged, ["MCP server removed: TestSrv"])


if __name__ == "__main__":
    unittest.main()

