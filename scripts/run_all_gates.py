#!/usr/bin/env python3
"""run_all_gates.py — Unified Pre-Flight Quality Gate Runner for KokertechAI.

Executes all repository quality gates sequentially, times each one,
and provides a consolidated status scorecard with clear error isolation.

Usage:
    python scripts/run_all_gates.py            # Run all 12 pre-flight gates
    python scripts/run_all_gates.py --fast     # Run the 9 fast gates only
    python scripts/run_all_gates.py --verbose  # Print full output of all gates
    python scripts/run_all_gates.py --json     # Output machine-readable JSON
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure UTF-8 output on Windows consoles/pipes to prevent cp1252 encoding crashes
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent

GATES: list[dict[str, str | bool]] = [
    {
        "id": "section11",
        "name": "Markdown Section 11 Gate",
        "script": "scripts/assert_section11_render_clean.py",
        "fast": True,
    },
    {
        "id": "freebuff_tables",
        "name": "Freebuff Table Render Gate",
        "script": "scripts/assert_freebuff_sections_render_clean.py",
        "fast": True,
    },
    {
        "id": "test_counts",
        "name": "Test Count Regression Gate",
        "script": "scripts/assert_test_counts.py",
        "fast": True,
    },
    {
        "id": "anchors",
        "name": "Knowledge Anchor Links Gate",
        "script": "scripts/check_knowledge_anchors.py",
        "fast": True,
    },
    {
        "id": "known_anchors",
        "name": "Knowledge Doc Hygiene Gate",
        "script": "scripts/assert_known_anchors.py",
        "fast": True,
    },
    {
        "id": "lint",
        "name": "Ruff Baseline Lint Gate",
        "script": "scripts/assert_no_new_lint.py",
        "fast": True,
    },
    {
        "id": "silent_catch",
        "name": "Silent-Catch Drift Gate",
        "script": "scripts/assert_no_new_silent_catches.py",
        "fast": True,
    },
    {
        "id": "singleton_reset",
        "name": "Singleton Reset Pattern Gate",
        "script": "scripts/assert_singleton_reset_pattern.py",
        "fast": False,
    },
    {
        "id": "no_corruption",
        "name": "Corruption Recurrence Gate",
        "script": "scripts/assert_no_corrupted_files.py",
        "fast": False,
    },
    {
        "id": "continuity",
        "name": "Continuity Consistency Gate",
        "script": "scripts/assert_continuity_file_consistency.py",
        "fast": False,
    },
    {
        "id": "doc_drift",
        "name": "Living Doc Drift Gate",
        "script": "scripts/assert_doc_drift.py",
        "fast": True,
    },
    {
        "id": "config_mode_pins",
        "name": "Config Mode Pins Gate",
        "script": "scripts/assert_config_mode_pins.py",
        "fast": True,
    },
]


GATE_SKIP_EXIT = 3  # shared contract: a gate exits 3 when its working-set input is absent


def _format_status(ok: bool) -> str:
    """Format green PASS or red FAIL with ANSI codes when supported."""
    if sys.stdout.isatty() and os.name != "nt":
        return "\033[92mPASS\033[0m" if ok else "\033[91mFAIL\033[0m"
    return "[PASS]" if ok else "[FAIL]"


def _format_skip() -> str:
    """Format a SKIPPED gate (exit code GATE_SKIP_EXIT)."""
    if sys.stdout.isatty() and os.name != "nt":
        return "\033[93mSKIP\033[0m"
    return "[SKIP]"


def run_gate(gate: dict[str, str | bool], verbose: bool = False) -> dict[str, object]:
    """Execute a single gate script and return timing and results."""
    script_rel = str(gate["script"])
    script_path = REPO_ROOT / script_rel
    gate_name = str(gate["name"])

    if not script_path.is_file():
        return {
            "name": gate_name,
            "script": script_rel,
            "ok": False,
            "duration": 0.0,
            "error": f"Script not found: {script_rel}",
            "stdout": "",
            "stderr": "",
        }

    start = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, str(script_path)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        duration = time.perf_counter() - start
        ok = (proc.returncode == 0)
        skipped = (proc.returncode == GATE_SKIP_EXIT)
        return {
            "name": gate_name,
            "script": script_rel,
            "ok": ok,
            "duration": duration,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "skipped": skipped,
            "error": proc.stderr.strip() if not ok else "",
        }
    except (subprocess.SubprocessError, OSError, ValueError) as err:
        duration = time.perf_counter() - start
        return {
            "name": gate_name,
            "script": script_rel,
            "ok": False,
            "duration": duration,
            "error": str(err),
            "stdout": "",
            "stderr": "",
        }


def main() -> int:
    """Main execution loop for all repository quality gates."""
    parser = argparse.ArgumentParser(description="KokertechAI Unified Quality Gate Runner")
    parser.add_argument("--fast", action="store_true", help="Run only the fastest pre-flight gates")
    parser.add_argument("--verbose", action="store_true", help="Print detailed stdout for all gates")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Output summary in JSON format")
    args = parser.parse_args()

    selected_gates = [g for g in GATES if not args.fast or g["fast"]]

    if not args.as_json:
        print("=" * 68)
        mode_str = "FAST" if args.fast else "FULL"
        print(f"  KokertechAI Unified Quality Gate Runner ({mode_str} - {len(selected_gates)} gates)")
        print("=" * 68)

    results: list[dict[str, object]] = []
    failed_gates: list[dict[str, object]] = []
    skipped_gates: list[dict[str, object]] = []
    total_start = time.perf_counter()

    for idx, gate in enumerate(selected_gates, 1):
        res = run_gate(gate, verbose=args.verbose)
        results.append(res)
        ok = bool(res["ok"])
        skipped = bool(res.get("skipped", False))
        dur = float(res["duration"])
        name = str(res["name"])

        if skipped:
            skipped_gates.append(res)
        elif not ok:
            failed_gates.append(res)

        if not args.as_json:
            status_text = _format_skip() if skipped else _format_status(ok)
            dots = "." * max(2, 42 - len(name))
            print(f"  [{idx}/{len(selected_gates)}] {name} {dots} {status_text} ({dur:.2f}s)")

            if args.verbose or (not ok and not skipped):
                stdout_txt = str(res["stdout"])
                stderr_txt = str(res["stderr"])
                if stdout_txt:
                    for line in stdout_txt.splitlines():
                        print(f"      | {line}")
                if stderr_txt:
                    for line in stderr_txt.splitlines():
                        print(f"      ! {line}")

    total_duration = time.perf_counter() - total_start

    if args.as_json:
        payload = {
            "ok": len(failed_gates) == 0,
            "total_duration": round(total_duration, 3),
            "gates_count": len(selected_gates),
            "failed_count": len(failed_gates),
            "skipped_count": len(skipped_gates),
            "results": results,
        }
        print(json.dumps(payload, indent=2))
        return 0 if len(failed_gates) == 0 else 1

    print("-" * 68)
    if failed_gates:
        print(f"  FAILED: {len(failed_gates)} gate(s) failed in {total_duration:.2f}s.")
        for fg in failed_gates:
            print(f"    - {fg['name']} ({fg['script']})")
            err = fg.get("error") or fg.get("stdout")
            if err:
                first_err_line = str(err).splitlines()[0]
                print(f"      Reason: {first_err_line}")
        print("=" * 68)
        return 1

    if skipped_gates:
        names = ", ".join(str(g["name"]) for g in skipped_gates)
        print(f"  ALL {len(selected_gates) - len(skipped_gates)} QUALITY GATES PASSED"
              f" ({len(skipped_gates)} SKIPPED: {names})"
              f" (total {total_duration:.2f}s)")
    else:
        print(f"  ALL {len(selected_gates)} QUALITY GATES PASSED (total {total_duration:.2f}s)")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
