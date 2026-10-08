#!/usr/bin/env python3
# assert_section11_render_clean.py -- CI-style pre-flight gate for freebuff_instructions.md section 11.
# (a) Pipe-count == 4 on every table row in the pitfall table.
# (b) No blank line splits the footnote blockquote (renders as multiple <blockquote> otherwise).
# (c) Every backslash+letter token is wrapped in a backtick code span.
# Mirrors assert_known_anchors.py pattern (find_X / assert_X / main, --json support).
# Pre-flight gate: wired into run_tests.bat BEFORE assert_known_anchors.py so a ``section 11``
# markup regression is caught during normal test runs (mirrors KNOWLEDGE.md §17 rationale).
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SECTION11_PATH = REPO_ROOT / "docs" / "sessions" / "freebuff_instructions.md"

# Exit-code contract (shared with scripts/run_all_gates.py): 0 = clean,
# 1 = violation, 3 = SKIPPED because the unpublished working-set doc is absent.
SKIP_EXIT = 3
SKIP_REASON = (
    "docs/sessions/freebuff_instructions.md is part of the internal working set "
    "(gitignored, not distributed with the public repo); run from the full "
    "working tree to enforce this gate."
)

# Section 11 pitfall table is <pitfall> | <why> | <fix> -> 4 pipes = 3 columns.
EXPECTED_PIPE_COUNT = 4
SECTION11_HEADER = "## 11. Common Pitfalls to Avoid"
SECTION12_PREFIX = "## 12."
FOOTNOTE_HEADER_PREFIX = "> **Footnote"

# Single backslash + single ASCII letter (the v0.18 fragile-substring family).
# Regex-source pattern: r"\\[a-zA-Z]" -> compiled as a literal backslash followed
# by one ASCII letter. The two backslash chars in the raw string are the regex
# escape sequence for a SINGLE literal backslash in input.
BT_CHAR = chr(96)             # backtick (used by _find_code_spans state machine)
BACKSLASH_LETTER_RE = re.compile(r"\\[a-zA-Z]")


class Section11Error(Exception):
    pass


def _read_section11_slice():
    """Return (slice_lines, sec_start_idx_in_full, all_lines). Raises Section11Error."""
    if not SECTION11_PATH.exists():
        raise Section11Error("file not found: " + str(SECTION11_PATH))
    text = SECTION11_PATH.read_text(encoding="utf-8")
    all_lines = text.splitlines()
    sec = next(
        (i for i, l in enumerate(all_lines) if l.startswith(SECTION11_HEADER)),
        None,
    )
    end = next(
        (i for i, l in enumerate(all_lines) if l.startswith(SECTION12_PREFIX)),
        None,
    )
    if sec is None or end is None or end <= sec:
        raise Section11Error(
            "boundaries missing: sec=" + repr(sec) + " end=" + repr(end)
        )
    return all_lines[sec:end], sec, all_lines


def find_pipe_violations(slice_lines, sec_start):
    """Pure-function form: Table rows in ``slice_lines`` whose pipe-count != EXPECTED_PIPE_COUNT.

    ``sec_start`` is the 0-based line index in the FULL file where ``slice_lines``
    begins; returned line numbers are 1-based absolute (``sec_start + i + 1``).
    No file IO. Caller is responsible for reading the file into ``slice_lines``.
    """
    out = []
    for i, line in enumerate(slice_lines):
        if not line.lstrip().startswith("|"):
            continue
        cnt = line.count("|")
        if cnt != EXPECTED_PIPE_COUNT:
            out.append((sec_start + i + 1, cnt, line[:120]))
    return out


def find_blockquote_break_violations(slice_lines, sec_start):
    """Pure-function form: Blank lines inside the footnote blockquote bracketed
    by > lines = split. ``sec_start`` is 0-based full-file index of slice start.

    Locates the footnote header via ``FOOTNOTE_HEADER_PREFIX`` lookup within the
    slice; if no footnote header is present, returns no violations.
    """
    fn_rel = next(
        (
            i for i, l in enumerate(slice_lines)
            if l.lstrip().startswith(FOOTNOTE_HEADER_PREFIX)
        ),
        None,
    )
    if fn_rel is None:
        return []
    out = []
    for k, line in enumerate(slice_lines):
        if line.strip() != "":
            continue
        prev_q = False
        for j in range(k - 1, fn_rel - 1, -1):
            if slice_lines[j].strip() == "":
                continue
            prev_q = slice_lines[j].lstrip().startswith(">")
            break
        next_q = False
        for j in range(k + 1, len(slice_lines)):
            if slice_lines[j].strip() == "":
                continue
            next_q = slice_lines[j].lstrip().startswith(">")
            break
        if prev_q and next_q:
            out.append((sec_start + k + 1, line))
    return out


def _find_code_spans(line):
    """Return list of (open_pos, close_pos) for matched code spans in ``line``.

    Match rule (CommonMark-style): a BT-run of length N closes the most recent
    unclosed BT-run of equal length; otherwise it opens a new (unmatched) span.
    Unmatched opens are deliberately dropped from the returned list so that
    matches inside an unmatched run are reported as outside any code span
    (matching CommonMark's literal-backtick rendering for unmatched BTs).

    The algorithm is conservative for nested cases (`` ``f`oo`` `` generates
    both an outer length-2 span AND a ``fake`` inner length-1 span); this is
    safe for our check because every ``\\letter`` match inside the outer is
    inside SOMETHING -> correctly skipped.
    """
    runs = []  # list of (position, length) for each maximal BT-run
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
    pending = []  # stack of (open_pos, open_len)
    for rpos, rlen in runs:
        if pending and pending[-1][1] == rlen:
            # Top of stack matches this run length -> close.
            opos, _ = pending.pop()
            spans.append((opos, rpos))
        else:
            pending.append((rpos, rlen))
    return spans


def find_bare_backslash_violations(slice_lines, sec_start):
    """Pure-function form: Every backslash+letter token OUTSIDE a code span.

    Uses a BT-run state machine (``_find_code_spans``) that correctly handles
    single-, double-, triple-backtick spans AND nested inner-BT-inside-outer
    spans. Replaces the earlier naive ``cum_bt % 2 == 1`` parity check which
    mis-flagged matches inside double-backtick fences.

    ``sec_start`` is 0-based full-file index of slice start; returned line
    numbers are 1-based absolute (``sec_start + k + 1``). No file IO.
    """
    out = []
    for k, line in enumerate(slice_lines):
        if not line.strip():
            continue
        spans = _find_code_spans(line)
        for m in BACKSLASH_LETTER_RE.finditer(line):
            pos = m.start()
            if any(osp < pos < csp for osp, csp in spans):
                # Inside a code span -> skip.
                continue
            prologue = line[:pos][-40:]
            ctx_start = max(0, pos - 8)
            ctx_end = min(len(line), pos + 6)
            ctx = line[ctx_start:ctx_end]
            out.append((sec_start + k + 1, prologue, ctx))
    return out


def _load_offs_from_file():
    """File-IO helper for the assert_X wrappers: load section 11 slice and run all
    three finders. Returns ``(slice_lines, sec_start, pipe_offs, bq_offs, bare_offs)``.
    Raises Section11Error if section 11 cannot be loaded.
    """
    slice_lines, sec_start, _ = _read_section11_slice()
    return (
        slice_lines,
        sec_start,
        find_pipe_violations(slice_lines, sec_start),
        find_blockquote_break_violations(slice_lines, sec_start),
        find_bare_backslash_violations(slice_lines, sec_start),
    )


def assert_section11_pipe_count():
    _, _, offs, _, _ = _load_offs_from_file()
    if not offs:
        return
    n = len(offs)
    head = "\n".join(
        "  line " + str(p[0]).rjust(4)
        + " [" + str(p[1]) + " pipes != " + str(EXPECTED_PIPE_COUNT) + "]: "
        + p[2]
        for p in offs[:50]
    )
    tail = (
        "\n  ... and " + str(n - 50) + " more (truncated)") if n > 50 else ""
    raise AssertionError(
        "Section-11 pipe-count gate: " + str(n)
        + " table row(s) in section 11 of " + str(SECTION11_PATH)
        + " have pipe_count != " + str(EXPECTED_PIPE_COUNT) + " (expected 3-column layout).\n\n"
        + "Offenders:\n" + head + tail
    )


def assert_section11_blockquote_continuity():
    _, _, _, offs, _ = _load_offs_from_file()
    if not offs:
        return
    n = len(offs)
    head = "\n".join(
        "  line " + str(o[0]).rjust(4)
        + ": blank line splits the footnote blockquote: " + repr(o[1])
        for o in offs[:50]
    )
    tail = (
        "\n  ... and " + str(n - 50) + " more (truncated)") if n > 50 else ""
    raise AssertionError(
        "Section-11 blockquote-continuity gate: " + str(n)
        + " blank line(s) inside the footnote split the blockquote in any CommonMark viewer"
        + " (would render as multiple <blockquote>).\n\nOffenders:\n" + head + tail
    )


def assert_section11_backslash_in_backticks():
    _, _, _, _, offs = _load_offs_from_file()
    if not offs:
        return
    n = len(offs)
    head = "\n".join(
        "  line " + str(o[0]).rjust(4)
        + ": context=" + repr(o[2])
        + ", prologue=" + repr(o[1])
        for o in offs[:50]
    )
    tail = (
        "\n  ... and " + str(n - 50) + " more (truncated)") if n > 50 else ""
    raise AssertionError(
        "Section-11 backslash-in-backticks gate: " + str(n)
        + " bare backslash+letter token(s) in section 11 prose. Every such"
        + " occurrence must be wrapped in a backtick code span (v0.18 fragile-substring"
        + " family: str_replace / sed / write_file all bit-tested broken on such anchors)."
        + "\n\nOffenders:\n" + head + tail
    )


def _summary():
    if not SECTION11_PATH.exists():
        return (SKIP_EXIT, {"skipped": True, "reason": SKIP_REASON, "section11_path": str(SECTION11_PATH)})
    try:
        slice_lines, sec_start, _ = _read_section11_slice()
    except Section11Error:
        return (1, {"error": "section 11 slice could not be loaded"})
    p = find_pipe_violations(slice_lines, sec_start)
    b = find_blockquote_break_violations(slice_lines, sec_start)
    bb = find_bare_backslash_violations(slice_lines, sec_start)
    return (
        0 if (not p and not b and not bb) else 1,
        {
            "section11_path": str(SECTION11_PATH),
            "expected_pipe_count": EXPECTED_PIPE_COUNT,
            "pipe_violations": len(p),
            "pipe_offenders": [
                {"line": l, "pipes": n, "snippet": s} for l, n, s in p
            ],
            "blockquote_break_violations": len(b),
            "blockquote_break_offenders": [
                {"line": l, "snippet": s} for l, s in b
            ],
            "bare_backslash_violations": len(bb),
            "bare_backslash_offenders": [
                {"line": l, "prologue": pr, "context": c} for l, pr, c in bb
            ],
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
        print(json.dumps(payload, indent=2))
        return ec
    if payload.get("skipped"):
        print("SECTION-11-GATE: SKIPPED (file absent: " + str(SECTION11_PATH) + ")")
        print("  " + payload["reason"])
        return ec
    if "error" in payload:
        print("SECTION-11-GATE: " + payload["error"])
        return 1
    if ec == 0:
        pv = payload["pipe_violations"]
        bv = payload["blockquote_break_violations"]
        bbv = payload["bare_backslash_violations"]
        print(
            "SECTION-11-GATE: " + str(pv)
            + " pipe viol | " + str(bv)
            + " blockquote break | " + str(bbv)
            + " bare backslash. Clean."
        )
        return 0
    pv = payload["pipe_violations"]
    bv = payload["blockquote_break_violations"]
    bbv = payload["bare_backslash_violations"]
    print("SECTION-11-GATE: build fails.")
    print("  pipe viol:            " + str(pv))
    print("  blockquote break:     " + str(bv))
    print("  bare backslash:       " + str(bbv))
    print("")
    if pv:
        print("--- pipe viol offenders ---")
        for p in payload["pipe_offenders"]:
            print(
                "  line " + str(p["line"]).rjust(4)
                + " [" + str(p["pipes"]) + " pipes != " + str(EXPECTED_PIPE_COUNT) + "]: "
                + p["snippet"]
            )
    if bv:
        print("--- blockquote break offenders ---")
        for b in payload["blockquote_break_offenders"]:
            print(
                "  line " + str(b["line"]).rjust(4)
                + ": " + repr(b["snippet"])
            )
    if bbv:
        print("--- bare backslash offenders ---")
        for off in payload["bare_backslash_offenders"]:
            print(
                "  line " + str(off["line"]).rjust(4)
                + ": context=" + repr(off["context"])
                + ", prologue=" + repr(off["prologue"])
            )
    return 1


if __name__ == "__main__":
    sys.exit(main())
