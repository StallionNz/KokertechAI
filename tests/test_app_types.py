"""Tests for app_types.py - backward-compatible re-exports from models.py."""

import unittest


class TestReExports(unittest.TestCase):
    def test_chat_message_imported(self):
        from app_types import ChatMessage
        from models import ChatMessage as ModelsChatMessage
        self.assertIs(ChatMessage, ModelsChatMessage)
    def test_memory_entry_imported(self):
        from app_types import MemoryEntry
        from models import MemoryEntry as ModelsMemoryEntry
        self.assertIs(MemoryEntry, ModelsMemoryEntry)
    def test_command_imported(self):
        from app_types import Command
        from models import Command as ModelsCommand
        self.assertIs(Command, ModelsCommand)
    def test_ai_response_imported(self):
        from app_types import AIResponse
        from models import AIResponse as ModelsAIResponse
        self.assertIs(AIResponse, ModelsAIResponse)

    def test_dashboard_context_imported(self):
        from app_types import DashboardContext
        from tabs.context import DashboardContext as TabsDashboardContext
        self.assertIs(DashboardContext, TabsDashboardContext)

    def test_dashboard_context_helpers(self):
        from app_types import DashboardContext
        logged = []
        ctx = DashboardContext(
            log_to_audit=lambda msg: logged.append(msg),
            config={"foo": "bar"}
        )
        ctx.log("test log")
        self.assertEqual(logged, ["test log"])
        self.assertEqual(ctx.get_config("foo"), "bar")
        self.assertEqual(ctx.get_config("missing", "default"), "default")
