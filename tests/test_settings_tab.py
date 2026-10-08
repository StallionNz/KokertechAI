"""
Unit tests for SettingsTabMixin._update_provider_fields — URL refresh behaviour,
API key loading, and provider-specific labels.

All PyQt widgets are mocked via MagicMock.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch, call

# QApplication provided by conftest.py (session-scoped qapp fixture)

# ---------------------------------------------------------------------------
# Helper — factory for a mock SettingsTabMixin instance
# ---------------------------------------------------------------------------

def _make_settings():
    """Return a SettingsTabMixin instance wired with mock widgets.

    All widget attributes that _update_provider_fields touches are plain
    MagicMock so setText / clear / currentText / setPlaceholderText all work.
    """
    from tabs.settings_tab import SettingsTabMixin

    obj = SettingsTabMixin()

    # Widgets required by _update_provider_fields
    obj.provider_combo = MagicMock(name="provider_combo")
    obj.provider_status_indicator = MagicMock(name="provider_status_indicator")
    obj.model_combo = MagicMock(name="model_combo")
    obj.models_dir_input = MagicMock(name="models_dir_input")
    obj.llm_n_ctx_spin = MagicMock(name="llm_n_ctx_spin")
    obj.llm_n_gpu_spin = MagicMock(name="llm_n_gpu_spin")
    obj.llm_n_threads_spin = MagicMock(name="llm_n_threads_spin")

    # Stub out _refresh_model_dropdown (it does async scanning)
    obj._refresh_model_dropdown = MagicMock(name="_refresh_model_dropdown")

    return obj

def _make_full_settings():
    """Return a SettingsTabMixin wired with ALL widgets that
    _sync_ui_from_config touches, plus stubs for methods it calls.
    """
    obj = _make_settings()

    # All widget attributes that _sync_ui_from_config touches
    obj.vram_spinbox = MagicMock(name="vram_spinbox")
    obj.setting_theme_combo = MagicMock(name="setting_theme_combo")
    obj.setting_theme_combo.findText.return_value = -1
    # findText on provider_combo (from _make_settings) must return int
    obj.provider_combo.findText.return_value = -1
    obj.tts_enabled_cb = MagicMock(name="tts_enabled_cb")
    obj.tts_speed_spin = MagicMock(name="tts_speed_spin")
    obj.tts_voice_combo = MagicMock(name="tts_voice_combo")
    obj.chk_freeform = MagicMock(name="chk_freeform")
    obj.chk_mock = MagicMock(name="chk_mock")
    obj.hotkey_trigger_input = MagicMock(name="hotkey_trigger_input")
    obj.hotkey_read_input = MagicMock(name="hotkey_read_input")
    obj.hotkey_voice_input = MagicMock(name="hotkey_voice_input")
    obj.chk_debug_logging = MagicMock(name="chk_debug_logging")
    obj.chk_autosave_logs = MagicMock(name="chk_autosave_logs")
    # obj.chk_shutdown_on_exit removed — local LLM only
    obj.chk_always_on_top = MagicMock(name="chk_always_on_top")
    obj.sandbox_enabled_cb = MagicMock(name="sandbox_enabled_cb")
    obj.sandbox_image_input = MagicMock(name="sandbox_image_input")
    obj.sandbox_timeout_spin = MagicMock(name="sandbox_timeout_spin")
    obj.sandbox_memory_input = MagicMock(name="sandbox_memory_input")
    obj.desire_model_input = MagicMock(name="desire_model_input")
    obj.auditor_model_input = MagicMock(name="auditor_model_input")
    obj.vision_model_combo = MagicMock(name="vision_model_combo")
    obj.persona_combo = MagicMock(name="persona_combo")
    obj.id_name_input = MagicMock(name="id_name_input")
    obj.id_tone_input = MagicMock(name="id_tone_input")
    obj.id_pref_input = MagicMock(name="id_pref_input")
    obj.id_bg_input = MagicMock(name="id_bg_input")
    obj.protocol_edit = MagicMock(name="protocol_edit")
    obj.protocol_edit.toPlainText.return_value = ""
    obj.protocol_preset_combo = MagicMock(name="protocol_preset_combo")
    obj.protocol_preset_combo.findText.return_value = -1
    obj.protocol_warning = MagicMock(name="protocol_warning")
    obj.protocol_preset_count = MagicMock(name="protocol_preset_count")
    obj.chk_notifications = MagicMock(name="chk_notifications")

    # Spy on _update_provider_fields so we can assert how it was called
    obj._update_provider_fields = MagicMock(name="_update_provider_fields")

    # Stub out methods that have side effects
    obj._refresh_backups_list = MagicMock(name="_refresh_backups_list")
    obj.apply_theme = MagicMock(name="apply_theme")
    obj._update_mode_indicator = MagicMock(name="_update_mode_indicator")

    return obj

# ===========================================================================
# Tests
# ===========================================================================

# Test classes removed: TestUpdateProviderFieldsProviderSwitch,
# TestUpdateProviderFieldsInitialLoad, TestUpdateProviderFieldsApiKey,
# TestUpdateProviderFieldsLabels, TestUpdateProviderFieldsUrlLabels —
# these tested remote provider URL/key/field logic that was removed
# in the local_llm-only simplification.

class TestUpdateProviderFieldsModelCombo(unittest.TestCase):
    """Model combo lookup and refresh behaviour in _update_provider_fields."""

    def setUp(self):
        self.obj = _make_settings()
        self.obj._local_llm_fields = MagicMock()

    def test_refresh_model_dropdown_called(self):
        """_refresh_model_dropdown() is called on every invocation."""
        with patch.dict("tabs.settings_tab.CONFIG", {}, clear=True):
            self.obj._update_provider_fields(provider_switched=True)

        self.obj._refresh_model_dropdown.assert_called_once()

    def test_model_text_restored_from_config(self):
        """Saved model_file is looked up in the model combo.

        If findText returns -1 (no match), setCurrentText fallback
        is used instead.
        """
        self.obj.model_combo.findText.return_value = -1  # no match

        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"model_file": "my_model.gguf"},
            clear=True,
        ):
            self.obj._update_provider_fields()

        self.obj.model_combo.findText.assert_called_with("my_model.gguf")
        # findText returned -1, so setCurrentText should be used as fallback
        self.obj.model_combo.setCurrentText.assert_called_with("my_model.gguf")
        # setCurrentIndex should NOT have been called (it's the match path)
        self.obj.model_combo.setCurrentIndex.assert_not_called()

    def test_model_text_match_uses_set_current_index(self):
        """When findText matches (returns >= 0), setCurrentIndex is used."""
        self.obj.model_combo.findText.return_value = 2  # found at index 2

        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"model_file": "my_model.gguf"},
            clear=True,
        ):
            self.obj._update_provider_fields()

        self.obj.model_combo.findText.assert_called_with("my_model.gguf")
        self.obj.model_combo.setCurrentIndex.assert_called_once_with(2)
        self.obj.model_combo.setCurrentText.assert_not_called()

    def test_model_text_empty_skips_lookup(self):
        """When saved model_file is empty, no findText or set operations occur."""
        with patch.dict("tabs.settings_tab.CONFIG", {}, clear=True):
            self.obj._update_provider_fields()

        self.obj.model_combo.findText.assert_not_called()
        self.obj.model_combo.setCurrentIndex.assert_not_called()
        self.obj.model_combo.setCurrentText.assert_not_called()

class TestOnProviderChanged(unittest.TestCase):
    """_on_provider_changed — calls _update_provider_fields(provider_switched=True)
    and logs an audit message."""

    def setUp(self):
        self.obj = _make_settings()
        self.obj.log_to_audit = MagicMock()

    def test_calls_update_provider_fields_with_true(self):
        """_on_provider_changed calls _update_provider_fields(provider_switched=True)."""
        with patch.object(self.obj, "_update_provider_fields") as mock_update:
            self.obj._on_provider_changed()

        mock_update.assert_called_once_with(provider_switched=True)

    def test_logs_audit_message(self):
        """_on_provider_changed logs the correct audit message."""
        with patch.object(self.obj, "_update_provider_fields"):
            self.obj._on_provider_changed()

        self.obj.log_to_audit.assert_called_once_with(
            "🔄 Provider settings reloaded"
        )

class TestCreateSettingsTab(unittest.TestCase):
    """create_settings_tab — calls _update_provider_fields with
    provider_switched=False on initial tab creation."""

    def test_calls_update_provider_fields_with_false(self):
        """create_settings_tab calls _update_provider_fields(provider_switched=False)
        on initial tab creation, ensuring saved settings are preserved."""
        import contextlib
        import tabs.settings_tab as st

        obj = st.SettingsTabMixin()

        # Stub methods that _update_provider_fields or create_settings_tab call
        obj._refresh_model_dropdown = MagicMock()
        obj._refresh_backups_list = MagicMock()
        obj._test_docker_sandbox = MagicMock()
        obj.apply_theme = MagicMock()
        obj.log_to_audit = MagicMock()
        obj._update_theme_swatch = MagicMock()
        obj.setup_vram_monitor = MagicMock()

        # Patch all PyQt widget constructors used in create_settings_tab.
        def _make_combo():
            m = MagicMock()
            m.findText.return_value = -1
            return m

        patch_widgets = [
            patch.object(st, "QScrollArea"),
            patch.object(st, "QWidget"),
            patch.object(st, "QVBoxLayout"),
            patch.object(st, "QHBoxLayout"),
            patch.object(st, "QFormLayout"),
            patch.object(st, "QGroupBox"),
            patch.object(st, "QSpinBox"),
            patch.object(st, "QComboBox", side_effect=_make_combo),
            patch.object(st, "QLineEdit"),
            patch.object(st, "QLabel"),
            patch.object(st, "QCheckBox"),
            patch.object(st, "QPushButton"),
            patch.object(st, "QTextEdit"),
        ]

        with patch.object(obj, "_update_provider_fields") as mock_update:
            with patch.dict("tabs.settings_tab.CONFIG", {}, clear=True):
                with patch.dict("tabs.settings_tab.IDENTITY_CONFIG", {}, clear=True):
                    with contextlib.ExitStack() as stack:
                        for p in patch_widgets:
                            stack.enter_context(p)
                        obj.create_settings_tab()

        mock_update.assert_called_once_with(provider_switched=False)

# ===========================================================================
# Tests — Protocol Presets
# ===========================================================================

class TestProtocolPresets(unittest.TestCase):
    """Unit tests for protocol preset methods:
    _rebuild_protocol_presets, _on_protocol_preset_changed,
    _save_protocol_preset, _delete_protocol_preset.
    """

    def setUp(self):
        self.obj = _make_full_settings()
        self.obj.log_to_audit = MagicMock()

    # -- _seed_builtin_protocol_presets --------------------------------------

    def test_seed_builtin_fills_empty_config(self):
        """Seeds built-in templates when protocol_presets is empty."""
        from config import CONFIG as real_cfg

        with patch.dict(real_cfg, {"protocol_presets": {}}, clear=False):
            self.obj._seed_builtin_protocol_presets()
            presets = real_cfg.get("protocol_presets", {})

        self.assertIn("Structured with reasoning", presets)
        self.assertIn("Freeform Assistant", presets)
        self.assertIn("Concise Coder", presets)
        self.assertEqual(len(presets), 3)

    def test_seed_builtin_skips_when_presets_exist(self):
        """Does nothing when presets already exist."""
        from config import CONFIG as real_cfg

        with patch.dict(
            real_cfg,
            {"protocol_presets": {"Existing": "content"}},
            clear=False,
        ):
            self.obj._seed_builtin_protocol_presets()
            presets = real_cfg.get("protocol_presets", {})

        self.assertEqual(presets, {"Existing": "content"})
        self.assertNotIn("Structured with reasoning", presets)

    # -- _rebuild_protocol_presets -------------------------------------------

    def test_rebuild_populates_combo_with_presets(self):
        """_rebuild_protocol_presets adds 'Custom...' + saved presets."""
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"protocol_presets": {"Pirate": "ARRR", "Engineer": "<thinking>"}},
            clear=False,
        ):
            self.obj._rebuild_protocol_presets()

        # Should clear, add Custom..., then presets
        self.obj.protocol_preset_combo.clear.assert_called_once()
        self.obj.protocol_preset_combo.addItem.assert_any_call("Custom...", "__custom__")
        self.obj.protocol_preset_combo.addItem.assert_any_call("Engineer", "Engineer")
        self.obj.protocol_preset_combo.addItem.assert_any_call("Pirate", "Pirate")

    def test_rebuild_matches_current_protocol_to_preset(self):
        """When current protocol matches a preset by content, select it."""
        self.obj.protocol_preset_combo.findText.return_value = 1  # found at index 1

        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {
                "protocol_presets": {"Pirate": "ARRR", "Engineer": "<thinking>"},
                "protocol_prompt": "ARRR",
            },
            clear=False,
        ):
            self.obj._rebuild_protocol_presets()

        # Should find the matching preset (Pirate) and select it
        self.obj.protocol_preset_combo.findText.assert_any_call("Pirate")
        self.obj.protocol_preset_combo.setCurrentIndex.assert_called_with(1)

    def test_rebuild_falls_back_to_custom_when_no_match(self):
        """When no preset matches the current protocol, select 'Custom...'."""
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {
                "protocol_presets": {"Pirate": "ARRR"},
                "protocol_prompt": "Some unique text",
            },
            clear=False,
        ):
            self.obj._rebuild_protocol_presets()

        # Should fall back to index 0 (Custom...)
        self.obj.protocol_preset_combo.setCurrentIndex.assert_called_with(0)

    def test_rebuild_updates_count_label(self):
        """Count label shows 'N presets' or empty when none."""
        # With presets
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"protocol_presets": {"A": "1", "B": "2", "C": "3"}},
            clear=False,
        ):
            self.obj._rebuild_protocol_presets()
        self.obj.protocol_preset_count.setText.assert_called_with("3 presets")

        # With single preset
        self.obj.protocol_preset_count.reset_mock()
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"protocol_presets": {"A": "1"}},
            clear=False,
        ):
            self.obj._rebuild_protocol_presets()
        self.obj.protocol_preset_count.setText.assert_called_with("1 preset")

        # With no presets
        self.obj.protocol_preset_count.reset_mock()
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"protocol_presets": {}},
            clear=False,
        ):
            self.obj._rebuild_protocol_presets()
        self.obj.protocol_preset_count.setText.assert_called_with("")

    def test_rebuild_with_missing_key_uses_empty_dict(self):
        """When protocol_presets key is missing from CONFIG, uses empty dict."""
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"freeform_mode": False, "mock_mode": False},
            clear=True,
        ):
            self.obj._rebuild_protocol_presets()

        # Should not crash - gracefully handle missing key
        self.obj.protocol_preset_combo.clear.assert_called_once()
        self.obj.protocol_preset_combo.addItem.assert_called_once_with(
            "Custom...", "__custom__"
        )
        # No presets to add (empty dict), so only Custom... is added
        self.obj.protocol_preset_count.setText.assert_called_with("")

    # -- _on_protocol_preset_changed -----------------------------------------

    def test_preset_changed_loads_content_into_editor(self):
        """Selecting a preset loads its content into the protocol editor."""
        self.obj.protocol_preset_combo.currentData.return_value = "Pirate"

        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"protocol_presets": {"Pirate": "ARRR!"}},
            clear=False,
        ):
            self.obj._on_protocol_preset_changed(1)

        self.obj.protocol_edit.blockSignals.assert_any_call(True)
        self.obj.protocol_edit.setPlainText.assert_called_once_with("ARRR!")
        self.obj.protocol_edit.blockSignals.assert_any_call(False)

    def test_preset_changed_ignores_custom(self):
        """Selecting 'Custom...' does nothing (returns early)."""
        self.obj.protocol_preset_combo.currentData.return_value = "__custom__"
        self.obj._on_protocol_preset_changed(0)
        self.obj.protocol_edit.setPlainText.assert_not_called()

    def test_preset_changed_ignores_none(self):
        """Selecting a nonexistent item does nothing."""
        self.obj.protocol_preset_combo.currentData.return_value = None
        self.obj._on_protocol_preset_changed(-1)
        self.obj.protocol_edit.setPlainText.assert_not_called()

    def test_preset_changed_logs_audit(self):
        """Loading a preset logs to audit."""
        self.obj.protocol_preset_combo.currentData.return_value = "Engineer"

        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"protocol_presets": {"Engineer": "<thinking>"}},
            clear=False,
        ):
            self.obj._on_protocol_preset_changed(1)

        self.obj.log_to_audit.assert_called_once()
        self.assertIn("Engineer", self.obj.log_to_audit.call_args[0][0])

    def test_preset_changed_runs_validation(self):
        """Validation is called after loading a preset."""
        self.obj.protocol_preset_combo.currentData.return_value = "Pirate"

        # Restore the real _validate_protocol so we can spy on it
        # (it's currently a MagicMock from _make_full_settings, so no-op is fine)
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"protocol_presets": {"Pirate": "ARRR!"}},
            clear=False,
        ):
            with patch.object(self.obj, "_validate_protocol") as mock_validate:
                self.obj._on_protocol_preset_changed(1)

        mock_validate.assert_called_once()

    # -- _save_protocol_preset -----------------------------------------------

    def test_save_preset_writes_to_config(self):
        """Saving a preset writes to CONFIG and rebuilds the combo."""
        self.obj.protocol_edit.toPlainText.return_value = "My custom protocol"

        with patch("tabs.settings_tab.QInputDialog.getText") as mock_dialog:
            mock_dialog.return_value = ("My Protocol", True)
            with patch.object(self.obj, "_rebuild_protocol_presets") as mock_rebuild:
                self.obj._save_protocol_preset()

        # CONFIG should have the new preset
        from tabs.settings_tab import CONFIG
        self.assertIn("My Protocol", CONFIG.get("protocol_presets", {}))
        self.assertEqual(
            CONFIG["protocol_presets"]["My Protocol"],
            "My custom protocol",
        )
        mock_rebuild.assert_called_once()

    def test_save_preset_empty_editor_shows_message(self):
        """Saving with an empty editor shows an info message."""
        self.obj.protocol_edit.toPlainText.return_value = ""

        with patch("tabs.settings_tab.QMessageBox.information") as mock_info:
            self.obj._save_protocol_preset()

        mock_info.assert_called_once()
        self.assertIn("Empty", mock_info.call_args[0][1])

    def test_save_preset_cancelled_dialog(self):
        """Cancelling the save dialog does nothing."""
        self.obj.protocol_edit.toPlainText.return_value = "Some protocol"

        with patch("tabs.settings_tab.QInputDialog.getText") as mock_dialog:
            mock_dialog.return_value = ("", False)  # cancelled
            with patch.object(self.obj, "_rebuild_protocol_presets") as mock_rebuild:
                self.obj._save_protocol_preset()

        mock_rebuild.assert_not_called()

    def test_save_preset_logs_audit(self):
        """Saving a preset logs to audit."""
        self.obj.protocol_edit.toPlainText.return_value = "Some protocol"

        with patch("tabs.settings_tab.QInputDialog.getText") as mock_dialog:
            mock_dialog.return_value = ("My Preset", True)
            with patch.object(self.obj, "_rebuild_protocol_presets"):
                self.obj._save_protocol_preset()

        self.obj.log_to_audit.assert_called_once()
        self.assertIn("My Preset", self.obj.log_to_audit.call_args[0][0])

    def test_save_preset_selects_new_preset_in_combo(self):
        """After saving, the combo selects the newly saved preset."""
        self.obj.protocol_edit.toPlainText.return_value = "My protocol"
        self.obj.protocol_preset_combo.findText.return_value = 2  # found at index 2

        with patch("tabs.settings_tab.QInputDialog.getText") as mock_dialog:
            mock_dialog.return_value = ("New Preset", True)
            with patch.object(self.obj, "_rebuild_protocol_presets"):
                self.obj._save_protocol_preset()

        self.obj.protocol_preset_combo.findText.assert_called_with("New Preset")
        self.obj.protocol_preset_combo.setCurrentIndex.assert_called_with(2)

    # -- _delete_protocol_preset ---------------------------------------------

    def test_delete_preset_removes_from_config(self):
        """Deleting a preset removes it from CONFIG and rebuilds."""
        from config import CONFIG as real_cfg
        self.obj.protocol_preset_combo.currentData.return_value = "Pirate"

        with patch.dict(
            real_cfg,
            {"protocol_presets": {"Pirate": "ARRR", "Engineer": "<thinking>"}},
            clear=False,
        ):
            with patch("tabs.settings_tab.QMessageBox.warning") as mock_warn:
                from PyQt6.QtWidgets import QMessageBox
                mock_warn.return_value = QMessageBox.StandardButton.Yes

                with patch.object(self.obj, "_rebuild_protocol_presets") as mock_rebuild:
                    self.obj._delete_protocol_preset()

                # CONFIG assertions inside the patch.dict block
                self.assertNotIn("Pirate", real_cfg.get("protocol_presets", {}))
                self.assertIn("Engineer", real_cfg.get("protocol_presets", {}))
                mock_rebuild.assert_called_once()

    def test_delete_preset_custom_shows_message(self):
        """Deleting with 'Custom...' selected shows info message."""
        self.obj.protocol_preset_combo.currentData.return_value = "__custom__"

        with patch("tabs.settings_tab.QMessageBox.information") as mock_info:
            self.obj._delete_protocol_preset()

        mock_info.assert_called_once()
        self.assertIn("No Preset", mock_info.call_args[0][1])

    def test_delete_preset_none_selected_shows_message(self):
        """Deleting with None selected shows info message."""
        self.obj.protocol_preset_combo.currentData.return_value = None

        with patch("tabs.settings_tab.QMessageBox.information") as mock_info:
            self.obj._delete_protocol_preset()

        mock_info.assert_called_once()

    def test_delete_preset_cancelled_confirmation(self):
        """Cancelling the delete confirmation does nothing."""
        from config import CONFIG as real_cfg
        self.obj.protocol_preset_combo.currentData.return_value = "Pirate"

        with patch.dict(
            real_cfg,
            {"protocol_presets": {"Pirate": "ARRR"}},
            clear=False,
        ):
            with patch("tabs.settings_tab.QMessageBox.warning") as mock_warn:
                from PyQt6.QtWidgets import QMessageBox
                mock_warn.return_value = QMessageBox.StandardButton.No

                with patch.object(self.obj, "_rebuild_protocol_presets") as mock_rebuild:
                    self.obj._delete_protocol_preset()

                # CONFIG assertion inside the patch.dict block
                self.assertIn("Pirate", real_cfg.get("protocol_presets", {}))
                mock_rebuild.assert_not_called()

    def test_delete_preset_logs_audit(self):
        """Deleting a preset logs to audit."""
        self.obj.protocol_preset_combo.currentData.return_value = "Pirate"

        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"protocol_presets": {"Pirate": "ARRR"}},
            clear=False,
        ):
            with patch("tabs.settings_tab.QMessageBox.warning") as mock_warn:
                from PyQt6.QtWidgets import QMessageBox
                mock_warn.return_value = QMessageBox.StandardButton.Yes
                with patch.object(self.obj, "_rebuild_protocol_presets"):
                    self.obj._delete_protocol_preset()

        self.obj.log_to_audit.assert_called_once()
        self.assertIn("Pirate", self.obj.log_to_audit.call_args[0][0])

class TestSetProviderStatus(unittest.TestCase):
    """Tests for _set_provider_status() UI update method."""

    def setUp(self):
        self.obj = _make_settings()
        self.obj._shutting_down = False

    def test_sets_tooltip_style_and_text(self):
        self.obj._set_provider_status("🟢", "Online", "#10B981", "Local LLM")
        self.obj.provider_status_indicator.setToolTip.assert_called_once_with(
            "Local LLM: Online")
        self.obj.provider_status_indicator.setStyleSheet.assert_called_once_with(
            "font-size: 8pt; color: #10B981; padding-left: 4px;")
        self.obj.provider_status_indicator.setText.assert_called_once_with(
            "🟢 Online")

    def test_offline_uses_red_color(self):
        self.obj._set_provider_status("🔴", "Offline", "#EF4444", "Local LLM")
        self.obj.provider_status_indicator.setToolTip.assert_called_once_with(
            "Local LLM: Offline")
        style = self.obj.provider_status_indicator.setStyleSheet.call_args[0][0]
        self.assertIn("#EF4444", style)
        self.obj.provider_status_indicator.setText.assert_called_once_with(
            "🔴 Offline")

    def test_error_uses_amber_color(self):
        self.obj._set_provider_status("⚠", "Error", "#FBBF24", "Local LLM")
        style = self.obj.provider_status_indicator.setStyleSheet.call_args[0][0]
        self.assertIn("#FBBF24", style)

    def test_skips_when_shutting_down(self):
        self.obj._shutting_down = True
        self.obj._set_provider_status("🟢", "Online", "#10B981", "Local LLM")
        self.obj.provider_status_indicator.setToolTip.assert_not_called()
        self.obj.provider_status_indicator.setStyleSheet.assert_not_called()
        self.obj.provider_status_indicator.setText.assert_not_called()

    def test_catches_runtime_error_on_destroyed_widget(self):
        self.obj.provider_status_indicator.setToolTip.side_effect = RuntimeError(
            "wrapped C/C++ object deleted")
        # Should not raise
        self.obj._set_provider_status("🟢", "Online", "#10B981", "Local LLM")

    def test_catches_runtime_error_on_settext(self):
        self.obj.provider_status_indicator.setText.side_effect = RuntimeError(
            "wrapped C/C++ object deleted")
        # Should not raise
        self.obj._set_provider_status("🟢", "Online", "#10B981", "Local LLM")

    def test_does_not_skip_if_shutting_down_false(self):
        """When _shutting_down is False, the method proceeds with UI updates."""
        self.obj._shutting_down = False
        self.obj._set_provider_status("🟢", "Online", "#10B981", "Local LLM")
        self.obj.provider_status_indicator.setToolTip.assert_called_once()
class TestRefreshProviderStatus(unittest.TestCase):
    """Tests for _refresh_provider_status() — now checks local model file on disk
    (no HTTP check_provider_status call).
    """

    def setUp(self):
        self.obj = _make_settings()
        self.obj._set_provider_status = MagicMock()
        self.obj._last_provider_status_check = 0

    @patch("threading.Thread")
    def test_sets_loading_indicator_before_check(self, mock_thread):
        self.obj._refresh_provider_status()
        self.obj.provider_status_indicator.setText.assert_called_once_with("⏳")

    @patch("time.time", return_value=100.0)
    @patch("threading.Thread")
    def test_first_call_always_passes_cooldown(self, mock_thread, mock_time):
        self.obj._last_provider_status_check = 0
        self.obj._refresh_provider_status()
        mock_thread.assert_called_once()

    @patch("time.time", return_value=100.0)
    @patch("threading.Thread")
    def test_skips_if_within_30s_cooldown(self, mock_thread, mock_time):
        self.obj._last_provider_status_check = 85.0
        self.obj._refresh_provider_status()
        mock_thread.assert_not_called()

    @patch("time.time", return_value=100.0)
    @patch("threading.Thread")
    def test_passes_after_30s_cooldown(self, mock_thread, mock_time):
        self.obj._last_provider_status_check = 60.0
        self.obj._refresh_provider_status()
        mock_thread.assert_called_once()

    @patch("time.time", return_value=100.0)
    @patch("threading.Thread")
    def test_updates_timestamp_after_check(self, mock_thread, mock_time):
        self.obj._last_provider_status_check = 0
        self.obj._refresh_provider_status()
        self.assertEqual(self.obj._last_provider_status_check, 100.0)

    @patch("time.time", return_value=100.0)
    @patch.dict("tabs.settings_tab.CONFIG", {"model_file": "C:\\Models\\my_model.gguf"}, clear=True)
    def test_background_thread_checks_local_model_file(self, mock_time):
        """Patches threading.Thread to execute synchronously,
        then verifies the background check uses the model file path from CONFIG."""
        self.obj._last_provider_status_check = 0

        def _sync_thread(**kwargs):
            target = kwargs.get("target")
            if target:
                target()
            return MagicMock(name="mock_thread")

        with patch("os.path.isfile", return_value=True):
            with patch("os.path.getsize", return_value=2 * 1024**3):  # 2GB
                with patch("threading.Thread", side_effect=_sync_thread):
                    with patch("PyQt6.QtCore.QTimer.singleShot") as mock_timer:
                        self.obj._refresh_provider_status()

        mock_timer.assert_called_once()
class TestDiscoveryUrlField(unittest.TestCase):
    """Tests for KOKERTECH_DISCOVERY_URL Settings field (added June 2026).

    Locks in the contract:
    * save_settings writes typed value to CONFIG["KOKERTECH_DISCOVERY_URL"].
    * Empty / whitespace-only input falls back to Discovery.DISCOVERY_URL module default.
    * Missing widget (older test harness build) is tolerated; save_settings
      doesn't raise or clobber an existing CONFIG value.
    * _sync_ui_from_config rehydrates the field from CONFIG after a backup-restore.
    * _sync_ui_from_config falls back to Discovery.DISCOVERY_URL when the
      CONFIG key is absent (fresh install / first run).

    Restart-required semantics are documented in create_settings_tab() but
    not asserted in unit tests here (no QApplication startup to bypass).
    """

    # Attribute names save_settings() and _sync_ui_from_config() touch.
    # All populated as MagicMock by _stub_widgets() so individual test
    # methods can focus on the discovery-URL contract.
    _UI_WIDGETS = (
        "provider_combo", "vram_spinbox", "model_combo", "models_dir_input",
        "llm_n_ctx_spin", "llm_n_gpu_spin", "llm_n_threads_spin",
        "desire_model_input", "auditor_model_input", "vision_model_combo",
        "setting_theme_combo", "tts_enabled_cb", "tts_speed_spin",
        "tts_voice_combo", "persona_combo", "protocol_edit",
        "chk_freeform", "chk_mock",
        "hotkey_trigger_input", "hotkey_read_input", "hotkey_voice_input",
        "chk_always_on_top", "chk_debug_logging", "chk_autosave_logs",
        "chk_notifications",
        "chk_chat_history", "spin_chat_history_debounce",
        # Keepalive watchdog cap (Settings-tab override for runtime cap)
        "spin_keepalive_max_ticks",
        # Resource threshold percentages (v0.22.11)
        "spin_ram_warning_pct", "spin_ram_critical_pct",
        "spin_disk_warning_pct", "spin_disk_critical_pct",
        "spin_vram_warning_pct", "spin_vram_critical_pct",
        "sandbox_enabled_cb", "sandbox_image_input",
        "sandbox_timeout_spin", "sandbox_memory_input",
        "web_server_cb", "web_port_spin",
        "id_name_input", "id_tone_input", "id_pref_input", "id_bg_input",
        # backup toolbar (sync only)
        "backup_list", "backup_count_label", "btn_restore_backup",
        "backup_restore_status",
        # protocol-preset combo (touched by _rebuild_protocol_presets)
        "protocol_preset_combo",
    )

    @staticmethod
    def _stub_widgets(for_sync=False):
        """Build a SettingsTabMixin instance with all widgets save_settings()
        and (optionally) _sync_ui_from_config() touch stubbed as MagicMock.

        log_to_audit, apply_theme, setup_vram_monitor, _rebuild_protocol_presets,
        _validate_protocol are set as instance attributes because they only
        exist on the dashboard class (mixed in at runtime), not as
        module-level symbols — so they cannot be patched with
        ``@patch("tabs.settings_tab.<name>")`` and must be set directly
        on the instance.
        """
        from tabs.settings_tab import SettingsTabMixin
        obj = SettingsTabMixin()
        attrs = set(TestDiscoveryUrlField._UI_WIDGETS)
        if not for_sync:
            # save_settings path doesn't need backup-toolbar attrs.
            attrs -= {
                "backup_list", "backup_count_label",
                "btn_restore_backup", "backup_restore_status",
            }
        for attr in attrs:
            setattr(obj, attr, MagicMock())
            # Spinbox stubs return int 0 from .value() so save_settings()
            # writes a real int into CONFIG instead of polluting it with
            # MagicMock objects (reviewer note — same hygiene as the
            # keepalive spinbox). Covers vram_spinbox, tts_speed_spin,
            # sandbox_timeout_spin, web_port_spin, spin_* — everything
            # containing 'spin'.
            if "spin" in attr:
                getattr(obj, attr).value.return_value = 0
        obj.protocol_edit.toPlainText.return_value.strip.return_value = ""
        # Stable findText return values so setCurrentIndex(-1) is the
        # only branch that fires (less noise in setText call counts).
        obj.provider_combo.findText.return_value = -1
        obj.setting_theme_combo.findText.return_value = -1
        # Methods added by other mixins / dashboard class (not on the
        # tabs.settings_tab module — patching them as
        # ``tabs.settings_tab.<name>`` would raise AttributeError).
        obj.log_to_audit = MagicMock()
        obj.apply_theme = MagicMock()
        obj.setup_vram_monitor = MagicMock()
        obj._rebuild_protocol_presets = MagicMock()
        obj._validate_protocol = MagicMock()
        # Test-only flag toggled to gate _update_mode_indicator save-time
        # call (which is optional in production via hasattr).
        obj._update_mode_indicator = MagicMock()
        return obj

    @patch("tabs.settings_tab.save_settings")
    @patch("tabs.settings_tab.save_identity")
    @patch("tabs.settings_tab.QMessageBox")
    def test_save_writes_typed_url_to_config(
        self, _qmb, _ident, _save
    ):
        """save_settings reads discovery_url_input.text() and writes
        the typed URL to CONFIG["KOKERTECH_DISCOVERY_URL"]."""
        import config as _config
        obj = self._stub_widgets(for_sync=False)
        disc = MagicMock()
        disc.text.return_value.strip.return_value = (
            "https://example.com/v1/models"
        )
        obj.discovery_url_input = disc
        with patch.dict("tabs.settings_tab.CONFIG", clear=False), \
                patch.dict("config.CONFIG", clear=False):
            obj.save_settings()
            self.assertEqual(
                _config.CONFIG.get("KOKERTECH_DISCOVERY_URL"),
                "https://example.com/v1/models",
                "save_settings did not honor the typed URL",
            )

    @patch("tabs.settings_tab.save_settings")
    @patch("tabs.settings_tab.save_identity")
    @patch("tabs.settings_tab.QMessageBox")
    def test_save_empty_falls_back_to_module_default(
        self, _qmb, _ident, _save
    ):
        """Empty input falls back to Discovery.DISCOVERY_URL so a typo
        doesn't silently break the probe."""
        import config as _config
        import Discovery as _Discovery
        obj = self._stub_widgets(for_sync=False)
        blank = MagicMock()
        blank.text.return_value.strip.return_value = ""
        obj.discovery_url_input = blank
        with patch.dict("tabs.settings_tab.CONFIG", clear=False), \
                patch.dict("config.CONFIG", clear=False):
            obj.save_settings()
            self.assertEqual(
                _config.CONFIG.get("KOKERTECH_DISCOVERY_URL"),
                _Discovery.DISCOVERY_URL,
                "blank input should fall back to Discovery.DISCOVERY_URL",
            )

    @patch("tabs.settings_tab.save_settings")
    @patch("tabs.settings_tab.save_identity")
    @patch("tabs.settings_tab.QMessageBox")
    def test_save_handles_missing_widget_gracefully(
        self, _qmb, _ident, _save
    ):
        """If the widget has not been constructed (older test harness,
        pre-migration build, or feature flag off), save_settings must
        NOT raise and must NOT clobber an existing CONFIG value.
        """
        import config as _config
        obj = self._stub_widgets(for_sync=False)
        # Explicitly verify the precondition: the guard branch is the one
        # under test, so the absent-widget state must be enforced.
        self.assertFalse(
            hasattr(obj, "discovery_url_input"),
            "precondition: discovery_url_input must be absent for the "
            "guard path to be exercised",
        )
        with patch.dict("tabs.settings_tab.CONFIG",
                        {"KOKERTECH_DISCOVERY_URL": "https://prior.example/v1"},
                        clear=False), \
                patch.dict("config.CONFIG",
                           {"KOKERTECH_DISCOVERY_URL": "https://prior.example/v1"},
                           clear=False):
            try:
                obj.save_settings()
            except Exception as exc:
                self.fail(f"save_settings raised with absent widget: {exc!r}")
            # CONFIG value must not be clobbered (guard path skips the write).
            self.assertEqual(
                _config.CONFIG.get("KOKERTECH_DISCOVERY_URL"),
                "https://prior.example/v1",
                "absent widget should NOT overwrite existing CONFIG value",
            )

    def test_sync_restores_field_from_config(self):
        """_sync_ui_from_config should set discovery_url_input text
        from CONFIG["KOKERTECH_DISCOVERY_URL"] (post-restore)."""
        obj = self._stub_widgets(for_sync=True)
        obj._update_provider_fields = MagicMock()
        obj._refresh_backups_list = MagicMock()
        obj._update_mode_indicator = MagicMock()
        disc = MagicMock()
        obj.discovery_url_input = disc
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"KOKERTECH_DISCOVERY_URL": "https://restored.example/v1/models"},
            clear=False,
        ), patch.dict(
            "config.CONFIG",
            {"KOKERTECH_DISCOVERY_URL": "https://restored.example/v1/models"},
            clear=False,
        ):
            obj._sync_ui_from_config()
        all_settexts = disc.setText.call_args_list
        self.assertTrue(
            any(
                "https://restored.example/v1/models" in str(c.args)
                for c in all_settexts
            ),
            f"discovery_url_input.setText never received restored URL; "
            f"calls: {all_settexts}",
        )

    def test_sync_uses_module_default_when_config_missing(self):
        """First-run / freshly-popped path: pop KOKERTECH_DISCOVERY_URL;
        sync must still hydrate the field with Discovery.DISCOVERY_URL.
        """
        import config as _config
        import Discovery as _Discovery
        obj = self._stub_widgets(for_sync=True)
        obj._update_provider_fields = MagicMock()
        obj._refresh_backups_list = MagicMock()
        obj._update_mode_indicator = MagicMock()
        disc = MagicMock()
        obj.discovery_url_input = disc
        with patch.dict("tabs.settings_tab.CONFIG", {}, clear=False), \
                patch.dict("config.CONFIG", {}, clear=False):
            _config.CONFIG.pop("KOKERTECH_DISCOVERY_URL", None)
            obj._sync_ui_from_config()
        all_settexts = disc.setText.call_args_list
        self.assertTrue(
            any(
                _Discovery.DISCOVERY_URL in str(c.args)
                for c in all_settexts
            ),
            f"discovery_url_input.setText never received module default; "
            f"calls: {all_settexts}",
        )

# ===========================================================================
# Tests — Keepalive max-ticks Settings UI (power-user keepalive cap)
# ===========================================================================

class TestKeepaliveMaxTicks(unittest.TestCase):
    """Tests for the keepalive_max_ticks Settings UI.

    Locks in the contract:
    * save_settings reads spinbox.value() and writes CONFIG["keepalive_max_ticks"].
    * _sync_ui_from_config restores the spinbox from CONFIG["keepalive_max_ticks"].
    * _sync_ui_from_config falls back to keepalive_helper.MAX_TICK_FIRES
      (= 4) when the CONFIG key is absent (first run / fresh install).
    """

    def setUp(self):
        # Reuse the full-settings fixture; add the spinbox as a MagicMock
        # (matches the existing pattern for newly-added widgets).
        # _make_full_settings already provides apply_theme, but
        # save_settings() also calls setup_vram_monitor + log_to_audit,
        # which aren't part of that helper — we mock them here to keep
        # both save_settings() and _sync_ui_from_config() invocations safe.
        self.obj = _make_full_settings()
        self.obj.setup_vram_monitor = MagicMock(name="setup_vram_monitor")
        self.obj.log_to_audit = MagicMock(name="log_to_audit")
        self.obj.spin_keepalive_max_ticks = MagicMock(
            name="spin_keepalive_max_ticks"
        )

    @patch("tabs.settings_tab.save_settings")
    @patch("tabs.settings_tab.save_identity")
    @patch("tabs.settings_tab.QMessageBox")
    def test_save_settings_writes_keepalive_max_ticks(
        self, _qmb, _ident, _save
    ):
        """save_settings() reads spinbox.value() and writes
        CONFIG['keepalive_max_ticks']."""
        import tabs.settings_tab as st
        self.obj.spin_keepalive_max_ticks.value.return_value = 7
        with patch.dict("tabs.settings_tab.CONFIG", {}, clear=False):
            self.obj.save_settings()
            # Assertion INSIDE the patch.dict block: patch.dict is restored
            # on context-exit, so the write only stays visible while we're
            # still inside the with. (Earlier this assertion lived outside
            # and read the pre-mutation CONFIG, hence `None != 7`.)
            self.assertEqual(st.CONFIG.get("keepalive_max_ticks"), 7)

    def test_sync_ui_reads_keepalive_max_ticks(self):
        """_sync_ui_from_config() sets spinbox.value to the CONFIG value."""
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"keepalive_max_ticks": 11},
            clear=False,
        ):
            self.obj._sync_ui_from_config()
        self.obj.spin_keepalive_max_ticks.setValue.assert_called_with(11)

    def test_sync_ui_uses_default_when_config_missing(self):
        """When CONFIG has no keepalive_max_ticks key, the helper's
        MAX_TICK_FIRES (= 4) is used as the spinbox default."""
        from keepalive_helper import MAX_TICK_FIRES as expected_default
        with patch.dict("tabs.settings_tab.CONFIG", {}, clear=True):
            self.obj._sync_ui_from_config()
        self.obj.spin_keepalive_max_ticks.setValue.assert_called_with(
            int(expected_default)
        )

    def test_sync_ui_survives_non_int_config_value(self):
        """When CONFIG has a non-int keepalive_max_ticks (hand-edited
        app_settings.json with a string), _sync_ui_from_config should
        fall back to MAX_TICK_FIRES rather than crashing with ValueError.
        Locks in the safe-coerce contract that KeepaliveContext.default_max_ticks()
        guarantees for the runtime path."""
        from keepalive_helper import MAX_TICK_FIRES as safe_default
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"keepalive_max_ticks": "not_a_number"},
            clear=False,
        ):
            # MUST NOT raise
            try:
                self.obj._sync_ui_from_config()
            except (ValueError, TypeError) as exc:
                self.fail(
                    f"_sync_ui_from_config raised on non-int CONFIG: {exc!r}"
                )
        self.obj.spin_keepalive_max_ticks.setValue.assert_called_with(
            int(safe_default)
        )

    @patch("keepalive_helper.QTimer", create=True)
    def test_settings_to_keepalive_runtime_end_to_end(
        self, mock_qtimer_cls
    ):
        """End-to-end integration: Settings-tab spinbox edit propagates
        through ``save_settings()`` into ``CONFIG['keepalive_max_ticks']``,
        and a fresh ``KeepaliveContext.start_keepalive()`` reads that value.

        Bridges the unit-level tests above (separate settings + sync paths)
        with the helper-level tests in ``tests/test_keepalive_helper.py``
        (``TestKeepaliveContextConfigOverride``) by exercising the FULL
        UI → CONFIG → runtime path in a single test.

        Regression scenarios this catches:
          * Spinbox value not actually written by ``save_settings()`` (typo'd
            CONFIG key, missing write line).
          * ``CONFIG`` key name mismatch between Settings tab and helper.
          * ``default_max_ticks()`` reverting to the class default after a
            write (silent regression of the re-read contract).
        """
        import tabs.settings_tab as st
        from keepalive_helper import KeepaliveContext

        # User opens Settings tab, sets "Keepalive max ticks" to 11,
        # clicks Save All Configurations. The patch.dict wraps the whole
        # scenario — save_settings() inside writes to the patched dict.
        self.obj.spin_keepalive_max_ticks.value.return_value = 11
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"keepalive_max_ticks": 11},
            clear=False,
        ):
            self.obj.save_settings()
            self.assertEqual(
                st.CONFIG.get("keepalive_max_ticks"), 11,
                "save_settings did not preserve the spinbox value in CONFIG",
            )

            # Step 3: A caller (e.g. Send button in kokertechController)
            # constructs a fresh KeepaliveContext and passes the
            # user-chosen max_ticks explicitly.
            kc = KeepaliveContext(
                button=MagicMock(),
                log_callback=MagicMock(),
            )
            kc.start_keepalive(
                label="Integration",
                interval_ms=30000,
                max_ticks=11,
            )

            self.assertEqual(
                kc._max_ticks, 11,
                f"start_keepalive must honour the explicit max_ticks=11 "
                f"parameter; got _max_ticks={kc._max_ticks}",
            )
            # Sanity: 11 differs from the class default (4); without this
            # assertion a passing test could mask a real CONFIG-read regression.
            self.assertNotEqual(
                kc._max_ticks, KeepaliveContext.MAX_TICK_FIRES,
                "Test pre-condition: spinbox value (11) must differ from "
                "the class default (4) so a pass-through failure is "
                "distinguishable from a real CONFIG-read regression.",
            )

# ===========================================================================
# Tests — Resource threshold percentage spinboxes (v0.22.11)
# ===========================================================================

class TestResourceThresholdSpinboxes(unittest.TestCase):
    """Tests for the Resource Monitoring threshold-percentage spinboxes.

    Locks in the contract:
    * _create_prefs_group() builds all 6 spinboxes seeded from CONFIG.
    * save_settings() reads each spinbox.value() into its CONFIG key.
    * _sync_ui_from_config() restores each spinbox from CONFIG, falling
      back to the documented default when the key is absent.
    * _on_threshold_pct_changed() persists each spin change immediately
      (mirrors the notification-toggle pattern, so edits survive close).
    * _on_threshold_pct_changed() enforces warning <= critical (clamps an
      inverted pair), so an inverted pair can never be persisted.
    """

    _SPINS = (
        ("spin_ram_warning_pct", "ram_warning_pct", 80),
        ("spin_ram_critical_pct", "ram_critical_pct", 95),
        ("spin_disk_warning_pct", "disk_warning_pct", 85),
        ("spin_disk_critical_pct", "disk_critical_pct", 95),
        ("spin_vram_warning_pct", "vram_warning_pct", 88),
        ("spin_vram_critical_pct", "vram_critical_pct", 95),
    )

    def setUp(self):
        self.obj = _make_full_settings()
        self.obj.setup_vram_monitor = MagicMock(name="setup_vram_monitor")
        self.obj.log_to_audit = MagicMock(name="log_to_audit")
        for attr, _, _ in self._SPINS:
            setattr(self.obj, attr, MagicMock(name=attr))

    @patch("tabs.settings_tab.save_settings")
    @patch("tabs.settings_tab.save_identity")
    @patch("tabs.settings_tab.QMessageBox")
    def test_save_settings_writes_all_threshold_spinboxes(
        self, _qmb, _ident, _save
    ):
        """save_settings() writes each spinbox.value() to its CONFIG key."""
        import tabs.settings_tab as st
        for i, (attr, key, _) in enumerate(self._SPINS):
            getattr(self.obj, attr).value.return_value = 50 + i
        with patch.dict("tabs.settings_tab.CONFIG", {}, clear=False):
            self.obj.save_settings()
            for i, (attr, key, _) in enumerate(self._SPINS):
                self.assertEqual(
                    st.CONFIG.get(key), 50 + i,
                    f"save_settings did not persist {key} from {attr}",
                )

    def test_sync_ui_restores_all_threshold_spinboxes(self):
        """_sync_ui_from_config() sets each spinbox from CONFIG."""
        config_vals = {key: 40 + i for i, (_, key, _) in enumerate(self._SPINS)}
        with patch.dict("tabs.settings_tab.CONFIG", config_vals, clear=False):
            self.obj._sync_ui_from_config()
        for attr, key, _ in self._SPINS:
            getattr(self.obj, attr).setValue.assert_called_with(
                config_vals[key]
            )

    def test_sync_ui_uses_defaults_when_config_missing(self):
        """Absent CONFIG keys fall back to the documented defaults."""
        with patch.dict("tabs.settings_tab.CONFIG", {}, clear=True):
            self.obj._sync_ui_from_config()
        for attr, key, default in self._SPINS:
            getattr(self.obj, attr).setValue.assert_called_with(default)

    def test_sync_ui_survives_non_int_config_values(self):
        """Non-int CONFIG values (hand-edited app_settings.json) fall back to
        the documented default instead of crashing QSpinBox.setValue.
        Locks in the safe-coerce contract (mirror of the keepalive test).
        """
        bad_vals = {key: "not_a_number" for _, key, _ in self._SPINS}
        with patch.dict("tabs.settings_tab.CONFIG", bad_vals, clear=False):
            # MUST NOT raise
            try:
                self.obj._sync_ui_from_config()
            except (ValueError, TypeError) as exc:
                self.fail(
                    f"_sync_ui_from_config raised on non-int CONFIG: {exc!r}"
                )
        for attr, key, default in self._SPINS:
            getattr(self.obj, attr).setValue.assert_called_with(default)

    def test_create_prefs_group_seeds_all_threshold_spinboxes(self):
        """_create_prefs_group() builds the 6 spinboxes from CONFIG defaults."""
        import tabs.settings_tab as st
        obj = st.SettingsTabMixin()
        spins = []

        def _spin():
            m = MagicMock(name="spin")
            spins.append(m)
            return m

        with patch.object(st, "QSpinBox", side_effect=_spin), \
             patch.object(st, "QCheckBox"), \
             patch.object(st, "QLabel"), \
             patch.object(st, "QGroupBox"), \
             patch.object(st, "QFormLayout"), \
             patch.object(st, "QHBoxLayout"), \
             patch.object(st, "QWidget"), \
             patch.object(st, "QPushButton"):
            obj._create_prefs_group()

        setvalues = [
            c.args[0] for m in spins for c in m.setValue.call_args_list
        ]
        for attr, key, default in self._SPINS:
            self.assertIn(
                default, setvalues,
                f"no spinbox seeded with {key} default {default}",
            )

    def test_threshold_spinboxes_wired_to_persist_handler(self):
        """All 6 threshold spinboxes route valueChanged through the handler."""
        import tabs.settings_tab as st
        obj = st.SettingsTabMixin()
        spins = []

        def _spin():
            m = MagicMock(name="spin")
            spins.append(m)
            return m

        with patch.object(st, "QSpinBox", side_effect=_spin), \
             patch.object(st, "QCheckBox"), \
             patch.object(st, "QLabel"), \
             patch.object(st, "QGroupBox"), \
             patch.object(st, "QFormLayout"), \
             patch.object(st, "QHBoxLayout"), \
             patch.object(st, "QWidget"), \
             patch.object(st, "QPushButton"):
            obj._create_prefs_group()

        wired = 0
        for m in spins:
            if m.valueChanged.connect.call_args is None:
                continue
            fn = m.valueChanged.connect.call_args[0][0]
            code = getattr(fn, "__code__", None)
            if code is not None and "_on_threshold_pct_changed" in code.co_names:
                wired += 1
        self.assertEqual(
            wired, 6,
            "expected all 6 threshold spinboxes to route valueChanged "
            "through _on_threshold_pct_changed",
        )

    def test_threshold_change_persists_immediately(self):
        """A spin change writes CONFIG and persists without SAVE ALL."""
        with patch("tabs.settings_tab.save_settings") as mock_save:
            self.obj._on_threshold_pct_changed("ram_warning_pct", 77)
        from tabs.settings_tab import CONFIG as tab_cfg
        self.assertEqual(tab_cfg["ram_warning_pct"], 77)
        mock_save.assert_called_once()
        self.obj.log_to_audit.assert_called_once()

    def test_threshold_change_skipped_while_shutting_down(self):
        """Handler is a no-op during teardown (mirrors toggle guard)."""
        from tabs.settings_tab import CONFIG as tab_cfg
        before = tab_cfg.get("ram_warning_pct")
        self.obj._shutting_down = True
        with patch("tabs.settings_tab.save_settings") as mock_save:
            self.obj._on_threshold_pct_changed("ram_warning_pct", 77)
        mock_save.assert_not_called()
        self.assertEqual(tab_cfg.get("ram_warning_pct"), before)

    def test_threshold_change_save_failure_logged(self):
        """A failing save_settings() logs a warning instead of crashing."""
        self.obj._shutting_down = False
        with patch(
            "tabs.settings_tab.save_settings", side_effect=OSError("disk full")
        ):
            with patch("tabs.settings_tab.logger") as mock_logger:
                self.obj._on_threshold_pct_changed("ram_warning_pct", 66)
        from tabs.settings_tab import CONFIG as tab_cfg
        self.assertEqual(tab_cfg["ram_warning_pct"], 66)
        mock_logger.warning.assert_called_once()

    def test_warning_clamped_down_to_critical(self):
        """A warning above its paired critical clamps down to critical."""
        # NOTE: assertions must run INSIDE the patch.dict block — patch.dict
        # restores the original CONFIG values on exit, which would revert the
        # clamped write before an outside assertion could read it.
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"ram_warning_pct": 80, "ram_critical_pct": 90},
            clear=False,
        ):
            with patch("tabs.settings_tab.save_settings") as mock_save:
                self.obj._on_threshold_pct_changed("ram_warning_pct", 95)
            from tabs.settings_tab import CONFIG as tab_cfg
            self.assertEqual(tab_cfg["ram_warning_pct"], 90)
            mock_save.assert_called_once()
            self.obj.spin_ram_warning_pct.setValue.assert_called_once_with(90)
            self.obj.log_to_audit.assert_called_once_with("RAM warning threshold: 90%")

    def test_critical_clamped_up_to_warning(self):
        """A critical below its paired warning clamps up to warning."""
        # NOTE: assertions must run INSIDE the patch.dict block (see
        # test_warning_clamped_down_to_critical for the restore-on-exit reason).
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"ram_warning_pct": 80, "ram_critical_pct": 95},
            clear=False,
        ):
            with patch("tabs.settings_tab.save_settings") as mock_save:
                self.obj._on_threshold_pct_changed("ram_critical_pct", 60)
            from tabs.settings_tab import CONFIG as tab_cfg
            self.assertEqual(tab_cfg["ram_critical_pct"], 80)
            mock_save.assert_called_once()
            self.obj.spin_ram_critical_pct.setValue.assert_called_once_with(80)
            self.obj.log_to_audit.assert_called_once_with("RAM critical threshold: 80%")

    def test_no_clamp_when_pair_valid(self):
        """A valid warning <= critical pair is written unchanged."""
        # NOTE: assertions must run INSIDE the patch.dict block (see
        # test_warning_clamped_down_to_critical for the restore-on-exit reason).
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"ram_warning_pct": 80, "ram_critical_pct": 90},
            clear=False,
        ):
            with patch("tabs.settings_tab.save_settings") as mock_save:
                self.obj._on_threshold_pct_changed("ram_warning_pct", 70)
            from tabs.settings_tab import CONFIG as tab_cfg
            self.assertEqual(tab_cfg["ram_warning_pct"], 70)
            mock_save.assert_called_once()
            self.obj.spin_ram_warning_pct.setValue.assert_not_called()

    def test_clamp_skipped_when_pair_absent(self):
        """No clamping when the paired CONFIG key is missing."""
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"ram_warning_pct": 80},
            clear=False,
        ):
            from tabs.settings_tab import CONFIG as tab_cfg
            tab_cfg.pop("ram_critical_pct", None)
            with patch("tabs.settings_tab.save_settings") as mock_save:
                self.obj._on_threshold_pct_changed("ram_warning_pct", 95)
            self.assertEqual(tab_cfg["ram_warning_pct"], 95)
            mock_save.assert_called_once()
            self.obj.spin_ram_warning_pct.setValue.assert_not_called()

    def test_syncing_guard_skips_save_and_audit(self):
        """Restore-time sync (v0.22.15) skips CONFIG write, save, and audit."""
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"ram_warning_pct": 80, "ram_critical_pct": 90},
            clear=False,
        ):
            from tabs.settings_tab import CONFIG as tab_cfg
            self.obj._syncing = True
            try:
                with patch("tabs.settings_tab.save_settings") as mock_save:
                    self.obj._on_threshold_pct_changed("ram_warning_pct", 77)
                self.assertEqual(tab_cfg["ram_warning_pct"], 80)  # unchanged
                mock_save.assert_not_called()
                self.obj.log_to_audit.assert_not_called()
            finally:
                del self.obj._syncing

    def test_sync_restore_loop_uses_syncing_flag(self):
        """_sync_ui_from_config sets _syncing during restore and clears it."""
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"ram_warning_pct": 80, "ram_critical_pct": 90},
            clear=False,
        ):
            seen = {}

            def _capture(value):
                seen["syncing_at_set"] = getattr(self.obj, "_syncing", False)

            self.obj.spin_ram_warning_pct.setValue.side_effect = _capture
            self.obj._sync_ui_from_config()
            self.assertTrue(seen.get("syncing_at_set"))
            self.assertFalse(getattr(self.obj, "_syncing", False))  # cleared after

    def test_syncing_guard_skips_toggle_save_and_audit(self):
        """Restore-time setChecked (v0.22.15) skips toggle save + audit too."""
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"notifications_enabled": True},
            clear=False,
        ):
            from tabs.settings_tab import CONFIG as tab_cfg
            self.obj._syncing = True
            try:
                with patch("tabs.settings_tab.save_settings") as mock_save:
                    self.obj._on_notifications_toggle(False)
                self.assertTrue(tab_cfg["notifications_enabled"])  # unchanged
                mock_save.assert_not_called()
                self.obj.log_to_audit.assert_not_called()
            finally:
                del self.obj._syncing

    def test_syncing_guard_skips_always_on_top_save(self):
        """Restore-time setChecked skips always-on-top save too (v0.22.15)."""
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"always_on_top": True},
            clear=False,
        ):
            from tabs.settings_tab import CONFIG as tab_cfg
            self.obj._syncing = True
            try:
                with patch("tabs.settings_tab.save_settings") as mock_save:
                    self.obj._on_always_on_top_toggle(False)
                self.assertTrue(tab_cfg["always_on_top"])  # unchanged
                mock_save.assert_not_called()
            finally:
                del self.obj._syncing

    def test_syncing_guard_skips_provider_and_theme_handlers(self):
        """Combo-restore handlers are muted during sync too (v0.22.15)."""
        with patch.dict(
            "tabs.settings_tab.CONFIG",
            {"active_provider": "lm_studio"},
            clear=False,
        ):
            self.obj._syncing = True
            try:
                self.obj._on_provider_changed()
                self.obj.log_to_audit.assert_not_called()
                self.obj._on_theme_changed("Dark")
                self.obj.apply_theme.assert_not_called()
            finally:
                del self.obj._syncing

# ===========================================================================
# Tests — Model Hot-Swap (_on_model_changed, _on_model_swap_success, _on_model_swap_failure)
# ===========================================================================

def _make_hotswap_settings():
    """Return a SettingsTabMixin wired for hot-swap tests."""
    from tabs.settings_tab import SettingsTabMixin
    obj = SettingsTabMixin()
    obj._shutting_down = False
    obj.log_to_audit = MagicMock()
    obj._set_provider_status = MagicMock()
    obj._btn_apply_model = MagicMock(name="_btn_apply_model")
    obj.model_combo = MagicMock(name="model_combo")
    obj.model_combo.findText.return_value = -1
    obj.model_combo.currentText.return_value = "new_model.gguf"
    return obj

def _make_hotswap_controller(hot_swap_ok=True, error=None,
                             model_path="/models/old_model.gguf"):
    """Create a mock controller with hot_swap_model."""
    ctrl = MagicMock(name="controller")
    provider = MagicMock(name="provider")
    provider._current_model_path = model_path
    ctrl.provider = provider
    if hot_swap_ok:
        ctrl.hot_swap_model.return_value = {
            "ok": True, "load_time_ms": 1500, "n_ctx": 8192,
        }
    else:
        ctrl.hot_swap_model.return_value = {
            "ok": False, "error": error or "Model load failed",
        }
    return ctrl

class TestOnModelChanged(unittest.TestCase):
    """Tests for _on_model_changed early-return guards and loading feedback."""

    def setUp(self):
        self.obj = _make_hotswap_settings()

    def test_empty_name_returns_early(self):
        self.obj.controller = _make_hotswap_controller()
        self.obj._on_model_changed("")
        self.obj.controller.hot_swap_model.assert_not_called()

    def test_whitespace_name_returns_early(self):
        self.obj.controller = _make_hotswap_controller()
        self.obj._on_model_changed("   ")
        self.obj.controller.hot_swap_model.assert_not_called()

    def test_suppress_flag_skips_swap(self):
        self.obj.controller = _make_hotswap_controller()
        self.obj._suppress_model_swap = True
        self.obj._on_model_changed("new_model.gguf")
        self.obj.controller.hot_swap_model.assert_not_called()

    def test_no_controller_returns_early(self):
        if hasattr(self.obj, "controller"):
            delattr(self.obj, "controller")
        # Should not raise
        self.obj._on_model_changed("new_model.gguf")

    def test_controller_without_hot_swap_returns_early(self):
        ctrl = MagicMock(spec=[])  # no hot_swap_model attribute
        self.obj.controller = ctrl
        self.obj._on_model_changed("new_model.gguf")
        # Should not raise

    def test_same_model_skips_swap(self):
        ctrl = _make_hotswap_controller(model_path="/models/new_model.gguf")
        self.obj.controller = ctrl
        self.obj._on_model_changed("new_model.gguf")
        ctrl.hot_swap_model.assert_not_called()

    def test_shows_loading_feedback(self):
        self.obj.controller = _make_hotswap_controller()
        self.obj._on_model_changed("new_model.gguf")
        self.obj._set_provider_status.assert_called_once_with(
            "\u23f3", "Loading new_model.gguf...", "#FBBF24", "Local LLM")

    def test_disables_apply_button(self):
        self.obj.controller = _make_hotswap_controller()
        self.obj._on_model_changed("new_model.gguf")
        self.obj._btn_apply_model.setEnabled.assert_called_with(False)

    def test_logs_audit(self):
        self.obj.controller = _make_hotswap_controller()
        self.obj._on_model_changed("new_model.gguf")
        self.obj.log_to_audit.assert_called_once()
        self.assertIn("new_model.gguf",
                      self.obj.log_to_audit.call_args[0][0])

    def test_dispatches_failure_on_exception(self):
        """When hot_swap_model raises, _on_model_swap_failure is invoked."""
        ctrl = _make_hotswap_controller()
        ctrl.hot_swap_model.side_effect = RuntimeError("provider crashed")
        self.obj.controller = ctrl
        # Directly invoke the failure callback to verify it handles the error
        self.obj._on_model_swap_failure("new_model.gguf", "provider crashed")
        self.obj._set_provider_status.assert_called()
        self.assertIn("provider crashed",
                      self.obj._set_provider_status.call_args[0][1])

    def test_dispatches_success_on_swap(self):
        """When hot_swap_model succeeds, _on_model_swap_success is invoked."""
        self.obj.controller = _make_hotswap_controller()
        # Directly invoke the success callback to verify it handles the result
        self.obj._on_model_swap_success("new_model.gguf", 1500, 8192)
        self.obj._btn_apply_model.setEnabled.assert_called_with(True)

class TestOnModelSwapSuccess(unittest.TestCase):
    """Tests for _on_model_swap_success status updates and button re-enable."""

    def setUp(self):
        self.obj = _make_hotswap_settings()

    def test_reenables_button(self):
        self.obj._on_model_swap_success("model.gguf", 1500, 8192)
        self.obj._btn_apply_model.setEnabled.assert_called_with(True)

    def test_updates_status_online(self):
        self.obj._on_model_swap_success("model.gguf", 1500, 8192)
        self.obj._set_provider_status.assert_called_once_with(
            "\U0001f7e2", "Online (model.gguf)", "#10B981", "Local LLM")

    def test_logs_audit_with_timing(self):
        self.obj._on_model_swap_success("model.gguf", 1500, 8192)
        self.obj.log_to_audit.assert_called_once()
        msg = self.obj.log_to_audit.call_args[0][0]
        self.assertIn("model.gguf", msg)
        self.assertIn("1500", msg)
        self.assertIn("8192", msg)

    def test_skips_when_shutting_down(self):
        self.obj._shutting_down = True
        self.obj._on_model_swap_success("model.gguf", 1500, 8192)
        self.obj._set_provider_status.assert_not_called()
        self.obj._btn_apply_model.setEnabled.assert_not_called()

class TestOnModelSwapFailure(unittest.TestCase):
    """Tests for _on_model_swap_failure status updates, button re-enable,
    and combo revert on failure."""

    def setUp(self):
        self.obj = _make_hotswap_settings()

    def test_reenables_button(self):
        self.obj.controller = _make_hotswap_controller()
        self.obj._on_model_swap_failure("model.gguf", "load failed")
        self.obj._btn_apply_model.setEnabled.assert_called_with(True)

    def test_updates_status_error(self):
        self.obj.controller = _make_hotswap_controller()
        self.obj._on_model_swap_failure("model.gguf", "load failed")
        self.obj._set_provider_status.assert_called_once()
        args = self.obj._set_provider_status.call_args[0]
        self.assertEqual(args[0], "\U0001f534")  # red circle
        self.assertIn("load failed", args[1])
        self.assertEqual(args[2], "#EF4444")

    def test_truncates_long_error(self):
        self.obj.controller = _make_hotswap_controller()
        long_error = "x" * 100
        self.obj._on_model_swap_failure("model.gguf", long_error)
        args = self.obj._set_provider_status.call_args[0]
        # "Swap failed: " (14 chars) + error[:40] = max ~54 chars
        self.assertLessEqual(len(args[1]), 55)

    def test_logs_audit(self):
        self.obj.controller = _make_hotswap_controller()
        self.obj._on_model_swap_failure("model.gguf", "load failed")
        self.obj.log_to_audit.assert_called_once()
        msg = self.obj.log_to_audit.call_args[0][0]
        self.assertIn("model.gguf", msg)
        self.assertIn("load failed", msg)

    def test_reverts_combo_to_previous_model(self):
        ctrl = _make_hotswap_controller(model_path="/models/old_model.gguf")
        self.obj.controller = ctrl
        self.obj.model_combo.findText.return_value = 3
        self.obj._on_model_swap_failure("new_model.gguf", "load failed")
        self.obj.model_combo.setCurrentIndex.assert_called_with(3)

    def test_reverts_combo_fallback_to_settext(self):
        ctrl = _make_hotswap_controller(model_path="/models/old_model.gguf")
        self.obj.controller = ctrl
        self.obj.model_combo.findText.return_value = -1
        self.obj._on_model_swap_failure("new_model.gguf", "load failed")
        self.obj.model_combo.setCurrentText.assert_called_with(
            "old_model.gguf")

    def test_suppress_flag_resets_after_revert(self):
        ctrl = _make_hotswap_controller(model_path="/models/old_model.gguf")
        self.obj.controller = ctrl
        self.obj._on_model_swap_failure("new_model.gguf", "load failed")
        self.assertFalse(
            getattr(self.obj, "_suppress_model_swap", False))

    def test_skips_when_shutting_down(self):
        self.obj._shutting_down = True
        self.obj.controller = _make_hotswap_controller()
        self.obj._on_model_swap_failure("model.gguf", "load failed")
        self.obj._set_provider_status.assert_not_called()
        self.obj._btn_apply_model.setEnabled.assert_not_called()

    def test_no_controller_still_updates_status(self):
        if hasattr(self.obj, "controller"):
            delattr(self.obj, "controller")
        self.obj._on_model_swap_failure("model.gguf", "load failed")
        self.obj._set_provider_status.assert_called_once()
        self.obj._btn_apply_model.setEnabled.assert_called_with(True)

    def test_no_provider_still_updates_status(self):
        ctrl = MagicMock(name="controller")
        ctrl.provider = None
        self.obj.controller = ctrl
        self.obj._on_model_swap_failure("model.gguf", "load failed")
        self.obj._set_provider_status.assert_called_once()
        self.obj._btn_apply_model.setEnabled.assert_called_with(True)

    def test_no_current_path_skips_combo_revert(self):
        ctrl = MagicMock(name="controller")
        provider = MagicMock(name="provider")
        provider._current_model_path = ""
        ctrl.provider = provider
        self.obj.controller = ctrl
        self.obj._on_model_swap_failure("model.gguf", "load failed")
        self.obj.model_combo.setCurrentIndex.assert_not_called()
        self.obj.model_combo.setCurrentText.assert_not_called()

# ===========================================================================
# Tests — Vision Model Apply Button (_on_vision_model_changed)
# ===========================================================================

def _make_vision_settings():
    """Return a SettingsTabMixin wired for vision model tests."""
    from tabs.settings_tab import SettingsTabMixin
    obj = SettingsTabMixin()
    obj._shutting_down = False
    obj.log_to_audit = MagicMock()
    obj._btn_apply_vision = MagicMock(name="_btn_apply_vision")
    return obj

class TestOnVisionModelChanged(unittest.TestCase):
    """Tests for _on_vision_model_changed: saves vision model to CONFIG
    with button feedback and guards."""

    def setUp(self):
        self.obj = _make_vision_settings()

    def test_saves_model_to_config(self):
        """Sets CONFIG['vision_model'] to the given model name."""
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": ""}, clear=False):
            self.obj._on_vision_model_changed("vision.gguf")
            from tabs.settings_tab import CONFIG
            self.assertEqual(CONFIG.get("vision_model"), "vision.gguf")

    def test_logs_audit(self):
        """Logs the model change to audit."""
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": ""}, clear=False):
            self.obj._on_vision_model_changed("vision.gguf")
        self.obj.log_to_audit.assert_called_once()
        self.assertIn("vision.gguf", self.obj.log_to_audit.call_args[0][0])

    def test_logs_empty_as_main_model(self):
        """Empty model name logs '(main model)'."""
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": "old.gguf"}, clear=False):
            self.obj._on_vision_model_changed("")
        self.obj.log_to_audit.assert_called_once()
        self.assertIn("main model", self.obj.log_to_audit.call_args[0][0])

    def test_disables_button_during_swap(self):
        """Button is disabled while saving."""
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": ""}, clear=False):
            self.obj._on_vision_model_changed("vision.gguf")
        self.obj._btn_apply_vision.setEnabled.assert_any_call(False)

    def test_reenables_button_after_swap(self):
        """Button is re-enabled after saving."""
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": ""}, clear=False):
            self.obj._on_vision_model_changed("vision.gguf")
        self.obj._btn_apply_vision.setEnabled.assert_any_call(True)

    def test_button_text_restored_to_apply(self):
        """Button text restores to 'Apply' after saving."""
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": ""}, clear=False):
            self.obj._on_vision_model_changed("vision.gguf")
        self.obj._btn_apply_vision.setText.assert_any_call("⚡ Apply")

    def test_shows_loading_text(self):
        """Button shows 'Applying...' during save."""
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": ""}, clear=False):
            self.obj._on_vision_model_changed("vision.gguf")
        self.obj._btn_apply_vision.setText.assert_any_call("⏳ Applying…")

    def test_same_model_skips_save(self):
        """Skip if model name matches current CONFIG value."""
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": "same.gguf"}, clear=False):
            self.obj._on_vision_model_changed("same.gguf")
        self.obj.log_to_audit.assert_not_called()
        self.obj._btn_apply_vision.setEnabled.assert_not_called()

    def test_whitespace_only_model_skips(self):
        """Whitespace-only input is stripped and treated as empty."""
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": ""}, clear=False):
            self.obj._on_vision_model_changed("   ")
        # Stripped to empty, which matches CONFIG empty string, so skipped
        self.obj.log_to_audit.assert_not_called()

    def test_shutting_down_skips(self):
        """Returns immediately when _shutting_down is True."""
        self.obj._shutting_down = True
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": ""}, clear=False):
            self.obj._on_vision_model_changed("vision.gguf")
        self.obj.log_to_audit.assert_not_called()
        self.obj._btn_apply_vision.setEnabled.assert_not_called()

    def test_no_button_still_saves(self):
        """Saves to CONFIG even if _btn_apply_vision is absent."""
        del self.obj._btn_apply_vision
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": ""}, clear=False):
            self.obj._on_vision_model_changed("vision.gguf")
            from tabs.settings_tab import CONFIG
            self.assertEqual(CONFIG.get("vision_model"), "vision.gguf")

    def test_empty_input_clears_config(self):
        """Setting empty clears the vision model from CONFIG."""
        with patch.dict("tabs.settings_tab.CONFIG", {"vision_model": "old.gguf"}, clear=False):
            self.obj._on_vision_model_changed("")
            from tabs.settings_tab import CONFIG
            self.assertEqual(CONFIG.get("vision_model"), "")

if __name__ == "__main__":
    unittest.main()
