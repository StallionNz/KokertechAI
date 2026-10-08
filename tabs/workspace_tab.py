"""
Workspace Explorer tab mixin.
"""
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem
from config import WORKSPACE_DIR

if TYPE_CHECKING:
    from tabs.context import DashboardContext

class WorkspaceIndexerWorker(QThread):
    indexed_signal = pyqtSignal(list)
    def run(self):
        if not os.path.exists(WORKSPACE_DIR):
            self.indexed_signal.emit([])
            return
        results = []
        try:
            for item in os.listdir(WORKSPACE_DIR):
                abs_path = os.path.join(WORKSPACE_DIR, item)
                is_dir = os.path.isdir(abs_path)
                mod_time = datetime.fromtimestamp(os.path.getmtime(abs_path), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                typ_lbl = "Directory" if is_dir else "Document"
                sub_items = []
                if is_dir:
                    for sub_item in os.listdir(abs_path):
                        sub_abs_path = os.path.join(abs_path, sub_item)
                        sub_mod = datetime.fromtimestamp(os.path.getmtime(sub_abs_path), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                        sub_items.append([sub_item, "Directory" if os.path.isdir(sub_abs_path) else "Document", sub_mod])
                results.append({"main": [item, typ_lbl, mod_time], "sub": sub_items})
        except OSError:
            pass
        self.indexed_signal.emit(results)

class WorkspaceTabMixin:
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
    def create_workspace_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel(f"Bounds: {WORKSPACE_DIR}"))
        top_bar.addStretch()
        refresh_btn = QPushButton("Index Workspace")
        refresh_btn.clicked.connect(self.index_workspace_directory)
        top_bar.addWidget(refresh_btn)
        layout.addLayout(top_bar)
        self.workspace_tree = QTreeWidget()
        self.workspace_tree.setHeaderLabels(["Filename", "Type", "Last Modified"])
        layout.addWidget(self.workspace_tree)
        self.index_workspace_directory()
        return tab

    def index_workspace_directory(self):
        self.workspace_tree.clear()
        self.workspace_root_item = QTreeWidgetItem(self.workspace_tree, [WORKSPACE_DIR, "Indexing Workspace...", ""])
        self.workspace_tree.expandItem(self.workspace_root_item)
        self.indexer_worker = WorkspaceIndexerWorker()
        self.indexer_worker.indexed_signal.connect(self.populate_workspace_tree)
        self.indexer_worker.finished.connect(self.indexer_worker.deleteLater)
        self.indexer_worker.start()

    def populate_workspace_tree(self, results):
        self.workspace_root_item.setText(1, "Workspace Boundary")
        for item_data in results:
            child_item = QTreeWidgetItem(self.workspace_root_item, item_data["main"])
            for sub_data in item_data["sub"]:
                QTreeWidgetItem(child_item, sub_data)
