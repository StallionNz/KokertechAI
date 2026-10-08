"""REGRESSION GUARD: production vault row-count watchdog (Sprint 19.8.3).

Fails the test suite when the PRODUCTION ``core_memories`` table grows past
a sane threshold. This is the automated version of the census bleed tripwire
that caught the 2026-09-22 re-pollution (consolidate_episodic re-promoting
~4,790 test-era journal rows per app run).

Threshold history (all measured on the real vault):
- 742,941  -- the disease (100% test pollution, pre-isolation-fix)
-      24  -- post P0-remediation reality (all real rows)
-    1,000 -- FAIL threshold: 40x the healthy state, ~2 app-run bursts
-      500 -- WARN threshold: investigation-worthy growth, still passes

ISOLATION NOTE (deliberate exception, read before "fixing" this):
This test runs under ``@pytest.mark.real_vault`` -- the registered escape
hatch that bypasses conftest._default_deny_vault_isolation. It must see the
REAL vault to do its job. The connection is opened with ``mode=ro`` so the
test can never write to production even if its own logic goes wrong.
"""

import os
import sqlite3

import pytest

import memory_vault as mv

# Sane-threshold contract: FAIL must trip long before the vault becomes
# unusable (the 742k-row state took 14.8 s per search) but not so tight
# that a legitimately memory-heavy week of real usage fails CI.
FAIL_THRESHOLD = 1_000
WARN_THRESHOLD = 500

PROD_DB = os.path.join(
    os.path.dirname(os.path.abspath(mv.__file__)), "kokertech_vault.db"
)


@pytest.mark.real_vault
class TestProductionVaultSize:
    def test_core_memories_row_count_within_sane_threshold(self):
        """ANTI-FRAGILITY/REGRESSION GUARD: production core_memories row
        count must stay below the FAIL threshold (1,000). Above 500 emits a
        warning; above 1,000 fails hard — that shape only ever meant
        pollution (see module docstring for the 742,941-row history)."""
        if not os.path.exists(PROD_DB):
            pytest.skip("production vault not present on this machine")
        if os.path.getsize(PROD_DB) < 1_024:
            # Fresh/empty placeholder DB (e.g. brand-new clone): nothing to
            # guard yet, and creating rows here would be counterproductive.
            pytest.skip("production vault is an empty placeholder (<1 KB)")

        conn = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
        try:
            n_rows = conn.execute(
                "SELECT COUNT(*) FROM core_memories"
            ).fetchone()[0]
        finally:
            conn.close()

        assert n_rows >= 0  # sanity: the table exists and is countable
        if n_rows > WARN_THRESHOLD:
            import warnings

            warnings.warn(
                f"Production core_memories at {n_rows} rows "
                f"(WARN threshold {WARN_THRESHOLD}). Investigate before it "
                f"reaches the FAIL threshold ({FAIL_THRESHOLD}). "
                f"Run: python scripts/vault_maintenance.py --census",
                stacklevel=1,
            )
        assert n_rows <= FAIL_THRESHOLD, (
            f"PRODUCTION VAULT POLLUTION: core_memories has {n_rows} rows "
            f"(threshold {FAIL_THRESHOLD}). This shape has historically only "
            f"meant test pollution or a consolidation-path leak. Diagnose "
            f"before deleting anything: "
            f"python scripts/vault_maintenance.py --census"
        )
