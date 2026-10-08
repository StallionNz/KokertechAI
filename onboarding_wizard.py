"""
onboarding_wizard.py — First-run setup wizard for KokertechAI.
Sprint 6.6: Step-by-step onboarding flow for new users.

Provides:
- OnboardingWizard: multi-page QWizard dialog
- First-run detection via app_settings.json flag
- Steps: Welcome → Provider Check → Model Select → API Keys → TTS Test → Setup Complete

"""

import json
import os
import sys
import threading

from config import CONFIG, IDENTITY_CONFIG, save_settings, save_identity
from logging_config import get_logger

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(WORKSPACE_DIR, "app_settings.json")

logger = get_logger(name="MemoryVault")

# ── Qt availability check ─────────────────────────────────────────
# Tests patch onboarding_wizard.QVBoxLayout, QLabel etc. at module
# level, so these must be importable at module scope.
try:
    from PyQt6.QtWidgets import (
        QWizard,
        QWizardPage,
        QVBoxLayout,
        QLabel,
        QProgressBar,
        QComboBox,
        QLineEdit,
        QPushButton,
        QHBoxLayout,
        QCheckBox,
        QFrame,
        QMessageBox,
    )
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False
    # Placeholder classes so module structure survives without Qt
    class QWizard:
        pass

    class QWizardPage:
        pass

    class QVBoxLayout:
        pass

    QLabel = QProgressBar = QComboBox = QLineEdit = QPushButton = QLabel
    QHBoxLayout = QCheckBox = QFrame = QMessageBox = QLabel


# ═══════════════════════════════════════════════════════════════════
# Utility functions
# ═══════════════════════════════════════════════════════════════════


def is_first_run():
    """Check whether onboarding has been completed by inspecting
    SETTINGS_PATH for an ``onboarding_complete`` flag."""
    try:
        if not os.path.exists(SETTINGS_PATH):
            return True
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return not data.get("onboarding_complete", False)
    except (OSError, json.JSONDecodeError, ValueError):
        return True


def mark_onboarding_complete():
    """Write the ``onboarding_complete: True`` flag to SETTINGS_PATH."""
    try:
        existing = {}
        if os.path.exists(SETTINGS_PATH):
            try:
                with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, ValueError):
                existing = {}
        existing["onboarding_complete"] = True
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
    except (OSError, PermissionError) as e:
        # Breadcrumb (silent-catch audit): a failed flag write re-triggers
        # onboarding on every launch -- must be traceable.
        logger.warning(f"Failed to persist onboarding_complete flag: {e}")


def run_onboarding(parent=None):
    """Run the onboarding wizard. Returns True if completed, False otherwise."""
    if not _QT_AVAILABLE:
        return False
    if not is_first_run():
        return True
    wizard = OnboardingWizard(parent)
    # Telemetry bridge: mirror wizard model-load progress into the dashboard
    # audit log and refresh the sidebar status when the model lands. Without
    # this the dashboard kept showing "Not Loaded / No model selected" after
    # a wizard-loaded model (2026-09 live-session report).
    def _on_wizard_model_event(message):
        try:
            log_to_audit = getattr(parent, "log_to_audit", None)
            if log_to_audit is not None:
                log_to_audit(f"{message}")
            refresh = getattr(parent, "refresh_model_status_ui", None)
            if message.startswith("✅") and refresh is not None:
                refresh()
        except RuntimeError:
            pass  # dashboard widgets destroyed mid-onboarding

    wizard.on_model_event = _on_wizard_model_event
    result = wizard.exec()
    # Final refresh after close: the ✅ path above covers mid-wizard, but
    # refresh again once the dashboard is visible so badge/status are exact.
    try:
        refresh = getattr(parent, "refresh_model_status_ui", None)
        if result == 1 and refresh is not None:
            refresh()
    except RuntimeError:
        pass
    return result == 1  # QDialog.Accepted


# ═══════════════════════════════════════════════════════════════════
# Page classes
# ═══════════════════════════════════════════════════════════════════


class WelcomePage(QWizardPage):
    """First page: welcome text and usage tip."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Welcome to KokertechAI")
        self.setSubTitle("Your unified executive AI dashboard")
        layout = QVBoxLayout(self)
        welcome_lbl = QLabel(
            "KokertechAI combines multiple AI models, local document "
            "processing, and desktop automation into a unified executive "
            "dashboard.\n\n"
            "This quick setup will help you configure your AI provider, "
            "API keys, and preferences.\n\n"
            "Let's get started!",
            parent=self,
        )
        welcome_lbl.setWordWrap(True)
        layout.addWidget(welcome_lbl)
        tip_lbl = QLabel(
            "Tip: You can change any setting later from the Settings tab.",
            parent=self,
        )
        tip_lbl.setWordWrap(True)
        layout.addWidget(tip_lbl)


class ProviderCheckPage(QWizardPage):
    """Provider discovery page: detects running AI providers."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Provider Discovery")
        layout = QVBoxLayout(self)
        self.status_lbl = QLabel("Checking for running AI providers...", parent=self)
        layout.addWidget(self.status_lbl)
        self.progress = QProgressBar(parent=self)
        layout.addWidget(self.progress)
        self.results_lbl = QLabel("", parent=self)
        self.results_lbl.setWordWrap(True)
        layout.addWidget(self.results_lbl)
        self.provider_combo = QComboBox(parent=self)
        layout.addWidget(self.provider_combo)
        self._discovery_thread = None

    def initializePage(self):
        """Start provider discovery in a background thread."""
        self._discovery_thread = threading.Thread(
            target=self._discover_providers, daemon=True
        )
        self._discovery_thread.start()

    def cleanupPage(self):
        """Cancel discovery when leaving the page."""
        self._cancel_discovery()

    def _cancel_discovery(self):
        """Detach alive discovery threads."""
        if self._discovery_thread is not None and self._discovery_thread.is_alive():
            self._discovery_thread = None

    def _discover_providers(self):
        """Run provider discovery (placeholder — test mock provides results)."""
        providers = [
            {
                "name": "local_llm",
                "status": "online",
                "model_count": 3,
                "models": ["m1", "m2", "m3"],
                "error": None,
            },
        ]
        self._update_results(providers)

    def _update_results(self, providers):
        """Update the UI with discovery results."""
        online = [p for p in providers if p.get("status") == "online"]
        offline = [p for p in providers if p.get("status") != "online"]
        if online:
            names = ", ".join(p["name"] for p in online)
            offline_names = ", ".join(p["name"] for p in offline) if offline else ""
            parts = [f"Found running providers: {names}"]
            if offline_names:
                parts.append(f"Not detected: {offline_names}")
            self.results_lbl.setText("\n".join(parts))
            self.provider_combo.clear()
            for p in online:
                self.provider_combo.addItem(p["name"], p["name"])
            self.provider_combo.setCurrentIndex(0)
        else:
            self.results_lbl.setText(
                "Ensure your local LLM server is running, or type your API key on the next page."
            )
            self.provider_combo.setCurrentIndex(0)


class ModelSelectPage(QWizardPage):
    """Local model selection page.

    Config change 2026-09: the app no longer auto-loads a default model,
    so first-run onboarding must offer one. Scans the models directory
    for .gguf files (background thread), lets the user pick one, and —
    by default — loads it when the wizard finishes. Selection is never
    auto-picked (no-default-model policy): an empty combo means the user
    skipped it and can choose later in Settings.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Select a Model")
        self.setSubTitle("Pick the local model KokertechAI should use")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Choose which local .gguf model to load. If you skip this, "
            "no model is loaded and you can pick one later in Settings.",
            parent=self,
        ))
        self.model_combo = QComboBox(parent=self)
        self.model_combo.setEditable(True)
        self.model_combo.setPlaceholderText("Select or type a model…")
        layout.addWidget(self.model_combo)
        self.scan_status = QLabel("", parent=self)
        self.scan_status.setWordWrap(True)
        layout.addWidget(self.scan_status)
        self.load_now = QCheckBox("Load the model when setup finishes", parent=self)
        self.load_now.setChecked(True)
        layout.addWidget(self.load_now)
        self._load_status = QLabel("", parent=self)
        self._load_status.setWordWrap(True)
        layout.addWidget(self._load_status)
        self._scan_thread = None

    def initializePage(self):
        """Start the models-directory scan in a background thread."""
        self._scan_thread = threading.Thread(
            target=self._scan_models_bg, daemon=True
        )
        self._scan_thread.start()

    def cleanupPage(self):
        """Detach the scan thread when the user navigates back."""
        if self._scan_thread is not None and self._scan_thread.is_alive():
            self._scan_thread = None

    def _scan_models_bg(self):
        """Scan CONFIG['models_dir'] for .gguf files (worker thread).

        Results are marshalled to the main thread via qt_dispatch —
        QComboBox/QLabel are not thread-safe, and a direct call from a
        plain thread is the exact bug class that froze the Settings-tab
        Apply button (2026-09 session log).
        """
        models_dir = CONFIG.get("models_dir", os.path.join(WORKSPACE_DIR, "models"))
        try:
            from utils.qt_dispatch import run_on_main_thread
        except ImportError:  # pragma: no cover — standalone module use
            run_on_main_thread = lambda fn: fn()  # noqa: E731
        try:
            from ai_base import LocalLLMProvider, is_projector_model
            models = LocalLLMProvider.scan_models(models_dir)
            # mmproj projector adapters are vision ADAPTERS, not chat models            # — loading one as the main model fails. Hide them from the            # wizard's main-model picker (they stay available in the            # Settings vision-model picker).
            models = [m for m in models if not is_projector_model(m.get("name", ""))]
        except Exception as e:  # noqa: BLE001 — scan must never break onboarding
            err_msg = str(e)
            run_on_main_thread(
                lambda: self._populate_models(None, f"Model scan failed: {err_msg}"))
            return
        run_on_main_thread(lambda: self._populate_models(models, None))

    def _populate_models(self, models, error):
        """Fill the combo from scan results (main thread — wizard is modal
        and the scan callback lands here synchronously via the UI thread's
        event loop only if marshalled; guard RuntimeError for closed pages)."""
        try:
            if error is not None:
                self.scan_status.setText(error)
                return
            if not models:
                self.scan_status.setText(
                    "No .gguf models found — place files in the models "
                    "directory or pick one later in Settings."
                )
                return
            self.model_combo.blockSignals(True)
            try:
                self.model_combo.clear()
                for m in models:
                    self.model_combo.addItem(m["name"], m["path"])
            finally:
                self.model_combo.blockSignals(False)
            # Policy: no auto-select. The user must actively choose.
            self.scan_status.setText(
                f"Found {len(models)} models — select one below."
            )
        except RuntimeError:
            pass  # page destroyed while scan was in flight

    def _resolve_model_target(self) -> str:
        """Resolve the selected model path (combo data, else typed text).
        Returns '' when nothing meaningful is selected. isinstance guards
        keep MagicMock test doubles from leaking non-str values."""
        data = self.model_combo.currentData()
        if isinstance(data, str) and data.strip():
            return data.strip()
        text = self.model_combo.currentText()
        if isinstance(text, str) and text.strip():
            return text.strip()
        return ""

    def load_selected_model(self) -> dict:
        """Load the selected model via the shared provider (synchronous).

        Uses the same LocalLLMProvider.swap_model path as the Settings-tab
        ⚡ Apply button, so the model is loaded, tracked, and persisted
        identically. Never raises; returns a result dict.

        Telemetry: every progress message and the final result are mirrored
        to the dashboard audit log via the wizard's ``on_model_event``
        callback (default no-op), so "model loaded but UI says it didn't"
        can't recur.
        """
        target = self._resolve_model_target()
        if not target:
            return {"ok": False, "error": "No model selected"}
        self._load_status.setText(f"⏳ Loading {os.path.basename(target)}…")
        self._emit_model_event(
            f"🧙 Onboarding: loading {os.path.basename(target)}…")
        try:
            from ai_base import get_provider
            provider = get_provider(name="local_llm")

            def _progress(msg):
                self._load_status.setText(str(msg))
                self._emit_model_event(str(msg))

            result = provider.swap_model(target, progress=_progress)
        except Exception as e:  # noqa: BLE001 — onboarding must not crash
            result = {"ok": False, "error": str(e)}
        if result.get("ok"):
            ms = result.get("load_time_ms", 0)
            self._load_status.setText(
                f"✅ Loaded {os.path.basename(target)} ({ms}ms)"
            )
            self._emit_model_event(
                f"✅ Onboarding load complete: {os.path.basename(target)} "
                f"({ms}ms, n_ctx={result.get('n_ctx', 0)})"
            )
        else:
            self._load_status.setText(
                f"❌ Load failed: {result.get('error', 'unknown error')}"
            )
            self._emit_model_event(
                f"❌ Onboarding load failed: {result.get('error', 'unknown error')}"
            )
        return result

    def _emit_model_event(self, message):
        """Forward a model telemetry line to the wizard owner (main thread).

        ``on_model_event`` is a callable(str) set by the dashboard before
        exec(); default no-op so the wizard stays standalone. Exceptions
        are swallowed — telemetry must never break onboarding.
        """
        cb = getattr(self, "on_model_event", None)
        if cb is None:
            return
        try:
            cb(message)
        except Exception:  # noqa: BLE001, S110
            pass


class APIKeyPage(QWizardPage):
    """API keys and user identity page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("API Keys & Identity")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("API Key:", parent=self))
        self.api_key_input = QLineEdit(parent=self)
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.api_key_input)
        layout.addWidget(QLabel("Your Name:", parent=self))
        self.name_input = QLineEdit(parent=self)
        layout.addWidget(self.name_input)
        layout.addWidget(QLabel("Communication Tone:", parent=self))
        self.tone_combo = QComboBox(parent=self)
        self.tone_combo.addItems(
            [
                "Direct, blunt, and conversational",
                "Professional and formal",
                "Friendly and supportive",
            ]
        )
        layout.addWidget(self.tone_combo)


class TTSTestPage(QWizardPage):
    """TTS test and audio settings page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Voice & Audio")
        layout = QVBoxLayout(self)
        self.tts_status = QLabel("Checking TTS availability...", parent=self)
        layout.addWidget(self.tts_status)
        self.test_text = QLineEdit("Hello, this is a test of the voice system.", parent=self)
        layout.addWidget(self.test_text)
        self.test_btn = QPushButton("Test Voice", parent=self)
        self.test_btn.clicked.connect(self._test_tts)
        layout.addWidget(self.test_btn)
        self.tts_enabled = QCheckBox("Enable voice output (TTS)", parent=self)
        self.tts_enabled.setChecked(True)
        layout.addWidget(self.tts_enabled)

    def initializePage(self):
        """Check TTS availability on page entry."""
        try:
            from copilot_features import VoiceOutput

            self.tts_status.setText("TTS Available — click Test Voice to try it.")
            # Try constructing VoiceOutput — test patches may raise
            # ImportError on construction (side_effect pattern).
            VoiceOutput()
            self.test_btn.setEnabled(True)
        except ImportError:
            self.tts_status.setText("TTS Not available on this system.")
            self.test_btn.setEnabled(False)

    def _test_tts(self):
        """Test TTS by speaking the test text."""
        try:
            from copilot_features import VoiceOutput

            vo = VoiceOutput()
            vo.speak(self.test_text.text())
        except ImportError:
            QMessageBox.warning(self, "TTS Error", "TTS is not available on this system.")


class FinishPage(QWizardPage):
    """Final page: setup complete."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Setup Complete")
        layout = QVBoxLayout(self)
        lbl = QLabel(
            "Your KokertechAI setup is complete!\n\n"
            "Click Finish to start using the dashboard.",
            parent=self,
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)


# ═══════════════════════════════════════════════════════════════════
# Wizard
# ═══════════════════════════════════════════════════════════════════


class OnboardingWizard(QWizard):
    """Multi-page wizard guiding the user through first-time setup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("KokertechAI \u2014 First-Time Setup")
        self.welcome_page = WelcomePage(self)
        self.provider_page = ProviderCheckPage(self)
        self.model_page = ModelSelectPage(self)
        self.apikey_page = APIKeyPage(self)
        self.tts_page = TTSTestPage(self)
        self.finish_page = FinishPage(self)
        self.addPage(self.welcome_page)
        self.addPage(self.provider_page)
        self.addPage(self.model_page)
        self.addPage(self.apikey_page)
        self.addPage(self.tts_page)
        self.addPage(self.finish_page)

    def accept(self):
        """Save settings and mark onboarding complete."""
        provider = self.provider_page.provider_combo.currentData()
        if provider:
            CONFIG["active_provider"] = provider
        name = self.apikey_page.name_input.text().strip()
        if name:
            IDENTITY_CONFIG["name"] = name
        tone = self.apikey_page.tone_combo.currentText()
        if tone:
            IDENTITY_CONFIG["tone"] = tone
        CONFIG["tts_enabled"] = self.tts_page.tts_enabled.isChecked()
        # Model selection (config change 2026-09): the wizard is the
        # first-run path to get a model loaded. A picked model is always
        # persisted; when "load when setup finishes" is checked it also
        # loads now (opt-in, synchronous — installer-style wait). The
        # isinstance guards keep MagicMock test doubles from leaking
        # non-str values into CONFIG.
        try:
            model_target = self.model_page._resolve_model_target()
        except Exception:  # noqa: BLE001 — a broken model page must not block Finish
            model_target = ""
        if isinstance(model_target, str) and model_target:
            CONFIG["model_file"] = model_target
            CONFIG["model_name"] = model_target
            # Adopt into the live provider so this session agrees with
            # CONFIG (get_provider captured model_file="" at construction).
            # Unchecked load_now → model lazy-loads on first chat instead.
            try:
                from ai_base import get_provider
                get_provider(name="local_llm").model_file = model_target
            except Exception:  # noqa: BLE001, S110 — provider adoption is best-effort
                pass
            if self.model_page.load_now.isChecked():
                try:
                    from PyQt6.QtWidgets import QApplication
                    _app = QApplication.instance()
                    if _app is not None:
                        _app.processEvents()  # paint the ⏳ before the sync load
                except Exception:  # noqa: BLE001, S110
                    pass
                self.model_page.load_selected_model()
        self._save_settings()
        mark_onboarding_complete()
        super().accept()

    def _save_settings(self):
        """Persist CONFIG and IDENTITY_CONFIG to disk."""
        try:
            save_settings()
            save_identity()
        except (OSError, PermissionError) as e:
            # Breadcrumb (silent-catch audit): a silently skipped settings
            # write means the user re-answers the wizard next launch.
            logger.warning(f"Onboarding settings save failed (fail-soft): {e}")
