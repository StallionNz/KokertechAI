"""test_tool_use_integration.py - Integration test for ToolUseAgent ReAct loop.

Exercises the full mocked flow:
    provider.chat_completion() -> parse_tool_calls() -> ToolUseAgent.execute()
    loop with registry dispatch and feedback.

Run with: python -m pytest tests/test_tool_use_integration.py -v
"""

import json
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MOCK_SCHEMAS = [
    {"action": "WEB_SEARCH", "query": "search query"},
    {"action": "SYSTEM_STATUS", "query": "system info"},
    {"action": "READ_FILE", "query": "file path to read"},
]


def _make_provider(responses):
    """Create a mock provider whose chat_completion returns responses in order."""
    provider = MagicMock()
    provider.chat_completion.side_effect = list(responses)
    return provider


def _tool_call_response(action, params=None):
    """Shorthand for a response containing a TOOL_CALL directive."""
    payload = {"action": action}
    if params:
        payload.update(params)
    tool_call_str = f"<<TOOL_CALL:{json.dumps(payload)}>>"
    return {
        "content": f"I need to call a tool.\n\n{tool_call_str}",
        "model": "mock",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "error": None,
        "tool_calls": [],
    }


def _text_response(text):
    """Shorthand for a response with plain text content."""
    return {
        "content": text,
        "model": "mock",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "error": None,
        "tool_calls": [],
    }


def _error_response(msg="provider error"):
    """Shorthand for a response with an error."""
    return {
        "content": "",
        "model": "mock",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "error": msg,
        "tool_calls": [],
    }


# ===========================================================================
# Test 1: Single tool call -> final response
# ===========================================================================

class TestSingleToolCall:
    """ToolUseAgent calls one tool, gets a result, then returns a final answer."""

    def test_single_tool_call_flow(self):
        from services.tool_use_agent import ToolUseAgent, clear_tool_schema_cache

        clear_tool_schema_cache()

        responses = [
            _tool_call_response("WEB_SEARCH", {"query": "python async"}),
            _text_response(
                "Here are the search results for Python async programming."
            ),
        ]
        provider = _make_provider(responses)

        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas", return_value=MOCK_SCHEMAS), \
             patch("plugin_registry.registry") as mock_registry:
            mock_registry.execute_command.return_value = (
                "Search results: 3 articles found about Python async/await"
            )

            agent = ToolUseAgent()
            result = agent.execute("Search for Python async tutorials")

        # Final response returned (not the tool call)
        assert "search results" in result.lower() or "python async" in result.lower()
        assert not result.startswith("[ToolUser]")

        # Provider called twice: once for tool call, once for final answer
        assert provider.chat_completion.call_count == 2

        # Registry was dispatched with the correct intent
        mock_registry.execute_command.assert_called_once()
        call_args = mock_registry.execute_command.call_args[0][0]
        assert call_args["action"] == "WEB_SEARCH"
        assert call_args["query"] == "python async"


# ===========================================================================
# Test 2: Multiple tool calls -> final response
# ===========================================================================

class TestMultipleToolCalls:
    """ToolUseAgent chains two tool calls before producing a final answer."""

    def test_two_tool_calls_flow(self):
        from services.tool_use_agent import ToolUseAgent, clear_tool_schema_cache

        clear_tool_schema_cache()

        responses = [
            _tool_call_response("WEB_SEARCH", {"query": "best practices"}),
            _tool_call_response("SYSTEM_STATUS"),
            _text_response("Summary: system is healthy, here are best practices."),
        ]
        provider = _make_provider(responses)

        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas", return_value=MOCK_SCHEMAS), \
             patch("plugin_registry.registry") as mock_registry:
            mock_registry.execute_command.side_effect = [
                "Found 5 articles on best practices",
                "CPU: 45%, RAM: 62%, Disk: 78%",
            ]

            agent = ToolUseAgent()
            result = agent.execute("Check best practices and system status")

        # Final response returned
        assert "summary" in result.lower() or "best practices" in result.lower()
        assert not result.startswith("[ToolUser]")

        # Provider called 3 times (2 tool calls + 1 final)
        assert provider.chat_completion.call_count == 3

        # Registry dispatched both commands with correct arguments
        assert mock_registry.execute_command.call_count == 2
        calls = [c[0][0] for c in mock_registry.execute_command.call_args_list]
        assert calls[0]["action"] == "WEB_SEARCH"
        assert calls[1]["action"] == "SYSTEM_STATUS"


# ===========================================================================
# Test 3: Invalid JSON in tool call -> error feedback -> retry
# ===========================================================================

class TestInvalidToolCallJson:
    """ToolUseAgent handles malformed TOOL_CALL JSON gracefully."""

    def test_invalid_json_retries(self):
        from services.tool_use_agent import ToolUseAgent, clear_tool_schema_cache

        clear_tool_schema_cache()

        responses = [
            _text_response("Let me try: <<TOOL_CALL:{broken json here>>"),
            _text_response("Done! Here is the result."),
        ]
        provider = _make_provider(responses)

        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas", return_value=MOCK_SCHEMAS), \
             patch("plugin_registry.registry") as mock_registry:
            agent = ToolUseAgent()
            result = agent.execute("Do something")

        # Should return the final response
        assert "done" in result.lower() or "result" in result.lower()

        # Registry should NOT be called (JSON was invalid)
        mock_registry.execute_command.assert_not_called()

        # Provider called twice: invalid attempt + retry
        assert provider.chat_completion.call_count == 2

        # Second call should include error feedback in messages
        second_call_msgs = provider.chat_completion.call_args_list[1][1]["messages"]
        error_feedback = [m for m in second_call_msgs
                          if "Invalid TOOL_CALL" in m.get("content", "")]
        assert len(error_feedback) == 1


# ===========================================================================
# Test 4: No tool calls -> direct text response
# ===========================================================================

class TestDirectResponse:
    """ToolUseAgent returns immediately when the model produces no tool calls."""

    def test_no_tool_calls_returns_directly(self):
        from services.tool_use_agent import ToolUseAgent, clear_tool_schema_cache

        clear_tool_schema_cache()

        responses = [
            _text_response("I can answer this directly. The answer is 42."),
        ]
        provider = _make_provider(responses)

        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas", return_value=MOCK_SCHEMAS), \
             patch("plugin_registry.registry") as mock_registry:
            agent = ToolUseAgent()
            result = agent.execute("What is the meaning of life?")

        assert "42" in result
        assert provider.chat_completion.call_count == 1
        mock_registry.execute_command.assert_not_called()


# ===========================================================================
# Test 5: Provider error mid-loop
# ===========================================================================

class TestProviderError:
    """ToolUseAgent handles provider errors gracefully mid-loop."""

    def test_provider_error_returns_error(self):
        from services.tool_use_agent import ToolUseAgent, clear_tool_schema_cache

        clear_tool_schema_cache()

        responses = [_error_response("Model crashed")]
        provider = _make_provider(responses)

        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas", return_value=MOCK_SCHEMAS):
            agent = ToolUseAgent()
            result = agent.execute("Do something")

        assert "[ToolUser] Error" in result
        assert "Model crashed" in result


# ===========================================================================
# Test 6: Max turns reached
# ===========================================================================

class TestMaxTurnsReached:
    """ToolUseAgent stops after max_turns and returns last response."""

    def test_max_turns_returns_last_response(self):
        from services.tool_use_agent import ToolUseAgent, clear_tool_schema_cache

        clear_tool_schema_cache()

        # Provide 11 tool-call responses — the real execute() loop has
        # max_turns=10, so after 10 tool calls it naturally returns
        # "Max turns reached" without reimplementing production code.
        responses = [
            _tool_call_response("WEB_SEARCH", {"query": f"q{i}"})
            for i in range(11)
        ]
        provider = _make_provider(responses)

        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas", return_value=MOCK_SCHEMAS), \
             patch("plugin_registry.registry") as mock_registry:
            mock_registry.execute_command.return_value = "result"

            agent = ToolUseAgent()
            result = agent.execute("Search continuously")

        assert "Max turns reached" in result
        # The real execute() loop stops at max_turns=10, so only 10 calls
        assert provider.chat_completion.call_count == 10


# ===========================================================================
# Test 7: Tool schemas passed to provider in OpenAI format
# ===========================================================================

class TestRegistryException:
    """ToolUseAgent handles registry.execute_command() raising an exception."""

    def test_registry_exception_returns_failure(self):
        from services.tool_use_agent import ToolUseAgent, clear_tool_schema_cache

        clear_tool_schema_cache()

        # Provide enough responses for: (1) initial tool call, (2) summarizer,
        # (3) next turn's retry or final answer after the error feedback.
        responses = [
            _tool_call_response("WEB_SEARCH", {"query": "test"}),
            _text_response("Error: tool failed, retrying"),
            _text_response("All retries exhausted, giving final report."),
        ]
        provider = _make_provider(responses)

        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas", return_value=MOCK_SCHEMAS), \
             patch("plugin_registry.registry") as mock_registry:
            mock_registry.execute_command.side_effect = RuntimeError("boom")

            agent = ToolUseAgent()
            result = agent.execute("Search for something")

        # _run_one_tool catches the RuntimeError and retries 3 times, then
        # returns a formatted error string. The error is injected back into
        # the conversation as tool result feedback. Verify it's in the
        # conversation history (not the final return value).
        last_msgs = provider.chat_completion.call_args_list[-1][1]["messages"]
        error_feedback = any(
            "failed after 3 attempts" in m.get("content", "")
            for m in last_msgs
        )
        assert error_feedback, "Expected error-feedback in conversation history"


# ===========================================================================
# Test 8: Tool schemas passed to provider in OpenAI format
# ===========================================================================

class TestToolSchemasPassed:
    """Verify that OpenAI-format tool schemas are passed to chat_completion."""

    def test_tools_passed_to_provider(self):
        from services.tool_use_agent import ToolUseAgent, clear_tool_schema_cache

        clear_tool_schema_cache()

        responses = [_text_response("Done.")]
        provider = _make_provider(responses)

        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas",
                   return_value=MOCK_SCHEMAS), \
             patch("plugin_registry.registry"):
            agent = ToolUseAgent()
            result = agent.execute("Quick question")

        # Check that tools kwarg was passed
        call_kwargs = provider.chat_completion.call_args[1]
        assert "tools" in call_kwargs
        tools = call_kwargs["tools"]
        assert tools is not None
        assert len(tools) == 3

        # Each tool should be in OpenAI format
        for tool in tools:
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]
            assert "parameters" in tool["function"]


# ===========================================================================
# Test 9: No tool schemas -> graceful fallback
# ===========================================================================

class TestNoToolSchemas:
    """ToolUseAgent works even when no tool schemas are available."""

    def test_no_schemas_still_works(self):
        from services.tool_use_agent import ToolUseAgent, clear_tool_schema_cache

        clear_tool_schema_cache()

        responses = [_text_response("I can answer directly.")]
        provider = _make_provider(responses)

        with patch("services.tool_use_agent.get_provider", return_value=provider), \
             patch("services.tool_use_agent._get_cached_tool_schemas", return_value=[]), \
             patch("plugin_registry.registry"):
            agent = ToolUseAgent()
            result = agent.execute("Simple question")

        assert "directly" in result.lower()

        # tools should be None when no schemas
        call_kwargs = provider.chat_completion.call_args[1]
        assert call_kwargs.get("tools") is None
