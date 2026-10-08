"""
UI components for the Neural Knowledge Graph tab.

Includes zoomable graphics view, minimap, node/edge items, cluster
regions, text labels, and a background layout worker thread.

Sprint N (2026-10): GraphLayoutWorker upgraded from a circular-layout stub
to a physics-driven Fruchterman-Reingold layout with simulated annealing,
weighted links, viewport culling, and convergence early-exit. EdgeItem now
carries rel_type/weight/confidence and a default_pen snapshot. The view
gained escape_pressed / background_clicked signals.
"""
from __future__ import annotations

import math
import threading

from PyQt6.QtCore import Qt, QThread, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QWheelEvent
from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsWidget, QGraphicsLineItem,
    QGraphicsRectItem, QGraphicsTextItem, QWidget,
)
from logging_config import get_logger

logger = get_logger(name="MemoryVault")

# =========================================================================
# 1. GraphLayoutWorker — background thread for Force-Directed Layout
# =========================================================================

class GraphLayoutWorker(QThread):
    """Runs physics-driven force-directed graph layout computation in a background thread."""

    MAX_ITERATIONS = 200
    CANVAS_SIZE = (1200, 800)
    CONVERGENCE_THRESHOLD = 0.05

    layout_ready_signal = pyqtSignal(object, object, object)  # (pos_dict, nodes_dict, links_list)

    def __init__(
        self,
        iterations: int = 100,
        canvas_size: tuple[int, int] = (1200, 800),
        force_recompute: bool = False,
        db_path: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self.iterations = iterations
        self.canvas_size = canvas_size
        self.force_recompute = force_recompute
        self.db_path = db_path

    def cancel(self) -> None:
        """Signal the worker to stop early."""
        self._cancel.set()

    def stop(self) -> None:
        """Cooperative stop alias."""
        self._cancel.set()

    def _compute_force_directed_layout(
        self,
        ids: list[int],
        links: list[tuple],
        saved_positions: dict[int, tuple[float, float]],
    ) -> dict[int, tuple[float, float]]:
        """Compute 2D coordinates for nodes using a physics-driven Fruchterman-Reingold layout with simulated annealing."""
        n = len(ids)
        if n == 0:
            return {}

        w, h = self.canvas_size
        cx, cy = w / 2.0, h / 2.0

        if n == 1:
            nid = ids[0]
            if nid in saved_positions and not self.force_recompute:
                return {nid: saved_positions[nid]}
            return {nid: (cx, cy)}

        # If all nodes already have saved positions and force_recompute is False, preserve them directly
        if not self.force_recompute and all(nid in saved_positions for nid in ids):
            return {nid: saved_positions[nid] for nid in ids}

        # Initialize positions
        pos: dict[int, list[float]] = {}
        for i, nid in enumerate(ids):
            if nid in saved_positions and not self.force_recompute:
                pos[nid] = [saved_positions[nid][0], saved_positions[nid][1]]
            else:
                # Golden angle phyllotaxis spiral distribution for natural initial dispersion
                theta = i * 2.399963229728653
                radius = 45.0 * math.sqrt(i + 1)
                pos[nid] = [
                    cx + radius * math.cos(theta),
                    cy + radius * math.sin(theta),
                ]

        id_to_idx = {nid: i for i, nid in enumerate(ids)}
        node_coords = [pos[nid] for nid in ids]

        # Build edge index list with clamped weights
        edges = []
        for link in links:
            if not isinstance(link, (tuple, list)) or len(link) < 2:
                continue
            src, tgt = link[0], link[1]
            weight = float(link[3]) if len(link) > 3 and link[3] is not None else 1.0
            if src not in id_to_idx or tgt not in id_to_idx or src == tgt:
                continue
            edges.append((id_to_idx[src], id_to_idx[tgt], max(0.1, min(5.0, weight))))

        area = float(w * h)
        k = max(50.0, min(250.0, 0.75 * math.sqrt(area / float(n))))
        k_sq = k * k

        iterations = min(self.MAX_ITERATIONS, max(40, self.iterations))
        temp = float(w) / 10.0
        cooling = math.pow(0.01 / max(temp, 0.01), 1.0 / max(iterations, 1))

        for _ in range(iterations):
            if self._cancel.is_set():
                break

            disp_x = [0.0] * n
            disp_y = [0.0] * n

            # Repulsive forces (O(n^2) pair loop, i < j)
            for i in range(n):
                xi, yi = node_coords[i][0], node_coords[i][1]
                for j in range(i + 1, n):
                    xj, yj = node_coords[j][0], node_coords[j][1]
                    dx = xi - xj
                    dy = yi - yj
                    dist = math.hypot(dx, dy)
                    if dist < 0.001:
                        # Jitter coincident nodes apart deterministically
                        dx = 0.1 * math.cos(float(i + j))
                        dy = 0.1 * math.sin(float(i + j))
                        dist = 0.1
                    force = k_sq / dist
                    fx = dx / dist * force
                    fy = dy / dist * force
                    disp_x[i] += fx
                    disp_y[i] += fy
                    disp_x[j] -= fx
                    disp_y[j] -= fy

            # Attractive forces along edges (weighted, clamped)
            for u_idx, v_idx, weight in edges:
                dx = node_coords[u_idx][0] - node_coords[v_idx][0]
                dy = node_coords[u_idx][1] - node_coords[v_idx][1]
                dist = math.hypot(dx, dy)
                if dist < 0.001:
                    continue
                force = dist * dist / k * weight
                fx = dx / dist * force
                fy = dy / dist * force
                disp_x[u_idx] -= fx
                disp_y[u_idx] -= fy
                disp_x[v_idx] += fx
                disp_y[v_idx] += fy

            # Weak gravity toward canvas center
            for i in range(n):
                gx = (cx - node_coords[i][0]) * 0.04
                gy = (cy - node_coords[i][1]) * 0.04
                disp_x[i] += gx
                disp_y[i] += gy

            max_disp = 0.0
            margin = 60.0
            min_x, max_x = margin, w - margin
            min_y, max_y = margin, h - margin

            # Apply displacements, limited by temperature, clamped to canvas
            for i in range(n):
                dx = disp_x[i]
                dy = disp_y[i]
                d_len = math.hypot(dx, dy)
                if d_len > 0:
                    step = min(d_len, temp)
                    node_coords[i][0] += dx / d_len * step
                    node_coords[i][1] += dy / d_len * step
                    if step > max_disp:
                        max_disp = step
                node_coords[i][0] = max(min_x, min(max_x, node_coords[i][0]))
                node_coords[i][1] = max(min_y, min(max_y, node_coords[i][1]))

            temp *= cooling
            if max_disp < self.CONVERGENCE_THRESHOLD:
                break

        return {
            nid: (round(node_coords[i][0], 1), round(node_coords[i][1], 1))
            for i, nid in enumerate(ids)
        }

    def run(self) -> None:
        """Compute a physics-driven ForceAtlas2 / Fruchterman-Reingold layout."""
        pos: dict[int, tuple[float, float]] = {}
        nodes: dict[int, dict[str, str]] = {}
        links: list[tuple] = []

        try:
            import sqlite3
            import os

            db_path = self.db_path
            if not db_path:
                try:
                    import memory_vault
                    db_path = getattr(memory_vault, "DB_PATH", None)
                except (ImportError, AttributeError):
                    pass
            if not db_path:
                from config import WORKSPACE_DIR
                db_path = os.path.join(WORKSPACE_DIR, "kokertech_vault.db")

            if os.path.isfile(db_path):
                conn = sqlite3.connect(db_path, timeout=15.0)
                try:
                    cursor = conn.execute("SELECT id, content, node_type FROM core_memories")
                    ids: list[int] = []
                    for row in cursor.fetchall():
                        nid, content, ntype = row
                        ids.append(nid)
                        node_type_str = str(ntype) if ntype else "fact"
                        nodes[nid] = {"content": str(content), "type": node_type_str}

                    saved_positions: dict[int, tuple[float, float]] = {}
                    try:
                        p_cursor = conn.execute("SELECT node_id, pos_x, pos_y FROM graph_positions")
                        for p_row in p_cursor.fetchall():
                            saved_positions[p_row[0]] = (float(p_row[1]), float(p_row[2]))
                    except sqlite3.OperationalError:
                        # graceful fallback: graph_positions table may not exist yet
                        pass

                    link_cols = set()
                    try:
                        prag = conn.execute("PRAGMA table_info(memory_links)").fetchall()
                        link_cols = {row[1] for row in prag}
                    except sqlite3.OperationalError:
                        # feature detection: schema probe fallback on legacy vaults
                        pass

                    if link_cols:
                        rel_col = "relation_type" if "relation_type" in link_cols else "relationship_type" if "relationship_type" in link_cols else "'RELATES_TO'"
                        weight_col = "weight" if "weight" in link_cols else "1.0"
                        conf_col = "confidence" if "confidence" in link_cols else "1.0"
                        try:
                            cursor = conn.execute(
                                f"SELECT source_id, target_id, {rel_col}, {weight_col}, {conf_col} FROM memory_links"  # noqa: S608
                            )
                            links = [
                                (
                                    row[0],
                                    row[1],
                                    str(row[2]) if row[2] else "RELATES_TO",
                                    float(row[3]) if row[3] is not None else 1.0,
                                    float(row[4]) if row[4] is not None else 1.0,
                                )
                                for row in cursor.fetchall()
                            ]
                        except sqlite3.OperationalError:
                            # graceful fallback: legacy schema or missing columns
                            pass

                    if ids:
                        pos = self._compute_force_directed_layout(ids, links, saved_positions)
                finally:
                    conn.close()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            logger.debug(f"GraphLayoutWorker load failed: {e}")
            pass

        if not self._cancel.is_set():
            self.layout_ready_signal.emit(pos, nodes, links)


# =========================================================================
# 2. ZoomableGraphicsView — QGraphicsView with zoom & keyboard shortcuts
# =========================================================================

class ZoomableGraphicsView(QGraphicsView):
    """A QGraphicsView that supports mouse-wheel zoom, fit-to-view, and
    provides signals for zoom changes and type-toggle keyboard shortcuts."""

    zoom_changed = pyqtSignal(float)       # zoom_factor (1.0 = 100%)
    type_toggle_signal = pyqtSignal(int)    # Alt+1..N shortcut index
    viewport_changed = pyqtSignal(object)   # emitted on scroll/pan
    escape_pressed = pyqtSignal()           # emitted on Esc key
    background_clicked = pyqtSignal()       # emitted when clicking empty background

    ZOOM_STEP = 1.15
    MIN_ZOOM = 0.05
    MAX_ZOOM = 10.0

    def __init__(self, scene: QGraphicsWidget | None = None) -> None:
        super().__init__(scene)
        self._current_zoom = 1.0
        self._minimap: MiniMapView | None = None
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)

    def get_zoom_level(self) -> float:
        """Return the current zoom factor (1.0 = 100%)."""
        return self._current_zoom

    # ── Public helpers ────────────────────────────────────────────────────

    def set_minimap(self, minimap: MiniMapView) -> None:
        """Store a reference to the associated mini-map widget."""
        self._minimap = minimap

    def _emit_viewport_rect(self) -> None:
        """Notify the mini-map that the viewport changed."""
        if self._minimap is not None:
            self._minimap.update()

    def _emit_zoom_level(self) -> None:
        """Emit the zoom_changed signal with the current zoom factor."""
        self.zoom_changed.emit(self._current_zoom)

    def zoom_in(self) -> None:
        """Zoom in by one step."""
        self._apply_zoom(self.ZOOM_STEP)

    def zoom_out(self) -> None:
        """Zoom out by one step."""
        self._apply_zoom(1.0 / self.ZOOM_STEP)

    def reset_zoom(self) -> None:
        """Reset zoom to 100%."""
        self._apply_zoom(1.0 / self._current_zoom)

    def _apply_zoom(self, factor: float) -> None:
        """Scale the view by *factor* and emit the new zoom level."""
        new_zoom = self._current_zoom * factor
        if self.MIN_ZOOM <= new_zoom <= self.MAX_ZOOM:
            self._current_zoom = new_zoom
            self.scale(factor, factor)
            self._emit_zoom_level()
            self._emit_viewport_rect()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """Override to emit ``viewport_changed`` on every scroll/pan."""
        super().scrollContentsBy(dx, dy)
        self.viewport_changed.emit(True)
        self._emit_viewport_rect()

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom in/out on mouse-wheel events."""
        if event is not None:
            delta = event.angleDelta().y()
            if delta > 0:
                self._apply_zoom(self.ZOOM_STEP)
            elif delta < 0:
                self._apply_zoom(1.0 / self.ZOOM_STEP)
            event.accept()
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        """Handle Alt+1..N type-toggle shortcuts and Escape key."""
        if event is None:
            return
        if hasattr(event, "key") and event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            return
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            key = event.key()
            if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
                index = key - Qt.Key.Key_1  # 0-based
                self.type_toggle_signal.emit(index)
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        """Detect clicks on empty background to unhighlight / clear focus."""
        if event is not None and hasattr(event, "button") and (
            event.button() == Qt.MouseButton.LeftButton
        ):
            item = self.itemAt(event.pos()) if hasattr(self, "itemAt") and hasattr(event, "pos") else None
            if item is None:
                self.background_clicked.emit()
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:
        """Re-locate the mini-map overlay on resize."""
        super().resizeEvent(event)
        if self._minimap is not None:
            mm_w = min(180, self.width() // 4)
            mm_h = min(120, self.height() // 4)
            self._minimap.setGeometry(self.width() - mm_w - 8, self.height() - mm_h - 8, mm_w, mm_h)
            self._minimap.update()


# =========================================================================

class MiniMapView(QWidget):
    """A small overview widget that shows the full graph extent and the
    current viewport rectangle."""

    def __init__(self, scene, main_view: QGraphicsView) -> None:
        super().__init__()
        self._scene = scene
        self._main_view = main_view
        self._indicator_rect = QRectF()
        self.setFixedSize(200, 150)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: rgba(30, 30, 30, 180); border: 1px solid #555; border-radius: 4px;")

    # ── Public API ──────────────────────────────────────────────────────

    def viewport(self):
        """Return the widget's paint device (self)."""
        return self

    def frameShape(self):
        """Return the frame shape (Box)."""
        from PyQt6.QtWidgets import QFrame
        return QFrame.Shape.Box

    def isInteractive(self) -> bool:
        """The minimap is not interactive (mouse events are transparent)."""
        return False

    def horizontalScrollBarPolicy(self):
        """Scroll bars are always hidden on the minimap."""
        return Qt.ScrollBarPolicy.ScrollBarAlwaysOff

    def verticalScrollBarPolicy(self):
        """Scroll bars are always hidden on the minimap."""
        return Qt.ScrollBarPolicy.ScrollBarAlwaysOff

    def update_viewport(self, rect: QRectF) -> None:
        """Store the current viewport indicator rect and repaint."""
        self._indicator_rect = QRectF(rect)
        self.update()

    def mousePressEvent(self, event) -> None:
        """No-op: the minimap is not interactive."""
        pass

    def drawForeground(self, painter, rect: QRectF) -> None:
        """Draw the viewport indicator rectangle."""
        if self._indicator_rect.isNull():
            return
        painter.save()
        painter.setPen(QPen(QColor("#10B981"), 1.0))
        painter.setBrush(QBrush(QColor(16, 185, 129, 30)))
        painter.drawRect(self._indicator_rect)
        painter.restore()

    # ── paintEvent ─────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        """Paint a scaled-down minimap with a viewport indicator."""
        from PyQt6.QtGui import QPainter, QBrush, QPen, QColor

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        scene_rect = self._scene.sceneRect() if self._scene else QRectF()
        if scene_rect.isEmpty():
            painter.end()
            return

        # Scale the scene into our widget
        scale = min(
            self.width() / scene_rect.width() if scene_rect.width() > 0 else 1,
            self.height() / scene_rect.height() if scene_rect.height() > 0 else 1,
        )
        painter.scale(scale, scale)
        painter.translate(-scene_rect.x(), -scene_rect.y())

        # Draw scene background
        painter.fillRect(scene_rect, QColor("#1A1A1A"))
        self._scene.render(painter, target=scene_rect, source=scene_rect)

        # Draw viewport indicator
        if self._main_view:
            vr = self._main_view.mapToScene(self._main_view.viewport().rect()).boundingRect()
            if not vr.isEmpty():
                painter.setPen(QPen(QColor("#10B981"), 2.0 / scale))
                painter.setBrush(QBrush(QColor(16, 185, 129, 30)))
                painter.drawRect(vr)

        painter.end()


# =========================================================================
# 4. EdgeItem — QGraphicsLineItem representing a relationship
# =========================================================================

class EdgeItem(QGraphicsLineItem):
    """A directed or undirected edge between two graph nodes."""

    def __init__(
        self, x1: float, y1: float, x2: float, y2: float,
        source_id: int, target_id: int,
        rel_type: str = "RELATES_TO", weight: float = 1.0,
        confidence: float = 1.0,
    ) -> None:
        super().__init__(x1, y1, x2, y2)
        self.source_id = source_id
        self.target_id = target_id
        self.rel_type = rel_type
        self.weight = weight
        self.confidence = confidence
        self._offset_x: float = 0.0
        self._offset_y: float = 0.0
        self.default_pen = None


# 5. NodeGraphicsItem — QGraphicsRectItem representing a memory node
# =========================================================================

class NodeGraphicsItem(QGraphicsRectItem):
    """A rectangular node in the knowledge graph with callbacks for
    click, edit, delete, expand, link, and drag."""

    def __init__(
        self, x: float, y: float, w: float, h: float,
        node_id: int, content: str, type_label: str,
        inspect_callback, edit_callback, delete_callback,
        expand_callback=None, link_callback=None,
        position_changed_callback=None,
    ) -> None:
        super().__init__(x, y, w, h)
        self.node_id = node_id
        self.content = content
        self.type_label = type_label
        self._inspect_callback = inspect_callback
        self._edit_callback = edit_callback
        self._delete_callback = delete_callback
        self._expand_callback = expand_callback
        self._link_callback = link_callback
        self._pos_callback = position_changed_callback
        self._dragging = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptHoverEvents(True)
        self.setFlags(
            QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsRectItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

        # Set up colours based on type
        from tabs.neural_graph_tab import NODE_TYPE_COLORS, TYPE_FALLBACK
        hex_color, _ = NODE_TYPE_COLORS.get(type_label, (TYPE_FALLBACK, type_label))
        self._fill_color = QColor(hex_color)
        self._fill_color.setAlpha(180)
        self.setBrush(QBrush(self._fill_color))
        self.setPen(QPen(QColor(40, 40, 40), 4.0))

    def hoverEnterEvent(self, event) -> None:
        """Highlight on hover."""
        self.setPen(QPen(self._fill_color.lighter(150), 4.0))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        """Remove highlight on hover leave."""
        self.setPen(QPen(QColor(40, 40, 40), 4.0))
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:
        """Inspect on click."""
        if event is not None:
            self._dragging = False
            if self._inspect_callback:
                self._inspect_callback(self.node_id, self.type_label, self.content)
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Track dragging for position callback."""
        self._dragging = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Notify position change on drag end."""
        if self._dragging and self._pos_callback:
            cx = self.rect().center().x() + self.pos().x()
            cy = self.rect().center().y() + self.pos().y()
            self._pos_callback(self.node_id, cx, cy)
            self._dragging = False
        super().mouseReleaseEvent(event)


# =========================================================================
# 6. ClusterRegionItem — clickable background region for node-type clusters
# =========================================================================

class ClusterRegionItem(QGraphicsRectItem):
    """A translucent background rectangle that groups nodes of the same type."""

    def __init__(
        self, x: float, y: float, w: float, h: float,
        hex_color: str, type_name: str = "",
        count: int = 0, collapsed: bool = False,
        click_callback=None,
    ) -> None:
        super().__init__(x, y, w, h)
        self.type_name = type_name
        self.count = count
        self._collapsed = collapsed
        self._hex_color = hex_color
        self._hovered = False
        self._click_callback = click_callback

        color = QColor(hex_color)
        color.setAlpha(25)
        self.setBrush(QBrush(color))
        self.setPen(QPen(QColor(hex_color), 1.5, Qt.PenStyle.DashLine))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptHoverEvents(True)
        self.setFlags(
            QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable
            | self.flags()
        )
        self.setZValue(-5)

    def zValue(self) -> float:
        """Return the fixed z-value so the region renders behind nodes."""
        return -5

    def brush(self):
        """Return the current brush."""
        return super().brush()

    def set_collapsed(self, state: bool) -> None:
        """Toggle collapse state and trigger a repaint."""
        self._collapsed = state
        self.update()

    def hoverEnterEvent(self, event) -> None:
        """Brighten on hover."""
        self._hovered = True
        c = QColor(self._hex_color)
        c.setAlpha(45)
        self.setBrush(QBrush(c))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        """Restore on hover leave."""
        self._hovered = False
        c = QColor(self._hex_color)
        c.setAlpha(25)
        self.setBrush(QBrush(c))
        super().hoverLeaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """Fire the click callback on double-click."""
        if self._click_callback is not None:
            self._click_callback(self.type_name)
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        """Toggle selection on left-click; right-click passes through."""
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self.setSelected(not self.isSelected())
        else:
            super().mousePressEvent(event)

    def paint(self, painter, option, widget=None) -> None:
        """Paint the cluster region with type label, count, and collapse state."""
        rect = self.rect()
        font = QFont("Consolas", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(font)

        # Collapsed: just a thin header bar
        if self._collapsed:
            header = QRectF(rect.x(), rect.y(), rect.width(), 22)
            painter.fillRect(header, QColor(self._hex_color).darker(150))
            painter.setPen(QColor("#D1D5DB"))
            label = f"{self.type_name} ({self.count}) [collapsed]"
            painter.drawText(rect.adjusted(6, 0, -6, 0),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             label)
            return

        super().paint(painter, option, widget)

        # Label
        painter.setPen(QColor("#D1D5DB"))
        label = f"{self.type_name} ({self.count})"
        painter.drawText(rect.adjusted(6, 2, -6, -4),
                         Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                         label)


# =========================================================================
# 7. TextLabelItem — non-interactive text overlay
# =========================================================================

class TextLabelItem(QGraphicsTextItem):
    """A styled text label for node names and cluster summaries."""

    def __init__(
        self, text: str,
        bg_color: str = "#222222",
        text_color: str = "#D1D5DB",
    ) -> None:
        super().__init__(text)
        self.setDefaultTextColor(QColor(text_color))
        font = QFont("Consolas", 8)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)

        # Draw a background rectangle behind the text
        self._bg_color = QColor(bg_color)
        self._bg_color.setAlpha(200)
        self._padding = 3

    def paint(self, painter, option, widget=None) -> None:
        """Draw a rounded background behind the text."""
        painter.save()
        rect = self.boundingRect()
        bg_rect = rect.adjusted(-self._padding, -self._padding,
                                self._padding, self._padding)
        painter.setBrush(self._bg_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bg_rect, 4, 4)
        painter.restore()
        super().paint(painter, option, widget)
