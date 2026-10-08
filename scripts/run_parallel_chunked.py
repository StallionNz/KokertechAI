"""Chunked-parallel test runner — the KNOWN_FAILURES Group 5 operating mode.

Why this exists (2026-09-22):
  Full-suite monolithic parallel runs (pytest-xdist) crash with INTERNALERROR
  once the collection set reaches ~35 files per worker (xdist workers each
  collect the ENTIRE set). 10 and 20 files/worker are green; >=35 crashes
  deterministically regardless of -n, capture mode, or Qt platform. This is
  the Python 3.14 / pytest capture-fd poisoning documented in pytest.ini
  (upstream pytest#14528), proven to bind inside xdist workers too.

Rules encoded here (all measured, see the known-failures operating notes):
  1. <= 15 files per chunk (measured safe bound: 20; margin for churn).
  2. Heavy-integration files (keyboard hooks, web servers, subprocess
     spawners) run in their own trailing chunk — belt-and-suspenders on top
     of the conftest _no_onboarding_modal guard.
  3. Resource-heavy files (ISOLATED) run ALONE with a capped worker count
     (trailing `-n N`, last -n wins) — added 2026-10-04 after
     test_stress_integration.py crashed its 15-file chunk with `MemoryError:`
     + `[gw1] node down` under `-n auto`. Tuned to `-n 0` (serial): under
     `--dist loadscope` a single file is one worker anyway, so serial is both
     fastest and lowest-memory (measured 29.6s / 667 MB vs 32.9s / 2186 MB at
     -n 2).
  4. Deterministic order (sorted); each chunk a fresh pytest process.

Per chunk: fresh log in _scratch/chunked_runs/chunk_NN.log, wall time,
pass/fail/skip counts parsed from the pytest summary, and FAILED test names
collected for classification. A chunk exceeding CHUNK_TIMEOUT_S is
tree-killed and marked as an infra problem — a wedged chunk must never
wedge the whole run.

Usage:
  python -X utf8 scripts/run_parallel_chunked.py              # all chunks
  python -X utf8 scripts/run_parallel_chunked.py --start 3    # resume from chunk 3

Exit code: 0 = no infra problems (test failures are listed, not infra);
           1 = at least one infra-problem chunk (crash or timeout).
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
LOG_DIR = os.path.join(ROOT, "_scratch", "chunked_runs")

CHUNK_SIZE = 15
CHUNK_TIMEOUT_S = 900
HEAVY = (
    "test_hotkey_system.py",
    "test_hotkey_controller_plugin_integration.py",
    "test_kokerpro_web.py",
    "test_git_tracker.py",
    "test_git_tracker_tab.py",
)

# Resource-heavy files that get their OWN chunk with a capped worker count.
# 2026-10-04: test_stress_integration.py crashed its 15-file chunk (07) with
# `MemoryError:` + `[gw1] node down` under the default `-n auto`. Its slowest
# test (TestCrewIntegrationStress::test_crewai_empty_task, ~37.5 s) plus 14
# sibling files exhausted memory across workers.
#
# Worker-count tuning (2026-10-04, 2 runs each, aggregate python.exe RSS):
#   -n 0: 29.6s /  667 MB   <-- fastest AND lowest memory
#   -n 1: 32.5s /  748 MB
#   -n 2: 32.9s / 2186 MB
#   -n 3: 28.7s / 1530 MB
#   -n 4: 29.8s / 1903 MB
# With `--dist loadscope` one file = one worker, so xdist buys NO parallelism
# here — it only adds worker overhead and memory. Serial (-n 0) wins on both
# axes and removes the MemoryError class entirely.
# Maps filename -> worker count for the isolated chunk (0 = serial).
ISOLATED = {
    "test_stress_integration.py": 0,
}


def build_chunks():
    """Return a list of {"files": [...], "workers": int|None} chunk dicts.

    workers=None means use pytest.ini's default (-n auto); an int caps the
    worker count for that chunk via a trailing `-n N` (last -n wins).
    """
    files = sorted(os.path.basename(p) for p in glob.glob(os.path.join(TESTS, "test_*.py")))
    heavy = [f for f in files if f in HEAVY]
    isolated = [f for f in files if f in ISOLATED]
    light = [f for f in files if f not in HEAVY and f not in ISOLATED]
    chunks = [{"files": light[i:i + CHUNK_SIZE], "workers": None}
              for i in range(0, len(light), CHUNK_SIZE)]
    if heavy:
        chunks.append({"files": heavy, "workers": None})
    # Each isolated file runs alone, with its own capped worker count.
    for f in isolated:
        chunks.append({"files": [f], "workers": ISOLATED[f]})
    return chunks


def kill_tree(pid):
    """Kill a process and its children (xdist workers are grandchildren)."""
    if os.name == "nt":
        taskkill_bin = shutil.which("taskkill") or "taskkill"
        subprocess.run([taskkill_bin, "/F", "/T", "/PID", str(pid)],  # noqa: S603, S607
                       capture_output=True)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run_chunk(i, chunk):
    """Run one chunk; returns (exit_code_or_None, elapsed_s, log_path).

    chunk is a {"files": [...], "workers": int|None} dict. A trailing `-n N`
    overrides pytest.ini's addopts `-n auto` (last -n wins in xdist).
    """
    log_path = os.path.join(LOG_DIR, f"chunk_{i:02d}.log")
    cmd = [sys.executable, "-X", "utf8", "-m", "pytest", "-q", "--tb=line",
           "-rf", "-p", "no:cacheprovider"]
    if chunk["workers"] is not None:
        cmd += ["-n", str(chunk["workers"])]
    cmd += [os.path.join("tests", f) for f in chunk["files"]]
    t0 = time.monotonic()
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)  # noqa: S603
        try:
            rc = proc.wait(timeout=CHUNK_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
            rc = None  # None == timeout == infra problem
    return rc, time.monotonic() - t0, log_path


def parse_log(path):
    """Extract counts + FAILED names from a chunk log (best effort)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()

    def count(pattern):
        m = re.search(pattern, text)
        return int(m.group(1)) if m else 0

    return {
        "passed": count(r"(\d+) passed"),
        "failed": count(r"(\d+) failed"),
        "skipped": count(r"(\d+) skipped"),
        "errors": count(r"(\d+) error"),
        "failed_names": re.findall(r"^FAILED (tests/\S+)", text, re.M),
        "internalerror": "INTERNALERROR" in text,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=0, help="chunk index to start from")
    args = ap.parse_args()

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.makedirs(LOG_DIR, exist_ok=True)
    chunks = build_chunks()

    totals = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    all_failed_names = []
    infra_chunks = []

    for i, chunk in enumerate(chunks):
        if i < args.start:
            continue
        workers = chunk["workers"] if chunk["workers"] is not None else "auto"
        print(f"===== CHUNK {i:02d} ({len(chunk['files'])} files, -n {workers}) =====",
              flush=True)
        rc, elapsed, log_path = run_chunk(i, chunk)
        res = parse_log(log_path)
        for k in totals:
            totals[k] += res[k]
        all_failed_names.extend(res["failed_names"])

        if rc is None:
            infra_chunks.append((i, f"TIMEOUT after {CHUNK_TIMEOUT_S}s (tree killed)"))
            verdict = "INFRA:TIMEOUT"
        elif res["internalerror"] or rc not in (0, 1):
            infra_chunks.append((i, f"exit {rc}" + (" + INTERNALERROR" if res["internalerror"] else "")))
            verdict = "INFRA:CRASH"
        else:
            verdict = "clean" if rc == 0 else "tests-failed"

        counts = (f"{res['passed']} passed, {res['failed']} failed, "
                  f"{res['skipped']} skipped, {res['errors']} errors")
        print(f"  chunk {i:02d}: {verdict} | {counts} | {elapsed:.1f}s | {log_path}", flush=True)
        for name in res["failed_names"]:
            print(f"    FAILED {name}", flush=True)

    print("\n===== SUMMARY =====")
    print(f"chunks run: {len(chunks) - args.start} | infra-problem chunks: {len(infra_chunks)}")
    print(f"totals: {totals['passed']} passed, {totals['failed']} failed, "
          f"{totals['skipped']} skipped, {totals['errors']} errors")
    for i, why in infra_chunks:
        print(f"  INFRA chunk {i:02d}: {why}")
    if all_failed_names:
        print(f"failed tests ({len(all_failed_names)}):")
        for name in all_failed_names:
            print(f"  {name}")
    return 1 if infra_chunks else 0


if __name__ == "__main__":
    sys.exit(main())
