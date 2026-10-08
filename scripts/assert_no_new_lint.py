#!/usr/bin/env python3
"""
assert_no_new_lint.py — Gate 7: static-analysis drift gate (Sprint 19.8).

Runs ``ruff check`` with the project rule set (ruff.toml) and fails only on
violations NOT present in ``scripts/lint_baseline.json``. Existing debt is
baselined (triaged on touch); NEW violations hard-fail the gate. This is
how a 23k-LOC codebase adopts linting without a 900-error wall.

Rebaseline ONLY after intentionally fixing a batch:
    python scripts/assert_no_new_lint.py --rebaseline

Exit codes:
    0  no new violations (or rebaseline succeeded)
    1  NEW violations found (fix them or, knowingly, rebaseline)
    2  ruff not runnable / unexpected tool failure

# Version: 1.0.0 — 2026-09-21 — initial release (Sprint 19.8 audit)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_PATH = os.path.join(PROJECT_ROOT, "scripts", "lint_baseline.json")


def _run_ruff() -> tuple[int, str]:
    """Run ruff in JSON format; returns (rc, stdout)."""
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format", "json"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout


def _relpath(filename: str) -> str:
    """Normalize a ruff filename to a repo-relative POSIX path.

    ruff emits ABSOLUTE paths, so a baseline keyed on them is not
    portable: in any other checkout (a fresh clone, another drive) every
    key misses and the whole baseline re-reports as NEW. Relativizing
    against PROJECT_ROOT keeps the baseline machine-independent and stops
    it publishing the author's local root path.
    """
    if not filename:
        return filename
    try:
        rel = os.path.relpath(filename, PROJECT_ROOT)
    except (ValueError, OSError):
        rel = filename
    if rel == ".." or rel.startswith(".." + os.sep):
        return filename.replace("\\", "/")
    return rel.replace("\\", "/")


def _violations() -> list[dict]:
    rc, out = _run_ruff()
    if rc not in (0, 1):  # ruff: 0=clean, 1=violations, >=2 = tool failure
        sys.stderr.write(out)
        raise SystemExit(2)
    try:
        data = json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        sys.stderr.write("ruff produced non-JSON output — tool failure.\n")
        raise SystemExit(2)
    for v in data:
        if "filename" in v:
            v["filename"] = _relpath(v["filename"])
    return data


def _key(v: dict) -> tuple:
    # path:code:logical symbol; message text is ignored so trivial rewordings
    # between ruff versions do not re-trigger baselined debt.
    return (v.get("filename", ""), v.get("code", ""),
            (v.get("logical_location") or {}).get("name", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate 7: no NEW lint violations")
    parser.add_argument("--rebaseline", action="store_true",
                        help="overwrite the baseline with current violations")
    args = parser.parse_args()

    current = _violations()
    if args.rebaseline:
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=1)
        print(f"Rebaselined {len(current)} violations -> {BASELINE_PATH}")
        return 0

    if not os.path.exists(BASELINE_PATH):
        print(f"Baseline missing: {BASELINE_PATH}\n"
              f"Run once: python scripts/assert_no_new_lint.py --rebaseline")
        return 2

    with open(BASELINE_PATH, encoding="utf-8") as f:
        baseline = json.load(f)

    from collections import Counter
    base_counts = Counter(map(_key, baseline))
    curr_counts = Counter(map(_key, current))

    new = []
    for key, count in curr_counts.items():
        extra = count - base_counts.get(key, 0)
        if extra > 0:
            new.extend([v for v in current if _key(v) == key][-extra:])

    if not new:
        print(f"Gate 7 CLEAN — {len(current)} baselined, 0 new lint violations.")
        return 0

    print(f"Gate 7 FAILED — {len(new)} NEW lint violation(s):\n")
    for v in new[:40]:
        loc = os.path.relpath(v.get("filename", ""), PROJECT_ROOT)
        print(f"  {loc}:{v.get('location', {}).get('row')}  "
              f"[{v.get('code')}]  {v.get('message')}")
    if len(new) > 40:
        print(f"  ... and {len(new) - 40} more.")
    print("\nFix the violations, or (knowingly) rebaseline with --rebaseline.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
