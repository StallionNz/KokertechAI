"""
vault_upgrade.py — Schema migration runner for the KokertechAI vault.

Runs ALTER TABLE migrations against kokertech_vault.db, adding columns
that the current version of memory_vault.py expects. Audit-before-mutate
invariant: scan_execution_log runs BEFORE sqlite3.connect so the audit
warning surfaces even if a downstream operation fails.

This is the schema-upgrade counterpart to vault_cleaner.py.
Both share the same audit-before-mutate pattern via utils.log_scanner.
"""

import os
import sqlite3

from config import CONFIG
from logging_config import get_logger
from utils.log_scanner import scan_execution_log

logger = get_logger(name="MemoryVault")

LOG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "execution_log.txt",
)

db_path = CONFIG.get("db_path", "kokertech_vault.db")


def upgrade_schema():
    """Run schema migration: audit log, then add columns.

    Audit-before-mutate invariant: scan_execution_log runs BEFORE
    sqlite3.connect so the RuntimeWarning audit surfaces even if
    a downstream ALTER TABLE statement fails.
    """
    # ── Audit ────────────────────────────────────────────────────
    warning_count = scan_execution_log(LOG_FILE, logger)

    # ── Mutate ───────────────────────────────────────────────────
    conn = sqlite3.connect(db_path, timeout=15.0)
    try:
        cursor = conn.cursor()

        # Add new columns if they don't already exist
        for col, col_type in [
            ("last_accessed", "TEXT"),
            ("metadata", "TEXT"),
            ("tags", "TEXT"),
        ]:
            try:
                cursor.execute(
                    f"ALTER TABLE core_memories ADD COLUMN {col} {col_type}"
                )
            except sqlite3.OperationalError:
                pass  # column already exists

        conn.commit()
        logger.ok(
            f"Schema expansion complete: added last_accessed, metadata, tags "
            f"to core_memories (warnings={warning_count})"
        )
    finally:
        conn.close()
