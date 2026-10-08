"""Unit tests for desire_engine.py — mocked get_provider, parameter verification."""

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, ANY

# API key override handled by conftest.py pytest_configure() which sets
# KOKERTECH_API_KEY=test-key-override before collection begins.


class TestDesireEngine(unittest.TestCase):
    """Verify generate_prediction calls get_provider with correct params."""

    def setUp(self):
        from ai_base import reset_providers
        reset_providers()

    def _make_mock_provider(self, return_content):
        """Build a mock provider whose chat_completion returns the given content."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": return_content,
            "model": "qwen2.5-0.5b-instruct",
            "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
            "error": None,
        }
        return provider

    # ---------- Parameter verification ----------

    @patch("desire_engine.get_provider")
    def test_calls_get_provider_with_active_provider(self, mock_get_provider):
        """Should call get_provider with name from CONFIG["active_provider"]."""
        from config import CONFIG
        mock_get_provider.return_value = self._make_mock_provider(
            '{"suggestions": []}'
        )
        from desire_engine import generate_prediction
        generate_prediction(history=[], current_prompt="test")
        mock_get_provider.assert_called_once_with(
            name=CONFIG["active_provider"]
        )

    @patch("desire_engine.get_provider")
    def test_passes_correct_model(self, mock_get_provider):
        """Should pass CONFIG["desire_model_name"] as model."""
        from config import CONFIG
        mock_provider = self._make_mock_provider('{"suggestions": []}')
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        generate_prediction(history=[], current_prompt="test")
        mock_provider.chat_completion.assert_called_once_with(
            messages=ANY,
            model=CONFIG["desire_model_name"],
            temperature=0.6,
            max_tokens=800,
            timeout=120,
        )

    @patch("desire_engine.get_provider")
    def test_passes_high_temperature(self, mock_get_provider):
        """Desire engine should use temperature=0.6 for creativity."""
        mock_provider = self._make_mock_provider('{"suggestions": []}')
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        generate_prediction(history=[], current_prompt="test")
        _, kwargs = mock_provider.chat_completion.call_args
        self.assertEqual(kwargs["temperature"], 0.6)

    @patch("desire_engine.get_provider")
    def test_passes_800_max_tokens(self, mock_get_provider):
        mock_provider = self._make_mock_provider('{"suggestions": []}')
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        generate_prediction(history=[], current_prompt="test")
        _, kwargs = mock_provider.chat_completion.call_args
        self.assertEqual(kwargs["max_tokens"], 800)

    @patch("desire_engine.get_provider")
    def test_passes_120s_timeout(self, mock_get_provider):
        mock_provider = self._make_mock_provider('{"suggestions": []}')
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        generate_prediction(history=[], current_prompt="test")
        _, kwargs = mock_provider.chat_completion.call_args
        self.assertEqual(kwargs["timeout"], 120)

    # ---------- Message construction ----------

    @patch("desire_engine.get_provider")
    def test_system_prompt_in_messages(self, mock_get_provider):
        """First message should be system prompt with desire_prompt."""
        from config import CONFIG
        mock_provider = self._make_mock_provider('{"suggestions": []}')
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        generate_prediction(history=[], current_prompt="test")
        messages = mock_provider.chat_completion.call_args[1]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn(CONFIG["desire_prompt"], messages[0]["content"])

    @patch("desire_engine.get_provider")
    def test_last_user_message_includes_current_prompt(self, mock_get_provider):
        """Last message should reference the current prompt."""
        mock_provider = self._make_mock_provider('{"suggestions": []}')
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        generate_prediction(history=[], current_prompt="list files")
        messages = mock_provider.chat_completion.call_args[1]["messages"]
        last = messages[-1]
        self.assertEqual(last["role"], "user")
        self.assertIn("list files", last["content"])

    @patch("desire_engine.get_provider")
    def test_history_truncation_to_3(self, mock_get_provider):
        """Only last 3 history messages should be included."""
        mock_provider = self._make_mock_provider('{"suggestions": []}')
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        history = [
            {"role": "user", "content": f"msg{i}"} for i in range(10)
        ]
        generate_prediction(history=history, current_prompt="test")
        messages = mock_provider.chat_completion.call_args[1]["messages"]
        # 1 system + up to 3 history + 1 user = 5 max
        self.assertLessEqual(len(messages), 5)
        self.assertGreaterEqual(len(messages), 2)  # at least system + current

    @patch("desire_engine.get_provider")
    def test_long_history_content_truncated(self, mock_get_provider):
        """History messages over 500 chars should be truncated."""
        mock_provider = self._make_mock_provider('{"suggestions": []}')
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        long_content = "A" * 1000
        history = [{"role": "user", "content": long_content}]
        generate_prediction(history=history, current_prompt="test")
        messages = mock_provider.chat_completion.call_args[1]["messages"]
        # The history message content should have been truncated
        hist_msg = [m for m in messages if m["role"] == "user" and "TRUNCATED" in m["content"]]
        self.assertEqual(len(hist_msg), 1)
        self.assertIn("[TRUNCATED", hist_msg[0]["content"])

    # ---------- Response parsing ----------

    @patch("desire_engine.get_provider")
    def test_returns_suggestions_from_json(self, mock_get_provider):
        """Should parse valid JSON and return suggestions list."""
        mock_provider = self._make_mock_provider(
            '{"suggestions": [{"prediction": "Check disk space", "action": {"action": "SYSTEM_STATUS"}}]}'
        )
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        result = generate_prediction(history=[], current_prompt="test")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["prediction"], "Check disk space")

    @patch("desire_engine.get_provider")
    def test_returns_suggestions_when_response_wraps_json(self, mock_get_provider):
        """Should extract JSON from surrounding text."""
        mock_provider = self._make_mock_provider(
            'Here are some suggestions:\n{"suggestions": [{"prediction": "Run script"}]}\nEnd.'
        )
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        result = generate_prediction(history=[], current_prompt="test")
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["prediction"], "Run script")

    @patch("desire_engine.get_provider")
    def test_returns_none_on_provider_error(self, mock_get_provider):
        """When provider returns error, should return None."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "", "model": "", "usage": {}, "error": "Connection refused"
        }
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        result = generate_prediction(history=[], current_prompt="test")
        self.assertIsNone(result)

    @patch("desire_engine.get_provider")
    def test_returns_none_on_invalid_json(self, mock_get_provider):
        """When response has no valid JSON and no bullets, should return None."""
        mock_provider = self._make_mock_provider("This is not JSON at all")
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        result = generate_prediction(history=[], current_prompt="test")
        self.assertIsNone(result)

    @patch("desire_engine.get_provider")
    def test_desire_suggestions_plain_text_bullets_regression(self, mock_get_provider):
        """REGRESSION GUARD for bulleted plain-text suggestion fallback in
        ``desire_engine._parse_suggestions`` (lines 140-210 of ``desire_engine.py``).

        Invariant: Models responding in bulleted or numbered plain text lines
        (common when system prompt is overridden or reasoning models output
        formatted lists without strict JSON) MUST still yield structured
        suggestions rather than being silently dropped.
        """
        raw_text = (
            "Here are some helpful next steps you can take:\n"
            "1. Check disk space and system memory\n"
            "2. Run Python script to inspect logs\n"
            "- List files in the workspace directory\n"
        )
        mock_provider = self._make_mock_provider(raw_text)
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        result = generate_prediction(history=[], current_prompt="test")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["prediction"], "Check disk space and system memory")
        self.assertEqual(result[1]["prediction"], "Run Python script to inspect logs")
        self.assertEqual(result[2]["prediction"], "List files in the workspace directory")

    @patch("desire_engine.get_provider")
    def test_desire_suggestions_with_think_tags(self, mock_get_provider):
        """Should strip <think>...</think> reasoning tags before extracting JSON."""
        content = (
            "<think>\n"
            "The user asked about performance.\n"
            "Stray brace {not real json} here.\n"
            "</think>\n"
            '{"suggestions": [{"prediction": "Inspect GPU memory usage", "action": {"action": "GPU_STATS"}}]}'
        )
        mock_provider = self._make_mock_provider(content)
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        result = generate_prediction(history=[], current_prompt="test")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["prediction"], "Inspect GPU memory usage")
        self.assertEqual(result[0]["action"]["action"], "GPU_STATS")

    @patch("desire_engine.get_provider")
    def test_desire_suggestions_from_json_array(self, mock_get_provider):
        """Should parse raw JSON array responses directly into suggestion list."""
        content = '[{"prediction": "Run tests", "action": {"action": "PYTEST"}}]'
        mock_provider = self._make_mock_provider(content)
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        result = generate_prediction(history=[], current_prompt="test")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["prediction"], "Run tests")

    @patch("desire_engine.get_provider")
    def test_desire_suggestions_string_items_normalized(self, mock_get_provider):
        """Should normalize string elements in suggestions into structured dicts."""
        content = '{"suggestions": ["Check system status", "Review recent logs"]}'
        mock_provider = self._make_mock_provider(content)
        mock_get_provider.return_value = mock_provider
        from desire_engine import generate_prediction
        result = generate_prediction(history=[], current_prompt="test")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["prediction"], "Check system status")
        self.assertEqual(result[1]["prediction"], "Review recent logs")

    @patch("desire_engine.get_provider")
    def test_desire_prompt_schema_appended_when_missing(self, mock_get_provider):
        """Should append JSON schema to custom desire_prompt when not already present."""
        custom_prompt = "Give the user 5 creative ideas for what to do next."
        mock_provider = self._make_mock_provider('{"suggestions": []}')
        mock_get_provider.return_value = mock_provider
        with patch.dict("desire_engine.CONFIG", {"desire_prompt": custom_prompt}):
            from desire_engine import generate_prediction
            generate_prediction(history=[], current_prompt="hello")
        messages = mock_provider.chat_completion.call_args[1]["messages"]
        system_msg = messages[0]["content"]
        self.assertTrue(system_msg.startswith(custom_prompt))
        self.assertIn("Return JSON with this exact structure:", system_msg)


if __name__ == "__main__":
    unittest.main()

