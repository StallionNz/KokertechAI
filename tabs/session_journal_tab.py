"""
tabs/session_journal_tab.py \u2014 Session Journal Dashboard.
Sprint 14: Searchable, filterable view of all session journal entries
from session_journal.jsonl.
"""
import json
import csv
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, QDateTime
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QTextEdit, QLineEdit, QComboBox, QSplitter,
    QDialog, QDateTimeEdit, QDialogButtonBox, QFormLayout,
    QFileDialog, QApplication,
)

from auto_logger import get_session_events, EventCategory

if TYPE_CHECKING:
    from tabs.context import DashboardContext


_CATEGORIES = ["all"] + [e.value for e in EventCategory]


class SessionJournalTabMixin:
    """Mixin that adds a Session Journal Dashboard tab."""

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

    def create_session_journal_tab(self):
        """Build and return the Session Journal tab widget."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(6, 6, 6, 6)

        # Header
        header = QHBoxLayout()
        title = QLabel("Session Journal Dashboard")
        title.setStyleSheet("font-size: 13pt; font-weight: bold; color: #F59E0B;")
        header.addWidget(title)
        header.addStretch()

        # Search bar
        self._sj_search = QLineEdit()
        self._sj_search.setPlaceholderText("Search events by message or data...")
        self._sj_search.textChanged.connect(self._sj_refresh)
        self._sj_search.setMinimumWidth(200)
        header.addWidget(self._sj_search)

        # Category filter
        self._sj_category_filter = QComboBox()
        self._sj_category_filter.addItems(_CATEGORIES)
        self._sj_category_filter.currentTextChanged.connect(self._sj_refresh)
        header.addWidget(self._sj_category_filter)

        # Time-range filter
        self._sj_time_filter = QComboBox()
        self._sj_time_filter.addItems([
            "All Time", "Last 30 min", "Last hour",
            "Last 2 hours", "Last 6 hours", "Today", "Custom range",
        ])
        self._sj_time_filter.setToolTip("Filter events by recency")
        self._sj_previous_time_filter = "All Time"
        self._sj_custom_start = None
        self._sj_custom_end = None
        self._sj_time_filter.currentTextChanged.connect(self._sj_on_time_filter)
        header.addWidget(self._sj_time_filter)

        # Sort-order toggle
        self._sj_sort_newest = True
        self._sj_sort_btn = QPushButton("\u2193 Newest")
        self._sj_sort_btn.setToolTip(
            "Toggle sort order (newest / oldest first)")
        self._sj_sort_btn.clicked.connect(self._sj_toggle_sort)
        self._sj_sort_btn.setFixedWidth(90)
        header.addWidget(self._sj_sort_btn)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._sj_refresh)
        header.addWidget(btn_refresh)

        # Export button
        btn_export = QPushButton("Export")
        btn_export.setToolTip("Export filtered events as JSON or CSV")
        btn_export.clicked.connect(self._sj_export)
        header.addWidget(btn_export)

        # Import button
        btn_import = QPushButton("Import")
        btn_import.setToolTip("Load events from a JSON or CSV file")
        btn_import.clicked.connect(self._sj_import)
        header.addWidget(btn_import)

        # Loading spinner label (hidden by default)
        self._sj_loading = QLabel("\u23f3")
        self._sj_loading.setStyleSheet(
            "font-size: 16px; color: #F59E0B;"
        )
        self._sj_loading.setToolTip("Operation in progress...")
        self._sj_loading.hide()
        header.addWidget(self._sj_loading)

        layout.addLayout(header)

        # Stats bar
        self._sj_stats = QLabel("Total: \u2014 | Session: \u2014 | Last refresh: \u2014")
        self._sj_stats.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        layout.addWidget(self._sj_stats)

        # Splitter: table + detail
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Event table
        self._sj_table = QTableWidget(0, 5)
        self._sj_table.setHorizontalHeaderLabels([
            "Timestamp", "Offset", "Category", "Message", "Data"
        ])
        self._sj_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._sj_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._sj_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self._sj_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        self._sj_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.ResizeToContents
        )
        self._sj_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._sj_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._sj_table.currentCellChanged.connect(self._sj_on_select)
        splitter.addWidget(self._sj_table)

        # Detail panel
        detail_group = QGroupBox("Event Details")
        detail_layout = QVBoxLayout(detail_group)

        self._sj_detail = QTextEdit()
        self._sj_detail.setReadOnly(True)
        self._sj_detail.setMaximumHeight(200)
        self._sj_detail.setPlaceholderText("Select an event to see full details")
        self._sj_detail.setStyleSheet("font-size: 9pt;")
        detail_layout.addWidget(self._sj_detail)

        splitter.addWidget(detail_group)
        splitter.setSizes([400, 150])
        layout.addWidget(splitter)

        # Auto-refresh + initial load
        self._sj_timer = QTimer(self)
        self._sj_timer.timeout.connect(self._sj_refresh)
        self._sj_timer.start(5000)
        QTimer.singleShot(100, self._sj_refresh)

        return tab

    def _sj_on_time_filter(self, text):
        """Handle time-filter combo changes.  Selecting 'Custom range'
        opens a date/time picker dialog; all other options refresh
        immediately."""
        if text == "Custom range":
            self._sj_time_filter.blockSignals(True)
            try:
                dlg = QDialog(self._sj_time_filter.window())
                dlg.setWindowTitle("Custom Date Range")
                dlg.setMinimumWidth(380)
                layout = QFormLayout(dlg)

                start_dt = QDateTimeEdit()
                start_dt.setCalendarPopup(True)
                start_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
                if self._sj_custom_start is not None:
                    start_dt.setDateTime(QDateTime(self._sj_custom_start))
                else:
                    start_dt.setDateTime(
                        QDateTime.currentDateTime().addSecs(-86400))
                layout.addRow("Start:", start_dt)

                end_dt = QDateTimeEdit()
                end_dt.setCalendarPopup(True)
                end_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
                if self._sj_custom_end is not None:
                    end_dt.setDateTime(QDateTime(self._sj_custom_end))
                else:
                    end_dt.setDateTime(QDateTime.currentDateTime())
                layout.addRow("End:", end_dt)

                buttons = QDialogButtonBox(
                    QDialogButtonBox.StandardButton.Ok
                    | QDialogButtonBox.StandardButton.Cancel)
                buttons.accepted.connect(dlg.accept)
                buttons.rejected.connect(dlg.reject)
                layout.addRow(buttons)

                if dlg.exec() == QDialog.DialogCode.Accepted:
                    self._sj_custom_start = (
                        start_dt.dateTime().toPyDateTime())
                    self._sj_custom_end = (
                        end_dt.dateTime().toPyDateTime())
                    self._sj_previous_time_filter = "Custom range"
                else:
                    self._sj_time_filter.setCurrentText(
                        self._sj_previous_time_filter)
            finally:
                self._sj_time_filter.blockSignals(False)
                self._sj_refresh()
        else:
            self._sj_previous_time_filter = text
            self._sj_refresh()

    def _sj_toggle_sort(self):
        """Toggle between newest-first and oldest-first sort order."""
        self._sj_sort_newest = not self._sj_sort_newest
        self._sj_sort_btn.setText(
            "\u2193 Newest" if self._sj_sort_newest else "\u2191 Oldest")
        self._sj_refresh()

    def _sj_load_events(self):
        category = self._sj_category_filter.currentText()
        cat = None if category == "all" else category
        events = get_session_events(category=cat, limit=2000)
        return list(reversed(events))

    def _sj_refresh(self):
        # Resume auto-refresh timer if it was paused by import
        if not self._sj_timer.isActive():
            self._sj_timer.start(5000)

        events = self._sj_load_events()

        # Time-range filter
        time_filter = self._sj_time_filter.currentText()
        if time_filter != "All Time":
            now = datetime.now(timezone.utc)
            if time_filter == "Last 30 min":
                cutoff = now.timestamp() - 1800
            elif time_filter == "Last hour":
                cutoff = now.timestamp() - 3600
            elif time_filter == "Last 2 hours":
                cutoff = now.timestamp() - 7200
            elif time_filter == "Last 6 hours":
                cutoff = now.timestamp() - 21600
            elif time_filter == "Today":
                cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            elif time_filter == "Custom range":
                if self._sj_custom_start is not None and self._sj_custom_end is not None:
                    filtered = []
                    for e in events:
                        ts = e.get("timestamp", "")
                        if ts:
                            try:
                                et = datetime.fromisoformat(ts)
                                if self._sj_custom_start <= et <= self._sj_custom_end:
                                    filtered.append(e)
                            except (ValueError, TypeError):
                                filtered.append(e)
                        else:
                            filtered.append(e)
                    events = filtered
                cutoff = None  # already filtered inline above
            else:
                cutoff = None

            if cutoff is not None:
                filtered = []
                for e in events:
                    ts = e.get("timestamp", "")
                    if ts:
                        try:
                            et = datetime.fromisoformat(ts).timestamp()
                            if et >= cutoff:
                                filtered.append(e)
                        except (ValueError, TypeError):
                            filtered.append(e)
                    else:
                        filtered.append(e)
                events = filtered

        search = self._sj_search.text().strip().lower()
        if search:
            events = [
                e for e in events
                if search in e.get("message", "").lower()
                or search in json.dumps(e.get("data", {}), default=str).lower()
            ]

        # Sort order — events arrive newest-first from _sj_load_events;
        # reverse for oldest-first display.
        if not self._sj_sort_newest:
            events = list(reversed(events))

        # Build custom-range badge for stats bar
        range_badge = ""
        if self._sj_time_filter.currentText() == "Custom range":
            if self._sj_custom_start and self._sj_custom_end:
                range_badge = (
                    f" | Range: {self._sj_custom_start.strftime('%Y-%m-%d %H:%M')}"
                    f" \u2192 {self._sj_custom_end.strftime('%Y-%m-%d %H:%M')}"
                )

        self._sj_populate_table(events, extra_badge=range_badge)

    def _sj_populate_table(self, events, extra_badge=""):
        """Populate the table and stats bar with *events* (already
        filtered and sorted).  Called by both _sj_refresh and _sj_import."""
        self._sj_filtered_events = events
        self._sj_table.setRowCount(len(events))

        for i, e in enumerate(events):
            ts = e.get("timestamp", "")[:19].replace("T", " ")
            self._sj_table.setItem(i, 0, QTableWidgetItem(ts))

            offset = e.get("session_offset_s", 0)
            if offset >= 60:
                offset_str = f"{offset // 3600}h {(offset % 3600) // 60}m"
            else:
                offset_str = f"{offset}s"
            self._sj_table.setItem(i, 1, QTableWidgetItem(offset_str))

            category = e.get("category", "?")
            cat_item = QTableWidgetItem(category)
            cat_map = {
                "error": Qt.GlobalColor.red,
                "warning": Qt.GlobalColor.yellow,
                "session": Qt.GlobalColor.green,
                "system": Qt.GlobalColor.cyan,
            }
            cat_color = cat_map.get(category)
            if cat_color:
                cat_item.setForeground(cat_color)
            self._sj_table.setItem(i, 2, cat_item)

            msg = e.get("message", "")
            self._sj_table.setItem(i, 3, QTableWidgetItem(msg[:150]))

            data = e.get("data", {})
            if data:
                data_str = json.dumps(data, default=str)
                data_item = QTableWidgetItem(
                    data_str[:80] + ("..." if len(data_str) > 80 else "")
                )
            else:
                data_item = QTableWidgetItem("\u2014")
            data_item.setForeground(Qt.GlobalColor.gray)
            self._sj_table.setItem(i, 4, data_item)

            self._sj_table.item(i, 0).setData(
                Qt.ItemDataRole.UserRole, e)

        total = len(events)
        by_cat = {}
        for e in events:
            cat = e.get("category", "?")
            by_cat[cat] = by_cat.get(cat, 0) + 1
        cat_parts = " | ".join(
            f"{c}: {n}" for c, n in sorted(by_cat.items()) if n > 0
        )
        self._sj_stats.setText(
            f"Total: {total} events | {cat_parts}{extra_badge} | "
            f"Last refresh: {datetime.now(timezone.utc).strftime('%H:%M:%S')}"
        )

    def _sj_show_loading(self):
        """Show the loading spinner and force a UI refresh."""
        self._sj_loading.show()
        if self._sj_loading.window() is not None:
            QApplication.processEvents()

    def _sj_hide_loading(self):
        """Hide the loading spinner."""
        self._sj_loading.hide()

    def _sj_import(self):
        """Load events from a JSON or CSV file and display them.

        The auto-refresh timer is paused while imported data is shown;
        any subsequent filter change or manual refresh resumes it."""
        self._sj_show_loading()
        try:
            path, fmt = QFileDialog.getOpenFileName(
                self._sj_table.window(),
                "Import Events",
                "",
                "JSON (*.json);;CSV (*.csv)",
            )
            if not path:
                return  # user cancelled (finally hides spinner)

            if fmt == "CSV (*.csv)" or path.endswith(".csv"):
                with open(path, newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    events = []
                    for row in reader:
                        event = {
                            "timestamp": row["timestamp"],
                            "session_offset_s": int(
                                row.get("session_offset_s", 0)),
                            "category": row["category"],
                            "message": row["message"],
                            "data": json.loads(row.get("data", "{}")),
                        }
                        events.append(event)
            else:
                with open(path, encoding="utf-8") as f:
                    events = json.load(f)

            if not isinstance(events, list):
                raise ValueError(
                    "Expected a JSON array of events, got "
                    f"{type(events).__name__}")

            # Pause auto-refresh so the imported data stays visible
            self._sj_timer.stop()

            self._sj_populate_table(
                events,
                extra_badge=(
                    f" | Imported: {len(events)} events from "
                    f"{path.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]}"
                ),
            )
        except (OSError, IOError, json.JSONDecodeError, KeyError,
                ValueError, TypeError, AttributeError) as e:
            self._sj_stats.setText(
                f"Import failed: {e} | {datetime.now(timezone.utc).strftime('%H:%M:%S')}"
            )
        finally:
            self._sj_hide_loading()

    def _sj_on_select(self, row, col):
        if row < 0:
            self._sj_detail.setPlainText("")
            return
        item = self._sj_table.item(row, 0)
        if not item:
            return
        event = item.data(Qt.ItemDataRole.UserRole)
        if not event:
            return
        lines = [
            f"Timestamp: {event.get('timestamp', '?')}",
            f"Session offset: {event.get('session_offset_s', 0)}s",
            f"Category: {event.get('category', '?')}",
            f"Message: {event.get('message', '')}",
        ]
        data = event.get("data", {})
        if data:
            lines.append("")
            lines.append("Data:")
            lines.append(json.dumps(data, indent=2, default=str))
        self._sj_detail.setPlainText("\n".join(lines))

    def _sj_export(self):
        """Export the currently filtered events as JSON or CSV."""
        events = getattr(self, "_sj_filtered_events", None)
        if not events:
            return

        self._sj_show_loading()
        try:
            path, fmt = QFileDialog.getSaveFileName(
                self._sj_table.window(),
                "Export Events",
                f"journal_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json",
                "JSON (*.json);;CSV (*.csv)",
            )
            if not path:
                return  # user cancelled (finally hides spinner)

            if fmt == "CSV (*.csv)" or path.endswith(".csv"):
                # Flatten data dict into JSON string for CSV columns
                rows = []
                for e in events:
                    row = {
                        "timestamp": e.get("timestamp", ""),
                        "session_offset_s": e.get("session_offset_s", 0),
                        "category": e.get("category", ""),
                        "message": e.get("message", ""),
                        "data": json.dumps(e.get("data", {}), default=str),
                    }
                    rows.append(row)
                with open(path, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=rows[0].keys())
                    w.writeheader()
                    w.writerows(rows)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(events, f, indent=2, default=str, ensure_ascii=False)

            self._sj_stats.setText(
                f"Exported {len(events)} events to "
                f"{path.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]}"
                f" | {datetime.now(timezone.utc).strftime('%H:%M:%S')}"
            )
        except (OSError, IOError) as e:
            self._sj_stats.setText(
                f"Export failed: {e} | {datetime.now(timezone.utc).strftime('%H:%M:%S')}"
            )
        finally:
            self._sj_hide_loading()

    def teardown_session_journal(self) -> None:
        """Stop auto-refresh timer during application shutdown."""
        timer = getattr(self, "_sj_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except (RuntimeError, AttributeError):
                pass

    def teardown(self) -> None:
        """Alias for teardown_session_journal."""
        self.teardown_session_journal()


