PLUGIN_METADATA = {
    "name": "Fetch Web",
    "description": "Fetches and extracts readable text from a URL, stripping HTML/CSS/JS",
    "version": "1.0.0",
    "tags": ["web", "scrape", "html", "content"],
    "author": "KokertechAI",
    "requires": ["requests", "beautifulsoup4"],
    "permissions": ['network']
}

COMMAND_NAME = "FETCH_WEB"
SCHEMA = {
    "action": "FETCH_WEB",
    "url": "<exact HTTP/HTTPS url to read>"
}
CHAR_LIMIT = 4000

def execute(intent_json):
    url = intent_json.get("url")
    if not url:
        return "❌ Missing 'url' parameter."
        
    if not url.startswith("http"):
        url = "https://" + url
        
    try:
        import requests
        from bs4 import BeautifulSoup
        
        # Standard header to prevent basic anti-bot blocks
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()
        
        # Strip all HTML tags and scripts, keep only the visible text
        soup = BeautifulSoup(res.text, 'html.parser')
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.extract()
            
        text = soup.get_text(separator='\n')
        # Collapse multiple blank lines into one
        import re
        text = re.sub(r'\n\s*\n', '\n\n', text).strip()
        
        if len(text) > CHAR_LIMIT:
            text = text[:CHAR_LIMIT] + "\n...[TRUNCATED TO PROTECT VRAM]"
            
        return f"✅ Fetched data from {url}:\n\n{text}"
    except Exception as e:
        return f"❌ Web fetch failed: {str(e)}"