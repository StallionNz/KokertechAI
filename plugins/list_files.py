import os

PLUGIN_METADATA = {
    "name": "List Files",
    "description": "Lists all files and directories in a workspace path with type indicators",
    "version": "1.0.0",
    "tags": ["filesystem", "workspace", "directory", "listing"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['fs_read']
}

COMMAND_NAME = "LIST_FILES"
SCHEMA = {
    "action": "LIST_FILES",
    "path": "<optional subfolder path, default is root>"
}
WORKSPACE_DIR = r"C:\KokertechAI"

def execute(intent_json):
    sub_path = intent_json.get("path", "")
    target_dir = os.path.abspath(os.path.join(WORKSPACE_DIR, sub_path.lstrip("\\/")))
    
    if not target_dir.startswith(WORKSPACE_DIR):
        return "❌ Security Exception: Path traversal attempt blocked."
        
    if not os.path.exists(target_dir) or not os.path.isdir(target_dir):
        return f"❌ Directory not found: {target_dir}"
        
    try:
        items = os.listdir(target_dir)
        if not items: 
            return f"📂 Directory {target_dir} is empty."
        
        details = []
        for item in items:
            item_path = os.path.join(target_dir, item)
            item_type = "DIR" if os.path.isdir(item_path) else "FILE"
            details.append(f"[{item_type}] {item}")
            
        return f"✅ Contents of {target_dir}:\n" + "\n".join(details)
    except Exception as e:
        return f"❌ Directory list error: {str(e)}"