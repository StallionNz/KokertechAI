import os

PLUGIN_METADATA = {
    "name": "Extract Image Text",
    "description": "Performs OCR on images using Tesseract to extract readable text",
    "version": "1.0.0",
    "tags": ["ocr", "image", "text", "tesseract"],
    "author": "KokertechAI",
    "requires": ["Pillow", "pytesseract"],
    "permissions": ['ai']
}

COMMAND_NAME = "EXTRACT_IMAGE_TEXT"
SCHEMA = {
    "action": "EXTRACT_IMAGE_TEXT",
    "path": "<relative or absolute path to the image file>"
}
WORKSPACE_DIR = r"C:\KokertechAI"
CHAR_LIMIT = 3000

def execute(intent_json):
    raw_path = intent_json.get("path")
    if not raw_path:
        return "❌ Missing 'path' parameter."
        
    # Strip leading slashes and enforce absolute boundary
    full_path = os.path.abspath(os.path.join(WORKSPACE_DIR, raw_path.lstrip("\\/")))
    
    # Critical Security Gate
    if not full_path.startswith(WORKSPACE_DIR):
        return "❌ Security Exception: Target path is outside the strictly enforced workspace."
        
    if not os.path.exists(full_path):
        return f"❌ Image file not found: {full_path}"
        
    try:
        from PIL import Image
        import pytesseract
        
        # Open the image and extract text
        img = Image.open(full_path)
        text = pytesseract.image_to_string(img).strip()
        
        if not text:
            return f"⚠️ OCR Complete, but no readable text was found in {full_path}."
            
        # Hard-cap the output to protect the LLM context window
        if len(text) > CHAR_LIMIT:
            text = text[:CHAR_LIMIT] + "\n...[TRUNCATED TO PROTECT VRAM]"
            
        return f"✅ Extracted Text from {full_path}:\n\n{text}"
        
    except ImportError:
        return "❌ Python dependencies missing. Run: pip install Pillow pytesseract"
    except Exception as e:
        err_msg = str(e)
        # Specifically catch the classic Windows Tesseract binary error
        if "tesseract is not installed" in err_msg or "tesseract_cmd" in err_msg:
            return (
                "❌ OS Dependency Missing: The Tesseract-OCR Windows executable is not installed or not in your system PATH. "
                "Download it from: https://github.com/UB-Mannheim/tesseract/wiki"
            )
        return f"❌ OCR Extraction failed: {err_msg}"