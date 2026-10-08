"""
tabs/context.py — Shared DashboardContext for composition-based tab decoupling.

Encapsulates shared core dependencies across dashboard tabs and mixins:
- controller: KokertechController instance
- file_logger: Logger instance for durable disk logging
- log_to_audit: Callable UI audit logging function
- audit_signal: PyQt signal for cross-thread audit marshaling
- config: Application configuration dictionary

This eliminates duck-typing and cross-tab private widget access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class DashboardContext:
    """Strongly-typed composition context for KokertechDashboard and its tabs."""
    controller: Any = None
    file_logger: Any = None
    log_to_audit: Optional[Callable[..., Any]] = None
    audit_signal: Any = None
    config: Dict[str, Any] = field(default_factory=dict)
    restore_chat_input: Optional[Callable[[str], None]] = None
    switch_tab: Optional[Callable[[int], None]] = None

    def log(self, message: str, is_debug: bool = False) -> None:
        """Safe audit logging helper that dispatches through log_to_audit, audit_signal, or file_logger."""
        if is_debug and not self.get_config("debug_logging", False):
            return
        logged = False
        if callable(self.log_to_audit):
            try:
                if is_debug:
                    try:
                        self.log_to_audit(message, is_debug=True)
                    except TypeError:
                        self.log_to_audit(message)
                else:
                    self.log_to_audit(message)
                logged = True
            except (RuntimeError, TypeError, AttributeError, OSError):
                pass
        if not logged and self.audit_signal is not None and hasattr(self.audit_signal, "emit"):
            try:
                self.audit_signal.emit(message)
                logged = True
            except (RuntimeError, TypeError, AttributeError, OSError):
                pass
        if self.file_logger is not None and hasattr(self.file_logger, "info"):
            try:
                self.file_logger.info(message)
            except (RuntimeError, TypeError, AttributeError, OSError):
                pass

    def get_config(self, key: str, default: Any = None) -> Any:
        """Safe config lookup with fallback per Zero-Trust Invariant 4."""
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return default
