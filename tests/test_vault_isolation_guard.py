"""REGRESSION GUARD: default-deny vault isolation (Sprint 19.8.1).

Root cause context: production core_memories was poisoned by ~743k test rows
(see conftest._default_deny_vault_isolation docstring). These tests lock in
the fix:

1. Any test writing via memory_vault lands in a TEMP DB, never production.
2. _reset_memory_vault() preserves the redirected DB_PATH across
   importlib.reload() (the July reload-reset leak).
3. The real_vault marker bypasses isolation explicitly.
"""
import os

import memory_vault as mv


class TestDefaultDenyVaultIsolation:
    def test_store_memory_writes_temp_db_not_production(self, tmp_path):
        """AGILITY: production vault must never receive test rows."""
        prod_path = os.path.join(os.path.dirname(os.path.abspath(mv.__file__)),
                                 "kokertech_vault.db")
        n_before = _count_rows(prod_path)
        mv.store_memory("isolation guard probe row", node_type="fact", importance=1)
        assert _count_rows(prod_path) == n_before

    def test_db_path_points_at_temp_file(self):
        """AGILITY: under isolation, DB_PATH is a temp file, not the prod vault."""
        assert mv.DB_PATH.endswith(".db")
        assert "kokertech_test_vault_" in mv.DB_PATH
        assert not mv.DB_PATH.endswith("kokertech_vault.db")

    def test_rows_writable_and_queryable_in_temp_db(self):
        """AGILITY: normal vault operations still work under isolation.

        _get_embedding is stubbed (deterministic 384-dim vectors) so the
        hybrid search path is exercised without loading a native model —
        same pattern as the conftest-level provider patching.
        """
        import numpy as np
        from unittest.mock import patch

        def fake_embedding(text):
            rng = np.random.default_rng(abs(hash(text)) % (2**32))
            return rng.standard_normal(384).astype(np.float32)

        with patch.object(mv, "_get_embedding", side_effect=fake_embedding):
            row_id = mv.store_memory("isolation roundtrip probe", importance=2)
            hits = mv.semantic_search("isolation roundtrip probe", top_k=5)
        assert any(h[0] == row_id for h in hits), f"row {row_id} not found in {hits}"

    def test_reset_memory_vault_preserves_db_path(self):
        """AGILITY: importlib.reload() in _reset_memory_vault must NOT reset
        DB_PATH back to production mid-session (the July leak mechanism)."""
        redirected = mv.DB_PATH
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "root_conftest", os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "conftest.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._reset_memory_vault()
        assert mv.DB_PATH == redirected


def _count_rows(db_path: str) -> int:
    import sqlite3
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM core_memories").fetchone()[0]
    finally:
        conn.close()
