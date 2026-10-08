"""provider_stream_service.py -- Sprint 19.3: streaming chat-completion executor.

Extracted from ``KokertechController._execute_stream`` in Sprint 19.3.
Decouples the streaming-token accumulation loop (provider
``chat_completion_stream()`` consumption, cancel-event handling between
tokens, error fallback semantics) from the main facade so future streaming
policy (token batching, compression, backpressure) can live here without
touching ``kokertechController.py``.

LAZY-SINGLETON PATTERN (per ``KNOWLEDGE.md`` section 10 R12 + ``SkillService``
precedent):

- ``_PROVIDER_STREAM_SERVICE_SINGLETON`` + ``_PROVIDER_STREAM_SERVICE_INIT_LOCK``
  at module level.
- Public accessor: ``get_provider_stream_service()`` returns the cached singleton.
- Reset helper: ``_reset_provider_stream_service_for_tests()`` clears the
  module-level cache under the init lock (NEVER touches ``registry._lock``).

CONTRACT (why ``provider`` is passed in):

The facade's ``_execute_stream`` forwards ``self.provider`` (which tests
replace with a stub provider) on every call, so the service stays a
stateless executor -- all mutable state belongs to the caller. The service
owns no provider instance.
"""

from __future__ import annotations

import threading
from typing import Optional

from logging_config import get_logger
from services import get_services

logger = get_logger(name="ProviderStreamService")


class ProviderStreamService:
    """Own the streaming-token accumulation loop for the facade."""

    def execute_stream(
        self,
        provider,
        messages,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: int,
        cancel_event=None,
        stream_callback=None,
        response_format=None,
    ):
        """Execute a streaming chat completion using the given provider.

        Uses ``provider.chat_completion_stream()`` to receive tokens
        progressively. Tokens are accumulated into a full response text
        which is returned. Returns None if streaming fails so the caller
        can fall back to the non-streaming path.

        The ``cancel_event`` is checked between each token -- once set,
        streaming stops immediately (the provider yields an error chunk
        or stops yielding, matching ``LocalLLMProvider`` behaviour).

        ``stream_callback(token)`` is invoked per-token when provided.
        Returns ``None`` immediately when ``stream_callback`` is None
        (the caller should use the non-streaming path).
        """
        if stream_callback is None:
            return None

        try:
            if not hasattr(provider, "chat_completion_stream"):
                logger.warning("Local LLM provider does not support streaming")
                return None

            chunks = []
            for chunk in provider.chat_completion_stream(
                messages=messages, model=model, temperature=temperature,
                max_tokens=max_tokens, timeout=timeout,
                cancel_event=cancel_event,
                response_format=response_format,
            ):
                if chunk.get("error"):
                    logger.warning(f"Stream error: {chunk['error']}")
                    return None

                token = chunk.get("token", "")
                if token:
                    chunks.append(token)
                    stream_callback(token)

                if chunk.get("done"):
                    return "".join(chunks)

            return "".join(chunks)

        except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning(f"Stream error: {e}")
            return None


# Module-level lazy singleton + init lock (matches SkillService /
# CodeIntelligenceFactory pattern per ``KNOWLEDGE.md`` section 10 R12).
_PROVIDER_STREAM_SERVICE_SINGLETON: Optional[ProviderStreamService] = None
_PROVIDER_STREAM_SERVICE_INIT_LOCK = threading.Lock()


def get_provider_stream_service() -> ProviderStreamService:
    """Eager-construct + register under ``ServiceRegistry`` (lazy singleton)."""
    global _PROVIDER_STREAM_SERVICE_SINGLETON
    with _PROVIDER_STREAM_SERVICE_INIT_LOCK:
        if _PROVIDER_STREAM_SERVICE_SINGLETON is None:
            _PROVIDER_STREAM_SERVICE_SINGLETON = ProviderStreamService()
            get_services().register_instance(
                "provider_stream_service", _PROVIDER_STREAM_SERVICE_SINGLETON,
            )
        return _PROVIDER_STREAM_SERVICE_SINGLETON


def _reset_provider_stream_service_for_tests() -> None:
    """Reset module-level singleton under init lock (NEVER touches registry._lock).

    NB: leaves the stale ``register_instance`` entry in ServiceRegistry (the
    factory lambda still returns the old object) -- the getter re-registers
    on the next call. Same precedent as ``SkillService``; callers should use
    ``get_provider_stream_service()``, never ``get_services().get(...)`` after
    a reset.
    """
    global _PROVIDER_STREAM_SERVICE_SINGLETON
    with _PROVIDER_STREAM_SERVICE_INIT_LOCK:
        _PROVIDER_STREAM_SERVICE_SINGLETON = None
