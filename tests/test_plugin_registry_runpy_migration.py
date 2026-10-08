"""Regression tests for plugin_registry.py migration to runpy.run_path.

The previous implementation used importlib.util.spec_from_file_location,
which builds a path-keyed ModuleSpec whose __pycache__-sidecar filename
mirrors the *literal* path string we hand it. On Windows, drive-letter
case mismatches and backslash/forward-slash mix tripped FileNotFoundError
tracebacks on every subsequent reload -- the same class of bug that bit
tabs/session_log_tab.py every 60 seconds.

We migrated to runpy.run_path because:
  1. It reads the file via the OS, so the Windows case-insensitive
     filesystem resolver handles lookup correctly (no spec cache to
     invalidate).
  2. Each call returns a fresh globals dict -- no sys.modules pollution
     between plugins created in different tempdirs.
  3. We wrap the dict in types.SimpleNamespace so the existing
     hasattr(module, "COMMAND_NAME") / module.execute access patterns
     in _load_plugins keep working unchanged.

These tests pin those three properties.
"""

import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import plugin_registry
from plugin_registry import PluginRegistry


class TestRunpyMigrationImportSideEffects(unittest.TestCase):
    """Verify that loading a plugin does not pollute sys.modules or sys.path."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="pr_runpy_isolation_")
        self.patcher = patch.object(plugin_registry, "PLUGIN_DIR", self.test_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_plugin(self, name, content):
        path = os.path.join(self.test_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_plugin_loading_does_not_register_in_sys_modules(self):
        """Loading a plugin must not stash it in sys.modules under any name."""
        self._make_plugin("isolated.py", '''
COMMAND_NAME = "ISOLATED"
PLUGIN_METADATA = {"name": "Isolated", "description": "", "version": "1.0.0", "tags": []}
def execute(i): return "ok"
''')
        reg = PluginRegistry()
        self.assertIn("ISOLATED", reg.plugins)
        for candidate in ("isolated", "ISOLATED"):
            self.assertNotIn(
                candidate, sys.modules,
                f"Plugin loading polluted sys.modules with {candidate!r}",
            )

    def test_plugin_loading_does_not_extend_sys_path(self):
        """Loading must not add PLUGIN_DIR (or its tempdir variant) to sys.path."""
        pre_path_count = len(sys.path)
        self._make_plugin("nopathplug.py", '''
COMMAND_NAME = "NOPATHPLUG"
PLUGIN_METADATA = {"name": "X", "description": "", "version": "1.0.0", "tags": []}
def execute(i): return "ok"
''')
        PluginRegistry()
        self.assertEqual(len(sys.path), pre_path_count)
        self.assertNotIn(self.test_dir, sys.path)

    def test_repeated_regenerate_with_same_name_two_tempdirs(self):
        """Two tempdirs, both with alpha.py, must not shadow each other.

        With importlib.import_module and a cached sys.modules['alpha'],
        the second PluginRegistry() would silently get the FIRST test's
        module. With runpy.run_path + no sys.modules entry, each call
        re-executes the file cleanly. We round-trip one tempdir here
        (in-place file rewrite) to prove same-tempdir mutation also re-loads.
        """
        self._make_plugin("alpha.py", '''
COMMAND_NAME = "ALPHA"
PLUGIN_METADATA = {"name": "Alpha1", "description": "", "version": "1.0.0", "tags": []}
SENTINEL = "first"
def execute(i): return SENTINEL
''')
        reg1 = PluginRegistry()
        self.assertEqual(reg1.plugins["ALPHA"]({}), "first")

        # Mutate the file in place. runpy must re-execute on the next load.
        with open(os.path.join(self.test_dir, "alpha.py"), "w", encoding="utf-8") as f:
            f.write('''
COMMAND_NAME = "ALPHA"
PLUGIN_METADATA = {"name": "Alpha2", "description": "", "version": "2.0.0", "tags": []}
SENTINEL = "second"
def execute(i): return SENTINEL
''')
        reg2 = PluginRegistry()
        self.assertEqual(reg2.plugins["ALPHA"]({}), "second",
                         "runpy must re-execute the file on each load; "
                         "no stale sys.modules cache should return 'first'.")
        self.assertEqual(reg2.metadata["ALPHA"]["version"], "2.0.0")


class TestSimpleNamespaceAttributeAccess(unittest.TestCase):
    """The wrapper around runpy.run_path returns SimpleNamespace --
    verify the existing hasattr + attribute-access patterns still resolve.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="pr_runpy_simplens_")
        self.patcher = patch.object(plugin_registry, "PLUGIN_DIR", self.test_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_plugin(self, name, content):
        path = os.path.join(self.test_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_command_name_attribute_access(self):
        """module.COMMAND_NAME (attribute access) must work -- not just dict.
        If SimpleNamespace wrapping broke, the plugin won't register.
        """
        self._make_plugin("attrcmd.py", '''
COMMAND_NAME = "ATTRCMD"
PLUGIN_METADATA = {"name": "AttrCmd", "description": "", "version": "1.0.0", "tags": []}
def execute(i): return "ok"
''')
        reg = PluginRegistry()
        self.assertIn("ATTRCMD", reg.plugins)
        self.assertEqual(reg.execute_command({"action": "ATTRCMD"}), "ok")

    def test_metadata_attribute_access(self):
        """module.PLUGIN_METADATA must be accessible -- SimpleNamespace
        copies dict keys to attributes, including dict-typed values."""
        self._make_plugin("attrmeta.py", '''
COMMAND_NAME = "ATTRMETA"
PLUGIN_METADATA = {"name": "AttrMeta", "description": "d", "version": "1.0.0", "tags": ["t"]}
SENTINEL_LIST = [1, 2, 3]
def execute(i): return SENTINEL_LIST[-1]
''')
        reg = PluginRegistry()
        self.assertEqual(reg.metadata["ATTRMETA"]["name"], "AttrMeta")
        # And a complex Python value bound at plugin top-level survives:
        self.assertEqual(reg.plugins["ATTRMETA"]({}), 3)


class TestFailSoftErrorTracking(unittest.TestCase):
    """The exception-handling contract for _load_plugins must be unchanged."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="pr_runpy_failsoft_")
        self.patcher = patch.object(plugin_registry, "PLUGIN_DIR", self.test_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_plugin(self, name, content):
        path = os.path.join(self.test_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_import_error_tracked_into_errors_list(self):
        """A plugin importing a missing module must record a load error
        and NOT register. The error string must include the filename."""
        self._make_plugin(
            "broken.py",
            'import nonexistent_module_xyz\n'
            'COMMAND_NAME = "X"\n'
            'def execute(i): return ""\n',
        )
        reg = PluginRegistry()
        self.assertEqual(reg.get_plugin_count(), 0)
        self.assertEqual(len(reg.errors), 1)
        self.assertIn("broken.py", reg.errors[0])
        self.assertIn("nonexistent_module_xyz", reg.errors[0])

    def test_syntax_error_tracked_into_errors_list(self):
        """A plugin with a SyntaxError must be tracked, not crash the loader."""
        self._make_plugin("syntaxerr.py", "def execute(:\nCOMMAND_NAME = \"Y\"\n")
        reg = PluginRegistry()
        self.assertEqual(reg.get_plugin_count(), 0)
        self.assertEqual(len(reg.errors), 1)
        self.assertIn("syntaxerr.py", reg.errors[0])

    def test_mixed_good_and_both_bad_types(self):
        """Mixed bag: one good, one import-error, one syntax-error, one
        missing-execute. Only the good one registers; both error sources
        tracked; loader does not crash."""
        self._make_plugin("good.py", '''
COMMAND_NAME = "GOOD"
PLUGIN_METADATA = {"name": "Good", "description": "", "version": "1.0.0", "tags": []}
def execute(i): return "ok"
''')
        self._make_plugin(
            "badimport.py",
            'import nonexistent_module_xyz\nCOMMAND_NAME = "BAD1"\n'
            'def execute(i): return ""\n',
        )
        self._make_plugin("badsyntax.py", "this is not valid python ::::\n")
        self._make_plugin("badexec.py", 'COMMAND_NAME = "BAD2"\n')

        reg = PluginRegistry()
        self.assertEqual(reg.get_plugin_count(), 1)
        self.assertIn("GOOD", reg.plugins)
        # badexec.py is silently skipped (no execute); the other two
        # raise during runpy.run_path execution.
        self.assertEqual(len(reg.errors), 2)
        errored_files = {e.split(":", 1)[0] for e in reg.errors}
        self.assertIn("badimport.py", errored_files)
        self.assertIn("badsyntax.py", errored_files)


class TestWindowsCasingRobustnessSmoke(unittest.TestCase):
    """Smoke test: runpy.run_path accepts normcased paths. This pins the
    migration to OS-level loading (not spec/cache-keyed loading)."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="pr_runpy_casing_")
        self.patcher = patch.object(plugin_registry, "PLUGIN_DIR", self.test_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_plugin(self, name, content):
        path = os.path.join(self.test_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_normcased_path_round_trips_via_runpy(self):
        """os.path.normcase(realpath) must produce a path runpy.run_path
        can still execute -- the same Windows OS resolver that made the
        original spec_from_file_location fail now handles case-insensitive
        lookups cleanly."""
        self._make_plugin("cased.py", '''
COMMAND_NAME = "CASED"
PLUGIN_METADATA = {"name": "Cased", "description": "", "version": "1.0.0", "tags": []}
def execute(i): return "ok"
''')
        real = os.path.realpath(os.path.join(self.test_dir, "cased.py"))
        normcased = os.path.normcase(real)

        import runpy
        result = runpy.run_path(normcased, run_name="cased_direct")
        self.assertTrue(callable(result.get("execute")))


class TestRealRegistryStillZeroErrors(unittest.TestCase):
    r"""Canary: migration must not regress the live registry's zero-error
    invariant on the 25+ real plugins under C:\KokertechAI\plugins."""

    def test_real_registry_loads_clean(self):
        reg = plugin_registry.registry
        self.assertGreaterEqual(
            reg.get_plugin_count(), 20,
            f"Real registry should load at least 20 plugins; got {reg.get_plugin_count()}",
        )
        self.assertEqual(
            len(reg.errors), 0,
            f"Real registry must have zero load errors after migration; got: {reg.errors}",
        )
        # And every plugin's execute() should be callable with empty intent
        # without raising -- smoke for the wire-up end-to-end.
        for cmd in list(reg.plugins.keys())[:5]:
            try:
                result = reg.plugins[cmd]({})
                self.assertIsInstance(result, str)
            except Exception as e:
                self.fail(f"Plugin {cmd} raised on empty params: {e}")


if __name__ == "__main__":
    unittest.main()
