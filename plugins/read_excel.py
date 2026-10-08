import os

PLUGIN_METADATA = {
    "name": "Read Excel",
    "description": "Parses .xlsx or .csv spreadsheets into markdown tables using pandas",
    "version": "1.0.0",
    "tags": ["spreadsheet", "excel", "csv", "data", "pandas"],
    "author": "KokertechAI",
    "requires": ["pandas", "openpyxl", "tabulate"],
    "permissions": ['fs_read']
}

COMMAND_NAME = "READ_EXCEL"
SCHEMA = {
    "action": "READ_EXCEL",
    "path": "<relative or absolute path to .xlsx or .csv>"
}
WORKSPACE_DIR = r"C:\KokertechAI"
CHAR_LIMIT = 4000

def execute(intent_json):
    raw_path = intent_json.get("path")
    if not raw_path:
        return "❌ Missing 'path' parameter."

    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, raw_path.lstrip("\\/")))

    if not full_path.startswith(WORKSPACE_DIR):
        return "❌ Security Exception: Target path is outside the workspace."

    if not os.path.exists(full_path):
        return f"❌ File not found: {full_path}"

    try:
        import pandas as pd
    except ImportError:
        return "❌ 'pandas' is not installed. Run: pip install pandas openpyxl"

    try:
        if full_path.endswith('.csv'):
            df = pd.read_csv(full_path)
        else:
            df = pd.read_excel(full_path)
            
        # Convert to markdown for highly readable LLM ingestion
        text_data = df.to_markdown(index=False)
        
        if len(text_data) > CHAR_LIMIT:
            text_data = text_data[:CHAR_LIMIT] + "\n...[TRUNCATED TO PROTECT VRAM]"
            
        return f"✅ Parsed Spreadsheet Data ({full_path}):\n\n{text_data}"
    except Exception as e:
        return f"❌ Spreadsheet parse error: {str(e)}"