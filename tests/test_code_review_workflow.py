"""Tests for code_review_workflow (Sprint 17 #1.4).

Locks the FIRST real consumer of ``CodeIntelligenceFactory`` end-to-end:
  1. Boot-time ``register_workspace`` flow stores the pattern.
  2. ``review_file`` resolves the SAME cached singleton across calls.
  3. ``WorkspaceNotRegisteredError`` wraps the factory's KeyError cleanly.

Cross-file pollution guard: each test calls ``_reset_factory_for_tests``
in ``setUp`` so prior tests cannot leak a registered pattern into this
file. Conftest's ``_reset_services_registry`` already auto-wires this for
the ``get_services()`` side; the factory-singleton side needs this
explicit call because the factory is registered via ``register_instance``
(rather than the lazy-rebuild ``register(name, factory_fn, singleton=True)``
form), per KNOWLEDGE.md §10 CodeIntelligenceFactory documentation.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.code_intelligence_factory import _reset_factory_for_tests



def _make_tmpdir(tracked_list):
    d = tempfile.mkdtemp(prefix="code_review_test_")
    tracked_list.append(d)
    return Path(d)


def _cleanup_tmpdirs(tracked_list):
    import shutil
    for d in tracked_list:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


class TestCodeReviewWorkflow(unittest.TestCase):
    """Sprint 17 #1.4: workflow consumer end-to-end integration."""

    def setUp(self):
        _reset_factory_for_tests()
        self._tmpdirs = []

    def tearDown(self):
        _cleanup_tmpdirs(self._tmpdirs)
        _reset_factory_for_tests()

    # ─────────────────────────────────────────────────────────────────
    # Test 1: WorkspaceNotRegisteredError wrapping contract
    # ─────────────────────────────────────────────────────────────────

    def test_review_workspace_not_registered_raises(self):
        """Lock the WorkspaceNotRegisteredError wrap of factory KeyError.

        BEFORE: A bare KeyError from factory.resolve() left the agent
        loop guessing whether it missed the boot step or hit some other
        dict-miss. AFTER: WorkspaceNotRegisteredError carries explicit
        "call register_workspace() before review_file()" guidance.

        REGRESSION GUARD: This is the error-handling contract for the
        workflow module's public surface. If a future refactor drops the
        try/except KeyError wrap (e.g., to "simplify" by re-raising the
        bare exception), this test fires because WorkspaceNotRegisteredError
        is no longer in the MRO.
        """
        from code_review_workflow import (
            WorkspaceNotRegisteredError,
            review_file,
        )

        tmpdir = _make_tmpdir(self._tmpdirs)
        with self.assertRaises(WorkspaceNotRegisteredError) as ctx:
            review_file(workspace_path=str(tmpdir), filepath="nonexistent.py")
        # Use repr(str(tmpdir)) — the workflow module's !r-format on a
        # str input produces escaped-backslash repr (e.g. 'C:\\Users\\...'),
        # which repr(str(tmpdir)) reproduces verbatim. DO NOT simplify to
        # repr(tmpdir) — that returns WindowsPath('C:/foo') on Windows and
        # would break the assertion again.
        self.assertIn(repr(str(tmpdir)), str(ctx.exception))
        self.assertIn("register_workspace", str(ctx.exception))

    # ─────────────────────────────────────────────────────────────────
    # Test 2: Boot + review end-to-end happy path
    # ─────────────────────────────────────────────────────────────────

    def test_review_file_end_to_end_success(self):
        """Lock the boot -> resolve -> review flow with a real tmpdir + .py file.

        BEFORE: Each review_file() call would need a fresh
        CodeIntelligence(workspace=...) instantiation, duplicating
        path-anchoring logic per call site. AFTER: register_workspace()
        at boot + review_file() resolves the SAME cached singleton,
        removing the duplication.

        REGRESSION GUARD: This is the Sprint 17 #1.4 acceptance criterion
        itself -- "use the new CodeIntelligenceFactory to resolve
        workspace-aware CodeIntelligence instances in a real workflow
        step". If a future refactor bypasses the factory, the linter_errors
        list will still be populated (because read_file + run_linter still
        work) but the cache-singleton assertion in
        test_workflow_cache_singleton_resolution would fail. Both tests
        together lock the integration end-to-end.
        """
        from code_review_workflow import register_workspace, review_file

        tmpdir = _make_tmpdir(self._tmpdirs)
        target = tmpdir / "hello.py"
        target.write_text("def greet():\n    return 'hi'\n", encoding="utf-8")

        register_workspace(workspace_path=str(tmpdir), linter_cmd=None)
        result = review_file(workspace_path=str(tmpdir), filepath="hello.py")

        self.assertEqual(result.status, "success",
                         f"unexpected error: {result.error_msg!r}")
        self.assertEqual(
            result.file_content_length,
            len("def greet():\n    return 'hi'\n"),
        )
        self.assertIn("functions", result.ast_summary)
        self.assertEqual(len(result.ast_summary["functions"]), 1)
        self.assertEqual(result.ast_summary["functions"][0]["name"], "greet")
        self.assertIsInstance(result.linter_errors, list)

    # ─────────────────────────────────────────────────────────────────
    # Test 2b: Non-Python file skips analyze_ast
    # ─────────────────────────────────────────────────────────────────

    def test_review_file_skips_ast_for_non_python(self):
        """Lock the .py-only analyze_ast skip contract.

        BEFORE: A .txt or .md review would invoke analyze_ast and raise
        SyntaxError, breaking review_file for non-Python files. AFTER:
        review_file skips analyze_ast when ``filepath`` does not end in
        ``.py``, returning an empty ``ast_summary`` and status=success.

        REGRESSION GUARD: If a future refactor removes the
        ``if filepath.endswith(".py"):`` guard, this test fires because
        analyze_ast raises SyntaxError on non-Python text, propagating
        to ``result.status == "error"`` instead of ``success``.
        """
        from code_review_workflow import register_workspace, review_file

        tmpdir = _make_tmpdir(self._tmpdirs)
        (tmpdir / "README.md").write_text("# Heading\n\nSome prose.\n", encoding="utf-8")
        (tmpdir / "Makefile").write_text("all:\n\t@echo hi\n", encoding="utf-8")

        register_workspace(workspace_path=str(tmpdir), linter_cmd=None)
        for path in ("README.md", "Makefile"):
            result = review_file(workspace_path=str(tmpdir), filepath=path)
            self.assertEqual(
                result.status, "success",
                f"unexpected error on {path!r}: {result.error_msg!r}",
            )
            self.assertEqual(
                result.ast_summary, {},
                f"ast_summary must be empty for non-.py file {path!r}, "
                f"got: {result.ast_summary!r} -- the .py-only guard likely regressed.",
            )

    # ─────────────────────────────────────────────────────────────────
    # Test 2c: Linter-disabled returns empty errors + warning
    # ─────────────────────────────────────────────────────────────────

    def test_review_file_linter_disabled_returns_empty_errors(self):
        """Lock the linter_cmd=None contract: empty errors + warning string.

        BEFORE: When linter_cmd=None, run_linter returns
        ``{"warning": "linter disabled..."}`` -- the workflow must propagate
        this as empty linter_errors WITHOUT crashing. AFTER: empty list + status=success.

        REGRESSION GUARD: If a future refactor calls ``shutil.which(None)``
        (raises TypeError) or fails to handle the disabled-path, this test
        fires because result.status would be "error" or linter_errors
        would not be a list.
        """
        from code_review_workflow import register_workspace, review_file

        tmpdir = _make_tmpdir(self._tmpdirs)
        (tmpdir / "hello.py").write_text("x = 1\n", encoding="utf-8")

        register_workspace(workspace_path=str(tmpdir), linter_cmd=None)
        result = review_file(workspace_path=str(tmpdir), filepath="hello.py")

        self.assertEqual(
            result.status, "success",
            f"unexpected error with linter_cmd=None: {result.error_msg!r}",
        )
        self.assertEqual(
            result.linter_errors, [],
            f"linter_errors must be empty when linter_cmd=None, got: "
            f"{result.linter_errors!r}.",
        )

    # ─────────────────────────────────────────────────────────────────
    # Test 3: Cached-singleton resolution across calls
    # ─────────────────────────────────────────────────────────────────

    def test_workflow_cache_singleton_resolution(self):
        """Lock that review_file() resolves the SAME CodeIntelligence across calls.

        BEFORE: Two review_file() calls would create two separate
        CodeIntelligence instances (each with its own rg cache, audit
        log path, etc.). AFTER: factory.resolve() returns the cached
        singleton, so rg cache hits + audit entries land in the same
        per-instance JSONL file -- critical for post-mortem correlation.

        Locks Sprint 17 #1.4 acceptance criterion specifically: "resolves
        the same singleton for each review action". Verified by spying
        on CodeIntelligence.__init__ and asserting it's called exactly
        once for N review_file() calls.

        REGRESSION GUARD: If a future refactor reverts to direct
        CodeIntelligence() instantiation per call (defeating the
        factory's lazy-cache purpose), __init__ fires N times and the
        assertion fails. The test exposes the regression with a clear
        diagnostic message pointing at the factory bypass.
        """
        from services.code_intelligence import CodeIntelligence
        from code_review_workflow import register_workspace, review_file

        original_init = CodeIntelligence.__init__
        init_call_count = {"n": 0}

        def _spy_init(self, *args, **kwargs):
            init_call_count["n"] += 1
            return original_init(self, *args, **kwargs)

        CodeIntelligence.__init__ = _spy_init
        try:
            tmpdir = _make_tmpdir(self._tmpdirs)
            (tmpdir / "a.py").write_text("x = 1\n", encoding="utf-8")
            (tmpdir / "b.py").write_text("y = 2\n", encoding="utf-8")

            register_workspace(workspace_path=str(tmpdir), linter_cmd=None)

            r1 = review_file(workspace_path=str(tmpdir), filepath="a.py")
            r2 = review_file(workspace_path=str(tmpdir), filepath="b.py")
            r3 = review_file(workspace_path=str(tmpdir), filepath="a.py")

            for r in (r1, r2, r3):
                self.assertEqual(r.status, "success",
                                 f"unexpected error: {r.error_msg!r}")

            self.assertEqual(
                init_call_count["n"], 1,
                f"REGRESSION: CodeIntelligence.__init__ should fire exactly "
                f"once for 3 review_file() calls (factory caches the singleton), "
                f"but fired {init_call_count['n']} times. Likely a refactor "
                f"reverted to direct CodeIntelligence() instantiation, "
                f"bypassing the factory."
            )
        finally:
            CodeIntelligence.__init__ = original_init


class TestCodeReviewWorkflowCrossFile(unittest.TestCase):
    """Sprint 17 #1.4 cross-file pollution guard.

    Ensures the workflow module's singleton cache does not interfere with
    other tests that touch the same factory singleton -- this is the
    "conftest._reset_services_registry auto-wires _reset_factory_for_tests"
    invariant verified end-to-end.
    """

    def setUp(self):
        _reset_factory_for_tests()
        self._tmpdirs = []

    def tearDown(self):
        _cleanup_tmpdirs(self._tmpdirs)
        _reset_factory_for_tests()

    def test_conftest_reset_factory_singleton_isolates_workflow(self):
        """Verify the register -> manual-reset -> fail triple.

        Locks the cross-file pollution guard added in Sprint 17 R9
        (conftest._reset_services_registry auto-wires _reset_factory_for_tests).
        See TestConftestRegistryWireup in tests/test_code_intelligence_factory.py
        for the canonical conftest-reset contract test.
        """
        from code_review_workflow import (
            WorkspaceNotRegisteredError,
            register_workspace,
            review_file,
        )

        tmpdir_a = _make_tmpdir(self._tmpdirs)
        register_workspace(workspace_path=str(tmpdir_a), linter_cmd=None)

        _reset_factory_for_tests()

        tmpdir_b = _make_tmpdir(self._tmpdirs)
        with self.assertRaises(WorkspaceNotRegisteredError):
            review_file(workspace_path=str(tmpdir_b), filepath="x.py")


if __name__ == "__main__":
    unittest.main()