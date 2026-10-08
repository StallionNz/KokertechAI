"""Tests for utils.qt_dispatch — cross-thread UI marshalling.

Regression guard for the stuck "⏳ Applying…" model-swap button:
QTimer.singleShot from a plain threading.Thread is silently dropped
(worker threads have no Qt event loop). run_on_main_thread must deliver
via a queued signal instead.
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication

from utils.qt_dispatch import is_main_thread, run_on_main_thread


class TestRunOnMainThreadFromWorkerThread(unittest.TestCase):
    """The core regression: callbacks posted from worker threads must fire."""

    def test_worker_thread_callback_actually_fires(self):
        app = QApplication.instance() or QApplication([])
        done = threading.Event()
        box = {}

        def work():
            def callback():
                box["thread"] = threading.current_thread()
                done.set()
            ok = run_on_main_thread(callback)
            # With a live QApplication the dispatch must be accepted.
            self.assertTrue(ok)

        t = threading.Thread(target=work, daemon=True)
        t.start()
        # Pump the main-thread event loop until the queued callback runs.
        deadline = time.time() + 10
        while not done.is_set() and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        t.join(timeout=5)
        self.assertTrue(done.is_set(),
                        "worker-posted callback never ran — silent-drop regression")
        self.assertIs(box["thread"], threading.current_thread(),
                      "callback must execute on the main thread")

    def test_inline_fallback_without_app(self):
        """No QApplication (headless/unit-test env) -> fn runs inline and
        the call reports success (documented contract: True = dispatched
        or executed; False only when a shutdown dropped the post)."""
        from unittest.mock import patch as _patch
        result = {}
        def work():
            ran = []
            with _patch("PyQt6.QtWidgets.QApplication.instance", return_value=None):
                ok = run_on_main_thread(lambda: ran.append(1))
            result["ok"] = ok
            result["ran"] = ran
        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(timeout=5)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("ran"), [1])

    def test_inline_when_no_app(self):
        """Without a QApplication (unit tests), fn runs inline."""
        from unittest.mock import patch as _patch
        ran = []
        with _patch("PyQt6.QtWidgets.QApplication.instance", return_value=None):
            ok = run_on_main_thread(lambda: ran.append(1))
        self.assertTrue(ok)
        self.assertEqual(ran, [1])


class TestRunOnMainThreadFromMainThread(unittest.TestCase):
    """Main-thread calls are synchronous — deterministic for callers/tests."""

    def test_inline_synchronous(self):
        ran = []
        ok = run_on_main_thread(lambda: ran.append(1))
        self.assertTrue(ok)
        self.assertEqual(ran, [1])  # already ran, no event loop needed

    def test_wait_ignored_on_main_thread(self):
        ran = []
        ok = run_on_main_thread(lambda: ran.append(1), wait=True)
        self.assertTrue(ok)
        self.assertEqual(ran, [1])


class TestIsMainThread(unittest.TestCase):
    def test_main_thread_true(self):
        self.assertTrue(is_main_thread())

    def test_worker_thread_false(self):
        result = {}
        t = threading.Thread(target=lambda: result.update(v=is_main_thread()))
        t.start()
        t.join(timeout=5)
        self.assertFalse(result.get("v", True))

    def test_qt_thread_detection(self):
        app = QApplication.instance() or QApplication([])
        self.assertIsNotNone(app)
        self.assertTrue(is_main_thread())


class TestWaitHandoff(unittest.TestCase):
    def test_wait_blocks_until_executed(self):
        app = QApplication.instance() or QApplication([])
        ran = []
        def work():
            ok = run_on_main_thread(lambda: ran.append(1) or time.sleep(0.05),
                                    wait=True)
            self.assertTrue(ok)
        t = threading.Thread(target=work, daemon=True)
        t.start()
        deadline = time.time() + 10
        while not ran and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        t.join(timeout=5)
        self.assertEqual(ran, [1])


if __name__ == "__main__":
    unittest.main()
