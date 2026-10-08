"""code_intelligence_factory.py - Sprint 17: DI factory for CodeIntelligence.

Workspace-aware factory that registers workspace patterns and resolves them
to CodeIntelligence instances. Lets the agent loop register workspace patterns
(which are the workspace paths themselves in Sprint 17 scope -- future sprints
may add regex/glob routing) and resolve a workspace-appropriate
CodeIntelligence instance without re-implementing path-anchoring logic per
call site.

Cross-file hygiene: factory state is per-instance (no module-level mutable
globals). The ServiceRegistry wiring is set up by
`get_code_intelligence_factory()` helper, lazy on first access.

THREAD-SAFETY: factory uses ``threading.RLock`` (per-instance) +
``threading.Lock`` (``_FACTORY_INIT_LOCK``, module-level for the singleton
helper). Single-instance ops (register/resolve/reset) hold ``_lock`` across
their body. Cross-test pollution guard: see ``_reset_factory_for_tests()``
for the registry-reset invariance between test files. (Sprint 16 R5 leaned
on a class-level flag defensive note; this factory uses real locks.)

LOCK-ORDER CONTRACT: The canonical outermost-to-innermost acquisition
order is ``_FACTORY_INIT_LOCK`` -> ``registry._lock``. This pattern is
enforced inside ``get_code_intelligence_factory()`` (which calls
``get_services().register_instance`` while holding ``_FACTORY_INIT_LOCK``
-- that internally acquires ``registry._lock``). Do NOT introduce a
future code path that acquires ``registry._lock`` BEFORE
``_FACTORY_INIT_LOCK`` -- inverting this order creates a deadlock cycle
with conftest's ``_reset_service_registry`` block. The reset helper
(``_reset_factory_for_tests()``) holds only ``_FACTORY_INIT_LOCK`` and
does NOT touch the registry; future maintainers must preserve that
invariant.
"""
from __future__ import annotations

import copy
import threading
from typing import Any, Dict, Optional

from .code_intelligence import CodeIntelligence
# Hoisted from inside get_code_intelligence_factory() so the helper body
# doesn't need to repeat the import on every call.
from .registry import get_services


class CodeIntelligenceFactory:
    """Workspace-pattern -> CodeIntelligence factory with lazy instantiation + cache.

    Pattern semantics: `workspace_pattern` IS the workspace path (Sprint 17
    scope). Future sprints may extend `register()` to accept
    `(pattern, real_path)` tuples for glob/regex routing.

    Lifecycle:
        - `register(workspace_pattern, linter_config)` stores the pattern
          (validates pattern shape; does NOT instantiate the CI).
        - `resolve(workspace_pattern)` lazily constructs the CI on the first
          call + caches it.
        - Subsequent `resolve(workspace_pattern)` calls return the cached
          instance.
        - `reset(clear_registrations=False)` clears the cache; with
          `clear_registrations=True` also wipes registrations.

    DI wiring: `get_code_intelligence_factory()` (below) returns the global
    singleton via `ServiceRegistry` for agent-loop access. Tests should
    instantiate `CodeIntelligenceFactory()` directly per-test for isolation.
    """

    def __init__(self) -> None:
        # pattern -> linter_config dict (deep-copied on register; see C3 note)
        self._patterns: Dict[str, Dict[str, Any]] = {}
        # workspace path -> CodeIntelligence instance (cache, lazy-filled on resolve)
        self._instances: Dict[str, CodeIntelligence] = {}
        # Guards mutations of _patterns + _instances. Kept as RLock (not
        # plain Lock) so any future method that re-enters via register /
        # resolve (e.g., an auto_register_on_resolve() helper that calls
        # register() from inside resolve()) cannot deadlock. Resolution
        # currently holds _lock across CodeIntelligence.__init__ + cache
        # write because construction is microseconds-fast (os.path.realpath
        # + JSON cache priming -- no model loading).
        self._lock = threading.RLock()

    def register(
        self,
        workspace_pattern: str,
        linter_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Register a workspace pattern. `workspace_pattern` == workspace path.

        Deep-copies ``linter_config`` so that subsequent caller-side
        mutation of nested values (e.g., ``extra_args`` lists or ``env``
        dicts) does not silently mutate the factory's stored config.
        Acquires ``self._lock`` so concurrent register/reset calls are
        serialized.
        """
        if not isinstance(workspace_pattern, str) or not workspace_pattern:
            raise ValueError(
                "workspace_pattern must be a non-empty string, got: "
                + repr(workspace_pattern)
            )
        with self._lock:
            self._patterns[workspace_pattern] = copy.deepcopy(linter_config or {})

    def resolve(self, workspace_pattern: str) -> CodeIntelligence:
        """Lazy-resolve `workspace_pattern` to a CodeIntelligence instance.

        First call: validates pattern is registered (KeyError otherwise),
        constructs `CodeIntelligence(workspace=workspace_pattern,
        linter_cmd=linter_config["linter_cmd"])`, caches it.
        Subsequent calls: return the cached instance.

        Entire body runs under ``self._lock`` so concurrent
        ``register/reset/resolve`` cannot leave a cached instance for a
        pattern that has just been unregistered. The lock is held across
        ``CodeIntelligence.__init__`` -- acceptable here because
        construction is microseconds-fast (no model I/O).

        Raises:
            KeyError: pattern not registered.
            (Other exceptions from `CodeIntelligence.__init__` propagate.)
        """
        with self._lock:
            if workspace_pattern not in self._patterns:
                raise KeyError(
                    "workspace pattern not registered: " + repr(workspace_pattern)
                )
            if workspace_pattern in self._instances:
                return self._instances[workspace_pattern]
            linter_config = self._patterns[workspace_pattern]
            linter_cmd = (
                linter_config.get("linter_cmd")
                if isinstance(linter_config, dict)
                else None
            )
            ci = CodeIntelligence(workspace=workspace_pattern, linter_cmd=linter_cmd)
            self._instances[workspace_pattern] = ci
            return ci

    def has(self, workspace_pattern: str) -> bool:
        """Check whether `workspace_pattern` is registered."""
        return workspace_pattern in self._patterns

    def reset(self, clear_registrations: bool = False) -> None:
        """Clear cached instances; with `clear_registrations=True` also wipe registrations.

        Entire body runs under ``self._lock`` (matches ``resolve`` /
        ``register``).
        """
        with self._lock:
            self._instances.clear()
            if clear_registrations:
                self._patterns.clear()

    def registered_patterns(self) -> list[str]:
        """Return sorted list of registered workspace patterns (debug/test helper)."""
        return sorted(self._patterns.keys())


# Module-level singleton stays set after construction so subsequent
# callers return the cached `_FACTORY_SINGLETON` directly without registry
# round-trip. The factory instance itself holds per-singleton state; tests
# that need isolation should construct their own `CodeIntelligenceFactory()`
# directly (matching the standard "no globals in tests" convention).
_FACTORY_SINGLETON: Optional[CodeIntelligenceFactory] = None
_FACTORY_INIT_LOCK = threading.Lock()


def get_code_intelligence_factory() -> CodeIntelligenceFactory:
    """Eager-construct singleton + ``ServiceRegistry.register_instance()``.

    Replaces the prior "check-then-register" wrapper that had a race when
    two concurrent callers both passed ``reg.has() == False`` and overwrote
    each other's factory closure in ``_factories``. Now: ``_FACTORY_INIT_LOCK``
    serializes construction + registration; after first call, the cached
    ``_FACTORY_SINGLETON`` is returned directly. ``ServiceRegistry`` acts as
    the discovery surface for callers querying by name
    (``reg.get("code_intelligence_factory")``).

    Tests that need isolation should instantiate ``CodeIntelligenceFactory()``
    directly + avoid this global helper.
    """
    global _FACTORY_SINGLETON
    with _FACTORY_INIT_LOCK:
        if _FACTORY_SINGLETON is None:
            _FACTORY_SINGLETON = CodeIntelligenceFactory()
            # Module-level import; the helper does not need to re-import.
            get_services().register_instance(
                "code_intelligence_factory", _FACTORY_SINGLETON,
            )
        return _FACTORY_SINGLETON


def _reset_factory_for_tests() -> None:
    """Reset the module-level ``_FACTORY_SINGLETON`` for cross-test isolation.

    Why this is locked: The ``with _FACTORY_INIT_LOCK:`` block on a single
    None-assignment is NOT for atomicity (CPython treats a single
    global-name store as already atomic). It's for ORDERING relative to
    ``get_code_intelligence_factory()``: serializing the reset against that
    helper's ``if _FACTORY_SINGLETON is None / construct / register`` read-
    check-act sequence so the two cannot half-overlap.    Without this lock, a concurrent registry-reset (e.g., from
    ``conftest._reset_service_registry()``) could leave the module-level
    cache pointing at an instance the no-longer-reset registry has
    forgotten -- the exact divergence this helper is designed to mitigate.

    NB: This helper clears ONLY the module-level ``_FACTORY_SINGLETON``.
    The registry entry at
    ``get_services()._services["code_intelligence_factory"]`` is NOT
    cleared here (no per-name deregister on ``ServiceRegistry``; calling
    ``reset(False)`` would wipe ALL singletons, collateral damage we don't
    want). After this helper returns, the caller MUST call
    ``get_code_intelligence_factory()`` to re-construct + re-register
    (``register_instance`` overwrites ``registry._services[name]`` cleanly).
    Without the follow-up call, the divergence this helper mitigates
    resurfaces -- the module-level cache is empty but the registry still
    holds the stale instance.

    This is currently a manual caller responsibility -- ``conftest.py`` has
    no wire-up. Tests exercising the global helper should call this from
    ``setup_method`` if they need a registry-reset between tests.
    """
    global _FACTORY_SINGLETON
    with _FACTORY_INIT_LOCK:
        _FACTORY_SINGLETON = None
