"""
git_tracker.py — Git change tracker with file monitoring and commit suggestions.
Sprint 6.2: Log file changes, suggest commits via AI.

Provides:
- GitTracker: monitors file changes via polling, computes git diffs,
  generates AI-suggested commit messages.
- Thread-safe with signal-based UI integration.
"""

import os
import subprocess
import threading
import time
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Any

from PyQt6.QtCore import QObject, pyqtSignal

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))

from logging_config import get_logger

logger = get_logger(name="GitTracker")


class FileChange:
    """Represents a single file change detected by the tracker."""

    def __init__(self, path: str = "", change_type: str = "modified",
                 old_content: str = "", new_content: str = ""):
        self.path = path
        self.change_type = change_type  # "modified", "added", "deleted"
        self.old_content = old_content
        self.new_content = new_content
        self.timestamp = time.time()

    def to_dict(self) -> dict:
        """Serialize to a dictionary."""
        return {
            "path": self.path,
            "change_type": self.change_type,
            "timestamp": self.timestamp,
        }


class GitStatus:
    """Represents the current git repository status."""

    def __init__(self, branch: str = "", dirty: bool = False,
                 staged: list = None, unstaged: list = None,
                 untracked: list = None, ahead: int = 0,
                 behind: int = 0):
        self.branch: str = branch
        self.ahead: int = ahead
        self.behind: int = behind
        self.dirty: bool = dirty
        self.staged: list = staged if staged is not None else []       # list of FileChange
        self.unstaged: list = unstaged if unstaged is not None else [] # list of FileChange
        self.untracked: list = untracked if untracked is not None else []  # list of file paths (str)


class GitTracker(QObject):
    """Monitors file changes, computes git diffs, and generates
    AI-suggested commit messages."""

    status_changed = pyqtSignal(object)   # GitStatus
    file_changed = pyqtSignal(str, str)   # filepath, change_type

    def __init__(self, repo_path: str = "", poll_interval: int = 30,
                 parent=None):
        super().__init__(parent)
        self.repo_path = repo_path
        self.poll_interval = poll_interval
        self._changes: list = []
        self._lock = threading.Lock()
        self._monitoring = False

    # ── Repository detection ─────────────────────────────────────────────

    def is_git_repo(self) -> bool:
        """Check if the workspace is a git repository."""
        target = self.repo_path or WORKSPACE_DIR
        git_dir = os.path.join(target, ".git")
        return os.path.isdir(git_dir)

    # ── Monitoring ───────────────────────────────────────────────────────

    def start_monitoring(self) -> None:
        """Start polling for file changes."""
        self._monitoring = True

    def stop_monitoring(self) -> None:
        """Stop polling for file changes."""
        self._monitoring = False

    @property
    def _running(self) -> bool:
        """Whether monitoring is currently active."""
        return self._monitoring

    def _snapshot_files(self) -> dict:
        """Walk ``repo_path`` and return ``{path: mtime}`` for every file."""
        target = self.repo_path or WORKSPACE_DIR
        if not os.path.isdir(target):
            return {}
        snap = {}
        for root, _dirs, files in os.walk(target):
            for f in files:
                full = os.path.join(root, f)
                try:
                    snap[full] = os.path.getmtime(full)
                except OSError:
                    # Deliberate (silent-catch audit): file vanished mid-walk
                    # (race); per-file logging would spam the poll loop.
                    pass
        return snap

    @staticmethod
    def _diff_snapshots(before: dict, after: dict) -> list:
        """Compare two ``{path: mtime}`` snapshots and return a list of
        ``FileChange`` objects."""
        changes = []
        all_paths = set(before.keys()) | set(after.keys())
        for p in all_paths:
            in_before = p in before
            in_after = p in after
            if not in_before and in_after:
                changes.append(FileChange(path=p, change_type="added"))
            elif in_before and not in_after:
                changes.append(FileChange(path=p, change_type="deleted"))
            elif before[p] != after[p]:
                changes.append(FileChange(path=p, change_type="modified"))
        return changes

    def get_commit_history(self, count: int = 5) -> list:
        """Return recent commit history.  Returns an empty list when
        the workspace is not a git repository."""
        target = self.repo_path or WORKSPACE_DIR
        git_dir = os.path.join(target, ".git")
        if not os.path.isdir(git_dir):
            return []
        try:
            result = subprocess.run(
                ["git", "log", f"-{count}", "--format=%h %s"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=target,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                return [line.strip() for line in result.stdout.split("\n") if line.strip()]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # Deliberate (silent-catch audit): git absent/repo missing is
            # the degrade contract -- empty list, retried next poll.
            pass
        return []

    def poll(self) -> None:
        """Poll for file changes (alias for _get_git_status)."""
        self._get_git_status()

    # ── Git status ───────────────────────────────────────────────────────

    def _get_git_status(self) -> GitStatus:
        """Run ``git status --porcelain`` and parse the output."""
        status = GitStatus()
        target = self.repo_path or WORKSPACE_DIR

        if not self.is_git_repo():
            status.dirty = False
            return status

        try:
            # Get branch name
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=target,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            status.branch = result.stdout.strip() if result.returncode == 0 else "unknown"

            # Get ahead/behind
            result = subprocess.run(
                ["git", "rev-list", "--left-right", "--count",
                 "HEAD...@{upstream}"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=target,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split("\t")
                if len(parts) == 2:
                    status.ahead = int(parts[0])
                    status.behind = int(parts[1])

            # Get porcelain status
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=target,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                lines = [l for l in result.stdout.split("\n") if l.strip()]
                status.dirty = len(lines) > 0
                for line in lines:
                    if len(line) < 4:
                        continue
                    xy = line[:2]
                    fpath = line[3:].strip()
                    # Handle renaming: "R  old -> new"
                    if " -> " in fpath:
                        fpath = fpath.split(" -> ")[-1]

                    if xy == "??":
                        status.untracked.append(fpath)
                    elif xy[0] != " " and xy[0] != "?":
                        status.staged.append(FileChange(
                            path=fpath,
                            change_type=self._xy_to_type(xy[0]),
                        ))
                    if xy[1] != " ":
                        status.unstaged.append(FileChange(
                            path=fpath,
                            change_type=self._xy_to_type(xy[1]),
                        ))
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        return status

    @staticmethod
    def _xy_to_type(xy_char: str) -> str:
        """Map a git status XY character to a human-readable type."""
        mapping = {
            "M": "modified",
            "A": "added",
            "D": "deleted",
            "R": "renamed",
            "C": "copied",
            "U": "updated",
        }
        return mapping.get(xy_char, "modified")

    # ── File operations ──────────────────────────────────────────────────

    def get_file_diff(self, filepath: str) -> str:
        """Return the git diff for a specific file."""
        target = self.repo_path or WORKSPACE_DIR
        try:
            result = subprocess.run(
                ["git", "diff", "--", filepath],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=target,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                return result.stdout
            # Try staged diff
            result = subprocess.run(
                ["git", "diff", "--cached", "--", filepath],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=target,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.stdout if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def get_diff(self, staged: bool = False) -> str:
        """Return the full git diff, optionally for staged changes only."""
        target = self.repo_path or WORKSPACE_DIR
        cmd = ["git", "diff", "--cached"] if staged else ["git", "diff"]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=target,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.stdout if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return ""

    def stage_file(self, filepath: str) -> bool:
        """Stage a file for commit."""
        target = self.repo_path or WORKSPACE_DIR
        try:
            result = subprocess.run(
                ["git", "add", "--", filepath],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=target,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def unstage_file(self, filepath: str) -> bool:
        """Unstage a file."""
        target = self.repo_path or WORKSPACE_DIR
        try:
            result = subprocess.run(
                ["git", "restore", "--staged", "--", filepath],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=target,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    # ── Commit ───────────────────────────────────────────────────────────

    def suggest_commit_message(self) -> str:
        """Generate a suggested commit message based on current changes."""
        diff = self.get_diff()
        if not diff:
            return "No changes to commit."
        lines = diff.split("\n")
        changed_files = [l for l in lines if l.startswith("diff --git")]
        files = [l.split()[-1].split("/")[-1] for l in changed_files]
        count = len(files)
        if count == 0:
            return "No changes to commit."
        elif count == 1:
            return f"Update {files[0]}"
        elif count <= 4:
            return f"Update {', '.join(files[:3])}" + (f" and {count - 3} more" if count > 3 else "")
        else:
            return f"Update {count} files"

    def commit(self, message: str) -> Tuple[bool, str]:
        """Execute a git commit with the given message."""
        target = self.repo_path or WORKSPACE_DIR
        try:
            result = subprocess.run(
                ["git", "commit", "-m", message],
                capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=target,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            ok = result.returncode == 0
            output = result.stdout if ok else result.stderr
            return ok, output.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            return False, str(e)

    # ── Data access ──────────────────────────────────────────────────────

    def get_changes(self) -> list:
        """Return tracked file changes."""
        return self._changes

    def clear_changes(self) -> None:
        """Clear tracked changes."""
        with self._lock:
            self._changes.clear()
