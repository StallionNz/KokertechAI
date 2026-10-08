"""
plugins/webhook.py — Webhook Triggers for Plugins.

Exposes a WEBHOOK command and manages dynamic POST endpoints on the
Flask web server (kokerpro_web.py). Each enabled plugin gets a
/webhook/<command_name> endpoint that accepts JSON payloads and
dispatches to the plugin's execute() function.

Sprint 5 backlog item (Feature suggestion #17).

Usage (HTTP POST):
    POST /webhook/SEARCH_WEB
    Content-Type: application/json
    {"query": "AI news today"}

Returns:
    {"ok": true, "result": "..."} on success
    {"ok": false, "error": "..."} on error
"""

import json
import threading

from logging_config import get_logger

logger = get_logger(name="WebhookPlugin")

PLUGIN_METADATA = {
    "name": "Webhook Triggers",
    "description": "Exposes plugins as HTTP POST webhook endpoints on the Flask web server for external integrations",
    "version": "1.0.0",
    "tags": ["webhook", "http", "integration", "automation"],
    "author": "KokertechAI",
    "requires": ["flask"],
    "permissions": ['network'],
}

COMMAND_NAME = "WEBHOOK"
SCHEMA = {
    "action": "WEBHOOK",
    "target": "<plugin_command_name to trigger>",
    "payload": {"<key>": "<value>"},
    "webhook_url": "<optional: /webhook/TARGET_URL>"
}


# ── Webhook endpoint registry ──────────────────────────────────────
# Maps command_name -> {"func": callable, "schema": dict, "metadata": dict}
_registered_webhooks = {}
_registration_lock = threading.Lock()


def register_all(flask_app):
    """Register webhook POST endpoints for all enabled plugins on the given Flask app.

    Scans the plugin registry for available commands and creates a
    ``/webhook/<command_name>`` endpoint for each one.

    Args:
        flask_app: A Flask application instance (from kokerpro_web.py).
    """
    try:
        import plugin_registry
    except ImportError:
        logger.warning("Plugin registry not available — skipping webhook registration")
        return

    pr = plugin_registry.registry
    registered = 0

    with _registration_lock:
        _registered_webhooks.clear()

        for cmd in sorted(pr.plugins.keys()):
            if not pr.is_enabled(cmd):
                continue

            # Build schema info from registry
            schema = pr.schemas.get(cmd, {})
            meta = pr.metadata.get(cmd, {})
            plugin_fn = pr.plugins[cmd]

            _registered_webhooks[cmd] = {
                "func": plugin_fn,
                "schema": schema,
                "metadata": meta,
            }

            # Register the Flask route inline
            _register_route(flask_app, cmd, plugin_fn)
            registered += 1

        if registered:
            logger.info(f"Registered {registered} webhook endpoints for plugins")

    return registered


def _register_route(app, cmd, plugin_fn):
    """Add a POST /webhook/<cmd> route to the Flask app for this plugin.

    The route:
      - Accepts JSON payload
      - Calls plugin_fn.execute(payload)
      - Returns JSON result
    """
    import functools

    @app.route(f"/webhook/{cmd}", methods=["POST"])
    @functools.wraps(plugin_fn.execute if hasattr(plugin_fn, "execute") else (lambda: None))
    def _webhook_handler():
        from flask import request, jsonify

        try:
            payload = request.get_json(silent=True) or {}
        except Exception:
            return jsonify({"ok": False, "error": "Invalid JSON payload"}), 400

        try:
            result = plugin_fn.execute(payload)
            return jsonify({"ok": True, "result": str(result)})
        except Exception as e:
            logger.error(f"Webhook /webhook/{cmd} execution failed: {e}")
            return jsonify({"ok": False, "error": str(e)}), 500

    # Store the endpoint name for discovery
    _webhook_handler.__webhook_cmd__ = cmd


def list_endpoints():
    """Return a list of registered webhook endpoints.

    Returns:
        list of dicts: [{cmd, name, description, schema}, ...]
    """
    with _registration_lock:
        return [
            {
                "cmd": cmd,
                "name": info["metadata"].get("name", cmd),
                "description": info["metadata"].get("description", ""),
                "schema": info["schema"],
            }
            for cmd, info in sorted(_registered_webhooks.items())
        ]


def unregister_all(flask_app):
    """Remove all webhook routes from the Flask app.

    Flask doesn't natively support route removal, so this clears the
    internal registry and logs that webhook endpoints are no longer valid.
    """
    with _registration_lock:
        count = len(_registered_webhooks)
        _registered_webhooks.clear()
        logger.info(f"Unregistered {count} webhook endpoints")


# ── Plugin execute function ────────────────────────────────────────


def execute(intent_json):
    """Trigger a plugin via the WEBHOOK command.

    This is the interface through which the AI can manually invoke
    a webhook-style plugin dispatch. It can also be used to trigger
    any enabled plugin by name from the chat interface.

    intent_json keys:
        target   – The command name of the plugin to trigger (required).
        payload  – Dict of parameters to pass to the plugin's execute() (optional).

    Returns:
        str: Result of the triggered plugin, or error description.
    """
    target = intent_json.get("target", "").upper().strip()
    payload = intent_json.get("payload", {})

    if not target:
        return "❌ Missing 'target' parameter — specify a plugin command name (e.g. SEARCH_WEB)"

    # Resolve from the live plugin registry
    try:
        import plugin_registry
        pr = plugin_registry.registry
    except ImportError:
        return "❌ Plugin registry not available"

    if target not in pr.plugins:
        available = ", ".join(sorted(pr.plugins.keys()))
        return f"❌ Unknown plugin '{target}'. Available: {available}"

    if not pr.is_enabled(target):
        return f"❌ Plugin '{target}' is disabled — enable it in the Plugins tab first"

    try:
        plugin_fn = pr.plugins[target]
        result = plugin_fn.execute(payload)
        logger.info(f"WEBHOOK -> {target}: executed via AI command")
        return f"✅ Webhook triggered '{target}': {result}"
    except Exception as e:
        logger.error(f"WEBHOOK -> {target} failed: {e}")
        return f"❌ Webhook '{target}' failed: {e}"
