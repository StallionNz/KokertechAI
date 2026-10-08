"""
Tests for the conftest _reset_ai_caches_per_test autouse fixture.

Verifies that the AI provider cache (_PROVIDER_CACHE) and the AI response
cache (_RESPONSE_CACHE) are cleared before each test, preventing in-file
test leakage where test A caches a provider/response and test B inherits it.
"""

import unittest
from unittest.mock import patch


class TestAICacheIsolation(unittest.TestCase):
    """Verifies the conftest _reset_ai_caches_per_test fixture clears the
    AI provider cache (_PROVIDER_CACHE) before each test, preventing in-file
    test leakage where test A caches a provider and test B inherits it.
    """

    def setUp(self):
        """Clear the provider cache before each test.

        The conftest teardown hook only resets shared state between test FILES,
        not within a single file. Since all four tests live in this one file,
        we clear the cache explicitly in setUp to prevent intra-file leakage.
        """
        import ai_base
        ai_base._PROVIDER_CACHE.clear()

    def test_ai_cache_part1_pollutes_cache(self):
        """Pollute the provider cache so part2 can verify it was cleared."""
        import ai_base
        with patch("ai_base._LLAMA_CPP_AVAILABLE", True), patch("ai_base.Llama"):
            provider = ai_base.get_provider(default_model="test_leak_model")
            self.assertIsNotNone(provider)
        self.assertIn("local_llm:test_leak_model", ai_base._PROVIDER_CACHE)
        self.assertGreater(len(ai_base._PROVIDER_CACHE), 0)

    def test_ai_cache_part2_cache_was_cleared(self):
        """Verify the autouse fixture cleared the provider cache before this test ran."""
        import ai_base
        self.assertEqual(
            len(ai_base._PROVIDER_CACHE), 0,
            f"Provider cache leaked between tests! Keys: {list(ai_base._PROVIDER_CACHE.keys())}. "
            f"The _reset_ai_caches_per_test fixture is not working."
        )

    def test_ai_cache_fresh_provider_creation_works(self):
        """After the fixture clears the cache, get_provider creates a new instance."""
        import ai_base
        with patch("ai_base._LLAMA_CPP_AVAILABLE", True), patch("ai_base.Llama"):
            p1 = ai_base.get_provider(default_model="model_a")
            p2 = ai_base.get_provider(default_model="model_b")
        self.assertIn("local_llm:model_a", ai_base._PROVIDER_CACHE)
        self.assertIn("local_llm:model_b", ai_base._PROVIDER_CACHE)
        self.assertIsNot(p1, p2)

    def test_ai_cache_idempotent_when_cache_already_empty(self):
        """Calling reset on an already-empty cache is a no-op."""
        import ai_base
        self.assertEqual(len(ai_base._PROVIDER_CACHE), 0)
        ai_base.reset_providers()
        self.assertEqual(len(ai_base._PROVIDER_CACHE), 0)