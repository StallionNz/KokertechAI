"""Unit tests for ai_base.py — local_llm provider, response cache, and base class."""

import hashlib
import json
import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

# API key override handled by conftest.py pytest_configure() which sets
# KOKERTECH_API_KEY=test-key-override before collection begins.

# =============================================================================
# Tests — AIProvider abstract base class
# =============================================================================

class TestAIProviderBase(unittest.TestCase):
    """Tests for the AIProvider abstract base class itself.
    Covers the abstract method body (line 31 ...) which is never hit
    by concrete subclass instances."""

    def test_abstract_chat_completion_body_ellipsis(self):
        """Bypass ABC restriction and call AIProvider.chat_completion()
        directly so the Ellipsis body (line 31) is executed."""
        from ai_base import AIProvider
        with patch.object(AIProvider, "__abstractmethods__", set()):
            p = AIProvider(
                default_model="test",
            )
            result = p.chat_completion(
                messages=[{"role": "user", "content": "Hi"}]
            )
            self.assertIsNone(result)

# =============================================================================
# Tests — cached_chat_completion (opt-in LRU response cache)
# =============================================================================

class TestCachedChatCompletion(unittest.TestCase):
    """cached_chat_completion — opt-in LRU response cache with TTL.

    Default CONFIG[response_cache_enabled] is False. Tests flip the flag on,
    assert hit/miss/TTL/expiry/error-not-cached behavior, then flip it back.
    """

    def setUp(self):
        from ai_base import (
            cached_chat_completion, clear_response_cache, reset_providers,
        )
        self.cached_chat_completion = cached_chat_completion
        self.clear_response_cache = clear_response_cache
        reset_providers()
        self.clear_response_cache()
        # Ensure the cache is enabled for these tests.
        from config import CONFIG
        self._orig_enabled = CONFIG.get("response_cache_enabled", False)
        CONFIG["response_cache_enabled"] = True

    def tearDown(self):
        from config import CONFIG
        CONFIG["response_cache_enabled"] = self._orig_enabled
        self.clear_response_cache()

    def _make_provider(self, return_value):
        p = MagicMock()
        p.chat_completion.return_value = return_value
        return p

    def test_cache_disabled_passes_through(self):
        """When CONFIG[response_cache_enabled] is False, no caching happens."""
        from config import CONFIG
        CONFIG["response_cache_enabled"] = False
        provider = self._make_provider({"content": "live", "error": None})
        messages = [{"role": "user", "content": "hi"}]
        result = self.cached_chat_completion(provider, messages)
        self.assertEqual(result["content"], "live")
        provider.chat_completion.assert_called_once()
        # Re-enable for the rest of the test
        CONFIG["response_cache_enabled"] = True

    def test_cache_miss_calls_provider(self):
        """First call hits the provider and stores the result."""
        provider = self._make_provider({"content": "first", "error": None})
        messages = [{"role": "user", "content": "hello"}]
        result = self.cached_chat_completion(provider, messages)
        self.assertEqual(result["content"], "first")
        provider.chat_completion.assert_called_once()

    def test_cache_hit_skips_provider(self):
        """Second call with same args short-circuits to the cached result."""
        provider = self._make_provider({"content": "cached", "error": None})
        messages = [{"role": "user", "content": "hello"}]
        self.cached_chat_completion(provider, messages)
        self.cached_chat_completion(provider, messages)
        self.cached_chat_completion(provider, messages)
        # Provider should only be hit once.
        self.assertEqual(provider.chat_completion.call_count, 1)

    def test_cache_returns_shallow_copy(self):
        """Cached result is a shallow copy (caller mutation doesn't poison the cache)."""
        provider = self._make_provider({"content": "x", "error": None, "extras": [1]})
        messages = [{"role": "user", "content": "hi"}]
        r1 = self.cached_chat_completion(provider, messages)
        r1["content"] = "mutated"
        r1["extras"].append(2)
        # Second call returns a fresh copy.
        r2 = self.cached_chat_completion(provider, messages)
        self.assertEqual(r2["content"], "x")
        # Shallow copy means the list is shared but the dict wrapper is not.
        self.assertEqual(r2["extras"], [1, 2])  # shared nested list

    def test_errors_are_not_cached(self):
        """When the provider returns an error, the error is NOT cached."""
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            {"error": "first failure", "content": ""},
            {"content": "second success", "error": None},
        ]
        messages = [{"role": "user", "content": "retry"}]
        r1 = self.cached_chat_completion(provider, messages)
        r2 = self.cached_chat_completion(provider, messages)
        self.assertIsNotNone(r1.get("error"))
        self.assertEqual(r2["content"], "second success")
        self.assertEqual(provider.chat_completion.call_count, 2)

    def test_ttl_expiry_re_issues_request(self):
        """After TTL expires, a new request is issued (cache miss again)."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "ok", "error": None,
        }
        messages = [{"role": "user", "content": "ttl-test"}]
        self.cached_chat_completion(provider, messages)
        # Manually expire the cache entry by reaching into the module.
        import ai_base
        key = ai_base._compute_cache_key(messages, "", 0.7, -1)
        with ai_base._RESPONSE_CACHE_LOCK:
            if key in ai_base._RESPONSE_CACHE:
                ai_base._RESPONSE_CACHE[key]["ts"] = 0  # ancient timestamp
        self.cached_chat_completion(provider, messages)
        self.assertEqual(provider.chat_completion.call_count, 2)

    def test_different_messages_different_keys(self):
        """Different message content -> different cache keys -> both hit provider."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "ok", "error": None,
        }
        self.cached_chat_completion(provider, [{"role": "user", "content": "msg1"}])
        self.cached_chat_completion(provider, [{"role": "user", "content": "msg2"}])
        self.assertEqual(provider.chat_completion.call_count, 2)

    def test_different_model_different_keys(self):
        """Different model name -> different cache keys."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "ok", "error": None,
        }
        msgs = [{"role": "user", "content": "x"}]
        self.cached_chat_completion(provider, msgs, model="model-A")
        self.cached_chat_completion(provider, msgs, model="model-B")
        self.assertEqual(provider.chat_completion.call_count, 2)

    def test_different_temperature_different_keys(self):
        """Different temperature -> different cache keys."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "ok", "error": None,
        }
        msgs = [{"role": "user", "content": "x"}]
        self.cached_chat_completion(provider, msgs, temperature=0.1)
        self.cached_chat_completion(provider, msgs, temperature=0.9)
        self.assertEqual(provider.chat_completion.call_count, 2)

    def test_different_max_tokens_different_keys(self):
        """Different max_tokens -> different cache keys."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "ok", "error": None,
        }
        msgs = [{"role": "user", "content": "x"}]
        self.cached_chat_completion(provider, msgs, max_tokens=100)
        self.cached_chat_completion(provider, msgs, max_tokens=500)
        self.assertEqual(provider.chat_completion.call_count, 2)

    def test_lru_eviction_distinguishes_lru_from_fifo(self):
        """LRU evicts the LEAST RECENTLY USED entry, not the oldest inserted.

        Sequence: insert A, B; access A (now A is recently used); insert C.
        A true LRU policy evicts B (LRU); a FIFO policy would evict A (oldest
        inserted). We verify B was evicted and A was preserved.
        """
        from config import CONFIG
        CONFIG["response_cache_max_size"] = 2
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "ok", "error": None,
        }
        self.cached_chat_completion(provider, [{"role": "user", "content": "A"}])
        self.cached_chat_completion(provider, [{"role": "user", "content": "B"}])
        self.assertEqual(provider.chat_completion.call_count, 2)
        self.cached_chat_completion(provider, [{"role": "user", "content": "A"}])
        self.assertEqual(provider.chat_completion.call_count, 2,
                         "A should still be cached after re-access (LRU promotes A)")
        self.cached_chat_completion(provider, [{"role": "user", "content": "C"}])
        self.assertEqual(provider.chat_completion.call_count, 3)
        self.cached_chat_completion(provider, [{"role": "user", "content": "A"}])
        self.assertEqual(provider.chat_completion.call_count, 3,
                         "A must survive (LRU policy)")
        self.cached_chat_completion(provider, [{"role": "user", "content": "B"}])
        self.assertEqual(provider.chat_completion.call_count, 4,
                         "B was evicted as the LRU entry")

    def test_request_id_forwarded(self):
        """request_id is forwarded to provider.chat_completion for error logging."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "ok", "error": None,
        }
        msgs = [{"role": "user", "content": "req-id test"}]
        self.cached_chat_completion(provider, msgs, request_id="req-abc-123")
        call_kwargs = provider.chat_completion.call_args[1]
        self.assertEqual(call_kwargs.get("request_id"), "req-abc-123")

    def test_cancel_event_forwarded(self):
        """cancel_event is forwarded to provider.chat_completion for cancellation."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "ok", "error": None,
        }
        msgs = [{"role": "user", "content": "cancel-event-forward test"}]
        e1 = threading.Event()
        self.cached_chat_completion(provider, msgs, cancel_event=e1)
        call_kwargs = provider.chat_completion.call_args[1]
        self.assertIs(call_kwargs.get("cancel_event"), e1)

    def test_request_id_not_part_of_cache_key(self):
        """Two calls with same args but different request_id share a cache entry."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "shared", "error": None,
        }
        msgs = [{"role": "user", "content": "shared-key test"}]
        self.cached_chat_completion(provider, msgs, request_id="req-1")
        self.cached_chat_completion(provider, msgs, request_id="req-2")
        self.assertEqual(provider.chat_completion.call_count, 1)

    def test_cancel_event_not_part_of_cache_key(self):
        """Two calls with same args but different cancel_event share a cache entry."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "shared", "error": None,
        }
        msgs = [{"role": "user", "content": "cancel-event-key test"}]
        e1 = threading.Event()
        e2 = threading.Event()
        e2.set()
        self.cached_chat_completion(provider, msgs, cancel_event=e1)
        self.cached_chat_completion(provider, msgs, cancel_event=e2)
        self.assertEqual(provider.chat_completion.call_count, 1)

    def test_clear_response_cache_empties_cache(self):
        """clear_response_cache() drops all cached entries."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": "ok", "error": None,
        }
        self.cached_chat_completion(provider, [{"role": "user", "content": "x"}])
        import ai_base
        self.assertGreater(len(ai_base._RESPONSE_CACHE), 0)
        self.clear_response_cache()
        self.assertEqual(len(ai_base._RESPONSE_CACHE), 0)
        self.cached_chat_completion(provider, [{"role": "user", "content": "x"}])
        self.assertEqual(provider.chat_completion.call_count, 2)

    def test_non_serializable_messages_falls_back_to_repr(self):
        """Non-JSON-serializable messages use repr() for the cache key (MD5)."""
        import datetime
        from ai_base import _compute_cache_key

        messages = [{"role": "user", "content": "x",
                     "ts": datetime.datetime(2026, 6, 13)}]
        key = _compute_cache_key(messages, "model-A", 0.1, -1)
        expected_key_str = f"model-A|0.1|-1|||{repr(messages)}"
        expected_key = hashlib.md5(expected_key_str.encode("utf-8")).hexdigest()
        self.assertEqual(key, expected_key)
        self.assertEqual(len(key), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in key))

    def test_serializable_messages_use_json_serialization(self):
        """Serializable messages use repr() for the cache key (MD5, fast path)."""
        from ai_base import _compute_cache_key

        messages = [{"role": "user", "content": "hello"}]
        key = _compute_cache_key(messages, "model-A", 0.1, -1)
        msgs_str = repr(messages)
        expected_key_str = f"model-A|0.1|-1|||{msgs_str}"
        expected_key = hashlib.md5(expected_key_str.encode("utf-8")).hexdigest()
        self.assertEqual(key, expected_key)

# =============================================================================
# Tests — LRU Cache Contract HIT tests
# =============================================================================

class TestLRUCacheContractHits(unittest.TestCase):
    """Regression tests for the LRU-on-HIT contract: a cache HIT must refresh
    entry["ts"] so eviction finds truly OLD entries (LRU), not just entries
    that were inserted long ago (FIFO)."""

    def setUp(self):
        from ai_base import (
            cached_chat_completion, clear_response_cache, reset_providers,
        )
        self.cached_chat_completion = cached_chat_completion
        self.clear_response_cache = clear_response_cache
        reset_providers()
        self.clear_response_cache()
        from config import CONFIG
        self._orig_enabled = CONFIG.get("response_cache_enabled", False)
        CONFIG["response_cache_enabled"] = True

    def tearDown(self):
        from config import CONFIG
        CONFIG["response_cache_enabled"] = self._orig_enabled
        self.clear_response_cache()

    def _make_provider(self, return_value):
        p = MagicMock()
        p.chat_completion.return_value = return_value
        return p

    def _cache_key_for(self, messages, model="", temperature=0.7, max_tokens=-1):
        import ai_base
        return ai_base._compute_cache_key(messages, model, temperature, max_tokens)

    def test_hit_refreshes_entry_ts_so_eviction_is_true_lru(self):
        """A cache HIT must update entry["ts"] to now."""
        import ai_base
        provider = self._make_provider({"content": "hi", "error": None})
        msgs = [{"role": "user", "content": "lru-hit-single"}]

        self.cached_chat_completion(provider, msgs)

        key = self._cache_key_for(msgs)
        with ai_base._RESPONSE_CACHE_LOCK:
            ts_after_insert = ai_base._RESPONSE_CACHE[key]["ts"]

        time.sleep(0.05)

        self.cached_chat_completion(provider, msgs)

        with ai_base._RESPONSE_CACHE_LOCK:
            ts_after_hit = ai_base._RESPONSE_CACHE[key]["ts"]

        self.assertGreater(
            ts_after_hit, ts_after_insert,
            "cache HIT must refresh entry['ts'] for true LRU",
        )

    def test_two_consecutive_hits_keep_refreshing_entry_ts(self):
        """Each HIT in a sequence must keep refreshing entry['ts']."""
        import ai_base
        provider = self._make_provider({"content": "hi", "error": None})
        msgs = [{"role": "user", "content": "lru-hit-double"}]

        self.cached_chat_completion(provider, msgs)

        key = self._cache_key_for(msgs)
        with ai_base._RESPONSE_CACHE_LOCK:
            ts0 = ai_base._RESPONSE_CACHE[key]["ts"]

        time.sleep(0.05)
        self.cached_chat_completion(provider, msgs)
        with ai_base._RESPONSE_CACHE_LOCK:
            ts1 = ai_base._RESPONSE_CACHE[key]["ts"]
        self.assertGreater(ts1, ts0, "first HIT must refresh entry['ts']")

        time.sleep(0.05)
        self.cached_chat_completion(provider, msgs)
        with ai_base._RESPONSE_CACHE_LOCK:
            ts2 = ai_base._RESPONSE_CACHE[key]["ts"]
        self.assertGreater(ts2, ts1, "second HIT must also refresh entry['ts']")

# =============================================================================
# Tests — invalidate_provider
# =============================================================================

class TestInvalidateProvider(unittest.TestCase):
    """invalidate_provider — clears the _PROVIDER_CACHE by name prefix or all."""

    def setUp(self):
        from ai_base import get_provider, invalidate_provider, reset_providers
        reset_providers()
        self.get_provider = get_provider
        self.invalidate_provider = invalidate_provider
        self.reset_providers = reset_providers

    def test_clear_all_with_empty_string(self):
        """Passing an empty string clears the entire cache."""
        self.get_provider(name="local_llm", default_model="test-model")
        import ai_base
        self.assertGreater(len(ai_base._PROVIDER_CACHE), 0)
        self.invalidate_provider("")
        self.assertEqual(len(ai_base._PROVIDER_CACHE), 0)

    def test_clear_specific_provider(self):
        """invalidate_provider('local_llm') drops local_llm entries."""
        self.get_provider(name="local_llm", default_model="model-A")
        import ai_base
        before_keys = set(ai_base._PROVIDER_CACHE.keys())
        self.assertTrue(any(k.startswith("local_llm:") for k in before_keys))
        self.invalidate_provider("local_llm")
        after_keys = set(ai_base._PROVIDER_CACHE.keys())
        self.assertFalse(any(k.startswith("local_llm:") for k in after_keys))

    def test_normalize_spaces_and_dashes(self):
        """'local llm' / 'local-llm' are normalized to 'local_llm'."""
        self.get_provider(name="local_llm", default_model="test")
        import ai_base
        self.assertTrue(any(k.startswith("local_llm:") for k in ai_base._PROVIDER_CACHE))
        self.invalidate_provider("local llm")
        self.assertFalse(any(k.startswith("local_llm:") for k in ai_base._PROVIDER_CACHE))

    def test_unknown_provider_name_is_noop(self):
        """Invalidating an unknown provider name clears nothing (no exception)."""
        self.get_provider(name="local_llm", default_model="test")
        import ai_base
        before_count = len(ai_base._PROVIDER_CACHE)
        self.invalidate_provider("nonexistent_provider")
        self.assertEqual(len(ai_base._PROVIDER_CACHE), before_count)

    def test_invalidate_then_re_get_returns_new_instance(self):
        """After invalidation, the next get_provider returns a fresh instance."""
        p1 = self.get_provider(name="local_llm", default_model="test")
        self.invalidate_provider("local_llm")
        p2 = self.get_provider(name="local_llm", default_model="test")
        self.assertIsNot(p1, p2)

    def test_reset_providers_preserves_dict_identity(self):
        """reset_providers() clears in-place (preserves dict identity).

        Any code that holds a reference to _PROVIDER_CACHE (e.g. via a
        closure or module-level alias) should still see the cleared state.
        If we reassigned the dict (ai_base._PROVIDER_CACHE = {}), those
        references would be stale. This contract is relied on by the
        conftest._reset_ai_caches_per_test fixture.
        """
        import ai_base
        from unittest.mock import patch
        cache_ref = ai_base._PROVIDER_CACHE
        original_id = id(cache_ref)
        with patch("ai_base._LLAMA_CPP_AVAILABLE", True), patch("ai_base.Llama"):
            ai_base.get_provider(default_model="identity_test")
        self.assertGreater(len(cache_ref), 0, "Sanity: cache should be polluted")
        ai_base.reset_providers()
        self.assertEqual(id(ai_base._PROVIDER_CACHE), original_id,
            "reset_providers() should clear in-place, not reassign the dict. ")
        self.assertEqual(len(cache_ref), 0)

    def test_provider_cache_scope_clears_on_exit(self):
        """provider_cache_scope() clears _PROVIDER_CACHE on exit."""
        import ai_base
        from unittest.mock import patch
        with ai_base.provider_cache_scope():
            with patch("ai_base._LLAMA_CPP_AVAILABLE", True), patch("ai_base.Llama"):
                ai_base.get_provider(default_model="inside_scope")
            self.assertEqual(len(ai_base._PROVIDER_CACHE), 1,
                "Cache should have exactly one provider inside the scope")
        # After exit, cache is cleared
        self.assertEqual(len(ai_base._PROVIDER_CACHE), 0,
            "Cache should be cleared on exit")

    def test_provider_cache_scope_clears_on_exception(self):
        """provider_cache_scope() clears the cache even when an exception is raised."""
        import ai_base
        from unittest.mock import patch
        with self.assertRaises(RuntimeError):
            with ai_base.provider_cache_scope():
                with patch("ai_base._LLAMA_CPP_AVAILABLE", True), patch("ai_base.Llama"):
                    ai_base.get_provider(default_model="will_raise")
                self.assertEqual(len(ai_base._PROVIDER_CACHE), 1)
                raise RuntimeError("simulated test failure")
        # Even though the exception propagated, the cache was cleared on exit
        self.assertEqual(len(ai_base._PROVIDER_CACHE), 0,
            "Cache should be cleared even when an exception is raised inside the scope")

    def test_provider_cache_scope_can_be_nested(self):
        """Nested provider_cache_scope() blocks compose correctly (each clears on its own exit)."""
        import ai_base
        from unittest.mock import patch
        with ai_base.provider_cache_scope():
            with patch("ai_base._LLAMA_CPP_AVAILABLE", True), patch("ai_base.Llama"):
                ai_base.get_provider(default_model="outer")
            self.assertEqual(len(ai_base._PROVIDER_CACHE), 1)
            with ai_base.provider_cache_scope():
                with patch("ai_base._LLAMA_CPP_AVAILABLE", True), patch("ai_base.Llama"):
                    ai_base.get_provider(default_model="inner")
                self.assertEqual(len(ai_base._PROVIDER_CACHE), 2)
            # Inner scope cleared on exit
            self.assertEqual(len(ai_base._PROVIDER_CACHE), 1,
                "Inner scope should have cleared its provider on exit")
        # Outer scope also cleared
        self.assertEqual(len(ai_base._PROVIDER_CACHE), 0,
            "Outer scope should have cleared the cache on exit")

# ===========================================================================
# Tests - retry_with_backoff (Sprint 15 Stream B)
# ===========================================================================

class TestRetryWithBackoff(unittest.TestCase):
    """Unit tests for ai_base.retry_with_backoff utility."""

    # -- Successful call

    def test_returns_result_on_success(self):
        """First call succeeds -> result returned, no retries."""
        from ai_base import retry_with_backoff
        fn = MagicMock(return_value=42)
        result = retry_with_backoff(fn, max_attempts=3, base_delay_ms=1, jitter=False)
        self.assertEqual(result, 42)
        fn.assert_called_once()

    # -- Exponential backoff growth

    def test_exponential_delay_growth(self):
        """Delays grow by backoff_factor between attempts."""
        from ai_base import retry_with_backoff
        delays = []
        fn = MagicMock(side_effect=[ValueError("1"), ValueError("2"), 99])
        result = retry_with_backoff(
            fn, max_attempts=3, base_delay_ms=100,
            max_delay_ms=10000, backoff_factor=3.0, jitter=False,
            on_retry=lambda a, d, e: delays.append(d),
        )
        self.assertEqual(result, 99)
        self.assertEqual(len(delays), 2)
        self.assertAlmostEqual(delays[0], 100, delta=1)
        self.assertAlmostEqual(delays[1], 300, delta=1)

    def test_delay_capped_at_max_delay(self):
        """Delay never exceeds max_delay_ms even with large backoff."""
        from ai_base import retry_with_backoff
        delays = []
        fn = MagicMock(side_effect=[ValueError("1"), ValueError("2"), ValueError("3"), 42])
        retry_with_backoff(
            fn, max_attempts=4, base_delay_ms=1000,
            max_delay_ms=2000, backoff_factor=10.0, jitter=False,
            on_retry=lambda a, d, e: delays.append(d),
        )
        self.assertEqual(len(delays), 3)
        self.assertAlmostEqual(delays[0], 1000, delta=1)
        self.assertAlmostEqual(delays[1], 2000, delta=1)
        self.assertAlmostEqual(delays[2], 2000, delta=1)

    # -- Jitter bounds (70-100%)

    def test_jitter_within_bounds(self):
        """With jitter enabled, delay is between 70% and 100% of computed."""
        from ai_base import retry_with_backoff
        delays = []
        fn = MagicMock(side_effect=[ValueError("1"), ValueError("2"), 42])
        retry_with_backoff(
            fn, max_attempts=3, base_delay_ms=100,
            max_delay_ms=10000, backoff_factor=2.0, jitter=True,
            on_retry=lambda a, d, e: delays.append(d),
        )
        self.assertEqual(len(delays), 2)
        # First retry delay: 100ms * (0.7 + jitter*0.3) -> [70, 100]
        self.assertGreaterEqual(delays[0], 70)
        self.assertLessEqual(delays[0], 100)

    def test_no_jitter_exact_delay(self):
        """With jitter=False, delay matches exact exponential value."""
        from ai_base import retry_with_backoff
        delays = []
        fn = MagicMock(side_effect=[ValueError("1"), ValueError("2"), 42])
        retry_with_backoff(
            fn, max_attempts=3, base_delay_ms=200,
            max_delay_ms=10000, backoff_factor=2.0, jitter=False,
            on_retry=lambda a, d, e: delays.append(d),
        )
        self.assertEqual(len(delays), 2)
        self.assertAlmostEqual(delays[0], 200, delta=1)

    # -- cancel_event

    def test_cancel_event_breaks_sleep_loop(self):
        """If cancel_event is set before retry, loop breaks immediately."""
        import threading
        from ai_base import retry_with_backoff
        fn = MagicMock(side_effect=[ValueError("1"), ValueError("2"), 42])
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ValueError):
            retry_with_backoff(
                fn, max_attempts=3, base_delay_ms=10000,
                max_delay_ms=10000, backoff_factor=1.0, jitter=False,
                cancel_event=cancel,
            )
        self.assertEqual(fn.call_count, 1)

    def test_cancel_event_not_set_allows_retry(self):
        """If cancel_event is not set, retries proceed normally."""
        import threading
        from ai_base import retry_with_backoff
        fn = MagicMock(side_effect=[ValueError("fail"), "ok"])
        cancel = threading.Event()
        result = retry_with_backoff(
            fn, max_attempts=2, base_delay_ms=1,
            max_delay_ms=10, backoff_factor=1.0, jitter=False,
            cancel_event=cancel,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(fn.call_count, 2)

    # -- on_retry callback

    def test_on_retry_called_with_correct_args(self):
        """on_retry(attempt, delay_ms, exception) called before each retry."""
        from ai_base import retry_with_backoff
        calls = []
        fn = MagicMock(side_effect=[ValueError("err1"), ValueError("err2"), 42])
        def capture(attempt, delay_ms, exc):
            calls.append((attempt, delay_ms, type(exc).__name__))
        retry_with_backoff(
            fn, max_attempts=3, base_delay_ms=50,
            max_delay_ms=10000, backoff_factor=2.0, jitter=False,
            on_retry=capture,
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], 1)
        self.assertAlmostEqual(calls[0][1], 50, delta=1)
        self.assertEqual(calls[0][2], "ValueError")
        self.assertEqual(calls[1][0], 2)
        self.assertAlmostEqual(calls[1][1], 100, delta=1)
        self.assertEqual(calls[1][2], "ValueError")

    def test_on_retry_exception_does_not_propagate(self):
        """If on_retry raises, the retry loop continues normally."""
        from ai_base import retry_with_backoff
        def bad_callback(a, d, e):
            raise RuntimeError("callback crashed")
        fn = MagicMock(side_effect=[ValueError("1"), "ok"])
        result = retry_with_backoff(
            fn, max_attempts=2, base_delay_ms=1,
            max_delay_ms=10, backoff_factor=1.0, jitter=False,
            on_retry=bad_callback,
        )
        self.assertEqual(result, "ok")

    # -- Max attempts exhaustion

    def test_max_attempts_exhaustion_raises_last_exception(self):
        """After max_attempts failures, raises the last exception."""
        from ai_base import retry_with_backoff
        fn = MagicMock(side_effect=[ValueError("e1"), TypeError("e2"), RuntimeError("e3")])
        with self.assertRaises(RuntimeError) as ctx:
            retry_with_backoff(
                fn, max_attempts=3, base_delay_ms=1,
                max_delay_ms=10, backoff_factor=1.0, jitter=False,
            )
        self.assertIn("e3", str(ctx.exception))
        self.assertEqual(fn.call_count, 3)

    def test_single_attempt_raises_immediately(self):
        """With max_attempts=1, no retry happens, exception raised."""
        from ai_base import retry_with_backoff
        fn = MagicMock(side_effect=ValueError("one shot"))
        with self.assertRaises(ValueError) as ctx:
            retry_with_backoff(
                fn, max_attempts=1, base_delay_ms=100,
                max_delay_ms=10000, backoff_factor=2.0, jitter=False,
            )
        self.assertIn("one shot", str(ctx.exception))
        fn.assert_called_once()

    def test_max_attempts_1_no_on_retry_called(self):
        """With max_attempts=1, on_retry should never be invoked."""
        from ai_base import retry_with_backoff
        fn = MagicMock(side_effect=ValueError("fail"))
        on_retry = MagicMock()
        with self.assertRaises(ValueError):
            retry_with_backoff(
                fn, max_attempts=1, base_delay_ms=100,
                max_delay_ms=10000, backoff_factor=2.0, jitter=False,
                on_retry=on_retry,
            )
        on_retry.assert_not_called()

# =============================================================================
# Tests -- LocalLLMProvider.health_check() thread safety
# =============================================================================

class TestHealthCheckInferenceLock(unittest.TestCase):
    """health_check() correctly skips the benchmark when _inference_lock
    is held by a concurrent chat_completion."""

    def setUp(self):
        from ai_base import LocalLLMProvider, reset_providers
        reset_providers()
        self.provider = LocalLLMProvider(default_model="test-model")
        # Clear model_file so health_check cannot trigger a real model load
        self.provider.model_file = ""

    def tearDown(self):
        from ai_base import reset_providers
        reset_providers()

    def test_health_check_skips_benchmark_when_inference_lock_held(self):
        """When _inference_lock is held by chat_completion, benchmark is skipped.

        Simulates a concurrent chat_completion by acquiring _inference_lock
        in a background thread, then calling health_check() and verifying
        the 1-token benchmark is skipped (create_chat_completion not called,
        latency_ms stays 0).
        """
        self.provider._model_instance = MagicMock()
        self.provider._current_model_path = "/models/test.gguf"
        self.provider._loaded = True
        self.provider.model_file = "/models/test.gguf"
        lock_held = threading.Event()
        lock_release = threading.Event()

        def hold_lock():
            self.provider._inference_lock.acquire()
            lock_held.set()
            lock_release.wait(timeout=5)
            self.provider._inference_lock.release()

        t = threading.Thread(target=hold_lock)
        t.start()
        lock_held.wait(timeout=5)
        try:
            result = self.provider.health_check()
            self.provider._model_instance.create_chat_completion.assert_not_called()
            self.assertEqual(result["latency_ms"], 0,
                "Benchmark should be skipped when inference is running")
        finally:
            lock_release.set()
            t.join(timeout=5)


# =============================================================================
# Tests -- LocalLLMProvider.scan_models (recursive)
# =============================================================================

class TestScanModelsRecursive(unittest.TestCase):
    """scan_models must find .gguf files in subdirectories, not just the top
    level — models organized as <family>/<quant>.gguf were invisible to the
    model dropdown (the empty-dropdown bug, 2026-09-24)."""

    def _scan(self, tmp):
        from ai_base import LocalLLMProvider
        return LocalLLMProvider.scan_models(tmp)

    def test_finds_models_in_subdirectories(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sub = os.path.join(tmp, "FamilyA")
            os.makedirs(sub)
            open(os.path.join(sub, "m-Q4.gguf"), "wb").close()
            res = self._scan(tmp)
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["path"], os.path.join(sub, "m-Q4.gguf"))
            self.assertEqual(res[0]["name"], "m-Q4.gguf")

    def test_duplicate_basenames_get_relative_names(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for fam in ("A", "B"):
                sub = os.path.join(tmp, fam)
                os.makedirs(sub)
                open(os.path.join(sub, "dup.gguf"), "wb").close()
            res = self._scan(tmp)
            names = {m["name"] for m in res}
            self.assertEqual(len(res), 2)
            self.assertNotIn("dup.gguf", names)
            self.assertIn(os.path.join("A", "dup.gguf"), names)
            self.assertIn(os.path.join("B", "dup.gguf"), names)

    def test_unique_names_stay_bare_filenames(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "top.gguf"), "wb").close()
            sub = os.path.join(tmp, "Nested")
            os.makedirs(sub)
            open(os.path.join(sub, "deep.gguf"), "wb").close()
            res = self._scan(tmp)
            names = {m["name"] for m in res}
            self.assertEqual(names, {"top.gguf", "deep.gguf"})

    def test_non_gguf_ignored_and_missing_dir_safe(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "notes.txt"), "wb").close()
            self.assertEqual(self._scan(tmp), [])
        self.assertEqual(self._scan(os.path.join("Z:", "definitely-missing")), [])


class TestIsProjectorModel(unittest.TestCase):
    """is_projector_model must identify mmproj projector adapters so the main
    model pickers (wizard + Settings) can hide them, while the vision-model
    picker keeps them (the user's vision model IS an mmproj file)."""

    def test_mmproj_basename_matches(self):
        from ai_base import is_projector_model
        self.assertTrue(is_projector_model(
            "mmproj-Ministral-3-3B-Instruct-2512-F16.gguf"))

    def test_mmproj_full_path_matches_on_basename(self):
        from ai_base import is_projector_model
        self.assertTrue(is_projector_model(
            os.path.join("C:", "m", "Ministral-3-3B-Instruct-2512-GGUF",
                         "mmproj-Ministral-3-3B-Instruct-2512-F16.gguf")))

    def test_regular_model_does_not_match(self):
        from ai_base import is_projector_model
        self.assertFalse(is_projector_model(
            "Ministral-3-3B-Instruct-2512-Q4_K_M.gguf"))

    def test_qwen_regular_model_does_not_match(self):
        from ai_base import is_projector_model
        self.assertFalse(is_projector_model("Qwen3-VL-4B-Instruct-Q4_K_M.gguf"))

    def test_vision_word_inside_name_does_not_match(self):
        """Only names STARTING with a projector marker count — a model that
        merely contains "mmproj" mid-name is a real model."""
        from ai_base import is_projector_model
        self.assertFalse(is_projector_model("Ministral-mmproj-note.gguf"))

    def test_empty_and_non_str_are_safe(self):
        from ai_base import is_projector_model
        self.assertFalse(is_projector_model(""))
        self.assertFalse(is_projector_model(None))
        self.assertFalse(is_projector_model(123))

    def test_mmproj_counted_as_vision_model(self):
        """Adding "mmproj" to VISION_MODELS means the Settings vision-model
        refresh (which filters via _is_vision_model) now lists projector
        files — previously they were undetectable there."""
        from ai_base import _is_vision_model
        self.assertTrue(_is_vision_model(
            "mmproj-Qwen3-VL-4B-Instruct-F16.gguf"))


# ===========================================================================
# Tests — swap_model ordering + verbose _load_model (stuck-Apply regression)
# ===========================================================================

class TestSwapModelSafety(unittest.TestCase):
    """swap_model must resolve the new path BEFORE unloading the old
    model, report progress, and roll back cleanly on failure."""

    def _make_provider(self, tmp):
        from ai_base import LocalLLMProvider
        p = LocalLLMProvider(models_dir=tmp, model_file="a.gguf")
        return p

    def test_missing_file_fails_without_unloading_current(self):
        """A bad filename must never unload the working model."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self._make_provider(tmp)
            sentinel = object()
            p._model_instance = sentinel
            p._model_path = os.path.join(tmp, "a.gguf")
            result = p.swap_model("does_not_exist.gguf")
            self.assertFalse(result["ok"])
            self.assertIn("not found", result["error"])
            self.assertIs(p._model_instance, sentinel,
                          "current model was unloaded for a nonexistent file")

    def test_progress_callback_receives_load_steps(self):
        """A successful load reports unload + loading + loaded steps."""
        import tempfile
        from unittest.mock import patch as _patch
        with tempfile.TemporaryDirectory() as tmp:
            gguf = os.path.join(tmp, "new.gguf")
            open(gguf, "wb").close()
            p = self._make_provider(tmp)
            events = []
            fake = type("FakeLlama", (), {"__init__": lambda self, **kw: None})
            with _patch.dict(sys.modules, {"llama_cpp": _FakeLlamaModule(fake)}):
                result = p.swap_model("new.gguf", progress=events.append)
            self.assertTrue(result["ok"])
            joined = "\n".join(events)
            self.assertIn("Loading new.gguf", joined)
            self.assertIn("Model loaded", joined)
            self.assertIn("Unloading", joined)

    def test_load_failure_reports_error_and_rolls_back(self):
        """A failing load surfaces the error via progress and restores
        the previous model file."""
        import tempfile
        from unittest.mock import patch as _patch
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("old.gguf", "bad.gguf"):
                open(os.path.join(tmp, name), "wb").close()
            p = self._make_provider(tmp)
            p.model_file = "old.gguf"          # consistent with rollback target
            p._model_path = os.path.join(tmp, "old.gguf")
            events = []

            calls = {"n": 0}
            class Boom:
                def __init__(self, **kw):
                    calls["n"] += 1
                    if kw.get("model_path", "").endswith("bad.gguf"):
                        raise RuntimeError("gguf corrupt")

            with _patch.dict(sys.modules, {"llama_cpp": _FakeLlamaModule(Boom)}):
                result = p.swap_model("bad.gguf", progress=events.append)
            self.assertFalse(result["ok"])
            self.assertIn("Failed to load model", result["error"])
            joined = "\n".join(events)
            self.assertIn("Load failed", joined)
            self.assertIn("Rolling back", joined)
            self.assertEqual(p.model_file, "old.gguf")

    def test_load_model_progress_failure_never_breaks_load(self):
        """A raising progress sink must not break the load itself."""
        import tempfile
        from unittest.mock import patch as _patch
        with tempfile.TemporaryDirectory() as tmp:
            gguf = os.path.join(tmp, "m.gguf")
            open(gguf, "wb").close()
            p = self._make_provider(tmp)

            def bad_sink(_msg):
                raise ValueError("sink exploded")

            fake = type("FakeLlama", (), {"__init__": lambda self, **kw: None})
            with _patch.dict(sys.modules, {"llama_cpp": _FakeLlamaModule(fake)}):
                ok = p._load_model(gguf, progress=bad_sink)
            self.assertTrue(ok, "progress-sink failure must never fail a load")


# ===========================================================================
# Tests — LocalLLMProvider._resolve_model_path recursive & multi-root lookup
# ===========================================================================

class TestResolveModelPath(unittest.TestCase):
    """REGRESSION GUARD for model file resolution in
    ``ai_base.LocalLLMProvider._resolve_model_path``.

    Ensures that bare filenames in subdirectories (e.g.
    ``models/Ministral-3-3B-Instruct-2512-GGUF/Ministral-3-3B-Instruct-2512-Q4_K_M.gguf``),
    paths without extension, and relative subpaths resolve deterministically
    across models_dir, CONFIG['models_dir'], and app roots.
    """

    def test_resolves_bare_filename_in_subdirectory(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subdir = os.path.join(tmp, "Subdir-Model-GGUF")
            os.makedirs(subdir)
            target = os.path.join(subdir, "my-model-Q4_K_M.gguf")
            open(target, "wb").close()

            from ai_base import LocalLLMProvider
            p = LocalLLMProvider(models_dir=tmp)
            resolved = p._resolve_model_path("my-model-Q4_K_M.gguf")
            self.assertEqual(os.path.abspath(resolved), os.path.abspath(target))

    def test_resolves_without_extension(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subdir = os.path.join(tmp, "Family-GGUF")
            os.makedirs(subdir)
            target = os.path.join(subdir, "model-instruct.gguf")
            open(target, "wb").close()

            from ai_base import LocalLLMProvider
            p = LocalLLMProvider(models_dir=tmp)
            resolved = p._resolve_model_path("model-instruct")
            self.assertEqual(os.path.abspath(resolved), os.path.abspath(target))

    def test_resolves_relative_subfolder_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            subdir = os.path.join(tmp, "Family-GGUF")
            os.makedirs(subdir)
            target = os.path.join(subdir, "model.gguf")
            open(target, "wb").close()

            from ai_base import LocalLLMProvider
            p = LocalLLMProvider(models_dir=tmp)
            rel = os.path.join("Family-GGUF", "model.gguf")
            resolved = p._resolve_model_path(rel)
            self.assertEqual(os.path.abspath(resolved), os.path.abspath(target))

    def test_resolves_absolute_path_directly(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "absolute-model.gguf")
            open(target, "wb").close()

            from ai_base import LocalLLMProvider
            p = LocalLLMProvider(models_dir="/some/other/dir")
            resolved = p._resolve_model_path(target)
            self.assertEqual(os.path.abspath(resolved), os.path.abspath(target))

    def test_missing_model_returns_empty_string(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            from ai_base import LocalLLMProvider
            p = LocalLLMProvider(models_dir=tmp)
            self.assertEqual(p._resolve_model_path("nonexistent.gguf"), "")
            self.assertEqual(p._resolve_model_path(""), "")
            self.assertEqual(p._resolve_model_path("   "), "")


class _FakeLlamaModule:
    """Minimal stand-in for the llama_cpp module in swap/load tests."""

    def __init__(self, llama_cls):
        self.Llama = llama_cls


class TestCircuitBreaker(unittest.TestCase):
    """Unit tests for CircuitBreaker failure tracking, allow_request, and cooldown."""

    def test_initial_state_allows_request(self):
        from ai_base import CircuitBreaker
        cb = CircuitBreaker(max_failures=3, reset_timeout=10.0)
        self.assertTrue(cb.allow_request())
        self.assertFalse(cb.is_open)
        self.assertEqual(cb.remaining_cooldown(), 0.0)

    def test_failures_under_threshold_allow_request(self):
        from ai_base import CircuitBreaker
        cb = CircuitBreaker(max_failures=3, reset_timeout=10.0)
        cb.record_failure()
        cb.record_failure()
        self.assertTrue(cb.allow_request())
        self.assertFalse(cb.is_open)
        self.assertEqual(cb.remaining_cooldown(), 0.0)

    def test_tripped_circuit_denies_request(self):
        from ai_base import CircuitBreaker
        cb = CircuitBreaker(max_failures=3, reset_timeout=10.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        self.assertFalse(cb.allow_request())
        self.assertTrue(cb.is_open)
        self.assertGreater(cb.remaining_cooldown(), 0.0)
        self.assertLessEqual(cb.remaining_cooldown(), 10.0)

    def test_record_success_resets_failures(self):
        from ai_base import CircuitBreaker
        cb = CircuitBreaker(max_failures=3, reset_timeout=10.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        self.assertTrue(cb.allow_request())

    def test_manual_reset(self):
        from ai_base import CircuitBreaker
        cb = CircuitBreaker(max_failures=2, reset_timeout=10.0)
        cb.record_failure()
        cb.record_failure()
        self.assertFalse(cb.allow_request())
        cb.reset()
        self.assertTrue(cb.allow_request())
        self.assertFalse(cb.is_open)
        self.assertEqual(cb.remaining_cooldown(), 0.0)

    def test_cooldown_expiry_allows_request(self):
        import time
        from ai_base import CircuitBreaker
        cb = CircuitBreaker(max_failures=2, reset_timeout=0.05)
        cb.record_failure()
        cb.record_failure()
        self.assertFalse(cb.allow_request())
        time.sleep(0.06)
        self.assertTrue(cb.allow_request())
        self.assertFalse(cb.is_open)
        self.assertEqual(cb.remaining_cooldown(), 0.0)


# =============================================================================
# Tests — LocalLLMProvider context budget clamping and n_ctx config sync
# =============================================================================

class TestLocalLLMContextBudgetClamping(unittest.TestCase):
    """Verifies that LocalLLMProvider respects CONFIG[llm_n_ctx], clamps
    max_tokens against remaining context window budget, and does not trip
    circuit breaker on prompt context overflow."""

    def setUp(self):
        from ai_base import reset_providers, get_circuit_breaker
        reset_providers()
        get_circuit_breaker().reset()

    def tearDown(self):
        from ai_base import reset_providers, get_circuit_breaker
        reset_providers()
        get_circuit_breaker().reset()

    def test_get_provider_respects_llm_n_ctx(self):
        from config import CONFIG
        from ai_base import get_provider
        old_ctx = CONFIG.get("llm_n_ctx")
        try:
            CONFIG["llm_n_ctx"] = 4096
            provider = get_provider(name="test_ctx_sync", default_model="test.gguf")
            self.assertEqual(provider.configured_n_ctx, 4096)
        finally:
            if old_ctx is not None:
                CONFIG["llm_n_ctx"] = old_ctx

    def test_chat_completion_clamps_max_tokens_to_remaining_budget(self):
        from ai_base import LocalLLMProvider
        provider = LocalLLMProvider(default_model="test.gguf", n_ctx=2048)
        mock_instance = MagicMock()
        mock_instance.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "ok"}}]
        }
        provider._model_instance = mock_instance
        provider.model_file = "test.gguf"

        # A prompt of ~4500 characters (~1400 tokens)
        long_text = "word " * 900
        messages = [{"role": "user", "content": long_text}]

        provider.chat_completion(messages, max_tokens=1024)
        mock_instance.create_chat_completion.assert_called_once()
        called_kwargs = mock_instance.create_chat_completion.call_args[1]
        called_max_tokens = called_kwargs["max_tokens"]

        # Prompt was ~1000 tokens. Total budget is 2048.
        # Max tokens should have been clamped well below 1024 so it fits within 2048.
        self.assertLess(called_max_tokens, 1024)
        self.assertGreaterEqual(called_max_tokens, 16)

    def test_context_overflow_error_does_not_trip_circuit_breaker(self):
        from ai_base import LocalLLMProvider, get_circuit_breaker
        cb = get_circuit_breaker()
        cb.reset()

        provider = LocalLLMProvider(default_model="test.gguf", n_ctx=2048)
        mock_instance = MagicMock()
        mock_instance.create_chat_completion.side_effect = ValueError(
            "Requested tokens (2050) exceed context window of 2048"
        )
        provider._model_instance = mock_instance
        provider.model_file = "test.gguf"

        res = provider.chat_completion([{"role": "user", "content": "hello"}])
        self.assertIn("Requested tokens (2050) exceed context window of 2048", res.get("error", ""))
        # Circuit breaker should NOT have recorded a failure
        self.assertEqual(cb._failures, 0)
        self.assertFalse(cb.is_open)

    def test_swap_model_accepts_and_updates_n_ctx(self):
        from ai_base import LocalLLMProvider
        provider = LocalLLMProvider(default_model="test.gguf", n_ctx=2048)
        mock_instance = MagicMock()
        provider._model_instance = mock_instance
        provider._model_path = "/models/test.gguf"
        provider.model_file = "test.gguf"

        with patch.object(provider, "_resolve_model_path", return_value="/models/new_model.gguf"):
            with patch.object(provider, "_load_model", return_value=True):
                result = provider.swap_model("new_model.gguf", n_ctx=8192)
                self.assertTrue(result["ok"])
                self.assertEqual(provider.configured_n_ctx, 8192)


class TestNormalizeChatMessages(unittest.TestCase):
    """REGRESSION GUARD: Prevent chat template role alternation failures.
    Mistral/Ministral chat templates strictly enforce alternating user/assistant roles.
    Consecutive user messages or misplaced system messages must be safely normalized.
    """

    def test_normalize_empty_and_invalid_inputs(self):
        from ai_base import _normalize_chat_messages
        self.assertEqual(_normalize_chat_messages([]), [{"role": "user", "content": ""}])
        self.assertEqual(_normalize_chat_messages(None), [{"role": "user", "content": ""}])
        self.assertEqual(_normalize_chat_messages("invalid"), [{"role": "user", "content": ""}])

    def test_normalize_extracts_system_messages_to_front(self):
        from ai_base import _normalize_chat_messages
        raw = [
            {"role": "user", "content": "Hello"},
            {"role": "system", "content": "You are a helper."},
            {"role": "assistant", "content": "Hi there!"},
        ]
        norm = _normalize_chat_messages(raw)
        self.assertEqual(norm[0]["role"], "system")
        self.assertEqual(norm[0]["content"], "You are a helper.")
        self.assertEqual(norm[1]["role"], "user")
        self.assertEqual(norm[1]["content"], "Hello")
        self.assertEqual(norm[2]["role"], "assistant")
        self.assertEqual(norm[2]["content"], "Hi there!")

    def test_normalize_coalesces_consecutive_user_messages(self):
        from ai_base import _normalize_chat_messages
        raw = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "First instruction"},
            {"role": "user", "content": "Second instruction"},
        ]
        norm = _normalize_chat_messages(raw)
        self.assertEqual(len(norm), 2)
        self.assertEqual(norm[0]["role"], "system")
        self.assertEqual(norm[1]["role"], "user")
        self.assertEqual(norm[1]["content"], "First instruction\n\nSecond instruction")

    def test_normalize_coalesces_consecutive_assistant_messages(self):
        from ai_base import _normalize_chat_messages
        raw = [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Part 1"},
            {"role": "assistant", "content": "Part 2"},
            {"role": "user", "content": "Follow up"},
        ]
        norm = _normalize_chat_messages(raw)
        self.assertEqual(len(norm), 3)
        self.assertEqual(norm[0]["role"], "user")
        self.assertEqual(norm[1]["role"], "assistant")
        self.assertEqual(norm[1]["content"], "Part 1\n\nPart 2")
        self.assertEqual(norm[2]["role"], "user")

    def test_chat_completion_normalizes_messages_before_inference(self):
        from ai_base import LocalLLMProvider
        provider = LocalLLMProvider(default_model="test.gguf", n_ctx=2048)
        mock_instance = MagicMock()
        mock_instance.create_chat_completion.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "OK"}}]
        }
        provider._model_instance = mock_instance
        provider.model_file = "test.gguf"

        provider.chat_completion([
            {"role": "user", "content": "msg 1"},
            {"role": "user", "content": "msg 2"},
        ])

        sent_kwargs = mock_instance.create_chat_completion.call_args[1]
        sent_messages = sent_kwargs["messages"]
        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0]["role"], "user")
        self.assertEqual(sent_messages[0]["content"], "msg 1\n\nmsg 2")


if __name__ == "__main__":
    unittest.main()

