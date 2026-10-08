import os
import memory_vault

PLUGIN_METADATA = {
    "name": "Index Document",
    "description": "Chunks and indexes text documents into the vector memory vault for semantic search",
    "version": "1.0.0",
    "tags": ["memory", "indexing", "vector", "document", "chunking"],
    "author": "KokertechAI",
    "requires": ["sentence-transformers"],
    "permissions": ['fs_read', 'memory']
}

COMMAND_NAME = "INDEX_DOCUMENT"
SCHEMA = {
    "action": "INDEX_DOCUMENT",
    "path": "<relative or absolute path to text document>",
    "chunk_size": "<integer character limit per chunk>"
}
WORKSPACE_DIR = r"C:\KokertechAI"

def execute(intent_json):
    raw_path = intent_json.get("path")
    chunk_size = intent_json.get("chunk_size", 1000)

    if not raw_path:
        return "❌ Missing 'path' parameter."

    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, raw_path.lstrip("\\/")))

    if not full_path.startswith(WORKSPACE_DIR):
        return "❌ Security Exception: Target path is outside the workspace."

    if not os.path.exists(full_path):
        return f"❌ File not found: {full_path}"

    try:
        chunk_size = int(chunk_size)
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()

        if not content.strip():
            return "⚠️ File is empty."

        # Aggressive chunking loop to split massive documents into digestible vector nodes
        chunks = [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
        first_node_id = None

        for i, chunk in enumerate(chunks):
            node_id = memory_vault.store_memory(
                f"Excerpt {i+1}/{len(chunks)} from {os.path.basename(full_path)}:\n{chunk}",
                node_type='document_chunk',
                importance=5
            )
            if i == 0:
                first_node_id = node_id

        return f"✅ Document indexed into {len(chunks)} vector chunks. Starting Node ID: {first_node_id}"

    except UnicodeDecodeError:
        return "❌ Cannot index binary or non-UTF-8 files directly. Use READ_PDF or EXTRACT_AUDIO."
    except Exception as e:
        return f"❌ Document indexing failed: {str(e)}"