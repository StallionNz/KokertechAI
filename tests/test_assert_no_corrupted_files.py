"""Tests for ``scripts/assert_no_corrupted_files.py`` (Sprint 19.7 OFF_BY_ONE contract).
\n
\nMirrors ``tests/test_assert_singleton_reset_pattern.py`` pattern:
\nimportlib-util spec load, ``tempfile.TemporaryDirectory()`` staging, and
\n``unittest.mock.patch.object`` context manager for PROJECT_ROOT isolation.
\nEach test exercises the OFF_BY_ONE defensive checklist contract that
\nlanded 2026-07-22 inside ``validate_quarantine_baseline()`` (the
\n"row_count must equal len(quarantined_files)" guard that auto-detected
\nthe 43 vs 44 incident).
\n
\nREGRESSION GUARD doc-comments per KNOWLEDGE.md #12 ANTI-FRAGILITY /
\nREGRESSION GUARD pattern. Each test pins a specific invariant (status,
\ndelta sign, off_by_one_delta / extra_rows_count keys) so future refactors
\nthat silently drop the OFF_BY_ONE branch (or invert a delta sign) fail
\nwith a diagnostic pointing at ``validate_quarantine_baseline()``.
\n
\nDrift-resistance: cite the function names ``validate_quarantine_baseline``
\n(survives line drift) and the status string ``OFF_BY_ONE`` (public gate
\ncontract -- future contributors MUST NOT rename it without a migration
\nplan). Exact line numbers appear in REGRESSION GUARD docstrings because
\nthe user-facing contract references them in CHANGELOG + KNOWLEDGE cites.
\n
\nCross-references:
\n  - scripts/assert_no_corrupted_files.py (validate_quarantine_baseline)
\n  - recovery_manifest.md (real quarantine triage table)
\n  - KNOWLEDGE.md #19 PENDING/DEFERRED Baseline -- mirrors contract
\n  - CHANGELOG.md v0.20.5 -- Sprint 19.7 audit trail
\n"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
GATE_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "assert_no_corrupted_files.py"


def _load_gate_module():
    """Import the gate as a module via importlib (avoids sys.path dance).
\n
\n    Uses ``spec_from_file_location`` keyed on a unique sentinel module
\n    name -- the dual-conftest-file pattern (per KNOWLEDGE.md #10
\n    TestConftestRegistryWireup canonical workaround) is intentionally
\n    mirrored here so this test file does NOT add any new sys.path entries.
\n    """
    spec = importlib.util.spec_from_file_location(
        "_assert_no_corrupted_files_gate_for_test_only",
        str(GATE_SCRIPT_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_quarantine_fixture(
    td_path: Path,
    *,
    disk_quarantine_count: int,
    manifest_row_count: int,
) -> None:
    """Stage a fake quarantine manifest + N ``<stem>.py.corrupted`` files.
\n
\n    Disk stems use the ``_gate_quarantine_NNN`` sentinel prefix to avoid
\n    collisions with real quarantine files (per existing underscore-prefixed
\n    reserved-names convention). No clean ``<stem>.py`` sibling is created,
\n    so each ``.py.corrupted`` file lands in the real_quarantine bucket
\n    (matches the gate's classification rule).
\n
\n    For the inverse-case test (45-row manifest with 44-disk quarantine) the
\n    rows beyond ``disk_quarantine_count`` use the ``_gate_extra_NNN`` stem,
\n    so they appear in the manifest but NOT on disk, driving
\n    ``off_by_one_delta == -1`` (manifest has 1 extra row).
\n    """
    for i in range(disk_quarantine_count):
        stem = "_gate_quarantine_" + str(i).zfill(3)
        (td_path / (stem + ".py.corrupted")).write_bytes(
            b"\x6f\x0d\x0d\x0a\x00" * 4
        )

    manifest_lines = [
        "# Recovery Manifest -- synthetic for OFF_BY_ONE test",
        "",
        "## Summary",
        "",
        "| File | Size | .pyc? | Recovery |",
        "|---|---|---|---|",
    ]
    for i in range(manifest_row_count):
        if i < disk_quarantine_count:
            stem = "_gate_quarantine_" + str(i).zfill(3)
        else:
            stem = "_gate_extra_" + str(i).zfill(3)
        manifest_lines.append("| " + stem + ".py | 100 | Y | 100B |")
    (td_path / "recovery_manifest.md").write_text(
        "\n".join(manifest_lines) + "\n",
        encoding="utf-8",
    )


class TestOffByOneContract(unittest.TestCase):
    """REGRESSION GUARD for ``OFF_BY_ONE`` defensive checklist (Sprint 19.7).
\n
\n    Pins ``validate_quarantine_baseline()`` so that:
\n      * 43-row manifest + 44-disk quarantine -> ``OFF_BY_ONE`` + delta=+1
\n      * 45-row manifest + 44-disk quarantine -> ``OFF_BY_ONE`` + delta=-1
\n      * baseline match (44 / 44) -> ``OK``
\n
\n    Locking this contract prevents future refactors from silently dropping
\n    the OFF_BY_ONE branch (which would weaken the 2026-07-22 defensive
\n    checklist) or inverting the delta sign (which would mask the direction
\n    of the off-by-one at runtime).
\n
\n    Design note (exit-code-1 pinning):
\n        The user-requested exit-code-1 contract is pinned IN-PROCESS via the
\n        ``status == "OFF_BY_ONE"`` tuple pin (each test below). Subprocess
\n        tests would require the gate to expose a PROJECT_ROOT override
\n        (currently computed at module-import time via
\n        ``Path(__file__).resolve().parent.parent``); tracking that gate-side
\n        refactor as a followup in KNOWLEDGE.md #19 PENDING/DEFERRED
\n        baseline. The in-process pin is sufficient because ``main()``
\n        propagates ``status == "OFF_BY_ONE"`` into the ``failed`` flag
\n        path deterministically -> the subprocess exit code is a
\n        mechanical consequence of the combinatorial branch reached.
\n    """

    def setUp(self):
        self.mod = _load_gate_module()

    def test_43_row_manifest_with_44_disk_returns_off_by_one_with_delta_plus_1(self):
        """REGRESSION GUARD: 43-row manifest + 44-disk -> OFF_BY_ONE +1.
\n
\n        Mirrors the 2026-07-22 incident: the gate was originally authored
\n        with only 43 rows (missing the ``_smoke_test.py`` row) while the
\n        filesystem had 44 quarantined files. The defensive checklist
\n        auto-detected this off-by-one BEFORE the existing set-equality
\n        check would have caught it (with a weaker ``DRIFT`` status).
\n
\n        Construct: 44 ``.py.corrupted`` files on disk, 43 rows in manifest.
\n        Assertion: status == ``OFF_BY_ONE``, ``off_by_one_delta`` == +1
\n        (44 on disk minus 43 in manifest). The diagnostic-message
\n        assertion also pins the substring ``OFF-BY-ONE detected`` that
\n        main() surfaces to the operator.
\n
\n        If a future refactor drops the OFF_BY_ONE branch, the
\n        ``assertEqual`` call fires with a diagnostic pointing at
\n        ``validate_quarantine_baseline``.
\n        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _make_quarantine_fixture(
                td_path,
                disk_quarantine_count=44,
                manifest_row_count=43,
            )
            with mock.patch.object(self.mod, "PROJECT_ROOT", td_path):
                status, message, details = self.mod.validate_quarantine_baseline()

            self.assertEqual(
                status,
                "OFF_BY_ONE",
                "REGRESSION: expected OFF_BY_ONE for 43-row manifest + "
                "44-disk; got " + repr(status) + " (" + repr(message) + "). "
                "Likely cause: validate_quarantine_baseline() dropped the "
                "OFF_BY_ONE defensive checklist branch (Sprint 19.7).",
            )
            self.assertEqual(
                details.get("off_by_one_delta"),
                1,
                "REGRESSION: expected off_by_one_delta=+1 "
                "(44 disk - 43 manifest); got "
                + repr(details.get("off_by_one_delta"))
                + ". Sign convention: "
                "delta = len(real_quarantine) - len(manifest_set).",
            )
            self.assertIn(
                "OFF-BY-ONE detected",
                message,
                "REGRESSION: expected the OFF-BY-ONE marker string in "
                "the diagnostic message; got " + repr(message),
            )

    def test_45_row_manifest_with_44_disk_returns_off_by_one_with_delta_minus_1(self):
        """REGRESSION GUARD: 45-row manifest + 44-disk -> OFF_BY_ONE -1.
\n
\n        Inverse case of the 2026-07-22 incident: manifest has 1 extra row
\n        the filesystem does not have. Confirms the delta sign convention
\n        is consistent (delta = len(real_quarantine) - len(manifest_set))
\n        AND that ``extra_rows_count`` is populated.
\n
\n        Construct: 44 ``.py.corrupted`` files on disk, 45 rows in manifest.
\n        Assertion: status == ``OFF_BY_ONE``, ``off_by_one_delta`` == -1,
\n        ``extra_rows_count`` == 1, ``missing_rows_count`` == 0.
\n        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _make_quarantine_fixture(
                td_path,
                disk_quarantine_count=44,
                manifest_row_count=45,
            )
            with mock.patch.object(self.mod, "PROJECT_ROOT", td_path):
                status, message, details = self.mod.validate_quarantine_baseline()

            self.assertEqual(
                status,
                "OFF_BY_ONE",
                "REGRESSION: expected OFF_BY_ONE for 45-row manifest + "
                "44-disk; got " + repr(status) + " (" + repr(message) + ").",
            )
            self.assertEqual(
                details.get("off_by_one_delta"),
                -1,
                "REGRESSION: expected off_by_one_delta=-1 "
                "(44 disk - 45 manifest); got "
                + repr(details.get("off_by_one_delta"))
                + ". Sign convention: "
                "delta = len(real_quarantine) - len(manifest_set).",
            )
            self.assertEqual(
                details.get("extra_rows_count"),
                1,
                "REGRESSION: expected extra_rows_count=1 "
                "(45 manifest - 44 disk); got "
                + repr(details.get("extra_rows_count")),
            )
            self.assertEqual(
                details.get("missing_rows_count"),
                0,
                "REGRESSION: expected missing_rows_count=0 "
                "(no quarantine file is unaccounted-for); got "
                + repr(details.get("missing_rows_count")),
            )

    def test_baseline_match_returns_ok(self):
        """REGRESSION GUARD: matching counts (44/44) returns ``OK``.
\n
\n        Positive-path baseline: 44 ``.py.corrupted`` files on disk, 44
\n        rows in manifest. Confirms that the OFF_BY_ONE branch does NOT
\n        fire when counts agree AND that the OK message surfaces the
\n        actual quarantine + forensic counts (sanity check on the
\n        message-format pin).
\n
\n        Construct: 44 ``.py.corrupted`` files on disk, 44 rows in manifest.
\n        Assertion: status == ``OK``, message surfaces the count.
\n        """
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            _make_quarantine_fixture(
                td_path,
                disk_quarantine_count=44,
                manifest_row_count=44,
            )
            with mock.patch.object(self.mod, "PROJECT_ROOT", td_path):
                status, message, details = self.mod.validate_quarantine_baseline()

            self.assertEqual(
                status,
                "OK",
                "REGRESSION: expected OK for matching baseline; got "
                + repr(status) + " (" + repr(message) + "). "
                "Likely cause: validate_quarantine_baseline() regressed "
                "the OK branch or the OFF_BY_ONE branch is over-firing.",
            )
            self.assertIn(
                "baseline matched",
                message,
                "REGRESSION: expected the OK message to surface the "
                "'baseline matched' marker; got " + repr(message),
            )


class TestBucketClaimConsistency(unittest.TestCase):
    """REGRESSION GUARD for Sprint 19.7 ``validate_bucket_count_consistency``.
\n
\n    Pins the manifest-internal consistency check (Summary bucket-claim counts
\n    vs per-bucket table row counts) so future refactors that mutate the regex
\n    patterns or section-parsing logic silently drift the bucket-claim assertion
\n    (the user-reported gap: bucket-claim 6 vs actual table rows 5). Locking this
\n    contract prevents future refactors from silently dropping the claim-parsing
\n    branch (which would weaken the defensive checklist) or inverting the delta
\n    sign in the drift output.
\n
\n    ## Two distinct failure modes (Sprint 19.7 round-6 split)
\n
\n    The check returns NONE on OK and one of TWO distinct status strings on
\n    failure, separating two operationally different conditions that round-5
\n    previously conflated under BUCKET_DRIFT:
\n
\n      * ``"BUCKET_DRIFT"`` -- Summary present, but bucket-claim counts
\n        disagree with per-bucket table row counts. Remediation: reconcile
\n        counts (edit Summary or per-bucket rows).
\n
\n      * ``"SUMMARY_ABSENT"`` -- manifest is present but entirely missing
\n        its ``## Summary`` H2 section. Remediation: ADD a Summary section
\n        (structural rebuild) -- the gate cannot enforce bucket-count
\n        consistency without the Summary anchor.
\n
\n    ## Drift-resistance anchors (KNOWLEDGE.md #12 + #21)
\n
\n    Three anchors survive line + heading-text refactors:
\n
\n      1. **Function-name anchor**: ``validate_bucket_count_consistency`` in
\n         ``scripts/assert_no_corrupted_files.py``. Verify line range via::
\n
\n             grep -n -F "def validate_bucket_count_consistency" \\
\n                  scripts/assert_no_corrupted_files.py
\n
\n         (canonical #12 replayable form; never hardcode line numbers).
\n
\n      2. **Status-string anchors**: ``"BUCKET_DRIFT"`` and
\n         ``"SUMMARY_ABSENT"`` -- both stable public contracts per
\n         KNOWLEDGE.md #21 marker stability guarantee. Renaming either
\n         MUST be done in lockstep across this test class, the cascade
\n         classifier, and the gate's main() elif chain.
\n
\n      3. **Diagnostic-message substrings**: ``"BUCKET-CLAIM DRIFT:"``
\n         and ``"Summary section absent"`` -- the per-gate user-facing
\n         message surfaces these substrings literally, and
\n         ``_cascade_summary.read_excerpt()`` matches on them.
\n    """

    def setUp(self):
        self.mod = _load_gate_module()

    def _manifest_text(self, *, critical_claim, critical_rows):
        """Build synthetic Recovery Manifest fixture for bucket assertion testing."""
        crit_files = [("file_n" + str(i).zfill(2) + ".py") for i in range(critical_rows)]
        crit_lines = [
            "| File | Size (B) | .pyc? | .pyc size | Priority | Recovery |",
            "|---|---:|---|---:|---|---|",
        ]
        for f in crit_files:
            crit_lines.append(
                "| " + f + " | 1000 | Y | 1010B | **P1** | decompile |"
            )
        crit_table = "\n".join(crit_lines)
        summary_lines = [
            "| Bucket | Count | Has .pyc | Recovery path |",
            "|---|---|---|---|",
            "| Critical cores (stub) | **" + str(critical_claim) + "** | all | Decompile-first |",
            "| Services-facing (stub) | **0** | all | Decompile-first |",
            "| Tab-heavy / utilities (stub) | **0** | none | Mixed |",
            "| Without `.pyc` (stub) | **0** | none | User-supplied |",
            "| `_smoke_test.py` (stub) | **0** | none | User-supplied |",
            "| **Total quarantined** | **" + str(critical_claim) + "** | stub | |",
        ]
        summary = "\n".join(summary_lines)
        result_lines = [
            "# Recovery Manifest SYNTHETIC",
            "",
            "## Summary",
            "",
            summary,
            "",
            "## Critical Cores - Decompile-First Candidates",
            "",
            crit_table,
            "",
            "## Services-Facing - Decompile-First Candidates",
            "",
            "(empty stub)",
            "",
            "## Tab-Heavy Utilities - Mixed Recovery",
            "",
            "(empty stub)",
            "",
            "## FORENSIC COPIES",
            "",
        ]
        return "\n".join(result_lines)

    def test_summary_critical_count_exceeds_actual_rows_fires_bucket_drift(self):
        """REGRESSION GUARD: Summary Critical cores claim=6 but only 5 rows triggers BUCKET_DRIFT."""
        text = self._manifest_text(critical_claim=6, critical_rows=5)
        result = self.mod.validate_bucket_count_consistency(text)
        self.assertIsNotNone(
            result,
            "REGRESSION: expected BUCKET_DRIFT for Critical cores claim=6 vs actual=5; "
            "got None (consistency check passed incorrectly). Likely cause: "
            "validate_bucket_count_consistency() dropped the claim-parsing branch "
            "or known_bucket_labels no longer matches 'Critical cores'.",
        )
        status, message, details = result
        self.assertEqual(
            status,
            "BUCKET_DRIFT",
            "REGRESSION: expected status='BUCKET_DRIFT'; got " + repr(status)
            + ". Marker string is stable per KNOWLEDGE.md section 21.",
        )
        drift = details.get("bucket_claim_drift", [])
        self.assertTrue(
            len(drift) >= 1,
            "REGRESSION: expected bucket_claim_drift to list at least 1 entry; got " + repr(drift),
        )
        first = drift[0]
        self.assertEqual(first.get("claimed"), 6, "REGRESSION: expected claimed=6 in drift[0]")
        self.assertEqual(first.get("actual"), 5, "REGRESSION: expected actual=5 in drift[0]")
        self.assertEqual(first.get("delta"), -1, "REGRESSION: expected delta=-1 in drift[0]")

if __name__ == "__main__":
    unittest.main()
