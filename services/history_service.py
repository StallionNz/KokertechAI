"""HistoryService — Sprint 3 (L99).

Owns the in-process conversation state that previously lived directly on
:class:`kokertechController.KokertechController`:

- ``history``              — list of {role, content} messages (bounded)
- ``_lock``                — threading.Lock guarding history mutations
- ``compressed_context_summary`` — rolling summary text persisted to
  ``<workspace>/context_summary.txt``
- ``summary_file``         — absolute path of that text file
- ``memory_limit``         — soft limit (history grows to 2× before eviction)
- ``workspace``            — base dir for vault / summary files
- ``_large_context_tags``  — model tags eligible for 4x truncation cap

Methods that move into the service:
- ``load_summary()``           — read summary_file, return contents (or "")
- ``wipe_memory()``            — clear history + summary + delete summary_file
- ``append_history()``         — append under lock (role, content)
- ``pop_last_user()``          — drop last message if it was a user prompt
- ``history_snapshot()``       — return a thread-safe shallow copy of history
- ``summarize_and_evict(log, provider_svc, episodic_store, session_id_fn, model_lookup)``  — eviction + summary pipeline (model_lookup MUST be supplied to preserve the 4x scaling cap on large-context models)
- ``dynamic_cap(model_name, base)``           — model-aware cap multiplier
- ``message_cap(base=1500)``                  — dynamic_cap + active model
- ``truncate(text, cap=1500)``                — tail-keeping slice
- ``shutdown()``                              — idempotent cleanup hook

Compatibility: the facade (:class:`kokertechController.KokertechController`)
re-exports these as thin delegations + @property forwards so the existing
~900 tests + 3 interface layers work unchanged.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from typing import Optional

from logging_config import get_logger
from . import DEFAULT_WORKSPACE

logger = get_logger(name="HistoryService")


# Tags of models known to have large context windows. Matched case-insensitively.
# Identical to the controller's pre-Sprint-3 constant — this is the model-aware
# eviction cap (KNOWLEDGE.md §3 Decision 8 Layer A).
_LARGE_CONTEXT_TAGS = (
    "qwen", "llama3.1", "llama-3.1", "gpt-4o", "gpt-4-turbo", "claude-3", "claude-sonnet",
)

try:
    from prompt_builder import SUMMARIZATION_SYSTEM_PROMPT as _DEFAULT_SUMMARIZATION_PROMPT
except (ImportError, AttributeError):
    _DEFAULT_SUMMARIZATION_PROMPT = (
        "Provide a highly compressed summary of the following conversation. "
        "Focus on facts, actions taken, and decisions. Return ONLY the summary text."
    )
SUMMARIZATION_SYSTEM_PROMPT = _DEFAULT_SUMMARIZATION_PROMPT


class HistoryService:
    """Owns conversation state, eviction, summarization, truncation caps."""

    def __init__(self) -> None:
        self.history: list = []
        self._lock = threading.Lock()
        self.compressed_context_summary: str = ""
        # Default workspace — __init__ callers may overwrite via attr set
        # (the facade wires this before the first eviction pass).
        self.workspace: str = DEFAULT_WORKSPACE
        self.summary_file: str = os.path.join(self.workspace, "context_summary.txt")
        self.memory_limit: int = 2
        self._large_context_tags = _LARGE_CONTEXT_TAGS

    # -- Lifecycle ---------------------------------------------------------

    def shutdown(self) -> None:
        """Idempotent — currently nothing to release, but present for parity
        with the controller's pre-Sprint-3 API."""
        return None

    # -- History bookmarks -------------------------------------------------

    def append_history(self, role: str, content: str) -> None:
        """Append a message under the lock."""
        with self._lock:
            self.history.append({"role": role, "content": content})

    def pop_last_user(self) -> None:
        """Pop the last message if it was a user-role prompt.

        Used by the facade's 3-layer inference-safety guards (KNOWLEDGE.md §3
        Decision 8) — when pre-flight or post-flight check fails, the just-
        appended user message is popped so the controller state stays clean
        for the next turn.
        """
        with self._lock:
            if self.history and self.history[-1]["role"] == "user":
                self.history.pop()

    def history_snapshot(self) -> list:
        """Thread-safe shallow copy of history (per LOCK-11 contract)."""
        with self._lock:
            return list(self.history)

    # -- Summary file ------------------------------------------------------

    def load_summary(self) -> str:
        """Read summary_file. Returns "" if missing or unreadable."""
        if not os.path.exists(self.summary_file):
            return ""
        try:
            with open(self.summary_file, "r", encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            logger.debug(f"load_summary read failed (graceful fallback): {e}")
            return ""

    def write_summary(self, summary: str) -> None:
        """Persist current compressed_context_summary to disk."""
        try:
            with open(self.summary_file, "w", encoding="utf-8") as f:
                f.write(self.compressed_context_summary)
        except OSError as e:
            logger.warning(f"write_summary failed (summary not persisted): {e}")

    # -- Truncation caps (model-aware) -------------------------------------

    def dynamic_cap(self, model_name: str, base_cap: int) -> int:
        """Scale a cap based on the model's context window.

        Models matching ``_large_context_tags`` get 4x; everything else
        returns ``base_cap`` unchanged. Empty/None model also returns base.

        Identical to the controller's pre-Sprint-3 method — the facade
        delegates.
        """
        if not model_name:
            return base_cap
        m = model_name.lower()
        if any(tag in m for tag in self._large_context_tags):
            return base_cap * 4
        return base_cap

    def message_cap(self, model_lookup, base: int = 1500) -> int:
        """Return dynamic_cap for the active model.

        ``model_lookup`` is a callable (model_name: str) -> int, the
        facade accepts either the controller's ``_load_target_model`` or
        a stub. Failure falls back to ``base`` (preserving the test
        contract in ``TestTruncate.test_message_cap_falls_back_*``).
        """
        try:
            return self.dynamic_cap(model_lookup(), base)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug(f"message_cap fell back to base {base}: {e}")
            return base

    def truncate(self, text: Optional[str], cap: int = 1500) -> Optional[str]:
        """Truncate text from the head, preserving the tail.

        Behavior contract (locked by ``TestTruncate.*``):
            - ``len(text) <= cap``        -> unchanged.
            - ``text is None``            -> unchanged.
            - else                        -> ``'...[TRUNCATED] ' + text[-cap:]``.

        IMPORTANT — pure function with no dynamic cap lookup. Callers
        wanting the model-aware cap MUST pre-compute it via
        :meth:`message_cap` + pass the result as ``cap``.
        """
        if not text or len(text) <= cap:
            return text
        return "...[TRUNCATED] " + text[-cap:]

    # -- Eviction + summarization -------------------------------------------

    def summarize_and_evict(
        self,
        log,
        provider_svc,
        episodic_store,
        session_id_fn,
        model_lookup,
    ) -> None:
        """History eviction + AI summarization pipeline.

        Args:
            log: callable (str) -> None (UI log callback).
            provider_svc: :class:`ProviderService` providing
                ``execute_with_fallback`` + ``build_summarization_prompt``.
            episodic_store: callable storing an episodic summary
                (from ``memory_vault.store_episodic``).
            session_id_fn: callable returning the current session id
                (from ``memory_vault.get_current_session_id``).
            model_lookup: callable () -> str returning the active model
                name. MUST be supplied — without it the eviction cap
                silently regresses to the base 1500 chars on large-context
                models (qwen / llama3.1 / gpt-4o / claude-3 / claude-sonnet
                / gpt-4-turbo), losing the 4x scaling from KNOWNLEDGE.md
                §3 Decision 8 Layer A. The facade wires this to
                ``self._load_target_model``.

        Behavior contract (locked by
        ``TestControllerSummarizeAndEvict.*``):
            - If ``len(history) < memory_limit * 2`` -> no-op.
            - Else: evict all but last 2, call provider for summary,
              append to summary + persist + episodic store.
            - On provider error -> log + return without persisting.
            - Cap eviction text to ``message_cap(1500)`` (model-aware
              via ``model_lookup``).

        Raises:
            TypeError: if ``model_lookup`` is missing (None) or
                non-callable, with a diagnostic message that names the
                canonical wiring source (``_load_target_model``) and lists
                the impacted model tags. The v0.18.x eviction-cap
                regression silently regressed to base 1500 chars on
                large-context models (qwen / llama3.1 / gpt-4o /
                claude-3 / claude-sonnet / gpt-4-turbo) when a no-op
                model_lookup was passed. This guard makes the regression
                fail catastrophically at entry instead of leaking an
                opaque ``TypeError: 'NoneType' object is not callable``
                deep inside ``message_cap()``. The legacy try/except
                inside ``message_cap()`` is now reserved for legitimate
                runtime failures (e.g. ``_load_target_model`` raising
                while reading ``app_settings.json``), not for caller
                mistakes. See KNOWLEDGE.md §3 Decision 8 Layer A.
        """
        # Fail-fast guard: surface missing/non-callable model_lookup with a
        # diagnostic message that names the canonical wiring source and the
        # impacted model tags. Uses an explicit ``raise TypeError`` (NOT
        # ``assert``) so the guard survives ``python -O`` and PyInstaller
        # ``--optimize`` -- ``assert`` would be stripped in those
        # configurations and the regression would land back as an opaque
        # ``TypeError: 'NoneType' object is not callable`` from inside
        # ``message_cap()``. Without this fail-fast, the silent regression
        # to 1500 cap on large-context models wasted hours of triage in
        # v0.18.x.
        if not callable(model_lookup):
            raise TypeError(
                "summarize_and_evict requires a callable model_lookup "
                "(typically KokertechController._load_target_model). Got "
                f"{type(model_lookup).__name__} = {model_lookup!r}. The "
                "v0.18.x eviction-cap regression silently regressed to "
                "1500 chars on large-context models (qwen / llama3.1 / "
                "gpt-4o / claude-3 / claude-sonnet / gpt-4-turbo) when a "
                "no-op or missing model_lookup was passed. This guard "
                "fails that regression loudly at entry. See KNOWLEDGE.md "
                "\u00a73 Decision 8 Layer A for the model-aware 4x cap "
                "contract."
            )

        with self._lock:
            if len(self.history) < (self.memory_limit * 2):
                return
            evicted = self.history[:-2]
            self.history = self.history[-2:]

        log("\U0001F9E0 [MEMORY SYSTEM] Evicting old messages. Generating context summary...")

        text_to_summarize = "\n".join(
            [f"{m['role']}: {m['content']}" for m in evicted]
        )

        # Hard-cap the eviction text to prevent 400 Bad Request.
        # Cap is model-aware: 4x scaling for qwen / llama3.1 / gpt-4o /
        # claude-3 / claude-sonnet / gpt-4-turbo via dynamic_cap().
        # The pre-Sprint-3 regression (always-1500 even on large-context
        # models) is fixed by threading model_lookup instead of using a
        # static placeholder.

        eviction_cap = self.message_cap(model_lookup, 1500)
        if len(text_to_summarize) > eviction_cap:
            text_to_summarize = "...[OLDER MESSAGES TRUNCATED] " + text_to_summarize[-eviction_cap:]

        # Sprint 14.4: Skip LLM call when evicted content is trivially short.
        # This avoids a full round-trip (~200-500ms) for small evictions.
        # Gated by CONFIG['summarize_evict_skip_recent'] (default True).
        # Fix 1.4: Threshold is now configurable via CONFIG['summarize_evict_threshold'].
        # Fix 2.11: Normalize whitespace for accurate length measurement.
        try:
            from config import CONFIG
            _skip_enabled = CONFIG.get("summarize_evict_skip_recent", True)
            _SHORT_THRESHOLD = CONFIG.get("summarize_evict_threshold", 200)
        except ImportError:
            _skip_enabled = True
            _SHORT_THRESHOLD = 200
        _normalized_len = len(text_to_summarize.strip().replace('\n', ' ').replace('\r', ''))
        if _skip_enabled and _normalized_len < _SHORT_THRESHOLD:
            quick_summary = text_to_summarize.strip()[:200]
            self.compressed_context_summary += f"\n- {quick_summary}"
            self.write_summary(quick_summary)
            try:
                episodic_store(
                    summary=quick_summary,
                    session_id=session_id_fn(),
                    tags=["auto-summary", "quick"],
                    metadata={"source": "summarize_and_evict", "message_count": len(evicted), "quick": True},
                    importance=3,
                )
                log("\U0001F4D3 Episodic journal: stored quick summary (short eviction)")
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as ej_err:
                log(f"\u26A0\ufe0f Episodic storage failed: {ej_err}")
            return

        # Static default — kept identical to controller's pre-Sprint-3 fallback
        # if app_settings.json can't be read.
        target_model = ""
        settings_path = os.path.join(self.workspace, "app_settings.json")
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    target_model = cfg.get("model_name", target_model)
            except (OSError, json.JSONDecodeError, ValueError, KeyError) as e:
                logger.debug(f"app_settings.json read failed in summarize_and_evict (graceful fallback): {e}")

        try:
            result, _ = provider_svc.execute_with_fallback(
                messages=[
                    {
                        "role": "system",
                        "content": provider_svc.build_summarization_prompt() if hasattr(provider_svc, 'build_summarization_prompt') else _DEFAULT_SUMMARIZATION_PROMPT,
                    },
                    {"role": "user", "content": text_to_summarize},
                ],
                model=target_model,
                temperature=0.1,
                max_tokens=150,
                timeout=120,
                log=log,
            )
            if result.get("error"):
                # Ensure unit tests can detect failure via substring "failed".
                log(f"\u26A0\ufe0f Memory summarization failed: {result['error']}")
                return
            summary = result["content"]
            summary = re.sub(
                r'<(?:thinking|thought)>.*?</(?:thinking|thought)>',
                "",
                summary,
                flags=re.DOTALL,
            ).strip()
            self.compressed_context_summary += f"\n- {summary}"
            self.write_summary(summary)

            # Tier 2: episodic journal.
            try:
                episodic_store(
                    summary=summary,
                    session_id=session_id_fn(),
                    tags=["auto-summary", "conversation"],
                    metadata={"source": "summarize_and_evict", "message_count": len(evicted)},
                    importance=5,
                )
                log("\U0001F4D3 Episodic journal: stored conversation summary")
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as ej_err:
                log(f"\u26A0\ufe0f Episodic storage failed: {ej_err}")
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
            log(f"\u26A0\ufe0f Memory summarization failed: {e}")

    # -- Wipe (process command from UI) ------------------------------------

    def wipe_memory(self, clear_api_cache_fn) -> str:
        """Clear in-process + on-disk history state.

        Returns a status string for the UI. ``clear_api_cache_fn`` is the
        provider-side cache-clear (the facade passes
        :meth:`ProviderService.clear_api_cache`).
        """
        with self._lock:
            self.history.clear()
            self.compressed_context_summary = ""
        if os.path.exists(self.summary_file):
            try:
                os.remove(self.summary_file)
            except OSError as e:
                logger.debug(f"wipe_memory summary file removal failed (graceful fallback): {e}")
        clear_api_cache_fn()
        return "Short-term history wiped. VRAM recycled."


