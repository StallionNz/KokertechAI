import json

from logging_config import get_logger

logger = get_logger(name="kokertech_bridge")


def extract_valid_actions(text):
    """Extract JSON objects with 'action' keys from text.

    Finds all JSON objects ``{...}`` embedded in *text* and returns
    those that contain an ``action`` key.  Malformed JSON is silently
    skipped (logged at debug level).

    Returns:
        List of parsed dicts containing an ``action`` key.
    """
    results = []
    # Find candidate JSON objects: content between outermost { and }
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start:i + 1]
                start = -1
                try:
                    obj = json.loads(candidate)
                    if isinstance(obj, dict) and 'action' in obj:
                        results.append(obj)
                except (json.JSONDecodeError, ValueError):
                    pass  # not valid JSON — skip silently
    return results


def handle_ai_intent(intent_json):
    """Dispatch an intent JSON and return the result string.

    Handles basic file operations (CREATE_FOLDER, WRITE_FILE, READ_FILE,
    DELETE_FILE) directly, then falls back to the plugin registry for
    all other actions.

    Args:
        intent_json: Dict with at least ``{"action": "COMMAND_NAME", ...}``.

    Returns:
        Result string from the operation, or an error description.
    """
    import os
    import pathlib

    action = intent_json.get("action", "") if isinstance(intent_json, dict) else ""

    # ── Direct file operations ──────────────────────────────────
    if action == "CREATE_FOLDER":
        path = intent_json.get("path", "")
        if not path:
            return "Missing 'path' in CREATE_FOLDER intent"
        try:
            os.makedirs(path, exist_ok=True)
            return f"Created folder: {path}"
        except OSError as e:
            return f"Error creating folder '{path}': {e}"

    if action == "WRITE_FILE":
        # Check if plugin is disabled first (before direct handling)
        try:
            from plugin_registry import registry
            if action in registry.disabled:
                return f"Plugin '{action}' is currently disabled."
        except Exception:
            # Deliberate (silent-catch audit): registry import/unavailable
            # means disabled-check cannot run -- fall through to direct
            # file handling below (action still served).
            pass
        filename = intent_json.get("filename") or intent_json.get("path", "")
        content = intent_json.get("content", "")
        if not filename:
            return "Missing 'filename' in WRITE_FILE intent"
        try:
            parent_dir = pathlib.Path(filename).parent
            os.makedirs(str(parent_dir), exist_ok=True)
            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
            return f"Successfully wrote to {filename}"
        except OSError as e:
            return f"Error writing file '{filename}': {e}"

    if action == "READ_FILE":
        # Check if plugin is disabled first (before direct handling)
        try:
            from plugin_registry import registry
            if action in registry.disabled:
                return f"Plugin '{action}' is currently disabled."
        except Exception:
            # Deliberate (silent-catch audit): registry import/unavailable
            # means disabled-check cannot run -- fall through to direct
            # file handling below (action still served).
            pass
        filename = intent_json.get("filename") or intent_json.get("path", "")
        if not filename:
            return "Missing 'filename' in READ_FILE intent"
        if not os.path.exists(filename):
            return f"File not found: {filename}"
        try:
            with open(filename, "r", encoding="utf-8") as f:
                content = f.read()
            return f"Read {len(content)} chars from {filename}:\n\n{content}"
        except OSError as e:
            return f"Error reading file '{filename}': {e}"

    if action == "DELETE_FILE":
        path = intent_json.get("path", "")
        if not path:
            return "Missing 'path' in DELETE_FILE intent"
        if not os.path.exists(path):
            return f"File not found: {path}"
        if os.path.isdir(path):
            return f"Path is a directory, not a file: {path}"
        try:
            os.remove(path)
            return f"Successfully deleted {path}"
        except OSError as e:
            return f"Error deleting file '{path}': {e}"

    # ── Fallback to plugin registry ─────────────────────────────
    try:
        from plugin_registry import registry
        return registry.execute_command(intent_json)
    except Exception as e:
        logger.error(f"handle_ai_intent failed: {e}")
        return f"Action '{action}' is unknown or not registered. Error: {e}"
