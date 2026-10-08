import os

PLUGIN_METADATA = {
    "name": "Rename File",
    "description": "Renames or moves a file/folder within the workspace",
    "version": "1.0.0",
    "tags": ["filesystem", "workspace", "rename", "move"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['fs_write']
}

COMMAND_NAME = "RENAME_FILE"
SCHEMA = {
    "action": "RENAME_FILE",
    "source": "<current file/folder path>",
    "destination": "<new file/folder name or path>"
}
WORKSPACE_DIR = r"C:\KokertechAI"

def execute(intent_json):
    src = intent_json.get("source")
    dst = intent_json.get("destination")
    
    if not src or not dst:
        return "❌ Missing 'source' or 'destination' parameters."
        
    full_src = os.path.abspath(os.path.join(WORKSPACE_DIR, src.lstrip("\\/")))
    full_dst = os.path.abspath(os.path.join(WORKSPACE_DIR, dst.lstrip("\\/")))
    
    # Security Gate: Both paths must be inside the workspace
    if not full_src.startswith(WORKSPACE_DIR) or not full_dst.startswith(WORKSPACE_DIR):
        return "❌ Security Exception: Paths must be within the workspace boundary."
        
    if not os.path.exists(full_src):
        return f"❌ Source not found: {full_src}"
        
    try:
        os.makedirs(os.path.dirname(full_dst), exist_ok=True)
        os.rename(full_src, full_dst)
        return f"✅ Successfully renamed to {full_dst}"
    except Exception as e:
        return f"❌ Rename error: {str(e)}"