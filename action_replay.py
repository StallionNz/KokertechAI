"""
action_replay.py -- Deterministic action replay for regression testing.

Stores AI prompt->response pairs in a JSONL file. Supports two modes:
  - ``"record"``: Capture every AI response for later replay.
  - ``"replay"``: Return stored responses instead of calling the AI provider.

CONFIG keys:
  action_replay_mode: ``"off"`` (default), ``"record"``, or ``"replay"``
  action_replay_file: path to the JSONL replay file
                      (default: data/action_replay.jsonl)

Architecture:
    compute_messages_hash() produces a deterministic SHA-256 hash from
    (messages, model, temperature, response_format) for replay lookup.
    get_replay() / get_replay() read/write a JSONL file keyed by this hash.
    ActionReplay wraps the file-level operations with a thread-safe lock.
"""

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta

from logging_config import get_logger


logger = get_logger(name="ActionReplay")

_DEFAULT_REPLAY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data", "action_replay.jsonl",
)


# ---------------------------------------------------------------------------
# Mode helpers
# ---------------------------------------------------------------------------


def get_replay_mode() -> str:
    """Return the current replay mode from CONFIG.

    Returns ``"off"`` (default), ``"record"``, or ``"replay"``.
    Falls back to ``"off"`` if the CONFIG key is missing or invalid.
    """
    try:
        from config import CONFIG
        mode = CONFIG.get("action_replay_mode", "off")
        if mode in ("off", "record", "replay"):
            return mode
        return "off"
    except Exception:
        return "off"


def get_replay_file() -> str:
    """Return the path to the replay JSONL file from CONFIG.

    Falls back to ``_DEFAULT_REPLAY_FILE``.
    """
    try:
        from config import CONFIG
        return CONFIG.get("action_replay_file", _DEFAULT_REPLAY_FILE)
    except Exception:
        return _DEFAULT_REPLAY_FILE


# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------


def compute_messages_hash(
    messages: list,
    model: str,
    temperature: float,
    response_format: dict = None,
) -> str:
    """Compute a deterministic SHA-256 hash of the messages for replay lookup.

    Includes ``response_format`` so XML and JSON replays don't collide.
    Excludes ``max_tokens`` and ``tools`` for reuse across minor parameter
    tweaks.

    Args:
        messages: The messages list sent to the AI provider.
        model: Model name string.
        temperature: Temperature value.
        response_format: Optional response format dict
            (e.g. ``{"type": "xml"}``).

    Returns:
        Hex digest SHA-256 hash string.
    """
    payload = {
        "messages": _canonicalise_messages(messages),
        "model": str(model),
        "temperature": float(temperature),
        "response_format": response_format,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonicalise_messages(messages: list) -> list:
    """Normalise messages for reproducible hashing.

    Strips ``max_tokens`` and ``tools`` from each message's metadata
    (if present) so that minor parameter tweaks don't change the hash.
    """
    result = []
    for msg in messages:
        clean = {
            "role": msg.get("role", ""),
            "content": msg.get("content", ""),
        }
        # Preserve only the fields that affect semantic content
        if "name" in msg:
            clean["name"] = msg["name"]
        result.append(clean)
    return result


# ---------------------------------------------------------------------------
# Replay file operations (thread-safe)
# ---------------------------------------------------------------------------

_REPLAY_LOCK = threading.Lock()


def _ensure_dir(filepath: str) -> None:
    """Create parent directories for *filepath* if they don't exist."""
    parent = os.path.dirname(filepath)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _load_lines(filepath: str) -> list:
    """Load all non-empty lines from a JSONL file."""
    if not os.path.isfile(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []


def _find_replay(hash_key: str, filepath: str):
    """Search a JSONL file for a matching hash key.

    Each JSONL line is expected to be ``{"hash": "<key>", "response": ...}``.

    Returns:
        The response dict, or ``None`` if no match.
    """
    lines = _load_lines(filepath)
    for line in lines:
        try:
            entry = json.loads(line)
            if entry.get("hash") == hash_key:
                return entry.get("response")
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _append_record(hash_key: str, response: dict, filepath: str) -> None:
    """Append a single hash->response entry to the JSONL file."""
    _ensure_dir(filepath)
    entry = json.dumps(
        {"hash": hash_key, "response": response, "ts": datetime.now().isoformat()},
        ensure_ascii=False,
    )
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError as exc:
        logger.warning(f"ActionReplay write failed: {exc}")


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------


class ActionReplay:
    """Thread-safe wrapper around the JSONL replay file.

    Usage::

        ar = ActionReplay(filepath="data/action_replay.jsonl")
        hash_key = compute_messages_hash(messages, model, temperature)
        if ar.has(hash_key):
            response = ar.get(hash_key)
        else:
            response = call_provider(...)
            ar.record(hash_key, response)
    """

    def __init__(self, filepath: str = None):
        self.filepath = filepath or get_replay_file()

    def has(self, hash_key: str) -> bool:
        """Check whether a replay entry exists for *hash_key*."""
        with _REPLAY_LOCK:
            return _find_replay(hash_key, self.filepath) is not None

    def get(self, hash_key: str):
        """Retrieve a stored response by *hash_key*.

        Returns:
            The response dict, or ``None`` if not found.
        """
        with _REPLAY_LOCK:
            return _find_replay(hash_key, self.filepath)

    def record(self, hash_key: str, response: dict) -> None:
        """Store a response for later replay."""
        with _REPLAY_LOCK:
            _append_record(hash_key, response, self.filepath)
            logger.info(
                f"ActionReplay recorded hash={hash_key[:12]}..."
            )

    def clear(self) -> None:
        """Delete the replay file (all recorded entries)."""
        with _REPLAY_LOCK:
            try:
                if os.path.isfile(self.filepath):
                    os.remove(self.filepath)
                    logger.info("ActionReplay file cleared")
            except OSError as exc:
                logger.warning(f"ActionReplay clear failed: {exc}")


def get_replay() -> ActionReplay:
    """Return a singleton ``ActionReplay`` instance for the configured file."""
    return ActionReplay(filepath=get_replay_file())


def reset_replay() -> None:
    """Clear all recorded replay data (delete the JSONL file)."""
    get_replay().clear()
