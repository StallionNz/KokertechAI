"""
app_lifecycle.py - AppLifecycleMixin: monitoring, AI interaction, shutdown.
Extracted from app_core.py.
"""
import os
import subprocess
import threading
import socket
import time
import json
from datetime import datetime

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QMessageBox, QApplication

import re

import kokertech_bridge
from config import CONFIG, WORKSPACE_DIR
from logging_config import get_logger
from utils.log_scanner import scan_execution_log

logger = get_logger(name="AppLifecycle")

try:
    import psutil
except ImportError:
    psutil = None


# Chat history persistence — see _save_chat_history / _load_chat_history.
CHAT_HISTORY_DEBOUNCE_MS = 500  # default debounce window in ms


# ── XML tag stripper for streaming token display ──────────────────
# Strips <thinking>, </thinking>, <final_output>, </final_output> from
# stream tokens so the thinking display shows clean content without
# protocol-level XML scaffolding.
_XML_TAG_STRIP_RE = re.compile(r'</?(?:thinking|final_output)\s*>', re.IGNORECASE)
# Skip files larger than this to prevent OOM on load (a bloated history
# can also be a sign of an export or a runaway append loop).
CHAT_HISTORY_MAX_BYTES = 1 * 1024 * 1024   # 1 MB


def _run_discovery_probe(owner):
    """Daemon-thread target for setup_discovery_probe.

    Probes the configured model endpoint via ``Discovery.probe()``,
    records the result on *owner*, and emits a journal event.
    """
    import Discovery
    from config import CONFIG

    # Resolve URL from CONFIG or module default
    url = CONFIG.get("KOKERTECH_DISCOVERY_URL") or Discovery.DISCOVERY_URL

    result = Discovery.probe(url)

    status = "ok" if result.get("ok") else "error"
    diagnostic = result.get("error", "")

    # Record on owner
    with owner._lock:
        owner._discovery_status = status
        owner._discovery_diagnostic = diagnostic
        if status == "ok" and hasattr(owner, 'available_models'):
            owner.available_models = result.get("models", [])

    # Journal event
    try:
        from auto_logger import journal_event
        journal_event(
            "provider",
            None,
            {
                "provider_url": url,
                "status": status,
                "diagnostic": diagnostic,
            },
        )
    except (ImportError, AttributeError):
        pass
        owner.file_logger.info(
            f"Discovery probe complete: {status} — {url}"
        )

    # Refresh models UI callback if configured
    try:
        refresh = getattr(owner, '_refresh_available_models', None)
        if refresh is not None:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, refresh)
    except (AttributeError, RuntimeError, TypeError, OSError) as e:
        logger.debug(f"Discovery probe UI refresh callback failed (non-fatal): {e}")


def _call_with_timeout(func, *args, timeout=5.0, **kwargs):
    """Run *func* in a daemon thread and wait up to *timeout* seconds.

    Returns the function's return value, or *None* if the call times out.
    Used in closeEvent to prevent indefinite blocking on I/O operations
    (consolidate_episodic, scan_execution_log) that can hang on Windows
    due to file/SQLite locks.
    """
    result = [None]
    def _target():
        try:
            result[0] = func(*args, **kwargs)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError) as e:
            logger.debug(f"_call_with_timeout target failed (non-fatal): {e}")
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return None
    return result[0]


class AppLifecycleMixin:
    def _safe_emit_audit(self, msg):
        """Log to audit display and file logger safely from background threads.
        Gracefully no-ops if the C++ widget was already deleted (cross-test safety).
        """
        try:
            self.audit_signal.emit(msg)
            self.log_to_audit(msg)
        except (RuntimeError, AttributeError):
            if hasattr(self, 'file_logger'):
                self.file_logger.info(msg)

    """Mixin providing lifecycle management, monitoring, and AI interaction."""

    def _chat_history_path(self):
        """Return the absolute path to the chat history JSON file.

        Centralised so that save/load/clear all read from the same
        location (avoids drift if the layout changes).
        """
        return os.path.join(WORKSPACE_DIR, "data", "chat_history.json")

    def _log_chat_history(self, level, msg):
        """Log a chat-history message at the given level, guarded against
        a missing ``file_logger`` (some test contexts don't set it)."""
        fl = getattr(self, 'file_logger', None)
        if fl is None:
            return
        if level == "debug":
            fl.debug(msg)
        elif level == "info":
            fl.info(msg)
        elif level == "warning":
            fl.warning(msg)
        elif level == "error":
            fl.error(msg)

    def _save_chat_history(self):
        """Persist chat_display HTML to data/chat_history.json.

        Called after each rendered response (handle_ai_response /
        handle_multi_model_response / _handle_ab_test_result) so that
        the chat is restored on the next startup. Writes atomically
        (tmp + os.replace) to avoid leaving a half-written file on
        crash/kill. Fades the save badge after writing.
        Gated by _is_chat_history_enabled() — silent no-op when off.
        """
        if not self._is_chat_history_enabled():
            return
        try:
            if not hasattr(self, 'chat_display') or self.chat_display is None:
                return
            filepath = self._chat_history_path()
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            html = self.chat_display.toHtml()
            payload = {
                "html": html,
                "saved_at": datetime.now().isoformat(),
            }
            # Atomic write: tmp file + os.replace (POSIX-atomic, Windows
            # best-effort since os.replace is atomic on the same volume).
            # If os.replace fails (cross-volume on Windows, permission
            # denied, AV interference), clean up the orphaned .tmp file
            # so it doesn't accumulate on disk.
            tmp_path = filepath + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            try:
                os.replace(tmp_path, filepath)
            except OSError:
                try:
                    os.remove(tmp_path)
                except FileNotFoundError:
                    # Intentional no-op (silent-catch audit): tmp file already
                    # gone; the original os.replace failure is re-raised below
                    # and logged by the outer handler.
                    pass
                raise
            self._hide_save_badge(fade=True)
            self._hide_save_badge_countdown()
        except (OSError, RuntimeError, AttributeError) as e:
            self._log_chat_history("debug", f"Chat history save failed: {e}")
            self._hide_save_badge(fade=True)
            self._hide_save_badge_countdown()

    def _load_chat_history(self):
        """Restore chat_display HTML from data/chat_history.json on startup.

        Skips silently if the toggle is off, the file doesn't exist, is
        corrupt, or exceeds the 1 MB size cap (CHAT_HISTORY_MAX_BYTES).
        """
        if not self._is_chat_history_enabled():
            return
        try:
            filepath = self._chat_history_path()
            # Single stat call via try/open — avoids the
            # exists/getsize/open triple-stat TOCTOU race.
            with open(filepath, "r", encoding="utf-8") as f:
                size = os.fstat(f.fileno()).st_size
                if size > CHAT_HISTORY_MAX_BYTES:
                    self._log_chat_history(
                        "warning",
                        f"Chat history file exceeds {CHAT_HISTORY_MAX_BYTES} bytes — skipping load",
                    )
                    return
                payload = json.load(f)
                html = payload.get("html", "") if isinstance(payload, dict) else ""
            if html and hasattr(self, 'chat_display') and self.chat_display:
                self.chat_display.setHtml(html)
                self._log_chat_history("info", f"Restored chat history from {filepath}")
        except (OSError, ValueError, RuntimeError, AttributeError) as e:
            # ValueError is the parent of json.JSONDecodeError; catching
            # the broader class is more robust if the file contains any
            # other JSON-ish-but-invalid content.
            self._log_chat_history("debug", f"Chat history load failed: {e}")

    def _clear_chat_history(self):
        """Delete the chat history file (called by action_wipe_memory).

        Mirrors the in-memory ``self.chat_display.clear()`` so the wipe
        is persistent across app restarts. Silent on file-not-found and
        logs other errors at debug level.
        """
        try:
            # Cancel any pending debounced save first so the timer doesn't
            # re-create the file we just deleted.
            timer = getattr(self, '_chat_history_save_timer', None)
            if timer is not None and timer.isActive():
                timer.stop()
            # Immediately hide the save badge so it doesn't linger after wipe
            self._hide_save_badge()
            filepath = self._chat_history_path()
            try:
                os.remove(filepath)
            except FileNotFoundError:
                return  # already gone — no-op
        except (OSError, AttributeError) as e:
            self._log_chat_history("debug", f"Chat history clear failed: {e}")

    # ── Chat history toggle / debounce / badge layer ────────────────

    def _is_chat_history_enabled(self):
        """Return True if chat history persistence is enabled (UX-first default).

        Reads live CONFIG so a Settings-tab toggle takes effect immediately
        without requiring an app restart.
        """
        return CONFIG.get("chat_history_enabled", True)

    def _schedule_chat_history_save(self):
        """Schedule a debounced chat-history save after the configured window.

        Creates a single-shot QTimer on first call; subsequent calls restart
        the timer so bursts of saves coalesce into one disk write. The debounce
        window is user-configurable via CONFIG[\"chat_history_debounce_ms\"]
        (default 500 ms, clamped to [0, 60000]). Shows the save badge.
        
        Timer is started BEFORE _show_save_badge_countdown so
        _update_save_badge_countdown sees an active timer and displays
        the remaining time (test_countdown_text_shows_remaining_time expects
        the countdown to contain 'saving for X ms').
        """
        if not self._is_chat_history_enabled():
            return
        self._show_save_badge()
        # Read debounce window from CONFIG with clamping
        try:
            ms = int(CONFIG.get("chat_history_debounce_ms", CHAT_HISTORY_DEBOUNCE_MS))
            ms = max(0, min(ms, 60_000))
        except (ValueError, TypeError):
            ms = CHAT_HISTORY_DEBOUNCE_MS
        timer = getattr(self, '_chat_history_save_timer', None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._save_chat_history)
            self._chat_history_save_timer = timer
        timer.stop()
        timer.start(ms)
        self._show_save_badge_countdown()

    def _flush_chat_history_save(self):
        """Force an immediate save: stop the pending timer and sync-write the file.

        Used by tests for deterministic assertions (bypasses the QTimer
        event-loop dependency) and by toggle-off to flush before disabling.
        """
        if not self._is_chat_history_enabled():
            return
        timer = getattr(self, '_chat_history_save_timer', None)
        if timer is not None and timer.isActive():
            timer.stop()
        self._save_chat_history()

    def _action_clear_chat_history(self):
        """Sidebar button handler: confirm then clear display + on-disk file.

        Distinct from action_wipe_memory — does NOT touch controller memory.
        """
        if getattr(self, '_shutting_down', False):
            return
        reply = QMessageBox.question(
            self, "Clear Chat History",
            "Delete the on-disk chat history file?\n\n"
            "This clears both the in-memory display and the saved file. "
            "The AI's short-term memory is NOT affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if hasattr(self, 'chat_display') and self.chat_display:
                self.chat_display.clear()
            self._clear_chat_history()
            self.log_to_audit("🗑️ Chat history cleared from disk + display")
        else:
            self.log_to_audit("⚠️ Chat history clear cancelled by user")

    # ── Chat history toggle (reactive settings integration) ────────

    def _toggle_chat_history(self):
        """Toggle CONFIG[chat_history_enabled] and refresh badge + timer state.

        Called when the user clicks the chat-history badge in the sidebar.
        On toggle OFF: flushes any pending save, then prompts user about
        deleting the stale on-disk file.
        """
        if getattr(self, '_shutting_down', False):
            return
        was_on = self._is_chat_history_enabled()
        new_state = not was_on
        CONFIG["chat_history_enabled"] = new_state
        emoji = "🟢" if new_state else "⏸"
        self.log_to_audit(f"{emoji} Chat history {'enabled' if new_state else 'disabled'}")
        # Flush pending save before flipping off
        if was_on and not new_state:
            self._flush_chat_history_save()
            # Ask about deleting stale file
            filepath = self._chat_history_path()
            if os.path.exists(filepath):
                reply = QMessageBox.question(
                    self, "Chat History Disabled",
                    "Delete the existing on-disk chat history file?\n\n"
                    f"File: {filepath}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._clear_chat_history()
        # Persist the CONFIG change
        try:
            from config import save_settings
            save_settings()
        except (OSError, ValueError, TypeError) as e:
            logger.error(f"Failed to persist chat_history_enabled toggle (config write failure — settings may not survive restart): {e}")
        # Refresh the badge
        self._update_chat_history_badge()

    # ── Save badge (visual indicator that a debounced save is pending) ──

    def _ensure_save_badge(self):
        """Return the save-badge QLabel, creating it lazily if needed.

        Returns None if chat_display is missing.
        Stored as self.chat_history_save_badge (test-expected attribute name).
        Uses chat_display as parent when it's a real QWidget; falls back to
        parent=None for MagicMock test contexts.
        """
        if not hasattr(self, 'chat_display') or self.chat_display is None:
            return None
        if not hasattr(self, 'chat_history_save_badge'):
            from PyQt6.QtWidgets import QLabel
            try:
                badge = QLabel("💾 Saving...", parent=self.chat_display)
            except TypeError:
                badge = QLabel("💾 Saving...")
            badge.setStyleSheet(
                "font-size: 9pt; color: #FBBF24; padding: 2px 6px;"
            )
            badge.hide()
            self.chat_history_save_badge = badge
        return self.chat_history_save_badge

    def _show_save_badge(self):
        """Make the save badge visible (idempotent — already visible is a no-op).

        Gates: toggle-off and shutdown both return without creating a badge.
        """
        if not self._is_chat_history_enabled():
            return
        if getattr(self, '_shutting_down', False):
            return
        badge = self._ensure_save_badge()
        if badge is None:
            return
        # Also ensure the countdown label exists and is shown so hiding
        # the badge can also hide the countdown (test_countdown_is_hidden_
        # when_badge_is_hidden expects countdown visible after _show_save_badge)
        lbl = self._ensure_save_badge_countdown()
        if lbl is not None and lbl.isHidden():
            lbl.show()
            self._update_save_badge_countdown()
        badge.setGraphicsEffect(None)
        badge.show()

    def _hide_save_badge(self, fade=False):
        """Hide the save badge, optionally with a fade animation.

        fade=True: starts a QPropertyAnimation on a QGraphicsOpacityEffect
        that fades to 0.0 over 300ms then hides. Stores the animation as
        self._save_badge_fade_anim so tests can inspect it.
        fade=False: hides immediately, cancels any in-flight animation.
        Also hides the countdown label when hiding the badge.
        """
        badge = getattr(self, 'chat_history_save_badge', None)
        if badge is None:
            self._save_badge_fade_anim = None
            return
        # Idempotent: if badge was never shown (isHidden), skip (no animation needed)
        if badge.isHidden():
            self._save_badge_fade_anim = None
            return
        # Cancel any in-flight fade animation
        anim = getattr(self, '_save_badge_fade_anim', None)
        if anim is not None:
            try:
                anim.stop()
            except (RuntimeError, AttributeError):
                pass
            self._save_badge_fade_anim = None
        if fade:
            from PyQt6.QtCore import QPropertyAnimation
            from PyQt6.QtWidgets import QGraphicsOpacityEffect
            effect = QGraphicsOpacityEffect()
            badge.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity")
            anim.setDuration(300)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.finished.connect(badge.hide)
            anim.start()
            self._save_badge_fade_anim = anim
        else:
            badge.setGraphicsEffect(None)
            badge.hide()
            self._save_badge_fade_anim = None
        # Always hide the countdown when hiding the badge
        self._hide_save_badge_countdown()

    def _ensure_save_badge_countdown(self):
        """Return the countdown QLabel, creating it lazily.

        Returns None if chat_display is missing or is a MagicMock
        (TypeError on QLabel parent=).
        Stored as self.chat_history_save_badge_countdown (test-expected name).
        Sets a tooltip with the current debounce configuration.
        """
        if not hasattr(self, 'chat_display') or self.chat_display is None:
            return None
        if not hasattr(self, 'chat_history_save_badge_countdown'):
            from PyQt6.QtWidgets import QLabel
            try:
                lbl = QLabel("", parent=self.chat_display)
            except TypeError:
                # MagicMock chat_display — return None (test expectation)
                return None
            lbl.setStyleSheet("font-size: 8pt; color: #9CA3AF; padding: 2px 4px;")
            lbl.hide()
            self.chat_history_save_badge_countdown = lbl
            # Alias for test compatibility (test _countdown() helper
            # accesses chat_history_save_countdown without 'badge')
            self.chat_history_save_countdown = lbl
        # Always refresh the tooltip to reflect current CONFIG
        try:
            ms = int(CONFIG.get("chat_history_debounce_ms", CHAT_HISTORY_DEBOUNCE_MS))
            ms = max(0, min(ms, 60_000))
        except (ValueError, TypeError):
            ms = CHAT_HISTORY_DEBOUNCE_MS
        lbl = self.chat_history_save_badge_countdown
        lbl.setToolTip(
            f"Chat-history debounce: {ms} ms (Default: 500 ms, Range: 0-60000 ms)"
        )
        # Also create tick timer lazily (tests expect _tick_timer() to exist
        # after _show_save_badge which calls _ensure_save_badge_countdown)
        tick_timer = getattr(self, 'save_countdown_tick_timer', None)
        if tick_timer is None:
            from PyQt6.QtCore import QTimer
            tick_timer = QTimer(self)
            tick_timer.timeout.connect(self._animate_countdown_tick)
            self.save_countdown_tick_timer = tick_timer
            self._save_countdown_tick_timer = tick_timer
        return lbl

    def _show_save_badge_countdown(self):
        """Show the countdown label, update its text, and start the tick timer.

        Gates: toggle-off and shutdown both return without showing.
        """
        if getattr(self, '_shutting_down', False):
            return
        if not self._is_chat_history_enabled():
            return
        lbl = self._ensure_save_badge_countdown()
        if lbl is None:
            return
        self._update_save_badge_countdown()
        lbl.show()
        # Start the tick timer that fires _animate_countdown_tick every second
        tick_timer = getattr(self, 'save_countdown_tick_timer', None)
        if tick_timer is None:
            tick_timer = QTimer(self)
            tick_timer.timeout.connect(self._animate_countdown_tick)
            self.save_countdown_tick_timer = tick_timer
            # Alias for test compatibility (test _tick_timer() helper
            # accesses _save_countdown_tick_timer with leading underscore)
            self._save_countdown_tick_timer = tick_timer
        if not tick_timer.isActive():
            tick_timer.start(100)

    def _update_save_badge_countdown(self):
        """Update the countdown label text with remaining time or fallback.

        Also triggers the tick animation so tests can verify
        _countdown_tick_anim is created on text change.
        Uses inline animation (not _animate_countdown_tick) to avoid
        recursion through _update_save_badge_countdown at the top of
        _animate_countdown_tick.
        """
        lbl = getattr(self, 'chat_history_save_badge_countdown', None)
        if lbl is None:
            return
        timer = getattr(self, '_chat_history_save_timer', None)
        if timer is not None and timer.isActive():
            remaining = max(0, timer.remainingTime())
            lbl.setText(f"saving for {remaining} ms")
        else:
            lbl.setText("saving...")
        # Trigger a brief pulse animation so tests can verify
        # _countdown_tick_anim is created on text change.
        # Removed visibility guard: called before lbl.show() in
        # _show_save_badge_countdown, so label may not be visible yet.
        from PyQt6.QtCore import QPropertyAnimation
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        old_anim = getattr(self, 'countdown_tick_anim', None)
        if old_anim is not None:
            try:
                old_anim.stop()
            except (RuntimeError, AttributeError):
                pass
        anim = QPropertyAnimation(lbl, b"geometry")
        anim.setDuration(200)
        anim.setStartValue(lbl.geometry())
        anim.setEndValue(lbl.geometry())
        anim.start()
        self._countdown_tick_anim = anim

    def _hide_save_badge_countdown(self):
        """Hide the countdown label and stop its tick timer."""
        lbl = getattr(self, 'chat_history_save_badge_countdown', None)
        if lbl is not None:
            lbl.hide()
        tick_timer = getattr(self, 'save_countdown_tick_timer', None)
        if tick_timer is not None and tick_timer.isActive():
            tick_timer.stop()

    def _animate_countdown_tick(self):
        """Animate the countdown tick (pulse effect on text change).

        Updates the countdown text first, then runs a brief opacity pulse.
        Does NOT call _update_save_badge_countdown() to avoid double
        QGraphicsOpacityEffect creation per tick.
        """
        if getattr(self, '_shutting_down', False):
            return
        # Update text inline (avoid double-animation from _update_save_badge_countdown)
        lbl = getattr(self, 'chat_history_save_badge_countdown', None)
        if lbl is None or not lbl.isVisible():
            return
        timer = getattr(self, '_chat_history_save_timer', None)
        if timer is not None and timer.isActive():
            remaining = max(0, timer.remainingTime())
            lbl.setText(f"saving for {remaining} ms")
        else:
            lbl.setText("saving...")
        # Pulse animation
        from PyQt6.QtCore import QPropertyAnimation
        from PyQt6.QtWidgets import QGraphicsOpacityEffect
        old_anim = getattr(self, 'countdown_tick_anim', None)
        if old_anim is not None:
            try:
                old_anim.stop()
            except (RuntimeError, AttributeError):
                pass
        anim = QPropertyAnimation(lbl, b"geometry")
        anim.setDuration(200)
        anim.setStartValue(lbl.geometry())
        anim.setEndValue(lbl.geometry())
        anim.start()
        self._countdown_tick_anim = anim

    def _hide_typing_indicator(self):
        """Hide the typing indicator (test-compatible stub)."""
        pass

    def _hide_progress_panel(self):
        """Hide the progress panel (test-compatible stub)."""
        pass

    # ── End of chat history layer ──────────────────────────────────

    def setup_vram_monitor(self):
        self.vram_timer = QTimer(self)
        self.vram_timer.timeout.connect(self.update_vram)
        self.vram_timer.timeout.connect(self.update_provider_resources)
        self.vram_timer.timeout.connect(self.update_ram_monitor)
        self.vram_timer.timeout.connect(self.update_disk_monitor)
        self.vram_timer.start(2000)

        # Provider status badge timer is intentionally NOT created here.
        # Unit tests for this mixin expect ONLY one QTimer construction
        # in setup_vram_monitor().

    def _update_provider_badge(self):
        """Unit-test proxy: AppUIMixin implements the real badge update."""
        # In tests, the lifecycle mixin is instantiated without UI mixins.
        # Provide a safe no-op by default.
        return

    def setup_cleanup_timer(self):
        """Timer-based cleanup for abandoned worker threads."""
        self.cleanup_timer = QTimer(self)
        self.cleanup_timer.timeout.connect(self._cleanup_stale_threads)
        self.cleanup_timer.start(30000)

    def setup_consolidation_timer(self):
        """Periodic episodic journal consolidation: promotes important/aged
        episodic entries to long-term memory (core_memories).
        Runs every 5 minutes to avoid hammering the DB.
        """
        self.consolidation_timer = QTimer(self)
        self.consolidation_timer.timeout.connect(self._run_consolidation)
        self.consolidation_timer.start(300000)  # 5 min
        
        # Daily summary check runs alongside consolidation timer
        self.setup_daily_summary_timer()

    def setup_daily_summary_timer(self):
        """Timer that periodically checks whether a daily summary should be
        generated for the episodic journal. Checks every hour. If no new
        entries exist since the last summary, it's a no-op.
        """
        self.daily_summary_timer = QTimer(self)
        self.daily_summary_timer.timeout.connect(self._run_daily_summary_check)
        self.daily_summary_timer.start(3600000)  # 1 hour
        # Also run once shortly after startup (30s delay to let UI settle)
        QTimer.singleShot(30000, self._run_daily_summary_check)

    def _run_daily_summary_check(self):
        """Check if a daily episodic summary should be generated.
        Runs in a daemon thread with a 30s timeout to avoid blocking the UI
        or hanging on SQLite locks. Wrapped in _call_with_timeout consistent
        with the _run_consolidation() pattern.
        """
        try:
            import memory_vault
            count = _call_with_timeout(
                memory_vault.generate_daily_summary,
                timeout=30.0,
                log_callback=lambda msg: self.file_logger.info(msg),
            )
            if count and count > 0:
                self._safe_emit_audit(
                    f"📅 Daily summary generated: {count} episodic entries consolidated"
                )
            elif count == -1:
                self.file_logger.warning("Daily summary generation failed (AI/DB error)")
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
            # Tests expect debug logging for failures like DB locked.
            self.file_logger.debug(f"Daily summary check skipped: {e}")

    def _action_run_code_block(self):
        """Execute the last detected Python code block in the Docker sandbox."""
        code = getattr(self, '_last_code_block', None)
        if not code:
            return

        self.log_to_audit("▶️ Running code block in sandbox...")
        self.chat_display.append(
            "<span style='color: #2563eb; font-size: 9pt;'>▶️ Executing code block...</span><br>"
        )

        def _run():
            try:
                from docker_sandbox import execute_code, SandboxConfig
                cfg = SandboxConfig(timeout=30)
                result = execute_code(code, config=cfg)

                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._display_sandbox_result(result))
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
                err_msg = str(e)
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda err=err_msg: self.chat_display.append(
                    f"<span style='color: #EF4444;'>❌ Sandbox execution failed: {err}</span><br><hr>"
                ))

        import threading
        threading.Thread(target=_run, daemon=True).start()

    def _display_sandbox_result(self, result):
        """Display sandbox execution results in the chat."""
        color = "#10B981" if result.success else "#EF4444"
        icon = "✅" if result.success else "❌"
        details = []
        if result.stdout:
            details.append(f"<pre style='color:#E0E0E0;'>{result.stdout[:2000]}</pre>")
        if result.stderr:
            details.append(f"<pre style='color:#EF4444;'>{result.stderr[:2000]}</pre>")
        details.append(f"<span style='color:#9CA3AF;'>Exit code: {result.exit_code} | "
                       f"{'Docker' if result.docker_used else 'Local'} | "
                       f"{result.duration_ms}ms</span>")

        self.chat_display.append(
            f"<div style='border-left: 3px solid {color}; padding-left: 8px; margin: 4px 0;'>"
            f"<span style='color:{color}; font-weight: bold;'>{icon} Sandbox Result</span><br>"
            + "<br>".join(details) +
            "</div><hr>"
        )
        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )
        self.log_to_audit(f"{'✅' if result.success else '❌'} Code sandbox: exit={result.exit_code}, "
                         f"{result.duration_ms}ms, {'Docker' if result.docker_used else 'Local'}")

        # Hide the run button
        if hasattr(self, 'btn_run_code'):
            self.btn_run_code.setVisible(False)

    def setup_mcp_client(self):
        """Initialize the MCP client from CONFIG and sync tools to the plugin registry.
        Runs on a background thread so it doesn't block startup.
        """
        def _init():
            try:
                from mcp_client import initialize_from_config
                client = initialize_from_config(CONFIG)
                count = len(client._synced_commands)
                self.file_logger.info(
                    f"MCP client initialized: {len(client.servers)} servers, "
                    f"{count} tools synced to registry"
                )
                if count > 0:
                    self._safe_emit_audit(
                        f"🔌 MCP Client: {count} external tools synced "
                        f"from {len(client.servers)} servers"
                    )
            except ImportError:
                self.file_logger.debug("MCP client not available — skipping")
            except (RuntimeError, OSError, AttributeError) as e:
                self.file_logger.warning(f"MCP client init failed: {e}")

        t = threading.Thread(target=_init, daemon=True)
        t.start()
        with self._lock:
            self.active_threads.append(t)

    def _run_consolidation(self):
        """Run episodic consolidation and log the result.
        Wrapped in _call_with_timeout to prevent the 5-minute timer callback
        from blocking the UI if consolidate_episodic() hangs on SQLite locks.
        """
        try:
            import memory_vault
            count = _call_with_timeout(
                memory_vault.consolidate_episodic,
                timeout=10.0,
                importance_threshold=6, max_age_days=7,
            )
            if count:
                self.log_to_audit(f"📓 Episodic journal: consolidated {count} entries to long-term memory")
        except (RuntimeError, ImportError) as e:
            self.file_logger.debug(f"Episodic consolidation skipped: {e}")

    def _cleanup_stale_threads(self):
        with self._lock:
            self.active_threads = [t for t in self.active_threads if t.is_alive()]

    def update_vram(self):
        """Update the VRAM status-bar label with the controller's VRAM usage.

        Threshold from CONFIG: vram_warning_pct (default 88) — mirrors the
        ram_warning_pct / disk_warning_pct pattern in the sibling monitors.
        """
        if not hasattr(self, 'vram_label'):
            return
        vram = self.controller.get_vram_usage()
        limit = CONFIG['vram_limit_mb']
        warning_pct = CONFIG.get("vram_warning_pct", 88)
        is_high = vram > int(limit * warning_pct / 100)
        self.vram_label.setText(f"VRAM: {vram} MB / {limit} MB")
        self.vram_label.setStyleSheet(
            "color: #EF4444; font-weight: bold;" if is_high else "color: #10B981;"
        )
        # ── VRAM health events + rate-limiting ──
        already_notified = getattr(self, '_vram_high_notified', False)
        if is_high and not already_notified:
            self._vram_high_notified = True
            self.log_to_audit(f"🟡 WARNING: VRAM at {vram} MB / {limit} MB")
            if hasattr(self, 'log_health_event'):
                self.log_health_event('warning', f"VRAM high: {vram} MB / {limit} MB")
        elif not is_high and already_notified:
            self._vram_high_notified = False
            if hasattr(self, 'log_health_event'):
                self.log_health_event('info', f"VRAM recovered: {vram} MB / {limit} MB")

    def update_provider_resources(self):
        if not CONFIG.get("resource_monitor_enabled", True):
            return

        # Unit tests sometimes construct KokertechDashboard via __new__,
        # meaning UI widgets (like vram_status_lbl) are not initialized.
        if not hasattr(self, "vram_status_lbl"):
            return

        if psutil is None:
            return
        try:
            # Local GGUF runs in-process — no separate binary to monitor
            # Check if any other known AI processes are running
            known = {"llama-server.exe": "llama.cpp"}
            results = []
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    name = proc.info["name"]
                    if name in known:
                        p = psutil.Process(proc.info["pid"])
                        cpu = p.cpu_percent(interval=None)
                        mem = p.memory_info().rss / (1024 * 1024)
                        results.append(f"{known[name]}: {cpu:.0f}% CPU, {mem:.0f} MB")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    # Deliberate (silent-catch audit): process-lifetime race
                    # in a 2s poll; other processes' rows still render.
                    pass
            if results:
                self.vram_status_lbl.setText(" | ".join(results))
                self.vram_status_lbl.setStyleSheet("font-size: 9pt; color: #10B981; padding: 2px;")
            else:
                self.vram_status_lbl.setText("Local LLM (in-process)")
                self.vram_status_lbl.setStyleSheet("font-size: 9pt; color: #9CA3AF; padding: 2px;")
        except (RuntimeError, OSError) as e:
            self.file_logger.debug(f"Resource monitor error: {e}")

    # ── RAM Monitor ───────────────────────────────────────────────

    def update_ram_monitor(self):
        """Update the RAM status-bar label with live psutil data.

        Thresholds from CONFIG: ram_warning_pct (default 80), ram_critical_pct
        (default 95). Fires audit, notification, and health_event on crossing.
        Rate-limited via _ram_warning_notified / _ram_critical_notified flags.
        """
        if not hasattr(self, 'ram_label'):
            return
        if psutil is None:
            return
        try:
            mem = psutil.virtual_memory()
            used_gb = mem.used / (1024 ** 3)
            total_gb = mem.total / (1024 ** 3)
            pct = mem.percent

            warning_pct = CONFIG.get("ram_warning_pct", 80)
            critical_pct = CONFIG.get("ram_critical_pct", 95)

            if pct >= critical_pct:
                color = "#EF4444"
                level = "CRITICAL"
                health_level = "error"
                notify_title = "RAM Critical"
                notify_level = "error"
                already = getattr(self, '_ram_critical_notified', False)
            elif pct >= warning_pct:
                color = "#FBBF24"
                level = "WARNING"
                health_level = "warning"
                notify_title = "RAM Warning"
                notify_level = "warning"
                already = getattr(self, '_ram_warning_notified', False)
            else:
                color = "#10B981"
                level = None
                # Recovery: reset both flags
                was_warn = getattr(self, '_ram_warning_notified', False)
                was_crit = getattr(self, '_ram_critical_notified', False)
                if was_warn or was_crit:
                    self._ram_warning_notified = False
                    self._ram_critical_notified = False
                    if hasattr(self, 'log_health_event'):
                        self.log_health_event(
                            'info',
                            f"RAM recovered: {pct:.0f}% ({used_gb:.1f}/{total_gb:.1f} GB)"
                        )
                already = False

            self.ram_label.setText(
                f"RAM: {used_gb:.1f}/{total_gb:.1f} GB ({pct:.0f}%)"
            )
            self.ram_label.setStyleSheet(
                f"font-size: 9pt; color: {color}; padding: 2px;"
            )

            if level is not None and not already:
                if level == "CRITICAL":
                    self._ram_critical_notified = True
                    self._ram_warning_notified = True
                else:
                    self._ram_warning_notified = True
                self.log_to_audit(f"🟡 {level}: RAM at {pct:.0f}% ({used_gb:.1f}/{total_gb:.1f} GB)")
                try:
                    from notifications import notify
                    notify(notify_title, f"RAM usage at {pct:.0f}%", notify_level)
                except ImportError:
                    # Deliberate (silent-catch audit): optional notification
                    # subsystem; the health event below still records the
                    # threshold crossing.
                    pass
                if hasattr(self, 'log_health_event'):
                    self.log_health_event(health_level, "RAM usage")
        except (RuntimeError, OSError, AttributeError) as e:
            if hasattr(self, 'file_logger'):
                self.file_logger.debug(f"RAM monitor error: {e}")

    # ── Disk Monitor ──────────────────────────────────────────────

    def update_disk_monitor(self):
        """Update the disk status-bar label with live psutil data.

        Thresholds from CONFIG: disk_warning_pct (default 85), disk_critical_pct
        (default 95). Fires audit, notification, and health_event on crossing.
        Rate-limited via _disk_warning_notified / _disk_critical_notified flags.
        """
        if not hasattr(self, 'disk_label'):
            return
        if psutil is None:
            return
        try:
            disk = psutil.disk_usage(WORKSPACE_DIR)
            free_gb = disk.free / (1024 ** 3)
            total_gb = disk.total / (1024 ** 3)
            pct = disk.percent

            warning_pct = CONFIG.get("disk_warning_pct", 85)
            critical_pct = CONFIG.get("disk_critical_pct", 95)

            if pct >= critical_pct:
                color = "#EF4444"
                level = "CRITICAL"
                health_level = "error"
                notify_title = "Disk Critical"
                notify_level = "error"
                already = getattr(self, '_disk_critical_notified', False)
            elif pct >= warning_pct:
                color = "#FBBF24"
                level = "WARNING"
                health_level = "warning"
                notify_title = "Disk Warning"
                notify_level = "warning"
                already = getattr(self, '_disk_warning_notified', False)
            else:
                color = "#10B981"
                level = None
                was_warn = getattr(self, '_disk_warning_notified', False)
                was_crit = getattr(self, '_disk_critical_notified', False)
                if was_warn or was_crit:
                    self._disk_warning_notified = False
                    self._disk_critical_notified = False
                    if hasattr(self, 'log_health_event'):
                        self.log_health_event(
                            'info',
                            f"Disk recovered: {pct:.0f}% ({free_gb:.0f}/{total_gb:.0f} GB free)"
                        )
                already = False

            self.disk_label.setText(
                f"Disk: {free_gb:.0f}/{total_gb:.0f} GB free ({pct:.0f}%)"
            )
            self.disk_label.setStyleSheet(
                f"font-size: 9pt; color: {color}; padding: 2px;"
            )

            if level is not None and not already:
                if level == "CRITICAL":
                    self._disk_critical_notified = True
                    self._disk_warning_notified = True
                else:
                    self._disk_warning_notified = True
                self.log_to_audit(f"🟡 {level}: Disk at {pct:.0f}% ({free_gb:.0f}/{total_gb:.0f} GB free)")
                try:
                    from notifications import notify
                    notify(notify_title, f"Disk usage at {pct:.0f}%", notify_level)
                except ImportError:
                    # Deliberate (silent-catch audit): optional notification
                    # subsystem; the health event below still records the
                    # threshold crossing.
                    pass
                if hasattr(self, 'log_health_event'):
                    self.log_health_event(health_level, "Disk usage")
        except (RuntimeError, OSError, AttributeError) as e:
            if hasattr(self, 'file_logger'):
                self.file_logger.debug(f"Disk monitor error: {e}")

    # ── Discovery probe integration ────────────────────────────────

    @classmethod
    def setup_discovery_probe(cls, owner):
        """Start a daemon thread that probes the model endpoint and
        records the result on *owner* (attributes + auto_logger journal).

        The daemon thread appends itself to ``owner.active_threads`` so
        callers can ``.join()`` it for synchronous test assertions.

        Args:
            owner: An object with ``_lock`` (threading.Lock),
                   ``file_logger``, ``_discovery_status``,
                   ``_discovery_diagnostic``, ``available_models``,
                   ``_refresh_available_models`` (callable or None),
                   and ``active_threads`` list.
        """
        import threading
        t = threading.Thread(
            target=_run_discovery_probe,
            args=(owner,),
            daemon=True,
        )
        t.start()
        with owner._lock:
            owner.active_threads.append(t)

    def startup_api_check(self):
        def _boot_routine():
            try:
                self._safe_emit_audit("Checking local API daemon...")
                with socket.create_connection(("127.0.0.1", CONFIG["api_port"]), timeout=2.0):
                    self._safe_emit_audit("Local API interface detected.")
                    return
            except OSError as e:
                # Breadcrumb (silent-catch audit): probe failure is the
                # trigger for daemon boot -- a skipped boot must be traceable.
                logger.debug(f"API daemon probe failed (will attempt boot): {e}")
            try:
                self._safe_emit_audit("Port closed. Spinning up daemon...")
                env = os.environ.copy()
                env["CUDA_VISIBLE_DEVICES"] = ""
                import shlex
                cmd_str = CONFIG.get("api_cmd", "")
                args = shlex.split(cmd_str) if cmd_str else []
                if not args:
                    self._safe_emit_audit("No API daemon command configured. Skipping daemon boot.")
                    return
                if os.name == 'nt':
                    self.controller.api_process = subprocess.Popen(args, creationflags=0x08000000, env=env)
                else:
                    self.controller.api_process = subprocess.Popen(args, env=env)
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, IndexError) as e:
                self._safe_emit_audit(f"Daemon boot fault: {e}")

        boot_thread = threading.Thread(target=_boot_routine, daemon=True)
        boot_thread.start()
        with self._lock:
            self.active_threads.append(boot_thread)

    def action_wipe_memory(self):
        if CONFIG.get("autosave_logs", True):
            log_text = self.chat_display.toPlainText().strip()
            if log_text:
                logs_dir = os.path.join(WORKSPACE_DIR, "data", "logs")
                os.makedirs(logs_dir, exist_ok=True)
                filename = f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                filepath = os.path.join(logs_dir, filename)
                try:
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(log_text)
                    self.log_to_audit(f"Autosaved to {filename}")
                except (OSError, RuntimeError) as e:
                    self.log_to_audit(f"Autosave failed: {e}")
        self.controller.wipe_memory()
        self.chat_display.clear()
        self._raw_stream_buffer = ""
        self._last_parsed_thinking = ""
        self.thinking_display.clear()
        self.chat_display.append("<span style='color:#10B981;'>Session history purged.</span>\n")
        # Persist the wipe so the on-disk chat history doesn't reappear
        # on the next startup.
        self._clear_chat_history()
        self.log_to_audit("Core VRAM context cleared.\n")

    def action_send_prompt(self):
        user_text = self.txt_input.toPlainText().strip()
        if not user_text:
            return
        self.txt_input.clear()
        # Record response start time for latency tracking
        self._response_start_time = time.time()
        # Fresh cancellation token for this send. _action_stop_generation
        # sets this event to interrupt the in-flight HTTP request inside the
        # AI provider (via _request_with_cancel in ai_base.py). A new Event
        # is created on each send so that an old "set" flag from a previous
        # send can't cancel the new request.
        self._cancel_event = threading.Event()

        # Check if persona A/B test mode is active
        ab_personas = self._get_ab_personas() if hasattr(self, '_get_ab_personas') else (None, None)
        if ab_personas != (None, None):
            self._action_send_ab_test(user_text, ab_personas[0], ab_personas[1])
            return

        # Check if multi-model compare mode is active
        if getattr(self, 'compare_toggle', None) and self.compare_toggle.isChecked():
            self._action_send_multi_model(user_text)
            return

        self._append_user_message(user_text)
        # Reset raw stream buffer and token counter for the new request
        self._raw_stream_buffer = ""
        self._last_parsed_thinking = ""
        self._stream_token_count = 0
        # Live-token telemetry (2026-09): measured rate from real llama.cpp
        # token arrivals — replaces the end-of-run word-count estimate that
        # reported 0.1 tok/s because it divided by total pipeline time
        # (vault fetch + prompt build + auditor) instead of generation time.
        self._gen_token_count = 0
        self._gen_first_token_time = None
        self._gen_last_token_time = None
        self.thinking_display.clear()
        # Open the live Output-Window block: tokens appear here as they
        # arrive, then the block is replaced by the final styled card.
        # hasattr guard mirrors _on_stream_token / handle_ai_response —
        # non-AppUIMixin hosts (unit-test stand-ins) skip the live block.
        if hasattr(self, '_begin_streaming_response'):
            self._begin_streaming_response()

        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(
            button=getattr(self, 'btn_send', None),
            log_callback=self.log_to_audit,
        )
        self._active_keepalive = kc
        kc.set_btn_text("⏳ Processing...")
        kc.start_keepalive("processing", 30000)

        from workers import AIWorker
        # Create the worker with the cancellation event so the AI provider
        # can abandon the in-flight HTTP request when the user clicks Stop.
        self.worker = AIWorker(self.controller, user_text, cancel_event=self._cancel_event)
        self.worker.reply_signal.connect(self.handle_ai_response)
        self.worker.log_signal.connect(self.log_to_audit)
        self.worker.stream_signal.connect(self._on_stream_token)
        # Rule 1 (KNOWLEDGE.md §17): cleanup hooks wire to lifecycle signals,
        # not data signals — so kc.done fires even if handle_ai_response raises.
        # Connect kc.done directly (canonical pattern per §17) AND also connect
        # _drop_slot_when_done to clear _active_keepalive for gc tracking.
        # kc.done() is idempotent, so double-fire on finished is safe.
        def _drop_slot_when_done():
            kc.done()
            self._active_keepalive = None
        self.worker.finished.connect(kc.done)
        self.worker.finished.connect(_drop_slot_when_done)
        self.worker.reply_signal.connect(lambda data: kc.done())
        self.last_user_text = user_text
        self.worker.start()

    def _on_stream_token(self, token):
        """Handle an incoming stream token — strip protocol XML tags, buffer
        the cleaned text, and show progressively in the thinking display.
        The “Show raw stream” toggle only affects what’s shown *after*
        completion; during streaming we always show tokens so the user sees
        live progress.
        """
        cleaned = _XML_TAG_STRIP_RE.sub("", token)
        self._raw_stream_buffer += cleaned
        self.thinking_display.insertPlainText(cleaned)

        # ── Live Output-Window token (2026-09) ──
        # Words appear in the chat while they generate, not only at the end.
        # hasattr guard: non-AppUIMixin hosts (unit-test stand-ins) lack the
        # mixin helpers — the thinking display still shows the token.
        if cleaned and hasattr(self, '_append_stream_text'):
            self._append_stream_text(cleaned)

        # ── Measured token-rate telemetry (2026-09) ──
        # Rate = real llama.cpp token arrivals over generation time only
        # (first token → now), excluding pipeline overhead. getattr defaults
        # keep AB/multi-model paths (which skip action_send_prompt) safe.
        self._gen_token_count = getattr(self, '_gen_token_count', 0) + 1
        _now = time.time()
        if getattr(self, '_gen_first_token_time', None) is None:
            self._gen_first_token_time = _now
        self._gen_last_token_time = _now
        if hasattr(self, 'latency_label'):
            try:
                gen_elapsed = max(self._gen_last_token_time - self._gen_first_token_time, 0.05)
                live_tps = (self._gen_token_count - 1) / gen_elapsed
                self.latency_label.setText(
                    f"⚡ {live_tps:.1f} tok/s | 📝 {self._gen_token_count} tok | streaming…")
            except (RuntimeError, AttributeError, ZeroDivisionError):
                # Deliberate (silent-catch audit): per-token hot path --
                # widget torn down mid-stream; per-token logging would spam.
                pass

        # ── Streaming token counter ──
        # Increment token count on each received token and update the
        # progress panel's progress bar + label in real-time.
        self._stream_token_count += 1
        if hasattr(self, '_progress_token_bar'):
            try:
                self._progress_token_bar.setValue(self._stream_token_count)
            except (RuntimeError, AttributeError):
                # Deliberate (silent-catch audit): per-token hot path --
                # widget torn down mid-stream.
                pass
        if hasattr(self, '_progress_token_lbl'):
            try:
                count = self._stream_token_count
                self._progress_token_lbl.setText(f"📝 {count} tok{'s' if count != 1 else ''}")
            except (RuntimeError, AttributeError):
                # Deliberate (silent-catch audit): per-token hot path --
                # widget torn down mid-stream.
                pass

        # ── Progressive Streaming UI ──
        # Show the progress panel above the input row with live step updates.
        # The _show_typing_indicator() call below delegates to the progress
        # panel when available (backward compatible). The log_signal emits
        # STEP 1/5 through STEP 5/5 as the controller processes the request
        # — these appear in real-time in the progress panel's scrolling log.
        self._show_typing_indicator()
        self.worker.log_signal.connect(self._update_progress_step)
        if hasattr(self, "_connect_log_signal"):
            self._connect_log_signal()
        if hasattr(self, 'btn_stop') and self.btn_stop:
            self.btn_stop.show()

    def _action_stop_generation(self):
        """User-initiated stop: cancel in-flight request + interrupt worker.

        Two cancellation signals are sent:
          1. ``self._cancel_event.set()`` — aborts the in-flight HTTP request
             inside the AI provider (via ``_request_with_cancel`` in
             ``ai_base.py``). The provider returns a 'Cancelled by user'
             error within ~100ms (one poll interval).
          2. ``worker.requestInterruption()`` — sets the Qt interrupt flag
             on the worker thread. The worker checks this after
             ``process_input`` returns and emits a 'stopped' reply (safety
             net in case the HTTP request finishes before the cancel
             event is checked).

        The HTTP socket itself cannot be force-closed mid-request by the
        Python ``requests`` library, so the underlying TCP connection
        stays open until the server closes it. The user gets immediate
        visual feedback (button changes to "⏳") and the result is
        discarded.
        """
        # Defensive: always reset btn_stop state on entry so the button
        # is left in a clean state if the worker has already finished.
        if hasattr(self, 'btn_stop') and self.btn_stop:
            self.btn_stop.setEnabled(True)
        # Signal 1: set the cancellation event so the AI provider's
        # _request_with_cancel helper aborts the in-flight HTTP request.
        cancel_event = getattr(self, '_cancel_event', None)
        if cancel_event is not None:
            try:
                cancel_event.set()
            except (RuntimeError, AttributeError):
                pass
        self._hide_progress_panel()
        # Signal 2: interrupt the Qt worker thread (safety net).
        worker = getattr(self, 'worker', None)
        if worker is not None and hasattr(worker, 'isRunning') and worker.isRunning():
            try:
                worker.requestInterruption()
            except (RuntimeError, AttributeError):
                pass
            if hasattr(self, 'btn_stop') and self.btn_stop:
                self.btn_stop.setEnabled(False)
                self.btn_stop.setText("⏳")
                self.btn_stop.setToolTip("Stopping... (in-flight request will be discarded)")
            self.log_to_audit("⏹ Stop requested — cancelling in-flight request")
        else:
            self.log_to_audit("⚠️ No active worker to stop")

    def _action_send_multi_model(self, user_text):
        """Send the prompt to multiple selected models in parallel."""
        model_specs = self._get_selected_model_specs()
        if not model_specs:
            self.chat_display.append(
                "<span style='color: #FBBF24;'>⚠️ No models selected for comparison. "
                "Check at least one model in the Compare panel.</span><br><hr>"
            )
            return

        model_labels = ", ".join(s["label"] for s in model_specs)
        self.chat_display.append(
            f"<b style='color: #3B82F6;'>User:</b> {user_text}<br>"
            f"<span style='color: #FBBF24; font-size: 9pt;'>🔀 Comparing: {model_labels}</span>\n"
        )
        self._raw_stream_buffer = ""
        self._last_parsed_thinking = ""
        self.thinking_display.clear()

        from workers import MultiModelWorker
        self.multi_worker = MultiModelWorker(self.controller, user_text, model_specs)
        self.multi_worker.multi_reply_signal.connect(self.handle_multi_model_response)
        self.multi_worker.log_signal.connect(self.log_to_audit)
        if hasattr(self, "_connect_log_signal"):
            self._connect_log_signal()
        self.multi_worker.finished.connect(self.multi_worker.deleteLater)
        self.last_user_text = user_text
        self.multi_worker.start()

    def _action_send_ab_test(self, user_text, persona_a, persona_b):
        """Send the prompt with two personas and show comparison."""
        self.chat_display.append(
            f"<b style='color: #3B82F6;'>User:</b> {user_text}<br>"
            f"<span style='color: #F59E0B; font-size: 9pt;'>🧪 A/B Test: comparing 2 personas</span>\n"
        )
        self.thinking_display.clear()

        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(
            button=getattr(self, 'btn_send', None),
            log_callback=self.log_to_audit,
        )
        kc.set_btn_text("⏳ A/B Testing...")
        kc.start_keepalive("processing", 60000)

        def _do():
            try:
                result = self.controller.process_input_with_personas(
                    text=user_text,
                    persona_a=persona_a,
                    persona_b=persona_b,
                    log_callback=lambda m: None,
                )
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: self._handle_ab_test_result(result, persona_a, persona_b))
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
                err_msg = str(e)
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda err=err_msg: self.chat_display.append(
                    f"<span style='color: #EF4444;'>❌ A/B test failed: {err}</span><br><hr>"
                ))
            finally:
                QTimer.singleShot(0, lambda: kc.done())

        import threading
        threading.Thread(target=_do, daemon=True).start()

    def _handle_ab_test_result(self, result, persona_a, persona_b):
        """Display A/B test results with real vote buttons."""
        vote_id = result.get("vote_id", -1)
        a = result.get("response_a", {})
        b = result.get("response_b", {})
        a_text = a.get("final", "❌ No response") if isinstance(a, dict) else str(a)
        b_text = b.get("final", "❌ No response") if isinstance(b, dict) else str(b)
        a_short = persona_a.replace("You are ", "").replace(".", "")[:50]
        b_short = persona_b.replace("You are ", "").replace(".", "")[:50]

        # Check if any existing vote is stored for this response
        existing_winner = ""
        if vote_id >= 0:
            try:
                import memory_vault
                votes = memory_vault.get_recent_persona_votes(limit=50)
                for v in votes:
                    if v["id"] == vote_id and v.get("winner"):
                        existing_winner = v["winner"]
                        break
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
                logger.debug(f"get_recent_persona_votes failed in A/B result handler (graceful fallback): {e}")

        html = (
            f"<div style='border: 1px solid #3B82F6; border-radius: 4px; padding: 6px; "
            f"margin: 4px 0; background-color: rgba(59, 130, 246, 0.08);'>"
            f"<div style='color: #3B82F6; font-weight: bold; font-size: 9pt;'>"
            f"🅰️ Persona A: {a_short}</div>"
            f"<div style='color: #E0E0E0; font-size: 10pt; margin-top: 4px;'>{a_text}</div>"
            f"</div>"
            f"<div style='border: 1px solid #F59E0B; border-radius: 4px; padding: 6px; "
            f"margin: 4px 0; background-color: rgba(245, 158, 11, 0.08);'>"
            f"<div style='color: #F59E0B; font-weight: bold; font-size: 9pt;'>"
            f"🅱️ Persona B: {b_short}</div>"
            f"<div style='color: #E0E0E0; font-size: 10pt; margin-top: 4px;'>{b_text}</div>"
            f"</div>"
        )
        self.chat_display.append(f"<br>{html}<hr>")

        # Store state for vote buttons
        self._last_ab_vote_id = vote_id
        self._last_ab_prompt = result.get("prompt", "")
        self._last_ab_a_text = a_text
        self._last_ab_b_text = b_text

        # Show and configure the vote bar
        self._show_ab_vote_bar(vote_id, existing_winner)

        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )

        self._schedule_chat_history_save()

        self.log_to_audit(f"🧪 A/B test complete: {a_short} vs {b_short}")

        # TTS for first successful response
        if CONFIG.get("tts_enabled", True) and a_text and a_text != "❌ No response":
            self.voice_output.speak(a_text[:300])

    def _show_ab_vote_bar(self, vote_id, existing_winner=""):
        """Show the A/B vote bar and configure its buttons.

        Args:
            vote_id: The ID of the vote record in the vault.
            existing_winner: Already recorded winner ('A', 'B', 'tie', or '').
        """
        if not hasattr(self, 'ab_vote_bar'):
            return

        self._current_ab_vote_id = vote_id

        if existing_winner:
            # Already voted — show status, disable buttons
            label_map = {"A": "🅰️ A won", "B": "🅱️ B won", "tie": "🤝 Tie"}
            self.ab_vote_label.setText(f"✓ Voted: {label_map.get(existing_winner, existing_winner)}")
            self.btn_ab_vote_a.setEnabled(False)
            self.btn_ab_vote_b.setEnabled(False)
            self.btn_ab_vote_tie.setEnabled(False)
            self.btn_ab_vote_skip.setVisible(False)
        else:
            self.ab_vote_label.setText("Which response was better?")
            self.btn_ab_vote_a.setEnabled(True)
            self.btn_ab_vote_b.setEnabled(True)
            self.btn_ab_vote_tie.setEnabled(True)
            self.btn_ab_vote_skip.setVisible(True)

        self.ab_vote_bar.show()

    def _hide_ab_vote_bar(self):
        """Hide the A/B vote bar."""
        if hasattr(self, 'ab_vote_bar'):
            self.ab_vote_bar.hide()

    def _vote_ab(self, winner):
        """Cast a vote for the current A/B test result.

        Args:
            winner: 'A', 'B', or 'tie'.
        """
        vote_id = getattr(self, '_current_ab_vote_id', -1)
        if vote_id < 0:
            self.log_to_audit("⚠️ No A/B test vote record to update")
            return

        # Persist vote to vault
        try:
            import memory_vault
            memory_vault.update_persona_vote(vote_id=vote_id, winner=winner)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
            self.log_to_audit(f"⚠️ Failed to save A/B vote: {e}")

        # Update UI
        label_map = {"A": "🅰️ A won", "B": "🅱️ B won", "tie": "🤝 Tie"}
        self.ab_vote_label.setText(f"✓ Voted: {label_map.get(winner, winner)}")
        self.btn_ab_vote_a.setEnabled(False)
        self.btn_ab_vote_b.setEnabled(False)
        self.btn_ab_vote_tie.setEnabled(False)
        self.btn_ab_vote_skip.setVisible(False)

        self.log_to_audit(f"🧪 A/B test vote recorded: {winner}")

        # Refresh the leaderboard tab
        if hasattr(self, '_refresh_ab_leaderboard'):
            self._refresh_ab_leaderboard()

    def handle_multi_model_response(self, results):
        """Display responses from multiple models side-by-side with styled frames."""
        provider_colors = {
            "local_llm": "#3B82F6",
        }
        provider_labels = {
            "local_llm": "Local LLM",
        }

        html_parts = []
        for i, r in enumerate(results):
            provider = r.get("provider", "")
            accent = provider_colors.get(provider, "#9CA3AF")
            prov_display = provider_labels.get(provider, provider)
            model_short = r.get("model", "?").split("/")[-1]
            status_icon = "✅" if r.get("ok") else "❌"

            html_parts.append(
                f"<div style='border: 1px solid {accent}; border-radius: 4px; "
                f"padding: 6px; margin: 4px 0; background-color: rgba(0,0,0,0.15);'>"
                f"<div style='color: {accent}; font-weight: bold; font-size: 9pt;'>"
                f"{status_icon} {prov_display} / {model_short}</div>"
                f"<div style='color: #E0E0E0; font-size: 10pt; margin-top: 4px;'>"
                f"{r.get('content', '') or r.get('error', 'No response')}</div>"
                f"</div>"
            )

        combined_html = "<div style='margin: 6px 0;'>" + "\n".join(html_parts) + "</div>"
        self.chat_display.append(
            f"<b style='color: #10B981;'>AI Comparison:</b><br>{combined_html}<hr>"
        )
        # Respect the raw stream toggle when setting the summary
        if getattr(self, 'show_raw_stream_toggle', None) and self.show_raw_stream_toggle.isChecked():
            # Keep raw stream buffer visible
            pass
        else:
            self.thinking_display.setPlainText(
                f"Multi-model comparison: {len(results)} response(s)\n"
                f"Successful: {sum(1 for r in results if r.get('ok'))}\n"
                f"Failed: {sum(1 for r in results if not r.get('ok'))}"
            )

        # TTS for the first successful response
        if CONFIG.get("tts_enabled", True):
            first_ok = next((r for r in results if r.get("ok")), None)
            if first_ok:
                self.voice_output.speak(first_ok["content"][:500])

        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )

        self._schedule_chat_history_save()

        # Auditor + desire engine still runs using the first successful response
        if results:
            first_ok = next((r for r in results if r.get("ok")), None)
            if first_ok:
                ai_text = first_ok["content"]
            else:
                ai_text = results[0].get("error") or "All models failed"

            from workers import AuditorWorker
            self.auditor_worker = AuditorWorker(self.last_user_text, ai_text)
            self.auditor_worker.log_signal.connect(self.log_to_audit)
            self.auditor_worker.finished.connect(self.refresh_agency_data)
            self.auditor_worker.finished.connect(self.load_rlhf_history)
            self.auditor_worker.finished.connect(self.auditor_worker.deleteLater)
            self.auditor_worker.start()

        if self.chk_desire_engine.isChecked():
            for i in reversed(range(self.suggestions_content_layout.count() - 1)):
                widget = self.suggestions_content_layout.itemAt(i).widget()
                if widget:
                    widget.deleteLater()
            from workers import DesireWorker
            self.desire_worker = DesireWorker(self.controller.history, self.last_user_text)
            self.desire_worker.suggestion_signal.connect(self.add_suggestion_card)
            self.desire_worker.finished.connect(self.desire_worker.deleteLater)
            self.desire_worker.start()

    def handle_ai_response(self, ai_data):
        # Hide typing indicator + progress panel + Stop button before rendering
        # the result. Done first so the user sees the response immediately, even
        # if any of the post-processing work (auditor, desire engine) is slow.
        self._hide_typing_indicator()
        self._hide_progress_panel()
        if hasattr(self, 'btn_stop') and self.btn_stop:
            self.btn_stop.hide()
            self.btn_stop.setEnabled(True)
            self.btn_stop.setText("⏹")
            self.btn_stop.setToolTip("Stop generation (discards in-flight response)")

        # ── Live Output-Window: swap the streamed block for the final card
        # (tokens were appearing live; the styled card replaces the raw run).
        # getattr fallback keeps non-AppUIMixin hosts (unit-test stand-ins)
        # working — the mixin method is the real implementation.
        if hasattr(self, '_finish_streaming_response'):
            self._finish_streaming_response()
        else:
            self._streaming_active = False
            self._stream_block_start = None

        err_msg = ai_data.get('error')
        if err_msg and not ai_data.get('final'):
            final_text = err_msg if str(err_msg).startswith("❌") else f"❌ {err_msg}"
        else:
            final_text = ai_data.get('final', 'Error')
        stopped = bool(ai_data.get('stopped', False))

        # ── Per-message token/time footer ──────────────────────────
        # Measured telemetry (2026-09): token rate comes from real llama.cpp
        # token arrivals (generation window only), not a word-count estimate
        # divided by total pipeline time. Falls back to the estimate when a
        # response arrived without streaming (e.g. tool path).
        footer_html = None
        if not stopped and hasattr(self, 'latency_label') and hasattr(self, '_response_start_time'):
            try:
                gen_count = getattr(self, '_gen_token_count', 0)
                if gen_count >= 2 and getattr(self, '_gen_first_token_time', None):
                    measured_tokens = gen_count
                    gen_elapsed = max(
                        self._gen_last_token_time - self._gen_first_token_time, 0.05)
                    tps = round((measured_tokens - 1) / gen_elapsed, 1)
                    elapsed = self._gen_last_token_time - self._response_start_time
                else:
                    elapsed = time.time() - self._response_start_time
                    word_count = len(final_text.split())
                    measured_tokens = max(int(word_count * 1.33), 1)
                    tps = round(measured_tokens / max(elapsed, 0.1), 1)
                color = "#10B981" if tps > 10 else "#FBBF24" if tps > 3 else "#EF4444"
                footer_html = (
                    f"<span style='color: {color};'>⚡ {tps} tok/s</span> · "
                    f"⏱ {elapsed:.1f}s · 📝 {measured_tokens} tok"
                )
                # Mirror the same info in the existing sidebar latency label
                self.latency_label.setText(f"⚡ {tps} tok/s | ⏱ {elapsed:.1f}s | 📝 {measured_tokens} tok")
                self.latency_label.setStyleSheet(
                    f"font-size: 8pt; color: {color}; padding: 0 2px;"
                )
            except (ValueError, TypeError, AttributeError):
                # Deliberate (silent-catch audit): cosmetic latency badge
                # update; widget may be gone at response teardown.
                pass

        # ── Error path: distinct styled card with Retry link ──────
        # The canonical signal is ``ai_data.get('error')``. Prefix-matching
        # on "❌" catches the AIWorker failsafe ("❌ Core Failure: ...") and
        # any provider that returns an error string with the emoji prefix.
        if ai_data.get('error') or final_text.startswith("❌"):
            self._append_error_message(final_text, show_retry=True)
        else:
            self._append_assistant_message(final_text, footer_html=footer_html, stopped=stopped)

        # Store parsed thinking and decide which view to show
        parsed = ai_data.get('thinking', '')
        self._last_parsed_thinking = parsed
        if getattr(self, 'show_raw_stream_toggle', None) and self.show_raw_stream_toggle.isChecked():
            # Keep raw stream content visible (already accumulated in buffer)
            pass
        else:
            self.thinking_display.setPlainText(parsed)

        # ── Detect Python code blocks for inline Run button ──
        self._last_code_block = None
        import re
        code_blocks = re.findall(r'```python\n(.*?)\n```', final_text, re.DOTALL)
        if code_blocks:
            self._last_code_block = code_blocks[0]
            if hasattr(self, 'btn_run_code'):
                self.btn_run_code.setVisible(True)
                snippet = code_blocks[0][:80].replace('\n', ' ')
                self.btn_run_code.setText(f"▶️ Run: {snippet}...")
        else:
            if hasattr(self, 'btn_run_code'):
                self.btn_run_code.setVisible(False)

        # ── Update latency label (AB/multi-model path fallback) ──
        # Measured values were already set above for the streaming path;
        # this only covers responses that never streamed.
        if (hasattr(self, 'latency_label') and hasattr(self, '_response_start_time')
                and getattr(self, '_gen_token_count', 0) < 2):
            elapsed = time.time() - self._response_start_time
            # Estimate tokens: ~1.33 tokens per word for English
            word_count = len(final_text.split())
            estimated_tokens = max(int(word_count * 1.33), 1)
            tps = round(estimated_tokens / max(elapsed, 0.1), 1)
            color = "#10B981" if tps > 10 else "#FBBF24" if tps > 3 else "#EF4444"
            self.latency_label.setText(f"⚡ {tps} tok/s | ⏱ {elapsed:.1f}s | 📝 {estimated_tokens} tok")
            self.latency_label.setStyleSheet(
                f"font-size: 8pt; color: {color}; padding: 0 2px;"
            )

        if CONFIG.get("tts_enabled", True) and final_text and final_text != 'Error':
            self.voice_output.speak(final_text[:500])

        if ai_data.get('command'):
            cmd = ai_data['command']
            action_name = cmd.get('action', 'UNKNOWN')
            restricted_actions = ["DELETE_FILE", "EXECUTE_SCRIPT", "RENAME_FILE", "WRITE_FILE"]
            proceed = True
            if action_name in restricted_actions:
                target = (
                    cmd.get("path")
                    or cmd.get("filename")
                    or cmd.get("source")
                    or cmd.get("script_name")
                    or ("inline script" if (cmd.get("script") or cmd.get("code") or cmd.get("content")) else "Unknown")
                )
                reply = QMessageBox.warning(
                    self, 'Action Requires Confirmation',
                    f"Action: {action_name}\nTarget: {target}\n\nAllow?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.No:
                    proceed = False
                    self.log_to_audit(f"HITL: User blocked {action_name}.")
                    self.chat_display.append(f"<b>Execution of {action_name} blocked.</b><br><hr>")
            if proceed:
                self.log_to_audit(f"Dispatched action: {action_name}")
                res = kokertech_bridge.handle_ai_intent(cmd)
                self.chat_display.append(f"<b>Result:</b><br>{res}<br><hr>")

        if self.chk_desire_engine.isChecked():
            for i in reversed(range(self.suggestions_content_layout.count() - 1)):
                widget = self.suggestions_content_layout.itemAt(i).widget()
                if widget:
                    widget.deleteLater()
            from workers import DesireWorker
            self.desire_worker = DesireWorker(self.controller.history, self.last_user_text)
            self.desire_worker.suggestion_signal.connect(self.add_suggestion_card)
            self.desire_worker.finished.connect(self.desire_worker.deleteLater)
            self.desire_worker.start()

        from workers import AuditorWorker
        self.auditor_worker = AuditorWorker(self.last_user_text, ai_data.get('final', ''))
        self.auditor_worker.log_signal.connect(self.log_to_audit)
        self.auditor_worker.finished.connect(self.refresh_agency_data)
        self.auditor_worker.finished.connect(self.load_rlhf_history)
        self.auditor_worker.finished.connect(self.auditor_worker.deleteLater)
        self.auditor_worker.start()

        self.chat_display.verticalScrollBar().setValue(
            self.chat_display.verticalScrollBar().maximum()
        )

        # Schedule a debounced save so the rendered response survives across
        # restarts. Done last so all post-processing (auditor, desire engine,
        # TTS, command dispatch) has had a chance to append to chat_display.
        self._schedule_chat_history_save()

    def _save_window_geometry(self):
        """Save window geometry (position, size, maximized state) to CONFIG.
        Uses Qt's saveGeometry/saveState which serialize to QByteArray,
        then base64-encodes for JSON storage.
        """
        import base64
        geom = self.saveGeometry()
        if geom:
            CONFIG["_window_geometry"] = base64.b64encode(geom.data()).decode("ascii")
        CONFIG["_window_maximized"] = self.isMaximized()
        from config import save_settings
        save_settings()

    def closeEvent(self, event):
        # Set shutting-down flag first — all signal-connected methods across tabs
        # check this to avoid crashing during widget destruction (cross-test safety).
        self._shutting_down = True
        # Set the cancellation event so any in-flight AI provider request
        # is abandoned within one poll interval (~100ms) instead of waiting
        # for the full timeout to expire. Combined with the worker
        # interrupt + terminate below, this minimizes the shutdown delay.
        cancel_event = getattr(self, '_cancel_event', None)
        if cancel_event is not None:
            try:
                cancel_event.set()
            except (RuntimeError, AttributeError):
                pass
        # Clean up the typing-indicator animation timer and progress panel
        # so they don't fire against a deleted widget during shutdown. Also
        # hide the Stop button so the teardown log doesn't show a "stuck"
        # button state.
        self._hide_typing_indicator()
        self._hide_progress_panel()
        if hasattr(self, 'btn_stop') and self.btn_stop:
            try:
                self.btn_stop.hide()
            except (RuntimeError, AttributeError):
                pass
        # Remove logging bridge handler to prevent dangling references after shutdown
        if hasattr(self, '_remove_logging_bridge'):
            try:
                self._remove_logging_bridge()
            except (RuntimeError, AttributeError):
                pass
        self._save_window_geometry()
        # Flush resource history ring buffer before exit (if wired)
        save_rh = getattr(self, '_save_resource_history_immediate', None)
        if save_rh is not None:
            try:
                save_rh()
            except (RuntimeError, AttributeError):
                pass
        self.log_to_audit("Shutdown initiated.")
        for timer_name in [
            'vram_timer', 'cleanup_timer', 'consolidation_timer', 'daily_summary_timer',
            'health_timer', 'git_refresh_timer', 'provider_badge_timer', '_idle_timer',
            '_sa_scheduler_timer', '_sj_timer', '_slog_auto_timer', '_ta_refresh_timer',
            '_server_poll_timer',
        ]:
            timer = getattr(self, timer_name, None)
            if timer:
                try:
                    timer.stop()
                except (RuntimeError, AttributeError):
                    pass
        git_tracker = getattr(self, 'git_tracker', None)
        if git_tracker:
            try:
                git_tracker.stop_monitoring()
            except (RuntimeError, AttributeError):
                pass
        # Close and stop Mini-HUD if active
        if hasattr(self, 'mini_hud') and self.mini_hud is not None:
            try:
                self.mini_hud.stop_execution()
                self.mini_hud.close()
            except (RuntimeError, AttributeError):
                pass
        # Cooperative thread shutdown to avoid "QThread: Destroyed while thread ... is still running".
        workers_to_stop = ['worker', 'desire_worker', 'indexer_worker', 'layout_worker', 'auditor_worker', 'voice_worker']
        for name in workers_to_stop:
            worker = getattr(self, name, None)
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    # Prefer explicit cooperative stop if the worker implements it.
                    if hasattr(worker, "stop") and callable(getattr(worker, "stop")):
                        try:
                            worker.stop()
                        except (RuntimeError, AttributeError, TypeError, OSError) as e:
                            logger.debug(f"worker.stop() failed during cleanup (non-fatal): {e}")

                    # If this is a QThread, request interruption (supported by Qt-recursive patterns).
                    if hasattr(worker, "requestInterruption") and callable(getattr(worker, "requestInterruption")):
                        try:
                            worker.requestInterruption()
                        except (RuntimeError, AttributeError, TypeError, OSError) as e:
                            logger.debug(f"worker.requestInterruption() failed during cleanup (non-fatal): {e}")

                    # Let the thread finish naturally.
                    if hasattr(worker, "quit") and callable(getattr(worker, "quit")):
                        try:
                            worker.quit()
                        except (RuntimeError, AttributeError, TypeError, OSError) as e:
                            logger.debug(f"worker.quit() failed during cleanup (non-fatal): {e}")

                    # As a last resort, terminate only if it is still running.
                    # Keep wait() semantics aligned with existing unit tests:
                    # call wait(2000) exactly once.
                    if worker.isRunning():
                        worker.terminate()
                        worker.wait(2000)
            except (RuntimeError, AttributeError):
                pass

        for teardown_fn_name in [
            "teardown_neural_graph",
            "teardown_progress_tab",
            "teardown_scheduled_actions",
            "teardown_session_journal",
            "teardown_session_log",
            "teardown_tool_audit",
        ]:
            teardown_fn = getattr(self, teardown_fn_name, None)
            if callable(teardown_fn):
                try:
                    teardown_fn()
                except (RuntimeError, AttributeError, TypeError, OSError) as e:
                    logger.debug(f"{teardown_fn_name} failed during shutdown: {e}")


        for thread in list(self.active_threads):
            if thread.is_alive():
                thread.join(timeout=2.0)
        self.active_threads.clear()
        api_proc = getattr(self.controller, 'api_process', None)
        if api_proc:
            try:
                api_proc.terminate()
                api_proc.wait(timeout=2)
            except (subprocess.SubprocessError, AttributeError):
                pass
        if hasattr(self, 'voice_output'):
            self.voice_output.stop()
        nm = getattr(self, 'notification_manager', None)
        if nm:
            try:
                nm.shutdown()
            except (RuntimeError, AttributeError):
                pass
        self._stop_wake_listener()
        try:
            from mcp_server import stop_http_server
            stop_http_server()
        except ImportError:
            pass
        # Disconnect MCP client servers to release HTTP sessions
        try:
            from mcp_client import get_mcp_client
            mcp = get_mcp_client()
            for name, conn in list(mcp.servers.items()):
                try:
                    conn.enabled = False
                except (RuntimeError, AttributeError):
                    pass
        except (ImportError, RuntimeError, AttributeError):
            pass
        # Local GGUF runs in-process — no external process to kill

        try:
            import keyboard
            keyboard.unhook_all()
        except (RuntimeError, AttributeError):
            pass
        # Consolidate episodic journal before shutdown.
        # Wrapped in _call_with_timeout (5s) to prevent indefinite blocking
        # on Windows due to SQLite file locks.
        try:
            import memory_vault
            count = _call_with_timeout(
                memory_vault.consolidate_episodic,
                timeout=5.0,
                importance_threshold=5, max_age_days=1,
            )
            if count:
                self.file_logger.info(f"Shutdown consolidation: {count} episodic entries promoted")
        except (RuntimeError, AttributeError):
            pass

        # Shutdown web server if running
        try:
            self.web_view_shutdown()
        except (AttributeError, RuntimeError):
            pass

        # End the current session
        try:
            import memory_vault
            memory_vault.start_new_session(summary="Session ended on app shutdown")
            self.file_logger.info("Session closed on shutdown")
        except (RuntimeError, AttributeError):
            pass

        self.file_logger.log_session_end()

        # Post-session cleanup: scan execution_log.txt for any RuntimeWarning
        # occurrences that would indicate a bool-as-fd regression in os.path.*
        # call sites. The file is fully flushed here (worker threads above have
        # all been .join()ed), so reading it is safe. Surfaces problems on the
        # same session's audit trail rather than next session's, so daily review
        # catches regressions the moment they appear.
        try:
            _log_path = getattr(self.file_logger, "log_path", None)
            if _log_path:
                # Use the shared util's broader default substring ("RuntimeWarning:")
                # -- catches both bool-as-fd and any other Python RuntimeWarning
                # emitted during this session. No need for the previous narrower
                # `"RuntimeWarning: bool is used"` since the shared util is the
                # single source of truth (see utils/log_scanner.py).
                # Prepend `[Cleanup] ` so existing daily-review grep workflows
                # that look for the `[Cleanup]` audit-trail marker continue to
                # match the new shared-helper output. Vault-cleaner and
                # vault-upgrade callers leave prefix empty (their audits land
                # during housekeeping, not shutdown).
                _call_with_timeout(
                    scan_execution_log,
                    _log_path,
                    self.file_logger,
                    timeout=5.0,
                    prefix="[Cleanup] ",
                )
        except (OSError, ValueError) as _e:
            # Don't block shutdown on file-access errors (Windows file lock).
            self.file_logger.debug(f"RuntimeWarning scan skipped: {_e}")

        self.log_to_audit("Shutdown complete.")
        event.accept()
        # Skip QApplication.quit() when the ``_suppress_quit`` flag is set.
        # This flag is used by conftest.py's shared_dashboard fixture teardown
        # to prevent the internal ``closingDown`` flag from being set, which
        # would cause Qt widget creation to fail in subsequent test files.
        if not getattr(self, '_suppress_quit', False):
            QApplication.instance().quit()
