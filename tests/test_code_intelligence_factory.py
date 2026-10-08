"""Tests for CodeIntelligenceFactory (Sprint 17)."""

import shutil
import tempfile
from pathlib import Path

import pytest

from services.code_intelligence import CodeIntelligence
from services.code_intelligence_factory import (
    CodeIntelligenceFactory,
    get_code_intelligence_factory,
)


class TestCodeIntelligenceFactory:
    """Sprint 17: 4-test spec for CodeIntelligenceFactory DI wiring."""

    def setup_method(self, method):
        """Per-test tmpdir + reset class-level _audit_warned for cross-test isolation."""
        self.tmpdir = Path(tempfile.mkdtemp(prefix="ci_factory_test_"))
        # Round 5 (Sprint 16) class-level audit-warned flag persisted across
        # tests in prior rounds; reset here defensively so new CI instances
        # created during resolve() don't trip the warn-once path.
        from services.code_intelligence import CodeIntelligence as _CI
        _CI._audit_warned = False

    def teardown_method(self, method):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_register_returns_instance(self):
        """register() stores pattern; resolve() returns CodeIntelligence subclass instance."""
        factory = CodeIntelligenceFactory()
        factory.register(str(self.tmpdir), {"linter_cmd": "ruff"})
        ci = factory.resolve(str(self.tmpdir))
        assert isinstance(ci, CodeIntelligence), \
            "resolve() must return a CodeIntelligence instance, got: " + repr(type(ci))
        # The factory stores workspace_pattern verbatim. On Windows,
        # ``tempfile.mkdtemp`` can return an 8.3 short-name form (e.g.,
        # "JDKBUI~1") for paths under junctions, and ``Path.resolve()`` does
        # NOT expand short names -- it only follows symlinks. So the correct
        # equality is the raw pattern that was registered, which is ``str(self.tmpdir)``.
        assert ci.workspace == str(self.tmpdir), (
            "ci.workspace must equal the registered path verbatim (not "
            "os.path.realpath-expanded); got: " + repr(ci.workspace)
            + " vs registered: " + repr(str(self.tmpdir))
        )
        assert ci._linter_cmd == "ruff", \
            "ci._linter_cmd must come from registered linter_config, got: " + repr(ci._linter_cmd)

    def test_factory_caches_workspace(self):
        """Two resolve() calls for the same pattern return the same cached instance."""
        factory = CodeIntelligenceFactory()
        factory.register(str(self.tmpdir))
        first = factory.resolve(str(self.tmpdir))
        second = factory.resolve(str(self.tmpdir))
        assert first is second, \
            "factory must cache by workspace_pattern; second resolve() should return cached instance"

    def test_factory_rejects_invalid_workspace(self):
        """Empty/None patterns raise ValueError; unregistered resolve() raises KeyError; has() reflects state."""
        factory = CodeIntelligenceFactory()
        # Empty string rejected by register()
        with pytest.raises(ValueError, match="workspace_pattern must be a non-empty string"):
            factory.register("", {"linter_cmd": "ruff"})
        # None rejected by register()
        with pytest.raises(ValueError, match="workspace_pattern must be a non-empty string"):
            factory.register(None, {"linter_cmd": "ruff"})
        # Unregistered resolve() raises KeyError (uses path under tmpdir to avoid hardcoded /tmp).
        unregistered = str(self.tmpdir / "_never_registered_xyz")
        with pytest.raises(KeyError, match="workspace pattern not registered"):
            factory.resolve(unregistered)
        # has() reflects registration state BEFORE register() -- must be False.
        other = str(self.tmpdir / "_other_workspace_xyz")
        assert factory.has(other) is False, (
            "has() must return False for unregistered pattern, got True for: "
            + repr(other)
        )
        # has() reflects registration state AFTER register() -- must be True.
        factory.register(other)
        assert factory.has(other) is True, (
            "has() must return True after register() for: " + repr(other)
            + "; registry: " + repr(factory.registered_patterns())
        )

    def test_factory_lazy_instantiation(self):
        """register() must not call CodeIntelligence.__init__; resolve() calls it exactly once.

        C4 fix: spy on ``CodeIntelligence.__init__`` to count actual
        constructor invocations. White-box assertions on ``factory._instances``
        would lock the cache attribute name into the test contract; spying
        on ``__init__`` is observable (real CodeIntelligence side-effects)
        and survives a future refactor that renames the internal cache.
        """
        # Spy on CodeIntelligence.__init__ via monkey-patching + restore in finally.
        init_call_count = [0]
        original_init = CodeIntelligence.__init__
        def _spy_init(self, *args, **kwargs):
            init_call_count[0] += 1
            return original_init(self, *args, **kwargs)
        CodeIntelligence.__init__ = _spy_init
        try:
            factory = CodeIntelligenceFactory()
            factory.register(str(self.tmpdir), {"linter_cmd": "ruff"})
            # Right after register(): zero __init__ calls.
            assert init_call_count[0] == 0, (
                "register() must NOT instantiate CodeIntelligence; __init__ fired "
                + str(init_call_count[0]) + " time(s) so far, expected 0"
            )
            # First resolve(): exactly one __init__ call.
            _ci = factory.resolve(str(self.tmpdir))
            assert init_call_count[0] == 1, (
                "first resolve() must trigger exactly 1 __init__ call, got: "
                + str(init_call_count[0])
            )
            # Second resolve(): still exactly one (cache hit, no new __init__).
            _ci2 = factory.resolve(str(self.tmpdir))
            assert init_call_count[0] == 1, (
                "second resolve() must hit the cache (no new __init__); got: "
                + str(init_call_count[0]) + " __init__ calls total, expected 1"
            )
        finally:
            # Always restore the original __init__ for cross-test isolation.
            CodeIntelligence.__init__ = original_init


class TestConftestRegistryWireup:
    """Sprint 17 R9 regression: conftest + factory must agree on cross-file reset.

    The factory exposes ``_reset_factory_for_tests()`` (Sprint 17 R5) which
    clears the module-level ``_FACTORY_SINGLETON``. conftest.py exposes
    ``_reset_services_registry()`` which clears the registry. The R9 wire-up is:
    conftest must ALSO call ``_reset_factory_for_tests()`` so both surfaces stay
    in sync post-teardown. Without the wire-up, the divergence resurfaces --
    ``_FACTORY_SINGLETON`` points at an instance the no-longer-reset registry has
    forgotten (``reg.get("code_intelligence_factory")`` raises ``KeyError`` while
    ``get_code_intelligence_factory()`` returns the stale cached instance).

    The Sprint 17 R9 wire-up adds ~5 LOC to conftest.py's
    ``_reset_services_registry()``. The regression test below imports conftest's
    helper via ``importlib.util.spec_from_file_location`` (loading the project-
    ROOT conftest.py by absolute path under a unique module name) so pytest's
    sys.modules cache of ``tests/conftest.py`` never collides -- this is the
    robust pattern that survives any future pytest-config drift.
    """

    def test_conftest_reset_services_registry_wireup_clears_factory_singleton(self):
        """After conftest._reset_services_registry(), _FACTORY_SINGLETON must be None.

        Behavioral test: invokes the conftest helper exactly as ``pytest_runtest_
        teardown`` and ``_reset_services_between_classes`` would, then asserts on
        the post-call module-level state. The key claim is: if the wire-up call
        site inside conftest forgets to invoke ``_reset_factory_for_tests()``,
        this assertion fails -- which is precisely the regression we'd want a
        future maintainer to catch instantly.
        """
        # Populate _FACTORY_SINGLETON via the helper (constructs + registers).
        import services.code_intelligence_factory as cif
        cif._reset_factory_for_tests()  # ensure clean precondition
        cif.get_code_intelligence_factory()
        assert cif._FACTORY_SINGLETON is not None, (
            "precondition: get_code_intelligence_factory() must populate "
            "_FACTORY_SINGLETON; got None -- helper must construct + register "
            "before this assertion can hold."
        )

        # Load the PROJECT-ROOT conftest.py by absolute file path under a
        # unique module name. This bypasses sys.modules' cached ``conftest``
        # slot (which pytest may have filled with ``tests/conftest.py`` -- a
        # separate, lighter-weight sys.path manipulation file that does NOT
        # define ``_reset_services_registry``). Loading under a unique name
        # avoids the import collision cleanly without requiring
        # ``pytest.skip`` fallbacks.
        import importlib.util
        project_root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(
            "_sprint17_r9_root_conftest_for_test_only",
            str(project_root / "conftest.py"),
        )
        if spec is None or spec.loader is None:
            import pytest
            pytest.skip(
                "Could not load project-root conftest.py at "
                + str(project_root / "conftest.py")
                + " via spec_from_file_location; pytest config drift cannot "
                + "verify Sprint 17 R9 wire-up. (This skip is a graceful "
                + "fallback, NOT a regression -- the wire-up code in "
                + "conftest.py IS still effective for live pytest runs.)"
            )
        _root_conftest = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_root_conftest)

        # Invoke the wire-up call site. The R9 closeout requires this function
        # to call ``_reset_factory_for_tests()`` AFTER ``get_services().reset()``
        # so both surfaces (registry + _FACTORY_SINGLETON) reset together.
        _root_conftest._reset_services_registry()

        # After the wire-up: _FACTORY_SINGLETON must be None.
        assert cif._FACTORY_SINGLETON is None, (
            "conftest._reset_services_registry() must invoke "
            "_reset_factory_for_tests() to clear _FACTORY_SINGLETON "
            "post-registry-reset. Without this wire-up, "
            "reg.get('code_intelligence_factory') raises KeyError while "
            "get_code_intelligence_factory() returns the stale cached "
            "instance -- the exact cross-test pollution divergence this "
            "Sprint 17 R9 closeout is designed to close. See "
            "services/code_intelligence_factory.py around line 192 "
            "(_reset_factory_for_tests) and conftest.py's "
            "_reset_services_registry for the call site."
        )

