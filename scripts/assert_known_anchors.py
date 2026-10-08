# assert_known_anchors.py -- CI-style drift-detector + wide-cell gate for KNOWLEDGE.md,
# PLUS a structural-integrity gate for `tabs/settings_tab.py`.
# (1) Detects stale ``(line N)`` / ``(lines N-M)`` / ``<module>.py:N`` prose cites
#     so they can be refactored to drift-immune ``<module>.<class>.<method>`` anchors.
# (2) Detects markdown table rows where any cell exceeds ``WIDE_CELL_THRESHOLD``
#     chars (currently 240 = ~3 display lines @ 80-col wrap) -- encouraging
#     compaction via row-pointer + sub-section pattern (see ``§2 L70 ai_base.py``
#     and ``§13 issues`` rows for the canonical refactors).
# (3) Detects deletion of canonical ``_create_*_group(self)`` methods from
#     ``tabs/settings_tab.py`` -- which would otherwise crash the dashboard
#     with ``AttributeError`` on launch. Catches regex-walker / per-method
#     boundary-walker cascades at PR-review time, before they reach users.
# All gates fire on exit 1 so future regressions are caught at PR review
# (pre-flight gate in ``run_tests.bat``) rather than after merge.
# Usage:
#   python assert_known_anchors.py           # CLI: exit 0 = clean, 1 = offenders
#   python assert_known_anchors.py --json    # JSON machine-readable report
#   from assert_known_anchors import (assert_no_stale_anchors,
#                                     assert_no_wide_table_cells,
#                                     assert_settings_tab_intact)  # pytest entry
import json, re, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_MD = REPO_ROOT / "KNOWLEDGE.md"
CONTINUITY_PATH = REPO_ROOT / "docs" / "sessions" / "continuace_freebuff.md"

# Import continuity file consistency check.
# Use importlib to avoid namespace-package resolution issues when this script is
# run as `python scripts/assert_known_anchors.py` (the `scripts.` prefix can fail
# because __main__ is not inside the `scripts` namespace package).
import importlib.util
_CONTINUITY_CHECK_MODULE = None
_CONTINUITY_CHECK_ERROR = None

# NOTE: REPO_ROOT/KNOWLEDGE_MD are Path constructions only -- they do not
# perform any filesystem I/O at module-import time.  Unit tests of the pure
# regex finders below call them with an explicit ``text=`` argument so they
# do not require KNOWLEDGE.md to exist on disk.  CLI callers (main()) and
# ``_summary()`` continue to read the file lazily inside the functions that
# need it; this preserves the FileNotFoundError behavior on the CLI.

# Stale cite patterns, applied to PROSE ONLY (after stripping code blocks
# and inline backtick spans). Order: longer/more specific patterns first.
STALE_PATTERNS = [
    (re.compile(r"\(\s*line\s+\d+(?:\s*-\s*\d+)*\s*\)"),
     "parenthesized (line N) citation"),
    (re.compile(r"\(\s*lines\s+\d+\s*-\s*\d+\s*\)"),
     "parenthesized (lines N-M) range citation"),
    (re.compile(r"`[a-zA-Z_][a-zA-Z0-9_]*\.py:\d+(?:-[,\s\d]+)?`"),
     "backticked <module>.py:N cite"),
    (re.compile(r"(?<![`A-Za-z_.\-])[a-z][a-zA-Z0-9_]*\.py:\d+(?:-[,\s\d]+)?"),
     "bare <module>.py:N cite"),
]

# Drift-immune anchor-style: backticked module.class.method.
ALLOWED_ANCHOR = re.compile(
    r"`[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*){1,3}`"
)


def _load_prose_lines(text=None):
    if text is None:
        if not KNOWLEDGE_MD.exists():
            raise FileNotFoundError(
                "KNOWLEDGE.md not found at " + str(KNOWLEDGE_MD))
        text = KNOWLEDGE_MD.read_text(encoding="utf-8")
    out = []
    in_block = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_block = not in_block
            out.append("")
            continue
        if in_block:
            out.append("")
            continue
        out.append(re.sub(r"`[^`]+`", "", line))
    return out


def find_drift_anchors(text=None):
    prose = _load_prose_lines(text=text)
    out = []
    for ln, line in enumerate(prose, 1):
        if not line.strip():
            continue
        for pat, desc in STALE_PATTERNS:
            for m in pat.finditer(line):
                out.append((ln, desc, m.group(0)))
    return out


def find_anchor_references(text=None):
    # NOTE: cannot delegate to ``_load_prose_lines()`` because that helper
    # strips inline backticks via ``re.sub(r"`[^`]+`", "", line)``.  ALLOWED_ANCHOR
    # matches backticked module.class.method spans specifically, so stripping
    # the backticks before regex-findall would destroy every match.  Keep the
    # bare read (with the same text=None opt-out for unit tests).
    if text is None:
        text = KNOWLEDGE_MD.read_text(encoding="utf-8")
    return sorted(set(ALLOWED_ANCHOR.findall(text)))


# Wide-cell threshold (chars per cell; ~3 display lines @ 80-col wrap).
WIDE_CELL_THRESHOLD = 240


# === Settings-tab structural-integrity gate ===
# Catches automated-rewrite cascades that DELETE `_create_*_group` method
# definitions (e.g., the per-method boundary walker that lost
# `_create_sys_config_group` and then ALL 15 `_create_*_group` methods in
# June 2026). Required: every canonical method below must still be present
# in tabs/settings_tab.py at column-4 indent with the exact name `_create_*_group(self)`.
SETTINGS_TAB_PY = REPO_ROOT / "tabs" / "settings_tab.py"
CANONICAL_CREATE_GROUP_METHODS = (
    "_create_sys_config_group",
    "_create_tts_settings_group",
    "_create_sandbox_group",
    "_create_web_settings_group",
    "_create_persona_group",
    "_create_protocol_group",
    "_create_mode_group",
    "_create_hotkey_group",
    "_create_registry_group",
    "_create_local_llm_advanced_group",
    "_create_provider_control_group",
    "_create_prefs_group",
    "_create_identity_group",
    "_create_backup_group",
)
# Lower bound allows for renaming; counts strictly BELOW this trigger the gate.
EXPECTED_CREATE_GROUP_MIN = 14


def _list_create_group_methods(file_path=SETTINGS_TAB_PY):
    """Return sorted list of `_create_*_group(self)` method names declared
    at column-4 indent in the file. Empty list if the file is missing."""
    if not file_path.exists():
        return []
    text = file_path.read_text(encoding="utf-8")
    return sorted(set(re.findall(
        r"^    def (_create_\w+_group)\(self", text, re.MULTILINE,
    )))


def find_missing_create_group_methods(file_path=SETTINGS_TAB_PY):
    """Return list of `_create_*_group` method names that should exist but
    are missing from the file. Catches automated-fix cascades that silently
    delete method definitions (the pre-method-boundary-walker regression).
    """
    if not file_path.exists():
        return ["_FILE_NOT_FOUND: " + str(file_path)]
    present = set(_list_create_group_methods(file_path))
    missing = [c for c in CANONICAL_CREATE_GROUP_METHODS if c not in present]
    if len(present) < EXPECTED_CREATE_GROUP_MIN:
        missing.append(
            "_COUNT_BELOW_MIN: only " + str(len(present))
            + " `_create_*_group` methods present; expected >= "
            + str(EXPECTED_CREATE_GROUP_MIN)
        )
    return missing


def assert_settings_tab_intact(file_path=SETTINGS_TAB_PY):
    """Pre-flight gate: blocks any automated `_create_*_group` mutation
    that would have removed a method definition. Companion pattern: pair
    with ``python -m py_compile tabs/settings_tab.py`` + offscreen
    ``KokertechDashboard()`` smoke test AFTER any str.replace on the file.
    """
    missing = find_missing_create_group_methods(file_path)
    if not missing:
        return
    head = chr(10).join("  - " + m for m in missing[:25])
    raise AssertionError(
        "Settings-tab structural-integrity gate: "
        + str(len(missing))
        + " canonical `_create_*_group` method(s) missing from "
        + str(file_path) + ". An automated fix (regex walker, per-method "
        + "boundary walker, str.replace cascade) deleted method "
        + "definitions. Restore the file from a known-good source, then "
        + "re-apply ONLY surgical edits validated by ``python -m py_compile "
        "tabs/settings_tab.py`` + offscreen ``KokertechDashboard()`` smoke "
        + "between each edit." + chr(10) + chr(10)
        + "Missing methods:" + chr(10) + head
    )


def find_wide_table_cells(threshold=WIDE_CELL_THRESHOLD, text=None):
    # Scan markdown tables for inline-cell content exceeding ``threshold`` chars.
    # Flags data rows in tables like ``§2 Key Module Sizes`` / ``§12 Testing``
    # / ``§13 Common Pitfalls`` where any cell is too long, encouraging
    # compaction via row-pointer + sub-section pattern.
    # Skips table header rows (all cells short) and separator rows.
    # If ``text`` is None, lazily reads KNOWLEDGE_MD; tests pass ``text=``
    # directly to exercise the regex logic without filesystem I/O.
    if text is None:
        if not KNOWLEDGE_MD.exists():
            return []
        text = KNOWLEDGE_MD.read_text(encoding="utf-8")
    out = []
    in_block = False
    for ln, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            in_block = not in_block
            continue
        if in_block:
            continue
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")]
        cells = parts[1:-1]  # drop empty leading/trailing separators
        if not cells:
            continue
        # Skip separator rows (cells are pure dashes/colons for alignment).
        if all(set(c) <= {"-", ":"} for c in cells if c):
            continue
        # Skip header rows: any row where ALL cells are <=80 chars.
        if all(len(c) <= 80 for c in cells):
            continue
        # Data row: at least one cell > 80 chars; flag any cell > threshold.
        for cid, cell in enumerate(cells, start=1):
            if len(cell) > threshold:
                out.append((ln, cid, len(cell), cell[:120]))
    return out


def assert_no_wide_table_cells(threshold=WIDE_CELL_THRESHOLD):
    offs = find_wide_table_cells(threshold=threshold)
    if not offs:
        return
    n = len(offs)
    head = chr(10).join(
        "  line " + str(p[0]).rjust(4)
        + " cell-" + str(p[1])
        + " [" + str(p[2]) + " chars > " + str(threshold) + "]: "
        + p[3]
        for p in offs[:50]
    )
    tail = (chr(10) + "  ... and " + str(n - 50) + " more (truncated)"
            if n > 50 else "")
    raise AssertionError(
        "Wide-cell detector: " + str(n)
        + " table cell(s) in KNOWLEDGE.md exceed " + str(threshold)
        + " chars." + chr(10)
        + "Compact via row-pointer + sub-section pattern"
        + " (see \u00a72 L70 ai_base.py row refactor for the canonical example)." + chr(10)
        + chr(10) + "Offenders:" + chr(10) + head + tail
    )


def assert_no_stale_anchors():
    offs = find_drift_anchors()
    if not offs:
        return
    n = len(offs)
    head = chr(10).join(
        "  line " + str(p[0]).rjust(4) + ": [" + p[1] + "] -> " + repr(p[2])
        for p in offs[:200]
    )
    tail = (chr(10) + "  ... and " + str(n - 200) + " more (truncated)"
            if n > 200 else "")
    raise AssertionError(
        "Drift-detector: " + str(n) + " stale cite(s) in KNOWLEDGE.md prose." + chr(10)
        + "Replace with drift-immune <module>.<class>.<method> anchors." + chr(10)
        + chr(10) + "Offenders:" + chr(10) + head + tail
    )


def _load_continuity_check():
    """Lazy-load the continuity consistency module via importlib.

    Uses importlib.util.spec_from_file_location to load the module by absolute
    path, avoiding namespace-package resolution issues that occur when
    `from scripts.assert_continuity_file_consistency import ...` is called
    from inside a script running as __main__ within the same directory.

    Returns None if the continuity file does not exist or the module cannot be
    loaded (graceful degradation -- the check is silently skipped).
    """
    global _CONTINUITY_CHECK_MODULE, _CONTINUITY_CHECK_ERROR
    if _CONTINUITY_CHECK_MODULE is not None:
        return (
            _CONTINUITY_CHECK_MODULE._summary,
            _CONTINUITY_CHECK_MODULE.assert_file_consistency,
            _CONTINUITY_CHECK_MODULE.assert_appendix_b_consistency,
            _CONTINUITY_CHECK_MODULE.find_appendix_b_violations,
        )
    if _CONTINUITY_CHECK_ERROR is not None:
        return False
    if not CONTINUITY_PATH.exists():
        _CONTINUITY_CHECK_ERROR = "file not found: " + str(CONTINUITY_PATH)
        return False
    try:
        mod_path = REPO_ROOT / "scripts" / "assert_continuity_file_consistency.py"
        if not mod_path.exists():
            _CONTINUITY_CHECK_ERROR = "module not found: " + str(mod_path)
            return False
        spec = importlib.util.spec_from_file_location(
            "assert_continuity_file_consistency", str(mod_path)
        )
        if spec is None:
            _CONTINUITY_CHECK_ERROR = "spec_from_file_location returned None"
            return False
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CONTINUITY_CHECK_MODULE = mod
        return (
            mod._summary,
            mod.assert_file_consistency,
            mod.assert_appendix_b_consistency,
            mod.find_appendix_b_violations,
        )
    except Exception as _exc:
        _CONTINUITY_CHECK_ERROR = str(_exc)
        return False


def find_continuity_violations():
    """Return (file_violations, appendix_b_hard_violations, total_sessions) for the continuity doc.

    Returns (0, 0, 0) if continuity doc is missing or cannot be imported.
    """
    chk = _load_continuity_check()
    if not chk:
        return 0, 0, 0
    summary_fn, _, _, find_ab_fn = chk
    ec, payload = summary_fn()
    file_violations = payload.get("total_violations", 0)
    ab_hard = payload.get("appendix_b_hard_violations", 0)
    total_sessions = payload.get("total_sessions", 0)
    return file_violations, ab_hard, total_sessions


def assert_continuity_consistency():
    """Pre-flight gate: blocks on continuity file consistency violations.

    Silently passes if continuity doc is missing or module cannot be imported.
    """
    chk = _load_continuity_check()
    if not chk:
        return
    _, assert_fn, assert_ab_fn, _ = chk
    assert_fn()
    assert_ab_fn()


def _summary():
    drift_offs = find_drift_anchors()
    wide_offs = find_wide_table_cells()
    settings_missing = find_missing_create_group_methods()
    continuity_violations, continuity_ab_hard, continuity_sessions = find_continuity_violations()
    drift_count = len(drift_offs)
    wide_count = len(wide_offs)
    settings_count = len(settings_missing)
    any_failure = (drift_count > 0 or wide_count > 0 or settings_count > 0 or continuity_violations > 0 or continuity_ab_hard > 0)
    return (
        1 if any_failure else 0,
        {
            "drift_count": drift_count,
            "offenders": [
                {"line": l, "desc": d, "snippet": s}
                for l, d, s in drift_offs
            ],
            "wide_cell_count": wide_count,
            "wide_cells": [
                {"line": l, "cell": c, "chars": n, "snippet": s}
                for l, c, n, s in wide_offs
            ],
            "anchors_in_use": find_anchor_references(),
            "knowledge_md_path": str(KNOWLEDGE_MD),
            "settings_tab_intact": settings_count == 0,
            "settings_tab_missing_count": settings_count,
            "settings_tab_missing": settings_missing,
            "expected_create_group_min": EXPECTED_CREATE_GROUP_MIN,
            "canonical_create_group_methods": list(CANONICAL_CREATE_GROUP_METHODS),
            "continuity_path": str(CONTINUITY_PATH),
            "continuity_sessions": continuity_sessions,
            "continuity_violations": continuity_violations,
            "continuity_appendix_b_violations": continuity_ab_hard,
        },
    )


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    # Windows console defaults to cp1252; ensure stdout can render ``→`` arrows
    # and other Unicode chars that appear in wide-cell snippets.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if "--json" in argv:
        ec, payload = _summary()
        print(json.dumps(payload, indent=2))
        return ec
    ec, payload = _summary()
    drift_count = payload["drift_count"]
    wide_count = payload["wide_cell_count"]
    settings_count = payload["settings_tab_missing_count"]
    anchors = payload["anchors_in_use"]
    continuity_violations = payload.get("continuity_violations", 0)
    continuity_sessions = payload.get("continuity_sessions", 0)
    if ec == 0:
        continuity_msg = (
            "; " + str(continuity_sessions) + " continuity sessions, 0 violations"
            if continuity_sessions > 0 else "; continuity doc not found"
        )
        print("KNOWLEDGE-MD-GATE: "
              + str(len(anchors))
              + " drift-immune anchors; 0 stale cites; 0 wide cells; "
              + str(len(payload["canonical_create_group_methods"]))
              + " canonical _create_*_group methods in tabs/settings_tab.py"
              + continuity_msg + ". Clean.")
        return 0
    print("KNOWLEDGE-MD-GATE: build fails. "
          + str(drift_count) + " stale cite(s); "
          + str(wide_count) + " wide cell(s); "
          + str(settings_count) + " canonical _create_*_group method(s) missing;"
          + " " + str(continuity_violations) + " continuity violation(s).")
    print("")
    if drift_count:
        print("--- stale cite offenders ---")
        for off in payload["offenders"]:
            print("  line " + str(off["line"]).rjust(4)
                  + ": [" + off["desc"] + "] -> " + repr(off["snippet"]))
        print("")
        print("Refactor to drift-immune ``<module>.<class>.<anchor>`` form")
        print("(see \u00a71 row + blockquote).")
    if wide_count:
        print("--- wide-cell offenders ---")
        for wc in payload["wide_cells"]:
            print("  line " + str(wc["line"]).rjust(4)
                  + " cell-" + str(wc["cell"])
                  + " [" + str(wc["chars"]) + " chars > "
                  + str(WIDE_CELL_THRESHOLD) + "]: "
                  + wc["snippet"])
        print("")
        print("Compact via row-pointer + sub-section pattern")
        print("(see \u00a72 L70 ``ai_base.py`` row refactor for canonical example).")
    if settings_count:
        print("--- settings-tab structural-integrity offenders ---")
        print("File: " + str(SETTINGS_TAB_PY))
        print("Expected canonical `_create_*_group` methods (>= "
              + str(EXPECTED_CREATE_GROUP_MIN) + "):")
        for m in payload["canonical_create_group_methods"]:
            print("    " + m)
        print("")
        print("Missing from file:")
        for m in payload["settings_tab_missing"][:25]:
            print("  - " + m)
        print("")
        print("Restore tabs/settings_tab.py from a known-good source, then")
        print("re-apply ONLY surgical edits validated by:")
        print("  python -m py_compile tabs/settings_tab.py")
        print("  # offscreen smoke:")
        print("  QT_QPA_PLATFORM=offscreen python -c \\")
        print("    'from app_core import KokertechDashboard; window = KokertechDashboard()'")
    if continuity_violations:
        print("--- continuity file consistency offenders ---")
        print("File: " + str(CONTINUITY_PATH))
        print(str(continuity_violations) + " violation(s) across "
              + str(continuity_sessions) + " sessions.")
        print("Run for diagnostics:")
        print("  python scripts/assert_continuity_file_consistency.py --verbose")
        print("")
        print("Fix: ensure every filename in the Session Index 'Files Changed[^2]' column")
        print("appears in the corresponding session body's '### Files Changed' table.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
