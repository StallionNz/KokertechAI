"""tests/test_tool_execution_service.py — Unit & Integration tests for Realm 4.

Tests:
1. ToolExecutionResult dataclass serialization and properties.
2. CommandPolicyGuard regex & AST classification (SAFE, CAUTION, DESTRUCTIVE).
3. Human-In-The-Loop (HITL) confirmation integration.
4. Path containment verification.
5. Sandboxed command execution, timeouts, memory ceilings, and error capture.
6. Python script sandbox execution and scratch file cleanup.
7. Native system metrics inspection.
8. ServiceRegistry registration and reset lifecycles.
9. Controller direct command execution (<<CMD:...>>, /cmd).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services.registry import get_services
from services.tool_execution_service import (
    CommandPolicyGuard,
    RiskLevel,
    ToolExecutionResult,
    ToolExecutionService,
)


class TestToolExecutionResult:
    """Validate ToolExecutionResult dataclass behavior."""

    def test_default_values(self):
        res = ToolExecutionResult()
        assert res.stdout == ""
        assert res.stderr == ""
        assert res.exit_code == 0
        assert res.duration_ms == 0.0
        assert res.status == "success"
        assert res.sandboxed is True
        assert res.ok is True
        assert res.error_message is None

    def test_ok_property_negative(self):
        res_err = ToolExecutionResult(exit_code=1, status="success")
        assert res_err.ok is False

        res_blocked = ToolExecutionResult(exit_code=0, status="policy_blocked")
        assert res_blocked.ok is False

        res_timeout = ToolExecutionResult(exit_code=-1, status="timeout")
        assert res_timeout.ok is False

    def test_to_dict_serialization(self):
        res = ToolExecutionResult(
            command="python -V",
            stdout="Python 3.11",
            duration_ms=42.5,
        )
        d = res.to_dict()
        assert isinstance(d, dict)
        assert d["command"] == "python -V"
        assert d["stdout"] == "Python 3.11"
        assert d["duration_ms"] == 42.5
        assert d["ok"] is True


class TestCommandPolicyGuard:
    """Validate regex and AST screening heuristics."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "format C:",
            "format d: /fs:ntfs",
            "diskpart",
            "clean all",
            "rmdir /s /q C:\\",
            "rd /s /q C:\\",
            "del /f /s /q C:\\Windows",
            "Remove-Item -Recurse -Force C:\\",
            "rm -rf /",
            "rm -rf C:\\",
            "reg delete HKLM\\Software\\Test",
            "reg delete HKCU\\Environment /f",
            "Remove-ItemProperty -Path HKLM:\\Software",
            "Set-MpPreference -DisableRealtimeMonitoring $true",
            "netsh advfirewall set allprofiles state off",
            "Stop-Service WinDefend",
            "taskkill /f /im csrss.exe",
            "Stop-Process -Name lsass",
            ":(){ :|:& };:",
        ],
    )
    def test_destructive_patterns_classified(self, cmd):
        guard = CommandPolicyGuard()
        risk, reason = guard.screen_command(cmd)
        assert risk == RiskLevel.DESTRUCTIVE
        assert "destructive" in reason.lower()

    @pytest.mark.parametrize(
        "cmd",
        [
            "pip install requests",
            "npm install express",
            "git commit -m 'feat: test'",
            "git push origin main",
            "mkdir my_folder",
            "del temp.txt",
        ],
    )
    def test_caution_patterns_classified(self, cmd):
        guard = CommandPolicyGuard()
        risk, reason = guard.screen_command(cmd)
        assert risk == RiskLevel.CAUTION
        assert "caution" in reason.lower()

    @pytest.mark.parametrize(
        "cmd",
        [
            "dir",
            "ls",
            "Get-ChildItem",
            "Get-Process",
            "python --version",
            "python -V",
            "pytest tests/",
            "ipconfig",
            "echo Hello World",
        ],
    )
    def test_safe_patterns_classified(self, cmd):
        guard = CommandPolicyGuard()
        risk, _ = guard.screen_command(cmd)
        assert risk == RiskLevel.SAFE

    def test_empty_command_is_safe(self):
        guard = CommandPolicyGuard()
        risk, _ = guard.screen_command("")
        assert risk == RiskLevel.SAFE

    def test_screen_python_code_ast_dangerous(self):
        guard = CommandPolicyGuard()
        # Direct os.system
        code1 = "import os\nos.system('dir')"
        risk, reason = guard.screen_python_code(code1)
        assert risk == RiskLevel.DESTRUCTIVE
        assert "os.system" in reason

        # shutil.rmtree
        code2 = "import shutil\nshutil.rmtree('/tmp')"
        risk, reason = guard.screen_python_code(code2)
        assert risk == RiskLevel.DESTRUCTIVE
        assert "shutil.rmtree" in reason

    def test_screen_python_code_ast_safe(self):
        guard = CommandPolicyGuard()
        code = "print('Hello world!')\nx = [i**2 for i in range(10)]\nprint(sum(x))"
        risk, _ = guard.screen_python_code(code)
        assert risk == RiskLevel.SAFE

    def test_screen_python_syntax_error(self):
        guard = CommandPolicyGuard()
        code = "def broken(\n  return 1"
        risk, reason = guard.screen_python_code(code)
        assert risk == RiskLevel.DESTRUCTIVE
        assert "SyntaxError" in reason

    def test_hitl_confirmation_enforcement(self, monkeypatch):
        guard = CommandPolicyGuard()
        monkeypatch.setattr("services.tool_execution_service.CONFIG", {"tool_require_hitl_for_destructive": True})

        # Unconfirmed destructive command is blocked
        safe, msg, risk = guard.is_safe_to_execute("format C:")
        assert safe is False
        assert risk == RiskLevel.DESTRUCTIVE
        assert "HITL confirmation" in msg

        # Confirmed via allow_destructive=True is allowed
        safe_allowed, _, _ = guard.is_safe_to_execute("format C:", allow_destructive=True)
        assert safe_allowed is True

        # Confirmed via confirmed=True is allowed
        safe_confirmed, _, _ = guard.is_safe_to_execute("format C:", confirmed=True)
        assert safe_confirmed is True

        # Safe command passes without confirmation
        safe_pass, _, risk_pass = guard.is_safe_to_execute("dir")
        assert safe_pass is True
        assert risk_pass == RiskLevel.SAFE


class TestPathContainment:
    """Validate workspace path containment checks."""

    def test_path_containment_valid(self, tmp_path):
        service = ToolExecutionService(workspace=str(tmp_path))
        inside_file = tmp_path / "subfolder" / "data.txt"
        assert service.check_path_containment(str(inside_file)) is True

    def test_path_containment_invalid(self, tmp_path):
        service = ToolExecutionService(workspace=str(tmp_path / "sandbox"))
        outside_file = tmp_path / "sensitive" / "passwords.txt"
        assert service.check_path_containment(str(outside_file), allowed_dirs=[str(tmp_path / "sandbox")]) is False

    def test_path_containment_empty(self, tmp_path):
        service = ToolExecutionService(workspace=str(tmp_path))
        assert service.check_path_containment("") is False


class TestExecuteSandboxedCommand:
    """Validate sandboxed subprocess execution."""

    def test_execute_safe_command_success(self, tmp_path):
        service = ToolExecutionService(workspace=str(tmp_path))
        result = service.execute_sandboxed_command(
            cmd=["python", "-c", "print('Antigravity Sandbox Test')"],
            cwd=str(tmp_path),
        )
        assert result.ok is True
        assert result.status == "success"
        assert result.exit_code == 0
        assert "Antigravity Sandbox Test" in result.stdout
        assert result.sandboxed is True
        assert result.duration_ms >= 0

    def test_execute_blocked_by_policy(self, tmp_path):
        service = ToolExecutionService(workspace=str(tmp_path))
        result = service.execute_sandboxed_command(
            cmd="format C:",
            allow_destructive=False,
        )
        assert result.ok is False
        assert result.status == "policy_blocked"
        assert result.exit_code == -1
        assert "HITL confirmation" in (result.error_message or "")

    def test_execute_containment_violation(self, tmp_path):
        service = ToolExecutionService(workspace=str(tmp_path / "sandbox"))
        (tmp_path / "sandbox").mkdir(parents=True, exist_ok=True)
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir(parents=True, exist_ok=True)

        result = service.execute_sandboxed_command(
            cmd=["python", "-c", "print('test')"],
            cwd=str(outside_dir),
            enforce_containment=True,
        )
        assert result.status == "policy_blocked"
        assert "containment violation" in (result.error_message or "").lower()

    def test_execute_timeout_handling(self, tmp_path):
        service = ToolExecutionService(workspace=str(tmp_path))
        import subprocess

        def _fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="fake", timeout=1.0)

        with patch("scripts.safe_subprocess.run_with_limits", side_effect=_fake_run):
            result = service.execute_sandboxed_command(
                cmd=["python", "-c", "import time; time.sleep(10)"],
                timeout=1,
            )
            assert result.status == "timeout"
            assert result.exit_code == -1
            assert "timed out" in (result.error_message or "").lower()

    def test_execute_memory_limit_handling(self, tmp_path):
        service = ToolExecutionService(workspace=str(tmp_path))
        from scripts.safe_subprocess import MemoryLimitExceeded

        def _fake_run(*args, **kwargs):
            raise MemoryLimitExceeded(cmd="fake", memory_mb=512, rss_mb=550.0)

        with patch("scripts.safe_subprocess.run_with_limits", side_effect=_fake_run):
            result = service.execute_sandboxed_command(
                cmd=["python", "-c", "pass"],
                memory_mb=512,
            )
            assert result.status == "memory_limit_exceeded"
            assert result.exit_code == -1
            assert result.memory_peak_mb == 550.0
            assert "exceeded memory ceiling" in (result.error_message or "").lower()


class TestExecutePythonScript:
    """Validate Python script sandbox execution and file lifecycle."""

    def test_script_execution_success(self, tmp_path):
        service = ToolExecutionService(workspace=str(tmp_path))
        code = "print('Script Executed Successfully: 42')"
        result = service.execute_python_script(
            code=code,
            filename="test_script_run.py",
            cwd=str(tmp_path),
        )
        assert result.ok is True
        assert "Script Executed Successfully: 42" in result.stdout

        # Verify scratch file was deleted
        scratch_file = tmp_path / "_scratch" / "test_script_run.py"
        assert not scratch_file.exists()

    def test_script_destructive_ast_blocked(self, tmp_path):
        service = ToolExecutionService(workspace=str(tmp_path))
        code = "import os\nos.system('format C:')"
        result = service.execute_python_script(
            code=code,
            filename="destructive.py",
            allow_destructive=False,
        )
        assert result.status == "policy_blocked"
        assert "os.system" in (result.error_message or "")


class TestNativeSystemMetrics:
    """Validate host diagnostics inspection."""

    def test_get_native_system_metrics_structure(self, tmp_path):
        service = ToolExecutionService(workspace=str(tmp_path))
        metrics = service.get_native_system_metrics()

        assert isinstance(metrics, dict)
        assert "timestamp" in metrics
        assert "os" in metrics
        assert "cpu" in metrics
        assert "memory" in metrics
        assert "disk" in metrics
        assert "uptime_seconds" in metrics

        if metrics.get("psutil_available"):
            cpu = metrics["cpu"]
            assert "percent" in cpu
            assert "count_logical" in cpu

            mem = metrics["memory"]
            assert "total_mb" in mem
            assert "used_mb" in mem
            assert "percent" in mem


class TestServiceRegistryIntegration:
    """Validate ServiceRegistry singleton and reset patterns."""

    def test_registered_and_retrievable(self):
        svc = get_services().get("tool_execution_service")
        assert isinstance(svc, ToolExecutionService)

    def test_reset_preserves_factory_and_creates_fresh_instance(self):
        svc1 = get_services().get("tool_execution_service")
        get_services().reset()
        svc2 = get_services().get("tool_execution_service")
        assert isinstance(svc2, ToolExecutionService)
        assert svc1 is not svc2


class TestControllerDirectCommandIntegration:
    """Validate Controller <<CMD:...>> and /cmd direct sandboxed routing."""

    def test_controller_executes_sandboxed_command(self):
        from kokertechController import KokertechController
        ctrl = KokertechController()
        res = ctrl.execute_sandboxed_command(["python", "-c", "print('Controller Direct CMD')"])
        assert res.ok is True
        assert "Controller Direct CMD" in res.stdout

    def test_controller_process_input_cmd_tag(self):
        from kokertechController import KokertechController
        ctrl = KokertechController()
        out = ctrl.process_input("<<CMD:python -c \"print('Hello from Tag')\">>")
        assert isinstance(out, dict)
        final_text = out.get("final", "")
        assert "**Command**" in final_text
        assert "Hello from Tag" in final_text
        assert "success" in final_text
