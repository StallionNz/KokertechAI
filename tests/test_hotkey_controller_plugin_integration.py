"""
Integration tests for the combined hotkey + controller + plugin pathways.

Tests how hotkey-triggered prompts flow through the controller and dispatch to plugins:
  1. Hotkey trigger → controller (mock mode) → parse_ai → command → plugin dispatch
  2. Hotkey trigger → controller (freeform/mocked provider) → command → plugin dispatch
  3. handle_ai_response plugin dispatch logic (simulating AIWorker → handle_ai_response)
  4. HITL intercept for restricted actions (WRITE_FILE, DELETE_FILE, etc.)
  5. Config-driven mode toggling via hotkey settings → controller behavior
  6. Dashboard action_send_prompt → controller → handle_ai_response flow (synchronous mock)

NOTE: The original 51 cross-file QApp isolation failures (conftest.py) are now resolved.
      The module-scoped ``shared_dashboard`` fixture-phase hang (GraphLayoutWorker
      painting Neural-Graph scenes inside app.processEvents during Dashboard
      creation) was also FIXED 2026-08-02 by patching
      NeuralGraphTabMixin.render_knowledge_graph in the shared_dashboard fixture
      (conftest.py). This file now runs cleanly both per-file AND in cross-file
      batches (verified in a 4-chunk full-suite run, chunk 2 = 1044 passed).
"""

import gc
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, call, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Qt cleanup helper — prevents access violations from orphaned C++ widgets
# ---------------------------------------------------------------------------
def _safe_close(w):
    """Close a QWidget and ensure its C++ object is fully released.

    Sets ``_suppress_quit`` before closing to prevent ``closeEvent``
    from calling ``QApplication.instance().quit()``, which would set the
    internal ``closingDown`` flag on the session-scoped QApplication and
    prevent widget creation in subsequent test files.
    """
    if w is None:
        return
    try:
        w._suppress_quit = True
        w.close()
    except Exception:
        pass
    try:
        w.deleteLater()
    except Exception:
        pass
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app:
        for _ in range(5):
            app.processEvents()
    gc.collect()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctrl():
    """Return a KokertechController with empty history (provider mocked)."""
    from unittest.mock import patch
    with patch("kokertechController.get_provider"):
        from kokertechController import KokertechController
        c = KokertechController()
    c.history = []
    return c

def _enable_all_plugins():
    """Ensure all loaded plugins are enabled."""
    import plugin_registry
    plugin_registry.registry.disabled.clear()

def _create_dashboard_with_mocks():
    """Create a KokertechDashboard with all heavy dependencies mocked.
    
    Returns (window, mocks_dict) so callers can access individual mocks if needed.
    The window can be used to call hotkey handlers, action_send_prompt, etc.
    """
    from PyQt6.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])
    import app_core

    # Stack up all the patches
    mem = patch.multiple(
        "memory_vault",
        semantic_search=MagicMock(return_value=[]),
        store_memory=MagicMock(),
        get_recent_bias=MagicMock(return_value=[]),
        get_growth_arc=MagicMock(return_value=[]),
        ensure_tables_exist=MagicMock(),
    )
    mem.start()

    log = patch("app_core.get_logger", return_value=MagicMock())
    log.start()

    tts = patch("app_core.VoiceOutput", return_value=MagicMock())
    tts.start()

    vrw = patch("workers.VoiceRecorderWorker", return_value=MagicMock())
    vrw.start()

    # Patch requests.post so background threads (startup, desire, auditor) don't hit real provider
    req = patch("requests.post")
    mock_post = req.start()
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"choices": [{"message": {"role": "assistant", "content": "OK"}}]},
        raise_for_status=lambda: None,
    )

    mock_registry = MagicMock()
    mock_registry.get_plugin_count.return_value = 0
    mock_registry.get_enabled_count.return_value = 0
    mock_registry.get_system_prompt_addition.return_value = "[]"
    mock_registry.plugins = {}
    mock_registry.metadata = {}
    mock_registry.disabled = set()
    mock_registry.is_enabled.return_value = True
    pl = patch("plugin_registry.registry", mock_registry)
    pl.start()

    window = app_core.KokertechDashboard()

    return window, {
        "memory_vault": mem,
        "logger": log,
        "tts": tts,
        "vrw": vrw,
        "plugin_registry": pl,
        "requests": req,
    }

# ===================================================================
# 1. HOTKEY TRIGGER → CONTROLLER (MOCK MODE) → PLUGIN DISPATCH
# ===================================================================

class TestHotkeyTriggerMockModePluginChain(unittest.TestCase):
    """
    Simulates the hotkey trigger data flow through the controller in mock mode,
    verifying that commands extracted from the response can be dispatched to plugins.
    """

    def setUp(self):
        self.mocks = patch.multiple(
            "memory_vault",
            semantic_search=MagicMock(return_value=[]),
            store_memory=MagicMock(),
            get_recent_bias=MagicMock(return_value=[]),
            get_growth_arc=MagicMock(return_value=[]),
            ensure_tables_exist=MagicMock(),
        )
        self.mocks.start()
        _enable_all_plugins()

    def tearDown(self):
        self.mocks.stop()

    # ---- Data flow: what happens when a hotkey-triggered prompt reaches the controller ----

    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": True}, clear=False)
    def test_mock_mode_processes_hotkey_prompt_and_returns_expected_structure(self):
        """
        Simulates what happens when a hotkey-triggered prompt is passed to the
        controller in mock+freeform mode. Verifies the output structure is what
        handle_ai_response expects.
        """
        ctrl = _ctrl()
        ctrl.memory_limit = 999  # prevent summarization
        with patch.object(ctrl, "provider") as mp:
            result = ctrl.process_input(
                "What is the system status?",
                log_callback=lambda x: None,
            )
            mp.chat_completion.assert_not_called()

        # This is the same structure handle_ai_response reads
        self.assertIn("final", result)
        self.assertIn("thinking", result)
        self.assertIn("command", result)
        self.assertIn("ts", result)
        self.assertIn("[MOCK]", result["final"])
        # Mock mode with freeform=True → thinking is empty
        self.assertEqual(result["thinking"], "")

    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": True}, clear=False)
    def test_mock_mode_no_command_skips_dispatch(self):
        """
        When mock mode returns a response without a JSON command (the default),
        handle_ai_response should see command=None and skip plugin dispatch.
        """
        ctrl = _ctrl()
        ctrl.memory_limit = 999
        with patch.object(ctrl, "provider") as mp:
            result = ctrl.process_input("Hello", log_callback=lambda x: None)
            mp.chat_completion.assert_not_called()

        # Default mock responses have no JSON, so command should be None
        self.assertIsNone(result.get("command"))

    # ---- parse_ai extracts commands from hotkey-triggered text ----

    def test_hotkey_trigger_text_with_command_flows_through_parse_ai(self):
        """
        Verifies that text arriving from a hotkey trigger can be parsed as
        freeform and yield a plugin command — same path mock mode uses.
        """
        ctrl = _ctrl()
        result = ctrl.parse_ai(
            '{"action": "SYSTEM_STATUS"}',
            freeform=True,
        )
        self.assertIsNotNone(result["command"])
        self.assertEqual(result["command"]["action"], "SYSTEM_STATUS")

    def test_hotkey_trigger_text_with_command_dispatches_to_plugin(self):
        """
        After parse_ai extracts a command (as happens in mock mode),
        the command can be dispatched through the bridge to a real plugin.
        This simulates what handle_ai_response does with the command.
        """
        ctrl = _ctrl()
        parse_result = ctrl.parse_ai(
            '{"action": "SYSTEM_STATUS"}',
            freeform=True,
        )

        # Simulate handle_ai_response dispatch
        import kokertech_bridge
        plugin_result = kokertech_bridge.handle_ai_intent(parse_result["command"])
        self.assertIsInstance(plugin_result, str)
        self.assertTrue(
            any(kw in plugin_result.lower() for kw in ["cpu", "memory", "disk", "system"]),
            f"Expected system info, got: {plugin_result[:80]}",
        )

    def test_hotkey_trigger_freeform_command_plus_bridge_full_chain(self):
        """Full chain: hotkey text → parse_ai(freeform=True) → bridge → plugin result."""
        ctrl = _ctrl()
        parse_result = ctrl.parse_ai(
            '{"action": "LIST_FILES", "path": "."}',
            freeform=True,
        )
        self.assertIsNotNone(parse_result["command"])

        import kokertech_bridge
        plugin_result = kokertech_bridge.handle_ai_intent(parse_result["command"])
        self.assertIsInstance(plugin_result, str)
        self.assertTrue(len(plugin_result) > 0)

# ===================================================================
# 2. HOTKEY TRIGGER → CONTROLLER (FREEFORM/MOCKED PROVIDER) → PLUGIN
# ===================================================================

class TestHotkeyTriggerFreeformModePluginChain(unittest.TestCase):
    """
    Freeform mode with mocked provider — simulates the full hotkey dispatch
    where the controller calls a real (mocked) provider and the response
    contains a JSON command that gets extracted and dispatched to a plugin.
    """

    def setUp(self):
        self.mocks = patch.multiple(
            "memory_vault",
            semantic_search=MagicMock(return_value=[]),
            store_memory=MagicMock(),
            get_recent_bias=MagicMock(return_value=[]),
            get_growth_arc=MagicMock(return_value=[]),
            ensure_tables_exist=MagicMock(),
        )
        self.mocks.start()
        _enable_all_plugins()

    def tearDown(self):
        self.mocks.stop()

    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": False}, clear=False)
    def test_freeform_provider_response_with_command_flows_to_plugin(self):
        """
        Simulates a hotkey trigger resulting in a freeform AI response
        with a JSON command. Verifies the command flows from process_input
        through parse_ai and would be dispatched by handle_ai_response.
        """
        ctrl = _ctrl()
        with patch.object(ctrl, "provider") as mock_provider:
            mock_provider.chat_completion.return_value = {
                "content": '{"action": "SYSTEM_STATUS"}',
                "model": "test",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "error": None,
                "tool_calls": [],
            }
            result = ctrl.process_input("Check system", log_callback=lambda x: None)
        self.assertIsNotNone(result["command"])
        self.assertEqual(result["command"]["action"], "SYSTEM_STATUS")

        # Simulate handle_ai_response dispatch
        import kokertech_bridge
        plugin_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIsInstance(plugin_result, str)

    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": False}, clear=False)
    def test_freeform_missing_command_skips_dispatch(self):
        """
        When the freeform AI response has no JSON command, the controller
        returns command=None and handle_ai_response skips plugin dispatch.
        """
        ctrl = _ctrl()
        with patch.object(ctrl, "provider") as mock_provider:
            mock_provider.chat_completion.return_value = {
                "content": "Sure, I can help with that!",
                "model": "test",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "error": None,
                "tool_calls": [],
            }
            result = ctrl.process_input("Hello", log_callback=lambda x: None)
        self.assertIsNone(result.get("command"))

    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": False}, clear=False)
    def test_freeform_write_file_command_dispatches_via_bridge(self):
        """
        Hotkey-triggered freeform response with WRITE_FILE command
        → parse_ai extracts → bridge writes file → we clean up.
        """
        test_path = "_test_hotkey_freeform_tmp.txt"
        ctrl = _ctrl()
        with patch.object(ctrl, "provider") as mock_provider:
            mock_provider.chat_completion.return_value = {
                "content": json.dumps({
                    "action": "WRITE_FILE",
                    "path": test_path,
                    "content": "hotkey integration test",
                }),
                "model": "test",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "error": None,
                "tool_calls": [],
            }
            result = ctrl.process_input("Write test file", log_callback=lambda x: None)
        self.assertIsNotNone(result["command"])
        self.assertEqual(result["command"]["action"], "WRITE_FILE")

        import kokertech_bridge
        plugin_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIn("Successfully", plugin_result)

        # Cleanup
        fp = os.path.join(r"C:\KokertechAI", test_path)
        if os.path.exists(fp):
            os.remove(fp)

# ===================================================================
# 3. handle_ai_response PLUGIN DISPATCH LOGIC
# ===================================================================

class TestHandleAiResponsePluginDispatch(unittest.TestCase):
    """
    Tests the exact plugin dispatch logic in handle_ai_response by calling it
    directly with controlled ai_data. This simulates what happens when an
    AIWorker emits its reply_signal after the controller processes a hotkey-triggered prompt.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, shared_dashboard):
        """Use module-scoped shared Dashboard and reset state per test."""
        self.window = shared_dashboard
        self.window.last_user_text = "system status"
        self.window.chat_display.clear()
        self.window.audit_log_display.clear()
        self.window.file_logger.reset_mock()
        _enable_all_plugins()
        yield
        # Prevent C++ state accumulation across test classes (avoids STATUS_FATAL_APP_EXIT)
        gc.collect()
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            for _ in range(5):
                app.processEvents()

    def _last_audit_line(self):
        """Get the last line from the audit log."""
        text = self.window.audit_log_display.toPlainText()
        lines = [l for l in text.split("\n") if l.strip()]
        return lines[-1] if lines else ""

    def _chat_display_text(self):
        """Get the chat display plain text."""
        return self.window.chat_display.toPlainText()

    def test_dispatch_system_status_to_plugin(self):
        """
        handle_ai_response receives ai_data with a SYSTEM_STATUS command
        and dispatches it to the real plugin via the bridge.
        """
        from PyQt6.QtWidgets import QMessageBox
        ai_data = {
            "final": "Checking system status...",
            "thinking": "I should check the system",
            "command": {"action": "SYSTEM_STATUS"},
            "ts": "12:00:00",
        }
        # SYSTEM_STATUS is not restricted, but patch to be safe
        with patch("PyQt6.QtWidgets.QMessageBox.warning", return_value=QMessageBox.StandardButton.Yes):
            self.window.handle_ai_response(ai_data)

        # The dispatch should log the action
        audit = self._last_audit_line()
        self.assertIn("SYSTEM_STATUS", audit)

        # The plugin result should be in the chat display
        chat = self._chat_display_text()
        self.assertTrue(
            any(kw in chat.lower() for kw in ["cpu", "memory", "disk", "system"]),
            f"Expected system info in chat, got: {chat[:100]}",
        )

    def test_dispatch_no_command_skips_plugin(self):
        """
        When ai_data has no command field, handle_ai_response skips plugin dispatch.
        """
        ai_data = {
            "final": "Hello there!",
            "thinking": "",
            "ts": "12:00:00",
        }
        self.window.handle_ai_response(ai_data)

        chat = self._chat_display_text()
        # The response text should appear
        self.assertIn("Hello there!", chat)
        # But there should be no "Dispatched action" audit log
        self.assertNotIn("Dispatched", self.window.audit_log_display.toPlainText())

    def test_dispatch_list_files_plugin(self):
        """
        handle_ai_response dispatches a LIST_FILES command to the real plugin.
        """
        ai_data = {
            "final": "Listing files...",
            "thinking": "",
            "command": {"action": "LIST_FILES", "path": "."},
            "ts": "12:00:00",
        }
        with patch("PyQt6.QtWidgets.QMessageBox.warning", return_value=None):
            self.window.handle_ai_response(ai_data)

        audit = self._last_audit_line()
        self.assertIn("LIST_FILES", audit)
        chat = self._chat_display_text()
        # LIST_FILES returns filenames, should include at least some test file references
        self.assertTrue(len(chat) > 50)

    def test_dispatch_search_web_plugin(self):
        """
        handle_ai_response dispatches a SEARCH_WEB command.
        """
        ai_data = {
            "final": "Searching...",
            "thinking": "I need to search",
            "command": {"action": "SEARCH_WEB", "query": "test integration"},
            "ts": "12:00:00",
        }
        with patch("PyQt6.QtWidgets.QMessageBox.warning", return_value=None):
            self.window.handle_ai_response(ai_data)

        audit = self._last_audit_line()
        self.assertIn("SEARCH_WEB", audit)

    def test_dispatch_multiple_commands_only_first_dispatched(self):
        """
        handle_ai_response uses ai_data['command'] (single dict), not a list.
        Verifies exactly one dispatch per response.
        """
        ai_data = {
            "final": "Running checks...",
            "thinking": "",
            "command": {"action": "SYSTEM_STATUS"},
            "ts": "12:00:00",
        }
        with patch("PyQt6.QtWidgets.QMessageBox.warning", return_value=None):
            # Capture audit log before
            before = self.window.audit_log_display.toPlainText()
            self.window.handle_ai_response(ai_data)
            after = self.window.audit_log_display.toPlainText()

        # Count "Dispatched action" entries that appeared since before
        new_lines = after[len(before):]
        dispatch_count = new_lines.count("Dispatched action")
        self.assertEqual(dispatch_count, 1, "Expected exactly one dispatch")

# ===================================================================
# 4. HITL INTERCEPT FOR RESTRICTED ACTIONS
# ===================================================================

class TestHitlInterceptFromHotkey(unittest.TestCase):
    """
    Human-in-the-loop: restricted actions (WRITE_FILE, DELETE_FILE, etc.)
    require user confirmation via QMessageBox. Non-restricted actions
    like SYSTEM_STATUS should NOT trigger the HITL dialog.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, shared_dashboard):
        """Use module-scoped shared Dashboard and reset state per test."""
        self.window = shared_dashboard
        self.window.last_user_text = "test"
        self.window.chat_display.clear()
        self.window.audit_log_display.clear()
        self.window.file_logger.reset_mock()
        _enable_all_plugins()
        yield
        # Prevent C++ state accumulation across test classes (avoids STATUS_FATAL_APP_EXIT)
        gc.collect()
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            for _ in range(5):
                app.processEvents()

    def test_non_restricted_action_skips_hitl(self):
        """
        SYSTEM_STATUS is NOT in the restricted list → should dispatch without
        showing any QMessageBox.
        """
        ai_data = {
            "final": "Status check",
            "command": {"action": "SYSTEM_STATUS"},
        }
        with patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_msgbox:
            self.window.handle_ai_response(ai_data)
            mock_msgbox.assert_not_called()

        # The dispatch should still happen
        audit = self.window.audit_log_display.toPlainText()
        self.assertIn("Dispatched action", audit)

    def test_writable_restricted_action_requires_hitl_yes(self):
        """
        WRITE_FILE is in the restricted list → QMessageBox.warning is shown.
        When user clicks Yes, execution proceeds.
        """
        from PyQt6.QtWidgets import QMessageBox
        test_path = "_test_hitl_ok.txt"
        ai_data = {
            "final": "Writing file...",
            "command": {"action": "WRITE_FILE", "path": test_path, "content": "hitl test"},
        }
        with patch("PyQt6.QtWidgets.QMessageBox.warning", return_value=QMessageBox.StandardButton.Yes):
            self.window.handle_ai_response(ai_data)

        audit = self.window.audit_log_display.toPlainText()
        self.assertIn("Dispatched action", audit)
        self.assertIn("WRITE_FILE", audit)

        # Cleanup
        fp = os.path.join(r"C:\KokertechAI", test_path)
        if os.path.exists(fp):
            os.remove(fp)

    def test_writable_restricted_action_hitl_no_blocks(self):
        """
        When user clicks No in the HITL dialog, the action is blocked
        and not dispatched to the plugin.
        """
        from PyQt6.QtWidgets import QMessageBox
        ai_data = {
            "final": "Writing file...",
            "command": {"action": "WRITE_FILE", "path": "should_not_exist.txt", "content": "data"},
        }
        with patch("PyQt6.QtWidgets.QMessageBox.warning", return_value=QMessageBox.StandardButton.No):
            self.window.handle_ai_response(ai_data)

        audit = self.window.audit_log_display.toPlainText()
        self.assertIn("blocked", audit.lower())
        self.assertNotIn("Successfully wrote", audit)

        # Verify file was NOT created
        fp = os.path.join(r"C:\KokertechAI", "should_not_exist.txt")
        self.assertFalse(os.path.exists(fp), "File should NOT have been created")

    def test_delete_file_restricted_action_triggers_hitl(self):
        """
        DELETE_FILE is in the restricted list → HITL dialog is shown.
        """
        from PyQt6.QtWidgets import QMessageBox
        ai_data = {
            "final": "Deleting...",
            "command": {"action": "DELETE_FILE", "path": "some_file.txt"},
        }
        with patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_msgbox:
            mock_msgbox.return_value = QMessageBox.StandardButton.No
            self.window.handle_ai_response(ai_data)
            mock_msgbox.assert_called_once()

            # Verify the dialog showed the action name and target
            call_args = mock_msgbox.call_args[0]
            call_text = " ".join(str(a) for a in call_args)
            self.assertIn("DELETE_FILE", call_text)
            self.assertIn("some_file.txt", call_text)

# ===================================================================
# 5. CONFIG-DRIVEN MODE TOGGLING (via hotkey settings)
# ===================================================================

class TestConfigModePropagation(unittest.TestCase):
    """
    Tests that changing CONFIG values (as the settings tab does when applying
    hotkey changes) correctly propagates to controller behavior.
    """

    def setUp(self):
        self.mocks = patch.multiple(
            "memory_vault",
            semantic_search=MagicMock(return_value=[]),
            store_memory=MagicMock(),
            get_recent_bias=MagicMock(return_value=[]),
            get_growth_arc=MagicMock(return_value=[]),
            ensure_tables_exist=MagicMock(),
        )
        self.mocks.start()
        _enable_all_plugins()

    def tearDown(self):
        self.mocks.stop()

    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": False}, clear=False)
    def test_mock_mode_active_bypasses_provider(self):
        """
        When mock_mode is True (set via hotkey settings), process_input
        should not call the provider.
        """
        ctrl = _ctrl()
        ctrl.memory_limit = 999
        with patch.object(ctrl, "provider") as mp:
            ctrl.process_input("Test", log_callback=lambda x: None)
            mp.chat_completion.assert_not_called()

    @patch.dict("config.CONFIG", {"mock_mode": False, "freeform_mode": True}, clear=False)
    def test_freeform_mode_active_uses_freeform_parsing(self):
        """
        When freeform_mode is True, controller uses parse_ai(freeform=True).
        """
        ctrl = _ctrl()
        # Patch chat_completion to return a valid response (non-mock path)
        # instead of using a HTTP mock (local LLM provider doesn't use HTTP).
        valid_result = {
            "content": "OK",
            "model": "test",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "error": None,
            "tool_calls": [],
        }
        with patch.object(ctrl.provider, 'chat_completion', return_value=valid_result):
            with patch.object(ctrl, "parse_ai", wraps=ctrl.parse_ai) as spy:
                ctrl.process_input("Hello", log_callback=lambda x: None)
                spy.assert_called_once()
                _, kwargs = spy.call_args
                self.assertTrue(kwargs.get("freeform", False))

    @patch.dict("config.CONFIG", {"mock_mode": False, "freeform_mode": False}, clear=False)
    def test_structured_mode_active_uses_xml_parsing(self):
        """
        When both modes are off, controller uses parse_ai(freeform=False).
        """
        ctrl = _ctrl()
        # Patch chat_completion to return a valid response (non-mock path)
        # instead of using a HTTP mock (local LLM provider doesn't use HTTP).
        valid_result = {
            "content": "OK",
            "model": "test",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "error": None,
            "tool_calls": [],
        }
        with patch.object(ctrl.provider, 'chat_completion', return_value=valid_result):
            with patch.object(ctrl, "parse_ai", wraps=ctrl.parse_ai) as spy:
                ctrl.process_input("Hello", log_callback=lambda x: None)
                spy.assert_called_once()
                _, kwargs = spy.call_args
                self.assertFalse(kwargs.get("freeform", True))

    def test_hotkey_config_changes_affect_apply_hotkey_registration(self):
        """
        Changing CONFIG hotkey values (as _apply_hotkeys does) should cause
        apply_hotkey to register new hotkeys with the updated values.
        This tests the config → hotkey registration propagation.
        """
        from config import CONFIG
        # Set up dashboard with real keyboard mock
        import app_core
        mock_kb = MagicMock()
        with patch("app_hotkeys._get_keyboard_module", return_value=mock_kb):
            window, _ = _create_dashboard_with_mocks()

            # Change hotkey config (as settings tab would)
            CONFIG["trigger_hotkey"] = "alt+9"
            CONFIG["read_hotkey"] = "alt+8"
            CONFIG["voice_hotkey"] = "alt+7"

            # Re-apply (as _apply_hotkeys does)
            window.apply_hotkey()

            expected_calls = [
                call("alt+9", window._hotkey_trigger_analysis),
                call("alt+8", window._hotkey_read_selected_text),
                call("alt+7", window._hotkey_voice_input),
            ]
            mock_kb.add_hotkey.assert_has_calls(expected_calls, any_order=False)

            _safe_close(window)

# ===================================================================
# 6. DASHBOARD action_send_prompt → CONTROLLER → PLUGIN (SYNCHRONOUS MOCK)
# ===================================================================

class TestDashboardSendPromptPluginDispatch(unittest.TestCase):
    """
    Tests the action_send_prompt → AIWorker (mocked synchronous) →
    handle_ai_response → plugin dispatch chain using a real dashboard
    with the AIWorker replaced by a synchronous mock.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, shared_dashboard):
        """Use module-scoped shared Dashboard and reset state per test."""
        self.window = shared_dashboard
        # Null out stale worker references from prior tests so the
        # action_send_prompt guard (which checks worker.isRunning())
        # doesn't fire and skip setting last_user_text.
        for attr in ('worker', 'multi_worker'):
            old = getattr(self.window, attr, None)
            if old is not None:
                try:
                    if hasattr(old, 'requestInterruption'):
                        old.requestInterruption()
                except Exception:
                    pass
            setattr(self.window, attr, None)
        self.window._ab_test_running = False
        self.window.last_user_text = ""
        self.window.chat_display.clear()
        self.window.audit_log_display.clear()
        self.window.file_logger.reset_mock()
        _enable_all_plugins()
        yield
        # Prevent C++ state accumulation across test classes (avoids STATUS_FATAL_APP_EXIT)
        gc.collect()
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            for _ in range(5):
                app.processEvents()

    def test_action_send_prompt_with_mock_worker_triggers_plugin_dispatch(self):
        """
        After action_send_prompt, when the AIWorker reply comes back with
        a command, handle_ai_response dispatches to the plugin.
        We simulate this by calling action_send_prompt (which creates the worker
        and stores last_user_text) then manually emitting the reply.

        NOTE: process_input is patched for the ENTIRE test method to avoid
        a race condition where the AIWorker's background thread might call
        process_input() on a real LocalLLMProvider (which would try to load
        a model and hang for 5-15s+). handle_ai_response does not call
        process_input, so the patch does not affect that part of the test.
        """
        from PyQt6.QtWidgets import QMessageBox

        mock_result = {
            "final": "[MOCK] System status check complete.",
            "thinking": "",
            "command": {"action": "SYSTEM_STATUS"},
            "ts": "12:00:00",
        }
        # Patch covers the ENTIRE test to avoid race condition with async QThread
        with patch.object(self.window.controller, 'process_input', return_value=mock_result):
            # Set input text (like _do_hotkey_trigger does)
            self.window.txt_input.setPlainText("Check the system status")

            # Call action_send_prompt (which clears input, stores last_user_text, creates worker)
            self.window.action_send_prompt()

            # Verify last_user_text was stored
            self.assertEqual(self.window.last_user_text, "Check the system status")
            # Verify input was cleared
            self.assertEqual(self.window.txt_input.toPlainText(), "")

            # Now simulate the worker replying with a command
            ai_data = {
                "final": "System status check complete.",
                "thinking": "Running diagnostics...",
                "command": {"action": "SYSTEM_STATUS"},
                "ts": "12:00:00",
            }
            with patch("PyQt6.QtWidgets.QMessageBox.warning", return_value=None):
                self.window.handle_ai_response(ai_data)

            # Verify results while still inside the process_input patch context
            chat = self.window.chat_display.toPlainText()
            self.assertIn("System status check complete.", chat)

            audit = self.window.audit_log_display.toPlainText()
            self.assertIn("Dispatched action: SYSTEM_STATUS", audit)

            self.assertTrue(
                any(kw in chat.lower() for kw in ["cpu", "memory", "disk", "system"]),
                f"Expected system info in chat, got: {chat[:150]}",
            )

    def test_action_send_prompt_plain_text_no_command(self):
        """
        When the worker reply has no command, plugin dispatch is skipped.

        NOTE: process_input is patched for the ENTIRE test method to avoid
        the AIWorker background thread hitting the real LocalLLMProvider
        (which would try to load a model and slow the test down by 25s+).
        handle_ai_response does not call process_input, so the patch does
        not affect that part of the test.
        """
        mock_result = {
            "final": "[MOCK] Hello there!",
            "thinking": "",
            "command": None,
            "ts": "12:00:00",
        }
        with patch.object(self.window.controller, 'process_input', return_value=mock_result):
            self.window.txt_input.setPlainText("Say hello")
            self.window.action_send_prompt()

            ai_data = {
                "final": "Hello there!",
                "thinking": "",
                "ts": "12:00:00",
            }
            self.window.handle_ai_response(ai_data)

            chat = self.window.chat_display.toPlainText()
            self.assertIn("Hello there!", chat)
            self.assertNotIn("Dispatched", self.window.audit_log_display.toPlainText())

if __name__ == "__main__":
    unittest.main()
