"""

workflow_engine.py — YAML-based workflow pipeline engine for KokertechAI.

Sprint 5.3: Defines, validates, and executes YAML workflow pipelines
that chain sub-agents, plugins, and decisions.

"""

import os
import threading
import time
from typing import Optional, List, Dict, Tuple, Any

from logging_config import get_logger


logger = get_logger(name="WorkflowEngine")


import json

class WorkflowStep:
    """
    WorkflowStep — a single step in a workflow pipeline.
    """
    def __init__(
        self,
        name: str = "",
        action: str = "",
        params: Optional[dict] = None,
        depends_on: Optional[list] = None,
        on_failure: str = "stop",
        timeout: int = 120,
        max_retries: int = 0,
        retry_delay: float = 0.5,
        if_condition: Optional[str] = None,
    ):
        self.name = name
        self.action = action
        self.params = params or {}
        self.depends_on = depends_on or []
        self.on_failure = on_failure
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.retry_delay = max(0.0, float(retry_delay))
        self.if_condition = str(if_condition).strip() if if_condition else None


class WorkflowDefinition:
    """
    WorkflowDefinition — a YAML workflow pipeline definition.
    """
    def __init__(self, name="", steps=None, parallel=False, description=""):
        self.name = name
        self.steps = steps or []
        self.parallel = parallel
        self.description = description

class StepResult:
    """
    StepResult — result of executing a single workflow step.
    """
    def __init__(self, step_name="", status="", output="", error=""):
        self.step_name = step_name
        self.status = status
        self.output = output
        self.error = error

class WorkflowResult:
    """
    WorkflowResult — overall result of executing a workflow.
    """
    def __init__(self, workflow_name="", overall="", steps=None):
        self.workflow_name = workflow_name
        self.overall = overall
        self.steps = steps if steps is not None else []


def _parse_steps_list(lines):
    """Parse a list of YAML step definitions.

    Each step starts with ``- name: ...`` and its properties are
    indented lines following it.  Handles multi-step lists with
    arbitrary indent depths as long as all steps share the same
    base indent.
    """
    steps: list[WorkflowStep] = []
    current: dict[str, Any] = {}
    base_indent: int | None = None

    def _flush():
        nonlocal current
        if not current:
            return
        # Parse scalar values that might be list/JSON strings
        depends_on_raw = current.get("depends_on", [])
        if isinstance(depends_on_raw, str):
            depends_on_raw = depends_on_raw.strip()
            if depends_on_raw.startswith("[") and depends_on_raw.endswith("]"):
                inner = depends_on_raw[1:-1]
                depends_on_raw = [
                    v.strip().strip("'\"")
                    for v in inner.split(",")
                    if v.strip()
                ]
            else:
                depends_on_raw = [depends_on_raw] if depends_on_raw else []

        params_raw = current.get("params", {})
        if isinstance(params_raw, str):
            params_raw = params_raw.strip()
            if params_raw.startswith("{") and params_raw.endswith("}"):
                try:
                    params_raw = json.loads(params_raw)
                except json.JSONDecodeError:
                    pass  # keep as string

        step = WorkflowStep(
            name=current.get("name", ""),
            action=current.get("action", ""),
            params=params_raw,
            depends_on=depends_on_raw,
            on_failure=current.get("on_failure", "stop"),
            timeout=int(current.get("timeout", 120)),
            max_retries=int(current.get("max_retries", 0)),
            retry_delay=float(current.get("retry_delay", 0.5)),
            if_condition=current.get("if_condition", None),
        )
        steps.append(step)
        current = {}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        indent = len(line) - len(line.lstrip())
        if base_indent is None:
            base_indent = indent

        # Check if this line starts a new step item
        if stripped.startswith("- "):
            _flush()
            rest = stripped[2:].strip()
            if ":" in rest:
                key, val = rest.split(":", 1)
                current[key.strip()] = val.strip()
        elif indent >= base_indent + 2 and ":" in stripped:
            # Property line (indented under a step)
            key, val = stripped.split(":", 1)
            current[key.strip()] = val.strip()
        elif indent < base_indent:
            # Back to parent level - stop processing
            break

    _flush()
    return steps


def parse_yaml(yaml_text):
    """
    Parse a simple YAML workflow definition.
    """
    if not yaml_text or not yaml_text.strip():
        raise ValueError("Empty YAML text")

    lines = yaml_text.split("\n")
    data = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if ":" in stripped:
            key, val = stripped.split(":", 1)
            key = key.strip()
            val = val.strip()
            if val == "":
                # Check next lines for list
                j = i + 1
                sub_lines = []
                while j < len(lines):
                    sub = lines[j]
                    if sub.strip() and (sub.startswith("  ") or sub.startswith("\t")):
                        sub_lines.append(sub)
                    else:
                        break
                    j += 1
                i = j - 1
                if sub_lines and sub_lines[0].strip().startswith("- "):
                    data[key] = _parse_steps_list(sub_lines)
                else:
                    data[key] = "\n".join(sub_lines) if sub_lines else ""
            else:
                # Try to parse as JSON-like dict
                if val.startswith("{") and val.endswith("}"):
                    try:
                        data[key] = json.loads(val)
                    except json.JSONDecodeError:
                        data[key] = val
                elif val.startswith("[") and val.endswith("]"):
                    val_clean = val.strip("[]")
                    if val_clean:
                        data[key] = [v.strip().strip("'\"") for v in val_clean.split(",")]
                    else:
                        data[key] = []
                elif val.lower() == "true":
                    data[key] = True
                elif val.lower() == "false":
                    data[key] = False
                elif val.isdigit():
                    data[key] = int(val)
                else:
                    data[key] = val
        i += 1

    if "name" not in data:
        raise ValueError("Workflow must have a name")
    if "steps" not in data or not data["steps"]:
        raise ValueError("Workflow must have at least one step")

    steps = data.get("steps", [])
    # Ensure defaults
    for s in steps:
        if not hasattr(s, 'on_failure') or not s.on_failure:
            s.on_failure = "stop"
        if not hasattr(s, 'timeout') or not s.timeout:
            s.timeout = 120

    return WorkflowDefinition(
        name=data["name"],
        steps=steps,
        parallel=data.get("parallel", False),
        description=data.get("description", ""),
    )


def _run_single_step(step: WorkflowStep) -> StepResult:
    try:
        if step.action == "sub_agent":
            from sub_agents import SubAgent
            persona = step.params.get("persona", "Researcher")
            task = step.params.get("task", "")
            agent = SubAgent(persona)
            result = agent.execute(task, timeout=step.timeout)
            return StepResult(step_name=step.name, status="success", output=result)

        elif step.action == "code_review":
            workspace_path = step.params.get("workspace_path", "")
            filepath = step.params.get("filepath", "")
            if not workspace_path or not filepath:
                return StepResult(
                    step_name=step.name, status="failed",
                    error="code_review requires {workspace_path, filepath}"
                )
            from services.code_intelligence_factory import get_code_intelligence_factory
            from code_review_workflow import review_file
            factory = get_code_intelligence_factory()
            factory.register(workspace_path)
            result = review_file(workspace_path, filepath)
            output = json.dumps(result.asdict()) if hasattr(result, 'asdict') else str(result)
            return StepResult(step_name=step.name, status="success", output=output)

        else:
            from plugin_registry import registry
            result = registry.execute_command({"command": step.action, "params": step.params})
            return StepResult(step_name=step.name, status="success", output=str(result))

    except Exception as e:  # noqa: BLE001
        return StepResult(step_name=step.name, status="failed", error=str(e))


def execute_step(step: WorkflowStep) -> StepResult:
    """
    Execute a single workflow step with automatic retry on failure.
    Returns StepResult.
    """
    max_retries = max(0, getattr(step, "max_retries", 0))
    retry_delay = max(0.0, getattr(step, "retry_delay", 0.0))
    attempts = 1 + max_retries

    last_res = None
    for attempt in range(attempts):
        res = _run_single_step(step)
        if res.status == "success":
            return res
        last_res = res
        if attempt < attempts - 1 and retry_delay > 0:
            time.sleep(retry_delay)

    return last_res if last_res is not None else StepResult(
        step_name=getattr(step, "name", ""),
        status="failed",
        error="Execution failed",
    )


def _evaluate_if_condition(
    condition: Optional[str],
    step_results: List[StepResult],
    completed_map: Dict[str, str],
) -> bool:
    """
    Evaluate if a step should execute based on its if_condition string.
    Supports 'previous_failed', 'previous_success', and '<step_name>_{failed|success}'.
    """
    if not condition:
        return True
    cond = condition.strip().lower()
    if cond in ("previous_failed", "on_failure"):
        last_exec = next((sr for sr in reversed(step_results) if sr.status != "skipped"), None)
        return last_exec is not None and last_exec.status == "failed"
    elif cond in ("previous_success", "on_success"):
        last_exec = next((sr for sr in reversed(step_results) if sr.status != "skipped"), None)
        return last_exec is not None and last_exec.status == "success"
    elif cond.endswith("_failed"):
        target_name = condition[: -len("_failed")].strip()
        return completed_map.get(target_name) == "failed"
    elif cond.endswith("_success"):
        target_name = condition[: -len("_success")].strip()
        return completed_map.get(target_name) == "success"
    return True


def execute_workflow(workflow, progress_callback=None, stop_event=None):
    """
    Execute a workflow definition, respecting dependencies and conditional branching.
    """
    result = WorkflowResult()
    step_results = []
    completed_map = {}  # step_name -> status

    if stop_event and stop_event.is_set():
        for s in workflow.steps:
            step_results.append(StepResult(step_name=s.name, status="skipped"))
        result.steps = step_results
        result.overall = "skipped"
        return result

    if workflow.parallel:
        # Execute all steps in parallel (no dependency checking in parallel mode)
        threads = []
        lock = threading.Lock()

        def _run_step(s):
            sr = execute_step(s)
            with lock:
                step_results.append(sr)

        for s in workflow.steps:
            t = threading.Thread(target=_run_step, args=(s,), daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=30)

        # Fill in any missing results
        executed_names = {sr.step_name for sr in step_results}
        for s in workflow.steps:
            if s.name not in executed_names:
                step_results.append(StepResult(step_name=s.name, status="success"))

    else:
        # Sequential execution with dependency resolution and conditional branching
        step_map = {s.name: s for s in workflow.steps}
        stopped_due_to_failure = False

        for s in workflow.steps:
            if stop_event and stop_event.is_set():
                step_results.append(StepResult(step_name=s.name, status="skipped"))
                continue

            is_failure_handler = bool(
                s.if_condition
                and ("failed" in s.if_condition.lower() or "failure" in s.if_condition.lower())
            )

            # If stopped due to failure, only explicit failure-handlers are eligible to run
            if stopped_due_to_failure and not is_failure_handler:
                step_results.append(StepResult(step_name=s.name, status="skipped"))
                completed_map[s.name] = "skipped"
                continue

            # Check if_condition
            if s.if_condition:
                if not _evaluate_if_condition(s.if_condition, step_results, completed_map):
                    step_results.append(StepResult(step_name=s.name, status="skipped"))
                    completed_map[s.name] = "skipped"
                    continue

            # Check dependencies
            deps_met = True
            for dep_name in s.depends_on:
                if dep_name not in step_map:
                    deps_met = False
                    break
                dep_status = completed_map.get(dep_name, "")
                if dep_status == "failed" and not is_failure_handler:
                    deps_met = False
                    break

            if not deps_met:
                step_results.append(StepResult(step_name=s.name, status="skipped"))
                completed_map[s.name] = "skipped"
                continue

            sr = execute_step(s)
            step_results.append(sr)
            completed_map[s.name] = sr.status

            if sr.status == "failed" and s.on_failure == "stop":
                stopped_due_to_failure = True

    # Determine overall status
    result.steps = step_results
    if any(sr.status == "failed" for sr in step_results):
        result.overall = "failed"
    elif any(sr.status == "skipped" for sr in step_results):
        result.overall = "skipped"
    else:
        result.overall = "success"

    if progress_callback:
        for sr in step_results:
            progress_callback(sr)

    return result


def execute_workflow_async(workflow, progress_callback=None, done_callback=None, stop_event=None):
    """
    Execute a workflow asynchronously in a background thread.
    """
    def _run():
        result = execute_workflow(workflow, progress_callback=progress_callback, stop_event=stop_event)
        if done_callback:
            done_callback(result)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


class WorkflowEngine:
    """
    High-level interface for listing and executing workflows.
    """

    def __init__(self, workflows_dir: Optional[str] = None):
        if workflows_dir is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.workflows_dir = os.path.join(base_dir, "workflows")
        else:
            self.workflows_dir = workflows_dir

    def list_workflows(self) -> List[str]:
        """
        List all available YAML workflows in the configured workflows directory.
        """
        if not os.path.isdir(self.workflows_dir):
            return []
        try:
            return sorted([
                f for f in os.listdir(self.workflows_dir)
                if (f.endswith(".yaml") or f.endswith(".yml"))
                and os.path.isfile(os.path.join(self.workflows_dir, f))
            ])
        except (OSError, ValueError) as e:
            logger.error("Failed to list workflows in %s: %s", self.workflows_dir, e)
            return []

    def execute(self, name_or_path: str) -> Dict[str, Any]:
        """
        Execute a workflow by name or file path and return a summary result dictionary.
        """
        path = name_or_path
        if not os.path.isabs(path) and not os.path.isfile(path):
            candidate = os.path.join(self.workflows_dir, path)
            if os.path.isfile(candidate):
                path = candidate

        if not os.path.isfile(path):
            return {"ok": False, "error": f"Workflow file not found: {name_or_path}"}

        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            definition = parse_yaml(content)
            result = execute_workflow(definition)
            is_ok = result.overall == "success"
            return {
                "ok": is_ok,
                "overall": result.overall,
                "error": None if is_ok else "Workflow execution failed or skipped steps",
                "steps": [
                    {"name": s.step_name, "status": s.status, "output": s.output, "error": s.error}
                    for s in result.steps
                ],
            }
        except (ValueError, OSError, RuntimeError, TypeError, KeyError) as e:
            logger.error("WorkflowEngine execution error: %s", e)
            return {"ok": False, "error": str(e)}
