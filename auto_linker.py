"""
auto_linker.py — Semantic auto-link suggestions for disconnected knowledge graph nodes.

Sprint 4 Stream E: computes cosine similarity between all disconnected node pairs
using sentence-transformers embeddings and suggests links above a threshold.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple, Any
from contextlib import closing

from logging_config import get_logger


logger = get_logger(name="AutoLinker")

# Re-export for tests that expect module-level attributes
import memory_vault as memory_vault  # noqa: F811 — real module for test patching


def _get_conn() -> sqlite3.Connection:
    """Return a new SQLite connection to the vault database.

    The DB path is resolved from ``memory_vault.DB_PATH`` (or fallback
    ``config.WORKSPACE_DIR / kokertech_vault.db``) to respect per-test vault isolation.
    """
    from config import WORKSPACE_DIR
    db_path = getattr(memory_vault, "DB_PATH", os.path.join(WORKSPACE_DIR, "kokertech_vault.db"))
    return sqlite3.connect(db_path, timeout=15.0)


def ensure_tables_exist():
    """Ensure required tables exist in the vault database."""
    try:
        conn = _get_conn()
        with closing(conn):
            conn.execute(
                "CREATE TABLE IF NOT EXISTS core_memories "
                "(id INTEGER PRIMARY KEY, content TEXT, node_type TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memory_links "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER, target_id INTEGER, "
                "relationship_type TEXT DEFAULT 'RELATES_TO', relation_type TEXT DEFAULT 'RELATES_TO', "
                "weight REAL DEFAULT 1.0, confidence REAL DEFAULT 1.0, "
                "created_at TEXT DEFAULT (datetime('now','localtime')), "
                "timestamp TEXT DEFAULT (datetime('now','localtime')))"
            )
            cursor = conn.execute("PRAGMA table_info(memory_links)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if "relation_type" not in existing_cols:
                conn.execute("ALTER TABLE memory_links ADD COLUMN relation_type TEXT DEFAULT 'RELATES_TO'")
            if "weight" not in existing_cols:
                conn.execute("ALTER TABLE memory_links ADD COLUMN weight REAL DEFAULT 1.0")
            if "confidence" not in existing_cols:
                conn.execute("ALTER TABLE memory_links ADD COLUMN confidence REAL DEFAULT 1.0")
            if "timestamp" not in existing_cols:
                conn.execute("ALTER TABLE memory_links ADD COLUMN timestamp TEXT DEFAULT ''")
                conn.execute("UPDATE memory_links SET timestamp = datetime('now','localtime') WHERE timestamp IS NULL OR timestamp = ''")
            conn.commit()
    except (sqlite3.Error, OSError, ValueError, KeyError):
        pass


def get_unlinked_node_ids() -> Tuple[List[int], Dict[int, str], set]:
    """Return (all_ids, id_to_content, linked_pairs_set).

    Queries ``core_memories`` for all node ids + content and
    ``memory_links`` for all source_id/target_id pairs.
    """
    try:
        conn = _get_conn()
        with closing(conn):
            # All nodes
            cursor = conn.execute("SELECT id, content FROM core_memories")
            rows = cursor.fetchall()
            all_ids = []
            id_to_content = {}
            for row in rows:
                if isinstance(row, dict):
                    nid = int(row["id"])
                    content = str(row.get("content", ""))
                else:
                    nid = int(row[0])
                    content = str(row[1]) if row[1] else ""
                all_ids.append(nid)
                id_to_content[nid] = content

            # Linked pairs
            cursor = conn.execute("SELECT source_id, target_id FROM memory_links")
            link_rows = cursor.fetchall()
            linked_pairs = set()
            for row in link_rows:
                if isinstance(row, dict):
                    linked_pairs.add((int(row["source_id"]), int(row["target_id"])))
                else:
                    linked_pairs.add((int(row[0]), int(row[1])))

            return all_ids, id_to_content, linked_pairs
    except Exception:
        return [], {}, set()


def suggest_links(
    threshold: float = 0.5, max_results: int = 10
) -> List[Dict[str, Any]]:
    """Find semantically similar but disconnected node pairs.

    Returns list of {source_id, source_content, target_id, target_content, score}.
    """
    try:
        ids, content, linked = get_unlinked_node_ids()
    except Exception:
        return []

    # Filter out empty/whitespace-only content nodes
    valid_ids = [nid for nid in ids if (content.get(nid) or "").strip()]
    if len(valid_ids) < 2:
        return []

    # Get sentence-transformer model
    try:
        model = memory_vault._get_model()
    except Exception as e:
        logger.warning(f"Failed to load embedding model in auto_linker: {e}")
        return []

    if model is None:
        logger.warning("Embedding model is None in auto_linker")
        return []

    # Encode all valid node contents
    texts = [content.get(nid, "").strip() for nid in valid_ids]
    try:
        import numpy as np
        embeddings = model.encode(texts)
    except Exception as e:
        logger.warning(f"Failed to encode texts in auto_linker: {e}")
        return []

    results = []
    for i in range(len(valid_ids)):
        for j in range(i + 1, len(valid_ids)):
            pair = (valid_ids[i], valid_ids[j])
            rev_pair = (valid_ids[j], valid_ids[i])
            if pair in linked or rev_pair in linked:
                continue
            # Cosine similarity
            a = embeddings[i]
            b = embeddings[j]
            dot = float(np.dot(a, b))
            norm_a = float(np.linalg.norm(a))
            norm_b = float(np.linalg.norm(b))
            if norm_a == 0 or norm_b == 0:
                score = 0.0
            else:
                score = dot / (norm_a * norm_b)
            if score >= threshold:
                results.append({
                    "source_id": valid_ids[i],
                    "source_content": content.get(valid_ids[i], ""),
                    "target_id": valid_ids[j],
                    "target_content": content.get(valid_ids[j], ""),
                    "score": round(float(score), 4),
                })

    # Sort by score descending, limit
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]


def infer_relationship_type(source_text: str, target_text: str) -> str:
    """Infer explicit semantic relationship type between two text snippets.

    Analyzes combined context to categorize into canonical RDF relation types
    ('IS_A', 'DEPENDS_ON', 'CAUSES', 'USES', 'IMPLEMENTS', 'PART_OF',
     'CREATED_BY', 'LOCATED_IN', 'HAS_ATTRIBUTE', 'PRECEDES', 'INTERACTS_WITH', 'RELATES_TO').
    """
    s_clean = (source_text or "").strip()
    t_clean = (target_text or "").strip()
    combined = f"{s_clean} {t_clean}".lower()
    if any(k in combined for k in ("part of", "belongs to", "component of", "member of")):
        return "PART_OF"
    if any(k in combined for k in ("depends on", "depend on", "requires", "require", "relies on", "relies upon", "needs", "need")):
        return "DEPENDS_ON"
    if any(k in combined for k in ("causes", "cause", "leads to", "lead to", "results in", "result in", "triggers", "trigger")):
        return "CAUSES"
    if any(k in combined for k in ("is a ", "is an ", "are a ", "are an ", "type of ", "kind of ")):
        return "IS_A"
    if any(k in combined for k in ("uses", "use ", "utilizes", "utilize", "operates with")):
        return "USES"
    if any(k in combined for k in ("implements", "implement", "supports", "support", "enables", "enable")):
        return "IMPLEMENTS"
    if any(k in combined for k in ("created by", "built by", "authored by", "designed by", "written by")):
        return "CREATED_BY"
    if any(k in combined for k in ("located in", "runs on", "hosted at", "deployed on")):
        return "LOCATED_IN"
    if any(k in combined for k in ("has attribute", "has property", "has feature", "features")):
        return "HAS_ATTRIBUTE"
    if any(k in combined for k in ("precedes", "runs before", "followed by", "prior to")):
        return "PRECEDES"
    if any(k in combined for k in ("interacts with", "communicates with", "connects to")):
        return "INTERACTS_WITH"
    return "RELATES_TO"


def suggest_semantic_triples(
    threshold: float = 0.5, max_results: int = 10
) -> List[Dict[str, Any]]:
    """Find disconnected node pairs and format them as structured RDF semantic triples.

    Returns list of dicts: {source_id, target_id, predicate, relation_type, score, weight, confidence, timestamp, subject, object}.
    """
    suggestions = suggest_links(threshold=threshold, max_results=max_results)
    ts = datetime.now(timezone.utc).isoformat()
    triples = []
    for s in suggestions:
        src_text = s.get("source_content", "")
        tgt_text = s.get("target_content", "")
        pred = infer_relationship_type(src_text, tgt_text)
        triples.append({
            "source_id": s["source_id"],
            "target_id": s["target_id"],
            "subject": src_text,
            "predicate": pred,
            "relation_type": pred,
            "object": tgt_text,
            "score": s["score"],
            "weight": s["score"],
            "confidence": s["score"],
            "timestamp": ts,
        })
    return triples


def apply_suggestions(suggestions: List[Dict[str, Any]]) -> int:
    """Auto-apply link suggestions to the database. Returns count of links created."""
    count = 0
    for s in suggestions:
        try:
            rel = s.get("relation_type") or s.get("predicate") or "SEMANTIC_LINK"
            if "relation_type" in s or "weight" in s or "timestamp" in s:
                weight = float(s.get("weight", s.get("score", 1.0)))
                conf = float(s.get("confidence", weight))
                ts = s.get("timestamp")
                memory_vault.link_memories(
                    s["source_id"], s["target_id"], rel,
                    weight=weight,
                    relation_type=rel,
                    timestamp=ts,
                    confidence=conf,
                )
            else:
                memory_vault.link_memories(
                    s["source_id"], s["target_id"], "SEMANTIC_LINK"
                )
            count += 1
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return count

