import os

PLUGIN_METADATA = {
    "name": "Read PDF",
    "description": "Extracts text from PDF files (up to 10 pages to protect VRAM)",
    "version": "1.0.0",
    "tags": ["document", "pdf", "text", "extraction"],
    "author": "KokertechAI",
    "requires": ["pypdf"],
    "permissions": ['fs_read']
}

COMMAND_NAME = "READ_PDF"
SCHEMA = {
    "action": "READ_PDF",
    "path": "<absolute or relative path to PDF>"
}
WORKSPACE_DIR = r"C:\KokertechAI"

def execute(intent_json):
    try:
        import pypdf
    except ImportError:
        return "❌ pypdf not installed. Run: pip install pypdf"

    raw_path = intent_json.get("path")
    if not raw_path: return "❌ Missing 'path'."
    
    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, raw_path.lstrip("\\/")))
    
    if not os.path.exists(full_path):
        return f"❌ File not found: {full_path}"
        
    try:
        text = ""
        with open(full_path, "rb") as f:
            reader = pypdf.PdfReader(f)
            # Limit to first 10 pages to prevent instant VRAM overflow
            for page in reader.pages[:10]: 
                text += page.extract_text() + "\n"
        
        if len(text) > 3000:
            text = text[:3000] + "\n...[TRUNCATED TO PROTECT VRAM]"
            
        return f"✅ Read PDF ({full_path}):\n{text}"
    except Exception as e:
        return f"❌ PDF read error: {str(e)}"