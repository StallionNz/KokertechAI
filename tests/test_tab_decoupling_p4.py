"""
Tests for Phase P4: Tab Decoupling onto DashboardContext.

Verifies that all Phase P4 migrated tab mixins support the DashboardContext
property and setter pattern, maintaining backward compatibility with legacy
attributes while decoupling cross-tab state.
"""

import unittest
from unittest.mock import MagicMock
from PyQt6.QtCore import QObject
from tabs.context import DashboardContext

from tabs import (
    WorkspaceTabMixin,
    NeuralGraphTabMixin,
    SettingsTabMixin,
    CognitiveAgencyTabMixin,
    RLHFTrainerTabMixin,
    DocumentPipelineTabMixin,
    WorkflowTabMixin,
    ComputerUseTabMixin,
    HealthTabMixin,
    GitTrackerTabMixin,
    ChatHistoryTabMixin,
    ThemeBuilderTabMixin,
    RagTabMixin,
    SessionBrowserTabMixin,
    WebViewTabMixin,
    SessionLogTabMixin,
    SessionJournalTabMixin,
    MemoryBrowserTabMixin,
    ScheduledActionsTabMixin,
    PluginStoreTabMixin,
    PersonaABTestingTabMixin,
    MCPClientTabMixin,
    ToolAuditTabMixin,
    ProgressTabMixin,
)


ALL_P4_TAB_MIXINS = [
    WorkspaceTabMixin,
    NeuralGraphTabMixin,
    SettingsTabMixin,
    CognitiveAgencyTabMixin,
    RLHFTrainerTabMixin,
    DocumentPipelineTabMixin,
    WorkflowTabMixin,
    ComputerUseTabMixin,
    HealthTabMixin,
    GitTrackerTabMixin,
    ChatHistoryTabMixin,
    ThemeBuilderTabMixin,
    RagTabMixin,
    SessionBrowserTabMixin,
    WebViewTabMixin,
    SessionLogTabMixin,
    SessionJournalTabMixin,
    MemoryBrowserTabMixin,
    ScheduledActionsTabMixin,
    PluginStoreTabMixin,
    PersonaABTestingTabMixin,
    MCPClientTabMixin,
    ToolAuditTabMixin,
    ProgressTabMixin,
]


class TestTabDecouplingP4(unittest.TestCase):
    """Test suite for Phase P4 Tab Decoupling onto DashboardContext."""

    def test_all_p4_mixins_have_context_property(self):
        """Verify every P4 mixin has a context property with getter and setter."""
        for mixin_cls in ALL_P4_TAB_MIXINS:
            with self.subTest(mixin=mixin_cls.__name__):
                self.assertTrue(
                    hasattr(mixin_cls, "context"),
                    f"{mixin_cls.__name__} must define a 'context' property",
                )
                prop = mixin_cls.context
                self.assertIsInstance(
                    prop,
                    property,
                    f"{mixin_cls.__name__}.context must be a property",
                )
                self.assertIsNotNone(
                    prop.fset,
                    f"{mixin_cls.__name__}.context must define a setter",
                )

    def test_legacy_fallback_constructs_dashboard_context(self):
        """Verify fallback constructs DashboardContext from legacy attributes when ctx is unset."""
        for mixin_cls in ALL_P4_TAB_MIXINS:
            with self.subTest(mixin=mixin_cls.__name__):
                # Instantiate a dynamic subclass with legacy attributes
                instance = type(f"Mock_{mixin_cls.__name__}", (mixin_cls, QObject), {})()
                instance.ctx = None
                instance.log_to_audit = MagicMock()
                instance.controller = MagicMock()
                instance.config = {"test_key": "val123"}

                ctx = instance.context
                self.assertIsInstance(ctx, DashboardContext)
                self.assertEqual(ctx.get_config("test_key"), "val123")
                self.assertIs(ctx.controller, instance.controller)

                # Calling ctx.log should route to instance.log_to_audit
                ctx.log("test fallback message")
                instance.log_to_audit.assert_called_once_with("test fallback message")

    def test_explicit_context_setter(self):
        """Verify explicit context assignment overrides legacy fallback."""
        for mixin_cls in ALL_P4_TAB_MIXINS:
            with self.subTest(mixin=mixin_cls.__name__):
                instance = type(f"Mock_{mixin_cls.__name__}", (mixin_cls, QObject), {})()
                custom_logged = []
                custom_ctx = DashboardContext(
                    log_to_audit=lambda msg, acc=custom_logged: acc.append(msg),
                    config={"p4_active": True},
                )

                instance.context = custom_ctx
                self.assertIs(instance.context, custom_ctx)
                self.assertTrue(instance.context.get_config("p4_active"))

                instance.context.log("p4 event")
                self.assertEqual(custom_logged, ["p4 event"])


if __name__ == "__main__":
    unittest.main()
