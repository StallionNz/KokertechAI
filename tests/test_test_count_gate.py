"""Contract pin-tests for the test-count CI gate (tests/_baseline_test_count.json).

These tests do NOT exercise the gate logic itself -- that runs implicitly on
every full pytest invocation via pytest_collection_finish in
conftest.py. They pin the wiring: a future refactor that strips the
addini registration or removes the hook should fail THIS test, leaving a
discoverable trail instead of a silent loss of CI protection.

Origin: defense-in-depth against the kind of accidental test-file deletion
that the cleanup sweep of tabs/*.py.pre_* (Jun 2026) could have hidden.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


GATE_MARKERS = (
    "pytest_collection_finish",        # the hook
    "test_drop_threshold",              # the ini option name
    "KOKERTECH_UPDATE_TEST_BASELINE",   # the refresh env var
    "_baseline_test_count.json",        # the baseline filename
)


class TestGateWiring(unittest.TestCase):
    """conftest.py MUST keep the gate hook + ini + env-var + filename wired.

    A plain source-text check is more robust across pytest versions than
    introspecting pluginmanager.get_hookcallers() (which changes shape
    between pytest 7/8/9 and across plugin registrations).
    """

    PROJECT_ROOT = Path(__file__).resolve().parent.parent

    def test_conftest_contains_all_gate_markers(self):
        conftest = self.PROJECT_ROOT / "conftest.py"
        self.assertTrue(
            conftest.exists(),
            f"conftest.py missing at {conftest}; cannot verify gate wiring.",
        )
        src = conftest.read_text(encoding="utf-8")
        missing = [m for m in GATE_MARKERS if m not in src]
        self.assertEqual(
            missing, [],
            f"conftest.py missing gate markers: {missing!r}. "
            f"The test-count CI gate has been DEWIRED. Restore the "
            f" hook AND the "
            f" ini registration AND the "
            f" env-var reference AND "
            f"the  filename to make this "
            f"contract pass again.",
        )


class TestGateIniOption(unittest.TestCase):
    """pytest.ini must declare test_drop_threshold as a non-negative int."""

    PROJECT_ROOT = Path(__file__).resolve().parent.parent

    def test_threshold_ini_declared_as_nonneg_int(self):
        ini = self.PROJECT_ROOT / "pytest.ini"
        self.assertTrue(ini.exists(), f"pytest.ini missing at {ini}")
        src = ini.read_text(encoding="utf-8")
        line = next(
            (l.strip() for l in src.splitlines()
             if l.strip().startswith("test_drop_threshold")),
            None,
        )
        self.assertIsNotNone(
            line,
            "pytest.ini must declare . "
            "Add a line  to [pytest].",
        )
        m = re.match(r"^test_drop_threshold\s*=\s*(-?\d+)\s*$", line)
        self.assertIsNotNone(
            m,
            f" must be a bare integer, got {line!r}",
        )
        val = int(m.group(1))
        self.assertGreaterEqual(
            val, 0,
            f" must be >= 0 (got {val}). "
            "Negative thresholds would always trip and are not supported.",
        )


class TestBaselineLocation(unittest.TestCase):
    """The baseline lives at tests/_baseline_test_count.json."""

    PROJECT_ROOT = Path(__file__).resolve().parent.parent

    def test_baseline_path_grepable(self):
        conftest = self.PROJECT_ROOT / "conftest.py"
        src = conftest.read_text(encoding="utf-8")
        self.assertIn(
            "_baseline_test_count.json", src,
            "conftest.py must reference the baseline filename "
            "() so grep finds the canonical "
            "location holistically.",
        )
        self.assertIn(
            "tests", src,
            "conftest.py gate logic must reference the  directory "
            "so the baseline path tests/_baseline_test_count.json is "
            "self-documenting in source.",
        )


if __name__ == "__main__":
    unittest.main()
