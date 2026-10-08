"""
tabs/plugin_store_tab.py — Plugin Store / Browser.

A comprehensive plugin browser that lists all available plugins with
full metadata, search/filter, one-click enable/disable, error monitoring,
and plugin detail inspection.

Sprint 2 backlog item (Feature suggestion #16).
"""

import json
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QTextEdit, QLineEdit, QComboBox,
)

from logging_config import get_logger

if TYPE_CHECKING:
    from tabs.context import DashboardContext

logger = get_logger(name="PluginStoreTab")


class PluginStoreTabMixin:
    """Mixin that adds a Plugin Store / Browser tab for plugin management."""

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

    def create_plugin_store_tab(self):
        """Build and return the Plugin Store tab widget."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── Header ──
        header = QHBoxLayout()
        title = QLabel("Plugin Store / Browser")
        title.setStyleSheet("font-size: 13pt; font-weight: bold; color: #8B5CF6;")
        header.addWidget(title)
        header.addStretch()

        # Search bar
        self._ps_search_input = QLineEdit()
        self._ps_search_input.setPlaceholderText("Search plugins by name, tag, or command...")
        self._ps_search_input.textChanged.connect(self._ps_refresh_table)
        self._ps_search_input.setMinimumWidth(260)
        header.addWidget(self._ps_search_input)

        # ── Health/status filter dropdown ──
        self._ps_filter_combo = QComboBox()
        self._ps_filter_combo.blockSignals(True)
        self._ps_filter_combo.addItems(["All", "Unhealthy", "Degraded", "Healthy", "Disabled"])
        self._ps_filter_combo.blockSignals(False)
        self._ps_filter_combo.currentTextChanged.connect(self._ps_refresh_table)
        self._ps_filter_combo.setToolTip("Filter plugins by health status")
        header.addWidget(self._ps_filter_combo)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._ps_refresh_table)
        header.addWidget(btn_refresh)

        self._ps_sort_health_btn = QPushButton("Sort by Health")
        self._ps_sort_health_btn.setCheckable(True)
        self._ps_sort_health_btn.clicked.connect(self._ps_refresh_table)
        self._ps_sort_health_btn.setStyleSheet(
            "QPushButton:checked { background-color: #8B5CF6; color: white; }"
        )
        header.addWidget(self._ps_sort_health_btn)

        layout.addLayout(header)

        # ── Stats bar ──
        stats_row = QHBoxLayout()
        self._ps_stats_label = QLabel("Total: — | Enabled: — | Disabled: — | Errors: —")
        self._ps_stats_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        stats_row.addWidget(self._ps_stats_label)

        stats_row.addStretch()

        btn_enable_all = QPushButton("Enable All")
        btn_enable_all.clicked.connect(self._ps_enable_all)
        stats_row.addWidget(btn_enable_all)

        btn_disable_all = QPushButton("Disable All")
        btn_disable_all.clicked.connect(self._ps_disable_all)
        stats_row.addWidget(btn_disable_all)

        layout.addLayout(stats_row)

        # ── Plugin table ──
        self._ps_table = QTableWidget(0, 7)
        self._ps_table.setHorizontalHeaderLabels([
            "Plugin", "Command", "Version", "Tags", "Status", "Errors", "Health"
        ])
        self._ps_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._ps_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._ps_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._ps_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._ps_table.currentCellChanged.connect(self._ps_on_selection_changed)
        self._ps_table.setMinimumHeight(200)
        layout.addWidget(self._ps_table)

        # ── Detail panel ──
        detail_group = QGroupBox("Plugin Details")
        detail_layout = QVBoxLayout(detail_group)

        self._ps_detail_text = QTextEdit()
        self._ps_detail_text.setReadOnly(True)
        self._ps_detail_text.setMaximumHeight(150)
        self._ps_detail_text.setPlaceholderText("Select a plugin to see its details")
        self._ps_detail_text.setStyleSheet("font-size: 9pt;")
        detail_layout.addWidget(self._ps_detail_text)

        detail_actions = QHBoxLayout()

        self._ps_toggle_btn = QPushButton("Toggle Enable")
        self._ps_toggle_btn.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        self._ps_toggle_btn.clicked.connect(self._ps_toggle_selected)
        self._ps_toggle_btn.setEnabled(False)
        detail_actions.addWidget(self._ps_toggle_btn)

        self._ps_clear_errors_btn = QPushButton("Clear Errors")
        self._ps_clear_errors_btn.clicked.connect(self._ps_clear_selected_errors)
        self._ps_clear_errors_btn.setEnabled(False)
        detail_actions.addWidget(self._ps_clear_errors_btn)

        btn_clear_all_errors = QPushButton("Clear All Errors")
        btn_clear_all_errors.clicked.connect(self._ps_clear_all_errors)
        detail_actions.addWidget(btn_clear_all_errors)

        detail_layout.addLayout(detail_actions)
        layout.addWidget(detail_group)

        # ── Initial load ──
        QTimer.singleShot(100, self._ps_refresh_table)

        return tab

    # ── Data Loading ───────────────────────────────────────────────

    def _ps_get_registry(self):
        """Get the plugin registry singleton."""
        try:
            import plugin_registry
            return plugin_registry.registry
        except ImportError:
            return None

    def _ps_gather_plugins(self):
        """Gather all plugins with their metadata, status, and health scores.

        Returns:
            list of dicts with keys: command, name, description, version,
            tags, enabled, error_count, last_error, last_error_time, schema,
            health.
        """
        pr = self._ps_get_registry()
        if not pr:
            return []

        errors = pr.get_execution_errors()
        health_scores = pr.get_plugin_health_scores()
        plugins = []

        for cmd in sorted(pr.plugins.keys()):
            meta = pr.metadata.get(cmd, {})
            err_info = errors.get(cmd, {})
            tags = meta.get("tags", [])
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (json.JSONDecodeError, TypeError):
                    tags = [tags]

            plugins.append({
                "command": cmd,
                "name": meta.get("name", cmd),
                "description": meta.get("description", "No description."),
                "version": meta.get("version", "0.0.0"),
                "tags": tags or [],
                "enabled": pr.is_enabled(cmd),
                "error_count": err_info.get("count", 0),
                "last_error": err_info.get("last_error", ""),
                "last_error_time": err_info.get("last_time", ""),
                "schema": meta.get("schema", {}),
                "health": health_scores.get(cmd, {}),
            })

        return plugins

    def _ps_refresh_table(self):
        """Reload the plugin table from the registry, applying search filter."""
        pr = self._ps_get_registry()
        if not pr:
            self._ps_stats_label.setText("Plugin registry unavailable")
            return

        plugins = self._ps_gather_plugins()
        search = self._ps_search_input.text().strip().lower()

        # Filter
        if search:
            filtered = []
            for p in plugins:
                if (search in p["name"].lower()
                        or search in p["command"].lower()
                        or search in p["description"].lower()
                        or any(search in t.lower() for t in p["tags"])):
                    filtered.append(p)
            plugins = filtered

        # ── Health/status filter ──
        filter_text = self._ps_filter_combo.currentText()
        if filter_text == "Unhealthy":
            plugins = [p for p in plugins
                       if p["enabled"] and p.get("health", {}).get("status") == "unhealthy"]
        elif filter_text == "Degraded":
            plugins = [p for p in plugins
                       if p["enabled"] and p.get("health", {}).get("status") == "degraded"]
        elif filter_text == "Healthy":
            plugins = [p for p in plugins
                       if p["enabled"] and p.get("health", {}).get("status") == "healthy"]
        elif filter_text == "Disabled":
            plugins = [p for p in plugins if not p["enabled"]]
        # "All" → no filter

        # ── Sort by health (worst first) if toggle is active ──
        if self._ps_sort_health_btn.isChecked():
            self._ps_sort_health_btn.setText("Sort: Health ↑")
            plugins.sort(key=lambda p: (
                p.get("health", {}).get("score") is None,  # None → bottom
                p.get("health", {}).get("score", 100),
            ))
        else:
            self._ps_sort_health_btn.setText("Sort by Health")

        self._ps_table.setRowCount(len(plugins))

        enabled_count = 0
        total_errors = 0

        for i, p in enumerate(plugins):
            self._ps_table.setItem(i, 0, QTableWidgetItem(p["name"]))
            self._ps_table.setItem(i, 1, QTableWidgetItem(p["command"]))

            ver_item = QTableWidgetItem(p["version"])
            ver_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._ps_table.setItem(i, 2, ver_item)

            tags_str = ", ".join(p["tags"][:3])
            tags_extra = len(p["tags"]) - 3
            if tags_extra > 0:
                tags_str += f" (+{tags_extra})"
            self._ps_table.setItem(i, 3, QTableWidgetItem(tags_str))

            # Status column
            if p["enabled"]:
                enabled_count += 1
                status_item = QTableWidgetItem("✅ Enabled")
                status_item.setForeground(Qt.GlobalColor.green)
            else:
                status_item = QTableWidgetItem("⏸️ Disabled")
                status_item.setForeground(Qt.GlobalColor.gray)
            self._ps_table.setItem(i, 4, status_item)

            # Error count column
            err_count = p["error_count"]
            total_errors += err_count
            err_item = QTableWidgetItem(str(err_count) if err_count > 0 else "—")
            if err_count > 0:
                err_item.setForeground(Qt.GlobalColor.red)
            else:
                err_item.setForeground(Qt.GlobalColor.green)
            self._ps_table.setItem(i, 5, err_item)

            # Health score column
            health = p.get("health", {})
            h_label = health.get("status_label", "⚪ Unknown")
            if health.get("score") is not None:
                h_label += f" ({health['score']:.0f})"
            health_item = QTableWidgetItem(h_label)
            h_status = health.get("status", "unknown")
            if h_status == "healthy":
                health_item.setForeground(Qt.GlobalColor.green)
            elif h_status == "degraded":
                health_item.setForeground(Qt.GlobalColor.yellow)
            elif h_status == "unhealthy":
                health_item.setForeground(Qt.GlobalColor.red)
            else:
                health_item.setForeground(Qt.GlobalColor.gray)
            self._ps_table.setItem(i, 6, health_item)

            # Store full plugin data for detail view
            self._ps_table.item(i, 0).setData(
                Qt.ItemDataRole.UserRole, p
            )

        # Update stats
        total = len(plugins)
        disabled = total - enabled_count
        self._ps_stats_label.setText(
            f"Total: {total} | Enabled: {enabled_count} | "
            f"Disabled: {disabled} | Errors: {total_errors}"
        )

    # ── Selection ──────────────────────────────────────────────────

    def _ps_on_selection_changed(self, row, col):
        """Show details for the selected plugin."""
        if row < 0:
            self._ps_detail_text.setPlainText("Select a plugin to see its details")
            self._ps_toggle_btn.setEnabled(False)
            self._ps_clear_errors_btn.setEnabled(False)
            return

        item = self._ps_table.item(row, 0)
        if not item:
            return

        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        self._ps_toggle_btn.setEnabled(True)
        toggle_label = "⏸️ Disable" if data["enabled"] else "✅ Enable"
        self._ps_toggle_btn.setText(toggle_label)

        self._ps_clear_errors_btn.setEnabled(data["error_count"] > 0)

        # Build detail text
        lines = [
            f"Plugin: {data['name']}",
            f"Command: {data['command']}",
            f"Version: {data['version']}",
            f"Status: {'Enabled' if data['enabled'] else 'Disabled'}",
            f"Tags: {', '.join(data['tags']) if data['tags'] else '(none)'}",
            "",
            f"Description: {data['description']}",
        ]

        if data["error_count"] > 0:
            lines.append("")
            lines.append(f"⚠️ Error count: {data['error_count']}")
            lines.append(f"Last error: {data['last_error'][:200]}")
            lines.append(f"Last error time: {data['last_error_time']}")

        # ── Health score detail ──
        health = data.get("health", {})
        if health and health.get("total_execs", 0) > 0:
            lines.append("")
            lines.append(f"Health: {health.get('status_label', '?')} (score: {health.get('score', '?'):.0f}/100)")
            lines.append(f"  Success rate: {health.get('success_rate', '?')}%")
            lines.append(f"  Avg latency: {health.get('avg_latency_ms', '?')} ms")
            lines.append(f"  Recent errors (last 100): {health.get('recent_errors', '?')}")
            lines.append(f"  Total executions: {health.get('total_execs', '?')}")

        if data["schema"]:
            lines.append("")
            lines.append("Parameters (schema):")
            lines.append(json.dumps(data["schema"], indent=2))

        self._ps_detail_text.setPlainText("\n".join(lines))

    # ── Actions ────────────────────────────────────────────────────

    def _ps_toggle_selected(self):
        """Toggle the enable/disable state of the selected plugin."""
        row = self._ps_table.currentRow()
        if row < 0:
            return

        item = self._ps_table.item(row, 0)
        if not item:
            return

        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        pr = self._ps_get_registry()
        if not pr:
            return

        cmd = data["command"]
        new_state = pr.toggle(cmd)
        self._ps_refresh_table()

        # Also refresh the Plugins tab if it exists
        if hasattr(self, '_refresh_plugin_list'):
            self._refresh_plugin_list()

        logger.info(f"Plugin toggled: {cmd} -> {'enabled' if new_state else 'disabled'}")
        self.context.log(f"🔄 Plugin toggled: {cmd} -> {'Enabled' if new_state else 'Disabled'}")

    def _ps_enable_all(self):
        """Enable all plugins."""
        pr = self._ps_get_registry()
        if not pr:
            return
        for cmd in list(pr.plugins.keys()):
            pr.enable(cmd)
        self._ps_refresh_table()
        if hasattr(self, '_refresh_plugin_list'):
            self._refresh_plugin_list()
        self.context.log("✅ All plugins enabled")

    def _ps_disable_all(self):
        """Disable all plugins."""
        pr = self._ps_get_registry()
        if not pr:
            return
        for cmd in list(pr.plugins.keys()):
            pr.disable(cmd)
        self._ps_refresh_table()
        if hasattr(self, '_refresh_plugin_list'):
            self._refresh_plugin_list()
        self.context.log("⏸️ All plugins disabled")

    def _ps_clear_selected_errors(self):
        """Clear execution errors for the selected plugin."""
        row = self._ps_table.currentRow()
        if row < 0:
            return
        item = self._ps_table.item(row, 0)
        if not item:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return

        pr = self._ps_get_registry()
        if pr:
            pr.clear_execution_errors(data["command"])
            self._ps_refresh_table()
            self.context.log(f"🧹 Errors cleared for: {data['command']}")

    def _ps_clear_all_errors(self):
        """Clear execution errors for all plugins."""
        pr = self._ps_get_registry()
        if pr:
            pr.clear_execution_errors()
            self._ps_refresh_table()
            self.context.log("🧹 Execution errors cleared for all plugins")
