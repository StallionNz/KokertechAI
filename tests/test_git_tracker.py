"""Tests for git_tracker.py - Git change monitoring and operations."""
import os
import tempfile
import unittest
from unittest.mock import patch
from git_tracker import FileChange, GitStatus, GitTracker

class TestFileChange(unittest.TestCase):
    def test_file_change_creation(self):
        fc = FileChange(path="/path/to/file.py", change_type="modified")
        self.assertEqual(fc.path, "/path/to/file.py")
        self.assertEqual(fc.change_type, "modified")
    def test_file_change_to_dict(self):
        fc = FileChange(path="test.py", change_type="added")
        d = fc.to_dict()
        self.assertEqual(d["path"], "test.py")
        self.assertEqual(d["change_type"], "added")
        self.assertIn("timestamp", d)

class TestGitStatus(unittest.TestCase):
    def test_git_status_defaults(self):
        gs = GitStatus()
        self.assertEqual(gs.branch, "")
        self.assertFalse(gs.dirty)
        self.assertEqual(gs.staged, [])
        self.assertEqual(gs.untracked, [])

class TestGitTrackerIsGitRepo(unittest.TestCase):
    def test_is_git_repo_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".git"))
            tracker = GitTracker(repo_path=tmpdir)
            self.assertTrue(tracker.is_git_repo())
    def test_is_git_repo_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = GitTracker(repo_path=tmpdir)
            self.assertFalse(tracker.is_git_repo())

class TestGitTrackerDiffSnapshots(unittest.TestCase):
    def test_no_changes(self):
        changes = GitTracker._diff_snapshots({"a.py": 100.0}, {"a.py": 100.0})
        self.assertEqual(changes, [])
    def test_added_file(self):
        changes = GitTracker._diff_snapshots({"a.py": 100.0}, {"a.py": 100.0, "b.py": 200.0})
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].change_type, "added")
    def test_deleted_file(self):
        changes = GitTracker._diff_snapshots({"a.py": 100.0, "b.py": 200.0}, {"a.py": 100.0})
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].change_type, "deleted")
    def test_modified_file(self):
        changes = GitTracker._diff_snapshots({"a.py": 100.0}, {"a.py": 150.0})
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].change_type, "modified")

class TestGitTrackerSnapshotFiles(unittest.TestCase):
    def test_snapshot_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            snap = GitTracker(repo_path=tmpdir)._snapshot_files()
            self.assertEqual(snap, {})
    def test_snapshot_with_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "a.txt"), "w").close()
            snap = GitTracker(repo_path=tmpdir)._snapshot_files()
            self.assertEqual(len(snap), 1)

class TestGitTrackerSuggestCommitMessage(unittest.TestCase):
    def setUp(self):
        self.tracker = GitTracker()
    def test_no_diff_returns_no_changes(self):
        with patch.object(self.tracker, "get_diff", return_value=""):
            self.assertEqual(self.tracker.suggest_commit_message(), "No changes to commit.")
    def test_single_file_diff(self):
        with patch.object(self.tracker, "get_diff", return_value="diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py"):
            self.assertEqual(self.tracker.suggest_commit_message(), "Update file.py")

class TestGitTrackerStartStop(unittest.TestCase):
    def test_start_stop_no_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = GitTracker(repo_path=tmpdir, poll_interval=0.1)
            tracker.start_monitoring()
            self.assertTrue(tracker._running)
            tracker.stop_monitoring()
            self.assertFalse(tracker._running)
    def test_double_start_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = GitTracker(repo_path=tmpdir)
            tracker.start_monitoring()
            tracker.start_monitoring()  # should not crash
            tracker.stop_monitoring()

class TestGitTrackerGetCommitHistory(unittest.TestCase):
    def test_get_commit_history_empty_when_no_git(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = GitTracker(repo_path=tmpdir)
            self.assertEqual(tracker.get_commit_history(count=5), [])

if __name__ == "__main__":
    unittest.main()
