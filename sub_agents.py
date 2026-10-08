"""
sub_agents.py — Specialized sub-agent system for KokertechAI.

Sprint 5.2: ResearcherAgent, CoderAgent, AuditorAgent, PlannerAgent
with distinct system prompts and capabilities.

Architecture:
    SubAgent is the base class.  Each persona class is a thin wrapper
    that sets its own system prompt.  execute() calls the AI provider
    via get_provider().  execute_async() wraps execute() in a daemon
    thread.  run_agent_team() orchestrates multiple agents.
"""

import re
import threading
from typing import Optional, List, Dict, Callable, Any

from config import CONFIG
from logging_config import get_logger
from ai_base import get_provider


logger = get_logger(name="SubAgents")

_PERSONA_PROMPTS = {
    "Researcher": (
        "You are a Research Agent. Your role is to search, analyse, and "
        "synthesise information. Be thorough, cite sources, and provide "
        "structured summaries."
    ),
    "Coder": (
        "You are a Coding Agent. Your role is to write clean, tested "
        "Python code. Follow best practices, add type hints, and include "
        "docstrings. Output only the code and a brief explanation."
    ),
    "Auditor": (
        "You are an Audit Agent. Your role is to review code, "
        "configurations, or decisions for bugs, security issues, "
        "performance problems, and correctness. Be critical and specific."
    ),
    "Planner": (
        "You are a Planning Agent. Your role is to break down complex "
        "tasks into steps with dependencies, estimated effort, and "
        "success criteria. Output structured plans."
    ),
    "ToolUser": (
        "You are a Tool-Use Agent. You have access to tools (plugins) "
        "for searching the web, reading/writing files, executing code, "
        "and checking system status. Use <<TOOL_CALL:{...}>> to invoke tools."
    ),
    "Orchestrator": (
        "You are an Orchestrator Agent. Your role is to act as the main "
        "router, classify user intent, delegate tasks to specialized sub-agents, "
        "and synthesize responses."
    ),
    "Synthesizer": (
        "You are a Synthesizer Agent. Your role is to compile, integrate, "
        "and harmonize findings and outputs from multiple sources into a coherent whole."
    ),
}

PERSONA_CONFIGS = {
    "Coder": {"temperature": 0.1, "max_tokens": 3000},
    "Auditor": {"temperature": 0.4, "max_tokens": 2500},
    "Planner": {"temperature": 0.2, "max_tokens": 2000},
    "Researcher": {"temperature": 0.2, "max_tokens": 2000},
    "Synthesizer": {"temperature": 0.3, "max_tokens": 2000},
    "Orchestrator": {"temperature": 0.3, "max_tokens": 2000},
    "ToolUser": {"temperature": 0.3, "max_tokens": 2000},
}

VALID_PERSONAS = frozenset(_PERSONA_PROMPTS.keys())


class Mode2DeadlockError(RuntimeError):
    """Raised when Auditor rejects Coder output max_retries times in Mode 2 verification loop."""
    pass


# ---------------------------------------------------------------------------
# SubAgent base class
# ---------------------------------------------------------------------------


class SubAgent:
    """Base class for all sub-agents.

    Args:
        persona: One of the valid persona names (Researcher, Coder, etc.).
        provider_name: AI provider name (defaults to CONFIG["active_provider"]).
        model: Model name (defaults to CONFIG["model_name"]).
        temperature: Optional temperature override (defaults to persona config).
        max_tokens: Optional max_tokens override (defaults to persona config).
    """

    def __init__(
        self,
        persona: str,
        provider_name: str = None,
        model: str = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        if persona not in VALID_PERSONAS:
            raise ValueError(
                f"Unknown persona '{persona}'. Valid: {sorted(VALID_PERSONAS)}"
            )
        self.persona = persona
        self._provider_name = provider_name or CONFIG.get("active_provider", "local_llm")
        self._model = model or CONFIG.get("model_name", "")
        cfg = PERSONA_CONFIGS.get(persona, {})
        self.temperature = temperature if temperature is not None else cfg.get("temperature", 0.3)
        self.max_tokens = max_tokens if max_tokens is not None else cfg.get("max_tokens", 2000)

    def _system_prompt(self) -> str:
        """Return the system prompt for this agent's persona."""
        return _PERSONA_PROMPTS.get(self.persona, "You are a helpful AI assistant.")

    def execute(self, task: str, temperature: Optional[float] = None) -> str:
        """Execute a task and return the result.

        Calls the AI provider with the persona's system prompt and the
        task as a user message.

        Args:
            task: The task description.

        Returns:
            The AI response content, or an error message on failure.
        """
        try:
            provider = get_provider(name=self._provider_name)
            messages = [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": task},
            ]
            result = provider.chat_completion(
                messages=messages,
                model=self._model,
                temperature=self.temperature if temperature is None else temperature,
                max_tokens=self.max_tokens,
                timeout=60,
            )
            if result.get("error"):
                return f"Error: {result['error']}"
            return result.get("content", "No response")
        except ConnectionError as exc:
            return f"Failed: {exc}"
        except (RuntimeError, ValueError, OSError, TypeError, KeyError) as exc:
            return f"Failed: {exc}"

    def execute_async(
        self,
        task: str,
        callback: Optional[Callable[[str], None]] = None,
    ) -> threading.Thread:
        """Execute a task in a background daemon thread.

        Args:
            task: The task description.
            callback: Optional callable invoked with the result string
                when execution completes.

        Returns:
            A daemon ``threading.Thread`` that has already been started.
        """
        def _run():
            result = self.execute(task)
            if callback:
                try:
                    callback(result)
                except Exception:
                    pass

        t = threading.Thread(target=_run, daemon=True, name=f"subagent-{self.persona}")
        t.start()
        return t


# ---------------------------------------------------------------------------
# Persona-specific subclasses
# ---------------------------------------------------------------------------


class ResearcherAgent(SubAgent):
    """Researcher — search, analyse, synthesise information."""

    def __init__(self, **kwargs):
        super().__init__(persona="Researcher", **kwargs)


class CoderAgent(SubAgent):
    """Coder — write clean, tested Python code."""

    def __init__(self, **kwargs):
        super().__init__(persona="Coder", **kwargs)


class AuditorAgent(SubAgent):
    """Auditor — review for bugs, security, performance."""

    def __init__(self, **kwargs):
        super().__init__(persona="Auditor", **kwargs)


class PlannerAgent(SubAgent):
    """Planner — break down complex tasks into steps."""

    def __init__(self, **kwargs):
        super().__init__(persona="Planner", **kwargs)


class SynthesizerAgent(SubAgent):
    """Synthesizer — compile and harmonize multi-source findings."""

    def __init__(self, **kwargs):
        super().__init__(persona="Synthesizer", **kwargs)


class ToolUserAgent(SubAgent):
    """ToolUser — invoke plugins and execute system tools."""

    def __init__(self, **kwargs):
        super().__init__(persona="ToolUser", **kwargs)


class OrchestratorAgent(SubAgent):
    """Orchestrator — main router, intent classification & delegation."""

    def __init__(self, **kwargs):
        super().__init__(persona="Orchestrator", **kwargs)

    def run_mode2(
        self,
        goal: str,
        planner: Optional[SubAgent] = None,
        coder: Optional[SubAgent] = None,
        auditor: Optional[SubAgent] = None,
        max_retries: int = 2,
        is_rejected: Optional[Callable[[str], bool]] = None,
    ) -> Dict[str, Any]:
        """Execute Mode 2: God Reviewer Audit verification loop.

        Flow:
            User Goal -> Orchestrator -> Planner -> Coder -> Auditor (God Reviewer) -> Result.
            Context Scoping: The Orchestrator passes only the User's Original Goal
            and the Coder's raw output to the Auditor. Intermediate chat history is stripped.
            Deadlock Prevention: Enforces max_retries=2 counter. If Auditor rejects
            the Coder's output twice (or max_retries times), raises Mode2DeadlockError.

        Args:
            goal: The original user goal.
            planner: Optional custom planner agent.
            coder: Optional custom coder agent.
            auditor: Optional custom auditor agent.
            max_retries: Maximum allowable rejection retries (default 2).
            is_rejected: Optional custom callable(audit_text) -> bool to evaluate rejection.

        Returns:
            Dict containing:
                - 'status': 'approved'
                - 'plan': str
                - 'code': str
                - 'audit': str
                - 'retries': int

        Raises:
            ValueError: If goal is empty or max_retries < 1.
            Mode2DeadlockError: If Auditor rejects Coder output max_retries times.
        """
        if not goal or not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must be a non-empty string")
        if max_retries < 1:
            raise ValueError(f"max_retries must be >= 1, got {max_retries}")

        planner = planner or PlannerAgent()
        coder = coder or CoderAgent()
        auditor = auditor or AuditorAgent()

        plan = planner.execute(f"Create execution plan for: {goal}")

        retries = 0
        current_feedback = ""
        last_code = ""
        last_audit = ""

        def default_is_rejected(audit_text: str) -> bool:
            if not audit_text or not isinstance(audit_text, str):
                return True
            upper = audit_text.upper().strip()
            if upper.startswith(("APPROVED", "VERDICT: APPROVED", "STATUS: APPROVED", "LGTM")):
                if "OVERALL: REJECT" not in upper and "VERDICT: REJECT" not in upper:
                    return False
            sanitized = upper
            for benign in [
                "NO CHANGES REQUIRED",
                "NO CHANGE REQUIRED",
                "0 FAILED",
                "0 FAILURES",
                "ZERO FAILURES",
                "NO REJECTION",
                "WITHOUT FAILURE",
                "NO FAILURES",
                "NOT FAIL",
                "NO FAILS",
            ]:
                sanitized = sanitized.replace(benign, "")
            for indicator in [
                "REJECT",
                "CHANGES REQUIRED",
                "NEEDS REVISION",
                "REQUEST CHANGES",
                "DISAPPROVED",
            ]:
                if indicator in sanitized:
                    return True
            if re.search(r"\b(FAIL|FAILED|FAILURE|FAILURES)\b", sanitized):
                return True
            return False

        check_rejected = is_rejected or default_is_rejected

        while True:
            if retries == 0:
                coder_task = f"Goal: {goal}\n\nExecution Plan:\n{plan}"
            else:
                coder_task = (
                    f"Goal: {goal}\n\n"
                    f"Previous Implementation:\n{last_code}\n\n"
                    f"Auditor Feedback / Revision Instructions:\n{current_feedback}\n\n"
                    f"Apply surgical fixes to address the auditor feedback."
                )

            code = coder.execute(coder_task)
            last_code = code

            # Context Scoping: original goal + coder raw output only
            auditor_task = f"User Goal:\n{goal}\n\nCoder Output:\n{code}"
            audit_result = auditor.execute(auditor_task)
            last_audit = audit_result

            if check_rejected(audit_result):
                retries += 1
                current_feedback = audit_result
                if retries >= max_retries:
                    raise Mode2DeadlockError(
                        f"Mode 2 verification loop exceeded max_retries={max_retries}: "
                        f"Auditor rejected Coder output {retries} times.\n"
                        f"Last Auditor Critique:\n{last_audit}"
                    )
            else:
                return {
                    "status": "approved",
                    "plan": plan,
                    "code": code,
                    "audit": audit_result,
                    "retries": retries,
                }

    execute_mode2 = run_mode2


def run_mode2_workflow(
    goal: str,
    planner: Optional[SubAgent] = None,
    coder: Optional[SubAgent] = None,
    auditor: Optional[SubAgent] = None,
    max_retries: int = 2,
    is_rejected: Optional[Callable[[str], bool]] = None,
) -> Dict[str, Any]:
    """Run the Mode 2 verification workflow with max_retries=2 deadlock prevention."""
    orchestrator = OrchestratorAgent()
    return orchestrator.run_mode2(
        goal=goal,
        planner=planner,
        coder=coder,
        auditor=auditor,
        max_retries=max_retries,
        is_rejected=is_rejected,
    )


# ---------------------------------------------------------------------------
# Team orchestration
# ---------------------------------------------------------------------------


def run_agent_team(
    task: str,
    agents: Optional[List[SubAgent]] = None,
    parallel: bool = False,
) -> Dict[str, str]:
    """Run multiple agents on the same task and return all results.

    Args:
        task: The task description.
        agents: List of ``SubAgent`` instances. Defaults to one of each
            of the 4 personas (Researcher, Coder, Auditor, Planner).
        parallel: If ``True``, run agents concurrently in threads.
            If ``False`` (default), run sequentially.

    Returns:
        Dict mapping ``persona -> result string``.
    """
    if agents is None:
        agents = [
            SubAgent("Researcher"),
            SubAgent("Coder"),
            SubAgent("Auditor"),
            SubAgent("Planner"),
        ]

    results: Dict[str, str] = {}

    if parallel:
        threads = []
        lock = threading.Lock()

        def _run_agent(agent: SubAgent):
            result = agent.execute(task)
            with lock:
                results[agent.persona] = result

        for agent in agents:
            t = threading.Thread(target=_run_agent, args=(agent,), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=120)
    else:
        for agent in agents:
            results[agent.persona] = agent.execute(task)

    return results


# ---------------------------------------------------------------------------
# Token router: intent dispatch to the model + Synthesizer consumer
# ---------------------------------------------------------------------------


def _detect_intent(text: str) -> str:
    """Classify user input into a routing bucket for the orchestrator.

    Heads-up: this is a deliberately simple keyword heuristic that exists
    for the purpose of exercising the new dispatch path. It is not a
    production classifier and should be swapped for a trained/semantic
    classifier before any shipping code relies on it.
    """
    upper = text.lower().strip()

    # Code / editing tasks -> Coder
    code_markers = ["write", "code", "implement", "fix", "bug", "refactor",
                    "edit", "patch", "program", "script", "compile", "test"]
    if any(m in upper for m in code_markers):
        return "code"

    # Research / search tasks -> Researcher
    research_markers = ["research", "search", "look up", "find out", "what is",
                        "why is", "who is", "explain", "summarize", "summarise"]
    if any(m in upper for m in research_markers):
        return "research"

    # Verification / review tasks -> Auditor
    audit_markers = ["review", "check", "audit", "verify", "test", "review",
                     "critique", "quality", "security"]
    if any(m in upper for m in audit_markers):
        return "audit"

    # Planning / architecture tasks -> Planner
    plan_markers = ["plan", "design", "architecture", "plan out",
                    "structural", "roadmap", "strategy"]
    if any(m in upper for m in plan_markers):
        return "plan"

    # Fallthrough: synthesize a direct answer
    return "synthesize"


def router(text: str, *, model: str = None) -> str:
    """Run the orchestrator: classify intent, dispatch to the model.

    Flow for a code task:
        user text -> _detect_intent -> "code" -> CoderAgent -> model
        result -> SynthesizerAgent -> unified response

    For other intents the router maps to the matching persona and runs the
    same dispatch, then hands the raw output to the Synthesizer to assemble
    a coherent final answer.
    """
    model = model or CONFIG.get("model_name", "")

    intent = _detect_intent(text)
    logger.info("router: intent=%s text=%r", intent, text)

    # 1) Persona dispatch (code -> Coder, research -> Researcher, ...)
    persona = {
        "code": "Coder",
        "research": "Researcher",
        "audit": "Auditor",
        "plan": "Planner",
        "synthesize": "Orchestrator",
    }.get(intent, "Synthesizer")

    # When the user just wants a synthesis, use the Orchestrator/Synthesizer.
    agent = SubAgent(persona=persona, model=model)
    raw = agent.execute(text)

    # 2) Synthesize the result into a coherent response (always, unless it
    #    is already a finished code/path output).
    if intent == "synthesize":
        return raw

    synths = SubAgent(persona="Synthesizer", model=model)
    synthesis = synths.execute(
        f"Source text: {text}\n\nRaw output from {persona}:\n{raw}"
    )

    return synthesis if synthesis else raw
