"""Cognitive Agency (SCBE & Growth Arcs) tab mixin."""
import os, sqlite3
from typing import TYPE_CHECKING
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QSplitter, QHeaderView
from PyQt6.QtGui import QColor
from config import WORKSPACE_DIR
from logging_config import get_logger

if TYPE_CHECKING:
    from tabs.context import DashboardContext

logger = get_logger(name="CognitiveAgencyTab")

class CognitiveAgencyTabMixin:
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

    def create_agency_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top_bar = QHBoxLayout()
        title = QLabel("⚙️ Cognitive Agency (SCBE & Growth Arcs)")
        title.setStyleSheet("font-weight: bold; color: #10B981; font-size: 12pt;")
        top_bar.addWidget(title)
        top_bar.addStretch()
        refresh_btn = QPushButton("REFRESH LEDGERS")
        refresh_btn.clicked.connect(self.refresh_agency_data)
        top_bar.addWidget(refresh_btn)
        layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        bias_panel = QWidget()
        bias_layout = QVBoxLayout(bias_panel)
        bias_layout.addWidget(QLabel("📊 Self-Consistent Bias Engine (SCBE) Ledger"))
        self.bias_table = QTableWidget(0, 4)
        self.bias_table.setHorizontalHeaderLabels(["Timestamp", "Bias Type", "Confidence", "Description"])
        self.bias_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        bias_layout.addWidget(self.bias_table)
        splitter.addWidget(bias_panel)

        growth_panel = QWidget()
        growth_layout = QVBoxLayout(growth_panel)
        growth_layout.addWidget(QLabel("📈 Growth Arcs & Energy Shifts"))
        self.growth_table = QTableWidget(0, 3)
        self.growth_table.setHorizontalHeaderLabels(["Timestamp", "Event Description", "Energy Shift"])
        self.growth_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        growth_layout.addWidget(self.growth_table)
        splitter.addWidget(growth_panel)

        # ── Available Models panel (Discovery.probe result) ──────
        # Third pane in the horizontal splitter.  Populated from
        # ``self.available_models`` (set by the startup discovery
        # probe thread, see AppLifecycleMixin.setup_discovery_probe).
        # Refresh is wired into refresh_agency_data() so the existing
        # REFRESH LEDGERS button doubles as the probe-refresh action.
        models_panel = QWidget()
        models_layout = QVBoxLayout(models_panel)
        self.models_status_label = QLabel("🔍 Available Local Models (probing…)")
        self.models_status_label.setStyleSheet(
            "font-size: 9pt; color: #9CA3AF; padding: 2px;"
        )
        models_layout.addWidget(self.models_status_label)
        self.models_table = QTableWidget(0, 4)
        self.models_table.setHorizontalHeaderLabels(
            ["ID", "Created", "Owned By", "Object"]
        )
        self.models_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        models_layout.addWidget(self.models_table)
        splitter.addWidget(models_panel)

        layout.addWidget(splitter)
        self.refresh_agency_data()
        return tab

    def _refresh_available_models(self):
        """Populate the ``models_table`` from ``self.available_models``
        (set by ``setup_discovery_probe``) and update the status label.

        Reads ``self.available_models``, ``self._discovery_status``,
        and ``self._discovery_diagnostic`` under ``self._lock`` so the
        UI thread sees an atomic snapshot of the producer's last
        publish.  When this tab isn't constructed (e.g., tests that
        skip tab creation), all three ``hasattr`` guards short-circuit
        into a no-op.

        Hoisted imports: ``datetime`` lives at module scope, NOT inside
        the per-row loop, so a models_list with 50 entries doesn't pay
        import resolution 50 times.
        """
        from datetime import datetime as _dt, timezone as _tz

        if not hasattr(self, 'models_table'):
            return  # tab not constructed; nothing to populate

        # Atomic snapshot under the SAME lock the probe thread writes
        # under.  Branch the read so we don't do the unlocked reads
        # twice -- earlier version unconditionally read first, then
        # re-read under lock when ``_lock`` existed (overwriting the
        # earlier unlocked values, wasting their work in the WITH path).
        if hasattr(self, '_lock'):
            with self._lock:
                snapshot_models = list(getattr(self, 'available_models', []))
                snapshot_status = getattr(self, '_discovery_status', 'pending')
                snapshot_diagnostic = getattr(
                    self, '_discovery_diagnostic', ''
                )
        else:
            # No lock on the dashboard -- torn reads acceptable here
            # (test mocks only -- production code always builds via
            # KokertechDashboard which sets self._lock in __init__).
            snapshot_models = list(getattr(self, 'available_models', []))
            snapshot_status = getattr(self, '_discovery_status', 'pending')
            snapshot_diagnostic = getattr(
                self, '_discovery_diagnostic', ''
            )

        # Status label
        if not hasattr(self, 'models_status_label'):
            return
        if snapshot_status == "success":
            self.models_status_label.setText(
                f"🔍 Available Local Models — {len(snapshot_models)} online"
            )
            self.models_status_label.setStyleSheet(
                "font-size: 9pt; color: #10B981; padding: 2px;"
            )
        elif snapshot_status == "empty":
            self.models_status_label.setText(
                f"🔍 Available Local Models — none found "
                f"({snapshot_diagnostic[:60]})"
            )
            self.models_status_label.setStyleSheet(
                "font-size: 9pt; color: #FBBF24; padding: 2px;"
            )
        elif snapshot_status == "error":
            self.models_status_label.setText(
                f"🔍 Available Local Models — probe failed "
                f"({snapshot_diagnostic[:60]})"
            )
            self.models_status_label.setStyleSheet(
                "font-size: 9pt; color: #EF4444; padding: 2px;"
            )
        elif snapshot_status == "skipped":
            self.models_status_label.setText(
                "🔍 Available Local Models — disabled via CONFIG"
            )
            self.models_status_label.setStyleSheet(
                "font-size: 9pt; color: #9CA3AF; padding: 2px;"
            )
        else:
            # pending or unknown
            self.models_status_label.setText(
                "🔍 Available Local Models — probing…"
            )
            self.models_status_label.setStyleSheet(
                "font-size: 9pt; color: #9CA3AF; padding: 2px;"
            )

        # Table rows
        try:
            self.models_table.setRowCount(len(snapshot_models))
            for ri, model in enumerate(snapshot_models):
                if not isinstance(model, dict):
                    # Defensive: extractor filters non-dicts, but a
                    # relaxed future variant might allow them.
                    self.models_table.setItem(
                        ri, 0, QTableWidgetItem(str(model))
                    )
                    continue
                # OpenAI-shaped dict: id, object, created, owned_by
                model_id = str(model.get("id", "—"))
                created_raw = model.get("created", "")
                if isinstance(created_raw, (int, float)):
                    # OpenAI emits unix timestamp; show ISO for readability.
                    try:
                        created_disp = _dt.fromtimestamp(
                            int(created_raw), tz=_tz.utc
                        ).strftime("%Y-%m-%d %H:%M")
                    except (ValueError, OSError, OverflowError):
                        created_disp = str(created_raw)
                else:
                    created_disp = str(created_raw) if created_raw else "—"
                owned_by = str(model.get("owned_by", "—"))
                obj_type = str(model.get("object", "—"))
                self.models_table.setItem(
                    ri, 0, QTableWidgetItem(model_id)
                )
                self.models_table.setItem(
                    ri, 1, QTableWidgetItem(created_disp)
                )
                self.models_table.setItem(
                    ri, 2, QTableWidgetItem(owned_by)
                )
                self.models_table.setItem(
                    ri, 3, QTableWidgetItem(obj_type)
                )
        except (RuntimeError, AttributeError) as e:
            # Widget destroyed mid-call (shutdown race).
            try:
                self.context.log(f"⚠️ Failed to populate models table: {e}")
            except (RuntimeError, AttributeError, OSError) as ex:
                logger.debug(f"Audit log write failed (models table): {ex}")

    def refresh_agency_data(self):
        import memory_vault
        db_path = getattr(memory_vault, "DB_PATH", os.path.join(WORKSPACE_DIR, "kokertech_vault.db"))
        # Probe panel comes AFTER SQLite on purpose: the user-pressed
        # REFRESH LEDGERS button is for ledger data primarily; doing
        # probe refresh in the same call is a nice-to-have but shouldn't
        # delay the primary surface if the SQLite query is slow.
        try:
            self._refresh_available_models()
        except RuntimeError:
            # The only documented failure mode is "widget destroyed
            # mid-call" during shutdown.  Catch ONLY that specifically
            # so a real bug in ``_refresh_available_models`` surfacing
            # as some other exception type still propagates instead of
            # being silently swallowed (formerly caught all `Exception`).
            pass

        if not os.path.exists(db_path): return
        try:
            conn = sqlite3.connect(db_path, timeout=15.0)
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT timestamp, bias_type, confidence_score, description FROM bias_ledger ORDER BY id DESC LIMIT 50")
                biases = cursor.fetchall()
                self.bias_table.setRowCount(len(biases))
                for ri, rd in enumerate(biases):
                    self.bias_table.setItem(ri, 0, QTableWidgetItem(str(rd[0])))
                    self.bias_table.setItem(ri, 1, QTableWidgetItem(str(rd[1])))
                    self.bias_table.setItem(ri, 2, QTableWidgetItem(f"{rd[2]}%"))
                    self.bias_table.setItem(ri, 3, QTableWidgetItem(str(rd[3])))
            except sqlite3.OperationalError as e:
                self.context.log(f"ℹ️ Agency bias ledger not initialized: {e}", is_debug=True)
            try:
                cursor.execute("SELECT timestamp, event_description, energy_shift FROM growth_arcs ORDER BY id DESC LIMIT 50")
                growths = cursor.fetchall()
                self.growth_table.setRowCount(len(growths))
                for ri, rd in enumerate(growths):
                    self.growth_table.setItem(ri, 0, QTableWidgetItem(str(rd[0])))
                    self.growth_table.setItem(ri, 1, QTableWidgetItem(str(rd[1])))
                    shift = float(rd[2])
                    shift_str = f"+{shift}%" if shift > 0 else f"{shift}%"
                    item = QTableWidgetItem(shift_str)
                    if shift > 0: item.setForeground(QColor("#10B981"))
                    elif shift < 0: item.setForeground(QColor("#EF4444"))
                    self.growth_table.setItem(ri, 2, item)
            except sqlite3.OperationalError as e:
                self.context.log(f"ℹ️ Agency growth arcs not initialized: {e}", is_debug=True)
            finally:
                conn.close()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            self.context.log(f"⚠️ Failed to refresh agency data: {e}")
