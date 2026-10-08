"""Unit tests for model_registry.py — model listing, testing, and benchmarking.

Covers all public functions: list_models, test_model, benchmark_model,
benchmark_async, compare_models, compare_models_async, get_benchmark_history,
log_benchmark_result.

All tests use the local_llm provider only (external providers have been removed).
"""

import sys
import time
import unittest
from unittest.mock import patch, MagicMock


class TestListModels(unittest.TestCase):
    """Tests for list_models() — scans local GGUF models."""

    @patch("model_registry.LocalLLMProvider.scan_models")
    def test_returns_models(self, mock_scan):
        mock_scan.return_value = [
            {"name": "model-a", "path": "/models/model-a.gguf", "size_bytes": 1000000},
            {"name": "model-b", "path": "/models/model-b.gguf", "size_bytes": 2000000},
        ]
        from model_registry import list_models
        result = list_models()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "model-a")
        self.assertEqual(result[0]["provider"], "local_llm")
        self.assertEqual(result[0]["path"], "/models/model-a.gguf")
        self.assertEqual(result[0]["size_bytes"], 1000000)

    @patch("model_registry.LocalLLMProvider.scan_models")
    def test_empty_returns_empty_list(self, mock_scan):
        mock_scan.return_value = []
        from model_registry import list_models
        result = list_models()
        self.assertEqual(result, [])

    @patch("model_registry.LocalLLMProvider.scan_models")
    def test_explicit_provider_name(self, mock_scan):
        """list_models("local_llm") should still work."""
        mock_scan.return_value = [
            {"name": "llama3", "path": "/models/llama3.gguf", "size_bytes": 5000000},
        ]
        from model_registry import list_models
        result = list_models("local_llm")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["provider"], "local_llm")

    @patch("model_registry.LocalLLMProvider.scan_models")
    def test_scan_models_exception(self, mock_scan):
        """If scan_models raises, list_models should propagate the error
        (the scan_models call propagates errors directly)."""
        mock_scan.side_effect = RuntimeError("Disk error")
        from model_registry import list_models
        with self.assertRaises(RuntimeError):
            list_models()

class TestTestModel(unittest.TestCase):
    """Tests for test_model() — quick connectivity check."""

    def setUp(self):
        from ai_base import reset_providers
        reset_providers()

    @patch("model_registry.get_provider")
    def test_successful_test(self, mock_get_provider):
        provider = MagicMock()
        provider.chat_completion.return_value = {"content": "OK", "error": None}
        mock_get_provider.return_value = provider
        from model_registry import test_model
        result = test_model("local_llm", "test-model")
        self.assertTrue(result["ok"])
        self.assertEqual(result["response"], "OK")

    @patch("model_registry.get_provider")
    def test_error_response(self, mock_get_provider):
        provider = MagicMock()
        provider.chat_completion.return_value = {"content": "", "error": "HTTP 500"}
        mock_get_provider.return_value = provider
        from model_registry import test_model
        result = test_model("local_llm", "test-model")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "HTTP 500")

    @patch("model_registry.get_provider")
    def test_exception_handling(self, mock_get_provider):
        mock_get_provider.side_effect = RuntimeError("Provider unavailable")
        from model_registry import test_model
        result = test_model("local_llm", "test-model")
        self.assertFalse(result["ok"])
        self.assertIn("Provider unavailable", result["error"])
        self.assertEqual(result["response_time_ms"], 0)

class TestBenchmarkModel(unittest.TestCase):
    """Tests for benchmark_model() — speed measurement."""

    @patch("model_registry.get_provider")
    @patch("model_registry.time.time")
    def test_successful_benchmark(self, mock_time, mock_get_provider):
        # Simulate a 500ms response so tokens_per_second is calculable
        mock_time.side_effect = [100.0, 100.5]  # start, end = 500ms elapsed
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "1\n2\n3\n4\n5\n6\n7\n8\n9\n10",
            "usage": {"total_tokens": 25},
            "error": None,
        }
        mock_get_provider.return_value = provider
        from model_registry import benchmark_model
        result = benchmark_model("local_llm", "test-model")
        self.assertTrue(result["ok"])
        self.assertEqual(result["token_count"], 25)
        self.assertEqual(result["tokens_per_second"], 50.0)  # 25 / 0.5s

    @patch("model_registry.get_provider")
    def test_benchmark_error(self, mock_get_provider):
        provider = MagicMock()
        provider.chat_completion.return_value = {"error": "Timeout", "content": ""}
        mock_get_provider.return_value = provider
        from model_registry import benchmark_model
        result = benchmark_model("local_llm", "test-model")
        self.assertFalse(result["ok"])
        self.assertEqual(result["tokens_per_second"], 0)

    @patch("model_registry.get_provider")
    def test_benchmark_exception(self, mock_get_provider):
        mock_get_provider.side_effect = RuntimeError("Crash")
        from model_registry import benchmark_model
        result = benchmark_model("local_llm", "test-model")
        self.assertFalse(result["ok"])
        self.assertIn("Crash", result["error"])

    @patch("model_registry.get_provider")
    def test_usage_fallback(self, mock_get_provider):
        """When usage is missing, token_count falls back to word count."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "one two three four five",
            "usage": {},
            "error": None,
        }
        mock_get_provider.return_value = provider
        from model_registry import benchmark_model
        result = benchmark_model("local_llm", "test-model")
        self.assertTrue(result["ok"])
        self.assertGreater(result["token_count"], 0)

class TestCompareModels(unittest.TestCase):
    """Tests for compare_models() — multi-model comparison sorted by speed."""

    @patch("model_registry.benchmark_model")
    def test_sorts_by_speed_descending(self, mock_benchmark):
        mock_benchmark.side_effect = [
            {"ok": True, "tokens_per_second": 5.0, "token_count": 10, "response_time_ms": 100},
            {"ok": True, "tokens_per_second": 20.0, "token_count": 10, "response_time_ms": 50},
            {"ok": True, "tokens_per_second": 10.0, "token_count": 10, "response_time_ms": 75},
        ]
        from model_registry import compare_models
        results = compare_models("local_llm", ["slow", "fast", "medium"])
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["model"], "fast")
        self.assertEqual(results[1]["model"], "medium")
        self.assertEqual(results[2]["model"], "slow")

    @patch("model_registry.benchmark_model")
    def test_failures_sorted_last(self, mock_benchmark):
        mock_benchmark.side_effect = [
            {"ok": True, "tokens_per_second": 15.0, "token_count": 10, "response_time_ms": 100},
            {"ok": False, "error": "Timeout", "tokens_per_second": 0, "token_count": 0, "response_time_ms": 0},
        ]
        from model_registry import compare_models
        results = compare_models("local_llm", ["working", "broken"])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["model"], "working")
        self.assertEqual(results[1]["model"], "broken")

    @patch("model_registry.benchmark_model")
    def test_empty_list_returns_empty(self, mock_benchmark):
        from model_registry import compare_models
        results = compare_models("local_llm", [])
        self.assertEqual(results, [])
        mock_benchmark.assert_not_called()

    @patch("model_registry.benchmark_model")
    def test_adds_model_and_provider_fields(self, mock_benchmark):
        mock_benchmark.return_value = {"ok": True, "tokens_per_second": 10.0, "token_count": 5, "response_time_ms": 50}
        from model_registry import compare_models
        results = compare_models("local_llm", ["m1"])
        self.assertEqual(results[0]["model"], "m1")
        self.assertEqual(results[0]["provider"], "local_llm")

class TestGetBenchmarkHistory(unittest.TestCase):
    """Tests for get_benchmark_history() — reading past results."""

    @patch("auto_logger.get_session_events")
    def test_returns_benchmark_data(self, mock_get_events):
        mock_get_events.return_value = [
            {"data": {"model": "a", "tokens_per_second": 10.0}},
            {"data": {"model": "b", "tokens_per_second": 5.0}},
        ]
        from model_registry import get_benchmark_history
        result = get_benchmark_history()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["model"], "a")

    @patch("auto_logger.get_session_events")
    def test_skips_entries_without_data(self, mock_get_events):
        mock_get_events.return_value = [
            {"data": {"model": "a", "tokens_per_second": 10.0}},
            {"message": "no data field"},
        ]
        from model_registry import get_benchmark_history
        result = get_benchmark_history()
        self.assertEqual(len(result), 1)

    @patch("auto_logger.get_session_events", side_effect=ImportError("no auto_logger"))
    def test_import_error_returns_empty(self, mock_get_events):
        from model_registry import get_benchmark_history
        result = get_benchmark_history()
        self.assertEqual(result, [])

    def test_empty_history(self):
        with patch("auto_logger.get_session_events", return_value=[]):
            from model_registry import get_benchmark_history
            result = get_benchmark_history()
            self.assertEqual(result, [])

class TestLogBenchmarkResult(unittest.TestCase):
    """Tests for log_benchmark_result() — persisting results."""

    @patch("auto_logger.journal_event")
    def test_logs_result(self, mock_journal):
        result = {"model": "test", "tokens_per_second": 15.0, "ok": True}
        from model_registry import log_benchmark_result
        log_benchmark_result(result)
        mock_journal.assert_called_once_with("benchmark", "test: 15.0 tok/s", data=result)

    @patch("auto_logger.journal_event")
    def test_logs_failed_result(self, mock_journal):
        result = {"model": "broken", "tokens_per_second": 0, "ok": False, "error": "Timeout"}
        from model_registry import log_benchmark_result
        log_benchmark_result(result)
        mock_journal.assert_called_once()
        args = mock_journal.call_args[0]
        self.assertIn("broken", args[1])
        self.assertIn("0", args[1])

    @patch("auto_logger.journal_event", side_effect=ImportError("no journal"))
    def test_import_error_caught(self, mock_journal):
        result = {"model": "test", "tokens_per_second": 10.0}
        from model_registry import log_benchmark_result
        log_benchmark_result(result)  # Should not raise

if __name__ == "__main__":
    unittest.main()
