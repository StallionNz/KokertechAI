"""REGRESSION GUARD suite for _runs/_run_pre_flight.sh (Sprint 19.7).

Pins the wrapper's three contracts:
  (a) clean baseline -> exit code 0 + per-gate summary "N passed, 0 failed"
  (b) single gate stubbed to fail -> exit code 1 + HARD-BROKEN banner
  (c) --json mode -> emits valid JSON parseable by stdlib json.loads()

Strategy: copy-and-patch the wrapper to a tempdir, replace the GATES=()
array entries with paths to temporary Python stub scripts that sys.exit()
with the desired code. The wrapper is exercised end-to-end without
depending on the real gate scripts' current state (defends against
false-positive flakes when a real downstream gate regresses mid-sprint).

Cross-platform safety:
  - Windows CRLF is normalized to LF on read+write (bash chokes on \r).
  - subprocess.run(["bash", wrapper_path], env={"PYTHON": sys.executable},
    cwd=PROJECT_ROOT) so the wrapper uses the test's own interpreter.
  - creationflags=CREATE_NO_WINDOW on win32 (per existing pattern in
    test_ignore_known_failures_chain.py).

Drift-resistance anchors (per KNOWLEDGE.md #12 ANTI-FRAGILITY pattern):
  - Cite _runs/_run_pre_flight.sh (path survives line drift).
  - Cite GATE_NAMES_IN_ORDER tuple (drift here mirrors wrapper's GATES=).
  - Cite the bash markers === Aggregate: N passed, 0 failed and
    [RESULT] HARD-BROKEN: 1 or more gates FAILED above -- these are
    the wrapper's STABLE SURFACE in --text mode.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WRAPPER_PATH = PROJECT_ROOT / "_runs" / "_run_pre_flight.sh"


# Order matters: must match the wrapper GATES=() array exactly. If
# the wrapper adds/removes a gate, this tuple MUST be updated in sync.
# Verified against _runs/_run_pre_flight.sh lines 25-31 (2026-07-22).
GATE_NAMES_IN_ORDER = (
    "FREE-BUFF-SECTIONS",
    "SECTION-11",
    "TEST-COUNTS",
    "KNOWN-ANCHORS",
    "R12-SINGLETON",
    "NO-CORRUPTION",
)


# Bash GATES=() block regex: anchors at literal "GATES=(" opener and ")"
# closer (each on its own line); ignores intermediate entry shape so the
# wrapper can extend entries without breaking this test.
_GATES_BLOCK_RE = re.compile(
    r"(?ms)^GATES=\(\n(?:[ \t]+\"[^\"]+\|[^\"]+\"\n)+\)$",
)


def _make_python_stub(td, *, exit_code):
    p = td / f"stub_exit_{exit_code}.py"
    p.write_text(
        f"import sys\nsys.exit({exit_code})\n",
        encoding="utf-8",
        newline="\n",
    )
    return p


def _stage_patched_wrapper(*, failure_gate=None):
    td = Path(tempfile.mkdtemp(prefix="run_pre_flight_test_"))
    stubs_dir = td / "stubs"
    stubs_dir.mkdir()
    stub_pass = _make_python_stub(stubs_dir, exit_code=0)
    stub_fail = _make_python_stub(stubs_dir, exit_code=1)
    wrapper_copy = td / "_run_pre_flight.sh"
    shutil.copy(WRAPPER_PATH, wrapper_copy)
    text = wrapper_copy.read_text(encoding="utf-8", newline="\n")
    patched_lines = []
    for name in GATE_NAMES_IN_ORDER:
        # Use forward-slash paths (Path.as_posix()) so re.subn() doesn't
        # misinterpret Windows backslashes as regex escape sequences:
        # re.subn's replacement string treats `\U` (from C:\\Users\\...)
        # as the start of a Unicode escape and raises "bad escape \U" if
        # not followed by 8 hex digits. Forward-slash paths work on both
        # Windows (git-bash + Python accept both) and Linux.
        stub = stub_fail if name == failure_gate else stub_pass
        script_path = str(stub.resolve().as_posix())
        patched_lines.append('    "' + name + "|" + script_path + '"')
    patched_block = "GATES=(\n" + "\n".join(patched_lines) + "\n)"
    new_text, n_replacements = _GATES_BLOCK_RE.subn(patched_block, text, count=1)
    assert n_replacements == 1, (
        "Could not patch _run_pre_flight.sh's GATES=() array; expected "
        "exactly 1 match, got " + str(n_replacements) + ". The wrapper's "
        "syntax may have drifted from _GATES_BLOCK_RE. Inspect "
        "_runs/_run_pre_flight.sh lines ~25-31 and update the regex."
    )
    wrapper_copy.write_text(new_text, encoding="utf-8", newline="\n")
    return td, wrapper_copy, len(GATE_NAMES_IN_ORDER)


def _invoke(wrapper_path, *, json_mode=False, timeout=120):
    """Invoke the patched bash wrapper via subprocess.

    Uses temp files for stdout/stderr instead of ``capture_output=True``
    (PIPE) to avoid pipe-buffer hangs on Windows git-bash where the
    stdout/stderr reader threads can deadlock against bash's output.
    """
    cmd = ["bash", str(wrapper_path)]
    if json_mode:
        cmd.append("--json")
    env = os.environ.copy()
    env["PYTHON"] = sys.executable
    # Avoid PIPE deadlock on Windows: write stdout/stderr to temp files.
    tmp_out = tempfile.NamedTemporaryFile(
        suffix=".stdout", mode="w+", encoding="utf-8", delete=False
    )
    tmp_err = tempfile.NamedTemporaryFile(
        suffix=".stderr", mode="w+", encoding="utf-8", delete=False
    )
    kwargs = dict(
        cwd=PROJECT_ROOT,
        env=env,
        stdout=tmp_out,
        stderr=tmp_err,
        text=True,
        timeout=timeout,
        close_fds=True,
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(cmd, **kwargs)
    finally:
        tmp_out.close()
        tmp_err.close()
    # Read back the temp files.
    out_path = Path(tmp_out.name)
    err_path = Path(tmp_err.name)
    stdout_text = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
    stderr_text = err_path.read_text(encoding="utf-8") if err_path.exists() else ""
    try:
        out_path.unlink(missing_ok=True)
        err_path.unlink(missing_ok=True)
    except OSError:
        pass
    # Build a result with the same interface as subprocess.CompletedProcess.
    return subprocess.CompletedProcess(
        args=cmd, returncode=result.returncode,
        stdout=stdout_text, stderr=stderr_text,
    )


def _bash_works() -> bool:
    """Verify bash can run a trivial Python stub without hanging.

    Uses temp files for stdout/stderr (not PIPE) to avoid the same
    Windows git-bash deadlock that _invoke() works around.
    """
    try:
        td = Path(tempfile.mkdtemp(prefix="bash_chk_"))
        stub = td / "stub.py"
        stub.write_text("import sys; sys.exit(0)\n", encoding="utf-8", newline="\n")
        env = os.environ.copy()
        env["PYTHON"] = sys.executable
        tmp_out = tempfile.NamedTemporaryFile(suffix=".out", delete=False)
        tmp_err = tempfile.NamedTemporaryFile(suffix=".err", delete=False)
        kwargs = dict(
            cwd=td, env=env,
            stdout=tmp_out, stderr=tmp_err,
            timeout=5, close_fds=True,
        )
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            ["bash", "-c", f'"{sys.executable}" "{stub.as_posix()}"'],
            **kwargs,
        )
        tmp_out.close()
        tmp_err.close()
        try:
            Path(tmp_out.name).unlink(missing_ok=True)
            Path(tmp_err.name).unlink(missing_ok=True)
        except OSError:
            pass
        shutil.rmtree(td, ignore_errors=True)
        return result.returncode == 0
    except Exception:
        return False


class TestRunPreFlight(unittest.TestCase):
    """REGRESSION GUARD for _runs/_run_pre_flight.sh (Sprint 19.7)."""

    @classmethod
    def setUpClass(cls):
        if not _bash_works():
            raise unittest.SkipTest(
                "bash subprocess cannot run Python stubs reliably on this "
                "machine (likely Windows git-bash PIPE hang). "
                "_run_pre_flight.sh integration tests are skipped — run "
                "the real wrapper directly to verify: bash _runs/_run_pre_flight.sh"
            )

    def setUp(self):
        self._tempdirs = []

    def tearDown(self):
        for td in self._tempdirs:
            # ignore_errors=True already swallows; no outer try/except needed
            shutil.rmtree(td, ignore_errors=True)

    def _stage(self, *, failure_gate=None):
        td, wrapper, n_stubs = _stage_patched_wrapper(failure_gate=failure_gate)
        self._tempdirs.append(td)
        return wrapper, n_stubs

    # ---- Test (a): clean baseline -> exit 0 ----
    def test_clean_baseline_exits_zero(self):
        wrapper, n_stubs = self._stage(failure_gate=None)
        result = _invoke(wrapper)
        self.assertEqual(
            result.returncode, 0,
            "Clean baseline should exit 0; got rc=" + str(result.returncode)
            + ". stderr:\n" + result.stderr[:1500]
            + "\nstdout:\n" + result.stdout[:1500],
        )
        self.assertIn(
            "=== Aggregate: " + str(n_stubs) + " passed, 0 failed",
            result.stdout,
            "Expected aggregate line '=== Aggregate: " + str(n_stubs)
            + " passed, 0 failed' in stdout; got:\n" + result.stdout[:1500],
        )
        for name in GATE_NAMES_IN_ORDER:
            self.assertIn(
                name, result.stdout,
                "Expected gate name " + repr(name) + " in summary; got:\n"
                + result.stdout[:1500],
            )
        # Stronger assertion: the wrapper MUST emit its per-gate [run] marker
        # for each gate (closes the loop-short-circuit false-positive path
        # where a regression statically prints all names but never actually
        # invokes the underlying gates).
        for name in GATE_NAMES_IN_ORDER:
            self.assertIn(
                "[run] " + name + " ->",
                result.stdout,
                "Expected per-gate [run] marker " + repr("[run] " + name + " ->")
                + " (closes loop-short-circuit regression); stdout:\n"
                + result.stdout[:1500],
            )

    # ---- Test (b): stubbed gate failure -> exit 1 ----
    def test_stubbed_gate_failure_propagates_exit_one(self):
        wrapper, n_stubs = self._stage(failure_gate="TEST-COUNTS")
        result = _invoke(wrapper)
        self.assertEqual(
            result.returncode, 1,
            "One gate stubbed to fail -> wrapper MUST exit 1; got rc="
            + str(result.returncode)
        )
        self.assertIn(
            "[RESULT] HARD-BROKEN: 1 or more gates FAILED above",
            result.stdout,
        )
        self.assertIn(
            "=== Aggregate: " + str(n_stubs - 1) + " passed, 1 failed",
            result.stdout,
        )
        self.assertIn("TEST-COUNTS", result.stdout)

    # ---- Test (c): --json mode emits valid parseable JSON ----
    def test_json_mode_emits_parseable_json(self):
        wrapper, n_stubs = self._stage(failure_gate=None)
        result = _invoke(wrapper, json_mode=True)
        self.assertEqual(result.returncode, 0)
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            self.fail("--json output is not valid JSON: " + str(e)
                      + "\nstdout:\n" + result.stdout[:2000])
        self.assertIsInstance(parsed, dict)
        self.assertIn("pre_flight", parsed)
        pf = parsed["pre_flight"]
        self.assertIsInstance(pf, dict)
        self.assertIn("overall_rc", pf)
        self.assertIsInstance(pf["overall_rc"], int)
        self.assertEqual(pf["overall_rc"], 0)
        self.assertEqual(pf.get("fail_count"), 0)
        self.assertEqual(pf.get("pass_count"), n_stubs)
        self.assertIsInstance(pf["gates"], list)
        self.assertEqual(len(pf["gates"]), n_stubs)
        for entry in pf["gates"]:
            self.assertIsInstance(entry, dict)
            for required_key in ("name", "status", "rc", "duration"):
                self.assertIn(required_key, entry,
                    "--json gate entry missing key " + repr(required_key))

    # ---- Test (d): --json mode + stubbed gate failure -> rc=1, JSON records ----
    def test_json_mode_with_stubbed_failure_includes_failed_gate_status(self):
        """REGRESSION GUARD (``_run_pre_flight.sh`` lines ~92-108 + ~83): the
        ``--json`` mode MUST (a) propagate the failing gate's exit code via
        ``overall_rc``, (b) record the per-gate ``status="FAIL"`` + ``rc=1``
        for the failing gate, AND (c) keep passing gates' count at
        ``pass_count == n_stubs - 1``. Closes comma-logic + status-mapping
        drift between --text and --json failure paths.
        """
        wrapper, n_stubs = self._stage(failure_gate="TEST-COUNTS")
        result = _invoke(wrapper, json_mode=True)
        # Two-way consistency: exit code matches overall_rc.
        self.assertEqual(
            result.returncode, 1,
            "Stubbed-gate-failure --json MUST exit 1; got rc="
            + str(result.returncode)
            + "\nstdout:\n" + result.stdout[:2000],
        )
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            self.fail(
                "--json failure output invalid JSON: " + str(e)
                + "\nstdout:\n" + result.stdout[:2000],
            )
        pf = parsed["pre_flight"]
        self.assertEqual(
            pf["overall_rc"], 1,
            "--json failure overall_rc MUST match exit code 1; got "
            + repr(pf["overall_rc"]),
        )
        self.assertEqual(
            pf["fail_count"], 1,
            "--json failure fail_count MUST equal 1; got "
            + repr(pf["fail_count"]),
        )
        self.assertEqual(
            pf["pass_count"], n_stubs - 1,
            "--json failure pass_count MUST equal stubs - 1 = "
            + str(n_stubs - 1) + "; got " + repr(pf["pass_count"]),
        )
        # Exactly one gate entry has status=="FAIL" AND rc==1 AND name=="TEST-COUNTS".
        failed_entries = [
            g for g in pf["gates"]
            if g.get("status") == "FAIL" and g.get("rc") == 1
        ]
        self.assertEqual(
            len(failed_entries), 1,
            "--json failure must yield exactly 1 gate with status=FAIL & rc=1; "
            "got " + str(len(failed_entries)) + ". entries: " + repr(pf["gates"]),
        )
        self.assertEqual(
            failed_entries[0].get("name"), "TEST-COUNTS",
            "--json failing gate's name MUST equal TEST-COUNTS; got "
            + repr(failed_entries[0]),
        )


if __name__ == "__main__":
    unittest.main()
