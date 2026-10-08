#!/usr/bin/env python3
"""lint_pipe_starvation.py - Scan for pytest | tail -N and similar pipe-starvation patterns.

Pytest in -q (quiet) mode only outputs 2 lines (dots + summary).
Piping to ``tail -15`` (or any ``tail -N`` with N > 2) blocks indefinitely
waiting for enough input lines - causing false "timeout" hangs in CI and
ad-hoc test commands.

This script scans the project for:
  - .bat / .sh files with pytest | tail/head/findstr -<count>
  - docs/ files with documented pytest | tail patterns
  - Any other script files that pipe pytest to a line-limited command

Usage:
    python scripts/lint_pipe_starvation.py          # scan project
    python scripts/lint_pipe_starvation.py --ci      # exit 1 on match (CI gate)

Exit codes:
    0 - no issues found
    1 - pipe-starvation patterns found (or --ci mode)
"""

import os
import re
import sys


PIPE_PATTERNS = [
    (r"pytest.*\|.*tail\s+-[1-9]", "pytest | tail -N (N>0 blocks on quiet output)"),
    (r"pytest.*\|.*head\s+-[1-9]", "pytest | head -N (N>0 blocks on sparse output)"),
    (r"pytest.*\|.*findstr\s+-[1-9]", "pytest | findstr -N (N lines may starve)"),
    (r"pytest.*\|.*grep\s+-m\s*[1-9]", "pytest | grep -m N (N matches may starve)"),
    (r"pytest.*\|.*select-string\s+-[1-9]", "pytest | select-string -N (PowerShell may starve)"),
]


SCAN_EXTENSIONS = {".bat", ".sh", ".ps1", ".py", ".md", ".yml", ".yaml", ".ini", ".cfg", ".toml"}

SKIP_DIRS = {"__pycache__", ".git", ".tmp.driveupload", "Backups", "Python", "voice_models",
             "node_modules", "settings_backups", "data", "cache", "captures", "badges",
             "models", "plugins", "services", "cli", "tabs"}

SAFE_PATTERNS = [
    r"pytest.*--collect-only.*\|.*tail\s+-2\b",
]


def _is_safe_pattern(line: str) -> bool:
    for pattern in SAFE_PATTERNS:
        if re.search(pattern, line, re.IGNORECASE):
            return True
    return False


def scan_file(filepath: str) -> list[dict]:
    issues = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, PermissionError) as e:
        return [{"file": filepath, "line": 0, "message": f"Cannot read: {e}"}]

    for i, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("#", "REM ", "@rem", "::", "//")):
            continue
        if not re.search(r"pytest", line, re.IGNORECASE):
            continue
        if _is_safe_pattern(line):
            continue

        for pattern, description in PIPE_PATTERNS:
            if re.search(pattern, line, re.IGNORECASE):
                issues.append({
                    "file": filepath,
                    "line": i,
                    "message": f"{description}: {line[:120]}",
                })
                break

    return issues


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ci_mode = "--ci" in sys.argv
    all_issues = []

    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SCAN_EXTENSIONS:
                continue
            filepath = os.path.join(root, fname)
            issues = scan_file(filepath)
            all_issues.extend(issues)

    if all_issues:
        print(f"=== PIPE-STARVATION LINT: {len(all_issues)} issue(s) found ===")
        print()
        for issue in sorted(all_issues, key=lambda x: (x["file"], x["line"])):
            relpath = os.path.relpath(issue["file"], project_root)
            if issue["line"] > 0:
                print(f"  {relpath}:{issue['line']}")
            else:
                print(f"  {relpath}")
            print(f"    {issue['message']}")
            print()
    else:
        print("OK: No pipe-starvation patterns found.")

    if ci_mode:
        # CI mode: exit 1 if ANY issues found, otherwise 0
        msg = "FAIL" if all_issues else "PASS"
        print(f"[lint_pipe_starvation] {msg}: {len(all_issues)} issue(s)")
        sys.exit(1 if all_issues else 0)
    else:
        sys.exit(1 if all_issues else 0)


if __name__ == "__main__":
    main()
