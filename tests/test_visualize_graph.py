"""Tests for visualize_graph.py - knowledge graph visualization."""

import unittest
from unittest.mock import patch, MagicMock


def _mock_db(nodes=None, links=None):
    """Create a mock sqlite3.connect context for testing."""
    cursor = MagicMock()
    def execute_side_effect(sql):
        if "core_memories" in sql:
            cursor.fetchall.return_value = nodes or []
        elif "memory_links" in sql:
            cursor.fetchall.return_value = links or []
        return cursor
    conn = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.side_effect = execute_side_effect
    return patch("visualize_graph.sqlite3.connect", return_value=conn)


class TestVisualizeEcosystem(unittest.TestCase):
    """visualize_ecosystem - DB-driven knowledge graph logging."""
    def setUp(self):
        self.logger_patch = patch("visualize_graph.logger")
        self.mock_logger = self.logger_patch.start()
        self.addCleanup(self.logger_patch.stop)
    def test_db_not_found_logs_error(self):
        with patch("visualize_graph.os.path.exists", return_value=False):
            from visualize_graph import visualize_ecosystem
            visualize_ecosystem()
        self.mock_logger.error.assert_called_once()
        self.assertIn("not found", self.mock_logger.error.call_args[0][0].lower())
    def test_no_links_lists_orphan_nodes(self):
        nodes = [(1, "fact", "First memory"), (2, "tool", "Second memory")]
        links = []
        with patch("visualize_graph.os.path.exists", return_value=True):
            with _mock_db(nodes, links):
                from visualize_graph import visualize_ecosystem
                visualize_ecosystem()
        info_calls = [str(c) for c in self.mock_logger.info.call_args_list]
        any_no_rel = any("No relationships" in c for c in info_calls)
        any_first = any("First memory" in c for c in info_calls)
        any_second = any("Second memory" in c for c in info_calls)
        self.assertTrue(any_no_rel)
        self.assertTrue(any_first)
        self.assertTrue(any_second)
    def test_links_display_with_nodes(self):
        nodes = [(1, "system", "KokerPro Core"), (2, "tool", "StallionNZ Tool")]
        links = [(1, 2, "USES_TOOL")]
        with patch("visualize_graph.os.path.exists", return_value=True):
            with _mock_db(nodes, links):
                from visualize_graph import visualize_ecosystem
                visualize_ecosystem()
        info_calls = [str(c) for c in self.mock_logger.info.call_args_list]
        any_link = any("USES_TOOL" in c for c in info_calls)
        any_src = any("KokerPro Core" in c for c in info_calls)
        any_tgt = any("StallionNZ Tool" in c for c in info_calls)
        self.assertTrue(any_link)
        self.assertTrue(any_src)
        self.assertTrue(any_tgt)
    def test_truncates_long_content(self):
        long = "x" * 50
        # Truncation only happens in linked path, not the no-links orphan path
        nodes = [(1, "fact", long), (2, "tool", "short")]
        links = [(1, 2, "LINKS")]
        with patch("visualize_graph.os.path.exists", return_value=True):
            with _mock_db(nodes, links):
                from visualize_graph import visualize_ecosystem
                visualize_ecosystem()
        info_calls = [str(c) for c in self.mock_logger.info.call_args_list]
        any_truncated = any("..." in c for c in info_calls)
        self.assertTrue(any_truncated)
    def test_orphan_nodes_logged_separately(self):
        nodes = [(1, "system", "Linked"), (2, "tool", "Orphan"), (3, "fact", "Also orphan")]
        links = [(1, 2, "CONNECTS")]
        with patch("visualize_graph.os.path.exists", return_value=True):
            with _mock_db(nodes, links):
                from visualize_graph import visualize_ecosystem
                visualize_ecosystem()
        info_calls = [str(c) for c in self.mock_logger.info.call_args_list]
        combined = " ".join(str(c) for c in info_calls)
        self.assertIn("Linked", combined)
        self.assertIn("Orphan", combined)
        self.assertIn("Also orphan", combined)
    def test_unknown_node_in_link_shows_unknown(self):
        nodes = [(1, "system", "Exists")]
        links = [(1, 999, "REFERENCES")]
        with patch("visualize_graph.os.path.exists", return_value=True):
            with _mock_db(nodes, links):
                from visualize_graph import visualize_ecosystem
                visualize_ecosystem()
        info_calls = [str(c) for c in self.mock_logger.info.call_args_list]
        combined = " ".join(str(c) for c in info_calls)
        self.assertIn("Exists", combined)
        self.assertIn("Unknown", combined)
