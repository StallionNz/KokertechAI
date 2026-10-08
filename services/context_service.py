"""ContextService — Sprint 3 (L99).

Owns the heavy I/O + parallel memory-fetch concerns that previously lived
on :class:`kokertechController.KokertechController`:

- ``workspace``                — base dir
- ``rag_engine``               — AgenticRAGEngine instance
- ``_context_pool``            — reusable ThreadPoolExecutor (5 workers)
- ``_bias_cache``              — TTL-keyed cache for ``_get_recent_biases()``

Methods that move into the service:
- ``fetch_components(text, agent_type, episodic_limit=3)``
- ``format_biases(agent_type)``
- ``format_growth_arc(agent_type)``
- ``get_recent_biases()``              — SQLite bias_ledger reader
- ``get_recent_biases_inline()``       — formatted string for system prompt
- ``load_user_profile(model_lookup=None)`` — formatted identity string
- ``shutdown()``                       — flush + close thread pool

Test compatibility: ``test_fetch_context_components.*`` patches
``memory_vault.semantic_search`` / ``get_recent_bias`` / etc. — the
service imports ``memory_vault`` at call time so conftest's
``patch.multiple('memory_vault', ...)`` still works.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from logging_config import get_logger

logger = get_logger(name="ContextService")


class ContextService:
    """Owns parallel memory fetch + RAG engine + bias/growth lookups."""

    def __init__(self, workspace: str) -> None:
        self.workspace: str = workspace
        self.rag_engine = None  # facade wires this from controller init
        self._context_pool: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=5, thread_name_prefix="ctx-fetch"
        )
        self._bias_cache: dict = {"data": "", "ts": 0.0}
        self._bias_cache_ttl: float = 5.0

    # -- Lifecycle ---------------------------------------------------------

    def shutdown(self) -> None:
        """Release the reusable ThreadPoolExecutor.

        Mirrors the controller's pre-Sprint-3 ``shutdown`` — safe against
        double-invoke (RuntimeError swallowed).
        """
        try:
            self._context_pool.shutdown(wait=False, cancel_futures=True)
        except RuntimeError:
            # Deliberate typed fallback (NOT a silent broad catch):
            # ThreadPoolExecutor.shutdown() raises RuntimeError on double
            # invoke — idempotent close is the contract, see method docstring.
            pass

    # -- Parallel fetch ----------------------------------------------------

    def fetch_components(
        self,
        text: str,
        agent_type: str,
        episodic_limit: int = 3,
        semantic_search_fn=None,
        session_context_fn=None,
        episodic_context_fn=None,
    ) -> dict:
        """Run 5 independent memory lookups in parallel.

        Returns a dict with keys:
            ``context`` (semantic_search first hit content or fallback)
            ``scbe_text`` (formatted biases)
            ``growth_text`` (formatted growth arc)
            ``episodic_context`` (session-context joined text)
            ``weighted_episodic`` (weighted episodic-context text)

        Each ``*_fn`` accepts ``memory_vault.<fn>`` (rebound for testability
        via :data:`conftest`'s ``patch.multiple('memory_vault', ...)``).
        Default-callable indirection is preserved so the existing
        ``memory_vault`` mocks remain authoritative.
        """
        components: dict = {}

        # Late imports so test patches to memory_vault.* resolve correctly.
        import memory_vault

        # Indirection: defaults route to memory_vault.* unless the service
        # was constructed with explicit dependencies.
        if semantic_search_fn is None:
            semantic_search_fn = memory_vault.semantic_search
        if session_context_fn is None:
            session_context_fn = memory_vault.get_session_context
        if episodic_context_fn is None:
            episodic_context_fn = memory_vault.get_episodic_context

        def _semantic():
            try:
                docs = semantic_search_fn(text, top_k=1)
                return ("context", docs[0][2] if docs else "No prior history found.")
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
                logger.debug(f"semantic_search failed in fetch_components (graceful fallback): {e}")
                return ("context", "No prior history found.")

        def _biases():
            return ("scbe_text", self.format_biases(agent_type))

        def _growth():
            return ("growth_text", self.format_growth_arc(agent_type))

        def _session():
            try:
                return ("episodic_context", session_context_fn(limit=episodic_limit))
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
                logger.debug(f"get_session_context failed in fetch_components (graceful fallback): {e}")
                return ("episodic_context", "No episodic journal available.")

        def _weighted():
            try:
                return (
                    "weighted_episodic",
                    episodic_context_fn(limit=episodic_limit, session_id=None, query=text),
                )
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
                logger.debug(f"get_episodic_context failed in fetch_components (graceful fallback): {e}")
                return ("weighted_episodic", "No episodic journal available.")

        futures = [
            self._context_pool.submit(_semantic),
            self._context_pool.submit(_biases),
            self._context_pool.submit(_growth),
            self._context_pool.submit(_session),
            self._context_pool.submit(_weighted),
        ]
        for f in futures:
            name, value = f.result()
            components[name] = value
        return components

    # -- Bias / growth formatting ------------------------------------------

    def format_biases(self, agent_type: str) -> str:
        """Format recent biases for the SCBE section.

        Reads ``memory_vault.get_recent_bias(agent_type)`` (late-bound so
        tests can patch it). Returns ``"No bias drift recorded yet."`` on
        empty OR on exception (locked by
        :func:`test_fetch_context_components.test_format_biases_handles_*`).
        """
        try:
            import memory_vault

            biases = memory_vault.get_recent_bias(agent_type)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug(f"get_recent_bias failed in format_biases (graceful fallback): {e}")
            biases = []
        if not biases:
            return "No bias drift recorded yet."
        return "\n".join(
            [
                f"- [{b['timestamp']}] {b['bias_type']} (Confidence: {b['confidence_score']}%): {b['description']}"
                for b in biases
            ]
        )

    def format_growth_arc(self, agent_type: str) -> str:
        """Format growth arc events for the timeline section.

        Returns ``"No growth events recorded yet."`` on empty OR exception.
        """
        try:
            import memory_vault

            growth = memory_vault.get_growth_arc(agent_type)
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug(f"get_growth_arc failed in format_growth_arc (graceful fallback): {e}")
            growth = []
        if not growth:
            return "No growth events recorded yet."
        return "\n".join(
            [
                f"- [{g['timestamp']}] {g['event_description']} (Energy Shift: {g['energy_shift']}%)"
                for g in growth
            ]
        )

    # -- Inline SCBE section for system prompt -----------------------------

    def get_recent_biases_inline(self) -> str:
        """Return ``[RECENT BIASES TO AVOID (Self-Correction)]`` block.

        Cached for ``_bias_cache_ttl`` seconds. The block is only emitted
        in :meth:`get_recent_biases_inline` if the cache has data. The
        :func:`kokertechController` facade appends it to the system prompt
        before final dispatch.
        """
        now = time.time()
        if now - self._bias_cache["ts"] < self._bias_cache_ttl:
            return self._bias_cache["data"]

        db_path = os.path.join(self.workspace, "kokertech_vault.db")
        if not os.path.exists(db_path):
            self._bias_cache = {"data": "", "ts": now}
            return ""

        try:
            conn = sqlite3.connect(db_path, timeout=15.0)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT bias_type, description FROM bias_ledger "
                    "ORDER BY id DESC LIMIT 3"
                )
                biases = cursor.fetchall()
                if not biases:
                    self._bias_cache = {"data": "", "ts": now}
                    return ""
                formatted = "\n".join([f"- {b[0]}: {b[1]}" for b in biases])
                result = (
                    f"\n[RECENT BIASES TO AVOID (Self-Correction)]:\n{formatted}\n"
                )
                self._bias_cache = {"data": result, "ts": now}
                return result
            finally:
                conn.close()
        except (sqlite3.Error, OSError, ValueError) as e:
            logger.debug(f"get_recent_biases_inline SQLite read failed (graceful fallback): {e}")
            self._bias_cache = {"data": "", "ts": now - self._bias_cache_ttl + 0.5}
            return ""

    # -- User identity JSON ------------------------------------------------

    def load_user_profile(self, cached_json_reader) -> str:
        """Format identity fields into a profile string.

        ``cached_json_reader`` is a callable (path: str) -> Optional[dict]
        — accepts :meth:`ProviderService._read_cached_json` for production,
        a stub in tests. Reads ``<workspace>/user_identity.json``.
        """
        pil_path = os.path.join(self.workspace, "user_identity.json")
        data = cached_json_reader(pil_path)
        if not data:
            return "No specific personal identity layer configured yet."
        return (
            f"Preferred Name: {data.get('name', 'Jacques')}\n"
            f"Tone Preferences: {data.get('tone', 'Technical & Direct')}\n"
        )

    # -- VRAM --------------------------------------------------------------

    def get_vram_usage(self) -> int:
        """Run ``nvidia-smi`` and return MB of used VRAM (0 on error).

        Delegates to the canonical ``utils.gpu.get_vram_usage`` probe
        (Sprint 19.8 dedup: same contract, one implementation). The
        tests patch ``subprocess.check_output`` which the helper still
        flows through, so TestGetVramUsage remains authoritative.
        ANTI-FRAGILITY (inherited): FileNotFoundError is an OSError
        subclass — the helper catches it, preserving the 0-on-failure
        contract on machines without an NVIDIA GPU. Locked by
        tests/test_context_service.py::TestGetVramUsage.
        """
        from utils.gpu import get_vram_usage as _probe
        return _probe()
