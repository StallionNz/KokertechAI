# plugins/extract_audio.py
import os

PLUGIN_METADATA = {
    "name": "Extract Audio",
    "description": "Transcribes audio files to text using Whisper (tiny model for low VRAM)",
    "version": "1.0.0",
    "tags": ["audio", "transcription", "whisper", "media"],
    "author": "KokertechAI",
    "requires": ["openai-whisper"],
    "permissions": ['shell']
}

COMMAND_NAME = "EXTRACT_AUDIO"
SCHEMA = {
    "action": "EXTRACT_AUDIO",
    "path": "<relative or absolute path to audio file>"
}
WORKSPACE_DIR = r"C:\KokertechAI"
CHAR_LIMIT = 4000

def execute(intent_json):
    raw_path = intent_json.get("path")
    if not raw_path:
        return "❌ Missing 'path' parameter."
        
    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, raw_path.lstrip("\\/")))
    
    # Security Gate
    if not full_path.startswith(WORKSPACE_DIR):
        return "❌ Security Exception: Target path is outside the strictly enforced workspace."
        
    if not os.path.exists(full_path):
        return f"❌ Audio file not found: {full_path}"
        
    try:
        import whisper
        import warnings
        
        # Suppress FP16 warnings on CPU execution
        warnings.filterwarnings("ignore")
        
        # Force the 'tiny' model to protect the 2048MB Quadro VRAM limit
        model = whisper.load_model("tiny")
        result = model.transcribe(full_path, fp16=False)
        text = result["text"].strip()
        
        if not text:
            return f"⚠️ Transcription Complete, but no speech was detected in {full_path}."
            
        if len(text) > CHAR_LIMIT:
            text = text[:CHAR_LIMIT] + "\n...[TRUNCATED TO PROTECT VRAM]"
            
        return f"✅ Extracted Audio Transcript from {full_path}:\n\n{text}"
        
    except ImportError:
        return "❌ Python dependencies missing. Run: pip install openai-whisper"
    except Exception as e:
        return f"❌ Audio Extraction failed: {str(e)}"