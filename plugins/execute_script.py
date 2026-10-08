"""
plugins/execute_script.py - Execute Python scripts with Docker sandbox support.

Sprint 8.1: Optionally executes code in isolated Docker containers
via docker_sandbox.py, with automatic local fallback.
"""

import os
import sys
from config import CONFIG
from logging_config import get_logger

logger = get_logger(name="ExecuteScript")

PLUGIN_METADATA = {
    "name": "Execute Script",
    "description": "Writes and executes a Python script, optionally in a Docker sandbox",
    "version": "2.0.0",
    "tags": ["code", "execution", "script", "python", "docker", "sandbox"],
    "author": "KokertechAI",
    "requires": [],
    "permissions": ['shell', 'fs_write']
}

COMMAND_NAME = "EXECUTE_SCRIPT"
SCHEMA = {
    "action": "EXECUTE_SCRIPT",
    "filename": "<script_name.py>",
    "content": "<python code to execute>",
    "sandbox": True
}
WORKSPACE_DIR = r"C:\KokertechAI"


def execute(intent_json):
    """Execute a Python script, optionally using Docker sandbox.

    intent_json keys:
        filename  – target script name (default: temp_script.py)
        content   – Python source code to execute (also accepts 'script', 'code', 'source', 'body', 'python')
        sandbox   – bool; if True, run in Docker sandbox (default: False)
        timeout   – int; max seconds (default: 30)
        image     – str; Docker image (default: python:3.11-slim)
    """
    raw_name = (
        intent_json.get("filename")
        or intent_json.get("path")
        or intent_json.get("file")
        or intent_json.get("script_name")
        or "temp_script.py"
    )
    script_name = os.path.basename(raw_name) or "temp_script.py"

    code_content = (
        intent_json.get("content")
        or intent_json.get("script")
        or intent_json.get("code")
        or intent_json.get("source")
        or intent_json.get("source_code")
        or intent_json.get("body")
        or intent_json.get("python")
        or ""
    )
    use_sandbox = intent_json.get("sandbox", False)
    timeout = intent_json.get("timeout", 30)
    image = intent_json.get("image", "python:3.11-slim")

    # If code_content is empty, check if filename/path points to an existing file
    if not code_content and (intent_json.get("filename") or intent_json.get("path") or intent_json.get("file")):
        target = intent_json.get("filename") or intent_json.get("path") or intent_json.get("file")
        candidate_paths = [
            target,
            os.path.join(WORKSPACE_DIR, target),
            os.path.join(WORKSPACE_DIR, "scripts", target),
            os.path.join(WORKSPACE_DIR, "plugins", target),
        ]
        for cp in candidate_paths:
            if os.path.isfile(cp):
                try:
                    with open(cp, "r", encoding="utf-8") as f:
                        code_content = f.read()
                    script_name = os.path.basename(cp)
                    break
                except OSError:
                    pass

    if not code_content:
        return "❌ No code provided to execute."

    # Strip markdown code fences if model wrapped code in ```python ... ```
    stripped = code_content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code_content = "\n".join(lines)

    try:
        from services.registry import get_services
        tool_svc = get_services().get("tool_execution_service")
        if tool_svc is not None:
            res = tool_svc.execute_python_script(
                code=code_content,
                filename=script_name,
                timeout=timeout,
                use_docker=use_sandbox,
                image=image,
            )
            if res.status == "policy_blocked":
                return f"❌ Script blocked by security policy: {res.error_message}"
            if res.status == "timeout":
                return f"❌ Script execution timed out after {timeout}s"
            if res.status == "memory_limit_exceeded":
                return f"❌ Script killed: memory limit exceeded ({res.memory_peak_mb} MB)"
            if not res.ok and res.error_message:
                return f"❌ Script execution failed: {res.error_message}"
            return f"[EXECUTION COMPLETE]\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
    except (ImportError, KeyError, RuntimeError, OSError, ValueError, AttributeError):
        # Graceful fallback: local execution if tool_execution_service unavailable
        pass

    # --- Local path fallback ------------------------------------------
    scratch_dir = os.path.join(WORKSPACE_DIR, "_scratch")
    os.makedirs(scratch_dir, exist_ok=True)
    script_path = os.path.join(scratch_dir, script_name)

    try:
        from scripts.safe_subprocess import run_with_limits, MemoryLimitExceeded
        import subprocess

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code_content)

        local_memory_mb = int(CONFIG.get("orchestration_default_memory_mb", 512))
        result = run_with_limits(
            [sys.executable, script_path],
            cwd=WORKSPACE_DIR,
            timeout=timeout,
            memory_mb=local_memory_mb,
        )
        stdout = result.stdout.strip() if result.stdout else ""
        stderr = result.stderr.strip() if result.stderr else ""
        return f"[EXECUTION COMPLETE]\nSTDOUT: {stdout}\nSTDERR: {stderr}"
    except MemoryLimitExceeded as e:
        return f"❌ Script killed: memory limit exceeded ({e.memory_mb} MB)"
    except subprocess.TimeoutExpired:
        return f"❌ Script execution timed out after {timeout}s"
    except (OSError, RuntimeError, ValueError) as e:
        return f"❌ Script execution failed: {str(e)}"
    finally:
        try:
            if os.path.exists(script_path):
                os.unlink(script_path)
        except OSError:
            # Cleanup guard: ignore unlink error if file already removed
            pass
