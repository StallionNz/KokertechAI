"""Wiring tests for the Sprint 5.2 intent router inside KokertechController.

``sub_agents.router`` / ``_detect_intent`` are unit-tested in
tests/test_sub_agents.py (TestRouteDispatch). These tests cover the *seam*:
that the main chat path dispatches through the router, and that routing can
never damage the chat path.

Anti-fragility invariants pinned here:
  1. Mock mode never reaches a provider. Sub-agents resolve a provider via
     ``ai_base.get_provider()``, which the orchestrators cannot patch, so the
     router is skipped outright when ``CONFIG["mock_mode"]`` is set.
  2. A dead/erroring router degrades to the raw user text, never to an error
     string and never to an exception out of ``process_input``.
  3. Routing never rewrites the user's turn. The routed text is injected as
     context; history/vault keep the user's own words.
  4. Only the main chat path routes. ``process_input_multi`` and
     ``process_input_with_personas`` compare personas/models on the *same*
     prompt, so the router must stay out of them — it would shift their
     provider-call sequence and differ the prompt under comparison.
"""
import unittest
from unittest.mock import MagicMock, patch

from kokertechController import KokertechController

# Built with chr(10) so the literal block never depends on escape handling.
_NL = chr(10)
DISPATCH_BLOCK = (_NL + _NL + "[INTENT ROUTER DISPATCH]:" + _NL + "ROUTED"
                  + _NL + "[END ROUTER DISPATCH]")


def _make_controller():
    """Return a KokertechController with history reset (provider mocked)."""
    with patch("kokertechController.get_provider"):
        ctrl = KokertechController()
    ctrl.history = []
    ctrl._history_svc.history = []
    ctrl.compressed_context_summary = ""
    ctrl._history_svc.compressed_context_summary = ""
    return ctrl


class TestRouteIntentHelper(unittest.TestCase):
    """``_route_intent`` — dispatch plus every fallback back to the raw text."""

    def test_mock_mode_skips_router_and_never_resolves_a_provider(self):
        """Mock mode is provider-isolated: no router call, no provider lookup."""
        ctrl = _make_controller()
        with patch.dict("config.CONFIG", {"mock_mode": True}, clear=False):
            with patch("kokertechController.router") as mock_router, \
                 patch("sub_agents.get_provider") as mock_provider:
                self.assertEqual(ctrl._route_intent("fix the bug"), "fix the bug")
        mock_router.assert_not_called()
        mock_provider.assert_not_called()

    def test_routed_text_is_returned(self):
        ctrl = _make_controller()
        with patch.dict("config.CONFIG",
                        {"mock_mode": False, "model_name": "m-A"}, clear=False):
            with patch("kokertechController.router",
                       return_value="ROUTED ANSWER") as mock_router:
                self.assertEqual(ctrl._route_intent("fix the bug"), "ROUTED ANSWER")
        mock_router.assert_called_once_with("fix the bug", model="m-A")

    def test_router_exception_falls_back_to_raw_text(self):
        ctrl = _make_controller()
        logs = []
        with patch.dict("config.CONFIG", {"mock_mode": False}, clear=False):
            with patch("kokertechController.router",
                       side_effect=RuntimeError("provider dead")):
                result = ctrl._route_intent("fix the bug", log=logs.append)
        self.assertEqual(result, "fix the bug")
        self.assertTrue(any("Intent router unavailable" in m for m in logs))

    def test_sub_agent_error_string_falls_back_to_raw_text(self):
        """SubAgent.execute returns 'Error: ...' strings — never forward those."""
        ctrl = _make_controller()
        for error_text in ("Error: HTTP 503", "Failed: no model loaded"):
            with patch.dict("config.CONFIG", {"mock_mode": False}, clear=False):
                with patch("kokertechController.router", return_value=error_text):
                    self.assertEqual(ctrl._route_intent("fix the bug"), "fix the bug")

    def test_empty_or_whitespace_router_output_falls_back(self):
        ctrl = _make_controller()
        for empty in ("", "   " + _NL + "  "):
            with patch.dict("config.CONFIG", {"mock_mode": False}, clear=False):
                with patch("kokertechController.router", return_value=empty):
                    self.assertEqual(ctrl._route_intent("fix the bug"), "fix the bug")

    def test_non_string_router_output_falls_back(self):
        ctrl = _make_controller()
        with patch.dict("config.CONFIG", {"mock_mode": False}, clear=False):
            with patch("kokertechController.router", return_value=None):
                self.assertEqual(ctrl._route_intent("fix the bug"), "fix the bug")


class TestRouterDispatchBlock(unittest.TestCase):
    """``_router_dispatch_block`` — an injected context block, never a rewrite."""

    def test_unrouted_text_produces_no_block(self):
        ctrl = _make_controller()
        with patch.object(ctrl, "_route_intent", return_value="hello there"):
            self.assertEqual(ctrl._router_dispatch_block("hello there"), "")

    def test_empty_route_produces_no_block(self):
        ctrl = _make_controller()
        with patch.object(ctrl, "_route_intent", return_value=""):
            self.assertEqual(ctrl._router_dispatch_block("hello there"), "")

    def test_routed_text_is_wrapped_in_a_dispatch_block(self):
        ctrl = _make_controller()
        with patch.object(ctrl, "_route_intent", return_value="ROUTED"):
            block = ctrl._router_dispatch_block("hello there")
        self.assertIn("[INTENT ROUTER DISPATCH]:", block)
        self.assertIn("ROUTED", block)
        self.assertIn("[END ROUTER DISPATCH]", block)


class TestOrchestratorRoutingWiring(unittest.TestCase):
    """All three chat orchestrators must dispatch through the router block."""

    def setUp(self):
        self.mocks = patch.multiple(
            "memory_vault",
            semantic_search=MagicMock(return_value=[]),
            store_memory=MagicMock(),
            get_recent_bias=MagicMock(return_value=[]),
            get_growth_arc=MagicMock(return_value=[]),
            ensure_tables_exist=MagicMock(),
            get_current_session_id=MagicMock(return_value="sid"),
            store_episodic=MagicMock(),
            record_persona_vote=MagicMock(return_value=1),
        )
        self.mocks.start()

    def tearDown(self):
        self.mocks.stop()

    def test_process_input_routes_user_text(self):
        ctrl = _make_controller()
        with patch.dict("config.CONFIG",
                        {"mock_mode": True, "freeform_mode": False}, clear=False):
            with patch.object(ctrl, "_router_dispatch_block",
                              return_value=DISPATCH_BLOCK) as mock_block:
                ctrl.process_input("hello there", log_callback=lambda x: None)
        mock_block.assert_called_once()
        self.assertEqual(mock_block.call_args[0][0], "hello there")

    def test_process_input_multi_never_routes(self):
        """REGRESSION GUARD: the multi-model path compares models on one prompt."""
        ctrl = _make_controller()
        provider = MagicMock()
        provider.chat_completion.return_value = {"content": "ok", "error": None}
        with patch.object(ctrl, "_router_dispatch_block",
                          return_value=DISPATCH_BLOCK) as mock_block, \
             patch("kokertechController.get_provider", return_value=provider), \
             patch.object(ctrl, "_load_target_model", return_value="m-A"):
            ctrl.process_input_multi(
                "hello there",
                model_specs=[{"provider": "local_llm", "model": "m-A", "label": "1"}],
                log_callback=lambda x: None,
            )
        mock_block.assert_not_called()

    def test_process_input_with_personas_never_routes(self):
        """REGRESSION GUARD: A/B compares personas on one prompt.

        Routing here shifted the alternating-provider fixture in
        tests/test_call_chain_e2e.py::test_persona_a_sentinel_persona_b_recovers_cleanly.
        """
        ctrl = _make_controller()
        with patch.object(ctrl, "_router_dispatch_block",
                          return_value=DISPATCH_BLOCK) as mock_block, \
             patch.object(ctrl, "_load_target_model", return_value="m-A"), \
             patch.object(ctrl, "_execute_with_fallback",
                          return_value=({"content": "ok", "error": None,
                                         "tool_calls": []}, "local_llm")):
            ctrl.process_input_with_personas(
                "hello there", "persona A", "persona B",
                log_callback=lambda x: None,
            )
        mock_block.assert_not_called()

    def test_process_input_keeps_the_raw_user_turn_in_history(self):
        """REGRESSION GUARD: routing must never rewrite the user's message.

        An earlier wiring assigned the routed text back onto ``text``, which
        replaced the user's words in history — caught by
        tests/test_call_chain_e2e.py::test_full_call_chain_structured_mode.
        """
        ctrl = _make_controller()
        with patch.dict("config.CONFIG",
                        {"mock_mode": True, "freeform_mode": False}, clear=False):
            with patch.object(ctrl, "_router_dispatch_block",
                              return_value=DISPATCH_BLOCK):
                ctrl.process_input("hello there", log_callback=lambda x: None)
        self.assertEqual(ctrl.history[0]["role"], "user")
        self.assertEqual(ctrl.history[0]["content"], "hello there")


if __name__ == "__main__":
    unittest.main()
