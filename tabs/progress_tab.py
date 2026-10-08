# -*- coding: utf-8 -*-
"""
tabs/progress_tab.py - Real-time Execution Progress tab.

Monitors and displays live progress from:
  - AI Controller pipeline (STEP 1/5 through STEP 5/5 via log_signal)
  - RAG multi-hop retrieval (hop progress, sub-questions, sources)
  - Workflow execution (step-by-step progress bar with names)
  - Sub-agent teams (agent status cards)

Sprint 16: Progressive Streaming UI - dedicated tab for live execution.
"""

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QFrame, QScrollArea, QTextBrowser)

if TYPE_CHECKING:
    from tabs.context import DashboardContext

_COL_PIPELINE = "#3B82F6"
_COL_RAG = "#8B5CF6"
_COL_WORKFLOW = "#F59E0B"
_COL_SUBAGENT = "#10B981"
_COL_ACTIVE = "#10B981"
_COL_IDLE = "#6B7280"
_COL_ERROR = "#EF4444"
_COL_TOOL = "#F97316"

class _StatusDot(QLabel):
    """Small status indicator - green when active, gray when idle."""
    def __init__(self, parent=None):
        super().__init__(chr(0x25CF), parent)
        self._active = False
        self._color = _COL_IDLE
        self._update_style()

    def set_active(self, active, color=_COL_ACTIVE):
        self._active = active
        self._color = color if active else _COL_IDLE
        self._update_style()

    def _update_style(self):
        c = self._color
        self.setStyleSheet("font-size: 14pt; color: " + c + "; font-weight: bold;")

class _ProgressSection(QFrame):
    """Collapsible section card with header toggle."""
    def __init__(self, title, accent_color, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self.setStyleSheet(
            "_ProgressSection {"
            "background: rgba(17,24,39,0.8);"
            "border: 1px solid " + accent_color + "44;"
            "border-left: 3px solid " + accent_color + ";"
            "border-radius: 6px; margin: 2px 0;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)

        self._header = QPushButton(title)
        self._header.setFlat(True)
        self._header.setStyleSheet(
            "QPushButton { color: " + accent_color + "; font-weight: bold;"
            "font-size: 10pt; text-align: left; padding: 4px; border: none;}"
            "QPushButton:hover { color: white; }")
        self._header.clicked.connect(self._toggle)
        layout.addWidget(self._header)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(4, 0, 4, 4)
        self._content_layout.setSpacing(4)
        layout.addWidget(self._content)

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._content.setVisible(not self._collapsed)
        arrow = chr(0x25B6) if self._collapsed else chr(0x25BC)
        self._header.setText(arrow + " " + self._header.text()[2:])

    def content_layout(self):
        return self._content_layout

class ProgressTabMixin:
    """Mixin providing the Execution Progress tab."""

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

    _pipeline_signal = pyqtSignal(int, int, str)
    _rag_signal = pyqtSignal(dict)
    _workflow_signal = pyqtSignal(int, int, str)
    _subagent_signal = pyqtSignal(str, str, str)
    _progress_log_signal = pyqtSignal(str, str, str)
    _log_bridge_signal = pyqtSignal(str)
    _tool_call_signal = pyqtSignal(str, str, str)


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pipeline_step = 0
        self._pipeline_total = 5
        self._rag_hop = 0
        self._rag_total = 3
        self._rag_sub_questions = []
        self._workflow_total = 0
        self._workflow_done = 0
        self._subagents = {}
        self._tool_calls = []

        self._pipeline_signal.connect(self._on_pipeline_update)
        self._rag_signal.connect(self._on_rag_update)
        self._workflow_signal.connect(self._on_workflow_update)
        self._subagent_signal.connect(self._on_subagent_update)
        self._progress_log_signal.connect(self._on_log_line)
        self._log_bridge_signal.connect(self._on_worker_log)
        self._tool_call_signal.connect(self._on_tool_call)

    def create_progress_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header
        header = QHBoxLayout()
        title = QLabel("Execution Progress")
        title.setStyleSheet("font-size: 14pt; font-weight: bold; color: white;")
        header.addWidget(title)

        self._overall_status = _StatusDot()
        self._overall_status.set_active(False, _COL_IDLE)
        header.addWidget(self._overall_status)

        self._overall_label = QLabel("Idle")
        self._overall_label.setStyleSheet("font-size: 10pt; color: " + _COL_IDLE + ";")
        header.addWidget(self._overall_label)
        header.addStretch()

        clear_btn = QPushButton("Clear Log")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self._clear_activity_log)
        btn_style = ("QPushButton { background: #1f2937; border: 1px solid #374151;"
            " border-radius: 4px; padding: 4px 8px; color: #9CA3AF; font-size: 9pt; }"
            "QPushButton:hover { background: #374151; color: white; }")
        clear_btn.setStyleSheet(btn_style)
        header.addWidget(clear_btn)
        layout.addLayout(header)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(6)

        # Section 1: AI Pipeline
        self._pipeline_section = _ProgressSection("AI Pipeline", _COL_PIPELINE)
        pipe_layout = self._pipeline_section.content_layout()

        pipe_status = QHBoxLayout()
        self._pipe_dot = _StatusDot()
        pipe_status.addWidget(self._pipe_dot)
        self._pipe_stage = QLabel("Waiting for pipeline...")
        self._pipe_stage.setStyleSheet("font-size: 10pt; color: " + _COL_PIPELINE + ";")
        pipe_status.addWidget(self._pipe_stage, 1)
        pipe_status.addStretch()
        pipe_layout.addLayout(pipe_status)

        self._pipe_progress = QProgressBar()
        self._pipe_progress.setRange(0, 100)
        self._pipe_progress.setFormat("Step %v / 100")
        self._pipe_progress.setFixedHeight(16)
        pbar_style = ("QProgressBar { background: #1f2937; border: none;"
            " border-radius: 4px; text-align: center; font-size: 8pt;"
            " color: #9CA3AF; }"
            "QProgressBar::chunk { background-color: #3B82F6; border-radius: 4px; }")
        self._pipe_progress.setStyleSheet(pbar_style)
        pipe_layout.addWidget(self._pipe_progress)

        self._pipe_detail = QLabel("")
        self._pipe_detail.setStyleSheet("font-size: 9pt; color: #9CA3AF; padding-left: 4px;")
        self._pipe_detail.setWordWrap(True)
        pipe_layout.addWidget(self._pipe_detail)
        scroll_layout.addWidget(self._pipeline_section)

        # Section 2: RAG Multi-Hop Retrieval
        self._rag_section = _ProgressSection("RAG Multi-Hop Retrieval", _COL_RAG)
        rag_layout = self._rag_section.content_layout()

        rag_status = QHBoxLayout()
        self._rag_dot = _StatusDot()
        rag_status.addWidget(self._rag_dot)
        self._rag_stage = QLabel("No active research")
        self._rag_stage.setStyleSheet("font-size: 10pt; color: " + _COL_RAG + ";")
        rag_status.addWidget(self._rag_stage, 1)
        rag_status.addStretch()
        rag_layout.addLayout(rag_status)

        rag_stats = QHBoxLayout()
        self._rag_hops_label = QLabel("Hops: -")
        self._rag_hops_label.setStyleSheet("font-size: 9pt; color: " + _COL_RAG + ";")
        rag_stats.addWidget(self._rag_hops_label)
        rag_stats.addSpacing(16)
        self._rag_questions_label = QLabel("Sub-Questions: 0")
        self._rag_questions_label.setStyleSheet("font-size: 9pt; color: " + _COL_RAG + ";")
        rag_stats.addWidget(self._rag_questions_label)
        rag_stats.addSpacing(16)
        self._rag_sources_label = QLabel("Sources: 0")
        self._rag_sources_label.setStyleSheet("font-size: 9pt; color: " + _COL_RAG + ";")
        rag_stats.addWidget(self._rag_sources_label)
        rag_stats.addStretch()
        rag_layout.addLayout(rag_stats)

        self._rag_sub_list = QLabel("")
        self._rag_sub_list.setStyleSheet(
            "font-size: 9pt; color: #D1D5DB; padding: 4px;"
            "background: rgba(0,0,0,0.2); border-radius: 4px;")
        self._rag_sub_list.setWordWrap(True)
        self._rag_sub_list.setMaximumHeight(120)
        rag_layout.addWidget(self._rag_sub_list)
        scroll_layout.addWidget(self._rag_section)

        # Section 3: Workflow Pipeline
        self._wf_section = _ProgressSection("Workflow Pipeline", _COL_WORKFLOW)
        wf_layout = self._wf_section.content_layout()

        wf_status = QHBoxLayout()
        self._wf_dot = _StatusDot()
        wf_status.addWidget(self._wf_dot)
        self._wf_stage = QLabel("No active workflow")
        self._wf_stage.setStyleSheet("font-size: 10pt; color: " + _COL_WORKFLOW + ";")
        wf_status.addWidget(self._wf_stage, 1)
        wf_status.addStretch()
        wf_layout.addLayout(wf_status)

        self._wf_progress = QProgressBar()
        self._wf_progress.setRange(0, 100)
        self._wf_progress.setFormat("Step %v / 100")
        self._wf_progress.setFixedHeight(16)
        wfbar_style = ("QProgressBar { background: #1f2937; border: none;"
            " border-radius: 4px; text-align: center; font-size: 8pt;"
            " color: #9CA3AF; }"
            "QProgressBar::chunk { background-color: #F59E0B; border-radius: 4px; }")
        self._wf_progress.setStyleSheet(wfbar_style)
        wf_layout.addWidget(self._wf_progress)

        self._wf_step_name = QLabel("")
        self._wf_step_name.setStyleSheet("font-size: 9pt; color: #D1D5DB;")
        wf_layout.addWidget(self._wf_step_name)
        scroll_layout.addWidget(self._wf_section)

        # Section 4.5: Tool Calls
        self._tc_section = _ProgressSection("Tool Calls", _COL_TOOL)
        tc_layout = self._tc_section.content_layout()

        tc_status = QHBoxLayout()
        self._tc_dot = _StatusDot()
        tc_status.addWidget(self._tc_dot)
        self._tc_stage = QLabel("No active tools")
        self._tc_stage.setStyleSheet("font-size: 10pt; color: " + _COL_TOOL + ";")
        tc_status.addWidget(self._tc_stage, 1)
        tc_status.addStretch()
        tc_layout.addLayout(tc_status)

        self._tc_list = QLabel("")
        self._tc_list.setStyleSheet(
            "font-size: 9pt; color: #D1D5DB; padding: 4px;"
            "background: rgba(0,0,0,0.2); border-radius: 4px;")
        self._tc_list.setWordWrap(True)
        self._tc_list.setMaximumHeight(180)
        tc_layout.addWidget(self._tc_list)
        scroll_layout.addWidget(self._tc_section)

        # Section 5: Sub-Agent Teams
        self._sa_section = _ProgressSection("Sub-Agent Teams", _COL_SUBAGENT)
        sa_layout = self._sa_section.content_layout()

        sa_status = QHBoxLayout()
        self._sa_dot = _StatusDot()
        sa_status.addWidget(self._sa_dot)
        self._sa_stage = QLabel("No active agents")
        self._sa_stage.setStyleSheet("font-size: 10pt; color: " + _COL_SUBAGENT + ";")
        sa_status.addWidget(self._sa_stage, 1)
        sa_status.addStretch()
        sa_layout.addLayout(sa_status)

        self._sa_cards = QVBoxLayout()
        sa_layout.addLayout(self._sa_cards)
        scroll_layout.addWidget(self._sa_section)

        # Section 5: Activity Log
        log_section = _ProgressSection("Activity Log", "#9CA3AF")
        log_layout = log_section.content_layout()

        self._activity_browser = QTextBrowser()
        self._activity_browser.setOpenExternalLinks(False)
        self._activity_browser.document().setMaximumBlockCount(500)
        log_style = ("QTextBrowser { background: #0f172a; border: 1px solid #1e293b;"
            " border-radius: 4px; padding: 4px;"
            " font-family: Consolas, monospace; font-size: 9pt; color: #D1D5DB; }")
        self._activity_browser.setStyleSheet(log_style)
        self._activity_browser.setMinimumHeight(150)
        log_layout.addWidget(self._activity_browser)
        scroll_layout.addWidget(log_section)
        scroll_layout.addStretch()

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._set_idle)

        return tab

    # -- Callback Methods (Thread-Safe) ------------------------------------------

    def on_pipeline_step(self, step, total, message):
        """Called when controller reports a pipeline step (thread-safe)."""
        self._pipeline_signal.emit(step, total, message)
        self._progress_log_signal.emit("Pipeline", chr(0x1F9E0), message)
        self._reset_idle_timer()

    def on_rag_progress(self, progress_data):
        """Called from RAG engine progress_callback (thread-safe).

        Accepts a dict with keys:
          - phase (str): "decompose" | "retrieve" | "evaluate" | "synthesize" | "complete"
          - hop (int), total_hops (int)
          - message (str): human-readable description
          - sub_questions (list[str] | None)
          - sub_question (str | None), sub_question_idx (int | None)
          - source_count (int | None), total_sources (int | None)
          - gaps (list[str] | None), sufficient (bool | None)
        Falls back to treating a plain string as a legacy log message.
        """
        if isinstance(progress_data, str):
            # Legacy path: plain string message, emit raw
            self._rag_signal.emit({"phase": "message", "message": progress_data})
            self._progress_log_signal.emit("RAG", chr(0x1F52C), progress_data)
        else:
            self._rag_signal.emit(progress_data)
            msg = progress_data.get("message", "")
            if msg:
                self._progress_log_signal.emit("RAG", chr(0x1F52C), msg)
        self._reset_idle_timer()

    def on_workflow_progress(self, done, total, step_name):
        """Called from workflow_engine progress_callback (thread-safe)."""
        self._workflow_signal.emit(done, total, step_name)
        self._progress_log_signal.emit("Workflow", chr(0x2699),
                               f"Step {done}/{total}: {step_name}")
        self._reset_idle_timer()

    def on_subagent_progress(self, persona, status, message):
        """Called from SubAgent execute_async callbacks (thread-safe)."""
        self._subagent_signal.emit(persona, status, message)
        emoji = {"running": chr(0x1F504), "complete": chr(0x2705),
                 "failed": chr(0x274C), "queued": chr(0x23F3)}.get(status, chr(0x2022))
        self._progress_log_signal.emit(f"Agent:{persona}", emoji, message)
        self._reset_idle_timer()

    def on_generic_progress(self, emoji, message):
        """Generic progress entry for systems not covered above (thread-safe)."""
        self._progress_log_signal.emit("System", emoji, message)
        self._reset_idle_timer()


    # -- Signal Handlers (Main Thread) -------------------------------------------

    def _on_pipeline_update(self, step, total, message):
        self._pipeline_step = step
        self._pipeline_total = max(total, 1)
        self._pipe_dot.set_active(True, _COL_PIPELINE)
        pct = int(step / self._pipeline_total * 100)
        self._pipe_progress.setValue(pct)
        self._pipe_progress.setFormat(f"Step {step} / {total}")
        self._pipe_stage.setText("Pipeline Running")
        # Strip emoji and STEP prefix
        clean = re.sub(r'[\U0001F300-\U0001FAFF\U00002702-\U000027B0]\s*', '', message)
        clean = re.sub(r'^\[?STEP\s+\d+/\d+\]?\s*:?\s*', '', clean, flags=re.IGNORECASE)
        self._pipe_detail.setText(clean)
        self._set_nonidle(f"Pipeline: Step {step}/{total}", _COL_PIPELINE)

    def _on_rag_update(self, data):
        """Signal handler for RAG progress updates.

        Accepts either a structured dict (from progress_callback) or falls
        back to parsing the legacy text-based message format (from log_signal).
        """
        self._rag_dot.set_active(True, _COL_RAG)
        self._rag_stage.setText("Researching...")
        self._set_nonidle("RAG: Researching...", _COL_RAG)

        # If we received a structured dict, use it directly
        if isinstance(data, dict) and data.get("phase") != "message":
            hop = data.get("hop", 0)
            total = data.get("total_hops", 0)

            # Clear stale sub-questions at first hop or decompose phase
            if data.get("phase") in ("decompose",):
                self._rag_sub_questions.clear()

            if total > 0:
                self._rag_hop = hop
                self._rag_total = total
                self._rag_hops_label.setText(f"Hops: {hop}/{total}")

            sub_questions = data.get("sub_questions")
            if sub_questions:
                self._rag_questions_label.setText(f"Sub-Questions: {len(sub_questions)}")
                for sq in sub_questions:
                    self._rag_sub_questions.append(sq[:80])
                self._update_rag_sub_list()

            source_count = data.get("source_count")
            if source_count is not None:
                total_sources = data.get("total_sources", 0)
                self._rag_sources_label.setText(f"Sources: +{source_count} ({total_sources} total)")

            total_sources = data.get("total_sources")
            if total_sources is not None and source_count is None:
                self._rag_sources_label.setText(f"Sources: {total_sources}")

            sub_question = data.get("sub_question")
            sq_idx = data.get("sub_question_idx")
            if sub_question and sq_idx:
                while len(self._rag_sub_questions) < sq_idx:
                    self._rag_sub_questions.append("...")
                self._rag_sub_questions[sq_idx - 1] = sub_question[:80]
                self._update_rag_sub_list()

            if data.get("phase") == "complete":
                self._rag_dot.set_active(True, _COL_RAG)
                self._rag_stage.setText("Complete")

            return

        # Fallback: legacy message-based parsing (from log_signal)
        message = data.get("message", "") if isinstance(data, dict) else str(data)

        hop_match = re.search(r'Hop\s+(\d+)/(\d+)', message, re.IGNORECASE)
        if hop_match:
            self._rag_hop = int(hop_match.group(1))
            self._rag_total = int(hop_match.group(2))
            self._rag_hops_label.setText(f"Hops: {self._rag_hop}/{self._rag_total}")

        sq_match = re.search(r'(\d+)\s+sub-questions?\s+generated', message, re.IGNORECASE)
        if sq_match:
            self._rag_questions_label.setText(f"Sub-Questions: {sq_match.group(1)}")

        src_match = re.search(r'(\d+)\s+chunk\(s\)\s+retrieved', message, re.IGNORECASE)
        if src_match:
            self._rag_sources_label.setText(f"Sources: +{src_match.group(1)}")

        src_match2 = re.search(r'(\d+)\s+unique\s+context\(s\)', message, re.IGNORECASE)
        if src_match2:
            self._rag_sources_label.setText(f"Sources: {src_match2.group(1)}")

        q_match = re.search(r'Retrieving for Q(\d+):\s*(.+)', message)
        if q_match:
            q_idx = int(q_match.group(1))
            q_text = q_match.group(2).strip()
            while len(self._rag_sub_questions) < q_idx:
                self._rag_sub_questions.append("...")
            self._rag_sub_questions[q_idx - 1] = q_text[:80]
            self._update_rag_sub_list()


    def _rebuild_agent_cards(self):
        while self._sa_cards.count():
            item = self._sa_cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self._subagents:
            lbl = QLabel("No agents running")
            lbl.setStyleSheet("font-size: 9pt; color: " + _COL_IDLE + "; padding: 4px;")
            self._sa_cards.addWidget(lbl)
            return
        for p, info in self._subagents.items():
            card = _build_agent_card(p, info["status"], info["message"], info["color"])
            self._sa_cards.addWidget(card)

    def _connect_log_signal(self):
        w = getattr(self, "worker", None)
        if w is not None and hasattr(w, "log_signal"):
            try:
                w.log_signal.disconnect(self._on_worker_log)
            except (TypeError, RuntimeError):
                pass
            w.log_signal.connect(self._on_worker_log)
        # Register RAG progress callback on the controller so the
        # AgenticRAGEngine.answer() progress_callback flows directly to
        # on_rag_progress() instead of relying on log message parsing.
        ctrl = getattr(self, 'controller', None)
        if ctrl is not None and hasattr(self, 'on_rag_progress'):
            ctrl.rag_progress_callback = self.on_rag_progress
        # Install logging handler bridge for sub-agent / crew messages
        self._install_logging_bridge()

    def _install_logging_bridge(self):
        """Install _QtLogHandler on the SubAgents and CrewIntegration loggers
        so their logger.info() calls reach _on_worker_log via a thread-safe signal.
        Avoids double-installation by checking for existing handler.
        """
        if getattr(self, '_log_handler_installed', False):
            return
        self._log_handler_bridge = _QtLogHandler(
            lambda msg: self._log_bridge_signal.emit(msg)
        )
        for logger_name in _QtLogHandler.WATCHED_LOGGERS:
            logger = logging.getLogger(logger_name)
            # Check if already installed to avoid duplicates
            if not any(isinstance(h, _QtLogHandler) for h in logger.handlers):
                logger.addHandler(self._log_handler_bridge)
        self._log_handler_installed = True

    def _remove_logging_bridge(self):
        """Remove the _QtLogHandler from watched loggers during shutdown
        to prevent the handler from firing after the mixin's signals are destroyed.
        """
        if not getattr(self, '_log_handler_installed', False):
            return
        for logger_name in _QtLogHandler.WATCHED_LOGGERS:
            logger = logging.getLogger(logger_name)
            logger.removeHandler(self._log_handler_bridge)
        self._log_handler_installed = False

    def _on_worker_log(self, message):
        if not message:
            return
        if "RAG" in message:
            # Legacy log-parsing path — still emit via _rag_signal as a
            # fallback for systems that don't use the new progress_callback.
            self._rag_signal.emit({"phase": "message", "message": message})
            self._progress_log_signal.emit("RAG", chr(0x1F52C), message)
        elif "STEP" in message:
            sm = re.search(r'STEP\s+(\d+)/(\d+)', message, re.IGNORECASE)
            if sm:
                self._pipeline_signal.emit(int(sm.group(1)), int(sm.group(2)), message)
            else:
                self._progress_log_signal.emit("Pipeline", chr(0x1F9E0), message)
        elif message.startswith("[TOOL]"):
            self._parse_tool_message(message)
            self._progress_log_signal.emit("Tool", chr(0x1F6E0), message)
        elif "sub-agent" in message.lower() or "subagent" in message.lower()\
                or "agent:" in message.lower() or "[CREW]" in message:
            # Parse sub-agent status to update the agent cards section
            self._parse_subagent_message(message)
            self._progress_log_signal.emit("Agent", chr(0x1F465), message)
        elif "workflow" in message.lower():
            self._progress_log_signal.emit("Workflow", chr(0x2699), message)
        else:
            self._progress_log_signal.emit("System", chr(0x2022), message)
        self._reset_idle_timer()


    def _on_workflow_update(self, done, total, step_name):
        self._workflow_done = done
        self._workflow_total = max(total, 1)
        self._wf_dot.set_active(True, _COL_WORKFLOW)
        pct = int(done / self._workflow_total * 100)
        self._wf_progress.setValue(pct)
        self._wf_progress.setFormat(f"Step {done} / {total}")
        self._wf_stage.setText(f"Running: {step_name}")
        self._wf_step_name.setText(f"Current: {step_name}")
        self._set_nonidle(f"Workflow: {step_name}", _COL_WORKFLOW)

    def _on_subagent_update(self, persona, status, message):
        if status == "running":
            self._subagents[persona] = {"status": status, "message": message, "color": _COL_ACTIVE}
            self._sa_dot.set_active(True, _COL_SUBAGENT)
            self._sa_stage.setText("Agents Active")
            self._set_nonidle(f"Agents: {persona} running", _COL_SUBAGENT)
        elif status == "complete":
            self._subagents[persona] = {"status": status, "message": message, "color": _COL_IDLE}
        elif status == "failed":
            self._subagents[persona] = {"status": status, "message": message, "color": _COL_ERROR}
            self._set_nonidle(f"Agents: {persona} failed", _COL_ERROR)
        self._rebuild_agent_cards()

    def _on_log_line(self, section, emoji, message):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        # escape_message_html (shared): preserves \n as <br> so multi-line
        # tool output keeps its line breaks; _html_escape below stays as-is
        # because the tool-list tests pin its exact quote-entity form.
        from html_sanitizer import escape_message_html
        escaped = escape_message_html(message)
        color_map = {
            "Pipeline": _COL_PIPELINE,
            "RAG": _COL_RAG,
            "Workflow": _COL_WORKFLOW,
            "Agent": _COL_SUBAGENT,
            "Tool": _COL_TOOL,
        }
        c = color_map.get(section, "#9CA3AF")
        html = f'<span style="color: #6B7280; font-size: 8pt;">[{ts}]</span> '
        html += f'<span style="color: {c};">{emoji} [{_html_escape(section)}]</span> '
        html += f'<span style="color: #D1D5DB;">{escaped}</span><br>'
        self._activity_browser.insertHtml(html)
        sb = self._activity_browser.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_nonidle(self, label, color):
        self._overall_status.set_active(True, color)
        self._overall_label.setText(label)
        self._overall_label.setStyleSheet(f"font-size: 10pt; color: {color};")

    def _set_idle(self):
        self._overall_status.set_active(False, _COL_IDLE)
        self._overall_label.setText("Idle")
        self._overall_label.setStyleSheet(f"font-size: 10pt; color: {_COL_IDLE};")
        self._pipe_dot.set_active(False, _COL_IDLE)
        self._pipe_stage.setText("Waiting for pipeline...")
        self._pipe_progress.setValue(0)
        self._pipe_progress.setFormat("Step %v / 100")
        self._rag_dot.set_active(False, _COL_IDLE)
        self._rag_stage.setText("No active research")
        self._wf_dot.set_active(False, _COL_IDLE)
        self._wf_stage.setText("No active workflow")
        self._wf_progress.setValue(0)
        self._wf_progress.setFormat("Step %v / 100")
        self._sa_dot.set_active(False, _COL_IDLE)
        self._sa_stage.setText("No active agents")
        self._subagents.clear()
        self._rebuild_agent_cards()
        self._tc_dot.set_active(False, _COL_IDLE)
        self._tc_stage.setText("No active tools")
        self._tool_calls.clear()
        self._tc_list.setText("")

    def _reset_idle_timer(self):
        if hasattr(self, '_idle_timer') and self._idle_timer:
            self._idle_timer.start(30000)

    def _clear_activity_log(self):
        self._activity_browser.clear()

    def _update_rag_sub_list(self):
        items = "<br>".join(
            f"{chr(0x2022)} {_html_escape(q)}" for q in self._rag_sub_questions if q
        )
        self._rag_sub_list.setText(items or "No sub-questions yet")

    def _on_tool_call(self, command, status, detail):
        """Signal handler for tool call events (main thread)."""
        entry = {"command": command, "status": status, "detail": detail}
        self._tool_calls.append(entry)
        # Keep only the last 10 tool calls
        if len(self._tool_calls) > 10:
            self._tool_calls.pop(0)
        if status == "running":
            self._tc_dot.set_active(True, _COL_TOOL)
            self._tc_stage.setText("Tools Active")
            self._set_nonidle(f"Tool: {command}", _COL_TOOL)
        elif status == "failed":
            self._tc_dot.set_active(True, _COL_ERROR)
            self._tc_stage.setText("Tool Failed")
            self._set_nonidle(f"Tool failed: {command}", _COL_ERROR)
        self._rebuild_tool_list()

    def _rebuild_tool_list(self):
        """Rebuild the HTML tool call list from self._tool_calls."""
        if not self._tool_calls:
            self._tc_list.setText("")
            return
        items = []
        for tc in reversed(self._tool_calls):
            cmd = tc["command"]
            st = tc["status"]
            detail = tc["detail"]
            icon = {"running": chr(0x1F504), "complete": chr(0x2705),
                     "failed": chr(0x274C)}.get(st, chr(0x2022))
            color = _COL_TOOL if st == "running" else _COL_ACTIVE if st == "complete" else _COL_ERROR
            escaped = _html_escape(f"{cmd}: {detail[:80]}")
            items.append(f'<span style="color: {color};">{icon} {escaped}</span>')
        self._tc_list.setText("<br>".join(items))

    def _parse_tool_message(self, message):
        """Parse a [TOOL] log message and emit tool_call_signal.

        Format from plugin_registry.execute_command:
          [TOOL] START SEARCH_WEB query=...   -> running
          [TOOL] DONE SEARCH_WEB: result...    -> complete
          [TOOL] FAIL SEARCH_WEB: error...     -> failed
        """
        # Pattern: [TOOL] START <COMMAND> <params>
        start_match = re.search(r'\[TOOL\]\s+START\s+(\S+)\s+(.*)', message, re.IGNORECASE)
        if start_match:
            cmd = start_match.group(1)
            params_summary = start_match.group(2).strip()[:60]
            self._tool_call_signal.emit(cmd, "running", params_summary)
            return

        # Pattern: [TOOL] DONE <COMMAND>: <result>
        done_match = re.search(r'\[TOOL\]\s+DONE\s+(\S+):\s*(.*)', message, re.IGNORECASE)
        if done_match:
            cmd = done_match.group(1)
            result_snippet = done_match.group(2).strip()[:80]
            self._tool_call_signal.emit(cmd, "complete", result_snippet)
            return

        # Pattern: [TOOL] FAIL <COMMAND>: <error>
        fail_match = re.search(r'\[TOOL\]\s+FAIL\s+(\S+):\s*(.*)', message, re.IGNORECASE)
        if fail_match:
            cmd = fail_match.group(1)
            error_snippet = fail_match.group(2).strip()[:80]
            self._tool_call_signal.emit(cmd, "failed", error_snippet)
            return

        # Fallback: try to parse any [TOOL] message
        generic_match = re.search(r'\[TOOL\]\s+(\S+)\s+(.*)', message)
        if generic_match:
            cmd = generic_match.group(1)
            rest = generic_match.group(2).strip()[:80]
            status = "running" if "START" in message else "complete"
            self._tool_call_signal.emit(cmd, status, rest)
            return

    def _parse_subagent_message(self, message):
        """Parse a log message to extract sub-agent persona and status,
        then update the sub-agent cards section via _on_subagent_update.

        Handles messages from:
          - sub_agents.py: logger.info("[SUB-AGENT] Starting Coder agent on: ...")
          - crew_integration.py: logger.info("[CREW] Starting SubAgent: Researcher")
        """
        # Pattern: [SUB-AGENT] Starting <Persona> agent on: task
        start_match = re.search(
            r'\[SUB-AGENT\]\s+Starting\s+(\w+)\s+agent', message, re.IGNORECASE
        )
        if start_match:
            persona = start_match.group(1).capitalize()
            self._on_subagent_update(persona, "running", message[:100])
            return

        # Pattern: [SUB-AGENT] <Persona> complete (N chars)
        complete_match = re.search(
            r'\[SUB-AGENT\]\s+(\w+)\s+complete', message, re.IGNORECASE
        )
        if complete_match:
            persona = complete_match.group(1).capitalize()
            self._on_subagent_update(persona, "complete", message[:100])
            return

        # Pattern: [SUB-AGENT] Async start: <Persona>
        async_match = re.search(
            r'\[SUB-AGENT\]\s+Async\s+start:\s+(\w+)', message, re.IGNORECASE
        )
        if async_match:
            persona = async_match.group(1).capitalize()
            self._on_subagent_update(persona, "running", message[:100])
            return

        # Pattern: [SUB-AGENT] <Persona> failed: <error>
        failed_match = re.search(
            r'\[SUB-AGENT\]\s+(\w+)\s+failed', message, re.IGNORECASE
        )
        if failed_match:
            persona = failed_match.group(1).capitalize()
            self._on_subagent_update(persona, "failed", message[:100])
            return

        # Pattern: [CREW] Starting SubAgent: <Persona>
        crew_match = re.search(
            r'\[CREW\]\s+Starting\s+SubAgent:\s+(\w+)', message, re.IGNORECASE
        )
        if crew_match:
            persona = crew_match.group(1).capitalize()
            self._on_subagent_update(persona, "running", message[:100])
            return

        # Pattern: [CREW] Starting CrewAI team with: <Persona1>, <Persona2>, ...
        # Create agent cards for all personas listed
        crew_team_match = re.search(
            r'\[CREW\]\s+Starting\s+CrewAI\s+team\s+with:\s*\[?([^\]]+)\]?', message, re.IGNORECASE
        )
        if crew_team_match:
            personas_raw = crew_team_match.group(1)
            for pname in re.findall(r"'(\w+)'|(\w+)", personas_raw):
                p = pname[0] or pname[1]
                if p:
                    self._on_subagent_update(p.capitalize(), "running", "CrewAI task assigned")
            return

    def teardown_progress_tab(self) -> None:
        """Cleanly terminate idle timer and unhook progress callbacks."""
        timer = getattr(self, "_idle_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except (RuntimeError, AttributeError):
                pass

    def teardown(self) -> None:
        """Alias for teardown_progress_tab."""
        self.teardown_progress_tab()




# -- Qt Log Handler Bridge --------------------------------------------------
# Bridges Python logging module -> Qt signal so sub-agent / crew messages
# reach the progress tab without plumbing log_callback through the entire stack.
class _QtLogHandler(logging.Handler):
    """Python logging handler that forwards records from specific loggers
    to a Qt signal callback for display in the progress tab.

    Only forwards from loggers whose names are in WATCHED_LOGGERS to avoid
    flooding the activity log with every info/debug message.
    """
    WATCHED_LOGGERS = {"SubAgents", "CrewIntegration", "PluginRegistry"}

    def __init__(self, callback):
        super().__init__()
        self.setFormatter(logging.Formatter("%(message)s"))
        self._callback = callback

    def emit(self, record):
        if record.name not in self.WATCHED_LOGGERS:
            return
        try:
            msg = self.format(record)
            self._callback(msg)
        except (RuntimeError, ValueError, TypeError, OSError):
            self.handleError(record)


# -- Module-level helpers -------------------------------------------------


def _html_escape(text):
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#x27;"))


def _build_agent_card(persona, status, message, color):
    card = QFrame()
    card.setStyleSheet(
        "QFrame {"
        "background: rgba(16, 185, 129, 0.05);"
        "border: 1px solid " + color + "44;"
        "border-radius: 4px; padding: 4px; margin: 1px 0;"
        "}")
    layout = QHBoxLayout(card)
    layout.setContentsMargins(6, 2, 6, 2)
    im = {"running": chr(0x1F504), "complete": chr(0x2705),
          "failed": chr(0x274C), "queued": chr(0x23F3)}
    icon = QLabel(im.get(status, chr(0x2022)))
    icon.setStyleSheet("font-size: 11pt;")
    layout.addWidget(icon)
    txt = QLabel("<b>" + persona + "</b>: " + message[:120])
    txt.setStyleSheet("font-size: 9pt; color: " + color + ";")
    txt.setTextFormat(Qt.TextFormat.RichText)
    layout.addWidget(txt, 1)
    return card
