#!/usr/bin/env python3
# scripts/restore_session_chats_backup.py
#
# Restore KokertechAI session/chat state from a Backups/session_chats_<timestamp>/
# snapshot. Parses the manifest.txt (path | size | sha256) emitted by the backup
# helper, re-hashes every source file, and (under --apply) copies each relative
# path back into PROJECT_ROOT (or --target).
#
# DEFAULTS TO DRY-RUN. Pass --apply to actually copy. Pass --yes to skip the
# confirmation prompt. Default-excludes: manifest.txt (self), *.bak / *.bak_*.
"""
restore_session_chats_backup - CLI for restoring from a session_chats_<ts>/manifest.txt.
"""
from __future__ import annotations

import argparse
import datetime
import fnmatch
import hashlib
import os
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(r"C:\KokertechAI")
DEFAULT_EXCLUDES = ("manifest.txt", "*.bak", "*.bak_*", "*.pre_restore*")
PARTIAL_HASH_CHARS = 12
CHUNK_SIZE = 65536


# --------------------------------------------------------------------------
# Manifest parser: identify columns by content not position (robust to filenames
# with spaces, e.g. "Freebuff session 1 2026_06_01 16_58_17.md").
# --------------------------------------------------------------------------
def parse_manifest(text):
    rows = []
    skipped = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            skipped += 1
            continue
        sha = parts[-1].lower()
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            skipped += 1
            continue
        try:
            size = int(parts[-2])
        except ValueError:
            skipped += 1
            continue
        path = " ".join(parts[:-2])
        rows.append((path, size, sha))
    return rows, skipped


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def sha256_of(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()

def is_path_safe(rel, root):
    """Refuse if rel resolves outside root (path-traversal guard)."""
    try:
        abs_target = (root / rel).resolve(strict=False)
        abs_target.relative_to(root.resolve(strict=False))
        return True
    except (ValueError, OSError):
        return False

def matches_any(name, patterns):
    name = name.replace("\\", "/")
    return any(fnmatch.fnmatch(name, p.replace("\\", "/")) for p in patterns)

def short_hash(sha):
    return sha[:PARTIAL_HASH_CHARS]


# Timestamped backup-of-current path (Fix 1: collision-safe naming).
def _pre_restore_path(tgt):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return tgt.with_name(tgt.name + ".pre_restore_" + ts)


# Atomic copy helper (Fix 2: .tmp + fsync + os.replace for SQLite files).
def _is_sqlite(name):
    return name.lower().endswith((".db", ".sqlite", ".sqlite3"))


def _atomic_copy(src, tgt):
    if _is_sqlite(tgt.name):
        tmp = tgt.with_name(tgt.name + ".tmp")
        with open(str(src), "rb") as fr:
            with open(str(tmp), "wb") as fw:
                shutil.copyfileobj(fr, fw, 1024 * 1024)
                fw.flush()
                os.fsync(fw.fileno())
        os.replace(str(tmp), str(tgt))
    else:
        shutil.copy2(str(src), str(tgt))


# --------------------------------------------------------------------------
# Plan accumulator + builder (per-file classification)
# --------------------------------------------------------------------------
class Plan:
    def __init__(self):
        self.do = []           # (rel, size, sha, op, do_backup)
        self.excluded = []     # (rel, reason)
        self.filtered = []
        self.unsafe = []
        self.bad_source = []
        self.exists = []
        self.warnings = []

    def add_warn(self, msg):
        self.warnings.append(msg)


def build_plan(rows, backup_root, target_root, excludes, only, force, backup_current):
    plan = Plan()

    db_files = [r[0] for r in rows if r[0].lower().endswith((".db", ".sqlite", ".sqlite3"))]
    if db_files:
        plan.add_warn("SQLite database(s) in plan: " + ", ".join(db_files) +
            " - close the dashboard / web / CLI before restoring;")
        plan.add_warn("  writing to a locked DB can corrupt it.")
    md_files = [r[0] for r in rows if r[0].lower().endswith(".md") and not r[0].startswith("manifest")]
    if md_files:
        plan.add_warn("MD note(s) in plan: " + ", ".join(md_files) +
            " - the restore overwrites newer manual edits.")
        plan.add_warn("  --backup-current is auto-applied to .md so current version is saved to <name>.pre_restore.")

    for rel, size, sha in rows:
        # 1. PATH SAFETY first (security - reject before any I/O).
        if not is_path_safe(rel, target_root):
            plan.unsafe.append((rel, "escapes target: " + str(target_root)))
            continue
        # 2. EXCLUDES.
        if matches_any(rel, excludes) or matches_any(os.path.basename(rel), excludes):
            plan.excluded.append((rel, "excluded (manifest.txt / *.bak* / --exclude)"))
            continue
        # 3. ONLY-FILTER.
        if only and not matches_any(rel, only):
            plan.filtered.append((rel, "not in --only filters"))
            continue
        # 4. SOURCE EXISTS + SHA256 MATCH.
        src = backup_root / rel
        if not src.exists():
            plan.bad_source.append((rel, "source missing: " + str(src)))
            continue
        actual_sha = sha256_of(src)
        if actual_sha != sha:
            plan.bad_source.append(
                (rel, "sha mismatch: manifest=" + short_hash(sha) + ".. actual=" + short_hash(actual_sha) + ".."))
            continue
        # 5. TARGET COLLISION.
        tgt = target_root / rel
        is_md = rel.lower().endswith(".md")
        if tgt.exists():
            if force:
                plan.do.append((rel, size, sha, "OVERWRITE (--force)", backup_current))
                continue
            if backup_current or is_md:
                plan.do.append((rel, size, sha, "OVERWRITE (saving current to .pre_restore)", True))
                continue
            plan.exists.append(
                (rel, "target exists: " + str(tgt) + "  (use --force or --backup-current)"))
            continue
        plan.do.append((rel, size, sha, "CREATE", False))
    return plan

# --------------------------------------------------------------------------
# Plan printer + apply (real writes)
# --------------------------------------------------------------------------
def _row_table(rows, label):
    if not rows:
        return
    print("--- " + label + " (" + str(len(rows)) + ") ---")
    for r in rows:
        print("  " + "  |  ".join(str(x) for x in r))

def render_plan(plan, backup_root, target_root):
    print()
    print("Backup root: " + str(backup_root))
    print("Target root: " + str(target_root))
    print()
    print("=== WARNINGS (" + str(len(plan.warnings)) + ") ===")
    for w in plan.warnings:
        print("  ! " + w)
    print()
    print("=== WILL RESTORE (" + str(len(plan.do)) + ") ===")
    for rel, size, sha, op, _backup in plan.do:
        print("  [" + ("{:<48s}".format(op)) + "]  " + rel + "  (" + str(size) + " B  sha=" + short_hash(sha) + "..)")
    print()
    skipped_n = len(plan.excluded) + len(plan.filtered) + len(plan.unsafe) + len(plan.bad_source) + len(plan.exists)
    print("=== SKIPPED (" + str(skipped_n) + ") ===")
    _row_table(plan.excluded, "EXCLUDED")
    _row_table(plan.filtered, "FILTER")
    _row_table(plan.unsafe, "UNSAFE (path-traversal)")
    _row_table(plan.bad_source, "BAD SOURCE (missing or sha mismatch)")
    _row_table(plan.exists, "TARGET EXISTS (refused - use --force or --backup-current)")
    print()

def apply_plan(plan, backup_root, target_root, verbose, apply_hash_verify=True):
    # failures = list of (rel, err, rescue_path_or_None)
    failures = []
    for rel, size, sha, _op, do_backup in plan.do:
        src = backup_root / rel
        tgt = target_root / rel
        rescue_path = None
        # TOCTOU guard: a file that existed at plan-time may be gone by apply-time.
        if not src.exists():
            failures.append((rel, "source disappeared since plan-time (" + str(src) + ")", None))
            if verbose:
                print("  FAIL " + rel + ": source disappeared since plan-time")
            continue
        try:
            # Fix 5: re-hash src at apply (TOCTOU mitigation; --skip-apply-hash disables).
            if apply_hash_verify:
                actual = sha256_of(src)
                if actual != sha:
                    failures.append((rel, "source sha changed since plan-time (manifest=" + short_hash(sha) + ".. actual=" + short_hash(actual) + "..)", None))
                    if verbose:
                        print("  FAIL " + rel + ": source sha changed since plan-time")
                    continue
            # Fix 1: timestamped backup of current target.
            if do_backup and tgt.exists():
                rescue_path = _pre_restore_path(tgt)
                shutil.copy2(str(tgt), str(rescue_path))
            tgt.parent.mkdir(parents=True, exist_ok=True)
            # Fix 2: atomic copy for SQLite files, regular copy2 otherwise.
            _atomic_copy(src, tgt)
            if verbose:
                print("  OK   " + rel)
        except Exception as e:
            failures.append((rel, str(e), str(rescue_path) if rescue_path else None))
            if verbose:
                print("  FAIL " + rel + ": " + str(e))
    if failures:
        print()
        print("=== FAILURES (" + str(len(failures)) + ") ===")
        for rel, err, rescue in failures:
            print("  FAIL " + rel + ": " + err)
            if rescue:
                print("       current version preserved at: " + rescue)
        return 1
    print()
    print("RESTORE COMPLETE. " + str(len(plan.do)) + " files copied into " + str(target_root) + ".")
    return 0


# --------------------------------------------------------------------------
# Argparser
# --------------------------------------------------------------------------
def build_argparser():
    p = argparse.ArgumentParser(
        prog="restore_session_chats_backup",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("manifest", type=Path, help="Path to manifest.txt.")
    p.add_argument("--apply", action="store_true", help="Actually copy files (default: dry-run).")
    p.add_argument("--yes", "-y", action="store_true", help="Skip the confirm prompt on --apply.")
    p.add_argument("--target", type=Path, default=PROJECT_ROOT,
        help="Root to restore into (default: " + str(PROJECT_ROOT) + ").")
    p.add_argument("--only", action="append", default=[], metavar="GLOB",
        help="Only include paths matching this glob (repeatable).")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB",
        help="Exclude paths matching this glob in addition to defaults (repeatable).")
    p.add_argument("--force", action="store_true",
        help="Overwrite existing target files without saving the current.")
    p.add_argument("--backup-current", action="store_true",
        help="Before overwriting, copy current target to <name>.pre_restore. Auto-applied to .md files.")
    p.add_argument("--verbose", "-v", action="store_true",
        help="Per-file OK/FAIL output during --apply.")
    p.add_argument("--skip-apply-hash", action="store_true",
        help="Disable re-hashing of source files at apply-time (faster but skips TOCTOU check).")
    p.add_argument("--list", action="store_true",
        help="Only list parsed manifest rows (no plan, no apply).")
    return p

# --------------------------------------------------------------------------
# main() + entrypoint
# --------------------------------------------------------------------------
def main(argv=None):
    ap = build_argparser()
    args = ap.parse_args(argv)

    if not args.manifest.exists():
        print("ERROR: manifest not found: " + str(args.manifest), file=sys.stderr)
        return 2
    manifest_text = args.manifest.read_text(encoding="utf-8")
    rows, skipped = parse_manifest(manifest_text)
    total_seen = len(rows) + skipped
    summary = str(len(rows)) + " of " + str(total_seen) + " data lines"
    if skipped:
        summary += " (" + str(skipped) + " malformed line(s) skipped)"

    if not rows:
        # Always log the parse summary so operator sees what was found before bailing.
        print("ERROR: nothing parsed from " + str(args.manifest) + ": " + summary, file=sys.stderr)
        return 2

    backup_root = args.manifest.parent.resolve(strict=False)
    target_root = args.target.resolve(strict=False)

    if args.list:
        print("Parsed " + summary + " from " + str(args.manifest) + ":")
        for rel, size, sha in rows:
            print("  " + ("{:>10d}".format(size)) + "  " + sha[:PARTIAL_HASH_CHARS] + "..  " + rel)
        return 0

    plan = build_plan(
        rows, backup_root, target_root,
        list(DEFAULT_EXCLUDES) + list(args.exclude),
        list(args.only), args.force, args.backup_current,
    )

    print("Manifest: " + str(args.manifest) + " (" + summary + ")")
    render_plan(plan, backup_root, target_root)

    if not args.apply:
        print("DRY-RUN COMPLETE. No files were copied. Re-run with --apply to perform.")
        return 0

    if not args.yes:
        print("About to COPY " + str(len(plan.do)) + " files into " + str(target_root) + ".")
        try:
            resp = input("Type 'yes' to continue, anything else to abort: ")
        except EOFError:
            print("ABORTED: no TTY available and --yes not set.", file=sys.stderr)
            return 2
        if resp.strip().lower() != "yes":
            print("ABORTED by user.")
            return 2

    rc = apply_plan(plan, backup_root, target_root, args.verbose, apply_hash_verify=not args.skip_apply_hash)
    if rc == 0:
        # Post-restore accumulation guard (Option 2: lighter-weight, scope-discipline deferred fix).
        # Each restore with --backup-current (or its .md auto-apply) creates a new
        # timestamped `.pre_restore_<UTC>` sibling; without manual cleanup those pile up
        # forever. The active alternative is a `--prune-restore-backups keep=N` flag
        # (deferred until a real cumulative-accident case is documented).
        pre_restored = sorted(target_root.rglob("*.pre_restore_*"))
        print()
        if pre_restored:
            print("WARN: " + str(len(pre_restored)) + " accumulated .pre_restore_* sibling(s) in " + str(target_root) + ".")
            print("      Run a future --prune-restore-backups keep=N flag, or delete manually to prevent accumulation.")
        else:
            print("Restore complete; 0 .pre_restore_* siblings accumulated in " + str(target_root) + ".")
    return rc


if __name__ == "__main__":
    sys.exit(main() or 0)

