"""
test_agentic_rag.py — Unit tests for the Agentic RAG engine.

Tests query decomposition, retrieval, gap analysis, refinement query
generation, and synthesis, all using mocked AI provider responses.
"""

import json
import threading
import unittest
from unittest.mock import patch, MagicMock, ANY

from agentic_rag import AgenticRAGEngine, _clear_rag_inrun_cache



class TestQueryDecomposition(unittest.TestCase):
    """Test the _decompose_query method."""

    def setUp(self):
        self.engine = AgenticRAGEngine(max_hops=2, top_k_per_source=3)

    @patch("agentic_rag.get_provider")
    def test_decompose_simple_query(self, mock_get_provider):
        """A simple query should return just itself."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": '["What is the capital of France?"]',
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        result = self.engine._decompose_query("What is the capital of France?")
        self.assertEqual(len(result), 1)
        self.assertIn("France", result[0])

    @patch("agentic_rag.get_provider")
    def test_decompose_complex_query(self, mock_get_provider):
        """A complex query should be decomposed into multiple sub-questions."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": json.dumps([
                "What are the performance characteristics of Docker for AI?",
                "What are the performance characteristics of Podman for AI?",
                "Which container runtime has better GPU support?",
            ]),
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        result = self.engine._decompose_query(
            "Docker vs Podman performance for AI inference and GPU support?"
        )
        self.assertEqual(len(result), 3)
        self.assertIn("Docker", result[0])
        self.assertIn("GPU", result[2])

    @patch("agentic_rag.get_provider")
    def test_decompose_caps_at_5(self, mock_get_provider):
        """Should cap sub-questions at 5."""
        many_questions = [f"Sub-question {i}?" for i in range(10)]
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": json.dumps(many_questions),
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        result = self.engine._decompose_query("Very complex query?")
        self.assertLessEqual(len(result), 5)

    @patch("agentic_rag.get_provider")
    def test_decompose_fallback_on_error(self, mock_get_provider):
        """Should fall back to the original query on AI error."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "error": "Connection refused",
            "content": "",
        }
        mock_get_provider.return_value = mock_provider

        result = self.engine._decompose_query("Fallback test?")
        self.assertEqual(result, ["Fallback test?"])

    @patch("agentic_rag.get_provider")
    def test_decompose_fallback_on_bad_json(self, mock_get_provider):
        """Should fall back on unparseable output."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "This is not valid JSON",
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        result = self.engine._decompose_query("Bad JSON?")
        self.assertEqual(result, ["Bad JSON?"])

    @patch("agentic_rag.get_provider")
    def test_decompose_exception_raises_fallback(self, mock_get_provider):
        """When get_provider itself raises, falls back to single query."""
        mock_get_provider.side_effect = RuntimeError("Provider crash")
        result = self.engine._decompose_query("Crash?")
        self.assertEqual(result, ["Crash?"])


class TestGapAnalysis(unittest.TestCase):
    """Test the _evaluate_gaps method."""

    def setUp(self):
        self.engine = AgenticRAGEngine(max_hops=2, top_k_per_source=3)

    def test_no_contexts_returns_gaps(self):
        """With no contexts, should always report gaps."""
        result = self.engine._evaluate_gaps("test query", [])
        self.assertFalse(result["sufficient"])
        self.assertIn("no information", result["gaps"][0].lower())

    @patch("agentic_rag.get_provider")
    def test_sufficient_coverage(self, mock_get_provider):
        """When AI says sufficient, should return True."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": json.dumps({
                "sufficient": True,
                "gaps": [],
                "reasoning": "All facets are covered.",
            }),
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        contexts = [{"source": "core_memory", "content": "Some relevant info", "score": 0.95}]
        result = self.engine._evaluate_gaps("test query", contexts)
        self.assertTrue(result["sufficient"])
        self.assertEqual(len(result["gaps"]), 0)

    @patch("agentic_rag.get_provider")
    def test_insufficient_with_gaps(self, mock_get_provider):
        """When AI identifies gaps, should return them."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": json.dumps({
                "sufficient": False,
                "gaps": ["Need GPU benchmarks", "Need CPU requirements"],
                "reasoning": "Missing performance data.",
            }),
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        contexts = [{"source": "core_memory", "content": "Some info", "score": 0.5}]
        result = self.engine._evaluate_gaps("test query", contexts)
        self.assertFalse(result["sufficient"])
        self.assertIn("GPU", result["gaps"][0])

    @patch("agentic_rag.get_provider")
    def test_gap_analysis_fallback_on_error(self, mock_get_provider):
        """Should fall back gracefully on AI error."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "error": "Timeout",
            "content": "",
        }
        mock_get_provider.return_value = mock_provider

        contexts = [{"source": "core_memory", "content": "Some info", "score": 0.5}]
        result = self.engine._evaluate_gaps("test query", contexts)
        self.assertFalse(result["sufficient"])

    @patch("agentic_rag.get_provider")
    def test_gap_analysis_bad_json(self, mock_get_provider):
        """Should fall back when provider returns unparseable JSON."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "not valid json at all",
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        contexts = [{"source": "core_memory", "content": "Some info", "score": 0.5}]
        result = self.engine._evaluate_gaps("test query", contexts)
        self.assertFalse(result["sufficient"])
        self.assertIn("Parse error", result["reasoning"])


class TestRefinementQueries(unittest.TestCase):
    """Test the _generate_refinement_queries method."""

    def setUp(self):
        self.engine = AgenticRAGEngine(max_hops=2, top_k_per_source=3)

    @patch("agentic_rag.get_provider")
    def test_refinement_generation(self, mock_get_provider):
        """Should generate targeted refinement queries from gaps."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": json.dumps([
                "Docker GPU CUDA benchmarks 2024",
                "Podman vs Docker GPU support comparison",
            ]),
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        gaps = ["Need GPU benchmarks", "Need comparison data"]
        result = self.engine._generate_refinement_queries(gaps, "original query")
        self.assertEqual(len(result), 2)
        self.assertIn("GPU", result[0])

    @patch("agentic_rag.get_provider")
    def test_refinement_fallback(self, mock_get_provider):
        """Should fall back to gap descriptions on error."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "error": "Provider unavailable",
            "content": "",
        }
        mock_get_provider.return_value = mock_provider

        gaps = ["Need specific data"]
        result = self.engine._generate_refinement_queries(gaps, "query")
        self.assertEqual(result, gaps)

    @patch("agentic_rag.get_provider")
    def test_refinement_bad_json_fallback(self, mock_get_provider):
        """Should fall back to gaps when provider returns unparseable content."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "not valid json",
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        gaps = ["Need more data"]
        result = self.engine._generate_refinement_queries(gaps, "query")
        self.assertEqual(result, gaps)

    @patch("agentic_rag.get_provider")
    def test_refinement_caps_at_3(self, mock_get_provider):
        """Should cap refinement queries at 3."""
        many_queries = [f"Refinement query {i}" for i in range(10)]
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": json.dumps(many_queries),
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        gaps = ["Gap 1", "Gap 2"]
        result = self.engine._generate_refinement_queries(gaps, "query")
        self.assertLessEqual(len(result), 3)

    @patch("agentic_rag.get_provider")
    def test_refinement_exception_fallback(self, mock_get_provider):
        """Should fall back to gaps when get_provider raises."""
        mock_get_provider.side_effect = RuntimeError("Crash")
        gaps = ["Need data"]
        result = self.engine._generate_refinement_queries(gaps, "query")
        self.assertEqual(result, gaps)


class TestSynthesis(unittest.TestCase):
    """Test the _synthesize_answer method."""

    def setUp(self):
        self.engine = AgenticRAGEngine(max_hops=2, top_k_per_source=3)

    def test_synthesis_no_contexts(self):
        """Should return a helpful message when no contexts available."""
        result = self.engine._synthesize_answer("test query", [], [])
        self.assertIn("unable to find", result.lower())

    @patch("agentic_rag.get_provider")
    def test_synthesis_with_contexts(self, mock_get_provider):
        """Should synthesize contexts into an answer."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "Here is a synthesized answer covering all aspects.",
            "error": None,
        }
        mock_get_provider.return_value = mock_provider

        contexts = [
            {"source": "core_memory", "content": "Docker runs as a daemon.", "score": 0.95},
            {"source": "episodic", "content": "Podman is daemonless.", "score": 0.85},
        ]
        result = self.engine._synthesize_answer(
            "Docker vs Podman?", contexts,
            ["What is Docker?", "What is Podman?"]
        )
        self.assertIn("synthesized", result.lower())

    @patch("agentic_rag.get_provider")
    def test_synthesis_error_handling(self, mock_get_provider):
        """Should handle AI errors gracefully."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "error": "Synthesis model unavailable",
            "content": "",
        }
        mock_get_provider.return_value = mock_provider

        contexts = [{"source": "core_memory", "content": "Some info", "score": 0.9}]
        result = self.engine._synthesize_answer("test", contexts, [])
        self.assertIn("Synthesis error", result)

    @patch("agentic_rag.get_provider")
    def test_synthesis_exception(self, mock_get_provider):
        """Should handle provider exception gracefully."""
        mock_get_provider.side_effect = RuntimeError("Synthesis crashed")
        contexts = [{"source": "core_memory", "content": "Some info", "score": 0.9}]
        result = self.engine._synthesize_answer("test", contexts, [])
        self.assertIn("error occurred", result.lower())


class TestRetrieval(unittest.TestCase):
    """Test the _retrieve_for method (completely untested before)."""

    def setUp(self):
        # Phase D 15.3: clear module-level RAG cache so test_xxx vault mocks
        # aren't masked by stale entries from prior tests sharing this process
        # (unittest runner bypasses conftest.py; this is the test-side
        # defense-in-depth sibling of conftest._reset_rag_inrun_cache_per_test).
        _clear_rag_inrun_cache()
        self.engine = AgenticRAGEngine(max_hops=2, top_k_per_source=3)

    def tearDown(self):
        # Symmetric to setUp: prevent bleed if a test poisons the cache
        # (mirrors TestRetrieveForCache.tearDown).
        _clear_rag_inrun_cache()

    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_core_memory(self, mock_search_episodic, mock_semantic_search):
        """Retrieves from core memory vault."""
        mock_semantic_search.return_value = [
            (1, "fact", "Docker uses a daemon.", 0.95),
        ]
        mock_search_episodic.return_value = []

        result = self.engine._retrieve_for("Docker?", existing_contexts=[])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "core_memory")
        self.assertIn("daemon", result[0]["content"])

    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_episodic(self, mock_search_episodic, mock_semantic_search):
        """Retrieves from episodic journal."""
        mock_semantic_search.return_value = []
        mock_search_episodic.return_value = [
            (1, "episodic", "Podman is daemonless.", 0.85, [], 6, "2026-06-01"),
        ]

        result = self.engine._retrieve_for("Podman?", existing_contexts=[])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "episodic")

    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_both_sources(self, mock_search_episodic, mock_semantic_search):
        """Retrieves from both sources and combines results."""
        mock_semantic_search.return_value = [
            (1, "fact", "Docker uses a daemon.", 0.95),
        ]
        mock_search_episodic.return_value = [
            (1, "episodic", "Podman is daemonless.", 0.85, [], 6, "2026-06-01"),
        ]

        result = self.engine._retrieve_for("Containers?", existing_contexts=[])
        self.assertEqual(len(result), 2)
        sources = {c["source"] for c in result}
        self.assertEqual(sources, {"core_memory", "episodic"})

    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_deduplicates_existing(self, mock_search_episodic, mock_semantic_search):
        """Deduplicates against existing_contexts content."""
        mock_semantic_search.return_value = [
            (1, "fact", "Duplicate content.", 0.95),
        ]
        mock_search_episodic.return_value = []

        result = self.engine._retrieve_for(
            "Test?", existing_contexts=[{"content": "Duplicate content."}]
        )
        self.assertEqual(len(result), 0)

    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_core_memory_exception(self, mock_search_episodic, mock_semantic_search):
        """Core memory search exception is caught gracefully."""
        mock_semantic_search.side_effect = RuntimeError("DB locked")
        mock_search_episodic.return_value = []

        result = self.engine._retrieve_for("Test?", existing_contexts=[])
        self.assertEqual(len(result), 0)

    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_episodic_exception(self, mock_search_episodic, mock_semantic_search):
        """Episodic search exception is caught gracefully."""
        mock_semantic_search.return_value = []
        mock_search_episodic.side_effect = RuntimeError("Search crashed")

        result = self.engine._retrieve_for("Test?", existing_contexts=[])
        self.assertEqual(len(result), 0)

    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_no_results(self, mock_search_episodic, mock_semantic_search):
        """Returns empty list when no results from any source."""
        mock_semantic_search.return_value = []
        mock_search_episodic.return_value = []

        result = self.engine._retrieve_for("Test?", existing_contexts=[])
        self.assertEqual(len(result), 0)

    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_web_search_enabled(
        self, mock_search_episodic, mock_semantic_search
    ):
        """Web search is called when enabled.
        The plugins.search_web module uses execute() not search_web(),
        so we patch sys.modules to add the search_web attribute.
        """
        self.engine.enable_web_search = True
        mock_semantic_search.return_value = []
        mock_search_episodic.return_value = []

        mock_web = MagicMock(return_value="Web search result about Docker.")
        mock_mod = MagicMock(search_web=mock_web)
        with patch.dict("sys.modules", {"plugins.search_web": mock_mod}):
            result = self.engine._retrieve_for("Docker?", existing_contexts=[])

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "web")

    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_web_import_error(
        self, mock_search_episodic, mock_semantic_search
    ):
        """Web search import error is caught gracefully."""
        self.engine.enable_web_search = True
        mock_semantic_search.return_value = []
        mock_search_episodic.return_value = []

        # Simulate ImportError by having the module in sys.modules
        # but without the search_web attribute
        mock_mod_no_func = MagicMock(spec_set=[])
        with patch.dict("sys.modules", {"plugins.search_web": mock_mod_no_func}):
            result = self.engine._retrieve_for("Docker?", existing_contexts=[])

        self.assertEqual(len(result), 0)

    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_episodic_empty_content_skipped(
        self, mock_search_episodic, mock_semantic_search
    ):
        """Episodic entries with empty content are skipped."""
        mock_semantic_search.return_value = []
        mock_search_episodic.return_value = [
            (1, "episodic", "", 0.5, [], 6, "2026-06-01"),
            (2, "episodic", "Valid content.", 0.8, [], 6, "2026-06-01"),
        ]

        result = self.engine._retrieve_for("Test?", existing_contexts=[])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "Valid content.")


class TestRetrieveForCache(unittest.TestCase):
    """Regression tests for the module-level RAG retrieval cache (Phase D 15.3).

    Before this cache existed, ``answer()`` used a per-call dict that only
    held results for one ``answer()`` invocation -- so a second ``answer()``
    call with the same sub-question re-issued the vault searches. The new
    module-level cache, keyed on SHA-256 of (stripped + lowercased) question
    + TTL aligned with ``CONFIG["response_cache_ttl"]`` (default 60s), shares
    vault work across calls.
    """

    def setUp(self):
        """Reset module-level cache + provider mocks BEFORE each test."""
        from agentic_rag import _clear_rag_inrun_cache
        _clear_rag_inrun_cache()
        from ai_base import reset_providers
        reset_providers()
        self.engine = AgenticRAGEngine(max_hops=2, top_k_per_source=3)

    def tearDown(self):
        # Don't bleed cache state into other test files (tests run in shared process).
        from agentic_rag import _clear_rag_inrun_cache
        _clear_rag_inrun_cache()

    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.semantic_search")
    def test_first_call_populates_cache(self, mock_search_episodic, mock_semantic_search):
        """A first call goes to the vault and stores the result in the cache."""
        mock_semantic_search.return_value = [
            (1, "fact", "Docker runs as a daemon.", 0.95),
        ]
        mock_search_episodic.return_value = []
        from agentic_rag import _RAG_RETRIEVE_CACHE, _compute_rag_retrieve_cache_key

        result = self.engine._retrieve_for("Docker?", existing_contexts=[])
        cache_key = _compute_rag_retrieve_cache_key("Docker?")
        self.assertIn(cache_key, _RAG_RETRIEVE_CACHE)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "Docker runs as a daemon.")

    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.semantic_search")
    def test_second_call_uses_cache_skipping_vault(self, mock_search_episodic, mock_semantic_search):
        """A second call with the same question should NOT hit the vault.

        Regression guard for the original Phase D 15.3 bug: pre-fix,
        per-call dict meant a separate ``answer()`` call with the same
        sub-question re-issued the vault.
        """
        mock_semantic_search.return_value = [
            (1, "fact", "Docker runs as a daemon.", 0.95),
        ]
        mock_search_episodic.return_value = []
        # First call populates the cache.
        self.engine._retrieve_for("Docker?", existing_contexts=[])
        self.assertEqual(mock_semantic_search.call_count, 1)
        # Second call is a HIT; vault MUST NOT be called again.
        result = self.engine._retrieve_for("Docker?", existing_contexts=[])
        self.assertEqual(
            mock_semantic_search.call_count, 1,
            "vault should NOT be hit a second time for an identical question "
            "(Phase D 15.3 regression; same-question dedup via module-level cache)",
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "Docker runs as a daemon.")

    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.semantic_search")
    def test_different_questions_different_keys(self, mock_search_episodic, mock_semantic_search):
        """Different questions produce different cache keys; both vault calls issued."""
        mock_semantic_search.side_effect = lambda *a, **kw: []
        mock_search_episodic.return_value = []
        self.engine._retrieve_for("What is Docker?", existing_contexts=[])
        self.engine._retrieve_for("What is Podman?", existing_contexts=[])
        self.assertEqual(mock_semantic_search.call_count, 2)

    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.semantic_search")
    def test_cache_returns_shallow_copy(self, mock_search_episodic, mock_semantic_search):
        """Caller mutation of cached result must NOT poison the cache.

        Mirrors the ``cached_chat_completion.HIT_path_return`` invariant:
        the cache must hand out a shallow copy so callers can mutate their
        own view without poisoning the cache for subsequent callers.
        """
        mock_semantic_search.return_value = [
            (1, "fact", "Cached content.", 0.95),
        ]
        mock_search_episodic.return_value = []
        r1 = self.engine._retrieve_for("Test?", existing_contexts=[])
        # Mutate the caller's view of the cache.
        r1[0]["content"] = "POISONED"
        del r1[0]
        # Second call returns the original cached value, unaffected by the mutation.
        r2 = self.engine._retrieve_for("Test?", existing_contexts=[])
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0]["content"], "Cached content.")

    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.semantic_search")
    def test_ttl_expires_entries(self, mock_search_episodic, mock_semantic_search):
        """Manually backdating the cache entry's ts triggers a re-issue."""
        mock_semantic_search.return_value = [
            (1, "fact", "Cached content.", 0.95),
        ]
        mock_search_episodic.return_value = []
        # First call populates the cache.
        self.engine._retrieve_for("TTL test?", existing_contexts=[])
        self.assertEqual(mock_semantic_search.call_count, 1)
        # Backdate the cache entry's ts so it's older than TTL.
        from agentic_rag import (
            _RAG_RETRIEVE_CACHE, _RAG_RETRIEVE_CACHE_LOCK,
            _compute_rag_retrieve_cache_key,
        )
        cache_key = _compute_rag_retrieve_cache_key("TTL test?")
        with _RAG_RETRIEVE_CACHE_LOCK:
            if cache_key in _RAG_RETRIEVE_CACHE:
                _RAG_RETRIEVE_CACHE[cache_key]["ts"] = 0  # ancient timestamp
        # Second call -- TTL expired, should re-issue vault call.
        self.engine._retrieve_for("TTL test?", existing_contexts=[])
        self.assertEqual(
            mock_semantic_search.call_count, 2,
            "expired cache entry should re-issue vault call",
        )

    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.semantic_search")
    def test_cache_disabled_passes_through(self, mock_search_episodic, mock_semantic_search):
        """When ``CONFIG["rag_inrun_cache_enabled"]`` is False, every call hits the vault."""
        mock_semantic_search.return_value = [
            (1, "fact", "Cached content.", 0.95),
        ]
        mock_search_episodic.return_value = []
        from agentic_rag import _clear_rag_inrun_cache
        from config import CONFIG
        _clear_rag_inrun_cache()
        original = CONFIG.get("rag_inrun_cache_enabled", True)
        CONFIG["rag_inrun_cache_enabled"] = False
        try:
            self.engine._retrieve_for("Disabled?", existing_contexts=[])
            self.engine._retrieve_for("Disabled?", existing_contexts=[])
            self.engine._retrieve_for("Disabled?", existing_contexts=[])
            self.assertEqual(
                mock_semantic_search.call_count, 3,
                "with cache disabled, every call should hit the vault",
            )
        finally:
            CONFIG["rag_inrun_cache_enabled"] = original
            _clear_rag_inrun_cache()


class TestFullPipeline(unittest.TestCase):
    """Test the complete answer() method end-to-end."""

    def setUp(self):
        # Phase D 15.3: clear before engine creation; sub-question overlap
        # across tests is the bleed vector (sibling: TestRetrieval.setUp
        # + conftest._reset_rag_inrun_cache_per_test).
        _clear_rag_inrun_cache()
        self.engine = AgenticRAGEngine(max_hops=2, top_k_per_source=3)

    def tearDown(self):
        # Symmetric to setUp: prevent bleed if a test poisons the cache
        # (mirrors TestRetrieveForCache.tearDown).
        _clear_rag_inrun_cache()

    @patch("agentic_rag.get_provider")
    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.store_episodic")
    def test_full_pipeline_success(
        self,
        mock_store_episodic,
        mock_search_episodic,
        mock_semantic_search,
        mock_get_provider,
    ):
        """The full pipeline should return a structured result dict."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.side_effect = [
            {"content": '["What is Docker?", "What is Podman?"]', "error": None},
            {"content": json.dumps({
                "sufficient": True, "gaps": [], "reasoning": "Covered"
            }), "error": None},
            {"content": "Docker and Podman are both container runtimes. "
                        "Docker uses a daemon, Podman is daemonless.",
             "error": None},
        ]
        mock_get_provider.return_value = mock_provider
        mock_semantic_search.return_value = [
            (1, "fact", "Docker is a container runtime that uses a daemon.", 0.95),
        ]
        mock_search_episodic.return_value = [
            (1, "episodic", "Podman is daemonless and rootless.", 0.85, [], 6, "2026-06-01"),
        ]
        mock_store_episodic.return_value = 1

        logs = []
        result = self.engine.answer(
            "Compare Docker and Podman.",
            log_callback=lambda x: logs.append(x),
        )

        self.assertIn("answer", result)
        self.assertIn("sub_questions", result)
        self.assertIn("contexts", result)
        self.assertIn("hops", result)
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["sub_questions"]), 2)
        self.assertGreater(len(result["contexts"]), 0)
        self.assertGreater(result["hops"], 0)
        self.assertIn("Docker", result["answer"])
        self.assertTrue(any("RAG complete" in log for log in logs))

    @patch("agentic_rag.get_provider")
    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.store_episodic")
    def test_full_pipeline_no_contexts(
        self, mock_store_episodic, mock_search_episodic,
        mock_semantic_search, mock_get_provider,
    ):
        """Should handle the case where no contexts are found."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.side_effect = [
            {"content": '["What is Docker?"]', "error": None},
            {"content": "No information found in my research.", "error": None},
        ]
        mock_get_provider.return_value = mock_provider
        mock_semantic_search.return_value = []
        mock_search_episodic.return_value = []
        mock_store_episodic.return_value = 1

        result = self.engine.answer("What is Docker?")
        self.assertIsNotNone(result["answer"])
        self.assertEqual(len(result["contexts"]), 0)

    @patch("agentic_rag.get_provider")
    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.store_episodic")
    def test_pipeline_short_circuits_on_no_new_contexts(
        self, mock_store_episodic, mock_search_episodic,
        mock_semantic_search, mock_get_provider,
    ):
        """When no contexts found in hop 0, should short-circuit and not retry."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.side_effect = [
            {"content": '["Q1", "Q2"]', "error": None},
            {"content": "No info found.", "error": None},
        ]
        mock_get_provider.return_value = mock_provider
        mock_semantic_search.return_value = []
        mock_search_episodic.return_value = []
        mock_store_episodic.return_value = 1

        result = self.engine.answer("Any question?")
        self.assertEqual(result["hops"], 1)

    def test_init_defaults(self):
        """Should initialize with sensible defaults."""
        engine = AgenticRAGEngine()
        self.assertEqual(engine.max_hops, 3)
        self.assertEqual(engine.top_k, 5)
        self.assertFalse(engine.enable_web_search)

    def test_init_custom_values(self):
        """Should accept custom initialization values."""
        engine = AgenticRAGEngine(
            provider_name="local_llm",
            model="llama3",
            max_hops=5,
            top_k_per_source=10,
            enable_web_search=True,
        )
        self.assertEqual(engine.provider_name, "local_llm")
        self.assertEqual(engine.model, "llama3")
        self.assertEqual(engine.max_hops, 5)
        self.assertEqual(engine.top_k, 10)
        self.assertTrue(engine.enable_web_search)

    @patch("agentic_rag.memory_vault.get_current_session_id")
    def test_answer_default_session_id(self, mock_get_sid):
        """Should use memory_vault session ID when none provided."""
        mock_get_sid.return_value = "test-session-123"
        engine = AgenticRAGEngine(max_hops=1, top_k_per_source=1)
        with patch("agentic_rag.get_provider") as mock_get_prov:
            mock_prov = MagicMock()
            mock_prov.chat_completion.side_effect = [
                {"content": '["Q1"]', "error": None},
                {"content": "Answer.", "error": None},
            ]
            mock_get_prov.return_value = mock_prov
            with patch("agentic_rag.memory_vault.semantic_search", return_value=[]):
                with patch("agentic_rag.memory_vault.search_episodic", return_value=[]):
                    with patch("agentic_rag.memory_vault.store_episodic", return_value=1):
                        result = engine.answer("Test?")
        self.assertIn("answer", result)
        mock_get_sid.assert_called_once()

    @patch("agentic_rag.memory_vault.get_current_session_id")
    def test_answer_session_id_fallback(self, mock_get_sid):
        """Should fall back to 'default' when memory_vault unavailable."""
        mock_get_sid.side_effect = RuntimeError("No vault")
        engine = AgenticRAGEngine(max_hops=1, top_k_per_source=1)
        with patch("agentic_rag.get_provider") as mock_get_prov:
            mock_prov = MagicMock()
            mock_prov.chat_completion.side_effect = [
                {"content": '["Q1"]', "error": None},
                {"content": "Answer.", "error": None},
            ]
            mock_get_prov.return_value = mock_prov
            with patch("agentic_rag.memory_vault.semantic_search", return_value=[]):
                with patch("agentic_rag.memory_vault.search_episodic", return_value=[]):
                    with patch("agentic_rag.memory_vault.store_episodic", return_value=1):
                        result = engine.answer("Test?")
        self.assertIn("answer", result)

    def test_async_execution(self):
        """answer_async should return a daemon thread."""
        t = self.engine.answer_async("test query")
        self.assertIsInstance(t, threading.Thread)
        self.assertTrue(t.daemon)
        t.join(timeout=2)

    @patch("agentic_rag.memory_vault.get_current_session_id")
    def test_async_execution_with_callback(self, mock_get_sid):
        """answer_async should invoke callback with result."""
        mock_get_sid.return_value = "session-1"
        engine = AgenticRAGEngine(max_hops=1, top_k_per_source=1)

        callback_results = []

        def cb(result):
            callback_results.append(result)

        with patch("agentic_rag.get_provider") as mock_get_prov:
            mock_prov = MagicMock()
            mock_prov.chat_completion.side_effect = [
                {"content": '["Q1"]', "error": None},
                {"content": "Answer.", "error": None},
            ]
            mock_get_prov.return_value = mock_prov
            with patch("agentic_rag.memory_vault.semantic_search", return_value=[]):
                with patch("agentic_rag.memory_vault.search_episodic", return_value=[]):
                    with patch("agentic_rag.memory_vault.store_episodic", return_value=1):
                        t = engine.answer_async(
                            "Test?", callback=cb, log_callback=lambda x: None
                        )
                        t.join(timeout=2)
        self.assertEqual(len(callback_results), 1)
        self.assertIn("answer", callback_results[0])

    @patch("agentic_rag.get_provider")
    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.store_episodic")
    def test_pipeline_error_handling(
        self, mock_store_episodic, mock_search_episodic,
        mock_semantic_search, mock_get_provider,
    ):
        """Should handle top-level errors gracefully.

        The RuntimeError is caught by inner try/except blocks,
        so the answer indicates failure in the text rather than
        setting result['error'].
        """
        mock_get_provider.side_effect = RuntimeError("Provider unavailable")
        mock_semantic_search.return_value = []
        mock_search_episodic.return_value = []
        mock_store_episodic.return_value = 1

        result = self.engine.answer("Test query")
        self.assertIn("answer", result)
        self.assertIsNone(result["error"])

    @patch("agentic_rag.get_provider")
    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.store_episodic")
    def test_pipeline_store_episodic_failure(
        self, mock_store_episodic, mock_search_episodic,
        mock_semantic_search, mock_get_provider,
    ):
        """Should handle store_episodic failure gracefully."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.side_effect = [
            {"content": '["Q1", "Q2"]', "error": None},
            {"content": json.dumps({
                "sufficient": True, "gaps": [], "reasoning": "Covered"
            }), "error": None},
            {"content": "Final answer here.", "error": None},
        ]
        mock_get_provider.return_value = mock_provider
        mock_semantic_search.return_value = []
        mock_search_episodic.return_value = []
        mock_store_episodic.side_effect = RuntimeError("Storage failed")

        result = self.engine.answer("Test query")
        self.assertIn("answer", result)
        self.assertIsNone(result["error"])

    @patch("agentic_rag.get_provider")
    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.store_episodic")
    def test_pipeline_error_all_components(
        self, mock_store_episodic, mock_search_episodic,
        mock_semantic_search, mock_get_provider,
    ):
        """When all components fail, the pipeline degrades gracefully.
        Each inner method has its own try/except, so errors never
        propagate to the top-level except. Result['error'] is None."""
        mock_get_provider.side_effect = RuntimeError("Total failure")
        mock_semantic_search.side_effect = RuntimeError("Search crash")

        result = self.engine.answer("Test query")
        self.assertIn("answer", result)
        # Error is caught at inner levels; result.error remains None
        self.assertIsNone(result["error"])

    @patch("agentic_rag.get_provider")
    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.store_episodic")
    def test_pipeline_deduplicates_contexts(
        self, mock_store_episodic, mock_search_episodic,
        mock_semantic_search, mock_get_provider,
    ):
        """Pipeline deduplicates contexts with identical content."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.side_effect = [
            {"content": '["Q1"]', "error": None},
            {"content": json.dumps({
                "sufficient": True, "gaps": [], "reasoning": "Covered"
            }), "error": None},
            {"content": "Answer.", "error": None},
        ]
        mock_get_provider.return_value = mock_provider
        # Both vault and episodic return the same content
        mock_semantic_search.return_value = [
            (1, "fact", "Docker uses a daemon.", 0.95),
        ]
        mock_search_episodic.return_value = [
            (1, "episodic", "Docker uses a daemon.", 0.85, [], 6, "2026-06-01"),
        ]
        mock_store_episodic.return_value = 1

        result = self.engine.answer("Test query")
        # Only 1 unique context (the duplicate was removed)
        self.assertEqual(len(result["contexts"]), 1)

    @patch("agentic_rag.get_provider")
    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.store_episodic")
    @patch("agentic_rag.memory_vault.get_current_session_id")
    def test_answer_with_explicit_session_id(
        self, mock_get_sid, mock_store, mock_search_epi,
        mock_semantic, mock_get_prov,
    ):
        """When session_id is provided, get_current_session_id is not called."""
        mock_get_sid.return_value = "auto-session"
        mock_provider = MagicMock()
        mock_provider.chat_completion.side_effect = [
            {"content": '["Q1"]', "error": None},
            {"content": "Answer.", "error": None},
        ]
        mock_get_prov.return_value = mock_provider
        mock_semantic.return_value = []
        mock_search_epi.return_value = []
        mock_store.return_value = 1

        engine = AgenticRAGEngine(max_hops=1, top_k_per_source=1)
        result = engine.answer("Test?", session_id="explicit-session")
        self.assertIn("answer", result)
        # get_current_session_id should NOT be called since we passed session_id
        mock_get_sid.assert_not_called()


class TestGraphRAGContextExpansion(unittest.TestCase):
    """REGRESSION GUARD for GraphRAG Context Expansion in AgenticRAG (Realm 2).

    Locks down:
        1. expand_graph_context traverses memory graph from core memory hits
        2. Connected 1-hop and 2-hop concepts are integrated into retrieval contexts
        3. Path traversal weights decay context scores appropriately
        4. Graph expansion can be disabled via enable_graph_expansion=False
        5. Deduplication prevents re-adding concepts already in contexts
        6. End-to-end synthesis integrates graph-expanded context
    """

    def setUp(self):
        _clear_rag_inrun_cache()
        self.engine = AgenticRAGEngine(
            max_hops=2, top_k_per_source=3, enable_graph_expansion=True, graph_hops=2
        )

    @patch("agentic_rag.memory_vault.traverse_subgraph")
    def test_expand_graph_context_direct(self, mock_traverse):
        """Verify expand_graph_context fetches 1-hop and 2-hop connected concepts."""
        mock_traverse.return_value = {
            "start_node_id": 1,
            "nodes": [
                {"id": 1, "content": "Core Docker", "hop": 0, "path_weight": 1.0},
                {"id": 2, "content": "Containerd Runtime", "hop": 1, "path_weight": 0.8},
                {"id": 3, "content": "Runc CLI", "hop": 2, "path_weight": 0.4},
            ],
            "links": [],
            "traversal_weights": {1: 1.0, 2: 0.8, 3: 0.4},
            "paths": {1: [1], 2: [1, 2], 3: [1, 2, 3]},
        }

        contexts = [{"id": 1, "source": "core_memory", "content": "Core Docker", "score": 0.9}]
        expanded = self.engine.expand_graph_context(contexts, max_hops=2)

        self.assertEqual(len(expanded), 2)
        # Node 1 itself is excluded
        expanded_ids = [c["id"] for c in expanded]
        self.assertNotIn(1, expanded_ids)
        self.assertIn(2, expanded_ids)
        self.assertIn(3, expanded_ids)

        # Check sources and decayed scores
        c2 = next(c for c in expanded if c["id"] == 2)
        self.assertEqual(c2["source"], "graph_1hop")
        self.assertAlmostEqual(c2["score"], 0.9 * 0.8, places=2)

        c3 = next(c for c in expanded if c["id"] == 3)
        self.assertEqual(c3["source"], "graph_2hop")
        self.assertAlmostEqual(c3["score"], 0.9 * 0.4, places=2)

    @patch("agentic_rag.memory_vault.traverse_subgraph")
    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_for_integrates_graph_expansion(
        self, mock_search_epi, mock_semantic, mock_traverse
    ):
        """Verify _retrieve_for enriches core memory search with graph traversal results."""
        mock_semantic.return_value = [
            (10, "fact", "Quantum Computing Principles", 0.95),
        ]
        mock_search_epi.return_value = []
        mock_traverse.return_value = {
            "start_node_id": 10,
            "nodes": [
                {"id": 10, "content": "Quantum Computing Principles", "hop": 0, "path_weight": 1.0},
                {"id": 20, "content": "Qubit Superposition Concept", "hop": 1, "path_weight": 0.85},
            ],
            "links": [],
            "traversal_weights": {10: 1.0, 20: 0.85},
            "paths": {},
        }

        results = self.engine._retrieve_for("Quantum?", existing_contexts=[])
        self.assertEqual(len(results), 2)

        sources = [r["source"] for r in results]
        self.assertIn("core_memory", sources)
        self.assertIn("graph_1hop", sources)

    @patch("agentic_rag.memory_vault.traverse_subgraph")
    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    def test_retrieve_for_respects_graph_expansion_disabled(
        self, mock_search_epi, mock_semantic, mock_traverse
    ):
        """Verify graph expansion is omitted when enable_graph_expansion is False."""
        engine_no_graph = AgenticRAGEngine(
            max_hops=2, top_k_per_source=3, enable_graph_expansion=False
        )
        mock_semantic.return_value = [
            (10, "fact", "Quantum Computing Principles", 0.95),
        ]
        mock_search_epi.return_value = []

        results = engine_no_graph._retrieve_for("Quantum?", existing_contexts=[])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "core_memory")
        mock_traverse.assert_not_called()

    @patch("agentic_rag.memory_vault.traverse_subgraph")
    def test_expand_graph_context_deduplication(self, mock_traverse):
        """Verify concepts already present in existing contexts are not duplicated."""
        mock_traverse.return_value = {
            "start_node_id": 1,
            "nodes": [
                {"id": 2, "content": "Already Known Information", "hop": 1, "path_weight": 0.8},
            ],
            "links": [],
            "traversal_weights": {2: 0.8},
            "paths": {},
        }

        existing = [
            {"id": 1, "source": "core_memory", "content": "Primary concept", "score": 0.9},
            {"id": 99, "source": "episodic", "content": "Already Known Information", "score": 0.7},
        ]
        expanded = self.engine.expand_graph_context(existing)
        self.assertEqual(len(expanded), 0)

    @patch("agentic_rag.get_provider")
    @patch("agentic_rag.memory_vault.traverse_subgraph")
    @patch("agentic_rag.memory_vault.semantic_search")
    @patch("agentic_rag.memory_vault.search_episodic")
    @patch("agentic_rag.memory_vault.get_current_session_id")
    def test_full_pipeline_answer_synthesizes_with_graph_context(
        self, mock_get_sid, mock_search_epi, mock_semantic, mock_traverse, mock_get_prov
    ):
        """End-to-end test verifying answer() expands graph context and synthesizes answer."""
        mock_get_sid.return_value = "session_test"
        mock_semantic.return_value = [
            (1, "fact", "SQLite is an embedded database.", 0.9),
        ]
        mock_search_epi.return_value = []
        mock_traverse.return_value = {
            "start_node_id": 1,
            "nodes": [
                {"id": 2, "content": "WAL mode enables concurrent reads.", "hop": 1, "path_weight": 0.85},
            ],
            "links": [],
            "traversal_weights": {2: 0.85},
            "paths": {},
        }

        mock_provider = MagicMock()
        mock_provider.chat_completion.side_effect = [
            # 1. Decomposition
            {"content": '["What is SQLite?"]', "error": None},
            # 2. Gap analysis (sufficient)
            {"content": json.dumps({"sufficient": True, "gaps": []}), "error": None},
            # 3. Synthesis
            {"content": "SQLite is embedded and WAL enables concurrent reads.", "error": None},
        ]
        mock_get_prov.return_value = mock_provider

        result = self.engine.answer("What is SQLite concurrency?")
        self.assertEqual(len(result["contexts"]), 2)
        contents = [c["content"] for c in result["contexts"]]
        self.assertIn("SQLite is an embedded database.", contents)
        self.assertIn("WAL mode enables concurrent reads.", contents)
        self.assertIn("WAL", result["answer"])

    @patch("agentic_rag.memory_vault.traverse_subgraph")
    def test_expand_graph_context_with_concept_node_type_and_relation_annotation(self, mock_traverse):
        """ANTI-FRAGILITY: Contexts with node_type='concept' are expanded and annotated with relation_type."""
        mock_traverse.return_value = {
            "start_node_id": 42,
            "nodes": [
                {"id": 42, "content": "FastAPI", "hop": 0, "path_weight": 1.0},
                {"id": 99, "content": "Starlette", "hop": 1, "path_weight": 0.9},
            ],
            "links": [
                {"source_id": 42, "target_id": 99, "relation_type": "DEPENDS_ON", "weight": 0.9},
            ],
            "traversal_weights": {42: 1.0, 99: 0.9},
            "paths": {42: [42], 99: [42, 99]},
        }

        # Concept node from semantic search
        contexts = [{"id": 42, "source": "concept", "content": "FastAPI", "score": 0.88}]
        expanded = self.engine.expand_graph_context(contexts)

        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0]["id"], 99)
        self.assertEqual(expanded[0]["relation_type"], "DEPENDS_ON")
        self.assertEqual(expanded[0]["source"], "graph_1hop")
        self.assertAlmostEqual(expanded[0]["score"], 0.88 * 0.9, places=2)


if __name__ == "__main__":
    unittest.main()

