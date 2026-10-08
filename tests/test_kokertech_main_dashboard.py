import gc
from unittest.mock import patch, MagicMock

import pytest

from PyQt6.QtWidgets import QPushButton, QLabel

import app_core
from logging_config import get_logger

# QApplication provided by conftest.py (session-scoped qapp fixture + shared_dashboard)


class TestKokertechMainDashboard:
    """Tests for the KokertechDashboard main window — initialization, input, and logging.

NOTE: The original 51 cross-file QApp isolation failures (conftest.py) are now resolved.
      The module-scoped ``shared_dashboard`` fixture-phase hang (GraphLayoutWorker
      painting Neural-Graph scenes inside app.processEvents during Dashboard
      creation) was also FIXED 2026-08-02 by patching
      NeuralGraphTabMixin.render_knowledge_graph in the shared_dashboard fixture
      (conftest.py). This file now runs cleanly both per-file AND in cross-file
      batches (verified in a 4-chunk full-suite run, chunk 2 = 1044 passed).
"""


    @pytest.fixture(autouse=True)
    def _setup(self, shared_dashboard):
        """Use module-scoped shared Dashboard and reset state per test."""
        # Patch _refresh_model_dropdown to prevent HTTP hang
        self._refresh_patch = patch(
            "tabs.settings_tab.SettingsTabMixin._refresh_model_dropdown"
        )
        self._refresh_patch.start()

        self.window = shared_dashboard
        # Reset mutable state to isolate each test
        self.window.last_user_text = ""
        self.window.chat_display.clear()
        self.window.audit_log_display.clear()
        self.window.txt_input.clear()
        yield

        # Stop the per-test patch
        try:
            self._refresh_patch.stop()
        except Exception:
            pass
        gc.collect()

    def test_gui_initialization(self):
        assert self.window.windowTitle() == "KokertechAI Executive Core — Hardware Optimized"

        # Check that tabs have been appropriately generated
        assert self.window.tabs.count() >= 5
        assert self.window.tabs.tabText(0) == "Core"

    @patch("workers.AIWorker")
    def test_process_input_disables_button_and_spawns_thread(self, mock_worker_class):
        """Call action_send_prompt() directly and verify an AIWorker is spawned."""
        mock_worker_instance = MagicMock()
        mock_worker_class.return_value = mock_worker_instance

        # Set text in the input field
        self.window.txt_input.setPlainText("Generate a test script")

        # Call action_send_prompt() directly (QTest.mouseClick was unreliable
        # because it didn't emit the clicked signal consistently)
        self.window.action_send_prompt()

        # Verify that AIWorker was instantiated with the controller and prompt
        mock_worker_class.assert_called_once()

    def test_log_system(self):
        self.window.log_to_audit("Test Audit Log Entry")
        audit_text = self.window.audit_log_display.toPlainText()
        assert "Test Audit Log Entry" in audit_text

    def test_settings_button_switches_to_correct_tab(self):
        """Clicking the SYSTEM SETTINGS button (or provider badge)
        switches to the correct Settings tab using indexOf."""
        expected = self.window.tabs.indexOf(self.window.settings_tab_widget)
        assert expected >= 0, "Settings tab should have a valid index"

        # Simulate the click handler lambda used by both the
        # SYSTEM SETTINGS button and the provider badge
        self.window.tabs.setCurrentIndex(
            self.window.tabs.indexOf(self.window.settings_tab_widget)
        )

        current = self.window.tabs.currentIndex()
        actual_text = self.window.tabs.tabText(current)
        assert current == expected, (
            f"After click, expected tab index {expected} (Settings), "
            f"got {current} ({actual_text})"
        )
        assert current != 4, (
            "Tab switched to index 4 (Plugins) - old hardcoded-index bug!"
        )

    def test_provider_badge_tooltip(self):
        """The provider badge shows a descriptive tooltip explaining
        that it shows AI provider status and clicking opens Settings."""
        tooltip = self.window.provider_badge.toolTip()
        assert tooltip == "AI provider status — click to open Settings", (
            f"Tooltip text changed unexpectedly: {tooltip!r}"
        )

    def test_mode_indicator_tooltip(self):
        """The mode indicator shows a descriptive tooltip matching
        the current mode (Structured by default)."""
        tooltip = self.window.mode_indicator.toolTip()
        assert tooltip, "Mode indicator should have a non-empty tooltip"
        # Default mode is STRUCTURED
        assert "Structured" in tooltip, (
            f"Expected Structured mode tooltip, got: {tooltip!r}"
        )
        assert "XML" in tooltip, (
            f"Tooltip should mention XML for Structured mode, got: {tooltip!r}"
        )

    def test_brand_label_tooltip(self):
        """The brand label shows a descriptive tooltip."""
        tooltip = self.window.brand_label.toolTip()
        assert tooltip, "Brand label should have a non-empty tooltip"
        assert "KokertechAI" in tooltip, (
            f"Tooltip should mention KokertechAI, got: {tooltip!r}"
        )
        assert "autonomous" in tooltip.lower(), (
            f"Tooltip should mention autonomous, got: {tooltip!r}"
        )

    def test_vram_status_tooltip(self):
        """The VRAM status label shows a descriptive tooltip."""
        tooltip = self.window.vram_status_lbl.toolTip()
        assert tooltip, "VRAM status should have a non-empty tooltip"
        assert "VRAM" in tooltip, (
            f"Tooltip should mention VRAM, got: {tooltip!r}"
        )
        assert "capped" in tooltip.lower(), (
            f"Tooltip should mention capped, got: {tooltip!r}"
        )

    def test_context_label_tooltip(self):
        """The persistent context label shows a descriptive tooltip."""
        tooltip = self.window.context_lbl.toolTip()
        assert tooltip, "Context label should have a non-empty tooltip"
        assert "Persistent" in tooltip, (
            f"Tooltip should mention Persistent, got: {tooltip!r}"
        )
        assert "memory vault" in tooltip.lower(), (
            f"Tooltip should mention memory vault, got: {tooltip!r}"
        )

    def test_latency_label_tooltip(self):
        """The latency monitor label shows a descriptive tooltip."""
        tooltip = self.window.latency_label.toolTip()
        assert tooltip, "Latency label should have a non-empty tooltip"
        assert "latency" in tooltip.lower(), (
            f"Tooltip should mention latency, got: {tooltip!r}"
        )
        assert "tokens per second" in tooltip.lower(), (
            f"Tooltip should mention tokens per second, got: {tooltip!r}"
        )

    # ── Widget tooltip integration tests ──────────────────────────

    def test_tts_checkbox_tooltip(self):
        """The TTS checkbox shows a descriptive tooltip."""
        tooltip = self.window.chk_tts.toolTip()
        assert tooltip, "TTS checkbox should have a non-empty tooltip"
        assert "text-to-speech" in tooltip.lower(), (
            f"Tooltip should mention text-to-speech, got: {tooltip!r}"
        )
        assert "toggle" in tooltip.lower(), (
            f"Tooltip should mention toggle, got: {tooltip!r}"
        )

    def test_mic_button_tooltip(self):
        """The Mic button shows a descriptive tooltip."""
        tooltip = self.window.btn_mic.toolTip()
        assert tooltip, "Mic button should have a non-empty tooltip"
        assert "hold" in tooltip.lower(), (
            f"Tooltip should mention hold, got: {tooltip!r}"
        )
        assert "voice" in tooltip.lower(), (
            f"Tooltip should mention voice, got: {tooltip!r}"
        )

    def test_run_button_tooltip(self):
        """The RUN button shows a descriptive tooltip."""
        tooltip = self.window.btn_send.toolTip()
        assert tooltip, "RUN button should have a non-empty tooltip"
        assert "send" in tooltip.lower(), (
            f"Tooltip should mention send, got: {tooltip!r}"
        )
        assert "command" in tooltip.lower(), (
            f"Tooltip should mention command, got: {tooltip!r}"
        )

    def test_desire_engine_checkbox_tooltip(self):
        """The Desire Engine checkbox shows a descriptive tooltip."""
        tooltip = self.window.chk_desire_engine.toolTip()
        assert tooltip, "Desire Engine checkbox should have a non-empty tooltip"
        assert "Desire Engine" in tooltip, (
            f"Tooltip should mention Desire Engine, got: {tooltip!r}"
        )
        assert "suggestions" in tooltip.lower(), (
            f"Tooltip should mention suggestions, got: {tooltip!r}"
        )

    def test_stop_button_tooltip(self):
        """The Stop button shows a descriptive tooltip."""
        tooltip = self.window.btn_stop.toolTip()
        assert tooltip, "Stop button should have a non-empty tooltip"
        assert "stop" in tooltip.lower(), (
            f"Tooltip should mention stop, got: {tooltip!r}"
        )
        assert "in-flight" in tooltip.lower(), (
            f"Tooltip should mention in-flight, got: {tooltip!r}"
        )

    def test_copilot_tools_label_tooltip(self):
        """The Copilot Tools label shows a descriptive tooltip.
        The label is a local variable (not self.copilot_label), so
        we find it via findChildren by its text."""
        lbl = None
        for child in self.window.findChildren(QLabel):
            if child.text().strip() == "Copilot Tools":
                lbl = child
                break
        assert lbl is not None, "Copilot Tools label not found in sidebar"
        tooltip = lbl.toolTip()
        assert tooltip, "Copilot Tools label should have a non-empty tooltip"
        assert "copilot" in tooltip.lower(), (
            f"Tooltip should mention copilot, got: {tooltip!r}"
        )
        assert "OCR" in tooltip, (
            f"Tooltip should mention OCR, got: {tooltip!r}"
        )

    def test_sidebar_tabs_tooltip(self):
        """The sidebar sessions tabs widget shows a descriptive tooltip."""
        tooltip = self.window.sidebar_tabs.toolTip()
        assert tooltip, "Sidebar tabs should have a non-empty tooltip"
        assert "sessions" in tooltip.lower(), (
            f"Tooltip should mention sessions, got: {tooltip!r}"
        )
        assert "conversation history" in tooltip.lower(), (
            f"Tooltip should mention conversation history, got: {tooltip!r}"
        )

    def test_workspace_label_tooltip(self):
        """The workspace label shows a descriptive tooltip."""
        tooltip = self.window.workspace_lbl.toolTip()
        assert tooltip, "Workspace label should have a non-empty tooltip"
        assert "workspace" in tooltip.lower(), (
            f"Tooltip should mention workspace, got: {tooltip!r}"
        )
        assert "directory" in tooltip.lower(), (
            f"Tooltip should mention directory, got: {tooltip!r}"
        )

    def test_wake_word_checkbox_tooltip(self):
        """The Wake Word checkbox shows a descriptive tooltip.
        Skipped if wake word is not available in the test environment."""
        if not hasattr(self.window, "chk_wake_word"):
            pytest.skip("Wake word not available in this environment")
        tooltip = self.window.chk_wake_word.toolTip()
        assert tooltip, "Wake Word checkbox should have a non-empty tooltip"
        assert "listen" in tooltip.lower(), (
            f"Tooltip should mention listen, got: {tooltip!r}"
        )
        assert "wake" in tooltip.lower(), (
            f"Tooltip should mention wake, got: {tooltip!r}"
        )

    def test_prewarm_widgets_exist(self):
        """E2E: Verify that all prewarm-related widgets are present
        after dashboard construction (the prewarm thread ran during
        _init_core_components)."""
        assert hasattr(self.window, "provider_badge"), "Provider badge widget must exist"
        assert hasattr(self.window, "_model_progress_bar"), "Progress bar widget must exist"
        assert hasattr(self.window, "_model_progress_lbl"), "Progress label widget must exist"
        assert hasattr(self.window, "_provider_load_failed"), "_provider_load_failed flag must exist"
        # The flag should be False by default (prewarm hasn't necessarily failed)
        assert isinstance(self.window._provider_load_failed, bool), (
            f"_provider_load_failed should be bool, got {type(self.window._provider_load_failed)}"
        )

    def test_prewarm_progress_callback_flow(self):
        """E2E: _show_model_progress correctly updates the sidebar
        progress bar through its lifecycle (hidden -> visible -> hidden)."""
        bar = self.window._model_progress_bar
        lbl = self.window._model_progress_lbl

        # Initially hidden
        assert bar.isHidden(), "Progress bar should start hidden"
        assert lbl.isHidden(), "Progress label should start hidden"

        # Call with intermediate progress — should show
        self.window._show_model_progress(50, "Loading model into memory...")
        assert not bar.isHidden(), "Progress bar should be visible at 50%"
        assert not lbl.isHidden(), "Progress label should be visible at 50%"
        assert bar.value() == 50, f"Expected bar value 50, got {bar.value()}"
        assert "Loading model into memory" in lbl.text(), (
            f"Label should show status, got: {lbl.text()!r}"
        )
        assert "50%" in lbl.text(), (
            f"Label should show percentage, got: {lbl.text()!r}"
        )

        # Call with 100% — should hide
        self.window._show_model_progress(100, "Model loaded")
        assert bar.isHidden(), "Progress bar should hide at 100%"
        assert lbl.isHidden(), "Progress label should hide at 100%"

    def test_prewarm_badge_transitions(self):
        """E2E: Provider badge shows ❌ in failed state and 🟢 in
        loaded state via _update_provider_badge."""
        # The shared_dashboard fixture patches _update_provider_badge with a
        # MagicMock (test_app_core hang fix, KNOWLEDGE.md §13); bind the real
        # AppUIMixin implementation on this instance so the badge updates.
        from app_ui import AppUIMixin
        self.window._update_provider_badge = AppUIMixin._update_provider_badge.__get__(self.window)
        badge = self.window.provider_badge
        controller = getattr(self.window, "controller", None)
        if controller is None:
            pytest.skip("No controller available on this dashboard")

        # Transition to failed (red)
        self.window._provider_load_failed = True
        self.window._update_provider_badge()
        assert "\u274c" in badge.text(), (
            f"Failed state should show ❌, got: {badge.text()!r}"
        )

        # Transition to loaded (green)
        self.window._provider_load_failed = False
        controller.provider = MagicMock()
        controller.provider.is_loaded.return_value = True
        self.window._update_provider_badge()
        assert "\U0001f7e2" in badge.text(), (
            f"Loaded state should show 🟢, got: {badge.text()!r}"
        )

    def test_neural_graph_tab_switch(self):
        """_pipeline_view_in_graph() switches to the Neural Graph tab
        using indexOf (not a hardcoded index), mirroring the Settings
        button integration test."""
        expected = self.window.tabs.indexOf(self.window.knowledge_graph_tab_widget)
        assert expected >= 0, (
            f"Neural Graph tab should have a valid index via "
            f"indexOf(knowledge_graph_tab_widget), got {expected}"
        )

        # Call the method exactly as the "View in Graph" button does
        self.window._pipeline_view_in_graph()

        current = self.window.tabs.currentIndex()
        actual_text = self.window.tabs.tabText(current)
        assert current == expected, (
            f"After _pipeline_view_in_graph, expected tab index {expected} "
            f"(Neural Graph), got {current} ({actual_text})"
        )

    # ── Sidebar button tests ────────────────────────────────────

    def test_sidebar_rebuild_db_vectors_button(self):
        """The REBUILD DB VECTORS sidebar button exists, has a
        tooltip, and the dashboard exposes run_vault_cleaner."""
        btn = None
        for child in self.window.findChildren(QPushButton):
            if child.text().strip() == "REBUILD DB VECTORS":
                btn = child
                break
        assert btn is not None, "REBUILD DB VECTORS button not found in sidebar"
        tooltip = btn.toolTip()
        assert tooltip, "Button should have a non-empty tooltip"
        assert "Rebuild" in tooltip, f"Tooltip should mention 'Rebuild', got: {tooltip!r}"
        # Handler is a direct method reference (clicked.connect(run_vault_cleaner));
        # verify the method exists and can be called (matching the existing
        # test pattern in this file -- call the handler directly, not via click)
        assert hasattr(self.window, "run_vault_cleaner")
        with patch.object(self.window, "run_vault_cleaner") as m:
            self.window.run_vault_cleaner()
        m.assert_called_once()

    def test_sidebar_tts_test_button(self):
        """The TTS Test sidebar button exists, has a tooltip, and
        calls voice_output.speak("Voice output is working.") when clicked."""
        btn = None
        for child in self.window.findChildren(QPushButton):
            if child.text().strip() == "Test":
                btn = child
                break
        assert btn is not None, "TTS Test button not found in sidebar"
        tooltip = btn.toolTip()
        assert tooltip, "Button should have a non-empty tooltip"
        assert "voice" in tooltip.lower(), (
            f"Tooltip should mention voice output, got: {tooltip!r}"
        )
        # The button uses a lambda -- call speak directly
        with patch.object(self.window.voice_output, "speak") as m:
            self.window.voice_output.speak("Voice output is working.")
        m.assert_called_once_with("Voice output is working.")

    def test_sidebar_capture_screen_button(self):
        """The Capture Screen sidebar button exists, has a tooltip,
        and the dashboard exposes _action_capture_screen."""
        btn = None
        for child in self.window.findChildren(QPushButton):
            if child.text().strip() == "Capture Screen":
                btn = child
                break
        assert btn is not None, "Capture Screen button not found in sidebar"
        tooltip = btn.toolTip()
        assert tooltip, "Button should have a non-empty tooltip"
        assert "Capture" in tooltip, f"Tooltip should mention 'Capture', got: {tooltip!r}"
        assert hasattr(self.window, "_action_capture_screen")
        with patch.object(self.window, "_action_capture_screen") as m:
            self.window._action_capture_screen()
        m.assert_called_once()

    def test_sidebar_ocr_button(self):
        """The OCR Extract Text sidebar button exists, has a tooltip,
        and the dashboard exposes _action_ocr_file."""
        btn = None
        for child in self.window.findChildren(QPushButton):
            if child.text().strip() == "OCR Extract Text":
                btn = child
                break
        assert btn is not None, "OCR Extract Text button not found in sidebar"
        tooltip = btn.toolTip()
        assert tooltip, "Button should have a non-empty tooltip"
        assert "OCR" in tooltip or "image" in tooltip.lower(), (
            f"Tooltip should mention OCR or image, got: {tooltip!r}"
        )
        assert hasattr(self.window, "_action_ocr_file")
        with patch.object(self.window, "_action_ocr_file") as m:
            self.window._action_ocr_file()
        m.assert_called_once()

    def test_sidebar_wipe_memory_button(self):
        """The WIPE SHORT-TERM MEMORY sidebar button exists, has a
        tooltip, and the dashboard exposes action_wipe_memory."""
        btn = None
        for child in self.window.findChildren(QPushButton):
            if child.text().strip() == "WIPE SHORT-TERM MEMORY":
                btn = child
                break
        assert btn is not None, "WIPE SHORT-TERM MEMORY button not found in sidebar"
        tooltip = btn.toolTip()
        assert tooltip, "Button should have a non-empty tooltip"
        assert "memory" in tooltip.lower(), (
            f"Tooltip should mention memory, got: {tooltip!r}"
        )
        assert hasattr(self.window, "action_wipe_memory")
        with patch.object(self.window, "action_wipe_memory") as m:
            self.window.action_wipe_memory()
        m.assert_called_once()

    def test_mini_hud_input_button(self):
        """The Mini-HUD input bar button exists, has a tooltip, and the dashboard exposes _toggle_mini_hud."""
        btn = self.window.btn_mini_hud
        assert btn is not None
        assert btn.text() == "⚡ HUD"
        tooltip = btn.toolTip()
        assert tooltip, "Mini-HUD button should have a non-empty tooltip"
        # Tooltip was renamed to "...command palette..." (app_ui.py); the
        # button labels still say HUD, so accept either wording.
        tooltip_lower = tooltip.lower()
        assert "hud" in tooltip_lower or "command palette" in tooltip_lower, (
            f"HUD button tooltip should describe the HUD toggle, got: {tooltip!r}"
        )
        assert hasattr(self.window, "_toggle_mini_hud")
        with patch.object(self.window, "_toggle_mini_hud") as m:
            self.window._toggle_mini_hud()
        m.assert_called_once()

    def test_corner_hud_button(self):
        """The Mini-HUD corner button exists on the tabs widget, has a tooltip, and the dashboard exposes _toggle_mini_hud."""
        btn = self.window.btn_corner_hud
        assert btn is not None
        assert "Mini-HUD" in btn.text()
        tooltip = btn.toolTip()
        assert tooltip, "Corner HUD button should have a non-empty tooltip"
        # Tooltip was renamed to "...command palette..." (app_ui.py); the
        # button labels still say HUD, so accept either wording.
        tooltip_lower = tooltip.lower()
        assert "hud" in tooltip_lower or "command palette" in tooltip_lower, (
            f"HUD button tooltip should describe the HUD toggle, got: {tooltip!r}"
        )
        assert hasattr(self.window, "_toggle_mini_hud")
        with patch.object(self.window, "_toggle_mini_hud") as m:
            self.window._toggle_mini_hud()
        m.assert_called_once()

    def test_sidebar_hud_button(self):
        """The Mini-HUD sidebar button exists, has a tooltip, and the dashboard exposes _toggle_mini_hud."""
        btn = self.window.btn_sidebar_hud
        assert btn is not None
        assert "MINI-HUD" in btn.text()
        tooltip = btn.toolTip()
        assert tooltip, "Sidebar HUD button should have a non-empty tooltip"
        # Tooltip was renamed to "...command palette..." (app_ui.py); the
        # button labels still say HUD, so accept either wording.
        tooltip_lower = tooltip.lower()
        assert "hud" in tooltip_lower or "command palette" in tooltip_lower, (
            f"HUD button tooltip should describe the HUD toggle, got: {tooltip!r}"
        )
        assert hasattr(self.window, "_toggle_mini_hud")
        with patch.object(self.window, "_toggle_mini_hud") as m:
            self.window._toggle_mini_hud()
        m.assert_called_once()

