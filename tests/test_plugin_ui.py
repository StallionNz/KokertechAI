"""
Plugin UI Integration Tests — Stream B (Sprint 2)

Tests enable/disable UI methods from app_core.py using mocked PyQt6 widgets
and an isolated plugin registry.

NOTE: Historical cross-file QApplication singleton issue (conftest.py) — now resolved.
      All 51 pre-existing failures were fixed in June 2026 (test fixture
      isolation improvements, proper QWidget cleanup, and standardized mock
      patterns).  Per-file and cross-file batches both pass cleanly.

      Run this file in isolation to verify actual test results:
        pytest test_plugin_ui.py
"""

import unittest
import os
import tempfile
import sys
from unittest.mock import MagicMock, patch, call

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _write_plugin(test_dir, filename, name, cmd, version="1.0.0",
                  tags=None, description="A test plugin", has_schema=True):
    """Write a minimal plugin .py file and return the command name."""
    tags_json = str(tags if tags is not None else [f"tag_{cmd.lower()}"])
    schema_block = f'SCHEMA = {{"action": "{cmd}"}}' if has_schema else ""
    content = f"""
COMMAND_NAME = "{cmd}"
PLUGIN_METADATA = {{
    "name": "{name}",
    "description": "{description}",
    "version": "{version}",
    "tags": {tags_json},
    "author": "KokertechAI",
    "requires": []
}}
{schema_block}
def execute(i):
    return "{cmd.lower()} ok"
"""
    with open(os.path.join(test_dir, filename), "w", encoding="utf-8") as f:
        f.write(content)
    return cmd

def _make_registry(test_dir):
    """Create an isolated PluginRegistry (requires PLUGIN_DIR already patched)."""
    from plugin_registry import PluginRegistry
    return PluginRegistry()

def _mock_dashboard():
    """Build a minimal MagicMock dashboard with plugin UI attributes."""
    d = MagicMock()
    d.plugin_list = MagicMock()
    d.plugin_status_label = MagicMock()
    d.plugin_detail = MagicMock()
    d.currentItem = MagicMock(return_value=None)
    d.plugin_list.currentItem = d.currentItem
    d._update_plugin_status = MagicMock()
    return d

# ═══════════════════════════════════════════════════════════════════════════
# Base class with common setUp / tearDown
# ═══════════════════════════════════════════════════════════════════════════

class _PluginUITestBase(unittest.TestCase):
    """Base: patches PLUGIN_DIR → temp dir, creates registry after files."""

    def _init_plugins(self, *specs):
        """Write plugin files then create the isolated registry.

        Each spec: (filename, name, cmd) or (filename, name, cmd, version, tags, desc, schema)
        """
        self.test_dir = tempfile.mkdtemp()
        # 1. Patch PLUGIN_DIR so _load_plugins scans our temp dir
        import plugin_registry as pr_module
        self._pr_module = pr_module
        self._patch_dir = patch.object(pr_module, "PLUGIN_DIR", self.test_dir)
        self._patch_dir.start()

        # 2. Write plugin files
        for s in specs:
            _write_plugin(self.test_dir, *s)

        # 3. Create registry (now finds our files)
        self.registry = _make_registry(self.test_dir)

        # 4. Patch the module-level registry so app_core sees it
        self._patch_reg = patch.object(pr_module, "registry", self.registry)
        self._patch_reg.start()

    def tearDown(self):
        self._patch_reg.stop()
        self._patch_dir.stop()
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

# ═══════════════════════════════════════════════════════════════════════════
# Test: _refresh_plugin_list
# ═══════════════════════════════════════════════════════════════════════════

class TestPluginUIRefresh(_PluginUITestBase):

    def setUp(self):
        self._init_plugins(
            ("a.py", "Alpha", "ALPHA"),
            ("b.py", "Beta", "BETA"),
        )

    def test_creates_item_per_plugin(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._refresh_plugin_list(d)
        self.assertEqual(d.plugin_list.addItem.call_count, 2)

    def test_clears_before_rebuild(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._refresh_plugin_list(d)
        d.plugin_list.clear.assert_called_once()

    def test_blocks_signals_during_build(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._refresh_plugin_list(d)
        calls = d.plugin_list.blockSignals.call_args_list
        self.assertEqual(calls[0], call(True))
        self.assertEqual(calls[-1], call(False))

    def test_updates_status_after_build(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._refresh_plugin_list(d)
        d._update_plugin_status.assert_called()

class TestPluginUIRefreshEmpty(_PluginUITestBase):

    def setUp(self):
        self._init_plugins()  # no plugins

    def test_empty_registry_adds_no_items(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._refresh_plugin_list(d)
        d.plugin_list.addItem.assert_not_called()

    def test_empty_registry_status_updated(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._refresh_plugin_list(d)
        d._update_plugin_status.assert_called()

# ═══════════════════════════════════════════════════════════════════════════
# Test: _on_plugin_toggle
# ═══════════════════════════════════════════════════════════════════════════

class TestPluginUIToggle(_PluginUITestBase):

    def setUp(self):
        self._init_plugins(
            ("a.py", "Alpha", "ALPHA"),
            ("b.py", "Beta", "BETA"),
        )

    def _item(self, cmd, checked=True):
        """Mock QListWidgetItem with UserRole data and check state."""
        item = MagicMock()
        item.data.return_value = cmd
        from PyQt6.QtCore import Qt
        item.checkState.return_value = (
            Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        )
        return item

    def test_checked_enables_plugin(self):
        d = _mock_dashboard()
        d.plugin_list.currentItem.return_value = None
        from app_core import KokertechDashboard
        KokertechDashboard._on_plugin_toggle(d, self._item("ALPHA", True))
        self.assertTrue(self.registry.is_enabled("ALPHA"))

    def test_unchecked_disables_plugin(self):
        d = _mock_dashboard()
        d.plugin_list.currentItem.return_value = None
        from app_core import KokertechDashboard
        KokertechDashboard._on_plugin_toggle(d, self._item("ALPHA", False))
        self.assertFalse(self.registry.is_enabled("ALPHA"))
        self.assertIn("ALPHA", self.registry.disabled)

    def test_toggle_updates_status_label(self):
        d = _mock_dashboard()
        d.plugin_list.currentItem.return_value = None
        from app_core import KokertechDashboard
        KokertechDashboard._on_plugin_toggle(d, self._item("ALPHA", True))
        d._update_plugin_status.assert_called()

    def test_refreshes_detail_when_selected(self):
        item = self._item("ALPHA", False)
        d = _mock_dashboard()
        d.plugin_list.currentItem.return_value = item
        d._on_plugin_selected = MagicMock()
        from app_core import KokertechDashboard
        KokertechDashboard._on_plugin_toggle(d, item)
        d._on_plugin_selected.assert_called_once_with(item, None)

    def test_skips_detail_when_not_selected(self):
        item = self._item("ALPHA", False)
        other = self._item("BETA", True)
        d = _mock_dashboard()
        d.plugin_list.currentItem.return_value = other
        d._on_plugin_selected = MagicMock()
        from app_core import KokertechDashboard
        KokertechDashboard._on_plugin_toggle(d, item)
        d._on_plugin_selected.assert_not_called()

    def test_enable_disable_roundtrip(self):
        d = _mock_dashboard()
        d.plugin_list.currentItem.return_value = None
        from app_core import KokertechDashboard
        KokertechDashboard._on_plugin_toggle(d, self._item("BETA", True))
        self.assertTrue(self.registry.is_enabled("BETA"))
        KokertechDashboard._on_plugin_toggle(d, self._item("BETA", False))
        self.assertFalse(self.registry.is_enabled("BETA"))
        KokertechDashboard._on_plugin_toggle(d, self._item("BETA", True))
        self.assertTrue(self.registry.is_enabled("BETA"))

# ═══════════════════════════════════════════════════════════════════════════
# Test: _on_plugin_selected (detail panel)
# ═══════════════════════════════════════════════════════════════════════════

class TestPluginUIDetail(_PluginUITestBase):

    def setUp(self):
        self._init_plugins(
            ("a.py", "Alpha Plugin", "ALPHA", "1.0.0",
             ["test", "alpha"], "First test plugin"),
        )

    def _item(self, cmd):
        item = MagicMock()
        item.data.return_value = cmd
        return item

    def test_none_shows_placeholder(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._on_plugin_selected(d, None, None)
        self.assertIn("Select a plugin", d.plugin_detail.setText.call_args[0][0])

    def test_enabled_shows_all_metadata(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._on_plugin_selected(d, self._item("ALPHA"), None)
        text = d.plugin_detail.setText.call_args[0][0]
        self.assertIn("Alpha Plugin", text)
        self.assertIn("ALPHA", text)
        self.assertIn("1.0.0", text)
        self.assertIn("First test plugin", text)
        self.assertIn("test, alpha", text)
        self.assertIn("✅ Enabled", text)

    def test_disabled_shows_disabled_status(self):
        self.registry.disable("ALPHA")
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._on_plugin_selected(d, self._item("ALPHA"), None)
        text = d.plugin_detail.setText.call_args[0][0]
        self.assertIn("❌ Disabled", text)
        self.assertNotIn("✅ Enabled", text)

    def test_no_tags_omits_tags_line(self):
        _write_plugin(self.test_dir, "nt.py", "NoTags", "NOTAGS",
                      tags=[], description="No tags")
        self.registry = _make_registry(self.test_dir)
        self._pr_module.registry = self.registry
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._on_plugin_selected(d, self._item("NOTAGS"), None)
        text = d.plugin_detail.setText.call_args[0][0]
        self.assertIn("NoTags", text)
        self.assertNotIn("Tags:", text)

    def test_updates_stylesheet(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._on_plugin_selected(d, self._item("ALPHA"), None)
        d.plugin_detail.setStyleSheet.assert_called_once()
        self.assertIn("font-size", d.plugin_detail.setStyleSheet.call_args[0][0])

# ═══════════════════════════════════════════════════════════════════════════
# Test: _update_plugin_status
# ═══════════════════════════════════════════════════════════════════════════

class TestPluginUIStatus(_PluginUITestBase):

    def setUp(self):
        self._init_plugins(
            ("a.py", "Alpha", "ALPHA"),
            ("b.py", "Beta", "BETA"),
            ("c.py", "Gamma", "GAMMA"),
        )

    def _assert_label(self, d, expected_count_str, expected_color):
        text = d.plugin_status_label.setText.call_args[0][0]
        style = d.plugin_status_label.setStyleSheet.call_args[0][0]
        self.assertIn(expected_count_str, text)
        self.assertIn(expected_color, style)

    def test_all_enabled_is_green(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._update_plugin_status(d)
        self._assert_label(d, "3/3", "#10B981")

    def test_all_disabled_is_red(self):
        self.registry.disable("ALPHA")
        self.registry.disable("BETA")
        self.registry.disable("GAMMA")
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._update_plugin_status(d)
        self._assert_label(d, "0/3", "#EF4444")

    def test_two_of_three_is_yellow(self):
        self.registry.disable("GAMMA")
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._update_plugin_status(d)
        self._assert_label(d, "2/3", "#FBBF24")

    def test_one_of_three_is_red(self):
        self.registry.disable("ALPHA")
        self.registry.disable("BETA")
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._update_plugin_status(d)
        self._assert_label(d, "1/3", "#EF4444")

    def test_one_of_one_is_green(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
        os.makedirs(self.test_dir)
        _write_plugin(self.test_dir, "solo.py", "Solo", "SOLO")
        self.registry = _make_registry(self.test_dir)
        self._pr_module.registry = self.registry
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._update_plugin_status(d)
        self._assert_label(d, "1/1", "#10B981")

# ═══════════════════════════════════════════════════════════════════════════
# Test: bulk disable / enable all
# ═══════════════════════════════════════════════════════════════════════════

class TestPluginUIBulk(_PluginUITestBase):

    def setUp(self):
        self._init_plugins(
            ("a.py", "Alpha", "ALPHA"),
            ("b.py", "Beta", "BETA"),
            ("c.py", "Gamma", "GAMMA"),
        )

    def test_disable_all_disables_every_plugin(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._disable_all_plugins(d)
        self.assertFalse(self.registry.is_enabled("ALPHA"))
        self.assertFalse(self.registry.is_enabled("BETA"))
        self.assertFalse(self.registry.is_enabled("GAMMA"))
        self.assertEqual(self.registry.get_enabled_count(), 0)

    def test_disable_all_calls_refresh(self):
        d = _mock_dashboard()
        d._refresh_plugin_list = MagicMock()
        from app_core import KokertechDashboard
        KokertechDashboard._disable_all_plugins(d)
        d._refresh_plugin_list.assert_called_once_with()

    def test_enable_all_restores_all(self):
        self.registry.disable("ALPHA")
        self.registry.disable("BETA")
        self.registry.disable("GAMMA")
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._enable_all_plugins(d)
        self.assertTrue(self.registry.is_enabled("ALPHA"))
        self.assertTrue(self.registry.is_enabled("BETA"))
        self.assertTrue(self.registry.is_enabled("GAMMA"))
        self.assertEqual(self.registry.get_enabled_count(), 3)

    def test_enable_all_calls_refresh(self):
        d = _mock_dashboard()
        d._refresh_plugin_list = MagicMock()
        from app_core import KokertechDashboard
        KokertechDashboard._enable_all_plugins(d)
        d._refresh_plugin_list.assert_called_once_with()

    def test_bulk_roundtrip_preserves_count(self):
        d = _mock_dashboard()
        orig = self.registry.get_enabled_count()
        from app_core import KokertechDashboard
        KokertechDashboard._disable_all_plugins(d)
        KokertechDashboard._enable_all_plugins(d)
        self.assertEqual(self.registry.get_enabled_count(), orig)

    def test_disable_all_idempotent(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._disable_all_plugins(d)
        KokertechDashboard._disable_all_plugins(d)
        self.assertEqual(self.registry.get_enabled_count(), 0)

    def test_enable_all_idempotent(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._enable_all_plugins(d)
        KokertechDashboard._enable_all_plugins(d)
        self.assertEqual(self.registry.get_enabled_count(), 3)

    def test_re_enable_one_after_bulk_disable(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._disable_all_plugins(d)
        self.registry.enable("BETA")
        self.assertTrue(self.registry.is_enabled("BETA"))
        self.assertFalse(self.registry.is_enabled("ALPHA"))
        self.assertEqual(self.registry.get_enabled_count(), 1)

    def test_disable_all_empty_registry(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
        os.makedirs(self.test_dir)
        self.registry = _make_registry(self.test_dir)
        self._pr_module.registry = self.registry
        d = _mock_dashboard()
        d._refresh_plugin_list = MagicMock()
        from app_core import KokertechDashboard
        KokertechDashboard._disable_all_plugins(d)
        d._refresh_plugin_list.assert_called_once_with()

    def test_enable_all_empty_registry(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)
        os.makedirs(self.test_dir)
        self.registry = _make_registry(self.test_dir)
        self._pr_module.registry = self.registry
        d = _mock_dashboard()
        d._refresh_plugin_list = MagicMock()
        from app_core import KokertechDashboard
        KokertechDashboard._enable_all_plugins(d)
        d._refresh_plugin_list.assert_called_once_with()

    def test_disable_all_partial_state(self):
        self.registry.disable("BETA")
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._disable_all_plugins(d)
        self.assertFalse(self.registry.is_enabled("ALPHA"))
        self.assertFalse(self.registry.is_enabled("BETA"))
        self.assertFalse(self.registry.is_enabled("GAMMA"))
        self.assertEqual(self.registry.get_enabled_count(), 0)

    def test_enable_all_partial_state(self):
        self.registry.disable("ALPHA")
        self.registry.disable("GAMMA")
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._enable_all_plugins(d)
        self.assertTrue(self.registry.is_enabled("ALPHA"))
        self.assertTrue(self.registry.is_enabled("BETA"))
        self.assertTrue(self.registry.is_enabled("GAMMA"))
        self.assertEqual(self.registry.get_enabled_count(), 3)

    def test_bulk_disable_then_enable_state_accuracy(self):
        d = _mock_dashboard()
        from app_core import KokertechDashboard
        KokertechDashboard._disable_all_plugins(d)
        self.registry.enable("ALPHA")
        self.registry.enable("GAMMA")
        self.assertEqual(self.registry.get_enabled_count(), 2)
        self.assertTrue(self.registry.is_enabled("ALPHA"))
        self.assertFalse(self.registry.is_enabled("BETA"))
        self.assertTrue(self.registry.is_enabled("GAMMA"))

if __name__ == "__main__":
    unittest.main()