"""test_sprint13_writer.py - Sprint 13: GraphRAG entity storage, relationships,
graph traversal, graph stats, and regex extraction tests.

Fills coverage gaps not covered by test_sprint13.py or test_sprint13_hybrid_search.py.

Run: python -m pytest tests/test_sprint13_writer.py -v
"""
import os
import sys
import tempfile
import unittest

# Embedding-load gate handled by conftest.py pytest_configure() which sets
# KOKERTECH_SKIP_EMBEDDINGS=1 before collection begins.
import memory_vault


class Sprint13TestCase(unittest.TestCase):
    """Base class that swaps in a temp SQLite DB for test isolation."""

    def setUp(self):
        self._orig_db = memory_vault.DB_PATH
        self._orig_db_init = memory_vault._db_initialized
        fd, self._tmp_db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        memory_vault.DB_PATH = self._tmp_db
        memory_vault._db_initialized = False
        # Clear stale pooled connections that may still point to the old DB
        memory_vault._clear_connection_pool()
        memory_vault.ensure_tables_exist()

    def tearDown(self):
        memory_vault.DB_PATH = self._orig_db
        memory_vault._db_initialized = self._orig_db_init
        memory_vault._clear_connection_pool()
        for suffix in ("", "-wal", "-shm"):
            try:
                path = self._tmp_db + suffix
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Entity storage (store_entity)
# ---------------------------------------------------------------------------

class TestStoreEntity(Sprint13TestCase):
    def test_insert_new_entity(self):
        eid = memory_vault.store_entity("Python", "technology", "A programming language")
        self.assertGreater(eid, 0)

    def test_upsert_increments_mention_count(self):
        eid1 = memory_vault.store_entity("Docker", "technology")
        eid2 = memory_vault.store_entity("Docker", "technology")
        self.assertEqual(eid1, eid2)
        entity = memory_vault.get_entity(name="Docker")
        self.assertGreaterEqual(entity["mention_count"], 2)

    def test_different_types_create_separate_entities(self):
        eid1 = memory_vault.store_entity("Python", "technology")
        eid2 = memory_vault.store_entity("Python", "concept")
        self.assertNotEqual(eid1, eid2)

    def test_empty_name_returns_negative(self):
        self.assertEqual(memory_vault.store_entity("", "technology"), -1)

    def test_none_name_returns_negative(self):
        self.assertEqual(memory_vault.store_entity(None, "technology"), -1)

    def test_strips_whitespace(self):
        memory_vault.store_entity("  Docker  ", "technology")
        entity = memory_vault.get_entity(name="Docker")
        self.assertIsNotNone(entity)

    def test_entity_embedding_when_disabled(self):
        """Entity embedding is skipped when graphrag_entity_embeddings is False."""
        old_val = memory_vault.CONFIG.get("graphrag_entity_embeddings", False)
        try:
            memory_vault.CONFIG["graphrag_entity_embeddings"] = False
            memory_vault.store_entity("TestEmbed", "concept")
            entity = memory_vault.get_entity(name="TestEmbed")
            self.assertIsNone(entity.get("embedding"))
        finally:
            memory_vault.CONFIG["graphrag_entity_embeddings"] = old_val

    def test_entity_embedding_update_preserves_prior_when_flag_disabled(self):
        """REGRESSION GUARD (2026-09-22): store_entity() on an EXISTING entity
        with embeddings disabled must preserve the prior embedding, not
        overwrite it with NULL. Lost in the Sprint 13 patch merge alongside
        the flag guard (see memory_vault.store_entity comment).

        _get_embedding is stubbed (deterministic 384-dim vector) because the
        native model is unavailable in tests — same pattern as
        test_vault_isolation_guard.py.
        """
        import numpy as np
        from unittest.mock import patch as mock_patch

        def fake_embedding(text):
            rng = np.random.default_rng(abs(hash(text)) % (2**32))
            return rng.standard_normal(384).astype(np.float32)

        old_val = memory_vault.CONFIG.get("graphrag_entity_embeddings", False)
        try:
            with mock_patch.object(memory_vault, "_get_embedding",
                                   side_effect=fake_embedding):
                memory_vault.CONFIG["graphrag_entity_embeddings"] = True
                memory_vault.store_entity("TestEmbedKeep", "concept")
                with_emb = memory_vault.get_entity(name="TestEmbedKeep")
                self.assertIsNotNone(with_emb.get("embedding"))
                memory_vault.CONFIG["graphrag_entity_embeddings"] = False
                memory_vault.store_entity("TestEmbedKeep", "concept")  # UPDATE path
                entity = memory_vault.get_entity(name="TestEmbedKeep")
                self.assertIsNotNone(
                    entity.get("embedding"),
                    "UPDATE path clobbered a prior embedding when the flag was off",
                )
        finally:
            memory_vault.CONFIG["graphrag_entity_embeddings"] = old_val


# ---------------------------------------------------------------------------
# Relationship storage (store_relationship)
# ---------------------------------------------------------------------------

class TestStoreRelationship(Sprint13TestCase):
    def setUp(self):
        super().setUp()
        self.eid_a = memory_vault.store_entity("Alpha", "concept")
        self.eid_b = memory_vault.store_entity("Beta", "concept")
        self.eid_c = memory_vault.store_entity("Gamma", "concept")

    def test_store_new_relationship(self):
        rid = memory_vault.store_relationship(
            self.eid_a, self.eid_b, "RELATED_TO", confidence=0.9, evidence="test"
        )
        self.assertGreater(rid, 0)

    def test_duplicate_relationship_strengthens_confidence(self):
        """Duplicate relationship should strengthen confidence (capped at 1.0).
        Regression test for BUG #2: removed non-existent valid_from column."""
        rid1 = memory_vault.store_relationship(
            self.eid_a, self.eid_b, "RELATED_TO", confidence=0.5
        )
        rid2 = memory_vault.store_relationship(
            self.eid_a, self.eid_b, "RELATED_TO", confidence=0.5
        )
        self.assertEqual(rid1, rid2)
        # Confidence should be strengthened, capped at 1.0
        rels = memory_vault.get_entity_relationships(self.eid_a, direction="outgoing")
        self.assertTrue(any(r["confidence"] > 0.5 for r in rels))

    def test_different_rel_types_are_separate(self):
        rid1 = memory_vault.store_relationship(
            self.eid_a, self.eid_b, "RELATED_TO", confidence=0.8
        )
        rid2 = memory_vault.store_relationship(
            self.eid_a, self.eid_b, "DEPENDS_ON", confidence=0.7
        )
        self.assertNotEqual(rid1, rid2)

    def test_default_relationship_type(self):
        rid = memory_vault.store_relationship(
            self.eid_a, self.eid_c, None
        )
        self.assertGreater(rid, 0)
        rels = memory_vault.get_entity_relationships(self.eid_a, direction="outgoing")
        self.assertTrue(any(r["relationship_type"] == "RELATES_TO" for r in rels))


# ---------------------------------------------------------------------------
# Entity retrieval (get_entity)
# ---------------------------------------------------------------------------

class TestGetEntity(Sprint13TestCase):
    def test_get_by_id(self):
        eid = memory_vault.store_entity("ById", "concept")
        entity = memory_vault.get_entity(entity_id=eid)
        self.assertIsNotNone(entity)
        self.assertEqual(entity["name"], "ById")

    def test_get_by_name(self):
        memory_vault.store_entity("ByName", "concept")
        entity = memory_vault.get_entity(name="ByName")
        self.assertIsNotNone(entity)
        self.assertEqual(entity["entity_type"], "concept")

    def test_get_by_name_case_insensitive(self):
        memory_vault.store_entity("CaseTest", "concept")
        entity = memory_vault.get_entity(name="casetest")
        self.assertIsNotNone(entity)

    def test_get_nonexistent_returns_none(self):
        self.assertIsNone(memory_vault.get_entity(name="NonexistentXYZ"))

    def test_get_no_args_returns_none(self):
        self.assertIsNone(memory_vault.get_entity())


# ---------------------------------------------------------------------------
# Entity search (search_entities)
# ---------------------------------------------------------------------------

class TestSearchEntities(Sprint13TestCase):
    def setUp(self):
        super().setUp()
        memory_vault.store_entity("PyTorch", "technology")
        memory_vault.store_entity("TensorFlow", "technology")
        memory_vault.store_entity("Python", "technology")
        memory_vault.store_entity("FastAPI", "framework")

    def test_search_by_name_substring(self):
        results = memory_vault.search_entities("Pyt")
        names = [r["name"] for r in results]
        self.assertTrue(any("Pyt" in n for n in names))

    def test_search_by_type(self):
        results = memory_vault.search_entities("", entity_type="framework")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "FastAPI")

    def test_search_all(self):
        results = memory_vault.search_entities("")
        self.assertGreaterEqual(len(results), 4)

    def test_search_no_match(self):
        results = memory_vault.search_entities("zzzz_nonexistent_zzzz")
        self.assertEqual(len(results), 0)


# ---------------------------------------------------------------------------
# Entity relationships (get_entity_relationships)
# ---------------------------------------------------------------------------

class TestGetEntityRelationships(Sprint13TestCase):
    def setUp(self):
        super().setUp()
        self.eid_a = memory_vault.store_entity("NodeA", "concept")
        self.eid_b = memory_vault.store_entity("NodeB", "concept")
        self.eid_c = memory_vault.store_entity("NodeC", "concept")
        memory_vault.store_relationship(self.eid_a, self.eid_b, "RELATED_TO")
        memory_vault.store_relationship(self.eid_b, self.eid_c, "DEPENDS_ON")
        memory_vault.store_relationship(self.eid_c, self.eid_a, "IMPLEMENTS")

    def test_outgoing_only(self):
        rels = memory_vault.get_entity_relationships(self.eid_a, direction="outgoing")
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["target_name"], "NodeB")

    def test_incoming_only(self):
        rels = memory_vault.get_entity_relationships(self.eid_a, direction="incoming")
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["source_name"], "NodeC")

    def test_both_directions(self):
        rels = memory_vault.get_entity_relationships(self.eid_a, direction="both")
        self.assertEqual(len(rels), 2)

    def test_filter_by_rel_type(self):
        rels = memory_vault.get_entity_relationships(
            self.eid_a, direction="both", rel_type="RELATED_TO"
        )
        self.assertEqual(len(rels), 1)

    def test_no_relationships(self):
        eid_isolated = memory_vault.store_entity("Isolated", "concept")
        rels = memory_vault.get_entity_relationships(eid_isolated, direction="both")
        self.assertEqual(len(rels), 0)


# ---------------------------------------------------------------------------
# Graph traversal (graph_traverse)
# ---------------------------------------------------------------------------

class TestGraphTraverse(Sprint13TestCase):
    def setUp(self):
        super().setUp()
        # Build a small graph: A -> B -> C
        self.eid_a = memory_vault.store_entity("TravA", "concept")
        self.eid_b = memory_vault.store_entity("TravB", "concept")
        self.eid_c = memory_vault.store_entity("TravC", "concept")
        memory_vault.store_relationship(self.eid_a, self.eid_b, "LINKS_TO")
        memory_vault.store_relationship(self.eid_b, self.eid_c, "LINKS_TO")

    def test_traverse_depth_1(self):
        result = memory_vault.graph_traverse(self.eid_a, depth=1)
        entity_names = [e["name"] for e in result["entities"]]
        self.assertIn("TravA", entity_names)
        self.assertIn("TravB", entity_names)
        self.assertNotIn("TravC", entity_names)

    def test_traverse_depth_2(self):
        result = memory_vault.graph_traverse(self.eid_a, depth=2)
        entity_names = [e["name"] for e in result["entities"]]
        self.assertIn("TravA", entity_names)
        self.assertIn("TravB", entity_names)
        self.assertIn("TravC", entity_names)

    def test_traverse_returns_relationships(self):
        result = memory_vault.graph_traverse(self.eid_a, depth=2)
        self.assertGreater(len(result["relationships"]), 0)

    def test_traverse_empty_start(self):
        result = memory_vault.graph_traverse(99999, depth=1)
        self.assertEqual(len(result["entities"]), 0)

    def test_traverse_with_rel_type_filter(self):
        result = memory_vault.graph_traverse(self.eid_a, depth=2, rel_type="LINKS_TO")
        self.assertGreater(len(result["relationships"]), 0)

    def test_traverse_with_wrong_rel_type_returns_no_rels(self):
        result = memory_vault.graph_traverse(self.eid_a, depth=2, rel_type="NONEXISTENT")
        self.assertEqual(len(result["relationships"]), 0)


# ---------------------------------------------------------------------------
# Graph stats (get_graph_stats)
# ---------------------------------------------------------------------------

class TestGraphStats(Sprint13TestCase):
    def test_empty_graph(self):
        stats = memory_vault.get_graph_stats()
        self.assertEqual(stats["entity_count"], 0)
        self.assertEqual(stats["relationship_count"], 0)

    def test_populated_graph(self):
        eid1 = memory_vault.store_entity("StatA", "concept")
        eid2 = memory_vault.store_entity("StatB", "technology")
        memory_vault.store_relationship(eid1, eid2, "USES")
        stats = memory_vault.get_graph_stats()
        self.assertEqual(stats["entity_count"], 2)
        self.assertEqual(stats["relationship_count"], 1)
        self.assertIn("concept", stats["entity_types"])
        self.assertIn("USES", stats["relationship_types"])


# ---------------------------------------------------------------------------
# Regex entity extraction (extract_entities_regex)
# ---------------------------------------------------------------------------

class TestExtractEntitiesRegex(Sprint13TestCase):
    def test_person_entity(self):
        entities = memory_vault.extract_entities_regex("Jacques built the system.")
        names = [e["name"] for e in entities]
        self.assertIn("Jacques", names)

    def test_quoted_concept(self):
        entities = memory_vault.extract_entities_regex('We discussed "hybrid search" today.')
        names = [e["name"] for e in entities]
        self.assertIn("hybrid search", names)

    def test_date_entity(self):
        entities = memory_vault.extract_entities_regex("Released on 2026-06-24.")
        dates = [e["name"] for e in entities if e["entity_type"] == "date"]
        self.assertIn("2026-06-24", dates)

    def test_url_entity(self):
        entities = memory_vault.extract_entities_regex("Visit https://example.com for info.")
        urls = [e["name"] for e in entities if e["entity_type"] == "url"]
        self.assertTrue(len(urls) > 0)

    def test_empty_input(self):
        self.assertEqual(memory_vault.extract_entities_regex(""), [])
        self.assertEqual(memory_vault.extract_entities_regex(None), [])

    def test_no_entities(self):
        entities = memory_vault.extract_entities_regex("the cat sat on the mat")
        # Should not extract false-positive person entities for common words
        names = [e["name"] for e in entities if e["entity_type"] == "person"]
        self.assertEqual(len(names), 0)

    def test_deduplication(self):
        entities = memory_vault.extract_entities_regex("Python is great. Python is awesome.")
        python_entries = [e for e in entities if e["name"] == "Python"]
        self.assertEqual(len(python_entries), 1)


# ---------------------------------------------------------------------------
# Regex relationship extraction (extract_relationships_regex)
# ---------------------------------------------------------------------------

class TestExtractRelationshipsRegex(Sprint13TestCase):
    def test_is_a_relationship(self):
        rels = memory_vault.extract_relationships_regex(
            "Python is a programming language."
        )
        self.assertTrue(len(rels) > 0)
        self.assertEqual(rels[0]["rel_type"], "IS_A")

    def test_has_relationship(self):
        rels = memory_vault.extract_relationships_regex(
            "Python has extensive library support."
        )
        self.assertTrue(len(rels) > 0)
        self.assertEqual(rels[0]["rel_type"], "HAS")

    def test_uses_relationship(self):
        rels = memory_vault.extract_relationships_regex(
            "Kokertech uses Docker for sandboxing."
        )
        self.assertTrue(len(rels) > 0)
        self.assertEqual(rels[0]["rel_type"], "USES")

    def test_empty_input(self):
        self.assertEqual(memory_vault.extract_relationships_regex(""), [])
        self.assertEqual(memory_vault.extract_relationships_regex(None), [])

    def test_no_match(self):
        rels = memory_vault.extract_relationships_regex("The sky is blue.")
        self.assertEqual(len(rels), 0)


# ---------------------------------------------------------------------------
# End-to-end graph extraction (extract_and_store_graph)
# ---------------------------------------------------------------------------

class TestExtractAndStoreGraph(Sprint13TestCase):
    def test_basic_extraction(self):
        result = memory_vault.extract_and_store_graph(
            "Jacques built the Kokertech system using Python.",
            use_llm=False,
        )
        self.assertIn("entities_found", result)
        self.assertIn("relationships_found", result)
        self.assertGreaterEqual(result["entities_found"], 1)

    def test_empty_text(self):
        result = memory_vault.extract_and_store_graph("", use_llm=False)
        self.assertEqual(result["entities_found"], 0)

    def test_none_text(self):
        result = memory_vault.extract_and_store_graph(None, use_llm=False)
        self.assertEqual(result["entities_found"], 0)

    def test_stores_entities_in_graph(self):
        memory_vault.extract_and_store_graph(
            "Docker provides containerization for Python.",
            use_llm=False,
        )
        stats = memory_vault.get_graph_stats()
        self.assertGreater(stats["entity_count"], 0)

    def test_disabled_graphrag(self):
        from unittest.mock import patch
        with patch("memory_vault._GRAPHRAG_ENABLED", False):
            result = memory_vault.extract_and_store_graph(
                "Test text.", use_llm=False
            )
            self.assertEqual(result["entities_found"], 0)


# ---------------------------------------------------------------------------
# Sprint 13 API function wiring: fts_snippet, export/import_graph, detect_communities
# ---------------------------------------------------------------------------

class TestSprint13Wiring(Sprint13TestCase):
    """Unit tests for Sprint 13 API functions wired into live code paths.

    Exercises fts_snippet context extraction, export_graph/import_graph
    round-trip fidelity, and detect_communities cluster detection.
    """

    # -- fts_snippet -----------------------------------------------------------

    def test_fts_snippet_returns_matching_context(self):
        """fts_snippet should return text surrounding the query match."""
        text = "Python is a popular programming language used for AI and data science."
        snippet = memory_vault.fts_snippet(text, "Python")
        self.assertIn("Python", snippet)
        self.assertLessEqual(len(snippet), memory_vault.CONFIG.get('snippet_max_chars', 200))

    def test_fts_snippet_empty_text(self):
        """fts_snippet returns empty string for empty text."""
        self.assertEqual(memory_vault.fts_snippet("", "query"), "")
        self.assertEqual(memory_vault.fts_snippet(None, "query"), "")

    def test_fts_snippet_empty_query(self):
        """fts_snippet returns truncated text when query is empty."""
        text = "A" * 300
        snippet = memory_vault.fts_snippet(text, "")
        self.assertTrue(len(snippet) > 0)
        self.assertLessEqual(len(snippet), 200)

    def test_fts_snippet_no_match_falls_back_to_first_word(self):
        """fts_snippet falls back to first word of query when exact match not found."""
        text = "The quick brown fox jumps over the lazy dog."
        snippet = memory_vault.fts_snippet(text, "brown fox")
        self.assertIn("brown", snippet)

    def test_fts_snippet_max_chars_respected(self):
        """fts_snippet respects max_chars parameter."""
        text = "word " * 200  # long text
        snippet = memory_vault.fts_snippet(text, "word", max_chars=50)
        self.assertLessEqual(len(snippet), 50)

    def test_fts_snippet_adds_ellipsis(self):
        """fts_snippet adds '...' prefix/suffix when snippet is mid-text."""
        text = "Start. " + "middle content " * 20 + "End."
        snippet = memory_vault.fts_snippet(text, "middle")
        # If text is long enough, snippet should be truncated with ellipsis
        if len(text) > memory_vault.CONFIG.get('snippet_max_chars', 200):
            self.assertTrue('...' in snippet)

    # -- export_graph / import_graph round-trip ---------------------------------

    def test_export_graph_empty(self):
        """export_graph on empty DB returns empty lists."""
        data = memory_vault.export_graph()
        self.assertEqual(data['entity_count'], 0)
        self.assertEqual(data['relationship_count'], 0)
        self.assertEqual(len(data['entities']), 0)
        self.assertEqual(len(data['relationships']), 0)

    def test_export_graph_has_format_metadata(self):
        """export_graph includes format and timestamp metadata."""
        data = memory_vault.export_graph()
        self.assertEqual(data['format'], 'kokertech-kg-v1')
        self.assertIn('exported_at', data)

    def test_export_graph_populated(self):
        """export_graph captures entities and relationships."""
        eid1 = memory_vault.store_entity("ExpA", "concept")
        eid2 = memory_vault.store_entity("ExpB", "technology")
        memory_vault.store_relationship(eid1, eid2, "USES", confidence=0.8)
        data = memory_vault.export_graph()
        self.assertEqual(data['entity_count'], 2)
        self.assertEqual(data['relationship_count'], 1)
        names = [e['name'] for e in data['entities']]
        self.assertIn("ExpA", names)
        self.assertIn("ExpB", names)

    def test_export_graph_excludes_embeddings(self):
        """export_graph strips embedding blobs from entity dicts."""
        memory_vault.store_entity("NoEmb", "concept")
        data = memory_vault.export_graph()
        for e in data['entities']:
            self.assertNotIn('embedding', e)

    def test_import_graph_empty_data(self):
        """import_graph with None/empty data returns zero counts."""
        result = memory_vault.import_graph(None)
        self.assertEqual(result['entities_imported'], 0)
        self.assertEqual(result['relationships_imported'], 0)

    def test_import_graph_entities_only(self):
        """import_graph correctly imports entities without relationships."""
        data = {
            'entities': [
                {'name': 'ImportA', 'entity_type': 'concept'},
                {'name': 'ImportB', 'entity_type': 'technology'},
            ],
            'relationships': [],
        }
        result = memory_vault.import_graph(data)
        self.assertEqual(result['entities_imported'], 2)
        self.assertEqual(result['relationships_imported'], 0)
        entity = memory_vault.get_entity(name='ImportA')
        self.assertIsNotNone(entity)

    def test_import_export_round_trip(self):
        """export then import preserves entity names and relationship count."""
        # Set up source graph
        eid1 = memory_vault.store_entity("RoundTrip_A", "concept")
        eid2 = memory_vault.store_entity("RoundTrip_B", "concept")
        memory_vault.store_relationship(eid1, eid2, "LINKS_TO", confidence=0.9)
        # Export
        exported = memory_vault.export_graph()
        self.assertEqual(exported['entity_count'], 2)
        self.assertEqual(exported['relationship_count'], 1)
        # Import into a fresh DB
        fd, tmp2 = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        old_db = memory_vault.DB_PATH
        try:
            memory_vault.DB_PATH = tmp2
            memory_vault._db_initialized = False
            memory_vault._clear_connection_pool()
            memory_vault.ensure_tables_exist()
            result = memory_vault.import_graph(exported)
            self.assertEqual(result['entities_imported'], 2)
            self.assertEqual(result['relationships_imported'], 1)
            # Verify imported data
            imported = memory_vault.export_graph()
            self.assertEqual(imported['entity_count'], 2)
            self.assertEqual(imported['relationship_count'], 1)
            imp_names = {e['name'] for e in imported['entities']}
            self.assertIn("RoundTrip_A", imp_names)
            self.assertIn("RoundTrip_B", imp_names)
        finally:
            memory_vault.DB_PATH = old_db
            memory_vault._db_initialized = False
            memory_vault._clear_connection_pool()
            memory_vault.ensure_tables_exist()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(tmp2 + suffix)
                except OSError:
                    pass

    def test_import_graph_relationships_wired_correctly(self):
        """import_graph maps old entity IDs to new ones in relationships."""
        data = {
            'entities': [
                {'id': 100, 'name': 'SrcEntity', 'entity_type': 'concept'},
                {'id': 200, 'name': 'TgtEntity', 'entity_type': 'concept'},
            ],
            'relationships': [
                {
                    'source_entity_id': 100,
                    'target_entity_id': 200,
                    'relationship_type': 'DEPENDS_ON',
                    'confidence': 0.7,
                },
            ],
        }
        result = memory_vault.import_graph(data)
        self.assertEqual(result['entities_imported'], 2)
        self.assertEqual(result['relationships_imported'], 1)
        src = memory_vault.get_entity(name='SrcEntity')
        tgt = memory_vault.get_entity(name='TgtEntity')
        rels = memory_vault.get_entity_relationships(src['id'], direction='outgoing')
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]['target_name'], 'TgtEntity')
        self.assertEqual(rels[0]['relationship_type'], 'DEPENDS_ON')

    # -- detect_communities -----------------------------------------------------

    def test_detect_communities_empty_graph(self):
        """detect_communities on empty graph returns empty list."""
        communities = memory_vault.detect_communities()
        self.assertEqual(communities, [])

    def test_detect_communities_small_cluster(self):
        """detect_communities finds a cluster of 3+ connected entities."""
        # Create a tightly connected cluster
        e1 = memory_vault.store_entity("CommA", "concept")
        e2 = memory_vault.store_entity("CommB", "concept")
        e3 = memory_vault.store_entity("CommC", "concept")
        e4 = memory_vault.store_entity("CommD", "concept")
        memory_vault.store_relationship(e1, e2, "LINKED")
        memory_vault.store_relationship(e2, e3, "LINKED")
        memory_vault.store_relationship(e3, e4, "LINKED")
        memory_vault.store_relationship(e1, e4, "LINKED")
        communities = memory_vault.detect_communities(min_cluster_size=3)
        self.assertGreaterEqual(len(communities), 1)
        # The cluster should contain all 4 entities
        all_members = []
        for c in communities:
            all_members.extend(c['members'])
        self.assertIn(e1, all_members)
        self.assertIn(e2, all_members)
        self.assertIn(e3, all_members)
        self.assertIn(e4, all_members)

    def test_detect_communities_below_threshold_filtered(self):
        """detect_communities filters out clusters smaller than min_cluster_size."""
        e1 = memory_vault.store_entity("SmallA", "concept")
        e2 = memory_vault.store_entity("SmallB", "concept")
        # Only 2 nodes — below default threshold of 3
        memory_vault.store_relationship(e1, e2, "LINKED")
        communities = memory_vault.detect_communities(min_cluster_size=3)
        self.assertEqual(len(communities), 0)

    def test_detect_communities_returns_entity_details(self):
        """detect_communities enriches community members with entity details."""
        e1 = memory_vault.store_entity("DetailA", "technology")
        e2 = memory_vault.store_entity("DetailB", "technology")
        e3 = memory_vault.store_entity("DetailC", "technology")
        memory_vault.store_relationship(e1, e2, "USES")
        memory_vault.store_relationship(e2, e3, "USES")
        communities = memory_vault.detect_communities(min_cluster_size=3)
        self.assertGreaterEqual(len(communities), 1)
        comm = communities[0]
        self.assertIn('entities', comm)
        self.assertGreaterEqual(len(comm['entities']), 3)
        entity_names = {e['name'] for e in comm['entities']}
        self.assertIn('DetailA', entity_names)
        self.assertIn('DetailB', entity_names)
        self.assertIn('DetailC', entity_names)

    def test_detect_communities_multiple_clusters(self):
        """detect_communities identifies separate clusters."""
        # Cluster 1: A-B-C
        a1 = memory_vault.store_entity("Multi1", "concept")
        b1 = memory_vault.store_entity("Multi2", "concept")
        c1 = memory_vault.store_entity("Multi3", "concept")
        memory_vault.store_relationship(a1, b1, "LINKED")
        memory_vault.store_relationship(b1, c1, "LINKED")
        memory_vault.store_relationship(a1, c1, "LINKED")
        # Cluster 2: D-E-F
        d1 = memory_vault.store_entity("Multi4", "concept")
        e1 = memory_vault.store_entity("Multi5", "concept")
        f1 = memory_vault.store_entity("Multi6", "concept")
        memory_vault.store_relationship(d1, e1, "LINKED")
        memory_vault.store_relationship(e1, f1, "LINKED")
        memory_vault.store_relationship(d1, f1, "LINKED")
        communities = memory_vault.detect_communities(min_cluster_size=3)
        self.assertGreaterEqual(len(communities), 2)
        sizes = sorted([c['size'] for c in communities])
        self.assertEqual(sizes[-1], 3)  # each cluster has 3 members
        self.assertEqual(sizes[-2], 3)

    def test_detect_communities_id_and_size_fields(self):
        """detect_communities returns dict with id, members, size, entities keys."""
        e1 = memory_vault.store_entity("Fields1", "concept")
        e2 = memory_vault.store_entity("Fields2", "concept")
        e3 = memory_vault.store_entity("Fields3", "concept")
        memory_vault.store_relationship(e1, e2, "LINKED")
        memory_vault.store_relationship(e2, e3, "LINKED")
        memory_vault.store_relationship(e1, e3, "LINKED")
        communities = memory_vault.detect_communities(min_cluster_size=3)
        self.assertGreaterEqual(len(communities), 1)
        comm = communities[0]
        self.assertIn('id', comm)
        self.assertIn('members', comm)
        self.assertIn('size', comm)
        self.assertIn('entities', comm)
        self.assertEqual(comm['size'], len(comm['members']))


if __name__ == "__main__":
    unittest.main()
