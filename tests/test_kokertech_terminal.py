import unittest
import io
import sys
from unittest.mock import patch

from kokertech_terminal import handle_slash_command
from kokertech_bridge import extract_valid_actions



class TestHandleSlashCommand(unittest.TestCase):
    """Tests for the handle_slash_command function in kokertech_terminal.py."""

    @patch('builtins.print')
    def test_rag_transforms_query(self, mock_print):
        should_skip, result = handle_slash_command("/rag What is Docker?")
        self.assertFalse(should_skip)
        self.assertEqual(result, "<<RAG:What is Docker?>>")

    @patch('builtins.print')
    def test_rag_with_extra_spaces(self, mock_print):
        should_skip, result = handle_slash_command("  /rag   GPU benchmarks  ")
        self.assertFalse(should_skip)
        self.assertEqual(result, "<<RAG:GPU benchmarks>>")

    @patch('builtins.print')
    def test_rag_empty_query_returns_help(self, mock_print):
        should_skip, result = handle_slash_command("/rag")
        self.assertTrue(should_skip)
        self.assertEqual(result, "")

    @patch('builtins.print')
    def test_rag_just_slash_rag_space(self, mock_print):
        should_skip, result = handle_slash_command("/rag   ")
        self.assertTrue(should_skip)
        self.assertEqual(result, "")

    @patch('builtins.print')
    def test_rag_with_punctuation(self, mock_print):
        should_skip, result = handle_slash_command("/rag How does RAG compare to fine-tuning?")
        self.assertFalse(should_skip)
        self.assertEqual(result, "<<RAG:How does RAG compare to fine-tuning?>>")

    @patch('builtins.print')
    def test_rag_case_insensitive(self, mock_print):
        should_skip, result = handle_slash_command("/RAG What is Kokertech?")
        self.assertFalse(should_skip)
        self.assertEqual(result, "<<RAG:What is Kokertech?>>")

    @patch('builtins.print')
    def test_rag_long_query(self, mock_print):
        long_q = "x" * 200
        should_skip, result = handle_slash_command(f"/rag {long_q}")
        self.assertFalse(should_skip)
        self.assertEqual(result, f"<<RAG:{long_q}>>")

    @patch('builtins.print')
    def test_rag_prints_status_message(self, mock_print):
        should_skip, result = handle_slash_command("/rag What is RAG?")
        self.assertFalse(should_skip)
        # Verify print was called with the system message
        printed_text = ' '.join(str(a) for a in mock_print.call_args_list[0][0])
        self.assertIn("Deep research mode activated", printed_text)
        self.assertIn("What is RAG?", printed_text)

    @patch('builtins.print')
    def test_help_returns_skip(self, mock_print):
        should_skip, result = handle_slash_command("/help")
        self.assertTrue(should_skip)
        self.assertEqual(result, "")

    @patch('builtins.print')
    def test_help_with_trailing_text(self, mock_print):
        should_skip, result = handle_slash_command("/help something extra")
        self.assertTrue(should_skip)
        self.assertEqual(result, "")

    @patch('builtins.print')
    def test_help_case_insensitive(self, mock_print):
        should_skip, result = handle_slash_command("/HELP")
        self.assertTrue(should_skip)

    @patch('builtins.print')
    def test_help_prints_help_text(self, mock_print):
        should_skip, result = handle_slash_command("/help")
        all_printed = ' '.join(str(a) for c in mock_print.call_args_list for a in c[0])
        self.assertIn("/rag", all_printed)
        self.assertIn("/help", all_printed)

    def test_normal_text_passes_through(self):
        should_skip, result = handle_slash_command("What is the weather today?")
        self.assertFalse(should_skip)
        self.assertEqual(result, "What is the weather today?")

    def test_empty_input_passes_through(self):
        should_skip, result = handle_slash_command("")
        self.assertFalse(should_skip)
        self.assertEqual(result, "")

    def test_numeric_input_passes_through(self):
        should_skip, result = handle_slash_command("42")
        self.assertFalse(should_skip)
        self.assertEqual(result, "42")

    def test_slash_alone_passes_through(self):
        should_skip, result = handle_slash_command("/")
        self.assertFalse(should_skip)
        self.assertEqual(result, "/")

    def test_unknown_slash_command_passes_through(self):
        should_skip, result = handle_slash_command("/foo bar baz")
        self.assertFalse(should_skip)
        self.assertEqual(result, "/foo bar baz")


class TestExtractValidActions(unittest.TestCase):
    """Pre-existing tests for kokertech_bridge.extract_valid_actions."""

    def test_extract_valid_actions_single(self):
        text = 'Here is the action: {"action": "READ_FILE", "filename": "test.txt"}'
        actions = extract_valid_actions(text)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "READ_FILE")

    def test_extract_valid_actions_multiple(self):
        text = '{"action": "LIST_FILES"} and then {"action": "DELETE_FILE", "filename": "old.txt"}'
        actions = extract_valid_actions(text)
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0]["action"], "LIST_FILES")
        self.assertEqual(actions[1]["action"], "DELETE_FILE")

    def test_extract_valid_actions_invalid_json(self):
        text = 'This is broken {"action": "READ_FILE", "filename": "test.txt"'
        actions = extract_valid_actions(text)
        self.assertEqual(len(actions), 0)

    def test_extract_valid_actions_missing_action_key(self):
        text = '{"filename": "test.txt", "content": "hello"}'
        actions = extract_valid_actions(text)
        self.assertEqual(len(actions), 0)


if __name__ == '__main__':
    unittest.main()
