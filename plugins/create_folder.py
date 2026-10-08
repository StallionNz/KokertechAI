import os

PLUGIN_METADATA = {
    "name": "Create Folder",
    "description": "Creates a new folder/directory within the workspace boundary",
    "version": "1.0.0",
    "tags": ["filesystem", "workspace", "directory"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['fs_write']
}

COMMAND_NAME = "CREATE_FOLDER"
SCHEMA = {
    "action": "CREATE_FOLDER",
    "path": "<folder name or relative path>"
}
WORKSPACE_DIR = r"C:\KokertechAI"

def execute(intent_json):
    target = intent_json.get("path")
    if not target: 
        return "❌ Missing 'path' parameter."
    
    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, target.lstrip("\\/")))
    
    if not full_path.startswith(WORKSPACE_DIR):
        return "❌ Security Exception: Path traversal attempt blocked."
        
    if os.path.exists(full_path):
        return f"⚠️ Folder already exists: {full_path}"
        
    try:
        os.makedirs(full_path, exist_ok=True)
        return f"✅ Created folder: {full_path}"
    except Exception as e:
        return f"❌ Create folder error: {str(e)}"