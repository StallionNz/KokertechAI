#!/usr/bin/env python3
"""Gate 9 (SILENT-CATCH DRIFT): fail only on NEW silent except handlers.

Mirrors Gate 7 (LINT-DRIFT): existing classified sites live in
``scripts/silent_catch_baseline.json``; this gate fails when a silent site
appears that is NOT in the baseline. Fix the site (breadcrumb or documented
marker) or, knowingly, rebaseline with ``--rebaseline``.

A "silent site" is a bare pass/ellipsis except handler with no log call and
no documented-justification marker, outside cleanup/teardown context (full
taxonomy in the classifier embedded below, shared with
``.scratch/audit_silent_catches.py``).
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "scripts" / "silent_catch_baseline.json"
EXCLUDE = {".venv", ".tox", "Backups", "_scratch", ".scratch", "_runs", "docs",
           "node_modules", "vscode-freebuff-agents", "__pycache__", ".git",
           "models", "voice_models", "data", "settings_backups", ".agents",
           "conftest.py"}

DOC_RE = re.compile(
    r"(silent[- ]catch|silent-catch audit|intentional|deliberate|by design|"
    r"best[- ]effort|non[- ]fatal|already swallows|ignore_errors|teardown guard|"
    r"cleanup|degenerate fallback|graceful fallback|nothing to guard|"
    "nothing to clean|no-op|probe|optional dependency|optional dep|"
    r"feature detection|fallback|fail-soft|fall through|degrade contract|"
    r"recursion|spam)",
    re.I,
)
CLEANUP_CTX_RE = re.compile(
    r"(teardown|cleanup|close|shutdown|dispose|rollback|kill|stop|terminate|finally)",
    re.I,
)
LOG_NAMES = {"warning", "debug", "error", "info", "exception", "log", "critical"}


def targets() -> list[Path]:
    out: list[Path] = []
    for pattern in ("*.py", "tabs/*.py", "services/*.py", "plugins/*.py", "cli/*.py"):
        for p in REPO.glob(pattern):
            if not any(part in EXCLUDE for part in p.parts):
                out.append(p)
    return sorted(set(out))


def is_logged(body: list[ast.stmt]) -> bool:
    for node in body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                if isinstance(fn, ast.Attribute) and fn.attr in LOG_NAMES:
                    return True
                if isinstance(fn, ast.Name) and fn.id in LOG_NAMES:
                    return True
    return False


def is_fallback(body: list[ast.stmt]) -> bool:
    for stmt in body:
        if isinstance(stmt, (ast.Return, ast.Raise, ast.Assign, ast.AugAssign,
                             ast.AnnAssign, ast.Continue, ast.Break)):
            return True
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            return True
    return False


def silent_sites() -> list[dict[str, object]]:
    sites: list[dict[str, object]] = []
    for path in targets():
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except (OSError, SyntaxError, ValueError):
            continue
        lines = src.splitlines()
        fn_of: dict[int, str] = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(fn):
                    if isinstance(sub, ast.ExceptHandler):
                        fn_of.setdefault(id(sub), fn.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            first, last = node.body[0], node.body[-1]
            handler_src = "\n".join(
                lines[(node.lineno or 1) - 3:(last.end_lineno or first.lineno) + 1])
            if is_logged(node.body) or DOC_RE.search(handler_src) or is_fallback(node.body):
                continue
            is_bare = all(isinstance(s, ast.Pass) for s in node.body) or all(
                isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                and s.value.value is Ellipsis for s in node.body)
            fn_name = fn_of.get(id(node), "<module>")
            if is_bare and (CLEANUP_CTX_RE.search(fn_name)
                            or CLEANUP_CTX_RE.search(handler_src)):
                continue
            exc = ast.unparse(node.type) if node.type else "<bare>"
            sites.append({
                "file": str(path.relative_to(REPO)).replace("\\", "/"),
                "line": node.lineno,
                "exc": exc,
                "fn": fn_name,
            })
    return sites


def site_keys(sites: list[dict[str, object]]) -> list[str]:
    """Line-stable keys: file:fn:exc, with #N suffix only on duplicates.

    Line numbers would false-positive on any edit above a site; the fn+exc
    pair is stable under upstream edits. Duplicates within the same fn get
    occurrence indices in sorted-source order.
    """
    counts: dict[str, int] = {}
    keyed: list[tuple[int, str]] = []
    for s in sites:
        base = f"{s['file']}:{s['fn']}:{s['exc']}"
        n = counts.get(base, 0)
        counts[base] = n + 1
        key = base if n == 0 else f"{base}#{n}"
        keyed.append((int(s["line"]), key))  # type: ignore[arg-type]
    keyed.sort()
    return [k for _, k in keyed]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebaseline", action="store_true",
                        help="overwrite the baseline with current silent sites")
    args = parser.parse_args()

    current = silent_sites()
    keys_list = site_keys(current)
    keys = set(keys_list)
    key_to_site: dict[str, dict[str, object]] = {}
    counts: dict[str, int] = {}
    for s in sorted(current, key=lambda x: int(x["line"])):  # type: ignore[arg-type]
        base = f"{s['file']}:{s['fn']}:{s['exc']}"
        n = counts.get(base, 0)
        counts[base] = n + 1
        key = base if n == 0 else f"{base}#{n}"
        key_to_site[key] = s

    if args.rebaseline or not BASELINE.is_file():
        BASELINE.write_text(
            json.dumps(keys_list, indent=1), encoding="utf-8"
        )
        print(f"Gate 9 baselined: {len(keys)} silent sites recorded.")
        return 0

    baselined = set(json.loads(BASELINE.read_text(encoding="utf-8")))
    new = sorted(k for k in keys if k not in baselined)
    fixed = len(baselined - keys)
    if new:
        print(f"Gate 9 FAILED — {len(new)} NEW silent-catch site(s) "
              f"({fixed} previously baselined now gone):")
        for k in new:
            s = key_to_site[k]
            print(f"  {s['file']}:{s['line']}  [{s['exc']}]  in {s['fn']}()")
        print("\nFix the sites (breadcrumb or documented marker), "
              "or rebaseline with --rebaseline.")
        return 1
    print(f"Gate 9 CLEAN — {len(baselined)} baselined, 0 new silent-catch sites "
          f"({fixed} baselined sites resolved).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
