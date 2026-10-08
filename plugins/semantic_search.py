import memory_vault

PLUGIN_METADATA = {
    "name": "Semantic Search",
    "description": "Searches the vector memory vault for semantically similar memories to a query",
    "version": "1.0.0",
    "tags": ["memory", "search", "vector", "semantic", "vault"],
    "author": "KokertechAI",
    "requires": ["sentence-transformers"],
    "permissions": ['memory']
}

COMMAND_NAME = "SEMANTIC_SEARCH"
SCHEMA = {
    "action": "SEMANTIC_SEARCH",
    "query": "<search term or question>",
    "top_k": "<number of results to return, integer>"
}

def execute(intent_json):
    query = intent_json.get("query")
    top_k = intent_json.get("top_k", 3)

    if not query:
        return "❌ Missing 'query' parameter."

    try:
        results = memory_vault.semantic_search(query, top_k=int(top_k))
        if not results:
            return f"🔍 No relevant memories found for: '{query}'"

        output = [f"🔍 Semantic Search Results for '{query}':"]
        for node_id, node_type, content, score in results:
            # Displays the vector match score so the AI knows how accurate the recall is
            output.append(f"[Node ID: {node_id} | Type: {node_type} | Match: {score:.2f}]\n{content}\n")

        return "\n".join(output)
    except Exception as e:
        return f"❌ Semantic search failed: {str(e)}"