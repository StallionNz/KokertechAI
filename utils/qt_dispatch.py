"""Cross-thread UI dispatch helpers that work from plain Python threads.

Why this exists
---------------
``QTimer.singleShot(0, fn)`` called from a plain ``threading.Thread``
silently does nothing: the timer is created with affinity to the worker
thread, and Qt only delivers timer events on threads running a Qt event
loop. A bare Python thread has none, so the callback is dropped on the
floor — with no warning. This was the root cause of the stuck
"⏳ Applying…" model-swap button (settings_tab._do_swap) and of several
worker-thread result callbacks that never updated the UI.

The fix: marshal the call with a **queued signal**. A signal emitted
with QueuedConnection posts an event to the *receiver's* thread (the
main GUI thread, which does run an event loop), so the callback fires on
the next event-loop iteration regardless of which thread requested it.

``run_on_main_thread(fn)`` is the single entry point:
  - worker thread  -> queued to the main thread via a persistent signal hub
  - main thread    -> called synchronously (predictable in tests)
  - no QApplication (pure unit tests) -> called synchronously

Threading note: the signal hub is created at import time. Python imports
application modules on the main thread, so the hub's thread affinity is
the main thread. If this module is first imported from a worker thread a
warning is logged — queued delivery then depends on that thread, which
is exactly the trap this module exists to avoid.
"""

from __future__ import annotations

import logging
import threading

__all__ = ["run_on_main_thread", "is_main_thread"]

_log = logging.getLogger(__name__)


class _MainThreadDispatcher:
    """Signal hub: workers emit through it, the main thread receives."""

    def __init__(self):
        from PyQt6.QtCore import Qt, QObject, pyqtSignal

        class _Hub(QObject):
            fire = pyqtSignal(object)  # carries a zero-arg callable

        self._hub = _Hub()
        # QueuedConnection forces event-loop delivery on the hub's
        # thread even when the sender runs elsewhere; DirectConnection
        # would execute in the *emitting* thread (wrong for us).
        self._hub.fire.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, fn):
        """Slot: runs on the hub's thread. Never let an exception vanish."""
        try:
            fn()
        except Exception:  # noqa: BLE001
            # RuntimeError = underlying C++ widget already deleted during
            # shutdown — that is normal teardown noise, not a bug.
            _log.exception("run_on_main_thread callback failed")

    def post(self, fn) -> bool:
        """Emit fn for main-thread execution. False if the hub is gone."""
        try:
            self._hub.fire.emit(fn)
            return True
        except RuntimeError:
            # Hub's C++ object was deleted (app shutting down).
            return False


_dispatcher = None
_dispatcher_lock = threading.Lock()


def _init_dispatcher():
    """Create the hub. Call on the main thread (see module docstring)."""
    global _dispatcher
    if threading.current_thread() is not threading.main_thread():
        _log.warning(
            "utils.qt_dispatch imported/initialised off the main thread; "
            "queued callbacks will target that thread instead")
    with _dispatcher_lock:
        if _dispatcher is None:
            try:
                _dispatcher = _MainThreadDispatcher()
            except (RuntimeError, TypeError, ValueError, ImportError) as e:  # pragma: no cover - no Qt available
                _log.warning("qt_dispatch hub unavailable: %s", e)
    return _dispatcher


# Create the hub eagerly at import time so its affinity is the thread
# that imported this module — the main thread in every real entry point.
_init_dispatcher()


def is_main_thread() -> bool:
    """True when running on the Qt main (GUI) thread."""
    try:
        from PyQt6.QtCore import QThread
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            return QThread.currentThread() is app.thread()
    except (ImportError, RuntimeError, AttributeError) as e:
        _log.debug("is_main_thread probe fallback: %s", e)
    return threading.current_thread() is threading.main_thread()


def run_on_main_thread(fn, wait: bool = False) -> bool:
    """Run ``fn()`` on the Qt main thread.

    Returns True when the call was dispatched (or executed), False when
    it could not be delivered (no Qt available, or app shutting down).
    Never raises for widget teardown noise.

    Parameters
    ----------
    fn : callable
        Zero-argument callable to execute.
    wait : bool
        Block until fn has run. Only use from worker threads — waiting
        on the main thread would deadlock, so ``wait`` is ignored there.
    """
    try:
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
    except (ImportError, RuntimeError, AttributeError):  # pragma: no cover - PyQt6 not importable
        app = None

    if app is None:
        # No Qt app (headless unit tests): run inline so behaviour is
        # observable instead of silently dropped.
        fn()
        return True

    if is_main_thread():
        fn()
        return True

    dispatcher = _dispatcher or _init_dispatcher()
    if dispatcher is None:  # pragma: no cover - Qt broken
        fn()
        return True

    if wait:
        # Synchronous hand-off via the queued hub + event. Deadlock-safe
        # here because we just proved we are NOT the main thread.
        done = threading.Event()

        def _wrapped():
            try:
                fn()
            finally:
                done.set()

        dispatched = dispatcher.post(_wrapped)
        if dispatched:
            done.wait(timeout=30)
        return dispatched

    return dispatcher.post(fn)
