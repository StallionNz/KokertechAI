"""
Tests for Agentic RAG integration in kokertechController.py.

Covers:
- Explicit <<RAG:query>> syntax triggers the RAG pipeline
- Automatic deep research detection (trigger keywords + word count)
- Simple queries do not trigger RAG
- RAG results injected into vault context
- RAG pipeline errors handled gracefully
- <<RAG:...>> wrapper stripped from user message
- Edge cases: short queries with trigger words, single-word trigger, etc.
"""

import sys
import unittest
from unittest.mock import patch, MagicMock, ANY

class TestRAGDetection(unittest.TestCase):
    """Test deep research query detection logic in process_input."""

    def setUp(self):
        self._patches = []
        self._mocks = {}

        # Patch memory_vault to avoid DB calls
        mem = patch.multiple("memory_vault",
            semantic_search=MagicMock(return_value=[]),
            store_memory=MagicMock(),
            store_episodic=MagicMock(return_value=1),
            get_recent_bias=MagicMock(return_value=[]),
            get_growth_arc=MagicMock(return_value=[]),
            ensure_tables_exist=MagicMock(),
            get_episodic_context=MagicMock(return_value=""),
        )
        mem.start()
        self._patches.append(mem)
        self._mocks["memory_vault"] = mem

        # Patch get_provider to return a mock provider
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "Mock AI response",
            "error": None,
        }
        prov = patch("kokertechController.get_provider", return_value=mock_provider)
        prov.start()
        self._patches.append(prov)
        self._mocks["get_provider"] = prov
        self._mock_provider = mock_provider

        # Patch AgenticRAGEngine to avoid actual LLM calls
        mock_rag = MagicMock()
        mock_rag.answer.return_value = {
            "answer": "Deep research answer with citations [1][2]",
            "sub_questions": ["Q1", "Q2"],
            "contexts": [
                {"source": "core_memory", "content": "Docker is a container runtime.", "score": 0.95},
                {"source": "episodic", "content": "Podman is an alternative to Docker.", "score": 0.85},
            ],
            "hops": 2,
            "error": None,
        }
        rag_patch = patch("kokertechController.AgenticRAGEngine", return_value=mock_rag)
        rag_patch.start()
        self._patches.append(rag_patch)
        self._mocks["AgenticRAGEngine"] = rag_patch
        self._mock_rag = mock_rag

        # NOTE: kokertechController no longer calls requests.post directly —
        # the provider abstraction (KNOWLEDGE.md §5) is already mocked above
        # via patch("kokertechController.get_provider", ...).

        # Isolate from cross-file CONFIG pollution (mock/ freeform mode set by other tests)
        cfg = patch.dict("kokertechController.CONFIG",
            {"mock_mode": False, "freeform_mode": False, "active_provider": "local_llm"},
            clear=False)
        cfg.start()
        self._patches.append(cfg)

        # Isolate from shared services singleton: clear protocol_prompt
        # so the system prompt is the default structured/freeform one,
        # and mock _summarize_and_evict to prevent spurious summarization
        # calls when the shared history service accumulates entries from
        # previous test methods.
        cfg2 = patch.dict("kokertechController.CONFIG",
            {"protocol_prompt": ""}, clear=False)
        cfg2.start()
        self._patches.append(cfg2)

        from kokertechController import KokertechController
        self.controller = KokertechController()

        # Mock _summarize_and_evict to prevent the shared HistoryService
        # from making summarization calls when history accumulates entries
        # from previous test methods (shared singleton state leakage).
        self.controller._summarize_and_evict = lambda log: None

    def tearDown(self):
        for p in self._patches:
            try:
                p.stop()
            except Exception:
                pass

    def _process(self, text):
        """Helper: call process_input."""
        return self.controller.process_input(text, agent_type="Executive", log_callback=lambda x: None)

    # --- Explicit <<RAG:query>> syntax ---

    def test_explicit_rag_tag_triggers_pipeline(self):
        """<<RAG:query>> syntax should trigger the RAG pipeline."""
        self._process("<<RAG:Tell me about Docker vs Podman>>")
        self._mock_rag.answer.assert_called_once()
        call_args = self._mock_rag.answer.call_args[1]
        self.assertEqual(call_args["query"], "Tell me about Docker vs Podman")

    def test_explicit_rag_tag_strips_wrapper_from_text(self):
        """After RAG, the user text should be the inner query, not the RAG tag."""
        self._process("<<RAG:Compare containers>>")
        all_calls = self._mock_provider.chat_completion.call_args_list
        self.assertGreater(len(all_calls), 0)
        messages = all_calls[0].kwargs.get("messages", [])
        user_msgs = [m for m in messages if m["role"] == "user"]
        self.assertTrue(any("Compare containers" in m["content"] for m in user_msgs))

    def test_explicit_rag_tag_with_special_chars(self):
        """<<RAG:query>> with special characters should still work."""
        self._process("<<RAG:What does 'agentic' mean in AI?>>")
        self._mock_rag.answer.assert_called_once()
        call_args = self._mock_rag.answer.call_args[1]
        self.assertEqual(call_args["query"], "What does 'agentic' mean in AI?")

    def test_explicit_rag_tag_multiline_query(self):
        """<<RAG:query>> with multiline content should work."""
        self._process("""<<RAG:Tell me about
Docker and Podman
performance>>""")
        self._mock_rag.answer.assert_called_once()
        call_args = self._mock_rag.answer.call_args[1]
        self.assertIn("Docker", call_args["query"])
        self.assertIn("Podman", call_args["query"])

    def test_explicit_rag_tag_handles_error_gracefully(self):
        """If RAG pipeline raises, the conversation should continue without crashing."""
        self._mock_rag.answer.side_effect = RuntimeError("LLM crashed")
        result = self._process("<<RAG:Research containers>>")
        self.assertIsNotNone(result)
        self._mock_rag.answer.assert_called_once()

    def test_explicit_rag_tag_error_in_result(self):
        """If RAG returns error in result dict, conversation should continue."""
        self._mock_rag.answer.return_value = {
            "answer": "",
            "sub_questions": [],
            "contexts": [],
            "hops": 0,
            "error": "Provider unavailable",
        }
        result = self._process("<<RAG:Research containers>>")
        self.assertIsNotNone(result)
        self._mock_rag.answer.assert_called_once()

    # --- Automatic deep research detection ---

    def test_auto_detection_long_query_with_trigger(self):
        """A long query (>= min words) containing a trigger keyword should trigger RAG."""
        from kokertechController import _DEEP_RESEARCH_MIN_WORDS
        # Build a query with exactly min words that includes the 'research' trigger
        words = ["research"] + ["word"] * (_DEEP_RESEARCH_MIN_WORDS - 1)
        text = " ".join(words)
        self._process(text)
        self._mock_rag.answer.assert_called_once()
        call_args = self._mock_rag.answer.call_args[1]
        self.assertEqual(call_args["query"], text)

    def test_auto_detection_short_query_no_trigger(self):
        """A short query without trigger keywords should NOT trigger RAG."""
        self._process("Hello, how are you?")
        self._mock_rag.answer.assert_not_called()

    def test_auto_detection_trigger_but_too_short(self):
        """A query with a trigger word but too few words should NOT trigger RAG."""
        self._process("research this")
        self._mock_rag.answer.assert_not_called()

    def test_auto_detection_long_but_no_trigger(self):
        """A long query without trigger keywords should NOT trigger RAG."""
        text = "I like to eat pizza with pepperoni and cheese on top."
        self._process(text)
        self._mock_rag.answer.assert_not_called()

    def test_auto_detection_multi_hop_keyword(self):
        """'multi-hop' keyword should trigger RAG regardless of word count."""
        self._process("multi-hop rag query test")
        self._mock_rag.answer.assert_called_once()

    def test_auto_detection_rag_query_keyword(self):
        """'rag query' keyword should trigger RAG regardless of word count."""
        self._process("rag query about everything")
        self._mock_rag.answer.assert_called_once()

    def test_auto_detection_exact_min_words(self):
        """A query at exactly the min word count with a trigger should trigger RAG."""
        from kokertechController import _DEEP_RESEARCH_MIN_WORDS
        words = ["research"] + ["word"] * (_DEEP_RESEARCH_MIN_WORDS - 1)
        self._process(" ".join(words))
        self._mock_rag.answer.assert_called_once()

    # --- RAG context injection ---

    def test_rag_results_injected_into_context(self):
        """RAG synthesis and evidence should appear in the context passed to the AI."""
        self._process("<<RAG:Tell me about containers>>")
        all_calls = self._mock_provider.chat_completion.call_args_list
        self.assertGreater(len(all_calls), 0)
        messages = all_calls[0].kwargs.get("messages", [])
        system_msg = messages[0]
        self.assertIn("DEEP RESEARCH RESULTS", system_msg["content"])
        self.assertIn("Deep research answer with citations", system_msg["content"])
        self.assertIn("Docker is a container runtime", system_msg["content"])

    def test_rag_results_injected_for_auto_detection(self):
        """RAG results should be injected even for auto-detected queries."""
        from kokertechController import _DEEP_RESEARCH_MIN_WORDS
        # Build a long enough query to trigger auto-detection
        words = ["research"] + ["word"] * (_DEEP_RESEARCH_MIN_WORDS - 1)
        text = " ".join(words)
        self._process(text)
        all_calls = self._mock_provider.chat_completion.call_args_list
        self.assertGreater(len(all_calls), 0)
        messages = all_calls[0].kwargs.get("messages", [])
        system_msg = messages[0]
        self.assertIn("DEEP RESEARCH RESULTS", system_msg["content"])

    # --- No RAG triggers ---

    def test_normal_simple_query_no_rag(self):
        """A simple conversational query should not trigger RAG."""
        self._process("What is the weather like?")
        self._mock_rag.answer.assert_not_called()

    def test_normal_greeting_no_rag(self):
        """A greeting should not trigger RAG."""
        self._process("Hi there!")
        self._mock_rag.answer.assert_not_called()

class TestRAGLogging(unittest.TestCase):
    """Test that RAG pipeline produces proper log messages."""

    def setUp(self):
        self._patches = []
        self._mocks = {}
        self.log_messages = []

        mem = patch.multiple("memory_vault",
            semantic_search=MagicMock(return_value=[]),
            store_memory=MagicMock(),
            store_episodic=MagicMock(return_value=1),
            get_recent_bias=MagicMock(return_value=[]),
            get_growth_arc=MagicMock(return_value=[]),
            ensure_tables_exist=MagicMock(),
            get_episodic_context=MagicMock(return_value=""),
        )
        mem.start()
        self._patches.append(mem)
        self._mocks["memory_vault"] = mem

        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "Mock AI response",
            "error": None,
        }
        prov = patch("kokertechController.get_provider", return_value=mock_provider)
        prov.start()
        self._patches.append(prov)
        self._mocks["get_provider"] = prov
        self._mock_provider = mock_provider

        mock_rag = MagicMock()
        mock_rag.answer.return_value = {
            "answer": "Research answer.",
            "sub_questions": ["Q1"],
            "contexts": [{"source": "core_memory", "content": "Data.", "score": 0.9}],
            "hops": 1,
            "error": None,
        }
        rag_patch = patch("kokertechController.AgenticRAGEngine", return_value=mock_rag)
        rag_patch.start()
        self._patches.append(rag_patch)
        self._mocks["AgenticRAGEngine"] = rag_patch
        self._mock_rag = mock_rag

        # NOTE: requests.post patch removed — the provider is already mocked
        # via patch("kokertechController.get_provider", ...) above (KNOWLEDGE.md §5).

        # Isolate from cross-file CONFIG pollution (mock/ freeform mode set by other tests)
        cfg = patch.dict("kokertechController.CONFIG",
            {"mock_mode": False, "freeform_mode": False, "active_provider": "local_llm"},
            clear=False)
        cfg.start()
        self._patches.append(cfg)

        # Isolate from shared services singleton: clear protocol_prompt
        # and mock _summarize_and_evict to prevent spurious summarization
        # calls from shared history state leakage.
        cfg2 = patch.dict("kokertechController.CONFIG",
            {"protocol_prompt": ""}, clear=False)
        cfg2.start()
        self._patches.append(cfg2)

        from kokertechController import KokertechController
        self.controller = KokertechController()

        # Mock _summarize_and_evict to prevent the shared HistoryService
        # from making summarization calls when history accumulates entries
        # from previous test methods (shared singleton state leakage).
        self.controller._summarize_and_evict = lambda log: None

    def tearDown(self):
        for p in self._patches:
            try:
                p.stop()
            except Exception:
                pass

    def _log(self, msg):
        self.log_messages.append(msg)

    def test_rag_logs_explicit_detection(self):
        """Explicit RAG tag should log its detection."""
        self.controller.process_input("<<RAG:Research stuff>>", log_callback=self._log)
        self.assertTrue(any("RAG:" in m and "<<RAG:" in m for m in self.log_messages))

    def test_rag_logs_auto_detection(self):
        """Auto-detection should log its detection."""
        from kokertechController import _DEEP_RESEARCH_MIN_WORDS
        words = ["research"] + ["word"] * (_DEEP_RESEARCH_MIN_WORDS - 1)
        text = " ".join(words)
        self.controller.process_input(text, log_callback=self._log)
        self.assertTrue(any("RAG:" in m and "trigger" in m.lower() for m in self.log_messages))

    def test_rag_logs_pipeline_start(self):
        """RAG pipeline should log its start."""
        self.controller.process_input("<<RAG:Research stuff>>", log_callback=self._log)
        self.assertTrue(any("RAG" in m and "pipeline" in m.lower() for m in self.log_messages))

    def test_rag_logs_pipeline_error(self):
        """RAG pipeline error should log error messages."""
        self._mock_rag.answer.side_effect = RuntimeError("LLM crashed")
        self.controller.process_input("<<RAG:Research stuff>>", log_callback=self._log)
        self.assertTrue(any("crashed" in m or "error" in m.lower() for m in self.log_messages))

    def test_no_rag_no_logs(self):
        """No RAG-related log messages for non-research queries."""
        self.controller.process_input("Hello!", log_callback=self._log)
        rag_logs = [m for m in self.log_messages if "RAG" in m or "Deep research" in m]
        self.assertEqual(len(rag_logs), 0)

if __name__ == "__main__":
    unittest.main()
