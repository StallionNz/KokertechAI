import os
import json
import logging
import requests
from bs4 import BeautifulSoup
from ai_base import get_provider

PLUGIN_METADATA = {
    "name": "Research Topic",
    "description": "Scrapes web search results and synthesizes a structured briefing on any topic",
    "version": "1.0.0",
    "tags": ["research", "web", "synthesis", "briefing"],
    "author": "KokertechAI",
    "requires": ["requests", "beautifulsoup4"],
    "permissions": ['ai', 'network']
}

COMMAND_NAME = "RESEARCH_TOPIC"
SCHEMA = {
    "action": "RESEARCH_TOPIC",
    "topic": "<the specific topic to research>"
}
WORKSPACE_DIR = r"C:\KokertechAI"
SETTINGS_PATH = os.path.join(WORKSPACE_DIR, "app_settings.json")

def execute(intent_json):
    topic = intent_json.get("topic")
    if not topic:
        return "❌ Missing 'topic' parameter."

    # Step 1: Scrape live context to ground the LLM
    search_text = ""
    try:
        url = f"https://html.duckduckgo.com/html/?q={topic}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        results = [a.text for a in soup.find_all('a', class_='result__snippet')]
        search_text = "\n".join(results[:5])
    except Exception as e:
        search_text = f"Live search unavailable ({e})."

    # Step 2: Spin up a sub-routine to synthesize the report
    model_name = ""

    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                model_name = cfg.get("model_name", model_name)
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to load settings for research_topic: {e}")

    system_prompt = (
        "You are a KokertechAI Research Synthesizer. Compile a comprehensive, structured briefing on the requested topic. "
        "Use the provided live search data to ground your facts, supplemented by your internal weights. "
        "Format with clear headings and bullet points. Output ONLY the report text."
    )

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"TOPIC: {topic}\n\nLIVE SEARCH DATA:\n{search_text}"}
        ],
        "temperature": 0.3,
        "max_tokens": 2000
    }

    try:
        provider = get_provider(name="local_llm", default_model=model_name)
        result = provider.chat_completion(
            messages=payload["messages"],
            model=model_name,
            temperature=payload["temperature"],
            max_tokens=payload["max_tokens"],
            timeout=120,
        )
        if result.get("error"):
            return f"❌ Research synthesis failed: {result['error']}"
        report = result["content"].strip()
        return f"✅ Research Briefing on '{topic}':\n\n{report}"
    except Exception as e:
        return f"❌ Research synthesis failed: {str(e)}"