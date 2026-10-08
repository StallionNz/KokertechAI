"""Tests for auto_logger.py - stream interception, journaling, and crash hooks."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from auto_logger import (
    AutoOutputInterceptor,
    EventCategory,
    get_session_events,
    get_session_stats,
    init_auto_logging,
    journal_event,
)



class TestAutoOutputInterceptor(unittest.TestCase):
    """Tests for the AutoOutputInterceptor class."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="w")
        self.log_path = self.tmp.name
        self.tmp.close()
        self.mock_stream = MagicMock()
        self.interceptor = AutoOutputInterceptor(self.mock_stream, self.log_path)

    def tearDown(self):
        try:
            os.unlink(self.log_path)
        except OSError:
            pass

    def test_write_empty_message_skips_log(self):
        self.interceptor.write("   ")
        self.mock_stream.write.assert_called_once_with("   ")
        with open(self.log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "")

    def test_write_forwards_to_original_stream(self):
        self.interceptor.write("hello world\n")
        self.mock_stream.write.assert_called_with("hello world\n")

    def test_write_logs_formatted_entry(self):
        self.interceptor.write("test message\n")
        with open(self.log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[AUTO]", content)
        self.assertIn("test message", content)

    def test_flush_delegates_to_original(self):
        self.interceptor.flush()
        self.mock_stream.flush.assert_called_once()

    def test_write_log_exception_does_not_crash(self):
        bad_interceptor = AutoOutputInterceptor(self.mock_stream, "/nonexistent/deep/dir/file.log")
        bad_interceptor.write("this should not crash\n")
        self.mock_stream.write.assert_called()

    def test_write_newline_handling(self):
        self.interceptor.write("line1\nline2\n")
        with open(self.log_path, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("line1\nline2", content)
        self.mock_stream.write.assert_called_with("line1\nline2\n")


class TestEventCategory(unittest.TestCase):
    """Tests for the EventCategory enum."""

    def test_enum_values(self):
        self.assertEqual(EventCategory.SESSION.value, "session")
        self.assertEqual(EventCategory.TASK.value, "task")
        self.assertEqual(EventCategory.PROVIDER.value, "provider")
        self.assertEqual(EventCategory.PLUGIN.value, "plugin")
        self.assertEqual(EventCategory.ERROR.value, "error")
        self.assertEqual(EventCategory.WARNING.value, "warning")
        self.assertEqual(EventCategory.AUDIT.value, "audit")
        self.assertEqual(EventCategory.SYSTEM.value, "system")
        self.assertEqual(EventCategory.PERF.value, "performance")

    def test_enum_members_count(self):
        self.assertEqual(len(EventCategory), 9)


class TestJournalEvent(unittest.TestCase):
    """Tests for journal_event() and related functions."""

    def setUp(self):
        self.tmp_journal = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl", mode="w")
        self.journal_path = self.tmp_journal.name
        self.tmp_journal.close()
        self._patcher = patch("auto_logger.JOURNAL_PATH", self.journal_path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        try:
            os.unlink(self.journal_path)
        except OSError:
            pass

    def test_journal_event_writes_entry(self):
        journal_event("test_category", "test message")
        with open(self.journal_path, encoding="utf-8") as f:
            line = f.readline().strip()
        entry = json.loads(line)
        self.assertEqual(entry["category"], "test_category")
        self.assertEqual(entry["message"], "test message")

    def test_journal_event_includes_timestamp(self):
        journal_event("test", "msg")
        with open(self.journal_path, encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertIn("timestamp", entry)
        self.assertRegex(entry["timestamp"], r"\d{4}-\d{2}-\d{2}")

    def test_journal_event_includes_session_offset(self):
        journal_event("test", "msg")
        with open(self.journal_path, encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertIn("session_offset_s", entry)
        self.assertIsInstance(entry["session_offset_s"], int)

    def test_journal_event_with_data_dict(self):
        journal_event("test", "with data", data={"key": "value", "count": 42})
        with open(self.journal_path, encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["data"]["key"], "value")
        self.assertEqual(entry["data"]["count"], 42)

    def test_journal_event_empty_data_default(self):
        journal_event("test", "no data")
        with open(self.journal_path, encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["data"], {})

    def test_get_session_events_returns_list(self):
        journal_event("cat_a", "msg1")
        journal_event("cat_b", "msg2")
        events = get_session_events(limit=100)
        self.assertEqual(len(events), 2)

    def test_get_session_events_filter_by_category(self):
        journal_event("cat_a", "msg_a")
        journal_event("cat_b", "msg_b")
        journal_event("cat_a", "msg_a2")
        filtered = get_session_events(category="cat_a", limit=100)
        self.assertEqual(len(filtered), 2)
        for e in filtered:
            self.assertEqual(e["category"], "cat_a")

    def test_get_session_events_respects_limit(self):
        for i in range(10):
            journal_event("test", f"msg{i}")
        events = get_session_events(limit=3)
        self.assertEqual(len(events), 3)

    def test_get_session_events_without_journal_file(self):
        with patch("auto_logger.JOURNAL_PATH", "/nonexistent/journal.jsonl"):
            events = get_session_events()
        self.assertEqual(events, [])

    def test_get_session_events_skips_corrupt_lines(self):
        with open(self.journal_path, "w", encoding="utf-8") as f:
            f.write('{"valid": true}\n')
            f.write("not-json\n")
            f.write('{"valid": false}\n')
        events = get_session_events(limit=100)
        self.assertEqual(len(events), 2)

    def test_get_session_stats_returns_dict(self):
        journal_event("test", "msg")
        stats = get_session_stats()
        self.assertIn("session_start", stats)
        self.assertIn("duration_seconds", stats)
        self.assertIn("total_events", stats)
        self.assertIn("by_category", stats)

    def test_get_session_stats_counts_by_category(self):
        for _ in range(3):
            journal_event("cat_x", "msg")
        for _ in range(2):
            journal_event("cat_y", "msg")
        stats = get_session_stats()
        self.assertEqual(stats["by_category"]["cat_x"], 3)
        self.assertEqual(stats["by_category"]["cat_y"], 2)


class TestInitAutoLogging(unittest.TestCase):
    """Tests for init_auto_logging()."""

    @patch("auto_logger.sys.stdout")
    @patch("auto_logger.sys.stderr")
    def test_init_replaces_streams(self, mock_stderr, mock_stdout):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as tmp:
            log_path = tmp.name
        try:
            init_auto_logging(log_path)
            self.assertIsInstance(sys.stdout, AutoOutputInterceptor)
            self.assertIsInstance(sys.stderr, AutoOutputInterceptor)
        finally:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
            try:
                os.unlink(log_path)
            except OSError:
                pass

    @patch("auto_logger.sys.stdout")
    @patch("auto_logger.sys.stderr")
    def test_init_creates_session_break(self, mock_stderr, mock_stdout):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="w") as tmp:
            log_path = tmp.name
            tmp.write("existing\n")
        try:
            init_auto_logging(log_path)
            with open(log_path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("NEW AUTO-LOGGING SESSION", content)
        finally:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
            try:
                os.unlink(log_path)
            except OSError:
                pass

    def test_init_handles_bad_log_path_gracefully(self):
        init_auto_logging(r"C:\:::invalid_path:::\log.txt")
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__

    @patch("auto_logger.sys.stdout")
    @patch("auto_logger.sys.stderr")
    def test_init_sets_excepthook(self, mock_stderr, mock_stdout):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as tmp:
            log_path = tmp.name
        try:
            init_auto_logging(log_path)
            self.assertIsNot(sys.excepthook, sys.__excepthook__)
        finally:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
            sys.excepthook = sys.__excepthook__
            try:
                os.unlink(log_path)
            except OSError:
                pass

    @patch("auto_logger.sys.stdout")
    @patch("auto_logger.sys.stderr")
    def test_init_writes_journal_event(self, mock_stderr, mock_stdout):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as tmp:
            log_path = tmp.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as jtmp:
            journal_path = jtmp.name
        try:
            with patch("auto_logger.JOURNAL_PATH", journal_path):
                init_auto_logging(log_path)
                events = get_session_events(category="session")
                self.assertTrue(any("started" in e.get("message", "") for e in events))
        finally:
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
            sys.excepthook = sys.__excepthook__
            try:
                os.unlink(log_path)
            except OSError:
                pass
            try:
                os.unlink(journal_path)
            except OSError:
                pass


class TestJournalThreadSafety(unittest.TestCase):
    """Test that journal_event is thread-safe."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl", mode="w")
        self.journal_path = self.tmp.name
        self.tmp.close()
        self._patcher = patch("auto_logger.JOURNAL_PATH", self.journal_path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        try:
            os.unlink(self.journal_path)
        except OSError:
            pass

    def test_concurrent_writes_dont_corrupt(self):
        import threading
        errors = []

        def writer(thread_id):
            try:
                for _ in range(20):
                    journal_event("thread_test", f"from thread {thread_id}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [])
        events = get_session_events(category="thread_test", limit=500)
        self.assertEqual(len(events), 100)


if __name__ == "__main__":
    unittest.main()
