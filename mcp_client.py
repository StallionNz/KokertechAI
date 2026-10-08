"""
mcp_client.py — MCP (Model Context Protocol) Client for KokertechAI.

Connects to external MCP servers via HTTP, discovers their tools,
and makes them callable through the plugin registry.

Supports:
  - Multiple concurrent server connections
  - Automatic tool discovery via tools/list
  - Tool invocation via tools/call
  - JSON-RPC 2.0 protocol
  - Per-server enable/disable
  - Connection health checks

Sprint 5 backlog item: MCP Client Integration.
"""

import json
import threading
from typing import Optional, List, Dict, Tuple, Any

import requests

from logging_config import get_logger


logger = get_logger(name="MCPClient")

MCP_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT = 30
DISCOVERY_TIMEOUT = 10


class MCPServerConnection:
    """Connection to a single MCP server."""

    def __init__(self, name: str, url: str, enabled: bool = True,
                 timeout: int = DEFAULT_TIMEOUT):
        self.name = name
        self.url = url.rstrip("/")
        self.enabled = enabled
        self.timeout = timeout
        self.tools: list = []
        self.server_info: Optional[dict] = None
        self.error: Optional[str] = None

    def __repr__(self) -> str:
        return f"MCPServerConnection({self.name}, {self.url})"

    def _send_request(self, method: str, params: dict = None,
                      timeout: int = None) -> dict:
        """Send a JSON-RPC request to the MCP server and return the result."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        try:
            resp = requests.post(
                self.url,
                json=payload,
                timeout=timeout or self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.Timeout:
            raise TimeoutError(f"MCP request timed out: {method}")
        except requests.ConnectionError as e:
            raise ConnectionError(f"MCP connection failed: {e}")
        except Exception as e:
            raise RuntimeError(f"MCP request failed: {e}")

        error = data.get("error")
        if error:
            raise RuntimeError(
                f"MCP error {error.get('code', '?')}: {error.get('message', '?')}"
            )
        return data.get("result", {})

    def initialize(self) -> bool:
        """Initialize the connection with the MCP server."""
        if not self.enabled:
            return False
        try:
            result = self._send_request("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "KokertechAI", "version": "1.0"},
            })
            self.server_info = result.get("serverInfo", {})
            return True
        except Exception as e:
            self.error = str(e)
            return False

    def discover_tools(self) -> list:
        """Discover tools from the MCP server."""
        if not self.enabled:
            return []
        if self.server_info is None:
            if not self.initialize():
                return []
        try:
            result = self._send_request("tools/list", timeout=DISCOVERY_TIMEOUT)
            self.tools = result.get("tools", [])
            return self.tools
        except Exception as e:
            self.error = str(e)
            return []

    def call_tool(self, name: str, arguments: dict) -> dict:
        """Call a tool on the MCP server."""
        if not self.enabled:
            return {"isError": True, "content": [{"type": "text", "text": "Server is disabled"}]}
        try:
            result = self._send_request("tools/call", {
                "name": name,
                "arguments": arguments,
            })
            if "content" not in result:
                result["content"] = [{"type": "text", "text": json.dumps(result)}]
            return result
        except Exception as e:
            return {"isError": True, "content": [{"type": "text", "text": str(e)}]}

    def check_health(self) -> dict:
        """Check if the MCP server is healthy."""
        if not self.enabled:
            return {"ok": False, "error": "Server is disabled", "tool_count": 0}
        try:
            result = self._send_request("tools/list", timeout=DISCOVERY_TIMEOUT)
            tools = result.get("tools", [])
            return {"ok": True, "error": None, "tool_count": len(tools)}
        except Exception as e:
            return {"ok": False, "error": str(e), "tool_count": 0}


class MCPClientManager:
    """Manages multiple MCP server connections."""

    def __init__(self):
        self.servers: Dict[str, MCPServerConnection] = {}
        self._lock = threading.Lock()
        self._synced_commands: Dict[str, str] = {}

    def load_from_config(self, config: dict):
        """Load MCP server configurations from a config dict."""
        servers_data = config.get("mcp_servers", [])
        if not isinstance(servers_data, list):
            return

        with self._lock:
            new_names = set()
            for entry in servers_data:
                name = entry.get("name", "")
                if not name:
                    continue
                new_names.add(name)
                url = entry.get("url", "")
                enabled = entry.get("enabled", True)
                timeout = entry.get("timeout", DEFAULT_TIMEOUT)

                if name in self.servers:
                    existing = self.servers[name]
                    existing.url = url.rstrip("/")
                    existing.enabled = enabled
                    existing.timeout = timeout
                else:
                    self.servers[name] = MCPServerConnection(
                        name=name, url=url, enabled=enabled, timeout=timeout
                    )

            # Remove servers not in the new config
            for name in list(self.servers.keys()):
                if name not in new_names:
                    del self.servers[name]

    def to_config(self) -> list:
        """Export current server configurations to a list."""
        result = []
        for name, conn in self.servers.items():
            result.append({
                "name": name,
                "url": conn.url,
                "enabled": conn.enabled,
                "timeout": conn.timeout,
            })
        return result

    def discover_all(self) -> Dict[str, int]:
        """Discover tools on all enabled servers."""
        results = {}
        for name, conn in self.servers.items():
            if not conn.enabled:
                continue
            tools = conn.discover_tools()
            results[name] = len(tools)
        return results

    def sync_to_registry(self):
        """Sync MCP tools to the plugin registry."""
        from plugin_registry import registry

        new_commands = {}

        for conn_name, conn in self.servers.items():
            if not conn.enabled:
                continue
            for tool in conn.tools:
                cmd_name = f"MCP_{conn_name.upper()}_{tool['name'].upper()}"
                new_commands[cmd_name] = conn_name

                if cmd_name not in registry.plugins:
                    schema = tool.get("inputSchema", {})
                    properties = schema.get("properties", {})
                    registry.plugins[cmd_name] = object()
                    registry.metadata[cmd_name] = {
                        "description": tool.get("description", ""),
                        "schema": {k: v.get("description", "") for k, v in properties.items()},
                    }

        # Remove old commands no longer in sync
        for old_cmd in list(self._synced_commands.keys()):
            if old_cmd not in new_commands:
                registry.plugins.pop(old_cmd, None)
                registry.metadata.pop(old_cmd, None)

        self._synced_commands = new_commands

    def get_server_status(self) -> Dict[str, dict]:
        """Get health status for all servers."""
        results = {}
        for name, conn in self.servers.items():
            results[name] = conn.check_health()
        return results

    def get_summary(self) -> str:
        """Get a human-readable summary of all servers."""
        if not self.servers:
            return "no MCP servers configured."

        parts = []
        for name, conn in self.servers.items():
            status = "enabled" if conn.enabled else "disabled"
            health = conn.check_health()
            tool_count = health.get("tool_count", len(conn.tools))
            parts.append(f"{name} ({status}, {tool_count} tools)")
        return "; ".join(parts)


# Module-level singleton
_client_instance: Optional[MCPClientManager] = None
_client_lock = threading.Lock()


def get_mcp_client() -> MCPClientManager:
    """Get or create the singleton MCPClientManager."""
    global _client_instance
    if _client_instance is not None:
        return _client_instance
    with _client_lock:
        if _client_instance is None:
            _client_instance = MCPClientManager()
        return _client_instance


def reset_mcp_client():
    """Reset the singleton (useful for testing)."""
    global _client_instance
    with _client_lock:
        _client_instance = None


def initialize_from_config(config: dict) -> MCPClientManager:
    """One-shot: load config, discover tools, and sync to registry.

    Call this on application startup.
    """
    client = get_mcp_client()
    client.load_from_config(config)
    client.discover_all()
    client.sync_to_registry()
    return client
