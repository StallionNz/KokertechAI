import memory_vault

PLUGIN_METADATA = {
    "name": "Log Growth",
    "description": "Records an energy shift or growth event in the agent's growth arc timeline",
    "version": "1.0.0",
    "tags": ["cognitive", "growth", "auditor", "arc"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['memory']
}

COMMAND_NAME = "LOG_GROWTH"
SCHEMA = {
    "action": "LOG_GROWTH",
    "agent_id": "String (e.g., Executive)",
    "event_description": "String (Description of realization or shift in mindset)",
    "energy_shift": "Number (e.g., 12.5 or -5.0)"
}

def execute(intent_json):
    agent_id = intent_json.get("agent_id", "Executive")
    desc = intent_json.get("event_description", "Unknown growth event")
    shift = intent_json.get("energy_shift", 0.0)
    
    try:
        memory_vault.log_growth(agent_id, desc, shift)
        return f"✅ Growth Arc Updated: Recorded energy shift of {shift}%."
    except Exception as e:
        return f"❌ Growth Logging Error: {e}"