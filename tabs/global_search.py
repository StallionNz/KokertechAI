"""
tabs/global_search.py — GlobalSearchDialog: Ctrl+K fuzzy search overlay.

Searches across three data sources:
  1. Current session history (in-memory controller.history)
  2. Episodic journal (semantic + keyword)
  3. Long-term memory vault (semantic + keyword)

Displays results grouped by source with relevance scoring.
Click to preview, double-click or Enter to restore content to chat input.
"""

import json
from datetime import datetime, timezone

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QTreeWidget,
    QTreeWidgetItem, QLabel, QTextEdit, QSplitter, QGroupBox,
    QPushButton, QApplication,
)
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from logging_config import get_logger

logger = get_logger(name="GlobalSearch")


_STYLE = """
QDialog {
    background-color: rgba(18, 18, 18, 240);
    border: 1px solid #3B82F6;
    border-radius: 6px;
}
QLineEdit {
    background-color: #1C1C1C;
    border: 1px solid #3B82F6;
    border-radius: 4px;
    padding: 8px 12px;
    font-size: 13pt;
    color: #E0E0E0;
    selection-background-color: #3B82F6;
}
QLineEdit:focus {
    border: 2px solid #3B82F6;
}
QTreeWidget {
    background-color: #1C1C1C;
    border: 1px solid #2A2A2A;
    border-radius: 4px;
    color: #E0E0E0;
    font-size: 10pt;
    outline: none;
}
QTreeWidget::item {
    padding: 4px 2px;
}
QTreeWidget::item:selected {
    background-color: rgba(59, 130, 246, 0.3);
    color: #FFFFFF;
}
QTreeWidget::item:hover {
    background-color: rgba(59, 130, 246, 0.15);
}
QTreeWidget QHeaderView::section {
    background-color: #121212;
    color: #9CA3AF;
    padding: 4px;
    border: 1px solid #2A2A2A;
    font-weight: bold;
    font-size: 9pt;
}
QLabel#SourceLabel {
    font-size: 9pt;
    color: #9CA3AF;
    padding: 2px 0;
}
QGroupBox {
    border: 1px solid #2A2A2A;
    border-radius: 4px;
    padding-top: 12px;
    margin-top: 6px;
    font-size: 9pt;
    color: #9CA3AF;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}
QTextEdit {
    background-color: #121212;
    border: 1px solid #2A2A2A;
    border-radius: 4px;
    color: #E0E0E0;
    font-size: 10pt;
    padding: 4px;
}
QPushButton {
    background-color: #1C1C1C;
    color: #E0E0E0;
    border: 1px solid #3B82F6;
    border-radius: 4px;
    padding: 4px 12px;
    font-size: 9pt;
}
QPushButton:hover {
    background-color: #3B82F6;
    color: #000;
}
QFrame#Separator {
    background-color: #2A2A2A;
}
"""


class GlobalSearchDialog(QDialog):
    """Ctrl+K overlay dialog for cross-source fuzzy search.

    Opens on top of the parent widget, searches across current session
    history + episodic journal + long-term memory vault, and allows
    restoring selected content to the chat input.

    Signals:
        restore_requested(str): Emitted with full content when the user
            double-clicks a result or presses Enter.
    """

    restore_requested = pyqtSignal(str)

    # ── Source colours ────────────────────────────────────────────────
    SOURCE_COLORS = {
        "session": "#3B82F6",   # Blue
        "episodic": "#10B981",  # Green
        "vault": "#F59E0B",     # Amber
    }
    SOURCE_LABELS = {
        "session": "💬 Current Session",
        "episodic": "📓 Episodic Journal",
        "vault": "📦 Long-Term Memory",
    }

    def __init__(self, parent=None, controller=None, ctx=None):
        super().__init__(parent)
        self.ctx = ctx
        self._controller = controller
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._execute_search)

        self._setup_ui()
        self._apply_styles()

        # Close on Escape
        esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        esc_shortcut.activated.connect(self.close)
        # Enter/Return on focused tree — restore selected item
        self._enter_shortcut = QShortcut(QKeySequence("Return"), self)
        self._enter_shortcut.activated.connect(self._restore_selected)
        # Up/down navigate tree from search input
        self._up_shortcut = QShortcut(QKeySequence("Up"), self)
        self._up_shortcut.activated.connect(self._navigate_up)
        self._down_shortcut = QShortcut(QKeySequence("Down"), self)
        self._down_shortcut.activated.connect(self._navigate_down)

    @property
    def context(self):
        """Return the shared DashboardContext, falling back to parent/legacy attributes if ctx is unset."""
        if hasattr(self, "ctx") and self.ctx is not None:
            return self.ctx
        parent = self.parent()
        parent_ctx = None
        if parent is not None:
            try:
                if hasattr(parent, "context") and parent.context is not None:
                    parent_ctx = parent.context
                elif hasattr(parent, "ctx") and parent.ctx is not None:
                    parent_ctx = parent.ctx
            except (AttributeError, TypeError):
                parent_ctx = None

        if parent_ctx is not None and getattr(self, "_controller", None) is None:
            return parent_ctx

        from tabs.context import DashboardContext
        ctrl = getattr(self, "_controller", None) or (getattr(parent_ctx, "controller", None) if parent_ctx is not None else (getattr(parent, "controller", None) if parent is not None else None))
        return DashboardContext(
            controller=ctrl,
            file_logger=getattr(parent_ctx, "file_logger", None) if parent_ctx is not None else (getattr(parent, "file_logger", None) if parent is not None else None),
            log_to_audit=getattr(parent_ctx, "log_to_audit", None) if parent_ctx is not None else (getattr(parent, "log_to_audit", None) if parent is not None else None),
            audit_signal=getattr(parent_ctx, "audit_signal", None) if parent_ctx is not None else (getattr(parent, "audit_signal", None) if parent is not None else None),
            config=getattr(parent_ctx, "config", {}) if parent_ctx is not None else (getattr(parent, "config", {}) if parent is not None else {}),
            restore_chat_input=getattr(parent_ctx, "restore_chat_input", None) if parent_ctx is not None else (
                (lambda text: parent.txt_input.setPlainText(text))
                if parent is not None and hasattr(parent, "txt_input")
                else None
            ),
            switch_tab=getattr(parent_ctx, "switch_tab", None) if parent_ctx is not None else (
                (lambda idx: parent.tabs.setCurrentIndex(idx))
                if parent is not None and hasattr(parent, "tabs")
                else None
            ),
        )

    @context.setter
    def context(self, value):
        self.ctx = value

    # ── UI Construction ───────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle("Global Search")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setModal(False)
        self.resize(720, 520)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self.setLayout(layout)

        # ── Header bar (Search icon + input + close hint) ──
        header = QHBoxLayout()
        header.setSpacing(6)
        search_icon = QLabel("🔍")
        search_icon.setStyleSheet("font-size: 14pt; color: #9CA3AF;")
        header.addWidget(search_icon)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "Search current session, episodic journal, and memory vault...  (↑↓ navigate, ↵ restore, ⎋ close)"
        )
        self.search_input.textChanged.connect(self._on_text_changed)
        self.search_input.setClearButtonEnabled(True)
        header.addWidget(self.search_input, stretch=1)
        self.result_count_label = QLabel("")
        self.result_count_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        header.addWidget(self.result_count_label)
        layout.addLayout(header)

        # ── Splitter: results tree (top) + preview (bottom) ──
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #2A2A2A; }"
        )

        # Results tree
        self.result_tree = QTreeWidget()
        self.result_tree.setHeaderLabels(["Result", "Source", "Score"])
        self.result_tree.setColumnWidth(0, 400)
        self.result_tree.setColumnWidth(1, 120)
        self.result_tree.setColumnWidth(2, 60)
        self.result_tree.setAlternatingRowColors(True)
        self.result_tree.setAnimated(True)
        self.result_tree.setRootIsDecorated(True)
        self.result_tree.setSelectionMode(
            QTreeWidget.SelectionMode.SingleSelection
        )
        self.result_tree.itemClicked.connect(self._on_item_clicked)
        self.result_tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        splitter.addWidget(self.result_tree)

        # Preview pane
        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(4, 4, 4, 4)

        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setMaximumHeight(120)
        self.preview_text.setPlaceholderText(
            "Click a result to preview full content here"
        )
        preview_layout.addWidget(self.preview_text)

        preview_actions = QHBoxLayout()
        self.btn_copy = QPushButton("📋 Copy")
        self.btn_copy.clicked.connect(self._copy_preview)
        preview_actions.addWidget(self.btn_copy)
        self.btn_restore = QPushButton("✏️ Send to Chat")
        self.btn_restore.clicked.connect(self._restore_selected)
        preview_actions.addWidget(self.btn_restore)
        preview_actions.addStretch()
        preview_layout.addLayout(preview_actions)

        splitter.addWidget(preview_group)
        splitter.setSizes([340, 140])
        layout.addWidget(splitter, stretch=1)

        # ── Footer with search hints ──
        footer = QHBoxLayout()
        hint = QLabel(
            "💡 Tip: Use quotes for exact phrase — otherwise searches word overlap and semantics"
        )
        hint.setStyleSheet("font-size: 8pt; color: #6B7280;")
        footer.addWidget(hint)
        footer.addStretch()
        layout.addLayout(footer)

    def _apply_styles(self):
        self.setStyleSheet(_STYLE)

    # ── Search Logic ──────────────────────────────────────────────────

    def _on_text_changed(self, text):
        """Debounce search: reset timer on each keystroke (300ms delay)."""
        self._search_timer.stop()
        if not text.strip():
            self.result_tree.clear()
            self.result_count_label.setText("")
            return
        self._search_timer.start(300)

    def _execute_search(self):
        """Run the search across all sources (background-invoked via timer)."""
        query = self.search_input.text().strip()
        if not query:
            return

        results = []

        # 1. Search current session history (in-memory)
        results.extend(self._search_session_history(query))

        # 2. Search episodic journal + memory vault via memory_vault
        try:
            import memory_vault
            vault_results = memory_vault.global_search(query, top_k=10)
            for r in vault_results:
                source = r.get("source", "")
                if source == "episodic":
                    results.append({
                        "id": r.get("id"),
                        "source": "episodic",
                        "content": r.get("content", ""),
                        "score": r.get("score", 0.0),
                        "importance": r.get("importance", 0),
                        "timestamp": r.get("timestamp", ""),
                        "tags": r.get("tags", "[]"),
                    })
                elif source in ("core", "core_keyword"):
                    results.append({
                        "id": r.get("id"),
                        "source": "vault",
                        "content": r.get("content", ""),
                        "score": r.get("score", 0.0),
                        "importance": r.get("importance", 0),
                        "timestamp": r.get("timestamp", ""),
                        "tags": r.get("tags", "[]"),
                    })
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as e:
            logger.debug(f"Vault search failed: {e}")

        # Display results
        self._display_results(results)

    def _search_session_history(self, query):
        """Search the in-memory controller.history via keyword match."""
        ctrl = getattr(self.context, "controller", None) or self._controller
        if not ctrl:
            return []

        query_lower = query.lower()
        query_words = set(query_lower.split())
        history = getattr(ctrl, "history", [])
        results = []

        for i, msg in enumerate(history):
            content = msg.get("content", "")
            if not content:
                continue
            content_lower = content.lower()
            role = msg.get("role", "user")

            # Score: exact substring match is strong, word overlap is weaker
            if query_lower in content_lower:
                score = 0.9
            elif query_words & set(content_lower.split()):
                overlap = len(query_words & set(content_lower.split()))
                score = 0.5 * overlap / max(len(query_words), 1)
            else:
                continue

            msg_ts = msg.get("ts", "") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            results.append({
                "id": i,
                "source": "session",
                "content": content,
                "timestamp": msg_ts,
                "importance": 5,  # neutral
                "tags": "[]",
                "score": score,
                "role": role,
            })

        results.sort(key=lambda x: (x["score"], len(x["content"])), reverse=True)
        return results[:15]

    # ── Display ───────────────────────────────────────────────────────

    def _display_results(self, results):
        """Populate the tree with results grouped by source."""
        self.result_tree.clear()
        if not results:
            self.result_count_label.setText("No results")
            return

        query = self.search_input.text().strip()
        # Sort: group by source, then by score within each group
        grouped = {"session": [], "episodic": [], "vault": []}
        for r in results:
            src = r.get("source", "")
            grouped.get(src, []).append(r)

        total_count = 0
        for source_key in ["session", "episodic", "vault"]:
            items = grouped.get(source_key, [])
            if not items:
                continue

            items.sort(key=lambda x: x.get("score", 0), reverse=True)

            # Section header
            label = self.SOURCE_LABELS.get(source_key, source_key)
            color = self.SOURCE_COLORS.get(source_key, "#9CA3AF")
            header_item = QTreeWidgetItem(self.result_tree, [f"{label}", "", ""])
            header_item.setFlags(header_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            header_item.setForeground(0, QColor(color))
            font = header_item.font(0)
            font.setBold(True)
            header_item.setFont(0, font)

            for r in items:
                content = r.get("content", "")
                # Sprint 13: use fts_snippet for highlighted context around query match
                try:
                    import memory_vault
                    snippet = memory_vault.fts_snippet(content, query, max_chars=150)
                except (ValueError, TypeError, KeyError, RuntimeError, OSError, AttributeError):
                    snippet = content[:150] + ("..." if len(content) > 150 else "")
                # Clean newlines for single-line display
                snippet = snippet.replace("\n", " ").replace("\r", "")

                score = r.get("score", 0)
                score_str = f"{score:.1%}" if score <= 1.0 else f"{score:.1f}"

                child = QTreeWidgetItem(header_item, [snippet, source_key, score_str])
                child.setForeground(1, QColor(color))
                child.setData(0, Qt.ItemDataRole.UserRole, r)
                total_count += 1

            self.result_tree.expandItem(header_item)

        self.result_count_label.setText(f"{total_count} results")
        # Select first result if available
        first_item = self.result_tree.itemAt(0, 0)
        if first_item and first_item.childCount() > 0:
            self.result_tree.setCurrentItem(first_item.child(0))
            # Auto-show preview for first item
            self._on_item_clicked(first_item.child(0), 0)

    # ── Interactions ──────────────────────────────────────────────────

    def _on_item_clicked(self, item, column):
        """Show preview of the clicked result."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        content = data.get("content", "")
        source = data.get("source", "")
        score = data.get("score", 0)
        importance = data.get("importance", 0)
        timestamp = data.get("timestamp", "")
        tags_raw = data.get("tags", "[]")
        role = data.get("role", "")

        if isinstance(tags_raw, str):
            try:
                tags = json.loads(tags_raw)[:5]
            except (json.JSONDecodeError, TypeError):
                tags = []
        else:
            tags = (tags_raw or [])[:5]

        tags_str = ", ".join(tags) if tags else "—"

        metadata = f"[{source.upper()}]"
        if role:
            metadata += f" Role: {role}"
        if importance:
            metadata += f" Importance: {importance}/10"
        if timestamp:
            metadata += f" @ {timestamp}"
        score_pct = f"{score:.1%}" if score <= 1.0 else f"{score:.1f}"
        metadata += f" | Score: {score_pct}"
        if tags_str and tags_str != "—":
            metadata += f" | Tags: {tags_str}"

        # Sprint 13 #26: search explanation
        try:
            import memory_vault
            result_tuple = (data.get('id'), data.get('source', ''), data.get('content', ''), data.get('score', 0))
            explanation = memory_vault.search_explanation(self.search_input.text(), result_tuple)
            reasons = explanation.get('reasons', [])
            if reasons:
                metadata = metadata + chr(10) + chr(10024) + repr(' Why this matched:') + chr(10) + chr(10).join([repr('  ') + chr(8226) + repr(' ') + r for r in reasons])
        except (ValueError, TypeError, KeyError, RuntimeError, OSError, AttributeError) as e:
            logger.debug(f"Search-explanation enrichment failed: {e}")

        self.preview_text.setPlainText(f"{metadata}\n{'-' * 50}\n{content}")
        self.preview_text.verticalScrollBar().setValue(0)

    def _on_item_double_clicked(self, item, column):
        """Restore content to chat input on double-click."""
        self._restore_selected()

    def _restore_selected(self):
        """Emit the full content of the selected item for chat input."""
        item = self.result_tree.currentItem()
        if not item:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        content = data.get("content", "")
        if content:
            if hasattr(self, "context") and self.context is not None:
                self.context.log(f"Global search restored content: {content[:40]}...")
                if callable(getattr(self.context, "restore_chat_input", None)):
                    self.context.restore_chat_input(content)
            self.restore_requested.emit(content)

    def _copy_preview(self):
        """Copy preview text to clipboard."""
        text = self.preview_text.toPlainText()
        if text:
            # Strip metadata line (everything before first ---)
            clean_text = text.split("-" * 50, 1)[-1].strip() if "-" * 50 in text else text
            QApplication.clipboard().setText(clean_text)

    def _navigate_up(self):
        """Move selection up in the tree."""
        current = self.result_tree.currentItem()
        if not current:
            return
        parent = current.parent()
        if not parent:
            return  # on a section header, do nothing
        idx = parent.indexOfChild(current)
        if idx > 0:
            prev = parent.child(idx - 1)
            self.result_tree.setCurrentItem(prev)
            self._on_item_clicked(prev, 0)
        elif idx == 0:
            # Wrap to last item of previous section
            root = self.result_tree.invisibleRootItem()
            section_idx = root.indexOfChild(parent)
            if section_idx > 0:
                prev_section = root.child(section_idx - 1)
                if prev_section and prev_section.childCount() > 0:
                    last_child = prev_section.child(prev_section.childCount() - 1)
                    self.result_tree.setCurrentItem(last_child)
                    self._on_item_clicked(last_child, 0)

    def _navigate_down(self):
        """Move selection down in the tree."""
        current = self.result_tree.currentItem()
        if not current:
            # Select first non-header child
            root = self.result_tree.invisibleRootItem()
            for i in range(root.childCount()):
                section = root.child(i)
                if section.childCount() > 0:
                    first = section.child(0)
                    self.result_tree.setCurrentItem(first)
                    self._on_item_clicked(first, 0)
                    return
            return

        parent = current.parent()
        if not parent:
            return  # on a section header, do nothing

        idx = parent.indexOfChild(current)
        if idx < parent.childCount() - 1:
            next_item = parent.child(idx + 1)
            self.result_tree.setCurrentItem(next_item)
            self._on_item_clicked(next_item, 0)
        else:
            # Wrap to first item of next section
            root = self.result_tree.invisibleRootItem()
            section_idx = root.indexOfChild(parent)
            if section_idx < root.childCount() - 1:
                next_section = root.child(section_idx + 1)
                if next_section and next_section.childCount() > 0:
                    first_child = next_section.child(0)
                    self.result_tree.setCurrentItem(first_child)
                    self._on_item_clicked(first_child, 0)

    # ── Dialog Lifecycle ──────────────────────────────────────────────

    def showEvent(self, event):
        """Clear previous state and focus the search input when shown."""
        super().showEvent(event)
        self.search_input.clear()
        self.search_input.setFocus()
        self.result_tree.clear()
        self.preview_text.clear()
        self.result_count_label.setText("")

        # Center on parent
        parent = self.parent()
        if parent:
            parent_rect = parent.geometry()
            x = parent_rect.x() + (parent_rect.width() - self.width()) // 2
            y = parent_rect.y() + (parent_rect.height() - self.height()) // 3
            self.move(x, y)

    def keyPressEvent(self, event):
        """Handle Escape to close."""
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
