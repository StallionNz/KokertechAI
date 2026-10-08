"""test_sprint13_hybrid_search.py - Sprint 13: Hybrid Search & RAG V2 tests."""
import logging
import os
import sys
import sqlite3
import time
import pytest

logger = logging.getLogger(__name__)

# Embedding-load gate handled by conftest.py pytest_configure() which sets
# os.environ["KOKERTECH_SKIP_EMBEDDINGS"] = "1" before collection begins.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_test_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS core_memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, content TEXT,
        importance_score INTEGER, node_type TEXT DEFAULT 'fact', embedding BLOB,
        last_accessed TEXT, metadata TEXT DEFAULT '{}', tags TEXT DEFAULT '[]')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS episodic_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, session_id TEXT,
        summary TEXT, tags TEXT DEFAULT '[]', importance_score INTEGER DEFAULT 5,
        metadata TEXT DEFAULT '{}', embedding BLOB)""")
    # Use standalone FTS5 tables (no content= option) for clean test isolation.
    # Production DB uses external content tables; these tests verify FTS5
    # search/sync behavior independent of the content-table model.
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS core_memories_fts USING fts5(content)")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS episodic_journal_fts USING fts5(summary)")
    return conn


def _insert_core(conn, content, importance=5):
    cur = conn.execute("INSERT INTO core_memories (timestamp, content, importance_score, tags) VALUES (?, ?, ?, ?)",
                       (time.strftime("%Y-%m-%d %H:%M:%S"), content, importance, "[]"))
    rid = cur.lastrowid
    conn.execute("INSERT INTO core_memories_fts(rowid, content) VALUES (?, ?)", (rid, content))
    return rid


def _insert_episodic(conn, summary, importance=5):
    cur = conn.execute("INSERT INTO episodic_journal (timestamp, session_id, summary, tags, importance_score) VALUES (?, ?, ?, ?, ?)",
                       (time.strftime("%Y-%m-%d %H:%M:%S"), "test", summary, "[]", importance))
    rid = cur.lastrowid
    conn.execute("INSERT INTO episodic_journal_fts(rowid, summary) VALUES (?, ?)", (rid, summary))
    return rid


class TestFTS5Tables:
    def test_fts5_tables_created(self):
        conn = _make_test_db()
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'").fetchall()
        names = [t["name"] for t in tables]
        assert "core_memories_fts" in names
        assert "episodic_journal_fts" in names
        conn.close()

    def test_fts5_insert_and_search(self):
        conn = _make_test_db()
        _insert_core(conn, "The quick brown fox jumps over the lazy dog")
        _insert_core(conn, "Python is a popular programming language for AI")
        _insert_core(conn, "Machine learning requires large datasets")
        results = conn.execute("SELECT rowid, bm25(core_memories_fts) AS rank, content FROM core_memories_fts WHERE core_memories_fts MATCH ? ORDER BY rank LIMIT 5", ("python",)).fetchall()
        assert len(results) >= 1
        assert any("Python" in r["content"] for r in results)
        conn.close()

    def test_fts5_bm25_ranking(self):
        conn = _make_test_db()
        _insert_core(conn, "deep learning neural network architecture")
        _insert_core(conn, "the cat sat on the mat")
        _insert_core(conn, "deep learning with PyTorch and TensorFlow")
        results = conn.execute("SELECT rowid, bm25(core_memories_fts) AS rank, content FROM core_memories_fts WHERE core_memories_fts MATCH ? ORDER BY rank LIMIT 5", ("deep learning",)).fetchall()
        assert len(results) >= 2
        conn.close()

    def test_fts5_rebuild_index(self):
        conn = _make_test_db()
        conn.execute("INSERT INTO core_memories (timestamp, content, importance_score, tags) VALUES (?, ?, ?, ?)",
                     (time.strftime("%Y-%m-%d %H:%M:%S"), "rebuilt content", 5, "[]"))
        # Standalone FTS: content table INSERT does NOT populate FTS
        assert conn.execute("SELECT COUNT(*) FROM core_memories_fts").fetchone()[0] == 0
        # Rebuild: clear FTS and re-populate from core_memories
        conn.execute("INSERT INTO core_memories_fts(core_memories_fts) VALUES('rebuild')")
        conn.execute("INSERT INTO core_memories_fts(rowid, content) SELECT id, content FROM core_memories WHERE content IS NOT NULL")
        assert conn.execute("SELECT COUNT(*) FROM core_memories_fts").fetchone()[0] == 1
        conn.close()

    def test_fts5_episodic_search(self):
        conn = _make_test_db()
        _insert_episodic(conn, "Discussed Docker containerization strategy")
        _insert_episodic(conn, "Reviewed Python code for performance")
        _insert_episodic(conn, "Docker networking configuration issues")
        results = conn.execute("SELECT rowid, bm25(episodic_journal_fts) AS rank, summary FROM episodic_journal_fts WHERE episodic_journal_fts MATCH ? ORDER BY rank LIMIT 5", ("docker",)).fetchall()
        assert len(results) >= 2
        conn.close()


class TestReciprocalRankFusion:
    def test_rrf_single_ranking(self):
        k = 60
        rrf = {}
        for _id, src, content, rank in [("A", "core", "cA", 1), ("B", "core", "cB", 2)]:
            rrf[(_id, src)] = {"score": 1.0 / (k + rank)}
        assert abs(rrf[("A", "core")]["score"] - 1.0 / 61) < 1e-10
        assert rrf[("A", "core")]["score"] > rrf[("B", "core")]["score"]

    def test_rrf_fusion_combines(self):
        k, alpha = 60, 0.6
        semantic = [("A", "core", "c1", 1), ("B", "core", "c2", 3)]
        fts = [("A", "core", "c1", 2), ("B", "core", "c2", 1)]
        rrf = {}
        for _id, src, content, rank in semantic:
            rrf[(_id, src)] = {"score": alpha / (k + rank)}
        for _id, src, content, rank in fts:
            key = (_id, src)
            if key not in rrf:
                rrf[key] = {"score": 0.0}
            rrf[key]["score"] += (1 - alpha) / (k + rank)
        assert rrf[("A", "core")]["score"] > rrf[("B", "core")]["score"]

    def test_rrf_boosts_doc_in_both(self):
        k, alpha = 60, 0.5
        rrf = {}
        for _id, src, content, rank in [("A", "core", "c1", 1), ("B", "core", "c2", 1)]:
            rrf[(_id, src)] = {"score": alpha / (k + rank)}
        rrf[("A", "core")]["score"] += (1 - alpha) / (k + 1)
        assert rrf[("A", "core")]["score"] > rrf[("B", "core")]["score"]


class TestFTSIndexSync:
    def test_insert_syncs_core_fts(self):
        from memory_vault import _fts_sync_core_insert
        conn = _make_test_db()
        cur = conn.execute("INSERT INTO core_memories (timestamp, content, importance_score, tags) VALUES (?, ?, ?, ?)",
                           (time.strftime("%Y-%m-%d %H:%M:%S"), "synced", 5, "[]"))
        _fts_sync_core_insert(conn, cur.lastrowid, "synced")
        assert conn.execute("SELECT COUNT(*) FROM core_memories_fts WHERE rowid = ?", (cur.lastrowid,)).fetchone()[0] == 1
        conn.close()

    def test_delete_syncs_core_fts(self):
        from memory_vault import _fts_sync_core_delete
        conn = _make_test_db()
        rid = _insert_core(conn, "to delete")
        _fts_sync_core_delete(conn, rid)
        assert conn.execute("SELECT COUNT(*) FROM core_memories_fts WHERE rowid = ?", (rid,)).fetchone()[0] == 0
        conn.close()

    def test_sync_disabled_noop(self):
        import memory_vault
        conn = _make_test_db()
        old = memory_vault._FTS_SYNC_ENABLED
        memory_vault._FTS_SYNC_ENABLED = False
        try:
            cur = conn.execute("INSERT INTO core_memories (timestamp, content, importance_score, tags) VALUES (?, ?, ?, ?)",
                               (time.strftime("%Y-%m-%d %H:%M:%S"), "no sync", 5, "[]"))
            memory_vault._fts_sync_core_insert(conn, cur.lastrowid, "no sync")
            assert conn.execute("SELECT COUNT(*) FROM core_memories_fts WHERE rowid = ?", (cur.lastrowid,)).fetchone()[0] == 0
        finally:
            memory_vault._FTS_SYNC_ENABLED = old
            conn.close()


class TestHybridSearchConfig:
    def test_hybrid_search_enabled_default(self):
        from config import CONFIG
        assert CONFIG.get("hybrid_search_enabled") is True

    def test_hybrid_search_alpha_default(self):
        from config import CONFIG
        assert CONFIG.get("hybrid_search_alpha") == 0.6

    def test_fts_rebuild_on_start_default(self):
        from config import CONFIG
        assert CONFIG.get("fts_rebuild_on_start") is False



class TestMemoryVaultFTS5:
    def _cleanup_row(self, row_id):
        """Remove a core_memories row and trigger FTS rebuild for clean state."""
        import memory_vault
        try:
            with memory_vault._get_conn() as conn:
                conn.execute("DELETE FROM core_memories WHERE id = ?", (row_id,))
                conn.commit()  # MUST commit or DELETE rolls back on close
            # Rebuild FTS index to stay in sync (individual FTS5 delete on
            # external content tables is unreliable)
            memory_vault.rebuild_fts_index()
        except Exception as e:
            # Surfaced, not silent: a failed rebuild leaves a dirty FTS index
            # that would otherwise manifest as a confusing failure in the NEXT
            # test. Logging pinpoints the real cause (v0.22.13 audit).
            logger.warning(
                "FTS5 cleanup rebuild failed for row %s: %s", row_id, e
            )

    def test_fts5_search_returns_results(self):
        import memory_vault
        memory_vault.ensure_tables_exist()
        # Use a unique search token so stale rows from prior runs don't
        # crowd out the freshly-inserted row under LIMIT 5.
        token = f"ZZFTS5_{int(time.time() * 1000)}"
        content = f"{token} Hybrid search FTS5 validation"
        mid = memory_vault.store_memory(content, importance=7)
        try:
            assert mid > 0
            results = memory_vault.fts5_search(token, top_k=5)
            assert len(results) >= 1
            ids = [r[0] for r in results]
            assert mid in ids
        finally:
            self._cleanup_row(mid)

    def test_fts5_search_empty_query(self):
        import memory_vault
        assert memory_vault.fts5_search("", top_k=5) == []
        assert memory_vault.fts5_search(None, top_k=5) == []

    def test_fts5_search_no_match(self):
        import memory_vault
        memory_vault.ensure_tables_exist()
        assert memory_vault.fts5_search("xyzzy_nonexistent_term_12345", top_k=5) == []

    def test_hybrid_search_returns_results(self):
        import memory_vault
        memory_vault.ensure_tables_exist()
        # Use a unique search token so stale rows from prior runs don't
        # crowd out the freshly-inserted row under LIMIT 5.
        token = f"ZZHYBRID_{int(time.time() * 1000)}"
        content = f"{token} hybrid integration test for RRF fusion"
        mid = memory_vault.store_memory(content, importance=7)
        try:
            assert mid > 0
            results = memory_vault.hybrid_search(token, top_k=5)
            assert len(results) >= 1
            ids = [r[0] for r in results]
            assert mid in ids
        finally:
            self._cleanup_row(mid)

    def test_hybrid_search_empty_query(self):
        import memory_vault
        assert memory_vault.hybrid_search("", top_k=5) == []