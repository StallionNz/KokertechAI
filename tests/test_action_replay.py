"""
tests/test_action_replay.py -- Regression tests for action_replay.py & ai_base wiring.

Validates:
  1. Deterministic hashing & canonicalization of messages
  2. ActionReplay file storage, retrieval, and cache eviction/clearing
  3. Thread-safe concurrent operations
  4. Corrupted line tolerance
  5. Integration with ai_base.cached_chat_completion across "off", "record", and "replay" modes
"""

import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from action_replay import (
    ActionReplay,
    compute_messages_hash,
    get_replay,
    get_replay_file,
    get_replay_mode,
    reset_replay,
)
from ai_base import AIProvider, cached_chat_completion


class TestActionReplayHelpers(unittest.TestCase):
    """Unit tests for module-level helpers in action_replay."""

    def test_get_replay_mode(self):
        with patch.dict("config.CONFIG", {"freeform_mode": False, "mock_mode": False, "action_replay_mode": "record"}, clear=False):
            self.assertEqual(get_replay_mode(), "record")
        with patch.dict("config.CONFIG", {"freeform_mode": False, "mock_mode": False, "action_replay_mode": "replay"}, clear=False):
            self.assertEqual(get_replay_mode(), "replay")
        with patch.dict("config.CONFIG", {"freeform_mode": False, "mock_mode": False, "action_replay_mode": "invalid_mode"}, clear=False):
            self.assertEqual(get_replay_mode(), "off")

    def test_get_replay_file_and_singleton(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_file = os.path.join(tmp_dir, "custom_replay.jsonl")
            with patch.dict("config.CONFIG", {"freeform_mode": False, "mock_mode": False, "action_replay_file": tmp_file}, clear=False):
                self.assertEqual(get_replay_file(), tmp_file)
                rep = get_replay()
                self.assertIsInstance(rep, ActionReplay)
                self.assertEqual(rep.filepath, tmp_file)

                # Record something then reset
                rep.record("hash1", {"content": "Hello"})
                self.assertTrue(os.path.isfile(tmp_file))
                reset_replay()
                self.assertFalse(os.path.isfile(tmp_file))


class DummyTestProvider(AIProvider):
    """Simple provider stub for action_replay integration tests."""

    def __init__(self, name="dummy", model="test-model"):
        super().__init__(default_model=model)
        self.name = name
        self.model = model
        self.call_count = 0

    def chat_completion(self, messages, **kwargs):
        self.call_count += 1
        return {
            "content": f"Response {self.call_count} to: {messages[-1].get('content', '')}",
            "model": self.model,
            "usage": {"total_tokens": 42},
        }


class TestActionReplayHashing(unittest.TestCase):
    """Unit tests for compute_messages_hash and canonicalization."""

    def test_deterministic_hash(self):
        messages = [
            {"role": "system", "content": "You are Kokertech."},
            {"role": "user", "content": "Hello local AI!"},
        ]
        hash1 = compute_messages_hash(messages, model="ministral", temperature=0.7)
        hash2 = compute_messages_hash(messages, model="ministral", temperature=0.7)
        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)  # SHA-256 hex string

    def test_different_temperature_or_model_yields_different_hash(self):
        messages = [{"role": "user", "content": "Ping"}]
        h1 = compute_messages_hash(messages, model="model-a", temperature=0.7)
        h2 = compute_messages_hash(messages, model="model-b", temperature=0.7)
        h3 = compute_messages_hash(messages, model="model-a", temperature=0.2)
        self.assertNotEqual(h1, h2)
        self.assertNotEqual(h1, h3)

    def test_response_format_affects_hash(self):
        messages = [{"role": "user", "content": "Format check"}]
        h_none = compute_messages_hash(messages, model="m", temperature=0.5, response_format=None)
        h_json = compute_messages_hash(messages, model="m", temperature=0.5, response_format={"type": "json"})
        h_xml = compute_messages_hash(messages, model="m", temperature=0.5, response_format={"type": "xml"})
        self.assertNotEqual(h_none, h_json)
        self.assertNotEqual(h_json, h_xml)

    def test_canonicalization_ignores_transient_metadata(self):
        msgs_clean = [{"role": "user", "content": "Run tool"}]
        msgs_with_meta = [{"role": "user", "content": "Run tool", "max_tokens": 100, "tools": ["grep"]}]
        h_clean = compute_messages_hash(msgs_clean, model="m", temperature=0.7)
        h_meta = compute_messages_hash(msgs_with_meta, model="m", temperature=0.7)
        self.assertEqual(h_clean, h_meta)


class TestActionReplayFileOperations(unittest.TestCase):
    """Unit tests for ActionReplay file recording, retrieval, and clearing."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.replay_file = os.path.join(self.temp_dir.name, "replay.jsonl")
        self.ar = ActionReplay(filepath=self.replay_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_replay_returns_none(self):
        self.assertFalse(self.ar.has("nonexistent_hash"))
        self.assertIsNone(self.ar.get("nonexistent_hash"))

    def test_record_and_retrieve(self):
        hash_key = "abc123def456"
        response_data = {"content": "Deterministic output", "tokens": 10}
        self.ar.record(hash_key, response_data)

        self.assertTrue(self.ar.has(hash_key))
        retrieved = self.ar.get(hash_key)
        self.assertEqual(retrieved, response_data)

    def test_clear_deletes_file(self):
        self.ar.record("hash1", {"content": "foo"})
        self.assertTrue(os.path.isfile(self.replay_file))

        self.ar.clear()
        self.assertFalse(os.path.isfile(self.replay_file))
        self.assertFalse(self.ar.has("hash1"))

    def test_corrupted_line_is_skipped_gracefully(self):
        self.ar.record("valid_1", {"content": "ok 1"})
        # Inject corrupted line
        with open(self.replay_file, "a", encoding="utf-8") as f:
            f.write("CORRUPTED NOT JSON {{{}\n")
        self.ar.record("valid_2", {"content": "ok 2"})

        self.assertEqual(self.ar.get("valid_1")["content"], "ok 1")
        self.assertEqual(self.ar.get("valid_2")["content"], "ok 2")

    def test_concurrent_recording(self):
        def worker(idx):
            self.ar.record(f"hash_{idx}", {"content": f"val_{idx}"})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for i in range(20):
            self.assertTrue(self.ar.has(f"hash_{i}"))
            self.assertEqual(self.ar.get(f"hash_{i}")["content"], f"val_{i}")


class TestActionReplayIntegration(unittest.TestCase):
    """Integration tests with ai_base.cached_chat_completion."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.replay_file = os.path.join(self.temp_dir.name, "replay.jsonl")
        self.provider = DummyTestProvider()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_record_mode_persists_response(self):
        config_patch = {
            "freeform_mode": False,
            "mock_mode": False,
            "response_cache_enabled": False,
            "action_replay_mode": "record",
            "action_replay_file": self.replay_file,
        }
        with patch.dict("config.CONFIG", config_patch, clear=False):
            messages = [{"role": "user", "content": "Execute step A"}]
            resp1 = cached_chat_completion(self.provider, messages, model="test-model", temperature=0.7)

            self.assertEqual(self.provider.call_count, 1)
            self.assertIn("Response 1", resp1["content"])

            # Verify saved to replay
            ar = ActionReplay(filepath=self.replay_file)
            h = compute_messages_hash(messages, model="test-model", temperature=0.7)
            self.assertTrue(ar.has(h))
            stored = ar.get(h)
            self.assertEqual(stored["content"], resp1["content"])

    def test_replay_mode_returns_stored_without_calling_provider(self):
        # 1. Pre-seed replay file
        ar = ActionReplay(filepath=self.replay_file)
        messages = [{"role": "user", "content": "What is the square root of 64?"}]
        h = compute_messages_hash(messages, model="test-model", temperature=0.7)
        ar.record(h, {"content": "Stored Replay 8", "usage": {"total_tokens": 1}})

        # 2. Call in replay mode
        config_patch = {
            "freeform_mode": False,
            "mock_mode": False,
            "response_cache_enabled": False,
            "action_replay_mode": "replay",
            "action_replay_file": self.replay_file,
        }
        with patch.dict("config.CONFIG", config_patch, clear=False):
            resp = cached_chat_completion(self.provider, messages, model="test-model", temperature=0.7)

            # Provider should NEVER have been called!
            self.assertEqual(self.provider.call_count, 0)
            self.assertEqual(resp["content"], "Stored Replay 8")

    def test_replay_mode_miss_falls_back_to_provider(self):
        config_patch = {
            "freeform_mode": False,
            "mock_mode": False,
            "response_cache_enabled": False,
            "action_replay_mode": "replay",
            "action_replay_file": self.replay_file,
        }
        with patch.dict("config.CONFIG", config_patch, clear=False):
            messages = [{"role": "user", "content": "Unseen message"}]
            resp = cached_chat_completion(self.provider, messages, model="test-model", temperature=0.7)

            # Falls back to live provider on miss
            self.assertEqual(self.provider.call_count, 1)
            self.assertIn("Response 1", resp["content"])

    def test_off_mode_does_not_read_or_write_replay(self):
        config_patch = {
            "freeform_mode": False,
            "mock_mode": False,
            "response_cache_enabled": False,
            "action_replay_mode": "off",
            "action_replay_file": self.replay_file,
        }
        with patch.dict("config.CONFIG", config_patch, clear=False):
            messages = [{"role": "user", "content": "Normal run"}]
            cached_chat_completion(self.provider, messages, model="test-model", temperature=0.7)

            self.assertEqual(self.provider.call_count, 1)
            self.assertFalse(os.path.isfile(self.replay_file))


if __name__ == "__main__":
    unittest.main()
