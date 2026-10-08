"""
crew_integration.py — CrewAI multi-agent orchestration for KokertechAI.

Sprint 8: CrewOrchestrator dispatches to CrewAI when available, or falls
back to sequential SubAgent execution.
"""

try:
    import crewai as _crewai_mod
except ImportError:
    _crewai_mod = None

try:
    import langchain_openai as _langchain_mod
except ImportError:
    _langchain_mod = None

from logging_config import get_logger

logger = get_logger(name="MemoryVault")

# ── Persona definitions ──────────────────────────────────────────
# Maps persona name to (role, goal, backstory) for CrewAI Agent creation.
# Fallback uses SubAgent which reads its own config from sub_agents.py.
_DEFAULT_PERSONAS = ["Researcher", "Coder", "Auditor", "Planner"]

_PERSONA_ROLES = {
    "Researcher": {
        "role": "Research Specialist",
        "goal": "Search, analyze, and synthesize information from multiple sources",
        "backstory": "Expert at finding and connecting information across domains.",
    },
    "Coder": {
        "role": "Code Engineer",
        "goal": "Write clean, tested, maintainable Python code",
        "backstory": "Senior engineer with deep Python expertise.",
    },
    "Auditor": {
        "role": "Code Auditor",
        "goal": "Find bugs, security issues, and performance problems",
        "backstory": "Security-focused code reviewer with a keen eye for edge cases.",
    },
    "Planner": {
        "role": "Planning Specialist",
        "goal": "Break down complex tasks into clear, actionable steps",
        "backstory": "Strategic thinker who excels at task decomposition.",
    },
}

# ── Module-level functions ──────────────────────────────────────


def is_crewai_available():
    """Return True if both crewai and langchain_openai are importable."""
    return _crewai_mod is not None and _langchain_mod is not None


def _get_llm():
    """Return a ChatOpenAI instance, or None if langchain_openai is unavailable."""
    if _langchain_mod is None:
        return None
    try:
        return _langchain_mod.ChatOpenAI(
            model="gpt-4",
            temperature=0.2,
            max_tokens=2000,
        )
    except Exception:
        return None


def _resolve_personas(personas):
    """Return personas list, falling back to defaults if None."""
    return personas if personas is not None else _DEFAULT_PERSONAS[:]


# ── CrewOrchestrator ────────────────────────────────────────────


class CrewOrchestrator:
    """Orchestrates multi-agent execution via CrewAI or fallback."""

    def __init__(self):
        self._available = is_crewai_available()

    def run_team(self, task, personas=None, process="sequential"):
        """Run a team of agents on the given task.

        Args:
            task: The task description string.
            personas: List of persona names (e.g. ["Researcher", "Coder"]).
                      Defaults to all four personas if None.
            process: "sequential" or "hierarchical" (CrewAI path only).

        Returns:
            Result string from the execution.
        """
        resolved = _resolve_personas(personas)
        if self._available:
            return self._run_crewai(task, resolved, process)
        else:
            return self._run_fallback(task, resolved)

    # ── CrewAI path ──────────────────────────────────────────────

    def _run_crewai(self, task, personas, process):
        """Execute via CrewAI agents, tasks, and crew."""
        if not personas:
            return "No valid personas"
        llm = _get_llm()
        if llm is None:
            return self._run_fallback(task, personas)
        try:
            import crewai

            agents = []
            tasks = []
            for persona in personas:
                role_info = _PERSONA_ROLES.get(persona)
                if role_info is None:
                    # Unknown persona — skip gracefully
                    continue
                agent = crewai.Agent(
                    role=persona,
                    goal=role_info["goal"],
                    backstory=role_info["backstory"],
                    allow_delegation=False,
                    llm=llm,
                )
                agents.append(agent)
                t = crewai.Task(
                    description=f"{task} (as {persona})",
                    agent=agent,
                )
                tasks.append(t)

            if not agents:
                return "No valid personas"

            process_value = (
                crewai.Process.hierarchical
                if process == "hierarchical"
                else crewai.Process.sequential
            )
            crew = crewai.Crew(
                agents=agents,
                tasks=tasks,
                process=process_value,
                verbose=True,
            )
            result = crew.kickoff()
            return str(result)
        except Exception:
            logger.debug("CrewAI execution failed, falling back to SubAgent")
            return self._run_fallback(task, personas)

    # ── SubAgent fallback path ───────────────────────────────────

    def _run_fallback(self, task, personas):
        """Execute personas sequentially using SubAgent."""
        if not personas:
            return "No agents executed"
        parts = []
        for persona in personas:
            role_info = _PERSONA_ROLES.get(persona)
            if role_info is None:
                # Unknown persona — skip
                continue
            try:
                from sub_agents import SubAgent

                agent = SubAgent(persona_name=persona)
                output = agent.execute(task)
                parts.append(f"--- {persona} ---\n{output}")
            except Exception:
                continue

        if not parts:
            return "No agents executed"
        return "\n\n".join(parts)


# ── Convenience function ────────────────────────────────────────


def run_crew_team(task, personas=None, process="sequential"):
    """Convenience function: create an orchestrator and run the team."""
    orchestrator = CrewOrchestrator()
    return orchestrator.run_team(task, personas, process)
