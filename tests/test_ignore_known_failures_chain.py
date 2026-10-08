"""Integration test: --ignore-known-failures chain end-to-end.

The chain has 4 links: .bat -> run_test_suite.py -> pytest -> conftest deselect.
Each link can break independently under refactor:
  - _scratch/run_tests.bat -- someone could remove the %* forwarder.
  - _scratch/run_test_suite.py -- someone could refactor away the sys.argv
    detection that conditionally appends the flag to the pytest cmd list.
  - _scratch/run_tests_batched.py -- someone could rename or remove the
    argparse --ignore-known-failures option.
  - conftest.py -- someone could alter or remove the pytest_collection_modifyitems
    deselect logic.
  - pytest.ini -- someone could delete the known_failure: marker registration.

This file's design:
  - Canary (test_canary_marker_test) carries @pytest.mark.known_failure
    AND is named with the `test_` prefix so pytest's default
    `python_functions = test_*` collection pattern picks it up.
  - One test per link so a future break surfaces as a single failed test.

The .bat pre-flight gates (assert_section11_render_clean.py etc.) are NOT
exercised here because they do not exist on this machine; they fail with
BUILD-ABORT before pytest ever runs. That is filed separately.

Reference: K5 implementation details + K5 follow-up + K5
follow-up-2 + K5 follow-up-3 (this file).
"""

import os
import re
import subprocess
import sys

import pytest


# Test data constants

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THIS_FILE = os.path.abspath(__file__)
RUN_TESTS_BAT = os.path.join(ROOT, "_scratch", "run_tests.bat")
RUN_TEST_SUITE_PY = os.path.join(ROOT, "_scratch", "run_test_suite.py")
RUN_TESTS_BATCHED_PY = os.path.join(ROOT, "_scratch", "run_tests_batched.py")

PYTEST_INVOKE_TIMEOUT = 300  # pytest subprocess timeout (seconds) — matches pytest.ini timeout=300 so the recursive subprocess budget never expires before the test-thread's own timeout.
RUNNER_INVOKE_TIMEOUT = 300  # per-batch runner timeout (seconds)


# Helper for constructing a portable Node ID that works on Windows.
# `os.path.abspath(__file__)` returns backslashes on Windows, and pytest
# rejects backslash-form absolute paths in `path::name` selectors (even
# though the relative-path version resolves). Compute the relative path
# from ROOT and replace `\` with `/` for pytest's preferred selector form.
THIS_FILE_REL = os.path.relpath(THIS_FILE, ROOT).replace("\\", "/")


# The canary

# Self-reference marker: any refactor of the canary (rename, drop the
# @pytest.mark.known_failure decorator, nest it inside a TestClass, etc.)
# will fail loud via test_self_canary_is_marked_with_known_failure at the
# bottom of this file. Don't move this test.
#
# Naming: must keep the `test_` prefix so pytest's default
# `python_functions = test_*` collection pattern picks it up. Without that
# prefix, conftest.py's pytest_collection_modifyitems would never see the
# canary in its `items` list (it'd be excluded BEFORE conftest's hook
# fires), defeating every chain-link test below.

@pytest.mark.known_failure
def test_canary_marker_test():
    """Marked @pytest.mark.known_failure. Asserts True so it always passes
    when actually run. Under --ignore-known-failures conftest.py adds a
    pytest.mark.skip(reason=...) to it so it appears as s (skipped).
    This is the data point the conftest + arg-detection tests operate on."""
    assert True


# Helpers

def _clean_env():
    """Build subprocess env that EXPLICITLY clears PYTEST_ADDOPTS.

    Without this, the subprocess pytest inheriting os.environ would pick up
    any PYTEST_ADDOPTS set by the host/CI (e.g. `--ignore-known-failures`),
    which would silently invert the flag-detection behavior of
    `test_conftest_does_not_skip_without_flag` (subprocess would skip the
    canary even though argv doesn't have the flag). The corollary is
    false-positive PROPAGATION: a CI env fix to `PYTEST_ADDOPTS` could
    break this test for the wrong reason.
    """
    env = os.environ.copy()
    env["PYTEST_ADDOPTS"] = ""  # empty == no addopts
    return env


# REGRESSION GUARD (Sprint 21.1 Phase 2): pytest-coverage-disable argv flags
# (the CLI modifiers pytest-cov normally registers when its plugin loads) are FORBIDDEN
# in inner-subprocess pytest argvs. pytest.ini deliberately disables pytest-cov via
# `-p no:cov` (July 2026 fd-exhaustion fix), so those CLI flags are NOT registered in
# argparse. Passing them to a subprocess pytest rejects as `unrecognized arguments: <pytest-cov-flag>`
# (uniform across parent and subprocess — no plugin-loading asymmetry). Cross-reference:
# Resolution Log row dated 2026-07-21 + CHANGELOG.md v0.20.1.
def _run(cmd, timeout=PYTEST_INVOKE_TIMEOUT, cwd=None):
    """Invoke a subprocess with robust pipe handling.

    Uses explicit ``subprocess.PIPE`` with ``close_fds=True``, ``stdin``
    redirected to ``DEVNULL``, and ``creationflags=CREATE_NO_WINDOW`` on
    Windows to prevent the child process from inheriting the parent's
    console or hanging on pipe buffer exhaustion (Python 3.14 Windows
    subprocess stability).
    """
    kwargs = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=timeout, cwd=cwd or ROOT,
        env=_clean_env(), close_fds=True,
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, errors="replace", **kwargs)


def _combined_output(r):
    return (r.stdout or "") + "\n" + (r.stderr or "")


def _has_count_line(out, expected_count, keyword):
    pattern = r"\b" + str(expected_count) + r"\s+" + keyword + r"\b"
    return bool(re.search(pattern, out))


# Tests: conftest.py deselect round-trip (link #4 of chain)

def test_conftest_deselects_known_failure_when_flag_set():
    """With --ignore-known-failures, conftest.py deselect logic must convert
    the canary's @pytest.mark.known_failure into s (skipped).

    Targets ONLY the canary via its Node ID (relative-path + forward-slash
    form) so the inner subprocess pytest runs exactly 1 test. This breaks
    what would otherwise be INFINITE RECURSION: invoking pytest on THIS_FILE
    here would re-collect every test (including this very test), and
    without `--ignore-known-failures` set on the inner invocation, the
    deselect hook would skip nothing and this test would re-run forever."""
    r = _run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "--color=no",
         "--ignore-known-failures",
         f"{THIS_FILE_REL}::test_canary_marker_test"],
        timeout=PYTEST_INVOKE_TIMEOUT,
    )
    out = _combined_output(r)
    assert _has_count_line(out, 1, "skipped"), (
        "Expected 1 skipped (the canary); got:\n" + out
    )


def test_conftest_does_not_skip_without_flag():
    """Without --ignore-known-failures, the canary's marker must NOT become
    a skip. Catches a regression where the deselect becomes unconditional
    (e.g. someone removes the flag guard).

    CRITICAL: targets the canary Node ID, NOT THIS_FILE, to avoid the
    infinite recursion explained in
    test_conftest_deselects_known_failure_when_flag_set's docstring.
    Targeting a single leaf test breaks the recursion at depth=1 and drops
    the inner subprocess wall-time to <1s."""
    r = _run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no", "--color=no",
         f"{THIS_FILE_REL}::test_canary_marker_test"],
        timeout=PYTEST_INVOKE_TIMEOUT,
    )
    out = _combined_output(r)
    # Pytest omits "0 skipped" from its summary line when no tests skip
    # (in pytest 9.x), but other pytest versions or verbose modes may emit
    # an explicit `0 skipped` line. So we match only NON-ZERO `N skipped`
    # counts — a `[1-9]\d*\s+skipped` match would prove the deselect hook
    # ran without its --ignore-known-failures flag guard. `0 skipped`, if
    # it ever appears, is benign (it IS the proof no skip happened).
    assert not re.search(r"[1-9]\d*\s+skipped", out), (
        "Expected NO 'N skipped' (N>0) when flag is unset "
        "(regression: deselect hook ran without its flag guard); got:\n" + out
    )
    assert _has_count_line(out, 1, "passed"), (
        "Expected 1 passed (just the canary ran); got:\n" + out
    )


# Tests: run_test_suite.py sys.argv detection (link #2 of chain)

def test_run_test_suite_summary_acknowledges_flag_when_set():
    """When _scratch/run_test_suite.py receives --ignore-known-failures in
    sys.argv, it must (1) append the flag to every batch's pytest cmd,
    (2) emit a `  --ignore-known-failures: ...` SUMMARY confirmation line.
    We run --quick and check for the SUMMARY line -- proof both halves fired
    (they share the same `if ignore_known_failures:` branch)."""
    r = _run(
        [sys.executable, RUN_TEST_SUITE_PY, "--quick", "--ignore-known-failures"],
        timeout=RUNNER_INVOKE_TIMEOUT,
    )
    out = _combined_output(r)
    assert "SUMMARY:" in out, "run_test_suite.py SUMMARY block missing; got:\n" + out
    assert "--ignore-known-failures:" in out, (
        "run_test_suite.py missed `  --ignore-known-failures:` line; "
        "SUMMARY acknowledges but cmd propagation may be broken. Got:\n" + out
    )


def test_run_test_suite_summary_omits_line_when_flag_unset():
    """Without the flag, the `  --ignore-known-failures:` SUMMARY line must
    NOT appear -- confirms default OFF (strict runs unchanged)."""
    r = _run(
        [sys.executable, RUN_TEST_SUITE_PY, "--quick"],
        timeout=RUNNER_INVOKE_TIMEOUT,
    )
    out = _combined_output(r)
    assert "SUMMARY:" in out, "run_test_suite.py SUMMARY block missing; got:\n" + out
    assert "--ignore-known-failures:" not in out, (
        "run_test_suite.py leaked K5 flag line when flag was unset; got:\n" + out
    )


# Tests: run_tests_batched.py argparse (link #3 of chain)

def test_run_tests_batched_argparse_shows_flag_in_help():
    """_scratch/run_tests_batched.py --help must advertise
    --ignore-known-failures. If someone refactors argparser (renames, moves
    to subcommand, deletes the option), this test fails. The help text also
    carries the human-readable description so operators find the option via -h."""
    r = _run(
        [sys.executable, RUN_TESTS_BATCHED_PY, "--help"],
        timeout=60,
    )
    out = _combined_output(r)
    assert "--ignore-known-failures" in out, (
        "run_tests_batched.py --help does not advertise --ignore-known-failures; got:\n" + out
    )


def test_run_tests_batched_argparse_parses_flag_and_emits_banner():
    """When invoked with --list-files --ignore-known-failures, the batched
    runner must (a) parse the flag (argparse store_true), (b) emit
    `Ignore known failures: enabled` in the top banner."""
    r = _run(
        [sys.executable, RUN_TESTS_BATCHED_PY, "--list-files", "--ignore-known-failures"],
        timeout=60,
    )
    out = _combined_output(r)
    assert "Ignore known failures: enabled" in out, (
        "run_tests_batched.py top banner did not confirm flag is active; got:\n" + out
    )


# Tests: .bat %* forwarder (link #1 of chain)

@pytest.mark.skipif(sys.platform != "win32", reason=".bat file is Windows-only")
def test_bat_forwards_args_via_star():
    """_scratch/run_tests.bat must forward %* to `python run_test_suite.py`
    so argv-level flags reach the Python layer. We inspect the .bat content
    (text-level). Reading rather than executing bypasses the pre-flight
    gates that BLOCK e2e execution on this machine (assert_section11_render_clean.py
    etc. -- missing scripts BUILD-ABORT before pytest).

    Two required patterns:
      1. The line containing `python run_test_suite.py %*` exists AND is
         uncommented (does NOT start with `REM ` -- otherwise a refactor
         that moves the live invocation to a comment line would silently
         pass this test for the wrong reason).
      2. --ignore-known-failures appears in the header comment block.

    Catches: someone hardcoding flag list (loses forward-compat for future
    CI flags), removing %* entirely (breaks all flag forwarding), or
    commenting out the live invocation without removing the test."""
    with open(RUN_TESTS_BAT, "r", encoding="utf-8") as f:
        bat_content = f.read()
    matching_lines = [
        line for line in bat_content.splitlines()
        if "python run_test_suite.py %*" in line
    ]
    assert matching_lines, (
        "run_tests.bat does not contain `python run_test_suite.py %*`; "
        "the chain is broken at link #1 (.bat -> run_test_suite.py)."
    )
    for line in matching_lines:
        assert not line.lstrip().upper().startswith("REM "), (
            f"`python run_test_suite.py %*` line is commented out (REM): {line!r}"
        )
    assert "--ignore-known-failures" in bat_content, (
        "run_tests.bat does not mention --ignore-known-failures in its "
        "header comment; future operators can't discover the flag via the .bat."
    )


# Tests: pytest.ini marker registration (parallel link)

def test_known_failure_marker_is_registered_with_pytest():
    """pytest.ini must list `known_failure:` in the `markers =` block so
    @pytest.mark.known_failure does not produce 'unknown marker' warnings
    at collection time.

    `pytest --markers` lists every registered marker including description."""
    r = _run(
        [sys.executable, "-m", "pytest", "--markers"],
        timeout=60,
    )
    out = _combined_output(r)
    assert "known_failure" in out, (
        "`known_failure` not registered in pytest.ini; pytest --markers output:\n" + out
    )


# Self-test: this file's canary is correctly identifiable

def test_self_canary_is_marked_with_known_failure():
    """Lint-equivalent check: ensure the canary in THIS file still carries
    the @pytest.mark.known_failure marker AND starts with the `test_`
    prefix (so pytest's default collection picks it up — without the prefix
    conftest's pytest_collection_modifyitems never sees it). If someone
    refactors the canary and accidentally drops either invariant, all the
    chain tests above lose their data point and silently degrade to no-op
    passes. This test fails loud so the refactor surfaces.

    Uses source-code inspection (``inspect.getsource``) rather than pytest's
    internal ``_pytestmark`` attribute, which is reliably populated on pytest
    Item objects but can be inconsistent on raw function objects retrieved
    via ``globals()`` depending on the pytest version and the
    ``--ignore-known-failures`` flag's effect on item markers.
    """
    canary = globals().get("test_canary_marker_test")
    assert canary is not None, "test_canary_marker_test removed from this file"
    import inspect
    source = inspect.getsource(canary)
    assert "@pytest.mark.known_failure" in source, (
        "test_canary_marker_test does not carry @pytest.mark.known_failure; "
        "the chain tests above cannot function.\n"
        f"First line of source: {source.split(chr(10))[0]!r}"
    )
    # `inspect.getsource()` returns the source DECORATED — the first line
    # is the @pytest.mark.known_failure decorator, not the def line. So we
    # check that `"def test_"` appears anywhere in the source (proving the
    # function name carries the `test_` prefix that pytest's default
    # `python_functions = test_*` collection pattern matches).
    assert "def test_" in source, (
        "test_canary_marker_test must define a function whose name starts "
        "with `test_` (visible anywhere in its source — i.e. as "
        "`def test_canary_marker_test`) so pytest's default "
        "`python_functions = test_*` collection pattern picks it up; "
        "without that, conftest.py's pytest_collection_modifyitems never "
        "sees it and the chain tests above have no data point.\n"
        f"First line of source: {source.split(chr(10))[0]!r}"
    )

