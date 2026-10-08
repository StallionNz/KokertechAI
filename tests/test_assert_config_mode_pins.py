#!/usr/bin/env python3
"""Unit tests for ``assert_config_mode_pins.py`` pure-function finders.

The gate (wired as the CONFIG-MODE-PINS pre-flight slot) enforces: every
setUp/setUpClass-level ``patch.dict(<CONFIG target>, ...)`` call in
tests/test_*.py must either pass ``clear=True`` (wipes to code defaults,
deterministic) or include BOTH ``freeform_mode`` and ``mock_mode`` as
literal keys in the overrides dict.  Rationale + incident narrative live
in the gate script's docstring and the known-failures status log (2026-09-27).

These tests exercise the AST finders directly against in-memory source
snippets via ``ast.parse`` + ``find_config_patch_violations`` on a
temporary directory -- no production test files are touched, so the
tests are order-independent and cheap.

Coverage matrix:

    - clean: pins both mode keys (gate passes)
    - clean: clear=True wipes to defaults (gate passes)
    - violation: missing one pin (the incident's exact shape)
    - violation: missing both pins
    - violation: non-literal overrides (unverifiable -> flagged)
    - violation: {**other} dict unpacking (unverifiable -> flagged)
    - clean: non-CONFIG patch.dict targets ignored (os.environ, other dicts)
    - clean: method-level patches out of scope (setUp only)
    - clean: setUpClass covered; helper methods ignored
    - clean: mock.patch.dict spelling accepted
    - clean: Attribute-target CONFIG accepted (config.CONFIG)
    - clean: empty overrides dict still requires pins (no free pass)
    - wrapper: rc 0 + summary text on clean tree; rc 1 + diagnostic on offenders
"""
# ``assert_config_mode_pins`` is importable because tests/conftest.py adds
# the ``scripts/`` directory to sys.path.  No local sys.path boilerplate.
import tempfile
import textwrap
from pathlib import Path

import assert_config_mode_pins as GATE


def _violations_for(source: str):
    """Parse an in-memory test-file snippet and return gate violations."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "test_fixture.py").write_text(textwrap.dedent(source), encoding="utf-8")
        return GATE.find_config_patch_violations(tmp_path)


def _first_reason(violations):
    return violations[0]["reason"] if violations else ""


class TestCleanPatches:
    """Patches that satisfy the gate must not be flagged."""

    def test_both_mode_pins_pass(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def setUp(self):
                    self._cfg_patcher = patch.dict(
                        "config.CONFIG",
                        {"chat_history_enabled": True, "freeform_mode": False, "mock_mode": False},
                        clear=False,
                    )
                    self._cfg_patcher.start()
            """
        )
        assert violations == []

    def test_clear_true_passes(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def setUp(self):
                    self._cfg_patcher = patch.dict(
                        "config.CONFIG", {"chat_history_enabled": True}, clear=True
                    )
                    self._cfg_patcher.start()
            """
        )
        assert violations == []

    def test_mock_module_spelling_accepted(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def setUp(self):
                    self._cfg_patcher = mock.patch.dict(
                        "config.CONFIG",
                        {"freeform_mode": False, "mock_mode": True},
                        clear=False,
                    )
                    self._cfg_patcher.start()
            """
        )
        assert violations == []

    def test_attribute_target_accepted(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def setUp(self):
                    with patch.dict(config.CONFIG, {"freeform_mode": False, "mock_mode": False}):
                        pass
            """
        )
        assert violations == []

    def test_setUpClass_is_scanned(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    cls._cfg_patcher = patch.dict(
                        "config.CONFIG", {"freeform_mode": False, "mock_mode": False}
                    )
                    cls._cfg_patcher.start()
            """
        )
        assert violations == []

    def test_non_config_targets_ignored(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def setUp(self):
                    self._env_patcher = patch.dict("os.environ", {"PATH": "/tmp"})
                    self._env_patcher.start()
                    self._other_patcher = patch.dict("some_module.REGISTRY", {"x": 1})
                    self._other_patcher.start()
            """
        )
        assert violations == []

    def test_method_level_patches_out_of_scope(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                @patch.dict("config.CONFIG", {"freeform_mode": False})
                def test_partial_pin_on_method(self):
                    assert True

                def test_inline_with_block(self):
                    with patch.dict("config.CONFIG", {"freeform_mode": False}):
                        assert True
            """
        )
        assert violations == []

    def test_helper_methods_ignored(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def _make_cfg(self):
                    return patch.dict("config.CONFIG", {"freeform_mode": False})

                def setUp(self):
                    self._cfg_patcher = patch.dict(
                        "config.CONFIG", {"freeform_mode": False, "mock_mode": False}
                    )
                    self._cfg_patcher.start()
            """
        )
        assert violations == []


class TestFlaggedPatches:
    """Patches that violate the gate must be flagged with a diagnosis."""

    def test_missing_single_pin_flagged(self):
        # The 2026-09-27 incident shape: one mode key pinned, one missing.
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def setUp(self):
                    self._cfg_patcher = patch.dict(
                        "config.CONFIG", {"freeform_mode": False, "model_name": "x"}, clear=False
                    )
                    self._cfg_patcher.start()
            """
        )
        assert len(violations) == 1
        assert violations[0]["missing"] == ["mock_mode"]
        assert "app_settings.json" in _first_reason(violations)

    def test_missing_both_pins_flagged(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def setUp(self):
                    self._cfg_patcher = patch.dict(
                        "config.CONFIG", {"chat_history_enabled": True}, clear=False
                    )
                    self._cfg_patcher.start()
            """
        )
        assert len(violations) == 1
        assert violations[0]["missing"] == ["freeform_mode", "mock_mode"]

    def test_non_literal_overrides_flagged(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def setUp(self):
                    overrides = {"freeform_mode": False, "mock_mode": False}
                    self._cfg_patcher = patch.dict("config.CONFIG", overrides, clear=False)
                    self._cfg_patcher.start()
            """
        )
        assert len(violations) == 1
        assert "not a fully-literal dict" in _first_reason(violations)

    def test_dict_unpacking_flagged(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def setUp(self):
                    self._cfg_patcher = patch.dict(
                        "config.CONFIG", {**base_overrides, "mock_mode": False}, clear=False
                    )
                    self._cfg_patcher.start()
            """
        )
        assert len(violations) == 1
        assert "not a fully-literal dict" in _first_reason(violations)

    def test_empty_overrides_flagged(self):
        # An empty dict is a literal dict with no pins: still a leak.
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def setUp(self):
                    self._cfg_patcher = patch.dict("config.CONFIG", {}, clear=False)
                    self._cfg_patcher.start()
            """
        )
        assert len(violations) == 1
        assert violations[0]["missing"] == ["freeform_mode", "mock_mode"]

    def test_offender_location_is_attributed(self):
        violations = _violations_for(
            """
            class TestThing(unittest.TestCase):
                def setUp(self):
                    self._cfg_patcher = patch.dict(
                        "config.CONFIG", {"chat_history_enabled": True}, clear=False
                    )
                    self._cfg_patcher.start()
            """
        )
        assert violations[0]["location"] == "TestThing.setUp"
        assert violations[0]["line"] > 0
        assert violations[0]["file"].endswith("test_fixture.py")


class TestGateWrapper:
    """run_gate / main behave per the pre-flight gate contract."""

    def test_run_gate_clean_tree_rc0(self):
        rc, violations = GATE.run_gate(GATE.TESTS_DIR)
        assert rc == 0
        assert violations == []

    def test_main_clean_prints_summary(self, capsys):
        # Text mode: human-readable summary line.
        assert GATE.main([]) == 0
        assert "clean" in capsys.readouterr().out
        # JSON mode: machine-readable report with the structured fields.
        assert GATE.main(["--json"]) == 0
        assert '"offender_count": 0' in capsys.readouterr().out

    def test_main_offender_rc1_and_diagnostic(self, capsys, tmp_path):
        (tmp_path / "test_bad.py").write_text(
            textwrap.dedent(
                """
                class TestBad(unittest.TestCase):
                    def setUp(self):
                        self._cfg_patcher = patch.dict(
                            "config.CONFIG", {"chat_history_enabled": True}, clear=False
                        )
                        self._cfg_patcher.start()
                """
            ),
            encoding="utf-8",
        )
        rc = GATE.main([str(tmp_path)])
        captured = capsys.readouterr()
        assert rc == 1
        assert "1 offender(s)" in captured.out
        assert "missing pins: freeform_mode, mock_mode" in captured.out
        assert "Fix:" in captured.out

    def test_main_missing_tests_dir_rc1(self, capsys, tmp_path):
        rc = GATE.main([str(tmp_path / "nope")])
        captured = capsys.readouterr()
        assert rc == 1
        assert "tests directory not found" in captured.out
