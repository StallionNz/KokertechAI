#!/usr/bin/env python3
"""
ai_base.py — AI inference provider for KokertechAI.

Only supports local GGUF model inference via llama-cpp-python.
No external AI providers (no HTTP API calls).
"""

import os
import sys
import time
import json
import hashlib
import random
import subprocess
import threading
from contextlib import closing, contextmanager
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from config import CONFIG
from logging_config import get_logger

logger = get_logger(name="AIBase")

# ─── Module-level state ─────────────────────────────────────────────
try:
    from llama_cpp import Llama
    _LLAMA_CPP_AVAILABLE: bool = True
except ImportError:
    Llama = None  # type: ignore
    _LLAMA_CPP_AVAILABLE: bool = False
_GRAMMAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "grammars")

# Response cache (opt-in LRU with TTL)
_RESPONSE_CACHE: dict = {}
_RESPONSE_CACHE_LOCK = threading.Lock()
_RESPONSE_CACHE_MAX = 50
_RESPONSE_CACHE_TTL = 60  # seconds

# Grammar cache: {format_type: {"grammar": str, "mtime": float}}
_GRAMMAR_CACHE: dict = {}
_GRAMMAR_CACHE_LOCK = threading.Lock()

# Vision model detection. "mmproj" covers llama.cpp Multimodal Projector
# files (mmproj-*.gguf) — pure vision adapters, never chat models.
VISION_MODELS = ("vision", "llava", "gemma3", "gemma-4", "mmproj")

# Projector files: llama.cpp emits multimodal projectors as
# mmproj-<model>-<quant>.gguf alongside the main weights. They are vision
# ADAPTERS, not chat models — loading one as a text model fails — so the
# main model pickers filter them out (see is_projector_model). Kept in the
# vision-model picker, where they are exactly what belongs.
PROJECTOR_BASENAMES = ("mmproj",)

# Provider cache: {name: instance}
_PROVIDER_CACHE: dict = {}
_PROVIDER_CACHE_LOCK = threading.Lock()

# XML thinking/final_output GBNF grammar
_XML_GRAMMAR = """root ::= sp thinking sp final_output sp
sp ::= [ \\t\\n]*
thinking ::= "<thinking>" thinking_content "</thinking>"
thinking_content ::= [^<]*
final_output ::= "<final_output>" final_content "</final_output>"
final_content ::= [^<]*"""
GBNF_XML_THINKING_FINAL = _XML_GRAMMAR  # Public alias for backward compatibility

# Dynamic n_ctx table for known models
_MODEL_CTX_TABLE = {
    "qwen2.5": 32768,
    "qwen2": 32768,
    "qwen3": 32768,
    "llama-3": 8192,
    "llama3": 8192,
    "llama-2": 4096,
    "llama2": 4096,
    "ministral": 32768,
    "mistral": 8192,
    "mixtral": 32768,
    "phi-3": 4096,
    "phi3": 4096,
    "phi-4": 4096,
    "phi4": 4096,
    "gemma-2": 8192,
    "gemma2": 8192,
    "codegemma": 8192,
    "deepseek": 4096,
    "yi": 4096,
    "codestral": 32768,
    "dbrx": 32768,
    "command-r": 131072,
    "lexi": 8192,
    "hermes": 8192,
    "nemotron": 4096,
}


# ═══════════════════════════════════════════════════════════════════════
# 1. AIProvider — Abstract Base Class
# ═══════════════════════════════════════════════════════════════════════

class AIProvider(ABC):
    """Abstract base class for AI providers.

    Subclasses must implement ``chat_completion()`` and optionally
    ``chat_completion_stream()``.
    """
    def __init__(self, default_model: str = ""):
        self.default_model = default_model

    @abstractmethod
    def chat_completion(self, messages: list, model: str = "",
                        temperature: float = 0.7, max_tokens: int = -1,
                        timeout: float = 30, request_id: str = "",
                        cancel_event=None, response_format: dict = None,
                        tools: list = None,
                        cache_prompt: Optional[bool] = None) -> dict:
        """Send a chat completion request.

        Returns a dict with 'content', 'error', and optionally 'usage'.
        """
        return None

    def chat_completion_stream(self, messages: list, model: str = "",
                                temperature: float = 0.7, max_tokens: int = -1,
                                timeout: float = 30, request_id: str = "",
                                cancel_event=None, response_format: dict = None,
                                tools: list = None,
                                cache_prompt: Optional[bool] = None):
        """Stream a chat completion response.

        Yields token dicts.
        """
        return
        yield  # Make this a generator


# ═══════════════════════════════════════════════════════════════════════
# 2. CircuitBreaker
# ═══════════════════════════════════════════════════════════════════════

class CircuitBreaker:
    """Circuit breaker for AI provider failures.

    Tracks failure counts and opens the circuit when ``max_failures``
    is exceeded. When open, subsequent calls return an error immediately
    without attempting inference.
    """
    def __init__(self, max_failures: int = 5, reset_timeout: float = 30.0):
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self._failures = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        """Check if the circuit is open (failing)."""
        with self._lock:
            if self._failures >= self.max_failures:
                if time.time() - self._last_failure_time > self.reset_timeout:
                    self._failures = 0
                    return False
                return True
            return False

    def allow_request(self) -> bool:
        """Check if a request is allowed to proceed (circuit is closed)."""
        return not self.is_open

    def remaining_cooldown(self) -> float:
        """Seconds remaining before an open circuit attempts recovery."""
        with self._lock:
            if self._failures >= self.max_failures:
                elapsed = time.time() - self._last_failure_time
                if elapsed < self.reset_timeout:
                    return max(0.0, self.reset_timeout - elapsed)
                self._failures = 0
            return 0.0

    def record_success(self):
        """Record a successful inference (reset failure count)."""
        with self._lock:
            self._failures = 0

    def record_failure(self):
        """Record a failed inference."""
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()

    def reset(self):
        """Manually reset the circuit breaker."""
        with self._lock:
            self._failures = 0
            self._last_failure_time = 0.0


_circuit_breaker = CircuitBreaker()


def get_circuit_breaker() -> CircuitBreaker:
    """Return the process-wide circuit breaker instance."""
    return _circuit_breaker


# ═══════════════════════════════════════════════════════════════════════
# 3. Response Cache — Optional LRU with TTL
# ═══════════════════════════════════════════════════════════════════════

def _compute_cache_key(messages: list, model: str = "",
                       temperature: float = 0.7, max_tokens: int = -1,
                       response_format: dict = None,
                       tools: list = None) -> str:
    """Compute a deterministic cache key for response caching.

    Sprint 14 Phase 2: Uses md5 instead of sha256 for speed (cache keys
    don't need cryptographic security). Uses repr() as fast path instead
    of json.dumps(sort_keys=True) which is ~1-5ms for large histories.
    """
    rf_str = "" if response_format is None else repr(response_format)
    tools_str = "" if tools is None else repr(tools)
    raw = f"{model}|{temperature}|{max_tokens}|{rf_str}|{tools_str}|{repr(messages)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def cached_chat_completion(provider: AIProvider, messages: list,
                           model: str = "", temperature: float = 0.7,
                           max_tokens: int = -1, timeout: float = 30,
                           request_id: str = "", cancel_event=None,
                           response_format: dict = None,
                           tools: list = None,
                           cache_prompt: Optional[bool] = None) -> dict:
    """Opt-in LRU response cache wrapper around provider.chat_completion().

    When ``CONFIG["response_cache_enabled"]`` is True, caches successful
    responses keyed by (messages, model, temperature, max_tokens, format, tools).
    Cache entries expire after ``_RESPONSE_CACHE_TTL`` (60s).
    Errors are never cached. Returns a shallow copy of cached results.
    """
    cache_enabled = CONFIG.get("response_cache_enabled", False)
    if not cache_enabled:
        return provider.chat_completion(
            messages=messages, model=model, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout, request_id=request_id,
            cancel_event=cancel_event, response_format=response_format,
            tools=tools, cache_prompt=cache_prompt
        )

    key = _compute_cache_key(messages, model, temperature, max_tokens,
                             response_format, tools)

    # Check cache
    with _RESPONSE_CACHE_LOCK:
        if key in _RESPONSE_CACHE:
            entry = _RESPONSE_CACHE[key]
            if time.time() - entry["ts"] < _RESPONSE_CACHE_TTL:
                # Move to end (LRU: mark as recently used) and refresh ts
                val = _RESPONSE_CACHE.pop(key)
                val["ts"] = time.time()
                _RESPONSE_CACHE[key] = val
                return dict(val["result"])  # shallow copy

    # Cache miss — call provider
    result = provider.chat_completion(
        messages=messages, model=model, temperature=temperature,
        max_tokens=max_tokens, timeout=timeout, request_id=request_id,
        cancel_event=cancel_event, response_format=response_format,
        tools=tools, cache_prompt=cache_prompt
    )

    # Only cache successful responses (no error)
    if result and not result.get("error"):
        with _RESPONSE_CACHE_LOCK:
            max_size = CONFIG.get("response_cache_max_size", _RESPONSE_CACHE_MAX)
            if len(_RESPONSE_CACHE) >= max_size:
                # LRU eviction: pop the first (least recently used) item
                _RESPONSE_CACHE.pop(next(iter(_RESPONSE_CACHE)))
            _RESPONSE_CACHE[key] = {
                "result": dict(result),  # store a copy
                "ts": time.time(),
            }

    return result


def clear_response_cache():
    """Clear all cached responses."""
    with _RESPONSE_CACHE_LOCK:
        _RESPONSE_CACHE.clear()


# ═══════════════════════════════════════════════════════════════════════
# 4. Grammar — GBNF Grammar for Structured Output
# ═══════════════════════════════════════════════════════════════════════

def _get_grammar_file_path(format_type: str) -> str:
    """Resolve the .gbnf file path for a given grammar format."""
    filename = f"{format_type}.gbnf"
    return os.path.join(_GRAMMAR_DIR, filename)


def invalidate_grammar_cache(format_type: str = None):
    """Force the next get_grammar() call to reload from disk.

    Args:
        format_type: Specific grammar to invalidate (e.g. "xml"),
                     or None to clear all cached grammars.
    """
    with _GRAMMAR_CACHE_LOCK:
        if format_type is None:
            _GRAMMAR_CACHE.clear()
        else:
            _GRAMMAR_CACHE.pop(format_type, None)


def _validate_response_format(rf: dict) -> dict:
    """Validate the response_format dict before sending to llama-cpp-python.

    Catches typos like {"type": "grammer"} or missing "value" for grammar type
    before they cause cryptic provider errors.

    Returns the validated dict, or raises ValueError on invalid input.
    """
    if rf is None:
        return rf
    if not isinstance(rf, dict):
        raise ValueError(f"response_format must be a dict, got {type(rf).__name__}")
    fmt_type = rf.get("type", "")
    if fmt_type == "grammar":
        if "value" not in rf:
            raise ValueError("response_format type='grammar' requires a 'value' key with the GBNF string")
        return rf
    elif fmt_type in ("json_object", "json"):
        # llama.cpp has built-in json_object support; no GBNF needed
        return rf
    elif fmt_type == "xml":
        # Built-in XML grammar
        return rf
    elif fmt_type:
        raise ValueError(f"Unknown response_format type: {fmt_type!r}")
    return rf


def _normalize_chat_messages(messages: list) -> list:
    """Normalize conversation messages for llama.cpp chat templates.

    Enforces strict role alternation (system? -> user <-> assistant) and extracts
    any misplaced system messages to the beginning. Prevents jinja chat template
    validation errors (e.g. 'After the optional system message, conversation roles
    must alternate user and assistant roles except for tool calls and results.').
    """
    if not messages or not isinstance(messages, list):
        return [{"role": "user", "content": ""}]

    system_parts = []
    non_system = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "user")).lower()
        content = m.get("content", "")
        if role == "system":
            if isinstance(content, str):
                if content.strip():
                    system_parts.append(content.strip())
            elif isinstance(content, list):
                text_items = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                extracted = "\n".join(t for t in text_items if t).strip()
                if extracted:
                    system_parts.append(extracted)
            elif content:
                system_parts.append(str(content).strip())
        else:
            m_copy = dict(m)
            m_copy["role"] = role
            if isinstance(content, list):
                m_copy["content"] = content
            else:
                m_copy["content"] = str(content or "")
            non_system.append(m_copy)

    normalized = []
    if system_parts:
        normalized.append({"role": "system", "content": "\n\n".join(system_parts)})

    for m in non_system:
        role = m["role"]
        if not normalized or normalized[-1]["role"] == "system":
            normalized.append(m)
            continue

        last_role = normalized[-1]["role"]
        # Coalesce consecutive messages with identical standard roles
        if (
            role == last_role
            and role in ("user", "assistant")
            and not m.get("tool_calls")
            and not normalized[-1].get("tool_calls")
        ):
            prev_content = normalized[-1].get("content", "")
            curr_content = m.get("content", "")
            if isinstance(prev_content, str) and isinstance(curr_content, str):
                if prev_content and curr_content:
                    normalized[-1]["content"] = f"{prev_content}\n\n{curr_content}"
                elif curr_content:
                    normalized[-1]["content"] = curr_content
            elif isinstance(prev_content, list) or isinstance(curr_content, list):
                p_blocks = (
                    prev_content
                    if isinstance(prev_content, list)
                    else [{"type": "text", "text": str(prev_content)}]
                    if prev_content
                    else []
                )
                c_blocks = (
                    curr_content
                    if isinstance(curr_content, list)
                    else [{"type": "text", "text": str(curr_content)}]
                    if curr_content
                    else []
                )
                normalized[-1]["content"] = list(p_blocks) + list(c_blocks)
            else:
                normalized.append(m)
        else:
            normalized.append(m)

    has_user = any(m.get("role") == "user" for m in normalized)
    if not has_user:
        normalized.append({"role": "user", "content": ""})

    return normalized


def get_grammar(format_type: str = "xml") -> str:
    """Return a GBNF grammar string for the requested output format.

    Args:
        format_type: "xml" for <thinking>/<final_output> format,
                     "json" / "json_object" for JSON object output.

    Returns:
        A GBNF grammar string suitable for llama-cpp-python.
        For "json"/"json_object", raises ValueError — GBNF cannot express
        JSON's nested structure. Use llama.cpp's built-in json_object mode
        ({"type": "json_object"}) or call resolve_response_format() from
        services.structured_output_service.

    Sprint 12: Grammar hot-reload. When CONFIG["grammar_hot_reload"] is True
    (default), the grammar is loaded from data/grammars/*.gbnf files on each
    call and cached by mtime. Editing the .gbnf file takes effect on the next
    request without restarting the app.
    """
    if format_type == "xml":
        # Check for file-based grammar (hot-reload)
        gbnf_path = _get_grammar_file_path("xml")
        hot_reload = CONFIG.get("grammar_hot_reload", True)
        if hot_reload and os.path.isfile(gbnf_path):
            try:
                current_mtime = os.path.getmtime(gbnf_path)
                with _GRAMMAR_CACHE_LOCK:
                    cached = _GRAMMAR_CACHE.get("xml")
                    if cached and cached["mtime"] >= current_mtime:
                        return cached["grammar"]
                with open(gbnf_path, "r", encoding="utf-8") as f:
                    grammar = f.read()
                # Empty/whitespace-only file falls back to built-in default
                if not grammar.strip():
                    return _XML_GRAMMAR
                with _GRAMMAR_CACHE_LOCK:
                    _GRAMMAR_CACHE["xml"] = {"grammar": grammar, "mtime": current_mtime}
                return grammar
            except (OSError, IOError) as e:
                logger.warning(f"Failed to load XML grammar from {gbnf_path}: {e}")
        # Fallback to built-in grammar
        return _XML_GRAMMAR

    elif format_type in ("json", "json_object"):
        # GBNF cannot express JSON's nested structure
        logger.warning(
            f"get_grammar('{format_type}') called but GBNF cannot represent JSON. "
            "Use response_format={'type': 'json_object'} with llama.cpp instead."
        )
        raise ValueError(
            "GBNF cannot express JSON nested structure. "
            "Use response_format={'type': 'json_object'} instead."
        )
    else:
        raise ValueError(f"Unknown grammar format: {format_type!r}")


# ═══════════════════════════════════════════════════════════════════════
# 5. Provider Factory
# ═══════════════════════════════════════════════════════════════════════

def _is_vision_model(model_name: str) -> bool:
    """Check if the model name indicates vision/multimodal capabilities."""
    if not model_name:
        return False
    lower = model_name.lower()
    return any(v in lower for v in VISION_MODELS)


def is_projector_model(model_name: str) -> bool:
    """True when ``model_name`` is an mmproj projector adapter, not a chat model.

    Matched on the BASENAME (so "C:/m/mmproj-x.gguf" and "mmproj-x.gguf"
    both match, while "Ministral-mmprojless.gguf" does not — only names
    *starting* with a projector marker count, mirroring llama.cpp's
    mmproj-<model> naming convention).
    """
    if not isinstance(model_name, str) or not model_name:
        return False
    basename = os.path.basename(model_name.replace("\\", "/")).strip().lower()
    return basename.startswith(PROJECTOR_BASENAMES)


def get_provider(name: str = "local_llm", default_model: str = "") -> AIProvider:
    """Factory: return a cached LocalLLMProvider instance.

    The provider is created on first access and cached in ``_PROVIDER_CACHE``.
    ``default_model`` is only used when creating a new provider (not when
    returning a cached one).
    """
    with _PROVIDER_CACHE_LOCK:
        cache_key = f"{name}:{default_model}" if default_model else name
        if cache_key in _PROVIDER_CACHE:
            return _PROVIDER_CACHE[cache_key]
        model = default_model or CONFIG.get("model_name", "")
        models_dir = CONFIG.get("models_dir", "models")
        n_ctx = CONFIG.get("llm_n_ctx") or CONFIG.get("n_ctx", 0)
        n_gpu_layers = CONFIG.get("llm_n_gpu_layers") or CONFIG.get("n_gpu_layers", 0)
        n_threads = CONFIG.get("llm_n_threads") or CONFIG.get("n_threads", 4)

        provider = LocalLLMProvider(
            models_dir=models_dir,
            model_file=model,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            n_threads=n_threads,
            default_model=model,
        )
        _PROVIDER_CACHE[cache_key] = provider
        return provider


def reset_providers():
    """Clear the provider cache, forcing re-creation on next get_provider()."""
    with _PROVIDER_CACHE_LOCK:
        _PROVIDER_CACHE.clear()


def invalidate_provider(name: str = None):
    """Remove cached provider(s) by cache key prefix.

    If name is None, clears all providers. If name is a string, removes
    any cached provider whose cache key starts with that name.
    """
    with _PROVIDER_CACHE_LOCK:
        if name is None:
            _PROVIDER_CACHE.clear()
        else:
            # Normalize spaces and dashes to underscores
            name = name.replace(" ", "_").replace("-", "_")
            keys_to_remove = [k for k in _PROVIDER_CACHE if k.startswith(name)]
            for k in keys_to_remove:
                _PROVIDER_CACHE.pop(k, None)


@contextmanager
def provider_cache_scope():
    """Context manager that isolates _PROVIDER_CACHE to the scope.

    On entry, snapshots the current cache. On exit, restores the snapshot
    so any providers added during the scope are removed while providers
    that existed before the scope started are preserved.

    Thread-safety: the snapshot and restore are NOT locked. If another
    thread modifies _PROVIDER_CACHE between snapshot and restore, the
    new entries will be silently dropped on restore.

    Primary use case: test fixtures, where tests run serially.
    """
    with _PROVIDER_CACHE_LOCK:
        snapshot = dict(_PROVIDER_CACHE)
    try:
        yield
    finally:
        with _PROVIDER_CACHE_LOCK:
            _PROVIDER_CACHE.clear()
            _PROVIDER_CACHE.update(snapshot)


# ═══════════════════════════════════════════════════════════════════════
# 6. Retry with Exponential Backoff
# ═══════════════════════════════════════════════════════════════════════

def retry_with_backoff(fn, *args,
                       max_attempts: int = None,
                       base_delay_ms: int = None,
                       max_delay_ms: int = None,
                       backoff_factor: float = None,
                       jitter: bool = None,
                       on_retry=None,
                       cancel_event=None,
                       **kwargs):
    """Call *fn* with automatic retry and exponential backoff.

    Args:
        fn: Callable to invoke (receives *args, **kwargs).
        max_attempts: Total attempts including the first call.
            Defaults to CONFIG["retry_max_attempts"] (3).
        base_delay_ms: Initial delay between retries in milliseconds.
            Defaults to CONFIG["retry_base_delay_ms"] (500).
        max_delay_ms: Upper bound on delay in milliseconds.
            Defaults to CONFIG["retry_max_delay_ms"] (10000).
        backoff_factor: Multiplier for each successive delay.
            Defaults to CONFIG["retry_backoff_factor"] (2.0).
        jitter: Whether to add random jitter (0-30% of delay).
            Defaults to CONFIG["retry_jitter"] (True).
        on_retry: Optional callback(attempt, delay_ms, error) called
            before each retry sleep.

    Returns:
        The result of fn(*args, **kwargs) on success.

    Raises:
        ValueError if max_attempts < 1.
        The last exception from fn if all attempts fail.
    """
    max_attempts = max_attempts if max_attempts is not None else CONFIG.get("retry_max_attempts", 3)
    base_delay_ms = base_delay_ms if base_delay_ms is not None else CONFIG.get("retry_base_delay_ms", 500)
    max_delay_ms = max_delay_ms if max_delay_ms is not None else CONFIG.get("retry_max_delay_ms", 10000)
    backoff_factor = backoff_factor if backoff_factor is not None else CONFIG.get("retry_backoff_factor", 2.0)
    jitter = jitter if jitter is not None else CONFIG.get("retry_jitter", True)

    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt >= max_attempts:
                raise
            if cancel_event and cancel_event.is_set():
                raise last_error
            delay = base_delay_ms * (backoff_factor ** (attempt - 1))
            delay = min(delay, max_delay_ms)
            if jitter:
                delay *= 0.7 + random.random() * 0.3  # 70%-100% of base
            if on_retry:
                try:
                    on_retry(attempt, delay, e)
                except Exception as e2:
                    logger.debug(f"on_retry callback failed (non-fatal): {e2}")
            time.sleep(delay / 1000.0)


# ═══════════════════════════════════════════════════════════════════════
# 7. Plugin Schema → OpenAI Tool Format Converter
# ═══════════════════════════════════════════════════════════════════════

def convert_plugin_schema_to_openai_tool(schema: dict) -> dict:
    """Convert a plugin SCHEMA dict to OpenAI tools format.

    Plugin schemas look like:
        {"action": "COMMAND_NAME", "param1": "description", ...}

    OpenAI tools format:
        {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}

    The first non-action param is required; subsequent params are optional.
    """
    if not schema or not isinstance(schema, dict):
        return {"type": "function", "function": {"name": "unknown", "description": "", "parameters": {"type": "object", "properties": {}, "required": []}}}
    action = schema.get("action", "unknown")
    properties = {}
    required = []
    param_index = 0
    for key, value in schema.items():
        if key == "action":
            continue
        properties[key] = {
            "type": "string",
            "description": str(value)[:200],
        }
        if param_index == 0:
            required.append(key)  # First param is required
        param_index += 1
    tool = {
        "type": "function",
        "function": {
            "name": action.replace(" ", "_"),
            "description": f"Execute the {action} command",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }
    return tool


# ═══════════════════════════════════════════════════════════════════════
# 8. LocalLLMProvider — llama-cpp-python Wrapper
# ═══════════════════════════════════════════════════════════════════════

def _resolve_n_ctx(model_name: str, configured_n_ctx: int = 0) -> int:
    """Resolve the context window size for the given model.

    If ``configured_n_ctx`` is > 0, uses it directly.
    Otherwise looks up the model in ``_MODEL_CTX_TABLE`` by prefix match.
    Falls back to 2048.
    """
    if configured_n_ctx and configured_n_ctx > 0:
        return configured_n_ctx
    lower = model_name.lower()
    for prefix, ctx in sorted(_MODEL_CTX_TABLE.items(), key=lambda x: -len(x[0])):
        if prefix in lower:
            return ctx
    return 2048


class LocalLLMProvider(AIProvider):
    """Local GGUF model provider via llama-cpp-python.

    Wraps a single ``llama_cpp.Llama`` instance with thread-safe inference
    via ``_inference_lock``. Supports response format via GBNF grammar,
    streaming, and function calling (tool use).
    """

    def __init__(self, models_dir: str = "models", model_file: str = "",
                 n_ctx: int = 0, n_gpu_layers: int = 0,
                 n_threads: int = 4, default_model: str = ""):
        super().__init__(default_model=default_model)
        self.models_dir = models_dir
        self.model_file = model_file
        self.configured_n_ctx = n_ctx or CONFIG.get("llm_n_ctx", 0) or CONFIG.get("n_ctx", 0)
        self.active_n_ctx = 0
        self.n_gpu_layers = n_gpu_layers
        self.n_threads = n_threads
        self._model_instance = None
        self._model_path = None
        self._cache = None
        self._inference_lock = threading.Lock()
        self._last_model_name = ""

    def is_loaded(self) -> bool:
        """True when a model instance is resident (loaded and ready).

        Telemetry contract (2026-09): the dashboard sidebar badge polls
        this to decide 🟢 vs ⚪. Explicit-selection policy means a fresh
        install starts unloaded; onboarding/Settings Apply flips this to
        True once llama.cpp has the weights resident.
        """
        return self._model_instance is not None

    def _setup_kv_cache(self, capacity_mb: Optional[int] = None):
        """Allocate and attach a LlamaRAMCache for KV prefix reuse."""
        if self._model_instance is None or not _LLAMA_CPP_AVAILABLE:
            return
        try:
            import llama_cpp
            if capacity_mb is None:
                capacity_mb = CONFIG.get("kv_cache_ram_mb", 512)
            capacity_bytes = int(capacity_mb) * 1024 * 1024
            self._cache = llama_cpp.LlamaRAMCache(capacity_bytes=capacity_bytes)
            if hasattr(self._model_instance, "set_cache"):
                self._model_instance.set_cache(self._cache)
            logger.info("KV cache prefix reuse enabled (allocated %d MB RAM cache)", capacity_mb)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to initialize KV cache: %s", e)
            self._cache = None

    def set_kv_cache_enabled(self, enabled: bool, capacity_mb: int = 512):
        """Dynamically toggle KV cache prefix reuse."""
        if enabled:
            self._setup_kv_cache(capacity_mb=capacity_mb)
        else:
            if self._model_instance is not None and hasattr(self._model_instance, "set_cache"):
                try:
                    self._model_instance.set_cache(None)
                except Exception as e:  # noqa: BLE001
                    logger.debug("Failed to detach cache: %s", e)
            if self._cache is not None:
                try:
                    if hasattr(self._cache, "cache_state") and isinstance(self._cache.cache_state, dict):
                        self._cache.cache_state.clear()
                    self._cache.cache_size = 0
                except Exception as e:  # noqa: BLE001
                    logger.debug("Failed to clear cache: %s", e)
                self._cache = None

    def clear_kv_cache(self):
        """Clear all stored prefix states in the KV cache."""
        if self._cache is not None:
            try:
                if hasattr(self._cache, "cache_state") and isinstance(self._cache.cache_state, dict):
                    self._cache.cache_state.clear()
                self._cache.cache_size = 0
            except Exception as e:  # noqa: BLE001
                logger.debug("Failed to clear cache state: %s", e)

    def _load_model(self, model_path: str, progress=None):
        """Load a GGUF model from the given path.

        Parameters
        ----------
        model_path : str
            Absolute path to the .gguf file.
        progress : callable, optional
            Zero-arg callback receiving human-readable progress strings
            for verbose load reporting (e.g. the audit log). Callback
            failures are swallowed — a broken sink must never break a
            load.
        """
        global _LLAMA_CPP_AVAILABLE
        self._model_path = model_path
        self._last_model_name = os.path.basename(model_path)
        try:
            import llama_cpp
        except ImportError:
            logger.error("llama-cpp-python is not installed. Cannot load local model.")
            _LLAMA_CPP_AVAILABLE = False
            return False

        if not self.configured_n_ctx:
            self.configured_n_ctx = CONFIG.get("llm_n_ctx", 0) or CONFIG.get("n_ctx", 0)
        n_ctx_val = _resolve_n_ctx(self._last_model_name, self.configured_n_ctx)
        self.active_n_ctx = n_ctx_val
        try:
            size_gb = 0.0
            try:
                size_gb = os.path.getsize(model_path) / (1024**3)
            except OSError as e:
                # Breadcrumb (silent-catch audit): 0.0GB in the load banner
                # without a trace looks like a stat bug; load proceeds and the
                # real failure (if any) is logged by the outer handler.
                logger.debug(f"Model size stat failed (banner shows 0.0GB): {e}")

            def _report(msg):
                """Emit a progress line; a broken sink never breaks a load."""
                logger.info(msg)
                if progress is not None:
                    try:
                        progress(msg)
                    except Exception as e:  # noqa: BLE001
                        # Breadcrumb (silent-catch audit): a broken progress
                        # sink must never break a load, but it is recorded.
                        logger.debug(f"Progress callback error: {e}")

            _report(
                f"⏳ Loading {self._last_model_name} "
                f"({size_gb:.1f}GB, ctx={n_ctx_val}, gpu={self.n_gpu_layers})...")
            t0 = time.time()
            # verbose=True: llama.cpp prints load progress (tokenizer,
            # tensor mapping, layer offload, warmup) to stderr, which the
            # launcher pipes to the file log — the only way to see WHY a
            # load is slow or failing. Guard: windowed launches (pythonw /
            # no-console exe) have sys.stderr = None and llama-cpp's
            # stderr writes would raise inside the C log callback.
            import sys as _sys
            _stderr_ok = _sys.stderr is not None
            self._model_instance = llama_cpp.Llama(
                model_path=model_path,
                n_ctx=n_ctx_val,
                n_gpu_layers=self.n_gpu_layers,
                n_threads=self.n_threads,
                verbose=_stderr_ok,
            )
            if CONFIG.get("kv_cache_prefix_reuse", False):
                self._setup_kv_cache()
            load_ms = int((time.time() - t0) * 1000)
            _report(
                f"✅ Model loaded: {self._last_model_name} "
                f"({load_ms}ms, ctx={n_ctx_val}, gpu={self.n_gpu_layers})")
            return True
        except Exception as e:
            logger.error(f"Failed to load model {model_path}: {e}", exc_info=True)
            if progress is not None:
                try:
                    progress(f"❌ Load failed: {self._last_model_name} — {e}")
                except Exception as err:  # noqa: BLE001
                    logger.debug(f"Progress callback error: {err}")
            self._model_instance = None
            return False

    def unload_model(self):
        """Unload the current model and free GPU memory."""
        if self._model_instance is not None:
            try:
                if hasattr(self._model_instance, "set_cache"):
                    self._model_instance.set_cache(None)
            except Exception as e:  # noqa: BLE001
                logger.debug("Cache detach failed (non-fatal): %s", e)
            if self._cache is not None:
                try:
                    if hasattr(self._cache, "cache_state") and isinstance(self._cache.cache_state, dict):
                        self._cache.cache_state.clear()
                    self._cache.cache_size = 0
                except Exception as e:  # noqa: BLE001
                    logger.debug("Cache clear failed (non-fatal): %s", e)
                self._cache = None
            try:
                del self._model_instance
            except Exception as e:
                logger.debug(f"Model instance deletion failed (non-fatal): {e}")
            self._model_instance = None
        import gc
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError as e:
            # Intentional no-op (silent-catch audit): torch is an optional
            # accelerator dependency; nothing to release when absent.
            logger.debug(f"torch unavailable -- skipping CUDA cache release: {e}")

    def swap_model(self, model_file: str, n_ctx: int = 0, progress=None) -> dict:
        """Swap to a different model file.

        Acquires ``_inference_lock`` with a 30s timeout before swapping.
        Resolves the new path BEFORE unloading the old model, so a bad
        filename can no longer leave the provider with no model at all.
        On load failure, attempts to roll back to the previous model.

        Parameters
        ----------
        model_file : str
            Model filename (models_dir-relative) or absolute path.
        n_ctx : int
            Override the configured context size when > 0.
        progress : callable, optional
            Zero-arg callback receiving human-readable progress strings.

        Returns a dict with 'ok': bool, and optionally 'error', 'old_model', 'new_model'.
        """
        if not self._inference_lock.acquire(timeout=30):
            old_basename = os.path.basename(self.model_file) if self.model_file else "none"
            return {
                "ok": False,
                "error": "swap_model timed out waiting for inference lock",
                "old_model": old_basename,
                "new_model": model_file,
            }

        try:
            old_path = self._model_path
            old_file = self.model_file
            old_ctx = self.configured_n_ctx
            old_model_name = self._last_model_name

            # Resolve FIRST: never unload a working model for a filename
            # that does not exist — that used to leave the app model-less
            # with only a file-log line as a clue.
            model_path = self._resolve_model_path(model_file)
            if not model_path:
                logger.error(
                    f"swap_model: model file not found: {model_file} "
                    f"(searched in models_dir={self.models_dir}) — keeping current model")
                return {"ok": False, "error": f"Model file not found: {model_file}",
                        "old_model": os.path.basename(old_path) if old_path else None}

            if progress is not None:
                try:
                    progress("⏳ Unloading current model...")
                except Exception as err:  # noqa: BLE001
                    logger.debug(f"Progress callback error: {err}")

            # Same-model noop: already loaded the requested model AND context window hasn't changed
            target_ctx = n_ctx if n_ctx > 0 else (self.configured_n_ctx or CONFIG.get("llm_n_ctx", 0) or CONFIG.get("n_ctx", 0))
            if self._model_instance is not None and old_path and model_path == old_path:
                if target_ctx <= 0 or target_ctx == self.configured_n_ctx:
                    return {
                        "ok": True,
                        "old_model": os.path.basename(old_path),
                        "new_model": os.path.basename(model_path),
                        "load_time_ms": 0,
                        "n_ctx": self.configured_n_ctx,
                        "n_gpu_layers": self.n_gpu_layers,
                    }

            self.unload_model()
            self.model_file = model_file
            if n_ctx > 0:
                self.configured_n_ctx = n_ctx
            elif not self.configured_n_ctx:
                self.configured_n_ctx = CONFIG.get("llm_n_ctx", 0) or CONFIG.get("n_ctx", 0)

            start_t = time.time()
            success = self._load_model(model_path, progress=progress)
            load_time_ms = int((time.time() - start_t) * 1000)

            if not success:
                # Rollback
                if old_path:
                    self.model_file = old_file
                    self.configured_n_ctx = old_ctx
                    self._last_model_name = old_model_name
                    if progress is not None:
                        try:
                            progress("↩️ Rolling back to previous model...")
                        except Exception as err:  # noqa: BLE001
                            logger.debug(f"Progress callback error: {err}")
                    self._load_model(old_path, progress=progress)
                return {"ok": False, "error": f"Failed to load model: {model_file}"}

            return {
                "ok": True,
                "old_model": os.path.basename(old_path) if old_path else None,
                "new_model": os.path.basename(model_path),
                "load_time_ms": load_time_ms,
                "n_ctx": self.configured_n_ctx,
                "n_gpu_layers": self.n_gpu_layers,
            }
        finally:
            self._inference_lock.release()

    def _resolve_model_path(self, model_file: str) -> str:
        """Resolve a model filename to a full path.

        Resolution strategy:
        1. Non-empty string validation.
        2. Absolute path: verify existence (with or without .gguf).
        3. Candidate roots:
           - self.models_dir
           - CONFIG["models_dir"]
           - <repo>/models
           - C:\\KokertechAI\\models
        4. Direct relative join against each candidate root (with and without .gguf).
        5. Recursive search inside candidate roots for a file whose basename
           matches model_file (or model_file.gguf).
        """
        if not model_file or not isinstance(model_file, str):
            return ""

        model_file = model_file.strip()
        if not model_file:
            return ""

        # 1. Absolute path check
        if os.path.isabs(model_file):
            if os.path.isfile(model_file):
                return os.path.abspath(model_file)
            if not model_file.lower().endswith(".gguf"):
                path_with_ext = model_file + ".gguf"
                if os.path.isfile(path_with_ext):
                    return os.path.abspath(path_with_ext)
            return ""

        # 2. Gather candidate root directories
        candidate_dirs: list[str] = []
        if getattr(self, "models_dir", None):
            candidate_dirs.append(self.models_dir)
        try:
            from config import CONFIG
            cfg_dir = CONFIG.get("models_dir")
            if cfg_dir and cfg_dir not in candidate_dirs:
                candidate_dirs.append(cfg_dir)
        except (ImportError, AttributeError):
            # Deliberate (silent-catch audit): models_dir is optional
            # config; the candidate-dir chain continues with defaults.
            pass

        app_dir = os.path.dirname(os.path.abspath(__file__))
        default_models = os.path.join(app_dir, "models")
        if default_models not in candidate_dirs:
            candidate_dirs.append(default_models)
        hardcoded_default = r"C:\KokertechAI\models"
        if hardcoded_default not in candidate_dirs:
            candidate_dirs.append(hardcoded_default)

        # 3. Direct relative join against candidate directories
        for base in candidate_dirs:
            if not base:
                continue
            path = os.path.join(base, model_file)
            if os.path.isfile(path):
                return os.path.abspath(path)
            if not model_file.lower().endswith(".gguf"):
                path2 = path + ".gguf"
                if os.path.isfile(path2):
                    return os.path.abspath(path2)

        # 4. Recursive search across candidate directories for matching basename
        target_name = os.path.basename(model_file).lower()
        target_name_gguf = target_name if target_name.endswith(".gguf") else (target_name + ".gguf")

        for base in candidate_dirs:
            if not base or not os.path.isdir(base):
                continue
            try:
                for dirpath, _dirnames, filenames in os.walk(base):
                    for fn in filenames:
                        fn_lower = fn.lower()
                        if fn_lower == target_name or fn_lower == target_name_gguf:
                            match_path = os.path.join(dirpath, fn)
                            if os.path.isfile(match_path):
                                return os.path.abspath(match_path)
            except OSError:
                continue

        return ""

    def _get_active_n_ctx(self, model_name: str = "") -> int:
        """Return the active context window size in tokens."""
        if (
            self._model_instance is not None
            and type(self._model_instance).__name__ not in ("MagicMock", "Mock", "NonCallableMagicMock")
        ):
            try:
                if callable(getattr(self._model_instance, "n_ctx", None)):
                    val = self._model_instance.n_ctx()
                    if isinstance(val, int) and val > 0:
                        return val
                ctx_attr = getattr(self._model_instance, "_n_ctx", None)
                if isinstance(ctx_attr, int) and ctx_attr > 0:
                    return ctx_attr
            except (AttributeError, TypeError, ValueError, RuntimeError) as err:
                logger.debug("Failed to query active n_ctx from model instance: %s", err)
        if getattr(self, "active_n_ctx", 0) > 0:
            return self.active_n_ctx
        return _resolve_n_ctx(model_name or self._last_model_name, self.configured_n_ctx)

    def _estimate_prompt_tokens(self, messages: list) -> int:
        """Estimate or count prompt token length."""
        if not messages:
            return 0
        parts = []
        for m in messages:
            if not isinstance(m, dict):
                continue
            c = m.get("content", "")
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                for item in c:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        parts.append(item)
            else:
                parts.append(str(c))
        full_text = "\n".join(parts)
        if (
            self._model_instance is not None
            and type(self._model_instance).__name__ not in ("MagicMock", "Mock", "NonCallableMagicMock")
            and callable(getattr(self._model_instance, "tokenize", None))
        ):
            try:
                tokens = self._model_instance.tokenize(full_text.encode("utf-8", errors="ignore"), add_bos=True)
                if isinstance(tokens, (list, tuple)):
                    return len(tokens) + len(messages) * 12
            except (AttributeError, TypeError, ValueError, RuntimeError) as err:
                logger.debug("Failed to tokenize prompt for token estimation: %s", err)
        return int(len(full_text) / 3.2) + len(messages) * 12

    def chat_completion(self, messages: list, model: str = "",
                        temperature: float = 0.7, max_tokens: int = -1,
                        timeout: float = 30, request_id: str = "",
                        cancel_event=None, response_format: dict = None,
                        tools: list = None,
                        cache_prompt: Optional[bool] = None) -> dict:
        """Send a chat completion request to the local model.

        Thread-safe: acquires ``_inference_lock`` for the entire inference.
        """
        # Pre-flight checks (outside lock)
        if cancel_event and cancel_event.is_set():
            return {"error": "Operation cancelled", "content": "", "tool_calls": []}

        messages = _normalize_chat_messages(messages)

        circuit = get_circuit_breaker()
        if circuit.is_open:
            return {"error": "Circuit breaker is open — too many failures", "content": "", "tool_calls": []}

        if not _LLAMA_CPP_AVAILABLE:
            return {"error": "llama-cpp-python not installed", "content": "", "tool_calls": []}

        # Explicit-selection policy (config change 2026-09): no hardcoded
        # default model. An empty selection is refused cleanly; an explicitly
        # applied model (Settings → ⚡ Apply) lazily loads as before.
        if not (self.model_file or "").strip():
            return {"error": "No model selected — choose one in Settings and click Apply",
                    "content": "", "tool_calls": []}

        # Ensure model is loaded (skip path resolution when already set, e.g. mocked in tests)
        if self._model_instance is None:
            model_path = self._resolve_model_path(self.model_file)
            if not model_path:
                return {"error": f"Model file not found: {self.model_file}", "content": "", "tool_calls": []}

        self._inference_lock.acquire()
        prev_cache = None
        try:
            if self._model_instance is None:
                if not self._load_model(model_path):
                    return {"error": f"Failed to load model: {model_path}", "content": "", "tool_calls": []}

            # If cache_prompt is explicitly False, temporarily detach cache
            if cache_prompt is False and self._model_instance and hasattr(self._model_instance, "cache"):
                prev_cache = getattr(self._model_instance, "cache", None)
                if hasattr(self._model_instance, "set_cache"):
                    self._model_instance.set_cache(None)

            # Resolve parameters
            model_name = model or self._last_model_name
            n_ctx_val = self._get_active_n_ctx(model_name)
            max_tokens_cap = CONFIG.get("llm_max_tokens_cap", 1024)
            if max_tokens is None or max_tokens <= 0:
                max_tokens = min(n_ctx_val // 2, max_tokens_cap)
            if temperature is None:
                temperature = 0.7

            # Pre-flight context budget clamp:
            # llama.cpp raises ValueError if len(prompt_tokens) + max_tokens > n_ctx.
            # Dynamically clamp max_tokens against remaining budget with 16-token margin.
            approx_prompt_tokens = self._estimate_prompt_tokens(messages)
            remaining_ctx = n_ctx_val - approx_prompt_tokens - 16
            if remaining_ctx > 0:
                max_tokens = max(16, min(max_tokens, remaining_ctx))
            else:
                max_tokens = 16

            # Build llama.cpp kwargs
            kwargs = {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }

            # Handle response_format
            if response_format:
                try:
                    rf = _validate_response_format(response_format)
                except ValueError as e:
                    circuit.record_failure()
                    return {"error": str(e), "content": "", "tool_calls": []}

                fmt_type = rf.get("type", "")
                if fmt_type == "grammar":
                    val = rf.get("value", None)
                    if val is not None and hasattr(val, "_grammar"):
                        kwargs["grammar"] = val
                elif fmt_type == "json_object":
                    kwargs["response_format"] = {"type": "json_object"}
                elif fmt_type == "xml":
                    # PromptBuilder enforces XML formatting via system prompt protocol.
                    # llama-cpp create_chat_completion expects LlamaGrammar object with _grammar attribute;
                    # passing raw GBNF string causes AttributeError: 'str' object has no attribute '_grammar'.
                    pass

            # Handle tools (function calling)
            if tools:
                kwargs["tools"] = tools

            # Call llama.cpp
            try:
                response = self._model_instance.create_chat_completion(**kwargs)
            except Exception as e:
                err_str = str(e)
                if not ("context window" in err_str.lower() or "requested tokens" in err_str.lower()):
                    circuit.record_failure()
                logger.error(f"Model inference failed: {e}")
                return {"error": err_str, "content": "", "tool_calls": []}

            # Parse response
            if response and "choices" in response and len(response["choices"]) > 0:
                choice = response["choices"][0]
                message = choice.get("message", {})
                content = message.get("content", "")
                raw_tool_calls = message.get("tool_calls") or []
                # Parse tool calls: convert JSON string arguments to dicts
                tool_calls = []
                for tc in raw_tool_calls:
                    parsed = dict(tc)
                    func = dict(tc.get("function", {}))
                    args_str = func.get("arguments", "{}")
                    try:
                        func["arguments"] = json.loads(args_str) if isinstance(args_str, str) else args_str
                    except (json.JSONDecodeError, TypeError):
                        func["arguments"] = {}
                    parsed["function"] = func
                    tool_calls.append(parsed)
                circuit.record_success()
                return {
                    "content": content,
                    "error": None,
                    "tool_calls": tool_calls,
                    "usage": response.get("usage", {}),
                }
            else:
                circuit.record_failure()
                return {"error": "Empty model response", "content": "", "tool_calls": []}

        except Exception as e:
            circuit.record_failure()
            logger.error(f"chat_completion error: {e}")
            return {"error": str(e), "content": "", "tool_calls": []}
        finally:
            if prev_cache is not None and self._model_instance and hasattr(self._model_instance, "set_cache"):
                self._model_instance.set_cache(prev_cache)
            self._inference_lock.release()

    def chat_completion_stream(self, messages: list, model: str = "",
                                temperature: float = 0.7, max_tokens: int = -1,
                                timeout: float = 30, request_id: str = "",
                                cancel_event=None, response_format: dict = None,
                                tools: list = None,
                                cache_prompt: Optional[bool] = None):
        """Stream a chat completion response token by token.

        Thread-safe: acquires ``_inference_lock`` for the entire stream.
        Yields dicts with 'token' and optionally 'error'.
        """
        # Pre-flight checks (outside lock)
        if cancel_event and cancel_event.is_set():
            yield {"error": "Operation cancelled"}
            return

        messages = _normalize_chat_messages(messages)

        circuit = get_circuit_breaker()
        if circuit.is_open:
            yield {"error": "Circuit breaker is open"}
            return

        if not _LLAMA_CPP_AVAILABLE:
            yield {"error": "llama-cpp-python not installed"}
            return

        # Explicit-selection policy: empty selection refused cleanly; an
        # explicitly applied model lazily loads (see chat_completion).
        if not (self.model_file or "").strip():
            yield {"error": "No model selected — choose one in Settings and click Apply"}
            return

        model_path = self._resolve_model_path(self.model_file)
        if not model_path:
            yield {"error": f"Model file not found: {self.model_file}"}
            return

        self._inference_lock.acquire()
        prev_cache = None
        try:
            if self._model_instance is None or self._model_path != model_path:
                if not self._load_model(model_path):
                    yield {"error": f"Failed to load model: {model_path}"}
                    return

            # If cache_prompt is explicitly False, temporarily detach cache
            if cache_prompt is False and self._model_instance and hasattr(self._model_instance, "cache"):
                prev_cache = getattr(self._model_instance, "cache", None)
                if hasattr(self._model_instance, "set_cache"):
                    self._model_instance.set_cache(None)

            model_name = model or self._last_model_name
            n_ctx_val = self._get_active_n_ctx(model_name)
            max_tokens_cap = CONFIG.get("llm_max_tokens_cap", 1024)
            if max_tokens is None or max_tokens <= 0:
                max_tokens = min(n_ctx_val // 2, max_tokens_cap)
            if temperature is None:
                temperature = 0.7

            # Pre-flight context budget clamp:
            # llama.cpp raises ValueError if len(prompt_tokens) + max_tokens > n_ctx.
            # Dynamically clamp max_tokens against remaining budget with 16-token margin.
            approx_prompt_tokens = self._estimate_prompt_tokens(messages)
            remaining_ctx = n_ctx_val - approx_prompt_tokens - 16
            if remaining_ctx > 0:
                max_tokens = max(16, min(max_tokens, remaining_ctx))
            else:
                max_tokens = 16

            kwargs = {
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            }

            if response_format:
                try:
                    rf = _validate_response_format(response_format)
                except ValueError as e:
                    circuit.record_failure()
                    yield {"error": str(e)}
                    return
                fmt_type = rf.get("type", "")
                if fmt_type == "grammar":
                    val = rf.get("value", None)
                    if val is not None and hasattr(val, "_grammar"):
                        kwargs["grammar"] = val
                elif fmt_type == "json_object":
                    kwargs["response_format"] = {"type": "json_object"}
                elif fmt_type == "xml":
                    # PromptBuilder enforces XML formatting via system prompt protocol.
                    # llama-cpp create_chat_completion expects LlamaGrammar object with _grammar attribute;
                    # passing raw GBNF string causes AttributeError: 'str' object has no attribute '_grammar'.
                    pass

            if tools:
                kwargs["tools"] = tools

            try:
                stream = self._model_instance.create_chat_completion(**kwargs)
                for chunk in stream:
                    if cancel_event and cancel_event.is_set():
                        yield {"error": "Operation cancelled"}
                        return
                    if "choices" in chunk and len(chunk["choices"]) > 0:
                        delta = chunk["choices"][0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            yield {"token": token}
            except Exception as e:
                err_str = str(e)
                if not ("context window" in err_str.lower() or "requested tokens" in err_str.lower()):
                    circuit.record_failure()
                logger.error(f"Stream inference failed: {e}")
                yield {"error": err_str}
                return

            circuit.record_success()
        finally:
            if prev_cache is not None and self._model_instance and hasattr(self._model_instance, "set_cache"):
                self._model_instance.set_cache(prev_cache)
            self._inference_lock.release()

    def _handle_error(self, error_msg: str) -> dict:
        """Return a standardized error dict with tool_calls field."""
        return {"error": error_msg, "content": "", "tool_calls": []}

    @staticmethod
    def scan_models(directory: str) -> list[dict[str, str]]:
        """Scan a directory (recursively) for GGUF model files.

        Returns a list of dicts with ``"name"`` and ``"path"`` keys, sorted
        alphabetically by name. ``"name"`` is the display name: the file name
        alone when unique across the scan, otherwise the models_dir-relative
        path (subdirectory included) so entries like
        ``FamilyA/model.gguf`` and ``FamilyB/model.gguf`` stay distinct.
        The ``"path"`` key is always the absolute path.
        """
        models: list[dict[str, str]] = []
        _basename_counts: dict[str, int] = {}
        if not os.path.isdir(directory):
            return models
        try:
            for dirpath, _dirnames, filenames in os.walk(directory):
                for fn in filenames:
                    if not fn.lower().endswith(".gguf"):
                        continue
                    full_path = os.path.join(dirpath, fn)
                    if not os.path.isfile(full_path):
                        continue
                    # Display name: bare filename unless a same-named file
                    # lives in another subdirectory of the scan.
                    if fn in _basename_counts:
                        _basename_counts[fn] += 1
                    else:
                        _basename_counts[fn] = 1
                    models.append({"name": fn, "path": full_path})
        except OSError as e:
            # Breadcrumb (silent-catch audit): a walk failure silently
            # shrinks the model list -- user sees 'no models' with no cause.
            logger.warning(f"Model scan of {directory} aborted early: {e}")
        # Resolve duplicate basenames to models_dir-relative display names.
        if _basename_counts:
            for m in models:
                if _basename_counts.get(m["name"], 0) > 1:
                    m["name"] = os.path.relpath(m["path"], directory)
        return models

    def health_check(self) -> dict:
        """Return a dict with provider health diagnostics.

        Reports: model loaded state, model name, context window, GPU layers,
        VRAM usage, and a latency benchmark (single token generation time).

        If the model has not been loaded yet (lazy-load on first inference),
        this method triggers a load so the returned diagnostics reflect the
        actual model state rather than reporting ``ok=False, n_ctx=0``.

        Thread-safety: the 1-token benchmark acquires ``_inference_lock``
        with ``blocking=False`` so it cannot race with a concurrent
        ``chat_completion`` call. If the lock is held, the benchmark
        is skipped (latency_ms stays 0) to avoid blocking the caller.
        """
        # Health-check may load an explicitly-selected model so the first
        # call returns accurate state. With no selection it reports the
        # real (not-loaded) state — no hardcoded default is ever loaded.
        if self._model_instance is None and self.model_file:
            model_path = self._resolve_model_path(self.model_file)
            if model_path:
                self._load_model(model_path)

        loaded = self._model_instance is not None
        model_path = self._model_path or ""
        model_name = os.path.basename(model_path) if model_path else ""
        kv_cache_active = bool(getattr(self, "_cache", None) is not None)
        kv_cache_size = getattr(self._cache, "cache_size", 0) if kv_cache_active else 0
        kv_cache_entries = (
            len(self._cache.cache_state)
            if kv_cache_active and hasattr(self._cache, "cache_state") and isinstance(self._cache.cache_state, dict)
            else 0
        )

        result = {
            "ok": loaded,
            "provider": "local_llm",
            "model_loaded": loaded,
            "model_name": model_name,
            "model_path": model_path,
            "n_ctx": _resolve_n_ctx(model_name, self.configured_n_ctx) if loaded else 0,
            "n_gpu_layers": self.n_gpu_layers if loaded else 0,
            "kv_cache_enabled": kv_cache_active,
            "kv_cache_size_bytes": kv_cache_size,
            "kv_cache_entries": kv_cache_entries,
            "vram_used_mb": 0,
            "latency_ms": 0,
        }

        # VRAM query (best-effort, non-fatal on failure)
        try:
            cmd = ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]
            cf = 0x08000000 if os.name == "nt" else 0
            vram = int(subprocess.check_output(cmd, creationflags=cf).decode().strip())
            result["vram_used_mb"] = vram
        except (OSError, subprocess.SubprocessError, ValueError) as err:
            logger.debug(f"nvidia-smi query failed: {err}")

        # 1-token latency benchmark (non-blocking via _inference_lock)
        acquired = self._inference_lock.acquire(blocking=False)
        if acquired:
            try:
                if self._model_instance is not None:
                    start = time.time()
                    self._model_instance.create_chat_completion(
                        messages=[{"role": "user", "content": "OK"}],
                        max_tokens=1,
                        temperature=0.1,
                    )
                    result["latency_ms"] = round((time.time() - start) * 1000, 1)
            except Exception as e:
                logger.debug(f"Health check benchmark failed (non-fatal): {e}")
                result["latency_error"] = str(e)
            finally:
                self._inference_lock.release()
        # else: lock held by another thread — skip benchmark (latency_ms stays 0)

        return result


def clear_kv_cache():
    """Clear KV prefix cache across all cached local LLM providers."""
    with _PROVIDER_CACHE_LOCK:
        for provider in _PROVIDER_CACHE.values():
            if hasattr(provider, "clear_kv_cache"):
                provider.clear_kv_cache()

