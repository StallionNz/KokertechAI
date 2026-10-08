"""test_sprint12_20.py - Sprint 12: Tests for 20-suggestion features.

Tests for:
- @tool decorator (plugin_registry)
- Tool usage counts (plugin_registry)
- Multi-tool parallel dispatch (services/tool_use_agent)
- Retry with exponential backoff (services/tool_use_agent)
- Tool call budget (services/tool_use_agent)
- response_format validation (ai_base)
- Auto-mode structured format (kokertechController)
- Adversarial inputs (services/prompt_service)
- Audit log path uses WORKSPACE_DIR (services/tool_use_agent)
- Success tracking in execute_command (plugin_registry)
"""
import json
import pytest
import os
import sys
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MOCK_SCHEMAS = [
    {"action": "SEARCH", "q": "search query"},
    {"action": "STATUS"},
]


def _tool_response(action, params=None):
    payload = {"action": action}
    if params:
        payload.update(params)
    return {
        "content": f"<<TOOL_CALL:{json.dumps(payload)}>>",
        "error": None,
        "model": "mock",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "tool_calls": [],
    }


def _text_response(text):
    return {
        "content": text,
        "error": None,
        "model": "mock",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "tool_calls": [],
    }


class TestToolDecorator:
    def test_registers(self):
        from plugin_registry import tool, _EXPOSED_TOOLS
        @tool(action="T_DECORATOR_1", description="A test", tags=["test"])
        def fn(intent):
            return "ok"
        assert "T_DECORATOR_1" in _EXPOSED_TOOLS
        assert _EXPOSED_TOOLS["T_DECORATOR_1"]["metadata"]["tags"] == ["test"]
        assert _EXPOSED_TOOLS["T_DECORATOR_1"]["metadata"]["version"] == "0.1.0"
        assert fn.COMMAND_NAME == "T_DECORATOR_1"

    def test_auto_schema(self):
        from plugin_registry import tool
        @tool(action="AUTO_SCHEMA_T")
        def fn(intent):
            return "ok"
        assert fn.SCHEMA["action"] == "AUTO_SCHEMA_T"


class TestToolUsageCounts:
    def test_returns_dict(self):
        from plugin_registry import get_tool_usage_counts
        c = get_tool_usage_counts()
        assert isinstance(c, dict)

    def test_has_successes_and_errors(self):
        from plugin_registry import registry, get_tool_usage_counts
        registry._execution_successes["FAKE_COUNTS"] = {"count": 3, "last_time": "2025-01-01"}
        registry._execution_errors["FAKE_COUNTS"] = {"count": 1, "last_error": "x", "last_time": "2025-01-01"}
        try:
            c = get_tool_usage_counts()
            assert "successes" in c["FAKE_COUNTS"]
            assert "errors" in c["FAKE_COUNTS"]
            assert c["FAKE_COUNTS"]["successes"] == 3
        finally:
            registry._execution_successes.pop("FAKE_COUNTS", None)
            registry._execution_errors.pop("FAKE_COUNTS", None)


class TestParallelDispatch:
    def test_multiple_tool_calls_parallel(self):
        from services.tool_use_agent import ToolUseAgent
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            _tool_response("SEARCH", {"q": "a"}),
            _tool_response("STATUS"),
            _text_response("Task complete."),
        ]
        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas", return_value=MOCK_SCHEMAS):
            agent = ToolUseAgent()
            with patch.object(agent, "model", "test"):
                with patch("plugin_registry.registry.execute_command", return_value="ok"):
                    result = agent.execute("search and status")
                    assert "complete" in result.lower()


class TestRetryBackoff:
    def test_retry_succeeds(self):
        from services.tool_use_agent import ToolUseAgent
        agent = ToolUseAgent()
        fc = [0]

        def flaky(intent):
            fc[0] += 1
            if fc[0] < 3:
                raise RuntimeError("err")
            return "success"

        with patch("plugin_registry.registry.execute_command", side_effect=flaky):
            r = agent._run_one_tool({"action": "T"}, tool_timeout=5, max_retries=3)
            assert r == "success"
            assert fc[0] == 3

    def test_exhausts_retries(self):
        from services.tool_use_agent import ToolUseAgent
        agent = ToolUseAgent()
        with patch("plugin_registry.registry.execute_command", side_effect=RuntimeError("x")):
            r = agent._run_one_tool({"action": "T"}, tool_timeout=5, max_retries=2)
            assert "failed after 2 attempts" in r.lower()


class TestToolBudget:
    def test_budget_enforced(self):
        from services.tool_use_agent import ToolUseAgent
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            _tool_response("SEARCH", {"q": "a"}),
            _tool_response("SEARCH", {"q": "b"}),
            _text_response("Final answer."),
        ]
        cfg = {
            "tool_call_budget": 1, "tool_use_per_tool_timeout": 5,
            "tool_use_max_retries": 2, "tool_use_stream_progress": False,
            "tool_use_destructive_confirm": True, "active_provider": "local_llm",
            "model_name": "", "tool_use_summarize_results": False,
            "tool_use_audit_log": False,
        }
        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas", return_value=MOCK_SCHEMAS), \
             patch.dict("config.CONFIG", cfg):
            agent = ToolUseAgent()
            with patch.object(agent, "model", "test"):
                with patch("plugin_registry.registry.execute_command", return_value="ok"):
                    r = agent.execute("test")
                    assert "final" in r.lower()


class TestResponseFormatValidation:
    def test_valid_json_object(self):
        from ai_base import _validate_response_format
        _validate_response_format({"type": "json_object"})

    def test_valid_grammar(self):
        from ai_base import _validate_response_format
        _validate_response_format({"type": "grammar", "value": "root ::= text"})

    def test_invalid_type(self):
        from ai_base import _validate_response_format
        with pytest.raises(ValueError, match="Unknown response_format type"):
            _validate_response_format({"type": "grammer"})

    def test_grammar_missing_value(self):
        from ai_base import _validate_response_format
        with pytest.raises(ValueError, match="requires a 'value' key"):
            _validate_response_format({"type": "grammar"})

    def test_not_dict(self):
        from ai_base import _validate_response_format
        with pytest.raises(ValueError, match="must be a dict"):
            _validate_response_format("nope")


class TestAutoStructuredFormat:
    def test_extraction_detected(self):
        from kokertechController import _detect_structured_format
        assert _detect_structured_format("extract prices and list as json") == "json"

    def test_thinking_defaults_xml(self):
        from kokertechController import _detect_structured_format
        assert _detect_structured_format("what is life") == "xml"

    def test_single_keyword_insufficient(self):
        from kokertechController import _detect_structured_format
        assert _detect_structured_format("find answer") == "xml"

    def test_multiple_extraction_kw(self):
        from kokertechController import _detect_structured_format
        assert _detect_structured_format("search and extract json data") == "json"


class TestAdversarialInputs:
    def test_malformed_json_tool_call(self):
        from services.prompt_service import PromptService
        assert PromptService.parse_tool_calls("<<TOOL_CALL:{action:SEARCH}>>") == []

    def test_long_action_name(self):
        from services.prompt_service import PromptService
        long_n = "A" * 100
        calls = PromptService.parse_tool_calls(
            f'<<TOOL_CALL:{{"action":"{long_n}","q":"x"}}>>'
        )
        assert len(calls) == 1
        assert len(calls[0]["action"]) == 100

    def test_empty_content_parse(self):
        from services.prompt_service import PromptService
        r = PromptService.parse_ai("", freeform=False)
        assert r["final"] == ""

    def test_max_turns_reached(self):
        from services.tool_use_agent import ToolUseAgent
        provider = MagicMock()
        provider.chat_completion.side_effect = [_tool_response("SEARCH", {"q": "x"})] * 11
        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas", return_value=MOCK_SCHEMAS):
            agent = ToolUseAgent()
            with patch.object(agent, "model", "test"):
                with patch("plugin_registry.registry.execute_command", return_value="ok"):
                    r = agent.execute("test")
                    assert "Max turns" in r

    def test_empty_tool_call_ignored(self):
        from services.prompt_service import PromptService
        assert PromptService.parse_tool_calls('<<TOOL_CALL:{"action":""}>>') == []


class TestAuditLogPath:
    def test_uses_workspace_dir(self):
        from services.tool_use_agent import ToolUseAgent
        from config import WORKSPACE_DIR
        agent = ToolUseAgent()
        with patch.dict("config.CONFIG", {"tool_use_audit_log": True}):
            with patch("builtins.open") as mock_open:
                with patch("os.makedirs"):
                    agent._audit_log({"event": "test"})
        assert mock_open.call_args is not None, "Expected _audit_log to call open()"
        path = mock_open.call_args[0][0]
        assert WORKSPACE_DIR in path or "tool_use_audit" in path


class TestSuccessTracking:
    def test_success_tracked(self):
        from plugin_registry import registry
        registry._execution_successes.clear()
        registry._execution_errors.clear()
        registry.plugins["TEST_SUCCESS"] = lambda i: "ok"
        try:
            registry.execute_command({"action": "TEST_SUCCESS"})
            assert registry._execution_successes["TEST_SUCCESS"]["count"] > 0
        finally:
            del registry.plugins["TEST_SUCCESS"]

    def test_clear_clears_both(self):
        from plugin_registry import registry
        registry._execution_successes["CT"] = {"count": 1, "last_time": ""}
        registry._execution_errors["CT"] = {"count": 1, "last_error": "", "last_time": ""}
        registry.clear_execution_errors()
        assert "CT" not in registry._execution_successes
        assert "CT" not in registry._execution_errors


# ---------------------------------------------------------------------------
# Streaming buffering tests (Sprint 12, suggestion #19)
# ---------------------------------------------------------------------------


def _make_chunk(content="", finish_reason=None):
    """Build a mock llama-cpp-python stream chunk dict."""
    return {"choices": [{"delta": {"content": content}, "finish_reason": finish_reason}]}


def _mock_stream(chunks):
    """Return an iterable that yields the given chunks."""
    for c in chunks:
        yield c


@pytest.mark.skip(reason="Streaming buffering token aggregation was removed from chat_completion_stream")
class TestStreamingBuffering:
    """Tests for token buffering in LocalLLMProvider.chat_completion_stream().

    Buffer size is controlled by CONFIG["stream_buffer_size"] (default 4).
    Tokens are accumulated and yielded in batches to reduce UI thread overhead.
    """

    @staticmethod
    def _set_buffer_size(size: int):
        """Temporarily set CONFIG['stream_buffer_size'] for a single test.

        Returns the previous value so the caller can restore it.
        """
        import config
        old = config.CONFIG.get("stream_buffer_size", 4)
        config.CONFIG["stream_buffer_size"] = size
        return old

    @staticmethod
    def _restore_buffer_size(old):
        import config
        config.CONFIG["stream_buffer_size"] = old

    @staticmethod
    def _make_provider():
        """Build a LocalLLMProvider pre-configured for in-memory testing.

        Sets model_file="__test__" (harmless placeholder, won't match any
        real model path), _loaded=True, _current_model_path="__test__" so
        chat_completion_stream skips _load_model(). Caller must still set
        _model_instance.create_chat_completion to a mock stream.
        """
        from ai_base import LocalLLMProvider
        prov = LocalLLMProvider(model_file="__test__")
        prov._loaded = True
        prov._current_model_path = "__test__"
        prov._model_instance = MagicMock()
        return prov

    def test_buffer_size_4_aggregates(self):
        """With buffer_size=4, 6 tokens should yield as [4-token chunk, 2-token flush]."""
        old = self._set_buffer_size(4)
        try:
            prov = self._make_provider()
            prov._model_instance.create_chat_completion.return_value = _mock_stream([
                _make_chunk("a"), _make_chunk("b"), _make_chunk("c"),
                _make_chunk("d"), _make_chunk("e"), _make_chunk("f"),
            ])
            results = list(prov.chat_completion_stream(
                messages=[{"role": "user", "content": "hi"}]))
            tokens = [r["token"] for r in results if r.get("token")]
            assert len(tokens) == 2, f"Expected 2 buffered yields, got {len(tokens)}: {tokens}"
            assert tokens[0] == "abcd"
            assert tokens[1] == "ef"
            assert results[-1]["done"] is True
        finally:
            self._restore_buffer_size(old)

    def test_buffer_size_0_disables(self):
        """With buffer_size=0, each token is yielded individually (no buffering)."""
        old = self._set_buffer_size(0)
        try:
            prov = self._make_provider()
            prov._model_instance.create_chat_completion.return_value = _mock_stream([
                _make_chunk("x"), _make_chunk("y"), _make_chunk("z"),
            ])
            results = list(prov.chat_completion_stream(
                messages=[{"role": "user", "content": "hi"}]))
            tokens = [r["token"] for r in results if r.get("token")]
            assert tokens == ["x", "y", "z"], f"Expected per-token yields, got {tokens}"
            assert results[-1]["done"] is True
        finally:
            self._restore_buffer_size(old)

    def test_cancel_flushes(self):
        """Cancellation mid-stream flushes buffered tokens before the error done."""
        import threading
        old = self._set_buffer_size(4)
        try:
            prov = self._make_provider()
            ce = threading.Event()
            stream = _mock_stream([
                _make_chunk("a"), _make_chunk("b"),
                _make_chunk("c"),
            ])
            def _wrapped():
                for i, c in enumerate(stream):
                    yield c
                    if i == 1:  # set cancel AFTER yielding chunk_b
                        ce.set()
            prov._model_instance.create_chat_completion.return_value = _wrapped()
            results = list(prov.chat_completion_stream(
                messages=[{"role": "user", "content": "hi"}],
                cancel_event=ce))
            tokens = [r["token"] for r in results if r.get("token")]
            errors = [r for r in results if r.get("error")]
            assert len(tokens) == 1, f"Expected 1 flush yield, got {tokens}"
            assert tokens[0] == "ab", f"Expected buffered 'ab', got {tokens[0]}"
            assert len(errors) == 1
            assert "Cancelled" in errors[0]["error"]
        finally:
            self._restore_buffer_size(old)

    def test_single_token_flush(self):
        """Single token is flushed when finish_reason is 'stop'."""
        old = self._set_buffer_size(4)
        try:
            prov = self._make_provider()
            prov._model_instance.create_chat_completion.return_value = _mock_stream([
                _make_chunk("hello", finish_reason="stop")])
            results = list(prov.chat_completion_stream(
                messages=[{"role": "user", "content": "hi"}]))
            tokens = [r["token"] for r in results if r.get("token")]
            assert tokens == ["hello"], f"Expected single-token flush, got {tokens}"
            assert results[-1]["done"] is True
            assert results[-1].get("error") is None
        finally:
            self._restore_buffer_size(old)

    def test_empty_stream(self):
        """Empty stream yields done=True with no tokens."""
        old = self._set_buffer_size(4)
        try:
            prov = self._make_provider()
            prov._model_instance.create_chat_completion.return_value = _mock_stream([])
            results = list(prov.chat_completion_stream(
                messages=[{"role": "user", "content": "hi"}]))
            tokens = [r["token"] for r in results if r.get("token")]
            assert tokens == [], f"Expected no tokens, got {tokens}"
            assert results[-1]["done"] is True
            assert results[-1]["token"] == ""
        finally:
            self._restore_buffer_size(old)


# ---------------------------------------------------------------------------
# Grammar hot-reload tests (Sprint 12, suggestion #12)
# ---------------------------------------------------------------------------


class TestGrammarHotReload:
    """Tests for mtime-based grammar hot-reload in get_grammar().

    Grammar files live in data/grammars/*.gbnf. get_grammar() checks the
    file's mtime on each call and reloads if changed. When the file is
    missing, empty, or hot-reload is disabled, the hardcoded
    GBNF_XML_THINKING_FINAL default is used.
    """

    @staticmethod
    def _gbnf_path():
        """Return the path to xml_thinking_final.gbnf."""
        from ai_base import _get_grammar_file_path
        return _get_grammar_file_path("xml")

    @staticmethod
    def _clear_grammar_cache():
        """Clear the module-level grammar cache."""
        import ai_base
        ai_base._GRAMMAR_CACHE.clear()

    @staticmethod
    def _write_grammar_file(content):
        """Write content to the grammar file, creating dirs as needed."""
        path = TestGrammarHotReload._gbnf_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    @staticmethod
    def _delete_grammar_file():
        """Remove the grammar file if it exists."""
        path = TestGrammarHotReload._gbnf_path()
        if os.path.isfile(path):
            os.remove(path)

    def test_mtime_reload(self):
        """Changing the grammar file's mtime triggers a reload from disk."""
        from ai_base import get_grammar
        self._clear_grammar_cache()
        self._delete_grammar_file()
        try:
            path = self._write_grammar_file("grammar-v1")
            real_mtime = os.path.getmtime(path)
            g1 = get_grammar("xml")
            assert g1 == "grammar-v1", f"Expected 'grammar-v1', got {g1!r}"
            # Update file content and force a newer mtime so cache misses
            with open(path, "w", encoding="utf-8") as f:
                f.write("grammar-v2")
            # Simulate a newer mtime
            with patch("os.path.getmtime", return_value=real_mtime + 10.0):
                g2 = get_grammar("xml")
            assert g2 == "grammar-v2", f"Expected 'grammar-v2', got {g2!r}"
        finally:
            self._delete_grammar_file()
            self._clear_grammar_cache()

    def test_cache_hit(self):
        """Same mtime returns cached grammar without re-reading the file."""
        from ai_base import get_grammar, _GRAMMAR_CACHE
        self._clear_grammar_cache()
        self._delete_grammar_file()
        try:
            path = self._write_grammar_file("cached-grammar")
            mtime = os.path.getmtime(path)
            g1 = get_grammar("xml")
            assert g1 == "cached-grammar"
            # Second call with same mtime should return cached version
            with patch("os.path.getmtime", return_value=mtime):
                g2 = get_grammar("xml")
            assert g2 == "cached-grammar"
            # Cache entry should exist with correct mtime
            cached = _GRAMMAR_CACHE.get("xml")
            assert cached is not None
            assert cached["mtime"] == mtime
        finally:
            self._delete_grammar_file()
            self._clear_grammar_cache()

    def test_file_missing_fallback(self):
        """When the grammar file doesn't exist, fall back to hardcoded default."""
        from ai_base import get_grammar, GBNF_XML_THINKING_FINAL
        self._clear_grammar_cache()
        self._delete_grammar_file()
        try:
            g = get_grammar("xml")
            assert g == GBNF_XML_THINKING_FINAL, (
                "Expected hardcoded default when file missing"
            )
        finally:
            self._clear_grammar_cache()

    def test_empty_file_fallback(self):
        """An empty grammar file falls back to the hardcoded default."""
        from ai_base import get_grammar, GBNF_XML_THINKING_FINAL
        self._clear_grammar_cache()
        self._delete_grammar_file()
        try:
            self._write_grammar_file("   \n  ")  # whitespace only → empty after strip()
            g = get_grammar("xml")
            assert g == GBNF_XML_THINKING_FINAL, (
                f"Expected hardcoded default for empty file, got {g[:50]!r}"
            )
        finally:
            self._delete_grammar_file()
            self._clear_grammar_cache()

    def test_hot_reload_disabled(self):
        """When grammar_hot_reload is False, hardcoded default is always returned."""
        from ai_base import get_grammar, GBNF_XML_THINKING_FINAL
        import config
        self._clear_grammar_cache()
        self._delete_grammar_file()
        old_hot = config.CONFIG.get("grammar_hot_reload", True)
        try:
            config.CONFIG["grammar_hot_reload"] = False
            self._write_grammar_file("should-not-be-used")
            g = get_grammar("xml")
            assert g == GBNF_XML_THINKING_FINAL, (
                "Expected hardcoded default when hot-reload disabled"
            )
        finally:
            self._delete_grammar_file()
            self._clear_grammar_cache()
            config.CONFIG["grammar_hot_reload"] = old_hot

    def test_invalidate_cache(self):
        """invalidate_grammar_cache() forces a reload on next get_grammar()."""
        from ai_base import get_grammar, invalidate_grammar_cache, _GRAMMAR_CACHE
        self._clear_grammar_cache()
        self._delete_grammar_file()
        try:
            path = self._write_grammar_file("pre-invalidate")
            real_mtime = os.path.getmtime(path)
            g1 = get_grammar("xml")
            assert g1 == "pre-invalidate"
            assert "xml" in _GRAMMAR_CACHE
            # Invalidate and update file
            invalidate_grammar_cache("xml")
            assert "xml" not in _GRAMMAR_CACHE
            with open(path, "w", encoding="utf-8") as f:
                f.write("post-invalidate")
            with patch("os.path.getmtime", return_value=real_mtime + 10.0):
                g2 = get_grammar("xml")
            assert g2 == "post-invalidate", f"Expected reload after invalidate, got {g2!r}"
        finally:
            self._delete_grammar_file()
            self._clear_grammar_cache()


# ---------------------------------------------------------------------------
# Tool schema pre-warming tests (Sprint 12, suggestion #20)
# ---------------------------------------------------------------------------


class TestToolSchemaPrewarm:
    """Tests for tool schema cache pre-warming in services/tool_use_agent.py.

    prewarm_tool_schemas() populates _TOOL_SCHEMA_CACHE synchronously.
    _prewarm_cache_async() spawns a daemon thread with 3 retry attempts,
    gated by CONFIG["tool_schema_prewarm"] and _PREWARM_DONE.
    """

    @staticmethod
    def _clear_cache():
        from services.tool_use_agent import clear_tool_schema_cache
        clear_tool_schema_cache()

    @staticmethod
    def _reset_prewarm_done():
        import services.tool_use_agent as tua
        tua._PREWARM_DONE = False

    @staticmethod
    def _cache_data():
        from services.tool_use_agent import _TOOL_SCHEMA_CACHE
        return _TOOL_SCHEMA_CACHE["data"]

    def test_prewarm_populates_cache(self):
        """prewarm_tool_schemas() synchronously populates _TOOL_SCHEMA_CACHE."""
        from services.tool_use_agent import prewarm_tool_schemas
        self._clear_cache()
        try:
            assert self._cache_data() is None
            prewarm_tool_schemas()
            assert self._cache_data() is not None, (
                "Expected cache to be populated after prewarm"
            )
            assert isinstance(self._cache_data(), list)
        finally:
            self._clear_cache()

    def test_prewarm_disabled_skips(self):
        """When CONFIG['tool_schema_prewarm'] is False, async prewarm does nothing."""
        import services.tool_use_agent as tua
        import config
        self._clear_cache()
        self._reset_prewarm_done()
        old_prewarm = config.CONFIG.get("tool_schema_prewarm", True)
        try:
            config.CONFIG["tool_schema_prewarm"] = False
            tua._prewarm_cache_async()
            assert tua._PREWARM_DONE is False, (
                "_PREWARM_DONE should remain False when prewarm is disabled"
            )
            assert self._cache_data() is None
        finally:
            config.CONFIG["tool_schema_prewarm"] = old_prewarm
            self._clear_cache()

    def test_synchronous_prewarm_survives_errors(self):
        """prewarm_tool_schemas() catches exceptions gracefully (no crash)."""
        from services.tool_use_agent import prewarm_tool_schemas
        self._clear_cache()
        try:
            with patch(
                "services.tool_use_agent._get_cached_tool_schemas",
                side_effect=RuntimeError("mock failure"),
            ):
                prewarm_tool_schemas()  # should not raise
            # Cache should still be None (the error was swallowed)
        finally:
            self._clear_cache()

    def test_retry_on_failure(self):
        """Async prewarm retries _get_cached_tool_schemas up to 3 times on failure."""
        import services.tool_use_agent as tua
        self._clear_cache()
        self._reset_prewarm_done()
        call_count = [0]
        target_fn = [None]

        def flaky_schema_fetch():
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError(f"attempt {call_count[0]} failed")
            # Populate the cache manually since the mock bypasses the real impl
            tua._TOOL_SCHEMA_CACHE["data"] = [
                {"name": "test", "description": "ok", "parameters": {}}
            ]
            tua._TOOL_SCHEMA_CACHE["ts"] = time.time()
            return tua._TOOL_SCHEMA_CACHE["data"]

        original_thread = tua.threading.Thread

        def _capture_thread(*args, target=None, **kwargs):
            target_fn[0] = target  # save for synchronous invocation
            return original_thread(*args, target=target, **kwargs)

        try:
            with patch(
                "services.tool_use_agent._get_cached_tool_schemas",
                side_effect=flaky_schema_fetch,
            ):
                with patch.object(tua.threading, "Thread", side_effect=_capture_thread):
                    tua._prewarm_cache_async()
                    assert target_fn[0] is not None, "Expected a thread target"
                    # Run the target synchronously for deterministic test
                    target_fn[0]()

            assert call_count[0] >= 3, (
                f"Expected at least 3 attempts, got {call_count[0]}"
            )
            assert self._cache_data() is not None, (
                "Cache should be populated after successful retry"
            )
        finally:
            self._clear_cache()
            self._reset_prewarm_done()