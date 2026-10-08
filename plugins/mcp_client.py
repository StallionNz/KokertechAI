"""
plugins/mcp_client.py — MCP Client plugin for KokertechAI.

Wraps the MCPClientManager so external MCP servers can be configured
via the plugin system. Provides:
  - MCP_CONNECT: Add/connect to an MCP server
  - MCP_DISCOVER: Re-discover tools from all configured servers
  - MCP_STATUS: Show connection status for all servers
  - MCP_SUMMARY: Human-readable summary
  - MCP_CALL: Call a specific tool on a server

Sprint 5 backlog item: MCP Client Integration.
"""

from logging_config import get_logger

logger = get_logger(name="MCPClientPlugin")

PLUGIN_METADATA = {
    "name": "MCP Client",
    "description": "Connect to external MCP servers, discover their tools, and call them through the plugin registry",
    "version": "1.0.0",
    "tags": ["mcp", "external", "integration", "protocol"],
    "author": "KokertechAI",
    "requires": ["requests"],
    "permissions": ['network'],
}

COMMAND_NAME = "MCP_CONNECT"
SCHEMA = {
    "action": "MCP_CONNECT",
    "name": "<server name>",
    "url": "<MCP server URL e.g. http://127.0.0.1:9100>",
    "enabled": "<optional: true/false, default true>",
}


def execute(intent_json):
    """Execute an MCP client command.

    Supports:
      MCP_CONNECT  - Add or update an MCP server connection
      MCP_DISCOVER - Re-discover tools from all configured servers
      MCP_STATUS   - Show connection status for all servers
      MCP_SUMMARY  - Show human-readable summary
      MCP_CALL     - Call a specific tool on a server
    """
    action = intent_json.get("action", "MCP_CONNECT")

    try:
        from mcp_client import get_mcp_client
        from config import CONFIG, save_settings

        client = get_mcp_client()

        if action == "MCP_CONNECT":
            name = intent_json.get("name", "")
            url = intent_json.get("url", "")
            enabled = intent_json.get("enabled", True)
            if isinstance(enabled, str):
                enabled = enabled.lower() in ("true", "yes", "1")

            if not name:
                return "X MCP_CONNECT requires a 'name' parameter"
            if not url:
                return "X MCP_CONNECT requires a 'url' parameter"

            # Add to manager
            from mcp_client import MCPServerConnection
            client.servers[name] = MCPServerConnection(
                name=name, url=url, enabled=enabled
            )

            # Discover tools and sync
            conn = client.servers[name]
            tools = conn.discover_tools()
            client.sync_to_registry()

            # Persist to CONFIG
            CONFIG["mcp_servers"] = client.to_config()
            save_settings()

            return (
                f"Connected to MCP server '{name}' at {url}. "
                f"Discovered {len(tools)} tools. "
                f"They are now available as MCP_* plugin commands."
            )

        elif action == "MCP_DISCOVER":
            results = client.discover_all()
            client.sync_to_registry()
            total = sum(results.values())
            parts = [
                f"{name}: {count} tools"
                for name, count in sorted(results.items())
            ]
            parts_str = ", ".join(parts)
            return (
                f"Discovery complete — {total} tools across "
                f"{len(results)} servers. {parts_str}"
            )

        elif action == "MCP_STATUS":
            statuses = client.get_server_status()
            lines = ["MCP Server Status:"]
            for name, status in statuses.items():
                if status["ok"]:
                    lines.append(
                        f"  [OK] {name}: {status['tool_count']} tools"
                    )
                else:
                    lines.append(f"  [DOWN] {name}: {status['error']}")
            if not statuses:
                lines.append("  (no MCP servers configured)")
            return "\n".join(lines)

        elif action == "MCP_SUMMARY":
            return client.get_summary()

        elif action == "MCP_CALL":
            server = intent_json.get("server", "")
            tool = intent_json.get("tool", "")
            if not server or not tool:
                return (
                    "X MCP_CALL requires 'server' and 'tool' parameters"
                )
            args = {
                k: v
                for k, v in intent_json.items()
                if k not in ("action", "server", "tool")
            }
            result = client.call_tool(server, tool, args)
            texts = [
                c.get("text", "")
                for c in result.get("content", [])
                if c.get("type") == "text"
            ]
            output = "\n".join(texts)
            if result.get("isError"):
                return f"X {output}"
            return output

        else:
            return (
                f"X Unknown MCP action: {action}. "
                "Use MCP_CONNECT, MCP_DISCOVER, MCP_STATUS, "
                "MCP_SUMMARY, or MCP_CALL."
            )

    except ImportError as e:
        return f"X MCP client not available: {e}"
    except Exception as e:
        logger.error(f"MCP client command failed: {e}")
        return f"X MCP command failed: {str(e)}"
