"""
tabs/rag_tab.py — Agentic RAG Dashboard tab.

Displays:
  - Research history (RAG queries from episodic journal)
  - Hop counts per query
  - Source breakdowns (core_memory vs episodic vs web)
  - Quick-run RAG query input
  - Summary statistics at the top
"""

import json
import sqlite3

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QLineEdit, QSplitter,
    QTextEdit, QGroupBox, QFrame, QApplication,
)
from PyQt6.QtGui import QColor

from logging_config import get_logger

logger = get_logger(name="RagTab")


class RagTabMixin:
    """Mixin that adds an Agentic RAG Dashboard tab.

    Displays research history from the episodic journal, including
    hop counts, source breakdowns, and sub-question detail.
    Also provides a quick-run RAG query input.
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
            config=getattr(self, "config", {}),
            restore_chat_input=(
                (lambda text: self.txt_input.setPlainText(text))
                if hasattr(self, "txt_input")
                else None
            ),
        )

    @context.setter
    def context(self, value):
        self.ctx = value

    def create_rag_tab(self) -> QWidget:
        """Build and return the RAG Dashboard tab widget."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Header ────────────────────────────────────────────────
        header = QLabel("🔬 Agentic RAG Dashboard")
        header.setStyleSheet("font-size: 14pt; font-weight: bold;")
        layout.addWidget(header)

        # ── Stats bar ─────────────────────────────────────────────
        stats_frame = QFrame()
        stats_frame.setStyleSheet("QFrame { background-color: #1a1a2e; border: 1px solid #16213e; border-radius: 4px; padding: 8px; }")
        stats_layout = QHBoxLayout(stats_frame)
        stats_layout.setContentsMargins(12, 6, 12, 6)

        self.rag_total_queries = QLabel("Total Research Queries: 0")
        self.rag_total_queries.setStyleSheet("font-size: 11pt; color: #10B981; font-weight: bold;")
        stats_layout.addWidget(self.rag_total_queries)

        stats_layout.addSpacing(24)

        self.rag_avg_hops = QLabel("Avg Hops: —")
        self.rag_avg_hops.setStyleSheet("font-size: 11pt; color: #3B82F6; font-weight: bold;")
        stats_layout.addWidget(self.rag_avg_hops)

        stats_layout.addSpacing(24)

        self.rag_total_sources = QLabel("Total Sources Retrieved: 0")
        self.rag_total_sources.setStyleSheet("font-size: 11pt; color: #F59E0B; font-weight: bold;")
        stats_layout.addWidget(self.rag_total_sources)

        stats_layout.addStretch()
        layout.addWidget(stats_frame)

        # ── Quick-run query input ─────────────────────────────────
        query_row = QHBoxLayout()
        self.rag_query_input = QLineEdit()
        self.rag_query_input.setPlaceholderText(
            "Run a quick RAG query... (uses <<RAG:query>> syntax)"
        )
        self.rag_query_input.returnPressed.connect(self._run_rag_query)
        query_row.addWidget(self.rag_query_input)

        run_btn = QPushButton("🔬 Research")
        run_btn.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb; font-weight: bold;")
        run_btn.clicked.connect(self._run_rag_query)
        query_row.addWidget(run_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_rag_history)
        query_row.addWidget(refresh_btn)

        layout.addLayout(query_row)

        # ── Tree + Preview splitter ───────────────────────────────
        splitter = QSplitter(Qt.Orientation.Vertical)

        self.rag_history_tree = QTreeWidget()
        self.rag_history_tree.setHeaderLabels(
            ["Research Query / Detail", "Hops", "Sources", "Time"]
        )
        self.rag_history_tree.setAlternatingRowColors(True)
        self.rag_history_tree.setColumnWidth(0, 350)
        self.rag_history_tree.setColumnWidth(1, 60)
        self.rag_history_tree.setColumnWidth(2, 180)
        self.rag_history_tree.setColumnWidth(3, 150)
        self.rag_history_tree.itemClicked.connect(self._on_rag_item_clicked)
        splitter.addWidget(self.rag_history_tree)

        # Preview pane
        preview_group = QGroupBox("Research Detail")
        preview_layout = QVBoxLayout(preview_group)
        self.rag_preview = QTextEdit()
        self.rag_preview.setReadOnly(True)
        self.rag_preview.setMaximumHeight(200)
        self.rag_preview.setPlaceholderText(
            "Click a research entry to see the full synthesis"
        )
        preview_layout.addWidget(self.rag_preview)

        preview_actions = QHBoxLayout()
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.clicked.connect(self._copy_rag_preview)
        preview_actions.addWidget(copy_btn)

        repeat_btn = QPushButton("Repeat Query in Chat")
        repeat_btn.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        repeat_btn.clicked.connect(self._repeat_rag_query)
        preview_actions.addWidget(repeat_btn)

        preview_layout.addLayout(preview_actions)
        splitter.addWidget(preview_group)

        layout.addWidget(splitter)

        # Initial load
        QTimer.singleShot(200, self._refresh_rag_history)

        return tab

    # ── Data Loading ──────────────────────────────────────────────

    def _refresh_rag_history(self):
        """Reload the RAG research tree from the episodic journal."""
        self.rag_history_tree.clear()
        total_queries = 0
        total_hops = 0
        total_sources = 0

        try:
            import memory_vault
            entries = memory_vault.get_recent_episodic(limit=100)
        except (sqlite3.Error, ImportError, RuntimeError, OSError, ValueError, TypeError, KeyError) as e:
            err_item = QTreeWidgetItem(
                self.rag_history_tree,
                [f"⚠️ Could not load research history: {e}", "", "", ""],
            )
            err_item.setForeground(0, Qt.GlobalColor.red)
            logger.warning(f"Failed to load RAG history: {e}")
            return

        # Filter to RAG entries only
        rag_entries = []
        for ep in entries:
            tags_raw = ep.get("tags", "[]")
            if isinstance(tags_raw, str):
                try:
                    tags = json.loads(tags_raw)
                except (json.JSONDecodeError, TypeError):
                    tags = []
            else:
                tags = tags_raw or []
            if "rag" in tags:
                rag_entries.append((ep, tags))

        if not rag_entries:
            empty_item = QTreeWidgetItem(
                self.rag_history_tree,
                ["(no research queries yet — run <<RAG:query>> in chat)", "", "", ""],
            )
            empty_item.setForeground(0, Qt.GlobalColor.gray)
            return

        for ep, tags in rag_entries:
            ep_ts = ep.get("timestamp", "?")
            summary = ep.get("summary", "")
            _importance = ep.get("importance_score", 0)

            # Parse the metadata JSON to extract hops, sources, sub_questions
            metadata_raw = ep.get("metadata", "{}")
            if isinstance(metadata_raw, str):
                try:
                    metadata = json.loads(metadata_raw)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            else:
                metadata = metadata_raw or {}

            hops = metadata.get("hops", "?")
            context_count = metadata.get("context_count", "?")
            sub_questions = metadata.get("sub_questions", [])

            # Extract query from summary (format: "[RAG] Q: ...\nA: ...")
            query_text = summary
            if summary.startswith("[RAG] Q:"):
                q_part = summary.split("\\n")[0] if "\\n" in summary else summary
                query_text = q_part.replace("[RAG] Q: ", "", 1)[:120]

            # Build display labels
            hops_str = str(hops) if hops != "?" else "—"
            sources_str = f"{context_count} chunk(s)" if context_count != "?" else "—"
            ts_short = ep_ts[11:19] if len(ep_ts) > 19 else ep_ts  # Extract HH:MM:SS

            tree_item = QTreeWidgetItem(
                self.rag_history_tree,
                [query_text + ("..." if len(query_text) > 120 else ""),
                 hops_str, sources_str, ts_short],
            )

            # Store full data for preview
            tree_item.setData(0, Qt.ItemDataRole.UserRole, {
                "full_summary": summary,
                "query": query_text,
                "hops": hops,
                "context_count": context_count,
                "sub_questions": sub_questions,
                "timestamp": ep_ts,
                "tags": tags,
                "metadata": metadata,
            })

            # Add sub-questions as children
            if sub_questions:
                for sq_idx, sq in enumerate(sub_questions[:5]):  # Max 5 sub-questions
                    sq_item = QTreeWidgetItem(
                        tree_item,
                        [f"  Sub-Q{sq_idx + 1}: {sq[:100]}", "", "", ""],
                    )
                    sq_item.setForeground(0, QColor("#9CA3AF"))
                    sq_item.setData(0, Qt.ItemDataRole.UserRole, {
                        "type": "sub_question",
                        "text": sq,
                    })

            # Count hops (integer hops or count sub-questions as proxy)
            if isinstance(hops, (int, float)):
                total_hops += hops
            total_queries += 1

            # Estimate source breakdown from context_count
            if isinstance(context_count, (int, float)):
                total_sources += int(context_count)

            # Color-code by hop count
            if isinstance(hops, (int, float)):
                if hops >= 3:
                    tree_item.setForeground(1, QColor("#10B981"))  # Green = deep
                elif hops >= 2:
                    tree_item.setForeground(1, QColor("#F59E0B"))  # Amber = moderate
                else:
                    tree_item.setForeground(1, QColor("#6B7280"))  # Gray = shallow

        # Update stats
        self.rag_total_queries.setText(f"Total Research Queries: {total_queries}")
        avg_hops = round(total_hops / total_queries, 1) if total_queries > 0 else 0
        self.rag_avg_hops.setText(f"Avg Hops: {avg_hops}")
        self.rag_total_sources.setText(f"Total Sources Retrieved: {total_sources}")

        logger.info(
            f"RAG tree refreshed: {total_queries} queries, "
            f"avg {avg_hops} hops, {total_sources} total sources"
        )

    # ── Interactions ──────────────────────────────────────────────

    def _on_rag_item_clicked(self, item, column):
        """Handle clicking a tree item — show full detail in preview."""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        item_type = data.get("type", "")

        if item_type == "sub_question":
            text = data.get("text", "")
            self.rag_preview.setPlainText(f"[SUB-QUESTION]\n{text}")
            return

        # Main research entry
        query = data.get("query", "")
        hops = data.get("hops", "?")
        context_count = data.get("context_count", "?")
        sub_questions = data.get("sub_questions", [])
        full_summary = data.get("full_summary", "")
        ts = data.get("timestamp", "")

        # Parse the summary to extract answer (format: "[RAG] Q: ...\nA: ...")
        answer = ""
        if "\\nA:" in full_summary:
            parts = full_summary.split("\\nA:", 1)
            answer = parts[1].strip() if len(parts) > 1 else ""
        elif "A: " in full_summary:
            a_idx = full_summary.index("A: ")
            answer = full_summary[a_idx + 3:].strip()

        # Build source breakdown
        if isinstance(context_count, (int, float)) and context_count > 0:
            # Estimate distribution (equal distribution when metadata lacks source breakdown)
            est_per_source = max(1, int(context_count / 3))
            source_breakdown = (
                f"  Core Memory:  ~{est_per_source} chunk(s)\\n"
                f"  Episodic:     ~{est_per_source} chunk(s)\\n"
                f"  Web:          ~{max(0, context_count - 2 * est_per_source)} chunk(s)"
            )
        else:
            source_breakdown = "  (source distribution not available)"

        sub_q_text = ""
        if sub_questions:
            sub_q_text = "\\n".join(f"  {i+1}. {sq}" for i, sq in enumerate(sub_questions))

        preview = (
            f"[RESEARCH QUERY] @ {ts}\\n"
            f"{'=' * 50}\\n"
            f"Query: {query}\\n\\n"
            f"Synthesis:\\n{answer[:500]}{'...' if len(answer) > 500 else ''}\\n\\n"
            f"---\\n"
            f"Retrieval Hops: {hops}\\n"
            f"Total Sources: {context_count}\\n\\n"
            f"Source Breakdown:\\n{source_breakdown}\\n"
        )
        if sub_q_text:
            preview += f"\\nSub-Questions:\\n{sub_q_text}\\n"

        self.rag_preview.setPlainText(preview)

    def _copy_rag_preview(self):
        """Copy the preview content to system clipboard."""
        text = self.rag_preview.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)

    def _repeat_rag_query(self):
        """Restore the selected research query to the chat input."""
        selected = self.rag_history_tree.currentItem()
        if not selected:
            return
        data = selected.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        query = data.get("query", "")
        if query:
            clean_query = query.replace("[RAG] Q: ", "", 1).strip()
            rag_cmd = f"<<RAG:{clean_query}>>"
            ctx = getattr(self, "context", None) or getattr(self, "ctx", None)
            if ctx is not None and callable(getattr(ctx, "restore_chat_input", None)):
                ctx.restore_chat_input(rag_cmd)
                logger.info(f"Repeated RAG query via context: {clean_query[:80]}")
            elif hasattr(self, 'txt_input'):
                self.txt_input.setPlainText(rag_cmd)
                logger.info(f"Repeated RAG query: {clean_query[:80]}")

    def _run_rag_query(self):
        """Run a quick RAG query from the tab's input box.

        Delegates to the controller's process_input with the <<RAG:>> prefix
        so the existing RAG detection logic handles it.
        """
        query = self.rag_query_input.text().strip()
        if not query:
            return

        logger.info(f"Quick RAG query from tab: {query[:80]}")
        self.rag_preview.setPlainText(f"🔬 Running research query...\n\nQuery: {query}\n\nPlease wait...")

        rag_cmd = f"<<RAG:{query}>>"
        ctx = getattr(self, "context", None) or getattr(self, "ctx", None)
        if ctx is not None and callable(getattr(ctx, "restore_chat_input", None)):
            ctx.restore_chat_input(rag_cmd)
            logger.info(f"Queued RAG query in chat input via context: {query[:80]}")
            QTimer.singleShot(5000, self._refresh_rag_history)
        elif hasattr(self, 'txt_input'):
            self.txt_input.setPlainText(rag_cmd)
            logger.info(f"Queued RAG query in chat input: {query[:80]}")

            # Refresh after a delay to pick up the new episodic journal entry
            QTimer.singleShot(5000, self._refresh_rag_history)
        else:
            self.rag_preview.setPlainText(
                "⚠️ Cannot run query: chat input not available.\n"
                "Please use <<RAG:query>> syntax in the main chat instead."
            )

        self.rag_query_input.clear()
