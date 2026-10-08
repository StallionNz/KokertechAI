"""
session_browser_tab.py — SessionBrowserTabMixin: episodic journal session browser.
Sprint 7: Browse, search, and drill into episodic journal sessions.

Provides a dashboard tab that lists all recorded sessions with metadata
and allows drilling into individual entries per session with actions
(boost importance, delete entry, delete session, new session).
"""

import sqlite3
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QTextEdit, QGroupBox, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox,
)

if TYPE_CHECKING:
    from tabs.context import DashboardContext


class SessionBrowserTabMixin:
    """Mixin providing the Session Browser tab for the episodic journal."""

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

    def create_session_browser_tab(self) -> QWidget:
        """Create the session browser tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Header ──
        header_row = QHBoxLayout()
        header = QLabel("📓 Session Browser — Episodic Journal")
        header.setStyleSheet("font-size: 14pt; font-weight: bold;")
        header_row.addWidget(header)
        header_row.addStretch()

        self.session_refresh_btn = QPushButton("🔄 Refresh")
        self.session_refresh_btn.clicked.connect(self._session_browser_refresh)
        header_row.addWidget(self.session_refresh_btn)

        self.session_rotate_btn = QPushButton("➕ New Session")
        self.session_rotate_btn.setStyleSheet(
            "background-color: #065F46; border-color: #059669;"
        )
        self.session_rotate_btn.clicked.connect(self._session_browser_new_session)
        header_row.addWidget(self.session_rotate_btn)
        layout.addLayout(header_row)

        # ── Search / filter bar ──
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("🔍 Search Sessions:"))
        self.session_search_input = QLineEdit()
        self.session_search_input.setPlaceholderText("Filter by session ID or summary...")
        self.session_search_input.textChanged.connect(self._session_browser_filter)
        search_row.addWidget(self.session_search_input, 1)

        self.session_count_label = QLabel("0 sessions")
        self.session_count_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        search_row.addWidget(self.session_count_label)
        layout.addLayout(search_row)

        # ── Splitter: session list (top) + entry detail (bottom) ──
        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Session list (top panel) ──
        session_group = QGroupBox("Session List")
        session_layout = QVBoxLayout(session_group)

        self.session_table = QTableWidget(0, 5)
        self.session_table.setHorizontalHeaderLabels([
            "Session ID", "Start Time", "End Time", "Entries", "Summary"
        ])
        self.session_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Interactive
        )
        self.session_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch
        )
        self.session_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.session_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.session_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.session_table.itemSelectionChanged.connect(
            self._session_browser_on_session_selected
        )
        self.session_table.setSortingEnabled(True)
        # Set reasonable default column widths
        self.session_table.setColumnWidth(0, 260)
        self.session_table.setColumnWidth(1, 150)
        self.session_table.setColumnWidth(2, 150)
        self.session_table.setColumnWidth(3, 70)
        session_layout.addWidget(self.session_table)
        splitter.addWidget(session_group)

        # ── Entry detail (bottom panel) ──
        entry_group = QGroupBox("Session Entries")
        entry_layout = QVBoxLayout(entry_group)

        entry_header = QHBoxLayout()
        self.session_entries_label = QLabel("Select a session to view entries")
        self.session_entries_label.setStyleSheet("font-size: 10pt; font-weight: bold;")
        entry_header.addWidget(self.session_entries_label, 1)

        self.entry_count_label = QLabel("")
        self.entry_count_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        entry_header.addWidget(self.entry_count_label)
        entry_layout.addLayout(entry_header)

        # Replace QTextEdit with QListWidget for selectable entries
        self.session_entries_list = QListWidget()
        self.session_entries_list.setStyleSheet(
            "font-size: 10pt; font-family: 'Consolas', monospace;"
        )
        self.session_entries_list.currentItemChanged.connect(
            self._session_browser_on_entry_selected
        )
        entry_layout.addWidget(self.session_entries_list)

        # ── Entry detail preview (shows full text of selected entry) ──
        self.session_entry_preview = QTextEdit()
        self.session_entry_preview.setReadOnly(True)
        self.session_entry_preview.setMaximumHeight(120)
        self.session_entry_preview.setPlaceholderText("Select an entry to see full text")
        self.session_entry_preview.setStyleSheet(
            "font-size: 9pt; font-family: 'Consolas', monospace;"
        )
        entry_layout.addWidget(self.session_entry_preview)

        # ── Entry actions ──
        action_row = QHBoxLayout()
        self.entry_importance_btn = QPushButton("⭐ Boost Importance +1")
        self.entry_importance_btn.setEnabled(False)
        self.entry_importance_btn.clicked.connect(
            self._session_browser_boost_importance
        )
        action_row.addWidget(self.entry_importance_btn)

        self.entry_delete_btn = QPushButton("🗑️ Delete Entry")
        self.entry_delete_btn.setEnabled(False)
        self.entry_delete_btn.setStyleSheet(
            "background-color: #7F1D1D; border-color: #991B1B;"
        )
        self.entry_delete_btn.clicked.connect(
            self._session_browser_delete_entry
        )
        action_row.addWidget(self.entry_delete_btn)

        action_row.addStretch()

        self.session_delete_btn = QPushButton("🗑️ Delete Session")
        self.session_delete_btn.setEnabled(False)
        self.session_delete_btn.setStyleSheet(
            "background-color: #7F1D1D; border-color: #991B1B;"
        )
        self.session_delete_btn.clicked.connect(
            self._session_browser_delete_session
        )
        action_row.addWidget(self.session_delete_btn)
        entry_layout.addLayout(action_row)

        splitter.addWidget(entry_group)
        splitter.setSizes([300, 350])

        layout.addWidget(splitter, 1)

        # ── Store state ──
        self._session_sessions_cache = []   # Full session list cache
        self._session_entries_cache = []    # Current session entries (list of dicts)
        self._session_selected_session_id = None
        self._session_selected_entry_id = None

        # Initial load (deferred slightly to let UI settle)
        QTimer.singleShot(100, self._session_browser_refresh)

        return tab

    # ────────────────────────────────────────────────────────────
    # Internal methods
    # ────────────────────────────────────────────────────────────

    def _session_browser_refresh(self):
        """Reload the session list from the vault."""
        try:
            import memory_vault
            sessions = memory_vault.list_recent_sessions(limit=200)
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            self.context.log(f"⚠️ Session refresh failed: {e}")
            sessions = []

        self._session_sessions_cache = sessions
        self._session_render_table(sessions)

    def _session_browser_new_session(self):
        """Start a new session and refresh the list."""
        try:
            import memory_vault
            new_sid = memory_vault.start_new_session(
                summary="Manual rotation from Session Browser"
            )
            self.context.log(f"📓 New session started: {new_sid[:30]}...")
            self._session_browser_refresh()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            self.context.log(f"⚠️ New session failed: {e}")

    def _session_browser_filter(self):
        """Filter the session table by search text."""
        search = self.session_search_input.text().strip().lower()
        if not search:
            self._session_render_table(self._session_sessions_cache)
            return

        filtered = [
            s for s in self._session_sessions_cache
            if search in s.get('session_id', '').lower()
            or search in s.get('summary', '').lower()
        ]
        self._session_render_table(filtered)

    def _session_render_table(self, sessions):
        """Populate the session table with the given list of session dicts."""
        self.session_table.setSortingEnabled(False)  # Disable during population
        self.session_table.setRowCount(len(sessions))

        for i, s in enumerate(sessions):
            sid = s.get('session_id', '')
            start = s.get('start_time') or '?'
            end = s.get('end_time') or 'ongoing'
            count = str(s.get('entry_count', 0))
            summary = s.get('summary', '')[:100]

            sid_item = QTableWidgetItem(sid)
            sid_item.setToolTip(sid)
            self.session_table.setItem(i, 0, sid_item)
            self.session_table.setItem(i, 1, QTableWidgetItem(start))
            self.session_table.setItem(i, 2, QTableWidgetItem(end))
            self.session_table.setItem(i, 3, QTableWidgetItem(count))
            self.session_table.setItem(i, 4, QTableWidgetItem(summary))

        self.session_table.setSortingEnabled(True)
        self.session_count_label.setText(f"{len(sessions)} session(s)")

    def _session_browser_on_session_selected(self):
        """Load entries for the selected session."""
        selected = self.session_table.selectedItems()
        if not selected:
            self.session_entries_list.clear()
            self.session_entry_preview.clear()
            self.session_entries_label.setText("Select a session to view entries")
            self.entry_count_label.setText("")
            self.session_delete_btn.setEnabled(False)
            self.entry_importance_btn.setEnabled(False)
            self.entry_delete_btn.setEnabled(False)
            self._session_selected_session_id = None
            self._session_selected_entry_id = None
            self._session_entries_cache = []
            return

        row = selected[0].row()
        sid_item = self.session_table.item(row, 0)
        if not sid_item:
            return

        session_id = sid_item.text()
        self._session_selected_session_id = session_id
        self.session_delete_btn.setEnabled(True)

        # Load entries for this session
        try:
            import memory_vault
            entries = memory_vault.get_recent_episodic(
                limit=200, session_id=session_id
            )
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            self.context.log(f"⚠️ Entry load failed: {e}")
            entries = []

        self._session_entries_cache = entries
        self._session_render_entries(entries)

    def _session_render_entries(self, entries):
        """Display entries as a selectable list."""
        self.session_entries_list.clear()
        self.session_entry_preview.clear()
        self.entry_importance_btn.setEnabled(False)
        self.entry_delete_btn.setEnabled(False)
        self._session_selected_entry_id = None

        if not entries:
            self.session_entries_label.setText(
                f"Session: {self._session_selected_session_id}"
            )
            self.entry_count_label.setText("0 entries")
            return

        for e in entries:
            ts = e.get('timestamp', '?')
            imp = e.get('importance_score', 0)
            summary = e.get('summary', '')
            tags = e.get('tags', '[]')
            eid = e.get('id', '?')

            # Build a one-line summary for the list item
            display = f"[{ts}] (ID={eid}, imp={imp}) "
            if len(summary) > 80:
                display += summary[:80] + "..."
            else:
                display += summary

            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, eid)  # Store entry ID
            item.setToolTip(f"Importance: {imp} | Tags: {tags}")
            self.session_entries_list.addItem(item)

        self.session_entries_label.setText(
            f"Session: {self._session_selected_session_id}"
        )
        self.entry_count_label.setText(f"{len(entries)} entr(ies)")

    def _session_browser_on_entry_selected(self, current, previous):
        """Handle entry selection — update preview and enable action buttons."""
        if not current:
            self.session_entry_preview.clear()
            self.entry_importance_btn.setEnabled(False)
            self.entry_delete_btn.setEnabled(False)
            self._session_selected_entry_id = None
            return

        entry_id = current.data(Qt.ItemDataRole.UserRole)
        self._session_selected_entry_id = entry_id

        # Find the full entry text from cache
        full_entry = None
        for e in self._session_entries_cache:
            if e.get('id') == entry_id:
                full_entry = e
                break

        if full_entry:
            ts = full_entry.get('timestamp', '?')
            imp = full_entry.get('importance_score', 0)
            summary = full_entry.get('summary', '')
            tags = full_entry.get('tags', '[]')

            preview = (
                f"Timestamp: {ts}\n"
                f"Importance: {imp}\n"
                f"Tags: {tags}\n"
                f"{'─' * 50}\n"
                f"{summary}"
            )
            self.session_entry_preview.setPlainText(preview)
        else:
            self.session_entry_preview.setPlainText("(entry details not loaded)")

        self.entry_importance_btn.setEnabled(True)
        self.entry_delete_btn.setEnabled(True)

    def _session_browser_boost_importance(self):
        """Boost importance of the selected entry."""
        entry_id = self._session_selected_entry_id
        if not entry_id:
            return

        try:
            import memory_vault
            new_imp = memory_vault.boost_entry_importance(entry_id, increment=1)
            if new_imp > 0:
                self.context.log(f"⭐ Boosted entry {entry_id} to importance={new_imp}")
                # Refresh entries list
                self._session_browser_on_session_selected()
            else:
                self.context.log(f"⚠️ Could not boost entry {entry_id}")
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            self.context.log(f"⚠️ Boost failed: {e}")

    def _session_browser_delete_entry(self):
        """Delete the selected entry from the current session."""
        entry_id = self._session_selected_entry_id
        if not entry_id:
            return

        reply = QMessageBox.question(
            self,
            "Delete Entry",
            f"Delete entry #{entry_id}? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            import memory_vault
            ok = memory_vault.delete_entry(entry_id)
            if ok:
                self.context.log(f"🗑️ Deleted entry {entry_id}")
                self._session_browser_on_session_selected()
            else:
                self.context.log(f"⚠️ Entry {entry_id} not found")
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            self.context.log(f"⚠️ Delete failed: {e}")

    def _session_browser_delete_session(self):
        """Delete all entries for the current session and the session record."""
        sid = self._session_selected_session_id
        if not sid:
            return

        reply = QMessageBox.question(
            self,
            "Delete Session",
            f"Delete session '{sid[:40]}...' and all its entries? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            import memory_vault
            ok = memory_vault.delete_session(sid)
            if ok:
                self.context.log(f"🗑️ Deleted session {sid[:40]}...")
                self._session_selected_session_id = None
                self._session_selected_entry_id = None
                self._session_entries_cache = []
                self._session_browser_refresh()
                self.session_entries_list.clear()
                self.session_entry_preview.clear()
                self.session_entries_label.setText("Session deleted")
                self.session_delete_btn.setEnabled(False)
                self.entry_importance_btn.setEnabled(False)
                self.entry_delete_btn.setEnabled(False)
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            self.context.log(f"⚠️ Session delete failed: {e}")
