"""Tests for ui_components.py."""

import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import Qt, QPointF, QRectF, QPoint
from PyQt6.QtWidgets import QGraphicsScene, QFrame
from PyQt6.QtGui import QPainter, QWheelEvent, QKeyEvent, QMouseEvent
from PyQt6.QtCore import QEvent

class TestGraphLayoutWorkerConstants(unittest.TestCase):
    """Class constants."""
    def test_max_iterations(self):
        from ui_components import GraphLayoutWorker
        assert isinstance(GraphLayoutWorker.MAX_ITERATIONS, int)
        assert GraphLayoutWorker.MAX_ITERATIONS > 0
    def test_canvas_size(self):
        from ui_components import GraphLayoutWorker
        w, h = GraphLayoutWorker.CANVAS_SIZE
        assert w > 0 and h > 0
    def test_convergence(self):
        from ui_components import GraphLayoutWorker
        assert GraphLayoutWorker.CONVERGENCE_THRESHOLD > 0

class TestEdgeItem(unittest.TestCase):
    def test_stores_ids(self):
        from ui_components import EdgeItem
        e = EdgeItem(0, 0, 100, 100, source_id=42, target_id=99)
        assert e.source_id == 42
        assert e.target_id == 99
    def test_stores_coords(self):
        from ui_components import EdgeItem
        e = EdgeItem(10, 20, 30, 40, source_id=1, target_id=2)
        assert e.line().x1() == 10
        assert e.line().y1() == 20
        assert e.line().x2() == 30
        assert e.line().y2() == 40
    def test_unique_ids(self):
        from ui_components import EdgeItem
        e1 = EdgeItem(0, 0, 1, 1, source_id=1, target_id=2)
        e2 = EdgeItem(0, 0, 2, 2, source_id=3, target_id=4)
        assert e1.source_id != e2.source_id
    def test_extended_attributes(self):
        from ui_components import EdgeItem
        e = EdgeItem(0, 0, 10, 10, source_id=1, target_id=2, rel_type="CAUSES", weight=2.5, confidence=0.9)
        assert e.rel_type == "CAUSES"
        assert e.weight == 2.5
        assert e.confidence == 0.9
        assert e.default_pen is None

class TestZoomableGraphicsView(unittest.TestCase):
    def setUp(self):
        from ui_components import ZoomableGraphicsView
        self.scene = QGraphicsScene()
        self.view = ZoomableGraphicsView(self.scene)
    def tearDown(self):
        self.view.close()
        self.scene.clear()
    def _wheel(self, dy):
        return QWheelEvent(QPointF(0,0), QPointF(0,0), QPoint(0,0), QPoint(0,dy),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False)
    def test_initial_zoom(self):
        assert self.view.get_zoom_level() == 1.0
    def test_zoom_in(self):
        self.view.zoom_in()
        assert self.view.get_zoom_level() > 1.0
    def test_zoom_out(self):
        self.view.zoom_in()
        before = self.view.get_zoom_level()
        self.view.zoom_out()
        assert self.view.get_zoom_level() < before
    def test_reset_zoom(self):
        self.view.zoom_in()
        self.view.reset_zoom()
        assert abs(self.view.get_zoom_level() - 1.0) < 0.001
    def test_zoom_changed_signal(self):
        emitted = []
        self.view.zoom_changed.connect(emitted.append)
        self.view.zoom_in()
        assert len(emitted) == 1
        assert emitted[0] > 1.0
    def test_reset_zoom_signal(self):
        self.view.zoom_in()
        emitted = []
        self.view.zoom_changed.connect(emitted.append)
        self.view.reset_zoom()
        assert emitted[0] == 1.0
    def test_wheel_forward(self):
        before = self.view.get_zoom_level()
        self.view.wheelEvent(self._wheel(120))
        assert self.view.get_zoom_level() > before
    def test_wheel_backward(self):
        self.view.zoom_in()
        before = self.view.get_zoom_level()
        self.view.wheelEvent(self._wheel(-120))
        assert self.view.get_zoom_level() < before
    def test_alt_1_emits_0(self):
        emitted = []
        self.view.type_toggle_signal.connect(emitted.append)
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_1, Qt.KeyboardModifier.AltModifier)
        self.view.keyPressEvent(ev)
        assert emitted == [0]
    def test_alt_3_emits_2(self):
        emitted = []
        self.view.type_toggle_signal.connect(emitted.append)
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_3, Qt.KeyboardModifier.AltModifier)
        self.view.keyPressEvent(ev)
        assert emitted == [2]
    def test_alt_9_emits_8(self):
        emitted = []
        self.view.type_toggle_signal.connect(emitted.append)
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_9, Qt.KeyboardModifier.AltModifier)
        self.view.keyPressEvent(ev)
        assert emitted == [8]
    def test_no_alt_no_emit(self):
        emitted = []
        self.view.type_toggle_signal.connect(emitted.append)
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier)
        self.view.keyPressEvent(ev)
        assert emitted == []
    def test_set_minimap(self):
        from ui_components import MiniMapView
        m = MiniMapView(self.scene, self.view)
        self.view.set_minimap(m)
        assert self.view._minimap is m
    def test_scroll_contents(self):
        emitted = []
        self.view.viewport_changed.connect(emitted.append)
        self.view.scrollContentsBy(10, 20)
        assert len(emitted) >= 1
    def test_escape_key_emits_escape_pressed(self):
        emitted = []
        self.view.escape_pressed.connect(lambda: emitted.append(True))
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        self.view.keyPressEvent(ev)
        assert len(emitted) == 1
    def test_background_clicked_on_empty_scene(self):
        emitted = []
        self.view.background_clicked.connect(lambda: emitted.append(True))
        ev = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(50, 50),
            QPointF(50, 50),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.view.mousePressEvent(ev)
        assert len(emitted) == 1

class TestMiniMapView(unittest.TestCase):
    def setUp(self):
        from ui_components import ZoomableGraphicsView, MiniMapView
        self.scene = QGraphicsScene()
        self.main = ZoomableGraphicsView(self.scene)
        self.mini = MiniMapView(self.scene, self.main)
    def tearDown(self):
        self.mini.close()
        self.main.close()
        self.scene.clear()
    def test_fixed_size(self):
        assert self.mini.width() == 200
        assert self.mini.height() == 150
    def test_scroll_bars_off(self):
        h = self.mini.horizontalScrollBarPolicy()
        assert h == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        v = self.mini.verticalScrollBarPolicy()
        assert v == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    def test_not_interactive(self):
        assert self.mini.isInteractive() is False
    def test_frame_shape_box(self):
        assert self.mini.frameShape() == QFrame.Shape.Box
    def test_viewport_empty(self):
        self.mini.update_viewport(QRectF())
        assert self.mini._indicator_rect.isNull()
    def test_viewport_nonempty(self):
        self.scene.addRect(0, 0, 100, 100)
        self.mini.update_viewport(QRectF(10, 10, 50, 50))
        assert not self.mini._indicator_rect.isNull()
    def test_mouse_press(self):
        self.scene.addRect(0, 0, 100, 100)
        self.main.set_minimap(self.mini)
        ev = QMouseEvent(QEvent.Type.MouseButtonPress,
            QPointF(50,50), QPointF(50,50),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)
        self.mini.mousePressEvent(ev)
    def test_draw_foreground_null(self):
        self.mini._indicator_rect = QRectF()
        painter = QPainter()
        try:
            painter.begin(self.mini.viewport())
            self.mini.drawForeground(painter, QRectF())
        except RuntimeError:
            pass
        finally:
            painter.end()

class TestClusterRegionItem(unittest.TestCase):
    def setUp(self):
        self.scene = QGraphicsScene()
    def tearDown(self):
        self.scene.clear()
    def _me(self, t, btn):
        e = MagicMock()
        e.button.return_value = btn
        e.buttons.return_value = btn
        e.pos.return_value = QPointF(10,10)
        e.scenePos.return_value = QPointF(10,10)
        e.screenPos.return_value = QPointF(10,10)
        return e
    def _he(self):
        e = MagicMock()
        e.pos.return_value = QPointF(5,5)
        e.scenePos.return_value = QPointF(5,5)
        e.screenPos.return_value = QPointF(5,5)
        return e
    def test_constructor(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0, 0, 100, 50, "#EF4444", type_name="fact", count=5)
        assert item.type_name == "fact"
        assert item.count == 5
        assert item._collapsed is False
        assert item._hex_color == "#EF4444"
    def test_constructor_collapsed(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0, 0, 100, 50, "#10B981", collapsed=True)
        assert item._collapsed is True
    def test_z_value(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0, 0, 100, 50, "#3B82F6")
        assert item.zValue() == -5
    def test_selectable(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0, 0, 100, 50, "#3B82F6")
        assert bool(item.flags() & item.GraphicsItemFlag.ItemIsSelectable)
    def test_hover_events(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0, 0, 100, 50, "#3B82F6")
        assert item.acceptHoverEvents()
    def test_set_collapsed(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0, 0, 100, 50, "#EF4444")
        assert item._collapsed is False
        item.set_collapsed(True)
        assert item._collapsed is True
        item.set_collapsed(False)
        assert item._collapsed is False
    def test_set_collapsed_update(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0, 0, 100, 50, "#EF4444")
        with patch.object(item, "update") as m:
            item.set_collapsed(True)
            m.assert_called_once()
    def test_hover_enter_flag(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0, 0, 100, 50, "#EF4444")
        self.scene.addItem(item)
        # super().hoverEnterEvent crashes with MagicMock; wrap in try
        try:
            item.hoverEnterEvent(self._he())
        except (RuntimeError, TypeError):
            pass
        assert item._hovered is True
    def test_hover_enter_alpha(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0, 0, 100, 50, "#EF4444")
        self.scene.addItem(item)
        try:
            item.hoverEnterEvent(self._he())
        except (RuntimeError, TypeError):
            pass
        assert item.brush().color().alpha() == 45
    def test_hover_leave(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0, 0, 100, 50, "#EF4444")
        self.scene.addItem(item)
        try:
            item.hoverEnterEvent(self._he())
        except (RuntimeError, TypeError):
            pass
        try:
            item.hoverLeaveEvent(self._he())
        except (RuntimeError, TypeError):
            pass
        assert item._hovered is False
        assert item.brush().color().alpha() == 25
    def test_dblclick_callback(self):
        from ui_components import ClusterRegionItem
        cb = MagicMock()
        item = ClusterRegionItem(0,0,100,50,"#EF4444",click_callback=cb)
        self.scene.addItem(item)
        ev = self._me(QEvent.Type.MouseButtonDblClick, Qt.MouseButton.LeftButton)
        # Call unbound method to bypass PyQt6 call-site type checking
        try:
            ClusterRegionItem.mouseDoubleClickEvent(item, ev)
        except (RuntimeError, TypeError):
            pass
        cb.assert_called_once()
    def test_dblclick_no_callback(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0,0,100,50,"#EF4444",click_callback=None)
        self.scene.addItem(item)
        ev = self._me(QEvent.Type.MouseButtonDblClick, Qt.MouseButton.LeftButton)
        try:
            ClusterRegionItem.mouseDoubleClickEvent(item, ev)
        except (RuntimeError, TypeError):
            pass
    def test_left_click_toggles(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0,0,100,50,"#EF4444")
        self.scene.addItem(item)
        was = item.isSelected()
        ev = self._me(QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton)
        item.mousePressEvent(ev)
        assert item.isSelected() != was
    def test_right_click_no_toggle(self):
        from ui_components import ClusterRegionItem
        item = ClusterRegionItem(0,0,100,50,"#EF4444")
        self.scene.addItem(item)
        was = item.isSelected()
        ev = self._me(QEvent.Type.MouseButtonPress, Qt.MouseButton.RightButton)
        # super().mousePressEvent crashes with MagicMock; wrap in try
        try:
            item.mousePressEvent(ev)
        except (RuntimeError, TypeError):
            pass
        assert item.isSelected() == was
    def test_paint(self):
        from ui_components import ClusterRegionItem
        from PyQt6.QtWidgets import QStyleOptionGraphicsItem
        item = ClusterRegionItem(0,0,100,50,"#EF4444",type_name="fact",count=5)
        self.scene.addItem(item)
        painter = QPainter()
        try:
            if self.scene.views():
                painter.begin(self.scene.views()[0].viewport())
                item.paint(painter, QStyleOptionGraphicsItem(), None)
        except (RuntimeError, Exception):
            pass
        finally:
            painter.end()

if __name__ == "__main__":
    unittest.main()
