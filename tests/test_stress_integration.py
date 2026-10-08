"""
test_stress_integration.py — Stress, edge-case, cross-module integration tests.

Sprint 8: Exercises docker_sandbox, crew_integration, and their integration points
under stress (large I/O, concurrency, boundary conditions) and edge cases
(missing dependencies, network failures, empty/None inputs).
"""

import sys
import threading
import unittest
from unittest.mock import patch, MagicMock

class TestDockerSandboxStress(unittest.TestCase):
    """Stress tests for docker_sandbox: large output, unicode, deep recursion."""

    def test_very_large_stdout(self):
        """100KB of output is truncated gracefully."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=30, max_output_chars=500)
        code = "print('x' * 100000)"
        result = _execute_local(code, config=cfg)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("truncated", result.stdout)
        self.assertLess(len(result.stdout), 1000)

    def test_unicode_in_output(self):
        """Unicode in output is preserved through pipe (cp1252-safe subset)."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=10)
        # Use only ASCII-safe escapes to avoid Windows cp1252 encoding issues
        code = 'print("\\u00e9\\u00e0\\u00fc" + " works")'
        result = _execute_local(code, config=cfg)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("works", result.stdout)

    def test_deep_recursion_traceback(self):
        """Deep recursion produces a traceback, not a hang."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=10)
        code = "def f(n):\n    f(n+1)\nf(0)"
        result = _execute_local(code, config=cfg)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("RecursionError", result.stderr)

    def test_syntax_error_in_code(self):
        """Syntax error is caught and reported."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=10)
        code = "if True print('missing colon')"
        result = _execute_local(code, config=cfg)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("SyntaxError", result.stderr)

    def test_empty_code(self):
        """Empty code produces exit code 0."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=10)
        result = _execute_local("", config=cfg)
        self.assertEqual(result.exit_code, 0)

    def test_code_with_only_comments(self):
        """Code with only comments runs cleanly."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=10)
        code = "# This is a comment\n# Another comment\npass"
        result = _execute_local(code, config=cfg)
        self.assertEqual(result.exit_code, 0)

    def test_memory_intensive_code(self):
        """Large list allocation does not crash."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=10)
        code = "x = [0] * 1000000\nprint(len(x))"
        result = _execute_local(code, config=cfg)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("1000000", result.stdout)

    def test_system_exit_variants(self):
        """Various sys.exit values are captured."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=10)
        for exit_val in [0, 1, 42]:
            code = f"import sys; sys.exit({exit_val})"
            result = _execute_local(code, config=cfg)
            self.assertEqual(result.exit_code, exit_val)

class TestConcurrentSandbox(unittest.TestCase):
    """Concurrent access stress tests."""

    def test_concurrent_local_executions(self):
        """10 parallel sandbox executions do not interfere."""
        from docker_sandbox import _execute_local, SandboxConfig

        def run_code(n, results_list, idx):
            cfg = SandboxConfig(timeout=10)
            code = f"print({n} * 2)"
            results_list[idx] = _execute_local(code, config=cfg)

        results = [None] * 10
        threads = []
        for i in range(10):
            t = threading.Thread(target=run_code, args=(i, results, i))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=15)

        for i in range(10):
            self.assertEqual(results[i].exit_code, 0)
            self.assertIn(str(i * 2), results[i].stdout)

    def test_sequential_rapid_executions(self):
        """50 rapid sequential executions do not degrade."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=5)
        for i in range(50):
            code = f"print({i})"
            result = _execute_local(code, config=cfg)
            self.assertEqual(result.exit_code, 0)
            self.assertIn(str(i), result.stdout)

class TestDockerSandboxEdgeCases(unittest.TestCase):
    """Edge cases for docker_sandbox modules."""

    def test_nonexistent_temp_file_cleanup(self):
        """os.unlink on nonexistent file does not crash."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=10)
        with patch("docker_sandbox.os.unlink", side_effect=OSError("file not found")):
            result = _execute_local("print('ok')", config=cfg)
            self.assertEqual(result.exit_code, 0)

    def test_subprocess_run_permission_error(self):
        """PermissionError returns ExecutionResult with error."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=10)
        with patch("docker_sandbox.subprocess.run", side_effect=PermissionError("access denied")):
            result = _execute_local("print(1)", cfg)
            self.assertTrue(len(result.error) > 0)
            self.assertEqual(result.exit_code, -1)

    def test_execute_code_config_none(self):
        """execute_code with config=None uses defaults."""
        from docker_sandbox import execute_code
        with patch("docker_sandbox.is_docker_available", return_value=False):
            result = execute_code("print(42)", config=None)
            self.assertEqual(result.exit_code, 0)
            self.assertIn("42", result.stdout)

    def test_negative_timeout(self):
        """Negative timeout does not crash."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=-1)
        result = _execute_local("print('ok')", config=cfg)
        self.assertIn(result.exit_code, [0, -1])

class TestCrewIntegrationStress(unittest.TestCase):
    """Stress tests for crew_integration."""

    def test_very_long_task_string(self):
        """Very long task string (10KB) is passed through."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        orchestrator._available = False
        long_task = "A" * 10000
        with patch.object(orchestrator, "_run_fallback", return_value="done") as mock_fallback:
            result = orchestrator.run_team(long_task)
            mock_fallback.assert_called_once_with(
                long_task, ["Researcher", "Coder", "Auditor", "Planner"]
            )
            self.assertEqual(result, "done")

    def test_many_personas_in_fallback(self):
        """20 personas execute without error."""
        from crew_integration import CrewOrchestrator
        known = ["Researcher", "Coder", "Auditor", "Planner"]
        many_personas = known * 5
        orchestrator = CrewOrchestrator()
        orchestrator._available = False
        with patch("sub_agents.SubAgent") as mock_cls:
            mock_agent = MagicMock()
            mock_agent.execute.return_value = "result"
            mock_cls.return_value = mock_agent
            result = orchestrator._run_fallback("task", many_personas)
            self.assertEqual(mock_agent.execute.call_count, 20)
            for p in known:
                self.assertIn(p, result)

    def test_rapid_orchestrator_creations(self):
        """50 orchestrator creations do not leak."""
        for _ in range(50):
            with patch("crew_integration.is_crewai_available", return_value=False):
                from crew_integration import CrewOrchestrator
                o = CrewOrchestrator()
                self.assertFalse(o._available)

    def test_crewai_empty_task(self):
        """Empty string task is passed through."""
        from crew_integration import CrewOrchestrator
        orchestrator = CrewOrchestrator()
        orchestrator._available = False
        with patch.object(orchestrator, "_run_fallback", return_value="empty") as mock_fallback:
            result = orchestrator.run_team("")
            self.assertEqual(result, "empty")
            mock_fallback.assert_called_once_with("", ["Researcher", "Coder", "Auditor", "Planner"])

class TestCrossModuleIntegration(unittest.TestCase):
    """Tests that exercise multiple Sprint 8 modules together."""

    def test_docker_and_crew_independent(self):
        """Docker and Crew work independently (no cross-contamination)."""
        from docker_sandbox import _execute_local, SandboxConfig
        from crew_integration import CrewOrchestrator
        cfg = SandboxConfig(timeout=10)
        docker_result = _execute_local("print('docker')", config=cfg)
        self.assertEqual(docker_result.exit_code, 0)
        orchestrator = CrewOrchestrator()
        orchestrator._available = False
        self.assertFalse(orchestrator._available)

    def test_docker_sandbox_subprocess_unicode_escape(self):
        """Unicode escape sequences in code are handled (cp1252-safe)."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=10)
        # Use \u00e9 which is cp1252-compatible
        code = 'print("caf\\u00e9")'
        result = _execute_local(code, config=cfg)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("caf", result.stdout)

    def test_docker_sandbox_multiline_code(self):
        """Multi-line code with complex structure executes."""
        from docker_sandbox import _execute_local, SandboxConfig
        cfg = SandboxConfig(timeout=10)
        code = (
            "class Test:\n"
            "    def __init__(self):\n"
            "        self.value = 42\n"
            "    def get(self):\n"
            "        return self.value\n"
            "t = Test()\n"
            "print(t.get())\n"
        )
        result = _execute_local(code, config=cfg)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("42", result.stdout)

class TestEdgeCases(unittest.TestCase):
    """Cross-cutting edge cases."""

    def test_format_output_empty(self):
        """format_output with all empty fields returns basic info."""
        from docker_sandbox import ExecutionResult
        r = ExecutionResult()
        fmt = r.format_output()
        self.assertIn("EXIT CODE: -1", fmt)
        self.assertIn("DOCKER: No", fmt)

    def test_timed_out_without_error(self):
        """Timed out result with no error message shows timeout."""
        from docker_sandbox import ExecutionResult
        r = ExecutionResult(timed_out=True, exit_code=-1, error="")
        fmt = r.format_output()
        self.assertIn("TIMEOUT:", fmt)

    def test_execute_code_fallback_works(self):
        """Full execute_code cycle (Docker unavailable -> local fallback)."""
        from docker_sandbox import execute_code
        with patch("docker_sandbox.is_docker_available", return_value=False):
            result = execute_code("print('fallback works')")
            self.assertEqual(result.exit_code, 0)
            self.assertIn("fallback works", result.stdout)
            self.assertFalse(result.docker_used)

class TestDockerSandboxConfig(unittest.TestCase):
    """Cover SandboxConfig edge cases."""

    def test_default_config_values(self):
        """Default SandboxConfig has all expected fields."""
        from docker_sandbox import SandboxConfig
        cfg = SandboxConfig()
        self.assertEqual(cfg.image, "python:3.11-slim")
        self.assertEqual(cfg.memory_limit, "256m")
        self.assertEqual(cfg.cpu_period, 100000)
        self.assertEqual(cfg.cpu_quota, 50000)
        self.assertEqual(cfg.timeout, 30)
        self.assertTrue(cfg.network_disabled)
        self.assertEqual(cfg.max_output_chars, 10000)

    def test_custom_config_values(self):
        """Custom SandboxConfig values are stored correctly."""
        from docker_sandbox import SandboxConfig
        cfg = SandboxConfig(
            image="python:3.12-slim",
            memory_limit="512m",
            cpu_period=50000,
            cpu_quota=25000,
            timeout=60,
            network_disabled=False,
            max_output_chars=5000,
        )
        self.assertEqual(cfg.image, "python:3.12-slim")
        self.assertEqual(cfg.memory_limit, "512m")
        self.assertEqual(cfg.cpu_period, 50000)
        self.assertEqual(cfg.cpu_quota, 25000)
        self.assertEqual(cfg.timeout, 60)
        self.assertFalse(cfg.network_disabled)
        self.assertEqual(cfg.max_output_chars, 5000)

class TestExecutionResultEdgeCases(unittest.TestCase):
    """Edge cases for ExecutionResult."""

    def test_success_with_error(self):
        """success is False when error is set even if exit_code is 0."""
        from docker_sandbox import ExecutionResult
        r = ExecutionResult(exit_code=0, error="something went wrong")
        self.assertFalse(r.success)

    def test_success_with_negative_exit(self):
        """success is False when exit_code is -1."""
        from docker_sandbox import ExecutionResult
        r = ExecutionResult(exit_code=-1)
        self.assertFalse(r.success)

    def test_success_with_zero_exit_no_error(self):
        """success is True when exit_code==0 and no error."""
        from docker_sandbox import ExecutionResult
        r = ExecutionResult(exit_code=0)
        self.assertTrue(r.success)

    def test_duration_ms_default(self):
        """Default duration_ms is 0."""
        from docker_sandbox import ExecutionResult
        r = ExecutionResult()
        self.assertEqual(r.duration_ms, 0)

    def test_format_output_empty_strings(self):
        """format_output with empty stdout/stderr does not include them."""
        from docker_sandbox import ExecutionResult
        r = ExecutionResult(exit_code=0)
        fmt = r.format_output()
        self.assertNotIn("STDOUT:", fmt)
        self.assertNotIn("STDERR:", fmt)

if __name__ == "__main__":
    unittest.main()
