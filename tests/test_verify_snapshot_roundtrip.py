"""test_verify_snapshot_roundtrip.py - Regression tests for the snapshot round-trip verifier.

Specifically: lock the Drift.summary_line() dedup invariant (one file with
both sha + size mismatch must count once, not twice) so a future refactor
cannot regress it back to the double-count bug found in the v1 code-review
pass (MEDIUM #5).
"""
import os
import sys
import unittest

# Make verify_snapshot_roundtrip.py importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(os.path.dirname(_HERE), 'scripts')
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from verify_snapshot_roundtrip import Drift  # noqa: E402


class TestDriftSummaryLineDedup(unittest.TestCase):
    """Drift.summary_line() must count each distinct rel exactly once."""

    def _mk_sha(self, letter):
        # 64-char hex sha: 22 reps of ONE valid hex char + 42 zero-pad.
        # Reviewer-caught HIGH #1 (fix pass 2): must always produce valid
        # hex characters (0-9, a-f) so the synthetic shas resemble real
        # sha256 hex output, not arbitrary ASCII (the prior `letter * 22`
        # version emitted non-hex characters like 's', 'z', 'x' for the
        # mixed-status test).
        HEX = "0123456789abcdef"
        if letter in HEX:
            ch = letter
        else:
            ch = HEX[ord(letter) % len(HEX)]
        return (ch * 22).ljust(64, '0')

    def test_single_sha_only_drift_counts_one(self):
        d = Drift()
        d.add('file_A.txt', Drift.MISMATCH, self._mk_sha('a'), self._mk_sha('b'))
        self.assertEqual(d.summary_line(), 'ROW-MISMATCH count: 1')

    def test_single_size_only_drift_counts_one(self):
        d = Drift()
        # sha matches but size drifts.
        d.add('file_B.txt', Drift.MISMATCH, self._mk_sha('e'), self._mk_sha('e'))
        d.size_mismatch.append(('file_B.txt', 100, 200))
        self.assertEqual(d.summary_line(), 'ROW-MISMATCH count: 1')

    def test_sha_and_size_drift_on_same_rel_counts_one(self):
        """BOTH sha and size drift on the same rel must count ONCE, not twice.
        (MEDIUM #5 fix from the v1 code-review pass.)
        """
        d = Drift()
        d.add('file_C.txt', Drift.MISMATCH, self._mk_sha('a'), self._mk_sha('c'))
        d.size_mismatch.append(('file_C.txt', 100, 200))
        # WITHOUT the fix: this would be 2 (one via MISMATCH in rows,
        # one via size_mismatch). WITH the fix: this is 1 (deduped via the set).
        self.assertEqual(d.summary_line(), 'ROW-MISMATCH count: 1')

    def test_disjoint_drift_rows_count_each(self):
        """Distinct rels in rows + size_mismatch should each count once."""
        d = Drift()
        # file_D: sha-only drift (manifest != actual; both are 64-char hex)
        d.add('file_D.txt', Drift.MISMATCH, self._mk_sha('d'), self._mk_sha('a'))
        # file_E: size-only drift
        d.add('file_E.txt', Drift.MISMATCH, self._mk_sha('e'), self._mk_sha('e'))
        d.size_mismatch.append(('file_E.txt', 100, 200))
        # file_F: both drifts
        d.add('file_F.txt', Drift.MISMATCH, self._mk_sha('f'), self._mk_sha('g'))
        d.size_mismatch.append(('file_F.txt', 100, 200))
        self.assertEqual(d.summary_line(), 'ROW-MISMATCH count: 3')

    def test_ok_rows_dont_count(self):
        d = Drift()
        d.add('ok_1.txt', Drift.OK, self._mk_sha('a'), self._mk_sha('a'))
        d.add('ok_2.txt', Drift.OK, self._mk_sha('b'), self._mk_sha('b'))
        self.assertEqual(d.summary_line(), 'ROW-MISMATCH count: 0')

    def test_missing_status_counts(self):
        d = Drift()
        d.add('missing_1.txt', Drift.MISSING, self._mk_sha('a'), 'snapshot file missing')
        self.assertEqual(d.summary_line(), 'ROW-MISMATCH count: 1')

    def test_unsafe_status_counts(self):
        d = Drift()
        d.add('../etc/passwd', Drift.UNSAFE, self._mk_sha('a'), 'path escapes scratch_dir')
        self.assertEqual(d.summary_line(), 'ROW-MISMATCH count: 1')

    def test_scratch_write_fail_status_counts(self):
        d = Drift()
        d.add('fail.txt', Drift.SCRATCH_WRITE_FAIL, self._mk_sha('a'), 'Permission denied')
        self.assertEqual(d.summary_line(), 'ROW-MISMATCH count: 1')

    def test_mixed_statuses_count_correctly(self):
        """Mix OK + sha-only + size-only + both-drifts + missing in one Drift."""
        d = Drift()
        # 2 OK
        d.add('ok_1.txt', Drift.OK, self._mk_sha('a'), self._mk_sha('a'))
        d.add('ok_2.txt', Drift.OK, self._mk_sha('b'), self._mk_sha('b'))
        # 1 sha-only
        d.add('sha_only.txt', Drift.MISMATCH, self._mk_sha('s'), self._mk_sha('z'))
        # 1 size-only (sha matches)
        d.add('size_only.txt', Drift.MISMATCH, self._mk_sha('e'), self._mk_sha('e'))
        d.size_mismatch.append(('size_only.txt', 100, 200))
        # 1 both drifts
        d.add('both.txt', Drift.MISMATCH, self._mk_sha('f'), self._mk_sha('g'))
        d.size_mismatch.append(('both.txt', 100, 200))
        # 1 missing
        d.add('missing.txt', Drift.MISSING, self._mk_sha('h'), 'snapshot file missing')
        # Distinct drifted rels: sha_only, size_only, both, missing = 4
        self.assertEqual(d.summary_line(), 'ROW-MISMATCH count: 4')


class TestDriftStatusConstants(unittest.TestCase):
    """Lock the public status string constants so renderers + buckets stay aligned."""

    def test_status_values_uniqueness(self):
        statuses = {Drift.OK, Drift.MISMATCH, Drift.MISSING,
                    Drift.UNSAFE, Drift.SCRATCH_WRITE_FAIL}
        self.assertEqual(len(statuses), 5, 'status constants must be unique')

    def test_status_value_strings(self):
        # Lock the exact strings so callers can match on them.
        self.assertEqual(Drift.OK, 'OK')
        self.assertEqual(Drift.MISMATCH, 'ROW-MISMATCH')
        self.assertEqual(Drift.MISSING, 'SOURCE-MISSING')
        self.assertEqual(Drift.UNSAFE, 'PATH-UNSAFE')
        self.assertEqual(Drift.SCRATCH_WRITE_FAIL, 'SCRATCH-WRITE-FAIL')


if __name__ == '__main__':
    unittest.main()
