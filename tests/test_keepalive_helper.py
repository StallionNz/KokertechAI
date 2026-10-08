"""
Unit tests for keepalive_helper.py -- KeepaliveContext class.

Covers:
  - __init__ (with/without button, pre-disabled button, callback storage)
  - set_btn_text (with/without button, thread-safe dispatch)
  - update_chat (with/without callback, thread-safe dispatch)
  - start_keepalive (timer connect/start, label storage, interval)
  - done (idempotency, timer stop, button restore)
  - elapsed property
  - log (thread-safe dispatch via QTimer.singleShot)
  - _tick (logging format, guard against _done)
  - Thread safety: QTimer.singleShot used for all cross-thread calls
"""

import sys
import unittest
from unittest.mock import patch, Mock, call, ANY

class TestKeepaliveContextInit(unittest.TestCase):

    @patch("keepalive_helper.QTimer")
    def test_init_with_button_disables_it(self, mock_qtimer_cls):
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "RUN"
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=print)
        mock_btn.setEnabled.assert_called_once_with(False)
        self.assertEqual(kc._original_text, "RUN")
        self.assertFalse(kc._done)

    @patch("keepalive_helper.QTimer")
    def test_init_without_button(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        self.assertIsNone(kc._original_text)
        self.assertFalse(kc._done)

    @patch("keepalive_helper.QTimer")
    def test_init_with_disabled_button(self, mock_qtimer_cls):
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = False
        mock_btn.text.return_value = "Busy"
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=print)
        mock_btn.setEnabled.assert_not_called()
        self.assertEqual(kc._original_text, "Busy")

    @patch("keepalive_helper.QTimer")
    def test_init_creates_timer_instance(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        mock_qtimer_cls.assert_called_once()
        self.assertIsNotNone(kc._timer)

    @patch("keepalive_helper.QTimer")
    def test_init_stores_callbacks(self, mock_qtimer_cls):
        mock_log = Mock()
        mock_chat = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=mock_log, chat_callback=mock_chat)
        self.assertIs(kc._log, mock_log)
        self.assertIs(kc._chat_cb, mock_chat)

    @patch("keepalive_helper.QTimer")
    def test_init_chat_defaults_to_none(self, mock_qtimer_cls):
        mock_log = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        self.assertIs(kc._log, mock_log)
        self.assertIsNone(kc._chat_cb)

    @patch("keepalive_helper.QTimer")
    @patch("keepalive_helper.time.time")
    def test_init_records_start_time(self, mock_time, mock_qtimer_cls):
        mock_time.return_value = 1234567.0
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        self.assertEqual(kc.start_time, 1234567.0)

class TestKeepaliveContextSetBtnText(unittest.TestCase):

    @patch("keepalive_helper.QTimer")
    def test_set_btn_text_with_button(self, mock_qtimer_cls):
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "RUN"
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=print)
        kc.set_btn_text("Capturing...")
        args, _ = mock_qtimer_cls.singleShot.call_args
        self.assertEqual(args[0], 0)
        args[1]()
        mock_btn.setText.assert_called_with("Capturing...")

    @patch("keepalive_helper.QTimer")
    def test_set_btn_text_without_button(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.set_btn_text("Processing...")
        self.assertTrue(True)

    @patch("keepalive_helper.QTimer")
    def test_set_btn_text_multiple_calls(self, mock_qtimer_cls):
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "RUN"
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=print)
        kc.set_btn_text("Step 1...")
        kc.set_btn_text("Step 2...")
        self.assertEqual(mock_qtimer_cls.singleShot.call_count, 2)

class TestKeepaliveContextUpdateChat(unittest.TestCase):

    @patch("keepalive_helper.QTimer")
    def test_update_chat_with_callback(self, mock_qtimer_cls):
        mock_chat = Mock()
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "RUN"
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=print, chat_callback=mock_chat)
        kc.update_chat("<b>Processing...</b>")
        args, _ = mock_qtimer_cls.singleShot.call_args
        self.assertEqual(args[0], 0)
        args[1]()
        mock_chat.assert_called_with("<b>Processing...</b>")

    @patch("keepalive_helper.QTimer")
    def test_update_chat_without_callback(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        count_before = mock_qtimer_cls.singleShot.call_count
        kc.update_chat("<b>test</b>")
        self.assertEqual(mock_qtimer_cls.singleShot.call_count, count_before)

class TestKeepaliveContextStartKeepalive(unittest.TestCase):

    @patch("keepalive_helper.QTimer")
    def test_start_keepalive_stores_label(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.start_keepalive(label="Analyzing")
        self.assertEqual(kc._label, "Analyzing")

    @patch("keepalive_helper.QTimer")
    def test_start_keepalive_connects_timer_timeout(self, mock_qtimer_cls):
        mock_timer_instance = mock_qtimer_cls.return_value
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.start_keepalive(label="Processing")
        mock_timer_instance.timeout.connect.assert_called_once()
        cb = mock_timer_instance.timeout.connect.call_args[0][0]
        self.assertEqual(cb, kc._tick)

    @patch("keepalive_helper.QTimer")
    def test_start_keepalive_starts_timer_with_interval(self, mock_qtimer_cls):
        mock_timer_instance = mock_qtimer_cls.return_value
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.start_keepalive(label="Processing", interval_ms=45000)
        mock_timer_instance.start.assert_called_once_with(45000)

    @patch("keepalive_helper.QTimer")
    def test_start_keepalive_default_interval(self, mock_qtimer_cls):
        mock_timer_instance = mock_qtimer_cls.return_value
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.start_keepalive(label="Processing")
        mock_timer_instance.start.assert_called_once_with(30000)

    @patch("keepalive_helper.QTimer")
    def test_start_keepalive_default_label(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.start_keepalive()
        self.assertEqual(kc._label, "Processing")

class TestKeepaliveContextDone(unittest.TestCase):

    @patch("keepalive_helper.QTimer")
    def test_done_stops_timer(self, mock_qtimer_cls):
        mock_timer_instance = mock_qtimer_cls.return_value
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.start_keepalive()
        kc.done()
        mock_timer_instance.stop.assert_called_once()

    @patch("keepalive_helper.QTimer")
    def test_done_restores_button(self, mock_qtimer_cls):
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "RUN"
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=print)
        kc.start_keepalive()
        kc.done()
        mock_btn.setEnabled.assert_called_with(True)
        mock_btn.setText.assert_called_with("RUN")

    @patch("keepalive_helper.QTimer")
    def test_done_no_crash_without_button(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.done()
        self.assertTrue(kc._done)

    @patch("keepalive_helper.QTimer")
    def test_done_idempotent(self, mock_qtimer_cls):
        mock_timer_instance = mock_qtimer_cls.return_value
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "RUN"
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=print)
        kc.start_keepalive()
        kc.done()
        kc.done()
        mock_timer_instance.stop.assert_called_once()

    @patch("keepalive_helper.QTimer")
    def test_done_sets_done_flag(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        self.assertFalse(kc._done)
        kc.done()
        self.assertTrue(kc._done)

class TestKeepaliveContextElapsed(unittest.TestCase):

    @patch("keepalive_helper.QTimer")
    @patch("keepalive_helper.time.time")
    def test_elapsed_returns_correct_seconds(self, mock_time, mock_qtimer_cls):
        mock_time.return_value = 1000.0
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        mock_time.return_value = 1042.7
        self.assertEqual(kc.elapsed, 42)

    @patch("keepalive_helper.QTimer")
    @patch("keepalive_helper.time.time")
    def test_elapsed_zero_immediately(self, mock_time, mock_qtimer_cls):
        mock_time.return_value = 2000.0
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        self.assertEqual(kc.elapsed, 0)

class TestKeepaliveContextLog(unittest.TestCase):

    @patch("keepalive_helper.QTimer")
    def test_log_dispatches_via_singleshot(self, mock_qtimer_cls):
        mock_log = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.log("Hello")
        args, _ = mock_qtimer_cls.singleShot.call_args
        self.assertEqual(args[0], 0)
        args[1]()
        mock_log.assert_called_with("Hello")

    @patch("keepalive_helper.QTimer")
    def test_log_preserves_arg(self, mock_qtimer_cls):
        mock_log = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.log("Still processing... (30s elapsed)")
        args, _ = mock_qtimer_cls.singleShot.call_args
        args[1]()
        mock_log.assert_called_with("Still processing... (30s elapsed)")

class TestKeepaliveContextTick(unittest.TestCase):

    @patch("keepalive_helper.QTimer")
    def test_tick_logs_elapsed_time(self, mock_qtimer_cls):
        mock_log = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.start_keepalive(label="Analyzing", interval_ms=30000)
        kc._tick()
        self.assertTrue(mock_log.called)
        log_msg = mock_log.call_args[0][0]
        self.assertIn("still analyzing", log_msg.lower())
        self.assertIn("elapsed", log_msg.lower())

    @patch("keepalive_helper.QTimer")
    def test_tick_does_not_log_if_done(self, mock_qtimer_cls):
        mock_log = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.start_keepalive(label="Processing")
        kc.done()
        kc._tick()
        mock_log.assert_not_called()

    @patch("keepalive_helper.QTimer")
    def test_tick_uses_lowercase_label(self, mock_qtimer_cls):
        mock_log = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.start_keepalive(label="PROCESSING")
        kc._tick()
        log_msg = mock_log.call_args[0][0]
        self.assertIn("processing", log_msg)
        self.assertNotIn("PROCESSING", log_msg)

class TestKeepaliveContextThreadSafety(unittest.TestCase):

    @patch("keepalive_helper.QTimer")
    def test_set_btn_text_uses_singleshot(self, mock_qtimer_cls):
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "RUN"
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=print)
        mock_qtimer_cls.singleShot.reset_mock()
        kc.set_btn_text("Working...")
        mock_qtimer_cls.singleShot.assert_called_once_with(0, ANY)
        mock_btn.setText.assert_not_called()
        args, _ = mock_qtimer_cls.singleShot.call_args
        args[1]()
        mock_btn.setText.assert_called_with("Working...")

    @patch("keepalive_helper.QTimer")
    def test_update_chat_uses_singleshot(self, mock_qtimer_cls):
        mock_chat = Mock()
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "RUN"
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=print, chat_callback=mock_chat)
        mock_qtimer_cls.singleShot.reset_mock()
        kc.update_chat("<b>Hi</b>")
        mock_qtimer_cls.singleShot.assert_called_once_with(0, ANY)
        mock_chat.assert_not_called()
        args, _ = mock_qtimer_cls.singleShot.call_args
        args[1]()
        mock_chat.assert_called_with("<b>Hi</b>")

    @patch("keepalive_helper.QTimer")
    def test_log_uses_singleshot(self, mock_qtimer_cls):
        mock_log = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        mock_qtimer_cls.singleShot.reset_mock()
        kc.log("test message")
        mock_qtimer_cls.singleShot.assert_called_once_with(0, ANY)
        mock_log.assert_not_called()
        args, _ = mock_qtimer_cls.singleShot.call_args
        args[1]()
        mock_log.assert_called_with("test message")

class TestKeepaliveContextTickSafety(unittest.TestCase):
    """Tests for the auto-abort safety net in KeepaliveContext.

    When a caller forgets to wire kc.done() to a signal that fires
    on end-of-work (PyQt aborts remaining slots on prior exception,
    so even reply_signal.can fail silently), the QTimer would otherwise
    keep firing "Still processing…" forever and flooding the audit
    log. MAX_TICK_FIRES caps the count and forces a self-stop with
    a clear "auto-aborting" breadcrumb.
    """

    @patch("keepalive_helper.QTimer")
    def test_class_constant_max_tick_fires_is_positive_int(self, mock_qtimer_cls):
        """Defends against accidental deletion of the safety net.

        MAX_TICK_FIRES must exist as a positive int class attribute.
        Without it the auto-abort branch in _tick() would raise
        AttributeError on the first runaway tick, leaving the timer
        in an undefined state.
        """
        from keepalive_helper import KeepaliveContext
        self.assertTrue(hasattr(KeepaliveContext, "MAX_TICK_FIRES"))
        self.assertIsInstance(KeepaliveContext.MAX_TICK_FIRES, int)
        self.assertGreater(KeepaliveContext.MAX_TICK_FIRES, 0)

    @patch("keepalive_helper.QTimer")
    def test_tick_auto_stops_after_max_ticks(self, mock_qtimer_cls):
        """After max_ticks fires without done(), the timer self-stops
        with a clear 'auto-aborting' audit entry.
        """
        from keepalive_helper import KeepaliveContext
        mock_log = Mock()
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.start_keepalive(label="Processing", interval_ms=30000, max_ticks=kc.MAX_TICK_FIRES)
        # Fire MAX_TICK_FIRES times — the LAST fire must trigger
        # the auto-abort log AND set _done.
        for _ in range(kc.MAX_TICK_FIRES):
            kc._tick()
        self.assertTrue(kc._done)
        # At least one log entry must mention auto-aborting
        logged = [c.args[0] for c in mock_log.call_args_list]
        self.assertTrue(
            any("auto-aborting" in m.lower() or "auto" in m.lower() for m in logged),
            f"Expected an auto-abort log; got: {logged!r}"
        )

    @patch("keepalive_helper.QTimer")
    def test_tick_does_not_log_when_already_done(self, mock_qtimer_cls):
        """After done() is called, _tick is a no-op (no log)."""
        from keepalive_helper import KeepaliveContext
        mock_log = Mock()
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.start_keepalive(label="Processing", interval_ms=30000)
        kc.done()
        mock_log.reset_mock()
        kc._tick()
        mock_log.assert_not_called()

    @patch("keepalive_helper.QTimer")
    def test_max_ticks_customizable_via_start(self, mock_qtimer_cls):
        """start_keepalive(max_ticks=N) overrides the class default cap.

        Lets callers opt for a stricter cap (e.g. tests that want
        the abort to fire on the 2nd tick) without monkey-patching
        the class constant.
        """
        from keepalive_helper import KeepaliveContext
        mock_log = Mock()
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.start_keepalive(label="Processing", interval_ms=30000, max_ticks=2)
        # Fire 2 times — the 2nd hits the cap and aborts
        kc._tick()
        kc._tick()
        self.assertTrue(kc._done)
        logged = [c.args[0] for c in mock_log.call_args_list]
        self.assertTrue(any("auto-aborting" in m.lower() for m in logged))
        # Further ticks are no-ops
        before = mock_log.call_count
        kc._tick()
        self.assertEqual(mock_log.call_count, before)

    @patch("keepalive_helper.QTimer")
    def test_done_before_cap_stops_normally(self, mock_qtimer_cls):
        """Explicit done() before max_ticks is reached — no auto-abort log."""
        from keepalive_helper import KeepaliveContext
        mock_log = Mock()
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.start_keepalive(label="Processing", interval_ms=30000)
        kc._tick()
        kc.done()
        # Subsequent ticks must not log nor abort
        for _ in range(kc.MAX_TICK_FIRES + 5):
            kc._tick()
        logged = [c.args[0] for c in mock_log.call_args_list]
        self.assertFalse(
            any("auto-aborting" in m.lower() for m in logged),
            f"Should not have auto-aborted when done() was explicit; got: {logged!r}"
        )

    @patch("keepalive_helper.QTimer")
    def test_restart_resets_done_and_fired_count(self, mock_qtimer_cls):
        """Restarting (``start_keepalive()`` again after auto-abort)
        clears ``_done``, the fired counter, and the cap so a recycled
        context is a clean slate. Without the ``_done`` reset, the
        ``if self._done: return`` guard in ``_tick()`` would short-circuit
        every fire and the recycled context would silently do nothing.
        """
        from keepalive_helper import KeepaliveContext
        mock_log = Mock()
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.start_keepalive(label="Processing", interval_ms=30000, max_ticks=2)
        kc._tick()
        kc._tick()  # cap fires, done
        self.assertTrue(kc._done)
        # Restart — _done, _fired_count, _max_ticks all reset
        kc.start_keepalive(label="Processing", interval_ms=30000, max_ticks=2)
        self.assertEqual(kc._fired_count, 0)
        self.assertEqual(kc._max_ticks, 2)
        self.assertFalse(kc._done)
        # New fresh budget: 2 normal ticks, then abort on the 2nd
        kc._tick()
        self.assertFalse(kc._done)
        kc._tick()
        self.assertTrue(kc._done)

class TestKeepaliveContextIntegration(unittest.TestCase):

    @patch("keepalive_helper.QTimer")
    def test_full_capture_screen_pattern(self, mock_qtimer_cls):
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "Capture Screen"
        mock_log = Mock()
        mock_chat = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=mock_log, chat_callback=mock_chat)
        kc.set_btn_text("Capturing...")
        kc.set_btn_text("Analyzing...")
        kc.start_keepalive(label="Analyzing", interval_ms=30000)
        mock_timer = mock_qtimer_cls.return_value
        mock_timer.timeout.connect.assert_called()
        mock_timer.start.assert_called_once_with(30000)
        kc.done()
        mock_timer.stop.assert_called_once()
        mock_btn.setEnabled.assert_called_with(True)
        mock_btn.setText.assert_called_with("Capture Screen")

    @patch("keepalive_helper.QTimer")
    def test_full_send_prompt_pattern(self, mock_qtimer_cls):
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "RUN"
        mock_log = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=mock_log)
        kc.set_btn_text("Processing...")
        kc.start_keepalive(label="Processing", interval_ms=30000)
        mock_timer = mock_qtimer_cls.return_value
        mock_timer.start.assert_called_once_with(30000)
        kc.done()
        mock_timer.stop.assert_called_once()

    @patch("keepalive_helper.QTimer")
    def test_full_ocr_pattern(self, mock_qtimer_cls):
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "OCR Extract Text"
        mock_log = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=mock_log)
        kc.set_btn_text("OCR Loading...")
        kc.set_btn_text("OCR Extracting...")
        kc.start_keepalive(label="Processing", interval_ms=15000)
        mock_timer = mock_qtimer_cls.return_value
        mock_timer.start.assert_called_once_with(15000)
        kc.done()
        mock_timer.stop.assert_called_once()
        mock_btn.setText.assert_called_with("OCR Extract Text")
        mock_btn.setEnabled.assert_called_with(True)

    @patch("keepalive_helper.QTimer")
    def test_no_button_no_chat_no_crash(self, mock_qtimer_cls):
        mock_log = Mock()
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.set_btn_text("Working...")
        kc.update_chat("<b>test</b>")
        kc.log("message")
        kc.start_keepalive(label="Processing")
        kc._tick()
        kc.done()
        self.assertTrue(kc._done)
        self.assertTrue(mock_log.called)

    @patch("keepalive_helper.QTimer")
    @patch("keepalive_helper.time.time")
    def test_elapsed_monotonic_across_lifecycle(self, mock_time, mock_qtimer_cls):
        mock_btn = Mock()
        mock_btn.isEnabled.return_value = True
        mock_btn.text.return_value = "RUN"
        mock_time.return_value = 5000.0
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=mock_btn, log_callback=print)
        mock_time.return_value = 5005.0
        self.assertEqual(kc.elapsed, 5)
        mock_time.return_value = 5010.0
        self.assertEqual(kc.elapsed, 10)
        kc.done()
        mock_time.return_value = 5020.0
        self.assertEqual(kc.elapsed, 20)

class TestKeepaliveContextContextManager(unittest.TestCase):
    """``__enter__``/``__exit__`` makes ``with kc.start_keepalive():``
    the canonical pattern — guarantees ``kc.done()`` on block exit even
    if an exception tears through the body."""

    @patch("keepalive_helper.QTimer")
    def test_enter_returns_self(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.start_keepalive(label="Processing", interval_ms=30000)
        self.assertIs(kc.__enter__(), kc)

    @patch("keepalive_helper.QTimer")
    def test_exit_calls_done(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        with kc.start_keepalive(label="Processing", interval_ms=30000):
            pass
        self.assertTrue(kc._done)

    @patch("keepalive_helper.QTimer")
    def test_exit_does_not_swallow_exception(self, mock_qtimer_cls):
        """The exit must RERAISE so caller exceptions propagate; we
        only guarantee cleanup, never intercep silent errors."""
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        with self.assertRaises(ValueError):
            with kc.start_keepalive(label="Processing", interval_ms=30000):
                raise ValueError("boom")
        # Cleanup STILL ran even though we exited via exception
        self.assertTrue(kc._done)

class TestKeepaliveContextStatus(unittest.TestCase):
    """``status()`` returns a snapshot dict safe to call from any
    thread (no Qt calls that would violate thread affinity)."""

    @patch("keepalive_helper.QTimer")
    def test_status_returns_expected_keys(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.start_keepalive(label="Processing", interval_ms=30000)
        s = kc.status()
        self.assertEqual(set(s.keys()), {
            "label", "elapsed_s", "fired_count", "max_ticks",
            "is_done", "last_breadcrumb",
        })
        self.assertEqual(s["label"], "Processing")
        self.assertFalse(s["is_done"])
        self.assertEqual(s["fired_count"], 0)

    @patch("keepalive_helper.QTimer")
    def test_status_reflects_done(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.start_keepalive(label="Processing", interval_ms=30000)
        kc.done()
        s = kc.status()
        self.assertTrue(s["is_done"])

    @patch("keepalive_helper.QTimer")
    def test_status_captures_breadcrumb_after_abort(self, mock_qtimer_cls):
        """After the safety net fires, ``last_breadcrumb`` exposes
        the auto-abort reason so a later telemetry poll can read it."""
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.start_keepalive(label="Processing", interval_ms=30000, max_ticks=2)
        kc._tick()
        kc._tick()  # cap fires
        s = kc.status()
        self.assertTrue(s["is_done"])
        self.assertIsNotNone(s["last_breadcrumb"])
        self.assertIn("auto-aborting", s["last_breadcrumb"])

class TestKeepaliveContextAbortCallback(unittest.TestCase):
    """``abort_callback`` fires only when the safety net trips
    (cap reached without explicit done()), never on a clean exit."""

    @patch("keepalive_helper.QTimer")
    def test_callback_fires_on_cap(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        cb = Mock()
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.abort_callback = cb
        kc.start_keepalive(label="Processing", interval_ms=30000, max_ticks=2)
        kc._tick()
        kc._tick()  # cap, abort, callback fires
        cb.assert_called_once()
        self.assertIs(cb.call_args.args[0], kc)

    @patch("keepalive_helper.QTimer")
    def test_callback_not_called_on_explicit_done(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        cb = Mock()
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.abort_callback = cb
        kc.start_keepalive(label="Processing", interval_ms=30000)
        # Fire some ticks then done() explicitly — callback must NOT fire
        kc._tick()
        kc._tick()
        kc.done()
        for _ in range(kc.MAX_TICK_FIRES + 5):
            kc._tick()
        cb.assert_not_called()

    @patch("keepalive_helper.QTimer")
    def test_callback_exception_does_not_propagate(self, mock_qtimer_cls):
        """A buggy abort_callback must not break the timer teardown —
        the very bug class this safety net exists to catch is silent
        failure, so the catch uses stdlib logging (always available)."""
        from keepalive_helper import KeepaliveContext
        bad_cb = Mock(side_effect=RuntimeError("ui glitch"))
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.abort_callback = bad_cb
        kc.start_keepalive(label="Processing", interval_ms=30000, max_ticks=2)
        # Must not raise even though callback raises
        kc._tick()
        kc._tick()
        # State is still clean
        self.assertTrue(kc._done)
        bad_cb.assert_called_once()

    @patch("keepalive_helper.QTimer")
    def test_callback_logging_failure_falls_back_to_stderr(self, mock_qtimer_cls):
        """REGRESSION TEST for lines 306-311 in keepalive_helper.py:
        when BOTH abort_callback AND logging.getLogger().exception()
        raise, the last-resort ``try: import sys; print(...,
        file=sys.stderr)`` fallback handles the error.

        NOTE: This is a leaf guard — no other test cross-links to it
        because no other test depends on the triple-nested stderr
        fallback. It remains a standalone regression test (not a
        REGRESSION GUARD with consumers).

        Construction-tuning: abort_callback raises ``RuntimeError``;
        ``logging.Logger.exception`` is patched to raise too, forcing
        the outermost ``except Exception`` branch (line 306) and its
        inner ``try: import sys as _sys; print(...)`` fallback
        (lines 309-311).  stderr is captured to verify the fallback
        print emitted something — the print itself covers line 311.

        The ``assertIn("abort_callback raised", captured)`` assertion
        fires if a refactor removes or bypasses the triple-nested
        exception handler chain, allowing the exception to propagate
        or be silently swallowed.
        """
        import io
        from keepalive_helper import KeepaliveContext
        bad_cb = Mock(side_effect=RuntimeError("ui glitch"))
        kc = KeepaliveContext(button=None, log_callback=print)
        kc.abort_callback = bad_cb
        kc.start_keepalive(label="Processing", interval_ms=30000, max_ticks=2)

        # Capture stderr to verify the fallback print
        captured = io.StringIO()
        with patch("sys.stderr", captured):
            with patch(
                "logging.Logger.exception",
                side_effect=RuntimeError("logging failed"),
            ):
                # Fire ticks until auto-abort — the abort_callback raises,
                # then the inner logging.exception ALSO raises,
                # forcing the outermost fallback to sys.stderr.
                kc._tick()
                kc._tick()

        self.assertTrue(
            kc._done,
            msg="REGRESSION: context should be done after auto-abort")
        bad_cb.assert_called_once()
        # The outermost fallback prints to stderr
        self.assertIn(
            "abort_callback raised", captured.getvalue(),
            msg=("REGRESSION: the last-resort sys.stderr print should "
                 "include the abort_callback error"))

    @patch("keepalive_helper.QTimer")
    def test_callback_default_is_none(self, mock_qtimer_cls):
        """Freshly-constructed context has abort_callback=None so callers
        who never set it are unaffected."""
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=print)
        self.assertIsNone(kc.abort_callback)

class TestKeepaliveContextDefaultMaxTicks(unittest.TestCase):
    """``default_max_ticks`` reads CONFIG so power users can tune the
    safety-net cap from Settings tab without editing source. Falls
    back gracefully on every plausible CONFIG failure mode."""

    def test_returns_positive_int(self):
        from keepalive_helper import KeepaliveContext
        value = KeepaliveContext.default_max_ticks()
        self.assertIsInstance(value, int)
        self.assertGreater(value, 0)

    def test_reads_from_config(self):
        from keepalive_helper import KeepaliveContext
        from config import CONFIG
        original = CONFIG.get("keepalive_max_ticks")
        try:
            CONFIG["keepalive_max_ticks"] = 99
            self.assertEqual(KeepaliveContext.default_max_ticks(), 99)
        finally:
            if original is None:
                CONFIG.pop("keepalive_max_ticks", None)
            else:
                CONFIG["keepalive_max_ticks"] = original

    def test_falls_back_when_config_missing(self):
        from keepalive_helper import KeepaliveContext
        from config import CONFIG
        original = CONFIG.get("keepalive_max_ticks")
        try:
            CONFIG.pop("keepalive_max_ticks", None)
            # Without the key, must fall through to the class constant.
            self.assertEqual(
                KeepaliveContext.default_max_ticks(),
                KeepaliveContext.MAX_TICK_FIRES,
            )
        finally:
            if original is not None:
                CONFIG["keepalive_max_ticks"] = original

    def test_clamps_non_positive(self):
        """A non-positive value from CONFIG gets clamped to 1 so the
        QTimer always gets a valid integer (never zero / negative)."""
        from keepalive_helper import KeepaliveContext
        from config import CONFIG
        original = CONFIG.get("keepalive_max_ticks")
        try:
            CONFIG["keepalive_max_ticks"] = -5
            self.assertEqual(KeepaliveContext.default_max_ticks(), 1)
            CONFIG["keepalive_max_ticks"] = "garbage"
            self.assertEqual(
                KeepaliveContext.default_max_ticks(),
                KeepaliveContext.MAX_TICK_FIRES,
            )
        finally:
            if original is None:
                CONFIG.pop("keepalive_max_ticks", None)
            else:
                CONFIG["keepalive_max_ticks"] = original

class TestKeepaliveContextRegression(unittest.TestCase):
    """Locks the original "Still processing…" perpetual-audit-log bug
    into the regression suite. Synthesizes the exact failing scenario:
    a KeepaliveContext whose caller forgot to wire ``kc.done()`` must
    emit exactly ``MAX_TICK_FIRES`` ordinary log lines + ONE auto-abort
    breadcrumb, then stop. Subsequent ticks must be silent no-ops."""

    @patch("keepalive_helper.QTimer")
    def test_original_bug_capped_at_max(self, mock_qtimer_cls):
        from keepalive_helper import KeepaliveContext
        mock_log = Mock()
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.start_keepalive(label="Processing", interval_ms=30000, max_ticks=kc.MAX_TICK_FIRES)
        # Fire MAX_TICK_FIRES + 5 extra ticks; only MAX_TICK_FIRES
        # ordinary logs + 1 abort breadcrumb must be emitted total.
        for _ in range(kc.MAX_TICK_FIRES + 5):
            kc._tick()
        logged = [c.args[0] for c in mock_log.call_args_list]
        # Exactly one "Still processing…" line per tick until cap,
        # then ONE auto-abort breadcrumb. No more.
        ordinary = [m for m in logged if "still processing" in m.lower()]
        aborts = [m for m in logged if "auto-aborting" in m.lower()]
        self.assertEqual(
            len(ordinary), kc.MAX_TICK_FIRES,
            f"Expected exactly {kc.MAX_TICK_FIRES} ordinary log lines; got {len(ordinary)}: {ordinary!r}"
        )
        self.assertEqual(
            len(aborts), 1,
            f"Expected exactly ONE auto-abort breadcrumb; got {len(aborts)}: {aborts!r}"
        )

class TestKeepaliveContextConfigOverride(unittest.TestCase):
    """Runtime override: ``CONFIG['keepalive_max_ticks'] = N`` overrides
    ``KeepaliveContext.MAX_TICK_FIRES`` when ``start_keepalive()`` is
    called WITHOUT an explicit ``max_ticks=`` argument.

    Validates the ``default_max_ticks()`` fallback path independent of
    whether the Settings-tab UI widget is delivered — i.e. the runtime
    contract is enforceable from a unit test on the helper alone, with
    no need to spin up the Settings-tab UI.

    Contract pinned here:
        * ``default_max_ticks()`` reads ``CONFIG`` fresh on every call
          so a Settings-tab change applies on the NEXT KeepaliveContext.
        * Explicit ``max_ticks=`` argument STILL takes precedence over
          CONFIG — locks in the precedence order so future refactors
          can't silently flip it.
        * The watchdog fires AT MOST ``N`` times where ``N`` is what
          ``_max_ticks`` got set to (the cap is enforced inside ``_tick``,
          so subsequent fires are no-ops via the ``_done`` guard).
    """

    def _set_or_restore_config(self, value):
        from config import CONFIG
        self._original_cfg = CONFIG.get("keepalive_max_ticks")
        CONFIG["keepalive_max_ticks"] = value

    def tearDown(self):
        from config import CONFIG
        if getattr(self, "_original_cfg", None) is None:
            CONFIG.pop("keepalive_max_ticks", None)
        else:
            CONFIG["keepalive_max_ticks"] = self._original_cfg

    @patch("keepalive_helper.QTimer")
    def test_config_seven_fires_exactly_seven_then_auto_aborts(self, mock_qtimer_cls):
        """The headline contract: CONFIG=7 → watchdog fires exactly 7
        times then auto-aborts, even though the class-level default is 4.

        Drives ``_tick()`` manually (interval_ms=30000 so the real timer
        never fires inside the test — fully deterministic, <10ms runtime).
        """
        from keepalive_helper import KeepaliveContext
        self._set_or_restore_config(7)

        mock_log = Mock()
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        # NOTE: explicit max_ticks= omitted — KeepaliveContext must
        # consult CONFIG via default_max_ticks().
        kc.start_keepalive(label="Processing", interval_ms=30000)

        # default_max_ticks() read CONFIG, _max_ticks=7 (NOT the class default=4)
        self.assertEqual(
            kc._max_ticks, 7,
            f"CONFIG['keepalive_max_ticks']=7 should override the "
            f"class-level cap; got _max_ticks={kc._max_ticks}",
        )
        self.assertNotEqual(
            kc._max_ticks, KeepaliveContext.MAX_TICK_FIRES,
            "Test pre-condition: CONFIG override must differ from class default",
        )

        # Fire 6 ticks — cap not yet reached, NO auto-abort log
        for _ in range(6):
            kc._tick()
        self.assertFalse(
            kc._done,
            f"After 6 ticks (cap=7) the context must not be done yet "
            f"(fired_count={kc._fired_count}, _done={kc._done})",
        )
        self.assertEqual(kc._fired_count, 6)
        self.assertIsNone(
            kc._last_breadcrumb,
            f"After 6 ticks no breadcrumb should be set "
            f"(got {kc._last_breadcrumb!r})",
        )
        # Also no auto-aborting log yet
        self.assertFalse(
            any("auto-aborting" in m.lower() for m in (c.args[0] for c in mock_log.call_args_list)),
            f"No auto-abort log expected before reaching the cap; "
            f"got: {[c.args[0] for c in mock_log.call_args_list]!r}",
        )

        # 7th tick → safety net trips → done() auto-invoked + breadcrumb logged
        kc._tick()

        self.assertTrue(
            kc._done,
            f"After 7 ticks (cap=7), kc.done() must be auto-invoked "
            f"(fired_count={kc._fired_count}, _done={kc._done})",
        )
        self.assertEqual(kc._fired_count, 7)
        self.assertIsNotNone(
            kc._last_breadcrumb,
            "_last_breadcrumb must be populated by the auto-abort branch",
        )
        self.assertIn(
            "auto-aborting", kc._last_breadcrumb,
            f"Breadcrumb should mention 'auto-aborting': {kc._last_breadcrumb!r}",
        )

        # Exactly ONE auto-abort log message was emitted
        logs = [c.args[0] for c in mock_log.call_args_list]
        abort_logs = [m for m in logs if "auto-aborting" in m.lower()]
        self.assertEqual(
            len(abort_logs), 1,
            f"Expected exactly ONE auto-abort breadcrumb log call; "
            f"got {len(abort_logs)}: {abort_logs!r}",
        )

        # Subsequent _tick() is a no-op (done-check guard) — watchdog
        # is CAPPED, not just slowed; the user-facing contract is that
        # we never emit more than N+1 logs even under runaway tickers.
        mock_log.reset_mock()
        kc._tick()
        kc._tick()
        kc._tick()
        mock_log.assert_not_called()
        self.assertEqual(
            kc._fired_count, 7,
            "fired_count must NOT increment past the cap "
            "(post-cap _tick is purely a done-check)",
        )

    @patch("keepalive_helper.QTimer")
    def test_explicit_max_ticks_overrides_config_seven(self, mock_qtimer_cls):
        """Explicit max_ticks=3 → cap is 3, NOT 7 (config).

        Locks in the precedence contract: caller-supplied explicit
        argument beats CONFIG so test harnesses and short-lived
        callers can opt for stricter caps without monkey-patching
        CONFIG.
        """
        from keepalive_helper import KeepaliveContext
        self._set_or_restore_config(7)

        mock_log = Mock()
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.start_keepalive(label="Explicit", interval_ms=30000, max_ticks=3)

        self.assertEqual(
            kc._max_ticks, 3,
            f"Explicit max_ticks=3 must beat CONFIG=7; got _max_ticks={kc._max_ticks}",
        )
        # Fire 3 → auto-abort on the 3rd
        kc._tick()
        kc._tick()
        kc._tick()
        self.assertTrue(kc._done)
        self.assertEqual(kc._fired_count, 3)
        self.assertIn("auto-aborting", kc._last_breadcrumb)

    @patch("keepalive_helper.QTimer")
    def test_config_zero_clamps_to_one(self, mock_qtimer_cls):
        """CONFIG['keepalive_max_ticks']=0 → clamps to 1 via the helper's
        ``max(1, int(value))`` guard. The watchdog fires ONE time then
        immediately auto-aborts. Without the clamp, a 0 cap would mean
        the watchdog never reaches the safety-net branch and the timer
        runs forever (because ``_fired_count >= _max_ticks`` would be
        true on the first fire).
        """
        from keepalive_helper import KeepaliveContext
        self._set_or_restore_config(0)

        mock_log = Mock()
        kc = KeepaliveContext(button=None, log_callback=mock_log)
        kc.start_keepalive(label="ClampTest", interval_ms=30000)

        self.assertEqual(
            kc._max_ticks, 1,
            f"CONFIG=0 should clamp to 1 via max(1, int(value)); got _max_ticks={kc._max_ticks}",
        )
        # Fire 1 → auto-abort immediately
        kc._tick()
        self.assertTrue(kc._done)
        self.assertEqual(kc._fired_count, 1)
        self.assertIn("auto-aborting", kc._last_breadcrumb)

    @patch("keepalive_helper.QTimer")
    def test_second_start_rereads_config_change(self, mock_qtimer_cls):
        """Settings-tab edit between two start_keepalive() calls must
        be picked up on the second start.

        Locks the 'reads-fresh-each-call' contract for ``default_max_ticks()``
        so a stale ``_max_ticks`` can never silently defeat a user Settings-tab
        edit. The user's contract: changes in CONFIG['keepalive_max_ticks']
        take effect on the NEXT start_keepalive (NOT retroactively on
        in-flight keepalives).
        """
        from keepalive_helper import KeepaliveContext

        mock_log = Mock()
        kc = KeepaliveContext(button=None, log_callback=mock_log)

        # First start: CONFIG=7, no explicit max_ticks= → cap reads 7.
        self._set_or_restore_config(7)
        kc.start_keepalive(label="Restart", interval_ms=30000)
        self.assertEqual(
            kc._max_ticks, 7,
            "First start must read CONFIG=7 via default_max_ticks()",
        )

        # Operator edits the Settings tab mid-session → CONFIG changes to 3.
        self._set_or_restore_config(3)

        # Second start: must RE-READ CONFIG, not reuse the cached 7.
        kc.start_keepalive(label="Restart", interval_ms=30000)
        self.assertEqual(
            kc._max_ticks, 3,
            "Restart must re-read CONFIG; stale _max_ticks would silently "
            "defeat Settings-tab edits (the user-facing contract).",
        )

        # Verify the new cap of 3 actually behaves correctly end-to-end:
        # fires 1, 2 → still alive; fire 3 → auto-abort.
        kc._tick()
        kc._tick()
        self.assertFalse(kc._done, "After 2 fires (cap=3) the context must NOT be done")
        kc._tick()
        self.assertTrue(kc._done, "The 3rd fire must trigger auto-abort (cap=3)")
        self.assertEqual(
            kc._fired_count, 3,
            "fired_count must equal the new cap (3), proving CONFIG-was-re-read",
        )


# =============================================================================
# Tests — default-cap raise to 12 ticks (2026-09-25)
# =============================================================================

class TestDefaultCapTwelveTicks(unittest.TestCase):
    """Regression: the auto-abort cap must be >= 12 ticks.

    The old 4-tick cap (4×30s = 120s) aborted real LLM generations that ran
    2-3 minutes (measured: 151.5s) with "auto-aborting keepalive after 4
    ticks — caller forgot to wire kc.done()" while the model was still
    streaming. 12 ticks × 30s = 360s of patience; callers can still pass an
    explicit max_ticks or tune Settings → Keepalive max ticks (range 1-20).
    """

    def test_max_ticks_fires_is_at_least_twelve(self):
        from keepalive_helper import MAX_TICK_FIRES
        self.assertGreaterEqual(MAX_TICK_FIRES, 12)

    def test_class_constant_mirrors_module_constant(self):
        from keepalive_helper import MAX_TICK_FIRES, KeepaliveContext
        self.assertEqual(KeepaliveContext.MAX_TICK_FIRES, MAX_TICK_FIRES)

    def test_config_default_is_at_least_twelve(self):
        import config
        self.assertGreaterEqual(
            config.CONFIG.get("keepalive_max_ticks"), 12)

    @patch("keepalive_helper.QTimer")
    def test_two_minute_generation_survives(self, mock_qtimer_cls):
        """The production failure, replayed: a 151.5s generation fires the
        30s timer 5 times. Under the old cap of 4 the 4th tick aborted
        mid-stream; at 12 the context must still be alive."""
        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(button=None, log_callback=Mock())
        kc.start_keepalive(label="processing", interval_ms=30000)
        self.assertEqual(kc._max_ticks, 12)
        for _ in range(5):
            kc._tick()
        self.assertFalse(
            kc._done,
            "A 151s generation must NOT hit the keepalive auto-abort cap")


if __name__ == "__main__":
    unittest.main()
