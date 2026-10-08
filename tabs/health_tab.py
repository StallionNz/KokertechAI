"""
Health Dashboard tab mixin — real-time system monitoring.
Sprint 6.7: CPU, RAM, VRAM, disk, uptime, error rate monitoring.
"""
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from config import WORKSPACE_DIR
from PyQt6.QtCore import Qt, QPointF, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QProgressBar, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QLinearGradient, QPainterPath
import resource_history
from logging_config import get_logger

if TYPE_CHECKING:
    from tabs.context import DashboardContext

logger = get_logger(name="HealthTab")

_START_TIME = time.time()  # Process start timestamp
_HISTORY_MAX = 60  # Keep 60 data points = 2 minutes at 2s intervals


def _to_utc(dt_val: datetime) -> datetime:
    """Normalize datetime to UTC for safe comparisons against UTC datetimes."""
    if dt_val.tzinfo is None:
        return dt_val.astimezone(timezone.utc)
    return dt_val.astimezone(timezone.utc)


class HistorySparkline(QWidget):
    """A QPainter-based sparkline widget showing a time series as a mini chart.

    Draws a filled gradient sparkline with a trimmed moving-window buffer.
    """
    def __init__(self, parent=None, line_color="#3B82F6", fill_color="#3B82F6",
                 max_points=60, height=36):
        super().__init__(parent)
        self._data = []
        self._line_color = QColor(line_color)
        self._fill_color = QColor(fill_color)
        self._max_points = max_points
        self._min_val = 0.0
        self._max_val = 100.0
        self.setFixedHeight(height)
        self.setMinimumWidth(100)

    def push(self, value: float):
        """Add a data point and trim the buffer."""
        self._data.append(value)
        if len(self._data) > self._max_points:
            self._data = self._data[-self._max_points:]
        if value < self._min_val:
            self._min_val = value
        if value > self._max_val:
            self._max_val = value
        self.update()

    def push_batch(self, values):
        """Replace data with a batch of values and repaint once.

        Avoids N individual repaints when seeding historical data
        (e.g. loading resource history on Health tab startup).
        Trims to ``_max_points`` and recalculates ``_min_val`` /
        ``_max_val`` from the batch.
        """
        if not values:
            return
        self._data = list(values)[-self._max_points:]
        self._min_val = min(self._data)
        self._max_val = max(self._data)
        self.update()

    def paintEvent(self, event):
        if not self._data:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        if w <= 1 or h <= 1:
            painter.end()
            return

        pad = 2
        plot_w = w - pad * 2
        plot_h = h - pad * 2

        val_range = max(self._max_val - self._min_val, 1.0)
        n = len(self._data)

        # Build point list
        points = []
        for i, val in enumerate(self._data):
            x = pad + (i / max(n - 1, 1)) * plot_w
            y = pad + plot_h - ((val - self._min_val) / val_range) * plot_h
            points.append(QPointF(x, y))

        # Fill gradient below the line
        if len(points) >= 2:
            gradient = QLinearGradient(0, 0, 0, h)
            fill = QColor(self._fill_color)
            fill.setAlpha(80)
            gradient.setColorAt(0.0, fill)
            fill_end = QColor(self._fill_color)
            fill_end.setAlpha(10)
            gradient.setColorAt(1.0, fill_end)

            path = QPainterPath()
            path.moveTo(points[0])
            for p in points[1:]:
                path.lineTo(p)
            path.lineTo(QPointF(points[-1].x(), h - pad))
            path.lineTo(QPointF(points[0].x(), h - pad))
            path.closeSubpath()
            painter.fillPath(path, QBrush(gradient))

        # Draw the line
        pen = QPen(self._line_color, 1.5)
        painter.setPen(pen)
        for i in range(len(points) - 1):
            painter.drawLine(points[i], points[i + 1])

        # Current value label
        latest = self._data[-1]
        label = f"{latest:.0f}"
        painter.setPen(QPen(self._line_color))
        painter.drawText(self.width() - 28, 10, label)

        painter.end()


class HealthTabMixin:
    """Mixin providing the Health Dashboard tab."""

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

    def create_health_tab(self) -> QWidget:
        """Create the health monitoring dashboard tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Header ──
        header = QLabel("🩺 System Health Dashboard")
        header.setStyleSheet("font-size: 14pt; font-weight: bold;")
        layout.addWidget(header)

        # ── Uptime row ──
        uptime_row = QHBoxLayout()
        self.health_uptime_lbl = QLabel("Uptime: --")
        self.health_uptime_lbl.setStyleSheet("font-size: 11pt; color: #10B981;")
        uptime_row.addWidget(self.health_uptime_lbl)
        uptime_row.addStretch()
        self.health_error_count_lbl = QLabel("Errors: 0")
        self.health_error_count_lbl.setStyleSheet("font-size: 11pt;")
        uptime_row.addWidget(self.health_error_count_lbl)
        layout.addLayout(uptime_row)

        # ── Resource gauges ──
        gauges_group = QGroupBox("📊 Resource Usage")
        gauges_layout = QVBoxLayout(gauges_group)

        # CPU
        cpu_row = QHBoxLayout()
        cpu_row.addWidget(QLabel("CPU:"))
        self.health_cpu_bar = QProgressBar()
        self.health_cpu_bar.setRange(0, 100)
        self.health_cpu_bar.setFormat("CPU %p%")
        self.health_cpu_bar.setTextVisible(True)
        self.health_cpu_bar.setStyleSheet(
            "QProgressBar::chunk { background-color: #3B82F6; }"
        )
        cpu_row.addWidget(self.health_cpu_bar, 1)
        self.health_cpu_label = QLabel("--")
        cpu_row.addWidget(self.health_cpu_label)
        gauges_layout.addLayout(cpu_row)

        # RAM
        ram_row = QHBoxLayout()
        ram_row.addWidget(QLabel("RAM:"))
        self.health_ram_bar = QProgressBar()
        self.health_ram_bar.setRange(0, 100)
        self.health_ram_bar.setFormat("RAM %p%")
        self.health_ram_bar.setTextVisible(True)
        self.health_ram_bar.setStyleSheet(
            "QProgressBar::chunk { background-color: #10B981; }"
        )
        ram_row.addWidget(self.health_ram_bar, 1)
        self.health_ram_label = QLabel("--")
        ram_row.addWidget(self.health_ram_label)
        gauges_layout.addLayout(ram_row)

        # VRAM / GPU
        gpu_row = QHBoxLayout()
        gpu_row.addWidget(QLabel("VRAM:"))
        self.health_vram_bar = QProgressBar()
        self.health_vram_bar.setRange(0, 100)
        self.health_vram_bar.setFormat("VRAM %p%")
        self.health_vram_bar.setTextVisible(True)
        self.health_vram_bar.setStyleSheet(
            "QProgressBar::chunk { background-color: #F59E0B; }"
        )
        gpu_row.addWidget(self.health_vram_bar, 1)
        self.health_vram_label = QLabel("--")
        gpu_row.addWidget(self.health_vram_label)
        gauges_layout.addLayout(gpu_row)

        # Disk
        disk_row = QHBoxLayout()
        disk_row.addWidget(QLabel("Disk:"))
        self.health_disk_bar = QProgressBar()
        self.health_disk_bar.setRange(0, 100)
        self.health_disk_bar.setFormat("Disk %p%")
        self.health_disk_bar.setTextVisible(True)
        self.health_disk_bar.setStyleSheet(
            "QProgressBar::chunk { background-color: #8B5CF6; }"
        )
        disk_row.addWidget(self.health_disk_bar, 1)
        self.health_disk_label = QLabel("--")
        disk_row.addWidget(self.health_disk_label)
        gauges_layout.addLayout(disk_row)

        layout.addWidget(gauges_group)

        # ── Provider processes ──
        proc_group = QGroupBox("🔌 AI Provider Processes")
        proc_layout = QVBoxLayout(proc_group)
        # Provider status badge header
        self.health_provider_status = QLabel()
        self.health_provider_status.setStyleSheet("font-size: 9pt; color: #9CA3AF; font-weight: bold; padding: 2px;")
        proc_layout.addWidget(self.health_provider_status)
        self.health_process_table = QTableWidget(0, 4)
        self.health_process_table.setHorizontalHeaderLabels(
            ["Process", "PID", "CPU %", "RAM (MB)"]
        )
        self.health_process_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.health_process_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.health_process_table.setMaximumHeight(150)
        proc_layout.addWidget(self.health_process_table)
        layout.addWidget(proc_group)

        # ── Event log ──
        events_group = QGroupBox("📋 Recent Events")
        events_layout = QVBoxLayout(events_group)
        self.health_events_table = QTableWidget(0, 3)
        self.health_events_table.setHorizontalHeaderLabels(
            ["Time", "Type", "Message"]
        )
        self.health_events_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.health_events_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.health_events_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self.health_events_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        events_layout.addWidget(self.health_events_table)
        layout.addWidget(events_group)

        # ── Error rate line ──
        error_row = QHBoxLayout()
        self.health_error_rate_lbl = QLabel("Error rate (last hour): --")
        error_row.addWidget(self.health_error_rate_lbl)
        error_row.addStretch()
        layout.addLayout(error_row)

        # ── History sparklines (appended below gauges) ──
        sparkline_group = QGroupBox("📈 Resource History (last 2 min)")
        sparkline_layout = QVBoxLayout(sparkline_group)

        # CPU sparkline
        cpu_spark_row = QHBoxLayout()
        cpu_spark_row.addWidget(QLabel("CPU:"))
        self._cpu_sparkline = HistorySparkline(line_color="#3B82F6", fill_color="#3B82F6")
        cpu_spark_row.addWidget(self._cpu_sparkline, 1)
        sparkline_layout.addLayout(cpu_spark_row)

        # RAM sparkline
        ram_spark_row = QHBoxLayout()
        ram_spark_row.addWidget(QLabel("RAM:"))
        self._ram_sparkline = HistorySparkline(line_color="#10B981", fill_color="#10B981")
        ram_spark_row.addWidget(self._ram_sparkline, 1)
        sparkline_layout.addLayout(ram_spark_row)

        # VRAM sparkline
        vram_spark_row = QHBoxLayout()
        vram_spark_row.addWidget(QLabel("VRAM:"))
        self._vram_sparkline = HistorySparkline(line_color="#F59E0B", fill_color="#F59E0B")
        vram_spark_row.addWidget(self._vram_sparkline, 1)
        sparkline_layout.addLayout(vram_spark_row)

        # Disk sparkline
        disk_spark_row = QHBoxLayout()
        disk_spark_row.addWidget(QLabel("Disk:"))
        self._disk_sparkline = HistorySparkline(line_color="#8B5CF6", fill_color="#8B5CF6")
        disk_spark_row.addWidget(self._disk_sparkline, 1)
        sparkline_layout.addLayout(disk_spark_row)

        layout.addWidget(sparkline_group)

        # ── Load persisted resource history for sparkline continuity ──
        self._load_resource_history()

        # ── Start polling ──
        self._health_events = []  # list of (timestamp, event_type, message)
        self._health_error_timestamps = []  # timestamps of errors for rate calc
        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self._update_health)
        self.health_timer.start(2000)  # Poll every 2 seconds
        self._update_health()  # Immediate first update

        return tab

    def _update_health_provider_status(self):
        """Update the provider online/offline badge (throttled to 30s intervals).

        Sprint 15: When a controller with provider_health_check() is available,
        runs a real health check in a background thread and displays model name,
        VRAM, latency. Falls back to "🔴 Offline (<err>)" on error.

        Without a controller (e.g. tests), synchronously sets "🔴 Offline (No controller)".
        """
        import time
        now = time.time()
        last = getattr(self, '_last_health_provider_check', 0)
        if now - last < 30:
            return
        self._last_health_provider_check = now

        ctrl = getattr(self, 'controller', None)
        if ctrl is None or not hasattr(ctrl, 'provider_health_check'):
            # No controller — synchronous path (tests and fallback)
            self._set_health_provider_status("\U0001f534", "Offline (No controller)", "#EF4444")
            return

        # Controller available — run real health check in background thread
        def _check():
            try:
                health = ctrl.provider_health_check()
                if health.get("ok"):
                    model = health.get("model_name", "unknown")
                    vram = health.get("vram_used_mb", 0)
                    latency = health.get("latency_ms", 0)
                    n_ctx = health.get("n_ctx", 0)
                    label = f"{model[:25]} | {vram}MB | {latency}ms | ctx={n_ctx}"
                    icon, color = "\U0001f7e2", "#10B981"
                else:
                    label = health.get("error", "Offline")
                    icon, color = "\U0001f534", "#EF4444"
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda i=icon, l=label, c=color: (
                    self._set_health_provider_status(i, l, c)))
            except (RuntimeError, OSError, ValueError, TypeError, KeyError) as e:
                err_str = str(e)
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda err=err_str: self._set_health_provider_status(
                    "\U0001f534", f"Offline ({err[:20]})" if err else "Offline", "#EF4444"))

        import threading
        threading.Thread(target=_check, daemon=True).start()

    def _set_health_provider_status(self, icon, name, color):
        """Update the health tab provider status label on the main thread.
        Guards against shutdown and destroyed widgets.
        """
        if getattr(self, '_shutting_down', False):
            return
        try:
            self.health_provider_status.setText(f"{icon} {name}")
            self.health_provider_status.setStyleSheet(
                f"font-size: 9pt; color: {color}; font-weight: bold; padding: 2px;"
            )
        except RuntimeError:
            pass

    def log_health_event(self, event_type: str, message: str):
        """Log a health event (thread-safe via QTimer callback on main thread)."""
        ts = datetime.now(timezone.utc)
        self._health_events.append((ts, event_type, message))
        # Keep last 200 events
        if len(self._health_events) > 200:
            self._health_events = self._health_events[-200:]
        if event_type == "error":
            self._health_error_timestamps.append(ts)
            # Keep last hour of errors
            cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
            self._health_error_timestamps = [
                t for t in self._health_error_timestamps if _to_utc(t) > cutoff
            ]

    def _update_health(self):
        """Poll system resources and update all gauges/tables."""
        try:
            import psutil

            # CPU (interval=None = non-blocking, returns last reading)
            cpu = psutil.cpu_percent(interval=None)
            self.health_cpu_bar.setValue(int(cpu))
            self.health_cpu_label.setText(f"{cpu:.0f}%")
            if cpu > 80:
                self.health_cpu_bar.setStyleSheet(
                    "QProgressBar::chunk { background-color: #EF4444; }"
                )
            else:
                self.health_cpu_bar.setStyleSheet(
                    "QProgressBar::chunk { background-color: #3B82F6; }"
                )
            # Push to sparkline
            if hasattr(self, '_cpu_sparkline'):
                self._cpu_sparkline.push(cpu)

            # RAM
            ram = psutil.virtual_memory()
            self.health_ram_bar.setValue(int(ram.percent))
            used_gb = ram.used / (1024 ** 3)
            total_gb = ram.total / (1024 ** 3)
            self.health_ram_label.setText(f"{used_gb:.1f}/{total_gb:.1f} GB")
            # Push to sparkline
            if hasattr(self, '_ram_sparkline'):
                self._ram_sparkline.push(ram.percent)
            # Threshold coloring for RAM bar (CONFIG-driven, mirrors disk pattern)
            ram_warn = self.context.get_config("ram_warning_pct", 80)
            ram_crit = self.context.get_config("ram_critical_pct", 95)
            if ram.percent >= ram_crit:
                self.health_ram_bar.setStyleSheet(
                    "QProgressBar::chunk { background-color: #EF4444; }"
                )
            elif ram.percent >= ram_warn:
                self.health_ram_bar.setStyleSheet(
                    "QProgressBar::chunk { background-color: #FBBF24; }"
                )
            else:
                self.health_ram_bar.setStyleSheet(
                    "QProgressBar::chunk { background-color: #10B981; }"
                )

            # Disk
            disk = psutil.disk_usage("C:\\")
            self.health_disk_bar.setValue(int(disk.percent))
            free_gb = disk.free / (1024 ** 3)
            self.health_disk_label.setText(f"{free_gb:.0f} GB free")
            # Push to sparkline
            if hasattr(self, '_disk_sparkline'):
                self._disk_sparkline.push(disk.percent)
            # Threshold coloring for disk bar
            disk_warn = self.context.get_config("disk_warning_pct", 85)
            disk_crit = self.context.get_config("disk_critical_pct", 95)
            if disk.percent >= disk_crit:
                self.health_disk_bar.setStyleSheet(
                    "QProgressBar::chunk { background-color: #EF4444; }"
                )
            elif disk.percent >= disk_warn:
                self.health_disk_bar.setStyleSheet(
                    "QProgressBar::chunk { background-color: #FBBF24; }"
                )
            else:
                self.health_disk_bar.setStyleSheet(
                    "QProgressBar::chunk { background-color: #8B5CF6; }"
                )

            # VRAM (try NVIDIA first)
            vram_used, vram_total = self._get_vram()
            if vram_total > 0:
                vram_pct = int((vram_used / vram_total) * 100)
                self.health_vram_bar.setValue(vram_pct)
                self.health_vram_label.setText(
                    f"{vram_used:.0f}/{vram_total:.0f} MB"
                )
                # Push to sparkline
                if hasattr(self, '_vram_sparkline'):
                    self._vram_sparkline.push(vram_pct)
                # Threshold coloring for VRAM bar (CONFIG-driven, mirrors disk pattern)
                vram_warn = self.context.get_config("vram_warning_pct", 88)
                vram_crit = self.context.get_config("vram_critical_pct", 95)
                if vram_pct >= vram_crit:
                    self.health_vram_bar.setStyleSheet(
                        "QProgressBar::chunk { background-color: #EF4444; }"
                    )
                elif vram_pct >= vram_warn:
                    self.health_vram_bar.setStyleSheet(
                        "QProgressBar::chunk { background-color: #FBBF24; }"
                    )
                else:
                    self.health_vram_bar.setStyleSheet(
                        "QProgressBar::chunk { background-color: #F59E0B; }"
                    )
            else:
                self.health_vram_bar.setValue(0)
                self.health_vram_label.setText("N/A")
                vram_pct = 0
                # Reset to default color so a stale red/amber chunk from a
                # prior poll doesn't linger when VRAM becomes unavailable.
                self.health_vram_bar.setStyleSheet(
                    "QProgressBar::chunk { background-color: #F59E0B; }"
                )

            # ── Push to persistent ring buffer ──
            rh = getattr(self, '_resource_history', None)
            if rh is not None:
                rh.push(cpu, ram.percent, vram_pct, disk.percent)
                rh.save()  # Debounced — only writes every 30s if dirty

            # Provider processes
            self._update_process_table()
            # Provider status (throttled to 30s intervals)
            self._update_health_provider_status()

            # Events table
            self._update_events_table()

            # Error rate
            hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
            recent_errors = sum(
                1 for t in self._health_error_timestamps if _to_utc(t) > hour_ago
            )
            self.health_error_rate_lbl.setText(
                f"Error rate (last hour): {recent_errors}/hr"
            )

        except ImportError:
            self.health_cpu_label.setText("psutil required")
            return
        except (RuntimeError, OSError, ValueError, AttributeError) as e:
            logger.debug(f"Health poll failed: {e}")

        # Uptime and error count — always updated, even if psutil polling
        # raises an exception (the broad ``except Exception: pass`` above
        # should not swallow these independent metrics).
        try:
            uptime_sec = time.time() - _START_TIME
            uptime_str = self._format_uptime(uptime_sec)
            self.health_uptime_lbl.setText(f"Uptime: {uptime_str}")
        except (RuntimeError, OSError, ValueError, AttributeError) as e:
            logger.debug(f"Uptime label update failed: {e}")

        try:
            error_count = len(self._health_error_timestamps)
            self.health_error_count_lbl.setText(f"Errors: {error_count}")
            if error_count > 0:
                self.health_error_count_lbl.setStyleSheet(
                    "font-size: 11pt; color: #EF4444;"
                )
            else:
                self.health_error_count_lbl.setStyleSheet(
                    "font-size: 11pt; color: #10B981;"
                )
        except (RuntimeError, OSError, ValueError, AttributeError) as e:
            logger.debug(f"Error-count label update failed: {e}")

    def _load_resource_history(self):
        """Load persisted resource history and seed the sparklines.

        Creates a ResourceHistoryRingBuffer pointed at the JSON file
        in WORKSPACE_DIR/data/, loads any existing records, and pushes
        them into the sparkline widgets so historical usage is visible
        immediately on Health tab open.
        """
        save_path = os.path.join(WORKSPACE_DIR, "data", "resource_history.json")
        rh = resource_history.ResourceHistoryRingBuffer.load(
            save_path, max_entries=300
        )
        self._resource_history = rh

        # Seed sparklines with historical series data (oldest first).
        for metric, sparkline_attr in (
            ("cpu", "_cpu_sparkline"),
            ("ram", "_ram_sparkline"),
            ("vram", "_vram_sparkline"),
            ("disk", "_disk_sparkline"),
        ):
            series = rh.get_series(metric)
            sparkline = getattr(self, sparkline_attr, None)
            if sparkline is not None and series:
                # Push all historical values in one batch so the sparkline
                # renders the full saved window with a single repaint
                # instead of one per data point (up to 300 repaints).
                sparkline.push_batch(series)

    def _save_resource_history_immediate(self):
        """Force an immediate save of the resource history ring buffer.

        Wired to ``closeEvent`` so the latest CPU, RAM, VRAM, and disk
        samples are on disk before the app exits. Called via hasattr guard
        in ``AppLifecycleMixin.closeEvent`` alongside ``_flush_chat_history_save``.
        """
        rh = getattr(self, '_resource_history', None)
        if rh is not None:
            rh.save_immediate()

    def _get_vram(self):
        """Get VRAM usage. Returns (used_mb, total_mb)."""
        try:
            # Try NVIDIA SMI
            result = subprocess.run(
                [  # noqa: S607
                    "nvidia-smi",
                    "--query-gpu=memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True, text=True, timeout=5,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split(",")
                return float(parts[0].strip()), float(parts[1].strip())
        except (subprocess.SubprocessError, OSError, ValueError, IndexError, Exception) as e:  # noqa: BLE001
            logger.debug(f"nvidia-smi VRAM probe failed: {e}")

        try:
            # Try GPUtil
            import GPUtil
            gpus = GPUtil.getGPUs()
            if gpus:
                gpu = gpus[0]
                return gpu.memoryUsed, gpu.memoryTotal
        except ImportError:
            pass

        return 0, 0

    def _update_process_table(self):
        """Update the provider process table."""
        if not hasattr(self, "_psutil_available"):
            try:
                import psutil
                self._psutil_available = True
            except ImportError:
                self._psutil_available = False

        if not self._psutil_available:
            return

        targets = {
            "llama-server.exe": "llama.cpp",
            "python.exe": "Python",
        }
        import psutil

        rows = []
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                name = proc.info["name"]
                if name in targets:
                    display = targets[name]
                    pid = proc.info["pid"]
                    p = psutil.Process(pid)
                    cpu = p.cpu_percent(interval=0)
                    mem = p.memory_info().rss / (1024 * 1024)
                    rows.append((display, pid, cpu, mem))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        self.health_process_table.setRowCount(len(rows))
        for i, (display, pid, cpu, mem) in enumerate(rows):
            self.health_process_table.setItem(i, 0, QTableWidgetItem(display))
            self.health_process_table.setItem(
                i, 1, QTableWidgetItem(str(pid))
            )
            self.health_process_table.setItem(
                i, 2, QTableWidgetItem(f"{cpu:.0f}%")
            )
            self.health_process_table.setItem(
                i, 3, QTableWidgetItem(f"{mem:.0f}")
            )

    def _update_events_table(self):
        """Update the recent events table with the last 50 events."""
        events = self._health_events[-50:]
        self.health_events_table.setRowCount(len(events))
        for i, (ts, event_type, message) in enumerate(reversed(events)):
            time_str = ts.strftime("%H:%M:%S")
            self.health_events_table.setItem(i, 0, QTableWidgetItem(time_str))
            type_item = QTableWidgetItem(event_type)
            if event_type == "error":
                type_item.setForeground(Qt.GlobalColor.red)
            elif event_type == "warning":
                type_item.setForeground(Qt.GlobalColor.yellow)
            self.health_events_table.setItem(i, 1, type_item)
            self.health_events_table.setItem(i, 2, QTableWidgetItem(message[:120]))

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """Format seconds into a readable uptime string."""
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            return f"{hours}h {minutes}m {secs}s"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        return f"{secs}s"
