"""
logging_config.py — Centralized logging configuration for KokertechAI.

Provides a factory for getting configured KokertechLogger instances
with log-level filtering, consistent formatting, and centralized path management.
"""

from enum import IntEnum

from config import LOG_FILE
from kokertech_logger import KokertechLogger


class LogLevel(IntEnum):
    """Standard log levels, higher = more severe."""
    DEBUG = 10
    INFO = 20
    OK = 25
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


# Global default: INFO level
_current_level = LogLevel.INFO
def set_log_level(level):
    """Set the global minimum log level. Messages below this level are suppressed."""
    global _current_level
    _current_level = level if isinstance(level, LogLevel) else LogLevel.INFO


def get_log_level():
    """Return the current global log level."""
    return _current_level


class _LevelFilteredLogger:
    """
    Wraps KokertechLogger with level filtering.
    Only passes through messages at or above the configured LogLevel.
    """
    _level_map = {
        "debug": LogLevel.DEBUG,
        "info": LogLevel.INFO,
        "ok": LogLevel.OK,
        "warning": LogLevel.WARNING,
        "error": LogLevel.ERROR,
    }

    def __init__(self, inner, name=""):
        self._inner = inner
        self._name = name

    def _should_log(self, method_name):
        return self._level_map.get(method_name, LogLevel.INFO) >= _current_level

    def _format_msg(self, message, args):
        if args:
            try:
                return message % args
            except (TypeError, ValueError):
                return f"{message} {' '.join(str(a) for a in args)}"
        return message

    def debug(self, message, *args):
        if self._should_log("debug"):
            formatted = self._format_msg(message, args)
            self._inner.info(f"[DEBUG] {formatted}")

    def info(self, message, *args):
        if self._should_log("info"):
            formatted = self._format_msg(message, args)
            prefix = f"[{self._name}] " if self._name else ""
            self._inner.info(f"{prefix}{formatted}")

    def ok(self, message, *args):
        if self._should_log("ok"):
            formatted = self._format_msg(message, args)
            self._inner.ok(formatted)

    def warning(self, message, *args):
        if self._should_log("warning"):
            formatted = self._format_msg(message, args)
            self._inner.warning(formatted)

    def error(self, message, *args, exc_info=None):
        if self._should_log("error"):
            formatted = self._format_msg(message, args)
            self._inner.error(formatted, exc_info)

    def log_session_start(self):
        self._inner.log_session_start()

    def log_session_end(self):
        self._inner.log_session_end()

    @property
    def log_path(self):
        return self._inner.log_path


# ---- Logger cache ----
_loggers = {}


def get_logger(name="", log_path=None):
    """
    Return a configured KokertechLogger instance.

    Parameters
    ----------
    name : str
        Optional name prefix shown in log messages for context.
    log_path : str or None
        Custom log file path. Defaults to config.LOG_FILE.

    Returns
    -------
    _LevelFilteredLogger
        Logger wrapper with level filtering.
    """
    cache_key = f"{name}:{log_path or LOG_FILE}"
    if cache_key not in _loggers:
        path = log_path or LOG_FILE
        inner = KokertechLogger(log_path=path)
        _loggers[cache_key] = _LevelFilteredLogger(inner, name=name)
    return _loggers[cache_key]


def reset_loggers():
    """Clear the logger cache (useful for testing)."""
    _loggers.clear()


# Export the default logger for simple import use
default_logger = get_logger()
