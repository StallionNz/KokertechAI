"""
model_registry.py — Model listing, testing, and benchmarking for local GGUF models.
All model operations use the local LLM provider (llama-cpp-python).
"""

import time
import threading

from logging_config import get_logger
from ai_base import get_provider, LocalLLMProvider


logger = get_logger(name="ModelRegistry")

MODELS_DIR = "models"


def list_models(provider_name: str = None):
    """List local GGUF models by scanning the models directory.

    Returns list of dicts: [{"name", "path", "size_bytes", "provider"}, ...]
    """
    models = LocalLLMProvider.scan_models(MODELS_DIR)
    for m in models:
        m["provider"] = "local_llm"
    return models


def test_model(provider_name: str, model_name: str, timeout: int = 30):
    """Test a model by sending a simple prompt.

    Returns {"ok": bool, "response": str, "error": str or None,
             "response_time_ms": int}
    """
    start = time.time()
    try:
        provider = get_provider()
        resp = provider.chat_completion(
            messages=[{"role": "user", "content": "Hello, respond with OK."}],
            model=model_name,
            timeout=timeout,
        )
        response_time_ms = int((time.time() - start) * 1000)
        if resp.get("error"):
            return {"ok": False, "response": "", "error": resp["error"],
                    "response_time_ms": response_time_ms}
        return {"ok": True, "response": resp.get("content", ""), "error": None,
                "response_time_ms": response_time_ms}
    except Exception as e:
        return {"ok": False, "response": "", "error": str(e),
                "response_time_ms": 0}


def benchmark_model(provider_name: str, model_name: str, timeout: int = 60):
    """Benchmark a model for token generation speed.

    Returns {"ok": bool, "token_count": int, "tokens_per_second": float,
             "response_time_ms": int, "error": str or None}
    """
    start = time.time()
    try:
        provider = get_provider()
        resp = provider.chat_completion(
            messages=[{"role": "user", "content": "Write ten short sentences about AI."}],
            model=model_name,
            timeout=timeout,
        )
        elapsed = time.time() - start
        response_time_ms = int(elapsed * 1000)

        if resp.get("error"):
            return {"ok": False, "token_count": 0, "tokens_per_second": 0,
                    "response_time_ms": response_time_ms, "error": resp["error"]}

        content = resp.get("content", "") or ""
        usage = resp.get("usage", {}) or {}
        token_count = usage.get("total_tokens", 0) or len(content.split())
        tokens_per_second = round(token_count / elapsed, 2) if elapsed > 0 else 0.0

        return {"ok": True, "token_count": token_count,
                "tokens_per_second": tokens_per_second,
                "response_time_ms": response_time_ms, "error": None}
    except Exception as e:
        return {"ok": False, "token_count": 0, "tokens_per_second": 0,
                "response_time_ms": 0, "error": str(e)}


def benchmark_async(provider_name: str, model_name: str,
                    callback: callable, timeout: int = 60):
    """Benchmark a model asynchronously."""
    def _run():
        result = benchmark_model(provider_name, model_name, timeout)
        if callback:
            callback(result)
    t = threading.Thread(target=_run, daemon=True)
    t.start()


def compare_models(provider_name: str, model_names: list, timeout: int = 60):
    """Benchmark multiple models and return sorted results by speed.

    Returns sorted list of {"model", "provider", ...} dicts with
    fastest model first. Failed benchmarks are sorted last.
    """
    if not model_names:
        return []

    results = []
    for name in model_names:
        bench = benchmark_model(provider_name, name, timeout)
        bench["model"] = name
        bench["provider"] = provider_name
        results.append(bench)

    results.sort(key=lambda r: (-r.get("ok", False), -r.get("tokens_per_second", 0)))
    return results


def compare_models_async(provider_name: str, model_names: list,
                         callback: callable, timeout: int = 60):
    """Benchmark multiple models asynchronously."""
    def _run():
        results = compare_models(provider_name, model_names, timeout)
        if callback:
            callback(results)
    t = threading.Thread(target=_run, daemon=True)
    t.start()


def get_benchmark_history():
    """Return up to 50 most recent benchmark results from the journal.

    Returns list of dicts, filtering for entries with benchmark data.
    """
    try:
        from auto_logger import get_session_events
        events = get_session_events()
        results = []
        for event in events:
            data = event.get("data")
            if data and "model" in data and "tokens_per_second" in data:
                results.append(data)
        return results
    except ImportError:
        return []


def log_benchmark_result(result: dict):
    """Log a benchmark result to the session journal.

    Uses auto_logger.journal_event to persist the result.
    """
    try:
        from auto_logger import journal_event
        model = result.get("model", "unknown")
        tps = result.get("tokens_per_second", 0)
        summary = f"{model}: {tps} tok/s"
        journal_event("benchmark", summary, data=result)
    except ImportError:
        pass
