"""
tool_use_agent.py — Sprint 12: Enhanced ToolUseAgent.

Extracted from sub_agents.py with these enhancements:
- Multi-tool parallel dispatch (#6): when LLM emits multiple <<TOOL_CALL:...>>
  directives, execute them concurrently via ThreadPoolExecutor.
- Tool call retries with exponential backoff (#7): retry failed tools up to 3 times.
- Tool call budget (#8): max_total_calls CONFIG key prevents runaway loops.
- LLM-as-judge for tool results (#9): summarize long results (>500 chars) via
  a fast model to save context window.
- Tool use audit log (#10): write every dispatch + result to JSONL.
- Per-tool timeout, result cache, streaming progress, destructive guard.
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional, Callable

from ai_base import get_provider, convert_plugin_schema_to_openai_tool
from config import CONFIG, WORKSPACE_DIR
from logging_config import get_logger

logger = get_logger(name="ToolUseAgent")

_TOOL_SCHEMA_CACHE = {"data": None, "ts": 0.0}
_TOOL_SCHEMA_CACHE_TTL = 30.0
_TOOL_SCHEMA_CACHE_LOCK = threading.Lock()


def _get_cached_tool_schemas():
    now = time.time()
    with _TOOL_SCHEMA_CACHE_LOCK:
        if (_TOOL_SCHEMA_CACHE["data"] is not None and
                now - _TOOL_SCHEMA_CACHE["ts"] < _TOOL_SCHEMA_CACHE_TTL):
            return _TOOL_SCHEMA_CACHE["data"]
    from plugin_registry import registry
    schemas = registry.get_tools_for_llm()
    with _TOOL_SCHEMA_CACHE_LOCK:
        _TOOL_SCHEMA_CACHE["data"] = schemas
        _TOOL_SCHEMA_CACHE["ts"] = now
    return schemas


def clear_tool_schema_cache():
    global _TOOL_SCHEMA_CACHE
    with _TOOL_SCHEMA_CACHE_LOCK:
        _TOOL_SCHEMA_CACHE = {"data": None, "ts": 0.0}


class ToolUseAgent:
    _DESTRUCTIVE_ACTIONS = {
        "FILE_DELETE", "FILE_WRITE", "CODE_EXECUTE", "SYSTEM_COMMAND",
        "SHELL_EXECUTE", "PIP_INSTALL", "NPM_INSTALL", "GIT_PUSH",
        "GIT_FORCE_PUSH", "DOCKER_RUN", "DOCKER_PRUNE",
    }

    _TOOLUSER_SYSTEM = (
        "You are a Tool-Use Agent. You have access to tools (plugins) that can "
        "search the web, fetch URLs, read/write files, list directories, execute "
        "code, check system status, and more.\n\n"
        "Your job is to complete the user's task by chaining tool calls.\n"
        "For each step: THINK -> ACT (<<TOOL_CALL:{...}>>) -> OBSERVE -> REPEAT.\n"
        "You can call MULTIPLE tools in one response — separate each with a newline."
    )

    _AUDIT_LOG_NAME = "data/tool_use_audit.jsonl"

    def __init__(self, provider_name: str = None, model: str = None):
        self.provider_name = provider_name or CONFIG.get("active_provider", "local_llm")
        self.model = model or CONFIG.get("model_name", "")
        self._tool_result_cache: dict = {}

    @classmethod
    def _is_destructive_tool(cls, intent: dict) -> bool:
        action = str(intent.get("action", "")).upper()
        if action in cls._DESTRUCTIVE_ACTIONS:
            return True
        cmd = intent.get("cmd") or intent.get("command") or intent.get("code") or intent.get("content") or ""
        if cmd:
            try:
                from services.tool_execution_service import CommandPolicyGuard, RiskLevel
                risk, _ = CommandPolicyGuard.screen_command(str(cmd))
                if risk == RiskLevel.DESTRUCTIVE:
                    return True
            except (ImportError, AttributeError):
                pass
        return False

    def _audit_log(self, entry: dict) -> None:
        if not CONFIG.get("tool_use_audit_log", False):
            return
        try:
            workspace = WORKSPACE_DIR
            audit_path = os.path.join(workspace, self._AUDIT_LOG_NAME)
            os.makedirs(os.path.dirname(audit_path), exist_ok=True)
            entry["ts"] = datetime.utcnow().isoformat() + "Z"
            with open(audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except (OSError, TypeError, ValueError) as e:
            logger.debug(f"Audit log write failed (non-fatal): {e}")

    @staticmethod
    def _progress(msg: str, stream_callback: Optional[Callable] = None) -> None:
        if CONFIG.get("tool_use_stream_progress", True) and stream_callback:
            stream_callback(msg)
        logger.info(msg)

    def _summarize_tool_result(self, result: str, already_summarized: bool = False) -> str:
        if not CONFIG.get("tool_use_summarize_results", True):
            return result
        if already_summarized:
            return result  # Already summarized on first computation
        if len(result) <= 500:
            return result
        try:
            provider = get_provider(name=self.provider_name, default_model=self.model)
            r = provider.chat_completion(
                messages=[
                    {"role": "system", "content": "Summarize in 2-3 sentences. Keep all key facts."},
                    {"role": "user", "content": result[:2000]},
                ],
                model=self.model, temperature=0.1, max_tokens=150, timeout=30,
            )
            if not r.get("error") and r.get("content"):
                return f"[SUMMARIZED] {r['content'].strip()}"
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.debug(f"Tool result summarization failed (graceful fallback to truncation): {e}")
        return result[:500] + "...[truncated]"

    def _run_one_tool(self, intent: dict, tool_timeout: int, max_retries: int) -> str:
        from plugin_registry import registry
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                tool_exception = [None]
                tool_output = [None]

                def _run():
                    try:
                        tool_output[0] = registry.execute_command(intent)
                    except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError, IndexError) as ex:
                        tool_exception[0] = ex

                t = threading.Thread(target=_run, daemon=True)
                t.start()
                t.join(timeout=tool_timeout)
                if t.is_alive():
                    return f"[TIMEOUT] '{intent.get('action','?')}' exceeded {tool_timeout}s (attempt {attempt}/{max_retries})"
                if tool_exception[0] is not None:
                    raise tool_exception[0]
                return tool_output[0] or ""

            except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError, IndexError) as e:
                last_error = str(e)
                if attempt < max_retries:
                    time.sleep(2 ** (attempt - 1))
                else:
                    return f"[ERROR] '{intent.get('action','?')}' failed after {max_retries} attempts: {last_error}"
        # Unreachable: every path above returns

    def execute(self, task: str, timeout: int = 300,
                stream_callback: Optional[Callable[[str], None]] = None) -> str:
        _p = lambda msg: self._progress(msg, stream_callback)
        destructive_confirm = CONFIG.get("tool_use_destructive_confirm", True)
        allow_destructive = "--allow-destructive" in task
        TOOL_TIMEOUT = int(CONFIG.get("tool_use_per_tool_timeout", 30))
        MAX_RETRIES = int(CONFIG.get("tool_use_max_retries", 3))
        TOOL_CALL_BUDGET = int(CONFIG.get("tool_call_budget", 0))
        self._tool_result_cache.clear()

        try:
            provider = get_provider(name=self.provider_name, default_model=self.model)
            max_turns, total_calls, response = 10, 0, ""
            tool_schemas = _get_cached_tool_schemas()
            system_content = self._TOOLUSER_SYSTEM
            openai_tools = None
            if tool_schemas:
                openai_tools = [convert_plugin_schema_to_openai_tool(s) for s in tool_schemas]
                system_content += (
                    f"\n\n[LOADED TOOL SCHEMAS]:\n{json.dumps(tool_schemas,indent=2)}\n\n"
                    "Call tools via <<TOOL_CALL:{\"action\":\"COMMAND_NAME\",...}>>. One per line."
                )

            messages = [{"role": "system", "content": system_content},
                       {"role": "user", "content": task}]

            for turn in range(max_turns):
                _p(f"Turn {turn+1}/{max_turns}: Thinking...")
                result = provider.chat_completion(
                    messages=messages, model=self.model, temperature=0.2,
                    max_tokens=2000, timeout=timeout,
                    tools=openai_tools if tool_schemas else None,
                )
                if result.get("error"):
                    return f"[ToolUser] Error: {result['error']}"
                response = result.get("content", "")

                from services.prompt_service import PromptService
                tool_calls = PromptService.parse_tool_calls(response)
                if not tool_calls:
                    if "<<TOOL_CALL:" in response:
                        _p("Malformed TOOL_CALL — retrying")
                        messages.append({"role": "assistant", "content": response})
                        messages.append({"role": "user", "content": "Invalid TOOL_CALL JSON. Please fix and retry."})
                        continue
                    return response.strip()

                if TOOL_CALL_BUDGET > 0 and total_calls + len(tool_calls) > TOOL_CALL_BUDGET:
                    _p(f"Budget ({TOOL_CALL_BUDGET}) exceeded. Asking AI to finalize.")
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": "Tool call budget reached. Provide final answer."})
                    continue

                for intent in tool_calls:
                    if destructive_confirm and not allow_destructive and self._is_destructive_tool(intent):
                        refusal = f"[ToolUser] Blocked '{intent.get('action')}'. Add --allow-destructive."
                        _p(refusal)
                        self._audit_log({"event": "blocked", "action": intent.get("action")})
                        return refusal

                # Execute tools
                tool_results: dict = {}
                if len(tool_calls) == 1:
                    intent = tool_calls[0]
                    ck = json.dumps(intent, sort_keys=True)
                    action_name = intent.get("action", "?")
                    if ck in self._tool_result_cache:
                        tool_results[ck] = self._summarize_tool_result(
                            self._tool_result_cache[ck], already_summarized=True)
                        _p(f"{action_name} (cached)")
                    else:
                        _p(f"Calling {action_name}...")
                        self._audit_log({"event": "start", "action": action_name, "intent": intent})
                        raw = self._run_one_tool(intent, TOOL_TIMEOUT, MAX_RETRIES)
                        self._tool_result_cache[ck] = raw
                        tool_results[ck] = self._summarize_tool_result(raw)
                        self._audit_log({"event": "done", "action": action_name, "len": len(str(raw))})
                        _p(f"Result: {str(tool_results[ck])[:200]}")
                    total_calls += 1
                else:
                    _p(f"Dispatching {len(tool_calls)} tools in parallel...")
                    futures_map = {}
                    with ThreadPoolExecutor(max_workers=min(len(tool_calls), 8)) as executor:
                        for intent in tool_calls:
                            ck = json.dumps(intent, sort_keys=True)
                            if ck in self._tool_result_cache:
                                tool_results[ck] = self._summarize_tool_result(
                                    self._tool_result_cache[ck], already_summarized=True)
                                _p(f"  {intent.get('action','?')} (cached)")
                            else:
                                f = executor.submit(self._run_one_tool, intent, TOOL_TIMEOUT, MAX_RETRIES)
                                futures_map[f] = (ck, intent)
                        for f in as_completed(futures_map):
                            ck, intent = futures_map[f]
                            try:
                                raw = f.result()
                            except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError, IndexError) as e:
                                raw = f"[ERROR] {intent.get('action','?')}: {e}"
                            self._tool_result_cache[ck] = raw
                            tool_results[ck] = self._summarize_tool_result(raw)
                            self._audit_log({"event": "done", "action": intent.get("action", "?"), "len": len(str(raw))})
                            total_calls += 1

                messages.append({"role": "assistant", "content": response})
                if len(tool_calls) == 1:
                    messages.append({"role": "user", "content": f"[TOOL RESULT]:\n{tool_results.get(json.dumps(tool_calls[0],sort_keys=True),'')}\n\nComplete or call next tool."})
                else:
                    combined = "\n\n".join(f"[{tc.get('action','?')}]: {tool_results.get(json.dumps(tc,sort_keys=True),'')}" for tc in tool_calls)
                    messages.append({"role": "user", "content": f"[PARALLEL TOOL RESULTS]:\n{combined}\n\nComplete or call next tool."})

            return "[ToolUser] Max turns reached.\n" + response.strip()
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError, IndexError) as e:
            logger.error(f"[ToolUseAgent] Failed: {e}", exc_info=True)
            return f"[ToolUser] Failed: {str(e)}"


_PREWARM_DONE = False
_PREWARM_LOCK = threading.Lock()


def prewarm_tool_schemas() -> None:
    """Pre-populate the tool schema cache synchronously.

    Can be called at any time to force-refresh the tool schema cache.
    Returns immediately if the cache is already populated (within TTL).
    """
    try:
        _get_cached_tool_schemas()
        logger.info("Tool schema cache pre-warmed (synchronous)")
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.warning(f"Tool schema pre-warm failed: {e}")


def _prewarm_cache_async() -> None:
    """Kick off a background thread to pre-fetch tool schemas at import time.

    Controlled by CONFIG["tool_schema_prewarm"] (default True). Uses a small
    delay to let the plugin registry finish initialization before fetching.
    Thread-safe: only one pre-warm thread ever spawned (guarded by _PREWARM_LOCK).
    """
    global _PREWARM_DONE
    if not CONFIG.get("tool_schema_prewarm", True):
        return
    with _PREWARM_LOCK:
        if _PREWARM_DONE:
            return
        _PREWARM_DONE = True

    def _do():
        # Retry up to 3 times with 100ms backoff so plugin_registry has time
        # to finish _load_plugins() even on slower systems.
        last_error = None
        for attempt in range(1, 4):
            time.sleep(0.1 * attempt)
            try:
                _get_cached_tool_schemas()
                logger.info("Tool schema cache pre-warmed (async background)")
                return
            except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
                last_error = e
                logger.debug(
                    f"Tool schema pre-warm attempt {attempt}/3 failed: {e}"
                )
        logger.debug(
            f"Tool schema async pre-warm failed after 3 attempts (non-fatal): "
            f"{last_error}"
        )

    t = threading.Thread(target=_do, daemon=True, name="prewarm-tools")
    t.start()


_prewarm_cache_async()
