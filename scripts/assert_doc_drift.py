"""DOC-DRIFT gate: retired myth strings, SSOT pointers, contract version sync.

Gate 8 (added 2026-09-24). Catches the drift class that P0/P1 of the doc
audit kept finding: retired claims resurrecting in living docs, invariant
restatements drifting from their single source of truth, and contract
version references disagreeing across files. The write_file myth survived
in FOUR documents for two months; this gate makes that class impossible
to reintroduce silently.

What it checks (in order):
  1. MYTH FAMILIES -- regex patterns for known-retired claims, scanned
     across the LIVING doc set (root *.md, docs/*.md top level, agent/*.md,
     .agents/skills/*/SKILL.md). Historical archives are excluded by
     design: Backups/, memory/, _scratch/, docs/{CHANGELOG,PROJECT_TIMELINE,
     HISTORY_*}.md, docs/{sessions,reports,notes,protocols,specs,stallion}/
     -- tombstone-preserved history is correct there; the gate's charter is
     evergreen guidance.
  2. Tombstone tolerance -- a myth-pattern match is NOT a violation when
     the match line (or +-2 lines) carries a retirement marker
     (RETIRED / disproven / debunked / had drifted / stub / collapsed /
     no longer applies / superseded / myth / dead lore). This is what
     allows the retirement notes themselves to quote the dead claim.
  3. SSOT pointers -- the agent contracts must reference AGENTS.md §3 as
     the invariant source (the P1 consolidation), and AGENTS.md must
     carry this gate's row (self-bootstrap assertion).
  4. Contract version sync -- every file that references a behavior
     contract version must agree on what that version IS. The v1.0-title
     vs v1.1-references drift this caught on its first live run is the
     exact failure class it exists for.

Exit codes: 0 = clean, 1 = drift detected, 2 = self-test failed.
--self-test runs the matcher against embedded cases and exits.
"""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- Myth families: name, regex, human note ---------------------------------
MYTH_FAMILIES = [
    ("write_file-cannot-create",
     r"write_file broken|cannot create new files|[Cc]annot create files|may fail in subdirectories",
     "write_file creates files + parent dirs (verified 2026-09-24); retired with evidence (2026-09-24)"),
    ("powershell-terminal",
     r"[Rr]uns? in PowerShell|PowerShell interprets backslashes",
     "the terminal is bash (Git Bash); fixed on 2026-09-24"),
    ("29-pattern-count",
     r"29 tool failure patterns|29-pattern|29 patterns",
     "the unverifiable count was replaced by a provenance line (P1, 2026-09-24)"),
    ("stale-test-counts",
     r"117 test files|900\+ tests passing",
     "suite is 138 files / ~3,600+ tests; run it instead of quoting frozen counts"),
    ("no-git-decision",
     r"Project uses no Git|no-Git choice is current",
     "local Git adopted 2026-09-22 (KNOWLEDGE.md §3 Decision 7)"),
]

TOMBSTONE_MARKERS = re.compile(
    r"retired|disproven|debunked|dead lore|myth|had drifted|still taught|"
    r"collapsed|redirect stub|no longer applies|superseded|tombstone|"
    r"was disproven|verified 2026-09-24",
    re.IGNORECASE,
)

# --- Scan scope: living docs only -------------------------------------------
EXCLUDED_DIR_PARTS = {
    "backups", "memory", "_scratch", "_runs", ".venv", ".git", "__pycache__",
    ".pytest_cache", ".ruff_cache", "sessions", "reports", "notes",
    "protocols", "specs", "stallion", "node_modules", "models", "data",
    "logs", "plugins", "tools", "subagents", ".agents",  # .agents handled separately below
}
EXCLUDED_FILE_NAMES = {
    "CHANGELOG.md",            # append-only dated history, by convention
    "PROJECT_TIMELINE.md",     # chronological history
    "recovery_manifest.md",    # dated recovery record
    "save_session.md",         # dated recovery record
    # Dated point-in-time reviews: their counts describe the codebase AS IT
    # WAS on their date -- "900+ tests passing" was true and load-bearing in
    # the 2026-07-21 review. Rewriting history to satisfy a present-tense
    # gate defeats the archive; exclusion (like CHANGELOG) is the correct
    # treatment. Same class as the tombstone tolerance, whole-file scope.
    "ARCHITECTURE_REVIEW_KOKERTECH.md",
    "kokertech_agent_architecture_review_2026_07_21.md",
    "kokertech_agent_architecture_review_2026_07_21.md".replace("_2026_07_21", ""),
    "sprint19_kokertech_controller_decomposition_plan_2026_07_21.md",
}


def living_doc_files():
    """Yield Path objects for every living doc the gate scans."""
    seen = set()
    candidates = []
    candidates.extend(PROJECT_ROOT.glob("*.md"))
    candidates.extend((PROJECT_ROOT / "docs").glob("*.md"))
    candidates.extend((PROJECT_ROOT / "agent").glob("*.md"))
    for skill in sorted((PROJECT_ROOT / ".agents" / "skills").glob("*")):
        candidates.extend(skill.glob("SKILL.md"))
    for p in candidates:
        rp = p.relative_to(PROJECT_ROOT)
        if p.name in EXCLUDED_FILE_NAMES:
            continue
        if any(part.lower() in EXCLUDED_DIR_PARTS for part in rp.parts[:-1]):
            continue
        key = str(rp).lower()
        if key not in seen:
            seen.add(key)
            yield p


def is_tombstoned(lines, idx):
    """True if line idx (0-based) or +-2 neighbors carries a retirement marker."""
    lo = max(0, idx - 2)
    hi = min(len(lines), idx + 3)
    return any(TOMBSTONE_MARKERS.search(lines[i]) for i in range(lo, hi))


def scan_myths(text):
    """Return list of (family, line_no, line_snippet) violations for one file."""
    lines = text.splitlines()
    violations = []
    for family_name, pattern, _note in MYTH_FAMILIES:
        rx = re.compile(pattern)
        for i, line in enumerate(lines):
            if rx.search(line) and not is_tombstoned(lines, i):
                violations.append((family_name, i + 1, line.strip()[:110]))
    return violations


# --- SSOT pointer assertions -------------------------------------------------
SSOT_REQUIREMENTS = [
    ("agent/Buffy.agent.md", "Single source of truth: AGENTS.md §3",
     "Buffy v1.1 consolidation (P1)"),
    ("agent/Kokertech.agent.md", "AGENTS.md §3",
     "Kokertech v4.2 consolidation (P1)"),
    ("AGENTS.md", "| **Gate 8** | `python scripts/assert_doc_drift.py`",
     "this gate's own registration row"),
]

CONTRACT_VERSION_RX = re.compile(r"Contract v(\d+\.\d+)")


def contract_versions(text, which):
    """Extract referenced versions for 'kokertech' or 'buffy' from text."""
    found = set()
    for line in text.splitlines():
        m = CONTRACT_VERSION_RX.search(line)
        if not m:
            continue
        low = line.lower()
        if which == "buffy" and ("buffy" in low or "strategic" in low):
            found.add(m.group(1))
        elif which == "kokertech" and ("kokertech" in low or "god reviewer" in low):
            found.add(m.group(1))
    return found


VERSION_REFERENCE_FILES = [
    "agent/Kokertech.agent.md", "agent/Buffy.agent.md",
    "AGENTS.md", "SOUL.md", "IDENTITY.md",
]


def check_ssot_and_versions():
    """Return list of violation strings for SSOT pointers + version sync."""
    violations = []
    for rel, needle, why in SSOT_REQUIREMENTS:
        p = PROJECT_ROOT / rel
        if p.exists() and needle not in p.read_text(encoding="utf-8", errors="replace"):
            violations.append(f"SSOT pointer missing in {rel}: expected \"{needle}\" ({why})")

    for which in ("kokertech", "buffy"):
        seen = {}  # version -> [files]
        for rel in VERSION_REFERENCE_FILES:
            p = PROJECT_ROOT / rel
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for v in contract_versions(text, which):
                seen.setdefault(v, []).append(rel)
        if not seen:
            violations.append(f"version sync: no \"{which}\" contract version references found at all")
            continue
        if len(seen) > 1:
            detail = "; ".join(f"v{v} in {', '.join(fs)}" for v, fs in sorted(seen.items()))
            violations.append(f"version sync: {which} contract referenced as multiple versions -> {detail}")
    return violations


# --- Self-test ----------------------------------------------------------------
SELFTEST_CASES = [
    # (text, expect_violations)
    ("Use write_to_file; note write_file cannot create new files.\n", 1),
    ("**RETIRED:** the old \"write_file cannot create files\" claim was disproven.\n", 0),
    ("The claim write_file cannot create new files was disproven 2026-09-24\nby three sessions; see §9.1.\n", 0),
    ("Commands run in PowerShell on Windows.\n", 1),
    ("Runs in **bash (Git Bash)** on Windows -- never PowerShell syntax.\n", 0),
    ("A canonical reference with 29 tool failure patterns lives here.\n", 1),
    ("The suite is 117 test files strong.\n", 1),
    ("Project uses no Git. History from mtimes.\n", 1),
    ("(fork collapsed 2026-09-24; it had drifted -- still taught \"no Git\")\n", 0),
]


def run_selftest():
    failures = []
    for text, expected in SELFTEST_CASES:
        got = len(scan_myths(text))
        if got != expected:
            failures.append(f"expected {expected} violations, got {got}: {text[:60]!r}")
    if failures:
        print("DOC-DRIFT-SELFTEST: FAIL")
        for f in failures:
            print("  " + f)
        return 2
    print(f"DOC-DRIFT-SELFTEST: OK ({len(SELFTEST_CASES)} cases: detection + tombstone tolerance)")
    return 0


# --- Main ---------------------------------------------------------------------
def main(argv):
    if "--self-test" in argv:
        return run_selftest()

    violations = []
    scanned = 0
    for p in living_doc_files():
        scanned += 1
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = p.relative_to(PROJECT_ROOT).as_posix()
        for family, line_no, snippet in scan_myths(text):
            violations.append(f"{rel}:{line_no}: [{family}] {snippet}")
    violations.extend(check_ssot_and_versions())

    if violations:
        print(f"DOC-DRIFT-GATE: FAIL -- {len(violations)} violation(s) across {scanned} living docs")
        for v in violations:
            print("  " + v)
        print("  Fix the doc, or (for intentional tombstones) add a retirement marker")
        print("  (RETIRED / disproven / had drifted / stub) within +-2 lines of the claim.")
        return 1
    print(f"DOC-DRIFT-GATE: {scanned} living docs scanned | 0 retired-myth violations | SSOT pointers OK | contract versions in sync. Clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
