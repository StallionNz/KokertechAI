"""
tabs/scheduled_actions_tab.py — Scheduled / Cron Actions.

Lets users define cron-like timed AI tasks that run on a background
timer. Tasks are stored in the memory vault and checked periodically.

Sprint 7 backlog item (Feature suggestion #15).
"""

import json
import threading
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QLineEdit, QComboBox, QMessageBox, QSpinBox,
)

from config import DB_PATH
from logging_config import get_logger

if TYPE_CHECKING:
    from tabs.context import DashboardContext

logger = get_logger(name="ScheduledActionsTab")

SCHEDULED_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    action TEXT NOT NULL,
    params TEXT DEFAULT '{}',
    interval_seconds INTEGER DEFAULT 3600,
    last_run TEXT,
    next_run TEXT,
    enabled INTEGER DEFAULT 1,
    created_at TEXT,
    run_count INTEGER DEFAULT 0,
    last_result TEXT DEFAULT ''
)
"""


class ScheduledActionsTabMixin:
    """Mixin that adds a Scheduled Actions tab for cron-like timed tasks."""

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

    # ── Lifecycle ──────────────────────────────────────────────────

    def create_scheduled_actions_tab(self):
        """Build and return the Scheduled Actions tab widget."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)

        # ── Header ──
        header = QHBoxLayout()
        title = QLabel("Scheduled Actions")
        title.setStyleSheet("font-size: 13pt; font-weight: bold; color: #F59E0B;")
        header.addWidget(title)
        header.addStretch()

        self._sa_status_label = QLabel("Scheduler: Idle")
        self._sa_status_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        header.addWidget(self._sa_status_label)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._sa_refresh_tasks)
        header.addWidget(btn_refresh)

        layout.addLayout(header)

        # ── Task table ──
        self._sa_table = QTableWidget(0, 7)
        self._sa_table.setHorizontalHeaderLabels([
            "Name", "Action", "Interval", "Last Run", "Next Run",
            "Count", "Status"
        ])
        self._sa_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._sa_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._sa_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._sa_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._sa_table.setMaximumHeight(280)
        layout.addWidget(self._sa_table)

        # ── Add task form ──
        form_group = QGroupBox("Add New Scheduled Task")
        form_layout = QHBoxLayout(form_group)

        self._sa_name_input = QLineEdit()
        self._sa_name_input.setPlaceholderText("Task name...")
        form_layout.addWidget(self._sa_name_input)

        self._sa_action_combo = QComboBox()
        self._sa_action_combo.setMinimumWidth(160)
        self._sa_populate_action_combo()
        form_layout.addWidget(self._sa_action_combo)

        self._sa_interval_spin = QSpinBox()
        self._sa_interval_spin.setRange(10, 86400)
        self._sa_interval_spin.setValue(3600)
        self._sa_interval_spin.setSuffix(" sec")
        form_layout.addWidget(self._sa_interval_spin)

        btn_add = QPushButton("Add Task")
        btn_add.setStyleSheet("background-color: #1e3a5f; border-color: #2563eb;")
        btn_add.clicked.connect(self._sa_add_task)
        form_layout.addWidget(btn_add)

        btn_delete = QPushButton("Delete Selected")
        btn_delete.setStyleSheet("background-color: #7f1d1d; border-color: #991b1b;")
        btn_delete.clicked.connect(self._sa_delete_task)
        form_layout.addWidget(btn_delete)

        layout.addWidget(form_group)

        # ── Last result preview ──
        self._sa_result_label = QLabel("")
        self._sa_result_label.setWordWrap(True)
        self._sa_result_label.setStyleSheet(
            "font-size: 9pt; color: #9CA3AF; padding: 2px;"
        )
        layout.addWidget(self._sa_result_label)

        # ── Stats bar ──
        self._sa_stats_label = QLabel("Total tasks: 0 | Enabled: 0")
        self._sa_stats_label.setStyleSheet("font-size: 9pt; color: #6B7280;")
        layout.addWidget(self._sa_stats_label)

        layout.addStretch()

        # ── Ensure table exists in DB ──
        self._sa_ensure_table()

        # ── Load initial data ──
        self._sa_refresh_tasks()

        # ── Start background scheduler timer ──
        self._sa_scheduler_timer = QTimer(self)
        self._sa_scheduler_timer.timeout.connect(self._sa_check_due_tasks)
        self._sa_scheduler_timer.start(30000)  # Check every 30 seconds
        self._sa_status_label.setText("Scheduler: Active (30s check)")

        return tab

    def _sa_populate_action_combo(self):
        """Populate the action dropdown from the plugin registry."""
        try:
            import plugin_registry
            pr = plugin_registry.registry
            self._sa_action_combo.clear()
            for cmd in sorted(pr.plugins.keys()):
                if pr.is_enabled(cmd):
                    meta = pr.metadata.get(cmd, {})
                    name = meta.get("name", cmd)
                    self._sa_action_combo.addItem(f"{name} ({cmd})", cmd)
        except (ImportError, AttributeError, KeyError, TypeError):
            self._sa_action_combo.addItem("(registry unavailable)")

    # ── Database ───────────────────────────────────────────────────

    def _sa_ensure_table(self):
        """Create the scheduled_tasks table if it doesn't exist."""
        import memory_vault
        db_path = getattr(memory_vault, "DB_PATH", DB_PATH)
        try:
            conn = sqlite3.connect(db_path, timeout=15.0)
            try:
                conn.execute(SCHEDULED_TASKS_DDL)
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            logger.error(f"Scheduled tasks table creation failed: {e}")

    def _sa_load_tasks(self):
        """Load all scheduled tasks from the DB.

        Returns:
            list of dicts with keys: id, name, action, params,
            interval_seconds, last_run, next_run, enabled, run_count, last_result.
        """
        tasks = []
        try:
            conn = sqlite3.connect(DB_PATH, timeout=15.0)
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT id, name, action, params, interval_seconds, "
                    "last_run, next_run, enabled, run_count, last_result "
                    "FROM scheduled_tasks ORDER BY id ASC"
                )
                tasks = [dict(r) for r in cursor.fetchall()]
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            logger.error(f"Failed to load scheduled tasks: {e}")
        return tasks

    # ── UI Refresh ─────────────────────────────────────────────────

    def _sa_refresh_tasks(self):
        """Reload the task table from the database."""
        tasks = self._sa_load_tasks()
        self._sa_table.setRowCount(len(tasks))
        enabled_count = 0

        for i, t in enumerate(tasks):
            self._sa_table.setItem(i, 0, QTableWidgetItem(t.get("name", "")))
            self._sa_table.setItem(i, 1, QTableWidgetItem(t.get("action", "")))
            interval = t.get("interval_seconds", 3600)
            self._sa_table.setItem(i, 2, QTableWidgetItem(self._sa_fmt_interval(interval)))
            self._sa_table.setItem(i, 3, QTableWidgetItem(t.get("last_run", "—")[:16]))
            self._sa_table.setItem(i, 4, QTableWidgetItem(t.get("next_run", "—")[:16]))
            self._sa_table.setItem(i, 5, QTableWidgetItem(str(t.get("run_count", 0))))

            enabled = t.get("enabled", 1)
            if enabled:
                enabled_count += 1
            status = "✅ Active" if enabled else "⏸️ Paused"
            status_item = QTableWidgetItem(status)
            status_item.setForeground(
                Qt.GlobalColor.green if enabled else Qt.GlobalColor.gray
            )
            self._sa_table.setItem(i, 6, status_item)

            # Store task ID in UserRole for reference
            self._sa_table.item(i, 0).setData(
                Qt.ItemDataRole.UserRole, t.get("id")
            )

        # Update stats
        self._sa_stats_label.setText(
            f"Total tasks: {len(tasks)} | Enabled: {enabled_count}"
        )

    @staticmethod
    def _sa_fmt_interval(seconds):
        """Format seconds into a human-readable interval string."""
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            return f"{seconds // 60}m"
        elif seconds < 86400:
            return f"{seconds // 3600}h"
        return f"{seconds // 86400}d"

    # ── Task CRUD ──────────────────────────────────────────────────

    def _sa_add_task(self):
        """Add a new scheduled task from the form inputs."""
        name = self._sa_name_input.text().strip()
        if not name:
            QMessageBox.warning(self._sa_table, "Missing Name",
                                "Please enter a task name.")
            return

        action_data = self._sa_action_combo.currentData()
        if not action_data:
            QMessageBox.warning(self._sa_table, "Missing Action",
                                "Please select an action.")
            return

        interval = self._sa_interval_spin.value()
        now = datetime.now(timezone.utc)
        next_run = now + timedelta(seconds=interval)

        import memory_vault
        db_path = getattr(memory_vault, "DB_PATH", DB_PATH)
        try:
            conn = sqlite3.connect(db_path, timeout=15.0)
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO scheduled_tasks "
                        "(name, action, params, interval_seconds, "
                        "created_at, next_run, enabled) "
                        "VALUES (?, ?, ?, ?, ?, ?, 1)",
                        (name, action_data, "{}", interval,
                         now.strftime("%Y-%m-%d %H:%M:%S"),
                         next_run.strftime("%Y-%m-%d %H:%M:%S"))
                    )
            finally:
                conn.close()
            self._sa_name_input.clear()
            self._sa_refresh_tasks()
            self.context.log(f"⏰ Scheduled task added: {name} ({action_data})")
        except sqlite3.OperationalError as e:
            QMessageBox.critical(self._sa_table, "DB Error", str(e))

    def _sa_delete_task(self):
        """Delete the selected task after confirmation."""
        selected = self._sa_table.currentRow()
        if selected < 0:
            QMessageBox.information(self._sa_table, "No Selection",
                                    "Please select a task to delete.")
            return

        task_id = self._sa_table.item(selected, 0).data(Qt.ItemDataRole.UserRole)
        task_name = self._sa_table.item(selected, 0).text()

        reply = QMessageBox.question(
            self._sa_table, "Delete Task",
            f"Delete scheduled task '{task_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        import memory_vault
        db_path = getattr(memory_vault, "DB_PATH", DB_PATH)
        try:
            conn = sqlite3.connect(db_path, timeout=15.0)
            try:
                with conn:
                    conn.execute("DELETE FROM scheduled_tasks WHERE id = ?",
                                 (task_id,))
            finally:
                conn.close()
            self._sa_refresh_tasks()
            self.context.log(f"⏰ Scheduled task deleted: {task_name}")
        except sqlite3.OperationalError as e:
            QMessageBox.critical(self._sa_table, "DB Error", str(e))

    # ── Scheduler ──────────────────────────────────────────────────

    def _sa_check_due_tasks(self):
        """Check for due tasks and execute them on a background thread."""
        # Skip if shutting down
        if getattr(self, '_shutting_down', False):
            return

        tasks = self._sa_load_tasks()
        now = datetime.now(timezone.utc)
        due = []
        for t in tasks:
            if not t.get("enabled", 1):
                continue
            next_run_str = t.get("next_run", "")
            if not next_run_str:
                continue
            try:
                next_dt = datetime.strptime(
                    next_run_str, "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
                if now >= next_dt:
                    due.append(t)
            except ValueError:
                continue

        if not due:
            return

        self.context.log(f"⏰ Scheduler: {len(due)} task(s) due")
        for t in due:
            self._sa_run_task(t)

    def _sa_run_task(self, task):
        """Execute a single scheduled task on a background thread.

        Updates last_run, next_run, run_count, and last_result after execution.
        """
        def _execute():
            action = task.get("action", "")
            params_str = task.get("params", "{}")
            try:
                params = json.loads(params_str) if params_str else {}
            except json.JSONDecodeError:
                params = {}

            result = ""
            try:
                import plugin_registry
                pr = plugin_registry.registry
                if action in pr.plugins:
                    intent = {"action": action, **params}
                    result = pr.plugins[action].execute(intent)
                    status = f"✅ {action} succeeded"
                else:
                    result = f"Unknown action: {action}"
                    status = f"❌ Unknown action: {action}"
            except (RuntimeError, OSError, ValueError, TypeError, KeyError) as e:
                result = str(e)
                status = f"❌ {action} failed: {e}"

            now = datetime.now(timezone.utc)
            next_run = now + timedelta(seconds=task.get("interval_seconds", 3600))
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            next_str = next_run.strftime("%Y-%m-%d %H:%M:%S")

            import memory_vault
            db_path = getattr(memory_vault, "DB_PATH", DB_PATH)
            try:
                conn = sqlite3.connect(db_path, timeout=15.0)
                try:
                    with conn:
                        conn.execute(
                            "UPDATE scheduled_tasks SET "
                            "last_run = ?, next_run = ?, "
                            "run_count = run_count + 1, last_result = ? "
                            "WHERE id = ?",
                            (now_str, next_str, result[:500], task["id"])
                        )
                finally:
                    conn.close()
            except sqlite3.OperationalError as e:
                logger.error(f"Failed to update task #{task['id']}: {e}")

            # Update UI on main thread
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: (
                self._sa_refresh_tasks(),
                self._sa_result_label.setText(
                    f"Last: {status} ({now_str})"
                )
            ))
            self.context.log(f"⏰ Task '{task.get('name', '?')}': {status}")

        threading.Thread(target=_execute, daemon=True).start()

    def teardown_scheduled_actions(self) -> None:
        """Stop background scheduler timer during application shutdown."""
        timer = getattr(self, "_sa_scheduler_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except (RuntimeError, AttributeError):
                pass

    def teardown(self) -> None:
        """Alias for teardown_scheduled_actions."""
        self.teardown_scheduled_actions()


