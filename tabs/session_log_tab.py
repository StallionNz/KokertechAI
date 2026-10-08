"""
tabs/session_log_tab.py — SessionLogTabMixin: Freebuff session log statistics.

Runs session_stats.py programmatically to aggregate Freebuff session data
and displays the results in a rich dashboard tab with stats cards, per-session
breakdown, sprint hours, tag frequency, and SVG badge generation.
"""

import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QApplication, QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QTextEdit, QSplitter, QFrame, QSizePolicy,
)

from logging_config import get_logger

logger = get_logger(name="SessionLogTab")

# Import session_stats functions — it lives at the project root (parent of tabs/).
# Lazy-loaded so we don't pay the ``yaml`` import / disk-scan cost unless the
# Session Log tab is actually instantiated.
_SESSION_STATS_LOADED = False
_load_attempted = False  # Prevent retry-flooding on persistent errors
_find_files = None
_parse_yaml = None
_aggregate = None
_generate_badges = None
_count_files = None


def _project_root():
    """Return the project root directory (parent of the tabs/ directory)."""
    here = os.path.dirname(os.path.abspath(__file__))  # .../tabs
    return os.path.dirname(here)


def _load_session_stats():
    """Import session_stats.py functions lazily (avoids circular import at module level).

    Uses Python's standard import machinery rather than ``importlib.util.spec_from_file_location``
    because the latter has historically tripped on subtle Windows path-casing mismatches
    (lowercase ``c:\\`` vs the real mixed-case drive letter on the volume) and on
    ``__pycache__`` source-lookup edge cases, producing sporadic ``FileNotFoundError``
    tracebacks even when ``os.path.isfile`` confirms the file exists.
    """
    global _SESSION_STATS_LOADED, _load_attempted, _find_files, _parse_yaml, _aggregate, _generate_badges, _count_files
    if _SESSION_STATS_LOADED:
        return True
    if _load_attempted:
        return False
    try:
        import sys as _sys

        root = _project_root()
        # Ensure the project root is on sys.path so ``import session_stats`` resolves
        # regardless of the current working directory or how the app was launched.
        if root and root not in _sys.path:
            _sys.path.insert(0, root)

        import session_stats as _mod  # type: ignore

        _find_files = _mod.find_files
        _parse_yaml = _mod.parse_yaml
        _aggregate = _mod.aggregate
        _generate_badges = _mod._generate_badges
        _count_files = _mod.count_files
        _SESSION_STATS_LOADED = True
        logger.info(f"session_stats loaded from {os.path.join(root, 'session_stats.py')}")
        return True
    except (ImportError, OSError, ValueError, RuntimeError, AttributeError) as e:
        logger.error(f"Failed to load session_stats: {e}")
        _load_attempted = True
    return False


class SessionLogTabMixin:
    """Mixin providing the Session Log Statistics tab.

    Displays aggregated Freebuff session data from session_stats.py
    in a rich PyQt6 dashboard panel.
    """

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
            restore_chat_input=getattr(self, "restore_chat_input", None),
            switch_tab=getattr(self, "switch_tab", None),
        )

    @context.setter
    def context(self, value):
        self.ctx = value

    def create_session_log_tab(self) -> QWidget:
        """Create and return the Session Log tab widget."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Header ──────────────────────────────────────────────
        header_row = QHBoxLayout()
        header = QLabel("📊 Session Log Statistics")
        header.setStyleSheet("font-size: 14pt; font-weight: bold;")
        header_row.addWidget(header)
        header_row.addStretch()

        self.slog_refresh_btn = QPushButton("🔄 Refresh")
        self.slog_refresh_btn.clicked.connect(self._slog_refresh)
        header_row.addWidget(self.slog_refresh_btn)

        self.slog_badge_btn = QPushButton("🏅 Generate Badges")
        self.slog_badge_btn.setStyleSheet(
            "background-color: #065F46; border-color: #059669;"
        )
        self.slog_badge_btn.clicked.connect(self._slog_generate_badges)
        header_row.addWidget(self.slog_badge_btn)

        self.slog_auto_badge_cb = QCheckBox("Auto-badges")
        self.slog_auto_badge_cb.setChecked(False)
        self.slog_auto_badge_cb.setToolTip(
            "Auto-generate badges on every refresh (including auto-refresh)"
        )
        self.slog_auto_badge_cb.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        header_row.addWidget(self.slog_auto_badge_cb)

        self.slog_status_label = QLabel("")
        self.slog_status_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        header_row.addWidget(self.slog_status_label)

        layout.addLayout(header_row)

        # ── Stats Cards ─────────────────────────────────────────
        cards_row = QHBoxLayout()

        self._slog_card("Sessions", "—", "#007ec6", cards_row, "slog_sessions_card")
        self._slog_card("Hours", "—", "#97ca00", cards_row, "slog_hours_card")
        self._slog_card("Created", "—", "#4c1", cards_row, "slog_created_card")
        self._slog_card("Modified", "—", "#e05d44", cards_row, "slog_modified_card")
        self._slog_card("Changed", "—", "#fe7d37", cards_row, "slog_changed_card")

        layout.addLayout(cards_row)

        # ── Splitter: session table (top) + detail (bottom) ────
        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Per-Session Table ───────────────────────────────────
        table_group = QGroupBox("Per-Session Breakdown")
        table_layout = QVBoxLayout(table_group)

        self.slog_table = QTableWidget(0, 6)
        self.slog_table.setHorizontalHeaderLabels([
            "Session", "Date", "Duration", "Files +", "Files ~", "Focus"
        ])
        self.slog_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch
        )
        self.slog_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.slog_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.slog_table.setSortingEnabled(True)
        self.slog_table.setColumnWidth(0, 80)
        self.slog_table.setColumnWidth(1, 120)
        self.slog_table.setColumnWidth(2, 80)
        self.slog_table.setColumnWidth(3, 60)
        self.slog_table.setColumnWidth(4, 60)
        table_layout.addWidget(self.slog_table)
        splitter.addWidget(table_group)

        # ── Detail panel (bottom) ───────────────────────────────
        detail_panel = QWidget()
        detail_layout = QHBoxLayout(detail_panel)

        # Sprint hours
        sprint_group = QGroupBox("Sprint Hours")
        sprint_layout = QVBoxLayout(sprint_group)
        self.slog_sprint_display = QTextEdit()
        self.slog_sprint_display.setReadOnly(True)
        self.slog_sprint_display.setMaximumHeight(180)
        self.slog_sprint_display.setStyleSheet("font-size: 10pt; font-family: 'Consolas', monospace;")
        sprint_layout.addWidget(self.slog_sprint_display)
        detail_layout.addWidget(sprint_group)

        # Tags
        tags_group = QGroupBox("Top Tags")
        tags_layout = QVBoxLayout(tags_group)
        self.slog_tags_display = QTextEdit()
        self.slog_tags_display.setReadOnly(True)
        self.slog_tags_display.setMaximumHeight(180)
        self.slog_tags_display.setStyleSheet("font-size: 10pt; font-family: 'Consolas', monospace;")
        tags_layout.addWidget(self.slog_tags_display)
        detail_layout.addWidget(tags_group)

        # Created / Modified files
        files_group = QGroupBox("Files Changed")
        files_layout = QVBoxLayout(files_group)
        self.slog_files_display = QTextEdit()
        self.slog_files_display.setReadOnly(True)
        self.slog_files_display.setStyleSheet("font-size: 9pt; font-family: 'Consolas', monospace;")
        files_layout.addWidget(self.slog_files_display)
        detail_layout.addWidget(files_group)

        splitter.addWidget(detail_panel)
        splitter.setSizes([300, 300])

        layout.addWidget(splitter, 1)

        # ── Badge area ───────────────────────────────────────────
        self.slog_badge_area = QFrame()
        badge_area_layout = QVBoxLayout(self.slog_badge_area)
        badge_area_layout.setContentsMargins(4, 4, 4, 4)
        badge_header = QLabel("🏅 Badges")
        badge_header.setStyleSheet("font-size: 10pt; font-weight: bold;")
        badge_area_layout.addWidget(badge_header)

        # Inline badge row — populated dynamically after generation
        self.slog_badge_row = QWidget()
        self.slog_badge_row_layout = QHBoxLayout(self.slog_badge_row)
        self.slog_badge_row_layout.setContentsMargins(0, 0, 0, 0)
        self.slog_badge_row_layout.setSpacing(6)
        placeholder = QLabel("Click 'Generate Badges' to render inline SVG badges.")
        placeholder.setStyleSheet("font-size: 9pt; color: #6B7280; border: none;")
        self.slog_badge_row_layout.addWidget(placeholder)
        badge_area_layout.addWidget(self.slog_badge_row)
        self.slog_badge_area.setVisible(False)
        layout.addWidget(self.slog_badge_area)

        # ── Store state ─────────────────────────────────────────
        self._slog_stats = None
        self._slog_badge_filepaths = []
        self._slog_badges_generated = False  # Becomes True after first manual 'Generate Badges'
        self._slog_auto_timer = QTimer(self)
        self._slog_auto_timer.setInterval(60_000)  # 60 seconds
        self._slog_auto_timer.timeout.connect(self._slog_refresh)
        self._slog_auto_timer.start()

        # Load on startup (deferred)
        QTimer.singleShot(200, self._slog_refresh)

        return tab

    # ── Internal helpers ─────────────────────────────────────────

    def _slog_card(self, label, value, color, parent_layout, attr_name):
        """Build a small stats card frame and add it to the given layout."""
        card = QFrame()
        card.setFixedHeight(70)
        card.setMinimumWidth(110)
        card.setStyleSheet(
            f"background-color: #1a1a2e; border: 1px solid {color}; border-radius: 6px;"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(8, 4, 8, 4)
        lbl = QLabel(label)
        lbl.setStyleSheet("font-size: 8pt; color: #9CA3AF; border: none;")
        card_layout.addWidget(lbl)
        val_lbl = QLabel(str(value))
        val_lbl.setStyleSheet(
            f"font-size: 16pt; font-weight: bold; color: {color}; border: none;"
        )
        val_lbl.setObjectName(attr_name + "_value")
        card_layout.addWidget(val_lbl)
        parent_layout.addWidget(card)

    def _slog_refresh(self):
        """Reload and refresh all stats from session_stats.py.
        Safe to call multiple times — skips if the tab widget is being destroyed.
        """
        if self._slog_auto_timer is None:
            return
        if not _load_session_stats():
            self.slog_status_label.setText("⚠️ session_stats.py not available")
            return

        try:
            sessions = []
            for fp in _find_files():
                d = _parse_yaml(fp)
                if d:
                    sessions.append({k: v for k, v in d.items() if not k.startswith("_")})

            if not sessions:
                self.slog_status_label.setText("No session files found")
                return

            self._slog_stats = _aggregate(sessions)
            self._slog_update_ui()
            self.slog_status_label.setText(
                f"✅ {self._slog_stats['sessions']} sessions · "
                f"{self._slog_stats['date_range'][0]} to {self._slog_stats['date_range'][1]}"
            )
            # Auto-regenerate badges if toggled on OR previously generated
            if self.slog_auto_badge_cb.isChecked() or self._slog_badges_generated:
                self._slog_generate_badges(silent=True)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
            self.slog_status_label.setText(f"⚠️ Refresh failed: {e}")
            logger.error(f"Session stats refresh failed: {e}")

    def _slog_generate_badges(self, silent=False):
        """Generate SVG badges and render them inline in the badge row.

        Args:
            silent: If True, don't update the status label (for auto-refresh).
        """
        if not self._slog_stats:
            self._slog_refresh()
            if not self._slog_stats:
                return

        if not _load_session_stats():
            return

        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            out_dir = os.path.join(root, "badges")
            badges = _generate_badges(self._slog_stats, out_dir)

            # Clear old badge labels
            self._slog_clear_badge_row()

            # Create a new QLabel per badge rendering the SVG inline
            for fn, fp in badges:
                lbl = QLabel()
                lbl.setMinimumWidth(20)
                lbl.setSizePolicy(
                    QSizePolicy.Policy.Fixed,
                    QSizePolicy.Policy.Fixed,
                )
                pixmap = self._slog_render_svg(fp, height=20)
                if pixmap and not pixmap.isNull():
                    lbl.setPixmap(pixmap)
                else:
                    fallback = fn.replace("badge_", "").replace(".svg", "").replace("_", " ")
                    lbl.setText(f"[{fallback}]")
                    lbl.setStyleSheet("font-size: 8pt; color: #9CA3AF; border: none;")
                lbl.setToolTip(f"Click to copy: {fp}")
                lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                fp_copy = fp  # capture in closure
                lbl.mousePressEvent = lambda e, path=fp_copy: self._slog_copy_badge_path(e, path)
                self.slog_badge_row_layout.addWidget(lbl)
                self._slog_badge_filepaths.append(fp)

            self._slog_badges_generated = True
            self.slog_badge_area.setVisible(True)

            if not silent:
                self.slog_status_label.setText(
                    f"🏅 {len(badges)} badges · {out_dir}"
                )
        except (OSError, ValueError, RuntimeError) as e:
            self._slog_clear_badge_row()
            err_lbl = QLabel(f"⚠️ Badge generation failed: {e}")
            err_lbl.setStyleSheet("font-size: 9pt; color: #F87171; border: none;")
            self.slog_badge_row_layout.addWidget(err_lbl)
            self.slog_badge_area.setVisible(True)
            logger.error(f"Badge generation failed: {e}")

    def _slog_clear_badge_row(self):
        """Remove all badge labels from the badge row."""
        self._slog_badge_filepaths = []
        while self.slog_badge_row_layout.count() > 0:
            item = self.slog_badge_row_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _slog_copy_badge_path(self, event, filepath):
        """Copy badge SVG file path to clipboard on click."""
        clipboard = QApplication.clipboard()
        clipboard.setText(filepath)
        self.slog_status_label.setText(f"📋 Copied: {filepath}")
        # Reset status after 3 seconds
        if self._slog_stats:
            QTimer.singleShot(3000, lambda: self.slog_status_label.setText(
                f"✅ {self._slog_stats['sessions']} sessions · "
                f"{self._slog_stats['date_range'][0]} to {self._slog_stats['date_range'][1]}"
            ))

    def _slog_render_svg(self, filepath, height):
        """Render an SVG file to a QPixmap for inline display."""
        try:
            renderer = QSvgRenderer(filepath)
            default_size = renderer.defaultSize()
            if default_size.isValid() and default_size.width() > 0:
                aspect = default_size.width() / default_size.height()
                width = int(height * aspect)
            else:
                width = height * 4  # estimated fallback
            pixmap = QPixmap(width, height)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            return pixmap
        except (OSError, RuntimeError, ValueError) as e:
            logger.debug(f"SVG render failed for {filepath}: {e}")
            return None

    def _slog_update_ui(self):
        """Update all UI elements from self._slog_stats."""
        stats = self._slog_stats
        if not stats:
            return

        # ── Update cards ────────────────────────────────────────
        for _name, key, attr_suffix in [
            ("sessions", "sessions", "slog_sessions_card_value"),
            ("hours", "hours", "slog_hours_card_value"),
            ("created", "created", "slog_created_card_value"),
            ("modified", "modified", "slog_modified_card_value"),
            ("changed", "changed", "slog_changed_card_value"),
        ]:
            val = stats.get(key, "—")
            if key == "hours" and isinstance(val, (int, float)):
                val = f"{val}h"
            lbl = self.findChild(QLabel, attr_suffix)
            if lbl:
                lbl.setText(str(val))

        # ── Per-session table ────────────────────────────────────
        self.slog_table.setSortingEnabled(False)
        self.slog_table.setRowCount(len(stats.get("session_list", [])))

        for i, s in enumerate(stats["session_list"]):
            sn = str(s.get("session", "?"))
            ds = str(s.get("date", ""))
            dur = str(s.get("duration", ""))
            cc, mm, _, _ = _count_files(s) if _count_files else (0, 0, [], [])
            focus = (s.get("focus") or "")[:60]

            self.slog_table.setItem(i, 0, QTableWidgetItem(sn))
            self.slog_table.setItem(i, 1, QTableWidgetItem(ds))
            self.slog_table.setItem(i, 2, QTableWidgetItem(dur))
            self.slog_table.setItem(i, 3, QTableWidgetItem(str(cc)))
            self.slog_table.setItem(i, 4, QTableWidgetItem(str(mm)))
            self.slog_table.setItem(i, 5, QTableWidgetItem(focus))

        self.slog_table.setSortingEnabled(True)

        # ── Sprint hours ─────────────────────────────────────────
        sprint_lines = []
        for sn in sorted(stats.get("sprint_hours", {})):
            label = f"Sprint {sn}" if sn > 0 else "Research/Planning"
            h = stats["sprint_hours"][sn]
            bar = "█" * int(h * 2)
            sprint_lines.append(f"  {label:<20} {h:>5.1f}h {bar}")
        self.slog_sprint_display.setPlainText("\n".join(sprint_lines) if sprint_lines else "(no sprint data)")

        # ── Tags ─────────────────────────────────────────────────
        tag_lines = []
        for t, c in list(stats.get("tag_frequency", {}).items())[:12]:
            tag_lines.append(f"  {t:<28} {c} session(s)")
        self.slog_tags_display.setPlainText("\n".join(tag_lines) if tag_lines else "(no tags)")

        # ── Files ────────────────────────────────────────────────
        file_lines = []
        for f in stats.get("created_files", []):
            file_lines.append(f"  + {f}")
        for f in stats.get("modified_files", []):
            file_lines.append(f"  ~ {f}")
        self.slog_files_display.setPlainText("\n".join(file_lines) if file_lines else "(no files)")

    def teardown_session_log(self) -> None:
        """Stop auto-refresh timer during application shutdown."""
        timer = getattr(self, "_slog_auto_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except (RuntimeError, AttributeError):
                pass

    def teardown(self) -> None:
        """Alias for teardown_session_log."""
        self.teardown_session_log()


