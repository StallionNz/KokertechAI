"""Unit tests for workflow_engine.py."""
import sys, threading, unittest
from unittest.mock import MagicMock, patch


class TestParseYaml(unittest.TestCase):
    def test_parse_valid(self):
        from workflow_engine import parse_yaml
        y = """
name: Test WF
description: A test
parallel: true
steps:
  - name: step1
    action: RESEARCH_TOPIC
    params: {query: "AI"}
  - name: step2
    action: sub_agent
    params: {persona: Coder, task: "write code"}
    depends_on: [step1]
"""
        wf = parse_yaml(y)
        self.assertEqual(wf.name, "Test WF")
        self.assertEqual(len(wf.steps), 2)
        self.assertTrue(wf.parallel)
        self.assertEqual(wf.steps[0].action, "RESEARCH_TOPIC")

    def test_empty_raises(self):
        from workflow_engine import parse_yaml
        with self.assertRaises(ValueError):
            parse_yaml("")

    def test_no_steps_raises(self):
        from workflow_engine import parse_yaml
        with self.assertRaises(ValueError):
            parse_yaml("name: orphan")

    def test_defaults(self):
        from workflow_engine import parse_yaml
        wf = parse_yaml("""name: M
steps:
  - name: only
    action: TEST
""")
        self.assertFalse(wf.parallel)
        self.assertEqual(wf.steps[0].on_failure, "stop")
        self.assertEqual(wf.steps[0].timeout, 120)

class TestExecuteStep(unittest.TestCase):
    def test_plugin_action(self):
        from workflow_engine import execute_step, WorkflowStep
        s = WorkflowStep(name="s1", action="LIST_FILES", params={"path": "/tmp"})
        with patch("plugin_registry.registry.execute_command", return_value="files"):
            r = execute_step(s)
        self.assertEqual(r.status, "success")
        self.assertEqual(r.output, "files")

    def test_sub_agent_action(self):
        from workflow_engine import execute_step, WorkflowStep
        s = WorkflowStep(name="s1", action="sub_agent", params={"persona": "Researcher", "task": "find"})
        with patch("sub_agents.SubAgent") as MockSubAgent:
            ma = MagicMock()
            ma.execute.return_value = "result"
            MockSubAgent.return_value = ma
            r = execute_step(s)
        self.assertEqual(r.status, "success")
        ma.execute.assert_called_once_with("find", timeout=120)

    def test_failure(self):
        from workflow_engine import execute_step, WorkflowStep
        s = WorkflowStep(name="f", action="CRASH")
        with patch("plugin_registry.registry.execute_command", side_effect=RuntimeError("boom")):
            r = execute_step(s)
        self.assertEqual(r.status, "failed")

class TestExecuteStepCodeReview(unittest.TestCase):
    """Sprint 17 R11: code_review step integration via code_review_workflow.

    Verifies the workflow_engine dispatches ``action="code_review"`` to
    the ``CodeIntelligenceFactory``-backed ``review_file`` (NOT direct
    ``CodeIntelligence(workspace=...)`` instantiation), and that the
    step output is a structured JSON payload that the agent loop can
    parse natively.

    Cross-file pollution guard: setUp + tearDown both call
    ``_reset_factory_for_tests`` so the workflow tests cannot leak
    registered workspace patterns into other test files (e.g.,
    ``tests/test_code_review_workflow.py``) -- matches the Sprint 17 R9
    conftest auto-wire for the global ``get_services().reset()``.
    """

    def setUp(self):
        from services.code_intelligence_factory import _reset_factory_for_tests
        _reset_factory_for_tests()
        self._tmpdirs = []

    def tearDown(self):
        from services.code_intelligence_factory import _reset_factory_for_tests
        import shutil
        for d in self._tmpdirs:
            # ignore_errors=True already swallows; no outer try/except needed
            shutil.rmtree(d, ignore_errors=True)
        _reset_factory_for_tests()

    def _make_tmpdir(self):
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp(prefix="wf_engine_cr_")
        self._tmpdirs.append(d)
        return Path(d)

    def test_code_review_step_success(self):
        """Verify code_review step dispatches to review_file + returns JSON.

        Locks Sprint 17 R11: the workflow engine MUST route through
        ``code_review_workflow.register_workspace()`` + ``review_file()``
        (NOT direct ``CodeIntelligence(workspace=...)`` instantiation).
        Verified end-to-end: create tmpdir + .py file, build
        ``WorkflowStep``, execute, assert step.status="success" +
        result.output is valid JSON with the expected dataclass fields.

        REGRESSION GUARD: If a future refactor bypasses the factory
        (e.g., direct ``CodeIntelligence(workspace=workspace_path)``
        instantiation), this test still passes (the output structure is
        unchanged), BUT the cache-singleton invariant from
        ``tests/test_code_review_workflow.py::test_workflow_cache_singleton_resolution``
        catches the regression -- the two tests together lock the
        factory integration end-to-end.
        """
        import json
        from workflow_engine import execute_step, WorkflowStep

        tmpdir = self._make_tmpdir()
        (tmpdir / "hello.py").write_text(
            "def greet():\n    return 'hi'\n", encoding="utf-8"
        )

        step = WorkflowStep(
            name="review1",
            action="code_review",
            params={"workspace_path": str(tmpdir), "filepath": "hello.py"},
        )
        result = execute_step(step)

        self.assertEqual(
            result.status, "success",
            f"unexpected failure: {result.error!r}",
        )
        # Output must be valid JSON (not str(dataclass) ugly format).
        # The JSON shape matches CodeReviewResult.asdict() exactly.
        parsed = json.loads(result.output)
        self.assertEqual(parsed["status"], "success")
        self.assertEqual(parsed["filepath"], "hello.py")
        self.assertEqual(
            parsed["file_content_length"],
            len("def greet():\n    return 'hi'\n"),
        )
        self.assertIn("functions", parsed["ast_summary"])
        self.assertEqual(len(parsed["ast_summary"]["functions"]), 1)

    def test_code_review_step_missing_params_raises(self):
        """Verify code_review step fails fast on missing required params.

        Locks Sprint 17 R11 error contract: a code_review step missing
        ``workspace_path`` OR ``filepath`` raises ``ValueError``
        immediately (BEFORE calling ``review_file`` or the factory),
        which the workflow engine's outer ``try/except`` catches and
        maps to ``step.status="failed"`` + ``result.error`` containing
        the parameter guidance.

        REGRESSION GUARD: If a future refactor moves the param check
        AFTER ``register_workspace()`` or ``review_file()`` (e.g., to
        surface better errors), this test fires because the step would
        succeed (or fail with a different error) instead of failing
        fast at the param-validation boundary.
        """
        from workflow_engine import execute_step, WorkflowStep

        # Missing filepath -- should fail fast.
        step = WorkflowStep(
            name="bad_step",
            action="code_review",
            params={"workspace_path": "/tmp/anything"},
        )
        result = execute_step(step)

        self.assertEqual(result.status, "failed")
        self.assertIn("workspace_path", result.error)
        self.assertIn("filepath", result.error)
        self.assertIn("requires", result.error)


class TestExecuteWorkflow(unittest.TestCase):
    def test_sequential_success(self):
        from workflow_engine import execute_workflow, WorkflowDefinition, WorkflowStep
        wf = WorkflowDefinition(name="Seq", steps=[
            WorkflowStep(name="a", action="A"),
            WorkflowStep(name="b", action="B", depends_on=["a"]),
        ])
        with patch("workflow_engine.execute_step") as me:
            me.side_effect = [
                MagicMock(status="success", step_name="a", output="a"),
                MagicMock(status="success", step_name="b", output="b"),
            ]
            r = execute_workflow(wf)
        self.assertEqual(r.overall, "success")
        self.assertEqual(len(r.steps), 2)

    def test_failure_stops(self):
        from workflow_engine import execute_workflow, WorkflowDefinition, WorkflowStep
        wf = WorkflowDefinition(name="F", steps=[
            WorkflowStep(name="a", action="A"),
            WorkflowStep(name="b", action="B"),
        ])
        with patch("workflow_engine.execute_step") as me:
            me.side_effect = [MagicMock(status="failed", step_name="a", error="err")]
            r = execute_workflow(wf)
        self.assertEqual(r.overall, "failed")
        self.assertEqual(r.steps[1].status, "skipped")

    def test_skip_on_failure_continues(self):
        from workflow_engine import execute_workflow, WorkflowDefinition, WorkflowStep
        wf = WorkflowDefinition(name="S", steps=[
            WorkflowStep(name="a", action="A", on_failure="skip"),
            WorkflowStep(name="b", action="B"),
        ])
        with patch("workflow_engine.execute_step") as me:
            def side(st):
                if st.name == "a":
                    return MagicMock(status="failed", step_name="a")
                return MagicMock(status="success", step_name="b")
            me.side_effect = side
            r = execute_workflow(wf)
        self.assertEqual(r.steps[1].status, "success")

    def test_unresolved_dep(self):
        from workflow_engine import execute_workflow, WorkflowDefinition, WorkflowStep
        wf = WorkflowDefinition(name="D", steps=[
            WorkflowStep(name="a", action="A", depends_on=["ghost"]),
        ])
        r = execute_workflow(wf)
        self.assertEqual(r.steps[0].status, "skipped")

    def test_stop_event(self):
        from workflow_engine import execute_workflow, WorkflowDefinition, WorkflowStep
        stop = threading.Event()
        stop.set()
        wf = WorkflowDefinition(name="S", steps=[WorkflowStep(name="a", action="A")])
        r = execute_workflow(wf, stop_event=stop)
        self.assertEqual(r.steps[0].status, "skipped")

    def test_progress_callback(self):
        from workflow_engine import execute_workflow, WorkflowDefinition, WorkflowStep
        wf = WorkflowDefinition(name="P", steps=[
            WorkflowStep(name="a", action="A"),
            WorkflowStep(name="b", action="B"),
        ])
        cb = MagicMock()
        with patch("workflow_engine.execute_step") as me:
            me.side_effect = [
                MagicMock(status="success", step_name="a"),
                MagicMock(status="success", step_name="b"),
            ]
            execute_workflow(wf, progress_callback=cb)
        self.assertEqual(cb.call_count, 2)

    def test_parallel(self):
        from workflow_engine import execute_workflow, WorkflowDefinition, WorkflowStep
        wf = WorkflowDefinition(name="P", parallel=True, steps=[
            WorkflowStep(name="a", action="A"),
            WorkflowStep(name="b", action="B"),
        ])
        with patch("workflow_engine.execute_step", return_value=MagicMock(status="success")):
            r = execute_workflow(wf)
        self.assertEqual(r.overall, "success")

class TestExecuteWorkflowAsync(unittest.TestCase):
    def test_returns_thread(self):
        from workflow_engine import execute_workflow_async, WorkflowDefinition, WorkflowStep
        wf = WorkflowDefinition(name="A", steps=[WorkflowStep(name="a", action="A")])
        with patch("workflow_engine.execute_workflow", return_value=MagicMock(overall="success")):
            t = execute_workflow_async(wf)
            self.assertIsInstance(t, threading.Thread)
            t.join(timeout=5)

    def test_done_callback(self):
        from workflow_engine import execute_workflow_async, WorkflowDefinition, WorkflowStep
        wf = WorkflowDefinition(name="A", steps=[WorkflowStep(name="a", action="A")])
        done = MagicMock()
        with patch("workflow_engine.execute_workflow", return_value=MagicMock(overall="success")):
            t = execute_workflow_async(wf, done_callback=done)
            t.join(timeout=5)
        done.assert_called_once()


class TestWorkflowEngine(unittest.TestCase):
    def test_list_workflows_nonexistent(self):
        from workflow_engine import WorkflowEngine
        engine = WorkflowEngine("/nonexistent_dir_12345")
        self.assertEqual(engine.list_workflows(), [])

    def test_list_workflows_with_files(self, tmp_path_factory=None):
        import tempfile, shutil, os
        from workflow_engine import WorkflowEngine
        tmpdir = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmpdir, "alpha.yaml"), "w") as f:
                f.write("name: a\nsteps:\n  - name: s\n    action: A\n")
            with open(os.path.join(tmpdir, "beta.yml"), "w") as f:
                f.write("name: b\nsteps:\n  - name: s\n    action: B\n")
            with open(os.path.join(tmpdir, "ignore.txt"), "w") as f:
                f.write("ignore")
            engine = WorkflowEngine(tmpdir)
            files = engine.list_workflows()
            self.assertEqual(files, ["alpha.yaml", "beta.yml"])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_execute_nonexistent(self):
        from workflow_engine import WorkflowEngine
        engine = WorkflowEngine()
        res = engine.execute("nonexistent_workflow_xyz.yaml")
        self.assertFalse(res["ok"])
        self.assertIn("not found", res["error"])

    def test_execute_success(self):
        import tempfile, shutil, os
        from workflow_engine import WorkflowEngine
        tmpdir = tempfile.mkdtemp()
        try:
            path = os.path.join(tmpdir, "test.yaml")
            with open(path, "w") as f:
                f.write("name: TestWF\nsteps:\n  - name: s1\n    action: TEST\n")
            engine = WorkflowEngine(tmpdir)
            with patch("workflow_engine.execute_step", return_value=MagicMock(status="success", step_name="s1", output="ok", error="")):
                res = engine.execute("test.yaml")
            self.assertTrue(res["ok"])
            self.assertEqual(res["overall"], "success")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestWorkflowRetriesAndConditionals(unittest.TestCase):
    """
    Tests for autonomous reflection loop: step retries and conditional execution.
    """
    def test_parse_yaml_with_retries_and_if_condition(self):
        from workflow_engine import parse_yaml
        yaml_text = """
name: Autonomous Loop
steps:
  - name: attempt_fix
    action: FIX_CODE
    max_retries: 3
    retry_delay: 0.1
  - name: rollback
    action: ROLLBACK
    if_condition: previous_failed
  - name: deploy
    action: DEPLOY
    if_condition: previous_success
"""
        wf = parse_yaml(yaml_text)
        self.assertEqual(len(wf.steps), 3)
        self.assertEqual(wf.steps[0].max_retries, 3)
        self.assertAlmostEqual(wf.steps[0].retry_delay, 0.1)
        self.assertEqual(wf.steps[1].if_condition, "previous_failed")
        self.assertEqual(wf.steps[2].if_condition, "previous_success")

    def test_step_retry_succeeds_on_second_attempt(self):
        from workflow_engine import execute_step, WorkflowStep, StepResult
        s = WorkflowStep(name="flaky", action="FLAKY", max_retries=2, retry_delay=0.01)
        call_count = 0
        def fake_run(step):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return StepResult(step_name=step.name, status="failed", error="transient error")
            return StepResult(step_name=step.name, status="success", output="fixed!")

        with patch("workflow_engine._run_single_step", side_effect=fake_run):
            res = execute_step(s)

        self.assertEqual(res.status, "success")
        self.assertEqual(res.output, "fixed!")
        self.assertEqual(call_count, 2)

    def test_step_retry_exhausted(self):
        from workflow_engine import execute_step, WorkflowStep, StepResult
        s = WorkflowStep(name="doomed", action="DOOMED", max_retries=2, retry_delay=0.01)
        call_count = 0
        def fake_run(step):
            nonlocal call_count
            call_count += 1
            return StepResult(step_name=step.name, status="failed", error="fatal error")

        with patch("workflow_engine._run_single_step", side_effect=fake_run):
            res = execute_step(s)

        self.assertEqual(res.status, "failed")
        self.assertEqual(res.error, "fatal error")
        self.assertEqual(call_count, 3)

    def test_workflow_conditional_branching_on_failure(self):
        from workflow_engine import execute_workflow, WorkflowDefinition, WorkflowStep
        wf = WorkflowDefinition(
            name="BranchWF",
            steps=[
                WorkflowStep(name="step1", action="A", on_failure="stop"),
                WorkflowStep(name="recovery", action="B", if_condition="previous_failed"),
                WorkflowStep(name="happy_path", action="C", if_condition="previous_success"),
            ],
        )
        def fake_exec(step):
            from workflow_engine import StepResult
            if step.name == "step1":
                return StepResult(step_name=step.name, status="failed", error="err")
            return StepResult(step_name=step.name, status="success", output="recovered")

        with patch("workflow_engine.execute_step", side_effect=fake_exec):
            res = execute_workflow(wf)

        self.assertEqual(res.steps[0].status, "failed")
        self.assertEqual(res.steps[1].status, "success")
        self.assertEqual(res.steps[1].output, "recovered")
        self.assertEqual(res.steps[2].status, "skipped")

    def test_workflow_conditional_branching_on_success(self):
        from workflow_engine import execute_workflow, WorkflowDefinition, WorkflowStep
        wf = WorkflowDefinition(
            name="BranchWF",
            steps=[
                WorkflowStep(name="step1", action="A"),
                WorkflowStep(name="recovery", action="B", if_condition="previous_failed"),
                WorkflowStep(name="happy_path", action="C", if_condition="previous_success"),
            ],
        )
        def fake_exec(step):
            from workflow_engine import StepResult
            return StepResult(step_name=step.name, status="success", output="ok")

        with patch("workflow_engine.execute_step", side_effect=fake_exec):
            res = execute_workflow(wf)

        self.assertEqual(res.steps[0].status, "success")
        self.assertEqual(res.steps[1].status, "skipped")
        self.assertEqual(res.steps[2].status, "success")

    def test_workflow_conditional_named_target(self):
        from workflow_engine import execute_workflow, WorkflowDefinition, WorkflowStep
        wf = WorkflowDefinition(
            name="TargetWF",
            steps=[
                WorkflowStep(name="compile", action="COMPILE"),
                WorkflowStep(name="lint", action="LINT"),
                WorkflowStep(name="fix_compile", action="FIX", if_condition="compile_failed"),
            ],
        )
        def fake_exec(step):
            from workflow_engine import StepResult
            if step.name == "compile":
                return StepResult(step_name=step.name, status="failed", error="syntax error")
            return StepResult(step_name=step.name, status="success", output="ok")

        with patch("workflow_engine.execute_step", side_effect=fake_exec):
            res = execute_workflow(wf)

        self.assertEqual(res.steps[0].status, "failed")
        self.assertEqual(res.steps[1].status, "skipped")
        self.assertEqual(res.steps[2].status, "success")


if __name__ == "__main__":
    unittest.main()

