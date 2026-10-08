"""Document Pipeline tab mixin — drag-drop batch document processing.
Sprint 4 Stream F: enhanced with type badges, settings, folder scan, and export.
"""
import os
import threading
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
    QListWidget, QListWidgetItem, QFileDialog, QGroupBox,
    QTextEdit, QCheckBox, QSpinBox, QMessageBox, QFrame
)
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QShortcut, QKeySequence

from config import WORKSPACE_DIR


# File type icons (mirrors doc_pipeline classification)
_FILE_TYPE_ICONS = {
    'pdf':   '📕',
    'image': '🖼️',
    'text':  '📄',
    'skip':  '⏭️',
}


def _classify_file_preview(filepath: str) -> str:
    """Classify a file by extension (mirrors doc_pipeline._classify_file)."""
    import doc_pipeline as dp
    if hasattr(dp, "_classify_file"):
        return dp._classify_file(filepath)
    return "skip"


def _get_file_size_str(filepath: str) -> str:
    try:
        size = os.path.getsize(filepath)
        if size < 1024:
            return f"{size}B"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f}KB"
        return f"{size / (1024 * 1024):.1f}MB"
    except OSError:
        return "?"


class DocPipelineDropZone(QLabel):
    """A label that accepts drag-and-drop of files or folders."""
    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback
        self.setText("📂 Drop files or folders here\nor click to browse")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(90)
        self.setStyleSheet("""
            QLabel {
                border: 2px dashed #F59E0B;
                border-radius: 8px;
                padding: 16px;
                color: #9CA3AF;
                font-size: 11pt;
            }
        """)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("""
                QLabel {
                    border: 2px dashed #10B981;
                    border-radius: 8px;
                    padding: 16px;
                    color: #10B981;
                    font-size: 11pt;
                    background-color: rgba(16, 185, 129, 0.1);
                }
            """)

    def dragLeaveEvent(self, event):
        self.setStyleSheet("""
            QLabel {
                border: 2px dashed #F59E0B;
                border-radius: 8px;
                padding: 16px;
                color: #9CA3AF;
                font-size: 11pt;
            }
        """)

    def dropEvent(self, event: QDropEvent):
        self.dragLeaveEvent(event)
        paths = []
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isfile(p):
                paths.append(p)
            elif os.path.isdir(p):
                for root, _, files in os.walk(p):
                    for f in files:
                        paths.append(os.path.join(root, f))
        if paths:
            self.callback(paths)

    def mousePressEvent(self, event):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Documents to Process",
            WORKSPACE_DIR,
            "All Supported (*.pdf *.png *.jpg *.jpeg *.txt *.md *.py *.csv *.json *.log *.xml *.html *.yaml *.yml);;All Files (*)"
        )
        if paths:
            self.callback(paths)


class DocumentPipelineTabMixin:
    """Mixin that adds the Document Pipeline tab."""

    @property
    def context(self):
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
            restore_chat_input=(
                (lambda text: self.txt_input.setPlainText(text))
                if hasattr(self, "txt_input")
                else None
            ),
            switch_tab=(
                (lambda index: self.tabs.setCurrentIndex(index))
                if hasattr(self, "tabs")
                else None
            ),
        )

    @context.setter
    def context(self, value):
        self.ctx = value

    def create_document_pipeline_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Header
        header = QHBoxLayout()
        title = QLabel("📄 Document Processing Pipeline")
        title.setStyleSheet("font-weight: bold; color: #F59E0B; font-size: 12pt;")
        header.addWidget(title)
        header.addStretch()

        # Pipeline settings inline in header
        self.chk_index_to_vault = QCheckBox("Index to Vault")
        self.chk_index_to_vault.setChecked(True)
        self.chk_index_to_vault.setToolTip("Store extracted text chunks in memory vault for semantic search")
        header.addWidget(self.chk_index_to_vault)

        header.addWidget(QLabel("Chunk:"))
        self.chunk_size_spin = QSpinBox()
        self.chunk_size_spin.setRange(256, 8000)
        self.chunk_size_spin.setValue(1500)
        self.chunk_size_spin.setSuffix(" chars")
        self.chunk_size_spin.setSingleStep(256)
        self.chunk_size_spin.setFixedWidth(120)
        header.addWidget(self.chunk_size_spin)

        layout.addLayout(header)

        # Drop zone
        self.pipeline_drop_zone = DocPipelineDropZone(self._pipeline_add_files)
        layout.addWidget(self.pipeline_drop_zone)

        # File list + progress
        middle = QHBoxLayout()

        # File list with type badges + internal drag-to-reorder
        file_group = QGroupBox("Files Queued")
        file_layout = QVBoxLayout(file_group)
        self.pipeline_file_list = QListWidget()
        self.pipeline_file_list.setAlternatingRowColors(True)
        self.pipeline_file_list.setDragDropMode(
            QListWidget.DragDropMode.InternalMove
        )
        self.pipeline_file_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.pipeline_file_list.setToolTip(
            "Drag items to reorder | Del to remove | Ctrl+A to select all"
        )
        file_layout.addWidget(self.pipeline_file_list)
        # Sync backing list when items are reordered via drag-drop
        self.pipeline_file_list.model().rowsMoved.connect(
            self._pipeline_sync_files_from_list
        )

        # Count summary bar
        count_bar = QHBoxLayout()
        self.pipeline_count_label = QLabel("0 files")
        self.pipeline_count_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        count_bar.addWidget(self.pipeline_count_label)
        count_bar.addStretch()

        # Classification summary
        self.pipeline_classification_label = QLabel("")
        self.pipeline_classification_label.setStyleSheet("font-size: 8pt; color: #6B7280;")
        count_bar.addWidget(self.pipeline_classification_label)

        file_layout.addLayout(count_bar)

        # File management buttons
        btn_row = QHBoxLayout()
        btn_add_files = QPushButton("📂 Add Files")
        btn_add_files.clicked.connect(self._pipeline_browse_files)
        btn_row.addWidget(btn_add_files)
        btn_add_folder = QPushButton("📁 Add Folder")
        btn_add_folder.clicked.connect(self._pipeline_add_folder)
        btn_row.addWidget(btn_add_folder)
        btn_row.addStretch()
        btn_remove = QPushButton("✕ Remove")
        btn_remove.clicked.connect(self._pipeline_remove_selected)
        btn_row.addWidget(btn_remove)
        btn_clear = QPushButton("🗑 Clear")
        btn_clear.clicked.connect(self._pipeline_clear_files)
        btn_row.addWidget(btn_clear)
        file_layout.addLayout(btn_row)
        middle.addWidget(file_group, 2)

        # Right panel: progress + controls
        right_panel = QVBoxLayout()

        progress_group = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)
        self.pipeline_progress_bar = QProgressBar()
        self.pipeline_progress_bar.setMinimum(0)
        self.pipeline_progress_bar.setMaximum(100)
        self.pipeline_progress_bar.setValue(0)
        self.pipeline_progress_bar.setTextVisible(True)
        self.pipeline_progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #F59E0B; border-radius: 4px; text-align: center; }
            QProgressBar::chunk { background-color: #F59E0B; border-radius: 3px; }
        """)
        progress_layout.addWidget(self.pipeline_progress_bar)

        self.pipeline_status_label = QLabel("Ready — add files to begin")
        self.pipeline_status_label.setStyleSheet("color: #9CA3AF; font-size: 9pt;")
        progress_layout.addWidget(self.pipeline_status_label)

        self.pipeline_current_label = QLabel("")
        self.pipeline_current_label.setStyleSheet("color: #F59E0B; font-size: 8pt;")
        self.pipeline_current_label.setWordWrap(True)
        progress_layout.addWidget(self.pipeline_current_label)
        right_panel.addWidget(progress_group)

        # Action buttons
        ctrl_group = QGroupBox("Actions")
        ctrl_layout = QVBoxLayout(ctrl_group)
        self.btn_process = QPushButton("🚀 Process All")
        self.btn_process.setStyleSheet("font-weight: bold; padding: 10px; font-size: 10pt;")
        self.btn_process.setEnabled(False)
        self.btn_process.clicked.connect(self._pipeline_process)
        ctrl_layout.addWidget(self.btn_process)

        self.btn_pipeline_stop = QPushButton("⏹ Stop")
        self.btn_pipeline_stop.setEnabled(False)
        self.btn_pipeline_stop.clicked.connect(self._pipeline_stop)
        ctrl_layout.addWidget(self.btn_pipeline_stop)

        self.btn_export = QPushButton("💾 Export Results")
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self._pipeline_export)
        ctrl_layout.addWidget(self.btn_export)

        self.btn_retry_failed = QPushButton("🔄 Retry Failed")
        self.btn_retry_failed.setEnabled(False)
        self.btn_retry_failed.setToolTip("Re-add only failed files to the queue")
        self.btn_retry_failed.clicked.connect(self._pipeline_retry_failed)
        ctrl_layout.addWidget(self.btn_retry_failed)

        self.btn_view_graph = QPushButton("🔗 View in Graph")
        self.btn_view_graph.setEnabled(False)
        self.btn_view_graph.setToolTip("Open the Neural Graph tab to see indexed nodes")
        self.btn_view_graph.clicked.connect(self._pipeline_view_in_graph)
        ctrl_layout.addWidget(self.btn_view_graph)

        right_panel.addWidget(ctrl_group)
        right_panel.addStretch()
        middle.addLayout(right_panel, stretch=1)
        layout.addLayout(middle)

        # Results panel with stats summary
        results_group = QGroupBox("Results")
        results_layout = QVBoxLayout(results_group)

        # Stats bar
        stats_bar = QHBoxLayout()
        self.pipeline_stats_success = QLabel("✅ 0")
        self.pipeline_stats_success.setStyleSheet("font-size: 10pt; color: #10B981; font-weight: bold; padding: 2px 8px;")
        stats_bar.addWidget(self.pipeline_stats_success)
        self.pipeline_stats_skipped = QLabel("⏭ 0")
        self.pipeline_stats_skipped.setStyleSheet("font-size: 10pt; color: #FBBF24; font-weight: bold; padding: 2px 8px;")
        stats_bar.addWidget(self.pipeline_stats_skipped)
        self.pipeline_stats_errors = QLabel("❌ 0")
        self.pipeline_stats_errors.setStyleSheet("font-size: 10pt; color: #EF4444; font-weight: bold; padding: 2px 8px;")
        stats_bar.addWidget(self.pipeline_stats_errors)
        self.pipeline_stats_nodes = QLabel("📊 0 nodes")
        self.pipeline_stats_nodes.setStyleSheet("font-size: 10pt; color: #8B5CF6; font-weight: bold; padding: 2px 8px;")
        stats_bar.addWidget(self.pipeline_stats_nodes)
        stats_bar.addStretch()
        results_layout.addLayout(stats_bar)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333;")
        results_layout.addWidget(sep)

        self.pipeline_results_log = QTextEdit()
        self.pipeline_results_log.setReadOnly(True)
        self.pipeline_results_log.setMaximumHeight(130)
        self.pipeline_results_log.setStyleSheet("font-size: 9pt;")
        results_layout.addWidget(self.pipeline_results_log)
        layout.addWidget(results_group)

        self._pipeline_files = []
        self._pipeline_stop_flag = threading.Event()
        self._last_progress = None
        self._last_results = None
        self._pipeline_processed = False  # flag to track if current files are results
        self._pipeline_skip_dropzone_update = False  # prevent recursion

        # Keyboard shortcuts
        self._setup_pipeline_shortcuts()

        return tab

    def _setup_pipeline_shortcuts(self):
        """Register keyboard shortcuts for the pipeline tab."""
        del_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self)
        del_shortcut.activated.connect(self._pipeline_remove_selected)

        select_all = QShortcut(QKeySequence.StandardKey.SelectAll, self)
        select_all.activated.connect(
            lambda: self.pipeline_file_list.selectAll()
        )

    def _pipeline_sync_files_from_list(self):
        """Rebuild _pipeline_files to match the new list order after drag-reorder.
        Connected to model().rowsMoved signal for InternalMove.
        """
        ordered = []
        for i in range(self.pipeline_file_list.count()):
            item = self.pipeline_file_list.item(i)
            fp = item.data(Qt.ItemDataRole.UserRole + 1)  # stored full path
            if fp and os.path.isfile(fp):
                ordered.append(fp)
        if ordered:
            self._pipeline_files = ordered

    # -------------------------------------------------------------
    # FILE MANAGEMENT
    # -------------------------------------------------------------
    def _classify_and_label(self, filepath: str):
        """Classify a file and return (display_label, file_type)."""
        ftype = _classify_file_preview(filepath)
        icon = _FILE_TYPE_ICONS.get(ftype, '📄')
        size_str = _get_file_size_str(filepath)
        name = os.path.basename(filepath)
        return f"{icon} {name}  ({size_str})", ftype

    def _pipeline_add_files(self, paths):
        """Add files to the processing queue."""
        added = 0
        for p in paths:
            if p not in self._pipeline_files and os.path.isfile(p):
                self._pipeline_files.append(p)
                label, ftype = self._classify_and_label(p)
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, ftype)
                item.setData(Qt.ItemDataRole.UserRole + 1, p)  # store full path for reorder sync
                self.pipeline_file_list.addItem(item)
                added += 1
        self._pipeline_update_counts()
        # Update dropzone hint with file count
        self._update_dropzone_hint()

    def _update_dropzone_hint(self):
        """Update the drop zone hint text to show current file count."""
        count = len(self._pipeline_files)
        if count > 0:
            self.pipeline_drop_zone.setText(
                f"📂 {count} file{'s' if count != 1 else ''} queued\n"
                f"Drop more files or click to add"
            )
        else:
            self.pipeline_drop_zone.setText(
                "📂 Drop files or folders here\nor click to browse"
            )

    def _pipeline_browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Documents to Process",
            WORKSPACE_DIR,
            "All Supported (*.pdf *.png *.jpg *.jpeg *.txt *.md *.py *.csv *.json *.log *.xml *.html *.yaml *.yml);;All Files (*)"
        )
        if paths:
            self._pipeline_add_files(paths)

    def _pipeline_add_folder(self):
        """Recursively add all supported files from a directory."""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder to Scan",
            WORKSPACE_DIR
        )
        if not folder:
            return
        paths = []
        for root, dirs, files in os.walk(folder):
            # Filter out directories that might cause permission errors
            dirs[:] = [d for d in dirs if not d.startswith(('$', 'System Volume Information'))]
            for f in files:
                fp = os.path.join(root, f)
                try:
                    if _classify_file_preview(fp) != 'skip' and os.path.isfile(fp):
                        paths.append(fp)
                except PermissionError:
                    continue
        if paths:
            self._pipeline_add_files(paths)

    def _pipeline_clear_files(self):
        self._pipeline_files.clear()
        self.pipeline_file_list.clear()
        self._last_results = None
        self.btn_retry_failed.setEnabled(False)
        self.btn_view_graph.setEnabled(False)
        self._pipeline_processed = False
        self._pipeline_update_counts()
        self._reset_stats()
        self._update_dropzone_hint()

    def _pipeline_remove_selected(self):
        for item in self.pipeline_file_list.selectedItems():
            idx = self.pipeline_file_list.row(item)
            self.pipeline_file_list.takeItem(idx)
            if idx < len(self._pipeline_files):
                self._pipeline_files.pop(idx)
        self._pipeline_update_counts()
        self._update_dropzone_hint()

    def _pipeline_update_counts(self):
        count = len(self._pipeline_files)
        self.pipeline_count_label.setText(f"{count} file{'s' if count != 1 else ''}")
        self.btn_process.setEnabled(count > 0)

        # Classification breakdown
        type_counts = {}
        for i in range(count):
            item = self.pipeline_file_list.item(i)
            if item:
                ftype = item.data(Qt.ItemDataRole.UserRole) or 'unknown'
                type_counts[ftype] = type_counts.get(ftype, 0) + 1
        if type_counts:
            parts = [f"{_FILE_TYPE_ICONS.get(t, '?')} {n}" for t, n in sorted(type_counts.items())]
            self.pipeline_classification_label.setText(" | ".join(parts))
        else:
            self.pipeline_classification_label.setText("")

        self.pipeline_status_label.setText(
            f"Ready — {count} file{'s' if count != 1 else ''} queued" if count else "Ready — add files to begin"
        )

    def _pipeline_set_group_title(self, processed: bool):
        """Update the file list display to reflect current state."""
        self._pipeline_processed = processed
        if processed:
            count = len(self._pipeline_files)
            self.pipeline_count_label.setText(f"✅ {count} processed")
            self.pipeline_classification_label.setText("Processed — Clear to start fresh")
            self.btn_process.setEnabled(False)  # prevent re-processing
        else:
            self._pipeline_update_counts()

    def _reset_stats(self):
        self.pipeline_stats_success.setText("✅ 0")
        self.pipeline_stats_skipped.setText("⏭ 0")
        self.pipeline_stats_errors.setText("❌ 0")
        self.pipeline_stats_nodes.setText("📊 0 nodes")
        self.btn_export.setEnabled(False)

    def _update_file_list_status(self, results):
        """Update file list items with status indicators after processing."""
        for i, r in enumerate(results):
            item = self.pipeline_file_list.item(i)
            if item is None:
                break
            status_icon = {"success": "✅", "skipped": "⏭", "error": "❌"}.get(r.status, "?")
            label = item.text()
            # Replace leading icon with status icon
            if label.startswith(("📕", "🖼️", "📄", "⏭️")):
                label = status_icon + label[1:]
                item.setText(label)

    # -------------------------------------------------------------
    # PROCESSING
    # -------------------------------------------------------------
    def _pipeline_process(self):
        """Start batch processing in a background thread."""
        if not self._pipeline_files:
            return

        self._pipeline_stop_flag.clear()
        self.btn_process.setEnabled(False)
        self.btn_pipeline_stop.setEnabled(True)
        self.btn_export.setEnabled(False)
        self._reset_stats()
        self.pipeline_results_log.clear()

        fps = self._pipeline_files[:]
        chunk_size = self.chunk_size_spin.value()
        index_to_vault = self.chk_index_to_vault.isChecked()

        if hasattr(self, "context") and self.context is not None:
            self.context.log(f"🚀 Document pipeline started: {len(fps)} file(s) queued")

        import doc_pipeline

        def on_progress(progress):
            QTimer.singleShot(0, lambda: self._pipeline_update_progress(progress))

        def on_done(progress):
            QTimer.singleShot(0, lambda: self._pipeline_done(progress))

        self._pipeline_thread = doc_pipeline.process_batch_async(
            fps,
            progress_callback=on_progress,
            done_callback=on_done,
            chunk_size=chunk_size,
            index_to_vault=index_to_vault,
        )

    def _pipeline_stop(self):
        self._pipeline_stop_flag.set()
        self.pipeline_status_label.setText("Stopping...")
        self.btn_pipeline_stop.setEnabled(False)

    def _pipeline_export(self):
        """Export results to a text file."""
        if not self._last_progress:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Results",
            os.path.join(WORKSPACE_DIR, "pipeline_results.txt"),
            "Text Files (*.txt);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.pipeline_results_log.toPlainText())
            self.pipeline_status_label.setText(f"✅ Results exported to {os.path.basename(path)}")
            if hasattr(self, "context") and self.context is not None:
                self.context.log(f"💾 Results exported to {os.path.basename(path)}")
        except (OSError, ValueError, RuntimeError, TypeError) as e:
            QMessageBox.warning(self, "Export Failed", str(e))

    def _pipeline_retry_failed(self):
        """Re-add only failed files from the last batch to the queue."""
        if not self._last_results:
            return
        failed_paths = [
            r.filepath for r in self._last_results
            if r.status == 'error' and os.path.isfile(r.filepath)
        ]
        if not failed_paths:
            QMessageBox.information(self, "Retry", "No failed files to retry.")
            return

        # Reset to queue mode
        self._pipeline_clear_files()
        self._pipeline_add_files(failed_paths)
        self.pipeline_status_label.setText(f"🔄 {len(failed_paths)} failed file(s) re-queued for processing")

    def _pipeline_view_in_graph(self):
        """Switch to the Neural Graph tab to see indexed nodes.
        Uses _switch_to_tab to find the tab dynamically (not a hardcoded index).
        """
        if hasattr(self, 'knowledge_graph_tab_widget'):
            self._switch_to_tab(self.knowledge_graph_tab_widget)
        # If the graph is already visible, trigger a re-render
        if hasattr(self, 'render_knowledge_graph'):
            self.render_knowledge_graph()

    # -------------------------------------------------------------
    # PROGRESS CALLBACKS
    # -------------------------------------------------------------
    def _pipeline_update_progress(self, progress):
        self.pipeline_progress_bar.setValue(progress.percent)
        self.pipeline_status_label.setText(
            f"Processing {progress.completed}/{progress.total}"
        )
        if progress.current_file:
            self.pipeline_current_label.setText(
                f"{progress.current_step}: {progress.current_file}"
            )

    def _pipeline_done(self, progress):
        """Called when batch completes."""
        self._last_progress = progress
        self.pipeline_progress_bar.setValue(100)

        success = sum(1 for r in progress.results if r.status == 'success')
        skipped = sum(1 for r in progress.results if r.status == 'skipped')
        errors = sum(1 for r in progress.results if r.status == 'error')
        total_nodes = sum(len(r.node_ids) for r in progress.results)

        # Update stats bar
        self.pipeline_stats_success.setText(f"✅ {success}")
        self.pipeline_stats_skipped.setText(f"⏭ {skipped}")
        self.pipeline_stats_errors.setText(f"❌ {errors}")
        self.pipeline_stats_nodes.setText(f"📊 {total_nodes} nodes")

        self.pipeline_status_label.setText(
            f"✅ Done: {success} indexed, ⏭ {skipped} skipped, ❌ {errors} errors"
        )
        self.pipeline_current_label.setText("")
        if hasattr(self, "context") and self.context is not None:
            self.context.log(f"📄 Document pipeline completed: {success} indexed, {skipped} skipped, {errors} errors")

        # Update file list with status indicators
        self._update_file_list_status(progress.results)

        # Detail log
        lines = [f"=== Pipeline Results ({success+skipped+errors} total) ==="]
        for r in progress.results:
            icon = {"success": "✅", "skipped": "⏭", "error": "❌"}.get(r.status, "?")
            name = os.path.basename(r.filepath)
            if r.status == 'success':
                lines.append(f"{icon} {name} ({r.file_type}) -> {len(r.node_ids)} node(s)")
            elif r.status == 'error':
                lines.append(f"{icon} {name} ({r.file_type}) — {r.error}")
            else:
                lines.append(f"{icon} {name} ({r.file_type}) — skipped")

        # Add timing if available
        lines.append(f"\nSettings: chunk_size={self.chunk_size_spin.value()}, "
                     f"index_to_vault={self.chk_index_to_vault.isChecked()}")
        self.pipeline_results_log.setPlainText("\n".join(lines))

        self.btn_process.setEnabled(True)
        self.btn_pipeline_stop.setEnabled(False)
        self.btn_export.setEnabled(True)

        # Store results for retry / view-in-graph
        self._last_results = progress.results

        # Enable post-processing buttons
        has_errors = errors > 0
        has_nodes = total_nodes > 0
        self.btn_retry_failed.setEnabled(has_errors)
        self.btn_view_graph.setEnabled(has_nodes)

        # Refresh knowledge graph if nodes were created
        if has_nodes and hasattr(self, 'render_knowledge_graph'):
            self.render_knowledge_graph()

        # Keep processed files visible with their status indicators
        self._pipeline_set_group_title(processed=True)
