# audit_doc_width.py -- Doc-width auditor for KNOWLEDGE.md PLUS a
# code-quality auditor for tabs/settings_tab.py.
# (1) Walks each ## N. section, counts narrative bullets + flags any line
#     exceeding WIDE_LINE_THRESHOLD chars (>3 display lines @ 80-col wrap).
# (2) Walks tabs/settings_tab.py and identifies code-quality issues inside
#     `_create_*_group` methods:
#     (a) duplicate persona-rebuild blocks (within +/- 5 lines)
#     (b) dead code after `return group` (lines unreachable in current method)
#     (c) `<group>_group.setLayout(<group>_layout)` patterns that are
#         unreachable scaffold (the function already returned `group`)
# Companion to assert_known_anchors.py: assert_known_anchors gates CITE
# form + structural-integrity of methods; audit_doc_width gates NARRATIVE
# WIDTH + per-method INTERNAL code quality.
# Run before PR to flag wide-block + dedup candidates.
#
# Usage:
#   python audit_doc_width.py                   # CLI: per-category report, exit 0=clean, 1=flagged
#   python audit_doc_width.py --json            # JSON machine-readable
#   python audit_doc_width.py --offenders-only  # only the flagged lines
#   from audit_doc_width import audit_all       # pytest entry

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
KNOWLEDGE_MD = REPO_ROOT / "KNOWLEDGE.md"

WIDE_LINE_THRESHOLD = 240  # chars per line; ~3 display lines @ 80-col wrap

# === Code-quality thresholds for tabs/settings_tab.py ===
SETTINGS_TAB_PY = REPO_ROOT / "tabs" / "settings_tab.py"
CANONICAL_RETURN_MARKER = "        return group"
# Auto-detect ANY `<X>_group.setLayout(<X>_layout)` unreachable scaffold pattern.
# Back-reference `\1` ensures both sides of the setLayout() call refer to the
# same group name; eliminates false positives like
# `sandbox_group.setLayout(web_layout)`. Generalized across all current and
# future `_create_*_group` methods without per-method whitelist maintenance.
UNREACHABLE_SETLAYOUT_RE = re.compile(
    r"^\s{4}(\w+)_group\.setLayout\(\1_layout\)\s*$",
    re.MULTILINE,
)
# Persona-rebuild proximity window (lines).
PERSONA_REBUILD_PROXIMITY = 5
# Persona-rebuild needle (substring; the full `persona_combo.addItems(...)`
# expression is the rebuild call site -- found in many _create_*_group methods).
PERSONA_ADD_ITEMS_NEEDLE = "persona_combo.addItems"


def _load_sections():
    if not KNOWLEDGE_MD.exists():
        raise FileNotFoundError("KNOWLEDGE.md not found at " + str(KNOWLEDGE_MD))
    text = KNOWLEDGE_MD.read_text(encoding="utf-8")
    lines = text.splitlines()
    h2_re = re.compile(r"^## (\d+)\. ")
    sections = [[i, l] for i, l in enumerate(lines) if h2_re.match(l)]
    for k in range(len(sections) - 1):
        sections[k].append(sections[k + 1][0])
    sections[-1].append(len(lines))
    return sections, lines


def audit_section(start, label, end, lines):
    bullet_re = re.compile(r"^[ \t]*(?:[-*] |\d+\. )")
    n_bullets = sum(
        1 for k in range(start, end)
        if bullet_re.match(lines[k])
    )
    flagged = []
    for k in range(start, end):
        chars = len(lines[k].encode("utf-8"))
        disp = max(1, chars // 80 + (1 if chars % 80 else 0))
        if chars > WIDE_LINE_THRESHOLD or disp > 3:
            flagged.append({
                "line": k + 1,
                "chars": chars,
                "display_lines": disp,
                "snippet": lines[k][:120],
            })
    return {
        "heading": label,
        "section_start_line": start + 1,
        "section_end_line": end,
        "total_lines": end - start,
        "narrative_bullets": n_bullets,
        "flagged_count": len(flagged),
        "flagged_lines": flagged,
    }


def audit_all():
    sections, lines = _load_sections()
    return [audit_section(s, lbl, e, lines) for s, lbl, e in sections]


def _filter_intentional_duplicates(text):
    """Skip lines tagged `# intentional-duplicate` -- the marker indicates
    a manually-preserved duplicate assignment (e.g., re-claiming a QLabel after
    styleSheet is applied) that audit_doc_width.py must NOT flag.
    Replaces each marker-tagged line with a blank line of equal length so
    the file line offsets in detector outputs still map back to source.
    """
    out_lines = []
    for line in text.splitlines(keepends=True):
        if "# intentional-duplicate" in line:
            out_lines.append(chr(32) * (len(line) - 1) + chr(10))
        else:
            out_lines.append(line)
    return "".join(out_lines)


def _walk_create_group_methods(text):
    """Yield (name, body_lines) tuples for every `def _create_*_group(self):`
    method body in `text`. Returns nothing if no methods are declared."""
    method_re = re.compile(r"^    def (_create_\w+_group)\(self\):", re.MULTILINE)
    matches = list(method_re.finditer(text))
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[start:end]
        body_lines = body.splitlines(keepends=False)
        yield m.group(1), body_lines


def find_dead_code_after_return(file_path=SETTINGS_TAB_PY):
    """Walk every `_create_*_group` method body, locate the first line
    whose stripped content equals ``CANONICAL_RETURN_MARKER``, then report
    any non-empty lines after it as DEAD code candidates. The function
    already returned `group` so anything below is unreachable."""
    if not file_path.exists():
        return []
    text = file_path.read_text(encoding="utf-8")
    out = []
    for name, body_lines in _walk_create_group_methods(text):
        try:
            return_idx = next(
                i for i, l in enumerate(body_lines)
                if l.strip() == CANONICAL_RETURN_MARKER.strip()
            )
        except StopIteration:
            continue
        # Lines after return_idx are dead within this method body.
        for offset, line in enumerate(body_lines[return_idx + 1:], start=1):
            if not line.strip():
                continue
            out.append({
                "method": name,
                "body_offset_lines_after_return": offset,
                "snippet": line.strip()[:160],
            })
    return out


def find_unreachable_group_setlayout(file_path=SETTINGS_TAB_PY):
    """Auto-detect ANY `<X>_group.setLayout(<X>_layout)` unreachable scaffold
    using regex `UNREACHABLE_SETLAYOUT_RE` (back-reference `\\1` guarantees
    both sides reference the same group name). Catches current + future
    `_create_*_group` methods without manual whitelist maintenance."""
    if not file_path.exists():
        return []
    text = file_path.read_text(encoding="utf-8")
    out = []
    for m in UNREACHABLE_SETLAYOUT_RE.finditer(text):
        group_name = m.group(1)
        line_no = text[:m.start()].count(chr(10)) + 1
        pattern_repr = group_name + "_group.setLayout(" + group_name + "_layout)"
        out.append({
            "line": line_no,
            "pattern": pattern_repr,
            "snippet": m.group(0).strip(),
        })
    return out


def find_duplicate_persona_rebuilds(file_path=SETTINGS_TAB_PY):
    """Find calls to `persona_combo.addItems(...)` rebuilt in close
    proximity (<= PERSONA_REBUILD_PROXIMITY lines apart). Each duplicate
    candidate is reported with line number + distance to the previous."""
    if not file_path.exists():
        return []
    text = file_path.read_text(encoding="utf-8")
    out = []
    needle = PERSONA_ADD_ITEMS_NEEDLE
    last_pos = None
    for m in re.finditer(re.escape(needle), text):
        if last_pos is not None:
            distance_lines = text[last_pos:m.start()].count(chr(10))
            if distance_lines <= PERSONA_REBUILD_PROXIMITY:
                line_no = text[:m.start()].count(chr(10)) + 1
                out.append({
                    "line": line_no,
                    "distance_lines_to_prev": distance_lines,
                    "snippet": needle,
                })
        last_pos = m.start()
    return out


def audit_code_quality(file_path=SETTINGS_TAB_PY):
    """Return an aggregated code-quality report for `file_path`. Covers
    ONLY `tabs/settings_tab.py`-relevant categories. Empty dict if file
    missing."""
    return {
        "dead_code_after_return": find_dead_code_after_return(file_path),
        "unreachable_group_setlayout": find_unreachable_group_setlayout(file_path),
        "duplicate_persona_rebuilds": find_duplicate_persona_rebuilds(file_path),
    }


def _summary():
    sections = audit_all()
    code_quality = audit_code_quality()
    total_flagged = sum(s["flagged_count"] for s in sections)
    code_quality_total = (
        len(code_quality["dead_code_after_return"])
        + len(code_quality["unreachable_group_setlayout"])
        + len(code_quality["duplicate_persona_rebuilds"])
    )
    wide_sections = [s for s in sections if s["flagged_count"] > 0]
    return total_flagged + code_quality_total, {
        "doc_path": str(KNOWLEDGE_MD),
        "settings_tab_path": str(SETTINGS_TAB_PY),
        "thresholds": {"wide_line_chars": WIDE_LINE_THRESHOLD},
        "total_sections": len(sections),
        "wide_sections": len(wide_sections),
        "total_flagged_lines": total_flagged,
        "sections": sections,
        "code_quality_total": code_quality_total,
        "code_quality": code_quality,
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    # Windows console defaults to cp1252; ensure stdout can render Unicode.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if "--json" in argv:
        total, payload = _summary()
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 1 if total > 0 else 0
    sections = audit_all()
    flagged_sum = sum(s["flagged_count"] for s in sections)
    code_quality = audit_code_quality()
    cq_total = (
        len(code_quality["dead_code_after_return"])
        + len(code_quality["unreachable_group_setlayout"])
        + len(code_quality["duplicate_persona_rebuilds"])
    )
    total_offenders = flagged_sum + cq_total
    print("DOC-WIDTH-AUDIT: KNOWLEDGE.md has "
          + str(len(sections))
          + " ## N. sections, "
          + str(flagged_sum)
          + " wide line(s) flagged.")
    print("CODE-QUALITY-AUDIT: tabs/settings_tab.py has "
          + str(cq_total)
          + " code-quality offender(s): "
          + str(len(code_quality["dead_code_after_return"]))
          + " dead-after-return + "
          + str(len(code_quality["unreachable_group_setlayout"]))
          + " unreachable-setLayout + "
          + str(len(code_quality["duplicate_persona_rebuilds"]))
          + " duplicate-persona-rebuild.")
    print("")
    print("Threshold: " + str(WIDE_LINE_THRESHOLD)
          + " chars per line (= ~3 display lines @ 80-col wrap). Persona-rebuild "
          + "proximity window: <= " + str(PERSONA_REBUILD_PROXIMITY) + " lines.")
    print("")
    for s in sections:
        marker = " !!! " if s["flagged_count"] > 0 else "     "
        print(marker + s["heading"]
              + "  (L" + str(s["section_start_line"])
              + ".." + str(s["section_end_line"])
              + " | " + str(s["total_lines"]) + " lines | "
              + str(s["narrative_bullets"]) + " bullets | "
              + str(s["flagged_count"]) + " flagged)")
    if flagged_sum and "--offenders-only" not in argv:
        print("")
        print("--- flagged wide lines (max 12 per section) ---")
        for s in sections:
            if not s["flagged_lines"]:
                continue
            print("")
            print(s["heading"])
            for fl in s["flagged_lines"][:12]:
                print("  L" + str(fl["line"]).rjust(4)
                      + " [" + str(fl["chars"]) + " chars, ~"
                      + str(fl["display_lines"]) + " disp-lines]: "
                      + fl["snippet"][:100])
    if cq_total and "--offenders-only" not in argv:
        print("")
        print("--- settings-tab code-quality offenders ---")
        if code_quality["dead_code_after_return"]:
            print("  (b) dead code after `return group`:")
            for d in code_quality["dead_code_after_return"][:30]:
                print("    " + d["method"] + " +"
                      + str(d["body_offset_lines_after_return"])
                      + " lines: " + d["snippet"][:120])
        if code_quality["unreachable_group_setlayout"]:
            print("  (c) unreachable `<group>_group.setLayout(...)` scaffold:")
            for u in code_quality["unreachable_group_setlayout"][:30]:
                print("    L" + str(u["line"]).rjust(4) + ": " + u["pattern"])
        if code_quality["duplicate_persona_rebuilds"]:
            print("  (a) duplicate persona-rebuild blocks:")
            for p in code_quality["duplicate_persona_rebuilds"][:30]:
                print("    L" + str(p["line"]).rjust(4)
                      + " (within " + str(p["distance_lines_to_prev"])
                      + " lines of previous): "
                      + p["snippet"])
    return 1 if total_offenders > 0 else 0


if __name__ == "__main__":
    sys.exit(main())



