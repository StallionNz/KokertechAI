"""
test_crew_integration.py — Integration tests for CrewAI multi-agent orchestration.

Sprint 8: Covers CrewOrchestrator, _get_llm(), is_crewai_available(), run_crew_team(),
CrewAI path, SubAgent fallback path, and edge cases.
"""

import sys
import unittest
from unittest.mock import patch, MagicMock

# Capture real __import__ before any patches
_real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

class TestIsCrewaiAvailable(unittest.TestCase):
    """Cover both paths: crewai installed and not installed."""

    def test_available_when_both_deps_present(self):
        """Both crewai and langchain_openai importable -> True."""
        with patch.dict("sys.modules", {
            "crewai": MagicMock(),
            "langchain_openai": MagicMock(),
        }):
            import importlib
            import crew_integration
            importlib.reload(crew_integration)
            self.assertTrue(crew_integration.is_crewai_available())

    def test_unavailable_when_crewai_missing(self):
        """crewai not importable -> False."""
        def mock_import(name, *args, **kwargs):
            if name == "crewai":
                raise ImportError("No module named crewai")
            return _real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            import importlib
            import crew_integration
            importlib.reload(crew_integration)
            self.assertFalse(crew_integration.is_crewai_available())

    def test_unavailable_when_langchain_missing(self):
        """langchain_openai not importable -> False."""
        def mock_import(name, *args, **kwargs):
            if name == "langchain_openai":
                raise ImportError("No module named langchain_openai")
            return _real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            import importlib
            import crew_integration
            importlib.reload(crew_integration)
            self.assertFalse(crew_integration.is_crewai_available())

class TestGetLlm(unittest.TestCase):
    """Cover all provider paths in _get_llm()."""

    def _make_langchain_openai(self):
        """Create a mock langchain_openai module with ChatOpenAI."""
        m = MagicMock()
        m.ChatOpenAI = MagicMock(return_value=MagicMock())
        return m

    def test_langchain_openai_import_error(self):
        """langchain_openai not installed -> None."""
        def mock_import(name, *args, **kwargs):
            if name == "langchain_openai":
                raise ImportError("No module named langchain_openai")
            return _real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            from crew_integration import _get_llm
            result = _get_llm()
            self.assertIsNone(result)

class TestCrewOrchestratorInit(unittest.TestCase):
    """CrewOrchestrator.__init__ — availability detection."""

    def test_init_marks_available(self):
        """When crewai is available, _available is True."""
        with patch("crew_integration.is_crewai_available", return_value=True):
            from crew_integration import CrewOrchestrator
            orchestrator = CrewOrchestrator()
            self.assertTrue(orchestrator._available)

    def test_init_marks_unavailable(self):
        """When crewai is not available, _available is False."""
        with patch("crew_integration.is_crewai_available", return_value=False):
            from crew_integration import CrewOrchestrator
            orchestrator = CrewOrchestrator()
            self.assertFalse(orchestrator._available)

class TestCrewOrchestratorRunTeam(unittest.TestCase):
    """CrewOrchestrator.run_team — dispatches to CrewAI or fallback."""

    def test_run_team_crewai_path_when_available(self):
        """When available, run_team calls _run_crewai."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        orchestrator._available = True
        with patch.object(orchestrator, "_run_crewai", return_value="crewai result") as mock_crewai:
            with patch.object(orchestrator, "_run_fallback") as mock_fallback:
                result = orchestrator.run_team("test task")
                mock_crewai.assert_called_once()
                mock_fallback.assert_not_called()
                self.assertEqual(result, "crewai result")

    def test_run_team_fallback_path_when_unavailable(self):
        """When unavailable, run_team calls _run_fallback."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        orchestrator._available = False
        with patch.object(orchestrator, "_run_fallback", return_value="fallback result") as mock_fallback:
            with patch.object(orchestrator, "_run_crewai") as mock_crewai:
                result = orchestrator.run_team("test task")
                mock_fallback.assert_called_once()
                mock_crewai.assert_not_called()
                self.assertEqual(result, "fallback result")

    def test_run_team_custom_personas(self):
        """Custom persona list is passed through."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        orchestrator._available = True
        with patch.object(orchestrator, "_run_crewai", return_value="ok") as mock_crewai:
            orchestrator.run_team("task", personas=["Researcher", "Coder"])
            mock_crewai.assert_called_once_with("task", ["Researcher", "Coder"], "sequential")

    def test_run_team_hierarchical_process(self):
        """Hierarchical process flag is passed through."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        orchestrator._available = True
        with patch.object(orchestrator, "_run_crewai", return_value="ok") as mock_crewai:
            orchestrator.run_team("task", process="hierarchical")
            mock_crewai.assert_called_once_with(
                "task", ["Researcher", "Coder", "Auditor", "Planner"], "hierarchical"
            )

class TestCrewOrchestratorEdgeCases(unittest.TestCase):
    """Edge cases for CrewOrchestrator end-to-end."""

    def test_run_team_no_personas_provided_uses_default(self):
        """Default personas are used when not specified."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        orchestrator._available = False
        with patch.object(orchestrator, "_run_fallback", return_value="ok") as mock_fallback:
            orchestrator.run_team("task")
            mock_fallback.assert_called_once_with(
                "task", ["Researcher", "Coder", "Auditor", "Planner"]
            )

class TestRunCrewai(unittest.TestCase):
    """CrewOrchestrator._run_crewai — full CrewAI orchestration mock."""

    def setUp(self):
        self.crewai_patcher = patch.dict("sys.modules", {"crewai": MagicMock()})
        self.crewai_patcher.start()
        self.addCleanup(self.crewai_patcher.stop)

    def _setup_basic_mocks(self):
        """Set up basic CrewAI mocks with Agent, Task, Crew, Process."""
        import crewai
        # Agent returns an object with a role property
        crewai.Agent = MagicMock(side_effect=lambda role=None, **kw: MagicMock(role=role))
        crewai.Task = MagicMock(return_value=MagicMock())
        crewai.Crew = MagicMock(return_value=MagicMock())
        crewai.Process = MagicMock()
        crewai.Process.sequential = "sequential"
        crewai.Process.hierarchical = "hierarchical"

    def test_successful_crewai_execution(self):
        """CrewAI agents, tasks, crew are created and kickoff is called."""
        self._setup_basic_mocks()
        from crew_integration import CrewOrchestrator
        import crewai

        orchestrator = CrewOrchestrator()
        orchestrator._available = True
        with patch("crew_integration._get_llm", return_value=MagicMock()):
            with patch("crew_integration.logger"):  # suppress logger output
                result = orchestrator._run_crewai("task", ["Researcher", "Coder"], "sequential")

        self.assertEqual(result, str(crewai.Crew.return_value.kickoff()))
        crewai.Crew.assert_called_once()
        _, kwargs = crewai.Crew.call_args
        self.assertEqual(len(kwargs["agents"]), 2)
        self.assertEqual(len(kwargs["tasks"]), 2)

    def test_crewai_with_hierarchical_process(self):
        """Hierarchical process uses Process.hierarchical."""
        self._setup_basic_mocks()
        from crew_integration import CrewOrchestrator
        import crewai

        orchestrator = CrewOrchestrator()
        orchestrator._available = True
        with patch("crew_integration._get_llm", return_value=MagicMock()):
            with patch("crew_integration.logger"):
                orchestrator._run_crewai("task", ["Researcher"], "hierarchical")

        _, kwargs = crewai.Crew.call_args
        self.assertEqual(kwargs["process"], "hierarchical")

    def test_crewai_empty_personas(self):
        """Empty persona list returns error message."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        orchestrator._available = True
        with patch("crew_integration._get_llm", return_value=MagicMock()):
            result = orchestrator._run_crewai("task", [], "sequential")
        self.assertIn("No valid personas", result)

    def test_crewai_llm_none_falls_back(self):
        """When _get_llm returns None, falls back to _run_fallback."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        orchestrator._available = True
        with patch("crew_integration._get_llm", return_value=None):
            with patch.object(orchestrator, "_run_fallback", return_value="fallback!") as mock_fallback:
                result = orchestrator._run_crewai("task", ["Researcher"], "sequential")
                self.assertEqual(result, "fallback!")
                mock_fallback.assert_called_once()

    def test_crewai_exception_falls_back(self):
        """When CrewAI raises an exception, falls back to _run_fallback."""
        self._setup_basic_mocks()
        from crew_integration import CrewOrchestrator
        import crewai

        # Make Agent raise on first call
        crewai.Agent = MagicMock(side_effect=RuntimeError("CrewAI is broken"))

        orchestrator = CrewOrchestrator()
        orchestrator._available = True
        with patch("crew_integration._get_llm", return_value=MagicMock()):
            with patch.object(orchestrator, "_run_fallback", return_value="fallback!") as mock_fallback:
                with patch("crew_integration.logger"):
                    result = orchestrator._run_crewai("task", ["Researcher"], "sequential")
                    self.assertEqual(result, "fallback!")
                    mock_fallback.assert_called_once()

    def test_crewai_unknown_persona_skipped(self):
        """Unknown persona name is skipped gracefully."""
        self._setup_basic_mocks()
        from crew_integration import CrewOrchestrator
        import crewai

        orchestrator = CrewOrchestrator()
        orchestrator._available = True
        with patch("crew_integration._get_llm", return_value=MagicMock()):
            with patch("crew_integration.logger"):
                result = orchestrator._run_crewai(
                    "task", ["NonExistent", "Coder"], "sequential"
                )

        _, kwargs = crewai.Crew.call_args
        self.assertEqual(len(kwargs["agents"]), 1)
        self.assertEqual(kwargs["agents"][0].role, "Coder")

class TestRunFallback(unittest.TestCase):
    """CrewOrchestrator._run_fallback — SubAgent sequential execution."""

    def test_executes_all_personas_in_order(self):
        """All personas execute and results are concatenated with markers."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        results = iter(["researcher output", "coder output"])

        with patch("sub_agents.SubAgent") as mock_subagent_cls:
            mock_agent = MagicMock()
            mock_agent.execute.side_effect = lambda task: next(results)
            mock_subagent_cls.return_value = mock_agent
            result = orchestrator._run_fallback("some task", ["Researcher", "Coder"])

        self.assertIn("Researcher", result)
        self.assertIn("researcher output", result)
        self.assertIn("Coder", result)
        self.assertIn("coder output", result)

    def test_unknown_persona_skipped(self):
        """Unknown persona is skipped without error."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        with patch("sub_agents.SubAgent") as mock_subagent_cls:
            mock_agent = MagicMock()
            mock_agent.execute.return_value = "result text"
            mock_subagent_cls.return_value = mock_agent
            orchestrator._run_fallback("task", ["Researcher", "MadeUp", "Coder"])
            self.assertEqual(mock_agent.execute.call_count, 2)

    def test_empty_personas_returns_message(self):
        """Empty persona list returns 'No agents executed'."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        result = orchestrator._run_fallback("task", [])
        self.assertEqual(result, "No agents executed")

    def test_all_unknown_personas_returns_message(self):
        """All personas unknown -> 'No agents executed'."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        result = orchestrator._run_fallback("task", ["MadeUp1", "MadeUp2"])
        self.assertEqual(result, "No agents executed")

class TestRunCrewTeam(unittest.TestCase):
    """run_crew_team() convenience function delegates to CrewOrchestrator."""

    def test_delegates_with_default_args(self):
        """run_crew_team calls orchestrator.run_team with defaults."""
        from crew_integration import run_crew_team
        with patch("crew_integration.CrewOrchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.run_team.return_value = "team result"
            mock_orch_cls.return_value = mock_orch
            result = run_crew_team("my task")
        self.assertEqual(result, "team result")
        mock_orch.run_team.assert_called_once_with("my task", None, "sequential")

    def test_delegates_with_custom_args(self):
        """run_crew_team passes custom personas and process."""
        from crew_integration import run_crew_team
        with patch("crew_integration.CrewOrchestrator") as mock_orch_cls:
            mock_orch = MagicMock()
            mock_orch.run_team.return_value = "custom result"
            mock_orch_cls.return_value = mock_orch
            result = run_crew_team("my task", personas=["Coder"], process="hierarchical")
        mock_orch.run_team.assert_called_once_with("my task", ["Coder"], "hierarchical")
        self.assertEqual(result, "custom result")

if __name__ == "__main__":
    unittest.main()
