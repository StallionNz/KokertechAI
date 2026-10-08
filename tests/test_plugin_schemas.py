"""test_plugin_schemas.py — L99 Sprint 1 PluginSchema validation tests.

Coverage:
1. Pure PluginSchema class behavior (unit tests with no plugins on disk).
2. Integration with PluginRegistry._load_plugins() via isolated temp-dirs.
   - Verifies fail-soft semantics: invalid schemas record an error
     but the plugin still registers (execute() remains callable).
   - Verifies schema is excluded from self.schemas and metadata on
     failure (so it cannot reach the LLM prompt path).
3. Graceful degradation in get_tools_for_llm(): invalid-schema plugins
   still appear as tools with empty properties (no crash).
4. Real-registry regression (canary): all 28 real plugins under
   /c/KokertechAI/plugins must validate cleanly under the new wire-up.

Backward compatibility:
  The two original pytest-style tests are kept verbatim at the bottom
  so existing test runs stay green.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pytest

import plugin_registry
from plugin_registry import PluginRegistry, PluginSchema, SchemaError


# ============================================================================
# 1. Pure PluginSchema class tests
# ============================================================================


class TestPluginSchemaClass(unittest.TestCase):
    """Unit tests for PluginSchema validation (no filesystem)."""

    # ---- success cases ------------------------------------------------

    def test_validates_empty_dict(self):
        """Empty schema is allowed (plugin tool with no params)."""
        PluginSchema({}).validate()

    def test_validates_action_only(self):
        """Minimal valid schema: just an action key."""
        PluginSchema({"action": "TEST"}).validate()

    def test_validates_typical_plugin_schema(self):
        """Typical plugin schema with string descriptions."""
        PluginSchema({
            "action": "WRITE_FILE",
            "path": "relative path to file",
            "content": "text content to write",
        }).validate()

    def test_validates_schema_with_boolean_value(self):
        """Real plugin (execute_script.py) has ``"sandbox": True``.

        The best-effort contract allows non-string values as long as
        they are JSON-serializable.
        """
        PluginSchema({
            "action": "EXECUTE_SCRIPT",
            "filename": "script.py",
            "sandbox": True,
        }).validate()

    def test_validates_schema_with_integer_value(self):
        """Integer values are allowed (e.g. example: ``"chunk_size": 1000``)."""
        PluginSchema({
            "action": "INDEX_DOCUMENT",
            "path": "doc path",
            "chunk_size": 1000,
        }).validate()

    def test_validates_real_capture_screen_schema(self):
        """Reference real schema from plugins/capture_screen.py."""
        schema = {
            "action": "CAPTURE_SCREEN",
            "monitor": "<optional monitor number, default 1>",
            "analyze": "<optional boolean, if true runs vision analysis>",
            "query": "<optional natural language question about the screen content>",
        }
        PluginSchema(schema).validate()

    def test_validates_schema_with_example_dict_value(self):
        """Dict values are allowed (illustrative examples)."""
        PluginSchema({
            "action": "WEBHOOK",
            "payload": {"key": "value", "type": "example"},
        }).validate()

    # ---- failure cases ------------------------------------------------

    def test_rejects_non_dict_input_list(self):
        with self.assertRaises(SchemaError) as ctx:
            PluginSchema([])
        self.assertIn("dict", str(ctx.exception))

    def test_rejects_non_dict_input_string(self):
        with self.assertRaises(SchemaError):
            PluginSchema("not a dict")

    def test_accepts_none_input(self):
        """None is accepted — plugins without SCHEMA pass None, and
        _load_plugins() handles this gracefully without errors."""
        # PluginSchema(None) returns early in __init__ — no SchemaError raised.
        # This is the correct production behavior: plugins without SCHEMA 
        # must not trigger validation errors during registry loading.
        schema = PluginSchema(None)
        self.assertIsNone(schema.raw)

    def test_rejects_non_dict_input_integer(self):
        with self.assertRaises(SchemaError):
            PluginSchema(42)

    def test_rejects_non_string_key_int(self):
        # Note: __init__ only checks dict-type, not key-types. Validation
        # of key types happens in validate(). int keys are caught by the
        # ``isinstance(k, str)`` loop in validate (Python's json.dumps
        # would silently str-convert int keys, so we rely on validate to
        # reject them before they reach the LLM prompt path).
        with self.assertRaises(SchemaError) as ctx:
            PluginSchema({42: "value with int key"}).validate()
        self.assertIn("string", str(ctx.exception).lower())

    def test_rejects_non_string_key_tuple(self):
        # tuple keys are caught by json.dumps itself (Python refuses
        # them at serialization time, even with default=str).
        with self.assertRaises(SchemaError):
            PluginSchema({("a", "b"): "value with tuple key"}).validate()

    def test_rejects_circular_reference(self):
        """Circular ref — even with default=str, json.dumps cannot resolve."""
        obj = {}
        obj["self"] = obj
        with self.assertRaises(SchemaError):
            PluginSchema(obj).validate()


# ============================================================================
# 2. Integration tests: _load_plugins() validates and reports errors
# ============================================================================


class TestLoadValidatesSchemas(unittest.TestCase):
    """Isolated-registry integration tests verifying fail-soft semantics."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="plugin_schema_test_")
        self.patcher = patch.object(plugin_registry, "PLUGIN_DIR", self.test_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # ---- helpers ------------------------------------------------------

    def _make_plugin(self, name, content):
        p = os.path.join(self.test_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def _new_registry(self):
        return PluginRegistry()

    # ---- positive cases -----------------------------------------------

    def test_valid_schema_loads_with_metadata(self):
        """Valid SCHEMA → loads cleanly, schema in metadata, no errors."""
        self._make_plugin("good.py", """
COMMAND_NAME = "VALID_PLUGIN"
PLUGIN_METADATA = {"name": "Valid", "description": "Has valid schema", "version": "1.0.0", "tags": ["test"]}
SCHEMA = {"action": "VALID_PLUGIN", "param1": "a description"}
def execute(i): return "ok"
""")
        reg = self._new_registry()
        self.assertEqual(reg.get_plugin_count(), 1)
        self.assertEqual(len(reg.errors), 0)
        self.assertIn("VALID_PLUGIN", reg.plugins)
        self.assertIn("VALID_PLUGIN", reg.metadata)
        self.assertIn("schema", reg.metadata["VALID_PLUGIN"])

        # Schema made it through to self.schemas (LLM prompt path).
        self.assertEqual(len(reg.schemas), 1)
        self.assertEqual(list(reg.schemas.values())[0]["action"], "VALID_PLUGIN")

    def test_no_schema_plugin_loads_cleanly(self):
        """Plugin without SCHEMA → loads cleanly, no schema in metadata."""
        self._make_plugin("noschema.py", """
COMMAND_NAME = "NO_SCHEMA_PLUGIN"
PLUGIN_METADATA = {"name": "No Schema", "description": "No schema", "version": "1.0.0", "tags": []}
def execute(i): return "ok"
""")
        reg = self._new_registry()
        self.assertEqual(reg.get_plugin_count(), 1)
        self.assertEqual(len(reg.errors), 0)
        # No schema attached to metadata
        self.assertNotIn("schema", reg.metadata["NO_SCHEMA_PLUGIN"])
        # self.schemas is empty
        self.assertEqual(len(reg.schemas), 0)

    # ---- fail-soft semantics ------------------------------------------

    def test_invalid_schema_plugin_still_registers_but_no_schema_in_metadata(self):
        """Invalid SCHEMA → plugin still registers, schema NOT in metadata,
        error recorded with filename, execute() still callable."""
        self._make_plugin("bad_schema.py", """
COMMAND_NAME = "INVALID_SCHEMA_PLUGIN"
PLUGIN_METADATA = {"name": "Bad Schema", "description": "Has bad schema", "version": "1.0.0", "tags": []}
SCHEMA = []  # Not a dict!
def execute(i): return "ok"
""")
        reg = self._new_registry()

        # Plugin still registers (fail-soft — backward compat)
        self.assertEqual(reg.get_plugin_count(), 1)
        self.assertIn("INVALID_SCHEMA_PLUGIN", reg.plugins)

        # But schema is NOT in metadata (LLM prompt path is protected)
        self.assertNotIn("schema", reg.metadata["INVALID_SCHEMA_PLUGIN"])

        # Error recorded with filename prefix
        self.assertEqual(len(reg.errors), 1)
        self.assertIn("bad_schema.py", reg.errors[0])
        self.assertIn("schema validation failed", reg.errors[0])

        # Execute() still works (plugin is functional)
        result = reg.execute_command({"action": "INVALID_SCHEMA_PLUGIN"})
        self.assertEqual(result, "ok")

    def test_mixed_valid_and_invalid_schemas_dont_cross_contaminate(self):
        """One good plugin + one bad plugin → only good reaches schema path,
        bad gets its own error, both register, good still works."""
        self._make_plugin("good.py", """
COMMAND_NAME = "GOOD_PLUGIN"
PLUGIN_METADATA = {"name": "Good", "description": "", "version": "1.0.0", "tags": []}
SCHEMA = {"action": "GOOD_PLUGIN", "input": "input data"}
def execute(i): return "ok"
""")
        self._make_plugin("bad.py", """
COMMAND_NAME = "BAD_PLUGIN"
PLUGIN_METADATA = {"name": "Bad", "description": "", "version": "1.0.0", "tags": []}
SCHEMA = {42: "non-string key"}  # raises SchemaError
def execute(i): return "ok"
""")
        reg = self._new_registry()

        # Both plugins registered (fail-soft)
        self.assertEqual(reg.get_plugin_count(), 2)
        self.assertIn("GOOD_PLUGIN", reg.plugins)
        self.assertIn("BAD_PLUGIN", reg.plugins)

        # Only good has schema in metadata
        self.assertIn("schema", reg.metadata["GOOD_PLUGIN"])
        self.assertNotIn("schema", reg.metadata["BAD_PLUGIN"])

        # Only bad plugin's error recorded
        self.assertEqual(len(reg.errors), 1)
        self.assertIn("bad.py", reg.errors[0])

        # self.schemas has only the good one
        self.assertEqual(len(reg.schemas), 1)

        # Execute still works for both
        self.assertEqual(reg.execute_command({"action": "GOOD_PLUGIN"}), "ok")
        self.assertEqual(reg.execute_command({"action": "BAD_PLUGIN"}), "ok")

    # ---- downstream consumer protection --------------------------------

    def test_invalid_schema_does_not_pollute_system_prompt(self):
        """get_system_prompt_addition() must not crash AND must not include
        the invalid-schema entry (graceful degradation)."""
        self._make_plugin("ok.py", """
COMMAND_NAME = "OK_PLUGIN"
PLUGIN_METADATA = {"name": "OK", "description": "", "version": "1.0.0", "tags": []}
SCHEMA = {"action": "OK_PLUGIN", "param": "x"}
def execute(i): return "ok"
""")
        self._make_plugin("bad.py", """
COMMAND_NAME = "BAD_PLUGIN"
PLUGIN_METADATA = {"name": "Bad", "description": "", "version": "1.0.0", "tags": []}
SCHEMA = [1, 2, 3]  # invalid
def execute(i): return "ok"
""")
        reg = self._new_registry()
        # Must NOT crash
        prompt = reg.get_system_prompt_addition()
        # Good plugin shows up, bad plugin's schema doesn't reach LLM
        self.assertIn("OK_PLUGIN", prompt)
        self.assertNotIn("BAD_PLUGIN", prompt)

    def test_invalid_schema_plugin_still_appears_in_get_tools_for_llm_with_empty_properties(self):
        """Graceful degradation: invalid-schema plugin still appears as a
        tool in get_tools_for_llm() but with empty properties {}.

        This is the right behavior — the LLM can still see the tool exists
        and might call it (with no parameters), but a schema malformed
        enough to be undumpable must not crash the prompt-tooling layer.
        """
        self._make_plugin("bad.py", """
COMMAND_NAME = "BAD_SCHEMA_TOOL"
PLUGIN_METADATA = {"name": "Bad Schema Tool", "description": "Tool with bad schema", "version": "1.0.0", "tags": ["test"]}
SCHEMA = "not even a dict"  # invalid
def execute(i): return "ok"
""")
        reg = self._new_registry()
        tools = reg.get_tools_for_llm()
        names = [t["name"] for t in tools]

        # Bad-schema plugin still appears as a tool (graceful degradation)
        self.assertIn("BAD_SCHEMA_TOOL", names)
        bad_tool = next(t for t in tools if t["name"] == "BAD_SCHEMA_TOOL")
        # But with empty properties (no schema → no params inferred)
        self.assertEqual(bad_tool["parameters"]["properties"], {})
        # And empty required (nothing to require)
        self.assertEqual(bad_tool["parameters"]["required"], [])

    def test_list_plugins_shows_validation_error(self):
        """list_plugins() must surface the validation error to operators."""
        self._make_plugin("bad.py", """
COMMAND_NAME = "X"
PLUGIN_METADATA = {"name": "X", "description": "", "version": "1.0.0", "tags": []}
SCHEMA = [1, 2, 3]
def execute(i): return "ok"
""")
        reg = self._new_registry()
        output = reg.list_plugins()
        self.assertIn("Load Errors (1)", output)
        self.assertIn("bad.py", output)
        self.assertIn("schema validation failed", output)

    def test_disabled_state_unaffected_by_schema_validation(self):
        """Schema validation must NOT change disable/enable behavior."""
        self._make_plugin("valid.py", """
COMMAND_NAME = "VALID"
PLUGIN_METADATA = {"name": "V", "description": "", "version": "1.0.0", "tags": []}
SCHEMA = {"action": "VALID"}
def execute(i): return "ok"
""")
        self._make_plugin("invalid.py", """
COMMAND_NAME = "INVALID"
PLUGIN_METADATA = {"name": "I", "description": "", "version": "1.0.0", "tags": []}
SCHEMA = {99: "bad key"}
def execute(i): return "ok"
""")
        reg = self._new_registry()

        # Disable valid plugin → still works as before
        reg.disable("VALID")
        self.assertFalse(reg.is_enabled("VALID"))
        reg.enable("VALID")
        self.assertTrue(reg.is_enabled("VALID"))

        # Disable invalid-schema plugin → also still works
        reg.disable("INVALID")
        self.assertFalse(reg.is_enabled("INVALID"))
        # Trying to execute disabled plugin returns the standard error
        result = reg.execute_command({"action": "INVALID"})
        self.assertIn("disabled", result.lower())


# ============================================================================
# 3. Real-registry regression — all 28 plugins must validate cleanly
# ============================================================================


class TestRealRegistryAllPluginsValidate(unittest.TestCase):
    """Canary: if any real plugin is malformed under the new schema contract,
    this test will surface it. This guards against silent regressions when
    new plugins are added or existing schemas are edited."""

    def test_real_registry_has_zero_schema_errors(self):
        """Zero errors in the real registry (no import failures, no schema
        validation failures).

        This assertion is semantically the same as
        ``test_plugin_registry.py::TestPluginRegistry::test_no_load_errors``
        but is intentionally kept here as part of the Sprint 1 schema
        regression guard: it pins that the new ``PluginSchema.validate()``
        wire-up in ``_load_plugins()`` does not regress the registry's
        error-zero invariant on real plugin files.
        """
        reg = plugin_registry.registry
        self.assertGreaterEqual(
            reg.get_plugin_count(), 20,
            "Real registry should have at least 20 plugins loaded",
        )
        self.assertEqual(
            len(reg.errors), 0,
            f"Real registry has errors (schema or import): {reg.errors}",
        )

    def test_real_registry_every_loaded_schema_validates_directly(self):
        """Every schema-bearing plugin in metadata must pass
        PluginSchema.validate() directly (the wire-up should have already
        excluded invalid ones, so this is a meta-check that the wire-up is
        consistent with the class's contract)."""
        reg = plugin_registry.registry
        schema_count = 0
        for cmd, meta in reg.metadata.items():
            if "schema" not in meta:
                continue
            schema_count += 1
            with self.subTest(plugin=cmd):
                # Must not raise
                PluginSchema(meta["schema"]).validate()
        # Sanity: at least 15 real plugins have schemas (guards against
        # accidentally regressing to "nobody passes a schema").
        self.assertGreaterEqual(
            schema_count, 15,
            f"Expected >=15 real plugins with schemas, got {schema_count}",
        )


# ============================================================================
# 4. Backward-compatibility — original pytest-style tests (unchanged)
# ============================================================================


def test_all_plugins_with_schema_have_valid_schema_types():
    """Original courtesy test (kept verbatim). Iterates real registry."""
    reg = plugin_registry.registry
    for cmd, meta in reg.metadata.items():
        if "schema" not in meta:
            continue
        raw = meta["schema"]
        plugin_registry.PluginSchema(raw).validate()


def test_plugin_registry_exposes_schema_types_errors_list():
    """Original courtesy test (kept verbatim). Verifies errors list."""
    reg = plugin_registry.registry
    assert isinstance(reg.errors, list)
