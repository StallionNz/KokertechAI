"""Neural Knowledge Graph tab mixin — interactive graph with legend, clusters, expand, and search."""
import os
import math
import sqlite3
from collections import defaultdict
from typing import TYPE_CHECKING
from PyQt6.QtCore import Qt, QVariantAnimation, QEasingCurve, QThread, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QSplitter, QInputDialog, QMessageBox, QLineEdit, QDialog, QDialogButtonBox,
    QListWidget, QListWidgetItem, QFrame, QComboBox, QSpinBox, QCheckBox,
    QFileDialog, QGraphicsScene
)
from PyQt6.QtGui import QColor, QPen, QImage, QPainter
import json
from config import WORKSPACE_DIR
from ui_components import (
    GraphLayoutWorker, ZoomableGraphicsView, MiniMapView,
    NodeGraphicsItem, TextLabelItem, ClusterRegionItem, EdgeItem
)
from logging_config import get_logger

if TYPE_CHECKING:
    from tabs.context import DashboardContext

logger = get_logger(name="NeuralGraphTab")

# ── Node type → color mapping (shared legend source) ──────────────────────
NODE_TYPE_COLORS = {
    "system":         ("#EF4444", "System"),
    "tool":           ("#F59E0B", "Tool"),
    "os":             ("#8B5CF6", "OS"),
    "hardware":       ("#10B981", "Hardware"),
    "fact":           ("#EAB308", "Fact"),
    "interaction":    ("#06B6D4", "Interaction"),
    "document_chunk": ("#EC4899", "Document"),
}
TYPE_FALLBACK = "#3B82F6"

# ── Relationship type → color mapping for edge rendering ─────────────────
RELATIONSHIP_COLORS = {
    "RELATES_TO":   "#3B82F6",   # blue
    "REFERENCES":   "#8B5CF6",   # purple
    "DERIVED_FROM": "#10B981",   # green
    "SEMANTIC_LINK": "#F59E0B",  # amber
    "CAUSES":        "#EF4444",  # red
    "DEPENDS_ON":    "#EC4899",  # pink
    "CONTAINS":      "#06B6D4",  # cyan
    "PART_OF":       "#84CC16",  # lime
    "FOLLOWS_UP":    "#F97316",  # orange
}
REL_FALLBACK_COLOR = "#6B7280"  # gray for unknown types


def _get_rel_color(rel_type: str) -> str:
    """Return color hex for a relationship type, with fallback."""
    if not rel_type:
        return REL_FALLBACK_COLOR
    return RELATIONSHIP_COLORS.get(rel_type.upper(), REL_FALLBACK_COLOR)


class AutoLinkWorker(QThread):
    """Worker thread for computing semantic auto-link suggestions."""

    suggestions_ready = pyqtSignal(list)
    error_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)

    def __init__(self, threshold: float = 0.5, max_results: int = 15, parent=None):
        super().__init__(parent)
        self.threshold = threshold
        self.max_results = max_results

    def run(self):
        try:
            self.status_signal.emit("🔗 Computing semantic link suggestions…")
            import auto_linker
            suggestions = auto_linker.suggest_links(
                threshold=self.threshold,
                max_results=self.max_results,
            )
            self.suggestions_ready.emit(suggestions)
        except ImportError:
            self.error_signal.emit("❌ auto_linker module not available.")
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError, AttributeError, KeyError, IndexError) as e:
            logger.error(f"AutoLinkWorker failed: {e}")
            self.error_signal.emit(f"❌ Auto-link failed: {e}")


def _get_positions_db_path() -> str:
    """Resolve vault DB path, respecting test-patched WORKSPACE_DIR if altered."""
    candidate = os.path.join(WORKSPACE_DIR, "kokertech_vault.db")
    import config
    if WORKSPACE_DIR != getattr(config, "WORKSPACE_DIR", None):
        return candidate
    import memory_vault
    return getattr(memory_vault, "DB_PATH", candidate)


class NeuralGraphTabMixin:
    """Mixin that adds the Neural Knowledge Graph tab to the dashboard."""

    _get_positions_db_path = staticmethod(_get_positions_db_path)

    @property
    def context(self) -> "DashboardContext":
        """Return the shared DashboardContext, falling back to legacy attributes if ctx is unset."""
        if hasattr(self, "ctx") and self.ctx is not None:
            return self.ctx
        from tabs.context import DashboardContext
        return DashboardContext(
            controller=getattr(self, "controller", None),
            file_logger=getattr(self, "file_logger", None),
            log_to_audit=getattr(self, "log_to_audit", None),
            audit_signal=getattr(self, "audit_signal", None),
            config=getattr(self, "config", {}),
            restore_chat_input=getattr(self, "restore_chat_input", None),
            switch_tab=getattr(self, "switch_tab", None),
        )

    @context.setter
    def context(self, value: "DashboardContext") -> None:
        self.ctx = value

    # ── Tab construction ──────────────────────────────────────────────────
    def create_knowledge_graph_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Top bar ───────────────────────────────────────────────────────
        top_bar = QHBoxLayout()
        title = QLabel("🧠 Neural Memory Knowledge Graph")
        title.setStyleSheet("font-weight: bold; color: #10B981; font-size: 12pt;")
        top_bar.addWidget(title)
        top_bar.addStretch()

        # Search
        self.graph_search = QLineEdit()
        self.graph_search.setPlaceholderText("🔍 Filter nodes…")
        self.graph_search.setFixedWidth(180)
        self.graph_search.textChanged.connect(self._on_graph_search)
        top_bar.addWidget(self.graph_search)

        self.graph_search_count = QLabel("")
        self.graph_search_count.setStyleSheet("color: #9CA3AF; font-size: 9pt; padding: 0 4px;")
        top_bar.addWidget(self.graph_search_count)

        self.btn_auto_link = QPushButton("🔗 AUTO-LINK")
        self.btn_auto_link.setToolTip("Suggest and apply semantic links between disconnected nodes")
        self.btn_auto_link.clicked.connect(self._action_auto_link)
        top_bar.addWidget(self.btn_auto_link)

        self.btn_show_all = QPushButton("⊞ Show All")
        self.btn_show_all.setToolTip("Show all nodes after an expand")
        self.btn_show_all.clicked.connect(self._show_all_nodes)
        self.btn_show_all.setVisible(False)
        top_bar.addWidget(self.btn_show_all)

        refresh_btn = QPushButton("⟳ RENDER")
        refresh_btn.clicked.connect(self.render_knowledge_graph)
        top_bar.addWidget(refresh_btn)

        self.btn_unfocus = QPushButton("✦ Clear Focus")
        self.btn_unfocus.setToolTip("Clear multi-hop neighborhood illumination (Esc)")
        self.btn_unfocus.clicked.connect(self.clear_subgraph_highlight)
        top_bar.addWidget(self.btn_unfocus)

        # ── Zoom controls ─────────────────────────────────────────────────
        zoom_sep = QFrame()
        zoom_sep.setFrameShape(QFrame.Shape.VLine)
        zoom_sep.setStyleSheet("border: 1px solid #444; margin: 2px 4px;")
        top_bar.addWidget(zoom_sep)

        self.zoom_out_btn = QPushButton("−")
        self.zoom_out_btn.setFixedWidth(28)
        self.zoom_out_btn.setToolTip("Zoom out (Ctrl+−)")
        self.zoom_out_btn.clicked.connect(self._zoom_out)
        top_bar.addWidget(self.zoom_out_btn)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(44)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setStyleSheet(
            "color: #10B981; font-weight: bold; font-size: 9pt; "
            "background: #1A1A1A; border: 1px solid #333; border-radius: 3px; "
            "padding: 1px 4px;"
        )
        top_bar.addWidget(self.zoom_label)

        self.zoom_in_btn = QPushButton("+")
        self.zoom_in_btn.setFixedWidth(28)
        self.zoom_in_btn.setToolTip("Zoom in (Ctrl++)")
        self.zoom_in_btn.clicked.connect(self._zoom_in)
        top_bar.addWidget(self.zoom_in_btn)

        self.zoom_reset_btn = QPushButton("⊡ 1:1")
        self.zoom_reset_btn.setToolTip("Reset zoom to 100%")
        self.zoom_reset_btn.clicked.connect(self._zoom_reset)
        top_bar.addWidget(self.zoom_reset_btn)

        # Fit to View button
        self.fit_view_btn = QPushButton("⊞ Fit")
        self.fit_view_btn.setToolTip("Fit all nodes into the viewport")
        self.fit_view_btn.clicked.connect(self._fit_to_view)
        self.fit_view_btn.setStyleSheet(
            "font-weight: bold; font-size: 8pt; padding: 1px 6px;"
        )
        top_bar.addWidget(self.fit_view_btn)

        export_btn = QPushButton("📷 Export PNG")
        export_btn.setToolTip("Save the current graph view as a PNG image")
        export_btn.clicked.connect(self._export_graph_as_image)
        top_bar.addWidget(export_btn)

        # Sprint 13 #25: JSON export/import buttons
        export_json_btn = QPushButton("📦 Export JSON")
        export_json_btn.setToolTip("Export the knowledge graph data as JSON")
        export_json_btn.clicked.connect(self._export_graph_json)
        top_bar.addWidget(export_json_btn)

        import_json_btn = QPushButton("📥 Import JSON")
        import_json_btn.setToolTip("Import a knowledge graph from a JSON file")
        import_json_btn.clicked.connect(self._import_graph_json)
        top_bar.addWidget(import_json_btn)

        # Sprint 13 #12: Community detection button
        community_btn = QPushButton("🏘️ Communities")
        community_btn.setToolTip("Detect and display entity communities/clusters")
        community_btn.clicked.connect(self._show_communities)
        top_bar.addWidget(community_btn)
        layout.addLayout(top_bar)

        # ── Splitter: graph | side panel ──────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.graph_scene = QGraphicsScene()
        self.graph_view = ZoomableGraphicsView(self.graph_scene)
        self.graph_view.zoom_changed.connect(self._on_zoom_changed)
        self.graph_view.escape_pressed.connect(self.clear_subgraph_highlight)
        self.graph_view.background_clicked.connect(self.clear_subgraph_highlight)

        # Create mini-map as a child overlay widget of the graph view
        self.graph_minimap = MiniMapView(self.graph_scene, self.graph_view)
        self.graph_minimap.setParent(self.graph_view)
        self.graph_minimap.hide()  # hidden until graph has nodes
        self.graph_view.set_minimap(self.graph_minimap)

        splitter.addWidget(self.graph_view)

        # Connect type toggle keyboard shortcuts
        self.graph_view.type_toggle_signal.connect(self._on_type_shortcut)

        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(4, 4, 4, 4)

        # Inspector panel
        self.graph_info_panel = QTextEdit()
        self.graph_info_panel.setReadOnly(True)
        self.graph_info_panel.setFixedWidth(320)
        self.graph_info_panel.setMaximumHeight(200)
        self.graph_info_panel.setHtml(
            "<h3>Memory Inspector</h3><p>Click on any node to view its neural payload.</p>"
        )
        side_layout.addWidget(self.graph_info_panel)

        # Action buttons
        btn_row = QHBoxLayout()
        self.btn_edit_node = QPushButton("✏️ Edit Memory")
        self.btn_edit_node.setEnabled(False)
        self.btn_edit_node.clicked.connect(self.action_edit_node)
        self.btn_delete_node = QPushButton("🗑️ Delete Node")
        self.btn_delete_node.setStyleSheet(
            "background-color: #EF4444; color: white; font-weight: bold;"
        )
        self.btn_delete_node.setEnabled(False)
        self.btn_delete_node.clicked.connect(self.action_delete_node)
        btn_row.addWidget(self.btn_edit_node)
        btn_row.addWidget(self.btn_delete_node)
        side_layout.addLayout(btn_row)

        # Expand button
        self.btn_expand_node = QPushButton("🔍 Expand Subgraph")
        self.btn_expand_node.setEnabled(False)
        self.btn_expand_node.setToolTip("Show only this node and its direct connections")
        self.btn_expand_node.clicked.connect(self._expand_current_node)
        side_layout.addWidget(self.btn_expand_node)

        # Separator + Add Node
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("border: 1px solid #333;")
        side_layout.addWidget(sep)

        add_node_btn = QPushButton("➕ Add New Node")
        add_node_btn.setStyleSheet(
            "background-color: #10B981; color: white; font-weight: bold;"
        )
        add_node_btn.setToolTip("Create a new memory node in the knowledge graph")
        add_node_btn.clicked.connect(self._action_add_node)
        side_layout.addWidget(add_node_btn)

        link_nodes_btn = QPushButton("🔗 Link Nodes")
        link_nodes_btn.setStyleSheet(
            "background-color: #8B5CF6; color: white; font-weight: bold;"
        )
        link_nodes_btn.setToolTip("Connect two existing nodes with a relationship")
        link_nodes_btn.clicked.connect(self._action_link_nodes)
        side_layout.addWidget(link_nodes_btn)

        # ── Node Type Legend (clickable toggles) ────────────────────────
        self._type_visibility = {type_name: True for type_name in NODE_TYPE_COLORS}

        legend_header = QHBoxLayout()
        legend_header.addWidget(QLabel("── Node Type Toggles ──"))
        legend_header.addStretch()
        btn_collapse_all = QPushButton("⊟ Hide All")
        btn_collapse_all.setFixedWidth(70)
        btn_collapse_all.setStyleSheet("font-size: 8pt; padding: 1px 4px;")
        btn_collapse_all.clicked.connect(self._collapse_all_types)
        legend_header.addWidget(btn_collapse_all)
        btn_show_all_types = QPushButton("⊞ Show All")
        btn_show_all_types.setFixedWidth(70)
        btn_show_all_types.setStyleSheet("font-size: 8pt; padding: 1px 4px;")
        btn_show_all_types.clicked.connect(self._show_all_types)
        legend_header.addWidget(btn_show_all_types)
        side_layout.addLayout(legend_header)

        # Hide clusters checkbox
        hide_clusters_row = QHBoxLayout()
        self._hide_clusters_cb = QCheckBox("Hide clusters for hidden types")
        self._hide_clusters_cb.setChecked(False)
        self._hide_clusters_cb.setStyleSheet("color: #9CA3AF; font-size: 8pt;")
        self._hide_clusters_cb.toggled.connect(self._on_hide_clusters_toggled)
        hide_clusters_row.addWidget(self._hide_clusters_cb)
        hide_clusters_row.addStretch()
        side_layout.addLayout(hide_clusters_row)

        self._type_count_labels = {}  # type_name -> QLabel (count badge)
        self._type_checkboxes = {}      # type_name -> QCheckBox (for shortcut toggles)

        legend_frame = QFrame()
        legend_frame.setStyleSheet("background: #1A1A1A; border: 1px solid #333; border-radius: 4px;")
        legend_layout = QVBoxLayout(legend_frame)
        legend_layout.setContentsMargins(6, 6, 6, 6)
        legend_layout.setSpacing(2)

        for i, (type_name, (hex_color, display_name)) in enumerate(sorted(NODE_TYPE_COLORS.items())):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            cb = QCheckBox()
            cb.setChecked(True)
            cb.setToolTip(f"Toggle visibility of {display_name} nodes (Alt+{i + 1})")
            cb.stateChanged.connect(
                lambda checked, t=type_name: self._on_type_toggle(t, checked)
            )
            cb.setStyleSheet(
                f"QCheckBox::indicator::unchecked {{ "
                f"  border: 2px solid {hex_color}; border-radius: 3px; "
                f"  width: 10px; height: 10px; background: #1A1A1A; "
                f"}}"
                f"QCheckBox::indicator::checked {{ "
                f"  border: 2px solid {hex_color}; border-radius: 3px; "
                f"  width: 10px; height: 10px; background: {hex_color}; "
                f"}}"
            )
            row.addWidget(cb)
            row.addSpacing(4)
            label = QLabel(f"{display_name}  [Alt+{i + 1}]")
            label.setStyleSheet("color: #D1D5DB; font-size: 9pt;")
            row.addWidget(label)
            row.addStretch()
            # Count badge
            count_label = QLabel("0")
            count_label.setStyleSheet(
                f"color: {hex_color}; font-size: 8pt; font-weight: bold; "
                f"background: #2A2A2A; border-radius: 6px; "
                f"padding: 0px 5px; min-width: 12px;"
            )
            row.addWidget(count_label)
            self._type_count_labels[type_name] = count_label
            self._type_checkboxes[type_name] = cb
            legend_layout.addLayout(row)

        side_layout.addWidget(legend_frame)

        # ── Relationship Type Legend ──────────────────────────────────────
        side_layout.addWidget(QLabel("── Relationship Colors ──"))
        rel_legend_frame = QFrame()
        rel_legend_frame.setStyleSheet("background: #1A1A1A; border: 1px solid #333; border-radius: 4px;")
        rel_legend_layout = QVBoxLayout(rel_legend_frame)
        rel_legend_layout.setContentsMargins(6, 6, 6, 6)
        rel_legend_layout.setSpacing(3)

        for rel_name, hex_color in sorted(RELATIONSHIP_COLORS.items()):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            line_indicator = QFrame()
            line_indicator.setFixedSize(22, 4)
            line_indicator.setStyleSheet(
                f"background-color: {hex_color}; border: none; border-radius: 1px;"
            )
            row.addWidget(line_indicator)
            row.addSpacing(4)
            label = QLabel(rel_name.replace("_", " ").title())
            label.setStyleSheet("color: #D1D5DB; font-size: 9pt;")
            row.addWidget(label)
            row.addStretch()
            rel_legend_layout.addLayout(row)

        side_layout.addWidget(rel_legend_frame)
        side_layout.addStretch()

        splitter.addWidget(side_panel)
        layout.addWidget(splitter)

        # ── State ─────────────────────────────────────────────────────────
        self.current_node_id = None
        self.current_node_content = ""
        self._expand_focus_id = None          # node_id being expanded
        self._expand_connected_ids: set[int] = set()
        self._active_highlight_node_id: int | None = None  # node_id for multi-hop illumination
        self._has_search_active = False
        self._collapsed_clusters: set[str] = set()  # type names currently collapsed
        self._cluster_collapse_labels: dict[str, TextLabelItem] = {}  # type -> summary label

        # Performance: store edge/label references for O(1) drag updates
        self._edge_items = {}       # (min_id, max_id, rel_type) -> EdgeItem
        self._label_items = {}      # node_id -> TextLabelItem

        # Ensure graph_positions table exists
        self._ensure_positions_table()

        self.render_knowledge_graph()
        return tab

    # ── Position persistence ────────────────────────────────────────────
    def _ensure_positions_table(self):
        """Create the graph_positions table if it doesn't exist."""
        db_path = _get_positions_db_path()
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=15.0)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS graph_positions ("
                "  node_id INTEGER PRIMARY KEY,"
                "  pos_x REAL NOT NULL,"
                "  pos_y REAL NOT NULL"
                ")"
            )
            conn.commit()
        except (sqlite3.Error, OSError) as e:
            logger.warning(f"Graph-position table write failed: {e}")
        finally:
            if conn:
                conn.close()

    def _save_node_position(self, node_id, x, y):
        """Persist a node's position to the graph_positions table."""
        db_path = _get_positions_db_path()
        conn = None
        try:
            conn = sqlite3.connect(db_path, timeout=15.0)
            conn.execute(
                "INSERT OR REPLACE INTO graph_positions (node_id, pos_x, pos_y) "
                "VALUES (?, ?, ?)",
                (node_id, round(x, 1), round(y, 1)),
            )
            conn.commit()
        except (sqlite3.Error, OSError) as e:
            logger.warning(f"Graph-position table write failed: {e}")
        finally:
            if conn:
                conn.close()

    # ── Rendering lifecycle ───────────────────────────────────────────────
    def render_knowledge_graph(self):
        """Trigger a full graph render from the database in a background thread."""
        self.graph_scene.clear()
        self._reset_inspector()
        self.btn_show_all.setVisible(False)
        self._expand_focus_id = None
        self._collapsed_clusters.clear()
        self._cluster_collapse_labels.clear()
        db_path = self._get_positions_db_path() if hasattr(self, "_get_positions_db_path") else None
        self.layout_worker = GraphLayoutWorker(db_path=db_path)
        self.layout_worker.layout_ready_signal.connect(self._on_layout_ready)
        self.layout_worker.finished.connect(self.layout_worker.deleteLater)
        self.layout_worker.start()

    def _on_layout_ready(self, pos, nodes, links):
        """Called on the main thread when the layout worker finishes."""
        self._cached_pos = pos
        self._cached_nodes = nodes
        self._cached_links = links
        self._update_type_counts(nodes)
        self.draw_knowledge_graph(pos, nodes, links)

    def _update_type_counts(self, nodes):
        """Update count badges for each type based on the current node set."""
        counts = defaultdict(int)
        for data in nodes.values():
            counts[data.get("type", "")] += 1
        for type_name, label in self._type_count_labels.items():
            count = counts.get(type_name, 0)
            label.setText(str(count))
            label.setVisible(count > 0)

    def draw_knowledge_graph(self, pos, nodes, links):
        """Render nodes, edges, cluster backgrounds into the scene."""
        self.graph_scene.clear()
        self._edge_items.clear()
        self._label_items.clear()
        if not nodes:
            return

        # ── Cluster backgrounds ───────────────────────────────────────────
        type_groups = defaultdict(list)
        for nid, data in nodes.items():
            if nid in pos:
                type_groups[data["type"]].append((nid, pos[nid]))

        # Store which nodes belong to which types for use later
        self._type_nodes: dict[str, list[int]] = defaultdict(list)
        for nid, data in nodes.items():
            self._type_nodes[data["type"]].append(nid)

        for type_name, points_with_ids in type_groups.items():
            points = [p for _, p in points_with_ids]
            if len(points) < 1:
                continue
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            margin = 60
            rect = (
                min(xs) - margin, min(ys) - margin,
                max(xs) - min(xs) + margin * 2,
                max(ys) - min(ys) + margin * 2,
            )
            hex_color = NODE_TYPE_COLORS.get(type_name, (TYPE_FALLBACK, type_name))[0]
            is_collapsed = type_name in self._collapsed_clusters
            count = len(points)
            region = ClusterRegionItem(
                *rect, hex_color, type_name=type_name,
                count=count, collapsed=is_collapsed,
                click_callback=self._toggle_cluster,
            )
            region.setZValue(-5)
            self.graph_scene.addItem(region)

            # Add centralized cluster summary label when collapsed
            label = self._cluster_collapse_labels.get(type_name)
            if label:
                # Clean up old labels from previous redraw
                self.graph_scene.removeItem(label)
                del self._cluster_collapse_labels[type_name]
                label = None

            if is_collapsed:
                cx = (min(xs) + max(xs)) / 2
                cy = (min(ys) + max(ys)) / 2
                display_name = NODE_TYPE_COLORS.get(type_name, ("", type_name))[1]
                summary_text = f"⊞ {display_name} ({count})\nDouble-click to expand"
                label = TextLabelItem(summary_text, bg_color="#222222", text_color=hex_color)
                label.setPos(cx - label.boundingRect().width() / 2,
                             cy - label.boundingRect().height() / 2)
                label.setZValue(10)
                self.graph_scene.addItem(label)
                self._cluster_collapse_labels[type_name] = label

        # ── Edges ──────────────────────────────────────────────────────────
        # Group links by (source, target) pair for offset calculation
        # Then render one edge per relationship type, each with its own color
        # Skip edges where either endpoint is in a collapsed cluster
        collapsed_node_ids = set()
        for type_name in self._collapsed_clusters:
            collapsed_node_ids.update(self._type_nodes.get(type_name, []))

        pair_links = defaultdict(list)
        for link in links:
            if isinstance(link, (tuple, list)) and len(link) >= 2:
                src, tgt = link[0], link[1]
                rel = str(link[2]) if len(link) > 2 and link[2] is not None else "RELATES_TO"
                weight = float(link[3]) if len(link) > 3 and link[3] is not None else 1.0
                conf = float(link[4]) if len(link) > 4 and link[4] is not None else weight
            elif isinstance(link, dict):
                src = link.get("source_id", link.get("source"))
                tgt = link.get("target_id", link.get("target"))
                rel = str(link.get("relation_type", link.get("relationship_type", "RELATES_TO")))
                weight = float(link.get("weight", 1.0))
                conf = float(link.get("confidence", weight))
            else:
                continue

            if src in pos and tgt in pos:
                if src in collapsed_node_ids or tgt in collapsed_node_ids:
                    continue
                pair = (min(src, tgt), max(src, tgt))
                pair_links[pair].append((src, tgt, rel, weight, conf))

        base_style = Qt.PenStyle.DashLine if self._expand_focus_id else Qt.PenStyle.SolidLine
        for pair, edge_list in pair_links.items():
            a, b = pair
            p1, p2 = pos[a], pos[b]
            n = len(edge_list)

            for i, (_src, _tgt, rel, weight, conf) in enumerate(edge_list):
                rel_color = _get_rel_color(rel)

                # Perpendicular offset for multiple edges between same pair
                if n > 1:
                    dx = p2[0] - p1[0]
                    dy = p2[1] - p1[1]
                    length = math.hypot(dx, dy)
                    offset = (i - (n - 1) / 2) * 10.0
                    if length > 0:
                        nx = -dy / length * offset
                        ny = dx / length * offset
                    else:
                        nx = ny = 0.0
                    sx = p1[0] + nx
                    sy = p1[1] + ny
                    ex = p2[0] + nx
                    ey = p2[1] + ny
                else:
                    nx = ny = 0.0
                    sx, sy = p1[0], p1[1]
                    ex, ey = p2[0], p2[1]

                # Realm 5 Dynamic Edge Styling: weight thickness & temporal decay fading
                pen_width = max(1.0, min(8.0, 1.2 + (weight - 0.5) * 1.5))
                alpha = max(60, min(255, int(130 + min(weight, 2.5) * 45)))
                color = QColor(rel_color)
                color.setAlpha(alpha)

                link_pen = QPen(color, pen_width)
                link_pen.setStyle(base_style)

                line = EdgeItem(sx, sy, ex, ey, a, b, rel_type=rel, weight=weight, confidence=conf)
                line._offset_x = nx  # store for drag repositioning
                line._offset_y = ny
                line.default_pen = QPen(link_pen)
                line.setPen(link_pen)
                line.setZValue(-1)
                line.setToolTip(f"{rel} | Weight: {weight:.2f} | Confidence: {conf:.2f}")
                self.graph_scene.addItem(line)
                self._edge_items[(a, b, rel)] = line

        # ── Pre-compute expand-connected IDs ─────────────────────────────
        expand_connected = set()
        if self._expand_focus_id:
            expand_connected.add(self._expand_focus_id)
            for link in links:
                if isinstance(link, (tuple, list)) and len(link) >= 2:
                    src, tgt = link[0], link[1]
                elif isinstance(link, dict):
                    src, tgt = link.get("source_id", link.get("source")), link.get("target_id", link.get("target"))
                else:
                    continue
                if src == self._expand_focus_id:
                    expand_connected.add(tgt)
                if tgt == self._expand_focus_id:
                    expand_connected.add(src)
        self._expand_connected_ids = expand_connected

        # ── Nodes ─────────────────────────────────────────────────────────
        for nid, data in nodes.items():
            if nid not in pos:
                continue

            # Skip rendering individual nodes if this type is collapsed
            node_type = data["type"]
            if node_type in self._collapsed_clusters:
                continue

            x, y = pos[nid]
            r = 25
            node_item = NodeGraphicsItem(
                x - r, y - r, r * 2, r * 2,
                nid, data["content"], node_type,
                self._on_node_click, self.action_edit_node, self.action_delete_node,
                expand_callback=self._expand_current_node,
                link_callback=self._action_link_nodes,
                position_changed_callback=self._on_node_dragged,
            )
            self.graph_scene.addItem(node_item)

            label_text = (
                data["content"][:15] + "…"
                if len(data["content"]) > 15
                else data["content"]
            )
            text_item = TextLabelItem(f"[{nid}] {label_text}")
            text_item.setPos(x - r, y + r + 4)
            self.graph_scene.addItem(text_item)
            self._label_items[nid] = text_item

            # Apply expand / search state
            if self._expand_focus_id:
                visible = nid in expand_connected
                node_item.setVisible(visible)
                text_item.setVisible(visible)

        self.graph_scene.setSceneRect(self.graph_scene.itemsBoundingRect())
        self.graph_view.fitInView(
            self.graph_scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio
        )

        # Show mini-map when there are nodes, sync viewport indicator
        if nodes and any(nid in pos for nid in nodes):
            self.graph_minimap.show()
            self.graph_view._emit_viewport_rect()
        else:
            self.graph_minimap.hide()

        # Apply search highlight if text is present
        self._apply_search_highlight()
        # Apply type filter LAST so it isn't overridden by search
        self._apply_type_filter()

    # ── Zoom controls ────────────────────────────────────────────────────
    def _on_zoom_changed(self, zoom_factor: float):
        """Update the zoom label when the view zoom changes and apply LOD culling."""
        pct = int(round(zoom_factor * 100))
        if hasattr(self, "zoom_label") and self.zoom_label:
            self.zoom_label.setText(f"{pct}%")
            # Color-code: green for normal, yellow for very zoomed in, cyan for zoomed out
            if pct < 50:
                color = "#06B6D4"  # cyan for far zoomed out
            elif pct > 200:
                color = "#F59E0B"  # amber for very zoomed in
            else:
                color = "#10B981"  # green for normal range
            self.zoom_label.setStyleSheet(
                f"color: {color}; font-weight: bold; font-size: 9pt; "
                "background: #1A1A1A; border: 1px solid #333; border-radius: 3px; "
                "padding: 1px 4px;"
            )
        if hasattr(self, "_update_lod") and callable(self._update_lod):
            self._update_lod(zoom_factor)

    def _update_lod(self, zoom_factor: float) -> None:
        """Apply Level-of-Detail (LOD) culling: hide micro labels during wide zoom-out (<0.4x)."""
        hide_labels = zoom_factor < 0.4
        label_items = getattr(self, "_label_items", {})
        if not label_items:
            return
        expand_focus = getattr(self, "_expand_focus_id", None)
        expand_connected = getattr(self, "_expand_connected_ids", set())
        has_search = getattr(self, "_has_search_active", False)
        search_query = self.graph_search.text().strip().lower() if has_search and hasattr(self, "graph_search") else ""

        for nid, text_label in label_items.items():
            try:
                if hide_labels:
                    text_label.setVisible(False)
                else:
                    if expand_focus is not None:
                        text_label.setVisible(nid in expand_connected)
                    elif has_search and search_query:
                        text_label.setVisible(search_query in text_label.toPlainText().lower())
                    else:
                        text_label.setVisible(True)
            except (RuntimeError, AttributeError):
                pass

    def _zoom_in(self):
        """Zoom the graph view in by one step."""
        if hasattr(self, 'graph_view'):
            self.graph_view.zoom_in()

    def _zoom_out(self):
        """Zoom the graph view out by one step."""
        if hasattr(self, 'graph_view'):
            self.graph_view.zoom_out()

    def _zoom_reset(self):
        """Reset the graph view zoom to 100%."""
        if hasattr(self, 'graph_view'):
            self.graph_view.reset_zoom()

    def _fit_to_view(self):
        """Zoom and pan to show all nodes in the viewport."""
        if not hasattr(self, 'graph_view') or not hasattr(self, 'graph_scene'):
            return
        scene_rect = self.graph_scene.itemsBoundingRect()
        if not scene_rect.isEmpty():
            self.graph_view.fitInView(
                scene_rect, Qt.AspectRatioMode.KeepAspectRatio
            )
            self.graph_view._emit_viewport_rect()
            self.graph_view._emit_zoom_level()

    # ── Export graph as PNG image ───────────────────────────────────────
    def _export_graph_as_image(self):
        """Save the current graph view as a PNG image file."""
        if not hasattr(self, "_cached_pos") or not self._cached_pos:
            QMessageBox.information(self, "Export", "No graph to export.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Graph as Image",
            os.path.join(WORKSPACE_DIR, "knowledge_graph.png"),
            "PNG Images (*.png)"
        )
        if not path:
            return

        scene_rect = self.graph_scene.sceneRect()
        if scene_rect.isEmpty():
            QMessageBox.warning(self, "Export", "Graph scene is empty.")
            return

        # Render at 2x resolution for crisp output
        scale = 2.0
        img_w = int(scene_rect.width() * scale)
        img_h = int(scene_rect.height() * scale)
        image = QImage(img_w, img_h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#1A1A1A").rgb())  # dark background matching the theme

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.graph_scene.render(painter, target=image.rect(), source=scene_rect)
        painter.end()

        try:
            image.save(path, "PNG")
            self.context.log(f"📷 Graph exported to {os.path.basename(path)}")
        except (OSError, RuntimeError) as e:
            QMessageBox.critical(self, "Export Failed", f"Could not save image: {e}")
            self.context.log(f"⚠️ Export failed: {e}")

    # ── Node drag-to-reposition ─────────────────────────────────────────
    def _on_node_dragged(self, node_id, new_center_x, new_center_y):
        """Called when a node is dragged to a new position.
        Updates cached positions, connected edge lines, the label,
        and persists the position to the database.
        """
        if not hasattr(self, "_cached_pos"):
            return

        # Throttle DB writes: only persist every 10th call during a drag
        # (checked via a simple counter stored on the method)
        self._drag_call_count = getattr(self, '_drag_call_count', 0) + 1
        should_persist = self._drag_call_count % 10 == 0

        # Update cached position
        self._cached_pos[node_id] = (new_center_x, new_center_y)

        r = 25

        # Reposition all edges connected to this node
        for _key, edge_item in self._edge_items.items():
            if edge_item.source_id == node_id or edge_item.target_id == node_id:
                src_pos = self._cached_pos.get(edge_item.source_id)
                tgt_pos = self._cached_pos.get(edge_item.target_id)
                if src_pos and tgt_pos:
                    ox = getattr(edge_item, '_offset_x', 0)
                    oy = getattr(edge_item, '_offset_y', 0)
                    edge_item.setLine(
                        src_pos[0] + ox, src_pos[1] + oy,
                        tgt_pos[0] + ox, tgt_pos[1] + oy,
                    )

        # Reposition the label for this node
        label_item = self._label_items.get(node_id)
        if label_item:
            label_item.setPos(new_center_x - r, new_center_y + r + 4)

        # Persist to DB (throttled to avoid excessive writes)
        if should_persist:
            self._save_node_position(node_id, new_center_x, new_center_y)

    def _find_line_item(self, nid1, nid2):
        """Find the first EdgeItem connecting two nodes."""
        for _key, item in self._edge_items.items():
            pair = (min(nid1, nid2), max(nid1, nid2))
            if (item.source_id == pair[0] and item.target_id == pair[1]) or \
               (item.source_id == pair[1] and item.target_id == pair[0]):
                return item
        return None

    # ── Node interaction & Subgraph Illumination ──────────────────────────
    def _reset_inspector(self):
        self.current_node_id = None
        self.current_node_content = ""
        if hasattr(self, "btn_edit_node") and self.btn_edit_node:
            self.btn_edit_node.setEnabled(False)
        if hasattr(self, "btn_delete_node") and self.btn_delete_node:
            self.btn_delete_node.setEnabled(False)
        if hasattr(self, "btn_expand_node") and self.btn_expand_node:
            self.btn_expand_node.setEnabled(False)
        if hasattr(self, "clear_subgraph_highlight") and callable(self.clear_subgraph_highlight):
            self.clear_subgraph_highlight()
        if hasattr(self, "graph_info_panel") and self.graph_info_panel:
            self.graph_info_panel.setHtml(
                "<h3>Memory Inspector</h3><p>Click on any node to view its neural payload.</p>"
            )

    def highlight_subgraph(self, node_id: int, max_hops: int = 2) -> dict:
        """Illuminate the multi-hop neighborhood for a node with background dimming."""
        if node_id is None:
            return {}

        subgraph: dict = {}
        try:
            import memory_vault
            if hasattr(memory_vault, "traverse_subgraph"):
                subgraph = memory_vault.traverse_subgraph(node_id, max_hops=max_hops)
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError, KeyError) as e:
            logger.debug(f"traverse_subgraph failed for node {node_id}: {e}")

        visited_nodes: dict[int, int] = {node_id: 0}
        traversed_edge_pairs: set[tuple[int, int]] = set()

        if subgraph and isinstance(subgraph, dict):
            for n_info in subgraph.get("nodes", []):
                if isinstance(n_info, dict) and "id" in n_info:
                    visited_nodes[n_info["id"]] = n_info.get("hop", 1)
            for l_info in subgraph.get("links", []):
                if isinstance(l_info, dict) and "source_id" in l_info and "target_id" in l_info:
                    traversed_edge_pairs.add(
                        (min(l_info["source_id"], l_info["target_id"]),
                         max(l_info["source_id"], l_info["target_id"]))
                    )

        if not traversed_edge_pairs and hasattr(self, "_cached_links"):
            for link in getattr(self, "_cached_links", []):
                if isinstance(link, (tuple, list)) and len(link) >= 2:
                    s, t = link[0], link[1]
                elif isinstance(link, dict):
                    s, t = link.get("source_id", link.get("source")), link.get("target_id", link.get("target"))
                else:
                    continue
                if s == node_id or t == node_id:
                    visited_nodes[s] = 1 if s != node_id else 0
                    visited_nodes[t] = 1 if t != node_id else 0
                    traversed_edge_pairs.add((min(s, t), max(s, t)))

        if hasattr(self, "graph_scene") and self.graph_scene is not None:
            for item in self.graph_scene.items():
                try:
                    if isinstance(item, NodeGraphicsItem):
                        nid = item.node_id
                        if nid == node_id:
                            item.setOpacity(1.0)
                            item.setPen(QPen(QColor("#F59E0B"), 5.0))
                        elif nid in visited_nodes and visited_nodes[nid] == 1:
                            item.setOpacity(1.0)
                            item.setPen(QPen(QColor("#10B981"), 3.5))
                        elif nid in visited_nodes and visited_nodes[nid] >= 2:
                            item.setOpacity(0.85)
                            item.setPen(QPen(QColor("#8B5CF6"), 2.5))
                        else:
                            item.setOpacity(0.20)
                            item.setPen(QPen(QColor(40, 40, 40), 4.0))

                    elif isinstance(item, EdgeItem):
                        pair = (min(item.source_id, item.target_id), max(item.source_id, item.target_id))
                        if pair in traversed_edge_pairs or (item.source_id in visited_nodes and item.target_id in visited_nodes):
                            item.setOpacity(1.0)
                            current_pen = item.pen()
                            current_pen.setWidthF(max(3.0, current_pen.widthF() + 1.5))
                            item.setPen(current_pen)
                        else:
                            item.setOpacity(0.12)

                    elif isinstance(item, TextLabelItem):
                        is_connected = any(f"[{vid}]" in item.toPlainText() for vid in visited_nodes)
                        item.setOpacity(1.0 if is_connected else 0.20)

                    elif isinstance(item, ClusterRegionItem):
                        item.setOpacity(0.30)
                except (RuntimeError, AttributeError):
                    pass

        self._active_highlight_node_id = node_id
        return subgraph

    def clear_subgraph_highlight(self) -> None:
        """Restore full opacity and standard styling for all graph items."""
        if hasattr(self, "graph_scene") and self.graph_scene is not None:
            for item in self.graph_scene.items():
                try:
                    item.setOpacity(1.0)
                    if isinstance(item, NodeGraphicsItem):
                        item.setPen(QPen(QColor(40, 40, 40), 4.0))
                    elif isinstance(item, EdgeItem):
                        if getattr(item, "default_pen", None) is not None:
                            item.setPen(QPen(item.default_pen))
                except (RuntimeError, AttributeError):
                    pass

        self._active_highlight_node_id = None

    def _on_node_click(self, node_id, type_label, content):
        """Handle node click — inspect, illuminate multi-hop subgraph, and enable expand."""
        self.current_node_id = node_id
        self.current_node_content = content
        if hasattr(self, "btn_edit_node") and self.btn_edit_node:
            self.btn_edit_node.setEnabled(True)
        if hasattr(self, "btn_delete_node") and self.btn_delete_node:
            self.btn_delete_node.setEnabled(True)
        if hasattr(self, "btn_expand_node") and self.btn_expand_node:
            self.btn_expand_node.setEnabled(True)
        if hasattr(self, "highlight_subgraph") and callable(self.highlight_subgraph):
            self.highlight_subgraph(node_id, max_hops=2)
        # DB-derived strings must never reach setHtml raw — escape with
        # layout preservation so multi-line memory content keeps its shape.
        from html_sanitizer import escape_message_html
        if hasattr(self, "graph_info_panel") and self.graph_info_panel:
            self.graph_info_panel.setHtml(
                f"<h3>Node ID: {escape_message_html(node_id)}</h3>"
                f"<b>Type:</b> {escape_message_html(type_label.upper())}<hr>"
                f"<p>{escape_message_html(content)}</p>"
            )

    def display_node_data(self, node_id, type_label, content):
        """Alias for external callers."""
        self._on_node_click(node_id, type_label, content)

    def action_edit_node(self):
        if not self.current_node_id:
            return
        new_content, ok = QInputDialog.getMultiLineText(
            self, "Edit Memory",
            f"Update content for Node {self.current_node_id}:",
            self.current_node_content,
        )
        if ok and new_content.strip():
            db_path = _get_positions_db_path()
            conn = None
            try:
                conn = sqlite3.connect(db_path, timeout=15.0)
                conn.execute(
                    "UPDATE core_memories SET content = ? WHERE id = ?",
                    (new_content.strip(), self.current_node_id),
                )
                conn.commit()
                self.context.log(f"✏️ Node {self.current_node_id} successfully updated.")
                self.render_knowledge_graph()
            except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
                self.context.log(f"⚠️ Edit failed: {e}")
            finally:
                if conn:
                    conn.close()

    def action_delete_node(self):
        if not self.current_node_id:
            return
        reply = QMessageBox.question(
            self, "Confirm Deletion",
            f"Permanently delete Node {self.current_node_id} and sever all links?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            deleted_id = self.current_node_id
            try:
                import memory_vault
                memory_vault.delete_memory_node(deleted_id)
                self.current_node_id = None
                self.current_node_content = None
                self.context.log(f"🗑️ Node {deleted_id} deleted.")
                self.render_knowledge_graph()
            except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
                self.context.log(f"⚠️ Delete failed: {e}")

    # ── Click-to-expand subgraph ──────────────────────────────────────────
    def _expand_current_node(self):
        """Isolate the current node and its direct neighbours with smooth fade-out animation."""
        nid = self.current_node_id
        if nid is None or not hasattr(self, "_cached_pos"):
            return

        # Cancel any in-progress animation first
        self._cancel_expand_animation()

        links = getattr(self, "_cached_links", [])
        focus = nid

        # Gather connected node IDs
        connected = {focus}
        for link in links:
            if isinstance(link, (tuple, list)) and len(link) >= 2:
                src, tgt = link[0], link[1]
            elif isinstance(link, dict):
                src, tgt = link.get("source_id", link.get("source")), link.get("target_id", link.get("target"))
            else:
                continue
            if src == focus:
                connected.add(tgt)
            if tgt == focus:
                connected.add(src)
        self._expand_connected_ids = connected

        # Separate items into hide vs keep groups
        hide_items = []
        keep_items = []
        for item in self.graph_scene.items():
            if isinstance(item, NodeGraphicsItem):
                if item.node_id in connected:
                    keep_items.append(item)
                else:
                    hide_items.append(item)
            elif isinstance(item, TextLabelItem):
                visible = any(f"[{c}]" in item.toPlainText() for c in connected)
                if visible:
                    keep_items.append(item)
                else:
                    hide_items.append(item)
            elif isinstance(item, EdgeItem):
                keep_items.append(item)

        # Ensure kept items are visible + at full opacity
        for item in keep_items:
            item.setVisible(True)
            item.setOpacity(1.0)

        def on_fade_complete():
            self._expand_focus_id = focus
            self.btn_show_all.setVisible(True)
            # Fit view to remaining visible items
            self.graph_view.fitInView(
                self.graph_scene.itemsBoundingRect(),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
            self.graph_view._emit_viewport_rect()

        self._animate_fade_out(hide_items, on_finished=on_fade_complete)

    def _toggle_cluster(self, type_name: str):
        """Toggle collapse/expand of a node-type cluster.

        When collapsed: individual nodes are hidden and a summary label is shown.
        When expanded: all nodes are shown again.
        """
        if type_name in self._collapsed_clusters:
            self._collapsed_clusters.discard(type_name)
        else:
            self._collapsed_clusters.add(type_name)

        # Re-render with the updated collapsed state
        if hasattr(self, "_cached_pos"):
            self.draw_knowledge_graph(self._cached_pos, self._cached_nodes, self._cached_links)
            # Update the type checkbox labels for collapsed indicators
            self._update_type_cluster_indicators()
            self.graph_view._emit_viewport_rect()

    def _update_type_cluster_indicators(self):
        """Update the legend checkbox label colors to indicate cluster state."""
        for type_name, cb in self._type_checkboxes.items():
            hex_color = NODE_TYPE_COLORS.get(type_name, (TYPE_FALLBACK, type_name))[0]
            if type_name in self._collapsed_clusters:
                # Preserve indicator styling, add amber label hint
                cb.setStyleSheet(
                    f"color: #F59E0B; "
                    f"QCheckBox::indicator::unchecked {{ "
                    f"  border: 2px solid {hex_color}; border-radius: 3px; "
                    f"  width: 10px; height: 10px; background: #1A1A1A; "
                    f"}}"
                    f"QCheckBox::indicator::checked {{ "
                    f"  border: 2px solid {hex_color}; border-radius: 3px; "
                    f"  width: 10px; height: 10px; background: {hex_color}; "
                    f"}}"
                )
            else:
                # Reset to normal (no amber tint)
                cb.setStyleSheet(
                    f"QCheckBox::indicator::unchecked {{ "
                    f"  border: 2px solid {hex_color}; border-radius: 3px; "
                    f"  width: 10px; height: 10px; background: #1A1A1A; "
                    f"}}"
                    f"QCheckBox::indicator::checked {{ "
                    f"  border: 2px solid {hex_color}; border-radius: 3px; "
                    f"  width: 10px; height: 10px; background: {hex_color}; "
                    f"}}"
                )

    def _redraw_expanded(self):
        """Re-apply the expand filter to the already-rendered scene (instant, no animation)."""
        if not self._expand_focus_id or not hasattr(self, "_cached_pos"):
            return
        links = getattr(self, "_cached_links", [])
        focus = self._expand_focus_id

        # Gather connected node IDs
        connected = {focus}
        for src, tgt, _ in links:
            if src == focus:
                connected.add(tgt)
            if tgt == focus:
                connected.add(src)

        for item in self.graph_scene.items():
            if isinstance(item, NodeGraphicsItem):
                item.setVisible(item.node_id in connected)
                item.setOpacity(1.0)
            elif isinstance(item, TextLabelItem):
                visible = any(f"[{c}]" in item.toPlainText() for c in connected)
                item.setVisible(visible)
                item.setOpacity(1.0)
            elif isinstance(item, EdgeItem):
                item.setVisible(True)
                item.setOpacity(1.0)

        self.graph_view.fitInView(
            self.graph_scene.itemsBoundingRect(), Qt.AspectRatioMode.KeepAspectRatio
        )
        self.graph_view._emit_viewport_rect()

    # ── Smooth animation transitions ─────────────────────────────────────
    def _cancel_expand_animation(self):
        """Cancel any in-progress expand/show-all animation."""
        if hasattr(self, '_active_animations'):
            for anim in self._active_animations[:]:
                anim.stop()
                anim.deleteLater()
            self._active_animations.clear()

    def _animate_fade_out(self, items, on_finished=None):
        """Animate a list of scene items fading to opacity 0, then hide them."""
        if not items:
            if on_finished:
                on_finished()
            return

        self._cancel_expand_animation()

        anim = QVariantAnimation()
        anim.setDuration(250)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        if not hasattr(self, '_active_animations'):
            self._active_animations = []
        self._active_animations.append(anim)

        def on_value(value):
            for item in items:
                try:
                    item.setOpacity(value)
                except RuntimeError:
                    pass  # item may have been deleted

        def on_finish():
            for item in items:
                try:
                    item.setVisible(False)
                    item.setOpacity(1.0)  # reset for next show
                except RuntimeError:
                    pass
            if on_finished:
                on_finished()
            if anim in self._active_animations:
                self._active_animations.remove(anim)
            anim.deleteLater()

        anim.valueChanged.connect(on_value)
        anim.finished.connect(on_finish)
        anim.start()

    def _animate_fade_in(self, items, on_finished=None):
        """Animate a list of scene items fading from opacity 0 to 1."""
        if not items:
            if on_finished:
                on_finished()
            return

        self._cancel_expand_animation()

        anim = QVariantAnimation()
        anim.setDuration(300)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        if not hasattr(self, '_active_animations'):
            self._active_animations = []
        self._active_animations.append(anim)

        def on_value(value):
            for item in items:
                try:
                    item.setOpacity(value)
                except RuntimeError:
                    pass

        def on_finish():
            for item in items:
                try:
                    item.setOpacity(1.0)
                except RuntimeError:
                    pass
            if on_finished:
                on_finished()
            if anim in self._active_animations:
                self._active_animations.remove(anim)
            anim.deleteLater()

        anim.valueChanged.connect(on_value)
        anim.finished.connect(on_finish)
        anim.start()

    # ── Type collapse / filter ───────────────────────────────────────────
    def _on_type_shortcut(self, index):
        """Handle Alt+number shortcut for type visibility toggles."""
        type_names = sorted(NODE_TYPE_COLORS.keys())
        if 0 <= index < len(type_names):
            type_name = type_names[index]
            cb = self._type_checkboxes.get(type_name)
            if cb:
                cb.blockSignals(True)
                cb.setChecked(not cb.isChecked())
                cb.blockSignals(False)
                self._on_type_toggle(type_name, cb.isChecked())

    def _on_type_toggle(self, type_name, checked):
        """Called when a type checkbox is toggled in the legend."""
        self._type_visibility[type_name] = bool(checked)
        # When re-showing a type, re-render to restore visibility
        # When hiding, the fast filter is sufficient
        if self._type_visibility[type_name]:
            self._redraw_with_filters()
        else:
            self._apply_type_filter()

    def _collapse_all_types(self):
        """Hide all node types via checkboxes."""
        for t in self._type_visibility:
            self._type_visibility[t] = False
        self._sync_type_checkboxes(checked=False)
        self._apply_type_filter()

    def _on_hide_clusters_toggled(self, checked):
        """Called when the 'Hide clusters' checkbox changes state."""
        if not hasattr(self, '_cached_pos'):
            return
        if checked:
            self._apply_type_filter()
        else:
            self._redraw_with_filters()

    def _show_all_types(self):
        """Show all node types via checkboxes."""
        for t in self._type_visibility:
            self._type_visibility[t] = True
        self._sync_type_checkboxes(checked=True)
        self._redraw_with_filters()

    def _sync_type_checkboxes(self, checked: bool):
        """Sync all type checkbox widgets to match _type_visibility state."""
        for child in self.findChildren(QCheckBox):
            child.blockSignals(True)
            child.setChecked(checked)
            child.blockSignals(False)

    def _apply_type_filter(self):
        """Apply current type visibility to the scene, respecting expand/search state."""
        if not hasattr(self, "_cached_pos"):
            return
        for item in self.graph_scene.items():
            if isinstance(item, NodeGraphicsItem):
                node_type = getattr(item, "type_label", "")
                type_visible = self._type_visibility.get(node_type, True)
                # Don't override search/expand visibility — only add type constraint
                if not type_visible:
                    item.setVisible(False)
            elif isinstance(item, TextLabelItem):
                # Hide labels of hidden nodes — will be handled by label visibility logic
                pass
            elif isinstance(item, EdgeItem):
                src_type = self._get_node_type(item.source_id)
                tgt_type = self._get_node_type(item.target_id)
                edge_visible = (
                    self._type_visibility.get(src_type, True) and
                    self._type_visibility.get(tgt_type, True)
                )
                if not edge_visible:
                    item.setVisible(False)
            elif isinstance(item, ClusterRegionItem):
                # Optionally hide clusters for hidden types
                if getattr(self, '_hide_clusters_cb', None) and self._hide_clusters_cb.isChecked():
                    cluster_type = getattr(item, 'type_name', '')
                    if not self._type_visibility.get(cluster_type, True):
                        item.setVisible(False)

    def _get_node_type(self, node_id):
        """Get the type label for a node ID from the cached nodes dict."""
        if hasattr(self, "_cached_nodes") and node_id in self._cached_nodes:
            return self._cached_nodes[node_id].get("type", "")
        return ""

    def _redraw_with_filters(self):
        """Re-render the graph from cached data, applying all active filters.
        Used when toggling a type back on to restore visibility.
        """
        if hasattr(self, "_cached_pos"):
            self.draw_knowledge_graph(self._cached_pos, self._cached_nodes, self._cached_links)
            self.graph_view._emit_viewport_rect()

    def _show_all_nodes(self):
        """Reset expand and show all nodes with a smooth fade-in animation."""
        if not hasattr(self, "_cached_pos"):
            return

        # Cancel any in-progress animation
        self._cancel_expand_animation()

        self._expand_focus_id = None
        self.btn_show_all.setVisible(False)

        # Full redraw — all items are created at full opacity
        self.draw_knowledge_graph(self._cached_pos, self._cached_nodes, self._cached_links)

        # Collect all newly-drawn nodes and labels, start them at opacity 0
        fade_items = [
            item for item in self.graph_scene.items()
            if isinstance(item, (NodeGraphicsItem, TextLabelItem))
        ]
        for item in fade_items:
            item.setOpacity(0.0)

        def on_fade_complete():
            for item in fade_items:
                try:
                    item.setOpacity(1.0)
                except RuntimeError:
                    pass
            self.graph_view._emit_viewport_rect()

        self._animate_fade_in(fade_items, on_finished=on_fade_complete)

    # ── Search / filter ───────────────────────────────────────────────────
    def _on_graph_search(self, text):
        """Filter graph nodes by search text. Shows count and highlights."""
        search = text.strip().lower()
        self._has_search_active = bool(search)

        match_count = 0
        total_count = 0
        for item in self.graph_scene.items():
            if isinstance(item, NodeGraphicsItem):
                total_count += 1
                content_match = search in item.content.lower() if search else True
                label_match = search in str(item.node_id) if search else True
                visible = content_match or label_match if search else True
                item.setVisible(visible)

                # Highlight effect: brighter outline for matches
                if search and visible:
                    item.setPen(QPen(QColor("#FFFFFF"), 4.0))
                    match_count += 1
                else:
                    item.setPen(QPen(QColor(40, 40, 40), 4.0))

        # Toggle labels and edges
        for item in self.graph_scene.items():
            if isinstance(item, TextLabelItem):
                if search:
                    item.setVisible(
                        search in item.toPlainText().lower()
                    )
                else:
                    item.setVisible(
                        self._expand_focus_id is None or
                        any(f"[{c}]" in item.toPlainText() for c in [self._expand_focus_id])
                        if self._expand_focus_id else True
                    )
            elif isinstance(item, EdgeItem):
                item.setVisible(
                    not (search and not self._expand_focus_id)
                )

        # Update count label
        if search:
            self.graph_search_count.setText(f"{match_count}/{total_count}")
            self.graph_search_count.setStyleSheet(
                f"color: {'#10B981' if match_count > 0 else '#EF4444'}; "
                f"font-size: 9pt; font-weight: bold; padding: 0 4px;"
            )
        else:
            self.graph_search_count.setText("")

    def _apply_search_highlight(self):
        """Re-apply search highlight after a redraw."""
        text = self.graph_search.text().strip()
        if text:
            self._on_graph_search(text)

    # ── Add Node ──────────────────────────────────────────────────────────
    def _action_add_node(self):
        """Open a dialog to create a new memory node in the knowledge graph."""
        dialog = QDialog(self)
        dialog.setWindowTitle("➕ Add New Memory Node")
        dialog.resize(480, 380)
        layout = QVBoxLayout()
        dialog.setLayout(layout)

        # Type selector
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Node Type:"))
        type_combo = QComboBox()
        type_labels = list(NODE_TYPE_COLORS.keys())
        type_combo.addItems(type_labels)
        type_combo.setCurrentIndex(type_labels.index("fact"))  # default to Fact
        type_layout.addWidget(type_combo)
        type_layout.addStretch()
        layout.addLayout(type_layout)

        # Importance
        imp_layout = QHBoxLayout()
        imp_layout.addWidget(QLabel("Importance (1–10):"))
        imp_spin = QSpinBox()
        imp_spin.setMinimum(1)
        imp_spin.setMaximum(10)
        imp_spin.setValue(5)
        imp_layout.addWidget(imp_spin)
        imp_layout.addStretch()
        layout.addLayout(imp_layout)

        # Content
        layout.addWidget(QLabel("Content:"))
        content_edit = QTextEdit()
        content_edit.setPlaceholderText(
            "Enter the memory content for this node…"
        )
        content_edit.setMinimumHeight(120)
        layout.addWidget(content_edit)

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        node_type = type_combo.currentText()
        importance = imp_spin.value()
        content = content_edit.toPlainText().strip()

        if not content:
            QMessageBox.warning(self, "Empty Content", "Please enter content for the new node.")
            return

        try:
            import memory_vault
            node_id = memory_vault.store_memory(
                content=content, node_type=node_type, importance=importance
            )
            if node_id < 1:
                QMessageBox.critical(self, "Error", "Failed to create memory node.")
                return
            self.context.log(
                f"➕ Created node #{node_id} [{node_type}] (importance={importance})"
            )
            self.render_knowledge_graph()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            QMessageBox.critical(self, "Error", f"Failed to create node: {e}")
            self.context.log(f"⚠️ Node creation failed: {e}")

    # ── Link Nodes (manual) ──────────────────────────────────────────────
    def _action_link_nodes(self):
        """Open a dialog to connect two existing nodes with a relationship type."""
        # Gather available nodes — prefer cache, fall back to DB
        nodes = getattr(self, "_cached_nodes", None)
        if not nodes:
            import memory_vault
            db_path = getattr(memory_vault, "DB_PATH", os.path.join(WORKSPACE_DIR, "kokertech_vault.db"))
            conn = None
            try:
                conn = sqlite3.connect(db_path, timeout=15.0)
                cursor = conn.execute(
                    "SELECT id, node_type, content FROM core_memories ORDER BY id"
                )
                nodes = {
                    row[0]: {"type": str(row[1]) if row[1] else "fact", "content": str(row[2])}
                    for row in cursor.fetchall()
                }
            except (sqlite3.Error, OSError) as e:
                QMessageBox.critical(self, "Error", f"Could not load nodes: {e}")
                return
            finally:
                if conn:
                    conn.close()

        if not nodes or len(nodes) < 2:
            QMessageBox.information(
                self, "Link Nodes",
                "Need at least 2 nodes in the graph to create a link."
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("🔗 Link Two Nodes")
        dialog.resize(460, 260)
        layout = QVBoxLayout()
        dialog.setLayout(layout)

        # Source node
        layout.addWidget(QLabel("Source Node:"))
        source_combo = QComboBox()
        # Default to currently selected node if any
        default_idx = 0
        for i, (nid, data) in enumerate(sorted(nodes.items())):
            preview = data["content"][:40].replace("\n", " ")
            label = f"{nid}: {preview}…  [{data['type']}]" if len(data["content"]) > 40 else f"{nid}: {preview}  [{data['type']}]"
            source_combo.addItem(label, nid)
            if nid == self.current_node_id:
                default_idx = i
        source_combo.setCurrentIndex(default_idx)
        layout.addWidget(source_combo)

        # Target node
        layout.addWidget(QLabel("Target Node:"))
        target_combo = QComboBox()
        target_default = 1 if default_idx == 0 else 0  # different from source default
        for _i, (nid, data) in enumerate(sorted(nodes.items())):
            preview = data["content"][:40].replace("\n", " ")
            label = f"{nid}: {preview}…  [{data['type']}]" if len(data["content"]) > 40 else f"{nid}: {preview}  [{data['type']}]"
            target_combo.addItem(label, nid)
        target_combo.setCurrentIndex(min(target_default, target_combo.count() - 1))
        layout.addWidget(target_combo)

        # Relationship type
        rel_layout = QHBoxLayout()
        rel_layout.addWidget(QLabel("Relationship:"))
        rel_combo = QComboBox()
        rel_combo.setEditable(True)
        rel_combo.addItems([
            "RELATES_TO", "REFERENCES", "DERIVED_FROM",
            "SEMANTIC_LINK", "CAUSES", "DEPENDS_ON",
            "CONTAINS", "PART_OF", "FOLLOWS_UP",
        ])
        rel_combo.setCurrentText("RELATES_TO")
        rel_layout.addWidget(rel_combo, 1)
        layout.addLayout(rel_layout)

        # Buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        source_id = source_combo.currentData()
        target_id = target_combo.currentData()
        rel_type = rel_combo.currentText().strip().upper().replace(" ", "_")

        if source_id == target_id:
            QMessageBox.warning(self, "Invalid Link", "Source and target must be different nodes.")
            return

        if not rel_type:
            rel_type = "RELATES_TO"

        try:
            import memory_vault
            memory_vault.link_memories(source_id, target_id, rel_type)
            self.context.log(f"🔗 Linked node #{source_id} → #{target_id} ({rel_type})")
            self.render_knowledge_graph()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            QMessageBox.critical(self, "Error", f"Failed to create link: {e}")
            self.context.log(f"⚠️ Link failed: {e}")

    # ── Auto-link ─────────────────────────────────────────────────────────
    def _action_auto_link(self):
        """Run auto-link suggestions in a background QThread."""
        if getattr(self, "_auto_link_worker", None) is not None and self._auto_link_worker.isRunning():
            self.context.log("⏳ Auto-link is already running…")
            return

        if hasattr(self, "btn_auto_link") and self.btn_auto_link:
            self.btn_auto_link.setEnabled(False)
            self.btn_auto_link.setText("⏳ LINKING…")

        self.context.log("🔗 Computing semantic link suggestions…")

        worker = AutoLinkWorker(parent=self if isinstance(self, QObject) else None)
        self._auto_link_worker = worker
        worker.status_signal.connect(self._on_auto_link_status)
        worker.suggestions_ready.connect(self._on_auto_link_suggestions_ready)
        worker.error_signal.connect(self._on_auto_link_error)
        worker.finished.connect(self._on_auto_link_finished)
        worker.start()

    def _on_auto_link_status(self, msg: str):
        self.context.log(msg)

    def _on_auto_link_error(self, err_msg: str):
        self.context.log(err_msg)
        if isinstance(self, QWidget):
            QMessageBox.warning(self, "Auto-Link Error", err_msg)

    def _on_auto_link_finished(self):
        if hasattr(self, "btn_auto_link") and self.btn_auto_link:
            self.btn_auto_link.setEnabled(True)
            self.btn_auto_link.setText("🔗 AUTO-LINK")
        self._auto_link_worker = None

    def _on_auto_link_suggestions_ready(self, suggestions):
        self._show_auto_link_dialog(suggestions)

    def _show_auto_link_dialog(self, suggestions):
        if not suggestions:
            if isinstance(self, QWidget):
                QMessageBox.information(
                    self, "Auto-Link",
                    "No new link suggestions found. All nodes may already be connected.",
                )
            self.context.log("🔗 No new link suggestions.")
            return

        dialog = QDialog(self if isinstance(self, QWidget) else None)
        dialog.setWindowTitle("Auto-Link Suggestions")
        dialog.resize(600, 400)
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        layout.addWidget(QLabel(f"Found {len(suggestions)} suggested links:\nSelect which to apply:"))

        list_widget = QListWidget()
        for s in suggestions:
            source_content = (s.get("source_content") or "").strip().replace("\n", " ")
            target_content = (s.get("target_content") or "").strip().replace("\n", " ")
            score = s.get("score", 0.0)
            item = QListWidgetItem(
                f"[{score:.2f}] Node #{s.get('source_id')}: {source_content[:60]}…\n"
                f"       ↔ Node #{s.get('target_id')}: {target_content[:60]}…"
            )
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, s)
            list_widget.addItem(item)
        layout.addWidget(list_widget)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        applied = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                applied.append(item.data(Qt.ItemDataRole.UserRole))
        if not applied:
            return

        try:
            import auto_linker
            count = auto_linker.apply_suggestions(applied)
            self.context.log(f"🔗 Applied {count} semantic links.")
            if hasattr(self, "render_knowledge_graph"):
                self.render_knowledge_graph()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            self.context.log(f"❌ Failed to apply semantic links: {e}")
            if isinstance(self, QWidget):
                QMessageBox.critical(self, "Error", f"Failed to apply links: {e}")


    # -- Sprint 13 #25: Export knowledge graph as JSON --
    def _export_graph_json(self):
        """Export the knowledge graph data as a JSON file."""
        try:
            import memory_vault
            data = memory_vault.export_graph()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            QMessageBox.critical(self, 'Export Failed', f'Could not export graph: {e}')
            return
        path, _ = QFileDialog.getSaveFileName(
            self, 'Export Knowledge Graph JSON',
            os.path.join(WORKSPACE_DIR, 'knowledge_graph.json'),
            'JSON Files (*.json)'
        )
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
            entity_count = data.get('entity_count', 0)
            rel_count = data.get('relationship_count', 0)
            self.context.log(f'Exported KG to {os.path.basename(path)} ({entity_count} entities, {rel_count} relationships)')
        except (OSError, ValueError, RuntimeError) as e:
            QMessageBox.critical(self, 'Export Failed', f'Could not write file: {e}')

    # -- Sprint 13 #25: Import knowledge graph from JSON --
    def _import_graph_json(self):
        """Import a knowledge graph from a JSON file."""
        path, _ = QFileDialog.getOpenFileName(
            self, 'Import Knowledge Graph JSON',
            WORKSPACE_DIR,
            'JSON Files (*.json)'
        )
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            QMessageBox.critical(self, 'Import Failed', f'Could not read file: {e}')
            return
        entity_count = len(data.get('entities', []))
        rel_count = len(data.get('relationships', []))
        reply = QMessageBox.question(
            self, 'Confirm Import',
            f'Import {entity_count} entities and {rel_count} relationships?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            import memory_vault
            result = memory_vault.import_graph(data)
            self.context.log(f'Imported KG: {result}')
            self.render_knowledge_graph()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            QMessageBox.critical(self, 'Import Failed', f'Could not import graph: {e}')

    # -- Sprint 13 #12: Show community detection results --
    def _show_communities(self):
        """Detect and display entity communities in the knowledge graph."""
        try:
            import memory_vault
            communities = memory_vault.detect_communities(min_cluster_size=3)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as e:
            QMessageBox.warning(self, 'Community Detection', f'Failed: {e}')
            return
        if not communities:
            QMessageBox.information(self, 'Communities', 'No communities found (need more connected entities).')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f'Detected {len(communities)} Communities')
        dialog.resize(500, 400)
        # Idiomatic Qt: build a layout without a parent widget arg so the
        # test harness can mock QDialog without triggering PyQt6 sip type
        # rejection on QVBoxLayout(MagicMock). Functionally equivalent to
        # QVBoxLayout(dialog).
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        tree = QListWidget()
        for comm in communities:
            names = [e.get('name', '?') for e in comm.get('entities', [])[:10]]
            entities_str = ', '.join(names)
            item = QListWidgetItem(
                f'Community {comm.get("id", 0)} ({comm.get("size", 0)} members): {entities_str}'
            )
            tree.addItem(item)
        layout.addWidget(tree)
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn_box.accepted.connect(dialog.accept)
        layout.addWidget(btn_box)
        dialog.exec()

    # ── Deterministic Lifecycle Teardown ──────────────────────────────────
    def teardown(self) -> None:
        """Cleanly terminate background workers, running animations, and clear state."""
        # 1. Stop layout worker
        worker = getattr(self, "layout_worker", None)
        if worker is not None:
            try:
                if hasattr(worker, "cancel"):
                    worker.cancel()
                if hasattr(worker, "stop"):
                    worker.stop()
                if worker.isRunning():
                    if hasattr(worker, "requestInterruption"):
                        worker.requestInterruption()
                    worker.quit()
                    worker.wait(500)
            except (RuntimeError, AttributeError):
                pass
            self.layout_worker = None

        # 2. Stop auto-link worker
        auto_worker = getattr(self, "_auto_link_worker", None)
        if auto_worker is not None:
            try:
                if auto_worker.isRunning():
                    if hasattr(auto_worker, "requestInterruption"):
                        auto_worker.requestInterruption()
                    auto_worker.quit()
                    auto_worker.wait(500)
            except (RuntimeError, AttributeError):
                pass
            self._auto_link_worker = None

        # 3. Stop animations
        if hasattr(self, "_cancel_expand_animation"):
            try:
                self._cancel_expand_animation()
            except (RuntimeError, AttributeError):
                pass

        # 4. Clear highlight & inspector
        self._active_highlight_node_id = None
        self.current_node_id = None
        self.current_node_content = ""

    def teardown_neural_graph(self) -> None:
        """Alias for teardown."""
        self.teardown()

