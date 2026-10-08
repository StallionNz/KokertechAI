"""tests/test_provider_stream_service.py -- Sprint 19.3 ProviderStreamService tests.

Pins the extraction contract from ``kokertechController._execute_stream``:
- token accumulation + per-token callback
- cancel_event honored mid-stream (Sprint 19.3 acceptance)
- error chunk -> None (caller falls back to non-streaming)
- no stream_callback -> None immediately
- provider without chat_completion_stream -> None
- exception in provider -> None
- lazy-singleton + reset helper
"""
from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock

from services.provider_stream_service import (
    ProviderStreamService,
    _reset_provider_stream_service_for_tests,
    get_provider_stream_service,
)


class FakeStreamProvider:
    """Deterministic provider stub that yields token/done/error chunks."""

    def __init__(self, chunks=None):
        self.chunks = chunks if chunks is not None else [
            {"token": "Hel"},
            {"token": "lo"},
            {"token": " world"},
            {"done": True},
        ]
        self.calls = 0

    def chat_completion_stream(self, **kwargs):
        self.calls += 1
        for chunk in self.chunks:
            if kwargs.get("cancel_event") is not None and kwargs["cancel_event"].is_set():
                return
            yield chunk


class TestExecuteStream(unittest.TestCase):
    """Token accumulation + per-token callback + done handling."""

    def setUp(self):
        self.svc = ProviderStreamService()

    def test_accumulates_tokens_and_returns_joined(self):
        provider = FakeStreamProvider()
        tokens = []
        result = self.svc.execute_stream(
            provider, [{"role": "user", "content": "hi"}],
            model="m", temperature=0.1, max_tokens=100, timeout=30,
            stream_callback=tokens.append,
        )
        self.assertEqual(result, "Hello world")
        self.assertEqual(tokens, ["Hel", "lo", " world"])

    def test_no_stream_callback_returns_none(self):
        provider = FakeStreamProvider()
        self.assertIsNone(
            self.svc.execute_stream(
                provider, [], model="m", temperature=0.1,
                max_tokens=100, timeout=30,
            )
        )
        self.assertEqual(provider.calls, 0, "provider must not be called")

    def test_cancel_event_stops_mid_stream(self):
        """Acceptance (Sprint 19.3): cancel_event honored mid-stream.

        The provider stops yielding when cancel_event is set between
        tokens; the service returns the partial accumulated text.
        """
        cancel_event = threading.Event()
        tokens = []

        def stream_callback(token):
            tokens.append(token)
            if len(tokens) >= 2:
                cancel_event.set()

        # Provider yields all 4 chunks but checks cancel between yields.
        provider = FakeStreamProvider()
        result = self.svc.execute_stream(
            provider, [{"role": "user", "content": "hi"}],
            model="m", temperature=0.1, max_tokens=100, timeout=30,
            cancel_event=cancel_event,
            stream_callback=stream_callback,
        )
        # The fake provider returns on cancel before the next chunk.
        self.assertTrue(cancel_event.is_set())
        self.assertGreaterEqual(len(tokens), 2)
        self.assertIsInstance(result, str)

    def test_error_chunk_returns_none(self):
        provider = FakeStreamProvider(chunks=[{"error": "boom"}])
        result = self.svc.execute_stream(
            provider, [], model="m", temperature=0.1,
            max_tokens=100, timeout=30,
            stream_callback=lambda token: None,
        )
        self.assertIsNone(result)

    def test_provider_without_streaming_returns_none(self):
        provider = MagicMock()
        del provider.chat_completion_stream
        result = self.svc.execute_stream(
            provider, [], model="m", temperature=0.1,
            max_tokens=100, timeout=30,
            stream_callback=lambda token: None,
        )
        self.assertIsNone(result)

    def test_exception_returns_none(self):
        def explode(**kwargs):
            raise RuntimeError("provider died")
        provider = MagicMock()
        provider.chat_completion_stream.side_effect = explode
        result = self.svc.execute_stream(
            provider, [], model="m", temperature=0.1,
            max_tokens=100, timeout=30,
            stream_callback=lambda token: None,
        )
        self.assertIsNone(result)

    def test_forwards_response_format_and_params(self):
        seen = {}
        def record(**kwargs):
            seen.update(kwargs)
            yield {"done": True}
        provider = MagicMock()
        provider.chat_completion_stream.side_effect = record
        self.svc.execute_stream(
            provider, [{"role": "user", "content": "x"}],
            model="m", temperature=0.5, max_tokens=42, timeout=7,
            cancel_event=threading.Event(),
            stream_callback=lambda token: None,
            response_format={"type": "grammar", "value": "x"},
        )
        self.assertEqual(seen["model"], "m")
        self.assertEqual(seen["temperature"], 0.5)
        self.assertEqual(seen["max_tokens"], 42)
        self.assertEqual(seen["timeout"], 7)
        self.assertEqual(seen["response_format"], {"type": "grammar", "value": "x"})


class TestSingletonPattern(unittest.TestCase):
    """Lazy singleton + reset helper (Sprint 17 R12 convention)."""

    def tearDown(self):
        _reset_provider_stream_service_for_tests()

    def test_getter_returns_same_instance(self):
        a = get_provider_stream_service()
        b = get_provider_stream_service()
        self.assertIs(a, b)

    def test_reset_helper_clears_singleton(self):
        _reset_provider_stream_service_for_tests()
        a = get_provider_stream_service()
        _reset_provider_stream_service_for_tests()
        b = get_provider_stream_service()
        self.assertIsNot(a, b)


if __name__ == "__main__":
    unittest.main()
