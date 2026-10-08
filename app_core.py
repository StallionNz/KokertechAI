"""
app_core.py — KokertechAI Dashboard entry point.
Refactored: orchestrates mixins from app_ui, app_hotkeys, app_plugins, app_lifecycle.
"""
import os
import sys
import threading

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox

from kokertechController import KokertechController
from logging_config import get_logger
from config import CONFIG, WORKSPACE_DIR, check_last_save_integrity, restore_backup
from copilot_features import VoiceOutput

# Tab mixins
from tabs import (
    WorkspaceTabMixin, NeuralGraphTabMixin, SettingsTabMixin,
    CognitiveAgencyTabMixin, RLHFTrainerTabMixin, DocumentPipelineTabMixin,
    WorkflowTabMixin, ComputerUseTabMixin, HealthTabMixin, GitTrackerTabMixin,
    ChatHistoryTabMixin, ThemeBuilderTabMixin, RagTabMixin,
    SessionBrowserTabMixin, WebViewTabMixin, SessionLogTabMixin,
    MemoryBrowserTabMixin,
    ScheduledActionsTabMixin, PluginStoreTabMixin,
    PersonaABTestingTabMixin, MCPClientTabMixin,
    ProgressTabMixin,
    DashboardContext,
)

# New app mixins
from app_ui import AppUIMixin
from app_hotkeys import AppHotkeysMixin
from app_plugins import AppPluginMixin
from app_lifecycle import AppLifecycleMixin

logger = get_logger(name="AppCore")


class KokertechDashboard(
    QMainWindow,
    # UI & Interaction
    AppUIMixin,
    AppHotkeysMixin,
    AppPluginMixin,
    AppLifecycleMixin,
    # Tab mixins
    WorkspaceTabMixin, NeuralGraphTabMixin, SettingsTabMixin,
    CognitiveAgencyTabMixin, RLHFTrainerTabMixin, DocumentPipelineTabMixin,
    WorkflowTabMixin, ComputerUseTabMixin, HealthTabMixin, GitTrackerTabMixin,
    ChatHistoryTabMixin, RagTabMixin, SessionBrowserTabMixin,
    ThemeBuilderTabMixin, WebViewTabMixin,
    SessionLogTabMixin, ProgressTabMixin, MemoryBrowserTabMixin,
    ScheduledActionsTabMixin, PluginStoreTabMixin,
    PersonaABTestingTabMixin, MCPClientTabMixin,
):
    audit_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._init_core_components()
        self._init_ui_and_state()
        self._init_timers()
        self._init_async_and_deferred_tasks()
        self._init_startup_checks()

        self.file_logger.info("Dashboard initialized")

    def _init_core_components(self):
        """Initialize core non-UI components like controller, logger, etc."""
        self.controller = KokertechController()
        self.file_logger = get_logger(name="Dashboard")
        self.audit_signal.connect(self.log_to_audit)
        self.ctx = DashboardContext(
            controller=self.controller,
            file_logger=self.file_logger,
            log_to_audit=self.log_to_audit,
            audit_signal=self.audit_signal,
            config=CONFIG,
            restore_chat_input=self._restore_chat_input_bridge,
            switch_tab=lambda idx: self.tabs.setCurrentIndex(idx) if hasattr(self, "tabs") else None,
        )
        self.active_threads = []
        self._lock = threading.Lock()
        self.tts_enabled = CONFIG.get("tts_enabled", True)
        self.voice_output = VoiceOutput()

        self.file_logger.info(
            f"[Startup] Config + vault initialised: "
            f"provider={CONFIG.get('active_provider', '?')}, "
            f"vault={self.controller.workspace}\\kokertech_vault.db"
        )

    def _restore_chat_input_bridge(self, text: str) -> None:
        """Composition bridge allowing tabs to restore chat text without direct widget coupling."""
        if hasattr(self, "txt_input"):
            self.txt_input.setPlainText(text)
            self.txt_input.setFocus()
        if hasattr(self, "tabs"):
            self.tabs.setCurrentIndex(0)

    def _prewarm_model(self):
        """Pre-warm the AI model in a daemon thread at startup.

        Loads the configured GGUF model in a background thread so it's ready
        before the user sends their first message.  Without this, the first
        chat message pays a 5-30s cold-start penalty while llama.cpp loads
        the model from disk.

        Logs progress to ``execution_log.txt`` so startup behaviour is
        auditable without needing the GUI to be fully painted.

        No hardcoded default (config change 2026-09): when no model has
        been explicitly applied (CONFIG["model_file"] empty) the pre-warm
        is skipped with a neutral status — the user must pick a model in
        Settings → ⚡ Apply. An applied selection persists and pre-warms
        on subsequent starts as before.
        """
        def _load_in_background():
            try:
                from ai_base import get_provider
                self.file_logger.info("[Pre-warm] Resolving model provider …")
                self._set_model_status("🔵 Loading model…", "#3B82F6")
                provider = get_provider(name="local_llm")
                if not (provider.model_file or "").strip():
                    # No default model (config change 2026-09): startup must
                    # NOT auto-load anything. The user explicitly picks a
                    # model in Settings → ⚡ Apply; an applied selection
                    # persists in CONFIG and pre-warms as before.
                    self.file_logger.info(
                        "[Pre-warm] No model selected — skipping auto-load "
                        "(user must pick one in Settings)"
                    )
                    self._set_model_status("⚪ No model selected", "#9CA3AF")
                    return
                if provider._model_instance is not None:
                    self.file_logger.info("[Pre-warm] Model already loaded — skipping")
                    self._set_model_status("🟢 Model ready", "#10B981")
                    return
                model_path = provider._resolve_model_path(provider.model_file)
                if not model_path:
                    self.file_logger.warning(
                        f"[Pre-warm] Model file not found: {provider.model_file} "
                        f"(searched in {provider.models_dir})"
                    )
                    self._set_model_status("❌ Model file not found", "#EF4444")
                    return
                self.file_logger.info(
                    f"[Pre-warm] Loading model: {os.path.basename(model_path)} …"
                )
                start = time.time()
                ok = provider._load_model(model_path)
                elapsed = time.time() - start
                if ok:
                    self.file_logger.info(
                        f"[Pre-warm] Model loaded in {elapsed:.1f}s "
                        f"(ctx={provider.configured_n_ctx}, gpu={provider.n_gpu_layers})"
                    )
                    self._set_model_status(
                        f"🟢 Model ready ({elapsed:.1f}s)", "#10B981"
                    )
                else:
                    self.file_logger.error(
                        f"[Pre-warm] Failed to load model after {elapsed:.1f}s"
                    )
                    self._set_model_status("❌ Model load failed", "#EF4444")
            except (RuntimeError, ValueError, TypeError, OSError, AttributeError, ImportError) as e:
                self.file_logger.error(f"[Pre-warm] Unexpected error: {e}")
                self._set_model_status(f"❌ {e}"[:60], "#EF4444")

        import os, time, threading
        threading.Thread(target=_load_in_background, daemon=True, name="prewarm-model").start()

    def _init_ui_and_state(self):
        """Initialize UI, window geometry, theme, and other UI-related state."""
        self.init_ui()

        # Set window icon
        icon_path = os.path.join(getattr(self.controller, "workspace", WORKSPACE_DIR), "kokertech.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self._restore_window_geometry()
        # Defer clamping until after show() for reliability
        QTimer.singleShot(0, self._clamp_window_to_screen)

        self.apply_theme(CONFIG.get("active_theme", "Cyberpunk (Default)"))
        self._load_chat_history()

        if CONFIG.get("always_on_top", False):
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            self.show()

    def _init_timers(self):
        """Set up and start all background timers."""
        self.setup_vram_monitor()
        self.setup_cleanup_timer()
        self.setup_consolidation_timer()

        _armed = [
            _n for _n in (
                "vram_timer", "cleanup_timer",
                "consolidation_timer", "daily_summary_timer",
            )
            if getattr(self, _n, None) is not None
        ]
        self.file_logger.info(
            f"[Startup] Timers armed: {len(_armed)}/4 "
            f"({','.join(_armed)})"
        )

    def _init_async_and_deferred_tasks(self):
        """Initialize tasks that run asynchronously or are deferred."""
        self._prewarm_model()
        self.setup_mcp_client()
        self.file_logger.info(
            "[Startup] MCP client init scheduled "
            "(count reported async from background thread)"
        )

        # Defer tasks that might block or require an active event loop
        QTimer.singleShot(0, self._deferred_init_dashboard)

        self.file_logger.log_session_start()

        try:
            from notifications import get_notification_manager
            self.notification_manager = get_notification_manager(parent=self)
            if self.notification_manager:
                self.notification_manager.initialize()
        except (ImportError, RuntimeError, OSError, ValueError, TypeError, AttributeError) as e:
            self.file_logger.debug(f"Notification init skipped: {e}")

    def _init_startup_checks(self):
        """Run onboarding for first-time users or check save integrity."""
        def _safe_onboarding():
            try:
                self._run_onboarding_or_show()
            except RuntimeError:
                pass  # dashboard already deleted

        try:
            from onboarding_wizard import is_first_run
            if is_first_run():
                self.file_logger.info(
                    "[Startup] Onboarding wizard scheduled (first run detected)"
                )
                self.hide()
                QTimer.singleShot(100, _safe_onboarding)
            else:
                self.file_logger.info(
                    "[Startup] Save-integrity check scheduled "
                    "(non-first-run path)"
                )
                QTimer.singleShot(200, self._check_save_integrity)
        except (ImportError, RuntimeError, OSError, ValueError, TypeError, AttributeError) as e:
            self.file_logger.debug(f"Onboarding check skipped: {e}")
            self.file_logger.info(
                "[Startup] Save-integrity check scheduled "
                "(onboarding check errored)"
            )
            QTimer.singleShot(200, self._check_save_integrity)

    # closeEvent must be defined on KokertechDashboard to override
    # QMainWindow.closeEvent in the MRO. Delegates to AppLifecycleMixin
    # which contains the full cleanup logic (timers, workers, threads, etc.).
    def closeEvent(self, event):
        AppLifecycleMixin.closeEvent(self, event)

    def _deferred_init_dashboard(self):
        """Deferred initialisation that runs after the event loop starts.
        Called once via QTimer.singleShot(0) at the end of __init__().
        Performs hotkey registration and API startup which can block or hang
        before the Qt event loop is running.
        """
        self.apply_hotkey()
        if CONFIG.get("auto_start_api", False):
            self.startup_api_check()

    def _run_onboarding_or_show(self):
        try:
            from onboarding_wizard import run_onboarding
            run_onboarding(self)
        except (ImportError, RuntimeError, OSError, ValueError, TypeError, AttributeError) as e:
            # Silent-catch audit convention: a failed onboarding run must leave a
            # breadcrumb. Severity `warning` -- the app deliberately continues to
            # the dashboard, but the wizard did not run. Module-level logger (not
            # self.file_logger): tests construct instances via __new__.
            logger.warning(f"Onboarding wizard failed, continuing to dashboard: {e}")
        self.show()
        self.raise_()
        self.activateWindow()
        # Re-clamp after show() — onboarding may have resized or the
        # deferred clamp from __init__ may have fired while hidden.
        self._clamp_window_to_screen()
        # Run integrity check after onboarding completes
        self._check_save_integrity()

    def _check_save_integrity(self):
        """Check if the last save completed successfully.
        If files are corrupt (interrupted save), prompt to restore from backup.
        Runs 200ms after init so the UI is fully ready for dialogs.
        """
        # Guard: tests sometimes construct KokertechDashboard via __new__ (no QObject init),
        # so even getattr() can raise "super-class __init__ was never called".
        # Also guard against timers firing after closeEvent.
        try:
            if getattr(self, '_shutting_down', False):
                return
        except RuntimeError:
            return
        try:
            result = check_last_save_integrity()
            if result["ok"]:
                return

            # Build a descriptive error message
            issues = []
            if not result["settings_valid"]:
                issues.append("app_settings.json is corrupted")
            if not result["identity_valid"]:
                issues.append("user_identity.json is corrupted")

            msg = (
                "The previous session may have crashed while saving.\n\n"
                "Issues detected:\n" + "\n".join(f"  • {i}" for i in issues) + "\n\n"
            )

            if result["backups_available"]:
                msg += (
                    f"A backup from {result['latest_backup_label']} is available.\n"
                    "Would you like to restore it now?"
                )
                reply = QMessageBox.question(
                    self, "Save Integrity Warning",
                    msg + "\n\nChoose Yes to restore from backup, or No to continue with current (possibly broken) files.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    success = restore_backup(result["latest_backup_ts"])
                    if success:
                        QMessageBox.information(
                            self, "Backup Restored",
                            f"Settings restored from {result['latest_backup_label']}.\n\n"
                            "The UI has been refreshed with the restored values."
                        )
                        self.file_logger.info(
                            f"Auto-restored from backup after crash: {result['latest_backup_ts']}"
                        )
                        # Sync UI to reflect restored config
                        if hasattr(self, '_sync_ui_from_config'):
                            self._sync_ui_from_config()
                    else:
                        QMessageBox.critical(
                            self, "Restore Failed",
                            "Could not restore from backup. The backup files may have been deleted."
                        )
            else:
                msg += (
                    "No backups are available.\n"
                    "The app will continue but some settings may not load correctly.\n"
                    "You can manually fix these files or delete them to start fresh."
                )
                QMessageBox.warning(self, "Save Integrity Warning", msg)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
            self.file_logger.debug(f"Save integrity check error: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = KokertechDashboard()
    window.show()
    sys.exit(app.exec())
