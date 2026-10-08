"""tests/test_context_service.py -- dedicated ContextService coverage.

Closes the KNOWLEDGE.md §12 coverage-gap row "services/ ... No test
coverage" for ``services/context_service.py`` (Sprint 3 extraction).
Previously only exercised indirectly through controller-level suites;
this file pins the service's own contracts directly:

- ``fetch_components`` parallel dispatch (5 components, injected fns)
- graceful-degradation fallbacks for each memory lookup
- ``format_biases`` / ``format_growth_arc`` empty + exception paths
- ``get_recent_biases_inline`` SQLite TTL cache (fresh / cached / miss)
- ``load_user_profile`` reader delegation
- ``get_vram_usage`` nvidia-smi probe (0 on failure)

ANTI-FRAGILITY: every SQLite test builds its own temp ``kokertech_vault.db``
under ``self._tmpdir`` and tears it down -- no cross-test pollution, no
dependency on the real workspace vault. ``ContextService.shutdown()`` is
called in ``tearDown`` so the 5-worker ``ThreadPoolExecutor`` never leaks
across tests.

REGRESSION GUARD: ``test_fetch_components_injected_fns_return_all_5_keys``
locks the 5-key return shape (``context`` / ``scbe_text`` / ``growth_text``
/ ``episodic_context`` / ``weighted_episodic``) -- fires if a future
refactor drops or renames a component key.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from services.context_service import ContextService

# Expected component keys returned by fetch_components (REGRESSION GUARD).
_FETCH_KEYS = ("context", "scbe_text", "growth_text", "episodic_context", "weighted_episodic")


class _BiasGrowthRowMixin:
    """Shared helpers to build temp vaults with bias_ledger / growth_arc rows."""

    def _make_tmp_workspace(self):
        tmpdir = tempfile.mkdtemp(prefix="ctx_svc_test_")
        self._tmpdirs.append(tmpdir)
        return tmpdir

    def _make_vault(self, workspace, biases=(), growth=()):
        """Create <workspace>/kokertech_vault.db with optional rows."""
        db_path = os.path.join(workspace, "kokertech_vault.db")
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE bias_ledger ("
                "id INTEGER PRIMARY KEY, timestamp TEXT, agent_id TEXT, "
                "bias_type TEXT, confidence_score REAL, description TEXT)"
            )
            conn.execute(
                "CREATE TABLE growth_arc ("
                "id INTEGER PRIMARY KEY, timestamp TEXT, agent_id TEXT, "
                "event_description TEXT, energy_shift REAL)"
            )
            for i, (btype, desc) in enumerate(biases):
                conn.execute(
                    "INSERT INTO bias_ledger (timestamp, agent_id, bias_type, "
                    "confidence_score, description) VALUES (?, ?, ?, ?, ?)",
                    (f"2026-08-01T00:00:{i:02d}", "agent", btype, 80.0, desc),
                )
            for i, (ev, shift) in enumerate(growth):
                conn.execute(
                    "INSERT INTO growth_arc (timestamp, agent_id, event_description, "
                    "energy_shift) VALUES (?, ?, ?, ?)",
                    (f"2026-08-01T00:00:{i:02d}", "agent", ev, shift),
                )
        return db_path


class TestFetchComponents(unittest.TestCase, _BiasGrowthRowMixin):
    """Parallel dispatch + graceful-degradation fallbacks."""

    def setUp(self):
        self._tmpdirs = []
        self.ws = self._make_tmp_workspace()
        self.svc = ContextService(self.ws)

    def tearDown(self):
        self.svc.shutdown()
        import shutil
        for d in self._tmpdirs:
            # ignore_errors=True already swallows; no outer try/except needed
            shutil.rmtree(d, ignore_errors=True)

    def test_fetch_components_injected_fns_return_all_5_keys(self):
        """REGRESSION GUARD for the fetch_components 5-key shape.

        Injected ``*_fn`` callables bypass ``memory_vault`` entirely, so the
        service's own dispatch logic is exercised in isolation. The key set
        is the contract consumed by the controller facade -- a dropped key
        would silently blank a system-prompt section.
        """
        out = self.svc.fetch_components(
            "hello",
            agent_type="primary",
            semantic_search_fn=lambda text, top_k=1: [("", "", "semantic hit")],
            session_context_fn=lambda limit=3: "session ctx",
            episodic_context_fn=lambda limit=3, session_id=None, query="": "episodic ctx",
        )
        for key in _FETCH_KEYS:
            self.assertIn(key, out, "fetch_components missing key " + repr(key))
        self.assertEqual(out["context"], "semantic hit")
        self.assertEqual(out["episodic_context"], "session ctx")
        self.assertEqual(out["weighted_episodic"], "episodic ctx")

    def test_fetch_components_semantic_empty_falls_back(self):
        """Empty semantic result -> 'No prior history found.' string."""
        out = self.svc.fetch_components(
            "hello",
            agent_type="primary",
            semantic_search_fn=lambda text, top_k=1: [],
            session_context_fn=lambda limit=3: "s",
            episodic_context_fn=lambda limit=3, session_id=None, query="": "e",
        )
        self.assertEqual(out["context"], "No prior history found.")

    def test_fetch_components_semantic_raise_falls_back(self):
        """Exception in semantic fn -> graceful fallback, other keys intact."""

        def _boom(text, top_k=1):
            raise RuntimeError("semantic down")

        out = self.svc.fetch_components(
            "hello",
            agent_type="primary",
            semantic_search_fn=_boom,
            session_context_fn=lambda limit=3: "s",
            episodic_context_fn=lambda limit=3, session_id=None, query="": "e",
        )
        self.assertEqual(out["context"], "No prior history found.")
        self.assertEqual(out["episodic_context"], "s")
        self.assertEqual(out["weighted_episodic"], "e")

    def test_fetch_components_session_raise_falls_back(self):
        def _boom(limit=3):
            raise RuntimeError("session down")

        out = self.svc.fetch_components(
            "hello",
            agent_type="primary",
            semantic_search_fn=lambda text, top_k=1: [("", "", "hit")],
            session_context_fn=_boom,
            episodic_context_fn=lambda limit=3, session_id=None, query="": "e",
        )
        self.assertEqual(out["episodic_context"], "No episodic journal available.")
        self.assertEqual(out["context"], "hit")

    def test_fetch_components_episodic_raise_falls_back(self):
        def _boom(limit=3, session_id=None, query=""):
            raise RuntimeError("episodic down")

        out = self.svc.fetch_components(
            "hello",
            agent_type="primary",
            semantic_search_fn=lambda text, top_k=1: [("", "", "hit")],
            session_context_fn=lambda limit=3: "s",
            episodic_context_fn=_boom,
        )
        self.assertEqual(out["weighted_episodic"], "No episodic journal available.")

    def test_fetch_components_default_paths_use_memory_vault(self):
        """Late-bound defaults route to memory_vault.* (patch at call time).

        ANTI-FRAGILITY: ALL five memory_vault functions are patched -- the
        ``_biases`` / ``_growth`` futures also call ``get_recent_bias`` /
        ``get_growth_arc``, and leaving them real would read the production
        vault (code-reviewer-glm isolation finding).
        """
        with patch("memory_vault.semantic_search", return_value=[("", "", "vault hit")]), \
                patch("memory_vault.get_session_context", return_value="vault session"), \
                patch("memory_vault.get_episodic_context", return_value="vault episodic"), \
                patch("memory_vault.get_recent_bias", return_value=[]), \
                patch("memory_vault.get_growth_arc", return_value=[]):
            out = self.svc.fetch_components("hello", agent_type="primary")
        self.assertEqual(out["context"], "vault hit")
        self.assertEqual(out["episodic_context"], "vault session")
        self.assertEqual(out["weighted_episodic"], "vault episodic")


class TestFormatBiases(unittest.TestCase):
    """format_biases / format_growth_arc (memory_vault late-bound)."""

    def setUp(self):
        self.svc = ContextService(tempfile.mkdtemp(prefix="ctx_bias_"))

    def tearDown(self):
        self.svc.shutdown()

    def test_format_biases_empty_returns_placeholder(self):
        with patch("memory_vault.get_recent_bias", return_value=[]):
            self.assertEqual(
                self.svc.format_biases("primary"), "No bias drift recorded yet."
            )

    def test_format_biases_exception_returns_placeholder(self):
        def _boom(agent_type):
            raise RuntimeError("vault down")

        with patch("memory_vault.get_recent_bias", side_effect=_boom):
            self.assertEqual(
                self.svc.format_biases("primary"), "No bias drift recorded yet."
            )

    def test_format_biases_renders_rows(self):
        rows = [
            {
                "timestamp": "2026-08-01T10:00:00",
                "bias_type": "recency",
                "confidence_score": 75.0,
                "description": "overweighted latest turn",
            },
            {
                "timestamp": "2026-08-01T09:00:00",
                "bias_type": "confirmation",
                "confidence_score": 60.0,
                "description": "echo user belief",
            },
        ]
        with patch("memory_vault.get_recent_bias", return_value=rows):
            text = self.svc.format_biases("primary")
        self.assertIn("[2026-08-01T10:00:00] recency (Confidence: 75.0%)", text)
        self.assertIn("[2026-08-01T09:00:00] confirmation (Confidence: 60.0%)", text)
        self.assertIn("overweighted latest turn", text)

    def test_format_growth_arc_empty_returns_placeholder(self):
        with patch("memory_vault.get_growth_arc", return_value=[]):
            self.assertEqual(
                self.svc.format_growth_arc("primary"), "No growth events recorded yet."
            )

    def test_format_growth_arc_exception_returns_placeholder(self):
        def _boom(agent_type):
            raise RuntimeError("vault down")

        with patch("memory_vault.get_growth_arc", side_effect=_boom):
            self.assertEqual(
                self.svc.format_growth_arc("primary"), "No growth events recorded yet."
            )

    def test_format_growth_arc_renders_rows(self):
        rows = [
            {
                "timestamp": "2026-08-01T10:00:00",
                "event_description": "energy spike",
                "energy_shift": 12.5,
            },
        ]
        with patch("memory_vault.get_growth_arc", return_value=rows):
            text = self.svc.format_growth_arc("primary")
        self.assertIn("[2026-08-01T10:00:00] energy spike (Energy Shift: 12.5%)", text)


class TestGetRecentBiasesInline(unittest.TestCase, _BiasGrowthRowMixin):
    """SQLite TTL cache for the SCBE system-prompt block."""

    def setUp(self):
        self._tmpdirs = []
        self.ws = self._make_tmp_workspace()
        self.svc = ContextService(self.ws)

    def tearDown(self):
        self.svc.shutdown()
        import shutil
        for d in self._tmpdirs:
            # ignore_errors=True already swallows; no outer try/except needed
            shutil.rmtree(d, ignore_errors=True)

    def test_no_vault_file_returns_empty(self):
        self.assertEqual(self.svc.get_recent_biases_inline(), "")

    def test_empty_bias_table_returns_empty(self):
        self._make_vault(self.ws, biases=())
        self.assertEqual(self.svc.get_recent_biases_inline(), "")

    def test_rows_rendered_into_scbe_block(self):
        self._make_vault(self.ws, biases=[("recency", "overweight latest")])
        text = self.svc.get_recent_biases_inline()
        self.assertIn("[RECENT BIASES TO AVOID (Self-Correction)]:", text)
        self.assertIn("- recency: overweight latest", text)

    def test_ttl_cache_hit_avoids_second_db_read(self):
        self._make_vault(self.ws, biases=[("recency", "overweight latest")])
        first = self.svc.get_recent_biases_inline()
        self.assertNotEqual(first, "")
        # Drop the table: a cache hit must NOT re-query the DB.
        with sqlite3.connect(os.path.join(self.ws, "kokertech_vault.db")) as conn:
            conn.execute("DROP TABLE bias_ledger")
        second = self.svc.get_recent_biases_inline()
        self.assertEqual(first, second, "TTL cache should serve the second read")

    def test_ttl_expiry_rereads_db(self):
        self._make_vault(self.ws, biases=[("recency", "old bias")])
        first = self.svc.get_recent_biases_inline()
        self.assertIn("old bias", first)
        # Force TTL expiry + change data underneath.
        self.svc._bias_cache_ttl = 0.0
        with sqlite3.connect(os.path.join(self.ws, "kokertech_vault.db")) as conn:
            conn.execute("DELETE FROM bias_ledger")
            conn.execute(
                "INSERT INTO bias_ledger (timestamp, agent_id, bias_type, "
                "confidence_score, description) VALUES (?, ?, ?, ?, ?)",
                ("2026-08-01T12:00:00", "agent", "confirmation", 90.0, "new bias"),
            )
        second = self.svc.get_recent_biases_inline()
        self.assertIn("new bias", second)
        self.assertNotIn("old bias", second)

    def test_db_error_falls_back_empty(self):
        # Corrupt the vault file so sqlite raises on connect.
        db_path = os.path.join(self.ws, "kokertech_vault.db")
        with open(db_path, "wb") as f:
            f.write(b"\x00\x01\x02 not a database")
        self.assertEqual(self.svc.get_recent_biases_inline(), "")


class TestLoadUserProfile(unittest.TestCase):
    """cached_json_reader delegation."""

    def setUp(self):
        self.svc = ContextService(tempfile.mkdtemp(prefix="ctx_prof_"))

    def tearDown(self):
        self.svc.shutdown()

    def test_no_data_returns_fallback(self):
        self.assertEqual(
            self.svc.load_user_profile(lambda path: None),
            "No specific personal identity layer configured yet.",
        )

    def test_reader_receives_workspace_path(self):
        seen = {}

        def reader(path):
            seen["path"] = path
            return {"name": "Jacques", "tone": "Technical & Direct"}

        text = self.svc.load_user_profile(reader)
        self.assertTrue(seen["path"].endswith("user_identity.json"))
        self.assertIn("Preferred Name: Jacques", text)
        self.assertIn("Tone Preferences: Technical & Direct", text)

    def test_missing_keys_use_defaults(self):
        # A truthy dict WITHOUT name/tone keys exercises the .get() defaults;
        # an empty dict is falsy and short-circuits to the fallback string.
        text = self.svc.load_user_profile(lambda path: {"other_field": "x"})
        self.assertIn("Preferred Name: Jacques", text)
        self.assertIn("Tone Preferences: Technical & Direct", text)


class TestGetVramUsage(unittest.TestCase):
    """nvidia-smi probe -- 0 on failure, int on success."""

    def setUp(self):
        self.svc = ContextService(tempfile.mkdtemp(prefix="ctx_vram_"))

    def tearDown(self):
        self.svc.shutdown()

    def test_vram_probe_failure_returns_zero(self):
        with patch("subprocess.check_output", side_effect=FileNotFoundError("nvidia-smi")):
            self.assertEqual(self.svc.get_vram_usage(), 0)

    def test_vram_probe_success_parses_mb(self):
        with patch("subprocess.check_output", return_value=b"1234\n"):
            self.assertEqual(self.svc.get_vram_usage(), 1234)


class TestShutdown(unittest.TestCase):
    """Lifecycle: shutdown is idempotent + releases the pool."""

    def test_shutdown_idempotent(self):
        svc = ContextService(tempfile.mkdtemp(prefix="ctx_shut_"))
        svc.shutdown()
        # Second call must not raise (RuntimeError swallowed).
        svc.shutdown()


if __name__ == "__main__":
    unittest.main()
