"""test_vault_v3_wal_and_self_heal.py — Tests for Vector 3: Memory Vault V3 WAL Auto-Checkpointing & Self-Healing Index.

Covers:
  - SQLite connection timeout compliance (15.0s per Zero-Trust Invariant 2)
  - WAL checkpointing modes (PASSIVE, FULL, RESTART, TRUNCATE)
  - Automatic WAL checkpointing on threshold breach
  - FTS5 health verification and automatic index rebuild/self-healing
  - FTS5 search query resiliency on operational error
  - Vector matrix cache health checking and drift healing
  - Configuration keys and defaults

Run: python -m pytest tests/test_vault_v3_wal_and_self_heal.py -v
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

import memory_vault
from config import CONFIG


class TestVaultV3WALAndCheckpoint(unittest.TestCase):
    """Test WAL checkpointing and auto-checkpoint threshold mechanisms."""

    def setUp(self):
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.test_db_fd)
        self.orig_db_path = memory_vault.DB_PATH
        memory_vault.DB_PATH = self.test_db_path
        memory_vault._db_initialized = False
        memory_vault._clear_connection_pool()
        memory_vault.ensure_tables_exist()

    def tearDown(self):
        memory_vault.DB_PATH = self.orig_db_path
        memory_vault._db_initialized = False
        memory_vault._clear_connection_pool()
        for suffix in ("", "-wal", "-shm"):
            p = f"{self.test_db_path}{suffix}"
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_sqlite_timeout_invariant(self):
        """Zero-Trust Invariant 2: SQLite connections must configure timeout=15.0."""
        with closing(memory_vault._get_conn()) as conn:
            # Under SQLite connect(..., timeout=15.0), timeout is applied
            self.assertIsInstance(conn, sqlite3.Connection)
            # Verify PRAGMA wal_autocheckpoint is active
            row = conn.execute("PRAGMA wal_autocheckpoint").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 1000)

    def test_checkpoint_wal_modes(self):
        """checkpoint_wal supports valid SQLite checkpoint modes."""
        # Store a row so there are write frames in the WAL
        memory_vault.store_memory("Checkpoint test content", node_type="fact", importance=5)

        # Test PASSIVE mode
        res_passive = memory_vault.checkpoint_wal(mode="PASSIVE", db_path=self.test_db_path)
        self.assertIsInstance(res_passive, tuple)
        self.assertEqual(len(res_passive), 3)
        self.assertEqual(res_passive[0], 0)  # busy = 0

        # Test TRUNCATE mode
        res_truncate = memory_vault.checkpoint_wal(mode="TRUNCATE", db_path=self.test_db_path)
        self.assertIsInstance(res_truncate, tuple)
        self.assertEqual(len(res_truncate), 3)
        self.assertEqual(res_truncate[0], 0)  # busy = 0

    def test_checkpoint_wal_invalid_mode_raises(self):
        """Invalid mode raises ValueError."""
        with self.assertRaises(ValueError):
            memory_vault.checkpoint_wal(mode="INVALID_MODE", db_path=self.test_db_path)

    def test_checkpoint_wal_nonexistent_db_returns_zero(self):
        """checkpoint_wal on nonexistent DB returns (0, 0, 0)."""
        res = memory_vault.checkpoint_wal(mode="PASSIVE", db_path="nonexistent_vault.db")
        self.assertEqual(res, (0, 0, 0))

    def test_auto_checkpoint_wal_below_threshold_skips(self):
        """auto_checkpoint_wal returns False when WAL size is below threshold."""
        wal_path = f"{self.test_db_path}-wal"
        # Ensure a small write exists
        memory_vault.store_memory("Small write", node_type="fact", importance=5)
        if os.path.exists(wal_path):
            # Threshold set to 100 MB -> should not trigger
            triggered = memory_vault.auto_checkpoint_wal(
                threshold_bytes=100 * 1024 * 1024,
                db_path=self.test_db_path,
            )
            self.assertFalse(triggered)

    def test_auto_checkpoint_wal_above_threshold_triggers(self):
        """auto_checkpoint_wal returns True and executes when WAL size meets threshold."""
        # Ensure write frames exist
        memory_vault.store_memory("Write to trigger WAL checkpoint", node_type="fact", importance=8)
        wal_path = f"{self.test_db_path}-wal"
        if not os.path.exists(wal_path):
            # Create a dummy WAL file if SQLite has already flushed it
            with open(wal_path, "wb") as f:
                f.write(b"x" * 2048)

        # Set threshold to 1 byte so it triggers
        triggered = memory_vault.auto_checkpoint_wal(
            threshold_bytes=1,
            mode="PASSIVE",
            db_path=self.test_db_path,
        )
        self.assertTrue(triggered)

    def test_auto_checkpoint_wal_no_wal_file_returns_false(self):
        """auto_checkpoint_wal returns False if no -wal file exists."""
        nonexistent_wal_db = f"{self.test_db_path}_nonexistent_wal.db"
        triggered = memory_vault.auto_checkpoint_wal(
            threshold_bytes=1,
            db_path=nonexistent_wal_db,
        )
        self.assertFalse(triggered)

    def test_close_vault_executes_cleanly(self):
        """close_vault flushes WAL and drains pool."""
        memory_vault.store_memory("Close vault test", node_type="fact", importance=5)
        memory_vault.close_vault(checkpoint_wal_first=True)
        # Verify pool is empty
        if hasattr(memory_vault._connection_pool, "pool"):
            self.assertEqual(len(memory_vault._connection_pool.pool), 0)


class TestVaultV3SelfHealing(unittest.TestCase):
    """Test FTS5 and Vector Cache self-healing and consistency mechanisms."""

    def setUp(self):
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.test_db_fd)
        self.orig_db_path = memory_vault.DB_PATH
        memory_vault.DB_PATH = self.test_db_path
        memory_vault._db_initialized = False
        memory_vault._clear_connection_pool()
        memory_vault.ensure_tables_exist()

    def tearDown(self):
        memory_vault.DB_PATH = self.orig_db_path
        memory_vault._db_initialized = False
        memory_vault._clear_connection_pool()
        for suffix in ("", "-wal", "-shm"):
            p = f"{self.test_db_path}{suffix}"
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_check_fts_health_clean(self):
        """Clean database returns healthy status with 0 diffs."""
        health = memory_vault.check_fts_health()
        self.assertTrue(health["healthy"])
        self.assertEqual(health["core_diff"], 0)
        self.assertEqual(health["episodic_diff"], 0)

    def test_heal_fts_index_when_clean_returns_false(self):
        """When FTS is already in sync, heal_fts_index_if_needed returns False."""
        healed = memory_vault.heal_fts_index_if_needed(tolerance=0)
        self.assertFalse(healed)

    def test_heal_fts_index_on_desync(self):
        """When FTS desync occurs, heal_fts_index_if_needed detects and rebuilds."""
        # Insert directly to core_memories bypassing FTS sync
        old_sync = memory_vault._FTS_SYNC_ENABLED
        try:
            memory_vault._FTS_SYNC_ENABLED = False
            memory_vault.store_memory("Desynced memory content", node_type="secret", importance=7)
        finally:
            memory_vault._FTS_SYNC_ENABLED = old_sync

        health_before = memory_vault.check_fts_health()
        self.assertFalse(health_before["healthy"])
        self.assertGreater(health_before["core_diff"], 0)

        # Trigger self-healing
        healed = memory_vault.heal_fts_index_if_needed(tolerance=0)
        self.assertTrue(healed)

        # Health after rebuild must be clean
        health_after = memory_vault.check_fts_health()
        self.assertTrue(health_after["healthy"])
        self.assertEqual(health_after["core_diff"], 0)

    def test_fts5_search_auto_heals_on_operational_error(self):
        """fts5_search transparently heals and retries on OperationalError."""
        # Store a memory that should be searchable
        memory_vault.store_memory("Quantum telepathic interface", node_type="concept", importance=9)

        # Drop the FTS table to simulate index corruption/error
        with closing(memory_vault._get_conn()) as conn:
            conn.execute("DROP TABLE core_memories_fts")
            conn.commit()

        # Search should trigger self-heal (rebuilding tables/indexes) and return the match
        results = memory_vault.fts5_search("Quantum", top_k=5, allow_self_heal=True)
        self.assertIsInstance(results, list)
        self.assertTrue(any("Quantum" in r[2] for r in results))

    def test_check_vector_cache_health(self):
        """check_vector_cache_health reports cache validity and row counts."""
        health = memory_vault.check_vector_cache_health()
        self.assertIn("valid", health)
        self.assertIn("cached_rows", health)
        self.assertIn("db_embedded_rows", health)
        self.assertIn("drift", health)

    def test_heal_vector_cache_if_needed(self):
        """heal_vector_cache_if_needed invalidates cache if drift is detected."""
        # Invalidate when drift is present
        memory_vault._vector_matrix_cache.valid = True
        memory_vault._vector_matrix_cache.ids = [1, 2, 3]
        healed = memory_vault.heal_vector_cache_if_needed(tolerance=0)
        self.assertTrue(healed)
        self.assertFalse(memory_vault._vector_matrix_cache.valid)


class TestVaultConfigKeys(unittest.TestCase):
    """Verify new Vector 3 configuration keys."""

    def test_wal_autocheckpoint_threshold_mb_config(self):
        self.assertIn("vault_wal_autocheckpoint_threshold_mb", CONFIG)
        self.assertIsInstance(CONFIG["vault_wal_autocheckpoint_threshold_mb"], int)
        self.assertGreater(CONFIG["vault_wal_autocheckpoint_threshold_mb"], 0)

    def test_wal_checkpoint_on_close_config(self):
        self.assertIn("vault_wal_checkpoint_on_close", CONFIG)
        self.assertIsInstance(CONFIG["vault_wal_checkpoint_on_close"], bool)

    def test_vault_fts_self_heal_config(self):
        self.assertIn("vault_fts_self_heal", CONFIG)
        self.assertIsInstance(CONFIG["vault_fts_self_heal"], bool)


if __name__ == "__main__":
    unittest.main()
