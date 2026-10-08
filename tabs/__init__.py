"""
Tab mixin classes for KokertechDashboard.
Each module provides a mixin that adds one tab + its action methods.
"""
from .workspace_tab import WorkspaceTabMixin
from .neural_graph_tab import NeuralGraphTabMixin
from .settings_tab import SettingsTabMixin
from .cognitive_agency_tab import CognitiveAgencyTabMixin
from .rlhf_trainer_tab import RLHFTrainerTabMixin
from .document_pipeline_tab import DocumentPipelineTabMixin
from .workflow_tab import WorkflowTabMixin
from .computer_use_tab import ComputerUseTabMixin
from .health_tab import HealthTabMixin
from .git_tracker_tab import GitTrackerTabMixin
from .chat_history_tab import ChatHistoryTabMixin
from .theme_builder_tab import ThemeBuilderTabMixin
from .session_browser_tab import SessionBrowserTabMixin
from .progress_tab import ProgressTabMixin
from .rag_tab import RagTabMixin
from .web_view_tab import WebViewTabMixin
from .session_log_tab import SessionLogTabMixin
from .session_journal_tab import SessionJournalTabMixin
from .memory_browser_tab import MemoryBrowserTabMixin
from .scheduled_actions_tab import ScheduledActionsTabMixin
from .plugin_store_tab import PluginStoreTabMixin
from .persona_ab_tab import PersonaABTestingTabMixin
from .mcp_client_tab import MCPClientTabMixin
from .tool_audit_tab import ToolAuditTabMixin
from .global_search import GlobalSearchDialog
from .mini_hud import MiniHudDialog
from .context import DashboardContext

__all__ = [
    "WorkspaceTabMixin",
    "NeuralGraphTabMixin",
    "SettingsTabMixin",
    "CognitiveAgencyTabMixin",
    "RLHFTrainerTabMixin",
    "DocumentPipelineTabMixin",
    "WorkflowTabMixin",
    "ComputerUseTabMixin",
    "HealthTabMixin",
    "GitTrackerTabMixin",
    "ChatHistoryTabMixin",
    "ThemeBuilderTabMixin",
    "RagTabMixin",
    "SessionBrowserTabMixin",
    "WebViewTabMixin",
    "SessionLogTabMixin",
    "SessionJournalTabMixin",
    "MemoryBrowserTabMixin",
    "ScheduledActionsTabMixin",
    "PluginStoreTabMixin",
    "PersonaABTestingTabMixin",
    "MCPClientTabMixin",
    "ToolAuditTabMixin",
    "ProgressTabMixin",
    "GlobalSearchDialog",
    "MiniHudDialog",
    "DashboardContext",
]
