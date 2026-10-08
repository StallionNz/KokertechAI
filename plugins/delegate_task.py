# plugins/delegate_task.py
from logging_config import get_logger

logger = get_logger(name="DelegateTask")

PLUGIN_METADATA = {
    "name": "Delegate Task",
    "description": "Spawns a sub-agent with a specific persona (Researcher, CodeGen, QA, Auditor) to execute tasks independently",
    "version": "2.0.0",
    "tags": ["agents", "sub-agent", "delegation", "parallel", "crewai"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['ai']
}

COMMAND_NAME = "DELEGATE_TASK"
SCHEMA = {
    "action": "DELEGATE_TASK",
    "task": "<detailed instructions for the sub-agent>",
    "persona": "<Researcher|CodeGen|QA|Auditor>",
    "mode": "<agent|crew>"
}
def execute(intent_json):
    task = intent_json.get("task")
    persona = intent_json.get("persona", "Researcher")
    mode = intent_json.get("mode", "agent")
    
    if not task:
        return "ERROR: Missing 'task' parameter."
    
    # Crew mode: use CrewAI orchestration with all agents
    if mode == "crew":
        try:
            from crew_integration import run_crew_team
            result = run_crew_team(task, process="sequential")
            return f"Crew Task Complete:\n\n{result.strip()}"
        except Exception as e:
            return f"Crew delegation failed: {e}"
    
    # Agent mode: use single SubAgent (original behavior)
    try:
        from sub_agents import SubAgent
        agent = SubAgent(persona=persona)
        result = agent.execute(task, timeout=120)
        return f"Sub-Agent [{persona}] Task Complete:\n\n{result.strip()}"
    except Exception as e:
        return f"Sub-Agent delegation failed: {str(e)}"