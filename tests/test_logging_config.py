"""Unit tests for logging_config.py - LogLevel, set/get_log_level, _LevelFilteredLogger, get_logger, and default_logger."""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

class TestLogLevel(unittest.TestCase):
    """LogLevel IntEnum values."""

    def test_debug_value(self):
        from logging_config import LogLevel
        self.assertEqual(LogLevel.DEBUG, 10)

    def test_info_value(self):
        from logging_config import LogLevel
        self.assertEqual(LogLevel.INFO, 20)

    def test_ok_value(self):
        from logging_config import LogLevel
        self.assertEqual(LogLevel.OK, 25)

    def test_warning_value(self):
        from logging_config import LogLevel
        self.assertEqual(LogLevel.WARNING, 30)

    def test_error_value(self):
        from logging_config import LogLevel
        self.assertEqual(LogLevel.ERROR, 40)

    def test_critical_value(self):
        from logging_config import LogLevel
        self.assertEqual(LogLevel.CRITICAL, 50)

class TestSetGetLogLevel(unittest.TestCase):
    """set_log_level() and get_log_level()."""

    def setUp(self):
        from logging_config import reset_loggers, set_log_level, LogLevel
        reset_loggers()
        set_log_level(LogLevel.INFO)

    def test_set_with_valid_level(self):
        from logging_config import set_log_level, get_log_level, LogLevel
        set_log_level(LogLevel.DEBUG)
        self.assertEqual(get_log_level(), LogLevel.DEBUG)

    def test_set_with_invalid_level_falls_back_to_info(self):
        from logging_config import set_log_level, get_log_level, LogLevel
        set_log_level("invalid_level")
        self.assertEqual(get_log_level(), LogLevel.INFO)

    def test_get_log_level_returns_current(self):
        from logging_config import get_log_level, LogLevel
        level = get_log_level()
        self.assertIsInstance(level, LogLevel)

class TestLevelFilteredLogger(unittest.TestCase):
    """_LevelFilteredLogger wraps KokertechLogger with level filtering."""

    def setUp(self):
        from logging_config import reset_loggers, set_log_level, LogLevel
        reset_loggers()
        set_log_level(LogLevel.INFO)

    def _make_logger(self, name=""):
        from logging_config import _LevelFilteredLogger
        inner = MagicMock()
        return _LevelFilteredLogger(inner, name=name)

    def test_debug_suppressed_at_info_level(self):
        """debug() suppressed at INFO level. Covers lines 58-59."""
        logger = self._make_logger()
        logger.debug("msg")
        logger._inner.info.assert_not_called()

    def test_debug_passes_at_debug_level(self):
        from logging_config import set_log_level, LogLevel
        set_log_level(LogLevel.DEBUG)
        logger = self._make_logger()
        logger.debug("dbg")
        logger._inner.info.assert_called_once_with("[DEBUG] dbg")

    def test_info_passes_at_info_level(self):
        logger = self._make_logger()
        logger.info("test")
        logger._inner.info.assert_called_once_with("test")

    def test_info_with_name_prefix(self):
        logger = self._make_logger(name="Mod")
        logger.info("msg")
        logger._inner.info.assert_called_once_with("[Mod] msg")

    def test_ok_passes_at_ok_level(self):
        """ok() passes at OK level. Covers lines 67-68."""
        logger = self._make_logger()
        logger.ok("done")
        logger._inner.ok.assert_called_once_with("done")

    def test_ok_suppressed_at_warning_level(self):
        from logging_config import set_log_level, LogLevel
        set_log_level(LogLevel.WARNING)
        logger = self._make_logger()
        logger.ok("nope")
        logger._inner.ok.assert_not_called()

    def test_warning_passes_at_warning_level(self):
        logger = self._make_logger()
        logger.warning("warn")
        logger._inner.warning.assert_called_once_with("warn")

    def test_warning_suppressed_at_error_level(self):
        from logging_config import set_log_level, LogLevel
        set_log_level(LogLevel.ERROR)
        logger = self._make_logger()
        logger.warning("nope")
        logger._inner.warning.assert_not_called()

    def test_error_passes_at_error_level(self):
        logger = self._make_logger()
        logger.error("err")
        logger._inner.error.assert_called_once_with("err", None)

    def test_error_with_exc_info(self):
        logger = self._make_logger()
        exc = (ValueError, ValueError("x"), None)
        logger.error("err", exc_info=exc)
        logger._inner.error.assert_called_once_with("err", exc)

    def test_log_session_start_delegates(self):
        """log_session_start() delegates. Covers line 79."""
        logger = self._make_logger()
        logger.log_session_start()
        logger._inner.log_session_start.assert_called_once_with()

    def test_log_session_end_delegates(self):
        """log_session_end() delegates. Covers line 82."""
        logger = self._make_logger()
        logger.log_session_end()
        logger._inner.log_session_end.assert_called_once_with()

    def test_log_path_delegates(self):
        """log_path property delegates. Covers line 86."""
        logger = self._make_logger()
        fake = r"C:\tmp\t.log"
        type(logger._inner).log_path = PropertyMock(return_value=fake)
        self.assertEqual(logger.log_path, fake)

    def test_should_log_unknown_defaults_to_true(self):
        from logging_config import _LevelFilteredLogger
        logger = _LevelFilteredLogger(MagicMock())
        self.assertTrue(logger._should_log("unknown"))

class TestGetLogger(unittest.TestCase):
    """get_logger() factory."""

    def setUp(self):
        from logging_config import reset_loggers
        reset_loggers()

    def test_returns_level_filtered_logger(self):
        from logging_config import get_logger, _LevelFilteredLogger
        self.assertIsInstance(get_logger(), _LevelFilteredLogger)

    def test_caching_same_key(self):
        from logging_config import get_logger
        a = get_logger(name="x")
        b = get_logger(name="x")
        self.assertIs(a, b)

    def test_caching_different_name(self):
        from logging_config import get_logger
        a = get_logger(name="a")
        b = get_logger(name="b")
        self.assertIsNot(a, b)

    def test_custom_log_path(self):
        from logging_config import get_logger
        a = get_logger(log_path=r"C:\tmp\c.log")
        b = get_logger(log_path=r"C:\tmp\c.log")
        self.assertIs(a, b)

    def test_different_log_path(self):
        from logging_config import get_logger
        a = get_logger(log_path=r"C:\tmp\a.log")
        b = get_logger(log_path=r"C:\tmp\b.log")
        self.assertIsNot(a, b)

class TestResetLoggers(unittest.TestCase):
    """reset_loggers() clears the cache."""

    def setUp(self):
        from logging_config import reset_loggers
        reset_loggers()

    def test_clears_cache(self):
        from logging_config import get_logger, reset_loggers
        a = get_logger(name="t")
        reset_loggers()
        b = get_logger(name="t")
        self.assertIsNot(a, b)

    def test_safe_multiple_calls(self):
        from logging_config import reset_loggers
        reset_loggers()
        reset_loggers()

class TestDefaultLogger(unittest.TestCase):
    """Module-level default_logger export."""

    def test_is_accessible(self):
        from logging_config import default_logger, _LevelFilteredLogger
        self.assertIsInstance(default_logger, _LevelFilteredLogger)

    def test_can_log(self):
        from logging_config import default_logger
        default_logger.info("ok")

    def test_is_same_type_as_get_logger(self):
        from logging_config import default_logger, get_logger, _LevelFilteredLogger
        self.assertIsInstance(default_logger, _LevelFilteredLogger)
        self.assertIsInstance(get_logger(), _LevelFilteredLogger)

if __name__ == "__main__":
    unittest.main()
