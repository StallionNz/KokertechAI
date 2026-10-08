"""
tests/test_call_chain_e2e.py -- End-to-end integration test for the full
Dashboard -> Controller -> Provider -> Parse -> Vault call chain.

Closes the call-chain verification gap: uses a real AIProvider subclass
(NOT a unittest.mock.MagicMock) with deterministic XML responses, wired
through the production KokertechController -> process_input -> parse_ai ->
store_memory pipeline.  No QApplication required -- tests run directly
against the controller with a tempdir-backed vault.

Design:
  - _TestProvider(AIProvider) returns deterministic <thinking>/<final_output>
    XML, exercising the real parse_ai() code path in prompt_service.py.
  - Controller is instantiated with the test provider via ai_base._PROVIDER_CACHE
    injection, bypassing the model-loading code path entirely.
  - memory_vault DB_PATH is patched to a tempdir so the real vault is
    never touched.
  - Every assertion traces a specific stage in the production pipeline.
"""
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

from ai_base import AIProvider


# ==================================================================
# _TestProvider -- real AIProvider with deterministic XML responses
# ==================================================================

class _TestProvider(AIProvider):
    """A real AIProvider implementation that returns deterministic XML.

    NOT a MagicMock -- this is a genuine Python class implementing the
    AIProvider interface.  The controller's process_input() calls
    chat_completion() on this provider, receives the XML, and runs the
    full production parse_ai / store_memory pipeline on the result.

    Each test can configure the response by setting ``xml_response``
    before calling process_input().  Default returns a valid
    <thinking>/<final_output> pair.
    """

    def __init__(self):
        super().__init__(default_model="test-model")
        self.xml_response = (
            "<thinking>Processing the user query step by step.</thinking>\n"
            "<final_output>This is the AI response to the test query.</final_output>"
        )
        self._call_count = 0
        self._call_records = []  # track every call (messages, model, temp)
        self._stream_call_count = 0  # specifically track streaming calls
        self._stream_should_fail = False  # when True, stream yields error chunk

    def chat_completion(self, messages, model="", temperature=0.7,
                        max_tokens=-1, timeout=30, request_id="",
                        cancel_event=None, response_format=None,
                        tools=None, cache_prompt=None):
        """Return a structured XML response matching the production shape.

        Respects ``cancel_event``: if set before or during the call,
        returns an error result (matching production behaviour).
        """
        self._call_count += 1
        self._call_records.append({
            "messages": messages,
            "model": model,
            "temperature": temperature,
        })
        if cancel_event and cancel_event.is_set():
            return {
                "content": "",
                "error": "Operation cancelled by user",
                "tool_calls": [],
            }
        return {
            "content": self.xml_response,
            "error": None,
            "tool_calls": [],
        }

    def chat_completion_stream(self, messages, model="", temperature=0.7,
                                max_tokens=-1, timeout=30, request_id="",
                                cancel_event=None, response_format=None,
                                tools=None, cache_prompt=None):
        """Stream the XML response token by token, or yield an error.

        Respects ``cancel_event``: if set before or during iteration,
        stops yielding immediately (matching production LocalLLMProvider
        behaviour at ai_base.py line 882).
        """
        self._call_count += 1
        self._stream_call_count += 1
        self._call_records.append({
            "messages": messages,
            "model": model,
            "temperature": temperature,
        })
        if self._stream_should_fail:
            yield {"error": "stream connection lost"}
            return
        for token in self.xml_response.split():
            if cancel_event and cancel_event.is_set():
                return
            yield {"token": token + " "}
        return


# ==================================================================
# Test class
# ==================================================================

class TestCallChainEndToEnd(unittest.TestCase):
    """End-to-end tests for the full Controller -> Provider -> Parse -> Vault chain.

    Each test:
      1. Creates a _TestProvider and injects it into the provider cache.
      2. Instantiates a KokertechController with a tempdir vault.
      3. Calls process_input().
      4. Asserts on every stage of the pipeline.
    """

    # -- Setup / teardown --------------------------------------------

    def setUp(self):
        # Temp workspace with its own vault DB
        self.tmpdir = tempfile.mkdtemp(prefix="e2e_chain_")
        self.vault_path = os.path.join(self.tmpdir, "kokertech_vault.db")

        # Reset memory_vault module-level state so ensure_tables_exist()
        # actually creates tables in the temp vault (not a stale real vault).
        import memory_vault
        memory_vault._db_initialized = False
        # Drain the connection pool so stale connections bound to a prior
        # test's temp vault (or the real vault) are never reused against
        # this fresh DB file (KNOWLEDGE.md section 20.6 bug class). The
        # path-aware _get_conn() guard is the root-cause fix; this explicit
        # drain is defense-in-depth + releases Windows file locks.
        memory_vault._clear_connection_pool()

        # Reset the services registry singleton so history/context state
        # from prior tests doesn't leak across test method boundaries.
        from services import get_services
        try:
            get_services().reset()
        except Exception:
            pass

        # Create and register the test provider BEFORE controller init
        self.test_provider = _TestProvider()
        import ai_base
        self._provider_patcher = patch.dict(
            ai_base._PROVIDER_CACHE,
            {"local_llm": self.test_provider},
            clear=True,
        )
        self._provider_patcher.start()

        # Point memory_vault.DB_PATH at our tempdir
        self._db_patcher = patch("memory_vault.DB_PATH", self.vault_path)
        self._db_patcher.start()

        # Patch CONFIG to disable features that need real models / network.
        # freeform_mode is pinned False because these tests assert
        # structured-mode parse_ai semantics (<thinking>/<final_output>
        # extraction); leaving it unset leaks the developer/user's real
        # app_settings.json preference ("freeform_mode": true) through the
        # clear=False patch and silently routes process_input through the
        # freeform parser (thinking="", final=raw XML, error=None). The
        # freeform-specific tests override this with their own patch.dict.
        self._cfg_patcher = patch.dict("config.CONFIG", {
            "active_provider": "local_llm",
            "model_name": "test-model",
            "freeform_mode": False,
            "mock_mode": False,
            "god_reviewer_contract": False,
            "grammar_hot_reload": False,
            "tool_use_audit_log": False,
            "rag_enable_web_search": False,
            "graphrag_llm_extraction": False,
            "tts_enabled": False,
        }, clear=False)
        self._cfg_patcher.start()

        # Patch the context pool so we don't spin up real threads
        self._pool_patcher = patch(
            "kokertechController.KokertechController._fetch_context_components",
            return_value={
                "context": "No prior history found.",
                "scbe_text": "No bias drift recorded yet.",
                "growth_text": "No growth events recorded yet.",
                "episodic_context": "No episodic journal available.",
                "weighted_episodic": "No episodic journal available.",
            },
        )
        self._pool_patcher.start()

        # Silently stub _summarize_and_evict so no history eviction runs
        import kokertechController
        self._evict_patcher = patch.object(
            kokertechController.KokertechController,
            "_summarize_and_evict",
            lambda self, log: None,
        )
        self._evict_patcher.start()

        # Now safe to create the controller (uses patched provider + vault)
        self.ctrl = kokertechController.KokertechController()

    def tearDown(self):
        try:
            self.ctrl.shutdown()
        except Exception:
            pass
        self._evict_patcher.stop()
        self._pool_patcher.stop()
        self._cfg_patcher.stop()
        self._db_patcher.stop()
        self._provider_patcher.stop()
        # Release any pooled connections to the temp vault so rmtree can
        # delete the files on Windows (no lingering file locks).
        import memory_vault
        memory_vault._clear_connection_pool()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- Helpers -----------------------------------------------------

    def _count_vault_entries(self):
        """Return the number of rows in core_memories."""
        if not os.path.isfile(self.vault_path):
            return 0
        with sqlite3.connect(self.vault_path) as conn:
            cur = conn.execute("SELECT COUNT(*) FROM core_memories")
            return cur.fetchone()[0]

    # -- Tests -------------------------------------------------------

    def test_full_call_chain_structured_mode(self):
        """E2E: Controller -> _TestProvider -> parse_ai -> store_memory.

        Verifies the FULL chain:
          1. process_input calls the _TestProvider exactly once.
          2. The provider receives the expected messages shape.
          3. parse_ai extracts thinking + final from the XML response.
          4. store_memory persists the interaction to the vault.
          5. The response dict has the expected keys and values.
          6. Controller history now contains 2 entries (user + assistant).
        """
        result = self.ctrl.process_input(
            "What is the weather today?",
            agent_type="Executive",
        )

        # Stage 1: Provider was called -- the real AIProvider interface.
        # May be called >1 times: eviction/summarization + main inference +
        # async tag extraction.  Find the inference call (system message
        # contains "AGENT INITIALIZATION") rather than assuming last call.
        self.assertGreaterEqual(
            self.test_provider._call_count, 1,
            "_TestProvider.chat_completion must be called at least once"
        )
        inference_calls = [
            c for c in self.test_provider._call_records
            if c["messages"] and c["messages"][0]["role"] == "system"
            and "AGENT INITIALIZATION" in c["messages"][0]["content"]
        ]
        self.assertGreaterEqual(
            len(inference_calls), 1,
            "At least one inference call must have AGENT INITIALIZATION in system prompt"
        )
        messages = inference_calls[-1]["messages"]
        system_msg = messages[0]
        self.assertEqual(system_msg["role"], "system")
        self.assertIn("AGENT INITIALIZATION", system_msg["content"])
        user_msg = messages[-1]
        self.assertEqual(user_msg["role"], "user")
        self.assertIn("What is the weather today?", user_msg["content"])

        # Stage 2: parse_ai extracted thinking + final from XML
        self.assertIn("final", result, "Response must contain 'final' key")
        self.assertIn("thinking", result, "Response must contain 'thinking' key")
        self.assertIn(
            "This is the AI response to the test query",
            result["final"],
            "parse_ai must extract <final_output> content"
        )
        self.assertIn(
            "Processing the user query",
            result["thinking"],
            "parse_ai must extract <thinking> content"
        )

        # Stage 3: No errors in the pipeline
        self.assertIsNone(
            result.get("error"),
            "Pipeline must not produce an error for valid XML responses"
        )
        self.assertIn("command", result, "Response must contain 'command' key")

        # Stage 4: store_memory was called -- interaction persisted to vault
        count = self._count_vault_entries()
        self.assertGreaterEqual(
            count, 1,
            f"Vault must contain >=1 entry after process_input, got {count}"
        )

        # Stage 5: Controller history was updated
        self.assertEqual(
            len(self.ctrl.history), 2,
            "History must have 2 entries (user + assistant) after process_input"
        )
        self.assertEqual(self.ctrl.history[0]["role"], "user")
        self.assertIn(
            "What is the weather today?",
            self.ctrl.history[0]["content"]
        )
        self.assertEqual(self.ctrl.history[1]["role"], "assistant")

    def test_full_call_chain_freeform_mode(self):
        """E2E: Freeform mode -- provider returns plain text (no XML tags)."""
        with patch.dict("config.CONFIG", {"freeform_mode": True}):
            self.test_provider.xml_response = (
                "Sure! The weather today is sunny with a high of 72F."
            )
            result = self.ctrl.process_input(
                "What is the weather today?",
                agent_type="Executive",
            )

        self.assertIn("sunny", result["final"])
        self.assertEqual(result["thinking"], "")
        self.assertIsNone(result.get("error"))

    def test_provider_error_propagates_through_chain(self):
        """E2E: Provider error -> controller surfaces it."""
        original_completion = self.test_provider.chat_completion

        def _error_response(*a, **kw):
            return {
                "content": "",
                "error": "Model inference timeout",
                "tool_calls": [],
            }

        self.test_provider.chat_completion = _error_response
        try:
            result = self.ctrl.process_input(
                "What is the weather?",
                agent_type="Executive",
            )
        finally:
            self.test_provider.chat_completion = original_completion

        self.assertIn("error", result)

    def test_vault_persistence_survives_multiple_calls(self):
        """E2E: Multiple process_input calls each write to the vault."""
        call_count = 3
        for i in range(call_count):
            self.test_provider.xml_response = (
                f"<thinking>processing query {i}</thinking>\n"
                f"<final_output>Response {i}</final_output>"
            )
            self.ctrl.process_input(f"query {i}")

        vault_count = self._count_vault_entries()
        self.assertGreaterEqual(
            vault_count, call_count,
            f"Vault must have >= {call_count} entries, got {vault_count}"
        )

    def test_sentinel_response_is_rejected(self):
        """E2E: Known sentinel triggers the 3-layer safety guard (Decision 8).

        Note: The sentinel check at Layer C (post-parse_ai) fires AFTER the
        assistant message was already appended to history (STEP 4, before
        parse_ai).  So history has 2 entries (user + assistant) and the
        defense-in-depth pop only removes the user entry — the assistant
        entry survives.  This is a known gap in the sentinel-rejection path.
        """
        self.test_provider.xml_response = (
            "<thinking></thinking>\n"
            "<final_output>Yo! What's on the bench today, mate?</final_output>"
        )
        result = self.ctrl.process_input("hello", agent_type="Executive")

        self.assertIn("error", result)
        self.assertIn("Engine returned", result["error"])
        # Layer A (raw-text sentinel check) can't match because the XML tags
        # wrap the sentinel text.  Layer C (post-parse_ai) catches it on the
        # extracted final field and now pops BOTH messages (user + assistant).
        self.assertEqual(
            len(self.ctrl.history), 0,
            "History must be empty — Layer C pops both user and assistant"
        )

    def test_sentinel_regex_pattern_is_rejected(self):
        """E2E: Regex sentinel (Example N:) triggers Layer A — clean rejection.

        Unlike the exact-match sentinel (which is XML-wrapped and only caught
        at Layer C after the assistant msg was appended), a raw-text response
        that starts with "Example 1:" matches the anchored regex
        ``_SENTINEL_PATTERN = re.compile(r'^\\s*Example\\s+\\d+\\b')`` at Layer A
        — BEFORE the assistant message is appended to history.

        Result: the user message is popped and history is left empty (0 entries).
        The error message cites "Engine returned sentinel/example text".
        """
        # Raw text starting with "Example N:" — no XML wrapping.
        self.test_provider.xml_response = (
            "Example 1: This is a sample response.\n"
            "The weather today is sunny with a high of 72F."
        )
        result = self.ctrl.process_input("hello", agent_type="Executive")

        # Layer A caught it — error with "sentinel/example text" in message
        self.assertIn("error", result)
        self.assertIn("Engine returned", result["error"])
        self.assertIn("sentinel", result["error"].lower())

        # Clean rejection — user msg popped before assistant msg appended
        self.assertEqual(
            len(self.ctrl.history), 0,
            "History must be empty — Layer A catches BEFORE assistant msg appended"
        )

    def test_sentinel_regex_pattern_with_title_is_rejected(self):
        """E2E: Regex sentinel variant 'Example 2 (Using a Tool):' also caught.

        Tests the titled variant: "Example 2 (Using a Tool):" — the word
        boundary ``\b`` in the regex anchors after the digit, so the
        parenthesized title does not prevent the match.
        """
        self.test_provider.xml_response = (
            "Example 2 (Using a Tool): Search the web for weather data.\n"
            "Result: It is 72F and sunny."
        )
        result = self.ctrl.process_input("hello", agent_type="Executive")

        self.assertIn("error", result)
        self.assertIn("Engine returned", result["error"])
        self.assertIn("sentinel", result["error"].lower())

        self.assertEqual(
            len(self.ctrl.history), 0,
            "History must be empty — titled variant also caught at Layer A"
        )

    def test_sentinel_regex_xml_wrapped_caught_at_layer_c(self):
        """E2E: Regex sentinel inside XML tags caught at Layer C only.

        When the regex sentinel is wrapped in ``<final_output>`` tags,
        Layer A misses it (the raw text starts with ``<thinking>``, not
        ``Example``, so the anchored ``^\\s*Example`` regex fails).
        Layer C catches it on the parsed ``final`` field after parse_ai
        extracts the content.

        Same known gap as the exact-match sentinel test: the assistant
        message was already appended before Layer C fires, so history
        has 1–2 entries (user popped, assistant survives).
        """
        self.test_provider.xml_response = (
            "<thinking>Processing the query.</thinking>\n"
            "<final_output>Example 1: This is the AI response.</final_output>"
        )
        result = self.ctrl.process_input("hello", agent_type="Executive")

        # Layer C caught it — error with "sentinel in parsed final" message
        self.assertIn("error", result)
        self.assertIn("sentinel", result["error"].lower())

        # Layer C now pops both messages — assistant + user
        self.assertEqual(
            len(self.ctrl.history), 0,
            "History must be empty — Layer C pops both user and assistant"
        )

    def test_streaming_path_delivers_tokens_progressively(self):
        """E2E: stream_callback receives tokens in real-time.

        When a ``stream_callback`` is passed to ``process_input()``, the
        controller uses ``_execute_stream`` → ``provider.chat_completion_stream()``
        instead of the non-streaming path.  Tokens arrive progressively via
        the callback, are accumulated into the full response text, and the
        result is parsed normally by ``parse_ai``.

        Verifies:
          1. The streaming path was used (stream_call_count > 0).
          2. Tokens arrived via the callback (token list is non-empty).
          3. The final parsed result has expected thinking/final fields.
          4. History was updated (2 entries: user + assistant).
        """
        # Use a simple response so word-level tokenization is clean.
        self.test_provider.xml_response = (
            "<thinking>Processing query</thinking>\n"
            "<final_output>Streamed response text</final_output>"
        )

        tokens = []
        initial_stream_count = self.test_provider._call_count

        result = self.ctrl.process_input(
            "hello stream",
            agent_type="Executive",
            stream_callback=lambda token: tokens.append(token),
        )

        # Stage 1: Streaming path was used — chat_completion_stream was called.
        # A dedicated counter proves the streaming path fired, not just that
        # _call_count increased (which would also happen on silent fallback).
        self.assertGreater(
            self.test_provider._stream_call_count, 0,
            "chat_completion_stream must have been called (streaming path)"
        )

        # Stage 2: Tokens arrived progressively via the callback.
        self.assertGreater(
            len(tokens), 0,
            "stream_callback must receive at least one token"
        )
        # Reconstruct and verify the full content was delivered.
        reconstructed = "".join(tokens)
        self.assertIn(
            "Processing query", reconstructed,
            "Reconstructed tokens must contain thinking content"
        )
        self.assertIn(
            "Streamed response text", reconstructed,
            "Reconstructed tokens must contain final_output content"
        )

        # Stage 3: parse_ai extracted thinking + final from the reassembled text
        self.assertIn("final", result)
        self.assertIn("Streamed response text", result["final"])
        self.assertIn("thinking", result)
        self.assertIn("Processing query", result["thinking"])
        self.assertIsNone(result.get("error"))

        # Stage 4: History was updated with both messages
        self.assertEqual(
            len(self.ctrl.history), 2,
            "History must have 2 entries (user + assistant)"
        )

    def test_non_streaming_cancel_event_returns_error(self):
        """E2E: Cancel event set before non-streaming call → error surfaced.

        When ``cancel_event`` is passed to ``process_input`` without a
        ``stream_callback``, the controller uses the non-streaming path:
        ``_execute_with_fallback`` → ``provider.chat_completion()``.
        The provider checks ``cancel_event.is_set()`` and returns an
        error result, matching production ``LocalLLMProvider`` behaviour.

        Verifies:
          1. The non-streaming path was used (stream_call_count == 0).
          2. chat_completion was called (call_count > 0).
          3. The provider returned a cancellation error.
          4. The controller surfaced the error in the result dict.
          5. History was NOT updated (cancellation before completion).
        """
        cancel_event = threading.Event()
        cancel_event.set()  # cancel BEFORE the call

        result = self.ctrl.process_input(
            "hello cancel",
            agent_type="Executive",
            cancel_event=cancel_event,
        )

        # Stage 1: The non-streaming path was used (no stream_callback).
        self.assertEqual(
            self.test_provider._stream_call_count, 0,
            "chat_completion_stream must NOT be called without stream_callback"
        )
        self.assertGreater(
            self.test_provider._call_count, 0,
            "chat_completion must have been called (non-streaming path)"
        )

        # Stage 2: The provider returned a cancellation error, and the
        # controller surfaced it.  Verify the error shape: it must be a
        # proper error result (no parsed final/thinking), not a normal
        # response that coincidentally contains the word "cancelled".
        self.assertIn("error", result)
        self.assertIn("Engine Sync Error", result.get("error", ""))
        self.assertIn(
            "cancelled", result["error"].lower(),
            "Result error must indicate cancellation"
        )
        self.assertNotIn(
            "final", result,
            "Cancelled calls must return error dicts, not parsed XML responses"
        )

        # Stage 3: History was NOT updated — cancellation means no
        # valid interaction was completed.
        self.assertEqual(
            len(self.ctrl.history), 0,
            "History must be empty after cancelled non-streaming call"
        )

    def test_controller_history_does_not_grow_unbounded(self):
        """E2E: After exceeding memory_limit, history is summarized/evicted."""
        self.ctrl.memory_limit = 2
        self._evict_patcher.stop()

        try:
            for i in range(5):
                self.test_provider.xml_response = (
                    f"<thinking>processing query {i}</thinking>\n"
                    f"<final_output>Response {i}</final_output>"
                )
                self.ctrl.process_input(f"query {i}")
        finally:
            self._evict_patcher.start()

        self.assertLessEqual(
            len(self.ctrl.history), 4,
            f"History must not exceed 2x memory_limit, got {len(self.ctrl.history)}"
        )

    def test_multi_model_calls_in_one_session(self):
        """E2E: Two calls with different models in the same controller session.

        The controller calls ``_load_target_model()`` before each
        ``process_input`` to determine which model to use.  This test
        patches it with different ``return_value`` values for two
        consecutive calls and verifies the controller passes the
        correct model to the provider each time.

        Note: ``_load_target_model`` is called multiple times within a
        single ``process_input`` (via ``_message_cap`` + directly), so
        each call gets its own ``patch.object`` block with a fixed
        ``return_value`` — all internal calls within one turn see the
        same model.

        Verifies:
          1. First call uses model-A, provider receives model-A.
          2. Second call uses model-B, provider receives model-B.
          3. Both calls succeed (no errors).
          4. ``_last_loaded_model`` was updated after the switch.
          5. History accumulates across model switches (4 entries).
          6. Vault has entries from both calls.
        """
        # First call: uses lexi-llama-3-8b
        with patch.object(self.ctrl, "_load_target_model",
                          return_value="lexi-llama-3-8b"):
            self.test_provider.xml_response = (
                "<thinking>Lexi reasoning</thinking>\n"
                "<final_output>Lexi response</final_output>"
            )
            result1 = self.ctrl.process_input("query 1")

        # Second call: uses qwen2.5-7b-instruct (different model)
        with patch.object(self.ctrl, "_load_target_model",
                          return_value="qwen2.5-7b-instruct"):
            self.test_provider.xml_response = (
                "<thinking>Qwen reasoning</thinking>\n"
                "<final_output>Qwen response</final_output>"
            )
            result2 = self.ctrl.process_input("query 2")

        # Stage 1: Both calls succeeded.
        self.assertIsNone(result1.get("error"))
        self.assertIsNone(result2.get("error"))

        # Stage 2: Each call used the correct model.
        # Filter for inference calls (system msg with AGENT INITIALIZATION).
        inference_calls = [
            c for c in self.test_provider._call_records
            if (c["messages"] and c["messages"][0]["role"] == "system"
                and "AGENT INITIALIZATION" in c["messages"][0]["content"])
        ]
        self.assertGreaterEqual(
            len(inference_calls), 2,
            "At least 2 inference calls must be recorded"
        )
        self.assertEqual(
            inference_calls[0]["model"], "lexi-llama-3-8b",
            "First inference call must use lexi-llama-3-8b"
        )
        self.assertEqual(
            inference_calls[1]["model"], "qwen2.5-7b-instruct",
            "Second inference call must use qwen2.5-7b-instruct"
        )

        # Stage 3: Each response was parsed correctly.
        self.assertIn("Lexi response", result1["final"])
        self.assertIn("Qwen response", result2["final"])

        # Stage 4: History accumulates across model switches.
        self.assertEqual(
            len(self.ctrl.history), 4,
            "History must have 4 entries (user+assistant × 2 calls)"
        )

        # Stage 5: Vault has entries from both calls.
        self.assertGreaterEqual(
            self._count_vault_entries(), 2,
            "Vault must have >=2 entries after two calls"
        )

    def test_multi_model_cancel_then_recover_with_model_b(self):
        """E2E: Cancel model A mid-stream, switch to model B, verify clean recovery.

        Simulates a user stopping a slow model-A generation and immediately
        re-querying with model B.  The controller must:
          1. Stream from model A, cancel mid-stream (callback sets event).
          2. Recover the partial stream result (parse_ai on partial XML).
          3. Switch to model B on the next call and return a clean response.

        Verifies:
          - Call 1 (model A, streaming cancel): partial tokens, cancel_event
            was set, inference calls use model-A.
          - Call 2 (model B, non-streaming): clean response from model B,
            inference calls use model-B, no bleed-through from model A.
          - History accumulates across the cancel+recover sequence (4 entries).
          - _TestProvider received correct model in each call.
        """
        # -- Call 1: model A with streaming, cancel mid-stream -----------
        with patch.object(self.ctrl, "_load_target_model",
                          return_value="lexi-llama-3-8b"):
            self.test_provider.xml_response = (
                "<thinking>"
                + "Lexi processing query step by step carefully now " * 10
                + "</thinking>\n"
                "<final_output>"
                + "This is a long Lexi streamed response with many words " * 10
                + "</final_output>"
            )

            cancel_event = threading.Event()
            tokens_a = []

            def stream_callback_cancel(token):
                tokens_a.append(token)
                if len(tokens_a) >= 5:
                    cancel_event.set()

            records_before_a = len(self.test_provider._call_records)
            stream_count_before = self.test_provider._stream_call_count

            result_a = self.ctrl.process_input(
                "query for model A",
                agent_type="Executive",
                stream_callback=stream_callback_cancel,
                cancel_event=cancel_event,
            )

        # Assertions for call 1 -------------------------------------------------
        # Streaming path was used.
        self.assertGreater(
            self.test_provider._stream_call_count, stream_count_before,
            "Call 1: chat_completion_stream must have been called"
        )
        # Cancel event was actually set.
        self.assertTrue(
            cancel_event.is_set(),
            "Call 1: cancel_event must be set after process_input returns"
        )
        # Some tokens arrived, but fewer than the full response.
        self.assertGreater(
            len(tokens_a), 0,
            "Call 1: stream_callback must receive at least one token before cancel"
        )
        reconstructed_a = "".join(tokens_a)
        full_text_a = self.test_provider.xml_response.replace(" ", "")
        self.assertLess(
            len(reconstructed_a.replace(" ", "")),
            len(full_text_a),
            "Call 1: Reconstructed tokens must be shorter than full response "
            "(cancellation stopped the stream early)"
        )
        # parse_ai handled the partial XML from model A.
        self.assertIn("final", result_a)
        self.assertIn("thinking", result_a)

        # Inference calls for call 1 must use model-A.
        calls_a = [
            c for c in self.test_provider._call_records[records_before_a:]
            if (c["messages"] and c["messages"][0]["role"] == "system"
                and "AGENT INITIALIZATION" in c["messages"][0]["content"])
        ]
        self.assertGreaterEqual(
            len(calls_a), 1,
            "Call 1: at least one inference call must have AGENT INITIALIZATION"
        )
        for c in calls_a:
            self.assertEqual(
                c["model"], "lexi-llama-3-8b",
                "Call 1: every inference call must use lexi-llama-3-8b"
            )

        # -- Call 2: model B, no streaming, verify clean recovery -----------
        with patch.object(self.ctrl, "_load_target_model",
                          return_value="qwen2.5-7b-instruct"):
            self.test_provider.xml_response = (
                "<thinking>Qwen reasoning after recovery</thinking>\n"
                "<final_output>Qwen clean response after cancel recovery</final_output>"
            )

            records_before_b = len(self.test_provider._call_records)
            stream_count_after_a = self.test_provider._stream_call_count

            result_b = self.ctrl.process_input(
                "query for model B after cancel",
                agent_type="Executive",
            )

        # Assertions for call 2 -------------------------------------------------
        # No error — model B responded cleanly.
        self.assertIsNone(
            result_b.get("error"),
            "Call 2: model B must respond cleanly after model A cancellation"
        )
        # Call 2 used the non-streaming path (no stream_callback passed).
        self.assertEqual(
            self.test_provider._stream_call_count, stream_count_after_a,
            "Call 2: stream_call_count must not increase "
            "(non-streaming path must be used when no stream_callback)"
        )
        # parse_ai extracted model B's content correctly.
        self.assertIn("final", result_b)
        self.assertIn(
            "Qwen clean response after cancel recovery", result_b["final"],
            "Call 2: model B's response must be parsed correctly"
        )
        self.assertIn("thinking", result_b)
        self.assertIn(
            "Qwen reasoning after recovery", result_b["thinking"],
            "Call 2: model B's thinking must be parsed correctly"
        )

        # Inference calls for call 2 must use model-B.
        calls_b = [
            c for c in self.test_provider._call_records[records_before_b:]
            if (c["messages"] and c["messages"][0]["role"] == "system"
                and "AGENT INITIALIZATION" in c["messages"][0]["content"])
        ]
        self.assertGreaterEqual(
            len(calls_b), 1,
            "Call 2: at least one inference call must have AGENT INITIALIZATION"
        )
        for c in calls_b:
            self.assertEqual(
                c["model"], "qwen2.5-7b-instruct",
                "Call 2: every inference call must use qwen2.5-7b-instruct (no model-A bleed-through)"
            )

        # -- Cross-call assertions --------------------------------------------
        # History accumulates across both calls (cancel + recover).
        self.assertEqual(
            len(self.ctrl.history), 4,
            "History must have 4 entries (user+assistant × 2 calls)"
        )
        # Vault has entries from both calls.
        self.assertGreaterEqual(
            self._count_vault_entries(), 2,
            "Vault must have >=2 entries after both calls"
        )

    def test_persona_a_sentinel_persona_b_recovers_cleanly(self):
        """E2E: Persona A returns sentinel, persona B recovers with clean response.

        ``process_input_with_personas()`` dispatches the same prompt to two
        personas sequentially using the same provider. This test simulates
        persona A echoing sentinel text (``Example 1:``) while persona B
        responds cleanly — the personas-path analog of the multi-model
        cancel+recover test.

        The provider's ``chat_completion`` is temporarily wrapped to return
        sentinel text on the first call and a clean response on the second.
        ``_query_with_persona()`` checks ``_is_sentinel_response()`` after
        the provider error check, so persona A gets ``ok: False`` while
        persona B gets ``ok: True``.

        Verifies:
          - Persona A: ``ok`` is ``False``, error indicates sentinel.
          - Persona B: ``ok`` is ``True``, response contains clean text.
          - The result dict has the expected shape (prompt, persona_a/b,
            response_a/b, vote_id).
          - No error at the top level (the method returns both results
            regardless of individual failures).
        """
        # Patch _load_target_model so it returns a known model path.
        with patch.object(self.ctrl, "_load_target_model",
                          return_value="test-persona-model"):
            # Wrap chat_completion to alternate: call 1 = sentinel, call 2 = clean
            original_completion = self.test_provider.chat_completion
            call_counter = [0]

            def _alternating(*a, **kw):
                call_counter[0] += 1
                if call_counter[0] == 1:
                    # Persona A: raw sentinel text (Layer A catches it)
                    return {
                        "content": "Example 1: This is sentinel text from persona A.",
                        "error": None,
                        "tool_calls": [],
                    }
                else:
                    # Persona B: clean freeform response
                    return {
                        "content": "Persona B clean response after sentinel recovery.",
                        "error": None,
                        "tool_calls": [],
                    }

            self.test_provider.chat_completion = _alternating
            try:
                result = self.ctrl.process_input_with_personas(
                    "test prompt for A/B",
                    persona_a="Persona A: You are a tester who echoes examples.",
                    persona_b="Persona B: You are a helpful assistant.",
                )
            finally:
                self.test_provider.chat_completion = original_completion

        # Stage 1: Top-level result has no error (both responses returned).
        self.assertIsNone(
            result.get("error"),
            "Top-level result must have no error — both responses are returned"
        )
        self.assertEqual(result["prompt"], "test prompt for A/B")

        # Stage 2: Persona A was rejected as sentinel.
        response_a = result["response_a"]
        self.assertIn(
            "Error:", response_a.get("final", ""),
            "Persona A response must contain error indicator"
        )
        self.assertIn(
            "sentinel", response_a["final"].lower(),
            "Persona A error must mention sentinel"
        )

        # Stage 3: Persona B recovered cleanly.
        response_b = result["response_b"]
        self.assertEqual(
            response_b["final"],
            "Persona B clean response after sentinel recovery.",
            "Persona B must return clean response after persona A sentinel"
        )
        self.assertEqual(
            response_b.get("thinking", ""), "",
            "Persona B freeform response must have empty thinking"
        )

        # Stage 4: Both personas were dispatched (sentinel at A didn't
        # short-circuit before persona B).
        self.assertEqual(
            call_counter[0], 2,
            "Both personas must be dispatched (call 1 = A sentinel, call 2 = B clean)"
        )

        # Stage 5: Result shape includes a valid vote_id (vault is functional).
        self.assertGreaterEqual(
            result.get("vote_id", -1), 0,
            "Result must have a real vote_id (>=0) — vault record_persona_vote succeeded"
        )

    def test_multi_model_one_sentinel_one_clean(self):
        """E2E: Multi-model dispatch — one model returns sentinel, the other clean.

        ``process_input_multi()`` dispatches to multiple models in parallel.
        This test passes two model specs: model-A returns sentinel text
        (``Example 1: ...``), model-B returns a clean response. Layer A
        catches the sentinel on model-A's result, marking it ``ok: False``
        while model-B gets ``ok: True``.

        The provider's ``chat_completion`` is temporarily wrapped to
        dispatch by model name (not position) — avoids race conditions
        from ``memory_vault.store_memory()`` daemon threads that also call
        ``chat_completion`` for async tag extraction.

        Verifies:
          - 2 results returned (one per model spec).
          - Exactly 1 result has ``ok: False`` with sentinel error.
          - Exactly 1 result has ``ok: True`` with clean content.
          - The clean result's content matches the expected response.
        """
        # Wrap chat_completion: dispatch by model name, not position.
        # Model-name-based dispatch is immune to daemon-thread interference
        # from memory_vault.store_memory → _update_tags_async.
        original_completion = self.test_provider.chat_completion
        sentinel_text = "Example 1: This is sentinel model output."
        clean_text = "Clean model response without sentinel patterns."

        def _by_model(*a, **kw):
            if kw.get("model") == "sentinel-model":
                return {
                    "content": sentinel_text,
                    "error": None,
                    "tool_calls": [],
                }
            return {
                "content": clean_text,
                "error": None,
                "tool_calls": [],
            }

        self.test_provider.chat_completion = _by_model
        try:
            results = self.ctrl.process_input_multi(
                "test multi-model sentinel",
                model_specs=[
                    {"provider": "local_llm", "model": "sentinel-model",
                     "label": "Sentinel Model"},
                    {"provider": "local_llm", "model": "clean-model",
                     "label": "Clean Model"},
                ],
            )
        finally:
            self.test_provider.chat_completion = original_completion

        # Stage 1: Both models were dispatched.
        self.assertEqual(
            len(results), 2,
            "Must return 2 results (one per model spec)"
        )

        # Stage 2: Exactly one sentinel failure.
        sentinel_results = [r for r in results if not r["ok"]]
        self.assertEqual(
            len(sentinel_results), 1,
            "Exactly 1 model must be marked ok:False (sentinel)"
        )
        self.assertIn(
            "sentinel", sentinel_results[0]["error"].lower(),
            "Sentinel result error must mention sentinel"
        )

        # Stage 3: Exactly one clean success.
        clean_results = [r for r in results if r["ok"]]
        self.assertEqual(
            len(clean_results), 1,
            "Exactly 1 model must be marked ok:True (clean)"
        )
        self.assertEqual(
            clean_results[0]["content"], clean_text,
            "Clean result must contain the expected response"
        )

    def test_persona_a_streams_sentinel_persona_b_streams_clean(self):
        """E2E: Both personas stream — persona A sentinel, persona B clean.

        ``process_input_with_personas()`` now supports ``stream_callback``
        (added v0.21.6). This test wraps ``chat_completion_stream`` to
        dispatch by system-prompt content: persona A's prompt contains
        "sentinel persona" → yields sentinel text; persona B's prompt
        (and any daemon-thread calls) → yields clean text.

        Cancellation via ``cancel_event`` is NOT tested here because both
        personas share the same event — cancelling A would also cancel B.
        Instead, this test validates the streaming path's integration with
        the sentinel check: persona A's streamed sentinel is caught by
        Layer A in ``_query_with_persona``, while persona B's clean stream
        produces a valid response.

        Verifies:
          - Persona A: response contains "Error:" and "sentinel".
          - Persona B: response contains the clean recovery text.
          - Both personas used the streaming path (stream_call_count
            increased by ≥2 from the personas calls).
          - Top-level result has no error.
        """
        # Patch _load_target_model so it returns a known model.
        with patch.object(self.ctrl, "_load_target_model",
                          return_value="test-persona-stream-model"):
            # Wrap chat_completion_stream: dispatch by system-prompt content.
            original_stream = self.test_provider.chat_completion_stream
            stream_before = self.test_provider._stream_call_count

            def _by_prompt(*a, **kw):
                # cancel_event not tested here; both personas share the
                # same event, so cancelling A would also cancel B.
                self.test_provider._stream_call_count += 1
                messages = a[0] if a else kw.get("messages", [])
                sys_msg = messages[0]["content"] if messages else ""
                if "sentinel persona" in sys_msg.lower():
                    # Persona A: yield sentinel tokens
                    for word in "Example 1: This is sentinel text".split():
                        yield {"token": word + " "}
                    return
                else:
                    # Persona B + daemon threads: yield clean tokens
                    for word in "Clean recovery stream response".split():
                        yield {"token": word + " "}
                    return

            self.test_provider.chat_completion_stream = _by_prompt
            try:
                tokens = []
                result = self.ctrl.process_input_with_personas(
                    "test streaming personas",
                    persona_a="Persona A: You are a sentinel persona tester.",
                    persona_b="Persona B: You are a helpful assistant.",
                    stream_callback=lambda t: tokens.append(t),
                )
            finally:
                self.test_provider.chat_completion_stream = original_stream

        # Stage 1: Top-level result has no error.
        self.assertIsNone(result.get("error"))

        # Stage 2: Persona A streaming sentinel caught by Layer A.
        response_a = result["response_a"]
        self.assertIn(
            "Error:", response_a.get("final", ""),
            "Persona A streamed sentinel must produce error response"
        )
        self.assertIn(
            "sentinel", response_a["final"].lower(),
            "Persona A error must mention sentinel"
        )

        # Stage 3: Persona B streamed clean response.
        response_b = result["response_b"]
        self.assertEqual(
            response_b["final"], "Clean recovery stream response",
            "Persona B must stream clean recovery response"
        )

        # Stage 4: Both personas used the streaming path.
        # process_input_with_personas does NOT call store_memory, so no
        # daemon threads fire. Exactly 2 stream calls expected.
        self.assertEqual(
            self.test_provider._stream_call_count - stream_before, 2,
            "Both personas must have used the streaming path (exactly 2)"
        )

        # Stage 5: Tokens arrived via the callback from both personas.
        reconstructed = "".join(tokens)
        self.assertIn("Example", reconstructed,
                      "stream_callback must receive persona A sentinel tokens")
        self.assertIn("recovery", reconstructed,
                      "stream_callback must receive persona B recovery tokens")

    def test_personas_cancel_a_mid_stream_b_recovers(self):
        """E2E: Cancel persona A mid-stream, persona B recovers cleanly.

        ``process_input_with_personas()`` shares a single ``cancel_event``
        between both personas. This test works around that limitation by
        having the wrapper detect persona B (via system-prompt content)
        and clear the event before streaming, allowing persona B to
        recover while persona A was cancelled.

        The test is the personas-path analog of the multi-model
        cancel+recover test but for the streaming path.

        Verifies:
          - Persona A: cancel_event was set mid-stream → partial tokens.
          - Persona B: event was cleared → full tokens arrive, clean
            response parsed correctly.
          - Both personas used the streaming path.
          - Reconstructed tokens show A's partial stream + B's full stream.
        """
        with patch.object(self.ctrl, "_load_target_model",
                          return_value="test-persona-cancel-model"):
            original_stream = self.test_provider.chat_completion_stream
            stream_before = self.test_provider._stream_call_count

            cancel_event = threading.Event()
            cancel_was_set = [False]  # survives ce.clear() by persona B
            tokens = []

            def _stream_with_cancel(*a, **kw):
                self.test_provider._stream_call_count += 1
                ce = kw.get("cancel_event")
                messages = a[0] if a else kw.get("messages", [])
                sys_msg = messages[0]["content"] if messages else ""

                if "cancel persona" in sys_msg.lower():
                    # Persona A: yield tokens until cancel, then stop
                    words = (
                        "This is a long persona A stream that will "
                        "be cancelled mid way through the response"
                    ).split()
                    for word in words:
                        if ce and ce.is_set():
                            return
                        yield {"token": word + " "}
                    return
                else:
                    # Persona B + daemon threads: clear stale cancel
                    # state from persona A before streaming.
                    # Assumes persona A already ran and set the event.
                    if ce and ce.is_set():
                        ce.clear()
                    for word in "Clean recovery after cancel".split():
                        if ce and ce.is_set():
                            return
                        yield {"token": word + " "}
                    return

            # Cancel persona A after 4 tokens; guard with cancel_was_set
            # so persona B's tokens don't re-trigger the cancellation.
            def stream_callback(token):
                tokens.append(token)
                if len(tokens) >= 4 and not cancel_event.is_set() and not cancel_was_set[0]:
                    cancel_event.set()
                    cancel_was_set[0] = True

            self.test_provider.chat_completion_stream = _stream_with_cancel
            try:
                result = self.ctrl.process_input_with_personas(
                    "test personas cancel",
                    persona_a="Persona A: You are a cancel persona tester.",
                    persona_b="Persona B: You are a helpful assistant.",
                    stream_callback=stream_callback,
                    cancel_event=cancel_event,
                )
            finally:
                self.test_provider.chat_completion_stream = original_stream

        # Stage 1: Cancel was triggered during persona A.
        self.assertTrue(
            cancel_was_set[0],
            "cancel_event must have been set during persona A streaming"
        )

        # Stage 2: Persona A's response is the partial accumulated text
        # (cancellation stops streaming early — ~4 words before cancel).
        response_a = result["response_a"]
        self.assertTrue(
            len(response_a.get("final", "")) > 0,
            "Persona A must have non-empty partial response from streaming"
        )

        # Stage 3: Persona B recovered cleanly (event was cleared).
        response_b = result["response_b"]
        self.assertEqual(
            response_b["final"], "Clean recovery after cancel",
            "Persona B must recover with clean response after cancel"
        )

        # Stage 4: Both personas used the streaming path.
        self.assertEqual(
            self.test_provider._stream_call_count - stream_before, 2,
            "Both personas must have used the streaming path (exactly 2)"
        )

        # Stage 5: Tokens from both personas arrived.
        reconstructed = "".join(tokens)
        self.assertIn(
            "long", reconstructed,
            "stream_callback must receive persona A tokens before cancel"
        )
        self.assertIn(
            "recovery", reconstructed,
            "stream_callback must receive persona B recovery tokens"
        )

    def test_cancel_event_b_independent_of_persona_a(self):
        """E2E: Cancel only persona B via cancel_event_b, persona A streams fully.

        ``process_input_with_personas()`` now accepts ``cancel_event_b``
        (added v0.21.7) for independent cancellation of persona B.
        This test passes two separate events:
          - ``cancel_event`` (for persona A) — never set → streams fully.
          - ``cancel_event_b`` (for persona B) — set after 8 tokens
            (once persona A's full stream is done) → cuts B short.

        Verifies:
          - Persona A streams fully (complete response, no cancellation).
          - Persona B is cancelled mid-stream (partial response).
          - cancel_event was never set (persona A unaffected).
          - cancel_event_b was set (persona B was cancelled).
        """
        with patch.object(self.ctrl, "_load_target_model",
                          return_value="test-cancel-b-model"):
            original_stream = self.test_provider.chat_completion_stream
            stream_before = self.test_provider._stream_call_count

            cancel_a = threading.Event()  # never set
            cancel_b = threading.Event()  # set mid-stream for persona B
            cancel_b_fired = [False]
            tokens = []

            def _stream_with_b_cancel(*a, **kw):
                self.test_provider._stream_call_count += 1
                ce = kw.get("cancel_event")
                messages = a[0] if a else kw.get("messages", [])
                sys_msg = messages[0]["content"] if messages else ""

                if "cancel persona b" in sys_msg.lower():
                    # Persona B: long text that gets cancelled mid-stream
                    words = (
                        "Persona B long stream that should be "
                        "cancelled mid way through generation"
                    ).split()
                    for word in words:
                        if ce and ce.is_set():
                            return
                        yield {"token": word + " "}
                    return
                else:
                    # Persona A + daemon threads: stream fully
                    for word in "Persona A full clean response".split():
                        if ce and ce.is_set():
                            return
                        yield {"token": word + " "}
                    return

            def stream_callback(token):
                tokens.append(token)
                # Persona A has 5 tokens; 8-token threshold ensures
                # A is fully done before B's cancel fires.
                if len(tokens) >= 8 and not cancel_b.is_set():
                    cancel_b.set()
                    cancel_b_fired[0] = True

            self.test_provider.chat_completion_stream = _stream_with_b_cancel
            try:
                result = self.ctrl.process_input_with_personas(
                    "test independent cancel B",
                    persona_a="Persona A: You are a helpful assistant.",
                    persona_b="Persona B: You are a cancel persona b tester.",
                    stream_callback=stream_callback,
                    cancel_event=cancel_a,
                    cancel_event_b=cancel_b,
                )
            finally:
                self.test_provider.chat_completion_stream = original_stream

        # Stage 1: cancel_event (for persona A) was never set.
        self.assertFalse(
            cancel_a.is_set(),
            "cancel_event (persona A) must not have been set"
        )

        # Stage 2: cancel_event_b (for persona B) was set and is still set
        # (unlike the shared-event test, cancel_b is never cleared here).
        self.assertTrue(
            cancel_b_fired[0],
            "cancel_event_b must have been set during persona B streaming"
        )
        self.assertTrue(
            cancel_b.is_set(),
            "cancel_event_b must still be set after the call (never cleared)"
        )

        # Stage 3: Persona A streamed fully — complete response.
        response_a = result["response_a"]
        self.assertEqual(
            response_a["final"],
            "Persona A full clean response",
            "Persona A must stream full response unaffected by cancel_event_b"
        )

        # Stage 4: Persona B was cancelled — partial response, fewer tokens.
        response_b = result["response_b"]
        self.assertTrue(
            len(response_b.get("final", "")) > 0,
            "Persona B must have partial response from streaming before cancel"
        )
        self.assertLess(
            len(response_b["final"]),
            len("Persona B long stream that should be cancelled mid way through generation"),
            "Persona B response must be shorter than full text (cancelled early)"
        )

        # Stage 5: Both personas used the streaming path.
        self.assertEqual(
            self.test_provider._stream_call_count - stream_before, 2,
            "Both personas must have used the streaming path"
        )

    def test_streaming_cancel_event_stops_mid_stream(self):
        """E2E: Cancel event set mid-stream → streaming stops, partial tokens.

        A ``threading.Event`` is passed as ``cancel_event`` to
        ``process_input``.  The stream callback sets the event after
        receiving 5 tokens, simulating a user clicking "Stop" during
        streaming.  The provider's ``chat_completion_stream`` checks
        ``cancel_event.is_set()`` between tokens (matching production
        ``LocalLLMProvider`` behaviour at ``ai_base.py`` line 882) and
        stops yielding.

        ``_execute_stream`` accumulates the partial tokens and returns
        them.  ``process_input`` then parses the partial text normally.

        Verifies:
          1. The streaming path was used (stream_call_count > 0).
          2. Some tokens arrived via the callback before cancellation.
          3. Cancellation cut the stream short (fewer tokens than full).
          4. The cancel_event was actually set (the cancellation path
             fired, not just normal completion).
          5. History was updated (2 entries).
        """
        self.test_provider.xml_response = (
            "<thinking>"
            + "processing query step by step very carefully now " * 10
            + "</thinking>\n"
            "<final_output>"
            + "This is a long streamed response with many words " * 10
            + "</final_output>"
        )

        cancel_event = threading.Event()
        tokens = []

        # Set the cancel event after 5 tokens arrive via the callback.
        # Deterministic — no sleep, no thread race.
        def stream_callback(token):
            tokens.append(token)
            if len(tokens) >= 5:
                cancel_event.set()

        result = self.ctrl.process_input(
            "hello stream cancel",
            agent_type="Executive",
            stream_callback=stream_callback,
            cancel_event=cancel_event,
        )

        # Stage 1: The streaming path was used.
        self.assertGreater(
            self.test_provider._stream_call_count, 0,
            "chat_completion_stream must have been called"
        )

        # Stage 2: The cancel_event was actually set — proves the
        # cancellation path fired, not just normal completion.
        self.assertTrue(
            cancel_event.is_set(),
            "cancel_event must be set after process_input returns"
        )

        # Stage 3: Some tokens arrived before cancellation, but fewer
        # than the full response (cancellation cut it short).
        self.assertGreater(
            len(tokens), 0,
            "stream_callback must receive at least one token before cancel"
        )
        reconstructed = "".join(tokens)
        full_text = self.test_provider.xml_response.replace(" ", "")
        self.assertLess(
            len(reconstructed.replace(" ", "")),
            len(full_text),
            "Reconstructed tokens must be shorter than full xml_response "
            "(cancellation stopped the stream early)"
        )

        # Stage 4: process_input returned a valid response dict.
        # parse_ai handles partial/incomplete XML gracefully.
        self.assertIn("final", result)
        self.assertIn("thinking", result)

        # Stage 5: History was updated.
        self.assertEqual(
            len(self.ctrl.history), 2,
            "History must have 2 entries (user + assistant)"
        )

    def test_streaming_error_falls_back_to_non_streaming(self):
        """E2E: Streaming error → fallback to chat_completion() path.

        When ``chat_completion_stream()`` yields an error chunk, the
        controller's ``_execute_stream`` returns ``None``, and
        ``process_input`` falls back to ``_execute_with_fallback`` →
        ``chat_completion()``.

        Verifies:
          1. The streaming path was attempted (stream_call_count > 0).
          2. chat_completion() was called as fallback (non-stream call
             with AGENT INITIALIZATION system prompt appears after the
             stream attempt).
          3. No tokens arrived via stream_callback (error before first token).
          4. The fallback result was parsed correctly by parse_ai.
          5. History was updated normally (2 entries).
        """
        self.test_provider._stream_should_fail = True
        try:
            self.test_provider.xml_response = (
                "<thinking>Fallback reasoning</thinking>\n"
                "<final_output>Result from fallback path</final_output>"
            )

            tokens = []
            stream_count_before = self.test_provider._stream_call_count
            records_before = len(self.test_provider._call_records)

            result = self.ctrl.process_input(
                "hello stream fail",
                agent_type="Executive",
                stream_callback=lambda token: tokens.append(token),
            )

            # Stage 1: The streaming path was attempted — the provider's
            # chat_completion_stream WAS called (and it returned an error).
            self.assertGreater(
                self.test_provider._stream_call_count, stream_count_before,
                "chat_completion_stream must have been called (streaming attempted)"
            )

            # Stage 2: The fallback path fired — at least one non-stream
            # call (chat_completion) happened AFTER the stream attempt.
            # These calls carry the AGENT INITIALIZATION system prompt.
            fallback_calls = [
                c for c in self.test_provider._call_records[records_before:]
                if (c["messages"] and c["messages"][0]["role"] == "system"
                    and "AGENT INITIALIZATION" in c["messages"][0]["content"])
            ]
            self.assertGreaterEqual(
                len(fallback_calls), 1,
                "At least one fallback chat_completion call must have "
                "AGENT INITIALIZATION in the system prompt"
            )

            # Stage 3: No tokens arrived via the stream callback (error
            # chunk was yielded before any valid token).
            self.assertEqual(
                len(tokens), 0,
                "stream_callback must not receive tokens when stream fails"
            )

            # Stage 4: The fallback result was parsed correctly by parse_ai.
            self.assertIn("final", result)
            self.assertIn("Result from fallback path", result["final"])
            self.assertIn("thinking", result)
            self.assertIn("Fallback reasoning", result["thinking"])
            self.assertIsNone(result.get("error"))

            # Stage 5: History was updated normally from the fallback path.
            self.assertEqual(
                len(self.ctrl.history), 2,
                "History must have 2 entries (user + assistant) from fallback path"
            )
        finally:
            self.test_provider._stream_should_fail = False
