"""Unit tests for vault_cleaner.py - RuntimeWarning daily-housekeeping scan
and the DB wipe behavior in run().

Mirrors the post-shutdown RuntimeWarning scan that lives in
app_lifecycle.closeEvent so the count also surfaces during the
`python vault_cleaner.py` daily housekeeping pass.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch


def _make_empty_vault_db(db_path):
    """Create the schema vault_cleaner.run() expects to wipe."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE core_memories (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE memory_links (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE bias_ledger (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE growth_arcs (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

def _write_log(log_path, body):
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(body)

def _warn_strings(mock):
    """Return positional first-argument strings from every recorded
    ``mock.warning(...)`` call.

    Tiny dedup helper for the repeated
    ``[c.args[0] for c in mock_logger.warning.call_args_list if c.args]``
    pattern that appeared at 3 sites in this file. Filters out calls that
    were made with no positional args (defensive against future test
    setups that only pass kwargs).
    """
    return [
        c.args[0] for c in mock.warning.call_args_list if c.args
    ]

def _ok_strings(mock):
    """Return positional first-argument strings from every recorded
    ``mock.ok(...)`` call. Sibling of :func:`_warn_strings`.

    Same shape, different verb: ``scan_execution_log`` fires
    ``logger.warning(...)`` while ``vault_cleaner.run`` /
    ``vault_upgrade.upgrade_schema`` success paths fire
    ``logger.ok(...)``. Both follow the same recorded-call-inspection
    pattern, so the dedup helper takes the same form. Filters out
    kwargs-only calls (defensive against future test setups that pass
    kwargs only).
    """
    return [
        c.args[0] for c in mock.ok.call_args_list if c.args
    ]

@contextmanager
def _patched_run(test_case):
    """Apply vault_cleaner's module-level patches (WORKSPACE_DIR, DB_PATH,
    LOG_FILE, logger), call vault_cleaner.run() inside the patch, then yield
    the captured mock_logger so the caller can inspect recorded calls after
    patch.multiple restores vault_cleaner.logger to its real value.

    Centralized here so the 3 integration tests in TestRunMissingDBBranch and
    TestRunPresentDBBranch share one implementation -- eliminates the 5-line
    local-MagicMock ceremony (mock local, patch.multiple block, run inside,
    yield back) that was previously repeated verbatim in each test.

    The local-MagicMock pattern is deliberate: ``patch.multiple`` restores
    ``vault_cleaner.logger`` on context exit, so the local ``mock_logger``
    reference here preserves every recorded call (.warning, .ok, .debug)
    for post-block assertions.
    """
    mock_logger = MagicMock()
    with patch.multiple(
        "vault_cleaner",
        WORKSPACE_DIR=test_case._ws,
        DB_PATH=test_case._db_path,
        LOG_FILE=test_case._log_path,
        logger=mock_logger,
    ):
        import vault_cleaner
        vault_cleaner.run()
        yield mock_logger

class TestRunMissingDBBranch(unittest.TestCase):
    """run() when kokertech_vault.db does not exist on disk.

    Required user case #1: missing-DB branch fires the scan.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="vc_missing_")
        self._log_path = os.path.join(self._tmpdir, "execution_log.txt")
        self._ws = self._tmpdir
        self._db_path = os.path.join(self._ws, "kokertech_vault.db")
        # INTENTIONALLY do not create the db -- branch under test.

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_missing_db_branch_fires_scan_with_count_surfaced(self):
        """DB-missing branch fires the RuntimeWarning scan and surfaces the count."""
        _write_log(
            self._log_path,
            "RuntimeWarning: bool is used as a file descriptor\n"
            "[AUTO] normal line\n"
            "RuntimeWarning: divide by zero\n"
            "RuntimeWarning: invalid value encountered\n",
        )
        # Capture the MagicMock locally via _patched_run -- this single helper
        # standardizes the patch.multiple + run() ceremony and yields the same
        # mock_logger instance so recorded calls survive the patch exit.
        with _patched_run(self) as mock_logger:
            # run() has already fired inside _patched_run; no body needed here.
            pass

        warn_messages = _warn_strings(mock_logger)

        self.assertTrue(
            any("Vault database does not exist" in m for m in warn_messages),
            f"Missing missing-DB warning. Calls: {warn_messages}",
        )
        count_warnings = [m for m in warn_messages if "RuntimeWarning" in m]
        self.assertEqual(
            len(count_warnings), 1,
            f"Expected exactly one RuntimeWarning-scan warning, got {count_warnings}",
        )
        self.assertIn("3", count_warnings[0])

class TestRunPresentDBBranch(unittest.TestCase):
    """run() when kokertech_vault.db exists and is wiped.

    Required user cases #2 and #3:
    - present-DB branch fires the scan with count surfaced
    - fake-log with 0 RuntimeWarning entries does not emit any warning
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="vc_present_")
        self._log_path = os.path.join(self._tmpdir, "execution_log.txt")
        self._ws = self._tmpdir
        self._db_path = os.path.join(self._ws, "kokertech_vault.db")
        _make_empty_vault_db(self._db_path)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_present_db_branch_fires_scan_with_count_surfaced(self):
        """DB-present branch fires the scan with count=5 surfaced via logger.warning."""
        _write_log(
            self._log_path,
            "".join(
                "RuntimeWarning: bool is used as a file descriptor\n"
                for _ in range(5)
            ),
        )
        # Capture the MagicMock locally via _patched_run -- single helper
        # standardizes the patch.multiple + run() ceremony.
        with _patched_run(self) as mock_logger:
            # run() has already fired inside _patched_run; no body needed here.
            pass

        ok_messages = _ok_strings(mock_logger)
        self.assertTrue(
            any("Vault Memory and Cognitive Ledgers" in m for m in ok_messages),
            f"Missing DB-wipe success log. .ok() calls: {ok_messages}",
        )

        warn_messages = _warn_strings(mock_logger)

        rt_warnings = [m for m in warn_messages if "RuntimeWarning" in m]
        self.assertEqual(
            len(rt_warnings), 1,
            f"Expected exactly one RuntimeWarning-scan warning, got {rt_warnings}",
        )
        self.assertIn("5", rt_warnings[0])

        conn = sqlite3.connect(self._db_path)
        try:
            for table in ("core_memories", "memory_links", "bias_ledger", "growth_arcs"):
                cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                self.assertEqual(
                    cursor.fetchone()[0], 0,
                    f"{table} should be wiped to 0 rows post-run()",
                )
        finally:
            conn.close()

    def test_present_db_with_zero_warnings_emits_no_scan_warning(self):
        _write_log(self._log_path, "totally normal session\nnothing alarming here\n")
        # Capture the MagicMock locally via _patched_run -- single helper
        # standardizes the patch.multiple + run() ceremony.
        with _patched_run(self) as mock_logger:
            # run() has already fired inside _patched_run; no body needed here.
            pass

        warn_messages = _warn_strings(mock_logger)

        rt_warnings = [m for m in warn_messages if "RuntimeWarning" in m]
        self.assertEqual(
            rt_warnings, [],
            f"Expected NO RuntimeWarning warnings on a clean log, got {rt_warnings}",
        )

        ok_messages = _ok_strings(mock_logger)
        self.assertTrue(
            any("Vault Memory and Cognitive Ledgers" in m for m in ok_messages),
            "Expected DB-wipe success log to fire (proves we hit present-DB branch).",
        )

if __name__ == "__main__":
    unittest.main()
