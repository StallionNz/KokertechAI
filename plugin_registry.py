"""
plugin_registry.py — Plugin system for KokertechAI.

Architecture (KNOWLEDGE.md §6):
- 28 native plugins in plugins/ directory
- Each plugin exports: COMMAND_NAME, execute(intent_json), SCHEMA (optional),
  PLUGIN_METADATA (optional)
- Plugins prefixed with _ are skipped (private/utility modules)
- Registry loads all plugins at startup via importlib
- Progressive disclosure: AI sees category summary first, requests schemas via
  <<LOAD_TOOLS:category_name>>

Contract (from L99 Sprint 1, fail-soft best-effort type-safety):
- SCHEMA may be absent (plugin is still allowed).
- If present, it MUST be a dict with string keys.
- ``json.dumps(raw, default=str)`` MUST succeed.
"""
from __future__ import annotations

import json
import os
import runpy
import threading
import time
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from config import CONFIG, WORKSPACE_DIR
from logging_config import get_logger

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

PLUGIN_DIR = os.path.join(WORKSPACE_DIR, "plugins")
MAX_SCHEMAS_PER_LOAD = 15
_EXPOSED_TOOLS: Dict[str, Any] = {}

logger = get_logger(name="PluginRegistry")


# ---------------------------------------------------------------------------
# Permission — capabilities a plugin must declare before using them
# ---------------------------------------------------------------------------

class Permission(str, Enum):
    """Capabilities a plugin must declare before using them."""
    FS_READ = "fs_read"
    FS_WRITE = "fs_write"
    NETWORK = "network"
    SHELL = "shell"
    SCREEN = "screen"
    SYSTEM = "system"
    MEMORY = "memory"
    AI = "ai"
    CLIPBOARD = "clipboard"

    @classmethod
    def all_values(cls) -> set:
        """Return the set of all known permission string values."""
        return {m.value for m in cls}

    @classmethod
    def validate(cls, permissions: list) -> list:
        """Return list of invalid permission strings."""
        valid = cls.all_values()
        return [p for p in permissions if p not in valid]


# ---------------------------------------------------------------------------
# SchemaError — raised when a plugin advertises an invalid schema
# ---------------------------------------------------------------------------

class SchemaError(Exception):
    """Raised when a plugin advertises an invalid schema."""


# ---------------------------------------------------------------------------
# PluginSchema — validation wrapper for plugin schemas
# ---------------------------------------------------------------------------

class PluginSchema:
    """Validation wrapper for plugin schemas.

    Contract (L99 Sprint 1, fail-soft best-effort type-safety):
    - SCHEMA may be absent (plugin is still allowed).
    - If present, it MUST be a dict.
    - Dict keys MUST be strings (used as LLM prompt parameter names).
    - Values MAY be of any JSON-serializable type.
    - ``json.dumps(raw, default=str)`` MUST succeed.
    """

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise SchemaError("SCHEMA must be a dict")
        self.validate()

    def validate(self) -> None:
        """Fail-soft validation: records errors but never raises."""
        raw = self.raw
        for key in raw:
            if not isinstance(key, str):
                raise SchemaError("schema keys must be strings")
        try:
            json.dumps(raw, default=str)
        except Exception as e:
            raise SchemaError(f"schema must be JSON-serializable: {e}")


# ---------------------------------------------------------------------------
# PluginRegistry
# ---------------------------------------------------------------------------

class PluginRegistry:
    """Registry for loading, managing, and executing plugins."""

    def __init__(self):
        # Plugin storage
        self.plugins: Dict[str, Any] = {}          # cmd_name -> execute callable
        self.schemas: Dict[str, dict] = {}          # cmd_name -> schema dict
        self.metadata: Dict[str, dict] = {}         # cmd_name -> metadata dict
        self.disabled: set = set()                   # set of disabled cmd_names
        self.errors: List[str] = []                  # load error strings

        # Execution tracking (thread-safe — protected by _exec_lock)
        self._exec_lock = threading.Lock()
        self._execution_errors: Dict[str, dict] = {}    # cmd -> {count, last_error, last_time}
        self._execution_successes: Dict[str, dict] = {} # cmd -> {count, last_time}
        self._execution_log: List[dict] = []            # audit trail entries
        self._execution_log_max = 500                   # max log entries

        # LLM tools cache (thread-safe — protected by _cache_lock)
        self._cache_lock = threading.Lock()
        self._tools_llm_cache: Dict[tuple, list] = {}

        # Load plugins
        self._load_plugins()

    # -----------------------------------------------------------------------
    # Plugin loading
    # -----------------------------------------------------------------------

    def _load_plugins(self):
        """Discover and load all plugins from PLUGIN_DIR.

        Skips files starting with '_' (private/utility modules).
        Each plugin must export COMMAND_NAME + execute(intent_json).
        Optional exports: SCHEMA (dict), PLUGIN_METADATA (dict).
        """
        if not os.path.isdir(PLUGIN_DIR):
            return

        for filename in sorted(os.listdir(PLUGIN_DIR)):
            if not filename.endswith(".py"):
                continue
            if filename.startswith("_"):
                continue
            if filename == "__init__.py":
                continue

            filepath = os.path.join(PLUGIN_DIR, filename)
            plugin_name = filename[:-3]  # strip .py

            try:
                module_ns = runpy.run_path(filepath)
            except Exception as e:
                self.errors.append(f"{filename}: {e}")
                continue

            # Required: COMMAND_NAME
            cmd = module_ns.get("COMMAND_NAME")
            if not cmd:
                self.errors.append(f"{filename}: missing COMMAND_NAME")
                continue

            # Required: execute().  Missing execute is silently skipped
            # (runpy.run_path returns None for missing names); a non-callable
            # execute (e.g. int, str) is tracked as an error.
            execute_fn = module_ns.get("execute")
            if execute_fn is None:
                continue
            if not callable(execute_fn):
                self.errors.append(f"{filename}: execute is not callable")
                continue

            # Optional: SCHEMA
            raw_schema = module_ns.get("SCHEMA")
            try:
                schema = PluginSchema(raw_schema)
            except SchemaError as e:
                self.errors.append(f"{filename}: schema validation failed: {e}")
                raw_schema = None

            # Optional: PLUGIN_METADATA
            meta = module_ns.get("PLUGIN_METADATA")
            if not isinstance(meta, dict):
                # Auto-generate defaults
                meta = {
                    "name": cmd.replace("_", " ").title(),
                    "description": "No description provided.",
                    "version": "0.0.0",
                    "tags": [],
                }
            else:
                # Fill in missing keys
                meta.setdefault("name", cmd.replace("_", " ").title())
                meta.setdefault("description", "No description provided.")
                meta.setdefault("version", "0.0.0")
                meta.setdefault("tags", [])

            # Validate declared permissions
            declared = meta.get("permissions", [])
            if declared:
                invalid = Permission.validate(declared)
                if invalid:
                    self.errors.append(
                        f"{filename}: unknown permissions: {invalid}"
                    )

            # Register
            self.plugins[cmd] = execute_fn
            self.metadata[cmd] = meta
            if raw_schema is not None:
                self.schemas[cmd] = raw_schema
                meta["schema"] = raw_schema

    # -----------------------------------------------------------------------
    # Plugin execution
    # -----------------------------------------------------------------------

    @staticmethod
    def _summarize_params(intent_json: dict) -> str:
        """Build a compact summary of tool call params for logging."""
        parts = []
        for k, v in intent_json.items():
            if k == "action":
                continue
            s = str(v)
            if len(s) > 40:
                s = s[:37] + "..."
            parts.append(f"{k}={s}")
        return ", ".join(parts)

    def execute_command(self, intent_json: dict) -> str:
        """Execute a plugin by action name.

        Args:
            intent_json: Dict with at least {"action": "COMMAND_NAME", ...}.
                         The full dict is passed through to plugin.execute().

        Returns:
            Result string from the plugin, or an error description string.
        """
        action = intent_json.get("action", "")
        if not action:
            return "[ERROR] Action '' unknown or not registered."

        if action not in self.plugins:
            return f"[ERROR] Action '{action}' unknown or not registered."

        if action in self.disabled:
            return f"[ERROR] Plugin '{action}' is disabled."

        # Check permissions (strict mode)
        strict = CONFIG.get("strict_plugin_permissions", False)
        meta = self.metadata.get(action, {})
        declared = meta.get("permissions", [])
        if strict and not declared:
            return (
                f"[ERROR] Plugin '{action}' has no declared permissions. "
                f"Strict mode requires all plugins to declare permissions."
            )
        if not declared and not strict:
            logger.warning(
                f"[PERM] Plugin '{action}' has no declared permissions "
                f"(add 'permissions' list to PLUGIN_METADATA)"
            )

        # Execute
        t0 = time.time()
        params_summary = self._summarize_params(intent_json)
        logger.info(f"[TOOL] START {action} {params_summary}")

        try:
            result = self.plugins[action](intent_json)
            elapsed_ms = round((time.time() - t0) * 1000)
        except Exception as e:
            elapsed_ms = round((time.time() - t0) * 1000)
            logger.error(
                f"[TOOL] FAIL {action} in {elapsed_ms}ms: {e}"
            )
            self._track_execution_error(action, str(e))
            self._log_execution(action, params_summary, False, elapsed_ms)
            return f"[EXECUTION FATAL ERROR] Plugin {action}: {e}"

        logger.info(f"[TOOL] DONE {action} in {elapsed_ms}ms")

        # Track success or error (returned [ERROR] string counts as error)
        if isinstance(result, str) and result.startswith("[ERROR]"):
            self._track_execution_error(action, result)
            self._log_execution(action, params_summary, False, elapsed_ms)
        else:
            self._track_execution_success(action)
            self._log_execution(action, params_summary, True, elapsed_ms)

        return result

    def execute(self, action: str, params: Optional[dict] = None) -> str:
        """Execute a plugin by action name with optional parameters dict.

        Convenience wrapper around execute_command().

        Args:
            action: Command name (e.g. 'SYSTEM_STATUS').
            params: Optional dict of parameter arguments passed to the plugin.

        Returns:
            Result string from the plugin, or an error description string.
        """
        payload = {"action": action}
        if params and isinstance(params, dict):
            payload.update(params)
        return self.execute_command(payload)

    # -----------------------------------------------------------------------
    # Execution error / success tracking
    # -----------------------------------------------------------------------

    def _track_execution_error(self, cmd: str, error_msg: str):
        """Record a runtime execution error for a plugin."""
        with self._exec_lock:
            if cmd not in self._execution_errors:
                self._execution_errors[cmd] = {
                    "count": 0,
                    "last_error": "",
                    "last_time": "",
                }
            entry = self._execution_errors[cmd]
            entry["count"] += 1
            entry["last_error"] = error_msg
            entry["last_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _track_execution_success(self, cmd: str):
        """Record a successful execution for a plugin."""
        with self._exec_lock:
            if cmd not in self._execution_successes:
                self._execution_successes[cmd] = {
                    "count": 0,
                    "last_time": "",
                }
            entry = self._execution_successes[cmd]
            entry["count"] += 1
            entry["last_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _log_execution(
        self, cmd: str, params: str, success: bool, duration_ms: int
    ):
        """Record a single execution in the audit trail."""
        with self._exec_lock:
            entry = {
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "command": cmd,
                "params": params,
                "success": success,
                "duration_ms": duration_ms,
            }
            self._execution_log.append(entry)
            if len(self._execution_log) > self._execution_log_max:
                self._execution_log.pop(0)

    def get_execution_errors(self, cmd: str = None) -> Any:
        """Return execution error info for a plugin, or all plugins.

        Args:
            cmd: Optional command name filter.

        Returns:
            For a specific cmd: dict with {count, last_error, last_time} or None.
            For all (cmd=None): dict of cmd -> {count, last_error, last_time}.
        """
        with self._exec_lock:
            if cmd:
                return self._execution_errors.get(cmd)
            return dict(self._execution_errors)

    def clear_execution_errors(self, cmd: str = None):
        """Clear execution errors for a specific plugin or all plugins."""
        with self._exec_lock:
            if cmd:
                self._execution_errors.pop(cmd, None)
                self._execution_successes.pop(cmd, None)
            else:
                self._execution_errors.clear()
                self._execution_successes.clear()

    def get_execution_successes(self, cmd: str = None) -> Any:
        """Return execution success counts for a plugin, or all plugins.

        Returns:
            For a specific cmd: dict with {count, last_time} or None.
            For all (cmd=None): dict of cmd -> {count, last_time}.
        """
        with self._exec_lock:
            if cmd:
                return self._execution_successes.get(cmd)
            return dict(self._execution_successes)

    # -----------------------------------------------------------------------
    # Enable / Disable
    # -----------------------------------------------------------------------

    def enable(self, cmd: str):
        """Enable a plugin (remove from disabled set)."""
        self.disabled.discard(cmd)
        self._invalidate_tools_cache()

    def disable(self, cmd: str):
        """Disable a plugin (add to disabled set)."""
        self.disabled.add(cmd)
        self._invalidate_tools_cache()

    def is_enabled(self, cmd: str) -> bool:
        """Check if a plugin is currently enabled."""
        return cmd not in self.disabled

    def toggle(self, cmd: str) -> bool:
        """Toggle a plugin's enabled state. Returns new state (True=enabled)."""
        if self.is_enabled(cmd):
            self.disable(cmd)
            return False
        else:
            self.enable(cmd)
            return True

    def get_enabled_count(self) -> int:
        """Return the number of enabled plugins."""
        return sum(1 for c in self.plugins if c not in self.disabled)

    def get_plugin_count(self) -> int:
        """Return the total number of loaded plugins."""
        return len(self.plugins)

    # -----------------------------------------------------------------------
    # Listing
    # -----------------------------------------------------------------------

    def list_plugins(self) -> str:
        """Return a formatted string listing all plugins with status."""
        lines = []
        for cmd in sorted(self.plugins):
            meta = self.metadata.get(cmd, {})
            name = meta.get("name", cmd)
            status = "[OFF]" if cmd in self.disabled else "[ON]"
            tags = ", ".join(meta.get("tags", [])) or "untagged"
            lines.append(f"  {status} {name} ({cmd}) — {tags}")
        if not lines:
            lines.append("  (no plugins loaded)")

        if self.errors:
            lines.append(f"\nLoad Errors ({len(self.errors)}):")
            for err in self.errors:
                lines.append(f"  - {err}")

        return "Plugins:\n" + "\n".join(lines)

    # -----------------------------------------------------------------------
    # System prompt / LLM tool interfaces
    # -----------------------------------------------------------------------

    def _invalidate_tools_cache(self):
        """Clear the LLM tools cache (called on enable/disable)."""
        with self._cache_lock:
            self._tools_llm_cache.clear()

    def get_system_prompt_addition(self) -> str:
        """Return a JSON array of schemas for enabled plugins.

        Only includes plugins that have a SCHEMA and are enabled.
        """
        items = []
        for cmd, schema in sorted(self.schemas.items()):
            if cmd in self.disabled:
                continue
            items.append({"command": cmd, "schema": schema})
        return json.dumps(items, indent=2, default=str)

    def get_skill_categories(self) -> Dict[str, list]:
        """Group enabled plugins by their primary tag.

        Returns:
            Dict of tag -> list of {"command": cmd, "name": name, "description": desc}.
            Plugins with no tags go to "general".
        """
        cats: Dict[str, list] = {}
        for cmd, meta in sorted(self.metadata.items()):
            if cmd in self.disabled:
                continue
            tags = meta.get("tags", [])
            primary = tags[0] if tags else "general"
            if primary not in cats:
                cats[primary] = []
            cats[primary].append({
                "command": cmd,
                "name": meta.get("name", cmd),
                "description": meta.get("description", ""),
            })
        return cats

    def get_tools_for_llm(
        self, categories: Optional[List[str]] = None
    ) -> List[dict]:
        """Return plugin schemas in OpenAI function-calling format.

        Args:
            categories: Optional list of category tags to filter by.

        Returns:
            List of tools in OpenAI format:
            [{"name": "CMD", "description": "...", "parameters": {"type": "object", "properties": {...}}}]

        Cache-aware: result is cached per (dir_mtime, tuple(categories)).
        """
        # Build cache key
        try:
            dir_mtime = os.path.getmtime(PLUGIN_DIR)
        except OSError:
            dir_mtime = 0
        cache_key = (dir_mtime, tuple(sorted(categories)) if categories else ())

        # Cache read
        with self._cache_lock:
            cached = self._tools_llm_cache.get(cache_key)
            if cached is not None:
                # Return shallow copy so callers don't mutate the cache
                return [dict(tool) for tool in cached]

        # Build
        skill_cats = self.get_skill_categories()
        tools = []

        for tag, entries in sorted(skill_cats.items()):
            if categories and tag not in categories:
                continue
            for entry in entries:
                cmd = entry["command"]
                schema = self.schemas.get(cmd, {})
                props = {}
                required = []
                for key, desc in schema.items():
                    if key == "action":
                        continue
                    # Type inference from description text
                    desc_lower = str(desc).lower()
                    if "boolean" in desc_lower or "flag" in desc_lower:
                        param_type = "boolean"
                    elif any(w in desc_lower for w in ("number", "count", "integer", "int")):
                        param_type = "integer"
                    else:
                        param_type = "string"
                    props[key] = {
                        "type": param_type,
                        "description": str(desc),
                    }
                    required.append(key)

                tools.append({
                    "name": cmd,
                    "description": entry.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                })

        # Enforce MAX_SCHEMAS_PER_LOAD limit
        if len(tools) > MAX_SCHEMAS_PER_LOAD:
            tools = tools[:MAX_SCHEMAS_PER_LOAD]

        # Cache write
        with self._cache_lock:
            self._tools_llm_cache[cache_key] = tools

        # Return shallow copy for caller isolation
        return [dict(tool) for tool in tools]

    def get_category_summary(self) -> str:
        """Return a text summary of available skill categories for the LLM."""
        cats = self.get_skill_categories()
        lines = ["Available Skill Categories:"]
        for tag in sorted(cats):
            entries = cats[tag]
            cmds = ", ".join(e["command"] for e in entries)
            lines.append(f"  [{tag}] ({len(entries)} tools): {cmds}")
        if not cats:
            lines.append("  (no tools available)")
        return "\n".join(lines)

    def progressive_disclosure_system_prompt(self) -> str:
        """Return the progressive disclosure system prompt for the LLM.

        Gives the AI a high-level category view and instructions on how to
        request full tool schemas via <<LOAD_TOOLS:category_name>>.
        """
        cats = self.get_skill_categories()
        cat_names = sorted(cats.keys())
        cat_list = ", ".join(cat_names) if cat_names else "(none)"

        lines = [
            "AVAILABLE SKILL CATEGORIES",
            "===========================",
            self.get_category_summary(),
            "",
            "TO LOAD DETAILED TOOL SCHEMAS:",
        ]
        for tag in cat_names:
            lines.append(f"  Use <<LOAD_TOOLS:{tag}>> to load {tag} tools.")
        if not cat_names:
            lines.append("  (no tools available — <<LOAD_TOOLS:category>> will be ignored)")
        # Combine all categories (general is always available, skip it)
        specific = [t for t in cat_names if t != "general"]
        if len(specific) > 1:
            # Reverse sort so broader/common tags come first (e.g. web,memory)
            specific_sorted = sorted(specific, reverse=True)
            lines.append(
                f"  Use <<LOAD_TOOLS:{','.join(specific_sorted)}>>"
                f" to load all specific tools."
            )
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    # Health scores
    # -----------------------------------------------------------------------

    def get_plugin_health_scores(self) -> Dict[str, dict]:
        """Compute health scores for every registered plugin.

        Scoring formula (weighted composite 0–100):
          - success_rate (50%): successes / total × 100 (defaults to 100).
          - latency_score (30%): 30 × (1 − avg_latency_ms / 5000), clamped at 0.
          - error_penalty (20%): 20 pts if zero recent errors, 0 otherwise.

        Returns:
            dict of cmd -> {score, status, success_rate, avg_latency_ms,
                            total_execs, recent_errors, status_label}
        """
        with self._exec_lock:
            all_log = list(self._execution_log)

        scores = {}
        # Group log entries by command
        cmd_logs: Dict[str, list] = {}
        for entry in all_log:
            cmd = entry["command"]
            cmd_logs.setdefault(cmd, []).append(entry)

        for cmd in sorted(self.plugins):
            entries = cmd_logs.get(cmd, [])
            total = len(entries)
            if total == 0:
                scores[cmd] = {
                    "score": 0,
                    "status": "unknown",
                    "status_label": "⚪ Unknown",
                    "success_rate": 0,
                    "avg_latency_ms": 0,
                    "total_execs": 0,
                    "recent_errors": 0,
                }
                continue

            successes = sum(1 for e in entries if e.get("success"))
            success_rate = round(successes / total * 100, 1) if total > 0 else 100
            durations = [e.get("duration_ms", 0) for e in entries]
            avg_latency = round(sum(durations) / len(durations), 1) if durations else 0

            # Recent errors (last 10 entries)
            recent = entries[-10:]
            recent_errors = sum(1 for e in recent if not e.get("success"))

            # Composite score
            sr_component = round(success_rate * 0.5, 1)
            latency_component = round(
                max(0, 30 * (1 - avg_latency / 5000)), 1
            )
            error_component = 20 if recent_errors == 0 else 0
            score = min(100, round(sr_component + latency_component + error_component))

            # Status threshold
            if score >= 80:
                status = "healthy"
                status_label = "🟢 Healthy"
            elif score >= 50:
                status = "degraded"
                status_label = "🟡 Degraded"
            else:
                status = "unhealthy"
                status_label = "🔴 Unhealthy"

            scores[cmd] = {
                "score": score,
                "status": status,
                "status_label": status_label,
                "success_rate": success_rate,
                "avg_latency_ms": avg_latency,
                "total_execs": total,
                "recent_errors": recent_errors,
            }

        return scores

    def get_execution_log(
        self, limit: int = 100, cmd_filter: str = None
    ) -> List[dict]:
        """Return audit trail entries, most recent first.

        Args:
            limit: Max entries to return (default 100).
            cmd_filter: Optional command name to filter by.

        Returns:
            List of dicts with keys: ts, command, params, result, success,
            duration_ms.
        """
        with self._exec_lock:
            entries = list(self._execution_log)
        if cmd_filter:
            entries = [e for e in entries if e["command"] == cmd_filter]
        return list(reversed(entries))[-limit:]


# ---------------------------------------------------------------------------
# Singleton access
# ---------------------------------------------------------------------------

def _get_default_registry() -> PluginRegistry:
    """Return the module-level singleton PluginRegistry instance."""
    return registry


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

registry: PluginRegistry = PluginRegistry()


# ---------------------------------------------------------------------------
# tool() decorator — auto-register a function as a plugin
# ---------------------------------------------------------------------------

def tool(
    action: str,
    description: str = "",
    version: str = "0.1.0",
    tags: list = None,
    schema: dict = None,
    permissions: list = None,
):
    """Decorator to auto-register a function as a plugin.

    Generates COMMAND_NAME, SCHEMA, and PLUGIN_METADATA for the decorated
    function, making it discoverable by the plugin registry.

    Args:
        action: Command name.
        description: Human-readable description.
        version: Semantic version string (default "0.1.0").
        tags: List of category tags (first tag = primary category).
        schema: Optional schema dict (auto-generated from function signature).
        permissions: List of Permission values.
    """
    tags = tags or []

    def decorator(fn):
        import inspect

        # Auto-generate schema from function signature
        if schema is None:
            sig = inspect.signature(fn)
            params = {}
            for name, param in sig.parameters.items():
                if name in ("intent", "intent_json"):
                    # These are the standard param names for plugin execute()
                    # — skip them; tool schemas describe keys inside the dict
                    continue
                params[name] = param.annotation.__name__ if hasattr(
                    param.annotation, "__name__"
                ) else str(param.annotation) if param.annotation != inspect.Parameter.empty else "string"
            auto_schema = {"action": action}
            auto_schema.update(params)
        else:
            auto_schema = schema

        fn.COMMAND_NAME = action
        fn.SCHEMA = auto_schema

        # Build metadata
        meta_desc = description or (fn.__doc__ or "").strip().split("\n")[0]
        fn.PLUGIN_METADATA = {
            "name": action.replace("_", " ").title(),
            "description": meta_desc or "No description provided.",
            "version": version,
            "tags": tags,
            "permissions": permissions or [],
        }

        # Register in global tool registry
        _EXPOSED_TOOLS[action] = {
            "fn": fn,
            "metadata": fn.PLUGIN_METADATA,
        }

        return fn

    return decorator


# ---------------------------------------------------------------------------
# Tool usage counts
# ---------------------------------------------------------------------------

def get_tool_usage_counts() -> dict:
    """Return per-tool usage statistics combining errors and successes.

    Returns:
        dict of action_name -> {successes: int, errors: int, last_used: str}.
    """
    result = {}
    all_cmds = set(registry._execution_successes.keys()) | set(
        registry._execution_errors.keys()
    )
    for cmd in sorted(all_cmds):
        err_info = registry._execution_errors.get(cmd, {})
        suc_info = registry._execution_successes.get(cmd, {})
        err_time = err_info.get("last_time", "")
        suc_time = suc_info.get("last_time", "")
        result[cmd] = {
            "successes": suc_info.get("count", 0),
            "errors": err_info.get("count", 0),
            "last_used": max(err_time, suc_time),
        }
    return result
