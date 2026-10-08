"""Service registry / dependency container for L99 refactor.

Goal (Sprint 1): replace scattered module-level mutable singletons with a
single injectable registry for cross-cutting state.

This implementation is intentionally small and testable.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from . import DEFAULT_WORKSPACE

# Use stdlib logging (not logging_config) to avoid potential circular
# imports — registry.py is the DI container, the most foundational service.
_logger = logging.getLogger("ServiceRegistry")


class ServiceRegistry:
    """Minimal DI container with optional singleton semantics."""

    def __init__(self) -> None:
        self._services: Dict[str, Any] = {}
        self._factories: Dict[str, Callable[[], Any]] = {}
        self._singleton_flags: Dict[str, bool] = {}
        self._lock = threading.RLock()


    def register(
        self,
        name: str,
        factory: Callable[[], Any],
        *,
        singleton: bool = True,
    ) -> None:

        """Register a service.

        If singleton=True, the factory is invoked at first `get()`.
        If singleton=False, factory is invoked on every `get()`.
        """
        if not name or not isinstance(name, str):
            raise ValueError("service name must be a non-empty string")

        with self._lock:
            # store factory; for non-singletons we keep factory but do not cache
            self._factories[name] = factory
            self._singleton_flags[name] = singleton
            if not singleton:
                # ensure stale instance isn't returned
                self._services.pop(name, None)


    def get(self, name: str) -> Any:
        with self._lock:
            if name not in self._factories:
                raise KeyError(f"service '{name}' is not registered")

            is_singleton = self._singleton_flags.get(name, True)

            if is_singleton:
                if name in self._services:
                    return self._services[name]

                inst = self._factories[name]()
                self._services[name] = inst
                return inst

            # non-singleton: create fresh each time
            return self._factories[name]()


    def has(self, name: str) -> bool:
        """Check if a service is registered."""
        with self._lock:
            return name in self._factories


    def list(self) -> list[str]:
        """Return sorted list of registered service names."""
        with self._lock:
            return sorted(self._factories.keys())


    def register_instance(self, name: str, instance: Any) -> None:
        """Register a pre-built instance (no factory needed).

        Convenience wrapper for when you already have the object
        and just want it tracked by the registry.
        """
        if not name or not isinstance(name, str):
            raise ValueError("service name must be a non-empty string")

        with self._lock:
            self._services[name] = instance
            self._factories[name] = lambda: instance
            self._singleton_flags[name] = True


    def reset(self, clear_factories: bool = False) -> None:
        """Reset the registry.

        Args:
            clear_factories: If True, also remove registered factories
                (full wipe). If False (default), only clear cached instances
                while preserving wiring — this is the common test pattern.
        """
        with self._lock:
            self._services.clear()
            if clear_factories:
                self._factories.clear()
                self._singleton_flags.clear()



_services: Optional[ServiceRegistry] = None
_services_lock = threading.Lock()


def get_services() -> ServiceRegistry:
    """Singleton accessor for the global service registry."""
    global _services
    with _services_lock:
        if _services is None:
            _services = ServiceRegistry()
        return _services


# ---------------------------------------------------------------------------
# Sprint 3 (L99) — Register the 4 controller-split services as lazy singletons.
#
# Each factory runs ONCE on first ``get_services().get("<name>")``. Lazy
# singletons avoid import-time side effects (the provider factory only runs
# when the controller is instantiated), and they keep test isolation easy:
# ``get_services().reset()`` clears the cached instances while preserving
# the wiring.
#
# Workspace + active_provider are bound at ``get()`` time so test patches
# to ``CONFIG["active_provider"]`` + ``os.environ["KOKERTECH_WORKSPACE"]``
# still take effect — the closed-over values are pulled from the current
# process globals, not captured at module import.
# ---------------------------------------------------------------------------

def _history_svc_factory():
    from .history_service import HistoryService
    import os as _os
    inst = HistoryService()
    inst.workspace = _os.getenv("KOKERTECH_WORKSPACE", DEFAULT_WORKSPACE)
    inst.summary_file = _os.path.join(inst.workspace, "context_summary.txt")
    return inst


def _context_svc_factory():
    from .context_service import ContextService
    import os as _os
    return ContextService(workspace=_os.getenv("KOKERTECH_WORKSPACE", DEFAULT_WORKSPACE))


def _prompt_svc_factory():
    from .prompt_service import PromptService
    return PromptService()


def _provider_svc_factory():
    from .provider_service import ProviderService
    import os as _os
    from config import CONFIG
    api_key = _os.getenv("KOKERTECH_API_KEY", "")
    active_provider_name = CONFIG.get("active_provider", "local_llm")
    # L99 Sprint 3 parallel-concerns fix: thread KOKERTECH_WORKSPACE through
    # the factory so tests + production env-overrides steer the provider
    # singleton's workspace. Without this, get_services().reset() would
    # silently regress to the hardcoded r"C:\KokertechAI" class default.
    workspace = _os.getenv("KOKERTECH_WORKSPACE", DEFAULT_WORKSPACE)
    inst = ProviderService(
        api_key=api_key,
        active_provider_name=active_provider_name,
        workspace=workspace,
    )
    # Attach a provider instance so immediate calls don't AttributeError.
    try:
        from ai_base import get_provider
        inst.provider = get_provider(name=active_provider_name, default_model="")
    except (ImportError, RuntimeError, ValueError, OSError, AttributeError) as e:
        _logger.debug(f"Provider attachment failed in _provider_svc_factory (non-fatal): {e}")
    return inst


def _hive_svc_factory():
    from .hive_service import HiveService
    return HiveService()


def _speculative_svc_factory():
    from .speculative_service import SpeculativeDraftEngine
    return SpeculativeDraftEngine()


def _sensory_svc_factory():
    from .sensory_service import SensoryService
    return SensoryService()


def _tool_execution_svc_factory():
    from .tool_execution_service import ToolExecutionService
    import os as _os
    workspace = _os.getenv("KOKERTECH_WORKSPACE", DEFAULT_WORKSPACE)
    return ToolExecutionService(workspace=workspace)


def register_canonical_services() -> None:
    """Register canonical services (idempotent, re-runnable)."""
    get_services().register("history_service", _history_svc_factory, singleton=True)
    get_services().register("context_service", _context_svc_factory, singleton=True)
    get_services().register("prompt_service", _prompt_svc_factory, singleton=True)
    get_services().register("provider_service", _provider_svc_factory, singleton=True)
    get_services().register("hive_service", _hive_svc_factory, singleton=True)
    get_services().register("speculative_service", _speculative_svc_factory, singleton=True)
    get_services().register("sensory_service", _sensory_svc_factory, singleton=True)
    get_services().register("tool_execution_service", _tool_execution_svc_factory, singleton=True)


register_canonical_services()

