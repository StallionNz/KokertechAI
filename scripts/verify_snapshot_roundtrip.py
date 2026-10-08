#!/usr/bin/env python3
# scripts/verify_snapshot_roundtrip.py
#
# Verify that a Backups/session_chats_<ts>/snapshot round-trips intact:
#   1. Parse <snapshot>/manifest.txt (path | size | sha256) -- same format
#      as restore_session_chats_backup.py -- via the shared parse_manifest.
#   2. For every manifest row, COPY the snapshot file into a scratch dir
#      (the same relative path the restore helper writes to under --apply).
#   3. Re-sha256 every scratch copy and compare to the manifest sha256.
#   4. Print "ROW-MISMATCH count: N" so nightly / pre-commit gates can grep it.
#
# Exit codes:
#   0   no drift
#   1   any row mismatched / missing / sha-drift detected
#   2   pre-flight error (manifest missing, scratch dir not empty, etc.)
#
# DRY: ships zero manifest-parser / sha256 code of its own; imports the
# restore helper's parse_manifest + sha256_of + _atomic_copy + _is_sqlite
# so the verifier parses the exact same column layout AND uses the exact
# same SQLite write path (fsync + os.replace) as the production restore.
"""
verify_snapshot_roundtrip - sha256-roundtrip verifier for session_chats_ snapshots.
"""
from __future__ import annotations

import argparse
import datetime
import shutil
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
# Re-import the production parsers + atomic-copy so the verifier cannot drift
# from what restore_session_chats_backup.py actually accepts / writes.  Any
# future column-layout change OR atomic-copy fix only needs to ripple through
# one file.
from restore_session_chats_backup import (
    parse_manifest,
    sha256_of,
    is_path_safe,
    PARTIAL_HASH_CHARS,
    _atomic_copy,    # borrowed so SQLite writes (e.g. kokertech_vault.db)
                     # mirror the production fsync + os.replace path (HIGH #2)
    _is_sqlite,      # borrowed so SQLite detection matches production
)


# --------------------------------------------------------------------------
# Module-level constants + helper for the --latest mode (pre-flight gate).
# Duplicated from scripts/cron_nightly_dr_smoke.py (same single source of
# truth kept in sync via the cross-references in docs/KNOWLEDGE.md §15).
# Future refactor: move both to scripts/restore_session_chats_backup.py and
# re-import. Tracked as a follow-up; duplication is contained to ~15 lines
# and well-tested via the gate integration.
# --------------------------------------------------------------------------
PROJECT_ROOT = Path(r"C:\KokertechAI")
BACKUPS_DIR = PROJECT_ROOT / "Backups"


def find_latest_snapshot():
    """Return the newest Backups/session_chats_*/manifest.txt path, or None.

    Returns None if Backups/ does not exist (fresh install) or contains no
    session_chats_ snapshots. NEVER raises. Used by --latest mode so the
    pre-flight gate does not depend on a hard-coded snapshot folder name.
    """
    try:
        children = list(BACKUPS_DIR.iterdir())
    except FileNotFoundError:
        return None
    candidates = []
    for child in children:
        if not child.is_dir():
            continue
        if not child.name.startswith("session_chats_"):
            continue
        manifest = child / "manifest.txt"
        if manifest.is_file():
            candidates.append((child.name, manifest))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


# --------------------------------------------------------------------------
# Drift accumulator
# --------------------------------------------------------------------------
class Drift:
    OK = "OK"
    MISMATCH = "ROW-MISMATCH"
    MISSING = "SOURCE-MISSING"
    UNSAFE = "PATH-UNSAFE"
    SCRATCH_WRITE_FAIL = "SCRATCH-WRITE-FAIL"

    def __init__(self):
        # list of (rel, status, manifest_sha, actual_sha_or_msg)
        self.rows = []
        # (rel, manifest_size, actual_size)
        self.size_mismatch = []

    def add(self, rel, status, manifest_sha, actual):
        self.rows.append((rel, status, manifest_sha, actual))

    def summary_line(self):
        # Dedupe via distinct rel keys: a single file with BOTH a sha mismatch
        # AND a size mismatch gets ONE count in the gate (not two).
        # Reviewer-caught MEDIUM #5 (double-count fix).
        distinct = set()
        for rel, status, _manifest_sha, _actual in self.rows:
            if status != self.OK:
                distinct.add(rel)
        for rel, _manifest_size, _actual_size in self.size_mismatch:
            distinct.add(rel)
        return "ROW-MISMATCH count: " + str(len(distinct))


# --------------------------------------------------------------------------
# In-place mode: re-hash the snapshot file itself (no copy).  Cheaper; useful
# as a fast smoke check before doing the full copy-into-scratch roundtrip.
# --------------------------------------------------------------------------
def verify_in_place(backup_root, rows):
    drift = Drift()
    for rel, size, manifest_sha in rows:
        # Symmetric path-traversal guard.
        if not is_path_safe(rel, backup_root):
            drift.add(rel, Drift.UNSAFE, manifest_sha,
                      "path escapes backup_root (" + str(backup_root) + ")")
            continue
        src = backup_root / rel
        if not src.exists():
            drift.add(rel, Drift.MISSING, manifest_sha,
                      "snapshot file missing: " + str(src))
            continue
        actual_sha = sha256_of(src)
        actual_size = src.stat().st_size
        if actual_sha == manifest_sha and actual_size == size:
            drift.add(rel, Drift.OK, manifest_sha, actual_sha)
        else:
            drift.add(rel, Drift.MISMATCH, manifest_sha, actual_sha)
            if actual_size != size:
                drift.size_mismatch.append((rel, size, actual_size))
    return drift


# --------------------------------------------------------------------------
# Roundtrip mode: copy each snapshot file into scratch_dir/<rel> (mirrors
# the restore helper's --apply destination write), then re-sha256 the copy.
# This is the "fresh --apply restore to a scratch dir" the user asked for:
# if the copy itself (or anything along the write/fsync/os.replace path)
# were broken, this surface would catch it.  Uses _atomic_copy so the
# SQLite write path is identical to production restore (HIGH #2).
# --------------------------------------------------------------------------
def verify_roundtrip(backup_root, scratch_dir, rows):
    drift = Drift()
    for rel, size, manifest_sha in rows:
        if not is_path_safe(rel, scratch_dir):
            drift.add(rel, Drift.UNSAFE, manifest_sha,
                      "path escapes scratch_dir (" + str(scratch_dir) + ")")
            continue
        # Symmetric guard: also refuse to read paths that escape backup_root
        # (MEDIUM #3).  Defense in depth against a hostile manifest.
        if not is_path_safe(rel, backup_root):
            drift.add(rel, Drift.UNSAFE, manifest_sha,
                      "path escapes backup_root (" + str(backup_root) + ")")
            continue
        src = backup_root / rel
        if not src.exists():
            drift.add(rel, Drift.MISSING, manifest_sha,
                      "snapshot file missing: " + str(src))
            continue
        actual_size = src.stat().st_size
        # Copy first (mirroring the --apply write), then re-hash the COPY.
        # Use _atomic_copy from the restore helper so SQLite files
        # (e.g. kokertech_vault.db) exercise the same fsync + os.replace
        # write path that production restore uses -- otherwise a regression
        # in _atomic_copy would silently slip past this verifier.
        tgt = scratch_dir / rel
        try:
            tgt.parent.mkdir(parents=True, exist_ok=True)
            _atomic_copy(src, tgt)
        except Exception as e:
            drift.add(rel, Drift.SCRATCH_WRITE_FAIL, manifest_sha, str(e))
            continue
        try:
            actual_sha = sha256_of(tgt)
        except Exception as e:
            drift.add(rel, Drift.SCRATCH_WRITE_FAIL, manifest_sha,
                      "re-hash failed: " + str(e))
            continue
        if actual_sha == manifest_sha and actual_size == size:
            drift.add(rel, Drift.OK, manifest_sha, actual_sha)
        else:
            drift.add(rel, Drift.MISMATCH, manifest_sha, actual_sha)
            if actual_size != size:
                drift.size_mismatch.append((rel, size, actual_size))
    return drift


# --------------------------------------------------------------------------
# Pretty-print + categorized table.  Print full error text when status != OK
# so triage does not have to dig for the truncated tail (MEDIUM #4).
# --------------------------------------------------------------------------
def render_verification(drift):
    by_status = {}
    for rel, status, manifest_sha, actual in drift.rows:
        by_status.setdefault(status, []).append((rel, manifest_sha, actual))

    print()
    print("Verification breakdown:")
    for status in (Drift.OK, Drift.MISMATCH, Drift.MISSING,
                   Drift.UNSAFE, Drift.SCRATCH_WRITE_FAIL):
        bucket = by_status.get(status, [])
        if not bucket:
            continue
        print("  [" + status + "]  " + str(len(bucket)))
        for rel, manifest_sha, actual in bucket:
            if status == Drift.OK:
                preview = (actual or "")[:PARTIAL_HASH_CHARS]
                print("    " + rel + "  (manifest=" + manifest_sha[:PARTIAL_HASH_CHARS] +
                      "..  actual=" + preview + "..)")
            else:
                print("    " + rel + "  (manifest=" + manifest_sha[:PARTIAL_HASH_CHARS] +
                      "..  actual/rescue=" + str(actual) + ")")

    if drift.size_mismatch:
        print()
        print("  [SIZE-DRIFT]  " + str(len(drift.size_mismatch)))
        for rel, msize, asize in drift.size_mismatch:
            print("    " + rel + "  (manifest_size=" + str(msize) +
                  "  actual_size=" + str(asize) + ")")


# --------------------------------------------------------------------------
# Argparser
# --------------------------------------------------------------------------
def build_argparser():
    PROJECT_ROOT = Path(r"C:\KokertechAI")
    DEFAULT_MANIFEST = (PROJECT_ROOT / "Backups" /
                        "session_chats_20260628_094250" / "manifest.txt")

    p = argparse.ArgumentParser(
        prog="verify_snapshot_roundtrip",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "manifest", type=Path, nargs="?", default=DEFAULT_MANIFEST,
        help="Path to manifest.txt (default: " + str(DEFAULT_MANIFEST) + ").",
    )
    p.add_argument(
        "--latest", action="store_true",
        help="Auto-resolve to the latest Backups/session_chats_*/manifest.txt. "
             "For pre-flight gate use; wins over a positional manifest. Exits 0 "
             "cleanly with 'ROW-MISMATCH count: 0  (no snapshots to verify, --latest)' "
             "when no snapshot exists (fresh workspace).",
    )
    p.add_argument(
        "--scratch-dir", type=Path, default=None,
        help="Scratch dir to write copies into.  Auto-generated under "
             "Backups/.verify_scratch_<UTC>/ if omitted.",
    )
    p.add_argument(
        "--keep-scratch", action="store_true",
        help="Do NOT delete the scratch dir after verification (default: delete).",
    )
    p.add_argument(
        "--in-place", action="store_true",
        help="Skip the copy-to-scratch step and re-hash snapshot files in "
             "place.  Faster smoke check, but does not exercise the write path.",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print per-row OK output (default: only print failing rows).",
    )
    return p


# --------------------------------------------------------------------------
# main() + entrypoint
# --------------------------------------------------------------------------
def main(argv=None):
    ap = build_argparser()
    args = ap.parse_args(argv)

    # --latest mode: auto-resolve to the newest snapshot manifest. If none exist
    # (fresh workspace, pre-snapshot), exit 0 cleanly with the gate-grepable
    # 'ROW-MISMATCH count: 0' line so the pre-flight gate does not false-alarm
    # on a developer who has not yet taken a snapshot.
    if args.latest:
        latest = find_latest_snapshot()
        if latest is None:
            print("VERIFIER: no manifest found (looked under %s). Skipping gate (--latest)." % BACKUPS_DIR)
            print()
            print("ROW-MISMATCH count: 0  (no snapshots to verify, --latest)")
            return 0
        args.manifest = latest

    if not args.manifest.exists():
        print("ERROR: manifest not found: " + str(args.manifest), file=sys.stderr)
        return 2
    manifest_text = args.manifest.read_text(encoding="utf-8")
    rows, skipped = parse_manifest(manifest_text)
    total = len(rows) + skipped
    parse_summary = str(len(rows)) + " of " + str(total) + " data lines"
    if skipped:
        parse_summary += " (" + str(skipped) + " malformed line(s) skipped)"

    # HIGH #1: refuse to claim "clean" if the manifest produced ZERO parseable
    # rows.  A future column-layout break must NOT silently pass the gate --
    # an empty inventory can never be meaningfully diffed, so a "clean"
    # verdict would be a false positive.  Pre-flight error = exit 2.
    if not rows:
        print("ERROR: manifest produced 0 parseable rows out of " + str(total) +
              " data lines: " + str(args.manifest), file=sys.stderr)
        print("       (" + str(skipped) + " malformed line(s) skipped) -- refusing",
              file=sys.stderr)
        print("       to declare the snapshot clean against an empty inventory.",
              file=sys.stderr)
        return 2

    backup_root = args.manifest.parent.resolve(strict=False)
    print("Manifest:        " + str(args.manifest))
    print("Backup root:     " + str(backup_root))
    print("Manifest parsed: " + parse_summary)

    if args.in_place:
        print("Mode:            IN-PLACE (snapshot files re-hashed; no scratch copy)")
        drift = verify_in_place(backup_root, rows)
    else:
        scratch_dir = (args.scratch_dir or
                       (backup_root.parent /
                        (".verify_scratch_" +
                         datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))))
        scratch_dir = scratch_dir.resolve(strict=False)
        if scratch_dir.exists():
            print("ERROR: scratch dir already exists: " + str(scratch_dir),
                  file=sys.stderr)
            print("       use a fresh --scratch-dir path or delete it manually.",
                  file=sys.stderr)
            return 2
        scratch_dir.mkdir(parents=True, exist_ok=False)
        print("Mode:            COPY-THEN-HASH (fresh --apply restore to scratch)")
        print("Scratch dir:     " + str(scratch_dir))
        try:
            drift = verify_roundtrip(backup_root, scratch_dir, rows)
        finally:
            if not args.keep_scratch:
                try:
                    shutil.rmtree(str(scratch_dir), ignore_errors=True)
                    if args.verbose:
                        print("Cleaned up scratch dir: " + str(scratch_dir))
                except Exception as e:
                    print("WARN: failed to remove scratch dir " + str(scratch_dir) +
                          ": " + str(e), file=sys.stderr)
            else:
                print("Scratch dir kept at: " + str(scratch_dir) +
                      " (use --keep-scratch=False to delete)")

    if args.verbose:
        ok_rows = [r for r in drift.rows if r[1] == Drift.OK]
        print()
        print("OK rows (" + str(len(ok_rows)) + "):")
        for rel, _status, manifest_sha, actual in ok_rows:
            print("    " + rel + "  sha=" + manifest_sha[:PARTIAL_HASH_CHARS] + "..")

    render_verification(drift)
    print()
    print(drift.summary_line())

    bad = [r for r in drift.rows if r[1] != Drift.OK]
    if bad or drift.size_mismatch:
        print("RESULT: drift detected -- restoring this snapshot would NOT round-trip.")
        return 1
    print("RESULT: snapshot round-trips clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
