"""services — Sprint 3 (L99) subpackage.

Splits the historical KokertechController god-object into 6 focused services
(4 Sprint-3 + 2 Sprint-19.3), each owning its own state and exposing a clean
public surface. All are lazily registered as singletons in
:class:`services.ServiceRegistry` on first
:class:`kokertechController.KokertechController` instantiation.

Architecture:
- :class:`HistoryService`  — owns conversation history, lock, summary file,
  memory-limit eviction + summarization gating, model-aware truncation caps.
- :class:`ContextService`  — owns parallel memory fetch, user-identity JSON,
  recent bias/growth arc lookups, the AgenticRAGEngine, and the reusable
  ThreadPoolExecutor for parallel lookups.
- :class:`PromptService`   — owns centralized prompt construction (via
  :class:`prompt_builder.PromptBuilder`), tool progressive-disclosure state,
  and the structured/freeform ``parse_ai`` tag extractor.
- :class:`ProviderService` — owns AI provider instantiation, mtime-keyed
  JSON cache for ``app_settings.json`` / ``user_identity.json``,
  fallback-chain execution, the 3-layer inference-safety guards
  (per KNOWLEDGE.md §3 Decision 8: model-aware eviction cap,
  pre-flight token-budget check, post-flight sentinel detection),
  and provider-side utilities (VRAM, cache-clear).
- :class:`SettingsCacheService` — Sprint 19.3: mtime-keyed TTL cache
  (``_json_cache`` / ``_json_cache_lock`` / ``_json_cache_ttl``) + the
  ``user_identity.json`` profile loader + the ``app_settings.json``
  model-name loader with provider-cache invalidation.
- :class:`ProviderStreamService` — Sprint 19.3: streaming-token
  accumulation loop (``chat_completion_stream()`` consumption with
  cancel-event checks between tokens + error fallback).

The :class:`kokertechController.KokertechController` facade keeps
``process_input`` / ``process_input_multi`` / ``process_input_with_personas``
orchestration bodies + property forwards for backward compatibility with the
~900 existing tests + 3 interface layers (PyQt6, Flask, Terminal CLI).

Public surface kept stable by the facade:
    Attributes: history, compressed_context_summary, summary_file,
                memory_limit, workspace, logger, provider, rag_engine,
                _last_loaded_model, _pending_tool_loads, _loaded_tool_categories,
                _bias_cache, _json_cache, _context_pool
    Methods:    __init__, shutdown, _truncate, _dynamic_cap, _message_cap,
                _load_user_profile, _load_target_model, _read_cached_json,
                _execute_stream,
                _fetch_context_components, _format_biases, _format_growth_arc,
                _summarize_and_evict, _get_recent_biases, _get_recent_biases_inline,
                _load_summary, parse_ai, get_vram_usage, clear_api_cache,
                wipe_memory, process_input, process_input_multi,
                process_input_with_personas, _is_sentinel_response,
                _model_input_budget, _execute_with_fallback, _build_context_text
"""

from __future__ import annotations

# Canonical workspace default. Centralized so a future workspace rename is
# a 1-line edit. This is the SINGLE source of truth for KOKERTECHAI's
# project root across all 4 Sprint 3 services + the singleton factories.
#
# ORDERING PIN: must stay above all -trigger submodule loads
# below. Submodules (history_service.py + provider_service.py + registry.py)
# do  at their own module-load time, and
# Python only marks the services package as  in
# sys.modules once __init__.py begins executing. A future maintainer who
# moves this DEFAULT_WORKSPACE binding (or puts a new from .X import above
# it) will explode every submodule at import time with
# . Grep this PIN
# marker () to audit the contract.
#
# IMPORTANT load-order note: history_service.py, provider_service.py, and
# registry.py all do . This binding MUST
# be defined in services/__init__.py BEFORE the service imports below --
# Python's import system marks the package as "currently loading" in
# sys.modules before __init__.py executes, so submodules can read the
# partial module namespace via .
DEFAULT_WORKSPACE: str = r"C:\KokertechAI"

from services.history_service import HistoryService
from services.context_service import ContextService
from services.prompt_service import PromptService
from services.provider_service import ProviderService

# L99 Sprint 3 modularization: ServiceRegistry + get_services used to live in
# a top-level services.py module that was shadowed by this services/
# subpackage (Python prefers the package when both exist). The result was
# that 'from services import get_services' raised ImportError -- blocking
# test_services.py, test_controller_split.py, and plugin_registry.py. The
# fix was to move the registry module inside the package as
# services/registry.py and re-export it here so all three importers stay
# byte-compatible.
#
# Guard: top-level services.py at the project root is intentionally DELETED --
# do NOT recreate it. If you put services.py back at C:\KokertechAI\ as a
# module alongside the services/ directory, Python will prefer the package
# and the module will be shadow-loaded, causing 'from services import
# get_services' to raise ImportError again. The only supported entry point
# for the registry is 'from .registry import ...' from inside this package.
from .registry import ServiceRegistry, get_services
from .code_intelligence_factory import CodeIntelligenceFactory, get_code_intelligence_factory
from .tool_execution_service import (
    CommandPolicyGuard,
    RiskLevel,
    ToolExecutionResult,
    ToolExecutionService,
)

__all__ = [
    "DEFAULT_WORKSPACE",
    "HistoryService",
    "ContextService",
    "PromptService",
    "ProviderService",
    "ServiceRegistry",
    "get_services",
    "CodeIntelligenceFactory",
    "get_code_intelligence_factory",
    "ToolExecutionService",
    "CommandPolicyGuard",
    "ToolExecutionResult",
    "RiskLevel",
]

