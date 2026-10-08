"""services/tool_execution_service.py — Local Sandboxed Tool Execution & Native OS Virtualization.

Realm 4: Local Sandboxed Tool Execution & Native OS Virtualization.
Enforces Zero-Trust Invariants:
1. Sandboxed Subprocess Execution with RSS memory limits and process-tree cleanup.
2. CommandPolicyGuard screening (regex + AST) for destructive commands with HITL confirmation.
3. Strict path containment defense (workspace directory sandboxing).
4. Windowless execution (CREATE_NO_WINDOW = 0x08000000) preventing Windows console flashes.
5. Native OS diagnostics (CPU, RAM, Disks, Battery, Uptime) via non-blocking queries.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from config import CONFIG, WORKSPACE_DIR
from logging_config import get_logger
from scripts import safe_subprocess

logger = get_logger(name="ToolExecutionService")

# Windows process creation flag to prevent console window popup
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# psutil availability
_PSUTIL_AVAILABLE = False
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    pass


class RiskLevel(str, Enum):
    """Command safety classification."""
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    DESTRUCTIVE = "DESTRUCTIVE"


@dataclass
class ToolExecutionResult:
    """Standardized result of a sandboxed tool or command execution."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: float = 0.0
    memory_peak_mb: float = 0.0
    status: str = "success"  # "success", "timeout", "memory_limit_exceeded", "policy_blocked", "error"
    sandboxed: bool = True
    error_message: Optional[str] = None
    command: str = ""

    @property
    def ok(self) -> bool:
        """True if execution completed without policy block, error, or non-zero exit."""
        return self.status == "success" and self.exit_code == 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        d = asdict(self)
        d["ok"] = self.ok
        return d


class CommandPolicyGuard:
    """Deterministic security filter and AST guard for CLI commands and Python scripts."""

    # Blacklist patterns for destructive operations (case-insensitive regex)
    _DESTRUCTIVE_PATTERNS = [
        # Disk formatting and volume wipes
        re.compile(r"\bformat\b(\s+[a-zA-Z]:)?", re.IGNORECASE),
        re.compile(r"\bdiskpart\b", re.IGNORECASE),
        re.compile(r"\bclean\s+all\b", re.IGNORECASE),
        # Recursive directory removal on root or system drives
        re.compile(r"\b(rmdir|rd)\b\s+/(s|s\s+/q|q\s+/s)", re.IGNORECASE),
        re.compile(r"\bdel\b\s+/[fFqQsS]+.*[a-zA-Z]:\\", re.IGNORECASE),
        re.compile(r"\bRemove-Item\b.*(-Recurse|-r).*(-Force|-f)?\s+([A-Za-z]:\\|\$env:|/|\\)", re.IGNORECASE),
        re.compile(r"\brm\b\s+-r[fF]?\s+(/|[a-zA-Z]:\\|\*)", re.IGNORECASE),
        # Registry destruction
        re.compile(r"\breg\b\s+(delete)\s+[\"']?HK(LM|CU|CR|U|CC)", re.IGNORECASE),
        re.compile(r"\bRemove-ItemProperty\b.*HK(LM|CU|CR|U|CC)", re.IGNORECASE),
        # Windows Defender & Firewall neutralization
        re.compile(r"\bSet-MpPreference\b.*-DisableRealtimeMonitoring", re.IGNORECASE),
        re.compile(r"\bnetsh\b\s+advfirewall\s+set.*state\s+off", re.IGNORECASE),
        re.compile(r"\bStop-Service\b.*(WinDefend|MpsSvc|wuauserv)", re.IGNORECASE),
        # Critical process termination
        re.compile(r"\btaskkill\b.*/im\s+(csrss|lsass|smss|services|svchost)\.exe", re.IGNORECASE),
        re.compile(r"\bStop-Process\b.*-Name\s+(csrss|lsass|smss|services|svchost)", re.IGNORECASE),
        # Fork bombs
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;:", re.IGNORECASE),
    ]

    # Cautionary patterns (file modifications, installs, git state mutations)
    _CAUTION_PATTERNS = [
        re.compile(r"\bpip\b\s+install\b", re.IGNORECASE),
        re.compile(r"\bnpm\b\s+install\b", re.IGNORECASE),
        re.compile(r"\bgit\b\s+(commit|push|checkout|reset|rebase|clean)\b", re.IGNORECASE),
        re.compile(r"\b(mkdir|md|New-Item)\b", re.IGNORECASE),
        re.compile(r"\b(del|Remove-Item|rm)\b", re.IGNORECASE),
        re.compile(r"\b(copy|cp|move|mv|xcopy|robocopy)\b", re.IGNORECASE),
    ]

    @classmethod
    def screen_command(cls, command: str) -> Tuple[RiskLevel, str]:
        """Classify command risk level and return explanation."""
        if not command or not command.strip():
            return RiskLevel.SAFE, "Empty command"

        cmd_str = command.strip()

        for pat in cls._DESTRUCTIVE_PATTERNS:
            match = pat.search(cmd_str)
            if match:
                return (
                    RiskLevel.DESTRUCTIVE,
                    f"Blocked destructive pattern '{match.group(0)}' detected in command",
                )

        for pat in cls._CAUTION_PATTERNS:
            match = pat.search(cmd_str)
            if match:
                return (
                    RiskLevel.CAUTION,
                    f"Caution pattern '{match.group(0)}' detected in command",
                )

        return RiskLevel.SAFE, "Command passed security heuristic"

    @classmethod
    def screen_python_code(cls, code: str) -> Tuple[RiskLevel, str]:
        """Parse Python AST to inspect for prohibited dangerous operations."""
        if not code or not code.strip():
            return RiskLevel.SAFE, "Empty code"

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return RiskLevel.DESTRUCTIVE, f"Python SyntaxError: {e}"

        for node in ast.walk(tree):
            # Check for direct calls to dangerous functions
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    # e.g., os.system, shutil.rmtree
                    val = node.func.value
                    prefix = getattr(val, "id", "") if isinstance(val, ast.Name) else ""
                    func_name = f"{prefix}.{node.func.attr}" if prefix else node.func.attr

                if func_name in {"os.system", "shutil.rmtree", "posix.system"}:
                    return (
                        RiskLevel.DESTRUCTIVE,
                        f"Prohibited call '{func_name}' detected in Python AST",
                    )

        # Also run regex screen over raw code for shell commands embedded in strings
        risk, reason = cls.screen_command(code)
        if risk == RiskLevel.DESTRUCTIVE:
            return risk, reason

        return RiskLevel.SAFE, "Python AST passed security screen"

    def is_safe_to_execute(
        self,
        command: str,
        allow_destructive: bool = False,
        confirmed: bool = False,
    ) -> Tuple[bool, str, RiskLevel]:
        """Evaluate if command can proceed based on policy and HITL confirmation."""
        risk, reason = self.screen_command(command)

        if risk == RiskLevel.DESTRUCTIVE:
            hitl_required = CONFIG.get("tool_require_hitl_for_destructive", True)
            if hitl_required and not (allow_destructive or confirmed):
                msg = (
                    f"Blocked by CommandPolicyGuard: Destructive command requires explicit "
                    f"HITL confirmation (--allow-destructive or confirmed=True). Reason: {reason}"
                )
                return False, msg, risk
            return True, f"Destructive command approved via HITL confirmation. Reason: {reason}", risk

        return True, reason, risk


class ToolExecutionService:
    """Manages sandboxed subprocess execution, command screening, and native OS virtualization."""

    def __init__(self, workspace: Optional[str] = None) -> None:
        self.workspace = os.path.abspath(workspace or WORKSPACE_DIR)
        self.policy_guard = CommandPolicyGuard()
        self._audit_lock = threading.Lock()
        self._audit_log_path = os.path.join(self.workspace, "data", "tool_execution_audit.jsonl")

    def audit_log(self, event_type: str, details: Dict[str, Any]) -> None:
        """Thread-safe append of execution event to JSONL audit log."""
        if not CONFIG.get("tool_execution_audit_log", True):
            return
        try:
            os.makedirs(os.path.dirname(self._audit_log_path), exist_ok=True)
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event_type,
                **details,
            }
            with self._audit_lock, open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except (OSError, TypeError, ValueError) as err:
            logger.debug(f"ToolExecutionService audit log failed (non-fatal): {err}")

    def check_path_containment(
        self,
        target_path: str,
        allowed_dirs: Optional[List[str]] = None,
    ) -> bool:
        """Verify that target_path resides within one of the allowed directories."""
        if not target_path:
            return False
        try:
            resolved_target = Path(target_path).resolve()
            search_roots = allowed_dirs or [self.workspace]
            for root in search_roots:
                resolved_root = Path(root).resolve()
                if resolved_target == resolved_root or resolved_target.is_relative_to(resolved_root):
                    return True
            return False
        except (OSError, ValueError):
            return False

    def execute_sandboxed_command(
        self,
        cmd: Union[str, List[str]],
        shell: str = "powershell",
        timeout: Optional[int] = None,
        memory_mb: Optional[int] = None,
        cwd: Optional[str] = None,
        allow_destructive: bool = False,
        confirmed: bool = False,
        enforce_containment: bool = False,
    ) -> ToolExecutionResult:
        """Execute a system command within strict timeout and memory boundaries."""
        t0 = time.time()
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)

        # 1. Policy screening
        safe, reason, risk = self.policy_guard.is_safe_to_execute(
            cmd_str,
            allow_destructive=allow_destructive,
            confirmed=confirmed,
        )
        if not safe:
            self.audit_log("blocked", {"command": cmd_str, "reason": reason, "risk": risk.value})
            return ToolExecutionResult(
                command=cmd_str,
                status="policy_blocked",
                exit_code=-1,
                error_message=reason,
                sandboxed=True,
                duration_ms=round((time.time() - t0) * 1000, 2),
            )

        # 2. Path containment check
        work_dir = os.path.abspath(cwd or self.workspace)
        if enforce_containment and not self.check_path_containment(work_dir):
            err_msg = f"Path containment violation: '{work_dir}' is outside allowed directories."
            self.audit_log("containment_violation", {"command": cmd_str, "cwd": work_dir})
            return ToolExecutionResult(
                command=cmd_str,
                status="policy_blocked",
                exit_code=-1,
                error_message=err_msg,
                sandboxed=True,
                duration_ms=round((time.time() - t0) * 1000, 2),
            )

        # 3. Formulate command list
        if isinstance(cmd, list):
            exec_args = cmd
        else:
            if shell == "powershell":
                exec_args = [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    cmd,
                ]
            elif shell == "cmd":
                exec_args = ["cmd", "/c", cmd]
            else:
                exec_args = [cmd]

        effective_timeout = timeout or int(CONFIG.get("tool_sandbox_timeout_s", 30))
        effective_memory_mb = memory_mb or int(CONFIG.get("tool_sandbox_memory_mb", 512))

        self.audit_log("start", {
            "command": cmd_str,
            "timeout": effective_timeout,
            "memory_mb": effective_memory_mb,
            "cwd": work_dir,
        })

        try:
            res = safe_subprocess.run_with_limits(
                exec_args,
                timeout=effective_timeout,
                memory_mb=effective_memory_mb,
                cwd=work_dir,
                creationflags=_CREATE_NO_WINDOW,
            )
            elapsed_ms = round((time.time() - t0) * 1000, 2)
            stdout = res.stdout.strip() if res.stdout else ""
            stderr = res.stderr.strip() if res.stderr else ""

            self.audit_log("done", {
                "command": cmd_str,
                "exit_code": res.returncode,
                "duration_ms": elapsed_ms,
            })

            return ToolExecutionResult(
                command=cmd_str,
                stdout=stdout,
                stderr=stderr,
                exit_code=res.returncode,
                duration_ms=elapsed_ms,
                status="success" if res.returncode == 0 else "error",
                sandboxed=True,
            )

        except subprocess.TimeoutExpired:
            elapsed_ms = round((time.time() - t0) * 1000, 2)
            self.audit_log("timeout", {"command": cmd_str, "timeout": effective_timeout})
            return ToolExecutionResult(
                command=cmd_str,
                status="timeout",
                exit_code=-1,
                error_message=f"Command execution timed out after {effective_timeout}s",
                sandboxed=True,
                duration_ms=elapsed_ms,
            )

        except safe_subprocess.MemoryLimitExceeded as e:
            elapsed_ms = round((time.time() - t0) * 1000, 2)
            self.audit_log("memory_kill", {"command": cmd_str, "memory_mb": e.memory_mb})
            return ToolExecutionResult(
                command=cmd_str,
                status="memory_limit_exceeded",
                exit_code=-1,
                memory_peak_mb=e.rss_mb if e.rss_mb >= 0 else effective_memory_mb,
                error_message=f"Process killed: exceeded memory ceiling of {e.memory_mb} MB",
                sandboxed=True,
                duration_ms=elapsed_ms,
            )

        except (OSError, RuntimeError, ValueError) as err:
            elapsed_ms = round((time.time() - t0) * 1000, 2)
            self.audit_log("error", {"command": cmd_str, "error": str(err)})
            return ToolExecutionResult(
                command=cmd_str,
                status="error",
                exit_code=-1,
                error_message=str(err),
                sandboxed=True,
                duration_ms=elapsed_ms,
            )

    def execute_python_script(
        self,
        code: str,
        filename: str = "temp_script.py",
        timeout: Optional[int] = None,
        memory_mb: Optional[int] = None,
        cwd: Optional[str] = None,
        allow_destructive: bool = False,
        confirmed: bool = False,
        use_docker: bool = False,
        image: str = "python:3.11-slim",
    ) -> ToolExecutionResult:
        """Execute a Python script in a controlled scratch directory or Docker container."""
        t0 = time.time()

        # 1. Screen Python code via AST
        risk, reason = self.policy_guard.screen_python_code(code)
        if risk == RiskLevel.DESTRUCTIVE:
            hitl_required = CONFIG.get("tool_require_hitl_for_destructive", True)
            if hitl_required and not (allow_destructive or confirmed):
                msg = f"Blocked by CommandPolicyGuard: {reason}"
                self.audit_log("script_blocked", {"reason": reason, "filename": filename})
                return ToolExecutionResult(
                    command=f"python {filename}",
                    status="policy_blocked",
                    exit_code=-1,
                    error_message=msg,
                    sandboxed=True,
                    duration_ms=round((time.time() - t0) * 1000, 2),
                )

        # 2. Docker path if requested and enabled
        if use_docker and CONFIG.get("docker_sandbox_enabled", False):
            try:
                from docker_sandbox import SandboxConfig, execute_code
                cfg = SandboxConfig(timeout=timeout or 30, image=image)
                docker_res = execute_code(code, config=cfg)
                elapsed_ms = round((time.time() - t0) * 1000, 2)
                return ToolExecutionResult(
                    command=f"docker python {filename}",
                    stdout=docker_res.stdout,
                    stderr=docker_res.stderr,
                    exit_code=docker_res.exit_code,
                    duration_ms=elapsed_ms,
                    status="success" if docker_res.exit_code == 0 else "error",
                    sandboxed=True,
                )
            except (ImportError, OSError, RuntimeError) as e:
                logger.warning(f"Docker sandbox unavailable, falling back to local: {e}")

        # 3. Local isolated execution
        sanitized_name = os.path.basename(filename) or "temp_script.py"
        scratch_dir = os.path.join(self.workspace, "_scratch")
        os.makedirs(scratch_dir, exist_ok=True)
        script_path = os.path.join(scratch_dir, sanitized_name)

        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            return self.execute_sandboxed_command(
                cmd=[sys.executable, script_path],
                timeout=timeout,
                memory_mb=memory_mb,
                cwd=cwd or self.workspace,
                allow_destructive=True,  # already screened above
            )
        finally:
            try:
                if os.path.exists(script_path):
                    os.unlink(script_path)
            except OSError:
                pass

    def get_native_system_metrics(self) -> Dict[str, Any]:
        """Inspect native host metrics without spawning external console windows."""
        metrics: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "os": sys.platform,
            "psutil_available": _PSUTIL_AVAILABLE,
            "cpu": {},
            "memory": {},
            "disk": {},
            "battery": None,
            "uptime_seconds": 0,
        }

        if not _PSUTIL_AVAILABLE:
            return metrics

        try:
            # CPU
            metrics["cpu"] = {
                "percent": psutil.cpu_percent(interval=None),
                "count_logical": psutil.cpu_count(logical=True),
                "count_physical": psutil.cpu_count(logical=False),
            }

            # Memory
            mem = psutil.virtual_memory()
            metrics["memory"] = {
                "total_mb": round(mem.total / (1024 * 1024), 1),
                "available_mb": round(mem.available / (1024 * 1024), 1),
                "used_mb": round(mem.used / (1024 * 1024), 1),
                "percent": mem.percent,
            }

            # Disks
            disks: Dict[str, Any] = {}
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disks[part.mountpoint] = {
                        "total_gb": round(usage.total / (1024 ** 3), 2),
                        "used_gb": round(usage.used / (1024 ** 3), 2),
                        "free_gb": round(usage.free / (1024 ** 3), 2),
                        "percent": usage.percent,
                        "fstype": part.fstype,
                    }
                except (PermissionError, OSError):
                    continue
            metrics["disk"] = disks

            # Battery
            battery = psutil.sensors_battery()
            if battery is not None:
                metrics["battery"] = {
                    "percent": battery.percent,
                    "power_plugged": battery.power_plugged,
                    "secsleft": battery.secsleft if battery.secsleft != psutil.POWER_TIME_UNLIMITED else -1,
                }

            # Boot time & Uptime
            boot_time = psutil.boot_time()
            metrics["uptime_seconds"] = round(time.time() - boot_time, 1)

        except (OSError, RuntimeError, AttributeError) as err:
            logger.debug(f"Failed to collect complete native metrics: {err}")

        return metrics


def _reset_tool_execution_service_for_tests() -> None:
    """Reset helper for tests."""
    pass
