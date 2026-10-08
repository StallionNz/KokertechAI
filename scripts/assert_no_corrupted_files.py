#!/usr/bin/env python3
"""
assert_no_corrupted_files.py -- Sprint 17 R12-style CI gate for .pyc corruption recurrence.

## Purpose (2026-07-22 v2 refactor)

The 2026-07-22 incident wrote .pyc bytecode headers + null bytes onto 53 root .py
files (re-exporter-style attempt). Recovery restored 9 from backup + quarantined
44 for source reconstruction. This gate prevents recurrence + tracks the baseline.

## File-state model (validated by thinker 2026-07-22)

A `.py.corrupted` file at root is EITHER:
  - **forensic_copy**  -- a clean `.py` exists at root (restored from backup;
                         the `.corrupted` is the preserved original for analysis).
                         Expected; NOT counted against the quarantine baseline.
  - **real_quarantine** -- no clean `.py` at root; awaits source reconstruction.
                         MUST appear in `recovery_manifest.md` table rows.

## Four checks

1. discover_corrupted_files_in_root -- any root .py with >0 null bytes (HARD FAIL).
   (Original corruption signature: .pyc magic header + null padding prepended.)
2. validate_quarantine_baseline -- EXACT set match between real_quarantine and
   manifest table-row names (HARD FAIL on drift).
3. find_stale_pyc_for_restored -- any .pyc cache whose sibling .py is currently
   clean (WARNING only, narrow except (OSError, PermissionError)).
4. (v3 plan) check1_recursive_scan -- extend check1 to tests/, services/, tabs/,
   plugins/ subdirs; skipped for v2 to keep gate fast.

## Exit codes
- 0 = CLEAN
- 1 = corruption recurrence detected (check1 or check2 failure)
- 2 = setup error

## Cross-references

- recovery_manifest.md -- canonical quarantine triage table (44 rows).
- the known-failures log (2026-07-22 row) -- incident details + audit.
- Backups/workspace_backup_20260625_034000/ -- June 25 source backup.
- scripts/assert_singleton_reset_pattern.py -- sister R12 gate (same pattern).
- _runs/_run_full_gate.{bat,sh} -- pre-flight runner (this gate is the 5th link).
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NULL_BYTE_THRESHOLD = 1
MANIFEST_NAME = "recovery_manifest.md"


# ----------------------------------------------------------------------------
# Check 1: root .py files containing null bytes (corruption signature).
# ----------------------------------------------------------------------------
def discover_corrupted_files_in_root():
    """Check 1: any root .py file containing null bytes (corruption signature).

    Returns list of (relpath, null_count) for offenders.
    """
    offenders = []
    for f in sorted(PROJECT_ROOT.glob("*.py")):
        try:
            data = f.read_bytes()
        except OSError:
            # Narrow: only swallow filesystem errors; bugs surface.
            continue
        null_count = sum(1 for b in data if b == 0)
        if null_count >= NULL_BYTE_THRESHOLD:
            offenders.append((str(f.relative_to(PROJECT_ROOT)), null_count))
    return offenders


# ----------------------------------------------------------------------------
# Helper: classify .py.corrupted files into forensic vs real_quarantine.
# ----------------------------------------------------------------------------
def discover_quarantined_files():
    """Split .py.corrupted at root into forensic vs real_quarantine buckets.

    Forensic = a clean `.py` exists at root AND its content has zero null bytes
              (i.e. the source recovery succeeded; the .corrupted is the original).
    Real quarantine = NO clean `.py` at root OR the source is still corrupted.
    Returns (forensic, real_quarantine) as two sorted lists of basenames.
    """
    forensic = []
    real_quarantine = []
    for f in sorted(PROJECT_ROOT.glob("*.py.corrupted")):
        stem = f.name.replace(".py.corrupted", "")
        clean_source = PROJECT_ROOT / (stem + ".py")
        if clean_source.exists():
            try:
                data = clean_source.read_bytes()
            except (OSError, PermissionError):
                # Cannot read: treat as real_quarantine (gate must inspect).
                real_quarantine.append(stem)
                continue
            if sum(1 for b in data if b == 0) == 0:
                forensic.append(stem)
                continue
        real_quarantine.append(stem)
    return forensic, real_quarantine


# ----------------------------------------------------------------------------
# Helper: parse the manifest table for quarantined file names.
# ----------------------------------------------------------------------------
_MANIFEST_ROW_RE = re.compile(r"^\|\s*(\w+)\.py\s*\|", re.MULTILINE)


def extract_manifest_quarantine_names(manifest_path):
    """Parse recovery_manifest.md for table-row quarantined filenames.

    Returns a sorted set of basenames (without .py extension), or None if the
    manifest cannot be read; validate_quarantine_baseline() maps that to
    MISSING, and main() skips check2 on MISSING (file absent), while any
    manifest DRIFT still fails hard (per thinker 2026-07-22 decision;
    alternate locations not searched).

    **2026-07-23 fix:** Excludes the ``## FORENSIC COPIES`` section and
    everything after it — those are restored files, not quarantined.
    Previously the regex scanned the entire manifest and counted the 9
    forensic-copy table rows as quarantine rows, inflating the count from
    44 → 53 and triggering a spurious OFF_BY_ONE.
    """
    if not manifest_path.exists():
        return None  # -> MISSING; main() skips check2 when genuinely absent
    try:
        content = manifest_path.read_text(encoding="utf-8")
    except (OSError, PermissionError):
        return None
    # Stop scanning at FORENSIC COPIES — those rows are restored files.
    forensic_marker = content.find("\n## FORENSIC COPIES")
    if forensic_marker != -1:
        content = content[:forensic_marker]
    names = set(_MANIFEST_ROW_RE.findall(content))
    return names


# ----------------------------------------------------------------------------
# Helper: validate Summary bucket-claim consistency (tighten OFF_BYONE).
# ----------------------------------------------------------------------------
# Defensive checklist extension (2026-07-22): the OFF_BYONE row_count check
# only catches manifest-rows-vs-filesystem drift. A bucket-claim drift
# (e.g., Summary says Critical cores | **6** but the Critical Cores table
# only has 5 rows) is silent under the existing machinery. The patterns
# below extend the count-first pattern to:
#   (1) Summary bucket-claim counts (parsed from `## Summary` table)
#   (2) per-bucket Python-file row counts (parsed from `## <Name>` sections)
# Both must reconcile; any mismatch short-circuits with BUCKET_DRIFT.
_SUMMARY_CLAIM_RE = re.compile(
    r"^\|\s+([A-Za-z][\w\s/_.`]+?)\s+(?:\([^)]*\))?\s*\|\s+\*\*(\d+)\*\*\s+\|",
    re.MULTILINE,
)
_PY_FILE_ROW_RE = re.compile(r"^\|\s+([\w_]+\.py)\s+\|\s+\d+", re.MULTILINE)


def validate_bucket_count_consistency(manifest_text):
    """Tighten OFF_BYONE: assert Summary claims == per-bucket table row counts.

    Returns ``None`` on OK. On assertion failure, returns one of two distinct
    statuses (the user-reported gap in Sprint 19.7: conflating missing-Summary
    with bucket-drift hid the difference between "manifest lost its Summary"
    vs "manifest has bucket counts that disagree with tables"):

      * ``("SUMMARY_ABSENT", message, details)`` -- the ``## Summary`` H2
        block is missing entirely. Distinct from BUCKET_DRIFT because this
        represents an unparsable manifest; the cascade_summary classifier
        routes SUMMARY_ABSENT as ``FAIL (SUMMARY_ABSENT)``, separate from
        BUCKET_DRIFT's ``FAIL (BUCKET_DRIFT)``. The diagnostic tells the
        operator to ADD a Summary section, not to edit bucket counts.

      * ``("BUCKET_DRIFT", message, details)`` -- Summary is present, but
        bucket-claim counts disagree with per-bucket table row counts. The
        drift ``details`` is ``{"bucket_claim_drift": [{bucket, claimed,
        actual, delta}, ...]}`` -- mirrors the existing ``OFF_BY_ONE``
        payload shape so cascade_summary.py's classifier can route state
        uniformly. The diagnostic tells the operator to RECONCILE counts.

    Marker strings locked here (stable contracts per KNOWLEDGE.md §21):
      * Statuses: ``"SUMMARY_ABSENT"``, ``"BUCKET_DRIFT"``
      * User-facing label: ``"BUCKET-CLAIM DRIFT: ..."`` (BUCKET_DRIFT only)
      * User-facing label: ``"Summary section absent ..."`` (SUMMARY_ABSENT only)
      * Bucket-section anchors: ``"Critical Cores"``, ``"Services-Facing"``,
        ``"Tab-Heavy Utilities"`` -- the H2 headings of the per-bucket tables.
      * Summary table rows: parsed via ``_SUMMARY_CLAIM_RE`` above.

    Function-name anchor is ``validate_bucket_count_consistency``; verify
    line range via ``grep -n 'def validate_bucket_count_consistency' \
scripts/assert_no_corrupted_files.py``.
    """
    # Find the Summary section block. Per manifest structure, it lives between
    # `## Summary\n` and the next `## ` heading (or end of file).
    summary_m = re.search(r"## Summary\s*\n.*?(?=\n## |\Z)", manifest_text, re.DOTALL)
    if not summary_m:
        # Distinct status: a manifest without a Summary section is a
        # STRUCTURAL problem (different remediation than a bucket count
        # mismatch). The user-reported Sprint 19.7 gap was conflating these
        # two distinct conditions under one BUCKET_DRIFT label.
        return (
            "SUMMARY_ABSENT",
            "manifest Summary section absent at root; cannot validate "
            + "bucket consistency (add a `## Summary` table or this gate "
            + "cannot function).",
            {"bucket_claim_drift": []},
        )
    summary_block = summary_m.group(0)

    # Parse Summary bucket-claim counts. Known labels (per the manifest):
    #   * "Critical cores"          (top-level bucket)
    #   * "Services-facing"         (top-level bucket)
    #   * "Tab-heavy / utilities"   (top-level bucket)
    #   * "Without `.pyc` (...)"    (derived sub-bucket -- skipped; not asserted)
    #   * "_smoke_test.py (...)"    (separable P5 row)
    #   * "Total quarantined"       (derived sum -- skipped; computed from sum)
    known_bucket_labels = {
        "Critical cores",
        "Services-facing",
        "Tab-heavy / utilities",
        "_smoke_test.py",
    }
    # §21 contract: these labels are STABLE — drift-resistant anchors per
    # KNOWLEDGE.md §21 marker stability. A future contributor renaming a
    # label MUST update this set AND the Summary table AND the per-bucket
    # section heading in lockstep. Strict equality (vs substring) is
    # intentional — looser matching would silently skip misclassified
    # buckets, defeating the point of the count-first defensive check.
    claims = {}
    for m in _SUMMARY_CLAIM_RE.finditer(summary_block):
        label = m.group(1).strip()
        # Strip leading/trailing markdown decoration (e.g. `` ` ``).
        label = label.strip("`").strip()
        if label in known_bucket_labels:
            claims[label] = int(m.group(2))

    # Per-bucket Python-file row counts. _smoke_test.py is filtered out of the
    # Tab-Heavy count (Summary claims it as a separable P5 row).
    bucket_section_anchors = [
        ("Critical Cores", "Critical cores"),
        ("Services-Facing", "Services-facing"),
        ("Tab-Heavy Utilities", "Tab-heavy / utilities"),
    ]
    actual = {}
    for section_header, claim_label in bucket_section_anchors:
        section_m = re.search(
            r"## " + re.escape(section_header) + r"[^\n]*\n.*?(?=\n## |\Z)",
            manifest_text,
            re.DOTALL,
        )
        if not section_m:
            continue
        section = section_m.group(0)
        rows = _PY_FILE_ROW_RE.findall(section)
        # Filter _smoke_test.py (separable P5 row in Summary).
        rows = [r for r in rows if r != "_smoke_test.py"]
        actual[claim_label] = len(rows)

    # Count _smoke_test.py separately. It is listed inside the Tab-Heavy
    # Utilities section but treated as its own bucket by the Summary.
    smoke_m = re.search(r"\|\s+(_smoke_test\.py)\s+\|\s+\d+", manifest_text)
    actual["_smoke_test.py"] = 1 if smoke_m else 0

    # Compare. Any mismatch triggers BUCKET_DRIFT.
    drift = []
    for label, claimed in claims.items():
        actual_n = actual.get(label, 0)
        if actual_n != claimed:
            drift.append(
                {
                    "bucket": label,
                    "claimed": claimed,
                    "actual": actual_n,
                    "delta": actual_n - claimed,
                }
            )

    if drift:
        msg = (
            "BUCKET-CLAIM DRIFT: "
            + "; ".join(
                d["bucket"]
                + " claim="
                + str(d["claimed"])
                + " actual="
                + str(d["actual"])
                + " (delta="
                + str(d["delta"])
                + ")"
                for d in drift
            )
        )
        return ("BUCKET_DRIFT", msg, {"bucket_claim_drift": drift})
    return None


# ----------------------------------------------------------------------------
# Check 2: real_quarantine count must EXACTLY match manifest row names.
# ----------------------------------------------------------------------------
def validate_quarantine_baseline():
    """Check 2: parse manifest, demand exact-set match against real_quarantine.

    Returns a (status, message, details) tuple.
      - ("OK", message, details)
      - ("DRIFT", message, details)        -- sets diverge
      - ("MISSING", message, details)      -- manifest absent at root
      - ("BUCKET_DRIFT", ...)              -- manifest internal inconsistency
                                              (Summary claims != per-bucket
                                              row counts). New in Sprint 19.7
                                              -- tightens OFF_BYONE defensive
                                              checklist to also catch bucket-
                                              claim-vs-row-count drift.
      - ("SUMMARY_ABSENT", ...)           -- manifest is present but has no
                                              `## Summary` H2 section at all
                                              (Sprint 19.7 round-6 split).
                                              Distinct from BUCKET_DRIFT
                                              because the remediation is
                                              DIFFERENT: add a Summary
                                              section (rebuild), NOT
                                              reconcile counts (edit).
    """
    manifest_path = PROJECT_ROOT / MANIFEST_NAME
    forensic, real_quarantine = discover_quarantined_files()
    manifest_set = extract_manifest_quarantine_names(manifest_path)

    if manifest_set is None:
        details = {
            "forensic": forensic,
            "real_quarantine": real_quarantine,
            "manifest_names": [],
        }
        return (
            "MISSING",
            "recovery_manifest.md absent at root; cannot verify quarantine baseline",
            details,
        )

    real_set = set(real_quarantine)
    only_in_fs = sorted(real_set - manifest_set)
    only_in_manifest = sorted(manifest_set - real_set)
    details = {
        "forensic": forensic,
        "real_quarantine": real_quarantine,
        "manifest_names": sorted(manifest_set),
        "only_in_fs": only_in_fs,
        "only_in_manifest": only_in_manifest,
    }

    # Tightened OFF_BYONE check (Sprint 19.7): bucket-claim consistency first.
    # If the Summary's bucket-claim counts disagree with the actual table rows
    # in their respective `## <Name>` sections, short-circuit with BUCKET_DRIFT
    # before the row_count-vs-filesystem check runs. This catches the silent
    # drift category where the gate's row_count assertion would PASS while the
    # manifest is internally inconsistent (the user-reported gap: bucket-claim
    # 6 vs actual table rows 5).
    # NB: manifest_path was already read successfully by extract_manifest_
    # quarantine_names above; if that succeeded, this read succeeds too.
    manifest_text = manifest_path.read_text(encoding="utf-8", errors="replace")
    bucket_result = validate_bucket_count_consistency(manifest_text)
    if bucket_result is not None:
        bucket_status, bucket_msg, bucket_details = bucket_result
        # Merge bucket details into details dict so cascade_summary.py sees a
        # single unified payload keyed on the shared shape.
        for k, v in bucket_details.items():
            details[k] = v
        return (bucket_status, bucket_msg, details)

    # Defensive checklist (2026-07-22): row_count must equal len(quarantined_files).
    # Auto-detect off-by-one discrepancies (43 vs 44 incident) BEFORE doing set-equality
    # so the diagnostic surface is sharper than a generic DRIFT.
    if len(real_quarantine) != len(manifest_set):
        delta = len(real_quarantine) - len(manifest_set)
        msg = (
            "OFF-BY-ONE detected: row_count="
            + str(len(manifest_set))
            + " but len(quarantined_files)="
            + str(len(real_quarantine))
            + " (delta="
            + str(delta)
            + ")"
        )
        # Augment details with off-by-one surface for clearer diagnosis.
        details["off_by_one_delta"] = delta
        details["missing_rows_count"] = len(only_in_fs)
        details["extra_rows_count"] = len(only_in_manifest)
        return ("OFF_BY_ONE", msg, details)
    if only_in_fs or only_in_manifest:
        msg = (
            "quarantine count drift: real_quarantine="
            + str(len(real_quarantine))
            + " manifest_rows="
            + str(len(manifest_set))
        )
        return ("DRIFT", msg, details)

    msg = (
        "baseline matched: "
        + str(len(real_quarantine))
        + " quarantined + "
        + str(len(forensic))
        + " forensic copies"
    )
    return ("OK", msg, details)


# ----------------------------------------------------------------------------
# Check 3: stale .pyc cache whose sibling .py is clean (WARNING only).
# ----------------------------------------------------------------------------
def find_stale_pyc_for_restored():
    """Check 3: any __pycache__/*.pyc whose sibling root .py is currently clean.

    WARNING only -- stale cache does not fail the gate. Narrow except so real
    bugs surface (PermissionError, OSError only).
    """
    pycache = PROJECT_ROOT / "__pycache__"
    if not pycache.exists():
        return []
    offenders = []
    for pyc in sorted(pycache.glob("*.pyc")):
        stem = pyc.stem.split(".")[0]
        source = PROJECT_ROOT / (stem + ".py")
        if source.exists():
            try:
                data = source.read_bytes()
            except (OSError, PermissionError):
                continue
            if sum(1 for b in data if b == 0) == 0:
                offenders.append(
                    (str(pyc.relative_to(PROJECT_ROOT)),
                     str(source.relative_to(PROJECT_ROOT)))
                )
    return offenders


def _emit_details_block(details):
    """Print drift/missing diagnostic details (called from main())."""
    if "forensic" in details:
        for s in details["forensic"]:
            print("  [forensic] " + s + ".py.corrupted -- clean " + s + ".py restored (expected)")
    if "only_in_fs" in details:
        for s in details["only_in_fs"]:
            print("  [quarantine NOT in manifest] " + s + ".py.corrupted")
    if "only_in_manifest" in details:
        for s in details["only_in_manifest"]:
            print("  [in manifest, NOT in filesystem] " + s + ".py")


# ----------------------------------------------------------------------------
# Main: orchestrate the three checks + footer on failure.
# ----------------------------------------------------------------------------
def main():
    if not PROJECT_ROOT.exists():
        sys.stderr.write("[assert_no_corrupted_files] project_root NOT FOUND\n")
        return 2

    print("=== 2026-07-22 .pyc Corruption Recurrence Gate (v2) ===")
    print()
    failed = False
    check2_skipped = False

    # CHECK 1: root .py with null bytes
    corrupted = discover_corrupted_files_in_root()
    if corrupted:
        failed = True
        print("[check1_corrupted_files_in_root] FAILED (" + str(len(corrupted)) + " offenders):")
        for fn, nulls in corrupted:
            print("  " + str(fn) + ": " + str(nulls) + " null bytes")
        print()
    else:
        print("[check1_corrupted_files_in_root] CLEAN")

    # CHECK 2: quarantine baseline (manifest cross-check)
    status, message, details = validate_quarantine_baseline()
    if status == "BUCKET_DRIFT":
        failed = True
        print("[check2_quarantine_baseline] BUCKET-CLAIM DRIFT (manifest internal inconsistency)")
        print("  " + message)
        print("  details:")
        for d in details.get("bucket_claim_drift", []):
            print(
                "    "
                + d["bucket"]
                + ": claim="
                + str(d["claimed"])
                + " actual="
                + str(d["actual"])
                + " (delta="
                + str(d["delta"])
                + ")"
            )
        print("  This is the Sprint 19.7 tightened defensive checklist: Summary bucket-claim")
        print("  counts must reconcile with per-bucket table row counts.")
        print("  To resolve: edit the Summary table to match the per-bucket table row counts,")
        print("  OR add/remove rows in the per-bucket table to match the Summary claim.")
    elif status == "SUMMARY_ABSENT":
        # Distinct from BUCKET_DRIFT (Sprint 19.7 round-6 split): the manifest
        # has NO `## Summary` section at all. The remediation is to ADD a
        # Summary section (structural rebuild), NOT to reconcile counts.
        # Conflating these two statuses would have hidden the difference
        # between "manifest is unparsable" (SUMMARY_ABSENT) and "manifest
        # has internal inconsistencies" (BUCKET_DRIFT) in cascade_summary.
        failed = True
        print("[check2_quarantine_baseline] SUMMARY SECTION ABSENT (manifest unparsable)")
        print("  " + message)
        print("  details:")
        print("    This is structurally different from BUCKET-CLAIM DRIFT.")
        print("    A Summary section is missing or unrecognized -- the gate")
        print("    cannot enforce bucket-count consistency without it.")
        print("  To resolve: add a `## Summary` H2 section with a `| Bucket |")
        print("    Count | ...` table to recovery_manifest.md at root.")
    elif status == "OK":
        print("[check2_quarantine_baseline] CLEAN")
        print("  " + message)
        print("  baseline validated against recovery_manifest.md")
    elif status == "OFF_BY_ONE":
        failed = True
        print("[check2_quarantine_baseline] OFF-BY-ONE (defensive checklist fired)")
        print("  " + message)
        print("  details:")
        print("    row_count vs len(quarantined_files) auto-detected mismatch.")
        print("    This is the 2026-07-22 incident defensive checklist (assert row_count == len(quarantined_files)).")
        print("    To resolve: append missing rows to recovery_manifest.md OR rename extra .py files.")
        _emit_details_block(details)
    elif status == "DRIFT":
        failed = True
        print("[check2_quarantine_baseline] DRIFT")
        print("  " + message)
        print("  details:")
        _emit_details_block(details)
    elif status == "MISSING":
        # Skip-if-absent: recovery_manifest.md is part of the internal working
        # set (gitignored, not distributed with the public repo). A fresh clone
        # cannot verify the quarantine baseline, so this CHECK is skipped rather
        # than failed; check1 (root .py null-byte scan) still runs and enforces.
        check2_skipped = True
        print("[check2_quarantine_baseline] SKIPPED (recovery_manifest.md absent at root)")
        print("  " + message)
        print("  recovery_manifest.md is gitignored; run from the full working tree")
        print("  to enforce the quarantine baseline.")
    print()

    # CHECK 3: stale __pycache__/*.pyc (WARNING only)
    stale = find_stale_pyc_for_restored()
    if stale:
        print("[check3_stale_pyc_for_restored] WARN (" + str(len(stale)) + " entries -- clear __pycache__ to recompile):")
        for pyc, src in stale:
            print("  __pycache__/" + str(pyc) + " <-> " + str(src))
        print()
    else:
        print("[check3_stale_pyc_for_restored] CLEAN")
        print()

    # Summary
    if failed:
        print("=== FAILED: corruption recurrence or baseline drift detected. ===")
        print("    Recovery: see recovery_manifest.md for triage + next steps.")
        print("    Manual reconstruct: rename <f>.py.corrupted -> <f>.py and restore from backup.")
        print("    Decompile pycdc: python -m pycdc __pycache__/<f>.cpython-314.pyc (low success ~10-30%).")
        return 1

    if check2_skipped:
        print("=== SKIPPED: quarantine baseline unverifiable (recovery_manifest.md absent). ===")
        print("    check1 (root .py null-byte scan) ran and passed. Run from the full")
        print("    working tree to enforce the quarantine-baseline check.")
        return 3

    print("=== CLEAN: no corruption recurrence detected. ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
