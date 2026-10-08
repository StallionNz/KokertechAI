"""
plugins/tool_use.py — Tool-Use / Function Calling Agent plugin.

Exposes a TOOL_USE command that delegates to the ToolUseAgent (sub_agents.py)
for multi-step ReAct-style orchestration of plugins (web search, calculator,
file operations, etc.).

Usage: {
    "action": "TOOL_USE",
    "task": "Search the web for AI news today and save it to a file"
}

Sprint 5 backlog item (Feature suggestion #10).
"""

from logging_config import get_logger

logger = get_logger(name="ToolUsePlugin")

PLUGIN_METADATA = {
    "name": "Tool-Use / Function Calling Agent",
    "description": "Multi-step ReAct agent that chains web search, calculator, file operations, and other plugins to complete complex tasks",
    "version": "1.0.0",
    "tags": ["agents", "orchestration", "react", "tools", "function-calling"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['ai']
}

COMMAND_NAME = "TOOL_USE"
SCHEMA = {
    "action": "TOOL_USE",
    "task": "<natural language task description — what to accomplish>",
    "timeout": "<optional max seconds for execution, default 300>"
}


def execute(intent_json):
    """Execute a multi-step tool orchestration via the ToolUseAgent.

    intent_json keys:
        task       – Natural-language description of what to accomplish.
        timeout    – Optional; max total execution seconds (default 300).

    Returns:
        The final result string from the ToolUseAgent.
    """
    task = intent_json.get("task") or intent_json.get("query")
    timeout = int(intent_json.get("timeout", 300))

    if not task:
        return "❌ Missing 'task' parameter for TOOL_USE command."

    try:
        from sub_agents import ToolUseAgent

        agent = ToolUseAgent()
        logger.info(
            f"ToolUseAgent invoked — task='{task[:80]}...', "
            f"timeout={timeout}s"
        )
        result = agent.execute(task, timeout=timeout)
        return result

    except ImportError as e:
        return f"❌ ToolUseAgent not available: {str(e)}"
    except Exception as e:
        logger.error(f"ToolUseAgent execution failed: {e}")
        return f"❌ Tool-Use orchestration failed: {str(e)}"
