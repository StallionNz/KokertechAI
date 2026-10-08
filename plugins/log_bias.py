import memory_vault

PLUGIN_METADATA = {
    "name": "Log Bias",
    "description": "Records a detected cognitive bias into the SCBE (Self-Consistent Bias Engine) ledger",
    "version": "1.0.0",
    "tags": ["cognitive", "bias", "auditor", "scbe"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['memory']
}

COMMAND_NAME = "LOG_BIAS"
SCHEMA = {
    "action": "LOG_BIAS",
    "agent_id": "String (e.g., Executive)",
    "bias_type": "String (e.g., regional empathy, frustration, logic bias)",
    "confidence_score": "Number (0-100)",
    "description": "String"
}

def execute(intent_json):
    agent_id = intent_json.get("agent_id", "Executive")
    bias_type = intent_json.get("bias_type", "Unknown Bias")
    score = intent_json.get("confidence_score", 50)
    desc = intent_json.get("description", "")
    
    try:
        memory_vault.log_bias(agent_id, bias_type, score, desc)
        return f"✅ SCBE Updated: Recorded '{bias_type}' bias drift into the ledger."
    except Exception as e:
        return f"❌ SCBE Logging Error: {e}"