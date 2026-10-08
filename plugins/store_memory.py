import json

PLUGIN_METADATA = {
    "name": "Store Memory",
    "description": "Manually stores a fact or memory into the long-term vector vault",
    "version": "1.0.0",
    "tags": ["memory", "vault", "storage", "fact"],
    "author": "KokertechAI",
    "requires": ["sentence-transformers"],
    "permissions": ['memory']
}

COMMAND_NAME = "STORE_MEMORY"
SCHEMA = {
    "action": "STORE_MEMORY",
    "content": "<the fact, summary, or interaction to remember>",
    "importance": "<integer 1-10>"
}

def execute(intent_json):
    content = intent_json.get("content")
    importance = intent_json.get("importance", 5)
    
    if not content:
        return "❌ Missing 'content' parameter for memory storage."
        
    # TYPE-SAFETY FIX: Force nested dictionaries or lists into a flat string
    if not isinstance(content, str):
        content = json.dumps(content)
        
    try:
        # Dynamic import ensures this only loads if the tool is actually called
        import memory_vault
        
        # Defaulting node_type to 'fact' for manual storage operations
        memory_id = memory_vault.store_memory(content, node_type='fact', importance=int(importance))
        
        return f"✅ Memory successfully committed to Long-Term Vault (Node ID: {memory_id})."
    except Exception as e:
        return f"❌ Vault storage error: {str(e)}"