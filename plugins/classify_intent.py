"""
classify_intent.py — KokertechAI Plugin
Wraps the trained Conversation Platform intent classifier (model.pkl) as a plugin command.
Model source: Projects/KokertechConversationplatform/model.pkl
"""

import os
import json
import logging
import threading

import joblib
import numpy as np

PLUGIN_METADATA = {
    "name": "Classify Intent",
    "description": "Classifies user text into one of six intents (Greeting, Support, Yes, No, Goodbye, Pricing) using a trained ML model",
    "version": "1.0.0",
    "tags": ["ml", "classifier", "intent", "conversation"],
    "author": "KokertechAI",
    "requires": ["joblib", "scikit-learn"],
    "permissions": ['ai']
}

COMMAND_NAME = "CLASSIFY_INTENT"
SCHEMA = {
    "action": "CLASSIFY_INTENT",
    "text": "<the user message to classify>"
}

logger = logging.getLogger("Plugin.ClassifyIntent")

# Resolve the model path relative to the workspace root
_CONVERSATION_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Projects",
    "KokertechConversationplatform"
)
_MODEL_PATH = os.path.join(_CONVERSATION_DIR, "model.pkl")

# Module-level cache: model loaded once on first use
_model = None
_lock = threading.Lock()


def _load_model():
    """Lazy-load the trained model. Returns (model, error_string).
    Thread-safe: uses double-check locking to avoid redundant loads.
    """
    global _model
    if _model is not None:
        return _model, None
    with _lock:
        if _model is not None:
            return _model, None
        if not os.path.exists(_MODEL_PATH):
            return None, f"\u274c Model file not found at {_MODEL_PATH}. Train the model via pipeline.py first."
        try:
            _model = joblib.load(_MODEL_PATH)
            logger.info(f"Intent classifier loaded: {len(_model.classes_)} classes")
            return _model, None
        except Exception as e:
            logger.error(f"Failed to load intent classifier model: {e}")
            return None, f"\u274c Failed to load model: {e}"


def execute(intent_json):
    text = intent_json.get("text")
    if not text or not isinstance(text, str) or not text.strip():
        return "\u274c Missing or invalid 'text' parameter for CLASSIFY_INTENT."

    model, error = _load_model()
    if error:
        return error  # Already includes cross mark prefix from _load_model()

    try:
        prediction = model.predict([text.strip()])[0]
        probabilities = model.predict_proba([text.strip()])[0]
        confidence = float(max(probabilities))
        class_probs = dict(zip(model.classes_.tolist(), probabilities.tolist()))

        result = {
            "intent": str(prediction),
            "confidence": round(confidence, 3),
            "probabilities": class_probs,
            "text": text.strip()
        }

        # Attach a human-readable response if confidence is low
        if confidence < 0.4:
            result["note"] = "Low confidence -- the model may not have enough training data for this input."

        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Classification error: {e}")
        return f"\u274c Classification failed: {e}"
