#!/usr/bin/env python3
"""assert_config_mode_pins.py -- pre-flight gate: setUp-level CONFIG patches
must pin the mode-affecting keys (freeform_mode, mock_mode).

Why (2026-09-27 incident): ``tests/test_call_chain_e2e.py`` patched
``config.CONFIG`` in setUp with ``clear=False`` but never pinned
``freeform_mode``.  The developer's real ``app_settings.json`` holds
``"freeform_mode": true`` and the preference leaked through the patch,
routing every "structured mode" test through the freeform parser
(``thinking=""``, ``final``=raw XML) and producing 6 deterministic
failures that were initially misattributed to production code.  See
the known-failures status log, 2026-09-27 entry.

Rule enforced (setUp-level sites only -- test class ``setUp`` /
``setUpClass`` methods):
  * every ``patch.dict(<CONFIG target>, <overrides>)`` call must either
    - pass ``clear=True`` (wipes the dict back to code defaults, fully
      deterministic regardless of the on-disk user settings), or
    - include BOTH ``freeform_mode`` AND ``mock_mode`` as literal keys in
      the overrides dict.
  * non-literal overrides (computed dicts, ``**kwargs``) and non-literal
    ``clear=`` arguments cannot be verified -> flagged.
  * other ``patch.dict`` targets (os.environ, other module dicts) are
    ignored.

Method-level ``@patch.dict`` decorators and ``with`` blocks inside test
bodies are OUT OF SCOPE: they sit next to the assertions they affect, so
a mode leak there is visible in the diff that introduced it.  The
dangerous pattern is a setUp-level patch whose semantics silently depend
on machine state that no test file owns.

Scope of scanned keys lives in MODE_KEYS; extend it when a new
CONFIG-backed mode flag gains parser-routing power (the failure class is
"behavior branch selected by machine-local state").

Usage:
  python scripts/assert_config_mode_pins.py           # exit 0=clean, 1=offenders
  python scripts/assert_config_mode_pins.py --json    # JSON report
"""
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

# Mode-affecting CONFIG keys whose real-app_settings.json value must never
# leak into setUp-level patches. Sorted for deterministic reporting.
MODE_KEYS = ("freeform_mode", "mock_mode")


def _is_config_target(node) -> bool:
    """True when a patch.dict() first argument targets a module CONFIG dict.

    Accepts string targets ("config.CONFIG", "tabs.config.CONFIG"),
    Name targets (``CONFIG`` imported from config), and Attribute targets
    (``config.CONFIG``).  os.environ and unrelated dicts do not match.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value == "CONFIG" or node.value.endswith("config.CONFIG")
    if isinstance(node, ast.Name):
        return node.id == "CONFIG"
    if isinstance(node, ast.Attribute):
        return node.attr == "CONFIG"
    return False


def _literal_dict_keys(call: ast.Call):
    """Return the literal string keys of the overrides dict, or None when
    the overrides are not a fully-literal dict (computed keys, {**other}
    unpacking, **kwargs)."""
    if len(call.args) < 2:
        return None
    overrides = call.args[1]
    if isinstance(overrides, ast.Dict):
        keys = set()
        for key in overrides.keys:
            if key is None:
                return None  # {**other} unpacking -> unverifiable
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
            else:
                return None  # computed key -> unverifiable
        return keys
    return None  # non-dict positional or **kwargs -> unverifiable


def _clear_flag(call: ast.Call):
    """Return the literal ``clear=`` value: True, False, or None when the
    keyword is absent.  Returns the sentinel string "unknown" when the
    argument is not a literal (cannot verify determinism)."""
    for kw in call.keywords:
        if kw.arg == "clear":
            try:
                return ast.literal_eval(kw.value)
            except (ValueError, SyntaxError):
                return "unknown"
    return None


def _is_config_patch_dict(call: ast.Call) -> bool:
    """True for patch.dict(<CONFIG target>, ...) calls.

    Accepts both ``patch.dict`` (``from unittest.mock import patch``) and
    ``mock.patch.dict`` (``import unittest.mock as mock``) spellings.
    """
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "dict"
        and bool(call.args)
        and _is_config_target(call.args[0])
    ):
        return False
    func_val = call.func.value
    if isinstance(func_val, ast.Name):
        return func_val.id == "patch"
    if isinstance(func_val, ast.Attribute):
        return func_val.attr == "patch"
    return False


def _missing_mode_keys(keys) -> list:
    if keys is None:
        return list(MODE_KEYS)
    return [key for key in MODE_KEYS if key not in keys]


def find_config_patch_violations(tests_dir=TESTS_DIR) -> list:
    """Scan tests/test_*.py setUp/setUpClass bodies for CONFIG patch.dict
    calls that neither pass clear=True nor pin every MODE_KEYS entry.

    Returns a list of offender dicts:
      {file, location, kind, line, reason, missing}
    sorted by (file, location) for deterministic output.
    """
    violations = []
    for path in sorted(Path(tests_dir).glob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            violations.append({
                "file": str(path),
                "location": "<module>",
                "kind": "file",
                "line": 0,
                "reason": "file could not be parsed",
                "missing": [],
            })
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for fn in cls.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if fn.name not in ("setUp", "setUpClass"):
                    continue
                for call in ast.walk(fn):
                    if not _is_config_patch_dict(call):
                        continue
                    location = f"{cls.name}.{fn.name}"
                    clear = _clear_flag(call)
                    if clear is True:
                        continue  # deterministic: wiped to code defaults
                    keys = _literal_dict_keys(call)
                    missing = _missing_mode_keys(keys)
                    if keys is None:
                        violations.append({
                            "file": str(path),
                            "location": location,
                            "kind": fn.name,
                            "line": call.lineno,
                            "reason": "overrides are not a fully-literal dict; pins cannot be verified",
                            "missing": missing,
                        })
                    elif missing:
                        violations.append({
                            "file": str(path),
                            "location": location,
                            "kind": fn.name,
                            "line": call.lineno,
                            "reason": "setUp-level CONFIG patch missing mode pins (user app_settings.json leaks through clear=False)",
                            "missing": missing,
                        })
                    elif clear == "unknown":
                        violations.append({
                            "file": str(path),
                            "location": location,
                            "kind": fn.name,
                            "line": call.lineno,
                            "reason": "clear= argument is not a literal; determinism cannot be verified",
                            "missing": [],
                        })
    violations.sort(key=lambda v: (v["file"], v["location"], v["line"]))
    return violations


def run_gate(tests_dir=TESTS_DIR):
    """Run the gate.  Returns (rc, violations): rc 0 clean, 1 offenders."""
    if not Path(tests_dir).is_dir():
        return 1, [{
            "file": str(tests_dir),
            "location": "<dir>",
            "kind": "dir",
            "line": 0,
            "reason": "tests directory not found",
            "missing": [],
        }]
    violations = find_config_patch_violations(tests_dir)
    return (1 if violations else 0), violations


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    tests_dir = Path(argv[0]) if argv else TESTS_DIR

    rc, violations = run_gate(tests_dir)
    if json_mode:
        print(json.dumps({"rc": rc, "offender_count": len(violations), "offenders": violations}, indent=2))
        return rc
    if rc == 0:
        print("CONFIG-MODE-PINS: clean -- every setUp-level CONFIG patch.dict pins "
              + ", ".join(MODE_KEYS) + " (or uses clear=True)")
        return 0
    print(f"CONFIG-MODE-PINS: {len(violations)} offender(s)")
    for v in violations:
        loc = f"{v['file']}:{v['line']}::{v['location']}" if v.get("line") else f"{v['file']}::{v['location']}"
        print(f"  {loc}")
        print(f"    reason: {v['reason']}")
        if v["missing"]:
            print(f"    missing pins: {', '.join(v['missing'])}")
    print("Fix: add the missing keys to the patch.dict overrides (pin the branch the "
          "assertions depend on), or use clear=True when the test wants pure code defaults.")
    print("See scripts/assert_config_mode_pins.py docstring + the known-failures status log, 2026-09-27.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
