"""
mcp_server.py — MCP (Model Context Protocol) server for KokertechAI plugins.

Sprint 5.1: Exposes all 24 plugins as MCP tools via HTTP (REST) and stdio transports.
Supports tools/list and tools/call JSON-RPC methods.

CLI Usage
----------
Run as a standalone MCP server:

    python mcp_server.py --http                       # HTTP mode on 127.0.0.1:9100 (default)
    python mcp_server.py --http --port 8080            # Custom port
    python mcp_server.py --http --host 0.0.0.0         # Bind to all interfaces
    python mcp_server.py --http --host 0.0.0.0 --port 8080  # Full custom
    python mcp_server.py --help                         # Show this help and exit
    python mcp_server.py                               # stdio mode (default, reads JSON-RPC from stdin)

Keyboard shortcuts:
    Ctrl+C  — graceful shutdown (closes socket, releases port)
    kill <pid>     — Unix: same graceful shutdown path (SIGTERM handler)
    kill -HUP <pid> — Unix: same graceful shutdown path (SIGHUP handler)
"""

import json
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from logging_config import get_logger
from plugin_registry import registry


logger = get_logger(name="MCPServer")

# Module-level state for HTTP server lifecycle
_http_server = None
_http_server_thread = None
_http_server_running = False


def _plugin_to_mcp_tool(cmd, meta):
    """Convert a plugin to MCP tool format."""
    schema = meta.get("schema", {})
    properties = {}
    for k, v in schema.items():
        if k == "action":
            continue
        desc = str(v).replace("<", "&lt;").replace(">", "&gt;")
        properties[k] = {"type": "string", "description": desc}
    return {
        "name": cmd,
        "description": meta.get("description", ""),
        "inputSchema": {
            "type": "object",
            "properties": properties,
        },
    }


def handle_list_tools():
    """Return all enabled plugins as MCP tools."""
    tools = []
    for cmd in registry.plugins:
        if registry.is_enabled(cmd):
            meta = registry.metadata.get(cmd, {})
            tools.append(_plugin_to_mcp_tool(cmd, meta))
    return {"tools": tools}


def handle_call_tool(name, arguments):
    """Execute a plugin and return MCP-formatted result."""
    if name not in registry.plugins:
        return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {name}"}]}
    if not registry.is_enabled(name):
        return {"isError": True, "content": [{"type": "text", "text": f"Tool disabled: {name}"}]}
    try:
        intent = {"action": name, **arguments}
        result = registry.execute_command(intent)
        return {"content": [{"type": "text", "text": str(result)}]}
    except Exception as e:
        return {"isError": True, "content": [{"type": "text", "text": str(e)}]}


def handle_jsonrpc(request_body):
    """Handle a JSON-RPC request, return JSON-RPC response."""
    try:
        req = json.loads(request_body)
    except (json.JSONDecodeError, ValueError):
        return json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None})

    method = req.get("method", "")
    req_id = req.get("id")

    # Notifications (no id) return None per JSON-RPC spec
    if req_id is None:
        return None

    if method == "initialize":
        return json.dumps({"jsonrpc": "2.0", "result": {"protocolVersion": "2025-03-26"}, "id": req_id})

    if method == "tools/list":
        try:
            result = handle_list_tools()
            return json.dumps({"jsonrpc": "2.0", "result": result, "id": req_id})
        except Exception as e:
            return json.dumps({"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": req_id})

    if method == "tools/call":
        try:
            params = req.get("params", {})
            result = handle_call_tool(params.get("name", ""), params.get("arguments", {}))
            return json.dumps({"jsonrpc": "2.0", "result": result, "id": req_id})
        except Exception as e:
            return json.dumps({"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": req_id})

    return json.dumps({"jsonrpc": "2.0", "error": {"code": -32601, "message": "Method not found"}, "id": req_id})


class _MCPHandler(BaseHTTPRequestHandler):
    """HTTP request handler that dispatches JSON-RPC requests."""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        response = handle_jsonrpc(body)
        if response is None:
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # Suppress HTTP server log output


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server with threading support for concurrent requests."""
    allow_reuse_address = True


def start_http_server(host="127.0.0.1", port=9100):
    """Start MCP server in HTTP mode on a background thread.

    Stores server reference for graceful shutdown via stop_http_server().
    Returns (server, thread).
    """
    global _http_server, _http_server_thread, _http_server_running

    # If already running, return existing server
    if _http_server is not None and _http_server_running:
        return _http_server, _http_server_thread

    server = _ThreadedHTTPServer((host, port), _MCPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    _http_server = server
    _http_server_thread = thread
    _http_server_running = True

    return server, thread


def stop_http_server(timeout=5):
    """Gracefully shut down the MCP HTTP server.

    Calls server.shutdown() and waits for the thread to finish.
    """
    global _http_server, _http_server_thread, _http_server_running

    if _http_server is None:
        return

    try:
        _http_server.shutdown()
        _http_server.server_close()
    except Exception:
        pass

    if _http_server_thread is not None and _http_server_thread.is_alive():
        _http_server_thread.join(timeout=timeout)

    _http_server = None
    _http_server_thread = None
    _http_server_running = False


def start_stdio_server():
    """Run MCP server in stdio mode (blocking). Reads JSON-RPC from stdin, writes to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        response = handle_jsonrpc(line)
        if response is not None:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()


def _handle_sigterm(signum, frame):
    """Convert SIGTERM (Unix kill) into a KeyboardInterrupt for graceful shutdown."""
    raise KeyboardInterrupt()


def _handle_sighup(signum, frame):
    """Convert SIGHUP (Unix terminal disconnect / reload) into a KeyboardInterrupt."""
    raise KeyboardInterrupt()


def _parse_args(argv):
    """Parse CLI arguments for standalone MCP server invocation.

    Returns a dict with keys:
        http: bool — run in HTTP mode
        host: str — bind address (default 127.0.0.1)
        port: int — HTTP port (default 9100)
    """
    result = {"http": False, "host": "127.0.0.1", "port": 9100}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--http":
            result["http"] = True
        elif arg == "--host":
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                result["host"] = argv[i + 1]
                i += 1
            # else keep default
        elif arg == "--port":
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                try:
                    result["port"] = int(argv[i + 1])
                except (ValueError, TypeError):
                    pass  # Keep default on invalid port
                i += 1
            # else keep default
        elif arg == "--help":
            _print_usage()
        i += 1
    return result


def _print_usage():
    """Print usage information and exit."""
    print(__doc__)
    sys.exit(0)
