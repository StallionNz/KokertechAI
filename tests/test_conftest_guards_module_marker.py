"""Module-level marker opt-out proof (onboarding-guard audit pass 3).

``pytestmark`` on a module must reach ``unittest.TestCase`` items too --
this module is deliberately a TestCase class under a module-level
marker, mirroring how a future dashboard test module would opt out of
the _no_onboarding_modal guard wholesale. Kept separate from
test_conftest_guards.py because a module-level marker there would opt
out the neuter-assertion tests as well.
"""
import unittest

import pytest

pytestmark = pytest.mark.real_onboarding


class TestModuleLevelOptOut(unittest.TestCase):
    def test_both_methods_stay_production(self):
        import app_core
        w = app_core.KokertechDashboard.__new__(app_core.KokertechDashboard)
        self.assertEqual(w._run_onboarding_or_show.__module__, "app_core")
        self.assertEqual(w._check_save_integrity.__module__, "app_core")
