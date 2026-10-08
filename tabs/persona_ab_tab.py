"""
tabs/persona_ab_tab.py — Persona A/B Testing Tab.

Displays the leaderboard and recent vote history for persona
A/B comparisons. Integrates with memory_vault persona_votes table.
"""

import sqlite3
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QTextEdit, QSplitter,
)
from PyQt6.QtGui import QColor

if TYPE_CHECKING:
    from tabs.context import DashboardContext


class PersonaABTestingTabMixin:
    """Mixin that adds a Persona A/B Testing tab with leaderboard and history."""

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

    def create_persona_ab_testing_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── Header ──────────────────────────────────────────────────
        header = QHBoxLayout()
        title = QLabel("🧪 Persona A/B Testing — Leaderboard")
        title.setStyleSheet("font-size: 12pt; font-weight: bold; color: #E0E0E0;")
        header.addWidget(title)
        header.addStretch()
        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.clicked.connect(self._refresh_ab_leaderboard)
        header.addWidget(btn_refresh)
        layout.addLayout(header)

        # ── Splitter: leaderboard table (top) + history (bottom) ────
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Leaderboard table
        lb_group = QGroupBox("🏆 Leaderboard (sorted by wins)")
        lb_layout = QVBoxLayout(lb_group)
        self.ab_leaderboard_table = QTableWidget()
        self.ab_leaderboard_table.setColumnCount(6)
        self.ab_leaderboard_table.setHorizontalHeaderLabels(
            ["Persona", "Wins", "Losses", "Ties", "Total", "Win Rate"]
        )
        self.ab_leaderboard_table.setAlternatingRowColors(True)
        self.ab_leaderboard_table.horizontalHeader().setStretchLastSection(True)
        self.ab_leaderboard_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.ab_leaderboard_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.ab_leaderboard_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.ab_leaderboard_table.verticalHeader().setVisible(False)
        lb_layout.addWidget(self.ab_leaderboard_table)
        splitter.addWidget(lb_group)

        # History group
        history_group = QGroupBox("📋 Recent A/B Tests")
        history_layout = QVBoxLayout(history_group)

        self.ab_history_text = QTextEdit()
        self.ab_history_text.setReadOnly(True)
        self.ab_history_text.setStyleSheet(
            "font-size: 9pt; color: #D1D5DB; padding: 4px;"
        )
        self.ab_history_text.setPlaceholderText("No A/B tests recorded yet")
        history_layout.addWidget(self.ab_history_text)

        # History actions
        history_actions = QHBoxLayout()
        self.ab_history_count = QLabel("")
        self.ab_history_count.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        history_actions.addWidget(self.ab_history_count)
        history_actions.addStretch()
        splitter.addWidget(history_group)
        splitter.setSizes([300, 200])

        layout.addWidget(splitter, 1)

        # Initial load
        QTimer.singleShot(200, self._refresh_ab_leaderboard)

        return tab

    def _refresh_ab_leaderboard(self):
        """Fetch and display the persona leaderboard + recent votes."""
        import memory_vault

        # ── Leaderboard ─────────────────────────────────────────────
        try:
            leaderboard = memory_vault.get_persona_leaderboard(limit=50)
            self.ab_leaderboard_table.setRowCount(len(leaderboard))

            for row, entry in enumerate(leaderboard):
                display = entry.get("display", entry["persona"][:60])
                self.ab_leaderboard_table.setItem(
                    row, 0, QTableWidgetItem(display)
                )
                self.ab_leaderboard_table.setItem(
                    row, 1, QTableWidgetItem(str(entry["wins"]))
                )
                self.ab_leaderboard_table.setItem(
                    row, 2, QTableWidgetItem(str(entry["losses"]))
                )
                self.ab_leaderboard_table.setItem(
                    row, 3, QTableWidgetItem(str(entry["ties"]))
                )
                self.ab_leaderboard_table.setItem(
                    row, 4, QTableWidgetItem(str(entry["total"]))
                )
                win_rate_pct = f"{entry['win_rate'] * 100:.0f}%"
                self.ab_leaderboard_table.setItem(
                    row, 5, QTableWidgetItem(win_rate_pct)
                )

                # Color the row: green for high win rate, amber for medium
                if entry["win_rate"] >= 0.6 and entry["total"] >= 2:
                    color = QColor(16, 185, 129, 40)  # green tint
                elif entry["win_rate"] >= 0.3 and entry["total"] >= 2:
                    color = QColor(251, 191, 36, 30)  # amber tint
                else:
                    color = QColor(0, 0, 0, 0)  # transparent

                for col in range(6):
                    item = self.ab_leaderboard_table.item(row, col)
                    if item:
                        item.setBackground(color)

        except (sqlite3.Error, OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
            self.ab_leaderboard_table.setRowCount(1)
            self.ab_leaderboard_table.setItem(
                0, 0, QTableWidgetItem(f"Error loading leaderboard: {e}")
            )

        # ── Recent votes ────────────────────────────────────────────
        try:
            votes = memory_vault.get_recent_persona_votes(limit=15)
            if not votes:
                self.ab_history_text.setText("No A/B tests recorded yet.")
                self.ab_history_count.setText("")
                return

            lines = []
            for v in votes:
                ts = v.get("timestamp", "?")
                prompt = v.get("prompt", "")[:80]
                winner = v.get("winner", "")
                a_short = v.get("persona_a", "?").replace("You are ", "").replace(".", "")[:40]
                b_short = v.get("persona_b", "?").replace("You are ", "").replace(".", "")[:40]

                if winner == "A":
                    icon = "🅰️ 🏆"
                elif winner == "B":
                    icon = "🅱️ 🏆"
                elif winner == "tie":
                    icon = "🤝"
                else:
                    icon = "⏳"

                lines.append(
                    f"{icon} [{ts}] {prompt}...\n"
                    f"   A: {a_short}\n"
                    f"   B: {b_short}\n"
                )

            self.ab_history_text.setText("\n".join(lines))
            self.ab_history_count.setText(f"{len(votes)} tests")
        except (sqlite3.Error, OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
            self.ab_history_text.setText(f"Error loading history: {e}")
