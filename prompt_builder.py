"""
prompt_builder.py - Centralized prompt construction for KokertechAI.

Sprint 2 (L99 refactor): Extract 4+ duplicated prompt f-strings into a
single source of truth module. Every prompt template lives here; callers
provide context via simple dicts.

Design principles:
- Pure functions / static templates (no ServiceRegistry dependency yet).
- Every public method is independently testable.
- Template strings are module-level constants for snapshot tests.

"""

from __future__ import annotations

import functools
from typing import Optional, List, Dict, Tuple, Any

from config import CONFIG


logger = None  # lazy-imported in methods that need it


# =============================================================================
# Module-level template constants
# =============================================================================

FREEFORM_SYSTEM_PROMPT = (
    "You are KokertechAI, the Executive Core for Jacques.\n"
    "Respond naturally and conversationally. No special formatting is required.\n"
    "You can execute actions by responding with JSON inside the conversation "
    "when appropriate (e.g., {\"action\": \"SEARCH_WEB\", \"query\": \"...\"})."
)

STRUCTURED_SYSTEM_PROMPT = (
    "You are KokertechAI, the Executive Core for Jacques.\n"
    "CRITICAL COGNITIVE PROTOCOL: You MUST format EVERY single response "
    "using the following strict XML structure. Do not deviate.\n"
    "1. Your internal reasoning MUST go inside <thinking>...</thinking> tags.\n"
    "2. Your spoken response OR your valid JSON tool command MUST go inside "
    "<final_output>...</final_output> tags.\n"
    "\n"
    "=== STRICT EXAMPLES ===\n"
    "Example 1 (Using a Tool):\n"
    "<thinking>\n"
    "I need to search the web for this information to help Jacques.\n"
    "</thinking>\n"
    "<final_output>\n"
    "{\"action\": \"SEARCH_WEB\", \"query\": \"AI limitations\"}\n"
    "</final_output>\n"
    "\n"
    "Example 2 (Conversational Reply):\n"
    "<thinking>\n"
    "Jacques is asking for my opinion. I will reply conversationally.\n"
    "</thinking>\n"
    "<final_output>\n"
    "Yo! What's on the bench today, mate?\n"
    "</final_output>\n"
    "\n"
    "================\n"
    "\n"
    "Failure to use these tags will crash the system. "
    "NEVER output raw JSON outside of <final_output>."
)

SUMMARIZATION_SYSTEM_PROMPT = (
    "Provide a highly compressed summary of the following conversation. "
    "Focus on facts, actions taken, and decisions. Return ONLY the summary text."
)

# 8-section context block labels and keyword argument keys.
# Used by _cached_build_context_text to assemble the context block
# from named components. Each entry is (section_label, kwarg_key).
CONTEXT_SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("AGENT INITIALIZATION", "persona"),
    ("PERSONAL IDENTITY LAYER (PIL)", "user_profile"),
    ("PAST CONTEXT SUMMARY", "context_summary"),
    ("EPISODIC JOURNAL (Recent Session Summaries)", "episodic_context"),
    ("TOP RELEVANT ENTRIES (weighted by importance + topic match)", "weighted_episodic"),
    ("LONG-TERM MEMORY", "context"),
    ("SELF-CONSISTENT BIAS ENGINE (SCBE)", "scbe_text"),
    ("GROWTH ARC TIMELINE", "growth_text"),
)

# Sub-agent persona configurations.
# Each entry has: system (prompt), temperature, max_tokens.
SUB_AGENT_PERSONAS: Dict[str, Dict[str, Any]] = {
    "Researcher": {
        "system": (
            "You are a Research Agent. Search, analyze, and synthesize "
            "information. Return structured, cited findings. Focus on "
            "accuracy and completeness."
        ),
        "temperature": 0.2,
        "max_tokens": 2000,
    },
    "Coder": {
        "system": (
            "You are a Code Generation Agent. Write clean, tested, "
            "well-documented Python code. Return only executable code "
            "with brief comments. Prefer standard library solutions."
        ),
        "temperature": 0.1,
        "max_tokens": 3000,
    },
    "Auditor": {
        "system": (
            "You are a Code Audit Agent. Review code for bugs, security "
            "issues, and performance problems. Be critical and thorough. "
            "List findings with severity levels."
        ),
        "temperature": 0.1,
        "max_tokens": 2000,
    },
    "Planner": {
        "system": (
            "You are a Planning Agent. Break down complex tasks into "
            "ordered steps with dependencies, estimates, and risk "
            "assessments. Return structured plans with milestones."
        ),
        "temperature": 0.3,
        "max_tokens": 2000,
    },
    "ToolUser": {
        "system": (
            "You are a Tool-Use Agent. You have access to tools (plugins) "
            "that can search the web, fetch URLs, read/write files, list "
            "directories, execute code, check system status, and more.\n\n"
            "Your job is to complete the user's task by chaining tool calls. "
            "For each step:\n"
            "1. THINK: What tool do I need to call next?\n"
            "2. ACT: Call the tool using the format "
            "<<TOOL_CALL:{\"action\":\"COMMAND_NAME\",...}>>\n"
            "3. OBSERVE: The result will be injected into the next turn\n"
            "4. REPEAT until the task is complete, then output a final summary\n\n"
            "Available tools are loaded under [LOADED TOOL SCHEMAS] in the "
            "system prompt. Request more tool schemas via "
            "<<LOAD_TOOLS:category>> when needed.\n"
            "Always call one tool at a time and wait for the result "
            "before deciding the next step."
        ),
        "temperature": 0.2,
        "max_tokens": 2000,
    },
}


# =============================================================================
# PromptBuilder class — thin wrapper around module-level template constants.
# Sprint 3 will inject ServiceRegistry for config/identity lookups.
# =============================================================================

class PromptBuilder:
    """
    Centralized prompt construction.

    Currently a thin wrapper around module-level template constants.
    Sprint 3 will inject ServiceRegistry for config/identity lookups.
    """

    def __init__(self, services=None):
        self._svc = services

    # -- System prompt selection ------------------------------------------

    def build_system_prompt(
        self,
        mode: str = "structured",
        custom_protocol: Optional[str] = None,
    ) -> str:
        """Return the system prompt for the given mode.

        Args:
            mode: ``"freeform"`` or ``"structured"`` (default).
            custom_protocol: If non-empty, bypasses built-in templates
                and returns this verbatim.

        Returns:
            The system prompt string.
        """
        if custom_protocol:
            return custom_protocol
        if mode == "freeform":
            return FREEFORM_SYSTEM_PROMPT
        return STRUCTURED_SYSTEM_PROMPT

    def build_summarization_prompt(self) -> str:
        """Return the system prompt for conversation summarization."""
        return SUMMARIZATION_SYSTEM_PROMPT

    def build_context_text(self, **kwargs: str) -> str:
        """Build the 9-section context block from named components.

        All keyword arguments map to context-section labels. Missing
        keys default to ``"(not available)"`` so callers never produce
        a broken prompt. There are 8 sections.

        Sprint 14 round 2 / feature 3.22: delegates to a module-level
        ``lru_cache`` helper so identical context payloads (common
        across consecutive agentic loop turns) skip the string
        fragmenting loop entirely. Cache key = sorted tuple of items.
        """
        return _cached_build_context_text(tuple(sorted(kwargs.items())))

    @staticmethod
    def build_messages(
        system_prompt: str,
        context_text: str,
        history: list,
        user_input: str,
        truncate_fn=None,
        msg_cap: int = 1500,
    ) -> list:
        """Assemble the full messages list for a chat completion.

        Args:
            system_prompt: The system prompt string.
            context_text: The context block (from build_context_text).
            history: List of {"role": ..., "content": ...} dicts.
            user_input: The latest user message.
            truncate_fn: Optional callable (text, cap) -> text.
            msg_cap: Character cap passed to truncate_fn.

        Returns:
            List of message dicts ready for the AI provider.
        """
        messages = [
            {"role": "system", "content": f"{system_prompt}\n\n{context_text}"}
        ]

        for m in history:
            role = "user" if m["role"] == "system" else m["role"]
            content = m["content"]
            if truncate_fn:
                content = truncate_fn(content, msg_cap)
            messages.append({"role": role, "content": content})

        if user_input:
            messages.append({"role": "user", "content": user_input})

        # Budget eviction: pop the oldest history entry until under budget
        budget = int(CONFIG.get("prompt_size_budget_chars", 128000))
        while sum(len(m.get("content", "")) for m in messages) > budget and len(messages) > 2:
            evicted = messages.pop(1)

        return messages


# =============================================================================
# Module-level helpers
# =============================================================================

@functools.lru_cache(maxsize=64)
def _cached_build_context_text(frozen_items: tuple) -> str:
    """Build the 8-section context block from a cached tuple of (key, value) pairs.

    The ``functools.lru_cache`` decorator caches results keyed on the
    sorted ``(label, value)`` pairs so identical context payloads
    (common across consecutive agentic loop turns) skip the string
    fragmenting loop entirely.
    """
    kwargs = dict(frozen_items)
    lines = []
    for label, key in CONTEXT_SECTIONS:
        value = kwargs.get(key, "(not available)")
        if key == "context_summary":
            value = value.strip() if value else ""
        lines.append(f"[{label}]\n{value}")
    return "\n\n".join(lines)


def get_persona_config(name: str) -> dict:
    """Look up a sub-agent persona by name.

    Args:
        name: Persona name (e.g. "Coder", "Researcher").

    Returns:
        Dict with keys: system, temperature, max_tokens.

    Raises:
        KeyError: If the persona name is not found.

    """
    return SUB_AGENT_PERSONAS[name]


def list_personas() -> list:
    """Return a sorted list of available persona names."""
    return sorted(SUB_AGENT_PERSONAS.keys())
