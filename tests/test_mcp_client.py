import sys
"""
Tests for mcp_client.py -- MCPClientManager + MCPServerConnection.
"""
import json
import threading
import unittest
from unittest.mock import patch, MagicMock

from mcp_client import (
    MCPServerConnection, MCPClientManager,
    get_mcp_client, reset_mcp_client, initialize_from_config,
    MCP_PROTOCOL_VERSION, DEFAULT_TIMEOUT, DISCOVERY_TIMEOUT,
)


class TestMCPServerConnectionInit(unittest.TestCase):
    def test_defaults(self):
        conn = MCPServerConnection(name="Test", url="http://localhost:9100")
        self.assertEqual(conn.name, "Test")
        self.assertTrue(conn.enabled)
        self.assertEqual(conn.timeout, DEFAULT_TIMEOUT)
        self.assertEqual(conn.tools, [])
    def test_custom_params(self):
        conn = MCPServerConnection(name="X", url="http://a:3000/", enabled=False, timeout=5)
        self.assertFalse(conn.enabled)
        self.assertEqual(conn.url, "http://a:3000")
    def test_repr(self):
        conn = MCPServerConnection(name="A", url="http://x:1")
        self.assertIn("A", repr(conn))

class TestMCPServerConnectionSendRequest(unittest.TestCase):
    def _conn(self):
        return MCPServerConnection(name="T", url="http://t:1")

    @patch("mcp_client.requests.post")
    def test_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp
        result = self._conn()._send_request("initialize", {"foo": "bar"})
        self.assertEqual(result, {"ok": True})
        mock_post.assert_called_once()

    @patch("mcp_client.requests.post")
    def test_jsonrpc_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Bad"}}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp
        with self.assertRaises(RuntimeError) as ctx:
            self._conn()._send_request("test")
        self.assertIn("-32600", str(ctx.exception))

    @patch("mcp_client.requests.post")
    def test_timeout(self, mock_post):
        import requests as real_requests
        mock_post.side_effect = real_requests.Timeout("timed out")
        with self.assertRaises(TimeoutError):
            self._conn()._send_request("test")

    @patch("mcp_client.requests.post")
    def test_connection_error(self, mock_post):
        import requests as real_requests
        mock_post.side_effect = real_requests.ConnectionError("refused")
        with self.assertRaises(ConnectionError):
            self._conn()._send_request("test")

class TestMCPServerConnectionLifecycle(unittest.TestCase):
    def _mock_conn(self):
        return MCPServerConnection(name="S", url="http://s:1")

    @patch.object(MCPServerConnection, "_send_request")
    def test_initialize_success(self, mock_req):
        mock_req.return_value = {"serverInfo": {"name": "srv", "version": "1.0"}}
        conn = self._mock_conn()
        self.assertTrue(conn.initialize())
        self.assertEqual(conn.server_info["name"], "srv")

    def test_initialize_disabled(self):
        conn = self._mock_conn()
        conn.enabled = False
        self.assertFalse(conn.initialize())

    @patch.object(MCPServerConnection, "_send_request")
    def test_initialize_failure(self, mock_req):
        mock_req.side_effect = ConnectionError("nope")
        conn = self._mock_conn()
        self.assertFalse(conn.initialize())
        self.assertIn("nope", conn.error)

    @patch.object(MCPServerConnection, "_send_request")
    def test_discover_tools(self, mock_req):
        mock_req.return_value = {"tools": [{"name": "t1", "description": "d"}]}
        conn = self._mock_conn()
        conn.server_info = {"name": "x"}
        tools = conn.discover_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "t1")

    def test_discover_tools_disabled(self):
        conn = self._mock_conn()
        conn.enabled = False
        self.assertEqual(conn.discover_tools(), [])

    @patch.object(MCPServerConnection, "_send_request")
    def test_call_tool_success(self, mock_req):
        mock_req.return_value = {"content": [{"type": "text", "text": "result"}]}
        result = self._mock_conn().call_tool("my_tool", {"arg1": "val"})
        self.assertFalse(result.get("isError"))
        self.assertEqual(result["content"][0]["text"], "result")

    def test_call_tool_disabled(self):
        conn = self._mock_conn()
        conn.enabled = False
        result = conn.call_tool("t", {})
        self.assertTrue(result["isError"])

    @patch.object(MCPServerConnection, "_send_request")
    def test_call_tool_error(self, mock_req):
        mock_req.side_effect = RuntimeError("fail")
        result = self._mock_conn().call_tool("t", {})
        self.assertTrue(result["isError"])
        self.assertIn("fail", result["content"][0]["text"])

    @patch.object(MCPServerConnection, "_send_request")
    def test_call_tool_wraps_no_content(self, mock_req):
        mock_req.return_value = {"data": "raw"}
        result = self._mock_conn().call_tool("t", {})
        self.assertIn("content", result)

    @patch.object(MCPServerConnection, "_send_request")
    def test_check_health_ok(self, mock_req):
        mock_req.return_value = {"tools": [{"name": "a"}, {"name": "b"}]}
        health = self._mock_conn().check_health()
        self.assertTrue(health["ok"])
        self.assertEqual(health["tool_count"], 2)

    def test_check_health_disabled(self):
        conn = self._mock_conn()
        conn.enabled = False
        self.assertFalse(conn.check_health()["ok"])

    @patch.object(MCPServerConnection, "_send_request")
    def test_check_health_offline(self, mock_req):
        mock_req.side_effect = ConnectionError("down")
        health = self._mock_conn().check_health()
        self.assertFalse(health["ok"])
        self.assertIn("down", health["error"])

    @patch.object(MCPServerConnection, "_send_request")
    def test_discover_tools_auto_init(self, mock_req):
        call_count = [0]
        def _se(method, params=None, timeout=None):
            call_count[0] += 1
            if method == "initialize":
                return {"serverInfo": {"name": "x"}}
            elif method == "tools/list":
                return {"tools": [{"name": "t1"}]}
            return {}
        mock_req.side_effect = _se
        tools = self._mock_conn().discover_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(call_count[0], 2)

class TestMCPClientManager(unittest.TestCase):
    def setUp(self):
        reset_mcp_client()

    def test_load_from_config(self):
        mgr = MCPClientManager()
        mgr.load_from_config({"mcp_servers": [
            {"name": "A", "url": "http://a:1", "enabled": True, "timeout": 10},
            {"name": "B", "url": "http://b:2", "enabled": False},
        ]})
        self.assertEqual(len(mgr.servers), 2)
        self.assertEqual(mgr.servers["A"].timeout, 10)
        self.assertFalse(mgr.servers["B"].enabled)

    def test_load_empty(self):
        mgr = MCPClientManager()
        mgr.load_from_config({})
        self.assertEqual(len(mgr.servers), 0)

    def test_load_non_list(self):
        mgr = MCPClientManager()
        mgr.load_from_config({"mcp_servers": "bad"})
        self.assertEqual(len(mgr.servers), 0)

    def test_load_removes_old(self):
        mgr = MCPClientManager()
        mgr.load_from_config({"mcp_servers": [{"name": "A", "url": "http://a:1"}]})
        self.assertIn("A", mgr.servers)
        mgr.load_from_config({"mcp_servers": []})
        self.assertNotIn("A", mgr.servers)

    def test_load_updates_existing(self):
        mgr = MCPClientManager()
        mgr.load_from_config({"mcp_servers": [{"name": "A", "url": "http://old:1", "timeout": 5}]})
        mgr.load_from_config({"mcp_servers": [{"name": "A", "url": "http://new:2", "timeout": 20}]})
        self.assertEqual(mgr.servers["A"].url, "http://new:2")

    def test_to_config(self):
        mgr = MCPClientManager()
        mgr.load_from_config({"mcp_servers": [{"name": "A", "url": "http://a:1", "enabled": True, "timeout": 15}]})
        cfg = mgr.to_config()
        self.assertEqual(len(cfg), 1)

    def test_discover_all(self):
        mgr = MCPClientManager()
        mgr.load_from_config({"mcp_servers": [
            {"name": "A", "url": "http://a:1"},
            {"name": "B", "url": "http://b:1", "enabled": False},
        ]})
        with patch.object(MCPServerConnection, "discover_tools", return_value=[{"name": "t1"}]):
            results = mgr.discover_all()
        self.assertEqual(results["A"], 1)
        self.assertNotIn("B", results)

    def test_sync_to_registry(self):
        mock_reg = MagicMock()
        mock_reg.plugins = {}
        mock_reg.disabled = set()
        mock_reg.metadata = {}
        mgr = MCPClientManager()
        mgr.load_from_config({"mcp_servers": [{"name": "Test", "url": "http://t:1"}]})
        mgr.servers["Test"].tools = [{
            "name": "my_tool", "description": "A tool",
            "inputSchema": {"properties": {"q": {"type": "string", "description": "query"}}},
        }]
        with patch.dict("sys.modules", {"plugin_registry": MagicMock(registry=mock_reg)}):
            mgr.sync_to_registry()
        self.assertIn("MCP_TEST_MY_TOOL", mock_reg.plugins)
        self.assertEqual(len(mgr._synced_commands), 1)

    def test_sync_clears_old(self):
        mock_reg = MagicMock()
        mock_reg.plugins = {"OLD_CMD": MagicMock()}
        mock_reg.disabled = set()
        mock_reg.metadata = {}
        mgr = MCPClientManager()
        mgr._synced_commands = {"OLD_CMD": "Old"}
        mgr.servers = {}
        with patch.dict("sys.modules", {"plugin_registry": MagicMock(registry=mock_reg)}):
            mgr.sync_to_registry()
        self.assertNotIn("OLD_CMD", mock_reg.plugins)

    def test_get_server_status(self):
        mgr = MCPClientManager()
        mgr.load_from_config({"mcp_servers": [{"name": "A", "url": "http://a:1"}]})
        with patch.object(MCPServerConnection, "check_health",
                          return_value={"ok": True, "error": None, "tool_count": 3}):
            status = mgr.get_server_status()
        self.assertTrue(status["A"]["ok"])

    def test_get_summary_empty(self):
        self.assertIn("no MCP servers", MCPClientManager().get_summary())

    def test_get_summary_with_servers(self):
        mgr = MCPClientManager()
        mgr.load_from_config({"mcp_servers": [{"name": "A", "url": "http://a:1"}]})
        with patch.object(MCPServerConnection, "check_health",
                          return_value={"ok": True, "error": None, "tool_count": 5}):
            s = mgr.get_summary()
        self.assertIn("A", s)
        self.assertIn("5 tools", s)

class TestMCPSingleton(unittest.TestCase):
    def setUp(self):
        reset_mcp_client()
    def tearDown(self):
        reset_mcp_client()

    def test_singleton(self):
        self.assertIs(get_mcp_client(), get_mcp_client())

    def test_reset(self):
        a = get_mcp_client()
        reset_mcp_client()
        self.assertIsNot(a, get_mcp_client())

    def test_initialize_from_config(self):
        with patch.object(MCPClientManager, "discover_all", return_value={}), \
             patch.object(MCPClientManager, "sync_to_registry"):
            client = initialize_from_config({"mcp_servers": [{"name": "X", "url": "http://x:1"}]})
        self.assertIn("X", client.servers)

class TestMCPThreadSafety(unittest.TestCase):
    def test_concurrent_discover(self):
        mgr = MCPClientManager()
        mgr.load_from_config({"mcp_servers": [
            {"name": f"S{i}", "url": f"http://s{i}:1"} for i in range(5)
        ]})
        with patch.object(MCPServerConnection, "discover_tools", return_value=[{"name": "t"}]):
            errors = []
            def _d():
                try:
                    mgr.discover_all()
                except Exception as e:
                    errors.append(e)
            ts = [threading.Thread(target=_d) for _ in range(10)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(errors, [])

if __name__ == "__main__":
    unittest.main()

