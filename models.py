"""
models.py — Pydantic v2 models for KokertechAI.

Provides typed, validated data structures used across the application.
These replace the earlier dataclass-based definitions in app_types.py
with full Pydantic validation, serialization, and schema generation.

Usage:
    from models import ChatMessage, MemoryEntry, PluginResult, Command, AIResponse
"""

from typing import Optional, List, Dict, Tuple, Any

from pydantic import BaseModel, Field

from logging_config import get_logger

logger = get_logger(name="MemoryVault")


class ChatMessage(BaseModel):
    """A single chat message with role and content."""
    role: str
    content: str

    model_config = {"frozen": True}


class Command(BaseModel):
    """Parsed plugin command from AI output."""
    action: str
    path: Optional[str] = None
    content: Optional[str] = None
    query: Optional[str] = None


class MemoryEntry(BaseModel):
    """Memory vault entry with importance scoring."""
    id: int
    content: str
    node_type: str = "fact"
    importance: int = Field(default=5, ge=1, le=10)
    tags: List[str] = Field(default_factory=list)
    embedding: Optional[Any] = None


class PluginResult:
    """Plugin execution result with ok/fail helpers."""
    def __init__(self, success: bool, command: str, message: str, data: Optional[dict] = None):
        self.success = success
        self.command = command
        self.message = message
        self.data = data

    @classmethod
    def ok(cls, command: str, message: str, data: Optional[dict] = None) -> "PluginResult":
        """Create a success result."""
        return cls(success=True, command=command, message=message, data=data)

    @classmethod
    def fail(cls, command: str, message: str, data: Optional[dict] = None) -> "PluginResult":
        """Create a failure result."""
        return cls(success=False, command=command, message=message, data=data)

    def to_display(self) -> str:
        """Return a human-readable display string."""
        return f"{'✅' if self.success else '❌'} {self.command}: {self.message}"


class FunctionCall:
    """Represents a function call with name and arguments."""
    def __init__(self, name: str = "", arguments: dict = None):
        self.name = name
        self.arguments = arguments or {}


class ToolCall:
    """Represents a tool call with id, type, and function."""
    def __init__(self, id: str = None, type: str = "function", function: FunctionCall = None):
        self.id = id
        self.type = type
        self.function = function or FunctionCall()


class AIResponse:
    """Represents an AI response with thinking, final output, and tool calls."""
    def __init__(self, thinking: str = "", final: str = "", ts: Optional[str] = None,
                 command: Optional["Command"] = None, tool_calls: list = None,
                 error: Optional[str] = None):
        self.thinking = thinking
        self.final = final
        self.ts = ts
        self.command = command
        self.tool_calls = tool_calls or []
        self.error = error

    def model_dump(self) -> dict:
        """Return a dict representation matching Pydantic model_dump()."""
        return {
            "thinking": self.thinking,
            "final": self.final,
            "ts": self.ts,
            "command": self.command,
            "tool_calls": self.tool_calls,
            "error": self.error,
        }
