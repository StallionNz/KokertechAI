# Sprint 12 consolidated structured output service
from config import CONFIG
from ai_base import get_grammar, GBNF_XML_THINKING_FINAL, convert_plugin_schema_to_openai_tool
from services.prompt_service import PromptService
parse_ai = PromptService.parse_ai
parse_tool_calls = PromptService.parse_tool_calls
def resolve_response_format(freeform=False):
    if freeform: return None
    fmt = CONFIG.get("structured_format", "xml")
    if fmt == "json": return {"type": "json_object"}
    return {"type": "grammar", "value": get_grammar("xml")}
