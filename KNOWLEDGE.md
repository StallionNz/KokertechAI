# KokertechAI — Knowledge Base

> **The canonical reference for how KokertechAI works**: architecture patterns, design decisions, coding conventions, and operational knowledge.
> Last updated: October 9, 2026

---

## 1. Project Identity & Philosophy

| Attribute | Value |
|-----------|-------|
| **Creator** | Jacques de Koker (StallionNz) — sole developer and owner |
| **Company** | Kokertech Pty Ltd |
| **Purpose** | Private, autonomous AI desktop automation system |
| **Philosophy** | 100% offline, user-owned, no telemetry, no cloud dependency |
| **Design target** | JARVIS-level digital entity — not a chatbot |
| **Lang/Framework** | Python 3.10+ / PyQt6 |
| **AI backends** | Local LLM via llama-cpp-python (GGUF models, 100% in-process) |
| **Version control** | Git — published on GitHub (StallionNz/KokertechAI). Internal notes still dated from file mtimes + execution_log.txt |
| **Test framework** | pytest (153 test files in `tests/` — 0 at root; grew from 70 baseline via July 2026 sprint additions; 4,200+ passing, **zero pre-existing failures**, pytest-qt 4.5.0 disabled via `-p no:pytest-qt` in `pytest.ini`) |
| **Pre-flight gates** | 12 script-based gates via `scripts/run_all_gates.py` (`--fast` = fast subset), incl. `assert_known_anchors.py` doc hygiene; `pytest` via `scripts/run_parallel_chunked.py`; per-gate detail in §12 |
| **Sprints** | 12 feature sprints / ~437 hours core scope. Sprints 1–12 ✅ + Sprint 21 cleanup. |

---

## Topical Index (2026-07-19)

> **Purpose.** Stable, durable landing page for the 25 `##` sections below — lists every section with a 1-sentence summary AND a "When you need to …" lookup for common contributor questions. Drifts only when a new `##` section is added (append a single row); all other content is auto-anchored.

**Master Index — all 25 sections.**

| § | Topic | 1-sentence summary |
|---|-------|--------------------|
| §1 | [Project Identity & Philosophy](#1-project-identity--philosophy) | Project metadata, philosophy, test-framework baseline, sprint count | Read first when onboarding or introducing KokertechAI to a newcomer. |
| §2 | [Architecture Overview](#2-architecture-overview) | Three interface layers (PyQt6 / Flask / CLI) → application layer (config + plugins + AI + memory + sub-agents + workflow + 24 tabs) → data layer (vault + settings) | Read first when planning a major module or onboarding a contributor. |
| §3 | [Key Design Decisions](#3-key-design-decisions) | Decisions 7–8: local Git (revoked no-Git, 2026-09-22), 3-layer inference safety guards | Read when wondering why a design choice exists (e.g. why local git, why three guards). |
| §4 | [Memory Architecture (3-Tier)](#4-memory-architecture-3-tier) | Tier-1 short-term history → Tier-2 episodic journal → Tier-3 long-term core memories; consolidation flow | Read when working on memory storage, consolidation, or `memory_vault.py` features. |
| §5 | [AI Provider Architecture](#5-ai-provider-architecture) | Single in-process `LocalLLMProvider` via llama-cpp-python; model assignments; `get_provider()` adoption | Read when changing AI backends, debugging inference errors, or adopting `get_provider()`. |
| §6 | [Plugin System](#6-plugin-system) | 28 native plugins with COMMAND_NAME + execute() + optional SCHEMA; progressive disclosure via <<LOAD_TOOLS>> | Read when adding new plugins or working on `plugin_registry.py`. |
| §7 | [Workflow Engine](#7-workflow-engine) | YAML pipeline with 4 action types (`plugin_command`, `sub_agent`, `crew`, `sandbox`); dependency graph via `depends_on` | Read when building or debugging multi-step workflows. |
| §8 | [Sub-Agent Personas](#8-sub-agent-personas) | 7 personas (Researcher / Coder / Auditor / Planner / ToolUser / Orchestrator / Synthesizer) with distinct temperature + max_tokens | Read when adding personas or changing `sub_agents.py`. |
| §9 | [UI Tab System](#9-ui-tab-system) | 24 tab modules across 24 files; imports + thread-safety + error-handling conventions | Read when adding new tabs, threading UI work, or wiring error paths. |
| §10 | [Coding Conventions](#10-coding-conventions) | Services / Schemas / Registries / Pools naming; Sprint 17 lazy-singleton + factory + isolation conventions | Read when adding new §10-eligible symbols (service / schema / registry / pool / singleton / factory). |
| §11 | [Configuration System](#11-configuration-system) | `app_settings.json` + `user_identity.json` + `personas.json`; secret-loading priority (keyring → env → legacy); auto-backup | Read when adding CONFIG keys or Settings-tab wiring. |
| §12 | [Testing Patterns](#12-testing-patterns) | 153-test-file suite, QApplication lifecycle, cross-file isolation, `_suppress_quit` flag, ANTI-FRAGILITY/REGRESSION GUARD doc-comments | Read when writing tests, debugging CI failures, or adopting ANTI-FRAGILITY comments. |
| §13 | [Common Pitfalls & Gotchas](#13-common-pitfalls--gotchas) | 400 errors, perpetual audit logs, C++ timer errors, cross-file Qt hangs, fd exhaustion, pytest pipe starvation, silent broad-catch bug-masking | Read FIRST when hitting a known error pattern — your bug is probably here. |
| §14 | [Protocol Preset System](#14-protocol-preset-system) | 3 built-in templates (Structured + Freeform + Concise Coder) editable via Settings; live validation + amber warnings | Read when changing AI prompt protocols or building the protocol editor. |
| §15 | [Document Inventory](#15-document-inventory) | Master list of the root docs + supporting scripts; `mcp_server.py` reliability upgrades | Read when locating a doc, identifying documentation gaps, or wiring DR cron jobs. |
| §16 | [Quick Reference](#16-quick-reference) | Startup sequence; edit-wiring for common tasks; AI agent tool-usage patterns (basher escape, disk-write-then-run) | Read FIRST when onboarding as a Buffy/basher agent — operational conventions live here. |
| §17 | [Worker Signal Wiring Patterns](#17-worker-signal-wiring-patterns) | 4 rules: lifecycle signals for cleanup, main-thread hop, idempotent hooks, session-end reconciliation | Read when wiring worker cleanup hooks, threading fixes, or auditing for the perpetual-audit-log bug. |
| §18 | [KeepaliveContext](#18-keepalivecontext----context-manager-status-abort-hook) | Context manager + auto-abort hook + click-handler button disable/restore for long-running operations | Read when adding long-running UI affordances (multi-second AI calls, batch jobs). |
| §19 | [Architecture Evolution](#19-architecture-evolution) | kokertech agent (formerly L99) 2026-07-21 review status — 4 critical + 6 high + 7 decisions + 6 edge cases; drift-prevention checklist; PENDING/DEFERRED baseline (19.3-19.5 + L99.5); cross-links to re-assessment + decomposition plan | Read when reviewing architecture, running the 12-item verification checklist, planning Sprint 19 (kokertech controller decomposition), or tracking Sprint closure progress against the PENDING/DEFERRED baseline. |
| §20 | [AST-Based Splice Hardening](#20-ast-based-splice-hardening-june-19-2026) | Iter6 AST-only pipeline (no regex) for splicing 15 `_create_<X>_group` methods into Settings tab | Read when needing the splice pipeline (Settings-tab method-runs, KNOWN-anchors check). |
| §21 | [Offscreen Smoke Gate](#21-offscreen-smoke-gate-runtime-validation) | Runtime validation (py_compile cannot catch NameError / AttributeError); `ALL_15_OK` pattern | Read when adding UI tab modules or running offscreen smoke validation. |
| §22 | [Resource Monitoring](#22-resource-monitoring) | 2-second QTimer runs VRAM + RAM + disk monitors; warning/critical thresholds; rate-limiting + recovery events | Read when adding resource monitoring, tweaking thresholds, or wiring health events. |
| §23 | [Thread-Safety in LocalLLMProvider](#23-thread-safety-in-localllmprovider) | 4 known gaps (3 fixed + 1 mitigated): daemon-thread races, swap_model use-after-free, health_check races, multi-turn VRAM | Read when debugging `GGML_ASSERT` crashes, VRAM exhaustion, or llama.cpp threading issues. |
| §24 | [Vault Maintenance Toolkit](#24-vault-maintenance-toolkit-scriptsvaultmaintenancepy-sprint-1982) | Opt-in vault health report + retention, FTS rebuild, VACUUM, staged `--migrate-embeddings` | Read when running vault maintenance or retention pruning. |
| §25 | [The Vault Recovery Saga](#25-the-vault-recovery-saga-742941--12-2026-09-22) | The 742,941 → 12 corrupted-row quarantine recovery (2026-09-22) | Read when investigating corrupted-row quarantine history. |

**When you need to … (common contributor questions).**

| When you need to … | Read this § |
|--------------------|-------------|
| Onboard a new contributor (or yourself) to KokertechAI | [§1](#1-project-identity--philosophy) + [§2](#2-architecture-overview) + [§16](#16-quick-reference) |
| Onboard as a Buffy / basher code-writing agent | [§16](#16-quick-reference) — operational conventions + basher escape patterns |
| Add a new CONFIG setting or Settings-tab control | [§11](#11-configuration-system) + [§16 edit-wiring table](#16-quick-reference) |
| Add a new module-level singleton, factory, or registry | [§10](#10-coding-conventions) — Sprint 17 Project-Wide Singleton Convention (R12) |
| Debug a singleton leaking state across test files | [§10](#10-coding-conventions) — Cross-File Isolation Invariant (R5+R9) |
| Add a new plugin | [§6](#6-plugin-system) — auto-registration + SCHEMA pattern |
| Add a new tab | [§9](#9-ui-tab-system) — imports + threading + error conventions |
| Add a new sub-agent persona | [§8](#8-sub-agent-personas) — temperature + max_tokens tuning |
| Wire a worker cleanup hook | [§17](#17-worker-signal-wiring-patterns) — Rule 1 (lifecycle signals for cleanup) |
| Add a long-running UI affordance (button + "Still processing...") | [§18](#18-keepalivecontext----context-manager-status-abort-hook) — context manager + auto-abort hook |
| Debug an AI inference crash (`GGML_ASSERT` / VRAM exhaustion / model-echo) | [§23](#23-thread-safety-in-localllmprovider) + [§5](#5-ai-provider-architecture) Decision 8 |
| Debug a cross-file pytest failure or QApplication leak | [§12](#12-testing-patterns) + [§13](#13-common-pitfalls--gotchas) (Cross-file Qt hangs row) |
| Hit a known error pattern (perpetual audit log, fd exhaustion, pipe-starvation, silent broad-catch) | [§13](#13-common-pitfalls--gotchas) — read this FIRST |
| Hit a vault / consolidation / embedding bug | [§4](#4-memory-architecture-3-tier) + [§12 memory_vault tests](#12-testing-patterns) |
| Find a doc, identify a documentation gap, or update the doc inventory | [§15](#15-document-inventory) |
| Review the architecture or run drift-prevention checks | [§19](#19-architecture-evolution) — kokertech agent (formerly L99) review + 12-item checklist |
| Track Sprint 19 closure progress or verify PENDING/DEFERRED inventory | [§19](#19-architecture-evolution) — PENDING/DEFERRED Baseline subsection (drift-resistance snapshot, 2026-07-21) |
| Add a new pre-flight CI gate or ANTI-FRAGILITY doc-comment | [§12](#12-testing-patterns) ANTI-FRAGILITY/REGRESSION GUARD pattern + [§10 R12 lock-order](#10-coding-conventions) |

**Drift-resistance note.** New `##` sections → append a single row to *Master Index*. New contributor questions → append a single row to *When you need to …*. Both tables auto-anchor to GitHub-style heading anchors.

---

## 2. Architecture Overview

### Three Interface Layers

```
┌──────────────────────────────────────────────────────┐
│                  INTERFACE LAYER                      │
├─────────────┬─────────────────────┬──────────────────┤
│  PyQt6 GUI  │   Flask Web         │   Terminal CLI   │
│ (app_core)  │ (kokerpro_web:5050) │ (kokertech_term) │
└──────┬──────┴──────────┬──────────┴───────┬──────────┘
       │                 │                   │
       └─────────────────┼───────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│                APPLICATION LAYER                      │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌───────┐ │
│  │ config   │ │ plugin   │ │ ai_base   │ │memory │ │
│  │ .py      │ │_registry │ │ (1 provider) ││_vault │ │
│  └──────────┘ └──────────┘ └───────────┘ └───────┘ │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐            │
│  │ sub_     │ │workflow_ │ │ 24 tab    │            │
│  │ agents   │ │engine    │ │ modules   │            │
│  └──────────┘ └──────────┘ └───────────┘            │
└──────────────────────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────┐
│                   DATA LAYER                          │
│  ┌─────────────────┐ ┌───────────────┐               │
│  │ kokertech_vault │ │ app_settings  │               │
│  │ .db (SQLite)    │ │ .json         │               │
│  └─────────────────┘ └───────────────┘               │
└──────────────────────────────────────────────────────┘
```

### Key Module Sizes

| File | Lines | Purpose |
|------|-------|---------|
| `app_core.py` | **375** | Dashboard entry (refactored down from 935) |
| `kokertechController.py` | **1469** | AI dispatch engine + intent-router wiring (refactored from 1818) |
| `memory_vault.py` | **3848** | SQLite vault + embeddings + FTS5 hybrid search + GraphRAG |
| `ai_base.py` | **~1575** | AI provider abstraction (Local LLM via llama-cpp-python) |
| `config.py` | **~669** (141 tests @ 100% cov) | Centralized config + Sprint 12/13 keys |
| `plugin_registry.py` | **847** (28 plugins) | Plugin lifecycle + `PluginSchema` + `SchemaError` fail-soft validation |
| `sub_agents.py` | **520** (7 personas) | Sub-agent orchestration + intent router (`_detect_intent` / `router`) |
| `prompt_builder.py` | **292** | Prompt construction |
| `workflow_engine.py` | **506** | YAML pipeline engine |
| `doc_pipeline.py` | **323** | Document batch processing (classify + extract + batch) |
| `Discovery.py` | **179** | Model endpoint discovery and probing |
| `auto_logger.py` | **244** | stdout/stderr interceptor + journal event system |
| `code_review_workflow.py` | **189** | CodeIntelligenceFactory consumer for code review |
| `copilot_features.py` | **365** | TTS engine (Piper + SAPI), VoiceOutput queue, wake-word integration |
| `app_hotkeys.py` | **364** | Global hotkey system (trigger, read, voice) |
| `app_plugins.py` | **189** | Plugin management UI for dashboard sidebar |
| `wake_word.py` | **299** | Speech-to-text + wake word detection |
| `html_sanitizer.py` | **429** | HTML sanitization (entity encoding, tag allow-list) |
| `cognitive_auditor.py` | **306** | LLM-powered bias/growth audit with SQLite persistence |
| `desire_engine.py` | **276** | Proactive suggestion generation via LLM |
| `action_replay.py` | **247** | Deterministic hash-keyed JSONL replay |
| `crew_integration.py` | **189** | CrewAI multi-agent orchestration wrapper |
| `git_tracker.py` | **349** | Git tracking and code change viewer |
| `keepalive_helper.py` | **306** | Keepalive context manager for long-running operations |
| `kokertech_logger.py` | **193** | Custom logger utilities with log rotation |
| `mcp_client.py` | **286** | MCP client for external tool management |
| `mcp_server.py` | **251** | MCP server for tool discovery and invocation |
| `model_registry.py` | **160** | Model registry for tracking available GGUF models |
| `models.py` | **106** | Pydantic model schemas for structured output |
| `notifications.py` | **232** | Desktop toast notifications (master kill-switch via CONFIG) |
| `save_session.md` | **122** | Sprint 14 Expansion session log (converted from misnamed `.py`, v0.22.18) |
| `workers.py` | **235** | Worker thread classes (AIWorker, MultiModelWorker, etc.) |
| `app_ui.py` | **2103** | Dashboard UI + mixins |
| `services/tool_use_agent.py` | **~338** | Tool-use agent with schema cache pre-warming |
| `logging_config.py` | **136** | Logging configuration |
| `services/registry.py` | **227** | ServiceRegistry DI container (Sprint 1) |
| `session_stats.py` | **331** | Session YAML aggregation + SVG badge generation (implemented from stubs) |
| `data/grammars/` | **empty** | Reserved for GBNF grammar definitions; no grammar files present |
| Tab modules (24) | **14,673** | UI tab modules (`tabs/*_tab.py`) |

---

## 3. Key Design Decisions

### Decision 7: Local Git Version Control (revokes the June 2026 no-Git choice; adopted 2026-09-22)
- **Decision**: Local-only Git repository — **no remote, no service, no network**. First commit `a8f5599` (2026-09-22, Sprint 19.8.3): 477 files, 6.57 MiB packed.
- **Original no-Git rationale** (still true): local-only project, single developer, no collaboration needed; history lived in file mtimes + execution_log.txt.
- **Why revoked**: mtime-history is not rollback. The July 2026 `.pyc`-corruption recovery (44 files quarantined, source reconstructed via decompilation) and every destructive maintenance op since proved that a wrong `rm` is unrecoverable without VCS. The 2026-09-22 full-project audit ranked absent version control as the #1 systemic risk.
- **Trade-off**: none material. The `.gitignore` (maintained since 2026-09-21) keeps secrets (`.env`), databases, 12 GB of local models, backups, and ephemera out of history — a repo can un-commit, but it cannot un-leak.

### Decision 8: 3-Layer Inference Safety Guards (June 2026)

- **Chosen**: Three independent guards prevent context-window overflow + model-echo bugs at distinct points in the inference pipeline (before / during / after the API call).
- **Why needed**: Small-context models (qwen2.5-0.5b @ 1024 tokens) overflow silently and echo training data (e.g. canned greetings like "Yo! What's on the bench today, mate?" or template lines like `Example 1 (Using a Tool):`) instead of answering. A hardcoded `2500`-char eviction cap and a missing pre-flight budget were both contributing factors.
- **Layer A — Model-aware eviction cap** (in `_summarize_and_evict`): replaced `text_to_summarize[-2500:]` with `text_to_summarize[-eviction_cap:]` where `eviction_cap = self._message_cap(1500)` — dynamically scales 1500-6000 chars per target model via `_dynamic_cap` (4x the base for tags matching `qwen`, `llama3.1`, `gpt-4o`, `claude-3`, `claude-sonnet`, `gpt-4-turbo`).
- **Layer B — Pre-flight token-budget guard** (in `process_input`, BEFORE the API call): `_model_input_budget(model_name)` looks up the 14-tag class-level table and returns `int(tokens * 4 * _SAFE_INPUT_MARGIN)` chars safe budget. Margin constant `_SAFE_INPUT_MARGIN = 0.7`.
- **Layer C — Post-flight sentinel detection** (in `process_input`, AFTER the API call): `_is_sentinel_response(ai_text)` rejects canned greetings + echoed example templates. **Exact-match guard**: `"Yo! What's on the bench today, mate?"` (case-insensitive). **Anchored regex guard**: `_SENTINEL_PATTERN = re.compile(r"^\s*Example\s+\d+\b", re.IGNORECASE)` matches structured-mode `Example 1:`, `Example 2 (Using a Tool):`, etc.
  - **Defense-in-depth**: Layer C also runs AGAIN on `parse_ai`'s extracted `final` field after the XML parse. Catches the case where the model wraps sentinel text inside `<thinking>…</thinking><final_output>SENTINEL</final_output>` tags that the raw `ai_text` check skipped (raw stripped starts with `<`, breaking the anchored regex).
  - **Regression test**: `test_defense_in_depth_wrapped_xml_sentinel` in `test_kokertechController.py::TestProcessInputPreFlightAndSentinel` pins this contract.
- **Error-shape contract** (all 3 guards return the same shape AND pop the just-appended user message from `self.history` BEFORE returning):

  ```python
  return {
      "error": "Engine returned <reason>. Total input: N chars vs budget M. Clear chat history or shorten input.",
      "final": "❌ <error message>",
      "thinking": "" (Layers A/B raw path) or parsed["thinking"] (Layer C defense-in-depth),
      "command": None,
      "ts": "%H:%M:%S",
  }
  ```
- **UI integration**: existing `app_lifecycle.py::handle_ai_response` → `_append_error_message(final_text, show_retry=True)` renders the error card with retry affordance.
- **Regression coverage**: `TestModelInputBudget` (16 tests, locks all 11 budget-table tags + 0.7 margin formula + table integrity), `TestIsSentinelResponse` (15 tests, locks exact-match greeting + anchored regex + 5 false-positive guards), `TestProcessInputPreFlightAndSentinel` (4 tests, locks both error paths + history-pop-on-reject invariant).
- **Sentinel coverage across all three orchestrators (v0.21.6):**

| Method | Layer A (raw text) | Layer C (parsed final) | Rationale |
|---|---|---|---|
| ``process_input()`` | ✅ Pops user | ✅ Pops user + assistant | Structured mode contains ``Example N:`` template guidance → XML wrapping is expected → Layer C needed |
| ``process_input_multi()`` | ✅ Surfaces as ``ok:False`` error | ❌ Not needed | Freeform mode — no ``Example N:`` template in system prompt; model would need to hallucinate XML wrapping it wasn't told to use |
| ``process_input_with_personas()`` | ✅ Surfaces as ``ok:False`` error | ❌ Not needed | Custom persona prompt — no ``Example N:`` template exists anywhere; model has nothing to echo |

- **Streaming + cancel_event support across all three orchestrators (v0.21.6):**

| Method | ``cancel_event`` | ``stream_callback`` | Notes |
|---|---|---|---|
| ``process_input()`` | ✅ Since Sprint 12 | ✅ Since Sprint 12 | Full support |
| ``process_input_multi()`` | ✅ Added v0.21.6 | ❌ | Multi-threaded dispatch makes streaming impractical; ``cancel_event`` passed to ``cached_chat_completion()`` per thread |
| ``process_input_with_personas()`` | ✅ Added v0.21.6 | ✅ Added v0.21.6 | Sequential dispatch — streams persona A then persona B; falls back to non-streaming on ``_execute_stream`` failure. **v0.21.7:** Added ``cancel_event_b`` parameter for independent cancellation of persona B (defaults to ``cancel_event`` for backward compatibility).

---

## 4. Memory Architecture (3-Tier)

```
USER INPUT
    │
    ▼
┌──────────────────────┐   consolidate_    ┌──────────────────────┐
│  TIER 1: SHORT-TERM  │   episodic()      │  TIER 2: EPISODIC    │
│  (Working Memory)    │  ──────────▶  │  (Journal)           │
│                      │                   │                      │
│  self.history        │                   │  episodic_journal    │
│  Limit: 4 messages   │                   │  table in vault      │
│  Eviction: summarize │                   │  (0 rows - ready)    │
└──────────────────────┘                   └──────────┬───────────┘
                                                       │
                                          promote on   │ importance >= 6
                                          or age > 7d  │
                                                       ▼
                                              ┌──────────────────────┐
                                              │  TIER 3: LONG-TERM   │
                                              │  (Core Memories)     │
                                              │                      │
                                              │  core_memories table │
                                              │  bias_ledger (55)    │
                                              │  growth_arcs (55)    │
                                              │  1 core memory entry │
                                              └──────────────────────┘
```

### Memory Tables

| Table | Rows | Purpose |
|-------|------|---------|
| `core_memories` | 1 | Long-term semantic memories with embeddings |
| `memory_links` | 0 | Directed relationships between memories |
| `bias_ledger` | 55 | Self-correction bias tracking |
| `growth_arcs` | 55 | Agent growth/energy-shift events |
| `episodic_journal` | 0 | Session summaries (middle tier) |

### Consolidation Flow
1. Short-term history exceeds `memory_limit` (4 messages)
2. Older messages are evicted and summarized by the AI
3. Summary stored in `episodic_journal` (Tier 2)
4. Background `consolidate_episodic()` promotes entries with `importance >= 6` or age > 7 days to `core_memories` (Tier 3)

---

## 5. AI Provider Architecture

### Providers

| Provider | URL | Use Case |
|----------|-----|----------|
| `Local LLM` | In-process (llama-cpp-python) | Primary inference |



### Provider Selection
- **Active provider** set via `CONFIG["active_provider"]` (default: `local_llm`)
- **Cache**: Provider cached via `_get_provider_cache()`
- **Cache**: Providers cached in `_PROVIDER_CACHE` dict (keyed by name+url+model)
- **Reset**: `reset_providers()` clears cache (useful for testing)

### Model Assignments

| Role | Default Model |
|------|--------------|
| Primary chat | `Lexi-Llama-3-8B-Uncensored_Q4_K_M` |
| Auditor | `qwen2.5-0.5b-instruct` |
| Desire engine | `qwen2.5-0.5b-instruct` |
| Sub-agents | Same as primary (configurable) |

### Vision Model Detection
- Models containing `vision`, `llava`, `gemma3`, or `gemma-4` in name are treated as vision-capable
- Multimodal images passed as base64 data-URI strings in message content

### `get_provider()` Adoption in `memory_vault.py`

**Refactored (July 2026):** Four functions in ``memory_vault.py`` were converted from
direct ``requests.post()`` HTTP calls to the project's canonical ``get_provider()``
abstraction (``from ai_base import get_provider``). All four carry ANTI-FRAGILITY
doc-comments documenting the BEFORE/AFTER refactoring and cross-linking to
``test_generate_daily_summary_ai_failure`` as the invariant lock test.

| Function | Line | Old Approach | New Approach | Notes |
|----------|------|-------------|-------------|-------|
| ``_update_tags_async`` | ~244 | ``requests.post(api_url, headers={"Authorization": f"Bearer {api_key}"}, json=payload, timeout=15)`` | ``provider.chat_completion(messages=messages, model=CONFIG.get("model_name", ""), temperature=0.1, timeout=15)`` | Async tag extraction for newly stored memories — runs in a daemon thread |
| ``extract_entities_llm`` | ~596 | ``requests.post(api_url, headers={'Authorization': f'Bearer {api_key}'}, json=payload, timeout=timeout)`` | ``provider.chat_completion(messages=messages, model=CONFIG.get('model_name', ''), temperature=0.1, max_tokens=300)`` | LLM-based entity extraction for GraphRAG — called from background threads |
| ``llm_extract_with_fallback`` | ~1710 | ``requests.post(api_url, headers={'Authorization': f'Bearer {api_key}'}, json=payload, timeout=timeout)`` | ``provider.chat_completion(messages=messages, model=fallback_model, temperature=0.1, max_tokens=300)`` | Fallback model chain for entity extraction — was already partially refactored |
| ``generate_daily_summary`` | ~2789 | ``requests.post(api_url, headers=..., json=payload, timeout=30)`` | ``provider.chat_completion(messages=messages, model=model_name, temperature=0.2, max_tokens=500)`` | Episodic journal consolidation — generates 2-3 paragraph narrative summary |

**Why:**
1. **Testability** — The test suite patches ``memory_vault.get_provider`` via
   ``@patch("memory_vault.get_provider")`` and expects ``mock_provider.return_value.chat_completion``
   to be consumed. Direct HTTP calls bypassed the mock, causing test failures.
2. **Consistency** — All AI inference in the project now goes through ``ai_base`` provider
   abstraction (supports ``LocalLLMProvider``, circuit breaker, error normalization).
3. **Offline-first** — ``get_provider()`` resolves to the in-process ``LocalLLMProvider``
   (llama-cpp-python), which never uses HTTP. The old direct calls hardcoded ``api_url``
   from CONFIG, mixing HTTP and local-inference code paths.

**Side effects cleaned up:**
- Dead ``requests.RequestException`` removed from ``except`` clauses in both
  ``_update_tags_async`` and ``extract_entities_llm`` (the provider handles its own
  errors internally and returns them via ``result.get("error")``).
- Added ``model`` and ``timeout`` parameters to ``chat_completion()`` calls where
  they were accidentally omitted in the initial pass (ensuring identical behavior
  to the old HTTP payload).

**MCP client excluded:** ``mcp_client.py`` still uses ``requests.post()`` — that's
correct. MCP (Model Context Protocol) is a JSON-RPC 2.0 standard for external tool
servers (``initialize`` / ``tools/list`` / ``tools/call`` endpoints), not AI chat
completions. Its exception hierarchy (``TimeoutError``, ``ConnectionError``,
``RuntimeError`` for JSON-RPC errors) is specific to the MCP protocol and should
not be replaced with the chat-completion provider abstraction.

**Verification:** All 51 ``test_memory_vault.py`` tests pass (50 pass, 1 skip).
``py_compile`` clean. The embedding cache test (``test_cache_evicts_oldest_entry_when_full``)
was also fixed in this session — the eviction check now reads
``CONFIG.get("embedding_cache_max", _EMBEDDING_CACHE_MAX)`` instead of the
hardcoded module-level constant, honoring the ``patch.dict(CONFIG, ...)``
test override.

---

## 6. Plugin System

### Architecture
- 28 native plugins in `plugins/` directory
- Each plugin exports: `COMMAND_NAME`, `execute(intent_json)`, `SCHEMA` (optional), `PLUGIN_METADATA` (optional)
- Registry loads all plugins at startup via `importlib`
- Plugins prefixed with `_` are skipped (private/utility modules)

### Plugin Lifecycle

| Operation | Method |
|-----------|--------|
| Load | `PluginRegistry._load_plugins()` |
| Execute | `registry.execute_command(intent_json)` |
| Enable | `registry.enable(cmd)` |
| Disable | `registry.disable(cmd)` |
| Toggle | `registry.toggle(cmd)` returns new state |
| List | `registry.list_plugins()` returns formatted string |

### Progressive Disclosure (Sprint 5)
- AI sees **category summary only** in system prompt (not full schemas)
- AI requests schemas via `<<LOAD_TOOLS:category_name>>` in response
- Requested schemas injected into next turn's context
- Controlled by `kokertechController._pending_tool_loads` set

---

## 7. Workflow Engine

### YAML Format

```yaml
name: My Workflow
description: Does something
parallel: false
steps:
  - name: step1
    action: RESEARCH_TOPIC
    params:
      query: "AI trends"
  - name: step2
    action: sub_agent
    params:
      persona: Coder
      task: "write a script"
    depends_on: [step1]
    on_failure: stop  # stop | skip | continue
```

### Action Types

| Action | Handler | Source |
|--------|---------|--------|
| `plugin_command` | `registry.execute_command()` | Any plugin |
| `sub_agent` | `SubAgent.execute()` | sub_agents.py |
| `crew` | `run_crew_team()` | crew_integration.py |
| `sandbox` | `execute_code()` | docker_sandbox.py |

---

## 8. Sub-Agent Personas

| Persona | Temperature | Max Tokens | Focus |
|---------|-------------|------------|-------|
| **Researcher** | 0.2 | 2000 | Search, analyze, synthesize |
| **Coder** | 0.1 | 3000 | Clean, tested Python code |
| **Auditor** | 0.4 | 2500 | Bug/security/performance review |
| **Planner** | 0.2 | 2000 | Task breakdown with dependencies |
| **ToolUser** | 0.3 | 2000 | Invoke plugins and system tools |
| **Orchestrator** | 0.3 | 2000 | Router: classify, delegate, delegate |
| **Synthesizer** | 0.3 | 2000 | Compile, integrate, harmonize findings |

### Intent Router (`sub_agents.router`)

`_detect_intent(text)` maps a user message to a bucket with keyword
heuristics (`code` / `research` / `audit` / `plan` / `synthesize`); then
`router(text, model=...)` runs the matching persona sub-agent and always
hands the raw output to a **Synthesizer** agent — except for the
`synthesize` bucket, which returns the Orchestrator's output directly.

**Wiring (Sprint 5.2, 2026-09-30).** The main chat path — `process_input` —
is the only orchestrator that routes, via
`KokertechController._router_dispatch_block` → `_route_intent`, which:

- returns the raw text unchanged when `CONFIG["mock_mode"]` is set. Mock
  mode must never resolve a provider, and `SubAgent.execute` calls
  `ai_base.get_provider()` — a lookup the orchestrators cannot patch.
- falls back to the raw user text on any router failure, empty output, or
  sub-agent error string (`Error:` / `Failed:`), so a dead provider can
  never break chat.
- feeds the routed text into the normal context + provider path. It does
  **not** replace `CONFIG["active_persona"]` — the persona system prompt
  is still the user's choice.

`process_input_multi` and `process_input_with_personas` deliberately do
**not** route: they compare models/personas on the *same* prompt, and routing
would shift their provider-call sequence and put different context under
comparison. Hive-bound turns also keep the raw goal (the hive returns before
the router runs). Orchestrator and Synthesizer are selectable in the UI: the
persona dropdown (`tabs/settings_tab.py`), the `/persona` CLI command, and
the A/B dropdowns (`app_ui.py`) all read `personas.json`, which seeds both
entries.

---

## 9. UI Tab System

### Tab List (24 modules)

| Tab | File | Purpose |
|-----|------|---------|
| Workspace | `tabs/workspace_tab.py` | File tree explorer |
| Neural Graph | `tabs/neural_graph_tab.py` | Knowledge graph visualization |
| Settings | `tabs/settings_tab.py` | Configuration |
| Cognitive Agency | `tabs/cognitive_agency_tab.py` | Agent autonomy |
| RLHF Trainer | `tabs/rlhf_trainer_tab.py` | Human feedback |
| Doc Pipeline | `tabs/document_pipeline_tab.py` | Batch doc processing |
| Workflow | `tabs/workflow_tab.py` | Visual workflow builder |
| Computer Use | `tabs/computer_use_tab.py` | Desktop automation |
| Health | `tabs/health_tab.py` | CPU/RAM/VRAM monitor |
| Git Tracker | `tabs/git_tracker_tab.py` | Code change viewer |
| Chat History | `tabs/chat_history_tab.py` | Session log viewer |
| RAG | `tabs/rag_tab.py` | Retrieval-augmented generation |
| Session Browser | `tabs/session_browser_tab.py` | Session navigation |
| Theme Builder | `tabs/theme_builder_tab.py` | Custom theme editor |
| Web View | `tabs/web_view_tab.py` | Embedded web view |
| Session Log | `tabs/session_log_tab.py` | Live session log |
| Memory Browser | `tabs/memory_browser_tab.py` | Long-term memory inspector |
| Scheduled Actions | `tabs/scheduled_actions_tab.py` | Cron-style task scheduling |
| Plugin Store | `tabs/plugin_store_tab.py` | Plugin marketplace |
| Persona A/B Testing | `tabs/persona_ab_tab.py` | Persona comparison tests |
| MCP Client | `tabs/mcp_client_tab.py` | External MCP server management |
| Progress | `tabs/progress_tab.py` | Real-time execution progress monitor |
| Session Journal | `tabs/session_journal_tab.py` | Searchable session-journal dashboard |
| Tool Audit | `tabs/tool_audit_tab.py` | Tool-call audit trail (schema + params) |

### Imports
```python
# Standard library first, then PyQt6, then project modules
import sys
import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QMainWindow

from config import CONFIG
from logging_config import get_logger
```

### Thread Safety
- `threading.Lock` (`self._lock`) on ALL shared resource access
- Pattern: `with self._lock:` before reading/writing `self.history`, `self.output`, vault connections
- QThread workers **must not** update UI directly — use signals
- **Worker cleanup hooks MUST connect to `worker.finished`** (a Qt-guaranteed end-of-thread signal), never to `worker.reply_signal` / `stream_signal` / `log_signal`. See [§17](#17-worker-signal-wiring-patterns) for the full rationale and anti-patterns.
- Cross-thread callers of `KeepaliveContext.done()` MUST hop the main thread with `QTimer.singleShot(0, lambda: kc.done())` because `done()` touches the QTimer + button (Qt-affinity-sensitive). See [§18](#18-keepalivecontext----context-manager-status-abort-hook).

### Error Handling
```python
try:
    ...
except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
    logger.error(f"Human-readable context: {e}")
    return []  # Graceful fallback
```

### Logging
- Use `get_logger(name="ModuleName")` from `logging_config.py`
- Methods: `logger.info()`, `logger.warning()`, `logger.error()`, `logger.debug()`, `logger.ok()` (custom)
- `logger.log_session_start()` for session boundaries
- Test logs redirected to `execution_log_TEST.txt` (via `conftest.py`)

### Naming
- `_` prefix for private methods and module-level helpers
- `UPPER_CASE` for module-level constants
- `snake_case` for functions, methods, variables
- `CamelCase` for classes
- `COMMAND_NAME` for plugin command constants

---

## 10. Coding Conventions

**Established patterns:**
- **Services:** `<Domain>Service` (e.g., `ContextService`, `PromptService`, `ProviderService`, `HistoryService`)
- **Schemas:** `<Entity>Schema` dataclass with `validate()` method (e.g., `PluginSchema`)
- **Registries:** `<Domain>Registry` with `register()` / `get()` / `reset()` (e.g., `ServiceRegistry`, `ProviderRegistry`)
- **Pools:** `<Resource>Pool` with `connection()` context manager (e.g., `SQLitePool`)

### Sprint 12 Quick Reference Index (2026-07-19)

Sprint 12 ("Structured Output & Function Calling," v0.19.0, 2026-06-23) replaced the fragile regex-based `parse_ai()` with Pydantic model validation (fail-soft) and introduced GBNF grammar-constrained structured output + OpenAI-format function calling for the local LLM provider. The `models.py` Pydantic schemas drive every downstream consumer (RAG synthesis, tool dispatch, cognitive agency). Response-format plumbing is threaded through `ai_base.py` via the `response_format` kwarg + `_validate_response_format` guard, with the canonical grammar historically declared under `data/grammars/`. (The `data/grammars/` directory is reserved for external grammars; some Sprint 12 grammars were inlined into `ai_base.py` constants.)

| Symbol | Location | Purpose |
|--------|----------|---------|
| Pydantic schemas | `models.py` line 16 (`BaseModel`, `Field`) | Typed response envelopes that replaced regex-based `parse_ai()` |
| `_validate_response_format` | `ai_base.py` line 1185 | Rejects malformed `{"type": "grammar", "value": ...}` before inference |
| `parse_ai()` | `kokertechController.py` line 1191 (primary) + `services/prompt_service.py` line 163 (refactored) | Splits raw model output into `thinking` + `final` + `command` via Pydantic |
| `response_format` kwarg | `ai_base.py` lines 43–989 | Threaded through every `chat_completion` entry point |

**When you need to ___ → go here**

| If you want to … | Read … |
|------------------|--------|
| Add a new structured-output field | `models.py` Pydantic schema + §3 Decision 8 (3-layer sentinel guards) |
| Debug a malformed `<final_output>` payload | `tests/test_structured_output.py` + `_is_sentinel_response` in `kokertechController.py` |
| Externalize an internal grammar to GBNF | `data/grammars/` directory + Sprint 12 grammar hot-reload precursor |
| Add OpenAI-format function-calling schema | `ai_base.py` `tools=` kwarg (lines 43, 81, 159, 989) + the 28 plugin schemas in `plugin_registry.py` |

**CHANGELOG anchor**: `## [0.19.0] - 2026-06-23` → `### Sprint 12: Structured Output & Function Calling` . Also references `### Sprint 12: Grammar Hot-Reload + Tool Schema Pre-Warming` .

**Drift resistance**: Cite `parse_ai`, `_validate_response_format`, and `BaseModel` by *function/class name* — these survive line drift. If `data/grammars/*.gbnf` is inlined into an `ai_base.py` constant, the table above will become stale; the contract-pinning tests are `TestStructuredOutput::test_response_format_validation` + `test_structured_output.py`'s full Pydantic-suite.

### Sprint 13 Quick Reference Index (2026-07-19)

Sprint 13 ("Hybrid Search & RAG V2," 2026-06-24) added SQLite FTS5 virtual tables for keyword search, a `hybrid_search()` Reciprocal Rank Fusion (RRF) that blends cosine-similarity vector search with BM25 keyword scores (k=60 literature constant), and the full GraphRAG pipeline (entity extraction via regex + LLM fallback, relationship storage, knowledge-graph query infrastructure). The Sprint 15 follow-up added the FTS5 `content_rowid=id` internal-content migration flag so the FTS5 index no longer duplicates memory content (avoids stale-cache drift on `_fts_sync_core_insert`).

| Symbol | Location | Purpose |
|--------|----------|---------|
| `hybrid_search()` | `memory_vault.py` line 2255 | RRF fusion of FTS5 BM25 + semantic cosine (alpha-planner-driven) |
| `_plan_hybrid_search_alpha()` | `memory_vault.py` line 740 | Per-query alpha selection (semantic vs keyword weight) |
| `extract_entities_llm()` | `memory_vault.py` line 938 | LLM-based GraphRAG entity extraction (regex fallback if disabled) |
| `fts5_search()` | `memory_vault.py` line 2114 | BM25-ranked full-text search via SQLite FTS5 |
| FTS5 virtual tables | `memory_vault.py` lines 178–189 (`content_rowid=id`) | `core_memories_fts` + `episodic_journal_fts` with internal-content migration |

**When you need to ___ → go here**

| If you want to … | Read … |
|------------------|--------|
| Tune the semantic↔keyword blend | `_plan_hybrid_search_alpha()` + `CONFIG["hybrid_search_alpha"]` (default 0.6) |
| Debug missing/low-recall results | FTS5 sync hooks (`_fts_sync_core_insert`) + `rebuild_fts_index` at `memory_vault.py` line 795 |
| Add a new GraphRAG entity type | `extract_entities_llm()` + `graphrag_entity_embeddings` CONFIG toggle |
| Migrate to a different alpha strategy | The `_plan_hybrid_search_alpha` pattern — replace upstream, signature stable |

**CHANGELOG anchor**: `### Sprint 13: Hybrid Search, GraphRAG & Knowledge Graph (2026-06-24)` . 3 dedicated test files cover the contract: `tests/test_sprint13_hybrid_search.py`, `tests/test_sprint13_ui_wiring.py`, `tests/test_sprint13_writer.py`.

**Drift resistance**: Cite `hybrid_search`, `extract_entities_llm`, and `fts5_search` by *function name* in `memory_vault.py` — these survive line drift. The `_plan_hybrid_search_alpha` signature change is the most likely drift vector; the contract is pinned by the 3 Sprint 13 test files (line counts at §2 "Document Inventory" — `tests/test_sprint13_*.py` mtime-locked to 2026-07-07 in tree).

### Sprint 14 Quick Reference Index (2026-07-19)

> **Purpose.** Sprint 14 ("Performance & Context Optimization") landed dynamic `n_ctx` (22-model table) + KV cache prefix reuse + batch embedding (128-entry LRU cache) + short-eviction skip. Round 2/3 follow-up expansion added `_search_cache` + `_search_lock` + TTL-gated cache wrap + `_EMBEDDING_INDEX` lazy-rebuild pattern. This index is the canonical "where to find memory_vault performance helpers" pointer — referenced by 7+ docs files but previously had no centralized §10 entry.

**Sprint 14 symbols + cross-references:**

| Symbol | Module | Cross-link |
|---|---|---|
| `_MODEL_CTX_TABLE` (22 models) + `_resolve_n_ctx()` dynamic scaling | `ai_base.py` | — |
| `_EMBEDDING_CACHE_MAX` (128 entries) + `batch_encode()` LRU cache | `memory_vault.py` | — |
| `_EMBEDDING_INDEX` lazy-rebuild + dirty-flag invalidation | `memory_vault.py` | [§4 Memory Architecture](#4-memory-architecture-3-tier) |
| `_search_cache` TTL-gated dict + `_search_lock` threading.Lock | `memory_vault.py` | [§4 Memory Architecture](#4-memory-architecture-3-tier) |
| `clear_search_cache()` cross-test helper | `memory_vault.py` | — |
| Short-eviction skip (`if len(text) < 200:` bypass) | `kokertechController.py` | — |

**Where to find X — common contributor questions:**

| You want to … | Read this section |
|---|---|
| Invalidate `_search_cache` programmatically | `memory_vault.py:_search_cache.clear()` + `_SEARCH_CACHE_TTL_SECONDS` (CONFIG-gated) |
| Force `_EMBEDDING_INDEX` rebuild | `memory_vault.py:store_memory()` dirty-flag invalidation (auto invalidates on write) |
| Why `n_ctx` is automatic now (no manual config) | `ai_base.py:_MODEL_CTX_TABLE` (22 models) + `_resolve_n_ctx()` per-model scaling |
| Add a new model to the `_MODEL_CTX_TABLE` | `ai_base.py` — insert entry + verify `_resolve_n_ctx()` handles the tag |
| Tune `_EMBEDDING_CACHE_MAX` | `memory_vault.py` — CONFIG-driven; sibling `_EMBEDDING_CACHE_MAX` constant |
| Reduce memory_vault `process_input()` latency | `kokertechController.py` — short-eviction skip + `_resolve_n_ctx()` auto-scaling |

**CHANGELOG anchors** (deep-history, in chronological order):

| Round | One-line summary | CHANGELOG anchor |
|---|---|---|
| R1 | `_MODEL_CTX_TABLE` 22-model dynamic-n_ctx landing | `Sprint 14 R1 Closeout` *(deferred)* |
| R2/3 | `_search_cache` + `_EMBEDDING_INDEX` expansion (`clear_search_cache()` helper) | `Sprint 14 Round 2/3` |

**Drift-resistance note.** This index references the existing [§4 Memory Architecture](#4-memory-architecture-3-tier) for `_EMBEDDING_INDEX` semantics + `_search_cache` TTL semantics; new model-table entries are stable via `_MODEL_CTX_TABLE`'s dict structure (no Sprint-index update needed for routine additions).

### Sprint 15 Quick Reference Index (2026-07-19)

> **Purpose.** Sprint 15 ("CLI Tool V2") landed the `cli/` package (`cli/commands.py` + `cli/formatting.py` + `cli/session.py`) + Rich terminal output + 50+ slash commands. The 4-iteration autouse-fixture pattern (the closed-fd debugging) is the most distinctive deliverable — a re-usable test-foundation pattern that future contributors adopting similar pytest + PyQt6 stacks should mirror. All 5 Sprint 15 ACs closed .

**Sprint 15 symbols + cross-references:**

| Symbol | Module | Cross-link |
|---|---|---|
| `cli/commands.py` (~1500 LOC, 50+ `@register("/cmd")` decorates commands) | `cli/` | [§10 Coding Conventions](#10-coding-conventions) |
| `cli/formatting.py` (Rich output — 5 theme presets + 50+ formatting helpers) | `cli/` | [§10 Coding Conventions](#10-coding-conventions) |
| `cli/session.py` (save / load / merge / search / archive / favourites) | `cli/` | [§10 Coding Conventions](#10-coding-conventions) |
| `_suppress_quit` flag (QApplication lifecycle hardening) | `conftest.py` (`_suppress_quit` + autouse cleanup fixtures) | [§12 Testing Patterns](#12-testing-patterns) |
| `StringIO` routing autouse-fixture (the closed-fd pattern) | `conftest.py` (conftest.py:_aggressive_cleanup_qt) | [§13 Common Pitfalls](#13-common-pitfalls--gotchas) |
| 4-iteration closeout narrative (the debugging story) | docs narrative | [§13 Common Pitfalls — fd exhaustion row](#13-common-pitfalls--gotchas) |

**Where to find X — common contributor questions:**

| You want to … | Read this section |
|---|---|
| Add a new slash command | `cli/commands.py` — `@register("/cmd_name")` decorator pattern (currently 50+ commands) |
| Debug fd-exhaustion hang on multi-file pytest | [§13 Common Pitfalls — fd exhaustion row](#13-common-pitfalls--gotchas) |
| Re-use the 4-iteration autouse-fixture pattern | [§12 Testing Patterns](#12-testing-patterns) — `_suppress_quit` + close-event-cleanup |
| Avoid `_quiet` short-circuit failure | [§12 Testing Patterns](#12-testing-patterns) — `_silent = True` flag on `_log_rotation` writes |
| Theme / Rich output conventions | `cli/formatting.py` — 5 theme presets (`dark`, `light`, `monokai`, `one-dark`, `dracula`) |

**CHANGELOG anchors** (deep-history, in chronological order):

| Round | One-line summary | CHANGELOG anchor |
|---|---|---|
| R1-R6 | `cli/` package landing (50+ commands, Rich output, session persistence) | `Sprint 15 R1–R6 Closeout` *(deferred)* |
| R7 | 4-iteration autouse-fixture pattern closeout (fd exhaustion FIXED) | `Sprint 15 R7 Closeout` *(deferred)* |

**Drift-resistance note.** The autouse-fixture pattern is referenced from §12 + §13 (cross-section); new slash commands add line-LOC to `cli/commands.py` without disrupting the §10 index (auto-registers via the `@register()` decorator pattern, so no Sprint-index update needed for routine additions).

### Sprint 16 Quick Reference Index (2026-07-19)

> **Purpose.** Sprint 16 ("Code Intelligence Core") landed the `CodeIntelligence` module (file ops, AST analysis, test/linter/git integration). Most Sprint 16 work was absorbed by Sprint 17's multi-round consumer-side adoption (R5/R9/R10/R11/R12); this index points at that downstream expansion for code-archaeology. The `git_diff` + `git_commit` pair was implemented 2026-08-01 (autonomous improvement session; see CHANGELOG v0.22.0).

**Sprint 16 symbols + Sprint 17 successors:**

| Sprint 16 module / symbol | Sprint 17 successor round | CHANGELOG anchor |
|---|---|---|
| `code_intelligence.py` (`CodeIntelligence` class — 11 methods: read/write/edit/search/analyze_ast/run_tests/run_linter/git_status/git_diff/git_commit/parse_error) | `git_diff` + `git_commit` IMPLEMENTED (2026-08-01, v0.22.0) — lines ~592/664; 7+ tests in `tests/test_code_intelligence.py` (unified format, renames, binary, multi-line body, destructive gate, audit entry) | `Sprint 17 R12 Closeout` + `v0.22.0` |
| `_DESTRUCTIVE_ACTIONS` set (write / edit / git ops) | Adopted live in `services/tool_use_agent.py:73` (Sprint 17 R5 cross-link) | `Sprint 17 R5 Closeout` |
| `tool_use_audit.jsonl` (audit-log as JSONL pattern) | Mirrored by `code_intelligence.py`'s write/edit/git_commit audit entries | `Sprint 17 R10 Closeout` |
| Path-traversal-validation hardening (`os.path.realpath` both sides of `commonpath`) | Sprint 17 R5-R12 closeout narrative | `Sprint 17 R5 Closeout` |

**Where to find X — common contributor questions:**

| You want to … | Read this section |
|---|---|
| Understand the `_DESTRUCTIVE_ACTIONS` gate + audit-log pattern | `services/tool_use_agent.py:73` (live) + [§17 Worker Signal Wiring](#17-worker-signal-wiring-patterns) |
| Review the `git_diff` / `git_commit` implementations (~592/664 in `services/code_intelligence.py`, 7 tests) | Sprint 17 R12 Closeout + CHANGELOG v0.22.0 |
| Adopt a workspace-aware CodeIntelligence consumer | Sprint 17 R10 + R11 closeout entries (workflow_engine dispatch + audit-log pattern) |
| Pattern for symlink-traversal hardening in user-supplied paths | CodeIntelligenceFactory `validate_path` (Sprint 17 absorbed) |

**CHANGELOG anchors** (deep-history, in chronological order):

| Round | One-line summary | CHANGELOG anchor |
|---|---|---|
| R1 (Sprint 16) | `CodeIntelligence` class landing | `Sprint 16 R1 Closeout` *(deferred)* |
| R5-Sprint17 | Singleton-reset helper + conftest wire-up | `Sprint 17 R5 Closeout` |
| R10-Sprint17 | `code_review_workflow.py` CodeIntelligenceFactory consumer adoption | `Sprint 17 R10 Closeout` |
| R11-Sprint17 | `workflow_engine.py::execute_step` adds `code_review` step-type routing | `Sprint 17 R11 Closeout` |

**Drift-resistance note.** Sprint 16 work is largely subsumed by Sprint 17's multi-round expansion; this index works as a code-archaeology entry for finding the original Sprint 16 specs. The formerly-deferred `git_diff` + `git_commit` pair is now implemented (CHANGELOG v0.22.0) — the R12 Forward-looking #2 backlog row is closed.

### Sprint 17 Quick Reference Index (2026-07-19)

> **Purpose.** Sprint 17 landed 5+ closeout rounds in §10 (R5 / R9 / R10 / R11 / R12) and added 2 production files. This index is a **durable landing page** — it points at the 3 deep-detail subsections rather than re-summarizing them — so the page stays stable as new rounds land and contributors find the right section without reading §10 end-to-end.

**Sprint 17 deep-detail subsections (in reading order):**

1. [Project-Wide Singleton Convention (Sprint 17 R12)](#project-wide-singleton-convention-sprint-17-r12) — the **5-rule convention** every future module-level singleton MUST follow, plus the new CI gate (`scripts/assert_singleton_reset_pattern.py`) that enforces it.
2. [CodeIntelligenceFactory (Sprint 17)](#codeintelligencefactory-sprint-17----closure-only-singleton-factory-pattern) — the **closure-only singleton factory pattern** + `_FACTORY_INIT_LOCK → registry._lock` lock-order contract. Reference for building any new `<Domain>Factory`-style helper.
3. [SettingsCacheService + ProviderStreamService (Sprint 19.3)](#project-wide-singleton-convention-sprint-17-r12) — the two Sprint 19.3 lazy singletons (settings TTL cache + streaming executor) extracted from `kokertechController` in v0.21.8, following the same 5-rule convention.
4. [Cross-File Isolation Invariant (the 9-round gap pattern, Sprint 17 R5+R9)](#cross-file-isolation-invariant-the-9-round-gap-pattern-sprint-17-r5r9) — the **9-round debugging narrative** + 2-sentence invariant + future-contributor checklist. WHY the `_reset_*.for_tests()` pattern is non-optional for cross-file test isolation.

**Where to find X — common contributor questions:**

| You want to … | Read this subsection (deep-link) |
|---|---|
| Add a new module-level singleton or factory | [§10 Project-Wide Singleton Convention](#project-wide-singleton-convention-sprint-17-r12) — 5 rules + adoption checklist |
| Debug a singleton leaking state across tests | [§10 Cross-File Isolation Invariant](#cross-file-isolation-invariant-the-9-round-gap-pattern-sprint-17-r5r9) — diagnostic + `_reset_X_for_tests` helper + conftest wire-up |
| Understand why a `<Domain>Factory` lazy-singleton pattern exists | [§10 CodeIntelligenceFactory](#codeintelligencefactory-sprint-17----closure-only-singleton-factory-pattern) — lock-order + closure-only pattern |
| Wire a workflow step that calls a singleton-aware consumer | [§10 CodeIntelligenceFactory](#codeintelligencefactory-sprint-17----closure-only-singleton-factory-pattern) — `register_workspace()` / `resolve()` API surface |
| Add a CI gate that enforces a coding convention | [§10 Project-Wide Singleton Convention](#project-wide-singleton-convention-sprint-17-r12) — `scripts/assert_*.py` template (mirror `assert_section11_render_clean.py`) |
| Verify CI gate coverage | `scripts/assert_singleton_reset_pattern.py` + `tests/test_assert_singleton_reset_pattern.py` (~10 tests, gate-only, fast subset) |
| Clean up / compact / diagnose the vault | [§24 Vault Maintenance Toolkit](#24-vault-maintenance-toolkit-scriptsvaultmaintenancepy-sprint-1982) — command map, safety rails, the 742,941 → 12 recovery saga |
| Investigate suspected test pollution in production data | [§25 The Vault Recovery Saga](#25-the-vault-recovery-saga-742941--12-2026-09-22) + `vault_maintenance.py --census` (24h bleed tripwire) |
| Run a search benchmark on the vault | `_scratch/bench_vector_search.py` (read-only; engine-only timings, legacy extrapolation) |

**CHANGELOG anchors** (deep-history reading, all 5 rounds, in chronological order):

| Round | One-line summary | CHANGELOG anchor |
|---|---|---|
| R5 | `_reset_factory_for_tests()` helper added to `services/code_intelligence_factory.py` | `Sprint 17 R5 Closeout` |
| R9 | `conftest._reset_services_registry()` auto-wires `_reset_factory_for_tests()` | `Sprint 17 R9 Closeout` |
| R10 | `code_review_workflow.py` consumer-side singleton adoption | `Sprint 17 R10 Closeout` |
| R11 | `workflow_engine.py::execute_step` adds `code_review` step-type routing through factory | `Sprint 17 R11 Closeout` |
| R12 | `scripts/assert_singleton_reset_pattern.py` CI gate + KNOWLEDGE.md promotion | `Sprint 17 R12 Closeout` |

**Drift-resistance note.** This index references the 3 deep-detail subsections (which contain full inline narrative) rather than duplicating them. New rounds can land without changing this page — just append a row to the **CHANGELOG anchors** table.

### PromptBuilder (Sprint 2)

`prompt_builder.py` centralizes all prompt construction. See `test_prompt_builder.py` (22 tests) for coverage.

### ServiceRegistry (Sprint 1)

`services.py` provides a minimal DI container for cross-cutting state. See `test_services.py` (26 tests) for coverage.

### Project-Wide Singleton Convention (Sprint 17 R12)

> **Convention: every future module-level singleton MUST ship with a `_reset_X_for_tests()` helper AND a conftest wire-up that auto-invokes it from `_reset_all_shared_state() -> _reset_services_registry()`.** Enforced by `scripts/assert_singleton_reset_pattern.py` (Sprint 17 R12 pre-flight CI gate, exit 0 on clean / 1 on offenders). Future singleton-bearing modules (Sprint 18's `AgentLoopFactory`, a hypothetical `CrewFactory` / `ToolProviderFactory`, etc.) get this convention by default rather than re-discovering the cross-test pollution gap from scratch (the 9-round Sprint 17 R5+R9 narrative is documented under `### Cross-File Isolation Invariant` further down in this section).

**Naming convention:** singleton variable `_X_SINGLETON: Optional[<Type>] = None` (or `_X_INSTANCE = None`) paired with the lowercased helper `_reset_x_for_tests() -> None` + init lock `_X_INIT_LOCK = threading.Lock()`. The canonical implementation lives in `services/code_intelligence_factory.py` (R1) and is the reference for future adopters.

**Three CI gate checks** (`scripts/assert_singleton_reset_pattern.py` -- pure functions mirroring the `assert_section11_render_clean.py` pattern):

- `find_singleton_without_reset_helper`: every `_X_SINGLETON` / `_X_INSTANCE` declared in `services/` or the canonical singleton homes MUST have a paired `_reset_*_for_tests()` helper definition in the SAME file.
- `find_reset_helper_without_conftest_wireup`: every `_reset_X_for_tests()` definition MUST be invoked in `conftest.py` somewhere in the `_reset_services_registry()` / `_reset_all_shared_state()` chain (the `try/except: pass` wrapper keeps the reset teardown resilient to missing-import errors).
- `find_dual_conftest_import_violations`: no test may use `from conftest import X` / `import conftest as X` -- the dual-conftest-file trap where pytest's `sys.modules` cache resolves `conftest` to `tests/conftest.py` (the WRONG file). Canonical workaround: `importlib.util.spec_from_file_location("_unique_test_only_name", <project_root> / "conftest.py")` -- see `TestConftestRegistryWireup` in `tests/test_code_intelligence_factory.py` for the canonical regression-test pattern.

**Sprint 19.3 singletons (2026-07-31):** `services/settings_cache_service.py` (`_SETTINGS_CACHE_SERVICE_SINGLETON` + `get_settings_cache_service()` + `_reset_settings_cache_service_for_tests()`) and `services/provider_stream_service.py` (`_PROVIDER_STREAM_SERVICE_SINGLETON` + `get_provider_stream_service()` + `_reset_provider_stream_service_for_tests()`) follow this exact convention — both wired into `conftest._reset_services_registry()`, both verified by the CI gate. See CHANGELOG v0.21.8 (Sprint 19.3).

**Worked adoption example** (Sprint 18+ factory adding a new singleton):

```python
# services/my_factory.py
_MY_SINGLETON: Optional["MyFactory"] = None
_MY_INIT_LOCK = threading.Lock()


def get_my_factory() -> "MyFactory":
    global _MY_SINGLETON
    with _MY_INIT_LOCK:
        if _MY_SINGLETON is None:
            _MY_SINGLETON = MyFactory()
            get_services().register_instance("my_factory", _MY_SINGLETON)
        return _MY_SINGLETON


def _reset_my_for_tests() -> None:  # auto-invoked from conftest._reset_services_registry()
    global _MY_SINGLETON
    with _MY_INIT_LOCK:
        _MY_SINGLETON = None
```

Then in `conftest.py::_reset_services_registry()`:

```python
try:
    from services.my_factory import _reset_my_for_tests
    _reset_my_for_tests()
except Exception:
    pass
```

The CI gate verifies both pairs (helper in same file + wire-up in conftest). If either is missing, the gate fails until the invariant is restored.

### CodeIntelligenceFactory (Sprint 17) -- closure-only singleton factory pattern

> **Note:** this subsection is denser than the adjacent `### PromptBuilder (Sprint 2)` / `### ServiceRegistry (Sprint 1)` one-liners because the pattern has non-obvious gotchas (lock-order inversion trap, dual-conftest-file trap, per-instance RLock choice). Sprint 18+ adopters should treat it as a deep-dive reference rather than a one-liner cross-reference. If you only need the cross-reference (file path + test file pointer), see the `### ServiceRegistry (Sprint 1)` entry above for the comparable pattern.

`CodeIntelligenceFactory` (`services/code_intelligence_factory.py`, ~220 LOC) is the canonical reference implementation for future contributors building similar workspace-aware factories that wire into `ServiceRegistry`. Sprint 18+ modules adopting this pattern (e.g., a hypothetical `AgentLoopFactory`, `ToolProviderFactory`) should mirror the four-pillar structure verbatim: module-level singleton + module-level init lock + per-instance RLock + paired reset helper.

**1. Closure-only singleton + lazy-return-from-module-cache.** Module-level state stays at `_FACTORY_SINGLETON: Optional[CodeIntelligenceFactory] = None` plus `_FACTORY_INIT_LOCK = threading.Lock()`. First `get_code_intelligence_factory()` call constructs + calls `get_services().register_instance(\"<name>\", _FACTORY_SINGLETON)` under `_FACTORY_INIT_LOCK`. **All subsequent calls return the cached module-level reference directly** — no registry round-trip, no resync risk. This pattern avoids BOTH the historical check-then-register race (two concurrent callers overwriting `_factories[name]`) AND the per-call registry-lookup cost. Apply this exact pattern to any new factory keyed on a SAM runtime-stable identity (worker pool, agent loop, cached detector).

**2. Per-instance `threading.RLock()` (NOT plain `Lock`).** Use `self._lock = threading.RLock()` on the factory INSTANCE so re-entrant acquisition works if a future method calls back into another method (e.g., an `auto_register_on_resolve()` that calls `register()` from inside `resolve()`). Held across the entire body of `register()` / `resolve()` / `reset()`. Construction-cost is the deciding factor for whether to hold the lock across the body: `CodeIntelligence.__init__` is microseconds-fast (just `os.path.realpath` + JSON cache priming -- no model I/O), so holding `_lock` across it is acceptable. If a future factory's `resolve()` had to do multi-second work (e.g., load a model), switch to a release-during-construction pattern instead.

**3. Lock-order contract: `_FACTORY_INIT_LOCK → registry._lock`.** Canonical outermost-to-innermost acquisition order. Enforced by the helper's body (which calls `get_services().register_instance()` while inside the `_FACTORY_INIT_LOCK` critical section -- that internally acquires `registry._lock`). **DO NOT invert this order.** A future code path that acquires `registry._lock` BEFORE `_FACTORY_INIT_LOCK` would deadlock against conftest's `_reset_service_registry()` block (acquires `registry._lock` while concurrent helper code waits for `_FACTORY_INIT_LOCK`). Pair-reset path (`_reset_services_registry()` -> `_reset_factory_for_tests()`) is documented in the file's `LOCK-ORDER CONTRACT` doc-block; preserve that invariant verbatim.

**4. `_reset_X_for_tests()` helper + conftest wire-up (cross-test pollution guard).** Pattern is `register_instance`-SPECIFIC (because of the module-level cache this factory ships, NOT the `register_instance` method itself — see below). Two paths through `ServiceRegistry` (verified against `services/registry.py::reset()` lines 99-112 — default `clear_factories=False` clears `_services` but preserves `_factories`):

- **`register_instance(name, obj)`** -- what `CodeIntelligenceFactory` uses. The eagerly-constructed instance lives directly in `registry._services[name]`. After `get_services().reset()`, `_services` is cleared but the module-level cache (e.g., `_FACTORY_SINGLETON`) stays populated, creating the divergence this helper closes. **MUST ship with `_reset_X_for_tests()` + conftest wire-up** to wipe the module-level state under the init lock and keep both surfaces in sync across `pytest_runtest_teardown`.
- **`register(name, factory_fn, singleton=True)`** -- the canonical pattern. The factory_fn itself is preserved in `_factories` across `reset(False)`; the cached instance is rebuilt lazily on the next `get()`. **Does NOT need `_reset_X_for_tests()`** -- the lazy-rebuild semantics handle cross-file cleanup automatically.

So the conditional contract is: **future singletons that use `register_instance()` MUST ship with `_reset_X_for_tests()` + conftest wire-up; future singletons that use `register(name, factory_fn, singleton=True)` get auto-rebuild for free.** Conftest.py's `_reset_services_registry()` should auto-invoke the helper after `get_services().reset()` only for the `register_instance()`-flavored singletons. **The dual-conftest-file layout trap:** NEVER load the conftest via `from conftest import X` from a test in this project -- the project has BOTH `C:\\KokertechAI\\conftest.py` (with the reset helpers) AND `tests/conftest.py` (a sys.path-setup-only file). pytest's sys.modules cache for the `conftest` slot is occupied by `tests/conftest.py`, so `import conftest` resolves to the wrong file. If a regression test needs to verify the wire-up behaviorally, use `importlib.util.spec_from_file_location(\"_unique_test_only_name\", <project_root> / \"conftest.py\")` to load by absolute path under a unique module name -- this bypasses the cache collision cleanly (see `TestConftestRegistryWireup::test_conftest_reset_services_registry_wireup_clears_factory_singleton` for the canonical pattern).

**Why this design over alternatives:** The closure-only + register-instance pattern (~10 LOC + a doc-block) was chosen over a "lazy-closure + register-closure" pattern (which would require a `if not has() then register()` race-free guard that historically was hard to maintain). The per-instance RLock + module-level init-lock layered structure is the simplest design that achieves both:
- **No double-construction** (init lock + cached module-level reference).
- **No check-then-register race** (eager-construct-then-register_instance, never check-then-register).
- **Cross-file pollution guard** (paired reset helper automatically wired into conftest).

Reference implementation: `services/code_intelligence_factory.py`. Test contracts: `tests/test_code_intelligence_factory.py` (5 tests: register-returns-instance, caches-workspace, rejects-invalid-workspace, lazy-instantiation-spies-on-__init__, conftest-reset-services-registry-wireup-clears-factory-singleton).

### Cross-File Isolation Invariant (the 9-round gap pattern, Sprint 17 R5+R9)

> **Why this subsection exists separately:** the gap pattern took 9 iterations (Sprint 17 R1 → R2 → R3 → R5 → R9 → R10 → R11) to close. Future contributors adding module-level singletons or factory helpers MUST read this BEFORE adopting the pattern.

**The gap:** any module-level singleton cached outside the registry (`_FACTORY_SINGLETON: Optional[X] = None`, `_LAZY_INSTANCE`, etc.) survives `get_services().reset()` because `reset(clear_factories=False)` only clears `registry._services[name]` -- the module-level reference stays populated. Without a paired `_reset_X_for_tests()` helper that wipes the module-level state AND a conftest wire-up that auto-invokes it, prior tests leak registered workspaces / instances into subsequent tests. These bugs only surface in multi-file pytest runs (NOT in single-file isolation), making them notoriously hard to diagnose.

**The 2-part solution (always ship together):**

1. **`_reset_X_for_tests()` helper** in the factory module itself (`services/code_intelligence_factory.py:191`). Must be locked under the factory's init lock to serialize against concurrent helper invocations. Sets the module-level reference to `None` without touching the registry. NB: callers MUST follow up with a `get_X_factory()` call to refresh the registry entry -- `reset_X_for_tests` alone leaves the registry holding a stale reference.

2. **Conftest auto-wire-up** in `conftest.py::_reset_services_registry()` (Sprint 17 R9, paired with the registry reset). Pattern:
   ```python
   try:
       from services.code_intelligence_factory import _reset_factory_for_tests
       _reset_factory_for_tests()
   except Exception:
       pass
   ```
   The `try/except: pass` is essential -- the reset helper is a test-only convenience, NOT a production invariant, so a missing import or runtime error must not break the rest of the teardown block.

**The dual-conftest-file layout trap** (re-stated from Pillar 4 for emphasis): the project has BOTH `C:\\KokertechAI\\conftest.py` (with the reset helpers) AND `tests/conftest.py` (sys.path-setup-only). pytest's `sys.modules` cache occupies the `conftest` slot with `tests/conftest.py`, so `from conftest import X` resolves to the wrong file in tests. The canonical workaround is `importlib.util.spec_from_file_location("_unique_test_only_name", <project_root> / "conftest.py")` -- see `TestConftestRegistryWireup::test_conftest_reset_services_registry_wireup_clears_factory_singleton` in `tests/test_code_intelligence_factory.py`.

**Future-contributor checklist** (apply when adding any new module-level singleton or factory helper):

- [ ] Singleton keyed on a runtime-stable identity (workspace path, model name, agent loop name) -- NOT per-request state.
- [ ] Helper `_reset_X_for_tests()` exists + is locked under the init lock (serializes against concurrent helper invocations).
- [ ] Conftest's `_reset_services_registry()` auto-invokes the helper (paired reset, `try/except: pass` for teardown resilience).
- [ ] `tests/test_X_factory.py` has a `TestConftestRegistryWireup` class with at least 1 behavioral test verifying the wire-up end-to-end.
- [ ] KNOWLEDGE.md §10 entry updated (this subsection + the `CodeIntelligenceFactory (Sprint 17)` cross-link above).
- [ ] CHANGELOG closeout entry appended with the gap-pattern narrative so future readers see the history.

**Deferred follow-ups that surface this pattern in practice:**

- **R10** (`code_review_workflow.py`): first consumer of the factory. Tests in `tests/test_code_review_workflow.py` call `_reset_factory_for_tests()` in `setUp/tearDown` -- defense-in-depth even with conftest's auto-wire, because the workflow module mutates the registration cache directly.
- **R11** (`workflow_engine.py` action="code_review" dispatch): workflow tests in `tests/test_workflow_engine.py::TestExecuteStepCodeReview` similarly call `_reset_factory_for_tests()` in `setUp/tearDown` for the same reason.

---

## 11. Configuration System

### File Locations

| File | Path |
|------|------|
| Main settings | `C:\KokertechAI\app_settings.json` |
| User identity | `C:\KokertechAI\user_identity.json` |
| Personas | `C:\KokertechAI\personas.json` |
| Database | `C:\KokertechAI\kokertech_vault.db` |
| ENV file | `C:\KokertechAI\.env` |
| Log file | `C:\KokertechAI\execution_log.txt` |
| Backups | `C:\KokertechAI\settings_backups\` |
| Save marker | `C:\KokertechAI\data\logs\.save_complete` |

### Key CONFIG Flags

| Key | Default | Purpose |
|-----|---------|---------|
| `active_provider` | `local_llm` | LM Studio / Ollama / OpenRouter selector |
| `keepalive_max_ticks` | `4` | Stale-tick cap before auto-abort with breadcrumb. Tunable via `start_keepalive(max_ticks=N)`. See [§18](#18-keepalivecontext----context-manager-status-abort-hook). |
| `chat_history_enabled` | `True` | Persistence on/off (Settings tab). When False, `_save_chat_history()` / `_load_chat_history()` / `_schedule_chat_history_save()` all silently no-op. |
| `chat_history_debounce_ms` | `500` | Coalesce window for chat-history writes (range 0-60000). |
| `notifications_enabled` | `True` | Master kill-switch for all desktop toast notifications. When False, `notify()` / `notify_task_complete()` / `notify_error()` all silently no-op. Toggleable from both PyQt6 Settings tab and Flask web UI (`localhost:5050`). |
| `tool_use_per_tool_timeout` | `30` | Per-tool execution timeout (seconds). |
| `circuit_breaker_max_fails` | `5` | AI provider failure threshold before circuit-breaker opens. |

### Secret Loading Priority
1. Windows Credential Manager (keyring)
2. Environment variable (`KOKERTECH_API_KEY`)
3. Legacy plaintext in JSON (auto-migrated on first load)

### Auto-Backup
- Settings + identity snapshotted to `settings_backups/` before each save
- Max 20 backup pairs retained
- `export_backup()` creates .zip; `import_backup()` restores from .zip
- `check_last_save_integrity()` validates files on startup, offers restore from backup if corrupt

---

## 12. Testing Patterns

### Test Structure
- 153 test files in `tests/` (0 at root); grew from 70 baseline via July 2026 sprint additions; 4,200+ passing, **zero pre-existing failures** — all 51 cross-file isolation failures resolved, all per-file test bugs fixed
- `conftest.py` provides shared fixtures + log isolation + QApplication lifecycle
- **Cross-file QApp state issue: RESOLVED** via Phase 4 widget cleanup — Qt-dependent tests can now be run together without crashes
- Full-suite: 4,200+ passing, **zero pre-existing failures** — all 51 cross-file isolation failures resolved, all per-file test bugs fixed

### QApplication Lifecycle (pytest-qt)

| Fixture | Scope | Location | Purpose |
|---------|-------|----------|---------|
| `qapp` | `session` | `conftest.py` | Overrides pytest-qt's function-scoped default. Single QApplication created at session start, reused by all tests |
| `_ensure_qapp` | `session`(autouse) | `conftest.py` | Ensures QApplication is initialized even for `unittest.TestCase`-based test files that don't explicitly request fixtures |
| `shared_dashboard` | `module` | `conftest.py` | Module-scoped KokertechDashboard. First test in each file pays ~15s creation; subsequent tests reuse with state reset. Saves **26x runtime** (48 tests in 20.81s vs 542s+) |

### Cross-File Isolation

`conftest.py` resets shared module-level state between test files via `pytest_runtest_teardown`:

1. **`patch.stopall()`** — Stops all active mocks (handles setUpClass patches from prior files)
2. **`_aggressive_cleanup_qt()`** — Destroys all top-level widgets via `deleteLater()` (avoids triggering `closeEvent` handlers which call `app.quit()` and set the `closingDown` flag), then flushes the Qt event queue (20× `processEvents` + `sendPostedEvents()`). No `app.quit()` call — the session-scoped QApp is kept alive for subsequent test files.
3. **`_reset_plugin_registry()`** — Recreates PluginRegistry singleton
4. **`_reset_memory_vault()`** — Uses `importlib.reload()` to atomically restore real functions (not just MagicMock attributes)
5. **`_reset_ai_cache()`** — Clears `ai_base._PROVIDER_CACHE`
6. **`_reset_mcp_state()`** — Clears `mcp_server._http_server*` state |
7. **`_reset_services_registry()` repairs canonical wiring** — After `get_services().reset()`, `conftest.py::_reset_services_registry()` now re-invokes `services.registry.register_canonical_services()` (idempotent). A destructive `reset(clear_factories=True)` from any test file (e.g. `test_services.py::TestGlobalGetServices`) wipes the 4 canonical service factories (`history_service` / `context_service` / `prompt_service` / `provider_service`) from the GLOBAL registry; without the repair, every later `KokertechController()` in the same pytest process raises `KeyError: service 'history_service' is not registered` (exposed 2026-08-01 by `tests/test_reject_with_error.py` — 7 tests green in isolation, all 7 red after `test_services.py`). Ordering contract: repair MUST run after `reset()` because `ServiceRegistry.register()` only pops stale cached instances for non-singletons. Pinned by REGRESSION GUARD `TestGlobalGetServices::test_register_canonical_services_restores_after_full_wipe`. |
8. **`shared_dashboard` fixture patches 4 tab-creation side-effects (4 sibling MagicMocks)** — The module-scoped fixture (used by `test_app_plugins.py` / `test_hotkey_controller_plugin_integration.py` / `test_hotkey_system.py` / `test_kokertech_main_dashboard.py`) patches four slow/hanging tab-creation side-effects so Dashboard construction stays fast under pytest: `_init_safety` (computer_use_tab — slow pyautogui→pyscreeze→PIL init), `_update_health` (health_tab — nvidia-smi/GPUtil VRAM queries), `refresh_agency_data` (cognitive_agency_tab — SQLite lock contention), and `render_knowledge_graph` (neural_graph_tab — **added 2026-08-01 as the missing sibling**). The last one was the root cause of the `test_hotkey_controller_plugin_integration.py` fixture-phase hang in cross-file batches: `create_neural_graph_tab()` calls `render_knowledge_graph()` at end of tab creation, which spawns `ui_components.GraphLayoutWorker` (a QThread that opens a REAL sqlite3 connection to `kokertech_vault.db` and, via `layout_ready_signal → _on_layout_ready`, paints `NodeGraphicsItem`/`EdgeItem` scenes). The signal fires inside `app.processEvents()` during fixture setup/teardown, hanging the run. Verified: 23/23 in 39s isolated (was: hang), and the file passed within chunk-2's 1044 cross-file. **Caveat (the `test_prewarm_badge_transitions` lesson, v0.22.3):** fixture-level MagicMock patches shadow the real method for the whole test FILE — a test that must exercise the real method has to re-bind it on the instance (see the v0.22.3 fix). The 4 patch-target magic strings are pinned by REGRESSION GUARD `tests/test_shared_dashboard_fixture_pins.py::test_fixture_patch_targets_exist` — a method rename now fires at collection time instead of silently no-oping the patch (add a 5th target to that test when extending the fixture). |

### `_suppress_quit` Flag Pattern

`KokertechDashboard.closeEvent()` normally calls `QApplication.instance().quit()` at the end, which sets the internal `closingDown` flag on the session-scoped QApplication. Once `closingDown` is `True`, Qt's `QWidget::create()` silently returns early, leaving widgets with no native window handle — causing hangs when the next test file's `shared_dashboard` fixture creates a new `KokertechDashboard`.

**`_suppress_quit` flag pattern** (used in `shared_dashboard` fixture teardown in `conftest.py`):
```python
# In shared_dashboard fixture teardown:
window._suppress_quit = True  # Tells closeEvent to skip QApp.quit()
window.close()                 # Triggers full closeEvent cleanup (timers, threads)
window.deleteLater()           # Schedules deferred C++ widget destruction
# Process events to flush deferred deletion
app = QApplication.instance()
if app:
    for _ in range(5):
        app.processEvents()
```

How it works:
1. `_suppress_quit = True` is set on the Dashboard instance before `close()` is called
2. `close()` triggers `closeEvent` which runs through all cleanup (timers stopped, threads terminated, MCP shutdown, consolidation, session end)
3. At the end of `closeEvent`, the guard `if not getattr(self, '_suppress_quit', False):` prevents `QApplication.instance().quit()` from being called — so the `closingDown` flag stays `False`
4. `deleteLater()` properly disposes of C++ widget objects

### Shutdown Safety Pattern

To prevent signal handlers from crashing during widget destruction, all Qt-connected signal handlers use the `_shutting_down` guard:

```python
class SomeMixin:
    def closeEvent(self, event):
        self._shutting_down = True  # ← VERY FIRST line
        # ... cleanup logic
```

Applied in: `app_lifecycle.py::closeEvent`, `app_core.py::_check_save_integrity`, `tabs/settings_tab.py::_validate_protocol` + `_on_protocol_text_changed` + `_deferred_init_settings_tab`.

### Coverage Gaps (Documented)

The following code areas have **no test coverage** as of July 2026. These are
documented here so they can be addressed in future sprint work:

| Area | Files | Est. Lines | Notes |
|------|-------|-----------|-------|
| **Tab modules** | 0/24 ``tabs/*.py`` files | — | All 24 tab modules have dedicated test files (e.g. `test_workspace_tab.py`, `test_neural_graph.py`, `test_progress_tab.py` — 46 tests). See §12 Key Test Files. |
| **Plugins** | 28 ``plugins/*.py`` files | ~1,100 | `tests/test_plugin_execute.py` (104 tests) covers all 28 plugins' `execute()` — exports, error paths, path-traversal security, mocked success. See §12 Key Test Files. |
| **Services** | ``services/`` directory | ~2,900 | Per-suite enumeration relocated to §12 "Coverage-Gap Service Suite Detail" below (16 service modules). |

These gaps are **known and accepted** — the project prioritizes core infrastructure
(config, AI providers, memory vault, hotkey system) over UI and plugin testing.
New test files should follow the ``tests/`` subdirectory convention.

#### Coverage-Gap Service Suite Detail (relocated)

> Verbatim detail relocated from the tables above (wide-cell limit). Nothing was removed.

- **Row:** **Pre-flight gates**
- *Original detail:* 11 script-based gates run in one pass via `scripts/run_all_gates.py` (`--fast` = sub-2s subset): FREE-BUFF-SECTIONS (1), SECTION-11 (2), TEST-COUNTS (3), KNOWN-ANCHORS (4), SINGLETON-RESET (6), LINT-DRIFT (7), DOC-DRIFT (8), SILENT-CATCH (9), NO-CORRUPTION, CONTINUITY, CONFIG-MODE-PINS; `pytest` (5) runs separately via `scripts/run_parallel_chunked.py`. **Gate 7 (LINT-DRIFT, Sprint 19.8, 2026-09-21):** `scripts/assert_no_new_lint.py` runs `ruff` (ruff.toml: F/E722/B/S/DTZ/BLE001) and fails only on violations NOT present in `scripts/lint_baseline.json` (1258 baselined; re-baseline with `--rebaseline`). Per-gate detail in §12.

- **Row:** **Tab modules**
- *Original detail:* 0/24 ``tabs/*.py`` files | — | All 24 tab modules have dedicated test files (``tests/test_workspace_tab.py``, ``tests/test_neural_graph.py``, ``tests/test_progress_tab.py`` with 46 tests, ``tests/test_tool_audit_tab.py`` with 15 tests, etc.). See §12 Key Test Files for the full list.

- **Row:** **Plugins**
- *Original detail:* 24 ``plugins/*.py`` files | ~1,100 | ``tests/test_plugin_execute.py`` (104 tests) covers all 28 plugins' ``execute()`` functions — module exports, error paths, path-traversal security, and mocked success paths. ``test_plugin_registry.py`` tests registry lifecycle. Added July 2026.

- **Row:** **Services**
- *Original detail:* ``services/`` directory | ~2,900 | **Covered as of 2026-08-01 (v0.22.0).** Dedicated suites exist for all 12 service modules: ``test_services.py`` (registry, 26), ``test_code_intelligence.py`` + ``test_code_intelligence_factory.py``, ``test_tool_use_agent.py`` + ``test_tool_use_integration.py``, ``test_structured_output.py`` + ``test_structured_output_service.py`` (NEW v0.22.0), ``test_prompt_builder.py`` + ``test_sprint12_20.py``, ``test_settings_cache_service.py`` (21), ``test_provider_stream_service.py`` (9), ``test_sprint15_provider.py`` (provider_service), ``test_context_service.py`` (NEW v0.22.0, 30), ``test_kokertechController.py`` (skill_service, 191).

### Key Test Files

| File | Tests | Notes |
|------|-------|-------|
| `test_config.py` | 141 | **100% coverage** on config.py |
| `test_memory_vault.py` | 51 | Memory vault, embeddings cache, cross-encoder fallback, requests import guard, daily summary — all pass (50 pass, 1 skip) |
| `test_hotkey_system.py` | 48 | Hotkey + wake word — hang fixed 2026-07-02, all 48 pass in 21s |
| `test_hotkey_controller_plugin_integration.py` | 23 | System integration. Uses `shared_dashboard` fixture |
| `test_app_mixins.py` | 87 | UI mixin tests |
| `test_settings_tab.py` | 53 | Settings 

### Known-Failure Marker Infrastructure

The project includes a built-in mechanism for documenting and suppressing pre-existing test failures so they don't mask new regressions:

**Marker:** `@pytest.mark.known_failure`

Apply this marker to a test class or individual test that has a documented, pre-existing failure. The marker itself doesn't affect test execution — it's purely for identification.

**Suppression flag:** `--ignore-known-failures`

When passed to pytest, this flag causes all tests marked with `known_failure` to be **skipped** (visible as `s` in output, with a reason explaining the marker). This lets CI distinguish "I broke something new" from "these N tests were already failing before my change."

**Usage:**
```bash
# All tests run; known-failure tests will FAIL if they're still broken
pytest

# Known-failure tests are SKIPPED; only unexpected failures surface
pytest --ignore-known-failures

# List all tests currently marked as known_failure
pytest -m known_failure --collect-only
```

**When to use:**
1. A feature is intentionally deferred but its tests exist — add the marker so the suite stays green
2. A cross-file isolation issue can't be reproduced in isolation — document it with the marker
3. An upstream dependency (Python, Qt, etc.) causes failures — mark them until upstream fixes land

**Cleanup:** Once the underlying issue is resolved, remove the `@pytest.mark.known_failure` decorator and verify all tests pass. See `KNOWN_FAILURES.md` for the full resolution log.

**Implementation:**
- Marker registered in `pytest.ini` (`markers =` block)
- CLI option registered and deselect logic in `conftest.py::pytest_addoption` / `pytest_collection_modifyitems`
- `--ignore-known-failures` also threaded through `_scratch/run_tests_batched.py` and `_scratch/run_test_suite.py`

### CONFIG-Reset Opt-Out Marker: `@pytest.mark.no_config_reset`

The **`_reset_config_per_test`** autouse fixture (see [§12 autouse fixture #6](#12-testing-patterns)) snapshots both ``CONFIG`` and ``IDENTITY_CONFIG`` before every test and restores them after.  To opt out of this behavior for a specific test class or individual test, apply the ``@pytest.mark.no_config_reset`` marker.

**When to use:**
1. A test class uses `setUpClass` to set CONFIG values that must persist across all test methods
2. A test measures cumulative CONFIG mutations across multiple operations
3. A test intentionally modifies CONFIG and needs the next sibling test to see those changes

**Usage:**
```python
import pytest

# Class-level: all tests in this class skip the CONFIG reset
@pytest.mark.no_config_reset
class TestMyClass:
    def test_something(self):
        from config import CONFIG
        CONFIG["my_key"] = "value"  # persists to next test

# Method-level: only this test skips the CONFIG reset
class TestOtherClass:
    @pytest.mark.no_config_reset
    def test_this_one_keeps_config(self):
        ...
```

**Usage (module-level):**
```python
# At the top of a test file — all tests in the file skip the CONFIG reset
pytestmark = pytest.mark.no_config_reset
```

**Implementation:**
- Marker registered in `pytest.ini` (`markers =` block)
- Checked at runtime by `_reset_config_per_test` fixture via `request.node.get_closest_marker("no_config_reset")`
- `get_closest_marker` walks the test hierarchy (function → class → module), so class-level and module-level `pytestmark` application both work

### Test Doc-Comment Pattern: ANTI-FRAGILITY + REGRESSION GUARD

The 13 tests in `tests/test_log_rotation.py` (added 2026-07-10) anchor a **two-tier doc-comment pattern** for flagging test setups that depend on invariants the source code may silently drift away from Sprint-over-Sprint. Every test carries **exactly one** of:

| Tag | Purpose | When to use |
|---|---|---|
| `ANTI-FRAGILITY:` (top of docstring) | Documents the invariant the test IMPLICITLY relies on. The BEFORE/AFTER narrative points future reviewers at WHY the setup looks the way it does. | Behavioral tests whose setup pre-fills bytes, mocks dependencies, or threads state to exercise a specific code path. Heavy setup. |
| `REGRESSION GUARD:` (top of docstring) | Locks down a specific invariant with a black-box end-state assertion. Catches refactor reverts even when the test author has forgotten why an invariant mattered. | Dedicated boundary tests with multi-step mocks, math anchors, and "if reverted → assertion fires" failure messages. Each warrants its own test. |

The two tiers are complementary — ANTI-FRAGILITY follows the "what changed and why" model, while REGRESSION GUARD follows the "what breaks if the invariant breaks" model. Most rotation tests are ANTI-FRAGILITY (heavy setup, normal assertions). A small subset are REGRESSION GUARD when the invariant warrants dedicated black-box coverage (UTF-8 byte projection, PermissionError retry, `_silent` short-circuit, rotate-before-write semantics).

#### ANTI-FRAGILITY template

```python
def test_<name>(self):
    """ANTI-FRAGILITY: <one-line invariant summary>.

    BEFORE: <what the test looked like under the OLD invariant>.
    AFTER: <what it looks like under the NEW invariant; reference
           function_name() in filename.py — drift-resistant
           function-name anchor>.

    <Optional one-line cross-link to stricter REGRESSION GUARD
     sibling by test name.>
    """
```

**Anchoring rules:**

- **Function-name anchors** (`_write_entry`, `_rotate_if_needed`) as the primary citation — drift-resistant across edits.
- **Tilde-prefixed line numbers** (e.g., `line ~65`) flag approximation; they supplement, never replace, function-name anchors.
- **Cross-link stricter REGRESSION GUARD siblings by test name** as one trailing line — do NOT duplicate their math or assertions.

**Length budget:** 6–14 lines including the `ANTI-FRAGILITY:` tag. Over 14 lines = trim; the body of the test should carry the precise math.

#### REGRESSION GUARD template

```python
def test_<invariant>_regression(self):
    """REGRESSION GUARD for <invariant summary> in
    ``<module>.<Class>.<method>`` (lines N-M of ``<typename>``).

    <One paragraph: what the invariant guarantees. Cite the
     mathematical or semantic contract the source upholds.>

    <Optional construction-tuning paragraph: IF the test depends
     on a specific pre-fill + payload math, walk through it here
     — bytes > chars OR sleeps >= N OR .1 present/absent —
     whatever disambiguates the regression from its siblings.>

    The `<first failure assertion>` fires if a refactor reverts
    <the invariant>: <one-sentence diagnosis pointing at the
    source location>.
    """
```

**Anchoring rules:**

- Cite the **specific function** (`_write_entry`, `_rotate_if_needed`) — drift-resistant.
- Cite the **specific line range** without `~` (e.g., `line 92`, `lines 142-167`). These tests intentionally anchor to source locations the comment will point at.
- **Always end assertions with a diagnostic message** (`self.assertTrue(..., "REGRESSION: ...")`) so the failure output guides a future contributor to the source.

**Length budget:** 10–25 lines including the `REGRESSION GUARD:` tag; longer is OK if the construction-tuning paragraph is required to disambiguate math from sibling regressions.

#### Cross-link: ANTI-FRAGILITY → REGRESSION GUARD

Each ANTI-FRAGILITY doc-comment ends with one trailing line that cross-links to the stricter REGRESSION GUARD sibling by test name. Real example from `tests/test_log_rotation.py::test_silent_short_circuits_rotation`:

```
Stricter regression guard
``test_silent_short_circuits_rotation_and_write_regression``
fires four write-paths back-to-back for partial-guard detection.
```

REGRESSION GUARD siblings do **NOT** cross-link back — they own the invariant; ANTI-FRAGILITY tests document the dependency. Skip this pattern for trivial smoke tests whose body is shorter than any 5-line explanation — the math is already in the body code.

#### Worked example: `tests/test_log_rotation.py`

| Count | Tag | Notes |
|---|---|---|
| 9 | ANTI-FRAGILITY | BEFORE/AFTER + cross-link to stricter guards |
| 4 | REGRESSION GUARD | math anchor + construction tuning + diagnostic failure messages |
| 0 | Un-annotated | every test carries exactly one of the two tags |

The tags are documentation-only (no runtime cost) but drastically improve future-refactor survivability. All 13 tests pass in ~5s.

**When adding a new invariant**: write the REGRESSION GUARD first if a dedicated black-box test is warranted, then add an ANTI-FRAGILITY doc-comment + cross-link by test name to every behavioral test whose setup depends on the new invariant. **When refactoring** an area with existing ANTI-FRAGILITY comments: read the BEFORE/AFTER block before simplifying setup; trust the REGRESSION GUARD sibling to catch any actual invariant drift via its end-state assertion.

### Fixed Pre-Existing Failures (2026-07-08)

The following pre-existing test failures were fixed. All files now pass or have documented exclusions:

| File | Tests Fixed | Root Cause | Fix |
|------|------------|-----------|-----|
| `test_discovery.py` | 3 (`TestDiscoveryWiring`) | `CONFIG["discovery_probe_on_startup"]` defaults to `False` — `setup_discovery_probe()` checked this gate and returned early without creating a thread | Added `"discovery_probe_on_startup": True` to 3 `patch.dict(config.CONFIG, ...)` calls |
| `test_runtime_warning_gate.py` | 1 (`test_pytest_ini_carries_strict_runtime_warning_flag`) | `pathlib.Path(__file__).parent` resolved to `tests/` instead of project root — `pytest.ini` is at the project root | Changed `.parent` → `.parent.parent` |
| `test_vault_upgrade.py` | 1 (AST lint check) | Same path bug: `PROJECT_ROOT = pathlib.Path(__file__).resolve().parent` resolved to `tests/` → `vault_upgrade.py` not found | Changed `parent` → `parent.parent` |
| `test_ignore_known_failures_chain.py` | 7 (subprocess chain tests, 2026-07-08) → post-Sprint 21: 9 un-marked + 1 canary (runs by default and asserts `True`; skipped via `--ignore-known-failures`) | Child pytest process hangs on Windows/Python 3.14 when spawned via `subprocess.run` (systemic env issue); 2 non-subprocess tests unaffected | Enhanced `_run()` with `close_fds=True`, `stdin=DEVNULL`, `CREATE_NO_WINDOW` (2026-07-08). Sprint 21 unwound the markers — see `KNOWN_FAILURES.md` Resolution Log row dated 2026-07-21. |
| `test_hotkey_controller_plugin_integration.py` | 3 | (1) Irrelevant `@patch("requests.post")` blocked `parse_ai`; (2) AIWorker thread loaded real model → 25s+ hang; (3) Stale worker skipped `last_user_text` assignment | (1) Mock `chat_completion` directly; (2) Patch `process_input` for instant return; (3) Nullify stale worker + reset `_ab_test_running` in `_setup` |

**Verification:** All 5 files pass cleanly. `test_discovery.py` 29/29, `test_runtime_warning_gate.py` 3/3, `test_vault_upgrade.py` 8/8, `test_ignore_known_failures_chain.py` 2/2 pass + 7/7 skipped under `--ignore-known-failures`, `test_hotkey_controller_plugin_integration.py` 23/23.

---


#### `test_hotkey_controller_plugin_integration.py` — detailed root causes and fixes

The 3 failing tests (referenced in the compacted row above):

1. **`test_freeform_mode_active_uses_freeform_parsing`** +
   **`test_structured_mode_active_uses_xml_parsing`**:
   `@patch("requests.post")` was irrelevant — local LLM provider
   doesn't use HTTP. The MagicMock provider caused
   `cached_chat_completion` to return a truthy error, early-returning
   before `parse_ai`.
   **Fix:** Removed `@patch("requests.post")`, added
   `patch.object(ctrl.provider, 'chat_completion', return_value=valid_result)`.

2. **AIWorker background thread**:
   AIWorker called real `LocalLLMProvider` which tried to load a model
   → 25s+ hang.
   **Fix:** Patched `self.window.controller.process_input` in both
   dashboard tests to return instantly with mock data; patch covers
   entire test method to avoid QThread async race.

3. **`test_action_send_prompt_with_mock_worker_triggers_plugin_dispatch`**:
   Stale worker from prior test triggered `action_send_prompt()` guard
   (`worker.isRunning()`) → skipped setting `last_user_text`.
   **Fix:** Added stale worker nullification
   (`self.window.worker = None`) and `_ab_test_running = False` reset
   to `_setup` fixture.

### Pre-flight gates (run_tests.bat) -- drift-immune invariants

`run_tests.bat` runs pre-flight gates BEFORE pytest to catch doc / markup regressions, test-count drift, and memory-ceiling contract violations. All gates mirror the `assert_known_anchors.py` pattern, extended to a three-way exit contract: **0** = clean, **1** = offenders, **3** = `SKIP` — the gate's only input is an internal working-set doc that is absent (e.g. a fresh clone of the public repo). Skip-if-absent gates: `assert_section11_render_clean.py` + `assert_freebuff_sections_render_clean.py` (`freebuff_instructions.md`), `assert_no_corrupted_files.py` (check-2 quarantine baseline from `recovery_manifest.md`; the check-1 root-`.py` null-byte scan always runs), and `assert_continuity_file_consistency.py` (`continuace_freebuff.md`). `scripts/run_all_gates.py` renders these as `[SKIP]` and adds `skipped_count` to `--json`.

**`assert_section11_render_clean.py`** — enforces 3 invariants on §11 of `freebuff_instructions.md`: (1) every pitfall-table row has pipe-count == 4, (2) no blank line splits footnote blockquotes, (3) every `\letter` token is wrapped in backtick code span. Exposes 4 pure-function finders (`find_pipe_violations`, `find_blockquote_break_violations`, `find_bare_backslash_violations`, `_find_code_spans`).

**`assert_freebuff_sections_render_clean.py`** — generalized gate walking every `## N.` section with per-table pipe-count internal consistency.

**`scripts/assert_test_counts.py`** — pre-flight test-count drift gate. Parses `tests/test_assert_section11_render_clean.py` with AST and verifies 5 classes meet expected minimums (total >= 37 tests). Exit 0 on clean, exit 1 on regression.

**`scripts/assert_config_mode_pins.py`** — pre-flight CONFIG-mode-pin gate (2026-09-27). Parses every `tests/test_*.py` with AST and flags `setUp`/`setUpClass`-level `patch.dict(<CONFIG>, ...)` calls that neither pass `clear=True` nor pin both `freeform_mode` and `mock_mode` — the leak class behind the 6 `test_call_chain_e2e.py` failures (the user's real `app_settings.json` mode preferences bled through `clear=False` patches and silently rerouted parse semantics; see the KNOWN_FAILURES.md status log). Method-level `@patch.dict` decorators and inline `with` blocks are out of scope by design. Exit 0 on clean, exit 1 on offenders; self-tests in `tests/test_assert_config_mode_pins.py`.

**`_runs/stress_test_memory_ceiling.py`** — memory-ceiling enforcement gate using `run_with_limits` to exercise `MemoryLimitExceeded` exception path. Exit 0 all-pass / 1 failure / 2 no psutil.

**`tests/test_assert_section11_render_clean.py`** — 37 tests across 5 classes exercising the four pure-function finders against fixture markdown slices.

## 13. Common Pitfalls & Gotchas

| Issue | Cause | Fix |
|-------|-------|-----|
| 400 Bad Request | Context window exceeded | Auto-truncation in `_summarize_and_evict()` and `process_input()` caps at 2500/1500 chars |
| **Perpetual "Still processing..." audit log** | Cleanup wired to data signal `worker.reply_signal.connect(lambda _: kc.done())`. If `handle_ai_response` raises, `kc.done()` strands → audit spam. See [§17 Rule 1](#rule-1----lifecycle-signals-for-cleanup-data-signals-for-data). | Wire to `worker.finished`: `self.worker.finished.connect(kc.done)`. Idempotent (double-fire safe). See [§17 Rule 1](#rule-1----lifecycle-signals-for-cleanup-data-signals-for-data). |
| **C++ error: `QObject::startTimer: Timers can only be used with threads started with QThread`** | `KeepaliveContext.done()` called directly from a worker thread (NOT main thread). `done()` touches the owning QTimer + button -- Qt-affinity-sensitive, crashes if touched off-thread. | Wrap in main-thread hop: `QTimer.singleShot(0, lambda: kc.done())` from `threading.Thread` target. Direct call OK for click-handler code. See [§17 Rule 2](#rule-2----cross-thread-kcdone-callers-must-hop-the-main-thread). |
| Cross-file Qt test hangs (Phase 5: **RESOLVED**) | `KokertechDashboard.closeEvent` calls `QApplication.instance().quit()`, setting the internal `closingDown` flag. Once set, Qt's `QWidget::create()` silently skips native window creation. | **Phase 5 fix:** `_suppress_quit` flag (see [§12](#12-testing-patterns)). |
| `test_hotkey_system.py` hang (**FIXED 2026-07-02**) | `shared_dashboard` fixture creation hang at test #7 (capture buffer) + real `closeEvent` I/O in `test_close_event_unhooks_hotkeys` (consolidation 5s + log-scan 5s + worker teardown 2s×2). | Fixed by patching `_call_with_timeout` + removing worker/thread/timeout attrs in test. All 48 tests pass in 21s (was: hang indefinitely). |
| `test_hotkey_controller_plugin_integration.py` fixture hang (**FIXED 2026-08-01**) | `create_neural_graph_tab()` spawns `GraphLayoutWorker` (QThread + real sqlite3 conn); via `layout_ready_signal → _on_layout_ready` it paints graph scene items inside `app.processEvents()` during fixture setup. | Full repro + sibling patches: **§13 Pitfall Detail** below. |
| SQLite concurrent access | Multiple threads writing to vault | Use `contextlib.closing(_get_conn())` + `with conn:` for auto-commit |
| Plugin import errors crash app | Bad plugin module | Caught by `_load_plugins()` try/except, errors recorded in `self.errors` |
| Config JSON corruption | Interrupted save (crash/kill) | `check_last_save_integrity()` + backup restore on next startup |
| Embedding model load failure | Missing `sentence-transformers` | `_embedding_load_attempted` flag prevents retries; `KOKERTECH_SKIP_EMBEDDINGS` disables entirely |
| TTS not working | Piper .onnx not found | Falls back to Windows SAPI automatically |
| Docker sandbox hangs | No Docker or image not pulled | `is_docker_available()` check + local fallback + timeout |
| **Full-suite `pytest` fd exhaustion** (FIXED 2026-07-02) | Python 3.14.5 closes capture buffer before `_ensure_unconfigure` reads it; FD leak in `_noop_fh_init` (`os.devnull` per FileHandler) compounded the issue | `--capture=no` in pytest.ini eliminates the capture buffer entirely. `_NullSink` singleton replaces `os.devnull` FD leak. All existing patches kept as defense-in-depth. Full 82-file suite (2,441 tests) passes. |
| **`pytest <file> | tail -N` hangs (2026-07-07)** | Piping pytest to `tail -N` (or `head -N`, `findstr -N`, `grep -m N`) blocks indefinitely when N exceeds pytest's output line count. Pytest `-q` mode only emits 2 lines (dots + summary), so `| tail -15` waits forever for 13 more lines. | Use `subprocess.run(capture_output=True)` instead of shell pipes. Exception: `pytest --collect-only -q | tail -2` is safe because `--collect-only` always produces >=6 lines. A lint script (`scripts/lint_pipe_starvation.py`) catches these patterns. |
| **`test_app_core.py` hang in full suite (RESOLVED 2026-07-14)** | `_make()` patched `init_ui`, not `_prewarm_model`. Daemon thread schedules `QTimer.singleShot(0, self._update_provider_badge)`; on `processEvents()` it hits a missing widget → `AttributeError` across C++ → indefinite hang. | Full repro: **§13 Pitfall Detail** below. |
| **Basher `python -c` escaping failures** | Nested escapes in basher `command` params cause `spawn_agents` JSON parse errors at 3+ nesting levels. See [Basher escaping fix patterns](#basher-escaping-fix-patterns) below. | Use disk-write-then-run (`_scratch/`), `str_replace`, or break into simple commands. See [§16](#16-quick-reference). Canonical reference: [`TOOL_USAGE_RULES.md`](TOOL_USAGE_RULES.md) §4. |
| **`memory_vault.py` SyntaxError on import (FIXED 2026-07-29)** | Two pre-existing syntax errors: (1) `ensure_tables_exist()` had a bare `try:` without `except`/`finally`; (2) `_return_conn()` had a duplicate `else: conn.close()` orphan. Both blocked `import memory_vault` on Python 3.14. | Full repro: **§13 Pitfall Detail** below. |
| **`--no-cov` (pytest-cov CLI flags) rejected inside subprocess pytest** | pytest-cov CLI flags are not registered in inner-subprocess argparse because `pytest.ini` disables the plugin via `-p no:cov` → `unrecognized arguments`. | See **§13 Pitfall Detail** below (cross-refs). |
| **Silent broad-catch bug-masking** (`except Exception: pass`) | ~157 `except Exception:` sites in production code; ~56 were *silent* (zero logging, `pass`/bare fallback) — bugs invisible, with no log, audit trail, or breadcrumb. | See **§13 Pitfall Detail** directly below (full record). |
| **tests/ silent-catch audit COMPLETE 2026-08-02 (v0.22.13)** | See **§13 Pitfall Detail** below — 136 test files scanned, 44 sites, 42 legitimate, 2 genuine fixes. |
| **test teardown dead-code cleanup (v0.22.16)** | See **§13 Pitfall Detail** below — 4 redundant `try/except` wrappers removed (`ignore_errors=True` already swallows). |
| **root+tabs silent-catch audit COMPLETE 2026-08-02 (v0.22.17)** | See **§13 Pitfall Detail** below — root `*.py` + `tabs/*.py` (78 files): 36 sites, 32 legitimate, 4 upgraded to `logger.debug`. |
| **misnamed doc file rescue (v0.22.18)** | See **§13 Pitfall Detail** below — `save_session.py` was a misnamed session log; converted to `save_session.md` losslessly. |


#### §13 Pitfall Detail (relocated: hang fixes + silent-catch audit)

> Full record relocated from the §13 pitfall table above (row-pointer + sub-section pattern: those cells exceeded the 240-char wide-cell limit enforced by `scripts/assert_known_anchors.py`). Nothing was removed — each row's original detail is preserved verbatim below.

- **Issue:** `test_hotkey_controller_plugin_integration.py` fixture hang (**FIXED 2026-08-01**)
- *Cause:* `shared_dashboard` fixture hangs at setup in cross-file batches. `create_neural_graph_tab()` calls `render_knowledge_graph()` at end of tab creation, which spawns `ui_components.GraphLayoutWorker` (QThread opening a real sqlite3 connection to `kokertech_vault.db`); via `layout_ready_signal → _on_layout_ready` it paints `NodeGraphicsItem`/`EdgeItem` scenes inside `app.processEvents()` during fixture setup/teardown.
- *Fix:* 4th sibling patch in `shared_dashboard` fixture: `patch("tabs.neural_graph_tab.NeuralGraphTabMixin.render_knowledge_graph", MagicMock())` (see [§12 item 8](#12-testing-patterns)). All 23 tests pass in 39s (was: hang indefinitely).

- **Issue:** **`test_app_core.py` hang in full suite (RESOLVED 2026-07-14)**
- *Cause:* ``_make()`` patched ``init_ui`` but not ``_prewarm_model``. Daemon thread schedules ``QTimer.singleShot(0, self._update_provider_badge)`` during ``__init__``. When ``app.processEvents()`` fires in test bodies, the callback accesses non-existent ``self.provider_badge`` (widget never created because ``init_ui`` was patched), raising ``AttributeError`` that propagates through PyQt6's C++ ``processEvents()`` boundary, causing indefinite hang.
- *Fix:* Patch ``_prewarm_model`` AND ``_update_provider_badge`` whenever ``init_ui`` is patched in a test that calls ``app.processEvents()``. See ``tests/test_app_core.py::_make()`` for the fix pattern. Also consider catching ``AttributeError`` in production ``_update_provider_badge()`` as defense-in-depth.

- **Issue:** **`memory_vault.py` SyntaxError on import (FIXED 2026-07-29)**
- *Cause:* Two pre-existing syntax errors: (1) `ensure_tables_exist()` had a bare `try:` block without `except`/`finally`; (2) `_return_conn()` had a duplicate `else: conn.close()` creating an orphaned else. Both prevented the module from importing on Python 3.14, breaking `import memory_vault` in all consuming modules and 6 test files.
- *Fix:* (1) Added `finally: _return_conn(conn)` after `conn.commit()` in `ensure_tables_exist()` so the connection is always returned to the pool. (2) Removed the duplicate `else: conn.close()` block from `_return_conn()`. Both fixes are minimal (1 line each) and preserve the original logic.

- **Issue:** **`--no-cov` (pytest-cov CLI flags) rejected inside subprocess pytest**
- *Cause:* pytest-cov CLI flags are NOT registered in inner-subprocess argparse because `pytest.ini` disables the plugin via `-p no:cov`. Passing them rejects as `unrecognized arguments` — uniform across parent + subprocess (no plugin-loading asymmetry).
- *Fix:* Strip pytest-cov CLI flags from subprocess argv lists; rely on `-p no:cov`. Cross-reference: `KNOWN_FAILURES.md` 2026-07-21 + v0.20.1.

- **Issue:** **Silent broad-catch bug-masking** (`except Exception: pass`)
- *Cause:* ~157 total `except Exception:` sites found in production code; ~56 of those were *silent* (zero logging, `pass` or bare fallback). Bugs hiding behind these silent catches were invisible — no log, no audit trail, no breadcrumb. The remaining ~101 already had logging or specific exception types.
- *Fix:* **Audit landed 2026-07-28**: all 56 silent sites in 9 core files changed to `except Exception as e:` + `logger.debug/warning/error(...)` based on severity. Catch **TYPE** intentionally NOT narrowed (tests raise `RuntimeError` deliberately to exercise graceful-degradation fallback paths; tests assert on return values, not log output). Logging levels: `debug` for read fallbacks, `warning` for config load + vault writes (data loss), `error` for save/restore/export failures. See v0.21.1. **tabs/ sweep landed 2026-08-02 (v0.22.4)**: 30 additional silent sites across 5 tab modules (`settings_tab` 21, `health_tab` 4, `neural_graph_tab` 2, `global_search` 2, `cognitive_agency_tab` 1) converted with module-level `get_logger` per the same severity mapping — `warning` ONLY for config/vault writes, `debug` for read fallbacks + audit-guard breadcrumbs (never `warning` on the 2s health-poll hot path). Comment-aware AST inventory: 0 silent sites remaining in those modules. **plugins/CLI/services sweep landed 2026-08-02 (v0.22.6)**: 9 additional silent sites across `plugins/capture_screen.py`, `services/code_intelligence.py`, `cli/commands.py`, `cli/formatting.py` converted with stdlib `logging` module loggers (circular-import-safe leaf pattern per the `code_intelligence.py` precedent) — `warning` ONLY for the 3 data-loss `save_settings`/`save_session` write sites, `debug` for read fallbacks (nvidia-smi VRAM probe, model resolution, ripgrep fallback, screenshot cleanup); the `_audit_log` inner guard is documented as a deliberate degenerate fallback (not a silent catch — logging itself failed). 217 tests pass across the 4 affected suites. **services/+plugins/ audit COMPLETE 2026-08-02 (v0.22.10)**: two-pass inventory (pass-body awk + AST no-logger scan) over all 75 handlers in `services/` (38) + `plugins/` (37) → **ZERO silent `except Exception: pass` sites remain**. All AST-flagged candidates classified: ~44 user-visible `❌` error returns (surfaced to caller — not silent), 3 typed graceful fallbacks deliberately left per the v0.21.1 convention (now documented inline: `context_service.py` RuntimeError double-invoke + 2× `prompt_service.py` JSONDecodeError keep-prior-value), ~6 already-logged via `log(...)` param/`_audit_log` dict (AST false positives), ~4 re-raises.

- **Issue:** **tests/ silent-catch audit COMPLETE 2026-08-02 (v0.22.13)**
- *Cause:* two-pass AST scan over all 136 test files (`tests/*.py` + root `test_*.py`) → **44** `except Exception: pass` / bare `except:` sites, 0 silent-return bodies. 42 classified legitimate: Qt Phase-4 teardown guards (`_suppress_quit`/`close()`/`deleteLater()`/`processEvents`), mock `.stop()` loops, `shutil.rmtree(..., ignore_errors=True)` temp cleanup, service/controller resets, fallback-logging helper, and the intentional test-design swallow in `test_copilot_features.py::test_speak_piper_cleanup_on_error` (asserts the `finally` cleanup ran despite a simulated error — raising would mask the real assertion). 2 genuine fixes: `test_config.py::TestIsValidJson.tearDown` suite's only **bare** `except:` tightened to `except OSError:` (v0.21.1 convention — no KeyboardInterrupt/SystemExit swallowing), and `test_sprint13_hybrid_search.py::TestMemoryVaultFTS5._cleanup_row` now logs `logger.warning(...)` on FTS-rebuild failure (a dirty index would otherwise surface as a confusing failure in the NEXT test). See v0.22.13.

- **Issue:** **test teardown dead-code cleanup (v0.22.16)**
- *Cause:* Removed the 4 redundant `try/except Exception: pass` wrappers around `shutil.rmtree(..., ignore_errors=True)` in test teardowns (`test_context_service.py` ×2, `test_workflow_engine.py` ×1, `test_run_pre_flight.py` ×1) — dead redundancy flagged by the v0.22.13 reviewer, since `ignore_errors=True` already swallows the OSError family. Each site now carries a `ignore_errors=True already swallows; no outer try/except needed` comment; `import shutil` moved above the loop in test_context_service.py. 42 passed / 4 skipped across the 3 files. See v0.22.16.

- **Issue:** **root+tabs silent-catch audit COMPLETE 2026-08-02 (v0.22.17)**
- *Cause:* Extended the silent-catch audit (v0.22.10 services/+plugins/, v0.22.13 tests/) to the remaining source: root `*.py` modules + `tabs/*.py` (78 files). **36** broad/bare `except Exception: pass` sites, 0 silent-return bodies. **32 classified legitimate as-is** (cleanup/teardown guards: `docker_sandbox.py` container kill/remove, `memory_vault.py` conn rollback + pool close, `wake_word.py` PyAudio terminate, `auto_logger.py` interceptor write/flush, `workers.py` thread interrupt, `mcp_server.py` HTTP shutdown, `kokertech_logger.py` makedirs, `auto_linker.py` table create; intentional fallbacks per v0.21.1: `ai_base.py` nvidia-smi VRAM with pre-seeded 0 + `.get()` consumers, `app_core.py` onboarding, `sub_agents.py` callback wrapper, `app_ui.py` provider badge, `kokertech_bridge.py` plugin-disabled lookups, `decompile_from_pyc.py` bytecode parsing, `doc_pipeline.py` progress callbacks, `kokerpro_web.py` intent actions, `ui_components.py` graph fetch). **4 sites upgraded to `logger.debug`** in `agentic_rag.py` (semantic/episodic/outer-memory-vault/web-search fallbacks) — per the v0.22.13 precedent, silent search-path failures masked real FTS5 production bugs earlier this session; debug level avoids spam when `KOKERTECH_SKIP_EMBEDDINGS` makes semantic_search fail by design. Behavior-preserving; 72 tests pass. Init-race audit also clean: no connect-before-set anti-patterns in `tabs/settings_tab.py`. See v0.22.17.

- **Issue:** **misnamed doc file rescue (v0.22.18)**
- *Cause:* `save_session.py` (the v0.22.17 audit's sole parse failure — unterminated `doc = """` string at line 122) turned out to be a Sprint 14 Expansion session log wrapped in a stray string literal, imported nowhere. Converted to `save_session.md` by stripping the wrapper (`sed '1s/^doc = """//'`, 122 lines preserved losslessly) and deleted the `.py`. Root-module AST scan now reports **0 parse failures across 77 files**. KNOWLEDGE §2 table row updated in both copies. See v0.22.18.

#### Basher escaping fix patterns

When passing complex Python operations to the basher agent, avoid nesting quote
characters inside JSON strings. The `spawn_agents` JSON parser chokes on `\\\"`
(backslash-escaped double-quote) at 3+ levels of nesting, producing errors like
`Expected '}'`. This session saw 5+ consecutive failures attempting complex
`python -c` one-liners before the disk-write-then-run workaround was discovered.

**Three fix patterns (in order of preference):**

1. **Disk-write-then-run** — write a `.py` script to `_scratch/<name>.py` via
   `write_file`, then execute with a simple basher command. Avoids all escaping
   issues. See `_scratch/README.md`.
2. **`str_replace`** — for file edits, prefer `str_replace(oldString=..., newString=...)`.
   Its parameters are JSON-safe strings without escape nesting.
3. **Break into simple commands** — each basher `command` string uses minimal
   escaping. Threshold: if the command contains `\\\"`, refactor.

#### pytest-cov CLI rejection diagnostic — uniform

When `pytest.ini` disables `pytest-cov` via `-p no:cov`, the pytest-cov entry-point fails to register and its CLI argv flags are silently absent from argparse. So a spawned pytest subprocess forwarding them rejects with `unrecognized arguments: <pytest-cov-flag>` — **uniform across parent and subprocess** (no plugin-loading asymmetry to chase; verifying `PYTEST_DISABLE_PLUGIN_AUTOLOAD` / `sys.path` / pytest-cov entry-point loading is the wrong diagnostic path).

**Latent fault class:** a CI-green parent pytest run masks a child-subprocess failure that only surfaces when the subprocess actually fires. Sprint 21 swept all subprocess test runners (`tests/test_ignore_known_failures_chain.py` + `_scratch/run_*.py`) and added inline regression guards so future contributors adding pytest-cov flags to existing subprocess argvs pick up the pattern. Cross-reference: `KNOWN_FAILURES.md` Resolution Log row dated 2026-07-21 + v0.20.1.

### Mtime-Asymmetry Git-Proxy Technique

> **Why this section exists:** Decision 7 originally chose *No Git Version Control*, revoked 2026-09-22 in favour of local Git (see §3 Decision 7) — so history predating the project's first commit has no `git log` / `git blame` / diff machinery to consult. For changes made before the first commit, the canonical way to infer "what changed last" is still **file mtime + execution_log.txt**.

**Method:**
1. `ls -la --time-style=full-iso path/to/prod.py path/to/test.py` — compare mtimes
2. `ls -lat --time-style=full-iso *.py | head -20` — compare against rest of project

**Key insight:** A production file whose mtime is LATER than its test file is NOT necessarily the culprit. Production modules rarely import test-mock primitives (`MagicMock`, `Mock`, `unittest.mock`). **Exonerate production first** with `grep -nE 'from unittest\\.mock|import unittest\\.mock|MagicMock =|Mock =' prod.py`. If 0 matches, the bug is in the test file — triage the first 30 lines of the test file before chasing a production class-local shadow.

---

## 14. Protocol Preset System

### Architecture
- Protocol presets stored in `CONFIG["protocol_presets"]` as `{name: content}` dict
- Built-in templates defined in `BUILTIN_PROTOCOL_TEMPLATES` module-level constant
- Auto-seeded on first run via `_seed_builtin_protocol_presets()` called from `create_settings_tab()`
- `_rebuild_protocol_presets()` rebuilds the QComboBox from CONFIG with content-matching

### Built-in Templates

| Template | Style | Key Features |
|----------|-------|-------------|
| **Structured with reasoning** | XML | `<thinking>` + `<final_output>` tags |
| **Freeform Assistant** | Natural | Conversational, no XML |
| **Concise Coder** | Brief | Code-focused, minimal explanation |

### UI Elements

| Element | Location | Behavior |
|---------|----------|----------|
| Protocol preset combo | Settings → System Protocol | Custom... + saved presets |
| Count label | Next to combo | "3 presets" / "1 preset" / empty |
| Validation warning | Below editor | Amber warning for missing/conflicting tags |
| Protocol override badge | Core tab sidebar | Amber "CUSTOM PROTOCOL" when active |
| Protocol badge (input row) | Core tab input row | emoji badge when active |

### Validation Rules
- **Structured mode**: warns if `<thinking>` or `<final_output>` missing
- **Freeform mode**: warns if XML tags present (will be ignored)
- **Empty**: no warning (built-in default used)
- Triggers: keystroke, mode toggle, preset load, backup restore, reset

### Key Methods

| Method | Purpose |
|--------|---------|
| `_seed_builtin_protocol_presets()` | Seed built-in templates on first run |
| `_rebuild_protocol_presets()` | Rebuild combo from CONFIG |
| `_on_protocol_preset_changed(index)` | Load preset into editor |
| `_on_protocol_text_changed()` | Switch to Custom... on manual edit |
| `_save_protocol_preset()` | Save current content as named preset |
| `_delete_protocol_preset()` | Delete selected preset with confirmation |
| `_validate_protocol()` | Live validation with amber warnings |
| `_update_protocol_badge()` | Show/hide badge in input row |

### Keyring Integration
- "Save to Keyring" button next to API key input in Settings
- Saves to Windows Credential Manager via `save_api_key()` / `save_openrouter_api_key()`
- Empty input clears stored key from keyring
- Audit log on save/clear

---

## 15. Document Inventory

| Document | Location | Purpose |
|----------|----------|---------|
| KNOWLEDGE.md | Root | **This file** — the single project knowledge base |
| AGENTS.md | Root | Agent operating system (published) |
| SOUL.md / IDENTITY.md / USER.md / MEMORY.md | Local only — gitignored, not published | Persona, identity, human context, long-term memory |
| `agent/Buffy.agent.md` | `agent/` | **Buffy agent v1.1 behavior contract** — strategic coding assistant; invariants reference AGENTS.md §3 as SSOT |
| `agent/Kokertech.agent.md` | `agent/` | **God Reviewer v4.2 behavior contract** — audit-mode routing, edge-case matrix, pre-flight gates |

> Locations verified 2026-09-24. Removed as no-longer-present: "visual summary of specs.md", "app_core-review-spec.md".
| `scripts/verify_snapshot_roundtrip.py` (+ `scripts/backup_session_chats.py` + `scripts/restore_session_chats_backup.py`) | `scripts/` | Sprint 14 expansion: full DR chain — sha256-verified backup + atomic-copy restore + roundtrip verifier |
| `scripts/cron_nightly_dr_smoke.bat` (+ `scripts/install_nightly_dr_smoke_task.ps1`) | `scripts/` | nightly DR cron — schtasks installer, 03:00 daily as SYSTEM (Sprint 14). Sprint 19.8.2 adds STAGE 3 vault census (`--census`); exits non-zero on DR drift or census FAIL (Slack webhook). Not registered — run installer as admin. |
| `scripts/vault_maintenance.py` | `scripts/` | Sprint 19.8: opt-in vault health report + retention (`--execute`, importance>=9 exempt), FTS rebuild, VACUUM, `--migrate-embeddings` (staged, verify-before-write, idempotent). Can rewrite 600k+ rows. |
| `_scratch/run_test_suite.py` (+ `_scratch/run_tests_batched.py` + `_scratch/run_tests.bat`) | `_scratch/` | Sprint 21 expansion: batched test runners for CI — isolate timeout offenders, split suite into parallel-chunk-safe subsets |
| execution_log.txt | Root | Session execution logs |

### mcp_server.py Improvements (2026-07-08)

The `__main__` entry point received several reliability upgrades:

| Improvement | Details |
|-------------|---------|
| **Main-thread `serve_forever()`** | Replaced daemon thread + `while True: time.sleep(1)` busy-wait with the canonical `HTTPServer.serve_forever()` on the main thread — eliminates the polling loop entirely |
| **Port release** | Added `server.server_close()` to `stop_http_server()` so the TCP port is released immediately on shutdown (prevents "Address already in use" errors on restart) |
| **Signal handlers** | Added `_handle_sigterm()` and `_handle_sighup()` that raise `KeyboardInterrupt` — `kill <pid>`, `kill -HUP <pid>`, and `service stop` all trigger the same graceful cleanup path as Ctrl+C |
| **`--port` CLI argument** | Added `_parse_args()` — `python mcp_server.py --http --port 8080` to bind to a custom port (default 9100) |
| **`import time` moved to top-level** | Eliminated per-iteration import overhead from the old `while True` body |

All 77 related tests pass (`test_mcp_server.py` + `test_mcp_client.py` + `test_mcp_client_tab.py`).

---

## 16. Quick Reference

### Startup Sequence
1. `app_core.py` -> `KokertechDashboard.__init__()`
2. Initialize `KokertechController` (loads provider, verifies vault)
3. `init_ui()` creates all 24 tabs via mixins
4. Start VRAM monitor timer
5. Start cleanup timer (resource management)
6. Start consolidation timer (episodic -> long-term memory)
7. Apply theme
8. API auto-start (if configured)
9. Notification manager init
10. Onboarding wizard (if first run) OR save integrity check

### Key Files to Edit for Common Tasks

| Task | File |
|------|------|
| Add new config setting | `config.py` + `app_settings.json` (see [§11](#11-configuration-system)) |
| Add new plugin | `plugins/<name>.py` (auto-registered) |
| Add new UI tab | `tabs/<name>.py` + mixin in `app_core.py` |
| Change AI provider | `config.py` CONFIG or Settings tab |
| Add new sub-agent persona | `sub_agents.py` PERSONAS dict |
| Change model name | `kokertechController.py` `target_model` var or Settings tab |
| Add new workflow action | `workflow_engine.py` `execute_step()` |
| Change memory limits | `kokertechController.py` `memory_limit` var |
| Toggle desktop notifications | `tabs/settings_tab.py` checkbox or `kokerpro_web.py` Settings tab — shares `notifications_enabled` CONFIG key across both surfaces |
| Tune keepalive cap | `app_settings.json` CONFIG key `keepalive_max_ticks` (see [§11](#11-configuration-system)) |
| Wire cleanup hook to a worker | `worker.finished.connect(cleanup_fn)` — NOT `reply_signal` (see [§17](#17-worker-signal-wiring-patterns)) |
| Add a long-running UI affordance | `with kc.start_keepalive(label, interval_ms): do_work()` (see [§18](#18-keepalivecontext----context-manager-status-abort-hook)) |
| Add ANTI-FRAGILITY or REGRESSION GUARD doc-comment to fragile test setup | (see [§12](#12-testing-patterns)) |
| Add resource history persistence | `resource_history.py` + `tabs/health_tab.py` (push 4 metrics every 2s, save debounced 30s, flush on closeEvent) |

### AI Agent Tool Usage Patterns (Operational)

These are patterns for how AI coding agents (Buffy, basher, etc.) should use tools when working on KokertechAI. They are operational conventions, not Python code conventions.

> **Canonical reference:** [`TOOL_USAGE_RULES.md`](TOOL_USAGE_RULES.md) — tool-failure patterns compiled from 7 Freebuff sessions (Jun 29 – Jul 14, 2026; Sep 2026 corrections applied). Covers all tools: `str_replace`, `write_file`, `basher`, `heredoc`, `spawn_agents`, pipe-starvation, resource leaks, system gotchas. Decision matrix at §5 — which tool to use when.
>
> **Buffy agent anti-patterns:** [`agent/Buffy.agent.md`](agent/Buffy.agent.md) §8 — Buffy-specific gotchas including basher escaping, `write_file` vs `str_replace`, and the disk-write-then-run pattern.

#### Basher command escaping

**Anti-pattern — complex `python -c` one-liner:**
```
# FAILS: spawn_agents JSON parser chokes on nested \" escapes
basher: python -c "from pathlib import Path; c=Path('file.py').read_text(); c=c.replace('old', 'new'); Path('file.py').write_text(c)"
```

**Correct — write script to `_scratch/`, then run:**
```
# Step 1: write_file to _scratch/fix_thing.py
# Step 2: basher: python _scratch/fix_thing.py
```

**Correct — use `str_replace` for file edits (no escaping needed):**
```
str_replace(path="file.py", oldString="...", newString="...")
```

**Correct — break into simple basher commands:**
```
# Each basher command has minimal escaping
basher: cd C:/KokertechAI && python -c "print('simple')"
```

**Rule of thumb:** If your basher `command` string contains `\\"` (backslash-escaped double-quote inside a JSON string), refactor to one of the three correct patterns above. The threshold for failure is typically 3+ levels of escape nesting.

> **Extended reference:** [`TOOL_USAGE_RULES.md`](TOOL_USAGE_RULES.md) §3 covers basher heredoc/escape failures in detail (3 failure types: Unicode escapes from Windows paths, EOF from nested quotes, bash syntax from parentheses). See also §6 Anti-Patterns (incl. the retired "write_file cannot create files" lore — it creates files + parent dirs fine, verified 2026-09-24), `spawn_agents` structural failures, and the Decision Matrix (§5) for choosing the right tool.

#### Disk-write-then-run pattern

When a complex Python operation is needed (multi-line edits, regex replacements, JSON manipulation), write a standalone `.py` script to `_scratch/<name>.py` via `write_file`, then execute it with a simple basher command. This pattern:
- Avoids all escaping issues entirely
- Leaves an audit trail in `_scratch/` for reproducibility
- Makes the operation testable and reviewable as a standalone script

#### `str_replace` over basher for file edits

Prefer `str_replace` (with `oldString` / `newString`) over `python -c ... read_text()... write_text()` basher commands for file modifications. `str_replace` handles line endings and encoding natively, and its parameters are JSON-safe strings without escape nesting.


---

## 17. Worker Signal Wiring Patterns

The perpetual **"Still processing..."** audit-log bug (June 2026) traced back to a single deep root cause: **cleanup hooks were wired to data signals, not lifecycle signals**. PyQt aborts remaining slot iterations when a previous handler raises -- chaining `kc.done()` to `worker.reply_signal.connect(...)` strands the cleanup if `handle_ai_response` crashes. This section codifies the wiring rules so future contributors don't ship the same bug.

### Rule 1 -- Lifecycle signals for cleanup, data signals for data

| Signal category | Example | Qt signal | Purpose |
|-----------------|---------|-----------|---------|
| **Lifecycle** | `worker.finished` | `()` | Thread end (normal exit, exception, terminate). Always fires. |
| **Data** | `worker.reply_signal` | `(payload)` | Final AI response. May raise mid-chain. |
| **Data** | `worker.stream_signal` | `(token)` | Streaming token chunks. May raise mid-chain. |
| **Data** | `worker.log_signal` | `(message)` | Audit breadcrumbs. May raise mid-chain. |

**The Fix** -- wire cleanup hooks ONLY to `worker.finished`:

```python
# CORRECT -- kc.done() is a 0-arg callable, fire-once, idempotent.
self.worker.finished.connect(kc.done)

# ANTI-PATTERN -- strands kc.done() on handle_ai_response crash.
self.worker.reply_signal.connect(self.handle_ai_response)
self.worker.reply_signal.connect(lambda data: kc.done())
```

`finished` is a separate signal: when the underlying QThread exits, Qt fires it regardless of how (normal reply, exception, `terminate()`, even an external kill). Its 0-arg signature matches `kc.done()` exactly -- no lambda required. `done()` is **idempotent** so the `worker.finished -> reply_signal` double-fire at teardown is safe (the second fire is a no-op via `if self._done: return`).

### Rule 2 -- Cross-thread `kc.done()` callers MUST hop the main thread

The current `done()` is **synchronous** -- it sets `self._done = True` then calls `_finalize_cleanup()` directly. `_finalize_cleanup()` stops the owning QTimer, restores the button text, and re-enables the button. All Qt-affinity-sensitive work.

Calling `kc.done()` directly from a worker thread produces the **C++ error message** `QObject::startTimer: Timers can only be used with threads started with QThread` and leaves the timer unstopped. If you see this in stderr/log and the audit log fills with "Still processing..." after that line, the forgot-the-hop bug is the cause.

```python
# CORRECT -- main-thread hop
from PyQt6.QtCore import QTimer
QTimer.singleShot(0, lambda: kc.done())

# ALSO OK -- direct call (click-handler code is already on main thread).
self._action_stop_generation()  # button click runs on main thread
keepalive.done()                # safe direct call

# ANTI-PATTERN -- direct call from threading.Thread(...) target.
def _do():
    ...
    kc.done()                    # CRASHES -- background-thread affinity violation
threading.Thread(target=_do, daemon=True).start()
```

### Rule 3 -- Cleanup hooks should be **idempotent**

Wire cleanup to **every** lifecycle signal that might fire, then make the cleanup itself a no-op on the second call:

```python
def done(self):
    """Idempotent -- second call short-circuits on self._done guard."""
    if self._done:
        return
    self._done = True
    self._finalize_cleanup()
```

This lets the same keepalive context safely sink cleanup signals from BOTH `worker.finished` AND `_action_stop_generation` (the Stop button) without producing duplicate button restores or duplicate "saved" badges.

### Rule 4 -- Session-end reconciliation

Every Qt-owning dashboard whose workers spawn keepalive contexts MUST add a near-top-of-`closeEvent` reconciliation block:

```python
def closeEvent(self, event):
    self._shutting_down = True       # FIRST line -- guards signal handlers
    ...
    # Session-end keepalive reconciliation
    keepalive = getattr(self, "_active_keepalive", None)
    if keepalive is not None:
        try:
            breadcrumb = getattr(keepalive, "_last_breadcrumb", None) or "no breadcrumb"
            self.file_logger.warning(
                f"[keepalive] Active at shutdown: label={keepalive._label!r}, "
                f"fired={keepalive._fired_count}, breadcrumb={breadcrumb}"
            )
            self.log_to_audit(
                "Keepalive context still active at shutdown -- force-stopping"
            )
        except Exception:
            pass
        try:
            keepalive.done()
        except Exception:
            pass
        self._active_keepalive = None
    ...
    event.accept()
```

The auto-abort safety net (`MAX_TICK_FIRES = 4 x 30s = ~2 min`) would normally catch a stranded keepalive, but a clean shutdown happens faster than that. This block surfaces the reconciliation to file_log + audit_trail so future code-reviewers can spot the pattern.

### Anti-patterns to avoid in future code review

- `worker.<data_signal>.connect(lambda _: cleanup_fn())` -- chains cleanup to a data-raising slot.
- `kc.done()` called directly from a `threading.Thread(target=...)` function without `QTimer.singleShot(0, ...)` wrapping.
- Two distinct cleanup signals (`worker.finished` AND `worker.deleteLater`) wired to two different cleanup functions without idempotence -- second fire races.
- Forgetting the `_shutting_down` guard on a signal handler that touches widgets during teardown -- crashes the entire close path.

---

### Sprint 18 R3 Closeout (2026-07-19): KNOWN-ANCHORS gate landed

> **Purpose.** Sprint 18 R3 added a 4th pre-flight CI gate (`scripts/check_knowledge_anchors.py`) for the documented 4-gate sequence named in section 1 (`FREE-BUFF-SECTIONS -> SECTION-11 -> KNOWN-ANCHORS -> pytest`). The gate is the missing-script realization of the `KNOWN-ANCHORS` slot -- it scans every Markdown `[text](#anchor)` inline link in `KNOWLEDGE.md` against every H1-H6 heading (computing the slug via GitHub's html-pipeline algorithm `gh_anchor()`); Tier 1 = HARD-BROKEN (exit 1), Tier 2 = bare section-N mention DRIFT WARNING (informational, exit 0).

**Stale-anchor sweep (the rationale).** On first run, `check_knowledge_anchors.py` surfaced 2 unique hard-broken targets: `[Section 17 Rule 1](#rule-1--lifecycle-signals-for-cleanup-data-signals-for-data)`  + `[Section 17 Rule 2](#rule-2--cross-thread-kcdone-callers-must-hop-the-main-thread)` . Both violated GitHub's literal-dash-preservation: the `### Rule N -- Lifecycle Signals ...` heading texts contain a literal `--` that the algorithm preserves verbatim, producing 4-dash slugs `rule-N----lifecycle-signals...` (1 dash from `"1 "`, 2 from literal `"--"`, 1 from `" lifecycle"`). The pre-R3 links wrote only 2 dashes; after `str_replace` fixes the anchor gauge now matches the algorithm output. A 3rd surfaced-broken target (`[Basher escaping fix patterns](#basher-escaping-fix-patterns)`) was a verifier-scope bug -- the prior round scanned only `section 2 + section 3` headings; post-R3 the scope is `^#{1,6}` so H4 sub-subsections resolve.

**5 invariants pinned by `tests/test_check_knowledge_anchors.py` (132 LOC, 6 cases passing in 4.19s):** (1) e2e real-KNOWLEDGE.md canary, (2) `[](#nope)` exits 1 with HARD-BROKEN, (3) duplicate-on-same-line links dedup to 1 unique broken target, (4) `#### Foo Bar Baz` heading slugifies and resolves (proves H1-H6 scope), (5) bare section-N-N mention without a real `## N.` triggers DRIFT WARNING (exit 0), (6) `gh_anchor()` spec pin: 4 ground-truth fixtures locked against future GitHub algorithm drift.

**Wired into `_runs/_run_full_gate.{bat,sh}` (this Sprint):** the new gate invocation lands BEFORE the cross-validate validator on both platforms, with early-exit on HARD-BROKEN so a single 4-gate run aborts cleanly on the first failure. Future sprints calling `_run_full_gate.*` get KNOWN-ANCHORS enforcement for free.

**Drift-resistance note.** Cite `gh_anchor()` by *function name* -- it mirrors GitHub's html-pipeline algorithm verbatim, so any future contributor reusing the function computes the same slug. If GitHub changes its algorithm, the spec pin in `tests/test_check_knowledge_anchors.py` fires; the section 17 entry stays valid until `gh_anchor` is intentionally re-derived.

## 18. KeepaliveContext -- Context Manager, Status, Abort Hook

`KeepaliveContext` (in `keepalive_helper.py`) manages the "Still capturing... (N s elapsed)" periodic audit log + click-handler button disable/restore for long-running operations. The original exclusion-style API (`kc = KeepaliveContext(...); kc.start_keepalive(); ...; kc.done()`) is still supported; the context manager + status + abort hook surface below were added June 2026 to plug the audit-log-spam bug and to give callers a richer observability + UI integration story.

### Class-level constants

```python
class KeepaliveContext:
    MAX_TICK_FIRES = 4   # Class attribute (not module-level -- class lookup
                         # does NOT fall through to module globals, so a
                         # module-level constant would raise AttributeError
                         # on hasattr(cls, "MAX_TICK_FIRES")). Default cap
                         # on breadcrumb emissions before auto-abort.
```

### Context manager -- `__enter__` / `__exit__`

`start_keepalive(...)` returns `self`, so:

```python
with kc.start_keepalive("processing", interval_ms=30000) as kc:
    # Do work. kc.done() is auto-called on exit -- even if an exception
    # tore through this block. Returns False so the exception is re-raised
    # normally; we clean up but never swallow user-code errors.
    self.worker.finished.connect(kc.done)   # belt AND suspenders
    self.worker.start()
```

| Method | Behaviour |
|--------|-----------|
| `__enter__(self)` | Returns `self` for `with kc as kc` ergonomics. No internal state mutation -- `start_keepalive` is the explicit init. |
| `__exit__(self, exc_type, exc_val, exc_tb)` | Always invokes `self.done()` so the QTimer stops + the button restores. **Always** returns `False` (so caller exceptions re-raise). Multiple `done()` calls are safe via the idempotent guard. |

### `default_max_ticks()` -- classmethod

Reads `CONFIG["keepalive_max_ticks"]` per `start_keepalive` call (latest value, not cached on first import). Graceful fall-through: `ImportError`, `ValueError`, `TypeError`, `KeyError` -> `MAX_TICK_FIRES = 4`. Re-evaluated on each `start_keepalive` so a Settings-tab change takes effect on the **next** keepalive (not retroactively).

```python
@classmethod
def default_max_ticks(cls) -> int:
    try:
        from config import CONFIG
        value = CONFIG.get("keepalive_max_ticks", cls.MAX_TICK_FIRES)
        return max(1, int(value))    # coerce + clamp -- non-int CONFIG
                                       # can't silently propagate.
    except (ImportError, ValueError, TypeError):
        return cls.MAX_TICK_FIRES      # graceful fallback
```

---

## 19. Architecture Evolution

> **Purpose:** Three architecture docs were produced during development: (a) the original 2026-06-15 sister review at ARCHITECTURE_REVIEW_KOKERTECH.md — durable 8-sprint refactor roadmap; (b) the 2026-07-21 status re-assessment by the **kokertech agent** (renamed from L99 on 2026-07-21) at kokertech_agent_architecture_review_2026_07_21.md — re-scopes Sprints 1–8 against today's `kokertechController.py` (1137 LOC), `memory_vault.py` (2924 LOC), `app_core.py` 25-class MRO with 4 critical + 6 high-priority + 7 architecture decisions + 6 edge cases + 12-item verification checklist; (c) the **Sprint 19 decomposition plan** to address Critical Finding C1 at sprint19_kokertech_controller_decomposition_plan_2026_07_21.md — phased 1137 → <400 LOC plan (882 lines removed in Sprint cleanup) with sub-sprints 19.1 (momentum headers, 15 min) → 19.2 (SkillService) → 19.3 (settings-cache + provider-stream services) → 19.4 (6-stage in-file pipeline) → 19.5 deferred (OrchestrationService extraction).

### Original Architecture Review — 2026-06-15 (kokertech agent, formerly L99)

The full L99 review (a development-time artifact, not published with this repository) documents:
- **4 critical findings** (C1-C4): God-object controller, module-level singletons, monolithic prompt, 24-mixin MRO
- **6 high-priority findings** (H1-H6): No DI, inconsistent error contracts, no plugin schema validation, mixed concurrency, per-turn rebuild, no SQLite pool
- **7 architecture decisions** with explicit winners and reasoning
- **8-sprint refactor roadmap** (S1-S8) with code sketches
- **6 edge cases** with runnable pytest + detection + mitigation
- **12-item verification checklist** with runnable commands

### Drift Prevention
- **Tab count** should be auto-verified by a script. If `KNOWLEDGE.md` §9 disagrees with the actual MRO in `app_core.py`, drift is present — fix immediately.
- **Module size table** should be re-verified against `wc -l *.py` on each release.
- **Sprint status** in the private development roadmap is the source of truth for in-flight work.

### PENDING/DEFERRED Baseline (2026-07-21 Audit)

Current PENDING/DEFERRED inventory, captured at Sprint 19.2 close-out (19.3 row closed 2026-07-31 — see CHANGELOG v0.21.8; 19.4 shared-stage core closed 2026-08-01 — see CHANGELOG v0.22.1).

| Tag | Count | Locations |
| Quarantined files (n=44, pending source reconstruction) | 44 + 9 forensic (53 total) | [`recovery_manifest.md`](recovery_manifest.md) table; forensic copies preserved alongside restored `.py`. Baseline added 2026-07-22 after `.pyc` corruption incident (see [KNOWN_FAILURES Resolution Log (2026-07-22)](KNOWN_FAILURES.md)). |
| 🛡️ Quarantine count invariant (OFF_BY_ONE defensive checklist, [`scripts/assert_no_corrupted_files.py`](scripts/assert_no_corrupted_files.py)) | 1 invariant | `assert_no_corrupted_files.py::validate_quarantine_baseline()` asserts `row_count == len(quarantined_files)` BEFORE set-equality (2026-07-22 off-by-one pattern; fires `OFF_BY_ONE`). Full detail: `recovery_manifest.md`. |
|-----|-------|-----------|
| ⏳ PENDING | 0 | — (19.4 shared-stage core closed 2026-08-01; stages 4–6 remain inline per plan note — not a tracked row) |
| ⏳ DEFERRED | 2 | Milestone-summary table: row 19.5; sub-task table: row 19.5 |
| ⬜ DEFERRED | 1 | L99.5 row |

All rows are intentional future work — Sprint 19.5 (OrchestrationService extraction, deferred post-19.4) and L99.5 (cross-link sync). Sprint 19.3 (ProviderStream + SettingsCache extraction) closed 2026-07-31 per CHANGELOG v0.21.8; Sprint 19.4 shared-stage core (`_stage_build_history` / `_stage_fetch_context` / `_stage_assemble_history_messages` / `_reject_with_error`) closed 2026-08-01 per CHANGELOG v0.22.1. Track closure deltas against this baseline when each sub-sprint lands.

> **Drift-resistance note:** The line-number references in this section (293, 294, 295, 522, 528, 529, 530, 769) were captured as a snapshot on 2026-07-21 and will silently drift if the roadmap gains or loses lines. The row-content identifiers in the Locations column (e.g. "milestone-summary table: rows 19.3, 19.4") are drift-resistant. Re-capture the full baseline after any Sprint closure that modifies the PENDING/DEFERRED inventory.

**Corrections made post-baseline (2026-07-21):** Sprint 7 and Sprint 8 summary status rows were updated from `~75%` / `~87%` to **✅ 100%** in the roadmap. The detailed sub-task tables already showed all items complete; the summary table was stale. This does not change the PENDING/DEFERRED counts above.

**Audit fixes applied (2026-07-21):** Two `except Exception:` catches in `services/prompt_service.py` were tightened to `except (AttributeError, TypeError):` (broad catch count: 20→18). One `except Exception: pass` in `services/history_service.py` line 292 was updated to log the error, matching the identical catch at line 348. These are code changes, not documentation corrections — they don't affect the PENDING/DEFERRED inventory.

---

## 20. AST-Based Splice Hardening (June 19, 2026)

The Settings tab's 15 `_create_<X>_group(self)` methods were spliced in from a temporary cascade source. Early regex-based attempts (iter4 + iter5) failed because regex matched inside docstrings/comments producing 30 duplicate methods. The hardened pipeline is AST-only.

### iter6 Pipeline (`_runs/iter6_hardened_splice.py`, ~270 lines)

| Phase | Action |
|-------|-------|
| **PHASE_1** | `ast.parse(k0k1w1.py)` -> find `SettingsTabMixin` -> walk for 15 `_create_*_group` FunctionDef nodes |
| **PHASE_1b** | Hygiene diagnostic: AST-walk each body for `use_before_define` + `never_defined`, filter builtins + imports + class-attrs |
| **PHASE_2** | `ast.unparse(node)` -> re-indent via `reindent_tree(text, body_indent, def_indent)` |
| **PHASE_3** | Apply replacements + deletions in descending line-start order |
| **PHASE_4** | `py_compile` + AST walk for 15 KNOWN_ANCHORS |

### Known Fixes

- **iter7 Fixer**: AST-only fixer for k0k1w1 hygiene bugs, solving multi-fix text-level offset drift via `FunctionDef.body` list mutation.
- **KNOWN_SUFFIXES**: Extended from 16 -> 36 entries.
- **addWidget kwargs fix**: 35 substitutions across 13 files for PyQt6 `QLayout.addWidget(widget, stretch=0, alignment=0)` TypeError.
- **Backup cleanup**: 6 settings_tab backup files moved to `_runs/archive_2026_06_19/`. MD5 hash-verified.

---

## 21. Offscreen Smoke Gate (Runtime Validation)

`py_compile` validates **syntax** only. It cannot catch runtime errors like `NameError` on use-before-define locals or `AttributeError` on missing fields.

Offscreen smoke procedure:

```python
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).parent))
from PyQt6.QtWidgets import QApplication, QWidget
app = QApplication.instance() or QApplication(sys.argv)
from tabs.settings_tab import SettingsTabMixin
class _Host(QWidget, SettingsTabMixin): pass
host = _Host()
for n, m in inspect.getmembers(host, predicate=inspect.ismethod):
    if n.startswith('_create_') and n.endswith('_group'):
        try:
            result = m()
            print(f'OK: {n}')
        except Exception as e:
            print(f'FAIL: {n}: {type(e).__name__}: {str(e)[:200]}')
```

Current state: `OFFSCREEN_SMOKE: ALL_15_OK` (15/15, achieved v0.17.1).

---

## 22. Resource Monitoring

The dashboard runs a 2-second QTimer (set up via setup_vram_monitor() in
app_lifecycle.py) that ticks three monitors every cycle (plus provider resources): update_vram(),
update_ram_monitor(), and update_disk_monitor(). Each monitor displays a
colour-coded status-bar label and fires health events and audit log entries
on threshold crossing.

### Thresholds

| Monitor | Warning | Critical | Source |
|---------|---------|----------|--------|
| VRAM | vram_warning_pct (default 88%) — CONFIG-driven since v0.22.5 (was hardcoded 0.88) | vram_critical_pct (default 95%) — added v0.22.9 | app_lifecycle.py::update_vram() |
| RAM | ram_warning_pct (default 80%) | ram_critical_pct (default 95%) | app_lifecycle.py::update_ram_monitor() |
| Disk | disk_warning_pct (default 85%) | disk_critical_pct (default 95%) | app_lifecycle.py::update_disk_monitor() |

> **Health-tab gauge parity (v0.22.9):** `tabs/health_tab.py::_update_health()` colors ALL four
> gauges from CONFIG thresholds — disk (pre-existing), RAM + VRAM (added v0.22.9, mirroring the
> disk branch): ≥ critical → red #EF4444, ≥ warning → amber #FBBF24, else the gauge default
> (RAM green #10B981, VRAM amber #F59E0B, disk purple #8B5CF6). The VRAM N/A branch
> (`vram_total == 0`) resets the bar to default amber so a stale warning color can't linger at 0%.
>
> **Settings UI editability (v0.22.11):** all 6 threshold pct keys are now user-editable
> QSpinBoxes in Settings → Window & Logging → Resource Monitoring (📏 Thresholds (%) section) —
> `ram_warning_pct` / `ram_critical_pct` / `disk_warning_pct` / `disk_critical_pct` /
> `vram_warning_pct` / `vram_critical_pct`. Live CONFIG write on change (monitors pick it up on
> the next 2s tick). Safe-coerced via `_coerce_pct()` in both construction and
> `_sync_ui_from_config()` (hand-edited string config can't crash QSpinBox).
>
> **Immediate persistence (v0.22.12):** each spin change now persists immediately via
> `_on_threshold_pct_changed()` — a faithful mirror of the notification-toggle pattern (guard →
> CONFIG write → try/except `save_settings()` → audit log with human-readable labels) — so an
> edited threshold survives an unexpected close without pressing SAVE ALL.
>
> **Warning ≤ critical clamp (v0.22.14):** `_on_threshold_pct_changed()` enforces the
> warning ≤ critical invariant before writing CONFIG — a `*_warning_pct` above its paired
> `*_critical_pct` is clamped down, a `*_critical_pct` below its paired warning is clamped up
> (`_THRESHOLD_PAIRS` map; guarded by `paired in CONFIG`). The edited spinbox is re-synced with
> signals blocked so the UI shows the enforced value. An inverted pair can never be persisted.
>
> **Sync-time `_syncing` guard (v0.22.15):** `_sync_ui_from_config()` restores widgets via
> setValue/setChecked/setCurrentIndex, which fires handlers that call `save_settings()` + audit
> on every sync. A method-wide `with self._sync_guard():` (new context manager, exception-safe
> + nesting-safe) wraps the whole restore body, and 8 handlers (`_on_threshold_pct_changed`,
> 4 notification toggles, `_on_always_on_top_toggle`, `_on_provider_changed`, `_on_theme_changed`)
> early-return when `self._syncing` is set — so a sync can't write disk or spam the audit log.
> Real user clicks run with `_syncing = False`, so no legit behavior is muted.
> **Convention:** new handlers wired to sync-restored widgets must add the same
> `if getattr(self, '_syncing', False): return` guard (the method-wide flag only
> mutes handlers that opt in).

### Health Events and Audit Logging

Each monitor emits two side effects on threshold crossing:

| Side effect | Method | Purpose |
|-------------|--------|--------|
| Health event | log_health_event(level, msg) | Structured event for Health tab |
| Audit log | log_to_audit(msg) | Entry in audit trail panel |

Both fire in strict order: audit first, then health event, matching the
pattern in update_ram_monitor().

### Rate-Limiting

Each monitor uses notification flags to fire events once per crossing:

| Flag | Set on | Cleared on |
|------|--------|-----------|
| _vram_high_notified | VRAM > 88% limit | VRAM <= 88% limit |
| _ram_warning_notified | RAM >= warning pct | RAM < warning pct |
| _ram_critical_notified | RAM >= critical pct | RAM < critical pct |
| _disk_warning_notified | Disk >= warning pct | Disk < warning pct |
| _disk_critical_notified | Disk >= critical pct | Disk < critical pct |

While flagged, repeated ticks do NOT re-fire. This prevents audit spam when
usage hovers near a boundary.

### Recovery Events

When usage drops below threshold, each monitor fires a recovery health
event (log_health_event with info level) and resets its flag. Recovery
does NOT emit an audit-log entry.

### Test Coverage

Key tests in tests/test_app_mixins.py::TestAppLifecycleMixin:

| Test | Verifies |
|------|---------|
| test_update_vram_health_event | Warning health event + audit at 88%, rate-limit, recovery |
| test_update_ram_monitor_warning | Warning health event + audit + notification |
| test_update_disk_monitor_warning | Warning health event + audit + notification |
| test_update_vram_recovery_health_event | Recovery info event after VRAM spike |
| test_update_ram_monitor_recovery_health_event | Recovery info event after RAM spike |
| test_update_disk_monitor_recovery_health_event | Recovery info event after disk spike |
| test_update_*_monitor_no_spam | Rate-limit guard |
| test_update_*_monitor_recovery | Flag reset on recovery |

---

## 23. Thread-Safety in LocalLLMProvider

`LocalLLMProvider` (``ai_base.py``) wraps a single ``llama_cpp.Llama`` model
instance (``self._model_instance``).  llama.cpp is **not thread-safe** for
concurrent inference — two simultaneous ``create_chat_completion()`` calls
on the same ``Llama`` object cause ``GGML_ASSERT`` failures (GPU scheduler
alloc conflicts) or silent data corruption.

This section documents the three known thread-safety gaps in the provider
and the one that has been fixed.

### 20.1 Fixed: ``_inference_lock`` for ``chat_completion`` / ``chat_completion_stream``

**Added:** July 14, 2026

**Problem:** ``memory_vault.store_memory()`` (line ~387) spawns a daemon
thread that calls ``extract_and_store_graph`` → ``extract_entities_llm``,
which accesses the **same** ``LocalLLMProvider`` instance concurrently with
the main thread's inference.  This caused:
```
GGML_ASSERT(!sched->is_alloc) failed
```

a non-deterministic crash that only appeared in multi-call pipelines (the
crash test passed because it exited before the daemon thread had time to
call the provider).

**Fix:** Added ``self._inference_lock = threading.Lock()`` to
``LocalLLMProvider.__init__()``.  Both ``chat_completion()`` and
``chat_completion_stream()`` acquire this lock for the **entire**
mode-load + inference + response-parse critical section and release it in
a ``finally`` block:

```python
# Pattern used in both methods:
self._inference_lock.acquire()
try:
    # ── Lazy-load model if needed ──
    # ── Build kwargs, KV cache hash ──
    # ── self._model_instance.create_chat_completion(...) ──
    # ── Parse response, record circuit-breaker success ──
    return result
except Exception as e:
    _circuit_breaker.record_failure()
    return self._handle_error(e, request_id)
finally:
    self._inference_lock.release()
```

**Generator note:** ``chat_completion_stream()`` is a generator that yields
mid-stream. The lock is acquired before the first ``yield`` and released in
the ``finally`` block. Python guarantees that generator ``finally`` runs on
all exit paths: normal ``return``, exception, and ``GeneratorExit`` (injected
by ``.close()`` when the caller stops iterating). So partial iteration or
abandoned generators do not leak the lock.

**Early-exit paths** (cancel-event check, circuit-breaker gate, no-model-
loaded guard) are intentionally **outside** the lock so cancellation does
not block on a held lock.

**Lock coverage diagram:**
```
chat_completion() / chat_completion_stream():
  [cancel_event check]          ← no lock
  [circuit_breaker check]       ← no lock
  lock.acquire()                ─┐
  try:                           │
    [lazy-load model]            │
    [inference call]             │  protected
    [response parse]             │  by lock
    [record_success/record_fail] │
    return / yield               │
  except Exception:              │
    yield error / return error   │
  finally:                       │
    lock.release()               ─┘
```

### 20.2 Fixed: ``swap_model()`` — Acquires ``_inference_lock`` with 30s Timeout

**Fixed:** July 14, 2026

**Problem:** ``swap_model()`` called ``unload_model()`` → ``del self._model_instance``
→ ``gc.collect()`` → ``torch.cuda.empty_cache()`` without holding
``_inference_lock``. If a background thread was mid-inference while a model
swap occurred, the background thread's ``_model_instance`` reference stayed
valid (Python GC won't delete an object with live references), but
``torch.cuda.empty_cache()`` freed the underlying GPU buffers, causing a
**use-after-free** crash on the next CUDA operation.

**Fix:** ``swap_model()`` now acquires ``_inference_lock`` with a **30-second
timeout** before unloading the current model:

```python
if not self._inference_lock.acquire(timeout=30):
    logger.warning("swap_model timed out waiting for inference lock")
    return {"ok": False, "error": "...still in progress",
            "old_model": old_basename, "new_model": new_model_basename}

try:
    self.unload_model()
    self._load_model(path)
    # ... success or rollback ...
finally:
    self._inference_lock.release()
```

**Timeout rationale:** 30 seconds balances responsiveness against completion
of in-flight inference. The default ``llm_max_tokens_cap`` (1024) means most
inferences complete within 5-15s. If the lock can't be acquired within 30s,
the caller receives an error dict and can retry.

**Early exits** (path validation, same-model check) are outside the lock,
so validation errors never block on inference.

**Edge cases covered:**
- Normal success → lock released in ``finally``
- Load failure + rollback → lock released in ``finally``
- Exception during load → lock released in ``finally``
- Timeout on acquire → no lock held, returns immediately
- ``KeyboardInterrupt`` during ``acquire()`` → lock never held, no leak

### 20.3 Fixed: ``health_check()`` — ``_inference_lock`` + Rich Diagnostics + Lazy-Load

**Fixed:** July 15, 2026 (lock consolidation) + July 29, 2026 (rich diagnostics restoration)

**Problem:** ``health_check()`` was reduced to a minimal benchmark-only method that:
(1) used its own ``_health_lock`` (separate from ``_inference_lock``), allowing races with ``chat_completion()``;
(2) had no lazy-load guard, so the first health check always reported "No model loaded";
(3) returned only ``{ok, latency_ms, provider}`` — no model_name, n_ctx, n_gpu_layers, or VRAM diagnostics.

**Fix (July 15):** Removed the separate ``_health_lock`` entirely. The benchmark now acquires ``_inference_lock`` with ``blocking=False``.

**Fix (July 29):** Restored the full diagnostic method with:
- **Lazy-load guard:** ``if self._model_instance is None and self.model_file:`` triggers model load before diagnostics (mirrors ``chat_completion()`` pattern)
- **Rich diagnostics:** Returns ``ok, provider, model_loaded, model_name, model_path, n_ctx (resolved via ``_resolve_n_ctx()``), n_gpu_layers, vram_used_mb, latency_ms``
- **VRAM query:** Best-effort ``nvidia-smi`` query (non-fatal on systems without NVIDIA GPUs)
- **``_inference_lock`` non-blocking benchmark:** 1-token latency test skips gracefully when lock is held
- **Consistent dict shape:** All keys always present (n_ctx=0, n_gpu_layers=0, latency_ms=0 when model not loaded)

The method now returns accurate diagnostics on first call without requiring a prior inference to lazy-load the model.

**Verification:** All 5 ``TestHealthCheck`` tests pass (8.8s), smoke test Stage 4 shows full diagnostics (model_loaded=True, model_name=Ministral, n_ctx=4096, latency_ms=18.5s).

### 20.4 Not Fixed: ``max_tokens`` Default — VRAM Exhaustion on Multi-Turn

**Root cause:** ``process_input`` passes ``max_tokens=-1`` to
``chat_completion()``, which resolves to ``n_ctx // 2`` — up to **16,384
** tokens for a 32K-context model. Simple prompts generate for 60-157
seconds, exhausting VRAM after 2-3 conversation turns.

**Mitigation (Jul 14, 2026):** Added ``llm_max_tokens_cap`` CONFIG key
(default 1024). The resolved max_tokens is now
``min(n_ctx // 2, max_tokens_cap)``. A value ≤ 0 disables the cap.

### 20.5 Summary

| Gap | Lock | Fixed? | Root Cause |
|-----|------|:------:|------------|
| Daemon thread races with inference | ``_inference_lock`` | ✅ **Yes** | ``store_memory()`` spawns thread → ``extract_entities_llm`` → same provider |
| ``swap_model()`` frees GPU during inference | ``_inference_lock`` (30s timeout) | ✅ **Yes** | ``unload_model()`` → ``torch.cuda.empty_cache()`` while lock holder uses GPU |
| ``health_check()`` benchmark races with inference | ``_inference_lock`` (non-blocking) | ✅ **Yes** | Uses ``_inference_lock`` with ``blocking=False`` to skip benchmark if busy |
| Multi-turn VRAM exhaustion | N/A | ✅ **Mitigated** | ``max_tokens=-1`` → 16K → VRAM exhaustion; capped at 1024 via CONFIG |


### 20.6 Fixed: memory_vault.py Connection Pool + Bare-Except (July 30, 2026)

**Fixed:** July 30, 2026

**Problem 1 Connection pool isolation:** _get_conn() uses a module-level _connection_pool list to cache SQLite connections. When tests override DB_PATH to a temp file, pooled connections pointing to the old database path are reused causing table-not-found failures across 9 tests. This was masked until the ensure_tables_exist / _return_conn syntax fixes made the module importable again on Python 3.14.

**Fix 1:** Added _clear_connection_pool() helper that closes all pooled connections under _pool_lock. Called in setUp and tearDown of all 5 test classes that override DB_PATH.

**Problem 2 Bare except:** _return_conn() used bare except: in the conn.rollback() try block, silently swallowing KeyboardInterrupt and SystemExit (documented anti-pattern, KNOWLEDGE.md 13).

**Fix 2:** Changed except: to except Exception:. Behavior identical for all practical error paths.

**Verification:** All 51 test_memory_vault.py tests pass. Broader suite: 223 passed, 32 skipped, 0 failed.

**Follow-up (2026-07-31, v0.21.9): root-cause hardening + e2e coverage.** The July 30 test-side fix
covered the 5 classes in `test_memory_vault.py` + `test_sprint13_writer.py`, but `test_call_chain_e2e.py`
still patched `memory_vault.DB_PATH` without draining the pool — its 4th test failed in serial mode
(`-n 0`, 1 failed / 3 passed) with `sqlite3.OperationalError: no such table: core_memories` because
`_get_conn()` popped a stale pooled connection bound to a prior test's (deleted) temp vault, so
`ensure_tables_exist()` created tables on the wrong file. Two-part fix:

1. **Path-aware `_get_conn()` (root cause):** new `_conn_is_for_path(conn, path)` helper reads the
   connection's actual file via `PRAGMA database_list` at pop time (sqlite3.Connection has no
   writable `__dict__`, so the path cannot be tagged at creation). `_get_conn()` now pops connections
   in a loop, closing any whose file does not match the current `DB_PATH` (`normcase`-normalized) —
   the ENTIRE stale-connection bug class is eliminated, so future test files that patch `DB_PATH`
   no longer need to remember `_clear_connection_pool()`.
2. **Defense-in-depth:** `test_call_chain_e2e.py` now calls `_clear_connection_pool()` in both `setUp`
   (after `DB_PATH` patch) and `tearDown` (before `rmtree`, releasing Windows file locks) — mirroring
   the `test_memory_vault.py` precedent.

**Verification (v0.21.9):** `test_call_chain_e2e.py -n 0` → 20 passed (was 1 failed in serial);
`test_memory_vault.py` + `test_sprint13_writer.py` serial → all pass; new
`tests/test_conn_pool_path_guard.py` (5 REGRESSION GUARD tests) pins the
`_conn_is_for_path` contract + the stale-conn discard (pools a conn to DB A,
repoints `DB_PATH` to B, asserts `_get_conn()` returns a B-bound conn).

## 24. Vault Maintenance Toolkit (`scripts/vault_maintenance.py`, Sprint 19.8.2)

> **What it is:** the single opt-in tool for vault health, hygiene, repair, and
> compaction. Every mode is explicit; nothing destructive runs implicitly (v1.3.1
> made `--execute`/`--dry-run` mode-scoped — see [Flag scoping](#flag-scoping-v131)).
> Written during the Sprint 19.8.2 recovery (see
> [§25 The Vault Recovery Saga](#25-the-vault-recovery-saga-742941--12-2026-09-22))
> and battle-tested on the production vault through migration, purge, and dedupe.

### Command map

```
python scripts/vault_maintenance.py                     # health report only
python scripts/vault_maintenance.py --dry-run           # plan for the selected mode
python scripts/vault_maintenance.py --execute           # journal retention (ALONE = retention)
python scripts/vault_maintenance.py --vacuum            # reclaim dead pages (run app-closed)
python scripts/vault_maintenance.py --reindex-fts       # rebuild FTS5 indexes
python scripts/vault_maintenance.py --migrate-embeddings [--limit N]   # JSON -> float16
python scripts/vault_maintenance.py --purge-test-rows [--dry-run|--execute] [--purge-all]
python scripts/vault_maintenance.py --dedupe [--dry-run|--execute]
python scripts/vault_maintenance.py --census            # hygiene census (cron-able, exit 0/1)
```

**Golden rule: plan → act → verify.** `--dry-run` first for any mode you haven't
run before; the plans print per-signature/per-group detail so you can validate
against reality before destructive modes touch a row.

### Flag scoping (v1.3.1)

`--execute` and `--dry-run` apply to the SELECTED mode only. Journal retention
runs ONLY when `--execute` is passed alone; combined invocations print an
explicit NOTE ("journal retention was NOT run"). Before v1.3.1, `--execute`
fired retention no matter which mode was selected — observed live during the
production purge (retention path ran uninvited, deleting 0 rows).

### Modes

| Mode | What it does | Safety rails |
|---|---|---|
| report (default) | table counts, size, embedding-format split, oldest journal entry | read-only |
| `--dry-run` | plan for the selected mode (retention / purge / dedupe) | read-only |
| `--execute` (alone) | journal retention: deletes episodic rows older than `--retention-days` (default 90); importance >= 9 exempt; `--prune-core` also wipes core (DESTRUCTIVE) | prints counts first; FTS rebuilt after |
| `--vacuum` | VACUUM; reclaims dead pages | needs free disk ~= DB size; exclusive-lock preflight recommended; run app-closed |
| `--reindex-fts` | rebuild both FTS5 indexes from source tables | safe |
| `--migrate-embeddings` | legacy JSON-text embeddings -> float16 binary (~8.4 KB -> 768 B/row, 91% smaller). Keyset-paginated on `id`, verify-before-write (round-trip decoded + compared in memory), idempotent, reuses the vault's own codec so the format cannot drift | `--limit N` to stage; failed rows kept as legacy and counted, never mutated |
| `--purge-test-rows` | deletes core_memories rows matching test-pollution signatures (13 substring + 3 regex families, matched against `[EPISODIC:]`-stripped lowercased content). July 2026 LLM summaries are the `july_summary` protected class. | per-signature dry-run plan; scratch smoke `_scratch/smoke_purge_test_rows.py` |
| `--dedupe` | collapses duplicate-content rows, keeping the EARLIEST (`timestamp ASC, id ASC` tiebreak — NOT insertion order; REGRESSION GUARD on `_dedupe_targets`). Zero information loss. | per-group dry-run plan; batched deletes + FTS rebuild; idempotent; smoke `_scratch/smoke_dedupe.py` |
| `--census` | READ-ONLY hygiene census on a dedicated `mode=ro` connection (cron-safe): format split, per-dims histogram, pollution scan, **24h bleed tripwire**, dead pages, `PRAGMA quick_check`. | **exit 0 clean / 1 on pollution, undecodable embeddings, active bleed, or integrity failure** — gate-able. WARNs never fail. Wired as STAGE 3 of the nightly DR cron (`scripts/cron_nightly_dr_smoke.bat`); smoke `_scratch/smoke_census.py` |

### Operating procedures

**Reclaiming space after deletes:** freed pages stay in the file until `--vacuum`
(SQLite keeps them in-place). VACUUM needs free disk ~= DB size and an exclusive
lock — preflight: `BEGIN EXCLUSIVE` probe + `df -h`; run with the app CLOSED.

**Staged migration pattern** (used for the 622k-row conversion): run
`--migrate-embeddings --limit 50000`, verify row-balance (`binary + legacy + null ==
total`), repeat without `--limit` to finish, then `--vacuum`. Re-run
`_scratch/estimate_vault_size.py` (read-only) between chunks for a live forecast.

**Recovery backups (pruned 2026-09-22):** during the saga, file-level backups
were taken before each destructive step (`Backups/vault_pre_purge_20260922/`,
748 MB; `Backups/vault_pre_dedupe_20260922/`, 101 MB) — restore = stop app,
copy the .db back, start app. Both were deleted after the final verification
battery (census CLEAN, 12 rows verified, quick_check ok) confirmed the clean
state; the pollution-era states they preserved were 100% test data. Future
destructive runs should re-create the same pattern: backup → lock preflight →
act → verify → prune only after evidence.

### The recovery saga

The complete narrative — root cause, isolation fix, migration, benchmark-driven
re-optimization, purge, and dedupe — lives in its own section:
**[§25 The Vault Recovery Saga](#25-the-vault-recovery-saga-742941--12-2026-09-22)**.
The short version: test pollution (742,941 rows, opt-in isolation + a
`DB_PATH`-resetting reload) was root-caused, the leak class killed with
default-deny isolation, embeddings migrated to float16, the search engine
re-optimized to a normalized one-pass matrix product, and the table reduced
to its 12 real rows via signature-based purge + earliest-keeper dedupe.

### Known quirks

- `-p no:xdist` fails because pytest.ini addopts injects `-n`; don't pass it.
- Git Bash mangles `schtasks /switch` into paths — prefix `MSYS_NO_PATHCONV=1`.
- The nightly task `KokertechAI Nightly DR Smoke` was NOT registered in Task
  Scheduler as of 2026-09-22 (installer exists, was never run as admin). Run
  `scripts/install_nightly_dr_smoke_task.ps1` as admin to activate; until then the
  03:00 DR smoke + census do not fire.
- Ruff noqa placement: multi-line statements — put the pragma on the line ruff
  flags (restructure to a single line when in doubt); f-prefix only on string
  fragments that actually contain placeholders (F541).

## 25. The Vault Recovery Saga (742,941 → 12, 2026-09-22)

> The complete account of how production `core_memories` ended up holding
> 742,941 rows of test data, how the search engine hiding underneath it was
> rescued, and how the table ended at exactly 12 rows. Written so the next
> agent understands why the numbers are what they are — and why the guards
> that exist now must never be removed.

### Act 1 — The disease (discovery, 2026-09-21)

A routine full-project audit asked why `core_memories` held 742,941 rows while
`episodic_journal` held only 4,789 — an inverted ratio for a "curated" table.
Forensic SQL (read-only probes kept under `_scratch/`) answered it:

- **742,941 rows, 237 distinct contents.** 705k rows carried `[MOCK]` /
  `test query` / `Msg N` signatures; the remaining 37,892 "real-looking" rows
  were smoke tests and fixtures (one literally said *"Smoke test memory entry
  -- safe to delete"*). **100% test pollution.**
- The burst window was **Jul 27 – Aug 3 2026** — the same era that produced
  the legacy JSON embedding blobs.
- `episodic_journal` (4,789 rows, Jun 26 →) was the **real** memory record.
  The ratio was never a design bug.
- **The class was still live:** rows written 2026-09-21 (during that day's
  own test batches) proved the leak ran in the present, not just the past.

**Root cause (two-layer):** vault isolation was **opt-in** — the
`temp_vault_db` fixture had to be requested, and any test file that forgot it
wrote straight into `kokertech_vault.db`. Worse, conftest's
`_reset_memory_vault()` ran `importlib.reload()`, which **reset `DB_PATH` to
production at every cross-file teardown** — silently killing any per-file
redirect. The trap was structural, which is why it kept recurring.

### Act 2 — The isolation fix (default-deny)

`conftest._default_deny_vault_isolation` (autouse) flipped the model: **every
test gets a per-test temp vault unless explicitly marked otherwise.**

- Escape hatch: `@pytest.mark.real_vault` (registered in pytest.ini, zero
  users — exists so a future need is explicit and greppable, never accidental).
- Debug override: `KOKERTECH_TEST_REAL_VAULT=1` (used once, to prove a
  failing test was pre-existing rather than isolation-induced).
- `_reset_memory_vault()` now **PRESERVES `DB_PATH` across reload** — the
  leak mechanism itself is dead.
- Locked by `tests/test_vault_isolation_guard.py` (4 REGRESSION GUARDs),
  one of which exposed a real latent bug: `if embedding else None` raises
  `ValueError` on numpy arrays → fixed to `is not None` in `store_memory`
  and `store_entity`.

### Act 3 — The migration (format, 622,128 rows)

The 5+ GB size had two causes: unbounded growth, and 2.0.x storing embeddings
as **JSON text (~8.4 KB/row at 384 dims)** instead of float16 binary (768 B).
The audit's initial "~2.8 KB, ~73% saving" estimate was **retracted** after
measurement — real saving is **~91%**; there was never a second (higher-dim)
embedding generation, just one model's output bloat by formatting.

`--migrate-embeddings` (v1.1.0) converted **622,128 rows in staged chunks
(50k / 250k / 200k / 122,128), zero failures** — verify-before-write (each
blob round-trips through decode + `allclose` in memory before any UPDATE),
keyset pagination on `id`, idempotent, reusing the vault's own codec so the
format cannot drift. VACUUM #1: 5.20 → 4.82 GB; after full conversion +
VACUUM #2: **747.3 MB**.

### Act 4 — The benchmark that told the truth (search engine)

A measured benchmark (`_scratch/bench_vector_search.py`, read-only, synthetic
query vectors so model latency is excluded) showed the two-phase engine at
**589 ms warm — 10x slower than BLAS math said it should be**. Cause: the
per-query `np.linalg.norm(matrix, axis=1)` re-read the entire 911 MB cached
matrix on EVERY query. Fix: rows are **L2-normalized in place at rebuild**, so
each query is one matrix-vector pass (cosine = dot with the normalized query).

Result, measured on the production vault: warm **589 → 53.7 ms (measured 275x
over the legacy per-row estimate)**, full hybrid 596 → 59 ms. The audit's
"100–500x" claim became measured truth only after this second fix. Alongside
the engine work, the **dims-drift guard** landed: `_check_embedding_dims()`
walls at write time, and the cache rebuild logs a census warning whenever
nonconforming-dim rows are excluded (the 23 × 768-dim rows from a July one-off
model experiment were the first silent instance).

### Act 5 — The purge (740,166 rows)

`--purge-test-rows` (v1.2.0/1.2.1): 13 substring + 3 regex signatures matched
against `[EPISODIC:]`-stripped content, an exact-match class for
`[EPISODIC: OK]`, and the July 2026 daily summaries as a **protected class**
(kept unless `--purge-all`; protection checked FIRST). Survivor census
(`_scratch/survivor_census.py`) calibrated 8 extra signatures against evidence
and identified the only real rows in the table: **4 one-off `interaction`
conversations with Jacques** (human typos included), spared by a
pair-anchored greeting signature.

Execution (2026-09-22, Jacques-triggered): backup
(`Backups/vault_pre_purge_20260922/`, 748 MB) → exclusive-lock preflight →
delete **740,166** → 2,775 remain → FTS rebuild → VACUUM **747.3 → 100.1 MB**.

### Act 6 — The dedupe (2,763 rows → 12)

Post-purge census WARNed on 2,763 duplicate-content rows. `--dedupe`
(v1.4.0) collapses duplicates keeping the **earliest** (`timestamp ASC, id
ASC` tiebreak — NOT insertion order; REGRESSION GUARD on `_dedupe_targets`).
Every deleted row was byte-identical to its keeper: zero information loss.

Execution (2026-09-22): backup (`Backups/vault_pre_dedupe_20260922/`,
101 MB) → delete **2,763 across 5 groups** → **exactly 12 rows** (4 real
interactions + 8 earliest summaries) → VACUUM **96.7 MB** → census CLEAN
(1 WARN: the 7 known 768-dim stragglers).

### Final state and the standing guards

| | before | after |
|---|---|---|
| core_memories | 742,941 rows (100% test data) | **12 rows (all real)** |
| vault file | 5.20 GB | **96.7 MB** |
| warm semantic search | ~14.8 s (legacy est.) | **3.19 ms** |
| matrix cache RAM | 911 MB | ~0 MB |

**The guards that must never be removed:** default-deny vault isolation
(conftest, locked by `test_vault_isolation_guard.py`); the dims-drift warnings
(`_check_embedding_dims` + cache-rebuild census); the `--census` 24h bleed
tripwire (STAGE 3 of the nightly DR cron — activate via
`scripts/install_nightly_dr_smoke_task.ps1`, **not yet registered as of
2026-09-22**); and the plan-first contract on every destructive mode of
`scripts/vault_maintenance.py` (§24).
