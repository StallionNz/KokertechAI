"""
cognitive_auditor.py — Self-improvement loop for KokertechAI.

After every interaction, the auditor analyses user/AI text for:
  - Cognitive biases (confirmation bias, anchoring, etc.)
  - Growth/energy-shift events (user frustration, excitement, etc.)

Results are persisted to the SQLite vault (bias_ledger + growth_arcs
tables) for long-term self-correction.  The auditor runs in a background
worker (AuditorWorker) and is non-blocking.
"""

import json
import os
import sqlite3
from datetime import datetime

from config import CONFIG
import re

from ai_base import get_provider


# Pre-compiled regex for extracting JSON from code fences
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(WORKSPACE_DIR, "kokertech_vault.db")

# agent_id attribution for rows this auditor writes (canonical schema —
# bias_ledger/growth_arcs.agent_id is NOT NULL, owned by memory_vault).
AUDITOR_AGENT_ID = "cognitive_auditor"

from logging_config import get_logger

logger = get_logger(name="CognitiveAuditor")

_AUDITOR_SYSTEM_PROMPT = (
    "You are a cognitive auditor. Analyse the following interaction "
    "between a user and an AI assistant.\n\n"
    "Return JSON with this exact structure:\n"
    '{\n'
    '  "biases": [\n'
    '    {\n'
    '      "bias_type": "<type of bias>",\n'
    '      "confidence_score": <0-100>,\n'
    '      "description": "<brief description>"\n'
    '    }\n'
    '  ],\n'
    '  "growths": [\n'
    '    {\n'
    '      "event_description": "<what happened>",\n'
    '      "energy_shift": <negative or positive float>\n'
    '    }\n'
    '  ]\n'
    '}\n\n'
    "Only include biases with confidence >= 80.\n"
    "Return empty arrays if nothing meaningful is detected."
)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def ensure_agency_tables():
    """Create the ``bias_ledger`` and ``growth_arcs`` tables if they
    do not already exist in the vault database.

    Safe to call repeatedly — uses ``CREATE TABLE IF NOT EXISTS``.

    Schema (2026-09): MUST match memory_vault.ensure_tables_exist — that
    module owns these tables and the live DB was created with
    ``timestamp``/``agent_id`` columns. This function previously declared
    a drifted ``created_at``-based variant; on existing DBs the
    IF-NOT-EXISTS is a no-op so the drifted INSERT then failed with
    "table bias_ledger has no column named created_at" (live-reported).
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bias_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    agent_id TEXT NOT NULL,
                    bias_type TEXT NOT NULL,
                    confidence_score REAL DEFAULT 0.5,
                    description TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS growth_arcs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                    agent_id TEXT NOT NULL,
                    event_description TEXT,
                    energy_shift REAL DEFAULT 0.0
                )
            """)
            conn.commit()
        finally:
            if hasattr(conn, "close"):
                conn.close()
    except sqlite3.OperationalError as exc:
        logger.debug(f"Agency tables already exist or creation failed: {exc}")


# ---------------------------------------------------------------------------
# Audit logic
# ---------------------------------------------------------------------------


def audit_interaction(
    user_text: str,
    ai_text: str,
    log_callback=None,
):
    """Analyse a single user↔AI interaction for biases and growth events.

    Steps:
      1. Ensure agency tables exist (idempotent).
      2. Call the AI provider with a structured system prompt.
      3. Parse the JSON response into biases and growths.
      4. Insert biases with ``confidence_score >= 80`` into
         ``bias_ledger`` (skip lower-confidence entries).
      5. Insert all growth events into ``growth_arcs``.
      6. Log a summary via *log_callback* (if provided).

    Args:
        user_text: The user's input text.
        ai_text: The AI's response text.
        log_callback: Optional callable receiving status/error strings.
            Called by ``AuditorWorker`` with ``self.log_signal.emit``
            for UI display.
    """
    if log_callback is None:
        log_callback = _noop_logger

    ensure_agency_tables()

    try:
        provider = get_provider(name=CONFIG.get("active_provider", "local_llm"))
        model = CONFIG.get("auditor_model_name", CONFIG.get("model_name", ""))

        result = provider.chat_completion(
            messages=[
                {"role": "system", "content": _AUDITOR_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"User: {user_text}\n\n"
                        f"AI Response: {ai_text}"
                    ),
                },
            ],
            model=model,
            temperature=0.1,
            max_tokens=300,
            timeout=30,
        )

        if result.get("error"):
            log_callback(f"⚠️ Auditor provider error: {result['error']}")
            return

        content = result.get("content", "")
        if not content:
            log_callback("⚠️ Auditor received empty response")
            return

        _process_audit_result(content, log_callback)

    except (RuntimeError, OSError, sqlite3.OperationalError) as exc:
        log_callback(f"⚠️ Auditor failed: {exc}")


# ---------------------------------------------------------------------------
# Response parsing & DB persistence
# ---------------------------------------------------------------------------


def _noop_logger(_msg: str) -> None:
    """Default no-op log callback when none is provided."""


def _process_audit_result(content: str, log_callback) -> None:
    """Parse the JSON response from the auditor and persist to the DB.

    Args:
        content: Raw text response from the AI provider.
        log_callback: Callable for logging status messages.
    """
    data = _try_parse_json(content)
    if data is None:
        log_callback("ℹ️ Auditor: No actionable data in response")
        return

    biases = data.get("biases", [])
    growths = data.get("growths", [])

    if not biases and not growths:
        log_callback("ℹ️ Auditor: No actionable data (empty arrays)")
        return

    try:
        conn = sqlite3.connect(DB_PATH, timeout=15.0)
        try:
            _insert_biases(conn, biases)
            _insert_growths(conn, growths)
            conn.commit()
        finally:
            if hasattr(conn, "close"):
                conn.close()

        log_callback(
            f"✅ Auditor: {len(biases)} bias(es), "
            f"{len(growths)} growth event(s) logged"
        )
    except (RuntimeError, sqlite3.OperationalError, ValueError) as exc:
        log_callback(f"⚠️ Auditor DB write failed: {exc}")


def _try_parse_json(content: str):
    """Attempt to parse the content string as JSON.

    Tries the raw text first; if that fails, attempts to extract a JSON
    block from within ``json``` code fences (common LLM output pattern).
    """
    # Direct parse
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting from code fence
    m = _JSON_FENCE_RE.search(content)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _insert_biases(conn, biases: list) -> None:
    """Insert bias entries with ``confidence_score >= 80``.

    NOTE: ``created_at`` is the **first** parameter so the test's
    ``params[1]`` resolves to ``bias_type`` and ``params[2]`` resolves
    to ``confidence_score``.  See ``test_inserts_bias_with_80pct_threshold``
    in ``tests/test_cognitive_auditor.py``.

    Uses ``conn.cursor().execute()`` rather than ``conn.execute()`` for
    compatibility with the test's ``FakeConn`` (which only has a
    ``cursor()`` method, not the Python 3.12+ ``Connection.execute``
    shortcut).

    Column order matches memory_vault.log_bias — the canonical
    ``timestamp, agent_id, bias_type, confidence_score, description``
    (created_at never existed in the live table; 2026-09 fix).
    """
    now = datetime.now().isoformat()
    cur = conn.cursor()
    for bias in biases:
        score = int(bias.get("confidence_score", 0))
        if score < 80:
            continue
        cur.execute(
            "INSERT INTO bias_ledger "
            "(timestamp, agent_id, bias_type, confidence_score, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                now,
                AUDITOR_AGENT_ID,
                bias.get("bias_type", "Unknown"),
                score,
                bias.get("description", ""),
            ),
        )


def _insert_growths(conn, growths: list) -> None:
    """Insert all growth event entries.

    Column order matches memory_vault.log_growth — the canonical
    ``timestamp, agent_id, event_description, energy_shift``
    (created_at never existed in the live table; 2026-09 fix).
    """
    now = datetime.now().isoformat()
    cur = conn.cursor()
    for event in growths:
        cur.execute(
            "INSERT INTO growth_arcs "
            "(timestamp, agent_id, event_description, energy_shift) "
            "VALUES (?, ?, ?, ?)",
            (
                now,
                AUDITOR_AGENT_ID,
                event.get("event_description", ""),
                float(event.get("energy_shift", 0.0)),
            ),
        )
