#!/usr/bin/env python3
"""assert_singleton_reset_pattern.py -- Sprint 17 R12 pre-flight CI gate.

Enforces the project-wide singleton-reset invariant first documented in
KNOWLEDGE.md §10 "Project-Wide Singleton Convention (Sprint 17 R12)":

    Every module-level singleton MUST ship with a paired
    ``_reset_X_for_tests()`` helper AND a conftest wire-up that
    auto-invokes it from ``_reset_all_shared_state()`` →
    ``_reset_services_registry()`` chain. Future singleton-bearing
    modules (e.g., Sprint 18's AgentLoopFactory) get this convention
    by default rather than re-discovering the cross-test pollution
    gap from scratch.

Three pure-function finders + ``__main__`` aggregator. Exits 0 on clean /
1 on offenders. Mirrors the ``scripts/assert_section11_render_clean.py``
pattern so future pre-flight automation can append this gate to any
runner that already invokes the assert_section11 / assert_freebuff /
assert_known_anchors / assert_test_counts sequence.

Usage:
    python scripts/assert_singleton_reset_pattern.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_project_root() -> Path:
    """Resolve the project root from ``--project-root`` CLI flag or cwd.

    When running the gate as a subprocess inside a temp directory (e.g.
    ``test_offending_project_root_exits_1``), the subprocess cwd takes
    precedence over the script's own file location so the gate scans the
    synthetic test project rather than the real one.
    """
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project-root", type=Path, default=None)
    args, _ = parser.parse_known_args()
    if args.project_root is not None:
        return args.project_root.resolve()
    return Path.cwd().resolve()

# ─── Regex patterns ──────────────────────────────────────────────────────────
# Match module-level singleton declarations:
#   _SINGLETON = None        _FACTORY_SINGLETON = None
#   _AGENT_LOOP_SINGLETON: Optional[X] = None
#   _LAZY_INSTANCE = None    _GLOBAL_INSTANCE: int = 0
# Controlled:
#   - Anchored to line start (^...MULTILINE) so it can't match mid-line assignments.
#   - Prefix is UPPERCASE so module-level _logger = ... doesn't match.
_SINGLETON_NAME_RE = re.compile(
    r"^_(?P<prefix>[A-Z][A-Z0-9_]*?_)?(?P<suffix>SINGLETON|INSTANCE)"
    r"\s*:?\s*[A-Za-z\[\], ]*=\s*",
    re.MULTILINE,
)

# Match `_reset_X_for_tests()` function definitions.
_RESET_FOR_TESTS_RE = re.compile(
    r"^def\s+_reset_(?P<name>[a-z][a-z0-9_]*)_for_tests\s*\(",
    re.MULTILINE,
)

# Dual-conftest-file trap detection is AST-based (see
# `find_dual_conftest_import_violations` below) -- a regex-based scan
# false-positives on docstrings/comment text that explains the canonical
# workaround (e.g., `tests/test_code_intelligence_factory.py` mentions
# the exact phrase `from conftest import` in its docstring). AST walking
# only flags real runtime `import conftest` / `from conftest import ...`
# statements -- which is what the dual-conftest-file trap actually fires
# on.

# SKIP_DIRS for check2 scan.
_SKIP_DIRS = (
    "tests/", "_scratch/", ".git/", "Backups/", "backups/",
    "vscode-freebuff-agents/", "_diag/", "_runs/",
)


def _candidate_module_files(project_root: Path) -> List[Path]:
    """Return files that may legitimately own a module-level singleton.

    Restricted to ``services/`` ONLY per KNOWLEDGE.md §10 Sprint 17 R12
    convention. The discriminator is "owns a singleton that flows through
    ``ServiceRegistry``" -- workflow modules wrap their resets in the
    factory's helper rather than at the workflow layer, so they don't need
    their own candidates here (workflow singleton pollution cannot reach
    the registry without going through ``register_instance``). Tabs /
    plugins / workflow modules do NOT own singletons; they read from
    the registry.
    """
    out: List[Path] = []
    services_dir = project_root / "services"
    if services_dir.exists():
        out.extend(sorted(services_dir.glob("*.py")))
    return out


# ─── Check 1: singleton has paired helper in SAME file ────────────────────
def find_singleton_without_reset_helper(
    project_root: Path,
) -> List[Tuple[Path, int, str]]:
    """Scan services/ + canonical singleton homes. For every module-level
    _X_SINGLETON / _X_INSTANCE declared, verify the SAME file defines a
    _reset_X_for_tests() function. Violations return as
    (file, lineno, reason) tuples.
    """
    out: List[Tuple[Path, int, str]] = []
    for f in _candidate_module_files(project_root):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        singleton_matches = list(_SINGLETON_NAME_RE.finditer(text))
        if not singleton_matches:
            continue

        reset_helpers = list(_RESET_FOR_TESTS_RE.finditer(text))
        helper_names = {hm.group("name") for hm in reset_helpers}

        if not helper_names:
            for sm in singleton_matches:
                line_no = text[: sm.start()].count("\n") + 1
                prefix = sm.group("prefix") or ""
                suffix = sm.group("suffix")
                out.append((
                    f,
                    line_no,
                    f"singleton `_{prefix}{suffix}` declared at line {line_no} "
                    f"with NO `_reset_X_for_tests()` helper in the same file -- "
                    f"violates project-wide convention (see KNOWLEDGE.md §10 "
                    f"Sprint 17 R12)",
                ))
    return out


# ─── Check 2: helper is auto-invoked from conftest chain ───────────────────
def find_reset_helper_without_conftest_wireup(
    project_root: Path,
) -> List[Tuple[Path, int, str]]:
    """Scan all .py files (excluding tests/_scratch/backups) for
    _reset_X_for_tests() definitions. Verify each helper is invoked in
    conftest.py's body (the _reset_services_registry() /
    _reset_all_shared_state() chain). Unwired helpers are flagged.
    """
    out: List[Tuple[Path, int, str]] = []

    definitions: List[Tuple[Path, str]] = []
    for f in project_root.rglob("*.py"):
        rel_str = str(f.relative_to(project_root).as_posix())
        if any(rel_str.startswith(p) for p in _SKIP_DIRS):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _RESET_FOR_TESTS_RE.finditer(text):
            definitions.append((f, m.group("name")))

    if not definitions:
        return out

    conftest_path = project_root / "conftest.py"
    try:
        conftest_text = conftest_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        for f, name in definitions:
            out.append((f, 0, f"`_reset_{name}_for_tests()` defined in "
                          f"`{f.relative_to(project_root)}` but conftest.py "
                          f"missing/unreadable -- cannot verify wire-up"))
        return out

    for f, name in definitions:
        needle = f"_{name}_for_tests("
        if needle in conftest_text:
            continue
        out.append((
            f,
            0,
            f"`_reset_{name}_for_tests()` defined in "
            f"`{f.relative_to(project_root)}` but NOT invoked anywhere in "
            f"conftest.py -- will leave module-level singleton state intact "
            f"across pytest_runtest_teardown and pollute subsequent tests. "
            f"Add a try/except: pass block under conftest."
            f"_reset_services_registry() that imports + invokes the helper.",
        ))
    return out


# ─── Check 3: dual-conftest-file trap detection (AST-based) ──────────────────
def find_dual_conftest_import_violations(
    project_root: Path,
) -> List[Tuple[Path, int, str]]:
    """AST-based scan of ``tests/*.py`` for `import conftest` / `from conftest import ...`.

    Surfaces only REAL runtime ``Import``/``ImportFrom`` statements --
    ignores text inside docstrings/comments that explain the canonical
    workaround (``importlib.util.spec_from_file_location``). Covers:
      - ``import conftest``
      - ``import conftest as foo``
      - ``import conftest.something``
      - ``from conftest import X`` (any form)
      - ``from conftest.something import X``
      - ``from .conftest import X`` / ``from ..conftest import X`` (relative)

    The AST walker is the right tool here because real imports bind at
    runtime regardless of source position, whereas regex would false-
    positive on docstring text that mentions the trap by name.
    """
    out: List[Tuple[Path, int, str]] = []
    tests_dir = project_root / "tests"
    if not tests_dir.exists():
        return out

    for f in sorted(tests_dir.glob("*.py")):
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(source, filename=str(f))
        except SyntaxError:
            # Unparseable Python files shouldn't crash the gate; skip them.
            continue

        for node in ast.walk(tree):
            rel = f.relative_to(project_root)
            if isinstance(node, ast.Import):
                # `import conftest` / `import conftest as foo` /
                # `import conftest.something`
                for alias in node.names:
                    if alias.name == "conftest" or alias.name.startswith("conftest."):
                        out.append((
                            f,
                            node.lineno,
                            f"`import {alias.name}` at line {node.lineno} in "
                            f"`{rel}` -- project has BOTH "
                            f"`{PROJECT_ROOT.name}/conftest.py` AND "
                            f"`tests/conftest.py`. pytest's `sys.modules` cache "
                            f"resolves `conftest` to `tests/conftest.py`, the "
                            f"WRONG file. Use "
                            f"`importlib.util.spec_from_file_location(\"_unique_test_only_name\", "
                            f"<project_root> / \"conftest.py\")` to bypass the collision.",
                        ))
            elif isinstance(node, ast.ImportFrom):
                # `from conftest import X` / `from .conftest import X`
                # / `from conftest.something import X`
                if (node.module == "conftest"
                        or (node.module and node.module.startswith("conftest."))):
                    out.append((
                        f,
                        node.lineno,
                        f"`from {node.module or '?'} import ...` at line "
                        f"{node.lineno} in `{rel}` -- project has BOTH "
                        f"`{PROJECT_ROOT.name}/conftest.py` AND "
                        f"`tests/conftest.py`. pytest's `sys.modules` cache "
                        f"resolves `conftest` to `tests/conftest.py`, the "
                        f"WRONG file. Use "
                        f"`importlib.util.spec_from_file_location(\"_unique_test_only_name\", "
                        f"<project_root> / \"conftest.py\")` to bypass the collision.",
                    ))
    return out


# ─── Aggregator + entry point ──────────────────────────────────────────────
def main() -> int:
    project_root = _resolve_project_root()
    print(f"=== Sprint 17 R12 Singleton Reset Pattern Gate (project_root={project_root}) ===\n")
    all_offenders: List[Tuple[Path, int, str]] = []
    check_specs = (
        ("check1_singleton_without_reset_helper",
         find_singleton_without_reset_helper),
        ("check2_reset_helper_without_conftest_wireup",
         find_reset_helper_without_conftest_wireup),
        ("check3_dual_conftest_import_violations",
         find_dual_conftest_import_violations),
    )
    for check_name, check_fn in check_specs:
        offenders = check_fn(project_root)
        if offenders:
            print(f"[{check_name}] {len(offenders)} offender(s):")
            for f, line_no, reason in offenders:
                try:
                    rel = f.resolve().relative_to(project_root)
                except ValueError:
                    rel = str(f)
                line_str = f":{line_no}" if line_no else ""
                print(f"  {rel}{line_str}: {reason}")
            all_offenders.extend(offenders)
        else:
            print(f"[{check_name}] CLEAN")

    if all_offenders:
        print(f"\n=== FAILED: {len(all_offenders)} total offender(s) ===")
        print("Fix: see KNOWLEDGE.md §10 Project-Wide Singleton Convention "
              "(Sprint 17 R12).")
        return 1
    print("\n=== CLEAN: all 3 invariants pass ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
