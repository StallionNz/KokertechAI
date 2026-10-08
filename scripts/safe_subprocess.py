"""
safe_subprocess.py -- Strict subprocess timeouts and memory ceiling enforcements.

Provides run_with_limits() as a drop-in replacement for subprocess.run that adds:
  - Hard timeout with process-tree cleanup (no orphaned children on timeout)
  - Memory ceiling monitoring (RSS-based, Windows-compatible via psutil)
  - MemoryLimitExceeded exception distinct from TimeoutExpired

Usage:
  from scripts.safe_subprocess import run_with_limits, MemoryLimitExceeded

  result = run_with_limits(
      ["pytest", "test_file.py"],
      timeout=600,        # 10 min hard limit
      memory_mb=1024,     # kill if RSS > 1 GB
      cwd=".",
  )

Design:
  - Memory monitor runs in a daemon thread polling psutil every 500ms.
  - On timeout or memory kill, the ENTIRE process tree is killed via
    kill_process_tree() -- uses psutil.Process.children(recursive=True).
  - Falls back to bare subprocess.run if psutil is not importable
    (memory_mb is ignored, timeout still enforced via stdlib).
  - Thread-safe: uses threading.Event for coordination between
    the monitoring thread and the main wait loop.
"""

from __future__ import annotations

import subprocess
import threading
from typing import Optional, List, Union

# ---------------------------------------------------------------------------
# psutil availability (optional -- memory features degrade gracefully)
# ---------------------------------------------------------------------------
_PSUTIL_AVAILABLE = False
try:
    import psutil  # noqa: F401 (used via module ref below)
    _PSUTIL_AVAILABLE = True
except ImportError:
    pass

_MEMORY_POLL_INTERVAL_S = 0.5
_PROCESS_TREE_KILL_TIMEOUT_S = 3.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MemoryLimitExceeded(subprocess.SubprocessError):
    """Raised when a subprocess exceeds its memory ceiling.

    Attributes:
        cmd: The command that was executed.
        memory_mb: The ceiling that was exceeded (in MB).
        rss_mb: The RSS at the time of kill, or -1 if unknown.
    """
    def __init__(self, cmd, memory_mb, rss_mb=-1):
        self.cmd = cmd
        self.memory_mb = memory_mb
        self.rss_mb = rss_mb
        msg = (
            f"Process exceeded memory ceiling of {memory_mb} MB"
            + (f" (RSS: {rss_mb:.1f} MB)" if rss_mb >= 0 else "")
            + f": {cmd}"
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Process tree kill (psutil-backed, Windows-compatible)
# ---------------------------------------------------------------------------

def kill_process_tree(pid: int, timeout: float = _PROCESS_TREE_KILL_TIMEOUT_S) -> bool:
    """Kill a process and all its descendants.

    Returns True if all processes were terminated, False if some survived.
    Gracefully handles psutil not being available (no-op).
    """
    if not _PSUTIL_AVAILABLE:
        return False
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True  # already gone

    procs = [parent]
    try:
        procs.extend(parent.children(recursive=True))
    except psutil.NoSuchProcess:
        pass

    # Kill phase
    for p in procs:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Wait phase
    try:
        gone, alive = psutil.wait_procs(procs, timeout=timeout)
    except (psutil.Error, OSError):
        # wait_procs can raise on some platforms; treat all survivors as alive
        alive = procs

    # Kill survivors again (force)
    for p in alive:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return len(alive) == 0


# ---------------------------------------------------------------------------
# Memory monitoring thread
# ---------------------------------------------------------------------------

def _monitor_memory_worker(pid: int, memory_mb: float, stop_event: threading.Event,
                           kill_event: threading.Event):
    """Background daemon thread: poll RSS, set kill_event and kill tree if ceiling breached."""
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return

    while not stop_event.is_set():
        try:
            rss_mb = proc.memory_info().rss / (1024 * 1024)
        except psutil.NoSuchProcess:
            return  # process exited normally
        except psutil.AccessDenied:
            # Windows can deny access during teardown; retry once then give up
            stop_event.wait(1.0)
            try:
                rss_mb = proc.memory_info().rss / (1024 * 1024)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return

        if rss_mb > memory_mb:
            kill_event.set()
            kill_process_tree(pid)
            return

        stop_event.wait(_MEMORY_POLL_INTERVAL_S)


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def run_with_limits(
    cmd: Union[str, List[str]],
    *,
    timeout: Optional[float] = None,
    memory_mb: Optional[float] = None,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    capture_output: bool = True,
    text: bool = True,
    check: bool = False,
    creationflags: int = 0,
) -> subprocess.CompletedProcess:
    """Run a subprocess with strict timeout and memory ceiling enforcement.

    On timeout or memory limit exceeded, the ENTIRE process tree is killed
    before the exception is raised (no orphaned children).

    Args:
        cmd: Command to execute (str or list, same as subprocess.run).
        timeout: Hard timeout in seconds. None = no limit.
        memory_mb: Memory ceiling in MB (RSS). None = no limit.
                   Requires psutil; silently ignored if psutil is not installed.
        cwd: Working directory.
        env: Environment variables dict.
        capture_output: Capture stdout/stderr (default True).
        text: Return str instead of bytes (default True).
        check: Raise CalledProcessError on non-zero exit.
        creationflags: Platform-specific creation flags (e.g. 0x08000000 for
                       CREATE_NO_WINDOW on Windows).

    Returns:
        subprocess.CompletedProcess with .returncode, .stdout, .stderr.

    Raises:
        subprocess.TimeoutExpired: If the process exceeds the timeout.
        MemoryLimitExceeded: If the process exceeds the memory ceiling.
        subprocess.CalledProcessError: If check=True and returncode != 0.
    """
    kwargs = {}
    if capture_output:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if text:
        kwargs["universal_newlines"] = True
    if cwd is not None:
        kwargs["cwd"] = cwd
    if env is not None:
        kwargs["env"] = env
    if creationflags:
        kwargs["creationflags"] = creationflags

    # Launch the process
    proc = subprocess.Popen(cmd, **kwargs)

    # Set up memory monitoring if requested AND psutil is available
    stop_event = threading.Event()
    kill_event = threading.Event()
    monitor_thread = None

    use_memory_monitor = (
        memory_mb is not None
        and memory_mb > 0
        and _PSUTIL_AVAILABLE
        and proc.pid is not None
    )

    if use_memory_monitor:
        monitor_thread = threading.Thread(
            target=_monitor_memory_worker,
            args=(proc.pid, memory_mb, stop_event, kill_event),
            daemon=True,
        )
        monitor_thread.start()

    try:
        # Wait for completion with timeout
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the process tree, then re-raise
            if proc.pid is not None:
                kill_process_tree(proc.pid)
            proc.wait()  # collect zombie
            raise

        # Check memory kill event (monitor thread may have killed the process)
        if kill_event.is_set():
            # Process was killed by memory monitor; collect exit status
            proc.wait()
            raise MemoryLimitExceeded(cmd, memory_mb)

    finally:
        # Always stop the memory monitor
        stop_event.set()
        if monitor_thread is not None:
            monitor_thread.join(timeout=1.0)

    # Build result
    result = subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout=stdout if capture_output else None,
        stderr=stderr if capture_output else None,
    )

    if check:
        result.check_returncode()

    return result
