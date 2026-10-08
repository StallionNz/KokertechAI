"""Tests for computer_use.py - pyautogui desktop automation with safety controls."""

import unittest
from unittest.mock import MagicMock, patch



class TestSafetyAndConfirm(unittest.TestCase):
    """Tests for module-level safety/confirm helpers."""

    def setUp(self):
        import computer_use
        computer_use._safety_enabled = True
        computer_use._confirm_callback = None

    def test_set_safety_false(self):
        import computer_use
        computer_use.set_safety(False)
        assert computer_use._safety_enabled is False

    def test_set_safety_true(self):
        import computer_use
        computer_use.set_safety(True)
        assert computer_use._safety_enabled is True

    def test_set_confirm_callback(self):
        import computer_use
        cb = MagicMock()
        computer_use.set_confirm_callback(cb)
        assert computer_use._confirm_callback is cb

    def test_confirm_safety_disabled_returns_true(self):
        import computer_use
        computer_use.set_safety(False)
        assert computer_use._confirm("any action") is True

    def test_confirm_callback_returns_true(self):
        import computer_use
        cb = MagicMock(return_value=True)
        computer_use.set_confirm_callback(cb)
        assert computer_use._confirm("click") is True
        cb.assert_called_once_with("click")

    def test_confirm_callback_returns_false(self):
        import computer_use
        cb = MagicMock(return_value=False)
        computer_use.set_confirm_callback(cb)
        assert computer_use._confirm("click") is False

    def test_confirm_no_callback_warns_and_returns_false(self):
        import computer_use
        with patch("computer_use.logger.warning") as mock_warn:
            result = computer_use._confirm("click")
        assert result is False
        mock_warn.assert_called_once()


class TestCheckPyautogui(unittest.TestCase):
    """Tests for _check_pyautogui helper."""

    def test_returns_module_when_available(self):
        import computer_use
        with patch("computer_use._pyautogui", MagicMock()):
            result = computer_use._check_pyautogui()
            assert result is not None

    def test_raises_importerror_when_none(self):
        import computer_use
        with patch("computer_use._pyautogui", None):
            with self.assertRaises(ImportError):
                computer_use._check_pyautogui()


class BaseActionTest(unittest.TestCase):
    """Base class with common helpers for action function tests."""

    def setUp(self):
        import computer_use
        computer_use._safety_enabled = False  # no confirm needed
        computer_use._confirm_callback = None
        self.mock_pa = MagicMock()
        self.pa_patcher = patch("computer_use._pyautogui", self.mock_pa)
        self.pa_patcher.start()

    def tearDown(self):
        self.pa_patcher.stop()


class TestClick(BaseActionTest):
    def test_success(self):
        import computer_use as cu
        result = cu.click(100, 200, button="left")
        self.mock_pa.click.assert_called_once_with(100, 200, button="left")
        assert "Clicked" in result

    def test_blocked_by_safety(self):
        import computer_use as cu
        cu._safety_enabled = True
        cb = MagicMock(return_value=False)
        cu.set_confirm_callback(cb)
        result = cu.click(100, 200)
        assert "blocked" in result

    def test_no_pyautogui(self):
        import computer_use as cu
        with patch("computer_use._pyautogui", None):
            result = cu.click(100, 200)
        assert "not installed" in result

    def test_exception(self):
        import computer_use as cu
        self.mock_pa.click.side_effect = RuntimeError("permission denied")
        result = cu.click(100, 200)
        assert "failed" in result.lower()


class TestTypeText(BaseActionTest):
    def test_success(self):
        import computer_use as cu
        result = cu.type_text("hello world")
        self.mock_pa.typewrite.assert_called_once_with("hello world", interval=0.05)
        assert "Typed" in result

    def test_blocked(self):
        import computer_use as cu
        cu._safety_enabled = True
        cb = MagicMock(return_value=False)
        cu.set_confirm_callback(cb)
        result = cu.type_text("secret")
        assert "blocked" in result

    def test_no_pyautogui(self):
        import computer_use as cu
        with patch("computer_use._pyautogui", None):
            result = cu.type_text("hello")
        assert "not installed" in result

    def test_exception(self):
        import computer_use as cu
        self.mock_pa.typewrite.side_effect = RuntimeError("keyboard error")
        result = cu.type_text("hello")
        assert "failed" in result.lower()


class TestScroll(BaseActionTest):
    def test_success(self):
        import computer_use as cu
        result = cu.scroll(5)
        self.mock_pa.scroll.assert_called_once_with(5)
        assert "Scrolled" in result

    def test_blocked(self):
        import computer_use as cu
        cu._safety_enabled = True
        cb = MagicMock(return_value=False)
        cu.set_confirm_callback(cb)
        result = cu.scroll(5)
        assert "blocked" in result

    def test_no_pyautogui(self):
        import computer_use as cu
        with patch("computer_use._pyautogui", None):
            result = cu.scroll(5)
        assert "not installed" in result

    def test_exception(self):
        import computer_use as cu
        self.mock_pa.scroll.side_effect = RuntimeError("scroll error")
        result = cu.scroll(5)
        assert "failed" in result.lower()


class TestMoveTo(BaseActionTest):
    def test_success(self):
        import computer_use as cu
        result = cu.move_to(400, 300)
        self.mock_pa.moveTo.assert_called_once_with(400, 300)
        assert "Moved" in result

    def test_blocked(self):
        import computer_use as cu
        cu._safety_enabled = True
        cb = MagicMock(return_value=False)
        cu.set_confirm_callback(cb)
        result = cu.move_to(100, 100)
        assert "blocked" in result

    def test_no_pyautogui(self):
        import computer_use as cu
        with patch("computer_use._pyautogui", None):
            result = cu.move_to(100, 100)
        assert "not installed" in result

    def test_exception(self):
        import computer_use as cu
        self.mock_pa.moveTo.side_effect = RuntimeError("no mouse")
        result = cu.move_to(100, 100)
        assert "failed" in result.lower()


class TestScreenshot(BaseActionTest):
    def test_default_path(self):
        import computer_use as cu
        import os
        result = cu.screenshot()
        expected = os.path.join(os.getcwd(), "kokertech_screenshot.png")
        self.mock_pa.screenshot.assert_called_once_with(expected)
        assert "Screenshot" in result

    def test_custom_path(self):
        import computer_use as cu
        result = cu.screenshot("/tmp/test.png")
        self.mock_pa.screenshot.assert_called_once_with("/tmp/test.png")
        assert "Screenshot" in result

    def test_no_pyautogui(self):
        import computer_use as cu
        with patch("computer_use._pyautogui", None):
            result = cu.screenshot()
        assert "not installed" in result

    def test_exception(self):
        import computer_use as cu
        self.mock_pa.screenshot.side_effect = RuntimeError("display error")
        result = cu.screenshot()
        assert "failed" in result.lower()


class TestHotkey(BaseActionTest):
    def test_success(self):
        import computer_use as cu
        result = cu.hotkey("ctrl", "c")
        self.mock_pa.hotkey.assert_called_once_with("ctrl", "c")
        assert "Pressed" in result

    def test_blocked(self):
        import computer_use as cu
        cu._safety_enabled = True
        cb = MagicMock(return_value=False)
        cu.set_confirm_callback(cb)
        result = cu.hotkey("ctrl", "v")
        assert "blocked" in result

    def test_no_pyautogui(self):
        import computer_use as cu
        with patch("computer_use._pyautogui", None):
            result = cu.hotkey("ctrl", "c")
        assert "not installed" in result

    def test_exception(self):
        import computer_use as cu
        self.mock_pa.hotkey.side_effect = RuntimeError("key error")
        result = cu.hotkey("ctrl", "c")
        assert "failed" in result.lower()


class TestGetPosition(BaseActionTest):
    def test_success(self):
        import computer_use as cu
        pos = MagicMock()
        pos.x = 500
        pos.y = 300
        self.mock_pa.position.return_value = pos
        result = cu.get_position()
        self.mock_pa.position.assert_called_once()
        assert "500" in result
        assert "300" in result

    def test_no_pyautogui(self):
        import computer_use as cu
        with patch("computer_use._pyautogui", None):
            result = cu.get_position()
        assert "not installed" in result

    def test_exception(self):
        import computer_use as cu
        self.mock_pa.position.side_effect = RuntimeError("query failed")
        result = cu.get_position()
        assert "failed" in result.lower()


if __name__ == "__main__":
    unittest.main()
