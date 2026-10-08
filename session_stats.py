"""
session_stats.py — Session log aggregation and SVG badge generation.

Scans ``data/sessions/`` for session log files, parses them, aggregates
summary statistics, generates SVG badges, and exposes file-count helpers.

Used by ``tabs/session_log_tab.py`` (SessionLogTabMixin).
"""

import os
import re
import json
import glob
from datetime import datetime, timedelta
from typing import Any, Optional

from logging_config import get_logger

logger = get_logger(name="SessionStats")


# ── Path resolution ────────────────────────────────────────────────────

def _resolve_root() -> str:
    """Return the project root directory (directory containing this file)."""
    return os.path.dirname(os.path.abspath(__file__))


def _default_sessions_dir() -> str:
    """Return the default sessions directory path."""
    return os.path.join(_resolve_root(), "data", "sessions")


# ── File discovery ─────────────────────────────────────────────────────

def find_files(
    glob_pattern: str = "*.yaml",
    sessions_dir: Optional[str] = None,
) -> list[str]:
    """Find all session files matching *glob_pattern* in *sessions_dir*.

    Args:
        glob_pattern: Glob pattern to match (default ``*.yaml``).
        sessions_dir: Directory to scan.  Falls back to ``_default_sessions_dir()``
            when ``None``.

    Returns:
        Sorted list of absolute file paths.  Empty list when the directory
        does not exist or contains no matching files.
    """
    if sessions_dir is None:
        sessions_dir = _default_sessions_dir()
    if not os.path.isdir(sessions_dir):
        return []
    pattern = os.path.join(sessions_dir, glob_pattern)
    files = glob.glob(pattern)
    return sorted(files)


# ── Parsing ────────────────────────────────────────────────────────────

def parse_yaml(filepath: str) -> Optional[dict[str, Any]]:
    """Parse a session file (JSON or YAML) into a dict.

    Tries JSON first (the canonical format for session files), falling
    back to YAML when ``PyYAML`` is importable.

    Returns:
        Parsed dict, or ``None`` on any failure (missing file, parse error,
        non-dict root).
    """
    if not os.path.isfile(filepath):
        logger.debug(f"parse_yaml: file not found — {filepath}")
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        logger.debug(f"parse_yaml: read error — {e}")
        return None
    # Try JSON first
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Fall back to YAML
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(raw)
        except (ImportError, Exception) as e:
            logger.debug(f"parse_yaml: parse error — {e}")
            return None
    if not isinstance(data, dict):
        return None
    return data


# ── Duration parsing ───────────────────────────────────────────────────

def _parse_duration_hours(raw: Any) -> float:
    """Parse a duration value into float hours.

    Handles:
      - ``None``               → 0.0
      - ``int`` / ``float``    → cast to float
      - ``"2h"``               → 2.0
      - ``"90m"``              → 1.5
      - ``"1h30m"``            → 1.5
      - invalid string         → 0.0
    """
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return 0.0
    total = 0.0
    # Match patterns like "2h", "90m", "1h30m", "2.5h"
    hours_match = re.search(r"(\d+\.?\d*)\s*h", raw)
    mins_match = re.search(r"(\d+\.?\d*)\s*m", raw)
    if hours_match:
        total += float(hours_match.group(1))
    if mins_match:
        total += float(mins_match.group(1)) / 60.0
    if not hours_match and not mins_match:
        # Try parsing as plain number
        try:
            total = float(raw)
        except (ValueError, TypeError):
            return 0.0
    return total


# ── File counting ──────────────────────────────────────────────────────

def count_files(session: dict[str, Any]) -> tuple[int, int, list[str], list[str]]:
    """Count created and modified files in a session dict.

    Looks for keys ``files_created``, ``files_modified``, ``created_files``,
    ``modified_files``, and ``files`` (with ``+`` / ``~`` prefixes for
    created / modified respectively).

    Returns:
        ``(created_count, modified_count, created_list, modified_list)``.
    """
    created: list[str] = []
    modified: list[str] = []

    # Direct list keys
    for key in ("files_created", "created_files"):
        val = session.get(key)
        if isinstance(val, list):
            created.extend(str(f) for f in val)

    for key in ("files_modified", "modified_files"):
        val = session.get(key)
        if isinstance(val, list):
            modified.extend(str(f) for f in val)

    # Prefixed 'files' list: "+" = created, "~" = modified
    files_list = session.get("files")
    if isinstance(files_list, list):
        for item in files_list:
            item_str = str(item).strip()
            if item_str.startswith("+"):
                created.append(item_str[1:].strip())
            elif item_str.startswith("~"):
                modified.append(item_str[1:].strip())

    return (len(created), len(modified), created, modified)


# ── Aggregation ────────────────────────────────────────────────────────

def aggregate(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a list of session dicts into summary statistics.

    Returns a dict with keys:
        sessions, hours, created, modified, changed,
        date_range, session_list, sprint_hours, tag_frequency,
        created_files, modified_files.
    """
    total_sessions = 0
    total_hours = 0.0
    total_created = 0
    total_modified = 0
    all_created: list[str] = []
    all_modified: list[str] = []
    dates: list[str] = []
    sprint_hours: dict[int, float] = {}
    tag_freq: dict[str, int] = {}
    session_list: list[str] = []

    for s in sessions:
        if not isinstance(s, dict):
            continue
        total_sessions += 1
        h = _parse_duration_hours(s.get("duration"))
        total_hours += h

        created_count, modified_count, created_list, modified_list = count_files(s)
        total_created += created_count
        total_modified += modified_count
        all_created.extend(created_list)
        all_modified.extend(modified_list)

        date = s.get("date", "")
        if date:
            dates.append(str(date))

        sprint = s.get("sprint")
        if sprint is not None:
            try:
                sprint_int = int(sprint)
            except (ValueError, TypeError):
                sprint_int = 0
            sprint_hours[sprint_int] = sprint_hours.get(sprint_int, 0.0) + h

        tags = s.get("tags")
        if isinstance(tags, list):
            for t in tags:
                tag_str = str(t).lower().strip()
                if tag_str:
                    tag_freq[tag_str] = tag_freq.get(tag_str, 0) + 1

        session_name = s.get("session", "")
        if session_name:
            session_list.append(str(session_name))

    # Deduplicate file lists
    all_created_dedup = sorted(set(all_created))
    all_modified_dedup = sorted(set(all_modified))

    # Date range
    if dates:
        dates_sorted = sorted(dates)
        date_range = (dates_sorted[0], dates_sorted[-1])
    else:
        date_range = ("N/A", "N/A")

    return {
        "sessions": total_sessions,
        "hours": round(total_hours, 1),
        "created": total_created,
        "modified": total_modified,
        "changed": total_created + total_modified,
        "date_range": date_range,
        "session_list": session_list,
        "sprint_hours": sprint_hours,
        "tag_frequency": tag_freq,
        "created_files": all_created_dedup,
        "modified_files": all_modified_dedup,
    }


# ── Badge generation ───────────────────────────────────────────────────

def _badge_svg(
    label: str,
    value: str,
    color_left: str = "#555",
    color_right: str = "#4c1",
) -> str:
    """Generate a shields.io-style SVG badge string.

    Args:
        label: Left-side label text.
        value: Right-side value text.
        color_left:  Left background colour (default ``#555``).
        color_right: Right background colour (default ``#4c1`` / green).

    Returns:
        Complete SVG string suitable for writing to a ``.svg`` file.
    """
    # Estimate text widths (approximate: ~7px per char)
    label_width = len(label) * 7 + 10
    value_width = len(value) * 7 + 10
    total_width = label_width + value_width
    label_center_x = label_width / 2
    value_center_x = label_width + value_width / 2

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20">'
        f'<rect x="0" y="0" width="{label_width}" height="20" fill="{color_left}"/>'
        f'<rect x="{label_width}" y="0" width="{value_width}" height="20" fill="{color_right}"/>'
        f'<text x="{label_center_x}" y="14" fill="#fff" font-family="sans-serif" font-size="11" text-anchor="middle">{label}</text>'
        f'<text x="{value_center_x}" y="14" fill="#fff" font-family="sans-serif" font-size="11" text-anchor="middle">{value}</text>'
        f"</svg>"
    )


def _generate_badges(stats: dict[str, Any], out_dir: str = "badges") -> list[tuple[str, str]]:
    """Generate SVG badge files from aggregated stats.

    Creates one badge per metric (sessions, hours, created, modified, changed,
    sprints, and an optional ``badge_top_tag.svg`` when tag data is present).

    Args:
        stats: Aggregated stats dict from ``aggregate()``.
        out_dir: Output directory for badge files (default ``badges/``).

    Returns:
        List of ``(filename, filepath)`` tuples.
    """
    os.makedirs(out_dir, exist_ok=True)
    results: list[tuple[str, str]] = []

    def _write(name: str, label: str, value: str) -> None:
        svg = _badge_svg(label, value)
        fpath = os.path.join(out_dir, name)
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write(svg)
        results.append((name, fpath))

    _write("badge_sessions.svg", "Sessions", str(stats.get("sessions", 0)))
    _write("badge_hours.svg", "Hours", str(stats.get("hours", 0)))
    _write("badge_created.svg", "Created", str(stats.get("created", 0)))
    _write("badge_modified.svg", "Modified", str(stats.get("modified", 0)))
    _write("badge_changed.svg", "Changed", str(stats.get("changed", 0)))

    # Sprint summary badge
    sprints = stats.get("sprint_hours", {})
    sprint_summary = ",".join(f"S{int(k)}:{v:.0f}h" for k, v in sorted(sprints.items())) or "0"
    _write("badge_sprints.svg", "Sprints", sprint_summary[:80])

    # Top tag badge (if any tags present)
    tags = stats.get("tag_frequency", {})
    if tags:
        top = max(tags, key=tags.get)
        _write("badge_top_tag.svg", "Top Tag", f"{top} ({tags[top]})")

    return results
