"""Tests for kokerpro_web.py — Flask web server routes and helpers."""

import json
import unittest
from unittest.mock import patch, MagicMock



class TestClassifyText(unittest.TestCase):
    """_classify_text() — JSONDecodeError and Exception paths.
    The success path is already tested in test_classify_intent.py.
    """

    def test_json_decode_error_returns_error_dict(self):
        """Plugin returns non-JSON string -> returns {"error": result}."""
        from kokerpro_web import _classify_text
        with patch("plugins.classify_intent.execute", return_value="Some error occurred"):
            result = _classify_text("hello")
        self.assertEqual(result, {"error": "Some error occurred"})

    def test_unexpected_exception_returns_error_dict(self):
        """Plugin raises unexpected exception -> returns {"error": str(e)}."""
        from kokerpro_web import _classify_text
        with patch("plugins.classify_intent.execute", side_effect=ImportError("No module")):
            result = _classify_text("hello")
        self.assertEqual(result, {"error": "No module"})


class TestHomeRoute(unittest.TestCase):
    """GET / — returns HTML."""

    def test_home_returns_html(self):
        """GET / returns 200 with text/html."""
        from kokerpro_web import app
        with app.test_client() as client:
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.content_type or "")
        self.assertIn("KOKERPRO SECURE LINK", resp.data.decode())


class TestClassifyRoute(unittest.TestCase):
    """POST /classify — text classification endpoint."""

    def test_classify_success(self):
        """Valid text -> returns 200 with intent result."""
        from kokerpro_web import app
        with patch("kokerpro_web._classify_text", return_value={
            "intent": "Greeting",
            "confidence": 0.95,
            "probabilities": {"Greeting": 0.95, "Other": 0.05},
        }):
            with app.test_client() as client:
                resp = client.post(
                    "/classify",
                    data=json.dumps({"text": "Hello!"}),
                    content_type="application/json",
                )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["intent"], "Greeting")
        self.assertAlmostEqual(data["confidence"], 0.95)

    def test_classify_missing_text(self):
        """No 'text' field -> returns 400 error."""
        from kokerpro_web import app
        with app.test_client() as client:
            resp = client.post(
                "/classify",
                data=json.dumps({}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_classify_empty_text(self):
        """Empty text string -> returns 400 error."""
        from kokerpro_web import app
        with app.test_client() as client:
            resp = client.post(
                "/classify",
                data=json.dumps({"text": ""}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_classify_non_string_text(self):
        """Non-string text -> returns 400 error."""
        from kokerpro_web import app
        with app.test_client() as client:
            resp = client.post(
                "/classify",
                data=json.dumps({"text": 123}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.data)
        self.assertIn("error", data)

    def test_classify_returns_error(self):
        """_classify_text returns error -> returns 500."""
        from kokerpro_web import app
        with patch("kokerpro_web._classify_text", return_value={"error": "Model not loaded"}):
            with app.test_client() as client:
                resp = client.post(
                    "/classify",
                    data=json.dumps({"text": "Hello"}),
                    content_type="application/json",
                )
        self.assertEqual(resp.status_code, 500)
        data = json.loads(resp.data)
        self.assertIn("error", data)


class TestChatRoute(unittest.TestCase):
    """POST /chat — conversation endpoint."""

    def setUp(self):
        self.mock_provider = MagicMock()
        self.mock_memories = "Some core memories"

    def test_chat_with_history(self):
        """Valid history -> returns reply with no sys note."""
        self.mock_provider.chat_completion.return_value = {
            "content": "Hello Jacques!"
        }
        from kokerpro_web import app
        patches = [
            patch("kokerpro_web.memory_vault.retrieve_all_memories",
                  return_value=self.mock_memories),
            patch("kokerpro_web.get_provider", return_value=self.mock_provider),
            patch("kokerpro_web.kokertech_bridge.extract_valid_actions",
                  return_value=[]),
        ]
        for p in patches:
            p.start()
        try:
            with app.test_client() as client:
                resp = client.post(
                    "/chat",
                    data=json.dumps({
                        "history": [{"role": "user", "content": "Hello"}]
                    }),
                    content_type="application/json",
                )
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["reply"], "Hello Jacques!")
        self.assertEqual(data["sys"], "")

    def test_chat_with_command(self):
        """Reply contains valid command -> sys note populated."""
        self.mock_provider.chat_completion.return_value = {
            "content": '{"action": "STORE_MEMORY", "content": "test"}'
        }
        from kokerpro_web import app
        patches = [
            patch("kokerpro_web.memory_vault.retrieve_all_memories",
                  return_value=self.mock_memories),
            patch("kokerpro_web.get_provider", return_value=self.mock_provider),
            patch("kokerpro_web.kokertech_bridge.extract_valid_actions",
                  return_value=[{"action": "STORE_MEMORY", "content": "test"}]),
            patch("kokerpro_web.kokertech_bridge.handle_ai_intent",
                  return_value="Memory stored"),
        ]
        for p in patches:
            p.start()
        try:
            with app.test_client() as client:
                resp = client.post(
                    "/chat",
                    data=json.dumps({
                        "history": [{"role": "user", "content": "Remember this"}]
                    }),
                    content_type="application/json",
                )
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertEqual(data["sys"], "Memory stored")

    def test_chat_provider_error(self):
        """Provider returns error -> reply contains error message."""
        self.mock_provider.chat_completion.return_value = {
            "error": "Context length exceeded"
        }
        from kokerpro_web import app
        patches = [
            patch("kokerpro_web.memory_vault.retrieve_all_memories",
                  return_value=self.mock_memories),
            patch("kokerpro_web.get_provider", return_value=self.mock_provider),
        ]
        for p in patches:
            p.start()
        try:
            with app.test_client() as client:
                resp = client.post(
                    "/chat",
                    data=json.dumps({"history": []}),
                    content_type="application/json",
                )
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("Link Error", data["reply"])
        self.assertIn("Context length exceeded", data["reply"])

    def test_chat_exception_handled(self):
        """Any exception -> reply contains error message."""
        from kokerpro_web import app
        patches = [
            patch("kokerpro_web.memory_vault.retrieve_all_memories",
                  return_value=self.mock_memories),
            patch("kokerpro_web.get_provider",
                  side_effect=RuntimeError("AI crash")),
        ]
        for p in patches:
            p.start()
        try:
            with app.test_client() as client:
                resp = client.post(
                    "/chat",
                    data=json.dumps({"history": []}),
                    content_type="application/json",
                )
        finally:
            for p in patches:
                p.stop()

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.data)
        self.assertIn("Link Error", data["reply"])
        self.assertIn("AI crash", data["reply"])


class TestRegisterWebhook(unittest.TestCase):
    """register_webhook_endpoints() — webhook registration wrapper."""

    def test_register_success(self):
        """Webhooks register successfully."""
        from kokerpro_web import register_webhook_endpoints
        mock_app = MagicMock()
        with patch("plugins.webhook.register_all", return_value=3) as mock_reg:
            register_webhook_endpoints(mock_app)
        mock_reg.assert_called_once_with(mock_app)

    def test_register_exception(self):
        """Webhook registration fails -> exception caught."""
        from kokerpro_web import register_webhook_endpoints
        mock_app = MagicMock()
        with patch("plugins.webhook.register_all",
                   side_effect=ImportError("No webhook plugin")):
            register_webhook_endpoints(mock_app)  # Should not raise
