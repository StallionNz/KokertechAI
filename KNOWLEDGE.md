# KokertechAI — Knowledge Base

> The canonical internal reference for how KokertechAI works: architecture,
> subsystem contracts, conventions, and operational gotchas.
> Last updated: October 9, 2026

Everything below is grounded in the code in this repository. Where a fact is
easy to re-verify, the module or symbol to inspect is named so the claim stays
drift-resistant.

---

## Contents

| § | Section |
|---|---------|
| §1 | [Overview](#1-overview) |
| §2 | [Architecture](#2-architecture) |
| §3 | [Interfaces](#3-interfaces) |
| §4 | [Configuration](#4-configuration) |
| §5 | [AI Provider](#5-ai-provider) |
| §6 | [Memory Vault](#6-memory-vault) |
| §7 | [Plugin System](#7-plugin-system) |
| §8 | [Sub-Agent Personas](#8-sub-agent-personas) |
| §9 | [Workflow Engine](#9-workflow-engine) |
| §10 | [UI Tabs](#10-ui-tabs) |
| §11 | [Services Layer](#11-services-layer) |
| §12 | [Testing](#12-testing) |
| §13 | [Quality Gates](#13-quality-gates) |
| §14 | [Conventions and Gotchas](#14-conventions-and-gotchas) |

---

## 1. Overview

KokertechAI is an offline-first, local-only AI desktop automation system for
Windows. A single in-process language model is given tools, persistent memory,
and multi-agent reasoning, then exposed through three interfaces.

| Attribute | Value |
|-----------|-------|
| Language / GUI | Python 3.10+ / PyQt6 |
| Inference | `llama-cpp-python` (GGUF), in-process — `ai_base.LocalLLMProvider` |
| Interfaces | PyQt6 dashboard, terminal CLI, Flask / FastAPI web |
| Persistence | SQLite (`kokertech_vault.db`) + `app_settings.json` |
| Plugins | 28 native plugins in `plugins/` |
| UI tabs | 24 tab modules in `tabs/` |
| Services | 16 modules in `services/` |
| Sub-agents | 7 personas in `sub_agents.py` |
| Tests | 153 test files under `tests/` (0 at repo root) |
| Quality gates | 12 scripts driven by `scripts/run_all_gates.py` |
| License | GNU AGPL-3.0 |

**Design principles.** Local-first and user-owned: inference never leaves the
machine, and there is no telemetry. The network is used only when a tool the user
invokes requires it (web search, page fetch, webhook). Every subsystem is
fail-soft — a missing optional dependency degrades a feature rather than
crashing the app.

**Workspace root.** `config.py` fixes `WORKSPACE_DIR` to `C:\KokertechAI` and
derives every data path from it.

---

## 2. Architecture

Three layers, top to bottom:

```
INTERFACE LAYER
  PyQt6 GUI (app_core + mixins) | Terminal CLI (kokertech_terminal, cli/)
  Flask (kokerpro_web) | FastAPI v2 (kokerpro_fastapi)
        |
APPLICATION LAYER
  config · plugin_registry · ai_base · memory_vault
  kokertechController (orchestration facade over services/)
  sub_agents · workflow_engine · services/ · tabs/
        |
DATA LAYER
  kokertech_vault.db (SQLite) · app_settings.json
```

### Key modules

| Module | Role |
|--------|------|
| `main.py` | Desktop entry point — builds `QApplication`, shows `KokertechDashboard` |
| `app_core.py` | `KokertechDashboard` — composes the app + tab mixins |
| `app_ui.py` / `app_hotkeys.py` / `app_plugins.py` / `app_lifecycle.py` | UI, hotkeys, plugin manager, monitoring/shutdown mixins |
| `kokertechController.py` | AI orchestration facade; delegates to six services |
| `ai_base.py` | `LocalLLMProvider`, circuit breaker, response cache, GBNF grammars |
| `memory_vault.py` | 3-tier memory, hybrid search, GraphRAG, session lifecycle |
| `plugin_registry.py` | Plugin loading, `PluginSchema` validation, tool exposure |
| `sub_agents.py` | Persona prompts/configs, intent router |
| `workflow_engine.py` | YAML pipeline execution |
| `services/` | DI container + 16 extracted services |
| `tabs/` | 24 PyQt6 tab modules |

**Controller as a facade.** `kokertechController` was decomposed so that
history, context, prompt construction, provider access, settings caching, and
streaming each live in their own service. The controller keeps the public
surface and wires them together.

---

## 3. Interfaces

**Desktop GUI.** `python main.py` (or `app_core.py`) launches the PyQt6
dashboard. `KokertechDashboard` is a `QMainWindow` that mixes in the app mixins
and every tab mixin, so adding a tab means adding a mixin plus a `tabs/` module.

**Terminal CLI.** `python kokertech_terminal.py` opens a rich interactive shell
with streaming output, readline history, session persistence, and around fifty
slash commands handled by `cli/commands.py`.

**Web.** `kokerpro_web.py` is the running Flask interface; `kokerpro_fastapi.py`
is the Architecture-V2 FastAPI server.

**Launcher.** `kokertechai.cmd` dispatches to CLI (default), `gui`, `web`, or the
CLI test subset.

---

## 4. Configuration

`config.py` merges user settings with defaults into the module-level `CONFIG`
dict and calls `_load_settings()` on a short mtime+TTL cache so repeated reads
do not hammer the disk.

`app_settings.json` (145 keys by default) is the on-disk store, surfaced through
the Settings tab. Groups of note:

| Group | Examples |
|-------|----------|
| Resource limits | `vram_limit_mb`, `ram_warning_pct`, `disk_critical_pct` |
| Inference | `active_provider`, model name, `freeform_mode`, `structured_format` |
| Tool use | `tool_use_per_tool_timeout`, `tool_require_hitl_for_destructive`, `tool_sandbox_*` |
| Memory | `hybrid_search_enabled`, `hybrid_search_alpha`, `batch_embedding_size` |
| Vault | `vault_wal_autocheckpoint_threshold_mb`, `vault_fts_self_heal` |

**Secrets.** `.env` is loaded at startup by `_load_dotenv`; existing OS
environment variables always win. API keys migrate to the Windows Credential
Manager, and the keyring takes priority over the file afterwards.

**Personas.** `personas.json` seeds the persona dropdown, the `/persona` CLI
command, and the A/B comparison dropdowns.

---

## 5. AI Provider

Inference is a single local provider. `ai_base.get_provider()` returns a cached
`LocalLLMProvider`; there are no external HTTP inference providers.

- **Loading** — `LocalLLMProvider` builds a `llama_cpp.Llama` instance for the
  configured GGUF model. Optional imports are guarded so the module imports even
  when `llama-cpp-python` is absent.
- **Caching** — `cached_chat_completion` wraps calls in an opt-in LRU response
  cache with a TTL, keyed by a hash of messages + model.
- **Resilience** — a `CircuitBreaker` (accessed via `get_circuit_breaker`) trips
  after repeated failures; `retry_with_backoff` adds jittered exponential
  backoff between attempts.
- **Grammars** — `get_grammar(format_type)` loads GBNF grammars for structured
  output, with an mtime-keyed cache and hot reload.
- **Tool schema bridge** — `convert_plugin_schema_to_openai_tool` maps a plugin
  `SCHEMA` into OpenAI-style function-calling format.
- **Vision** — `_is_vision_model` / `is_projector_model` gate multimodal paths.

---

## 6. Memory Vault

`memory_vault.py` is the persistence backbone, backed by SQLite at
`kokertech_vault.db`.

**Three tiers**

| Tier | Storage | Purpose |
|------|---------|---------|
| 1 — Short-term | in-memory history in the controller | live conversation context |
| 2 — Episodic | `episodic_journal` table | session-scoped summaries |
| 3 — Long-term | `core_memories` table | durable facts with embeddings + importance |

**Hybrid search.** Vector similarity and FTS5 BM25 results are fused with
reciprocal rank fusion; the balance is the `hybrid_search_alpha` setting.

**Knowledge graph (GraphRAG).** Entities and relationships are stored alongside
memories and can be linked automatically (`auto_linker`) and explored
(`vault_explorer`, `visualize_graph`, `tabs/neural_graph_tab.py`).

**Lifecycle.** The vault manages session start/end, daily summary generation
(via the AI provider), persona A/B vote tracking, WAL checkpointing, and FTS
self-healing.

---

## 7. Plugin System

`plugin_registry.py` loads every module in `plugins/` via `importlib` at startup.

**Plugin contract**

- Exports `COMMAND_NAME` and `execute(intent_json)`; `SCHEMA` and
  `PLUGIN_METADATA` are optional.
- Modules whose name starts with `_` are skipped (private/utility helpers).
- If `SCHEMA` is present it must be a dict with string keys and must survive
  `json.dumps(raw, default=str)`.

**Progressive disclosure.** The model receives a compact category summary, not
every schema. It requests the schemas it needs with `<<LOAD_TOOLS:category>>`,
and the registry injects them into the next turn (`MAX_SCHEMAS_PER_LOAD` caps a
single load).

**Validation is fail-soft.** A malformed schema raises a `SchemaError` that is
caught and reported; it never prevents the registry from finishing its load.

**The 28 plugins** cover: screen capture, intent classification, folder/file
creation, task delegation, script execution, audio + image text extraction, web
fetch and search, DOCX/Excel/PDF reading, document indexing, knowledge-graph
linking, bias and growth logging, MCP client, file listing/reading/renaming,
research, semantic search, memory storage, system status, tool use, webhooks, and
file writes.

---

## 8. Sub-Agent Personas

`sub_agents.py` defines seven personas, each with its own temperature and token
budget, in `PERSONA_CONFIGS` (validated against `VALID_PERSONAS`).

| Persona | Temperature | Max tokens | Focus |
|---------|-------------|-----------|-------|
| Researcher | 0.2 | 2000 | Search, analyse, synthesise |
| Coder | 0.1 | 3000 | Clean, tested Python |
| Auditor | 0.4 | 2500 | Bug / security / performance review |
| Planner | 0.2 | 2000 | Task breakdown with dependencies |
| ToolUser | 0.3 | 2000 | Invoke plugins and system tools |
| Orchestrator | 0.3 | 2000 | Classify intent, delegate |
| Synthesizer | 0.3 | 2000 | Integrate findings into a coherent whole |

**Intent router.** `_detect_intent(text)` buckets a message with keyword
heuristics (code / research / audit / plan / synthesise). `router(text, ...)` runs
the matching persona and normally hands the raw output to a Synthesizer; the
`synthesize` bucket returns the Orchestrator output directly.

**Routing scope.** Only the main chat path routes. The multi-model and
multi-persona comparisons deliberately do not, because routing would change the
provider call sequence under comparison.

---

## 9. Workflow Engine

`workflow_engine.py` runs YAML pipelines.

```yaml
name: My Workflow
parallel: false
steps:
  - name: research
    action: search_web
    params: { query: "AI trends" }
  - name: write
    action: sub_agent
    params: { persona: Coder, task: "write a script" }
    depends_on: [research]
    on_failure: stop   # stop | skip | continue
```

| Action | Handler |
|--------|---------|
| `plugin_command` | `PluginRegistry.execute_command()` |
| `sub_agent` | `SubAgent.execute()` |
| `crew` | `run_crew_team()` (`crew_integration.py`) |
| `sandbox` | `execute_code()` (`docker_sandbox.py`) |

---

## 10. UI Tabs

24 tab modules live in `tabs/` (one `*_tab.py` per tab). The dashboard mixes each
in through `tabs/__init__.py`.

Workspace, Neural Graph, Settings, Cognitive Agency, RLHF Trainer, Doc Pipeline,
Workflow, Computer Use, Health, Git Tracker, Chat History, RAG, Session Browser,
Theme Builder, Web View, Session Log, Memory Browser, Scheduled Actions, Plugin
Store, Persona A/B Testing, MCP Client, Progress, Session Journal, Tool Audit.

**Threading.** Shared state is guarded by a per-instance lock. Worker threads
must never touch widgets directly — they emit Qt signals and the main thread
updates the UI. Worker cleanup hooks connect to `worker.finished` (the
Qt-guaranteed end-of-thread signal), never to data signals such as
`reply_signal`, so cleanup always runs exactly once.

**Error handling.** Tab code catches narrow exceptions and returns a graceful
fallback rather than letting a UI callback raise.

---

## 11. Services Layer

`services/` is a small dependency-injection container plus focused services,
exposed through `services.get_services()`.

| Service | Responsibility |
|---------|----------------|
| `history_service` | conversation history and trimming |
| `context_service` | context assembly and summarisation |
| `prompt_service` | prompt construction / parsing |
| `provider_service` | provider access |
| `provider_stream_service` | streaming chat-completion execution |
| `settings_cache_service` | mtime-keyed JSON + profile/model loaders |
| `code_intelligence` + `code_intelligence_factory` | code search / review core |
| `tool_execution_service` + `tool_use_agent` | tool dispatch and the tool-use agent |
| `context_service`, `skill_service`, `structured_output_service` | skill policy injection, structured output |
| `hive_service`, `speculative_service`, `sensory_service` | optional orchestration accelerators |

**Registry contract.** `services/registry.py` defines `ServiceRegistry` and
`register_canonical_services()`. A destructive `reset(clear_factories=True)`
wipes the canonical factories, so anything that resets the registry must re-run
`register_canonical_services()` afterwards or later construction fails.

---

## 12. Testing

The suite runs with `pytest` and is configured in `pytest.ini`.

```bash
python -m pytest                          # target the tests/ tree
python scripts/run_parallel_chunked.py    # chunked runner for the full suite
```

- **Layout** — 153 test files under `tests/` (nothing at repo root). Nested
  helper directories exist for tab and pipeline fixtures.
- **Parallelism** — `-n auto --dist loadscope` groups tests by file so
  module-scoped fixtures stay in one worker.
- **Strict warnings** — `-W error::RuntimeWarning` turns a whole class of
  accidental non-string arguments to file APIs into hard failures.
- **Isolation** — a shared fixture resets module-level state (provider cache,
  plugin registry, memory vault, service registry) between files, which is what
  lets the suite run in one process.

When writing a new test, prefer explicit setup over relying on state left by
another file, and avoid importing the root `conftest` by module name — load it by
absolute path if you must, because a `conftest` name collision resolves to the
wrong file under pytest's import cache.

---

## 13. Quality Gates

`scripts/run_all_gates.py` runs twelve fast pre-flight checks and reports a
single combined result. Each gate script exits **0** (clean), **1** (violation),
or **3** (`SKIP` — the gate's only input doc is absent, as in a fresh clone).

| # | Gate | Checks |
|---|------|--------|
| 1 | Markdown Section 11 | freebuff-instructions render invariants |
| 2 | Freebuff Table Render | per-section markdown table pipe consistency |
| 3 | Test Count Regression | test-count drift vs. the stored baseline |
| 4 | Knowledge Anchor Links | every inline anchor resolves to a real heading |
| 5 | Knowledge Doc Hygiene | no stale line cites, no over-wide table cells |
| 6 | Ruff Baseline Lint | no ruff violations beyond `scripts/lint_baseline.json` |
| 7 | Silent-Catch Drift | no new broad `except: pass` sites |
| 8 | Living Doc Drift | retired myths absent; SSOT pointers + contract versions in sync |
| 9 | Corruption Recurrence | root `.py` null-byte scan + quarantine baseline |
| 10 | Continuity Consistency | session-continuity file consistency |
| 11 | Singleton Reset Pattern | singleton reset helpers + conftest wire-up |
| 12 | Config Mode Pins | CONFIG patch sites pin their modes |

**Skip contract.** Gates 1, 2, 9, and 10 skip (exit 3) when their internal input
doc is absent, so a fresh clone of the public repo passes the suite instead of
failing on files that are intentionally not published. All gates stay strict
whenever their input is present. The runner renders skips distinctly and reports
`skipped_count` in its JSON output.

**When you edit a living doc.** Keep table cells short (well under 240 chars) and
describe code by symbol name rather than line number; the doc-drift and
doc-hygiene gates actively reject frozen counts, retired claims, and line cites.

---

## 14. Conventions and Gotchas

**Naming.** `snake_case` for functions and variables, `CamelCase` for classes,
`UPPER_CASE` for module constants, `_` prefix for private helpers,
`COMMAND_NAME` for plugin command constants.

**Module-level singletons.** Any new module-level singleton must ship with a
`_reset_X_for_tests()` helper and a conftest wire-up that calls it, or it will
leak state across test files.

**Optional dependencies.** Guard `import` of heavy or optional packages
(Docker, CrewAI, Tesseract, Piper, vision stacks) and degrade gracefully.

**Fail-soft validation.** Prefer reporting a bad schema or config value over
raising through startup.

**Threading.** Never update widgets off the main thread; marshal work back with
signals. Connect cleanup to lifecycle signals (`finished`), not data signals.

**Windows assumptions.** The app resolves its data root to `C:\KokertechAI`, uses
the Windows Credential Manager for secrets, and drives the desktop with
automation libraries — treat it as Windows-only.

**Keep counts honest.** File/module/test counts in this document are maintained
by hand and drift as the code grows; re-verify against the tree before quoting
them, and prefer naming the symbol over citing a size.
