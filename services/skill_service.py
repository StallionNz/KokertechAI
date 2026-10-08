"""skill_service.py -- Sprint 19.2: Skill policy injection service.

Extracted from ``KokertechController._inject_skill_policy`` in Sprint 19.2.
Decouples the SKILL.md file-read logic from the main facade so future
"skill pack discovery" / multi-skill composition can live here without
touching ``kokertechController.py``.

LAZY-SINGLETON PATTERN (per ``KNOWLEDGE.md`` section 10 R12 + ``CodeIntelligenceFactory``
precedent):

- ``_SKILL_SERVICE_SINGLETON`` + ``_SKILL_SERVICE_INIT_LOCK`` at module level.
- Per-instance ``self._lock = threading.RLock()`` for re-entrant safety.
- Public accessor: ``get_skill_service()`` returns the cached singleton.
- Reset helper: ``_reset_skill_service_for_tests()`` clears the module-
  level cache under the init lock (NEVER touches ``registry._lock``).
"""
from __future__ import annotations

import os
import threading
from typing import Optional

from config import CONFIG
from logging_config import get_logger
from services import get_services


class SkillService:
    """Inject ``[SKILL POLICY: <name>]`` blocks into system prompts.

    Reads ``<workspace>/.blackbox/skills/<name>/SKILL.md`` and appends the
    wrapped block to ``system_prompt``. Returns the prompt unchanged when:

      * ``active_skill`` is empty/None,
      * the SKILL.md file does not exist,
      * the SKILL.md file is empty after ``strip()``, or
      * any I/O exception is raised (logged at ``debug``).

    ====================================================================
    Decision: Option A (append) -- see ``implementation-roadmap.md``
    Sprint 16 / Phase E row 16.3 for the full A vs B comparison.
    ====================================================================
    Option A (append) -- CHOSEN:
        Keep the existing base protocol (structured/freeform/custom) AND
        append a ``[SKILL POLICY: <name>]\n{skill_doc}\n[END SKILL POLICY]``
        block at the end of the system prompt. Skill doc + parsing protocol
        co-exist; net length grows by ~``len(skill_doc)`` + 50 chars of
        delimiters.

    Option B (replace) -- REJECTED for this codebase, with three concrete
    failure modes:
        1. Loses ``STRUCTURED_SYSTEM_PROMPT`` -- the LLM stops emitting
           ``<final_output>`` tags; ``parse_ai()`` falls back to raw text
           and downstream ``handle_ai_response()`` misrenders.
        2. Loses ``[LOADED TOOL SCHEMAS]`` from progressive disclosure --
           plugin ``command`` calls stop working mid-conversation.
        3. Loses ``[RECENT BIASES TO AVOID]`` -- the SCBE bias-feedback
           block stops reaching the LLM, regressing self-correction.
    ====================================================================
    Delimiter safety -- the block delimiters ``[SKILL POLICY]`` and
    ``[END SKILL POLICY]`` use square brackets, NEVER angle brackets.
    ``parse_ai()`` scans the *model output* (not the system prompt) for
    ``<final_output>...</final_output>`` with optional
    ``<thinking>...</thinking>`` prefix -- and so is unaffected by anything
    inside ``system_prompt``. The remaining risk is if the skill doc body
    itself contains XML-like fragments that the LLM echoes verbatim back
    as ``Example 1:``-style output; that path is covered by
    ``kokertechController._is_sentinel_response()`` defense-in-depth (see
    ``KNOWLEDGE.md`` Decision 8 Layer C).
    ====================================================================

    Note for skill authors -- avoid literal ``<final_output>`` /
    ``<thinking>`` / ``<example>`` patterns inside ``SKILL.md`` bodies.
    ``parse_ai()`` AND ``_is_sentinel_response()`` both scan the MODEL
    OUTPUT -- a verbatim echo of these tags from the system prompt would
    be incorrectly attributed as the LLM's answer.
    """

    def __init__(self) -> None:
        # Per-instance RLock (not plain Lock) so any future helper that
        # re-enters via inject()/set_workspace() cannot deadlock. Mirrors
        # the CodeIntelligenceFactory choice.
        self._lock = threading.RLock()
        self.workspace: Optional[str] = None
        self.logger = get_logger(name="SkillService")

    def set_workspace(self, workspace: str) -> None:
        """Mirror the facade's workspace path into this service.

        Called from ``KokertechController._sync_services_workspace`` so
        test patches to ``ctrl.workspace`` propagate before the next
        ``inject()`` call. Idempotent under the per-instance RLock.
        """
        with self._lock:
            self.workspace = workspace

    def inject(self, system_prompt: str, workspace: str,
               active_skill: Optional[str] = None) -> str:
        """Append the active skill's policy block to ``system_prompt``."""
        if active_skill is None:
            raw = CONFIG.get("active_skill", "")
            active_skill = raw.strip() if raw else ""
        if not active_skill:
            return system_prompt
        try:
            skill_path = os.path.join(
                workspace, ".blackbox", "skills", active_skill, "SKILL.md",
            )
            if not os.path.exists(skill_path):
                self.logger.warning(f"Skill doc not found: {skill_path}")
                return system_prompt
            with open(skill_path, "r", encoding="utf-8") as f:
                skill_doc = f.read().strip()
            if not skill_doc:
                return system_prompt
            self.logger.info(f"Skill policy injected: {active_skill}")
            return (
                system_prompt
                + f"\n\n[SKILL POLICY: {active_skill}]\n{skill_doc}\n"
                + "[END SKILL POLICY]"
            )
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
            self.logger.debug(f"Skill policy injection skipped: {e}")
            return system_prompt


# Module-level lazy singleton + init lock (matches CodeIntelligenceFactory
# pattern per ``KNOWLEDGE.md`` section 10 R12).
_SKILL_SERVICE_SINGLETON: Optional[SkillService] = None
_SKILL_SERVICE_INIT_LOCK = threading.Lock()


def get_skill_service() -> SkillService:
    """Eager-construct + register under ``ServiceRegistry`` (lazy singleton)."""
    global _SKILL_SERVICE_SINGLETON
    with _SKILL_SERVICE_INIT_LOCK:
        if _SKILL_SERVICE_SINGLETON is None:
            _SKILL_SERVICE_SINGLETON = SkillService()
            get_services().register_instance(
                "skill_service", _SKILL_SERVICE_SINGLETON,
            )
        return _SKILL_SERVICE_SINGLETON


def _reset_skill_service_for_tests() -> None:
    """Reset module-level singleton under init lock (NEVER touches registry._lock)."""
    global _SKILL_SERVICE_SINGLETON
    with _SKILL_SERVICE_INIT_LOCK:
        _SKILL_SERVICE_SINGLETON = None
