"""
code_review_workflow.py - Sprint 17: Workspace-aware code-review workflow.

First real consumer of the ``CodeIntelligenceFactory``. Lands Sprint 17
acceptance criterion #1.4 by registering a workspace pattern at boot
(via ``register_workspace``) and resolving the SAME workspace-appropriate
``CodeIntelligence`` instance from the factory cache for each review
action (via ``review_file``) -- rather than instantiating a fresh
``CodeIntelligence(workspace=...)`` per call site.

Pattern follows ``doc_pipeline.py`` (function-style API + dataclass) and
``workflow_engine.py`` (sync execution returning a structured Result).
Audit-trail reuses ``CodeIntelligence._audit_log`` built-in (no
fragmented workflow-level JSONL).

ANTI-FRAGILITY: cross-test pollution guard requires callers (CLI boot
scripts, agent loop, tests) to call ``_reset_factory_for_tests()`` from
``services.code_intelligence_factory`` between processes that mutate the
singleton's registration cache. ``conftest.py`` auto-wires this for
pytest; CLI boot is single-process so the guard is moot there.
"""

import os
import subprocess
import threading
from typing import Optional, List, Dict, Tuple, Any

from logging_config import get_logger


logger = get_logger(name="MemoryVault")


class WorkspaceNotRegisteredError(Exception):
    """Raised when ``review_file`` is called for an unregistered workspace.

    Carries the workspace path in the error message so the agent loop can
    surface a clear "call register_workspace() before review_file()" guidance.
    """

    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        super().__init__(
            f"Workspace not registered: {workspace_path!r}. "
            f"Call register_workspace() before review_file()."
        )


class CodeReviewResult:
    """Result of a 3-step code review (read + linter + AST analysis).

    Attributes:
        status: ``"success"`` or ``"error"``.
        error_msg: Truncated exception message if status is ``"error"``.
        file_content_length: Byte count of the reviewed file.
        linter_errors: List of linter error dicts (empty if linter_cmd was None).
        ast_summary: Dict with ``functions``, ``classes``, ``imports`` keys, or
            empty dict for non-``.py`` files.
    """

    def __init__(
        self,
        status: str = "success",
        error_msg: str = "",
        filepath: str = "",
        file_content_length: int = 0,
        linter_errors: Optional[List[Dict]] = None,
        ast_summary: Optional[Dict] = None,
    ):
        self.status = status
        self.error_msg = error_msg
        self.filepath = filepath
        self.file_content_length = file_content_length
        self.linter_errors = linter_errors or []
        self.ast_summary = ast_summary or {}

    def asdict(self) -> Dict[str, Any]:
        """Return result fields as a dict for JSON serialization."""
        return {
            "status": self.status,
            "error_msg": self.error_msg,
            "filepath": self.filepath,
            "file_content_length": self.file_content_length,
            "linter_errors": self.linter_errors,
            "ast_summary": self.ast_summary,
        }


def register_workspace(workspace_path: str, linter_cmd: Optional[str] = None) -> None:
    """Boot-time registration of a workspace pattern with the CodeIntelligence factory.

    The factory's ``register`` stores the pattern (the workspace path itself
    in Sprint 17 scope) plus an optional linter_config dict. Subsequent
    ``review_file`` calls on the same workspace path resolve to a cached
    ``CodeIntelligence`` instance from the factory.

    Idempotent: re-registering the same workspace overwrites the prior
    linter_config (deep-copied inside the factory -- caller-side mutation
    after this call does NOT leak into the factory's stored config).

    Args:
        workspace_path: Absolute path to the workspace root.
        linter_cmd: Optional linter binary name (e.g., ``"ruff"``). If
            ``None``, linter checks return empty errors with a warning
            string in the linter subprocess output.
    """
    from services.code_intelligence_factory import get_code_intelligence_factory

    factory = get_code_intelligence_factory()
    linter_config = {}
    if linter_cmd is not None:
        linter_config["linter_cmd"] = linter_cmd
    factory.register(workspace_path, linter_config=linter_config)


def review_file(workspace_path: str, filepath: str) -> CodeReviewResult:
    """Execute a 3-step code review via the cached ``CodeIntelligence`` singleton.

    Steps:
      1. ``read_file`` -- validates symlink + workspace-escape safety,
         captures byte count.
      2. ``run_linter`` -- collects ruff (or configured linter) errors.
      3. ``analyze_ast`` -- Python-only AST structural context (functions /
         classes / imports). Skipped for non-``.py`` paths to avoid
         ``ast.parse`` SyntaxError on arbitrary text.

    On any step failure, captures ``status="error"`` + the truncated
    exception message in ``error_msg``; the exception is NOT re-raised so
    the agent loop can collect a partial result. This matches the
    doc_pipeline pattern where process_file never raises on per-file
    failures -- the caller inspects ``result.status`` instead.

    Args:
        workspace_path: Must match a previously-registered workspace
            pattern, otherwise ``WorkspaceNotRegisteredError`` is raised.
        filepath: Path relative to the workspace (or absolute, if it
            resolves inside the workspace after ``_validate_path``).

    Returns:
        ``CodeReviewResult``; ``status="success"`` only if all 3 steps
        completed without exception.

    Raises:
        WorkspaceNotRegisteredError: ``workspace_path`` was never registered.
    """
    from services.code_intelligence_factory import get_code_intelligence_factory

    factory = get_code_intelligence_factory()

    # Resolve the cached CodeIntelligence instance -- raises KeyError if
    # the workspace was never registered.
    try:
        ci = factory.resolve(workspace_path)
    except KeyError:
        raise WorkspaceNotRegisteredError(workspace_path)

    result = CodeReviewResult(filepath=filepath)

    # Step 1: read_file
    try:
        content = ci.read_file(filepath)
        result.file_content_length = len(content)
    except (RuntimeError, OSError, ValueError) as e:
        result.status = "error"
        result.error_msg = f"read_file failed: {type(e).__name__}: {str(e)[:200]}"
        return result

    # Step 2: run_linter
    try:
        linter_result = ci.run_linter(filepath)
        result.linter_errors = linter_result.get("errors", [])
    except (RuntimeError, OSError, subprocess.SubprocessError) as e:
        result.status = "error"
        result.error_msg = f"run_linter failed: {type(e).__name__}: {str(e)[:200]}"
        return result

    # Step 3: analyze_ast (Python-only)
    try:
        if filepath.endswith(".py"):
            result.ast_summary = ci.analyze_ast(filepath)
        else:
            result.ast_summary = {}
    except (RuntimeError, SyntaxError, ValueError) as e:
        result.status = "error"
        result.error_msg = f"analyze_ast failed: {type(e).__name__}: {str(e)[:200]}"
        return result

    result.status = "success"
    return result
