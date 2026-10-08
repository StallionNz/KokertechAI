"""Smoke-import guard for app_ui.

Complementary to the AST static check in test_app_ui_qtextedit.py --
this catches the regression class the AST walker CANNOT see:
real Python-level AttributeError / ImportError raised when any
transitive "from X import Y" line in app_ui.py resolves a name
that does not exist on its source module.

Regression classes this catches (vs. AST static check):
  - "from app_hotkeys import _WAKE_WORD_AVAILABLE" -- if app_hotkeys.py
    renames/removes the constant, this raises AttributeError on import.
  - "from config import CONFIG, WORKSPACE_DIR, save_settings" -- if
    config.py drops one of those names, this raises ImportError.
  - "from tabs.theme_builder_tab import get_all_themes" -- if the symbol
    is missing, raises ImportError.
  - PyQt6 widget imports + "from datetime import datetime" -- all the
    same import-resolution hazards surface here.

The AST walker in test_app_ui_qtextedit.py only sees attribute accesses
on instances (self.chat_display.<method>). It does NOT execute import
statements, so module-level import regressions slip through.

Runs under pytest.ini's -W error::RuntimeWarning so any bool-as-fd
regression that surfaces during class-definition metaclass binding in
any transitively imported Qt plugin also crashes immediately -- the
smoke-import guard covers the import path, the strict gate covers the runtime.
"""

from __future__ import annotations

import importlib
import sys
import unittest

import pytest


@pytest.mark.usefixtures("qapp")
class TestAppUiImportsSmoke(unittest.TestCase):
    """Import-time regression guard for app_ui.

    The headline assertion: importing app_ui (with a QApplication
    alive so any Qt-using sibling side-effects find a parent context)
    MUST NOT raise ImportError / AttributeError / any other exception.

    The @pytest.mark.usefixtures("qapp") decorator ensures the
    session-scoped qapp fixture is invoked at class start so that
    any Qt-using transitive imports find an active QApplication
    (defensive against "RuntimeError: Please instantiate the
    QApplication first" on PyQt6 builds that lazy-bind Qt classes).
    """

    def test_app_ui_module_imports_cleanly(self):
        """Re-import app_ui and confirm no AttributeError / ImportError
        surfaces.

        importlib.reload exercises the full module body a second time
        -- exactly what we want to verify (a regression in any transitive
        "from X import Y" line on the import path will fire here).
        """
        try:
            if "app_ui" in sys.modules:
                importlib.reload(sys.modules["app_ui"])
            else:
                importlib.import_module("app_ui")
        except AttributeError as e:
            self.fail(
                "Importing app_ui raised AttributeError: "
                + type(e).__name__ + ": " + str(e)
                + ". The AST check in test_app_ui_qtextedit.py cannot catch this "
                "class -- this smoke-import test is the complementary guard. "
                "Likely cause: a module-level 'from X import Y' references "
                "a name that no longer exists on X."
            )
        except ImportError as e:
            self.fail(
                "Importing app_ui raised ImportError: "
                + type(e).__name__ + ": " + str(e)
                + ". The AST check in test_app_ui_qtextedit.py cannot catch this "
                "class -- this smoke-import test is the complementary guard. "
                "Likely cause: a module-level 'from X import Y' references "
                "a module or symbol that no longer exists."
            )
        except Exception as e:  # noqa: BLE001 -- this IS the smoke-import gate
            self.fail(
                "Importing app_ui raised "
                + type(e).__name__ + ": " + str(e)
                + ". Any exception at import time fails the smoke-import gate."
            )

    def test_app_ui_class_definitions_present(self):
        """After import, AppUIMixin class + AppUIMixin.THEMES class
        attribute must be accessible -- belt-and-braces against partial
        module-load corruption.

        NOTE: THEMES lives on the AppUIMixin class, NOT at module level
        (it is defined inside the class body in app_ui.py). The test
        queries ``AppUIMixin.THEMES`` so the attribute path matches the
        actual definition site.

        Also spot-checks every theme dict for the keys apply_theme()
        dereferences (bg, bg_alt, bg_tab, text, etc.) --
        a missing key would KeyError at the first user-triggered theme
        switch, and the default-fallback in get_all_themes masks a
        broken custom theme silently.
        """
        import app_ui
        self.assertTrue(
            hasattr(app_ui, "AppUIMixin"),
            "app_ui.AppUIMixin class is missing after import."
        )
        # NOTE: THEMES is a class attribute of AppUIMixin, NOT a module-level
        # constant -- querying via ``app_ui.AppUIMixin.THEMES`` matches the
        # actual definition site (line ~22 of app_ui.py, inside the class body).
        self.assertTrue(
            hasattr(app_ui.AppUIMixin, "THEMES"),
            "AppUIMixin.THEMES class attribute is missing after import."
        )
        themes = app_ui.AppUIMixin.THEMES
        self.assertIsInstance(
            themes, dict,
            "AppUIMixin.THEMES must be a dict for apply_theme() to look up keys."
        )
        # Spot-check: canonical built-in theme names must be present.
        # If a rename slips through unnoticed, init_ui apply_theme
        # default-falls-back silently and the user sees a non-token visual.
        for theme in ("Cyberpunk (Pink/Cyan)", "Classic (Charcoal)"):
            self.assertIn(
                theme, themes,
                "Built-in theme '" + theme + "' missing from AppUIMixin.THEMES dict."
            )
        # Spot-check: every defined theme dict must have the keys apply_theme()
        # dereferences. Indexing a missing key would KeyError at runtime.
        required_keys = {
            "bg", "bg_alt", "bg_tab", "text", "text_tab", "accent1", "accent2",
        }
        for theme_name, theme_def in themes.items():
            self.assertEqual(
                set(theme_def.keys()), required_keys,
                "Theme '" + theme_name + "' has wrong keys vs. apply_theme required "
                + str(sorted(required_keys)) + ". Surplus keys are silently ignored "
                "by f-string template but missing keys would KeyError at first switch."
            )


if __name__ == "__main__":
    unittest.main()
