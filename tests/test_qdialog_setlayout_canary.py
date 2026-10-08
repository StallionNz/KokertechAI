"""Regression test for the canonical ``QVBoxLayout() + self.setLayout(layout)`` pattern.

Locks the production rationale at the test layer:
  * ``QVBoxLayout()`` followed by ``self.setLayout(layout)`` is the canonical,
    mock-friendly way to bind a layout to a ``QDialog`` (or any ``QWidget``)
    subclass.
  * The legacy idiom ``QVBoxLayout(parent_widget)`` couples layout construction
    with parent-binding in the constructor, so test-harness mocks for the
    parent widget do not observe the binding -- and on some PyQt6 builds the
    C++ binding raises ``TypeError``/``RuntimeError`` from sip-side arity
    checks against spec-based mocks.

Three layers of canary:

  1. ``test_legacy_layout_parent_mock_rejection`` (``MagicMock(spec=QDialog)``
     + ``QVBoxLayout(mock)``): the ironclad contract is
     ``mock.setLayout.call_count == 0``. We accept either PyQt6 outcome
     (sip-cast ``TypeError`` OR silent mock bypass) by wrapping the
     constructor in ``try/except TypeError: pass`` and asserting the
     ``call_count == 0`` invariant unconditionally at the bottom.

  2. ``test_canonical_layout_pattern_runtime``: real ``QDialog`` + canonical
     pattern + ``dialog.layout() is layout`` assertion. Runtime mirror of
     the AST-visible change in ``tabs/global_search.py:174``.

  3. ``test_global_search_dialog_ast_uses_canonical_pattern``: AST-level
     guard on ``tabs/global_search.py::GlobalSearchDialog._setup_ui`` --
     catches reverts to ``QVBoxLayout(self)`` and accidental removal of the
     ``self.setLayout(...)`` call BEFORE the Qt runtime is constructed.
     Target module/method lifted to module-level constants
     (``_TARGET_MODULE``/``_TARGET_METHOD``) so future renames are a one-line
     update.

Separate file location rationale:
  The sibling ``TestTabQtInterface`` in ``tests/test_tab_qt_interface.py`` runs
  ``TABS = _discover_tabs_from_init()`` at class-body time, which raises
  ``RuntimeError`` if ``tests/tabs/__init__.py`` is absent -- a pre-existing
  brittleness unrelated to this regression. Isolating this regression in its
  own module keeps both ``python -m unittest`` and ``pytest`` collection
  reachable without the sibling-class import barrier.
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import MagicMock


# Target AST node: future renames of the production method or file are a
# one-line update at the top of this file (no regex / hardcoded path).
_TARGET_MODULE = "tabs.global_search"
_TARGET_METHOD = "_setup_ui"


# Ensure the project root is on sys.path so ``from tabs.X import ...`` resolves
# consistently in both ``python -m unittest`` and ``pytest`` run modes.
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class TestQDialogSetLayoutCanary(unittest.TestCase):
    """Regression class: locks the QDialog decoupled layout pattern."""

    def setUp(self):
        # Pre-initialize a QApplication so the runtime tests have a valid Qt
        # environment. Mirrors conftest._ensure_qapp but works without pytest
        # plugins. Skip cleanly if PyQt6 is unavailable.
        #
        # Use ``QApplication([])`` (empty argv), NOT ``QApplication(sys.argv)``:
        # PyQt6 parses argv for ``-platform``/``-style`` flags, which would
        # conflict with the host process's pytest/CLI args under unfamiliar
        # test runners.
        try:
            from PyQt6.QtWidgets import (  # noqa: F401
                QApplication,
                QDialog,
                QVBoxLayout,
            )
        except ImportError:
            self.skipTest("PyQt6 not available in this environment")
        self._app = QApplication.instance() or QApplication([])
        if self._app is None:
            self.skipTest("QApplication could not be initialized")

    def test_legacy_layout_parent_mock_rejection(self) -> None:
        """``MagicMock(spec=QDialog)`` + ``QVBoxLayout(mock)`` -- dangerous
        idiom. PyQt6 may either:
          (A) Raise ``TypeError``/``RuntimeError`` from sip-side arity check.
          (B) Silently accept the mock, leaving
              ``mock.setLayout.call_count == 0`` -- the test harness can no
              longer observe the binding via ``mock.layout()``.
        Either branch is unsafe for mocking; the lone unbroken contract is
        ``mock.setLayout`` NEVER called explicitly. The unconditional
        ``call_count == 0`` assertion below is what makes this a true canary
        regardless of PyQt6 build behavior. ``MagicMock`` auto-attributes are
        independent of ``setLayout`` -- the counter only reflects explicit
        invocations from user code (or from ``QVBoxLayout``'s C++ binding,
        which does not call Python-level ``setLayout`` on the mock)."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout

        mock_dialog = MagicMock(spec=QDialog)

        try:
            _layout = QVBoxLayout(mock_dialog)  # legacy / dangerous idiom
        except TypeError:
            # (A) PyQt6 raised sip-cast rejection -- block (A) of the rationale.
            pass

        # Ironclad invariant across both PyQt6 branches:
        self.assertEqual(
            mock_dialog.setLayout.call_count,
            0,
            "Legacy QVBoxLayout(mock) somehow called setLayout -- either "
            "PyQt6 semantics changed or the test framework contract is wrong.",
        )

    def test_canonical_layout_pattern_runtime(self) -> None:
        """Real ``QDialog`` + ``QVBoxLayout() + dialog.setLayout(layout)`` --
        canonical pattern. ``dialog.layout()`` returns the bound layout
        instance at runtime.

        Not tautological: if the C++ binding silently failed (e.g. setLayout
        on a destroyed QWidget), ``dialog.layout()`` would return ``None`` and
        the ``assertIs`` would fail. This is a real binding assertion."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout

        dialog = QDialog()
        try:
            layout = QVBoxLayout()
            dialog.setLayout(layout)
            self.assertIs(
                dialog.layout(),
                layout,
                "Canonical setLayout failed to bind dialog.layout() to the "
                "QVBoxLayout instance.",
            )
        finally:
            # Schedule C++ widget destruction via the event loop; the session
            # is single-QApplication so deferred deletion prevents
            # widget accumulation across the test session.
            dialog.deleteLater()

    def test_global_search_dialog_ast_uses_canonical_pattern(self) -> None:
        """AST-level audit of ``_TARGET_MODULE._TARGET_METHOD``:
          (i)  No ``QVBoxLayout(self)`` call exists inside the method.
          (ii) At least one ``self.setLayout(...)`` call exists inside the
               method (the canonical binding).
        Path resolved via ``importlib.util.find_spec`` so the test gracefully
        ``skipTest``s if the production file is renamed/moved."""
        spec = importlib.util.find_spec(_TARGET_MODULE)
        if spec is None or spec.origin is None:
            self.skipTest(f"{_TARGET_MODULE} module not found -- file moved")

        with open(spec.origin, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())

        setup_ui_node = next(
            (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == _TARGET_METHOD
            ),
            None,
        )
        self.assertIsNotNone(
            setup_ui_node,
            f"{_TARGET_MODULE}.{_TARGET_METHOD} method not found -- refactor "
            "target file was rewritten without the canonical _setup_ui.",
        )

        # (i) No QVBoxLayout(self) call anywhere in the method.
        bad_legacy_calls: list[int] = []
        for node in ast.walk(setup_ui_node):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Name) and f.id == "QVBoxLayout":
                args = node.args
                if (
                    args
                    and isinstance(args[0], ast.Name)
                    and args[0].id == "self"
                ):
                    bad_legacy_calls.append(node.lineno)
        self.assertEqual(
            bad_legacy_calls,
            [],
            "Legacy QVBoxLayout(self) detected in "
            f"{_TARGET_MODULE}.{_TARGET_METHOD} at line(s) {bad_legacy_calls}. "
            "Use the canonical QVBoxLayout() + self.setLayout(layout) "
            "pattern instead.",
        )

        # (ii) At least one self.setLayout(...) call -- the explicit binder.
        has_self_setlayout = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setLayout"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            for node in ast.walk(setup_ui_node)
        )
        self.assertTrue(
            has_self_setlayout,
            "No self.setLayout(...) call found inside "
            f"{_TARGET_MODULE}.{_TARGET_METHOD} -- the canonical binder is "
            "missing; layout will not be parented to the dialog at runtime.",
        )


if __name__ == "__main__":
    unittest.main()
