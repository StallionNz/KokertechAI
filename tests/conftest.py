"""tests/conftest.py - Local pytest fixtures for the tests/ directory.

Adds project root and ``scripts/`` to ``sys.path`` so test files in this
directory do not need their own ``sys.path.insert()`` boilerplate. The
additions are scoped to test collection only; they are written to
``sys.path`` before test modules are imported, so the test files'
``import`` statements resolve normally.

Project root is needed so ``import Discovery`` works (Discovery.py lives
at the project root alongside KokertechAI package modules). The
``scripts/`` directory is needed so ``import assert_section11_render_clean``
and ``from assert_known_anchors import ...`` resolve to the real
implementations next to ``run_tests.bat``.

The 50+ other test files in this project that use
``sys.path.insert(0, r\"C:\\KokertechAI\")`` are intentionally NOT migrated
here -- that is a separate hardcoded-path cleanup with its own risk
profile (would change every test file's import contract simultaneously).

Cross-file test isolation
---------------------------
``pytest_runtest_teardown`` resets module-level state between test files
to prevent cross-file contamination. Without this, tests in later files
see CONFIG / IDENTITY_CONFIG dicts mutated by earlier test files that
used ``patch.dict(..., clear=True)`` or direct dict writes (since
``from config import CONFIG`` gives every importing module a reference
to the SAME dict object).

The cleanup mirrors the KNOWLEDGE.md §12 pattern.

``pytest_runtest_makereport`` tracks pass/fail per test for crash-aware
teardown.
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Insert at position 0 so these take precedence over any site-packages
# that might ship modules with the same name.
sys.path.insert(0, str(_PROJECT_ROOT))             # for ``import Discovery``
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))  # for ``import assert_*``


# ── Cross-file test isolation ──────────────────────────────────
# Captures the clean module-level defaults at import time (before any
# test file runs). These are used by pytest_runtest_teardown to reset
# global state between test files.
# Cross-file config reset is handled by the root conftest.py's
# _reset_all_shared_state() which calls _reset_config_state() to
# remove non-JSON-serializable CONFIG keys, plus patch.stopall()
# reverts any patch.dict() mutations. Tests should use patch.dict()
# (not direct assignment) to mutate CONFIG so the root cleanup is
# sufficient. No duplicate reset needed here.
