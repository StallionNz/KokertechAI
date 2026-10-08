"""
app_types.py — Backward-compatible re-exports from models.py.

Provides the original dataclass-based types for legacy compatibility.
New code should import directly from models.py for Pydantic validation.

Usage (legacy):
    from app_types import ChatMessage, MemoryEntry, Command, AIResponse

Usage (preferred):
    from models import ChatMessage, MemoryEntry, PluginResult, Command, AIResponse
"""

from models import AIResponse, ChatMessage, Command, MemoryEntry  # noqa: F401
from tabs.context import DashboardContext  # noqa: F401

__all__ = ["AIResponse", "ChatMessage", "Command", "MemoryEntry", "DashboardContext"]
