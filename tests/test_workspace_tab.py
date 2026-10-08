"""Tests for tabs/workspace_tab.py -- Workspace Explorer tab mixin."""
import unittest
from unittest.mock import patch, MagicMock

class TestWorkspaceTabMixin(unittest.TestCase):
    def setUp(self):
        self._p = patch("tabs.workspace_tab.pyqtSignal", return_value=MagicMock())
        self._p.start()
    def tearDown(self):
        self._p.stop()
    def _mk(self, obj):
        with patch.object(obj, "index_workspace_directory"):
            obj._tab = obj.create_workspace_tab()

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.workspace_tab import WorkspaceTabMixin
        obj = WorkspaceTabMixin()
        self._mk(obj)
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "workspace_tree"))

    def test_tree_has_3_columns(self):
        from tabs.workspace_tab import WorkspaceTabMixin
        obj = WorkspaceTabMixin()
        self._mk(obj)
        tree = obj.workspace_tree
        self.assertEqual(tree.columnCount(), 3)
        labels = [tree.headerItem().text(i) for i in range(3)]
        self.assertEqual(labels, ["Filename", "Type", "Last Modified"])

    def test_populate_tree_fills_entries(self):
        from tabs.workspace_tab import WorkspaceTabMixin
        obj = WorkspaceTabMixin()
        self._mk(obj)
        from PyQt6.QtWidgets import QTreeWidgetItem
        obj.workspace_root_item = QTreeWidgetItem(obj.workspace_tree, ["root", "", ""])
        results = [
            {"main": ["file1.py", "Document", "2026-07-09 10:00:00"],
             "sub": [["nested.py", "Document", "2026-07-09 11:00:00"]]},
            {"main": ["subdir", "Directory", "2026-07-08 09:00:00"],
             "sub": [["child.txt", "Document", "2026-07-08 10:00:00"],
                     ["child2.py", "Document", "2026-07-08 11:00:00"]]},
        ]
        obj.populate_workspace_tree(results)
        root = obj.workspace_tree.topLevelItem(0)
        self.assertEqual(root.childCount(), 2)
        self.assertEqual(root.child(0).text(0), "file1.py")
        self.assertEqual(root.child(0).childCount(), 1)
        self.assertEqual(root.child(0).child(0).text(0), "nested.py")

    def test_populate_tree_empty_results(self):
        from tabs.workspace_tab import WorkspaceTabMixin
        obj = WorkspaceTabMixin()
        self._mk(obj)
        from PyQt6.QtWidgets import QTreeWidgetItem
        obj.workspace_root_item = QTreeWidgetItem(obj.workspace_tree, ["root", "", ""])
        obj.populate_workspace_tree([])
        root = obj.workspace_tree.topLevelItem(0)
        self.assertEqual(root.childCount(), 0)

    def test_worker_run_with_files(self):
        """WorkspaceIndexerWorker.run() indexes files and subdirectories.

        Covers lines 18-29: the main indexing loop with files and subdirs.
        """
        from tabs.workspace_tab import WorkspaceIndexerWorker

        mock_emit = MagicMock()
        with patch.object(WorkspaceIndexerWorker, "indexed_signal") as mock_sig:
            mock_sig.emit = mock_emit
            worker = WorkspaceIndexerWorker()

            with patch("os.path.exists", return_value=True), \
                 patch("os.listdir",
                       side_effect=[["file1.py", "subdir"], ["nested.txt"]]), \
                 patch("os.path.isdir",
                       side_effect=[False, True, False]), \
                 patch("os.path.getmtime", return_value=1700000000.0):
                worker.run()

        mock_emit.assert_called_once()
        results = mock_emit.call_args[0][0]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["main"][0], "file1.py")
        self.assertEqual(results[0]["main"][1], "Document")
        self.assertEqual(results[1]["main"][0], "subdir")
        self.assertEqual(results[1]["main"][1], "Directory")
        self.assertEqual(len(results[1]["sub"]), 1)
        self.assertEqual(results[1]["sub"][0][0], "nested.txt")

    def test_worker_run_empty_workspace(self):
        """WorkspaceIndexerWorker emits [] when WORKSPACE_DIR missing.

        Covers lines 13-15: early return when directory doesn't exist.
        """
        from tabs.workspace_tab import WorkspaceIndexerWorker

        mock_emit = MagicMock()
        with patch.object(WorkspaceIndexerWorker, "indexed_signal") as mock_sig:
            mock_sig.emit = mock_emit
            worker = WorkspaceIndexerWorker()

            with patch("os.path.exists", return_value=False):
                worker.run()

        mock_emit.assert_called_once_with([])

    def test_worker_run_os_error(self):
        """WorkspaceIndexerWorker catches OSError and emits empty results.

        Covers lines 30-31: the except OSError handler.
        """
        from tabs.workspace_tab import WorkspaceIndexerWorker

        mock_emit = MagicMock()
        with patch.object(WorkspaceIndexerWorker, "indexed_signal") as mock_sig:
            mock_sig.emit = mock_emit
            worker = WorkspaceIndexerWorker()

            with patch("os.path.exists", return_value=True), \
                 patch("os.listdir", side_effect=OSError("Permission denied")):
                worker.run()

        mock_emit.assert_called_once_with([])

    def test_index_workspace_directory_creates_root_item(self):
        """index_workspace_directory() clears tree, creates root item,
        and starts the indexing worker.

        Covers lines 55-58: the method body.
        """
        from tabs.workspace_tab import WorkspaceTabMixin
        from config import WORKSPACE_DIR

        obj = WorkspaceTabMixin()
        # Create the tab without running index_workspace_directory first
        with patch.object(obj, "index_workspace_directory"):
            obj._tab = obj.create_workspace_tab()

        # Now test index_workspace_directory directly
        with patch("tabs.workspace_tab.WorkspaceIndexerWorker") as mock_wc:
            mock_worker = MagicMock()
            mock_wc.return_value = mock_worker
            obj.index_workspace_directory()

        self.assertEqual(obj.workspace_tree.topLevelItemCount(), 1,
            msg="REGRESSION: should have exactly one root item")
        self.assertEqual(obj.workspace_root_item.text(0), WORKSPACE_DIR,
            msg="REGRESSION: root item text should be WORKSPACE_DIR")
        mock_wc.assert_called_once()
        mock_worker.start.assert_called_once()
        mock_worker.indexed_signal.connect.assert_called_once_with(
            obj.populate_workspace_tree
        )
        mock_worker.finished.connect.assert_called_once()

    def test_workspace_tab_widgets_are_real_qt_objects(self):
        """create_workspace_tab() builds a QWidget with all expected
        UI elements as real Qt widgets."""
        from PyQt6.QtCore import QObject
        from PyQt6.QtWidgets import QWidget, QTreeWidget
        from tabs.workspace_tab import WorkspaceTabMixin
        class _T(QObject, WorkspaceTabMixin):
            pass
        obj = _T()
        with patch.object(obj, "index_workspace_directory"):
            tab = obj.create_workspace_tab()
        self.assertIsInstance(tab, QWidget,
            msg="REGRESSION: create_workspace_tab must return a QWidget")
        self.assertIsInstance(obj.workspace_tree, QTreeWidget,
            msg="REGRESSION: workspace_tree should be a QTreeWidget")
        self.assertEqual(obj.workspace_tree.columnCount(), 3,
            msg="REGRESSION: workspace_tree should have 3 columns")

if __name__ == "__main__":
    unittest.main()
