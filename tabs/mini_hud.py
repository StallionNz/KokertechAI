"""
tabs/mini_hud.py — Desktop mini-Kokertechai / Floating Command Palette.

Lightweight, frameless, spotlight-style HUD overlay providing:
  - Rapid AI prompts with streaming response & latency tracking
  - Safe shell command execution (prefixed with '!')
  - Plugin actions and discovery (prefixed with '/')
  - Memory vault search (prefixed with '?')
  - In-app and global hotkey invocation (Ctrl+Space / Ctrl+Alt+Space)
  - Zero-Trust Invariant compliance: PyQt6 GUI thread affinity,
    canonical layout pattern, high-DPI scaling, and explicit exception tuples.
"""
from __future__ import annotations

import math
import os
import subprocess
import threading
import time
from typing import Any, List, Optional

from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    QSettings,
    QThread,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QImageReader,
    QKeyEvent,
    QKeySequence,
    QLinearGradient,
    QMovie,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizeGrip,
    QSystemTrayIcon,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from config import CONFIG, WORKSPACE_DIR
from logging_config import get_logger

logger = get_logger(name="MiniHUD")

_HUD_STYLE = """
QDialog {
    background-color: transparent;
}
QFrame#hud_container {
    background-color: rgba(15, 23, 42, 0.97);
    border: 2px solid #3B82F6;
    border-radius: 12px;
}
QLabel#hud_title {
    font-size: 11pt;
    font-weight: bold;
    color: #60A5FA;
}
QLabel#hud_badge {
    background-color: #1E293B;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 8pt;
    color: #94A3B8;
}
QLabel#hud_mode_badge {
    background-color: #1E3A8A;
    border: 1px solid #3B82F6;
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 8pt;
    font-weight: bold;
    color: #93C5FD;
}
QPushButton#hud_btn_close {
    background-color: transparent;
    border: none;
    color: #94A3B8;
    font-size: 11pt;
    font-weight: bold;
    padding: 2px 6px;
    border-radius: 4px;
}
QPushButton#hud_btn_close:hover {
    background-color: #EF4444;
    color: #FFFFFF;
}
QLineEdit#hud_input {
    background-color: #1E293B;
    border: 1px solid #3B82F6;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 12pt;
    color: #F8FAFC;
    selection-background-color: #3B82F6;
}
QLineEdit#hud_input:focus {
    border: 2px solid #60A5FA;
    background-color: #0F172A;
}
QPushButton#hud_btn_run {
    background-color: #2563EB;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: bold;
    font-size: 9pt;
}
QPushButton#hud_btn_run:hover {
    background-color: #3B82F6;
}
QPushButton#hud_btn_stop {
    background-color: #DC2626;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: bold;
    font-size: 9pt;
}
QPushButton#hud_btn_stop:hover {
    background-color: #EF4444;
}
QPushButton.hud_chip {
    background-color: #1E293B;
    border: 1px solid #334155;
    border-radius: 11px;
    padding: 2px 10px;
    font-size: 8pt;
    color: #CBD5E1;
}
QPushButton.hud_chip:hover {
    background-color: #334155;
    border-color: #60A5FA;
    color: #FFFFFF;
}
QTextBrowser#hud_output {
    background-color: #0B0F19;
    border: 1px solid #1E293B;
    border-radius: 6px;
    color: #E2E8F0;
    font-family: 'Consolas', 'Cascadia Code', monospace;
    font-size: 9.5pt;
    padding: 8px;
}
QPushButton.hud_action_btn {
    background-color: #1E293B;
    border: 1px solid #334155;
    border-radius: 4px;
    color: #CBD5E1;
    padding: 3px 8px;
    font-size: 8.5pt;
}
QPushButton.hud_action_btn:hover {
    background-color: #334155;
    color: #FFFFFF;
}
QLabel#hud_status {
    color: #94A3B8;
    font-size: 8.5pt;
}
QLabel#hud_hints {
    color: #64748B;
    font-size: 8pt;
}
"""


class CommandWorker(QThread):
    """Executes a system shell command safely in a background thread."""

    output_signal = pyqtSignal(str, int)  # output_text, exit_code
    error_signal = pyqtSignal(str)

    def __init__(self, command: str, timeout: int = 15):
        super().__init__()
        self.command = command
        self.timeout = timeout

    def run(self):
        try:
            start = time.time()
            proc = subprocess.run(  # noqa: S602
                self.command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
            elapsed = time.time() - start
            out = proc.stdout
            err = proc.stderr
            combined = (out + ("\n" + err if err else "")).strip()
            if not combined:
                combined = f"(Command completed with exit code {proc.returncode} in {elapsed:.2f}s, no output)"
            self.output_signal.emit(combined, proc.returncode)
        except subprocess.TimeoutExpired:
            self.error_signal.emit(f"Command timed out after {self.timeout}s.")
        except (subprocess.SubprocessError, OSError, ValueError, RuntimeError, UnicodeDecodeError, TypeError, AttributeError) as e:
            self.error_signal.emit(f"Execution error: {e}")


class PluginWorker(QThread):
    """Executes a Kokertech plugin safely in a background thread."""

    result_signal = pyqtSignal(str, bool)  # result_text, is_success
    error_signal = pyqtSignal(str)

    def __init__(self, action: str, params: dict):
        super().__init__()
        self.action = action
        self.params = params

    def run(self):
        try:
            import plugin_registry
            reg = getattr(plugin_registry, "registry", None)
            if reg is None:
                self.error_signal.emit("Plugin registry not available.")
                return

            # Case-insensitive resolution
            target_cmd = self.action.upper()
            if target_cmd not in reg.plugins:
                matches = [k for k in reg.plugins if k.upper() == target_cmd]
                if matches:
                    target_cmd = matches[0]
                else:
                    avail = ", ".join(sorted(reg.plugins.keys())[:10])
                    self.error_signal.emit(
                        f"Plugin '{self.action}' not found. Available: {avail}..."
                    )
                    return

            intent = {"action": target_cmd}
            if self.params and isinstance(self.params, dict):
                intent.update(self.params)

            if hasattr(reg, "execute"):
                res = reg.execute(target_cmd, self.params)
            elif hasattr(reg, "execute_command"):
                res = reg.execute_command(intent)
            else:
                self.error_signal.emit(
                    f"Plugin registry has no execution method for '{target_cmd}'."
                )
                return

            success = not (isinstance(res, str) and res.startswith("[ERROR]"))
            self.result_signal.emit(str(res), success)
        except (RuntimeError, ValueError, KeyError, OSError, TypeError, AttributeError, IndexError, ImportError) as e:
            self.error_signal.emit(f"Plugin error: {e}")


class SearchWorker(QThread):
    """Searches memory vault in a background thread."""

    result_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self):
        try:
            import memory_vault
            start = time.time()
            hits = memory_vault.fts5_search(self.query, top_k=5)
            elapsed = (time.time() - start) * 1000

            if not hits:
                self.result_signal.emit(f"🔍 No memory vault matches found for '{self.query}'.")
                return

            lines = [f"🔍 Found {len(hits)} memory result(s) in {elapsed:.1f}ms:\n"]
            for idx, hit in enumerate(hits, 1):
                if isinstance(hit, dict):
                    category = hit.get("category", "memory")
                    summary = hit.get("summary", "")
                    snippet = hit.get("snippet", hit.get("content", ""))
                    ts = hit.get("timestamp", "")
                elif isinstance(hit, (tuple, list)):
                    # memory_vault.fts5_search returns: (rowid, source, content, score)
                    category = str(hit[1]) if len(hit) > 1 else "memory"
                    content = str(hit[2]) if len(hit) > 2 else ""
                    score = hit[3] if len(hit) > 3 else 0.0
                    summary = content[:80].strip()
                    snippet = content
                    ts = f"score: {float(score):.2f}" if isinstance(score, (int, float)) else str(score)
                else:
                    category = "memory"
                    summary = str(hit)[:80].strip()
                    snippet = str(hit)
                    ts = ""

                lines.append(f"[{idx}] [{category.upper()}] {summary} ({ts})")
                if snippet and snippet != summary:
                    lines.append(f"    {snippet[:200].strip()}\n")
            self.result_signal.emit("\n".join(lines))
        except (RuntimeError, ValueError, OSError, KeyError, AttributeError, TypeError, IndexError) as e:
            self.error_signal.emit(f"Search error: {e}")


class NeonCompanionWidget(QWidget):
    """Procedural neon-face mascot with a low-cost idle animation."""

    activated = pyqtSignal()
    context_requested = pyqtSignal(QPoint)
    roaming_changed = pyqtSignal(bool)

    STATES = ("idle", "working", "attention", "success")
    STATE_COLORS = {
        "idle": QColor(95, 255, 185),
        "working": QColor(79, 225, 255),
        "attention": QColor(255, 190, 75),
        "success": QColor(147, 255, 115),
    }
    STATE_LABELS = {
        "idle": "READY",
        "working": "WORKING",
        "attention": "ATTENTION",
        "success": "SUCCESS",
    }

    # 5 Atomic Orbital Rings tilted in 3D around the head (Euler tilt_z, tilt_x, radius, speed, particles)
    _ATOMIC_RINGS = (
        (-65, 55, 96.0, 1.4, 2),
        (-28, 62, 102.0, -1.2, 2),
        (8, 48, 93.0, 1.6, 2),
        (42, 64, 100.0, -1.3, 2),
        (78, 52, 97.0, 1.5, 2),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(220, 240)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._phase = 0.0
        self._paused = False
        self._state = "idle"
        self._pack_root = ""
        self._artwork_path = ""
        self._pixmap: Optional[QPixmap] = None
        self._movie: Optional[QMovie] = None
        self._roaming = False
        self._roam_velocity = QPointF(1.8, 1.2)
        self._press_global: Optional[QPoint] = None
        self._drag_offset: Optional[QPoint] = None
        self._dragged = False
        self.setMouseTracking(True)
        self._target_look = QPointF(0.0, 0.0)
        self._current_look = QPointF(0.0, 0.0)
        self._last_cursor_pos = (-1, -1)
        self._last_cursor_move_time = time.time()
        self._manual_gaze_until = 0.0

        # Precompute unit atomic orbital ring geometry
        self._precomputed_rings = []
        for tz_deg, tx_deg, r, spd, n_parts in self._ATOMIC_RINGS:
            tz = math.radians(tz_deg)
            tx = math.radians(tx_deg)
            back_pts = []
            for step in range(33):
                t = math.pi + (step / 32.0) * math.pi
                x1 = r * math.cos(t)
                y1 = r * math.sin(t) * math.cos(tx)
                px = x1 * math.cos(tz) - y1 * math.sin(tz)
                py = x1 * math.sin(tz) + y1 * math.cos(tz)
                back_pts.append((px, py))
            front_pts = []
            for step in range(33):
                t = (step / 32.0) * math.pi
                x1 = r * math.cos(t)
                y1 = r * math.sin(t) * math.cos(tx)
                px = x1 * math.cos(tz) - y1 * math.sin(tz)
                py = x1 * math.sin(tz) + y1 * math.cos(tz)
                front_pts.append((px, py))
            self._precomputed_rings.append({
                "r": r, "tx": tx, "tz": tz, "spd": spd, "n_parts": n_parts,
                "back_pts": back_pts, "front_pts": front_pts,
            })

        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(40)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._roam_timer = QTimer(self)
        self._roam_timer.setInterval(40)
        self._roam_timer.timeout.connect(self._advance_roaming)
        self.setToolTip("Kokertech Ai Hub — click to open, drag to move, right-click for options")

    @property
    def animation_state(self) -> str:
        return self._state

    @property
    def roaming(self) -> bool:
        return self._roaming

    @property
    def character_pack(self) -> str:
        return self._pack_root

    def set_animation_state(self, state: str):
        if state not in self.STATES:
            raise ValueError(f"Unknown companion animation state: {state}")
        self._state = state
        artwork_path = self._find_pack_animation(state)
        if artwork_path != self._artwork_path:
            if self._movie is not None:
                self._movie.stop()
                self._movie.deleteLater()
                self._movie = None
            self._pixmap = None
            self._artwork_path = artwork_path
            if artwork_path:
                reader = QImageReader(artwork_path)
                if reader.supportsAnimation():
                    movie = QMovie(artwork_path, parent=self)
                    movie.setCacheMode(QMovie.CacheMode.CacheAll)
                    if movie.isValid():
                        movie.frameChanged.connect(self.update)
                        self._movie = movie
                    else:
                        movie.deleteLater()
                if self._movie is None:
                    pixmap = QPixmap(artwork_path)
                    if not pixmap.isNull():
                        self._pixmap = pixmap
            if self._movie is not None and self.isVisible() and not self._paused:
                self._movie.start()
        elif self._movie is not None and self.isVisible() and not self._paused:
            self._movie.start()
        self.update()

    def set_character_pack(self, pack_root: Optional[str]) -> bool:
        """Load a folder containing state-named animated image files."""
        if not pack_root:
            self._pack_root = ""
            self._artwork_path = ""
            self._pixmap = None
            if self._movie is not None:
                self._movie.stop()
                self._movie.deleteLater()
                self._movie = None
            self.update()
            return True
        pack_root = os.path.abspath(os.path.expanduser(pack_root))
        if not os.path.isdir(pack_root):
            return False
        if not any(
            self._find_pack_animation(state, pack_root)
            for state in (*self.STATES, "mascot", "animation")
        ):
            return False
        self._pack_root = pack_root
        self.set_animation_state(self._state)
        return True

    def _find_pack_animation(self, state: str, pack_root: Optional[str] = None) -> str:
        root = pack_root if pack_root is not None else self._pack_root
        if not root or not os.path.isdir(root):
            return ""
        supported = sorted({
            bytes(fmt).decode("ascii", errors="ignore").lower()
            for fmt in QImageReader.supportedImageFormats()
        })
        try:
            files = {name.casefold(): name for name in os.listdir(root)}
        except OSError:
            return ""
        fallback_states = (state, "idle", "mascot", "animation")
        for stem in dict.fromkeys(fallback_states):
            for extension in supported:
                filename = files.get(f"{stem}.{extension}".casefold())
                if filename is None:
                    continue
                candidate = os.path.join(root, filename)
                if os.path.isfile(candidate) and QImageReader(candidate).canRead():
                    return candidate
        return ""

    def set_roaming(self, enabled: bool):
        self._roaming = bool(enabled)
        if self._roaming:
            if self._roam_velocity.isNull():
                self._roam_velocity = QPointF(1.8, 1.2)
            if self.isVisible() and self._press_global is None:
                self._roam_timer.start()
        else:
            self._roam_timer.stop()
        self.roaming_changed.emit(self._roaming)

    def _advance_roaming(self):
        if not self._roaming or not self.isVisible() or self._press_global is not None:
            return
        screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        min_x, min_y = area.x(), area.y()
        max_x = max(min_x, area.right() - self.width() + 1)
        max_y = max(min_y, area.bottom() - self.height() + 1)
        x = self.x() + self._roam_velocity.x()
        y = self.y() + self._roam_velocity.y()
        if x < min_x or x > max_x:
            self._roam_velocity.setX(-self._roam_velocity.x())
        if y < min_y or y > max_y:
            self._roam_velocity.setY(-self._roam_velocity.y())
        x = min(max(x, min_x), max_x)
        y = min(max(y, min_y), max_y)
        self.move(round(x), round(y))

    def _resume_roaming(self):
        if self._roaming and self.isVisible():
            self._roam_timer.start()

    def _advance_animation(self):
        self._phase = (self._phase + 0.045) % (math.tau * 100)
        self._update_cursor_gaze()
        self._current_look = self._current_look * 0.82 + self._target_look * 0.18
        self.update()

    def _update_cursor_gaze(self):
        """Update 3D gaze vector toward desktop cursor across the entire screen."""
        if self._press_global is not None:
            self._target_look = QPointF(0.0, 0.0)
            return

        if not self.isVisible() or self._paused:
            return

        now = time.time()
        if now < self._manual_gaze_until:
            return

        try:
            cursor_pos = QCursor.pos()
            center_global = self.mapToGlobal(QPoint(self.width() // 2, self.height() // 2))
            dx = float(cursor_pos.x() - center_global.x())
            dy = float(cursor_pos.y() - center_global.y())

            if (cursor_pos.x(), cursor_pos.y()) == self._last_cursor_pos:
                if now - self._last_cursor_move_time > 4.0:
                    self._target_look = QPointF(0.0, 0.0)
                    return
            else:
                self._last_cursor_pos = (cursor_pos.x(), cursor_pos.y())
                self._last_cursor_move_time = now

            norm_x = math.tanh(dx / 380.0)
            norm_y = math.tanh(dy / 280.0)
            self._target_look = QPointF(norm_x, norm_y)
        except (AttributeError, RuntimeError, TypeError, OSError):
            pass

    def set_animation_paused(self, paused: bool):
        self._paused = paused
        if paused:
            self._animation_timer.stop()
            if self._movie is not None:
                self._movie.stop()
        elif self.isVisible():
            self._animation_timer.start()
            if self._movie is not None:
                self._movie.start()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._paused:
            self._animation_timer.start()
            if self._movie is not None:
                self._movie.start()
        self._resume_roaming()

    def hideEvent(self, event):
        self._animation_timer.stop()
        self._roam_timer.stop()
        if self._movie is not None:
            self._movie.stop()
        super().hideEvent(event)

    def _draw_atomic_rings_back(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        accent: QColor,
        phase: float,
        speed_mult: float,
    ) -> None:
        """Render the back segment (z < 0) of the 5 atomic orbital rings and orbiting particles."""
        track_pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), 30), 1.0, Qt.PenStyle.DotLine)
        painter.setPen(track_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for ring in self._precomputed_rings:
            poly = QPolygonF([QPointF(cx + px, cy + py) for px, py in ring["back_pts"]])
            painter.drawPolyline(poly)

        painter.setPen(Qt.PenStyle.NoPen)
        for ring in self._precomputed_rings:
            spd = ring["spd"] * speed_mult
            r = ring["r"]
            tx = ring["tx"]
            tz = ring["tz"]
            n = ring["n_parts"]
            for p_idx in range(n):
                t = (phase * spd + p_idx * (math.tau / n)) % math.tau
                z = math.sin(t)
                if z < 0:
                    x1 = r * math.cos(t)
                    y1 = r * math.sin(t) * math.cos(tx)
                    px = cx + x1 * math.cos(tz) - y1 * math.sin(tz)
                    py = cy + x1 * math.sin(tz) + y1 * math.cos(tz)
                    alpha = int(35 + 45 * (z + 1.0))
                    size = 1.0 + (z + 1.0) * 0.7
                    painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), alpha))
                    painter.drawEllipse(QPointF(px, py), size, size)

    def _draw_atomic_rings_front(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        accent: QColor,
        phase: float,
        speed_mult: float,
    ) -> None:
        """Render the front segment (z >= 0) of the 5 atomic orbital rings and glowing particles."""
        track_pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), 55), 1.1, Qt.PenStyle.DotLine)
        painter.setPen(track_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for ring in self._precomputed_rings:
            poly = QPolygonF([QPointF(cx + px, cy + py) for px, py in ring["front_pts"]])
            painter.drawPolyline(poly)

        painter.setPen(Qt.PenStyle.NoPen)
        for ring in self._precomputed_rings:
            spd = ring["spd"] * speed_mult
            r = ring["r"]
            tx = ring["tx"]
            tz = ring["tz"]
            n = ring["n_parts"]
            for p_idx in range(n):
                t = (phase * spd + p_idx * (math.tau / n)) % math.tau
                z = math.sin(t)
                if z >= 0:
                    x1 = r * math.cos(t)
                    y1 = r * math.sin(t) * math.cos(tx)
                    px = cx + x1 * math.cos(tz) - y1 * math.sin(tz)
                    py = cy + x1 * math.sin(tz) + y1 * math.cos(tz)
                    alpha = int(120 + 135 * z)
                    size = 1.4 + z * 1.5
                    # Outer energy glow halo
                    painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), int(40 * z)))
                    painter.drawEllipse(QPointF(px, py), size + 2.0, size + 2.0)
                    # Luminous particle core
                    painter.setBrush(QColor(235, 255, 250, alpha))
                    painter.drawEllipse(QPointF(px, py), size, size)

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width() / 240.0, self.height() / 270.0)
        painter.translate((self.width() - 240 * scale) / 2, (self.height() - 270 * scale) / 2)
        painter.scale(scale, scale)

        phase = self._phase
        pulse = (math.sin(phase * (2.0 if self._state == "working" else 1.4)) + 1.0) / 2.0
        bob = math.sin(phase * (1.35 if self._state == "attention" else 1.0)) * (3.5 if self._state == "success" else 2.0)
        painter.translate(0, bob)

        accent = self.STATE_COLORS[self._state]
        look = self._current_look
        shift_x = look.x() * 9.0
        shift_y = look.y() * 6.0
        tilt = look.x() * 7.5 + math.sin(phase * 0.7) * 1.5
        breath = 1.0 + 0.016 * math.sin(phase * (2.2 if self._state == "working" else 1.2))

        pixmap = self._movie.currentPixmap() if self._movie is not None and self._movie.isValid() else self._pixmap
        if pixmap is not None and not pixmap.isNull():
            # 1. Volumetric dynamic aura behind 3D character
            aura = QRadialGradient(120 + shift_x, 115 + shift_y, 110)
            aura.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 32 + int(24 * pulse)))
            aura.setColorAt(0.65, QColor(accent.red(), accent.green(), accent.blue(), 10))
            aura.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(aura)
            painter.drawEllipse(12, 10, 216, 216)

            # 2. 3D Atomic orbital rings & particles (back layer: z < 0)
            speed_mult = 1.8 if self._state == "working" else 1.0
            self._draw_atomic_rings_back(painter, 120 + shift_x, 115 + shift_y, accent, phase, speed_mult)

            # 3. 3D Animated Character Sprite with breathing, tilt, and perspective steering
            painter.save()
            painter.translate(120 + shift_x, 118 + shift_y)
            painter.rotate(tilt)
            painter.scale(breath, breath)
            painter.shear(-look.x() * 0.04, 0.0)
            painter.translate(-120, -118)

            target = QRectF(18, 12, 204, 222)
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))

            # Dynamic 3D Cybernetic Gaze Pupils clipped inside true anatomical eye sockets
            eye_dx = look.x() * 4.5
            eye_dy = look.y() * 3.2
            for eye_cx, eye_cy in ((95.0, 109.0), (144.5, 109.0)):
                socket_path = QPainterPath()
                socket_path.addEllipse(QRectF(eye_cx - 11, eye_cy - 7.5, 22, 15))
                painter.save()
                painter.setClipPath(socket_path)

                base_grad = QRadialGradient(eye_cx, eye_cy, 12)
                base_grad.setColorAt(0.0, QColor(10, 15, 25, 230))
                base_grad.setColorAt(0.85, QColor(5, 8, 15, 250))
                base_grad.setColorAt(1.0, QColor(0, 0, 0, 255))
                painter.setBrush(base_grad)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QRectF(eye_cx - 11, eye_cy - 7.5, 22, 15))

                pupil_x = eye_cx + eye_dx
                pupil_y = eye_cy + eye_dy
                pupil_grad = QRadialGradient(pupil_x, pupil_y, 7.5)
                pupil_grad.setColorAt(0.0, QColor(255, 255, 255, 255))
                pupil_grad.setColorAt(0.35, QColor(accent.red(), accent.green(), accent.blue(), 235))
                pupil_grad.setColorAt(0.8, QColor(accent.red(), accent.green(), accent.blue(), 80))
                pupil_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
                painter.setBrush(pupil_grad)
                painter.drawEllipse(QPointF(pupil_x, pupil_y), 6.5, 5.0)

                painter.setBrush(QColor(255, 255, 255, 240))
                painter.drawEllipse(QPointF(pupil_x - 1.8, pupil_y - 1.4), 1.8, 1.3)
                painter.restore()

            # 4. Animated Visor Specular Glint (sweeps across 3D glass visor & follows gaze)
            glint_phase = (phase * 0.5) % 6.0
            gx = 95 + look.x() * 28.0
            gy = 100 + look.y() * 12.0
            if glint_phase < 1.0:
                sweep_offset = (glint_phase / 1.0 - 0.5) * 80.0
                gx += sweep_offset
            glint = QLinearGradient(gx - 25, gy - 35, gx + 25, gy + 35)
            glint.setColorAt(0.0, QColor(255, 255, 255, 0))
            glint.setColorAt(0.5, QColor(255, 255, 255, 65 + int(30 * pulse)))
            glint.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(glint)
            painter.drawRoundedRect(QRectF(55, 75, 130, 65), 18, 18)

            painter.restore()

            # 5. 3D Atomic orbital rings & particles (front layer: z >= 0)
            self._draw_atomic_rings_front(painter, 120 + shift_x, 115 + shift_y, accent, phase, speed_mult)

            # 6. State Badge
            painter.setPen(accent)
            painter.setFont(QFont("Consolas", 8, QFont.Weight.DemiBold))
            painter.drawText(QRectF(43, 242, 154, 18), Qt.AlignmentFlag.AlignCenter,
                             f"KOKERTECH  /  {self.STATE_LABELS[self._state]}")
            return

        # ── Procedural 3D Volumetric Fallback ─────────────────────────────────
        # 1. Volumetric 3D Aura
        aura = QRadialGradient(120 + shift_x, 115 + shift_y, 110)
        aura.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 35 + int(20 * pulse)))
        aura.setColorAt(0.65, QColor(18, 193, 220, 12))
        aura.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(aura)
        painter.drawEllipse(10, 8, 220, 225)

        # 2. 3D Atomic orbital rings & particles (Back Layer: z < 0)
        speed_mult = 1.8 if self._state == "working" else 1.0
        self._draw_atomic_rings_back(painter, 120 + shift_x, 115 + shift_y, accent, phase, speed_mult)

        painter.save()
        painter.translate(120 + shift_x, 118 + shift_y)
        painter.rotate(tilt)
        painter.scale(breath, breath)
        painter.translate(-120, -118)

        # 3. 3D Head Shell Path
        head = QPainterPath()
        head.moveTo(120, 24)
        head.cubicTo(165, 24, 194, 52, 195, 93)
        head.cubicTo(207, 120, 197, 168, 178, 196)
        head.cubicTo(163, 220, 144, 233, 120, 236)
        head.cubicTo(96, 233, 77, 220, 62, 196)
        head.cubicTo(43, 168, 33, 120, 45, 93)
        head.cubicTo(46, 52, 75, 24, 120, 24)
        head.closeSubpath()

        # 4. Volumetric 3D Shading inside head (ceramic/glass sphere)
        head_grad = QRadialGradient(105, 80, 120)
        head_grad.setColorAt(0.0, QColor(24, 48, 54, 220))
        head_grad.setColorAt(0.55, QColor(10, 22, 28, 235))
        head_grad.setColorAt(0.85, QColor(5, 12, 16, 245))
        head_grad.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 75))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(head_grad)
        painter.drawPath(head)

        # 5. 3D Spherical Point Cloud Matrix
        painter.save()
        painter.setClipPath(head)
        for row in range(15):
            norm_y = (row - 7) / 7.0
            y = 52 + row * 10
            cols = 13 - int(abs(norm_y) * 4)
            for c in range(cols):
                norm_x = (c - cols / 2.0) / (cols / 2.0)
                r_sq = norm_x**2 + norm_y**2
                if r_sq < 1.0:
                    z_sph = math.sqrt(1.0 - r_sq)
                    x = 120 + norm_x * (cols * 5.2) * (0.85 + z_sph * 0.15)
                    shimmer = (math.sin(phase * 1.8 + row * 0.6 + c * 0.4) + 1.0) / 2.0
                    alpha = int(25 + z_sph * 55 + shimmer * 75)
                    pt_r = 0.7 + z_sph * 0.8
                    painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), alpha))
                    painter.drawEllipse(QPointF(x, y), pt_r, pt_r)
        painter.restore()

        # 6. Sculpted 3D Visor / Glass Eye Plate
        visor = QPainterPath()
        visor.moveTo(60, 88)
        visor.cubicTo(70, 78, 105, 82, 120, 88)
        visor.cubicTo(135, 82, 170, 78, 180, 88)
        visor.cubicTo(192, 98, 188, 138, 172, 146)
        visor.cubicTo(158, 153, 142, 132, 120, 130)
        visor.cubicTo(98, 132, 82, 153, 68, 146)
        visor.cubicTo(52, 138, 48, 98, 60, 88)
        visor.closeSubpath()

        visor_grad = QLinearGradient(60, 80, 180, 150)
        visor_grad.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 45))
        visor_grad.setColorAt(0.5, QColor(10, 35, 42, 140))
        visor_grad.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 25))
        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 140), 1.2))
        painter.setBrush(visor_grad)
        painter.drawPath(visor)

        # 7. Animated Specular Visor Gleam
        gleam_phase = (phase * 0.6) % 5.0
        if gleam_phase < 1.2:
            gx = 40 + (gleam_phase / 1.2) * 160
            painter.save()
            painter.setClipPath(visor)
            gleam = QLinearGradient(gx - 20, 70, gx + 20, 150)
            gleam.setColorAt(0.0, QColor(255, 255, 255, 0))
            gleam.setColorAt(0.5, QColor(255, 255, 255, 90))
            gleam.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(gleam)
            painter.drawRect(40, 70, 160, 90)
            painter.restore()

        # 8. Cybernetic Circuit Traces (3D layered)
        traces = QPainterPath()
        traces.moveTo(68, 88); traces.lineTo(82, 88); traces.lineTo(92, 102); traces.lineTo(92, 128); traces.lineTo(84, 146)
        traces.moveTo(172, 88); traces.lineTo(158, 88); traces.lineTo(148, 102); traces.lineTo(148, 128); traces.lineTo(156, 146)
        traces.moveTo(106, 216); traces.lineTo(120, 222); traces.lineTo(134, 216)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 185), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPath(traces)

        # Solder nodes
        painter.setBrush(QColor(230, 255, 245, 220))
        for pt in [(92, 102), (92, 128), (84, 146), (148, 102), (148, 128), (156, 146), (120, 222)]:
            painter.drawEllipse(QPointF(pt[0], pt[1]), 1.8, 1.8)

        # 9. Realistic 3D Cybernetic Eyes
        blink_phase = phase % 5.5
        eye_open = 0.08 if blink_phase < 0.22 else 1.0
        eye_h = 16.0 * eye_open
        gaze_x = look.x() * 6.5 + math.sin(phase * 0.5) * 1.2
        gaze_y = look.y() * 4.5

        for eye_cx in (88, 152):
            socket = QRadialGradient(eye_cx, 114, 18)
            socket.setColorAt(0.0, QColor(5, 18, 22, 240))
            socket.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 120))
            painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 190), 1.2))
            painter.setBrush(socket)
            painter.drawEllipse(QRectF(eye_cx - 19, 114 - eye_h / 2, 38, eye_h))

            if eye_open > 0.3:
                iris = QRadialGradient(eye_cx + gaze_x, 114 + gaze_y, 11)
                iris.setColorAt(0.0, QColor(240, 255, 250, 245))
                iris.setColorAt(0.4, QColor(accent.red(), accent.green(), accent.blue(), 230))
                iris.setColorAt(0.85, QColor(10, 160, 180, 200))
                iris.setColorAt(1.0, QColor(5, 20, 25, 240))
                painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 220), 1.0))
                painter.setBrush(iris)
                painter.drawEllipse(QRectF(eye_cx + gaze_x - 10, 114 + gaze_y - 10 * eye_open, 20, 20 * eye_open))

                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(230, 255, 245, 180), 0.8, Qt.PenStyle.DashLine))
                painter.drawEllipse(QRectF(eye_cx + gaze_x - 6.5, 114 + gaze_y - 6.5 * eye_open, 13, 13 * eye_open))

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 255, 255, 230))
                painter.drawEllipse(QRectF(eye_cx + gaze_x - 4, 110 + gaze_y, 3.5, 3.5 * eye_open))
                painter.setBrush(QColor(255, 255, 255, 140))
                painter.drawEllipse(QRectF(eye_cx + gaze_x + 2, 116 + gaze_y, 1.8, 1.8 * eye_open))

        # 10. 3D Sculpted Nose
        nose = QPainterPath()
        nose.moveTo(120, 126); nose.lineTo(116, 154); nose.cubicTo(117, 158, 123, 158, 124, 154); nose.lineTo(120, 126)
        painter.setBrush(QColor(accent.red(), accent.green(), accent.blue(), 28))
        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 140), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(nose)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(230, 255, 245, 160))
        painter.drawEllipse(QPointF(120, 154), 1.8, 1.4)

        # 11. 3D Sculpted Cyber-Lips
        lips = QPainterPath()
        lips.moveTo(102, 180)
        lips.cubicTo(112, 178, 116, 182, 120, 182)
        lips.cubicTo(124, 182, 128, 178, 138, 180)
        lips.cubicTo(128, 192, 112, 192, 102, 180)
        lips_grad = QLinearGradient(120, 178, 120, 192)
        lips_grad.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 90))
        lips_grad.setColorAt(0.6, QColor(15, 38, 45, 180))
        lips_grad.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 40))
        painter.setBrush(lips_grad)
        painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 180), 1.2))
        painter.drawPath(lips)

        # 12. Head Shell Outer 3D Rim Highlights (Fresnel edge glow)
        for width, alpha in ((14, 16), (7, 35), (2.2, 220)):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), alpha), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(head)

        painter.restore()

        # 13. 3D Atomic orbital rings & particles (Front Layer: z >= 0)
        self._draw_atomic_rings_front(painter, 120 + shift_x, 115 + shift_y, accent, phase, speed_mult)

        # 14. Badge
        painter.setPen(QColor(accent.red(), accent.green(), accent.blue(), 215))
        painter.setFont(QFont("Consolas", 8, QFont.Weight.DemiBold))
        painter.drawText(QRectF(43, 242, 154, 18), Qt.AlignmentFlag.AlignCenter,
                         f"KOKERTECH  /  {self.STATE_LABELS[self._state]}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._roam_timer.stop()
            self._press_global = event.globalPosition().toPoint()
            self._drag_offset = self._press_global - self.window().frameGeometry().topLeft()
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_global is not None and event.buttons() & Qt.MouseButton.LeftButton:
            global_pos = event.globalPosition().toPoint()
            if (global_pos - self._press_global).manhattanLength() >= QApplication.startDragDistance():
                self._dragged = True
                self._roam_timer.stop()
                self.window().move(global_pos - self._drag_offset)
            event.accept()
            return
        if hasattr(event, "position") and callable(event.position):
            pos = event.position()
            cx = self.width() / 2.0
            cy = self.height() / 2.0
            norm_x = max(-1.0, min(1.0, (pos.x() - cx) / max(cx, 1.0)))
            norm_y = max(-1.0, min(1.0, (pos.y() - cy) / max(cy, 1.0)))
            self._target_look = QPointF(norm_x, norm_y)
            self._manual_gaze_until = time.time() + 0.8
        try:
            super().mouseMoveEvent(event)
        except (AttributeError, TypeError):
            pass

    def leaveEvent(self, event):
        self._manual_gaze_until = 0.0
        self._target_look = QPointF(0.0, 0.0)
        try:
            super().leaveEvent(event)
        except (AttributeError, TypeError):
            pass

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press_global is not None:
            if not self._dragged:
                self.activated.emit()
            self._press_global = None
            self._drag_offset = None
            self._resume_roaming()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.context_requested.emit(event.globalPos())
        event.accept()


class MiniHudDialog(QDialog):
    """Desktop Mini-Jarvis HUD / Floating Command Palette overlay.

    Non-modal floating spotlight window for instantaneous AI prompts,
    quick shell commands, plugin triggering, and memory search without
    disrupting main desktop activities.

    Signals:
        send_to_main_requested(str): Emitted when prompt or result is sent
            to the main window chat input.
        action_completed(str, str): Emitted on completion with (mode, result).
        hud_closed(): Emitted when HUD closes or hides.
    """

    send_to_main_requested = pyqtSignal(str)
    action_completed = pyqtSignal(str, str)
    hud_closed = pyqtSignal()

    MODE_AI = "ai"
    MODE_CMD = "cmd"
    MODE_PLUGIN = "plugin"
    MODE_SEARCH = "search"

    def __init__(self, parent: Optional[QWidget] = None, controller: Optional[Any] = None):
        super().__init__(None)
        self._parent = parent
        self._controller = controller
        self._active_worker: Optional[QThread] = None
        self._cancel_event = threading.Event()
        self._history: List[str] = []
        self._history_index: int = -1
        self._last_mode: str = self.MODE_AI
        self._start_time: float = 0.0
        self._initial_positioned = False
        self._drag_offset: Optional[QPoint] = None
        self._click_through = False
        self._companion_only = False
        self._closing = False
        self._tray: Optional[QSystemTrayIcon] = None
        self._tray_menu: Optional[QMenu] = None
        self._tray_companion_action: Optional[QAction] = None
        self._tray_click_through_action: Optional[QAction] = None
        self._tray_roaming_action: Optional[QAction] = None
        self._companion_settings = QSettings("KokertechAI", "Kokertech Ai Hub")
        self._state_reset_timer = QTimer(self)
        self._state_reset_timer.setSingleShot(True)
        self._state_reset_timer.timeout.connect(self._restore_idle_state)
        self._pending_state_reset_ms = 0
        self.companion: Optional[NeonCompanionWidget] = None

        self._setup_window_properties()
        self._setup_ui()
        self._setup_shortcuts()
        self._setup_companion()
        self._setup_tray_controls()
        self._update_mode_indicator("")

    def _setup_window_properties(self):
        """Configure a resizable, frameless floating companion window."""
        self.setWindowTitle("Kokertech Ai Hub")
        self.setMinimumSize(220, 240)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(False)
        self.setStyleSheet(_HUD_STYLE)

    def _setup_ui(self):
        """Build canonical UI layout conforming to PyQt6 canary invariant."""
        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(0)

        # Outer rounded container frame
        self.container_frame = QFrame()
        self.container_frame.setObjectName("hud_container")
        self.container_frame.setMinimumSize(480, 300)
        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(14, 12, 14, 12)
        container_layout.setSpacing(10)
        self.container_frame.setLayout(container_layout)
        root_layout.addWidget(self.container_frame)

        # ── 1. Header Bar ──
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        self.title_label = QLabel("[-K-] ♉ Kokertech Ai Hub")
        self.title_label.setObjectName("hud_title")
        self.title_label.setToolTip("Drag here to move the Hub")
        header_layout.addWidget(self.title_label)

        self.mode_badge = QLabel("💬 Ask AI")
        self.mode_badge.setObjectName("hud_mode_badge")
        header_layout.addWidget(self.mode_badge)

        active_provider = CONFIG.get("active_provider", "local_llm")
        self.provider_badge = QLabel(f"🟢 {active_provider}")
        self.provider_badge.setObjectName("hud_badge")
        header_layout.addWidget(self.provider_badge)

        header_layout.addStretch(1)

        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("hud_btn_close")
        self.btn_close.setToolTip("Close HUD (Esc)")
        self.btn_close.clicked.connect(self.hide_hud)
        header_layout.addWidget(self.btn_close)

        self.header_bar = QWidget()
        self.header_bar.setLayout(header_layout)
        container_layout.addWidget(self.header_bar)
        self._drag_widgets = [self.header_bar, self.title_label, self.mode_badge, self.provider_badge]
        for drag_widget in self._drag_widgets:
            drag_widget.installEventFilter(self)

        # ── 2. Input Row ──
        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)

        self.prompt_icon = QLabel("⚡")
        self.prompt_icon.setStyleSheet("font-size: 13pt; color: #60A5FA;")
        input_layout.addWidget(self.prompt_icon)

        self.input_edit = QLineEdit()
        self.input_edit.setObjectName("hud_input")
        self.input_edit.setPlaceholderText(
            "Ask Jarvis, !command, /plugin, ?search... (↵ Run, Esc Close, Ctrl+↵ Main)"
        )
        self.input_edit.setClearButtonEnabled(True)
        self.input_edit.textChanged.connect(self._on_input_text_changed)
        self.input_edit.returnPressed.connect(self.submit)
        input_layout.addWidget(self.input_edit, stretch=1)

        self.btn_run = QPushButton("▶ Run")
        self.btn_run.setObjectName("hud_btn_run")
        self.btn_run.setToolTip("Execute prompt or command (Enter)")
        self.btn_run.clicked.connect(self.submit)
        input_layout.addWidget(self.btn_run)

        self.btn_stop = QPushButton("⏹ Stop")
        self.btn_stop.setObjectName("hud_btn_stop")
        self.btn_stop.setToolTip("Cancel execution")
        self.btn_stop.clicked.connect(self.stop_execution)
        self.btn_stop.hide()
        input_layout.addWidget(self.btn_stop)

        container_layout.addLayout(input_layout)

        # ── 3. Quick Action Chips Row ──
        chips_layout = QHBoxLayout()
        chips_layout.setSpacing(6)

        chip_ai = QPushButton("💬 AI")
        chip_ai.setProperty("class", "hud_chip")
        chip_ai.clicked.connect(lambda: self._set_input_prefix(""))
        chips_layout.addWidget(chip_ai)

        chip_cmd = QPushButton("! Shell")
        chip_cmd.setProperty("class", "hud_chip")
        chip_cmd.clicked.connect(lambda: self._set_input_prefix("!"))
        chips_layout.addWidget(chip_cmd)

        chip_plugin = QPushButton("/ Plugins")
        chip_plugin.setProperty("class", "hud_chip")
        chip_plugin.clicked.connect(lambda: self._set_input_prefix("/"))
        chips_layout.addWidget(chip_plugin)

        chip_search = QPushButton("? Vault")
        chip_search.setProperty("class", "hud_chip")
        chip_search.clicked.connect(lambda: self._set_input_prefix("?"))
        chips_layout.addWidget(chip_search)

        chip_sys = QPushButton("🖥️ SysStatus")
        chip_sys.setProperty("class", "hud_chip")
        chip_sys.clicked.connect(lambda: self._quick_trigger("/system_status"))
        chips_layout.addWidget(chip_sys)

        chip_voice = QPushButton("🎙️ Voice")
        chip_voice.setProperty("class", "hud_chip")
        chip_voice.setToolTip("Start voice dictation (hands-free VAD)")
        chip_voice.clicked.connect(self._toggle_voice_dictation)
        chips_layout.addWidget(chip_voice)

        chips_layout.addStretch(1)
        container_layout.addLayout(chips_layout)

        # ── 4. Output Display Area ──
        self.output_view = QTextBrowser()
        self.output_view.setObjectName("hud_output")
        self.output_view.setOpenExternalLinks(False)
        self.output_view.setMinimumHeight(140)
        self.output_view.setMaximumHeight(360)
        self.output_view.setPlaceholderText("Jarvis output and responses appear here...")
        container_layout.addWidget(self.output_view)

        # ── 5. Footer & Status Bar ──
        footer_layout = QHBoxLayout()
        footer_layout.setSpacing(8)

        self.status_label = QLabel("⚡ Ready")
        self.status_label.setObjectName("hud_status")
        footer_layout.addWidget(self.status_label)

        footer_layout.addStretch(1)

        self.btn_speak = QPushButton("🔊 Speak")
        self.btn_speak.setProperty("class", "hud_action_btn")
        self.btn_speak.setToolTip("Read response aloud via Piper TTS")
        self.btn_speak.clicked.connect(self._speak_output)
        footer_layout.addWidget(self.btn_speak)

        self.btn_copy = QPushButton("📋 Copy")
        self.btn_copy.setProperty("class", "hud_action_btn")
        self.btn_copy.clicked.connect(self._copy_output)
        footer_layout.addWidget(self.btn_copy)

        self.btn_send_main = QPushButton("↗ Send to Main")
        self.btn_send_main.setProperty("class", "hud_action_btn")
        self.btn_send_main.setToolTip("Send content to main chat input (Ctrl+Enter)")
        self.btn_send_main.clicked.connect(self._send_to_main)
        footer_layout.addWidget(self.btn_send_main)

        self.btn_clear = QPushButton("🗑️ Clear")
        self.btn_clear.setProperty("class", "hud_action_btn")
        self.btn_clear.clicked.connect(self.clear)
        footer_layout.addWidget(self.btn_clear)

        self.hints_label = QLabel("↵ Run | Esc Close | Ctrl+↵ Main")
        self.hints_label.setObjectName("hud_hints")
        footer_layout.addWidget(self.hints_label)

        self.resize_grip = QSizeGrip(self)
        self.resize_grip.setToolTip("Drag to resize the Hub")
        footer_layout.addWidget(self.resize_grip, alignment=Qt.AlignmentFlag.AlignBottom)

        container_layout.addLayout(footer_layout)

        # Canonical binding invariant
        self.setLayout(root_layout)

        # Zero-Trust Dialog Invariant: In QDialog, push buttons default to autoDefault=True,
        # which causes Enter/Return key presses to trigger the first button (btn_close)
        # or call accept(), dismissing the HUD. Explicitly disable autoDefault and default.
        for btn in self.findChildren(QPushButton):
            btn.setAutoDefault(False)
            btn.setDefault(False)

    def _setup_companion(self):
        """Create the independent transparent mascot window."""
        self.companion = NeonCompanionWidget()
        self.companion.setWindowTitle("Kokertech Ai Hub Companion")
        self.companion.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.companion.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.companion.resize(260, 292)
        self.companion.activated.connect(self._open_from_companion)
        self.companion.context_requested.connect(self._show_companion_menu)
        self.companion.roaming_changed.connect(self._sync_roaming_action)
        self.companion.destroyed.connect(self._on_companion_destroyed)
        self._load_companion_preferences()

    def _on_companion_destroyed(self, *_args):
        self.companion = None
        self._state_reset_timer.stop()
        self._pending_state_reset_ms = 0

    def _setup_tray_controls(self):
        """Add tray recovery controls when the desktop provides a system tray."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "kokertech_icon.png",
        )
        icon = QIcon(icon_path) if os.path.isfile(icon_path) else QApplication.windowIcon()
        if icon.isNull():
            pixmap = QPixmap(32, 32)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(92, 255, 190), 3))
            painter.setBrush(QColor(7, 24, 34, 235))
            painter.drawEllipse(3, 3, 26, 26)
            painter.setPen(QPen(QColor(133, 255, 210), 2))
            painter.drawLine(10, 13, 13, 13)
            painter.drawLine(19, 13, 22, 13)
            painter.drawArc(10, 15, 12, 9, 200 * 16, 140 * 16)
            painter.end()
            icon = QIcon(pixmap)
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("Kokertech Ai Hub — click to show controls")
        self._tray.activated.connect(self._on_tray_activated)
        self._tray_menu = QMenu()
        show_action = QAction("Show Kokertech Ai Hub", self._tray_menu)
        show_action.triggered.connect(self.show_hud)
        self._tray_menu.addAction(show_action)
        self._tray_click_through_action = self._create_click_through_action(self._tray_menu)
        self._tray_menu.addAction(self._tray_click_through_action)
        self._tray_companion_action = self._create_companion_mode_action(self._tray_menu)
        self._tray_menu.addAction(self._tray_companion_action)
        self._tray_roaming_action = self._create_roaming_action(self._tray_menu)
        self._tray_menu.addAction(self._tray_roaming_action)
        self._create_character_pack_menu(self._tray_menu)
        pause_action = QAction("Pause mascot animation", self._tray_menu)
        pause_action.setCheckable(True)
        pause_action.toggled.connect(self._set_animation_paused)
        self._tray_menu.addAction(pause_action)
        self._tray_menu.addSeparator()
        exit_action = QAction("Exit Kokertech Ai Hub", self._tray_menu)
        exit_action.triggered.connect(QApplication.quit)
        self._tray_menu.addAction(exit_action)
        self._tray.setContextMenu(self._tray_menu)
        self._tray.show()

    def _sync_companion_action(self, enabled: bool):
        if self._tray_companion_action is not None:
            was_blocked = self._tray_companion_action.blockSignals(True)
            self._tray_companion_action.setChecked(enabled)
            self._tray_companion_action.blockSignals(was_blocked)

    def _create_click_through_action(self, parent):
        action = QAction("Click-through (Ctrl+Alt+Shift+X)", parent)
        action.setCheckable(True)
        action.toggled.connect(self.set_click_through)
        return action

    def _create_roaming_action(self, parent):
        action = QAction("Roam within screen", parent)
        action.setCheckable(True)
        action.setChecked(self.companion.roaming if self.companion is not None else False)
        action.toggled.connect(self._set_roaming)
        return action

    def _create_character_pack_menu(self, parent):
        menu = QMenu("Character artwork", parent)
        if parent is not None:
            parent.addMenu(menu)
        choose_action = QAction("Choose character pack folder…", menu)
        choose_action.setToolTip(
            "Folder of Qt-supported animated or still images named idle, working, "
            "attention, or success; a shared idle, mascot, or animation image is also supported."
        )
        choose_action.triggered.connect(self._choose_character_pack)
        menu.addAction(choose_action)
        reset_action = QAction("Use built-in neon mascot", menu)
        reset_action.triggered.connect(self._reset_character_pack)
        menu.addAction(reset_action)
        return menu

    def _reset_character_pack(self):
        if self.companion is not None:
            self.companion.set_character_pack(None)
            self._companion_settings.setValue("companion/pack_root", "builtin")
            self._set_companion_state(self.companion.animation_state)

    def _load_companion_preferences(self):
        if self.companion is None:
            return
        pack_root = self._companion_settings.value("companion/pack_root", "", type=str)
        if pack_root == "builtin":
            self.companion.set_character_pack(None)
        elif pack_root:
            if not self.companion.set_character_pack(pack_root):
                self._companion_settings.remove("companion/pack_root")
        else:
            default_pack = os.path.join(WORKSPACE_DIR, "assets", "mascot")
            if os.path.isdir(default_pack):
                self.companion.set_character_pack(default_pack)
        self.companion.set_roaming(
            self._companion_settings.value("companion/roaming", False, type=bool)
        )

    def _choose_character_pack(self):
        start_dir = self.companion.character_pack if self.companion.character_pack else os.path.expanduser("~")
        dialog_parent = self if self.isVisible() else (
            self.companion if self.companion is not None and self.companion.isVisible() else None
        )
        folder = QFileDialog.getExistingDirectory(
            dialog_parent,
            "Choose a Kokertech Ai Hub character pack",
            start_dir,
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder and self.companion is not None:
            if self.companion.set_character_pack(folder):
                self._companion_settings.setValue("companion/pack_root", self.companion.character_pack)
                self._set_companion_state(self.companion.animation_state)
                self.status_label.setText(f"Mascot pack: {os.path.basename(folder)}")
            else:
                self.status_label.setText(
                    "Pack not loaded — add Qt-supported idle/working/attention/success images, "
                    "or one shared idle, mascot, or animation image."
                )
                self.show_hud()

    def _set_roaming(self, enabled: bool):
        if self.companion is not None:
            self.companion.set_roaming(enabled)
            self._companion_settings.setValue("companion/roaming", enabled)

    def _sync_roaming_action(self, enabled: bool):
        if self._tray_roaming_action is not None:
            blocked = self._tray_roaming_action.blockSignals(True)
            self._tray_roaming_action.setChecked(enabled)
            self._tray_roaming_action.blockSignals(blocked)

    # ── Public preference API for external panels (Settings tab) ──
    # The Settings panel drives the same code paths as the tray/context
    # menus, so both surfaces stay in sync through roaming_changed and
    # _sync_roaming_action.

    def companion_roaming(self) -> bool:
        """Current companion roaming preference (False without a companion)."""
        return self.companion.roaming if self.companion is not None else False

    def set_companion_roaming(self, enabled: bool):
        """Enable/disable companion roaming and persist the preference."""
        self._set_roaming(enabled)

    def companion_pack_root(self) -> str:
        """Selected character-pack folder ('' = built-in procedural mascot)."""
        return self.companion.character_pack if self.companion is not None else ""

    def set_companion_pack_root(self, pack_root: str) -> bool:
        """Apply a character-pack folder; return False if it was rejected.

        Pass the empty string to fall back to the built-in procedural
        mascot; use _reset_character_pack() instead when the user wants
        the built-in mascot persisted as their explicit choice.
        """
        if self.companion is None:
            return False
        if not self.companion.set_character_pack(pack_root):
            return False
        self._companion_settings.setValue("companion/pack_root", self.companion.character_pack)
        self._set_companion_state(self.companion.animation_state)
        return True

    def _open_from_companion(self):
        self._set_companion_state("attention", reset_after_ms=1100)
        self.show_hud()

    def _set_companion_state(self, state: str, reset_after_ms: int = 0):
        if self._closing:
            return
        if self.companion is not None:
            self.companion.set_animation_state(state)
            self._state_reset_timer.stop()
            self._pending_state_reset_ms = max(0, reset_after_ms) if state != "idle" else 0
            self._resume_state_reset()

    def _pause_state_reset(self):
        if self._state_reset_timer.isActive():
            remaining = self._state_reset_timer.remainingTime()
            if remaining > 0:
                self._pending_state_reset_ms = remaining
            self._state_reset_timer.stop()

    def _resume_state_reset(self):
        if (
            self._pending_state_reset_ms > 0
            and self.companion is not None
            and self.companion.isVisible()
            and not self._closing
        ):
            self._state_reset_timer.start(self._pending_state_reset_ms)
            self._pending_state_reset_ms = 0

    def _restore_idle_state(self):
        self._pending_state_reset_ms = 0
        self._set_companion_state("idle")

    def _create_companion_mode_action(self, parent):
        action = QAction("Show floating companion", parent)
        action.setCheckable(True)
        action.setChecked(True)
        action.toggled.connect(self.set_companion_only)
        return action

    def _show_companion_menu(self, global_pos: QPoint):
        menu = QMenu(self)
        show_action = QAction("Open Kokertech Ai Hub", menu)
        show_action.triggered.connect(self.show_hud)
        menu.addAction(show_action)
        click_through_action = self._create_click_through_action(menu)
        click_through_action.setChecked(self._click_through)
        menu.addAction(click_through_action)
        menu.addAction(self._create_roaming_action(menu))
        self._create_character_pack_menu(menu)
        pause_action = QAction("Pause mascot animation", menu)
        pause_action.setCheckable(True)
        pause_action.setChecked(self.companion._paused)
        pause_action.toggled.connect(self._set_animation_paused)
        menu.addAction(pause_action)
        hide_action = QAction("Hide companion", menu)
        hide_action.triggered.connect(self._hide_companion)
        menu.addAction(hide_action)
        menu.exec(global_pos)

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_hud()

    def set_companion_only(self, enabled: bool):
        """Show only the mascot while keeping the Hub available in the tray."""
        if self.companion is None:
            self.show_hud()
            return
        self._companion_only = enabled
        self._sync_companion_action(enabled)
        self.hide()
        if enabled:
            self._position_companion_once()
            self.companion.show()
            self.companion.raise_()
            self._resume_state_reset()
        else:
            self._pause_state_reset()
            self.companion.hide()
            self.show_hud()

    def _position_companion_once(self):
        if self.companion is None or self.companion.property("initialPositioned"):
            return
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = area.right() - self.companion.width() - 28
            y = area.y() + max(32, (area.height() - self.companion.height()) // 2)
            self.companion.move(x, y)
        self.companion.setProperty("initialPositioned", True)

    def _set_animation_paused(self, paused: bool):
        if self.companion is not None:
            self.companion.set_animation_paused(paused)

    def _hide_companion(self):
        if self.companion is not None:
            self._pause_state_reset()
            self.companion.hide()
        self._companion_only = False
        self._sync_companion_action(False)

    def set_click_through(self, enabled: bool):
        """Toggle native input transparency; tray/hotkey restores interaction."""
        self._click_through = bool(enabled)
        if self._tray_click_through_action is not None:
            was_blocked = self._tray_click_through_action.blockSignals(True)
            self._tray_click_through_action.setChecked(self._click_through)
            self._tray_click_through_action.blockSignals(was_blocked)
        if self.companion is not None:
            was_visible = self.companion.isVisible()
            self.companion.setWindowFlag(Qt.WindowType.WindowTransparentForInput, self._click_through)
            if was_visible:
                self.companion.show()
        if self._tray is not None:
            self._tray.setToolTip(
                "Kokertech Ai Hub — click-through ON; use Ctrl+Alt+Shift+X or tray menu to restore"
                if enabled else "Kokertech Ai Hub — click to show controls"
            )

    def _setup_shortcuts(self):
        """Configure keyboard accelerators."""
        esc_shortcut = QShortcut(QKeySequence("Escape"), self)
        esc_shortcut.activated.connect(self.hide_hud)

        ctrl_enter_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        ctrl_enter_shortcut.activated.connect(self._send_to_main)

        up_shortcut = QShortcut(QKeySequence("Up"), self)
        up_shortcut.activated.connect(self._history_up)

        down_shortcut = QShortcut(QKeySequence("Down"), self)
        down_shortcut.activated.connect(self._history_down)

    # ── Mode Detection & Affordances ───────────────────────────────────

    def _detect_mode(self, raw_text: str) -> str:
        text = raw_text.strip()
        if text.startswith("!"):
            return self.MODE_CMD
        if text.startswith("/"):
            return self.MODE_PLUGIN
        if text.startswith("?"):
            return self.MODE_SEARCH
        return self.MODE_AI

    def _on_input_text_changed(self, text: str):
        self._update_mode_indicator(text)

    def _update_mode_indicator(self, text: str):
        mode = self._detect_mode(text)
        self._last_mode = mode
        if mode == self.MODE_CMD:
            self.mode_badge.setText("⚡ Shell Command")
            self.mode_badge.setStyleSheet(
                "background-color: #581C87; border: 1px solid #A855F7; "
                "border-radius: 10px; padding: 2px 10px; font-size: 8pt; "
                "font-weight: bold; color: #E9D5FF;"
            )
            self.container_frame.setStyleSheet(
                "QFrame#hud_container { background-color: rgba(15, 23, 42, 0.97); "
                "border: 2px solid #A855F7; border-radius: 12px; }"
            )
        elif mode == self.MODE_PLUGIN:
            self.mode_badge.setText("🧩 Plugin Action")
            self.mode_badge.setStyleSheet(
                "background-color: #064E3B; border: 1px solid #10B981; "
                "border-radius: 10px; padding: 2px 10px; font-size: 8pt; "
                "font-weight: bold; color: #A7F3D0;"
            )
            self.container_frame.setStyleSheet(
                "QFrame#hud_container { background-color: rgba(15, 23, 42, 0.97); "
                "border: 2px solid #10B981; border-radius: 12px; }"
            )
        elif mode == self.MODE_SEARCH:
            self.mode_badge.setText("🔍 Vault Search")
            self.mode_badge.setStyleSheet(
                "background-color: #78350F; border: 1px solid #F59E0B; "
                "border-radius: 10px; padding: 2px 10px; font-size: 8pt; "
                "font-weight: bold; color: #FDE68A;"
            )
            self.container_frame.setStyleSheet(
                "QFrame#hud_container { background-color: rgba(15, 23, 42, 0.97); "
                "border: 2px solid #F59E0B; border-radius: 12px; }"
            )
        else:
            self.mode_badge.setText("💬 Ask AI")
            self.mode_badge.setStyleSheet(
                "background-color: #1E3A8A; border: 1px solid #3B82F6; "
                "border-radius: 10px; padding: 2px 10px; font-size: 8pt; "
                "font-weight: bold; color: #93C5FD;"
            )
            self.container_frame.setStyleSheet(
                "QFrame#hud_container { background-color: rgba(15, 23, 42, 0.97); "
                "border: 2px solid #3B82F6; border-radius: 12px; }"
            )

    def _set_input_prefix(self, prefix: str):
        current = self.input_edit.text()
        stripped = current.lstrip("!/? ")
        self.input_edit.setText(f"{prefix}{stripped}" if prefix else stripped)
        self.input_edit.setFocus()
        self.input_edit.setCursorPosition(len(self.input_edit.text()))

    def _quick_trigger(self, command_str: str):
        self.input_edit.setText(command_str)
        self.submit()

    # ── History Navigation ─────────────────────────────────────────────

    def _history_up(self):
        if not self._history:
            return
        if self._history_index > 0:
            self._history_index -= 1
        else:
            self._history_index = len(self._history) - 1
        self.input_edit.setText(self._history[self._history_index])

    def _history_down(self):
        if not self._history:
            return
        if self._history_index < len(self._history) - 1:
            self._history_index += 1
            self.input_edit.setText(self._history[self._history_index])
        else:
            self._history_index = len(self._history)
            self.input_edit.clear()

    # ── Geometry & Positioning ─────────────────────────────────────────

    def reposition(self):
        """Set the Hub's first-show position near the upper center of the screen."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        width = min(740, max(520, int(geo.width() * 0.55)))
        x = geo.x() + (geo.width() - width) // 2
        y = geo.y() + max(40, int(geo.height() * 0.15))
        target_h = max(260, self.sizeHint().height() if self.sizeHint().height() > 0 else 320)
        self.resize(width, target_h)
        self.move(x, y)

    def toggle_visibility(self):
        """Toggle between the compact mascot and expanded Hub panel."""
        if self.isVisible():
            self.set_companion_only(True)
        elif self.companion is not None and self.companion.isVisible():
            self.show_hud()
        else:
            self.set_companion_only(True)

    def show_hud(self):
        self._pause_state_reset()
        if not self._initial_positioned:
            self.reposition()
            self._initial_positioned = True
        self._companion_only = False
        self._sync_companion_action(False)
        if self.companion is not None:
            self.companion.hide()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input_edit.setFocus()
        self.input_edit.selectAll()

    def hide_hud(self):
        self.hide()
        if self.companion is not None:
            self._position_companion_once()
            self.companion.show()
            self.companion.raise_()
            self._companion_only = True
            self._resume_state_reset()
        self.hud_closed.emit()

    def eventFilter(self, watched, event):
        """Move the frameless Hub by dragging its header, with a Qt fallback."""
        if watched in self._drag_widgets:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                global_pos = event.globalPosition().toPoint()
                handle = self.windowHandle()
                if handle is not None and handle.startSystemMove():
                    self._drag_offset = None
                else:
                    self._drag_offset = global_pos - self.frameGeometry().topLeft()
                    return True
            elif event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_offset = None
        return super().eventFilter(watched, event)

    # ── Action Execution ───────────────────────────────────────────────

    def submit(self):
        """Parse input, route to target execution engine, and track latency."""
        # If Ctrl is held, user triggered send-to-main (Ctrl+Enter); do not execute in HUD
        if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
            return

        raw_text = self.input_edit.text().strip()
        if not raw_text:
            return

        # Record history
        if not self._history or self._history[-1] != raw_text:
            self._history.append(raw_text)
        self._history_index = len(self._history)

        mode = self._detect_mode(raw_text)
        self._state_reset_timer.stop()
        self._set_companion_state("working")
        self._start_time = time.time()
        self._cancel_event.clear()

        # UI state during execution
        self.btn_run.hide()
        self.btn_stop.show()

        if mode == self.MODE_CMD:
            cmd = raw_text[1:].strip()
            self._execute_command(cmd)
        elif mode == self.MODE_PLUGIN:
            plugin_cmd = raw_text[1:].strip()
            self._execute_plugin(plugin_cmd)
        elif mode == self.MODE_SEARCH:
            query = raw_text[1:].strip()
            self._execute_search(query)
        else:
            self._execute_ai(raw_text)

    def _execute_command(self, command: str):
        if not command:
            self._on_action_completed("cmd", "No command provided.", is_success=False)
            return

        self.status_label.setText(f"⏳ Executing: !{command[:30]}...")
        self.output_view.clear()

        worker = CommandWorker(command)
        self._active_worker = worker
        worker.output_signal.connect(
            lambda out, code: self._on_action_completed(
                "cmd", out, is_success=(code == 0)
            )
        )
        worker.error_signal.connect(
            lambda err: self._on_action_completed("cmd", err, is_success=False)
        )
        worker.finished.connect(self._reset_worker_state)
        worker.start()

    def _execute_plugin(self, plugin_str: str):
        if not plugin_str:
            self._on_action_completed("plugin", "No plugin specified.", is_success=False)
            return

        parts = plugin_str.split(maxsplit=1)
        action = parts[0].strip()
        raw_params = parts[1].strip() if len(parts) > 1 else ""

        # Quick discovery helper
        if action.lower() in ("help", "list", "?"):
            self._list_available_plugins()
            return

        # Simple parameter dictionary construction
        params = {}
        if raw_params:
            if raw_params.startswith("{") and raw_params.endswith("}"):
                try:
                    import json
                    params = json.loads(raw_params)
                except (ValueError, json.JSONDecodeError):
                    params = {"query": raw_params}
            else:
                params = {"query": raw_params}

        self.status_label.setText(f"⏳ Running plugin: /{action}...")
        self.output_view.clear()

        worker = PluginWorker(action, params)
        self._active_worker = worker
        worker.result_signal.connect(
            lambda res, ok: self._on_action_completed("plugin", res, is_success=ok)
        )
        worker.error_signal.connect(
            lambda err: self._on_action_completed("plugin", err, is_success=False)
        )
        worker.finished.connect(self._reset_worker_state)
        worker.start()

    def _list_available_plugins(self):
        try:
            import plugin_registry
            reg = getattr(plugin_registry, "registry", None)
            if reg and reg.plugins:
                cmds = sorted(reg.plugins.keys())
                lines = [f"🧩 Available Native Plugins ({len(cmds)} total):\n"]
                for c in cmds:
                    meta = reg.metadata.get(c, {})
                    desc = meta.get("description", "No description")
                    lines.append(f"  • /{c.lower()}: {desc}")
                res = "\n".join(lines)
            else:
                res = "No plugins registered or registry unavailable."
            self._on_action_completed("plugin", res, is_success=True)
        except (RuntimeError, ValueError, OSError) as e:
            self._on_action_completed("plugin", f"Plugin list error: {e}", is_success=False)

    def _execute_search(self, query: str):
        if not query:
            self._on_action_completed("search", "No query provided.", is_success=False)
            return

        self.status_label.setText(f"🔍 Searching vault: {query[:30]}...")
        self.output_view.clear()

        worker = SearchWorker(query)
        self._active_worker = worker
        worker.result_signal.connect(
            lambda res: self._on_action_completed("search", res, is_success=True)
        )
        worker.error_signal.connect(
            lambda err: self._on_action_completed("search", err, is_success=False)
        )
        worker.finished.connect(self._reset_worker_state)
        worker.start()

    def _execute_ai(self, prompt: str):
        self.status_label.setText("🧠 Thinking...")
        self.output_view.clear()

        # Route through AIWorker if controller available
        if self._controller is not None:
            try:
                from workers import AIWorker
                worker = AIWorker(
                    self._controller,
                    prompt,
                    cancel_event=self._cancel_event,
                    enable_streaming=True,
                )
                self._active_worker = worker
                worker.stream_signal.connect(self._on_ai_stream_token)
                worker.reply_signal.connect(self._on_ai_reply)
                worker.finished.connect(self._reset_worker_state)
                worker.start()
                return
            except (RuntimeError, ImportError) as e:
                logger.warning(f"AIWorker initialization failed: {e}")

        # Fallback provider path
        self._execute_ai_fallback(prompt)

    def _execute_ai_fallback(self, prompt: str):
        try:
            from ai_base import get_provider
            provider = get_provider(name="local_llm")

            class DirectAIWorker(QThread):
                reply_signal = pyqtSignal(dict)
                error_signal = pyqtSignal(str)

                def __init__(self, prov, p, cancel):
                    super().__init__()
                    self.prov = prov
                    self.p = p
                    self.cancel = cancel

                def run(self):
                    try:
                        if hasattr(self.prov, "chat_completion"):
                            res = self.prov.chat_completion(
                                messages=[{"role": "user", "content": self.p}],
                                cancel_event=self.cancel,
                            )
                        elif hasattr(self.prov, "generate_response"):
                            res = self.prov.generate_response(self.p, cancel_event=self.cancel)
                        else:
                            self.error_signal.emit("Provider has no compatible completion method.")
                            return

                        if not isinstance(res, dict):
                            self.reply_signal.emit({"final": str(res)})
                            return
                        if res.get("error"):
                            self.error_signal.emit(str(res.get("error")))
                            return
                        content = res.get("content", "")
                        self.reply_signal.emit({"final": content, "thinking": "", "command": None})
                    except (RuntimeError, ValueError, OSError, AttributeError, TypeError, KeyError, TimeoutError) as err:
                        self.error_signal.emit(str(err))

            worker = DirectAIWorker(provider, prompt, self._cancel_event)
            self._active_worker = worker
            worker.reply_signal.connect(self._on_ai_reply)
            worker.error_signal.connect(
                lambda err: self._on_action_completed("ai", f"❌ AI Error: {err}", is_success=False)
            )
            worker.finished.connect(self._reset_worker_state)
            worker.start()
        except (RuntimeError, ValueError, OSError, ImportError) as e:
            self._on_action_completed("ai", f"❌ AI Fallback Error: {e}", is_success=False)

    def _on_ai_stream_token(self, token: str):
        cursor = self.output_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(token)
        self.output_view.setTextCursor(cursor)
        self.output_view.ensureCursorVisible()

    def _on_ai_reply(self, reply_dict: dict):
        if not isinstance(reply_dict, dict):
            reply_dict = {"final": str(reply_dict)}
        final_text = reply_dict.get("final", "")
        if not self.output_view.toPlainText().strip():
            self.output_view.setPlainText(final_text)
        is_success = not reply_dict.get("stopped", False) and not final_text.startswith("❌")
        self._on_action_completed("ai", final_text, is_success=is_success)

    def _on_action_completed(self, mode: str, result_text: str, is_success: bool = True):
        elapsed = (time.time() - self._start_time) * 1000 if self._start_time > 0 else 0
        if not self.output_view.toPlainText().strip():
            self.output_view.setPlainText(result_text)

        status_prefix = "✅" if is_success else "❌"
        self.status_label.setText(f"{status_prefix} Done ({elapsed:.0f}ms)")
        self._set_companion_state("success" if is_success else "attention", reset_after_ms=2200)
        self.action_completed.emit(mode, result_text)

        if is_success and CONFIG.get("voice_auto_speak", False) and mode == "ai":
            try:
                from acoustic_pipeline import get_acoustic_pipeline
                get_acoustic_pipeline().speak(result_text[:400])
            except (RuntimeError, OSError, ValueError):
                pass

    def _toggle_voice_dictation(self):
        """Toggle voice dictation using VAD recorder."""
        if hasattr(self, "_voice_worker") and self._voice_worker is not None and self._voice_worker.isRunning():
            self._voice_worker.stop_recording()
            self._voice_worker = None
            self.status_label.setText("Voice dictation stopped.")
            return

        try:
            from workers import VoiceRecorderWorker
            self._voice_worker = VoiceRecorderWorker()
            self._voice_worker.status_signal.connect(lambda s: self.status_label.setText(s))
            self._voice_worker.transcription_signal.connect(self._on_voice_transcribed)
            self._voice_worker.start_recording()
            self.status_label.setText("🎙️ Listening... (Speak now)")
        except (RuntimeError, OSError, ImportError) as e:
            self.status_label.setText(f"Voice error: {e}")

    def _on_voice_transcribed(self, text: str):
        if text and not text.startswith("Error:"):
            self.input_edit.setText(text)
            self.status_label.setText(f"🗣️ Heard: \"{text[:40]}\"")
            if CONFIG.get("voice_auto_submit", True):
                self.submit()
        elif text.startswith("Error:"):
            self.status_label.setText(text[:50])

    def _speak_output(self):
        """Read the current output text aloud using the acoustic pipeline."""
        content = self.output_view.toPlainText().strip()
        if content:
            try:
                from acoustic_pipeline import get_acoustic_pipeline
                get_acoustic_pipeline().speak(content[:500])
                self.status_label.setText("🔊 Speaking...")
            except (RuntimeError, OSError, ValueError) as e:
                self.status_label.setText(f"Speech error: {e}")

    def _reset_worker_state(self):
        self._active_worker = None
        self.btn_stop.hide()
        self.btn_run.show()

    def stop_execution(self):
        """Halt in-flight generation or command worker."""
        self._cancel_event.set()
        if self._active_worker is not None:
            if hasattr(self._active_worker, "cancel_event") and hasattr(self._active_worker.cancel_event, "set"):
                try:
                    self._active_worker.cancel_event.set()
                except (RuntimeError, AttributeError):
                    pass
            if hasattr(self._active_worker, "requestInterruption"):
                try:
                    self._active_worker.requestInterruption()
                except (RuntimeError, AttributeError):
                    pass
            if self._active_worker.isRunning():
                try:
                    self._active_worker.wait(1000)
                except (RuntimeError, AttributeError):
                    pass
            self.status_label.setText("⏹ Stopped by user.")
            self._set_companion_state("attention", reset_after_ms=2200)
        self._reset_worker_state()

    def accept(self):
        """Prevent QDialog default accept behavior from closing the HUD overlay.

        The Mini-HUD is a non-modal floating command palette; Enter key executions must
        never dismiss the HUD overlay.
        """
        pass

    def reject(self):
        """Handle Esc or reject by cleanly calling hide_hud."""
        self.hide_hud()

    def keyPressEvent(self, event: QKeyEvent):
        """Handle keyboard interactions, preventing premature overlay dismissal.

        Zero-Trust Invariant: In Qt, QDialog automatically intercepts Enter/Return
        and calls accept() or invokes autoDefault buttons, dismissing the window.
        We intercept Key_Return and Key_Enter:
        - If Ctrl is pressed, route to _send_to_main()
        - If a focused button exists (via keyboard tab navigation), click it
        - If input_edit is not focused, trigger submit() (if input_edit is focused,
          its returnPressed signal already dispatched submit())
        - Consume (accept) the event to prevent QDialog.accept() from closing the HUD.
        """
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._send_to_main()
            else:
                focused = self.focusWidget()
                if isinstance(focused, QPushButton):
                    focused.click()
                elif focused != self.input_edit:
                    self.submit()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        """Clean teardown on dialog close."""
        self._closing = True
        self._pending_state_reset_ms = 0
        self._state_reset_timer.stop()
        self.stop_execution()
        if hasattr(self, "_voice_worker") and self._voice_worker is not None and self._voice_worker.isRunning():
            try:
                self._voice_worker.stop_recording()
                self._voice_worker.wait(1000)
            except (RuntimeError, AttributeError):
                pass
        if self.companion is not None:
            self.companion.set_roaming(False)
            self.companion.hide()
            self.companion.close()
        if self._tray is not None:
            self._tray.hide()
        self.hud_closed.emit()
        super().closeEvent(event)

    # ── Clipboard & Integration ────────────────────────────────────────

    def _copy_output(self):
        text = self.output_view.toPlainText().strip()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard is not None:
                clipboard.setText(text)
                self.status_label.setText("📋 Copied to clipboard!")

    def _send_to_main(self):
        """Transmit current output (or input if output is empty) to main window."""
        content = self.output_view.toPlainText().strip() or self.input_edit.text().strip()
        if content:
            self.send_to_main_requested.emit(content)
            self.status_label.setText("↗ Sent to main chat!")
            self.hide_hud()

    def clear(self):
        self.input_edit.clear()
        self.output_view.clear()
        self.status_label.setText("⚡ Ready")
        self._reset_worker_state()
