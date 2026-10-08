# KokertechAI Configuration Example

def get_system_config():
    return {
        "workspace": r"C:\KokertechAI",
        "model_name": "gemma-4-E2B-it-Q4_K_M.gguf",
        "active_provider": "local_llm",
        "plugins_enabled": True,
        "auto_index_documents": True,
        "vram_limit_mb": 2048,
    }

# Load and return configuration
def load_settings(path):
    import json
    with open(path, 'r') as f:
        return json.load(f)
