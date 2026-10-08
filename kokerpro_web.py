import json
import sys
import os

from flask import Flask, request, jsonify

from config import CONFIG
from ai_base import get_provider
import memory_vault
import kokertech_bridge


from logging_config import get_logger

logger = get_logger(name="KokerproWeb")

app = Flask(__name__)


def _classify_text(text):
    """
    Classify user text using the trained intent model from the conversation platform.
    Returns dict with intent, confidence, and probabilities.
    """
    try:
        import plugins.classify_intent
        result = plugins.classify_intent.execute({"text": text})
        # If result is a dict, return it as-is
        if isinstance(result, dict):
            return result
        # If result is a JSON string, parse it
        if isinstance(result, str):
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                return {"error": result}
        return {"error": str(result)}
    except ImportError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


@app.route("/")
def home():
    """GET / — returns HTML."""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>KOKERPRO SECURE LINK</title></head>
    <body>
        <h1>KOKERPRO SECURE LINK</h1>
        <p>KokertechAI Web Interface is running.</p>
    </body>
    </html>
    """


@app.route("/classify", methods=["POST"])
def classify():
    """
    Classify user text using the trained intent model.

    Accepts JSON: {"text": "..."}
    Returns JSON: {"intent": "...", "confidence": 0.95, "probabilities": {...}}
    """
    data = request.get_json(silent=True) or {}
    text = data.get("text")

    if text is None:
        return jsonify({"error": "Missing 'text' field"}), 400
    if not isinstance(text, str):
        return jsonify({"error": "'text' must be a string"}), 400
    if not text.strip():
        return jsonify({"error": "'text' cannot be empty"}), 400

    result = _classify_text(text)

    if "error" in result:
        return jsonify(result), 500

    return jsonify(result), 200


@app.route("/chat", methods=["POST"])
def chat():
    """
    Chat endpoint — accepts conversation history and returns AI reply.

    Accepts JSON: {"history": [{"role": "user", "content": "..."}], ...}
    Returns JSON: {"reply": "...", "sys": "..."}
    """
    data = request.get_json(silent=True) or {}
    history = data.get("history", [])

    try:
        # Retrieve core memories for context
        try:
            memories = memory_vault.retrieve_all_memories()
        except Exception:
            memories = ""

        provider = get_provider()
        if provider is None:
            return jsonify({"reply": "Link Error: No AI provider available", "sys": ""}), 200

        # Build messages
        messages = []
        if memories:
            messages.append({"role": "system", "content": f"Core memories: {memories}"})
        for msg in history:
            messages.append(msg)

        response = provider.chat_completion(messages=messages)
        if "error" in response:
            return jsonify({"reply": f"Link Error: {response['error']}", "sys": ""}), 200

        reply = response.get("content", "")
        sys_note = ""

        # Check for actionable commands
        try:
            actions = kokertech_bridge.extract_valid_actions(reply)
            if actions:
                for action in actions:
                    result = kokertech_bridge.handle_ai_intent(action)
                    sys_note = result
        except Exception:
            pass

        return jsonify({"reply": reply, "sys": sys_note}), 200

    except Exception as e:
        return jsonify({"reply": f"Link Error: {e}", "sys": ""}), 200


@app.route("/settings", methods=["GET"])
def get_settings():
    """
    Return a subset of CONFIG keys relevant to the web UI settings page.
    """
    safe_keys = [
        "active_provider", "model_name", "tts_enabled", "notifications_enabled",
        "chat_history_enabled", "freeform_mode", "vram_limit_mb",
        "keepalive_max_ticks", "active_theme",
    ]
    result = {}
    for key in safe_keys:
        if key in CONFIG:
            result[key] = CONFIG[key]
    return jsonify(result), 200


@app.route("/settings", methods=["POST"])
def update_settings():
    """
    Update CONFIG settings from the web UI and persist to disk.
    """
    data = request.get_json(silent=True) or {}
    safe_keys = [
        "active_provider", "model_name", "tts_enabled", "notifications_enabled",
        "chat_history_enabled", "freeform_mode", "vram_limit_mb",
        "keepalive_max_ticks", "active_theme",
    ]
    updated = {}
    for key in safe_keys:
        if key in data:
            CONFIG[key] = data[key]
            updated[key] = data[key]

    if updated:
        from config import save_settings
        save_settings()

    return jsonify({"updated": updated}), 200


def register_webhook_endpoints(flask_app):
    """
    Register dynamic webhook endpoints for all enabled plugins.
    """
    try:
        import plugins.webhook
        count = plugins.webhook.register_all(flask_app)
        return count
    except (ImportError, RuntimeError, AttributeError):
        return 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Kokerpro Flask Web Server")
    parser.add_argument("--port", type=int, default=5050, help="Port to listen on")
    parser.add_argument("--host", default="127.0.0.1", help="Host address to bind")
    args = parser.parse_args()
    register_webhook_endpoints(app)
    app.run(host=args.host, port=args.port, debug=False)

