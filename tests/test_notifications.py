"""Tests for notifications.py - system tray and desktop toast notifications."""

import unittest
from unittest.mock import patch, MagicMock, call

from notifications import KokerNotificationManager, get_notification_manager, notify, notify_task_complete, notify_error


QT_PATCH_PATHS = [
    "notifications.QSystemTrayIcon",
    "notifications.QMenu",
    "notifications.QAction",
    "notifications.QTimer",
    "notifications.QIcon",
]



class TestKokerNotificationManager(unittest.TestCase):
    """Core notification manager: init, notify, queue, shutdown."""
    def setUp(self):
        self.app_patch = patch("notifications.QApplication.instance", return_value=MagicMock())
        self.app_patch.start()
        self.addCleanup(self.app_patch.stop)
        self.qt_patchers = {}
        for path in QT_PATCH_PATHS:
            p = patch(path)
            p.start()
            self.qt_patchers[path] = p
            self.addCleanup(p.stop)
        # QPixmap is imported inside initialize() body, not at module level
        self.qpixmap_patch = patch("PyQt6.QtGui.QPixmap")
        self.qpixmap_patch.start()
        self.addCleanup(self.qpixmap_patch.stop)
        self.mgr = KokerNotificationManager()
    def test_initial_state(self):
        self.assertFalse(self.mgr._initialized)
        self.assertIsNone(self.mgr._tray)
        self.assertEqual(self.mgr._max_concurrent_toasts, 3)
    def test_initialize_creates_tray_icon(self):
        self.mgr.initialize()
        self.assertTrue(self.mgr._initialized)
        self.assertIsNotNone(self.mgr._tray)
        self.mgr._tray.show.assert_called_once()
    def test_initialize_skips_when_already_initialized(self):
        self.mgr.initialize()
        self.mgr._tray.reset_mock()
        self.mgr.initialize()
        self.mgr._tray.show.assert_not_called()
    def test_initialize_skips_when_no_app_instance(self):
        with patch("notifications.QApplication.instance", return_value=None):
            m = KokerNotificationManager()
            m.initialize()
        self.assertFalse(m._initialized)
    def test_notify_queues_when_not_initialized(self):
        self.mgr.notify("Title", "Message", "info")
        self.assertEqual(self.mgr._message_queue.qsize(), 1)
    def test_notify_emits_signal_when_initialized(self):
        self.mgr.initialize()
        with patch.object(self.mgr, "toast_signal") as mock_sig:
            self.mgr.notify("Title", "Msg", "info")
            mock_sig.emit.assert_called_once_with("Title", "Msg", "info")
    def test_show_toast_displays_message(self):
        self.mgr.initialize()
        self.mgr._tray.supportsMessages.return_value = True
        self.mgr._show_toast("Title", "Body", "info")
        self.mgr._tray.showMessage.assert_called_once()
    def test_show_toast_prefixes_title_with_emoji(self):
        self.mgr.initialize()
        self.mgr._tray.supportsMessages.return_value = True
        self.mgr._show_toast("Err", "Oops", "error")
        args = self.mgr._tray.showMessage.call_args[0]
        self.assertIn("\u274c", args[0])
    def test_show_toast_throttles_when_busy(self):
        self.mgr.initialize()
        self.mgr._tray.supportsMessages.return_value = True
        self.mgr._active_toasts = 3
        self.mgr._show_toast("T", "M", "info")
        self.mgr._tray.showMessage.assert_not_called()
        self.assertEqual(self.mgr._message_queue.qsize(), 1)
    def test_on_toast_expired_decrements_counter(self):
        self.mgr._active_toasts = 2
        self.mgr._on_toast_expired()
        self.assertEqual(self.mgr._active_toasts, 1)
    def test_on_toast_expired_never_goes_negative(self):
        self.mgr._on_toast_expired()
        self.assertEqual(self.mgr._active_toasts, 0)
    def test_process_queue_drains(self):
        self.mgr.initialize()
        self.mgr._tray.supportsMessages.return_value = True
        self.mgr._message_queue.put(("T1", "M1", "info"))
        self.mgr._message_queue.put(("T2", "M2", "success"))
        self.mgr._process_queue()
        self.assertEqual(self.mgr._message_queue.qsize(), 0)
        self.assertEqual(self.mgr._tray.showMessage.call_count, 2)
    def test_shutdown_stops_timer_and_hides_tray(self):
        self.mgr.initialize()
        self.mgr.shutdown()
        self.assertFalse(self.mgr._initialized)
        self.mgr._toast_timer.stop.assert_called_once()
        self.mgr._tray.hide.assert_called_once()


class TestModuleLevelFunctions(unittest.TestCase):
    """Module-level notify, get_notification_manager, task_complete, error."""
    def setUp(self):
        self.mgr_patch = patch("notifications.get_notification_manager")
        self.mock_mgr = self.mgr_patch.start()
        self.mock_mgr.return_value = self.mock_mgr  # get_notification_manager() returns itself
        self.addCleanup(self.mgr_patch.stop)
    def test_notify_convenience_calls_manager(self):
        notify("T", "M", "info")
        self.mock_mgr.notify.assert_called_once_with("T", "M", "info")
    def test_notify_task_complete(self):
        notify_task_complete("my_task")
        self.mock_mgr.notify.assert_called_once()
        args = self.mock_mgr.notify.call_args[0]
        self.assertEqual(args[0], "Task Complete")
        self.assertEqual(args[2], "task_complete")
    def test_notify_error(self):
        notify_error("my_task", "fail")
        self.mock_mgr.notify.assert_called_once()
        args = self.mock_mgr.notify.call_args[0]
        self.assertIn("my_task", args[1])
        self.assertIn("fail", args[1])
        self.assertEqual(args[2], "error")
