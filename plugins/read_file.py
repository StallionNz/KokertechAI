import os

PLUGIN_METADATA = {
    "name": "Read File",
    "description": "Reads the contents of a text file within the workspace (with VRAM protection truncation)",
    "version": "1.0.0",
    "tags": ["filesystem", "workspace", "read", "text"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['fs_read']
}

COMMAND_NAME = "READ_FILE"
SCHEMA = {
    "action": "READ_FILE",
    "path": "<relative or absolute path>"
}
WORKSPACE_DIR = r"C:\KokertechAI"
CHAR_LIMIT = 3000

def execute(intent_json):
    raw_path = intent_json.get("path") or intent_json.get("filename")
    if not raw_path: 
        return "❌ Missing 'path' parameter."
    
    # Strip leading slashes to prevent absolute path override hacks
    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, raw_path.lstrip("\\/")))
    
    # Critical Security Gate: Prevent path traversal (e.g., ../../../Windows/System32)
    if not full_path.startswith(WORKSPACE_DIR): 
        return "❌ Security Exception: Target path is outside the strictly enforced workspace."
        
    if not os.path.exists(full_path): 
        return f"❌ File not found: {full_path}"
    
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Hard-cap the output so we don't blow the LLM's context window
        if len(content) > CHAR_LIMIT:
            return f"✅ Read {full_path}:\n{content[:CHAR_LIMIT]}\n...[TRUNCATED TO PROTECT VRAM]"
            
        return f"✅ Read {full_path}:\n{content}"
    except UnicodeDecodeError:
        return f"❌ Cannot read binary or non-UTF-8 file: {full_path}"
    except Exception as e:
        return f"❌ Read error: {str(e)}"