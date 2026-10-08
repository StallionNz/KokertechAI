"""Self-tests for conftest.py autouse guards (audit 2026-09-24).

The autouse fixtures in conftest.py are infrastructure: if one silently
rots (method renamed, API drift, swallowed ImportError), parallel runs
re-expose the exact worker-death class it was built to fix. These tests
pin the guard contract so rot fails loudly here instead.
"""
import unittest

import pytest


class TestInheritedModelIsolation(unittest.TestCase):
    """The developer's persisted settings and local model must not enter tests."""

    def test_settings_path_uses_disposable_session_file(self):
        import os
        import config

        self.assertNotEqual(
            config.SETTINGS_PATH,
            os.path.join(config.WORKSPACE_DIR, "app_settings.json"),
        )
        self.assertIn("kokertech_test_settings_", config.SETTINGS_PATH)

    def test_inherited_local_model_paths_are_cleared(self):
        from config import CONFIG

        self.assertEqual(CONFIG.get("model_file"), "")
        self.assertEqual(CONFIG.get("model_name"), "")


class TestNoOnboardingModalGuard(unittest.TestCase):
    """_no_onboarding_modal must neuter BOTH startup modal choke points.

    Choke point 1: _run_onboarding_or_show -> run_onboarding ->
    OnboardingWizard.exec (the Group 5 worker-death mechanism).
    Choke point 2: _check_save_integrity -> corrupt-settings
    QMessageBox.question (same death class, else-branch of
    _init_startup_checks, audited 2026-09-24).
    """

    def _bare_dashboard(self):
        import app_core
        # __new__: no QObject init needed -- the guard's lambdas are
        # plain no-ops, so a bare instance is enough to prove binding.
        return app_core.KokertechDashboard.__new__(app_core.KokertechDashboard)

    def test_run_onboarding_or_show_is_neutered(self):
        w = self._bare_dashboard()
        self.assertEqual(w._run_onboarding_or_show.__module__, "conftest")
        w._run_onboarding_or_show()  # safe no-op on a bare instance

    def test_check_save_integrity_is_neutered(self):
        w = self._bare_dashboard()
        self.assertEqual(w._check_save_integrity.__module__, "conftest")
        w._check_save_integrity()  # safe no-op on a bare instance


@pytest.mark.real_onboarding
class TestRealOnboardingClassLevelOptOut(unittest.TestCase):
    """Class-level opt-out (audit pass 3, materialized from live probes):
    the marker on a TestCase class leaves BOTH production methods intact
    for every test inside -- the scope future dashboard tests will most
    often use."""

    def test_both_methods_stay_production(self):
        import app_core
        w = app_core.KokertechDashboard.__new__(app_core.KokertechDashboard)
        self.assertEqual(w._run_onboarding_or_show.__module__, "app_core")
        self.assertEqual(w._check_save_integrity.__module__, "app_core")


@pytest.mark.real_onboarding
def test_real_onboarding_marker_opt_out():
    """Opt-out contract: with @pytest.mark.real_onboarding the fixture
    must leave BOTH production methods untouched (module == app_core)."""
    import app_core
    w = app_core.KokertechDashboard.__new__(app_core.KokertechDashboard)
    assert w._run_onboarding_or_show.__module__ == "app_core"
    assert w._check_save_integrity.__module__ == "app_core"

