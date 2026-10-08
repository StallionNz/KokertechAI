#!/usr/bin/env python3
"""assert_continuity_file_consistency.py -- CI-style gate for docs/sessions/continuace_freebuff.md.

Validates the "summary vs detailed" file listing pattern:
  Session Index "Files Changed[^2]"  -> comma-separated summary of primary files
  Session body "### Files Changed"   -> every file individually listed

Rules:
  (1) Every filename-like entry in a session's index row MUST appear in that session's
      body table. (Allowing for path-prefix differences: "settings_tab.py" matches
      "tabs/settings_tab.py".)
  (2) The body table may list MORE files than the index row -- by design (see footnote [^2]).
  (3) Sessions with no index files listed (dash `-`) need no body table match.
  (4) Prose descriptions in the index (e.g. "11 docs", "4 test files") are skipped
      -- they are not filenames.

Usage:
  python scripts/assert_continuity_file_consistency.py           # exit 0 = clean
  python scripts/assert_continuity_file_consistency.py --json    # JSON report
  python scripts/assert_continuity_file_consistency.py --verbose # per-session pass/fail
"""
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTINUITY_PATH = REPO_ROOT / "docs" / "sessions" / "continuace_freebuff.md"

# Exit-code contract (shared with scripts/run_all_gates.py): 0 = clean,
# 1 = violation, 3 = SKIPPED because the unpublished working-set doc is absent.
SKIP_EXIT = 3
SKIP_REASON = (
    "docs/sessions/continuace_freebuff.md is part of the internal working set "
    "(gitignored, not distributed with the public repo); run from the full "
    "working tree to enforce this gate."
)


# -- Helpers ----------------------------------------------------------------

def _read_doc(path=None):
    if path is None:
        path = CONTINUITY_PATH
    if not path.exists():
        raise FileNotFoundError("continuity doc not found: " + str(path))
    return path.read_text(encoding="utf-8")


# Date-to-session mapping for Appendix B cross-reference.
# Each date maps to one or more session numbers.
DATE_TO_SESSIONS = {
    "Jun 1": [1],
    "Jun 6": [2, 3],
    "Jun 9": [4],
    "Jun 10": [5],
    "Jun 13": [6],
    "Jun 19": [7],
    "Jun 20": [8],
    "Jun 21": [9],
    "Jun 28": [10],
    "Jun 29": [11, 12],
    "Jul 2": [13],
    "Jul 3": [14],
    "Jul 7": [15],
    "Jul 9": [16],
    "Jul 14": [17],
    "Jul 19": [18],
}


def _normalise_filename(raw):
    """Strip parenthetical annotations and whitespace."""
    name = raw.strip()
    name = re.sub(r"\s*\([^)]*\)\s*", "", name).strip()
    return name


def _looks_like_filename(s):
    """Return True if s looks like a file path (has extension, path separator).

    Filters out prose descriptions like "11 docs", "4 test files", "3 stub modules".
    """
    if not s:
        return False
    return "." in s or "/" in s or "\\" in s


def _file_matches(summary_file, body_file):
    """True if summary_file matches body_file, allowing path-prefix differences."""
    a = _normalise_filename(summary_file).lower().replace("\\", "/")
    b = _normalise_filename(body_file).lower().replace("\\", "/")
    if a == b:
        return True
    if a.endswith("/" + b) or b.endswith("/" + a):
        return True
    a_stem = a.split("/")[-1]
    b_stem = b.split("/")[-1]
    return a_stem == b_stem


# -- Parsers ----------------------------------------------------------------

def _parse_index_table(lines):
    """Parse Session Index table into list of {num, files_str, raw_line} dicts."""
    sessions = []
    in_index = False
    found_header = False
    for line in lines:
        if line.strip().startswith("## Session Index"):
            in_index = True
            continue
        if not in_index:
            continue
        # Skip blank lines between header and table
        if not line.strip():
            continue
        # Skip separator row
        if re.match(r"^\|[-|:]+\|$", line.strip()):
            continue
        if not line.strip().startswith("|"):
            break
        # First data row is the header -- skip it
        if not found_header:
            found_header = True
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 7:
            continue
        try:
            num = int(parts[1])
        except ValueError:
            continue
        files_str = parts[6] if len(parts) >= 7 else ""
        sessions.append({"num": num, "files_str": files_str, "raw_line": line.strip()[:80]})
    return sessions


def _parse_body_files_table(lines, session_num):
    """Find and parse the '### Files Changed' table for a specific session.

    Returns list of filename strings from the table's first column.
    """
    session_header = "## Session " + str(session_num)
    next_session_header = "## Session " + str(session_num + 1)
    appendix_header = "## Appendix A"

    start_idx = None
    end_idx = len(lines)

    for i, line in enumerate(lines):
        if line.strip().startswith(session_header):
            start_idx = i
        elif start_idx is not None and (
            line.strip().startswith(next_session_header)
            or line.strip().startswith(appendix_header)
        ):
            end_idx = i
            break

    if start_idx is None:
        return []

    # Find "### Files Changed" or "## Files Changed" heading
    fc_idx = None
    for i in range(start_idx, end_idx):
        if re.match(r"^#{2,3}\s+Files Changed\s*$", lines[i].strip()):
            fc_idx = i
            break

    if fc_idx is None:
        return []

    # Parse table after heading -- skip blanks before table
    files = []
    found_sep = False
    for j in range(fc_idx + 1, end_idx):
        line = lines[j].strip()
        if not line:
            continue
        if not line.startswith("|"):
            break
        if re.match(r"^\|[-|:]+\|$", line):
            found_sep = True
            continue
        if not found_sep:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        cell = parts[1].strip().strip("`")
        if not cell or cell.startswith("#"):
            continue
        files.append(cell)

    return files


# -- Appendix B parsers ----------------------------------------------------

def _parse_appendix_b_entries(lines):
    """Parse Appendix B table and return list of {num, date, decision, raw_line} dicts."""
    entries = []
    in_appendix_b = False
    found_sep = False
    for line in lines:
        if line.strip().startswith("## Appendix B"):
            in_appendix_b = True
            continue
        if line.strip().startswith("## Appendix C"):
            break
        if not in_appendix_b:
            continue
        if not line.strip().startswith("|"):
            continue
        # Skip separator row
        if re.match(r"^\|[-|:]+\|$", line.strip()):
            if not found_sep:
                found_sep = True
            continue
        if not found_sep:
            continue
        # Header row
        if " # " in line or "# |" in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue
        try:
            num = int(parts[1])
        except ValueError:
            continue
        date = parts[2]
        decision = parts[3]
        entries.append({
            "num": num,
            "date": date,
            "decision": decision,
            "raw_line": line.strip()[:100],
        })
    return entries


def _parse_session_body_decisions(lines):
    """Extract all Decision N entries from all session bodies.

    Returns dict mapping session_num -> list of decision descriptions (first 60 chars).
    """
    decisions = {}
    current_session = None
    for line in lines:
        m = re.match(r"^## Session (\d+)", line.strip())
        if m:
            current_session = int(m.group(1))
            decisions[current_session] = []
            continue
        if line.strip().startswith("## Appendix"):
            break
        if current_session is not None:
            # Match "**Decision N: description." patterns
            dm = re.search(r"\*\*Decision \d*:\s*([^.]+?)\.", line)
            if dm:
                desc = dm.group(1).strip()[:60]
                if desc not in decisions[current_session]:
                    decisions[current_session].append(desc)
    return decisions


def _decision_text_key(full_text):
    """Extract a match key from a decision description.

    Strips backticks, normalises whitespace, and takes first 50 chars.
    Used for fuzzy matching between Appendix B and session body decisions.
    """
    text = full_text.strip()
    text = text.replace("`", "")
    text = re.sub(r"\s+", " ", text)
    # Take first 50 chars as the key
    return text[:50].strip().lower()


def find_appendix_b_violations(lines=None):
    """Cross-reference Appendix B entries against session body decisions.

    Two-tier check:
      HARD violation: Appendix B date does not map to any session (blocking).
      SOFT violation: Date maps to session but decision text cannot be found
                       in any labeled Decision N: entry (informational).

    Returns list of violation dicts.
    """
    if lines is None:
        lines = _read_doc().splitlines()

    entries = _parse_appendix_b_entries(lines)
    session_decisions = _parse_session_body_decisions(lines)
    violations = []

    for entry in entries:
        num = entry["num"]
        date = entry["date"]
        decision = entry["decision"]
        key = _decision_text_key(decision)

        # Find candidate sessions for this date
        candidate_sessions = DATE_TO_SESSIONS.get(date, [])
        if not candidate_sessions:
            # HARD violation: date unmapped
            violations.append({
                "entry_num": num,
                "date": date,
                "decision": decision,
                "severity": "hard",
                "detail": (
                    "Unmapped date " + repr(date)
                    + ". Add to DATE_TO_SESSIONS mapping."
                ),
            })
            continue

        # Check if any candidate session exists in the document
        valid_session_exists = any(
            snum in session_decisions for snum in candidate_sessions
        )
        if not valid_session_exists:
            # HARD violation: date maps to sessions but none exist in doc
            violations.append({
                "entry_num": num,
                "date": date,
                "decision": decision,
                "severity": "hard",
                "detail": (
                    "Date maps to sessions " + str(candidate_sessions)
                    + " but none found in document"
                ),
            })
            continue

        # Soft check: try to match decision text to labeled entries
        text_match_found = False
        for snum in candidate_sessions:
            if snum not in session_decisions:
                continue
            for sd in session_decisions[snum]:
                sd_key = _decision_text_key(sd)
                if len(key) >= 15 and (key in sd_key or sd_key in key):
                    text_match_found = True
                    break
                if len(key) >= 25 and (key[:25] in sd_key or sd_key[:25] in key):
                    text_match_found = True
                    break
            if text_match_found:
                break

        if not text_match_found:
            # SOFT violation: decision text not found in labeled entries
            # This is expected for sessions with narrative-style decisions
            violations.append({
                "entry_num": num,
                "date": date,
                "decision": decision,
                "severity": "soft",
                "detail": (
                    "Decision text not matched to session " + str(candidate_sessions)
                    + " labeled decisions (narrative-style session)"
                ),
            })

    return violations


def assert_appendix_b_consistency():
    """Pre-flight gate: blocks only on HARD Appendix B violations.

    Hard violations = unmapped dates or missing sessions.
    Soft violations (narrative-style sessions without labeled decisions) are
    informational only and do not block.
    """
    violations = find_appendix_b_violations()
    hard = [v for v in violations if v.get("severity") == "hard"]
    if not hard:
        return
    n = len(hard)
    lines_out = []
    for v in hard[:50]:
        lines_out.append(
            "  Entry #" + str(v["entry_num"]) + " (" + v["date"] + "): "
            + repr(v["decision"][:60])
        )
    head = "\n".join(lines_out)
    tail = "\n  ... and " + str(n - 50) + " more" if n > 50 else ""
    raise AssertionError(
        "Continuity appendix-b gate: " + str(n) + " HARD violation(s) in Appendix B.\n"
        + "Every Appendix B entry must map to a valid session date.\n\n"
        + "Violations:\n" + head + tail
    )


# -- File consistency assertions --------------------------------------------

def find_violations(lines=None):
    """Return list of violation dicts."""
    if lines is None:
        lines = _read_doc().splitlines()

    sessions = _parse_index_table(lines)
    violations = []

    for sess in sessions:
        num = sess["num"]
        files_str = sess["files_str"]

        index_files = []
        if files_str and files_str.strip() not in ("-", "", "\u2014"):
            index_files = [f.strip() for f in files_str.split(",") if f.strip()]

        body_files = _parse_body_files_table(lines, num)

        for idx_file in index_files:
            normalised = _normalise_filename(idx_file)
            if not normalised or not normalised.strip():
                continue
            # Skip prose descriptions that aren't filenames
            if not _looks_like_filename(normalised):
                continue
            if not any(_file_matches(normalised, bf) for bf in body_files):
                violations.append({
                    "session": num,
                    "type": "missing_from_body",
                    "summary_file": idx_file,
                    "detail": (
                        "Session " + str(num) + " index lists " + repr(idx_file)
                        + " but it is not found in body table. Body files: " + str(body_files)
                    ),
                })

    return violations


def assert_file_consistency():
    """Pre-flight gate: blocks on missing-from-body violations."""
    violations = find_violations()
    hard = [v for v in violations if v["type"] == "missing_from_body"]
    if not hard:
        return
    n = len(hard)
    lines_out = []
    for v in hard[:50]:
        lines_out.append("  Session " + str(v["session"]) + ": " + repr(v["summary_file"]) + " not found in body table")
    head = "\n".join(lines_out)
    tail = "\n  ... and " + str(n - 50) + " more" if n > 50 else ""
    raise AssertionError(
        "Continuity file-consistency gate: " + str(n) + " index file(s) missing from body table.\n"
        "Every index entry must appear in the body Files Changed table.\n\n"
        "Violations:\n" + head + tail
    )


def _summary():
    try:
        lines = _read_doc().splitlines()
    except FileNotFoundError as e:
        return (SKIP_EXIT, {"skipped": True, "reason": SKIP_REASON,
                            "continuity_path": str(CONTINUITY_PATH), "error": str(e)})

    # File consistency check
    sessions = _parse_index_table(lines)
    violations = find_violations(lines)
    hard = [v for v in violations if v["type"] == "missing_from_body"]

    per_session = []
    for sess in sessions:
        num = sess["num"]
        fs = sess["files_str"]
        if not fs or fs.strip() in ("-", ""):
            idx_count = 0
        else:
            idx_count = len([f for f in fs.split(",") if f.strip()])
        body_files = _parse_body_files_table(lines, num)
        sess_v = [v for v in violations if v["session"] == num]
        per_session.append({
            "session": num,
            "index_file_count": idx_count,
            "body_file_count": len(body_files),
            "violations": [
                {"type": v["type"], "summary_file": v["summary_file"]}
                for v in sess_v
            ],
        })

    # Appendix B consistency check
    appendix_b_violations = find_appendix_b_violations(lines)
    appendix_b_hard = [v for v in appendix_b_violations if v.get("severity") == "hard"]
    appendix_b_soft = [v for v in appendix_b_violations if v.get("severity") == "soft"]
    appendix_b_entries = _parse_appendix_b_entries(lines)

    any_violations = (len(hard) > 0 or len(appendix_b_hard) > 0)

    return (
        1 if any_violations else 0,
        {
            "continuity_path": str(CONTINUITY_PATH),
            "total_sessions": len(sessions),
            "total_violations": len(hard),
            "per_session": per_session,
            "appendix_b_total_entries": len(appendix_b_entries),
            "appendix_b_violations": len(appendix_b_violations),
            "appendix_b_hard_violations": len(appendix_b_hard),
            "appendix_b_soft_violations": len(appendix_b_soft),
            "appendix_b_unmatched": [
                {
                    "entry_num": v["entry_num"],
                    "date": v["date"],
                    "decision": v["decision"][:60],
                    "severity": v.get("severity", "unknown"),
                }
                for v in appendix_b_violations
            ],
        },
    )


# -- CLI --------------------------------------------------------------------

def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    verbose = "--verbose" in argv
    ec, payload = _summary()

    if "--json" in argv:
        print(json.dumps(payload, indent=2, default=str))
        return ec

    if payload.get("skipped"):
        print("CONTINUITY-FILE-GATE: SKIPPED (file absent: " + str(CONTINUITY_PATH) + ")")
        print("  " + payload["reason"])
        return ec

    ab_entries = payload.get("appendix_b_total_entries", 0)
    ab_violations = payload.get("appendix_b_violations", 0)

    if ec == 0:
        total = payload["total_sessions"]
        ab_msg = "; " + str(ab_entries) + " Appendix B entries, 0 unmatched" if ab_entries > 0 else "; no Appendix B"
        print("CONTINUITY-FILE-GATE: " + str(total) + " sessions" + ab_msg + " | 0 violations. Clean.")
        if verbose:
            for ps in payload["per_session"]:
                status = "OK" if not ps["violations"] else "VIOL"
                print(("  S" + str(ps["session"]).rjust(2)
                       + ": " + str(ps["index_file_count"]) + " idx -> "
                       + str(ps["body_file_count"]) + " body [" + status + "]"))
        return 0

    total_v = payload["total_violations"]
    print("CONTINUITY-FILE-GATE: build fails. " + str(total_v) + " file violation(s); "
          + str(ab_violations) + " Appendix B violation(s).\n")

    # File consistency violations
    for ps in payload["per_session"]:
        if not ps["violations"]:
            continue
        print("--- Session " + str(ps["session"])
              + " (" + str(ps["index_file_count"]) + " idx -> "
              + str(ps["body_file_count"]) + " body) ---")
        for v in ps["violations"]:
            print("  [" + v["type"] + "] " + repr(v["summary_file"]))
        print()

    # Appendix B violations
    if ab_violations:
        print("--- Appendix B unmatched entries ---")
        for ue in payload["appendix_b_unmatched"]:
            print("  Entry #" + str(ue["entry_num"]) + " (" + ue["date"] + "): " + ue["decision"])
        print()
        print("Fix: ensure every Appendix B entry has a corresponding **Decision N:**")
        print("entry in the matching session body. Add missing decision documentation.")
        print()

    if total_v > 0:
        print("Fix: ensure every filename in the Session Index 'Files Changed[^2]' column")
        print("appears in the corresponding session body's '### Files Changed' table.")
        print("The body table MUST list every file; the index is a comma-separated summary.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
