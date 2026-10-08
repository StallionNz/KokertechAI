"""
config.py — Centralized configuration for KokertechAI.

Merges CONFIG and IDENTITY_CONFIG from 6 modules into one source of truth.
"""

import os
import json
import time
import shutil
import zipfile
import logging
from datetime import datetime

# Use stdlib logging (not logging_config) to avoid circular imports —
# logging_config.py imports from config.py, so importing it here would cycle.
_logger = logging.getLogger("Config")

WORKSPACE_DIR = os.environ.get(
    "KOKERTECH_WORKSPACE_DIR",
    os.path.dirname(os.path.abspath(__file__)),
)
SETTINGS_PATH = os.path.join(WORKSPACE_DIR, "app_settings.json")
IDENTITY_PATH = os.path.join(WORKSPACE_DIR, "user_identity.json")
DB_PATH = os.path.join(WORKSPACE_DIR, "kokertech_vault.db")
PLUGIN_DIR = os.path.join(WORKSPACE_DIR, "plugins")
SUMMARY_FILE = os.path.join(WORKSPACE_DIR, "context_summary.txt")
LOG_FILE = os.path.join(WORKSPACE_DIR, "execution_log.txt")
PERSONAS_PATH = os.path.join(WORKSPACE_DIR, "personas.json")
LOG_DIR = os.path.join(WORKSPACE_DIR, "data", "logs")
SETTINGS_BACKUP_DIR = os.path.join(WORKSPACE_DIR, "settings_backups")
GRAMMAR_DIR = os.path.join(WORKSPACE_DIR, "data", "grammars")
SAVE_MARKER_PATH = os.path.join(LOG_DIR, ".save_complete")
MAX_BACKUPS = 20

# mtime + TTL cache for _load_settings() — prevents redundant disk I/O
# when called repeatedly within the min-interval window.
_settings_mtime_cache: dict = {"mtime": 0.0, "last_check": 0.0}
settings_load_min_interval_sec: float = 1.0

CONFIG = {
    "vram_limit_mb": 2048,
    "vram_warning_pct": 88,
    "vram_critical_pct": 95,
    "ram_warning_pct": 80,
    "ram_critical_pct": 95,
    "disk_warning_pct": 85,
    "disk_critical_pct": 95,
    "notifications_enabled": True,
    "vram_notifications_enabled": True,
    "ram_notifications_enabled": True,
    "disk_notifications_enabled": True,
    # 12 ticks × 30s = 6min keepalive patience (raised from 4 on 2026-09-25:
    # 2min+ generations were hitting the old 120s auto-abort mid-stream).
    "keepalive_max_ticks": 12,
    "active_agent": "Buffy",
    "hive_orchestration_enabled": False,
    "hive_max_retries": 2,
    "speculative_drafting_enabled": False,
    "speculative_draft_model": "",
    "sensory_mesh_enabled": False,
    "sensory_capture_target": "active_window",
    "sensory_max_dimension": 1024,
    "duplex_barge_in_enabled": True,
    "duplex_energy_multiplier": 2.0,
    "duplex_min_speech_duration": 0.2,
    "api_cmd": "",
    "api_port": 8080,
    "code_intelligence_audit_log": False,
    "KOKERTECH_DISCOVERY_URL": "http://127.0.0.1:1337/v1/models",
    "discovery_timeout_seconds": 5,
    "discovery_probe_on_startup": False,
    "model_prewarm_enabled": False,
    "response_cache_enabled": False,
    "retry_max_attempts": 3,
    "retry_base_delay_ms": 500,
    "retry_max_delay_ms": 10000,
    "retry_backoff_factor": 2.0,
    "retry_jitter": True,
    "embedding_cache_max": 512,
    "summarize_evict_threshold": 200,
    "summarize_evict_skip_recent": True,
    "llm_n_ctx_auto": False,
    "llm_n_gpu_layers_auto": False,
    "kv_cache_prefix_reuse": False,
    "kv_cache_ram_mb": 512,
    "vault_wal_autocheckpoint_threshold_mb": 16,
    "vault_wal_checkpoint_on_close": True,
    "vault_fts_self_heal": True,
    "batch_embedding_size": 32,
    "_window_geometry": "",
    "_window_maximized": False,
    "name": "Jacques",
    "tone": "Direct, blunt, and highly conversational.",
    "preferences": "Keep it concise and skip the safety lectures.",
    "background": "Owner of Kokertech Pty Ltd, Senior Electrical Engineer.",
    "active_theme": "Terminal (Amber/Orange)",
    "active_provider": "local_llm",
    "desire_model_name": "qwen2.5-0.5b-instruct",
    "auditor_model_name": "qwen2.5-0.5b-instruct",
    "desire_prompt": "Predict up to 10 helpful actions the user might want to do NEXT. Include options for executing Python scripts, searching the web for specific topics, listing files, or checking system status. Keep descriptions short.",
    "active_persona": "You are a helpful AI assistant.",
    "saved_personas": [],
    "freeform_mode": False,
    "mock_mode": False,
    "structured_format": "xml",
    "tool_use_stream_progress": True,
    "tool_use_destructive_confirm": True,
    "tool_use_per_tool_timeout": 30,
    "tool_call_budget": 0,
    "tool_use_audit_log": False,
    "tool_use_max_retries": 3,
    "tool_use_summarize_results": True,
    "tool_schema_prewarm": True,
    "tool_sandbox_enabled": True,
    "tool_sandbox_memory_mb": 512,
    "tool_sandbox_timeout_s": 30,
    "tool_require_hitl_for_destructive": True,
    "tool_execution_audit_log": True,
    "stream_buffer_size": 4,
    "grammar_hot_reload": True,
    "hybrid_search_enabled": True,
    "hybrid_search_alpha": 0.6,
    "fts_rebuild_on_start": False,
    "graphrag_enabled": True,
    "graphrag_llm_extraction": True,
    "cross_encoder_model": "all-MiniLM-L6-v2",
    "query_expansion_enabled": True,
    "query_expansion_synonyms": {},
    "fts_content_weight": 10,
    "fts_summary_weight": 1,
    "adaptive_alpha_enabled": False,
    "snippet_max_chars": 200,
    "dedup_similarity_threshold": 0.85,
    "time_decay_half_life_days": 30,
    "search_analytics_enabled": False,
    "graphrag_entity_embeddings": False,
    "graphrag_entity_embedding_model": "all-MiniLM-L6-v2",
    "graphrag_fuzzy_merge_threshold": 0.92,
    "graphrag_confidence_propagation": False,
    "graphrag_community_detection": False,
    "graphrag_relationship_decay_days": 90,
    "graphrag_llm_fallback_model": "",
    "graphrag_cascade_cleanup": False,
    "god_reviewer_contract": True,
    "chat_history_enabled": True,
    "chat_history_debounce_ms": 500,
    "max_thinking_tokens": 512,
    "max_final_tokens": 2048,
    "trigger_hotkey": "ctrl+alt+a",
    "read_hotkey": "ctrl+alt+s",
    "voice_hotkey": "ctrl+alt+v",
    "hud_hotkey": "ctrl+alt+space",
    "wake_word": "hey koker",
    "wake_word_enabled": False,
    "always_on_top": False,
    "debug_logging": False,
    "autosave_logs": True,
    "resource_monitor_enabled": True,
    "tts_enabled": True,
    "tts_speed": 180,
    "tts_voice": "",
    "tts_model_dir": "",
    "whisper_model": "tiny.en",
    "voice_vad_enabled": True,
    "voice_vad_silence_seconds": 1.2,
    "voice_vad_energy_threshold": 120.0,
    "voice_auto_speak": False,
    "voice_auto_submit": True,
    "vision_model": "",
    "docker_sandbox_enabled": True,
    "docker_sandbox_image": "python:3.11-slim",
    "docker_sandbox_timeout": 30,
    "docker_sandbox_memory": "256m",
    "mcp_servers": [],
    "custom_themes": {},
    "protocol_prompt": "",
    "protocol_presets": {},
    "active_skill": "",
    "web_server_enabled": False,
    "web_server_port": 5050,
    "models_dir": os.path.join(WORKSPACE_DIR, "models"),
    # No default model: the user must explicitly pick one at startup
    # (Settings → Apply). Empty string means "no model loaded yet".
    "model_file": "",
    "llm_n_ctx": 4096,
    "llm_n_gpu_layers": 0,
    "llm_n_threads": None,
    "model_name": "",
}


IDENTITY_CONFIG = {
    "name": "Jacques",
    "tone": "Direct, blunt, and highly conversational.",
    "preferences": "Keep it concise and skip the safety lectures.",
    "background": "Owner of Kokertech Pty Ltd, Senior Electrical Engineer."
}

def _load_dotenv() -> None:
    """Load .env file into os.environ (never overrides existing env vars)."""
    env_path = os.path.join(WORKSPACE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except (OSError, UnicodeDecodeError) as e:
        _logger.debug(f"Failed to load .env file (non-fatal): {e}")

def _load_settings() -> None:
    """Load settings from app_settings.json on disk with TTL + mtime caching.

    Two-layer short-circuit:
      1. TTL: skip entirely if less than ``settings_load_min_interval_sec``
         (1.0s) has elapsed since the last check.
      2. Mtime: if TTL has expired, compare the file's current mtime against
         the cached value — only re-read from disk when the file has changed.
    """
    now = time.time()
    elapsed = now - _settings_mtime_cache["last_check"]

    # Layer 1 — TTL short-circuit
    if _settings_mtime_cache["last_check"] > 0 and elapsed < settings_load_min_interval_sec:
        return

    _settings_mtime_cache["last_check"] = now

    if not os.path.exists(SETTINGS_PATH):
        return

    try:
        current_mtime = os.path.getmtime(SETTINGS_PATH)
    except OSError as e:
        _logger.debug(f"Could not stat {SETTINGS_PATH} for mtime: {e}")
        current_mtime = 0.0

    # Layer 2 — mtime short-circuit (file hasn't changed since last read)
    if _settings_mtime_cache["mtime"] > 0 and current_mtime == _settings_mtime_cache["mtime"]:
        return

    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                CONFIG.update(data)
            else:
                _logger.warning(f"Settings file {SETTINGS_PATH} did not contain a JSON object (got {type(data).__name__})")
        _settings_mtime_cache["mtime"] = current_mtime
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as e:
        _logger.warning(f"Failed to load settings from {SETTINGS_PATH}: {e}")

def _load_identity() -> None:
    if os.path.exists(IDENTITY_PATH):
        try:
            with open(IDENTITY_PATH, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    IDENTITY_CONFIG.update(data)
                else:
                    _logger.warning(f"Identity file {IDENTITY_PATH} did not contain a JSON object (got {type(data).__name__})")
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as e:
            _logger.warning(f"Failed to load identity from {IDENTITY_PATH}: {e}")
    for _k in ("name", "tone", "preferences", "background"):
        if _k in IDENTITY_CONFIG:
            CONFIG[_k] = IDENTITY_CONFIG[_k]

def _load_personas() -> None:
    """Load personas list from disk, maintaining custom entries added via UI."""
    if os.path.exists(PERSONAS_PATH):
        try:
            with open(PERSONAS_PATH, encoding="utf-8") as f:
                static_personas = json.load(f)
                if isinstance(static_personas, list) and static_personas:
                    # Only seed from static file if no saved personas yet
                    existing = CONFIG.get("saved_personas", [])
                    if not existing:
                        CONFIG["saved_personas"] = static_personas[:]
                    else:
                        # Merge: ensure static personas are present, keep custom ones
                        seen = set(existing)
                        for p in static_personas:
                            if p not in seen:
                                existing.append(p)
                                seen.add(p)
                        CONFIG["saved_personas"] = existing
                    # Ensure active_persona is one of the available personas
                    all_personas = CONFIG["saved_personas"]
                    if CONFIG["active_persona"] not in all_personas:
                        CONFIG["active_persona"] = all_personas[0] if all_personas else "You are a helpful AI assistant."
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            _logger.warning(f"Failed to load personas from {PERSONAS_PATH}: {e}")

def _snapshot_backups() -> None:
    """Snapshot both app_settings.json and user_identity.json into settings_backups/.
    Uses a common timestamp so they can be restored together.
    Also prunes old backup pairs to keep within MAX_BACKUPS.
    """
    if not os.path.exists(SETTINGS_PATH) and not os.path.exists(IDENTITY_PATH):
        return
    os.makedirs(SETTINGS_BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if os.path.exists(SETTINGS_PATH):
        backup_name = f"app_settings_{ts}.json"
        shutil.copy2(SETTINGS_PATH, os.path.join(SETTINGS_BACKUP_DIR, backup_name))
    if os.path.exists(IDENTITY_PATH):
        backup_name = f"user_identity_{ts}.json"
        shutil.copy2(IDENTITY_PATH, os.path.join(SETTINGS_BACKUP_DIR, backup_name))

    # Prune old backups — count unique timestamps (each may have 2 files)
    timestamps = set()
    for fname in os.listdir(SETTINGS_BACKUP_DIR):
        # Extract ts from app_settings_TS.json or user_identity_TS.json
        parts = fname.split("_", 2)
        if len(parts) == 3:
            ts_str = parts[2].replace(".json", "")
            timestamps.add(ts_str)
        elif len(parts) >= 2:
            ts_str = parts[-1].replace(".json", "")
            timestamps.add(ts_str)
    timestamps = sorted(timestamps)
    while len(timestamps) > MAX_BACKUPS:
        old_ts = timestamps.pop(0)
        for fname in list(os.listdir(SETTINGS_BACKUP_DIR)):
            if old_ts in fname:
                try:
                    os.remove(os.path.join(SETTINGS_BACKUP_DIR, fname))
                except OSError as e:
                    _logger.debug(f"Could not prune old backup {fname}: {e}")


def _write_save_marker() -> None:
    """Write a marker file indicating the last save completed successfully.
    Creates the log directory if it doesn't exist yet.
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(SAVE_MARKER_PATH, "w") as f:
            f.write(datetime.now().isoformat())
    except OSError as e:
        _logger.debug(f"Failed to write save marker (non-fatal): {e}")


def _is_valid_json(filepath: str) -> bool:
    """Return True if the file exists and contains valid JSON."""
    if not os.path.isfile(filepath):
        return False
    try:
        with open(filepath, encoding="utf-8") as f:
            json.load(f)
        return True
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        _logger.debug(f"Invalid JSON in {filepath}: {e}")
        return False


def check_last_save_integrity() -> dict:
    """Check whether the last save completed successfully.

    Returns a dict:
        {
            "ok": True/False,
            "settings_valid": True/False,
            "identity_valid": True/False,
            "marker_exists": True/False,
            "settings_path": str,
            "identity_path": str,
            "backups_available": True/False,
            "latest_backup_ts": str or None,
            "latest_backup_label": str or None,
        }

    "ok" is True only if both settings and identity are valid JSON.
    If files don't exist yet (first run), ok is True.
    """
    settings_exists = os.path.isfile(SETTINGS_PATH)
    identity_exists = os.path.isfile(IDENTITY_PATH)

    settings_valid = _is_valid_json(SETTINGS_PATH) if settings_exists else True
    identity_valid = _is_valid_json(IDENTITY_PATH) if identity_exists else True
    marker_exists = os.path.isfile(SAVE_MARKER_PATH)

    # If no files exist at all, it's a fresh install — nothing to validate
    if not settings_exists and not identity_exists:
        return {
            "ok": True,
            "settings_valid": True,
            "identity_valid": True,
            "marker_exists": False,
            "settings_path": SETTINGS_PATH,
            "identity_path": IDENTITY_PATH,
            "backups_available": False,
            "latest_backup_ts": None,
            "latest_backup_label": None,
        }

    # Look for the most recent backup
    backups = list_backups()
    latest_backup_ts = backups[0]["ts"] if backups else None
    latest_backup_label = backups[0]["label"] if backups else None

    ok = settings_valid and identity_valid

    return {
        "ok": ok,
        "settings_valid": settings_valid,
        "identity_valid": identity_valid,
        "marker_exists": marker_exists,
        "settings_path": SETTINGS_PATH,
        "identity_path": IDENTITY_PATH,
        "backups_available": len(backups) > 0,
        "latest_backup_ts": latest_backup_ts,
        "latest_backup_label": latest_backup_label,
    }


def save_settings() -> None:
    """Persist CONFIG to app_settings.json.
    Snapshots both settings and identity files before overwriting.
    """
    try:
        _snapshot_backups()

        # Remove marker while saving — gets re-written on success
        if os.path.isfile(SAVE_MARKER_PATH):
            try:
                os.remove(SAVE_MARKER_PATH)
            except OSError as e:
                _logger.debug(f"Could not remove save marker before save: {e}")

        # Atomic write: write to .tmp first, then rename to target.
        # This prevents truncated/corrupted files if the write is interrupted
        # (e.g. disk full, system crash, Windows file lock contention).
        tmp_path = SETTINGS_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=4)
        os.replace(tmp_path, SETTINGS_PATH)

        _write_save_marker()
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
        # Clean up temp file if something went wrong
        _logger.error(f"Failed to save settings to {SETTINGS_PATH}: {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError as e2:
            _logger.debug(f"Failed to clean up temp file {tmp_path}: {e2}")


def list_backups() -> list:
    """Return a list of backup entries grouped by timestamp, sorted newest-first.

    Each entry is a dict:
        {
            "ts": "20250101_120000",
            "display": "2025-01-01 12:00:00",
            "settings_path": str or None,
            "identity_path": str or None,
            "has_settings": bool,
            "has_identity": bool,
            "label": "2025-01-01 12:00:00 (settings + identity)",
        }

    Returns empty list if no backups exist.
    """
    if not os.path.isdir(SETTINGS_BACKUP_DIR):
        return []
    try:
        # Group files by timestamp
        groups = {}  # ts -> {settings_path, identity_path}
        for fname in os.listdir(SETTINGS_BACKUP_DIR):
            fpath = os.path.join(SETTINGS_BACKUP_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            # Parse: app_settings_20250101_120000.json or user_identity_20250101_120000.json
            if fname.startswith("app_settings_") and fname.endswith(".json"):
                ts_str = fname[len("app_settings_"):-len(".json")]
                groups.setdefault(ts_str, {"settings_path": None, "identity_path": None})["settings_path"] = fpath
            elif fname.startswith("user_identity_") and fname.endswith(".json"):
                ts_str = fname[len("user_identity_"):-len(".json")]
                groups.setdefault(ts_str, {"settings_path": None, "identity_path": None})["identity_path"] = fpath

        entries = []
        for ts_str in sorted(groups.keys(), reverse=True):
            g = groups[ts_str]
            try:
                parsed = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
                display = parsed.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                display = ts_str

            # Build a descriptive label
            parts = []
            if g["settings_path"]:
                parts.append("settings")
            if g["identity_path"]:
                parts.append("identity")
            type_str = " + ".join(parts) if parts else "unknown"
            label = f"{display} ({type_str})"

            entries.append({
                "ts": ts_str,
                "display": display,
                "settings_path": g["settings_path"],
                "identity_path": g["identity_path"],
                "has_settings": g["settings_path"] is not None,
                "has_identity": g["identity_path"] is not None,
                "label": label,
            })
        return entries
    except OSError as e:
        _logger.warning(f"Failed to list backups from {SETTINGS_BACKUP_DIR}: {e}")
        return []


def restore_backup(ts_str: str) -> bool:
    """Restore settings and/or identity from a backup timestamp.

    Args:
        ts_str: Timestamp string like "20250101_120000" to restore.

    If both settings and identity backups exist for this timestamp,
    both are restored. Returns True if at least one file was restored.
    """
    try:
        settings_backup = os.path.join(SETTINGS_BACKUP_DIR, f"app_settings_{ts_str}.json")
        identity_backup = os.path.join(SETTINGS_BACKUP_DIR, f"user_identity_{ts_str}.json")

        restored_any = False

        if os.path.isfile(settings_backup):
            shutil.copy2(settings_backup, SETTINGS_PATH)
            restored_any = True

        if os.path.isfile(identity_backup):
            shutil.copy2(identity_backup, IDENTITY_PATH)
            restored_any = True

        if restored_any:
            reload()

        return restored_any
    except OSError as e:
        _logger.error(f"Failed to restore backup {ts_str}: {e}")
        return False


def save_identity() -> None:
    """Persist IDENTITY_CONFIG to user_identity.json.
    Snapshots both identity and settings files before overwriting.
    """
    try:
        _snapshot_backups()

        # Remove marker while saving — gets re-written on success
        if os.path.isfile(SAVE_MARKER_PATH):
            try:
                os.remove(SAVE_MARKER_PATH)
            except OSError as e:
                _logger.debug(f"Could not remove save marker before identity save: {e}")

        # Atomic write: write to .tmp first, then rename to target.
        tmp_path = IDENTITY_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(IDENTITY_CONFIG, f, indent=4)
        os.replace(tmp_path, IDENTITY_PATH)
        for _k in ("name", "tone", "preferences", "background"):
            if _k in IDENTITY_CONFIG:
                CONFIG[_k] = IDENTITY_CONFIG[_k]

        _write_save_marker()
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
        # Clean up temp file if something went wrong
        _logger.error(f"Failed to save identity to {IDENTITY_PATH}: {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError as e2:
            _logger.debug(f"Failed to clean up temp file {tmp_path}: {e2}")


def export_backup(ts_str: str, export_path: str) -> bool:
    """Export a backup as a .zip file.

    Args:
        ts_str: Timestamp string like "20250101_120000".
        export_path: Full path to the .zip file to create.

    Returns True on success, False on failure.
    The zip contains app_settings_TIMESTAMP.json and/or
    user_identity_TIMESTAMP.json.
    """
    try:
        settings_src = os.path.join(SETTINGS_BACKUP_DIR, f"app_settings_{ts_str}.json")
        identity_src = os.path.join(SETTINGS_BACKUP_DIR, f"user_identity_{ts_str}.json")

        files_to_add = []
        if os.path.isfile(settings_src):
            files_to_add.append((settings_src, f"app_settings_{ts_str}.json"))
        if os.path.isfile(identity_src):
            files_to_add.append((identity_src, f"user_identity_{ts_str}.json"))

        if not files_to_add:
            return False

        with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for src_path, arc_name in files_to_add:
                zf.write(src_path, arc_name)

        return True
    except (OSError, zipfile.BadZipFile) as e:
        _logger.error(f"Failed to export backup {ts_str} to {export_path}: {e}")
        return False


def import_backup(import_path: str) -> str | None:
    """Import a .zip backup file into the backups directory with a fresh timestamp.

    Args:
        import_path: Full path to a .zip file created by export_backup().

    Returns the new timestamp string on success, or None on failure.
    The extracted files get a new timestamp so they appear as a new
    backup entry (rather than overwriting existing ones).
    """
    try:
        if not os.path.isfile(import_path):
            return None

        os.makedirs(SETTINGS_BACKUP_DIR, exist_ok=True)
        new_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        extracted_count = 0

        with zipfile.ZipFile(import_path, "r") as zf:
            for info in zf.infolist():
                fname = info.filename
                # Only extract known backup files
                if fname.startswith("app_settings_") and fname.endswith(".json"):
                    dest = os.path.join(SETTINGS_BACKUP_DIR, f"app_settings_{new_ts}.json")
                    with open(dest, "wb") as f:
                        f.write(zf.read(fname))
                    extracted_count += 1
                elif fname.startswith("user_identity_") and fname.endswith(".json"):
                    dest = os.path.join(SETTINGS_BACKUP_DIR, f"user_identity_{new_ts}.json")
                    with open(dest, "wb") as f:
                        f.write(zf.read(fname))
                    extracted_count += 1

        return new_ts if extracted_count > 0 else None
    except (OSError, zipfile.BadZipFile) as e:
        _logger.error(f"Failed to import backup from {import_path}: {e}")
        return None


def reload() -> None:
    _load_settings()
    _load_identity()
    _load_personas()

_load_dotenv()
_load_settings()
_load_identity()
_load_personas()
