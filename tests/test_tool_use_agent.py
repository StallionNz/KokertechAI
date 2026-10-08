"""
test_tool_use_agent.py — End-to-end test for the ToolUseAgent ReAct loop.

Run with pytest (skipped by default — requires requires_api=true):
    pytest test_tool_use_agent.py -v

Run standalone:
    python test_tool_use_agent.py
"""

import sys
import os
import time
import logging

# Force UTF-8 for stdout/stderr so emoji characters don't cause
# UnicodeEncodeError on Windows consoles with cp437/cp1252 encoding.
# Guarded: pytest collection closes stdout in Python 3.14, causing
# "ValueError: I/O operation on closed file" at module level.
try:
    if hasattr(sys.stdout, "reconfigure") and sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (ValueError, OSError):
    pass
try:
    if hasattr(sys.stderr, "reconfigure") and sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (ValueError, OSError):
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CONFIG
from logging_config import get_logger

logger = get_logger(name="ToolUseTest")


import pytest

requires_provider = pytest.mark.skipif(
    "not config.getini('requires_api')",
    reason="Requires AI provider — set requires_api=true in pytest.ini or pass -o requires_api=true",
)


@requires_provider
def test_list_files():
    """Test: List files in the workspace root — a single tool call."""
    logger.info("=" * 70)
    logger.info("TEST 1: List files in workspace root")
    logger.info("=" * 70)

    from sub_agents import ToolUseAgent

    agent = ToolUseAgent()
    logger.info(f"  Provider: {agent.provider_name}")
    logger.info(f"  Model: {agent.model}")

    task = "Please list the files in the workspace directory C:\\KokertechAI using the LIST_FILES tool, then summarize what you found."

    start = time.time()
    result = agent.execute(task, timeout=120)
    elapsed = time.time() - start

    logger.info(f"  Elapsed: {elapsed:.1f}s")
    logger.info(f"  RESULT:\n{'-' * 40}\n{result}\n{'-' * 40}")

    assert not result.startswith("[ToolUser] Error"), f"Agent returned error: {result}"
    assert not result.startswith("[ToolUser] Failed"), f"Agent failed: {result}"
    assert len(result) > 10, "Response too short"
    logger.ok("TEST 1 PASSED")
    return result


@requires_provider
def test_multi_tool():
    """Test: Two tools in one session — list files then check system status."""
    logger.info("=" * 70)
    logger.info("TEST 2: Multi-step orchestration (LIST_FILES + SYSTEM_STATUS)")
    logger.info("=" * 70)

    from sub_agents import ToolUseAgent

    agent = ToolUseAgent()
    task = (
        "First, list the files in the workspace using LIST_FILES. "
        "Then, check system status using SYSTEM_STATUS. "
        "Finally, summarize what you learned from both tools."
    )

    start = time.time()
    result = agent.execute(task, timeout=180)
    elapsed = time.time() - start

    logger.info(f"  Elapsed: {elapsed:.1f}s")
    logger.info(f"  RESULT:\n{'-' * 40}\n{result}\n{'-' * 40}")

    assert not result.startswith("[ToolUser] Error"), f"Agent returned error: {result}"
    assert not result.startswith("[ToolUser] Failed"), f"Agent failed: {result}"
    assert len(result) > 20, "Response too short"
    logger.ok("TEST 2 PASSED")
    return result


@requires_provider
def test_plugin_wrapper():
    """Test: Invoke the ToolUseAgent via the TOOL_USE plugin command."""
    logger.info("=" * 70)
    logger.info("TEST 3: TOOL_USE plugin wrapper via plugin_registry")
    logger.info("=" * 70)

    from plugin_registry import registry

    task = "Check the system status and list what's in the plugins/ directory."
    intent = {"action": "TOOL_USE", "task": task}

    start = time.time()
    result = registry.execute_command(intent)
    elapsed = time.time() - start

    logger.info(f"  Elapsed: {elapsed:.1f}s")
    logger.info(f"  RESULT:\n{'-' * 40}\n{result}\n{'-' * 40}")

    assert not result.startswith("[ERROR]"), f"Plugin returned error: {result}"
    assert not result.startswith("❌"), f"Plugin failed: {result}"
    assert len(result) > 10, "Response too short"
    logger.ok("TEST 3 PASSED")
    return result


def test_missing_task():
    """Test: TOOL_USE plugin with missing task returns proper error."""
    logger.info("=" * 70)
    logger.info("TEST 4: TOOL_USE plugin — missing task error handling")
    logger.info("=" * 70)

    from plugin_registry import registry

    result = registry.execute_command({"action": "TOOL_USE"})
    logger.info(f"  Result: {str(result)[:100]}")

    assert "Missing" in str(result) or "❌" in str(result), f"Expected error message, got: {result}"
    logger.ok("TEST 4 PASSED")


@requires_provider
def test_unknown_command():
    """Test: ToolUseAgent handles unknown tool calls gracefully."""
    logger.info("=" * 70)
    logger.info("TEST 5: Agent handles unknown tool call gracefully")
    logger.info("=" * 70)

    from sub_agents import ToolUseAgent

    agent = ToolUseAgent()
    task = "Call the NONEXISTENT_TOOL tool and tell me what error you get."

    start = time.time()
    result = agent.execute(task, timeout=120)
    elapsed = time.time() - start

    logger.info(f"  Elapsed: {elapsed:.1f}s")
    logger.info(f"  RESULT:\n{'-' * 40}\n{result}\n{'-' * 40}")

    assert not result.startswith("[ToolUser] Failed"), f"Agent failed: {result}"
    logger.ok("TEST 5 PASSED")
    return result


if __name__ == "__main__":
    # Add a console handler when running standalone so output is visible
    _console = logging.StreamHandler(sys.stdout)
    _console.setLevel(logging.INFO)
    _console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_console)
    logger.setLevel(logging.INFO)

    logger.info("🚀 ToolUseAgent End-to-End Test Suite")
    logger.info(f"  Provider: {CONFIG.get('active_provider')}")
    logger.info(f"  Model: {CONFIG.get('model_name')}")
    logger.info(f"  Provider: {CONFIG.get('active_provider')}")

    results = []
    tests = [
        ("test_list_files", test_list_files),
        ("test_multi_tool", test_multi_tool),
        ("test_plugin_wrapper", test_plugin_wrapper),
        ("test_missing_task", test_missing_task),
        ("test_unknown_command", test_unknown_command),
    ]

    for name, fn in tests:
        try:
            fn()
            results.append((name, "PASSED"))
        except Exception as e:
            results.append((name, f"FAILED: {e}"))
            import traceback
            traceback.print_exc()
        logger.info("")

    logger.info("=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    for name, status in results:
        icon = "✅" if "PASSED" in status else "❌"
        logger.info(f"  {icon} {status} — {name}")

    passed = sum(1 for _, s in results if "PASSED" in s)
    total = len(results)
    logger.info(f"  {passed}/{total} tests passed")

    sys.exit(0 if passed == total else 1)
