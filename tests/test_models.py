"""Tests for models.py - Pydantic v2 data models."""

import unittest


class TestChatMessage(unittest.TestCase):
    """ChatMessage - frozen model with role and content."""
    def test_construct(self):
        from models import ChatMessage
        m = ChatMessage(role="user", content="hello")
        self.assertEqual(m.role, "user")
        self.assertEqual(m.content, "hello")
    def test_frozen_cannot_mutate(self):
        from models import ChatMessage
        m = ChatMessage(role="assistant", content="hi")
        with self.assertRaises(Exception):
            m.role = "user"
    def test_serializes_to_dict(self):
        from models import ChatMessage
        m = ChatMessage(role="system", content="prompt")
        d = m.model_dump()
        self.assertEqual(d, {"role": "system", "content": "prompt"})

class TestCommand(unittest.TestCase):
    """Command - parsed plugin command with optional fields."""
    def test_minimal_construct(self):
        from models import Command
        c = Command(action="SEARCH_WEB")
        self.assertEqual(c.action, "SEARCH_WEB")
        self.assertIsNone(c.query)
        self.assertIsNone(c.path)
    def test_all_fields(self):
        from models import Command
        c = Command(action="WRITE_FILE", path="/tmp/f.txt", content="data")
        self.assertEqual(c.action, "WRITE_FILE")
        self.assertEqual(c.path, "/tmp/f.txt")
        self.assertEqual(c.content, "data")
    def test_serializes_with_defaults(self):
        from models import Command
        c = Command(action="LIST_DIR")
        d = c.model_dump()
        self.assertEqual(d["action"], "LIST_DIR")
        self.assertIsNone(d["path"])

class TestFunctionCall(unittest.TestCase):
    """FunctionCall - function name and arguments."""
    def test_construct(self):
        from models import FunctionCall
        f = FunctionCall(name="search", arguments={"q": "test"})
        self.assertEqual(f.name, "search")
        self.assertEqual(f.arguments, {"q": "test"})
    def test_default_arguments_empty_dict(self):
        from models import FunctionCall
        f = FunctionCall(name="list")
        self.assertEqual(f.arguments, {})

class TestToolCall(unittest.TestCase):
    """ToolCall - tool call with function reference."""
    def test_construct(self):
        from models import ToolCall, FunctionCall
        fn = FunctionCall(name="search")
        tc = ToolCall(id="call_1", function=fn)
        self.assertEqual(tc.id, "call_1")
        self.assertEqual(tc.function.name, "search")
        self.assertEqual(tc.type, "function")
    def test_default_type_is_function(self):
        from models import ToolCall, FunctionCall
        tc = ToolCall(function=FunctionCall(name="x"))
        self.assertEqual(tc.type, "function")

class TestAIResponse(unittest.TestCase):
    """AIResponse - structured AI inference output."""
    def test_minimal_construct(self):
        from models import AIResponse
        r = AIResponse()
        self.assertEqual(r.thinking, "")
        self.assertEqual(r.final, "")
        self.assertIsNone(r.command)
        self.assertEqual(r.tool_calls, [])
        self.assertIsNone(r.error)
    def test_with_all_fields(self):
        from models import AIResponse, Command
        r = AIResponse(thinking="thinking...", final="final reply", ts="2025-01-01T00:00:00", command=Command(action="GO"))
        self.assertEqual(r.thinking, "thinking...")
        self.assertEqual(r.final, "final reply")
        self.assertEqual(r.command.action, "GO")

class TestMemoryEntry(unittest.TestCase):
    """MemoryEntry - memory vault entry."""
    def test_minimal_construct(self):
        from models import MemoryEntry
        m = MemoryEntry(id=1, content="some memory")
        self.assertEqual(m.id, 1)
        self.assertEqual(m.content, "some memory")
        self.assertEqual(m.node_type, "fact")
        self.assertEqual(m.importance, 5)
        self.assertEqual(m.tags, [])
        self.assertIsNone(m.embedding)
    def test_importance_bounds(self):
        from models import MemoryEntry
        m1 = MemoryEntry(id=1, content="x", importance=1)
        m10 = MemoryEntry(id=1, content="y", importance=10)
        self.assertEqual(m1.importance, 1)
        self.assertEqual(m10.importance, 10)
    def test_importance_out_of_range(self):
        from models import MemoryEntry
        with self.assertRaises(Exception):
            MemoryEntry(id=1, content="z", importance=0)
        with self.assertRaises(Exception):
            MemoryEntry(id=1, content="z", importance=11)

class TestPluginResult(unittest.TestCase):
    """PluginResult - plugin execution result with ok/fail helpers."""
    def test_construct(self):
        from models import PluginResult
        r = PluginResult(success=True, command="TEST", message="It worked")
        self.assertTrue(r.success)
        self.assertEqual(r.command, "TEST")
        self.assertEqual(r.message, "It worked")
    def test_ok_classmethod(self):
        from models import PluginResult
        r = PluginResult.ok("OPEN_FILE", "Opened file", {"path": "/x"})
        self.assertTrue(r.success)
        self.assertEqual(r.command, "OPEN_FILE")
        self.assertEqual(r.message, "Opened file")
        self.assertEqual(r.data, {"path": "/x"})
    def test_fail_classmethod(self):
        from models import PluginResult
        r = PluginResult.fail("DELETE", "Permission denied")
        self.assertFalse(r.success)
        self.assertEqual(r.command, "DELETE")
        self.assertEqual(r.message, "Permission denied")
        self.assertIsNone(r.data)
    def test_to_display_success(self):
        from models import PluginResult
        r = PluginResult.ok("TEST", "Done")
        s = r.to_display()
        self.assertIn("Done", s)
    def test_to_display_failure(self):
        from models import PluginResult
        r = PluginResult.fail("TEST", "Failed")
        s = r.to_display()
        self.assertIn("Failed", s)
