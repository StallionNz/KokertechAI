"""CI-cron regression detector for the tabs-touching pytest sweep.

Runs the same 12-file sweep that verified the cleanup of stale
tabs/*.py.pre_* backup variants, computes per-file PASS/FAIL/TIMEOUT/ERROR
status with elapsed duration, compares vs an auto-seeded baseline at
tests/_baseline_tabs_sweep.json, classifies flips as REGRESSION /
IMPROVEMENT / UNCHANGED via the rank PASS > FAIL > ERROR > TIMEOUT, and
exits 1 ONLY on genuine regression flips. Improvements log but exit 0
(the ratchet rule auto-promotes fixed failures to the new baseline).

Cron usage::

    0 6 * * *   cd /path/to/KokertechAI \
                && python scripts/ci_tabs_regression.py \
                >> /var/log/kokertech_tabs_cron.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal  # noqa: F401  (kept for downstream typing re-exports)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Ensure project root is on sys.path so that `from scripts.safe_subprocess`
# and `from config` resolve correctly when the script is invoked from any CWD
# (e.g., `python scripts/ci_tabs_regression.py --self-test` from project root).
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.safe_subprocess import run_with_limits, MemoryLimitExceeded
from config import CONFIG


BASELINE_PATH = PROJECT_ROOT / "tests" / "_baseline_tabs_sweep.json"
REPORT_PATH = PROJECT_ROOT / "_diag" / "tabs_regression_latest.json"
HISTORY_PATH = PROJECT_ROOT / "_diag" / "tabs_regression_history.jsonl"

TABS_TESTS = (
    "tests/test_session_log_tab.py",
    "tests/test_settings_tab.py",
    "tests/test_web_view_tab.py",
    "tests/test_health_tab.py",
    "tests/test_memory_browser_tab.py",
    "tests/test_mcp_client_tab.py",
    "tests/test_neural_graph.py",
    "tests/test_rag_tab.py",
    "tests/test_sprint13_ui_wiring.py",
    "tests/test_tab_qt_interface.py",
    "tests/test_hotkey_system.py",
    "tests/test_kokertechController.py",
)

PER_FILE_BUDGET_SLOW = 180
PER_FILE_BUDGET_FAST = 60
SLOW_FILE_MARKERS = (
    "kokertechController", "kokertech_main_dashboard", "hotkey_system",
    "sprint13_ui_wiring", "tab_qt_interface",
)

STATUS_RANKS = {"PASS": 4, "FAIL": 3, "ERROR": 2, "MEMORY": 1.5, "TIMEOUT": 1, "UNKNOWN": 0}
# Canonical status list used by all summary/report sites — update ALL_STATUSES
# whenever a new status code is added to STATUS_RANKS.
ALL_STATUSES = ("PASS", "FAIL", "ERROR", "MEMORY", "TIMEOUT")
BASELINE_VERSION = 1


def _budget_for(test_file):
    name = Path(test_file).name
    if any(marker in name for marker in SLOW_FILE_MARKERS):
        return PER_FILE_BUDGET_SLOW
    return PER_FILE_BUDGET_FAST


def _truncate_output(text, head_lines=15, tail_lines=15):
    """Capture head + tail so info at start (assertion lines) survives.

    pytest prints the failing assertion near the top; tail-only slicing
    discards it. Keep first 15 + last 15 lines + an omission marker.
    """
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= head_lines + tail_lines:
        return text
    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:])
    return f"{head}\n... ({len(lines) - head_lines - tail_lines} lines omitted) ...\n{tail}"


def _run_one(test_file):
    # pytest 9 + Windows + Python 3.14 FDCapture quirk: pytest full stdout
    # does not reliably reach subprocess.run. Exit code is authoritative.
    # Uses run_with_limits for strict timeout + memory ceiling enforcement.
    t0 = time.time()
    ci_memory_mb = int(CONFIG.get("orchestration_ci_memory_mb", 1024))
    try:
        proc = run_with_limits(
            [sys.executable, "-m", "pytest", test_file,
             "--tb=line", "-q", "--no-header", "-p", "no:cacheprovider"],
            timeout=_budget_for(test_file),
            memory_mb=ci_memory_mb,
        )
        dt = time.time() - t0
        if proc.returncode == 0:
            status = "PASS"
        elif proc.returncode in (1, 2):
            status = "FAIL"
        else:
            status = "ERROR"
        return (
            status, dt,
            _truncate_output(proc.stdout or ""),
            _truncate_output(proc.stderr or ""),
        )
    except subprocess.TimeoutExpired:
        return ("TIMEOUT", time.time() - t0, "", "(timeout)")
    except MemoryLimitExceeded as exc:
        return ("MEMORY", time.time() - t0, "", f"(memory limit: {exc})")
    except Exception as exc:
        return ("ERROR", time.time() - t0, "", repr(exc))


def _load_baseline(path):
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        # TOCTOU: deleted between exists() and read_text() — treat as missing.
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        # Corruption (or read I/O) is distinct from "missing": log to stderr
        # so cron output shows why the baseline was treated as absent.
        print(
            f"[load_baseline] treating {path.name} as missing: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None
    if not isinstance(data, dict) or data.get("version") != BASELINE_VERSION:
        return None
    files = data.get("files")
    return data if isinstance(files, dict) else None


def _write_baseline(path, files):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": BASELINE_VERSION,
        "schema": "tabs-regression-baseline",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    # PID-suffixed .tmp avoids concurrent-script trampling on shared FS.
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
        tmp.replace(path)
    finally:
        # Best-effort cleanup if replace failed; non-fatal if unlink errors.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _classify_flips(baseline_files, current_files):
    # REGRESSION / IMPROVEMENT / UNCHANGED row tuple shape:
    #     (fname: str, baseline_status: str, current_status: str)
    # Self-test (4) unpacks the first slot as the filename, so any future
    # refactor here must keep fname at index 0.
    out = {"REGRESSION": [], "IMPROVEMENT": [], "UNCHANGED": []}
    for fname, c_data in current_files.items():
        c_status = c_data.get("status", "UNKNOWN")
        if fname not in baseline_files:
            continue
        b_status = baseline_files[fname].get("status", "UNKNOWN")
        if c_status == b_status:
            out["UNCHANGED"].append((fname, b_status, c_status))
            continue
        c_rank = STATUS_RANKS.get(c_status, 0)
        b_rank = STATUS_RANKS.get(b_status, 0)
        if c_rank < b_rank:
            out["REGRESSION"].append((fname, b_status, c_status))
        else:
            out["IMPROVEMENT"].append((fname, b_status, c_status))
    return out


def _write_latest_report(results, baseline, flips, new_files, missing_files, exit_code):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "baseline_at": baseline.get("captured_at") if baseline else None,
        "exit_code": exit_code,
        "results": results,
        "summary": {s: sum(1 for r in results.values() if r.get("status") == s)
                    for s in ALL_STATUSES},
        "flips": {kind: [{"file": f, "from": b, "to": c} for (f, b, c) in entries]
                  for kind, entries in flips.items()},
        "new_files": new_files,
        "missing_files": missing_files,
    }
    REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")


def _append_history(results, flips, exit_code):
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    summary = {s: sum(1 for r in results.values() if r.get("status") == s)
               for s in ALL_STATUSES}
    record = {"at": datetime.now(timezone.utc).isoformat(),
              "exit_code": exit_code,
              "summary": summary,
              "regressions": len(flips["REGRESSION"]),
              "improvements": len(flips["IMPROVEMENT"])}
    with open(HISTORY_PATH, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(record) + "\n")


def _run_real_sweep(force_update_baseline=False):
    results = {}
    for f in TABS_TESTS:
        status, dt, _so, _se = _run_one(f)
        results[f] = {"status": status, "duration_seconds": round(dt, 1)}
        print(f"  [{status:7s}] {dt:6.1f}s  {f}")
        if status != "PASS":
            if _so:
                print(f"       --- stdout ---\n{_so}")
            if _se:
                print(f"       --- stderr ---\n{_se}")

    baseline = _load_baseline(BASELINE_PATH)
    base_files = baseline.get("files", {}) if baseline else {}
    flips = _classify_flips(base_files, results)
    new_files = [f for f in TABS_TESTS if f not in base_files]
    missing_files = [f for f in base_files if f not in TABS_TESTS]

    should_ratchet = force_update_baseline or not flips["REGRESSION"]
    if should_ratchet:
        _write_baseline(BASELINE_PATH, results)

    exit_code = 1 if flips["REGRESSION"] else 0
    _write_latest_report(results, baseline, flips, new_files, missing_files, exit_code)
    _append_history(results, flips, exit_code)

    print()
    print("=" * 64)
    print("TABS REGRESSION SWEEP RESULTS")
    print("=" * 64)
    if baseline:
        print(f"Baseline captured_at:  {baseline.get('captured_at', 'unknown')}")
    print(f"Run captured_at:       {datetime.now(timezone.utc).isoformat()}")
    summary = {s: sum(1 for r in results.values() if r.get("status") == s)
               for s in ALL_STATUSES}
    for k in ALL_STATUSES:
        print(f"  {k:7s}: {summary[k]}")
    if flips["REGRESSION"]:
        print()
        print("REGRESSIONS (current worse than baseline, exit=1):")
        for fname, b, c in flips["REGRESSION"]:
            print(f"  - {fname}: {b} -> {c}")
    if flips["IMPROVEMENT"]:
        print()
        print("IMPROVEMENTS (exited 0, baseline ratcheted forward):")
        for fname, b, c in flips["IMPROVEMENT"]:
            print(f"  + {fname}: {b} -> {c}")
    if new_files:
        print()
        print(f"NEW FILES (added to baseline): {new_files}")
    if missing_files:
        print()
        print(f"MISSING FROM SWEEP: {missing_files}")
    print()
    print(f"Latest report:     {REPORT_PATH}")
    print(f"History (JSONL):   {HISTORY_PATH}")
    print(f"Baseline (locked): {BASELINE_PATH}")
    print(f"Exit code:         {exit_code}")

    sys.exit(exit_code)


def _self_test():
    """Verify the classifier and baseline I/O without touching real state."""
    print("--- self-test: starting ---")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "_baseline_tabs_sweep.json"

        # (1) mixed regression + improvement flips
        bf = {"tests/test_a.py": {"status": "PASS"}, "tests/test_b.py": {"status": "FAIL"}, "tests/test_c.py": {"status": "TIMEOUT"}}
        cf = {"tests/test_a.py": {"status": "FAIL"}, "tests/test_b.py": {"status": "FAIL"}, "tests/test_c.py": {"status": "FAIL"}}
        f = _classify_flips(bf, cf)
        assert len(f["REGRESSION"]) == 1 and f["REGRESSION"][0][0] == "tests/test_a.py"
        assert len(f["IMPROVEMENT"]) == 1 and f["IMPROVEMENT"][0][0] == "tests/test_c.py"
        assert len(f["UNCHANGED"]) == 1
        print("  (1) mixed regression + improvement: PASS")

        # (2) all-improvement only
        f2 = _classify_flips({"a": {"status": "FAIL"}, "b": {"status": "TIMEOUT"}},
                             {"a": {"status": "PASS"}, "b": {"status": "FAIL"}})
        assert len(f2["REGRESSION"]) == 0 and len(f2["IMPROVEMENT"]) == 2
        print("  (2) all-improvement: PASS")

        # (3) ratchet round-trip + corrupt input handling
        _write_baseline(tmp, bf)
        r = _load_baseline(tmp)
        assert r is not None
        f3 = _classify_flips(r["files"], bf)
        assert len(f3["REGRESSION"]) == 0 and len(f3["IMPROVEMENT"]) == 0 and len(f3["UNCHANGED"]) == 3
        tmp.write_text("not json", encoding="utf-8")
        assert _load_baseline(tmp) is None
        print("  (3) ratchet round-trip + corrupt-input: PASS")

        # (4) ratchet-promote-then-regress: the real-world flapping pattern.
        # Initial baseline FAIL -> current PASS (improvement, ratchet promotes) ->
        # new baseline PASS -> current FAIL (regression detected, exit=1).
        _write_baseline(tmp, {"a.py": {"status": "FAIL"}, "b.py": {"status": "PASS"}})
        b_v1 = _load_baseline(tmp)
        assert b_v1 is not None
        f_promote = _classify_flips(
            b_v1["files"], {"a.py": {"status": "PASS"}, "b.py": {"status": "PASS"}},
        )
        assert len(f_promote["REGRESSION"]) == 0
        assert len(f_promote["IMPROVEMENT"]) == 1
        _write_baseline(tmp, {"a.py": {"status": "PASS"}, "b.py": {"status": "PASS"}})
        b_v2 = _load_baseline(tmp)
        assert b_v2 is not None
        f_regress = _classify_flips(
            b_v2["files"], {"a.py": {"status": "FAIL"}, "b.py": {"status": "PASS"}},
        )
        # Order-independent + cardinality-constrained atomically: a future
        # refactor of _classify_flips cannot weaken this into a vacuous
        # pass by changing length or filename-set semantics in isolation.
        assert [f for f, _, _ in f_regress["REGRESSION"]] == ["a.py"]
        print("  (4) ratchet-promote-then-regress: PASS")

    print("--- self-test: all checks PASSED ---")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="CI-cron regression detector for tabs-touching pytest sweep.",
    )
    parser.add_argument("--self-test", action="store_true",
                        help="Synthetic flip-classification tests instead of the real sweep.")
    parser.add_argument("--force-ratchet", action="store_true",
                        help="Force baseline update even if regressions detected.")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    _run_real_sweep(force_update_baseline=args.force_ratchet)
    return 0  # _run_real_sweep calls sys.exit()


if __name__ == "__main__":
    raise SystemExit(main())
