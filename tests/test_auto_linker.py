"""Tests for auto_linker.py - semantic auto-link suggestions."""

import unittest
from unittest.mock import patch, MagicMock
import numpy as np

from auto_linker import (
    get_unlinked_node_ids,
    suggest_links,
    apply_suggestions,
    infer_relationship_type,
    suggest_semantic_triples,
)



class TestGetUnlinkedNodeIds(unittest.TestCase):
    """get_unlinked_node_ids - DB query for nodes and linked pairs."""
    def setUp(self):
        self.node_rows = [
            {"id": 1, "content": "First memory"},
            {"id": 2, "content": "Second memory"},
            {"id": 3, "content": "Third memory"},
        ]
        self.link_rows = [
            {"source_id": 1, "target_id": 2},
        ]
        self.ensure_tables_patch = patch("auto_linker.ensure_tables_exist")
        self.ensure_tables_patch.start()
        self.addCleanup(self.ensure_tables_patch.stop)
    def _mock_db(self, node_rows=None, link_rows=None):
        node_cursor = MagicMock()
        node_cursor.fetchall.return_value = node_rows or []
        link_cursor = MagicMock()
        link_cursor.fetchall.return_value = link_rows or []
        conn = MagicMock()
        def execute_side_effect(sql):
            if "core_memories" in sql:
                return node_cursor
            return link_cursor
        conn.execute.side_effect = execute_side_effect
        return patch("auto_linker._get_conn", return_value=conn)
    def test_returns_all_ids_and_content(self):
        with self._mock_db(self.node_rows, self.link_rows):
            ids, content, linked = get_unlinked_node_ids()
        self.assertEqual(len(ids), 3)
        self.assertIn(1, ids)
        self.assertEqual(content[1], "First memory")
    def test_linked_pairs_set(self):
        with self._mock_db(self.node_rows, self.link_rows):
            ids, content, linked = get_unlinked_node_ids()
        self.assertIn((1, 2), linked)
        self.assertNotIn((2, 1), linked)
        self.assertNotIn((1, 3), linked)
    def test_empty_db_returns_empty(self):
        with self._mock_db():
            ids, content, linked = get_unlinked_node_ids()
        self.assertEqual(ids, [])
        self.assertEqual(content, {})
        self.assertEqual(linked, set())
    def test_no_links_returns_empty_linked_set(self):
        with self._mock_db(self.node_rows, []):
            ids, content, linked = get_unlinked_node_ids()
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(linked), 0)

class TestApplySuggestions(unittest.TestCase):
    """apply_suggestions - auto-apply link suggestions to DB."""
    def setUp(self):
        self.suggestions = [
            {"source_id": 1, "target_id": 3, "score": 0.95},
            {"source_id": 2, "target_id": 4, "score": 0.85},
        ]
    def test_applies_all_suggestions(self):
        with patch("memory_vault.link_memories") as mock_link:
            count = apply_suggestions(self.suggestions)
        self.assertEqual(count, 2)
        mock_link.assert_any_call(1, 3, "SEMANTIC_LINK")
        mock_link.assert_any_call(2, 4, "SEMANTIC_LINK")
    def test_error_during_linking_skips(self):
        with patch("memory_vault.link_memories") as mock_link:
            mock_link.side_effect = [None, ValueError("duplicate")]
            count = apply_suggestions(self.suggestions)
        self.assertEqual(count, 1)
    def test_empty_list_returns_zero(self):
        with patch("memory_vault.link_memories"):
            count = apply_suggestions([])
        self.assertEqual(count, 0)

class TestSuggestLinks(unittest.TestCase):
    """suggest_links - semantic similarity suggestions."""
    def setUp(self):
        self.nodes = {1: "First", 2: "Second", 3: "Third"}
        self.ids = [1, 2, 3]
        self.linked = {(1, 2)}
    def _mock_deps(self, embeddings=None):
        import numpy as np
        if embeddings is None:
            embeddings = np.array([
                [1.0, 0.0, 0.0, 0.0],
                [0.9, 0.1, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
            ])
        model = MagicMock()
        model.encode.return_value = embeddings
        p1 = patch("auto_linker.get_unlinked_node_ids", return_value=(self.ids, self.nodes, self.linked))
        p2 = patch("auto_linker.memory_vault._get_model", return_value=model)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
    def test_fewer_than_two_nodes_returns_empty(self):
        with patch("auto_linker.get_unlinked_node_ids", return_value=([1], {1: "x"}, set())):
            result = suggest_links()
        self.assertEqual(result, [])
    def test_model_load_failure_returns_empty(self):
        with patch("auto_linker.get_unlinked_node_ids", return_value=([1, 2], {1: "a", 2: "b"}, set())):
            with patch("auto_linker.memory_vault._get_model", side_effect=RuntimeError("no model")):
                result = suggest_links()
        self.assertEqual(result, [])
    def test_excludes_linked_pairs_below_threshold(self):
        self._mock_deps()
        result = suggest_links(threshold=0.5)
        self.assertEqual(len(result), 0)
    def test_includes_similar_unlinked_pairs(self):
        import numpy as np
        emb = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.9, 0.0, 0.1, 0.0],
        ])
        self._mock_deps(emb)
        result = suggest_links(threshold=0.7)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_id"], 1)
        self.assertEqual(result[0]["target_id"], 3)
    def test_results_sorted_by_score(self):
        import numpy as np
        emb = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.8, 0.0, 0.6, 0.0],
            [0.0, 0.7, 0.0, 0.7],
        ])
        ids = [1, 2, 3, 4]
        nodes = {1: "a", 2: "b", 3: "c", 4: "d"}
        with patch("auto_linker.get_unlinked_node_ids", return_value=(ids, nodes, set())):
            with patch("auto_linker.memory_vault._get_model") as m:
                m.return_value.encode.return_value = emb
                result = suggest_links(threshold=0.1)
        scores = [r["score"] for r in result]
        self.assertEqual(scores, sorted(scores, reverse=True))
    def test_max_results_limits(self):
        import numpy as np
        emb = np.eye(4)
        with patch("auto_linker.get_unlinked_node_ids", return_value=([1,2,3,4], {1:"a",2:"b",3:"c",4:"d"}, set())):
            with patch("auto_linker.memory_vault._get_model") as m:
                m.return_value.encode.return_value = emb
                result = suggest_links(threshold=0.0, max_results=2)
        self.assertLessEqual(len(result), 2)

    def test_filters_out_empty_content_nodes(self):
        """ANTI-FRAGILITY: Empty/whitespace nodes are excluded from semantic embedding."""
        emb = np.array([
            [1.0, 0.0],
            [0.9, 0.1],
        ])
        ids = [1, 2, 3, 4]
        nodes = {1: "Valid content 1", 2: "", 3: "   ", 4: "Valid content 2"}
        with patch("auto_linker.get_unlinked_node_ids", return_value=(ids, nodes, set())):
            with patch("auto_linker.memory_vault._get_model") as m:
                m.return_value.encode.return_value = emb
                result = suggest_links(threshold=0.5)
                # Model should only be called with the 2 non-empty strings
                m.return_value.encode.assert_called_once_with(["Valid content 1", "Valid content 2"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source_id"], 1)
        self.assertEqual(result[0]["target_id"], 4)

    def test_encode_failure_returns_empty(self):
        """ANTI-FRAGILITY: Graceful handling if model.encode raises an error."""
        ids = [1, 2]
        nodes = {1: "Alpha", 2: "Beta"}
        with patch("auto_linker.get_unlinked_node_ids", return_value=(ids, nodes, set())):
            with patch("auto_linker.memory_vault._get_model") as m:
                m.return_value.encode.side_effect = RuntimeError("GPU out of memory")
                result = suggest_links(threshold=0.5)
        self.assertEqual(result, [])


class TestInferRelationshipType(unittest.TestCase):
    """infer_relationship_type maps text relationships to canonical types."""

    def test_is_a_inference(self):
        self.assertEqual(infer_relationship_type("Apple is a type of fruit", "Fruit is healthy"), "IS_A")

    def test_depends_on_inference(self):
        self.assertEqual(infer_relationship_type("Service B requires Service A", "Service A handles auth"), "DEPENDS_ON")

    def test_causes_inference(self):
        self.assertEqual(infer_relationship_type("High load causes latency spikes", "Latency spikes hurt UX"), "CAUSES")

    def test_part_of_inference(self):
        self.assertEqual(infer_relationship_type("The carburetor is a component of the engine", "Engine"), "PART_OF")

    def test_created_by_and_located_in_inference(self):
        self.assertEqual(infer_relationship_type("Linux was created by Linus Torvalds", "Linus"), "CREATED_BY")
        self.assertEqual(infer_relationship_type("The service runs on AWS", "Cloud infrastructure"), "LOCATED_IN")

    def test_plural_and_null_inferences(self):
        self.assertEqual(infer_relationship_type("Workers depend on Redis", "Cache"), "DEPENDS_ON")
        self.assertEqual(infer_relationship_type(None, None), "RELATES_TO")
        self.assertEqual(infer_relationship_type("", " "), "RELATES_TO")

    def test_fallback_related_to(self):
        self.assertEqual(infer_relationship_type("Arbitrary statement one", "Arbitrary statement two"), "RELATES_TO")

    def test_get_conn_respects_memory_vault_db_path(self):
        """ANTI-FRAGILITY: _get_conn respects memory_vault.DB_PATH under vault isolation."""
        import auto_linker
        with patch.object(auto_linker.memory_vault, "DB_PATH", "temp_isolated_vault.db"):
            with patch("sqlite3.connect") as mock_conn:
                auto_linker._get_conn()
                mock_conn.assert_called_once_with("temp_isolated_vault.db", timeout=15.0)


class TestSuggestSemanticTriples(unittest.TestCase):
    """suggest_semantic_triples returns structured triples for candidate memory links."""

    def test_suggest_semantic_triples_formats_triples(self):
        import numpy as np
        emb = np.array([
            [1.0, 0.0],
            [0.95, 0.05],
        ])
        ids = [1, 2]
        nodes = {
            1: "PyQt6 is a GUI framework",
            2: "GUI framework used for desktop apps",
        }
        with patch("auto_linker.get_unlinked_node_ids", return_value=(ids, nodes, set())):
            with patch("auto_linker.memory_vault._get_model") as m:
                m.return_value.encode.return_value = emb
                triples = suggest_semantic_triples(threshold=0.8)

        self.assertEqual(len(triples), 1)
        t = triples[0]
        self.assertEqual(t["source_id"], 1)
        self.assertEqual(t["target_id"], 2)
        self.assertIn("subject", t)
        self.assertIn("predicate", t)
        self.assertIn("object", t)
        self.assertIn("confidence", t)
        self.assertIn("weight", t)
        self.assertIn("timestamp", t)
        self.assertGreater(t["confidence"], 0.8)

