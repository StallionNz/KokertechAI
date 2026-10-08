"""

docker_sandbox.py - Docker-based coding sandbox for KokertechAI.

Sprint 8.1: Executes Python code in isolated Docker containers with
resource limits, optional network restriction, and automatic fallback
to local subprocess when Docker is unavailable.

"""

import os
import subprocess
import sys
import time
from typing import Optional, List, Dict, Tuple, Any

from logging_config import get_logger


logger = get_logger(name="DockerSandbox")

DEFAULT_IMAGE = "python:3.11-slim"
DEFAULT_MEMORY_LIMIT = "256m"
DEFAULT_TIMEOUT = 30

class SandboxConfig:
    """
    SandboxConfig — resource limits and execution configuration for sandbox.
    """
    def __init__(self, image="python:3.11-slim", memory_limit="256m",
                 cpu_period=100000, cpu_quota=50000, timeout=30,
                 network_disabled=True, max_output_chars=10000):
        self.image = image
        self.memory_limit = memory_limit
        self.cpu_period = cpu_period
        self.cpu_quota = cpu_quota
        self.timeout = timeout
        self.network_disabled = network_disabled
        self.max_output_chars = max_output_chars

class ExecutionResult:
    """
    ExecutionResult — result of executing code in a sandbox.
    """
    def __init__(self, exit_code=-1, stdout="", stderr="", error="",
                 timed_out=False, duration_ms=0, docker_used=False):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.timed_out = timed_out
        self.duration_ms = duration_ms
        self.docker_used = docker_used

    @property
    def success(self):
        """True when exit_code == 0 and no error message."""
        return self.exit_code == 0 and not self.error

    def format_output(self):
        """Return a human-readable formatted string of the result."""
        parts = [f"EXIT CODE: {self.exit_code}"]
        parts.append(f"DOCKER: {'Yes' if self.docker_used else 'No'}")
        parts.append(f"TIMEOUT: {'Yes' if self.timed_out else 'No'}")
        if self.stdout:
            parts.append(f"STDOUT: {self.stdout}")
        if self.stderr:
            parts.append(f"STDERR: {self.stderr}")
        if self.error:
            parts.append(f"ERROR: {self.error}")
        if self.duration_ms:
            parts.append(f"DURATION: {self.duration_ms}ms")
        return "\n".join(parts)


def is_docker_available():
    """
    Check if Docker daemon is running and accessible.

    Returns True if 'import docker' succeeds and client.ping() works,
    False otherwise (ImportError or ping failure).
    """
    try:
        import docker
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def _try_pull_image(client, image):
    """
    Try to get or pull a Docker image.

    Returns True if the image is available locally or pulled successfully.
    Returns False if both get and pull fail.
    """
    try:
        client.images.get(image)
        return True
    except Exception:
        try:
            client.images.pull(image)
            return True
        except Exception:
            return False


def execute_code(code, config=None):
    """
    Execute Python code in a Docker sandbox or locally with fallback.
        
        Args:
            code: Python code to execute
            config: SandboxConfig with resource limits (uses defaults if None)
            
        Returns:
            ExecutionResult with stdout, stderr, exit_code, etc.
        
    """
    if config is None:
        config = SandboxConfig()
    if is_docker_available():
        try:
            return _execute_in_docker(code, config)
        except RuntimeError:
            pass  # Fall through to local execution on Docker failure
    return _execute_local(code, config)


def _execute_in_docker(code, config):
    """
    Execute code in a Docker container with full isolation.

    Uses the docker SDK to run code in a container with memory/cpu limits.
    Returns ExecutionResult with docker_used=True on success, or falls
    back gracefully on Docker errors.
    """
    import docker
    try:
        client = docker.from_env()
    except Exception as e:
        return ExecutionResult(
            exit_code=-1,
            error=f"Docker client init failed: {e}",
            docker_used=True,
        )

    # Try to get or pull the image
    if not _try_pull_image(client, config.image):
        return ExecutionResult(
            exit_code=-1,
            error=f"Image '{config.image}' not available (pull failed)",
            docker_used=True,
        )

    import tempfile

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            fpath = f.name

        volumes = {}
        if sys.platform == "win32":
            # On Windows, mount the temp dir
            tmpdir = os.path.dirname(fpath)
            volumes[tmpdir] = {"bind": "/code", "mode": "rw"}
            container_code_path = f"/code/{os.path.basename(fpath)}"
        else:
            volumes[fpath] = {"bind": "/code.py", "mode": "ro"}
            container_code_path = "/code.py"

        mem_limit = config.memory_limit if config.memory_limit else "256m"
        cpu_period = config.cpu_period or 100000
        cpu_quota = config.cpu_quota or 50000
        timeout = config.timeout or 30

        mem_swap_limit = None
        try:
            mem_mb = int(''.join(c for c in mem_limit if c.isdigit()))
            mem_swap_limit = f"{mem_mb * 2}m"
        except (ValueError, TypeError):
            pass

        container = client.containers.run(
            config.image,
            command=["python", container_code_path],
            volumes=volumes,
            mem_limit=mem_limit,
            memswap_limit=mem_swap_limit,
            cpu_period=cpu_period,
            cpu_quota=cpu_quota,
            network_disabled=config.network_disabled,
            detach=True,
            remove=False,
        )

        try:
            start = time.time()
            result = container.wait(timeout=timeout)
            duration_ms = int((time.time() - start) * 1000)
            exit_code = result.get("StatusCode", -1)
            stdout_bytes = container.logs(stdout=True, stderr=False)
            stderr_bytes = container.logs(stdout=False, stderr=True)
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            if config.max_output_chars and len(stdout) > config.max_output_chars:
                stdout = stdout[:config.max_output_chars] + "\n... [truncated]"
            if config.max_output_chars and len(stderr) > config.max_output_chars:
                stderr = stderr[:config.max_output_chars] + "\n... [truncated]"

            return ExecutionResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                docker_used=True,
                timed_out=False,
            )
        except Exception:
            # Timeout or other wait failure
            duration_ms = int((time.time() - start) * 1000)
            try:
                container.kill()
            except Exception:
                pass
            return ExecutionResult(
                exit_code=-1,
                error="Docker execution timed out",
                timed_out=True,
                docker_used=True,
                duration_ms=duration_ms,
            )
        finally:
            try:
                container.remove(force=True)
            except Exception:
                pass
            try:
                os.unlink(fpath)
            except (OSError, NameError):
                pass
    except Exception as e:
        raise RuntimeError(f"Docker execution failed: {e}")


def _execute_local(code, config):
    """
    Execute code locally using subprocess (fallback).
    """
    import tempfile

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            fpath = f.name

        start = time.time()
        result = subprocess.run(
            [sys.executable, fpath],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=config.timeout if config.timeout and config.timeout > 0 else None,
        )
        duration_ms = int((time.time() - start) * 1000)

        stdout = result.stdout
        stderr = result.stderr

        # Truncate output if configured
        if config.max_output_chars and len(stdout) > config.max_output_chars:
            stdout = stdout[:config.max_output_chars] + "\n... [truncated]"
        if config.max_output_chars and len(stderr) > config.max_output_chars:
            stderr = stderr[:config.max_output_chars] + "\n... [truncated]"

        try:
            os.unlink(fpath)
        except OSError:
            pass

        return ExecutionResult(
            exit_code=result.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=False,
        )
    except subprocess.TimeoutExpired:
        try:
            os.unlink(fpath)
        except (OSError, NameError):
            pass
        return ExecutionResult(
            exit_code=-1,
            error="Execution timed out",
            timed_out=True,
        )
    except PermissionError as e:
        try:
            os.unlink(fpath)
        except (OSError, NameError):
            pass
        return ExecutionResult(
            exit_code=-1,
            error=str(e),
        )
    except Exception as e:
        try:
            os.unlink(fpath)
        except (OSError, NameError):
            pass
        return ExecutionResult(
            exit_code=-1,
            error=str(e),
        )
