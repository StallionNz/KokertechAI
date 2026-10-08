"""tests/test_kokerpro_fastapi.py — Unit and integration tests for Architecture V2 FastAPI web server."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from kokerpro_fastapi import create_app


class TestFastAPISystemEndpoints(unittest.TestCase):
    """Test health, status, and service discovery endpoints."""

    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_health_endpoint_healthy(self):
        """GET /api/v2/health returns 200 with system telemetry."""
        with patch("kokerpro_fastapi.get_provider", return_value=MagicMock()), \
             patch("kokerpro_fastapi.memory_vault.get_all_core_memories", return_value=[{"id": i} for i in range(12)]), \
             patch("kokerpro_fastapi.get_vram_usage", return_value=1024):
            resp = self.client.get("/api/v2/health")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["vram_usage_mb"], 1024)
        self.assertEqual(data["core_memories_count"], 12)
        self.assertTrue(data["memory_vault_healthy"])

    def test_health_endpoint_degraded(self):
        """GET /api/v2/health reports degraded when AI provider is unavailable."""
        with patch("kokerpro_fastapi.get_provider", return_value=None), \
             patch("kokerpro_fastapi.memory_vault.get_all_core_memories", return_value=[{"id": 1}]):
            resp = self.client.get("/api/v2/health")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "degraded")

    def test_status_endpoint(self):
        """GET /api/v2/status returns comprehensive Architecture V2 status."""
        resp = self.client.get("/api/v2/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["system"], "KokertechAI")
        self.assertEqual(data["architecture"], "V2")
        self.assertIn("registered_services", data)
        self.assertIsInstance(data["registered_services"], list)

    def test_services_endpoint(self):
        """GET /api/v2/services lists DI container services."""
        resp = self.client.get("/api/v2/services")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("services", data)
        self.assertIn("history_service", data["services"])
        self.assertIn("prompt_service", data["services"])


class TestFastAPIChatEndpoints(unittest.TestCase):
    """Test /api/v2/chat and /api/v2/chat/stream endpoints."""

    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_chat_success_with_thinking(self):
        """POST /api/v2/chat parses <thinking> and returns clean reply."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "<thinking>Analyzing user input</thinking><final_output>All systems green.</final_output>"
        }

        with patch("kokerpro_fastapi.get_provider", return_value=mock_provider), \
             patch("kokerpro_fastapi.memory_vault.retrieve_all_memories", return_value="Core context"):
            resp = self.client.post("/api/v2/chat", json={"text": "System status check"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["reply"], "All systems green.")
        self.assertEqual(data["thinking"], "Analyzing user input")
        self.assertGreaterEqual(data["duration_ms"], 0.0)

    def test_chat_no_provider_fallback(self):
        """POST /api/v2/chat returns graceful Link Error message if no provider."""
        with patch("kokerpro_fastapi.get_provider", return_value=None):
            resp = self.client.post("/api/v2/chat", json={"text": "Hello"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("Link Error", data["reply"])
        self.assertEqual(data["sys"], "Offline")

    def test_chat_empty_request_rejected(self):
        """POST /api/v2/chat with empty text and history returns 400 Bad Request."""
        resp = self.client.post("/api/v2/chat", json={})
        self.assertEqual(resp.status_code, 400)

    def test_chat_stream_endpoint(self):
        """POST /api/v2/chat/stream returns text/event-stream chunks."""
        mock_provider = MagicMock()
        mock_provider.chat_completion_stream.return_value = [
            {"content": "Chunk 1 "},
            {"content": "Chunk 2"},
        ]

        with patch("kokerpro_fastapi.get_provider", return_value=mock_provider):
            resp = self.client.post("/api/v2/chat/stream", json={"text": "Stream test"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers.get("content-type", ""))
        self.assertIn("Chunk 1", resp.text)
        self.assertIn("[DONE]", resp.text)


class TestFastAPIClassificationAndSettings(unittest.TestCase):
    """Test intent classification and configuration endpoints."""

    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_classify_success(self):
        """POST /api/v2/classify returns intent classification."""
        with patch("plugins.classify_intent.execute", return_value={
            "intent": "StatusCheck",
            "confidence": 0.98,
            "probabilities": {"StatusCheck": 0.98, "Chat": 0.02}
        }):
            resp = self.client.post("/api/v2/classify", json={"text": "Check status"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["intent"], "StatusCheck")
        self.assertEqual(data["confidence"], 0.98)

    def test_classify_empty_rejected(self):
        """POST /api/v2/classify rejects empty input with 400."""
        resp = self.client.post("/api/v2/classify", json={"text": "   "})
        self.assertEqual(resp.status_code, 400)

    def test_settings_get_and_post(self):
        """GET and POST /api/v2/settings operate only on whitelisted safe keys."""
        resp = self.client.get("/api/v2/settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("settings", data)
        self.assertIn("active_provider", data["settings"])

        with patch("kokerpro_fastapi.save_settings") as mock_save:
            update_resp = self.client.post("/api/v2/settings", json={
                "settings": {"active_theme": "dark_cyber", "malicious_injected_key": "bad"}
            })

        self.assertEqual(update_resp.status_code, 200)
        updated = update_resp.json()["settings"]
        self.assertIn("active_theme", updated)
        self.assertNotIn("malicious_injected_key", updated)
        mock_save.assert_called_once()


class TestFastAPIMemoryAndHTMX(unittest.TestCase):
    """Test memory vault retrieval and HTMX view rendering."""

    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_memory_recent_endpoint(self):
        """GET /api/v2/memory/recent returns memory list."""
        with patch("kokerpro_fastapi.memory_vault.get_recent_episodic", return_value=[{"id": 1, "text": "Test"}]):
            resp = self.client.get("/api/v2/memory/recent?limit=5")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)

    def test_home_page_returns_htmx_dashboard(self):
        """GET / returns HTML dashboard with HTMX and Alpine.js assets."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        self.assertIn("KOKERTECH", resp.text)
        self.assertIn("htmx.org", resp.text)
        self.assertIn("alpinejs", resp.text)

    def test_htmx_chat_fragment(self):
        """POST /htmx/chat returns rendered HTML fragment."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "<thinking>Thinking</thinking><final_output>HTML Response</final_output>"
        }

        with patch("kokerpro_fastapi.get_provider", return_value=mock_provider):
            resp = self.client.post("/htmx/chat", data={"prompt": "Test HTMX"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("HTML Response", resp.text)
        self.assertIn("Cognitive Trace", resp.text)

    def test_htmx_status_badge(self):
        """GET /htmx/status-badge returns live status badge HTML."""
        with patch("kokerpro_fastapi.get_provider", return_value=MagicMock()), \
             patch("kokerpro_fastapi.get_vram_usage", return_value=2048):
            resp = self.client.get("/htmx/status-badge")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("2048 MB VRAM", resp.text)


class TestFastAPIPluginEndpoints(unittest.TestCase):
    """Test /api/v2/plugins endpoints."""

    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_plugins_list_endpoint(self):
        """GET /api/v2/plugins returns discovered plugins list."""
        resp = self.client.get("/api/v2/plugins")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("count", data)
        self.assertIn("enabled_count", data)
        self.assertIn("plugins", data)
        self.assertGreaterEqual(data["count"], 1)

    def test_plugin_toggle_success(self):
        """POST /api/v2/plugins/{name}/toggle toggles enabled state."""
        # Toggle SYSTEM_STATUS twice so its state restores
        resp1 = self.client.post("/api/v2/plugins/SYSTEM_STATUS/toggle")
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.json()
        self.assertEqual(data1["command"], "SYSTEM_STATUS")
        self.assertIn("enabled", data1)

        resp2 = self.client.post("/api/v2/plugins/SYSTEM_STATUS/toggle")
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()
        self.assertNotEqual(data1["enabled"], data2["enabled"])

    def test_plugin_toggle_not_found(self):
        """POST /api/v2/plugins/{name}/toggle returns 404 for unknown plugin."""
        resp = self.client.post("/api/v2/plugins/NON_EXISTENT_PLUGIN_XYZ/toggle")
        self.assertEqual(resp.status_code, 404)

    def test_plugin_execute_success(self):
        """POST /api/v2/plugins/{name}/execute runs plugin with parameters."""
        with patch("kokerpro_fastapi.plugin_registry.execute", return_value="Result: OK"):
            resp = self.client.post("/api/v2/plugins/SYSTEM_STATUS/execute", json={"params": {}})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["command"], "SYSTEM_STATUS")
        self.assertTrue(data["success"])
        self.assertEqual(data["result"], "Result: OK")

    def test_plugin_execute_not_found(self):
        """POST /api/v2/plugins/{name}/execute returns 404 for unknown plugin."""
        resp = self.client.post("/api/v2/plugins/NON_EXISTENT_XYZ/execute", json={"params": {}})
        self.assertEqual(resp.status_code, 404)


class TestFastAPIGraphEndpoints(unittest.TestCase):
    """Test /api/v2/graph endpoints."""

    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_graph_stats_endpoint(self):
        """GET /api/v2/graph/stats returns neural graph summary."""
        with patch("kokerpro_fastapi.memory_vault.get_graph_stats", return_value={
            "entity_count": 5, "relationship_count": 4, "entity_types": {"concept": 5}, "relationship_types": {"RELATES_TO": 4}
        }), patch("kokerpro_fastapi.memory_vault.get_all_core_memories", return_value=[{"id": 1}]), \
           patch("kokerpro_fastapi.memory_vault.get_memory_links", return_value=[{"id": 1}]):
            resp = self.client.get("/api/v2/graph/stats")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["node_count"], 1)
        self.assertEqual(data["link_count"], 1)

    def test_graph_nodes_endpoint(self):
        """GET /api/v2/graph/nodes returns paginated memory nodes."""
        mock_nodes = [{"id": i, "content": f"Memory {i}", "node_type": "fact"} for i in range(10)]
        with patch("kokerpro_fastapi.memory_vault.get_all_core_memories", return_value=mock_nodes):
            resp = self.client.get("/api/v2/graph/nodes?limit=3&offset=2")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 10)
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["nodes"][0]["id"], 2)

    def test_graph_links_endpoint(self):
        """GET /api/v2/graph/links returns relationship edges."""
        mock_links = [{"id": 1, "source_id": 10, "target_id": 20, "weight": 1.0}]
        with patch("kokerpro_fastapi.memory_vault.get_memory_links", return_value=mock_links):
            resp = self.client.get("/api/v2/graph/links")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["links"][0]["source_id"], 10)

    def test_graph_triples_endpoint(self):
        """GET /api/v2/graph/triples returns RDF semantic triples."""
        mock_triples = [{"link_id": 1, "subject": "FastAPI", "predicate": "IS_A", "object": "Framework"}]
        with patch("kokerpro_fastapi.memory_vault.get_semantic_triples", return_value=mock_triples):
            resp = self.client.get("/api/v2/graph/triples")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["triples"][0]["predicate"], "IS_A")

    def test_graph_traverse_endpoint(self):
        """GET /api/v2/graph/traverse/{node_id} returns multi-hop subgraph."""
        mock_traverse = {
            "start_node_id": 1,
            "nodes": [{"id": 1, "hop": 0}],
            "links": [],
            "traversal_weights": {"1": 1.0},
            "paths": {"1": [1]},
        }
        with patch("kokerpro_fastapi.memory_vault.traverse_subgraph", return_value=mock_traverse):
            resp = self.client.get("/api/v2/graph/traverse/1?max_hops=2")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["start_node_id"], 1)
        self.assertEqual(len(data["nodes"]), 1)


class TestFastAPILogsAndHTMX(unittest.TestCase):
    """Test logs endpoints and additional HTMX partial views."""

    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_logs_recent_endpoint(self):
        """GET /api/v2/logs/recent returns log list."""
        mock_events = [{"timestamp": "2026-10-04T12:00:00", "category": "system", "message": "Boot"}]
        with patch("kokerpro_fastapi.auto_logger.get_session_events", return_value=mock_events):
            resp = self.client.get("/api/v2/logs/recent")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)

    def test_htmx_plugins_table(self):
        """GET /htmx/plugins returns table HTML fragment."""
        resp = self.client.get("/htmx/plugins")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("<table", resp.text)
        self.assertIn("Plugin", resp.text)

    def test_htmx_plugin_toggle_row(self):
        """POST /htmx/plugins/{name}/toggle returns single updated table row."""
        resp = self.client.post("/htmx/plugins/SYSTEM_STATUS/toggle")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("<tr", resp.text)
        self.assertIn("SYSTEM_STATUS", resp.text)
        # Restore state
        self.client.post("/htmx/plugins/SYSTEM_STATUS/toggle")

    def test_htmx_graph_summary(self):
        """GET /htmx/graph returns graph cards HTML fragment."""
        resp = self.client.get("/htmx/graph")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Core Nodes", resp.text)
        self.assertIn("Memory Links", resp.text)

    def test_htmx_logs_summary(self):
        """GET /htmx/logs returns log stream HTML fragment."""
        mock_events = [{"timestamp": "12:00", "category": "test", "message": "Log test event"}]
        with patch("kokerpro_fastapi.auto_logger.get_session_events", return_value=mock_events):
            resp = self.client.get("/htmx/logs")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("Live Session Logs", resp.text)
        self.assertIn("Log test event", resp.text)


if __name__ == "__main__":
    unittest.main()
