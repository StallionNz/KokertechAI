"""
tests/test_mini_hud.py — Unit tests for Desktop Mini-Jarvis HUD (MiniHudDialog).

Tests cover:
  1. Window properties, frameless flags, translucency, and canonical layout.
  2. Input mode detection (AI, shell command '!', plugin '/', memory vault '?').
  3. Dynamic mode badge & style updating.
  4. Prompt & command history cycling (Up/Down).
  5. Shell command worker execution, timeout, and signal dispatch.
  6. Plugin worker execution, discovery, and error handling.
  7. Memory vault search worker and formatted results.
  8. AI streaming & completion routing through AIWorker / fallback.
  9. Affordances: Copy to clipboard, Send to Main window, Clear, and Stop.
  10. Repositioning & visibility toggling.
  11. Shipped starter character pack (assets/mascot) matching its documented format.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from tabs.mini_hud import (
    CommandWorker,
    MiniHudDialog,
    NeonCompanionWidget,
    PluginWorker,
    SearchWorker,
)


def _create_test_dialog(controller=None):
    """Use isolated settings defaults so tests never read or write user preferences."""
    settings = MagicMock()
    settings.value.side_effect = lambda _key, default=None, **_kwargs: default
    with patch("tabs.mini_hud.QSettings", return_value=settings):
        dialog = MiniHudDialog(parent=None, controller=controller)
    dialog._companion_settings = settings
    return dialog


class TestMiniHudInitialization(unittest.TestCase):
    """Verify window flags, layout, and component initialization."""

    def setUp(self):
        self.dialog = _create_test_dialog(controller=None)

    def tearDown(self):
        self.dialog.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

    def test_window_flags(self):
        flags = self.dialog.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert flags & Qt.WindowType.Tool

    def test_translucent_background_and_modal(self):
        assert self.dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert not self.dialog.isModal()

    def test_canonical_layout_pattern(self):
        """Must have root layout bound via self.setLayout(layout)."""
        layout = self.dialog.layout()
        assert layout is not None
        assert layout.count() == 1  # Contains container_frame

    def test_initial_widget_states(self):
        assert not self.dialog.btn_run.isHidden()
        assert self.dialog.btn_stop.isHidden()
        assert self.dialog.status_label.text() == "⚡ Ready"
        assert self.dialog.input_edit.text() == ""
        assert self.dialog.output_view.toPlainText() == ""

    def test_branding_and_titles(self):
        """Verify windowTitle and title_label branding."""
        assert self.dialog.windowTitle() == "Kokertech Ai Hub"
        assert self.dialog.title_label.text() == "[-K-] ♉ Kokertech Ai Hub"

    def test_floating_companion_can_be_resized(self):
        from PyQt6.QtWidgets import QSizeGrip

        assert self.dialog.container_frame.minimumSize().width() >= 480
        assert self.dialog.container_frame.minimumSize().height() >= 300
        assert self.dialog.resize_grip is not None
        assert isinstance(self.dialog.resize_grip, QSizeGrip)

    def test_transparent_companion_renders_neon_mascot(self):
        from PyQt6.QtGui import QImage, QPainter

        companion = self.dialog.companion
        companion.resize(260, 292)
        image = QImage(companion.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        companion.render(painter)
        painter.end()

        assert not image.isNull()
        assert image.hasAlphaChannel()
        assert image.pixelColor(image.width() // 2, image.height() // 2).alpha() > 0
        assert image.pixelColor(0, 0).alpha() == 0

    def test_transparent_companion_starts_with_idle_animation(self):
        companion = self.dialog.companion
        assert companion is not None
        assert companion.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert companion.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        assert not companion._animation_timer.isActive()
        self.dialog.set_companion_only(True)
        assert companion.isVisible()
        assert companion._animation_timer.isActive()
        self.dialog._set_animation_paused(True)
        assert not companion._animation_timer.isActive()

    def test_click_through_can_be_recovered(self):
        companion = self.dialog.companion
        self.dialog.set_companion_only(True)
        self.dialog.set_click_through(True)
        assert companion.windowFlags() & Qt.WindowType.WindowTransparentForInput
        assert companion.isVisible()
        self.dialog.set_click_through(False)
        assert not companion.windowFlags() & Qt.WindowType.WindowTransparentForInput
        assert companion.isVisible()

    def test_companion_opens_hub_and_hub_returns_to_companion(self):
        self.dialog.set_companion_only(True)
        assert self.dialog.companion.isVisible()
        self.dialog.show_hud()
        assert self.dialog.isVisible()
        assert not self.dialog.companion.isVisible()
        self.dialog.hide_hud()
        assert not self.dialog.isVisible()
        assert self.dialog.companion.isVisible()

    def test_reopening_preserves_user_geometry(self):
        self.dialog.show_hud()
        self.dialog.resize(620, 420)
        self.dialog.move(80, 90)
        expected_geometry = self.dialog.geometry()

        self.dialog.hide_hud()
        self.dialog.show_hud()

        assert self.dialog.geometry() == expected_geometry


class TestCompanionArtworkAndStates(unittest.TestCase):
    """Verify local artwork packs and the mascot's four visual states."""

    def setUp(self):
        self.companion = NeonCompanionWidget()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self._dispose_companion)

    def _dispose_companion(self):
        self.companion.set_roaming(False)
        self.companion.set_animation_paused(True)
        self.companion.close()
        self.companion.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

    def _write_image(self, filename, color):
        from PyQt6.QtGui import QColor, QImage

        image = QImage(24, 24, QImage.Format.Format_ARGB32)
        image.fill(QColor(color))
        path = os.path.join(self.temp_dir.name, filename)
        assert image.save(path)
        return path

    def test_roaming_is_optional_and_stops_when_hidden(self):
        assert not self.companion.roaming
        assert not self.companion._roam_timer.isActive()

        self.companion.set_roaming(True)
        assert not self.companion._roam_timer.isActive()
        self.companion.show()
        QApplication.processEvents()
        assert self.companion._roam_timer.isActive()
        self.companion.hide()
        assert not self.companion._roam_timer.isActive()

    def test_roaming_bounces_inside_available_screen_bounds(self):
        from PyQt6.QtCore import QPointF, QRect

        screen = MagicMock()
        screen.availableGeometry.return_value = QRect(0, 0, 300, 320)
        self.companion.resize(220, 240)
        self.companion.move(79, 79)
        self.companion._roaming = True
        self.companion._roam_velocity = QPointF(3.0, 4.0)
        self.companion.show()
        QApplication.processEvents()
        with patch.object(QApplication, "screenAt", return_value=screen):
            self.companion._advance_roaming()

        assert self.companion.pos().x() <= 80
        assert self.companion.pos().y() <= 80
        assert self.companion._roam_velocity.x() < 0
        assert self.companion._roam_velocity.y() < 0

    def test_drag_pauses_roaming_until_mouse_release(self):
        from PyQt6.QtCore import QPointF

        self.companion.show()
        QApplication.processEvents()
        self.companion.set_roaming(True)
        press = SimpleNamespace(
            button=lambda: Qt.MouseButton.LeftButton,
            globalPosition=lambda: QPointF(10, 10),
            accept=lambda: None,
        )
        move = SimpleNamespace(
            buttons=lambda: Qt.MouseButton.LeftButton,
            globalPosition=lambda: QPointF(80, 80),
            accept=lambda: None,
        )
        release = SimpleNamespace(
            button=lambda: Qt.MouseButton.LeftButton,
            accept=lambda: None,
        )

        self.companion.mousePressEvent(press)
        self.companion.mouseMoveEvent(move)
        assert not self.companion._roam_timer.isActive()
        assert self.companion._dragged
        self.companion.mouseReleaseEvent(release)
        assert self.companion._roam_timer.isActive()

    def test_state_names_select_matching_art_and_fallback_to_idle(self):
        idle_path = self._write_image("idle.png", "green")
        working_path = self._write_image("working.png", "cyan")

        assert self.companion.set_character_pack(self.temp_dir.name)
        assert self.companion.character_pack == os.path.abspath(self.temp_dir.name)
        assert self.companion._artwork_path == idle_path

        for state in NeonCompanionWidget.STATES:
            self.companion.set_animation_state(state)
            expected = working_path if state == "working" else idle_path
            assert self.companion.animation_state == state
            assert self.companion._artwork_path == expected
            assert self.companion._pixmap is not None

    def test_shared_artwork_name_is_accepted_and_invalid_pack_is_rejected(self):
        shared_path = self._write_image("MaScOt.PNG", "magenta")
        assert self.companion.set_character_pack(self.temp_dir.name)
        assert self.companion._artwork_path == shared_path

        invalid_dir = tempfile.TemporaryDirectory()
        self.addCleanup(invalid_dir.cleanup)
        with open(os.path.join(invalid_dir.name, "idle.png"), "w", encoding="utf-8") as image_file:
            image_file.write("not an image")
        current_pack = self.companion.character_pack
        assert not self.companion.set_character_pack(invalid_dir.name)
        assert self.companion.character_pack == current_pack

    def test_animated_pack_movie_starts_and_pauses_with_companion(self):
        from PIL import Image
        from PyQt6.QtGui import QMovie

        frames = [Image.new("RGBA", (24, 24), color) for color in ("green", "cyan")]
        gif_path = os.path.join(self.temp_dir.name, "idle.gif")
        frames[0].save(
            gif_path,
            save_all=True,
            append_images=frames[1:],
            duration=80,
            loop=0,
            format="GIF",
        )
        if not QMovie(gif_path).isValid():
            self.skipTest("Qt GIF animation plugin unavailable")
        assert self.companion.set_character_pack(self.temp_dir.name)
        assert self.companion._movie is not None
        assert os.path.normcase(self.companion._movie.fileName()) == os.path.normcase(gif_path)

        self.companion.show()
        QApplication.processEvents()
        movie = self.companion._movie
        assert movie.state() == QMovie.MovieState.Running
        self.companion.set_animation_paused(True)
        assert movie.state() == QMovie.MovieState.NotRunning
        self.companion.set_animation_paused(False)
        assert movie.state() == QMovie.MovieState.Running
        self.companion.set_animation_paused(True)
        movie.stop()
        self.companion._movie = None
        movie.deleteLater()
        self.companion.set_character_pack(None)

    def test_reset_pack_restores_procedural_art_and_invalid_state_fails(self):
        self._write_image("idle.png", "green")
        assert self.companion.set_character_pack(self.temp_dir.name)
        self.companion.set_character_pack(None)
        assert self.companion.character_pack == ""
        assert self.companion._artwork_path == ""
        assert self.companion._pixmap is None
        with self.assertRaises(ValueError):
            self.companion.set_animation_state("sleeping")

    def test_state_completion_and_error_select_success_and_attention(self):
        dialog = _create_test_dialog(controller=None)
        self.addCleanup(dialog.close)
        dialog._on_action_completed("search", "found it", is_success=True)
        assert dialog.companion.animation_state == "success"
        assert dialog._pending_state_reset_ms == 2200

        dialog._on_action_completed("plugin", "failed", is_success=False)
        assert dialog.companion.animation_state == "attention"
        dialog._pending_state_reset_ms = 0
        dialog._state_reset_timer.stop()

        dialog.input_edit.setText("hello")
        with patch.object(dialog, "_execute_ai") as execute_ai:
            dialog.submit()
        execute_ai.assert_called_once_with("hello")
        assert dialog.companion.animation_state == "working"

        dialog._restore_idle_state()
        assert dialog.companion.animation_state == "idle"

    def test_persisted_preferences_load_pack_and_optional_roaming(self):
        self._write_image("idle.png", "green")
        settings = MagicMock()
        settings.value.side_effect = lambda key, default=None, **_kwargs: (
            self.temp_dir.name if key == "companion/pack_root" else True if key == "companion/roaming" else default
        )
        with patch("tabs.mini_hud.QSettings", return_value=settings):
            dialog = MiniHudDialog(parent=None, controller=None)
        self.addCleanup(dialog.close)

        assert dialog.companion.character_pack == os.path.abspath(self.temp_dir.name)
        assert dialog.companion.roaming
        assert settings.remove.call_count == 0
        dialog.companion.set_roaming(False)
        dialog.companion.set_character_pack(None)

    def test_character_pack_picker_action_applies_and_persists_selected_folder(self):
        dialog = _create_test_dialog(controller=None)
        self.addCleanup(dialog.close)
        self._write_image("idle.png", "green")
        self._write_image("success.png", "lime")

        from PyQt6.QtWidgets import QMenu

        parent_menu = QMenu()
        parent_menu.setParent(dialog)
        with patch("tabs.mini_hud.QFileDialog.getExistingDirectory", return_value=self.temp_dir.name) as picker:
            dialog._create_character_pack_menu(parent_menu).actions()[0].trigger()
        parent_menu.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

        picker.assert_called_once()
        assert dialog.companion.character_pack == os.path.abspath(self.temp_dir.name)
        assert dialog._companion_settings.setValue.call_args.args == (
            "companion/pack_root",
            os.path.abspath(self.temp_dir.name),
        )
        dialog.companion.set_character_pack(None)

    def test_character_artwork_menu_retains_its_submenu(self):
        from PyQt6.QtWidgets import QMenu

        dialog = _create_test_dialog(controller=None)
        self.addCleanup(dialog.close)
        parent_menu = QMenu(dialog)
        submenu = dialog._create_character_pack_menu(parent_menu)
        assert submenu is not None
        assert [item.text() for item in submenu.actions()] == [
            "Choose character pack folder…",
            "Use built-in neon mascot",
        ]

    def test_temporary_state_reset_waits_until_companion_is_visible(self):
        dialog = _create_test_dialog(controller=None)
        self.addCleanup(dialog.close)

        dialog._set_companion_state("success", reset_after_ms=5000)
        assert dialog.companion.animation_state == "success"
        assert not dialog._state_reset_timer.isActive()
        assert dialog._pending_state_reset_ms == 5000

        dialog.set_companion_only(True)
        assert dialog._state_reset_timer.isActive()
        dialog.show_hud()
        assert not dialog._state_reset_timer.isActive()
        assert dialog._pending_state_reset_ms > 0
        dialog.hide_hud()
        assert dialog._state_reset_timer.isActive()
        dialog._state_reset_timer.stop()

    def test_roaming_preference_action_stores_user_choice(self):
        dialog = _create_test_dialog(controller=None)
        self.addCleanup(dialog.close)
        stored = {}
        dialog._companion_settings.setValue = lambda key, value: stored.__setitem__(key, value)

        dialog._set_roaming(True)

        assert dialog.companion.roaming
        assert stored == {"companion/roaming": True}
        dialog.companion.set_roaming(False)

    def test_invalid_character_pack_does_not_replace_current_pack(self):
        good_pack = tempfile.TemporaryDirectory()
        bad_pack = tempfile.TemporaryDirectory()
        self.addCleanup(good_pack.cleanup)
        self.addCleanup(bad_pack.cleanup)
        from PyQt6.QtGui import QColor, QImage

        image = QImage(12, 12, QImage.Format.Format_ARGB32)
        image.fill(QColor("cyan"))
        assert image.save(os.path.join(good_pack.name, "idle.png"))
        with open(os.path.join(bad_pack.name, "idle.png"), "w", encoding="utf-8") as image_file:
            image_file.write("invalid image content")

        assert self.companion.set_character_pack(good_pack.name)
        existing_path = self.companion.character_pack
        assert not self.companion.set_character_pack(bad_pack.name)
        assert self.companion.character_pack == existing_path
        self.companion.set_character_pack(None)

    def test_reset_character_pack_persists_builtin_and_clears_pack(self):
        dialog = _create_test_dialog(controller=None)
        self.addCleanup(dialog.close)
        stored = {}
        dialog._companion_settings.setValue = lambda key, value: stored.__setitem__(key, value)
        self._write_image("idle.png", "green")
        assert dialog.companion.set_character_pack(self.temp_dir.name)
        assert dialog.companion.character_pack == os.path.abspath(self.temp_dir.name)

        dialog._reset_character_pack()
        assert dialog.companion.character_pack == ""
        assert stored.get("companion/pack_root") == "builtin"
        dialog.companion.set_character_pack(None)

    def test_persisted_builtin_preference_restores_procedural_neon(self):
        settings = MagicMock()
        settings.value.side_effect = lambda key, default=None, **_kwargs: (
            "builtin" if key == "companion/pack_root" else default
        )
        with patch("tabs.mini_hud.QSettings", return_value=settings):
            dialog = MiniHudDialog(parent=None, controller=None)
        self.addCleanup(dialog.close)
        assert dialog.companion.character_pack == ""

    def test_companion_interactive_cursor_gaze_and_leave_event(self):
        self.companion.resize(240, 270)
        move = SimpleNamespace(
            buttons=lambda: Qt.MouseButton.NoButton,
            position=lambda: QPointF(200.0, 50.0),
        )
        self.companion.mouseMoveEvent(move)
        assert self.companion._target_look.x() > 0.0
        assert self.companion._target_look.y() < 0.0

        leave = SimpleNamespace()
        self.companion.leaveEvent(leave)
        assert self.companion._target_look == QPointF(0.0, 0.0)

    def test_desktop_wide_cursor_gaze_tracking_and_idle_reset(self):
        self.companion.resize(240, 270)
        self.companion.show()
        with patch.object(self.companion, "mapToGlobal", return_value=QPoint(500, 500)):
            # Cursor to the right and below: (850, 750)
            with patch("tabs.mini_hud.QCursor.pos", return_value=QPoint(850, 750)):
                self.companion._update_cursor_gaze()
                assert self.companion._target_look.x() > 0.5
                assert self.companion._target_look.y() > 0.5

            # Cursor to the left and above: (150, 250)
            with patch("tabs.mini_hud.QCursor.pos", return_value=QPoint(150, 250)):
                self.companion._update_cursor_gaze()
                assert self.companion._target_look.x() < -0.5
                assert self.companion._target_look.y() < -0.5

            # Inactivity timeout (>4s idle): resets target look to neutral (0, 0)
            with patch("tabs.mini_hud.QCursor.pos", return_value=QPoint(150, 250)):
                self.companion._last_cursor_move_time = time.time() - 5.0
                self.companion._update_cursor_gaze()
                assert self.companion._target_look == QPointF(0.0, 0.0)
        self.companion.hide()

    def test_companion_atomic_orbital_rings_and_socket_clipped_pupil_rendering(self):
        """ANTI-FRAGILITY: Regression guard for 3D atomic orbital rings & socket-clipped pupils."""
        # 1. Ring invariants: 4 to 6 tilted 3D orbital rings (5 configured)
        assert 4 <= len(self.companion._ATOMIC_RINGS) <= 6
        assert len(self.companion._ATOMIC_RINGS) == 5
        assert len(self.companion._precomputed_rings) == 5
        for ring in self.companion._precomputed_rings:
            assert len(ring["back_pts"]) == 33
            assert len(ring["front_pts"]) == 33
            assert ring["r"] > 80.0
            assert ring["spd"] != 0.0
            assert ring["n_parts"] >= 1

        # 2. Procedural Fallback mode paint execution
        self.companion.resize(240, 270)
        self.companion.set_character_pack(None)
        self.companion._current_look = QPointF(0.5, -0.4)
        pix_procedural = QPixmap(240, 270)
        pix_procedural.fill(Qt.GlobalColor.transparent)
        self.companion.render(pix_procedural)
        img_proc = pix_procedural.toImage()
        assert img_proc.pixelColor(0, 0).alpha() == 0

        # 3. Character Pack mode paint execution with socket-clipped eye rendering
        pack_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "mascot"
        )
        if os.path.isdir(pack_root):
            assert self.companion.set_character_pack(pack_root)
            self.companion._current_look = QPointF(-0.7, 0.6)
            pix_pack = QPixmap(240, 270)
            pix_pack.fill(Qt.GlobalColor.transparent)
            self.companion.render(pix_pack)
            img_pack = pix_pack.toImage()
            assert img_pack.pixelColor(0, 0).alpha() == 0
            self.companion.set_character_pack(None)


class TestCompanionPreferenceApi(unittest.TestCase):
    """Verify the public companion preference API used by the Settings panel."""

    def setUp(self):
        self.dialog = _create_test_dialog(controller=None)
        self.addCleanup(self._dispose_dialog)

    def _dispose_dialog(self):
        self.dialog.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

    def _write_image(self, path):
        from PyQt6.QtGui import QColor, QImage

        image = QImage(24, 24, QImage.Format.Format_ARGB32)
        image.fill(QColor("green"))
        assert image.save(path)
        return path

    def test_set_companion_roaming_persists_and_emits(self):
        settings = self.dialog._companion_settings
        emissions = []
        self.dialog.companion.roaming_changed.connect(emissions.append)

        self.dialog.set_companion_roaming(True)
        assert self.dialog.companion_roaming() is True
        settings.setValue.assert_called_with("companion/roaming", True)
        assert emissions[-1] is True

        self.dialog.set_companion_roaming(False)
        assert self.dialog.companion_roaming() is False
        settings.setValue.assert_called_with("companion/roaming", False)

    def test_preference_accessors_tolerate_missing_companion(self):
        companion = self.dialog.companion
        self.dialog.companion = None
        try:
            assert self.dialog.companion_roaming() is False
            assert self.dialog.companion_pack_root() == ""
            assert self.dialog.set_companion_pack_root("assets/mascot") is False
            self.dialog.set_companion_roaming(True)  # must not raise
        finally:
            self.dialog.companion = companion

    def test_set_companion_pack_root_persists_valid_pack(self):
        pack_dir = tempfile.TemporaryDirectory()
        self.addCleanup(pack_dir.cleanup)
        self._write_image(os.path.join(pack_dir.name, "idle.png"))
        settings = self.dialog._companion_settings

        assert self.dialog.set_companion_pack_root(pack_dir.name) is True
        expected = os.path.abspath(pack_dir.name)
        assert self.dialog.companion_pack_root() == expected
        settings.setValue.assert_called_with("companion/pack_root", expected)

    def test_set_companion_pack_root_rejects_invalid_and_clears_on_empty(self):
        pack_dir = tempfile.TemporaryDirectory()
        self.addCleanup(pack_dir.cleanup)
        empty_dir = tempfile.TemporaryDirectory()
        self.addCleanup(empty_dir.cleanup)
        self._write_image(os.path.join(pack_dir.name, "idle.png"))
        settings = self.dialog._companion_settings

        assert self.dialog.set_companion_pack_root(pack_dir.name) is True
        good_root = self.dialog.companion_pack_root()

        assert self.dialog.set_companion_pack_root(empty_dir.name) is False
        assert self.dialog.companion_pack_root() == good_root

        assert self.dialog.set_companion_pack_root("") is True
        assert self.dialog.companion_pack_root() == ""
        settings.setValue.assert_called_with("companion/pack_root", "")


class TestShippedStarterPack(unittest.TestCase):
    """Verify the bundled assets/mascot sample pack matches its documented format."""

    def setUp(self):
        self.pack_root = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "mascot"
        )
        if not os.path.isdir(self.pack_root):
            self.skipTest("bundled starter pack not present in this checkout")
        self.companion = NeonCompanionWidget()
        self.addCleanup(self._dispose_companion)

    def _dispose_companion(self):
        self.companion.set_roaming(False)
        self.companion.set_animation_paused(True)
        self.companion.close()
        self.companion.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

    def test_starter_pack_loads_and_maps_every_state(self):
        assert self.companion.set_character_pack(self.pack_root)
        assert self.companion.character_pack == os.path.abspath(self.pack_root)
        for state in NeonCompanionWidget.STATES:
            self.companion.set_animation_state(state)
            artwork = self.companion._artwork_path
            assert artwork, f"no artwork resolved for state {state!r}"
            stem = os.path.splitext(os.path.basename(artwork))[0].casefold()
            assert stem == state, f"state {state!r} resolved unrelated artwork {artwork!r}"
            assert self.companion._pixmap is not None
            assert self.companion._movie is None  # sample pack ships stills only

    def test_starter_pack_images_are_transparent_square_stills(self):
        from PyQt6.QtGui import QImage

        for name in ("idle.png", "working.png", "attention.png", "success.png"):
            image = QImage(os.path.join(self.pack_root, name))
            assert not image.isNull(), f"unreadable sample image: {name}"
            assert image.width() == image.height(), f"sample image not square: {name}"
            assert image.hasAlphaChannel(), f"sample image lacks transparency: {name}"

    def test_starter_pack_contains_only_documented_state_images(self):
        entries = sorted(
            name.casefold() for name in os.listdir(self.pack_root) if not name.startswith(".")
        )
        assert entries == ["attention.png", "idle.png", "readme.md", "success.png", "working.png"]


class TestMiniHudModeDetection(unittest.TestCase):
    """Verify prefix-based mode detection and dynamic UI badge updates."""

    def setUp(self):
        self.dialog = _create_test_dialog(controller=None)

    def tearDown(self):
        self.dialog.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

    def test_detect_mode_ai_default(self):
        assert self.dialog._detect_mode("") == MiniHudDialog.MODE_AI
        assert self.dialog._detect_mode("What is quantum computing?") == MiniHudDialog.MODE_AI

    def test_detect_mode_cmd(self):
        assert self.dialog._detect_mode("!dir") == MiniHudDialog.MODE_CMD
        assert self.dialog._detect_mode("! python --version") == MiniHudDialog.MODE_CMD

    def test_detect_mode_plugin(self):
        assert self.dialog._detect_mode("/system_status") == MiniHudDialog.MODE_PLUGIN
        assert self.dialog._detect_mode("/list_files path=.") == MiniHudDialog.MODE_PLUGIN

    def test_detect_mode_search(self):
        assert self.dialog._detect_mode("?database WAL") == MiniHudDialog.MODE_SEARCH
        assert self.dialog._detect_mode("? architectural invariants") == MiniHudDialog.MODE_SEARCH

    def test_mode_indicator_updates_on_text_change(self):
        self.dialog.input_edit.setText("!echo test")
        assert "Shell" in self.dialog.mode_badge.text()

        self.dialog.input_edit.setText("/system_status")
        assert "Plugin" in self.dialog.mode_badge.text()

        self.dialog.input_edit.setText("?checkpoint")
        assert "Vault" in self.dialog.mode_badge.text()

        self.dialog.input_edit.setText("Hello Jarvis")
        assert "Ask AI" in self.dialog.mode_badge.text()

    def test_set_input_prefix(self):
        self.dialog.input_edit.setText("system_status")
        self.dialog._set_input_prefix("/")
        assert self.dialog.input_edit.text() == "/system_status"

        self.dialog._set_input_prefix("!")
        assert self.dialog.input_edit.text() == "!system_status"

        self.dialog._set_input_prefix("")
        assert self.dialog.input_edit.text() == "system_status"


class TestMiniHudHistory(unittest.TestCase):
    """Verify input command history cycling."""

    def setUp(self):
        self.dialog = _create_test_dialog(controller=None)

    def tearDown(self):
        self.dialog.close()

    def test_history_cycling(self):
        # Empty history does not crash
        self.dialog._history_up()
        self.dialog._history_down()

        # Add history entries
        self.dialog._history = ["prompt 1", "!dir", "/system_status"]
        self.dialog._history_index = 3

        self.dialog._history_up()
        assert self.dialog.input_edit.text() == "/system_status"

        self.dialog._history_up()
        assert self.dialog.input_edit.text() == "!dir"

        self.dialog._history_up()
        assert self.dialog.input_edit.text() == "prompt 1"

        self.dialog._history_down()
        assert self.dialog.input_edit.text() == "!dir"

        self.dialog._history_down()
        assert self.dialog.input_edit.text() == "/system_status"

        self.dialog._history_down()
        assert self.dialog.input_edit.text() == ""


class TestCommandWorker(unittest.TestCase):
    """Verify background shell command execution."""

    def test_command_worker_success(self):
        worker = CommandWorker("echo test_mini_hud", timeout=5)
        results = []
        worker.output_signal.connect(lambda out, code: results.append((out, code)))
        worker.run()

        assert len(results) == 1
        out, code = results[0]
        assert "test_mini_hud" in out
        assert code == 0

    @patch("subprocess.run")
    def test_command_worker_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="sleep 10", timeout=1)
        worker = CommandWorker("sleep 10", timeout=1)
        errors = []
        worker.error_signal.connect(lambda err: errors.append(err))
        worker.run()

        assert len(errors) == 1
        assert "timed out" in errors[0]


class TestPluginWorker(unittest.TestCase):
    """Verify background plugin execution and discovery."""

    @patch("plugin_registry.registry")
    def test_plugin_worker_success(self, mock_reg):
        mock_reg.plugins = {"SYSTEM_STATUS": MagicMock()}
        mock_reg.execute.return_value = "CPU: 10%, RAM: 40%"

        worker = PluginWorker("system_status", {})
        results = []
        worker.result_signal.connect(lambda res, ok: results.append((res, ok)))
        worker.run()

        assert len(results) == 1
        res, ok = results[0]
        assert "CPU: 10%" in res
        assert ok is True

    @patch("plugin_registry.registry")
    def test_plugin_worker_not_found(self, mock_reg):
        mock_reg.plugins = {"SYSTEM_STATUS": MagicMock()}
        worker = PluginWorker("non_existent_plugin", {})
        errors = []
        worker.error_signal.connect(lambda err: errors.append(err))
        worker.run()

        assert len(errors) == 1
        assert "not found" in errors[0]

    @patch("plugin_registry.registry")
    def test_plugin_worker_execute_command_fallback(self, mock_reg):
        del mock_reg.execute
        mock_reg.plugins = {"SYSTEM_STATUS": MagicMock()}
        mock_reg.execute_command.return_value = "CPU: 15%, RAM: 45%"

        worker = PluginWorker("system_status", {})
        results = []
        worker.result_signal.connect(lambda res, ok: results.append((res, ok)))
        worker.run()

        assert len(results) == 1
        res, ok = results[0]
        assert "CPU: 15%" in res
        assert ok is True

    def test_real_plugin_registry_has_execute(self):
        import plugin_registry
        reg = plugin_registry.registry
        assert hasattr(reg, "execute")
        assert callable(reg.execute)
        res = reg.execute("SYSTEM_STATUS", {})
        assert "HARDWARE DIAGNOSTICS" in res


class TestSearchWorker(unittest.TestCase):
    """Verify memory vault search worker."""

    @patch("memory_vault.fts5_search")
    def test_search_worker_success(self, mock_fts):
        mock_fts.return_value = [
            {
                "category": "core",
                "summary": "SQLite WAL autocheckpoint verified",
                "snippet": "Enforced PRAGMA wal_autocheckpoint=1000",
                "timestamp": "2026-09-24 12:00:00",
            }
        ]
        worker = SearchWorker("WAL")
        results = []
        worker.result_signal.connect(lambda res: results.append(res))
        worker.run()

        assert len(results) == 1
        assert "SQLite WAL autocheckpoint verified" in results[0]
        assert "Enforced PRAGMA wal_autocheckpoint" in results[0]

    @patch("memory_vault.fts5_search")
    def test_search_worker_no_hits(self, mock_fts):
        mock_fts.return_value = []
        worker = SearchWorker("non_existent_query")
        results = []
        worker.result_signal.connect(lambda res: results.append(res))
        worker.run()

        assert len(results) == 1
        assert "No memory vault matches found" in results[0]

    @patch("memory_vault.fts5_search")
    def test_search_worker_tuple_results_regression(self, mock_fts):
        """REGRESSION GUARD for tuple search results in
        ``tabs.mini_hud.SearchWorker.run`` (lines 268-285 of ``tabs/mini_hud.py``).

        Invariant: memory_vault.fts5_search returns list of tuples:
        (rowid, source, content, score). SearchWorker must unpack tuple results
        without calling .get() or raising AttributeError.
        """
        mock_fts.return_value = [
            (101, "memory", "Verified offline SQLite WAL mode configuration", -2.45),
        ]
        worker = SearchWorker("SQLite")
        results = []
        worker.result_signal.connect(lambda res: results.append(res))
        worker.run()

        assert len(results) == 1
        assert "Verified offline SQLite WAL mode configuration" in results[0]
        assert "score: -2.45" in results[0]


class TestMiniHudAffordancesAndActions(unittest.TestCase):
    """Verify copy, send to main, clear, stop, and visibility toggling."""

    def setUp(self):
        self.dialog = _create_test_dialog(controller=None)

    def tearDown(self):
        self.dialog.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

    def test_clear_resets_fields(self):
        self.dialog.input_edit.setText("Some input")
        self.dialog.output_view.setPlainText("Some output")
        self.dialog.status_label.setText("Done")
        self.dialog.clear()

        assert self.dialog.input_edit.text() == ""
        assert self.dialog.output_view.toPlainText() == ""
        assert self.dialog.status_label.text() == "⚡ Ready"

    def test_send_to_main_emits_signal(self):
        emitted = []
        self.dialog.send_to_main_requested.connect(lambda txt: emitted.append(txt))
        self.dialog.output_view.setPlainText("Result to export")
        self.dialog._send_to_main()

        assert emitted == ["Result to export"]
        assert not self.dialog.isVisible()

    def test_copy_output_to_clipboard(self):
        self.dialog.output_view.setPlainText("Clipboard test payload")
        self.dialog._copy_output()
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            assert clipboard.text() == "Clipboard test payload"
        assert "Copied" in self.dialog.status_label.text()

    def test_toggle_visibility_switches_between_mascot_and_hub(self):
        assert not self.dialog.isVisible()
        self.dialog.toggle_visibility()
        assert self.dialog.companion.isVisible()
        assert not self.dialog.isVisible()
        self.dialog.toggle_visibility()
        assert self.dialog.isVisible()
        assert not self.dialog.companion.isVisible()

    def test_reposition_positions_top_third(self):
        self.dialog.reposition()
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            dialog_geo = self.dialog.geometry()
            # Must be placed in the upper portion of screen
            assert dialog_geo.y() < geo.height() // 2
            assert dialog_geo.width() >= 520

    def test_stop_execution_signals_cancellation(self):
        mock_worker = MagicMock()
        self.dialog._active_worker = mock_worker
        self.dialog.stop_execution()

        assert self.dialog._cancel_event.is_set()
        mock_worker.requestInterruption.assert_called_once()
        assert "Stopped" in self.dialog.status_label.text()

    @patch("acoustic_pipeline.get_acoustic_pipeline")
    def test_speak_output_calls_acoustic_pipeline(self, mock_get_pipeline):
        mock_pipeline = MagicMock()
        mock_get_pipeline.return_value = mock_pipeline
        self.dialog.output_view.setPlainText("Jarvis speech test")
        self.dialog._speak_output()

        mock_pipeline.speak.assert_called_once_with("Jarvis speech test")
        assert "Speaking" in self.dialog.status_label.text()

    def test_on_voice_transcribed_populates_input(self):
        with patch.object(self.dialog, "submit") as mock_submit:
            self.dialog._on_voice_transcribed("what is the weather today")
            assert self.dialog.input_edit.text() == "what is the weather today"
            assert "Heard" in self.dialog.status_label.text()
            mock_submit.assert_called_once()

    def test_enter_key_does_not_close_dialog(self):
        """ANTI-FRAGILITY: QDialog must not close or accept when Key_Return is pressed."""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent
        self.dialog.show()
        assert self.dialog.isVisible()
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        self.dialog.keyPressEvent(event)
        assert self.dialog.isVisible()

    def test_keypad_enter_does_not_close_dialog(self):
        """ANTI-FRAGILITY: QDialog must not close or accept when Key_Enter (numpad) is pressed."""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent
        self.dialog.show()
        assert self.dialog.isVisible()
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Enter, Qt.KeyboardModifier.NoModifier)
        self.dialog.keyPressEvent(event)
        assert self.dialog.isVisible()

    def test_auto_default_disabled_on_all_buttons(self):
        """REGRESSION GUARD: In QDialog, push buttons must have autoDefault=False and isDefault=False

        so hitting Enter from an input field does not prematurely trigger the first button
        in the layout (such as the close button).
        """
        from PyQt6.QtWidgets import QPushButton
        buttons = self.dialog.findChildren(QPushButton)
        assert len(buttons) >= 6
        for btn in buttons:
            assert not btn.autoDefault(), f"Button '{btn.text()}' should have autoDefault=False"
            assert not btn.isDefault(), f"Button '{btn.text()}' should have isDefault=False"

    def test_dialog_accept_is_noop(self):
        """REGRESSION GUARD: Calling dialog.accept() directly must not hide the HUD overlay."""
        self.dialog.show()
        assert self.dialog.isVisible()
        self.dialog.accept()
        assert self.dialog.isVisible()

    def test_dialog_reject_routes_to_hide_hud(self):
        """Verify dialog.reject() properly closes/hides the HUD overlay."""
        self.dialog.show()
        assert self.dialog.isVisible()
        self.dialog.reject()
        assert not self.dialog.isVisible()

    def test_ctrl_enter_routes_to_send_to_main(self):
        """Verify Ctrl+Return key event triggers _send_to_main instead of submit."""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent
        with patch.object(self.dialog, "_send_to_main") as mock_send, patch.object(self.dialog, "submit") as mock_submit:
            event = QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Return,
                Qt.KeyboardModifier.ControlModifier,
            )
            self.dialog.keyPressEvent(event)
            mock_send.assert_called_once()
            mock_submit.assert_not_called()

    def test_enter_key_triggers_submit_when_input_not_focused(self):
        """Verify Enter triggers submit if focus is on non-input, non-button widget."""
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QKeyEvent
        self.dialog.output_view.setFocus()
        with patch.object(self.dialog, "submit") as mock_submit:
            event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
            self.dialog.keyPressEvent(event)
            mock_submit.assert_called_once()

    def test_close_event_halts_workers_cleanly(self):
        """Verify closeEvent cancels active worker and emits hud_closed signal."""
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = False
        self.dialog._active_worker = mock_worker

        closed_emitted = []
        self.dialog.hud_closed.connect(lambda: closed_emitted.append(True))
        self.dialog.close()

        assert self.dialog._cancel_event.is_set()
        assert closed_emitted == [True]
        assert not self.dialog._state_reset_timer.isActive()
        assert not self.dialog.companion._roam_timer.isActive()


class TestMiniHudAIFallback(unittest.TestCase):
    """Verify fallback direct AI execution and reply handling."""

    def setUp(self):
        self.dialog = _create_test_dialog(controller=None)

    def tearDown(self):
        self.dialog.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()

    @patch("ai_base.get_provider")
    def test_direct_ai_worker_chat_completion_regression(self, mock_get_prov):
        """REGRESSION GUARD for provider chat_completion invocation in
        ``tabs.mini_hud.MiniHudDialog._execute_ai_fallback`` (lines 825-845 of ``tabs/mini_hud.py``).

        Invariant: AIProvider interface uses chat_completion, not generate_response.
        DirectAIWorker must call chat_completion and propagate results via reply_signal.
        """
        mock_prov = MagicMock()
        mock_prov.chat_completion.return_value = {"content": "Direct response from local LLM"}
        mock_get_prov.return_value = mock_prov

        with patch.object(self.dialog, "_on_ai_reply") as mock_reply:
            self.dialog._execute_ai_fallback("Test query")
            worker = self.dialog._active_worker
            assert worker is not None
            worker.run()
            mock_reply.assert_called_once()
            args, _ = mock_reply.call_args
            assert args[0].get("final") == "Direct response from local LLM"


class TestHudSignalParameterAbsorption(unittest.TestCase):
    """Verify slots absorb QPushButton.clicked boolean argument without TypeError."""

    def test_toggle_mini_hud_accepts_clicked_argument(self):
        from app_ui import AppUIMixin
        mixin = AppUIMixin()
        mixin._do_toggle_hud = MagicMock()
        # QPushButton.clicked emits clicked(bool)
        mixin._toggle_mini_hud(False)
        mixin._do_toggle_hud.assert_called_once_with(False)


