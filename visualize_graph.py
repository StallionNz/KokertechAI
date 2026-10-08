"""
visualize_graph.py — Knowledge graph visualization for KokertechAI.
Provides visualize_ecosystem() to dump the current vault graph to logs.
"""

import os
import sqlite3

from config import CONFIG
from logging_config import get_logger

logger = get_logger(name="MemoryVault")

DB_PATH = CONFIG.get("db_path", "kokertech_vault.db")
_TRUNCATE_LIMIT = 20


def _truncate(text, limit=_TRUNCATE_LIMIT):
    """Truncate text, appending '...' if over limit."""
    return text[:limit] + ("..." if len(text) > limit else "")


def visualize_ecosystem():
    """Log the current ecosystem graph (nodes + links + orphans)."""
    if not os.path.exists(DB_PATH):
        logger.error(f"Vault not found at {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        try:
            cursor = conn.cursor()

            # Fetch all nodes
            cursor.execute("SELECT id, type, content FROM core_memories")
            nodes = {row[0]: {"type": row[1], "content": row[2]} for row in cursor.fetchall()}

            # Fetch all links
            cursor.execute("SELECT source_id, target_id, link_type FROM memory_links")
            links = cursor.fetchall()

        finally:
            conn.close()

        # Build set of linked node IDs
        linked_ids = set()
        for src, tgt, _ in links:
            linked_ids.add(src)
            linked_ids.add(tgt)

        # Log orphans (nodes with no relationships)
        orphans = [nid for nid in nodes if nid not in linked_ids]
        if orphans:
            orphan_contents = [_truncate(nodes[oid]["content"]) for oid in orphans]
            logger.info(f"Orphan nodes (No relationships): {', '.join(orphan_contents)}")
        else:
            logger.info("All nodes have relationships.")

        # Log each link
        for src, tgt, link_type in links:
            if src in nodes:
                src_content = _truncate(nodes[src]["content"])
            else:
                src_content = "Unknown"
            if tgt in nodes:
                tgt_content = _truncate(nodes[tgt]["content"])
            else:
                tgt_content = "Unknown"
            logger.info(
                f"  {src_content} --[{link_type}]--> {tgt_content}"
            )

    except Exception as e:
        logger.error(f"Ecosystem visualization failed: {e}")
