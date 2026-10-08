"""REGRESSION GUARDs pinning the ``shared_dashboard`` fixture's 4 tab-side-effect
patch targets (conftest.py, v0.22.3).

The module-scoped fixture patches four slow/hanging tab-creation side-effects with
``MagicMock`` so Dashboard construction stays fast under pytest. The patch targets
are **magic strings** -- if a method is renamed, the patch silently no-ops and the
original hang/latency returns with zero test failure to point at the cause.
"""

import importlib
from unittest.mock import MagicMock

# (module, mixin_class_name, method_name) -- MUST match conftest.py::shared_dashboard.
# NOTE: the fixture ALSO patches the third-party ``requests.post`` (lines ~977-980)
# -- intentionally NOT pinned here, since third-party names don't drift with
# project refactors. If a future contributor adds a 5th project-side fixture
# patch, extend this list to match.
_FIXTURE_PATCH_TARGETS = [
    ("tabs.computer_use_tab", "ComputerUseTabMixin", "_init_safety"),
    ("tabs.health_tab", "HealthTabMixin", "_update_health"),
    ("tabs.cognitive_agency_tab", "CognitiveAgencyTabMixin", "refresh_agency_data"),
    ("tabs.neural_graph_tab", "NeuralGraphTabMixin", "render_knowledge_graph"),
]


# pragma: anti-fragility-orphan-ok  # by-design self-contained pure-invariant guard
def test_fixture_patch_targets_exist():
    """REGRESSION GUARD for the 4 ``shared_dashboard`` fixture patch targets in
    ``conftest.py::shared_dashboard`` (lines 983-1015 of ``conftest.py``).

    The fixture patches four tab-creation side-effects via string targets. Each
    target is a magic string: a rename of any method silently no-ops the patch,
    restoring the original slow/hanging Dashboard-creation behavior with no failing
    test to point at the cause. This guard imports each target module and asserts
    the class + method exist, so a rename fires here at collection time.

    The first failing assertion fires if a refactor renames any of the 4 patched
    methods (or the mixin classes): update BOTH the tab module AND the
    ``conftest.py`` fixture patch target string together.
    """
    for module_name, mixin_name, method_name in _FIXTURE_PATCH_TARGETS:
        mod = importlib.import_module(module_name)
        mixin = getattr(mod, mixin_name, None)
        assert mixin is not None, (
            "REGRESSION: conftest fixture patch target mixin "
            f"{module_name}.{mixin_name} no longer exists -- rename in conftest.py "
            "fixture patch target string must be synced"
        )
        assert hasattr(mixin, method_name), (
            "REGRESSION: conftest fixture patch target method "
            f"{module_name}.{mixin_name}.{method_name} no longer exists -- rename in "
            "conftest.py fixture patch target string must be synced"
        )


# pragma: anti-fragility-orphan-ok  # by-design self-contained pure-invariant guard
def test_shared_dashboard_does_not_spawn_graph_worker(shared_dashboard):
    """REGRESSION GUARD for the v0.22.3 ``shared_dashboard`` fixture patch
    behavior in ``conftest.py::shared_dashboard``.

    The static guard above proves the 4 patch-target strings resolve; this
    behavioral guard proves the patches are ACTIVE on a real constructed
    dashboard. ``render_knowledge_graph`` must be a ``MagicMock`` on the
    instance (class-level patch) AND ``self.layout_worker`` must never be a
    real worker object -- if it were, ``GraphLayoutWorker`` (a QThread that
    opens the real vault DB and paints scenes via ``layout_ready_signal``)
    would have been spawned during Dashboard construction, reintroducing the
    fixture-phase hang this patch was created to prevent (v0.22.3).

    The first failing assertion fires if a future contributor removes or
    narrows the fixture's ``render_knowledge_graph`` patch: the hang returns
    with no failing test pointing at the fixture.
    """
    window = shared_dashboard
    # The 4 tab side-effect methods must all be class-level MagicMocks on the
    # constructed dashboard (proof the fixture patches are live, not dormant).
    for module_name, mixin_name, method_name in _FIXTURE_PATCH_TARGETS:
        candidate = getattr(window, method_name, None)
        assert isinstance(candidate, MagicMock), (
            "REGRESSION: shared_dashboard fixture no longer patches "
            f"{module_name}.{mixin_name}.{method_name} on the constructed "
            "dashboard -- the slow/hanging tab side-effect is active again"
        )
    # No GraphLayoutWorker QThread must have been spawned during construction.
    # getattr-default-None form is robust to a future defensive
    # ``self.layout_worker = None`` init while still failing when a real worker
    # object exists.
    assert getattr(window, "layout_worker", None) is None, (
        "REGRESSION: a GraphLayoutWorker QThread was spawned during shared_dashboard "
        "construction -- the fixture's render_knowledge_graph patch is not effective"
    )
