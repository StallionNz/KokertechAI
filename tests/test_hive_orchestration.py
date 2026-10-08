"""tests/test_hive_orchestration.py — Comprehensive tests for Autonomous Speculative Multi-Agent Hive.

Realm 3: Autonomous Speculative Multi-Agent Hive.
Validates:
- HiveService trigger routing (<<HIVE:...>>, <<AGENT:...>>, /hive, config toggle).
- 5-agent execution workflow (Orchestrator -> Planner -> Coder -> Auditor -> Synthesizer).
- Mode 2 God Reviewer verification loop with revision and deadlock protection.
- Cooperative cancellation mid-stream.
- Speculative drafting and verification with graceful fallback.
- Controller integration with vault storage and history synchronization.
"""

from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock, patch

from config import CONFIG
from services.hive_service import HiveService
from services.speculative_service import SpeculativeDraftEngine


class TestHiveServiceRouting(unittest.TestCase):
    """REGRESSION GUARD for HiveService routing rules in services/hive_service.py."""

    def setUp(self) -> None:
        self.hive_svc = HiveService()

    def test_should_route_hive_tag(self) -> None:
        """REGRESSION GUARD for <<HIVE:...>> syntax routing."""
        is_hive, goal = self.hive_svc.should_route_to_hive("<<HIVE: build a robust sqlite cache>>")
        self.assertTrue(is_hive)
        self.assertEqual(goal, "build a robust sqlite cache")

    def test_should_route_agent_tag(self) -> None:
        """REGRESSION GUARD for <<AGENT:...>> syntax routing."""
        is_hive, goal = self.hive_svc.should_route_to_hive("<<AGENT: optimize neural embeddings>>")
        self.assertTrue(is_hive)
        self.assertEqual(goal, "optimize neural embeddings")

    def test_should_route_slash_hive(self) -> None:
        """REGRESSION GUARD for /hive slash command routing."""
        is_hive, goal = self.hive_svc.should_route_to_hive("/hive refactor data pipeline")
        self.assertTrue(is_hive)
        self.assertEqual(goal, "refactor data pipeline")

    def test_should_route_config_toggle_with_complex_intent(self) -> None:
        """REGRESSION GUARD for autonomous routing when hive_orchestration_enabled=True."""
        with patch.dict(CONFIG, {"hive_orchestration_enabled": True}):
            is_hive, goal = self.hive_svc.should_route_to_hive("please code and audit a new parser")
            self.assertTrue(is_hive)
            self.assertEqual(goal, "please code and audit a new parser")

    def test_should_not_route_normal_query(self) -> None:
        """REGRESSION GUARD ensuring standard queries bypass the hive when disabled."""
        with patch.dict(CONFIG, {"hive_orchestration_enabled": False}):
            is_hive, goal = self.hive_svc.should_route_to_hive("What is the current CPU temperature?")
            self.assertFalse(is_hive)
            self.assertEqual(goal, "")

    def test_should_not_route_invalid_inputs(self) -> None:
        """ANTI-FRAGILITY: Empty, None, or whitespace inputs must safely return False."""
        self.assertEqual(self.hive_svc.should_route_to_hive(""), (False, ""))
        self.assertEqual(self.hive_svc.should_route_to_hive("   "), (False, ""))
        self.assertEqual(self.hive_svc.should_route_to_hive(None), (False, ""))  # type: ignore[arg-type]


class TestHiveServiceExecution(unittest.TestCase):
    """Test suite for autonomous 5-agent execution and God Reviewer Mode 2 loop."""

    def setUp(self) -> None:
        self.hive_svc = HiveService()

    @patch("services.hive_service.SynthesizerAgent")
    @patch("services.hive_service.AuditorAgent")
    @patch("services.hive_service.CoderAgent")
    @patch("services.hive_service.PlannerAgent")
    @patch("services.hive_service.OrchestratorAgent")
    def test_hive_execution_success_flow(
        self,
        mock_orch_cls: MagicMock,
        mock_plan_cls: MagicMock,
        mock_code_cls: MagicMock,
        mock_audit_cls: MagicMock,
        mock_synth_cls: MagicMock,
    ) -> None:
        """REGRESSION GUARD for full 5-phase swarm execution flow."""
        mock_orch = mock_orch_cls.return_value
        mock_orch.execute.return_value = "Subtasks: 1. Parse data, 2. Output report"

        mock_plan = mock_plan_cls.return_value
        mock_plan.execute.return_value = "Plan: Step 1 write function, Step 2 verify"

        mock_coder = mock_code_cls.return_value
        mock_coder.execute.return_value = "def solve(): return 42"

        mock_auditor = mock_audit_cls.return_value
        mock_auditor.execute.return_value = "APPROVED: Implementation meets all invariants."

        mock_synth = mock_synth_cls.return_value
        mock_synth.execute.return_value = "Solution verified and ready."

        streamed_chunks: list[str] = []
        logged_events: list[str] = []

        result = self.hive_svc.execute_hive_flow(
            goal="Implement solver",
            log_callback=logged_events.append,
            stream_callback=streamed_chunks.append,
        )

        self.assertIn("Autonomous Hive Swarm Report", result["final"])
        self.assertIn("Solution verified and ready.", result["final"])
        self.assertIn("def solve(): return 42", result["final"])
        self.assertEqual(result["hive_meta"]["status"], "approved")
        self.assertEqual(result["hive_meta"]["retries"], 0)
        self.assertFalse(result.get("stopped", False))

        # Check logs and streaming
        self.assertTrue(any("[HIVE: ORCHESTRATOR]" in event for event in logged_events))
        self.assertTrue(any("[HIVE: SYNTHESIZER]" in event for event in logged_events))
        self.assertTrue(len(streamed_chunks) > 0)

    @patch("services.hive_service.SynthesizerAgent")
    @patch("services.hive_service.AuditorAgent")
    @patch("services.hive_service.CoderAgent")
    @patch("services.hive_service.PlannerAgent")
    @patch("services.hive_service.OrchestratorAgent")
    def test_hive_execution_mode2_revision_loop(
        self,
        mock_orch_cls: MagicMock,
        mock_plan_cls: MagicMock,
        mock_code_cls: MagicMock,
        mock_audit_cls: MagicMock,
        mock_synth_cls: MagicMock,
    ) -> None:
        """REGRESSION GUARD: Mode 2 God Reviewer rejects, triggering coder revision."""
        mock_orch = mock_orch_cls.return_value
        mock_orch.execute.return_value = "Subtasks breakdown"

        mock_plan = mock_plan_cls.return_value
        mock_plan.execute.return_value = "Execution plan"

        mock_coder = mock_code_cls.return_value
        mock_coder.execute.side_effect = [
            "def broken(): pass",  # First attempt
            "def fixed(): return True",  # Revision
        ]

        mock_auditor = mock_audit_cls.return_value
        mock_auditor.execute.side_effect = [
            "REJECT: Missing return value and type hints",  # Rejection
            "APPROVED: Invariants verified cleanly",  # Approval
        ]

        mock_synth = mock_synth_cls.return_value
        mock_synth.execute.return_value = "Fixed deliverable ready."

        logged_events: list[str] = []
        result = self.hive_svc.execute_hive_flow(
            goal="Refactor function",
            log_callback=logged_events.append,
        )

        self.assertEqual(result["hive_meta"]["status"], "approved")
        self.assertEqual(result["hive_meta"]["retries"], 1)
        self.assertEqual(mock_coder.execute.call_count, 2)
        self.assertEqual(mock_auditor.execute.call_count, 2)
        self.assertTrue(any("Changes required" in event for event in logged_events))

    @patch("services.hive_service.SynthesizerAgent")
    @patch("services.hive_service.AuditorAgent")
    @patch("services.hive_service.CoderAgent")
    @patch("services.hive_service.PlannerAgent")
    @patch("services.hive_service.OrchestratorAgent")
    def test_hive_execution_mode2_deadlock_guard(
        self,
        mock_orch_cls: MagicMock,
        mock_plan_cls: MagicMock,
        mock_code_cls: MagicMock,
        mock_audit_cls: MagicMock,
        mock_synth_cls: MagicMock,
    ) -> None:
        """REGRESSION GUARD: Auditor repeatedly rejects; breaks after max_retries without deadlock."""
        mock_orch = mock_orch_cls.return_value
        mock_orch.execute.return_value = "Subtasks breakdown"

        mock_plan = mock_plan_cls.return_value
        mock_plan.execute.return_value = "Execution plan"

        mock_coder = mock_code_cls.return_value
        mock_coder.execute.return_value = "def code(): pass"

        mock_auditor = mock_audit_cls.return_value
        # Always reject
        mock_auditor.execute.return_value = "REJECT: Critical flaws detected"

        mock_synth = mock_synth_cls.return_value
        mock_synth.execute.return_value = "Synthesis after deadlock."

        logged_events: list[str] = []
        result = self.hive_svc.execute_hive_flow(
            goal="Tough task",
            max_retries=2,
            log_callback=logged_events.append,
        )

        self.assertEqual(result["hive_meta"]["status"], "deadlock")
        self.assertEqual(result["hive_meta"]["retries"], 2)
        self.assertTrue(any("Deadlock limit reached" in event for event in logged_events))
        self.assertIn("Synthesis after deadlock.", result["final"])

    def test_hive_execution_cooperative_cancellation(self) -> None:
        """REGRESSION GUARD: cancel_event stops hive execution early."""
        cancel_event = threading.Event()
        cancel_event.set()

        result = self.hive_svc.execute_hive_flow(
            goal="Cancelled task",
            cancel_event=cancel_event,
        )

        self.assertTrue(result.get("stopped"))
        self.assertIn("stopped by user", result["final"])
        self.assertEqual(result["hive_meta"]["status"], "cancelled")

    @patch("services.hive_service.SynthesizerAgent")
    @patch("services.hive_service.AuditorAgent")
    @patch("services.hive_service.CoderAgent")
    @patch("services.hive_service.PlannerAgent")
    @patch("services.hive_service.OrchestratorAgent")
    def test_hive_execution_stage_badges_streamed(
        self,
        mock_orch_cls: MagicMock,
        mock_plan_cls: MagicMock,
        mock_code_cls: MagicMock,
        mock_audit_cls: MagicMock,
        mock_synth_cls: MagicMock,
    ) -> None:
        """REGRESSION GUARD: Verifies real-time stage badges are streamed into UI callback."""
        mock_orch = mock_orch_cls.return_value
        mock_orch.execute.return_value = "Directive"
        mock_plan = mock_plan_cls.return_value
        mock_plan.execute.return_value = "Plan"
        mock_code = mock_code_cls.return_value
        mock_code.execute.return_value = "def code(): pass"
        mock_audit = mock_audit_cls.return_value
        mock_audit.execute.return_value = "VERDICT: APPROVED"
        mock_synth = mock_synth_cls.return_value
        mock_synth.execute.return_value = "Summary"

        streamed_chunks: list[str] = []
        self.hive_svc.execute_hive_flow(
            goal="Add feature",
            stream_callback=streamed_chunks.append,
        )

        full_stream = "".join(streamed_chunks)
        self.assertIn("[🐝 Orchestrator]", full_stream)
        self.assertIn("[📐 Planner]", full_stream)
        self.assertIn("[💻 Coder]", full_stream)
        self.assertIn("[⚖️ Auditor]", full_stream)
        self.assertIn("[✨ Synthesizer]", full_stream)

    @patch("services.hive_service.SynthesizerAgent")
    @patch("services.hive_service.AuditorAgent")
    @patch("services.hive_service.CoderAgent")
    @patch("services.hive_service.PlannerAgent")
    @patch("services.hive_service.OrchestratorAgent")
    def test_hive_execution_multi_candidate_drafting(
        self,
        mock_orch_cls: MagicMock,
        mock_plan_cls: MagicMock,
        mock_code_cls: MagicMock,
        mock_audit_cls: MagicMock,
        mock_synth_cls: MagicMock,
    ) -> None:
        """REGRESSION GUARD: Multi-candidate speculative drafting generates candidates and selects winner."""
        mock_orch = mock_orch_cls.return_value
        mock_orch.execute.return_value = "Orchestrator directive"
        mock_plan = mock_plan_cls.return_value
        mock_plan.execute.return_value = "Architectural plan"
        mock_coder = mock_code_cls.return_value
        mock_coder.execute.side_effect = [
            "def candidate_a(): return 1",
            "def candidate_b(): return 2",
        ]
        mock_auditor = mock_audit_cls.return_value
        mock_auditor.execute.side_effect = [
            "def candidate_b(): return 2",
            "VERDICT: APPROVED - Clean implementation",
        ]
        mock_synth = mock_synth_cls.return_value
        mock_synth.execute.return_value = "Synthesis complete."

        streamed_chunks: list[str] = []
        result = self.hive_svc.execute_hive_flow(
            goal="Generate optimal sorting routine",
            speculative_candidates=2,
            stream_callback=streamed_chunks.append,
        )

        self.assertEqual(mock_coder.execute.call_count, 2)
        coder_calls = mock_coder.execute.call_args_list
        self.assertEqual(coder_calls[0].kwargs.get("temperature"), 0.1)
        self.assertEqual(coder_calls[1].kwargs.get("temperature"), 0.35)

        self.assertEqual(mock_auditor.execute.call_count, 2)
        self.assertTrue(result["hive_meta"]["speculative"])
        self.assertEqual(
            result["hive_meta"]["candidates"],
            ["def candidate_a(): return 1", "def candidate_b(): return 2"],
        )
        self.assertEqual(result["hive_meta"]["code"], "def candidate_b(): return 2")
        self.assertEqual(result["hive_meta"]["status"], "approved")

        full_stream = "".join(streamed_chunks)
        self.assertIn("Drafting 2 speculative candidate implementations", full_stream)
        self.assertIn("Auditor selecting superior candidate", full_stream)



class TestSpeculativeDraftEngine(unittest.TestCase):
    """Test suite for Speculative Drafting & Verification Engine."""

    def setUp(self) -> None:
        self.engine = SpeculativeDraftEngine()

    def test_draft_and_verify_disabled(self) -> None:
        """ANTI-FRAGILITY: When speculative drafting is disabled, calls verifier single-pass."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {"content": "standard verified code"}

        with patch("services.speculative_service.get_provider", return_value=mock_provider):
            with patch.dict(CONFIG, {"speculative_drafting_enabled": False}):
                res = self.engine.draft_and_verify(
                    prompt="write code",
                )
                self.assertEqual(res["final"], "standard verified code")
                self.assertFalse(res["verified"])
                mock_provider.chat_completion.assert_called_once()

    def test_draft_and_verify_enabled_success(self) -> None:
        """REGRESSION GUARD: Draft model creates fast candidate, verifier refines."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.side_effect = [
            {"content": "fast draft snippet"},
            {"content": "refined verified code"},
        ]

        with patch("services.speculative_service.get_provider", return_value=mock_provider):
            with patch.dict(
                CONFIG,
                {
                    "speculative_drafting_enabled": True,
                    "speculative_draft_model": "qwen2.5:0.5b",
                    "model_name": "llama-3.1-8b",
                },
            ):
                res = self.engine.draft_and_verify(
                    prompt="generate logic",
                )
                self.assertEqual(res["final"], "refined verified code")
                self.assertTrue(res["verified"])
                self.assertEqual(res["draft"], "fast draft snippet")
                self.assertEqual(mock_provider.chat_completion.call_count, 2)

    def test_draft_and_verify_failure_fallback(self) -> None:
        """REGRESSION GUARD: On draft error, falls back gracefully to direct verifier."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.side_effect = [
            RuntimeError("Model out of memory"),
            {"content": "fallback verified code"},
        ]

        with patch("services.speculative_service.get_provider", return_value=mock_provider):
            with patch.dict(
                CONFIG,
                {
                    "speculative_drafting_enabled": True,
                    "speculative_draft_model": "qwen2.5:0.5b",
                    "model_name": "llama-3.1-8b",
                },
            ):
                res = self.engine.draft_and_verify(
                    prompt="generate logic",
                )
                self.assertEqual(res["final"], "fallback verified code")
                self.assertFalse(res["verified"])
                self.assertEqual(mock_provider.chat_completion.call_count, 2)


class TestControllerHiveIntegration(unittest.TestCase):
    """Test suite for KokertechController routing into HiveService."""

    def test_controller_routes_hive_tag(self) -> None:
        """REGRESSION GUARD: Controller process_input detects <<HIVE:...>> and routes to HiveService."""
        with patch("kokertechController.get_provider"):
            from kokertechController import KokertechController
            ctrl = KokertechController()

        mock_hive = MagicMock()
        mock_hive.should_route_to_hive.return_value = (True, "build feature X")
        mock_hive.execute_hive_flow.return_value = {
            "final": "### Swarm Deliverable for feature X",
            "thinking": "Swarm thinking trace",
            "command": None,
            "ts": "12:00:00",
            "hive_meta": {"status": "approved"},
        }
        ctrl._hive_svc = mock_hive

        with patch("memory_vault.store_memory") as mock_store:
            res = ctrl.process_input("<<HIVE: build feature X>>")

        self.assertIn("Swarm Deliverable", res["final"])
        self.assertEqual(ctrl.history[-1]["content"], "### Swarm Deliverable for feature X")
        mock_store.assert_called_once()
        self.assertIn("Hive Deliverable", mock_store.call_args[0][0])

    def test_controller_bypasses_hive_for_standard_input(self) -> None:
        """ANTI-FRAGILITY: Standard input does not invoke execute_hive_flow."""
        with patch("kokertechController.get_provider"):
            from kokertechController import KokertechController
            ctrl = KokertechController()

        mock_hive = MagicMock()
        mock_hive.should_route_to_hive.return_value = (False, "")
        ctrl._hive_svc = mock_hive

        with patch.object(ctrl, "_execute_with_fallback", return_value=({"content": "standard AI", "error": None}, "local_llm")):
            with patch("memory_vault.store_memory"):
                res = ctrl.process_input("Hello, how are you?")

        mock_hive.execute_hive_flow.assert_not_called()
        self.assertEqual(res["final"], "standard AI")


if __name__ == "__main__":
    unittest.main()
