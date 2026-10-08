"""Tests for services.py — ServiceRegistry + singleton migration (Sprint 1)."""

import pytest

from services import ServiceRegistry, get_services


# ---------------------------------------------------------------------------
# ServiceRegistry core behaviour
# ---------------------------------------------------------------------------

class TestServiceRegistryCore:
    """Fundamental register / get / reset semantics."""

    def test_register_get_same_instance_singleton(self):
        reg = ServiceRegistry()
        reg.register("x", lambda: {"v": 1}, singleton=True)
        a = reg.get("x")
        b = reg.get("x")
        assert a is b
        assert a["v"] == 1

    def test_register_get_factory_multiple_calls_non_singleton(self):
        reg = ServiceRegistry()
        reg.register("x", lambda: {"v": 1}, singleton=False)
        a = reg.get("x")
        b = reg.get("x")
        assert a is not b

    def test_get_unknown_raises(self):
        reg = ServiceRegistry()
        with pytest.raises(KeyError):
            reg.get("missing")

    def test_register_empty_name_raises(self):
        reg = ServiceRegistry()
        with pytest.raises(ValueError):
            reg.register("", lambda: 1)

    def test_register_non_string_name_raises(self):
        reg = ServiceRegistry()
        with pytest.raises(ValueError):
            reg.register(123, lambda: 1)  # type: ignore[arg-type]

    def test_reset_clears_singletons_keeps_factories(self):
        reg = ServiceRegistry()
        reg.register("x", lambda: {"v": 1}, singleton=True)
        _ = reg.get("x")  # instantiate
        reg.reset()
        # Factory still registered → get() creates fresh instance
        obj = reg.get("x")
        assert obj["v"] == 1

    def test_reset_clear_factories_full_wipe(self):
        reg = ServiceRegistry()
        reg.register("x", lambda: {"v": 1}, singleton=True)
        reg.reset(clear_factories=True)
        with pytest.raises(KeyError):
            reg.get("x")


# ---------------------------------------------------------------------------
# has() — new in Sprint 1
# ---------------------------------------------------------------------------

class TestServiceRegistryHas:
    """has() checks whether a name is registered."""

    def test_has_returns_true_for_registered(self):
        reg = ServiceRegistry()
        reg.register("x", lambda: 1)
        assert reg.has("x") is True

    def test_has_returns_false_for_unregistered(self):
        reg = ServiceRegistry()
        assert reg.has("nope") is False

    def test_has_after_reset_still_true(self):
        reg = ServiceRegistry()
        reg.register("x", lambda: 1)
        reg.get("x")  # instantiate
        reg.reset()
        # Factory kept → has() still True
        assert reg.has("x") is True

    def test_has_after_clear_factories_false(self):
        reg = ServiceRegistry()
        reg.register("x", lambda: 1)
        reg.reset(clear_factories=True)
        assert reg.has("x") is False


# ---------------------------------------------------------------------------
# list() — new in Sprint 1
# ---------------------------------------------------------------------------

class TestServiceRegistryList:
    """list() returns sorted registered names."""

    def test_list_empty(self):
        reg = ServiceRegistry()
        assert reg.list() == []

    def test_list_returns_sorted(self):
        reg = ServiceRegistry()
        reg.register("z", lambda: 1)
        reg.register("a", lambda: 2)
        reg.register("m", lambda: 3)
        assert reg.list() == ["a", "m", "z"]

    def test_list_survives_reset(self):
        reg = ServiceRegistry()
        reg.register("x", lambda: 1)
        reg.reset()
        assert reg.list() == ["x"]


# ---------------------------------------------------------------------------
# register_instance() — new in Sprint 1
# ---------------------------------------------------------------------------

class TestServiceRegistryRegisterInstance:
    """register_instance() stores a pre-built object."""

    def test_register_instance_returns_same_object(self):
        reg = ServiceRegistry()
        obj = {"hello": "world"}
        reg.register_instance("my_obj", obj)
        assert reg.get("my_obj") is obj

    def test_register_instance_is_singleton(self):
        reg = ServiceRegistry()
        obj = [1, 2, 3]
        reg.register_instance("lst", obj)
        a = reg.get("lst")
        b = reg.get("lst")
        assert a is b
        assert a is obj

    def test_register_instance_empty_name_raises(self):
        reg = ServiceRegistry()
        with pytest.raises(ValueError):
            reg.register_instance("", "x")


# ---------------------------------------------------------------------------
# Global get_services() singleton
# ---------------------------------------------------------------------------

class TestGlobalGetServices:
    """get_services() returns the global singleton registry."""

    def test_singleton_identity(self):
        r1 = get_services()
        r2 = get_services()
        assert r1 is r2

    def test_register_get_round_trip(self):
        reg = get_services()
        reg.register("test_svc_sprint1", lambda: 42, singleton=True)
        assert reg.get("test_svc_sprint1") == 42
        # cleanup
        reg.reset(clear_factories=True)

    def test_reset_clears_instances(self):
        reg = get_services()
        reg.register("tmp_sprint1", lambda: 99, singleton=True)
        _ = reg.get("tmp_sprint1")
        reg.reset(clear_factories=True)
        with pytest.raises(KeyError):
            reg.get("tmp_sprint1")
        # cleanup
        reg.reset(clear_factories=True)

    def test_register_canonical_services_restores_after_full_wipe(self):
        """REGRESSION GUARD for cross-file registry wiring restoration.

        The sibling tests in this class call ``get_services().reset(clear_factories=True)``
        on the GLOBAL singleton — a full wipe that removes the canonical
        ``history_service`` / ``context_service`` / ``prompt_service`` /
        ``provider_service`` factories registered at ``services/registry.py``
        import time. Previously nothing re-registered them, so ANY later
        ``KokertechController()`` construction in the same pytest process
        raised ``KeyError: service 'history_service' is not registered``
        (services/registry.py:61 ``get()``) — exposed by
        ``tests/test_reject_with_error.py`` failing only in cross-file batches
        after this file. ``conftest._reset_services_registry()`` now re-runs
        ``register_canonical_services()`` after every test to repair the
        wiring; this test pins the helper's own contract (idempotent, restores
        all 4 factories, fresh instance constructible).

        The ``get_services().reset(clear_factories=True)`` fires if a refactor
        removes the helper's invocation from ``services/registry.py``
        (``register_canonical_services()`` call at module bottom).
        """
        from services.registry import register_canonical_services

        reg = get_services()
        reg.reset(clear_factories=True)  # destructive wipe, same as siblings
        assert reg.has("history_service") is False

        register_canonical_services()
        for name in (
            "history_service",
            "context_service",
            "prompt_service",
            "provider_service",
        ):
            assert reg.has(name), f"{name} not re-registered after wipe"
        # A repaired singleton constructs a fresh, working instance
        assert reg.get("history_service") is not None
        assert reg.get("history_service") is reg.get("history_service")  # singleton


# ---------------------------------------------------------------------------
# ai_base._PROVIDER_CACHE migration
# ---------------------------------------------------------------------------

class TestProviderCacheMigration:
    """Verify _get_provider_cache() returns a mutable dict.

    NOTE: _get_provider_cache was planned but never added to ai_base.py.
    The provider cache is a module-level dict (_PROVIDER_CACHE) that is
    cleared by reset_providers(). These tests verify the cache semantics
    via the public API instead.
    """

    def test_provider_cache_is_cleared_by_reset(self):
        import ai_base
        from ai_base import get_provider, reset_providers
        # Populate the cache
        get_provider(name="local_llm", default_model="test-cache")
        assert len(ai_base._PROVIDER_CACHE) > 0
        reset_providers()
        assert len(ai_base._PROVIDER_CACHE) == 0

    def test_reset_providers_clears_singleton_instances(self):
        from ai_base import get_provider, reset_providers
        p1 = get_provider(name="local_llm", default_model="test-reset")
        reset_providers()
        p2 = get_provider(name="local_llm", default_model="test-reset")
        # After reset, a fresh instance should be created
        assert p1 is not p2


# ---------------------------------------------------------------------------
# memory_vault._model_state migration
# ---------------------------------------------------------------------------

class TestModelStateMigration:
    """Verify _model_state dict holds embedding model state."""

    def test_model_state_is_dict(self):
        from memory_vault import _model_state
        assert isinstance(_model_state, dict)
        assert "model" in _model_state
        assert "load_attempted" in _model_state

    def test_get_model_returns_from_state(self):
        from memory_vault import _model_state, _get_model
        # When embeddings are skipped, _model_state["model"] should stay None
        import os
        os.environ["KOKERTECH_SKIP_EMBEDDINGS"] = "1"
        _model_state["model"] = None
        _model_state["load_attempted"] = False
        result = _get_model()
        assert result is None
        assert _model_state["load_attempted"] is True
        # cleanup
        del os.environ["KOKERTECH_SKIP_EMBEDDINGS"]


# ---------------------------------------------------------------------------
# memory_vault._session_state migration
# ---------------------------------------------------------------------------

class TestSessionStateMigration:
    """Verify _session_state dict holds session ID."""

    def test_session_state_is_dict(self):
        from memory_vault import _session_state
        assert isinstance(_session_state, dict)
        assert "session_id" in _session_state

    def test_get_current_session_id_populates_state(self):
        from memory_vault import _session_state, get_current_session_id
        _session_state["session_id"] = None  # reset
        sid = get_current_session_id()
        assert isinstance(sid, str)
        assert sid.startswith("session_")
        assert _session_state["session_id"] == sid

    def test_start_new_session_rotates(self):
        from memory_vault import _session_state, get_current_session_id, start_new_session
        old_sid = get_current_session_id()
        new_sid = start_new_session(summary="test rotation")
        assert new_sid != old_sid
        assert _session_state["session_id"] == new_sid


# ---------------------------------------------------------------------------
# Thread-safety smoke test
# ---------------------------------------------------------------------------

class TestRegistryThreadSafety:
    """Verify concurrent get() doesn't crash or duplicate."""

    def test_concurrent_get(self):
        import threading
        reg = ServiceRegistry()
        reg.register("shared", lambda: {"counter": 0})
        results = []

        def getter():
            obj = reg.get("shared")
            results.append(obj)

        threads = [threading.Thread(target=getter) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads got the same singleton
        assert len(results) == 20
        assert all(r is results[0] for r in results)
