"""
desire_engine.py — Proactive suggestion generation for KokertechAI.

After every interaction, the desire engine analyses conversation history and
generates proactive suggestions for the user (e.g. "Check disk space",
"Run a system status scan", "Search for X").  These appear as suggestion
cards in the UI sidebar.

Architecture:
    generate_prediction(history, current_prompt) is called by DesireWorker
    in a background thread.  It constructs a structured prompt, calls the
    AI provider with temperature=0.6 for creative suggestions, and parses
    the JSON response into suggestion dicts.

CONFIG keys:
    desire_prompt: System prompt template (defaults to a built-in template).
    desire_model_name: Model for suggestions (defaults to primary model).
"""

import json
import re

from config import CONFIG
from logging_config import get_logger
from ai_base import get_provider


logger = get_logger(name="DesireEngine")

_DESIRE_SYSTEM_PROMPT = (
    "You are a proactive suggestion engine for an AI desktop assistant. "
    "Based on the conversation history and the user's latest input, "
    "suggest 1-3 useful follow-up actions the user might want to take. "
    "Be creative but practical. Return JSON with this exact structure:\n\n"
    '{\n'
    '  "suggestions": [\n'
    '    {\n'
    '      "prediction": "Short action suggestion text",\n'
    '      "action": {\n'
    '        "action": "PLUGIN_COMMAND_NAME",\n'
    '        "params": {}\n'
    '      }\n'
    '    }\n'
    '  ]\n'
    '}\n\n'
    'If no suggestions apply, return {"suggestions": []}.'
)

_DESIRE_JSON_SCHEMA = (
    "Return JSON with this exact structure:\n\n"
    '{\n'
    '  "suggestions": [\n'
    '    {\n'
    '      "prediction": "Short action suggestion text",\n'
    '      "action": {\n'
    '        "action": "PLUGIN_COMMAND_NAME",\n'
    '        "params": {}\n'
    '      }\n'
    '    }\n'
    '  ]\n'
    '}\n\n'
    'If no suggestions apply, return {"suggestions": []}.'
)

# Max history entries to include in the prompt
_MAX_HISTORY_ENTRIES = 3
# Truncate individual history messages to this length
_MAX_HISTORY_CHARS = 500


def generate_prediction(history, current_prompt):
    """Generate proactive suggestions based on conversation history.

    Args:
        history: List of message dicts with ``role`` and ``content`` keys.
            Only the last ``_MAX_HISTORY_ENTRIES`` are included.
        current_prompt: The user's current input text.

    Returns:
        List of suggestion dicts with ``prediction`` and ``action`` keys,
        or ``None`` if the provider failed or the response was unparseable.
    """
    try:
        provider = get_provider(name=CONFIG.get("active_provider", "local_llm"))
        model = CONFIG.get(
            "desire_model_name",
            CONFIG.get("model_name", ""),
        )

        base_prompt = CONFIG.get("desire_prompt", "").strip() or _DESIRE_SYSTEM_PROMPT
        if "suggestions" not in base_prompt.lower() or "json" not in base_prompt.lower():
            system_prompt = f"{base_prompt}\n\n{_DESIRE_JSON_SCHEMA}"
        else:
            system_prompt = base_prompt

        # Build messages: system + truncated history + current prompt
        messages = [{"role": "system", "content": system_prompt}]

        # Include last N history entries, truncating long ones
        recent_history = history[-_MAX_HISTORY_ENTRIES:] if history else []
        for msg in recent_history:
            content = msg.get("content", "")
            if len(content) > _MAX_HISTORY_CHARS:
                content = content[:_MAX_HISTORY_CHARS] + " [TRUNCATED]"
            messages.append({
                "role": msg.get("role", "user"),
                "content": content,
            })

        # Avoid redundant consecutive user prompt if recent history already ends with current_prompt
        last_role = messages[-1]["role"] if messages else None
        last_content = messages[-1].get("content", "") if messages else ""
        if last_role == "user" and last_content.strip() == current_prompt.strip():
            messages[-1]["content"] = f"Current prompt: {current_prompt}"
        else:
            messages.append({
                "role": "user",
                "content": f"Current prompt: {current_prompt}",
            })

        result = provider.chat_completion(
            messages=messages,
            model=model,
            temperature=0.6,
            max_tokens=800,
            timeout=120,
        )

        if result.get("error"):
            logger.debug(f"Desire engine provider error: {result['error']}")
            return None

        content = result.get("content", "")
        if not content:
            return None

        return _parse_suggestions(content)

    except (RuntimeError, ValueError, OSError) as exc:
        logger.debug(f"Desire engine failed: {exc}")
        return None


def _parse_suggestions(content: str):
    """Parse the LLM response and extract suggestions.

    Tries direct JSON parsing first, code fence extraction, then falls back to
    numbered or bulleted plain-text lines if JSON extraction fails.

    Args:
        content: Raw text response from the AI provider.

    Returns:
        List of suggestion dicts, or ``None`` if no suggestions found.
    """
    data = _try_extract_json(content)
    raw_suggestions = None

    if data is not None:
        if isinstance(data, dict):
            raw_suggestions = data.get("suggestions")
            if raw_suggestions is None:
                raw_suggestions = data.get("actions") or data.get("predictions")
            if raw_suggestions is None and "prediction" in data:
                raw_suggestions = [data]
        elif isinstance(data, list):
            raw_suggestions = data

    # Fallback to bullet / numbered list extraction if no JSON suggestions found
    if raw_suggestions is None:
        raw_suggestions = _extract_bullet_suggestions(content)

    if raw_suggestions is None:
        logger.debug("Desire engine: no valid JSON or structured suggestions in response")
        return None

    if not raw_suggestions:
        return []

    # Normalize suggestions to list of dicts with 'prediction' and 'action'
    suggestions = []
    for item in raw_suggestions:
        if isinstance(item, dict):
            pred = (
                item.get("prediction")
                or item.get("title")
                or item.get("suggestion")
                or item.get("text")
                or item.get("action_text")
                or ""
            )
            action = item.get("action", {})
            if isinstance(action, str):
                action = {"action": action, "params": {}}
            elif not isinstance(action, dict):
                action = {"action": "", "params": {}}
            if pred:
                suggestions.append({
                    "prediction": str(pred).strip(),
                    "action": action,
                })
        elif isinstance(item, str) and item.strip():
            suggestions.append({
                "prediction": item.strip(),
                "action": {"action": "", "params": {}},
            })

    return suggestions


def _try_extract_json(content: str):
    """Attempt to parse content as JSON, with fallback extraction strategies."""
    if not content:
        return None

    # Strip thinking tags from reasoning models (<think>...</think>)
    clean = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    # Strategy 1: Direct parse
    try:
        return json.loads(clean)
    except (json.JSONDecodeError, ValueError):
        pass

    # Strategy 2: Code fence extraction
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", clean, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 3: Find JSON object in surrounding text
    m = re.search(r"\{.*\}", clean, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    # Strategy 4: Find JSON array in surrounding text
    m = re.search(r"\[.*\]", clean, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(0))
            if isinstance(arr, list):
                return {"suggestions": arr}
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _extract_bullet_suggestions(content: str):
    """Fallback extraction when the model returns numbered or bulleted lines instead of JSON."""
    if not content:
        return None

    clean = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    suggestions = []
    for line in clean.splitlines():
        line = line.strip()
        if not line:
            continue
        # Match lines starting with "1. ", "1) ", "- ", "* ", "• "
        m = re.match(r"^(?:(?:\d+[\.\)]|[-*•])\s+)(.+)$", line)
        if m:
            text = m.group(1).strip()
            # Clean markdown bold/italic or surrounding quotes
            text = text.strip("*_`\"' ")
            if text and len(text) >= 3:
                suggestions.append({
                    "prediction": text,
                    "action": {"action": "", "params": {}},
                })
    return suggestions if suggestions else None
