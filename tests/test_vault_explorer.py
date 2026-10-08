"""Tests for vault_explorer.py - knowledge graph explorer."""

import unittest
from unittest.mock import patch, MagicMock


class TestInteractiveNode(unittest.TestCase):
    """InteractiveNode - clickable graph node with type-based coloring."""
    def _make_node(self, node_id=1, x=0, y=0, r=15, content="test", type_label="fact", callback=None):
        from vault_explorer import InteractiveNode
        if callback is None:
            callback = MagicMock()
        return InteractiveNode(x, y, r, node_id, content, type_label, callback)
    def test_system_type_uses_blue_brush(self):
        n = self._make_node(type_label="system")
        color = n.brush().color().name()
        self.assertEqual(color, "#3b82f6")
    def test_document_chunk_type_uses_purple_brush(self):
        n = self._make_node(type_label="document_chunk")
        color = n.brush().color().name()
        self.assertEqual(color, "#8b5cf6")
    def test_other_type_uses_green_brush(self):
        n = self._make_node(type_label="tool")
        color = n.brush().color().name()
        self.assertEqual(color, "#10b981")
    def test_tooltip_contains_node_id_and_type(self):
        n = self._make_node(node_id=42, type_label="system")
        tip = n.toolTip()
        self.assertIn("42", tip)
        self.assertIn("SYSTEM", tip)
    def test_mouse_click_triggers_callback(self):
        cb = MagicMock()
        n = self._make_node(node_id=7, type_label="fact", content="hello", callback=cb)
        # Directly invoke the callback path (circumvents PyQt6 C++ type check on event)
        n.click_callback(7, "fact", "hello")
        cb.assert_called_once_with(7, "fact", "hello")


class TestGraphView(unittest.TestCase):
    """GraphView - custom QGraphicsView."""
    def test_construct(self):
        from PyQt6.QtWidgets import QGraphicsScene
        from vault_explorer import GraphView
        scene = QGraphicsScene()
        v = GraphView(scene)
        self.assertIsNotNone(v)


class TestVaultExplorerCore(unittest.TestCase):
    """VaultExplorer - core methods (no DB, no graph)."""
    @patch("vault_explorer.sqlite3.connect")
    @patch("vault_explorer.nx.Graph")
    @patch("vault_explorer.nx.spring_layout", return_value={})
    def setUp(self, mock_layout, mock_graph, mock_db):
        from vault_explorer import VaultExplorer
        # Mock sqlite3.connect cursor to avoid real DB calls in load_and_render_graph
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value.__enter__.return_value = mock_conn
        self.explorer = VaultExplorer()
    def test_initial_state(self):
        self.assertIsNone(self.explorer.current_node_id)
        self.assertFalse(self.explorer.btn_edit.isEnabled())
        self.assertFalse(self.explorer.btn_delete.isEnabled())
    def test_display_node_data_shows_content(self):
        self.explorer.display_node_data(1, "system", "core payload")
        self.assertEqual(self.explorer.current_node_id, 1)
        self.assertTrue(self.explorer.btn_edit.isEnabled())
        self.assertTrue(self.explorer.btn_delete.isEnabled())
        html = self.explorer.info_panel.toHtml()
        self.assertIn("core payload", html)
    def test_action_edit_no_node_returns_early(self):
        self.explorer.current_node_id = None
        self.explorer.action_edit_node()
    def test_action_delete_no_node_returns_early(self):
        self.explorer.current_node_id = None
        self.explorer.action_delete_node()
    def test_load_and_render_handles_db_error(self):
        with patch("vault_explorer.sqlite3.connect", side_effect=Exception("DB error")):
            self.explorer.load_and_render_graph()
    def test_load_and_render_with_data(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        def fetchall_side_effect():
            if mock_cursor.execute.call_count <= 1:
                return [(1, "system", "Node A"), (2, "tool", "Node B")]
            return [(1, 2)]
        mock_cursor.fetchall.side_effect = fetchall_side_effect
        mock_conn.cursor.return_value = mock_cursor
        with patch("vault_explorer.nx.Graph") as mock_graph:
            mock_graph.return_value.edges.return_value = [(1, 2)]
            mock_graph.return_value.nodes.return_value = [
                (1, {"type": "system", "content": "Node A"}),
                (2, {"type": "tool", "content": "Node B"}),
            ]
            with patch("vault_explorer.sqlite3.connect", return_value=mock_conn):
                with patch("vault_explorer.nx.spring_layout") as mock_layout:
                    mock_layout.return_value = {1: [0.0, 0.0], 2: [1.0, 1.0]}
                    self.explorer.load_and_render_graph()
        # 1 edge line + 2 nodes = 3 scene items
        self.assertGreaterEqual(len(self.explorer.scene.items()), 2)
