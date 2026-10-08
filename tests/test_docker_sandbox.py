"""
test_docker_sandbox.py - Unit tests for docker_sandbox.py
Sprint 8: Covers Docker execution, local fallback, timeout, and config.
"""

import pytest
import os
import sys
from unittest.mock import patch, MagicMock

from docker_sandbox import (
    execute_code, SandboxConfig, ExecutionResult,
    is_docker_available, _execute_local,
    DEFAULT_IMAGE, DEFAULT_MEMORY_LIMIT, DEFAULT_TIMEOUT,
)



class TestSandboxConfig:
    def test_defaults(self):
        cfg = SandboxConfig()
        assert cfg.image == DEFAULT_IMAGE
        assert cfg.memory_limit == DEFAULT_MEMORY_LIMIT
        assert cfg.timeout == DEFAULT_TIMEOUT
        assert cfg.network_disabled is True
        assert cfg.max_output_chars == 10000

    def test_custom_values(self):
        cfg = SandboxConfig(image="python:3.12-slim", memory_limit="512m", timeout=60, network_disabled=False)
        assert cfg.image == "python:3.12-slim"
        assert cfg.memory_limit == "512m"
        assert cfg.timeout == 60
        assert cfg.network_disabled is False


class TestExecutionResult:
    def test_success_property(self):
        r = ExecutionResult(exit_code=0, error="")
        assert r.success is True

    def test_failure_on_nonzero_exit(self):
        r = ExecutionResult(exit_code=1, error="")
        assert r.success is False

    def test_failure_on_error(self):
        r = ExecutionResult(exit_code=0, error="some error")
        assert r.success is False

    def test_format_output_success(self):
        r = ExecutionResult(stdout="hello", exit_code=0, docker_used=True, duration_ms=100)
        fmt = r.format_output()
        assert "STDOUT:" in fmt
        assert "hello" in fmt
        assert "EXIT CODE: 0" in fmt
        assert "DOCKER: Yes" in fmt
        assert "100ms" in fmt

    def test_format_output_with_error(self):
        r = ExecutionResult(stderr="traceback", exit_code=1, error="failed", docker_used=False)
        fmt = r.format_output()
        assert "ERROR: failed" in fmt
        assert "STDERR:" in fmt
        assert "DOCKER: No" in fmt

    def test_format_output_timeout(self):
        r = ExecutionResult(timed_out=True, exit_code=-1)
        fmt = r.format_output()
        assert "TIMEOUT:" in fmt


class TestLocalExecution:
    def test_simple_print(self):
        cfg = SandboxConfig(timeout=10)
        r = _execute_local("print(42)", config=cfg)
        assert r.exit_code == 0
        assert "42" in r.stdout
        assert r.docker_used is False

    def test_nonzero_exit(self):
        cfg = SandboxConfig(timeout=10)
        r = _execute_local("import sys; sys.exit(1)", config=cfg)
        assert r.exit_code == 1
        assert r.success is False

    def test_syntax_error(self):
        cfg = SandboxConfig(timeout=10)
        r = _execute_local("def (bad", config=cfg)
        assert r.exit_code != 0
        assert r.success is False

    def test_output_truncation(self):
        cfg = SandboxConfig(timeout=10, max_output_chars=50)
        r = _execute_local("print(chr(120) * 200)", config=cfg)
        assert r.exit_code == 0
        assert "truncated" in r.stdout

    def test_multiline_code(self):
        code = "x = 10" + chr(10) + "y = 20" + chr(10) + "print(x + y)"
        cfg = SandboxConfig(timeout=10)
        r = _execute_local(code, config=cfg)
        assert r.exit_code == 0
        assert "30" in r.stdout


class TestExecuteCode:
    def test_local_fallback_when_docker_unavailable(self):
        with patch("docker_sandbox.is_docker_available", return_value=False):
            r = execute_code("print(99)")
            assert r.exit_code == 0
            assert "99" in r.stdout
            assert r.docker_used is False

    def test_custom_config(self):
        with patch("docker_sandbox.is_docker_available", return_value=False):
            cfg = SandboxConfig(timeout=5, max_output_chars=100)
            r = execute_code("print(2)", config=cfg)
            assert r.exit_code == 0
            assert "2" in r.stdout

    def test_docker_failure_falls_back_to_local(self):
        with patch("docker_sandbox.is_docker_available", return_value=True):
            with patch("docker_sandbox._execute_in_docker", side_effect=RuntimeError("broken")):
                r = execute_code("print(3)")
                assert r.exit_code == 0
                assert "3" in r.stdout

    def test_duration_ms_populated(self):
        with patch("docker_sandbox.is_docker_available", return_value=False):
            r = execute_code("print(0)")
            assert r.duration_ms >= 0


class TestDockerAvailability:
    def test_returns_bool(self):
        result = is_docker_available()
        assert isinstance(result, bool)

    def test_with_docker_available(self):
        if is_docker_available():
            assert is_docker_available() is True

    def test_local_fallback_preserves_output(self):
        with patch("docker_sandbox.is_docker_available", return_value=False):
            r = execute_code("import sys; print(sys.version)")
            assert r.exit_code == 0
            assert len(r.stdout) > 0
            assert r.docker_used is False




class TestTimeoutBehavior:
    def test_timeout_config_respected(self):
        import subprocess as sp
        with patch("docker_sandbox.is_docker_available", return_value=False):
            with patch("docker_sandbox.subprocess.run", side_effect=sp.TimeoutExpired(cmd="python", timeout=1)):
                cfg = SandboxConfig(timeout=1)
                r = _execute_local("import time; time.sleep(10)", config=cfg)
                assert r.timed_out is True
                assert r.exit_code == -1

    def test_timeout_in_execute_code(self):
        import subprocess as sp
        with patch("docker_sandbox.is_docker_available", return_value=False):
            with patch("docker_sandbox.subprocess.run", side_effect=sp.TimeoutExpired(cmd="python", timeout=1)):
                r = execute_code("import time; time.sleep(10)")
                assert r.timed_out is True
                assert "TIMEOUT:" in r.format_output()


class TestEdgeCases:
    def test_empty_code(self):
        with patch("docker_sandbox.is_docker_available", return_value=False):
            r = execute_code("")
            assert r.exit_code == 0

    def test_large_output(self):
        with patch("docker_sandbox.is_docker_available", return_value=False):
            r = execute_code("print(chr(120) * 50000)")
            assert r.exit_code == 0
            assert len(r.stdout) <= 10050

    def test_import_error(self):
        with patch("docker_sandbox.is_docker_available", return_value=False):
            r = execute_code("import nonexistent_module_xyz")
            assert r.exit_code != 0
            assert r.success is False


class TestIsDockerAvailable:
    """Cover the Docker-available path (lines 77-78).

    Note: Uses patch.dict(sys.modules) because is_docker_available() does
    'import docker' locally, which resolves via sys.modules, not via
    docker_sandbox.docker module attribute.
    """

    def test_docker_available_success(self):
        """is_docker_available returns True when docker.from_env() and ping() work."""
        mock_docker_mod = MagicMock()
        mock_client = MagicMock()
        mock_docker_mod.from_env.return_value = mock_client
        with patch.dict("sys.modules", {"docker": mock_docker_mod}):
            result = is_docker_available()
            assert result is True
            mock_client.ping.assert_called_once()

    def test_docker_available_import_error(self):
        """is_docker_available returns False when import docker fails."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "docker":
                raise ImportError("no docker module")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = is_docker_available()
            assert result is False

    def test_docker_available_ping_fails(self):
        """is_docker_available returns False when ping() raises."""
        mock_docker_mod = MagicMock()
        mock_client = MagicMock()
        mock_client.ping.side_effect = RuntimeError("daemon not running")
        mock_docker_mod.from_env.return_value = mock_client
        with patch.dict("sys.modules", {"docker": mock_docker_mod}):
            result = is_docker_available()
            assert result is False


class TestTryPullImage:
    """Cover _try_pull_image (lines 78-87).

    Note: _try_pull_image receives client as a parameter, so no sys.modules
    patching is needed — we pass a MagicMock client directly.
    """

    def test_image_exists_locally(self):
        """client.images.get succeeds -> returns True."""
        mock_client = MagicMock()
        from docker_sandbox import _try_pull_image
        result = _try_pull_image(mock_client, "python:3.11-slim")
        assert result is True
        mock_client.images.get.assert_called_once_with("python:3.11-slim")
        mock_client.images.pull.assert_not_called()

    def test_image_pulled_successfully(self):
        """get raises, pull succeeds -> returns True."""
        mock_client = MagicMock()
        mock_client.images.get.side_effect = Exception("not found")
        mock_client.images.pull.return_value = MagicMock()
        from docker_sandbox import _try_pull_image
        result = _try_pull_image(mock_client, "python:3.12-slim")
        assert result is True
        mock_client.images.pull.assert_called_once_with("python:3.12-slim")

    def test_image_pull_fails(self):
        """both get and pull raise -> returns False."""
        mock_client = MagicMock()
        mock_client.images.get.side_effect = Exception("not found")
        mock_client.images.pull.side_effect = Exception("registry unreachable")
        from docker_sandbox import _try_pull_image
        result = _try_pull_image(mock_client, "python:3.13-slim")
        assert result is False


class TestExecuteInDocker:
    """Cover _execute_in_docker with mocked docker library.

    Note: Uses patch.dict(sys.modules) because _execute_in_docker() does
    'import docker' locally, which resolves via sys.modules, not via
    docker_sandbox.docker module attribute.
    """

    @staticmethod
    def _make_mocks():
        """Create a full set of docker mocks for _execute_in_docker tests."""
        mock_docker_mod = MagicMock()
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_docker_mod.from_env.return_value = mock_client
        return mock_docker_mod, mock_client, mock_container

    def test_image_not_available(self):
        """_try_pull_image returns False -> error ExecutionResult."""
        mock_docker_mod, mock_client, _ = self._make_mocks()
        mock_client.images.get.side_effect = Exception("not found")
        mock_client.images.pull.side_effect = Exception("cannot pull")
        with patch.dict("sys.modules", {"docker": mock_docker_mod}):
            from docker_sandbox import _execute_in_docker, SandboxConfig
            config = SandboxConfig()
            result = _execute_in_docker("print(1)", config)
            assert result.error != ""
            assert "not available" in result.error
            assert result.docker_used is True

    def test_successful_execution(self):
        """Full Docker execution path returns stdout and exit code 0."""
        mock_docker_mod, mock_client, mock_container = self._make_mocks()
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.side_effect = [b"42", b""]
        with patch.dict("sys.modules", {"docker": mock_docker_mod}):
            from docker_sandbox import _execute_in_docker, SandboxConfig
            config = SandboxConfig()
            result = _execute_in_docker("print(42)", config)
            assert result.exit_code == 0
            assert "42" in result.stdout
            assert result.docker_used is True
            assert result.error == ""
            mock_container.remove.assert_called_once_with(force=True)

    def test_execution_timeout(self):
        """container.wait raises -> timed_out ExecutionResult, container killed."""
        mock_docker_mod, mock_client, mock_container = self._make_mocks()
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_container.wait.side_effect = Exception("timeout")
        with patch.dict("sys.modules", {"docker": mock_docker_mod}):
            from docker_sandbox import _execute_in_docker, SandboxConfig
            config = SandboxConfig()
            result = _execute_in_docker("import time; time.sleep(999)", config)
            assert result.timed_out is True
            assert result.error != ""
            assert result.docker_used is True
            mock_container.kill.assert_called_once()

    def test_stdout_truncation(self):
        """stdout exceeding max_output_chars gets truncated."""
        mock_docker_mod, mock_client, mock_container = self._make_mocks()
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_container.wait.return_value = {"StatusCode": 0}
        long_output = "x" * 20000
        mock_container.logs.side_effect = [long_output.encode(), b""]
        with patch.dict("sys.modules", {"docker": mock_docker_mod}):
            from docker_sandbox import _execute_in_docker, SandboxConfig
            config = SandboxConfig(max_output_chars=100)
            result = _execute_in_docker("print('x'*20000)", config)
            assert "truncated" in result.stdout
            assert len(result.stdout) < 200

    def test_stderr_truncation(self):
        """stderr exceeding max_output_chars gets truncated."""
        mock_docker_mod, mock_client, mock_container = self._make_mocks()
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_container.wait.return_value = {"StatusCode": 1}
        long_err = "error!" * 5000
        mock_container.logs.side_effect = [b"", long_err.encode()]
        with patch.dict("sys.modules", {"docker": mock_docker_mod}):
            from docker_sandbox import _execute_in_docker, SandboxConfig
            config = SandboxConfig(max_output_chars=50)
            result = _execute_in_docker("bad code", config)
            assert "truncated" in result.stderr
            assert result.docker_used is True

    def test_container_remove_failure_does_not_crash(self):
        """container.remove raises Exception -> handled gracefully."""
        mock_docker_mod, mock_client, mock_container = self._make_mocks()
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.side_effect = [b"ok", b""]
        mock_container.remove.side_effect = Exception("remove failed")
        with patch.dict("sys.modules", {"docker": mock_docker_mod}):
            from docker_sandbox import _execute_in_docker, SandboxConfig
            config = SandboxConfig()
            result = _execute_in_docker("print('ok')", config)
            assert result.exit_code == 0
            assert result.docker_used is True
            mock_container.remove.assert_called_once_with(force=True)

    def test_kill_also_fails_on_timeout(self):
        """Both container.wait and container.kill raise -> handled."""
        mock_docker_mod, mock_client, mock_container = self._make_mocks()
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.return_value = mock_container
        mock_container.wait.side_effect = Exception("timeout")
        mock_container.kill.side_effect = Exception("kill also failed")
        with patch.dict("sys.modules", {"docker": mock_docker_mod}):
            from docker_sandbox import _execute_in_docker, SandboxConfig
            config = SandboxConfig()
            result = _execute_in_docker("import time; time.sleep(999)", config)
            assert result.timed_out is True
            mock_container.kill.assert_called_once()

    def test_container_run_raises_exception(self):
        """client.containers.run raises -> RuntimeError propagated."""
        mock_docker_mod, mock_client, mock_container = self._make_mocks()
        mock_client.images.get.return_value = MagicMock()
        mock_client.containers.run.side_effect = Exception("container creation failed")
        with patch.dict("sys.modules", {"docker": mock_docker_mod}):
            from docker_sandbox import _execute_in_docker, SandboxConfig
            config = SandboxConfig()
            import pytest
            with pytest.raises(RuntimeError, match="Docker execution failed"):
                _execute_in_docker("print(1)", config)


class TestExecuteCodeDockerSuccess:
    """Cover the Docker success path in execute_code (lines 118-119)."""

    def test_docker_success_path(self):
        """Docker available and _execute_in_docker succeeds -> result returned."""
        with patch("docker_sandbox.is_docker_available", return_value=True):
            with patch("docker_sandbox._execute_in_docker") as mock_exec:
                mock_result = ExecutionResult(stdout="docker ok", exit_code=0, docker_used=True)
                mock_exec.return_value = mock_result
                result = execute_code("print('docker')")
                assert result.stdout == "docker ok"
                assert result.docker_used is True
                assert result.duration_ms >= 0


class TestLocalExecutionExceptions:
    """Cover _execute_local exception handler (lines 232-233) and stderr truncation (line 217)."""

    def test_generic_exception_returns_result(self):
        """run_with_limits raises generic Exception -> returns ExecutionResult with error."""
        with patch("docker_sandbox.subprocess.run", side_effect=PermissionError("access denied")):
            from docker_sandbox import _execute_local, SandboxConfig
            cfg = SandboxConfig(timeout=10)
            result = _execute_local("print(1)", cfg)
            assert result.error != ""
            assert result.docker_used is False
            assert result.exit_code == -1

    def test_stderr_truncation(self):
        """stderr exceeding max_output_chars gets truncated in _execute_local."""
        import subprocess as sp
        mock_result = sp.CompletedProcess(args=["python"], returncode=1, stdout="", stderr="E" * 50000)
        with patch("docker_sandbox.subprocess.run", return_value=mock_result):
            from docker_sandbox import _execute_local, SandboxConfig
            cfg = SandboxConfig(timeout=10, max_output_chars=100)
            result = _execute_local("print(1)", cfg)
            assert "truncated" in result.stderr
            assert len(result.stderr) < 200

    def test_tempfile_cleanup_failure_does_not_crash(self):
        """os.unlink on temp file raises -> handled in finally."""
        import subprocess as sp
        mock_result = sp.CompletedProcess(args=["python"], returncode=0, stdout="ok", stderr="")
        with patch("docker_sandbox.subprocess.run", return_value=mock_result):
            with patch("docker_sandbox.os.unlink", side_effect=OSError("file in use")):
                from docker_sandbox import _execute_local, SandboxConfig
                cfg = SandboxConfig(timeout=10)
                result = _execute_local("print('ok')", cfg)
                assert result.exit_code == 0
                assert result.stdout == "ok"
