"""services/hive_service.py — Autonomous Speculative Multi-Agent Hive Service.

Realm 3: Autonomous Speculative Multi-Agent Hive.
Operationalizes Behavior Contract v4.2 (agent/Kokertech.agent.md) by coordinating
the Mode 2 God Reviewer multi-agent swarm:
    User Goal -> Orchestrator -> Planner -> Coder (Speculative) -> Auditor (God Reviewer) -> Synthesizer

Features:
- Trigger detection (<<HIVE:...>>, <<AGENT:...>>, /hive, or CONFIG["hive_orchestration_enabled"])
- Real-time progressive phase streaming & status logging
- Mode 2 verification loop with max_retries=2 deadlock prevention
- Speculative drafting integration via SpeculativeDraftEngine
- Graceful cooperative cancellation (cancel_event)
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from config import CONFIG
from logging_config import get_logger
from services.speculative_service import SpeculativeDraftEngine
from sub_agents import (
    AuditorAgent,
    CoderAgent,
    OrchestratorAgent,
    PlannerAgent,
    SynthesizerAgent,
    _detect_intent,
)

logger = get_logger(name="HiveService")

_HIVE_TAG_PATTERN = re.compile(
    r"^\s*(?:<<HIVE:(.+)>>|<<AGENT:(.+)>>|/hive\s+(.+))\s*$",
    re.IGNORECASE | re.DOTALL,
)


class HiveService:
    """Coordinates autonomous multi-agent swarm execution and speculative routing."""

    def __init__(self) -> None:
        self.speculative_engine = SpeculativeDraftEngine()

    def should_route_to_hive(self, text: str) -> Tuple[bool, str]:
        """Detect whether a query warrants execution via the multi-agent Hive.

        Returns:
            (is_hive, cleaned_goal_string)
        """
        if not text or not isinstance(text, str):
            return False, ""

        stripped = text.strip()

        # 1. Explicit tag or slash command
        m = _HIVE_TAG_PATTERN.match(stripped)
        if m:
            goal = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            if goal:
                return True, goal

        # 2. Configuration-enabled automatic orchestration
        if CONFIG.get("hive_orchestration_enabled", False):
            intent = _detect_intent(stripped)
            # Route complex coding, planning, or audit tasks to Hive
            if intent in ("code", "plan", "audit"):
                return True, stripped

        return False, ""

    def execute_hive_flow(
        self,
        goal: str,
        log_callback: Optional[Callable[[str], None]] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
        cancel_event: Optional[threading.Event] = None,
        max_retries: Optional[int] = None,
        speculative_candidates: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Execute the full 5-stage Autonomous Speculative Multi-Agent Hive flow.

        Stages:
            1. Orchestrator: Scope analysis and agent routing directive.
            2. Planner: Architecture and dependency execution plan.
            3. Coder: Speculative/surgical code generation.
            4. Auditor (God Reviewer): Zero-trust quality gate evaluation.
               (Re-enters Coder if rejected, bounded by max_retries).
            5. Synthesizer: Final executive deliverable assembly.

        Returns:
            Decision-8 formatted response dict:
            {"final": str, "thinking": str, "command": None, "ts": str, "hive_meta": dict}
        """
        def _log(msg: str) -> None:
            if log_callback:
                try:
                    log_callback(msg)
                except (RuntimeError, TypeError, AttributeError, OSError):
                    pass

        def _stream(chunk: str) -> None:
            if stream_callback:
                try:
                    stream_callback(chunk)
                except (RuntimeError, TypeError, AttributeError, OSError):
                    pass

        def _check_cancelled() -> bool:
            return cancel_event is not None and cancel_event.is_set()

        limit_retries = max_retries if max_retries is not None else CONFIG.get("hive_max_retries", 2)
        if limit_retries < 1:
            limit_retries = 2

        num_candidates = (
            speculative_candidates
            if speculative_candidates is not None
            else int(CONFIG.get("hive_speculative_candidates", 1))
        )

        logger.info(f"Initiating Autonomous Hive flow for goal: {goal[:80]}...")
        _log("🐝 [HIVE: ORCHESTRATOR] Autonomous Multi-Agent Swarm activated.")
        _stream("### 🐝 Autonomous Hive: Multi-Agent Swarm Engaged\n\n")

        if _check_cancelled():
            return self._build_cancelled_response(goal)

        # Stage 1: Orchestrator directive
        orchestrator = OrchestratorAgent()
        _log("🐝 [HIVE: ORCHESTRATOR] Analyzing goal and decomposing requirements...")
        _stream("[🐝 Orchestrator] Analyzing goal and decomposing requirements...\n\n")
        orch_prompt = (
            f"Analyze user goal and produce an orchestration directive for the swarm:\n"
            f"Goal: {goal}\n\n"
            f"State primary deliverables, key technical risks, and quality standards."
        )
        orch_directive = orchestrator.execute(orch_prompt)
        _stream(f"**Orchestrator Directive**:\n> {orch_directive.replace(chr(10), ' ')}\n\n")

        if _check_cancelled():
            return self._build_cancelled_response(goal)

        # Stage 2: Planner execution plan
        planner = PlannerAgent()
        _log("📝 [HIVE: PLANNER] Generating multi-step execution plan...")
        _stream("[📐 Planner] Formulating architectural execution plan...\n\n")
        _stream("#### 📝 Stage 1: Architectural Execution Plan\n")
        plan_prompt = (
            f"User Goal: {goal}\n\n"
            f"Orchestrator Directive:\n{orch_directive}\n\n"
            f"Construct a concise step-by-step engineering plan with invariants, "
            f"file locations, and verification steps."
        )
        plan = planner.execute(plan_prompt)
        _stream(f"{plan}\n\n")

        if _check_cancelled():
            return self._build_cancelled_response(goal)

        # Stage 3 & 4: Mode 2 Coder + Auditor verification loop with speculative drafting
        coder = CoderAgent()
        auditor = AuditorAgent()

        retries = 0
        current_feedback = ""
        last_code = ""
        last_audit = ""
        speculative_used = False
        candidates_history = []

        _log("💻 [HIVE: CODER] Formulating implementation...")
        _stream("#### 💻 Stage 2: Implementation & Verification Loop\n")

        def default_is_rejected(audit_text: str) -> bool:
            if not audit_text or not isinstance(audit_text, str):
                return True
            upper = audit_text.upper().strip()
            if upper.startswith(("APPROVED", "VERDICT: APPROVED", "STATUS: APPROVED", "LGTM")):
                if "OVERALL: REJECT" not in upper and "VERDICT: REJECT" not in upper:
                    return False
            sanitized = upper
            for benign in [
                "NO CHANGES REQUIRED", "NO CHANGE REQUIRED", "0 FAILED", "0 FAILURES",
                "ZERO FAILURES", "NO REJECTION", "WITHOUT FAILURE", "NO FAILURES",
                "NOT FAIL", "NO FAILS",
            ]:
                sanitized = sanitized.replace(benign, "")
            for indicator in [
                "REJECT", "CHANGES REQUIRED", "NEEDS REVISION", "REQUEST CHANGES", "DISAPPROVED",
            ]:
                if indicator in sanitized:
                    return True
            if re.search(r"\b(FAIL|FAILED|FAILURE|FAILURES)\b", sanitized):
                return True
            return False

        while True:
            if _check_cancelled():
                return self._build_cancelled_response(goal)

            if retries == 0:
                coder_task = (
                    f"Goal: {goal}\n\n"
                    f"Execution Plan:\n{plan}\n\n"
                    f"Deliver complete, production-ready, clean Python code."
                )
            else:
                coder_task = (
                    f"Goal: {goal}\n\n"
                    f"Previous Implementation:\n{last_code}\n\n"
                    f"Auditor Critique / Revision Instructions:\n{current_feedback}\n\n"
                    f"Apply surgical fixes strictly addressing the auditor critique."
                )

            _log(f"💻 [HIVE: CODER] Formulating implementation (Attempt {retries + 1})...")
            _stream(f"[💻 Coder] Formulating implementation (Attempt {retries + 1})...\n\n")

            # Check if multi-candidate speculative drafting is active
            if num_candidates > 1 and retries == 0:
                _log(f"⚡ [HIVE: SPECULATIVE] Generating {num_candidates} candidate implementations...")
                _stream(f"⚡ *Drafting {num_candidates} speculative candidate implementations…*\n\n")
                candidates = []
                for idx in range(num_candidates):
                    if _check_cancelled():
                        return self._build_cancelled_response(goal)
                    temp = 0.1 if idx == 0 else 0.35
                    _stream(f"  - Drafting candidate {idx + 1} (temp={temp})…\n")
                    c_out = coder.execute(coder_task, temperature=temp)
                    candidates.append(c_out)

                candidates_history = candidates
                _log("⚖️ [HIVE: AUDITOR] Comparing speculative candidates...")
                _stream("\n⚖️ *Auditor selecting superior candidate…*\n\n")
                eval_prompt = (
                    f"User Goal:\n{goal}\n\n"
                    f"Candidate 1:\n{candidates[0]}\n\n"
                    f"Candidate 2:\n{candidates[1]}\n\n"
                    f"Select the superior candidate that best satisfies zero-trust invariants, "
                    f"clean error handling, and complete functionality. Return ONLY the code of the winning candidate."
                )
                winner_code = auditor.execute(eval_prompt)
                code = (
                    winner_code
                    if winner_code.strip() and not winner_code.startswith("Error")
                    else candidates[0]
                )
                speculative_used = True
            elif CONFIG.get("speculative_drafting_enabled", False):
                spec_res = self.speculative_engine.draft_and_verify(
                    prompt=coder_task,
                    system_prompt=coder._system_prompt(),
                    cancel_event=cancel_event,
                    progress_callback=_log,
                )
                code = spec_res.get("final", "")
                speculative_used = spec_res.get("verified", False)
            else:
                code = coder.execute(coder_task)

            last_code = code

            if _check_cancelled():
                return self._build_cancelled_response(goal)

            # Stage 4: Auditor (God Reviewer)
            _log(f"⚖️ [HIVE: AUDITOR] Running God Reviewer verification gate (Attempt {retries + 1})...")
            _stream(f"[⚖️ Auditor] Running God Reviewer verification gate (Attempt {retries + 1})...\n\n")
            audit_prompt = (
                f"User Goal:\n{goal}\n\n"
                f"Coder Output:\n{code}\n\n"
                f"Audit against zero-trust invariants: deterministic error handling, "
                f"no bare excepts, concurrency locks, and test isolation. "
                f"State VERDICT: APPROVED if clean, or VERDICT: REJECT with specific issues."
            )
            audit_result = auditor.execute(audit_prompt)
            last_audit = audit_result

            is_reject = default_is_rejected(audit_result)
            if is_reject:
                retries += 1
                current_feedback = audit_result
                _log(f"⚠️ [HIVE: AUDITOR] Changes required ({retries}/{limit_retries}).")
                _stream(f"*Attempt {retries} rejected by Auditor:* {audit_result[:200]}…\n\n")
                if retries >= limit_retries:
                    err_msg = (
                        f"Mode 2 verification loop exceeded max_retries={limit_retries}. "
                        f"Auditor rejected Coder output {retries} times.\n"
                        f"Final Critique:\n{last_audit}"
                    )
                    logger.warning(err_msg)
                    _log(f"❌ [HIVE] Deadlock limit reached ({limit_retries} attempts).")
                    break
            else:
                _log("✅ [HIVE: AUDITOR] God Reviewer verification PASSED.")
                _stream(f"✅ **God Reviewer Audit**: Approved\n\n```\n{audit_result.strip()[:300]}…\n```\n\n")
                break

        if _check_cancelled():
            return self._build_cancelled_response(goal)

        # Stage 5: Synthesizer compilation
        synthesizer = SynthesizerAgent()
        _log("✨ [HIVE: SYNTHESIZER] Assembling executive deliverable...")
        _stream("[✨ Synthesizer] Assembling executive deliverable...\n\n")
        _stream("#### ✨ Stage 3: Executive Synthesis\n")
        synth_prompt = (
            f"User Goal: {goal}\n\n"
            f"Plan:\n{plan}\n\n"
            f"Verified Code:\n{last_code}\n\n"
            f"Auditor Evaluation:\n{last_audit}\n\n"
            f"Synthesize an authoritative, executive-grade deliverable presenting the "
            f"solution, instructions, and verification summary."
        )
        synthesis = synthesizer.execute(synth_prompt)
        _stream(f"{synthesis}\n")

        full_final = (
            f"### 🐝 Autonomous Hive Swarm Report\n\n"
            f"**Goal**: {goal}\n\n"
            f"---\n\n"
            f"{synthesis}\n\n"
            f"<details><summary><b>View Implementation Details & Audit Trail</b></summary>\n\n"
            f"#### Execution Plan\n{plan}\n\n"
            f"#### Code Payload\n```python\n{last_code}\n```\n\n"
            f"#### God Reviewer Audit\n{last_audit}\n\n"
            f"</details>"
        )

        thinking = (
            f"Autonomous Hive executed {retries + 1} iteration(s) across Orchestrator, "
            f"Planner, Coder, Auditor (God Reviewer), and Synthesizer. "
            f"Speculative drafting: {'Active' if speculative_used else 'Bypassed'}."
        )

        _log("🎉 [HIVE] Autonomous multi-agent swarm execution complete.")

        return {
            "final": full_final,
            "thinking": thinking,
            "command": None,
            "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "hive_meta": {
                "status": "approved" if retries < limit_retries else "deadlock",
                "retries": retries,
                "plan": plan,
                "code": last_code,
                "audit": last_audit,
                "speculative": speculative_used,
                "candidates": candidates_history,
            },
        }

    def _build_cancelled_response(self, goal: str) -> Dict[str, Any]:
        """Construct response dict when execution was cancelled mid-flight."""
        return {
            "final": "⏹ Hive execution stopped by user.",
            "thinking": f"Autonomous Hive stopped during execution for goal: {goal[:60]}.",
            "command": None,
            "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "stopped": True,
            "hive_meta": {"status": "cancelled"},
        }
