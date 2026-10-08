import os
import sys
import threading
import time
from datetime import datetime

from config import CONFIG

# Rotation defaults
_DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_DEFAULT_BACKUP_COUNT = 3
_PERMISSION_RETRY_ATTEMPTS = 5
_PERMISSION_RETRY_BASE_SECONDS = 0.01  # 10 ms


class KokertechLogger:
    """Rotating file logger with level filtering and session tracking."""

    def __init__(self, log_path=None, max_bytes=None, backup_count=None):
        self.log_path = log_path or CONFIG.get("LOG_FILE", "execution_log.txt")
        self._raw_max_bytes = max_bytes if max_bytes is not None else _DEFAULT_MAX_BYTES
        self._raw_backup_count = backup_count if backup_count is not None else _DEFAULT_BACKUP_COUNT
        self._lock = threading.Lock()
        self._silent = False
        # Create parent directory if needed
        log_dir = os.path.dirname(self.log_path)
        if log_dir:
            try:
                os.makedirs(log_dir, exist_ok=True)
            except Exception:
                pass

    @property
    def max_bytes(self):
        return self._raw_max_bytes

    @max_bytes.setter
    def max_bytes(self, value):
        self._raw_max_bytes = value

    @property
    def backup_count(self):
        return max(1, self._raw_backup_count)

    @backup_count.setter
    def backup_count(self, value):
        self._raw_backup_count = value

    def _format_message(self, level: str, message: str) -> str:
        """Format a log message with timestamp and level."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"[{level}] {ts} - {message}\n"

    def _write_with_retry(self, formatted: str):
        """Write formatted text to the log file with PermissionError retry.

        Retries up to ``_PERMISSION_RETRY_ATTEMPTS`` times with linear backoff
        when the file cannot be opened (PermissionError).  Falls back to stderr
        with a ``"Logger Error"`` prefix if all retries are exhausted.
        Non-``PermissionError`` OSErrors also fall back to stderr but skip
        the retry delay.
        """
        last_err = None
        for attempt in range(_PERMISSION_RETRY_ATTEMPTS):
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(formatted)
                return  # Success
            except PermissionError as e:
                last_err = e
                if attempt < _PERMISSION_RETRY_ATTEMPTS - 1:
                    time.sleep(_PERMISSION_RETRY_BASE_SECONDS * (attempt + 1))
                    continue
                # Exhausted retries — fall back to stderr
                break
            except OSError as e:
                # Non-PermissionError OSError: do not retry, fall back immediately
                last_err = e
                break

        # Fall back to stderr (PermissionError exhausted or non-retryable OSError)
        sys.stderr.write(
            f"Logger Error: Unable to append to '{self.log_path}'. "
            f"Details: {last_err}\n"
        )
        sys.stderr.flush()

    def _write_entry(self, level: str, message: str):
        """Write a formatted log entry with optional rotation."""
        if self._silent:
            return

        formatted = self._format_message(level, message)
        projected_bytes = len(formatted.encode("utf-8"))

        with self._lock:
            self._rotate_if_needed(projected_bytes)
            self._write_with_retry(formatted)

    def _rotate_if_needed(self, projected_bytes: int):
        """Rotate the log file if the current size + projected bytes exceeds max_bytes.

        Rotation is best-effort: non-retryable errors are silently skipped.
        PermissionError is retried with linear backoff.
        """
        if not self.max_bytes:
            return
        try:
            current_size = os.path.getsize(self.log_path)
        except OSError:
            return

        if current_size + projected_bytes <= self.max_bytes:
            return

        # Backup chain: shift .N → .N+1, then rename live → .1
        bc = self.backup_count

        # Delete the oldest backup first (if it exists)
        oldest = f"{self.log_path}.{bc}"
        try:
            if os.path.exists(oldest):
                os.remove(oldest)
        except OSError:
            pass

        # Shift existing backups up: .{bc-1} → .{bc}, ..., .1 → .2
        for i in range(bc - 1, 0, -1):
            src = f"{self.log_path}.{i}"
            dst = f"{self.log_path}.{i + 1}"
            try:
                if os.path.exists(src):
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.replace(src, dst)
            except PermissionError:
                # Retry with linear backoff
                for attempt in range(_PERMISSION_RETRY_ATTEMPTS):
                    try:
                        time.sleep(_PERMISSION_RETRY_BASE_SECONDS * (attempt + 1))
                        if os.path.exists(dst):
                            os.remove(dst)
                        os.replace(src, dst)
                        break
                    except PermissionError:
                        continue
                    except OSError:
                        break
            except OSError:
                pass

        # Rename live log → .1
        try:
            if os.path.exists(self.log_path):
                dst = f"{self.log_path}.1"
                if os.path.exists(dst):
                    os.remove(dst)
                os.replace(self.log_path, dst)
        except PermissionError:
            for attempt in range(_PERMISSION_RETRY_ATTEMPTS):
                try:
                    time.sleep(_PERMISSION_RETRY_BASE_SECONDS * (attempt + 1))
                    dst = f"{self.log_path}.1"
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.replace(self.log_path, dst)
                    break
                except PermissionError:
                    continue
                except OSError:
                    break
        except OSError:
            pass

    # ── Public API ─────────────────────────────────────────────────

    def info(self, message):
        self._write_entry("INFO", message)

    def ok(self, message):
        self._write_entry("OK", message)

    def warning(self, message):
        self._write_entry("WARN", message)

    def error(self, message, exc_info=None):
        self._write_entry("ERROR", message)

    def log_session_start(self):
        self.info("Session started")

    def log_session_end(self):
        self.info("Session ended")
