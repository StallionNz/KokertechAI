"""
Integration tests for the combined freeform + mock + plugin pathways.

Tests the full command extraction and dispatch chain:
  AI response → parse_ai (freeform/structured) → command extraction →
  kokertech_bridge.handle_ai_intent → plugin_registry.execute_command → plugin result

Also verifies plugin schema injection into system prompts for both modes.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

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
    # Also reset the Sprint 3 service's internal history so facade mirrors
    # stay in sync after _refresh_history_mirror() calls.
    try:
        c._history_svc.history = []
    except Exception:
        pass
    return c

def _enable_all_plugins():
    """Ensure all loaded plugins are enabled."""
    import plugin_registry
    plugin_registry.registry.disabled.clear()

def _disable_plugin(command_name):
    """Disable a specific plugin by command name."""
    import plugin_registry
    plugin_registry.registry.disabled.add(command_name)

# ===================================================================
# 1. PARSE_AI → BRIDGE → PLUGIN DISPATCH CHAIN
# ===================================================================

class TestParseAiToPluginDispatch(unittest.TestCase):
    """parse_ai extracts JSON commands → bridge dispatches to real plugins."""

    def setUp(self):
        _enable_all_plugins()

    # --- Freeform mode ---

    def test_freeform_search_web_dispatch(self):
        """Freeform parse_ai extracts SEARCH_WEB and bridge dispatches to the real plugin."""
        ctrl = _ctrl()
        result = ctrl.parse_ai(
            '{"action": "SEARCH_WEB", "query": "AI news"}',
            freeform=True,
        )
        self.assertIsNotNone(result["command"])
        self.assertEqual(result["command"]["action"], "SEARCH_WEB")

        import kokertech_bridge
        bridge_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIsInstance(bridge_result, str)
        self.assertTrue(len(bridge_result) > 0)

    def test_freeform_write_file_creates_cleans_up(self):
        """Freeform parse_ai extracts WRITE_FILE → bridge writes file → we clean up."""
        ctrl = _ctrl()
        d = {"action": "WRITE_FILE", "path": "_test_int_tmp.txt", "content": "integration data"}
        result = ctrl.parse_ai(json.dumps(d), freeform=True)
        self.assertIsNotNone(result["command"])

        import kokertech_bridge
        bridge_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIn("Successfully", bridge_result)

        # Cleanup
        fp = os.path.join(r"C:\KokertechAI", "_test_int_tmp.txt")
        if os.path.exists(fp):
            os.remove(fp)

    def test_freeform_system_status_dispatch(self):
        """Freeform parse_ai extracts SYSTEM_STATUS → bridge returns system info."""
        ctrl = _ctrl()
        result = ctrl.parse_ai('{"action": "SYSTEM_STATUS"}', freeform=True)
        self.assertIsNotNone(result["command"])

        import kokertech_bridge
        bridge_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIsInstance(bridge_result, str)
        self.assertTrue(
            any(kw in bridge_result.lower() for kw in ["cpu", "memory", "disk", "system", "host"]),
            f"Expected system info keywords, got: {bridge_result[:100]}",
        )

    # --- Structured mode ---

    def test_structured_search_web_in_final_output(self):
        """Structured parse_ai extracts SEARCH_WEB from <final_output>."""
        ctrl = _ctrl()
        content = (
            "<thinking>I should search the web</thinking>\n"
            '<final_output>{"action": "SEARCH_WEB", "query": "python testing"}</final_output>'
        )
        result = ctrl.parse_ai(content, freeform=False)
        self.assertIsNotNone(result["command"])
        self.assertEqual(result["command"]["action"], "SEARCH_WEB")
        self.assertEqual(result["thinking"], "I should search the web")

        import kokertech_bridge
        bridge_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIsInstance(bridge_result, str)

    def test_structured_write_file_from_final_output(self):
        """Structured parse_ai extracts WRITE_FILE command and bridge executes it."""
        ctrl = _ctrl()
        content = (
            "<thinking>Need to save data</thinking>\n"
            '<final_output>{"action": "WRITE_FILE", "path": "_test_struct.txt", "content": "structured test"}</final_output>'
        )
        result = ctrl.parse_ai(content, freeform=False)
        self.assertIsNotNone(result["command"])

        import kokertech_bridge
        bridge_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIn("Successfully", bridge_result)

        # Cleanup
        fp = os.path.join(r"C:\KokertechAI", "_test_struct.txt")
        if os.path.exists(fp):
            os.remove(fp)

    # --- No JSON edge cases ---

    def test_freeform_no_json_no_command(self):
        """Freeform parse_ai with no JSON returns command=None."""
        ctrl = _ctrl()
        result = ctrl.parse_ai("Just a plain conversational response.", freeform=True)
        self.assertIsNone(result["command"])
        self.assertEqual(result["final"], "Just a plain conversational response.")

    def test_structured_no_json_no_command(self):
        """Structured parse_ai with no JSON returns command=None."""
        ctrl = _ctrl()
        content = "<thinking>No action needed</thinking>\n<final_output>Hello there!</final_output>"
        result = ctrl.parse_ai(content, freeform=False)
        self.assertIsNone(result["command"])
        self.assertEqual(result["final"], "Hello there!")

# ===================================================================
# 2. PROCESS_INPUT FULL CYCLE — MOCK MODE + PLUGIN
# ===================================================================

class TestMockModeFullCycle(unittest.TestCase):
    """Mock mode → process_input → parse_ai → plugin dispatch."""

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

    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": True}, clear=False)
    def test_mock_mode_returns_valid_structure(self):
        """Mock mode returns a well-formed response dict without calling the provider."""
        ctrl = _ctrl()
        with patch.object(ctrl, "provider") as mp:
            result = ctrl.process_input("Hello", log_callback=lambda x: None)
            mp.chat_completion.assert_not_called()

        self.assertIn("final", result)
        self.assertIn("thinking", result)
        self.assertIn("ts", result)
        self.assertIn("command", result)
        self.assertIn("[MOCK]", result["final"])

    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": True}, clear=False)
    def test_mock_mode_history_has_two_entries(self):
        """Mock mode appends user + assistant to history."""
        ctrl = _ctrl()
        ctrl.memory_limit = 999  # prevent summarization
        ctrl.process_input("Test", log_callback=lambda x: None)
        self.assertEqual(len(ctrl.history), 2)
        self.assertEqual(ctrl.history[0]["role"], "user")
        self.assertEqual(ctrl.history[1]["role"], "assistant")

    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": False}, clear=False)
    def test_mock_mode_calls_parse_ai_with_freeform_true(self):
        """Mock mode always uses freeform parsing internally."""
        ctrl = _ctrl()
        with patch.object(ctrl, "parse_ai", wraps=ctrl.parse_ai) as spy:
            with patch.object(ctrl, "provider") as mp:
                ctrl.process_input("Hello", log_callback=lambda x: None)
                mp.chat_completion.assert_not_called()
            spy.assert_called_once()
            _, kwargs = spy.call_args
            self.assertTrue(kwargs.get("freeform", False))

    def test_mock_mode_command_flows_through_bridge(self):
        """The result dict from parse_ai can be dispatched through the bridge."""
        ctrl = _ctrl()
        parse_result = ctrl.parse_ai(
            '{"action": "SYSTEM_STATUS"}',
            freeform=True,
        )
        self.assertIsNotNone(parse_result["command"])

        import kokertech_bridge
        bridge_result = kokertech_bridge.handle_ai_intent(parse_result["command"])
        self.assertIsInstance(bridge_result, str)

    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": False}, clear=False)
    def test_mock_mode_repeated_calls_unique(self):
        """Repeated mock calls yield different responses (random selection)."""
        ctrl = _ctrl()
        ctrl.memory_limit = 999
        results = set()
        for i in range(10):
            r = ctrl.process_input(f"Msg {i}", log_callback=lambda x: None)
            results.add(r["final"])
        self.assertGreater(len(results), 1, "Expected > 1 unique mock responses")

# ===================================================================
# 3. PROCESS_INPUT FULL CYCLE — FREEFORM MODE + PLUGIN
# ===================================================================

class TestFreeformModeFullCycle(unittest.TestCase):
    """Freeform mode with mocked provider → command → plugin dispatch."""

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
    def test_freeform_ai_response_with_command_flows_through(self):
        """Freeform mode: AI responds with JSON command → parse_ai extracts it → bridge dispatches."""
        valid_result = {
            "content": '{"action": "SYSTEM_STATUS"}',
            "model": "test",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "error": None,
            "tool_calls": [],
        }
        ctrl = _ctrl()
        with patch.object(ctrl.provider, "chat_completion", return_value=valid_result):
            result = ctrl.process_input("Check system", log_callback=lambda x: None)
        self.assertIsNotNone(result["command"])
        self.assertEqual(result["command"]["action"], "SYSTEM_STATUS")

        import kokertech_bridge
        bridge_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIsInstance(bridge_result, str)

    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": False}, clear=False)
    def test_freeform_ai_response_write_file(self):
        """Freeform mode: AI says to write a file → bridge writes it."""
        valid_result = {
            "content": json.dumps({
                "action": "WRITE_FILE",
                "path": "_test_freeform_tmp.txt",
                "content": "freeform integration test",
            }),
            "model": "test",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "error": None,
            "tool_calls": [],
        }
        ctrl = _ctrl()
        with patch.object(ctrl.provider, "chat_completion", return_value=valid_result):
            result = ctrl.process_input("Write a test file", log_callback=lambda x: None)
        self.assertIsNotNone(result["command"])
        self.assertEqual(result["command"]["action"], "WRITE_FILE")

        import kokertech_bridge
        bridge_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIn("Successfully", bridge_result)

        # Cleanup
        fp = os.path.join(r"C:\KokertechAI", "_test_freeform_tmp.txt")
        if os.path.exists(fp):
            os.remove(fp)

    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": False}, clear=False)
    def test_freeform_no_command_plain_response(self):
        """Freeform mode with no command returns command=None."""
        valid_result = {
            "content": "Sure, I can help with that!",
            "model": "test",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "error": None,
            "tool_calls": [],
        }
        ctrl = _ctrl()
        with patch.object(ctrl.provider, "chat_completion", return_value=valid_result):
            result = ctrl.process_input("Hello", log_callback=lambda x: None)
        self.assertIsNone(result["command"])

    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": False}, clear=False)
    def test_freeform_passes_freeform_true_to_parse_ai(self):
        """Freeform mode calls parse_ai with freeform=True."""
        valid_result = {
            "content": "OK",
            "model": "test",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "error": None,
            "tool_calls": [],
        }
        ctrl = _ctrl()
        with patch.object(ctrl.provider, "chat_completion", return_value=valid_result):
            with patch.object(ctrl, "parse_ai", wraps=ctrl.parse_ai) as spy:
                ctrl.process_input("Hello", log_callback=lambda x: None)
            spy.assert_called_once()
            _, kwargs = spy.call_args
            self.assertTrue(kwargs.get("freeform", False))

# ===================================================================
# 4. PROCESS_INPUT FULL CYCLE — STRUCTURED MODE + PLUGIN
# ===================================================================

class TestStructuredModeFullCycle(unittest.TestCase):
    """Structured mode → process_input → parse_ai(freeform=False) → command → plugin."""

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

    @patch.dict("config.CONFIG", {"freeform_mode": False, "mock_mode": False}, clear=False)
    def test_structured_extracts_command_and_dispatches(self):
        """Structured mode: AI responds with XML → parse_ai extracts command → bridge dispatches."""
        valid_result = {
            "content": (
                "<thinking>Checking system health</thinking>\n"
                '<final_output>{"action": "SYSTEM_STATUS"}</final_output>'
            ),
            "model": "test",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "error": None,
            "tool_calls": [],
        }
        ctrl = _ctrl()
        with patch.object(ctrl.provider, "chat_completion", return_value=valid_result):
            result = ctrl.process_input("Status check", log_callback=lambda x: None)
        self.assertIsNotNone(result["command"])
        self.assertEqual(result["command"]["action"], "SYSTEM_STATUS")
        self.assertEqual(result["thinking"], "Checking system health")

        import kokertech_bridge
        bridge_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIsInstance(bridge_result, str)

    @patch.dict("config.CONFIG", {"freeform_mode": False, "mock_mode": False}, clear=False)
    def test_structured_error_handling(self):
        """Structured mode: provider returns error → process_input returns error dict."""
        error_result = {
            "content": "",
            "model": "test",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "error": "API error: 400 Bad Request",
            "tool_calls": [],
        }
        ctrl = _ctrl()
        with patch.object(ctrl.provider, "chat_completion", return_value=error_result):
            result = ctrl.process_input("Hello", log_callback=lambda x: None)
        self.assertIn("error", result)

# ===================================================================
# 5. DISABLED PLUGIN HANDLING
# ===================================================================

class TestPluginDisabledIntegration(unittest.TestCase):
    """When a plugin is disabled, commands targeting it fail gracefully."""

    def setUp(self):
        _enable_all_plugins()

    def tearDown(self):
        _enable_all_plugins()

    def test_bridge_returns_disabled_error(self):
        """Bridge returns a clear error when the target plugin is disabled."""
        _disable_plugin("SEARCH_WEB")

        import kokertech_bridge
        intent = {"action": "SEARCH_WEB", "query": "test"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("disabled", result.lower())

    def test_bridge_succeeds_after_reenable(self):
        """After re-enabling, commands dispatch successfully."""
        _disable_plugin("SEARCH_WEB")
        _enable_all_plugins()  # re-enable

        import kokertech_bridge
        intent = {"action": "SEARCH_WEB", "query": "test"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertNotIn("disabled", result.lower())

    def test_parse_ai_still_extracts_disabled_plugin_command(self):
        """parse_ai extracts commands regardless of plugin enable state."""
        ctrl = _ctrl()
        _disable_plugin("WRITE_FILE")

        content = '{"action": "WRITE_FILE", "path": "test.txt", "content": "data"}'
        result = ctrl.parse_ai(content, freeform=True)
        self.assertIsNotNone(result["command"])
        self.assertEqual(result["command"]["action"], "WRITE_FILE")

        # But bridge should fail
        import kokertech_bridge
        bridge_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIn("disabled", bridge_result.lower())

    def test_nonexistent_command(self):
        """Bridge returns error for unknown actions."""
        import kokertech_bridge
        intent = {"action": "DOES_NOT_EXIST_99999"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("unknown", result.lower())

    def test_mock_mode_plus_disabled_plugin_chain(self):
        """Full chain: parse_ai → disabled plugin → bridge returns error."""
        _disable_plugin("WRITE_FILE")
        ctrl = _ctrl()
        result = ctrl.parse_ai(
            '{"action": "WRITE_FILE", "path": "x.txt", "content": "test"}',
            freeform=True,
        )
        self.assertIsNotNone(result["command"])

        import kokertech_bridge
        bridge_result = kokertech_bridge.handle_ai_intent(result["command"])
        self.assertIn("disabled", bridge_result.lower())

# ===================================================================
# 6. PLUGIN ERROR HANDLING
# ===================================================================

class TestPluginErrorHandling(unittest.TestCase):
    """Plugins handle missing parameters and exceptions gracefully."""

    def setUp(self):
        _enable_all_plugins()

    def test_search_web_missing_query(self):
        """SEARCH_WEB without query param returns error message."""
        import kokertech_bridge
        intent = {"action": "SEARCH_WEB"}  # missing 'query'
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("Missing", result)

    def test_fetch_web_missing_url(self):
        """FETCH_WEB without url param returns error message."""
        import kokertech_bridge
        intent = {"action": "FETCH_WEB"}  # missing 'url'
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("Missing", result)

    def test_write_file_missing_path(self):
        """WRITE_FILE without path returns error."""
        import kokertech_bridge
        intent = {"action": "WRITE_FILE", "content": "data"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("missing", result.lower())

    def test_read_file_nonexistent_path(self):
        """READ_FILE with nonexistent path returns error."""
        import kokertech_bridge
        intent = {"action": "READ_FILE", "path": "_nonexistent_file_9999.txt"}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIn("not found", result.lower())

    def test_list_files_valid_path(self):
        """LIST_FILES with valid path returns directory listing."""
        import kokertech_bridge
        intent = {"action": "LIST_FILES", "path": "."}
        result = kokertech_bridge.handle_ai_intent(intent)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

# ===================================================================
# 7. PLUGIN SCHEMA IN SYSTEM PROMPT
# ===================================================================

class TestPluginSystemPromptInjection(unittest.TestCase):
    """Plugin schemas are injected into the system prompt for both modes."""

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

    def tearDown(self):
        self.mocks.stop()

    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": False, "protocol_prompt": ""}, clear=False)
    def test_plugin_schemas_in_freeform_prompt(self):
        """Freeform mode system prompt includes DYNAMIC PLUGINS section with schemas."""
        valid_result = {
            "content": "OK",
            "model": "test",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "error": None,
            "tool_calls": [],
        }
        ctrl = _ctrl()
        with patch.object(ctrl.provider, "chat_completion", return_value=valid_result) as mock_cc:
            with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):
                ctrl.process_input("Test", log_callback=lambda x: None)

        # Capture call_args from the PAT cloned mock (patch.object returns the
        # patched mock, NOT the original — so call_args is available AFTER
        # the with-block via the returned mock object).
        self.assertIsNotNone(mock_cc.call_args, "chat_completion was not called")
        _, kwargs = mock_cc.call_args
        msgs = kwargs["messages"]
        system_msg = next(m for m in msgs if m["role"] == "system")
        content = system_msg["content"]

        # Freeform mode prompt has no XML tags
        self.assertNotIn("<thinking>", content)
        # But DOES include skill category summary (without square brackets)
        self.assertIn("AVAILABLE SKILL CATEGORIES", content)
        self.assertIn("SEARCH_WEB", content)
        self.assertIn("WRITE_FILE", content)

    @patch.dict("config.CONFIG", {"freeform_mode": False, "mock_mode": False, "protocol_prompt": ""}, clear=False)
    def test_plugin_schemas_in_structured_prompt(self):
        """Structured mode system prompt includes DYNAMIC PLUGINS section."""
        valid_result = {
            "content": "OK",
            "model": "test",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "error": None,
            "tool_calls": [],
        }
        ctrl = _ctrl()
        with patch.object(ctrl.provider, "chat_completion", return_value=valid_result) as mock_cc:
            with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):
                ctrl.process_input("Test", log_callback=lambda x: None)

        # Capture call_args from the PAT cloned mock (same reasoning as freeform test)
        self.assertIsNotNone(mock_cc.call_args, "chat_completion was not called")
        _, kwargs = mock_cc.call_args
        msgs = kwargs["messages"]
        system_msg = next(m for m in msgs if m["role"] == "system")
        content = system_msg["content"]

        # Structured mode prompt HAS XML tags
        self.assertIn("<thinking>", content)
        self.assertIn("<final_output>", content)
        # AND includes skill category summary (without square brackets)
        self.assertIn("AVAILABLE SKILL CATEGORIES", content)
        self.assertIn("SEARCH_WEB", content)

    def test_plugin_schemas_in_mock_mode(self):
        """get_system_prompt_addition returns schemas (used internally in both modes)."""
        import plugin_registry
        schemas = plugin_registry.registry.get_system_prompt_addition()
        self.assertIn("SEARCH_WEB", schemas)

# ===================================================================
# 8. MOCK + FREEFORM COMBINATION
# ===================================================================

class TestMockFreeformCombo(unittest.TestCase):
    """Mock mode + freeform mode simultaneously — no provider call, freeform parsing."""

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

    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": True}, clear=False)
    def test_mock_freeform_no_provider_call(self):
        """Both modes active: provider is never called."""
        ctrl = _ctrl()
        with patch.object(ctrl, "provider") as mp:
            ctrl.process_input("Hello", log_callback=lambda x: None)
            mp.chat_completion.assert_not_called()

    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": True}, clear=False)
    def test_mock_freeform_thinking_empty(self):
        """Freeform parsing in mock mode results in empty thinking."""
        ctrl = _ctrl()
        result = ctrl.process_input("Hello", log_callback=lambda x: None)
        self.assertEqual(result["thinking"], "")

    def test_mock_freeform_command_extraction(self):
        """Freeform parsing extracts commands from text."""
        ctrl = _ctrl()
        result = ctrl.parse_ai(
            '{"action": "SYSTEM_STATUS"}',
            freeform=True,
        )
        self.assertIsNotNone(result["command"])

# ===================================================================
# 9. BRIDGE + REGISTRY EDGE CASES
# ===================================================================

class TestBridgeEdgeCases(unittest.TestCase):
    """Edge cases around the bridge/registry boundary."""

    def setUp(self):
        _enable_all_plugins()

    def test_extract_valid_actions_parses_embedded_json(self):
        """extract_valid_actions finds JSON objects with 'action' keys."""
        import kokertech_bridge
        text = (
            "Some text before. "
            '{"action": "SEARCH_WEB", "query": "test"} '
            "Some text in between. "
            '{"action": "WRITE_FILE", "path": "test.txt", "content": "data"} '
            "Some text after."
        )
        commands = kokertech_bridge.extract_valid_actions(text)
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0]["action"], "SEARCH_WEB")
        self.assertEqual(commands[1]["action"], "WRITE_FILE")

    def test_extract_valid_actions_ignores_non_action_json(self):
        """extract_valid_actions ignores JSON without 'action' keys."""
        import kokertech_bridge
        text = '{"temperature": 72, "unit": "F"}'
        commands = kokertech_bridge.extract_valid_actions(text)
        self.assertEqual(len(commands), 0)

    def test_extract_valid_actions_empty(self):
        """extract_valid_actions returns empty list for text with no JSON."""
        import kokertech_bridge
        commands = kokertech_bridge.extract_valid_actions("Just plain text.")
        self.assertEqual(len(commands), 0)

    def test_registry_get_plugin_count(self):
        """Plugin registry reports a positive plugin count."""
        import plugin_registry
        count = plugin_registry.registry.get_plugin_count()
        self.assertGreater(count, 0)
        self.assertEqual(
            count,
            len(plugin_registry.registry.plugins),
        )

    def test_registry_list_and_metadata(self):
        """Plugin registry listing includes expected metadata."""
        import plugin_registry
        listing = plugin_registry.registry.list_plugins()
        self.assertIn("SEARCH_WEB", listing)
        self.assertIn("WRITE_FILE", listing)

    def test_plugin_schema_presence_in_metadata(self):
        """Each plugin's metadata includes its schema."""
        import plugin_registry
        for cmd in plugin_registry.registry.plugins:
            meta = plugin_registry.registry.metadata.get(cmd, {})
            self.assertIn("schema", meta, f"Plugin '{cmd}' missing schema in metadata")
            self.assertIn("action", meta["schema"])

if __name__ == "__main__":
    unittest.main()
