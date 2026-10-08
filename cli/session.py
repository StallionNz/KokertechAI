"""cli.session — Session save/load/archive/template/backup for KokertechAI CLI."""

import gzip
import json
import os
import shutil
import zipfile
from datetime import datetime
from typing import Optional

SESSIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".sessions")
ARCHIVE_DIR = os.path.join(SESSIONS_DIR, "archive")
BACKUP_DIR = os.path.join(SESSIONS_DIR, "backups")
TEMPLATES_DIR = os.path.join(SESSIONS_DIR, "templates")


def _ensure_dirs() -> None:
    for d in (SESSIONS_DIR, ARCHIVE_DIR, BACKUP_DIR, TEMPLATES_DIR):
        os.makedirs(d, exist_ok=True)


def _safe_name(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in "_- ").strip()
    return safe or "session"


def _path_for(name: str, subdir: str = "") -> str:
    _ensure_dirs()
    safe = _safe_name(name)
    base = os.path.join(SESSIONS_DIR, subdir) if subdir else SESSIONS_DIR
    return os.path.join(base, f"{safe}.json")


def _read_session_file(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        # Try .json.gz
        gz_path = path + ".gz"
        if os.path.isfile(gz_path):
            try:
                with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError, gzip.BadGzipFile):
                return None
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _session_summary(data: dict, path: str) -> dict:
    return {
        "name": data.get("name", os.path.basename(path).removesuffix(".json").removesuffix(".gz")),
        "message_count": data.get("message_count", 0),
        "tags": data.get("tags", []),
        "favorite": data.get("favorite", False),
        "duration_minutes": data.get("duration_minutes", 0),
        "word_count": data.get("word_count", 0),
        "parent_session": data.get("parent_session", ""),
        "created": data.get("created", "?"),
        "updated": data.get("updated", "?"),
        "path": path,
    }


def _get_mtime_or_now(path: str) -> str:
    if os.path.isfile(path):
        try:
            return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Core CRUD ──

def save_session(name: str, chat_history: list, context_summary: str = "",
                 tags: Optional[list] = None, notes: str = "",
                 parent_session: str = "", duration_minutes: float = 0,
                 config_overrides: Optional[dict] = None) -> str:
    _ensure_dirs()
    safe = _safe_name(name)
    path = os.path.join(SESSIONS_DIR, f"{safe}.json")

    existing_tags = []
    existing_notes = ""
    if os.path.isfile(path):
        existing = _read_session_file(path)
        if existing:
            existing_tags = existing.get("tags", [])
            existing_notes = existing.get("notes", "")

    merged_tags = list(set((tags or []) + existing_tags))
    merged_notes = f"{existing_notes}\n{notes}".strip() if notes else existing_notes

    data = {
        "name": safe, "chat_history": chat_history,
        "context_summary": context_summary,
        "message_count": len(chat_history),
        "tags": merged_tags, "notes": merged_notes,
        "favorite": (existing or {}).get("favorite", False) if os.path.isfile(path) else False,
        "parent_session": parent_session,
        "duration_minutes": round(duration_minutes, 1),
        "word_count": sum(len(m.get("content", "").split()) for m in chat_history),
        "config_overrides": config_overrides or {},
        "created": _get_mtime_or_now(path),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Backup before save
    if os.path.isfile(path):
        _backup_file(path)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def load_session(name: str) -> Optional[dict]:
    _ensure_dirs()
    path = _path_for(name)
    data = _read_session_file(path)
    if data is None:
        path = _path_for(name, "archive")
        data = _read_session_file(path)
    return data


def merge_session(base_name: str, merge_name: str) -> Optional[dict]:
    base = load_session(base_name)
    merge = load_session(merge_name)
    if base is None or merge is None:
        return None
    # Merge with dedup: skip messages from merge that already appear in base
    base_content = {m.get("content", "") for m in base.get("chat_history", [])}
    for m in merge.get("chat_history", []):
        if m.get("content", "") not in base_content:
            base["chat_history"].append(m)
            base_content.add(m.get("content", ""))
    base["message_count"] = len(base["chat_history"])
    base["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    base["tags"] = list(set(base.get("tags", []) + merge.get("tags", [])))
    return base


def delete_session(name: str) -> bool:
    path = _path_for(name)
    if not os.path.isfile(path):
        path = _path_for(name, "archive")
        if not os.path.isfile(path):
            return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def rename_session(old: str, new: str) -> bool:
    old_path = _path_for(old)
    if not os.path.isfile(old_path):
        return False
    new_path = _path_for(new)
    if os.path.isfile(new_path):
        return False
    try:
        os.rename(old_path, new_path)
        # Update name inside file
        data = _read_session_file(new_path)
        if data:
            data["name"] = _safe_name(new)
            data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(new_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except OSError:
        return False


def copy_session(source: str, dest: str) -> bool:
    src = load_session(source)
    if src is None:
        return False
    save_session(
        name=dest, chat_history=src.get("chat_history", []),
        context_summary=src.get("context_summary", ""),
        tags=src.get("tags"), notes=src.get("notes", ""),
        parent_session=src.get("parent_session", ""),
        duration_minutes=src.get("duration_minutes", 0),
    )
    return True


# ── Listing & search ──

def list_sessions(tag_filter: str = "", favorites_only: bool = False) -> list:
    _ensure_dirs()
    sessions = []
    try:
        for fname in os.listdir(SESSIONS_DIR):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(SESSIONS_DIR, fname)
            data = _read_session_file(path)
            if data is None:
                continue
            if tag_filter and tag_filter not in data.get("tags", []):
                continue
            if favorites_only and not data.get("favorite", False):
                continue
            sessions.append(_session_summary(data, path))
    except OSError:
        pass
    # Sort: favorites first, then by updated
    sessions.sort(key=lambda s: (not s.get("favorite", False), s.get("updated", "")),
                  reverse=False)
    return sessions


def search_sessions(term: str) -> list:
    _ensure_dirs()
    results = []
    term_lower = term.lower()
    for fname in os.listdir(SESSIONS_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(SESSIONS_DIR, fname)
        data = _read_session_file(path)
        if data is None:
            continue
        if term_lower in data.get("name", "").lower():
            results.append(_session_summary(data, path))
            continue
        # Full-text search all message content
        for msg in data.get("chat_history", []):
            if term_lower in msg.get("content", "").lower():
                results.append(_session_summary(data, path))
                break
    results.sort(key=lambda s: s.get("updated", ""), reverse=True)
    return results


def get_last_session() -> Optional[dict]:
    sessions = list_sessions()
    if not sessions:
        return None
    return load_session(sessions[0]["name"])


def get_last_n_sessions(n: int = 3) -> list:
    sessions = list_sessions()[:n]
    return sessions


# ── Favorites ──

def toggle_favorite(name: str) -> bool:
    path = _path_for(name)
    data = _read_session_file(path)
    if data is None:
        return False
    data["favorite"] = not data.get("favorite", False)
    data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return True


# ── Archive ──

def archive_session(name: str) -> bool:
    src = _path_for(name)
    if not os.path.isfile(src):
        return False
    dest = os.path.join(ARCHIVE_DIR, os.path.basename(src))
    try:
        shutil.move(src, dest)
        return True
    except OSError:
        return False


def unarchive_session(name: str) -> bool:
    src = os.path.join(ARCHIVE_DIR, f"{_safe_name(name)}.json")
    if not os.path.isfile(src):
        return False
    dest = _path_for(name)
    try:
        shutil.move(src, dest)
        return True
    except OSError:
        return False


def list_archived() -> list:
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    try:
        return [f.removesuffix(".json") for f in os.listdir(ARCHIVE_DIR)
                if f.endswith(".json")]
    except OSError:
        return []


# ── Backup ──

def _backup_file(path: str) -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = os.path.basename(path)
        backup_path = os.path.join(BACKUP_DIR, f"{ts}_{name}")
        shutil.copy2(path, backup_path)
        # Keep only last 5 backups per file
        all_backups = sorted(
            [f for f in os.listdir(BACKUP_DIR) if f.endswith(f"_{name}")],
            reverse=True)
        for old in all_backups[5:]:
            try:
                os.remove(os.path.join(BACKUP_DIR, old))
            except OSError:
                pass
    except OSError:
        pass


# ── Integrity check ──

def integrity_check() -> dict:
    _ensure_dirs()
    result = {"total": 0, "ok": 0, "corrupt": [], "empty": []}
    try:
        for fname in os.listdir(SESSIONS_DIR):
            if not fname.endswith(".json"):
                continue
            result["total"] += 1
            path = os.path.join(SESSIONS_DIR, fname)
            data = _read_session_file(path)
            if data is None:
                result["corrupt"].append(fname)
            elif not data.get("chat_history"):
                result["empty"].append(fname)
            else:
                result["ok"] += 1
    except OSError:
        pass
    return result


# ── Templates ──

def session_to_template(name: str, template_name: str) -> bool:
    data = load_session(name)
    if data is None:
        return False
    template = {
        "name": template_name,
        "system_prompt": "",
        "first_message": "",
        "description": data.get("notes", ""),
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    history = data.get("chat_history", [])
    for m in history:
        if m.get("role") == "system" and not template["system_prompt"]:
            template["system_prompt"] = m.get("content", "")
        if m.get("role") == "user" and not template["first_message"]:
            template["first_message"] = m.get("content", "")
        if template["system_prompt"] and template["first_message"]:
            break
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    path = os.path.join(TEMPLATES_DIR, f"{_safe_name(template_name)}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2, ensure_ascii=False)
    return True


def session_from_template(template_name: str, session_name: str = "") -> Optional[dict]:
    path = os.path.join(TEMPLATES_DIR, f"{_safe_name(template_name)}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            template = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    history = []
    if template.get("system_prompt"):
        history.append({"role": "system", "content": template["system_prompt"]})
    if template.get("first_message"):
        history.append({"role": "user", "content": template["first_message"]})
    return {
        "name": session_name or template_name,
        "chat_history": history,
        "context_summary": "",
        "message_count": len(history),
        "tags": ["template"],
        "template_name": template_name,
    }


def list_templates() -> list:
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    try:
        return [f.removesuffix(".json") for f in os.listdir(TEMPLATES_DIR)
                if f.endswith(".json")]
    except OSError:
        return []


# ── Bulk export ──

def export_sessions_bulk(tag: Optional[str] = None) -> Optional[str]:
    sessions = list_sessions(tag_filter=tag or "")
    if not sessions:
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"sessions_export_{ts}.zip"
    zip_path = os.path.join(SESSIONS_DIR, zip_name)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for s in sessions:
                data = load_session(s["name"])
                if data:
                    zf.writestr(f"{s['name']}.json",
                                json.dumps(data, indent=2, ensure_ascii=False))
        return zip_path
    except OSError:
        return None


def import_session(filepath: str, as_name: str = "") -> Optional[str]:
    if not os.path.isfile(filepath):
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    name = as_name or data.get("name", os.path.basename(filepath).removesuffix(".json"))
    return save_session(
        name=name, chat_history=data.get("chat_history", []),
        context_summary=data.get("context_summary", ""),
        tags=data.get("tags"), notes=data.get("notes", ""),
        parent_session=data.get("parent_session", ""),
    )


# ── Stats ──

def get_session_duration(name: str) -> float:
    data = load_session(name)
    if data is None:
        return 0
    return data.get("duration_minutes", 0)


def get_session_word_count(name: str) -> int:
    data = load_session(name)
    if data is None:
        return 0
    return sum(len(m.get("content", "").split()) for m in data.get("chat_history", []))


# ── Session notes ──

def set_session_notes(name: str, notes: str) -> bool:
    path = _path_for(name)
    data = _read_session_file(path)
    if data is None:
        return False
    data["notes"] = notes
    data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return True


def get_session_notes(name: str) -> str:
    data = load_session(name)
    if data is None:
        return ""
    return data.get("notes", "")
