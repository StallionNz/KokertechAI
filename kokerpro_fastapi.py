"""kokerpro_fastapi.py — Architecture V2 FastAPI Server for KokertechAI.

Canonical V2 Web & Headless Remote Dashboard entry point per docs/ARCHITECTURE_V2.md §3.3.
Provides asynchronous REST v2 endpoints, SSE token streaming, and an HTMX/Alpine.js
single-page interface connected directly to the DI ServiceRegistry.

Usage:
    python kokerpro_fastapi.py
    # or
    uvicorn kokerpro_fastapi:app --host 127.0.0.1 --port 5050
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from ai_base import get_provider
import auto_logger
from config import CONFIG, save_settings
from logging_config import get_logger
import memory_vault
import kokertech_bridge
from plugin_registry import registry as plugin_registry
from services import get_services
from utils.gpu import get_vram_usage

logger = get_logger(name="FastAPIWeb")

# Safe configuration keys permitted to be queried or updated over the web API
SAFE_CONFIG_KEYS = [
    "active_provider",
    "model_name",
    "tts_enabled",
    "notifications_enabled",
    "chat_history_enabled",
    "freeform_mode",
    "vram_limit_mb",
    "keepalive_max_ticks",
    "active_theme",
    "active_persona",
]


# ============================================================================
# Pydantic Schemas
# ============================================================================

class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    text: Optional[str] = None
    history: List[ChatMessage] = Field(default_factory=list)
    persona: Optional[str] = None
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


class ChatResponse(BaseModel):
    reply: str
    thinking: str = ""
    sys: str = ""
    model: str = ""
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    duration_ms: float = 0.0


class ClassifyRequest(BaseModel):
    text: str


class ClassifyResponse(BaseModel):
    intent: str
    confidence: float
    probabilities: Dict[str, float] = Field(default_factory=dict)
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "offline"]
    provider: str
    model: str
    vram_usage_mb: int
    memory_vault_healthy: bool
    core_memories_count: int


class StatusResponse(BaseModel):
    system: str = "KokertechAI"
    architecture: str = "V2"
    active_persona: str
    active_provider: str
    model_name: str
    vram_usage_mb: int
    registered_services: List[str]
    uptime_seconds: float


class SettingsResponse(BaseModel):
    settings: Dict[str, Any]


class SettingsUpdateRequest(BaseModel):
    settings: Dict[str, Any]


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = 5


class MemoryItem(BaseModel):
    id: int
    content: str
    created_at: Optional[str] = None
    category: Optional[str] = None


class PluginInfo(BaseModel):
    command: str
    name: str
    description: str
    version: str = "0.0.0"
    tags: List[str] = Field(default_factory=list)
    enabled: bool
    health_score: Optional[float] = None
    success_count: int = 0
    error_count: int = 0


class PluginsResponse(BaseModel):
    count: int
    enabled_count: int
    plugins: List[PluginInfo]


class PluginToggleResponse(BaseModel):
    command: str
    enabled: bool
    message: str


class PluginExecuteRequest(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict)


class PluginExecuteResponse(BaseModel):
    command: str
    success: bool
    result: Any
    duration_ms: float = 0.0


class GraphStatsResponse(BaseModel):
    node_count: int
    link_count: int
    entity_types: Dict[str, int] = Field(default_factory=dict)
    relationship_types: Dict[str, int] = Field(default_factory=dict)


class GraphTraverseResponse(BaseModel):
    start_node_id: Optional[int]
    nodes: List[Dict[str, Any]] = Field(default_factory=list)
    links: List[Dict[str, Any]] = Field(default_factory=list)
    traversal_weights: Dict[str, float] = Field(default_factory=dict)
    paths: Dict[str, List[int]] = Field(default_factory=dict)


class LogsResponse(BaseModel):
    count: int
    logs: List[Dict[str, Any]] = Field(default_factory=list)


# ============================================================================
# Application Factory
# ============================================================================

_START_TIME = time.time()


def create_app() -> FastAPI:
    """Create and configure the canonical Architecture V2 FastAPI application."""
    app = FastAPI(
        title="KokertechAI Neural Web Link",
        version="2.0.0",
        description="Architecture V2 Headless & Remote Dashboard Control API",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------------
    # REST API v2: System & Health
    # ------------------------------------------------------------------------

    @app.get("/api/v2/health", response_model=HealthResponse, tags=["System"])
    def get_health() -> HealthResponse:
        """Health probe inspecting provider readiness, GPU VRAM, and Vault connectivity."""
        provider_name = str(CONFIG.get("active_provider", "offline"))
        model_name = str(CONFIG.get("model_name", "unknown"))
        vram_mb = get_vram_usage()

        vault_ok = True
        core_count = 0
        try:
            core_count = len(memory_vault.get_all_core_memories())
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError) as e:
            logger.debug(f"Vault count probe failed in health check: {e}")
            vault_ok = False

        prov = None
        try:
            prov = get_provider()
        except (ImportError, RuntimeError, ValueError, OSError) as e:
            logger.debug(f"Provider probe failed in health check: {e}")

        is_healthy = prov is not None and vault_ok
        health_status: Literal["healthy", "degraded", "offline"] = (
            "healthy" if is_healthy else ("degraded" if vault_ok else "offline")
        )

        return HealthResponse(
            status=health_status,
            provider=provider_name,
            model=model_name,
            vram_usage_mb=vram_mb,
            memory_vault_healthy=vault_ok,
            core_memories_count=core_count,
        )

    @app.get("/api/v2/status", response_model=StatusResponse, tags=["System"])
    def get_status() -> StatusResponse:
        """Detailed subsystem telemetry for remote dashboards."""
        services = get_services().list()
        return StatusResponse(
            system="KokertechAI",
            architecture="V2",
            active_persona=str(CONFIG.get("active_persona", "Coder")),
            active_provider=str(CONFIG.get("active_provider", "local_llm")),
            model_name=str(CONFIG.get("model_name", "Gemma-4-E2B")),
            vram_usage_mb=get_vram_usage(),
            registered_services=services,
            uptime_seconds=round(time.time() - _START_TIME, 2),
        )

    @app.get("/api/v2/services", tags=["Services"])
    def list_services() -> Dict[str, List[str]]:
        """List all DI singletons registered in the ServiceRegistry."""
        return {"services": get_services().list()}

    # ------------------------------------------------------------------------
    # REST API v2: Inference & Intent
    # ------------------------------------------------------------------------

    @app.post("/api/v2/classify", response_model=ClassifyResponse, tags=["Intent"])
    def classify_intent_endpoint(req: ClassifyRequest) -> ClassifyResponse:
        """Classify user text using the intent model plugin or SubAgent router."""
        text = req.text.strip()
        if not text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Input text cannot be empty.",
            )

        try:
            import plugins.classify_intent
            result = plugins.classify_intent.execute({"text": text})
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    return ClassifyResponse(intent="Unknown", confidence=0.0, error=result)

            if isinstance(result, dict) and "intent" in result:
                return ClassifyResponse(
                    intent=str(result.get("intent", "Unknown")),
                    confidence=float(result.get("confidence", 0.0)),
                    probabilities=dict(result.get("probabilities", {})),
                )
            return ClassifyResponse(intent="General", confidence=0.5, error=str(result))
        except (ImportError, ValueError, RuntimeError, TypeError, KeyError) as e:
            logger.debug(f"Intent classification failed: {e}")
            return ClassifyResponse(intent="General", confidence=0.0, error=str(e))

    @app.post("/api/v2/chat", response_model=ChatResponse, tags=["Chat"])
    def chat_endpoint(req: ChatRequest) -> ChatResponse:
        """Primary synchronous chat inference endpoint with memory context and bridge actions."""
        start_t = time.perf_counter()

        user_text = (req.text or "").strip()
        if not user_text and not req.history:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No messages or prompt text provided.",
            )

        # Build message history
        messages: List[Dict[str, str]] = []

        # Retrieve core memories for context grounding
        memories = ""
        try:
            memories = memory_vault.retrieve_all_memories()
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            logger.debug(f"Memory retrieval fallback in /api/v2/chat: {e}")

        if memories:
            messages.append({"role": "system", "content": f"Core memories: {memories}"})

        for msg in req.history:
            messages.append({"role": msg.role, "content": msg.content})

        if user_text:
            messages.append({"role": "user", "content": user_text})

        provider = None
        try:
            provider = get_provider()
        except (ImportError, RuntimeError, ValueError, OSError) as e:
            logger.warning(f"Failed to obtain AI provider: {e}")

        if provider is None:
            return ChatResponse(
                reply="Link Error: No AI provider available. Please verify local GGUF model or API key.",
                sys="Offline",
                duration_ms=round((time.perf_counter() - start_t) * 1000, 2),
            )

        call_kwargs: Dict[str, Any] = {"messages": messages}
        if req.temperature is not None:
            call_kwargs["temperature"] = req.temperature
        if req.max_tokens is not None:
            call_kwargs["max_tokens"] = req.max_tokens

        response = provider.chat_completion(**call_kwargs)

        if "error" in response:
            return ChatResponse(
                reply=f"Link Error: {response['error']}",
                sys="Inference Failure",
                duration_ms=round((time.perf_counter() - start_t) * 1000, 2),
            )

        reply_content = response.get("content", "")

        # Extract thinking tags if present
        thinking = ""
        final_reply = reply_content
        if "<thinking>" in reply_content and "</thinking>" in reply_content:
            parts = reply_content.split("</thinking>", 1)
            thinking = parts[0].replace("<thinking>", "").strip()
            final_reply = parts[1].replace("<final_output>", "").replace("</final_output>", "").strip()

        # Execute bridge actions if model requested tool execution
        actions_executed: List[Dict[str, Any]] = []
        sys_note = ""
        try:
            actions = kokertech_bridge.extract_valid_actions(final_reply)
            if actions:
                for action in actions:
                    act_result = kokertech_bridge.handle_ai_intent(action)
                    actions_executed.append({"action": action, "result": act_result})
                    sys_note = str(act_result)
        except (ValueError, RuntimeError, TypeError, KeyError) as e:
            logger.debug(f"Action bridge dispatch exception: {e}")

        duration = round((time.perf_counter() - start_t) * 1000, 2)
        return ChatResponse(
            reply=final_reply,
            thinking=thinking,
            sys=sys_note,
            model=str(CONFIG.get("model_name", "default")),
            actions=actions_executed,
            duration_ms=duration,
        )

    @app.post("/api/v2/chat/stream", tags=["Chat"])
    async def chat_stream_endpoint(req: ChatRequest) -> StreamingResponse:
        """Token-by-token streaming inference using Server-Sent Events (SSE)."""
        messages: List[Dict[str, str]] = []
        for msg in req.history:
            messages.append({"role": msg.role, "content": msg.content})
        if req.text:
            messages.append({"role": "user", "content": req.text})

        async def token_generator() -> AsyncGenerator[str, None]:
            provider = None
            try:
                provider = get_provider()
            except (ImportError, RuntimeError, ValueError, OSError) as e:
                logger.warning(f"Streaming provider access failed: {e}")

            if provider is None:
                yield f"data: {json.dumps({'error': 'No AI provider available'})}\n\n"
                return

            try:
                # Use provider streaming if supported, otherwise yield complete answer
                if hasattr(provider, "chat_completion_stream"):
                    for chunk in provider.chat_completion_stream(messages=messages):
                        token = chunk.get("content", "")
                        if token:
                            yield f"data: {json.dumps({'token': token})}\n\n"
                else:
                    resp = provider.chat_completion(messages=messages)
                    token = resp.get("content", "")
                    yield f"data: {json.dumps({'token': token})}\n\n"
                yield "data: [DONE]\n\n"
            except (OSError, ValueError, RuntimeError, TypeError, KeyError) as e:
                logger.warning(f"Streaming token generation error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return StreamingResponse(
            token_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------------
    # REST API v2: Memory & Knowledge
    # ------------------------------------------------------------------------

    @app.get("/api/v2/memory/recent", tags=["Memory"])
    def get_recent_memories(limit: int = 10) -> Dict[str, Any]:
        """Fetch recently stored core and episodic memory records."""
        safe_limit = max(1, min(limit, 50))
        try:
            episodic_rows = memory_vault.get_recent_episodic(limit=safe_limit)
            return {"count": len(episodic_rows), "memories": episodic_rows}
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError) as e:
            logger.debug(f"Recent memory fetch failed: {e}")
            return {"count": 0, "memories": [], "error": str(e)}

    @app.post("/api/v2/memory/search", tags=["Memory"])
    def search_memories(req: MemorySearchRequest) -> Dict[str, Any]:
        """Hybrid vector and keyword search across Memory Vault."""
        query = req.query.strip()
        if not query:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Search query cannot be empty.",
            )

        safe_limit = max(1, min(req.limit, 20))
        try:
            results = memory_vault.hybrid_search(query=query, limit=safe_limit)
            return {"query": query, "count": len(results), "results": results}
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError) as e:
            logger.debug(f"Memory search error: {e}")
            return {"query": query, "count": 0, "results": [], "error": str(e)}

    # ------------------------------------------------------------------------
    # REST API v2: Settings
    # ------------------------------------------------------------------------

    @app.get("/api/v2/settings", response_model=SettingsResponse, tags=["Settings"])
    def get_settings_endpoint() -> SettingsResponse:
        """Return safe subset of CONFIG keys."""
        out: Dict[str, Any] = {}
        for k in SAFE_CONFIG_KEYS:
            if k in CONFIG:
                out[k] = CONFIG[k]
        return SettingsResponse(settings=out)

    @app.post("/api/v2/settings", response_model=SettingsResponse, tags=["Settings"])
    def update_settings_endpoint(req: SettingsUpdateRequest) -> SettingsResponse:
        """Update CONFIG with validated keys and persist durably."""
        updated: Dict[str, Any] = {}
        for k in SAFE_CONFIG_KEYS:
            if k in req.settings:
                CONFIG[k] = req.settings[k]
                updated[k] = req.settings[k]

        if updated:
            save_settings()

        return SettingsResponse(settings=updated)

    # ------------------------------------------------------------------------
    # REST API v2: Plugins Management
    # ------------------------------------------------------------------------

    @app.get("/api/v2/plugins", response_model=PluginsResponse, tags=["Plugins"])
    def list_plugins_endpoint() -> PluginsResponse:
        """List all discovered plugins, enabled states, health scores, and metrics."""
        health_scores = plugin_registry.get_plugin_health_scores()
        usage_counts = plugin_registry.get_execution_successes()
        error_counts = plugin_registry.get_execution_errors()

        items: List[PluginInfo] = []
        for cmd in sorted(plugin_registry.plugins.keys()):
            meta = plugin_registry.metadata.get(cmd, {})
            h_info = health_scores.get(cmd, {})
            s_entry = usage_counts.get(cmd, {}) if isinstance(usage_counts, dict) else {}
            e_entry = error_counts.get(cmd, {}) if isinstance(error_counts, dict) else {}

            s_count = s_entry.get("count", 0) if isinstance(s_entry, dict) else 0
            e_count = e_entry.get("count", 0) if isinstance(e_entry, dict) else 0

            items.append(PluginInfo(
                command=cmd,
                name=str(meta.get("name", cmd)),
                description=str(meta.get("description", "")),
                version=str(meta.get("version", "0.0.0")),
                tags=list(meta.get("tags", [])),
                enabled=plugin_registry.is_enabled(cmd),
                health_score=float(h_info.get("score")) if "score" in h_info else None,
                success_count=s_count,
                error_count=e_count,
            ))

        return PluginsResponse(
            count=len(items),
            enabled_count=plugin_registry.get_enabled_count(),
            plugins=items,
        )

    @app.post("/api/v2/plugins/{name}/toggle", response_model=PluginToggleResponse, tags=["Plugins"])
    def toggle_plugin_endpoint(name: str) -> PluginToggleResponse:
        """Toggle a plugin's enabled status."""
        cmd = name.strip()
        if cmd not in plugin_registry.plugins:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plugin '{cmd}' not found.",
            )
        new_state = plugin_registry.toggle(cmd)
        state_str = "enabled" if new_state else "disabled"
        return PluginToggleResponse(
            command=cmd,
            enabled=new_state,
            message=f"Plugin {cmd} is now {state_str}.",
        )

    @app.post("/api/v2/plugins/{name}/execute", response_model=PluginExecuteResponse, tags=["Plugins"])
    def execute_plugin_endpoint(name: str, req: PluginExecuteRequest) -> PluginExecuteResponse:
        """Execute a plugin safely with given parameter dictionary."""
        cmd = name.strip()
        if cmd not in plugin_registry.plugins:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Plugin '{cmd}' not found.",
            )
        t0 = time.perf_counter()
        res = plugin_registry.execute(cmd, req.params)
        duration = round((time.perf_counter() - t0) * 1000, 2)
        is_err = isinstance(res, str) and res.startswith("[ERROR]")
        return PluginExecuteResponse(
            command=cmd,
            success=not is_err,
            result=res,
            duration_ms=duration,
        )

    # ------------------------------------------------------------------------
    # REST API v2: Neural Memory Graph
    # ------------------------------------------------------------------------

    @app.get("/api/v2/graph/stats", response_model=GraphStatsResponse, tags=["Graph"])
    def get_graph_stats_endpoint() -> GraphStatsResponse:
        """Return neural memory graph statistics, hub counts, and distributions."""
        stats = memory_vault.get_graph_stats() or {}
        nodes_cnt = 0
        try:
            nodes_cnt = len(memory_vault.get_all_core_memories())
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError):
            nodes_cnt = stats.get("entity_count", 0)

        links_cnt = 0
        try:
            links_cnt = len(memory_vault.get_memory_links())
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError):
            links_cnt = stats.get("relationship_count", 0)

        return GraphStatsResponse(
            node_count=nodes_cnt,
            link_count=links_cnt,
            entity_types=dict(stats.get("entity_type_distribution", stats.get("entity_types", {}))),
            relationship_types=dict(stats.get("relationship_type_distribution", stats.get("relationship_types", {}))),
        )

    @app.get("/api/v2/graph/nodes", tags=["Graph"])
    def get_graph_nodes_endpoint(limit: int = 50, offset: int = 0, query: Optional[str] = None) -> Dict[str, Any]:
        """Fetch core memory nodes with optional filtering."""
        safe_limit = max(1, min(limit, 200))
        try:
            nodes = memory_vault.get_all_core_memories()
            if query:
                q_lower = query.lower()
                nodes = [n for n in nodes if q_lower in str(n.get("content", "")).lower()]
            total = len(nodes)
            sliced = nodes[offset: offset + safe_limit]
            return {"total": total, "count": len(sliced), "nodes": sliced}
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError) as e:
            return {"total": 0, "count": 0, "nodes": [], "error": str(e)}

    @app.get("/api/v2/graph/links", tags=["Graph"])
    def get_graph_links_endpoint(
        node_id: Optional[int] = None,
        direction: str = "both",
        min_weight: float = 0.0,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Fetch relationship links across neural graph nodes."""
        safe_limit = max(1, min(limit, 500))
        try:
            links = memory_vault.get_memory_links(node_id=node_id, direction=direction, min_weight=min_weight)
            return {"count": len(links[:safe_limit]), "links": links[:safe_limit]}
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError) as e:
            return {"count": 0, "links": [], "error": str(e)}

    @app.get("/api/v2/graph/triples", tags=["Graph"])
    def get_graph_triples_endpoint(node_id: Optional[int] = None, limit: int = 50) -> Dict[str, Any]:
        """Fetch structured RDF semantic triples (<Subject, Predicate, Object>)."""
        safe_limit = max(1, min(limit, 200))
        try:
            triples = memory_vault.get_semantic_triples(node_id=node_id, limit=safe_limit)
            return {"count": len(triples), "triples": triples}
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError) as e:
            return {"count": 0, "triples": [], "error": str(e)}

    @app.get("/api/v2/graph/traverse/{node_id}", response_model=GraphTraverseResponse, tags=["Graph"])
    def traverse_graph_endpoint(
        node_id: int,
        max_hops: int = 2,
        min_weight: float = 0.1,
    ) -> GraphTraverseResponse:
        """Multi-hop subgraph traversal from a starting node."""
        safe_hops = max(1, min(max_hops, 5))
        try:
            res = memory_vault.traverse_subgraph(start_node_id=node_id, max_hops=safe_hops, min_weight=min_weight)
            return GraphTraverseResponse(
                start_node_id=res.get("start_node_id"),
                nodes=list(res.get("nodes", [])),
                links=list(res.get("links", [])),
                traversal_weights=dict(res.get("traversal_weights", {})),
                paths=dict(res.get("paths", {})),
            )
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError) as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Graph traversal failed: {e}",
            ) from e

    # ------------------------------------------------------------------------
    # REST API v2: System & Execution Logs
    # ------------------------------------------------------------------------

    @app.get("/api/v2/logs/recent", response_model=LogsResponse, tags=["Logs"])
    def get_recent_logs_endpoint(limit: int = 50, category: Optional[str] = None) -> LogsResponse:
        """Fetch recent session journal events or plugin execution logs."""
        safe_limit = max(1, min(limit, 200))
        events = []
        try:
            events = auto_logger.get_session_events(limit=safe_limit, category=category)
        except (OSError, ValueError, TypeError) as e:
            logger.debug(f"Session journal fetch failed; falling back to plugin registry: {e}")

        if not events:
            # Fallback to plugin registry execution audit trail
            raw_execs = plugin_registry.get_recent_executions(limit=safe_limit)
            events = [
                {
                    "timestamp": e.get("ts", ""),
                    "category": "plugin_execution",
                    "message": f"Executed {e.get('command')} (success={e.get('success')}) in {e.get('duration_ms')}ms",
                    "details": e,
                }
                for e in raw_execs
            ]

        return LogsResponse(count=len(events), logs=events)

    @app.get("/api/v2/logs/stream", tags=["Logs"])
    async def stream_logs_endpoint() -> StreamingResponse:
        """Stream live system log events via Server-Sent Events."""
        async def log_generator() -> AsyncGenerator[str, None]:
            last_count = 0
            for _ in range(30):
                events = []
                try:
                    events = auto_logger.get_session_events(limit=10)
                except (OSError, ValueError, TypeError) as e:
                    logger.debug(f"Log stream poll failed; emitting heartbeat: {e}")
                if len(events) != last_count:
                    last_count = len(events)
                    yield f"data: {json.dumps({'type': 'log_batch', 'events': events[:5]})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'heartbeat', 'time': time.time()})}\n\n"
                import asyncio
                await asyncio.sleep(2.0)
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            log_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    # ------------------------------------------------------------------------
    # HTMX / Alpine.js Web Dashboard
    # ------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, tags=["UI"])
    def home_dashboard() -> str:
        """Serve the modern Architecture V2 HTMX/Alpine.js Single-Page Dashboard."""
        return _render_dashboard_html()

    @app.post("/htmx/chat", response_class=HTMLResponse, tags=["HTMX"])
    async def htmx_chat(request: Request) -> str:
        """HTMX endpoint: receives form or json data, returns rendered message bubble."""
        body = await request.body()
        content_type = request.headers.get("content-type", "")
        user_text = ""
        if "application/json" in content_type:
            try:
                data = json.loads(body.decode("utf-8", errors="ignore"))
                user_text = str(data.get("prompt") or data.get("text", "")).strip()
            except (json.JSONDecodeError, UnicodeDecodeError):
                user_text = ""
        else:
            from urllib.parse import parse_qs
            parsed = parse_qs(body.decode("utf-8", errors="ignore"))
            prompt_vals = parsed.get("prompt", [""])
            text_vals = parsed.get("text", [""])
            user_text = str(prompt_vals[0] or text_vals[0]).strip()

        if not user_text:
            return ""

        # Invoke chat endpoint logic
        req = ChatRequest(text=user_text)
        resp = chat_endpoint(req)

        # Build thinking HTML if present
        thinking_block = ""
        if resp.thinking:
            thinking_block = f"""
            <details class="mb-3 bg-zinc-900/80 rounded-lg border border-purple-500/20 text-xs text-zinc-400 p-2.5">
                <summary class="cursor-pointer font-semibold text-purple-400 hover:text-purple-300">
                    🧠 Cognitive Trace ({resp.duration_ms}ms)
                </summary>
                <div class="mt-2 whitespace-pre-wrap font-mono leading-relaxed pl-2 border-l-2 border-purple-500/40">
                    {resp.thinking}
                </div>
            </details>
            """

        sys_badge = ""
        if resp.sys:
            sys_badge = f"""<span class="inline-block mt-2 px-2 py-0.5 rounded text-[11px] bg-emerald-950 text-emerald-300 border border-emerald-800">⚡ Action: {resp.sys}</span>"""

        return f"""
        <!-- User Bubble -->
        <div class="flex justify-end mb-4 animate-fade-in">
            <div class="max-w-[80%] bg-blue-600/90 text-white px-4 py-3 rounded-2xl rounded-tr-sm shadow-md border border-blue-400/30">
                <p class="whitespace-pre-wrap leading-relaxed">{user_text}</p>
            </div>
        </div>

        <!-- Assistant Bubble -->
        <div class="flex justify-start mb-6 animate-fade-in">
            <div class="max-w-[85%] bg-zinc-800/90 text-zinc-100 px-5 py-4 rounded-2xl rounded-tl-sm shadow-lg border border-zinc-700/50">
                <div class="flex items-center gap-2 mb-2 text-xs text-zinc-400 font-mono">
                    <span class="h-2 w-2 rounded-full bg-emerald-400"></span>
                    <span>{resp.model or 'KokertechAI'}</span>
                    <span>•</span>
                    <span>{resp.duration_ms}ms</span>
                </div>
                {thinking_block}
                <div class="whitespace-pre-wrap leading-relaxed text-[15px]">{resp.reply}</div>
                {sys_badge}
            </div>
        </div>
        """

    @app.get("/htmx/status-badge", response_class=HTMLResponse, tags=["HTMX"])
    def htmx_status_badge() -> str:
        """HTMX partial for status polling."""
        health = get_health()
        status_color = "emerald" if health.status == "healthy" else ("amber" if health.status == "degraded" else "rose")
        return f"""
        <div class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-zinc-900 border border-{status_color}-500/30 text-xs font-mono">
            <span class="h-2 w-2 rounded-full bg-{status_color}-400 animate-pulse"></span>
            <span class="text-{status_color}-300 capitalize">{health.status}</span>
            <span class="text-zinc-600">|</span>
            <span class="text-zinc-400">{health.provider}</span>
            <span class="text-zinc-600">|</span>
            <span class="text-zinc-400">{health.vram_usage_mb} MB VRAM</span>
        </div>
        """

    @app.get("/htmx/plugins", response_class=HTMLResponse, tags=["HTMX"])
    def htmx_plugins() -> str:
        """HTMX partial: renders interactive plugin management table."""
        plugins_resp = list_plugins_endpoint()
        rows = []
        for p in plugins_resp.plugins:
            status_cls = "bg-emerald-950 text-emerald-300 border-emerald-800" if p.enabled else "bg-zinc-800 text-zinc-400 border-zinc-700"
            btn_cls = "bg-rose-900/80 hover:bg-rose-800 text-rose-200" if p.enabled else "bg-emerald-900/80 hover:bg-emerald-800 text-emerald-200"
            btn_txt = "Disable" if p.enabled else "Enable"
            rows.append(f"""
            <tr class="border-b border-zinc-800/50 hover:bg-zinc-900/40 text-xs font-mono">
                <td class="p-3 font-semibold text-zinc-200">{p.name} <span class="text-zinc-500 font-normal">({p.command})</span></td>
                <td class="p-3 text-zinc-400 max-w-xs truncate">{p.description}</td>
                <td class="p-3"><span class="px-2 py-0.5 rounded border text-[11px] {status_cls}">{'Active' if p.enabled else 'Disabled'}</span></td>
                <td class="p-3 text-zinc-400">{p.success_count} / {p.error_count}</td>
                <td class="p-3">
                    <button hx-post="/htmx/plugins/{p.command}/toggle"
                            hx-target="closest tr"
                            hx-swap="outerHTML"
                            class="px-2.5 py-1 rounded text-[11px] font-sans font-semibold transition-all {btn_cls}">
                        {btn_txt}
                    </button>
                </td>
            </tr>
            """)
        tbody = "\n".join(rows) if rows else "<tr><td colspan='5' class='p-4 text-center text-zinc-500'>No plugins loaded</td></tr>"
        return f"""
        <div class="overflow-x-auto p-4">
            <table class="w-full text-left border-collapse">
                <thead>
                    <tr class="border-b border-zinc-800 text-zinc-400 text-xs font-mono uppercase bg-zinc-900/60">
                        <th class="p-3">Plugin</th>
                        <th class="p-3">Description</th>
                        <th class="p-3">Status</th>
                        <th class="p-3">Exec (OK/Err)</th>
                        <th class="p-3">Action</th>
                    </tr>
                </thead>
                <tbody>
                    {tbody}
                </tbody>
            </table>
        </div>
        """

    @app.post("/htmx/plugins/{name}/toggle", response_class=HTMLResponse, tags=["HTMX"])
    def htmx_plugin_toggle(name: str) -> str:
        """HTMX endpoint: toggles plugin and returns single updated <tr>."""
        cmd = name.strip()
        if cmd not in plugin_registry.plugins:
            return ""
        new_state = plugin_registry.toggle(cmd)
        meta = plugin_registry.metadata.get(cmd, {})
        status_cls = "bg-emerald-950 text-emerald-300 border-emerald-800" if new_state else "bg-zinc-800 text-zinc-400 border-zinc-700"
        btn_cls = "bg-rose-900/80 hover:bg-rose-800 text-rose-200" if new_state else "bg-emerald-900/80 hover:bg-emerald-800 text-emerald-200"
        btn_txt = "Disable" if new_state else "Enable"
        return f"""
        <tr class="border-b border-zinc-800/50 hover:bg-zinc-900/40 text-xs font-mono animate-fade-in">
            <td class="p-3 font-semibold text-zinc-200">{meta.get('name', cmd)} <span class="text-zinc-500 font-normal">({cmd})</span></td>
            <td class="p-3 text-zinc-400 max-w-xs truncate">{meta.get('description', '')}</td>
            <td class="p-3"><span class="px-2 py-0.5 rounded border text-[11px] {status_cls}">{'Active' if new_state else 'Disabled'}</span></td>
            <td class="p-3 text-zinc-400">-</td>
            <td class="p-3">
                <button hx-post="/htmx/plugins/{cmd}/toggle"
                        hx-target="closest tr"
                        hx-swap="outerHTML"
                        class="px-2.5 py-1 rounded text-[11px] font-sans font-semibold transition-all {btn_cls}">
                    {btn_txt}
                </button>
            </td>
        </tr>
        """

    @app.get("/htmx/graph", response_class=HTMLResponse, tags=["HTMX"])
    def htmx_graph_summary() -> str:
        """HTMX partial: renders neural memory graph summary cards."""
        stats = get_graph_stats_endpoint()
        triples = []
        try:
            triples = memory_vault.get_semantic_triples(limit=5)
        except (sqlite3.Error, OSError, ValueError, RuntimeError, TypeError) as e:
            logger.debug(f"Failed to fetch triples for HTMX graph card: {e}")

        triple_items = "".join([
            f"<li class='text-xs font-mono text-zinc-300 py-1 border-b border-zinc-800/40'>"
            f"<span class='text-blue-400 font-semibold'>[{t.get('predicate', 'RELATES_TO')}]</span> "
            f"{str(t.get('subject', ''))[:40]}... &rarr; {str(t.get('object', ''))[:40]}...</li>"
            for t in triples
        ]) if triples else "<li class='text-xs text-zinc-500'>No semantic triples found.</li>"

        return f"""
        <div class="space-y-4 p-4">
            <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
                <div class="bg-zinc-900/80 p-3 rounded-xl border border-zinc-800 text-center">
                    <span class="block text-2xl font-bold text-blue-400 font-mono">{stats.node_count}</span>
                    <span class="text-[11px] uppercase tracking-wider text-zinc-500">Core Nodes</span>
                </div>
                <div class="bg-zinc-900/80 p-3 rounded-xl border border-zinc-800 text-center">
                    <span class="block text-2xl font-bold text-purple-400 font-mono">{stats.link_count}</span>
                    <span class="text-[11px] uppercase tracking-wider text-zinc-500">Memory Links</span>
                </div>
                <div class="bg-zinc-900/80 p-3 rounded-xl border border-zinc-800 text-center">
                    <span class="block text-2xl font-bold text-emerald-400 font-mono">{len(stats.entity_types)}</span>
                    <span class="text-[11px] uppercase tracking-wider text-zinc-500">Entity Types</span>
                </div>
                <div class="bg-zinc-900/80 p-3 rounded-xl border border-zinc-800 text-center">
                    <span class="block text-2xl font-bold text-amber-400 font-mono">{len(stats.relationship_types)}</span>
                    <span class="text-[11px] uppercase tracking-wider text-zinc-500">Relation Types</span>
                </div>
            </div>
            <div class="bg-zinc-900/60 p-4 rounded-xl border border-zinc-800/80">
                <h3 class="text-xs font-bold uppercase tracking-wider text-zinc-400 mb-2 font-mono">Recent RDF Semantic Triples</h3>
                <ul class="space-y-1">{triple_items}</ul>
            </div>
        </div>
        """

    @app.get("/htmx/logs", response_class=HTMLResponse, tags=["HTMX"])
    def htmx_logs_summary() -> str:
        """HTMX partial: renders recent log entries."""
        logs_resp = get_recent_logs_endpoint(limit=15)
        items = []
        for l in logs_resp.logs:
            ts = l.get("timestamp", "")
            cat = l.get("category", "system")
            msg = l.get("message", "")
            items.append(f"""
            <div class="text-xs font-mono p-2 rounded bg-zinc-900/60 border border-zinc-800/60 flex items-start gap-2">
                <span class="text-zinc-500 whitespace-nowrap">{ts}</span>
                <span class="px-1.5 py-0.5 rounded text-[10px] bg-blue-950 text-blue-300 border border-blue-800 uppercase">{cat}</span>
                <span class="text-zinc-300 flex-1 break-all">{msg}</span>
            </div>
            """)
        body = "\n".join(items) if items else "<div class='text-xs text-zinc-500 p-4 text-center'>No recent log entries.</div>"
        return f"""
        <div class="space-y-2 p-4">
            <div class="flex justify-between items-center mb-2">
                <h3 class="text-xs font-bold uppercase tracking-wider text-zinc-400 font-mono">Live Session Logs ({logs_resp.count})</h3>
                <span class="text-[11px] text-zinc-500 font-mono">auto-refreshed</span>
            </div>
            <div class="space-y-1.5 max-h-[500px] overflow-y-auto">
                {body}
            </div>
        </div>
        """

    return app


# ============================================================================
# HTMX Dashboard Template
# ============================================================================

def _render_dashboard_html() -> str:
    """Return the complete standalone HTML5/HTMX/Alpine dark-mode dashboard."""
    active_persona = str(CONFIG.get("active_persona", "Coder"))
    active_provider = str(CONFIG.get("active_provider", "local_llm"))
    model_name = str(CONFIG.get("model_name", "Gemma-4-E2B"))

    return f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KokertechAI — Neural Web Link (V2)</title>
    <!-- TailwindCSS CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- HTMX CDN -->
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <!-- Alpine.js CDN -->
    <script defer src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js"></script>
    <script>
        tailwind.config = {{
            darkMode: 'class',
            theme: {{
                extend: {{
                    colors: {{
                        brand: '#3b82f6',
                        darkBg: '#090a0f',
                        cardBg: '#12141c',
                    }}
                }}
            }}
        }}
    </script>
    <style>
        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(6px); }} to {{ opacity: 1; transform: translateY(0); }} }}
        .animate-fade-in {{ animation: fadeIn 0.25s ease-out forwards; }}
    </style>
</head>
<body x-data="{{ tab: 'chat' }}" class="bg-darkBg text-zinc-100 min-h-screen flex flex-col font-sans selection:bg-blue-500/30 selection:text-blue-200">
    <!-- Top Navigation Bar -->
    <header class="h-16 border-b border-zinc-800/80 bg-zinc-950/70 backdrop-blur-md px-6 flex items-center justify-between sticky top-0 z-50">
        <div class="flex items-center gap-3">
            <div class="h-9 w-9 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center font-black text-white shadow-lg shadow-blue-500/20">
                [OO]
            </div>
            <div>
                <h1 class="text-sm font-bold tracking-wide flex items-center gap-2">
                    KOKERTECH<span class="text-blue-500">AI</span>
                    <span class="text-[10px] font-mono uppercase bg-blue-500/10 text-blue-400 px-2 py-0.5 rounded border border-blue-500/20">Arch V2</span>
                </h1>
                <p class="text-[11px] text-zinc-400 font-mono">Headless & Remote Dashboard Link</p>
            </div>
        </div>

        <!-- Live Status Bar (Polled via HTMX) -->
        <div hx-get="/htmx/status-badge" hx-trigger="load, every 5s" hx-swap="outerHTML">
            <div class="flex items-center gap-2 px-3 py-1.5 rounded-full bg-zinc-900 border border-zinc-800 text-xs font-mono text-zinc-400">
                <span class="h-2 w-2 rounded-full bg-zinc-600"></span>
                <span>Connecting...</span>
            </div>
        </div>
    </header>

    <!-- Main Workspace Grid -->
    <div class="flex-1 flex max-w-7xl w-full mx-auto p-4 md:p-6 gap-6 overflow-hidden">
        <!-- Sidebar: System Stats & Quick Actions -->
        <aside class="w-72 hidden lg:flex flex-col gap-4">
            <div class="bg-cardBg border border-zinc-800/80 rounded-2xl p-4 shadow-sm">
                <h2 class="text-xs font-bold uppercase tracking-wider text-zinc-400 mb-3">Active Configuration</h2>
                <div class="space-y-3 text-xs font-mono">
                    <div class="flex justify-between border-b border-zinc-800/50 pb-2">
                        <span class="text-zinc-500">Persona</span>
                        <span class="font-semibold text-blue-400">{active_persona}</span>
                    </div>
                    <div class="flex justify-between border-b border-zinc-800/50 pb-2">
                        <span class="text-zinc-500">Provider</span>
                        <span class="font-semibold text-zinc-300">{active_provider}</span>
                    </div>
                    <div class="flex justify-between border-b border-zinc-800/50 pb-2">
                        <span class="text-zinc-500">Model</span>
                        <span class="font-semibold text-zinc-300 truncate max-w-[140px]">{model_name}</span>
                    </div>
                    <div class="flex justify-between">
                        <span class="text-zinc-500">Web Port</span>
                        <span class="font-semibold text-zinc-400">5050 (FastAPI)</span>
                    </div>
                </div>
            </div>

            <div class="bg-cardBg border border-zinc-800/80 rounded-2xl p-4 shadow-sm flex-1">
                <h2 class="text-xs font-bold uppercase tracking-wider text-zinc-400 mb-3">Service Registry</h2>
                <div hx-get="/api/v2/services" hx-trigger="load" hx-swap="innerHTML" class="text-xs font-mono space-y-1.5 text-zinc-400">
                    <span class="text-zinc-600">Querying DI container...</span>
                </div>
            </div>
        </aside>

        <!-- Central Multi-View Workspace -->
        <main class="flex-1 bg-cardBg border border-zinc-800/80 rounded-2xl flex flex-col overflow-hidden shadow-xl">
            <!-- Workspace Navigation Tabs -->
            <div class="flex items-center gap-2 border-b border-zinc-800/80 px-4 py-2.5 bg-zinc-950/40 text-xs font-mono">
                <button @click="tab = 'chat'"
                        :class="tab === 'chat' ? 'bg-blue-600/20 text-blue-400 border-blue-500/30' : 'text-zinc-400 hover:text-zinc-200 border-transparent'"
                        class="px-3 py-1.5 rounded-lg border font-semibold transition-all flex items-center gap-1.5">
                    <span>💬</span> <span>Chat Link</span>
                </button>
                <button @click="tab = 'graph'"
                        hx-get="/htmx/graph"
                        hx-trigger="click"
                        hx-target="#graph-panel"
                        :class="tab === 'graph' ? 'bg-purple-600/20 text-purple-400 border-purple-500/30' : 'text-zinc-400 hover:text-zinc-200 border-transparent'"
                        class="px-3 py-1.5 rounded-lg border font-semibold transition-all flex items-center gap-1.5">
                    <span>🧠</span> <span>Neural Graph</span>
                </button>
                <button @click="tab = 'plugins'"
                        hx-get="/htmx/plugins"
                        hx-trigger="click"
                        hx-target="#plugins-panel"
                        :class="tab === 'plugins' ? 'bg-emerald-600/20 text-emerald-400 border-emerald-500/30' : 'text-zinc-400 hover:text-zinc-200 border-transparent'"
                        class="px-3 py-1.5 rounded-lg border font-semibold transition-all flex items-center gap-1.5">
                    <span>🧩</span> <span>Plugins</span>
                </button>
                <button @click="tab = 'logs'"
                        hx-get="/htmx/logs"
                        hx-trigger="click"
                        hx-target="#logs-panel"
                        :class="tab === 'logs' ? 'bg-amber-600/20 text-amber-400 border-amber-500/30' : 'text-zinc-400 hover:text-zinc-200 border-transparent'"
                        class="px-3 py-1.5 rounded-lg border font-semibold transition-all flex items-center gap-1.5">
                    <span>📜</span> <span>Logs</span>
                </button>
            </div>

            <!-- Tab 1: Chat View -->
            <div x-show="tab === 'chat'" class="flex-1 flex flex-col overflow-hidden">
                <!-- Chat Transcript -->
                <div id="chat-messages" class="flex-1 p-6 overflow-y-auto space-y-4">
                    <div class="flex justify-start">
                        <div class="max-w-[85%] bg-zinc-800/60 text-zinc-300 px-5 py-4 rounded-2xl rounded-tl-sm border border-zinc-700/40 text-sm">
                            <p class="font-semibold text-blue-400 mb-1">KokertechAI Architecture V2 Connected.</p>
                            <p class="text-zinc-400 leading-relaxed text-xs">Ready for headless queries, memory retrieval, and remote execution. Type below to begin.</p>
                        </div>
                    </div>
                </div>

                <!-- Chat Input Form (Powered by HTMX) -->
                <div class="p-4 border-t border-zinc-800/80 bg-zinc-900/40">
                    <form hx-post="/htmx/chat"
                          hx-target="#chat-messages"
                          hx-swap="beforeend"
                          hx-on::after-request="this.reset(); document.getElementById('chat-messages').scrollTop = document.getElementById('chat-messages').scrollHeight;"
                          class="flex gap-3">
                        <input type="text"
                               name="prompt"
                               placeholder="Send command or query to KokertechAI..."
                               required
                               autocomplete="off"
                               class="flex-1 bg-zinc-950/80 border border-zinc-700/60 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all text-zinc-100 placeholder:text-zinc-500">
                        <button type="submit"
                                class="bg-blue-600 hover:bg-blue-500 active:scale-95 text-white px-5 py-3 rounded-xl text-sm font-semibold transition-all shadow-lg shadow-blue-600/20 flex items-center gap-2">
                            <span>Send</span>
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
                        </button>
                    </form>
                </div>
            </div>

            <!-- Tab 2: Neural Graph View -->
            <div x-show="tab === 'graph'" id="graph-panel" class="flex-1 overflow-y-auto p-4">
                <div class="p-8 text-center text-zinc-500 text-xs font-mono">Click to load Neural Memory Graph...</div>
            </div>

            <!-- Tab 3: Plugins View -->
            <div x-show="tab === 'plugins'" id="plugins-panel" class="flex-1 overflow-y-auto p-4">
                <div class="p-8 text-center text-zinc-500 text-xs font-mono">Click to load Plugin Registry...</div>
            </div>

            <!-- Tab 4: Logs View -->
            <div x-show="tab === 'logs'" id="logs-panel" class="flex-1 overflow-y-auto p-4">
                <div class="p-8 text-center text-zinc-500 text-xs font-mono">Click to load System Logs...</div>
            </div>
        </main>
    </div>
</body>
</html>
"""


# Global application instance
app = create_app()


if __name__ == "__main__":
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description="KokertechAI Architecture V2 FastAPI Server")
    parser.add_argument("--port", type=int, default=5050, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, reload=False)
