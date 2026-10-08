#!/usr/bin/env python3
# scripts/backup_session_chats.py
#
# Paired with scripts/restore_session_chats_backup.py -- produces the manifest.txt
# format that helper parses (path | size | sha256). Default source = C:\KokertechAI,
# default target = C:\KokertechAI\Backups. Snapshot label defaults to session_chats_<UTC>.
#
# CLI surface mirrors the restore helper:
#   --source DIR         what to back up (default: C:\KokertechAI)
#   --target DIR         where to put the snapshot (default: ...\Backups)
#   --label NAME         snapshot folder name (default: session_chats_<UTC>)
#   --only GLOB_OR_PATH  REPLACE default inventory with this glob/path (repeatable)
#   --include GLOB       ADD this glob to the default inventory (repeatable)
#   --exclude GLOB       exclude in addition to defaults (repeatable)
#   --apply              actually copy + write manifest (default: dry-run)
#   --yes                skip confirm on --prune
#   --prune N            keep newest N snapshots, prune older (0 = no prune)
#   --verbose, -v        per-file OK/FAIL output during --apply
"""
backup_session_chats - paired backup CLI for the restore helper.

Default inventory: ~57 files (~600KB) -- excludes execution_log.txt.

As of 2026-06-26, execute_log.txt self-bounds via KokertechLogger's
size-based rotation (5MB max + 3 rotating backups, see kokertech_logger.py).
The live file and rotated copies ("execution_log.txt.1", ".2", ".3")
are therefore NOT included in the default inventory (they re-rotate
independently and lose chronology across restarts). For a point-in-time
snapshot include them explicitly:
    --include execution_log.txt --include 'execution_log.txt.*'
"""
from __future__ import annotations

import argparse
import datetime
import os
import shutil
import sys
import time
from pathlib import Path


# Reuse sha256_of + matches_any + PARTIAL_HASH_CHARS from the restore helper so the
# manifest format stays in sync (DRY: single source of truth for hashing + glob match).
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from restore_session_chats_backup import sha256_of, matches_any, PARTIAL_HASH_CHARS


PROJECT_ROOT = Path(r"C:\KokertechAI")
DEFAULT_BACKUP_ROOT = PROJECT_ROOT / "Backups"

# Same default-excludes as restore helper so a backed-up snapshot is symmetric.
DEFAULT_EXCLUDES = ("manifest.txt", "*.bak", "*.bak_*", "*.pre_restore*", "*.tmp")

# Default inventory: top-level runtime state + freebuff MD notes + settings_backups tree.
# Mirrors what the prior manual backup at Backups/session_chats_20260620_145920/ snapshotted.
DEFAULT_INVENTORY = (
    # Top-level runtime state
    "app_settings.json",
    "context_summary.txt",
    "custom_themes.json",
    # execution_log.txt excluded by default. Since 2026-06-26 it self-rotates
    # at 5MB with 3 backups in KokertechLogger (see _rotate_if_needed).
    # Snapshot explicitly if needed via --include execution_log.txt
    # --include 'execution_log.txt.*' to capture both the live file and the
    # 3 rotating backup snapshots.
    "personas.json",
    "user_identity.json",
    "kokertech_vault.db",
    "session_journal.jsonl",
    # Freebuff MD session notes (handles spaces in "Freebuff session 1 ..." filenames)
    "freebuff_*.md",
    "Freebuff*.md",
    # Settings backup tree
    "settings_backups/*.json",
)

PATH_COL_WIDTH = 70  # mirrors restore_helper manifest column sizing
CHUNK_SIZE_FOR_HASH = 65536


# --------------------------------------------------------------------------
# Inventory resolver
# --------------------------------------------------------------------------
def _has_glob_chars(s):
    return any(c in s for c in "*?[]")

def build_inventory(source_root, only=None, include=None, exclude=None):
    """Resolve inventory into a sorted list of absolute files.

    Precedence:
    * --only (if any) REPLACES the default inventory (selective backup in tests).
    * --include (if any) is ADDED to the default inventory.
    * --exclude (if any) is added to the default excludes.
    """
    patterns = list(DEFAULT_INVENTORY) if not only else list(only)
    if include:
        patterns = patterns + list(include)
    excludes = list(exclude or [])
    out = []
    seen = set()
    for pat in patterns:
        if not _has_glob_chars(pat):
            p = source_root / pat
            if p.is_file():
                rel = str(p.relative_to(source_root)).replace("\\", "/")
                if rel not in seen:
                    out.append(p)
                    seen.add(rel)
        else:
            for p in sorted(source_root.glob(pat)):
                if p.is_file():
                    rel = str(p.relative_to(source_root)).replace("\\", "/")
                    if rel not in seen:
                        out.append(p)
                        seen.add(rel)
    final = []
    for p in out:
        rel = str(p.relative_to(source_root)).replace("\\", "/")
        if matches_any(rel, excludes) or matches_any(p.name, excludes):
            continue
        final.append(p)
    return sorted(final, key=lambda p: str(p).lower())


# --------------------------------------------------------------------------
# Manifest writer (mirrors Backups/session_chats_20260620_145920/manifest.txt format)
# --------------------------------------------------------------------------
def write_manifest(manifest_path, source_root, files, snapshot_ts):
    """Write the manifest.txt format the restore helper parses.

    Format mirrors the existing Backups/session_chats_20260620_145920/manifest.txt
    header + footer exactly so the restore helper parses it via content-based columns.
    """
    lines = []
    L = lines.append
    L("# Session Chats Backup Manifest")
    L("# Source: " + str(source_root))
    L("# Created: " + snapshot_ts)
    L("# Backup root: " + str(manifest_path.parent))
    L("")
    L("## File inventory (path | size_bytes | sha256)")
    L("")
    for p in files:
        rel = str(p.relative_to(source_root)).replace("\\", "/")
        size = p.stat().st_size
        sha = sha256_of(p)
        L(rel.ljust(PATH_COL_WIDTH) + "  " + str(size) + "  " + sha)
    L("")
    L("## Summary")
    total_bytes = sum(p.stat().st_size for p in files)
    md_count = sum(1 for p in files if p.suffix.lower() == ".md" and "manifest" not in p.name.lower())
    sb_count = sum(1 for p in files if "settings_backups/" in str(p).replace("\\", "/"))
    L("Files copied: " + str(len(files)))
    L("Total bytes: " + str(total_bytes))
    if md_count:
        L("Freebuff MD notes included: " + str(md_count))
    if sb_count:
        L("Settings backups included: " + str(sb_count))
    L("")
    manifest_path.write_text(chr(10).join(lines), encoding="utf-8", newline=chr(10))


# --------------------------------------------------------------------------
# Post-copy verify (sha256 of dst files must match src)
# --------------------------------------------------------------------------
def verify_copied(snapshot_dir, source_root, files):
    """Re-hash each copy in snapshot_dir; return list of (rel, err) failures."""
    failures = []
    for p in files:
        rel = str(p.relative_to(source_root)).replace("\\", "/")
        tgt = snapshot_dir / rel
        if not tgt.exists():
            failures.append((rel, "destination missing: " + str(tgt)))
            continue
        try:
            src_sha = sha256_of(p)
            tgt_sha = sha256_of(tgt)
        except Exception as e:
            failures.append((rel, "hash failed: " + str(e)))
            continue
        if src_sha != tgt_sha:
            failures.append((rel, "src sha=" + src_sha[:PARTIAL_HASH_CHARS] + ".. tgt sha=" + tgt_sha[:PARTIAL_HASH_CHARS] + ".."))
    return failures


# --------------------------------------------------------------------------
# Apply: copy each file using shutil.copy2 (regular files -- not SQLite-atomic;
# the BACKUP direction tolerates torn writes because we re-verify post-copy).
# --------------------------------------------------------------------------
def apply_copy(snapshot_dir, source_root, files, verbose):
    """Copy each file into snapshot_dir. Returns list of (rel, err) failures."""
    failures = []
    for p in files:
        rel = str(p.relative_to(source_root)).replace("\\", "/")
        tgt = snapshot_dir / rel
        try:
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(p), str(tgt))
            if verbose:
                print("  OK   " + rel)
        except Exception as e:
            failures.append((rel, str(e)))
            if verbose:
                print("  FAIL " + rel + ": " + str(e))
    return failures


# --------------------------------------------------------------------------
# Prune old snapshot dirs (only touch dirs matching session_chats_* glob)
# --------------------------------------------------------------------------
def prune_old_backups(target_root, keep):
    """Prune oldest Backups/session_chats_<ts> dirs, keeping the most recent `keep`."""
    pattern = "session_chats_*"
    candidates = [p for p in target_root.glob(pattern) if p.is_dir()]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    to_prune = candidates[max(keep, 0):]
    pruned = []
    for b in to_prune:
        try:
            shutil.rmtree(str(b))
            pruned.append(b)
        except OSError as e:
            print("WARN: failed to prune " + str(b) + ": " + str(e), file=sys.stderr)
    return pruned


# --------------------------------------------------------------------------
# Argparser
# --------------------------------------------------------------------------
def build_argparser():
    p = argparse.ArgumentParser(
        prog="backup_session_chats",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--source", type=Path, default=PROJECT_ROOT,
        help="Source root (default: " + str(PROJECT_ROOT) + ").")
    p.add_argument("--target", type=Path, default=DEFAULT_BACKUP_ROOT,
        help="Destination dir (default: " + str(DEFAULT_BACKUP_ROOT) + ").")
    p.add_argument("--label", type=str, default=None,
        help="Snapshot folder name (default: session_chats_<UTC>; e.g. weekly_pre_sprint).")
    p.add_argument("--only", action="append", default=[], metavar="GLOB_OR_PATH",
        help="REPLACE default inventory with this glob/path (repeatable; --only foo.json --only bar.md).")
    p.add_argument("--include", action="append", default=[], metavar="GLOB",
        help="ADD this glob to the default inventory (repeatable).")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB",
        help="Exclude paths matching this glob in addition to defaults (repeatable).")
    p.add_argument("--apply", action="store_true",
        help="Actually copy + write manifest (default: dry-run).")
    p.add_argument("--yes", "-y", action="store_true",
        help="Skip the confirm prompt on --prune.")
    p.add_argument("--prune", type=int, default=0, metavar="N",
        help="After successful backup, prune oldest snapshot dirs keeping newest N (0 = no prune).")
    p.add_argument("--verbose", "-v", action="store_true",
        help="Per-file OK/FAIL output during --apply.")
    p.add_argument("--list", action="store_true",
        help="Only list the inventory that would be backed up (no copy, no manifest).")
    return p


# --------------------------------------------------------------------------
# Timestamp formatter (matches the format the existing 20260620 backup used)
# --------------------------------------------------------------------------
def now_label():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


# --------------------------------------------------------------------------
# main + entrypoint
# --------------------------------------------------------------------------
def main(argv=None):
    ap = build_argparser()
    args = ap.parse_args(argv)

    if not args.source.exists():
        print("ERROR: source root not found: " + str(args.source), file=sys.stderr)
        return 2

    excludes = list(DEFAULT_EXCLUDES) + list(args.exclude)
    files = build_inventory(
        args.source,
        only=args.only or None,
        include=args.include or None,
        exclude=excludes,
    )

    if not files:
        print("ERROR: no files matched the inventory + filters", file=sys.stderr)
        return 2

    ts_str = now_label()
    label = args.label if args.label else "session_chats_" + ts_str
    snapshot_dir = args.target / label
    manifest_path = snapshot_dir / "manifest.txt"

    print("Source:        " + str(args.source))
    print("Snapshot dir:  " + str(snapshot_dir))
    print("Files:         " + str(len(files)))
    for p in files[:25]:
        rel = str(p.relative_to(args.source)).replace("\\", "/")
        print("  " + rel + "  (" + str(p.stat().st_size) + " B)")
    if len(files) > 25:
        print("  ... and " + str(len(files) - 25) + " more")

    if args.list:
        return 0

    if not args.apply:
        print("DRY-RUN COMPLETE. Re-run with --apply to actually copy + write manifest.")
        return 0

    # Optional confirm prompt on --prune (skip with --yes).
    if args.prune and not args.yes:
        try:
            resp = input("About to prune snapshot dirs after backup (keeping newest " + str(args.prune) + "). Type 'yes' to continue: ")
        except EOFError:
            print("ABORTED: no TTY available and --yes not set.", file=sys.stderr)
            return 2
        if resp.strip().lower() != "yes":
            print("ABORTED by user.")
            return 2

    # Apply: collision-check + mkdir + copy + verify + write manifest.
    # Refuse on --label collision: if the snapshot dir already has a manifest.txt,
    # the user is about to silently overwrite a previous snapshot. Two backups in
    # the same second with the same label would otherwise corrupt state.
    if snapshot_dir.is_file():
        print("ERROR: snapshot path is a regular file, not a directory: " + str(snapshot_dir), file=sys.stderr)
        print("       delete the file or use a different --label.", file=sys.stderr)
        return 2
    if snapshot_dir.exists() and (snapshot_dir / "manifest.txt").exists():
        print("ERROR: snapshot dir already exists with manifest.txt: " + str(snapshot_dir), file=sys.stderr)
        print("       use a fresh --label, or delete the existing snapshot.", file=sys.stderr)
        return 2
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    failures = apply_copy(snapshot_dir, args.source, files, args.verbose)

    print()
    print("Verifying sha256 of copied files...")
    verify_failures = verify_copied(snapshot_dir, args.source, files)
    if verify_failures:
        print()
        print("=== VERIFY FAILURES (" + str(len(verify_failures)) + ") ===")
        for rel, err in verify_failures:
            print("  FAIL " + rel + ": " + err)
        print()
        print("Backup manifest NOT written due to verify failures. Delete " + str(snapshot_dir) + " and retry.")
        return 1

    # Write manifest last (so it reflects only fully-verified backups).
    write_manifest(manifest_path, args.source, files, ts_str)
    print("Manifest written: " + str(manifest_path))
    if failures:
        print()
        print("=== COPY FAILURES (" + str(len(failures)) + ") ===")
        for rel, err in failures:
            print("  FAIL " + rel + ": " + err)
        return 1

    if args.prune:
        pruned = prune_old_backups(args.target, max(args.prune, 1))
        if pruned:
            print()
            print("Pruned " + str(len(pruned)) + " old snapshot(s):")
            for p in pruned:
                print("  " + str(p))

    print()
    print("BACKUP COMPLETE. " + str(len(files)) + " files + 1 manifest written to " + str(snapshot_dir) + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)

