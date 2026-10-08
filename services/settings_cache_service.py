"""settings_cache_service.py -- Sprint 19.3: mtime-keyed JSON cache + profile/model loaders.

Extracted from ``KokertechController._read_cached_json`` /
``_load_user_profile`` / ``_load_target_model`` in Sprint 19.3.
Decouples the cached-JSON file-read logic (``app_settings.json`` /
``user_identity.json``) from the main facade so future cache-policy work
(TTL tuning, multi-workspace caches, mtime watchers) can live here without
touching ``kokertechController.py``.

LAZY-SINGLETON PATTERN (per ``KNOWLEDGE.md`` section 10 R12 + ``SkillService``
precedent):

- ``_SETTINGS_CACHE_SERVICE_SINGLETON`` + ``_SETTINGS_CACHE_SERVICE_INIT_LOCK``
  at module level.
- Public accessor: ``get_settings_cache_service()`` returns the cached singleton.
- Reset helper: ``_reset_settings_cache_service_for_tests()`` clears the
  module-level cache under the init lock (NEVER touches ``registry._lock``).

STATE-OWNERSHIP CONTRACT (why ``cache`` / ``cache_lock`` are overridable):

The facade mirrors ``_json_cache`` / ``_json_cache_lock`` / ``_json_cache_ttl``
attributes, and TESTS rebind them (e.g. ``ctrl._json_cache = {}``) to get a
fresh cache per test. Methods therefore accept optional ``cache`` /
``cache_lock`` parameters that default to the service's own state -- the
facade passes its mirrored (possibly rebound) dict through so mutations land
where the tests expect. The service's own state is the production default.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

from logging_config import get_logger
from services import get_services

logger = get_logger(name="SettingsCacheService")

# Default model name used when app_settings.json is missing / has no model_name.
_DEFAULT_MODEL = "Lexi-Llama-3-8B-Uncensored_Q4_K_M"
# Default identity string when user_identity.json is missing / empty.
_DEFAULT_PROFILE = "No specific personal identity layer configured yet."


class SettingsCacheService:
    """Own the mtime-keyed TTL cache + profile/model loaders for the facade."""

    def __init__(self, workspace: str = r"C:\KokertechAI") -> None:
        self.workspace: str = workspace
        self._json_cache: dict = {}
        self._json_cache_lock = threading.RLock()
        self._json_cache_ttl: float = 2.0
        self.logger = get_logger(name="SettingsCacheService")

    def read_cached_json(
        self,
        path: str,
        ttl: Optional[float] = None,
        cache: Optional[dict] = None,
        cache_lock: Optional[threading.RLock] = None,
    ):
        """Read a JSON file with an mtime-keyed TTL cache.

        Avoids re-parsing app_settings.json and user_identity.json on every
        turn. Cache is keyed on file mtime so external edits invalidate it
        without explicit calls.

        Thread-safe: the full check + read + write cycle happens under
        ``cache_lock`` (defaults to ``self._json_cache_lock``). This prevents
        a write race where two threads could each read the same mtime, do
        file I/O, and then race to write -- potentially clobbering a newer
        cache entry with a stale one. File I/O here is small (settings.json
        is ~3KB) so the critical section is short.
        """
        if ttl is None:
            ttl = self._json_cache_ttl
        if cache is None:
            cache = self._json_cache
        if cache_lock is None:
            cache_lock = self._json_cache_lock
        if not path or not os.path.exists(path):
            return None
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        now = time.time()
        with cache_lock:
            entry = cache.get(path)
            if (
                entry is not None
                and entry["mtime"] == mtime
                and now - entry["ts"] < ttl
            ):
                return entry["data"]
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError, TypeError):
                return None
            cache[path] = {"data": data, "mtime": mtime, "ts": now}
            return data

    def load_user_profile(
        self,
        cached_json_reader,
        workspace: Optional[str] = None,
    ) -> str:
        """Load user_identity.json with TTL cache (returns formatted string).

        ``cached_json_reader`` is a callable ``(path: str) -> Optional[dict]``
        -- the facade passes its own ``_read_cached_json`` (which tests
        patch). Mirrors ``ContextService.load_user_profile`` semantics.
        """
        base = workspace if workspace is not None else self.workspace
        pil_path = os.path.join(base, "user_identity.json")
        data = cached_json_reader(pil_path)
        if not data:
            return _DEFAULT_PROFILE
        return (
            f"Preferred Name: {data.get('name', 'Jacques')}\n"
            f"Tone Preferences: {data.get('tone', 'Technical & Direct')}\n"
        )

    def load_target_model(
        self,
        cached_json_reader,
        last_loaded_model: Optional[str] = None,
        workspace: Optional[str] = None,
    ) -> tuple:
        """Load model_name from app_settings.json with TTL cache.

        Returns ``(model_name, updated_last_loaded_model)``. Invalidates the
        ai_base provider cache when the model name changes, so live model
        swaps in Settings take effect on the next turn (otherwise the cached
        provider would keep using the old model).

        ``cached_json_reader`` is a callable ``(path: str) -> Optional[dict]``
        -- the facade passes its own ``_read_cached_json`` (which tests
        patch). ``last_loaded_model`` is passed in and the updated value
        returned so the facade keeps its mirrored attribute authoritative
        (tests set ``ctrl._last_loaded_model`` directly).
        """
        base = workspace if workspace is not None else self.workspace
        settings_path = os.path.join(base, "app_settings.json")
        data = cached_json_reader(settings_path)
        if not data:
            return _DEFAULT_MODEL, last_loaded_model
        new_model = data.get("model_name", _DEFAULT_MODEL)
        # Invalidate stale provider cache if the model has changed.
        # Late import so tests patching ``ai_base.invalidate_provider``
        # observe the mock (module-level import would bind the real fn).
        if last_loaded_model is not None and new_model != last_loaded_model:
            try:
                from ai_base import invalidate_provider
                invalidate_provider()  # clear all -- simplest, infrequent
            except (ImportError, AttributeError, RuntimeError) as e:
                logger.debug(
                    f"invalidate_provider failed in load_target_model "
                    f"(non-fatal): {e}"
                )
        return new_model, new_model


# Module-level lazy singleton + init lock (matches SkillService /
# CodeIntelligenceFactory pattern per ``KNOWLEDGE.md`` section 10 R12).
_SETTINGS_CACHE_SERVICE_SINGLETON: Optional[SettingsCacheService] = None
_SETTINGS_CACHE_SERVICE_INIT_LOCK = threading.Lock()


def get_settings_cache_service() -> SettingsCacheService:
    """Eager-construct + register under ``ServiceRegistry`` (lazy singleton)."""
    global _SETTINGS_CACHE_SERVICE_SINGLETON
    with _SETTINGS_CACHE_SERVICE_INIT_LOCK:
        if _SETTINGS_CACHE_SERVICE_SINGLETON is None:
            _SETTINGS_CACHE_SERVICE_SINGLETON = SettingsCacheService()
            get_services().register_instance(
                "settings_cache_service", _SETTINGS_CACHE_SERVICE_SINGLETON,
            )
        return _SETTINGS_CACHE_SERVICE_SINGLETON


def _reset_settings_cache_service_for_tests() -> None:
    """Reset module-level singleton under init lock (NEVER touches registry._lock).

    NB: leaves the stale ``register_instance`` entry in ServiceRegistry (the
    factory lambda still returns the old object) -- the getter re-registers
    on the next call. Same precedent as ``SkillService``; callers should use
    ``get_settings_cache_service()``, never ``get_services().get(...)`` after
    a reset.
    """
    global _SETTINGS_CACHE_SERVICE_SINGLETON
    with _SETTINGS_CACHE_SERVICE_INIT_LOCK:
        _SETTINGS_CACHE_SERVICE_SINGLETON = None
