"""Unit tests for kokertechController.py — freeform/mock mode paths and parse_ai edge cases."""


import inspect


import json


import os


import tempfile


import shutil


import logging


import sys


import threading


import time


import sqlite3
import unittest


from unittest.mock import patch, MagicMock, ANY


import memory_vault


from kokertechController import KokertechController


from config import CONFIG


# ---------------------------------------------------------------------------


# Helper


# ---------------------------------------------------------------------------


def _make_controller():


    """Return a KokertechController with history reset (provider mocked)."""


    from unittest.mock import patch


    with patch("kokertechController.get_provider"):


        from kokertechController import KokertechController


        ctrl = KokertechController()


    ctrl.history = []


    ctrl._history_svc.history = []


    ctrl.compressed_context_summary = ""


    ctrl._history_svc.compressed_context_summary = ""


    return ctrl


class TestControllerMockMode(unittest.TestCase):


    """Mock mode: simulated responses without calling the AI provider."""


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


    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": False}, clear=False)


    def test_mock_returns_without_calling_provider(self):


        ctrl = _make_controller()


        with patch.object(ctrl, "provider") as mp:


            result = ctrl.process_input("Hello", log_callback=lambda x: None)


            mp.chat_completion.assert_not_called()


        self.assertIsNotNone(result)


        self.assertIn("final", result)


        self.assertIn("[MOCK]", result["final"])


    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": False}, clear=False)


    def test_mock_updates_history(self):


        ctrl = _make_controller()


        ctrl.process_input("Test message", log_callback=lambda x: None)


        self.assertEqual(len(ctrl.history), 2)


        self.assertEqual(ctrl.history[0]["role"], "user")


        self.assertEqual(ctrl.history[0]["content"], "Test message")


        self.assertEqual(ctrl.history[1]["role"], "assistant")


        self.assertIn("[MOCK]", ctrl.history[1]["content"])


    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": False}, clear=False)


    def test_mock_uses_freeform_parsing(self):


        ctrl = _make_controller()


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertEqual(result["thinking"], "")


        self.assertIsNotNone(result["final"])


    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": True}, clear=False)


    def test_mock_plus_freeform(self):


        ctrl = _make_controller()


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIn("[MOCK]", result["final"])


        self.assertEqual(result["thinking"], "")


    @patch.dict("config.CONFIG", {"mock_mode": True, "freeform_mode": False}, clear=False)


    def test_repeated_mock_different_responses(self):


        ctrl = _make_controller()


        ctrl.memory_limit = 999  # prevent _summarize_and_evict from triggering


        results = set()


        for i in range(10):


            r = ctrl.process_input(f"Msg {i}", log_callback=lambda x: None)


            results.add(r["final"])


        self.assertGreater(len(results), 1, "Expected > 1 unique mock responses")


class TestControllerFreeformMode(unittest.TestCase):


    """Freeform mode: relaxed system prompt, natural responses."""


    def setUp(self):


        self.mocks = patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            store_memory=MagicMock(),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            ensure_tables_exist=MagicMock(),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(return_value=""),


        )


        self.mocks.start()


    def tearDown(self):


        self.mocks.stop()


    @patch.dict("config.CONFIG", {"protocol_prompt": "", "freeform_mode": True, "mock_mode": False}, clear=False)


    def test_freeform_calls_parse_ai_with_freeform_param(self):


        ctrl = _make_controller()
        ctrl.provider.chat_completion = MagicMock(return_value={
            "content": "Natural reply", "model": "test", "usage": {}, "error": None,
        })
        ctrl.provider.chat_completion = MagicMock(return_value={
            "content": "Natural reply", "model": "test", "usage": {}, "error": None,
        })


        with patch.object(ctrl, "parse_ai", wraps=ctrl.parse_ai) as spy:


            ctrl.process_input("Hello", log_callback=lambda x: None)


            spy.assert_called_once()


            _, kwargs = spy.call_args


            self.assertTrue(kwargs.get("freeform", False))


    @patch.dict("config.CONFIG", {"protocol_prompt": "", "freeform_mode": True, "mock_mode": False}, clear=False)


    def test_freeform_system_prompt_no_xml(self):


        ctrl = _make_controller()


        with patch.object(ctrl.provider, "chat_completion", wraps=ctrl.provider.chat_completion) as spy:


            with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):


                ctrl.process_input("Hello", log_callback=lambda x: None)


                spy.assert_called_once()


                _, kwargs = spy.call_args


                msgs = kwargs.get("messages", [])


                system_msg = next((m for m in msgs if m["role"] == "system"), None)


                self.assertIsNotNone(system_msg)


                content = system_msg["content"]


                self.assertNotIn("<thinking>", content)


                self.assertNotIn("<final_output>", content)


                self.assertIn("Respond naturally", content)


    @patch.dict("config.CONFIG", {"protocol_prompt": "", "freeform_mode": False, "mock_mode": False}, clear=False)


    def test_structured_system_prompt_has_xml(self):


        ctrl = _make_controller()


        with patch.object(ctrl.provider, "chat_completion", wraps=ctrl.provider.chat_completion) as spy:


            with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):


                ctrl.process_input("Hello", log_callback=lambda x: None)


                spy.assert_called_once()


                _, kwargs = spy.call_args


                msgs = kwargs.get("messages", [])


                system_msg = next((m for m in msgs if m["role"] == "system"), None)


                self.assertIsNotNone(system_msg)


                content = system_msg["content"]


                self.assertIn("<thinking>", content)


                self.assertIn("<final_output>", content)


class TestParseAi(unittest.TestCase):


    """Direct tests of the parse_ai method."""


    def setUp(self):


        self.ctrl = _make_controller()


        self.ctrl.history = []


    # --- freeform=True ---


    def test_freeform_plain_text(self):


        result = self.ctrl.parse_ai("Hi there!", freeform=True)


        self.assertEqual(result["final"], "Hi there!")


        self.assertEqual(result["thinking"], "")


        self.assertIsNone(result["command"])


    def test_freeform_with_json_command(self):


        content = 'Let me search. {"action": "SEARCH_WEB", "query": "AI news"}'


        result = self.ctrl.parse_ai(content, freeform=True)


        self.assertIsNotNone(result["command"])


        self.assertEqual(result["command"]["action"], "SEARCH_WEB")


        self.assertEqual(result["command"]["query"], "AI news")


        self.assertEqual(result["final"], content.strip())


    def test_freeform_invalid_json_ignored(self):


        content = "Text with {invalid: json, missing quotes}"


        result = self.ctrl.parse_ai(content, freeform=True)


        self.assertIsNone(result["command"])


    def test_freeform_empty_content(self):


        result = self.ctrl.parse_ai("", freeform=True)


        self.assertEqual(result["final"], "")


        self.assertEqual(result["thinking"], "")


    def test_freeform_json_no_action_key(self):


        content = 'Data: {"temperature": 72, "unit": "F"}'


        result = self.ctrl.parse_ai(content, freeform=True)


        self.assertIsNone(result["command"],


                          "JSON without 'action' key should not become a command")


    # --- freeform=False (structured) ---


    def test_structured_with_tags(self):


        content = "<thinking>Thinking...</thinking>\n<final_output>Answer.</final_output>"

        result = self.ctrl.parse_ai(content, freeform=False)
        self.assertEqual(result["thinking"], "Thinking...")
        self.assertEqual(result["final"], "Answer.")
        self.assertIsNone(result["command"])
        content = "Plain response without XML tags."










    def test_structured_no_tags_fallback(self):




        content = "Plain response without XML tags."
        result = self.ctrl.parse_ai(content, freeform=False)


        self.assertEqual(result["final"], content.strip())


        self.assertEqual(result["thinking"], "Synthesizing...")


    def test_structured_with_json_command(self):


        content = (


            "<thinking>Searching web</thinking>\n"



            '<final_output>{"action": "SEARCH_WEB", "query": "python"}</final_output>'


        )


        result = self.ctrl.parse_ai(content, freeform=False)


        self.assertIsNotNone(result["command"])


        self.assertEqual(result["command"]["action"], "SEARCH_WEB")


        self.assertEqual(result["command"]["query"], "python")


    def test_structured_partial_tags(self):


        content = "<thinking>Just thinking</thinking>\nNo final here."



        result = self.ctrl.parse_ai(content, freeform=False)


        self.assertEqual(result["thinking"], "Just thinking")


        self.assertEqual(result["final"], content.strip())


    def test_structured_timestamp(self):


        result = self.ctrl.parse_ai("Hello", freeform=False)


        self.assertIn("ts", result)


        self.assertRegex(result["ts"], r"\d{2}:\d{2}:\d{2}")


class TestControllerStructuredMode(unittest.TestCase):


    """Verify structured (default) mode still works end-to-end."""


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


    @patch.dict("config.CONFIG", {"freeform_mode": False, "mock_mode": False}, clear=False)





    def test_structured_parses_xml_response(self):
















































        ctrl = _make_controller()
        ctrl.provider.chat_completion = MagicMock(return_value={
            "content": "<thinking>Computing answer...</thinking>" + chr(10) + "<final_output>42</final_output>",
            "model": "test-model",
            "usage": {},
            "error": None,
        })



        result = ctrl.process_input("What is the meaning?", log_callback=lambda x: None)


        self.assertEqual(result["thinking"], "Computing answer...")


        self.assertEqual(result["final"], "42")


    @patch.dict("config.CONFIG", {"freeform_mode": False, "mock_mode": False}, clear=False)





    def test_structured_error_response(self):























        ctrl = _make_controller()
        ctrl.provider.chat_completion = MagicMock(return_value={

            "content": "",

            "model": "test-model",

            "usage": {},

            "error": "No model loaded",

        })





        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIn("error", result)


class TestProtocolOverride(unittest.TestCase):


    """Tests for the protocol_prompt override feature in kokertechController.py.
    Verifies the three-way branching logic:



      1. Custom protocol from CONFIG → used as system prompt


      2. No custom + freeform_mode → freeform prompt


      3. No custom + structured → XML-structured prompt


    """


    def setUp(self):


        self.mocks = patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            store_memory=MagicMock(),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            ensure_tables_exist=MagicMock(),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(return_value=""),


        )


        self.mocks.start()


    def tearDown(self):


        self.mocks.stop()


    # ── Custom protocol with freeform=False (structured mode) ─────────────


    @patch.dict(


        "config.CONFIG",


        {"protocol_prompt": "You are a pirate. ARRR!",


         "freeform_mode": False, "mock_mode": False},


        clear=False,


    )


    def test_custom_protocol_overrides_structured(self):


        """Custom protocol replaces the hardcoded structured prompt."""


        ctrl = _make_controller()


        with patch.object(ctrl.provider, "chat_completion",


                          wraps=ctrl.provider.chat_completion) as spy:


            with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):


                ctrl.process_input("Hello", log_callback=lambda x: None)


                _, kwargs = spy.call_args


                msgs = kwargs["messages"]


                system_msg = next(m for m in msgs if m["role"] == "system")


                content = system_msg["content"]


                # Custom protocol should be used (not XML tags)


                self.assertIn("You are a pirate", content)


                self.assertNotIn("CRITICAL COGNITIVE PROTOCOL", content)


                self.assertNotIn("<thinking>", content)


    # ── Custom protocol with freeform=True ────────────────────────────────


    @patch.dict(


        "config.CONFIG",


        {"protocol_prompt": "You are a pirate. ARRR!",


         "freeform_mode": True, "mock_mode": False},


        clear=False,


    )


    def test_custom_protocol_overrides_freeform(self):


        """Custom protocol overrides even when freeform mode is active."""


        ctrl = _make_controller()


        with patch.object(ctrl.provider, "chat_completion",


                          wraps=ctrl.provider.chat_completion) as spy:


            with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):


                ctrl.process_input("Hello", log_callback=lambda x: None)


                _, kwargs = spy.call_args


                msgs = kwargs["messages"]


                system_msg = next(m for m in msgs if m["role"] == "system")


                content = system_msg["content"]


                # Custom protocol should be used (not freeform prompt)


                self.assertIn("You are a pirate", content)


                self.assertNotIn("Respond naturally", content)


    # ── No custom protocol, freeform=True → freeform default ─────────────


    @patch.dict(


        "config.CONFIG",


        {"protocol_prompt": "",


         "freeform_mode": True, "mock_mode": False},


        clear=False,


    )


    def test_fallback_to_freeform_when_no_protocol(self):


        """With no custom protocol and freeform=True, use freeform default."""


        ctrl = _make_controller()


        with patch.object(ctrl.provider, "chat_completion",


                          wraps=ctrl.provider.chat_completion) as spy:


            with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):


                ctrl.process_input("Hello", log_callback=lambda x: None)


                _, kwargs = spy.call_args


                msgs = kwargs["messages"]


                system_msg = next(m for m in msgs if m["role"] == "system")


                content = system_msg["content"]


                self.assertNotIn("CRITICAL COGNITIVE PROTOCOL", content)


                self.assertIn("Respond naturally", content)


    # ── No custom protocol, freeform=False → structured default ──────────


    @patch.dict(


        "config.CONFIG",


        {"protocol_prompt": "",


         "freeform_mode": False, "mock_mode": False},


        clear=False,


    )


    def test_fallback_to_structured_when_no_protocol(self):


        """With no custom protocol and freeform=False, use structured default."""


        ctrl = _make_controller()


        with patch.object(ctrl.provider, "chat_completion",


                          wraps=ctrl.provider.chat_completion) as spy:


            with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):


                ctrl.process_input("Hello", log_callback=lambda x: None)


                _, kwargs = spy.call_args


                msgs = kwargs["messages"]


                system_msg = next(m for m in msgs if m["role"] == "system")


                content = system_msg["content"]


                self.assertIn("CRITICAL COGNITIVE PROTOCOL", content)


                self.assertIn("<thinking>", content)


                self.assertIn("<final_output>", content)


    # ── Protocol via clear=True (no leftover keys from config defaults) ────


    @patch.dict(


        "config.CONFIG",


        {"freeform_mode": False, "mock_mode": False},


        clear=True,


    )


    def test_no_protocol_in_config_still_defaults(self):


        """When CONFIG has no protocol_prompt key, falls back to defaults."""


        ctrl = _make_controller()


        with patch.object(ctrl.provider, "chat_completion",


                          wraps=ctrl.provider.chat_completion) as spy:


            with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):


                ctrl.process_input("Hello", log_callback=lambda x: None)


                _, kwargs = spy.call_args


                msgs = kwargs["messages"]


                system_msg = next(m for m in msgs if m["role"] == "system")


                content = system_msg["content"]


                self.assertIn("CRITICAL COGNITIVE PROTOCOL", content)


    # ── Protocol is whitespace only → fallback ───────────────────────────


    @patch.dict(


        "config.CONFIG",


        {"protocol_prompt": "   \n",



         "freeform_mode": True, "mock_mode": False},


        clear=False,


    )


    def test_whitespace_protocol_falls_back(self):


        """Whitespace-only protocol falls back to freeform default."""


        ctrl = _make_controller()


        with patch.object(ctrl.provider, "chat_completion",


                          wraps=ctrl.provider.chat_completion) as spy:


            with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):


                ctrl.process_input("Hello", log_callback=lambda x: None)


                _, kwargs = spy.call_args


                msgs = kwargs["messages"]


                system_msg = next(m for m in msgs if m["role"] == "system")


                content = system_msg["content"]


                self.assertIn("Respond naturally", content)


    # ── Custom protocol + mock mode ──────────────────────────────────────


    @patch.dict(


        "config.CONFIG",


        {"protocol_prompt": "You are a pirate. ARRR!",


         "freeform_mode": False, "mock_mode": True},


        clear=False,


    )


    def test_custom_protocol_with_mock_mode(self):


        """Mock mode works with custom protocol set (no provider call)."""


        ctrl = _make_controller()


        with patch.object(ctrl, "provider") as mp:


            result = ctrl.process_input("Hello", log_callback=lambda x: None)


            mp.chat_completion.assert_not_called()


        self.assertIsNotNone(result)


        self.assertIn("[MOCK]", result["final"])


    # ── Custom protocol + mock + freeform combined ───────────────────────


    @patch.dict(


        "config.CONFIG",


        {"protocol_prompt": "You are a robot.",


         "freeform_mode": True, "mock_mode": True},


        clear=False,


    )


    def test_custom_protocol_with_mock_and_freeform(self):


        """All three settings active: custom protocol + mock + freeform."""


        ctrl = _make_controller()


        with patch.object(ctrl, "provider") as mp:


            result = ctrl.process_input("Hello", log_callback=lambda x: None)


            mp.chat_completion.assert_not_called()


        self.assertIn("[MOCK]", result["final"])


        self.assertEqual(result["thinking"], "")


class TestProtocolOverrideSettingsTab(unittest.TestCase):


    """Tests for the Settings tab's protocol editor integration."""


    def setUp(self):


        from tabs.settings_tab import SettingsTabMixin


        self.obj = SettingsTabMixin()


        self.obj.protocol_edit = MagicMock(name="protocol_edit")


        self.obj.protocol_edit.toPlainText.return_value = ""


        self.obj.protocol_preset_combo = MagicMock(name="protocol_preset_combo")


        self.obj.protocol_preset_count = MagicMock(name="protocol_preset_count")


        self.obj.protocol_warning = MagicMock(name="protocol_warning")


        self.obj.chk_freeform = MagicMock(name="chk_freeform")


        self.obj.log_to_audit = MagicMock()


    def test_reset_protocol_clears_editor(self):


        """_reset_protocol clears the protocol editor."""


        self.obj.protocol_edit.toPlainText.return_value = "Some custom protocol"


        self.obj._reset_protocol()


        self.obj.protocol_edit.clear.assert_called_once()


    def test_reset_protocol_logs_audit(self):


        """_reset_protocol logs to audit."""


        self.obj._reset_protocol()


        self.obj.log_to_audit.assert_called_once()


        self.assertIn("reset", self.obj.log_to_audit.call_args[0][0].lower())


    def test_save_settings_includes_protocol(self):


        """save_settings writes protocol_prompt to CONFIG."""


        from config import CONFIG


        self.obj.protocol_edit.toPlainText.return_value = "Custom protocol here"


        # Stub out required attributes for save_settings


        self.obj.provider_combo = MagicMock()


        self.obj.provider_combo.currentText.return_value = "Local LLM"


        self.obj.vram_spinbox = MagicMock()


        self.obj.api_url_input = MagicMock()


        self.obj.api_key_input = MagicMock()


        self.obj.model_combo = MagicMock()


        self.obj.desire_model_input = MagicMock()


        self.obj.auditor_model_input = MagicMock()


        self.obj.vision_model_combo = MagicMock()


        self.obj.setting_theme_combo = MagicMock()


        self.obj.tts_enabled_cb = MagicMock()


        self.obj.tts_speed_spin = MagicMock()


        self.obj.tts_voice_combo = MagicMock()


        self.obj.persona_combo = MagicMock()


        self.obj.chk_freeform = MagicMock()


        self.obj.chk_mock = MagicMock()


        self.obj.hotkey_trigger_input = MagicMock()


        self.obj.hotkey_read_input = MagicMock()


        self.obj.hotkey_voice_input = MagicMock()


        self.obj.chk_always_on_top = MagicMock()


        self.obj.chk_debug_logging = MagicMock()


        self.obj.chk_autosave_logs = MagicMock()


        self.obj.chk_shutdown_on_exit = MagicMock()
        # Sprint 15+ regression guard (ANTI-FRAGILITY, see KNOWLEDGE.md §12):
        # save_settings() reads `self.chk_notifications.isChecked()` at
        # tabs/settings_tab.py L2254 (read site) to persist
        # CONFIG["notifications_enabled"]. Widget is CREATED at
        # tabs/settings_tab.py L465-468 inside `_create_notifications_group`.
        # Invariant: tests must mirror production widget surface.
        # The Settings tab added this checkbox AFTER the FATFO test surface
        # was last updated; without this stub, save_settings raises
        # AttributeError.
        # Stricter REGRESSION GUARD sibling (TBD): assert that
        # save_settings() round-trips CONFIG["notifications_enabled"] via
        # chk_notifications.isChecked() — pin once notification flow gains
        # its own test surface in Sprint 22+.
        self.obj.chk_notifications = MagicMock()


        self.obj.sandbox_enabled_cb = MagicMock()


        self.obj.sandbox_image_input = MagicMock()


        self.obj.sandbox_timeout_spin = MagicMock()


        self.obj.sandbox_memory_input = MagicMock()

        self.obj.models_dir_input = MagicMock()

        self.obj.llm_n_ctx_spin = MagicMock()

        self.obj.llm_n_gpu_spin = MagicMock()

        self.obj.llm_n_threads_spin = MagicMock()

        self.obj.discovery_url_input = MagicMock()


        self.obj.id_name_input = MagicMock()


        self.obj.id_tone_input = MagicMock()


        self.obj.id_pref_input = MagicMock()


        self.obj.id_bg_input = MagicMock()


        self.obj._update_mode_indicator = MagicMock()


        self.obj.apply_theme = MagicMock()


        self.obj.setup_vram_monitor = MagicMock()


        self.obj.log_to_audit = MagicMock()


        with patch("tabs.settings_tab.save_settings") as mock_save:


            with patch("tabs.settings_tab.save_identity"):


                self.obj.save_settings()


        self.assertEqual(CONFIG.get("protocol_prompt"), "Custom protocol here")


class TestFatfoSkill(unittest.TestCase):


    """Phase E / Sprint 16 / row 16.5 -- FATFO Skill wiring smoke tests.
    Targets ``KokertechController._inject_skill_policy`` in isolation



    (no DB, no embedding model, no HTTP). The full controller


    ``__init__`` is not required: we bind the unbound method to a tiny


    stub carrying just ``workspace`` + ``logger``.


    """
    SKILL_NAME = "l99"



    BASE = "base protocol here."


    SKILL_DOC = """## L99 Skill Body\nRefactor the codebase into clearly-named services.



    Trigger-tag: ftw."""


    def setUp(self):


        self._tmp = tempfile.mkdtemp(prefix="fatfo_test_")


        self._saved_skill = CONFIG.get("active_skill", "")


        skill_dir = os.path.join(self._tmp, ".blackbox", "skills", self.SKILL_NAME)


        os.makedirs(skill_dir, exist_ok=True)


        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:


            f.write(self.SKILL_DOC)


    def tearDown(self):


        CONFIG["active_skill"] = self._saved_skill


        shutil.rmtree(self._tmp, ignore_errors=True)

    def _stub(self):
        # Sprint 19.2: wire a real SkillService onto the Stub so the
        # facade forwarder `self._skill_svc.inject(system_prompt,
        # self.workspace, active_skill)` resolves correctly.
        # A MagicMock would silently degrade `test_missing_skill_file_skips_gracefully`
        # and `test_xml_fragments_in_skill_doc_do_not_break_injection` to no-ops
        # (the file-I/O + XML-safety regression guards would vanish). Real
        # SkillService preserves the Sentinel-template regression guard while
        # routing reads to the tempdir-fixture SKILL.md files created in setUp.
        from services.skill_service import SkillService
        skill_svc = SkillService()
        skill_svc.set_workspace(self._tmp)
        return type("Stub", (), {
            "workspace": self._tmp,
            "logger": logging.getLogger("test_fatfo_skill"),
            "_skill_svc": skill_svc,
        })()


    def test_active_skill_appended_to_system_prompt(self):


        """Option A: [SKILL POLICY] block + skill doc appear AFTER base."""


        CONFIG["active_skill"] = self.SKILL_NAME


        stub = self._stub()


        result = KokertechController._inject_skill_policy(stub, self.BASE, None)


        self.assertIn(self.BASE, result)


        self.assertIn("[SKILL POLICY: l99]", result)


        self.assertIn("[END SKILL POLICY]", result)


        self.assertIn("L99 Skill Body", result)


        self.assertIn("Trigger-tag: ftw.", result)


        self.assertLess(result.index(self.BASE), result.index("[SKILL POLICY:"))


    def test_empty_active_skill_does_not_inject(self):


        """Empty active_skill -> system_prompt returned verbatim."""


        CONFIG["active_skill"] = ""


        stub = self._stub()


        result = KokertechController._inject_skill_policy(stub, self.BASE, "")


        self.assertEqual(result, self.BASE)


        self.assertNotIn("[SKILL POLICY", result)


    def test_missing_skill_file_skips_gracefully(self):


        """Active_skill with no SKILL.md on disk -> unchanged prompt."""


        CONFIG["active_skill"] = "this-skill-does-not-exist"


        stub = self._stub()


        result = KokertechController._inject_skill_policy(stub, self.BASE, None)


        self.assertEqual(result, self.BASE)


        self.assertNotIn("[SKILL POLICY", result)


    def test_xml_fragments_in_skill_doc_do_not_break_injection(self):


        """Row 16.4: angle brackets inside skill doc are inert in system_prompt."""


        CONFIG["active_skill"] = self.SKILL_NAME


        noisy = (


            "Skill body: <example>foo</example> and "


            "<final_output>fake</final_output> and "


            "<thinking>fake</thinking>."


        )


        with open(os.path.join(self._tmp, ".blackbox", "skills", self.SKILL_NAME, "SKILL.md"), "w", encoding="utf-8") as f:


            f.write(noisy)


        stub = self._stub()


        result = KokertechController._inject_skill_policy(stub, self.BASE, None)


        self.assertIn("[SKILL POLICY: l99]", result)


        self.assertIn("<example>foo</example>", result)


        self.assertIn("[END SKILL POLICY]", result)


if __name__ == "__main__":


    unittest.main()


# =============================================================================


# Tests — _truncate, _dynamic_cap, _message_cap


# =============================================================================


class TestTruncateAndDynamicCap(unittest.TestCase):


    """_truncate, _dynamic_cap, _message_cap — context-window sizing helpers."""


    @patch("memory_vault.ensure_tables_exist")


    @patch("memory_vault.get_current_session_id", return_value=1)


    @patch("kokertechController.get_provider")


    def setUp(self, mock_get_provider, mock_session, mock_tables):


        from kokertechController import KokertechController


        self.ctrl = KokertechController()


    # --- _truncate: tail-keeping contract ---


    def test_truncate_short_text_returned_unchanged(self):


        """Text shorter than cap is returned as-is (no marker)."""


        result = self.ctrl._truncate("hello", 1500)


        self.assertEqual(result, "hello")


    def test_truncate_exact_length_returned_unchanged(self):


        """Text exactly at the cap is returned as-is (boundary)."""


        text = "x" * 1500


        result = self.ctrl._truncate(text, 1500)


        self.assertEqual(result, text)


        self.assertNotIn("TRUNCATED", result)


    def test_truncate_long_text_keeps_tail(self):


        """For text longer than cap, the LAST cap chars are kept."""


        text = "HEAD" + ("x" * 2000) + "TAIL"


        result = self.ctrl._truncate(text, 1500)


        # The head should be replaced with the truncation marker.


        self.assertIn("TRUNCATED", result)


        # The tail of the original text must be preserved verbatim.


        self.assertTrue(result.endswith("TAIL"))


        # The result is roughly cap chars + the TRUNCATED marker.


        self.assertLess(len(result), 2000)


    def test_truncate_empty_string(self):


        """Empty input is returned as empty (short-circuit)."""


        self.assertEqual(self.ctrl._truncate(""), "")


        self.assertEqual(self.ctrl._truncate("", 1500), "")


    def test_truncate_none_safe(self):


        """None input is returned as None (no AttributeError)."""


        result = self.ctrl._truncate(None, 1500)


        self.assertIsNone(result)


    def test_truncate_default_cap_is_1500(self):


        """Default cap=1500 is the documented constant."""


        sig = inspect.signature(self.ctrl._truncate)


        self.assertEqual(sig.parameters["cap"].default, 1500)


    def test_truncate_with_smaller_cap(self):


        """Custom smaller cap is honored."""


        text = "A" * 100 + "Z" * 50


        result = self.ctrl._truncate(text, 30)


        self.assertIn("TRUNCATED", result)


        # Tail of the original must survive.


        self.assertTrue(result.endswith("Z" * 30))


        # The original head ("A" * 100) is gone.


        self.assertNotIn("A" * 50, result)


    # --- _dynamic_cap: model-aware scaling ---


    def test_dynamic_cap_unknown_model_uses_base(self):


        """Unknown model returns the base cap unchanged."""


        self.assertEqual(self.ctrl._dynamic_cap("Lexi-Llama-3-8B", 1500), 1500)


        self.assertEqual(self.ctrl._dynamic_cap("random-model-v1", 2000), 2000)


    def test_dynamic_cap_empty_model_uses_base(self):


        """Empty model name returns the base cap."""


        self.assertEqual(self.ctrl._dynamic_cap("", 1500), 1500)


    def test_dynamic_cap_qwen_scales_4x(self):


        """Models containing 'qwen' get 4x the base cap."""


        self.assertEqual(self.ctrl._dynamic_cap("qwen2.5-7b-instruct", 1500), 6000)


        self.assertEqual(self.ctrl._dynamic_cap("QWEN-7B", 1500), 6000)  # case-insensitive


    def test_dynamic_cap_llama31_scales_4x(self):


        """Models containing 'llama3.1' get 4x the base cap."""


        self.assertEqual(self.ctrl._dynamic_cap("llama3.1-8b", 1500), 6000)


        self.assertEqual(self.ctrl._dynamic_cap("Llama-3.1-70B", 1500), 6000)


    def test_dynamic_cap_gpt4o_scales_4x(self):


        """Models containing 'gpt-4o' get 4x the base cap."""


        self.assertEqual(self.ctrl._dynamic_cap("gpt-4o", 1500), 6000)


        self.assertEqual(self.ctrl._dynamic_cap("openai/gpt-4o-mini", 1500), 6000)


    def test_dynamic_cap_claude3_scales_4x(self):


        """Models containing 'claude-3' or 'claude-sonnet' get 4x the base cap."""


        self.assertEqual(self.ctrl._dynamic_cap("claude-3-opus", 1500), 6000)


        self.assertEqual(self.ctrl._dynamic_cap("claude-sonnet-4", 1500), 6000)


    def test_dynamic_cap_gpt4turbo_scales_4x(self):


        """Models containing 'gpt-4-turbo' get 4x the base cap."""


        self.assertEqual(self.ctrl._dynamic_cap("gpt-4-turbo-2024-04-09", 1500), 6000)


    def test_dynamic_cap_llama3_non31_uses_base(self):


        """llama3 (without .1) does NOT match the large-context tag."""


        # Only llama3.1 is in the tag list, not plain llama3.


        self.assertEqual(self.ctrl._dynamic_cap("llama3-8b", 1500), 1500)


    # --- _message_cap: integration of _dynamic_cap + _load_target_model ---


    def test_message_cap_default_base_1500(self):


        """_message_cap returns the cap for the active model, 4x for known large models."""


        with patch.object(self.ctrl, "_load_target_model", return_value="Lexi-Llama-3-8B"):


            self.assertEqual(self.ctrl._message_cap(1500), 1500)


    def test_message_cap_scales_for_qwen(self):


        """_message_cap picks up the 4x scaling for qwen models."""


        with patch.object(self.ctrl, "_load_target_model", return_value="qwen2.5-7b"):


            self.assertEqual(self.ctrl._message_cap(1500), 6000)


    def test_message_cap_falls_back_to_base_on_exception(self):


        """If _load_target_model raises, _message_cap returns the base unchanged."""


        with patch.object(self.ctrl, "_load_target_model", side_effect=RuntimeError("DB locked")):


            self.assertEqual(self.ctrl._message_cap(1500), 1500)


    def test_message_cap_custom_base_unknown(self):


        """Caller can override the base via the `base` argument (unknown model)."""


        with patch.object(self.ctrl, "_load_target_model", return_value="unknown-model"):


            self.assertEqual(self.ctrl._message_cap(base=2000), 2000)


    def test_message_cap_custom_base_qwen(self):


        """Caller can override the base via the `base` argument (qwen scaling applies)."""


        with patch.object(self.ctrl, "_load_target_model", return_value="qwen2.5"):


            self.assertEqual(self.ctrl._message_cap(base=2000), 8000)


# =============================================================================


# Tests — _load_user_profile, _load_target_model


# =============================================================================


class TestLoadCachedJsonHelpers(unittest.TestCase):


    """_load_user_profile / _load_target_model — cached JSON file readers.
    These wrap _read_cached_json to read user_identity.json and



    app_settings.json with the mtime-keyed TTL cache.


    """
    @patch("memory_vault.ensure_tables_exist")



    @patch("memory_vault.get_current_session_id", return_value=1)


    @patch("kokertechController.get_provider")


    def setUp(self, mock_get_provider, mock_session, mock_tables):


        from kokertechController import KokertechController


        self.ctrl = KokertechController()


    # --- _load_user_profile ---


    def test_user_profile_with_data(self):


        """_load_user_profile formats identity fields into a profile string."""


        profile_data = {"name": "Jacques", "tone": "Direct"}


        with patch.object(self.ctrl, "_read_cached_json", return_value=profile_data):


            result = self.ctrl._load_user_profile()


        self.assertIn("Jacques", result)


        self.assertIn("Direct", result)


        self.assertIn("Preferred Name", result)


        self.assertIn("Tone Preferences", result)


    def test_user_profile_missing_file_returns_default(self):


        """No identity file → 'No specific personal identity layer configured yet.'"""


        with patch.object(self.ctrl, "_read_cached_json", return_value=None):


            result = self.ctrl._load_user_profile()


        self.assertIn("No specific personal identity layer", result)


    def test_user_profile_empty_dict_returns_default(self):


        """Empty identity dict → treated as no data (same as missing file)."""


        with patch.object(self.ctrl, "_read_cached_json", return_value={}):


            result = self.ctrl._load_user_profile()


        # Empty dict is falsy in Python, so same as None/missing


        self.assertIn("No specific personal identity layer", result)


    def test_user_profile_uses_cached_json(self):


        """_load_user_profile delegates to _read_cached_json with the identity path."""


        with patch.object(self.ctrl, "_read_cached_json", return_value=None) as spy:


            self.ctrl._load_user_profile()


        # Path argument should be user_identity.json inside workspace


        call_args = spy.call_args[0][0]


        self.assertTrue(call_args.endswith("user_identity.json"))


    # --- _load_target_model ---


    def test_target_model_with_data(self):


        """_load_target_model returns the model_name from settings."""


        settings_data = {"model_name": "qwen2.5-7b-instruct"}


        with patch.object(self.ctrl, "_read_cached_json", return_value=settings_data):


            result = self.ctrl._load_target_model()


        self.assertEqual(result, "qwen2.5-7b-instruct")


    def test_target_model_missing_file_returns_default(self):


        """No settings file → default model name."""


        with patch.object(self.ctrl, "_read_cached_json", return_value=None):


            result = self.ctrl._load_target_model()


        self.assertEqual(result, "Lexi-Llama-3-8B-Uncensored_Q4_K_M")


    def test_target_model_missing_key_returns_default(self):


        """Settings file without model_name key → default model name."""


        with patch.object(self.ctrl, "_read_cached_json", return_value={"other_key": "x"}):


            result = self.ctrl._load_target_model()


        self.assertEqual(result, "Lexi-Llama-3-8B-Uncensored_Q4_K_M")


    def test_target_model_uses_cached_json(self):


        """_load_target_model delegates to _read_cached_json with the settings path."""


        with patch.object(self.ctrl, "_read_cached_json", return_value=None) as spy:


            self.ctrl._load_target_model()


        call_args = spy.call_args[0][0]


        self.assertTrue(call_args.endswith("app_settings.json"))


    def test_target_model_first_call_no_provider_invalidation(self):


        """First call (None sentinel) does NOT invalidate the provider cache."""


        with patch.object(self.ctrl, "_read_cached_json",


                          return_value={"model_name": "model-A"}) as mock_read:


            with patch("ai_base.invalidate_provider") as mock_invalidate:


                result = self.ctrl._load_target_model()


        self.assertEqual(result, "model-A")


        mock_invalidate.assert_not_called()


    def test_target_model_real_change_invalidates_provider(self):


        """When the model name CHANGES, invalidate_provider is called."""


        # Pre-warm with model-A


        self.ctrl._last_loaded_model = "model-A"


        with patch.object(self.ctrl, "_read_cached_json",


                          return_value={"model_name": "model-B"}):


            with patch("ai_base.invalidate_provider") as mock_invalidate:


                result = self.ctrl._load_target_model()


        self.assertEqual(result, "model-B")


        mock_invalidate.assert_called_once()


        self.assertEqual(self.ctrl._last_loaded_model, "model-B")


    def test_target_model_same_model_skips_invalidation(self):


        """When the model name is UNCHANGED, invalidate_provider is NOT called."""


        self.ctrl._last_loaded_model = "model-A"


        with patch.object(self.ctrl, "_read_cached_json",


                          return_value={"model_name": "model-A"}):


            with patch("ai_base.invalidate_provider") as mock_invalidate:


                result = self.ctrl._load_target_model()


        self.assertEqual(result, "model-A")


        mock_invalidate.assert_not_called()


    def test_target_model_round_trip_invalidates(self):


        """model-A → model-B → model-A: each real change triggers invalidation."""


        with patch.object(self.ctrl, "_read_cached_json",


                          return_value={"model_name": "model-A"}):


            with patch("ai_base.invalidate_provider") as mock_invalidate:


                self.ctrl._load_target_model()


        mock_invalidate.assert_not_called()  # first call (None sentinel)


        with patch.object(self.ctrl, "_read_cached_json",


                          return_value={"model_name": "model-B"}):


            with patch("ai_base.invalidate_provider") as mock_invalidate:


                self.ctrl._load_target_model()


        mock_invalidate.assert_called_once()  # A → B


        with patch.object(self.ctrl, "_read_cached_json",


                          return_value={"model_name": "model-A"}):


            with patch("ai_base.invalidate_provider") as mock_invalidate:


                self.ctrl._load_target_model()


        mock_invalidate.assert_called_once()  # B → A


# =============================================================================


# Tests — _fetch_context_components


# =============================================================================


class TestFetchContextComponents(unittest.TestCase):


    """_fetch_context_components — parallel memory lookups via ThreadPoolExecutor."""


    @patch("memory_vault.ensure_tables_exist")


    @patch("memory_vault.get_current_session_id", return_value=1)


    @patch("kokertechController.get_provider")


    def setUp(self, mock_get_provider, mock_session, mock_tables):


        from kokertechController import KokertechController


        self.ctrl = KokertechController()


    def test_returns_all_five_components(self):


        """All 5 expected keys are present in the returned dict."""


        with patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[(1, "fact", "mem content", 0.9)]),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(return_value="session data"),


            get_episodic_context=MagicMock(return_value="weighted episodic"),


        ):


            with patch.object(self.ctrl, "_format_biases", return_value="bias text"):


                with patch.object(self.ctrl, "_format_growth_arc", return_value="growth text"):


                    result = self.ctrl._fetch_context_components("query", "agent1")


        self.assertEqual(set(result.keys()),


                         {"context", "scbe_text", "growth_text",


                          "episodic_context", "weighted_episodic"})


        self.assertEqual(result["context"], "mem content")


        self.assertEqual(result["scbe_text"], "bias text")


        self.assertEqual(result["growth_text"], "growth text")


        self.assertEqual(result["episodic_context"], "session data")


        self.assertEqual(result["weighted_episodic"], "weighted episodic")


    def test_empty_semantic_search_returns_fallback(self):


        """Empty semantic_search list → 'No prior history found.' context."""


        with patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(return_value=""),


        ):


            with patch.object(self.ctrl, "_format_biases", return_value=""):


                with patch.object(self.ctrl, "_format_growth_arc", return_value=""):


                    result = self.ctrl._fetch_context_components("query", "agent1")


        self.assertEqual(result["context"], "No prior history found.")


    def test_episodic_limit_passed_through(self):


        """episodic_limit kwarg is forwarded to get_session/get_episodic_context."""


        mock_session = MagicMock(return_value="")


        mock_weighted = MagicMock(return_value="")


        with patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=mock_session,


            get_episodic_context=mock_weighted,


        ):


            with patch.object(self.ctrl, "_format_biases", return_value=""):


                with patch.object(self.ctrl, "_format_growth_arc", return_value=""):


                    self.ctrl._fetch_context_components("query", "agent1", episodic_limit=2)


        mock_session.assert_called_once_with(limit=2)


        mock_weighted.assert_called_once_with(limit=2, session_id=None, query="query")


    def test_default_episodic_limit_is_3(self):


        """Default episodic_limit=3 is the documented value."""


        mock_session = MagicMock(return_value="")


        with patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=mock_session,


            get_episodic_context=MagicMock(return_value=""),


        ):


            with patch.object(self.ctrl, "_format_biases", return_value=""):


                with patch.object(self.ctrl, "_format_growth_arc", return_value=""):


                    self.ctrl._fetch_context_components("query", "agent1")


        mock_session.assert_called_once_with(limit=3)


    def test_semantic_search_exception_returns_fallback(self):


        """If semantic_search raises, context falls back to default message."""


        with patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(side_effect=RuntimeError("DB locked")),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(return_value=""),


        ):


            with patch.object(self.ctrl, "_format_biases", return_value=""):


                with patch.object(self.ctrl, "_format_growth_arc", return_value=""):


                    result = self.ctrl._fetch_context_components("query", "agent1")


        self.assertEqual(result["context"], "No prior history found.")


    def test_session_context_exception_returns_fallback(self):


        """If get_session_context raises, episodic_context falls back to default."""


        with patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(side_effect=RuntimeError("Session lost")),


            get_episodic_context=MagicMock(return_value=""),


        ):


            with patch.object(self.ctrl, "_format_biases", return_value=""):


                with patch.object(self.ctrl, "_format_growth_arc", return_value=""):


                    result = self.ctrl._fetch_context_components("query", "agent1")


        self.assertEqual(result["episodic_context"], "No episodic journal available.")


    def test_weighted_episodic_exception_returns_fallback(self):


        """If get_episodic_context raises, weighted_episodic falls back to default."""


        with patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(side_effect=RuntimeError("Weighted fail")),


        ):


            with patch.object(self.ctrl, "_format_biases", return_value=""):


                with patch.object(self.ctrl, "_format_growth_arc", return_value=""):


                    result = self.ctrl._fetch_context_components("query", "agent1")


        self.assertEqual(result["weighted_episodic"], "No episodic journal available.")


    def test_semantic_search_first_result_content_extracted(self):


        """The content of the FIRST semantic search result becomes the context."""


        with patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[


                (1, "fact", "first memory content", 0.95),


                (2, "fact", "second memory content", 0.80),


            ]),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(return_value=""),


        ):


            with patch.object(self.ctrl, "_format_biases", return_value=""):


                with patch.object(self.ctrl, "_format_growth_arc", return_value=""):


                    result = self.ctrl._fetch_context_components("query", "agent1")


        # The first tuple's index 2 is the content.


        self.assertEqual(result["context"], "first memory content")


    def test_uses_reusable_thread_pool(self):


        """The reusable _context_pool is used (not a per-call ThreadPoolExecutor).
        Spy on the pool's submit() to prove the SAME pool instance is used



        across multiple _fetch_context_components calls.


        """
        pool = self.ctrl._context_pool



        with patch.object(pool, "submit", wraps=pool.submit) as spy_submit:


            for _ in range(3):


                with patch.multiple(


                    "memory_vault",


                    semantic_search=MagicMock(return_value=[]),


                    get_recent_bias=MagicMock(return_value=[]),


                    get_growth_arc=MagicMock(return_value=[]),


                    get_session_context=MagicMock(return_value=""),


                    get_episodic_context=MagicMock(return_value=""),


                ):


                    with patch.object(self.ctrl, "_format_biases", return_value=""):


                        with patch.object(self.ctrl, "_format_growth_arc", return_value=""):


                            self.ctrl._fetch_context_components("q", "a")


        # 5 submits per call, 3 calls = 15 total. All routed through the same pool.


        self.assertEqual(spy_submit.call_count, 15)


        # The pool object identity is preserved (not recreated per call).


        self.assertIs(self.ctrl._context_pool, pool)


    def test_query_forwarded_to_weighted_episodic(self):


        """The user query is forwarded to get_episodic_context for semantic scoring."""


        mock_weighted = MagicMock(return_value="")


        with patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=mock_weighted,


        ):


            with patch.object(self.ctrl, "_format_biases", return_value=""):


                with patch.object(self.ctrl, "_format_growth_arc", return_value=""):


                    self.ctrl._fetch_context_components("specific user query", "a")


        mock_weighted.assert_called_once_with(limit=3, session_id=None, query="specific user query")


    def test_format_biases_handles_get_recent_bias_exception(self):


        """_format_biases swallows get_recent_bias exceptions and returns 'No bias drift recorded yet.'
        The _fetch_context_components _biases closure calls



        self._format_biases(agent_type), which has its own try/except around


        memory_vault.get_recent_bias. When the underlying SQLite call raises,


        _format_biases must return the empty-bias string so the parallel


        fetch doesn't crash the whole turn.


        """
        with patch.multiple(



            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            get_recent_bias=MagicMock(side_effect=RuntimeError("vault hiccup")),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(return_value=""),


        ):


            with patch.object(self.ctrl, "_format_growth_arc", return_value=""):


                result = self.ctrl._fetch_context_components("q", "a")


        # The exception was caught inside _format_biases → scbe_text is the


        # documented empty-bias string (NOT a crash, NOT an exception leak).


        self.assertEqual(result["scbe_text"], "No bias drift recorded yet.")


    def test_format_growth_arc_handles_get_growth_arc_exception(self):


        """_format_growth_arc swallows get_growth_arc exceptions and returns 'No growth events recorded yet.'"""


        with patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(side_effect=RuntimeError("arc DB down")),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(return_value=""),


        ):


            with patch.object(self.ctrl, "_format_biases", return_value=""):


                result = self.ctrl._fetch_context_components("q", "a")


        self.assertEqual(result["growth_text"], "No growth events recorded yet.")


# =============================================================================


# Tests — __init__


# =============================================================================


class TestControllerInit(unittest.TestCase):


    """KokertechController.__init__ — provider creation, rag_engine, memory_vault."""


    @patch("memory_vault.ensure_tables_exist")


    @patch("memory_vault.get_current_session_id", return_value=42)


    def test_provider_created_from_config(self, mock_session, mock_tables):


        """Provider is created based on CONFIG active_provider."""


        from kokertechController import KokertechController


        with patch.dict("config.CONFIG", {"active_provider": "local_llm"}, clear=False):


            with patch("kokertechController.get_provider") as mock_get_provider:


                ctrl = KokertechController()


                mock_get_provider.assert_called_once()


                args, kwargs = mock_get_provider.call_args


                self.assertEqual(kwargs.get("name"), "local_llm")


    @patch("memory_vault.ensure_tables_exist")


    @patch("memory_vault.get_current_session_id", return_value=42)


    def test_rag_engine_created(self, mock_session, mock_tables):


        """RAG engine is created with configured params."""


        from kokertechController import KokertechController


        with patch.dict("config.CONFIG", {"rag_max_hops": 5, "rag_top_k": 10}, clear=False):


            from config import CONFIG as cfg


            with patch("kokertechController.AgenticRAGEngine") as mock_rag:


                ctrl = KokertechController()


                mock_rag.assert_called_once_with(


                    max_hops=5, top_k_per_source=10,


                    enable_web_search=cfg.get("rag_enable_web_search", False),


                )


    @patch("kokertechController.AgenticRAGEngine")


    def test_memory_vault_exception_handled(self, mock_rag):


        """Memory vault init failure is non-fatal."""


        from kokertechController import KokertechController


        with patch("memory_vault.ensure_tables_exist", side_effect=sqlite3.OperationalError("DB locked")):


            with patch("kokertechController.get_logger") as mock_get_logger:


                mock_log = MagicMock()


                mock_get_logger.return_value = mock_log


                ctrl = KokertechController()


                mock_log.warning.assert_any_call(


                    "Memory vault init failed (non-fatal): DB locked"


                )


    @patch("memory_vault.ensure_tables_exist")


    @patch("memory_vault.get_current_session_id", return_value=99)


    def test_session_initialized(self, mock_session, mock_tables):


        """Session is initialized via get_current_session_id."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        self.assertGreaterEqual(mock_session.call_count, 1)  # called in __init__


        mock_tables.assert_called()


    @patch("memory_vault.ensure_tables_exist")


    @patch("memory_vault.get_current_session_id")


    def test_default_provider_when_not_in_config(self, mock_session, mock_tables):


        """Defaults to local_llm when active_provider not set."""


        from kokertechController import KokertechController


        with patch.dict("config.CONFIG", {}, clear=True):


            with patch("kokertechController.get_provider") as mock_get_provider:


                ctrl = KokertechController()


                mock_get_provider.assert_called_once()


                _, kwargs = mock_get_provider.call_args


                self.assertEqual(kwargs.get("name"), "local_llm")


# =============================================================================


# Tests — _get_recent_biases


# =============================================================================


class TestControllerGetRecentBiases(unittest.TestCase):


    """_get_recent_biases() — reads bias_ledger from vault DB."""


    @patch("memory_vault.ensure_tables_exist")


    @patch("memory_vault.get_current_session_id", return_value=1)


    @patch("kokertechController.get_provider")


    def setUp(self, mock_get_provider, mock_session, mock_tables):


        from kokertechController import KokertechController


        self.ctrl = KokertechController()


        self.ctrl._context_svc._bias_cache = {"data": "", "ts": 0.0}


    @patch("os.path.exists", return_value=True)


    def test_returns_empty_string_when_db_empty(self, mock_exists):


        from unittest.mock import MagicMock as _MM


        mock_cursor = _MM()


        mock_cursor.fetchall.return_value = []


        mock_conn = _MM()


        mock_conn.cursor.return_value = mock_cursor


        mock_conn.__enter__.return_value = mock_conn


        from unittest.mock import patch as _p


        with _p("sqlite3.connect", return_value=mock_conn):


            with _p.object(self.ctrl, "workspace", r"C:\tmp"):


                with _p("os.path.join", return_value=r"C:\tmpult.db"):


                    result = self.ctrl._get_recent_biases()



                    self.assertEqual(result, "")


    @patch("os.path.exists", return_value=False)


    def test_returns_empty_on_no_db(self, mock_exists):


        result = self.ctrl._get_recent_biases()


        self.assertEqual(result, "")

    def test_returns_empty_on_db_error(self):


        with patch.object(self.ctrl, "workspace", r"C:\nonexistent"):



            result = self.ctrl._get_recent_biases()


            self.assertEqual(result, "")

    def test_returns_empty_on_sqlite_exception(self):


        """_get_recent_biases except Exception handler (line 81)."""


        with patch.object(self.ctrl, "workspace", r"C:\tmp"):


            with patch("os.path.exists", return_value=True):


                with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("DB locked")):
                    result = self.ctrl._get_recent_biases()

                    self.assertEqual(result, "")


# =============================================================================


# Tests — _load_summary, get_vram_usage, clear_api_cache, wipe_memory


# =============================================================================


class TestControllerUtilityMethods(unittest.TestCase):


    """_load_summary, get_vram_usage, clear_api_cache, wipe_memory."""


    @patch("memory_vault.ensure_tables_exist")


    @patch("memory_vault.get_current_session_id", return_value=1)


    @patch("kokertechController.get_provider")


    def setUp(self, mock_get_provider, mock_session, mock_tables):


        from kokertechController import KokertechController


        self.ctrl = KokertechController()


        self.ctrl.history = []


    # -- _load_summary --


    def test_load_summary_returns_empty_when_file_missing(self):


        with patch("os.path.exists", return_value=False):
            result = self.ctrl._load_summary()



        self.assertEqual(result, "")


    def test_load_summary_reads_file_content(self):


        with patch("os.path.exists", return_value=True):


            with patch("builtins.open", unittest.mock.mock_open(read_data="Summary content")):


                result = self.ctrl._load_summary()


        self.assertEqual(result, "Summary content")


    def test_load_summary_returns_empty_on_oserror(self):


        with patch("os.path.exists", return_value=True):


            with patch("builtins.open", side_effect=OSError("Permission denied")):
                result = self.ctrl._load_summary()



        self.assertEqual(result, "")


    # -- get_vram_usage --


    @patch("subprocess.check_output", return_value=b"1234\n")



    def test_get_vram_usage_returns_int(self, mock_subprocess):


        result = self.ctrl.get_vram_usage()


        self.assertEqual(result, 1234)


    @patch("subprocess.check_output", side_effect=__import__("subprocess").CalledProcessError(1, "nvidia-smi"))


    def test_get_vram_usage_returns_zero_on_error(self, mock_subprocess):


        with patch.object(self.ctrl.logger, "warning") as mock_warn:


            result = self.ctrl.get_vram_usage()


        self.assertEqual(result, 0)


    @patch("subprocess.check_output", side_effect=ValueError("invalid int"))


    def test_get_vram_usage_returns_zero_on_value_error(self, mock_subprocess):


        result = self.ctrl.get_vram_usage()


        self.assertEqual(result, 0)


    # -- clear_api_cache --


    def test_clear_api_cache_unloads_model(self):


        """clear_api_cache unloads the model from memory."""


        mock_provider = MagicMock()


        mock_provider.unload_model = MagicMock()


        self.ctrl._provider_svc.provider = mock_provider


        self.ctrl.clear_api_cache()


        mock_provider.unload_model.assert_called_once()


    def test_clear_api_cache_handles_no_provider(self):


        """Should not raise when provider has no unload_model."""


        mock_provider = MagicMock(spec=[])  # no attributes


        self.ctrl._provider_svc.provider = mock_provider


        self.ctrl.clear_api_cache()  # should not raise


    def test_clear_api_cache_handles_exception(self):


        """Should not raise on provider exception."""


        mock_provider = MagicMock()


        mock_provider.unload_model.side_effect = RuntimeError("fail")


        self.ctrl._provider_svc.provider = mock_provider


        self.ctrl.clear_api_cache()  # should not raise


    # -- wipe_memory --


    def test_wipe_memory_clears_history(self):


        self.ctrl.history = [{"role": "user", "content": "test"}]


        self.ctrl.compressed_context_summary = "some summary"


        self.ctrl.summary_file = r"C:\nonexistent\summary.txt"



        result = self.ctrl.wipe_memory()


        self.assertEqual(len(self.ctrl.history), 0)


        self.assertEqual(self.ctrl.compressed_context_summary, "")


        self.assertIn("wiped", result.lower())


        self.assertIn("wiped", result.lower())


    def test_wipe_memory_removes_summary_file(self):


        import tempfile


        tmp = tempfile.mktemp(suffix=".txt")


        try:


            with open(tmp, "w") as f:


                f.write("data")


            self.ctrl.summary_file = tmp


            with patch.object(self.ctrl, "clear_api_cache"):


                self.ctrl.wipe_memory()


            self.assertFalse(os.path.exists(tmp))


        finally:


            if os.path.exists(tmp):


                os.remove(tmp)


# =============================================================================


# Tests — _summarize_and_evict


# =============================================================================


class TestControllerSummarizeAndEvict(unittest.TestCase):


    """_summarize_and_evict — memory eviction, truncation, summary generation."""


    @patch("memory_vault.ensure_tables_exist")


    @patch("memory_vault.get_current_session_id", return_value=1)


    @patch("kokertechController.get_provider")


    def setUp(self, mock_get_provider, mock_session, mock_tables):


        from kokertechController import KokertechController


        self.ctrl = KokertechController()


        self.ctrl.history = []


        self.ctrl.memory_limit = 2


        self.log_calls = []


    def _log(self, msg):


        self.log_calls.append(msg)


    def test_no_eviction_when_history_small(self):


        """Does not evict when history is below threshold."""


        self.ctrl.history = [


            {"role": "user", "content": "hi"},


            {"role": "assistant", "content": "hello"},


            {"role": "user", "content": "how are you"},


        ]


        # memory_limit=2, so threshold = 2*2 = 4. History len=3 < 4, so no eviction


        self.ctrl._summarize_and_evict(self._log)


        self.assertEqual(len(self.ctrl.history), 3)  # unchanged


    @patch("memory_vault.store_episodic")


    def test_evicts_when_history_exceeds_limit(self, mock_episodic):


        """Old messages are evicted when history exceeds memory_limit*2."""


        self.ctrl.history = [


            {"role": "user", "content": "msg1"},


            {"role": "assistant", "content": "resp1"},


            {"role": "user", "content": "msg2"},


            {"role": "assistant", "content": "resp2"},


            {"role": "user", "content": "msg3"},


        ]


        from unittest.mock import patch as _p


        with _p.object(self.ctrl, "provider") as mock_provider:


            mock_provider.chat_completion.return_value = {


                "content": "Summary of conversation",


                "model": "test",


                "usage": {},


                "error": None,


            }


            self.ctrl._summarize_and_evict(self._log)


        # Should have kept last 2 messages


        self.assertEqual(len(self.ctrl.history), 2)


        self.assertEqual(self.ctrl.history[0]["content"], "resp2")


        self.assertEqual(self.ctrl.history[1]["content"], "msg3")


        # Verify episodic was stored


        mock_episodic.assert_called_once()


        self.assertIn("episodic", " ".join(self.log_calls).lower())


    @patch("memory_vault.store_episodic")


    def test_handles_provider_error_gracefully(self, mock_episodic):


        """When provider returns error, log it and don't crash."""


        self.ctrl.history = [


            {"role": "user", "content": "a" * 70},


            {"role": "assistant", "content": "b" * 70},


            {"role": "user", "content": "c" * 70},


            {"role": "assistant", "content": "d" * 70},


            {"role": "user", "content": "e" * 70},


        ]


        with patch.object(self.ctrl.provider, "chat_completion",


                          return_value={"error": "API timeout"}):


            self.ctrl._summarize_and_evict(self._log)


        self.assertEqual(len(self.ctrl.history), 2)


        self.assertTrue(any("failed" in msg.lower() for msg in self.log_calls))


    @patch("memory_vault.store_episodic")


    def test_handles_exception_during_summary(self, mock_episodic):


        """Exception during provider call is handled without crashing."""


        self.ctrl.history = [


            {"role": "user", "content": "a" * 70},


            {"role": "assistant", "content": "b" * 70},


            {"role": "user", "content": "c" * 70},


            {"role": "assistant", "content": "d" * 70},


            {"role": "user", "content": "e" * 70},


        ]


        with patch.object(self.ctrl.provider, "chat_completion",


                          side_effect=RuntimeError("Connection lost")):


            self.ctrl._summarize_and_evict(self._log)


        self.assertEqual(len(self.ctrl.history), 2)


        self.assertTrue(any("failed" in msg.lower() for msg in self.log_calls))


    def test_truncates_large_eviction_text(self):


        """Eviction text >2500 chars gets truncated (line 133)."""


        self.ctrl.history = [


            {"role": "user", "content": "x" * 600},


            {"role": "assistant", "content": "y" * 600},


            {"role": "user", "content": "z" * 600},


            {"role": "assistant", "content": "w" * 600},


            {"role": "user", "content": "v" * 600},


        ]


        with patch.object(self.ctrl.provider, "chat_completion") as mock_provider:


            mock_provider.chat_completion.return_value = {


                "content": "Summary", "model": "t", "usage": {}, "error": None,


            }


            with patch("memory_vault.store_episodic"):


                self.ctrl._summarize_and_evict(self._log)


        self.assertEqual(len(self.ctrl.history), 2)


    def test_handles_settings_load_error_during_summary(self):


        """Model config file error during summary is non-fatal (lines 143-144)."""


        self.ctrl.history = [


            {"role": "user", "content": "a" * 70},


            {"role": "assistant", "content": "b" * 70},


            {"role": "user", "content": "c" * 70},


            {"role": "assistant", "content": "d" * 70},


            {"role": "user", "content": "e" * 70},


        ]


        with patch.object(self.ctrl.provider, "chat_completion") as mock_provider:


            mock_provider.chat_completion.return_value = {


                "content": "Summary", "model": "t", "usage": {}, "error": None,


            }


            with patch("os.path.exists", return_value=True):


                with patch("builtins.open", side_effect=OSError("Permission denied")):


                    with patch("memory_vault.store_episodic"):


                        self.ctrl._summarize_and_evict(self._log)


        self.assertEqual(len(self.ctrl.history), 2)


    def test_handles_episodic_storage_failure(self):


        """Episodic journal store failure is non-fatal (lines 177-178)."""


        self.ctrl.history = [


            {"role": "user", "content": "a" * 70},


            {"role": "assistant", "content": "b" * 70},


            {"role": "user", "content": "c" * 70},


            {"role": "assistant", "content": "d" * 70},


            {"role": "user", "content": "e" * 70},


        ]


        with patch.object(self.ctrl.provider, "chat_completion") as mock_provider:


            mock_provider.chat_completion.return_value = {


                "content": "Summary", "model": "t", "usage": {}, "error": None,


            }


            with patch("memory_vault.store_episodic", side_effect=RuntimeError("Journal full")):


                self.ctrl._summarize_and_evict(self._log)


        self.assertEqual(len(self.ctrl.history), 2)


        self.assertTrue(any("failed" in msg.lower() for msg in self.log_calls))


# =============================================================================


# Tests — process_input: RAG detection, user identity, error paths


# =============================================================================


class TestControllerProcessInputRag(unittest.TestCase):


    """process_input — RAG query detection and pipeline integration."""


    def setUp(self):


        self.mocks = patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            store_memory=MagicMock(),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(return_value=""),


            get_current_session_id=MagicMock(return_value=1),


            ensure_tables_exist=MagicMock(),


        )


        self.mocks.start()


    def tearDown(self):


        self.mocks.stop()


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_rag_explicit_syntax_extracts_query(self):


        """<<RAG:query>> syntax triggers RAG pipeline."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.rag_engine = MagicMock()


        ctrl.rag_engine.answer.return_value = {


            "answer": "Research result",


            "hops": 2,


            "contexts": [],


        }


        result = ctrl.process_input("<<RAG:Tell me about AI>>", log_callback=lambda x: None)


        ctrl.rag_engine.answer.assert_called_once()


        call_kwargs = ctrl.rag_engine.answer.call_args[1]


        self.assertEqual(call_kwargs["query"], "Tell me about AI")


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_rag_auto_trigger_research_keyword(self):


        """Long query with 'research' keyword triggers automatic RAG."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.rag_engine = MagicMock()


        ctrl.rag_engine.answer.return_value = {


            "answer": "Research result", "hops": 1, "contexts": [],


        }


        query = "research " * 30  # 30 words, well above 25 min


        result = ctrl.process_input(query, log_callback=lambda x: None)


        ctrl.rag_engine.answer.assert_called_once()


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_rag_high_specificity_bypasses_word_count(self):


        """'multi-hop' triggers RAG even with short query."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.rag_engine = MagicMock()


        ctrl.rag_engine.answer.return_value = {


            "answer": "Result", "hops": 1, "contexts": [],


        }


        ctrl.process_input("multi-hop analysis", log_callback=lambda x: None)


        ctrl.rag_engine.answer.assert_called_once()


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_rag_short_query_no_trigger_no_rag(self):


        """Short query without trigger keywords does NOT start RAG."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.rag_engine = MagicMock()


        ctrl.process_input("Hello world", log_callback=lambda x: None)


        ctrl.rag_engine.answer.assert_not_called()


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_rag_engine_error_logged(self):


        """RAG engine error is logged but doesn't crash process_input."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.rag_engine = MagicMock()


        ctrl.rag_engine.answer.return_value = {"error": "API error"}


        result = ctrl.process_input("<<RAG:test>>", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_rag_engine_crash_logged(self):


        """RAG engine crash is caught and logged."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.rag_engine = MagicMock()


        ctrl.rag_engine.answer.side_effect = RuntimeError("RAG crash")


        result = ctrl.process_input("<<RAG:test>>", log_callback=lambda x: None)


        self.assertIsNotNone(result)


class TestControllerProcessInputUserIdentity(unittest.TestCase):


    """process_input — user identity loading and bias/growth formatting."""


    def setUp(self):


        self.mocks = patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            store_memory=MagicMock(),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(return_value=""),


            get_current_session_id=MagicMock(return_value=1),


            ensure_tables_exist=MagicMock(),


        )


        self.mocks.start()


    def tearDown(self):


        self.mocks.stop()


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_short_query_context_truncated(self):


        """History messages are truncated when over 1500 chars."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.history = [{"role": "user", "content": "x" * 2000}]


        # Just verify it doesn't crash


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_vault_search_failure_handled(self):


        """Memory vault semantic search failure doesn't crash."""


        memory_vault.semantic_search.side_effect = Exception("Search failed")


        from kokertechController import KokertechController


        ctrl = KokertechController()


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_bias_formatting_with_data(self):


        """Bias data formats correctly when present."""


        memory_vault.get_recent_bias.return_value = [


            {"bias_type": "Confirmation", "description": "Avoid",


             "confidence_score": 85, "timestamp": "2026-01-01"}


        ]


        from kokertechController import KokertechController


        ctrl = KokertechController()


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_growth_arc_formatting_with_data(self):


        """Growth arc data formats correctly when present."""


        memory_vault.get_growth_arc.return_value = [


            {"event_description": "Learned X", "energy_shift": 15,


             "timestamp": "2026-01-01"}


        ]


        from kokertechController import KokertechController


        ctrl = KokertechController()


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_context_summary_truncated(self):


        """Compressed context summary gets truncated when >2000 chars."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.compressed_context_summary = "x" * 2500


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


        self.assertLessEqual(len(ctrl.compressed_context_summary), 2003)  # "..." + 2000


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_memory_store_failure_handled(self):


        memory_vault.store_memory.side_effect = Exception("Store failed")


        from kokertechController import KokertechController


        ctrl = KokertechController()


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_vault_context_truncated_when_long(self):


        """Vault context >1500 chars gets truncated (line 196)."""


        memory_vault.semantic_search.return_value = [


            (None, None, "X" * 2000, None)


        ]


        from kokertechController import KokertechController


        ctrl = KokertechController()


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_user_identity_load_failure_handled(self):


        """Corrupt user_identity.json caught (lines 282-283)."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        with patch.object(ctrl, "workspace", r"C:\tmp"):


            with patch("os.path.exists", return_value=True):


                with patch("builtins.open", side_effect=OSError("Permission denied")):


                    result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_bias_exception_handled(self):


        """get_recent_bias exception caught (lines 287-288)."""


        memory_vault.get_recent_bias.side_effect = Exception("Bias error")


        from kokertechController import KokertechController


        ctrl = KokertechController()


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_growth_arc_exception_handled(self):


        """get_growth_arc exception caught (lines 291-292)."""


        memory_vault.get_growth_arc.side_effect = Exception("Arc error")


        from kokertechController import KokertechController


        ctrl = KokertechController()


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_episodic_context_exception_handled(self):


        """get_session_context exception caught (lines 404-405)."""


        memory_vault.get_session_context.side_effect = Exception("Session error")


        from kokertechController import KokertechController


        ctrl = KokertechController()


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": True}, clear=False)


    def test_weighted_episodic_exception_handled(self):


        """get_episodic_context exception caught (lines 412-413)."""


        memory_vault.get_episodic_context.side_effect = Exception("Weighted error")


        from kokertechController import KokertechController


        ctrl = KokertechController()


        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


# =============================================================================


# Tests — process_input: non-mock mode paths


# =============================================================================


class TestControllerProcessInputNonMock(unittest.TestCase):


    """process_input with mock_mode=False — AI response processing paths."""


    def setUp(self):


        self.mocks = patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            store_memory=MagicMock(),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(return_value=""),


            get_current_session_id=MagicMock(return_value=1),


            ensure_tables_exist=MagicMock(),


        )


        self.mocks.start()


    def tearDown(self):


        self.mocks.stop()


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": False}, clear=False)


    def test_model_loading_exception_handled(self):


        """Model config load error in process_input non-fatal (lines 462-463)."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.provider.chat_completion = MagicMock(return_value={


            "content": "AI reply", "model": "t", "usage": {}, "error": None,


        })


        with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):


            with patch.object(ctrl, "workspace", r"C:\tmp"):


                with patch("os.path.exists", return_value=True):


                    with patch("builtins.open", side_effect=OSError("Permission denied")):


                        result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": False}, clear=False)


    def test_processes_load_tools_from_ai_response(self):


        """AI response with <<LOAD_TOOLS>> sets pending_tool_loads (lines 484-487)."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.provider.chat_completion = MagicMock(return_value={


            "content": "Here is the result. <<LOAD_TOOLS:search,web>>",


            "model": "t", "usage": {}, "error": None,


        })


        with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):


            ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIn("search", ctrl._pending_tool_loads)


        self.assertIn("web", ctrl._pending_tool_loads)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": False}, clear=False)


    def test_memory_storage_failure_after_ai_response(self):


        """Memory store failure after AI response is non-fatal (lines 495-497)."""


        memory_vault.store_memory.side_effect = Exception("Store failed")


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.provider.chat_completion = MagicMock(return_value={


            "content": "AI reply", "model": "t", "usage": {}, "error": None,


        })


        with patch.object(ctrl, "parse_ai", return_value={"final": "ok"}):


            result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIsNotNone(result)


    @patch.dict("config.CONFIG", {"freeform_mode": True, "mock_mode": False}, clear=False)


    def test_engine_crash_during_chat_completion(self):


        """Provider exception caught in process_input (lines 502-506)."""


        from kokertechController import KokertechController


        ctrl = KokertechController()


        from unittest.mock import patch


        with patch("kokertechController.cached_chat_completion",
                   return_value={"error": "Connection lost",
                                "content": "",
                                "model": "test"}):
            result = ctrl.process_input("Hello", log_callback=lambda x: None)


        self.assertIn("error", result)


# =============================================================================


# Tests — process_input_multi: cached_chat_completion wiring


# =============================================================================


class TestProcessInputMultiCaching(unittest.TestCase):


    """process_input_multi — wires cached_chat_completion so multi-model
    dispatches share a cache entry when specs are identical (same



    provider+model+temp+max_tokens+messages).


    These tests verify:


      1. The multi-model _query path actually invokes cached_chat_completion


         (spied via patch, not bypassed).


      2. Two identical model_specs share a cache entry on the second call:


         cached_chat_completion runs both times, but the underlying


         provider.chat_completion only runs once.


      3. Different models produce different cache keys (both hit provider).


      4. With caching disabled, two identical specs each hit the provider


         (cache is truly opt-in).


    """
    def setUp(self):



        # Mock all memory_vault calls so the context-fetch path doesn't touch


        # disk. process_input_multi reads ~5 vault tables in parallel; without


        # mocks, the SQLite/Vault layer would attempt real I/O.


        self.mocks = patch.multiple(


            "memory_vault",


            semantic_search=MagicMock(return_value=[]),


            store_memory=MagicMock(),


            get_recent_bias=MagicMock(return_value=[]),


            get_growth_arc=MagicMock(return_value=[]),


            get_session_context=MagicMock(return_value=""),


            get_episodic_context=MagicMock(return_value=""),


            get_current_session_id=MagicMock(return_value=1),


            ensure_tables_exist=MagicMock(),


        )


        self.mocks.start()


        # Enable the response cache for these tests (default is off).


        from config import CONFIG


        self._orig_cache_enabled = CONFIG.get("response_cache_enabled", False)


        CONFIG["response_cache_enabled"] = True


        # Clear any leftover cache entries from prior tests.


        from ai_base import clear_response_cache, reset_providers


        clear_response_cache()


        reset_providers()


    def tearDown(self):


        self.mocks.stop()


        from config import CONFIG


        CONFIG["response_cache_enabled"] = self._orig_cache_enabled


        from ai_base import clear_response_cache


        clear_response_cache()


    def _get_local_llm_provider(self):


        """Return a real LocalLLMProvider whose chat_completion is a MagicMock.
        Using a real provider (not a bare MagicMock) means the cache key



        derived from messages+model+temp+max_tokens is computed normally and


        cache hits/misses follow the production code path.


        """
        from ai_base import LocalLLMProvider



        provider = LocalLLMProvider(


            default_model="test-model",


        )


        provider.chat_completion = MagicMock(return_value={


            "content": "shared", "error": None, "model": "test-model", "usage": {},


        })


        return provider


    def test_cached_chat_completion_is_actually_invoked(self):


        """The multi-model _query path calls cached_chat_completion (not
        provider.chat_completion directly).



        Spy on kokertechController.cached_chat_completion to confirm it


        receives the dispatch. Using wraps= keeps the real cache logic


        active so the underlying provider is also exercised.


        """
        from kokertechController import KokertechController



        from ai_base import cached_chat_completion


        ctrl = KokertechController()


        ctrl.history = []


        provider = self._get_local_llm_provider()


        with patch("kokertechController.get_provider", return_value=provider):


            with patch("kokertechController.cached_chat_completion",


                       wraps=cached_chat_completion) as spy:


                results = ctrl.process_input_multi(


                    "hello",


                    model_specs=[


                        {"provider": "local_llm", "model": "m-A", "label": "spec-1"},


                    ],


                )


        # The spy confirms cached_chat_completion was called (not bypassed).


        self.assertEqual(spy.call_count, 1)


        # And it was called with the right args.


        _, kwargs = spy.call_args


        self.assertEqual(kwargs["model"], "m-A")


        self.assertEqual(kwargs["temperature"], 0.1)


        self.assertEqual(kwargs["max_tokens"], -1)


        self.assertEqual(kwargs["timeout"], 600)


        self.assertIsNone(kwargs["request_id"])


        # The messages list is forwarded — first entry is the system prompt.


        self.assertGreater(len(kwargs["messages"]), 0)


        self.assertEqual(kwargs["messages"][0]["role"], "system")


        # Result is still returned normally.


        self.assertEqual(len(results), 1)


        self.assertTrue(results[0]["ok"])


        self.assertEqual(results[0]["content"], "shared")


    def test_two_identical_specs_share_cache_entry_on_second_call(self):


        """Two specs with identical (provider+model+temp+max_tokens+messages)
          share a cache entry on the second call:



          - cached_chat_completion runs TWICE (once per spec)


          - provider.chat_completion runs ONCE (cache hit on 2nd)


        Note: the two _query threads run concurrently. cached_chat_completion


        has a lock around dict mutations, but the check-then-store sequence


        is not atomic — both threads could theoretically see a cache miss


        before either stores. In practice the CPython GIL makes this race


        very unlikely; we assert call_count <= 1 to be robust against the


        theoretical TOCTOU window while still proving the cache-sharing


        contract holds.


        """
        from kokertechController import KokertechController



        from ai_base import cached_chat_completion


        ctrl = KokertechController()


        ctrl.history = []


        provider = self._get_local_llm_provider()


        spec = {"provider": "local_llm", "model": "test-model", "label": "dup"}


        with patch("kokertechController.get_provider", return_value=provider):


            with patch("kokertechController.cached_chat_completion",


                       wraps=cached_chat_completion) as cache_spy:


                results = ctrl.process_input_multi(


                    "cache-test", model_specs=[spec, spec],


                )


        # Two results returned (one per spec).


        self.assertEqual(len(results), 2)


        for r in results:


            self.assertTrue(r["ok"], f"Spec result should be ok: {r}")


            self.assertEqual(r["content"], "shared")


        # The cache wrapper was invoked twice (once per spec dispatch).


        self.assertEqual(cache_spy.call_count, 2,


                         "cached_chat_completion should run for EACH spec")


        # At most one underlying provider call — the second should hit the


        # cache (in the common case it's exactly 1; <= 1 tolerates the


        # theoretical TOCTOU race documented above).


        self.assertLessEqual(


            provider.chat_completion.call_count, 1,


            "Two identical specs should share a cache entry — "


            "provider.chat_completion called at most once",


        )


    def test_different_models_produce_separate_cache_entries(self):


        """Two specs with DIFFERENT model names → different cache keys →
        both call the underlying provider.



        """


        from kokertechController import KokertechController


        ctrl = KokertechController()


        ctrl.history = []


        provider = self._get_local_llm_provider()


        with patch("kokertechController.get_provider", return_value=provider):


            results = ctrl.process_input_multi(


                "test",


                model_specs=[


                    {"provider": "local_llm", "model": "model-A", "label": "A"},


                    {"provider": "local_llm", "model": "model-B", "label": "B"},


                ],


            )


        self.assertEqual(len(results), 2)


        # Different models → different cache keys → both hit the provider.


        self.assertEqual(


            provider.chat_completion.call_count, 2,


            "Different model names should produce different cache keys — "


            "both specs must call the provider",


        )


    def test_provider_error_marks_result_not_ok(self):


        """When provider.chat_completion returns an error payload, the
        result dict has ok=False, content="", and the error string



        propagated. Covers the error branch of _query (lines ~947-958).


        """
        from kokertechController import KokertechController



        ctrl = KokertechController()


        ctrl.history = []


        provider = self._get_local_llm_provider()


        provider.chat_completion.return_value = {


            "error": "API down for maintenance", "content": "",


            "model": "test-model", "usage": {},


        }


        spec = {"provider": "local_llm", "model": "test-model", "label": "failing"}


        with patch("kokertechController.get_provider", return_value=provider):


            results = ctrl.process_input_multi("error-test", model_specs=[spec])


        self.assertEqual(len(results), 1)


        r = results[0]


        self.assertFalse(r["ok"])


        self.assertEqual(r["error"], "API down for maintenance")


        self.assertEqual(r["content"], "")


        # Spec metadata is preserved on the error result.


        self.assertEqual(r["provider"], "local_llm")


        self.assertEqual(r["model"], "test-model")


        self.assertEqual(r["label"], "failing")


        # Timestamp is populated.


        self.assertRegex(r["ts"], r"\d{2}:\d{2}:\d{2}")


    def test_get_provider_exception_marks_result_not_ok(self):


        """When get_provider raises (provider factory failure), the
        ``except Exception`` branch in _query (lines ~959-970) catches it



        and produces a result with ok=False, content="", and the exception


        message preserved on ``error``.


        """
        from kokertechController import KokertechController



        ctrl = KokertechController()


        ctrl.history = []


        spec = {"provider": "local_llm", "model": "test-model", "label": "explode"}


        with patch("kokertechController.get_provider",


                   side_effect=RuntimeError("no such provider")):


            results = ctrl.process_input_multi("crash-test", model_specs=[spec])


        self.assertEqual(len(results), 1)


        r = results[0]


        self.assertFalse(r["ok"])


        # _query does error=str(e) exactly — assertEqual (not assertIn) catches


        # unintended string wrapping if the production code later adds a prefix.


        self.assertEqual(r["error"], "no such provider")


        self.assertEqual(r["content"], "")


        # Spec metadata is preserved on the exception-caught result.


        self.assertEqual(r["provider"], "local_llm")


        self.assertEqual(r["model"], "test-model")


        self.assertEqual(r["label"], "explode")


        self.assertRegex(r["ts"], r"\d{2}:\d{2}:\d{2}")


# =============================================================================


# Tests — _read_cached_json (mtime-keyed TTL cache)


# =============================================================================


class TestReadCachedJson(unittest.TestCase):


    """_read_cached_json — mtime-keyed TTL cache for JSON files.
    Cache is keyed by path, with each entry tracking {"data", "mtime", "ts"}.



    On every call, the file's mtime is checked against the cached mtime.


    If the file changed on disk (mtime differs), the cache entry is


    invalidated and re-read. If the TTL has elapsed, the cache entry is


    also invalidated and re-read. All mutations happen under


    ``_json_cache_lock`` for thread safety.


    """
    @patch("memory_vault.ensure_tables_exist")



    @patch("memory_vault.get_current_session_id", return_value=1)


    @patch("kokertechController.get_provider")


    def setUp(self, mock_get_provider, mock_session, mock_tables):


        from services import get_services


        get_services().reset()  # Clear singleton service state to prevent cross-test contamination


        from kokertechController import KokertechController


        self.ctrl = KokertechController()


        # Each test gets a fresh cache to avoid pollution.


        self.ctrl._json_cache = {}


        self._tmpfiles = []


    def tearDown(self):


        for path in self._tmpfiles:


            try:


                os.remove(path)


            except OSError:


                pass


    def _write_json(self, payload):


        """Write a JSON file to a temp path, return (path, mtime)."""


        import tempfile


        fd, path = tempfile.mkstemp(suffix=".json")


        try:


            with os.fdopen(fd, "w", encoding="utf-8") as f:


                json.dump(payload, f)


        except Exception:


            os.close(fd)


            raise


        self._tmpfiles.append(path)


        return path, os.path.getmtime(path)


    def _touch(self, path, new_payload):


        """Overwrite the file with new content (new mtime)."""


        with open(path, "w", encoding="utf-8") as f:


            json.dump(new_payload, f)


        # Ensure mtime advances on filesystems with second-resolution mtime


        time.sleep(0.05)


        return os.path.getmtime(path)


    # --- input validation ---


    def test_missing_file_returns_none(self):


        """Non-existent path returns None (no exception, no cache entry)."""


        result = self.ctrl._read_cached_json(r"C:\nonexistent\nope.json")









        self.assertIsNone(result)


        # No cache entry was created for the missing path.


        self.assertEqual(len(self.ctrl._json_cache), 0)


    def test_empty_path_returns_none(self):


        """Empty string path returns None (the ! path short-circuits)."""


        self.assertIsNone(self.ctrl._read_cached_json(""))


        self.assertIsNone(self.ctrl._json_cache.get("", None))


    def test_path_with_whitespace_is_still_checked(self):


        """Whitespace-only path is treated as a real path (os.path.exists returns False)."""


        result = self.ctrl._read_cached_json("   ")


        self.assertIsNone(result)


    # --- cache miss / hit / TTL ---


    def test_cache_miss_reads_file_and_populates_cache(self):


        """First call reads the file and populates _json_cache."""


        path, _ = self._write_json({"key": "value"})


        result = self.ctrl._read_cached_json(path)


        self.assertEqual(result, {"key": "value"})


        # Cache entry created.


        self.assertIn(path, self.ctrl._json_cache)


        entry = self.ctrl._json_cache[path]


        self.assertEqual(entry["data"], {"key": "value"})


        self.assertGreater(entry["ts"], 0)


        self.assertGreater(entry["mtime"], 0)


    def test_cache_hit_returns_cached_data_without_disk_read(self):


        """Second call within TTL returns cached data, no file I/O."""


        path, _ = self._write_json({"key": "v1"})


        # First call populates cache.


        r1 = self.ctrl._read_cached_json(path)


        self.assertEqual(r1, {"key": "v1"})


        # Spy on open() to prove the second call doesn't touch disk.


        with patch("builtins.open") as spy_open:


            r2 = self.ctrl._read_cached_json(path)


            spy_open.assert_not_called()


        self.assertEqual(r2, {"key": "v1"})


    def test_ttl_expiry_re_reads_file(self):


        """After TTL elapses, the next call re-reads the file."""


        path, _ = self._write_json({"v": 1})


        # Populate cache.


        self.ctrl._read_cached_json(path)


        # Force the cache entry's ts into the distant past.


        with self.ctrl._json_cache_lock:


            self.ctrl._json_cache[path]["ts"] = 0.0


        # Next call should re-read (TTL expired).


        # Touch the file to make mtime stable but different.


        self._touch(path, {"v": 2})


        result = self.ctrl._read_cached_json(path)


        self.assertEqual(result, {"v": 2})


    def test_custom_ttl_respected(self):


        """ttl= kwarg overrides the default 2s TTL."""


        path, _ = self._write_json({"v": 1})


        # Populate cache.


        self.ctrl._read_cached_json(path, ttl=10.0)


        # Within 10s → cache hit, even though default 2s expired.


        # Force the entry ts to be 5s ago.


        with self.ctrl._json_cache_lock:


            self.ctrl._json_cache[path]["ts"] = time.time() - 5.0


        # With custom ttl=10, this should still hit.


        with patch("builtins.open") as spy_open:


            result = self.ctrl._read_cached_json(path, ttl=10.0)


            spy_open.assert_not_called()


        self.assertEqual(result, {"v": 1})


        # With custom ttl=2 (smaller than 5s age), it should miss but


        # TypeError from json.load(MagicMock) is caught, returns None.


        with patch("builtins.open") as spy_open:


            result = self.ctrl._read_cached_json(path, ttl=2.0)


            spy_open.assert_called_once()


        self.assertIsNone(result)


    # --- mtime-based invalidation ---


    def test_mtime_change_invalidates_cache(self):


        """When the file's mtime changes (e.g. external edit), the cache is invalidated."""


        path, _ = self._write_json({"v": 1})


        self.ctrl._read_cached_json(path)


        # Simulate external edit: rewrite the file (mtime advances).


        new_mtime = self._touch(path, {"v": 2})


        # Now reading should return the new content (cache miss).


        result = self.ctrl._read_cached_json(path)


        self.assertEqual(result, {"v": 2})


        # Cache entry was updated with the new mtime.


        self.assertEqual(self.ctrl._json_cache[path]["mtime"], new_mtime)


    def test_same_mtime_within_ttl_uses_cache(self):


        """If mtime AND TTL are both preserved, cache hit (no re-read)."""


        path, original_mtime = self._write_json({"v": 1})


        # First read.


        self.ctrl._read_cached_json(path)


        # Second read immediately — same mtime, within TTL.


        with patch("builtins.open") as spy_open:


            result = self.ctrl._read_cached_json(path)


            spy_open.assert_not_called()


        self.assertEqual(result, {"v": 1})


        # Cached mtime matches the on-disk mtime.


        self.assertEqual(self.ctrl._json_cache[path]["mtime"], original_mtime)


    # --- error handling ---


    def test_invalid_json_returns_none(self):


        """Malformed JSON returns None (no exception leaks)."""


        import tempfile


        fd, path = tempfile.mkstemp(suffix=".json")


        try:


            with os.fdopen(fd, "w", encoding="utf-8") as f:


                f.write("{not valid json at all")


        except Exception:


            os.close(fd)


            raise


        self._tmpfiles.append(path)


        result = self.ctrl._read_cached_json(path)


        self.assertIsNone(result)


    def test_oserror_on_mtime_returns_none(self):


        """If os.path.getmtime raises OSError, return None."""


        path, _ = self._write_json({"v": 1})


        with patch("os.path.getmtime", side_effect=OSError("Permission denied")):


            result = self.ctrl._read_cached_json(path)


        self.assertIsNone(result)


    def test_oserror_on_open_returns_none(self):


        """If open() raises OSError, return None (no exception leak)."""


        path, _ = self._write_json({"v": 1})


        with patch("builtins.open", side_effect=OSError("File locked")):


            result = self.ctrl._read_cached_json(path)


        self.assertIsNone(result)


    # --- multi-path / thread safety ---


    def test_different_paths_have_separate_cache_entries(self):


        """The cache is keyed by path — two paths don't share entries."""


        path_a, _ = self._write_json({"who": "A"})


        path_b, _ = self._write_json({"who": "B"})


        r_a = self.ctrl._read_cached_json(path_a)


        r_b = self.ctrl._read_cached_json(path_b)


        self.assertEqual(r_a, {"who": "A"})


        self.assertEqual(r_b, {"who": "B"})


        self.assertEqual(len(self.ctrl._json_cache), 2)


    def test_concurrent_reads_only_read_file_once(self):


        """10 threads reading the same path: file is opened once (cache hit on 9).
        The mtime-keyed TTL cache, combined with _json_cache_lock, ensures



        the file I/O happens at most once.


        """
        path, _ = self._write_json({"shared": "data"})



        with patch("builtins.open", wraps=open) as spy_open:


            results = []


            threads = []


            barrier = threading.Barrier(10)


            def reader():


                barrier.wait()  # Coordinate threads to start at the same time


                results.append(self.ctrl._read_cached_json(path))


            for _ in range(10):


                t = threading.Thread(target=reader)


                t.start()


                threads.append(t)


            for t in threads:


                t.join()


        # All 10 threads got the same data.


        for r in results:


            self.assertEqual(r, {"shared": "data"})


        # The file was opened at most a few times (the lock prevents a


        # thundering herd, but a small race window is acceptable for this


        # defensive cache). The key assertion is "much less than 10".


        self.assertLess(spy_open.call_count, 10,


                        f"Expected <10 opens (cache should dedup), got {spy_open.call_count}")


        self.assertGreaterEqual(spy_open.call_count, 1,


                                "Expected at least 1 open (cold start)")


# =============================================================================


# Tests — KokertechController.shutdown (thread-pool lifecycle)


# =============================================================================


class TestShutdown(unittest.TestCase):


    """KokertechController.shutdown() — releases the reusable _context_pool.
    The thread pool is created in __init__ and must be shut down to release



    worker threads when the application exits. shutdown() is idempotent


    (safe to call multiple times) and tolerates a missing pool attribute


    (for defensive construction in unusual lifecycles).


    """
    @patch("memory_vault.ensure_tables_exist")



    @patch("memory_vault.get_current_session_id", return_value=1)


    @patch("kokertechController.get_provider")


    def setUp(self, mock_get_provider, mock_session, mock_tables):


        from kokertechController import KokertechController


        self.ctrl = KokertechController()


    def _make_pool_mock(self):


        """Return a MagicMock that mimics a ThreadPoolExecutor.shutdown()."""


        pool = MagicMock()


        pool.shutdown = MagicMock()


        return pool


    def _get_local_llm_provider(self):


        """Return a real LocalLLMProvider whose chat_completion is a MagicMock."""


        from ai_base import LocalLLMProvider


        provider = LocalLLMProvider(


            default_model="test-model",


        )


        provider.chat_completion = MagicMock(return_value={


            "content": "shared", "error": None, "model": "test-model", "usage": {},


        })


        return provider


    def test_shutdown_calls_pool_shutdown(self):


        """shutdown() invokes _context_pool.shutdown(wait=False, cancel_futures=True)."""


        pool = self._make_pool_mock()


        self.ctrl._context_pool = pool


        self.ctrl.shutdown()


        pool.shutdown.assert_called_once_with(wait=False, cancel_futures=True)


    def test_shutdown_is_idempotent(self):


        """Calling shutdown() twice does not raise (RuntimeError swallowed)."""


        pool = self._make_pool_mock()


        # First call succeeds; second call raises RuntimeError (already shut down).


        pool.shutdown.side_effect = [None, RuntimeError("cannot schedule new futures")]


        self.ctrl._context_pool = pool


        # Both calls must not raise.


        self.ctrl.shutdown()


        self.ctrl.shutdown()


        # shutdown() was attempted twice.


        self.assertEqual(pool.shutdown.call_count, 2)


    def test_shutdown_with_no_pool_attribute_is_noop(self):


        """If _context_pool attribute doesn't exist (e.g. unusual lifecycle), no-op."""


        # Simulate a controller where _context_pool was never set.


        if hasattr(self.ctrl, "_context_pool"):


            delattr(self.ctrl, "_context_pool")


        # Should not raise AttributeError (the getattr returns None).


        self.ctrl.shutdown()


    def test_shutdown_with_none_pool_is_noop(self):


        """If _context_pool is explicitly None, shutdown() is a no-op."""


        self.ctrl._context_pool = None


        # Should not raise — the if pool is not None check guards.


        self.ctrl.shutdown()


    def test_shutdown_releases_real_thread_pool(self):


        """The real ThreadPoolExecutor's _shutdown flag is set after shutdown()."""


        # Don't replace the pool — use the real one created in __init__.


        real_pool = self.ctrl._context_pool


        # Pre-shutdown: pool is alive.


        self.assertFalse(real_pool._shutdown)


        self.ctrl.shutdown()


        # Post-shutdown: pool is marked as shut down.


        self.assertTrue(real_pool._shutdown)


    def test_shutdown_cancels_pending_futures(self):


        """shutdown(cancel_futures=True) prevents queued tasks from running."""


        from concurrent.futures import ThreadPoolExecutor


        # Create a fresh pool with a slow task; submit a slow task; verify


        # shutdown cancels it (the task never starts).


        fresh_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test")


        self.ctrl._context_pool = fresh_pool


        task_started = threading.Event()


        task_finished = threading.Event()


        def slow_task():


            task_started.set()


            time.sleep(10)  # Long enough to be cancelled.


            task_finished.set()


        # Submit the slow task BEFORE shutdown.


        fresh_pool.submit(slow_task)


        # Shutdown with cancel_futures=True.


        self.ctrl.shutdown()


        # The task may or may not have started depending on thread scheduling,


        # but if it started, it was interrupted. Either way, no exception leaks.


        self.assertTrue(fresh_pool._shutdown)


    def test_shutdown_concurrent_calls_are_safe(self):


        """Multiple threads calling shutdown() simultaneously all return cleanly."""


        pool = self._make_pool_mock()


        # Make the mock raise RuntimeError on every call (simulating concurrent


        # shutdown attempts after the pool is already down).


        pool.shutdown.side_effect = RuntimeError("already shut down")


        self.ctrl._context_pool = pool


        errors = []


        def caller():


            try:


                self.ctrl.shutdown()


            except Exception as e:


                errors.append(e)


        threads = [threading.Thread(target=caller) for _ in range(5)]


        for t in threads:


            t.start()


        for t in threads:


            t.join()


        # All 5 callers returned without raising — the RuntimeError was


        # swallowed inside shutdown().


        self.assertEqual(errors, [])


        # All 5 shutdown attempts were recorded.


        self.assertEqual(pool.shutdown.call_count, 5)


    @patch.dict("config.CONFIG", {"response_cache_enabled": False}, clear=False)


    def test_cache_disabled_does_not_share_entries(self):


        """With CONFIG[response_cache_enabled]=False, the cache is fully
        bypassed: cached_chat_completion passes through to



        provider.chat_completion on every call.


        Two identical specs therefore each hit the provider (call_count=2).


        """
        from kokertechController import KokertechController



        from ai_base import cached_chat_completion


        ctrl = KokertechController()


        ctrl.history = []


        provider = self._get_local_llm_provider()


        spec = {"provider": "local_llm", "model": "test-model", "label": "dup"}


        with patch("kokertechController.get_provider", return_value=provider):


            with patch("kokertechController.cached_chat_completion",


                       wraps=cached_chat_completion) as cache_spy:


                results = ctrl.process_input_multi(


                    "no-cache", model_specs=[spec, spec],


                )


        # Both calls reached the underlying provider (cache off).


        self.assertEqual(provider.chat_completion.call_count, 2)


        # And the wrapper was still called (just doesn't share a cache entry).


        self.assertEqual(cache_spy.call_count, 2)


        # Both results returned.


        self.assertEqual(len(results), 2)


        for r in results:


            self.assertTrue(r["ok"])


# Shared helper - no @patch decorators (KokertechController.__new__ skips __init__)


# =============================================================================


def _make_kctrl_bare():


    ctrl = KokertechController.__new__(KokertechController)


    ctrl.history = []


    ctrl.logger = MagicMock()


    ctrl.output = MagicMock()


    return ctrl


# =============================================================================


# Tests - _model_input_budget (Fix 2 pre-flight) + _SAFE_INPUT_MARGIN


# =============================================================================


class TestModelInputBudget(unittest.TestCase):


    """Locks the 14-tag budget table + 0.7 safety margin against regression.
    Covers each documented tag, specific-tag priority, case-insensitive
    matching, unknown/empty-model default, and the margin-arithmetic constant.
    """
    def test_margin_constant_is_point_seven(self):
        self.assertEqual(KokertechController._SAFE_INPUT_MARGIN, 0.7)

    def test_qwen_zero_point_five_b_matches_specific_tag(self):
        ctrl = _make_controller()
        self.assertEqual(ctrl._model_input_budget("qwen2.5-0.5b-instruct"), int(1024 * 4 * 0.7))

    def test_ministral_matches_tag(self):
        ctrl = _make_controller()
        self.assertEqual(ctrl._model_input_budget("Ministral-3-3B-Instruct-2512-Q4_K_M.gguf"), int(32768 * 4 * 0.7))

    def test_nemotron_matches_tag(self):
        ctrl = _make_controller()
        self.assertEqual(ctrl._model_input_budget("nemotron-3-nano.gguf"), int(4096 * 4 * 0.7))

    def test_qwen3_matches_tag(self):
        ctrl = _make_controller()
        self.assertEqual(ctrl._model_input_budget("qwen3-7b-instruct.gguf"), int(32768 * 4 * 0.7))

    def test_qwen_seven_b_matches_qwen_tag(self):
        ctrl = _make_controller()
        self.assertEqual(ctrl._model_input_budget("qwen-7b-chat"), int(8192 * 4 * 0.7))

    def test_llama_three_point_one_eight_b_matches_llama_three_point_one_tag(self):
        ctrl = _make_controller()
        self.assertEqual(ctrl._model_input_budget("llama3.1-8b-instruct"), int(8192 * 4 * 0.7))

    def test_gpt_four_o_matches_tag(self):
        ctrl = _make_controller()
        self.assertEqual(ctrl._model_input_budget("gpt-4o"), int(8192 * 4 * 0.7))

    def test_claude_three_matches_tag(self):
        ctrl = _make_controller()
        self.assertEqual(ctrl._model_input_budget("claude-3-opus"), int(100000 * 4 * 0.7))

    def test_unknown_model_falls_back_to_4096_tokens(self):


        ctrl = _make_controller()


        self.assertEqual(ctrl._model_input_budget(""), int(4096 * 4 * 0.7))


    def test_empty_model_falls_back_to_4096_tokens(self):


        ctrl = _make_controller()


        self.assertEqual(ctrl._model_input_budget(""), int(4096 * 4 * 0.7))


    def test_specific_tag_beats_generic_qwen(self):


        ctrl = _make_controller()


        budget_small = ctrl._model_input_budget("qwen2.5-0.5b-instruct")


        budget_large = ctrl._model_input_budget("qwen-7b-chat")


        self.assertLess(budget_small, budget_large,


                        "qwen2.5-0.5b must NOT match generic qwen tag")


    def test_case_insensitive_qwen(self):


        ctrl = _make_controller()


        self.assertEqual(ctrl._model_input_budget("QWEN-7B"), ctrl._model_input_budget("qwen-7b"))


        self.assertEqual(ctrl._model_input_budget("Qwen-7B"), ctrl._model_input_budget("qwen-7b"))


    def test_case_insensitive_claude(self):


        ctrl = _make_controller()


        self.assertEqual(ctrl._model_input_budget("CLAUDE-3-OPUS"), ctrl._model_input_budget("claude-3-opus"))


    def test_case_insensitive_phi(self):


        ctrl = _make_controller()


        self.assertEqual(ctrl._model_input_budget("Phi-3-Mini"), ctrl._model_input_budget("PHI-3-MINI"))


    def test_int_truncation_correctness(self):


        ctrl = _make_controller()


        raw = 1024 * 4 * 0.7  # 2867.2...


        self.assertEqual(ctrl._model_input_budget("qwen2.5-0.5b-instruct"), int(raw))


    def test_table_integrity_has_fourteen_entries(self):
        self.assertEqual(len(KokertechController._MODEL_INPUT_TOKEN_BUDGETS), 14)

    def test_table_entries_are_str_int_tuples(self):
        for entry in KokertechController._MODEL_INPUT_TOKEN_BUDGETS:
            self.assertEqual(len(entry), 2, "Entry %r is not a 2-tuple" % (entry,))
            self.assertIsInstance(entry[0], str, "Tag in %r is not str" % (entry,))
            self.assertIsInstance(entry[1], int, "Tokens in %r is not int" % (entry,))

    def test_table_contains_user_reported_model_tag(self):
        tags = [t for (t, _n) in KokertechController._MODEL_INPUT_TOKEN_BUDGETS]
        self.assertIn("qwen2.5-0.5b", tags)
        self.assertIn("ministral", tags)


# =============================================================================


# Tests - _is_sentinel_response (Fix 3 + defense-in-depth anchor regex)


# =============================================================================


class TestIsSentinelResponse(unittest.TestCase):


    """Locks the exact-match list (canned greeting) and the regex anchor
    (start-of-string Example N digits) plus the false-positive guards.



    """


    def test_returns_false_for_empty_string(self):


        ctrl = _make_controller()


        self.assertFalse(ctrl._is_sentinel_response(""))

    def test_returns_false_for_none(self):


        ctrl = _make_controller()


        self.assertFalse(ctrl._is_sentinel_response(None))


    def test_greeting_sentinel_exact_match(self):


        ctrl = _make_controller()


        self.assertTrue(ctrl._is_sentinel_response("Yo! What's on the bench today, mate?"))


    def test_greeting_sentinel_with_whitespace_padding(self):


        ctrl = _make_controller()


        self.assertTrue(ctrl._is_sentinel_response("  \nYo! What's on the bench today, mate?  "))






    def test_example_one_structured_template_matches(self):


        ctrl = _make_controller()


        self.assertTrue(ctrl._is_sentinel_response("Example 1 (Using a Tool):"))


    def test_example_two_structured_template_matches(self):


        ctrl = _make_controller()


        self.assertTrue(ctrl._is_sentinel_response("Example 2 (Conversational Reply):"))


    def test_example_three_with_description_matches(self):


        ctrl = _make_controller()


        self.assertTrue(ctrl._is_sentinel_response("Example 3 (Another Tool):"))


    def test_lowercase_example_still_matches_case_insensitive(self):


        ctrl = _make_controller()


        self.assertTrue(ctrl._is_sentinel_response("example 1: foo"))


    def test_uppercase_example_still_matches_case_insensitive(self):


        ctrl = _make_controller()


        self.assertTrue(ctrl._is_sentinel_response("EXAMPLE 1: FOO"))


    def test_legitimate_short_query_not_sentinel(self):


        ctrl = _make_controller()


        self.assertFalse(ctrl._is_sentinel_response("An example query?"))


    def test_colon_but_no_digit_not_sentinel(self):


        ctrl = _make_controller()


        self.assertFalse(ctrl._is_sentinel_response("Example: foo"))


    def test_mid_sentence_for_example_not_sentinel(self):


        ctrl = _make_controller()


        self.assertFalse(ctrl._is_sentinel_response("For example, let me explain how this works."))


    def test_partial_greeting_not_sentinel(self):


        ctrl = _make_controller()


        self.assertFalse(ctrl._is_sentinel_response("Yo! How are you doing mate?"))


    def test_normal_llm_response_not_sentinel(self):


        ctrl = _make_controller()


        self.assertFalse(ctrl._is_sentinel_response(


            "I'll help you set up the workflow. First, let me check the project structure."


        ))


    def test_example_n_with_xml_prefix_not_sentinel_raw(self):


        ctrl = _make_controller()


        raw = "<example>Example 1 (Using a Tool): foo bar</example>"


        self.assertFalse(ctrl._is_sentinel_response(raw))


    def test_parsed_final_extracted_from_xml_wrapper_is_sentinel(self):


        ctrl = _make_controller()


        inner = "Example 1 (Using a Tool):"


        self.assertTrue(ctrl._is_sentinel_response(inner))


# =============================================================================


# Tests - process_input integration (pre-flight refusal + sentinel defense)


# =============================================================================


# =============================================================================


# Tests - process_input integration (pre-flight refusal + sentinel defense)


# =============================================================================


class TestProcessInputPreFlightAndSentinel(unittest.TestCase):


    """End-to-end coverage of the two error paths added to process_input."""


    def setUp(self):


        from services import get_services


        get_services().reset()  # Clear singleton service state to prevent cross-test contamination


        from config import CONFIG


        CONFIG["mock_mode"] = False


        CONFIG["freeform_mode"] = False


    def test_preflight_refusal_when_history_exceeds_budget(self):


        # qwen2.5-0.5b-instruct -> 2867 char safe budget. Stuffed 10000-char


        # history busts the budget -> pre-flight MUST refuse WITHOUT calling AI.


        ctrl = _make_controller()


        ctrl._load_target_model = MagicMock(return_value="qwen2.5-0.5b-instruct")


        ctrl._get_provider_url_and_headers = MagicMock()


        big = "x" * 5000


        ctrl.history = [


            {"role": "user", "content": big},


            {"role": "assistant", "content": big},


        ]


        ctrl._execute_with_fallback = MagicMock(side_effect=AssertionError("provider MUST NOT be called on pre-flight reject"))


        result = ctrl.process_input("Another question")


        self.assertIn("error", result)


        self.assertIn("overflow", result["final"].lower())  # Layer B rejection tag


        self.assertIn("2867", result["final"])          # Layer B specific budget (contract-pinned; prevents false-positive on unrelated 'overflow' word)


    def test_preflight_overflow_history_is_reverted(self):


        # The newly-appended user message MUST be popped on reject so history is unchanged.


        ctrl = _make_controller()


        ctrl._load_target_model = MagicMock(return_value="qwen2.5-0.5b-instruct")


        ctrl._get_provider_url_and_headers = MagicMock()


        big = "x" * 5000


        ctrl.history = [


            {"role": "user", "content": big},


            {"role": "assistant", "content": big},


        ]


        history_len_before = len(ctrl.history)


        ctrl._execute_with_fallback = MagicMock(side_effect=AssertionError)


        ctrl.process_input("Another question")


        self.assertEqual(len(ctrl.history), history_len_before)


    def test_sentinel_response_returns_error_dict(self):


        # Provider returns the canned greeting -> controller MUST return error dict.


        ctrl = _make_controller()


        ctrl._load_target_model = MagicMock(return_value="")


        ctrl._get_provider_url_and_headers = MagicMock()


        ctrl._execute_with_fallback = MagicMock(return_value=(


            {"content": "Yo! What's on the bench today, mate?", "model": "local_llm", "usage": {}, "error": None},


            "local_llm"


        ))


        # Production calls self.parse_ai (NO underscore) - match the real attr name.


        ctrl.parse_ai = MagicMock(return_value={


            "final": "Yo! What's on the bench today, mate?",


            "thinking": "", "ts": "00:00:00", "command": None,


        })


        result = ctrl.process_input("What is the meaning of life?")


        self.assertIn("error", result)


        self.assertIn("sentinel", result["final"].lower())  # Layer C rejection tag


        self.assertIn("❌", result["final"])             # Layer C user-facing error-card emoji (contract-pinned; ensures shape = user error, not debug text)


    def test_defense_in_depth_wrapped_xml_sentinel(self):


        """Defense-in-depth: even when raw ai_text doesn't match the anchored
        regex or exact-greeting list (because the greeting is wrapped in



        <final_output>...</final_output> tags), the SECOND post-parse


        _is_sentinel_response check on parsed['final'] must still catch it.


        Regression test for the user's original ask: structured-mode response


        whose <final_output> contains a sentinel greeting must be rejected


        even though the raw ai_text itself doesn't trigger the first check.


        """
        from kokertechController import KokertechController



        ctrl = KokertechController()


        ctrl.system_prompt = "x" * 1000


        part1 = "<thinking>Some internal reasoning</thinking>"


        part2 = "<final_output>Yo! What's on the bench today, mate?</final_output>"


        wrapped_sentinel = part1 + chr(10) + part2


        ctrl.provider.chat_completion = MagicMock(return_value={


            "content": wrapped_sentinel,


            "model": "test-model",


            "usage": {},


            "error": None,


        })


        with patch.object(ctrl, "_model_input_budget", return_value=100000):


            with patch.dict("config.CONFIG", clear=False, mock_mode=False, freeform_mode=False):


                result = ctrl.process_input(


                    "Hello", log_callback=lambda x: None,


                )


        # regression: original defense-in-depth ask; pinned to _SENTINEL_RESPONSES greeting


        self.assertNotIn("overflow", result["final"].lower())  # diagnostic: pre-flight must NOT have rejected the wrapped blob (otherwise this is a pre-flight regression, not a sentinel catch)


        self.assertIn("sentinel", result["final"].lower())


        self.assertIn("❌", result["final"])