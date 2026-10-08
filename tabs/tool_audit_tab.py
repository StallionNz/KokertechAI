"""
tabs/tool_audit_tab.py - Tool-Call Audit Trail Tab.

Displays every executed plugin action with its schema, parameters,
result, and side effects. Uses the per-execution audit log from
PluginRegistry (get_execution_log / clear_execution_log).

Sprint 14 backlog item (#9).
"""

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QTextEdit, QComboBox, QCheckBox,
)

from logging_config import get_logger

if TYPE_CHECKING:
    from tabs.context import DashboardContext

logger = get_logger(name="ToolAuditTab")


class ToolAuditTabMixin:
    """Mixin that adds a Tool-Call Audit Trail tab for execution history."""

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

    def create_tool_audit_tab(self):
        """Build and return the Tool Audit Trail tab widget."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)

        # Header
        header = QHBoxLayout()
        title = QLabel("Tool-Call Audit Trail")
        title.setStyleSheet("font-size: 13pt; font-weight: bold; color: #F59E0B;")
        header.addWidget(title)
        header.addStretch()

        # Command filter dropdown
        self._ta_cmd_filter = QComboBox()
        self._ta_cmd_filter.setMinimumWidth(180)
        self._ta_cmd_filter.setToolTip("Filter by plugin command")
        self._ta_cmd_filter.currentTextChanged.connect(self._ta_refresh_table)
        header.addWidget(QLabel("Filter:"))
        header.addWidget(self._ta_cmd_filter)

        # Auto-refresh checkbox
        self._ta_auto_refresh = QCheckBox("Auto-refresh")
        self._ta_auto_refresh.setChecked(True)
        self._ta_auto_refresh.toggled.connect(self._ta_on_auto_refresh_toggle)
        header.addWidget(self._ta_auto_refresh)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._ta_refresh_table)
        header.addWidget(btn_refresh)

        btn_clear = QPushButton("Clear All")
        btn_clear.setStyleSheet("background-color: #7f1d1d; border-color: #991b1b;")
        btn_clear.clicked.connect(self._ta_clear_log)
        header.addWidget(btn_clear)

        layout.addLayout(header)

        # Stats bar
        self._ta_stats_label = QLabel("Total: -- | Success: -- | Failed: -- | Avg ms: --")
        self._ta_stats_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        layout.addWidget(self._ta_stats_label)

        # Audit table
        self._ta_table = QTableWidget(0, 6)
        self._ta_table.setHorizontalHeaderLabels([
            "Time", "Command", "Params", "Result", "Status", "ms"
        ])
        self._ta_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._ta_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._ta_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self._ta_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._ta_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._ta_table.currentCellChanged.connect(self._ta_on_selection_changed)
        self._ta_table.setMinimumHeight(250)
        layout.addWidget(self._ta_table)

        # Detail panel
        detail_group = QGroupBox("Entry Details")
        detail_layout = QVBoxLayout(detail_group)

        self._ta_detail_text = QTextEdit()
        self._ta_detail_text.setReadOnly(True)
        self._ta_detail_text.setMaximumHeight(130)
        self._ta_detail_text.setPlaceholderText("Select an entry to see full details")
        self._ta_detail_text.setStyleSheet("font-size: 9pt;")
        detail_layout.addWidget(self._ta_detail_text)
        layout.addWidget(detail_group)

        # Footer legend
        footer = QHBoxLayout()
        footer.addWidget(QLabel("OK = Success   FAIL = Failed"))
        footer.addStretch()
        footer.addWidget(QLabel("Max 500 entries retained in memory"))
        layout.addLayout(footer)

        # Auto-refresh timer
        self._ta_refresh_timer = QTimer(self)
        self._ta_refresh_timer.timeout.connect(self._ta_refresh_table)
        self._ta_refresh_timer.start(3000)

        # Initial load
        QTimer.singleShot(100, self._ta_refresh_table)

        return tab

    def _ta_get_registry(self):
        """Get the plugin registry singleton."""
        try:
            import plugin_registry
            return plugin_registry.registry
        except ImportError:
            return None

    def _ta_refresh_table(self):
        """Reload the audit table from the registry, applying command filter."""
        pr = self._ta_get_registry()
        if not pr:
            self._ta_stats_label.setText("Plugin registry unavailable")
            return

        # Update command filter dropdown
        current_filter = self._ta_cmd_filter.currentText()
        self._ta_cmd_filter.blockSignals(True)
        self._ta_cmd_filter.clear()
        self._ta_cmd_filter.addItem("-- All --")
        for cmd in sorted(pr.plugins.keys()):
            self._ta_cmd_filter.addItem(cmd)
        idx = self._ta_cmd_filter.findText(current_filter)
        if idx >= 0:
            self._ta_cmd_filter.setCurrentIndex(idx)
        self._ta_cmd_filter.blockSignals(False)

        cmd_filter = None if current_filter in ("", "-- All --") else current_filter
        entries = pr.get_execution_log(limit=200, cmd_filter=cmd_filter)

        self._ta_table.setRowCount(len(entries))

        success_count = 0
        fail_count = 0
        total_ms = 0

        for i, entry in enumerate(entries):
            # Time
            time_item = QTableWidgetItem(entry.get("ts", ""))
            time_item.setToolTip(entry.get("ts", ""))
            self._ta_table.setItem(i, 0, time_item)

            # Command
            cmd_item = QTableWidgetItem(entry.get("command", ""))
            cmd_item.setToolTip(entry.get("command", ""))
            self._ta_table.setItem(i, 1, cmd_item)

            # Params
            params = entry.get("params", "--")
            params_item = QTableWidgetItem(params[:80])
            params_item.setToolTip(params)
            self._ta_table.setItem(i, 2, params_item)

            # Result
            result = entry.get("result", "--")
            result_item = QTableWidgetItem(result[:80])
            result_item.setToolTip(result)
            self._ta_table.setItem(i, 3, result_item)

            # Status
            success = entry.get("success", False)
            if success:
                success_count += 1
                status_item = QTableWidgetItem("OK")
                status_item.setForeground(Qt.GlobalColor.green)
            else:
                fail_count += 1
                status_item = QTableWidgetItem("FAIL")
                status_item.setForeground(Qt.GlobalColor.red)
            self._ta_table.setItem(i, 4, status_item)

            # Duration
            dur_ms = entry.get("duration_ms", 0)
            total_ms += dur_ms
            dur_item = QTableWidgetItem(str(dur_ms) if dur_ms else "--")
            dur_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            if dur_ms > 5000:
                dur_item.setForeground(Qt.GlobalColor.red)
            elif dur_ms > 1000:
                dur_item.setForeground(Qt.GlobalColor.yellow)
            self._ta_table.setItem(i, 5, dur_item)

            # Store full entry for detail view
            self._ta_table.item(i, 0).setData(
                Qt.ItemDataRole.UserRole, entry
            )

        # Update stats
        total = len(entries)
        avg_ms = round(total_ms / total) if total > 0 else 0
        self._ta_stats_label.setText(
            f"Total: {total} | Success: {success_count} | "
            f"Failed: {fail_count} | Avg ms: {avg_ms}"
        )

    def _ta_on_selection_changed(self, row, col):
        """Show full details for the selected audit entry."""
        if row < 0:
            self._ta_detail_text.setPlainText("Select an entry to see full details")
            return

        item = self._ta_table.item(row, 0)
        if not item:
            return

        entry = item.data(Qt.ItemDataRole.UserRole)
        if not entry:
            return

        pr = self._ta_get_registry()

        success_str = "Yes" if entry.get("success") else "No"
        lines = [
            f"Timestamp:  {entry.get('ts', '--')}",
            f"Command:    {entry.get('command', '--')}",
            f"Duration:   {entry.get('duration_ms', '--')} ms",
            f"Success:    {success_str}",
            "",
            "Parameters:",
            f"  {entry.get('params', '--')}",
            "",
            "Result:",
            f"  {entry.get('result', '--')}",
        ]

        # Look up schema from plugin metadata
        if pr:
            cmd = entry.get("command", "")
            meta = pr.metadata.get(cmd, {})
            schema = meta.get("schema", {})
            perms = meta.get("permissions", [])
            if schema:
                lines.append("")
                lines.append("Schema:")
                for k, v in schema.items():
                    lines.append(f"  {k}: {v}")
            if perms:
                lines.append("")
                lines.append(f"Permissions: {', '.join(perms)}")

        self._ta_detail_text.setPlainText("\n".join(lines))

    def _ta_clear_log(self):
        """Clear the in-memory audit trail."""
        pr = self._ta_get_registry()
        if pr:
            pr.clear_execution_log()
            self._ta_refresh_table()
            self.context.log("Tool audit trail cleared")

    def _ta_on_auto_refresh_toggle(self, enabled):
        """Start or stop the auto-refresh timer."""
        if enabled:
            self._ta_refresh_timer.start(3000)
        else:
            self._ta_refresh_timer.stop()

    def teardown_tool_audit(self) -> None:
        """Stop auto-refresh timer during application shutdown."""
        timer = getattr(self, "_ta_refresh_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except (RuntimeError, AttributeError):
                pass

    def teardown(self) -> None:
        """Alias for teardown_tool_audit."""
        self.teardown_tool_audit()
