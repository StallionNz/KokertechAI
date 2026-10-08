import unittest, os, tempfile, sys
from unittest.mock import patch
import plugin_registry as pr


class TestPluginRegistry(unittest.TestCase):
    """Tests on the real 23-plugin registry."""

    def test_all_plugins_loaded(self):
        self.assertGreaterEqual(pr.registry.get_plugin_count(), 20)

    def test_no_load_errors(self):
        self.assertEqual(len(pr.registry.errors), 0)

    def test_metadata_fields_present(self):
        required = {"name", "description", "version", "tags"}
        for cmd, meta in pr.registry.metadata.items():
            with self.subTest(cmd=cmd):
                self.assertEqual(required, set(meta.keys()) & required)

    def test_metadata_types(self):
        for cmd, meta in pr.registry.metadata.items():
            with self.subTest(cmd=cmd):
                self.assertIsInstance(meta["name"], str)
                self.assertGreater(len(meta["name"]), 0)
                self.assertRegex(meta["version"], r"^\d+\.\d+\.\d+$")
                self.assertIsInstance(meta["tags"], list)
                for t in meta["tags"]:
                    self.assertIsInstance(t, str)

    def test_list_plugins_contains_all_cmds(self):
        output = pr.registry.list_plugins()
        for cmd in pr.registry.plugins:
            self.assertIn(cmd, output)

    def test_execute_unknown_command(self):
        result = pr.registry.execute_command({"action": "NONEXISTENT"})
        self.assertIn("unknown", result.lower())

    def test_execute_all_plugins_survive_empty_params(self):
        for cmd, fn in pr.registry.plugins.items():
            with self.subTest(cmd=cmd):
                try:
                    r = fn({})
                    self.assertIsInstance(r, str)
                except Exception as e:
                    self.fail(f"Plugin {cmd} crashed on empty params: {e}")

class TestIsolatedRegistry(unittest.TestCase):
    """Tests using a temp plugins directory (no side effects)."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.patcher = patch.object(pr, "PLUGIN_DIR", self.test_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_plugin(self, name, content):
        p = os.path.join(self.test_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def _new_registry(self):
        from plugin_registry import PluginRegistry
        return PluginRegistry()

    def test_empty_dir_loads_zero(self):
        reg = self._new_registry()
        self.assertEqual(reg.get_plugin_count(), 0)

    def test_valid_plugin_loads_with_metadata(self):
        self._make_plugin("good.py", """
COMMAND_NAME = "TEST"
PLUGIN_METADATA = {"name": "Test Plugin", "description": "A test", "version": "1.0.0", "tags": ["test"]}
SCHEMA = {"action": "TEST"}
def execute(intent):
    return "result"
""")
        reg = self._new_registry()
        self.assertEqual(reg.get_plugin_count(), 1)
        self.assertEqual(reg.metadata["TEST"]["name"], "Test Plugin")
        self.assertEqual(reg.metadata["TEST"]["version"], "1.0.0")
        self.assertEqual(reg.metadata["TEST"]["tags"], ["test"])

    def test_plugin_missing_execute_skipped(self):
        self._make_plugin("noexec.py", 'COMMAND_NAME = "SKIP_ME"')
        reg = self._new_registry()
        self.assertEqual(reg.get_plugin_count(), 0)

    def test_plugin_import_error_tracked(self):
        self._make_plugin("broken.py", "import nonexistent_module_xyz\nCOMMAND_NAME = \"X\"\ndef execute(i): return ''\n")
        reg = self._new_registry()
        self.assertEqual(reg.get_plugin_count(), 0)
        self.assertEqual(len(reg.errors), 1)
        self.assertIn("broken.py", reg.errors[0])

    def test_mixed_good_and_bad_plugins(self):
        self._make_plugin("good.py", """
COMMAND_NAME = "GOOD"
PLUGIN_METADATA = {"name": "Good", "description": "", "version": "1.0.0", "tags": []}
SCHEMA = {"action": "GOOD"}
def execute(i): return "ok"
""")
        self._make_plugin("bad.py", "import nonexistent_module\nCOMMAND_NAME = \"BAD\"\ndef execute(i): return ''\n")
        reg = self._new_registry()
        self.assertEqual(reg.get_plugin_count(), 1)
        self.assertIn("GOOD", reg.plugins)
        self.assertEqual(len(reg.errors), 1)
        self.assertIn("bad.py", reg.errors[0])

    # ----------------------------------------------------------------
    # ENABLE / DISABLE TESTS
    # ----------------------------------------------------------------
    def _make_simple_registry(self):
        """Helper: returns a registry with 2 plugins (ALPHA, BETA)."""
        self._make_plugin("alpha.py", """
COMMAND_NAME = "ALPHA"
PLUGIN_METADATA = {"name": "Alpha", "description": "First plugin", "version": "1.0.0", "tags": ["a"]}
SCHEMA = {"action": "ALPHA"}
def execute(i): return "alpha ok"
""")
        self._make_plugin("beta.py", """
COMMAND_NAME = "BETA"
PLUGIN_METADATA = {"name": "Beta", "description": "Second plugin", "version": "2.0.0", "tags": ["b"]}
def execute(i): return "beta ok"
""")
        return self._new_registry()

    def test_enable_disable_is_enabled(self):
        reg = self._make_simple_registry()
        # All start enabled
        self.assertTrue(reg.is_enabled("ALPHA"))
        self.assertTrue(reg.is_enabled("BETA"))
        # Disable one
        reg.disable("ALPHA")
        self.assertFalse(reg.is_enabled("ALPHA"))
        self.assertTrue(reg.is_enabled("BETA"))
        # Re-enable
        reg.enable("ALPHA")
        self.assertTrue(reg.is_enabled("ALPHA"))
        self.assertTrue(reg.is_enabled("BETA"))

    def test_disable_twice_no_error(self):
        reg = self._make_simple_registry()
        reg.disable("ALPHA")
        reg.disable("ALPHA")  # should not raise
        self.assertFalse(reg.is_enabled("ALPHA"))

    def test_enable_twice_no_error(self):
        reg = self._make_simple_registry()
        reg.disable("ALPHA")
        reg.enable("ALPHA")
        reg.enable("ALPHA")  # should not raise
        self.assertTrue(reg.is_enabled("ALPHA"))

    def test_toggle(self):
        reg = self._make_simple_registry()
        # Toggle off
        result = reg.toggle("ALPHA")
        self.assertFalse(result)  # returns False when now disabled
        self.assertFalse(reg.is_enabled("ALPHA"))
        # Toggle back on
        result = reg.toggle("ALPHA")
        self.assertTrue(result)  # returns True when now enabled
        self.assertTrue(reg.is_enabled("ALPHA"))

    def test_execute_disabled_returns_error(self):
        reg = self._make_simple_registry()
        reg.disable("ALPHA")
        result = reg.execute_command({"action": "ALPHA"})
        self.assertIn("disabled", result.lower())
        self.assertIn("ALPHA", result)

    def test_execute_enabled_still_works(self):
        reg = self._make_simple_registry()
        result = reg.execute_command({"action": "ALPHA"})
        self.assertEqual(result, "alpha ok")

    def test_execute_unknown_ignores_disabled_state(self):
        reg = self._make_simple_registry()
        result = reg.execute_command({"action": "NONEXISTENT"})
        self.assertIn("unknown", result.lower())

    def test_get_enabled_count(self):
        reg = self._make_simple_registry()
        self.assertEqual(reg.get_enabled_count(), 2)
        reg.disable("ALPHA")
        self.assertEqual(reg.get_enabled_count(), 1)
        reg.disable("BETA")
        self.assertEqual(reg.get_enabled_count(), 0)
        reg.enable("ALPHA")
        self.assertEqual(reg.get_enabled_count(), 1)

    def test_list_plugins_shows_disabled_status(self):
        reg = self._make_simple_registry()
        output = reg.list_plugins()
        self.assertIn("[ON] Alpha", output)
        self.assertIn("[ON] Beta", output)
        # Disable one and re-check
        reg.disable("ALPHA")
        output2 = reg.list_plugins()
        self.assertIn("[OFF] Alpha", output2)
        self.assertIn("[ON] Beta", output2)

    def test_get_system_prompt_filters_disabled(self):
        reg = self._make_simple_registry()
        # Only ALPHA has a SCHEMA; BETA does not
        prompt_all = reg.get_system_prompt_addition()
        self.assertIn("ALPHA", prompt_all)
        self.assertNotIn("BETA", prompt_all)  # no schema
        # Disable ALPHA — its schema should disappear
        reg.disable("ALPHA")
        prompt_disabled = reg.get_system_prompt_addition()
        self.assertNotIn("ALPHA", prompt_disabled)
        self.assertEqual(prompt_disabled, "[]")

    def test_get_system_prompt_all_disabled_returns_empty(self):
        reg = self._make_simple_registry()
        reg.disable("ALPHA")
        # BETA has no schema, ALPHA is disabled — result should be empty
        prompt = reg.get_system_prompt_addition()
        self.assertEqual(prompt, "[]")

    # ----------------------------------------------------------------
    # PLUGIN METADATA DEFAULTS
    # ----------------------------------------------------------------

    def test_plugin_without_metadata_gets_defaults(self):
        self._make_plugin("bare.py", """
COMMAND_NAME = "BARE"
SCHEMA = {"action": "BARE", "param1": "a string param"}
def execute(i): return "bare ok"
""")
        reg = self._new_registry()
        self.assertEqual(reg.get_plugin_count(), 1)
        meta = reg.metadata["BARE"]
        self.assertEqual(meta["name"], "Bare")
        self.assertEqual(meta["description"], "No description provided.")
        self.assertEqual(meta["version"], "0.0.0")
        self.assertEqual(meta["tags"], [])
        self.assertIn("schema", meta)

    # ----------------------------------------------------------------
    # EXECUTE_COMMAND EXCEPTION
    # ----------------------------------------------------------------

    def test_execute_command_catches_exception(self):
        self._make_plugin("crash.py", """
COMMAND_NAME = "CRASH"
PLUGIN_METADATA = {"name": "Crash", "description": "", "version": "1.0.0", "tags": []}
def execute(i):
    raise RuntimeError("Something broke")
""")
        reg = self._new_registry()
        result = reg.execute_command({"action": "CRASH"})
        self.assertIn("EXECUTION FATAL ERROR", result)
        self.assertIn("CRASH", result)
        self.assertIn("Something broke", result)

    # ----------------------------------------------------------------
    # LIST_PLUGINS WITH ERRORS
    # ----------------------------------------------------------------

    def test_list_plugins_shows_load_errors(self):
        self._make_plugin("good.py", """
COMMAND_NAME = "GOOD"
PLUGIN_METADATA = {"name": "Good", "description": "", "version": "1.0.0", "tags": []}
def execute(i): return "ok"
""")
        self._make_plugin("broken.py", "syntax error!!!")
        reg = self._new_registry()
        output = reg.list_plugins()
        self.assertIn("Load Errors (1)", output)
        self.assertIn("broken.py", output)

    # ----------------------------------------------------------------
    # EXECUTION ERROR TRACKING
    # ----------------------------------------------------------------

    def _make_crash_plugin_registry(self):
        """Helper: registry with one plugin that raises on execute."""
        self._make_plugin("crashy.py", """
COMMAND_NAME = "CRASHY"
PLUGIN_METADATA = {"name": "Crashy", "description": "Boom", "version": "1.0.0", "tags": []}
def execute(i):
    raise RuntimeError("Intentional failure")
""")
        return self._new_registry()

    def _make_error_return_plugin_registry(self):
        """Helper: registry with one plugin that returns [ERROR]."""
        self._make_plugin("errreturn.py", """
COMMAND_NAME = "ERRRET"
PLUGIN_METADATA = {"name": "ErrRet", "description": "Returns error string", "version": "1.0.0", "tags": []}
def execute(i):
    return "[ERROR] Something went wrong but not an exception"
""")
        return self._new_registry()

    def test_execution_error_tracks_exception(self):
        reg = self._make_crash_plugin_registry()
        reg.execute_command({"action": "CRASHY"})
        err = reg.get_execution_errors("CRASHY")
        self.assertIsNotNone(err)
        self.assertEqual(err["count"], 1)
        self.assertIn("Intentional failure", err["last_error"])

    def test_execution_error_increments_count(self):
        reg = self._make_crash_plugin_registry()
        reg.execute_command({"action": "CRASHY"})
        reg.execute_command({"action": "CRASHY"})
        reg.execute_command({"action": "CRASHY"})
        err = reg.get_execution_errors("CRASHY")
        self.assertEqual(err["count"], 3)

    def test_execution_error_clean_plugin(self):
        reg = self._make_simple_registry()
        reg.execute_command({"action": "ALPHA"})
        err = reg.get_execution_errors("ALPHA")
        self.assertIsNone(err)

    def test_execution_error_tracks_returned_error(self):
        reg = self._make_error_return_plugin_registry()
        reg.execute_command({"action": "ERRRET"})
        err = reg.get_execution_errors("ERRRET")
        self.assertIsNotNone(err)
        self.assertEqual(err["count"], 1)
        self.assertIn("Something went wrong", err["last_error"])

    def test_get_execution_errors_all(self):
        reg = self._make_crash_plugin_registry()
        reg.execute_command({"action": "CRASHY"})
        all_errors = reg.get_execution_errors()
        self.assertIn("CRASHY", all_errors)
        self.assertEqual(len(all_errors), 1)

    def test_clear_execution_errors_single(self):
        reg = self._make_crash_plugin_registry()
        reg.execute_command({"action": "CRASHY"})
        reg.clear_execution_errors("CRASHY")
        self.assertIsNone(reg.get_execution_errors("CRASHY"))

    def test_clear_execution_errors_all(self):
        reg = self._make_crash_plugin_registry()
        reg.execute_command({"action": "CRASHY"})
        reg.clear_execution_errors()
        self.assertEqual(reg.get_execution_errors(), {})

    def test_execution_error_unknown_plugin_not_tracked(self):
        reg = self._make_simple_registry()
        reg.execute_command({"action": "NONEXISTENT"})
        self.assertEqual(reg.get_execution_errors(), {})

    def test_execution_error_disabled_plugin_not_tracked(self):
        reg = self._make_simple_registry()
        reg.disable("ALPHA")
        reg.execute_command({"action": "ALPHA"})
        self.assertEqual(reg.get_execution_errors(), {})

    # ----------------------------------------------------------------
    # GET_SKILL_CATEGORIES
    # ----------------------------------------------------------------

    def _make_categorized_registry(self):
        """Helper: registry with plugins in different categories."""
        self._make_plugin("web_search.py", """
COMMAND_NAME = "WEB_SEARCH"
PLUGIN_METADATA = {"name": "Web Search", "description": "Search the web", "version": "1.0.0", "tags": ["web"]}
SCHEMA = {"action": "WEB_SEARCH", "query": "Search query"}
def execute(i): return "search results"
""")
        self._make_plugin("fetch_page.py", """
COMMAND_NAME = "FETCH_PAGE"
PLUGIN_METADATA = {"name": "Fetch Page", "description": "Fetch a URL", "version": "1.0.0", "tags": ["web"]}
def execute(i): return "page content"
""")
        self._make_plugin("store_data.py", """
COMMAND_NAME = "STORE_DATA"
PLUGIN_METADATA = {"name": "Store Data", "description": "Store in memory", "version": "1.0.0", "tags": ["memory"]}
def execute(i): return "stored"
""")
        self._make_plugin("no_tags.py", """
COMMAND_NAME = "NO_TAGS"
PLUGIN_METADATA = {"name": "No Tags", "description": "Untagged", "version": "1.0.0", "tags": []}
def execute(i): return "ok"
""")
        return self._new_registry()

    def test_get_skill_categories_structure(self):
        reg = self._make_categorized_registry()
        cats = reg.get_skill_categories()
        self.assertIn("web", cats)
        self.assertIn("memory", cats)
        self.assertIn("general", cats)  # untagged goes to "general"
        self.assertEqual(len(cats["web"]), 2)
        self.assertEqual(len(cats["memory"]), 1)
        self.assertEqual(len(cats["general"]), 1)

    def test_get_skill_categories_disabled_excluded(self):
        reg = self._make_categorized_registry()
        reg.disable("WEB_SEARCH")
        cats = reg.get_skill_categories()
        self.assertEqual(len(cats["web"]), 1)
        self.assertEqual(cats["web"][0]["command"], "FETCH_PAGE")

    def test_get_skill_categories_empty_registry(self):
        reg = self._new_registry()
        cats = reg.get_skill_categories()
        self.assertEqual(cats, {})

    # ----------------------------------------------------------------
    # GET_TOOLS_FOR_LLM
    # ----------------------------------------------------------------

    def test_get_tools_for_llm_all_categories(self):
        reg = self._make_categorized_registry()
        tools = reg.get_tools_for_llm()
        self.assertEqual(len(tools), 4)
        names = [t["name"] for t in tools]
        self.assertIn("WEB_SEARCH", names)
        self.assertIn("STORE_DATA", names)
        self.assertIn("NO_TAGS", names)

    def test_get_tools_for_llm_filtered_by_category(self):
        reg = self._make_categorized_registry()
        tools = reg.get_tools_for_llm(categories=["web"])
        self.assertEqual(len(tools), 2)
        names = [t["name"] for t in tools]
        self.assertIn("WEB_SEARCH", names)
        self.assertIn("FETCH_PAGE", names)
        self.assertNotIn("STORE_DATA", names)

    def test_get_tools_for_llm_category_not_found(self):
        reg = self._make_categorized_registry()
        tools = reg.get_tools_for_llm(categories=["database"])
        self.assertEqual(tools, [])

    def test_get_tools_for_llm_excludes_disabled(self):
        reg = self._make_categorized_registry()
        reg.disable("WEB_SEARCH")
        tools = reg.get_tools_for_llm()
        names = [t["name"] for t in tools]
        self.assertNotIn("WEB_SEARCH", names)

    def test_get_tools_for_llm_type_inference(self):
        self._make_plugin("typed.py", """
COMMAND_NAME = "TYPED"
PLUGIN_METADATA = {"name": "Typed", "description": "Has typed params", "version": "1.0.0", "tags": ["test"]}
SCHEMA = {
    "action": "TYPED",
    "count": "The count limit number",
    "enabled": "A boolean flag to enable",
    "name": "A string parameter",
}
def execute(i): return "ok"
""")
        reg = self._new_registry()
        tools = reg.get_tools_for_llm()
        self.assertEqual(len(tools), 1)
        props = tools[0]["parameters"]["properties"]
        self.assertEqual(props["count"]["type"], "integer")
        self.assertEqual(props["enabled"]["type"], "boolean")
        self.assertEqual(props["name"]["type"], "string")
        # Action should be excluded from properties
        self.assertNotIn("action", props)

    def test_get_tools_for_llm_trims_at_max(self):
        # Create more than MAX_SCHEMAS_PER_LOAD (15) plugins
        for i in range(18):
            self._make_plugin(f"plugin{i}.py", f"""
COMMAND_NAME = "PLUGIN_{i}"
PLUGIN_METADATA = {{"name": "Plugin {i}", "description": "", "version": "1.0.0", "tags": ["test"]}}
SCHEMA = {{"action": "PLUGIN_{i}", "param": "desc"}}
def execute(i): return "ok"
""")
        from plugin_registry import MAX_SCHEMAS_PER_LOAD
        reg = self._new_registry()
        tools = reg.get_tools_for_llm()
        self.assertLessEqual(len(tools), MAX_SCHEMAS_PER_LOAD)

    def test_get_tools_for_llm_empty_registry(self):
        reg = self._new_registry()
        tools = reg.get_tools_for_llm()
        self.assertEqual(tools, [])

    # ----------------------------------------------------------------
    # GET_CATEGORY_SUMMARY
    # ----------------------------------------------------------------

    def test_get_category_summary_format(self):
        reg = self._make_categorized_registry()
        summary = reg.get_category_summary()
        self.assertIn("Available Skill Categories:", summary)
        self.assertIn("[web]", summary)
        self.assertIn("[memory]", summary)
        self.assertIn("[general]", summary)
        self.assertIn("WEB_SEARCH", summary)
        self.assertIn("STORE_DATA", summary)
        self.assertIn("NO_TAGS", summary)

    def test_get_category_summary_empty(self):
        reg = self._new_registry()
        summary = reg.get_category_summary()
        self.assertIn("Available Skill Categories:", summary)

    # ----------------------------------------------------------------
    # PROGRESSIVE_DISCLOSURE_SYSTEM_PROMPT
    # ----------------------------------------------------------------

    def test_progressive_disclosure_system_prompt(self):
        reg = self._make_categorized_registry()
        prompt = reg.progressive_disclosure_system_prompt()
        self.assertIn("AVAILABLE SKILL CATEGORIES", prompt)
        self.assertIn("LOAD_TOOLS:web", prompt)
        self.assertIn("LOAD_TOOLS:web,memory", prompt)
        self.assertIn("[web]", prompt)
        self.assertIn("[memory]", prompt)
        self.assertIn("[general]", prompt)

    def test_progressive_disclosure_system_prompt_empty(self):
        reg = self._new_registry()
        prompt = reg.progressive_disclosure_system_prompt()
        self.assertIn("AVAILABLE SKILL CATEGORIES", prompt)
        self.assertIn("LOAD_TOOLS", prompt)

    # ----------------------------------------------------------------
    # INTEGRATION: REAL PLUGIN LIFECYCLE
    # ----------------------------------------------------------------

    def _make_lifecycle_registry(self):
        """Helper: registry with 3 plugins for lifecycle testing."""
        self._make_plugin("alpha.py", """
COMMAND_NAME = "ALPHA"
PLUGIN_METADATA = {"name": "Alpha", "description": "First plugin", "version": "1.0.0", "tags": ["core"]}
SCHEMA = {"action": "ALPHA", "param": "A parameter"}
def execute(i): return "alpha result"
""")
        self._make_plugin("beta.py", """
COMMAND_NAME = "BETA"
PLUGIN_METADATA = {"name": "Beta", "description": "Second plugin", "version": "2.0.0", "tags": ["core"]}
def execute(i): return "beta result"
""")
        self._make_plugin("gamma.py", """
COMMAND_NAME = "GAMMA"
PLUGIN_METADATA = {"name": "Gamma", "description": "Third plugin", "version": "3.0.0", "tags": ["advanced"]}
SCHEMA = {"action": "GAMMA", "input": "Input data"}
def execute(i):
    if not i.get("input"):
        return "[ERROR] Gamma requires input parameter"
    return "gamma result: " + i["input"]
""")
        return self._new_registry()

    def test_lifecycle_plugin_count_and_list(self):
        """Full lifecycle: load, verify count, verify list output contains all."""
        reg = self._make_lifecycle_registry()
        self.assertEqual(reg.get_plugin_count(), 3)
        self.assertEqual(reg.get_enabled_count(), 3)
        output = reg.list_plugins()
        self.assertIn("ALPHA", output)
        self.assertIn("BETA", output)
        self.assertIn("GAMMA", output)

    def test_lifecycle_disable_enable_then_execute(self):
        """Disable a plugin → verify disabled — re-enable → verify executes again."""
        reg = self._make_lifecycle_registry()

        # Execute before disable — succeeds
        result = reg.execute_command({"action": "ALPHA", "param": "test"})
        self.assertEqual(result, "alpha result")

        # Disable and verify
        reg.disable("ALPHA")
        self.assertFalse(reg.is_enabled("ALPHA"))
        self.assertEqual(reg.get_enabled_count(), 2)

        # Execute while disabled — fails with disabled message
        result = reg.execute_command({"action": "ALPHA", "param": "test"})
        self.assertIn("disabled", result.lower())

        # Re-enable and verify execute works again
        reg.enable("ALPHA")
        self.assertTrue(reg.is_enabled("ALPHA"))
        self.assertEqual(reg.get_enabled_count(), 3)
        result = reg.execute_command({"action": "ALPHA", "param": "test"})
        self.assertEqual(result, "alpha result")

    def test_lifecycle_execution_error_track_and_clear(self):
        """Execute a plugin that returns [ERROR] → verify tracked → clear → verify cleared."""
        reg = self._make_lifecycle_registry()

        # Execute GAMMA without required param — should return [ERROR]
        result1 = reg.execute_command({"action": "GAMMA"})
        self.assertIn("[ERROR]", result1)

        # Verify error was tracked
        err1 = reg.get_execution_errors("GAMMA")
        self.assertIsNotNone(err1)
        self.assertEqual(err1["count"], 1)
        self.assertIn("requires input", err1["last_error"])

        # Execute again — count should increment
        result2 = reg.execute_command({"action": "GAMMA"})
        self.assertIn("[ERROR]", result2)
        err2 = reg.get_execution_errors("GAMMA")
        self.assertEqual(err2["count"], 2)

        # Clear single plugin errors
        reg.clear_execution_errors("GAMMA")
        self.assertIsNone(reg.get_execution_errors("GAMMA"))

        # Clear all (should be noop now since already cleared)
        reg.clear_execution_errors()
        self.assertEqual(reg.get_execution_errors(), {})

    def test_lifecycle_multi_plugin_errors(self):
        """Two plugins erroring independently — verify separate counts."""
        self._make_plugin("crash_a.py", """
COMMAND_NAME = "CRASH_A"
PLUGIN_METADATA = {"name": "CrashA", "description": "", "version": "1.0.0", "tags": []}
def execute(i):
    raise RuntimeError("Crash A failure")
""")
        self._make_plugin("crash_b.py", """
COMMAND_NAME = "CRASH_B"
PLUGIN_METADATA = {"name": "CrashB", "description": "", "version": "1.0.0", "tags": []}
def execute(i):
    raise RuntimeError("Crash B failure")
""")
        reg = self._new_registry()

        # Execute both — each should crash
        reg.execute_command({"action": "CRASH_A"})
        reg.execute_command({"action": "CRASH_B"})
        reg.execute_command({"action": "CRASH_A"})  # A twice

        # Verify independent counts
        err_a = reg.get_execution_errors("CRASH_A")
        self.assertEqual(err_a["count"], 2)
        err_b = reg.get_execution_errors("CRASH_B")
        self.assertEqual(err_b["count"], 1)

        # Verify get_execution_errors() returns both
        all_errors = reg.get_execution_errors()
        self.assertEqual(len(all_errors), 2)
        self.assertIn("CRASH_A", all_errors)
        self.assertIn("CRASH_B", all_errors)

        # Clear all — verify empty
        reg.clear_execution_errors()
        self.assertEqual(reg.get_execution_errors(), {})

    def test_lifecycle_get_system_prompt_addition_filters_disabled(self):
        """System prompt should not include schemas for disabled plugins."""
        reg = self._make_lifecycle_registry()

        # Both ALPHA and GAMMA have schemas; BETA does not
        prompt_all = reg.get_system_prompt_addition()
        self.assertIn("ALPHA", prompt_all)
        self.assertIn("GAMMA", prompt_all)
        self.assertNotIn("BETA", prompt_all)

        # Disable GAMMA — its schema should disappear
        reg.disable("GAMMA")
        prompt_disabled = reg.get_system_prompt_addition()
        self.assertIn("ALPHA", prompt_disabled)
        self.assertNotIn("GAMMA", prompt_disabled)

    def test_lifecycle_skill_categories(self):
        """Skill categories reflect real plugin tags after enable/disable."""
        reg = self._make_lifecycle_registry()
        cats = reg.get_skill_categories()
        self.assertIn("core", cats)
        self.assertIn("advanced", cats)
        self.assertEqual(len(cats["core"]), 2)
        self.assertEqual(len(cats["advanced"]), 1)

        # Disable advanced plugin
        reg.disable("GAMMA")
        cats2 = reg.get_skill_categories()
        self.assertNotIn("advanced", cats2)
        self.assertEqual(len(cats2["core"]), 2)

        # Re-enable — advanced should return
        reg.enable("GAMMA")
        cats3 = reg.get_skill_categories()
        self.assertIn("advanced", cats3)
        self.assertEqual(len(cats3["advanced"]), 1)

    def test_lifecycle_get_tools_for_llm_excludes_disabled(self):
        """Tools for LLM should exclude disabled plugins.
        Note: plugins without SCHEMA still appear (with empty properties).
        """
        reg = self._make_lifecycle_registry()

        tools_all = reg.get_tools_for_llm(categories=["core"])
        names = [t["name"] for t in tools_all]
        self.assertIn("ALPHA", names)
        # BETA has no SCHEMA but still appears (empty properties)
        self.assertIn("BETA", names)

        # Disable ALPHA — only BETA should remain (both are ["core"])
        reg.disable("ALPHA")
        tools_disabled = reg.get_tools_for_llm(categories=["core"])
        names2 = [t["name"] for t in tools_disabled]
        self.assertNotIn("ALPHA", names2)
        self.assertIn("BETA", names2)

    def test_lifecycle_list_plugins_shows_disabled_and_errors(self):
        """list_plugins() should show [OFF] for disabled and load errors."""
        # Create registry with 1 good + 1 broken plugin
        self._make_plugin("good.py", """
COMMAND_NAME = "GOOD"
PLUGIN_METADATA = {"name": "Good", "description": "", "version": "1.0.0", "tags": []}
def execute(i): return "ok"
""")
        self._make_plugin("broken.py", "syntax error!@#$%")
        reg = self._new_registry()

        output = reg.list_plugins()
        self.assertIn("[ON] Good", output)
        self.assertIn("Load Errors (1)", output)
        self.assertIn("broken.py", output)

        # Disable the good plugin and verify [OFF] appears
        reg.disable("GOOD")
        output2 = reg.list_plugins()
        self.assertIn("[OFF] Good", output2)

    def test_lifecycle_toggle_then_execute(self):
        """Toggle plugin off, verify disabled. Toggle on, verify works."""
        reg = self._make_lifecycle_registry()

        # Toggle GAMMA off
        result = reg.toggle("GAMMA")
        self.assertFalse(result)  # returns False when now disabled
        self.assertFalse(reg.is_enabled("GAMMA"))
        self.assertEqual(reg.get_enabled_count(), 2)

        # Trying to execute disabled plugin returns error
        result = reg.execute_command({"action": "GAMMA"})
        self.assertIn("disabled", result.lower())

        # Toggle back on
        result = reg.toggle("GAMMA")
        self.assertTrue(result)  # returns True when now enabled
        self.assertTrue(reg.is_enabled("GAMMA"))
        self.assertEqual(reg.get_enabled_count(), 3)

        # Execute works again
        result = reg.execute_command({"action": "GAMMA", "input": "hello"})
        self.assertIn("gamma result", result)

    def test_lifecycle_clear_errors_updates_empty_state(self):
        """Verify clear_execution_errors returns empty dict, not None."""
        reg = self._make_lifecycle_registry()
        self.assertEqual(reg.get_execution_errors(), {})

        # Induce an error
        reg.execute_command({"action": "GAMMA"})
        self.assertEqual(len(reg.get_execution_errors()), 1)

        # Clear and verify
        reg.clear_execution_errors()
        self.assertEqual(reg.get_execution_errors(), {})

class TestGetToolsForLlmCache(unittest.TestCase):
    """Phase D row 15.1.3 — output-stability tests for get_tools_for_llm().

    Locks the cache HIT contract documented in plugin_registry.py:
      - Same (categories) call returns byte-identical output across calls
      - Each call returns a NEW list (independent list refs)
      - Top-level tool-dict mutation does NOT propagate into next HIT
      - PLUGIN_DIR mtime change adds a NEW cache key (cache MISS for old)
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.patcher = patch.object(pr, "PLUGIN_DIR", self.test_dir)
        self.patcher.start()
        # Stable 2-plugin set: alpha (web) + beta (web, no schema)
        with open(os.path.join(self.test_dir, "alpha.py"), "w", encoding="utf-8") as f:
            f.write("""
COMMAND_NAME = "ALPHA"
PLUGIN_METADATA = {"name": "Alpha", "description": "First", "version": "1.0.0", "tags": ["web"]}
SCHEMA = {"action": "ALPHA", "input": "input string"}
def execute(i): return "ok"
""")
        with open(os.path.join(self.test_dir, "beta.py"), "w", encoding="utf-8") as f:
            f.write("""
COMMAND_NAME = "BETA"
PLUGIN_METADATA = {"name": "Beta", "description": "Second", "version": "2.0.0", "tags": ["web"]}
def execute(i): return "ok"
""")
        self.reg = pr.PluginRegistry()

    def tearDown(self):
        self.patcher.stop()
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cache_hit_returns_byte_identical_output(self):
        """Multiple HIT calls return byte-identical content."""
        r1 = self.reg.get_tools_for_llm(categories=["web"])
        r2 = self.reg.get_tools_for_llm(categories=["web"])
        r3 = self.reg.get_tools_for_llm(categories=["web"])
        r4 = self.reg.get_tools_for_llm(categories=["web"])

        self.assertEqual(r1, r2)
        self.assertEqual(r2, r3)
        self.assertEqual(r3, r4)
        # Cache should NOT grow from HIT-only calls
        self.assertEqual(len(self.reg._tools_llm_cache), 1)

    def test_cache_hit_returns_independent_list_instances(self):
        """Each call returns a NEW list object — caller can keep/clear
        references without affecting the cache or future calls."""
        results = [self.reg.get_tools_for_llm(categories=["web"]) for _ in range(4)]
        # Different list objects each time
        self.assertIsNot(results[0], results[1])
        self.assertIsNot(results[1], results[2])
        self.assertIsNot(results[0], results[2])
        self.assertIsNot(results[2], results[3])
        # Content identical across all four
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[3])

    def test_cache_top_level_field_mutation_does_not_propagate(self):
        """Mutating a top-level tool-dict field (or clearing the list)
        does NOT propagate into subsequent cache HIT calls.

        This pins the shallow-copy contract at the top-level dict: the
        cache returns ``[dict(t) for t in cache_value]`` which gives
        callers independent top-level dicts they can replace freely.
        """
        r1 = self.reg.get_tools_for_llm(categories=["web"])
        original_first_name = r1[0]["name"]

        # Mutate top-level
        r1[0]["name"] = "HACKED"
        r1[0]["description"] = "HACKED DESC"
        r1.clear()
        r1.append({"junk": True})

        # Cache HIT must return the ORIGINAL tool definitions
        r2 = self.reg.get_tools_for_llm(categories=["web"])
        self.assertEqual(len(r2), 2)
        names = [t["name"] for t in r2]
        self.assertIn(original_first_name, names)
        self.assertNotIn("HACKED", names)
        self.assertNotIn({"junk": True}, r2)
        # First slot's structure is intact
        self.assertEqual(set(r2[0].keys()), {"name", "description", "parameters"})

    def test_cache_invalidates_on_plugin_dir_mtime_change(self):
        """When ``PLUGIN_DIR`` mtime moves (forced here via ``os.utime``),
        the cache key changes and a fresh entry is added on the next call.

        Note on Windows: directory mtime resolution varies by filesystem.
        We force-bump via ``os.utime`` to guarantee the mtime moves even
        when no file is added/removed.
        """
        # Populate cache
        r1 = self.reg.get_tools_for_llm(categories=["web"])
        self.assertEqual(len(r1), 2)
        self.assertEqual(len(self.reg._tools_llm_cache), 1)
        pre_keys = set(self.reg._tools_llm_cache.keys())

        # Force directory mtime forward
        import time
        time.sleep(0.05)  # let timestamp resolution advance
        new_time = time.time() + 100
        os.utime(self.test_dir, (new_time, new_time))

        r2 = self.reg.get_tools_for_llm(categories=["web"])
        # Cache now has 2 entries (old mtime key + new mtime key)
        self.assertGreaterEqual(len(self.reg._tools_llm_cache), 2)
        post_keys = set(self.reg._tools_llm_cache.keys())
        # At least one new cache key appeared (old + new differ)
        self.assertNotEqual(pre_keys, post_keys,
                            "Cache key set should differ after mtime bump")
        # Output content is identical (registry hasn't changed)
        self.assertEqual(r1, r2)

if __name__ == "__main__":
    unittest.main()
