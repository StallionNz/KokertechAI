"""
Unit tests for Neural Knowledge Graph tab (tabs/neural_graph_tab.py).

Tests module-level constants and pure functions that don't require Qt,
plus mixin helper methods using lightweight mock parents.
"""

import os
import sys
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from tabs.neural_graph_tab import (
    NODE_TYPE_COLORS,
    RELATIONSHIP_COLORS,
    TYPE_FALLBACK,
    REL_FALLBACK_COLOR,
    _get_rel_color,
    AutoLinkWorker,
    NeuralGraphTabMixin,
)
from PyQt6.QtWidgets import QGraphicsScene

# ── Helper: lightweight mock parent for mixin method tests ────────────
class _MockParent:
    """Minimal object that accepts arbitrary attribute assignment."""
    pass

# ═══════════════════════════════════════════════════════════════════════
# Module-level constant tests (no Qt needed)
# ═══════════════════════════════════════════════════════════════════════

class TestNodeTypeColors(unittest.TestCase):
    """Tests for NODE_TYPE_COLORS mapping."""

    def test_has_expected_types(self):
        expected = {"system", "tool", "os", "hardware", "fact", "interaction", "document_chunk"}
        self.assertEqual(set(NODE_TYPE_COLORS.keys()), expected)

    def test_each_entry_has_color_and_display_name(self):
        for type_name, (color, display_name) in NODE_TYPE_COLORS.items():
            self.assertIsInstance(color, str)
            self.assertTrue(color.startswith("#"),
                            f"{type_name} color {color!r} should be hex")
            self.assertIsInstance(display_name, str)
            self.assertTrue(len(display_name) > 0,
                            f"{type_name} display name should not be empty")

    def test_all_colors_are_valid_hex(self):
        import re
        for type_name, (color, display_name) in NODE_TYPE_COLORS.items():
            self.assertTrue(
                re.match(r"^#[0-9A-Fa-f]{6}$", color),
                f"{type_name} color {color!r} is not a valid 6-digit hex",
            )

    def test_type_fallback_color(self):
        self.assertEqual(TYPE_FALLBACK, "#3B82F6")

class TestRelationshipColors(unittest.TestCase):
    """Tests for RELATIONSHIP_COLORS mapping."""

    def test_has_expected_relationships(self):
        expected = {
            "RELATES_TO", "REFERENCES", "DERIVED_FROM", "SEMANTIC_LINK",
            "CAUSES", "DEPENDS_ON", "CONTAINS", "PART_OF", "FOLLOWS_UP",
        }
        self.assertEqual(set(RELATIONSHIP_COLORS.keys()), expected)

    def test_all_relationship_colors_are_valid_hex(self):
        import re
        for rel_name, color in RELATIONSHIP_COLORS.items():
            self.assertTrue(
                re.match(r"^#[0-9A-Fa-f]{6}$", color),
                f"{rel_name} color {color!r} is not a valid 6-digit hex",
            )

    def test_rel_fallback_is_gray(self):
        self.assertEqual(REL_FALLBACK_COLOR, "#6B7280")

class TestGetRelColor(unittest.TestCase):
    """Tests for _get_rel_color helper."""

    def test_known_type_returns_correct_color(self):
        self.assertEqual(_get_rel_color("RELATES_TO"), "#3B82F6")
        self.assertEqual(_get_rel_color("CAUSES"), "#EF4444")

    def test_case_insensitive(self):
        self.assertEqual(_get_rel_color("relates_to"), "#3B82F6")
        self.assertEqual(_get_rel_color("Relates_To"), "#3B82F6")

    def test_unknown_type_returns_fallback(self):
        self.assertEqual(_get_rel_color("UNKNOWN_REL"), REL_FALLBACK_COLOR)
        self.assertEqual(_get_rel_color(""), REL_FALLBACK_COLOR)

    def test_none_returns_fallback(self):
        self.assertEqual(_get_rel_color(None), REL_FALLBACK_COLOR)

# ═══════════════════════════════════════════════════════════════════════
# Mixin method tests (no QApplication needed — _MockParent + MagicMock)
# ═══════════════════════════════════════════════════════════════════════

class TestGetNodeType(unittest.TestCase):
    """Tests for _get_node_type() helper — pure dict lookup, no Qt."""

    def setUp(self):
        self.parent = _MockParent()
        self.parent._cached_nodes = {
            1: {"type": "fact", "content": "test fact"},
            2: {"type": "system", "content": "test system"},
            3: {"type": "tool", "content": "test tool"},
        }

    def test_known_id_returns_type(self):
        result = NeuralGraphTabMixin._get_node_type(self.parent, 1)
        self.assertEqual(result, "fact")

    def test_second_known_id(self):
        result = NeuralGraphTabMixin._get_node_type(self.parent, 2)
        self.assertEqual(result, "system")

    def test_unknown_id_returns_empty(self):
        result = NeuralGraphTabMixin._get_node_type(self.parent, 99)
        self.assertEqual(result, "")

    def test_no_cache_returns_empty(self):
        parent = _MockParent()
        result = NeuralGraphTabMixin._get_node_type(parent, 1)
        self.assertEqual(result, "")

    def test_node_missing_type_key_returns_empty(self):
        self.parent._cached_nodes = {1: {"content": "no type"}}
        result = NeuralGraphTabMixin._get_node_type(self.parent, 1)
        self.assertEqual(result, "")

class TestFindLineItem(unittest.TestCase):
    """Tests for _find_line_item() helper — dict iteration, no Qt."""

    def setUp(self):
        self.parent = _MockParent()
        self.e1 = MagicMock()
        self.e1.source_id = 1
        self.e1.target_id = 2
        self.e2 = MagicMock()
        self.e2.source_id = 2
        self.e2.target_id = 3
        self.parent._edge_items = {
            ("a", "RELATES_TO"): self.e1,
            ("b", "REFERENCES"): self.e2,
        }

    def test_direct_pair_found(self):
        result = NeuralGraphTabMixin._find_line_item(self.parent, 1, 2)
        self.assertIs(result, self.e1)

    def test_reversed_pair_found(self):
        result = NeuralGraphTabMixin._find_line_item(self.parent, 2, 1)
        self.assertIs(result, self.e1)

    def test_second_pair_found(self):
        result = NeuralGraphTabMixin._find_line_item(self.parent, 2, 3)
        self.assertIs(result, self.e2)

    def test_no_match_returns_none(self):
        result = NeuralGraphTabMixin._find_line_item(self.parent, 1, 99)
        self.assertIsNone(result)

    def test_empty_edges_returns_none(self):
        parent = _MockParent()
        parent._edge_items = {}
        result = NeuralGraphTabMixin._find_line_item(parent, 1, 2)
        self.assertIsNone(result)

class TestEnsurePositionsTable(unittest.TestCase):
    """Tests for _ensure_positions_table() — SQLite DDL, no Qt."""

    def test_creates_table_if_not_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import tabs.neural_graph_tab as ngt
            with patch.object(ngt, 'WORKSPACE_DIR', tmpdir):
                parent = _MockParent()
                NeuralGraphTabMixin._ensure_positions_table(parent)

            # Verify the table was created
            db_path = os.path.join(tmpdir, "kokertech_vault.db")
            self.assertTrue(os.path.exists(db_path))
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='graph_positions'"
            )
            self.assertIsNotNone(cursor.fetchone())
            conn.close()

    def test_handles_existing_table_gracefully(self):
        """Calling twice should not raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            import tabs.neural_graph_tab as ngt
            with patch.object(ngt, 'WORKSPACE_DIR', tmpdir):
                parent = _MockParent()
                NeuralGraphTabMixin._ensure_positions_table(parent)
                NeuralGraphTabMixin._ensure_positions_table(parent)  # second call — no error

class TestSaveNodePosition(unittest.TestCase):
    """Tests for _save_node_position() — SQLite persistence, no Qt."""

    def test_inserts_new_position(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import tabs.neural_graph_tab as ngt
            with patch.object(ngt, 'WORKSPACE_DIR', tmpdir):
                parent = _MockParent()
                NeuralGraphTabMixin._ensure_positions_table(parent)
                NeuralGraphTabMixin._save_node_position(parent, 1, 100.0, 200.0)

            db_path = os.path.join(tmpdir, "kokertech_vault.db")
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT pos_x, pos_y FROM graph_positions WHERE node_id = ?",
                (1,),
            )
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertAlmostEqual(row[0], 100.0)
            self.assertAlmostEqual(row[1], 200.0)
            conn.close()

    def test_updates_existing_position(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import tabs.neural_graph_tab as ngt
            with patch.object(ngt, 'WORKSPACE_DIR', tmpdir):
                parent = _MockParent()
                NeuralGraphTabMixin._ensure_positions_table(parent)
                NeuralGraphTabMixin._save_node_position(parent, 1, 100.0, 200.0)
                NeuralGraphTabMixin._save_node_position(parent, 1, 300.0, 400.0)

            db_path = os.path.join(tmpdir, "kokertech_vault.db")
            conn = sqlite3.connect(db_path)
            cursor = conn.execute(
                "SELECT pos_x, pos_y FROM graph_positions WHERE node_id = ?",
                (1,),
            )
            row = cursor.fetchone()
            self.assertAlmostEqual(row[0], 300.0)
            self.assertAlmostEqual(row[1], 400.0)
            count = conn.execute(
                "SELECT COUNT(*) FROM graph_positions"
            ).fetchone()[0]
            self.assertEqual(count, 1)
            conn.close()

# ═══════════════════════════════════════════════════════════════════════
# _update_type_counts tests (no Qt — MagicMock label objects)
# ═══════════════════════════════════════════════════════════════════════

class TestUpdateTypeCounts(unittest.TestCase):
    """Tests for _update_type_counts() — pure dict counting, no Qt."""

    def setUp(self):
        self.parent = _MockParent()
        self.parent._type_count_labels = {}
        for type_name in NODE_TYPE_COLORS:
            label = MagicMock()
            label.setText = MagicMock()
            label.setVisible = MagicMock()
            self.parent._type_count_labels[type_name] = label

    def test_counts_correctly(self):
        nodes = {
            1: {"type": "fact", "content": "A"},
            2: {"type": "fact", "content": "B"},
            3: {"type": "tool", "content": "C"},
            4: {"type": "system", "content": "D"},
        }
        NeuralGraphTabMixin._update_type_counts(self.parent, nodes)
        self.parent._type_count_labels["fact"].setText.assert_called_with("2")
        self.parent._type_count_labels["fact"].setVisible.assert_called_with(True)
        self.parent._type_count_labels["tool"].setText.assert_called_with("1")
        self.parent._type_count_labels["system"].setText.assert_called_with("1")
        # Types with zero count should be hidden
        self.parent._type_count_labels["hardware"].setText.assert_called_with("0")
        self.parent._type_count_labels["hardware"].setVisible.assert_called_with(False)

    def test_empty_nodes_all_zero(self):
        NeuralGraphTabMixin._update_type_counts(self.parent, {})
        for label in self.parent._type_count_labels.values():
            label.setText.assert_called_with("0")
            label.setVisible.assert_called_with(False)

    def test_unknown_type_maps_to_no_count(self):
        nodes = {1: {"type": "nonexistent", "content": "X"}}
        NeuralGraphTabMixin._update_type_counts(self.parent, nodes)
        # All known types should be zero
        for label in self.parent._type_count_labels.values():
            label.setText.assert_called_with("0")
        # Only nonexistent types get no label; existing labels all show 0

# ═══════════════════════════════════════════════════════════════════════
# display_node_data tests (no Qt — _MockParent with MagicMock widgets)
# ═══════════════════════════════════════════════════════════════════════

class TestDisplayNodeData(unittest.TestCase):
    """Tests for display_node_data() — delegates to _on_node_click."""

    def setUp(self):
        self.parent = _MockParent()
        self.parent.current_node_id = None
        self.parent.current_node_content = ""
        self.parent.btn_edit_node = MagicMock()
        self.parent.btn_delete_node = MagicMock()
        self.parent.btn_expand_node = MagicMock()
        self.parent.graph_info_panel = MagicMock()
        # Bind the real _on_node_click method so display_node_data delegates properly
        import types
        self.parent._on_node_click = types.MethodType(
            NeuralGraphTabMixin._on_node_click, self.parent
        )

    def test_sets_current_node_id_and_content(self):
        NeuralGraphTabMixin.display_node_data(self.parent, 42, "fact", "test content")
        self.assertEqual(self.parent.current_node_id, 42)
        self.assertEqual(self.parent.current_node_content, "test content")

    def test_enables_action_buttons(self):
        NeuralGraphTabMixin.display_node_data(self.parent, 42, "fact", "test content")
        self.parent.btn_edit_node.setEnabled.assert_called_with(True)
        self.parent.btn_delete_node.setEnabled.assert_called_with(True)
        self.parent.btn_expand_node.setEnabled.assert_called_with(True)

    def test_sets_inspector_html(self):
        NeuralGraphTabMixin.display_node_data(self.parent, 42, "fact", "hello world")
        html = self.parent.graph_info_panel.setHtml.call_args[0][0]
        self.assertIn("Node ID: 42", html)
        self.assertIn("FACT", html)
        self.assertIn("hello world", html)

# ═══════════════════════════════════════════════════════════════════════
# _on_node_dragged tests (requires MagicMock EdgeItems, no QApp)
# ═══════════════════════════════════════════════════════════════════════

class TestOnNodeDragged(unittest.TestCase):
    """Tests for _on_node_dragged() — position update, edge/label
    repositioning, and DB write throttling."""

    def setUp(self):
        self.parent = _MockParent()
        self.parent._cached_pos = {1: (0.0, 0.0), 2: (100.0, 0.0)}

        # Mock edge item connecting node 1 to node 2
        self.e1 = MagicMock()
        self.e1.source_id = 1
        self.e1.target_id = 2
        self.e1._offset_x = 0
        self.e1._offset_y = 0
        self.parent._edge_items = {("a", "RELATES_TO"): self.e1}

        # Mock label for node 1
        self.l1 = MagicMock()
        self.parent._label_items = {1: self.l1}

        # Mock the DB persistence method
        self.parent._save_node_position = MagicMock()

    def test_early_return_without_cache(self):
        """Should return silently if _cached_pos doesn't exist."""
        parent = _MockParent()
        NeuralGraphTabMixin._on_node_dragged(parent, 1, 50.0, 50.0)  # no error

    def test_updates_cached_position(self):
        NeuralGraphTabMixin._on_node_dragged(self.parent, 1, 50.0, 25.0)
        self.assertEqual(self.parent._cached_pos[1], (50.0, 25.0))

    def test_updates_connected_edge_line(self):
        NeuralGraphTabMixin._on_node_dragged(self.parent, 1, 50.0, 0.0)
        self.e1.setLine.assert_called_once()

    def test_updates_label_position(self):
        NeuralGraphTabMixin._on_node_dragged(self.parent, 1, 50.0, 0.0)
        self.l1.setPos.assert_called_once()

    def test_throttles_db_writes_every_10th_call(self):
        """_save_node_position should only be called every 10th drag."""
        parent = self.parent
        for i in range(9):
            NeuralGraphTabMixin._on_node_dragged(parent, 1, 50.0, 0.0)
        parent._save_node_position.assert_not_called()
        # 10th call should persist
        NeuralGraphTabMixin._on_node_dragged(parent, 1, 50.0, 0.0)
        parent._save_node_position.assert_called_once_with(1, 50.0, 0.0)

    def test_throttle_counter_rolls_over(self):
        """After 10 calls, the counter should persist, then not persist on 11th."""
        parent = self.parent
        for _ in range(10):
            NeuralGraphTabMixin._on_node_dragged(parent, 1, 50.0, 0.0)
        parent._save_node_position.reset_mock()
        # 11th call should NOT persist
        NeuralGraphTabMixin._on_node_dragged(parent, 1, 50.0, 0.0)
        parent._save_node_position.assert_not_called()

# ═══════════════════════════════════════════════════════════════════════
# draw_knowledge_graph tests (requires QApplication + QGraphicsScene)
# ═══════════════════════════════════════════════════════════════════════

class TestDrawKnowledgeGraph(unittest.TestCase):
    """Tests for draw_knowledge_graph() layout rendering.
    Requires QApplication for QGraphicsScene and Qt item classes.
    Tests verify scene item counts and structure rather than pixel output.
    """

    # QApplication provided by conftest.py (session-scoped qapp fixture)

    def setUp(self):
        """Build a minimal mock parent with all attributes draw_knowledge_graph needs."""
        self.parent = _MockParent()
        self.parent.graph_scene = QGraphicsScene()
        self.parent._edge_items = {}
        self.parent._label_items = {}
        self.parent._collapsed_clusters = set()
        self.parent._cluster_collapse_labels = {}
        self.parent._expand_focus_id = None
        self.parent._has_search_active = False
        self.parent._type_visibility = {t: True for t in NODE_TYPE_COLORS}

        # Callbacks required by draw_knowledge_graph (evaluated as arguments
        # during Qt item construction; stored but not invoked in tests)
        self.parent._toggle_cluster = MagicMock()
        self.parent._on_node_click = MagicMock()
        self.parent.action_edit_node = MagicMock()
        self.parent.action_delete_node = MagicMock()
        self.parent._expand_current_node = MagicMock()
        self.parent._action_link_nodes = MagicMock()
        self.parent._on_node_dragged = MagicMock()

        # Both _apply_search_highlight and _apply_type_filter are CALLED at
        # the end of draw_knowledge_graph; mock them to prevent side effects
        self.parent._apply_search_highlight = MagicMock()
        self.parent._apply_type_filter = MagicMock()

        # Mock graph_minimap
        mm = MagicMock()
        mm.show = MagicMock()
        mm.hide = MagicMock()
        self.parent.graph_minimap = mm

        # Mock graph_view
        gv = MagicMock()
        gv.fitInView = MagicMock()
        gv._emit_viewport_rect = MagicMock()
        self.parent.graph_view = gv

        # Mock graph_search (text() returns "" so _apply_search_highlight is a no-op)
        gs = MagicMock()
        gs.text = MagicMock(return_value="")
        self.parent.graph_search = gs

        # Mock graph_search_count
        gsc = MagicMock()
        gsc.setText = MagicMock()
        gsc.setStyleSheet = MagicMock()
        self.parent.graph_search_count = gsc

    def _call_draw(self, pos, nodes, links):
        """Convenience wrapper for calling the mixin method."""
        NeuralGraphTabMixin.draw_knowledge_graph(self.parent, pos, nodes, links)

    def test_empty_graph_creates_no_items(self):
        self._call_draw({}, {}, [])
        self.assertEqual(len(self.parent.graph_scene.items()), 0)

    def test_single_node_creates_node_and_label(self):
        pos = {1: (100.0, 100.0)}
        nodes = {1: {"type": "fact", "content": "hello"}}
        self._call_draw(pos, nodes, [])
        # Scene items: NodeGraphicsItem + TextLabelItem + ClusterRegionItem
        self.assertGreaterEqual(len(self.parent.graph_scene.items()), 2)

    def test_two_nodes_with_edge(self):
        pos = {1: (0.0, 0.0), 2: (200.0, 0.0)}
        nodes = {
            1: {"type": "fact", "content": "A"},
            2: {"type": "tool", "content": "B"},
        }
        links = [(1, 2, "RELATES_TO")]
        self._call_draw(pos, nodes, links)
        items = self.parent.graph_scene.items()
        self.assertGreaterEqual(len(items), 6)

    def test_collapsed_cluster_skips_individual_nodes(self):
        """Nodes in a collapsed cluster should not appear as individual items."""
        self.parent._collapsed_clusters.add("fact")
        pos = {1: (100.0, 100.0)}
        nodes = {1: {"type": "fact", "content": "hello"}}
        self._call_draw(pos, nodes, [])
        from ui_components import NodeGraphicsItem
        node_items = [
            item for item in self.parent.graph_scene.items()
            if isinstance(item, NodeGraphicsItem)
        ]
        self.assertEqual(len(node_items), 0)

    def test_edge_skipped_when_endpoint_in_collapsed_cluster(self):
        """Edge should not be rendered if either endpoint type is collapsed."""
        self.parent._collapsed_clusters.add("fact")
        pos = {1: (0.0, 0.0), 2: (200.0, 0.0)}
        nodes = {
            1: {"type": "fact", "content": "A"},
            2: {"type": "tool", "content": "B"},
        }
        links = [(1, 2, "RELATES_TO")]
        self._call_draw(pos, nodes, links)
        from ui_components import EdgeItem
        edge_items = [
            item for item in self.parent.graph_scene.items()
            if isinstance(item, EdgeItem)
        ]
        self.assertEqual(len(edge_items), 0)

    def test_expand_mode_shows_only_connected(self):
        """In expand mode, focus node + direct neighbours visible; others hidden."""
        self.parent._expand_focus_id = 1
        pos = {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (400.0, 0.0)}
        nodes = {
            1: {"type": "fact", "content": "A"},
            2: {"type": "tool", "content": "B"},
            3: {"type": "system", "content": "C"},
        }
        links = [(1, 2, "RELATES_TO")]  # node 3 is disconnected
        self._call_draw(pos, nodes, links)
        from ui_components import NodeGraphicsItem
        node_items = {
            item.node_id: item
            for item in self.parent.graph_scene.items()
            if isinstance(item, NodeGraphicsItem)
        }
        self.assertIn(1, node_items)
        self.assertIn(2, node_items)
        self.assertIn(3, node_items)
        self.assertTrue(node_items[1].isVisible(), "Focus node should be visible")
        self.assertTrue(node_items[2].isVisible(), "Connected node should be visible")
        self.assertFalse(node_items[3].isVisible(), "Disconnected node should be hidden")

    def test_long_content_truncated_in_label(self):
        """Node content > 15 chars should have '…' in the label."""
        pos = {1: (100.0, 100.0)}
        nodes = {1: {"type": "fact", "content": "This is a very long content string"}}
        self._call_draw(pos, nodes, [])
        from ui_components import TextLabelItem
        label_texts = [item.toPlainText() for item in self.parent.graph_scene.items()
                       if isinstance(item, TextLabelItem)]
        self.assertTrue(any("…" in t for t in label_texts))

    def test_short_content_not_truncated(self):
        """Node content <= 15 chars should not have '…' in the label."""
        pos = {1: (100.0, 100.0)}
        nodes = {1: {"type": "fact", "content": "Short"}}
        self._call_draw(pos, nodes, [])
        from ui_components import TextLabelItem
        label_texts = [item.toPlainText() for item in self.parent.graph_scene.items()
                       if isinstance(item, TextLabelItem)]
        self.assertTrue(any("Short" in t for t in label_texts))
        self.assertFalse(any("…" in t for t in label_texts))

# ═══════════════════════════════════════════════════════════════════════
# _reset_inspector tests (no Qt)
# ═══════════════════════════════════════════════════════════════════════

class TestResetInspector(unittest.TestCase):
    """Tests for _reset_inspector() — state + widget reset."""

    def setUp(self):
        self.parent = _MockParent()
        self.parent.current_node_id = 42
        self.parent.current_node_content = "old content"
        self.parent.btn_edit_node = MagicMock()
        self.parent.btn_delete_node = MagicMock()
        self.parent.btn_expand_node = MagicMock()
        self.parent.graph_info_panel = MagicMock()

    def test_clears_current_state(self):
        NeuralGraphTabMixin._reset_inspector(self.parent)
        self.assertIsNone(self.parent.current_node_id)
        self.assertEqual(self.parent.current_node_content, "")

    def test_disables_action_buttons(self):
        NeuralGraphTabMixin._reset_inspector(self.parent)
        self.parent.btn_edit_node.setEnabled.assert_called_with(False)
        self.parent.btn_delete_node.setEnabled.assert_called_with(False)
        self.parent.btn_expand_node.setEnabled.assert_called_with(False)

    def test_restores_placeholder_html(self):
        NeuralGraphTabMixin._reset_inspector(self.parent)
        html = self.parent.graph_info_panel.setHtml.call_args[0][0]
        self.assertIn("Memory Inspector", html)
        self.assertIn("Click on any node", html)


# ═══════════════════════════════════════════════════════════════════════
# _on_zoom_changed tests (no Qt)
# ═══════════════════════════════════════════════════════════════════════

class TestOnZoomChanged(unittest.TestCase):
    """Tests for _on_zoom_changed() — label + color update."""

    def setUp(self):
        self.parent = _MockParent()
        self.parent.zoom_label = MagicMock()

    def test_100_percent_is_normal_green(self):
        NeuralGraphTabMixin._on_zoom_changed(self.parent, 1.0)
        self.parent.zoom_label.setText.assert_called_with("100%")
        stylesheet = self.parent.zoom_label.setStyleSheet.call_args[0][0]
        self.assertIn("#10B981", stylesheet)  # green

    def test_50_percent_is_cyan(self):
        NeuralGraphTabMixin._on_zoom_changed(self.parent, 0.25)
        self.parent.zoom_label.setText.assert_called_with("25%")
        stylesheet = self.parent.zoom_label.setStyleSheet.call_args[0][0]
        self.assertIn("#06B6D4", stylesheet)  # cyan

    def test_300_percent_is_amber(self):
        NeuralGraphTabMixin._on_zoom_changed(self.parent, 3.0)
        self.parent.zoom_label.setText.assert_called_with("300%")
        stylesheet = self.parent.zoom_label.setStyleSheet.call_args[0][0]
        self.assertIn("#F59E0B", stylesheet)  # amber

    def test_200_percent_is_green(self):
        NeuralGraphTabMixin._on_zoom_changed(self.parent, 2.0)
        stylesheet = self.parent.zoom_label.setStyleSheet.call_args[0][0]
        self.assertIn("#10B981", stylesheet)  # green for normal range


# ═══════════════════════════════════════════════════════════════════════
# State management tests (no Qt)
# ═══════════════════════════════════════════════════════════════════════

class TestOnTypeToggle(unittest.TestCase):
    """Tests for _on_type_toggle() — visibility state update."""

    def setUp(self):
        self.parent = _MockParent()
        self.parent._type_visibility = {"fact": True, "tool": True}
        self.parent._apply_type_filter = MagicMock()
        self.parent._redraw_with_filters = MagicMock()

    def test_hide_calls_apply_type_filter(self):
        NeuralGraphTabMixin._on_type_toggle(self.parent, "fact", False)
        self.assertFalse(self.parent._type_visibility["fact"])
        self.parent._apply_type_filter.assert_called_once()
        self.parent._redraw_with_filters.assert_not_called()

    def test_show_calls_redraw_with_filters(self):
        NeuralGraphTabMixin._on_type_toggle(self.parent, "tool", True)
        self.assertTrue(self.parent._type_visibility["tool"])
        self.parent._redraw_with_filters.assert_called_once()
        self.parent._apply_type_filter.assert_not_called()


class TestCollapseShowAllTypes(unittest.TestCase):
    """Tests for _collapse_all_types() and _show_all_types()."""

    def setUp(self):
        self.parent = _MockParent()
        self.parent._type_visibility = {"fact": True, "tool": True}
        self.parent._apply_type_filter = MagicMock()
        self.parent._redraw_with_filters = MagicMock()
        self.parent._sync_type_checkboxes = MagicMock()

    def test_collapse_all_sets_all_false(self):
        NeuralGraphTabMixin._collapse_all_types(self.parent)
        self.assertFalse(self.parent._type_visibility["fact"])
        self.assertFalse(self.parent._type_visibility["tool"])
        self.parent._sync_type_checkboxes.assert_called_with(checked=False)
        self.parent._apply_type_filter.assert_called_once()

    def test_show_all_sets_all_true(self):
        NeuralGraphTabMixin._show_all_types(self.parent)
        self.assertTrue(self.parent._type_visibility["fact"])
        self.assertTrue(self.parent._type_visibility["tool"])
        self.parent._sync_type_checkboxes.assert_called_with(checked=True)
        self.parent._redraw_with_filters.assert_called_once()


class TestOnHideClustersToggled(unittest.TestCase):
    """Tests for _on_hide_clusters_toggled()."""

    def test_checked_calls_apply_type_filter(self):
        parent = _MockParent()
        parent._cached_pos = {1: (0.0, 0.0)}  # make sure guard passes
        parent._apply_type_filter = MagicMock()
        parent._redraw_with_filters = MagicMock()
        NeuralGraphTabMixin._on_hide_clusters_toggled(parent, True)
        parent._apply_type_filter.assert_called_once()
        parent._redraw_with_filters.assert_not_called()

    def test_unchecked_calls_redraw_with_filters(self):
        parent = _MockParent()
        parent._cached_pos = {1: (0.0, 0.0)}
        parent._apply_type_filter = MagicMock()
        parent._redraw_with_filters = MagicMock()
        NeuralGraphTabMixin._on_hide_clusters_toggled(parent, False)
        parent._redraw_with_filters.assert_called_once()
        parent._apply_type_filter.assert_not_called()

    def test_no_cached_pos_returns_early(self):
        parent = _MockParent()
        parent._apply_type_filter = MagicMock()
        parent._redraw_with_filters = MagicMock()
        NeuralGraphTabMixin._on_hide_clusters_toggled(parent, True)
        parent._apply_type_filter.assert_not_called()
        parent._redraw_with_filters.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# _toggle_cluster tests (no Qt)
# ═══════════════════════════════════════════════════════════════════════

class TestToggleCluster(unittest.TestCase):
    """Tests for _toggle_cluster() — collapse/expand set operations."""

    def setUp(self):
        self.parent = _MockParent()
        self.parent._collapsed_clusters = set()
        self.parent._cluster_collapse_labels = {}
        self.parent._type_checkboxes = {}
        self.parent._type_visibility = {"fact": True, "tool": True}
        self.parent.draw_knowledge_graph = MagicMock()
        self.parent._update_type_cluster_indicators = MagicMock()
        gv = MagicMock()
        gv._emit_viewport_rect = MagicMock()
        self.parent.graph_view = gv
        # Need cached data for redraw
        self.parent._cached_pos = {1: (100.0, 100.0)}
        self.parent._cached_nodes = {1: {"type": "fact", "content": "test"}}
        self.parent._cached_links = []

    def test_add_to_collapsed_when_not_present(self):
        NeuralGraphTabMixin._toggle_cluster(self.parent, "fact")
        self.assertIn("fact", self.parent._collapsed_clusters)
        self.parent.draw_knowledge_graph.assert_called_once()
        self.parent._update_type_cluster_indicators.assert_called_once()

    def test_remove_from_collapsed_when_present(self):
        self.parent._collapsed_clusters.add("fact")
        NeuralGraphTabMixin._toggle_cluster(self.parent, "fact")
        self.assertNotIn("fact", self.parent._collapsed_clusters)
        self.parent.draw_knowledge_graph.assert_called_once()

    def test_no_cached_pos_skips_redraw(self):
        parent = _MockParent()
        parent._collapsed_clusters = set()
        parent.draw_knowledge_graph = MagicMock()
        parent._update_type_cluster_indicators = MagicMock()
        NeuralGraphTabMixin._toggle_cluster(parent, "fact")
        parent.draw_knowledge_graph.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# _redraw_with_filters tests (no Qt)
# ═══════════════════════════════════════════════════════════════════════

class TestRedrawWithFilters(unittest.TestCase):
    """Tests for _redraw_with_filters() — delegation to draw."""

    def test_redraws_when_cached_data_exists(self):
        parent = _MockParent()
        pos = {1: (100.0, 100.0)}
        nodes = {1: {"type": "fact", "content": "test"}}
        links = []
        parent._cached_pos = pos
        parent._cached_nodes = nodes
        parent._cached_links = links
        parent.draw_knowledge_graph = MagicMock()
        gv = MagicMock()
        gv._emit_viewport_rect = MagicMock()
        parent.graph_view = gv
        NeuralGraphTabMixin._redraw_with_filters(parent)
        parent.draw_knowledge_graph.assert_called_once_with(pos, nodes, links)
        gv._emit_viewport_rect.assert_called_once()

    def test_no_cached_pos_skips(self):
        parent = _MockParent()
        parent.draw_knowledge_graph = MagicMock()
        NeuralGraphTabMixin._redraw_with_filters(parent)
        parent.draw_knowledge_graph.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# _on_type_shortcut tests (no Qt)
# ═══════════════════════════════════════════════════════════════════════

class TestOnTypeShortcut(unittest.TestCase):
    """Tests for _on_type_shortcut() — toggles type visibility by index."""

    def setUp(self):
        self.parent = _MockParent()
        self.parent._type_visibility = {t: True for t in NODE_TYPE_COLORS}
        self.parent._type_checkboxes = {}
        self.parent._on_type_toggle = MagicMock()
        for t in NODE_TYPE_COLORS:
            cb = MagicMock()
            cb.isChecked = MagicMock(return_value=True)
            self.parent._type_checkboxes[t] = cb

    def test_valid_index_toggles_first_type(self):
        type_names = sorted(NODE_TYPE_COLORS.keys())
        first_type = type_names[0]
        self.parent._type_checkboxes[first_type].isChecked = MagicMock(return_value=True)
        NeuralGraphTabMixin._on_type_shortcut(self.parent, 0)
        # Should set to False (toggling from True)
        self.parent._type_checkboxes[first_type].setChecked.assert_called_with(False)
        self.parent._on_type_toggle.assert_called_once()

    def test_valid_index_toggles_to_true_when_unchecked(self):
        type_names = sorted(NODE_TYPE_COLORS.keys())
        first_type = type_names[0]
        self.parent._type_checkboxes[first_type].isChecked = MagicMock(return_value=False)
        NeuralGraphTabMixin._on_type_shortcut(self.parent, 0)
        self.parent._type_checkboxes[first_type].setChecked.assert_called_with(True)

    def test_out_of_bounds_index_does_nothing(self):
        NeuralGraphTabMixin._on_type_shortcut(self.parent, 999)
        self.parent._on_type_toggle.assert_not_called()

    def test_negative_index_does_nothing(self):
        NeuralGraphTabMixin._on_type_shortcut(self.parent, -1)
        self.parent._on_type_toggle.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# AutoLink tests (worker and mixin slots)
# ═══════════════════════════════════════════════════════════════════════

class TestAutoLinkWorker(unittest.TestCase):
    """Tests for AutoLinkWorker QThread."""

    def test_run_emits_suggestions_ready(self):
        """ANTI-FRAGILITY: AutoLinkWorker computes links in thread and emits suggestions_ready."""
        worker = AutoLinkWorker(threshold=0.6, max_results=5)
        suggestions_received = []
        status_received = []
        worker.suggestions_ready.connect(suggestions_received.append)
        worker.status_signal.connect(status_received.append)

        mock_suggestions = [
            {"source_id": 1, "target_id": 2, "source_content": "A", "target_content": "B", "score": 0.88}
        ]
        with patch("auto_linker.suggest_links", return_value=mock_suggestions) as mock_suggest:
            worker.run()
            mock_suggest.assert_called_once_with(threshold=0.6, max_results=5)

        self.assertEqual(len(suggestions_received), 1)
        self.assertEqual(suggestions_received[0], mock_suggestions)
        self.assertTrue(len(status_received) > 0)

    def test_run_handles_exception_and_emits_error(self):
        """ANTI-FRAGILITY: AutoLinkWorker catches exceptions and emits error_signal."""
        worker = AutoLinkWorker()
        errors_received = []
        worker.error_signal.connect(errors_received.append)

        with patch("auto_linker.suggest_links", side_effect=RuntimeError("Database lock")):
            worker.run()

        self.assertEqual(len(errors_received), 1)
        self.assertIn("Database lock", errors_received[0])


class _MockDashboard(_MockParent, NeuralGraphTabMixin):
    """Mock parent that inherits NeuralGraphTabMixin methods."""
    pass


class TestAutoLinkActions(unittest.TestCase):
    """Tests for Auto-link actions on NeuralGraphTabMixin."""

    def setUp(self):
        self.parent = _MockDashboard()
        self.parent.log_to_audit = MagicMock()
        self.parent.render_knowledge_graph = MagicMock()
        self.btn_mock = MagicMock()
        self.parent.btn_auto_link = self.btn_mock

    def test_action_auto_link_starts_worker(self):
        """ANTI-FRAGILITY: _action_auto_link disables button and starts background worker."""
        with patch("tabs.neural_graph_tab.AutoLinkWorker") as mock_worker_cls:
            mock_instance = MagicMock()
            mock_instance.isRunning.return_value = False
            mock_worker_cls.return_value = mock_instance

            NeuralGraphTabMixin._action_auto_link(self.parent)

            self.btn_mock.setEnabled.assert_called_with(False)
            self.btn_mock.setText.assert_called_with("⏳ LINKING…")
            self.parent.log_to_audit.assert_called_with("🔗 Computing semantic link suggestions…")
            mock_instance.start.assert_called_once()

    def test_action_auto_link_prevents_duplicate_run_if_running(self):
        """ANTI-FRAGILITY: _action_auto_link does not launch a second worker if one is running."""
        running_worker = MagicMock()
        running_worker.isRunning.return_value = True
        self.parent._auto_link_worker = running_worker

        with patch("tabs.neural_graph_tab.AutoLinkWorker") as mock_worker_cls:
            NeuralGraphTabMixin._action_auto_link(self.parent)
            mock_worker_cls.assert_not_called()
            self.parent.log_to_audit.assert_called_with("⏳ Auto-link is already running…")

    def test_on_auto_link_finished_restores_button(self):
        """ANTI-FRAGILITY: _on_auto_link_finished restores button state and cleans up worker."""
        self.parent._auto_link_worker = MagicMock()
        NeuralGraphTabMixin._on_auto_link_finished(self.parent)
        self.btn_mock.setEnabled.assert_called_with(True)
        self.btn_mock.setText.assert_called_with("🔗 AUTO-LINK")
        self.assertIsNone(self.parent._auto_link_worker)

    def test_show_auto_link_dialog_empty_suggestions(self):
        """ANTI-FRAGILITY: _show_auto_link_dialog handles empty suggestions cleanly."""
        with patch("tabs.neural_graph_tab.QMessageBox.information"):
            NeuralGraphTabMixin._show_auto_link_dialog(self.parent, [])
            self.parent.log_to_audit.assert_called_with("🔗 No new link suggestions.")

    def test_show_auto_link_dialog_applies_selected(self):
        """ANTI-FRAGILITY: _show_auto_link_dialog invokes apply_suggestions and redraws graph."""
        suggestions = [
            {"source_id": 1, "target_id": 2, "source_content": "A", "target_content": "B", "score": 0.88}
        ]
        with patch("tabs.neural_graph_tab.QDialog.exec", return_value=1):  # Accepted
            with patch("auto_linker.apply_suggestions", return_value=1) as mock_apply:
                NeuralGraphTabMixin._show_auto_link_dialog(self.parent, suggestions)
                mock_apply.assert_called_once_with(suggestions)
                self.parent.log_to_audit.assert_called_with("🔗 Applied 1 semantic links.")
                self.parent.render_knowledge_graph.assert_called_once()


class TestDeleteCurrentNode(unittest.TestCase):
    """REGRESSION GUARD: verify action_delete_node in NeuralGraphTabMixin delegates
    to memory_vault.delete_memory_node without hardcoding database paths or raw SQL.
    """

    def setUp(self):
        self.parent = _MockDashboard()
        self.parent.log_to_audit = MagicMock()
        self.parent.render_knowledge_graph = MagicMock()
        self.parent.current_node_id = 99

    def test_delete_current_node_none_id_returns_early(self):
        self.parent.current_node_id = None
        NeuralGraphTabMixin.action_delete_node(self.parent)
        self.parent.log_to_audit.assert_not_called()

    def test_delete_current_node_user_rejects_dialog(self):
        from PyQt6.QtWidgets import QMessageBox
        with patch("tabs.neural_graph_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            with patch("memory_vault.delete_memory_node") as mock_del:
                NeuralGraphTabMixin.action_delete_node(self.parent)
                mock_del.assert_not_called()

    def test_delete_current_node_user_confirms_calls_memory_vault(self):
        from PyQt6.QtWidgets import QMessageBox
        with patch("tabs.neural_graph_tab.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            with patch("memory_vault.delete_memory_node", return_value=True) as mock_del:
                NeuralGraphTabMixin.action_delete_node(self.parent)
                mock_del.assert_called_once_with(99)
                self.parent.log_to_audit.assert_called_with("🗑️ Node 99 deleted.")
                self.parent.render_knowledge_graph.assert_called_once()
                self.assertIsNone(self.parent.current_node_id)


# ═══════════════════════════════════════════════════════════════════════
# Realm 5: Physics Layout & Visual Knowledge Canvas Tests
# ═══════════════════════════════════════════════════════════════════════

class TestForceDirectedLayoutEngine(unittest.TestCase):
    """REGRESSION GUARD: verify GraphLayoutWorker computes physics-driven
    force-directed coordinates (Fruchterman-Reingold / simulated annealing)
    with weight-driven edge attraction and cancellation support.
    """

    def test_single_node_layout_centers_in_canvas(self):
        """Single node must be centered on canvas coordinates."""
        from ui_components import GraphLayoutWorker
        worker = GraphLayoutWorker(canvas_size=(1000, 600))
        pos = worker._compute_force_directed_layout([1], [], {})
        self.assertEqual(pos, {1: (500.0, 300.0)})

    def test_preserves_saved_positions_when_all_present_and_no_force_recompute(self):
        """When all nodes already have saved positions, they must be preserved exactly."""
        from ui_components import GraphLayoutWorker
        worker = GraphLayoutWorker(force_recompute=False)
        saved = {1: (150.0, 250.0), 2: (400.0, 500.0)}
        pos = worker._compute_force_directed_layout([1, 2], [(1, 2, "RELATES_TO", 1.0, 1.0)], saved)
        self.assertEqual(pos[1], (150.0, 250.0))
        self.assertEqual(pos[2], (400.0, 500.0))

    def test_force_recompute_relaxes_saved_positions(self):
        """When force_recompute is True, simulation runs and updates coordinates."""
        from ui_components import GraphLayoutWorker
        import math
        worker = GraphLayoutWorker(iterations=50, force_recompute=True)
        saved = {1: (50.0, 50.0), 2: (60.0, 60.0)}
        pos = worker._compute_force_directed_layout([1, 2], [], saved)
        dist = math.hypot(pos[1][0] - pos[2][0], pos[1][1] - pos[2][1])
        self.assertGreater(dist, 20.0)

    def test_edge_weight_attraction_pulls_connected_nodes_closer(self):
        """Heavy weight edge must pull nodes significantly closer than disconnected nodes."""
        from ui_components import GraphLayoutWorker
        import math
        worker = GraphLayoutWorker(iterations=80, force_recompute=True)
        links = [(1, 2, "DEPENDS_ON", 4.0, 1.0)]
        pos = worker._compute_force_directed_layout([1, 2, 3], links, {})
        dist_12 = math.hypot(pos[1][0] - pos[2][0], pos[1][1] - pos[2][1])
        dist_13 = math.hypot(pos[1][0] - pos[3][0], pos[1][1] - pos[3][1])
        self.assertLess(dist_12, dist_13)

    def test_cancellation_stops_simulation_early(self):
        """Setting cancel flag stops simulation iterations promptly."""
        from ui_components import GraphLayoutWorker
        worker = GraphLayoutWorker(iterations=200)
        worker.cancel()
        self.assertTrue(worker._cancel.is_set())
        pos = worker._compute_force_directed_layout([1, 2, 3], [], {})
        self.assertEqual(len(pos), 3)

    def test_worker_run_loads_links_with_weights_from_db(self):
        """Worker run queries memory_links and emits pos, nodes, and weighted links."""
        from ui_components import GraphLayoutWorker
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        try:
            conn = sqlite3.connect(temp_db)
            conn.execute("CREATE TABLE core_memories (id INTEGER PRIMARY KEY, content TEXT, node_type TEXT)")
            # Canonical schema has BOTH column names (memory_vault.py ~L510);
            # the dual-schema COALESCE query resolves both at prepare time, so
            # a table missing either column fails the primary AND legacy reads.
            conn.execute("CREATE TABLE memory_links (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, relationship_type TEXT DEFAULT 'RELATES_TO', relation_type TEXT, weight REAL, confidence REAL)")
            conn.execute("INSERT INTO core_memories VALUES (1, 'Alpha', 'concept'), (2, 'Beta', 'system')")
            conn.execute("INSERT INTO memory_links (id, source_id, target_id, relation_type, weight, confidence) VALUES (10, 1, 2, 'CAUSES', 2.5, 0.95)")
            conn.commit()
            conn.close()

            worker = GraphLayoutWorker(iterations=30)
            emitted = []
            worker.layout_ready_signal.connect(lambda p, n, l: emitted.append((p, n, l)))

            with patch("memory_vault.DB_PATH", temp_db):
                worker.run()

            self.assertEqual(len(emitted), 1)
            pos, nodes, links = emitted[0]
            self.assertIn(1, pos)
            self.assertIn(2, pos)
            self.assertEqual(nodes[1]["content"], "Alpha")
            self.assertEqual(len(links), 1)
            src, tgt, rel, weight, conf = links[0]
            self.assertEqual((src, tgt, rel), (1, 2, "CAUSES"))
            self.assertAlmostEqual(weight, 2.5)
            self.assertAlmostEqual(conf, 0.95)
        finally:
            if os.path.exists(temp_db):
                os.remove(temp_db)


class TestLevelOfDetailViewportScaling(unittest.TestCase):
    """REGRESSION GUARD: verify Level-of-Detail (LOD) hides micro labels when
    zoomed out (<0.4x) and restores them when zoomed in (>=0.4x).
    """

    def setUp(self):
        self.parent = _MockParent()
        self.l1 = MagicMock()
        self.l2 = MagicMock()
        self.l1.toPlainText.return_value = "[1] Root Concept"
        self.l2.toPlainText.return_value = "[2] Sub Item"
        self.parent._label_items = {1: self.l1, 2: self.l2}
        self.parent._expand_focus_id = None
        self.parent._expand_connected_ids = set()
        self.parent._has_search_active = False

    def test_lod_hides_micro_labels_below_40_percent(self):
        NeuralGraphTabMixin._update_lod(self.parent, 0.35)
        self.l1.setVisible.assert_called_with(False)
        self.l2.setVisible.assert_called_with(False)

    def test_lod_shows_labels_at_normal_zoom(self):
        NeuralGraphTabMixin._update_lod(self.parent, 1.0)
        self.l1.setVisible.assert_called_with(True)
        self.l2.setVisible.assert_called_with(True)

    def test_lod_respects_expand_mode_at_normal_zoom(self):
        self.parent._expand_focus_id = 1
        self.parent._expand_connected_ids = {1}
        NeuralGraphTabMixin._update_lod(self.parent, 1.0)
        self.l1.setVisible.assert_called_with(True)
        self.l2.setVisible.assert_called_with(False)


class TestNeighborhoodSubgraphIllumination(unittest.TestCase):
    """REGRESSION GUARD: verify highlight_subgraph illuminates 1-hop and 2-hop
    neighbors with distinctive styling and dims unrelated scene items.
    """

    def setUp(self):
        self.parent = _MockParent()
        self.scene = QGraphicsScene()
        self.parent.graph_scene = self.scene

        from ui_components import NodeGraphicsItem, EdgeItem, TextLabelItem
        self.n1 = NodeGraphicsItem(0, 0, 50, 50, 1, "Center", "concept", None, None, None)
        self.n2 = NodeGraphicsItem(100, 0, 50, 50, 2, "Hop1", "fact", None, None, None)
        self.n3 = NodeGraphicsItem(200, 0, 50, 50, 3, "Hop2", "tool", None, None, None)
        self.n4 = NodeGraphicsItem(300, 0, 50, 50, 4, "Isolated", "os", None, None, None)

        self.e12 = EdgeItem(25, 25, 125, 25, 1, 2, "RELATES_TO", 2.0, 1.0)
        self.e23 = EdgeItem(125, 25, 225, 25, 2, 3, "DEPENDS_ON", 1.5, 1.0)
        self.e_other = EdgeItem(500, 500, 600, 600, 5, 6, "REFERENCES", 1.0, 1.0)

        self.lbl1 = TextLabelItem("[1] Center")
        self.lbl4 = TextLabelItem("[4] Isolated")

        for item in [self.n1, self.n2, self.n3, self.n4, self.e12, self.e23, self.e_other, self.lbl1, self.lbl4]:
            self.scene.addItem(item)

    def test_highlight_subgraph_styles_and_dims_appropriately(self):
        mock_subgraph = {
            "start_node_id": 1,
            "nodes": [
                {"id": 1, "hop": 0},
                {"id": 2, "hop": 1},
                {"id": 3, "hop": 2},
            ],
            "links": [
                {"source_id": 1, "target_id": 2, "relation_type": "RELATES_TO"},
                {"source_id": 2, "target_id": 3, "relation_type": "DEPENDS_ON"},
            ],
        }
        with patch("memory_vault.traverse_subgraph", return_value=mock_subgraph):
            NeuralGraphTabMixin.highlight_subgraph(self.parent, 1, max_hops=2)

        self.assertEqual(self.n1.opacity(), 1.0)
        self.assertEqual(self.n2.opacity(), 1.0)
        self.assertAlmostEqual(self.n3.opacity(), 0.85)
        self.assertAlmostEqual(self.n4.opacity(), 0.20)

        self.assertEqual(self.e12.opacity(), 1.0)
        self.assertEqual(self.e23.opacity(), 1.0)
        self.assertAlmostEqual(self.e_other.opacity(), 0.12)

        self.assertEqual(self.lbl1.opacity(), 1.0)
        self.assertAlmostEqual(self.lbl4.opacity(), 0.20)

    def test_clear_subgraph_highlight_restores_full_opacity(self):
        self.n4.setOpacity(0.20)
        self.e_other.setOpacity(0.12)
        self.parent._active_highlight_node_id = 1

        NeuralGraphTabMixin.clear_subgraph_highlight(self.parent)

        self.assertEqual(self.n4.opacity(), 1.0)
        self.assertEqual(self.e_other.opacity(), 1.0)
        self.assertIsNone(self.parent._active_highlight_node_id)


class TestNeuralGraphTeardown(unittest.TestCase):
    """REGRESSION GUARD: verify teardown and teardown_neural_graph terminate
    background layout and auto-link worker threads and cancel animations.
    """

    def setUp(self):
        self.parent = _MockParent()
        self.mock_worker = MagicMock()
        self.mock_worker.isRunning.return_value = True
        self.mock_auto = MagicMock()
        self.mock_auto.isRunning.return_value = True
        self.parent.layout_worker = self.mock_worker
        self.parent._auto_link_worker = self.mock_auto
        self.parent._cancel_expand_animation = MagicMock()
        self.parent._active_highlight_node_id = 42
        self.parent.current_node_id = 42
        self.parent.current_node_content = "Something"

    def test_teardown_stops_workers_and_cleans_state(self):
        NeuralGraphTabMixin.teardown(self.parent)
        self.mock_worker.cancel.assert_called_once()
        self.mock_worker.quit.assert_called_once()
        self.mock_worker.wait.assert_called_once()
        self.assertIsNone(self.parent.layout_worker)

        self.mock_auto.quit.assert_called_once()
        self.mock_auto.wait.assert_called_once()
        self.assertIsNone(self.parent._auto_link_worker)

        self.parent._cancel_expand_animation.assert_called_once()
        self.assertIsNone(self.parent._active_highlight_node_id)
        self.assertIsNone(self.parent.current_node_id)
        self.assertEqual(self.parent.current_node_content, "")

    def test_teardown_neural_graph_delegates_to_teardown(self):
        self.parent.teardown = MagicMock()
        NeuralGraphTabMixin.teardown_neural_graph(self.parent)
        self.parent.teardown.assert_called_once()


if __name__ == "__main__":
    unittest.main()


