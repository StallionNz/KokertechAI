# tests/test_conn_pool_path_guard.py -- Path-aware connection pool guard.
# REGRESSION GUARD: v0.21.9 _conn_is_for_path / _get_conn stale-conn discard
# in memory_vault. When DB_PATH changes (test patches it to a temp vault), a
# blind pop() would return a connection bound to the OLD database file ->
# sqlite3.OperationalError: no such table (KNOWLEDGE.md section 20.6 bug class,
# seen in test_call_chain_e2e.py serial mode). _get_conn must discard stale
# connections at pop time.

import os
import sqlite3
import tempfile
import unittest

import memory_vault


def _cleanup_db(path):
    if not os.path.exists(path):
        return
    for attempt in range(3):
        try:
            os.remove(path)
            return
        except PermissionError:
            if attempt < 2:
                import time
                time.sleep(0.05 * (attempt + 1))


class TestConnIsForPath(unittest.TestCase):

    def setUp(self):
        fd, self.db_a = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        fd, self.db_b = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self._orig_path = memory_vault.DB_PATH

    def tearDown(self):
        memory_vault.DB_PATH = self._orig_path
        memory_vault._clear_connection_pool()
        _cleanup_db(self.db_a)
        _cleanup_db(self.db_b)

    def test_matching_file_returns_true(self):
        conn = sqlite3.connect(self.db_a)
        try:
            self.assertTrue(memory_vault._conn_is_for_path(conn, self.db_a))
        finally:
            conn.close()

    def test_path_change_returns_false(self):
        conn = sqlite3.connect(self.db_a)
        try:
            self.assertFalse(memory_vault._conn_is_for_path(conn, self.db_b))
        finally:
            conn.close()

    def test_in_memory_conn_returns_false(self):
        conn = sqlite3.connect(':memory:')
        try:
            self.assertFalse(memory_vault._conn_is_for_path(conn, ':memory:'))
        finally:
            conn.close()

    def test_closed_conn_returns_false(self):
        conn = sqlite3.connect(self.db_a)
        conn.close()
        self.assertFalse(memory_vault._conn_is_for_path(conn, self.db_a))

    def test_get_conn_discards_stale_pooled_conn(self):
        """REGRESSION GUARD: _get_conn never returns a conn bound to the OLD
        DB_PATH after a path change (fires if reverted to a blind pop)."""
        memory_vault.DB_PATH = self.db_a
        conn_a = memory_vault._get_conn()
        self.assertTrue(memory_vault._conn_is_for_path(conn_a, self.db_a))
        memory_vault._return_conn(conn_a)

        memory_vault.DB_PATH = self.db_b
        got = memory_vault._get_conn()
        try:
            self.assertTrue(
                memory_vault._conn_is_for_path(got, self.db_b),
                'REGRESSION: stale conn bound to db_a returned after DB_PATH changed',
            )
        finally:
            memory_vault._return_conn(got)


if __name__ == '__main__':
    unittest.main()
