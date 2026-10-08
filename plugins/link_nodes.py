import memory_vault

PLUGIN_METADATA = {
    "name": "Link Nodes",
    "description": "Creates a typed relationship between two memory nodes in the knowledge graph",
    "version": "1.0.0",
    "tags": ["knowledge-graph", "memory", "relationships"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['memory']
}

COMMAND_NAME = "LINK_NODES"
SCHEMA = {
    "action": "LINK_NODES",
    "source_id": "<integer ID of the first node>",
    "target_id": "<integer ID of the second node>",
    "relationship_type": "<e.g., DEPENDS_ON, CONTRADICTS, EXPANDS_ON>"
}

def execute(intent_json):
    src = intent_json.get("source_id")
    tgt = intent_json.get("target_id")
    rel = intent_json.get("relationship_type", "RELATES_TO")

    if not src or not tgt:
        return "❌ Missing 'source_id' or 'target_id'."

    try:
        memory_vault.link_memories(int(src), int(tgt), relationship_type=rel)
        return f"✅ Knowledge Graph updated: Node {src} --[{rel}]--> Node {tgt}"
    except Exception as e:
        return f"❌ Failed to link nodes: {str(e)}"