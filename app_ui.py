"""
app_ui.py — AppUIMixin: UI construction, themes, audit logging.
Extracted from app_core.py to split the monolithic dashboard.
"""
from datetime import datetime
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTextEdit, QTextBrowser, QPushButton,
    QLabel, QListWidget, QFrame, QScrollArea, QCheckBox, QTabWidget,
    QComboBox, QMenu, QApplication, QProgressBar,
)

from PyQt6.QtGui import QShortcut, QKeySequence, QAction, QTextCursor

from config import CONFIG, WORKSPACE_DIR, save_settings
from tabs.theme_builder_tab import get_all_themes
import plugin_registry

# Wake word availability — defined in app_hotkeys.py
from app_hotkeys import _WAKE_WORD_AVAILABLE


class AppUIMixin:
    """Mixin providing the main dashboard UI construction and theme engine."""

    THEMES = {
        "Cyberpunk (Pink/Cyan)": {"bg": "#0A0A0C", "bg_alt": "#050507", "bg_tab": "#13141C", "text": "#00E5FF", "text_tab": "#007A88", "accent1": "#FF007F", "accent2": "#00E5FF"},
        "Terminal (Amber/Orange)": {"bg": "#0A0800", "bg_alt": "#050400", "bg_tab": "#141000", "text": "#FFB000", "text_tab": "#CC8C00", "accent1": "#FF8C00", "accent2": "#FFB000"},
        "Matrix (Neon Green)": {"bg": "#050A05", "bg_alt": "#020502", "bg_tab": "#081408", "text": "#00FF41", "text_tab": "#008F11", "accent1": "#008F11", "accent2": "#00FF41"},
        "Classic (Charcoal)": {"bg": "#121212", "bg_alt": "#1C1C1C", "bg_tab": "#1C1C1C", "text": "#E0E0E0", "text_tab": "#9CA3AF", "accent1": "#374151", "accent2": "#10B981"},
    }

    def log_to_audit(self, message, is_debug=False):
        if is_debug and not CONFIG.get("debug_logging", False):
            return
        ts = datetime.now().strftime('%H:%M:%S')
        if not message.startswith("["):
            prefix = "[DEBUG] " if is_debug else ""
            message = f"[{ts}] {prefix}{message}"
        import threading
        if threading.current_thread() is not threading.main_thread():
            # run_on_main_thread, NOT QTimer.singleShot: a timer created
            # on a worker thread has no event loop and is silently
            # dropped — audit lines from background workers vanished.
            from utils.qt_dispatch import run_on_main_thread
            run_on_main_thread(lambda: self.log_to_audit(message, is_debug))
            return
        # Guard: audit_log_display may not exist yet if called during init_ui
        if hasattr(self, 'audit_log_display'):
            self.audit_log_display.insertPlainText(message + "\n")
            self.audit_log_display.verticalScrollBar().setValue(
                self.audit_log_display.verticalScrollBar().maximum()
            )
        if hasattr(self, 'statusBar') and callable(getattr(self, 'statusBar', None)):
            try:
                sb = self.statusBar()
                if sb and hasattr(sb, 'showMessage') and not is_debug:
                    clean_msg = message.split("] ", 1)[-1] if message.startswith("[") else message
                    sb.showMessage(clean_msg, 5000)
            except Exception:
                pass
        if hasattr(self, 'file_logger'):
            if is_debug:
                self.file_logger.debug(message)
            else:
                self.file_logger.info(message)

    def _append_chat(self, text):
        self.chat_display.append(f"{text}<br><hr>")
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )

    # ── Styled message card helpers ──────────────────────────────
    # All chat messages now render as styled cards with role label,
    # timestamp, and a left accent border. User/Assistant/Error each
    # have distinct colors. Plain chat_display.append(<html>) still
    # works for backward compatibility with existing call sites.

    @staticmethod
    def _html_escape(text):
        """Escape HTML special chars for safe insertion into QTextEdit."""
        if not text:
            return ""
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    @staticmethod
    def _render_message_html(text):
        """Escape text for HTML and preserve its visible layout.

        Thin delegate to html_sanitizer.escape_message_html — the shared
        implementation (escape first, \n → <br>, leading spaces → &nbsp;)
        so every tab log view gets identical layout-preserving behavior.
        """
        from html_sanitizer import escape_message_html
        return escape_message_html(text)

    def _format_user_card(self, text, ts=None):
        """Format a user message as a styled card HTML string."""
        if ts is None:
            ts = datetime.now().strftime("%H:%M:%S")
        safe = self._render_message_html(text)
        return (
            f"<div style='border-left: 3px solid #3B82F6; padding: 6px 10px; "
            f"margin: 6px 0; background-color: rgba(59, 130, 246, 0.08); "
            f"border-radius: 3px;'>"
            f"<div style='font-size: 8pt; color: #6B7280; margin-bottom: 2px;'>"
            f"<b style='color: #3B82F6;'>👤 You</b> · {ts}"
            f"</div>"
            f"<div style='color: #E0E0E0; font-size: 11pt;'>{safe}</div>"
            f"</div>"
        )

    def _format_assistant_card(self, text, ts=None, footer_html=None, stopped=False):
        """Format an assistant message as a styled card HTML string.

        Args:
            text: Message body.
            ts: Timestamp string (defaults to now).
            footer_html: Optional HTML for the footer line (tokens/sec, etc).
            stopped: If True, render in muted style for cancelled responses.
        """
        if ts is None:
            ts = datetime.now().strftime("%H:%M:%S")
        safe = self._render_message_html(text)
        if stopped:
            border_color = "#6B7280"
            bg_color = "rgba(107, 114, 128, 0.08)"
            label = "⏹ Stopped"
            label_color = "#6B7280"
        else:
            border_color = "#10B981"
            bg_color = "rgba(16, 185, 129, 0.08)"
            label = "🤖 KokertechAI"
            label_color = "#10B981"

        footer = ""
        if footer_html:
            footer = (
                f"<div style='font-size: 8pt; color: #6B7280; margin-top: 4px; "
                f"text-align: right;'>{footer_html}</div>"
            )

        return (
            f"<div style='border-left: 3px solid {border_color}; padding: 6px 10px; "
            f"margin: 6px 0; background-color: {bg_color}; border-radius: 3px;'>"
            f"<div style='font-size: 8pt; color: #6B7280; margin-bottom: 2px;'>"
            f"<b style='color: {label_color};'>{label}</b> · {ts}"
            f"</div>"
            f"<div style='color: #E0E0E0; font-size: 11pt;'>{safe}</div>"
            f"{footer}"
            f"</div>"
        )

    def _format_error_card(self, error, ts=None, show_retry=True):
        """Format an error message as a styled card HTML string.

        Includes a Retry link (kokertech-retry:// scheme) when show_retry=True
        and last_user_text is non-empty. The link is intercepted via the
        ``anchorClicked`` signal on chat_display.
        """
        if ts is None:
            ts = datetime.now().strftime("%H:%M:%S")
        safe = self._render_message_html(str(error))
        retry_html = ""
        if show_retry and getattr(self, 'last_user_text', ''):
            retry_html = (
                "<div style='margin-top: 4px; font-size: 9pt;'>"
                "<a href='kokertech-retry://' style='color: #3B82F6; "
                "text-decoration: none;'>🔁 Retry</a>"
                "</div>"
            )
        return (
            f"<div style='border-left: 3px solid #EF4444; padding: 6px 10px; "
            f"margin: 6px 0; background-color: rgba(239, 68, 68, 0.08); "
            f"border-radius: 3px;'>"
            f"<div style='font-size: 8pt; color: #EF4444; margin-bottom: 2px;'>"
            f"<b>❌ Error</b> · {ts}"
            f"</div>"
            f"<div style='color: #E0E0E0; font-size: 10pt;'>{safe}</div>"
            f"{retry_html}"
            f"</div>"
        )

    def _append_user_message(self, text):
        """Append a styled user message card to the chat display."""
        try:
            if not hasattr(self, 'chat_display') or not self.chat_display:
                return
            self.chat_display.append(self._format_user_card(text))
            self._scroll_chat_to_bottom()
        except (RuntimeError, AttributeError):
            pass

    def _append_assistant_message(self, text, footer_html=None, stopped=False):
        """Append a styled assistant message card to the chat display."""
        try:
            if not hasattr(self, 'chat_display') or not self.chat_display:
                return
            self.chat_display.append(self._format_assistant_card(text, footer_html=footer_html, stopped=stopped))
            self._scroll_chat_to_bottom()
        except (RuntimeError, AttributeError):
            pass

    def _append_error_message(self, error, show_retry=True):
        """Append a styled error message card to the chat display."""
        try:
            if not hasattr(self, 'chat_display') or not self.chat_display:
                return
            self.chat_display.append(self._format_error_card(error, show_retry=show_retry))
            self._scroll_chat_to_bottom()
        except (RuntimeError, AttributeError):
            pass

    def _scroll_chat_to_bottom(self):
        """Scroll the chat display to the bottom (safe for missing widget)."""
        try:
            if hasattr(self, 'chat_display') and self.chat_display:
                self.chat_display.verticalScrollBar().setValue(
                    self.chat_display.verticalScrollBar().maximum()
                )
        except (RuntimeError, AttributeError):
            pass

    def refresh_model_status_ui(self):
        """Refresh every sidebar, header, and panel indicator that reflects model state.

        Single entry point for "the model state changed, repaint the UI" —
        called after onboarding loads a model, after Settings ⚡ Apply, on tab switches,
        and by the badge poll timer. Syncs:
          - Provider badge (Local LLM / Not Loaded)
          - Sidebar model status (residency state)
          - Main tab Output Window header model indicator
          - Desire Engine panel model indicator
          - Executive Audit Logs panel model indicator
          - Sidebar Copilot Tools vision model indicator
        """
        app = QApplication.instance()
        if app is not None:
            from PyQt6.QtCore import QThread
            if QThread.currentThread() != app.thread():
                from utils.qt_dispatch import run_on_main_thread
                run_on_main_thread(self.refresh_model_status_ui)
                return
        if getattr(self, '_shutting_down', False):
            return
        self._update_provider_badge()

        # 1. Main model status & Output Window header
        try:
            controller = getattr(self, 'controller', None)
            provider = getattr(controller, 'provider', None)
            if provider is not None and getattr(provider, 'is_loaded', lambda: False)():
                path = getattr(provider, '_model_path', '') or ''
                name = os.path.basename(path) if path else 'model'
                self._set_model_status(f"🟢 {name}", "#10B981")
                self._set_main_model_header(f"🟢 {name}", "#10B981")
            elif (CONFIG.get('model_file', '') or '').strip():
                name = os.path.basename(CONFIG.get('model_file', ''))
                self._set_model_status(f"🟡 {name} (unloaded)", "#F59E0B")
                self._set_main_model_header(f"🟡 {name} (unloaded)", "#F59E0B")
            else:
                self._set_model_status("⚪ No model selected", "#9CA3AF")
                self._set_main_model_header("⚪ No model selected", "#9CA3AF")
        except (RuntimeError, AttributeError):
            pass

        # 2. Desire Engine model indicator (Main tab Suggestions panel)
        try:
            if hasattr(self, 'desire_model_lbl') and self.desire_model_lbl:
                desire_m = (CONFIG.get('desire_model_name', '') or '').strip()
                if desire_m:
                    d_display = os.path.basename(desire_m)
                    self.desire_model_lbl.setText(f"🧠 Model: {d_display}")
                    self.desire_model_lbl.setStyleSheet("font-size: 8pt; color: #10B981; font-weight: bold; padding: 1px 2px;")
                else:
                    self.desire_model_lbl.setText("🧠 Model: (main model)")
                    self.desire_model_lbl.setStyleSheet("font-size: 8pt; color: #9CA3AF; padding: 1px 2px;")
        except (RuntimeError, AttributeError):
            pass

        # 3. Cognitive Auditor model indicator (Main tab Executive Audit panel)
        try:
            if hasattr(self, 'auditor_model_lbl') and self.auditor_model_lbl:
                auditor_m = (CONFIG.get('auditor_model_name', '') or '').strip()
                if auditor_m:
                    a_display = os.path.basename(auditor_m)
                    self.auditor_model_lbl.setText(f"⚖️ Model: {a_display}")
                    self.auditor_model_lbl.setStyleSheet("font-size: 8pt; color: #10B981; font-weight: bold; padding: 1px 2px;")
                else:
                    self.auditor_model_lbl.setText("⚖️ Model: (main model)")
                    self.auditor_model_lbl.setStyleSheet("font-size: 8pt; color: #9CA3AF; padding: 1px 2px;")
        except (RuntimeError, AttributeError):
            pass

        # 4. Vision model indicator (Main tab Sidebar Copilot Tools)
        try:
            if hasattr(self, 'vision_model_lbl') and self.vision_model_lbl:
                vision_m = (CONFIG.get('vision_model', '') or '').strip()
                if vision_m:
                    v_display = os.path.basename(vision_m)
                    self.vision_model_lbl.setText(f"👁️ Vision: {v_display}")
                    self.vision_model_lbl.setStyleSheet("font-size: 8pt; color: #10B981; font-weight: bold; padding: 1px 2px;")
                else:
                    self.vision_model_lbl.setText("👁️ Vision: (main model)")
                    self.vision_model_lbl.setStyleSheet("font-size: 8pt; color: #9CA3AF; padding: 1px 2px;")
        except (RuntimeError, AttributeError):
            pass

        # Settings-tab provider indicator: reuse the existing throttled checker
        try:
            if hasattr(self, '_refresh_provider_status'):
                self._refresh_provider_status()
        except (RuntimeError, AttributeError):
            pass

    # ── Live Output-Window streaming (2026-09 telemetry request) ──
    # Tokens are appended into a live assistant block as they arrive from
    # llama.cpp; on completion the block is replaced by the final styled
    # card so history stays clean. Token metrics update in real time.

    def _begin_streaming_response(self):
        """Open a live assistant block in the chat display.
        Records the block start position so _finish_streaming_response can
        replace the streamed text with the final styled card."""
        self._streaming_active = True
        self._stream_block_start = None
        try:
            if hasattr(self, 'chat_display') and self.chat_display:
                self.chat_display.append(self._format_streaming_header())
                self._stream_block_start = self.chat_display.textCursor().position()
                self._scroll_chat_to_bottom()
        except (RuntimeError, AttributeError):
            pass

    def _format_streaming_header(self):
        """Header line for the live streaming block."""
        ts = datetime.now().strftime("%H:%M:%S")  # noqa: DTZ005
        return (
            f"<div style='margin-top:8px; margin-bottom:2px;'>"
            f"<b style='color:#10B981;'>KokertechAI</b>"
            f" <span style='color:#6B7280; font-size:8pt;'>· {ts} · streaming…</span>"
            f"</div>"
        )

    def _append_stream_text(self, text):
        """Append one stream token's text to the live block (main thread)."""
        try:
            if not hasattr(self, 'chat_display') or not self.chat_display:
                return
            cur = self.chat_display.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.End)
            cur.insertText(text)
            self._scroll_chat_to_bottom()
        except (RuntimeError, AttributeError):
            pass

    def _finish_streaming_response(self):
        """Remove the live-streamed block. The final styled card is rendered
        by the normal completion path immediately after this call."""
        start = getattr(self, '_stream_block_start', None)
        if start is not None and hasattr(self, 'chat_display') and self.chat_display:
            try:
                cur = self.chat_display.textCursor()
                cur.setPosition(start)
                cur.movePosition(
                    QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
                cur.removeSelectedText()
            except (RuntimeError, ValueError, AttributeError):
                pass
        self._streaming_active = False
        self._stream_block_start = None

    # ── Typing indicator ──────────────────────────────────────────
    # Animated "● ● ●" label shown above the input row during processing.
    # Uses a QTimer to cycle through 1-3 visible dots every 400ms.

    # ── Progress Panel (Progressive Streaming UI) ──────────────────
    # Replaces the simple typing indicator with a rich progress panel
    # that shows current step, animated indicator, and scrolling log.

    def _show_progress_panel(self, initial_step="Initializing..."):
        """Show the progress panel above the input row.

        Creates the panel widgets on first call; shows and resets on
        subsequent calls so log messages from a prior run don't linger.
        """
        if not hasattr(self, '_progress_panel') or self._progress_panel is None:
            self._create_progress_panel()

        # Reset state
        self._progress_step_count = 0
        self._progress_log_text = ""
        self._progress_log_display.clear()
        self._progress_step_lbl.setText(f"🤖 {initial_step}")
        self._progress_step_lbl.setStyleSheet(
            "font-size: 10pt; color: #10B981; font-weight: bold; padding: 0;"
        )
        # Reset streaming token bar (counter is initialized in action_send_prompt)
        if hasattr(self, '_progress_token_bar'):
            self._progress_token_bar.setValue(0)
        if hasattr(self, '_progress_token_lbl'):
            self._progress_token_lbl.setText("")
        # Start the animated dots timer
        self._progress_dot_count = 0
        if getattr(self, '_progress_timer', None) is None:
            from PyQt6.QtCore import QTimer
            self._progress_timer = QTimer(self)
            self._progress_timer.timeout.connect(self._animate_progress_dots)
        self._progress_timer.start(400)

        # Hide old typing indicator if visible
        if hasattr(self, 'typing_indicator_lbl') and self.typing_indicator_lbl:
            self.typing_indicator_lbl.hide()
            old_timer = getattr(self, '_typing_timer', None)
            if old_timer:
                try:
                    old_timer.stop()
                except (RuntimeError, AttributeError):
                    pass
                self._typing_timer = None

        self._progress_panel.show()

    def _create_progress_panel(self):
        """Create the progress panel widget (called during init_ui)."""
        self._progress_panel = QFrame()
        self._progress_panel.setObjectName("ProgressPanel")
        self._progress_panel.setStyleSheet(
            "QFrame#ProgressPanel {"
            "  border: 1px solid #10B981;"
            "  border-radius: 4px;"
            "  background-color: rgba(16, 185, 129, 0.06);"
            "  padding: 4px;"
            "}"
        )
        panel_layout = QVBoxLayout(self._progress_panel)
        panel_layout.setContentsMargins(8, 6, 8, 6)
        panel_layout.setSpacing(2)

        # Header row: step label + status
        header_row = QHBoxLayout()
        self._progress_step_lbl = QLabel()
        self._progress_step_lbl.setStyleSheet(
            "font-size: 10pt; color: #10B981; font-weight: bold; border: none;"
        )
        header_row.addWidget(self._progress_step_lbl)
        header_row.addStretch()
        self._progress_status_lbl = QLabel("")
        self._progress_status_lbl.setStyleSheet(
            "font-size: 8pt; color: #6B7280; border: none;"
        )
        header_row.addWidget(self._progress_status_lbl)
        panel_layout.addLayout(header_row)

        # Token streaming row: progress bar + token count
        token_row = QHBoxLayout()
        self._progress_token_bar = QProgressBar()
        self._progress_token_bar.setObjectName("TokenBar")
        self._progress_token_bar.setMinimum(0)
        self._progress_token_bar.setMaximum(10000)
        self._progress_token_bar.setValue(0)
        self._progress_token_bar.setTextVisible(False)
        self._progress_token_bar.setFixedHeight(8)
        self._progress_token_bar.setStyleSheet(
            "QProgressBar#TokenBar {"
            "  background-color: rgba(16, 185, 129, 0.15);"
            "  border: 1px solid rgba(16, 185, 129, 0.3);"
            "  border-radius: 3px;"
            "}"
            "QProgressBar#TokenBar::chunk {"
            "  background-color: #10B981;"
            "  border-radius: 2px;"
            "}"
        )
        token_row.addWidget(self._progress_token_bar, stretch=1)
        self._progress_token_lbl = QLabel("")
        self._progress_token_lbl.setStyleSheet(
            "font-size: 8pt; color: #6B7280; border: none;"
        )
        self._progress_token_lbl.setFixedWidth(100)
        self._progress_token_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        token_row.addWidget(self._progress_token_lbl)
        panel_layout.addLayout(token_row)

        # Scrolling log display for progress messages
        self._progress_log_display = QTextBrowser()
        self._progress_log_display.setObjectName("ProgressLog")
        self._progress_log_display.setMaximumHeight(120)
        self._progress_log_display.setStyleSheet(
            "QTextBrowser#ProgressLog {"
            "  background-color: rgba(0, 0, 0, 0.2);"
            "  border: none;"
            "  border-radius: 2px;"
            "  font-size: 9pt;"
            "  color: #9CA3AF;"
            "  padding: 2px;"
            "}"
        )
        panel_layout.addWidget(self._progress_log_display)

        self._progress_panel.hide()

    def _update_progress_step(self, msg: str):
        """Update the current step label with a new status message.
        Called from the log_signal while the worker is processing."""
        if not hasattr(self, '_progress_panel') or not self._progress_panel:
            return
        if not self._progress_panel.isVisible():
            return

        # Detect step transitions (e.g. "STEP 1/5", "[STEP 1/5]", "🧠 [STEP 1/5]...")
        # Only increment counter on actual step patterns, not on every emoji message,
        # to avoid "Step 12/5" from sub-step log lines in RAG, memory, etc.
        import re
        step_match = re.search(
            r'\[?(?:STEP\s+)?(\d+)[/\.](\d+)\]?',
            msg, re.IGNORECASE
        )

        # Truncate long messages for the step label
        display_text = msg.strip()
        if len(display_text) > 80:
            display_text = display_text[:77] + "..."

        if step_match:
            # Real step transition — update label prominently
            current = int(step_match.group(1))
            total = int(step_match.group(2))
            self._progress_step_lbl.setText(f"🤖 {display_text}")
            self._progress_step_count = current
            self._progress_status_lbl.setText(f"Step {current}/{total}")
        else:
            # Sub-step or detail log message — update label without counting as a step
            # Only update the label if it's a substantive message (not whitespace)
            if display_text and not display_text.startswith("   "):
                self._progress_step_lbl.setText(f"🤖 {display_text}")

        # Always append to the scrolling log
        self._append_progress_log(msg)

    def _append_progress_log(self, msg: str):
        """Append a log message to the scrolling progress log."""
        if not hasattr(self, '_progress_log_display') or not self._progress_log_display:
            return
        # Color-code message by prefix
        color = "#9CA3AF"  # default grey
        if msg.startswith("🧠") or msg.startswith("🔬"):
            color = "#3B82F6"  # blue for thinking/research
        elif msg.startswith("📡") or msg.startswith("⚙️"):
            color = "#FBBF24"  # amber for transmission
        elif msg.startswith("📋"):
            color = "#10B981"  # green for mode info
        elif msg.startswith("💾") or msg.startswith("📓"):
            color = "#8B5CF6"  # purple for memory
        elif msg.startswith("❌"):
            color = "#EF4444"  # red for errors
        elif msg.startswith("✅"):
            color = "#10B981"  # green for success
        elif msg.startswith("⚠️") or msg.startswith("🔄"):
            color = "#F59E0B"  # amber for warnings/fallback

        # Escape HTML using shared helper and wrap
        safe = self._render_message_html(msg)
        self._progress_log_display.append(
            f"<div style='color: {color}; font-size: 9pt;'>{safe}</div>"
        )
        # Auto-scroll to bottom
        self._progress_log_display.verticalScrollBar().setValue(
            self._progress_log_display.verticalScrollBar().maximum()
        )

    def _animate_progress_dots(self):
        """Animate the step label with cycling dots to show activity."""
        if not hasattr(self, '_progress_panel') or not self._progress_panel:
            return
        if not self._progress_panel.isVisible():
            return
        self._progress_dot_count = (self._progress_dot_count % 3) + 1
        dots = "." * self._progress_dot_count
        current_text = self._progress_step_lbl.text()
        # Remove trailing dots from previous animation then add new ones
        base = current_text.rstrip(".")
        self._progress_step_lbl.setText(f"{base}{dots}")

    def _hide_progress_panel(self):
        """Hide the progress panel and stop the animation timer."""
        if hasattr(self, '_progress_panel') and self._progress_panel:
            self._progress_panel.hide()
        timer = getattr(self, '_progress_timer', None)
        if timer:
            try:
                timer.stop()
            except (RuntimeError, AttributeError):
                pass
            self._progress_timer = None

    def show_barge_in_badge(self, text: str = "⚡ Interrupted — Listening..."):
        """Display the duplex barge-in badge and auto-hide after 3.5s."""
        if hasattr(self, "voice_barge_in_badge") and self.voice_barge_in_badge:
            self.voice_barge_in_badge.setText(text)
            self.voice_barge_in_badge.show()
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(3500, self.voice_barge_in_badge.hide)

    def _show_typing_indicator(self):
        """Show the typing indicator above the input row.
        Legacy — delegates to the new progress panel if available."""
        if hasattr(self, '_progress_panel') and self._progress_panel:
            self._show_progress_panel()
            return
        # Fallback to original behavior if progress panel doesn't exist
        if not hasattr(self, 'typing_indicator_lbl') or not self.typing_indicator_lbl:
            return
        if self.typing_indicator_lbl.isVisible():
            return
        self.typing_indicator_lbl.setText("● ● ●  KokertechAI is thinking...")
        self.typing_indicator_lbl.show()
        if not getattr(self, '_typing_timer', None):
            from PyQt6.QtCore import QTimer
            self._typing_timer = QTimer(self)
            self._typing_timer.timeout.connect(self._animate_typing_dots)
            self._typing_dot_count = 0
            self._typing_timer.start(400)

    def _animate_typing_dots(self):
        """Cycle the typing indicator through different dot states."""
        if not hasattr(self, 'typing_indicator_lbl') or not self.typing_indicator_lbl:
            return
        if not self.typing_indicator_lbl.isVisible():
            return  # hidden; let timer be stopped elsewhere
        self._typing_dot_count = (self._typing_dot_count % 3) + 1
        filled = "● " * self._typing_dot_count
        empty = "○ " * (3 - self._typing_dot_count)
        self.typing_indicator_lbl.setText(
            f"{filled}{empty} KokertechAI is thinking..."
        )

    def _hide_typing_indicator(self):
        """Hide the typing indicator and stop the animation timer.
        Also hides the progress panel if visible."""
        # Hide new progress panel first
        self._hide_progress_panel()
        # Legacy: hide old typing indicator
        if getattr(self, 'typing_indicator_lbl', None):
            self.typing_indicator_lbl.hide()
        timer = getattr(self, '_typing_timer', None)
        if timer:
            try:
                timer.stop()
            except (RuntimeError, AttributeError):
                pass
            self._typing_timer = None

    def _on_raw_stream_toggle(self, checked):
        """Called when the 'Show raw stream' toggle changes.
        Switches the thinking display between raw stream buffer and parsed thinking.
        """
        raw_buffer = getattr(self, '_raw_stream_buffer', None)
        parsed_thinking = getattr(self, '_last_parsed_thinking', '')
        if checked and raw_buffer is not None:
            # Switch to raw stream view
            self.thinking_display.setPlainText(raw_buffer)
        elif not checked:
            # Switch to parsed thinking view
            self.thinking_display.setPlainText(parsed_thinking)

    def _on_anchor_clicked(self, url):
        """Handle clicks on HTML anchor tags (retry links + external URLs).

        In Qt6, ``QTextEdit`` never auto-opens external links, so ``anchorClicked``
        is always delivered here via ``anchorClicked.connect`` below. We re-open
        external URLs via ``QDesktopServices`` so the previous "click a link,
        browser opens" behavior is preserved (the Qt5 ``setOpenLinks(False)``
        call is no longer needed and was removed in Qt6's QTextEdit API).
        """
        url_str = url.toString() if hasattr(url, 'toString') else str(url)
        if url_str == "kokertech-retry://":
            last = getattr(self, 'last_user_text', '')
            if last and hasattr(self, 'txt_input') and hasattr(self, 'action_send_prompt'):
                self.txt_input.setPlainText(last)
                self.action_send_prompt()
                self.log_to_audit("🔁 Retried last prompt")
            else:
                self.log_to_audit("⚠️ No last prompt to retry")
            return
        # Re-open external URLs (preserves pre-Stop-button behavior).
        if url_str and url_str not in ("", "#"):
            try:
                from PyQt6.QtCore import QUrl
                from PyQt6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl(url_str))
            except Exception as e:
                self.log_to_audit(f"⚠️ Could not open link: {e}")

    def _set_tts_enabled(self, enabled):
        CONFIG["tts_enabled"] = enabled
        save_settings()

    def apply_theme(self, theme_name):
        CONFIG["active_theme"] = theme_name
        save_settings()
        # Merge built-in + custom themes
        all_themes = get_all_themes()
        t = all_themes.get(theme_name, all_themes.get("Classic (Charcoal)", self.THEMES["Classic (Charcoal)"]))
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {t['bg']}; }}
            QWidget {{ background-color: {t['bg']}; color: {t['text']}; font-family: 'Consolas', monospace; }}
            QTabWidget::pane {{ border: 1px solid {t['accent1']}; }}
            QTabBar::tab {{ background-color: {t['bg_tab']}; border: 1px solid {t['accent1']}; padding: 8px 16px; color: {t['text_tab']}; }}
            QTabBar::tab:selected {{ background-color: {t['bg']}; color: {t['text']}; border-top: 2px solid {t['accent2']}; }}
            QTextEdit, QLineEdit, QListWidget, QComboBox, QTreeWidget, QSpinBox, QTableWidget {{ background-color: {t['bg_alt']}; border: 1px solid {t['accent1']}; padding: 6px; color: {t['text']}; gridline-color: {t['accent1']}; }}
            QHeaderView::section {{ background-color: {t['bg_tab']}; color: {t['text_tab']}; padding: 4px; border: 1px solid {t['accent1']}; font-weight: bold; }}
            QPushButton {{ background-color: {t['bg_tab']}; color: {t['text']}; border: 1px solid {t['accent1']}; padding: 8px 16px; font-weight: bold; }}
            QPushButton:hover {{ background-color: {t['accent1']}; color: #000; border: 1px solid {t['accent2']}; }}
            QGroupBox {{ border: 1px solid {t['accent1']}; padding-top: 15px; margin-top: 10px; font-weight: bold; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; }}
            QFrame#Separator {{ background-color: {t['accent1']}; }}
            QTextEdit#ThinkingDisplay {{ color: {t['text_tab']}; background-color: {t['bg_tab']}; font-size: 10pt; }}
            QTextEdit#AuditLog {{ background-color: {t['bg_tab']}; font-size: 9pt; color: {t['accent2']}; }}
        """)

    def _update_protocol_badge(self):
        """Show/hide a small 📜 badge next to the input box when custom protocol is active."""
        if not hasattr(self, 'protocol_badge'):
            return
        protocol = CONFIG.get("protocol_prompt", "").strip()
        if protocol:
            self.protocol_badge.setText("📜")
            self.protocol_badge.setStyleSheet(
                "font-size: 14px; color: #F59E0B; font-weight: bold;"
            )
            self.protocol_badge.setToolTip(
                f"Custom protocol active — {len(protocol)} chars"
            )
            self.protocol_badge.show()
        else:
            self.protocol_badge.setText("")
            self.protocol_badge.setStyleSheet("")
            self.protocol_badge.setToolTip("")
            self.protocol_badge.hide()

    def _update_provider_badge(self):
        """Update the live provider status badge in the sidebar.
        Shows ❌ when provider load failed, 🟢 when loaded, or ⚪ when not yet loaded.
        """
        try:
            if getattr(self, '_provider_load_failed', False):
                self._set_provider_badge("\u274c", "Load Failed", "#EF4444")
                return
            controller = getattr(self, 'controller', None)
            if controller is not None:
                provider = getattr(controller, 'provider', None)
                if provider is not None and getattr(provider, 'is_loaded', None):
                    if provider.is_loaded():
                        self._set_provider_badge("\U0001f7e2", "Local LLM", "#10B981")
                        return
            self._set_provider_badge("\u26aa", "Not Loaded", "#9CA3AF")
        except RuntimeError:
            pass

    def _set_provider_badge(self, icon, name, color):
        """Set the badge text and style (must run on main thread)."""
        if getattr(self, '_shutting_down', False):
            return
        try:
            self.provider_badge.setText(f"{icon} {name}")
            self.provider_badge.setStyleSheet(
                f"font-size: 9pt; color: {color}; font-weight: bold; padding: 2px;"
            )
        except RuntimeError:
            pass

    def _set_model_status(self, text, color="#3B82F6"):
        """Update the model-loading status label in the sidebar.

        Thread-safe: if called from a non-main thread, hops to the main
        thread via ``utils.qt_dispatch.run_on_main_thread`` (a queued
        signal — QTimer.singleShot from a worker thread is silently
        dropped because that thread has no Qt event loop).

        Args:
            text: Status text (e.g. "🟢 Model ready (6.1s)").
            color: CSS colour hex string.
        """
        app = QApplication.instance()
        if app is not None:
            from PyQt6.QtCore import QThread
            if QThread.currentThread() != app.thread():
                from utils.qt_dispatch import run_on_main_thread
                run_on_main_thread(lambda: self._set_model_status(text, color))
                return
        if getattr(self, '_shutting_down', False):
            return
        try:
            if hasattr(self, 'model_status_lbl') and self.model_status_lbl:
                self.model_status_lbl.setText(text)
                self.model_status_lbl.setStyleSheet(
                    f"font-size: 8pt; color: {color}; font-weight: bold; padding: 2px;"
                )
                self.model_status_lbl.show()
        except RuntimeError:
            pass

    def _set_main_model_header(self, text, color="#3B82F6"):
        """Update the active model indicator in the Output Window header."""
        app = QApplication.instance()
        if app is not None:
            from PyQt6.QtCore import QThread
            if QThread.currentThread() != app.thread():
                from utils.qt_dispatch import run_on_main_thread
                run_on_main_thread(lambda: self._set_main_model_header(text, color))
                return
        if getattr(self, '_shutting_down', False):
            return
        try:
            if hasattr(self, 'main_model_header_lbl') and self.main_model_header_lbl:
                self.main_model_header_lbl.setText(text)
                self.main_model_header_lbl.setStyleSheet(
                    f"font-size: 8.5pt; color: {color}; font-weight: bold; padding: 2px 6px;"
                )
                self.main_model_header_lbl.show()
        except RuntimeError:
            pass

    def _update_chat_history_badge(self):
        """Update the chat_history_badge QLabel to reflect the current
        CONFIG[chat_history_enabled] state — ON (green) or OFF (gray).

        Called on every toggle change and at startup via _toggle_chat_history.
        """
        if not hasattr(self, 'chat_history_badge') or not self.chat_history_badge:
            return
        if getattr(self, '_shutting_down', False):
            return
        try:
            enabled = CONFIG.get("chat_history_enabled", True)
            if enabled:
                self.chat_history_badge.setText("💬 ON")
                self.chat_history_badge.setStyleSheet(
                    "font-size: 9pt; color: #10B981; font-weight: bold; padding: 2px;"
                )
                self.chat_history_badge.setToolTip("Chat history persistence is ON — NOT being persisted")
            else:
                self.chat_history_badge.setText("💬 OFF")
                self.chat_history_badge.setStyleSheet(
                    "font-size: 9pt; color: #6B7280; font-weight: bold; padding: 2px;"
                )
                self.chat_history_badge.setToolTip("Chat history persistence is OFF — NOT being persisted")
            self.chat_history_badge.show()
        except RuntimeError:
            pass

    def _update_mode_indicator(self):
        freeform = CONFIG.get("freeform_mode", False)
        mock = CONFIG.get("mock_mode", False)
        if mock:
            self.mode_indicator.setText("🎭 MOCK MODE")
            self.mode_indicator.setStyleSheet("font-size: 9pt; color: #FBBF24; font-weight: bold; padding: 2px;")
        elif freeform:
            self.mode_indicator.setText("🗣️ FREEFORM MODE")
            self.mode_indicator.setStyleSheet("font-size: 9pt; color: #3B82F6; font-weight: bold; padding: 2px;")
        else:
            self.mode_indicator.setText("📋 STRUCTURED MODE")
            self.mode_indicator.setStyleSheet("font-size: 9pt; color: #10B981; font-weight: bold; padding: 2px;")
        self._update_protocol_badge()
        # Show/hide amber protocol override badge in sidebar
        protocol = CONFIG.get("protocol_prompt", "").strip()
        if protocol:
            self.protocol_active_label.setText("📜 CUSTOM PROTOCOL")
            self.protocol_active_label.setStyleSheet(
                "font-size: 9pt; color: #F59E0B; font-weight: bold; padding: 2px;"
            )
            self.protocol_active_label.setToolTip(
                f"Custom protocol overriding system prompt — {len(protocol)} chars"
            )
            self.protocol_active_label.show()
        else:
            self.protocol_active_label.hide()
        # Also refresh provider badge when mode changes
        try:
            self._update_provider_badge()
        except Exception:
            pass

    def _on_chat_context_menu(self, pos):
        """Show a rich right-click context menu on the main chat display.

        Actions:
          - Copy selected text
          - Copy all text to clipboard
          - Select all
          - Regenerate last response (re-send last prompt)
          - Edit last message (restore to input for editing)
          - Clear chat display
          - Translate (placeholder — opens translation prompt)
        """
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background: #1A1A1A; border: 1px solid #444; color: #E0E0E0; }"
            "QMenu::item:selected { background: #3B82F6; color: white; }"
            "QMenu::separator { height: 1px; background: #333; margin: 4px 8px; }"
        )

        # ── Copy actions ──
        act_copy = QAction("📋 Copy Selected", self)
        act_copy.triggered.connect(lambda: self.chat_display.copy())
        menu.addAction(act_copy)

        act_copy_all = QAction("📋 Copy All", self)
        act_copy_all.triggered.connect(lambda: (
            QApplication.clipboard().setText(self.chat_display.toPlainText())
        ))
        menu.addAction(act_copy_all)

        act_select_all = QAction("🔲 Select All", self)
        act_select_all.triggered.connect(lambda: self.chat_display.selectAll())
        menu.addAction(act_select_all)

        menu.addSeparator()

        # ── Edit / Regen actions ──
        last_user = getattr(self, 'last_user_text', '')
        if last_user:
            act_regen = QAction("🔄 Regenerate", self)
            act_regen.setToolTip(f"Re-send: \"{last_user[:60]}...\"")
            act_regen.triggered.connect(lambda: (
                self.txt_input.setPlainText(last_user),
                self.action_send_prompt()
            ))
            menu.addAction(act_regen)

            act_edit = QAction("✏️ Edit Last Message", self)
            act_edit.triggered.connect(lambda: (
                self.txt_input.setPlainText(last_user),
                self.txt_input.setFocus()
            ))
            menu.addAction(act_edit)

        # ── Translate (opens a prompt in the input box) ──
        selected = self.chat_display.textCursor().selectedText().strip()
        if selected:
            act_translate = QAction(f"🌐 Translate: \"{selected[:30]}...\"", self)
            act_translate.triggered.connect(lambda: (
                self.txt_input.setPlainText(
                    f"Translate the following to English: {selected}"
                ),
                self.action_send_prompt()
            ))
            menu.addAction(act_translate)

        menu.addSeparator()

        act_clear = QAction("🗑️ Clear Display", self)
        act_clear.triggered.connect(self.chat_display.clear)
        menu.addAction(act_clear)

        menu.exec(self.chat_display.mapToGlobal(pos))

    def _on_compare_toggle(self, enabled):
        """Show/hide the multi-model compare panel and refresh model list."""
        self.compare_panel.setVisible(enabled)
        if enabled:
            self._populate_model_checkboxes()

    def _on_ab_toggle(self, enabled):
        """Show/hide the A/B persona testing panel and populate dropdowns."""
        self.ab_panel.setVisible(enabled)
        if enabled:
            self._populate_ab_personas()

    def _populate_ab_personas(self):
        """Populate the A/B persona dropdowns from CONFIG saved_personas."""
        saved_personas = CONFIG.get("saved_personas", [])
        current_persona = CONFIG.get("active_persona", "You are a helpful AI assistant.")

        self.ab_persona_a_combo.blockSignals(True)
        self.ab_persona_b_combo.blockSignals(True)

        self.ab_persona_a_combo.clear()
        self.ab_persona_b_combo.clear()

        if saved_personas:
            self.ab_persona_a_combo.addItems(saved_personas)
            self.ab_persona_b_combo.addItems(saved_personas)

        # Persona A defaults to the active persona
        idx_a = self.ab_persona_a_combo.findText(current_persona)
        if idx_a >= 0:
            self.ab_persona_a_combo.setCurrentIndex(idx_a)
        else:
            self.ab_persona_a_combo.setCurrentText(current_persona)

        # Persona B defaults to a different persona (if available)
        if len(saved_personas) > 1:
            alt_idx = 1 if idx_a != 1 else 2
            if alt_idx < len(saved_personas):
                self.ab_persona_b_combo.setCurrentIndex(alt_idx)
            elif saved_personas:
                self.ab_persona_b_combo.setCurrentText(saved_personas[-1])
        elif saved_personas:
            self.ab_persona_b_combo.setCurrentText(saved_personas[0])

        self.ab_persona_a_combo.blockSignals(False)
        self.ab_persona_b_combo.blockSignals(False)

        self._update_ab_preview()

    def _update_ab_preview(self):
        """Update the preview label showing selected persona snippets."""
        a = self.ab_persona_a_combo.currentText().strip()[:60]
        b = self.ab_persona_b_combo.currentText().strip()[:60]
        if a or b:
            self.ab_preview.setText(f"🅰️ {a}…  vs  🅱️ {b}…")

    def _get_ab_personas(self):
        """Return the two selected personas for A/B testing.

        Returns:
            (persona_a, persona_b) tuple of strings, or (None, None)
            if A/B mode is not active.
        """
        if not hasattr(self, 'ab_toggle') or not self.ab_toggle.isChecked():
            return None, None
        a = self.ab_persona_a_combo.currentText().strip()
        b = self.ab_persona_b_combo.currentText().strip()
        if not a or not b:
            return None, None
        return a, b

    # ── Tab switching helpers ───────────────────────────────────────

    def _switch_to_tab(self, tab_widget_or_name):
        """Switch to a tab by widget reference or name string.

        If given a string, delegates to ``_switch_to_tab_by_name``.
        If given a widget, finds its index via ``tabs.indexOf()``.
        Skips safely when ``tabs`` is missing, the argument is None,
        or the index is -1.
        """
        if tab_widget_or_name is None:
            return
        if isinstance(tab_widget_or_name, str):
            self._switch_to_tab_by_name(tab_widget_or_name)
            return
        if not hasattr(self, 'tabs') or not self.tabs:
            return
        try:
            index = self.tabs.indexOf(tab_widget_or_name)
            if index < 0 and hasattr(self.tabs, 'widget') and hasattr(self.tabs, 'count') and callable(self.tabs.count):
                cnt = self.tabs.count()
                if isinstance(cnt, int):
                    for i in range(cnt):
                        w = self.tabs.widget(i)
                        if isinstance(w, QScrollArea) and w.widget() is tab_widget_or_name:
                            index = i
                            break
        except (RuntimeError, AttributeError, TypeError):
            return
        if index >= 0:
            self.tabs.setCurrentIndex(index)

    def _add_scrollable_tab(self, widget: QWidget, title: str) -> None:
        """Add a tab to self.tabs, wrapping in a QScrollArea if not already scrollable."""
        if widget is None or not hasattr(self, 'tabs') or not self.tabs:
            return
        if isinstance(widget, QScrollArea) or title in ("Neural Graph", "Web View", "Workspace"):
            self.tabs.addTab(widget, title)
            return
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        self.tabs.addTab(scroll, title)

    def _switch_to_tab_by_name(self, name):
        """Switch to a tab by exact ``tabText`` match.

        Iterates all tab indices, compares ``tabs.tabText(i)``, and
        calls ``tabs.setCurrentIndex(i)`` on the first match.
        No-ops safely when ``tabs`` is missing, ``name`` is None/empty,
        or no match is found.
        """
        if not name:
            return
        if not hasattr(self, 'tabs') or not self.tabs:
            return
        try:
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i) == name:
                    self.tabs.setCurrentIndex(i)
                    return
        except RuntimeError:
            pass

    # ── Model progress helpers ──────────────────────────────────────

    def _show_model_progress(self, percent, status=None):
        """Show or update the model-loading progress bar and label.

        At 0 % or 100 % the widgets are hidden; intermediate values
        show the bar and set the label to ``"X% — status"`` or just
        ``"X%"`` when *status* is ``None``.

        Safe when ``_model_progress_bar`` / ``_model_progress_lbl``
        don't exist yet and during shutdown.
        """
        app = QApplication.instance()
        if app is not None:
            from PyQt6.QtCore import QThread
            if QThread.currentThread() != app.thread():
                from utils.qt_dispatch import run_on_main_thread
                run_on_main_thread(lambda: self._show_model_progress(percent, status))
                return
        if getattr(self, '_shutting_down', False):
            return
        if not hasattr(self, '_model_progress_bar') or not hasattr(self, '_model_progress_lbl'):
            return
        try:
            if percent <= 0 or percent >= 100:
                self._model_progress_bar.hide()
                self._model_progress_lbl.hide()
                return
            self._model_progress_bar.setValue(percent)
            self._model_progress_bar.show()
            label_text = f"{percent}%"
            if status:
                label_text = f"{percent}% — {status}"
            self._model_progress_lbl.setText(label_text)
            self._model_progress_lbl.show()
        except RuntimeError:
            pass

    def _hide_model_progress(self):
        """Hide the model-loading progress bar and label.

        Safe when the widgets don't exist yet.
        """
        app = QApplication.instance()
        if app is not None:
            from PyQt6.QtCore import QThread
            if QThread.currentThread() != app.thread():
                from utils.qt_dispatch import run_on_main_thread
                run_on_main_thread(self._hide_model_progress)
                return
        if getattr(self, '_shutting_down', False):
            return
        try:
            if hasattr(self, '_model_progress_bar') and self._model_progress_bar:
                self._model_progress_bar.hide()
        except RuntimeError:
            pass
        try:
            if hasattr(self, '_model_progress_lbl') and self._model_progress_lbl:
                self._model_progress_lbl.hide()
        except RuntimeError:
            pass

    def _populate_model_checkboxes(self):
        """Populate model checkboxes from model_registry for online providers.
        Runs the network calls in a background thread to avoid freezing the UI.
        """
        # Clear existing checkboxes immediately
        for i in reversed(range(self.model_checkbox_layout.count())):
            widget = self.model_checkbox_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        # Show loading indicator
        loading = QLabel("Loading models...")
        loading.setStyleSheet("color: #9CA3AF; font-size: 8pt; padding: 2px;")
        self.model_checkbox_layout.addWidget(loading)

        def _fetch_and_build():
            """Fetch models in background thread, then update UI on main thread."""
            try:
                import model_registry
                all_models = model_registry.list_models()
            except Exception as e:
                all_models = [f"ERROR:{e}"]

            from utils.qt_dispatch import run_on_main_thread
            run_on_main_thread(lambda: self._build_model_checkboxes(all_models))

        import threading
        threading.Thread(target=_fetch_and_build, daemon=True).start()

    def _build_model_checkboxes(self, all_models):
        """Build model checkbox widgets from fetched model list (main thread)."""
        # Clear loading indicator
        for i in reversed(range(self.model_checkbox_layout.count())):
            widget = self.model_checkbox_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        if not all_models or (isinstance(all_models, list) and len(all_models) > 0
                              and isinstance(all_models[0], str)
                              and all_models[0].startswith("ERROR:")):
            err_msg = all_models[0].replace("ERROR:", "") if all_models else "Unknown error"
            no_models = QLabel(f"Could not load models: {err_msg}")
            no_models.setStyleSheet("color: #EF4444; font-size: 8pt; padding: 2px;")
            self.model_checkbox_layout.addWidget(no_models)
            return

        if not all_models:
            placeholder = QLabel("No models found — ensure a provider is running")
            placeholder.setStyleSheet("color: #9CA3AF; font-size: 8pt; padding: 2px;")
            self.model_checkbox_layout.addWidget(placeholder)
            return

        # Group models by provider, show short names after provider prefix is clear
        provider_groups = {}
        for m in all_models:
            prov = m.get("provider", "unknown")
            provider_groups.setdefault(prov, []).append(m)

        for provider in sorted(provider_groups.keys()):
            models = provider_groups[provider]
            header = QLabel(f"{provider.upper()}")
            header.setStyleSheet(
                "font-size: 7pt; font-weight: bold; color: #3B82F6; "
                "padding: 1px 0; margin-top: 2px;"
            )
            self.model_checkbox_layout.addWidget(header)

            for m in models:
                model_name = m.get("name", "?")
                # Show just the short name (after last / or :) since provider is in the header
                short_name = model_name.split("/")[-1].split(":")[-1]
                label_text = f"{provider}/{short_name}" if "/" in model_name else short_name
                if len(label_text) > 50:
                    label_text = label_text[:47] + "..."
                cb = QCheckBox(label_text)
                cb.setStyleSheet("font-size: 8pt; padding: 1px; color: #E0E0E0;")
                cb.setProperty("model_name", model_name)
                cb.setProperty("provider", provider)
                self.model_checkbox_layout.addWidget(cb)

    def _get_selected_model_specs(self):
        """Return list of model_spec dicts for selected checkboxes.

        Returns:
            [{"provider": str, "model": str, "label": str}, ...]
        """
        specs = []
        if not hasattr(self, 'model_checkbox_layout'):
            return specs
        for i in range(self.model_checkbox_layout.count()):
            widget = self.model_checkbox_layout.itemAt(i).widget()
            if isinstance(widget, QCheckBox) and widget.isChecked():
                model_name = widget.property("model_name")
                provider = widget.property("provider")
                if model_name and provider:
                    specs.append({
                        "provider": provider,
                        "model": model_name,
                        "label": f"{provider}/{model_name}",
                    })
        return specs

    def add_suggestion_card(self, suggestion_data):
        card = QFrame()
        card.setStyleSheet("background-color: #252525; border: 1px solid #3D3D3D; border-radius: 6px; padding: 6px;")
        card_layout = QVBoxLayout(card)
        pred_text = str(suggestion_data.get("prediction", "Suggestion available.") or "").strip()
        lbl = QLabel(pred_text or "Suggestion available.")
        lbl.setWordWrap(True)
        lbl.setStyleSheet("border: none; color: #E0E0E0;")
        card_layout.addWidget(lbl)
        btn_approve = QPushButton("✅ Approve")
        btn_approve.setStyleSheet("font-size: 8pt; padding: 3px 8px; background-color: #1e3a5f; border-color: #2563eb;")
        btn_approve.clicked.connect(
            lambda checked=False, p=pred_text, c=card: [
                self.txt_input.setPlainText(p),
                self.action_send_prompt(),
                c.hide(),
            ]
        )
        card_layout.addWidget(btn_approve)
        self.suggestions_content_layout.insertWidget(self.suggestions_content_layout.count() - 1, card)
        card.show()

    def _apply_default_window_geometry(self):
        """Size and position the window optimally based on available screen geometry.

        - On compact / laptop displays (avail width <= 1366 or height <= 768), sets
          a comfortable restore fallback and starts maximized so that no panels,
          tabs, or status bars are clipped or hidden by the taskbar.
        - On standard and large displays (1080p, 1440p, 4K), calculates proportional
          bounds (~85% width, ~88% height, clamped within comfortable minimums and
          maximums) and centers the window cleanly.
        """
        get_scr = getattr(self, "screen", None)
        screen = get_scr() if callable(get_scr) else None
        if screen is None:
            screen = QApplication.primaryScreen()

        avail = screen.availableGeometry() if screen is not None else None
        if avail is None:
            if hasattr(self, "resize"):
                self.resize(1200, 750)
            return

        avail_w = avail.width()
        avail_h = avail.height()
        avail_x = avail.x()
        avail_y = avail.y()

        # Compact displays (e.g. 1280x720, 1366x768, 1080p @ 150% scaling)
        if avail_w <= 1366 or avail_h <= 768:
            restore_w = max(min(avail_w - 40, 1150), 400)
            restore_h = max(min(avail_h - 40, 640), 300)
            restore_x = avail_x + max(0, (avail_w - restore_w) // 2)
            restore_y = avail_y + max(0, (avail_h - restore_h) // 2)
            if hasattr(self, "setGeometry"):
                self.setGeometry(restore_x, restore_y, restore_w, restore_h)
            elif hasattr(self, "resize") and hasattr(self, "move"):
                self.resize(restore_w, restore_h)
                self.move(restore_x, restore_y)
            if hasattr(self, "showMaximized"):
                self.showMaximized()
            return

        # Standard / high-res displays (1080p @ 100%, 1440p, 4K, Ultrawide)
        target_w = min(max(int(avail_w * 0.85), 1100), min(2200, avail_w - 40))
        target_h = min(max(int(avail_h * 0.88), 750), min(1300, avail_h - 40))
        x = avail_x + (avail_w - target_w) // 2
        y = avail_y + (avail_h - target_h) // 2

        if hasattr(self, "setGeometry"):
            self.setGeometry(x, y, target_w, target_h)
        elif hasattr(self, "resize") and hasattr(self, "move"):
            self.resize(target_w, target_h)
            self.move(x, y)

    def _restore_window_geometry(self):
        """Restore saved window geometry from CONFIG (saved by closeEvent).
        Falls back to screen-optimized default geometry if no saved geometry exists.
        """
        import base64
        geom_b64 = CONFIG.get("_window_geometry")
        restored = False
        if geom_b64:
            try:
                data = base64.b64decode(geom_b64)
                # saveGeometry stores a C struct as bytes; restoreGeometry expects a QByteArray
                from PyQt6.QtCore import QByteArray
                restored = bool(self.restoreGeometry(QByteArray(data)))
            except (ValueError, TypeError):
                pass
        if CONFIG.get("_window_maximized", False):
            if hasattr(self, "showMaximized"):
                self.showMaximized()
        elif not restored and not geom_b64:
            self._apply_default_window_geometry()

    def _clamp_window_to_screen(self):
        """Clamp window size to available screen geometry to prevent
        QWindowsWindow::setGeometry warnings on smaller displays.

        Uses frameGeometry() to account for window chrome (title bar,
        borders) so the full window frame fits within the available area.
        Safe to call both before and after show() — uses frame geometry
        which is available once the widget is constructed.
        """
        # Guard: timer may fire after closeEvent during shutdown.
        # Wrap in try/except RuntimeError for objects created via __new__
        # without calling __init__ (some tests do this).
        try:
            if getattr(self, '_shutting_down', False):
                return
        except RuntimeError:
            return

        try:
            if hasattr(self, 'isMaximized') and self.isMaximized():
                return
        except (AttributeError, RuntimeError):
            pass

        screen = self.screen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        margin = 20
        max_fw = avail.width() - margin
        max_fh = avail.height() - margin

        # Use frame geometry to account for window chrome.
        # Fall back to client geometry if frameGeometry() is not available
        # (e.g. bare mixin instances in unit tests).
        try:
            frame = self.frameGeometry()
            frame_available = True
        except AttributeError:
            frame = None
            frame_available = False

        if frame_available:
            resized = False
            # Compare frame size against available screen area
            if frame.width() > max_fw or frame.height() > max_fh:
                # Calculate new frame size clamped to available area
                new_fw = min(frame.width(), max_fw)
                new_fh = min(frame.height(), max_fh)

                # Convert frame size to client size (resize() operates on client area)
                chrome_w = frame.width() - self.width()
                chrome_h = frame.height() - self.height()
                new_client_w = max(new_fw - chrome_w, 100)
                new_client_h = max(new_fh - chrome_h, 100)

                self.resize(new_client_w, new_client_h)
                resized = True
                frame_w = new_fw
                frame_h = new_fh
            else:
                frame_w = frame.width()
                frame_h = frame.height()

            if resized:
                # Re-center on the available screen area using frame dimensions
                x = max(avail.x(), avail.x() + (avail.width() - frame_w) // 2)
                y = max(avail.y(), avail.y() + (avail.height() - frame_h) // 2)
                self.move(x, y)
            else:
                # Ensure title bar and window frame are reachable on the active screen
                cur_x = frame.x()
                cur_y = frame.y()
                safe_x = max(avail.x(), min(cur_x, avail.x() + avail.width() - frame_w))
                safe_y = max(avail.y(), min(cur_y, avail.y() + avail.height() - frame_h))
                if safe_x != cur_x or safe_y != cur_y:
                    self.move(safe_x, safe_y)
        else:
            # Fallback: compare client geometry directly (unit tests / bare mixins)
            current_w = self.width()
            current_h = self.height()
            if current_w <= (max_fw - 20) and current_h <= (max_fh - 20):
                return  # Already fits with extra margin for untracked chrome
            self.resize(
                min(current_w, max_fw),
                min(current_h, max_fh),
            )
            # Re-center on the available screen area
            x = max(avail.x(), avail.x() + (avail.width() - self.width()) // 2)
            y = max(avail.y(), avail.y() + (avail.height() - self.height()) // 2)
            self.move(x, y)

    def init_ui(self):
        self.setWindowTitle("KokertechAI Executive Core — Hardware Optimized")
        self._apply_default_window_geometry()
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Mini-HUD quick access button pinned to tab bar corner (visible across all tabs)
        self.btn_corner_hud = QPushButton("⚡ Mini-HUD")
        self.btn_corner_hud.setObjectName("HudCornerButton")
        self.btn_corner_hud.setToolTip(
            "Toggle floating mini-Kokertechai command palette (Ctrl+Space / Ctrl+Alt+Space)"
        )
        self.btn_corner_hud.setStyleSheet(
            "QPushButton#HudCornerButton { "
            "  background-color: rgba(59, 130, 246, 0.2); "
            "  color: #60A5FA; "
            "  border: 1px solid #3B82F6; "
            "  border-radius: 4px; "
            "  padding: 4px 10px; "
            "  font-size: 8pt; "
            "  font-weight: bold; "
            "  margin-right: 6px; "
            "} "
            "QPushButton#HudCornerButton:hover { "
            "  background-color: #3B82F6; "
            "  color: #FFFFFF; "
            "}"
        )
        self.btn_corner_hud.clicked.connect(self._toggle_mini_hud)
        self.tabs.setCornerWidget(self.btn_corner_hud, Qt.Corner.TopRightCorner)

        core_tab = QWidget()
        main_layout = QHBoxLayout(core_tab)

        sidebar = QWidget()
        sidebar.setFixedWidth(260)
        sidebar_layout = QVBoxLayout(sidebar)
        self.brand_label = QLabel("KOKERTECH AI")
        self.brand_label.setToolTip("KokertechAI — autonomous desktop AI agent. 100% offline, user-owned, no telemetry.")
        sidebar_layout.addWidget(self.brand_label)
        self.vram_status_lbl = QLabel(f"STATUS: VRAM Capped ({CONFIG['vram_limit_mb']}MB)")
        self.vram_status_lbl.setStyleSheet("font-size: 9pt; color: #9CA3AF; padding: 2px;")
        self.vram_status_lbl.setToolTip("VRAM usage monitor — shows current GPU memory capped at configured limit")
        sidebar_layout.addWidget(self.vram_status_lbl)
        self.workspace_lbl = QLabel(f"Workspace: {WORKSPACE_DIR}")
        self.workspace_lbl.setToolTip("Active workspace directory — files and plugins are loaded from this location")
        sidebar_layout.addWidget(self.workspace_lbl)
        self.context_lbl = QLabel("Persistent Context: ACTIVE")
        self.context_lbl.setStyleSheet("font-size: 9pt; color: #10B981; font-weight: bold; padding: 2px;")
        self.context_lbl.setToolTip("Persistent memory context — consolidates short-term chat into long-term memory vault")
        sidebar_layout.addWidget(self.context_lbl)
        self.mode_indicator = QLabel()
        self.mode_indicator.setToolTip("Current mode — Structured (XML tags) for reliable command extraction or Freeform for natural conversation")
        sidebar_layout.addWidget(self.mode_indicator)
        self.protocol_active_label = QLabel()
        self.protocol_active_label.hide()
        sidebar_layout.addWidget(self.protocol_active_label)
        # Live provider status badge
        # Prewarm state flags
        self._provider_load_failed = False
        # Model-loading progress widgets (created hidden)
        self._model_progress_bar = QProgressBar()
        self._model_progress_bar.setRange(0, 100)
        self._model_progress_bar.setValue(0)
        self._model_progress_bar.setTextVisible(False)
        self._model_progress_bar.setFixedHeight(8)
        self._model_progress_bar.setStyleSheet(
            "QProgressBar { background-color: rgba(59, 130, 246, 0.15);"
            "  border: 1px solid rgba(59, 130, 246, 0.3); border-radius: 3px; }"
            "QProgressBar::chunk { background-color: #3B82F6; border-radius: 2px; }"
        )
        self._model_progress_bar.hide()
        self._model_progress_lbl = QLabel("")
        self._model_progress_lbl.setStyleSheet("font-size: 8pt; color: #3B82F6; padding: 0 2px;")
        self._model_progress_lbl.hide()

        self.provider_badge = QLabel()
        self.provider_badge.setStyleSheet("font-size: 9pt; color: #9CA3AF; padding: 2px;")
        self.provider_badge.setToolTip("AI provider status — click to open Settings")
        self.provider_badge.mousePressEvent = lambda e: self._switch_to_tab_by_name("Settings")
        sidebar_layout.addWidget(self.provider_badge)
        self.model_status_lbl = QLabel("🔵 Loading model…")
        self.model_status_lbl.setStyleSheet(
            "font-size: 8pt; color: #3B82F6; font-weight: bold; padding: 2px;"
        )
        self.model_status_lbl.setToolTip(
            "Model loading status — shows progress as the GGUF model loads at startup"
        )
        sidebar_layout.addWidget(self.model_status_lbl)
        sidebar_layout.addWidget(self._model_progress_bar)
        sidebar_layout.addWidget(self._model_progress_lbl)
        self._update_mode_indicator()

        btn_view_vault = QPushButton("REBUILD DB VECTORS")
        btn_view_vault.setToolTip("Rebuild database vectors — re-indexes the memory vault FTS5 search index and embedding cache")
        btn_view_vault.clicked.connect(self.run_vault_cleaner)
        sidebar_layout.addWidget(btn_view_vault)
        btn_settings = QPushButton("SYSTEM SETTINGS")
        btn_settings.clicked.connect(lambda: self._switch_to_tab_by_name("Settings"))
        sidebar_layout.addWidget(btn_settings)

        self.btn_sidebar_hud = QPushButton("⚡ MINI-HUD PALETTE")
        self.btn_sidebar_hud.setObjectName("SidebarMiniHudBtn")
        self.btn_sidebar_hud.setToolTip(
            "Open floating mini-Kokertechai command palette (Ctrl+Space / Ctrl+Alt+Space)"
        )
        self.btn_sidebar_hud.clicked.connect(self._toggle_mini_hud)
        sidebar_layout.addWidget(self.btn_sidebar_hud)

        tts_row = QHBoxLayout()
        self.chk_tts = QCheckBox("TTS")
        self.chk_tts.setToolTip("Toggle text-to-speech — reads AI responses aloud using Piper or SAPI voice")
        self.chk_tts.setChecked(CONFIG.get("tts_enabled", True))
        self.chk_tts.toggled.connect(lambda v: self._set_tts_enabled(v))
        tts_row.addWidget(self.chk_tts)
        btn_tts_test = QPushButton("Test")
        btn_tts_test.setToolTip("Test voice output — speaks a short phrase to verify TTS is working")
        btn_tts_test.clicked.connect(lambda: self.voice_output.speak("Voice output is working."))
        tts_row.addWidget(btn_tts_test)
        sidebar_layout.addLayout(tts_row)

        # Wake word toggle
        if _WAKE_WORD_AVAILABLE:
            wake_row = QHBoxLayout()
            self.chk_wake_word = QCheckBox("Wake Word")
            self.chk_wake_word.setToolTip(f"Listen for '{CONFIG.get('wake_word', 'hey koker')}' to trigger voice input")
            self.chk_wake_word.setChecked(CONFIG.get("wake_word_enabled", False))
            self.chk_wake_word.toggled.connect(self._toggle_wake_word)
            wake_row.addWidget(self.chk_wake_word)
            self.wake_status_lbl = QLabel("")
            self.wake_status_lbl.setStyleSheet("font-size: 8pt; color: #9CA3AF;")
            wake_row.addWidget(self.wake_status_lbl)
            sidebar_layout.addLayout(wake_row)
            if CONFIG.get("wake_word_enabled", False):
                self._start_wake_listener()

        copilot_label = QLabel("Copilot Tools")
        copilot_label.setStyleSheet("font-size: 9pt; font-weight: bold; padding-top: 6px;")
        copilot_label.setToolTip("Copilot utilities — screen capture, OCR text extraction, and vision analysis")
        sidebar_layout.addWidget(copilot_label)
        self.vision_model_lbl = QLabel("")
        self.vision_model_lbl.setObjectName("VisionModelLabel")
        self.vision_model_lbl.setStyleSheet("font-size: 8pt; color: #9CA3AF; padding: 1px 2px;")
        self.vision_model_lbl.setToolTip("Active vision model for screen capture and image analysis — click to open Settings")
        self.vision_model_lbl.mousePressEvent = lambda e: self._switch_to_tab_by_name("Settings")
        sidebar_layout.addWidget(self.vision_model_lbl)
        self.btn_screen = QPushButton("Capture Screen")
        self.btn_screen.setToolTip("Capture screen — takes a screenshot for vision analysis or OCR processing")
        self.btn_screen.clicked.connect(self._action_capture_screen)
        sidebar_layout.addWidget(self.btn_screen)
        self.btn_ocr = QPushButton("OCR Extract Text")
        self.btn_ocr.setToolTip("OCR image text — extracts text from screenshots or uploaded images using optical character recognition")
        self.btn_ocr.clicked.connect(self._action_ocr_file)
        sidebar_layout.addWidget(self.btn_ocr)

        self.sidebar_tabs = QTabWidget()
        self.sidebar_tabs.setObjectName("SidebarTabs")
        self.sidebar_tabs.setToolTip("Session tabs — browse and switch between saved conversation history sessions")
        sessions_tab = QWidget()
        sessions_layout = QVBoxLayout(sessions_tab)
        sessions_layout.setContentsMargins(4, 4, 4, 4)
        self.session_list = QListWidget()
        self.session_list.addItem("Current Project Session")
        sessions_layout.addWidget(self.session_list)
        self.sidebar_tabs.addTab(sessions_tab, "Sessions")
        sidebar_layout.addWidget(self.sidebar_tabs)
        # ── Streaming Latency Monitor label ──
        self.latency_label = QLabel("")
        self.latency_label.setStyleSheet("font-size: 8pt; color: #6B7280; padding: 0 2px;")
        self.latency_label.setToolTip("Streaming latency monitor — shows real-time tokens per second during generation")
        sidebar_layout.addWidget(self.latency_label)

        self.btn_wipe = QPushButton("WIPE SHORT-TERM MEMORY")
        self.btn_wipe.setStyleSheet("background-color: #7f1d1d; border-color: #991b1b;")
        self.btn_wipe.setToolTip("Wipe short-term memory — clears the current chat history from the controller session")
        self.btn_wipe.clicked.connect(self.action_wipe_memory)
        sidebar_layout.addWidget(self.btn_wipe)

        sidebar_scroll = QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_scroll.setFixedWidth(272)
        sidebar_scroll.setWidget(sidebar)
        main_layout.addWidget(sidebar_scroll)

        v_sep = QFrame(); v_sep.setFrameShape(QFrame.Shape.VLine); v_sep.setObjectName("Separator")
        main_layout.addWidget(v_sep)

        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        self.main_chat_splitter = QSplitter(Qt.Orientation.Horizontal)
        center_layout.addWidget(self.main_chat_splitter)
        chat_area_widget = QWidget()
        chat_area_layout = QVBoxLayout(chat_area_widget)
        chat_area_layout.setContentsMargins(0, 0, 0, 0)
        chat_splitter = QSplitter(Qt.Orientation.Vertical)
        chat_area_layout.addWidget(chat_splitter)
        upper_panel = QWidget()
        self.upper_layout = QVBoxLayout(upper_panel)
        upper_header = QHBoxLayout()
        upper_header.addWidget(QLabel("Output Window"))
        self.main_model_header_lbl = QLabel("")
        self.main_model_header_lbl.setObjectName("MainModelHeaderLabel")
        self.main_model_header_lbl.setStyleSheet("font-size: 8.5pt; color: #9CA3AF; padding: 2px 6px;")
        self.main_model_header_lbl.setToolTip("Active main AI model — click to open Settings")
        self.main_model_header_lbl.mousePressEvent = lambda e: self._switch_to_tab_by_name("Settings")
        upper_header.addWidget(self.main_model_header_lbl)
        upper_header.addStretch()
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(lambda: self.chat_display.clear())
        upper_header.addWidget(btn_clear)
        self.upper_layout.addLayout(upper_header)
        self.chat_display = QTextBrowser()

        self.chat_display.setReadOnly(True)
        self.chat_display.setStyleSheet("font-size: 11pt; line-height: 1.4;")
        # Rich context menu — right-click actions
        self.chat_display.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_display.customContextMenuRequested.connect(self._on_chat_context_menu)
        # Intercept anchor clicks (e.g. retry links) instead of letting
        # QTextBrowser auto-open them.
        self.chat_display.setOpenExternalLinks(False)
        try:
            self.chat_display.anchorClicked.connect(self._on_anchor_clicked)
        except Exception:
            # If anchorClicked isn't available for the current Qt build,
            # retry links will be non-functional but the app will start.
            pass


        self.upper_layout.addWidget(self.chat_display)
        chat_splitter.addWidget(upper_panel)
        lower_panel = QWidget()
        lower_layout = QVBoxLayout(lower_panel)
        thinking_header = QHBoxLayout()
        thinking_header.addWidget(QLabel("Internal Reasoning"))
        thinking_header.addStretch()
        self.show_raw_stream_toggle = QCheckBox("Show raw stream")
        self.show_raw_stream_toggle.setToolTip(
            "When checked, the thinking display shows the progressive token stream "
            "instead of the final parsed &lt;thinking&gt; content"
        )
        self.show_raw_stream_toggle.setStyleSheet(
            "font-size: 8pt; color: #9CA3AF;"
        )
        self.show_raw_stream_toggle.toggled.connect(self._on_raw_stream_toggle)
        thinking_header.addWidget(self.show_raw_stream_toggle)
        lower_layout.addLayout(thinking_header)
        self.thinking_display = QTextEdit()
        self.thinking_display.setReadOnly(True)
        self.thinking_display.setObjectName("ThinkingDisplay")
        lower_layout.addWidget(self.thinking_display)
        chat_splitter.addWidget(lower_panel)
        chat_splitter.setSizes([500, 150])
        chat_splitter.setStretchFactor(0, 3)
        chat_splitter.setStretchFactor(1, 1)

        # ── Multi-Model Compare Panel (collapsible) ──
        # ── Multi-Model Compare Panel (collapsible) ──
        self.compare_panel = QFrame()
        self.compare_panel.setObjectName("ComparePanel")
        self.compare_panel.setStyleSheet(
            "QFrame#ComparePanel { border: 1px solid #3B82F6; border-radius: 4px; "
            "padding: 2px; background-color: rgba(59, 130, 246, 0.05); }"
        )
        compare_layout = QVBoxLayout(self.compare_panel)
        compare_layout.setContentsMargins(4, 4, 4, 4)
        compare_layout.setSpacing(2)
        compare_header = QHBoxLayout()
        self.compare_toggle = QCheckBox("🔀 Compare Models")
        self.compare_toggle.setToolTip(
            "Enable to send the same prompt to multiple AI models side-by-side"
        )
        self.compare_toggle.toggled.connect(self._on_compare_toggle)
        compare_header.addWidget(self.compare_toggle)
        compare_header.addStretch()
        btn_refresh_models = QPushButton("Refresh")
        btn_refresh_models.setFixedWidth(70)
        btn_refresh_models.clicked.connect(self._populate_model_checkboxes)
        compare_header.addWidget(btn_refresh_models)
        compare_layout.addLayout(compare_header)

        self.model_scroll = QScrollArea()
        self.model_scroll.setWidgetResizable(True)
        self.model_scroll.setMaximumHeight(120)
        self.model_scroll.setStyleSheet("border: none;")
        self.model_scroll_container = QWidget()
        self.model_checkbox_layout = QVBoxLayout(self.model_scroll_container)
        self.model_checkbox_layout.setContentsMargins(2, 2, 2, 2)
        self.model_checkbox_layout.setSpacing(1)
        self.model_scroll.setWidget(self.model_scroll_container)
        compare_layout.addWidget(self.model_scroll)
        self.compare_panel.hide()

        # ── Persona A/B Testing Panel (collapsible) ──
        self.ab_panel = QFrame()
        self.ab_panel.setObjectName("ABPanel")
        self.ab_panel.setStyleSheet(
            "QFrame#ABPanel { border: 1px solid #F59E0B; border-radius: 4px; "
            "padding: 2px; background-color: rgba(245, 158, 11, 0.05); }"
        )
        ab_layout = QVBoxLayout(self.ab_panel)
        ab_layout.setContentsMargins(4, 4, 4, 4)
        ab_layout.setSpacing(2)
        ab_header = QHBoxLayout()
        self.ab_toggle = QCheckBox("🧪 A/B Test Personas")
        self.ab_toggle.setToolTip(
            "Enable to send the same prompt to two different personas and compare responses"
        )
        self.ab_toggle.toggled.connect(self._on_ab_toggle)
        ab_header.addWidget(self.ab_toggle)
        ab_header.addStretch()
        ab_layout.addLayout(ab_header)

        self.ab_persona_a_combo = QComboBox()
        self.ab_persona_a_combo.setEditable(True)
        self.ab_persona_a_combo.setPlaceholderText("Persona A (blue)")
        self.ab_persona_a_combo.currentTextChanged.connect(self._update_ab_preview)
        ab_layout.addWidget(QLabel("🅰️ Persona A:"))
        ab_layout.addWidget(self.ab_persona_a_combo)

        self.ab_persona_b_combo = QComboBox()
        self.ab_persona_b_combo.setEditable(True)
        self.ab_persona_b_combo.setPlaceholderText("Persona B (amber)")
        self.ab_persona_b_combo.currentTextChanged.connect(self._update_ab_preview)
        ab_layout.addWidget(QLabel("🅱️ Persona B:"))
        ab_layout.addWidget(self.ab_persona_b_combo)

        # Label to show selected persona previews
        self.ab_preview = QLabel("")
        self.ab_preview.setWordWrap(True)
        self.ab_preview.setStyleSheet(
            "font-size: 7pt; color: #6B7280; padding: 1px;"
        )
        ab_layout.addWidget(self.ab_preview)
        self.ab_panel.hide()

        # ── A/B Vote Bar (hidden until A/B test result displays) ──
        self.ab_vote_bar = QFrame()
        self.ab_vote_bar.setObjectName("ABVoteBar")
        self.ab_vote_bar.setStyleSheet(
            "QFrame#ABVoteBar { border: 1px solid #F59E0B; border-radius: 4px; "
            "padding: 3px; background-color: rgba(245, 158, 11, 0.08); }"
        )
        ab_vote_layout = QHBoxLayout(self.ab_vote_bar)
        ab_vote_layout.setContentsMargins(6, 3, 6, 3)
        ab_vote_layout.setSpacing(6)
        self.ab_vote_label = QLabel("Which response was better?")
        self.ab_vote_label.setStyleSheet(
            "font-size: 9pt; color: #F59E0B; font-weight: bold; border: none;"
        )
        ab_vote_layout.addWidget(self.ab_vote_label)
        ab_vote_layout.addStretch()
        self.btn_ab_vote_a = QPushButton("🅰️ Vote A")
        self.btn_ab_vote_a.setObjectName("ABVoteBtn")
        self.btn_ab_vote_a.setStyleSheet(
            "QPushButton#ABVoteBtn { font-size: 9pt; padding: 4px 12px; }"
        )
        ab_vote_layout.addWidget(self.btn_ab_vote_a)
        self.btn_ab_vote_b = QPushButton("🅱️ Vote B")
        self.btn_ab_vote_b.setObjectName("ABVoteBtn")
        self.btn_ab_vote_b.setStyleSheet(
            "QPushButton#ABVoteBtn { font-size: 9pt; padding: 4px 12px; }"
        )
        ab_vote_layout.addWidget(self.btn_ab_vote_b)
        self.btn_ab_vote_tie = QPushButton("🤝 Tie")
        self.btn_ab_vote_tie.setObjectName("ABVoteBtn")
        self.btn_ab_vote_tie.setStyleSheet(
            "QPushButton#ABVoteBtn { font-size: 9pt; padding: 4px 12px; }"
        )
        ab_vote_layout.addWidget(self.btn_ab_vote_tie)
        self.btn_ab_vote_skip = QPushButton("Skip")
        self.btn_ab_vote_skip.setObjectName("ABVoteBtn")
        self.btn_ab_vote_skip.setStyleSheet(
            "QPushButton#ABVoteBtn { font-size: 9pt; padding: 4px 12px; color: #6B7280; }"
        )
        ab_vote_layout.addWidget(self.btn_ab_vote_skip)
        self.ab_vote_bar.hide()

        # ── Connect A/B vote buttons to handlers ──
        self.btn_ab_vote_a.clicked.connect(lambda: self._vote_ab("A"))
        self.btn_ab_vote_b.clicked.connect(lambda: self._vote_ab("B"))
        self.btn_ab_vote_tie.clicked.connect(lambda: self._vote_ab("tie"))
        self.btn_ab_vote_skip.clicked.connect(self._hide_ab_vote_bar)

        # ── Combine: compare panel + AB panel + vote bar + input row ──

        input_row = QHBoxLayout()
        self.btn_mic = QPushButton("Mic")
        self.btn_mic.setToolTip("Push-to-talk voice input — hold to record, release to transcribe and send")
        self.btn_mic.pressed.connect(self.start_voice_record)
        self.btn_mic.released.connect(self.stop_voice_record)
        self.btn_mic.setFixedWidth(40)
        input_row.addWidget(self.btn_mic)

        self.voice_barge_in_badge = QLabel("")
        self.voice_barge_in_badge.setStyleSheet(
            "font-size: 8pt; color: #F59E0B; font-weight: bold; padding: 2px;"
        )
        self.voice_barge_in_badge.hide()
        input_row.addWidget(self.voice_barge_in_badge)

        self.btn_mini_hud = QPushButton("⚡ HUD")
        self.btn_mini_hud.setObjectName("MiniHudInputBtn")
        self.btn_mini_hud.setToolTip(
            "Toggle floating mini-Kokertechai command palette (Ctrl+Space / Ctrl+Alt+Space)"
        )
        self.btn_mini_hud.clicked.connect(self._toggle_mini_hud)
        self.btn_mini_hud.setFixedWidth(55)
        self.btn_hud = self.btn_mini_hud
        input_row.addWidget(self.btn_mini_hud)
        self.txt_input = QTextEdit()
        self.txt_input.setPlaceholderText("Enter command... (Ctrl+Enter to send)")
        self.txt_input.setMaximumHeight(65)
        input_row.addWidget(self.txt_input)
        self.shortcut_send = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.shortcut_send.activated.connect(self.action_send_prompt)
        # Global search shortcut (Ctrl+K)
        self.shortcut_global_search = QShortcut(QKeySequence("Ctrl+K"), self)
        self.shortcut_global_search.activated.connect(self._open_global_search)
        # mini-Kokertechai shortcut (Ctrl+Space)
        self.shortcut_mini_hud = QShortcut(QKeySequence("Ctrl+Space"), self)
        self.shortcut_mini_hud.activated.connect(self._toggle_mini_hud)
        self.protocol_badge = QLabel()
        self.protocol_badge.setFixedWidth(22)
        input_row.addWidget(self.protocol_badge)
        self.btn_send = QPushButton("RUN")
        self.btn_send.setToolTip("Send command to AI — Ctrl+Enter shortcut also works from the input field")
        self.btn_send.clicked.connect(self.action_send_prompt)
        self.btn_send.setFixedWidth(50)
        input_row.addWidget(self.btn_send)

        # ── Stop button (hidden until a worker is running) ──
        self.btn_stop = QPushButton("\u23F9")
        self.btn_stop.setToolTip("Stop generation (discards in-flight response)")
        self.btn_stop.setFixedWidth(40)
        self.btn_stop.setStyleSheet(
            "QPushButton { background-color: #7f1d1d; border-color: #991b1b; "
            "font-size: 11pt; font-weight: bold; }"
            "QPushButton:hover { background-color: #991b1b; }"
        )
        self.btn_stop.clicked.connect(self._action_stop_generation)
        self.btn_stop.hide()
        input_row.addWidget(self.btn_stop)

        # ── Inline Code Sandbox: Run button (hidden until code block detected) ──
        self.btn_run_code = QPushButton()
        self.btn_run_code.setVisible(False)
        self.btn_run_code.setStyleSheet(
            "font-size: 9pt; padding: 4px 8px; background-color: #1e3a5f; border-color: #2563eb;"
        )
        self.btn_run_code.clicked.connect(self._action_run_code_block)
        input_row.addWidget(self.btn_run_code)
        self._update_protocol_badge()

        # Combine compare panel + AB panel + typing indicator + input row
        # Typing indicator appears just above the input row when a worker is
        # processing. Uses an animated "● ● ●" label driven by a QTimer.
        self.typing_indicator_lbl = QLabel("")
        self.typing_indicator_lbl.setStyleSheet(
            "font-size: 9pt; color: #6B7280; padding: 2px 6px; font-style: italic;"
        )
        self.typing_indicator_lbl.hide()
        chat_area_layout.addWidget(self.compare_panel)
        chat_area_layout.addWidget(self.ab_panel)
        chat_area_layout.addWidget(self.typing_indicator_lbl)
        # ── Progressive Streaming UI: Progress Panel ──
        self._create_progress_panel()
        chat_area_layout.addWidget(self._progress_panel)
        chat_area_layout.addLayout(input_row)
        self.main_chat_splitter.addWidget(chat_area_widget)

        self.suggestions_panel = QWidget()
        self.suggestions_panel.setMinimumWidth(180)
        sugg_layout = QVBoxLayout(self.suggestions_panel)
        sugg_layout.addWidget(QLabel("Desire Engine"))
        self.desire_model_lbl = QLabel("")
        self.desire_model_lbl.setObjectName("DesireModelLabel")
        self.desire_model_lbl.setStyleSheet("font-size: 8pt; color: #9CA3AF; padding: 1px 2px;")
        self.desire_model_lbl.setToolTip("Model used by Desire Engine for proactive suggestions — click to open Settings")
        self.desire_model_lbl.mousePressEvent = lambda e: self._switch_to_tab_by_name("Settings")
        sugg_layout.addWidget(self.desire_model_lbl)
        self.chk_desire_engine = QCheckBox("Enable")
        self.chk_desire_engine.setToolTip("Desire Engine — generates proactive task suggestions and curiosity-driven queries")
        self.chk_desire_engine.setChecked(True)
        sugg_layout.addWidget(self.chk_desire_engine)
        self.suggestions_scroll = QScrollArea()
        self.suggestions_scroll.setWidgetResizable(True)
        self.suggestions_content = QWidget()
        self.suggestions_content_layout = QVBoxLayout(self.suggestions_content)
        self.suggestions_content_layout.addStretch()
        self.suggestions_scroll.setWidget(self.suggestions_content)
        sugg_layout.addWidget(self.suggestions_scroll)
        self.main_chat_splitter.addWidget(self.suggestions_panel)
        self.main_chat_splitter.setSizes([750, 250])
        main_layout.addWidget(center_widget, stretch=3)

        v_sep2 = QFrame(); v_sep2.setFrameShape(QFrame.Shape.VLine); v_sep2.setObjectName("Separator")
        main_layout.addWidget(v_sep2)

        audit_panel = QWidget()
        audit_panel.setFixedWidth(280)
        audit_layout = QVBoxLayout(audit_panel)
        audit_layout.addWidget(QLabel("Executive Audit Logs"))
        self.auditor_model_lbl = QLabel("")
        self.auditor_model_lbl.setObjectName("AuditorModelLabel")
        self.auditor_model_lbl.setStyleSheet("font-size: 8pt; color: #9CA3AF; padding: 1px 2px;")
        self.auditor_model_lbl.setToolTip("Model used by Cognitive Auditor for interaction auditing — click to open Settings")
        self.auditor_model_lbl.mousePressEvent = lambda e: self._switch_to_tab_by_name("Settings")
        audit_layout.addWidget(self.auditor_model_lbl)
        self.audit_log_display = QTextEdit()
        self.audit_log_display.setReadOnly(True)
        self.audit_log_display.setObjectName("AuditLog")
        audit_layout.addWidget(self.audit_log_display)
        main_layout.addWidget(audit_panel)

        self.vram_label = QLabel("VRAM: Checking...")
        self.vram_label.setObjectName("VramLabel")
        self.statusBar().addPermanentWidget(self.vram_label)

        self.tabs.addTab(core_tab, "Core")
        self.tabs.addTab(self.create_workspace_tab(), "Workspace")
        self.knowledge_graph_tab_widget = self.create_knowledge_graph_tab()
        self.tabs.addTab(self.knowledge_graph_tab_widget, "Neural Graph")
        self.settings_tab_widget = self.create_settings_tab()
        self.tabs.addTab(self.settings_tab_widget, "Settings")

        # Plugins moved to its own top-level tab (removed from sidebar)
        plugins_tab = QWidget()
        plugins_layout = QVBoxLayout(plugins_tab)
        plugins_layout.setContentsMargins(4, 4, 4, 4)
        pr = plugin_registry.registry
        # [Startup] Plugin registry initialised: registry is a singleton whose first access runs importlib over the plugins/ directory; this firing point is right at the same access init_ui() does to populate the Plugins tab, so the count here is authoritative.
        self.plugin_status_label = QLabel(
            f"{pr.get_enabled_count()}/{pr.get_plugin_count()} enabled"
        )
        self.plugin_status_label.setStyleSheet(
            "font-size: 9pt; font-weight: bold; padding: 2px 0;"
        )
        plugins_layout.addWidget(self.plugin_status_label)

        # Execution error summary (hidden when no errors)
        self.plugin_error_summary = QLabel()
        self.plugin_error_summary.setWordWrap(True)
        self.plugin_error_summary.hide()
        plugins_layout.addWidget(self.plugin_error_summary)

        self.plugin_list = QListWidget()
        self.plugin_list.itemChanged.connect(self._on_plugin_toggle)
        self.plugin_list.currentItemChanged.connect(self._on_plugin_selected)
        plugins_layout.addWidget(self.plugin_list)

        self.plugin_detail = QLabel("Select a plugin to view details")
        self.plugin_detail.setWordWrap(True)
        self.plugin_detail.setStyleSheet(
            "font-size: 8pt; color: #9CA3AF; padding: 4px;"
        )
        plugins_layout.addWidget(self.plugin_detail)

        refresh_row = QHBoxLayout()
        btn_refresh_plugins = QPushButton("Reload")
        btn_refresh_plugins.clicked.connect(self._refresh_plugin_list)
        refresh_row.addWidget(btn_refresh_plugins)

        btn_disable_all = QPushButton("Disable All")
        btn_disable_all.clicked.connect(self._disable_all_plugins)
        refresh_row.addWidget(btn_disable_all)

        btn_enable_all = QPushButton("Enable All")
        btn_enable_all.clicked.connect(self._enable_all_plugins)
        refresh_row.addWidget(btn_enable_all)

        btn_clear_errors = QPushButton("Clear Errors")
        btn_clear_errors.setToolTip("Reset execution error counters for all plugins")
        btn_clear_errors.clicked.connect(self._clear_plugin_errors)
        refresh_row.addWidget(btn_clear_errors)

        plugins_layout.addLayout(refresh_row)
        self._plugins_tab_index = self.tabs.count()
        self._add_scrollable_tab(plugins_tab, "Plugins")
        self._add_scrollable_tab(self.create_agency_tab(), "Agency")
        self._add_scrollable_tab(self.create_alignment_tab(), "RLHF")
        self._add_scrollable_tab(self.create_document_pipeline_tab(), "Doc Pipeline")
        self._add_scrollable_tab(self.create_workflow_tab(), "Workflows")
        self._add_scrollable_tab(self.create_computer_use_tab(), "Computer")
        self._add_scrollable_tab(self.create_health_tab(), "Health")
        self._add_scrollable_tab(self.create_git_tracker_tab(), "Git")
        self._add_scrollable_tab(self.create_chat_history_tab(), "Chat History")
        self._add_scrollable_tab(self.create_memory_browser_tab(), "Memory")
        self._add_scrollable_tab(self.create_rag_tab(), "RAG")
        self._add_scrollable_tab(self.create_scheduled_actions_tab(), "Scheduler")
        self._add_scrollable_tab(self.create_plugin_store_tab(), "Plugin Store")
        self._add_scrollable_tab(self.create_theme_builder_tab(), "Theme Builder")
        self._add_scrollable_tab(self.create_session_browser_tab(), "Sessions")
        self._add_scrollable_tab(self.create_web_view_tab(), "Web View")
        self._add_scrollable_tab(self.create_session_log_tab(), "Session Log")
        self._add_scrollable_tab(self.create_persona_ab_testing_tab(), "A/B Test")
        self.tabs.currentChanged.connect(lambda _idx: self.refresh_model_status_ui())
        self.refresh_model_status_ui()
        self.log_to_audit("UI Initialized.")

    def _open_global_search(self):
        """Open the Ctrl+K global search overlay."""
        from tabs.global_search import GlobalSearchDialog
        if hasattr(self, '_global_search_dialog') and self._global_search_dialog.isVisible():
            self._global_search_dialog.raise_()
            self._global_search_dialog.search_input.setFocus()
            self._global_search_dialog.search_input.selectAll()
            return
        self._global_search_dialog = GlobalSearchDialog(
            parent=self,
            controller=getattr(self, 'controller', None),
        )
        self._global_search_dialog.restore_requested.connect(
            self._on_global_search_restore
        )
        self._global_search_dialog.show()

    def _on_global_search_restore(self, content):
        """Restore content from global search results to chat input."""
        if hasattr(self, 'txt_input') and content:
            self.txt_input.setPlainText(content[:2000])
        self.log_to_audit("📋 Global search: restored content to chat input")
        # Close the dialog after restoring
        if hasattr(self, '_global_search_dialog'):
            self._global_search_dialog.close()

    def _toggle_mini_hud(self, *args, **kwargs):
        """Toggle the floating mini-Kokertechai palette."""
        if hasattr(self, "_do_toggle_hud"):
            self._do_toggle_hud(*args, **kwargs)
        else:
            from tabs.mini_hud import MiniHudDialog
            if not hasattr(self, "mini_hud") or self.mini_hud is None:
                self.mini_hud = MiniHudDialog(
                    parent=self if hasattr(self, "show") else None,
                    controller=getattr(self, "controller", None),
                )
                if hasattr(self, "_on_hud_send_to_main"):
                    self.mini_hud.send_to_main_requested.connect(self._on_hud_send_to_main)
            self.mini_hud.toggle_visibility()


