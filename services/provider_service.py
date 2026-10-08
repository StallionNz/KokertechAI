"""ProviderService — Sprint 3 (L99).

Owns AI provider instantiation + the inference-path surface that previously
lived on :class:`kokertechController.KokertechController`:

- ``api_key``                     — bearer token (unused for local_llm)
- ``provider``                       — singleton provider (rebinds on fallback)
- ``_active_provider_name``          — current provider name (e.g. ``local_llm``)
- ``_fallback_used``                 — True once a fallback has been promoted
- ``_last_loaded_model``             — None sentinel for first-call, else str

Caches:
- ``_json_cache``                    — path -> {data, mtime, ts}  for app_settings.json / user_identity.json
- ``_json_cache_lock``               — RLock for cache mutations
- ``_json_cache_ttl``                — seconds (default 2.0)
- ``_bias_cache``                    — held here too for the facade's
  ``_get_recent_biases()`` proxy; ContextService owns its own copy.

3-layer inference-safety guards (per KNOWLEDGE.md §3 Decision 8):
- ``_model_input_budget(model_name)``           — Layer B (pre-flight token-budget)
- ``_is_sentinel_response(ai_text)``            — Layer C (post-flight sentinel)
- ``_dynamic_cap`` / ``message_cap``            — Layer A helper (lives in HistoryService)

Provider fallback chain (``_execute_with_fallback``) is the runtime anchor.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Optional

from . import DEFAULT_WORKSPACE
from ai_base import retry_with_backoff, get_circuit_breaker
from logging_config import get_logger

logger = get_logger(name="ProviderService")

# ── Provider fallback chain (per-provider) ────────────────────────────────
# Order in which providers are tried when the primary fails.
_PROVIDER_FALLBACK_CHAIN: dict = {
    "local_llm": [],
}

# Model-aware token budgets. Per Decision 8 Layer B: tag-matched table.
# Specific tags (0.5b) come BEFORE generic (qwen) so a small-context
# model is correctly sized to 1024, not 8192.
_MODEL_INPUT_TOKEN_BUDGETS: tuple = (
    ("qwen2.5-0.5b", 1024),
    ("gemma:2b", 2048),
    ("phi-2", 2048),
    ("tinyllama", 2048),
    ("ministral", 32768),
    ("nemotron", 4096),
    ("qwen3", 32768),
    ("qwen", 8192),
    ("llama3.1", 8192),
    ("llama-3.1", 8192),
    ("mistral", 8192),
    ("phi-3", 4096),
    ("gpt-4", 8192),
    ("claude", 100000),
)
_SAFE_INPUT_MARGIN: float = 0.7

# Sentinel exact matches (case-insensitive whole-string).
_SENTINEL_RESPONSES: tuple = (
    "Yo! What's on the bench today, mate?",
)

# Anchored regex for structured-template "Example N" / "Example N (Title):"
# markers. Start-of-string anchor prevents false-positives on legitimate
# text like "An example query?" or "Example: foo" mid-response.
import re
_SENTINEL_PATTERN = re.compile(r"^\s*Example\s+\d+\b", re.IGNORECASE)


class ProviderService:
    """Owns AI provider + mtime-keyed JSON cache + 3-layer safety guards."""

    def __init__(
        self,
        api_key: str = "",
        active_provider_name: str = "local_llm",
        workspace: str = DEFAULT_WORKSPACE,
    ) -> None:
        self.api_key: str = api_key
        self._active_provider_name: str = active_provider_name
        self.workspace: str = workspace  # mutable attr -- tests override post-construction
        self._fallback_used: bool = False
        self._last_loaded_model: Optional[str] = None

        # Provider instance — facade wires this from controller init via
        # ``provider_factory`` callback so S1's ServiceRegistry resolution
        # can route to ``ai_base.get_provider``.

        # Cached JSON reader state.
        self._json_cache: dict = {}
        self._json_cache_lock = threading.RLock()
        self._json_cache_ttl: float = 2.0

    # ── Provider factory hook ────────────────────────────────────────────

    def attach_provider(self, provider_factory, ai_base_get_provider):
        """Lazy provider attachment. ``ai_base_get_provider`` accepts
        ``ai_base.get_provider(name=...)`` and is rebound for testability.
        """
        self.provider = provider_factory(name=self._active_provider_name)

    # ── Cached JSON read helper (mtime-keyed TTL) ─────────────────────────
    #
    # NOTE (Sprint 19.3): the mtime-keyed TTL algorithm now has a canonical
    # home in ``services/settings_cache_service.py`` (SettingsCacheService),
    # extracted from ``KokertechController`` in v0.21.8. This ProviderService
    # copy predates the extraction and is retained for its own test surface
    # (``tests/test_sprint15_provider.py``) -- the controller delegates to
    # SettingsCacheService, NOT here. Keep both in sync or migrate
    # ProviderService to delegate to SettingsCacheService in a future sprint.

    def read_cached_json(self, path: str, ttl: Optional[float] = None):
        """Read a JSON file with an mtime-keyed TTL cache.

        Thread-safe; file I/O inside the critical section is bounded
        (``app_settings.json`` is ~3KB). Cache key includes file mtime so
        external edits invalidate the entry without explicit calls.
        """
        if ttl is None:
            ttl = self._json_cache_ttl
        if not path or not os.path.exists(path):
            return None
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        now = time.time()
        with self._json_cache_lock:
            cache = self._json_cache.get(path)
            if (
                cache is not None
                and cache["mtime"] == mtime
                and now - cache["ts"] < ttl
            ):
                return cache["data"]
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = __import__("json").load(f)
            except (OSError, __import__("json").JSONDecodeError):
                return None
            self._json_cache[path] = {"data": data, "mtime": mtime, "ts": now}
            return data

    def invalidate_target_model_cache(self, new_model: str) -> None:
        """When the active model name changes, ask ai_base to discard its
        cached provider instance so the next swap picks up the new model.

        ``_last_loaded_model`` is updated even if ``invalidate_provider``
        fails — the inconsistency window is at most one provider call.
        """
        if self._last_loaded_model is not None and new_model != self._last_loaded_model:
            try:
                from ai_base import invalidate_provider
                invalidate_provider()
            except (ImportError, AttributeError, RuntimeError) as e:
                logger.debug(f"invalidate_provider failed in invalidate_target_model_cache (non-fatal): {e}")
        self._last_loaded_model = new_model

    # ── Model lookup (returns the active model's name from settings) ──────

    def load_target_model(self):
        """Returns the target model from ``<self.workspace>/app_settings.json``.

        Side effects:
            - On model-name change, invalidates the ai_base provider cache.
            - Updates self._last_loaded_model.

        Returns the default ```` on missing
        file or empty data.

        IMPORTANT -- workspace comes from ``self.workspace`` (set at
        ``__init__`` or via post-construction attribute assignment), NOT
        from ``os.getenv("KOKERTECH_WORKSPACE")``. The env-var path was
        a silent-regression risk: tests that mutated ``self.workspace``
        post-construction had their changes ignored because the env var
        was read fresh on every call. Same pattern as the v0.18.x
        ``model_lookup`` callable regression -- see KNOWLEDGE.md §3
        Decision 8 Layer A.
        """
        settings_path = os.path.join(self.workspace, "app_settings.json")
        data = self.read_cached_json(settings_path)
        if not data:
            return ""
        new_model = data.get("model_name", "")
        self.invalidate_target_model_cache(new_model)
        return new_model

    # ── Layer B: pre-flight token-budget guard (per Decision 8) ──────────

    def model_input_budget(self, model_name: Optional[str]) -> int:
        """Compute the safe CHARACTER budget for the model's full input.

        More-specific tags (e.g. ``qwen2.5-0.5b``) come BEFORE generic
        (``qwen``) so a small-context model is correctly sized to 1024,
        not 8192. Unknown models get a conservative 4096-token default.
        """
        if not model_name:
            max_tokens = 4096
        else:
            m = model_name.lower()
            max_tokens = 4096  # safe unknown default
            for tag, budget in _MODEL_INPUT_TOKEN_BUDGETS:
                if tag in m:
                    max_tokens = budget
                    break
        return int(max_tokens * 4 * _SAFE_INPUT_MARGIN)

    # ── Layer C: post-flight sentinel detection (per Decision 8) ─────────

    def is_sentinel_response(self, ai_text: Optional[str]) -> bool:
        """Detect 'model echoed its example template' responses.

        Two checks:
            1. Exact-match against known sentinel greetings (case-insensitive).
            2. Anchored regex against ``_SENTINEL_PATTERN`` (``^\\s*Example\\s+\\d+\\b``).

        The regex anchor (``^\\s*``) prevents false-positives on legitimate
        text like ``An example query?`` or ``For example, ...`` mid-sentence.
        Runs at least twice per ``process_input`` (raw + parsed-final)
        for defense-in-depth (locked by
        ``test_defense_in_depth_wrapped_xml_sentinel``).
        """
        if not ai_text:
            return False
        stripped = ai_text.strip()
        for sentinel in _SENTINEL_RESPONSES:
            if stripped.lower() == sentinel.lower():
                return True
        if _SENTINEL_PATTERN.match(stripped):
            return True
        return False

    # ── Provider fallback chain ──────────────────────────────────────────

    def execute_with_fallback(
        self,
        messages,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
        log,
        cancel_event=None,
        cached_chat_completion_fn=None,
        provider_lookup_fn=None,
        provider_override=None,
    ):
        """Execute a chat completion with retry + backoff + provider fallback.

        Sprint 15 Stream B: Each provider in the fallback chain is attempted
        with exponential backoff and jitter (via retry_with_backoff). If all
        retries for a provider are exhausted, the next provider in the chain
        is tried. The circuit breaker gates entry to each attempt.

        Args:
            messages: List[dict] — the messages list (system+history+user).
            model:    str — the target model name.
            temperature / max_tokens / timeout: per-call params.
            log: callable (str) -> None — UI log hook.
            cancel_event: Optional[threading.Event] — checked at top of each
                iteration; mid-flight HTTP cancellation is forwarded to provider.
            cached_chat_completion_fn: callable (provider, messages, ...) -> dict.
            provider_lookup_fn: callable (name: str) -> provider.

        Returns:
            Tuple (result_dict, provider_name_used). On all-providers-failed,
            ``result_dict = {"error": "..."}`` and ``provider_name_used = None``.
        """
        chain = [self._active_provider_name] + _PROVIDER_FALLBACK_CHAIN.get(
            self._active_provider_name, []
        )
        last_error = None

        # Late-bind default provider lookup from ai_base (so tests can
        # patch ``kokertechController.get_provider``).
        if cached_chat_completion_fn is None:
            from ai_base import cached_chat_completion
            cached_chat_completion_fn = cached_chat_completion
        if provider_lookup_fn is None:
            from ai_base import get_provider as _lookup
            provider_lookup_fn = _lookup

        cb = get_circuit_breaker()
        for idx, fallback_name in enumerate(chain):
            if cancel_event is not None and cancel_event.is_set():
                return {"error": "Cancelled by user"}, None
            if idx > 0:
                log(f"\U0001F504 Fallback: trying {fallback_name}...")

            # Circuit breaker gate: skip this provider if breaker is open.
            can_request = True
            if hasattr(cb, 'allow_request'):
                can_request = cb.allow_request()
            elif hasattr(cb, 'is_open'):
                can_request = not cb.is_open

            if not can_request:
                remaining_fn = getattr(cb, 'remaining_cooldown', None)
                remaining = remaining_fn() if callable(remaining_fn) else 0.0
                last_error = f"Circuit breaker open ({remaining:.0f}s remaining)"
                log(f"\u26d4 Circuit breaker open for {fallback_name}, skipping")
                continue

            try:
                if idx == 0:
                    provider = provider_override if provider_override is not None else self.provider
                else:
                    provider = provider_lookup_fn(name=fallback_name)

                # Sprint 15 Stream B: retry with exponential backoff per provider.
                _p = provider

                def _attempt():
                    return cached_chat_completion_fn(
                        _p,
                        messages=messages,
                        model=model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=timeout,
                        request_id=None,
                        cancel_event=cancel_event,
                    )

                def _on_retry(attempt, delay_ms, exc):
                    log(f"\u23f3 Retry {attempt} for {fallback_name} in {delay_ms:.0f}ms: {exc}")

                result = retry_with_backoff(_attempt, on_retry=_on_retry, cancel_event=cancel_event)
                cb.record_success()
                if result.get("error"):
                    last_error = result["error"]
                    cb.record_failure()
                    if idx == 0:
                        return {"error": result["error"]}, None
                    continue
                if idx > 0:
                    self._active_provider_name = fallback_name
                    self.provider = provider
                    self._fallback_used = True
                    log(f"\u2705 Switched to fallback provider: {fallback_name}")
                return result, fallback_name
            except __import__("requests").ConnectionError as e:
                last_error = str(e)
                cb.record_failure()
                continue
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
                last_error = str(e)
                cb.record_failure()
                if idx == 0:
                    return {"error": f"Provider failed: {last_error}"}, None
                continue

        return {"error": f"All providers failed. Last error: {last_error}"}, None

    # ── VRAM / cache-clear utilities ─────────────────────────────────────

    def get_vram_usage(self) -> int:
        """Run ``nvidia-smi`` and return MB of used VRAM (0 on error).

        Sprint 19.8 dedup: delegates to the canonical
        ``utils.gpu.get_vram_usage`` probe (list-args + CREATE_NO_WINDOW,
        never shell=True). Same 0-on-error contract; tests patching
        ``subprocess.check_output`` remain authoritative.
        """
        from utils.gpu import get_vram_usage as _probe
        return _probe()

    def clear_api_cache(self) -> None:
        """Clear the AI provider's in-memory model cache (best-effort).

        For LocalLLMProvider this unloads the model so the next request
        triggers a fresh load.  For remote providers this is a no-op.
        """
        try:
            provider = getattr(self, "provider", None)
            if provider is not None and hasattr(provider, "unload_model"):
                provider.unload_model()
        except (RuntimeError, OSError, AttributeError, TypeError) as e:
            logger.debug(f"clear_api_cache failed (non-fatal): {e}")

    # ── Sprint 15: Provider health check ──

    def provider_health_check(self) -> dict:
        """Run a health check on the active provider and return diagnostics.

        Returns a dict with: ok, provider, model_loaded, model_name,
        n_ctx, n_gpu_layers, vram_used_mb, latency_ms.
        Delegates to the provider's own health_check() method when available.
        """
        provider = getattr(self, "provider", None)
        if provider is not None and hasattr(provider, "health_check"):
            try:
                return provider.health_check()
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
                return {"ok": False, "error": str(e),
                        "provider": self._active_provider_name}
        return {"ok": False, "error": "No provider attached",
                "provider": self._active_provider_name}

    # ── Sprint 15: Model hot-swap without restart ──

    def hot_swap_model(self, new_model_file: str, n_ctx: int = 0, progress=None, **kwargs) -> dict:
        """Hot-swap the active model without restarting the application.

        Unloads the current model, loads the new one, and updates
        CONFIG["model_file"] so the swap persists across restarts.
        Invalidates the ai_base provider cache so subsequent calls
        pick up the new model.

        Parameters
        ----------
        new_model_file : str
            Model file to load (resolved against the provider's models_dir).
        n_ctx : int, optional
            Override context window size in tokens when > 0.
        progress : callable, optional
            Zero-arg callback receiving human-readable load-progress
            strings; forwarded to the provider for verbose reporting
            (e.g. into the dashboard audit log).

        Returns a dict with timing and status info.
        """
        provider = getattr(self, "provider", None)
        if provider is None or not hasattr(provider, "swap_model"):
            return {"ok": False, "error": "Provider does not support hot-swap",
                    "provider": self._active_provider_name}
        try:
            from config import CONFIG
            cfg_dir = CONFIG.get("models_dir")
            if cfg_dir and hasattr(provider, "models_dir"):
                provider.models_dir = cfg_dir
            swap_kwargs = {}
            if n_ctx > 0:
                swap_kwargs["n_ctx"] = n_ctx
            if progress is not None:
                swap_kwargs["progress"] = progress
                try:
                    progress("⏳ Unloading current model...")
                except (RuntimeError, TypeError, ValueError, OSError, AttributeError) as err:
                    logger.debug(f"Progress callback error: {err}")
            result = provider.swap_model(new_model_file, **swap_kwargs)
            if result.get("ok"):
                from config import CONFIG
                CONFIG["model_file"] = new_model_file
                CONFIG["model_name"] = new_model_file
                # Ensure the provider cache is invalidated even on
                # first hot-swap (invalidate_target_model_cache skips
                # when _last_loaded_model is None).
                self._last_loaded_model = new_model_file
                try:
                    from ai_base import invalidate_provider
                    invalidate_provider()
                except (ImportError, AttributeError, RuntimeError) as e:
                    logger.debug(f"invalidate_provider failed after hot_swap (non-fatal): {e}")
            return result
        except (RuntimeError, ValueError, OSError, TypeError, AttributeError) as e:
            return {"ok": False, "error": str(e),
                    "provider": self._active_provider_name}
