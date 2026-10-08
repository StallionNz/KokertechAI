"""
test_structured_output.py - Sprint 12: Structured Output & Function Calling

Tests for:
- Pydantic models (ToolCall, FunctionCall, AIResponse)
- GBNF grammar definitions
- parse_ai() with Pydantic validation
- parse_tool_calls() directive extraction
- convert_plugin_schema_to_openai_tool()
- cached_chat_completion passthrough of response_format/tools
"""

import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------

class TestFunctionCallModel:
    def test_basic_creation(self):
        from models import FunctionCall
        fc = FunctionCall(name="search_web", arguments={"query": "AI news"})
        assert fc.name == "search_web"
        assert fc.arguments == {"query": "AI news"}

    def test_empty_arguments(self):
        from models import FunctionCall
        fc = FunctionCall(name="get_status")
        assert fc.arguments == {}


class TestToolCallModel:
    def test_basic_creation(self):
        from models import ToolCall, FunctionCall
        tc = ToolCall(id="call_1", type="function",
                      function=FunctionCall(name="search", arguments={"q": "t"}))
        assert tc.id == "call_1"
        assert tc.function.name == "search"

    def test_defaults(self):
        from models import ToolCall, FunctionCall
        tc = ToolCall(function=FunctionCall(name="test"))
        assert tc.id is None
        assert tc.type == "function"


class TestAIResponseModel:
    def test_basic_creation(self):
        from models import AIResponse
        resp = AIResponse(thinking="analyzing", final="done")
        assert resp.thinking == "analyzing"
        assert resp.tool_calls == []

    def test_with_tool_calls(self):
        from models import AIResponse, ToolCall, FunctionCall
        tc = ToolCall(function=FunctionCall(name="search", arguments={"q": "t"}))
        resp = AIResponse(thinking="need search", final="searching", tool_calls=[tc])
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].function.name == "search"


# ---------------------------------------------------------------------------
# GBNF Grammar tests
# ---------------------------------------------------------------------------

class TestGBNFGrammars:
    def test_xml_grammar_exists(self):
        from ai_base import GBNF_XML_THINKING_FINAL
        assert "<thinking>" in GBNF_XML_THINKING_FINAL
        assert "<final_output>" in GBNF_XML_THINKING_FINAL
        assert "root ::=" in GBNF_XML_THINKING_FINAL

    def test_json_grammar_removed(self):
        # GBNF_JSON_OBJECT was removed in Sprint 12.1 — GBNF cannot express
        # JSON's nested structure. Use llama.cpp built-in json_object mode.
        with pytest.raises(ImportError):
            from ai_base import GBNF_JSON_OBJECT  # noqa: F811

    def test_get_grammar_xml(self):
        from ai_base import get_grammar
        grammar = get_grammar("xml")
        assert "thinking" in grammar

    def test_get_grammar_json_raises(self):
        from ai_base import get_grammar
        with pytest.raises(ValueError, match="GBNF cannot express"):
            get_grammar("json")

    def test_get_grammar_json_object_raises(self):
        from ai_base import get_grammar
        with pytest.raises(ValueError, match="GBNF cannot express"):
            get_grammar("json_object")

    def test_get_grammar_default(self):
        from ai_base import get_grammar
        grammar = get_grammar()
        assert "thinking" in grammar


# ---------------------------------------------------------------------------
# convert_plugin_schema_to_openai_tool tests
# ---------------------------------------------------------------------------

class TestConvertPluginSchema:
    def test_basic_conversion(self):
        from ai_base import convert_plugin_schema_to_openai_tool
        schema = {"action": "WEB_SEARCH", "query": "search query"}
        result = convert_plugin_schema_to_openai_tool(schema)
        assert result["type"] == "function"
        assert result["function"]["name"] == "WEB_SEARCH"
        assert "query" in result["function"]["parameters"]["properties"]

    def test_action_only(self):
        from ai_base import convert_plugin_schema_to_openai_tool
        result = convert_plugin_schema_to_openai_tool({"action": "STATUS"})
        assert result["function"]["name"] == "STATUS"

    def test_optional_params(self):
        from ai_base import convert_plugin_schema_to_openai_tool
        schema = {"action": "S", "q": "required query", "n": "optional limit"}
        result = convert_plugin_schema_to_openai_tool(schema)
        assert "q" in result["function"]["parameters"]["required"]
        assert "n" not in result["function"]["parameters"]["required"]

    def test_empty_schema(self):
        from ai_base import convert_plugin_schema_to_openai_tool
        result = convert_plugin_schema_to_openai_tool({})
        assert result["function"]["name"] == "unknown"


# ---------------------------------------------------------------------------
# parse_ai with Pydantic validation tests
# ---------------------------------------------------------------------------

class TestParseAiPydantic:
    def test_structured_both_tags(self):
        from services.prompt_service import PromptService
        content = "<thinking>thought</thinking><final_output>result</final_output>"
        result = PromptService.parse_ai(content, freeform=False)
        assert result["thinking"] == "thought"
        assert result["final"] == "result"
        assert result["ts"]

    def test_no_tags_fallback(self):
        from services.prompt_service import PromptService
        result = PromptService.parse_ai("plain text", freeform=False)
        assert result["final"] == "plain text"
        assert result["thinking"] == "Synthesizing..."

    def test_freeform_plain(self):
        from services.prompt_service import PromptService
        result = PromptService.parse_ai("hello", freeform=True)
        assert result["thinking"] == ""
        assert result["final"] == "hello"
        assert result["command"] is None

    def test_freeform_json_command(self):
        from services.prompt_service import PromptService
        result = PromptService.parse_ai('{"action":"SEARCH"}', freeform=True)
        assert result["command"] is not None
        assert result["command"]["action"] == "SEARCH"

    def test_structured_json_in_final(self):
        from services.prompt_service import PromptService
        content = '<thinking>t</thinking><final_output>{"action":"READ","path":"x.py"}</final_output>'
        result = PromptService.parse_ai(content, freeform=False)
        assert result["command"] is not None
        assert result["command"]["action"] == "READ"


# ---------------------------------------------------------------------------
# parse_tool_calls tests
# ---------------------------------------------------------------------------

class TestParseToolCalls:
    def test_single(self):
        from services.prompt_service import PromptService
        content = '<<TOOL_CALL:{"action":"SEARCH","q":"x"}>>'
        calls = PromptService.parse_tool_calls(content)
        assert len(calls) == 1
        assert calls[0]["action"] == "SEARCH"

    def test_multiple(self):
        from services.prompt_service import PromptService
        content = '<<TOOL_CALL:{"action":"A"}>> text <<TOOL_CALL:{"action":"B"}>>'
        calls = PromptService.parse_tool_calls(content)
        assert len(calls) == 2

    def test_none(self):
        from services.prompt_service import PromptService
        assert PromptService.parse_tool_calls("no calls") == []

    def test_invalid_json(self):
        from services.prompt_service import PromptService
        assert PromptService.parse_tool_calls("<<TOOL_CALL:{bad}>>") == []

    def test_empty_action(self):
        from services.prompt_service import PromptService
        assert PromptService.parse_tool_calls('<<TOOL_CALL:{"action":""}>>') == []


# ---------------------------------------------------------------------------
# _handle_error includes tool_calls
# ---------------------------------------------------------------------------

class TestHandleErrorToolCalls:
    def test_includes_tool_calls(self):
        from ai_base import LocalLLMProvider
        provider = LocalLLMProvider()
        result = provider._handle_error("test")
        assert "tool_calls" in result
        assert result["tool_calls"] == []


# ---------------------------------------------------------------------------
# LocalLLMProvider tool_calls extraction tests
# ---------------------------------------------------------------------------

class TestLocalLLMToolCallsExtraction:
    def _make_mock_provider(self):
        """Create a LocalLLMProvider with a mocked model instance.

        Sets model_file, _current_model_path, and _model_instance consistently
        so chat_completion does not attempt to load a real model.
        """
        from ai_base import LocalLLMProvider
        provider = LocalLLMProvider()
        provider.model_file = "test.gguf"
        provider._loaded = True
        provider._current_model_path = "test.gguf"
        provider._model_instance = MagicMock()
        return provider

    def test_tool_calls_extracted(self):
        provider = self._make_mock_provider()

        mock_response = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "search_web",
                            "arguments": json.dumps({"query": "test"}),
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        provider._model_instance.create_chat_completion.return_value = mock_response
        result = provider.chat_completion(
            messages=[{"role": "user", "content": "search"}],
        )

        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["function"]["name"] == "search_web"
        assert result["tool_calls"][0]["function"]["arguments"] == {"query": "test"}

    def test_no_tool_calls(self):
        provider = self._make_mock_provider()

        mock_response = {
            "choices": [{
                "message": {"content": "Hello!", "tool_calls": None},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        with patch.object(provider._model_instance, "create_chat_completion", return_value=mock_response):
            result = provider.chat_completion(
                messages=[{"role": "user", "content": "hi"}],
            )

        assert result["tool_calls"] == []
        assert result["content"] == "Hello!"

    def test_invalid_tool_call_arguments_fallback(self):
        provider = self._make_mock_provider()

        mock_response = {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bad_tool", "arguments": "not valid json"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        with patch.object(provider._model_instance, "create_chat_completion", return_value=mock_response):
            result = provider.chat_completion(
                messages=[{"role": "user", "content": "test"}],
            )

        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["function"]["arguments"] == {}
