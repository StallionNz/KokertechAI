"""Unit tests for mcp_server.py."""
import json, sys, unittest
from unittest.mock import MagicMock, patch


class TestPluginToMCPTool(unittest.TestCase):
    def test_converts_with_schema(self):
        from mcp_server import _plugin_to_mcp_tool
        meta = {"description": "Test", "schema": {"path": "File"}}
        tool = _plugin_to_mcp_tool("CMD", meta)
        self.assertEqual(tool["name"], "CMD")
        self.assertIn("path", tool["inputSchema"]["properties"])

    def test_excludes_action_key(self):
        from mcp_server import _plugin_to_mcp_tool
        tool = _plugin_to_mcp_tool("X", {"schema": {"action": "skip", "p": "ok"}})
        self.assertNotIn("action", tool["inputSchema"]["properties"])

    def test_sanitizes_brackets(self):
        from mcp_server import _plugin_to_mcp_tool
        tool = _plugin_to_mcp_tool("X", {"schema": {"i": "<a>"}})
        d = tool["inputSchema"]["properties"]["i"]["description"]
        self.assertNotIn("<", d)

class TestHandleListTools(unittest.TestCase):
    def test_returns_enabled(self):
        from mcp_server import handle_list_tools
        with patch("mcp_server.registry") as r:
            r.plugins = {"A": object(), "B": object()}
            r.is_enabled.side_effect = lambda c: c == "A"
            r.metadata = {"A": {"description": "A", "schema": {}}}
            result = handle_list_tools()
        self.assertEqual(len(result["tools"]), 1)
        self.assertEqual(result["tools"][0]["name"], "A")

class TestHandleCallTool(unittest.TestCase):
    def test_unknown(self):
        from mcp_server import handle_call_tool
        with patch("mcp_server.registry") as r:
            r.plugins = {}
            result = handle_call_tool("MISSING", {})
        self.assertTrue(result["isError"])

    def test_disabled(self):
        from mcp_server import handle_call_tool
        with patch("mcp_server.registry") as r:
            r.plugins = {"OFF": object()}
            r.is_enabled.return_value = False
            result = handle_call_tool("OFF", {})
        self.assertTrue(result["isError"])

    def test_success(self):
        from mcp_server import handle_call_tool
        with patch("mcp_server.registry") as r:
            r.plugins = {"EXEC": object()}
            r.is_enabled.return_value = True
            r.execute_command.return_value = "ok"
            result = handle_call_tool("EXEC", {"a": "b"})
        self.assertFalse(result.get("isError", False))
        r.execute_command.assert_called_once_with({"action": "EXEC", "a": "b"})

    def test_exception(self):
        from mcp_server import handle_call_tool
        with patch("mcp_server.registry") as r:
            r.plugins = {"EX": object()}
            r.is_enabled.return_value = True
            r.execute_command.side_effect = RuntimeError("boom")
            result = handle_call_tool("EX", {})
        self.assertTrue(result["isError"])

class TestHandleJsonRPC(unittest.TestCase):
    def test_parse_error(self):
        from mcp_server import handle_jsonrpc
        r = json.loads(handle_jsonrpc("not json"))
        self.assertEqual(r["error"]["code"], -32700)

    def test_initialize(self):
        from mcp_server import handle_jsonrpc
        r = json.loads(handle_jsonrpc('{"jsonrpc":"2.0","method":"initialize","id":1}'))
        self.assertIn("protocolVersion", r["result"])

    def test_notification_returns_none(self):
        from mcp_server import handle_jsonrpc
        self.assertIsNone(handle_jsonrpc('{"jsonrpc":"2.0","method":"notifications/initialized"}'))

    def test_unknown_method(self):
        from mcp_server import handle_jsonrpc
        r = json.loads(handle_jsonrpc('{"jsonrpc":"2.0","method":"bad","id":1}'))
        self.assertEqual(r["error"]["code"], -32601)

    def test_tools_list(self):
        from mcp_server import handle_jsonrpc
        with patch("mcp_server.handle_list_tools", return_value={"tools": []}):
            r = json.loads(handle_jsonrpc('{"jsonrpc":"2.0","method":"tools/list","id":2}'))
        self.assertEqual(r["result"], {"tools": []})

    def test_tools_call(self):
        from mcp_server import handle_jsonrpc
        with patch("mcp_server.handle_call_tool", return_value={"content": []}):
            r = json.loads(handle_jsonrpc('{"jsonrpc":"2.0","method":"tools/call","params":{"name":"T"},"id":3}'))
        self.assertEqual(r["id"], 3)

    def test_method_exception(self):
        from mcp_server import handle_jsonrpc
        with patch("mcp_server.handle_list_tools", side_effect=ValueError):
            r = json.loads(handle_jsonrpc('{"jsonrpc":"2.0","method":"tools/list","id":4}'))
        self.assertEqual(r["error"]["code"], -32603)

class TestHTTPServerLifecycle(unittest.TestCase):
    def test_start_stop(self):
        from mcp_server import start_http_server, stop_http_server
        stop_http_server()
        s, t = start_http_server(host="127.0.0.1", port=9299)
        self.assertIsNotNone(s)
        self.assertTrue(t.is_alive())
        stop_http_server(timeout=3)
        self.assertFalse(t.is_alive())

    def test_start_twice(self):
        from mcp_server import start_http_server, stop_http_server
        stop_http_server()
        s1, t1 = start_http_server(host="127.0.0.1", port=9298)
        s2, t2 = start_http_server(host="127.0.0.1", port=9298)
        self.assertIs(s1, s2)
        stop_http_server()

    def test_stop_not_running(self):
        from mcp_server import stop_http_server
        stop_http_server()

class TestParseArgs(unittest.TestCase):
    """Unit tests for the _parse_args CLI argument parser."""

    def test_defaults(self):
        from mcp_server import _parse_args
        args = _parse_args([])
        self.assertFalse(args["http"])
        self.assertEqual(args["host"], "127.0.0.1")
        self.assertEqual(args["port"], 9100)

    def test_defaults_no_flags(self):
        from mcp_server import _parse_args
        args = _parse_args(["script.py"])
        self.assertFalse(args["http"])
        self.assertEqual(args["host"], "127.0.0.1")
        self.assertEqual(args["port"], 9100)

    def test_http_flag(self):
        from mcp_server import _parse_args
        args = _parse_args(["--http"])
        self.assertTrue(args["http"])

    def test_custom_port(self):
        from mcp_server import _parse_args
        args = _parse_args(["--http", "--port", "8080"])
        self.assertEqual(args["port"], 8080)
        self.assertTrue(args["http"])

    def test_custom_host(self):
        from mcp_server import _parse_args
        args = _parse_args(["--http", "--host", "0.0.0.0"])
        self.assertEqual(args["host"], "0.0.0.0")
        self.assertTrue(args["http"])

    def test_all_flags(self):
        from mcp_server import _parse_args
        args = _parse_args(["--http", "--host", "0.0.0.0", "--port", "9090"])
        self.assertTrue(args["http"])
        self.assertEqual(args["host"], "0.0.0.0")
        self.assertEqual(args["port"], 9090)

    def test_invalid_port_falls_back_to_default(self):
        from mcp_server import _parse_args
        args = _parse_args(["--http", "--port", "abc"])
        self.assertEqual(args["port"], 9100)

    def test_port_without_value(self):
        from mcp_server import _parse_args
        args = _parse_args(["--http", "--port"])
        self.assertEqual(args["port"], 9100)

    def test_host_without_value(self):
        from mcp_server import _parse_args
        args = _parse_args(["--http", "--host"])
        self.assertEqual(args["host"], "127.0.0.1")

    def test_host_and_port_order_independent(self):
        from mcp_server import _parse_args
        args = _parse_args(["--port", "7070", "--host", "192.168.1.1", "--http"])
        self.assertEqual(args["port"], 7070)
        self.assertEqual(args["host"], "192.168.1.1")
        self.assertTrue(args["http"])

class TestPrintUsage(unittest.TestCase):
    """Unit tests for the _print_usage / --help path."""

    def test_print_usage_output(self):
        """Verify _print_usage() prints the module docstring."""
        from mcp_server import _print_usage
        with patch("sys.exit") as mock_exit:
            with patch("sys.stdout") as mock_stdout:
                _print_usage()
        mock_exit.assert_called_once_with(0)
        written = "".join(
            call[0][0] for call in mock_stdout.write.call_args_list
        )
        self.assertIn("mcp_server.py", written)
        self.assertIn("--http", written)
        self.assertIn("--port", written)
        self.assertIn("--host", written)
        self.assertIn("--help", written)

if __name__ == "__main__":
    unittest.main()
