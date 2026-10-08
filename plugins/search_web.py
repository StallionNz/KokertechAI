# Create a folder called 'plugins' in C:\KokertechAI\
# Save this file as C:\KokertechAI\plugins\search_web.py

import requests
from bs4 import BeautifulSoup

PLUGIN_METADATA = {
    "name": "Search Web",
    "description": "Searches the web via DuckDuckGo and returns relevant snippet results",
    "version": "1.0.0",
    "tags": ["web", "search", "duckduckgo", "internet"],
    "author": "KokertechAI",
    "requires": ["requests", "beautifulsoup4"],
    "permissions": ['network']
}

COMMAND_NAME = "SEARCH_WEB"
SCHEMA = {
    "action": "SEARCH_WEB",
    "query": "<search term here>"
}

def execute(intent_json):
    query = intent_json.get("query")
    if not query:
        return "❌ Missing 'query' for SEARCH_WEB."
        
    try:
        url = f"https://html.duckduckgo.com/html/?q={query}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        results = [a.text for a in soup.find_all('a', class_='result__snippet')]
        return "\n".join(results[:3]) if results else "No clear results found."
    except Exception as e:
        return f"❌ Search failed: {str(e)}"