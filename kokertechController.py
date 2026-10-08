# kokertechController.py -- AI orchestration facade (post-Sprint 19 extraction).
# Thin delegation layer over 6 services (HistoryService / ContextService / PromptService
# / ProviderService / SettingsCacheService / ProviderStreamService).
# Version: 1.9.1 -- 2026-09-21 -- Sprint 19.8: get_vram_usage delegates to the
#   canonical windowless utils.gpu probe (fixes shell=True cmd.exe-parsing
#   regression; subprocess import removed).
from __future__ import annotations

import os
import re
import sys
import threading
from datetime import datetime
from typing import Optional

import memory_vault
from agentic_rag import AgenticRAGEngine
from ai_base import cached_chat_completion, get_provider
from config import CONFIG
from logging_config import get_logger

logger = get_logger(name="Controller")
from prompt_builder import PromptBuilder
from services import get_services
from services.provider_stream_service import get_provider_stream_service
from services.settings_cache_service import get_settings_cache_service
from sub_agents import router

# Deep research query indicators — if a user message contains any of these patterns,
# the RAG pipeline is automatically triggered for multi-hop retrieval + synthesis.
_DEEP_RESEARCH_TRIGGERS = [
    "research", "investigate", "deep dive", "compare and contrast",
    "comprehensive analysis", "thorough analysis", "tell me everything about",
    "what do we know about", "gather information", "look into",
    "multi-hop", "rag query",
]

# Number of words above which a query is automatically considered "deep research"
_DEEP_RESEARCH_MIN_WORDS = 25

# High-specificity triggers that bypass the word count minimum.
# These are explicit RAG intent indicators — if present, RAG runs regardless of query length.
_HIGH_SPECIFICITY_TRIGGERS = {"multi-hop", "rag query"}

# Sprint 12: auto-mode heuristics for structured_format
_DATA_EXTRACTION_KEYWORDS = {
    "extract", "list", "find", "get", "json", "schema", "parse",
    "count", "search", "fetch", "query", "scrape", "summarize",
}


def _detect_structured_format(text: str) -> str:
    """Auto-detect whether to use JSON or XML structured output.

    Returns "json" for data-extraction queries (keywords like extract/list/json),
    "xml" for thinking-heavy queries (default).
    """
    text_lower = text.lower()
    score = sum(1 for kw in _DATA_EXTRACTION_KEYWORDS if kw in text_lower)
    return "json" if score >= 2 else "xml"


# No external providers — always use local_llm (direct GGUF via llama-cpp-python)


class KokertechController:
    def __init__(self) -> None:
        # ── Acquire services from the registry ──
        svc = get_services()
        self._history_svc = svc.get("history_service")
        self._context_svc = svc.get("context_service")
        self._prompt_svc = svc.get("prompt_service")
        self._provider_svc = svc.get("provider_service")
        try:
            self._hive_svc = svc.get("hive_service")
            self._speculative_svc = svc.get("speculative_service")
            self._sensory_svc = svc.get("sensory_service")
            self._tool_exec_svc = svc.get("tool_execution_service")
        except (KeyError, ValueError, AttributeError, RuntimeError):
            self._hive_svc = None
            self._speculative_svc = None
            self._sensory_svc = None
            self._tool_exec_svc = None
        if self._hive_svc is None:
            try:
                from services.hive_service import HiveService
                self._hive_svc = HiveService()
            except (ImportError, RuntimeError, ValueError):
                self._hive_svc = None
        if self._sensory_svc is None:
            try:
                from services.sensory_service import SensoryService
                self._sensory_svc = SensoryService()
            except (ImportError, RuntimeError, ValueError):
                self._sensory_svc = None
        if self._tool_exec_svc is None:
            try:
                from services.tool_execution_service import ToolExecutionService
                self._tool_exec_svc = ToolExecutionService(workspace=svc.get("context_service").workspace if svc.has("context_service") else r"C:\KokertechAI")
            except (ImportError, RuntimeError, ValueError):
                self._tool_exec_svc = None
        self._settings_cache_svc = get_settings_cache_service()
        self._stream_svc = get_provider_stream_service()

        self.workspace = r"C:\KokertechAI"
        self.api_url = os.getenv("KOKERTECH_API_URL", "http://127.0.0.1:1234/v1/chat/completions")
        self.api_key = os.getenv("KOKERTECH_API_KEY", "")
        self.summary_file = os.path.join(self.workspace, "context_summary.txt")
        self.memory_limit = 2

        # Sync workspace into the 4 services BEFORE any service-read happens.
        # Tests do `patch.object(ctrl, 'workspace', ...)` and expect the service
        # to honor the patched path on the next call -- _sync_services_workspace
        # mirrors the new path to every service attribute that reads it.
        self._sync_services_workspace()

        # Eager attribute mirror: tests introspect ctrl.history / ctrl._lock
        # directly. List mutations on the service flow through (same object).
        # Rebinds (e.g. summarize_and_evict recreates a list) require
        # _refresh_history_mirror() to keep these in sync.
        self.history = self._history_svc.history
        self._lock = self._history_svc._lock
        self.compressed_context_summary = self._history_svc.load_summary()
        self._history_svc.compressed_context_summary = self.compressed_context_summary
        self._history_svc.summary_file = self.summary_file

        self.logger = get_logger(name="Controller")
        self.provider = get_provider(name="local_llm")
        self._provider_svc.provider = self.provider
        self._fallback_used = False
        self._last_loaded_model = self._provider_svc._last_loaded_model

        self.rag_engine = AgenticRAGEngine(
            max_hops=CONFIG.get("rag_max_hops", 3),
            top_k_per_source=CONFIG.get("rag_top_k", 5),
            enable_web_search=CONFIG.get("rag_enable_web_search", False),
        )
        self.rag_progress_callback = None
        self._context_svc.rag_engine = self.rag_engine

        self._pending_tool_loads = self._prompt_svc._pending_tool_loads
        self._loaded_tool_categories = self._prompt_svc._loaded_tool_categories
        self._tool_persistence_ttl = 3

        self._json_cache = self._settings_cache_svc._json_cache
        self._json_cache_lock = self._settings_cache_svc._json_cache_lock
        self._json_cache_ttl = self._settings_cache_svc._json_cache_ttl
        self._bias_cache = self._context_svc._bias_cache
        self._bias_cache_ttl = 5.0

        self._pb = self._prompt_svc._pb
        self._context_pool = self._context_svc._context_pool

        try:
            memory_vault.ensure_tables_exist()
            memory_vault.get_current_session_id()
            self.logger.debug("Memory vault initialized")
        except Exception as e:
            self.logger.warning(f"Memory vault init failed (non-fatal): {e}")
        self.logger.debug("Controller initialized")

    # ── Synchronization helpers (mirror slots for service-side rebinds) ──

    def _sync_services_workspace(self) -> None:
        """Mirror facade.workspace -> every service that reads it.

        Tests do `patch.object(ctrl, 'workspace', ...)` expecting the
        facade + downstream services to honor the new path. Without this
        mirror, services were constructed with a frozen workspace
        (DEFAULT_WORKSPACE via the factory) and silently ignored facade
        patches.
        """
        self._history_svc.workspace = self.workspace
        self._history_svc.summary_file = os.path.join(self.workspace, "context_summary.txt")
        self._context_svc.workspace = self.workspace
        self._provider_svc.workspace = self.workspace
        self._settings_cache_svc.workspace = self.workspace
        if getattr(self, "_tool_exec_svc", None):
            self._tool_exec_svc.workspace = self.workspace

    def _refresh_history_mirror(self) -> None:
        """Re-mirror facade.history after HistoryService rebinds its list.

        HistoryService.summarize_and_evict rebinds
        ``self.history = self.history[-2:]`` (creates a new list object).
        Without this re-mirror, facade.history (set eagerly in __init__)
        points at the pre-eviction orphan list while the service uses the
        fresh post-eviction list -- tests like
        TestControllerSummarizeAndEvict::test_evicts_when_history_exceeds_limit
        fail because they assert on ctrl.history.length after eviction.
        """
        self.history = self._history_svc.history

    def shutdown(self) -> None:
        """Idempotent cleanup -- called from closeEvent.

        Uses the facade's ``_context_pool`` (which may be replaced by
        tests) instead of delegating to ``_context_svc.shutdown()`` so
        that ``patch``-based test fixtures that set
        ``ctrl._context_pool = mock_pool`` are correctly exercised.
        """
        try:
            pool = getattr(self, '_context_pool', None)
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            self.logger.debug(f"Context pool shutdown failed (non-fatal): {e}")


    def _get_recent_biases(self):
        # CRITICAL FIX (Sprint 3): mirror workspace before delegating so
        # post-construction patches propagate into the service cache read.
        self._context_svc.workspace = self.workspace
        return self._context_svc.get_recent_biases_inline()

    def clear_api_cache(self):
        self._provider_svc.clear_api_cache()

    # ── Cached JSON read helper (Sprint 19.3: delegated) ──
    def _read_cached_json(self, path, ttl=None):
        """Read a JSON file with an mtime-keyed TTL cache.

        Sprint 19.3: the algorithm lives in
        ``SettingsCacheService.read_cached_json`` (services/settings_cache_service.py).
        The facade forwards its mirrored ``_json_cache`` / ``_json_cache_lock``
        attributes so test rebinds of ``ctrl._json_cache = {}`` stay
        authoritative for the cache the service mutates.
        """
        return self._settings_cache_svc.read_cached_json(
            path, ttl if ttl is not None else self._json_cache_ttl,
            self._json_cache, self._json_cache_lock,
        )

    def _load_user_profile(self):
        """Load user_identity.json with TTL cache (returns formatted string).

        Sprint 19.3: delegates to ``SettingsCacheService.load_user_profile``,
        passing the facade's own ``_read_cached_json`` as the reader so
        test patches on ``ctrl._read_cached_json`` keep working.
        """
        return self._settings_cache_svc.load_user_profile(
            self._read_cached_json, self.workspace,
        )

    def _load_target_model(self):
        """Load model_name from app_settings.json with TTL cache.

        Sprint 19.3: delegates to ``SettingsCacheService.load_target_model``
        (passing the facade's ``_read_cached_json`` reader + mirrored
        ``_last_loaded_model``). The updated model is mirrored back so
        ``ctrl._last_loaded_model`` stays authoritative for tests.
        """
        model, last_loaded = self._settings_cache_svc.load_target_model(
            self._read_cached_json, self._last_loaded_model, self.workspace,
        )
        self._last_loaded_model = last_loaded
        return model

    # ── Dynamic context-window caps ──
    # Tags of models known to have large context windows. Matched case-insensitively.
    _LARGE_CONTEXT_TAGS = ("qwen", "llama3.1", "llama-3.1", "gpt-4o", "gpt-4-turbo", "claude-3", "claude-sonnet")

    def _dynamic_cap(self, model_name, base_cap):
        """Scale a truncation cap based on the model's context window.

        Known large-context models (qwen2.5, llama3.1, gpt-4o, claude-3)
        get 4x larger caps. Unknown models fall back to base_cap.
        """
        if not model_name:
            return base_cap
        m = model_name.lower()
        if any(tag in m for tag in self._LARGE_CONTEXT_TAGS):
            return base_cap * 4
        return base_cap

    def _message_cap(self, base=1500):
        """Compute the message-truncation cap for the active model.

        Hoisted out of ``_truncate`` so callers (which may invoke it once
        per history message) don't pay a JSON-read cost per call. Returns
        a plain int — no object identity, safe to share across calls.
        """
        try:
            return self._dynamic_cap(self._load_target_model(), base)
        except Exception as e:
            self.logger.debug(f"_message_cap model lookup failed (using base cap): {e}")
            return base

    def _truncate(self, text, cap=1500):
        """Truncate text from the head, preserving the tail.

        Behavior contract: for text longer than ``cap``, keep the LAST
        ``cap`` characters. The right behavior for conversation history
        where the most recent context is most relevant.

        IMPORTANT — this is a pure function with no dynamic cap lookup.
        Callers that want the model-aware cap (4x for qwen/llama3.1/
        gpt-4o/claude-3) MUST pre-compute it via ``_message_cap()`` and
        pass the result as ``cap``. Using the default 1500 silently
        produces a static truncation even on large-context models.
        """
        if not text or len(text) <= cap:
            return text
        return "...[TRUNCATED] " + text[-cap:]

    # ── Pre-flight: safe-input-char budget by model ──
    # Fix 2 (context-overflow): tag-matched max_input_tokens table, used by
    # `process_input` to refuse sending prompts that won't fit. Token→char
    # ratio ~4 (English/code-mixed). 0.7 safety margin leaves room for
    # max_tokens output + tokenizer overhead. More-specific tags (e.g.
    # "qwen2.5-0.5b") come BEFORE generic ("qwen") so a 0.5b model is
    # correctly sized to 1024, not 8192.
    _MODEL_INPUT_TOKEN_BUDGETS = (
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
    _SAFE_INPUT_MARGIN = 0.7   # leave 30% for output tokens + tokenizer overhead
    _SENTINEL_RESPONSES = (
        "Yo! What's on the bench today, mate?",
    )

    def _model_input_budget(self, model_name):
        """Compute the safe CHARACTER budget for the model's full input prompt.

        Returns the maximum total characters across all input messages. Used
        by ``process_input`` as a pre-flight check before sending to the AI
        provider — anything larger risks provider-side truncation or
        sentinel/example-text echo (the symptom seen when the rendered
        prompt overflows a small-context model).

        Unknown models get a conservative 4096-token default (~11469 chars
        safe). If a smaller-context model isn't in ``_MODEL_INPUT_TOKEN_BUDGETS``,
        add a tag-specific entry rather than relying on this default.
        """
        if not model_name:
            max_tokens = 4096
        else:
            m = model_name.lower()
            max_tokens = 4096   # safe unknown default
            for tag, budget in self._MODEL_INPUT_TOKEN_BUDGETS:
                if tag in m:
                    max_tokens = budget
                    break
        return int(max_tokens * 4 * self._SAFE_INPUT_MARGIN)

    # Sentinel pattern: matches the structured system prompt's "Example N" / "Example N (Title):"
    # template markers exactly. Anchored to start-of-string so legitimate user-facing
    # text like "An example query?" or "Example: foo" mid-response won't false-positive.
    _SENTINEL_PATTERN = re.compile(r'^\s*Example\s+\d+\b', re.IGNORECASE)

    def _is_sentinel_response(self, ai_text):
        """Detect 'model echoed its example template' responses (Fix 3).

        Returns True if ``ai_text`` matches a known sentinel (case-insensitive
        exact match) or starts with the structured-template's "Example N" /
        "Example N (Title):" marker that the model was supposed to use as
        guidance, not as the actual response. Useful as a post-flight safety
        net when the pre-flight check passed but the response is still
        sentinel content.
        """
        if not ai_text:
            return False
        stripped = ai_text.strip()
        for sentinel in self._SENTINEL_RESPONSES:
            if stripped.lower() == sentinel.lower():
                return True
        if self._SENTINEL_PATTERN.match(stripped):
            return True
        return False

    # ── Parallel memory lookups ──
    def _fetch_context_components(self, text, agent_type, episodic_limit=3):
        """Run independent memory lookups in parallel via a thread pool.

        All 5 lookups (semantic search, bias format, growth arc, session
        context, weighted episodic) hit different SQLite tables and have
        no shared state, so they're safe to run concurrently. Wall-clock
        savings on a typical cold lookup is ~4-5x.

        Args:
            text: User query for semantic search.
            agent_type: Persona/agent identifier for bias/growth lookups.
            episodic_limit: Max episodic entries to include (default 3).
                The A/B test path uses 2 to keep the side-by-side context
                lean and improve comparison signal-to-noise.
        """
        components = {}

        def _semantic():
            try:
                docs = memory_vault.semantic_search(text, top_k=1)
                return ("context", docs[0][2] if docs else "No prior history found.")
            except Exception as e:
                self.logger.debug(f"semantic_search failed (graceful fallback): {e}")
                return ("context", "No prior history found.")

        def _biases():
            return ("scbe_text", self._format_biases(agent_type))

        def _growth():
            return ("growth_text", self._format_growth_arc(agent_type))

        def _session():
            try:
                return ("episodic_context",
                        memory_vault.get_session_context(limit=episodic_limit))
            except Exception as e:
                self.logger.debug(f"get_session_context failed (graceful fallback): {e}")
                return ("episodic_context", "No episodic journal available.")

        def _weighted():
            try:
                return ("weighted_episodic",
                        memory_vault.get_episodic_context(
                            limit=episodic_limit, session_id=None, query=text))
            except Exception as e:
                self.logger.debug(f"get_episodic_context failed (graceful fallback): {e}")
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

    def _format_biases(self, agent_type):
        """Format recent biases for the SCBE section."""
        try:
            biases = memory_vault.get_recent_bias(agent_type)
        except Exception as e:
            self.logger.debug(f"get_recent_bias failed (graceful fallback): {e}")
            biases = []
        if not biases:
            return "No bias drift recorded yet."
        return "\n".join([
            f"- [{b['timestamp']}] {b['bias_type']} (Confidence: {b['confidence_score']}%): {b['description']}"
            for b in biases
        ])

    def _format_growth_arc(self, agent_type):
        """Format growth arc events for the timeline section."""
        try:
            growth = memory_vault.get_growth_arc(agent_type)
        except Exception as e:
            self.logger.debug(f"get_growth_arc failed (graceful fallback): {e}")
            growth = []
        if not growth:
            return "No growth events recorded yet."
        return "\n".join([
            f"- [{g['timestamp']}] {g['event_description']} (Energy Shift: {g['energy_shift']}%)"
            for g in growth
        ])

    @staticmethod
    def _strip_thinking_blocks(text: str) -> str:
        """Remove ``<thinking>...</thinking>`` blocks from history-derived text.

        REGRESSION GUARD (freeform prompt contract): freeform-mode system
        prompts must contain NO ``<thinking>`` tags — the structured-mode
        template teaches the tag format, but freeform forbids it
        (tests/test_integration_freeform_mock_plugin.py::
        TestPluginSystemPromptInjection::test_plugin_schemas_in_freeform_prompt).
        ``compressed_context_summary`` is built from HISTORY ASSISTANT
        TURNS, which may originate from structured-mode sessions whose
        replies carry reasoning blocks — those leaked verbatim into the
        freeform system message. Only PAIRED blocks are stripped; an
        unclosed trailing tag is left alone (history writes complete
        turns; a truncated one is rarer than prose containing a lone
        marker).
        """
        import re
        return re.sub(
            r"<thinking>.*?</thinking>\s*", "", text,
            flags=re.DOTALL | re.IGNORECASE,
        )

    def _build_context_text(self, persona, user_profile, episodic_context,
                            weighted_episodic, context, scbe_text, growth_text,
                            freeform: bool = False):
        """Build the shared context_text block (identical for process_input and multi).

        Assembles the 9-section context block used as the system message
        body for normal chat and multi-model dispatch. The A/B test
        method uses its own (simpler) format and does not call this.

        ``freeform=True`` strips ``<thinking>`` blocks from the history
        summary — freeform prompts must not carry structured-mode output
        tags (see ``_strip_thinking_blocks``).
        """
        past_summary = (
            self._strip_thinking_blocks(self.compressed_context_summary).strip()
            if freeform else self.compressed_context_summary.strip()
        )
        return (
            f"[AGENT INITIALIZATION]\n{persona}\n\n"
            f"[PERSONAL IDENTITY LAYER (PIL)]\n{user_profile}\n\n"
            f"[PAST CONTEXT SUMMARY]\n{past_summary}\n\n"
            f"[EPISODIC JOURNAL (Recent Session Summaries)]\n{episodic_context}\n\n"
            f"[TOP RELEVANT ENTRIES (weighted by importance + topic match)]\n{weighted_episodic}\n\n"
            f"[LONG-TERM MEMORY]\n{context}\n\n"
            f"[SELF-CONSISTENT BIAS ENGINE (SCBE)]\n{scbe_text}\n\n"
            f"[GROWTH ARC TIMELINE]\n{growth_text}"
        )

    def _build_freeform_system_prompt(self, custom_protocol):
        """Return the freeform-mode system prompt, honoring a custom override.

        Used by process_input (when freeform_mode is on) and process_input_multi.
        Delegates to PromptBuilder (Sprint 2) — the template lives in
        prompt_builder.py as FREEFORM_SYSTEM_PROMPT.
        """
        return self._pb.build_system_prompt(mode="freeform", custom_protocol=custom_protocol)

    # =================================================================
    # Agent contract injection -- loads the God Reviewer behavior contract
    # and appends it to the system prompt so the LLM's inference is governed
    # by the contract at runtime (response structure, quality gates, tone,
    # edge case matrix). Togglable via CONFIG["god_reviewer_contract"].
    # =================================================================
    _AGENT_FILES = {
        "Kokertech": "Kokertech.agent.md",
        "Buffy": "Buffy.agent.md",
    }

    def _inject_agent_contract(self, system_prompt):
        """Append an agent behavior contract to the system prompt.

        Reads the agent definition file (``agent/<AgentName>.agent.md``),
        strips YAML frontmatter and changelog blockquotes, and appends the
        governing body as a ``[AGENT CONTRACT: <Name>]`` block.

        The active agent is selected via ``CONFIG["active_agent"]`` —
        ``"Kokertech"`` (default, God Reviewer) or ``"Buffy"`` (Strategic
        Coding Assistant). Controlled by the ``god_reviewer_contract`` gate
        (must be ``True``). Returns ``system_prompt`` unchanged when the
        gate is off, the file is missing, or any I/O error occurs.

        SWALLOWED vs SURFACED audit (Sprint 5.2 regression guard):
        The outer `except Exception` is intentionally broad because this is a
        best-effort prompt decoration path — a missing/ unreadable/ unparseable
        contract must never abort chat. The method surfaces EVERY failure as a
        ``debug`` log line (`Agent contract injection skipped: {e}`) and then
        returns `system_prompt` unchanged (the decoration is skipped, never
        partial). The compiled prompt shape under error is byte-identical to the
        gate-off shape: the caller receives the prompt it would have gotten with
        ``god_reviewer_contract=False``. Nothing is swallowed silently.

        Failure classes handled inside the try block (all surfaced as debug logs,
        all degrade to "prompt unchanged"):
          * gate off (early return before any I/O)
          * active_agent not in _AGENT_FILES -> falls back to Kokertech.agent.md (no error)
          * contract_path missing (exists() check) -> debug log + unchanged prompt
          * open()/read() OSError / UnicodeDecodeError / PermissionError -> debug log + unchanged
          * frontmatter / blockquote stripping producing empty body -> early return (no log, by design: an empty doc is not a failure worth spamming)
          * ANY other Exception during stripping/assembly -> debug log + unchanged prompt
        """
        if not CONFIG.get("god_reviewer_contract", True):
            return system_prompt
        agent_name = CONFIG.get("active_agent", "Kokertech")
        agent_file = self._AGENT_FILES.get(agent_name, "Kokertech.agent.md")
        try:
            contract_path = os.path.join(self.workspace, "agent", agent_file)
            if not os.path.exists(contract_path):
                self.logger.debug(f"Agent contract not found: {contract_path}")
                return system_prompt
            with open(contract_path, "r", encoding="utf-8") as f:
                raw = f.read()
            # Strip YAML frontmatter (between first two --- delimiters)
            if raw.startswith("---"):
                second = raw.find("---", 3)
                if second != -1:
                    raw = raw[second + 3:].lstrip()
            # Strip blockquotes before the first heading (changelog + project context).
            # Only strips contiguous > blocks at the top — heading-anchored, so
            # mid-body > lines in code blocks are preserved.
            lines = raw.split("\n")
            body_lines = []
            found_heading = False
            for line in lines:
                if not found_heading and line.startswith("## "):
                    found_heading = True
                if found_heading:
                    body_lines.append(line)
                elif not line.startswith("> ") and line.strip() != "":
                    # First non-blockquote, non-blank line starts the body
                    found_heading = True
                    body_lines.append(line)
                # else: skip blockquote lines and blank lines before the first heading
            body = "\n".join(body_lines).strip()
            if not body:
                return system_prompt
            self.logger.info(f"Agent contract injected: {agent_name}")
            return (
                system_prompt
                + f"\n\n[AGENT CONTRACT: {agent_name}]\n"
                + body
                + "\n[END AGENT CONTRACT]"
            )
        except Exception as e:
            self.logger.debug(f"Agent contract injection skipped: {e}")
            return system_prompt

    # =================================================================
    # Skill policy injection (FATFO) -- helper mid-Sprint 19 extraction.
    # Defined here, between _build_freeform_system_prompt and _load_summary,
    # so both process_input and process_input_multi can call it without
    # duplicating the file-read logic.
    # =================================================================
    def _inject_skill_policy(self, system_prompt, active_skill=None):
        """Inject the active skill's [SKILL POLICY: <name>] block from
        ``<workspace>/.blackbox/skills/<name>/SKILL.md`` into ``system_prompt``.

        Returns ``system_prompt`` unchanged when:
          * ``active_skill`` is empty/None,
          * the SKILL.md file does not exist,
          * the SKILL.md file is empty after ``strip()``, or
          * any I/O exception is raised (logged at ``debug``).

        =================================================================
        Decision: Option A (append) -- see ``implementation-roadmap.md``
        Sprint 16 / Phase E row 16.3 for the full A vs B comparison.
        =================================================================

        Option A (append) -- CHOSEN:
            Keep the existing base protocol (structured/freeform/custom)
            AND append a ``[SKILL POLICY: <name>]\n{skill_doc}\n[END SKILL POLICY]``
            block at the end of the system prompt. Skill doc + parsing
            protocol co-exist; net length grows by ~len(skill_doc) + 50
            chars of delimiters.

        Option B (replace) -- REJECTED for this codebase, with three
        concrete failure modes:
          1. Loses ``STRUCTURED_SYSTEM_PROMPT`` -- the LLM stops emitting
             ``<final_output>`` tags; ``parse_ai()`` falls back to raw
             text and downstream ``handle_ai_response()`` misrenders.
          2. Loses ``[LOADED TOOL SCHEMAS]`` from progressive disclosure
             -- plugin ``command`` calls stop working mid-conversation.
          3. Loses ``[RECENT BIASES TO AVOID]`` -- the SCBE bias-feedback
             block stops reaching the LLM, regressing self-correction.

        =================================================================
        Delimiter safety -- see ``implementation-roadmap.md`` Sprint 16
        / Phase E row 16.4. The block delimiters ``[SKILL POLICY]`` and
        ``[END SKILL POLICY]`` use square brackets, NEVER angle brackets.
        ``parse_ai()`` scans the *model output* (not the system prompt)
        for ``<final_output>...</final_output>`` with optional
        ``<thinking>...</thinking>`` prefix -- and so is unaffected by
        anything inside ``system_prompt``. The remaining risk is if the
        skill doc body itself contains XML-like fragments that the LLM
        echoes verbatim back as ``Example 1:``-style output; that path
        is covered by ``_is_sentinel_response()`` defense-in-depth
        (see KNOWLEDGE.md Decision 8 Layer C).
        =================================================================
        
        Note for skill authors -- avoid literal `<final_output>` / `<thinking>` / `<example>` patterns inside `SKILL.md` bodies. parse_ai() AND _is_sentinel_response() both scan the MODEL OUTPUT -- a verbatim echo of these tags from the system prompt would be incorrectly attributed as the LLM's answer.
"""
        if active_skill is None:
            raw = CONFIG.get("active_skill", "")
            active_skill = raw.strip() if raw else ""
        if not active_skill:
            return system_prompt
        try:
            skill_path = os.path.join(
                self.workspace, ".blackbox", "skills", active_skill, "SKILL.md",
            )
            if not os.path.exists(skill_path):
                self.logger.warning(f"Skill doc not found: {skill_path}")
                return system_prompt
            with open(skill_path, "r", encoding="utf-8") as f:
                skill_doc = f.read().strip()
            if not skill_doc:
                return system_prompt
            self.logger.info(f"Skill policy injected: {active_skill}")
            return (
                system_prompt
                + f"\n\n[SKILL POLICY: {active_skill}]\n{skill_doc}\n"
                + "[END SKILL POLICY]"
            )
        except Exception as e:
            self.logger.debug(f"Skill policy injection skipped: {e}")
            return system_prompt

    def _load_summary(self):
        if os.path.exists(self.summary_file):
            try:
                with open(self.summary_file, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError:
                pass
        return ""

    def get_vram_usage(self):
        """Return used VRAM in MB via the canonical windowless probe (0 on error).

        Delegates to ``utils.gpu.get_vram_usage`` — single source of truth
        shared with ProviderService/ContextService. Fixes the July 2026
        ``shell=True`` regression (console flash + cmd.exe parsing) by
        never shelling out through cmd.exe. Locked by
        tests/test_kokertechController.py (get_vram_usage contract: int
        on success, 0 on SubprocessError/ValueError — both preserved by
        the helper's OSError-inclusive catch).
        """
        try:
            from utils.gpu import get_vram_usage as _probe
            return _probe()
        except Exception as e:  # broad-catch-ok: facade must never raise on a diagnostics probe
            self.logger.warning(f"VRAM query failed: {e}")
            return 0

    def wipe_memory(self):
        with self._lock:
            self.history.clear()
            self.compressed_context_summary = ""
        if os.path.exists(self.summary_file):
            try:
                os.remove(self.summary_file)
            except OSError:
                pass
        return "Short-term history wiped."

    def _execute_with_fallback(self, messages, model, temperature, max_tokens, timeout, log, cancel_event=None, response_format=None):
        """Execute chat completion via the local LLM provider (no fallback chain).
        Returns (result_dict, "local_llm").
        """
        result = cached_chat_completion(
            self.provider,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            request_id=None,
            cancel_event=cancel_event,
            response_format=response_format,
        )
        return result, "local_llm"

    def _summarize_and_evict(self, log):
        """Delegate eviction + summarization to HistoryService.

        CRITICAL FIX 1: HistoryService.summarize_and_evict rebinds
        ``self.history = self.history[-2:]`` (creates a new list). We
        re-mirror self.history AFTER the service-side pass completes to
        keep facade.history in sync.

        CRITICAL FIX 2: Sync facade.history -> service.history BEFORE
        delegation so tests (and runtime branches) that write directly
        to ``ctrl.history`` have their changes visible to the service.
        Without this mirror, ``TestControllerSummarizeAndEvict.*`` all
        fail because they set ``ctrl.history`` (facade) while the
        service operates on its own (empty) ``self.history``.
        """
        self._history_svc.workspace = self.workspace
        self._history_svc.summary_file = self.summary_file
        self._history_svc.history = list(self.history)
        # CRITICAL FIX 3: Sync facade.provider -> service.provider so that
        # test patches to ``ctrl.provider`` propagate into the summarization
        # path.  HistoryService.summarize_and_evict calls
        # ``provider_svc.execute_with_fallback()`` WITHOUT ``provider_override``,
        # so the service falls back to ``self.provider`` (the original) and
        # never sees the mock.  Syncing here ensures both point to the same
        # object regardless of patches.
        self._provider_svc.provider = self.provider
        # Inject build_summarization_prompt from PromptBuilder onto
        # ProviderService so HistoryService.summarize_and_evict can call it.
        self._provider_svc.build_summarization_prompt = (
            lambda: self._pb.build_summarization_prompt()
        )
        self._history_svc.summarize_and_evict(
            log=log, provider_svc=self._provider_svc,
            episodic_store=memory_vault.store_episodic,
            session_id_fn=memory_vault.get_current_session_id,
            model_lookup=self._load_target_model,
        )
        self._refresh_history_mirror()
        self.compressed_context_summary = self._history_svc.compressed_context_summary

    # ──────────────────────────────────────────────────────────────────
    # Sprint 19.4 shared pipeline stages (dedup across the 3 orchestrators).
    # Each stage owns its own lock block, receives typed inputs, and
    # returns an immutable-ish payload -- no cross-stage locals. The
    # safety guards (_is_sentinel_response, budget pre-flight, Layer C)
    # remain byte-identical in the orchestrators.
    # ──────────────────────────────────────────────────────────────────

    def _stage_build_history(self, text):
        """Stage 1 (shared) -- append the user message under lock; return snapshot.

        Sprint 19.4: extracted from process_input / process_input_multi /
        process_input_with_personas (3 identical blocks). The lock is
        owned by this stage only -- no nested lock acquisition in callers.
        """
        with self._lock:
            self.history.append({"role": "user", "content": text})
            return list(self.history)

    def _stage_fetch_context(self, text, agent_type, episodic_limit=3,
                             episodic_placeholder="No episodic journal available."):
        """Stage 2 (shared) -- parallel memory fetch + graceful fallback.

        Sprint 19.4: extracted from the 3 orchestrators (previously a
        duplicated try/except + fallback dict at each call site).
        ``episodic_placeholder`` lets the A/B path emit empty episodic
        sections while chat/multi use the standard fallback string.
        """
        try:
            return self._fetch_context_components(
                text, agent_type, episodic_limit=episodic_limit,
            )
        except Exception as e:
            self.logger.warning(f"Parallel context fetch failed: {e}")
            return {
                "context": "No prior history found.",
                "scbe_text": "No bias drift recorded yet.",
                "growth_text": "No growth events recorded yet.",
                "episodic_context": episodic_placeholder,
                "weighted_episodic": episodic_placeholder,
            }

    def _stage_assemble_history_messages(self, history_copy, msg_cap):
        """Stage 3 (shared) -- role-map + truncate history into chat messages.

        Sprint 19.4: extracted from the 3 orchestrators (identical loop).
        Returns a fresh list; the caller prepends the system message
        (chat/multi) or uses it verbatim as base_messages (A/B).
        """
        messages = []
        for m in history_copy:
            role = "user" if m["role"] == "system" else m["role"]
            messages.append({
                "role": role, "content": self._truncate(m["content"], msg_cap),
            })
        return messages

    def _reject_with_error(self, err, final=None, thinking="", pop_assistant=False):
        """Pop the just-appended user message and return the Decision-8 error dict.

        Sprint 19.4: consolidates the 3 identical pop-and-reject blocks in
        process_input (budget overflow, Layer-A sentinel, Layer-C sentinel).
        ``pop_assistant=True`` pops the assistant message FIRST (Layer C --
        the assistant was appended in the update-history step) then the user.
        Return shape is the KNOWLEDGE.md §3 Decision 8 error contract:
        {error, final, thinking, command, ts}.
        """
        if pop_assistant:
            if self.history and self.history[-1]["role"] == "assistant":
                self.history.pop()
        if self.history and self.history[-1]["role"] == "user":
            self.history.pop()
        return {
            "error": err,
            "final": final if final is not None else f"\u274c {err}",
            "thinking": thinking,
            "command": None,
            "ts": datetime.now().strftime("%H:%M:%S"),
        }

    # ──────────────────────────────────────────────────────────────────
    # Orchestrators (process_input / process_input_multi / with_personas).
    # Kept inline because they coordinate cross-service state and have
    # many tests patching facade methods at the module level.
    # ──────────────────────────────────────────────────────────────────

    def _router_dispatch_block(self, text, log=None):
        """Return an ``[INTENT ROUTER DISPATCH]`` context block ("" if unrouted).

        The routed text is injected as context rather than replacing the
        user's turn. The user message is what the history, the vault, and the
        turn record must contain — an E2E contract pinned by
        tests/test_call_chain_e2e.py
        (``test_full_call_chain_structured_mode`` asserts the raw user text is
        in history). Rewriting ``text`` mutated the conversation record and
        broke that test, so routing appends here instead, exactly like the
        existing ``[DEEP RESEARCH RESULTS]`` RAG injection.
        """
        routed = self._route_intent(text, log)
        if not routed or routed == text:
            return ""
        return f"\n\n[INTENT ROUTER DISPATCH]:\n{routed}\n[END ROUTER DISPATCH]"

    def _route_intent(self, text, log=None):
        """Dispatch ``text`` through the sub-agent intent router (Sprint 5.2).

        ``sub_agents.router`` classifies the intent, dispatches to the
        matching persona (Coder / Researcher / Auditor / Planner /
        Orchestrator), then always hands the raw output to the Synthesizer.
        Scope: the main chat path (``process_input``) only. The
        multi-model and A/B orchestrators exist to compare personas/models
        on the *same* prompt, so they keep the raw user text and never
        route. The routed text is fed into the normal context + provider
        path as context; it does not replace ``CONFIG["active_persona"]``.

        REGRESSION GUARD (mock mode must never reach a provider):
        tests/test_kokertechController.py::TestControllerMockMode asserts
        ``chat_completion`` is not called in mock mode. Sub-agents resolve a
        provider via ``ai_base.get_provider()`` — a lookup the orchestrators
        cannot patch — so routing is skipped outright when
        ``CONFIG["mock_mode"]`` is set.

        Intent dispatch is an enhancement, never a hard dependency: any
        router failure (including a sub-agent error string) falls back to the
        original user text so chat still works with a dead provider.
        """
        if CONFIG.get("mock_mode", False):
            return text
        try:
            routed = router(text, model=CONFIG.get("model_name", ""))
        except Exception as exc:  # router must never break the chat path
            logger.debug(f"Intent router failed, using raw input: {exc}")
            if log:
                log(f"Intent router unavailable ({exc}); using raw input.")
            return text
        if not isinstance(routed, str) or not routed.strip():
            return text
        if routed.lstrip().startswith(("Error:", "Failed:")):
            logger.debug(f"Intent router returned sub-agent error: {routed[:120]}")
            return text
        return routed

    def process_input(self, text, agent_type="Executive", log_callback=None, cancel_event=None, stream_callback=None):
        """Process user input and return AI response.

        Args:
            text: User input text.
            agent_type: Agent persona type.
            log_callback: Optional callable for progress log messages.
            cancel_event: Optional threading.Event for cancellation.
            stream_callback: Optional callable(str) for progressive token streaming.
                When provided, the AI response tokens are emitted in real-time
                via this callback as they arrive from the provider's SSE stream.
                The full parsed result is still returned at the end.
        """
        log = log_callback if log_callback else (lambda x: None)
        self._summarize_and_evict(log)
        self._refresh_history_mirror()

        # ── Autonomous Hive Swarm Routing (Realm 3) ──
        if self._hive_svc:
            is_hive, hive_goal = self._hive_svc.should_route_to_hive(text)
            if is_hive:
                log("🐝 Routing task to Autonomous Multi-Agent Hive...")
                with self._lock:
                    self.history.append({"role": "user", "content": text})
                hive_res = self._hive_svc.execute_hive_flow(
                    goal=hive_goal,
                    log_callback=log,
                    stream_callback=stream_callback,
                    cancel_event=cancel_event,
                )
                with self._lock:
                    self.history.append({"role": "assistant", "content": hive_res.get("final", "")})

                log("STEP 4.5: Committing Hive deliverable to Long-Term Vault...")
                try:
                    memory_vault.store_memory(
                        f"User: {text}\nHive Deliverable:\n{hive_res.get('final', '')}",
                        node_type="interaction",
                    )
                except (RuntimeError, ValueError, OSError, TypeError) as e:
                    log(f"Hive memory storage failed: {e}")

                self.logger.ok("Hive swarm execution successfully completed and committed")
                return hive_res

        # ── Direct Sandboxed Command Routing (Realm 4) ──
        cmd_pattern = re.compile(r"^\s*(?:<<CMD:(.+)>>|<<EXEC:(.+)>>|/cmd\s+(.+))\s*$", re.IGNORECASE | re.DOTALL)
        cmd_match = cmd_pattern.match(text)
        if cmd_match and getattr(self, "_tool_exec_svc", None):
            raw_cmd = (cmd_match.group(1) or cmd_match.group(2) or cmd_match.group(3) or "").strip()
            if raw_cmd:
                log(f"⚡ Executing sandboxed command: {raw_cmd[:80]}")
                allow_destructive = "--allow-destructive" in raw_cmd
                clean_cmd = raw_cmd.replace("--allow-destructive", "").strip()
                res = self._tool_exec_svc.execute_sandboxed_command(
                    cmd=clean_cmd,
                    allow_destructive=allow_destructive,
                )
                output_lines = [
                    f"**Command**: `{clean_cmd}`",
                    f"**Status**: `{res.status}` (exit code {res.exit_code}, {res.duration_ms}ms)",
                ]
                if res.stdout:
                    output_lines.append(f"```\n{res.stdout}\n```")
                if res.stderr:
                    output_lines.append(f"**STDERR**:\n```\n{res.stderr}\n```")
                if res.error_message:
                    output_lines.append(f"⚠️ **Error**: {res.error_message}")
                result_text = "\n\n".join(output_lines)
                with self._lock:
                    self.history.append({"role": "user", "content": text})
                    self.history.append({"role": "assistant", "content": result_text})
                return {"thinking": "", "final": result_text}

        log("STEP 1/5: Fetching context from Long-Term Vault (parallel)...")
        components = self._stage_fetch_context(text, agent_type)
        msg_cap = self._message_cap(1500)
        context = self._truncate(components["context"], msg_cap)
        context += self._router_dispatch_block(text, log)

        # ── RAG detection ──
        rag_query = None
        rag_match = re.match(r'^<<RAG:(.+)>>$', text.strip(), re.IGNORECASE | re.DOTALL)
        if rag_match:
            rag_query = rag_match.group(1).strip()
            log("RAG: explicit <<RAG:...>> query")
        else:
            text_lower = text.lower()
            word_count = len(text.split())
            has_high_specificity = any(t in text_lower for t in _HIGH_SPECIFICITY_TRIGGERS)
            has_general_trigger = any(t in text_lower for t in _DEEP_RESEARCH_TRIGGERS)
            if has_high_specificity or (has_general_trigger and word_count >= _DEEP_RESEARCH_MIN_WORDS):
                rag_query = text
                if has_high_specificity:
                    log("RAG: high-specificity trigger")
                else:
                    log("RAG: automatic trigger")

        if rag_query:
            log("RAG pipeline starting...")
            try:
                rag_result = self.rag_engine.answer(
                    query=rag_query,
                    session_id=memory_vault.get_current_session_id(),
                    progress_callback=self.rag_progress_callback,
                )
                if rag_result.get("error"):
                    log(f"RAG error: {rag_result['error']}")
                else:
                    answer_summary = rag_result.get("answer", "")
                    top_evidence = rag_result.get("contexts", [])[:5]
                    evidence_text = "\n".join([f"  [{i+1}] ({c.get('source', '?')}) {c.get('content', '')[:300]}" for i, c in enumerate(top_evidence)])
                    rag_injection = f"\n\n[DEEP RESEARCH RESULTS]:\nQuery: {rag_query}\nSynthesis: {answer_summary}\nEvidence:\n{evidence_text}\n[END RESEARCH]"
                    context += rag_injection
                    if rag_match:
                        text = rag_query
            except Exception as e:
                log(f"RAG crashed: {e}")
                self.logger.error(f"RAG pipeline crashed: {e}", exc_info=sys.exc_info())

        # ── Local Multimodal Sensory Mesh Perception (Realm 1) ──
        if self._sensory_svc:
            is_sensory, sensory_query, sensory_target = self._sensory_svc.should_route_sensory(text)
            if is_sensory:
                log(f"📷 [SENSORY MESH] Capturing in-memory desktop framebuffer ({sensory_target})...")
                frame = self._sensory_svc.capture_framebuffer(target=sensory_target)
                if frame:
                    log(f"🔍 [SENSORY MESH] Analyzing visual observation ({frame.width}x{frame.height})...")
                    vision_analysis = self._sensory_svc.analyze_frame(
                        frame=frame,
                        prompt=sensory_query,
                    )
                    sensory_injection = (
                        f"\n\n[LOCAL SENSORY MESH OBSERVATION]:\n"
                        f"Target: {frame.target} ({frame.width}x{frame.height})\n"
                        f"Timestamp: {frame.timestamp}\n"
                        f"Visual Analysis:\n{vision_analysis}\n"
                        f"[END SENSORY OBSERVATION]"
                    )
                    context += sensory_injection
                    if text.strip().startswith(("<<SEE", "<<VISION", "/see")):
                        text = sensory_query

        user_profile = self._load_user_profile()
        scbe_text = components["scbe_text"]
        growth_text = components["growth_text"]

        if len(self.compressed_context_summary) > 2000:
            self.compressed_context_summary = "..." + self.compressed_context_summary[-2000:]

        persona = CONFIG.get("active_persona", "You are a helpful AI assistant.")
        freeform = CONFIG.get("freeform_mode", False)
        mock = CONFIG.get("mock_mode", False)

        log("STEP 2/5: Compiling prompt...")
        log(f"Mode: {'Freeform' if freeform else 'Structured'} | {'MOCK' if mock else 'Live'}")

        custom_protocol = CONFIG.get("protocol_prompt", "").strip()
        mode = "freeform" if freeform else "structured"
        system_prompt = self._pb.build_system_prompt(mode=mode, custom_protocol=custom_protocol or None)
        if custom_protocol:
            log("Using custom system protocol")

        system_prompt = self._inject_skill_policy(system_prompt)

        bias_feedback = self._get_recent_biases()
        if bias_feedback:
            system_prompt += bias_feedback
            log("Injected recent biases (cognitive loop)")

        try:
            import plugin_registry
            system_prompt = self._prompt_svc.inject_progressive_disclosure(system_prompt, plugin_registry.registry, log=lambda msg: log(f"   {msg}"))
        except ImportError:
            pass

        episodic_context = components["episodic_context"]
        weighted_episodic = components["weighted_episodic"]

        context_text = self._build_context_text(persona, user_profile, episodic_context, weighted_episodic, context, scbe_text, growth_text, freeform=freeform)

        history_copy = self._stage_build_history(text)
        messages = [{"role": "system", "content": f"{system_prompt}\n\n{context_text}"}]
        messages += self._stage_assemble_history_messages(history_copy, msg_cap)

        log("STEP 3/5: Transmitting to AI provider...")

        if mock:
            import random
            ai_text = random.choice([f"[MOCK] help with {text[:60]}", f"[MOCK] processing {text[:80]}"])
            if stream_callback:
                stream_callback(ai_text)
            with self._lock:
                self.history.append({"role": "assistant", "content": ai_text})
            return self.parse_ai(ai_text, freeform=True)

        target_model = self._load_target_model()

        try:
            budget_chars = self._model_input_budget(target_model)
        except Exception as e:
            self.logger.debug(f"_model_input_budget failed (using 4096 fallback): {e}")
            budget_chars = 4096
        total_input_chars = sum(len(m.get("content", "")) for m in messages)
        if total_input_chars > budget_chars:
            err = f"Context overflow: {total_input_chars} chars exceeds safe budget {budget_chars} chars for model {target_model}."
            return self._reject_with_error(
                err,
                final=f"❌ Context overflow ({total_input_chars} > {budget_chars}). Try shorter input or wipe chat history.",
            )

        # Sprint 12: GBNF grammar-constrained structured output.
        # JSON mode: uses llama-cpp-python's built-in json_object response_format
        # when structured_format="json". XML mode: uses GBNF grammar.
        # "auto" mode: chooses JSON for data-extraction queries, XML for
        # thinking-heavy tasks based on keyword heuristics.
        grammar_rf = None
        if not freeform:
            structured_format = CONFIG.get("structured_format", "xml")
            fmt = structured_format
            if fmt == "auto":
                fmt = _detect_structured_format(text)
            if fmt == "json":
                grammar_rf = {"type": "json_object"}
            else:
                from ai_base import get_grammar
                grammar_rf = {"type": "grammar", "value": get_grammar("xml")}

        # ── Streaming path ──
        # When stream_callback is provided, use the provider's chat_completion_stream
        # to emit tokens in real-time. Tokens are accumulated into full_text and
        # processed normally at the end.
        if stream_callback:
            ai_text = self._execute_stream(
                messages=messages, model=target_model, temperature=0.1,
                max_tokens=-1, timeout=600, log=log,
                cancel_event=cancel_event, stream_callback=stream_callback,
                response_format=grammar_rf,
            )
            if ai_text is None:
                # Streaming failed — fall back to non-streaming path
                log("Streaming failed, falling back to non-streaming...")
                result, fallback_name = self._execute_with_fallback(
                    messages=messages, model=target_model, temperature=0.1,
                    max_tokens=-1, timeout=600, log=log, cancel_event=cancel_event,
                    response_format=grammar_rf,
                )
                if result.get("error"):
                    log(f"Engine Sync Error: {result['error']}")
                    self.logger.error(f"Engine sync error: {result['error']}")
                    if self.history and self.history[-1]["role"] == "user":
                        self.history.pop()
                    return {"error": f"Engine Sync Error: {result['error']}"}
                ai_text = result["content"]
                fallback_name = fallback_name
            else:
                fallback_name = "local_llm"
        else:
            # ── Non-streaming path (existing behavior) ──
            result, fallback_name = self._execute_with_fallback(
                messages=messages, model=target_model, temperature=0.1,
                max_tokens=-1, timeout=600, log=log, cancel_event=cancel_event,
                response_format=grammar_rf,
            )
            if result.get("error"):
                log(f"Engine Sync Error: {result['error']}")
                self.logger.error(f"Engine sync error: {result['error']}")
                if self.history and self.history[-1]["role"] == "user":
                    self.history.pop()
                return {"error": f"Engine Sync Error: {result['error']}"}
            ai_text = result["content"]

        log("STEP 4/5: Extracting neural output...")

        if self._is_sentinel_response(ai_text):
            err = f"Engine returned sentinel/example text '{ai_text[:40]}...' Total input: {total_input_chars} chars vs budget {budget_chars}."
            return self._reject_with_error(err)

        self._prompt_svc.register_pending_load(ai_text, log=log)

        with self._lock:
            self.history.append({"role": "assistant", "content": ai_text})

        log("STEP 4.5: Committing interaction to Long-Term Vault...")
        try:
            memory_vault.store_memory(f"User: {text}\nAgent ({agent_type}): {ai_text}", node_type="interaction")
        except Exception as e:
            log(f"Memory storage failed: {e}")

        self.logger.ok(f"AI response processed for agent_type={agent_type}")
        parsed = self.parse_ai(ai_text, freeform=freeform)
        if self._is_sentinel_response(parsed.get("final", "")):
            return self._reject_with_error(
                "Engine returned sentinel in parsed final",
                thinking=parsed.get("thinking", ""),
                pop_assistant=True,
            )
        return parsed

    def _execute_stream(self, messages, model, temperature, max_tokens, timeout, log, cancel_event=None, stream_callback=None, response_format=None):
        """Execute a streaming chat completion using the local LLM provider.

        Sprint 19.3: delegates to ``ProviderStreamService.execute_stream``,
        forwarding ``self.provider`` (which tests replace with stubs).
        Returns None if streaming fails so the caller can fall back to the
        non-streaming path.
        """
        return self._stream_svc.execute_stream(
            self.provider, messages, model, temperature, max_tokens, timeout,
            cancel_event=cancel_event,
            stream_callback=stream_callback,
            response_format=response_format,
        )

    def process_input_multi(self, text, model_specs, agent_type="Executive", log_callback=None, cancel_event=None):
        """Dispatch the same prompt to multiple models in parallel.

        Args:
            text: User input text.
            model_specs: List of {"provider": str, "model": str, "label": str} dicts.
            agent_type: Agent persona type.
            log_callback: Optional callable for progress log messages.
            cancel_event: Optional threading.Event for cancellation.
                When set, each provider call returns a cancellation error
                instead of executing.
        """
        log = log_callback if log_callback else (lambda x: None)
        self._summarize_and_evict(log)


        log("MULTI-MODEL: Fetching context components in parallel...")
        components = self._stage_fetch_context(text, agent_type)
        msg_cap = self._message_cap(1500)
        context = self._truncate(components["context"], msg_cap)

        if len(self.compressed_context_summary) > 2000:
            self.compressed_context_summary = "..." + self.compressed_context_summary[-2000:]

        persona = CONFIG.get("active_persona", "You are a helpful AI assistant.")
        custom_protocol = CONFIG.get("protocol_prompt", "").strip()
        system_prompt = self._build_freeform_system_prompt(custom_protocol)
        system_prompt = self._inject_skill_policy(system_prompt)
        system_prompt = self._inject_agent_contract(system_prompt)

        bias_feedback = self._get_recent_biases()
        if bias_feedback:
            system_prompt += bias_feedback

        try:
            import plugin_registry
            system_prompt = self._prompt_svc.inject_progressive_disclosure(system_prompt, plugin_registry.registry, log=lambda msg: log(f"   {msg}"))
        except ImportError:
            pass

        user_profile = self._load_user_profile()
        context_text = self._build_context_text(persona, user_profile, components["episodic_context"], components["weighted_episodic"], context, components["scbe_text"], components["growth_text"], freeform=True)

        history_copy = self._stage_build_history(text)
        messages = [{"role": "system", "content": f"{system_prompt}\n\n{context_text}"}]
        messages += self._stage_assemble_history_messages(history_copy, msg_cap)

        log(f"MULTI-MODEL: Dispatching to {len(model_specs)} model(s)...")

        results = []
        results_lock = threading.Lock()

        def _query(spec):
            try:
                provider = get_provider(name=spec["provider"])
                result = cached_chat_completion(provider, messages=messages, model=spec["model"], temperature=0.1, max_tokens=-1, timeout=600, request_id=None, cancel_event=cancel_event)
                with results_lock:
                    if result.get("error"):
                        results.append({"provider": spec["provider"], "model": spec["model"], "label": spec.get("label", f"{spec['provider']}/{spec['model']}"), "ok": False, "content": "", "error": result["error"], "ts": datetime.now().strftime("%H:%M:%S")})
                    elif self._is_sentinel_response(result.get("content", "")):
                        results.append({"provider": spec["provider"], "model": spec["model"], "label": spec.get("label", f"{spec['provider']}/{spec['model']}"), "ok": False, "content": "", "error": "Engine returned sentinel/example text", "ts": datetime.now().strftime("%H:%M:%S")})
                    else:
                        results.append({"provider": spec["provider"], "model": spec["model"], "label": spec.get("label", f"{spec['provider']}/{spec['model']}"), "ok": True, "content": result["content"], "error": None, "ts": datetime.now().strftime("%H:%M:%S")})
            except Exception as e:
                with results_lock:
                    results.append({"provider": spec["provider"], "model": spec["model"], "label": spec.get("label", f"{spec['provider']}/{spec['model']}"), "ok": False, "content": "", "error": str(e), "ts": datetime.now().strftime("%H:%M:%S")})

        threads = []
        for spec in model_specs:
            t = threading.Thread(target=_query, args=(spec,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=600)

        combined_lines = []
        for r in results:
            if r["ok"]:
                combined_lines.append(f"[{r['label']}]:\n{r['content']}")
            else:
                combined_lines.append(f"[{r['label']}]: ERROR -- {r['error']}")
        combined_text = "\n\n---\n\n".join(combined_lines)
        with self._lock:
            self.history.append({"role": "assistant", "content": combined_text})

        try:
            memory_vault.store_memory(f"User: {text}\nMulti-Model Response ({len(results)} models): {combined_text[:200]}...", node_type="multi_model_interaction")
        except Exception as e:
            self.logger.warning(f"Multi-model memory storage failed (vault write — possible data loss): {e}")

        log(f"MULTI-MODEL: {len(results)} responses received")
        return results

    def process_input_with_personas(self, text, persona_a, persona_b, agent_type="Executive", log_callback=None, cancel_event=None, cancel_event_b=None, stream_callback=None):
        """Send the same prompt with two different personas side-by-side.

        Args:
            text: User input text.
            persona_a: First persona system prompt.
            persona_b: Second persona system prompt.
            agent_type: Agent persona type.
            log_callback: Optional callable for progress log messages.
            cancel_event: Optional threading.Event for cancelling persona A.
                Also used for persona B unless ``cancel_event_b`` is provided.
            cancel_event_b: Optional threading.Event for cancelling persona B
                only. Defaults to ``cancel_event`` for backward compatibility.
            stream_callback: Optional callable(str) for progressive token
                streaming. When provided, persona A's tokens are streamed
                first, then persona B's. The full parsed result for both
                is still returned at the end.
        """
        log = log_callback if log_callback else (lambda x: None)

        log("A/B TEST: Building shared context in parallel...")
        components = self._stage_fetch_context(
            text, agent_type, episodic_limit=2, episodic_placeholder="",
        )
        msg_cap = self._message_cap(1500)
        context = self._truncate(components["context"], msg_cap)
        scbe_text = components["scbe_text"]
        episodic_context = components["episodic_context"]
        weighted_episodic = components["weighted_episodic"]

        history_copy = self._stage_build_history(text)

        base_messages = self._stage_assemble_history_messages(history_copy, msg_cap)

        target_model = self._load_target_model()

        def _query_with_persona(persona_text, label, ce=None):
            system_prompt = (f"[AGENT INITIALIZATION]\n{persona_text}\n\n[LONG-TERM MEMORY]\n{context}\n\n[RECENT BIASES TO AVOID]\n{scbe_text}\n\n[EPISODIC JOURNAL]\n{episodic_context}\n\n[TOP RELEVANT ENTRIES]\n{weighted_episodic}\n\nYou are in A/B test mode.")
            messages = [{"role": "system", "content": system_prompt}] + base_messages
            try:
                if stream_callback:
                    # Streaming path — accumulate tokens, fall back on failure
                    content = self._execute_stream(
                        messages=messages, model=target_model,
                        temperature=0.3, max_tokens=-1, timeout=600,
                        log=log, cancel_event=ce,
                        stream_callback=stream_callback,
                    )
                    if content is None:
                        # Streaming failed — fall back to non-streaming
                        log(f"A/B TEST [{label}]: streaming failed, fallback...")
                        result, _ = self._execute_with_fallback(
                            messages=messages, model=target_model,
                            temperature=0.3, max_tokens=-1, timeout=600,
                            log=log, cancel_event=ce,
                        )
                    else:
                        result = {"content": content, "error": None, "tool_calls": []}
                else:
                    result, _ = self._execute_with_fallback(
                        messages=messages, model=target_model,
                        temperature=0.3, max_tokens=-1, timeout=600,
                        log=log, cancel_event=ce,
                    )
                if result.get("error"):
                    return {"ok": False, "content": "", "error": result["error"]}
                if self._is_sentinel_response(result.get("content", "")):
                    return {"ok": False, "content": "", "error": "Engine returned sentinel/example text"}
                return {"ok": True, "content": result["content"], "error": None}
            except Exception as e:
                return {"ok": False, "content": "", "error": str(e)}

        log("A/B TEST: Dispatching...")
        results_a = _query_with_persona(persona_a, "A", cancel_event)
        results_b = _query_with_persona(persona_b, "B", cancel_event_b if cancel_event_b is not None else cancel_event)

        parsed_a = self.parse_ai(results_a["content"], freeform=True) if results_a["ok"] else {"final": f"Error: {results_a['error']}", "thinking": ""}
        parsed_b = self.parse_ai(results_b["content"], freeform=True) if results_b["ok"] else {"final": f"Error: {results_b['error']}", "thinking": ""}

        log("A/B TEST: Both responses received")

        try:
            vote_id = memory_vault.record_persona_vote(prompt=text, persona_a=persona_a[:100], persona_b=persona_b[:100], response_a=parsed_a["final"][:2000], response_b=parsed_b["final"][:2000])
        except Exception as e:
            self.logger.warning(f"Persona vote recording failed (vault write — possible data loss): {e}")
            vote_id = -1

        return {"prompt": text, "persona_a": persona_a, "persona_b": persona_b, "response_a": parsed_a, "response_b": parsed_b, "vote_id": vote_id, "error": None}

    def parse_ai(self, content, freeform=False):
        return self._prompt_svc.parse_ai(content, freeform)

    # ── Provider health-check and model swap delegation ──

    def provider_health_check(self) -> dict:
        """Delegate health check to the provider service."""
        return self._provider_svc.provider_health_check()

    def hot_swap_model(self, model_file: str, **kwargs) -> dict:
        """Delegate model swap to the provider service and sync local provider.

        Accepts and forwards hot-swap kwargs (e.g. a ``progress``
        callback for verbose load reporting) to the provider service.
        On success, syncs ``self.provider`` and refreshes the legacy
        ``_current_model_path`` alias that UI code reads.
        """
        result = self._provider_svc.hot_swap_model(model_file, **kwargs)
        if result.get("ok"):
            self.provider = self._provider_svc.provider
            try:
                provider = self.provider
                if provider is not None:
                    path = (getattr(provider, "_model_path", "")
                            or getattr(provider, "model_file", ""))
                    if path:
                        provider._current_model_path = path
            except (AttributeError, RuntimeError) as err:
                logger.debug(f"Failed to record current model path: {err}")
        return result

    def execute_sandboxed_command(self, cmd, **kwargs):
        """Execute a system command through the sandboxed tool execution service."""
        if not getattr(self, "_tool_exec_svc", None):
            from services.tool_execution_service import ToolExecutionService
            self._tool_exec_svc = ToolExecutionService(workspace=self.workspace)
        return self._tool_exec_svc.execute_sandboxed_command(cmd, **kwargs)