"""
tabs/chat_history_tab.py — Multi-Turn Chat History Tree.

Collapsible session tree that replaces the flat scrollback.
Shows the current session's messages and past session summaries
from the episodic journal in a single navigable tree.

Sprint 6 backlog (Feature suggestion #2).
"""

import json
import sqlite3
from datetime import datetime, timezone

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QLineEdit, QSplitter,
    QTextEdit, QGroupBox, QApplication, QFileDialog, QMessageBox,
)

from PyQt6.QtGui import QColor

from config import CONFIG
from logging_config import get_logger

logger = get_logger(name="ChatHistoryTab")


class ChatHistoryTabMixin:
    """Mixin that adds a Chat History tab with a collapsible session tree.

    Displays:
      - Current session messages grouped into conversation turns.
      - Past sessions from the episodic journal.
      - Search/filter bar to quickly find messages.

    Integrates with the dashboard's controller.history and memory_vault.
    """

    @property
    def context(self):
        """Return the shared DashboardContext, falling back to legacy attributes if ctx is unset."""
        if hasattr(self, "ctx") and self.ctx is not None:
            return self.ctx
        from tabs.context import DashboardContext
        return DashboardContext(
            controller=getattr(self, "controller", None),
            file_logger=getattr(self, "file_logger", None),
            log_to_audit=getattr(self, "log_to_audit", None),
            audit_signal=getattr(self, "audit_signal", None),
            config=getattr(self, "config", CONFIG),
        )

    @context.setter
    def context(self, value):
        self.ctx = value

    def create_chat_history_tab(self):
        """Build and return the Chat History tab widget."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Toolbar ────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        self.chat_search_input = QLineEdit()
        self.chat_search_input.setPlaceholderText("Search messages... (Ctrl+F)")
        self.chat_search_input.textChanged.connect(self._filter_chat_tree)
        toolbar.addWidget(self.chat_search_input)

        toolbar.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_chat_history)
        toolbar.addWidget(refresh_btn)

        export_md_btn = QPushButton("Export Markdown")
        export_md_btn.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        export_md_btn.clicked.connect(self._export_as_markdown)
        toolbar.addWidget(export_md_btn)

        export_json_btn = QPushButton("Export JSON")
        export_json_btn.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        export_json_btn.clicked.connect(self._export_as_json)
        toolbar.addWidget(export_json_btn)

        import_json_btn = QPushButton("Import JSON")
        import_json_btn.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        import_json_btn.clicked.connect(self._import_as_json)
        toolbar.addWidget(import_json_btn)

        import_md_btn = QPushButton("Import Markdown")
        import_md_btn.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        import_md_btn.clicked.connect(self._import_as_markdown)
        toolbar.addWidget(import_md_btn)

        export_html_btn = QPushButton("Export HTML")
        export_html_btn.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        export_html_btn.clicked.connect(self._export_as_html)
        toolbar.addWidget(export_html_btn)

        clear_btn = QPushButton("Clear Current Session")
        clear_btn.setStyleSheet("background-color: #7f1d1d; border-color: #991b1b;")
        clear_btn.clicked.connect(self._clear_current_session)
        toolbar.addWidget(clear_btn)

        layout.addLayout(toolbar)

        # ── Tree + Preview splitter ────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)

        self.chat_history_tree = QTreeWidget()
        self.chat_history_tree.setHeaderLabels(["Session / Message", "Role", "Time"])
        self.chat_history_tree.setAlternatingRowColors(True)
        self.chat_history_tree.setColumnWidth(0, 400)
        self.chat_history_tree.setColumnWidth(1, 80)
        self.chat_history_tree.setColumnWidth(2, 140)
        self.chat_history_tree.itemClicked.connect(self._on_tree_item_clicked)

        # Rich context menu (right-click) for quick actions on tree items.
        self.chat_history_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_history_tree.customContextMenuRequested.connect(
            self._on_tree_context_menu
        )
        splitter.addWidget(self.chat_history_tree)


        # Preview pane
        preview_group = QGroupBox("Message Preview")
        preview_layout = QVBoxLayout(preview_group)
        self.chat_preview = QTextEdit()
        self.chat_preview.setReadOnly(True)
        self.chat_preview.setMaximumHeight(150)
        self.chat_preview.setPlaceholderText("Click a message to preview it here")
        preview_layout.addWidget(self.chat_preview)

        preview_actions = QHBoxLayout()
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(self._copy_preview_to_clipboard)
        preview_actions.addWidget(copy_btn)

        restore_btn = QPushButton("Send to Chat Input")
        restore_btn.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        restore_btn.clicked.connect(self._restore_to_chat_input)
        preview_actions.addWidget(restore_btn)

        preview_layout.addLayout(preview_actions)
        splitter.addWidget(preview_group)

        layout.addWidget(splitter)

        # ── Stats bar ──────────────────────────────────────────────
        self.chat_stats_label = QLabel("Sessions: — | Messages: —")
        self.chat_stats_label.setStyleSheet("font-size: 9pt; color: #9CA3AF; padding: 2px;")
        layout.addWidget(self.chat_stats_label)

        # Initial load
        QTimer.singleShot(100, self._refresh_chat_history)

        return tab

    # ── Data Loading ───────────────────────────────────────────────

    def _refresh_chat_history(self):
        """Reload the tree from the controller's history and episodic journal."""
        self.chat_history_tree.clear()
        total_messages = 0

        # Section 1: Current Session
        current_session_item = QTreeWidgetItem(
            self.chat_history_tree,
            ["💬 Current Session", "", ""],
        )
        current_session_item.setFlags(current_session_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)

        ctrl = getattr(self.context, "controller", None) if hasattr(self, "context") else getattr(self, "controller", None)
        history = getattr(ctrl, "history", [])
        if not history:
            child = QTreeWidgetItem(current_session_item, ["(no messages yet)", "", ""])
            child.setForeground(0, Qt.GlobalColor.gray)
        else:
            # Group into turns (user message + assistant response)
            turn_count = 0
            for _i, msg in enumerate(history):
                role = msg.get("role", "?")
                content = msg.get("content", "")
                preview = content[:120] + ("..." if len(content) > 120 else "")
                ts = msg.get("ts", "")

                color = "#10B981" if role == "assistant" else "#3B82F6" if role == "user" else "#9CA3AF"
                role_label = "🤖 AI" if role == "assistant" else "👤 User" if role == "user" else f"⚙️ {role}"

                child = QTreeWidgetItem(current_session_item, [preview, role_label, ts])
                child.setForeground(1, QColor(color))
                child.setData(0, Qt.ItemDataRole.UserRole, {
                    "type": "message",
                    "role": role,
                    "content": content,
                    "ts": ts,
                    "source": "current",
                })
                total_messages += 1

                if role == "assistant":
                    turn_count += 1

            # Update session header with count
            current_session_item.setText(2, f"{turn_count} turns")

        self.chat_history_tree.expandItem(current_session_item)

        # Section 2: Episodic Journal (Past Sessions)
        past_sessions_item = QTreeWidgetItem(
            self.chat_history_tree,
            ["📓 Past Sessions (Episodic Journal)", "", ""],
        )
        past_sessions_item.setFlags(past_sessions_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)

        try:
            import memory_vault
            episodes = memory_vault.get_recent_episodic(limit=50)
            if not episodes:
                child = QTreeWidgetItem(past_sessions_item, ["(no past sessions recorded yet)", "", ""])
                child.setForeground(0, Qt.GlobalColor.gray)
            else:
                for ep in episodes:
                    ep_ts = ep.get("timestamp", "?")
                    summary = ep.get("summary", "")
                    importance = ep.get("importance_score", 0)
                    preview = summary[:150] + ("..." if len(summary) > 150 else "")
                    tags_raw = ep.get("tags", "[]")
                    if isinstance(tags_raw, str):
                        try:
                            tags = json.loads(tags_raw)
                        except (json.JSONDecodeError, TypeError):
                            tags = []
                    else:
                        tags = tags_raw or []
                    tags_str = ", ".join(tags[:3]) if tags else ""

                    ses_item = QTreeWidgetItem(
                        past_sessions_item,
                        [preview, f"⭐ {importance}", ep_ts],
                    )
                    ses_item.setToolTip(0, f"Tags: {tags_str}" if tags_str else f"Importance: {importance}")
                    ses_item.setData(0, Qt.ItemDataRole.UserRole, {
                        "type": "episodic",
                        "summary": summary,
                        "ts": ep_ts,
                        "importance": importance,
                        "tags": tags,
                        "source": "journal",
                    })
                    total_messages += 1

                past_sessions_item.setText(2, f"{len(episodes)} entries")
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as e:
            child = QTreeWidgetItem(past_sessions_item, [f"(journal unavailable: {e})", "", ""])
            child.setForeground(0, Qt.GlobalColor.red)
            logger.warning(f"Failed to load episodic journal: {e}")

        self.chat_history_tree.expandItem(past_sessions_item)

        # Section 3: Long-Term Memory (Vault)
        vault_item = QTreeWidgetItem(
            self.chat_history_tree,
            ["📦 Long-Term Memory Vault", "", ""],
        )
        vault_item.setFlags(vault_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)

        vault_text = ""
        try:
            vault_text = memory_vault.retrieve_all_memories()
            if vault_text and vault_text != "Vault empty." and vault_text != "Vault unavailable.":
                entries = vault_text.split(" | ")
                for entry_text in entries[:20]:  # Limit to 20 vault entries
                    preview = entry_text[:150] + ("..." if len(entry_text) > 150 else "")
                    child = QTreeWidgetItem(vault_item, [preview, "📄", ""])
                    child.setData(0, Qt.ItemDataRole.UserRole, {
                        "type": "vault",
                        "content": entry_text,
                        "source": "vault",
                    })
                    total_messages += 1
                vault_item.setText(2, f"{min(len(entries), 20)} entries")
            else:
                child = QTreeWidgetItem(vault_item, ["(no long-term memories)", "", ""])
                child.setForeground(0, Qt.GlobalColor.gray)
        except (RuntimeError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as e:
            child = QTreeWidgetItem(vault_item, [f"(vault unavailable: {e})", "", ""])
            child.setForeground(0, Qt.GlobalColor.red)

        # Update stats
        session_count = 2  # Current + Past
        if vault_text and vault_text not in ("Vault empty.", "Vault unavailable."):
            session_count += 1
        self.chat_stats_label.setText(
            f"Sessions: {session_count} | Messages: {total_messages}"
        )
        logger.info(f"Chat history tree refreshed: {total_messages} messages across {session_count} sessions")

    # ── Import ──────────────────────────────────────────────────────

    def _import_as_json(self):
        """Import a JSON chat history file and restore messages to the chat input."""
        file_path, _ = QFileDialog.getOpenFileName(
            self.chat_history_tree,
            "Import JSON Chat History",
            "",
            "JSON files (*.json);;All files (*.*)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as e:
            QMessageBox.warning(self.chat_history_tree, "Import Failed",
                                f"Could not read file:\n{e}")
            return

        # Validate structure
        sessions = data.get("sessions", [])
        if not sessions and "sessions" not in data:
            # Maybe it's a bare list or just a sessions array
            sessions = data if isinstance(data, list) else [data]

        # Extract messages and restore first user message to chat input
        messages_found = 0
        first_user_content = None

        for section in sessions:
            items = section.get("items", []) if isinstance(section, dict) else \
                    (section.get("messages", []) if isinstance(section, dict) else [])
            for item in items:
                if isinstance(item, dict):
                    role = item.get("role", "")
                    content = item.get("content") or item.get("summary") or ""
                    if content:
                        messages_found += 1
                        if first_user_content is None and role.lower() in ("user", "👤"):
                            first_user_content = content

        if messages_found == 0:
            QMessageBox.information(
                self.chat_history_tree, "Import",
                "No messages found in the imported file."
            )
            return

        # Restore the first user message to the chat input via context bridge or fallback
        restored = False
        if first_user_content:
            ctx = getattr(self, "context", None)
            if ctx is not None and ctx.restore_chat_input is not None:
                ctx.restore_chat_input(first_user_content[:2000])
                restored = True
            elif hasattr(self, 'txt_input'):
                self.txt_input.setPlainText(first_user_content[:2000])
                restored = True

        if restored:
            QMessageBox.information(
                self.chat_history_tree, "Import Complete",
                f"Found {messages_found} message(s).\n"
                f"First user message restored to chat input.\n"
                f"Refresh the Chat History tree to see imported data in context."
            )
        else:
            QMessageBox.information(
                self.chat_history_tree, "Import Complete",
                f"Found {messages_found} message(s).\n"
                "No user messages to restore to input."
            )

        self.context.log(f"📥 Imported JSON chat: {messages_found} messages from {file_path}")
        logger.info(f"Chat history imported from JSON: {file_path} ({messages_found} messages)")

    def _import_as_markdown(self):
        """Import a Markdown chat history file and restore the first message."""
        file_path, _ = QFileDialog.getOpenFileName(
            self.chat_history_tree,
            "Import Markdown Chat History",
            "",
            "Markdown files (*.md);;All files (*.*)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except (OSError, UnicodeDecodeError, ValueError) as e:
            QMessageBox.warning(self.chat_history_tree, "Import Failed",
                                f"Could not read file:\n{e}")
            return

        if not content.strip():
            QMessageBox.information(self.chat_history_tree, "Import",
                                    "The file is empty.")
            return

        # Extract first user message from markdown (look for 👤 or USER headings)
        import re
        user_match = re.search(
            r'### 👤 (?:USER|User).*?\n(.*?)(?:\n### |\Z)',
            content, re.DOTALL
        )
        first_content = user_match.group(1).strip() if user_match else content[:500]

        # Restore to chat input via context bridge or fallback
        ctx = getattr(self, "context", None)
        if ctx is not None and ctx.restore_chat_input is not None:
            ctx.restore_chat_input(first_content[:2000])
        elif hasattr(self, 'txt_input'):
            self.txt_input.setPlainText(first_content[:2000])

        char_count = len(content)
        QMessageBox.information(
            self.chat_history_tree, "Import Complete",
            f"Loaded {char_count} characters from Markdown.\n"
            f"First user message restored to chat input."
        )
        self.context.log(f"📥 Imported Markdown chat: {char_count} chars from {file_path}")
        logger.info(f"Chat history imported from Markdown: {file_path} ({char_count} chars)")

    def _export_as_html(self):
        """Export the current chat tree as a styled HTML file for easy viewing."""
        data = self._collect_tree_data()
        if not data:
            QMessageBox.information(self.chat_history_tree, "Export", "No data to export.")
            return

        html_parts = [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="UTF-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
            "<title>Chat History Export</title>",
            "<style>",
            "  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; ",
            "         background: #0d1117; color: #c9d1d9; padding: 20px; line-height: 1.6; }",
            "  h1 { color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 8px; }",
            "  h2 { color: #f0c674; margin-top: 24px; }",
            "  h3 { color: #8b949e; font-size: 14px; }",
            "  .message { background: #161b22; border: 1px solid #30363d; border-radius: 6px; ",
            "             padding: 8px; margin: 6px 0; }",
            "  .user { border-left: 3px solid #58a6ff; }",
            "  .assistant { border-left: 3px solid #3fb950; }",
            "  .episodic { border-left: 3px solid #f0c674; }",
            "  .vault { border-left: 3px solid #8b5cf6; }",
            "  .meta { color: #8b949e; font-size: 12px; margin-bottom: 4px; }",
            "  .content { white-space: pre-wrap; }",
            "  .tag { display: inline-block; background: #21262d; color: #8b949e; ",
            "         border-radius: 10px; padding: 0 6px; font-size: 11px; }",
            "  hr { border: none; border-top: 1px solid #30363d; margin: 16px 0; }",
            "</style>",
            "</head>",
            "<body>",
            "<h1>Chat History Export</h1>",
            f"<p style='color: #8b949e;'>Exported on "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}</p>",
            "<hr>",
        ]

        for section in data:
            html_parts.append(f"<h2>{section['section']}</h2>")
            for item in section["items"]:
                msg_type = item.get("type", "")
                role = item.get("role", "")
                ts = item.get("timestamp", "")
                content = item.get("content") or item.get("summary") or ""
                importance = item.get("importance", "")
                tags = item.get("tags", [])
                tags_str = " ".join(
                    f'<span class="tag">{t}</span>' for t in tags
                ) if tags else ""

                if msg_type == "message":
                    emoji = "🤖" if role == "assistant" else "👤"
                    role_label = role.upper() if role else "?"
                    css_class = role if role in ("user", "assistant") else ""
                    html_parts.append(
                        f'<div class="message {css_class}">'
                        f'<div class="meta">{emoji} {role_label} — {ts}</div>'
                        f'<div class="content">{content}</div>'
                        f"</div>"
                    )
                elif msg_type == "episodic":
                    html_parts.append(
                        f'<div class="message episodic">'
                        f'<div class="meta">📓 Episodic — {ts} '
                        f'| Importance: {importance}/10</div>'
                        f'{tags_str}'
                        f'<div class="content">{content}</div>'
                        f"</div>"
                    )
                elif msg_type == "vault":
                    html_parts.append(
                        f'<div class="message vault">'
                        f'<div class="meta">📦 Memory Vault Entry</div>'
                        f'<div class="content">{content}</div>'
                        f"</div>"
                    )

            html_parts.append("<hr>")

        html_parts.extend(["</body>", "</html>"])
        html_content = "\n".join(html_parts)

        file_path, _ = QFileDialog.getSaveFileName(
            self.chat_history_tree,
            "Export as HTML",
            f"chat_history_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.html",
            "HTML files (*.html);;All files (*.*)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            QMessageBox.information(
                self.chat_history_tree,
                "Export Complete",
                f"HTML exported to:\n{file_path}",
            )
            logger.info(f"Chat history exported as HTML: {file_path} ({len(html_content)} chars)")
        except (OSError, UnicodeEncodeError, ValueError, TypeError) as e:
            QMessageBox.warning(
                self.chat_history_tree,
                "Export Failed",
                f"Could not save file:\n{e}",
            )
            logger.error(f"Failed to export chat history as HTML: {e}")

    # ── Export ────────────────────────────────────────────────────

    def _collect_tree_data(self):
        """Walk the tree and return structured data for export.

        Returns a list of dicts, one per visible message/episodic entry/vault entry,
        grouped under their section header.
        """
        root = self.chat_history_tree.invisibleRootItem()
        data = []

        for i in range(root.childCount()):
            section = root.child(i)
            section_title = section.text(0)
            children = []

            for j in range(section.childCount()):
                child = section.child(j)
                if child.isHidden():
                    continue

                item_data = child.data(0, Qt.ItemDataRole.UserRole)
                if not item_data:
                    continue

                entry = {
                    "label": child.text(0),
                    "role": child.text(1),
                    "timestamp": child.text(2),
                }
                entry.update(item_data)

                # Don't include placeholder items like "(no messages yet)"
                label = entry.get("label", "")
                if label.startswith("(") and label.endswith(")"):
                    continue

                children.append(entry)

            if children:
                data.append({
                    "section": section_title,
                    "items": children,
                })

        return data

    def _export_as_markdown(self):
        """Export the current chat tree as a Markdown file."""
        data = self._collect_tree_data()
        if not data:
            QMessageBox.information(self.chat_history_tree, "Export", "No data to export.")
            return

        lines = [
            "# Chat History Export",
            "",
            f"*Exported on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}*",
            "",
            "---",
            "",
        ]

        for section in data:
            lines.append(f"## {section['section']}")
            lines.append("")

            for item in section["items"]:
                msg_type = item.get("type", "")
                role = item.get("role", "")
                ts = item.get("timestamp", "")
                content = item.get("content") or item.get("summary") or ""
                importance = item.get("importance", "")
                tags = item.get("tags", [])

                if msg_type == "message":
                    emoji = "🤖" if role == "assistant" else "👤"
                    lines.append(f"### {emoji} {role.upper()} — {ts}")
                    lines.append("")
                    lines.append(content)
                    lines.append("")

                elif msg_type == "episodic":
                    tags_str = ", ".join(tags) if tags else "—"
                    lines.append(f"### 📓 Episodic Summary — {ts}")
                    lines.append(f"- **Importance:** {importance}/10")
                    lines.append(f"- **Tags:** {tags_str}")
                    lines.append("")
                    lines.append(content)
                    lines.append("")

                elif msg_type == "vault":
                    lines.append("### 📦 Memory Vault Entry")
                    lines.append("")
                    lines.append(content)
                    lines.append("")

            lines.append("---")
            lines.append("")

        md_content = "\n".join(lines)

        file_path, _ = QFileDialog.getSaveFileName(
            self.chat_history_tree,
            "Export as Markdown",
            f"chat_history_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md",
            "Markdown files (*.md);;All files (*.*)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            QMessageBox.information(
                self.chat_history_tree,
                "Export Complete",
                f"Markdown exported to:\n{file_path}",
            )
            logger.info(f"Chat history exported as Markdown: {file_path} ({len(md_content)} chars)")
        except (OSError, UnicodeEncodeError, ValueError, TypeError) as e:
            QMessageBox.warning(
                self.chat_history_tree,
                "Export Failed",
                f"Could not save file:\n{e}",
            )
            logger.error(f"Failed to export chat history as Markdown: {e}")

    def _export_as_json(self):
        """Export the current chat tree as a JSON file."""
        data = self._collect_tree_data()
        if not data:
            QMessageBox.information(self.chat_history_tree, "Export", "No data to export.")
            return

        export_obj = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "version": "1.0",
            "sessions": data,
        }

        json_content = json.dumps(export_obj, indent=2, ensure_ascii=False, default=str)

        file_path, _ = QFileDialog.getSaveFileName(
            self.chat_history_tree,
            "Export as JSON",
            f"chat_history_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json",
            "JSON files (*.json);;All files (*.*)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(json_content)
            QMessageBox.information(
                self.chat_history_tree,
                "Export Complete",
                f"JSON exported to:\n{file_path}",
            )
            logger.info(f"Chat history exported as JSON: {file_path} ({len(json_content)} chars)")
        except (OSError, UnicodeEncodeError, ValueError, TypeError) as e:
            QMessageBox.warning(
                self.chat_history_tree,
                "Export Failed",
                f"Could not save file:\n{e}",
            )
            logger.error(f"Failed to export chat history as JSON: {e}")

    # ── Search / Filter ────────────────────────────────────────────

    def _filter_chat_tree(self, text):
        """Filter tree items by search text."""
        text = text.strip().lower()
        root = self.chat_history_tree.invisibleRootItem()

        for i in range(root.childCount()):
            section = root.child(i)
            self._filter_section(section, text)

    def _filter_section(self, section, text):
        """Recursively filter a tree section."""
        if not text:
            # Show all
            for i in range(section.childCount()):
                child = section.child(i)
                child.setHidden(False)
            section.setHidden(False)
            return

        visible_count = 0
        for i in range(section.childCount()):
            child = section.child(i)
            item_text = child.text(0).lower()
            role_text = child.text(1).lower()
            matches = text in item_text or text in role_text
            child.setHidden(not matches)
            if matches:
                visible_count += 1

        section.setHidden(visible_count == 0)

    # ── Interactions ───────────────────────────────────────────────

    def _on_tree_item_clicked(self, item, column):

        """Handle clicking on a tree item — show preview."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        msg_type = data.get("type", "")

        if msg_type == "message":
            role = data.get("role", "?")
            content = data.get("content", "")
            ts = data.get("ts", "")
            self.chat_preview.setPlainText(
                f"[{role.upper()}] @ {ts}\n{'-' * 40}\n{content}"
            )
        elif msg_type == "episodic":
            summary = data.get("summary", "")
            ts = data.get("ts", "")
            importance = data.get("importance", 0)
            tags = data.get("tags", [])
            tags_str = ", ".join(tags) if tags else "(none)"
            self.chat_preview.setPlainText(
                f"[EPISODIC SUMMARY] @ {ts}\n"
                f"Importance: {importance}/10\n"
                f"Tags: {tags_str}\n"
                f"{'-' * 40}\n{summary}"
            )
        elif msg_type == "vault":
            content = data.get("content", "")
            self.chat_preview.setPlainText(
                f"[LONG-TERM MEMORY]\n{'-' * 40}\n{content}"
            )

    def _copy_preview_to_clipboard(self):
        """Copy the preview content to system clipboard."""
        text = self.chat_preview.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)

    def _restore_to_chat_input(self):
        """Restore the original message content to the chat input box."""
        # Get the currently selected tree item
        selected = self.chat_history_tree.currentItem()
        if not selected:
            return
        data = selected.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        # Extract original content from UserRole data
        content = data.get("content") or data.get("summary") or ""
        if content:
            ctx = getattr(self, "context", None)
            if ctx is not None and ctx.restore_chat_input is not None:
                ctx.restore_chat_input(content[:1000])
            elif hasattr(self, 'txt_input'):
                self.txt_input.setPlainText(content[:1000])

    def _on_tree_context_menu(self, pos):
        """Show a rich context menu for the currently right-clicked tree item."""
        item = self.chat_history_tree.itemAt(pos)
        if item is None:
            return

        data = item.data(0, Qt.ItemDataRole.UserRole)
        msg_type = (data or {}).get("type", "")

        menu = QApplication.instance().createPopupMenu() if QApplication.instance() else None
        # Fallback if QApplication.instance() is None (shouldn't happen)
        if menu is None:
            return

        # Common actions
        action_copy_preview = menu.addAction("Copy preview")
        action_restore = menu.addAction("Send to Chat Input")

        # Type-specific actions
        if msg_type in ("message", "episodic", "vault"):
            action_copy_content = menu.addAction("Copy content")
        else:
            action_copy_content = None

        action_export = menu.addAction("Export entry as Markdown")
        action_clear_current = menu.addAction("Clear Current Session")

        chosen = menu.exec(self.chat_history_tree.mapToGlobal(pos))
        if chosen is None:
            return

        if chosen == action_copy_preview:
            # Ensure preview is up-to-date for this item
            self._on_tree_item_clicked(item, 0)
            self._copy_preview_to_clipboard()
            return

        if chosen == action_restore:
            # Ensure the selected item becomes the input source
            self._restore_to_chat_input()
            return

        if action_copy_content is not None and chosen == action_copy_content:
            content = (data or {}).get("content") or (data or {}).get("summary") or ""
            if content:
                QApplication.clipboard().setText(content)
            return

        if chosen == action_export:
            self._export_single_tree_entry_as_markdown(item, data)
            return

        if chosen == action_clear_current:
            self._clear_current_session()
            return

    def _export_single_tree_entry_as_markdown(self, item, data):
        """Export a single selected tree item to a Markdown file."""
        if not data:
            QMessageBox.information(self.chat_history_tree, "Export", "Nothing to export.")
            return

        msg_type = data.get("type", "")
        role = data.get("role", "")
        ts = data.get("ts", "")
        content = data.get("content") or data.get("summary") or ""

        title = "Chat History Entry"
        if msg_type == "message":
            title = f"Message — {role.upper() if role else 'ROLE'}"
        elif msg_type == "episodic":
            title = "Episodic Summary"
        elif msg_type == "vault":
            title = "Memory Vault Entry"

        lines = [
            f"# {title}",
            f"*Exported on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}*",
            "",
        ]

        if ts:
            lines.append(f"- **Time:** {ts}")
            lines.append("")

        if msg_type == "episodic":
            importance = data.get("importance", "")
            tags = data.get("tags", [])
            tags_str = ", ".join(tags) if tags else "—"
            lines.append(f"- **Importance:** {importance}/10")
            lines.append(f"- **Tags:** {tags_str}")
            lines.append("")

        lines.append(content)
        md_content = "\n".join(lines).strip() + "\n"

        file_path, _ = QFileDialog.getSaveFileName(
            self.chat_history_tree,
            "Export Entry as Markdown",
            f"chat_entry_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md",
            "Markdown files (*.md);;All files (*.*)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            QMessageBox.information(self.chat_history_tree, "Export Complete", f"Markdown exported to:\n{file_path}")
            logger.info(f"Exported single chat entry as Markdown: {file_path}")
        except (OSError, ValueError) as e:
            QMessageBox.warning(
                self.chat_history_tree,
                "Export Failed",
                f"Could not save file:\n{e}",
            )
            logger.error(f"Failed to export single chat entry as Markdown: {e}")

    def _clear_current_session(self):
        """Clear the current session's chat history via the controller."""
        ctrl = getattr(self.context, "controller", None) if hasattr(self, "context") else getattr(self, "controller", None)
        if ctrl is not None:
            result = ctrl.wipe_memory()
            logger.info(f"Current session cleared: {result}")
            self._refresh_chat_history()
            self.context.log("🗑️ Current session chat cleared.")
        else:
            self._refresh_chat_history()
