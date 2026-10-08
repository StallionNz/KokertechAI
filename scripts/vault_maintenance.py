#!/usr/bin/env python3
"""
vault_maintenance.py — Vault health, retention, and compaction for KokertechAI.

The episodic journal is append-only and unbounded; the vault crossed 5 GB
because (a) nothing ever expired and (b) 2.0.x stored embeddings as ~8.4 KB
JSON text per 384-dim vector instead of 768 bytes of float16 binary (~91%
larger — measured on production, 2026-09).

This tool is RUN ON DEMAND ONLY (explicit `--vacuum`, or `--execute` for
retention deletes). It NEVER touches production data implicitly, so test
suites and app startup can never corrupt the vault by importing it.

Modes:
  --dry-run            Print the plan for the selected mode without acting
  --execute            Destructive gate for the SELECTED mode. Alone = journal
                       retention. Combined with another mode flag it applies
                       to that mode ONLY — journal retention is NOT implied
                       (v1.3.1 flag-overlap fix).
  --vacuum             VACUUM the database (reclaims space; needs free disk
                       ~= DB size; run offline / app closed)
  --reindex-fts        Rebuild both FTS5 indexes from source tables
  --migrate-embeddings Convert legacy JSON-text embeddings to float16
                       binary (2.1.0 format), --limit N rows per run
  --purge-test-rows    DELETE core_memories rows matching known test-pollution
                       signatures (dry-run shows the per-signature plan first;
                       July 2026 daily summaries are KEPT unless --purge-all)
  --census             READ-ONLY hygiene census: format split, dims coverage,
                       pollution scan, duplication, 24h bleed tripwire, dead
                       pages, integrity. Exit 1 if pollution or ACTIVE bleed
                       is detected (gate-able), 0 otherwise.
  --dedupe             Collapse duplicate-content core_memories rows keeping
                       the EARLIEST (timestamp ASC, id ASC tiebreak).
                       --dry-run shows the per-group plan first.

Safety rails:
- Standalone connection (NOT _get_conn) so the pool is untouched.
- Retention never deletes high-importance records: journal rows with
  importance_score >= 9 and ALL core memories (without --prune-core)
  are exempt.
- Every destructive step prints row counts BEFORE deleting.

Usage:
  python scripts/vault_maintenance.py                     # health report
  python scripts/vault_maintenance.py --dry-run           # same, explicit
  python scripts/vault_maintenance.py --execute --retention-days 90
  python scripts/vault_maintenance.py --vacuum
  python scripts/vault_maintenance.py --migrate-embeddings --limit 50000
  python scripts/vault_maintenance.py --purge-test-rows --dry-run
  python scripts/vault_maintenance.py --census   # hygiene check (cron-able)
  python scripts/vault_maintenance.py --dedupe --dry-run

# Version: 1.5.0 — 2026-09-22 — P0 remediation: --purge-journal-test-rows
#   (delete test-era episodic_journal rows via the core signature engine;
#   Sprint 19.8.3 — the journal itself was 4,789/4,789 test pollution).
# Version: 1.4.0 — 2026-09-22 — added --dedupe (collapse duplicate-content
#                         rows, earliest kept by timestamp ASC + id tiebreak;
#                         batched deletes + FTS rebuild)
# Version: 1.3.1 — 2026-09-22 — flag-overlap fix: --execute (and --dry-run
#                         plans) are mode-scoped; journal retention runs ONLY
#                         when --execute is passed alone. v1.3.0 --census.

# Version: 1.3.0 — 2026-09-22 — added --census (read-only hygiene report,
#                         gate-able exit code: 1 on pollution/active bleed)
# Version: 1.2.1 — 2026-09-22 — survivor sweep: +8 fixture signatures and
#                         an exact-match class for [EPISODIC: OK]; the 4 real
#                         July interactions are spared (census-verified)
# Version: 1.2.0 — 2026-09-22 — added --purge-test-rows (signature-based,
#                         per-signature dry-run plan, July summaries kept
#                         unless --purge-all)
# Version: 1.1.0 — 2026-09-21 — added --migrate-embeddings (batched,
#                         keyset-paginated, idempotent legacy->binary conversion)
# Version: 1.0.0 — 2026-09-21 — initial release (Sprint 19.8 audit)

# Version: 1.0.0 — 2026-09-21 — initial release (Sprint 19.8 audit)
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG  # noqa: E402  (project-root import after sys.path fix)
from logging_config import get_logger  # noqa: E402

logger = get_logger(name="vault_maintenance")

DEFAULT_RETENTION_DAYS = 90
HIGH_IMPORTANCE_FLOOR = 9  # journal rows at/above this importance are exempt
DEFAULT_MIGRATE_BATCH = 50000  # rows per --migrate-embeddings pass

# Test-pollution signatures (Sprint 19.8.2 forensics: core_memories held
# 742,941 rows with only 237 distinct contents — ALL test artifacts that
# bled in Jul 27–Aug 3 through the opt-in isolation hole, since fixed in
# conftest.py). Matching is conservative: substring/regex against the
# CLEANED content ([EPISODIC: ...] wrapper stripped), case-insensitive.
# Calibrated to the 2026-07/08 pollution; review the dry-run plan before
# ever passing --execute.
TEST_ROW_SIGNATURES = {
    "mock_marker": "[mock]",
    "rag_test_query": "test query",
    "fixture_user_hello": "user: hello",
    "fixture_research": 'repeatedly typed "research.',
    "fixture_processed": "system processed message",
    "fixture_being_processed": "was being processed",
    "smoke_ok": "smoke test ok",
    "smoke_entry": "smoke test memory entry",
    "palindrome_fixture": "is_palindrome",
    "diag_marker": "zzztestdiag",
    "uniq_marker": "uniquetestxyz",
    "eviction_probe": "test eviction",
    "multi_model_fixture": "multi-model response",
    # Sprint 19.8.2 survivor sweep (calibrated on the 17-distinct-content
    # census of the 4,819 rows the first pass left behind).
    "fixture_initiated_contact": "user initiated contact",
    "fixture_greeted": "greeted assistant",
    "fixture_cache_nocache": "no-cache",
    "fixture_cache_cachetest": "cache-test",
    "fixture_say_hi": "user: say hi.",
    "fixture_reply_ok": "reply with exactly 'ok'",
    "fixture_turn_one_ok": "turn one ok",
    # Pair-anchored so the REAL July 17 interaction ("User: whats up\n"
    # "Agent (Executive): Hey!...") survives; only the EPISODIC-wrapped
    # fixture variant (user:/assistant:) matches.
    "fixture_canned_greeting": "whats up\nassistant: hey!",
    # 2026-09-22 morning pollution: one-shot app-usage simulation wrote
    # "Simulated test user preference: ..." rows directly to the vault;
    # the scratch script was deleted, so this literal is the only trace.
    "fixture_simulated_pref": "simulated test user",
}
# Exact raw-content matches for fixtures whose cleaned text is too generic
# to substring-match safely (e.g. "[EPISODIC: OK]" cleans to just "ok").
EXACT_CONTENT_SIGNATURES = {
    "episodic_ok_probe": "[episodic: ok]",
}
TEST_ROW_PATTERNS = {
    "fixture_msg_range": r"\bmsg \d+\b",
    "fixture_query_range": r"\bquery \d+\b",
    "fixture_message_range": r"\bmessage \d+\b",
}
# July 2026 LLM daily summaries (narrative, imp=9) — KEPT by default;
# they are the only non-fixture rows in the table.
JULY_SUMMARY_MARKERS = (
    "**summary:**",
    "daily summary",
    "24-hour period",
    "interactions centered on two primary tasks",
    "repetitive, low-value interactions",
)


def _db_path() -> str:
    """Resolve the vault path identically to memory_vault (root-relative)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "kokertech_vault.db")


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} TB"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, sql: str, params=()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def health_report(conn: sqlite3.Connection) -> int:
    """Print table counts, DB size, and embedding-format breakdown."""
    db_path = _db_path()
    size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    print(f"Vault: {db_path}")
    print(f"Size:  {_human(size)}")

    for table in ("core_memories", "episodic_journal", "kg_entities",
                  "kg_relationships", "search_log", "session_metadata"):
        if _table_exists(conn, table):
            n = _count(conn, f"SELECT COUNT(*) FROM {table}")
            print(f"  {table:<22} {n:>10,} rows")

    if _table_exists(conn, "core_memories"):
        binary = _count(
            conn,
            "SELECT COUNT(*) FROM core_memories "
            "WHERE typeof(embedding)='blob'",
        )
        json_txt = _count(
            conn,
            "SELECT COUNT(*) FROM core_memories "
            "WHERE typeof(embedding)='text'",
        )
        nulls = _count(
            conn, "SELECT COUNT(*) FROM core_memories WHERE embedding IS NULL"
        )
        print(f"  embeddings: {binary:,} binary (2.1.0+) / "
              f"{json_txt:,} legacy JSON / {nulls:,} null")
        if json_txt:
            print("  NOTE: legacy JSON embeddings remain; they still work "
                  "(decoded on the fly) but cost ~11x the space (~8.4 KB vs "
                  "768 B). Run --migrate-embeddings to convert them.")

    oldest = conn.execute(
        "SELECT MIN(timestamp) FROM episodic_journal"
    ).fetchone()[0]
    print(f"  oldest journal entry: {oldest or 'n/a'}")
    return 0


def _print_delete_plan(conn: sqlite3.Connection, days: int, prune_core: bool) -> int:
    cutoff = f"datetime('now', 'localtime', '-{int(days)} days')"
    n_journal = _count(
        conn,
        f"SELECT COUNT(*) FROM episodic_journal "
        f"WHERE timestamp < {cutoff} AND importance_score < {HIGH_IMPORTANCE_FLOOR}",
    )
    print(f"RETENTION PLAN (dry-run): would DELETE {n_journal:,} episodic "
          f"journal rows older than {days} days "
          f"(importance < {HIGH_IMPORTANCE_FLOOR} rows exempt).")
    if prune_core:
        n_core = _count(conn, "SELECT COUNT(*) FROM core_memories")
        print(f"  --prune-core would ALSO delete {n_core:,} core memories "
              f"(DESTRUCTIVE — double-check).")
    else:
        print("  core memories: untouched (pass --prune-core to change).")
    return 0


def execute_retention(conn: sqlite3.Connection, days: int, prune_core: bool) -> int:
    cutoff = f"datetime('now', 'localtime', '-{int(days)} days')"
    plan = _count(
        conn,
        f"SELECT COUNT(*) FROM episodic_journal "
        f"WHERE timestamp < {cutoff} AND importance_score < {HIGH_IMPORTANCE_FLOOR}",
    )
    print(f"Deleting {plan:,} episodic journal rows older than {days} days...")
    cur = conn.execute(
        f"DELETE FROM episodic_journal "
        f"WHERE id IN (SELECT id FROM episodic_journal "
        f"WHERE timestamp < {cutoff} AND importance_score < {HIGH_IMPORTANCE_FLOOR})"
    )
    deleted_journal = cur.rowcount
    # FTS is content-linked (content='episodic_journal') — triggers do not
    # exist for raw deletes here, so rebuild after batch removal.
    deleted_core = 0
    if prune_core:
        cur = conn.execute("DELETE FROM core_memories")
        deleted_core = cur.rowcount
    conn.commit()
    print(f"Deleted: {deleted_journal:,} journal rows, {deleted_core:,} core rows.")
    if _table_exists(conn, "episodic_journal_fts"):
        conn.execute("INSERT INTO episodic_journal_fts(episodic_journal_fts) VALUES('rebuild')")
        conn.commit()
        print("FTS (episodic) rebuilt.")
    if prune_core and _table_exists(conn, "core_memories_fts"):
        conn.execute("INSERT INTO core_memories_fts(core_memories_fts) VALUES('rebuild')")
        conn.commit()
        print("FTS (core) rebuilt.")
    logger.info(
        f"vault_maintenance retention: deleted {deleted_journal} journal + "
        f"{deleted_core} core rows (retention={days}d, prune_core={prune_core})"
    )
    return 0


def vacuum(conn: sqlite3.Connection) -> int:
    print("VACUUM running — needs free disk ~= current DB size. This can take minutes...")
    conn.execute("VACUUM")
    conn.commit()
    size = os.path.getsize(_db_path())
    print(f"Done. New size: {_human(size)}")
    return 0


def reindex_fts(conn: sqlite3.Connection) -> int:
    for fts, src in (("core_memories_fts", "core_memories"),
                     ("episodic_journal_fts", "episodic_journal")):
        if _table_exists(conn, fts):
            print(f"Rebuilding {fts} from {src}...")
            conn.execute(f"INSERT INTO {fts}({fts}) VALUES('rebuild')")
    conn.commit()
    print("FTS rebuild complete.")
    return 0


def migrate_embeddings(conn: sqlite3.Connection, limit: int) -> int:
    """Convert legacy JSON-text embeddings to float16 binary (2.1.0 format).

    REGRESSION GUARD invariants:
    - Uses memory_vault's own _encode_embedding/_decode_embedding codec so
      the on-disk format can never drift from what the runtime writes.
    - Keyset pagination on ``id`` (PK) instead of OFFSET: stable cursor over
      a 600k-row table, no quadratic rescans.
    - Verify-before-write: each candidate blob is decoded back IN MEMORY and
      compared to the source vector BEFORE any UPDATE is issued. A bad row
      is skipped without ever touching the database (idempotent re-runs).
    - Commit granularity is one batch: a crash mid-batch rolls back that
      batch only; re-running continues from the last converted id.
    """
    import numpy as np
    import memory_vault as mv

    if not _table_exists(conn, "core_memories"):
        print("No core_memories table — nothing to migrate.")
        return 0

    remaining = limit
    converted_total = 0
    failed_total = 0
    last_id = 0
    while remaining > 0:
        batch_n = min(DEFAULT_MIGRATE_BATCH, remaining)
        rows = conn.execute(
            "SELECT id, embedding FROM core_memories "
            "WHERE typeof(embedding)='text' AND id > ? "
            "ORDER BY id LIMIT ?",
            (last_id, batch_n),
        ).fetchall()
        if not rows:
            break
        converted = 0
        for row_id, raw in rows:
            last_id = row_id
            try:
                vec = mv._decode_embedding(raw)
                if vec is None or vec.size == 0:
                    raise ValueError("undecodable legacy embedding")
                blob = mv._encode_embedding(vec)
                back = mv._decode_embedding(blob)
                if back is None or not np.allclose(back, vec, atol=2e-3):
                    raise ValueError(f"round-trip mismatch for id {row_id}")
                conn.execute(
                    "UPDATE core_memories SET embedding = ? WHERE id = ?",
                    (blob, row_id),
                )
                converted += 1
            except (ValueError, TypeError, sqlite3.Error) as e:
                failed_total += 1
                logger.warning(
                    f"migrate_embeddings: failed row id={row_id} kept as-is: {e}"
                )
        conn.commit()
        converted_total += converted
        remaining -= len(rows)
    print(f"Migration: {converted_total:,} rows converted, {failed_total:,} rows "
          f"failed (left as legacy JSON — still decode fine).")
    return 0


def _clean_content(content: str) -> str:
    """Strip the [EPISODIC: ...] wrapper so signatures match inner text."""
    s = content.strip()
    if s.startswith("[EPISODIC:") and s.endswith("]"):
        s = s[len("[EPISODIC:"):-1]
    return s.lower()


def _is_july_summary(content: str) -> bool:
    """True for the July 2026 LLM daily-summary rows (kept by default)."""
    low = _clean_content(content)
    return any(marker in low for marker in JULY_SUMMARY_MARKERS)


def match_test_signature(content: str):
    """Return the matching signature name, or None.

    Matching runs against the CLEANED, lowercased content ([EPISODIC:
    wrapper stripped). July 2026 daily summaries match as the special
    ``july_summary`` class: PROTECTED by default (kept), removable only
    via --purge-all. Protection is checked FIRST so a summary that also
    happens to contain a pollution substring stays protected.
    """
    if _is_july_summary(content):
        return "july_summary"
    raw = content.strip().lower()
    for name, sig in EXACT_CONTENT_SIGNATURES.items():
        if raw == sig:
            return name
    low = _clean_content(content)
    for name, sig in TEST_ROW_SIGNATURES.items():
        if sig in low:
            return name
    for name, pat in TEST_ROW_PATTERNS.items():
        if re.search(pat, low):
            return name
    return None


def _print_purge_plan(conn: sqlite3.Connection) -> int:
    """Per-signature dry-run plan for --purge-test-rows."""
    rows = conn.execute("SELECT content FROM core_memories").fetchall()
    by_sig = {}
    protected = 0
    for (content,) in rows:
        sig = match_test_signature(content)
        if sig is None:
            continue
        if sig == "july_summary":
            protected += 1
            continue
        by_sig[sig] = by_sig.get(sig, 0) + 1
    total = sum(by_sig.values())
    print(f"PURGE PLAN (dry-run): {total:,} of {len(rows):,} core_memories rows "
          f"match test-pollution signatures.")
    for sig, n in sorted(by_sig.items(), key=lambda kv: -kv[1]):
        print(f"  {sig:<24} {n:>9,}")
    if protected:
        print(f"  PROTECTED (july_summary class, kept): {protected:,} rows "
              f"— pass --purge-all to include them")
    keep = len(rows) - total - protected
    print(f"  rows that would REMAIN: {keep:,} "
          f"(+ {protected:,} protected)")
    return 0


def execute_purge(conn: sqlite3.Connection, purge_all: bool) -> int:
    """Delete signature-matched core_memories rows (with FTS rebuild)."""
    rows = conn.execute("SELECT id, content FROM core_memories").fetchall()
    doomed = []
    protected = 0
    for row_id, content in rows:
        sig = match_test_signature(content)
        if sig is None:
            continue
        if sig == "july_summary":
            protected += 1
            if not purge_all:
                continue
        doomed.append(row_id)
    total = len(doomed)
    print(f"Deleting {total:,} test-pollution rows..."
          f"{' (incl. ' + str(protected) + ' protected July summaries)' if purge_all and protected else ''}")
    for i in range(0, total, 10000):
        chunk = doomed[i:i + 10000]
        sql = "DELETE FROM core_memories WHERE id IN (" + ",".join("?" * len(chunk)) + ")"  # noqa: S608 — internally generated "?" placeholders, no user input
        conn.execute(sql, chunk)
        conn.commit()
    kept = conn.execute("SELECT COUNT(*) FROM core_memories").fetchone()[0]
    print(f"Deleted: {total:,}. Remaining core_memories rows: {kept:,}.")
    if _table_exists(conn, "core_memories_fts"):
        conn.execute("INSERT INTO core_memories_fts(core_memories_fts) VALUES('rebuild')")
        conn.commit()
        print("FTS (core) rebuilt.")
    logger.info(
        f"vault_maintenance purge-test-rows: deleted {total} rows "
        f"(purge_all={purge_all}, protected_kept={protected})"
    )
    return 0


def _journal_purge_targets(conn: sqlite3.Connection) -> list:
    """IDs of episodic_journal rows matching any test-pollution signature."""
    rows = conn.execute("SELECT id, summary FROM episodic_journal").fetchall()
    return [row_id for row_id, summary in rows
            if match_test_signature(summary or "") is not None]


def _print_journal_purge_plan(conn: sqlite3.Connection) -> int:
    """Dry-run plan for --purge-journal-test-rows."""
    total_rows = _count(conn, "SELECT COUNT(*) FROM episodic_journal")
    doomed = _journal_purge_targets(conn)
    print(f"JOURNAL PURGE PLAN (dry-run): {len(doomed):,} of {total_rows:,} "
          "episodic_journal rows match test-pollution signatures.")
    survivors = total_rows - len(doomed)
    if survivors:
        print(f"  {survivors:,} row(s) would SURVIVE — verify they are real "
              "before running with --execute.")
    return 0


def execute_journal_purge(conn: sqlite3.Connection) -> int:
    """Delete test-signature episodic_journal rows, batched, with FTS rebuild.

    Rationale (Sprint 19.8.3 P0): the journal itself was test-era pollution
    (4,789/4,789 rows), and consolidate_episodic() re-promotes qualifying
    journal rows into core_memories on every app run — a polluted journal
    is a perpetual re-pollution engine. Purging the journal closes the loop.
    """
    total_rows = _count(conn, "SELECT COUNT(*) FROM episodic_journal")
    doomed = _journal_purge_targets(conn)
    total = len(doomed)
    print(f"Purging journal: deleting {total:,} of {total_rows:,} "
          "episodic_journal rows...")
    for i in range(0, total, 10000):
        chunk = doomed[i:i + 10000]
        sql = "DELETE FROM episodic_journal WHERE id IN (" + ",".join("?" * len(chunk)) + ")"  # noqa: S608 — internally generated "?" placeholders, no user input
        conn.execute(sql, chunk)
        conn.commit()
    kept = _count(conn, "SELECT COUNT(*) FROM episodic_journal")
    print(f"Deleted: {total:,}. Remaining episodic_journal rows: {kept:,}.")
    if _table_exists(conn, "episodic_journal_fts"):
        conn.execute("INSERT INTO episodic_journal_fts(episodic_journal_fts) VALUES('rebuild')")
        conn.commit()
        print("FTS (episodic) rebuilt.")
    logger.info(f"vault_maintenance purge-journal-test-rows: deleted {total} "
                f"of {total_rows} rows")
    return 0


def census(conn: sqlite3.Connection) -> int:
    """Read-only hygiene census; returns 1 if pollution or ACTIVE bleed is found.

    Exit-code contract (gate-able — safe to wire into pre-flight or cron):
      0  clean (WARNs allowed)
      1  FAIL: test-pollution rows present, or rows written in the last 24h
         while matching a pollution signature (active bleed — a test is
         leaking into production RIGHT NOW)
    WARNs (exit still 0): legacy-JSON remnants, dims-mismatch exclusions,
    duplicate content, unreclaimed dead pages.
    """
    fails = []
    warns = []

    # -- format split ---------------------------------------------------
    binary = _count(conn,
        "SELECT COUNT(*) FROM core_memories WHERE typeof(embedding)='blob'")
    legacy = _count(conn,
        "SELECT COUNT(*) FROM core_memories WHERE typeof(embedding)='text'")
    nulls = _count(conn,
        "SELECT COUNT(*) FROM core_memories WHERE embedding IS NULL")
    total = binary + legacy + nulls
    print(f"format split        : {binary:,} binary / {legacy:,} legacy JSON / "
          f"{nulls:,} null  (total {total:,})")
    if legacy:
        warns.append(f"{legacy:,} legacy JSON embedding(s) remain — "
                     "run --migrate-embeddings (11x the binary size)")

    # -- dims coverage (needs the row set once; decode via vault codec) --
    import memory_vault as mv
    dim_counts = {}
    embedded = 0
    decodable = 0
    for (emb,) in conn.execute(
        "SELECT embedding FROM core_memories WHERE embedding IS NOT NULL"
    ):
        embedded += 1
        if isinstance(emb, (bytes, bytearray, memoryview)):
            dims = len(emb) // 2
        else:
            text = emb.decode("utf-8", "replace") if isinstance(emb, bytes) else emb
            dims = text.count(",") + 1
        dim_counts[dims] = dim_counts.get(dims, 0) + 1
        if mv._decode_embedding(emb) is not None:
            decodable += 1
    conforming = dim_counts.get(mv._EMBEDDING_DIM, 0)
    print("embedding dims      : "
          + ", ".join(f"{d}-dim x {n:,}" for d, n in sorted(dim_counts.items()))
          + f"  ({embedded:,} embedded, {decodable:,} decodable)")
    if embedded and conforming < embedded:
        excluded = embedded - conforming
        warns.append(f"{excluded:,} embedded row(s) with dim != "
                     f"{mv._EMBEDDING_DIM} are INVISIBLE to semantic search")
    if embedded and decodable < embedded:
        fails.append(f"{embedded - decodable:,} embedded row(s) UNDECODABLE "
                     "(corrupt embeddings in the vector path)")

    # -- pollution scan (signature + dupes) -----------------------------
    pollution = 0
    dup_rows = 0
    distinct = 0
    seen = {}
    for (content,) in conn.execute("SELECT content FROM core_memories"):
        if content in seen:
            dup_rows += 1
        else:
            seen[content] = True
            distinct += 1
        sig = match_test_signature(content)
        if sig is not None and sig != "july_summary":
            pollution += 1
    print(f"content shapes      : {distinct:,} distinct / {dup_rows:,} duplicate rows")
    if pollution:
        fails.append(f"{pollution:,} test-pollution row(s) present — run "
                     "--purge-test-rows --dry-run for the plan")
    if dup_rows:
        warns.append(f"{dup_rows:,} duplicate-content row(s) "
                     "(inflates search space; consider dedupe)")

    # -- 24h bleed tripwire --------------------------------------------
    row = conn.execute(
        "SELECT COUNT(*) FROM core_memories "
        "WHERE timestamp >= datetime('now', 'localtime', '-1 day')"
    ).fetchone()
    recent = row[0] if row else 0
    bleed = 0
    if recent:
        for (content,) in conn.execute(
            "SELECT content FROM core_memories "
            "WHERE timestamp >= datetime('now', 'localtime', '-1 day')"
        ):
            sig = match_test_signature(content)
            if sig is not None and sig != "july_summary":
                bleed += 1
    print(f"recent writes (24h) : {recent:,}  (signature-matching: {bleed:,})")
    if bleed:
        fails.append(f"ACTIVE BLEED: {bleed:,} pollution-signature row(s) written "
                     "in the last 24h — a test is leaking into production NOW; "
                     "check conftest isolation")

    # -- dead pages + integrity ----------------------------------------
    freelist = _count(conn, "PRAGMA freelist_count")
    page_size = _count(conn, "PRAGMA page_size")
    dead_mb = freelist * page_size / 1024**2
    print(f"dead pages          : {freelist:,} ({dead_mb:.1f} MB unreclaimed)")
    if dead_mb > 100:
        warns.append(f"{dead_mb:.0f} MB of dead pages — run --vacuum to reclaim")
    integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
    print(f"integrity           : {integrity}")
    if integrity != "ok":
        fails.append(f"INTEGRITY: PRAGMA quick_check returned {integrity!r} — "
                     "STOP and restore from backup before any further writes")

    # -- verdict ---------------------------------------------------------
    print()
    for w in warns:
        print(f"  WARN: {w}")
    for f in fails:
        print(f"  FAIL: {f}")
    if fails:
        print(f"CENSUS: {len(fails)} FAIL, {len(warns)} WARN — exit 1")
        return 1
    print(f"CENSUS: CLEAN ({len(warns)} WARN) — exit 0" if warns
          else "CENSUS: CLEAN — exit 0")
    return 0


def _dedupe_targets(conn: sqlite3.Connection):
    """Return (keep_id -> delete_ids) for duplicate-content groups.

    REGRESSION GUARD: the keeper is the row with the EARLIEST timestamp
    (string compare — the vault stores ``datetime('now','localtime')``
    TEXT, lexicographic == chronological), with id ASC as the tiebreak.
    Insertion order is NOT the contract: a row inserted later can carry
    an earlier timestamp, and this function must honor the data, not the
    write sequence.
    """
    groups = {}
    for text, row_id, ts in conn.execute(
        "SELECT content, id, timestamp FROM core_memories ORDER BY id"
    ):
        groups.setdefault(text, []).append((ts, row_id))
    plan = {}
    for _text, members in groups.items():
        if len(members) < 2:
            continue
        members.sort()  # (timestamp, id) ASC — earliest first
        keep_id = members[0][1]
        plan[keep_id] = [row_id for _ts, row_id in members[1:]]
    return plan


def _print_dedupe_plan(conn: sqlite3.Connection) -> int:
    plan = _dedupe_targets(conn)
    if not plan:
        print("DEDUPE PLAN (dry-run): no duplicate-content groups — nothing to do.")
        return 0
    groups = len(plan)
    doomed = sum(len(v) for v in plan.values())
    total = _count(conn, "SELECT COUNT(*) FROM core_memories")
    print(f"DEDUPE PLAN (dry-run): {groups:,} duplicate group(s), "
          f"would DELETE {doomed:,} of {total:,} rows, "
          f"keeping {total - doomed:,} (earliest per group).")
    for keep_id, del_ids in sorted(plan.items()):
        kept, = conn.execute(
            "SELECT timestamp FROM core_memories WHERE id = ?", (keep_id,)).fetchone()
        print(f"  keep id={keep_id} ({kept})  delete {len(del_ids):,}: "
              f"{del_ids[:5]}{'...' if len(del_ids) > 5 else ''}")
    return 0


def execute_dedupe(conn: sqlite3.Connection) -> int:
    """Delete duplicate-content rows (earliest kept), batched, with FTS rebuild."""
    plan = _dedupe_targets(conn)
    doomed = [row_id for del_ids in plan.values() for row_id in del_ids]
    total = len(doomed)
    print(f"Deduplicating: deleting {total:,} rows across {len(plan):,} groups "
          f"(earliest per group kept)...")
    for i in range(0, total, 10000):
        chunk = doomed[i:i + 10000]
        # Note: placeholders are internally generated "?" literals.
        sql = "DELETE FROM core_memories WHERE id IN (" + ",".join("?" * len(chunk)) + ")"  # noqa: S608
        conn.execute(sql, chunk)
        conn.commit()
    kept = _count(conn, "SELECT COUNT(*) FROM core_memories")
    print(f"Deleted: {total:,}. Remaining core_memories rows: {kept:,}.")
    if _table_exists(conn, "core_memories_fts"):
        conn.execute("INSERT INTO core_memories_fts(core_memories_fts) VALUES('rebuild')")
        conn.commit()
        print("FTS (core) rebuilt.")
    logger.info(f"vault_maintenance dedupe: deleted {total} rows in "
                f"{len(plan)} groups (earliest kept)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="KokertechAI vault maintenance")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the retention plan without deleting")
    parser.add_argument("--execute", action="store_true",
                        help="actually delete rows per retention policy")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--prune-core", action="store_true",
                        help="with --execute: also delete ALL core memories")
    parser.add_argument("--vacuum", action="store_true",
                        help="VACUUM the database to reclaim disk space")
    parser.add_argument("--reindex-fts", action="store_true",
                        help="rebuild FTS5 indexes")
    parser.add_argument("--migrate-embeddings", action="store_true",
                        help="convert legacy JSON embeddings to float16 binary "
                             "(use --limit to convert a first batch)")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap rows processed by --migrate-embeddings")
    parser.add_argument("--purge-test-rows", action="store_true",
                        help="delete core_memories rows matching test-pollution "
                             "signatures (review the --dry-run plan first)")
    parser.add_argument("--purge-all", action="store_true",
                        help="with --purge-test-rows: also delete the protected "
                             "July 2026 daily summaries")
    parser.add_argument("--census", action="store_true",
                        help="read-only hygiene census (exit 1 on pollution or "
                             "active 24h bleed — gate-able)")
    parser.add_argument("--dedupe", action="store_true",
                        help="collapse duplicate-content core_memories rows, "
                             "keeping the earliest (use --dry-run first)")
    parser.add_argument("--purge-journal-test-rows", action="store_true",
                        help="delete episodic_journal rows matching test-pollution "
                             "signatures (closes the consolidation re-pollution loop)")
    args = parser.parse_args()

    db_path = _db_path()
    if not os.path.exists(db_path):
        print(f"No vault found at {db_path} — nothing to do.")
        return 0

    conn = sqlite3.connect(db_path, timeout=30)
    try:
        rc = health_report(conn)
        # ----------------------------------------------------------------
        # Mode scoping (v1.3.1 flag-overlap fix): --execute historically
        # fired JOURNAL RETENTION no matter which mode was selected, so
        # `--purge-test-rows --execute` silently ran retention too (and
        # `--purge-test-rows --dry-run` printed the retention plan).
        # Now: --execute / --dry-run belong to the selected mode; journal
        # retention runs ONLY when --execute is passed alone.
        # ----------------------------------------------------------------
        other_mode = any((args.migrate_embeddings, args.purge_test_rows,
                          args.census, args.reindex_fts, args.vacuum,
                          args.dedupe, args.purge_journal_test_rows))
        retention_is_the_mode = not other_mode

        if args.execute and other_mode:
            print("NOTE: --execute applies to the selected mode only; journal "
                  "retention was NOT run (pass --execute alone to run it).")

        if retention_is_the_mode:
            if args.dry_run:
                rc = _print_delete_plan(conn, args.retention_days, args.prune_core)
            if args.execute:
                rc = execute_retention(conn, args.retention_days, args.prune_core)
            if not args.dry_run and not args.execute:
                print("\n(Report only. Pass --dry-run / --execute / --vacuum / "
                      "--reindex-fts to act. Run with the app CLOSED.)")
            return rc

        if args.migrate_embeddings:
            rc = migrate_embeddings(conn, args.limit if args.limit is not None else 10**12)
        if args.purge_test_rows:
            if args.dry_run or not args.execute:
                rc = _print_purge_plan(conn)
            if args.execute:
                rc = execute_purge(conn, args.purge_all)
        if args.dedupe:
            if args.dry_run or not args.execute:
                rc = _print_dedupe_plan(conn)
            if args.execute:
                rc = execute_dedupe(conn)
        if args.purge_journal_test_rows:
            if args.dry_run or not args.execute:
                rc = _print_journal_purge_plan(conn)
            if args.execute:
                rc = execute_journal_purge(conn)
        if args.census:
            # Dedicated READ-ONLY connection: census can never write, even
            # accidentally — matters when scheduled from cron.
            ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rc = census(ro)
            finally:
                ro.close()
        if args.reindex_fts:
            rc = reindex_fts(conn)
        if args.vacuum:
            rc = vacuum(conn)
        return rc
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
