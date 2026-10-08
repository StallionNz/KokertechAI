#!/usr/bin/env python3
# assert_freebuff_sections_render_clean.py -- CI-style pre-flight gate for the WHOLE
# freebuff_instructions.md document.
# Walks every ``## N.`` section, runs three invariants in each:
#   (a) Pipe-count INTERNAL consistency within each table (mode-based; supports tables
#       of varying column counts such as section 17's 5-column + 3-column tables).
#   (b) No blank line splits any blockquote region ANYWHERE in any section (universal).
#   (c) Every backslash+letter token is wrapped in a backtick code span
#       (CommonMark BT-run state machine via ``_find_code_spans``).
# Per-section + global risk count reported. Pre-flight gate (replaces
# ``assert_section11_render_clean.py`` at the start of ``run_tests.bat``).
# Mirrors ``assert_known_anchors.py`` pattern (find_X / _summary / main, --json support).
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "sessions" / "freebuff_instructions.md"

# Exit-code contract (shared with scripts/run_all_gates.py): 0 = clean,
# 1 = violation, 3 = SKIPPED because the unpublished working-set doc is absent.
SKIP_EXIT = 3
SKIP_REASON = (
    "docs/sessions/freebuff_instructions.md is part of the internal working set "
    "(gitignored, not distributed with the public repo); run from the full "
    "working tree to enforce this gate."
)

# Single backslash + single ASCII letter (v0.18 fragile-substring family).
# Regex-source pattern: r"\\[a-zA-Z]" -> compiled as a literal backslash followed
# by one ASCII letter. The two backslash chars in the raw string are the regex
# escape sequence for a SINGLE literal backslash in input.
BT_CHAR = chr(96)             # backtick (used by _find_code_spans state machine)
BACKSLASH_LETTER_RE = re.compile(r"\\[a-zA-Z]")
HEADER_RE = re.compile(r"^##\s+(\d+)\.\s*(.*)$")


class GateError(Exception):
    pass


def _read_sections():
    """Return list of section dicts with 0-based line indices."""
    if not DOC_PATH.exists():
        raise GateError("file not found: " + str(DOC_PATH))
    text = DOC_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    headers = []
    for i, l in enumerate(lines):
        m = HEADER_RE.match(l)
        if m:
            headers.append((i, int(m.group(1)), m.group(2)))
    sections = []
    for idx, (line_0, num, title) in enumerate(headers):
        end_line_0 = headers[idx + 1][0] - 1 if idx + 1 < len(headers) else len(lines) - 1
        sections.append({
            "num": num,
            "title": title,
            "header_line": line_0 + 1,
            "start": line_0,
            "end": end_line_0,
            "lines": lines[line_0:end_line_0 + 1],
        })
    return sections


def _find_code_spans(line):
    """Return list of (open_pos, close_pos) for matched code spans in ``line``.

    Match rule (CommonMark-style): a BT-run of length N closes the most recent
    unclosed BT-run of equal length; otherwise it opens a new (unmatched) span.
    Unmatched opens are dropped so matches inside an unmatched run are reported
    as outside any code span (matching CommonMark literal-backtick rendering).
    Conservative for nested cases (outer-and-inner spans both reported).
    """
    runs = []
    i = 0
    n = len(line)
    while i < n:
        if line[i] == BT_CHAR:
            j = i
            while j < n and line[j] == BT_CHAR:
                j += 1
            runs.append((i, j - i))
            i = j
        else:
            i += 1
    spans = []
    pending = []
    for rpos, rlen in runs:
        if pending and pending[-1][1] == rlen:
            opos, _ = pending.pop()
            spans.append((opos, rpos))
        else:
            pending.append((rpos, rlen))
    return spans


def find_pipe_violations_in_section(sl, sec_start, sec_num):
    """Per-table INTERNAL consistency. Each contiguous run of lines starting
    with ``|`` is one table; rows whose pipe count differs from the table's
    mode (most common count) are flagged. Supports tables of varying column
    counts within the same document.
    """
    out = []
    in_tbl = False
    rows = []
    for k, line in enumerate(sl):
        if line.lstrip().startswith("|"):
            in_tbl = True
            rows.append((k, line.count("|")))
        else:
            if in_tbl:
                _flush_pipe_table(out, rows, sl, sec_start, sec_num)
                in_tbl = False
                rows = []
    if in_tbl and rows:
        _flush_pipe_table(out, rows, sl, sec_start, sec_num)
    return out


def _flush_pipe_table(out, rows, sl, sec_start, sec_num):
    counts = [c for _, c in rows]
    if not counts:
        return
    mode_count = Counter(counts).most_common(1)[0][0]
    for rk, cnt in rows:
        if cnt != mode_count:
            out.append((sec_start + rk + 1, sec_num, cnt, mode_count, sl[rk][:120]))


def find_blockquote_break_violations(sl, sec_start, sec_num):
    """No blank line splits any blockquote region UNIVERSALLY across the section.
    Any blank line whose nearest non-blank predecessor AND nearest non-blank
    successor both start with ``>`` is a fragmentation in any CommonMark viewer.
    """
    out = []
    for k, line in enumerate(sl):
        if line.strip() != "":
            continue
        prev_q = False
        for j in range(k - 1, -1, -1):
            if sl[j].strip() == "":
                continue
            prev_q = sl[j].lstrip().startswith(">")
            break
        next_q = False
        for j in range(k + 1, len(sl)):
            if sl[j].strip() == "":
                continue
            next_q = sl[j].lstrip().startswith(">")
            break
        if prev_q and next_q:
            out.append((sec_start + k + 1, sec_num, line))
    return out


def find_bare_backslash_violations(sl, sec_start, sec_num):
    """Every ``\\letter`` token OUTSIDE a code span (CommonMark state machine)."""
    out = []
    for k, line in enumerate(sl):
        if not line.strip():
            continue
        spans = _find_code_spans(line)
        for m in BACKSLASH_LETTER_RE.finditer(line):
            pos = m.start()
            if any(osp < pos < csp for osp, csp in spans):
                continue
            prologue = line[:pos][-40:]
            ctx_start = max(0, pos - 8)
            ctx_end = min(len(line), pos + 6)
            ctx = line[ctx_start:ctx_end]
            out.append((sec_start + k + 1, sec_num, prologue, ctx))
    return out


def _summary():
    if not DOC_PATH.exists():
        return (SKIP_EXIT, {"skipped": True, "reason": SKIP_REASON, "doc_path": str(DOC_PATH)})
    sections = _read_sections()
    per_section = {}
    total_pipe = 0
    total_blockquote = 0
    total_bare = 0
    for s in sections:
        pipe_offenders = [
            {"line": l, "pipes": c, "mode": m, "snippet": sn}
            for l, _, c, m, sn in find_pipe_violations_in_section(
                s["lines"], s["start"], s["num"]
            )
        ]
        blockquote_offenders = [
            {"line": l, "snippet": sn}
            for l, _, sn in find_blockquote_break_violations(
                s["lines"], s["start"], s["num"]
            )
        ]
        bare_offenders = [
            {"line": l, "prologue": p, "context": c}
            for l, _, p, c in find_bare_backslash_violations(
                s["lines"], s["start"], s["num"]
            )
        ]
        per_section[s["num"]] = {
            "num": s["num"],
            "title": s["title"],
            "header_line": s["header_line"],
            "pipe_offenders": pipe_offenders,
            "blockquote_offenders": blockquote_offenders,
            "bare_offenders": bare_offenders,
            "pipe_count": len(pipe_offenders),
            "blockquote_count": len(blockquote_offenders),
            "bare_count": len(bare_offenders),
        }
        total_pipe += len(pipe_offenders)
        total_blockquote += len(blockquote_offenders)
        total_bare += len(bare_offenders)
    has_any = (total_pipe + total_blockquote + total_bare) > 0
    return (
        1 if has_any else 0,
        {
            "doc_path": str(DOC_PATH),
            "total_sections": len(sections),
            "total_pipe_violations": total_pipe,
            "total_blockquote_break_violations": total_blockquote,
            "total_bare_backslash_violations": total_bare,
            "per_section": per_section,
        },
    )


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ec, payload = _summary()
    if "--json" in argv:
        print(json.dumps(payload, indent=2, default=str))
        return ec
    if payload.get("skipped"):
        print("FREEBUFF-SECTIONS-GATE: SKIPPED (file absent: " + str(DOC_PATH) + ")")
        print("  " + payload["reason"])
        return ec
    if ec == 0:
        print(
            "FREEBUFF-SECTIONS-GATE: "
            + str(payload["total_sections"])
            + " sections scanned | "
            + str(payload["total_pipe_violations"])
            + " pipe viol | "
            + str(payload["total_blockquote_break_violations"])
            + " blockquote break | "
            + str(payload["total_bare_backslash_violations"])
            + " bare backslash. Clean."
        )
        return 0
    print("FREEBUFF-SECTIONS-GATE: build fails.")
    print("  Total sections:    " + str(payload["total_sections"]))
    print("  Pipe viol:         " + str(payload["total_pipe_violations"]))
    print("  Blockquote break:  " + str(payload["total_blockquote_break_violations"]))
    print("  Bare backslash:    " + str(payload["total_bare_backslash_violations"]))
    print("")
    for num, ps in payload["per_section"].items():
        if ps["pipe_count"] or ps["blockquote_count"] or ps["bare_count"]:
            print(
                "-- section " + str(ps["num"])
                + " " + (ps["title"][:50] or "")
                + " (line " + str(ps["header_line"]) + ") --"
            )
            if ps["pipe_offenders"]:
                print("  pipe viols (" + str(ps["pipe_count"]) + "):")
                for off in ps["pipe_offenders"][:5]:
                    print(
                        "    line " + str(off["line"]).rjust(4)
                        + ": " + str(off["pipes"]) + " pipes vs mode "
                        + str(off["mode"]) + ": " + repr(off["snippet"])
                    )
            if ps["blockquote_offenders"]:
                print("  blockquote breaks (" + str(ps["blockquote_count"]) + "):")
                for off in ps["blockquote_offenders"][:5]:
                    print(
                        "    line " + str(off["line"]).rjust(4)
                        + ": " + repr(off["snippet"])
                    )
            if ps["bare_offenders"]:
                print("  bare backslash (" + str(ps["bare_count"]) + "):")
                for off in ps["bare_offenders"][:5]:
                    print(
                        "    line " + str(off["line"]).rjust(4)
                        + ": context=" + repr(off["context"])
                        + ", prologue=" + repr(off["prologue"])
                    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
