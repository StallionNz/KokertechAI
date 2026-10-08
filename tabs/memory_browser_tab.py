"""
tabs/memory_browser_tab.py — Memory Browser & Editor Tab.

Browse, search, edit, delete, and boost importance of entries in both
the episodic journal and core memories tables. Provides a tabbed interface
with search, inline editing, and confirmation dialogs before destructive actions.

Sprint 7 extended scope (Feature suggestion #13).
"""

import json
import sqlite3
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QLineEdit, QSplitter, QTextEdit, QGroupBox, QMessageBox,
    QSpinBox,
)

if TYPE_CHECKING:
    from tabs.context import DashboardContext


class MemoryBrowserTabMixin:
    """Mixin that adds a Memory Browser tab for vault management."""

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

    def create_memory_browser_tab(self):
        """Build and return the Memory Browser tab widget."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── Header ──────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("🔍 Memory Browser & Editor")
        title.setStyleSheet("font-size: 12pt; font-weight: bold; color: #E0E0E0;")
        header.addWidget(title)
        header.addStretch()

        self.mem_stats_label = QLabel("")
        self.mem_stats_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        header.addWidget(self.mem_stats_label)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._refresh_memory_browser)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # ── Search bar ──────────────────────────────────────────────
        search_row = QHBoxLayout()
        self.mem_search_input = QLineEdit()
        self.mem_search_input.setPlaceholderText("Search entries... (filters current table view)")
        self.mem_search_input.textChanged.connect(self._filter_memory_table)
        search_row.addWidget(self.mem_search_input)
        layout.addLayout(search_row)

        # ── Splitter: table (top) + detail (bottom) ────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Tab widget for switching between sources
        self.mem_source_tabs = QTabWidget()
        self.mem_source_tabs.currentChanged.connect(self._on_mem_source_changed)

        # ── Tab 1: Episodic Journal ──
        epi_tab = QWidget()
        epi_layout = QVBoxLayout(epi_tab)
        epi_layout.setContentsMargins(0, 0, 0, 0)

        self.episodic_table = QTableWidget()
        self.episodic_table.setColumnCount(6)
        self.episodic_table.setHorizontalHeaderLabels(
            ["ID", "Timestamp", "Summary", "Imp.", "Tags", "Session"]
        )
        self.episodic_table.setAlternatingRowColors(True)
        self.episodic_table.horizontalHeader().setStretchLastSection(True)
        self.episodic_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.episodic_table.setColumnWidth(0, 50)
        self.episodic_table.setColumnWidth(1, 140)
        self.episodic_table.setColumnWidth(3, 40)
        self.episodic_table.setColumnWidth(4, 100)
        self.episodic_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.episodic_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.episodic_table.verticalHeader().setVisible(False)
        self.episodic_table.itemSelectionChanged.connect(
            self._on_mem_table_selection_changed
        )
        epi_layout.addWidget(self.episodic_table)
        self.mem_source_tabs.addTab(epi_tab, "📓 Episodic Journal")

        # ── Tab 2: Core Memories ──
        core_tab = QWidget()
        core_layout = QVBoxLayout(core_tab)
        core_layout.setContentsMargins(0, 0, 0, 0)

        self.core_table = QTableWidget()
        self.core_table.setColumnCount(5)
        self.core_table.setHorizontalHeaderLabels(
            ["ID", "Timestamp", "Content", "Type", "Imp."]
        )
        self.core_table.setAlternatingRowColors(True)
        self.core_table.horizontalHeader().setStretchLastSection(True)
        self.core_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.core_table.setColumnWidth(0, 50)
        self.core_table.setColumnWidth(1, 140)
        self.core_table.setColumnWidth(3, 80)
        self.core_table.setColumnWidth(4, 40)
        self.core_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.core_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.core_table.verticalHeader().setVisible(False)
        self.core_table.itemSelectionChanged.connect(
            self._on_mem_table_selection_changed
        )
        core_layout.addWidget(self.core_table)
        self.mem_source_tabs.addTab(core_tab, "📦 Core Memories")

        splitter.addWidget(self.mem_source_tabs)

        # ── Detail panel ────────────────────────────────────────────
        detail_group = QGroupBox("Entry Detail & Actions")
        detail_layout = QVBoxLayout(detail_group)

        # Metadata row
        self.mem_meta_label = QLabel("Select an entry to view and edit")
        self.mem_meta_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        self.mem_meta_label.setWordWrap(True)
        detail_layout.addWidget(self.mem_meta_label)

        # Editable content
        self.mem_editor = QTextEdit()
        self.mem_editor.setPlaceholderText("Entry content appears here. Edit and click Save.")
        self.mem_editor.setMaximumHeight(200)
        detail_layout.addWidget(self.mem_editor)

        # Action buttons
        actions = QHBoxLayout()

        self.mem_btn_save = QPushButton("💾 Save Changes")
        self.mem_btn_save.setStyleSheet(
            "background-color: #1e3a5f; border-color: #2563eb;"
        )
        self.mem_btn_save.clicked.connect(self._mem_save_changes)
        self.mem_btn_save.setEnabled(False)
        actions.addWidget(self.mem_btn_save)

        self.mem_btn_boost = QPushButton("⬆ Boost Importance")
        self.mem_btn_boost.clicked.connect(self._mem_boost_importance)
        self.mem_btn_boost.setEnabled(False)
        actions.addWidget(self.mem_btn_boost)

        self.mem_boost_spin = QSpinBox()
        self.mem_boost_spin.setRange(1, 5)
        self.mem_boost_spin.setValue(1)
        self.mem_boost_spin.setToolTip("Boost increment (1-5)")
        self.mem_boost_spin.setFixedWidth(50)
        actions.addWidget(self.mem_boost_spin)

        actions.addStretch()

        self.mem_btn_delete = QPushButton("🗑️ Delete Entry")
        self.mem_btn_delete.setStyleSheet(
            "background-color: #7f1d1d; border-color: #991b1b;"
        )
        self.mem_btn_delete.clicked.connect(self._mem_delete_entry)
        self.mem_btn_delete.setEnabled(False)
        actions.addWidget(self.mem_btn_delete)

        detail_layout.addLayout(actions)
        splitter.addWidget(detail_group)
        splitter.setSizes([400, 250])

        layout.addWidget(splitter, 1)

        # Initial load
        QTimer.singleShot(200, self._refresh_memory_browser)

        return tab

    # ── Data Loading ───────────────────────────────────────────────

    def _refresh_memory_browser(self):
        """Reload both tables from the memory vault."""
        # Guard: this may be called from a QTimer after the tab's C++
        # widgets have been deleted (e.g., during test teardown or shutdown).
        # hasattr isn't sufficient — the Python wrapper survives even when
        # the underlying C++ object is deleted.
        try:
            self.mem_btn_save.isEnabled()
        except RuntimeError:
            return
        import memory_vault

        self._current_mem_id = None
        self._current_mem_source = None
        self.mem_btn_save.setEnabled(False)
        self.mem_btn_boost.setEnabled(False)
        self.mem_btn_delete.setEnabled(False)

        # Load episodic journal
        try:
            episodes = memory_vault.get_recent_episodic(limit=500)
            self.episodic_table.setRowCount(len(episodes))
            for row, ep in enumerate(episodes):
                ep_id = ep.get("id", "?")
                ts = ep.get("timestamp", "")
                summary = ep.get("summary", "")
                importance = ep.get("importance_score", 5)
                tags_raw = ep.get("tags", "[]")
                session_id = ep.get("session_id", "")
                if isinstance(tags_raw, str):
                    try:
                        tags = json.loads(tags_raw)
                    except (json.JSONDecodeError, TypeError):
                        tags = []
                else:
                    tags = tags_raw or []
                tags_str = ", ".join(t[:15] for t in tags[:3])

                self.episodic_table.setItem(row, 0, QTableWidgetItem(str(ep_id)))
                self.episodic_table.setItem(row, 1, QTableWidgetItem(ts or ""))
                preview = summary[:120] + ("..." if len(summary) > 120 else "")
                self.episodic_table.setItem(row, 2, QTableWidgetItem(preview))
                self.episodic_table.setItem(row, 3, QTableWidgetItem(str(importance)))
                self.episodic_table.setItem(row, 4, QTableWidgetItem(tags_str))
                self.episodic_table.setItem(row, 5, QTableWidgetItem(session_id[:40] if session_id else ""))

                # Store full data in UserRole
                self.episodic_table.item(row, 0).setData(
                    Qt.ItemDataRole.UserRole,
                    {
                        "id": ep_id,
                        "source": "episodic",
                        "full_content": summary,
                        "importance": importance,
                        "tags": tags,
                        "session_id": session_id,
                        "timestamp": ts,
                    }
                )
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            self.episodic_table.setRowCount(1)
            self.episodic_table.setItem(0, 0, QTableWidgetItem(f"Error: {e}"))

        # Load core memories
        try:
            memories = memory_vault.get_all_core_memories(limit=200)
            self.core_table.setRowCount(len(memories))
            for row, r in enumerate(memories):
                self.core_table.setItem(row, 0, QTableWidgetItem(str(r["id"])))
                self.core_table.setItem(row, 1, QTableWidgetItem((r.get("timestamp") or "")))
                content = r.get("content", "")
                preview = content[:120] + ("..." if len(content) > 120 else "")
                self.core_table.setItem(row, 2, QTableWidgetItem(preview))
                self.core_table.setItem(row, 3, QTableWidgetItem(r.get("node_type") or "fact"))
                self.core_table.setItem(row, 4, QTableWidgetItem(str(r.get("importance_score") or 5)))

                self.core_table.item(row, 0).setData(
                    Qt.ItemDataRole.UserRole,
                    {
                        "id": r["id"],
                        "source": "core",
                        "full_content": content,
                        "node_type": r.get("node_type"),
                        "importance": r.get("importance_score") or 5,
                        "tags": r.get("tags"),
                        "timestamp": r.get("timestamp"),
                    }
                )
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            self.core_table.setRowCount(1)
            self.core_table.setItem(0, 0, QTableWidgetItem(f"Error: {e}"))

        # Update stats
        epi_count = self.episodic_table.rowCount()
        core_count = self.core_table.rowCount()
        self.mem_stats_label.setText(
            f"📓 {epi_count} episodic  |  📦 {core_count} core memories"
        )

    def _on_mem_source_changed(self, index):
        """Clear detail panel when switching tabs."""
        self._clear_mem_detail()

    def _on_mem_table_selection_changed(self):
        """Populate detail panel when an entry is selected."""
        source_tab = self.mem_source_tabs.currentIndex()
        if source_tab == 0:
            table = self.episodic_table
        else:
            table = self.core_table

        selected = table.selectedItems()
        if not selected:
            self._clear_mem_detail()
            return

        row = selected[0].row()
        item = table.item(row, 0)
        if not item:
            self._clear_mem_detail()
            return

        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            self._clear_mem_detail()
            return

        self._current_mem_id = data["id"]
        self._current_mem_source = data["source"]

        # Build metadata string
        lines = []
        lines.append(f"ID: {data['id']}  |  Source: {data['source'].upper()}")
        lines.append(f"Timestamp: {data.get('timestamp', '?')}")
        lines.append(f"Importance: {data.get('importance', '?')}/10")
        if data.get("tags"):
            tags = data.get("tags", [])
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (json.JSONDecodeError, TypeError):
                    tags = [tags]
            lines.append(f"Tags: {', '.join(str(t)[:20] for t in tags)}")
        if data.get("node_type"):
            lines.append(f"Type: {data['node_type']}")
        if data.get("session_id"):
            lines.append(f"Session: {data['session_id']}")

        self.mem_meta_label.setText("  |  ".join(lines))

        # Set editor with full content
        content = data.get("full_content", "")
        self.mem_editor.setPlainText(content)
        self.mem_btn_save.setEnabled(True)
        self.mem_btn_boost.setEnabled(True)
        self.mem_btn_delete.setEnabled(True)

    def _clear_mem_detail(self):
        """Clear the detail panel."""
        # Guard: this may be called during QTabWidget.addTab() before the
        # detail panel widgets are fully constructed (currentChanged fires
        # on first tab addition), or after C++ objects have been deleted
        # (e.g. during test teardown).
        if not hasattr(self, "mem_meta_label"):
            return
        try:
            self._current_mem_id = None
            self._current_mem_source = None
            self.mem_meta_label.setText("Select an entry to view and edit")
            self.mem_editor.clear()
            self.mem_btn_save.setEnabled(False)
            self.mem_btn_boost.setEnabled(False)
            self.mem_btn_delete.setEnabled(False)
        except RuntimeError:
            pass  # C++ object deleted

    # ── Actions ────────────────────────────────────────────────────

    def _mem_save_changes(self):
        """Save edited content to the vault."""
        entry_id = self._current_mem_id
        source = self._current_mem_source
        if entry_id is None or source is None:
            return

        new_content = self.mem_editor.toPlainText().strip()
        if not new_content:
            QMessageBox.warning(self.mem_editor, "Save", "Content cannot be empty.")
            return

        import memory_vault
        ok = memory_vault.update_entry_content(
            entry_id=entry_id,
            new_content=new_content,
            source=source,
        )

        if ok:
            self.context.log(f"💾 Memory entry {entry_id} ({source}) updated")
            # Refresh the table to show updated content
            self._refresh_memory_browser()
        else:
            QMessageBox.warning(
                self.mem_editor,
                "Save Failed",
                f"Could not update entry {entry_id}. It may have been deleted.",
            )

    def _mem_boost_importance(self):
        """Boost the importance of the selected entry."""
        source = self._current_mem_source
        if source != "episodic":
            QMessageBox.information(
                self.mem_editor,
                "Boost",
                "Importance boosting is only available for episodic journal entries.",
            )
            return

        entry_id = self._current_mem_id
        increment = self.mem_boost_spin.value()
        if entry_id is None:
            return

        import memory_vault
        new_importance = memory_vault.boost_entry_importance(
            entry_id=entry_id,
            increment=increment,
        )

        if new_importance >= 0:
            self.context.log(
                f"⬆ Entry {entry_id} importance boosted to {new_importance}/10"
            )
            self._refresh_memory_browser()
        else:
            QMessageBox.warning(
                self.mem_editor,
                "Boost Failed",
                f"Could not boost entry {entry_id}. It may have been deleted.",
            )

    def _mem_delete_entry(self):
        """Delete the selected entry with confirmation."""
        entry_id = self._current_mem_id
        source = self._current_mem_source
        if entry_id is None or source is None:
            return

        label = "episodic journal entry" if source == "episodic" else "core memory"
        reply = QMessageBox.question(
            self.mem_editor,
            "Confirm Deletion",
            f"Permanently delete {label} #{entry_id}?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        import memory_vault
        ok = memory_vault.delete_entry(entry_id=entry_id, source=source)

        if ok:
            self.context.log(f"🗑️ Deleted {label} #{entry_id}")
            self._refresh_memory_browser()
        else:
            QMessageBox.warning(
                self.mem_editor,
                "Delete Failed",
                f"Could not delete entry {entry_id}. It may have already been removed.",
            )

    # ── Search / Filter ────────────────────────────────────────────

    def _filter_memory_table(self, text):
        """Filter the currently active table by search text."""
        text = text.strip().lower()
        source_tab = self.mem_source_tabs.currentIndex()
        table = self.episodic_table if source_tab == 0 else self.core_table

        for row in range(table.rowCount()):
            visible = False
            for col in range(table.columnCount()):
                item = table.item(row, col)
                if item and text in item.text().lower():
                    visible = True
                    break
            table.setRowHidden(row, not visible)
