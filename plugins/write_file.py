import os

PLUGIN_METADATA = {
    "name": "Write File",
    "description": "Writes content to a file within the workspace (with path traversal protection)",
    "version": "1.0.0",
    "tags": ["filesystem", "workspace", "write", "create"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['fs_write']
}

COMMAND_NAME = "WRITE_FILE"
SCHEMA = {
    "action": "WRITE_FILE",
    "path": "<relative or absolute path>",
    "content": "<exact text or code to write>"
}
WORKSPACE_DIR = r"C:\KokertechAI"

def execute(intent_json):
    raw_path = intent_json.get("path") or intent_json.get("filename")
    content = intent_json.get("content", "")
    
    if not raw_path: 
        return "❌ Missing 'path' parameter."
    if not content:
        return "⚠️ Warning: 'content' parameter is empty. Writing a blank file."
    
    # Strip leading slashes to prevent absolute path override hacks
    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, raw_path.lstrip("\\/")))
    
    # Critical Security Gate: Prevent path traversal overwrites
    if not full_path.startswith(WORKSPACE_DIR): 
        return "❌ Security Exception: Target path is outside the strictly enforced workspace."
        
    try:
        # Automatically create parent directories if they don't exist
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
            
        return f"✅ Successfully wrote to {full_path}"
    except Exception as e:
        return f"❌ Write error: {str(e)}"