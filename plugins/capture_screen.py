# plugins/capture_screen.py
import os
import json
import logging

logger = logging.getLogger(__name__)

PLUGIN_METADATA = {
    "name": "Capture & Analyze Screen",
    "description": "Captures the current screen (or specified monitor) and optionally analyzes it using a vision-capable AI model",
    "version": "1.0.0",
    "tags": ["screen", "capture", "vision", "screenshot", "analysis"],
    "author": "KokertechAI",
    "requires": ["mss", "Pillow"],
    "permissions": ['screen']
}

COMMAND_NAME = "CAPTURE_SCREEN"
SCHEMA = {
    "action": "CAPTURE_SCREEN",
    "monitor": "<optional monitor number, default 1>",
    "analyze": "<optional boolean, if true runs vision analysis>",
    "query": "<optional natural language question about the screen content>"
}

WORKSPACE_DIR = r"C:\KokertechAI"

def execute(intent_json):
    monitor = intent_json.get("monitor", 1)
    analyze = intent_json.get("analyze", False)
    query = intent_json.get("query", "")

    try:
        from copilot_features import ScreenCapture, VisionAnalyzer
    except ImportError:
        return "❌ Copilot features module not found."

    try:
        save_dir = os.path.join(WORKSPACE_DIR, "data", "screenshots")
        capture = ScreenCapture(save_dir=save_dir)
    except ImportError as e:
        return f"❌ Missing screen capture dependency: {e}\nRun: pip install mss pywin32"

    try:
        if isinstance(monitor, str) and monitor.isdigit():
            monitor = int(monitor)
        filepath, active_window = capture.capture(monitor_index=int(monitor))
    except Exception as e:
        return f"❌ Screen capture failed: {e}"

    result_parts = [
        f"✅ Screen captured from {active_window}",
        f"   Saved to: {filepath}",
    ]

    if analyze or query:
        try:
            from config import CONFIG
            vision = VisionAnalyzer(provider_name=CONFIG.get("active_provider", "local_llm"), model_name=CONFIG.get("model_name", ""))
            context = f"Active window: {active_window}"
            vision_model = CONFIG.get("vision_model", "")
            model_info = f" (model: {vision_model})" if vision_model else ""
            result_parts.append(f"\n🔍 Analyzing{model_info}...")
            analysis = vision.analyze(filepath, context=context)
            result_parts.append(f"\n{analysis}")
        except Exception as e:
            result_parts.append(f"\n⚠️ Vision analysis failed: {e}")

    # Cleanup old screenshots (best-effort housekeeping — a failure here
    # must not fail the capture result; log at debug per §13 read-fallback
    # convention rather than swallowing silently).
    try:
        capture.cleanup(max_files=10)
    except Exception as e:
        logger.debug(f"Screenshot cleanup (max_files=10) failed (non-fatal): {e}")

    return "\n".join(result_parts)
