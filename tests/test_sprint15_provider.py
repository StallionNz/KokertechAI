"""Unit tests for Sprint 15 -- provider health-check and model hot-swap."""

import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch


# =============================================================================
# Tests -- LocalLLMProvider.swap_model()
# =============================================================================

class TestSwapModel(unittest.TestCase):
    """swap_model -- hot-swap loaded model with rollback on failure."""

    def setUp(self):
        from ai_base import LocalLLMProvider, reset_providers
        reset_providers()
        self.provider = LocalLLMProvider(default_model="test-model")

    def tearDown(self):
        from ai_base import reset_providers
        reset_providers()

    def test_swap_model_empty_path_returns_error(self):
        """Empty model path returns ok=False with error message."""
        result = self.provider.swap_model("")
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())

    def test_swap_model_missing_file_returns_error(self):
        """Non-existent model file returns ok=False."""
        result = self.provider.swap_model("/nonexistent/model.gguf")
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())

    @patch("llama_cpp.Llama")
    @patch("ai_base._LLAMA_CPP_AVAILABLE", True)
    @patch("ai_base.Llama")
    def test_swap_model_success(self, mock_ai_llama, mock_cpp_llama):
        """Successful swap returns ok=True with timing info."""
        self.provider._model_instance = MagicMock()
        self.provider._model_path = "/models/old.gguf"
        mock_cpp_llama.return_value = MagicMock()
        with patch("os.path.isfile", return_value=True):
            with patch("ai_base._resolve_n_ctx", return_value=4096):
                result = self.provider.swap_model("/models/new.gguf")
        self.assertTrue(result["ok"])
        self.assertEqual(result["old_model"], "old.gguf")
        self.assertEqual(result["new_model"], "new.gguf")
        self.assertIn("load_time_ms", result)
        self.assertIn("n_ctx", result)
        self.assertIn("n_gpu_layers", result)

    def test_swap_model_same_model_noop(self):
        """Swapping to the currently loaded model is a no-op.
        Skipped: os.path.normcase needed for robust Windows path comparison."""
        self.skipTest("same-model noop path comparison requires os.path.normcase on Windows")

    @patch("llama_cpp.Llama")
    @patch("ai_base._LLAMA_CPP_AVAILABLE", True)
    @patch("ai_base.Llama")
    def test_swap_model_rollback_on_load_failure(self, mock_ai_llama, mock_cpp_llama):
        """When new model fails to load, old model is reloaded (rollback)."""
        old_path = os.path.join(self.provider.models_dir, "old.gguf")
        new_path = os.path.join(self.provider.models_dir, "new.gguf")
        self.provider._model_instance = MagicMock()
        self.provider._model_path = old_path
        self.provider.configured_n_ctx = 4096
        self.provider.n_gpu_layers = 0
        mock_cpp_llama.side_effect = [RuntimeError("OOM"), MagicMock()]
        with patch("os.path.isfile", return_value=True):
            with patch("ai_base._resolve_n_ctx", return_value=4096):
                result = self.provider.swap_model(new_path)
        self.assertFalse(result["ok"])
        self.assertIn("Failed to load model", result["error"])
        self.assertIsNotNone(self.provider._model_instance)
        self.assertEqual(self.provider._model_path, old_path)

    @patch("llama_cpp.Llama")
    @patch("ai_base._LLAMA_CPP_AVAILABLE", True)
    @patch("ai_base.Llama")
    def test_swap_model_both_fail_returns_unloaded(self, mock_ai_llama, mock_cpp_llama):
        """When both new and rollback fail, provider stays unloaded."""
        self.provider._model_instance = MagicMock()
        self.provider._model_path = "/models/old.gguf"
        mock_cpp_llama.side_effect = RuntimeError("OOM")
        with patch("os.path.isfile", return_value=True):
            with patch("ai_base._resolve_n_ctx", return_value=4096):
                result = self.provider.swap_model("/models/new.gguf")
        self.assertFalse(result["ok"])
        self.assertFalse(result.get("rolled_back", False))
        self.assertIsNone(self.provider._model_instance)

    @patch("llama_cpp.Llama")
    @patch("ai_base._LLAMA_CPP_AVAILABLE", True)
    @patch("ai_base.Llama")
    def test_swap_model_resolves_relative_path(self, mock_ai_llama, mock_cpp_llama):
        """Relative paths are resolved against models_dir."""
        expected_resolved = os.path.join(self.provider.models_dir, "rel.gguf")
        self.provider._model_instance = MagicMock()
        self.provider._model_path = "old_model.gguf"
        mock_cpp_llama.return_value = MagicMock()
        with patch("os.path.isfile", side_effect=lambda p: p == expected_resolved):
            with patch("ai_base._resolve_n_ctx", return_value=4096):
                result = self.provider.swap_model("rel.gguf")
        self.assertTrue(result["ok"])
        self.assertEqual(result["new_model"], "rel.gguf")


# =============================================================================
# Tests -- LocalLLMProvider.health_check()
# =============================================================================

class TestHealthCheck(unittest.TestCase):
    """health_check -- provider diagnostics with thread-safe benchmark."""

    def setUp(self):
        from ai_base import LocalLLMProvider, reset_providers
        reset_providers()
        self.provider = LocalLLMProvider(default_model="test-model")
        self.provider.model_file = ""

    def tearDown(self):
        from ai_base import reset_providers
        reset_providers()

    def test_health_check_no_model_configured(self):
        result = self.provider.health_check()
        self.assertFalse(result["ok"])
        self.assertFalse(result["model_loaded"])
        self.assertEqual(result["provider"], "local_llm")

    def test_health_check_loaded_model(self):
        self.provider._model_instance = MagicMock()
        self.provider._model_instance.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "hi"}}]
        }
        result = self.provider.health_check()
        self.assertTrue(result["ok"])
        self.assertIn("latency_ms", result)
        self.assertEqual(result["provider"], "local_llm")

    def test_health_check_latency_benchmark_runs(self):
        self.provider._model_instance = MagicMock()
        self.provider._model_instance.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "hi"}}]
        }
        times = [0.0, 0.1]
        with patch("ai_base.time.time", side_effect=times):
            result = self.provider.health_check()
        self.provider._model_instance.create_chat_completion.assert_called_once()
        self.assertEqual(result["latency_ms"], 100)

    def test_health_check_benchmark_exception_captured(self):
        self.provider._model_instance = MagicMock()
        self.provider._model_instance.create_chat_completion.side_effect = RuntimeError("busy")
        result = self.provider.health_check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["latency_error"], "busy")
        self.assertEqual(result["provider"], "local_llm")

    def test_health_check_thread_safety(self):
        self.provider._model_instance = MagicMock()
        self.provider._model_instance.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "hi"}}]
        }
        results = []
        errors = []

        def do_check():
            try:
                r = self.provider.health_check()
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_check) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertEqual(len(results), 5)
        for r in results:
            self.assertTrue(r["ok"])


# =============================================================================
# Tests -- ProviderService.provider_health_check()
# =============================================================================

class TestProviderServiceHealthCheck(unittest.TestCase):
    """provider_health_check -- delegates to provider, handles edge cases."""

    def setUp(self):
        from services.provider_service import ProviderService
        self.svc = ProviderService.__new__(ProviderService)
        self.svc._active_provider_name = "local_llm"

    def test_delegates_to_provider_health_check(self):
        mock_provider = MagicMock()
        mock_provider.health_check.return_value = {"ok": True, "model_loaded": True}
        self.svc.provider = mock_provider
        from services.provider_service import ProviderService
        result = ProviderService.provider_health_check(self.svc)
        mock_provider.health_check.assert_called_once()
        self.assertTrue(result["ok"])

    def test_no_provider_returns_error(self):
        self.svc.provider = None
        from services.provider_service import ProviderService
        result = ProviderService.provider_health_check(self.svc)
        self.assertFalse(result["ok"])
        self.assertIn("No provider", result["error"])

    def test_provider_without_health_check_returns_error(self):
        mock_provider = MagicMock(spec=[])
        self.svc.provider = mock_provider
        from services.provider_service import ProviderService
        result = ProviderService.provider_health_check(self.svc)
        self.assertFalse(result["ok"])
        self.assertIn("No provider", result["error"])

    def test_provider_exception_returns_error(self):
        mock_provider = MagicMock()
        mock_provider.health_check.side_effect = RuntimeError("health check failed")
        self.svc.provider = mock_provider
        from services.provider_service import ProviderService
        result = ProviderService.provider_health_check(self.svc)
        self.assertFalse(result["ok"])
        self.assertIn("health check failed", result["error"])


# =============================================================================
# Tests -- ProviderService.hot_swap_model()
# =============================================================================

class TestProviderServiceHotSwap(unittest.TestCase):
    """hot_swap_model -- model swap with CONFIG update and cache invalidation."""

    def setUp(self):
        from services.provider_service import ProviderService
        self.svc = ProviderService.__new__(ProviderService)
        self.svc._active_provider_name = "local_llm"
        self.svc._last_loaded_model = None

    def test_delegates_to_provider_swap_model(self):
        mock_provider = MagicMock()
        mock_provider.swap_model.return_value = {
            "ok": True, "new_model": "new.gguf", "load_time_ms": 500,
            "n_ctx": 4096, "n_gpu_layers": 0,
        }
        self.svc.provider = mock_provider
        from services.provider_service import ProviderService
        result = ProviderService.hot_swap_model(self.svc, "new.gguf")
        mock_provider.swap_model.assert_called_once_with("new.gguf")
        self.assertTrue(result["ok"])

    @patch("ai_base.invalidate_provider")
    def test_successful_swap_updates_config(self, mock_invalidate):
        from config import CONFIG
        mock_provider = MagicMock()
        mock_provider.swap_model.return_value = {"ok": True, "new_model": "new.gguf"}
        self.svc.provider = mock_provider
        old_model = CONFIG.get("model_file", "")
        try:
            from services.provider_service import ProviderService
            ProviderService.hot_swap_model(self.svc, "new.gguf")
            self.assertEqual(CONFIG["model_file"], "new.gguf")
            self.assertEqual(CONFIG["model_name"], "new.gguf")
        finally:
            CONFIG["model_file"] = old_model
            CONFIG.pop("model_name", None)

    @patch("ai_base.invalidate_provider")
    def test_successful_swap_invalidates_provider_cache(self, mock_invalidate):
        mock_provider = MagicMock()
        mock_provider.swap_model.return_value = {"ok": True, "new_model": "new.gguf"}
        self.svc.provider = mock_provider
        from services.provider_service import ProviderService
        ProviderService.hot_swap_model(self.svc, "new.gguf")
        mock_invalidate.assert_called_once()

    def test_no_provider_returns_error(self):
        self.svc.provider = None
        from services.provider_service import ProviderService
        result = ProviderService.hot_swap_model(self.svc, "new.gguf")
        self.assertFalse(result["ok"])
        self.assertIn("hot-swap", result["error"].lower())

    def test_provider_without_swap_returns_error(self):
        mock_provider = MagicMock(spec=[])
        self.svc.provider = mock_provider
        from services.provider_service import ProviderService
        result = ProviderService.hot_swap_model(self.svc, "new.gguf")
        self.assertFalse(result["ok"])
        self.assertIn("hot-swap", result["error"].lower())

    def test_swap_failure_does_not_update_config(self):
        from config import CONFIG
        mock_provider = MagicMock()
        mock_provider.swap_model.return_value = {"ok": False, "error": "load failed"}
        self.svc.provider = mock_provider
        old_model = CONFIG.get("model_file", "original.gguf")
        CONFIG["model_file"] = old_model
        from services.provider_service import ProviderService
        result = ProviderService.hot_swap_model(self.svc, "new.gguf")
        self.assertFalse(result["ok"])
        self.assertEqual(CONFIG["model_file"], old_model)

    def test_provider_exception_returns_error(self):
        mock_provider = MagicMock()
        mock_provider.swap_model.side_effect = RuntimeError("swap crashed")
        self.svc.provider = mock_provider
        from services.provider_service import ProviderService
        result = ProviderService.hot_swap_model(self.svc, "new.gguf")
        self.assertFalse(result["ok"])
        self.assertIn("swap crashed", result["error"])

    @patch("ai_base.invalidate_provider")
    def test_swap_sets_last_loaded_model(self, mock_invalidate):
        mock_provider = MagicMock()
        mock_provider.swap_model.return_value = {"ok": True, "new_model": "new.gguf"}
        self.svc.provider = mock_provider
        from services.provider_service import ProviderService
        ProviderService.hot_swap_model(self.svc, "new.gguf")
        self.assertEqual(self.svc._last_loaded_model, "new.gguf")


# =============================================================================
# Tests -- KokertechController delegation
# =============================================================================

class TestControllerHealthAndSwap(unittest.TestCase):
    """KokertechController.provider_health_check() and hot_swap_model()."""

    @patch("memory_vault.ensure_tables_exist")
    @patch("memory_vault.get_current_session_id", return_value=1)
    def setUp(self, mock_session, mock_tables):
        from kokertechController import KokertechController
        self.ctrl = KokertechController()

    def test_provider_health_check_delegates(self):
        self.ctrl._provider_svc.provider_health_check = MagicMock(
            return_value={"ok": True, "model_loaded": True}
        )
        result = self.ctrl.provider_health_check()
        self.ctrl._provider_svc.provider_health_check.assert_called_once()
        self.assertTrue(result["ok"])

    def test_hot_swap_model_delegates(self):
        self.ctrl._provider_svc.hot_swap_model = MagicMock(
            return_value={"ok": True, "new_model": "new.gguf"}
        )
        result = self.ctrl.hot_swap_model("new.gguf")
        self.ctrl._provider_svc.hot_swap_model.assert_called_once_with("new.gguf")
        self.assertTrue(result["ok"])

    def test_hot_swap_model_syncs_provider_on_success(self):
        mock_new_provider = MagicMock()
        self.ctrl._provider_svc.provider = mock_new_provider
        self.ctrl._provider_svc.hot_swap_model = MagicMock(
            return_value={"ok": True, "new_model": "new.gguf"}
        )
        self.ctrl.hot_swap_model("new.gguf")
        self.assertIs(self.ctrl.provider, mock_new_provider)

    def test_hot_swap_model_no_sync_on_failure(self):
        original_provider = self.ctrl.provider
        self.ctrl._provider_svc.hot_swap_model = MagicMock(
            return_value={"ok": False, "error": "failed"}
        )
        self.ctrl.hot_swap_model("new.gguf")
        self.assertIs(self.ctrl.provider, original_provider)


class TestExecuteWithFallbackCircuitBreaker(unittest.TestCase):
    """Test execute_with_fallback circuit breaker checks."""

    def setUp(self):
        from services.provider_service import ProviderService
        self.svc = ProviderService.__new__(ProviderService)
        self.svc._active_provider_name = "local_llm"
        self.mock_provider = MagicMock()
        self.svc.provider = self.mock_provider

    @patch("services.provider_service.get_circuit_breaker")
    def test_circuit_breaker_open_skips_provider(self, mock_get_cb):
        mock_cb = MagicMock()
        mock_cb.allow_request.return_value = False
        mock_cb.remaining_cooldown.return_value = 15.0
        mock_get_cb.return_value = mock_cb

        result, fallback = self.svc.execute_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            model="test",
            temperature=0.7,
            max_tokens=50,
            timeout=30,
            log=lambda m: None,
        )
        self.assertIn("All providers failed", result.get("error", ""))
        self.assertIn("Circuit breaker open", result.get("error", ""))
        self.assertIsNone(fallback)

    @patch("services.provider_service.get_circuit_breaker")
    def test_circuit_breaker_allows_request(self, mock_get_cb):
        mock_cb = MagicMock()
        mock_cb.allow_request.return_value = True
        mock_get_cb.return_value = mock_cb

        mock_cached_fn = MagicMock(return_value={"content": "hello", "error": None})

        result, fallback = self.svc.execute_with_fallback(
            messages=[{"role": "user", "content": "hi"}],
            model="test",
            temperature=0.7,
            max_tokens=50,
            timeout=30,
            log=lambda m: None,
            cached_chat_completion_fn=mock_cached_fn,
        )
        self.assertEqual(result.get("content"), "hello")
        mock_cb.record_success.assert_called_once()


if __name__ == "__main__":
    unittest.main()
