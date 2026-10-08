"""Tests for module-level functions in app_hotkeys.py — _get_keyboard_module, _safe_log_to_audit."""
import sys
import unittest
from unittest.mock import MagicMock, patch


class TestGetKeyboardModule(unittest.TestCase):
    """Module-level _get_keyboard_module() — 3 code paths."""

    def test_disabled_returns_none(self):
        """KOKERTECH_DISABLE_KEYBOARD=1 → returns None."""
        import app_hotkeys
        with patch("app_hotkeys._KOKERTECH_DISABLE_KEYBOARD", True):
            result = app_hotkeys._get_keyboard_module()
        self.assertIsNone(result)

    def test_success_returns_keyboard(self):
        """import keyboard succeeds → returns keyboard module."""
        import app_hotkeys
        mock_kb = MagicMock()
        with patch("app_hotkeys._KOKERTECH_DISABLE_KEYBOARD", False):
            with patch.dict("sys.modules", {"keyboard": mock_kb}):
                result = app_hotkeys._get_keyboard_module()
        self.assertIs(result, mock_kb)

    def test_import_error_returns_none(self):
        """import keyboard fails → returns None."""
        import app_hotkeys
        import builtins
        real_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == "keyboard":
                raise ImportError("No module named keyboard")
            return real_import(name, *args, **kwargs)
        with patch("app_hotkeys._KOKERTECH_DISABLE_KEYBOARD", False):
            with patch("builtins.__import__", side_effect=mock_import):
                result = app_hotkeys._get_keyboard_module()
                self.assertIsNone(result)


class TestSafeLogToAudit(unittest.TestCase):
    """AppHotkeysMixin._safe_log_to_audit() — 3 code paths."""

    def setUp(self):
        from app_hotkeys import AppHotkeysMixin
        self.obj = AppHotkeysMixin()
        self.obj.file_logger = MagicMock()
        self.obj.log_to_audit = MagicMock()

    def test_uses_log_to_audit(self):
        """log_to_audit available → called directly."""
        self.obj._safe_log_to_audit("test msg")
        self.obj.log_to_audit.assert_called_once_with("test msg")
        self.obj.file_logger.info.assert_not_called()

    def test_runtime_error_from_log_to_audit_returns_early(self):
        """log_to_audit raises RuntimeError → returns early, file_logger NOT called."""
        self.obj.log_to_audit.side_effect = RuntimeError("wrapped C/C++")
        self.obj._safe_log_to_audit("rte msg")
        self.obj.log_to_audit.assert_called_once_with("rte msg")
        self.obj.file_logger.info.assert_not_called()

    def test_noop_when_log_to_audit_missing(self):
        """log_to_audit attribute missing → falls back to file_logger."""
        del self.obj.log_to_audit
        self.obj._safe_log_to_audit("no audit attr")
        self.obj.file_logger.info.assert_called_once_with("no audit attr")

    def test_noop_when_no_file_logger_either(self):
        """Both log_to_audit and file_logger missing → no crash."""
        del self.obj.log_to_audit
        if hasattr(self.obj, 'file_logger'):
            del self.obj.file_logger
        self.obj._safe_log_to_audit("no loggers at all")
