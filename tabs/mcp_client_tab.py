"""
tabs/mcp_client_tab.py - MCP Client Management Tab.

Manage connections to external MCP (Model Context Protocol) servers.
Discover tools, test connectivity, and call external tools.
"""
import json
import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QTextEdit, QLineEdit, QSpinBox, QMessageBox,
)

from logging_config import get_logger

logger = get_logger(name="MCPTab")


class MCPClientTabMixin:
    """Mixin that adds an MCP Client management tab."""

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
            switch_tab=(
                (lambda index: self.tabs.setCurrentIndex(index))
                if hasattr(self, "tabs")
                else None
            ),
        )

    @context.setter
    def context(self, value):
        self.ctx = value

    def create_mcp_client_tab(self):
        """Build and return the MCP Client tab widget."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)

        # -- Header --
        header = QHBoxLayout()
        title = QLabel("MCP Client")
        title.setStyleSheet("font-size: 13pt; font-weight: bold; color: #8B5CF6;")
        header.addWidget(title)
        header.addStretch()
        self._mcp_status_label = QLabel("No servers configured")
        self._mcp_status_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        header.addWidget(self._mcp_status_label)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._mcp_refresh)
        header.addWidget(btn_refresh)
        btn_discover = QPushButton("Discover All")
        btn_discover.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        btn_discover.clicked.connect(self._mcp_discover_all)
        header.addWidget(btn_discover)
        layout.addLayout(header)

        # -- Server table --
        self._mcp_table = QTableWidget(0, 5)
        self._mcp_table.setHorizontalHeaderLabels(["Name", "URL", "Tools", "Status", "Enabled"])
        self._mcp_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._mcp_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._mcp_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._mcp_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._mcp_table.currentCellChanged.connect(self._mcp_on_selection_changed)
        self._mcp_table.setMaximumHeight(200)
        layout.addWidget(self._mcp_table)

        # -- Add server form --
        form_group = QGroupBox("Add / Edit MCP Server")
        form_layout = QHBoxLayout(form_group)
        self._mcp_name_input = QLineEdit()
        self._mcp_name_input.setPlaceholderText("Server name...")
        self._mcp_name_input.setMaximumWidth(160)
        form_layout.addWidget(self._mcp_name_input)
        self._mcp_url_input = QLineEdit()
        self._mcp_url_input.setPlaceholderText("URL (e.g. http://127.0.0.1:9100)")
        form_layout.addWidget(self._mcp_url_input)
        self._mcp_timeout_spin = QSpinBox()
        self._mcp_timeout_spin.setRange(1, 120)
        self._mcp_timeout_spin.setValue(15)
        self._mcp_timeout_spin.setSuffix("s")
        self._mcp_timeout_spin.setMaximumWidth(60)
        form_layout.addWidget(self._mcp_timeout_spin)
        btn_connect = QPushButton("Connect")
        btn_connect.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        btn_connect.clicked.connect(self._mcp_add_server)
        form_layout.addWidget(btn_connect)
        btn_delete = QPushButton("Remove")
        btn_delete.setStyleSheet("background-color: #7f1d1d; border-color: #991b1b;")
        btn_delete.clicked.connect(self._mcp_remove_selected)
        form_layout.addWidget(btn_delete)
        btn_test = QPushButton("Test")
        btn_test.clicked.connect(self._mcp_test_selected)
        form_layout.addWidget(btn_test)
        layout.addWidget(form_group)

        # -- Detail panel --
        detail_group = QGroupBox("Server Details & Discovered Tools")
        detail_layout = QVBoxLayout(detail_group)
        self._mcp_detail_text = QTextEdit()
        self._mcp_detail_text.setReadOnly(True)
        self._mcp_detail_text.setMaximumHeight(180)
        self._mcp_detail_text.setPlaceholderText("Select a server to see its details.")
        self._mcp_detail_text.setStyleSheet("font-size: 9pt;")
        detail_layout.addWidget(self._mcp_detail_text)

        # -- Call tool form --
        call_layout = QHBoxLayout()
        call_layout.addWidget(QLabel("Call Tool:"))
        self._mcp_call_tool_input = QLineEdit()
        self._mcp_call_tool_input.setPlaceholderText("Tool name...")
        self._mcp_call_tool_input.setMaximumWidth(200)
        call_layout.addWidget(self._mcp_call_tool_input)
        self._mcp_call_args_input = QLineEdit()
        self._mcp_call_args_input.setPlaceholderText('JSON args (e.g. {"query": "test"})')
        call_layout.addWidget(self._mcp_call_args_input)
        btn_call = QPushButton("Call")
        btn_call.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        btn_call.clicked.connect(self._mcp_call_tool)
        call_layout.addWidget(btn_call)
        detail_layout.addLayout(call_layout)
        layout.addWidget(detail_group)

        # -- Stats bar --
        self._mcp_stats_label = QLabel("Servers: 0 | Synced tools: 0")
        self._mcp_stats_label.setStyleSheet("font-size: 9pt; color: #6B7280;")
        layout.addWidget(self._mcp_stats_label)
        layout.addStretch()

        QTimer.singleShot(100, self._mcp_refresh)
        return tab

    def _get_mcp_client(self):
        """Get the MCP client manager singleton."""
        try:
            from mcp_client import get_mcp_client
            return get_mcp_client()
        except ImportError:
            return None

    def _mcp_refresh(self):
        """Reload the server table from the MCP client.
        Guarded against deleted C++ widgets (e.g. during test teardown
        or shutdown when a QTimer.singleShot fires late)."""
        try:
            self._mcp_status_label.text()
        except RuntimeError:
            return  # C++ widget already deleted
        client = self._get_mcp_client()
        if not client:
            self._mcp_status_label.setText("MCP client not available")
            return
        servers = client.servers
        self._mcp_table.setRowCount(len(servers))
        for i, (name, conn) in enumerate(sorted(servers.items())):
            self._mcp_table.setItem(i, 0, QTableWidgetItem(conn.name))
            self._mcp_table.setItem(i, 1, QTableWidgetItem(conn.url))
            self._mcp_table.setItem(i, 2, QTableWidgetItem(str(len(conn.tools))))
            if not conn.enabled:
                si = QTableWidgetItem("Disabled")
                si.setForeground(Qt.GlobalColor.gray)
            elif conn.error:
                si = QTableWidgetItem("Error")
                si.setForeground(Qt.GlobalColor.red)
            else:
                si = QTableWidgetItem("Connected")
                si.setForeground(Qt.GlobalColor.green)
            self._mcp_table.setItem(i, 3, si)
            self._mcp_table.setItem(i, 4, QTableWidgetItem("Yes" if conn.enabled else "No"))
            self._mcp_table.item(i, 0).setData(Qt.ItemDataRole.UserRole, name)
        total_tools = sum(len(c.tools) for c in servers.values())
        synced = len(client._synced_commands)
        self._mcp_stats_label.setText(
            "Servers: {} | Total tools: {} | Synced: {}".format(
                len(servers), total_tools, synced))
        self._mcp_status_label.setText(
            "{} servers, {} tools".format(len(servers), synced))

    def _mcp_on_selection_changed(self, row, col):
        """Show details for the selected server."""
        if row < 0:
            self._mcp_detail_text.setPlainText("Select a server to see details.")
            return
        item = self._mcp_table.item(row, 0)
        if not item:
            return
        server_name = item.data(Qt.ItemDataRole.UserRole)
        client = self._get_mcp_client()
        if not client or server_name not in client.servers:
            return
        conn = client.servers[server_name]
        lines = [
            "Name: {}".format(conn.name),
            "URL: {}".format(conn.url),
            "Enabled: {}".format(conn.enabled),
            "Timeout: {}s".format(conn.timeout),
            "Tools: {}".format(len(conn.tools)),
        ]
        if conn.server_info:
            lines.append("Server info: {}".format(
                json.dumps(conn.server_info, indent=2)))
        if conn.error:
            lines.append("Last error: {}".format(conn.error))
        if conn.tools:
            lines.append("Discovered tools ({}):".format(len(conn.tools)))
            for tool in conn.tools:
                tname = tool.get("name", "?")
                tdesc = tool.get("description", "")[:80]
                lines.append("  {}: {}".format(tname, tdesc))
                schema = tool.get("inputSchema", {})
                props = schema.get("properties", {})
                for pname, pinfo in props.items():
                    ptype = pinfo.get("type", "?")
                    pdesc = pinfo.get("description", "")[:40]
                    lines.append("    {} ({}): {}".format(pname, ptype, pdesc))
        else:
            lines.append("No tools discovered. Click Discover All.")
        self._mcp_detail_text.setPlainText("\n".join(lines))

    def _mcp_add_server(self):
        """Add or update an MCP server connection."""
        name = self._mcp_name_input.text().strip()
        url = self._mcp_url_input.text().strip()
        timeout = self._mcp_timeout_spin.value()
        if not name:
            QMessageBox.warning(self._mcp_table, "Missing Name",
                                "Enter a server name.")
            return
        if not url:
            QMessageBox.warning(self._mcp_table, "Missing URL",
                                "Enter a server URL.")
            return
        client = self._get_mcp_client()
        if not client:
            QMessageBox.critical(self._mcp_table, "Error",
                                 "MCP client not available.")
            return
        from config import CONFIG, save_settings
        from mcp_client import MCPServerConnection
        conn = MCPServerConnection(name=name, url=url, enabled=True,
                                   timeout=timeout)
        client.servers[name] = conn

        def _connect():
            try:
                ok = conn.initialize()
                if ok:
                    tools = conn.discover_tools()
                    client.sync_to_registry()
                    CONFIG["mcp_servers"] = client.to_config()
                    save_settings()
                    QTimer.singleShot(0, lambda: (
                        self._mcp_refresh(),
                        self.context.log(
                            "MCP connected: {} -- {} tools".format(
                                name, len(tools))),
                    ))
                else:
                    QTimer.singleShot(0, lambda: (
                        self._mcp_refresh(),
                        self.context.log(
                            "MCP connect failed: {} -- {}".format(
                                name, conn.error)),
                    ))
            except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, TimeoutError, ConnectionError) as e:
                err_str = str(e)
                QTimer.singleShot(0, lambda err=err_str: (
                    self._mcp_refresh(),
                    self.context.log("MCP error: {}".format(err)),
                ))

        self._mcp_status_label.setText(
            "Connecting to {}...".format(name))
        threading.Thread(target=_connect, daemon=True).start()
        self._mcp_name_input.clear()
        self._mcp_url_input.clear()

    def _mcp_remove_selected(self):
        """Remove the selected MCP server."""
        row = self._mcp_table.currentRow()
        if row < 0:
            QMessageBox.information(self._mcp_table, "No Selection",
                                    "Select a server.")
            return
        item = self._mcp_table.item(row, 0)
        if not item:
            return
        server_name = item.data(Qt.ItemDataRole.UserRole)
        reply = QMessageBox.question(
            self._mcp_table, "Remove Server",
            "Remove MCP server '{}'?".format(server_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        client = self._get_mcp_client()
        if not client:
            return
        if server_name in client.servers:
            del client.servers[server_name]
        from plugin_registry import registry
        server_slug = server_name.upper().replace(" ", "_")
        prefix = "MCP_{}_".format(server_slug)
        to_remove = [c for c in client._synced_commands
                     if c.startswith(prefix)]
        for cmd in to_remove:
            if cmd in registry.plugins:
                del registry.plugins[cmd]
            registry.disabled.discard(cmd)
            del client._synced_commands[cmd]
        from config import CONFIG, save_settings
        CONFIG["mcp_servers"] = client.to_config()
        save_settings()
        self._mcp_refresh()
        self._mcp_detail_text.clear()
        self.context.log(
            "MCP server removed: {}".format(server_name))

    def _mcp_test_selected(self):
        """Test connectivity to the selected server."""
        row = self._mcp_table.currentRow()
        if row < 0:
            QMessageBox.information(self._mcp_table, "No Selection",
                                    "Select a server.")
            return
        item = self._mcp_table.item(row, 0)
        if not item:
            return
        server_name = item.data(Qt.ItemDataRole.UserRole)
        client = self._get_mcp_client()
        if not client or server_name not in client.servers:
            return
        conn = client.servers[server_name]
        self._mcp_detail_text.setPlainText(
            "Testing {}...".format(conn.name))

        def _test():
            try:
                health = conn.check_health()
                QTimer.singleShot(0, lambda:
                    self._mcp_show_test_result(conn.name, health))
            except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, TimeoutError, ConnectionError) as e:
                err_str = str(e)
                QTimer.singleShot(0, lambda err=err_str:
                    self._mcp_detail_text.setPlainText(
                        "Test failed: {}".format(err)))

        threading.Thread(target=_test, daemon=True).start()

    def _mcp_show_test_result(self, name, health):
        """Display health check result."""
        if health["ok"]:
            text = "{} is reachable! Tools: {}".format(
                name, health["tool_count"])
            self.context.log(
                "MCP test passed: {} ({} tools)".format(
                    name, health["tool_count"]))
        else:
            text = "{} is not reachable! Error: {}".format(
                name, health["error"])
            self.context.log(
                "MCP test failed: {} -- {}".format(
                    name, health["error"]))
        self._mcp_detail_text.setPlainText(text)

    def _mcp_discover_all(self):
        """Re-discover tools from all servers."""
        client = self._get_mcp_client()
        if not client:
            return
        self._mcp_status_label.setText("Discovering tools...")

        def _discover():
            try:
                results = client.discover_all()
                client.sync_to_registry()
                from config import CONFIG, save_settings
                CONFIG["mcp_servers"] = client.to_config()
                save_settings()
                total = sum(results.values())
                parts = ["{}: {}".format(n, c)
                         for n, c in sorted(results.items())]
                msg = "Discovery complete -- {} tools across {} servers".format(
                    total, len(results))
                text = msg + "\n\n" + "\n".join(parts)
                QTimer.singleShot(0, lambda: (
                    self._mcp_refresh(),
                    self._mcp_detail_text.setPlainText(text),
                    self.context.log("MCP discovery: {}".format(msg)),
                ))
            except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, TimeoutError, ConnectionError) as e:
                err_str = str(e)
                QTimer.singleShot(0, lambda err=err_str: (
                    self._mcp_refresh(),
                    self.context.log(
                        "MCP discovery failed: {}".format(err)),
                ))

        threading.Thread(target=_discover, daemon=True).start()

    def _mcp_call_tool(self):
        """Call a tool on the selected server."""
        row = self._mcp_table.currentRow()
        if row < 0:
            QMessageBox.information(self._mcp_table, "No Selection",
                                    "Select a server.")
            return
        item = self._mcp_table.item(row, 0)
        if not item:
            return
        server_name = item.data(Qt.ItemDataRole.UserRole)
        tool_name = self._mcp_call_tool_input.text().strip()
        if not tool_name:
            QMessageBox.warning(self._mcp_table, "Missing Tool",
                                "Enter a tool name.")
            return
        args_text = self._mcp_call_args_input.text().strip()
        try:
            args = json.loads(args_text) if args_text else {}
        except json.JSONDecodeError as e:
            QMessageBox.warning(self._mcp_table, "Invalid JSON",
                                "Args must be valid JSON: {}".format(e))
            return
        client = self._get_mcp_client()
        if not client or server_name not in client.servers:
            return
        conn = client.servers[server_name]
        self._mcp_detail_text.setPlainText(
            "Calling {} on {}...".format(tool_name, conn.name))

        def _call():
            try:
                result = conn.call_tool(tool_name, args)
                texts = [c.get("text", "")
                         for c in result.get("content", [])
                         if c.get("type") == "text"]
                output = ("\n".join(texts) if texts
                          else json.dumps(result, indent=2))
                is_error = result.get("isError", False)
                label = "Error" if is_error else "Result"
                msg = "MCP call: {} on {} {}".format(
                    tool_name, conn.name,
                    "failed" if is_error else "ok")
                QTimer.singleShot(0, lambda: (
                    self._mcp_detail_text.setPlainText(
                        "{} from {}:\n\n{}".format(
                            label, tool_name, output)),
                    self.context.log(msg),
                ))
            except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, TimeoutError, ConnectionError) as e:
                err_str = str(e)
                QTimer.singleShot(0, lambda err=err_str:
                    self._mcp_detail_text.setPlainText(
                        "Call failed: {}".format(err)))

        threading.Thread(target=_call, daemon=True).start()