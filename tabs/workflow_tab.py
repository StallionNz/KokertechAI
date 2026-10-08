import threading
from typing import TYPE_CHECKING
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QProgressBar, QSplitter, QTreeWidget, QTreeWidgetItem
)

if TYPE_CHECKING:
    from tabs.context import DashboardContext


class WorkflowTabMixin:

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

    def create_workflow_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        header = QHBoxLayout()
        title = QLabel("⚡ Workflow Engine")
        title.setStyleSheet("font-weight: bold; color: #8B5CF6; font-size: 12pt;")
        header.addWidget(title)
        header.addStretch()
        btn_run = QPushButton("▶ Run Workflow")
        btn_run.clicked.connect(self._workflow_run)
        header.addWidget(btn_run)
        btn_stop = QPushButton("⏹ Stop")
        btn_stop.clicked.connect(self._workflow_stop)
        header.addWidget(btn_stop)
        layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # YAML editor
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("YAML Definition:"))
        self.workflow_editor = QTextEdit()
        self.workflow_editor.setPlaceholderText(
            "name: My Workflow\nsteps:\n  - name: research\n    action: RESEARCH_TOPIC\n    params: {query: \"AI trends\"}\n  - name: summarize\n    action: sub_agent\n    params: {persona: Researcher, task: \"summarize\"}\n    depends_on: [research]"
        )
        self.workflow_editor.setStyleSheet("font-family: Consolas, monospace; font-size: 10pt;")
        left_layout.addWidget(self.workflow_editor)
        splitter.addWidget(left)

        # Results
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.workflow_progress = QProgressBar()
        right_layout.addWidget(self.workflow_progress)

        self.workflow_status = QLabel("Ready")
        self.workflow_status.setStyleSheet("color: #9CA3AF;")
        right_layout.addWidget(self.workflow_status)

        self.workflow_tree = QTreeWidget()
        self.workflow_tree.setHeaderLabels(["Step", "Status", "Output"])
        right_layout.addWidget(self.workflow_tree)

        splitter.addWidget(right)
        splitter.setSizes([500, 400])
        layout.addWidget(splitter)

        self._wf_stop = threading.Event()
        return tab

    def _workflow_run(self):
        yaml_text = self.workflow_editor.toPlainText().strip()
        if not yaml_text:
            return
        try:
            from workflow_engine import execute_workflow_async, parse_yaml
            wf = parse_yaml(yaml_text)
        except (ValueError, TypeError, KeyError, RuntimeError, OSError) as e:
            self.workflow_status.setText(f"Parse error: {e}")
            return

        self.workflow_tree.clear()
        self.workflow_progress.setValue(0)
        self.workflow_status.setText(f"Running: {wf.name}")
        self._wf_stop.clear()

        def on_progress(done, total, step_name):
            # Also update the progress tab if available
            if hasattr(self, "on_workflow_progress"):
                self.on_workflow_progress(done, total, step_name)
            QTimer.singleShot(0, lambda: self.workflow_progress.setValue(
                int(done / total * 100)))

        def on_done(result):
            QTimer.singleShot(0, lambda: self._workflow_done(result))

        self._wf_thread = execute_workflow_async(
            wf, progress_callback=on_progress, done_callback=on_done,
            stop_event=self._wf_stop)

    def _workflow_stop(self):
        self._wf_stop.set()
        self.workflow_status.setText("Stopping...")

    def _workflow_done(self, result):
        self.workflow_progress.setValue(100)
        self.workflow_status.setText(
            f"Workflow '{result.workflow_name}' — {result.overall}")
        self.workflow_tree.clear()
        for step in result.steps:
            item = QTreeWidgetItem([
                step.step_name,
                "✅" if step.status == "success" else ("❌" if step.status == "failed" else "⏭"),
                (step.output or step.error)[:120]
            ])
            self.workflow_tree.addTopLevelItem(item)
