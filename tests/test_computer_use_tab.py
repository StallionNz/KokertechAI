"""Tests for tabs/computer_use_tab.py -- Computer Use tab mixin."""
import unittest
from unittest.mock import patch, MagicMock


class TestComputerUseTabMixin(unittest.TestCase):
    """Tests for ComputerUseTabMixin -- safety controls + actions."""

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "chk_safety"))
        self.assertTrue(hasattr(obj, "cu_x"))
        self.assertTrue(hasattr(obj, "cu_y"))
        self.assertTrue(hasattr(obj, "cu_log"))

    def test_create_tab_safety_checked_by_default(self):
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        self.assertTrue(obj.chk_safety.isChecked())

    def test_create_tab_spinbox_ranges(self):
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        self.assertEqual(obj.cu_x.minimum(), 0)
        self.assertEqual(obj.cu_x.maximum(), 3840)
        self.assertEqual(obj.cu_y.minimum(), 0)
        self.assertEqual(obj.cu_y.maximum(), 2160)
        self.assertEqual(obj.cu_x.value(), 500)
        self.assertEqual(obj.cu_y.value(), 500)

    def test_create_tab_log_is_readonly(self):
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        self.assertTrue(obj.cu_log.isReadOnly())

    def test_toggle_safety_enabled(self):
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        with patch("computer_use.set_safety") as mock_set:
            obj._toggle_safety(True)
        mock_set.assert_called_once_with(True)
        self.assertIn("Safety enabled", obj.cu_log.toPlainText())

    def test_toggle_safety_disabled(self):
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        with patch("computer_use.set_safety") as mock_set:
            obj._toggle_safety(False)
        mock_set.assert_called_once_with(False)
        self.assertIn("Safety DISABLED", obj.cu_log.toPlainText())

    def test_cu_move_calls_computer_use(self):
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        obj.cu_x.setValue(100)
        obj.cu_y.setValue(200)
        with patch("computer_use.move_to", return_value="Moved OK") as mock_move:
            obj._cu_move()
        mock_move.assert_called_once_with(100, 200)
        self.assertIn("Moved OK", obj.cu_log.toPlainText())

    def test_cu_click_calls_computer_use(self):
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        obj.cu_x.setValue(300)
        obj.cu_y.setValue(400)
        with patch("computer_use.click", return_value="Clicked OK") as mock_click:
            obj._cu_click()
        mock_click.assert_called_once_with(300, 400)
        self.assertIn("Clicked OK", obj.cu_log.toPlainText())

    def test_cu_scroll_calls_computer_use(self):
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        with patch("computer_use.scroll", return_value="Scrolled OK") as mock_scroll:
            obj._cu_scroll(5)
        mock_scroll.assert_called_once_with(5)
        self.assertIn("Scrolled OK", obj.cu_log.toPlainText())

    def test_cu_screenshot_calls_computer_use(self):
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        with patch("computer_use.screenshot", return_value="Screenshot OK") as mock_ss:
            obj._cu_screenshot()
        mock_ss.assert_called_once()
        self.assertIn("Screenshot OK", obj.cu_log.toPlainText())

    def test_cu_capture_analyze_no_screenshot(self):
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        with patch("computer_use.screenshot", return_value="Failed"):
            obj._cu_capture_analyze()
        log_text = obj.cu_log.toPlainText()
        self.assertIn("Failed", log_text)
        self.assertNotIn("Analyzing", log_text)

    def test_cu_capture_analyze_file_not_found(self):
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        p1 = patch("computer_use.screenshot", return_value="✅ Screenshot saved to /tmp/test.png")
        p2 = patch("os.path.exists", return_value=False)
        with p1, p2:
            obj._cu_capture_analyze()
        log_text = obj.cu_log.toPlainText()
        self.assertIn("not found", log_text)

    def test_cu_confirm_returns_true_when_yes(self):
        """_cu_confirm returns True when QMessageBox.question returns Yes."""
        from tabs.computer_use_tab import ComputerUseTabMixin
        from PyQt6.QtWidgets import QMessageBox
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        # QMessageBox is imported inside _cu_confirm, not at module level.
        # Patch the canonical PyQt6 location directly.
        with patch("PyQt6.QtWidgets.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            result = obj._cu_confirm("Move mouse to (500, 500)")
        self.assertTrue(result,
            msg="REGRESSION: _cu_confirm should return True when Yes clicked")

    def test_cu_confirm_returns_false_when_no(self):
        """_cu_confirm returns False when QMessageBox.question returns No."""
        from tabs.computer_use_tab import ComputerUseTabMixin
        from PyQt6.QtWidgets import QMessageBox
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        with patch("PyQt6.QtWidgets.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.No):
            result = obj._cu_confirm("Delete file")
        self.assertFalse(result,
            msg="REGRESSION: _cu_confirm should return False when No clicked")

    def test_cu_capture_analyze_full_success(self):
        """_cu_capture_analyze success path: screenshot taken, file exists,
        VisionAnalyzer runs and returns analysis."""
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        p1 = patch("computer_use.screenshot",
                    return_value="✅ Screenshot saved to /tmp/test.png")
        p2 = patch("os.path.exists", return_value=True)
        p3 = patch("copilot_features.VisionAnalyzer")
        with p1, p2, p3 as mock_va_cls:
            mock_va = MagicMock()
            mock_va_cls.return_value = mock_va
            mock_va.analyze.return_value = (
                "The screen shows a desktop with a terminal window."
            )
            obj._cu_capture_analyze()
        log_text = obj.cu_log.toPlainText()
        self.assertIn("Analyzing screenshot", log_text,
            msg="REGRESSION: should log 'Analyzing screenshot' on success")
        self.assertIn("The screen shows", log_text,
            msg="REGRESSION: should log vision analysis result")

    def test_cu_capture_analyze_vision_analyzer_raises_exception(self):
        """_cu_capture_analyze catches a VisionAnalyzer failure.

        When ``analyzer.analyze()`` raises a caught error type
        (TimeoutError/OSError here), the handler logs the failure.
        """
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        p1 = patch("computer_use.screenshot",
                    return_value="✅ Screenshot saved to /tmp/test.png")
        p2 = patch("os.path.exists", return_value=True)
        p3 = patch("copilot_features.VisionAnalyzer")
        with p1, p2, p3 as mock_va_cls:
            mock_va = MagicMock()
            mock_va_cls.return_value = mock_va
            mock_va.analyze.side_effect = TimeoutError("API timeout")
            obj._cu_capture_analyze()
        log_text = obj.cu_log.toPlainText()
        self.assertIn("Analysis failed", log_text,
            msg="REGRESSION: should log 'Analysis failed' on TimeoutError")
        self.assertIn("API timeout", log_text,
            msg="REGRESSION: should include exception message")

    def test_cu_capture_analyze_vision_import_error(self):
        """_cu_capture_analyze catches ImportError from VisionAnalyzer.

        When VisionAnalyzer construction raises ImportError (patched via
        ``side_effect``), the ``except ImportError`` handler (line ~168)
        logs the unavailability message.
        """
        from tabs.computer_use_tab import ComputerUseTabMixin
        obj = ComputerUseTabMixin()
        with patch("computer_use.set_confirm_callback"):
            obj._tab = obj.create_computer_use_tab()
        p1 = patch("computer_use.screenshot",
                    return_value="✅ Screenshot saved to /tmp/test.png")
        p2 = patch("os.path.exists", return_value=True)
        p3 = patch("copilot_features.VisionAnalyzer",
                    side_effect=ImportError("No module 'copilot_features'"))
        with p1, p2, p3:
            obj._cu_capture_analyze()
        log_text = obj.cu_log.toPlainText()
        self.assertIn("Vision analysis unavailable", log_text,
            msg="REGRESSION: should log 'Vision analysis unavailable' on ImportError")

    def test_computer_use_tab_widgets_are_real_qt_objects(self):
        """create_computer_use_tab() builds a QWidget with all expected
        UI elements as real Qt widgets."""
        from PyQt6.QtCore import QObject
        from PyQt6.QtWidgets import QWidget, QCheckBox, QSpinBox, QTextEdit
        from tabs.computer_use_tab import ComputerUseTabMixin
        class _T(QObject, ComputerUseTabMixin):
            pass
        obj = _T()
        with patch("computer_use.set_confirm_callback"):
            tab = obj.create_computer_use_tab()
        self.assertIsInstance(tab, QWidget,
            msg="REGRESSION: create_computer_use_tab must return a QWidget")
        self.assertIsInstance(obj.chk_safety, QCheckBox,
            msg="REGRESSION: chk_safety should be a QCheckBox")
        self.assertIsInstance(obj.cu_x, QSpinBox,
            msg="REGRESSION: cu_x should be a QSpinBox")
        self.assertIsInstance(obj.cu_y, QSpinBox,
            msg="REGRESSION: cu_y should be a QSpinBox")
        self.assertIsInstance(obj.cu_log, QTextEdit,
            msg="REGRESSION: cu_log should be a QTextEdit")
        self.assertTrue(obj.chk_safety.isChecked(),
            msg="REGRESSION: safety checkbox should be checked by default")

if __name__ == "__main__":
    unittest.main()
