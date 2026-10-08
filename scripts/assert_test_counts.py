#!/usr/bin/env python3
"""assert_test_counts.py -- drift-detector for per-class test counts
in tests/test_assert_section11_render_clean.py.

Verifies the 5 test classes have NOT silently lost test methods from a
bad str_replace / regex walker / automated-refactor cascade.
Uses "expected >= N" so ADDING tests does not break the gate, but
DELETING existing tests triggers it.

Usage:
  python scripts/assert_test_counts.py           # exit 0=clean, 1=offenders
  python scripts/assert_test_counts.py --json    # JSON report
"""
import ast, json, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_FILE = REPO_ROOT / "tests" / "test_assert_section11_render_clean.py"

EXPECTED_COUNTS = {
    "TestFindCodeSpans": 10,
    "TestFindPipeViolations": 7,
    "TestFindBlockquoteBreakViolations": 6,
    "TestFindBareBackslashViolations": 11,
    "TestIntegrationFixture": 3,
}
EXPECTED_TOTAL = sum(EXPECTED_COUNTS.values())  # 37


def _parse_test_classes(file_path=TEST_FILE):
    """Parse the test file with AST and return {class_name: [test_methods]}."""
    if not file_path.exists():
        return {}
    try:
        text = file_path.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (SyntaxError, OSError):
        return {}
    classes = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            methods = sorted(
                n.name for n in node.body
                if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")
            )
            classes[node.name] = methods
    return classes


def find_missing_test_methods(file_path=TEST_FILE):
    """Return {class_name: {expected_min, actual, missing}} for regressions."""
    classes = _parse_test_classes(file_path)
    if not classes:
        return {"_FILE_MISSING": {"expected_min": EXPECTED_TOTAL, "actual": 0, "missing": EXPECTED_TOTAL}}
    offenders = {}
    total_actual = 0
    for cls_name, expected_min in EXPECTED_COUNTS.items():
        actual = len(classes.get(cls_name, []))
        total_actual += actual
        if actual < expected_min:
            offenders[cls_name] = {"expected_min": expected_min, "actual": actual, "missing": expected_min - actual}
    for cls_name in EXPECTED_COUNTS:
        if cls_name not in classes and cls_name not in offenders:
            offenders[cls_name] = {"expected_min": EXPECTED_COUNTS[cls_name], "actual": 0, "missing": EXPECTED_COUNTS[cls_name]}
    if total_actual < EXPECTED_TOTAL:
        offenders["_TOTAL"] = {"expected_min": EXPECTED_TOTAL, "actual": total_actual, "missing": EXPECTED_TOTAL - total_actual}
    return offenders


def assert_test_class_counts(file_path=TEST_FILE):
    """Pre-flight gate: raises AssertionError on any regression."""
    offenders = find_missing_test_methods(file_path)
    if not offenders:
        return
    lines = []
    for cls_name, info in sorted(offenders.items()):
        if cls_name == "_TOTAL":
            lines.append("  TOTAL: expected >= %d, got %d (missing %d)" % (info["expected_min"], info["actual"], info["missing"]))
        elif cls_name == "_FILE_MISSING":
            lines.append("  FILE NOT FOUND: %s" % str(file_path))
        else:
            lines.append("  %s: expected >= %d, got %d (missing %d test methods)" % (cls_name, info["expected_min"], info["actual"], info["missing"]))
    raise AssertionError(
        "Test-count drift gate: %d regressions in %s.\n"
        "  A bad str_replace / regex walker / automated-refactor cascade may have silently deleted test methods.\n"
        "  Restore the file from a known-good source, then re-verify with pytest.\n\n%s"
        % (len(offenders), file_path.name, "\n".join(lines))
    )


def _summary(file_path=TEST_FILE):
    """Return (exit_code, payload_dict) for --json and CLI reporting."""
    classes = _parse_test_classes(file_path)
    offenders = find_missing_test_methods(file_path)
    total_actual = sum(len(v) for v in classes.values())
    per_class = {}
    for cls_name, expected_min in EXPECTED_COUNTS.items():
        actual = len(classes.get(cls_name, []))
        per_class[cls_name] = {"expected_min": expected_min, "actual": actual, "ok": actual >= expected_min}
    any_failures = len(offenders) > 0
    return (
        1 if any_failures else 0,
        {
            "test_file": str(file_path),
            "expected_total_min": EXPECTED_TOTAL,
            "total_actual": total_actual,
            "total_ok": total_actual >= EXPECTED_TOTAL,
            "per_class": per_class,
            "offenders": {k: v for k, v in offenders.items() if k != "_FILE_MISSING"},
            "offender_count": len(offenders),
            "file_missing": "_FILE_MISSING" in offenders,
        },
    )


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    if "--json" in argv:
        ec, payload = _summary()
        print(json.dumps(payload, indent=2))
        return ec
    ec, payload = _summary()
    if ec == 0:
        parts = ["%s: %d" % (c, payload["per_class"][c]["actual"]) for c in EXPECTED_COUNTS]
        print("TEST-COUNT-GATE: %d total test methods (expected >= %d); %s. Clean." % (payload["total_actual"], EXPECTED_TOTAL, "; ".join(parts)))
        return 0
    print("TEST-COUNT-GATE: build fails. %d regression(s); total=%d (expected >= %d)." % (payload["offender_count"], payload["total_actual"], EXPECTED_TOTAL))
    print()
    if payload["file_missing"]:
        print("--- file not found ---")
        print("  %s" % TEST_FILE)
        print()
    for cls_name, info in sorted(payload["offenders"].items()):
        label = "TOTAL" if cls_name == "_TOTAL" else cls_name
        print("  %s: expected >= %d, got %d (missing %d)" % (label, info["expected_min"], info["actual"], info["missing"]))
    print()
    print("A bad str_replace / regex walker / automated-refactor cascade")
    print("may have silently deleted test methods. Restore the file from")
    print("a known-good source, then re-verify with pytest.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
