import os

PLUGIN_METADATA = {
    "name": "Delete File",
    "description": "Deletes a file or directory within the workspace (with path traversal protection)",
    "version": "1.0.0",
    "tags": ["filesystem", "workspace", "delete"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['fs_write']
}

COMMAND_NAME = "DELETE_FILE"
SCHEMA = {
    "action": "DELETE_FILE",
    "path": "<relative or absolute path to delete>"
}
WORKSPACE_DIR = r"C:\KokertechAI"

def execute(intent_json):
    raw_path = intent_json.get("path")
    if not raw_path: 
        return "❌ Missing 'path' parameter."
    
    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, raw_path.lstrip("\\/")))
    
    # Security Gate: Prevent traversing out of workspace
    if not full_path.startswith(WORKSPACE_DIR): 
        return "❌ Security Exception: Target path is outside the strictly enforced workspace."
        
    if not os.path.exists(full_path):
        return f"❌ File not found: {full_path}"
        
    try:
        if os.path.isdir(full_path):
            import shutil
            shutil.rmtree(full_path)
            return f"✅ Successfully deleted directory and contents: {full_path}"
        else:
            os.remove(full_path)
            return f"✅ Successfully deleted file: {full_path}"
    except Exception as e:
        return f"❌ Delete error: {str(e)}"