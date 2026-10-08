"""tests/test_settings_cache_service.py -- Sprint 19.3 SettingsCacheService tests.

Pins the extraction contract from ``kokertechController``:
- mtime-keyed TTL cache (hit / miss / expiry / mtime invalidation)
- ``load_user_profile`` formatting + reader delegation
- ``load_target_model`` default + provider-cache invalidation on change
- lazy-singleton + reset helper

ANTI-FRAGILITY: the service accepts optional ``cache`` / ``cache_lock``
overrides so the facade can pass its mirrored (rebound) dict -- tests here
cover both the service-default state and the override path.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from services.settings_cache_service import (
    SettingsCacheService,
    _reset_settings_cache_service_for_tests,
    get_settings_cache_service,
)


class SettingsCacheServiceTmpFileMixin:
    """Shared tmp-file helpers for cache tests (mirrors controller tests)."""

    def setUp(self):
        super().setUp()
        self._tmpfiles = []

    def tearDown(self):
        for path in self._tmpfiles:
            try:
                os.remove(path)
            except OSError:
                pass
        super().tearDown()

    def _write_json(self, payload):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception:
            os.close(fd)
            raise
        self._tmpfiles.append(path)
        return path, os.path.getmtime(path)

    def _touch(self, path, new_payload):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(new_payload, f)
        time.sleep(0.05)
        return os.path.getmtime(path)


class TestReadCachedJson(SettingsCacheServiceTmpFileMixin, unittest.TestCase):
    """mtime-keyed TTL cache: miss / hit / expiry / mtime invalidation."""

    def setUp(self):
        super().setUp()
        self.svc = SettingsCacheService()

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.svc.read_cached_json(r"C:\nonexistent\nope.json"))
        self.assertEqual(len(self.svc._json_cache), 0)

    def test_empty_path_returns_none(self):
        self.assertIsNone(self.svc.read_cached_json(""))

    def test_cache_miss_populates_cache(self):
        path, _ = self._write_json({"key": "value"})
        self.assertEqual(self.svc.read_cached_json(path), {"key": "value"})
        self.assertIn(path, self.svc._json_cache)

    def test_cache_hit_avoids_disk_read(self):
        path, _ = self._write_json({"key": "v1"})
        self.assertEqual(self.svc.read_cached_json(path), {"key": "v1"})
        with patch("builtins.open") as spy_open:
            self.assertEqual(self.svc.read_cached_json(path), {"key": "v1"})
            spy_open.assert_not_called()

    def test_ttl_expiry_rereads(self):
        """Acceptance: after TTL elapses the next call re-reads the file."""
        path, _ = self._write_json({"v": 1})
        self.svc.read_cached_json(path)
        with self.svc._json_cache_lock:
            self.svc._json_cache[path]["ts"] = 0.0
        self._touch(path, {"v": 2})
        self.assertEqual(self.svc.read_cached_json(path), {"v": 2})

    def test_mtime_change_invalidates_cache(self):
        """Acceptance: file-write to next call repopulates from disk."""
        path, _ = self._write_json({"v": 1})
        self.assertEqual(self.svc.read_cached_json(path), {"v": 1})
        self._touch(path, {"v": 2})
        self.assertEqual(self.svc.read_cached_json(path), {"v": 2})

    def test_custom_ttl_override(self):
        path, _ = self._write_json({"v": 1})
        self.svc.read_cached_json(path, ttl=10.0)
        with self.svc._json_cache_lock:
            self.svc._json_cache[path]["ts"] = time.time() - 5.0
        with patch("builtins.open") as spy_open:
            self.assertEqual(self.svc.read_cached_json(path, ttl=10.0), {"v": 1})
            spy_open.assert_not_called()

    def test_external_cache_override_is_used(self):
        """Facade passes its mirrored (rebound) cache dict -- must be honored."""
        path, _ = self._write_json({"k": "v"})
        external_cache = {}
        external_lock = self.svc._json_cache_lock
        self.assertEqual(
            self.svc.read_cached_json(path, cache=external_cache, cache_lock=external_lock),
            {"k": "v"},
        )
        self.assertIn(path, external_cache)
        self.assertNotIn(path, self.svc._json_cache)


class TestLoadUserProfile(unittest.TestCase):
    """load_user_profile formatting + reader delegation."""

    def setUp(self):
        self.svc = SettingsCacheService()

    def test_formats_profile(self):
        def reader(path):
            self.assertTrue(path.endswith("user_identity.json"))
            return {"name": "Jacques", "tone": "Direct"}
        result = self.svc.load_user_profile(reader)
        self.assertIn("Jacques", result)
        self.assertIn("Direct", result)
        self.assertIn("Preferred Name", result)
        self.assertIn("Tone Preferences", result)

    def test_missing_reader_data_returns_default(self):
        result = self.svc.load_user_profile(lambda path: None)
        self.assertIn("No specific personal identity layer", result)

    def test_empty_dict_returns_default(self):
        result = self.svc.load_user_profile(lambda path: {})
        self.assertIn("No specific personal identity layer", result)

    def test_workspace_override(self):
        seen = []
        def reader(path):
            seen.append(path)
            return None
        self.svc.load_user_profile(reader, workspace=r"C:\custom_ws")
        self.assertTrue(seen[0].endswith("user_identity.json"))
        self.assertTrue(seen[0].startswith(r"C:\custom_ws"))


class TestLoadTargetModel(unittest.TestCase):
    """load_target_model default + provider-cache invalidation."""

    def setUp(self):
        self.svc = SettingsCacheService()

    def test_returns_model_from_settings(self):
        def reader(path):
            self.assertTrue(path.endswith("app_settings.json"))
            return {"model_name": "qwen2.5-7b-instruct"}
        model, last = self.svc.load_target_model(reader, last_loaded_model=None)
        self.assertEqual(model, "qwen2.5-7b-instruct")
        self.assertEqual(last, "qwen2.5-7b-instruct")

    def test_missing_file_returns_default(self):
        model, last = self.svc.load_target_model(lambda path: None, last_loaded_model=None)
        self.assertEqual(model, "Lexi-Llama-3-8B-Uncensored_Q4_K_M")
        self.assertIsNone(last)

    def test_missing_key_returns_default(self):
        model, _ = self.svc.load_target_model(lambda path: {"other": 1}, last_loaded_model=None)
        self.assertEqual(model, "Lexi-Llama-3-8B-Uncensored_Q4_K_M")

    def test_first_call_no_invalidation(self):
        with patch("ai_base.invalidate_provider") as mock_invalidate:
            model, _ = self.svc.load_target_model(
                lambda path: {"model_name": "model-A"}, last_loaded_model=None,
            )
        self.assertEqual(model, "model-A")
        mock_invalidate.assert_not_called()

    def test_change_invalidates_provider(self):
        with patch("ai_base.invalidate_provider") as mock_invalidate:
            model, last = self.svc.load_target_model(
                lambda path: {"model_name": "model-B"}, last_loaded_model="model-A",
            )
        self.assertEqual(model, "model-B")
        self.assertEqual(last, "model-B")
        mock_invalidate.assert_called_once()

    def test_same_model_skips_invalidation(self):
        with patch("ai_base.invalidate_provider") as mock_invalidate:
            model, last = self.svc.load_target_model(
                lambda path: {"model_name": "model-A"}, last_loaded_model="model-A",
            )
        self.assertEqual(model, "model-A")
        mock_invalidate.assert_not_called()

    def test_workspace_override(self):
        seen = []
        def reader(path):
            seen.append(path)
            return {"model_name": "m"}
        self.svc.load_target_model(reader, last_loaded_model=None, workspace=r"C:\ws2")
        self.assertTrue(seen[0].startswith(r"C:\ws2"))
        self.assertTrue(seen[0].endswith("app_settings.json"))


class TestSingletonPattern(unittest.TestCase):
    """Lazy singleton + reset helper (Sprint 17 R12 convention)."""

    def tearDown(self):
        _reset_settings_cache_service_for_tests()

    def test_getter_returns_same_instance(self):
        a = get_settings_cache_service()
        b = get_settings_cache_service()
        self.assertIs(a, b)

    def test_reset_helper_clears_singleton(self):
        _reset_settings_cache_service_for_tests()
        a = get_settings_cache_service()
        _reset_settings_cache_service_for_tests()
        b = get_settings_cache_service()
        self.assertIsNot(a, b)


if __name__ == "__main__":
    unittest.main()
