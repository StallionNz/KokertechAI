"""
vault_explorer.py — Knowledge graph explorer for KokertechAI.
Provides InteractiveNode (clickable graph node), GraphView, and VaultExplorer.
"""

import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsScene,
    QGraphicsView,
    QTextBrowser,
    QPushButton,
)

import networkx as nx

from config import CONFIG
from logging_config import get_logger

logger = get_logger(name="MemoryVault")

# ── Colour map for node types ────────────────────────────────────
_TYPE_COLORS = {
    "system": "#3b82f6",
    "document_chunk": "#8b5cf6",
}
_DEFAULT_COLOR = "#10b981"


class InteractiveNode(QGraphicsEllipseItem):
    """Clickable graph node with type-based coloring."""

    def __init__(self, x, y, r, node_id, content, type_label, callback):
        super().__init__(x, y, r, r)
        self._node_id = node_id
        self._type_label = type_label
        self._content = content
        self._callback = callback
        color = _TYPE_COLORS.get(type_label, _DEFAULT_COLOR)
        self.setBrush(QBrush(QColor(color)))
        self.setToolTip(f"ID: {node_id} | Type: {type_label.upper()}")
        self.setAcceptHoverEvents(True)

    def mousePressEvent(self, event):
        self.click_callback(self._node_id, self._type_label, self._content)
        super().mousePressEvent(event)

    def click_callback(self, node_id, type_label, content):
        """Invoke the stored callback with node data."""
        if self._callback is not None:
            self._callback(node_id, type_label, content)


class GraphView(QGraphicsView):
    """Custom QGraphicsView for the knowledge graph."""

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)


class VaultExplorer:
    """Vault knowledge-graph explorer with DB loading and rendering."""

    def __init__(self):
        self.current_node_id = None
        self.scene = QGraphicsScene()
        self.info_panel = QTextBrowser()
        self.btn_edit = QPushButton("Edit")
        self.btn_delete = QPushButton("Delete")
        self.btn_edit.setEnabled(False)
        self.btn_delete.setEnabled(False)

    def display_node_data(self, node_id, type_label, content):
        """Display node details and enable action buttons."""
        from html_sanitizer import escape_message_html
        self.current_node_id = node_id
        self.btn_edit.setEnabled(True)
        self.btn_delete.setEnabled(True)
        # DB-derived strings must never reach setHtml raw — escape with
        # layout preservation so multi-line node content keeps its shape.
        self.info_panel.setHtml(
            f"<h3>Node {escape_message_html(node_id)}</h3>"
            f"<p><b>Type:</b> {escape_message_html(type_label)}</p>"
            f"<p><b>Content:</b> {escape_message_html(content)}</p>"
        )

    def action_edit_node(self):
        """Edit the selected node (stub — placeholder)."""
        if self.current_node_id is None:
            return

    def action_delete_node(self):
        """Delete the selected node (stub — placeholder)."""
        if self.current_node_id is None:
            return

    def load_and_render_graph(self):
        """Load graph data from the vault DB and render into scene."""
        try:
            db_path = CONFIG.get("db_path", "kokertech_vault.db")
            conn = sqlite3.connect(db_path, timeout=15.0)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT id, type, content FROM core_memories")
                nodes = cursor.fetchall()
                cursor.execute("SELECT source_id, target_id FROM memory_links")
                edges = cursor.fetchall()
            finally:
                conn.close()

            G = nx.Graph()
            for nid, ntype, ncontent in nodes:
                G.add_node(nid, type=ntype, content=ncontent)
            for src, tgt in edges:
                G.add_edge(src, tgt)

            layout = nx.spring_layout(G, seed=42)

            self.scene.clear()
            for nid, pos in layout.items():
                data = G.nodes[nid]
                x, y = pos[0] * 200, pos[1] * 200
                node = InteractiveNode(
                    x, y, 15, nid, data.get("content", ""),
                    data.get("type", "fact"), self.display_node_data,
                )
                self.scene.addItem(node)
            for src, tgt in G.edges():
                if src in layout and tgt in layout:
                    x1, y1 = layout[src][0] * 200, layout[src][1] * 200
                    x2, y2 = layout[tgt][0] * 200, layout[tgt][1] * 200
                    self.scene.addLine(x1 + 15, y1 + 15, x2 + 15, y2 + 15)

        except Exception as e:
            logger.debug(f"Graph load failed: {e}")
