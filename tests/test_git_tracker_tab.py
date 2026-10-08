"""Tests for tabs/git_tracker_tab.py -- Git Tracker tab mixin."""
import unittest
from unittest.mock import patch, MagicMock


class TestGitTrackerTabMixin(unittest.TestCase):
    """Tests for GitTrackerTabMixin -- file change tracking + diff + commit."""

    def setUp(self):
        self._qt_patcher = patch("tabs.git_tracker_tab.QTimer", return_value=MagicMock())
        self._qt_patcher.start()

    def tearDown(self):
        self._qt_patcher.stop()

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "git_staged_tree"))
        self.assertTrue(hasattr(obj, "git_unstaged_tree"))
        self.assertTrue(hasattr(obj, "git_untracked_tree"))
        self.assertTrue(hasattr(obj, "git_diff_view"))
        self.assertTrue(hasattr(obj, "git_commit_msg"))
        self.assertTrue(hasattr(obj, "git_auto_refresh"))

    def test_create_tab_tree_headers(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        staged_labels = [obj.git_staged_tree.headerItem().text(i) for i in range(2)]
        self.assertEqual(staged_labels, ["File", "Type"])
        unstaged_labels = [obj.git_unstaged_tree.headerItem().text(i) for i in range(2)]
        self.assertEqual(unstaged_labels, ["File", "Type"])
        untracked_labels = [obj.git_untracked_tree.headerItem().text(i) for i in range(1)]
        self.assertEqual(untracked_labels, ["File"])

    def test_create_tab_auto_refresh_default_checked(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = True
            mock_gt.start_monitoring = MagicMock()
            obj._tab = obj.create_git_tracker_tab()
        self.assertTrue(obj.git_auto_refresh.isChecked())

    def test_check_availability_not_a_repo(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        self.assertIn("Not a git repo", obj.git_branch_lbl.text())
        self.assertFalse(obj.git_commit_btn.isEnabled())
        self.assertFalse(obj.git_suggest_btn.isEnabled())
        self.assertFalse(obj.git_auto_refresh.isEnabled())

    def test_check_availability_is_a_repo(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = True
            mock_gt.start_monitoring = MagicMock()  # prevent real file walking
            obj._tab = obj.create_git_tracker_tab()
        self.assertIn("detecting", obj.git_branch_lbl.text())
        self.assertTrue(obj.git_commit_btn.isEnabled())
        self.assertTrue(obj.git_suggest_btn.isEnabled())

    def test_refresh_not_a_repo_calls_check(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        obj._git_refresh()
        self.assertIn("Not a git repo", obj.git_branch_lbl.text())

    def test_refresh_populates_trees(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        from git_tracker import GitStatus, FileChange
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = True
            mock_gt.start_monitoring = MagicMock()  # prevent real file walking
            mock_status = GitStatus(
                branch="main",
                dirty=True,
                staged=[FileChange(path="src/a.py", change_type="modified")],
                unstaged=[FileChange(path="src/b.py", change_type="modified")],
                untracked=["new_file.txt"],
                ahead=2,
                behind=1,
            )
            mock_gt._get_git_status.return_value = mock_status
            obj._tab = obj.create_git_tracker_tab()
        obj._git_refresh()
        self.assertIn("main", obj.git_branch_lbl.text())
        self.assertIn("2", obj.git_branch_lbl.text())  # ahead
        self.assertEqual(obj.git_staged_tree.topLevelItemCount(), 1)
        self.assertEqual(obj.git_unstaged_tree.topLevelItemCount(), 1)
        self.assertEqual(obj.git_untracked_tree.topLevelItemCount(), 1)
        self.assertEqual(obj.git_staged_tree.topLevelItem(0).text(0), "src/a.py")
        self.assertEqual(obj.git_unstaged_tree.topLevelItem(0).text(0), "src/b.py")
        self.assertEqual(obj.git_untracked_tree.topLevelItem(0).text(0), "new_file.txt")

    def test_refresh_clean_repo_status(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        from git_tracker import GitStatus
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = True
            mock_gt.start_monitoring = MagicMock()  # prevent real file walking
            mock_gt._get_git_status.return_value = GitStatus(branch="dev", dirty=False)
            obj._tab = obj.create_git_tracker_tab()
        obj._git_refresh()
        self.assertIn("dev", obj.git_branch_lbl.text())
        self.assertNotIn("↑", obj.git_branch_lbl.text())  # no ahead arrow
        self.assertEqual(obj.git_staged_tree.topLevelItemCount(), 0)
        self.assertEqual(obj.git_unstaged_tree.topLevelItemCount(), 0)

    def test_on_file_clicked_shows_diff(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        # Simulate a tree item click
        from PyQt6.QtWidgets import QTreeWidgetItem
        item = QTreeWidgetItem(["src/main.py", "modified"])
        mock_gt.get_file_diff.return_value = "+added line\n-removed line"
        obj._git_on_file_clicked(item, 0)
        self.assertEqual(obj.git_diff_view.toPlainText(), "+added line\n-removed line")
        mock_gt.get_file_diff.assert_called_once_with("src/main.py")

    def test_on_file_clicked_no_diff_shows_placeholder(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        from PyQt6.QtWidgets import QTreeWidgetItem
        item = QTreeWidgetItem(["clean.py", ""])
        mock_gt.get_file_diff.return_value = ""
        obj._git_on_file_clicked(item, 0)
        self.assertIn("No changes", obj.git_diff_view.toPlainText())

    def test_stage_all_stages_files(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        from git_tracker import GitStatus, FileChange
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = True
            mock_gt.start_monitoring = MagicMock()  # prevent real file walking
            mock_gt._get_git_status.return_value = GitStatus(
                branch="main",
                unstaged=[FileChange(path="a.py", change_type="modified")],
                untracked=["b.txt"],
            )
            mock_gt.get_diff.return_value = "staged diff text"
            obj._tab = obj.create_git_tracker_tab()
        obj._git_stage_all()
        mock_gt.stage_file.assert_any_call("a.py")
        mock_gt.stage_file.assert_any_call("b.txt")
        mock_gt.get_diff.assert_called_once_with(staged=True)

    def test_unstage_all_unstages_files(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        from git_tracker import GitStatus, FileChange
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = True
            mock_gt.start_monitoring = MagicMock()  # prevent real file walking
            mock_gt._get_git_status.return_value = GitStatus(
                branch="main",
                staged=[FileChange(path="c.py", change_type="staged")],
            )
            obj._tab = obj.create_git_tracker_tab()
        obj._git_unstage_all()
        mock_gt.unstage_file.assert_called_once_with("c.py")

    def test_suggest_commit_sets_message(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            mock_gt.suggest_commit_message.return_value = "Update src/main.py"
            obj._tab = obj.create_git_tracker_tab()
        obj._git_suggest_commit()
        self.assertEqual(obj.git_commit_msg.toPlainText(), "Update src/main.py")

    def test_toggle_auto_refresh_on_starts_timer(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        obj.git_refresh_timer.reset_mock()
        obj._git_toggle_auto_refresh(True)
        obj.git_refresh_timer.start.assert_called_once_with(3000)

    def test_toggle_auto_refresh_off_stops_timer(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        obj.git_refresh_timer.reset_mock()
        obj._git_toggle_auto_refresh(False)
        obj.git_refresh_timer.stop.assert_called_once()

    def test_populate_git_tree_two_columns(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        items = [("file1.py", "modified"), ("file2.py", "added")]
        obj._populate_git_tree(obj.git_staged_tree, items, columns=2)
        self.assertEqual(obj.git_staged_tree.topLevelItemCount(), 2)
        self.assertEqual(obj.git_staged_tree.topLevelItem(0).text(0), "file1.py")
        self.assertEqual(obj.git_staged_tree.topLevelItem(0).text(1), "modified")
        self.assertEqual(obj.git_staged_tree.topLevelItem(1).text(0), "file2.py")

    def test_populate_git_tree_single_column(self):
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        items = [("untracked1.txt",), ("untracked2.txt",)]
        obj._populate_git_tree(obj.git_untracked_tree, items, columns=1)
        self.assertEqual(obj.git_untracked_tree.topLevelItemCount(), 2)
        self.assertEqual(obj.git_untracked_tree.topLevelItem(0).text(0), "untracked1.txt")

    def test_on_status_calls_refresh(self):
        """_git_on_status() delegates to _git_refresh()."""
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        with patch.object(obj, "_git_refresh") as mock_refresh:
            obj._git_on_status(None)
        mock_refresh.assert_called_once()

    def test_on_file_change_is_noop(self):
        """_git_on_file_change() does nothing but pass."""
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        # Should not raise
        obj._git_on_file_change("src/a.py", "modified")

    def test_on_file_clicked_none_item_early_return(self):
        """_git_on_file_clicked(None, ..) returns immediately without calling
        get_file_diff, covering the ``if not item: return`` guard."""
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        obj._git_on_file_clicked(None, 0)
        mock_gt.get_file_diff.assert_not_called()

    def test_do_commit_empty_message_shows_warning(self):
        """_git_do_commit() with empty message shows warning and returns."""
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            obj._tab = obj.create_git_tracker_tab()
        with patch("tabs.git_tracker_tab.QMessageBox.warning") as mock_warn:
            obj.git_commit_msg.setPlainText("")
            obj._git_do_commit()
        mock_warn.assert_called_once()
        mock_gt.commit.assert_not_called()

    def test_do_commit_success_shows_info_and_clears(self):
        """_git_do_commit() with message and success shows info and clears."""
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            mock_gt.commit.return_value = (True, "Committed 3 files")
            obj._tab = obj.create_git_tracker_tab()
        with patch("tabs.git_tracker_tab.QMessageBox.information") as mock_info:
            obj.git_commit_msg.setPlainText("Fix bug in parser")
            obj._git_do_commit()
        mock_info.assert_called_once()
        mock_gt.commit.assert_called_once_with("Fix bug in parser")
        self.assertEqual(obj.git_commit_msg.toPlainText(), "",
            msg="REGRESSION: commit msg should be cleared after success")

    def test_do_commit_failure_shows_critical(self):
        """_git_do_commit() with commit failure shows critical."""
        from tabs.git_tracker_tab import GitTrackerTabMixin
        obj = GitTrackerTabMixin()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            mock_gt.commit.return_value = (False, "Merge conflict")
            obj._tab = obj.create_git_tracker_tab()
        with patch("tabs.git_tracker_tab.QMessageBox.critical") as mock_crit:
            obj.git_commit_msg.setPlainText("My commit")
            obj._git_do_commit()
        mock_crit.assert_called_once()
        mock_gt.commit.assert_called_once_with("My commit")

    def test_git_tracker_tab_widgets_are_real_qt_objects(self):
        """create_git_tracker_tab() builds a QWidget with all expected
        UI elements as real Qt widgets."""
        from PyQt6.QtCore import QObject
        from PyQt6.QtWidgets import QWidget, QTreeWidget, QTextEdit, QCheckBox, QLabel
        from tabs.git_tracker_tab import GitTrackerTabMixin
        class _T(QObject, GitTrackerTabMixin):
            pass
        obj = _T()
        with patch("tabs.git_tracker_tab.GitTracker") as mock_gt_cls:
            mock_gt = MagicMock()
            mock_gt_cls.return_value = mock_gt
            mock_gt.is_git_repo.return_value = False
            mock_gt.status_changed = MagicMock()
            mock_gt.file_changed = MagicMock()
            tab = obj.create_git_tracker_tab()
        self.assertIsInstance(tab, QWidget,
            msg="REGRESSION: create_git_tracker_tab must return a QWidget")
        self.assertIsInstance(obj.git_staged_tree, QTreeWidget,
            msg="REGRESSION: git_staged_tree should be a QTreeWidget")
        self.assertIsInstance(obj.git_unstaged_tree, QTreeWidget,
            msg="REGRESSION: git_unstaged_tree should be a QTreeWidget")
        self.assertIsInstance(obj.git_untracked_tree, QTreeWidget,
            msg="REGRESSION: git_untracked_tree should be a QTreeWidget")
        self.assertIsInstance(obj.git_diff_view, QTextEdit,
            msg="REGRESSION: git_diff_view should be a QTextEdit")
        self.assertIsInstance(obj.git_commit_msg, QTextEdit,
            msg="REGRESSION: git_commit_msg should be a QTextEdit")
        self.assertIsInstance(obj.git_auto_refresh, QCheckBox,
            msg="REGRESSION: git_auto_refresh should be a QCheckBox")
        self.assertIsInstance(obj.git_branch_lbl, QLabel,
            msg="REGRESSION: git_branch_lbl should be a QLabel")
        self.assertIsInstance(obj.git_status_indicator, QLabel,
            msg="REGRESSION: git_status_indicator should be a QLabel")

if __name__ == "__main__":
    unittest.main()
