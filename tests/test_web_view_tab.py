"""Tests for tabs/web_view_tab.py -- Web View tab mixin (UI-only)."""
import unittest
from unittest.mock import patch, MagicMock

class TestWebViewTabMixin(unittest.TestCase):
    def setUp(self):
        self._p = patch("tabs.web_view_tab.QTimer", return_value=MagicMock())
        self._p.start()
        self._pe = patch("tabs.web_view_tab._add_qt6_bin_to_path")
        self._pe.start()
    def tearDown(self):
        self._p.stop()
        self._pe.stop()
    def _mk(self, obj):
        with patch("tabs.web_view_tab.CONFIG", {"web_server_enabled": False, "web_server_port": 5050}):
            obj._tab = obj.create_web_view_tab()

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.web_view_tab import WebViewTabMixin
        obj = WebViewTabMixin()
        self._mk(obj)
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "web_enabled_cb"))
        self.assertTrue(hasattr(obj, "web_port_spin"))
        self.assertTrue(hasattr(obj, "web_status_lbl"))
        self.assertTrue(hasattr(obj, "btn_web_start"))
        self.assertTrue(hasattr(obj, "btn_web_stop"))
        self.assertTrue(hasattr(obj, "web_url_input"))
        self.assertTrue(hasattr(obj, "web_page_status"))

    def test_port_spin_defaults(self):
        from tabs.web_view_tab import WebViewTabMixin
        obj = WebViewTabMixin()
        self._mk(obj)
        self.assertEqual(obj.web_port_spin.minimum(), 1024)
        self.assertEqual(obj.web_port_spin.maximum(), 65535)
        self.assertEqual(obj.web_port_spin.value(), 5050)

    def test_stop_button_disabled_initially(self):
        from tabs.web_view_tab import WebViewTabMixin
        obj = WebViewTabMixin()
        self._mk(obj)
        self.assertTrue(obj.btn_web_start.isEnabled())
        self.assertFalse(obj.btn_web_stop.isEnabled())

    def test_nav_buttons_disabled_initially(self):
        from tabs.web_view_tab import WebViewTabMixin
        obj = WebViewTabMixin()
        self._mk(obj)
        self.assertFalse(obj.btn_back.isEnabled())
        self.assertFalse(obj.btn_forward.isEnabled())

    def test_url_input_has_default(self):
        from tabs.web_view_tab import WebViewTabMixin
        obj = WebViewTabMixin()
        self._mk(obj)
        self.assertIn("5050", obj.web_url_input.text())

    def test_server_enabled_cb_unchecked_default(self):
        from tabs.web_view_tab import WebViewTabMixin
        obj = WebViewTabMixin()
        self._mk(obj)
        self.assertFalse(obj.web_enabled_cb.isChecked())

    def test_poll_server_status_no_proc(self):
        from tabs.web_view_tab import WebViewTabMixin
        obj = WebViewTabMixin()
        self._mk(obj)
        obj._web_server_proc = None
        obj._server_poll_timer = MagicMock()
        obj._poll_server_status()
        obj._server_poll_timer.stop.assert_called_once()

    def test_navigate_home_server_not_running(self):
        from tabs.web_view_tab import WebViewTabMixin
        obj = WebViewTabMixin()
        self._mk(obj)
        obj._web_server_proc = None
        obj._navigate_home()
        self.assertIn("Server not running", obj.web_page_status.text())

    def test_load_web_view_no_engine(self):
        from tabs.web_view_tab import WebViewTabMixin
        obj = WebViewTabMixin()
        self._mk(obj)
        obj._shutting_down = False
        obj._web_engine_available = False
        obj._load_web_view()
        self.assertIn("not available", obj.web_page_status.text())

    def test_load_web_view_shutting_down(self):
        from tabs.web_view_tab import WebViewTabMixin
        obj = WebViewTabMixin()
        self._mk(obj)
        obj._shutting_down = True
        obj._web_engine_available = True
        prev = obj.web_page_status.text()
        obj._load_web_view()
        self.assertEqual(obj.web_page_status.text(), prev)  # unchanged

    def test_web_view_tab_widgets_are_real_qt_objects(self):
        """create_web_view_tab() builds a QWidget with all expected
        UI elements as real Qt widgets."""
        from PyQt6.QtCore import QObject
        from PyQt6.QtWidgets import QWidget, QCheckBox, QSpinBox, QLabel, QLineEdit, QPushButton
        from tabs.web_view_tab import WebViewTabMixin
        class _T(QObject, WebViewTabMixin):
            pass
        obj = _T()
        with patch("tabs.web_view_tab.QTimer", return_value=MagicMock()):
            with patch("tabs.web_view_tab._add_qt6_bin_to_path"):
                with patch("tabs.web_view_tab.CONFIG", {"web_server_enabled": False, "web_server_port": 5050}):
                    tab = obj.create_web_view_tab()
        self.assertIsInstance(tab, QWidget,
            msg="REGRESSION: create_web_view_tab must return a QWidget")
        self.assertIsInstance(obj.web_enabled_cb, QCheckBox,
            msg="REGRESSION: web_enabled_cb should be a QCheckBox")
        self.assertIsInstance(obj.web_port_spin, QSpinBox,
            msg="REGRESSION: web_port_spin should be a QSpinBox")
        self.assertIsInstance(obj.web_status_lbl, QLabel,
            msg="REGRESSION: web_status_lbl should be a QLabel")
        self.assertIsInstance(obj.web_url_input, QLineEdit,
            msg="REGRESSION: web_url_input should be a QLineEdit")
        self.assertIsInstance(obj.web_page_status, QLabel,
            msg="REGRESSION: web_page_status should be a QLabel")
        self.assertIsInstance(obj.btn_web_start, QPushButton,
            msg="REGRESSION: btn_web_start should be a QPushButton")
        self.assertIsInstance(obj.btn_web_stop, QPushButton,
            msg="REGRESSION: btn_web_stop should be a QPushButton")
        self.assertTrue(obj.btn_web_start.isEnabled(),
            msg="REGRESSION: start button should be enabled initially")
        self.assertFalse(obj.btn_web_stop.isEnabled(),
            msg="REGRESSION: stop button should be disabled initially")

if __name__ == "__main__":
    unittest.main()
