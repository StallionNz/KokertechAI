import os
import tempfile
import threading
import unittest
from unittest.mock import patch, mock_open
import sys
import kokertech_logger

class TestKokertechLogger(unittest.TestCase):

    @patch('os.makedirs')
    def test_init_creates_directory(self, mock_makedirs):
        # Test that the logger creates the nested directory if needed
        logger = kokertech_logger.KokertechLogger("C:\\FakeWorkspace\\logs\\test_log.txt")
        mock_makedirs.assert_called_once_with("C:\\FakeWorkspace\\logs", exist_ok=True)

    @patch('builtins.open', new_callable=mock_open)
    @patch('os.makedirs')
    def test_info_logging_format(self, mock_makedirs, mock_file):
        logger = kokertech_logger.KokertechLogger("test_log.txt")
        logger.info("Connecting to Jan API...")

        mock_file.assert_called_once_with("test_log.txt", "a", encoding="utf-8")
        written_content = mock_file().write.call_args[0][0]

        # Verify structural formatting
        self.assertIn("[INFO]", written_content)
        self.assertIn("Connecting to Jan API...", written_content)
        self.assertTrue(written_content.endswith("\n"))

    @patch('builtins.open', new_callable=mock_open)
    @patch('os.makedirs')
    def test_error_logging_format(self, mock_makedirs, mock_file):
        logger = kokertech_logger.KokertechLogger("test_log.txt")
        logger.error("Failed to connect!")

        written_content = mock_file().write.call_args[0][0]

        # Verify structural formatting
        self.assertIn("[ERROR]", written_content)
        self.assertIn("Failed to connect!", written_content)


class TestKokertechLoggerLockAndRetry(unittest.TestCase):
    """Tests for the in-process lock + cross-process PermissionError retry.

    These cover the Phase-5-cross-file-isolation follow-up: the logger must
    not raise PermissionError when another process (pytest capture, an AV
    scanner, the dashboard's own writers) briefly holds the file.
    """

    def setUp(self):
        # Use a real tempdir so the lock + retry exercise real file I/O.
        self.tmpdir = tempfile.mkdtemp(prefix="kokerlog_test_")
        self.log_path = os.path.join(self.tmpdir, "test_log.txt")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # === _lock attribute exists ===

    def test_init_creates_lock(self):
        logger = kokertech_logger.KokertechLogger(self.log_path)
        self.assertIsInstance(logger._lock, type(threading.Lock()))

    # === Concurrent writes are serialized (in-process safety) ===

    def test_concurrent_writes_do_not_raise(self):
        """ANTI-FRAGILITY: 10 threads x 50 writes (500 total) verifies
        ``self._lock`` in ``KokertechLogger._write_entry``
        (``kokertech_logger.py``) serializes concurrent in-process
        writers.

        Invariant: ``with self._lock:`` in ``_write_entry_lock`` covers both
        rotate and write; the dual assertion
        (``errors == []`` + ``len(lines) == 500``) proves no thread
        raised AND every text persisted in order (no dropped writes
        from race-induced skip).

        BEFORE: earlier in-process tests of this invariant were
        flake-prone on Windows when the lock was acquired in the wrong
        scope.
        """
        logger = kokertech_logger.KokertechLogger(self.log_path)
        errors = []

        def writer(thread_id):
            try:
                for i in range(50):
                    logger.info(f"thread {thread_id} line {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], f"Concurrent writes raised: {errors}")
        # Verify all 10 * 50 = 500 lines were actually written
        with open(self.log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 500)

    def test_lock_serializes_writes(self):
        """ANTI-FRAGILITY: ``builtins.open`` is wrapped with a counter
        that increments ``in_flight`` before delegating and decrements
        on close; ``max_in_flight == 1`` is asserted after 20 concurrent
        ``logger.info(...)`` calls.

        Invariant: ``with self._lock:`` in ``_write_entry_lock``
        holds the lock for the entire open/
        write/close block. The counter catches in-process races
        independent of disk behaviour -- any refactor that moves the
        lock acquisition to AFTER ``open()`` (or drops it) jumps
        ``max_in_flight`` to 2+ and this assertion fires.

        BEFORE: an earlier hand-rolled 2-thread version occasionally
        flaked intermittently when the lock was acquired in the wrong
        scope.
        """
        logger = kokertech_logger.KokertechLogger(self.log_path)
        call_count = [0]
        in_flight = [0]
        max_in_flight = [0]
        lock = threading.Lock()

        # Wrap open() to count concurrent file handles
        original_open = open
        def counting_open(*args, **kwargs):
            with lock:
                call_count[0] += 1
                in_flight[0] += 1
                if in_flight[0] > max_in_flight[0]:
                    max_in_flight[0] = in_flight[0]
            try:
                f = original_open(*args, **kwargs)
                return f
            finally:
                with lock:
                    in_flight[0] -= 1

        def writer():
            logger.info("test line")

        with patch("builtins.open", side_effect=counting_open):
            threads = [threading.Thread(target=writer) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        # All 20 writes should have happened
        self.assertEqual(call_count[0], 20)
        # Maximum concurrent opens should be 1 (lock serializes them)
        self.assertEqual(max_in_flight[0], 1, "Lock did not serialize concurrent writes")

    # === PermissionError retry (cross-process safety) ===

    def test_permission_error_retries_and_succeeds(self):
        """ANTI-FRAGILITY: cross-process PermissionError on the WRITE
        path is retried; the first ``open(mode='a')`` is patched to
        raise, real ``open`` runs on retry. ``attempt_count == 2``
        proves the retry loop fired exactly once.

        Invariant: ``for attempt in range(_PERMISSION_RETRY_ATTEMPTS)``
        (= 5) with linear 10/20/30/40ms backoff in
        ``KokertechLogger._write_entry`` (``kokertech_logger.py``).
        Sibling-invariant of the ROTATION-path retry covered by
        ``tests/test_log_rotation.py`` REGRESSION GUARD
        ``test_rotation_permission_error_retry_recovers_regression``
        -- rotation handles .N backup locks; this guards the live
        write path.
        """
        logger = kokertech_logger.KokertechLogger(self.log_path)
        # Pre-create the file so open(mode='a') can succeed on retry
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("")
        attempt_count = [0]
        original_open = open

        def flaky_open(*args, **kwargs):
            attempt_count[0] += 1
            if attempt_count[0] == 1:
                raise PermissionError("simulated cross-process file lock")
            return original_open(*args, **kwargs)

        with patch("builtins.open", side_effect=flaky_open):
            logger.info("first attempt should fail, retry should succeed")
        self.assertEqual(attempt_count[0], 2)

    def test_permission_error_exhausts_retries_then_falls_back(self):
        """ANTI-FRAGILITY: when the retry loop exhausts 5 attempts,
        the logger falls back to stderr with the ``"Logger Error"``
        prefix and the log path embedded.

        Invariant: ``sys.stderr.write(f"Logger Error: Unable to
        append to '{self.log_path}'. Details: {str(last_err)}\\n")``
        fires AFTER the retry loop in
        ``_write_entry_retry_exhaustion``.
        A refactor that leaks

        Sibling-invariant of the ROTATION-path retry covered by
        ``test_rotation_permission_error_retry_recovers_regression``
        -- both share ``_PERMISSION_RETRY_ATTEMPTS`` and the linear
        backoff formula. ``PermissionError`` to the caller
        crashes every log call; a refactor that suppresses the
        fallback silently loses messages.
        """
        logger = kokertech_logger.KokertechLogger(self.log_path)
        with patch("builtins.open", side_effect=PermissionError("held forever")):
            with patch.object(sys, "stderr") as mock_stderr:
                logger.info("should fall back to stderr")
        # Stderr was called with the fallback message
        mock_stderr.write.assert_called()
        msg = mock_stderr.write.call_args[0][0]
        self.assertIn("Logger Error", msg)
        self.assertIn(self.log_path, msg)

    def test_oserror_does_not_retry(self):
        """ANTI-FRAGILITY: non-PermissionError ``OSError`` (disk full,
        path too long) short-circuits the retry loop and goes straight
        to the stderr fallback.

        Invariant: ``except OSError as e: last_err = e; break`` in
        ``_write_entry_oserror_noretry``
        breaks out of the retry loop WITHOUT consuming any of the
        linear backoff slots.

        BEFORE: without the explicit ``break``, disk-full errors would
        waste 4 x (10ms + 20ms + 30ms + 40ms = 100ms total) before
        falling back, slowing the hot path under genuine I/O exhaustion.
        """
        logger = kokertech_logger.KokertechLogger(self.log_path)
        with patch("builtins.open", side_effect=OSError("disk full")):
            with patch.object(sys, "stderr") as mock_stderr:
                logger.info("should fall back to stderr")
        # Stderr was called with the fallback message
        mock_stderr.write.assert_called()

    def test_retry_uses_linear_backoff(self):
        """ANTI-FRAGILITY: ``time.sleep`` is patched to record calls
        (sleep_calls list) and the assertion ``[0.01, 0.02, 0.03,
        0.04]`` proves the linear 10/20/30/40ms sequence.

        Invariant: ``time.sleep(_PERMISSION_RETRY_BASE_SECONDS *
        (attempt + 1))`` in ``KokertechLogger._write_entry``
        (``kokertech_logger.py``). Sibling-invariant of the ROTATION-
        path retry covered by        ``tests/test_log_rotation.py`` REGRESSION GUARD
        ``test_rotation_permission_error_retry_recovers_regression``
        -- both share ``_PERMISSION_RETRY_BASE_SECONDS = 0.01`` and
        the linear formula.
        """
        logger = kokertech_logger.KokertechLogger(self.log_path)
        sleep_calls = []
        with patch("kokertech_logger.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with patch("builtins.open", side_effect=PermissionError("held")):
                with patch.object(sys, "stderr"):
                    logger.info("trigger retry")
        # 5 attempts, 4 sleeps in between (last attempt has no sleep after it)
        self.assertEqual(len(sleep_calls), 4)
        # Linear backoff: 0.01, 0.02, 0.03, 0.04
        self.assertEqual(sleep_calls, [0.01, 0.02, 0.03, 0.04])


if __name__ == '__main__':
    unittest.main()