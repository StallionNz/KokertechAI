"""
notifications.py — System tray + desktop toast notification system.
Sprint 6.1: Windows toast notifications for background AI events.

Provides:
- KokerNotificationManager: system tray icon, toast popups, notification queue
- Thread-safe `notify()` function for use from any thread
- Event categories: info, success, warning, error, task_complete
"""

import queue
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple, Any

from PyQt6.QtCore import pyqtSignal, QObject, QTimer
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QApplication

from config import CONFIG
from logging_config import get_logger

logger = get_logger(name="Notifications")


# ---------------------------------------------------------------------------
# Emoji prefix lookup by category
# ---------------------------------------------------------------------------

_CATEGORY_EMOJI = {
    "info": "\u2139\ufe0f",       # ℹ️
    "success": "\u2705",          # ✅
    "warning": "\u26a0\ufe0f",    # ⚠️
    "error": "\u274c",            # ❌
    "task_complete": "\u2705",    # ✅
}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_notification_manager: Optional["KokerNotificationManager"] = None
_manager_lock = threading.Lock()


# ---------------------------------------------------------------------------
# KokerNotificationManager
# ---------------------------------------------------------------------------

class KokerNotificationManager(QObject):
    """System tray + desktop toast notification manager.

    Manages a QSystemTrayIcon, a notification queue (for messages that arrive
    before initialization), toast throttling (max 3 concurrent), and a timer
    that drains the queue periodically.
    """

    toast_signal = pyqtSignal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._initialized = False
        self._tray: Optional[QSystemTrayIcon] = None
        self._max_concurrent_toasts = 3
        self._active_toasts = 0
        self._message_queue: queue.Queue = queue.Queue()
        self._toast_timer: Optional[QTimer] = None

    # -------------------------------------------------------------------
    # Initialize / shutdown
    # -------------------------------------------------------------------

    def initialize(self):
        """Create the system tray icon, context menu, and queue-drain timer.

        No-ops if already initialized or if there is no QApplication instance.
        """
        if self._initialized:
            return

        app = QApplication.instance()
        if app is None:
            return

        # --- Tray icon ---
        icon = QIcon()
        self._tray = QSystemTrayIcon(icon, self)

        # Context menu
        menu = QMenu()
        show_action = QAction("Show", self)
        show_action.triggered.connect(lambda: None)  # placeholder
        menu.addAction(show_action)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(app.quit)
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)

        self._tray.show()

        # --- Toast signal → _show_toast bridge ---
        self.toast_signal.connect(self._show_toast)

        # --- Queue-drain timer ---
        self._toast_timer = QTimer(self)
        self._toast_timer.timeout.connect(self._process_queue)
        self._toast_timer.start(2000)  # poll every 2 seconds

        self._initialized = True

    def shutdown(self):
        """Stop the timer, hide the tray icon, and mark as uninitialized."""
        if self._toast_timer is not None:
            self._toast_timer.stop()
        if self._tray is not None:
            self._tray.hide()
        self._initialized = False

    # -------------------------------------------------------------------
    # Public notify entry point (thread-safe)
    # -------------------------------------------------------------------

    def notify(self, title: str, message: str, category: str = "info"):
        """Queue a notification or emit it directly if already initialized.

        When ``_initialized`` is True the toast_signal is emitted immediately.
        Otherwise the (title, message, category) tuple is pushed onto the
        internal message queue and will be delivered once ``initialize()``
        is called.
        """
        if self._initialized:
            self.toast_signal.emit(title, message, category)
        else:
            self._message_queue.put((title, message, category))

    # -------------------------------------------------------------------
    # Internal toast display
    # -------------------------------------------------------------------

    def _show_toast(self, title: str, body: str, category: str):
        """Display a single toast notification via the tray icon.

        Throttles when the maximum number of concurrent toasts is reached
        (re-queues the message).  Prefixes the title with a category emoji.
        """
        if self._tray is None:
            return

        if not self._tray.supportsMessages():
            return

        if self._active_toasts >= self._max_concurrent_toasts:
            self._message_queue.put((title, body, category))
            return

        emoji = _CATEGORY_EMOJI.get(category, "")
        self._tray.showMessage(f"{emoji} {title}", body)
        self._active_toasts += 1

    # -------------------------------------------------------------------
    # Toast lifecycle
    # -------------------------------------------------------------------

    def _on_toast_expired(self):
        """Decrement the active-toast counter (never below zero)."""
        if self._active_toasts > 0:
            self._active_toasts -= 1

    # -------------------------------------------------------------------
    # Queue processing
    # -------------------------------------------------------------------

    def _process_queue(self):
        """Drain all queued notifications into _show_toast."""
        while not self._message_queue.empty():
            try:
                title, message, category = self._message_queue.get_nowait()
            except queue.Empty:
                break
            self._show_toast(title, message, category)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def get_notification_manager(parent=None) -> KokerNotificationManager:
    """Get or create the singleton notification manager."""
    global _notification_manager
    with _manager_lock:
        if _notification_manager is None:
            _notification_manager = KokerNotificationManager(parent)
        return _notification_manager


def _notifications_enabled() -> bool:
    """Check whether desktop notifications are enabled in CONFIG.

    Gracefully handles import errors so callers don't crash if config.py
    hasn't loaded yet (e.g. during early startup before QApplication init).
    """
    try:
        return bool(CONFIG.get("notifications_enabled", True))
    except Exception:
        return True


def notify(title: str, message: str, category: str = "info"):
    """Convenience: queue a notification from anywhere (thread-safe).

    Respects the ``notifications_enabled`` CONFIG flag — no-ops silently
    when the user has disabled notifications in Settings.

    Example:
        from notifications import notify
        notify("AI Task Complete", "Research on topic X finished.", "success")
    """
    if not _notifications_enabled():
        return
    mgr = get_notification_manager()
    mgr.notify(title, message, category)


def notify_task_complete(task_name: str):
    """Notify that a background AI task has completed."""
    notify("Task Complete", f"'{task_name}' completed successfully.", "task_complete")


def notify_error(task_name: str, error_msg: str):
    """Notify that a background AI task has failed."""
    notify("Task Failed", f"'{task_name}' failed: {error_msg}", "error")
