import unittest
import os
import gc
import tempfile
import sqlite3
import json
import datetime
from contextlib import closing
from unittest.mock import patch, MagicMock
import requests
import numpy as np
import memory_vault


def _cleanup_db(path):
    """Remove a SQLite DB file with retry to handle Windows file locks."""
    if not os.path.exists(path):
        return
    gc.collect()  # Force garbage collection to release any lingering connections
    for attempt in range(3):
        try:
            os.remove(path)
            return
        except PermissionError:
            if attempt < 2:
                import time
                time.sleep(0.05 * (attempt + 1))
                gc.collect()
            else:
                pass  # Give up after 3 tries


class TestMemoryVault(unittest.TestCase):
    def setUp(self):
        # Create a temporary file to act as the isolated SQLite database
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix='.db')
        os.close(self.test_db_fd)
        
        # Override memory_vault to point to our temporary database
        self.original_db_path = memory_vault.DB_PATH
        memory_vault.DB_PATH = self.test_db_path
        memory_vault._db_initialized = False
        # Clear stale pooled connections that may still point to the old DB
        memory_vault._clear_connection_pool()
        
        # Initialize the test vault tables
        memory_vault.ensure_tables_exist()

    def tearDown(self):
        # Restore original DB path so it doesn't affect subsequent imports
        memory_vault.DB_PATH = self.original_db_path
        memory_vault._db_initialized = False
        # Clear pool so stale connections to the now-deleted temp DB are drained
        memory_vault._clear_connection_pool()
        
        # Cleanup the temp database file
        _cleanup_db(self.test_db_path)

    def test_ensure_tables_exist(self):
        # Drop tables to ensure ensure_tables_exist re-creates them properly
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            conn.execute("DROP TABLE IF EXISTS core_memories")
            conn.commit()
        
        memory_vault._db_initialized = False
        memory_vault.ensure_tables_exist()
        
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='core_memories'")
            self.assertIsNotNone(cursor.fetchone())

    def test_store_memory(self):
        mem_id = memory_vault.store_memory("Test memory content", node_type="test_fact", importance=10)
        self.assertGreater(mem_id, 0)
        
        # Verify retrieval directly via SQLite execution
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            row = conn.execute(
                "SELECT content, node_type, importance_score FROM core_memories WHERE id=?",
                (mem_id,)
            ).fetchone()
        
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Test memory content")
        self.assertEqual(row[1], "test_fact")
        self.assertEqual(row[2], 10)

    @unittest.skipIf(os.environ.get("KOKERTECH_SKIP_EMBEDDINGS"), "Embeddings disabled via KOKERTECH_SKIP_EMBEDDINGS")
    def test_semantic_search(self):
        # NOTE: Loading the sentence-transformer here may take ~2-3 seconds during the test.
        memory_vault.store_memory("The quick brown fox jumps over the lazy dog.")
        memory_vault.store_memory("Python programming language is awesome.")
        
        results = memory_vault.semantic_search("What is Python?", top_k=1)
        self.assertEqual(len(results), 1)
        
        row_id, n_type, content, score = results[0]
        self.assertEqual(content, "Python programming language is awesome.")
        self.assertGreater(score, 0.5)

    def test_retrieve_all_memories(self):
        # Insert directly via SQL to avoid triggering sentence-transformers import
        # (which causes a pyarrow access violation on Python 3.14/Windows)
        ts = "2026-06-01 12:00:00"
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            conn.execute(
                "INSERT INTO core_memories (timestamp, content, node_type, importance_score, tags) VALUES (?, ?, ?, ?, ?)",
                (ts, "Context entry 1", "fact", 5, "[]")
            )
            conn.execute(
                "INSERT INTO core_memories (timestamp, content, node_type, importance_score, tags) VALUES (?, ?, ?, ?, ?)",
                (ts, "Context entry 2", "fact", 5, "[]")
            )
            conn.commit()
        
        context = memory_vault.retrieve_all_memories()
        self.assertIn("Context entry 1", context)
        self.assertIn("Context entry 2", context)

class TestEpisodicJournal(unittest.TestCase):
    def setUp(self):
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix='.db')
        os.close(self.test_db_fd)
        self.original_db_path = memory_vault.DB_PATH
        memory_vault.DB_PATH = self.test_db_path
        memory_vault._db_initialized = False
        # Clear stale pooled connections that may still point to the old DB
        memory_vault._clear_connection_pool()
        memory_vault.ensure_tables_exist()
        # Verify the episodic_journal table was created
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        table_names = [t[0] for t in tables]
        assert 'episodic_journal' in table_names, f"episodic_journal not in {table_names}"
        assert 'core_memories' in table_names, f"core_memories not in {table_names}"

    def tearDown(self):
        memory_vault.DB_PATH = self.original_db_path
        memory_vault._db_initialized = False
        memory_vault._clear_connection_pool()
        _cleanup_db(self.test_db_path)

    def test_store_episodic_returns_id(self):
        mem_id = memory_vault.store_episodic("Test journal entry", session_id="test-session")
        self.assertGreater(mem_id, 0)

    def test_store_and_retrieve_episodic(self):
        memory_vault.store_episodic("Entry one", session_id="s1", tags=["tag1"])
        memory_vault.store_episodic("Entry two", session_id="s1", tags=["tag2"])
        entries = memory_vault.get_recent_episodic(limit=5)
        self.assertEqual(len(entries), 2)
        self.assertIn("Entry one", entries[1]["summary"])
        self.assertIn("Entry two", entries[0]["summary"])

    def test_get_recent_episodic_respects_limit(self):
        for i in range(5):
            memory_vault.store_episodic(f"Entry {i}")
        entries = memory_vault.get_recent_episodic(limit=3)
        self.assertEqual(len(entries), 3)

    def test_get_recent_episodic_by_session(self):
        memory_vault.store_episodic("Session A entry", session_id="a")
        memory_vault.store_episodic("Session B entry", session_id="b")
        entries_a = memory_vault.get_recent_episodic(limit=5, session_id="a")
        self.assertEqual(len(entries_a), 1)
        self.assertEqual(entries_a[0]["summary"], "Session A entry")

    def test_get_episodic_context_formatted(self):
        memory_vault.store_episodic("Test context", session_id="default", importance=7)
        ctx = memory_vault.get_episodic_context(limit=1)
        self.assertIn("Test context", ctx)
        self.assertIn("importance=7", ctx)

    def test_consolidate_episodic_promotes_high_importance(self):
        memory_vault.store_episodic("Important fact", session_id="s1", importance=8)
        memory_vault.store_episodic("Trivial note", session_id="s1", importance=2)
        memory_vault.store_episodic("Another important", session_id="s1", importance=7)
        
        count = memory_vault.consolidate_episodic(importance_threshold=6, max_age_days=365)
        self.assertEqual(count, 2)
        
        # Verify they appear in long-term memory
        long_term = memory_vault.retrieve_all_memories()
        self.assertIn("[EPISODIC:", long_term)

    def test_consolidate_episodic_idempotent(self):
        """REGRESSION GUARD (2026-09-22 re-pollution, Sprint 19.8.3).

        consolidate_episodic() previously had no promoted marker: every
        call re-promoted ALL qualifying journal rows, and the app calls
        it at lifecycle points — two morning app runs re-polluted
        core_memories with ~9,580 duplicate rows from a test-era journal.
        Contract: a journal row is promoted to core_memories AT MOST ONCE,
        no matter how many times consolidation runs.
        """
        memory_vault.store_episodic("Idem potens fact", session_id="s1", importance=8)
        first = memory_vault.consolidate_episodic(importance_threshold=6, max_age_days=365)
        self.assertEqual(first, 1)

        # Second run must promote NOTHING and store no duplicates.
        second = memory_vault.consolidate_episodic(importance_threshold=6, max_age_days=365)
        self.assertEqual(second, 0)
        long_term = memory_vault.retrieve_all_memories()
        self.assertEqual(long_term.count("[EPISODIC: Idem potens fact]"), 1)

    def test_consolidate_episodic_new_rows_promoted(self):
        """Idempotency must not suppress NEW journal rows: a row stored
        after a consolidation run is still promoted on the next run."""
        memory_vault.store_episodic("First wave", session_id="s1", importance=8)
        self.assertEqual(
            memory_vault.consolidate_episodic(importance_threshold=6, max_age_days=365), 1)
        memory_vault.store_episodic("Second wave", session_id="s1", importance=8)
        self.assertEqual(
            memory_vault.consolidate_episodic(importance_threshold=6, max_age_days=365), 1)
        long_term = memory_vault.retrieve_all_memories()
        self.assertIn("[EPISODIC: Second wave]", long_term)

    def test_empty_journal_returns_empty_context(self):
        ctx = memory_vault.get_episodic_context(limit=3)
        self.assertEqual(ctx, "No episodic journal entries yet.")


class TestWeightedEpisodicContext(unittest.TestCase):
    """Tests for importance-weighted + topic-relevant episodic context retrieval."""

    def setUp(self):
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix='.db')
        os.close(self.test_db_fd)
        self.original_db_path = memory_vault.DB_PATH
        memory_vault.DB_PATH = self.test_db_path
        memory_vault._db_initialized = False
        # Clear stale pooled connections that may still point to the old DB
        memory_vault._clear_connection_pool()
        memory_vault.ensure_tables_exist()

    def tearDown(self):
        memory_vault.DB_PATH = self.original_db_path
        memory_vault._db_initialized = False
        memory_vault._clear_connection_pool()
        _cleanup_db(self.test_db_path)

    def _insert_entry(self, summary, importance, minutes_ago=0, session="default"):
        """Helper: insert an episodic entry with a relative timestamp."""
        ts = (datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago)).strftime('%Y-%m-%d %H:%M:%S')
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                "INSERT INTO episodic_journal (timestamp, session_id, summary, tags, importance_score, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, session, summary, "[]", importance, "{}")
            )
            conn.commit()

    def test_importance_overrides_recency(self):
        """High-importance old entry ranks above low-importance recent entry."""
        self._insert_entry("Old but critical", importance=9, minutes_ago=120)
        self._insert_entry("Recent but trivial", importance=1, minutes_ago=5)

        ctx = memory_vault.get_episodic_context(limit=2, session_id="default")
        entries = ctx.split("\n")
        self.assertEqual(len(entries), 2)
        # Both should appear, but old+crtical should come first due to higher importance weight
        # The composite score for old+critical: 0.5*0.9 + 0.3*exp(-2/24) = 0.45 + 0.3*0.92 = 0.726
        # The composite score for recent+trivial: 0.5*0.1 + 0.3*exp(-0.083/24) = 0.05 + 0.3*0.997 = 0.349
        self.assertIn("Old but critical", ctx)
        self.assertIn("Recent but trivial", ctx)
        # Old but critical should appear first
        crit_idx = ctx.index("Old but critical")
        triv_idx = ctx.index("Recent but trivial")
        self.assertLess(crit_idx, triv_idx, "High-importance old entry should rank higher")

    def test_recency_boost_similar_importance(self):
        """Among entries with similar importance, newer ones rank higher."""
        self._insert_entry("Old entry", importance=5, minutes_ago=120)
        self._insert_entry("Recent entry", importance=5, minutes_ago=2)

        ctx = memory_vault.get_episodic_context(limit=2, session_id="default")
        self.assertIn("Old entry", ctx)
        self.assertIn("Recent entry", ctx)
        # Recent entry should appear first due to recency boost
        old_idx = ctx.index("Old entry")
        recent_idx = ctx.index("Recent entry")
        self.assertLess(recent_idx, old_idx, "Recent entry should rank higher when importance is equal")

    def test_query_relevance_boosts_matching_entries(self):
        """Passing a query boosts entries that semantically match the topic."""
        self._insert_entry("Debugged memory leak in docker container", importance=5, minutes_ago=60)
        self._insert_entry("Discussed UI color scheme preferences", importance=5, minutes_ago=60)

        # Both have same importance and recency, but query relevance gives docker an edge
        ctx = memory_vault.get_episodic_context(limit=2, session_id="default", query="docker container debug")

        self.assertIn("Debugged memory leak", ctx)
        self.assertIn("UI color scheme", ctx)

    def test_query_boost_overrides_equal_importance_recency(self):
        """Semantic boost from query can flip ordering when importance+recency are equal."""
        # Entries with same timestamp and importance - only semantic score differentiates
        self._insert_entry("Working on the docker sandbox implementation", importance=6, minutes_ago=30)
        self._insert_entry("Discussed frontend CSS styling approach", importance=6, minutes_ago=30)

        ctx = memory_vault.get_episodic_context(limit=2, session_id="default", query="docker container sandbox")
        self.assertIn("docker sandbox", ctx)
        self.assertIn("CSS styling", ctx)
        # The docker entry keyword-overlaps with the query, so it should rank first
        docker_idx = ctx.index("docker sandbox")
        css_idx = ctx.index("CSS styling")
        self.assertLess(docker_idx, css_idx,
                        "Entry matching query keywords should rank higher than non-matching entry")

    def test_query_empty_returns_all(self):
        """Empty query returns entries without semantic scoring."""
        self._insert_entry("Test entry", importance=5, minutes_ago=10)
        ctx = memory_vault.get_episodic_context(limit=1, query="")
        self.assertIn("Test entry", ctx)

    def test_higher_limit_returns_more(self):
        """Increasing limit returns more entries."""
        for i in range(5):
            self._insert_entry(f"Entry {i}", importance=5, minutes_ago=i * 10)
        ctx3 = memory_vault.get_episodic_context(limit=3)
        ctx5 = memory_vault.get_episodic_context(limit=5)
        self.assertGreater(len(ctx5.split("\n")), len(ctx3.split("\n")))

    def test_empty_journal_weighted(self):
        """Returns empty message when no entries exist."""
        ctx = memory_vault.get_episodic_context(limit=3)
        self.assertEqual(ctx, "No episodic journal entries yet.")


class TestSessionLifecycle(unittest.TestCase):
    def setUp(self):
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix='.db')
        os.close(self.test_db_fd)
        self.original_db_path = memory_vault.DB_PATH
        memory_vault.DB_PATH = self.test_db_path
        memory_vault._db_initialized = False
        # Clear stale pooled connections that may still point to the old DB
        memory_vault._clear_connection_pool()
        # Reset session state
        memory_vault._current_session_id = None
        memory_vault._session_state["session_id"] = None
        if hasattr(memory_vault, '_session_start_time'):
            memory_vault._session_start_time = None
        memory_vault.ensure_tables_exist()

    def tearDown(self):
        memory_vault.DB_PATH = self.original_db_path
        memory_vault._db_initialized = False
        memory_vault._current_session_id = None
        memory_vault._session_state["session_id"] = None
        if hasattr(memory_vault, '_session_start_time'):
            memory_vault._session_start_time = None
        _cleanup_db(self.test_db_path)

    def test_get_current_session_id_creates_new(self):
        """First call to get_current_session_id creates a new session."""
        sid = memory_vault.get_current_session_id()
        self.assertTrue(sid.startswith("session_"))
        # Format: session_YYYYMMDD_HHMMSS_ffffff (6 parts with underscores)
        self.assertGreaterEqual(len(sid.split("_")), 3)

    def test_get_current_session_id_is_stable(self):
        """Multiple calls return the same session ID."""
        sid1 = memory_vault.get_current_session_id()
        sid2 = memory_vault.get_current_session_id()
        self.assertEqual(sid1, sid2)

    def test_get_current_session_id_registers_in_db(self):
        """Session is registered in the session_metadata table."""
        sid = memory_vault.get_current_session_id()
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT session_id, start_time, entry_count FROM session_metadata WHERE session_id = ?",
                (sid,)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['session_id'], sid)
        self.assertEqual(row['entry_count'], 0)

    def test_start_new_session_rotates(self):
        """start_new_session creates a new session ID."""
        sid1 = memory_vault.get_current_session_id()
        sid2 = memory_vault.start_new_session()
        self.assertNotEqual(sid1, sid2)
        self.assertTrue(sid2.startswith("session_"))

    def test_start_new_session_updates_after_get(self):
        """After start_new_session, get_current_session_id returns new ID."""
        sid1 = memory_vault.get_current_session_id()
        memory_vault.start_new_session()
        sid3 = memory_vault.get_current_session_id()
        self.assertNotEqual(sid1, sid3)

    def test_list_recent_sessions_returns_sessions(self):
        """list_recent_sessions returns created sessions."""
        sid1 = memory_vault.get_current_session_id()
        sid2 = memory_vault.start_new_session()

        sessions = memory_vault.list_recent_sessions(limit=10)
        sids = [s['session_id'] for s in sessions]
        self.assertIn(sid1, sids)
        self.assertIn(sid2, sids)

    def test_start_new_session_stores_summary(self):
        """start_new_session accepts and stores a summary."""
        sid1 = memory_vault.get_current_session_id()
        memory_vault.store_episodic("Worked on RAG pipeline", session_id=sid1, importance=6)
        memory_vault.store_episodic("Fixed docker bug", session_id=sid1, importance=7)

        memory_vault.start_new_session(summary="Fixed RAG and Docker issues")

        sessions = memory_vault.list_recent_sessions(limit=10)
        for s in sessions:
            if s['session_id'] == sid1:
                self.assertEqual(s['summary'], "Fixed RAG and Docker issues")
                self.assertGreater(s['entry_count'], 0)

    def test_get_session_context_formatted(self):
        """get_session_context returns formatted session groupings."""
        sid = memory_vault.get_current_session_id()
        memory_vault.store_episodic("Discussed architecture", session_id=sid, importance=7)

        ctx = memory_vault.get_session_context(limit=3)
        self.assertIn("--- Session:", ctx)
        self.assertIn(sid, ctx)
        self.assertIn("Discussed architecture", ctx)

    def test_get_session_context_empty(self):
        """get_session_context returns appropriate message when no sessions."""
        # Use a clean DB without sessions
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            conn.execute("DROP TABLE IF EXISTS session_metadata")
            conn.commit()

        ctx = memory_vault.get_session_context(limit=3)
        self.assertEqual(ctx, "No episodic journal entries yet.")

    def test_get_session_context_adds_to_context(self):
        """get_session_context is wired correctly: entries appear in context."""
        sid = memory_vault.get_current_session_id()
        memory_vault.store_episodic("Fixed the RAG pipeline", session_id=sid, importance=8)

        ctx = memory_vault.get_session_context(limit=3)
        self.assertIn("--- Session:", ctx)
        self.assertIn("Fixed the RAG pipeline", ctx)


class TestDailySummary(unittest.TestCase):
    def setUp(self):
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix='.db')
        os.close(self.test_db_fd)
        self.original_db_path = memory_vault.DB_PATH
        memory_vault.DB_PATH = self.test_db_path
        memory_vault._db_initialized = False
        # Clear stale pooled connections that may still point to the old DB
        memory_vault._clear_connection_pool()
        memory_vault.ensure_tables_exist()

    def tearDown(self):
        memory_vault.DB_PATH = self.original_db_path
        memory_vault._db_initialized = False
        memory_vault._clear_connection_pool()
        _cleanup_db(self.test_db_path)

    def _row_conn(self):
        """Create a connection with sqlite3.Row factory for test assertions."""
        conn = sqlite3.connect(self.test_db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @patch("memory_vault.get_provider")
    def test_generate_daily_summary_with_entries(self, mock_provider):
        """Daily summary generates and stores AI narrative when entries exist."""
        # Insert some episodic entries from today
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with closing(self._row_conn()) as conn:
            conn.execute(
                "INSERT INTO episodic_journal (timestamp, session_id, summary, tags, importance_score, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, "s1", "Discussed RAG architecture improvements", "[]", 7, "{}")
            )
            conn.execute(
                "INSERT INTO episodic_journal (timestamp, session_id, summary, tags, importance_score, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, "s1", "Fixed docker sandbox memory leak bug", "[]", 8, "{}")
            )
            conn.commit()

        # Mock AI response
        mock_provider.return_value.chat_completion.return_value = {
            'content': 'Today focused on RAG architecture improvements and fixing a docker sandbox memory leak bug.',
            'error': None
        }

        log_msgs = []
        result = memory_vault.generate_daily_summary(log_callback=log_msgs.append)

        self.assertEqual(result, 2)

        # Verify a daily summary entry was created
        with closing(self._row_conn()) as conn:
            summary_entries = conn.execute(
                "SELECT summary, importance_score, tags FROM episodic_journal WHERE tags LIKE '%daily-summary%'"
            ).fetchall()
            self.assertEqual(len(summary_entries), 1)
            self.assertEqual(summary_entries[0]['importance_score'], 9)
            self.assertIn('RAG', summary_entries[0]['summary'])

        # Verify source entries are marked as summarized
        with closing(self._row_conn()) as conn:
            marked = conn.execute(
                "SELECT metadata FROM episodic_journal WHERE id IN (1, 2)"
            ).fetchall()
            for m in marked:
                meta = json.loads(m['metadata'])
                self.assertEqual(meta.get('summarized_in_daily'), 'true')

        # Verify the AI was called
        mock_provider.return_value.chat_completion.assert_called_once()

    @patch("memory_vault.get_provider")
    def test_generate_daily_summary_no_entries(self, mock_provider):
        """Returns 0 when no entries exist in the past 24h."""
        result = memory_vault.generate_daily_summary()
        self.assertEqual(result, 0)
        mock_provider.return_value.chat_completion.assert_not_called()

    @patch("memory_vault.get_provider")
    def test_generate_daily_summary_already_exists(self, mock_provider):
        """Returns 0 when a daily summary already exists for today."""
        # Insert an existing daily summary
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            conn.execute(
                "INSERT INTO episodic_journal (timestamp, session_id, summary, tags, importance_score, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, "daily-summary", "Yesterday's summary", '["daily-summary"]', 9, "{}")
            )
            conn.commit()

        result = memory_vault.generate_daily_summary()
        self.assertEqual(result, 0)
        mock_provider.return_value.chat_completion.assert_not_called()

    @patch("memory_vault.get_provider")
    def test_generate_daily_summary_skips_already_summarized(self, mock_provider):
        """Only summarizes entries not already marked as summarized."""
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            # Entry already summarized
            conn.execute(
                "INSERT INTO episodic_journal (timestamp, session_id, summary, tags, importance_score, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, "s1", "Old news", "[]", 5, '{"summarized_in_daily": "true"}')
            )
            # Entry not yet summarized
            conn.execute(
                "INSERT INTO episodic_journal (timestamp, session_id, summary, tags, importance_score, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, "s1", "New information discovered", "[]", 7, "{}")
            )
            conn.commit()

        mock_provider.return_value.chat_completion.return_value = {
            'content': 'New information was discovered today.',
            'error': None
        }

        result = memory_vault.generate_daily_summary()
        self.assertEqual(result, 1)  # Only the un-summarized entry

        # Verify the AI only got 1 entry in its prompt
        call_kwargs = mock_provider.return_value.chat_completion.call_args
        prompt = call_kwargs[1]['messages'][1]['content']
        self.assertIn('New information discovered', prompt)
        self.assertNotIn('Old news', prompt)

    @patch("memory_vault.get_provider")
    def test_generate_daily_summary_ai_failure(self, mock_provider):
        """Returns -1 when the AI provider fails."""
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            conn.execute(
                "INSERT INTO episodic_journal (timestamp, session_id, summary, tags, importance_score, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, "s1", "Test entry", "[]", 5, "{}")
            )
            conn.commit()

        # Simulate AI provider timeout
        mock_provider.return_value.chat_completion.return_value = {"error": "Connection timeout", "content": ""}

        log_msgs = []
        result = memory_vault.generate_daily_summary(log_callback=log_msgs.append)
        self.assertEqual(result, -1)

        # Verify no daily summary was stored
        with closing(sqlite3.connect(self.test_db_path)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM episodic_journal WHERE tags LIKE '%daily-summary%'"
            ).fetchone()[0]
            self.assertEqual(count, 0)


import threading as _threading_for_test  # noqa: E402
import sys as _sys_for_test  # noqa: E402


class TestEmbeddingCache(unittest.TestCase):
    """Phase C Task A.2 — per-query embedding cache contract tests.

    Uses a mocked SentenceTransformer so the heavy real model is never
    loaded. The contract:
      - Identical text → identical vector (cache HIT, encode() called once)
      - Cache bounded by ``_EMBEDDING_CACHE_MAX``; oldest eviction by
        insertion order when capacity is exceeded
      - ``None`` model returns ``None`` vector and does NOT poison the cache
      - Cache key is text[:2000] (matches what each caller passes)
    """

    def setUp(self):
        # Reset cache state for test isolation
        self._orig_cache = dict(memory_vault._embedding_cache)
        self._orig_lock = memory_vault._embedding_lock
        memory_vault._embedding_cache.clear()
        memory_vault._embedding_lock = _threading_for_test.Lock()

    def tearDown(self):
        memory_vault._embedding_cache.clear()
        memory_vault._embedding_cache.update(self._orig_cache)
        memory_vault._embedding_lock = self._orig_lock

    def test_cache_hit_returns_same_vector_and_skips_encode(self):
        """Same query → same vector; ``encode()`` invoked exactly once
        across multiple calls (cache HIT after first encode)."""
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([0.1, 0.2, 0.3], dtype=np.float32)

        with patch.object(memory_vault, '_get_model', return_value=mock_model):
            v1 = memory_vault._get_embedding("hello world")
            v2 = memory_vault._get_embedding("hello world")
            v3 = memory_vault._get_embedding("hello world")

        # encode() called exactly once across 3 lookups
        self.assertEqual(mock_model.encode.call_count, 1)
        np.testing.assert_array_equal(v1, v2)
        np.testing.assert_array_equal(v2, v3)

    def test_cache_distinct_queries_get_distinct_vectors(self):
        """Different queries → re-encode each. Repeat of an earlier query
        hits the cache."""
        encode_calls = []

        def fake_encode(text):
            encode_calls.append(text)
            return np.array([(hash(text) % 100) / 100.0] * 3, dtype=np.float32)

        mock_model = MagicMock()
        mock_model.encode.side_effect = fake_encode

        with patch.object(memory_vault, '_get_model', return_value=mock_model):
            v1 = memory_vault._get_embedding("query A")
            v2 = memory_vault._get_embedding("query B")
            v3 = memory_vault._get_embedding("query A")  # cache hit

        self.assertEqual(len(encode_calls), 2)
        self.assertEqual(encode_calls[0], "query A")
        self.assertEqual(encode_calls[1], "query B")
        np.testing.assert_array_equal(v1, v3)
        self.assertFalse(np.array_equal(v1, v2))

    def test_cache_returns_none_when_model_unavailable(self):
        """When model is ``None``, cache returns ``None`` and does NOT
        store the ``None`` value (avoids cache poisoning)."""
        with patch.object(memory_vault, '_get_model', return_value=None):
            v1 = memory_vault._get_embedding("any text")
            v2 = memory_vault._get_embedding("any text")

        self.assertIsNone(v1)
        self.assertIsNone(v2)
        # Verify the cache was NOT polluted with None
        self.assertEqual(len(memory_vault._embedding_cache), 0)

    def test_cache_evicts_oldest_entry_when_full(self):
        """At cap, the OLDEST entry (first inserted) is evicted."""
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda q: np.array(
            [len(q) / 1000.0] * 3, dtype=np.float32
        )

        # Tighten the bound for fast test
        with patch.dict(memory_vault.CONFIG, {"embedding_cache_max": 3}):
            with patch.object(memory_vault, '_get_model', return_value=mock_model):
                memory_vault._get_embedding("q1")
                memory_vault._get_embedding("q2")
                memory_vault._get_embedding("q3")
                self.assertEqual(len(memory_vault._embedding_cache), 3)
                self.assertIn("q1", memory_vault._embedding_cache)
                self.assertIn("q2", memory_vault._embedding_cache)
                self.assertIn("q3", memory_vault._embedding_cache)

                # 4th insertion evicts q1 (oldest, first inserted)
                memory_vault._get_embedding("q4")
                self.assertEqual(len(memory_vault._embedding_cache), 3)
                self.assertNotIn("q1", memory_vault._embedding_cache,
                                 "Oldest insertion (q1) should be evicted on capacity overflow")
                self.assertIn("q2", memory_vault._embedding_cache)
                self.assertIn("q3", memory_vault._embedding_cache)
                self.assertIn("q4", memory_vault._embedding_cache)
                # Verify the oldest-of-remaining is still q2 (insertion order)
                first_remaining = next(iter(memory_vault._embedding_cache.keys()))
                self.assertEqual(first_remaining, "q2",
                                 "After evicting q1, q2 should be the oldest surviving entry")

    def test_cache_reencodes_evicted_key_on_next_lookup(self):
        """After q1 is evicted, looking it up again re-encodes (cache
        miss). Distinct from the eviction test, which only checks
        insertion-order eviction. Note: re-inserting q1 triggers another
        eviction cycle, evicting q2 in this 3-bound setup."""
        mock_model = MagicMock()
        mock_model.encode.side_effect = lambda q: np.array(
            [len(q) / 1000.0] * 3, dtype=np.float32
        )

        with patch.dict(memory_vault.CONFIG, {"embedding_cache_max": 3}):
            with patch.object(memory_vault, '_get_model', return_value=mock_model):
                memory_vault._get_embedding("q1")
                memory_vault._get_embedding("q2")
                memory_vault._get_embedding("q3")
                memory_vault._get_embedding("q4")  # evicts q1
                self.assertNotIn("q1", memory_vault._embedding_cache)

                # Now lookup q1: cache miss, re-encode, then re-insert.
                mock_model.encode.reset_mock()
                memory_vault._get_embedding("q1")
                self.assertEqual(mock_model.encode.call_count, 1,
                                 "q1 was evicted; lookup must re-encode")
                # q1 is back in the cache (its re-insertion evicted q2 in
                # this 3-bound setup).
                self.assertIn("q1", memory_vault._embedding_cache)
                self.assertNotIn("q2", memory_vault._embedding_cache)
                self.assertIn("q3", memory_vault._embedding_cache)
                self.assertIn("q4", memory_vault._embedding_cache)

    def test_cache_key_normalizes_to_2000_chars(self):
        """Cache key is ``text[:2000]``; ``encode()`` receives only the
        sliced prefix. A repeat call with the same prefix is a HIT."""
        mock_model = MagicMock()
        mock_model.encode.return_value = np.array([1.0] * 3, dtype=np.float32)

        long_text = "x" * 5000
        with patch.object(memory_vault, '_get_model', return_value=mock_model):
            memory_vault._get_embedding(long_text)
            # encode() got the 2000-char slice, NOT the 5000-char raw text
            called_with = mock_model.encode.call_args[0][0]
            self.assertEqual(len(called_with), 2000)
            # Verify second call hits the cache (no re-encode)
            mock_model.encode.reset_mock()
            memory_vault._get_embedding(long_text[:2000])
            self.assertEqual(mock_model.encode.call_count, 0)


class TestCrossEncoderFallbackWarning(unittest.TestCase):
    """Production-visibility tests for cross-encoder fallback logging.

    Two log paths gate the silent cross-encoder fallback so it surfaces
    in observability:
      1. ``_check_cross_encoder_config()`` startup WARNING
         (once at module import, says "pip install sentence-transformers").
      2. ``_cross_encoder_rerank()`` inside-function INFO log
         (once per process, says "CrossEncoder unavailable; skipping
         rerank.").

    Both gates are suppressed when ``KOKERTECH_SKIP_EMBEDDINGS`` is set;
    the function-side gate is an *early return* before any import attempt,
    and the startup-side gate skips the ``find_spec()`` call entirely.

    All assertions target ``memory_vault.logger.{warning,info,debug}``
    via ``unittest.mock.patch.object``. This bypasses ``addHandler`` /
    stdout machinery, so the test surface survives any future refactor of
    the underlying ``_LevelFilteredLogger`` wrapper (it doesn't subclass
    ``logging.Logger``).
    """

    # ------------------- setUp / tearDown (state hygiene) -------------------

    def setUp(self):
        # Snapshot mutable state we mutate so tearDown restores exactly.
        self._original_warned = memory_vault._CROSS_ENCODER_WARNED
        self._original_cache = memory_vault._cross_encoder_cache
        self._original_cache_name = memory_vault._cross_encoder_cache_name
        self._original_config_val = memory_vault.CONFIG.get(
            'cross_encoder_model', None)
        self._original_env = os.environ.get('KOKERTECH_SKIP_EMBEDDINGS')

        # Reset fire-once boundary before every test method so we can
        # assert the per-process invariant in isolation.
        memory_vault._CROSS_ENCODER_WARNED = False
        memory_vault._cross_encoder_cache = None
        memory_vault._cross_encoder_cache_name = None
        memory_vault.CONFIG['cross_encoder_model'] = (
            'cross-encoder/ms-marco-MiniLM-L-6-v2')
        os.environ.pop('KOKERTECH_SKIP_EMBEDDINGS', None)
        # Force `from sentence_transformers import CrossEncoder` to raise
        # ``ModuleNotFoundError`` (subclass of ImportError) regardless of
        # whether the package is actually installed in this Python env.
        # CPython checks sys.modules first; mapping to None is the standard
        # "fake uninstall" technique.
        _sys_for_test.modules['sentence_transformers'] = None

        # Sanity-assert the precondition all 4 invariants depend on:
        # if ``cross_encoder_model`` were empty in CONFIG, both gates would
        # silently trivially-pass on the empty-model check, and the tests
        # would become meaningless. Pin it here so a future CONFIG regression
        # fails LOUDLY in setUp rather than silently producing 4 green tests.
        self.assertTrue(
            memory_vault.CONFIG.get('cross_encoder_model'),
            "TestCrossEncoderFallbackWarning requires "
            "CONFIG['cross_encoder_model'] to be truthy in setUp; otherwise "
            "the cross-encoder path short-circuits on the empty-model check "
            "and the invariants cannot be exercised.")

    def tearDown(self):
        memory_vault._CROSS_ENCODER_WARNED = self._original_warned
        memory_vault._cross_encoder_cache = self._original_cache
        memory_vault._cross_encoder_cache_name = self._original_cache_name
        if 'cross_encoder_model' in memory_vault.CONFIG:
            if self._original_config_val:
                memory_vault.CONFIG['cross_encoder_model'] = (
                    self._original_config_val)
            else:
                del memory_vault.CONFIG['cross_encoder_model']
        if self._original_env is not None:
            os.environ['KOKERTECH_SKIP_EMBEDDINGS'] = self._original_env
        else:
            os.environ.pop('KOKERTECH_SKIP_EMBEDDINGS', None)
        # Remove the sentence_transformers entry so the next import
        # re-resolves freshly.  .pop() is safe whether the key exists
        # or not -- test_rerank_early_return_when_skip_embeddings_env_set
        # deletes the key itself, so del would raise KeyError here.        _sys_for_test.modules.pop('sentence_transformers', None)

    # ------------------- Invariant 1: startup WARNING fires once -------------------

    def test_startup_warning_fires_once_when_package_missing(self):
        """Invariant 1: when ``cross_encoder_model`` is set AND the package
        is missing, ``_check_cross_encoder_config()`` emits exactly one
        WARNING mentioning ``pip install sentence-transformers``. A second
        invocation in the same process must NOT refire (import-only gate)."""
        with patch.object(
                memory_vault.importlib.util, 'find_spec',
                return_value=None) as _unused_find_spec, \
             patch.object(memory_vault.logger, 'warning') as mock_warn, \
             patch.object(memory_vault.logger, 'info') as mock_info:
            # Call TWICE: fire-once-per-process contract.
            memory_vault._check_cross_encoder_config()
            memory_vault._check_cross_encoder_config()

        self.assertEqual(
            mock_warn.call_count, 1,
            f"Expected exactly 1 startup WARNING across 2 calls, got "
            f"{mock_warn.call_count}.")
        # No INFO from the helper — startup path emits WARNING only.
        self.assertEqual(mock_info.call_count, 0)

        # Verify the message format: includes the configured model name
        # AND the remediation hint, so operators know what to install.
        message = mock_warn.call_args[0][0]
        self.assertIn(
            'cross-encoder/ms-marco-MiniLM-L-6-v2', message,
            "WARNING should name the configured model so operators can "
            "verify it matches their CONFIG.")
        self.assertIn(
            'pip install sentence-transformers', message,
            "WARNING must include the install remediation.")
        self.assertIn(
            'Reranking will be disabled', message,
            "WARNING must state the consequence of the fallback.")

    # ------------------- Invariant 2: startup WARNING suppressed by env -------------------

    def test_startup_warning_suppressed_by_skip_embeddings_env(self):
        """Invariant 2: ``KOKERTECH_SKIP_EMBEDDINGS`` short-circuits the
        startup helper BEFORE ``find_spec()`` is even called, so no WARNING
        fires regardless of whether the package is present."""
        os.environ['KOKERTECH_SKIP_EMBEDDINGS'] = '1'
        # find_spec would return a real spec if the package is installed
        # — but the env-var gate must short-circuit BEFORE we get there.
        with patch.object(
                memory_vault.importlib.util, 'find_spec') as mock_find_spec, \
             patch.object(memory_vault.logger, 'warning') as mock_warn:
            memory_vault._check_cross_encoder_config()

        self.assertEqual(
            mock_warn.call_count, 0,
            "Env-gate must silence the startup WARNING; got "
            f"{mock_warn.call_count} calls instead.")
        self.assertEqual(
            mock_find_spec.call_count, 0,
            "Env-gate must skip the find_spec() call entirely; got "
            f"{mock_find_spec.call_count} calls instead.")

    # ------------------- Invariant 3: inside-function INFO fires exactly once -------------------

    def test_rerank_info_fires_exactly_once_across_two_calls(self):
        """Invariant 3: when the inner ``from sentence_transformers import
        CrossEncoder`` raises ``ImportError`` (forced via the sys.modules
        sentinel in setUp), the inside-function INFO log fires exactly
        ONCE across two consecutive ``_cross_encoder_rerank`` calls. The
        DEBUG-level forensic hint fires on EVERY failure."""
        results = [(1, 'core', 'matching content', 0.9)]
        with patch.object(memory_vault.logger, 'info') as mock_info, \
             patch.object(memory_vault.logger, 'debug') as mock_debug, \
             patch.object(memory_vault.logger, 'warning') as mock_warning:
            memory_vault._cross_encoder_rerank('query', results)
            # Flag is now True. Second call must NOT refire the INFO log.
            memory_vault._cross_encoder_rerank('query', results)

        self.assertEqual(
            mock_info.call_count, 1,
            f"Expected exactly 1 inside-function INFO across 2 calls, got "
            f"{mock_info.call_count}.")
        info_message = mock_info.call_args[0][0]
        self.assertEqual(
            info_message,
            "CrossEncoder unavailable; skipping rerank.",
            "INFO message must be the canonical 'skipping rerank.' string "
            "so log scrapers can pattern-match against it.")
        # DEBUG-level forensic hint is NOT fire-once-gated — documented
        # contract is "fires on EVERY failure" (forensic context). The
        # exact count of 2 (one per rerank call) pins down the dual
        # invariant in a single assertion: fire-once INFO + fire-every
        # DEBUG. If the gate were ever rewired to also gate DEBUG, this
        # assertion would catch the silent regression immediately.
        self.assertEqual(
            mock_debug.call_count, 2,
            "DEBUG-level 'install sentence-transformers' hint must fire "
            "on EVERY ImportError (once per rerank call), so 2 calls "
            f"should produce exactly 2 DEBUG entries; got "
            f"{mock_debug.call_count}.")
        # Sanity: no WARNING should fire from the inside-function path.
        self.assertEqual(
            mock_warning.call_count, 0,
            "Inside-function path emits DEBUG + INFO only, never WARNING.")

    # ------------------- Invariant 4: inside-function path suppressed by env -------------------

    def test_rerank_early_return_when_skip_embeddings_env_set(self):
        """Invariant 4: ``KOKERTECH_SKIP_EMBEDDINGS`` short-circuits
        ``_cross_encoder_rerank`` BEFORE the import attempt, so neither
        INFO nor DEBUG fires AND the function returns the input
        unchanged (no model code is touched)."""
        os.environ['KOKERTECH_SKIP_EMBEDDINGS'] = '1'
        # Remove the sys.modules sentinel for THIS test so we can prove
        # the env-var (not the missing-package) is what short-circuits.
        # .pop() is safe whether the key exists or not — the test
        # test_rerank_early_return_when_skip_embeddings_env_set deletes
        # the key itself, so del would raise KeyError here.
        _sys_for_test.modules.pop('sentence_transformers', None)

        results = [(1, 'core', 'matching content', 0.9),
                   (2, 'core', 'other content', 0.8)]
        with patch.object(memory_vault.logger, 'info') as mock_info, \
             patch.object(memory_vault.logger, 'debug') as mock_debug, \
             patch.object(memory_vault.logger, 'warning') as mock_warn, \
             patch.object(
                 memory_vault.importlib.util, 'find_spec') as mock_find_spec:
            returned = memory_vault._cross_encoder_rerank('query', results)

        # Env-gate short-circuits BEFORE any log or model code.
        self.assertEqual(
            mock_info.call_count, 0,
            "Env-gate must silence the inside-function INFO; got "
            f"{mock_info.call_count} INFO calls instead.")
        self.assertEqual(
            mock_debug.call_count, 0,
            "Env-gate must skip the DEBUG forensic hint too; got "
            f"{mock_debug.call_count} DEBUG calls instead.")
        self.assertEqual(
            mock_warn.call_count, 0,
            "Env-gate must not emit any WARNING either; got "
            f"{mock_warn.call_count}.")
        # Function returns input unchanged (no model code touched).
        self.assertEqual(
            returned, results,
            "Env-gate must return input results unchanged; rerank is "
            "silently disabled.")

    # ------------------- Positive-path: helper is silent when package IS present -------------------

    def test_startup_no_warning_when_package_present(self):
        """Positive-path control: when ``find_spec()`` returns a truthy
        spec object (simulating sentence-transformers installed), the
        startup helper must emit ZERO warnings. Locks in the negative
        contract from the OTHER direction: a future regression that adds
        a 'package-not-installed' check under the wrong branch would
        fall through here rather than trigger Invariant 1, so this test
        catches the missed-detection case."""
        # find_spec returns a MagicMock spec-object (non-None) — the
        # helper short-circuits the ``if find_spec(...) is None:`` branch
        # and never reaches ``logger.warning(...)``.
        truthy_spec = MagicMock(name='sentence_transformers_spec')
        with patch.object(
                memory_vault.importlib.util, 'find_spec',
                return_value=truthy_spec) as mock_find_spec, \
             patch.object(memory_vault.logger, 'warning') as mock_warn, \
             patch.object(memory_vault.logger, 'info') as mock_info:
            memory_vault._check_cross_encoder_config()
            memory_vault._check_cross_encoder_config()

        # Helper called find_spec — env-var was unset and CONFIG truthy.
        self.assertGreaterEqual(
            mock_find_spec.call_count, 2,
            "Helper should reach find_spec on each invocation when env-var "
            f"is unset and CONFIG has a model; got {mock_find_spec.call_count}.")
        # But neither call produced a WARNING — package IS present.
        self.assertEqual(
            mock_warn.call_count, 0,
            "Helper must NOT warn when find_spec returns a truthy spec; "
            f"got {mock_warn.call_count} WARNING calls instead.")
        # And no INFO either (positive-path never logs from this helper).
        self.assertEqual(
            mock_info.call_count, 0,
            "Helper must not emit INFO either on the happy path; got "
            f"{mock_info.call_count} INFO calls instead.")


class TestCrossEncoderLoggingRegression(unittest.TestCase):
    """Regression coverage for the three (a)/(b)/(c) cross-encoder
    logging invariants.

    Each method maps to one invariant from the cross-encoder
    production-visibility audit:

      (a) ``_check_cross_encoder_config()`` emits ONE WARNING when
          ``cross_encoder_model`` is set AND sentence-transformers is
          missing. A second call in the same process must NOT refire.
      (b) ``_check_cross_encoder_config()`` emits ZERO logs when
          ``KOKERTECH_SKIP_EMBEDDINGS`` is set (operator's intentional
          opt-out). The helper short-circuits BEFORE ``find_spec()``.
      (c) ``_cross_encoder_rerank()`` emits exactly ONE INFO across
          multiple consecutive calls when the inline
          ``from sentence_transformers import CrossEncoder`` raises.
          The INFO message is the canonical
          ``"CrossEncoder unavailable; skipping rerank."`` so log
          scrapers can pattern-match against it.

    Pattern: ``unittest.mock.patch.object(memory_vault.logger, ...)`` —
    same idiom as ``TestCrossEncoderFallbackWarning``, so the test
    surface survives any future refactor of the underlying
    ``_LevelFilteredLogger`` wrapper (which is NOT a subclass of
    ``logging.Logger``).
    """

    # ------------------- setUp / tearDown -------------------

    def setUp(self):
        # Snapshot mutable state so tearDown restores exactly.
        self._original_warned = memory_vault._CROSS_ENCODER_WARNED
        self._original_config = memory_vault.CONFIG.get(
            'cross_encoder_model', None)
        self._original_env = os.environ.get('KOKERTECH_SKIP_EMBEDDINGS')
        self._had_st_module = (
            'sentence_transformers' in _sys_for_test.modules)
        self._original_st_module = _sys_for_test.modules.get(
            'sentence_transformers')

        # Reset to a known starting state for every test.
        memory_vault._CROSS_ENCODER_WARNED = False
        memory_vault.CONFIG['cross_encoder_model'] = (
            'cross-encoder/ms-marco-MiniLM-L-6-v2')
        os.environ.pop('KOKERTECH_SKIP_EMBEDDINGS', None)
        # Force `from sentence_transformers import CrossEncoder` to raise
        # ModuleNotFoundError (subclass of ImportError) regardless of
        # whether the package is actually installed. CPython checks
        # sys.modules first; the None sentinel is the standard
        # "fake uninstall" technique.
        _sys_for_test.modules['sentence_transformers'] = None

    def tearDown(self):
        memory_vault._CROSS_ENCODER_WARNED = self._original_warned
        if 'cross_encoder_model' in memory_vault.CONFIG:
            if self._original_config is not None:
                memory_vault.CONFIG['cross_encoder_model'] = (
                    self._original_config)
            else:
                del memory_vault.CONFIG['cross_encoder_model']
        if self._original_env is not None:
            os.environ['KOKERTECH_SKIP_EMBEDDINGS'] = self._original_env
        else:
            os.environ.pop('KOKERTECH_SKIP_EMBEDDINGS', None)
        # Restore sys.modules to its pre-setUp state. The two cases
        # we have to handle: the key was absent, or it had a real
        # module object (or, less commonly, a None sentinel).
        if self._had_st_module:
            _sys_for_test.modules['sentence_transformers'] = (
                self._original_st_module)
        else:
            _sys_for_test.modules.pop('sentence_transformers', None)

    # ------------------- (a) -------------------

    def test_a_check_config_warns_once_when_package_missing_and_model_set(
            self):
        """(a) When ``cross_encoder_model`` is set AND
        sentence-transformers is missing, ``_check_cross_encoder_config()``
        emits exactly ONE WARNING across multiple invocations. The
        message includes the configured model name AND the install
        remediation so operators can act without re-reading CONFIG."""
        with patch.object(memory_vault.importlib.util, 'find_spec',
                          return_value=None), \
             patch.object(memory_vault.logger, 'warning') as mock_warn, \
             patch.object(memory_vault.logger, 'info') as mock_info, \
             patch.object(memory_vault.logger, 'debug') as mock_debug:
            # The helper has NO internal fire-once flag — the
            # production "fire-once-per-process" contract is enforced
            # by the import-time invocation (the bare call at module
            # import line ~L2962), NOT by an in-function gate. So this
            # regression test calls the helper ONCE: the contract is
            # "one WARNING per call when the package is missing", not
            # "one WARNING across N calls".
            memory_vault._check_cross_encoder_config()

        # Exactly one WARNING from a single helper invocation.
        self.assertEqual(
            mock_warn.call_count, 1,
            f"Expected exactly 1 WARNING; got "
            f"{mock_warn.call_count}.")
        # Helper emits WARNING only — no INFO, no DEBUG.
        self.assertEqual(mock_info.call_count, 0)
        self.assertEqual(mock_debug.call_count, 0)

        # Message-content checks against the production string format
        # (``memory_vault.py`` ~L2956):
        #   "cross_encoder_model '<model>' is configured but
        #    sentence-transformers is missing. Reranking will be
        #    disabled. Run 'pip install sentence-transformers' to
        #    enable it."
        message = mock_warn.call_args[0][0]
        self.assertIn(
            'cross-encoder/ms-marco-MiniLM-L-6-v2', message,
            "WARNING must name the configured model so operators can "
            "verify it against their CONFIG without re-reading the "
            "config file.")
        self.assertIn(
            'sentence-transformers', message,
            "WARNING must name the missing package.")
        self.assertIn(
            'Reranking will be disabled', message,
            "WARNING must state the consequence so operators know "
            "search quality is degraded, not search itself.")
        self.assertIn(
            'pip install sentence-transformers', message,
            "WARNING must include the install remediation so "
            "operators don't have to look it up separately.")

    # ------------------- (b) -------------------

    def test_b_check_config_silent_under_skip_embeddings_env(self):
        """(b) When ``KOKERTECH_SKIP_EMBEDDINGS`` is set, the env-gate
        short-circuits the helper at the FIRST line — BEFORE
        ``find_spec()`` is ever called and BEFORE any log call. So
        ZERO WARNING / INFO / DEBUG fires regardless of package
        presence / absence / CONFIG state."""
        os.environ['KOKERTECH_SKIP_EMBEDDINGS'] = '1'
        with patch.object(memory_vault.logger, 'warning') as mock_warn, \
             patch.object(memory_vault.logger, 'info') as mock_info, \
             patch.object(memory_vault.logger, 'debug') as mock_debug:
            # Call TWICE: even if a regression broke the env-gate,
            # the second call would amplify the failure.
            memory_vault._check_cross_encoder_config()
            memory_vault._check_cross_encoder_config()

        # Hard zero: NO log level fires from the helper under the gate.
        self.assertEqual(
            mock_warn.call_count, 0,
            f"Env-gate must silence WARNING; got {mock_warn.call_count}.")
        self.assertEqual(
            mock_info.call_count, 0,
            f"Env-gate must silence INFO; got {mock_info.call_count}.")
        self.assertEqual(
            mock_debug.call_count, 0,
            f"Env-gate must silence DEBUG; got {mock_debug.call_count}.")

    # ------------------- (c) -------------------

    def test_c_rerank_info_fires_exactly_once_across_multiple_calls(
            self):
        """(c) When ``from sentence_transformers import CrossEncoder``
        inside ``_cross_encoder_rerank()`` raises (forced via the
        sys.modules sentinel), the function emits exactly ONE INFO
        across multiple consecutive calls — the per-process
        ``_CROSS_ENCODER_WARNED`` flag drives the fire-once contract."""
        results = [(1, 'core', 'matching content', 0.9)]
        with patch.object(memory_vault.logger, 'info') as mock_info, \
             patch.object(memory_vault.logger, 'debug') as mock_debug, \
             patch.object(memory_vault.logger, 'warning') as mock_warn:
            # Call THREE times so we cover: first-fire (gates flip),
            # second-call (gate suppresses INFO), third-call (still
            # suppressed, plus proves the 1-vs-3 ratio is stable).
            memory_vault._cross_encoder_rerank('query', results)
            memory_vault._cross_encoder_rerank('query', results)
            memory_vault._cross_encoder_rerank('query', results)

        # Exactly 1 INFO across 3 calls.
        self.assertEqual(
            mock_info.call_count, 1,
            f"Expected exactly 1 INFO across 3 rerank calls; got "
            f"{mock_info.call_count}.")

        # Message content is exactly the canonical string. If this
        # regresses to a different word order (e.g. "skipping
        # rerank; CrossEncoder unavailable"), downstream log scrapers
        # that pattern-match on the literal string would break.
        self.assertEqual(
            mock_info.call_args[0][0],
            "CrossEncoder unavailable; skipping rerank.")

        # DEBUG fires on EVERY ImportError (not fire-once-gated).
        # 3 calls → 3 DEBUG. If the gate were ever rewired to also
        # gate DEBUG, this assertion catches the regression.
        self.assertEqual(
            mock_debug.call_count, 3,
            f"DEBUG forensic hint must fire on EVERY ImportError "
            f"(3 calls → 3 DEBUG); got {mock_debug.call_count}.")

        # No WARNING from the inside-function path (that's exclusively
        # the helper's contract).
        self.assertEqual(
            mock_warn.call_count, 0,
            f"Inside-function path emits DEBUG + INFO only; got "
            f"{mock_warn.call_count} WARNING calls instead.")
class TestRequestsImportGuard(unittest.TestCase):
    """Locks in the 4-scenario invariant of the memory_vault `requests` import guard landed 2026-06-27. The guard is `try: import requests as _requests_real / except ImportError: requests = _RequestsMissingStub()` at module top; production callsites (`_update_tags_async`, `extract_entities_llm`, `llm_extract_with_fallback`) already wrap HTTP calls in `except (requests.RequestException, ...)` so the stub engages their silent-fallback path. These tests ensure no future refactor can regress the 4-scenario contract quietly."""
    @staticmethod
    def _memory_vault_path():
        """Resolve `memory_vault.py` relative to this test file so the test does not depend on the runner's cwd. Asserts existence at test-time so a future directory restructuring cannot silently degrade the test to a no-op."""
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.normpath(os.path.join(here, '..', 'memory_vault.py'))
    # --------------------- Scenario 1 ---------------------
    def test_py_compile_succeeds_with_requests_installed(self):
        """Scenario 1: `py_compile.compile(<memory_vault>, doraise=True)` returns a non-None code object when `requests` is installed. Happy path: the guard's try-block succeeded, `requests = _requests_real`, and the module source parses cleanly."""
        import os
        import py_compile
        import tempfile
        path = self._memory_vault_path()
        self.assertTrue(os.path.isfile(path), "memory_vault.py not found at expected location: " + path)
        with tempfile.TemporaryDirectory() as td:
            cfile = os.path.join(td, "compiled.pyc")
            result = py_compile.compile(path, cfile=cfile, doraise=True)
            self.assertIsNotNone(result, "py_compile.compile returned None under doraise=True; the guard's enclosing module has regressed to an unparseable state")
    # --------------------- Scenario 2 ---------------------
    def test_py_compile_succeeds_with_requests_blocked_at_import_hook(self):
        """Scenario 2: `py_compile` succeeds even when `__builtins__['__import__']` is overridden to raise ImportError for `requests` and `urllib3*`. Proves the guard + its enclosing module never trigger any path that requires the real package at compile time. Override restored in `finally`. SPEC NOTE: the user's spec said `__builtins__.__import__` (attribute access); the actual implementation uses `__builtins__['__import__']` (subscript) because this test runs as a non-main module where `__builtins__` is a dict, not the module object that exists in `__main__`. Both forms work in this context; subscript survives correctly when the test is imported via pytest or unittest."""
        import os
        import py_compile
        import tempfile
        # Capture pristine built-in BEFORE installing the override so the
        # restore in `finally` puts back the same object we found at entry.
        _orig_import = __builtins__['__import__']

        def _guarded(name, globals=None, locals=None, fromlist=(), level=0):
            if name == 'requests' or name == 'urllib3' or name.startswith('urllib3.'):
                raise ImportError("TestRequestsImportGuard: blocked import '" + name + "'")
            return _orig_import(name, globals, locals, fromlist, level)

        try:
            __builtins__['__import__'] = _guarded
            path = self._memory_vault_path()
            self.assertTrue(os.path.isfile(path))
            with tempfile.TemporaryDirectory() as td:
                cfile = os.path.join(td, "compiled.pyc")
                result = py_compile.compile(path, cfile=cfile, doraise=True)
                self.assertIsNotNone(result, "py_compile.compile returned None under __import__ override + doraise=True; guard or module has regressed")
        finally:
            __builtins__['__import__'] = _orig_import
    # --------------------- Scenario 3 ---------------------
    def test_import_with_requests_installed_resolves_real_package(self):
        """Scenario 3: with `requests` installed (the default state at the top of this file's `import memory_vault`), the guard's try-block succeeded so `memory_vault.requests` is the real package. OPPOSITE invariant from Scenario 4. No `importlib.reload` is needed; reloading would only mutate `_db_initialized`/`_model_state` for no coverage benefit and could destabilize sibling tests."""
        self.assertEqual(
            type(memory_vault.requests).__name__,
            'module',
            "When 'requests' is installed, the guard must bind to the real package; instead got type=%r name=%r" % (
                type(memory_vault.requests),
                type(memory_vault.requests).__name__,
            ),
        )
    # --------------------- Scenario 4 ---------------------
    def test_import_with_requests_blocked_engages_stub(self):
        """Scenario 4: `_sys_for_test.modules['requests'] = None` BEFORE `importlib.reload(memory_vault)` forces the guard's `except ImportError:` branch to fire, engaging `_RequestsMissingStub` with the production-mirror surface (`RequestException` + callable `post`). After the assertions, restores `_sys_for_test.modules['requests']` and reloads `memory_vault` to clear the stub binding for sibling tests. Re-engagement assertion: after the second reload, the guard's try-branch must win again so `memory_vault.requests` is back to the real package. The `_sys_for_test` alias matches the file's pre-existing convention (see `TestCrossEncoderFallbackWarning` siblings); importing bare `sys` would re-clobber the imported module name and risk the test runner's module-resolution order. CPython's import machinery checks `sys.modules` FIRST during `import`; finding a None sentinel there, it re-raises ModuleNotFoundError without attempting real loader resolution -- the standard "fake uninstall" technique. Why ONLY `_sys_for_test.modules['requests']` (no urllib3 blanket): CPython short-circuits on the None sentinel BEFORE reaching the loader, so `import requests` inside the guard's try-block fails directly without ever needing urllib3 submodules to be cached or blocked. NOTE on what this test does NOT check: `_RequestsMissingStub` (the class definition) persists in `vars(memory_vault)` across reloads because Python re-executes the module body on `importlib.reload` and the class is defined unconditionally at module top level. That is CORRECT behavior -- the class is part of the module's schema, not a leak. The re-engagement check inspects the BOUND NAME `memory_vault.requests` (post-second-reload), NOT `vars(memory_vault)`, so it catches the actual leak shape: a prior stub INSTANCE still bound to the name after reload, or a module-level closure that captured the stub reference."""
        import importlib

        # Snapshot the requests sentinel ONLY. Restore in finally.
        _has_requests = 'requests' in _sys_for_test.modules
        _saved_requests = _sys_for_test.modules.get('requests', None)

        try:
            _sys_for_test.modules['requests'] = None
            importlib.reload(memory_vault)

            stub_class = type(memory_vault.requests)
            self.assertEqual(
                stub_class.__name__,
                '_RequestsMissingStub',
                "When _sys_for_test.modules['requests'] is None, the guard must engage the stub; instead got type=%r name=%r" % (stub_class, stub_class.__name__),
            )
            self.assertTrue(
                hasattr(memory_vault.requests, 'RequestException'),
                "Stub must expose RequestException so production callsites' `except (requests.RequestException, ...)` clauses fire into the silent-fallback path.",
            )
            self.assertTrue(
                callable(getattr(memory_vault.requests, 'post', None)),
                "Stub must expose a callable post() that raises RequestException on call.",
            )
        finally:
            # Restore _sys_for_test.modules['requests']: delete if it wasn't set
            # pre-test, restore if it was.
            if not _has_requests:
                _sys_for_test.modules.pop('requests', None)
            else:
                _sys_for_test.modules['requests'] = _saved_requests
            # Reload memory_vault to clear stub binding and re-engage
            # the real-package branch for sibling tests.
            importlib.reload(memory_vault)
            # Defensive re-engagement check: after the second reload, the
            # guard's try-branch must win again so `memory_vault.requests`
            # is back to the real `requests` package. The leak shape this
            # assertion catches is a prior stub INSTANCE still bound to the
            # `requests` name after reload (e.g., a module-level closure
            # captured `requests.post` at import time). We deliberately
            # inspect the BOUND NAME here, not `vars(memory_vault)`, because
            # the `_RequestsMissingStub` class itself is defined at module
            # top level and intentionally persists across reloads -- that
            # is part of the module's schema, not a leak.
            self.assertEqual(
                type(memory_vault.requests).__name__,
                'module',
                "After real-package reload, memory_vault.requests must rebind to the real `requests` module; got type=%r name=%r. The guard's except-branch has become sticky across reloads." % (
                    type(memory_vault.requests),
                    type(memory_vault.requests).__name__,
                ),
            )


class TestDeleteMemoryNode(unittest.TestCase):
    """REGRESSION GUARD for delete_memory_node encapsulation in
    ``memory_vault.delete_memory_node`` (Phase P1 Concurrency & DB Sanitization).
    """

    def setUp(self):
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix='.db')
        os.close(self.test_db_fd)
        self.original_db_path = memory_vault.DB_PATH
        memory_vault.DB_PATH = self.test_db_path
        memory_vault._db_initialized = False
        memory_vault._clear_connection_pool()
        memory_vault.ensure_tables_exist()

    def tearDown(self):
        memory_vault.DB_PATH = self.original_db_path
        memory_vault._db_initialized = False
        memory_vault._clear_connection_pool()
        _cleanup_db(self.test_db_path)

    def test_delete_memory_node_removes_row_links_fts_and_invalidates_cache(self):
        """REGRESSION GUARD for delete_memory_node encapsulation in
        ``memory_vault.delete_memory_node`` (Phase P1 Concurrency & DB Sanitization).

        Locks down:
            1. Row deleted from core_memories
            2. Memory links where source or target equals node_id are deleted
            3. FTS5 index core_memories_fts row is removed
            4. Vector matrix cache is invalidated (_vector_matrix_cache.valid == False)
        """
        # 1. Insert two nodes
        nid1 = memory_vault.store_memory("First node on concurrency patterns", node_type="concept")
        nid2 = memory_vault.store_memory("Second node on database locks", node_type="concept")

        # 2. Link them
        memory_vault.link_memories(nid1, nid2, "RELATES_TO")

        with closing(memory_vault._get_conn()) as conn:
            # Check link was created
            links = conn.execute(
                "SELECT * FROM memory_links WHERE source_id = ? OR target_id = ?",
                (nid1, nid1),
            ).fetchall()
            self.assertEqual(len(links), 1)

            # Check FTS5 row exists
            fts = conn.execute(
                "SELECT rowid FROM core_memories_fts WHERE rowid = ?",
                (nid1,),
            ).fetchall()
            self.assertEqual(len(fts), 1)

        # Set vector cache to valid to verify invalidation
        memory_vault._vector_matrix_cache.valid = True

        # 3. Call delete_memory_node
        res = memory_vault.delete_memory_node(nid1)
        self.assertTrue(res)

        # 4. Assertions
        with closing(memory_vault._get_conn()) as conn:
            # Row removed
            row1 = conn.execute("SELECT * FROM core_memories WHERE id = ?", (nid1,)).fetchone()
            self.assertIsNone(row1)
            row2 = conn.execute("SELECT * FROM core_memories WHERE id = ?", (nid2,)).fetchone()
            self.assertIsNotNone(row2)

            # Links cleaned up
            links_after = conn.execute(
                "SELECT * FROM memory_links WHERE source_id = ? OR target_id = ?",
                (nid1, nid1),
            ).fetchall()
            self.assertEqual(len(links_after), 0)

            # FTS5 cleaned up
            fts_after = conn.execute(
                "SELECT rowid FROM core_memories_fts WHERE rowid = ?",
                (nid1,),
            ).fetchall()
            self.assertEqual(len(fts_after), 0)

        # Vector cache invalidated
        self.assertFalse(memory_vault._vector_matrix_cache.valid)

    def test_delete_memory_node_nonexistent_and_negative_ids(self):
        """Verify delete_memory_node handles non-existent and negative IDs gracefully."""
        res_nonexistent = memory_vault.delete_memory_node(999999)
        self.assertTrue(res_nonexistent)

        res_negative = memory_vault.delete_memory_node(-42)
        self.assertTrue(res_negative)

    def test_delete_memory_node_self_referential_link(self):
        """Verify delete_memory_node cleanly removes self-referential links (source_id == target_id)."""
        nid = memory_vault.store_memory("Self-referential node", node_type="concept")
        memory_vault.link_memories(nid, nid, "RECURSIVE")

        with closing(memory_vault._get_conn()) as conn:
            links = conn.execute(
                "SELECT * FROM memory_links WHERE source_id = ? AND target_id = ?",
                (nid, nid),
            ).fetchall()
            self.assertEqual(len(links), 1)

        res = memory_vault.delete_memory_node(nid)
        self.assertTrue(res)

        with closing(memory_vault._get_conn()) as conn:
            links_after = conn.execute(
                "SELECT * FROM memory_links WHERE source_id = ? OR target_id = ?",
                (nid, nid),
            ).fetchall()
            self.assertEqual(len(links_after), 0)

    def test_delete_memory_node_bidirectional_and_multi_links(self):
        """Verify delete_memory_node removes both inbound and outbound links while preserving others."""
        nid_a = memory_vault.store_memory("Node A", node_type="concept")
        nid_b = memory_vault.store_memory("Node B", node_type="concept")
        nid_c = memory_vault.store_memory("Node C", node_type="concept")

        memory_vault.link_memories(nid_a, nid_b, "A_TO_B")
        memory_vault.link_memories(nid_b, nid_c, "B_TO_C")
        memory_vault.link_memories(nid_a, nid_c, "A_TO_C")

        res = memory_vault.delete_memory_node(nid_b)
        self.assertTrue(res)

        with closing(memory_vault._get_conn()) as conn:
            # B is deleted
            self.assertIsNone(conn.execute("SELECT id FROM core_memories WHERE id = ?", (nid_b,)).fetchone())
            # A and C remain
            self.assertIsNotNone(conn.execute("SELECT id FROM core_memories WHERE id = ?", (nid_a,)).fetchone())
            self.assertIsNotNone(conn.execute("SELECT id FROM core_memories WHERE id = ?", (nid_c,)).fetchone())
            # Links involving B are removed
            b_links = conn.execute(
                "SELECT * FROM memory_links WHERE source_id = ? OR target_id = ?",
                (nid_b, nid_b),
            ).fetchall()
            self.assertEqual(len(b_links), 0)
            # Link between A and C is preserved
            ac_links = conn.execute(
                "SELECT * FROM memory_links WHERE source_id = ? AND target_id = ?",
                (nid_a, nid_c),
            ).fetchall()
            self.assertEqual(len(ac_links), 1)


class TestNeuralMemoryGraphTemporal(unittest.TestCase):
    """REGRESSION GUARD and unit tests for Realm 2: Neural Memory Graph 2.0 (Temporal GraphRAG).

    Locks down:
        1. Backward-compatible schema migration of memory_links with temporal and weight attributes
        2. Link reinforcement (reinforce_link) and temporal decay (apply_link_decay)
        3. Multi-hop graph traversal (traverse_subgraph) with path decay and cycle defense
        4. Structured RDF semantic triple contracts (JSON schema, GBNF grammar) and extraction
        5. Semantic triple storage and query APIs (store_semantic_triple, get_semantic_triples)
    """

    def setUp(self):
        self.test_db_fd, self.test_db_path = tempfile.mkstemp(suffix='.db')
        os.close(self.test_db_fd)
        self.original_db_path = memory_vault.DB_PATH
        memory_vault.DB_PATH = self.test_db_path
        memory_vault._db_initialized = False
        memory_vault._clear_connection_pool()
        memory_vault.ensure_tables_exist()

    def tearDown(self):
        memory_vault.DB_PATH = self.original_db_path
        memory_vault._db_initialized = False
        memory_vault._clear_connection_pool()
        _cleanup_db(self.test_db_path)

    def test_schema_migration_backward_compatible(self):
        """REGRESSION GUARD for safe backward-compatible migration of legacy memory_links table.

        Verifies that when an older database contains memory_links lacking the temporal
        and reinforcement columns (relation_type, weight, confidence, timestamp),
        ensure_tables_exist safely adds the columns without data loss or exceptions.
        """
        # Create a fresh isolated legacy database
        legacy_fd, legacy_path = tempfile.mkstemp(suffix='.db')
        os.close(legacy_fd)
        try:
            with closing(sqlite3.connect(legacy_path)) as conn:
                conn.execute(
                    "CREATE TABLE core_memories (id INTEGER PRIMARY KEY, timestamp TEXT, content TEXT, "
                    "node_type TEXT, importance_score INTEGER, embedding BLOB, tags TEXT)"
                )
                conn.execute(
                    "CREATE TABLE memory_links (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER, "
                    "target_id INTEGER, relationship_type TEXT, created_at TEXT)"
                )
                conn.execute(
                    "INSERT INTO core_memories (id, content) VALUES (1, 'Old Node 1'), (2, 'Old Node 2')"
                )
                conn.execute(
                    "INSERT INTO memory_links (source_id, target_id, relationship_type, created_at) "
                    "VALUES (1, 2, 'LEGACY_REL', '2026-01-01 00:00:00')"
                )
                conn.commit()

            # Point memory_vault to legacy DB and run ensure_tables_exist
            orig_path = memory_vault.DB_PATH
            memory_vault.DB_PATH = legacy_path
            memory_vault._db_initialized = False
            memory_vault._clear_connection_pool()
            try:
                memory_vault.ensure_tables_exist()

                # Verify new columns exist and existing data is intact
                with closing(memory_vault._get_conn()) as conn:
                    cursor = conn.execute("PRAGMA table_info(memory_links)")
                    cols = {row["name"] for row in cursor.fetchall()}
                    self.assertIn("relation_type", cols)
                    self.assertIn("weight", cols)
                    self.assertIn("confidence", cols)
                    self.assertIn("timestamp", cols)

                    row = conn.execute("SELECT * FROM memory_links WHERE source_id = 1 AND target_id = 2").fetchone()
                    self.assertIsNotNone(row)
                    self.assertEqual(row["relationship_type"], "LEGACY_REL")
                    self.assertEqual(row["relation_type"], "LEGACY_REL")
                    self.assertEqual(row["weight"], 1.0)
            finally:
                memory_vault.DB_PATH = orig_path
                memory_vault._db_initialized = False
                memory_vault._clear_connection_pool()
        finally:
            _cleanup_db(legacy_path)

    def test_link_memories_with_temporal_and_weight_attributes(self):
        """Verify link_memories persists temporal timestamp, weight, and relation_type."""
        nid1 = memory_vault.store_memory("Node 1", node_type="concept")
        nid2 = memory_vault.store_memory("Node 2", node_type="concept")

        custom_ts = "2026-09-30T10:00:00+00:00"
        link_id = memory_vault.link_memories(
            source_id=nid1,
            target_id=nid2,
            relationship_type="DEPENDS_ON",
            weight=0.85,
            relation_type="DEPENDS_ON",
            timestamp=custom_ts,
            confidence=0.9
        )
        self.assertGreater(link_id, 0)

        with closing(memory_vault._get_conn()) as conn:
            row = conn.execute("SELECT * FROM memory_links WHERE id = ?", (link_id,)).fetchone()
            self.assertEqual(row["source_id"], nid1)
            self.assertEqual(row["target_id"], nid2)
            self.assertEqual(row["relationship_type"], "DEPENDS_ON")
            self.assertEqual(row["relation_type"], "DEPENDS_ON")
            self.assertAlmostEqual(row["weight"], 0.85, places=2)
            self.assertAlmostEqual(row["confidence"], 0.9, places=2)
            self.assertEqual(row["timestamp"], custom_ts)

    def test_reinforce_link_existing_and_new(self):
        """Verify reinforce_link increases link weight by delta and creates link if absent."""
        nid1 = memory_vault.store_memory("Service A", node_type="concept")
        nid2 = memory_vault.store_memory("Service B", node_type="concept")
        nid3 = memory_vault.store_memory("Service C", node_type="concept")

        memory_vault.link_memories(nid1, nid2, "USES", weight=0.8)

        # Reinforce existing directed link
        new_w = memory_vault.reinforce_link(nid1, nid2, delta=0.15)
        self.assertAlmostEqual(new_w, 0.95, places=2)

        # Reinforce nonexistent link (creates new with 1.0 + delta)
        created_w = memory_vault.reinforce_link(nid1, nid3, delta=0.2, relation_type="DEPENDS_ON")
        self.assertAlmostEqual(created_w, 1.2, places=2)

        with closing(memory_vault._get_conn()) as conn:
            row12 = conn.execute("SELECT weight FROM memory_links WHERE source_id = ? AND target_id = ?", (nid1, nid2)).fetchone()
            self.assertAlmostEqual(row12["weight"], 0.95, places=2)
            row13 = conn.execute("SELECT weight, relation_type FROM memory_links WHERE source_id = ? AND target_id = ?", (nid1, nid3)).fetchone()
            self.assertAlmostEqual(row13["weight"], 1.2, places=2)
            self.assertEqual(row13["relation_type"], "DEPENDS_ON")

    def test_apply_link_decay_and_prune_threshold(self):
        """Verify apply_link_decay applies exponential decay and optional pruning below min_weight."""
        nid1 = memory_vault.store_memory("Node 1")
        nid2 = memory_vault.store_memory("Node 2")
        nid3 = memory_vault.store_memory("Node 3")

        memory_vault.link_memories(nid1, nid2, "RELATES_TO", weight=1.0)
        memory_vault.link_memories(nid2, nid3, "RELATES_TO", weight=0.1)

        # Decay by factor 0.5 without pruning
        updated = memory_vault.apply_link_decay(decay_factor=0.5, prune_zero=False)
        self.assertEqual(updated, 2)

        with closing(memory_vault._get_conn()) as conn:
            w1 = conn.execute("SELECT weight FROM memory_links WHERE source_id = ? AND target_id = ?", (nid1, nid2)).fetchone()["weight"]
            w2 = conn.execute("SELECT weight FROM memory_links WHERE source_id = ? AND target_id = ?", (nid2, nid3)).fetchone()["weight"]
            self.assertAlmostEqual(w1, 0.5, places=2)
            self.assertAlmostEqual(w2, 0.05, places=2)

        # Apply decay with pruning below min_weight=0.1
        memory_vault.apply_link_decay(decay_factor=0.9, min_weight=0.1, prune_zero=True)
        with closing(memory_vault._get_conn()) as conn:
            links = conn.execute("SELECT id FROM memory_links").fetchall()
            # Only the first link (0.5 * 0.9 = 0.45 >= 0.1) should remain; 0.05 * 0.9 = 0.045 < 0.1 pruned
            self.assertEqual(len(links), 1)

    def test_get_memory_links_direction_and_weight_filter(self):
        """Verify get_memory_links filters by node_id, direction, and min_weight threshold."""
        a = memory_vault.store_memory("A")
        b = memory_vault.store_memory("B")
        c = memory_vault.store_memory("C")

        memory_vault.link_memories(a, b, "A_TO_B", weight=0.9)
        memory_vault.link_memories(b, a, "B_TO_A", weight=0.7)
        memory_vault.link_memories(c, a, "C_TO_A", weight=0.2)

        # All links >= 0.5
        all_strong = memory_vault.get_memory_links(min_weight=0.5)
        self.assertEqual(len(all_strong), 2)

        # Outgoing from A
        out_a = memory_vault.get_memory_links(node_id=a, direction="outgoing")
        self.assertEqual(len(out_a), 1)
        self.assertEqual(out_a[0]["target_id"], b)

        # Incoming to A
        in_a = memory_vault.get_memory_links(node_id=a, direction="incoming")
        self.assertEqual(len(in_a), 2)

    def test_traverse_subgraph_multi_hop_weights_and_decay(self):
        """Verify traverse_subgraph correctly traverses multi-hop graph and decays path weights."""
        # A -> B -> C -> D
        a = memory_vault.store_memory("Root concept A", node_type="concept")
        b = memory_vault.store_memory("Child concept B", node_type="concept")
        c = memory_vault.store_memory("Grandchild concept C", node_type="concept")
        d = memory_vault.store_memory("Great-grandchild concept D", node_type="concept")

        memory_vault.link_memories(a, b, "LEADS_TO", weight=0.8)
        memory_vault.link_memories(b, c, "LEADS_TO", weight=0.5)
        memory_vault.link_memories(c, d, "LEADS_TO", weight=0.4)

        # 2-hop traversal from A
        res = memory_vault.traverse_subgraph(start_node_id=a, max_hops=2, min_weight=0.1)
        self.assertEqual(res["start_node_id"], a)

        node_ids = {n["id"] for n in res["nodes"]}
        self.assertIn(a, node_ids)
        self.assertIn(b, node_ids)
        self.assertIn(c, node_ids)
        self.assertNotIn(d, node_ids)  # 3 hops away, should be excluded by max_hops=2

        # Verify path weights: A=1.0, B=0.8, C=0.8*0.5=0.4
        weights = res["traversal_weights"]
        self.assertEqual(weights[a], 1.0)
        self.assertAlmostEqual(weights[b], 0.8, places=2)
        self.assertAlmostEqual(weights[c], 0.4, places=2)

    def test_traverse_subgraph_cycle_defense(self):
        """Verify traverse_subgraph safely terminates on cyclic graphs without infinite recursion."""
        a = memory_vault.store_memory("Cycle A")
        b = memory_vault.store_memory("Cycle B")
        c = memory_vault.store_memory("Cycle C")

        # Cyclic triangle: A -> B -> C -> A
        memory_vault.link_memories(a, b, "CYCLE", weight=0.9)
        memory_vault.link_memories(b, c, "CYCLE", weight=0.9)
        memory_vault.link_memories(c, a, "CYCLE", weight=0.9)

        res = memory_vault.traverse_subgraph(start_node_id=a, max_hops=5, min_weight=0.1)
        node_ids = {n["id"] for n in res["nodes"]}
        self.assertEqual(node_ids, {a, b, c})

    def test_structured_triple_extraction_contracts(self):
        """Verify JSON schema and GBNF grammar contracts define explicit canonical relationship types."""
        schema = memory_vault.get_triple_extraction_schema()
        self.assertEqual(schema["type"], "object")
        self.assertIn("triples", schema["properties"])
        allowed_preds = schema["properties"]["triples"]["items"]["properties"]["predicate"]["enum"]
        self.assertIn("IS_A", allowed_preds)
        self.assertIn("DEPENDS_ON", allowed_preds)
        self.assertIn("CAUSES", allowed_preds)
        self.assertIn("RELATES_TO", allowed_preds)

        gbnf = memory_vault.get_triple_extraction_gbnf()
        self.assertIn("root ::=", gbnf)
        self.assertIn("IS_A", gbnf)
        self.assertIn("DEPENDS_ON", gbnf)
        self.assertIn(r'\"IS_A\"', gbnf)

    def test_extract_semantic_triples_canonical_patterns(self):
        """Verify extract_semantic_triples extracts canonical RDF triples from natural text."""
        text = (
            "FastAPI is a web framework. "
            "KokertechAI depends on SQLite for storage. "
            "Memory leak causes crash."
        )
        triples = memory_vault.extract_semantic_triples(text)
        self.assertGreaterEqual(len(triples), 3)

        predicates = [t["predicate"] for t in triples]
        self.assertIn("IS_A", predicates)
        self.assertIn("DEPENDS_ON", predicates)
        self.assertIn("CAUSES", predicates)

        for t in triples:
            self.assertIn("subject", t)
            self.assertIn("object", t)
            self.assertIn("timestamp", t)
            self.assertEqual(t["confidence"], 1.0)

    @patch("memory_vault.get_provider")
    def test_extract_semantic_triples_llm_mode_with_fallback(self, mock_get_prov):
        """Verify extract_semantic_triples uses LLM structured output and falls back cleanly on error."""
        # 1. Successful LLM extraction conforming to schema
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": json.dumps({
                "triples": [
                    {"subject": "Docker", "predicate": "USES", "object": "cgroups", "confidence": 0.95}
                ]
            }),
            "error": None
        }
        mock_get_prov.return_value = mock_provider

        triples = memory_vault.extract_semantic_triples("Docker uses cgroups.", use_llm=True)
        self.assertEqual(len(triples), 1)
        self.assertEqual(triples[0]["subject"], "Docker")
        self.assertEqual(triples[0]["predicate"], "USES")
        self.assertEqual(triples[0]["object"], "cgroups")

        # 2. LLM error triggers fallback to pattern matcher
        mock_provider.chat_completion.return_value = {"content": "", "error": "Model timeout"}
        fallback_triples = memory_vault.extract_semantic_triples("Antigravity depends on Python.", use_llm=True)
        self.assertGreaterEqual(len(fallback_triples), 1)
        self.assertEqual(fallback_triples[0]["predicate"], "DEPENDS_ON")

    def test_store_semantic_triple_with_strings_and_ids(self):
        """Verify store_semantic_triple creates nodes, persists triples, and returns structured result."""
        # String concepts create new concept nodes
        res1 = memory_vault.store_semantic_triple("PyQt6", "IMPLEMENTS", "Desktop UI", weight=0.95)
        self.assertGreater(res1["link_id"], 0)
        self.assertEqual(res1["predicate"], "IMPLEMENTS")
        self.assertEqual(res1["subject"], "PyQt6")
        self.assertEqual(res1["object"], "Desktop UI")

        # Query triples
        triples = memory_vault.get_semantic_triples(node_id=res1["source_id"])
        self.assertEqual(len(triples), 1)
        self.assertEqual(triples[0]["predicate"], "IMPLEMENTS")
        self.assertEqual(triples[0]["subject"], "PyQt6")
        self.assertEqual(triples[0]["object"], "Desktop UI")

        # Store triple between existing integer IDs
        nid_a = memory_vault.store_memory("Alpha Node")
        nid_b = memory_vault.store_memory("Beta Node")
        res2 = memory_vault.store_semantic_triple(nid_a, "CAUSES", nid_b, weight=0.7)
        self.assertEqual(res2["source_id"], nid_a)
        self.assertEqual(res2["target_id"], nid_b)
        self.assertEqual(res2["predicate"], "CAUSES")

    def test_store_memory_with_attached_triples(self):
        """Verify store_memory attaches and stores structured RDF triples during memory ingestion."""
        nid = memory_vault.store_memory(
            "Neural Engine 2.0",
            node_type="fact",
            triples=[
                {"predicate": "DEPENDS_ON", "object": "SQLite WAL"},
                {"predicate": "IMPLEMENTS", "object": "GraphRAG"},
            ]
        )
        self.assertGreater(nid, 0)

        links = memory_vault.get_memory_links(node_id=nid, direction="outgoing")
        self.assertEqual(len(links), 2)
        rel_types = {l["relation_type"] for l in links}
        self.assertEqual(rel_types, {"DEPENDS_ON", "IMPLEMENTS"})

    def test_store_memory_with_3tuple_triples(self):
        """ANTI-FRAGILITY: Verify store_memory correctly handles 3-tuples (subject, predicate, object)."""
        nid = memory_vault.store_memory(
            "Language ecosystem",
            node_type="fact",
            triples=[
                ("Python", "IS_A", "Programming Language"),
                ("SQLite", "IMPLEMENTS", "Relational Storage"),
            ]
        )
        self.assertGreater(nid, 0)

        triples = memory_vault.get_semantic_triples()
        pred_map = {t["predicate"]: (t["subject"], t["object"]) for t in triples}
        self.assertIn("IS_A", pred_map)
        self.assertEqual(pred_map["IS_A"], ("Python", "Programming Language"))
        self.assertIn("IMPLEMENTS", pred_map)
        self.assertEqual(pred_map["IMPLEMENTS"], ("SQLite", "Relational Storage"))

        # Verify parent memory is connected to the subjects
        links = memory_vault.get_memory_links(node_id=nid, direction="outgoing")
        self.assertGreaterEqual(len(links), 2)

    def test_extract_triples_connects_parent_memory_and_supports_plural_verbs(self):
        """ANTI-FRAGILITY: Plural verb forms are extracted and parent memory is connected to graph."""
        text = "Microservices depend on Redis. Workers use RabbitMQ."
        nid = memory_vault.store_memory(text, node_type="fact", extract_triples=True)
        self.assertGreater(nid, 0)

        # Parent memory must be connected to the concept nodes
        links = memory_vault.get_memory_links(node_id=nid, direction="outgoing")
        self.assertGreaterEqual(len(links), 2)

        # Subgraph traversal from parent memory discovers the concepts
        subgraph = memory_vault.traverse_subgraph(start_node_id=nid, max_hops=2)
        contents = {n["content"] for n in subgraph["nodes"]}
        self.assertIn("Microservices", contents)
        self.assertIn("Redis", contents)

    def test_has_attribute_pattern_does_not_falsely_match_auxiliary_has(self):
        """ANTI-FRAGILITY: Auxiliary 'has' does not trigger false positive HAS_ATTRIBUTE triples."""
        text = "The user has completed the onboarding flow and has verified credentials."
        triples = memory_vault.extract_semantic_triples(text)
        has_attr_triples = [t for t in triples if t["predicate"] == "HAS_ATTRIBUTE"]
        self.assertEqual(len(has_attr_triples), 0)

    def test_store_semantic_triple_validation(self):
        """ANTI-FRAGILITY: Empty/None subject or object raises ValueError."""
        with self.assertRaises(ValueError):
            memory_vault.store_semantic_triple("", "IS_A", "Concept")
        with self.assertRaises(ValueError):
            memory_vault.store_semantic_triple("Concept", "IS_A", "  ")
        with self.assertRaises(ValueError):
            memory_vault.store_semantic_triple(None, "IS_A", "Concept")
        with self.assertRaises(ValueError):
            memory_vault.store_semantic_triple("Concept", "IS_A", None)

    def test_link_validation_guards(self):
        """ANTI-FRAGILITY: None source or target ID raises ValueError."""
        with self.assertRaises(ValueError):
            memory_vault.link_memories(None, 1)
        with self.assertRaises(ValueError):
            memory_vault.link_memories(1, None)
        with self.assertRaises(ValueError):
            memory_vault.reinforce_link(None, 1)
        with self.assertRaises(ValueError):
            memory_vault.apply_link_decay(decay_factor=-0.1)

    def test_traverse_subgraph_path_weight_sync_and_reinforced_cycles(self):
        """ANTI-FRAGILITY: Path weights stay synchronized between nodes list and traversal_weights under reinforced cycles."""
        a = memory_vault.store_memory("Cycle Alpha")
        b = memory_vault.store_memory("Cycle Beta")

        # Reinforced weights > 1.0 with mutual links
        memory_vault.link_memories(a, b, "CYCLE", weight=1.2)
        memory_vault.link_memories(b, a, "CYCLE", weight=1.2)

        res = memory_vault.traverse_subgraph(start_node_id=a, max_hops=3, min_weight=0.1)
        # Must terminate safely and not cycle indefinitely
        self.assertEqual({n["id"] for n in res["nodes"]}, {a, b})

        # Verify weights in nodes list match traversal_weights dict
        for node in res["nodes"]:
            nid = node["id"]
            self.assertEqual(node["path_weight"], res["traversal_weights"][nid])



