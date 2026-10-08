"""
cron_nightly_dr_smoke - end-to-end DR chain smoke test for nightly cron.

Runs the full chain (verify -> restore --apply) so any nightly drift in
Backups/session_chats_<ts>/ snapshots is detected before it accumulates.

CLI:
  --apply              Actually run the chain (default: dry-run, prints plan only)
  --verify-only        Run only stage 1 (verify); skip restore + Slack. For pre-flight
                       gate use. Exits 0 cleanly when no snapshots exist.
  --yes                Skip the confirm prompt (only effective with --apply)
  --scratch-dir PATH   Where to land the restore target. Default: auto-generated
                       under tempfile.gettempdir(). REJECTED if it already exists
                       (refuse-with-loud-error, exit 2) to avoid silent rmtree
                       of a user-provided path.
  --manifest PATH      Snapshot manifest to test. Default: latest
                       Backups/session_chats_*/manifest.txt.
  --keep-scratch       Leave the scratch dir on disk for forensic debugging.
  --timeout SECONDS    Per-stage subprocess timeout. Default 180.

Exit codes:
  0   all stages clean
  1   any stage detected drift
  2   pre-flight error (manifest missing, scratch-dir conflict, etc.)
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Ensure project root is on sys.path so imports of scripts.* and config work
# from any CWD (same pattern as _scratch/run_tests_batched.py and
# scripts/ci_tabs_regression.py).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.safe_subprocess import run_with_limits, MemoryLimitExceeded
from config import CONFIG

PROJECT_ROOT = Path(r"C:\KokertechAI")
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
BACKUPS_DIR = PROJECT_ROOT / "Backups"


def find_latest_snapshot():
    """Return the newest Backups/session_chats_*/manifest.txt path, or None.

    Returns None if Backups/ does not exist (e.g. fresh install) or contains
    no session_chats_ snapshots. NEVER raises.
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


def run_step(label, cmd, cwd, timeout, tail_chars=5000):
    """Run a subprocess, return the returncode. Tail is printed inside; never raises."""
    print()
    print("--- %s ---" % label)
    print("  cmd: " + " ".join(cmd))
    t0 = time.time()
    dr_memory_mb = int(CONFIG.get("orchestration_dr_smoke_memory_mb", 512))
    try:
        proc = run_with_limits(
            cmd, cwd=str(cwd), timeout=timeout,
            memory_mb=dr_memory_mb,
        )
        elapsed = time.time() - t0
        rc = proc.returncode
        out = (proc.stdout or "") + (proc.stderr or "")
        tail = out[-tail_chars:] if len(out) > tail_chars else out
        print("  rc: %d  elapsed: %.1fs" % (rc, elapsed))
        if tail:
            for tl in tail.splitlines():
                print("    " + tl)
        return rc
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        print("  TIMEOUT after %.1fs (limit %ds)" % (elapsed, timeout))
        return 124
    except MemoryLimitExceeded as e:
        elapsed = time.time() - t0
        print("  MEMORY LIMIT EXCEEDED after %.1fs: %s" % (elapsed, e))
        return 125


def main():
    ap = argparse.ArgumentParser(
        prog="cron_nightly_dr_smoke",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--apply", action="store_true",
        help="Actually run the chain (default: dry-run, prints plan only).",
    )
    ap.add_argument(
        "--verify-only", action="store_true",
        help="Run only the verify stage (stage 1); skip restore + Slack. "
             "For pre-flight gate use. Exits 0 cleanly when no snapshots exist.",
    )
    ap.add_argument(
        "--yes", action="store_true",
        help="Skip confirm prompt (only effective with --apply).",
    )
    ap.add_argument(
        "--scratch-dir", type=Path, default=None,
        help="Scratch dir to write the restore target into. Default: auto-generated.",
    )
    ap.add_argument(
        "--manifest", type=Path, default=None,
        help="Snapshot manifest to test. Default: latest Backups/session_chats_*/manifest.txt.",
    )
    ap.add_argument(
        "--keep-scratch", action="store_true",
        help="Leave the scratch dir on disk after the run (forensic debugging).",
    )
    ap.add_argument(
        "--timeout", type=float, default=180.0,
        help="Per-stage subprocess timeout in seconds. Default 180.",
    )
    args = ap.parse_args()

    # Validate --timeout (Reviewer-caught MEDIUM: must be > 0 or subprocess
    # raises TimeoutExpired immediately / with a confusing trace).
    if args.timeout <= 0:
        ap.error("--timeout must be > 0 (got %s)" % args.timeout)

    # ----- pre-flight: resolve manifest path -----
    if args.manifest is not None:
        manifest = args.manifest
    else:
        manifest = find_latest_snapshot()
    if manifest is None or not manifest.is_file():
        # Fresh workspace / no snapshots yet: in --verify-only mode the gate
        # passes cleanly (nothing to verify), but in the default nightly-cron
        # mode this is still a pre-flight error.
        if args.verify_only:
            print("CRON: no manifest found (looked under %s). Skipping gate (--verify-only)." % BACKUPS_DIR)
            print()
            print("NIGHTLY DR SMOKE: PASS (no snapshots to verify, --verify-only)")
            return 0
        print("CRON: no manifest found (looked under %s)." % BACKUPS_DIR, file=sys.stderr)
        return 2
    print("CRON: manifest = %s" % manifest)

    # ----- resolve scratch_dir ONLY when --apply is set (no wasted mkdir on dry-run) -----
    scratch_dir = None
    if args.apply:
        if args.scratch_dir is not None:
            # User-provided path: REFUSE if it already exists (Reviewer-caught CRITICAL
            # fix). Otherwise we'd silently nuke a real directory.
            if args.scratch_dir.exists():
                print(
                    "CRON: refusing --scratch-dir %s (already exists, would be destructive). "
                    "Move it aside or delete it yourself first." % args.scratch_dir,
                    file=sys.stderr,
                )
                return 2
            scratch_dir = args.scratch_dir
        else:
            # Auto-generated scratch dir under tempfile: safe to nuke.
            scratch_dir = Path(tempfile.gettempdir()) / ("dr_smoke_" + manifest.parent.name)
            if scratch_dir.exists():
                shutil.rmtree(scratch_dir, ignore_errors=True)
        scratch_dir.mkdir(parents=True, exist_ok=True)
        print("CRON: scratch_dir = %s" % scratch_dir)
    else:
        print("CRON: dry-run (use --apply to actually execute the chain).")

    # ----- gate-grepable: --verify-only dispatches to stage 1 only (no
    # scratch dir, no Slack, no restore). Dry-run (no --apply, no --verify-only)
    # is a no-op.
    if not args.apply:
        if args.verify_only:
            rc = run_step(
                "STAGE 1 (verify-only): verify --in-place",
                [sys.executable, str(SCRIPTS_DIR / "verify_snapshot_roundtrip.py"),
                 str(manifest), "--in-place"],
                cwd=PROJECT_ROOT, timeout=args.timeout,
            )
            if rc != 0:
                print()
                print("NIGHTLY DR SMOKE: FAIL (verify stage, --verify-only)")
                return 1
            print()
            print("NIGHTLY DR SMOKE: PASS (verify-only)")
            return 0
        print()
        print("NIGHTLY DR SMOKE: PASS (DRY-RUN, --apply not set)")
        return 0

    # ----- try/finally ensures scratch_dir is cleaned up unless --keep-scratch -----
    failed = False
    try:
        # Stage 1: verify --in-place (re-hash the snapshot files directly).
        rc = run_step(
            "STAGE 1: verify --in-place",
            [sys.executable, str(SCRIPTS_DIR / "verify_snapshot_roundtrip.py"),
             str(manifest), "--in-place"],
            cwd=PROJECT_ROOT, timeout=args.timeout,
        )
        if rc != 0:
            print("STAGE 1 FAILED: rc=%d" % rc)
            failed = True

        # Stage 2: restore --apply --yes into the fresh scratch dir.
        if not failed:
            rc = run_step(
                "STAGE 2: restore --apply --yes",
                [sys.executable, str(SCRIPTS_DIR / "restore_session_chats_backup.py"),
                 str(manifest), "--apply", "--yes", "--target", str(scratch_dir)],
                cwd=PROJECT_ROOT, timeout=args.timeout,
            )
            if rc != 0:
                print("STAGE 2 FAILED: rc=%d" % rc)
                failed = True
    finally:
        if scratch_dir is not None and not args.keep_scratch and scratch_dir.exists():
            shutil.rmtree(scratch_dir, ignore_errors=True)
            print("CRON: scratch_dir cleaned up (--keep-scratch NOT set).")
        elif scratch_dir is not None and args.keep_scratch:
            print("CRON: scratch_dir LEFT ON DISK (--keep-scratch set): %s" % scratch_dir)

    # ----- gate-grepable footer line -----
    if failed:
        print()
        print("NIGHTLY DR SMOKE: FAIL")
        return 1
    print()
    print("NIGHTLY DR SMOKE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
