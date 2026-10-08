"""Tests for prompt_builder.py - centralized prompt construction."""

import unittest
from unittest.mock import patch, MagicMock



class TestConstants(unittest.TestCase):
    """Module-level prompt template constants."""
    def test_freeform_system_prompt(self):
        from prompt_builder import FREEFORM_SYSTEM_PROMPT
        self.assertIsInstance(FREEFORM_SYSTEM_PROMPT, str)
        self.assertIn("KokertechAI", FREEFORM_SYSTEM_PROMPT)
    def test_structured_system_prompt(self):
        from prompt_builder import STRUCTURED_SYSTEM_PROMPT
        self.assertIsInstance(STRUCTURED_SYSTEM_PROMPT, str)
        self.assertIn("CRITICAL COGNITIVE PROTOCOL", STRUCTURED_SYSTEM_PROMPT)
        self.assertIn("<thinking>", STRUCTURED_SYSTEM_PROMPT)
    def test_summarization_system_prompt(self):
        from prompt_builder import SUMMARIZATION_SYSTEM_PROMPT
        self.assertIsInstance(SUMMARIZATION_SYSTEM_PROMPT, str)
        self.assertIn("compressed summary", SUMMARIZATION_SYSTEM_PROMPT)
    def test_context_sections_structure(self):
        from prompt_builder import CONTEXT_SECTIONS
        self.assertIsInstance(CONTEXT_SECTIONS, tuple)
        self.assertEqual(len(CONTEXT_SECTIONS), 8)
        labels, keys = zip(*CONTEXT_SECTIONS)
        self.assertIn("AGENT INITIALIZATION", labels)
        self.assertIn("LONG-TERM MEMORY", labels)
        self.assertIn("persona", keys)
        self.assertIn("context", keys)
    def test_sub_agent_personas_structure(self):
        from prompt_builder import SUB_AGENT_PERSONAS
        self.assertIn("Researcher", SUB_AGENT_PERSONAS)
        self.assertIn("Coder", SUB_AGENT_PERSONAS)
        self.assertIn("Auditor", SUB_AGENT_PERSONAS)
        self.assertIn("Planner", SUB_AGENT_PERSONAS)
        self.assertIn("ToolUser", SUB_AGENT_PERSONAS)
        for name, cfg in SUB_AGENT_PERSONAS.items():
            with self.subTest(name=name):
                self.assertIn("system", cfg)
                self.assertIn("temperature", cfg)
                self.assertIn("max_tokens", cfg)


class TestBuildSystemPrompt(unittest.TestCase):
    """PromptBuilder.build_system_prompt."""
    def setUp(self):
        from prompt_builder import PromptBuilder
        self.b = PromptBuilder()
    def test_default_is_structured(self):
        result = self.b.build_system_prompt()
        self.assertIn("<thinking>", result)
    def test_freeform_mode(self):
        result = self.b.build_system_prompt(mode="freeform")
        self.assertIn("naturally", result)
        self.assertNotIn("<thinking>", result)
    def test_custom_protocol_overrides(self):
        custom = "You are a custom AI."
        result = self.b.build_system_prompt(custom_protocol=custom)
        self.assertEqual(result, custom)
    def test_empty_custom_protocol_falls_back(self):
        result = self.b.build_system_prompt(custom_protocol="")
        self.assertIn("<thinking>", result)
    def test_custom_protocol_with_freeform_ignores_mode(self):
        custom = "Custom override"
        result = self.b.build_system_prompt(mode="freeform", custom_protocol=custom)
        self.assertEqual(result, custom)


class TestBuildSummarizationPrompt(unittest.TestCase):
    """PromptBuilder.build_summarization_prompt."""
    def setUp(self):
        from prompt_builder import PromptBuilder
        self.b = PromptBuilder()
    def test_returns_summarization_prompt(self):
        from prompt_builder import SUMMARIZATION_SYSTEM_PROMPT
        result = self.b.build_summarization_prompt()
        self.assertEqual(result, SUMMARIZATION_SYSTEM_PROMPT)


class TestBuildContextText(unittest.TestCase):
    """PromptBuilder.build_context_text."""
    def setUp(self):
        from prompt_builder import PromptBuilder
        self.b = PromptBuilder()
    def test_all_sections_present(self):
        from prompt_builder import CONTEXT_SECTIONS
        result = self.b.build_context_text(persona="assistant", context="core data")
        for label, _ in CONTEXT_SECTIONS:
            with self.subTest(label=label):
                self.assertIn(f"[{label}]", result)
    def test_missing_key_defaults_to_not_available(self):
        result = self.b.build_context_text(persona="test")
        self.assertIn("(not available)", result)
    def test_context_summary_stripped(self):
        result = self.b.build_context_text(context_summary="  hello world  ")
        self.assertIn("hello world", result)
        self.assertNotIn("  hello world  ", result)
    def test_all_kwargs_provided(self):
        kwargs = {
            "persona": "agent",
            "user_profile": "Jacques",
            "context_summary": "session data",
            "episodic_context": "recent sessions",
            "weighted_episodic": "weighted entries",
            "context": "long-term memory",
            "scbe_text": "bias info",
            "growth_text": "growth info",
        }
        result = self.b.build_context_text(**kwargs)
        self.assertIn("agent", result)
        self.assertIn("Jacques", result)
        self.assertIn("session data", result)
        self.assertIn("long-term memory", result)
    def test_cache_returns_same_object(self):
        r1 = self.b.build_context_text(persona="cache", context="test")
        r2 = self.b.build_context_text(persona="cache", context="test")
        self.assertIs(r1, r2)
    def test_cache_miss_on_different_args(self):
        r1 = self.b.build_context_text(persona="cache")
        r2 = self.b.build_context_text(persona="different")
        self.assertIsNot(r1, r2)

