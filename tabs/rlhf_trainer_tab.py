"""RLHF Alignment Trainer tab mixin — ratings viz, bias ledger, dataset curation."""
import json
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QTextEdit, QLineEdit, QComboBox, QSplitter,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QFileDialog,
)
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush

from config import DB_PATH
from logging_config import get_logger

if TYPE_CHECKING:
    from tabs.context import DashboardContext

logger = get_logger(name="RLHFTrainerTab")

# ── QPainter bar chart for win-rate display ───────────────────────


class MiniBarChart(QFrame):
    """A simple horizontal bar chart widget drawn with QPainter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bars = []  # list of (label, value, color_hex)
        self.setMinimumHeight(80)
        self.setMaximumHeight(160)

    def set_bars(self, bars):
        """Set bar data.

        Args:
            bars: list of (label, value_float, color_hex_str)
        """
        self._bars = bars
        self.update()

    def paintEvent(self, event):
        if not self._bars:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width() - 20
        h = self.height() - 20
        n = len(self._bars)
        bar_h = min(22, (h - 10) // max(n, 1))
        max_val = max(b[1] for b in self._bars) or 1.0

        for i, (label, val, color_hex) in enumerate(self._bars):
            y = 10 + i * (bar_h + 2)
            bar_w = int((val / max_val) * w * 0.7)

            # Bar
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(color_hex)))
            painter.drawRect(90, y, bar_w, bar_h)

            # Label
            painter.setPen(QPen(QColor("#E0E0E0")))
            painter.drawText(5, y + bar_h - 4, label[:12])

            # Value text
            painter.drawText(90 + bar_w + 4, y + bar_h - 4, f"{val:.0%}" if val <= 1.0 else f"{val:.0f}")

        painter.end()


class RLHFTrainerTabMixin:
    """Mixin that adds an RLHF Alignment Trainer tab with ratings viz and dataset curation."""

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

    def create_alignment_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── Header ──
        top_bar = QHBoxLayout()
        title = QLabel("⚖️ RLHF Cognitive Alignment Trainer")
        title.setStyleSheet("font-weight: bold; color: #10B981; font-size: 12pt;")
        top_bar.addWidget(title)
        top_bar.addStretch()

        self._rlhf_stats_label = QLabel("")
        self._rlhf_stats_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        top_bar.addWidget(self._rlhf_stats_label)

        self.btn_refresh_rlhf = QPushButton("🔄 Refresh")
        self.btn_refresh_rlhf.clicked.connect(self.load_rlhf_history)
        top_bar.addWidget(self.btn_refresh_rlhf)
        layout.addLayout(top_bar)

        # ── Main splitter: left (interaction history), right (detail + bias) ──
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)

        # Interaction list
        left_layout.addWidget(QLabel("📜 Recent AI Interactions:"))
        self.interaction_list = QListWidget()
        self.interaction_list.itemSelectionChanged.connect(self.load_rlhf_interaction)
        left_layout.addWidget(self.interaction_list)
        main_splitter.addWidget(left_panel)

        # Right panel
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # ── Bias type breakdown (mini bar chart) ──
        self._rlhf_bias_chart = MiniBarChart()
        right_layout.addWidget(QLabel("📊 Bias Type Breakdown:"))
        right_layout.addWidget(self._rlhf_bias_chart)

        # ── Audit payload ──
        right_layout.addWidget(QLabel("🔍 Interaction Audit Payload:"))
        self.audit_display_rlhf = QTextEdit()
        self.audit_display_rlhf.setReadOnly(True)
        self.audit_display_rlhf.setMaximumHeight(120)
        right_layout.addWidget(self.audit_display_rlhf)

        # ── Bias ledger table ──
        right_layout.addWidget(QLabel("📋 Recent Bias Entries:"))
        self._rlhf_bias_table = QTableWidget(0, 4)
        self._rlhf_bias_table.setHorizontalHeaderLabels([
            "Time", "Type", "Confidence", "Description"
        ])
        self._rlhf_bias_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self._rlhf_bias_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._rlhf_bias_table.setMaximumHeight(130)
        right_layout.addWidget(self._rlhf_bias_table)

        # ── Correction form ──
        right_layout.addWidget(QLabel("⚠️ Inject Manual Behavioral Correction:"))
        form_layout = QHBoxLayout()
        self.correction_type = QComboBox()
        self.correction_type.addItems([
            "Logic/Format Error", "Tone Adjustment", "Hallucination",
            "Over-Apologetic", "Refusal Issue", "Safety Concern"
        ])
        form_layout.addWidget(self.correction_type)

        self.correction_input = QLineEdit()
        self.correction_input.setPlaceholderText(
            "Describe what the AI did wrong and how to fix it..."
        )
        form_layout.addWidget(self.correction_input, 1)
        right_layout.addLayout(form_layout)

        # ── Action buttons ──
        action_row = QHBoxLayout()

        self.btn_submit_rlhf = QPushButton("⚡ ENFORCE OVERRIDE")
        self.btn_submit_rlhf.setStyleSheet(
            "background-color: #EF4444; color: white; font-weight: bold; padding: 10px;"
        )
        self.btn_submit_rlhf.clicked.connect(self.submit_rlhf_correction)
        action_row.addWidget(self.btn_submit_rlhf)

        btn_export_bias = QPushButton("📤 Export Dataset")
        btn_export_bias.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        btn_export_bias.clicked.connect(self._rlhf_export_dataset)
        action_row.addWidget(btn_export_bias)

        btn_import_bias = QPushButton("📥 Import Dataset")
        btn_import_bias.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        btn_import_bias.clicked.connect(self._rlhf_import_dataset)
        action_row.addWidget(btn_import_bias)

        right_layout.addLayout(action_row)

        main_splitter.addWidget(right_panel)
        main_splitter.setSizes([300, 700])
        layout.addWidget(main_splitter)

        # Initial load
        QTimer.singleShot(100, self.load_rlhf_history)

        return tab

    # ── Data Loading ───────────────────────────────────────────────

    def _rlhf_get_bias_data(self):
        """Fetch bias ledger entries with type counts.

        Returns:
            (rows: list of dicts, type_counts: dict[str, int])
        """
        rows = []
        type_counts = {}
        try:
            conn = sqlite3.connect(DB_PATH, timeout=15.0)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT id, timestamp, bias_type, confidence_score, description "
                    "FROM bias_ledger ORDER BY id DESC LIMIT 100"
                )
                for r in cursor.fetchall():
                    d = dict(r)
                    rows.append(d)
                    bt = d.get("bias_type", "Unknown")
                    type_counts[bt] = type_counts.get(bt, 0) + 1
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            logger.warning(f"Bias ledger fetch failed: {e}")
        return rows, type_counts

    def load_rlhf_history(self):
        """Reload the interaction list, bias table, and bias chart."""
        # ── Interaction list ──
        self.interaction_list.clear()
        try:
            conn = sqlite3.connect(DB_PATH, timeout=15.0)
            try:
                cursor = conn.execute(
                    "SELECT id, content FROM core_memories "
                    "WHERE node_type = 'interaction' ORDER BY id DESC LIMIT 50"
                )
                for mem_id, content in cursor.fetchall():
                    preview = content.replace("\n", " ")[:45] + "..."
                    self.interaction_list.addItem(f"ID {mem_id}: {preview}")
                    self.interaction_list.item(
                        self.interaction_list.count() - 1
                    ).setData(Qt.ItemDataRole.UserRole, content)
            finally:
                conn.close()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            self.context.log(f"⚠️ RLHF Load Error: {e}")

        # ── Bias ledger table ──
        bias_rows, type_counts = self._rlhf_get_bias_data()
        self._rlhf_bias_table.setRowCount(len(bias_rows[:50]))
        for i, r in enumerate(bias_rows[:50]):
            self._rlhf_bias_table.setItem(
                i, 0, QTableWidgetItem(str(r.get("timestamp", ""))[:16])
            )
            self._rlhf_bias_table.setItem(
                i, 1, QTableWidgetItem(str(r.get("bias_type", ""))[:30])
            )
            conf = r.get("confidence_score", 0)
            conf_item = QTableWidgetItem(f"{conf:.0f}%" if isinstance(conf, (int, float)) else str(conf))
            if isinstance(conf, (int, float)) and conf >= 80:
                conf_item.setForeground(Qt.GlobalColor.red)
            elif isinstance(conf, (int, float)) and conf >= 50:
                conf_item.setForeground(Qt.GlobalColor.yellow)
            self._rlhf_bias_table.setItem(i, 2, conf_item)
            self._rlhf_bias_table.setItem(
                i, 3, QTableWidgetItem(str(r.get("description", ""))[:100])
            )

        # ── Bias chart ──
        colors = ["#EF4444", "#F59E0B", "#3B82F6", "#10B981", "#8B5CF6", "#EC4899"]
        bars = [
            (bt[:10], cnt, colors[i % len(colors)])
            for i, (bt, cnt) in enumerate(sorted(type_counts.items(), key=lambda x: -x[1])[:6])
        ]
        self._rlhf_bias_chart.set_bars(bars)

        # ── Stats ──
        total_interactions = self.interaction_list.count()
        total_bias = len(bias_rows)
        self._rlhf_stats_label.setText(
            f"Interactions: {total_interactions} | Biases: {total_bias} | "
            f"Types: {len(type_counts)}"
        )

    def load_rlhf_interaction(self):
        """Load the selected interaction into the audit display."""
        selected = self.interaction_list.currentItem()
        if selected:
            self.audit_display_rlhf.setPlainText(
                selected.data(Qt.ItemDataRole.UserRole)
            )

    # ── Correction Submission ──────────────────────────────────────

    def submit_rlhf_correction(self):
        """Submit a manual behavioral correction to the bias ledger."""
        correction = self.correction_input.text().strip()
        if not correction:
            return

        ctype = self.correction_type.currentText()
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        try:
            conn = sqlite3.connect(DB_PATH, timeout=15.0)
            try:
                conn.execute(
                    "INSERT INTO bias_ledger "
                    "(timestamp, agent_id, bias_type, confidence_score, description) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ts, "rlhf_trainer", f"MANUAL_OVERRIDE: {ctype}", 100, correction)
                )
                conn.commit()
            finally:
                conn.close()
            QMessageBox.information(
                self, "Directive Enforced",
                "Cognitive alignment directive saved."
            )
            self.correction_input.clear()

            # Refresh everything
            self.load_rlhf_history()
            if hasattr(self, 'refresh_agency_data'):
                self.refresh_agency_data()
        except (sqlite3.Error, OSError, ValueError) as e:
            QMessageBox.critical(self, "Write Error", str(e))
            self.context.log(f"⚠️ Directive write failed: {e}")

    # ── Dataset Curation (#20) ─────────────────────────────────────

    def _rlhf_export_dataset(self):
        """Export the bias ledger as a JSON dataset for curation/training."""
        rows, _ = self._rlhf_get_bias_data()
        if not rows:
            QMessageBox.information(self, "Export", "No bias entries to export.")
            return

        # Build a clean training dataset
        dataset = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "version": "1.0",
            "source": "RLHF Bias Ledger",
            "total_entries": len(rows),
            "entries": [
                {
                    "id": r["id"],
                    "timestamp": r.get("timestamp", ""),
                    "type": r.get("bias_type", ""),
                    "confidence": r.get("confidence_score", 0),
                    "description": r.get("description", ""),
                }
                for r in rows
            ]
        }

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export RLHF Dataset",
            f"rlhf_dataset_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json",
            "JSON files (*.json);;All files (*.*)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(dataset, f, indent=2, ensure_ascii=False)
            QMessageBox.information(
                self, "Export Complete",
                f"RLHF dataset exported to:\n{file_path}\n\n"
                f"{len(rows)} bias entries"
            )
            logger.info(f"RLHF dataset exported: {file_path} ({len(rows)} entries)")
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Export Failed", str(e))

    def _rlhf_import_dataset(self):
        """Import bias entries from a JSON dataset file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Import RLHF Dataset",
            "", "JSON files (*.json);;All files (*.*)"
        )
        if not file_path:
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                dataset = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            QMessageBox.warning(self, "Import Failed",
                                f"Could not read file:\n{e}")
            return

        entries = dataset.get("entries", [])
        if not entries:
            QMessageBox.information(self, "Import", "No entries found in dataset.")
            return

        imported = 0
        try:
            conn = sqlite3.connect(DB_PATH, timeout=15.0)
            try:
                with conn:
                    for entry in entries:
                        ts = entry.get("timestamp", datetime.now(timezone.utc).strftime("%H:%M:%S"))
                        bt = entry.get("type", "IMPORTED")
                        conf = entry.get("confidence", 50)
                        desc = entry.get("description", "")
                        if desc:
                            conn.execute(
                                "INSERT INTO bias_ledger "
                                "(timestamp, agent_id, bias_type, confidence_score, description) "
                                "VALUES (?, ?, ?, ?, ?)",
                                (ts, "rlhf_import", bt, conf, desc)
                            )
                            imported += 1
            finally:
                conn.close()
            self.load_rlhf_history()
            QMessageBox.information(
                self, "Import Complete",
                f"Imported {imported} bias entries from dataset."
            )
            logger.info(f"RLHF dataset imported: {imported} entries from {file_path}")
        except (sqlite3.Error, OSError, ValueError) as e:
            QMessageBox.critical(self, "Import Error", str(e))
