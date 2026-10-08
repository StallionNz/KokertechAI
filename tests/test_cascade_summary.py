"""Tests for ``_runs/_cascade_summary.py`` (Sprint 19.7 OFF_BYONE cascade surface).


Mirrors ``tests/test_assert_no_corrupted_files.py`` pattern:
``importlib.util.spec_from_file_location`` key-load (avoids sys.path
mutations), ``tempfile.TemporaryDirectory()`` staging, and
``unittest.mock.patch.object`` for stdout + sys.exit isolation.

Each test pins a specific branch of ``classify_no_corruption`` /
``read_excerpt`` / ``parse_pairs`` / ``main`` so a future refactor that
silently drops a defense (or inverts the detection order -- CORRUPT must
fire BEFORE OFF_BYONE per the source docstring's "Detection order
matters" note) fails with a diagnostic pointing at the public function
name.

Drift-resistance (per KNOWLEDGE.md #12 + #21):
  * Function-name anchors -- ``classify_no_corruption``, ``read_excerpt``,
    ``parse_pairs``, ``main`` -- survive line drift.
  * Status-string anchors -- ``"PASS"``, ``"FAIL (SETUP)"``, ``"FAIL"``,
    ``"FAIL (CORRUPT)"``, ``"FAIL (BUCKET_DRIFT)"``,
    ``"FAIL (SUMMARY_ABSENT)"``, ``"FAIL (OFF_BYONE)"``, ``"FAIL (DRIFT)"``,
    ``"FAIL (MISSING)"``. Renaming ANY of these MUST be done in lockstep
    across this test file, ``_run_full_gate.sh``, ``_run_full_gate.bat``,
    and any downstream consumer.
  * Marker-string anchors -- ``"BUCKET-CLAIM DRIFT"``,
    ``"SUMMARY SECTION ABSENT"``, ``"OFF-BY-ONE"``,
    ``"[check2_quarantine_baseline] DRIFT"``, ``"absent at root"``,
    ``"check1_corrupted_files_in_root"``, ``"OFF-BY-ONE detected:"``,
    ``"quarantine count drift:"``, ``"Summary section absent"`` -- exact
    strings produced by ``scripts/assert_no_corrupted_files.py`` main()
    output and matched on by the cascade helper. Drift in any of these
    markers silently breaks both the classifier AND the excerpt surfacer.

Cross-references:
  - ``_runs/_cascade_summary.py`` (the unit under test)
  - ``scripts/assert_no_corrupted_files.py`` (producer of marker strings)
  - ``_runs/_run_full_gate.sh`` + ``_runs/_run_full_gate.bat`` (consumers
    invoking this helper via make_tmp + cascade)
  - ``KNOWLEDGE.md`` #21 marker stability guarantee
  - ``sprints/sprint19.7-closeout.md`` (the cascade refactor narrative)
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASCADE_PATH = PROJECT_ROOT / "_runs" / "_cascade_summary.py"


def _load_cascade_module():
    """Spec-load ``_cascade_summary.py`` as a uniquely-named module.

    Mirrors the dual-conftest-file canonical workaround
    (per KNOWLEDGE.md #10 TestConftestRegistryWireup) so loading does
    NOT mutate ``sys.path``. The sentinel suffix ``_for_test_only``
    prevents any second ``importlib.util.spec_from_file_location`` of
    the same path from colliding in ``sys.modules``.
    """
    spec = importlib.util.spec_from_file_location(
        "_cascade_summary_for_test_only",
        str(CASCADE_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_gate_output(td_path: Path, name: str, content: str) -> str:
    """Write a synthetic gate-output file and return its posix path string.

    Posix-form path matters: ``subprocess`` + bash on Windows mangles
    backslash paths in argv; ``as_posix()`` keeps the cast cross-platform.
    """
    p = td_path / name
    p.write_text(content, encoding="utf-8")
    return p.as_posix()


class TestClassifyNoCorruption(unittest.TestCase):
    """REGRESSION GUARD for ``classify_no_corruption`` (10 branches, 10 tests).

    Pins the detection order contract documented at the top of
    ``_cascade_summary.py``: CORRUPT is the early-defense (actionable
    first), BUCKET_DRIFT pre-empts OFF_BYONE which pre-empts DRIFT which
    pre-empts MISSING. A regression that re-orders any pair to put a
    less-specific defense ahead of a more-specific one will silently
    mislabel the offending gate in the composite summary table.

    Triangular-sync anchor: targets ``_cascade_summary.py::classify_no_
    corruption`` (lines 37-66 of ``_runs/_cascade_summary.py``); the
    Python function's REGRESSION GUARD docstring + this class's
    per-test docstrings together pin the status-string + marker-
    substring + detection-order contracts (closes the 3-corner
    citation triangle with the Python + shell doc-comments).
    """

    def setUp(self):
        self.mod = _load_cascade_module()

    def test_rc_zero_returns_PASS_short_circuit(self):
        """``rc == 0`` short-circuits to ``PASS`` before opening the file.

        REGRESSION invariant: regardless of file content (even garbage),
        rc==0 returns "PASS". If a future refactor moves the rc==0 check
        AFTER the file-open block, this test fires because the OSError
        branch fires first on a missing file.

        Function-anchor (KNOWLEDGE.md #12): the FIRST ``if`` of the
        ``classify_no_corruption`` body returns ``PASS`` on rc==0.
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path, "pass.txt", "_random_garbage_no_match_"
            )
            self.assertEqual(
                self.mod.classify_no_corruption(out_path, 0),
                "PASS",
                "REGRESSION: rc=0 must short-circuit to PASS regardless "
                "of file content; got a different status. Likely cause: "
                "classify_no_corruption() moved the rc==0 check after "
                "the file-open block.",
            )

    def test_rc_two_returns_SETUP_failure_short_circuit(self):
        """``rc == 2`` short-circuits to ``FAIL (SETUP)`` regardless of content.

        Function-anchor (KNOWLEDGE.md #12): the SECOND ``if`` of the
        ``classify_no_corruption`` body returns ``FAIL (SETUP)`` on rc==2.
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(td_path, "setup.txt", "_random_")
            self.assertEqual(
                self.mod.classify_no_corruption(out_path, 2),
                "FAIL (SETUP)",
                "REGRESSION: rc=2 must short-circuit to FAIL (SETUP); "
                "got a different status.",
            )

    def test_missing_output_file_returns_generic_FAIL_via_OSError(self):
        """OSError on file-open falls back to bare ``FAIL``.

        Lazy-trying to open a non-existent path triggers the ``except
        OSError: return "FAIL"`` branch. Note: the missing-file test uses
        an absolute path that does NOT exist on disk; ``open()`` raises
        FileNotFoundError (a subclass of OSError), so the except clause
        catches it.

        Function-anchor (KNOWLEDGE.md #12): the ``except OSError``
        fallback at the end of the file-open ``try`` block in
        ``classify_no_corruption`` returns the bare ``FAIL`` string.
        """
        nonexistent = (
            "/nonexistent_path_for_cascade_test_"
            + "a4d8f7c3b2e9_"  # unique sentinel
            + "no_such_file.txt"
        )
        self.assertEqual(
            self.mod.classify_no_corruption(nonexistent, 1),
            "FAIL",
            "REGRESSION: missing output file must fall back to generic "
            "FAIL via the OSError branch; got a different status. "
            "Likely cause: classify_no_corruption() lost the except "
            "OSError handler OR the generic FAIL return was renamed.",
        )

    def test_check1_corrupted_marker_with_FAILED_returns_FAIL_CORRUPT(self):
        """``check1_corrupted_files_in_root`` + ``FAILED`` -> ``FAIL (CORRUPT)``.

        Pins the EARLIEST defense in the cascade order: a null-byte hit
        in any root ``.py`` file fires CORRUPT, regardless of what other
        defenses would have fired if it weren't an early catch. The
        dual-substring matcher (``check1_corrupted_files_in_root`` AND
        ``FAILED``) is what reduces false positives from incidental
        matches on a key-word that legitimately appears in normal
        gate output.

        Function-anchor (KNOWLEDGE.md #12): the FIRST inner-``if`` of
        the file-open ``try`` block in ``classify_no_corruption`` --
        the EARLIEST defense in detection order per the source's
        "Detection order matters" preamble.
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "corrupt.txt",
                "check1_corrupted_files_in_root: 1 root .py with null bytes FAILED\n"
                "REGRESSION: see CONFIRMED_QUARANTINE list above\n",
            )
            self.assertEqual(
                self.mod.classify_no_corruption(out_path, 1),
                "FAIL (CORRUPT)",
                "REGRESSION: check1 marker + FAILED must yield FAIL (CORRUPT); "
                "likely cause: dropped marker substring OR renamed status.",
            )

    def test_bucket_claim_drift_marker_returns_FAIL_BUCKET_DRIFT(self):
        """``BUCKET-CLAIM DRIFT`` marker -> ``FAIL (BUCKET_DRIFT)``.

        Sprint 19.7 round-6 split: BUCKET_DRIFT (claim vs row mismatch)
        is operationally distinct from DRIFT (set mismatch); the cascade
        helper therefore separates them so the operator sees the
        specific defense that fired.
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "bucket_drift.txt",
                "BUCKET-CLAIM DRIFT: critical_claim=6 actual_rows=5\n",
            )
            self.assertEqual(
                self.mod.classify_no_corruption(out_path, 1),
                "FAIL (BUCKET_DRIFT)",
                "REGRESSION: BUCKET-CLAIM DRIFT marker must yield "
                "FAIL (BUCKET_DRIFT); likely cause: dropped marker OR "
                "renamed status.",
            )

    def test_summary_section_absent_marker_returns_FAIL_SUMMARY_ABSENT(self):
        """``SUMMARY SECTION ABSENT`` marker -> ``FAIL (SUMMARY_ABSENT)``.

        The substring is uppercase per the gate's main() output
        (``REGRESSION: SUMMARY SECTION ABSENT (manifest unparsable)``).
        REGRESSION GUARD: this branch MUST fire BEFORE OFF_BYONE / DRIFT
        so a missing Summary section doesn't get misclassified as
        generic DRIFT downstream (the BUG-2026-07-22-A class).
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "summary_absent.txt",
                "REGRESSION: SUMMARY SECTION ABSENT (manifest unparsable)\n",
            )
            self.assertEqual(
                self.mod.classify_no_corruption(out_path, 1),
                "FAIL (SUMMARY_ABSENT)",
                "REGRESSION: SUMMARY SECTION ABSENT marker must yield "
                "FAIL (SUMMARY_ABSENT); likely cause: the detection-order "
                "contract was inverted.",
            )

    def test_off_by_one_marker_returns_FAIL_OFF_BYONE(self):
        """``OFF-BY-ONE`` marker -> ``FAIL (OFF_BYONE)``.

        Pins the 2026-07-22 Sprint-19.7 defensive checklist contract:
        the ``OFF-BY-ONE`` substring (capital-O, hyphen, capital-O, N, E)
        is what the gate's main() emits when its ``validate_quarantine_
        baseline()`` defensive checklist fires.
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "off_by_one.txt",
                "OFF-BY-ONE detected: delta=+1 (44 disk - 43 manifest)\n",
            )
            self.assertEqual(
                self.mod.classify_no_corruption(out_path, 1),
                "FAIL (OFF_BYONE)",
                "REGRESSION: OFF-BY-ONE marker must yield FAIL (OFF_BYONE); "
                "likely cause: dropped marker OR renamed status.",
            )

    def test_drift_marker_returns_FAIL_DRIFT(self):
        """``[check2_quarantine_baseline] DRIFT`` marker -> ``FAIL (DRIFT)``.

        The bracketed-prefix form (``[check2_quarantine_baseline] DRIFT``)
        distinguishes DRIFT (set mismatch) from BUCKET_DRIFT (claim vs
        row mismatch) and from OFF_BYONE (row-count off-by-one).
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "drift.txt",
                "[check2_quarantine_baseline] DRIFT: set mismatch A != B\n",
            )
            self.assertEqual(
                self.mod.classify_no_corruption(out_path, 1),
                "FAIL (DRIFT)",
            )

    def test_missing_marker_returns_FAIL_MISSING(self):
        """``absent at root`` marker -> ``FAIL (MISSING)``.

        The literal ``absent at root`` substring is what the gate emits
        when ``recovery_manifest.md`` is not at project root.
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "missing.txt",
                "REGRESSION: recovery_manifest.md absent at root\n",
            )
            self.assertEqual(
                self.mod.classify_no_corruption(out_path, 1),
                "FAIL (MISSING)",
            )

    def test_no_marker_match_returns_generic_FAIL_fallback(self):
        """rc=1 + no marker match -> bare ``FAIL`` (the catch-all branch)."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path, "plain.txt", "no markers here, just a generic error\n"
            )
            self.assertEqual(
                self.mod.classify_no_corruption(out_path, 1),
                "FAIL",
            )

    def test_classify_detection_order_corrupt_preempts_off_byone(self):
        """REGRESSION GUARD: detection-order contract -- CORRUPT wins over OFF_BYONE.

        Source preamble in ``_cascade_summary.py`` explicitly states
        "Detection order matters: 1. CORRUPT ... 4. OFF_BYONE ..." --
        a regression that reordered BUCKET_DRIFT or OFF_BYONE BEFORE
        CORRUPT would silently mislabel the actionable defense
        (null-bytes hit) behind a less-actionable one (row-count drift),
        burying the operator-visible HARD-BROKEN line.

        This test pins the EARLIEST-defense-wins invariant by feeding
        a single file containing BOTH the CORRUPT and OFF_BYONE marker
        strings, then asserting the classifier surfaces CORRUPT
        (not OFF_BYONE).

        REGRESSION: if a future refactor moves the CORRUPT branch
        AFTER OFF_BYONE / DRIFT / MISSING in
        ``classify_no_corruption``, the assertion fires with a
        diagnostic pointing at the function name.

        Function-anchor (KNOWLEDGE.md #12): the FIRST inner-``if`` of
        the file-open try block (CORRUPT) MUST execute before the
        OFF_BYONE ``if``.
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "dual_marker.txt",
                "check1_corrupted_files_in_root: 1 root .py FAILED\n"
                "OFF-BY-ONE detected: delta=+1\n",
            )
            self.assertEqual(
                self.mod.classify_no_corruption(out_path, 1),
                "FAIL (CORRUPT)",
                "REGRESSION: with BOTH CORRUPT and OFF_BYONE markers "
                "present, classify_no_corruption() must return "
                "'FAIL (CORRUPT)' (CORRUPT is the early-defense); got "
                "a different status. Likely cause: detection-order "
                "contract regressed in classify_no_corruption().",
            )


class TestReadExcerpt(unittest.TestCase):
    """REGRESSION GUARD for ``read_excerpt`` (per-status surfacing + edges)."""

    def setUp(self):
        self.mod = _load_cascade_module()

    def test_unreadable_file_returns_diagnostic_string(self):
        """OSError on open -> ``(output file unreadable)``."""
        nonexistent = "/nonexistent_path_for_cascade_read_a4d8f7c3b2e9.txt"
        self.assertEqual(
            self.mod.read_excerpt(nonexistent, "FAIL (CORRUPT)"),
            "(output file unreadable)",
        )

    def test_empty_file_returns_no_output_placeholder(self):
        """Empty file -> ``(no output)`` regardless of status."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(td_path, "empty.txt", "")
            self.assertEqual(
                self.mod.read_excerpt(out_path, "FAIL"),
                "(no output)",
            )

    def test_bucket_drift_surfaces_marker_line_truncated_to_78(self):
        """BUCKET_DRIFT excerpt surfaces line containing ``BUCKET-CLAIM DRIFT:``.

        Truncation is enforced by ``[:78]`` on the surfaced line; the
        composite table's EXCERPT column is 78 chars wide.
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "bd.txt",
                "BUCKET-CLAIM DRIFT: critical=6 actual=5 (claimed > actual)\n"
                "next line should be ignored\n",
            )
            self.assertEqual(
                self.mod.read_excerpt(out_path, "FAIL (BUCKET_DRIFT)"),
                "BUCKET-CLAIM DRIFT: critical=6 actual=5 (claimed > actual)",
            )

    def test_summary_absent_surfaces_marker_line_case_insensitive(self):
        """SUMMARY_ABSENT excerpt matches ``Summary section absent`` substring.

        The gate emits the marker in mixed case (``Summary section
        absent``); the cascade helper's read_excerpt matches on the
        exact mixed-case substring (NOT the all-caps form that the
        *detect* path uses).
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "sa.txt",
                "Summary section absent at line 42 — fix manifest\n",
            )
            self.assertEqual(
                self.mod.read_excerpt(out_path, "FAIL (SUMMARY_ABSENT)"),
                "Summary section absent at line 42 — fix manifest",
            )

    def test_summary_absent_with_no_matching_line_returns_fallback_string(self):
        """SUMMARY_ABSENT with no marker-line matches -> fallback summary."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path, "sa_fb.txt", "no marker line here\n"
            )
            result = self.mod.read_excerpt(
                out_path, "FAIL (SUMMARY_ABSENT)"
            )
            self.assertIn("manifest Summary section absent", result)
            self.assertIn("rebuild Summary table at root", result)

    def test_off_byone_surfaces_marker_line(self):
        """OFF_BYONE excerpt surfaces line containing ``OFF-BY-ONE detected:``."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "obo.txt",
                "OFF-BY-ONE detected: delta=+1 (44 disk - 43 manifest)\n",
            )
            self.assertEqual(
                self.mod.read_excerpt(out_path, "FAIL (OFF_BYONE)"),
                "OFF-BY-ONE detected: delta=+1 (44 disk - 43 manifest)",
            )

    def test_drift_surfaces_marker_line(self):
        """DRIFT excerpt surfaces line containing ``quarantine count drift:``."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "dr.txt",
                "quarantine count drift: set A != set B (5 vs 4)\n",
            )
            self.assertEqual(
                self.mod.read_excerpt(out_path, "FAIL (DRIFT)"),
                "quarantine count drift: set A != set B (5 vs 4)",
            )

    def test_missing_surfaces_marker_line(self):
        """MISSING excerpt surfaces line containing ``absent at root``."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "ms.txt",
                "REGRESSION: recovery_manifest.md absent at root\n",
            )
            self.assertEqual(
                self.mod.read_excerpt(out_path, "FAIL (MISSING)"),
                "REGRESSION: recovery_manifest.md absent at root",
            )

    def test_corrupt_status_returns_hardcoded_null_bytes_summary(self):
        """CORRUPT excerpt returns the hardcoded null-bytes summary line."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "c.txt",
                "anything at all goes here, the hardcoded message wins\n",
            )
            self.assertEqual(
                self.mod.read_excerpt(out_path, "FAIL (CORRUPT)"),
                "root .py with null bytes (see gate output above)",
            )

    def test_read_excerpt_SETUP_falls_through_to_generic_first_line(self):
        """REGRESSION GUARD: ``FAIL (SETUP)`` has NO specific branch in ``read_excerpt``.

        The source function ``read_excerpt`` has NO
        ``status == "FAIL (SETUP)"`` branch -- its only status-aware
        branches are BUCKET_DRIFT, SUMMARY_ABSENT, OFF_BYONE, DRIFT,
        MISSING, CORRUPT. SETUP therefore falls through to the
        generic first-non-empty-line path at the bottom of the
        function.

        REGRESSION: if a future refactor ADDS a SETUP-specific
        branch to ``read_excerpt``, this test fires because the
        surfacing rule changes. Useful anchor because there is no
        other test pinning the SETUP-specific absence.
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            out_path = _write_gate_output(
                td_path,
                "setup.txt",
                "first non-empty line wins for SETUP fallback\n"
                "second line ignored (SETUP has no specific branch)\n",
            )
            self.assertEqual(
                self.mod.read_excerpt(out_path, "FAIL (SETUP)"),
                "first non-empty line wins for SETUP fallback",
                "REGRESSION: FAIL (SETUP) MUST fall through to the "
                "generic first-non-empty-line excerpt path; got a "
                "different value. Likely cause: a SETUP-specific "
                "branch was added to read_excerpt without updating "
                "this REGRESSION GUARD.",
            )

    def test_long_line_is_truncated_to_at_most_78_chars(self):
        """Excerpt capped at <= 78 chars to fit the table column width.

        The 78-char cap comes from the composite table's EXCERPT column
        width (see ``main()`` print format string in
        ``_cascade_summary.py``). Asserting ``<=`` (not ``==``) lets
        the cap shrink/grow in lockstep with future column-width
        refactors (64, 100, etc.) without breaking this test. The
        ``startswith`` assertion still pins that truncation actually
        happens (vs. the input being short enough to not need
        truncation).
        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            long_line = "x" * 200  # 200-char input
            out_path = _write_gate_output(td_path, "long.txt", long_line)
            result = self.mod.read_excerpt(out_path, "FAIL")
            self.assertLessEqual(
                len(result),
                78,
                "REGRESSION: excerpt length must be <= 78 chars to "
                "fit the composite table column; got " + str(len(result)),
            )
            self.assertTrue(
                result.startswith("x" * 10),
                "Excerpt should preserve leading characters after "
                "truncation (i.e. truncation actually happened for "
                "this 200-char input); got " + repr(result[:20]),
            )


class TestParsePairs(unittest.TestCase):
    """REGRESSION GUARD for ``parse_pairs`` argv parser + sys.exit handling.

    Pairs of form ``KEY=rc|path``. Order MUST be preserved (the composite
    table renders rows in argv order). Malformed argv triggers
    ``sys.exit(2)`` (distinct from gate execution failures which exit 1).
    """

    def setUp(self):
        self.mod = _load_cascade_module()

    def test_three_valid_key_rc_path_tuples_returned_in_order(self):
        """3 valid argv entries produce 3 tuples preserving argv order."""
        result = self.mod.parse_pairs(
            [
                "progname",
                "FB=0|/tmp/fb.txt",
                "NC=1|/tmp/nc.txt",
                "KA=0|/tmp/ka.txt",
            ]
        )
        self.assertEqual(
            result,
            [
                ("FB", 0, "/tmp/fb.txt"),
                ("NC", 1, "/tmp/nc.txt"),
                ("KA", 0, "/tmp/ka.txt"),
            ],
        )

    def test_single_pair_returns_single_tuple(self):
        """Single argv entry returns single (key, rc, path) tuple."""
        result = self.mod.parse_pairs(["progname", "FB=0|/tmp/fb.txt"])
        self.assertEqual(result, [("FB", 0, "/tmp/fb.txt")])

    def test_malformed_argv_missing_pipe_raises_SystemExit_code_2(self):
        """Argv missing ``|`` raises ``SystemExit`` with code 2.

        The parser writes a stderr diagnostic then calls
        ``sys.exit(2)`` -- exit-code 2 is the POSIX convention for
        usage errors (distinct from gate failures which exit 1).

        Pinned via ``assertRaises(SystemExit)`` per KNOWLEDGE.md
        #12 (idiomatic stdlib approach). This catches Python's
        ACTUAL exit contract -- not a ``mock.patch.object(sys,
        "exit")`` proxy which would silently mask any future
        refactor that called ``os._exit()`` or ``raise
        SystemExit(...)`` with a different code.
        """
        captured_stderr = io.StringIO()
        with mock.patch.object(sys, "stderr", captured_stderr):
            with self.assertRaises(SystemExit) as cm:
                self.mod.parse_pairs(
                    ["progname", "FB=0_NO_PIPE_HERE"]
                )
        self.assertEqual(
            cm.exception.code,
            2,
            "REGRESSION: malformed argv MUST raise SystemExit with "
            "code 2 (POSIX usage-error convention); got code "
            + repr(cm.exception.code),
        )
        # Diagnostic-line pin: parse_pairs writes a stderr line
        # BEFORE raising -- anchors the error-surface contract.
        self.assertIn(
            "argv parse error",
            captured_stderr.getvalue(),
            "REGRESSION: parse_pairs must write an 'argv parse "
            "error' diagnostic to stderr BEFORE raising; got "
            + repr(captured_stderr.getvalue()),
        )


class TestMainIntegration(unittest.TestCase):
    """REGRESSION GUARD for ``main()`` -- composite 5-gate orchestration.

    Pins the OVERALL_RC contract: any single gate failure flips
    overall_rc to 1 AND emits the HARD-BROKEN summary line. The
    PASS-only case emits ``ALL 5 GATES CLEAN``.
    """

    def setUp(self):
        self.mod = _load_cascade_module()

    def test_five_all_pass_returns_zero_and_emits_clean_summary(self):
        """5 all-PASS argv pairs -> overall_rc=0 + ``ALL 5 GATES CLEAN``."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            argv = ["progname"]
            for key, ext in (
                ("FB", "fb.txt"),
                ("S11", "s11.txt"),
                ("TC", "tc.txt"),
                ("KA", "ka.txt"),
                ("NC", "nc.txt"),
            ):
                _write_gate_output(td_path, ext, "")
                argv.append(key + "=0|" + (td_path / ext).as_posix())
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                rc = self.mod.main(argv)
            self.assertEqual(
                rc, 0,
                "REGRESSION: 5 all-PASS gates must yield overall_rc=0; "
                "got " + str(rc),
            )
            output = captured.getvalue()
            self.assertIn(
                "ALL 5 GATES CLEAN",
                output,
                "REGRESSION: clean baseline must surface "
                "'ALL 5 GATES CLEAN' in the aggregate line.",
            )
            self.assertIn("5 passed, 0 failed", output)

    def test_nc_corrupt_returns_one_with_hardbroken_summary(self):
        """4 PASS + 1 NC CORRUPT -> overall_rc=1 + ``HARD-BROKEN``."""
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            argv = ["progname"]
            for key, ext in (
                ("FB", "fb.txt"),
                ("S11", "s11.txt"),
                ("TC", "tc.txt"),
                ("KA", "ka.txt"),
            ):
                _write_gate_output(td_path, ext, "")
                argv.append(key + "=0|" + (td_path / ext).as_posix())
            nc_path = _write_gate_output(
                td_path,
                "nc.txt",
                "check1_corrupted_files_in_root: 1 root .py with null bytes FAILED\n",
            )
            argv.append("NC=1|" + nc_path)
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                rc = self.mod.main(argv)
            self.assertEqual(
                rc, 1,
                "REGRESSION: NC failure must flip overall_rc=1; got "
                + str(rc),
            )
            output = captured.getvalue()
            self.assertIn(
                "HARD-BROKEN",
                output,
                "REGRESSION: any gate failure must surface HARD-BROKEN "
                "in the RESULT line.",
            )
            self.assertIn("FAIL (CORRUPT)", output)


if __name__ == "__main__":
    unittest.main()
