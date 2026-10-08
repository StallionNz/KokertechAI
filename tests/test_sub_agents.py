"""Unit tests for sub_agents.py — SubAgent, ResearcherAgent, CoderAgent, AuditorAgent, PlannerAgent,
and run_agent_team."""
import unittest
from unittest.mock import MagicMock, patch


class TestSubAgent(unittest.TestCase):

    def test_invalid_persona_raises(self):
        from sub_agents import SubAgent
        with self.assertRaises(ValueError):
            SubAgent("NonExistent")

    def test_execute_success(self):
        from sub_agents import SubAgent
        agent = SubAgent("Researcher")
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "Found some info", "error": None
        }
        with patch("sub_agents.get_provider", return_value=mock_provider):
            result = agent.execute("search for X")
        self.assertEqual(result, "Found some info")
        mock_provider.chat_completion.assert_called_once()
        msgs = mock_provider.chat_completion.call_args[1]["messages"]
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[1]["content"], "search for X")

    def test_execute_temperature_override(self):
        from sub_agents import SubAgent
        agent = SubAgent("Coder")
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "Custom temp code", "error": None
        }
        with patch("sub_agents.get_provider", return_value=mock_provider):
            result = agent.execute("write code", temperature=0.75)
        self.assertEqual(result, "Custom temp code")
        self.assertEqual(mock_provider.chat_completion.call_args[1]["temperature"], 0.75)

    def test_execute_provider_error(self):
        from sub_agents import SubAgent
        agent = SubAgent("Coder")
        with patch("sub_agents.get_provider") as mock_get:
            mock_get.return_value.chat_completion.return_value = {
                "error": "HTTP 503", "content": ""
            }
            result = agent.execute("write code")
        self.assertIn("Error", result)
        self.assertIn("503", result)

    def test_execute_exception(self):
        from sub_agents import SubAgent
        agent = SubAgent("Auditor")
        with patch("sub_agents.get_provider", side_effect=ConnectionError("refused")):
            result = agent.execute("audit this")
        self.assertIn("Failed", result)
        self.assertIn("refused", result)

    def test_execute_async_calls_back(self):
        from sub_agents import SubAgent
        agent = SubAgent("Planner")
        callback = MagicMock()
        with patch.object(agent, "execute", return_value="plan result"):
            t = agent.execute_async("plan", callback=callback)
            t.join(timeout=5)
        callback.assert_called_once_with("plan result")

    def test_execute_async_no_callback(self):
        from sub_agents import SubAgent
        agent = SubAgent("Researcher")
        with patch.object(agent, "execute", return_value="ok"):
            t = agent.execute_async("task")
            t.join(timeout=5)

    def test_researcher_default_persona(self):
        from sub_agents import ResearcherAgent
        r = ResearcherAgent()
        self.assertEqual(r.persona, "Researcher")

    def test_coder_default_persona(self):
        from sub_agents import CoderAgent
        c = CoderAgent()
        self.assertEqual(c.persona, "Coder")

    def test_auditor_default_persona(self):
        from sub_agents import AuditorAgent
        a = AuditorAgent()
        self.assertEqual(a.persona, "Auditor")

    def test_planner_default_persona(self):
        from sub_agents import PlannerAgent
        p = PlannerAgent()
        self.assertEqual(p.persona, "Planner")

    def test_orchestrator_default_persona(self):
        from sub_agents import OrchestratorAgent
        o = OrchestratorAgent()
        self.assertEqual(o.persona, "Orchestrator")
        self.assertEqual(o.temperature, 0.3)
        self.assertEqual(o.max_tokens, 2000)

    def test_synthesizer_default_persona(self):
        from sub_agents import SynthesizerAgent
        s = SynthesizerAgent()
        self.assertEqual(s.persona, "Synthesizer")
        self.assertEqual(s.temperature, 0.3)
        self.assertEqual(s.max_tokens, 2000)

    def test_tooluser_default_persona(self):
        from sub_agents import ToolUserAgent
        t = ToolUserAgent()
        self.assertEqual(t.persona, "ToolUser")
        self.assertEqual(t.temperature, 0.3)
        self.assertEqual(t.max_tokens, 2000)

    def test_per_persona_temperatures_and_tokens(self):
        """Verify per-persona temperature differentiation per Behavior Contract v4.2."""
        from sub_agents import SubAgent
        expected = {
            "Coder": (0.1, 3000),
            "Auditor": (0.4, 2500),
            "Planner": (0.2, 2000),
            "Researcher": (0.2, 2000),
            "Synthesizer": (0.3, 2000),
            "Orchestrator": (0.3, 2000),
            "ToolUser": (0.3, 2000),
        }
        for persona, (temp, max_tok) in expected.items():
            agent = SubAgent(persona)
            self.assertEqual(agent.temperature, temp, f"Wrong temp for {persona}")
            self.assertEqual(agent.max_tokens, max_tok, f"Wrong max_tokens for {persona}")


class TestMode2VerificationLoop(unittest.TestCase):
    """REGRESSION GUARD for Mode 2 God Reviewer verification loop in sub_agents.py
    (Behavior Contract v4.2).

    Locks down:
        1. User Goal -> Orchestrator -> Planner -> Coder -> Auditor flow.
        2. Context Scoping: Auditor receives only Original Goal and Coder raw output.
        3. Immediate approval returns approved status with retries=0.
        4. Rejection retry loop allows Coder to fix based on Auditor critique.
        5. Rejection twice triggers Mode2DeadlockError when max_retries=2.
    """

    def test_mode2_approved_on_first_try(self):
        from sub_agents import OrchestratorAgent, SubAgent
        orch = OrchestratorAgent()

        planner = SubAgent("Planner")
        coder = SubAgent("Coder")
        auditor = SubAgent("Auditor")

        with patch.object(planner, "execute", return_value="Step 1: Write helper"), \
             patch.object(coder, "execute", return_value="def helper(): pass"), \
             patch.object(auditor, "execute", return_value="APPROVED: Looks clean.") as mock_audit:

            result = orch.run_mode2(
                goal="Implement helper",
                planner=planner,
                coder=coder,
                auditor=auditor,
            )

            self.assertEqual(result["status"], "approved")
            self.assertEqual(result["retries"], 0)
            self.assertEqual(result["code"], "def helper(): pass")
            self.assertEqual(result["plan"], "Step 1: Write helper")
            self.assertIn("APPROVED", result["audit"])

            # Verify context scoping on Auditor:
            mock_audit.assert_called_once()
            auditor_prompt = mock_audit.call_args[0][0]
            self.assertIn("Implement helper", auditor_prompt)
            self.assertIn("def helper(): pass", auditor_prompt)
            self.assertNotIn("Step 1: Write helper", auditor_prompt)

    def test_mode2_approved_after_one_rejection(self):
        from sub_agents import OrchestratorAgent, SubAgent
        orch = OrchestratorAgent()

        planner = SubAgent("Planner")
        coder = SubAgent("Coder")
        auditor = SubAgent("Auditor")

        with patch.object(planner, "execute", return_value="Step 1: Build parser"), \
             patch.object(coder, "execute", side_effect=["bad_code()", "fixed_code()"]), \
             patch.object(auditor, "execute", side_effect=["REJECT: missing type hints", "APPROVED: perfect"]):

            result = orch.run_mode2(
                goal="Build parser",
                planner=planner,
                coder=coder,
                auditor=auditor,
                max_retries=2,
            )

            self.assertEqual(result["status"], "approved")
            self.assertEqual(result["retries"], 1)
            self.assertEqual(result["code"], "fixed_code()")

    def test_mode2_deadlock_on_max_retries(self):
        from sub_agents import OrchestratorAgent, SubAgent, Mode2DeadlockError
        orch = OrchestratorAgent()

        planner = SubAgent("Planner")
        coder = SubAgent("Coder")
        auditor = SubAgent("Auditor")

        with patch.object(planner, "execute", return_value="Plan"), \
             patch.object(coder, "execute", return_value="flawed_code()"), \
             patch.object(auditor, "execute", return_value="REJECT: security vulnerability"):

            with self.assertRaises(Mode2DeadlockError) as cm:
                orch.run_mode2(
                    goal="Fix security flaw",
                    planner=planner,
                    coder=coder,
                    auditor=auditor,
                    max_retries=2,
                )

            self.assertIn("max_retries=2", str(cm.exception))
            self.assertIn("Auditor rejected Coder output 2 times", str(cm.exception))

    def test_mode2_approved_with_negated_words_not_rejected(self):
        """Verify that positive approval phrases containing 'no changes required' or '0 failed'
        are NOT falsely classified as rejections.
        """
        from sub_agents import OrchestratorAgent, SubAgent
        orch = OrchestratorAgent()
        planner = SubAgent("Planner")
        coder = SubAgent("Coder")
        auditor = SubAgent("Auditor")

        with patch.object(planner, "execute", return_value="Plan"), \
             patch.object(coder, "execute", return_value="clean_code()"), \
             patch.object(auditor, "execute", return_value="APPROVED: All checks pass, no changes required, 0 failed."):

            result = orch.run_mode2(
                goal="Write clean code",
                planner=planner,
                coder=coder,
                auditor=auditor,
            )
            self.assertEqual(result["status"], "approved")
            self.assertEqual(result["retries"], 0)

    def test_mode2_custom_is_rejected_predicate(self):
        """Verify caller can pass a custom rejection evaluation predicate."""
        from sub_agents import OrchestratorAgent, SubAgent
        orch = OrchestratorAgent()
        planner = SubAgent("Planner")
        coder = SubAgent("Coder")
        auditor = SubAgent("Auditor")

        # Custom predicate checks for [REVISE]
        custom_checker = lambda text: "[REVISE]" in text

        with patch.object(planner, "execute", return_value="Plan"), \
             patch.object(coder, "execute", side_effect=["code_v1()", "code_v2()"]), \
             patch.object(auditor, "execute", side_effect=["[REVISE] needs more comments", "LGTM"]):

            result = orch.run_mode2(
                goal="Custom predicate task",
                planner=planner,
                coder=coder,
                auditor=auditor,
                is_rejected=custom_checker,
            )
            self.assertEqual(result["status"], "approved")
            self.assertEqual(result["retries"], 1)

    def test_mode2_invalid_inputs(self):
        """Verify ValueError is raised on empty goal or invalid max_retries."""
        from sub_agents import OrchestratorAgent
        orch = OrchestratorAgent()
        with self.assertRaises(ValueError):
            orch.run_mode2(goal="")
        with self.assertRaises(ValueError):
            orch.run_mode2(goal="   ")
        with self.assertRaises(ValueError):
            orch.run_mode2(goal="Valid", max_retries=0)

    def test_run_mode2_workflow_helper(self):
        """Verify top-level run_mode2_workflow helper delegates cleanly."""
        from sub_agents import run_mode2_workflow, SubAgent
        planner = SubAgent("Planner")
        coder = SubAgent("Coder")
        auditor = SubAgent("Auditor")

        with patch.object(planner, "execute", return_value="Plan"), \
             patch.object(coder, "execute", return_value="code()"), \
             patch.object(auditor, "execute", return_value="APPROVED: Looks great"):

            res = run_mode2_workflow(
                goal="Top-level workflow test",
                planner=planner,
                coder=coder,
                auditor=auditor,
            )
            self.assertEqual(res["status"], "approved")
            self.assertEqual(res["retries"], 0)


class TestRunAgentTeam(unittest.TestCase):

    def test_default_agents(self):
        from sub_agents import run_agent_team
        with patch("sub_agents.SubAgent.execute", return_value="ok"):
            results = run_agent_team("task")
        self.assertEqual(set(results.keys()), {"Researcher", "Coder", "Auditor", "Planner"})

    def test_parallel_execution(self):
        from sub_agents import run_agent_team, SubAgent
        agents = [SubAgent("Researcher"), SubAgent("Coder")]
        with patch("sub_agents.SubAgent.execute", return_value="done"):
            results = run_agent_team("task", agents=agents, parallel=True)
        self.assertEqual(set(results.keys()), {"Researcher", "Coder"})

    def test_sequential_execution(self):
        from sub_agents import run_agent_team, SubAgent
        agents = [SubAgent("Researcher"), SubAgent("Planner")]
        with patch("sub_agents.SubAgent.execute", return_value="seq"):
            results = run_agent_team("task", agents=agents, parallel=False)
        self.assertEqual(set(results.keys()), {"Researcher", "Planner"})


if __name__ == "__main__":
    unittest.main()

# =============================================================================
# Tests — _get_cached_tool_schemas + clear_tool_schema_cache
# =============================================================================

class TestCachedToolSchemas(unittest.TestCase):
    """Tool-schema TTL cache (sub_agents._get_cached_tool_schemas).

    The cache is module-level and shared across all SubAgent instances.
    These tests clear it in setUp/tearDown to ensure isolation.
    """

    def setUp(self):
        # Ensure the cache starts empty.
        from services.tool_use_agent import clear_tool_schema_cache
        clear_tool_schema_cache()

    def tearDown(self):
        from services.tool_use_agent import clear_tool_schema_cache
        clear_tool_schema_cache()

    def test_returns_schemas_from_registry(self):
        """First call queries the registry and returns its result."""
        from services.tool_use_agent import _get_cached_tool_schemas
        with patch("plugin_registry.registry.get_tools_for_llm",
                   return_value=[{"name": "tool1"}, {"name": "tool2"}]) as mock_get:
            result = _get_cached_tool_schemas()
        self.assertEqual(result, [{"name": "tool1"}, {"name": "tool2"}])
        mock_get.assert_called_once()

    def test_second_call_uses_cache(self):
        """Second call within TTL skips the registry query."""
        from services.tool_use_agent import _get_cached_tool_schemas
        with patch("plugin_registry.registry.get_tools_for_llm",
                   return_value=[{"name": "x"}]) as mock_get:
            _get_cached_tool_schemas()
            _get_cached_tool_schemas()
            _get_cached_tool_schemas()
        # Only the first call hit the registry.
        self.assertEqual(mock_get.call_count, 1)

    def test_cache_returns_same_object_reference(self):
        """Cached result is the exact list object stored at first call (no copy)."""
        from services.tool_use_agent import _get_cached_tool_schemas
        original = [{"name": "a"}]
        with patch("plugin_registry.registry.get_tools_for_llm", return_value=original):
            r1 = _get_cached_tool_schemas()
            r2 = _get_cached_tool_schemas()
        self.assertIs(r1, r2)
        self.assertIs(r1, original)

    def test_clear_tool_schema_cache_drops_entry(self):
        """After clear_tool_schema_cache, the next call re-queries the registry."""
        from services.tool_use_agent import _get_cached_tool_schemas, clear_tool_schema_cache
        with patch("plugin_registry.registry.get_tools_for_llm",
                   side_effect=[[{"name": "first"}], [{"name": "second"}]]) as mock_get:
            r1 = _get_cached_tool_schemas()
            clear_tool_schema_cache()
            r2 = _get_cached_tool_schemas()
        self.assertEqual(r1, [{"name": "first"}])
        self.assertEqual(r2, [{"name": "second"}])
        self.assertEqual(mock_get.call_count, 2)

    def test_ttl_expiry_re_queries_registry(self):
        """After the 30s TTL elapses, the registry is queried again."""
        from services.tool_use_agent import _get_cached_tool_schemas, _TOOL_SCHEMA_CACHE
        with patch("plugin_registry.registry.get_tools_for_llm",
                   side_effect=[[{"name": "first"}], [{"name": "second"}]]) as mock_get:
            _get_cached_tool_schemas()
            # Manually expire the cache entry
            _TOOL_SCHEMA_CACHE["ts"] = 0.0
            _get_cached_tool_schemas()
        self.assertEqual(mock_get.call_count, 2)

    def test_empty_registry_returns_empty_list(self):
        """If the registry returns an empty list, that's what we cache."""
        from services.tool_use_agent import _get_cached_tool_schemas
        with patch("plugin_registry.registry.get_tools_for_llm", return_value=[]):
            result = _get_cached_tool_schemas()
        self.assertEqual(result, [])

    def test_tool_use_agent_uses_cached_helper(self):
        """ToolUseAgent.execute() calls _get_cached_tool_schemas() (not registry direct).

        Verifies the integration: when ToolUseAgent.execute() runs, it routes
        through the cached helper, so the underlying registry is only hit once
        across multiple execute() calls within the TTL window.
        """
        from services.tool_use_agent import ToolUseAgent, _get_cached_tool_schemas

        # Pre-populate the cache by calling the helper once directly,
        # then spy on the registry to confirm no further hits during execute().
        _get_cached_tool_schemas()

        with patch("plugin_registry.registry.get_tools_for_llm",
                   return_value=[{"name": "test-tool"}]) as mock_get:
            # Patch services.tool_use_agent.get_provider (NOT sub_agents.get_provider):
            # ToolUseAgent is defined in services/tool_use_agent.py and calls
            # get_provider from its own module namespace. Patching the
            # sub_agents namespace would NOT affect services.tool_use_agent,
            # so the real LocalLLMProvider would be instantiated — loading
            # a real GGUF model and hanging in llama_cpp.llama_decode.
            with patch("services.tool_use_agent.get_provider") as mock_provider_factory:
                mock_provider = MagicMock()
                # A "DONE" result terminates the ToolUseAgent loop on the first call.
                mock_provider.chat_completion.return_value = {
                    "content": '{"action": "DONE", "result": "ok"}',
                    "error": None,
                }
                mock_provider_factory.return_value = mock_provider

                agent = ToolUseAgent()
                # Run execute() — it should read the cached schema, not the registry.
                agent.execute("test task")
        # Registry must not be re-queried during execute() (cache was pre-warmed).
        self.assertEqual(mock_get.call_count, 0)

    def test_clear_when_cache_empty_is_safe(self):
        """clear_tool_schema_cache() with empty cache is a no-op (no error)."""
        from services.tool_use_agent import clear_tool_schema_cache
        # Should not raise
        clear_tool_schema_cache()
        clear_tool_schema_cache()
        clear_tool_schema_cache()


# =============================================================================
# Tests — route dispatch (new token router in sub_agents.py)
# =============================================================================


class TestRouteDispatch(unittest.TestCase):

    def test_detect_intent_code(self):
        from sub_agents import _detect_intent
        for text in ["write a helper", "fix this bug", "implement the feature",
                     "refactor the module", "script a download"]:
            self.assertEqual(_detect_intent(text), "code",
                             f"expected 'code' for {text!r}")

    def test_detect_intent_research(self):
        from sub_agents import _detect_intent
        for text in ["research this topic", "search for answers",
                     "explain what is a class", "summarise the findings"]:
            self.assertEqual(_detect_intent(text), "research",
                             f"expected 'research' for {text!r}")

    def test_detect_intent_audit(self):
        from sub_agents import _detect_intent
        for text in ["security review this", "quality check the config",
                     "critique this design", "review the pull request"]:
            self.assertEqual(_detect_intent(text), "audit",
                             f"expected 'audit' for {text!r}")

    def test_detect_intent_plan(self):
        from sub_agents import _detect_intent
        for text in ["plan the architecture", "design a solution", "build a roadmap"]:
            self.assertEqual(_detect_intent(text), "plan",
                             f"expected 'plan' for {text!r}")

    def test_detect_intent_synthesize_fallback(self):
        from sub_agents import _detect_intent
        self.assertEqual(_detect_intent("hello there"), "synthesize")
        self.assertEqual(_detect_intent("how are you"), "synthesize")

    def test_router_code_path_uses_coder_persona(self):
        from sub_agents import router
        from unittest.mock import patch, MagicMock

        created_agents = []

        def _make_agent(persona=None, model=None, **kwargs):
            mock_inst = MagicMock()
            mock_inst.persona = persona
            mock_inst.model = model
            mock_inst.execute.side_effect = lambda task: f"[{persona}] {task}"
            created_agents.append(mock_inst)
            return mock_inst

        with patch("sub_agents.SubAgent", side_effect=_make_agent):
            result = router("write a helper")

        self.assertEqual(len(created_agents), 2)
        self.assertEqual(created_agents[0].persona, "Coder")
        self.assertEqual(created_agents[1].persona, "Synthesizer")
        self.assertIn("[Coder]", result)
        self.assertIn("helper", result)

    def test_router_routes_through_sub_agent_execute(self):
        from sub_agents import router
        from unittest.mock import patch, MagicMock

        created_agents = []

        def _make_agent(persona=None, model=None, **kwargs):
            mock_inst = MagicMock()
            mock_inst.persona = persona
            mock_inst.model = model
            mock_inst.execute.side_effect = lambda task: f"[{persona}] {task}"
            created_agents.append(mock_inst)
            return mock_inst

        with patch("sub_agents.SubAgent", side_effect=_make_agent):
            result = router("what is a class", model="test-model")

        self.assertEqual(len(created_agents), 2)
        self.assertEqual(created_agents[0].persona, "Researcher")
        self.assertEqual(created_agents[0].model, "test-model")
        self.assertEqual(created_agents[1].persona, "Synthesizer")
        self.assertIn("Researcher", result)

    def test_router_synthesize_path_uses_orchestrator(self):
        from sub_agents import router
        from unittest.mock import patch, MagicMock

        created_agents = []

        def _make_agent(persona=None, model=None, **kwargs):
            mock_inst = MagicMock()
            mock_inst.persona = persona
            mock_inst.model = model
            mock_inst.execute.side_effect = lambda task: f"[{persona}] {task}"
            created_agents.append(mock_inst)
            return mock_inst

        with patch("sub_agents.SubAgent", side_effect=_make_agent):
            result = router("how are you today")

        self.assertEqual(len(created_agents), 1)
        self.assertEqual(created_agents[0].persona, "Orchestrator")
        self.assertEqual(result, "[Orchestrator] how are you today")

    def test_detect_intent_case_insensitive(self):
        from sub_agents import _detect_intent
        for text in ["WRITE CODE", "Fix this bug", "RESEARCH this", "REVIEW it"]:
            self.assertIsNotNone(_detect_intent(text),
                                 f"expected a non-None intent for {text!r}")
