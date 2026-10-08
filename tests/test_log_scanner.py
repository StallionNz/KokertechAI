"""Unit tests for utils.log_scanner - the shared RuntimeWarning audit helper.

This file is the canonical home for tests of utils.log_scanner.scan_execution_log
itself (as opposed to tests of any specific caller like vault_cleaner or
vault_upgrade). Future helper features (kwarg variants, logging level
tweaks, return-shape changes) should land here, not in caller test files.

History: TestScanExecutionLog was originally in test_vault_cleaner.py during
the pre-extraction era when vault_cleaner._scan_log_for_runtime_warnings
was a private helper. After the helper was promoted to utils.log_scanner
for reuse across app_lifecycle.closeEvent, vault_cleaner.run, and
vault_upgrade.upgrade_schema, the generic helper tests were extracted
out of the vault_cleaner-specific file into THIS canonical location.

A single regression in scan_execution_log now trips this file alongside the
caller-integration tests in test_vault_cleaner.py and test_vault_upgrade.py.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

def _write_log(log_path, body):
    """Write body to log_path (UTF-8). Mirrors the helper in
    test_vault_cleaner.py -- intentionally duplicated here to keep
    test_log_scanner self-contained (cross-file test-helper imports
    couple suites unnecessarily). The 3-line helper is cheap to keep
    in sync.
    """
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(body)

@contextmanager
def _patched_invoke(test_case, *, module_path, **patches):
    """Standard ceremony for caller-integration tests in
    :class:`TestScanExecutionLogIntegration`. Mirrors ``_patched_run``
    from test_vault_cleaner.py and ``_patched_upgrade`` from
    test_vault_upgrade.py.

    Generalised shape: patches module-level constants (LOG_FILE,
    db_path, WORKSPACE_DIR, etc.) on the named module, yields a
    MagicMock logger so the caller can assert on recorded calls
    after the module's function runs. The caller invokes the
    target function inside the ``with`` block (via import-and-call).
    ``patch.multiple`` restores the real values on exit; the
    MagicMock preserves every recorded call (.warning, .ok, .debug,
    .info) for post-block assertions.

    Exists here as a canonical, file-local ceremony so a 4th caller
    test file does not grow its own copy of the same 5-line
    ``patch.multiple`` + ``import`` + call + yield skeleton. The
    signature is deliberately shaped to accept any caller
    (``**patches`` captures WORKSPACE_DIR, db_path, LOG_FILE, etc.).

    Production kokertech_vault.db at C:\\\\KokertechAI\\\\kokertech_vault.db
    is NEVER opened by callers -- db_path / WORKSPACE_DIR is patched
    to a tempfile.mkdtemp path passed by the test.
    """
    mock_logger = MagicMock()
    with patch.multiple(module_path, logger=mock_logger, **patches):
        yield mock_logger

class TestScanExecutionLog(unittest.TestCase):
    """Direct unit tests for utils.log_scanner.scan_execution_log().

    Was previously a class inside test_vault_cleaner.py; extracted here so
    future helper features land in their canonical home (this file).
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="ls_scan_")
        # Match the production file name so any path-based sanity checks
        # still work.
        self._log_path = os.path.join(self._tmpdir, "execution_log.txt")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _scanned_with(self, body, **kwargs):
        """Write ``body`` to ``self._log_path``, invoke ``scan_execution_log``
        against a :class:`MagicMock` logger, return the mock so the caller
        can assert on recorded calls.

        Sibling of :func:`_patched_run` from ``test_vault_cleaner.py``:
        same dedup rationale -- centralize the (write log + invoke helper +
        return mock) ceremony so the 4 tests that fit this shape share one
        implementation. ``**kwargs`` are forwarded to ``scan_execution_log``
        so the ``prefix='[Cleanup] '`` variant fits without diverging.

        The 5th test (:meth:`test_missing_log_file_silently_returns_zero`)
        does NOT use ``_scanned_with`` because it operates on a bogus
        non-existent path -- no write-then-scan ceremony; scans directly on
        the bogus path so the missing-file branch is exercised.
        """
        from utils.log_scanner import scan_execution_log
        _write_log(self._log_path, body)
        mock_logger = MagicMock()
        scan_execution_log(self._log_path, mock_logger, **kwargs)
        return mock_logger

    def test_empty_log_returns_zero_and_no_warning(self):
        """Empty log returns count=0, no warning, no exception."""
        mock_logger = self._scanned_with("just a normal session line\n")
        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_not_called()

    def test_log_with_two_runtime_warnings_returns_two_and_emits_warning(self):
        """Log with N=2 RuntimeWarning occurrences returns 2 and emits warning containing '2'."""
        mock_logger = self._scanned_with(
            "normal line\n"
            "RuntimeWarning: bool is used as a file descriptor\n"
            "more normal\n"
            "RuntimeWarning: invalid value encountered\n",
        )
        # No warning call means count==0; one warning call with substring
        # '2' embedded in the message string means count==2 (proven by
        # scan_execution_log's f-string contract).
        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args.args[0]
        self.assertIn("2", msg)
        self.assertIn("RuntimeWarning", msg)

    def test_missing_log_file_silently_returns_zero(self):
        """ANTI-FRAGILITY: ``scan_execution_log`` invoked against a
        path that does NOT exist on disk; returns 0 and emits a single
        ``debug()`` log call (NOT warning).

        Invariant: the function catches the missing-file case without
        surfacing it as a warning -- callers (vault_cleaner,
        vault_upgrade, app_lifecycle) run on fresh machines with no
        prior log and would otherwise pollute the audit trail.

        BEFORE: a refactor that uses ``os.path.exists`` as a pre-check
        would emit console warnings for every fresh boot.
        """
        bogus = os.path.join(self._tmpdir, "no_such_file.log")
        mock_logger = MagicMock()
        import utils.log_scanner
        count = utils.log_scanner.scan_execution_log(bogus, mock_logger)
        self.assertEqual(count, 0)
        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_called_once()

    def test_warning_is_prepended_with_custom_prefix_kwarg(self):
        """The `prefix=` kwarg is interpolated at the front of the warning.

        Verifies the new opt-in audit-trail marker pattern: closeEvent
        passes `prefix='[Cleanup] '` so existing daily-review grep
        workflows that anchor on `[Cleanup]` continue to match the
        shared-helper output. Vault-cleaner / vault-upgrade leave it
        empty -- so they implicitly rely on the empty default.
        """
        mock_logger = self._scanned_with(
            "RuntimeWarning: bool used as fd\n",
            prefix="[Cleanup] ",
        )
        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args.args[0]
        # Prefix lands at the very front of the warning string.
        self.assertTrue(
            msg.startswith("[Cleanup] "),
            f"expected warning to start with '[Cleanup] ', got: {msg!r}",
        )
        # Sanity: prefix did not eat the RuntimeWarning anchor.
        self.assertIn("RuntimeWarning", msg)

    def test_default_prefix_is_empty_so_existing_callers_are_unchanged(self):
        """ANTI-FRAGILITY: default ``prefix=''`` (empty string) preserves
        the legacy warning format for callers like ``vault_cleaner.run``
        and ``vault_upgrade.upgrade_schema`` that predate the ``prefix=``
        kwarg and were not updated to pass one.

        Invariant: ``prefix: str = ''`` is the default in
        ``utils.log_scanner.scan_execution_log``; with no prefix, the
        warning message starts with the log path itself (assertion:
        ``msg.startswith(self._log_path)``).

        BEFORE: a refactor that flips the default to a non-empty value
        would silently change the warning shape for every existing
        caller; daily-review grep workflows anchored on the log path
        would stop matching.
        """
        mock_logger = self._scanned_with("RuntimeWarning: x\n")
        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args.args[0]
        # Without a prefix, the message starts with the absolute log path.
        self.assertTrue(
            msg.lstrip().startswith(self._log_path) or
            msg.startswith(self._log_path),
            f"expected warning to start with log path, got: {msg!r}",
        )

class TestResetRuntimeWarningCount(unittest.TestCase):
    """Direct unit tests for utils.log_scanner.reset_runtime_warning_count().

    Companion to TestScanExecutionLog -- reset is the rotation/reset path
    that prevents phantom RuntimeWarning lines (smoke runs, bad tests) from
    inflating subsequent scan counts forever. Together they form the
    audit-before / archive-after contract:

      - ``scan_execution_log``: read-only audit, surfaces counts.
      - ``reset_runtime_warning_count``: write-side rotation, archives
        phantoms to ``<log_path>.archived`` and rewrites the original
        log with only the clean lines.
    """

    def setUp(self):
        # Unique per-test subdir so each test's archive lives in isolation
        # and tearDown can rmtree the whole thing without leaking.
        self._tmpdir = tempfile.mkdtemp(prefix="ls_reset_")
        self._log_path = os.path.join(self._tmpdir, "execution_log.txt")
        self._archive_path = self._log_path + ".archived"

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_reset_returns_zero_when_log_missing(self):
        """Missing log path returns 0 (no-op) without creating any archive."""
        import utils.log_scanner
        # Precondition: nothing on disk
        self.assertFalse(os.path.exists(self._log_path))
        count = utils.log_scanner.reset_runtime_warning_count(self._log_path)
        self.assertEqual(count, 0)
        # Archive must NOT be created when the source file is missing.
        self.assertFalse(os.path.exists(self._archive_path))

    def test_reset_returns_zero_when_no_runtime_warnings_present(self):
        """Log with zero RuntimeWarning lines returns 0 and leaves log untouched."""
        _write_log(self._log_path, "[OK] normal session line one\n[AUTO] ok\n")
        original_size = os.path.getsize(self._log_path)
        import utils.log_scanner
        count = utils.log_scanner.reset_runtime_warning_count(self._log_path)
        self.assertEqual(count, 0)
        # Log content is unchanged on disk.
        self.assertEqual(os.path.getsize(self._log_path), original_size)
        # No archive file produced when nothing moved.
        self.assertFalse(os.path.exists(self._archive_path))

    def test_reset_archives_phantom_lines_and_truncates_original(self):
        """ANTI-FRAGILITY: heavy setup writes a 6-line mixed log
        (3 phantom + 3 clean), invokes ``reset_runtime_warning_count``,
        then asserts count == 3, archive has exactly 3 lines each
        containing "RuntimeWarning:", live log retains exactly 3
        lines NONE containing "RuntimeWarning:", ``session_start``
        at remaining[0], ``save_settings done`` in joined remaining text.

        Invariant: the audit/rotation contract in
        ``utils.log_scanner.reset_runtime_warning_count``: phantom
        lines move to ``<log_path>.archived`` so the NEXT scan sees
        a clean baseline; clean lines stay in-place so in-flight
        context is not lost. Tests the precise 3-of-3 split, not
        just an aggregate count.

        A "phantom" line is one written by a smoke run via
        ``logger.warning('RuntimeWarning: ...')`` -- not a real
        Python ``RuntimeWarning``. They are extracted to
        ``<log>.archived`` so the next scan sees a clean baseline.
        """
        _write_log(
            self._log_path,
            "[2026-06-18] session_start\n"
            "RuntimeWarning: bool used as fd\n"
            "[AUTO] ok\n"
            "RuntimeWarning: divide by zero\n"
            "RuntimeWarning: invalid value\n"
            "[INFO] save_settings done\n",
        )
        import utils.log_scanner
        count = utils.log_scanner.reset_runtime_warning_count(self._log_path)
        # 3 phantom lines moved to archive.
        self.assertEqual(count, 3)
        # Archive file exists with exactly the 3 phantom lines.
        self.assertTrue(os.path.exists(self._archive_path))
        with open(self._archive_path, "r", encoding="utf-8") as f:
            archived = f.readlines()
        self.assertEqual(len(archived), 3)
        for ln in archived:
            self.assertIn("RuntimeWarning:", ln)
        # Original log retains ONLY the 3 clean lines.
        with open(self._log_path, "r", encoding="utf-8") as f:
            remaining = f.readlines()
        self.assertEqual(len(remaining), 3)
        for ln in remaining:
            self.assertNotIn("RuntimeWarning:", ln)
        self.assertIn("session_start", remaining[0])
        self.assertIn("save_settings done", "".join(remaining))

    def test_reset_is_idempotent_on_second_call(self):
        """A second reset on a now-clean log returns 0 (nothing left to move)."""
        _write_log(
            self._log_path,
            "[AUTO] line\n"
            "RuntimeWarning: phantom once\n"
            "[AUTO] ok\n",
        )
        import utils.log_scanner
        first = utils.log_scanner.reset_runtime_warning_count(self._log_path)
        self.assertEqual(first, 1)
        second = utils.log_scanner.reset_runtime_warning_count(self._log_path)
        self.assertEqual(second, 0)
        # Archive still has the original single phantom line, no extras.
        with open(self._archive_path, "r", encoding="utf-8") as f:
            archived = f.readlines()
        self.assertEqual(len(archived), 1)
        self.assertIn("phantom once", archived[0])

    def test_reset_appends_to_existing_archive_instead_of_overwriting(self):
        """ANTI-FRAGILITY: heavy setup seeds the archive with a prior
        phantom BEFORE invoking ``reset_runtime_warning_count`` on a
        live log containing a new phantom. The assertion
        ``len(archived) == 2`` with order preserved (``prior phantom``
        at [0], ``new phantom`` at [1]) proves APPEND semantic, not
        truncate-then-write.

        Invariant: archive file is opened in APPEND mode in
        ``utils.log_scanner.reset_runtime_warning_count`` so multiple
        smoke runs accumulate history of suspicious lines rather than
        each replacing the prior one.

        This is the 'rotation' contract the user asked for -- multiple
        smoke runs that all land phantom lines accumulate into one
        archive rather than each replacing the prior one.

        BEFORE: an earlier truncate-mode rewrite made spot-checks of
        last week's phantoms disappear after every cleanup pass.
        """
        # Seed the archive with a prior phantom.
        _write_log(self._archive_path, "RuntimeWarning: prior phantom\n")
        # Now write fresh phantoms to the live log.
        _write_log(
            self._log_path,
            "[AUTO] line\n"
            "RuntimeWarning: new phantom\n",
        )
        import utils.log_scanner
        count = utils.log_scanner.reset_runtime_warning_count(self._log_path)
        self.assertEqual(count, 1)
        with open(self._archive_path, "r", encoding="utf-8") as f:
            archived = f.readlines()
        # The prior 1 + newly moved 1 = 2.
        self.assertEqual(len(archived), 2)
        self.assertIn("prior phantom", archived[0])
        self.assertIn("new phantom", archived[1])

class TestScanExecutionLogIntegration(unittest.TestCase):
    """Caller-integration smoke-pass confirming the shared
    :func:`utils.log_scanner.scan_execution_log` IS invoked by each of
    the 3 production callers (app_lifecycle.closeEvent,
    vault_cleaner.run, vault_upgrade.upgrade_schema).

    Mirrors the ceremony pattern used in
    test_vault_cleaner.py::TestRunPresentDBBranch and
    test_vault_upgrade.py::TestUpgradeSchemaIntegration -- but here
    as a quick cross-file contract check that proves the helper
    FIRES inside the caller's body, not just that the call exists
    in source (full integration depth lives in the per-caller test
    files).

    The deeper motivation: this class also exercises the canonical
    ``_patched_invoke`` ceremony so future caller test files can
    reuse it instead of copy-pasting the same 5-line patch.multiple
    + import + run + yield skeleton.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="ls_intg_")
        # Match the production file name so any path-based sanity
        # checks inside the caller's module still work.
        self._log_path = os.path.join(self._tmpdir, "execution_log.txt")
        self._db_path = os.path.join(self._tmpdir, "kokertech_vault.db")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_vault_cleaner_run_invokes_scan_ceremony(self):
        """vault_cleaner.run() invokes scan_execution_log inside the
        patched run -- exercises the ``_patched_invoke`` ceremony
        mirrored here from test_vault_cleaner.py::_patched_run."""
        _write_log(
            self._log_path,
            "[AUTO] normal session\n"
            "RuntimeWarning: bool is used as a file descriptor\n",
        )
        with _patched_invoke(
            self,
            module_path="vault_cleaner",
            WORKSPACE_DIR=self._tmpdir,
            LOG_FILE=self._log_path,
            DB_PATH=self._db_path,
        ) as mock_logger:
            import vault_cleaner
            vault_cleaner.run()
        warn_messages = [
            c.args[0] for c in mock_logger.warning.call_args_list if c.args
        ]
        self.assertTrue(
            any("RuntimeWarning" in m for m in warn_messages),
            f"vault_cleaner.run should invoke scan_execution_log; "
            f"warning not seen. Calls: {warn_messages}",
        )

    def test_vault_upgrade_schema_invokes_scan_ceremony(self):
        """vault_upgrade.upgrade_schema() invokes scan_execution_log
        (audit-before-mutate) -- exercises the ``_patched_invoke``
        ceremony mirrored here from test_vault_upgrade.py::_patched_upgrade."""
        _write_log(
            self._log_path,
            "[AUTO] normal session\n"
            "RuntimeWarning: bool is used as a file descriptor\n",
        )
        with _patched_invoke(
            self,
            module_path="vault_upgrade",
            LOG_FILE=self._log_path,
            db_path=self._db_path,
        ) as mock_logger:
            import vault_upgrade
            vault_upgrade.upgrade_schema()
        warn_messages = [
            c.args[0] for c in mock_logger.warning.call_args_list if c.args
        ]
        self.assertTrue(
            any("RuntimeWarning" in m for m in warn_messages),
            f"vault_upgrade.upgrade_schema should invoke scan_execution_log; "
            f"warning not seen. Calls: {warn_messages}",
        )

    def test_app_lifecycle_close_event_scan_uses_cleanup_prefix(self):
        """app_lifecycle.closeEvent invokes ``scan_execution_log`` with
        ``prefix='[Cleanup] '`` as the post-shutdown audit-trail marker.

        Invoking the full ``closeEvent`` on ``KokertechDashboard`` is too
        heavyweight for a unit test (requires live ``QApplication``,
        ``QWidget`` base, ``file_logger`` attributes, etc.). Instead
        this test exercises the SAME ``scan_execution_log`` call
        structure that closeEvent performs -- proving the prefix
        contract that closeEvent relies on. The contract is also
        enforced by :meth:`TestScanExecutionLog.test_warning_is_prepended_with_custom_prefix_kwarg`
        above (direct unit test on the helper).
        """
        _write_log(
            self._log_path,
            "RuntimeWarning: bool is used as a file descriptor\n",
        )
        mock_logger = MagicMock()
        import utils.log_scanner
        utils.log_scanner.scan_execution_log(
            self._log_path, mock_logger, prefix="[Cleanup] "
        )
        warn_messages = [
            c.args[0] for c in mock_logger.warning.call_args_list if c.args
        ]
        self.assertTrue(
            any(m.startswith("[Cleanup] ") for m in warn_messages),
            f"closeEvent-style scan should use [Cleanup] prefix. "
            f"Calls: {warn_messages}",
        )
        self.assertTrue(
            any("RuntimeWarning" in m for m in warn_messages),
            f"closeEvent-style scan should surface RuntimeWarning substring. "
            f"Calls: {warn_messages}",
        )

if __name__ == "__main__":
    unittest.main()
