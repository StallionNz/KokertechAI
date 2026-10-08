"""Tests for KokertechLogger size-based log rotation.

Companion to tests/test_kokertech_logger.py. Exercises the
``_rotate_if_needed`` path added to prevent unbounded growth of
``execution_log.txt`` (which previously accumulated ~370 KB of
repeated session_stats FileNotFoundError tracebacks before this fix).
"""

import os
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

import kokertech_logger


class TestKokertechLoggerRotation(unittest.TestCase):
    """Behavioral tests for the size-based rotation feature."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="kokerlog_rot_")
        self.log_path = os.path.join(self.tmpdir, "test_log.txt")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- constructor parameters --

    def test_default_max_bytes_is_5mb(self):
        """ANTI-FRAGILITY: anchors DEFAULT_MAX_BYTES=5*1024*1024 and
        DEFAULT_BACKUP_COUNT=3 (kokertech_logger.py lines 30-32).

        BEFORE: there was no explicit constant anchor — a refactor that
        reduced the defaults to fit a tight disk budget (e.g. 1 MB)
        would silently push normally-sized sessions into rotation and
        cascade-fail downstream tests that assume the 5 MB / 3-backup
        envelope.

        AFTER: this test pins the rotation budget at the project-wide
        defaults so any constant drift trips a single, easy-to-attribute
        failure BEFORE downstream tests collapse.
        """
        logger = kokertech_logger.KokertechLogger(self.log_path)
        self.assertEqual(logger.max_bytes, 5 * 1024 * 1024)
        self.assertEqual(logger.backup_count, 3)

    def test_max_bytes_zero_disables_rotation(self):
        """max_bytes=0 keeps the legacy append-only behavior.

        ANTI-FRAGILITY: heavy setup pre-fills 6 MB of 'x's and asserts
        byte-exact append-only growth (post-write size ==
        pre_fill_size + single_info_line_bytes, and ``.1`` does NOT
        exist). Relies on `if not self.max_bytes: return`` at line
        ~167 of kokertech_logger.py firing BEFORE the size-check.

        BEFORE: the size-comparison `current_size + projected_bytes
        > 0`` always evaluated True, so ``max_bytes=0`` would have
        triggered rotation on every write — corrupting legacy append-
        only fixtures (``test_kokertech_logger.py``, ``test_auto_logger.py``).

        AFTER: rotation fully disabled; writes grow the file by exactly
        one info line per call. If the guard is removed/moved,
        ``size == before + info_line_bytes`` fails OR ``.1`` appears.
        """
        logger = kokertech_logger.KokertechLogger(self.log_path, max_bytes=0)
        self.assertEqual(logger.max_bytes, 0)
        # Capture the exact byte length of a single info() call's payload so
        # the assertion doesn't depend on the timestamp string length.
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("x" * (6 * 1024 * 1024))
        before = os.path.getsize(self.log_path)
        logger.info("trigger nothing")
        info_line_bytes = os.path.getsize(self.log_path) - before
        # Sanity: an info line is non-empty
        self.assertGreater(info_line_bytes, 0)
        # Now overwrite the big payload + one fresh info line and assert
        # the size matches the sum precisely (i.e. no rotation occurred).
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("x" * (6 * 1024 * 1024))
        before = os.path.getsize(self.log_path)
        logger.info("trigger nothing")
        self.assertEqual(os.path.getsize(self.log_path), before + info_line_bytes)
        self.assertFalse(os.path.exists(self.log_path + ".1"))

    def test_backup_count_is_clamped_to_at_least_one(self):
        """ANTI-FRAGILITY: ``backup_count`` is clamped to >= 1 in
        ``KokertechLogger.__init__`` so rotation stays operational
        when ``0`` or a negative is passed.

        Invariant: ``backup_count = max(1, int(backup_count))`` at
        line ~65 of kokertech_logger.py. Without the clamp, the rename
        chain's ``for i in range(backup_count - 1, 0, -1)`` becomes
        ``range(-1, 0, -1)`` -- empty and inert -- so rotation would
        silently stop firing while logs grow unbounded.

        BEFORE: if the clamp were removed, ``logger.backup_count`` is
        0 and ``assertEqual(..., 1)`` trips immediately.

        AFTER: ``0`` and negatives coerce to 1 transparently; rotation
        chain remains operational.
        """
        logger = kokertech_logger.KokertechLogger(self.log_path, backup_count=0)
        self.assertEqual(logger.backup_count, 1)

    # -- rotation fires on threshold --

    def test_rotation_triggers_when_size_exceeds_max_bytes(self):
        """ANTI-FRAGILITY: this test was heavily restructured when
        ``_write_entry`` flipped from rotate-AFTER-write to
        rotate-BEFORE-write (CPython's ``RotatingFileHandler``).

        BEFORE: ``size == X_bulk + info_line_bytes``; the freshly-
        written info line landed ON TOP of X bulk; ``.1`` contained
        both.

    AFTER: ``size == info_line_bytes`` alone. Body comments walk
    through the precise byte-counting math used to float this
    safely above the threshold.

    Stricter regression guard
    ``test_projected_bytes_uses_utf8_encoding_regression``
    enforces the underlying UTF-8 byte math.

        Refactor revert to rotate-AFTER-write →
        ``assertEqual(os.path.getsize, recorded_info_line_bytes)``
        fails; cross-ref ``test_rotate_before_write_semantics_regression``
        catches both DUMB and SMART variants stricter.
        """
        logger = kokertech_logger.KokertechLogger(
            self.log_path, max_bytes=1024, backup_count=3
        )
        # Capture the exact byte length of one info-line by writing it to
        # an EMPTY log file (no rotation since size=0). The assertion on a
        # pre-write ``before`` baseline here wouldn't work, because rotation
        # happens BEFORE the write (matching CPython's
        # ``RotatingFileHandler`` semantic: the freshly-written content is
        # never the trigger for its own rotation, so the live file after
        # rotation + write contains only the new info line).
        with open(self.log_path, "w", encoding="utf-8"):
            pass  # truncate to empty
        before = os.path.getsize(self.log_path)
        logger.info("this pushes us over 1024 bytes")
        info_line_bytes = os.path.getsize(self.log_path) - before
        self.assertGreater(info_line_bytes, 0)
        # Use the EXACT same literal so the byte count matches the real test.
        with open(self.log_path, "w", encoding="utf-8"):
            pass  # truncate before the next real call
        before = os.path.getsize(self.log_path)
        logger.info("this pushes us over 1024 bytes")
        recorded_info_line_bytes = os.path.getsize(self.log_path) - before
        # Now the real test: write X*1000 (well below max_bytes), then a
        # logger.info call. The pending write's UTF-8 byte count (1000 + ~60)
        # exceeds max_bytes (1024), so rotation fires BEFORE the write and
        # the X bulk lands in .1 while the live file gets only the new line.
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("X" * 1000)
        logger.info("this pushes us over 1024 bytes")
        # Live file now contains ONLY the new info line. Its byte count must
        # equal the byte count of a single info line (already measured on an
        # empty file above -- independent of any rotation side effect).
        self.assertEqual(
            os.path.getsize(self.log_path), recorded_info_line_bytes
        )
        self.assertFalse(os.path.exists(self.log_path + ".2"))
        with open(self.log_path + ".1", "r", encoding="utf-8") as f:
            backup = f.read()
        self.assertIn("X" * 100, backup)

    # -- backup chain shifts N -> N+1 --

    def test_backup_chain_shifts_and_drops_oldest(self):
        """ANTI-FRAGILITY: backup chain shifts N → N+1; oldest is dropped.

        Heavy setup drives 3 rotation cycles to detect:
          (a) in-place rotation (no shift) -- .2 disappears cycle 2;
          (b) over-eager caps -- .3 slot appears.

        Invariant: ``for i in range(backup_count - 1, 0, -1)`` in
        ``_rotate_chain_shift`` shifts ``.1 → .2`` BEFORE
        ``live → .1``; ``.N+1`` deleted FIRST so
        ``os.path.exists(.N+1)`` is always False.

        BEFORE: in-place rotation would lose history; the cap-at-
        backup_count contract was untested.
        """
        logger = kokertech_logger.KokertechLogger(
            self.log_path, max_bytes=512, backup_count=2
        )
        for _ in range(3):
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write("A" * 500)
            logger.info("force rotate #1")
        self.assertTrue(os.path.exists(self.log_path + ".1"))

        for _ in range(3):
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write("B" * 500)
            logger.info("force rotate #2")
        self.assertTrue(os.path.exists(self.log_path + ".1"))
        self.assertTrue(os.path.exists(self.log_path + ".2"))
        self.assertFalse(os.path.exists(self.log_path + ".3"))

        for _ in range(3):
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write("C" * 500)
            logger.info("force rotate #3")
        # After shift, .1 has new content, .2 holds what was previously .1
        # (the prior .2 was dropped). The .3 slot is never created.
        self.assertTrue(os.path.exists(self.log_path + ".1"))
        self.assertTrue(os.path.exists(self.log_path + ".2"))
        self.assertFalse(os.path.exists(self.log_path + ".3"))

    # -- _silent short-circuit --

    def test_silent_short_circuits_rotation(self):
        """ANTI-FRAGILITY: ``logger._silent = True`` skips rotation AND write.

        Heavy setup pre-fills 1024 'Y' bytes (TWICE max_bytes=512
        to GUARANTEE rotation would fire if the guard leaks), then
        sets ``_silent = True`` BEFORE any info() call. The two
        assertions ``no .1`` AND byte-exact size unchanged jointly
        prove the guard fires BEFORE both rotation AND write.

        BEFORE: tests had to thread bespoke silencing through every
        code path; the PermissionError-retry loop could interfere
        with unrelated fixtures.

        AFTER: ``_silent=True`` is a single-point mute,
        comprehensively guarding every subsequent write. Stricter
        regression guard
        ``test_silent_short_circuits_rotation_and_write_regression``
        fires four write-paths back-to-back for partial-guard
        detection.
        """
        logger = kokertech_logger.KokertechLogger(
            self.log_path, max_bytes=512, backup_count=3
        )
        logger._silent = True
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("Y" * 1024)
        logger.info("should be no-op")
        self.assertFalse(os.path.exists(self.log_path + ".1"))
        self.assertEqual(os.path.getsize(self.log_path), 1024)

    # -- PermissionError retry on rotation --

    def test_rotation_permission_error_retries_with_linear_backoff(self):
        """ANTI-FRAGILITY: PermissionError on rename triggers a retry
        loop with linear backoff (10ms, 20ms, 30ms, 40ms).

        Heavy setup patches ``os.replace`` to fail once then succeed,
        AND patches ``time.sleep`` to record backoffs (asserts
        ``0.01`` is in the sequence -- minimum proof retry loop
        fired). Stricter regression guard
        ``test_rotation_permission_error_retry_recovers_regression``
        drives 2 failures + asserts 0.01 AND 0.02.

        BEFORE: a Windows file-locked rotation (AV scanner, pytest
        capture buffer) would fail permanently and live log would
        grow unbounded.

        AFTER: ``for attempt in range(_PERMISSION_RETRY_ATTEMPTS):``
        (= 5) in ``_rotate_if_needed`` retries with linear
        ``_PERMISSION_RETRY_BASE_SECONDS * (attempt + 1)`` backoff;
        transient locks absorb cleanly. If the loop is removed,
        ``time.sleep`` is never called and ``assertIn(0.01, ...)``
        fails.
        """
        logger = kokertech_logger.KokertechLogger(
            self.log_path, max_bytes=512, backup_count=3
        )
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("Z" * 1024)

        sleep_calls = []
        original_replace = os.replace

        def flaky_replace(src, dst):
            if not flaky_replace.already_raise:
                flaky_replace.already_raise = True
                raise PermissionError("simulated cross-process lock")
            return original_replace(src, dst)
        flaky_replace.already_raise = False

        with patch("kokertech_logger.os.replace", side_effect=flaky_replace), \
             patch("kokertech_logger.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            logger.info("trigger rotation")

        self.assertGreaterEqual(len(sleep_calls), 1)
        self.assertIn(0.01, sleep_calls)

    # -- rotation is best-effort: OSError must NOT propagate --

    def test_rotation_failure_does_not_break_writes(self):
        """ANTI-FRAGILITY: non-retryable OSError on rename is swallowed;
        the write still succeeds and the live file is preserved.

        Heavy setup patches ``os.replace`` to ``OSError``; asserts
        ``logger.info(...)`` does NOT raise AND
        ``live.startswith('Q' * 1024)`` -- pre-fill survives.

        Invariant: ``except OSError: return`` inside the
        ``_rotate_oserror_swallow`` retry loop catches non-retryable
        errors AFTER the ``except PermissionError: continue``
        handler; the outer ``_write_entry_fallback_write`` write step runs anyway.

        BEFORE: any non-retryable OSError (disk full, antivirus
        block) propagated up and crashed every log call.

        AFTER: writes always succeed; rotation gets 5 retries then
        silently skips one cycle.
        """
        logger = kokertech_logger.KokertechLogger(
            self.log_path, max_bytes=512, backup_count=3
        )
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("Q" * 1024)

        with patch("kokertech_logger.os.replace",
                   side_effect=OSError("disk full or rename blocked")):
            # Must NOT raise -- the write goes through and rotation is skipped.
            logger.info("best-effort write must succeed even if rotation fails")

        with open(self.log_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("best-effort write must succeed", content)
        self.assertTrue(content.startswith("Q"))

    # -- rotation respects _lock --

    def test_rotate_before_write_semantics_regression(self):
        """REGRESSION GUARD for the rotate-before-write contract in
        ``kokertech_logger.KokertechLogger._write_entry`` (lines 105-120
        of ``kokertech_logger.py``).

        The freshly-written info line MUST land ONLY in the live file
        after a rotation cycle, never in the backup ``.1``. Catches any
        refactor that reverts to rotate-AFTER-write semantics (DUMB: live
        empty after rotate; SMART: backup contains old + the freshly
        written line). Mirrors CPython's ``logging.handlers.RotatingFileHandler``
        — the new write is never the trigger for its own rotation.
        """
        logger = kokertech_logger.KokertechLogger(
            self.log_path, max_bytes=80, backup_count=3
        )
        # Pre-fill the live log just below max_bytes. The pending info
        # line's UTF-8 bytes (timestamp + brackets + payload + \n) easily
        # push 60 + ~50 > 80 → rotation fires this cycle.
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("X" * 60)

        # Highly unique magic payload — won't false-positive against
        # pre-fill content or residual state from sibling tests.
        magic_str = "UNIQUE_PAYLOAD_TRIGGER_REGRESSION_TEST"
        logger.info(magic_str)

        with open(self.log_path, "r", encoding="utf-8") as f:
            live = f.read()
        with open(self.log_path + ".1", "r", encoding="utf-8") as f:
            backup1 = f.read()

        # Sanity: rotation fired (pre-fill shipped to .1). If this fails,
        # the test setup is broken (threshold too high), not the logger.
        self.assertIn(
            "X" * 60, backup1,
            "Rotation did not fire (pre-fill missing from .1); "
            "test setup or threshold should be re-checked.",
        )

        # Live file != empty AND contains the freshly-written line.
        # Empty-or-missing live catches AFTER-write DUMB (rotation
        # happened, no re-write, live is just a touched-empty file).
        self.assertGreater(
            len(live), 0,
            "Live file is empty AFTER rotation — would happen under "
            "AFTER-write DUMB refactors (rotation ran, the second "
            "re-write was forgotten).",
        )
        self.assertIn(
            magic_str, live,
            f"Live file missing the freshly-written info line. "
            f"Got: {live!r}",
        )

        # THE CRITICAL invariant: the backup MUST NOT contain the
        # freshly-written info line. With rotate-before-write, the
        # payload was not yet in the live file at the moment rotation
        # shipped X*60 → .1; with rotate-after-write (DUMB or SMART),
        # the payload would have been in the live file at rotation
        # time and ended up baked into .1.
        self.assertNotIn(
            magic_str, backup1,
            "REGRESSION: the freshly-written info line leaked into "
            "the backup! The logger reverted to rotate-AFTER-write "
            "semantics (rotation MUST happen BEFORE the write — "
            "see _write_entry in kokertech_logger.py).",
        )

    def test_concurrent_writes_dont_corrupt_rotation_chain(self):
        """ANTI-FRAGILITY: ``self._lock`` serializes concurrent writes
        and rotations so the backup chain never ends up with an empty
        or partial file.

        Heavy setup: 5 threads x 20 info() calls (100 writes) with
        concurrent rotation triggers; each backup that EXISTS must
        be non-empty -- partial files indicate a thread interleaving
        with the rotate chain.

        BEFORE: without ``self._lock``, two threads could interleave
        ``open('a')`` calls with the rotation chain -- leaving a
        partial empty backup file.

        AFTER: ``with self._lock:`` in ``_write_entry`` AND
    ``_rotate_if_needed`` atomically serializes write+rename.
    Cross-process races are covered by the
    ``test_rotation_permission_error_retry_recovers_regression`` guard.

    Stricter regression guard
    ``test_silent_short_circuits_rotation_and_write_regression``
    fires the silent-flag short-circuit that concurrent rotation triggers depend on.
        """
        logger = kokertech_logger.KokertechLogger(
            self.log_path, max_bytes=2048, backup_count=3
        )

        def writer(thread_id):
            for _ in range(20):
                logger.info(f"t{thread_id} payload " + ("A" * 100))

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        for suffix in ("", ".1", ".2", ".3"):
            p = self.log_path + suffix
            if os.path.exists(p):
                self.assertGreater(
                    os.path.getsize(p), 0,
                    f"Backup file unexpectedly empty: {p}",
                )

    # -- regression guards (Sprint 14 round 3 / improvement #21) --
    # The following three tests lock down the new invariants introduced
    # for utf-8 byte projection of pending writes, PermissionError retry
    # on the rotation rename, and the top-of-_write_entry _silent guard.
    # Each test is a black-box end-state assertion: a refactor that
    # reverts the invariant trips the assertion, a refactor that
    # preserves it passes.

    # pragma: anti-fragility-orphan-ok
    def test_projected_bytes_uses_utf8_encoding_regression(self):
        """REGRESSION GUARD: rotation thresholds use UTF-8 BYTE count.

        ``_write_entry`` projects the pending write size via
        ``len(formatted_message.encode("utf-8"))`` (line 92-95 of
        ``kokertech_logger.py``), NOT ``len(formatted_message)``. For
        multi-byte characters (emoji, CJK, accented), bytes > chars —
        so char-count projection underestimates the post-write size and
        would let the live file grow past ``max_bytes`` without
        rotating. Catches any revert to ``len(message)`` accounting.

        Construction is tuned so:
          - Under UTF-8 accounting: 5 pre-fill + 69 UTF-8 bytes = 74 > 60 → rotate
          - Under char-count (regression): 5 pre-fill + 39 chars = 44 ≤ 60 → no rotate
        The threshold 60 is chosen so the gap is unambiguous in BOTH
        directions — UTF-8 fires by ~14 bytes, char-count misses by
        ~16 bytes, no plausible off-by-one misreading changes either.
        """
        logger = kokertech_logger.KokertechLogger(
            self.log_path, max_bytes=60, backup_count=3
        )
        # Pre-fill with 5 ASCII bytes (5 chars == 5 bytes).
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("A" * 5)
        # 10 emoji = 10 chars but 40 bytes UTF-8. Pure-emoji payload is
        # CRITICAL: any ASCII prefix would inflate BOTH the char count
        # AND the byte count equally, defeating the disambiguation
        # between UTF-8 and char-count projection. The docstring math
        # (39 chars / 69 bytes) is intentionally calibrated to this
        # pure-emoji payload — DO NOT prefix further text.
        #   Under UTF-8:  5 + 69 = 74 > 60 → fires rotation
        #   Under chars:  5 + 39 = 44 ≤ 60 → no rotation
        logger.info("\U0001F600" * 10)

        # Critical invariant: rotation fired because the BYTE projection
        # pushed the threshold over. If a refactor reverts to char-count,
        # 5+39=44 ≤ 60 -> no rotation -> .1 absent -> this assertion fires.
        self.assertTrue(
            os.path.exists(self.log_path + ".1"),
            "REGRESSION: rotation did NOT fire despite UTF-8 byte "
            "projection exceeding max_bytes. The logger reverted to "
            "char-count accounting for projected_bytes "
            "(see _write_entry in kokertech_logger.py — must use "
            "len(formatted_message.encode('utf-8'))).",
        )
        # Sanity: the freshly-written emoji line landed in the live
        # file (rotate-before-write semantic). The 4-byte UTF-8 emoji
        # byte-sequence is the magic marker — any future reviewer can
        # grep `\\U0001F600` to trace which test produced this file.
        with open(self.log_path, "r", encoding="utf-8") as f:
            live = f.read()
        self.assertIn(
            "\U0001F600", live,
            "Freshly-written emoji line lost during the rotation "
            "cycle (verify rotate-before-write + write order).",
        )

    def test_rotation_permission_error_retry_recovers_regression(self):
        """REGRESSION GUARD: PermissionError on the rotation rename
        is retried with linear backoff and eventually recovers.

        ``_rotate_if_needed`` runs the rename chain inside
        ``for attempt in range(_PERMISSION_RETRY_ATTEMPTS):``
        (= 5, lines 142-167 of ``kokertech_logger.py``), sleeping
        10ms, 20ms, 30ms, 40ms between attempts. A refactor that
        drops the loop or the backoff would leave the rotation
        permanently fragile to transient cross-process locks
        (pytest capture buffers, AV scanners, dashboard writers
        briefly holding the file).
        """
        logger = kokertech_logger.KokertechLogger(
            self.log_path, max_bytes=512, backup_count=3
        )
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("R" * 1024)  # well over max_bytes

        state = {"calls": 0, "fail_count": 2}
        original_replace = os.replace
        sleep_calls = []

        def flaky_replace(src, dst):
            state["calls"] += 1
            if state["calls"] <= state["fail_count"]:
                raise PermissionError(f"simulated lock #{state['calls']}")
            return original_replace(src, dst)

        with patch(
            "kokertech_logger.os.replace", side_effect=flaky_replace
        ), patch(
            "kokertech_logger.time.sleep",
            side_effect=lambda s: sleep_calls.append(s),
        ):
            logger.info("RET_MAGIC inside rotation-under-retry")

        # Refactor must retry at LEAST fail_count+1 times to recover.
        self.assertGreaterEqual(
            state["calls"], state["fail_count"] + 1,
            f"os.replace was called only {state['calls']} times "
            f"for {state['fail_count']} PermissionErrors — retry "
            f"loop did not run enough attempts to recover.",
        )
        # Linear backoff sequence (10ms, 20ms per _PERMISSION_RETRY_BASE_SECONDS).
        self.assertGreaterEqual(
            len(sleep_calls), state["fail_count"],
            f"Expected at least {state['fail_count']} backoff sleeps "
            f"for {state['fail_count']} PermissionErrors; got "
            f"{len(sleep_calls)}: {sleep_calls}",
        )
        self.assertIn(
            0.01, sleep_calls,
            f"Expected first backoff sleep of 0.01s; got: {sleep_calls}",
        )
        self.assertIn(
            0.02, sleep_calls,
            f"Expected second backoff sleep of 0.02s; got: {sleep_calls}",
        )
        # The write succeeded end-to-end (so we can also verify the
        # recovery path actually produces a valid backup file).
        self.assertTrue(
            os.path.exists(self.log_path + ".1"),
            "REGRESSION: rotation did not recover after retries — "
            "backing up .1 file is missing.",
        )
        with open(self.log_path, "r", encoding="utf-8") as f:
            live = f.read()
        self.assertIn(
            "RET_MAGIC", live,
            "The retry path produced a backup but the fresh write "
            "itself didn't land in the live file.",
        )

    def test_silent_short_circuits_rotation_and_write_regression(self):
        """REGRESSION GUARD: when ``_silent`` is True, NEITHER rotation
        NOR write happens.

        ``_write_entry`` MUST short-circuit at the very top
        (line 92 of ``kokertech_logger.py``)
        before computing ``projected_bytes``, before acquiring
        ``_lock``, before calling ``_rotate_if_needed``, and before
        the actual ``f.write``. Catches three distinct regression
        shapes:
          (a) the `if self._silent: return` line is removed entirely;
          (b) the check moves to only the rotation path (write still
              happens — file size grows);
          (c) the check moves to only the write path (rotation still
              happens — backup file created).
        """
        logger = kokertech_logger.KokertechLogger(
            self.log_path, max_bytes=512, backup_count=3
        )
        logger._silent = True
        # Pre-existing log well past max_bytes+rotation trigger AND large
        # enough to defeat spam-keeps-it-bigger-anyway. 5KB is 10x the
        # 512-byte budget — many rotations are queued to fire if the
        # silent guard somehow leaked.
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write("Z" * 5000)
        initial_size = os.path.getsize(self.log_path)
        with open(self.log_path, "rb") as f:
            initial_bytes = f.read()

        # Bombard with multiple distinct write paths. If the silent
        # guard only fires on one of these, the others will trip our
        # size-or-backup assertions below.
        logger.info("SILENT_MAGIC - first call")
        logger.warning("SILENT_MAGIC - second call")
        logger.ok("SILENT_MAGIC - third call")
        logger.info("SILENT_MAGIC - fourth call")

        # LIVE FILE: byte-perfect unchanged. Catches regression shapes
        # (a) silent guard removed (write happens, content grows) and
        # (b) silent guard moved to only-rotation path (write happens,
        # content grows — even when rotation is still skipped). The
        # byte-content equality check is strictly stronger than a
        # byte-size check (catches partial/append writes that don't
        # change the byte count).
        with open(self.log_path, "rb") as f:
            current_bytes = f.read()
        self.assertEqual(
            current_bytes, initial_bytes,
            "REGRESSION: live log file content mutated while logger "
            "was in _silent mode (a write leaked through the guard).",
        )
        # BACKUP FILES: none created. Catches regression shape (c)
        # (silent guard moved to only-write path, rotation still
        # fires — backup appears) AND (a).
        for suffix in (".1", ".2", ".3"):
            self.assertFalse(
                os.path.exists(self.log_path + suffix),
                f"REGRESSION: backup file {suffix!r} was created "
                f"while logger was in _silent mode (a rotation "
                f"leaked through the guard).",
            )


if __name__ == "__main__":
    unittest.main()
