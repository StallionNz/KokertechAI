"""test_sprint13_ui_wiring.py - Integration tests for Sprint 13 API wiring in UI tabs.

Tests exercise the actual UI methods that call into memory_vault functions:
  1. GlobalSearchDialog._display_results() - fts_snippet wiring
  2. NeuralGraphTabMixin._export_graph_json() - export_graph wiring
  3. NeuralGraphTabMixin._import_graph_json() - import_graph wiring
  4. NeuralGraphTabMixin._show_communities() - detect_communities wiring

Run: python -m pytest tests/test_sprint13_ui_wiring.py -v
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Imported at module top because tests use QMessageBox.StandardButton.Yes / No
# directly as patched return values for question() in TestImportGraphJson.
from PyQt6.QtWidgets import QMessageBox  # noqa: I202  -- module-level import intentional

# Embedding-load gate handled by conftest.py pytest_configure() which sets
# KOKERTECH_SKIP_EMBEDDINGS=1 before collection begins.

import memory_vault


# Qt setup (headless-safe)
# Uses QApplication([]) (empty argv), NOT QApplication(sys.argv), so pytest's
# CLI arguments are never parsed by Qt. The conftest's session-scoped
# _ensure_qapp fixture creates the QApp before collection, so this simply
# grabs a reference to the existing instance.
def _ensure_qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

# Bind QApplication singleton to module-level reference so it survives GC.
# Without this anchor, Python GC reclaims the C++ QApplication after the
# function returns, causing "QWidget: Must construct a QApplication" errors
# on tests that instantiate real Qt widgets after the first mocked test run.
_APP_REF = _ensure_qapp()


# Base mixin for NeuralGraphTab tests
class _FakeGraphTab:
    """Minimal stand-in that satisfies NeuralGraphTabMixin attribute access.

    graph_scene is a MagicMock rather than a real QGraphicsScene so that
    Python GC at process exit does NOT trip PyQt6's sip-window-class
    destructor on Windows (observed as SIGSEGV/STATUS_ACCESS_VIOLATION).
    None of the _export_graph_json / _import_graph_json / _show_communities
    call sites touch graph_scene, so MagicMock is a faithful stand-in.
    """
    def __init__(self):
        self.graph_scene = MagicMock()
        self._cached_pos = {}
        self._cached_nodes = {}
        self._cached_links = []
        self._expand_focus_id = None
        self._edge_items = {}
        self._label_items = {}
        self._collapsed_clusters = set()
        self._cluster_collapse_labels = {}
        self._type_nodes = {}
        self._active_animations = []
        self.btn_show_all = MagicMock()
        self.btn_edit_node = MagicMock()
        self.btn_delete_node = MagicMock()
        self.btn_expand_node = MagicMock()
        self.graph_info_panel = MagicMock()
        self.graph_search = MagicMock()
        self.graph_search_count = MagicMock()
        self.graph_minimap = MagicMock()
        self.graph_view = MagicMock()
        self.zoom_label = MagicMock()
        self.current_node_id = None
        self.current_node_content = ""
        self._has_search_active = False
        self._type_visibility = {}
        self._type_count_labels = {}
        self._type_checkboxes = {}
        self._hide_clusters_cb = MagicMock()
        self._drag_call_count = 0

    def log_to_audit(self, msg):
        self._last_audit = msg

    def render_knowledge_graph(self):
        self._render_called = True


# 1. GlobalSearchDialog._display_results - fts_snippet wiring

class TestGlobalSearchFtsSnippetWiring(unittest.TestCase):
    """Verify that _display_results calls memory_vault.fts_snippet."""

    def setUp(self):
        from tabs.global_search import GlobalSearchDialog
        self.dialog = GlobalSearchDialog(parent=None, controller=None)
        self.dialog.search_input = MagicMock()
        self.dialog.search_input.text.return_value = "Python"

    def tearDown(self):
        self.dialog.close()

    @patch("memory_vault.fts_snippet")
    def test_uses_fts_snippet_for_vault_results(self, mock_fts):
        """fts_snippet should be called for vault/episodic results."""
        mock_fts.return_value = "...Python is used for AI..."
        results = [
            {"id": 1, "source": "vault", "content": "Python is a language used for AI",
             "score": 0.9, "importance": 5, "timestamp": "", "tags": "[]"},
        ]
        self.dialog._display_results(results)
        mock_fts.assert_called_once_with(
            "Python is a language used for AI", "Python", max_chars=150,
        )
        tree = self.dialog.result_tree
        self.assertGreater(tree.invisibleRootItem().childCount(), 0)

    @patch("memory_vault.fts_snippet")
    def test_uses_fts_snippet_for_session_results(self, mock_fts):
        mock_fts.return_value = "...Python rocks..."
        results = [
            {"id": 0, "source": "session", "content": "I love Python because it rocks",
             "score": 0.9, "importance": 5, "timestamp": "", "tags": "[]", "role": "user"},
        ]
        self.dialog._display_results(results)
        mock_fts.assert_called_once()

    @patch("memory_vault.fts_snippet")
    def test_uses_fts_snippet_for_episodic_results(self, mock_fts):
        mock_fts.return_value = "...episodic context..."
        results = [
            {"id": 5, "source": "episodic", "content": "Meeting notes about Python",
             "score": 0.85, "importance": 5, "timestamp": "", "tags": "[]"},
        ]
        self.dialog._display_results(results)
        mock_fts.assert_called_once()
        tree = self.dialog.result_tree
        self.assertGreater(tree.invisibleRootItem().childCount(), 0)

    @patch("memory_vault.fts_snippet")
    def test_empty_results_shows_no_results(self, mock_fts):
        self.dialog._display_results([])
        self.assertEqual(self.dialog.result_count_label.text(), "No results")
        mock_fts.assert_not_called()

    @patch("memory_vault.fts_snippet")
    def test_fallback_when_fts_snippet_raises(self, mock_fts):
        mock_fts.side_effect = RuntimeError("DB connection lost")
        content = "A" * 200
        results = [
            {"id": 1, "source": "vault", "content": content,
             "score": 0.8, "importance": 5, "timestamp": "", "tags": "[]"},
        ]
        self.dialog._display_results(results)
        root = self.dialog.result_tree.invisibleRootItem()
        self.assertGreater(root.childCount(), 0)

    @patch("memory_vault.fts_snippet")
    def test_fallback_when_fts_snippet_returns_empty(self, mock_fts):
        mock_fts.return_value = ""
        results = [
            {"id": 1, "source": "vault", "content": "Some content",
             "score": 0.7, "importance": 5, "timestamp": "", "tags": "[]"},
        ]
        self.dialog._display_results(results)

    @patch("memory_vault.fts_snippet")
    def test_result_count_matches_items(self, mock_fts):
        mock_fts.return_value = "snippet"
        results = [
            {"id": 1, "source": "session", "content": "a", "score": 0.9,
             "importance": 5, "timestamp": "", "tags": "[]", "role": "user"},
            {"id": 2, "source": "vault", "content": "b", "score": 0.8,
             "importance": 5, "timestamp": "", "tags": "[]"},
        ]
        self.dialog._display_results(results)
        label_text = self.dialog.result_count_label.text()
        self.assertTrue(label_text.endswith(" results"))
        count = int(label_text.split()[0])
        self.assertEqual(count, 2)


# 2-4. NeuralGraphTabMixin - export JSON, import JSON, communities

from tabs.neural_graph_tab import NeuralGraphTabMixin


class _GraphTabHarness(_FakeGraphTab, NeuralGraphTabMixin):
    """Combine the mixin with the fake stand-in for testing.

    MRO rationale: _FakeGraphTab is listed FIRST so that
    _FakeGraphTab.render_knowledge_graph (the no-op stub that marks
    `_render_called = True`) takes precedence over
    NeuralGraphTabMixin.render_knowledge_graph. The mixin's real version
    starts a GraphLayoutWorker QThread that hits the real SQLite vault
    and the real networkx spring layout -- activity that is irrelevant
    to these tests and causes (a) assertion failure on
    `assertTrue(_render_called)` in test_imports_and_renders, and
    (b) Windows STATUS_ACCESS_VIOLATION at process exit when the
    background thread is killed mid-cleanup. Listing the fake first
    keeps the threaded code path dormant while mixin-specific methods
    (_export_graph_json / _import_graph_json / _show_communities) still
    resolve correctly because they are unique to the mixin.
    """
    pass


class TestExportGraphJson(unittest.TestCase):
    """Test _export_graph_json wiring to memory_vault.export_graph."""

    def setUp(self):
        self.tab = _GraphTabHarness()

    @patch("memory_vault.export_graph")
    def test_writes_json_file(self, mock_export):
        mock_export.return_value = {
            "format": "kokertech-kg-v1", "entity_count": 2,
            "relationship_count": 1,
            "entities": [{"name": "A"}, {"name": "B"}],
            "relationships": [{"type": "LINKS"}],
            "exported_at": "2026-06-26",
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
            tmp_path = tmp.name
        try:
            with patch("tabs.neural_graph_tab.QFileDialog.getSaveFileName",
                        return_value=(tmp_path, "JSON (*.json)")):
                self.tab._export_graph_json()
            with open(tmp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["format"], "kokertech-kg-v1")
            self.assertEqual(data["entity_count"], 2)
            mock_export.assert_called_once()
            self.assertIn("Exported KG", self.tab._last_audit)
        finally:
            os.unlink(tmp_path)

    @patch("memory_vault.export_graph")
    def test_user_cancels_dialog(self, mock_export):
        with patch("tabs.neural_graph_tab.QFileDialog.getSaveFileName",
                    return_value=("", "")):
            self.tab._export_graph_json()
        mock_export.assert_called_once()
        # Verify no audit was logged (no export completed)
        self.assertFalse(getattr(self.tab, "_last_audit", None))

    @patch("memory_vault.export_graph")
    def test_export_failure_shows_error(self, mock_export):
        mock_export.side_effect = RuntimeError("DB locked")
        with patch("tabs.neural_graph_tab.QMessageBox.critical") as mock_err:
            self.tab._export_graph_json()
        mock_err.assert_called_once()
        # Verify error dialog mentions export
        err_msg = mock_err.call_args[0][1]
        self.assertIn("export", err_msg.lower())

    @patch("memory_vault.export_graph")
    def test_export_empty_graph(self, mock_export):
        mock_export.return_value = {
            "format": "kokertech-kg-v1", "entity_count": 0,
            "relationship_count": 0, "entities": [], "relationships": [],
            "exported_at": "2026-06-26",
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
            tmp_path = tmp.name
        try:
            with patch("tabs.neural_graph_tab.QFileDialog.getSaveFileName",
                        return_value=(tmp_path, "JSON (*.json)")):
                self.tab._export_graph_json()
            with open(tmp_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["entity_count"], 0)
            self.assertEqual(len(data["entities"]), 0)
        finally:
            os.unlink(tmp_path)


class TestImportGraphJson(unittest.TestCase):
    """Test _import_graph_json wiring to memory_vault.import_graph."""

    def setUp(self):
        self.tab = _GraphTabHarness()

    @patch("memory_vault.import_graph")
    def test_imports_and_renders(self, mock_import):
        mock_import.return_value = {"entities_imported": 3, "relationships_imported": 2}
        payload = {
            "entities": [{"name": "X"}, {"name": "Y"}, {"name": "Z"}],
            "relationships": [{"source": 1, "target": 2}],
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as tmp:
            json.dump(payload, tmp)
            tmp_path = tmp.name
        try:
            with patch("tabs.neural_graph_tab.QFileDialog.getOpenFileName",
                        return_value=(tmp_path, "JSON (*.json)")), \
                 patch("tabs.neural_graph_tab.QMessageBox.question",
                        return_value=QMessageBox.StandardButton.Yes):
                self.tab._import_graph_json()
            mock_import.assert_called_once_with(payload)
            self.assertTrue(getattr(self.tab, "_render_called", False))
        finally:
            os.unlink(tmp_path)

    @patch("memory_vault.import_graph")
    def test_user_cancels_file_dialog(self, mock_import):
        with patch("tabs.neural_graph_tab.QFileDialog.getOpenFileName",
                    return_value=("", "")):
            self.tab._import_graph_json()
        mock_import.assert_not_called()

    @patch("memory_vault.import_graph")
    def test_user_declines_confirmation(self, mock_import):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as tmp:
            json.dump({"entities": [], "relationships": []}, tmp)
            tmp_path = tmp.name
        try:
            with patch("tabs.neural_graph_tab.QFileDialog.getOpenFileName",
                        return_value=(tmp_path, "JSON")), \
                 patch("tabs.neural_graph_tab.QMessageBox.question",
                       return_value=MagicMock(value=False)):
                self.tab._import_graph_json()
            mock_import.assert_not_called()
        finally:
            os.unlink(tmp_path)

    @patch("memory_vault.import_graph")
    def test_corrupt_json_shows_error(self, mock_import):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as tmp:
            tmp.write("{not valid json!!!")
            tmp_path = tmp.name
        try:
            with patch("tabs.neural_graph_tab.QFileDialog.getOpenFileName",
                        return_value=(tmp_path, "JSON")), \
                 patch("tabs.neural_graph_tab.QMessageBox.critical") as mock_err:
                self.tab._import_graph_json()
            mock_err.assert_called_once()
            mock_import.assert_not_called()
        finally:
            os.unlink(tmp_path)

    @patch("memory_vault.import_graph")
    def test_import_failure_shows_error(self, mock_import):
        mock_import.side_effect = RuntimeError("Table missing")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as tmp:
            json.dump({"entities": [{"name": "A"}], "relationships": []}, tmp)
            tmp_path = tmp.name
        try:
            with patch("tabs.neural_graph_tab.QFileDialog.getOpenFileName",
                        return_value=(tmp_path, "JSON")), \
                 patch("tabs.neural_graph_tab.QMessageBox.question",
                       return_value=QMessageBox.StandardButton.Yes), \
                 patch("tabs.neural_graph_tab.QMessageBox.critical") as mock_err:
                self.tab._import_graph_json()
            mock_err.assert_called_once()
            # Verify error dialog mentions import
            err_msg = mock_err.call_args[0][1]
            self.assertIn("import", err_msg.lower())
        finally:
            os.unlink(tmp_path)

    @patch("memory_vault.import_graph")
    def test_confirmation_shows_counts(self, mock_import):
        mock_import.return_value = {"entities_imported": 5, "relationships_imported": 3}
        payload = {
            "entities": [{"name": "E" + str(i)} for i in range(5)],
            "relationships": [{"source": i, "target": i + 1} for i in range(3)],
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as tmp:
            json.dump(payload, tmp)
            tmp_path = tmp.name
        try:
            with patch("tabs.neural_graph_tab.QFileDialog.getOpenFileName",
                        return_value=(tmp_path, "JSON")), \
                 patch("tabs.neural_graph_tab.QMessageBox.question") as mock_q:
                mock_q.return_value = MagicMock(value=True)
                self.tab._import_graph_json()
            call_args = mock_q.call_args[0]
            self.assertIn("5", str(call_args))
            self.assertIn("3", str(call_args))
        finally:
            os.unlink(tmp_path)


class TestShowCommunities(unittest.TestCase):
    """Test _show_communities wiring to memory_vault.detect_communities."""

    def setUp(self):
        self.tab = _GraphTabHarness()

    @patch("memory_vault.detect_communities")
    def test_displays_communities(self, mock_detect):
        mock_detect.return_value = [
            {"id": 1, "size": 3, "members": [1, 2, 3],
             "entities": [
                 {"name": "Alpha", "entity_type": "concept"},
                 {"name": "Beta", "entity_type": "concept"},
                 {"name": "Gamma", "entity_type": "concept"},
             ]},
        ]
        with patch("tabs.neural_graph_tab.QDialog") as MockDialog, \
             patch("tabs.neural_graph_tab.QListWidget"), \
             patch("tabs.neural_graph_tab.QVBoxLayout"), \
             patch("tabs.neural_graph_tab.QDialogButtonBox"):
            MockDialog.return_value.exec.return_value = True
            self.tab._show_communities()
        mock_detect.assert_called_once_with(min_cluster_size=3)

    @patch("memory_vault.detect_communities")
    def test_empty_communities_shows_info(self, mock_detect):
        mock_detect.return_value = []
        with patch("tabs.neural_graph_tab.QMessageBox.information") as mock_info:
            self.tab._show_communities()
        mock_info.assert_called_once()
        self.assertIn("No communities", mock_info.call_args[0][2])

    @patch("memory_vault.detect_communities")
    def test_detect_failure_shows_warning(self, mock_detect):
        mock_detect.side_effect = RuntimeError("Missing table")
        with patch("tabs.neural_graph_tab.QMessageBox.warning") as mock_warn:
            self.tab._show_communities()
        mock_warn.assert_called_once()        # Verify warning dialog mentions failure
        warn_msg = mock_warn.call_args[0][2]
        self.assertIn("Failed", warn_msg)

    @patch("memory_vault.detect_communities")
    def test_multiple_communities_all_shown(self, mock_detect):
        mock_detect.return_value = [
            {"id": 1, "size": 3, "members": [1, 2, 3],
             "entities": [{"name": "A" + str(i), "entity_type": "concept"} for i in range(3)]},
            {"id": 2, "size": 4, "members": [4, 5, 6, 7],
             "entities": [{"name": "B" + str(i), "entity_type": "tool"} for i in range(4)]},
        ]
        added_items = []
        def capture_addItem(item):
            added_items.append(item)
        with patch("tabs.neural_graph_tab.QDialog") as MockDialog, \
             patch("tabs.neural_graph_tab.QListWidget") as MockList, \
             patch("tabs.neural_graph_tab.QVBoxLayout"), \
             patch("tabs.neural_graph_tab.QDialogButtonBox"):
            MockDialog.return_value.exec.return_value = True
            MockList.return_value.addItem.side_effect = capture_addItem
            self.tab._show_communities()
        self.assertEqual(len(added_items), 2)

    @patch("memory_vault.detect_communities")
    def test_uses_default_min_cluster_size(self, mock_detect):
        mock_detect.return_value = [
            {"id": 1, "size": 3, "members": [1, 2, 3],
             "entities": [{"name": "X", "entity_type": "concept"},
                          {"name": "Y", "entity_type": "concept"},
                          {"name": "Z", "entity_type": "concept"}]},
        ]
        with patch("tabs.neural_graph_tab.QDialog") as MockDialog, \
             patch("tabs.neural_graph_tab.QListWidget"), \
             patch("tabs.neural_graph_tab.QVBoxLayout"), \
             patch("tabs.neural_graph_tab.QDialogButtonBox"):
            MockDialog.return_value.exec.return_value = True
            self.tab._show_communities()
        mock_detect.assert_called_once_with(min_cluster_size=3)


if __name__ == "__main__":
    unittest.main()
