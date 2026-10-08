import sqlite3
import os
import re

from logging_config import get_logger

WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(WORKSPACE_DIR, "kokertech_vault.db")
LOG_FILE = os.path.join(WORKSPACE_DIR, "execution_log.txt")

logger = get_logger(name="MemoryVault")

# Regex for RuntimeWarning lines
_RT_WARNING_RE = re.compile(r"^RuntimeWarning:")


def _count_runtime_warnings(log_path: str) -> int:
    """Count RuntimeWarning lines in the given log file."""
    if not os.path.isfile(log_path):
        return 0
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if _RT_WARNING_RE.match(line))
    except Exception:
        return 0


def _wipe_tables(conn: sqlite3.Connection) -> None:
    """Delete all rows from known vault tables."""
    for table in ("core_memories", "memory_links", "bias_ledger", "growth_arcs"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def run():
    """
    Daily housekeeping: scan the execution log for RuntimeWarning
    counts and wipe the vault database tables.
    """
    count = _count_runtime_warnings(LOG_FILE)

    if count > 0:
        logger.warning(f"RuntimeWarning scan complete: {count} warning(s) found in log")

    if not os.path.isfile(DB_PATH):
        logger.warning("Vault database does not exist yet — skipping wipe.")
        return

    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        try:
            _wipe_tables(conn)
            logger.ok("Vault Memory and Cognitive Ledgers wiped clean.")
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning(f"Failed to wipe vault database: {e}")
