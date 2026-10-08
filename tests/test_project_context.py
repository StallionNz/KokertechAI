"""tests.test_project_context -- Sprint 18 AC #4 closeout tests (2026-07-19).

16 tests across 7 classes. Drives ``cli/project_context.detect()`` and
``ProjectContext.validate()`` via ``tmp_path`` fixtures.

ANTI-FRAGILITY: every test that writes a marker file uses ``tmp_path``
(pytest built-in) -- no cross-test fixture pollution possible because
detection is a pure function over a directory listing. Per the Sprint 17
R12 Project-Wide Singleton Convention (KNOWLEDGE.md Section 10), this test
file does NOT need a conftest wire-up: ``cli/project_context.py`` is a
stateless helper, not a singleton-bearing module.

REGRESSION GUARD: ``TestProjectContextValidation`` locks the
``ProjectContext.validate()`` contract (absolute path + directory existence
-- raises ``ValueError`` on violation). The sibling sprint_45 P0 case that
triggered this guarantee (a Sprint 17 deferred follow-up) was a passed
RELATIVE path that silently bypassed detection -- the validator now rejects
it before any os.listdir walk.

Sprint 18 R1 Closeout target: closes Sprint 18 AC #4 (Project context
detection). Companion str_replace in docs/IMPLEMENTATION_PLAN.md flips the
Sprint 18 AC #4 bullet from ``[ ]`` to ``[x]`` with cross-link to the
Sprint 18 R1 Closeout CHANGELOG entry.

Cross-link: docs/CHANGELOG.md Sprint 18 R1 Closeout entry.
"""

from __future__ import annotations

import json
import os

import pytest

from cli.project_context import ProjectContext, detect


# ══════════════════════════════════════════════════════════════════════
# Language detection (3 test cases)
# ══════════════════════════════════════════════════════════════════════


class TestDetectPythonProject:
    """ANTI-FRAGILITY: ``tmp_path`` is per-test; ``Path.write_text`` constructs
    the workspace fixture without filesystem pollution. Detection is a pure
    function over ``os.listdir(root_path)``, so no module-level cache state
    can leak across tests (no singleton, no conftest wire-up needed).
    """

    def test_requirements_txt_detects_python(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("requests\npytest\n")
        ctx = detect(str(tmp_path))
        assert ctx.language == "python", f"expected 'python', got {ctx.language!r}"

    def test_pyproject_toml_detects_python(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        ctx = detect(str(tmp_path))
        assert ctx.language == "python"

    def test_setup_py_detects_python(self, tmp_path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\n")
        ctx = detect(str(tmp_path))
        assert ctx.language == "python"


# ══════════════════════════════════════════════════════════════════════
# JavaScript / TypeScript (tsconfig wins)
# ══════════════════════════════════════════════════════════════════════


class TestDetectJavaScriptAndTypescript:
    def test_package_json_alone_is_javascript(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"name": "x"}))
        ctx = detect(str(tmp_path))
        assert ctx.language == "javascript"

    def test_tsconfig_overrides_package_json(self, tmp_path):
        # TypeScript projects always carry a package.json; tsconfig is the
        # distinguishing marker. Detection must prefer TypeScript when both
        # files are present at workspace root.
        (tmp_path / "package.json").write_text(json.dumps({"name": "x"}))
        (tmp_path / "tsconfig.json").write_text("{}")
        ctx = detect(str(tmp_path))
        assert ctx.language == "typescript"


# ══════════════════════════════════════════════════════════════════════
# Other languages (Ruby, Go, Rust, PHP markers)
# ══════════════════════════════════════════════════════════════════════


class TestDetectRubyAndOtherLanguages:

    def test_gemfile_detects_ruby(self, tmp_path):
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n")
        ctx = detect(str(tmp_path))
        assert ctx.language == "ruby"

    def test_go_mod_detects_go(self, tmp_path):
        (tmp_path / "go.mod").write_text("module x\n\ngo 1.21\n")
        ctx = detect(str(tmp_path))
        assert ctx.language == "go"

    def test_cargo_toml_detects_rust(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
        ctx = detect(str(tmp_path))
        assert ctx.language == "rust"


# ══════════════════════════════════════════════════════════════════════
# Frameworks (via package.json)
# ══════════════════════════════════════════════════════════════════════


class TestDetectFrameworks:

    def test_next_detected_via_dependency(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"next": "13.0.0"},
        }))
        ctx = detect(str(tmp_path))
        assert "next" in ctx.frameworks

    def test_react_detected_via_dependency(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({
            "dependencies": {"react": "18.0.0"},
        }))
        ctx = detect(str(tmp_path))
        assert "react" in ctx.frameworks

    def test_vite_detected_via_config_only(self, tmp_path):
        # ANTI-FRAGILITY: vite.config.ts (without an explicit "vite" dep)
        # is still detected -- the marker-file pattern is "OR" on (dep,
        # config-file) so hand-rolled configs without a vite dep don't
        # silently bypass detection.
        (tmp_path / "package.json").write_text(json.dumps({"name": "x"}))
        (tmp_path / "vite.config.ts").write_text("export default {}\n")
        ctx = detect(str(tmp_path))
        assert "vite" in ctx.frameworks


# ══════════════════════════════════════════════════════════════════════
# Conventions (multiple detection per workspace)
# ══════════════════════════════════════════════════════════════════════


class TestDetectConventions:

    def test_gitignore_is_a_convention(self, tmp_path):
        (tmp_path / ".gitignore").write_text("__pycache__\n")
        ctx = detect(str(tmp_path))
        assert "gitignore" in ctx.conventions

    def test_multiple_conventions_accumulate(self, tmp_path):
        (tmp_path / ".gitignore").write_text("")
        (tmp_path / ".editorconfig").write_text("root = true\n")
        (tmp_path / ".prettierrc").write_text("{}")
        ctx = detect(str(tmp_path))
        # AI-friendly insight: frozenset supports >= (superset comparison)
        assert ctx.conventions >= frozenset({"gitignore", "editorconfig", "prettier"})


# ══════════════════════════════════════════════════════════════════════
# Empty workspace + defensive handling
# ══════════════════════════════════════════════════════════════════════


class TestDetectEmptyAndDefensive:

    def test_no_markers_returns_none_language(self, tmp_path):
        ctx = detect(str(tmp_path))
        assert ctx.language is None
        assert ctx.frameworks == frozenset()
        assert ctx.conventions == frozenset()

    def test_root_path_preserved_in_result(self, tmp_path):
        ctx = detect(str(tmp_path))
        # ``str(tmp_path)`` normalises trailing slashes; equality is preserved
        # because tmp_path is already a stable absolute path.
        assert ctx.root_path == str(tmp_path)

    def test_malformed_package_json_does_not_crash(self, tmp_path):
        # Garbage JSON should not raise -- language still detected from the
        # filename marker, but framework set stays empty because the JSON
        # dep parse failed.
        (tmp_path / "package.json").write_text("{ this is malformed json")
        ctx = detect(str(tmp_path))
        assert ctx.language == "javascript"   # filename marker still wins
        assert ctx.frameworks == frozenset()

    def test_wrong_shape_dependencies_does_not_crash(self, tmp_path):
        """REGRESSION GUARD for the crash bug surfaced in Sprint 18 R1 Round 2
        code-review: valid-JSON-but-wrong-shape ``package.json`` with
        ``"dependencies": "not-a-dict"`` (a literal STRING instead of an
        object) MUST NOT crash ``detect()`` via ``TypeError: 'str' object is
        not a mapping`` raised by ``{**pkg.get("dependencies", {})}`` when
        the value is a string.

        The fix lives in ``cli/project_context.py`` (Sprint 18 R1 Round 2):
        (a) explicit ``isinstance(deps, dict)`` guard + coercion-to-``{}``
        before the dict unpacking, (b) belt-and-suspenders ``TypeError``
        extension on the existing ``except`` tuple covering framework
        detection. Without (a) the value passed to ``**`` would raise
        ``TypeError``; without (b) any *other* unhandled crash path in the
        framework-detection block (e.g. a malformed peer-deps dict) would
        propagate up to the caller. This test pins (a). The
        ``test_malformed_package_json_does_not_crash`` sibling pins the
        SYNTACTIC-JSON-malformed path (b's primary rationale).
        """
        (tmp_path / "package.json").write_text(
            json.dumps({"dependencies": "not-a-dict"})
        )
        ctx = detect(str(tmp_path))
        # Language detection still wins from the filename marker --
        # the crash would only affect framework detection.
        assert ctx.language == "javascript"
        # Framework set is empty because the wrong-shape "dependencies"
        # was coerced to {}, which contains no recognised framework keys.
        assert ctx.frameworks == frozenset()


# ══════════════════════════════════════════════════════════════════════
# Validation contract (REGRESSION GUARD)
# ══════════════════════════════════════════════════════════════════════


class TestProjectContextValidation:
    """REGRESSION GUARD for ``ProjectContext.validate()`` in
    ``cli/project_context.py`` (lines 65-78). The validator enforces the
    contract that ``root_path`` must be a non-empty absolute path to an
    ``os.path.isdir``-passing directory, raising ``ValueError`` on any
    violation.

    The guarantees the source upholds (and these tests pin):
      1. Empty-or-non-string path -> ``ValueError``
      2. Relative path -> ``ValueError``
      3. Non-existent path -> ``ValueError``
      4. Absolute-existing directory -> returns silently (no raise)

    If a refactor reverts any of these -- e.g. by removing the absolute
    check to "make tests easier" -- the corresponding ``ValueError`` test
    below will fire with a meaningful diagnostic. This is the boundary test
    for ``cli/agent.py`` (Sprint 18 AC #1 deferred) which will eventually
    pass detect() an unnormalised path; a buggy validator would silently
    let that through and ``os.listdir`` would then raise an unhelpful
    ``FileNotFoundError`` downstream.
    """

    def test_empty_string_root_path_raises_value_error(self):
        ctx = ProjectContext(root_path="")
        with pytest.raises(ValueError, match="non-empty"):
            ctx.validate()

    def test_relative_root_path_raises_value_error(self, tmp_path):
        # Strip the absolute prefix -- result is a relative path that must
        # be rejected by the validator.
        relative = os.path.basename(str(tmp_path))
        ctx = ProjectContext(root_path=relative)
        with pytest.raises(ValueError, match="absolute"):
            ctx.validate()

    def test_nonexistent_root_path_raises_value_error(self):
        # ANTI-FRAGILITY: select a platform-correct absolute path. On Windows,
        # Python's stdlib ``os.path.isabs`` AND ``pathlib.Path.is_absolute``
        # BOTH reject a POSIX-style "/foo" path because it lacks a drive letter.
        # Without this conditional, the test on Windows would emit
        # "must be absolute" instead of the expected "existing directory"
        # because the validator's first check (``os.path.isabs``) would fail
        # BEFORE the second check (``os.path.isdir``) could fire.
        if os.name == "nt":
            nonexistent = "C:\\nonexistent_path_should_not_exist_xyz_42"
        else:
            nonexistent = "/nonexistent_path_should_not_exist_xyz_42"
        ctx = ProjectContext(root_path=nonexistent)
        with pytest.raises(ValueError, match="existing directory"):
            ctx.validate()

    def test_absolute_existing_directory_passes(self, tmp_path):
        ctx = ProjectContext(root_path=str(tmp_path))
        ctx.validate()  # must NOT raise
