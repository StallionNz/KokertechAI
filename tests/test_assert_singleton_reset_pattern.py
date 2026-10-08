"""Tests for scripts/assert_singleton_reset_pattern.py (Sprint 17 R12).

Mirrors ``tests/test_assert_section11_render_clean.py`` pattern: class-based
test organization, one class per check category, tmpdir staging for
negative-case violations (NEVER mutate the real project files).

Each test exercises one of the gate's 3 pure-function finders against a
synthetic project layout. The staging helper ``_make_fake_project`` builds
a tmpdir with a minimal services/ + conftest.py + tests/ structure; the
gate operates purely on the directory contents (no system imports).
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GATE_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "assert_singleton_reset_pattern.py"


def _load_gate_module():
    """Import the gate script as a module (avoids sys.path / PYTHONPATH dance).

    Uses importlib.util.spec_from_file_location (same pattern as the
    canonical workaround in TestConftestRegistryWireup for the dual-conftest
    cache collision -- would be ironic if THIS test used the bad pattern).
    """
    spec = importlib.util.spec_from_file_location(
        "_singleton_gate_for_test_only",
        str(GATE_SCRIPT_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_fake_project(
    td_path: Path,
    *,
    with_singleton: bool = True,
    with_reset_helper: bool = True,
    with_conftest_wireup: bool = True,
    with_conftest_import_violation: bool = False,
) -> None:
    """Stage a minimal fake project layout for the gate to scan.

    Structure:
      td_path/services/fake_module.py  -- contains (or lacks) singleton + helper
      td_path/conftest.py             -- contains (or lacks) the wire-up
      td_path/tests/_probe.py         -- empty

    Flags control which violations are present (each is independent).
    """
    services_dir = td_path / "services"
    services_dir.mkdir(exist_ok=True)

    module_text = '"""fake testing module."""\n'
    if with_singleton:
        module_text += "_FAKE_SINGLETON = None\n"
    if with_reset_helper:
        module_text += (
            "def _reset_fake_for_tests():\n"
            "    global _FAKE_SINGLETON\n"
            "    _FAKE_SINGLETON = None\n"
        )
    elif not with_reset_helper and with_singleton:
        # Singleton is here but reset helper is intentionally omitted.
        pass
    (services_dir / "fake_module.py").write_text(module_text, encoding="utf-8")

    if with_conftest_wireup:
        conftest_text = (
            "def _reset_services_registry():\n"
            "    from services.fake_module import _reset_fake_for_tests\n"
            "    _reset_fake_for_tests()\n\n"
            "def _reset_all_shared_state():\n"
            "    _reset_services_registry()\n"
        )
    else:
        # Empty conftest body -- no wire-up invocation at all.
        conftest_text = (
            "def _reset_services_registry():\n"
            "    pass\n\n"
            "def _reset_all_shared_state():\n"
            "    pass\n"
        )
    (td_path / "conftest.py").write_text(conftest_text, encoding="utf-8")

    # tests/ dir with optional dual-conftest-file-trap violation
    tests_dir = td_path / "tests"
    tests_dir.mkdir(exist_ok=True)
    if with_conftest_import_violation:
        probe_text = (
            "import unittest\n"
            "from conftest import _reset_services_registry  # VIOLATION\n"
        )
    else:
        probe_text = "import unittest\n"
    (tests_dir / "_probe.py").write_text(probe_text, encoding="utf-8")


class TestCheck1SingletonWithoutResetHelper(unittest.TestCase):
    """Check 1: every module-level singleton has a paired `_reset_X_for_tests()` helper IN THE SAME FILE."""

    def setUp(self):
        self.mod = _load_gate_module()

    def test_clean_when_singleton_and_helper_both_present(self):
        """Positive case: matching the canonical pattern (_X_SINGLETON + _reset_X_for_tests)."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _make_fake_project(td_path, with_singleton=True, with_reset_helper=True)
            offenders = self.mod.find_singleton_without_reset_helper(td_path)
            self.assertEqual(
                offenders, [],
                f"positive case falsely flagged: {offenders}",
            )

    def test_flags_when_singleton_present_without_reset_helper(self):
        """Negative case: singleton declared without paired helper flags a violation."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _make_fake_project(td_path, with_singleton=True, with_reset_helper=False)
            offenders = self.mod.find_singleton_without_reset_helper(td_path)
            self.assertGreater(
                len(offenders), 0,
                "expected violation when singleton declared without helper -- got empty list",
            )
            self.assertTrue(
                any("_FAKE_SINGLETON" in r[2] for r in offenders),
                f"expected _FAKE_SINGLETON in offense reasons: {[r[2] for r in offenders]}",
            )

    def test_clean_when_no_singleton_at_all(self):
        """Edge case: file without any singleton should produce no offenders."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _make_fake_project(td_path, with_singleton=False, with_reset_helper=False)
            offenders = self.mod.find_singleton_without_reset_helper(td_path)
            self.assertEqual(
                offenders, [],
                f"edge case (no singleton) produced false positives: {offenders}",
            )


class TestCheck2ResetHelperWithoutConftestWireup(unittest.TestCase):
    """Check 2: every `_reset_X_for_tests()` definition is auto-invoked from conftest chain."""

    def setUp(self):
        self.mod = _load_gate_module()

    def test_clean_when_helper_invoked_in_conftest(self):
        """Positive case: conftest imports + invokes the helper."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _make_fake_project(
                td_path, with_singleton=True, with_reset_helper=True,
                with_conftest_wireup=True,
            )
            offenders = self.mod.find_reset_helper_without_conftest_wireup(td_path)
            self.assertEqual(
                offenders, [],
                f"positive case (wire-up present) falsely flagged: {offenders}",
            )

    def test_flags_when_helper_defined_but_not_wired(self):
        """Negative case: helper definition present but conftest doesn't call it."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _make_fake_project(
                td_path, with_singleton=True, with_reset_helper=True,
                with_conftest_wireup=False,  # no `_reset_fake_for_tests(` in conftest
            )
            offenders = self.mod.find_reset_helper_without_conftest_wireup(td_path)
            self.assertGreater(
                len(offenders), 0,
                "expected violation when helper defined but not wired in conftest",
            )
            self.assertTrue(
                any("_reset_fake_for_tests" in r[2] for r in offenders),
                f"expected `_reset_fake_for_tests` in offense reasons: {[r[2] for r in offenders]}",
            )


class TestCheck3DualConftestImportViolations(unittest.TestCase):
    """Check 3: no test uses `from conftest import X` / `import conftest as X`."""

    def setUp(self):
        self.mod = _load_gate_module()

    def test_flags_from_conftest_import_in_test(self):
        """Negative case: tests/_probe.py uses `from conftest import X` (trap)."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _make_fake_project(
                td_path, with_singleton=False, with_reset_helper=False,
                with_conftest_import_violation=True,
            )
            offenders = self.mod.find_dual_conftest_import_violations(td_path)
            self.assertGreater(
                len(offenders), 0,
                "expected violation when test uses `from conftest import ...`",
            )

    def test_clean_when_no_dual_conftest_import(self):
        """Positive case: tests don't use unqualified `conftest` import."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _make_fake_project(
                td_path, with_singleton=False, with_reset_helper=False,
                with_conftest_import_violation=False,
            )
            offenders = self.mod.find_dual_conftest_import_violations(td_path)
            self.assertEqual(
                offenders, [],
                f"positive case falsely flagged: {offenders}",
            )


class TestGateScriptExitCodes(unittest.TestCase):
    """End-to-end smoke: the gate script itself exits 0 on clean project / 1 on offenders."""

    def test_clean_against_current_project_root(self):
        """The current project root IS compliant: should exit 0."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(GATE_SCRIPT_PATH)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        # The current project DOES comply (per Sprint 17 R10 closeout):
        # _FACTORY_SINGLETON paired with _reset_factory_for_tests + conftest wire-up.
        # No `from conftest import X` in tests.
        self.assertEqual(
            result.returncode, 0,
            f"current project root should pass gate (exit 0); got exit "
            f"{result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )

    def test_offending_project_root_exits_1(self):
        """A fake project with a violating singleton exits 1."""
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _make_fake_project(
                td_path, with_singleton=True, with_reset_helper=False,
            )
            result = subprocess.run(
                [sys.executable, str(GATE_SCRIPT_PATH)],
                cwd=str(td_path),
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode, 1,
                f"violating project root should exit 1; got exit "
                f"{result.returncode}\nstdout: {result.stdout}",
            )
            self.assertTrue(
                result.returncode == 1 or "FAILED" in result.stdout,
                f"Expected exit code 1 or 'FAILED' in stdout; got exit "
                f"{result.returncode}\nstdout: {result.stdout}",
            )


if __name__ == "__main__":
    unittest.main()
