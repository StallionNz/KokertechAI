"""
Tests for tabs/tool_audit_tab.py - Tool-Call Audit Trail tab mixin.
"""
import unittest
from unittest.mock import MagicMock, patch

from tabs.context import DashboardContext

class _MockParent:
    pass

class TestTaGetRegistry(unittest.TestCase):
    def test_returns_none_on_import_error(self):
        from tabs.tool_audit_tab import ToolAuditTabMixin
        parent = _MockParent()
        import builtins
        orig = builtins.__import__
        def fake(name, *a, **kw):
            if name == "plugin_registry":
                raise ImportError("No")
            return orig(name, *a, **kw)
        with patch("builtins.__import__", side_effect=fake):
            r = ToolAuditTabMixin._ta_get_registry(parent)
            self.assertIsNone(r)

class TestTaRefreshTable(unittest.TestCase):
    def setUp(self):
        from tabs.tool_audit_tab import ToolAuditTabMixin
        self.T = ToolAuditTabMixin
        self.p = _MockParent()
        self.p._ta_table = MagicMock()
        self.p._ta_cmd_filter = MagicMock()
        self.p._ta_stats_label = MagicMock()
        self.p._ta_cmd_filter.currentText = MagicMock(return_value="-- All --")
        self.p._ta_cmd_filter.findText = MagicMock(return_value=-1)

    def test_no_registry(self):
        self.p._ta_get_registry = MagicMock(return_value=None)
        self.T._ta_refresh_table(self.p)
        self.p._ta_stats_label.setText.assert_called_with("Plugin registry unavailable")

    def test_populates_filter(self):
        reg = MagicMock()
        reg.plugins = {"A": "o1", "B": "o2"}
        reg.get_execution_log = MagicMock(return_value=[])
        self.p._ta_get_registry = MagicMock(return_value=reg)
        self.T._ta_refresh_table(self.p)
        self.assertEqual(len(self.p._ta_cmd_filter.addItem.call_args_list), 3)

    def test_stats(self):
        reg = MagicMock()
        reg.plugins = {}
        reg.get_execution_log = MagicMock(return_value=[
            {"ts": "12:00:01", "command": "X", "params": "p",
             "result": "ok", "success": True, "duration_ms": 100},
            {"ts": "12:00:02", "command": "Y", "params": "p",
             "result": "e", "success": False, "duration_ms": 200},
        ])
        self.p._ta_get_registry = MagicMock(return_value=reg)
        self.T._ta_refresh_table(self.p)
        self.p._ta_stats_label.setText.assert_called_with(
            "Total: 2 | Success: 1 | Failed: 1 | Avg ms: 150"
        )

    def test_filter_applied(self):
        self.p._ta_cmd_filter.currentText = MagicMock(return_value="SEARCH")
        reg = MagicMock()
        reg.plugins = {"SEARCH": "o"}
        reg.get_execution_log = MagicMock(return_value=[])
        self.p._ta_get_registry = MagicMock(return_value=reg)
        self.T._ta_refresh_table(self.p)
        reg.get_execution_log.assert_called_with(limit=200, cmd_filter="SEARCH")

class TestTaOnSelectionChanged(unittest.TestCase):
    def setUp(self):
        self.p = _MockParent()
        self.p._ta_table = MagicMock()
        self.p._ta_detail_text = MagicMock()

    def test_negative_row(self):
        from tabs.tool_audit_tab import ToolAuditTabMixin
        ToolAuditTabMixin._ta_on_selection_changed(self.p, -1, 0)
        self.p._ta_detail_text.setPlainText.assert_called_with(
            "Select an entry to see full details")

    def test_shows_entry(self):
        from tabs.tool_audit_tab import ToolAuditTabMixin
        entry = {"ts": "12:00", "command": "C", "duration_ms": 150,
                 "success": True, "params": "p", "result": "ok"}
        item = MagicMock()
        item.data = MagicMock(return_value=entry)
        self.p._ta_table.item = MagicMock(return_value=item)
        self.p._ta_get_registry = MagicMock(return_value=None)
        ToolAuditTabMixin._ta_on_selection_changed(self.p, 0, 0)
        t = self.p._ta_detail_text.setPlainText.call_args[0][0]
        self.assertIn("C", t)
        self.assertIn("Yes", t)

class TestTaClearLog(unittest.TestCase):
    def setUp(self):
        self.p = _MockParent()
        self.p._ta_table = MagicMock()
        self.p._ta_cmd_filter = MagicMock()
        self.p._ta_stats_label = MagicMock()
        self.p.log_to_audit = MagicMock()
        # DashboardContext migration: _ta_clear_log logs via self.context.log,
        # which dispatches to log_to_audit.
        self.p.context = DashboardContext(log_to_audit=self.p.log_to_audit)

    def test_clears(self):
        from tabs.tool_audit_tab import ToolAuditTabMixin
        reg = MagicMock()
        reg.plugins = {}
        self.p._ta_get_registry = MagicMock(return_value=reg)
        self.p._ta_refresh_table = MagicMock()
        ToolAuditTabMixin._ta_clear_log(self.p)
        reg.clear_execution_log.assert_called_once()
        self.p._ta_refresh_table.assert_called_once()

    def test_no_registry(self):
        from tabs.tool_audit_tab import ToolAuditTabMixin
        self.p._ta_get_registry = MagicMock(return_value=None)
        self.p._ta_refresh_table = MagicMock()
        ToolAuditTabMixin._ta_clear_log(self.p)
        self.p._ta_refresh_table.assert_not_called()

class TestTaAutoRefresh(unittest.TestCase):
    def test_start(self):
        from tabs.tool_audit_tab import ToolAuditTabMixin
        p = _MockParent()
        p._ta_refresh_timer = MagicMock()
        ToolAuditTabMixin._ta_on_auto_refresh_toggle(p, True)
        p._ta_refresh_timer.start.assert_called_with(3000)

    def test_stop(self):
        from tabs.tool_audit_tab import ToolAuditTabMixin
        p = _MockParent()
        p._ta_refresh_timer = MagicMock()
        ToolAuditTabMixin._ta_on_auto_refresh_toggle(p, False)
        p._ta_refresh_timer.stop.assert_called_once()

class TestToolAuditModule(unittest.TestCase):
    def test_imports(self):
        import tabs.tool_audit_tab
        self.assertTrue(hasattr(tabs.tool_audit_tab, "ToolAuditTabMixin"))

    def test_methods(self):
        from tabs.tool_audit_tab import ToolAuditTabMixin
        for m in ["create_tool_audit_tab", "_ta_get_registry",
                  "_ta_refresh_table", "_ta_on_selection_changed",
                  "_ta_clear_log", "_ta_on_auto_refresh_toggle"]:
            self.assertTrue(hasattr(ToolAuditTabMixin, m), f"Missing {m}")

if __name__ == "__main__":
    unittest.main()
