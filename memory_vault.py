#!/usr/bin/env python3
"""
memory_vault.py — SQLite-backed persistent memory for KokertechAI.

Provides a 3-tier memory architecture:
    Tier 1 (Short-term):  In-memory history in kokertechController
    Tier 2 (Episodic):    session-scoped journal summaries in episodic_journal table
    Tier 3 (Long-term):   core_memories table with embeddings + importance scoring

Also contains:
    - Hybrid search (vector + FTS5 BM25 with RRF fusion)
    - Knowledge Graph (GraphRAG) entities and relationships
    - Session lifecycle management
    - Persona A/B vote tracking
    - Daily summary generation via AI provider
"""

import sqlite3
from datetime import datetime, timedelta, timezone
import os
import json
import math
import copy
import importlib.util
import threading
from contextlib import closing
from typing import Optional, Union
import re
from collections import defaultdict, deque


try:
    import requests as _requests_real
except ImportError:
    class _RequestsMissingStub:
        """Fallback stub when requests is not installed."""
        class RequestException(Exception):
            pass
        @staticmethod
        def post(*args, **kwargs):
            raise _RequestsMissingStub.RequestException("requests package not installed")
        @staticmethod
        def get(*args, **kwargs):
            raise _RequestsMissingStub.RequestException("requests package not installed")
    requests = _RequestsMissingStub()
else:
    requests = _requests_real

import re

try:
    import numpy as np
except ImportError:
    np = None

from config import CONFIG
from logging_config import get_logger
from ai_base import get_provider

logger = get_logger(name="MemoryVault")

# ─── Module-level paths ─────────────────────────────────────────────
WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
# Version: 2.2.0 — 2026-09-22 — P0 remediation: episodic_journal.promoted
#   marker; consolidate_episodic() is now idempotent (REGRESSION GUARD against
#   the 2026-09-22 re-pollution: every lifecycle call re-promoted ALL
#   qualifying journal rows, ~4,790 dup rows per app run).
# Version: 2.1.0 — 2026-09-21 — Sprint 19.8: two-phase vector search, float16
#   binary embeddings, cached embedding-matrix fast path, invalidation hooks
#   on store/delete (see _vector_matrix_cache docstring).
DB_PATH = os.path.join(WORKSPACE_DIR, "kokertech_vault.db")

# ─── Module-level state ─────────────────────────────────────────────
_model_state: dict = {"model": None, "load_attempted": False}  # Sentence-transformer model reference
_session_state: dict = {"session_id": None}                         # Session state (ServiceRegistry-resettable)
_db_initialized: bool = False    # Lazy-init guard for ensure_tables_exist
_current_session_id: str | None = None
_session_start_time: datetime | None = None

# Connection pooling for better performance (thread-local to avoid SQLite threading issues)
_connection_pool = threading.local()
_pool_lock = threading.Lock()
_MAX_POOL_SIZE = 5

# Locks
_session_lock = threading.Lock()
_embedding_lock = threading.Lock()
_EMBEDDING_CACHE_MAX = 128
_embedding_cache: dict = {}
_FTS_SYNC_ENABLED = True
_GRAPHRAG_ENABLED = True
_graph_extraction_count = 0
_cross_encoder_cache = {}
_cross_encoder_cache_name = None
_CROSS_ENCODER_WARNED = False

# Snippets for entity extraction (regex patterns used by GraphRAG)
_ENTITY_PATTERNS = [
    (re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b'), 'person'),
    (re.compile(r'"([^"]{2,60})"'), 'concept'),
    (re.compile(r'\b(\d{4}-\d{2}-\d{2})\b'), 'date'),
    (re.compile(r'(https?://[^\s]+)'), 'url'),
    (re.compile(r'([A-Za-z]:\\[^\s]+|/[^\s]+\.\w+)'), 'filepath'),
]

_RELATIONSHIP_PATTERNS = [
    (re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:is|are|was|were)\s+(.+)', re.IGNORECASE), 'IS_A'),
    (re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:has|have|had)\s+(.+)', re.IGNORECASE), 'HAS'),
    (re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:uses?|used)\s+(.+)', re.IGNORECASE), 'USES'),
    (re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:created?|built|made)\s+(.+)', re.IGNORECASE), 'CREATED'),
    (re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:depends?\s+on|requires?|needs?)\s+(.+)', re.IGNORECASE), 'DEPENDS_ON'),
    (re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s+(?:implements?|supports?|enables?)\s+(.+)', re.IGNORECASE), 'IMPLEMENTS'),
]

_ENTITY_TYPE_PATTERNS = {
    'project': re.compile(r'\b(project|initiative|sprint|release)\b', re.IGNORECASE),
    'module': re.compile(r'\b(class|function|module|package|file|script)\b', re.IGNORECASE),
    'api': re.compile(r'\b(api|endpoint|route|interface)\b', re.IGNORECASE),
    'config': re.compile(r'\b(config|setting|option|parameter|env var)\b', re.IGNORECASE),
    'error': re.compile(r'\b(error|exception|bug|crash|fault)\b', re.IGNORECASE),
    'metric': re.compile(r'\b(metric|benchmark|score|rate|latency|throughput)\b', re.IGNORECASE),
    'database': re.compile(r'\b(database|table|schema|column|index)\b', re.IGNORECASE),
    'service': re.compile(r'\b(service|server|host|container|pod)\b', re.IGNORECASE),
}

_ENTITY_KEYWORD_TYPES = {
    'large language model': 'concept',
    'language model': 'concept',
    'application programming interface': 'concept',
    'database': 'technology',
    'graphical user interface': 'concept',
    'machine learning': 'concept',
    'natural language processing': 'concept',
    'knowledge graph': 'concept',
    'retrieval augmented generation': 'concept',
}

# ─── Canonical RDF Relationship Types & Extraction Contracts ────────────────
CANONICAL_RELATION_TYPES = (
    "IS_A",
    "PART_OF",
    "DEPENDS_ON",
    "CAUSES",
    "RELATES_TO",
    "USES",
    "IMPLEMENTS",
    "CREATED_BY",
    "LOCATED_IN",
    "HAS_ATTRIBUTE",
    "PRECEDES",
    "INTERACTS_WITH",
)

TRIPLE_EXTRACTION_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "SemanticTripleExtraction",
    "description": "Structured RDF semantic triples extracted from memory text",
    "type": "object",
    "properties": {
        "triples": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Subject entity or concept",
                    },
                    "predicate": {
                        "type": "string",
                        "enum": list(CANONICAL_RELATION_TYPES),
                        "description": "Explicit canonical relationship predicate",
                    },
                    "object": {
                        "type": "string",
                        "description": "Object entity, concept, or target value",
                    },
                    "timestamp": {
                        "type": "string",
                        "description": "ISO 8601 timestamp string",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "default": 1.0,
                        "description": "Extraction confidence or relationship strength",
                    },
                },
                "required": ["subject", "predicate", "object"],
                "additionalProperties": True,
            },
        },
    },
    "required": ["triples"],
}

TRIPLE_EXTRACTION_GBNF_GRAMMAR = r'''root ::= "{" ws "\"triples\":" ws "[" ws (triple ("," ws triple)*)? ws "]" ws "}"
triple ::= "{" ws "\"subject\":" ws string "," ws "\"predicate\":" ws predicate "," ws "\"object\":" ws string ("," ws "\"timestamp\":" ws string)? ("," ws "\"confidence\":" ws number)? ws "}"
predicate ::= "\"IS_A\"" | "\"PART_OF\"" | "\"DEPENDS_ON\"" | "\"CAUSES\"" | "\"RELATES_TO\"" | "\"USES\"" | "\"IMPLEMENTS\"" | "\"CREATED_BY\"" | "\"LOCATED_IN\"" | "\"HAS_ATTRIBUTE\"" | "\"PRECEDES\"" | "\"INTERACTS_WITH\""
string ::= "\"" ([^"\\] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]))* "\""
number ::= [0-9]+ ("." [0-9]+)?
ws ::= [ \t\n\r]*
'''

_SEMANTIC_TRIPLE_PATTERNS = [
    (re.compile(r'\b([A-Z][A-Za-z0-9_\-\s]{1,40}?)\s+(?:is\s+an?|are\s+an?|is\s+a\s+type\s+of|are\s+types?\s+of)\s+([A-Za-z0-9_\-\s]{2,40})\b', re.IGNORECASE), "IS_A"),
    (re.compile(r'\b([A-Z][A-Za-z0-9_\-\s]{1,40}?)\s+(?:depends?\s+on|relies?\s+on|requires?|needs?)\s+([A-Za-z0-9_\-\s]{2,40})\b', re.IGNORECASE), "DEPENDS_ON"),
    (re.compile(r'\b([A-Z][A-Za-z0-9_\-\s]{1,40}?)\s+(?:causes?|leads?\s+to|results?\s+in|triggers?)\s+([A-Za-z0-9_\-\s]{2,40})\b', re.IGNORECASE), "CAUSES"),
    (re.compile(r'\b([A-Z][A-Za-z0-9_\-\s]{1,40}?)\s+(?:uses?|utilizes?|operates?\s+with)\s+([A-Za-z0-9_\-\s]{2,40})\b', re.IGNORECASE), "USES"),
    (re.compile(r'\b([A-Z][A-Za-z0-9_\-\s]{1,40}?)\s+(?:implements?|supports?|enables?)\s+([A-Za-z0-9_\-\s]{2,40})\b', re.IGNORECASE), "IMPLEMENTS"),
    (re.compile(r'\b([A-Z][A-Za-z0-9_\-\s]{1,40}?)\s+(?:is\s+part\s+of|are\s+parts?\s+of|belongs?\s+to|is\s+a?\s*components?\s+of|are\s+components?\s+of)\s+([A-Za-z0-9_\-\s]{2,40})\b', re.IGNORECASE), "PART_OF"),
    (re.compile(r'\b([A-Z][A-Za-z0-9_\-\s]{1,40}?)\s+(?:was\s+created\s+by|were\s+created\s+by|is\s+built\s+by|are\s+built\s+by|authored\s+by)\s+([A-Za-z0-9_\-\s]{2,40})\b', re.IGNORECASE), "CREATED_BY"),
    (re.compile(r'\b([A-Z][A-Za-z0-9_\-\s]{1,40}?)\s+(?:is\s+located\s+in|are\s+located\s+in|runs?\s+on|hosted\s+at)\s+([A-Za-z0-9_\-\s]{2,40})\b', re.IGNORECASE), "LOCATED_IN"),
    (re.compile(r'\b([A-Z][A-Za-z0-9_\-\s]{1,40}?)\s+(?:has\s+attribute|has\s+property|has\s+feature|features)\s+([A-Za-z0-9_\-\s]{2,40})\b', re.IGNORECASE), "HAS_ATTRIBUTE"),
    (re.compile(r'\b([A-Z][A-Za-z0-9_\-\s]{1,40}?)\s+(?:precedes?|runs?\s+before|followed\s+by)\s+([A-Za-z0-9_\-\s]{2,40})\b', re.IGNORECASE), "PRECEDES"),
    (re.compile(r'\b([A-Z][A-Za-z0-9_\-\s]{1,40}?)\s+(?:relates?\s+to|associated\s+with|linked\s+to)\s+([A-Za-z0-9_\-\s]{2,40})\b', re.IGNORECASE), "RELATES_TO"),
]



# ═══════════════════════════════════════════════════════════════════════
# 1. REQUESTS IMPORT GUARD
# ═══════════════════════════════════════════════════════════════════════

# The _RequestsMissingStub class is defined above (outside try/except so
# it's always available). The guard at import time decides whether
# `requests` points to the real package or the stub.


# ═══════════════════════════════════════════════════════════════════════
# 2. INFRASTRUCTURE — DB CONNECTION, TABLE CREATION, EMBEDDING MODEL
# ═══════════════════════════════════════════════════════════════════════

def _get_model():
    """Return the sentence-transformer model, loading it on first call.

    State is held in ``_model_state`` (a mutable dict) so that the
    ServiceRegistry can reset it between tests.
    """
    if "model" in _model_state and _model_state["model"] is not None:
        _model_state["load_attempted"] = True
        return _model_state["model"]
    if os.environ.get("KOKERTECH_SKIP_EMBEDDINGS"):
        _model_state["model"] = None
        _model_state["load_attempted"] = True
        return None
    try:
        from sentence_transformers import SentenceTransformer
        model_name = CONFIG.get("embedding_model", "all-MiniLM-L6-v2")
        try:
            _model_state["model"] = SentenceTransformer(model_name, local_files_only=True)
        except Exception:
            _model_state["model"] = SentenceTransformer(model_name)
        logger.info(f"Loaded embedding model: {model_name}")
    except Exception as e:
        logger.warning(f"Failed to load embedding model: {e}")
        _model_state["model"] = None
    _model_state["load_attempted"] = True
    return _model_state["model"]


def _conn_is_for_path(conn: sqlite3.Connection, path: str) -> bool:
    """Return True if ``conn`` is attached to ``path`` (PRAGMA database_list).

    Stale-connection guard: pooled connections remember the file they were
    opened against, but ``sqlite3.Connection`` has no writable ``__dict__``
    (cannot tag the path at creation time), so the actual file is read back
    with ``PRAGMA database_list`` at pop time. A connection whose file does
    not match the current ``DB_PATH`` (e.g. a prior test's temp vault after
    a ``DB_PATH`` change) must never be reused -- ``_get_conn`` closes it
    instead. Paths are normalized with ``normcase`` so Windows drive-letter
    casing cannot cause false negatives.
    """
    try:
        rows = conn.execute("PRAGMA database_list").fetchall()
        main_file = rows[0][2] if rows else ""
        if not main_file:
            # In-memory / detached database -- identity is opaque, never reuse.
            return False
        return os.path.normcase(os.path.abspath(main_file)) == os.path.normcase(
            os.path.abspath(path)
        )
    except Exception as e:
        logger.debug(f"Pool probe failed for a cached connection: {e}")
        return False


def _get_conn() -> sqlite3.Connection:
    """Get a SQLite connection from the pool or create a new one.

    Enables WAL (Write-Ahead Log) journal mode on first open to allow
    concurrent readers + one writer.
    """
    # Initialize thread-local connection pool if it doesn't exist
    if not hasattr(_connection_pool, 'pool'):
        _connection_pool.pool = []
    
    with _pool_lock:
        while _connection_pool.pool:
            conn = _connection_pool.pool.pop()
            if _conn_is_for_path(conn, DB_PATH):
                return conn
            # Stale connection bound to a different DB file (e.g. a prior
            # test's temp vault after a DB_PATH change) -- close it and keep
            # looking. Root-cause fix for the KNOWLEDGE.md section 20.6
            # connection-pool bug class: test files that forget to call
            # _clear_connection_pool() no longer reuse the wrong database.
            try:
                conn.close()
            except Exception:
                pass
    
    # Create new connection if pool is empty
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA wal_autocheckpoint = 1000")
    conn.row_factory = sqlite3.Row
    return conn


def _return_conn(conn: Optional[sqlite3.Connection]) -> None:
    """Return a connection to the pool for reuse."""
    if conn is not None:
        # Rollback any pending transaction and reset connection state
        try:
            conn.rollback()
        except Exception:
            pass  # Ignore errors during rollback
        
        # Initialize thread-local connection pool if it doesn't exist
        if not hasattr(_connection_pool, 'pool'):
            _connection_pool.pool = []
        
        with _pool_lock:
            if len(_connection_pool.pool) < _MAX_POOL_SIZE:
                _connection_pool.pool.append(conn)
            else:
                conn.close()


def _clear_connection_pool() -> None:
    """Close all pooled connections and empty the pool.

    Call this when ``DB_PATH`` is changed (e.g. in test setUp/tearDown)
    to prevent stale connections from being reused against the wrong
    database file.
    """
    # Initialize thread-local connection pool if it doesn't exist
    if not hasattr(_connection_pool, 'pool'):
        _connection_pool.pool = []
    
    with _pool_lock:
        while _connection_pool.pool:
            conn = _connection_pool.pool.pop()
            try:
                conn.close()
            except Exception:
                pass


def checkpoint_wal(mode: str = "PASSIVE", db_path: Optional[str] = None) -> tuple[int, int, int]:
    """Execute SQLite WAL checkpoint and return (busy, log_frames, checkpointed_frames).

    Valid modes: "PASSIVE", "FULL", "RESTART", "TRUNCATE".
    - PASSIVE: Checkpoint as many frames as possible without waiting for readers.
    - FULL: Wait for readers to finish and checkpoint all frames.
    - RESTART: Like FULL, and restart log file so next writer writes at start.
    - TRUNCATE: Like RESTART, and truncate WAL file to zero bytes.

    Returns:
        (busy, log_frames, checkpointed_frames) where:
        - busy: 0 if complete, 1 if blocked by readers
        - log_frames: total frames in WAL file
        - checkpointed_frames: total frames successfully checkpointed to database
    """
    mode_upper = mode.upper()
    valid_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
    if mode_upper not in valid_modes:
        raise ValueError(f"Invalid WAL checkpoint mode: {mode}. Must be one of {valid_modes}")

    target_path = db_path or DB_PATH
    if not os.path.isfile(target_path):
        return (0, 0, 0)

    try:
        conn = sqlite3.connect(target_path, timeout=15.0)
        try:
            cursor = conn.execute(f"PRAGMA wal_checkpoint({mode_upper})")
            row = cursor.fetchone()
            if row:
                busy, log_frames, checkpointed = int(row[0]), int(row[1]), int(row[2])
                logger.debug(
                    f"wal_checkpoint({mode_upper}) on {os.path.basename(target_path)}: "
                    f"busy={busy}, log={log_frames}, checkpointed={checkpointed}"
                )
                return (busy, log_frames, checkpointed)
            return (0, 0, 0)
        finally:
            conn.close()
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        logger.warning(f"wal_checkpoint({mode_upper}) failed on {target_path}: {e}")
        return (1, 0, 0)


def auto_checkpoint_wal(
    threshold_bytes: Optional[Union[int, sqlite3.Connection]] = None,
    mode: str = "TRUNCATE",
    db_path: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> bool:
    """Trigger WAL checkpoint if WAL file exceeds threshold_bytes.

    Default threshold is configured via CONFIG['vault_wal_autocheckpoint_threshold_mb'] (default 16MB).
    Returns True if a checkpoint was triggered, False otherwise.
    """
    if isinstance(threshold_bytes, sqlite3.Connection):
        conn = threshold_bytes
        threshold_bytes = None

    target_path = db_path
    if not target_path and conn is not None:
        try:
            row = conn.execute("PRAGMA database_list").fetchone()
            if row and len(row) > 2 and row[2]:
                target_path = row[2]
        except (sqlite3.Error, OSError, ValueError):
            pass
    target_path = target_path or DB_PATH
    wal_path = f"{target_path}-wal"
    if not os.path.isfile(wal_path):
        return False

    if threshold_bytes is None:
        thresh_mb = CONFIG.get("vault_wal_autocheckpoint_threshold_mb", 16)
        threshold_bytes = int(thresh_mb) * 1024 * 1024

    try:
        wal_size = os.path.getsize(wal_path)
    except OSError:
        return False

    if wal_size >= threshold_bytes:
        size_mb = wal_size / (1024 * 1024)
        thresh_mb = threshold_bytes / (1024 * 1024)
        logger.info(
            f"WAL size ({size_mb:.2f} MB) >= threshold ({thresh_mb:.2f} MB); triggering wal_checkpoint({mode})..."
        )
        busy, log_frames, checkpointed = checkpoint_wal(mode=mode, db_path=target_path)
        logger.info(
            f"WAL checkpoint complete: busy={busy}, log_frames={log_frames}, checkpointed={checkpointed}"
        )
        return True
    return False


def close_vault(checkpoint_wal_first: bool = True) -> None:
    """Flush pending WAL frames and close all pooled connections."""
    if checkpoint_wal_first and os.path.isfile(DB_PATH):
        try:
            checkpoint_wal(mode="PASSIVE", db_path=DB_PATH)
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            logger.debug(f"close_vault checkpoint failed: {e}")
    _clear_connection_pool()


def _ensure_promoted_column():
    """Add episodic_journal.promoted if the DB predates v2.2.0.

    Lightweight ALTER TABLE migration, safe to call on every consolidation:
    no-ops when the column exists (Python 3.11 sqlite3 has no
    "IF NOT EXISTS" for ADD COLUMN, so duplicate-add errors are caught
    and treated as success).
    """
    try:
        with closing(_get_conn()) as conn:
            conn.execute(
                "ALTER TABLE episodic_journal ADD COLUMN promoted INTEGER NOT NULL DEFAULT 0")
            conn.commit()
            logger.info("episodic_journal: added promoted column (v2.2.0 migration)")
    except sqlite3.OperationalError as e:
        # "duplicate column" = already migrated — the expected path after
        # first run. Other OperationalErrors (locked DB, etc.) are real.
        if "duplicate column" not in str(e).lower():
            logger.warning(f"promoted-column migration check failed: {e}")


def ensure_tables_exist(force: bool = False):
    """Create all required SQLite tables if they don't exist.

    Called at startup and on first use. Idempotent.
    """
    global _db_initialized
    if _db_initialized and not force:
        return
    conn = _get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS core_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                content TEXT NOT NULL,
                node_type TEXT DEFAULT 'fact',
                importance_score INTEGER DEFAULT 5,
                embedding BLOB,
                tags TEXT DEFAULT '[]'
            );

            CREATE TABLE IF NOT EXISTS memory_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                target_id INTEGER,
                relationship_type TEXT DEFAULT 'RELATES_TO',
                relation_type TEXT DEFAULT 'RELATES_TO',
                weight REAL DEFAULT 1.0,
                confidence REAL DEFAULT 1.0,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                timestamp TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (source_id) REFERENCES core_memories(id) ON DELETE CASCADE,
                FOREIGN KEY (target_id) REFERENCES core_memories(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS bias_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                agent_id TEXT NOT NULL,
                bias_type TEXT NOT NULL,
                confidence_score REAL DEFAULT 0.5,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS growth_arcs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                agent_id TEXT NOT NULL,
                event_description TEXT,
                energy_shift REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS episodic_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                session_id TEXT,
                summary TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                importance_score INTEGER DEFAULT 5,
                metadata TEXT DEFAULT '{}',
                promoted INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS session_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                start_time TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                end_time TEXT,
                summary TEXT DEFAULT '',
                entry_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS persona_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                prompt TEXT,
                persona_a TEXT,
                persona_b TEXT,
                response_a TEXT,
                response_b TEXT,
                winner TEXT,
                notes TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS core_memories_fts USING fts5(
                content,
                content='core_memories',
                content_rowid='id'
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS episodic_journal_fts USING fts5(
                summary,
                content='episodic_journal',
                content_rowid='id'
            );

            CREATE TABLE IF NOT EXISTS kg_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type TEXT DEFAULT 'unknown',
                description TEXT,
                metadata TEXT DEFAULT '{}',
                embedding BLOB,
                mention_count INTEGER DEFAULT 1,
                first_seen TEXT DEFAULT (datetime('now','localtime')),
                last_seen TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS kg_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_entity_id INTEGER NOT NULL,
                target_entity_id INTEGER NOT NULL,
                relationship_type TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                evidence TEXT,
                source_memory_id INTEGER,
                source_type TEXT DEFAULT 'auto',
                metadata TEXT DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (source_entity_id) REFERENCES kg_entities(id) ON DELETE CASCADE,
                FOREIGN KEY (target_entity_id) REFERENCES kg_entities(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                query TEXT,
                result_count INTEGER,
                latency_ms REAL,
                search_type TEXT,
                metadata TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_core_memories_importance ON core_memories(importance_score DESC);
            CREATE INDEX IF NOT EXISTS idx_core_memories_timestamp ON core_memories(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_episodic_journal_session ON episodic_journal(session_id);
            CREATE INDEX IF NOT EXISTS idx_episodic_journal_timestamp ON episodic_journal(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_kg_entities_name ON kg_entities(name);
            CREATE INDEX IF NOT EXISTS idx_kg_relationships_source ON kg_relationships(source_entity_id);
            CREATE INDEX IF NOT EXISTS idx_kg_relationships_target ON kg_relationships(target_entity_id);
            CREATE INDEX IF NOT EXISTS idx_memory_links_source ON memory_links(source_id);
            CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_id);
        """)
        # Ensure backward compatibility: safe schema migration for memory_links
        try:
            cursor = conn.execute("PRAGMA table_info(memory_links)")
            existing_cols = {row["name"] if isinstance(row, dict) else row[1] for row in cursor.fetchall()}
            if "relation_type" not in existing_cols:
                conn.execute("ALTER TABLE memory_links ADD COLUMN relation_type TEXT DEFAULT ''")
                if "relationship_type" in existing_cols:
                    conn.execute("UPDATE memory_links SET relation_type = COALESCE(relationship_type, 'RELATES_TO')")
                else:
                    conn.execute("UPDATE memory_links SET relation_type = 'RELATES_TO'")
            if "weight" not in existing_cols:
                conn.execute("ALTER TABLE memory_links ADD COLUMN weight REAL DEFAULT 1.0")
            if "confidence" not in existing_cols:
                conn.execute("ALTER TABLE memory_links ADD COLUMN confidence REAL DEFAULT 1.0")
                conn.execute("UPDATE memory_links SET confidence = weight WHERE confidence IS NULL")
            if "timestamp" not in existing_cols:
                conn.execute("ALTER TABLE memory_links ADD COLUMN timestamp TEXT DEFAULT ''")
                conn.execute("UPDATE memory_links SET timestamp = COALESCE(created_at, datetime('now','localtime')) WHERE timestamp IS NULL OR timestamp = ''")
            if "relationship_type" not in existing_cols:
                conn.execute("ALTER TABLE memory_links ADD COLUMN relationship_type TEXT DEFAULT ''")
                conn.execute("UPDATE memory_links SET relationship_type = COALESCE(relation_type, 'RELATES_TO')")
            if "created_at" not in existing_cols:
                conn.execute("ALTER TABLE memory_links ADD COLUMN created_at TEXT DEFAULT ''")
                conn.execute("UPDATE memory_links SET created_at = datetime('now','localtime') WHERE created_at IS NULL OR created_at = ''")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_weight ON memory_links(weight)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_relation ON memory_links(relation_type)")
        except (sqlite3.Error, OSError, ValueError, KeyError) as e:
            logger.debug(f"memory_links schema migration check: {e}")
        conn.commit()
    finally:
        _return_conn(conn)
    _db_initialized = True


def _get_embedding(text: str) -> Optional[list]:
    """Cached, thread-safe text→embedding lookup.

    Returns ``model.encode(text)`` as a list of floats, or ``None`` when
    the model is not available. Cache key is the text sliced to 2000 chars.
    Cache bounded to ``_EMBEDDING_CACHE_MAX`` (128) entries.
    """
    if np is None:
        return None
    cache_key = text[:2000]
    with _embedding_lock:
        if cache_key in _embedding_cache:
            return _embedding_cache[cache_key]

    model = _get_model()
    if model is None:
        return None
    try:
        emb = model.encode(text[:2000])
        if isinstance(emb, np.ndarray):
            emb_list = emb.tolist()
        else:
            emb_list = list(emb)
        with _embedding_lock:
            max_cache = CONFIG.get("embedding_cache_max", _EMBEDDING_CACHE_MAX)
            if len(_embedding_cache) >= max_cache:
                # Evict oldest by insertion order (Python 3.7+ dict order)
                _embedding_cache.pop(next(iter(_embedding_cache)))
            _embedding_cache[cache_key] = emb_list
        return emb_list
    except Exception as e:
        logger.warning(f"Embedding failed: {e}")
        return None


# ─── Sprint 19.8: binary embedding codec + cached vector matrix ────

_EMBEDDING_DIM = int(CONFIG.get("embedding_dim", 384))


def _encode_embedding(emb) -> bytes:
    """Encode an embedding list/array as float16 bytes for compact storage.

    Saves ~91% vs the legacy JSON-text format (384 dims: 768 bytes binary
    vs ~8.4 KB JSON text, measured on production) — the primary cause of
    the 5+ GB vault.
    float16 precision loss (~1e-3 relative) is well below the noise floor
    of MiniLM embeddings and does not change top-K ordering in practice.
    """
    arr = np.asarray(emb, dtype=np.float16)
    return arr.tobytes()


_vector_cache_lock = threading.Lock()


class _VectorMatrixCache:
    """Cached float32 embedding matrix + row-id index for two-phase search.

    REGRESSION GUARD for the matrix/row alignment invariant: ``ids[i]``
    is the core_memories row id whose embedding occupies ``matrix[i]``
    (rows are L2-NORMALIZED in place at rebuild — cosine = plain dot
    product with the normalized query); ``dims[i]`` holds each row's
    dimension for shape filtering. Every
    mutation path that INSERTs or DELETEs from core_memories MUST call
    :func:`_invalidate_vector_cache` (store_memory and delete_entry do;
    if a future write site forgets, searches keep serving stale vectors
    — symptom: a newly stored memory is not findable by semantic search
    until process restart, NOT an exception).

    Rebuild (single SELECT of id+embedding, binary-first decode) costs
    one sequential scan and ~100-200 ms per 100k rows; steady-state
    queries afterwards are a single matrix-vector product (~10 ms per
    100k rows on CPU) — the ~100-500x win over the per-row Python loop.
    """

    __slots__ = ("ids", "matrix", "dims", "valid")

    def __init__(self):
        self.ids = None        # np.ndarray[int64] row ids
        self.matrix = None     # np.ndarray[float32] (n_rows, n_dims)
        self.dims = None       # np.ndarray[int64] per-row dims
        self.valid = False


_vector_matrix_cache = _VectorMatrixCache()


def _check_embedding_dims(embedding, context: str) -> None:
    """Warn when an embedding's dim differs from the active ``_EMBEDDING_DIM``.

    REGRESSION GUARD (Sprint 19.8.1, dims-drift): a mismatched-dim vector is
    silently EXCLUDED from the cached matrix by ``_get_vector_matrix()`` —
    a misconfigured embedding model would quietly shrink search coverage
    instead of raising. This surfaces the drift at write time, where the
    wrong model can still be caught. Never raises: a missing/odd-shaped
    embedding is handled by the normal store path.
    """
    if embedding is None or np is None:
        return
    try:
        dim = len(embedding)
    except TypeError:
        return
    if dim != _EMBEDDING_DIM:
        logger.warning(
            f"dims-drift: {context} produced a {dim}-dim embedding but the "
            f"vector cache filters for {_EMBEDDING_DIM}-dim — this memory will "
            f"be INVISIBLE to semantic search until dims match (wrong model "
            f"loaded?)"
        )


def _invalidate_vector_cache() -> None:
    """Drop the cached embedding matrix after any core_memories write."""
    with _vector_cache_lock:
        _vector_matrix_cache.valid = False


def check_vector_cache_health() -> dict:
    """Check integrity of the cached embedding matrix against stored core memories."""
    ensure_tables_exist()
    res = {
        "valid": bool(_vector_matrix_cache.valid),
        "cached_rows": len(_vector_matrix_cache.ids) if _vector_matrix_cache.ids is not None else 0,
        "db_embedded_rows": 0,
        "drift": 0,
    }
    try:
        with closing(_get_conn()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM core_memories WHERE embedding IS NOT NULL AND length(embedding) > 0"
            ).fetchone()
            res["db_embedded_rows"] = row[0] if row else 0
        if res["valid"]:
            res["drift"] = abs(res["cached_rows"] - res["db_embedded_rows"])
        else:
            res["drift"] = res["db_embedded_rows"]
    except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
        logger.debug(f"check_vector_cache_health failed: {e}")
    return res


def heal_vector_cache_if_needed(tolerance: int = 0) -> bool:
    """Rebuild or invalidate vector matrix cache if drift exceeds tolerance."""
    health = check_vector_cache_health()
    if health["valid"] and health["drift"] <= tolerance:
        return False
    logger.info(
        f"Vector cache drift detected (cached={health['cached_rows']}, "
        f"db={health['db_embedded_rows']}, drift={health['drift']}). Invalidating..."
    )
    _invalidate_vector_cache()
    return True


def _get_vector_matrix():
    """Return ``(ids, matrix, dims)`` for all embedded core memories.

    Two-phase search phase 1: rows with a decoded embedding whose dim
    matches ``_EMBEDDING_DIM``. Legacy JSON rows are decoded once here
    and stay cached until the next write. Rows are L2-normalized in
    place so per-query cost is ONE matrix-vector pass (measured 2026-09:
    recomputing row norms per query doubled warm latency). Returns
    ``(None, None, None)`` when numpy is missing or no embeddings exist.
    """
    with _vector_cache_lock:
        if _vector_matrix_cache.valid:
            return (_vector_matrix_cache.ids, _vector_matrix_cache.matrix,
                    _vector_matrix_cache.dims)
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, embedding FROM core_memories WHERE embedding IS NOT NULL"
        ).fetchall()
    ids, vecs = [], []
    excluded = 0
    for r in rows:
        emb = _decode_embedding(r["embedding"])
        if emb is None:
            continue
        if emb.ndim == 1 and emb.shape[0] == _EMBEDDING_DIM:
            ids.append(r["id"])
            vecs.append(emb)
        else:
            excluded += 1
    if excluded:
        # Census warning (once per rebuild): nonconforming dims are excluded
        # from search — the 23 x 768-dim one-off rows from July 2026 were the
        # first real instance. Surfaced instead of swallowed so a future
        # misconfiguration cannot silently shrink coverage.
        logger.warning(
            f"dims-drift: {excluded:,} embedded row(s) excluded from the vector "
            f"cache (dim != {_EMBEDDING_DIM}) — semantic search coverage reduced"
        )
    if not ids:
        return (None, None, None)
    ids_arr = np.asarray(ids, dtype=np.int64)
    dims_arr = np.full(len(vecs), _EMBEDDING_DIM, dtype=np.int64)
    matrix = np.vstack(vecs).astype(np.float32, copy=False)
    # L2-normalize rows IN PLACE on the fresh vstack buffer: cosine
    # similarity collapses to a single matrix-vector product per query
    # (unit rows x normalized query). Zero-vectors floored to 1e-12.
    norms = np.linalg.norm(matrix, axis=1)
    np.maximum(norms, 1e-12, out=norms)
    matrix /= norms[:, None]
    with _vector_cache_lock:
        _vector_matrix_cache.ids = ids_arr
        _vector_matrix_cache.matrix = matrix
        _vector_matrix_cache.dims = dims_arr
        _vector_matrix_cache.valid = True
    return (ids_arr, matrix, dims_arr)


def _search_vectors_by_embedding(query_emb, top_k: int) -> list:
    """Two-phase vector search: matrix-vector product, then fetch content.

    Replaces the per-row ``_cosine_similarity`` Python loop (Sprint 19.8).
    Cached rows are pre-normalized (see ``_get_vector_matrix``), so the
    score is ``unit_row @ (q / |q|)`` — exactly cosine, computed in ONE
    matrix pass per query. Returns ``[(id, 'core', content, score)]``
    sorted by descending score — the same tuple contract the per-row
    loop honoured, so RRF fusion and all callers are unaffected.
    """
    if np is None or query_emb is None:
        return []
    ids, matrix, _dims = _get_vector_matrix()
    if ids is None or matrix.size == 0:
        return []
    q = np.asarray(query_emb, dtype=np.float32)
    if q.ndim != 1 or q.shape[0] != matrix.shape[1]:
        return []
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        return []
    q = q / q_norm
    scores = matrix @ q
    if top_k < scores.shape[0]:
        top_idx = np.argpartition(-scores, top_k)[:top_k]
    else:
        top_idx = np.arange(scores.shape[0])
    top_idx = top_idx[np.argsort(-scores[top_idx])]
    top_ids = ids[top_idx].tolist()
    if not top_ids:
        return []
    placeholders = ",".join("?" * len(top_ids))
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            f"SELECT id, node_type, content FROM core_memories WHERE id IN ({placeholders})",
            top_ids,
        ).fetchall()
    by_id = {r["id"]: (r["node_type"], r["content"]) for r in rows}
    return [
        (mem_id, by_id[mem_id][0], by_id[mem_id][1], float(scores[idx]))
        for idx, mem_id in zip(top_idx.tolist(), top_ids)
        if mem_id in by_id
    ]


# ═══════════════════════════════════════════════════════════════════════
# 3. CORE CRUD — STORE, SEARCH, RETRIEVE MEMORIES
# ═══════════════════════════════════════════════════════════════════════

def _update_tags_async(memory_id: int, content: str):
    """ANTI-FRAGILITY: Uses ``get_provider()`` (not raw ``requests.post()``).

    Uses AI provider to extract tags from content and update the
    core_memories row. Runs silently — errors are logged, not raised.
    """
    try:
        provider = get_provider()
        messages = [
            {"role": "system", "content": "Extract 3-5 keyword tags from the text. Return ONLY a JSON array of strings."},
            {"role": "user", "content": content[:1000]}
        ]
        result = provider.chat_completion(
            messages=messages,
            model=CONFIG.get("model_name", ""),
            temperature=0.1,
            timeout=15
        )
        if result and result.get("error"):
            logger.warning(f"Tag extraction failed: {result['error']}")
            return
        raw = result.get("content", "[]")
        # Try to parse JSON array from response
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n")[1] if "\n" in raw else raw
            raw = raw.strip()
            if raw.endswith("```"):
                raw = raw[:-3]
        try:
            tags = json.loads(raw)
            if not isinstance(tags, list):
                tags = []
        except (json.JSONDecodeError, TypeError):
            tags = []
        with closing(_get_conn()) as conn:
            conn.execute("UPDATE core_memories SET tags = ? WHERE id = ?",
                         (json.dumps(tags), memory_id))
            conn.commit()
    except Exception as e:
        logger.warning(f"Tag update failed for memory {memory_id}: {e}")


def store_memory(content: str, node_type: str = "fact", importance: int = 5,
                 triples: list = None, extract_triples: bool = False) -> int:
    """Store a new memory in core_memories.

    Returns the new row ID. Also spawns an async tag-extraction thread
    and updates the FTS index. Errors raise on DB failure.
    Optionally attaches structured RDF triples (<Subject, Predicate, Object, Timestamp>).
    """
    ensure_tables_exist()
    embedding = _get_embedding(content)
    _check_embedding_dims(embedding, "store_memory")
    embedding_blob = _encode_embedding(embedding) if embedding is not None else None
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO core_memories (timestamp, content, node_type, importance_score, embedding, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, content, node_type, importance, embedding_blob, "[]")
        )
        mem_id = cursor.lastrowid
        conn.commit()
        # FTS sync (inside with block while conn is open)
        _fts_sync_core_insert(conn, mem_id, content)
    finally:
        _return_conn(conn)
    _invalidate_vector_cache()
    auto_checkpoint_wal()

    # Ingest structured semantic triples attached to this memory
    if triples:
        for t in triples:
            try:
                if isinstance(t, dict):
                    s = t.get("subject") or mem_id
                    p = t.get("predicate") or t.get("relation_type") or "RELATES_TO"
                    o = t.get("object")
                    t_ts = t.get("timestamp") or ts
                    w = float(t.get("weight") or t.get("confidence") or 1.0)
                    if o is not None:
                        res = store_semantic_triple(s, p, o, timestamp=t_ts, weight=w)
                        if res.get("source_id") and res["source_id"] != mem_id:
                            link_memories(mem_id, res["source_id"], "RELATES_TO", weight=w, timestamp=t_ts)
                elif isinstance(t, (list, tuple)):
                    if len(t) >= 3:
                        s, p, o = t[0], str(t[1]), t[2]
                        res = store_semantic_triple(s, p, o, timestamp=ts)
                        if res.get("source_id") and res["source_id"] != mem_id:
                            link_memories(mem_id, res["source_id"], "RELATES_TO", timestamp=ts)
                    elif len(t) == 2:
                        p = str(t[0])
                        o = t[1]
                        store_semantic_triple(mem_id, p, o, timestamp=ts)
            except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as err:
                logger.debug(f"Failed to attach triple to memory {mem_id}: {err}")

    # Optionally extract triples from content if requested
    if extract_triples:
        try:
            extracted = extract_semantic_triples(content, timestamp=ts)
            for t in extracted:
                res = store_semantic_triple(
                    t["subject"], t["predicate"], t["object"],
                    timestamp=t.get("timestamp") or ts,
                    weight=float(t.get("confidence") or 1.0)
                )
                if res.get("source_id") and res["source_id"] != mem_id:
                    link_memories(
                        mem_id, res["source_id"],
                        relationship_type="RELATES_TO",
                        relation_type="RELATES_TO",
                        weight=float(t.get("confidence") or 1.0),
                        timestamp=t.get("timestamp") or ts
                    )
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as err:
            logger.debug(f"Failed to extract triples for memory {mem_id}: {err}")

    # Async tag extraction (fire-and-forget)
    t = threading.Thread(target=_update_tags_async, args=(mem_id, content), daemon=True)
    t.start()
    return mem_id


def _decode_embedding(raw):
    """Decode a stored embedding value into a float32 numpy array.

    Binary-capable (Sprint 19.8): accepts BOTH storage formats so the
    2.1.0 migration is zero-downtime:
    - NEW: float16 raw ``bytes`` (2 bytes/dim)
    - LEGACY: JSON float array text (what 2.0.x stored via json.dumps,
      ~8.4 KB/row at 384 dims due to full-float repr)

    Returns None on any decode failure (graceful — the row is skipped
    by callers, never raised).
    """
    if np is None or raw is None:
        return None
    try:
        if isinstance(raw, (bytes, bytearray, memoryview)):
            return np.frombuffer(bytes(raw), dtype=np.float16).astype(np.float32)
        if isinstance(raw, str):
            return np.array(json.loads(raw), dtype=np.float32)
        return np.array(raw, dtype=np.float32)
    except Exception:
        return None


def _cosine_similarity(a, b) -> float:
    """Compute cosine similarity between two vectors.

    REGRESSION GUARD for the decode-compatibility invariant: ``b`` may be
    a JSON string (legacy rows) or raw bytes (2.1.0+ rows); both must
    score identically for identical vectors. If a refactor drops either
    format from :func:`_decode_embedding`, stored memories of that
    format silently stop matching in search — the "if reverted" symptom
    is a full-suite semantic-search result change, not an exception.
    """
    if np is None:
        return 0.0
    try:
        if isinstance(a, str):
            a = json.loads(a)
        a_arr = np.asarray(a, dtype=np.float32)
        b_arr = _decode_embedding(b)
        if b_arr is None or a_arr.shape != b_arr.shape:
            return 0.0
        norm_a = np.linalg.norm(a_arr)
        norm_b = np.linalg.norm(b_arr)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))
    except Exception as e:
        logger.debug(f"_cosine_similarity failed (graceful fallback to 0.0): {e}")
        return 0.0


def semantic_search(query: str, top_k: int = 5, candidate_limit: int = 50) -> list:
    """Search across long-term memory using hybrid search (Sprint 13).

    When ``CONFIG["hybrid_search_enabled"]`` is True (default), this
    delegates to :func:`hybrid_search` which combines vector cosine
    similarity with FTS5 keyword matching via Reciprocal Rank Fusion.

    When hybrid search is disabled or FTS is unavailable, falls back
    to the original pure-vector approach.

    Returns a list of tuples: ``(id, node_type, content, score)`` sorted
    by descending relevance.
    """
    ensure_tables_exist()
    hybrid_enabled = CONFIG.get("hybrid_search_enabled", True)
    if hybrid_enabled:
        try:
            alpha = adaptive_alpha(query)
            return hybrid_search(query, top_k, alpha, candidate_limit)
        except Exception as e:
            logger.debug(f"hybrid_search failed, falling back to pure vector search: {e}")
    # Fallback: pure vector search
    query_emb = _get_embedding(query)
    if query_emb is None:
        # No embeddings available — return recent memories sorted by importance
        with closing(_get_conn()) as conn:
            rows = conn.execute(
                "SELECT id, content, node_type, importance_score FROM core_memories "
                "ORDER BY importance_score DESC, timestamp DESC LIMIT ?",
                (top_k,)
            ).fetchall()
        return [(r["id"], r["node_type"], r["content"], float(r["importance_score"]) / 10.0) for r in rows]

    # Vector search — Sprint 19.8 two-phase fast path (matrix-vector product
    # over the cached embedding matrix; importance boost applied post-hoc
    # to preserve the 2.0.x score contract).
    results = _search_vectors_by_embedding(query_emb, candidate_limit)
    if results:
        imp_boost = 0.1 * (CONFIG.get("default_importance", 5) / 10.0)
        scored = [(rid, ntype, content, score + imp_boost)
                  for (rid, ntype, content, score) in results]
        scored.sort(key=lambda x: x[3], reverse=True)
        return scored[:top_k]
    # Fallback (no embeddings / dim mismatch): importance-ordered recents.
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, content, node_type, importance_score FROM core_memories "
            "ORDER BY importance_score DESC, timestamp DESC LIMIT ?",
            (top_k,)
        ).fetchall()
    return [(r["id"], r["node_type"], r["content"], float(r["importance_score"]) / 10.0) for r in rows]


def retrieve_all_memories() -> str:
    """Return a formatted string of all core memories.

    Returns "No core memories yet." if none exist.
    """
    ensure_tables_exist()
    try:
        with closing(_get_conn()) as conn:
            rows = conn.execute(
                "SELECT id, node_type, content FROM core_memories ORDER BY id DESC"
            ).fetchall()
    except sqlite3.Error as e:
        logger.error(f"Vault retrieval failed: {e}")
        return "No core memories yet."
    if not rows:
        return "No core memories yet."
    parts = []
    for r in rows:
        parts.append(f"[{r['node_type']}] {r['content']}")
    return "\n".join(parts)


def link_memories(source_id: int, target_id: int,
                  relationship_type: str = "RELATES_TO",
                  weight: float = 1.0,
                  relation_type: str = None,
                  timestamp: str = None,
                  confidence: float = None) -> int:
    """Create or update a link between two memories with temporal and weight attributes.

    Returns the new link ID.
    """
    ensure_tables_exist()
    if source_id is None or target_id is None:
        raise ValueError("source_id and target_id must not be None")
    source_id = int(source_id)
    target_id = int(target_id)
    rel = relation_type or relationship_type or "RELATES_TO"
    w = float(weight if weight is not None else 1.0)
    conf = float(confidence if confidence is not None else w)
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    with closing(_get_conn()) as conn:
        cursor = conn.execute(
            "INSERT INTO memory_links (source_id, target_id, relationship_type, relation_type, weight, confidence, created_at, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, target_id, rel, rel, w, conf, ts, ts)
        )
        conn.commit()
        return cursor.lastrowid


def reinforce_link(source_id: int, target_id: int, delta: float = 0.1,
                   relation_type: str = None) -> float:
    """Reinforce the connection weight between two memories.

    Increases link weight by delta (default +0.1) and updates timestamp.
    If no link exists between the nodes, creates one with weight 1.0 + delta.

    Returns the new weight (float).
    """
    ensure_tables_exist()
    if source_id is None or target_id is None:
        raise ValueError("source_id and target_id must not be None")
    source_id = int(source_id)
    target_id = int(target_id)
    ts = datetime.now(timezone.utc).isoformat()
    with closing(_get_conn()) as conn:
        query = (
            "SELECT rowid AS id, weight FROM memory_links WHERE source_id = ? AND target_id = ?"
        )
        params = [source_id, target_id]
        if relation_type:
            query += " AND (relation_type = ? OR relationship_type = ?)"
            params.extend([relation_type, relation_type])
        query += " ORDER BY rowid DESC LIMIT 1"
        row = conn.execute(query, params).fetchone()

        if row:
            current_w = float(row["weight"] if row["weight"] is not None else 1.0)
            new_w = max(0.0, round(current_w + delta, 4))
            conn.execute(
                "UPDATE memory_links SET weight = ?, confidence = ?, timestamp = ? WHERE rowid = ?",
                (new_w, new_w, ts, row["id"])
            )
            conn.commit()
            return new_w

        # Check reverse link if directed not found
        rev_query = (
            "SELECT rowid AS id, weight FROM memory_links WHERE source_id = ? AND target_id = ?"
        )
        rev_params = [target_id, source_id]
        if relation_type:
            rev_query += " AND (relation_type = ? OR relationship_type = ?)"
            rev_params.extend([relation_type, relation_type])
        rev_query += " ORDER BY rowid DESC LIMIT 1"
        rev_row = conn.execute(rev_query, rev_params).fetchone()
        if rev_row:
            current_w = float(rev_row["weight"] if rev_row["weight"] is not None else 1.0)
            new_w = max(0.0, round(current_w + delta, 4))
            conn.execute(
                "UPDATE memory_links SET weight = ?, confidence = ?, timestamp = ? WHERE rowid = ?",
                (new_w, new_w, ts, rev_row["id"])
            )
            conn.commit()
            return new_w

        # Create new link
        new_w = max(0.0, round(1.0 + delta, 4))
        rel = relation_type or "RELATES_TO"
        conn.execute(
            "INSERT INTO memory_links (source_id, target_id, relationship_type, relation_type, weight, confidence, created_at, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, target_id, rel, rel, new_w, new_w, ts, ts)
        )
        conn.commit()
        return new_w


def apply_link_decay(decay_factor: float = 0.95, min_weight: float = 0.05,
                     prune_zero: bool = False) -> int:
    """Apply temporal exponential decay to all link weights in the graph.

    Multiplies link weights by decay_factor (default 0.95).
    If prune_zero is True, deletes links where weight drops below min_weight.

    Returns the number of links updated.
    """
    ensure_tables_exist()
    if decay_factor < 0:
        raise ValueError("decay_factor must be non-negative")
    factor = float(decay_factor)
    with closing(_get_conn()) as conn:
        cursor = conn.execute(
            "UPDATE memory_links SET weight = ROUND(COALESCE(weight, 1.0) * ?, 4), "
            "confidence = ROUND(COALESCE(confidence, 1.0) * ?, 4)",
            (factor, factor)
        )
        updated_count = cursor.rowcount
        if prune_zero:
            conn.execute(
                "DELETE FROM memory_links WHERE weight < ?", (min_weight,)
            )
        conn.commit()
        auto_checkpoint_wal(conn)
        return updated_count


def get_memory_links(node_id: int = None, direction: str = "both",
                     min_weight: float = 0.0) -> list:
    """Retrieve memory links, optionally filtered by node_id, direction, and min_weight.

    direction: 'outgoing', 'incoming', or 'both' (default).
    Returns list of link dicts.
    """
    ensure_tables_exist()
    min_w = float(min_weight) if min_weight is not None else 0.0
    with closing(_get_conn()) as conn:
        if node_id is None:
            rows = conn.execute(
                "SELECT rowid AS id, source_id, target_id, relationship_type, "
                "COALESCE(relation_type, relationship_type) AS relation_type, "
                "COALESCE(weight, 1.0) AS weight, COALESCE(confidence, 1.0) AS confidence, "
                "COALESCE(timestamp, created_at) AS timestamp, created_at "
                "FROM memory_links WHERE COALESCE(weight, 1.0) >= ? "
                "ORDER BY weight DESC, rowid DESC",
                (min_w,)
            ).fetchall()
        else:
            if direction == "outgoing":
                rows = conn.execute(
                    "SELECT rowid AS id, source_id, target_id, relationship_type, "
                    "COALESCE(relation_type, relationship_type) AS relation_type, "
                    "COALESCE(weight, 1.0) AS weight, COALESCE(confidence, 1.0) AS confidence, "
                    "COALESCE(timestamp, created_at) AS timestamp, created_at "
                    "FROM memory_links WHERE source_id = ? AND COALESCE(weight, 1.0) >= ? "
                    "ORDER BY weight DESC, rowid DESC",
                    (node_id, min_w)
                ).fetchall()
            elif direction == "incoming":
                rows = conn.execute(
                    "SELECT rowid AS id, source_id, target_id, relationship_type, "
                    "COALESCE(relation_type, relationship_type) AS relation_type, "
                    "COALESCE(weight, 1.0) AS weight, COALESCE(confidence, 1.0) AS confidence, "
                    "COALESCE(timestamp, created_at) AS timestamp, created_at "
                    "FROM memory_links WHERE target_id = ? AND COALESCE(weight, 1.0) >= ? "
                    "ORDER BY weight DESC, rowid DESC",
                    (node_id, min_w)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT rowid AS id, source_id, target_id, relationship_type, "
                    "COALESCE(relation_type, relationship_type) AS relation_type, "
                    "COALESCE(weight, 1.0) AS weight, COALESCE(confidence, 1.0) AS confidence, "
                    "COALESCE(timestamp, created_at) AS timestamp, created_at "
                    "FROM memory_links WHERE (source_id = ? OR target_id = ?) AND COALESCE(weight, 1.0) >= ? "
                    "ORDER BY weight DESC, rowid DESC",
                    (node_id, node_id, min_w)
                ).fetchall()
        return [dict(r) for r in rows]


def traverse_subgraph(start_node_id: int, max_hops: int = 2,
                      min_weight: float = 0.1, direction: str = "both") -> dict:
    """Multi-hop graph traversal from a starting core_memory node.

    Explores connected nodes up to max_hops away with edge weight >= min_weight.
    Computes path traversal weights (decayed by edge weights along the path).

    Returns
    -------
    dict
        {
            "start_node_id": int,
            "nodes": list[dict],       # Connected nodes with hop, path_weight, content
            "links": list[dict],       # Traversed edges with weights and relations
            "traversal_weights": dict, # {node_id: best_path_weight}
            "paths": dict,             # {node_id: [path_node_ids]}
        }
    """
    ensure_tables_exist()
    if start_node_id is None:
        return {"start_node_id": None, "nodes": [], "links": [], "traversal_weights": {}, "paths": {}}

    with closing(_get_conn()) as conn:
        start_row = conn.execute(
            "SELECT id, content, node_type, importance_score, timestamp FROM core_memories WHERE id = ?",
            (start_node_id,)
        ).fetchone()

        if not start_row:
            return {"start_node_id": start_node_id, "nodes": [], "links": [], "traversal_weights": {}, "paths": {}}

        start_node = {
            "id": start_row["id"],
            "content": start_row["content"],
            "node_type": start_row["node_type"],
            "importance_score": start_row["importance_score"],
            "timestamp": start_row["timestamp"],
            "hop": 0,
            "path_weight": 1.0,
        }

        if max_hops <= 0:
            return {
                "start_node_id": start_node_id,
                "nodes": [start_node],
                "links": [],
                "traversal_weights": {start_node_id: 1.0},
                "paths": {start_node_id: [start_node_id]},
            }

        queue = deque([(start_node_id, 0, 1.0, [start_node_id])])
        visited_weights = {start_node_id: 1.0}
        visited_nodes = {start_node_id: start_node}
        collected_links = {}
        paths = {start_node_id: [start_node_id]}

        while queue:
            curr_id, curr_hop, curr_weight, curr_path = queue.popleft()
            if curr_hop >= max_hops:
                continue

            if direction == "outgoing":
                sql = "SELECT * FROM memory_links WHERE source_id = ?"
                params = (curr_id,)
            elif direction == "incoming":
                sql = "SELECT * FROM memory_links WHERE target_id = ?"
                params = (curr_id,)
            else:
                sql = "SELECT * FROM memory_links WHERE source_id = ? OR target_id = ?"
                params = (curr_id, curr_id)

            links = conn.execute(sql, params).fetchall()

            for link_row in links:
                link = dict(link_row)
                w = float(link.get("weight") if link.get("weight") is not None else 1.0)
                if w < min_weight:
                    continue

                src = link["source_id"]
                tgt = link["target_id"]
                neighbor_id = tgt if src == curr_id else src
                if neighbor_id == curr_id or neighbor_id in curr_path:
                    continue

                next_weight = round(curr_weight * w, 4)
                if next_weight < min_weight:
                    continue

                # Normalize link fields for consistency
                link["relation_type"] = link.get("relation_type") or link.get("relationship_type") or "RELATES_TO"
                link["relationship_type"] = link["relation_type"]
                link["weight"] = w
                link["confidence"] = float(link.get("confidence") if link.get("confidence") is not None else w)
                collected_links[link["id"]] = link

                if neighbor_id not in visited_weights or next_weight > visited_weights[neighbor_id]:
                    if neighbor_id not in visited_nodes:
                        nrow = conn.execute(
                            "SELECT id, content, node_type, importance_score, timestamp FROM core_memories WHERE id = ?",
                            (neighbor_id,)
                        ).fetchone()
                        if not nrow:
                            continue
                        visited_nodes[neighbor_id] = {
                            "id": nrow["id"],
                            "content": nrow["content"],
                            "node_type": nrow["node_type"],
                            "importance_score": nrow["importance_score"],
                            "timestamp": nrow["timestamp"],
                            "hop": curr_hop + 1,
                            "path_weight": next_weight,
                        }
                    else:
                        visited_nodes[neighbor_id]["path_weight"] = next_weight
                        visited_nodes[neighbor_id]["hop"] = min(visited_nodes[neighbor_id]["hop"], curr_hop + 1)

                    visited_weights[neighbor_id] = next_weight
                    paths[neighbor_id] = curr_path + [neighbor_id]

                    if curr_hop + 1 < max_hops:
                        queue.append((neighbor_id, curr_hop + 1, next_weight, curr_path + [neighbor_id]))

        return {
            "start_node_id": start_node_id,
            "nodes": list(visited_nodes.values()),
            "links": list(collected_links.values()),
            "traversal_weights": visited_weights,
            "paths": paths,
        }


def get_triple_extraction_schema() -> dict:
    """Return a deep copy of the JSON schema contract for RDF semantic triple extraction."""
    return copy.deepcopy(TRIPLE_EXTRACTION_JSON_SCHEMA)


def get_triple_extraction_gbnf() -> str:
    """Return the GBNF grammar specification for structured LLM triple extraction."""
    return TRIPLE_EXTRACTION_GBNF_GRAMMAR


def extract_semantic_triples(text: str, timestamp: str = None,
                             use_llm: bool = False, provider=None) -> list:
    """Extract structured RDF semantic triples (<Subject, Predicate, Object, Timestamp>) from text.

    Uses LLM with JSON schema / GBNF contract when use_llm is True and provider is available,
    falling back to canonical pattern matching for zero-shot offline extraction.
    """
    if not text or not text.strip():
        return []

    ts = timestamp or datetime.now(timezone.utc).isoformat()
    triples = []
    seen = set()

    if use_llm:
        try:
            prov = provider or get_provider()
            prompt = (
                f"Extract structured RDF semantic triples from the following text according to this schema:\n"
                f"{json.dumps(TRIPLE_EXTRACTION_JSON_SCHEMA, indent=2)}\n\n"
                f"Allowed predicates: {list(CANONICAL_RELATION_TYPES)}\n\n"
                f"Text:\n{text[:2000]}\n\n"
                f"Return JSON adhering strictly to: {{\"triples\": [ {{\"subject\": \"...\", \"predicate\": \"...\", \"object\": \"...\", \"timestamp\": \"{ts}\", \"confidence\": 1.0}} ]}}.\n"
                f"Return ONLY valid JSON."
            )
            messages = [
                {"role": "system", "content": "You are an RDF semantic triple extraction engine."},
                {"role": "user", "content": prompt}
            ]
            result = prov.chat_completion(messages=messages, temperature=0.1, max_tokens=400)
            content = result.get("content", "") or result.get("text", "")
            if content:
                raw = content.strip()
                if raw.startswith("```"):
                    lines = raw.split("\n")
                    raw = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
                    raw = raw.strip()
                parsed = json.loads(raw)
                extracted_list = parsed.get("triples", []) if isinstance(parsed, dict) else parsed if isinstance(parsed, list) else []
                for item in extracted_list:
                    if isinstance(item, dict) and "subject" in item and "predicate" in item and "object" in item:
                        pred = str(item["predicate"]).strip().upper().replace(" ", "_")
                        if pred not in CANONICAL_RELATION_TYPES:
                            pred = "RELATES_TO"
                        s = str(item["subject"]).strip()
                        o = str(item["object"]).strip()
                        key = (s.lower(), pred, o.lower())
                        if key not in seen and s and o:
                            seen.add(key)
                            triples.append({
                                "subject": s,
                                "predicate": pred,
                                "relation_type": pred,
                                "object": o,
                                "timestamp": item.get("timestamp") or ts,
                                "confidence": float(item.get("confidence") or 1.0),
                            })
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as e:
            logger.debug(f"LLM triple extraction failed (falling back to pattern matching): {e}")

    # Fallback / heuristic pattern matcher
    if not triples:
        for pattern, rel_type in _SEMANTIC_TRIPLE_PATTERNS:
            for match in pattern.finditer(text):
                s = match.group(1).strip()
                o = match.group(2).strip().rstrip(".,;!? ")
                if s and o:
                    key = (s.lower(), rel_type, o.lower())
                    if key not in seen:
                        seen.add(key)
                        triples.append({
                            "subject": s,
                            "predicate": rel_type,
                            "relation_type": rel_type,
                            "object": o,
                            "timestamp": ts,
                            "confidence": 1.0,
                        })

    return triples


def store_semantic_triple(subject: Union[str, int], predicate: str,
                          object_: Union[str, int], timestamp: str = None,
                          weight: float = 1.0, confidence: float = 1.0,
                          create_nodes: bool = True) -> dict:
    """Store a structured RDF semantic triple in the vault.

    Links subject and object in core_memories and memory_links with explicit
    relation_type and temporal timestamp. If subject or object_ are strings,
    resolves them to existing memory IDs or creates new concept nodes if create_nodes is True.

    Returns dict with {link_id, source_id, target_id, subject, predicate, object, timestamp, weight}.
    """
    ensure_tables_exist()
    if subject is None or not str(subject).strip():
        raise ValueError("Subject must not be empty or None")
    if object_ is None or not str(object_).strip():
        raise ValueError("Object must not be empty or None")

    ts = timestamp or datetime.now(timezone.utc).isoformat()
    w = float(weight if weight is not None else 1.0)
    conf = float(confidence if confidence is not None else w)
    pred = str(predicate).strip().upper().replace(" ", "_") if predicate else "RELATES_TO"
    if pred not in CANONICAL_RELATION_TYPES:
        pred = "RELATES_TO"

    with closing(_get_conn()) as conn:
        # Resolve subject
        if isinstance(subject, int):
            source_id = subject
            row = conn.execute("SELECT content FROM core_memories WHERE id = ?", (subject,)).fetchone()
            if not row:
                if not create_nodes:
                    raise ValueError(f"Subject memory ID {subject} does not exist and create_nodes is False")
                subj_text = str(subject)
            else:
                subj_text = row["content"]
        else:
            subj_text = str(subject).strip()
            row = conn.execute(
                "SELECT id FROM core_memories WHERE LOWER(content) = LOWER(?) LIMIT 1",
                (subj_text,)
            ).fetchone()
            if row:
                source_id = row["id"]
            elif create_nodes:
                source_id = store_memory(subj_text, node_type="concept")
            else:
                raise ValueError(f"Subject '{subj_text}' does not exist and create_nodes is False")

        # Resolve object
        if isinstance(object_, int):
            target_id = object_
            row = conn.execute("SELECT content FROM core_memories WHERE id = ?", (object_,)).fetchone()
            if not row:
                if not create_nodes:
                    raise ValueError(f"Object memory ID {object_} does not exist and create_nodes is False")
                obj_text = str(object_)
            else:
                obj_text = row["content"]
        else:
            obj_text = str(object_).strip()
            row = conn.execute(
                "SELECT id FROM core_memories WHERE LOWER(content) = LOWER(?) LIMIT 1",
                (obj_text,)
            ).fetchone()
            if row:
                target_id = row["id"]
            elif create_nodes:
                target_id = store_memory(obj_text, node_type="concept")
            else:
                raise ValueError(f"Object '{obj_text}' does not exist and create_nodes is False")

    # Create link
    link_id = link_memories(
        source_id=source_id,
        target_id=target_id,
        relationship_type=pred,
        weight=w,
        relation_type=pred,
        timestamp=ts,
        confidence=conf,
    )

    # Sync with knowledge graph if enabled
    if _GRAPHRAG_ENABLED and CONFIG.get("graphrag_enabled", True):
        try:
            s_eid = store_entity(subj_text, entity_type="concept")
            o_eid = store_entity(obj_text, entity_type="concept")
            if s_eid > 0 and o_eid > 0:
                store_relationship(
                    s_eid, o_eid, pred,
                    confidence=conf,
                    evidence=f"Triple: <{subj_text}, {pred}, {obj_text}>",
                    source_memory_id=source_id,
                    source_type="triple"
                )
        except (sqlite3.Error, OSError, ValueError, KeyError) as e:
            logger.debug(f"KG sync for triple failed: {e}")

    return {
        "link_id": link_id,
        "source_id": source_id,
        "target_id": target_id,
        "subject": subj_text,
        "predicate": pred,
        "relation_type": pred,
        "object": obj_text,
        "timestamp": ts,
        "weight": w,
        "confidence": conf,
    }


# Convenience alias
store_triple = store_semantic_triple


def get_semantic_triples(node_id: int = None, limit: int = 100) -> list:
    """Retrieve structured RDF semantic triples joined with core memory contents.

    Returns list of dicts: {link_id, source_id, target_id, subject, predicate, relation_type, object, timestamp, weight, confidence}.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        if node_id is None:
            rows = conn.execute(
                "SELECT l.rowid AS link_id, l.source_id, l.target_id, "
                "COALESCE(l.relation_type, l.relationship_type) AS predicate, "
                "COALESCE(l.relation_type, l.relationship_type) AS relation_type, "
                "COALESCE(l.weight, 1.0) AS weight, "
                "COALESCE(l.confidence, l.weight, 1.0) AS confidence, "
                "COALESCE(l.timestamp, l.created_at) AS timestamp, "
                "s.content AS subject, t.content AS object "
                "FROM memory_links l "
                "LEFT JOIN core_memories s ON l.source_id = s.id "
                "LEFT JOIN core_memories t ON l.target_id = t.id "
                "ORDER BY l.rowid DESC LIMIT ?",
                (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT l.rowid AS link_id, l.source_id, l.target_id, "
                "COALESCE(l.relation_type, l.relationship_type) AS predicate, "
                "COALESCE(l.relation_type, l.relationship_type) AS relation_type, "
                "COALESCE(l.weight, 1.0) AS weight, "
                "COALESCE(l.confidence, l.weight, 1.0) AS confidence, "
                "COALESCE(l.timestamp, l.created_at) AS timestamp, "
                "s.content AS subject, t.content AS object "
                "FROM memory_links l "
                "LEFT JOIN core_memories s ON l.source_id = s.id "
                "LEFT JOIN core_memories t ON l.target_id = t.id "
                "WHERE l.source_id = ? OR l.target_id = ? "
                "ORDER BY l.rowid DESC LIMIT ?",
                (node_id, node_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# 4. BIAS / GROWTH TRACKING
# ═══════════════════════════════════════════════════════════════════════

def log_bias(agent_id: str, bias_type: str, confidence_score: float = 0.5, description: str = "") -> int:
    """Log a self-correction bias entry.

    Returns the new row ID.
    """
    ensure_tables_exist()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(_get_conn()) as conn:
        cursor = conn.execute(
            "INSERT INTO bias_ledger (timestamp, agent_id, bias_type, confidence_score, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (ts, agent_id, bias_type, confidence_score, description)
        )
        conn.commit()
        return cursor.lastrowid


def get_recent_bias(agent_id: str, limit: int = 10) -> list:
    """Retrieve recent bias entries for an agent.

    Returns a list of dicts with timestamp, bias_type, confidence_score, description.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT timestamp, bias_type, confidence_score, description "
            "FROM bias_ledger WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
            (agent_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def log_growth(agent_id: str, event_description: str, energy_shift: float = 0.0) -> int:
    """Log a growth/energy-shift event.

    Returns the new row ID.
    """
    ensure_tables_exist()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(_get_conn()) as conn:
        cursor = conn.execute(
            "INSERT INTO growth_arcs (timestamp, agent_id, event_description, energy_shift) "
            "VALUES (?, ?, ?, ?)",
            (ts, agent_id, event_description, energy_shift)
        )
        conn.commit()
        return cursor.lastrowid


def get_growth_arc(agent_id: str, limit: int = 10) -> list:
    """Retrieve recent growth events for an agent.

    Returns a list of dicts with timestamp, event_description, energy_shift.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT timestamp, event_description, energy_shift "
            "FROM growth_arcs WHERE agent_id = ? ORDER BY id DESC LIMIT ?",
            (agent_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# 5. FTS5 — FULL-TEXT SEARCH
# ═══════════════════════════════════════════════════════════════════════

def _fts_sync_core_insert(conn, row_id: int, content: str):
    """Insert a row into core_memories_fts after a core_memories INSERT."""
    if not _FTS_SYNC_ENABLED:
        return
    try:
        conn.execute(
            "INSERT INTO core_memories_fts (rowid, content) VALUES (?, ?)",
            (row_id, content)
        )
        conn.commit()
    except sqlite3.OperationalError as e:
        logger = get_logger(name="MemoryVault")
        logger.warning(f"_fts_sync_core_insert failed for row {row_id}: {e}")


def _fts_sync_core_delete(conn, row_id: int):
    """Remove a row from core_memories_fts after a core_memories DELETE."""
    if not _FTS_SYNC_ENABLED:
        return
    try:
        conn.execute("DELETE FROM core_memories_fts WHERE rowid = ?", (row_id,))
        conn.commit()
    except sqlite3.OperationalError as e:
        # Breadcrumb (silent-catch audit): a failed FTS delete silently
        # desyncs the index and masks real bugs in later searches
        # (see test_neural_graph FTS-rebuild lesson).
        logger.warning(f"core_memories_fts delete failed for row {row_id}: {e}")


def _fts_sync_episodic_insert(conn, row_id: int, summary: str):
    """Insert a row into episodic_journal_fts after an episodic_journal INSERT."""
    if not _FTS_SYNC_ENABLED:
        return
    try:
        conn.execute(
            "INSERT INTO episodic_journal_fts (rowid, summary) VALUES (?, ?)",
            (row_id, summary)
        )
        conn.commit()
    except sqlite3.OperationalError as e:
        # Breadcrumb (silent-catch audit): FTS insert failure desyncs the
        # journal index -- future searches silently miss this entry.
        logger.warning(f"episodic_journal_fts insert failed for row {row_id}: {e}")


def _fts_sync_episodic_delete(conn, row_id: int):
    """Remove a row from episodic_journal_fts after an episodic_journal DELETE."""
    if not _FTS_SYNC_ENABLED:
        return
    try:
        conn.execute("DELETE FROM episodic_journal_fts WHERE rowid = ?", (row_id,))
        conn.commit()
    except sqlite3.OperationalError as e:
        # Breadcrumb (silent-catch audit): stale FTS rows keep deleted
        # journal entries searchable -- desync that masks real bugs.
        logger.warning(f"episodic_journal_fts delete failed for row {row_id}: {e}")


def rebuild_fts_index():
    """Rebuild FTS5 index from core_memories and episodic_journal content.

    Called on first use if FTS tables are empty, and as a fallback
    when sync issues are suspected. Safe to call multiple times.
    """
    ensure_tables_exist(force=True)
    with closing(_get_conn()) as conn:
        try:
            conn.execute("INSERT INTO core_memories_fts(core_memories_fts) VALUES('rebuild')")
            conn.execute("INSERT INTO episodic_journal_fts(episodic_journal_fts) VALUES('rebuild')")
            conn.commit()
            logger.info("FTS index rebuilt successfully")
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS rebuild failed: {e}")


def check_fts_health() -> dict:
    """Check row count consistency between source tables and FTS5 index tables.

    Returns dict with counts and parity diffs:
    {
        "healthy": bool,
        "core_count": int,
        "fts_core_count": int,
        "core_diff": int,
        "episodic_count": int,
        "fts_episodic_count": int,
        "episodic_diff": int,
        "error": Optional[str],
    }
    """
    ensure_tables_exist()
    res = {
        "healthy": True,
        "core_count": 0,
        "fts_core_count": 0,
        "core_diff": 0,
        "episodic_count": 0,
        "fts_episodic_count": 0,
        "episodic_diff": 0,
        "error": None,
    }
    try:
        with closing(_get_conn()) as conn:
            c1 = conn.execute("SELECT COUNT(*) FROM core_memories").fetchone()[0]
            # In FTS5 external content tables, the shadow table <name>_docsize stores
            # the exact count of indexed rows in the FTS index structure.
            c2_row = conn.execute("SELECT COUNT(*) FROM core_memories_fts_docsize").fetchone()
            c2 = c2_row[0] if c2_row else 0

            e1 = conn.execute("SELECT COUNT(*) FROM episodic_journal").fetchone()[0]
            e2_row = conn.execute("SELECT COUNT(*) FROM episodic_journal_fts_docsize").fetchone()
            e2 = e2_row[0] if e2_row else 0

            res["core_count"] = c1
            res["fts_core_count"] = c2
            res["core_diff"] = abs(c1 - c2)
            res["episodic_count"] = e1
            res["fts_episodic_count"] = e2
            res["episodic_diff"] = abs(e1 - e2)
            if res["core_diff"] > 0 or res["episodic_diff"] > 0:
                res["healthy"] = False
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        res["healthy"] = False
        res["error"] = str(e)
    return res


def heal_fts_index_if_needed(tolerance: int = 0) -> bool:
    """Inspect FTS health and execute rebuild_fts_index() if desync exceeds tolerance.

    Returns True if rebuild was triggered and succeeded, False if already healthy or failed.
    """
    health = check_fts_health()
    if health["healthy"] and health["core_diff"] <= tolerance and health["episodic_diff"] <= tolerance:
        return False

    logger.warning(
        f"FTS index desync detected (core diff={health['core_diff']}, episodic diff={health['episodic_diff']}, "
        f"error={health['error']}). Triggering self-healing rebuild_fts_index()..."
    )
    try:
        rebuild_fts_index()
        return True
    except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
        logger.error(f"FTS self-healing rebuild failed: {e}")
        return False


def fts5_search(query: str, top_k: int = 10, allow_self_heal: bool = True) -> list:
    """Full-text search using SQLite FTS5 with BM25 ranking.

    Searches across both core_memories and episodic_journal FTS tables.
    Uses the built-in ``bm25()`` function for scoring (lower is better
    in FTS5, so we negate for consistency with cosine similarity where
    higher = more relevant).

    If an OperationalError occurs and allow_self_heal is True (and CONFIG['vault_fts_self_heal'] is True),
    automatically rebuilds the FTS index once and retries the search.

    Returns a list of tuples: ``(id, source, content, score)`` sorted
    by descending relevance.
    """
    ensure_tables_exist()
    results = []
    need_heal = False

    with closing(_get_conn()) as conn:
        # Search core_memories
        try:
            fts_rows = conn.execute(
                "SELECT rowid, content, bm25(core_memories_fts) AS score "
                "FROM core_memories_fts WHERE core_memories_fts MATCH ? ORDER BY score LIMIT ?",
                (query, top_k)
            ).fetchall()
            for r in fts_rows:
                results.append((r["rowid"], "core", r["content"], -r["score"]))
        except sqlite3.OperationalError as e:
            logger.debug(f"core_memories_fts search error: {e}")
            need_heal = True

        # Search episodic_journal
        try:
            fts_rows = conn.execute(
                "SELECT rowid, summary, bm25(episodic_journal_fts) AS score "
                "FROM episodic_journal_fts WHERE episodic_journal_fts MATCH ? ORDER BY score LIMIT ?",
                (query, top_k)
            ).fetchall()
            for r in fts_rows:
                results.append((r["rowid"], "episodic", r["summary"], -r["score"]))
        except sqlite3.OperationalError as e:
            logger.debug(f"episodic_journal_fts search error: {e}")
            need_heal = True

    if need_heal and allow_self_heal and CONFIG.get("vault_fts_self_heal", True):
        logger.info("Attempting self-healing FTS rebuild after search OperationalError...")
        try:
            rebuild_fts_index()
            return fts5_search(query, top_k, allow_self_heal=False)
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            logger.warning(f"Self-healing retry failed: {e}")

    results.sort(key=lambda x: x[3], reverse=True)
    return results[:top_k]


def expand_query(query: str) -> str:
    """Expand a search query with synonyms for better FTS5 recall.

    Appends common synonyms to the query using FTS5's OR syntax.
    Example: "code bug" → "code bug OR defect OR issue OR error OR fix"
    """
    synonym_map = {
        'bug': 'bug OR defect OR issue OR error OR fault',
        'fix': 'fix OR repair OR patch OR resolve',
        'code': 'code OR script OR implementation OR program',
        'doc': 'doc OR documentation OR manual OR guide',
        'test': 'test OR check OR verify OR validate',
        'ai': 'ai OR artificial OR intelligence OR llm OR model',
        'memory': 'memory OR recall OR context OR history',
        'search': 'search OR query OR find OR retrieve OR lookup',
        'slow': 'slow OR latency OR performance OR speed',
        'error': 'error OR exception OR crash OR fail',
        'update': 'update OR change OR modify OR edit OR upgrade',
        'create': 'create OR add OR build OR generate OR make',
        'delete': 'delete OR remove OR erase OR clear',
        'config': 'config OR setting OR option OR parameter',
        'ui': 'ui OR interface OR gui OR display',
    }
    words = query.lower().split()
    expanded = []
    for w in words:
        if w in synonym_map:
            expanded.append(f"({synonym_map[w]})")
        else:
            expanded.append(w)
    return " ".join(expanded)


def fts_snippet(text: str, query: str, max_chars: int = 200) -> str:
    """Extract a highlighted context snippet from text around query match.

    Finds the first occurrence of a query term in text and returns a
    window of up to max_chars around it, with '...' ellipsis on either
    side if truncated.
    """
    if not text or not query:
        return (text or "")[:max_chars]
    words = query.lower().split()
    lower_text = text.lower()
    best_pos = -1
    for w in words:
        pos = lower_text.find(w)
        if pos >= 0 and (best_pos < 0 or pos < best_pos):
            best_pos = pos
    if best_pos < 0:
        return text[:max_chars]
    half = max_chars // 2
    start = max(0, best_pos - half)
    end = min(len(text), best_pos + half)
    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def deduplicate_results(results: list, threshold: float = 0.85) -> list:
    """Deduplicate search results by content hash similarity.

    Uses character bigram overlap to detect near-duplicates.
    Results with similarity >= threshold are collapsed to the
    highest-scoring entry.
    """
    if not results:
        return results
    deduped = []
    seen = []
    for r in results:
        # r is (id, source, content, score) tuple
        content = r[2] if len(r) > 2 else str(r)
        is_dup = False
        for s in seen:
            sim = _bigram_similarity(content, s)
            if sim >= threshold:
                is_dup = True
                break
        if not is_dup:
            seen.append(content)
            deduped.append(r)
    return deduped


def adaptive_alpha(query: str) -> float:
    """Dynamically adjust search blend weight based on query characteristics.

    Returns an alpha value between 0.0 (pure keyword) and 1.0 (pure semantic)
    based on query length, presence of quoted phrases, and stop-word ratio.
    Short queries with specific terms lean keyword; long queries lean semantic.
    """
    if not query or not query.strip():
        return 0.6
    # Short queries (1-2 words) → prefer keyword search
    word_count = len(query.strip().split())
    if word_count <= 1:
        return 0.3
    # Quoted phrases → prefer semantic
    if '"' in query:
        return 0.8
    # Long queries → prefer semantic
    if word_count >= 5:
        return 0.8
    # Medium queries → balanced, check keyword density
    stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
                  'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                  'would', 'could', 'should', 'may', 'might', 'shall', 'can',
                  'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
                  'as', 'into', 'through', 'during', 'before', 'after', 'and',
                  'or', 'but', 'not', 'no', 'if', 'so', 'than', 'that', 'this',
                  'these', 'those', 'it', 'its'}
    words = set(w.lower().strip('.,;:!?"\'') for w in query.split())
    if not words:
        return 0.6
    meaningful = words - stop_words
    keyword_ratio = len(meaningful) / len(words)
    if keyword_ratio > 0.8:
        return 0.4  # High keyword density → lean keyword
    else:
        return 0.7  # More descriptive → lean semantic


def time_decay_rerank(results: list, decay_days: float = 30.0) -> list:
    """Apply exponential time decay to search results.

    Results that carry a timestamp (third element if string, or via fetch)
    get their score multiplied by ``exp(-days_old / decay_days)``.
    Returns a new list sorted by decayed score descending.
    """
    import time as _time
    now = _time.time()
    decayed = []
    for r in results:
        score = r[3] if len(r) > 3 else 1.0
        # Try to extract timestamp from result
        ts = r[2] if len(r) > 2 else None
        days_old = 0.0
        if ts and isinstance(ts, str):
            try:
                dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                days_old = (datetime.now() - dt).total_seconds() / 86400.0
            except (ValueError, IndexError):
                pass
        decay_factor = math.exp(-days_old / max(decay_days, 1.0))
        decayed.append((r[0], r[1], r[2], score * decay_factor))
    decayed.sort(key=lambda x: x[3], reverse=True)
    return decayed


def log_search(query: str, result_count: int, latency_ms: float,
               search_type: str = "hybrid", metadata: dict = None) -> int:
    """Log search query + metrics to search_log table."""
    ensure_tables_exist()
    meta_json = json.dumps(metadata or {})
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(_get_conn()) as conn:
        cursor = conn.execute(
            "INSERT INTO search_log (timestamp, query, result_count, latency_ms, search_type, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, query, result_count, latency_ms, search_type, meta_json)
        )
        conn.commit()
        return cursor.lastrowid


# ═══════════════════════════════════════════════════════════════════════
# 6. HYBRID SEARCH — RRF FUSION OF VECTOR + FTS5
# ═══════════════════════════════════════════════════════════════════════

def hybrid_search(query: str, top_k: int = 5, alpha: float = 0.6, candidate_limit: int = 50) -> list:
    """Hybrid search combining semantic (vector) and FTS5 (keyword) with
    Reciprocal Rank Fusion (RRF).

    Args:
        query: Search text.
        top_k: Number of final results to return.
        alpha: Blend weight for semantic results in the RRF formula.
               alpha=0.6 means 60% semantic, 40% keyword weighting.
        candidate_limit: Max rows for the semantic candidate set.

    Returns a list of tuples: ``(id, source, content, score)`` sorted
    by descending RRF score.

    RRF formula (Cormack et al. 2009):
        rrf_score(d) = sum_{r in ranks} 1 / (k + r(d))
    where k=60 (standard constant) and r(d) is the 1-based rank of
    document d in each ranking list.
    """
    ensure_tables_exist()
    k = 60  # Standard RRF constant
    rrf_scores = defaultdict(float)

    # 1. Semantic (vector) search — Sprint 19.8 two-phase fast path.
    #    Score = pure cosine (no importance boost), exactly as the 2.0.x
    #    per-row loop computed it, so RRF rank ordering is unchanged.
    query_emb = _get_embedding(query)
    semantic_results = []
    if query_emb is not None:
        semantic_results = _search_vectors_by_embedding(query_emb, top_k)

    # 2. Keyword (FTS5) search
    try:
        keyword_results = fts5_search(expand_query(query), top_k)
    except Exception as e:
        logger.debug(f"FTS5 keyword search failed in hybrid_search (graceful fallback): {e}")
        keyword_results = []

    # 3. RRF fusion
    for rank, result in enumerate(semantic_results):
        doc_id = f"core:{result[0]}"
        rrf_scores[doc_id] += alpha * (1.0 / (k + rank + 1))
        rrf_scores[f"{doc_id}_content"] = result[2]
        rrf_scores[f"{doc_id}_type"] = result[1]

    for rank, result in enumerate(keyword_results):
        doc_id = f"{result[1]}:{result[0]}"
        rrf_scores[doc_id] += (1.0 - alpha) * (1.0 / (k + rank + 1))
        if f"{doc_id}_content" not in rrf_scores:
            rrf_scores[f"{doc_id}_content"] = result[2]
        if f"{doc_id}_type" not in rrf_scores:
            rrf_scores[f"{doc_id}_type"] = result[1]

    # 4. Build final sorted list
    final = []
    for doc_id in sorted(rrf_scores.keys()):
        if doc_id.endswith("_content") or doc_id.endswith("_type"):
            continue
        score = rrf_scores[doc_id]
        if score <= 0:
            continue
        content = rrf_scores.get(f"{doc_id}_content", "")
        src = rrf_scores.get(f"{doc_id}_type", "core")
        # Parse id from doc_id string
        try:
            _, row_id = doc_id.split(":", 1)
            row_id = int(row_id)
        except (ValueError, IndexError):
            row_id = 0
        final.append((row_id, src, content, score))

    final.sort(key=lambda x: x[3], reverse=True)
    return final[:top_k]


# ═══════════════════════════════════════════════════════════════════════
# 7. GRAPHRAG — ENTITY & RELATIONSHIP EXTRACTION
# ═══════════════════════════════════════════════════════════════════════

def extract_entities_regex(text: str) -> list:
    """Extract entities from text using regex patterns.

    Returns a list of dicts: ``[{"name": str, "entity_type": str}]``.
    Deduplicates by (name, entity_type). Filters out common false
    positives (single-word names that are likely not entities).
    """
    if not text:
        return []
    found = {}
    for pattern, ent_type in _ENTITY_PATTERNS:
        for m in pattern.finditer(text):
            name = m.group(1).strip()
            # Filter single uppercase words (likely proper nouns, keep them)
            name_lower = name.lower()
            if name_lower in _ENTITY_KEYWORD_TYPES:
                ent_type = _ENTITY_KEYWORD_TYPES[name_lower]
            key = (name, ent_type)
            if key not in found:
                found[key] = {"name": name, "entity_type": ent_type}
    # Detect entity type from context only for 'concept' types
    # (don't override pattern-detected types like 'date', 'url', 'person')
    pattern_types = {pt for _, pt in _ENTITY_PATTERNS}
    for name, ent in list(found.items()):
        if ent["entity_type"] not in pattern_types:
            ent["entity_type"] = detect_entity_type_from_text(ent["name"], text)
    return list(found.values())


def detect_entity_type_from_text(name: str, context: str) -> str:
    """Detect entity type by matching context keywords around the entity name."""
    for ent_type, pattern in _ENTITY_TYPE_PATTERNS.items():
        if pattern.search(context):
            return ent_type
    return "concept"


def extract_entities_llm(text: str, timeout: int = 30) -> list:
    """ANTI-FRAGILITY: Uses ``get_provider()`` (not raw ``requests.post()``).

    Extract entities from text using the configured LLM provider.
    Sends a prompt asking the model to return a JSON array of
    ``{"name": ..., "entity_type": ...}`` objects. Falls back to
    an empty list on any error.

    Returns a list of dicts: ``[{"name": str, "entity_type": str}]``.
    """
    try:
        provider = get_provider()
        prompt = (
            "Extract named entities from the following text. Return a JSON array of objects "
            'with "name" (the entity name) and "entity_type" (person, concept, technology, '
            "project, module, api, config, error, metric, database, service, or location). "
            "Only return the JSON array, no other text.\n\n"
            f"Text: {text[:2000]}"
        )
        messages = [
            {"role": "system", "content": "You are an entity extraction system. Return only valid JSON arrays."},
            {"role": "user", "content": prompt}
        ]
        result = provider.chat_completion(
            messages=messages,
            model=CONFIG.get("model_name", ""),
            temperature=0.1,
            timeout=timeout
        )
        if not result or result.get("error"):
            return []
        raw = result.get("content", "[]").strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
            raw = raw.strip()
        entities = json.loads(raw)
        if not isinstance(entities, list):
            return []
        # Validate shape
        return [{"name": e["name"], "entity_type": e.get("entity_type", "concept")}
                for e in entities if isinstance(e, dict) and "name" in e]
    except Exception as e:
        logger.warning(f"LLM entity extraction failed: {e}")
        return []


def extract_entities(text: str, use_llm: bool = False) -> list:
    """Extract entities from text using regex + optional LLM fallback.

    Combines regex-based extraction (fast, deterministic) with optional
    LLM-based extraction (slower, higher recall). Deduplicates across
    both sources.

    Returns a list of dicts: ``[{"name": str, "entity_type": str}]``.
    """
    entities = extract_entities_regex(text)
    if use_llm and CONFIG.get("graphrag_enabled", True):
        llm_entities = extract_entities_llm(text)
        existing_names = {(e["name"], e["entity_type"]) for e in entities}
        for e in llm_entities:
            key = (e["name"], e["entity_type"])
            if key not in existing_names:
                entities.append(e)
                existing_names.add(key)
    return entities


def extract_relationships_regex(text: str, entities: list = None) -> list:
    """Extract relationships from text using verb-pattern matching.

    If ``entities`` is None or empty, entities are auto-extracted from
    the text via ``extract_entities_regex()`` so callers can pass just
    the text (backward-compatible convenience).

    Returns a list of dicts:
    ``[{"subject": str, "predicate": str, "object": str, "rel_type": str}]``.
    """
    if not text:
        return []
    if entities is None:
        entities = extract_entities_regex(text)
    results = []
    entity_names = {e["name"].lower() for e in entities}
    for pattern, rel_type in _RELATIONSHIP_PATTERNS:
        for m in pattern.finditer(text):
            subj = m.group(1).strip()
            obj = m.group(2).strip()
            if subj.lower() in entity_names or obj.lower() in entity_names:
                results.append({
                    "subject": subj,
                    "predicate": m.group(0),
                    "object": obj,
                    "rel_type": rel_type,
                })
    # If entities list was empty (no entities found), still try pattern matching
    # against known uppercase words that aren't in any entity set.
    if not entity_names:
        for pattern, rel_type in _RELATIONSHIP_PATTERNS:
            for m in pattern.finditer(text):
                subj = m.group(1).strip()
                obj = m.group(2).strip()
                results.append({
                    "subject": subj,
                    "predicate": m.group(0),
                    "object": obj,
                    "rel_type": rel_type,
                })
    return results


def extract_relationships(text: str, entities: list, use_llm: bool = False) -> list:
    """Extract relationships from text using regex + optional LLM.

    Returns a list of dicts:
    ``[{"subject": str, "predicate": str, "object": str, "rel_type": str}]``.
    """
    relationships = extract_relationships_regex(text, entities)
    if use_llm and CONFIG.get("graphrag_enabled", True):
        try:
            provider = get_provider()
            entity_names = [e["name"] for e in entities[:10]]
            prompt = (
                "Extract relationships between the following entities from the text. "
                'Return a JSON array of objects with "source", "target", "relationship". '
                f"Entities: {json.dumps(entity_names)}\n\n"
                f"Text: {text[:2000]}\n\n"
                "Only return the JSON array, no other text."
            )
            messages = [
                {"role": "system", "content": "You are a relationship extraction system."},
                {"role": "user", "content": prompt}
            ]
            result = provider.chat_completion(
                messages=messages,
                model=CONFIG.get("model_name", ""),
                temperature=0.1,
                timeout=30
            )
            if result and not result.get("error"):
                raw = result.get("content", "[]").strip()
                if raw.startswith("```"):
                    lines = raw.split("\n")
                    raw = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
                    raw = raw.strip()
                llm_rels = json.loads(raw)
                if isinstance(llm_rels, list):
                    existing = {(r["subject"], r["predicate"], r["object"]) for r in relationships}
                    for r in llm_rels:
                        if isinstance(r, dict) and "source" in r and "target" in r:
                            key = (r["source"], r.get("relationship", ""), r["target"])
                            if key not in existing:
                                relationships.append({
                                    "subject": r["source"],
                                    "predicate": r.get("relationship", "related_to"),
                                    "object": r["target"],
                                    "rel_type": "llm_extracted",
                                })
                                existing.add(key)
        except Exception as e:
            logger.warning(f"LLM relationship extraction failed: {e}")
    return relationships


def store_entity(name: str, entity_type: str = "concept",
                 description: str = "", metadata: dict = None) -> int:
    """Store or update a knowledge graph entity.

    Uses UPSERT semantics: if an entity with the same (name, entity_type)
    already exists, increment mention_count and update last_seen.
    Otherwise insert a new row.

    Returns the entity ID, or -1 when name is empty/None.
    """
    if not name or not name.strip():
        return -1
    name = name.strip()
    ensure_tables_exist()
    meta_json = json.dumps(metadata or {})
    # Sprint 13 #11: entity embeddings are OPT-IN via graphrag_entity_embeddings
    # (default False). This guard existed in patches/sprint13_improvements.py and
    # was lost when Sprint 13 was merged into memory_vault — without it every
    # store_entity() call paid a model-embedding pass regardless of config.
    # Restored 2026-09-22 (order-dependent chunk-07 failure, Group 5 follow-up).
    embedding = None
    if CONFIG.get("graphrag_entity_embeddings", False):
        try:
            embedding = _get_embedding(name)
        except Exception as e:
            logger.debug(f"Entity embedding failed for '{name}' (graceful fallback): {e}")
            embedding = None
    embedding_blob = _encode_embedding(embedding) if embedding is not None else None
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(_get_conn()) as conn:
        # Check if entity exists
        existing = conn.execute(
            "SELECT id, mention_count FROM kg_entities WHERE name = ? AND entity_type = ?",
            (name, entity_type)
        ).fetchone()
        if existing:
            if embedding_blob is not None:
                conn.execute(
                    "UPDATE kg_entities SET mention_count = ?, last_seen = ?, embedding = ? WHERE id = ?",
                    (existing["mention_count"] + 1, ts, embedding_blob, existing["id"])
                )
            else:
                # Flag off (or embedding unavailable): preserve any prior
                # embedding instead of overwriting it with NULL.
                conn.execute(
                    "UPDATE kg_entities SET mention_count = ?, last_seen = ? WHERE id = ?",
                    (existing["mention_count"] + 1, ts, existing["id"])
                )
            conn.commit()
            return existing["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO kg_entities (name, entity_type, description, metadata, embedding, mention_count, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                (name, entity_type, description, meta_json, embedding_blob, ts, ts)
            )
            conn.commit()
            return cursor.lastrowid


def store_relationship(source_entity_id: int, target_entity_id: int,
                       relationship_type: str, confidence: float = 1.0,
                       evidence: str = "", source_memory_id: int = None,
                       source_type: str = "auto", metadata: dict = None) -> int:
    """Store a relationship between two entities.

    Avoids exact duplicates: if a relationship of the same type between
    the same entities already exists, the confidence is strengthened
    (averaged and capped at 1.0).

    Returns the relationship ID.
    """
    if relationship_type is None:
        relationship_type = "RELATES_TO"
    ensure_tables_exist()
    meta_json = json.dumps(metadata or {})
    with closing(_get_conn()) as conn:
        # Check for existing duplicate
        existing = conn.execute(
            "SELECT id, confidence FROM kg_relationships WHERE source_entity_id = ? AND target_entity_id = ? AND relationship_type = ?",
            (source_entity_id, target_entity_id, relationship_type)
        ).fetchone()
        if existing:
            # Strengthen confidence: probabilistic OR (1 - (1-a)*(1-b)), capped at 1.0
            new_conf = min(1.0, existing["confidence"] + confidence * (1.0 - existing["confidence"]))
            conn.execute(
                "UPDATE kg_relationships SET confidence = ? WHERE id = ?",
                (new_conf, existing["id"])
            )
            conn.commit()
            return existing["id"]
        cursor = conn.execute(
            "INSERT INTO kg_relationships (source_entity_id, target_entity_id, relationship_type, confidence, evidence, source_memory_id, source_type, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (source_entity_id, target_entity_id, relationship_type, confidence, evidence,
             source_memory_id, source_type, meta_json)
        )
        conn.commit()
        return cursor.lastrowid


def get_entity(entity_id: int = None, name: str = None) -> dict:
    """Fetch a single entity by ID or name.

    Name lookup is case-insensitive. Returns a dict with entity fields or None.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        if entity_id is not None:
            row = conn.execute(
                "SELECT * FROM kg_entities WHERE id = ?", (entity_id,)
            ).fetchone()
        elif name is not None:
            row = conn.execute(
                "SELECT * FROM kg_entities WHERE LOWER(name) = LOWER(?) ORDER BY mention_count DESC LIMIT 1",
                (name,)
            ).fetchone()
        else:
            return None
    return dict(row) if row else None


def search_entities(query: str, entity_type: str = None, top_k: int = 10) -> list:
    """Search entities by name substring match or type filter.

    Uses ``INSTR()`` with ``LOWER()`` for case-insensitive substring
    matching — safe against the ``LIKE or GLOB pattern too complex``
    SQLite error.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        if entity_type:
            rows = conn.execute(
                "SELECT * FROM kg_entities WHERE entity_type = ? AND INSTR(LOWER(name), LOWER(?)) > 0 "
                "ORDER BY mention_count DESC LIMIT ?",
                (entity_type, query, top_k)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kg_entities WHERE INSTR(LOWER(name), LOWER(?)) > 0 "
                "ORDER BY mention_count DESC LIMIT ?",
                (query, top_k)
            ).fetchall()
    return [dict(r) for r in rows]


def get_entity_relationships(entity_id: int, direction: str = "both",
                              rel_type: str = None, top_k: int = 50) -> list:
    """Get relationships involving an entity.

    direction: 'outgoing', 'incoming', or 'both' (default).
    rel_type: optional filter by relationship type.

    Returns a list of dicts with relationship details plus
    source/target entity names.
    """
    ensure_tables_exist()
    results = []
    with closing(_get_conn()) as conn:
        if direction in ("outgoing", "both"):
            if rel_type:
                rows = conn.execute(
                    "SELECT r.*, e.name AS target_name, e.entity_type AS target_type "
                    "FROM kg_relationships r JOIN kg_entities e ON r.target_entity_id = e.id "
                    "WHERE r.source_entity_id = ? AND r.relationship_type = ? "
                    "ORDER BY r.confidence DESC LIMIT ?",
                    (entity_id, rel_type, top_k)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT r.*, e.name AS target_name, e.entity_type AS target_type "
                    "FROM kg_relationships r JOIN kg_entities e ON r.target_entity_id = e.id "
                    "WHERE r.source_entity_id = ? ORDER BY r.confidence DESC LIMIT ?",
                    (entity_id, top_k)
                ).fetchall()
            for r in rows:
                d = dict(r)
                d["direction"] = "outgoing"
                results.append(d)
        if direction in ("incoming", "both"):
            if rel_type:
                rows = conn.execute(
                    "SELECT r.*, e.name AS source_name, e.entity_type AS source_type "
                    "FROM kg_relationships r JOIN kg_entities e ON r.source_entity_id = e.id "
                    "WHERE r.target_entity_id = ? AND r.relationship_type = ? "
                    "ORDER BY r.confidence DESC LIMIT ?",
                    (entity_id, rel_type, top_k)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT r.*, e.name AS source_name, e.entity_type AS source_type "
                    "FROM kg_relationships r JOIN kg_entities e ON r.source_entity_id = e.id "
                    "WHERE r.target_entity_id = ? ORDER BY r.confidence DESC LIMIT ?",
                    (entity_id, top_k)
                ).fetchall()
            for r in rows:
                d = dict(r)
                d["direction"] = "incoming"
                results.append(d)
    return results


def graph_traverse(start_entity_id: int, depth: int = 2,
                   rel_type: str = None) -> dict:
    """BFS traversal from a starting entity.

    Returns a dict with 'entities' (list of entity dicts) and
    'relationships' (list of relationship dicts).
    """
    ensure_tables_exist()
    seen_entities = set()
    seen_rels = set()
    entities = []
    relationships = []
    queue = deque([(start_entity_id, 0)])
    while queue:
        eid, d = queue.popleft()
        if eid in seen_entities or d > depth:
            continue
        seen_entities.add(eid)
        ent = get_entity(entity_id=eid)
        if ent:
            entities.append(ent)
        rels = get_entity_relationships(eid, rel_type=rel_type)
        for r in rels:
            rid = r["id"]
            if rid not in seen_rels:
                seen_rels.add(rid)
                relationships.append(r)
            if d < depth:
                if r.get("direction") == "outgoing" and r["target_entity_id"] not in seen_entities:
                    queue.append((r["target_entity_id"], d + 1))
                elif r.get("direction") == "incoming" and r["source_entity_id"] not in seen_entities:
                    queue.append((r["source_entity_id"], d + 1))
    return {"entities": entities, "relationships": relationships}


def get_graph_stats() -> dict:
    """Return summary statistics of the knowledge graph."""
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        entity_count = conn.execute("SELECT COUNT(*) AS c FROM kg_entities").fetchone()["c"]
        rel_count = conn.execute("SELECT COUNT(*) AS c FROM kg_relationships").fetchone()["c"]
        type_dist = conn.execute(
            "SELECT entity_type, COUNT(*) AS c FROM kg_entities GROUP BY entity_type ORDER BY c DESC"
        ).fetchall()
        rel_type_dist = conn.execute(
            "SELECT relationship_type, COUNT(*) AS c FROM kg_relationships GROUP BY relationship_type ORDER BY c DESC"
        ).fetchall()
    entity_types = {r["entity_type"]: r["c"] for r in type_dist}
    rel_types = {r["relationship_type"]: r["c"] for r in rel_type_dist}
    return {
        "entity_count": entity_count,
        "relationship_count": rel_count,
        "entity_type_distribution": entity_types,
        "relationship_type_distribution": rel_types,
        "entity_types": entity_types,
        "relationship_types": rel_types,
    }


def extract_and_store_graph(text: str, source_memory_id: int = None,
                            source_type: str = "auto", use_llm: bool = False) -> dict:
    """Extract entities and relationships from text and store in the knowledge graph.

    This is the main entry point for populating the knowledge graph.
    It extracts entities, stores them, extracts relationships between
    them, and stores the relationships.

    Returns a dict with 'entities_found', 'entities_stored',
    'relationships_found', 'relationships_stored'.
    """
    global _graph_extraction_count
    if not _GRAPHRAG_ENABLED or not CONFIG.get("graphrag_enabled", True):
        return {"entities_found": 0, "entities_stored": 0,
                "relationships_found": 0, "relationships_stored": 0}
    entities = extract_entities(text, use_llm=use_llm)
    relationships = extract_relationships(text, entities, use_llm=use_llm)
    entity_id_map = {}
    entity_count = 0
    for e in entities:
        if _graph_extraction_count >= CONFIG.get("graphrag_max_extractions_per_session", 100):
            break
        eid = store_entity(e["name"], e["entity_type"])
        entity_id_map[e["name"]] = eid
        entity_count += 1
        _graph_extraction_count += 1
    rel_count = 0
    for r in relationships:
        subj_id = entity_id_map.get(r["subject"])
        obj_id = entity_id_map.get(r["object"])
        if subj_id and obj_id:
            store_relationship(subj_id, obj_id, r["rel_type"],
                               evidence=r.get("predicate", ""),
                               source_memory_id=source_memory_id,
                               source_type=source_type)
            rel_count += 1
    return {
        "entities_found": len(entities),
        "entities_stored": entity_count,
        "relationships_found": len(relationships),
        "relationships_stored": rel_count,
    }


def compute_entity_embedding(name: str, description: str = "") -> list:
    """Compute and return an embedding vector for a KG entity.

    Returns the embedding as a list of floats, or None if unavailable.
    """
    text = f"{name}: {description}" if description else name
    return _get_embedding(text)


def _bigram_similarity(s1: str, s2: str) -> float:
    """Compute character bigram overlap similarity between two strings."""
    if not s1 or not s2:
        return 0.0
    s1 = s1.lower()
    s2 = s2.lower()
    bigrams1 = set(s1[i:i+2] for i in range(len(s1) - 1))
    bigrams2 = set(s2[i:i+2] for i in range(len(s2) - 1))
    if not bigrams1 or not bigrams2:
        return 0.0
    intersection = bigrams1 & bigrams2
    union = bigrams1 | bigrams2
    return len(intersection) / len(union)


def fuzzy_merge_entities(threshold: float = 0.85) -> int:
    """Merge entities with similar names using fuzzy string matching.

    Finds pairs of entities with name similarity >= threshold, merges
    the lower-mentioned into the higher-mentioned. Returns number of
    merges performed.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        entities = conn.execute(
            "SELECT id, name, mention_count FROM kg_entities ORDER BY mention_count DESC"
        ).fetchall()
    merges = 0
    for i, e1 in enumerate(entities):
        for e2 in entities[i + 1:]:
            sim = _bigram_similarity(e1["name"], e2["name"])
            if sim >= threshold:
                # Merge e2 into e1 (higher mention count wins)
                if e2["mention_count"] > e1["mention_count"]:
                    primary, secondary = e2, e1
                else:
                    primary, secondary = e1, e2
                with closing(_get_conn()) as conn:
                    # Update relationships
                    conn.execute(
                        "UPDATE kg_relationships SET source_entity_id = ? WHERE source_entity_id = ?",
                        (primary["id"], secondary["id"])
                    )
                    conn.execute(
                        "UPDATE kg_relationships SET target_entity_id = ? WHERE target_entity_id = ?",
                        (primary["id"], secondary["id"])
                    )
                    conn.execute("DELETE FROM kg_entities WHERE id = ?", (secondary["id"],))
                    conn.commit()
                merges += 1
    return merges


def path_confidence(path: list) -> float:
    """Compute compound confidence along a traversal path.

    path is a list of dicts with 'confidence' keys (from relationships).
    Returns the product of all confidences.
    """
    if not path:
        return 0.0
    conf = 1.0
    for node in path:
        conf *= node.get("confidence", 0.5)
    return conf


def detect_communities(min_cluster_size: int = 3) -> list:
    """Detect entity communities/clusters in the knowledge graph.

    Uses simple connected-component analysis on entity relationships.
    Returns a list of community dicts, each with 'id', 'members',
    'size', and 'entities' keys.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        rels = conn.execute(
            "SELECT source_entity_id, target_entity_id FROM kg_relationships"
        ).fetchall()
        entities = conn.execute("SELECT id, name, entity_type FROM kg_entities").fetchall()
    # Build adjacency list
    adj = defaultdict(set)
    for r in rels:
        adj[r["source_entity_id"]].add(r["target_entity_id"])
        adj[r["target_entity_id"]].add(r["source_entity_id"])
    # BFS connected components
    visited = set()
    communities = []
    entity_map = {e["id"]: dict(e) for e in entities}
    community_id = 0
    for eid in entity_map:
        if eid in visited:
            continue
        component = set()
        queue = deque([eid])
        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)
            component.add(nid)
            for neighbor in adj.get(nid, set()):
                if neighbor not in visited:
                    queue.append(neighbor)
        if len(component) >= min_cluster_size:
            community_id += 1
            member_entities = [entity_map[cid] for cid in component if cid in entity_map]
            communities.append({
                "id": community_id,
                "members": [e["id"] for e in member_entities],
                "size": len(member_entities),
                "entities": member_entities,
            })
    return communities


def export_graph(fmt: str = "json") -> dict:
    """Export the knowledge graph as a dict.

    Returns a dict with 'entities', 'relationships', 'entity_count',
    'relationship_count', 'format', and 'exported_at' keys.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        entities = [dict(r) for r in conn.execute("SELECT * FROM kg_entities").fetchall()]
        relationships = [dict(r) for r in conn.execute("SELECT * FROM kg_relationships").fetchall()]
    for e in entities:
        if "embedding" in e:
            del e["embedding"]
    return {
        "format": "kokertech-kg-v1",
        "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entity_count": len(entities),
        "relationship_count": len(relationships),
        "entities": entities,
        "relationships": relationships,
    }


def import_graph(data: str) -> dict:
    """Import a knowledge graph from exported JSON data.

    data is a JSON string with 'entities' and 'relationships' arrays.
    Returns a dict with counts of imported entities and relationships.
    """
    ensure_tables_exist()
    try:
        graph = json.loads(data) if isinstance(data, str) else data
        entity_count = 0
        rel_count = 0
        entity_id_map = {}
        for e in graph.get("entities", []):
            eid = store_entity(
                e["name"], e.get("entity_type", "concept"),
                e.get("description", ""), e.get("metadata")
            )
            entity_id_map[e.get("id")] = eid
            entity_count += 1
        for r in graph.get("relationships", []):
            src = entity_id_map.get(r.get("source_entity_id"))
            tgt = entity_id_map.get(r.get("target_entity_id"))
            if src and tgt:
                store_relationship(src, tgt, r.get("relationship_type", "related_to"),
                                   r.get("confidence", 1.0), r.get("evidence", ""),
                                   r.get("source_memory_id"), r.get("source_type", "imported"),
                                   r.get("metadata"))
                rel_count += 1
        return {"entities_imported": entity_count, "relationships_imported": rel_count}
    except Exception as e:
        logger.error(f"Graph import failed: {e}")
        return {"entities_imported": 0, "relationships_imported": 0, "error": str(e)}


def search_explanation(query: str, result: tuple) -> str:
    """Generate an explanation for why a search result matched.

    result is a tuple ``(id, source, content, score)``.
    Returns a human-readable explanation string.
    """
    if not result or len(result) < 3:
        return "No match information available."
    row_id, source, content, score = result[0], result[1], result[2], result[3] if len(result) > 3 else 0
    query_terms = query.lower().split()
    content_lower = content.lower()
    matched_terms = [t for t in query_terms if t in content_lower]
    if matched_terms:
        return (f"Matched {len(matched_terms)} of {len(query_terms)} terms "
                f"({', '.join(matched_terms)}) in {source} memory #{row_id} "
                f"(score: {score:.3f})")
    else:
        return (f"Semantic match from {source} memory #{row_id} "
                f"(score: {score:.3f}, no exact term overlap)")


def graph_rag_search(query: str, top_k: int = 5) -> list:
    """GraphRAG-aware search: expand query to include entity neighbors.

    Searches entities matching the query, then expands with their
    relationships and returns the combined results.
    """
    ensure_tables_exist()
    # Find entities matching the query
    entities = search_entities(query, top_k=top_k)
    results = []
    seen_entities = set()
    for e in entities:
        if e["id"] in seen_entities:
            continue
        seen_entities.add(e["id"])
        results.append({"source": "entity", "entity": e["name"],
                        "type": e["entity_type"], "relevance": 1.0})
        # Get relationships
        rels = get_entity_relationships(e["id"], top_k=3)
        for r in rels:
            neighbor_id = r.get("target_entity_id") or r.get("source_entity_id")
            if neighbor_id and neighbor_id not in seen_entities:
                neighbor = get_entity(entity_id=neighbor_id)
                if neighbor:
                    results.append({
                        "source": "relationship",
                        "entity": neighbor["name"],
                        "type": neighbor["entity_type"],
                        "relation": r.get("relationship_type"),
                        "relevance": r.get("confidence", 0.5),
                    })
    return results[:top_k]


def graph_aware_summary(text: str) -> str:
    """Generate a graph-aware summary that includes entity context.

    Extracts entities from text, fetches KG context, and enriches
    the output with entity descriptions.
    """
    entities = extract_entities_regex(text)
    context_parts = []
    for e in entities[:5]:
        entity = get_entity(name=e["name"])
        if entity:
            rels = get_entity_relationships(entity["id"], top_k=3)
            if rels:
                rel_strs = []
                for r in rels:
                    if r.get("direction") == "outgoing":
                        rel_strs.append(f"→ {r.get('target_name', '?')} ({r.get('relationship_type', 'related')})")
                    else:
                        rel_strs.append(f"← {r.get('source_name', '?')} ({r.get('relationship_type', 'related')})")
                context_parts.append(f"  {e['name']}: {'; '.join(rel_strs)}")
    if context_parts:
        return "Graph context:\n" + "\n".join(context_parts)
    return "No graph context found."


def cascade_cleanup_orphaned_entities() -> int:
    """Remove orphaned entities from the KG (entities with no relationships).

    Returns the number of entities removed.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT id FROM kg_entities e WHERE NOT EXISTS ("
            "SELECT 1 FROM kg_relationships WHERE source_entity_id = e.id OR target_entity_id = e.id"
            ")"
        ).fetchall()
        count = len(rows)
        for r in rows:
            conn.execute("DELETE FROM kg_entities WHERE id = ?", (r["id"],))
        conn.commit()
    return count


def llm_extract_with_fallback(text: str, timeout: int = 30) -> list:
    """ANTI-FRAGILITY: Uses ``get_provider()`` for fallback model call.

    Entity extraction with LLM fallback chain. Tries the primary model
    first via ``extract_entities_llm``, and if that returns empty, tries
    the fallback model from ``CONFIG['graphrag_llm_fallback_model']``.

    Returns a list of dicts: ``[{"name": str, "entity_type": str}]``.
    """
    entities = extract_entities_llm(text, timeout=timeout)
    if entities:
        return entities
    fallback_model = CONFIG.get("graphrag_llm_fallback_model", "")
    if not fallback_model:
        return []
    try:
        provider = get_provider()
        prompt = (
            "Extract named entities from the following text. Return a JSON array of objects "
            'with "name" and "entity_type". Only return the JSON array.\n\n'
            f"Text: {text[:2000]}"
        )
        messages = [
            {"role": "system", "content": "You are an entity extraction system."},
            {"role": "user", "content": prompt}
        ]
        result = provider.chat_completion(
            messages=messages,
            model=fallback_model,
            temperature=0.1,
            timeout=timeout
        )
        if not result or result.get("error"):
            return []
        raw = result.get("content", "[]").strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else lines[-1]
            raw = raw.strip()
        entities = json.loads(raw)
        if isinstance(entities, list):
            return [{"name": e["name"], "entity_type": e.get("entity_type", "concept")}
                    for e in entities if isinstance(e, dict) and "name" in e]
    except Exception as e:
        logger.warning(f"Fallback LLM extraction failed: {e}")
    return []


# ═══════════════════════════════════════════════════════════════════════
# 8. CROSS-ENCODER RERANKER
# ═══════════════════════════════════════════════════════════════════════

def _check_cross_encoder_config():
    """Startup check: warn once if cross-encoder model is configured but
    sentence-transformers is missing.

    Emits a single WARNING per process when ``CONFIG['cross_encoder_model']``
    is set and the ``sentence_transformers`` package cannot be found via
    ``importlib.util.find_spec``. Suppressed entirely when
    ``KOKERTECH_SKIP_EMBEDDINGS`` is set (operator's intentional opt-out).
    """
    global _CROSS_ENCODER_WARNED
    if _CROSS_ENCODER_WARNED:
        return
    if os.environ.get("KOKERTECH_SKIP_EMBEDDINGS"):
        return
    model = CONFIG.get("cross_encoder_model", "")
    if not model:
        return
    if importlib.util.find_spec("sentence_transformers") is None:
        logger.warning(
            f"cross_encoder_model '{model}' is configured but sentence-transformers is missing. "
            "Reranking will be disabled. Run 'pip install sentence-transformers' to enable it."
        )
        _CROSS_ENCODER_WARNED = True


def _cross_encoder_rerank(query: str, results: list, max_candidates: int = 20) -> list:
    """Rerank results using a cross-encoder model.

    Uses sentence-transformers CrossEncoder for pairwise relevance scoring.
    Falls back gracefully if the model is unavailable.

    Returns the reranked results list.
    """
    global _CROSS_ENCODER_WARNED
    if not results:
        return results
    if os.environ.get("KOKERTECH_SKIP_EMBEDDINGS"):
        return results
    model_name = CONFIG.get("cross_encoder_model", "")
    if not model_name:
        return results
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        if not _CROSS_ENCODER_WARNED:
            logger.info("CrossEncoder unavailable; skipping rerank.")
            _CROSS_ENCODER_WARNED = True
        logger.debug("CrossEncoder import failed (non-fatal)")
        return results
    try:
        model = CrossEncoder(model_name)
        candidates = results[:max_candidates]
        pairs = [(query, r[2] if len(r) > 2 else str(r)) for r in candidates]
        scores = model.predict(pairs)
        for i, score in enumerate(scores):
            score_val = float(score)
            if len(candidates[i]) > 3:
                candidates[i] = (candidates[i][0], candidates[i][1], candidates[i][2], score_val)
            else:
                candidates[i] = candidates[i] + (score_val,)
        candidates.sort(key=lambda x: x[3] if len(x) > 3 else 0.0, reverse=True)
        return candidates + results[max_candidates:]
    except Exception as e:
        logger.warning(f"Cross-encoder rerank failed: {e}")
        return results


# ═══════════════════════════════════════════════════════════════════════
# 9. SESSION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

def _generate_session_id() -> str:
    """Generate a unique session ID string."""
    now = datetime.now()
    return f"session_{now.strftime('%Y%m%d_%H%M%S')}_{now.microsecond:06d}"


def get_current_session_id() -> str:
    """Return the current session ID, creating one if needed.

    State is held in ``_session_state`` (a mutable dict) so that the
    ServiceRegistry can reset it between tests.

    P7 fix: call ``ensure_tables_exist()`` **before** acquiring the session
    lock so a table-creation failure does not leave ``_session_state``
    populated with a session ID whose ``session_metadata`` row was never
    inserted.
    """
    ensure_tables_exist()
    with _session_lock:
        if _current_session_id is not None and _current_session_id not in _session_state.get("invalidated", set()):
            return _current_session_id
        sid = _generate_session_id()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(_get_conn()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO session_metadata (session_id, start_time, entry_count) VALUES (?, ?, 0)",
                (sid, ts)
            )
            conn.commit()
        globals()["_current_session_id"] = sid
        _session_state["session_id"] = sid
        return sid


def start_new_session(summary: str = "", metadata: dict = None) -> str:
    """End the current session and start a new one.

    Stores the summary for the previous session and creates a new
    session ID. Returns the new session ID.
    """
    with _session_lock:
        old_sid = _current_session_id
        # Update old session summary
        if old_sid and summary:
            with closing(_get_conn()) as conn:
                conn.execute(
                    "UPDATE session_metadata SET summary = ?, end_time = datetime('now','localtime') WHERE session_id = ?",
                    (summary, old_sid)
                )
                conn.commit()
        # Create new session
        sid = _generate_session_id()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with closing(_get_conn()) as conn:
            conn.execute(
                "INSERT INTO session_metadata (session_id, start_time, entry_count) VALUES (?, ?, 0)",
                (sid, ts)
            )
            conn.commit()
        globals()["_current_session_id"] = sid
        _session_state["session_id"] = sid
        return sid


def list_recent_sessions(limit: int = 10) -> list:
    """Return a list of recent session metadata dicts."""
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM session_metadata ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_session_context(limit: int = 10) -> str:
    """Get a formatted context string that groups episodic entries by session."""
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        try:
            sessions = conn.execute(
                "SELECT * FROM session_metadata ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        except sqlite3.OperationalError:
            return "No episodic journal entries yet."
        if not sessions:
            return "No episodic journal entries yet."
    parts = []
    for s in sessions:
        sid = s["session_id"]
        parts.append(f"--- Session: {sid} ---")
        if s["summary"]:
            parts.append(f"Summary: {s['summary']}")
    with closing(_get_conn()) as conn:
        entries = conn.execute(
                "SELECT summary, importance_score FROM episodic_journal "
                "WHERE session_id = ? ORDER BY id DESC LIMIT 5",
                (sid,)
            ).fetchall()
        for e in entries:
            parts.append(f"  [{e['summary'][:100]}] (importance={e['importance_score']})")
    return "\n".join(parts)


def delete_session(session_id: str) -> bool:
    """Delete a session and all its episodic entries.

    Returns True on success.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        conn.execute("DELETE FROM episodic_journal WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM session_metadata WHERE session_id = ?", (session_id,))
        conn.commit()
    return True


# ═══════════════════════════════════════════════════════════════════════
# 10. EPISODIC JOURNAL
# ═══════════════════════════════════════════════════════════════════════

def store_episodic(summary: str, session_id: str = None,
                   tags: list = None, metadata: dict = None,
                   importance: int = 5) -> int:
    """Store an episodic journal entry.

    Returns the new entry ID.
    """
    ensure_tables_exist()
    if session_id is None:
        session_id = get_current_session_id()
    tags_json = json.dumps(tags or [])
    meta_json = json.dumps(metadata or {})
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(_get_conn()) as conn:
        cursor = conn.execute(
            "INSERT INTO episodic_journal (timestamp, session_id, summary, tags, importance_score, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts, session_id, summary, tags_json, importance, meta_json)
        )
        eid = cursor.lastrowid
        conn.commit()
        # Update session entry count
        conn.execute(
            "UPDATE session_metadata SET entry_count = entry_count + 1 WHERE session_id = ?",
            (session_id,)
        )
        # FTS sync (inside with block while conn is open)
        _fts_sync_episodic_insert(conn, eid, summary)
    return eid


def search_episodic(query: str, top_k: int = 5) -> list:
    """Search episodic journal entries.

    Uses FTS5 when available, falls back to simple substring matching.

    Returns a list of tuples compatible with callers in agentic_rag:
    ``(id, 'episodic', summary, score)``.
    """
    ensure_tables_exist()
    try:
        fts_results = fts5_search(query, top_k)
        episodic_results = [(r[0], "episodic", r[2], r[3]) for r in fts_results if r[1] == "episodic"]
        if episodic_results:
            return episodic_results
    except Exception as e:
        logger.debug(f"FTS5 episodic search failed, falling back to substring matching: {e}")
    # Fallback: substring matching
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, summary, importance_score FROM episodic_journal "
            "WHERE INSTR(LOWER(summary), LOWER(?)) > 0 "
            "ORDER BY importance_score DESC, id DESC LIMIT ?",
            (query, top_k)
        ).fetchall()
    return [(r["id"], "episodic", r["summary"], float(r["importance_score"]) / 10.0) for r in rows]


def get_recent_episodic(limit: int = 10, session_id: str = None) -> list:
    """Return the most recent episodic journal entries as dicts.

    Each dict has keys: id, timestamp, session_id, summary, tags,
    importance_score, metadata.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM episodic_journal WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM episodic_journal ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # Parse tags back to list for convenience
        try:
            d["tags"] = json.loads(d.get("tags", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
        result.append(d)
    return result


def consolidate_episodic(importance_threshold: int = 6, max_age_days: int = 7) -> int:
    """Promote high-importance or old episodic entries to core_memories.

    Entries with importance_score >= importance_threshold or timestamp
    older than max_age_days are copied to core_memories with a
    '[EPISODIC:]' prefix. Returns the count of entries promoted.

    REGRESSION GUARD (2026-09-22 re-pollution): idempotent via the
    episodic_journal.promoted marker. Historical context: every call
    re-promoted ALL qualifying rows — the app calls this at lifecycle
    points (app_lifecycle), so two morning app runs re-polluted
    core_memories with ~9,580 duplicate rows from a test-era journal.
    Contract: a journal row is stored to core_memories AT MOST ONCE,
    regardless of how many times consolidation runs. The promoted flag
    is set in the SAME transaction as the core_memories insert so a
    crash cannot double-promote.
    """
    ensure_tables_exist()
    _ensure_promoted_column()
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, summary, importance_score FROM episodic_journal "
            "WHERE promoted = 0 "
            "AND (importance_score >= ? OR timestamp < ?)",
            (importance_threshold, cutoff)
        ).fetchall()
        count = 0
        for r in rows:
            content = f"[EPISODIC: {r['summary']}]"
            try:
                store_memory(content, node_type="episodic",
                             importance=r["importance_score"])
            except Exception as e:
                logger.warning(f"Consolidation failed for episodic #{r['id']}: {e}")
                continue
            # Mark promoted in the same transaction scope as the store
            # above (store_memory commits its own insert; this commit
            # closes the journal-side half of the promotion).
            conn.execute("UPDATE episodic_journal SET promoted = 1 WHERE id = ?",
                         (r['id'],))
            conn.commit()
            count += 1
    if count > 0:
        auto_checkpoint_wal()
    return count


def get_episodic_context(limit: int = 10, session_id: str = None,
                         query: str = "") -> str:
    """Get a formatted context string with importance-weighted and
    optionally query-relevant episodic entries.

    Composite score formula:
        score = 0.5 * (importance / 10) + 0.3 * exp(-hours_ago / 24) + 0.2 * query_relevance

    Returns a multi-line string or "No episodic journal entries yet." if empty.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        if session_id:
            rows = conn.execute(
                "SELECT * FROM episodic_journal WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit * 2)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM episodic_journal ORDER BY id DESC LIMIT ?",
                (limit * 2,)
            ).fetchall()
    if not rows:
        return "No episodic journal entries yet."

    now = datetime.now()
    scored = []
    for r in rows:
        d = dict(r)
        timestamp_str = d.get("timestamp", "")
        try:
            ts = datetime.strptime(timestamp_str[:19], "%Y-%m-%d %H:%M:%S")
            hours_ago = (now - ts).total_seconds() / 3600.0
        except (ValueError, IndexError):
            hours_ago = 0
        importance = d.get("importance_score", 5)
        summary = d.get("summary", "")

        # Composite score
        importance_score = 0.5 * (importance / 10.0)
        recency_score = 0.3 * math.exp(-hours_ago / 24.0)
        query_score = 0.0
        if query:
            # Simple keyword overlap
            query_terms = query.lower().split()
            summary_lower = summary.lower()
            matches = sum(1 for t in query_terms if t in summary_lower)
            query_score = 0.2 * (matches / max(len(query_terms), 1))
        total_score = importance_score + recency_score + query_score
        scored.append((total_score, summary, importance))

    scored.sort(key=lambda x: x[0], reverse=True)
    scored = scored[:limit]

    parts = []
    for score, summary, importance in scored:
        parts.append(f"{summary} (importance={importance}, score={score:.2f})")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════
# 11. GLOBAL SEARCH
# ═══════════════════════════════════════════════════════════════════════

def global_search(query: str, top_k: int = 5, include_keywords: bool = True) -> list:
    """Search across all memory tiers (core + episodic + graph).

    Returns a list of result dicts with 'source', 'content', 'score'.
    """
    ensure_tables_exist()
    results = []

    # Search core memories
    query_lower = query.lower()
    try:
        semantic = semantic_search(query, top_k=top_k)
        for r in semantic:
            results.append({"source": "core", "id": r[0], "content": r[2], "score": r[3]})
    except Exception as e:
        logger.debug(f"Semantic search failed in global search (graceful fallback): {e}")

    # Search episodic journal
    try:
        episodic = search_episodic(query, top_k=top_k)
        for r in episodic:
            results.append({"source": "episodic", "id": r[0], "content": r[2], "score": r[3]})
    except Exception as e:
        logger.debug(f"Episodic search failed in global search (graceful fallback): {e}")

    # Include keyword results if requested
    if include_keywords:
        with closing(_get_conn()) as conn:
            core_rows = conn.execute(
                "SELECT id, content FROM core_memories WHERE INSTR(LOWER(content), LOWER(?)) > 0 LIMIT ?",
                (query, top_k)
            ).fetchall()
            for r in core_rows:
                if not any(existing["id"] == r["id"] and existing["source"] == "core" for existing in results):
                    results.append({"source": "core_keyword", "id": r["id"], "content": r["content"], "score": 0.5})

    # Sort by score descending
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[:top_k]


# ═══════════════════════════════════════════════════════════════════════
# 12. PERSONA A/B VOTING
# ═══════════════════════════════════════════════════════════════════════

def record_persona_vote(prompt: str, persona_a: str, persona_b: str,
                         response_a: str, response_b: str,
                         winner: str = "", notes: str = "") -> int:
    """Record a persona comparison vote.

    Returns the vote ID.
    """
    ensure_tables_exist()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with closing(_get_conn()) as conn:
        cursor = conn.execute(
            "INSERT INTO persona_votes (timestamp, prompt, persona_a, persona_b, response_a, response_b, winner, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, prompt, persona_a, persona_b, response_a, response_b, winner, notes)
        )
        conn.commit()
        return cursor.lastrowid


def update_persona_vote(vote_id: int, winner: str, notes: str = "") -> bool:
    """Update an existing persona vote with winner and notes.

    Returns True on success.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        conn.execute(
            "UPDATE persona_votes SET winner = ?, notes = ? WHERE id = ?",
            (winner, notes, vote_id)
        )
        conn.commit()
    return True


def get_persona_leaderboard(limit: int = 10) -> list:
    """Return aggregate win counts per persona.

    Returns a list of dicts: ``[{"persona": str, "wins": int, "total": int}]``
    sorted by win rate descending.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT persona_a AS persona FROM persona_votes "
            "UNION SELECT persona_b AS persona FROM persona_votes"
        ).fetchall()
    leaderboard = {}
    for r in rows:
        persona = r["persona"]
        with closing(_get_conn()) as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM persona_votes WHERE persona_a = ? OR persona_b = ?",
                (persona, persona)
            ).fetchone()["c"]
            wins = conn.execute(
                "SELECT COUNT(*) AS c FROM persona_votes WHERE winner = ?",
                (persona,)
            ).fetchone()["c"]
        leaderboard[persona] = {"persona": persona, "wins": wins, "total": total}
    return sorted(leaderboard.values(), key=lambda x: x["wins"] / max(x["total"], 1), reverse=True)[:limit]


def get_recent_persona_votes(limit: int = 10) -> list:
    """Return recent persona vote records as dicts."""
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM persona_votes ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# 13. CORE MEMORY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

def get_core_memory_by_id(memory_id: int) -> dict:
    """Fetch a single core memory by ID."""
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM core_memories WHERE id = ?", (memory_id,)
        ).fetchone()
    return dict(row) if row else None


def get_all_core_memories(limit: int = 100) -> list:
    """Return all core memories as dicts, ordered by id DESC."""
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM core_memories ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_entry_content(entry_id: int, new_content: str, source: str = "core") -> bool:
    """Update content for a core memory or episodic entry.

    source: 'core' for core_memories, 'episodic' for episodic_journal.
    Returns True on success.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        if source == "core":
            conn.execute("UPDATE core_memories SET content = ? WHERE id = ?", (new_content, entry_id))
        else:
            conn.execute("UPDATE episodic_journal SET summary = ? WHERE id = ?", (new_content, entry_id))
        conn.commit()
    return True


def boost_entry_importance(entry_id: int, increment: int = 1) -> bool:
    """Increment the importance score of an entry.

    Works for both core_memories and episodic_journal.
    Returns True on success.
    """
    ensure_tables_exist()
    with closing(_get_conn()) as conn:
        conn.execute(
            "UPDATE core_memories SET importance_score = MIN(10, importance_score + ?) WHERE id = ?",
            (increment, entry_id)
        )
        conn.commit()
    return True


def delete_entry(entry_id: int, source: str = "core") -> bool:
    """Delete an entry from core_memories or episodic_journal.

    Also cleans up the corresponding FTS index.
    Returns True on success.
    """
    ensure_tables_exist()
    table = "core_memories" if source == "core" else "episodic_journal"
    with closing(_get_conn()) as conn:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (entry_id,))
        conn.commit()
        # FTS cleanup (inside with block while conn is open)
        if source == "core":
            _fts_sync_core_delete(conn, entry_id)
        else:
            _fts_sync_episodic_delete(conn, entry_id)
    if source == "core":
        _invalidate_vector_cache()
    auto_checkpoint_wal()
    return True


def delete_memory_node(node_id: int) -> bool:
    """Delete a memory node and sever all its inbound/outbound links.

    Cleans up memory_links where source_id = node_id or target_id = node_id,
    removes the row from core_memories, synchronizes the FTS5 index,
    invalidates the vector matrix cache, and triggers an auto WAL checkpoint.

    Returns:
        True if the deletion completed successfully.
    """
    ensure_tables_exist()
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    try:
        conn.execute(
            "DELETE FROM memory_links WHERE source_id = ? OR target_id = ?",
            (node_id, node_id),
        )
        conn.execute("DELETE FROM core_memories WHERE id = ?", (node_id,))
        conn.commit()
        _fts_sync_core_delete(conn, node_id)
        auto_checkpoint_wal(conn)
    finally:
        conn.close()

    _invalidate_vector_cache()
    return True


# ═══════════════════════════════════════════════════════════════════════
# 14. DAILY SUMMARY
# ═══════════════════════════════════════════════════════════════════════

def generate_daily_summary(log_callback=None) -> int:
    """ANTI-FRAGILITY: Uses ``get_provider()`` (not raw ``requests.post()``).

    Generate a compressed narrative summary of today's episodic journal entries.

    Collects all episodic entries from the past 24 hours that haven't been
    summarized yet, uses the AI provider to create a narrative summary,
    and stores it as a high-importance episodic entry (importance=9).

    Returns:
        int: Number of entries summarized, 0 if no new entries or a daily
             summary already exists, -1 on error.
    """
    ensure_tables_exist()
    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    with closing(_get_conn()) as conn:
            # Check if daily summary already exists for today
            today_start = datetime.now().strftime("%Y-%m-%d")
            existing = conn.execute(
                "SELECT COUNT(*) AS c FROM episodic_journal "
                "WHERE tags LIKE '%daily-summary%' AND timestamp >= ?",
                (today_start,)
            ).fetchone()["c"]
            if existing > 0:
                return 0
            # Get entries from the past 24 hours that haven't been summarized yet
            entries = conn.execute(
                "SELECT id, summary, importance_score, metadata FROM episodic_journal "
                "WHERE timestamp >= ? AND (tags NOT LIKE '%daily-summary%' OR tags IS NULL) "
                "ORDER BY timestamp ASC",
                (cutoff,)
            ).fetchall()

    if not entries:
        return 0

    # Filter out entries already marked as summarized in metadata
    unsummarized = []
    for e in entries:
        meta = {}
        try:
            meta = json.loads(e["metadata"]) if e["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        if meta.get("summarized_in_daily") != "true":
            unsummarized.append(e)

    if not unsummarized:
        return 0

    # Build context for AI
    entry_texts = "\n".join(f"- [{e['importance_score']}/10] {e['summary']}" for e in unsummarized)
    if log_callback:
        log_callback(f"Generating daily summary from {len(entries)} entries...")

    try:
        provider = get_provider()
        messages = [
            {"role": "system", "content": (
                "You are a daily summarizer. Create a concise 2-3 paragraph narrative summary "
                "of the day's activities from the journal entries below. Focus on key achievements, "
                "decisions, and important events. Write in past tense."
            )},
            {"role": "user", "content": f"Daily journal entries:\n{entry_texts}"}
        ]
        result = provider.chat_completion(
            messages=messages,
            model=CONFIG.get("model_name", ""),
            temperature=0.2,
            max_tokens=500
        )
        if not result or result.get("error"):
            logger.warning(f"Daily summary generation failed: {result}")
            return -1
        summary_text = result.get("content", "").strip()
        if not summary_text:
            return -1

        # Store as a high-importance episodic entry
        store_episodic(
            summary=summary_text,
            session_id=get_current_session_id(),
            tags=["daily-summary"],
            importance=9,
            metadata={"summarized_in_daily": "true"},
        )
        # Mark original entries as summarized
        for e in unsummarized:
            try:
                with closing(_get_conn()) as conn:
                    old_meta = json.loads(e["metadata"]) if e["metadata"] else {}
                    old_meta["summarized_in_daily"] = "true"
                    conn.execute(
                        "UPDATE episodic_journal SET metadata = ? WHERE id = ?",
                        (json.dumps(old_meta), e["id"])
                    )
                    conn.commit()
            except Exception as e:
                logger.warning(f"Daily summary metadata update failed (vault write — possible data loss): {e}")
        if log_callback:
            log_callback(f"Daily summary stored: {summary_text[:100]}...")
        return len(unsummarized)

    except (OSError, ValueError, RuntimeError, TypeError, KeyError, sqlite3.Error) as e:
        logger.error(f"Daily summary generation failed: {e}")
        if log_callback:
            log_callback(f"Error generating daily summary: {e}")
        return -1


# ─── Run cross-encoder check on import ──────────────────────────────
_check_cross_encoder_config()
