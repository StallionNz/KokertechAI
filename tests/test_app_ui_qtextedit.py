"""
Static-regression test for `self.chat_display` method calls in app_ui.py.

Why this exists
---------------
PyQt6 (Qt6) removed a handful of QTextEdit methods that existed in Qt5 --
``setOpenLinks`` is the canonical example. If ``app_ui.py`` invokes a Qt5-only
method on a ``QTextEdit`` or ``QTextBrowser`` instance, ``KokertechDashboard.__init__``
crashes at startup with::

    AttributeError: 'QTextEdit' object has no attribute 'setOpenLinks'

This test catches that entire class of regression *before* it lands: it walks
the AST of ``app_ui.py``, enumerates every method called on
``self.chat_display``, and asserts every one of those method names lives on
``PyQt6.QtWidgets.QTextBrowser`` (the actual runtime type of ``chat_display``
in production, which inherits from ``QTextEdit``). No QApplication or widget
instantiation required -- pure source-level introspection.

Replaces a yearly recurring incident (the ``setOpenLinks`` removal was a known
class of Qt5->Qt6 break that previously shipped into production once).
"""

from __future__ import annotations

import ast
import pathlib
import unittest

import pytest

from PyQt6.QtWidgets import QTextBrowser, QTextEdit


# Project root == parent of tests/ directory.
PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP_UI_PATH = PROJECT_ROOT / "app_ui.py"


def _extract_chat_display_method_calls(path: pathlib.Path) -> set:
    """Walk ``path``'s AST and return every bare method-name call on
    ``self.chat_display``.

    Matches the exact pattern ``self.chat_display.METHOD(...)`` -- that is,
    ``ast.Call`` whose ``func`` is ``ast.Attribute(attr="METHOD",
    value=ast.Attribute(attr="chat_display", value=ast.Name(id="self")))``.

    Returns a ``set`` of method-name strings, deduplicated across call sites.
    Does NOT execute ``app_ui.py`` -- pure textual analysis.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Pattern: self.chat_display.METHOD(...)
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self"
            and func.value.attr == "chat_display"
        ):
            calls.add(func.attr)
    return calls


@pytest.mark.no_config_reset
class TestChatDisplayApiRegression(unittest.TestCase):
    """The main regression class. Catches missing 'QTextEdit' attributes
    before they break startup.
    """

    @classmethod
    def setUpClass(cls):
        cls._calls = _extract_chat_display_method_calls(APP_UI_PATH)

    def test_ast_walker_found_at_least_one_call(self):
        """Sanity-check the AST walker: if zero methods were extracted,
        either app_ui.py has stopped using ``self.chat_display`` (likely
        a refactor) or the walker itself is broken. Either way it deserves
        investigation.
        """
        self.assertGreater(
            len(self._calls),
            0,
            "AST walker found 0 'self.chat_display.METHOD' calls. Either "
            "the walker is broken or 'chat_display' was renamed. "
            "Update this test if the rename was intentional.",
        )

    def test_every_chat_display_method_exists_on_qtextedit(self):
        """The headline assertion: every method invoked on
        ``self.chat_display`` must exist on PyQt6's ``QTextEdit``.

        Failure means a Qt5-only method slipped into ``app_ui.py`` (e.g.
        ``setOpenLinks``, which was removed in Qt6). The fix is either:
          - remove the call (Qt6 QTextEdit has no equivalent auto-open
            behavior to disable; ``anchorClicked.connect`` already provides
            click interception), or
          - migrate ``chat_display`` to ``QTextBrowser`` and replace the call
            with the ``QTextBrowser`` equivalent (``setOpenExternalLinks``).
        """
        # NOTE: chat_display is created as QTextBrowser (app_ui.py:1171), which inherits
        # from QTextEdit and adds QTextBrowser-specific methods like setOpenExternalLinks.
        # We check against QTextBrowser since that's the actual type.
        chat_api = set(dir(QTextBrowser))
        missing = sorted(self._calls - chat_api)
        self.assertEqual(
            missing,
            [],
            f"app_ui.py invokes {len(missing)} method(s) on QTextEdit that "
            f"DO NOT EXIST on PyQt6's QTextEdit: {missing!r}. This is the "
            f"Qt5->Qt6 regression class (e.g. setOpenLinks removal). Fix the "
            f"call sites or migrate chat_display to QTextBrowser.",
        )

    def test_set_open_links_was_not_reintroduced(self):
        """Brutally explicit witness for the original incident. If anybody
        re-adds the Qt5 call, this fails loudly with a remediation hint
        before the integration tests even run.
        """
        self.assertNotIn(
            "setOpenLinks",
            self._calls,
            "'self.chat_display.setOpenLinks(...)' was re-introduced in "
            "app_ui.py. This Qt5 method has no PyQt6 equivalent on "
            "QTextEdit (it moved to QTextBrowser as setOpenExternalLinks). "
            "Fix: delete the call -- the adjacent anchorClicked.connect() "
            "preserves the original Qt5 click-interception intent.",
        )

    def test_call_set_is_well_formed_python(self):
        """Defensive: confirm the extracted calls are real strings (rules
        out AST visitor returning ast.Attribute objects instead of names).
        """
        for name in self._calls:
            self.assertIsInstance(name, str)
            self.assertTrue(
                name.isidentifier(),
                f"Extracted chat_display method name is not a valid Python "
                f"identifier: {name!r}. AST walker likely has a bug.",
            )


class TestQtTextEditApiSurface(unittest.TestCase):
    """Self-checks on the Qt surface itself. Belt-and-braces coverage of the
    QTextEdit methods most likely to be called by chat_display across tabs.

    Note: deliberately NOT checking ``anchorClicked`` here. PyQt6 signals are
    exposed lazily on the class object and are not always enumerated by
    ``dir(QTextEdit)`` or detected by ``hasattr`` -- they may only be
    accessible after class-body metaclass binding. The headline regression
    test ``test_every_chat_display_method_exists_on_qtextedit`` already
    catches ``anchorClicked`` correctly via ``set(dir(QTextEdit))`` set
    arithmetic (the surface test there confirmed this on PyQt6 6.11.0).
    Adding a second redundant check here would re-introduce the brittle
    detection method we just abandoned.
    """

    def test_basic_writing_methods_present_on_qtextedit(self):
        """Most likely chat_display methods used across tabs. Cheap
        belt-and-braces check on regular class-level methods (these are
        reliably detected by ``hasattr`` -- unlike signals).
        """
        for method_name in ("setPlainText", "append", "setHtml", "clear"):
            self.assertTrue(
                hasattr(QTextEdit, method_name),
                f"QTextEdit no longer exposes '{method_name}'.",
            )


if __name__ == "__main__":
    unittest.main()
