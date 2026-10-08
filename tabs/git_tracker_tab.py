"""
Git Tracker tab mixin — file change monitoring, diff viewer, commit suggestions.
Sprint 6.2: Git change tracking UI.
"""
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QTreeWidget, QTreeWidgetItem, QGroupBox,
    QSplitter, QMessageBox, QCheckBox,
)

from typing import TYPE_CHECKING

from git_tracker import GitTracker, GitStatus

if TYPE_CHECKING:
    from tabs.context import DashboardContext


class GitTrackerTabMixin:
    """Mixin providing the Git Tracker tab."""

    @property
    def context(self) -> "DashboardContext":
        """Return the shared DashboardContext, falling back to legacy attributes if ctx is unset."""
        if hasattr(self, "ctx") and self.ctx is not None:
            return self.ctx
        from tabs.context import DashboardContext
        return DashboardContext(
            controller=getattr(self, "controller", None),
            file_logger=getattr(self, "file_logger", None),
            log_to_audit=getattr(self, "log_to_audit", None),
            audit_signal=getattr(self, "audit_signal", None),
            config=getattr(self, "config", {}),
            restore_chat_input=getattr(self, "restore_chat_input", None),
            switch_tab=getattr(self, "switch_tab", None),
        )

    @context.setter
    def context(self, value: "DashboardContext") -> None:
        self.ctx = value

    def create_git_tracker_tab(self) -> QWidget:
        """Create the git change tracker tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Header row ──
        header_row = QHBoxLayout()
        header = QLabel("📁 Git Change Tracker")
        header.setStyleSheet("font-size: 14pt; font-weight: bold;")
        header_row.addWidget(header)
        header_row.addStretch()

        self.git_branch_lbl = QLabel("Branch: --")
        self.git_branch_lbl.setStyleSheet("font-size: 10pt; color: #F59E0B;")
        header_row.addWidget(self.git_branch_lbl)

        self.git_status_indicator = QLabel("●")
        self.git_status_indicator.setStyleSheet("font-size: 12pt; color: #6B7280;")
        header_row.addWidget(self.git_status_indicator)
        layout.addLayout(header_row)

        # ── Main splitter: file tree + diff viewer ──
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Top: file change tree
        top_widget = QWidget()
        top_layout = QHBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        # Staged files
        staged_group = QGroupBox("✓ Staged")
        staged_layout = QVBoxLayout(staged_group)
        self.git_staged_tree = QTreeWidget()
        self.git_staged_tree.setHeaderLabels(["File", "Type"])
        self.git_staged_tree.setRootIsDecorated(False)
        staged_layout.addWidget(self.git_staged_tree)

        btn_stage = QPushButton("Stage All ▶")
        btn_stage.clicked.connect(self._git_stage_all)
        staged_layout.addWidget(btn_stage)
        top_layout.addWidget(staged_group)

        # Unstaged files
        unstaged_group = QGroupBox("✎ Unstaged")
        unstaged_layout = QVBoxLayout(unstaged_group)
        self.git_unstaged_tree = QTreeWidget()
        self.git_unstaged_tree.setHeaderLabels(["File", "Type"])
        self.git_unstaged_tree.setRootIsDecorated(False)
        self.git_unstaged_tree.itemClicked.connect(self._git_on_file_clicked)
        unstaged_layout.addWidget(self.git_unstaged_tree)

        btn_unstage = QPushButton("◀ Unstage All")
        btn_unstage.clicked.connect(self._git_unstage_all)
        unstaged_layout.addWidget(btn_unstage)
        top_layout.addWidget(unstaged_group)

        # Untracked files
        untracked_group = QGroupBox("❓ Untracked")
        untracked_layout = QVBoxLayout(untracked_group)
        self.git_untracked_tree = QTreeWidget()
        self.git_untracked_tree.setHeaderLabels(["File"])
        self.git_untracked_tree.setRootIsDecorated(False)
        untracked_layout.addWidget(self.git_untracked_tree)
        top_layout.addWidget(untracked_group)

        splitter.addWidget(top_widget)

        # Bottom: diff viewer + commit area
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        diff_label = QLabel("Diff View")
        bottom_layout.addWidget(diff_label)
        self.git_diff_view = QTextEdit()
        self.git_diff_view.setReadOnly(True)
        self.git_diff_view.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 9pt; "
            "background-color: #0A0A0C;"
        )
        bottom_layout.addWidget(self.git_diff_view)

        # Commit row
        commit_row = QHBoxLayout()
        self.git_commit_msg = QTextEdit()
        self.git_commit_msg.setPlaceholderText("Commit message...")
        self.git_commit_msg.setMaximumHeight(50)
        commit_row.addWidget(self.git_commit_msg, 1)

        self.git_suggest_btn = QPushButton("💡 Suggest")
        self.git_suggest_btn.clicked.connect(self._git_suggest_commit)
        commit_row.addWidget(self.git_suggest_btn)

        self.git_commit_btn = QPushButton("📝 Commit")
        self.git_commit_btn.clicked.connect(self._git_do_commit)
        self.git_commit_btn.setStyleSheet(
            "background-color: #065F46; border-color: #10B981;"
        )
        commit_row.addWidget(self.git_commit_btn)
        bottom_layout.addLayout(commit_row)

        # Auto-refresh toggle
        auto_row = QHBoxLayout()
        self.git_auto_refresh = QCheckBox("Auto-refresh (3s)")
        self.git_auto_refresh.setChecked(True)
        self.git_auto_refresh.toggled.connect(self._git_toggle_auto_refresh)
        auto_row.addWidget(self.git_auto_refresh)
        auto_row.addStretch()

        self.git_refresh_btn = QPushButton("🔄 Refresh Now")
        self.git_refresh_btn.clicked.connect(self._git_refresh)
        auto_row.addWidget(self.git_refresh_btn)
        bottom_layout.addLayout(auto_row)

        splitter.addWidget(bottom_widget)
        splitter.setSizes([300, 350])
        layout.addWidget(splitter)

        # ── Initialize git tracker ──
        self.git_tracker = GitTracker()
        self.git_tracker.status_changed.connect(self._git_on_status)
        self.git_tracker.file_changed.connect(self._git_on_file_change)

        # Auto-refresh timer
        self.git_refresh_timer = QTimer(self)
        self.git_refresh_timer.timeout.connect(self._git_refresh)
        self.git_refresh_timer.start(3000)

        # Initial check
        self._git_check_availability()
        if self.git_tracker.is_git_repo():
            self.git_tracker.start_monitoring()

        return tab

    def _git_check_availability(self):
        """Check if git is available and update UI accordingly."""
        if not self.git_tracker.is_git_repo():
            self.git_branch_lbl.setText("⚠️ Not a git repo")
            self.git_status_indicator.setStyleSheet(
                "font-size: 12pt; color: #EF4444;"
            )
            self.git_commit_btn.setEnabled(False)
            self.git_suggest_btn.setEnabled(False)
            self.git_auto_refresh.setChecked(False)
            self.git_auto_refresh.setEnabled(False)
        else:
            self.git_branch_lbl.setText("Branch: detecting...")
            self.git_status_indicator.setStyleSheet(
                "font-size: 12pt; color: #6B7280;"
            )

    def _git_refresh(self):
        """Refresh the git status display."""
        if not self.git_tracker.is_git_repo():
            self._git_check_availability()
            return

        status = self.git_tracker._get_git_status()

        # Update branch
        self.git_branch_lbl.setText(f"Branch: {status.branch}")
        if status.ahead > 0:
            self.git_branch_lbl.setText(
                f"Branch: {status.branch} ↑{status.ahead}"
            )
        if status.behind > 0:
            current = self.git_branch_lbl.text()
            self.git_branch_lbl.setText(f"{current} ↓{status.behind}")

        # Status indicator
        if status.dirty:
            self.git_status_indicator.setStyleSheet(
                "font-size: 12pt; color: #F59E0B;"
            )
            self.git_status_indicator.setToolTip("Dirty — uncommitted changes")
        else:
            self.git_status_indicator.setStyleSheet(
                "font-size: 12pt; color: #10B981;"
            )
            self.git_status_indicator.setToolTip("Clean")

        # Populate trees
        self._populate_git_tree(
            self.git_staged_tree,
            [(f.path, f.change_type) for f in status.staged],
        )
        self._populate_git_tree(
            self.git_unstaged_tree,
            [(f.path, f.change_type) for f in status.unstaged],
        )
        self._populate_git_tree(
            self.git_untracked_tree,
            [(f, "untracked") for f in status.untracked],
            columns=1,
        )

    def _populate_git_tree(self, tree, items, columns=2):
        """Populate a QTreeWidget with file entries."""
        tree.clear()
        for entry in items:
            if columns >= 2:
                fpath, change_type = entry
            else:
                fpath = entry[0] if isinstance(entry, tuple) else entry
                change_type = ""
            item = QTreeWidgetItem([fpath, change_type] if columns >= 2 else [fpath])
            item.setToolTip(0, fpath)
            tree.addTopLevelItem(item)

    def _git_on_file_clicked(self, item, column):
        """Show diff for clicked file."""
        if not item:
            return
        fpath = item.text(0)
        diff = self.git_tracker.get_file_diff(fpath)
        if diff:
            self.git_diff_view.setPlainText(diff)
        else:
            self.git_diff_view.setPlainText(f"No changes to show for {fpath}")

    def _git_on_status(self, status: GitStatus):
        """Handle status_changed signal from GitTracker."""
        self._git_refresh()

    def _git_on_file_change(self, filepath: str, change_type: str):
        """Handle real-time file change detection."""
        pass  # Auto-refresh timer handles this; could add animation

    def _git_stage_all(self):
        """Stage all unstaged and untracked files."""
        if not self.git_tracker.is_git_repo():
            return
        status = self.git_tracker._get_git_status()
        for f in status.unstaged:
            self.git_tracker.stage_file(f.path)
        for f in status.untracked:
            self.git_tracker.stage_file(f)
        # Refresh diff
        diff = self.git_tracker.get_diff(staged=True)
        self.git_diff_view.setPlainText(diff)
        self._git_refresh()

    def _git_unstage_all(self):
        """Unstage all staged files."""
        if not self.git_tracker.is_git_repo():
            return
        status = self.git_tracker._get_git_status()
        for f in status.staged:
            self.git_tracker.unstage_file(f.path)
        self.git_diff_view.clear()
        self._git_refresh()

    def _git_suggest_commit(self):
        """Generate an AI-suggested commit message."""
        suggestion = self.git_tracker.suggest_commit_message()
        self.git_commit_msg.setPlainText(suggestion)

    def _git_do_commit(self):
        """Execute a commit with the current message."""
        message = self.git_commit_msg.toPlainText().strip()
        if not message:
            QMessageBox.warning(
                self,
                "No Message",
                "Please enter a commit message or use 💡 Suggest.",
            )
            return

        ok, output = self.git_tracker.commit(message)
        if ok:
            QMessageBox.information(
                self, "Committed", f"✅ Commit successful.\n\n{output}"
            )
            self.git_commit_msg.clear()
            self.git_diff_view.clear()
        else:
            QMessageBox.critical(
                self, "Commit Failed", f"❌ Commit failed:\n\n{output}"
            )
        self._git_refresh()

    def _git_toggle_auto_refresh(self, enabled):
        """Toggle auto-refresh timer."""
        if enabled:
            self.git_refresh_timer.start(3000)
        else:
            self.git_refresh_timer.stop()
