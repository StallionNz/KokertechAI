"""Computer Use tab mixin — safety controls + manual overrides (Sprint 5.6)."""
from typing import TYPE_CHECKING
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QSpinBox, QTextEdit, QGroupBox
)

if TYPE_CHECKING:
    from tabs.context import DashboardContext


class ComputerUseTabMixin:
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

    def create_computer_use_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        header = QHBoxLayout()
        title = QLabel("🖱️ Computer Use")
        title.setStyleSheet("font-weight: bold; color: #EF4444; font-size: 12pt;")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        # Safety group
        safety = QGroupBox("Safety Controls")
        safety_layout = QVBoxLayout(safety)

        self.chk_safety = QCheckBox("Enable Safety Confirmations")
        self.chk_safety.setChecked(True)
        self.chk_safety.toggled.connect(self._toggle_safety)
        safety_layout.addWidget(self.chk_safety)

        safety_info = QLabel(
            "When enabled, all computer actions (click, type, scroll) require\n"
            "manual confirmation before execution."
        )
        safety_info.setStyleSheet("color: #9CA3AF; font-size: 9pt;")
        safety_layout.addWidget(safety_info)

        layout.addWidget(safety)

        # Quick actions
        actions = QGroupBox("Quick Actions")
        actions_layout = QVBoxLayout(actions)

        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("X:"))
        self.cu_x = QSpinBox()
        self.cu_x.setRange(0, 3840)
        self.cu_x.setValue(500)
        pos_row.addWidget(self.cu_x)
        pos_row.addWidget(QLabel("Y:"))
        self.cu_y = QSpinBox()
        self.cu_y.setRange(0, 2160)
        self.cu_y.setValue(500)
        pos_row.addWidget(self.cu_y)
        actions_layout.addLayout(pos_row)

        btn_row = QHBoxLayout()
        btn_move = QPushButton("Move")
        btn_move.clicked.connect(self._cu_move)
        btn_row.addWidget(btn_move)
        btn_click = QPushButton("Click")
        btn_click.clicked.connect(self._cu_click)
        btn_row.addWidget(btn_click)
        btn_scroll = QPushButton("Scroll ↑")
        btn_scroll.clicked.connect(lambda: self._cu_scroll(3))
        btn_row.addWidget(btn_scroll)
        btn_screenshot = QPushButton("Screenshot")
        btn_screenshot.clicked.connect(self._cu_screenshot)
        btn_row.addWidget(btn_screenshot)
        btn_capture_analyze = QPushButton("📷 Capture & Analyze")
        btn_capture_analyze.setStyleSheet("font-weight: bold;")
        btn_capture_analyze.clicked.connect(self._cu_capture_analyze)
        btn_row.addWidget(btn_capture_analyze)
        actions_layout.addLayout(btn_row)

        layout.addWidget(actions)

        # Log
        log_group = QGroupBox("Activity Log")
        log_layout = QVBoxLayout(log_group)
        self.cu_log = QTextEdit()
        # Results are plain text (command output, paths, statuses). Feed
        # everything through _cu_log (insertText) — never QTextEdit.append,
        # which interprets HTML and would both eat newlines and let <...>
        # in tool output render as markup.
        self.cu_log.setReadOnly(True)
        self.cu_log.setMaximumHeight(200)
        self.cu_log.setStyleSheet("font-size: 9pt;")
        log_layout.addWidget(self.cu_log)
        layout.addWidget(log_group)

        layout.addStretch()

        # Init safety state
        self._init_safety()
        return tab

    def _init_safety(self):
        import computer_use as cu
        cu.set_confirm_callback(self._cu_confirm)

    def _cu_log(self, text):
        """Append plain text to the computer-use log."""
        from PyQt6.QtGui import QTextCursor
        cursor = self.cu_log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(str(text) + "\n")
        self.cu_log.setTextCursor(cursor)
        self.cu_log.verticalScrollBar().setValue(
            self.cu_log.verticalScrollBar().maximum()
        )

    def _toggle_safety(self, enabled):
        import computer_use as cu
        cu.set_safety(enabled)
        self._cu_log(f"Safety {'enabled' if enabled else 'DISABLED'}")

    def _cu_confirm(self, action_desc):
        from PyQt6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "⚠️ Confirm Computer Action",
            f"Allow: {action_desc}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        return reply == QMessageBox.StandardButton.Yes

    def _cu_move(self):
        import computer_use as cu
        x, y = self.cu_x.value(), self.cu_y.value()
        result = cu.move_to(x, y)
        self._cu_log(result)

    def _cu_click(self):
        import computer_use as cu
        x, y = self.cu_x.value(), self.cu_y.value()
        result = cu.click(x, y)
        self._cu_log(result)

    def _cu_scroll(self, clicks):
        import computer_use as cu
        result = cu.scroll(clicks)
        self._cu_log(result)

    def _cu_screenshot(self):
        import computer_use as cu
        result = cu.screenshot()
        self._cu_log(result)

    def _cu_capture_analyze(self):
        """Take a screenshot and analyze it with vision AI."""
        import computer_use as cu
        import os

        result = cu.screenshot()
        self._cu_log(result)

        if "✅" not in result:
            return

        # Extract the file path from the result
        path = result.replace("✅ Screenshot saved to ", "").strip()
        if not os.path.exists(path):
            self._cu_log("❌ Screenshot file not found for analysis.")
            return

        self._cu_log("🔍 Analyzing screenshot with vision AI...")
        try:
            from copilot_features import VisionAnalyzer
            vision_model = self.context.get_config("vision_model", "")
            analyzer = VisionAnalyzer(model_name=vision_model or None)
            if vision_model:
                self._cu_log(f"📡 Using vision model: {vision_model}")
            analysis = analyzer.analyze(
                image_path=path,
                context="Screen capture from Computer Use tab",
                prompt="Describe what you see on this screen in detail. List all UI elements, text content, and notable features.",
                max_tokens=512,
            )
            self._cu_log("📋 Vision Analysis:")
            self._cu_log(analysis[:1000])
        except ImportError as e:
            self._cu_log(f"❌ Vision analysis unavailable: {e}")
        except (RuntimeError, OSError, ValueError) as e:
            self._cu_log(f"❌ Analysis failed: {e}")
