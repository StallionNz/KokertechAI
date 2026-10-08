"""
app_plugins.py - AppPluginMixin: plugin manager UI for the dashboard.
Extracted from app_core.py.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import QListWidgetItem

import plugin_registry


class AppPluginMixin:
    """Mixin providing plugin management UI for the dashboard sidebar."""

    def _refresh_plugin_list(self):
        """Rebuild the plugin list from the registry."""
        pr = plugin_registry.registry
        self.plugin_list.blockSignals(True)
        self.plugin_list.clear()
        for cmd in sorted(pr.plugins.keys()):
            meta = pr.metadata.get(cmd, {})
            name = meta.get("name", cmd)
            desc = meta.get("description", "")
            ver = meta.get("version", "0.0.0")
            tags = ", ".join(meta.get("tags", []))
            item = QListWidgetItem()
            item.setText(f"{name}  ({cmd})")
            tooltip = desc
            if tags:
                tooltip += f"\nTags: {tags}"
            tooltip += f"\nVersion: {ver}"
            item.setToolTip(tooltip)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if pr.is_enabled(cmd) else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, cmd)
            self.plugin_list.addItem(item)
        self.plugin_list.blockSignals(False)
        self._update_plugin_status()
        self._update_plugin_list_errors()

    def _update_plugin_status(self):
        """Update the enabled/total count label."""
        pr = plugin_registry.registry
        self.plugin_status_label.setText(
            f"{pr.get_enabled_count()}/{pr.get_plugin_count()} enabled"
        )
        ratio = pr.get_enabled_count() / max(pr.get_plugin_count(), 1)
        if ratio >= 0.8:
            self.plugin_status_label.setStyleSheet(
                "font-size: 9pt; font-weight: bold; padding: 2px 0; color: #10B981;"
            )
        elif ratio >= 0.4:
            self.plugin_status_label.setStyleSheet(
                "font-size: 9pt; font-weight: bold; padding: 2px 0; color: #FBBF24;"
            )
        else:
            self.plugin_status_label.setStyleSheet(
                "font-size: 9pt; font-weight: bold; padding: 2px 0; color: #EF4444;"
            )

    def _on_plugin_toggle(self, item):
        """Called when a plugin checkbox is toggled."""
        pr = plugin_registry.registry
        cmd = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            pr.enable(cmd)
        else:
            pr.disable(cmd)
        self._update_plugin_status()
        if self.plugin_list.currentItem() is item:
            self._on_plugin_selected(item, None)

    def _on_plugin_selected(self, current, previous):
        """Show plugin metadata when selected."""
        if not current:
            self.plugin_detail.setText("Select a plugin to view details")
            return
        pr = plugin_registry.registry
        cmd = current.data(Qt.ItemDataRole.UserRole)
        meta = pr.metadata.get(cmd, {})
        lines = [
            f"Name: {meta.get('name', cmd)}",
            f"Command: {cmd}",
            f"Version: {meta.get('version', '0.0.0')}",
            f"Author: {meta.get('author', 'Unknown')}",
            f"Description: {meta.get('description', 'N/A')}",
        ]
        tags = meta.get("tags", [])
        if tags:
            lines.append(f"Tags: {', '.join(tags)}")
        if cmd in pr.disabled:
            lines.append("Status: ❌ Disabled")
        else:
            lines.append("Status: ✅ Enabled")
        # Execution error reporting
        exec_err = pr.get_execution_errors(cmd)
        if exec_err and exec_err["count"] > 0:
            lines.append(f"Errors: {exec_err['count']} — last: {exec_err['last_error'][:60]}")
            self.plugin_detail.setStyleSheet(
                "font-size: 8pt; padding: 4px; color: #EF4444;"
            )
        else:
            self.plugin_detail.setStyleSheet("font-size: 8pt; padding: 4px;")
        self.plugin_detail.setText("\n".join(lines))

    def _disable_all_plugins(self):
        """Disable every loaded plugin."""
        pr = plugin_registry.registry
        for cmd in list(pr.plugins.keys()):
            pr.disable(cmd)
        self._refresh_plugin_list()

    def _enable_all_plugins(self):
        """Enable every loaded plugin."""
        pr = plugin_registry.registry
        for cmd in list(pr.plugins.keys()):
            pr.enable(cmd)
        self._refresh_plugin_list()

    def _clear_plugin_errors(self):
        """Clear all execution error counts and refresh the detail panel."""
        pr = plugin_registry.registry
        pr.clear_execution_errors()
        self.log_to_audit("Cleared all plugin execution error counters.")
        # Refresh error visuals in the list and tab badge
        self._update_plugin_list_errors()
        # Refresh the detail panel if a plugin is currently selected
        current = self.plugin_list.currentItem()
        if current is not None:
            self._on_plugin_selected(current, None)

    def _update_plugin_list_errors(self):
        """Color plugin list items red if they have execution errors.
        Clean items reset to default (theme inherits).
        Also updates the tab badge and error summary label.
        """
        pr = plugin_registry.registry
        for i in range(self.plugin_list.count()):
            item = self.plugin_list.item(i)
            cmd = item.data(Qt.ItemDataRole.UserRole)
            exec_err = pr.get_execution_errors(cmd)
            if exec_err and exec_err["count"] > 0:
                item.setForeground(QColor("#EF4444"))
            else:
                item.setForeground(QBrush())  # reset to default (theme inherits)
        self._update_plugin_tab_and_error_summary()

    def _update_plugin_tab_and_error_summary(self):
        """Update the Plugins tab label and error summary with execution error count."""
        pr = plugin_registry.registry
        errors = pr.get_execution_errors()
        plugins_with_errors = sum(1 for err in errors.values() if err["count"] > 0)
        total_errors = sum(err["count"] for err in errors.values())

        # Update plug-ins tab label with error badge
        tab_text = "Plugins" + ("⚠️" if plugins_with_errors > 0 else "")
        if hasattr(self, '_plugins_tab_index') and hasattr(self, 'tabs'):
            try:
                self.tabs.setTabText(self._plugins_tab_index, tab_text)
            except (RuntimeError, AttributeError):
                pass

        # Update error summary label at top of plugin tab
        if hasattr(self, 'plugin_error_summary'):
            if plugins_with_errors > 0:
                self.plugin_error_summary.setText(
                    f"⚠️ {plugins_with_errors} plugin(s) with execution errors ({total_errors} total)"
                )
                self.plugin_error_summary.setStyleSheet(
                    "font-size: 9pt; font-weight: bold; padding: 2px 0; color: #EF4444;"
                )
                self.plugin_error_summary.show()
            else:
                self.plugin_error_summary.setText("")
                self.plugin_error_summary.hide()

    def run_vault_cleaner(self):
        import vault_cleaner
        from datetime import datetime
        try:
            ts = datetime.now().strftime('%H:%M:%S')
            self.log_to_audit(f"[{ts}] Running Vault Cleaner...")
            vault_cleaner.run()
            self.log_to_audit(f"[{ts}] Vault Cleaner completed.")
            self.render_knowledge_graph()
        except Exception as e:
            self.log_to_audit(f"Vault Cleaner error: {str(e)}")
