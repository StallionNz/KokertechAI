"""
auto_logger.py — Stream interception, journaling, and crash hooks.

Provides:
  - AutoOutputInterceptor: wraps stdout/stderr to tee output to a log file
  - journal_event: thread-safe JSONL journal writer
  - get_session_events / get_session_stats: journal query helpers
  - init_auto_logging: boots interceptors + global excepthook
"""
import enum
import json
import os
import sys
import threading
from datetime import datetime, timedelta

from logging_config import get_logger


logger = get_logger(name="MemoryVault")

# Default journal file path — used by journal_event() and friends.
JOURNAL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "session_journal.jsonl",
)

# Module-level lock for thread-safe journal writes.
_journal_lock = threading.Lock()

# Module-level session-start sentinel (set by init_auto_logging).
_session_start: float = 0.0


class AutoOutputInterceptor:
    """Intercepts writes to an output stream and tees them to a log file.

    Wraps an original stream (e.g. sys.stdout) so that all writes are
    forwarded to the original stream AND appended to a log file with an
    ``[AUTO]`` prefix for easy filtering.
    """

    def __init__(self, original_stream, log_file_path):
        self._original = original_stream
        self._log_path = log_file_path
        self._lock = threading.Lock()
        # Open the log file once up front so write() doesn't reopen per call.
        try:
            self._log_fh = open(log_file_path, "a", encoding="utf-8")
        except (OSError, PermissionError):
            self._log_fh = None

    def write(self, text):
        # Forward to original stream (always, even for None/empty).
        try:
            self._original.write(text)
        except Exception:
            pass
        # Write to log file (skip empty/whitespace-only, prefix each line).
        if text is not None and text.strip() and self._log_fh:
            try:
                with self._lock:
                    for line in text.splitlines(keepends=True):
                        if line.strip():
                            self._log_fh.write(f"[AUTO] {line}")
                    self._log_fh.flush()
            except (OSError, AttributeError):
                pass

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass
        if self._log_fh:
            try:
                with self._lock:
                    self._log_fh.flush()
            except (OSError, AttributeError):
                pass


class EventCategory(enum.Enum):
    SESSION = "session"
    TASK = "task"
    PROVIDER = "provider"
    PLUGIN = "plugin"
    ERROR = "error"
    WARNING = "warning"
    AUDIT = "audit"
    SYSTEM = "system"
    PERF = "performance"


def journal_event(category, message, data=None):
    """Thread-safe: write a structured event to the session journal.

    Args:
        category: One of EventCategory values (or custom string).
        message: Human-readable event description.
        data: Optional structured data dict.
    """
    if data is None:
        data = {}
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "category": category,
        "message": message,
        "data": data,
        "session_offset_s": max(0, int((datetime.now() - datetime.fromtimestamp(_session_start)).total_seconds()))
        if _session_start > 0
        else 0,
    }
    with _journal_lock:
        try:
            os.makedirs(os.path.dirname(JOURNAL_PATH), exist_ok=True)
            with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except (OSError, PermissionError, json.JSONEncodeError):
            pass


def get_session_events(category=None, limit=100):
    """Read recent journal events, optionally filtered by category.

    Args:
        category: Optional category string to filter by.
        limit: Maximum number of events to return (default 100).

    Returns:
        List of event dicts (most recent first, up to ``limit``).
    """
    if not os.path.isfile(JOURNAL_PATH):
        return []
    events = []
    try:
        with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue  # skip corrupt lines
                events.append(entry)
    except (OSError, PermissionError):
        return []
    if category:
        events = [e for e in events if e.get("category") == category]
    # Return most recent events (reversed) capped at limit.
    events.reverse()
    return events[:limit]


def get_session_stats():
    """Return session statistics: event counts by category, session duration.

    Returns:
        Dict with keys: session_start, duration_seconds, total_events, by_category.
    """
    total = 0
    by_category = {}
    if os.path.isfile(JOURNAL_PATH):
        try:
            with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    total += 1
                    cat = entry.get("category", "unknown")
                    by_category[cat] = by_category.get(cat, 0) + 1
        except (OSError, PermissionError):
            pass
    duration = (datetime.now() - datetime.fromtimestamp(_session_start)).total_seconds() if _session_start > 0 else 0.0
    return {
        "session_start": datetime.fromtimestamp(_session_start).isoformat() if _session_start > 0 else "",
        "duration_seconds": int(duration),
        "total_events": total,
        "by_category": by_category,
    }


def init_auto_logging(log_file):
    """Inject stream interceptors and global exception handlers into the runtime.

    Call this at the absolute top of your execution entry scripts.

    Failure modes are tolerated wherever reasonable (invalid paths,
    permission errors, locked files). The interceptor hooks are installed
    even when the underlying log file cannot be prepared, so the rest of the
    app still runs — just without local log persistence.
    """
    global _session_start
    _session_start = datetime.now().timestamp()

    # Write a session-break marker to the log file (if possible).
    try:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 60}\n")
            f.write(f"NEW AUTO-LOGGING SESSION — {datetime.now().isoformat()}\n")
            f.write(f"{'=' * 60}\n\n")
    except (OSError, PermissionError):
        pass

    # Replace stdout / stderr with interceptors.
    try:
        sys.stdout = AutoOutputInterceptor(sys.__stdout__, log_file)
        sys.stderr = AutoOutputInterceptor(sys.__stderr__, log_file)
    except Exception:
        pass

    # Set a global excepthook that logs unhandled exceptions.
    _original_excepthook = sys.excepthook

    def _auto_excepthook(exc_type, exc_value, exc_tb):
        try:
            stderr_write = getattr(sys.stderr, "write", None)
            if stderr_write:
                import traceback
                stderr_write(
                    f"[AUTO] Unhandled exception: {exc_type.__name__}: {exc_value}\n"
                )
                stderr_write("".join(traceback.format_tb(exc_tb)))
        except Exception:
            pass
        # Always call the original excepthook so the default crash handler runs.
        if _original_excepthook:
            _original_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _auto_excepthook

    # Write a session-started journal event.
    try:
        journal_event("session", f"Session started — log_file={log_file}")
    except Exception:
        pass
