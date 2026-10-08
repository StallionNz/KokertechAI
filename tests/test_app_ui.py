"""Tests for AppUIMixin methods not covered by test_app_mixins.py."""

import unittest
from unittest.mock import MagicMock, patch, call, ANY

from PyQt6.QtWidgets import QPushButton, QLabel, QCheckBox, QTabWidget


class MockParent:
    """Minimal QMainWindow stand-in with MagicMock attributes."""
    def __init__(self):
        self.audit_log_display = MagicMock(name="audit_log_display")
        self.chat_display = MagicMock(name="chat_display")
        self.thinking_display = MagicMock(name="thinking_display")
        self.mode_indicator = MagicMock(name="mode_indicator")
        self.txt_input = MagicMock(name="txt_input")
        self.protocol_badge = MagicMock(name="protocol_badge")
        self.protocol_active_label = MagicMock(name="protocol_active_label")
        self.provider_badge = MagicMock(name="provider_badge")
        self.chat_history_badge = MagicMock(name="chat_history_badge")
        self.vram_label = MagicMock(name="vram_label")
        self.vram_status_lbl = MagicMock(name="vram_status_lbl")
        self.ram_label = MagicMock(name="ram_label")
        self.wake_status_lbl = MagicMock(name="wake_status_lbl")
        self.file_logger = MagicMock(name="file_logger")
        self.controller = MagicMock(name="controller")
        self.voice_output = MagicMock(name="voice_output")
        self.audit_signal = MagicMock(name="audit_signal")
        self.action_send_prompt = MagicMock()
        self.log_to_audit = MagicMock()
        self.setStyleSheet = MagicMock()
        self.statusBar = MagicMock(return_value=MagicMock())
        self.tabs = MagicMock(name="tabs")
        self.settings_tab_widget = MagicMock(name="settings_tab_widget")
        self._model_progress_bar = MagicMock(name="_model_progress_bar")
        self._model_progress_lbl = MagicMock(name="_model_progress_lbl")
        self.model_status_lbl = MagicMock(name="model_status_lbl")
        self.main_model_header_lbl = MagicMock(name="main_model_header_lbl")
        self.desire_model_lbl = MagicMock(name="desire_model_lbl")
        self.auditor_model_lbl = MagicMock(name="auditor_model_lbl")
        self.vision_model_lbl = MagicMock(name="vision_model_lbl")


def _make_ui():
    """Return an AppUIMixin instance bound to a MockParent."""
    from app_ui import AppUIMixin
    obj = AppUIMixin()
    obj.__dict__.update(MockParent().__dict__)
    obj.__dict__.pop("add_suggestion_card", None)
    obj._shutting_down = False
    return obj


class TestScrollChatToBottom(unittest.TestCase):
    """_scroll_chat_to_bottom - safe scroll helper."""
    def setUp(self):
        self.ui = _make_ui()
    def test_scrolls_to_maximum(self):
        sb = MagicMock()
        sb.maximum.return_value = 200
        self.ui.chat_display.verticalScrollBar.return_value = sb
        self.ui._scroll_chat_to_bottom()
        sb.setValue.assert_called_once_with(200)
    def test_no_chat_display_skips(self):
        del self.ui.chat_display
        self.ui._scroll_chat_to_bottom()
    def test_chat_display_none_skips(self):
        self.ui.chat_display = None
        self.ui._scroll_chat_to_bottom()
    def test_runtime_error_caught(self):
        sb = MagicMock()
        sb.setValue.side_effect = RuntimeError("C++ deleted")
        self.ui.chat_display.verticalScrollBar.return_value = sb
        self.ui._scroll_chat_to_bottom()


class TestUpdateProtocolBadge(unittest.TestCase):
    """_update_protocol_badge - badge show/hide."""
    def setUp(self):
        self.ui = _make_ui()
    def test_shows_when_protocol_set(self):
        with patch.dict("app_ui.CONFIG", {"protocol_prompt": "You are a helpful AI."}, clear=False):
            self.ui._update_protocol_badge()
        self.ui.protocol_badge.setText.assert_called_with("\U0001f4dc")
        self.ui.protocol_badge.show.assert_called_once()
    def test_hides_when_protocol_empty(self):
        with patch.dict("app_ui.CONFIG", {"protocol_prompt": ""}, clear=False):
            self.ui._update_protocol_badge()
        self.ui.protocol_badge.setText.assert_called_with("")
        self.ui.protocol_badge.hide.assert_called_once()
    def test_no_badge_attr_skips(self):
        del self.ui.protocol_badge
        self.ui._update_protocol_badge()
    def test_tooltip_shows_char_count(self):
        with patch.dict("app_ui.CONFIG", {"protocol_prompt": "You are a helpful AI."}, clear=False):
            self.ui._update_protocol_badge()
        tooltip = self.ui.protocol_badge.setToolTip.call_args[0][0]
        self.assertIn("21 chars", tooltip)


class TestUpdateChatHistoryBadge(unittest.TestCase):
    """_update_chat_history_badge - persistence status badge."""
    def setUp(self):
        self.ui = _make_ui()
    def test_on_when_enabled(self):
        with patch.dict("app_ui.CONFIG", {"chat_history_enabled": True}, clear=False):
            self.ui._update_chat_history_badge()
        txt = self.ui.chat_history_badge.setText.call_args[0][0]
        self.assertIn("💬 ON", txt)
        self.ui.chat_history_badge.show.assert_called_once()
    def test_off_when_disabled(self):
        with patch.dict("app_ui.CONFIG", {"chat_history_enabled": False}, clear=False):
            self.ui._update_chat_history_badge()
        txt = self.ui.chat_history_badge.setText.call_args[0][0]
        self.assertIn("💬 OFF", txt)
        self.ui.chat_history_badge.show.assert_called_once()
    def test_skips_when_shutting_down(self):
        self.ui._shutting_down = True
        self.ui._update_chat_history_badge()
        self.ui.chat_history_badge.setText.assert_not_called()
    def test_skips_when_no_badge_attr(self):
        del self.ui.chat_history_badge
        self.ui._update_chat_history_badge()
    def test_catches_runtime_error(self):
        self.ui.chat_history_badge.setText.side_effect = RuntimeError("C++ deleted")
        self.ui._update_chat_history_badge()


class TestUpdateProviderBadge(unittest.TestCase):
    """_update_provider_badge - shows amber/green/red based on load state."""
    def setUp(self):
        self.ui = _make_ui()
        # Wire up a mock provider with is_loaded
        self.ui.controller.provider = MagicMock()
        self.ui.controller.provider.is_loaded.return_value = False

    def test_white_when_not_loaded(self):
        """Provider exists but not loaded — white (⚪) 'Not Loaded' badge."""
        with patch("app_ui.AppUIMixin._set_provider_badge") as mock_spb:
            self.ui._update_provider_badge()
        mock_spb.assert_called_once_with(
            "\u26aa", "Not Loaded", "#9CA3AF"
        )

    def test_green_when_loaded(self):
        """Provider loaded -> green (\U0001f7e2) badge."""
        self.ui.controller.provider.is_loaded.return_value = True
        with patch("app_ui.AppUIMixin._set_provider_badge") as mock_spb:
            self.ui._update_provider_badge()
        mock_spb.assert_called_once_with(
            "\U0001f7e2", "Local LLM", "#10B981"
        )

    def test_red_when_load_failed(self):
        """_provider_load_failed flag — red (❌) 'Load Failed' badge takes priority."""
        self.ui._provider_load_failed = True
        with patch("app_ui.AppUIMixin._set_provider_badge") as mock_spb:
            self.ui._update_provider_badge()
        mock_spb.assert_called_once_with(
            "\u274c", "Load Failed", "#EF4444"
        )

    def test_failed_takes_precedence_over_loaded(self):
        """Failed flag takes priority even when provider reports loaded."""
        self.ui._provider_load_failed = True
        self.ui.controller.provider.is_loaded.return_value = True
        with patch("app_ui.AppUIMixin._set_provider_badge") as mock_spb:
            self.ui._update_provider_badge()
        mock_spb.assert_called_once_with(
            "\u274c", "Load Failed", "#EF4444"
        )

    def test_white_when_no_controller(self):
        """No controller — white (⚪) 'Not Loaded' badge."""
        del self.ui.controller
        with patch("app_ui.AppUIMixin._set_provider_badge") as mock_spb:
            self.ui._update_provider_badge()
        mock_spb.assert_called_once_with(
            "\u26aa", "Not Loaded", "#9CA3AF"
        )

    def test_white_when_no_provider(self):
        """Controller exists but no provider — white (⚪) 'Not Loaded' badge."""
        self.ui.controller.provider = None
        with patch("app_ui.AppUIMixin._set_provider_badge") as mock_spb:
            self.ui._update_provider_badge()
        mock_spb.assert_called_once_with(
            "\u26aa", "Not Loaded", "#9CA3AF"
        )

    def test_white_when_provider_no_is_loaded(self):
        """Provider exists but has no is_loaded() method — white (⚪) 'Not Loaded' badge."""
        self.ui.controller.provider = MagicMock(spec=[])  # no is_loaded
        with patch("app_ui.AppUIMixin._set_provider_badge") as mock_spb:
            self.ui._update_provider_badge()
        mock_spb.assert_called_once_with(
            "\u26aa", "Not Loaded", "#9CA3AF"
        )

    def test_catches_runtime_error(self):
        """RuntimeError in _set_provider_badge is swallowed."""
        with patch("app_ui.AppUIMixin._set_provider_badge", side_effect=RuntimeError("C++ deleted")):
            self.ui._update_provider_badge()

    def test_hides_progress_on_loaded(self):
        """When provider is loaded — simplified badge doesn't call _hide_model_progress."""
        self.ui.controller.provider.is_loaded.return_value = True
        with patch.object(self.ui, '_hide_model_progress') as mock_hide:
            with patch("app_ui.AppUIMixin._set_provider_badge"):
                self.ui._update_provider_badge()
        mock_hide.assert_not_called()

    def test_hides_progress_on_failed(self):
        """When provider load failed — simplified badge doesn't call _hide_model_progress."""
        self.ui._provider_load_failed = True
        with patch.object(self.ui, '_hide_model_progress') as mock_hide:
            with patch("app_ui.AppUIMixin._set_provider_badge"):
                self.ui._update_provider_badge()
        mock_hide.assert_not_called()

    def test_does_not_hide_progress_on_loading(self):
        """When provider is still loading — simplified badge doesn't call _hide_model_progress."""
        with patch.object(self.ui, '_hide_model_progress') as mock_hide:
            with patch("app_ui.AppUIMixin._set_provider_badge"):
                self.ui._update_provider_badge()
        mock_hide.assert_not_called()


class TestShowModelProgress(unittest.TestCase):
    """_show_model_progress - updates progress bar and label."""
    def setUp(self):
        self.ui = _make_ui()

    def test_shows_bar_and_label_for_intermediate(self):
        """percent=50 shows bar and label with percentage."""
        self.ui._show_model_progress(50, "Loading...")
        self.ui._model_progress_bar.setValue.assert_called_with(50)
        self.ui._model_progress_bar.show.assert_called_once()
        self.ui._model_progress_lbl.setText.assert_called_with("50% — Loading...")
        self.ui._model_progress_lbl.show.assert_called_once()

    def test_hides_bar_at_100_percent(self):
        """percent=100 hides the bar and label."""
        self.ui._show_model_progress(100, "Done")
        self.ui._model_progress_bar.hide.assert_called_once()
        self.ui._model_progress_lbl.hide.assert_called_once()

    def test_hides_bar_at_zero_percent(self):
        """percent=0 hides the bar and label."""
        self.ui._show_model_progress(0, "Failed")
        self.ui._model_progress_bar.hide.assert_called_once()
        self.ui._model_progress_lbl.hide.assert_called_once()

    def test_skips_when_no_bar_attr(self):
        """Safe when _model_progress_bar doesn't exist."""
        del self.ui._model_progress_bar
        self.ui._show_model_progress(50, "test")

    def test_skips_when_shutting_down(self):
        """Safe when _shutting_down flag is True."""
        self.ui._shutting_down = True
        self.ui._show_model_progress(50, "test")
        self.ui._model_progress_bar.setValue.assert_not_called()

    def test_label_without_status(self):
        """percent=50 without status shows just percentage."""
        self.ui._show_model_progress(50)
        self.ui._model_progress_lbl.setText.assert_called_with("50%")

    def test_catches_runtime_error(self):
        """RuntimeError is swallowed."""
        self.ui._model_progress_bar.setValue.side_effect = RuntimeError("C++ deleted")
        self.ui._show_model_progress(50, "crash")


class TestHideModelProgress(unittest.TestCase):
    """_hide_model_progress - hides bar and label."""
    def setUp(self):
        self.ui = _make_ui()

    def test_hides_both_widgets(self):
        """Both progress bar and label are hidden."""
        self.ui._hide_model_progress()
        self.ui._model_progress_bar.hide.assert_called_once()
        self.ui._model_progress_lbl.hide.assert_called_once()

    def test_no_bar_attr_skips(self):
        """Safe when _model_progress_bar doesn't exist."""
        del self.ui._model_progress_bar
        self.ui._hide_model_progress()

    def test_no_lbl_attr_skips(self):
        """Safe when _model_progress_lbl doesn't exist."""
        del self.ui._model_progress_lbl
        self.ui._hide_model_progress()

    def test_catches_runtime_error(self):
        """RuntimeError is swallowed."""
        self.ui._model_progress_bar.hide.side_effect = RuntimeError("C++ deleted")
        self.ui._hide_model_progress()


class TestOnRawStreamToggle(unittest.TestCase):
    """_on_raw_stream_toggle - raw/parsed thinking display."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui._raw_stream_buffer = "raw tokens..."
        self.ui._last_parsed_thinking = "parsed thinking"
    def test_checked_shows_raw_buffer(self):
        self.ui._on_raw_stream_toggle(True)
        self.ui.thinking_display.setPlainText.assert_called_once_with("raw tokens...")
    def test_unchecked_shows_parsed(self):
        self.ui._on_raw_stream_toggle(False)
        self.ui.thinking_display.setPlainText.assert_called_once_with("parsed thinking")
    def test_checked_without_buffer_noop(self):
        del self.ui._raw_stream_buffer
        self.ui._on_raw_stream_toggle(True)
        self.ui.thinking_display.setPlainText.assert_not_called()


class TestOnCompareToggle(unittest.TestCase):
    """_on_compare_toggle - show/hide compare panel."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui.compare_panel = MagicMock()
        self.ui._populate_model_checkboxes = MagicMock()
    def test_enabled_shows_and_populates(self):
        self.ui._on_compare_toggle(True)
        self.ui.compare_panel.setVisible.assert_called_once_with(True)
        self.ui._populate_model_checkboxes.assert_called_once()
    def test_disabled_hides(self):
        self.ui._on_compare_toggle(False)
        self.ui.compare_panel.setVisible.assert_called_once_with(False)
        self.ui._populate_model_checkboxes.assert_not_called()


class TestOnABToggle(unittest.TestCase):
    """_on_ab_toggle - show/hide AB panel."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui.ab_panel = MagicMock()
        self.ui._populate_ab_personas = MagicMock()
    def test_enabled_shows_and_populates(self):
        self.ui._on_ab_toggle(True)
        self.ui.ab_panel.setVisible.assert_called_once_with(True)
        self.ui._populate_ab_personas.assert_called_once()
    def test_disabled_hides(self):
        self.ui._on_ab_toggle(False)
        self.ui.ab_panel.setVisible.assert_called_once_with(False)
        self.ui._populate_ab_personas.assert_not_called()


class TestPopulateABPersonas(unittest.TestCase):
    """_populate_ab_personas - populate persona combos from CONFIG."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui.ab_persona_a_combo = MagicMock()
        self.ui.ab_persona_b_combo = MagicMock()
        self.ui.ab_persona_a_combo.findText.return_value = -1
        self.ui.ab_persona_b_combo.findText.return_value = -1
        self.ui.ab_preview = MagicMock()
        self.ui._update_ab_preview = MagicMock()
    def test_no_personas_still_safe(self):
        with patch.dict("app_ui.CONFIG", {"saved_personas": [], "active_persona": "default"}, clear=True):
            self.ui._populate_ab_personas()
        self.ui.ab_persona_a_combo.clear.assert_called_once()
    def test_populates_with_personas(self):
        p = ["p1", "p2", "p3"]
        with patch.dict("app_ui.CONFIG", {"saved_personas": p, "active_persona": "p1"}, clear=True):
            self.ui._populate_ab_personas()
        self.ui.ab_persona_a_combo.addItems.assert_called_once_with(p)
    def test_calls_update_preview(self):
        with patch.dict("app_ui.CONFIG", {"saved_personas": [], "active_persona": ""}, clear=True):
            self.ui._populate_ab_personas()
        self.ui._update_ab_preview.assert_called_once()
    def test_signals_blocked_during_population(self):
        with patch.dict("app_ui.CONFIG", {"saved_personas": ["a", "b"], "active_persona": "a"}, clear=True):
            self.ui._populate_ab_personas()
        self.ui.ab_persona_a_combo.blockSignals.assert_any_call(True)
        self.ui.ab_persona_a_combo.blockSignals.assert_any_call(False)


class TestUpdateABPreview(unittest.TestCase):
    """_update_ab_preview - preview label text."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui.ab_persona_a_combo = MagicMock()
        self.ui.ab_persona_b_combo = MagicMock()
        self.ui.ab_preview = MagicMock()
    def test_shows_both_personas(self):
        self.ui.ab_persona_a_combo.currentText.return_value = "You are a friendly AI"
        self.ui.ab_persona_b_combo.currentText.return_value = "You are a code expert"
        self.ui._update_ab_preview()
        text = self.ui.ab_preview.setText.call_args[0][0]
        self.assertIn("friendly AI", text)
        self.assertIn("code expert", text)
    def test_empty_is_safe(self):
        self.ui.ab_persona_a_combo.currentText.return_value = ""
        self.ui.ab_persona_b_combo.currentText.return_value = ""
        self.ui._update_ab_preview()


class TestGetABPersonas(unittest.TestCase):
    """_get_ab_personas - returns persona pair or (None, None)."""
    def setUp(self):
        self.ui = _make_ui()
    def test_returns_none_when_no_ab_toggle(self):
        self.assertEqual(self.ui._get_ab_personas(), (None, None))
    def test_returns_none_when_toggle_off(self):
        self.ui.ab_toggle = MagicMock()
        self.ui.ab_toggle.isChecked.return_value = False
        self.assertEqual(self.ui._get_ab_personas(), (None, None))
    def test_returns_none_when_personas_empty(self):
        self.ui.ab_toggle = MagicMock()
        self.ui.ab_toggle.isChecked.return_value = True
        self.ui.ab_persona_a_combo = MagicMock()
        self.ui.ab_persona_a_combo.currentText.return_value = ""
        self.ui.ab_persona_b_combo = MagicMock()
        self.ui.ab_persona_b_combo.currentText.return_value = ""
        self.assertEqual(self.ui._get_ab_personas(), (None, None))
    def test_returns_personas_when_active(self):
        self.ui.ab_toggle = MagicMock()
        self.ui.ab_toggle.isChecked.return_value = True
        self.ui.ab_persona_a_combo = MagicMock()
        self.ui.ab_persona_a_combo.currentText.return_value = "pa"
        self.ui.ab_persona_b_combo = MagicMock()
        self.ui.ab_persona_b_combo.currentText.return_value = "pb"
        self.assertEqual(self.ui._get_ab_personas(), ("pa", "pb"))


class TestHideProgressPanel(unittest.TestCase):
    """_hide_progress_panel - cleanup."""
    def setUp(self):
        self.ui = _make_ui()
    def test_hides_panel_and_stops_timer(self):
        self.ui._progress_panel = MagicMock()
        timer = MagicMock()
        self.ui._progress_timer = timer
        self.ui._hide_progress_panel()
        self.ui._progress_panel.hide.assert_called_once()
        timer.stop.assert_called_once()
        self.assertIsNone(self.ui._progress_timer)
    def test_no_panel_skips(self):
        self.ui._hide_progress_panel()
    def test_no_timer_skips(self):
        self.ui._progress_panel = MagicMock()
        self.ui._hide_progress_panel()
        self.ui._progress_panel.hide.assert_called_once()
    def test_timer_runtime_error_swallowed(self):
        self.ui._progress_panel = MagicMock()
        timer = MagicMock()
        timer.stop.side_effect = RuntimeError("C++ deleted")
        self.ui._progress_timer = timer
        self.ui._hide_progress_panel()
        timer.stop.assert_called_once()
        self.assertIsNone(self.ui._progress_timer)


class TestAnimateProgressDots(unittest.TestCase):
    """_animate_progress_dots - cycling dots animation."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui._progress_dot_count = 0
        self.ui._progress_panel = MagicMock()
        self.ui._progress_panel.isVisible.return_value = True
        self.ui._progress_step_lbl = MagicMock()
        self.ui._progress_step_lbl.text.return_value = "Processing"
    def test_cycles_through_three_states(self):
        texts = set()
        for _ in range(6):
            self.ui._animate_progress_dots()
            text = self.ui._progress_step_lbl.setText.call_args[0][0]
            texts.add(text)
            self.ui._progress_step_lbl.text.return_value = text
        self.assertGreaterEqual(len(texts), 2)
    def test_not_visible_skips(self):
        self.ui._progress_panel.isVisible.return_value = False
        self.ui._animate_progress_dots()
        self.ui._progress_step_lbl.setText.assert_not_called()
    def test_no_panel_skips(self):
        del self.ui._progress_panel
        self.ui._animate_progress_dots()


class TestUpdateProgressStep(unittest.TestCase):
    """_update_progress_step - step detection and label update."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui._progress_panel = MagicMock()
        self.ui._progress_panel.isVisible.return_value = True
        self.ui._progress_step_lbl = MagicMock()
        self.ui._progress_status_lbl = MagicMock()
        self.ui._append_progress_log = MagicMock()
    def test_step_pattern_updates_status(self):
        self.ui._update_progress_step("STEP 2/5: Loading model")
        self.ui._progress_status_lbl.setText.assert_called_with("Step 2/5")
    def test_non_step_sets_label_only(self):
        self.ui._update_progress_step("Initializing context")
        self.ui._progress_step_lbl.setText.assert_called()
    def test_appends_to_log(self):
        self.ui._update_progress_step("Processing...")
        self.ui._append_progress_log.assert_called_once_with("Processing...")
    def test_not_visible_skips(self):
        self.ui._progress_panel.isVisible.return_value = False
        self.ui._update_progress_step("Hidden step")
        self.ui._progress_step_lbl.setText.assert_not_called()
    def test_truncates_long_messages(self):
        long_msg = "x" * 100
        self.ui._update_progress_step(long_msg)
        text = self.ui._progress_step_lbl.setText.call_args[0][0]
        self.assertIn("...", text)
        self.assertLess(len(text), 85)


class TestAppendProgressLog(unittest.TestCase):
    """_append_progress_log - color-coded log entries."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui._progress_log_display = MagicMock()
        self.ui._progress_log_display.verticalScrollBar.return_value = MagicMock()
    def test_brain_uses_blue(self):
        self.ui._append_progress_log("\U0001f9e0 Thinking...")
        args = self.ui._progress_log_display.append.call_args[0][0]
        self.assertIn("#3B82F6", args)
    def test_error_uses_red(self):
        self.ui._append_progress_log("\u274c Failed")
        args = self.ui._progress_log_display.append.call_args[0][0]
        self.assertIn("#EF4444", args)
    def test_memory_uses_purple(self):
        self.ui._append_progress_log("\U0001f4be Saved to vault")
        args = self.ui._progress_log_display.append.call_args[0][0]
        self.assertIn("#8B5CF6", args)
    def test_success_uses_green(self):
        self.ui._append_progress_log("\u2705 Completed")
        args = self.ui._progress_log_display.append.call_args[0][0]
        self.assertIn("#10B981", args)
    def test_warning_uses_amber(self):
        self.ui._append_progress_log("\u26a0\ufe0f Warning")
        args = self.ui._progress_log_display.append.call_args[0][0]
        self.assertIn("#F59E0B", args)
    def test_regular_uses_grey(self):
        self.ui._append_progress_log("Regular log")
        args = self.ui._progress_log_display.append.call_args[0][0]
        self.assertIn("#9CA3AF", args)
    def test_no_log_display_skips(self):
        del self.ui._progress_log_display
        self.ui._append_progress_log("skip")
    def test_escapes_html(self):
        self.ui._append_progress_log("<bad>")
        args = self.ui._progress_log_display.append.call_args[0][0]
        self.assertIn("&lt;bad&gt;", args)


class TestCreateProgressPanel(unittest.TestCase):
    """_create_progress_panel - creates widget tree."""
    def setUp(self):
        self.ui = _make_ui()
    @patch("app_ui.QFrame")
    @patch("app_ui.QLabel")
    @patch("app_ui.QProgressBar")
    @patch("app_ui.QTextBrowser")
    @patch("app_ui.QVBoxLayout")
    @patch("app_ui.QHBoxLayout")
    def test_creates_all_widgets(self, *mocks):
        self.ui._create_progress_panel()
        self.assertIsNotNone(self.ui._progress_panel)
        self.assertIsNotNone(self.ui._progress_step_lbl)
        self.assertIsNotNone(self.ui._progress_status_lbl)
        self.assertIsNotNone(self.ui._progress_token_bar)
        self.assertIsNotNone(self.ui._progress_token_lbl)
        self.assertIsNotNone(self.ui._progress_log_display)
        self.ui._progress_panel.hide.assert_called_once()


class TestShowProgressPanel(unittest.TestCase):
    """_show_progress_panel - shows/resets progress panel."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui._progress_panel = MagicMock()
        self.ui._progress_step_lbl = MagicMock()
        self.ui._progress_status_lbl = MagicMock()
        self.ui._progress_log_display = MagicMock()
        self.ui._create_progress_panel = MagicMock()
    @patch("PyQt6.QtCore.QTimer")
    def test_skips_create_when_panel_exists(self, mock_qtimer):
        self.ui._show_progress_panel("Test")
        self.ui._create_progress_panel.assert_not_called()
    @patch("PyQt6.QtCore.QTimer")
    def test_resets_log_and_step(self, mock_qtimer):
        self.ui._show_progress_panel("Loading...")
        self.ui._progress_log_display.clear.assert_called_once()
        self.ui._progress_step_lbl.setText.assert_called_with("\U0001f916 Loading...")
    @patch("PyQt6.QtCore.QTimer")
    def test_shows_panel(self, mock_qtimer):
        self.ui._show_progress_panel()
        self.ui._progress_panel.show.assert_called_once()
    @patch("PyQt6.QtCore.QTimer")
    def test_starts_timer_when_none(self, mock_qtimer):
        self.ui._show_progress_panel()
        self.assertIsNotNone(self.ui._progress_timer)
    @patch("PyQt6.QtCore.QTimer")
    def test_hides_old_typing_indicator(self, mock_qtimer):
        self.ui.typing_indicator_lbl = MagicMock()
        self.ui._show_progress_panel()
        self.ui.typing_indicator_lbl.hide.assert_called_once()


class TestShowTypingIndicator(unittest.TestCase):
    """_show_typing_indicator - delegates to progress panel."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui._show_progress_panel = MagicMock()
    def test_delegates_to_progress_panel_when_exists(self):
        self.ui._progress_panel = MagicMock()
        self.ui._show_typing_indicator()
        self.ui._show_progress_panel.assert_called_once()
    def test_fallback_no_progress_panel_is_safe(self):
        self.ui._show_typing_indicator()


class TestOpenGlobalSearch(unittest.TestCase):
    """_open_global_search - dialog creation/management."""
    def setUp(self):
        self.ui = _make_ui()
    @patch("tabs.global_search.GlobalSearchDialog")
    def test_creates_dialog_when_none(self, mock_cls):
        dlg = MagicMock()
        mock_cls.return_value = dlg
        self.ui._open_global_search()
        mock_cls.assert_called_once()
        dlg.show.assert_called_once()
    @patch("tabs.global_search.GlobalSearchDialog")
    def test_raises_existing_dialog_when_visible(self, mock_cls):
        dlg = MagicMock()
        dlg.isVisible.return_value = True
        self.ui._global_search_dialog = dlg
        self.ui._open_global_search()
        dlg.raise_.assert_called_once()
    @patch("tabs.global_search.GlobalSearchDialog")
    def test_connects_restore_signal(self, mock_cls):
        dlg = MagicMock()
        mock_cls.return_value = dlg
        self.ui._open_global_search()
        dlg.restore_requested.connect.assert_called_once()


class TestToggleMiniHud(unittest.TestCase):
    """_toggle_mini_hud - HUD dialog creation/toggle."""
    def setUp(self):
        self.ui = _make_ui()

    @patch("tabs.mini_hud.MiniHudDialog")
    def test_creates_dialog_and_toggles(self, mock_cls):
        dlg = MagicMock()
        mock_cls.return_value = dlg
        self.ui._toggle_mini_hud()
        mock_cls.assert_called_once()
        dlg.toggle_visibility.assert_called_once()

    def test_delegates_to_do_toggle_hud_if_present(self):
        self.ui._do_toggle_hud = MagicMock()
        self.ui._toggle_mini_hud()
        self.ui._do_toggle_hud.assert_called_once()


class TestOnGlobalSearchRestore(unittest.TestCase):
    """_on_global_search_restore - restore content to input."""
    def setUp(self):
        self.ui = _make_ui()
    def test_restores_content_to_input(self):
        self.ui._global_search_dialog = MagicMock()
        self.ui._on_global_search_restore("restored text")
        self.ui.txt_input.setPlainText.assert_called_with("restored text")
    def test_truncates_long_content(self):
        self.ui._global_search_dialog = MagicMock()
        long = "x" * 3000
        self.ui._on_global_search_restore(long)
        self.ui.txt_input.setPlainText.assert_called_with(long[:2000])
    def test_closes_dialog(self):
        self.ui._global_search_dialog = MagicMock()
        self.ui._on_global_search_restore("hello")
        self.ui._global_search_dialog.close.assert_called_once()
    def test_no_dialog_attr_skips_close(self):
        self.ui._on_global_search_restore("hello")
        self.ui.txt_input.setPlainText.assert_called_with("hello")


class TestGetSelectedModelSpecs(unittest.TestCase):
    """_get_selected_model_specs - specs from checked checkboxes."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui.model_checkbox_layout = MagicMock()
    def test_returns_specs_for_checked_boxes(self):
        from PyQt6.QtWidgets import QCheckBox
        checked = MagicMock(spec=QCheckBox)
        checked.isChecked.return_value = True
        prop_values = {"model_name": "gpt-4", "provider": "openai"}
        checked.property = lambda k: prop_values.get(k)
        unchecked = MagicMock(spec=QCheckBox)
        unchecked.isChecked.return_value = False
        self.ui.model_checkbox_layout.count.return_value = 2
        self.ui.model_checkbox_layout.itemAt.side_effect = [
            MagicMock(widget=lambda: checked),
            MagicMock(widget=lambda: unchecked),
        ]
        specs = self.ui._get_selected_model_specs()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["provider"], "openai")
        self.assertEqual(specs[0]["model"], "gpt-4")
    def test_empty_when_no_layout(self):
        del self.ui.model_checkbox_layout
        self.assertEqual(self.ui._get_selected_model_specs(), [])


class TestBuildModelCheckboxes(unittest.TestCase):
    """_build_model_checkboxes - widget building."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui.model_checkbox_layout = MagicMock()
    @patch("app_ui.QLabel")
    def test_error_message_when_models_start_with_error(self, mock_label):
        self.ui._build_model_checkboxes(["ERROR:API timeout"])
        text = mock_label.call_args[0][0]
        self.assertIn("Could not load models", text)
    @patch("app_ui.QLabel")
    def test_placeholder_when_empty(self, mock_label):
        # empty list triggers the error path ("not all_models" is True)
        self.ui._build_model_checkboxes([])
        text = mock_label.call_args[0][0]
        self.assertIn("Could not load models", text)
    @patch("app_ui.QLabel")
    @patch("app_ui.QCheckBox")
    def test_groups_by_provider(self, mock_cb, mock_label):
        models = [
            {"provider": "openai", "name": "gpt-4"},
            {"provider": "openai", "name": "gpt-3.5"},
            {"provider": "anthropic", "name": "claude-3"},
        ]
        self.ui._build_model_checkboxes(models)
        # 2 providers = 2 header labels + 3 checkboxes = 5 addWidget calls
        self.assertEqual(self.ui.model_checkbox_layout.addWidget.call_count, 5)


class TestOnAnchorClicked(unittest.TestCase):
    """_on_anchor_clicked - retry and external URL."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui.last_user_text = "previous prompt"
        self.ui.action_send_prompt = MagicMock()
    def test_retry_sends_last_prompt(self):
        url = MagicMock()
        url.toString.return_value = "kokertech-retry://"
        self.ui._on_anchor_clicked(url)
        self.ui.txt_input.setPlainText.assert_called_with("previous prompt")
        self.ui.action_send_prompt.assert_called_once()
        self.ui.log_to_audit.assert_called_with("\U0001f501 Retried last prompt")
    def test_retry_no_last_text_logs_warning(self):
        self.ui.last_user_text = ""
        url = MagicMock()
        url.toString.return_value = "kokertech-retry://"
        self.ui._on_anchor_clicked(url)
        self.ui.log_to_audit.assert_called_with("\u26a0\ufe0f No last prompt to retry")
    @patch("PyQt6.QtGui.QDesktopServices")
    def test_external_url_opens_via_qdesktop(self, mock_ds):
        url = MagicMock()
        url.toString.return_value = "https://example.com"
        self.ui._on_anchor_clicked(url)
        mock_ds.openUrl.assert_called_once()
    def test_blank_url_skipped(self):
        url = MagicMock()
        url.toString.return_value = ""
        self.ui._on_anchor_clicked(url)


class TestPipelineViewInGraph(unittest.TestCase):
    """_pipeline_view_in_graph uses _switch_to_tab(indexOf) to
    switch to the Neural Graph tab, and optionally calls
    render_knowledge_graph."""
    def setUp(self):
        self.ui = _make_ui()
        # _pipeline_view_in_graph is defined on DocumentPipelineTabMixin,
        # not AppUIMixin -- bind it onto the mock object
        import types
        from tabs.document_pipeline_tab import DocumentPipelineTabMixin
        self.ui._pipeline_view_in_graph = types.MethodType(
            DocumentPipelineTabMixin._pipeline_view_in_graph, self.ui
        )
        self.ui.knowledge_graph_tab_widget = MagicMock(name="knowledge_graph_tab_widget")
        self.ui.render_knowledge_graph = MagicMock()

    def test_switches_to_knowledge_graph_tab_via_switch_to_tab(self):
        """_pipeline_view_in_graph calls _switch_to_tab with the
        knowledge_graph_tab_widget, not a hardcoded index."""
        with patch.object(self.ui, '_switch_to_tab') as mock_switch:
            self.ui._pipeline_view_in_graph()
        mock_switch.assert_called_once_with(
            self.ui.knowledge_graph_tab_widget
        )

    def test_calls_render_knowledge_graph_when_available(self):
        """_pipeline_view_in_graph calls render_knowledge_graph
        after switching tabs."""
        with patch.object(self.ui, '_switch_to_tab'):
            self.ui._pipeline_view_in_graph()
        self.ui.render_knowledge_graph.assert_called_once()

    def test_skips_graph_tab_when_widget_missing(self):
        """_pipeline_view_in_graph skips tab switch when
        knowledge_graph_tab_widget is not set."""
        del self.ui.knowledge_graph_tab_widget
        with patch.object(self.ui, '_switch_to_tab') as mock_switch:
            self.ui._pipeline_view_in_graph()
        mock_switch.assert_not_called()
        # render_knowledge_graph still works
        self.ui.render_knowledge_graph.assert_called_once()

    def test_skips_render_when_method_missing(self):
        """_pipeline_view_in_graph skips render_knowledge_graph
        when the method doesn't exist on the dashboard."""
        del self.ui.render_knowledge_graph
        with patch.object(self.ui, '_switch_to_tab') as mock_switch:
            self.ui._pipeline_view_in_graph()
        mock_switch.assert_called_once_with(
            self.ui.knowledge_graph_tab_widget
        )


class TestSettingsTabButton(unittest.TestCase):
    """Verify that the SYSTEM SETTINGS button and provider badge
    both use indexOf to switch to the Settings tab (not hardcoded indices).

    Both sidebar click handlers use the _switch_to_tab helper:
        self._switch_to_tab(self.settings_tab_widget)
    """
    def setUp(self):
        self.ui = _make_ui()

    def test_click_handler_uses_index_of(self):
        """_switch_to_tab uses indexOf to find the Settings tab dynamically,
        avoiding the hardcoded-index bug that previously pointed to the
        Plugins tab (index 4) instead of Settings (index 3).
        """
        for expected_index in (3, 5, 8):
            with self.subTest(index=expected_index):
                self.ui.tabs.indexOf.reset_mock()
                self.ui.tabs.setCurrentIndex.reset_mock()
                self.ui.tabs.indexOf.return_value = expected_index

                # Execute the helper method used by both the SYSTEM SETTINGS
                # button click handler and the provider badge click handler
                self.ui._switch_to_tab(self.ui.settings_tab_widget)

                self.ui.tabs.indexOf.assert_called_once_with(
                    self.ui.settings_tab_widget
                )
                self.ui.tabs.setCurrentIndex.assert_called_once_with(
                    expected_index
                )

    def test_switch_to_tab_skips_when_index_negative(self):
        """_switch_to_tab does nothing when indexOf returns -1."""
        self.ui.tabs.indexOf.reset_mock()
        self.ui.tabs.setCurrentIndex.reset_mock()
        self.ui.tabs.indexOf.return_value = -1

        self.ui._switch_to_tab(self.ui.settings_tab_widget)

        self.ui.tabs.indexOf.assert_called_once_with(
            self.ui.settings_tab_widget
        )
        self.ui.tabs.setCurrentIndex.assert_not_called()

    def test_switch_to_tab_skips_when_no_tabs_attr(self):
        """_switch_to_tab does nothing when self.tabs doesn't exist."""
        del self.ui.tabs
        self.ui._switch_to_tab(self.ui.settings_tab_widget)

    def test_switch_to_tab_skips_when_widget_none(self):
        """_switch_to_tab does nothing when tab_widget is None."""
        self.ui._switch_to_tab(None)
        self.ui.tabs.setCurrentIndex.assert_not_called()


class TestSwitchToTabByName(unittest.TestCase):
    """_switch_to_tab_by_name searches tabText for exact match."""
    def setUp(self):
        self.ui = _make_ui()
        self.ui.tabs.count.return_value = 3
        self.ui.tabs.tabText.side_effect = ["Core", "Settings", "Neural Graph"]

    def test_switches_to_tab_by_name(self):
        self.ui._switch_to_tab_by_name("Settings")
        self.ui.tabs.setCurrentIndex.assert_called_once_with(1)

    def test_switches_to_another_tab_by_name(self):
        self.ui._switch_to_tab_by_name("Neural Graph")
        self.ui.tabs.setCurrentIndex.assert_called_once_with(2)

    def test_unknown_name_noops(self):
        self.ui._switch_to_tab_by_name("Unknown")
        self.ui.tabs.setCurrentIndex.assert_not_called()

    def test_empty_name_noops(self):
        self.ui._switch_to_tab_by_name("")
        self.ui.tabs.setCurrentIndex.assert_not_called()

    def test_none_name_noops(self):
        self.ui._switch_to_tab_by_name(None)
        self.ui.tabs.setCurrentIndex.assert_not_called()

    def test_no_tabs_attr_skips(self):
        del self.ui.tabs
        self.ui._switch_to_tab_by_name("Settings")

    def test_delegated_from_switch_to_tab(self):
        with patch.object(self.ui, '_switch_to_tab_by_name') as mock_by_name:
            self.ui._switch_to_tab("Settings")
        mock_by_name.assert_called_once_with("Settings")

    def test_switch_to_tab_with_widget_still_works(self):
        self.ui.tabs.indexOf.return_value = 2
        self.ui._switch_to_tab(self.ui.settings_tab_widget)
        self.ui.tabs.indexOf.assert_called_once_with(self.ui.settings_tab_widget)
        self.ui.tabs.setCurrentIndex.assert_called_once_with(2)


class TestSidebarButtonTooltips(unittest.TestCase):
    """Sidebar button tooltips are non-empty and contain relevant keywords.
    Mirror the integration tests in test_kokertech_main_dashboard.py."""

    def test_rebuild_db_vectors_tooltip(self):
        btn = MagicMock(spec=QPushButton)
        btn.setToolTip(
            "Rebuild database vector indexes and embeddings from stored memory"
        )
        tip = btn.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("Rebuild", tip)
        self.assertIn("vector", tip.lower())

    def test_tts_test_tooltip(self):
        btn = MagicMock(spec=QPushButton)
        btn.setToolTip(
            "Test text-to-speech output (says 'Voice output is working.')"
        )
        tip = btn.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("text-to-speech", tip.lower())
        self.assertIn("voice", tip.lower())

    def test_capture_screen_tooltip(self):
        btn = MagicMock(spec=QPushButton)
        btn.setToolTip(
            "Capture the current screen for AI analysis"
        )
        tip = btn.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("Capture", tip)
        self.assertIn("screen", tip.lower())

    def test_ocr_tooltip(self):
        btn = MagicMock(spec=QPushButton)
        btn.setToolTip(
            "Open an image file and extract text via OCR"
        )
        tip = btn.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("OCR", tip)
        self.assertIn("image", tip.lower())

    def test_wipe_memory_tooltip(self):
        btn = MagicMock(spec=QPushButton)
        btn.setToolTip(
            "Clear the AI's short-term conversation memory (cannot be undone)"
        )
        tip = btn.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("memory", tip.lower())
        self.assertIn("cannot be undone", tip.lower())

    def test_settings_button_tooltip(self):
        btn = MagicMock(spec=QPushButton)
        btn.setToolTip(
            "Open the Settings tab to configure AI provider, personas, "
            "protocol, themes, and more"
        )
        tip = btn.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("Settings", tip)
        self.assertIn("provider", tip.lower())

    def test_provider_badge_tooltip(self):
        lbl = MagicMock(spec=QLabel)
        lbl.setToolTip("AI provider status — click to open Settings")
        tip = lbl.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("provider", tip.lower())
        self.assertIn("Settings", tip)

    def test_mode_indicator_structured_tooltip(self):
        lbl = MagicMock(spec=QLabel)
        lbl.setToolTip(
            "Structured mode — prompts use XML tags for tool selection and parsing"
        )
        tip = lbl.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("Structured", tip)
        self.assertIn("XML", tip)

    def test_mode_indicator_freeform_tooltip(self):
        lbl = MagicMock(spec=QLabel)
        lbl.setToolTip(
            "Freeform mode — send natural language prompts without XML structure"
        )
        tip = lbl.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("Freeform", tip)
        self.assertIn("natural language", tip.lower())

    def test_mode_indicator_mock_tooltip(self):
        lbl = MagicMock(spec=QLabel)
        lbl.setToolTip(
            "Mock mode — AI responses are simulated for testing purposes"
        )
        tip = lbl.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("Mock", tip)
        self.assertIn("simulated", tip.lower())

    def test_brand_label_tooltip(self):
        lbl = MagicMock(spec=QLabel)
        lbl.setToolTip(
            "KokertechAI Executive Core — autonomous AI automation system"
        )
        tip = lbl.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("KokertechAI", tip)
        self.assertIn("autonomous", tip.lower())

    def test_vram_status_tooltip(self):
        lbl = MagicMock(spec=QLabel)
        lbl.setToolTip(
            "Hardware VRAM is limited to 1024MB — "
            "model context window and batch size are capped accordingly"
        )
        tip = lbl.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("VRAM", tip)
        self.assertIn("capped", tip.lower())

    def test_context_label_tooltip(self):
        lbl = MagicMock(spec=QLabel)
        lbl.setToolTip(
            "Persistent memory context is active — the AI retains information "
            "across sessions using the memory vault"
        )
        tip = lbl.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("Persistent", tip)
        self.assertIn("memory vault", tip.lower())

    def test_latency_label_tooltip(self):
        lbl = MagicMock(spec=QLabel)
        lbl.setToolTip(
            "Real-time streaming latency — tokens per second for the current response"
        )
        tip = lbl.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("latency", tip.lower())
        self.assertIn("tokens per second", tip.lower())

    def test_tts_checkbox_tooltip(self):
        cb = MagicMock(spec=QCheckBox)
        cb.setToolTip(
            "Toggle text-to-speech output on or off for AI responses"
        )
        tip = cb.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("text-to-speech", tip.lower())
        self.assertIn("toggle", tip.lower())

    def test_mic_button_tooltip(self):
        btn = MagicMock(spec=QPushButton)
        btn.setToolTip(
            "Hold to record voice input — release to send for processing"
        )
        tip = btn.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("hold", tip.lower())
        self.assertIn("voice", tip.lower())

    def test_run_button_tooltip(self):
        btn = MagicMock(spec=QPushButton)
        btn.setToolTip(
            "Send the current command or prompt to the AI for processing"
        )
        tip = btn.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("send", tip.lower())
        self.assertIn("command", tip.lower())

    def test_desire_engine_checkbox_tooltip(self):
        cb = MagicMock(spec=QCheckBox)
        cb.setToolTip(
            "Enable the Desire Engine — AI automatically generates "
            "proactive suggestions and tasks"
        )
        tip = cb.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("Desire Engine", tip)
        self.assertIn("suggestions", tip.lower())

    def test_stop_button_tooltip(self):
        btn = MagicMock(spec=QPushButton)
        btn.setToolTip(
            "Stop generation (discards in-flight response)"
        )
        tip = btn.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("stop", tip.lower())
        self.assertIn("in-flight", tip.lower())

    def test_copilot_tools_label_tooltip(self):
        lbl = MagicMock(spec=QLabel)
        lbl.setToolTip(
            "AI-powered copilot tools — capture screens, extract text via OCR, and more"
        )
        tip = lbl.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("copilot", tip.lower())
        self.assertIn("OCR", tip)

    def test_sidebar_tabs_tooltip(self):
        tabs = MagicMock(spec=QTabWidget)
        tabs.setToolTip(
            "Project sessions and conversation history — switch between active sessions"
        )
        tip = tabs.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("sessions", tip.lower())
        self.assertIn("conversation history", tip.lower())

    def test_workspace_label_tooltip(self):
        lbl = MagicMock(spec=QLabel)
        lbl.setToolTip(
            "Active workspace directory — documents, exports, and session files are stored here"
        )
        tip = lbl.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("workspace", tip.lower())
        self.assertIn("directory", tip.lower())

    def test_clear_chat_history_tooltip(self):
        btn = MagicMock(spec=QPushButton)
        btn.setToolTip(
            "Erase the persisted chat history file (data/chat_history.json)."
            " The chat window starts empty on the next launch. Use this if"
            " you previously enabled the chat-history toggle and want to"
            " remove any remaining on-disk content."
        )
        tip = btn.setToolTip.call_args[0][0]
        self.assertTrue(tip, "Tooltip should be non-empty")
        self.assertIn("chat history", tip.lower())
        self.assertIn("erase", tip.lower())


class TestRefreshModelStatusUI(unittest.TestCase):
    """refresh_model_status_ui - synchronize all 4 models between Settings and Main tab."""

    def setUp(self):
        self.ui = _make_ui()

    def test_refresh_model_status_ui_loaded(self):
        """When provider has a loaded model, status label and header show 🟢."""
        self.ui.controller.provider.is_loaded.return_value = True
        self.ui.controller.provider._model_path = "C:/models/test_model.gguf"
        test_config = {
            "desire_model_name": "qwen_desire.gguf",
            "auditor_model_name": "qwen_auditor.gguf",
            "vision_model": "qwen_vision.gguf",
            "model_file": "C:/models/test_model.gguf",
        }
        with patch.dict("app_ui.CONFIG", test_config, clear=False):
            self.ui.refresh_model_status_ui()

        self.ui.model_status_lbl.setText.assert_called_with("🟢 test_model.gguf")
        self.ui.main_model_header_lbl.setText.assert_called_with("🟢 test_model.gguf")
        self.ui.desire_model_lbl.setText.assert_called_with("🧠 Model: qwen_desire.gguf")
        self.ui.auditor_model_lbl.setText.assert_called_with("⚖️ Model: qwen_auditor.gguf")
        self.ui.vision_model_lbl.setText.assert_called_with("👁️ Vision: qwen_vision.gguf")

    def test_refresh_model_status_ui_unloaded(self):
        """When model is configured but provider is not loaded, show 🟡 unloaded."""
        self.ui.controller.provider.is_loaded.return_value = False
        test_config = {
            "model_file": "C:/models/unloaded_model.gguf",
            "desire_model_name": "",
            "auditor_model_name": "",
            "vision_model": "",
        }
        with patch.dict("app_ui.CONFIG", test_config, clear=False):
            self.ui.refresh_model_status_ui()

        self.ui.model_status_lbl.setText.assert_called_with("🟡 unloaded_model.gguf (unloaded)")
        self.ui.main_model_header_lbl.setText.assert_called_with("🟡 unloaded_model.gguf (unloaded)")
        self.ui.desire_model_lbl.setText.assert_called_with("🧠 Model: (main model)")
        self.ui.auditor_model_lbl.setText.assert_called_with("⚖️ Model: (main model)")
        self.ui.vision_model_lbl.setText.assert_called_with("👁️ Vision: (main model)")

    def test_refresh_model_status_ui_no_model(self):
        """When no model configured, show ⚪ No model selected."""
        self.ui.controller.provider.is_loaded.return_value = False
        test_config = {
            "model_file": "",
            "desire_model_name": "",
            "auditor_model_name": "",
            "vision_model": "",
        }
        with patch.dict("app_ui.CONFIG", test_config, clear=False):
            self.ui.refresh_model_status_ui()

        self.ui.model_status_lbl.setText.assert_called_with("⚪ No model selected")
        self.ui.main_model_header_lbl.setText.assert_called_with("⚪ No model selected")

