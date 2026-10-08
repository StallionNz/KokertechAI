"""Unit tests for vault_upgrade.py - RuntimeWarning audit-before-mutate invariant.

Mirrors test_vault_cleaner.py's 6-test structure (3 helper unit tests on
utils.log_scanner.scan_execution_log + 3 caller-integration tests for
vault_upgrade.upgrade_schema).

All tests redirect vault_upgrade.LOG_FILE to a per-test tempdir so test runs
never read or write the production execution_log.txt.

Per user requirement, the caller-integration tests verify the audit-before-
mutate invariant WITHOUT executing upgrade_schema against the production
vault -- achieved via:
  - AST-level source inspection (line-number ordering of scan_execution_log
    vs sqlite3.connect inside upgrade_schema body)
  - Direct invocation of utils.log_scanner.scan_execution_log with temp
    files (no upgrade_schema body execution)
"""

from __future__ import annotations

import ast
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
VAULT_UPGRADE_PATH = PROJECT_ROOT / "vault_upgrade.py"

def _write_log(log_path, body):
    """Write body to log_path (UTF-8)."""
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(body)

def _audit_and_connect_linenos_in_upgrade_schema():
    """Walk the AST of vault_upgrade.py and return
    (audit_lineno, connect_lineno) for the first occurrences inside
    upgrade_schema body, or (None, None) if either is missing.

    Static analysis only -- no upgrade_schema() execution, so the
    production kokertech_vault.db is never touched.
    """
    src = VAULT_UPGRADE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    upgrade_func = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "upgrade_schema"),
        None,
    )
    if upgrade_func is None:
        return (None, None)
    audit_lineno = None
    connect_lineno = None
    for stmt in upgrade_func.body:
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Call):
                f = sub.func
                if isinstance(f, ast.Name) and f.id == "scan_execution_log":
                    if audit_lineno is None:
                        audit_lineno = sub.lineno
                elif isinstance(f, ast.Attribute) and f.attr == "connect":
                    if connect_lineno is None:
                        connect_lineno = sub.lineno
    return (audit_lineno, connect_lineno)

def _make_empty_vault_db(db_path):
    """Create the minimal schema vault_upgrade.upgrade_schema() expects to ALTER.

    Mirrors the same-named helper in test_vault_cleaner.py so both files
    seed equivalent vouch-for-patching tempfile vaults. Production
    kokertech_vault.db at C:\\KokertechAI\\kokertech_vault.db is NEVER
    opened by tests -- db_path is patched to a tempfile.mkdtemp path
    inside _patched_upgrade.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE core_memories (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

def _warn_strings(mock):
    """Return positional first-argument strings from every recorded
    ``mock.warning(...)`` call.

    Mirrors the same-named helper in test_vault_cleaner.py so the caller-
    integration tests in both files share one extraction pattern. Filters
    out calls made with no positional args (defensive against future
    setups that pass kwargs only).
    """
    return [
        c.args[0] for c in mock.warning.call_args_list if c.args
    ]

def _ok_strings(mock):
    """Return positional first-argument strings from every recorded
    ``mock.ok(...)`` call. Sibling of :func:`_warn_strings`.

    Same shape, different verb: ``scan_execution_log`` fires
    ``logger.warning(...)`` while ``vault_upgrade.upgrade_schema`` /
    ``vault_cleaner.run`` success paths fire ``logger.ok(...)``. Both
    follow the same recorded-call-inspection pattern, so the dedup
    helper takes the same form. Filters out kwargs-only calls.
    """
    return [
        c.args[0] for c in mock.ok.call_args_list if c.args
    ]

@contextmanager
def _patched_upgrade(test_case):
    """Apply vault_upgrade's module-level patches (LOG_FILE, db_path, logger),
    call vault_upgrade.upgrade_schema() inside the patch, then yield the
    captured mock_logger so the caller can inspect recorded calls after
    patch.multiple restores vault_upgrade.logger to its real value.

    Mirrors _patched_run from test_vault_cleaner.py so the caller-
    integration tests across both files share one patch.multiple + yield
    mock logger pattern. The local-MagicMock captures every recorded call
    (.warning, .ok, .info) for post-block assertions, even after
    patch.multiple restores the real logger on context exit.

    Production kokertech_vault.db is NEVER touched: db_path is patched to
    a tempfile.mkdtemp path before upgrade_schema() runs.
    """
    mock_logger = MagicMock()
    with patch.multiple(
        "vault_upgrade",
        LOG_FILE=test_case._log_path,
        logger=mock_logger,
        db_path=test_case._db_path,
    ):
        import vault_upgrade
        vault_upgrade.upgrade_schema()
        yield mock_logger

class TestScanExecutionLog(unittest.TestCase):
    """Direct unit tests on utils.log_scanner.scan_execution_log.

    Mirrors test_vault_cleaner.py::TestScanExecutionLog so a single
    regression in the shared helper trips BOTH test files.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="vu_scan_")
        self._log_path = os.path.join(self._tmpdir, "execution_log.txt")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_empty_log_returns_zero_and_no_warning(self):
        """Empty log returns count=0, no warning, no exception."""
        _write_log(self._log_path, "just a normal session line\n")
        mock_logger = MagicMock()
        from utils.log_scanner import scan_execution_log
        count = scan_execution_log(self._log_path, mock_logger)
        self.assertEqual(count, 0)
        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_not_called()

    def test_log_with_two_runtime_warnings_returns_two_and_emits_warning(self):
        """Log with N=2 RuntimeWarning occurrences returns 2 and emits warning containing '2'."""
        _write_log(
            self._log_path,
            "normal line\n"
            "RuntimeWarning: bool is used as a file descriptor\n"
            "more normal\n"
            "RuntimeWarning: invalid value encountered\n",
        )
        mock_logger = MagicMock()
        from utils.log_scanner import scan_execution_log
        count = scan_execution_log(self._log_path, mock_logger)
        self.assertEqual(count, 2)
        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args.args[0]
        self.assertIn("2", msg)
        self.assertIn("RuntimeWarning", msg)

    def test_missing_log_file_silently_returns_zero(self):
        """Missing log file caught quietly: count=0, no warning, debug fires once."""
        bogus = os.path.join(self._tmpdir, "no_such_file.log")
        mock_logger = MagicMock()
        from utils.log_scanner import scan_execution_log
        count = scan_execution_log(bogus, mock_logger)
        self.assertEqual(count, 0)
        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_called_once()

class TestUpgradeSchemaAuditOrdering(unittest.TestCase):
    """Audit-before-mutate invariants for vault_upgrade.upgrade_schema().

    Per user requirement, these tests verify the audit-before-mutate invariant
    WITHOUT executing upgrade_schema() against the production vault. Two
    techniques are used:

    1. AST-level source inspection: line-number ordering of scan_execution_log
       versus sqlite3.connect inside upgrade_schema body.

    2. Direct invocation of scan_execution_log with temp files. Verifies the
       underlying helper produces the right output contract, which is what
       upgrade_schema() relies on.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="vu_audit_")

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_audit_call_appears_before_sqlite_connect_in_function_body(self):
        """upgrade_schema body must call scan_execution_log BEFORE
        sqlite3.connect so the audit surfaces even if downstream schema
        ALTER TABLE statements fail (e.g. column already exists)."""
        audit_lineno, connect_lineno = _audit_and_connect_linenos_in_upgrade_schema()
        self.assertIsNotNone(
            audit_lineno,
            "scan_execution_log call not found in upgrade_schema body",
        )
        self.assertIsNotNone(
            connect_lineno,
            "sqlite3.connect call not found in upgrade_schema body",
        )
        self.assertLess(
            audit_lineno,
            connect_lineno,
            "Audit-before-mutate invariant violated: "
            "scan at line " + str(audit_lineno) +
            ", connect at line " + str(connect_lineno),
        )

    def test_audit_directly_called_with_runtime_warnings_surfaces_count_via_logger(self):
        """Calling scan_execution_log directly against a temp log with
        N=2 RuntimeWarning entries returns 2 and emits a warning containing
        '2' -- without executing the full upgrade_schema() body."""
        log_path = os.path.join(self._tmpdir, "execution_log.txt")
        _write_log(
            log_path,
            "RuntimeWarning: bool is used as a file descriptor\n"
            "more normal\n"
            "RuntimeWarning: invalid value encountered\n",
        )
        mock_logger = MagicMock()
        from utils.log_scanner import scan_execution_log
        with patch("vault_upgrade.LOG_FILE", log_path):
            count = scan_execution_log(log_path, mock_logger)
        self.assertEqual(count, 2)
        mock_logger.warning.assert_called_once()
        msg = mock_logger.warning.call_args.args[0]
        self.assertIn("2", msg)
        self.assertIn("RuntimeWarning", msg)

    def test_audit_directly_called_with_empty_log_returns_zero_and_emits_no_warning(self):
        """Calling scan_execution_log against an empty temp log returns 0
        and emits no warning -- without executing upgrade_schema() body."""
        log_path = os.path.join(self._tmpdir, "execution_log.txt")
        _write_log(log_path, "")
        mock_logger = MagicMock()
        from utils.log_scanner import scan_execution_log
        with patch("vault_upgrade.LOG_FILE", log_path):
            count = scan_execution_log(log_path, mock_logger)
        self.assertEqual(count, 0)
        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_not_called()

class TestUpgradeSchemaIntegration(unittest.TestCase):
    """Caller-integration tests for vault_upgrade.upgrade_schema().

    The earlier TestUpgradeSchemaAuditOrdering covers the audit-before-mutate
    invariant via static AST analysis + direct scan_execution_log calls --
    but does NOT actually exercise upgrade_schema() against a vault. This
    class does: it patches vault_upgrade.db_path to a tempfile.mkdtemp
    path, lets upgrade_schema() run on that vault, and verifies the audit
    log surfaces during REAL function-body execution. Complements the
    audit-ordering static checks with a runtime smoke-pass that runs the
    full ALTER TABLE migration sequence against a sandbox vault.

    Production kokertech_vault.db at C:\\KokertechAI\\kokertech_vault.db is
    NEVER touched by these tests -- db_path is patched to a tempfile path.

    Mirrors test_vault_cleaner.py::TestRunPresentDBBranch's structure
    (setUp seeds vault + log, _patched_* orchestrates, assertions verify
    logger.warning + logger.ok calls + post-state-of-vault). Two tests:
    one with N=2 RuntimeWarnings (audit warning fires + migration ok),
    one with 0 RuntimeWarnings (no audit warning + migration still ok).
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="vu_present_")
        self._log_path = os.path.join(self._tmpdir, "execution_log.txt")
        self._db_path = os.path.join(self._tmpdir, "kokertech_vault.db")
        _make_empty_vault_db(self._db_path)

    def tearDown(self):
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_upgrade_with_runtime_warnings_emits_scan_warning_then_schema_success(self):
        """upgrade_schema() on a tempfile vault with N=2 RuntimeWarning entries:
        audit-before-mutate: scan fires BEFORE the ALTER TABLE statements,
        surfaces count=2 via logger.warning; schema migration still completes
        (logger.ok with all 3 column names); new columns are actually present
        in the (tempfile) core_memories -- proving the full function body ran.
        """
        _write_log(
            self._log_path,
            "[AUTO] normal session\n"
            "RuntimeWarning: bool is used as a file descriptor\n"
            "[AUTO] ok\n"
            "RuntimeWarning: invalid value encountered\n",
        )
        with _patched_upgrade(self) as mock_logger:
            pass  # upgrade_schema() already ran inside the context.

        warn_messages = _warn_strings(mock_logger)
        # Exactly one RuntimeWarning-scan warning, count=2 surfaced.
        rt_warnings = [m for m in warn_messages if "RuntimeWarning" in m]
        self.assertEqual(
            len(rt_warnings), 1,
            f"Expected exactly one RuntimeWarning-scan warning, got {rt_warnings}",
        )
        self.assertIn("2", rt_warnings[0])
        self.assertIn("RuntimeWarning", rt_warnings[0])

        # Schema-expansion success log fires -- proves full body ran (downstream
        # ALTER TABLE statements reached the .ok() call after scan).
        ok_messages = _ok_strings(mock_logger)
        self.assertTrue(
            any(
                "last_accessed" in m and "metadata" in m and "tags" in m
                for m in ok_messages
            ),
            f"Expected schema-expansion success log with all 3 column names. "
            f".ok() calls: {ok_messages}",
        )

        # Verify the new columns were actually added to OUR tempfile vault
        # (PRODUCTION VAULT IS NEVER OPENED -- db_path is patched).
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "SELECT name FROM pragma_table_info('core_memories')"
            )
            column_names = {row[0] for row in cursor.fetchall()}
            for col in ("last_accessed", "metadata", "tags"):
                self.assertIn(
                    col, column_names,
                    f"Column {col} not added to core_memories by upgrade_schema",
                )
        finally:
            conn.close()

    def test_upgrade_with_clean_log_emits_no_scan_warning_but_schema_still_migrates(self):
        """upgrade_schema() on a tempfile vault with 0 RuntimeWarning entries:
        no scan warning fires; schema migration STILL completes -- proving
        audit-before-mutate does not block downstream work on a clean log."""
        _write_log(
            self._log_path,
            "[AUTO] totally normal session\n"
            "nothing alarming here\n",
        )
        with _patched_upgrade(self) as mock_logger:
            pass

        warn_messages = _warn_strings(mock_logger)
        # No RuntimeWarning-scan warning -- the log is clean.
        rt_warnings = [m for m in warn_messages if "RuntimeWarning" in m]
        self.assertEqual(
            rt_warnings, [],
            f"Expected NO RuntimeWarning warnings on a clean log, got {rt_warnings}",
        )

        # Schema-expansion success log STILL fires -- audit-before-mutate
        # doesn't gate downstream ALTER TABLE on a warning having fired.
        ok_messages = _ok_strings(mock_logger)
        self.assertTrue(
            any("last_accessed" in m for m in ok_messages),
            f"Expected schema-expansion success log to fire on clean log. "
            f".ok() calls: {ok_messages}",
        )

        # Confirm the vault actually got the new columns even when no audit
        # warning fired (PRODUCTION VAULT IS NEVER OPENED).
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute(
                "SELECT name FROM pragma_table_info('core_memories')"
            )
            column_names = {row[0] for row in cursor.fetchall()}
            self.assertIn("last_accessed", column_names)
            self.assertIn("metadata", column_names)
            self.assertIn("tags", column_names)
        finally:
            conn.close()

if __name__ == "__main__":
    unittest.main()
