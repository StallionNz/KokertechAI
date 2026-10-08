"""tests/test_structured_output_service.py -- dedicated coverage for
``services/structured_output_service.py`` (Sprint 12 consolidated facade).

Closes the remaining KNOWLEDGE.md §12 services coverage gap for the
structured-output layer by pinning ``resolve_response_format``'s three
branches directly:

- ``freeform=True``  -> ``None`` (no response-format constraint)
- ``structured_format="json"`` -> ``{"type": "json_object"}``
- default (``"xml"``) -> ``{"type": "grammar", "value": <xml grammar>}``

Also locks the re-export surface (``parse_ai`` / ``parse_tool_calls`` are
the ``PromptService`` staticmethods) so a future refactor that moves
parsing off ``PromptService`` breaks loudly here rather than silently
changing controller behavior.

ANTI-FRAGILITY: ``structured_format`` is read from the shared ``CONFIG``
dict at call time -- tests use ``patch.dict(config.CONFIG, ...)`` and the
autouse ``_reset_config_per_test`` fixture restores the snapshot after
each test, so no cross-test CONFIG pollution is possible.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_base import get_grammar
from config import CONFIG
from services.prompt_service import PromptService
from services.structured_output_service import (
    parse_ai,
    parse_tool_calls,
    resolve_response_format,
)


class TestResolveResponseFormat(unittest.TestCase):
    """Three-branch contract for resolve_response_format."""

    def test_freeform_returns_none(self):
        self.assertIsNone(resolve_response_format(freeform=True))

    def test_json_format_returns_json_object(self):
        with patch.dict(CONFIG, {"structured_format": "json"}, clear=False):
            result = resolve_response_format(freeform=False)
        self.assertEqual(result, {"type": "json_object"})

    def test_xml_default_returns_grammar(self):
        with patch.dict(CONFIG, {"structured_format": "xml"}, clear=False):
            result = resolve_response_format(freeform=False)
        self.assertIsInstance(result, dict)
        self.assertEqual(result["type"], "grammar")
        self.assertEqual(result["value"], get_grammar("xml"))
        self.assertIn("<thinking>", result["value"])

    def test_unknown_format_falls_back_to_grammar(self):
        """Any non-'json' format value falls back to the XML grammar branch."""
        with patch.dict(CONFIG, {"structured_format": "unknown"}, clear=False):
            result = resolve_response_format(freeform=False)
        self.assertEqual(result["type"], "grammar")


class TestReExportSurface(unittest.TestCase):
    """parse_ai / parse_tool_calls must remain the PromptService statics."""

    def test_parse_ai_is_prompt_service_staticmethod(self):
        self.assertIs(parse_ai, PromptService.parse_ai)

    def test_parse_tool_calls_is_prompt_service_staticmethod(self):
        self.assertIs(parse_tool_calls, PromptService.parse_tool_calls)


if __name__ == "__main__":
    unittest.main()
