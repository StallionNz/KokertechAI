"""Unit tests for onboarding_wizard.py is_first_run, mark_onboarding_complete, gui pages, and run_onboarding."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock, PropertyMock

# QApplication provided by conftest.py (session-scoped qapp fixture)

# =============================================================================
# Tests - is_first_run
# =============================================================================


class TestIsFirstRun(unittest.TestCase):
    """is_first_run() checks SETTINGS_PATH for onboarding_complete flag."""

    @patch("os.path.exists", return_value=False)
    def test_returns_true_when_no_settings_file(self, mock_exists):
        from onboarding_wizard import is_first_run
        self.assertTrue(is_first_run())

    @patch("os.path.exists", return_value=True)
    def test_returns_true_when_flag_missing(self, mock_exists):
        from onboarding_wizard import is_first_run
        tmp = tempfile.mktemp(suffix=".json")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"some_other_key": True}, f)
            with patch("onboarding_wizard.SETTINGS_PATH", tmp):
                self.assertTrue(is_first_run())
        finally:
            os.remove(tmp)

    @patch("os.path.exists", return_value=True)
    def test_returns_false_when_flag_present(self, mock_exists):
        from onboarding_wizard import is_first_run
        tmp = tempfile.mktemp(suffix=".json")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"onboarding_complete": True}, f)
            with patch("onboarding_wizard.SETTINGS_PATH", tmp):
                self.assertFalse(is_first_run())
        finally:
            os.remove(tmp)

    @patch("os.path.exists", return_value=True)
    def test_returns_true_on_corrupt_json(self, mock_exists):
        from onboarding_wizard import is_first_run
        tmp = tempfile.mktemp(suffix=".json")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("{corrupt json")
            with patch("onboarding_wizard.SETTINGS_PATH", tmp):
                self.assertTrue(is_first_run())
        finally:
            os.remove(tmp)

# =============================================================================
# Tests - mark_onboarding_complete
# =============================================================================

class TestMarkOnboardingComplete(unittest.TestCase):
    """mark_onboarding_complete() writes the completion flag."""

    def test_creates_file_with_flag(self):
        from onboarding_wizard import mark_onboarding_complete
        tmp = tempfile.mktemp(suffix=".json")
        try:
            with patch("onboarding_wizard.SETTINGS_PATH", tmp):
                with patch("os.path.exists", return_value=False):
                    mark_onboarding_complete()
            with open(tmp, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertTrue(data.get("onboarding_complete"))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_updates_existing_file(self):
        from onboarding_wizard import mark_onboarding_complete
        tmp = tempfile.mktemp(suffix=".json")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"existing": "value"}, f)
            with patch("onboarding_wizard.SETTINGS_PATH", tmp):
                with patch("os.path.exists", return_value=True):
                    mark_onboarding_complete()
            with open(tmp, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.assertTrue(data.get("onboarding_complete"))
            self.assertEqual(data.get("existing"), "value")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_handles_exception_gracefully(self):
        from onboarding_wizard import mark_onboarding_complete
        with patch("builtins.open", side_effect=PermissionError("denied")):
            mark_onboarding_complete()

# =============================================================================
# Tests - run_onboarding
# =============================================================================

class TestRunOnboarding(unittest.TestCase):
    """run_onboarding() orchestrates the wizard flow."""

    @patch("onboarding_wizard._QT_AVAILABLE", False)
    def test_returns_false_when_qt_unavailable(self):
        from onboarding_wizard import run_onboarding
        self.assertFalse(run_onboarding())

    @patch("onboarding_wizard.is_first_run", return_value=False)
    def test_returns_true_when_already_complete(self, mock_first_run):
        from onboarding_wizard import run_onboarding
        self.assertTrue(run_onboarding())

    @patch("onboarding_wizard._QT_AVAILABLE", True)
    @patch("onboarding_wizard.is_first_run", return_value=True)
    @patch("onboarding_wizard.OnboardingWizard")
    def test_creates_wizard_when_needed(self, mock_wizard_cls, mock_first_run):
        mock_wizard = MagicMock()
        mock_wizard.exec.return_value = 1  # QDialog.Accepted
        mock_wizard_cls.return_value = mock_wizard
        from onboarding_wizard import run_onboarding
        result = run_onboarding()
        mock_wizard_cls.assert_called_once()
        self.assertTrue(result)

# =============================================================================
# Tests - WelcomePage
# =============================================================================

class TestWelcomePage(unittest.TestCase):
    """WelcomePage: first page with welcome text and tip."""

    def setUp(self):
        # Patch QWizardPage base class for isolated testing
        self.qt_patches = [
            # QWizardPage.__init__ runs real to avoid RuntimeError
            patch("onboarding_wizard.QVBoxLayout", return_value=MagicMock()),
            patch("onboarding_wizard.QLabel", return_value=MagicMock()),
        ]
        for p in self.qt_patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.qt_patches):
            p.stop()

    def test_init_sets_title_and_subtitle(self):
        from onboarding_wizard import WelcomePage
        page = WelcomePage()
        self.assertEqual(page.title(), "Welcome to KokertechAI")
        self.assertEqual(page.subTitle(), "Your unified executive AI dashboard")

    def test_init_creates_welcome_label(self):
        from onboarding_wizard import WelcomePage
        page = WelcomePage()
        from onboarding_wizard import QLabel
        self.assertGreaterEqual(QLabel.call_count, 2)
        calls = QLabel.call_args_list
        first_text = str(calls[0])
        second_text = str(calls[1])
        self.assertIn("KokertechAI combines", first_text)
        self.assertIn("Tip", second_text)

# =============================================================================
# Tests - ProviderCheckPage
# =============================================================================

class TestProviderCheckPage(unittest.TestCase):
    """ProviderCheckPage: discovers running AI providers."""

    def setUp(self):
        self.qt_patches = [
            # QWizardPage.__init__ runs real to avoid RuntimeError
            patch("onboarding_wizard.QVBoxLayout", return_value=MagicMock()),
            patch("onboarding_wizard.QLabel", return_value=MagicMock()),
            patch("onboarding_wizard.QProgressBar", return_value=MagicMock()),
            patch("onboarding_wizard.QComboBox", return_value=MagicMock()),
        ]
        for p in self.qt_patches:
            p.start()
        # Configure combo mock so findData returns a valid int
        from onboarding_wizard import QComboBox
        QComboBox.return_value.findData.return_value = 0

    def tearDown(self):
        for p in reversed(self.qt_patches):
            p.stop()

    def test_init_creates_widgets(self):
        from onboarding_wizard import ProviderCheckPage
        page = ProviderCheckPage()
        self.assertEqual(page.title(), "Provider Discovery")
        self.assertIsNotNone(page.provider_combo)
        self.assertIsNotNone(page.status_lbl)
        self.assertIsNotNone(page.results_lbl)
        self.assertIsNotNone(page.progress)

    def test_init_sets_title_and_subtitle(self):
        from onboarding_wizard import ProviderCheckPage
        page = ProviderCheckPage()
        self.assertEqual(page.title(), "Provider Discovery")

    def test_initializePage_starts_discovery(self):
        from onboarding_wizard import ProviderCheckPage
        page = ProviderCheckPage()
        page.initializePage()
        self.assertIsNotNone(page._discovery_thread)
        self.assertTrue(page._discovery_thread.daemon)

    def test_cleanupPage_cancels_discovery(self):
        from onboarding_wizard import ProviderCheckPage
        page = ProviderCheckPage()
        page._discovery_thread = MagicMock()
        page._discovery_thread.is_alive.return_value = True
        page.cleanupPage()
        self.assertIsNone(page._discovery_thread)

    def test_cleanupPage_no_thread_no_crash(self):
        from onboarding_wizard import ProviderCheckPage
        page = ProviderCheckPage()
        page._discovery_thread = None
        page.cleanupPage()  # should not raise

    def test_cancel_discovery_detaches_alive_thread(self):
        from onboarding_wizard import ProviderCheckPage
        page = ProviderCheckPage()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        page._discovery_thread = mock_thread
        page._cancel_discovery()
        self.assertIsNone(page._discovery_thread)

    def test_cancel_discovery_dead_thread_unchanged(self):
        from onboarding_wizard import ProviderCheckPage
        page = ProviderCheckPage()
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        page._discovery_thread = mock_thread
        page._cancel_discovery()
        self.assertIsNotNone(page._discovery_thread)

    def test_update_results_with_online_providers(self):
        from onboarding_wizard import ProviderCheckPage
        page = ProviderCheckPage()
        providers = [
            {"name": "local_llm", "status": "online", "model_count": 3, "models": ["m1", "m2", "m3"], "error": None},
            {"name": "llama.cpp", "status": "offline", "model_count": 0, "models": [], "error": "Connection refused"},
        ]
        page._update_results(providers)
        text = page.results_lbl.setText.call_args[0][0]
        self.assertIn("Found running providers", text)
        self.assertIn("local_llm", text)
        self.assertIn("Not detected", text)

    def test_update_results_no_online_providers(self):
        from onboarding_wizard import ProviderCheckPage
        page = ProviderCheckPage()
        providers = [
            {"name": "local_llm", "status": "offline", "model_count": 0, "models": [], "error": "refused"},
        ]
        page._update_results(providers)
        text = page.results_lbl.setText.call_args[0][0]
        self.assertIn("Ensure your local LLM server is running", text)
        # Should default to index 0 (only option)
        page.provider_combo.setCurrentIndex.assert_called_with(0)

# =============================================================================
# Tests - ModelSelectPage
# =============================================================================

class TestModelSelectPage(unittest.TestCase):
    """ModelSelectPage: local model picker (2026-09 no-default-model policy).

    The app no longer auto-loads a model at startup, so onboarding offers
    the selection. Policy pins:
    * scan results populate the combo but NEVER auto-select an item
    * empty selection is valid (user may skip; Settings handles it later)
    * load uses the same LocalLLMProvider.swap_model path as ⚡ Apply
    """

    def setUp(self):
        self.qt_patches = [
            # QWizardPage.__init__ runs real to avoid RuntimeError
            patch("onboarding_wizard.QVBoxLayout", return_value=MagicMock()),
            patch("onboarding_wizard.QLabel", return_value=MagicMock()),
            patch("onboarding_wizard.QComboBox", return_value=MagicMock()),
            patch("onboarding_wizard.QCheckBox", return_value=MagicMock()),
        ]
        for p in self.qt_patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.qt_patches):
            p.stop()

    def _page(self):
        from onboarding_wizard import ModelSelectPage
        return ModelSelectPage()

    def test_init_sets_title(self):
        page = self._page()
        self.assertEqual(page.title(), "Select a Model")

    def test_init_load_now_defaults_checked(self):
        page = self._page()
        page.load_now.setChecked.assert_called_once_with(True)

    def test_initializePage_starts_scan_thread(self):
        page = self._page()
        with patch("onboarding_wizard.threading.Thread") as mock_thread:
            page.initializePage()
        mock_thread.assert_called_once()
        self.assertIsNotNone(page._scan_thread)

    def test_cleanupPage_detaches_alive_thread(self):
        page = self._page()
        fake_thread = MagicMock()
        fake_thread.is_alive.return_value = True
        page._scan_thread = fake_thread
        page.cleanupPage()
        self.assertIsNone(page._scan_thread)

    def test_populate_models_fills_combo_without_autoselect(self):
        page = self._page()
        models = [
            {"name": "a.gguf", "path": "C:/m/a.gguf"},
            {"name": "b.gguf", "path": "C:/m/b.gguf"},
        ]
        page._populate_models(models, None)
        self.assertEqual(page.model_combo.addItem.call_count, 2)
        # Policy: no auto-pick — setCurrentIndex is never touched
        page.model_combo.setCurrentIndex.assert_not_called()
        status = page.scan_status.setText.call_args[0][0]
        self.assertIn("2 models", status)

    def test_populate_models_empty_shows_hint(self):
        page = self._page()
        page._populate_models([], None)
        status = page.scan_status.setText.call_args[0][0]
        self.assertIn("No .gguf models found", status)
        page.model_combo.clear.assert_not_called()

    def test_populate_models_error_shows_message(self):
        page = self._page()
        page._populate_models(None, "scan boom")
        page.scan_status.setText.assert_called_once_with("scan boom")

    def test_populate_models_survives_destroyed_page(self):
        page = self._page()
        page.scan_status.setText.side_effect = RuntimeError("underlying C/C++ object deleted")
        page._populate_models([], None)  # must not raise

    def test_resolve_target_prefers_item_data(self):
        page = self._page()
        page.model_combo.currentData.return_value = "C:/m/a.gguf"
        page.model_combo.currentText.return_value = "a.gguf"
        self.assertEqual(page._resolve_model_target(), "C:/m/a.gguf")

    def test_resolve_target_falls_back_to_typed_text(self):
        page = self._page()
        page.model_combo.currentData.return_value = None
        page.model_combo.currentText.return_value = "typed-model.gguf"
        self.assertEqual(page._resolve_model_target(), "typed-model.gguf")

    def test_resolve_target_empty_when_nothing_selected(self):
        page = self._page()
        page.model_combo.currentData.return_value = None
        page.model_combo.currentText.return_value = "   "
        self.assertEqual(page._resolve_model_target(), "")

    def test_load_selected_model_no_selection_refuses(self):
        page = self._page()
        page.model_combo.currentData.return_value = None
        page.model_combo.currentText.return_value = ""
        result = page.load_selected_model()
        self.assertFalse(result.get("ok"))
        self.assertIn("No model selected", result.get("error", ""))

    @patch("ai_base.get_provider")
    def test_load_selected_model_success_reports_status(self, mock_get):
        page = self._page()
        provider = mock_get.return_value
        provider.swap_model.return_value = {"ok": True, "load_time_ms": 123, "n_ctx": 4096}
        page.model_combo.currentData.return_value = "C:/m/a.gguf"
        result = page.load_selected_model()
        self.assertTrue(result["ok"])
        provider.swap_model.assert_called_once()
        status = page._load_status.setText.call_args[0][0]
        self.assertIn("\u2705", status)
        self.assertIn("123", status)

    @patch("ai_base.get_provider")
    def test_load_selected_model_failure_reports_error(self, mock_get):
        page = self._page()
        mock_get.return_value.swap_model.return_value = {"ok": False, "error": "boom"}
        page.model_combo.currentData.return_value = "C:/m/a.gguf"
        result = page.load_selected_model()
        self.assertFalse(result["ok"])
        status = page._load_status.setText.call_args[0][0]
        self.assertIn("\u274c", status)
        self.assertIn("boom", status)

    @patch("ai_base.get_provider", side_effect=RuntimeError("no provider"))
    def test_load_selected_model_exception_never_raises(self, mock_get):
        page = self._page()
        page.model_combo.currentData.return_value = "C:/m/a.gguf"
        result = page.load_selected_model()
        self.assertFalse(result.get("ok"))

    def test_scan_models_bg_reports_scan_errors(self):
        page = self._page()
        with patch("ai_base.LocalLLMProvider") as mock_cls:
            mock_cls.scan_models.side_effect = OSError("dir gone")
            page._scan_models_bg()
        status = page.scan_status.setText.call_args[0][0]
        self.assertIn("scan failed", status)

    def test_populate_models_renders_what_it_is_given(self):
        """_populate_models is a faithful renderer — filtering happens in the
        SCAN layer (_scan_models_bg), not here. Pinned so the two layers
        don't silently swap responsibilities."""
        page = self._page()
        models = [
            {"name": "a.gguf", "path": "C:/m/a.gguf"},
            {"name": "mmproj-a-F16.gguf", "path": "C:/m/mmproj-a-F16.gguf"},
        ]
        page._populate_models(models, None)
        added_names = [c.args[0] for c in page.model_combo.addItem.call_args_list]
        self.assertEqual(added_names, ["a.gguf", "mmproj-a-F16.gguf"])

    def test_scan_models_bg_filters_projectors(self):
        """End-to-end through the scan path: _scan_models_bg drops mmproj
        entries before populating (real is_projector_model, scanned models
        mocked)."""
        page = self._page()
        scanned = [
            {"name": "chat-Q4.gguf", "path": "C:/m/chat-Q4.gguf"},
            {"name": "mmproj-chat-F16.gguf", "path": "C:/m/mmproj-chat-F16.gguf"},
        ]
        with patch("ai_base.LocalLLMProvider") as mock_cls:
            mock_cls.scan_models.return_value = scanned
            page._scan_models_bg()
        added_names = [c.args[0] for c in page.model_combo.addItem.call_args_list]
        self.assertEqual(added_names, ["chat-Q4.gguf"])

    def test_scan_models_bg_filters_real_inventory_projectors(self):
        """With the real models/ inventory shape (Ministral + Qwen3-VL chat
        models, each shipping an mmproj-* adapter), only the 2 chat models
        reach the wizard combo."""
        page = self._page()
        scanned = [
            {"name": "Ministral-3-3B-Instruct-2512-Q4_K_M.gguf",
             "path": "C:/m/Ministral-3-3B-Instruct-2512-GGUF/Ministral-3-3B-Instruct-2512-Q4_K_M.gguf"},
            {"name": "mmproj-Ministral-3-3B-Instruct-2512-F16.gguf",
             "path": "C:/m/Ministral-3-3B-Instruct-2512-GGUF/mmproj-Ministral-3-3B-Instruct-2512-F16.gguf"},
            {"name": "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
             "path": "C:/m/Qwen3-VL-4B-Instruct-GGUF/Qwen3-VL-4B-Instruct-Q4_K_M.gguf"},
            {"name": "mmproj-Qwen3-VL-4B-Instruct-F16.gguf",
             "path": "C:/m/Qwen3-VL-4B-Instruct-GGUF/mmproj-Qwen3-VL-4B-Instruct-F16.gguf"},
        ]
        with patch("ai_base.LocalLLMProvider") as mock_cls:
            mock_cls.scan_models.return_value = scanned
            page._scan_models_bg()
        added_names = [c.args[0] for c in page.model_combo.addItem.call_args_list]
        self.assertEqual(added_names, [
            "Ministral-3-3B-Instruct-2512-Q4_K_M.gguf",
            "Qwen3-VL-4B-Instruct-Q4_K_M.gguf",
        ])
        status = page.scan_status.setText.call_args[0][0]
        self.assertIn("2 models", status)


# =============================================================================
# Tests - APIKeyPage
# =============================================================================

class TestAPIKeyPage(unittest.TestCase):
    """APIKeyPage: API keys and user identity."""

    def setUp(self):
        self.qt_patches = [
            # QWizardPage.__init__ runs real to avoid RuntimeError
            patch("onboarding_wizard.QVBoxLayout", return_value=MagicMock()),
            patch("onboarding_wizard.QLabel", return_value=MagicMock()),
            patch("onboarding_wizard.QLineEdit", return_value=MagicMock()),
            patch("onboarding_wizard.QPushButton", return_value=MagicMock()),
            patch("onboarding_wizard.QComboBox", return_value=MagicMock()),
            patch("onboarding_wizard.QFrame", return_value=MagicMock()),
        ]
        for p in self.qt_patches:
            p.start()
        # Configure combo mock so findText returns a valid int (not MagicMock)
        from onboarding_wizard import QComboBox
        QComboBox.return_value.findText.return_value = 0

    def tearDown(self):
        for p in reversed(self.qt_patches):
            p.stop()

    def test_init_sets_title(self):
        from onboarding_wizard import APIKeyPage
        page = APIKeyPage()
        self.assertEqual(page.title(), "API Keys & Identity")

    def test_init_creates_input_fields(self):
        from onboarding_wizard import APIKeyPage
        page = APIKeyPage()
        self.assertIsNotNone(page.api_key_input)
        self.assertIsNotNone(page.name_input)
        self.assertIsNotNone(page.tone_combo)

# =============================================================================
# Tests - TTSTestPage
# =============================================================================

class TestTTSTestPage(unittest.TestCase):
    """TTSTestPage: TTS test and audio settings."""

    def setUp(self):
        self.qt_patches = [
            # QWizardPage.__init__ runs real to avoid RuntimeError
            patch("onboarding_wizard.QVBoxLayout", return_value=MagicMock()),
            patch("onboarding_wizard.QHBoxLayout", return_value=MagicMock()),
            patch("onboarding_wizard.QLabel", return_value=MagicMock()),
            patch("onboarding_wizard.QLineEdit", return_value=MagicMock()),
            patch("onboarding_wizard.QPushButton", return_value=MagicMock()),
            patch("onboarding_wizard.QCheckBox", return_value=MagicMock()),
            patch("onboarding_wizard.QMessageBox", return_value=MagicMock()),
        ]
        for p in self.qt_patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.qt_patches):
            p.stop()

    def test_init_sets_title(self):
        from onboarding_wizard import TTSTestPage
        page = TTSTestPage()
        self.assertEqual(page.title(), "Voice & Audio")

    def test_init_creates_widgets(self):
        from onboarding_wizard import TTSTestPage
        page = TTSTestPage()
        self.assertIsNotNone(page.tts_status)
        self.assertIsNotNone(page.test_text)
        self.assertIsNotNone(page.test_btn)
        self.assertIsNotNone(page.tts_enabled)

    @patch("copilot_features.VoiceOutput")
    def test_initializePage_tts_available(self, mock_vo):
        from onboarding_wizard import TTSTestPage
        page = TTSTestPage()
        page.initializePage()
        self.assertIn("Available", page.tts_status.setText.call_args[0][0])
        self.assertTrue(page.test_btn.setEnabled.called)

    @patch("copilot_features.VoiceOutput", side_effect=ImportError("no TTS"))
    def test_initializePage_tts_unavailable(self, mock_vo):
        from onboarding_wizard import TTSTestPage
        page = TTSTestPage()
        page.initializePage()
        self.assertIn("Not available", page.tts_status.setText.call_args[0][0])
        self.assertTrue(page.test_btn.setEnabled.called)
        page.test_btn.setEnabled.assert_called_once_with(False)

    @patch("copilot_features.VoiceOutput")
    def test_test_tts_success(self, mock_vo):
        from onboarding_wizard import TTSTestPage
        page = TTSTestPage()
        page.test_text.text.return_value = "Hello"
        page._test_tts()
        mock_vo.return_value.speak.assert_called_once_with("Hello")

    @patch("copilot_features.VoiceOutput", side_effect=ImportError("no TTS"))
    def test_test_tts_error(self, mock_vo):
        from onboarding_wizard import TTSTestPage, QMessageBox
        page = TTSTestPage()
        page._test_tts()
        QMessageBox.warning.assert_called()

# =============================================================================
# Tests - OnboardingWizard
# =============================================================================

class TestOnboardingWizard(unittest.TestCase):
    """OnboardingWizard: multi-page wizard with accept/save logic."""

    def setUp(self):
        self.qt_patches = [
            # QWizard.__init__ runs real to avoid RuntimeError
            patch("onboarding_wizard.QWizard.addPage", return_value=None),
            
            patch("onboarding_wizard.QWizard.DialogCode", create=True),
            patch("onboarding_wizard.QWizard.accept", return_value=None),
            patch("onboarding_wizard.WelcomePage", return_value=MagicMock()),
            patch("onboarding_wizard.ProviderCheckPage", return_value=MagicMock()),
            patch("onboarding_wizard.ModelSelectPage", return_value=MagicMock()),
            patch("onboarding_wizard.APIKeyPage", return_value=MagicMock()),
            patch("onboarding_wizard.TTSTestPage", return_value=MagicMock()),
            patch("onboarding_wizard.FinishPage", return_value=MagicMock()),
        ]
        for p in self.qt_patches:
            p.start()
        from onboarding_wizard import OnboardingWizard
        self.wizard = OnboardingWizard()

    def tearDown(self):
        for p in reversed(self.qt_patches):
            p.stop()

    def test_init_sets_window_title(self):
        self.assertEqual(self.wizard.windowTitle(), "KokertechAI \u2014 First-Time Setup")

    def test_init_creates_all_pages(self):
        from onboarding_wizard import WelcomePage, ProviderCheckPage, ModelSelectPage, APIKeyPage, TTSTestPage, FinishPage
        WelcomePage.assert_called_once_with(self.wizard)
        ProviderCheckPage.assert_called_once_with(self.wizard)
        ModelSelectPage.assert_called_once_with(self.wizard)
        APIKeyPage.assert_called_once_with(self.wizard)
        TTSTestPage.assert_called_once_with(self.wizard)
        FinishPage.assert_called_once_with(self.wizard)

    def test_init_adds_all_pages(self):
        from onboarding_wizard import QWizard
        self.assertEqual(QWizard.addPage.call_count, 6)

    def test_accept_saves_provider(self):
        self.wizard.provider_page.provider_combo.currentData.return_value = "local_llm"
        self.wizard.apikey_page.name_input.text.return_value.strip.return_value = ""
        self.wizard.tts_page.tts_enabled.isChecked.return_value = True
        with patch.object(self.wizard, "_save_settings") as mock_save:
            with patch("onboarding_wizard.mark_onboarding_complete") as mock_mark:
                self.wizard.accept()
        from config import CONFIG
        self.assertEqual(CONFIG.get("active_provider"), "local_llm")
        mock_save.assert_called_once()
        mock_mark.assert_called_once()

    def test_accept_saves_identity(self):
        self.wizard.provider_page.provider_combo.currentData.return_value = "local_llm"
        self.wizard.apikey_page.name_input.text.return_value.strip.return_value = "Jacques"
        self.wizard.apikey_page.tone_combo.currentText.return_value = "Direct, blunt, and conversational"
        self.wizard.tts_page.tts_enabled.isChecked.return_value = True
        with patch.object(self.wizard, "_save_settings") as mock_save:
            with patch("onboarding_wizard.mark_onboarding_complete"):
                self.wizard.accept()
        from config import IDENTITY_CONFIG
        self.assertEqual(IDENTITY_CONFIG.get("name"), "Jacques")
        self.assertEqual(IDENTITY_CONFIG.get("tone"), "Direct, blunt, and conversational")

    def test_accept_saves_tts_setting(self):
        self.wizard.provider_page.provider_combo.currentData.return_value = "local_llm"
        self.wizard.apikey_page.name_input.text.return_value.strip.return_value = ""
        self.wizard.tts_page.tts_enabled.isChecked.return_value = False
        with patch.object(self.wizard, "_save_settings") as mock_save:
            with patch("onboarding_wizard.mark_onboarding_complete"):
                self.wizard.accept()
        from config import CONFIG
        self.assertFalse(CONFIG.get("tts_enabled"))

    def test_accept_persists_model_and_adopts_provider(self):
        """A picked model is persisted to CONFIG and adopted by the live
        provider even when load-now is off (lazy-loads on first chat)."""
        self.wizard.model_page._resolve_model_target.return_value = "C:/m/a.gguf"
        self.wizard.model_page.load_now.isChecked.return_value = False
        from config import CONFIG
        with patch.object(self.wizard, "_save_settings"):
            with patch("onboarding_wizard.mark_onboarding_complete"):
                with patch("ai_base.get_provider") as mock_get:
                    with patch.dict("onboarding_wizard.CONFIG", {}, clear=True):
                        self.wizard.accept()
                        # Assert INSIDE the block: patch.dict restores the
                        # dict on exit, so post-block reads see the original.
                        self.assertEqual(CONFIG.get("model_file"), "C:/m/a.gguf")
                        self.assertEqual(CONFIG.get("model_name"), "C:/m/a.gguf")
        mock_get.assert_called_once_with(name="local_llm")
        self.assertEqual(mock_get.return_value.model_file, "C:/m/a.gguf")
        self.wizard.model_page.load_selected_model.assert_not_called()

    def test_accept_loads_model_when_load_now_checked(self):
        """load_now checked → synchronous load on Finish; status shown."""
        self.wizard.model_page._resolve_model_target.return_value = "C:/m/a.gguf"
        self.wizard.model_page.load_now.isChecked.return_value = True
        self.wizard.model_page.load_selected_model.return_value = {
            "ok": True, "load_time_ms": 100,
        }
        with patch.object(self.wizard, "_save_settings"):
            with patch("onboarding_wizard.mark_onboarding_complete"):
                # Patch get_provider: accept() adopts the selection into the
                # live provider BEFORE loading — must not touch the real cache.
                with patch("ai_base.get_provider"):
                    self.wizard.accept()
        self.wizard.model_page.load_selected_model.assert_called_once()

    def test_accept_skips_model_when_nothing_selected(self):
        """Empty selection → no load, no CONFIG write (no-default policy)."""
        self.wizard.provider_page.provider_combo.currentData.return_value = "local_llm"
        self.wizard.tts_page.tts_enabled.isChecked.return_value = False
        self.wizard.model_page._resolve_model_target.return_value = ""
        from config import CONFIG
        with patch.object(self.wizard, "_save_settings"):
            with patch("onboarding_wizard.mark_onboarding_complete"):
                # clear=True: start from a pristine CONFIG so the assert
                # below proves accept() never wrote the key.
                with patch.dict("onboarding_wizard.CONFIG", {}, clear=True):
                    self.wizard.accept()
                    # Assert INSIDE the block (patch.dict restores on exit).
                    self.assertNotIn("model_file", CONFIG)  # never touched

    def test_accept_model_page_runtime_error_does_not_block_finish(self):
        """A broken model page must not prevent completing the wizard."""
        self.wizard.provider_page.provider_combo.currentData.return_value = "local_llm"
        self.wizard.tts_page.tts_enabled.isChecked.return_value = False
        self.wizard.model_page._resolve_model_target.side_effect = RuntimeError("page gone")
        with patch.object(self.wizard, "_save_settings"):
            with patch("onboarding_wizard.mark_onboarding_complete"):
                with patch.dict("onboarding_wizard.CONFIG", {}, clear=True):
                    self.wizard.accept()  # must not raise

    def test_save_settings_calls_save_functions(self):
        with patch("onboarding_wizard.save_settings") as mock_save_s:
            with patch("onboarding_wizard.save_identity") as mock_save_i:
                self.wizard._save_settings()
        mock_save_s.assert_called_once()
        mock_save_i.assert_called_once()

    def test_save_settings_handles_exception(self):
        with patch("onboarding_wizard.save_settings", side_effect=PermissionError("denied")):
            with patch("onboarding_wizard.save_identity"):
                self.wizard._save_settings()

if __name__ == "__main__":
    unittest.main()
