"""Unit tests for cognitive_auditor.py — mocked get_provider, parameter verification."""

import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock, ANY

# API key override handled by conftest.py pytest_configure() which sets
# KOKERTECH_API_KEY=test-key-override before collection begins.


class TestCognitiveAuditor(unittest.TestCase):
    """Verify audit_interaction calls get_provider with correct params."""

    def setUp(self):
        from ai_base import reset_providers
        reset_providers()

    @staticmethod
    def _make_fake_conn(exec_calls):
        """Build a FakeConn/FakeCursor pair that records SQL calls into exec_calls."""
        class FakeCursor:
            def execute(self, sql, params=None):
                exec_calls.append((sql, params))
                return self

        class FakeConn:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def cursor(self): return FakeCursor()
            def commit(self): pass

        return FakeConn()

    def _make_mock_provider(self, return_content):
        """Build a mock provider whose chat_completion returns the given content."""
        provider = MagicMock()
        provider.chat_completion.return_value = {
            "content": return_content,
            "model": "qwen2.5-0.5b-instruct",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            "error": None,
        }
        return provider

    # ---------- Parameter verification ----------

    @patch("cognitive_auditor.get_provider")
    @patch("cognitive_auditor.ensure_agency_tables")
    def test_calls_get_provider_with_active_provider(
        self, mock_tables, mock_get_provider
    ):
        """Should call get_provider with name from CONFIG["active_provider"]."""
        from config import CONFIG
        mock_provider = self._make_mock_provider(
            '{"biases": [], "growths": []}'
        )
        mock_get_provider.return_value = mock_provider
        from cognitive_auditor import audit_interaction
        audit_interaction(user_text="hello", ai_text="world")
        mock_get_provider.assert_called_once_with(
            name=CONFIG["active_provider"]
        )

    @patch("cognitive_auditor.get_provider")
    @patch("cognitive_auditor.ensure_agency_tables")
    def test_passes_correct_model(
        self, mock_tables, mock_get_provider
    ):
        """Should pass CONFIG["auditor_model_name"] as model."""
        from config import CONFIG
        mock_provider = self._make_mock_provider(
            '{"biases": [], "growths": []}'
        )
        mock_get_provider.return_value = mock_provider
        from cognitive_auditor import audit_interaction
        audit_interaction(user_text="hello", ai_text="world")
        mock_provider.chat_completion.assert_called_once_with(
            messages=ANY,
            model=CONFIG["auditor_model_name"],
            temperature=0.1,
            max_tokens=300,
            timeout=30,
        )

    @patch("cognitive_auditor.get_provider")
    @patch("cognitive_auditor.ensure_agency_tables")
    def test_passes_low_temperature(
        self, mock_tables, mock_get_provider
    ):
        """Auditor should use temperature=0.1 for deterministic output."""
        mock_provider = self._make_mock_provider(
            '{"biases": [], "growths": []}'
        )
        mock_get_provider.return_value = mock_provider
        from cognitive_auditor import audit_interaction
        audit_interaction(user_text="hello", ai_text="world")
        _, kwargs = mock_provider.chat_completion.call_args
        self.assertEqual(kwargs["temperature"], 0.1)

    @patch("cognitive_auditor.get_provider")
    @patch("cognitive_auditor.ensure_agency_tables")
    def test_passes_300_max_tokens(
        self, mock_tables, mock_get_provider
    ):
        mock_provider = self._make_mock_provider(
            '{"biases": [], "growths": []}'
        )
        mock_get_provider.return_value = mock_provider
        from cognitive_auditor import audit_interaction
        audit_interaction(user_text="hello", ai_text="world")
        _, kwargs = mock_provider.chat_completion.call_args
        self.assertEqual(kwargs["max_tokens"], 300)

    @patch("cognitive_auditor.get_provider")
    @patch("cognitive_auditor.ensure_agency_tables")
    def test_passes_30s_timeout(
        self, mock_tables, mock_get_provider
    ):
        mock_provider = self._make_mock_provider(
            '{"biases": [], "growths": []}'
        )
        mock_get_provider.return_value = mock_provider
        from cognitive_auditor import audit_interaction
        audit_interaction(user_text="hello", ai_text="world")
        _, kwargs = mock_provider.chat_completion.call_args
        self.assertEqual(kwargs["timeout"], 30)

    # ---------- Message construction ----------

    @patch("cognitive_auditor.get_provider")
    @patch("cognitive_auditor.ensure_agency_tables")
    def test_messages_include_user_and_ai_text(
        self, mock_tables, mock_get_provider
    ):
        """Messages should contain the user and AI texts for analysis."""
        mock_provider = self._make_mock_provider(
            '{"biases": [], "growths": []}'
        )
        mock_get_provider.return_value = mock_provider
        from cognitive_auditor import audit_interaction
        audit_interaction(user_text="What is AI?", ai_text="AI is...")
        messages = mock_provider.chat_completion.call_args[1]["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("What is AI?", messages[1]["content"])
        self.assertIn("AI is...", messages[1]["content"])

    # ---------- Error handling ----------

    @patch("cognitive_auditor.get_provider")
    @patch("cognitive_auditor.ensure_agency_tables")
    def test_logs_error_on_provider_failure(
        self, mock_tables, mock_get_provider
    ):
        """Should log error when provider returns error."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "", "model": "", "usage": {}, "error": "HTTP 503"
        }
        mock_get_provider.return_value = mock_provider
        logs = []
        from cognitive_auditor import audit_interaction
        audit_interaction(
            user_text="hello", ai_text="world",
            log_callback=lambda msg: logs.append(msg)
        )
        self.assertTrue(any("503" in log for log in logs))

    # ---------- Response parsing & DB ----------

    def test_inserts_bias_with_80pct_threshold(self):
        """Should insert biases with >=80% confidence, skip <80%."""
        from unittest.mock import patch
        from cognitive_auditor import audit_interaction

        content = json.dumps({
            "biases": [
                {"bias_type": "Confirmation Bias", "confidence_score": 85,
                 "description": "AI agreed with user"},
                {"bias_type": "Ambiguity Effect", "confidence_score": 60,
                 "description": "Too vague"},
            ],
            "growths": [],
        })

        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": content,
            "model": "test",
            "usage": {},
            "error": None,
        }

        logs = []
        exec_calls = []

        with patch("cognitive_auditor.get_provider", return_value=mock_provider):
            with patch("cognitive_auditor.sqlite3") as mock_sqlite3:
                mock_sqlite3.connect.return_value = self._make_fake_conn(exec_calls)
                with patch("cognitive_auditor.ensure_agency_tables"):
                    audit_interaction(
                        user_text="hello", ai_text="world",
                        log_callback=lambda m: logs.append(str(m))
                    )

        self.assertTrue(mock_provider.chat_completion.called, "provider was not called")

        insert_calls = [
            call for call in exec_calls
            if call[0].startswith("INSERT INTO bias_ledger")
        ]
        self.assertEqual(len(insert_calls), 1,
                         f"Expected 1 INSERT, got {len(insert_calls)}. "
                         f"Logs: {logs}\n"
                         f"All execute calls: {[c[0][:50] for c in exec_calls]}")
        _, params = insert_calls[0]
        self.assertIsNotNone(params)
        # Canonical schema (2026-09): (timestamp, agent_id, bias_type,
        # confidence_score, description) — matches memory_vault.log_bias.
        self.assertEqual(params[1], "cognitive_auditor")
        self.assertEqual(params[2], "Confirmation Bias")
        self.assertEqual(params[3], 85)

    def test_inserts_growth_events(self):
        """Should insert growth arc events from response."""
        from unittest.mock import patch
        from cognitive_auditor import audit_interaction

        mock_provider = self._make_mock_provider(json.dumps({
            "biases": [],
            "growths": [
                {"event_description": "User showed frustration", "energy_shift": -15.0},
                {"event_description": "User understood concept", "energy_shift": 20.5},
            ],
        }))

        exec_calls = []

        with patch("cognitive_auditor.get_provider", return_value=mock_provider):
            with patch("cognitive_auditor.sqlite3") as mock_sqlite3:
                mock_sqlite3.connect.return_value = self._make_fake_conn(exec_calls)
                with patch("cognitive_auditor.ensure_agency_tables"):
                    audit_interaction(user_text="hello", ai_text="world")

        growth_calls = [
            (sql, params) for sql, params in exec_calls
            if sql.startswith("INSERT INTO growth_arcs")
        ]
        self.assertEqual(len(growth_calls), 2)
        # Canonical schema (2026-09): (timestamp, agent_id,
        # event_description, energy_shift) — matches memory_vault.log_growth.
        self.assertEqual(growth_calls[0][1][1], "cognitive_auditor")
        self.assertIn("frustration", growth_calls[0][1][2])
        self.assertEqual(growth_calls[0][1][3], -15.0)

    def test_skips_non_json_response(self):
        """When response has no JSON, should not insert anything."""
        from unittest.mock import patch
        from cognitive_auditor import audit_interaction

        mock_provider = self._make_mock_provider(
            "The interaction seems normal with no biases detected."
        )
        logs = []
        exec_calls = []

        with patch("cognitive_auditor.get_provider", return_value=mock_provider):
            with patch("cognitive_auditor.sqlite3") as mock_sqlite3:
                mock_sqlite3.connect.return_value = self._make_fake_conn(exec_calls)
                with patch("cognitive_auditor.ensure_agency_tables"):
                    audit_interaction(
                        user_text="hello", ai_text="world",
                        log_callback=lambda msg: logs.append(msg)
                    )

        insert_calls = [(s, p) for s, p in exec_calls if s.startswith("INSERT")]
        self.assertEqual(len(insert_calls), 0)
        found = any("No actionable data" in log for log in logs)
        self.assertTrue(found, f"Expected 'No actionable data' in logs: {logs}")

    def test_empty_biases_and_growths_skips_inserts(self):
        """Empty bias/growth arrays should result in no INSERTs."""
        from unittest.mock import patch
        from cognitive_auditor import audit_interaction

        mock_provider = self._make_mock_provider(
            '{"biases": [], "growths": []}'
        )
        exec_calls = []

        with patch("cognitive_auditor.get_provider", return_value=mock_provider):
            with patch("cognitive_auditor.sqlite3") as mock_sqlite3:
                mock_sqlite3.connect.return_value = self._make_fake_conn(exec_calls)
                with patch("cognitive_auditor.ensure_agency_tables"):
                    audit_interaction(user_text="hello", ai_text="world")

        insert_calls = [(s, p) for s, p in exec_calls if s.startswith("INSERT")]
        self.assertEqual(len(insert_calls), 0)

if __name__ == "__main__":
    unittest.main()
