"""

keepalive_helper.py - Reusable keepalive context for long-running operations.

Provides KeepaliveContext, a helper that manages:
- Button disabling/re-enabling (prevents double-clicks)
- Button text changes (e.g. "Capturing..." -> "Saved")
- Periodic elapsed-time keepalive logs (e.g. "Still analyzing... (30s elapsed)")
- Step-by-step chat updates from background threads

"""

import time
import threading

from PyQt6.QtCore import QTimer

from logging_config import get_logger

logger = get_logger(name="MemoryVault")

# Auto-abort safety-net cap, in timer ticks. Raised 4 → 12 (2026-09-25):
# real LLM generations ran 2-3 minutes and hit the old 4×30s=120s cap
# mid-stream, self-aborting with "caller forgot to wire kc.done()" while
# the model was still generating. 12×30s = 360s of patience; still a
# safety net (users can tune 1-20 via Settings → Keepalive max ticks).
MAX_TICK_FIRES = 12


class KeepaliveContext:
    """
    KeepaliveContext — manages button state, keepalive logging, and cleanup
    for long-running operations.
    """

    def __init__(self, button=None, log_callback=None, chat_callback=None):
        self.button = button
        self._log = log_callback
        self._chat_cb = chat_callback
        self._label = ""
        self._fired_count = 0
        self._done = False
        self._last_breadcrumb = None
        self._timer = QTimer()
        self._start_time = time.time()
        self._interval_ms = 30000
        self._max_ticks = MAX_TICK_FIRES
        self._lock = threading.Lock()
        self._btn_text_stack = []  # stack of (text, tooltip) for restore
        self._original_text = None
        self.abort_callback = None

        # Disable button on construction if it's enabled
        if button is not None and hasattr(button, 'isEnabled'):
            try:
                if button.isEnabled():
                    button.setEnabled(False)
                    self._original_text = button.text()
            except (RuntimeError, AttributeError):
                pass
        if button is not None and hasattr(button, 'text'):
            try:
                self._original_text = button.text()
            except (RuntimeError, AttributeError):
                pass

    # ── Public API ────────────────────────────────────────────────

    def set_btn_text(self, text, tooltip=None):
        """Set the button text (and optionally tooltip) via main-thread hop."""
        if self.button is None:
            return
        QTimer.singleShot(0, lambda: self._do_set_btn_text(text, tooltip))

    def _do_set_btn_text(self, text, tooltip=None):
        """Actually set button text (called on main thread via singleShot)."""
        if self.button is not None and hasattr(self.button, 'setText'):
            try:
                self._btn_text_stack.append((
                    self.button.text(),
                    self.button.toolTip() if hasattr(self.button, 'toolTip') else ""
                ))
                self.button.setText(text)
                if tooltip is not None and hasattr(self.button, 'setToolTip'):
                    self.button.setToolTip(tooltip)
            except (RuntimeError, AttributeError):
                # Deliberate (silent-catch audit): widget torn down between
                # scheduling and delivery -- keepalive must never crash UI.
                pass

    def update_chat(self, html):
        """Update chat via main-thread hop."""
        if self._chat_cb is None:
            return
        QTimer.singleShot(0, lambda: self._do_update_chat(html))

    def _do_update_chat(self, html):
        """Actually call chat callback (on main thread via singleShot)."""
        if self._chat_cb is not None:
            try:
                self._chat_cb(html)
            except (RuntimeError, AttributeError):
                # Deliberate (silent-catch audit): widget torn down between
                # scheduling and delivery -- keepalive must never crash UI.
                pass

    def start_keepalive(self, label="Processing", interval_ms=30000, max_ticks=None):
        """Begin keepalive logging and return self for context-manager use.

        Args:
            label: Lowercased and used in log messages ("Still {label}...")
            interval_ms: QTimer interval in milliseconds (default 30000)
            max_ticks: Maximum number of tick firings before auto-abort.
                       If None, uses the class-level MAX_TICK_FIRES.

        Returns:
            self, for context-manager usage.
        """
        with self._lock:
            self._label = label
            self._interval_ms = interval_ms
            self._start_time = time.time()
            self._fired_count = 0
            self._done = False
            self._last_breadcrumb = None
            if max_ticks is not None:
                self._max_ticks = max_ticks
            else:
                self._max_ticks = self.default_max_ticks()
            # Connect and start the timer
            try:
                self._timer.timeout.connect(self._tick)
                self._timer.start(interval_ms)
            except (RuntimeError, AttributeError):
                pass
        return self

    def done(self):
        """Idempotent cleanup — stops keepalive, restores button state."""
        with self._lock:
            if self._done:
                return
            self._done = True
        try:
            self._timer.stop()
        except (RuntimeError, AttributeError):
            pass
        self._finalize_cleanup()

    def log(self, msg):
        """Log a breadcrumb message via main-thread hop."""
        if self._log is not None:
            QTimer.singleShot(0, lambda: self._do_log(msg))
        self._last_breadcrumb = msg

    def _do_log(self, msg):
        """Actually call log callback (on main thread via singleShot)."""
        if self._log is not None:
            try:
                self._log(msg)
            except (RuntimeError, AttributeError):
                # Deliberate (silent-catch audit): host teardown race; the
                # keepalive log line is cosmetic.
                pass

    def status(self):
        """Return a snapshot dict safe to call from any thread.

        Returns:
            dict with keys: label, elapsed_s, fired_count, max_ticks,
                            is_done, last_breadcrumb
        """
        with self._lock:
            return {
                "label": self._label,
                "elapsed_s": self.elapsed,
                "fired_count": self._fired_count,
                "max_ticks": self._max_ticks,
                "is_done": self._done,
                "last_breadcrumb": self._last_breadcrumb,
            }

    # ── Properties ────────────────────────────────────────────────

    @property
    def elapsed(self):
        """Return integer seconds since start_keepalive was called."""
        return int(time.time() - self._start_time)

    @property
    def start_time(self):
        return self._start_time

    @start_time.setter
    def start_time(self, value):
        self._start_time = value

    # ── Context manager ───────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.done()
        return False  # never suppress exceptions

    # ── Internal: tick (auto-abort safety net) ────────────────────

    def _tick(self):
        """Periodic keepalive callback — fires from QTimer.

        Logs a "Still {label}... ({N}s elapsed)" message.
        Auto-aborts after _max_ticks firings (safety net for callers
        that forget to wire done()).
        """
        if self._done:
            return

        self._fired_count += 1

        # Log the keepalive breadcrumb (direct call — _tick runs on main thread)
        label_lower = self._label.lower()
        elapsed_s = self.elapsed
        msg = f"Still {label_lower}... ({elapsed_s}s elapsed)"
        if self._log is not None:
            try:
                self._log(msg)
            except (RuntimeError, AttributeError):
                # Deliberate (silent-catch audit): host teardown race; the
                # keepalive log line is cosmetic.
                pass
        # _last_breadcrumb intentionally NOT set here — tests expect it
        # to remain None until the auto-abort fires

        # Auto-abort safety net: cap reached without explicit done()
        if self._fired_count >= self._max_ticks:
            abort_msg = f"auto-aborting keepalive after {self._fired_count} ticks — caller forgot to wire kc.done()"
            self._last_breadcrumb = abort_msg
            if self._log is not None:
                try:
                    self._log(abort_msg)
                except (RuntimeError, AttributeError):
                    pass

            # Fire abort callback if set
            if self.abort_callback is not None:
                try:
                    self.abort_callback(self)
                except Exception:
                    try:
                        import logging
                        logging.getLogger(__name__).exception(
                            "KeepaliveContext abort_callback raised"
                        )
                    except Exception:
                        import sys as _sys
                        print(
                            f"KeepaliveContext: abort_callback raised: abort_callback={self.abort_callback}",
                            file=_sys.stderr,
                        )

            # Stop timer and mark done
            try:
                self._timer.stop()
            except (RuntimeError, AttributeError):
                pass
            self._done = True
            self._finalize_cleanup()

    # ── Internal: cleanup ─────────────────────────────────────────

    def _finalize_cleanup(self):
        """Restore button text and enabled state."""
        if self.button is not None:
            try:
                if hasattr(self.button, 'setEnabled'):
                    self.button.setEnabled(True)
            except (RuntimeError, AttributeError):
                pass
            try:
                if self._original_text is not None:
                    self.button.setText(self._original_text)
                elif self._btn_text_stack:
                    old_text, old_tooltip = self._btn_text_stack.pop()
                    self.button.setText(old_text)
                    if hasattr(self.button, 'setToolTip'):
                        self.button.setToolTip(old_tooltip)
            except (RuntimeError, AttributeError):
                pass

    # ── Class-level constant ──────────────────────────────────────

    # Mirrors module MAX_TICK_FIRES (12 — see comment at module top).
    MAX_TICK_FIRES = 12

    # ── Class methods ─────────────────────────────────────────────

    @classmethod
    def default_max_ticks(cls) -> int:
        """Read keepalive_max_ticks from CONFIG, falling back to MAX_TICK_FIRES."""
        try:
            from config import CONFIG
            value = CONFIG.get("keepalive_max_ticks", cls.MAX_TICK_FIRES)
            return max(1, int(value))
        except (ImportError, ValueError, TypeError):
            return cls.MAX_TICK_FIRES
